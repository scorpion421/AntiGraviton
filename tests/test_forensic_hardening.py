"""
Forensic hardening regression tests for AntiGraviton v1.4.3 (SkillsDB v3.7.3).
Covers FTS5 operator/hyphen/punctuation sanitization, fenced code block section parsing,
agent handoff read/format hydration, project_memory_fts deduplication and tombstoning,
extended-length local path detection, decimal model version regex, and differential DB merges.
"""

import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from skillsdb.core.db import _is_network_path, managed_connection, init_db
from skillsdb.core.concurrency import extract_skill_sections
from skillsdb.core.detector import _classify_model_string, detect_model_tier
from skillsdb.core.fim import slice_fim, slice_fim_symbol
from skillsdb.core.guardrails import fix_text, fix_file
from skillsdb.search.synonyms import build_safe_fts_query, expand_query_with_synonyms, init_synonyms
from skillsdb.search.fts import suggest_skills, search
from skillsdb.memory.project_memory import (
    get_project_connection,
    mem_save_fact,
    mem_deprecate_fact,
    mem_task_add,
    mem_task_update,
    mem_task_clear,
    mem_search,
)
from skillsdb.memory.writer_queue import stop_all_writer_queues
from skillsdb.memory.handoff import create_handoff, read_handoff, format_handoff_block
from skillsdb.updater.merger import merge_upstream_database, remove_item
import deploy as deploy_mod


