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


if __name__ == "__main__":
    unittest.main()