class TestForensicHardeningV143(unittest.TestCase):
    def test_build_safe_fts_query_handles_hyphens_operators_and_punctuation(self):
        self.assertEqual(build_safe_fts_query(["   ", "?!@#$%^&*()"]), "")
        self.assertEqual(build_safe_fts_query(["AND", "OR", "NOT", "NEAR"]), "")
        q = build_safe_fts_query(["Komposita-Regeln", "write_file"])
        self.assertIn('"komposita"', q)
        self.assertIn('"regeln"', q)
        self.assertIn('"write_file"', q)

    def test_extract_skill_sections_ignores_comments_in_code_fences(self):
        md = (
            "## 1. Install\n"
            "Execute:\n"
            "```powershell\n"
            "# Step A: comment inside code block\n"
            "Write-Host 'ok'\n"
            "```\n"
            "~~~python\n"
            "# Step B: comment inside tilde fence\n"
            "print(1)\n"
            "~~~\n"
            "## 2. Verify\n"
            "All good.\n"
        )
        secs = extract_skill_sections(md)
        titles = [s["title"] for s in secs]
        self.assertEqual(titles, ["1. Install", "2. Verify"])

    def test_extended_length_local_vs_unc_path_detection(self):
        self.assertFalse(_is_network_path(Path(r"\\?\C:\Users\test\memory.db")))
        self.assertTrue(_is_network_path(Path(r"\\?\UNC\fileserver\share\memory.db")))
        self.assertTrue(_is_network_path(Path(r"\\fileserver\share\memory.db")))

    def test_detector_captures_decimal_model_names_in_settings_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain_root = Path(tmp)
            cid = "test-cid-decimal"
            log_dir = brain_root / cid / ".system_generated" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            tfile = log_dir / "transcript.jsonl"
            tfile.write_text(
                json.dumps({
                    "type": "USER_INPUT",
                    "content": "<USER_SETTINGS_CHANGE>\nThe user changed the model to Gemini 3.8 Flash.\n</USER_SETTINGS_CHANGE>",
                }) + "\n",
                encoding="utf-8",
            )
            # Verify classification of decimal model names directly and via detect_model_tier
            self.assertEqual(_classify_model_string("Gemini 3.8 Flash"), "lean")
            self.assertEqual(_classify_model_string("Claude Opus 5.5"), "ultra")
            self.assertEqual(_classify_model_string("Claude Sonnet 5.5"), "standard")
            tier, _ = detect_model_tier(model_name_hint="Gemini 3.8 Flash")
            self.assertEqual(tier, "lean")

    def test_fim_slice_includes_middle_and_async_def(self):
        with tempfile.TemporaryDirectory() as tmp:
            py_file = Path(tmp) / "sample.py"
            py_file.write_text("import asyncio\n\nasync def fetch_data(url: str):\n    return url\n", encoding="utf-8")
            res = slice_fim_symbol(py_file, "fetch_data", prefix_lines=2, suffix_lines=2)
            self.assertIn("middle", res)
            self.assertIn("async def fetch_data", res["middle"])

    def test_handoff_read_and_format_hydration(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            conn = get_project_connection(ws)
            try:
                tid = mem_task_add(conn, "Audit endpoints", description="Check auth", priority="critical")
                mem_save_fact(conn, "auth_mode", "oauth2")
            finally:
                stop_all_writer_queues()
                conn.close()

            created = create_handoff(tid, "Security Auditor", notes="Verify tokens", project_root=ws)
            loaded = read_handoff(created["handoff_id"], project_root=ws)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["handoff_id"], created["handoff_id"])
            self.assertIn("auth_mode", loaded.get("active_facts", {}))
            block = format_handoff_block(loaded)
            self.assertIn(created["handoff_id"], block)
            self.assertIn("Security Auditor", block)

    def test_project_memory_fts_deduplication_and_hyphen_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            conn = get_project_connection(ws)
            try:
                mem_save_fact(conn, "cache-strategy", "write-through")
                mem_save_fact(conn, "cache-strategy", "write-back", reason="latency")
                rows = conn.execute(
                    "SELECT content FROM project_memory_fts WHERE source_table='fact' AND title_or_key='cache-strategy';"
                ).fetchall()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0][0], "write-back")

                buf = io.StringIO()
                old_stdout = sys.stdout if "sys" in globals() else None
                mem_deprecate_fact(conn, "cache-strategy", reason="removed")
                rows_after = conn.execute(
                    "SELECT content FROM project_memory_fts WHERE source_table='fact' AND title_or_key='cache-strategy';"
                ).fetchall()
                self.assertEqual(len(rows_after), 0)

                # Hyphenated search query must not raise sqlite3.OperationalError
                mem_search(conn, "write-back AND OR NOT")
            finally:
                stop_all_writer_queues()
                conn.close()

    def test_guardrail_preserves_negative_examples_and_skips_test_files(self):
        sample = 'Write "Abteilungskontingent" never "Abteilungs-Kontingent" in prose, but fix Abteilungs-Kontingent here.'
        cleaned, fixes = fix_text(sample)
        self.assertIn('never "Abteilungs-Kontingent"', cleaned)
        self.assertTrue(cleaned.endswith("fix Abteilungskontingent here."))

    def test_deploy_and_merger_preserve_learned_rules_and_runtime_config(self):
        repo_db = Path(__file__).resolve().parent.parent / "database" / "customizations.db"
        with tempfile.TemporaryDirectory() as tmp:
            target_db = Path(tmp) / "customizations.db"
            shutil.copy2(repo_db, target_db)
            conn = sqlite3.connect(str(target_db))
            conn.execute(
                "INSERT OR REPLACE INTO runtime_config (key, value, updated_at) VALUES ('gitea_token', 'tok_preserve_99', '2026-10-28');"
            )
            conn.execute(
                "INSERT OR REPLACE INTO rules (key, name, category, scope, is_active, summary, content, token_estimate, updated_at) "
                "VALUES ('learned-test-rule', 'Learned Rule', 'learned', 'global', 1, 'sum', 'content', 10, '2026-10-28');"
            )
            conn.commit()
            conn.close()

            deploy_mod.safe_deploy_database(repo_db, target_db)
            merge_res = merge_upstream_database(target_db, repo_db)
            self.assertEqual(merge_res["preserved_learned_rules"], 1)

            conn2 = sqlite3.connect(str(target_db))
            tok = conn2.execute("SELECT value FROM runtime_config WHERE key='gitea_token';").fetchone()
            self.assertIsNotNone(tok)
            self.assertEqual(tok[0], "tok_preserve_99")
            conn2.close()



    def test_hooks_json_has_no_escaped_quotes_for_go_exec_runner(self):
        hooks_path = Path(__file__).resolve().parent.parent / "plugin" / "hooks.json"
        data = json.loads(hooks_path.read_text(encoding="utf-8"))
        block = data["skillsdb-autonomous-memory"]
        pjson = json.loads((Path(__file__).resolve().parent.parent / "plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(pjson.get("name"), "customizations-db")
        self.assertTrue(pjson.get("version"))
        self.assertTrue(pjson.get("displayName"))
        self.assertEqual(len(pjson.get("suggestedPrompts", [])), 3)
        for item in block.get("PreInvocation", []):
            cmd = item["command"]
            self.assertNotIn('"', cmd)
            tokens = cmd.split()
            self.assertEqual(tokens[0], "python")
            self.assertFalse(tokens[1].startswith('"'))
        for matcher_group in block.get("PreToolUse", []):
            for item in matcher_group.get("hooks", []):
                cmd = item["command"]
                self.assertNotIn('"', cmd)
                tokens = cmd.split()
                self.assertEqual(tokens[0], "python")
                self.assertFalse(tokens[1].startswith('"'))


    def test_conversation_epoch_slicing_and_cross_project_guard(self):
        import sys
        import uuid
        from skillsdb.memory.project_memory import (
            analyze_conversation_epochs_and_scope,
            calculate_transcript_savings,
            handle_pre_invocation_hook,
        )
        from skillsdb.memory.gitea_config import (
            learn_workspace,
            get_learned_workspaces_with_timestamps,
        )

        scratch_base = Path(__file__).resolve().parent / "scratch"
        scratch_base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(scratch_base), prefix="epoch_suite_") as tmp:
            root = Path(tmp)
            db_file = root / "test_customizations.db"
            with managed_connection(db_file) as c:
                init_db(c)

            ws_active = root / "AntiGraviton"
            ws_foreign = root / "Vivaldi_Enterprise_PiP" / "picture-in-picture"
            (ws_active / ".agents").mkdir(parents=True, exist_ok=True)
            (ws_foreign / ".agents").mkdir(parents=True, exist_ok=True)
            (ws_active / "README.md").write_text("# AntiGraviton\n", encoding="utf-8")
            (ws_foreign / "README.md").write_text("# Vivaldi PiP\n", encoding="utf-8")

            learn_workspace(ws_active, db_path=db_file, touch_timestamp=True)
            learn_workspace(ws_foreign, db_path=db_file, touch_timestamp=False)

            # Force a known historical timestamp on ws_foreign and verify passive touch_timestamp=False preserves it
            with managed_connection(db_file) as c:
                c.execute(
                    "UPDATE runtime_config SET updated_at = '2026-10-21 11:50:51' WHERE key LIKE 'workspace:%picture-in-picture%';"
                )
                c.commit()
            learn_workspace(ws_foreign, db_path=db_file, touch_timestamp=False)
            ws_with_ts = get_learned_workspaces_with_timestamps(db_path=db_file)
            self.assertIn("picture-in-picture", ws_with_ts)
            self.assertEqual(ws_with_ts["picture-in-picture"][1], "2026-10-21")

            # Create multi-epoch transcript across 2026-10-21 and 2026-10-28
            brain_dir = root / "brain"
            cid = f"test-epoch-{uuid.uuid4()}"
            log_dir = brain_dir / cid / ".system_generated" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            tfile = log_dir / "transcript.jsonl"
            lines_ep1 = [
                json.dumps({
                    "step_index": 10,
                    "type": "USER_INPUT",
                    "source": "USER_EXPLICIT",
                    "created_at": "2026-10-21T11:50:51Z",
                    "content": "Where is config.json for Vivaldi picture-in-picture?",
                }),
                json.dumps({
                    "step_index": 11,
                    "type": "PLANNER_RESPONSE",
                    "source": "MODEL",
                    "created_at": "2026-10-21T11:51:10Z",
                    "content": "Answered Vivaldi question.",
                }),
            ]
            tfile.write_text("\n".join(lines_ep1) + "\n", encoding="utf-8")

            # Hook Call 1 in Epoch 1: injects Turn-1 guardrails
            payload = {
                "conversationId": cid,
                "invocationNum": 1,
                "workspacePaths": [str(ws_active)],
                "brainDir": str(brain_dir),
                "dbPath": str(db_file),
            }
            old_stdin, old_stdout = sys.stdin, sys.stdout
            try:
                sys.stdin = io.StringIO(json.dumps(payload))
                out1 = io.StringIO()
                sys.stdout = out1
                handle_pre_invocation_hook()
                res1 = json.loads(out1.getvalue())
                self.assertEqual(len(res1.get("injectSteps", [])), 1)

                # Hook Call 2 in same Epoch 1: deduplicated (empty injectSteps)
                sys.stdin = io.StringIO(json.dumps(payload))
                out2 = io.StringIO()
                sys.stdout = out2
                handle_pre_invocation_hook()
                res2 = json.loads(out2.getvalue())
                self.assertEqual(res2.get("injectSteps"), [])

                # Append Epoch 2 on 2026-10-28 to transcript.jsonl
                lines_ep2 = [
                    json.dumps({
                        "step_index": 50,
                        "type": "USER_INPUT",
                        "source": "USER_EXPLICIT",
                        "created_at": "2026-10-28T13:00:00Z",
                        "content": "What did we save today with AntiGraviton?",
                    }),
                    json.dumps({
                        "step_index": 51,
                        "type": "PLANNER_RESPONSE",
                        "source": "MODEL",
                        "created_at": "2026-10-28T13:00:15Z",
                        "content": "Computed today savings.",
                    }),
                ]
                with open(tfile, "a", encoding="utf-8") as f:
                    f.write("\n".join(lines_ep2) + "\n")

                ep_info = analyze_conversation_epochs_and_scope(
                    cid=cid,
                    active_workspace=ws_active,
                    db_path=db_file,
                    brain_dir=brain_dir,
                )
                self.assertEqual(ep_info["epoch_count"], 2)
                self.assertEqual(ep_info["active_epoch"]["date"], "2026-10-28")
                self.assertEqual(len(ep_info["foreign_project_mentions"]), 1)
                self.assertEqual(ep_info["foreign_project_mentions"][0]["date"], "2026-10-21")
                self.assertIn("picture-in-picture", ep_info["foreign_project_mentions"][0]["project"])

                # Hook Call 3 in new Epoch 2: automatically re-fires because epoch_key advanced!
                sys.stdin = io.StringIO(json.dumps(payload))
                out3 = io.StringIO()
                sys.stdout = out3
                handle_pre_invocation_hook()
                res3 = json.loads(out3.getvalue())
                self.assertEqual(len(res3.get("injectSteps", [])), 1)
                msg3 = res3["injectSteps"][0]["ephemeralMessage"]
                self.assertIn("[TEMPORAL & WORKSPACE ISOLATION ANCHOR]", msg3)

                self.assertIn("[CROSS-PROJECT CONTAMINATION GUARD]", msg3)

                self.assertIn("picture-in-picture", msg3)
                self.assertIn("[CROSS-PROJECT CONTAMINATION GUARD]", msg3)
                self.assertIn("picture-in-picture", msg3)

                # Verify date-scoped vs cumulative savings calculation
                sav = calculate_transcript_savings(target_date="2026-10-28", brain_dir=brain_dir, db_path=db_file)
                self.assertIsNotNone(sav)
                self.assertEqual(sav["model_turns"], 2)
                self.assertEqual(sav["day_model_turns"], 1)
                self.assertEqual(sav["day_steps"], 2)
                self.assertGreater(sav["tokens_saved_per_turn"], 1000)
                self.assertGreater(sav["reduction_pct"], 90.0)
            finally:
                sys.stdin, sys.stdout = old_stdin, old_stdout


    def test_keep_a_changelog_guardrail_rejects_duplicate_version_headers(self):
        from skillsdb.core.guardrails import check_text
        dup_cl = (
            "# Changelog\n\n"
            "## [1.4.6] - 2026-10-28\n\n### Changed\n- First block.\n\n"
            "## [1.4.6] - 2026-10-28\n\n### Changed\n- Duplicate block.\n"
        )
        violations = check_text(dup_cl, filename="CHANGELOG.md")
        dup_msgs = [v["message"] for v in violations if v["rule"] == "keep-a-changelog"]
        self.assertTrue(any("Duplicate Keep a Changelog version header" in m for m in dup_msgs))


    def test_build_sync_project_versions_and_site_neutral_plugin_readme(self):
        from skillsdb.config import __version__, __suite_version__
        repo_root = Path(__file__).resolve().parent.parent
        version_txt = (repo_root / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(version_txt, __suite_version__)
        pjson = json.loads((repo_root / "plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(pjson.get("version"), __version__)
        self.assertEqual(pjson.get("license"), "AGPL-3.0-only")
        lic_txt = (repo_root / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("GNU AFFERO GENERAL PUBLIC LICENSE", lic_txt)
        preadme = (repo_root / "plugin" / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"AntiGraviton `v{__suite_version__}` (`SkillsDB v{__version__}`)", preadme)
        self.assertNotIn("http://localhost:3000/MoerthJ/AntiGraviton", preadme)
        self.assertNotIn("http://localhost:3000/MickyFoley/AntiGraviton", preadme)


if __name__ == "__main__":
    unittest.main()

