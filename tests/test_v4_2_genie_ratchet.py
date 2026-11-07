import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from skillsdb.core.db import init_db, managed_connection
from skillsdb.core.guardrails import handle_pre_tool_guardrail_hook
from skillsdb.memory.project_memory import (
    analyze_conversation_epochs_and_scope,
    get_project_connection,
    get_project_db_path,
    handle_pre_invocation_hook,
    init_project_db,
    mem_index_symbols,
    mem_locate_symbol,
    mem_pulse,
    mem_save_fact,
    mem_symbol_impact,
)
from skillsdb.memory.writer_queue import stop_all_writer_queues
from skillsdb.search.fts import rebuild_skill_sections_index, slice_skill
from skillsdb.updater.merger import add_or_update_skill


class TestV42GenieRatchet(unittest.TestCase):
    def tearDown(self):
        stop_all_writer_queues()

    def test_step1_incremental_cursor_and_compaction_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            central_db = tmp_p / "central.db"
            ws = tmp_p / "proj"
            (ws / ".agents").mkdir(parents=True, exist_ok=True)
            pconn = get_project_connection(ws)
            mem_save_fact(pconn, "init_key", "init_val")
            stop_all_writer_queues()
            pconn.close()

            brain = tmp_p / "brain"
            cid = "test-cid-step1"
            log_dir = brain / cid / ".system_generated" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            t_file = log_dir / "transcript.jsonl"
            steps = [
                {"step_index": 1, "type": "USER_INPUT", "created_at": "2026-11-07T10:00:00Z", "content": "Start"},
                {
                    "step_index": 50,
                    "type": "CHECKPOINT",
                    "created_at": "2026-11-07T10:15:00Z",
                    "content": "Compaction #1 summary touching surgical_deep_shrink.py L10-L50",
                },
                {"step_index": 51, "type": "USER_INPUT", "created_at": "2026-11-07T10:16:00Z", "content": "Next"},
            ]
            t_file.write_text("\n".join(json.dumps(s) for s in steps) + "\n", encoding="utf-8")

            res1 = analyze_conversation_epochs_and_scope(
                cid=cid, active_workspace=ws, db_path=central_db, brain_dir=brain
            )
            self.assertEqual(res1["checkpoint_count"], 1)
            self.assertEqual(res1["max_step_index"], 51)

            # Append a second checkpoint and verify incremental seek picks up the delta
            with open(t_file, "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "step_index": 100,
                            "type": "CHECKPOINT",
                            "created_at": "2026-11-07T10:30:00Z",
                            "content": "Compaction #2 summary touching Lci/j4; method X",
                        }
                    )
                    + "\n"
                )
            res2 = analyze_conversation_epochs_and_scope(
                cid=cid, active_workspace=ws, db_path=central_db, brain_dir=brain
            )
            self.assertEqual(res2["checkpoint_count"], 2)
            self.assertEqual(res2["max_step_index"], 100)

            with managed_connection(get_project_db_path(ws), is_project_db=True) as check_conn:
                cnt = check_conn.execute("SELECT COUNT(*) FROM compaction_archive;").fetchone()[0]
                self.assertEqual(cnt, 2)

    def test_step2_crlf_normalization_working_set_and_bm25_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            (ws / ".agents").mkdir(parents=True, exist_ok=True)
            pconn = get_project_connection(ws)
            pconn.close()
            stop_all_writer_queues()

            crlf_py = ws / "service.py"
            crlf_py.write_bytes(
                b"class PaymentEngine:\r\n"
                b"    def charge(self, amt: int) -> int:\r\n"
                b"        return amt\r\n\r\n"
                b"def audit_log(msg: str) -> str:\r\n"
                b"    return msg\r\n"
            )
            payload = {
                "toolCall": {
                    "name": "edit_file",
                    "args": {
                        "Path": str(crlf_py),
                        "Target": "    def charge(self, amt: int) -> int:\n        return amt",
                        "Replacement": (
                            "    def charge(self, amt: int) -> int:\n"
                            "        tax = amt * 2\n"
                            "        audit_log(str(tax))\n"
                            "        return tax"
                        ),
                    },
                }
            }
            old_in, old_out = sys.stdin, sys.stdout
            try:
                sys.stdin = io.StringIO(json.dumps(payload))
                out = io.StringIO()
                sys.stdout = out
                handle_pre_tool_guardrail_hook()
                resp = json.loads(out.getvalue())
            finally:
                sys.stdin, sys.stdout = old_in, old_out

            self.assertEqual(resp, {"decision": "allow"})
            self.assertNotIn(b"\r\n", crlf_py.read_bytes())

            with managed_connection(get_project_db_path(ws), is_project_db=True) as check_conn:
                row = check_conn.execute(
                    "SELECT start_line, end_line FROM symbol_index WHERE symbol_name = 'audit_log';"
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual((row["start_line"], row["end_line"]), (7, 8))
                ws_row = check_conn.execute(
                    "SELECT symbols_touched FROM active_working_set WHERE file_path = 'service.py';"
                ).fetchone()
                self.assertIsNotNone(ws_row)
                self.assertIn("PaymentEngine.charge", ws_row["symbols_touched"])

    def test_step3_dependency_edges_and_relational_impact(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            (ws / ".git").mkdir(parents=True, exist_ok=True)
            (ws / "core.py").write_text(
                "class BaseValidator:\n"
                "    def check(self, v: int) -> bool:\n"
                "        return v > 0\n\n"
                "def normalize_token(t: str) -> str:\n"
                "    return t.strip()\n",
                encoding="utf-8",
                newline="\n",
            )
            (ws / "api.py").write_text(
                "from core import BaseValidator, normalize_token\n\n"
                "class TokenValidator(BaseValidator):\n"
                "    def run(self, raw: str) -> bool:\n"
                "        clean = normalize_token(raw)\n"
                "        return self.check(len(clean))\n",
                encoding="utf-8",
                newline="\n",
            )
            with managed_connection(get_project_db_path(ws), is_project_db=True) as pconn:
                init_project_db(pconn)
                stats = mem_index_symbols(pconn, project_root=ws, force=True, silent=True)
                self.assertGreaterEqual(stats["total_edges"], 4)
                old_out = sys.stdout
                try:
                    sys.stdout = io.StringIO()
                    impacts = mem_symbol_impact(pconn, "normalize_token", project_root=ws, auto_index=False, as_json=True)
                    loc = mem_locate_symbol(
                        pconn, "BaseValidator", project_root=ws, auto_index=False, as_json=True, include_impact=True
                    )
                finally:
                    sys.stdout = old_out
            stop_all_writer_queues()

            kinds = {(i["source_file"], i["source_symbol"], i["edge_kind"]) for i in impacts}
            self.assertIn(("api.py", "TokenValidator.run", "calls"), kinds)
            self.assertIn(("api.py", "<module>", "imports"), kinds)
            self.assertTrue(any(i["edge_kind"] == "inherits" for i in loc[0]["impact"]))

    def test_step4_skill_sections_slice_and_federated_pulse(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            central_db = tmp_p / "central.db"
            ws = tmp_p / "ws"
            (ws / ".git").mkdir(parents=True, exist_ok=True)
            (ws / "app.py").write_text(
                "def deploy_release(tag: str) -> bool:\n    return True\n",
                encoding="utf-8",
                newline="\n",
            )
            with managed_connection(central_db, init_schema=True) as cconn:
                init_db(cconn)
                old_out = sys.stdout
                try:
                    sys.stdout = io.StringIO()
                    add_or_update_skill(
                        cconn,
                        name="release-workflow",
                        description="Release automation skill",
                        content=(
                            "# Release Skill\n\n"
                            "## Overview\nGeneral intro.\n\n"
                            "## SemVer Tagging\nAlways bump VERSION and CHANGELOG.md before deploy_release.\n\n"
                            "## Rollback\nRestore backup on failure.\n"
                        ),
                    )
                    sec_count = rebuild_skill_sections_index(cconn, force=True)
                    slices = slice_skill(cconn, "SemVer CHANGELOG deploy_release", limit=2, as_json=True)
                finally:
                    sys.stdout = old_out
                self.assertGreaterEqual(sec_count, 3)
                self.assertGreaterEqual(len(slices), 1)
                self.assertEqual(slices[0]["skill_name"], "release-workflow")

            with managed_connection(get_project_db_path(ws), is_project_db=True) as pconn:
                init_project_db(pconn)
                old_out = sys.stdout
                try:
                    sys.stdout = io.StringIO()
                    mem_save_fact(pconn, "release_branch", "main")
                    pulse = mem_pulse(
                        pconn,
                        "deploy_release SemVer release_branch",
                        project_root=ws,
                        central_db_path=central_db,
                        as_json=True,
                    )
                finally:
                    sys.stdout = old_out
            stop_all_writer_queues()

            self.assertGreaterEqual(len(pulse["symbols"]), 1)
            self.assertEqual(pulse["symbols"][0]["symbol_name"], "deploy_release")
            self.assertGreaterEqual(len(pulse["skill_slices"]), 1)

    def test_step5_universal_polyglot_indexing_and_live_shift(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            (ws / ".git").mkdir(parents=True, exist_ok=True)
            (ws / "server.go").write_text(
                "package main\n\n"
                "func (s *Server) Handle(req string) string {\n"
                "    return sanitize(req)\n"
                "}\n\n"
                "func sanitize(in string) string {\n"
                "    return in\n"
                "}\n",
                encoding="utf-8",
                newline="\n",
            )
            (ws / "engine.cpp").write_text(
                "bool ShaderEngine::compile(int id) {\n"
                "    return crc32(id) > 0;\n"
                "}\n\n"
                "int crc32(int v) {\n"
                "    return v;\n"
                "}\n",
                encoding="utf-8",
                newline="\n",
            )
            (ws / "deploy.sh").write_text(
                "deploy_cluster() {\n"
                "    verify_node \"$1\"\n"
                "}\n\n"
                "verify_node() {\n"
                "    echo \"ok\"\n"
                "}\n",
                encoding="utf-8",
                newline="\n",
            )
            p_db = get_project_db_path(ws)
            with managed_connection(p_db, is_project_db=True) as pconn:
                init_project_db(pconn)
                stats = mem_index_symbols(pconn, project_root=ws, force=True, silent=True)
                self.assertGreaterEqual(stats["total_symbols"], 6)
                old_out = sys.stdout
                try:
                    sys.stdout = io.StringIO()
                    go_edges = mem_symbol_impact(pconn, "sanitize", project_root=ws, auto_index=False, as_json=True)
                    cpp_edges = mem_symbol_impact(pconn, "crc32", project_root=ws, auto_index=False, as_json=True)
                    sh_edges = mem_symbol_impact(pconn, "verify_node", project_root=ws, auto_index=False, as_json=True)
                finally:
                    sys.stdout = old_out
            stop_all_writer_queues()

            self.assertTrue(any(e["source_symbol"] == "Server.Handle" for e in go_edges))
            self.assertTrue(any(e["source_symbol"] == "ShaderEngine.compile" for e in cpp_edges))
            self.assertTrue(any(e["source_symbol"] == "deploy_cluster" for e in sh_edges))

    def test_step6_non_coding_research_and_normal_conversation_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            brain_dir = tmp_p / "brain"
            cid = "conv-research-no-ws"
            log_dir = brain_dir / cid / ".system_generated" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            t_file = log_dir / "transcript.jsonl"
            central_db = tmp_p / "central.db"
            with managed_connection(central_db, init_schema=True) as cconn:
                init_db(cconn)

            t_file.write_text(
                json.dumps({
                    "step_index": 1,
                    "type": "USER_INPUT",
                    "created_at": "2026-11-07T18:00:00Z",
                    "content": "Welchen Kaffeevollautomaten bis 800 Euro kannst Du mir für das Büro empfehlen?",
                }) + "\n",
                encoding="utf-8",
            )

            def _invoke_hook(msg: str) -> str:
                payload = {
                    "invocationNum": 1,
                    "conversationId": cid,
                    "workspacePaths": [],
                    "brainDir": str(brain_dir),
                    "dbPath": str(central_db),
                    "userMessage": msg,
                }
                old_in, old_out = sys.stdin, sys.stdout
                try:
                    sys.stdin = io.StringIO(json.dumps(payload))
                    out = io.StringIO()
                    sys.stdout = out
                    handle_pre_invocation_hook()
                    data = json.loads(out.getvalue())
                    steps = data.get("injectSteps") or []
                    return steps[0].get("ephemeralMessage", "") if steps else ""
                finally:
                    sys.stdin, sys.stdout = old_in, old_out

            # Turn 1: Normal non-coding question should NOT inject false-positive coding skills
            msg1 = _invoke_hook("Welchen Kaffeevollautomaten bis 800 Euro kannst Du mir für das Büro empfehlen?")
            self.assertNotIn("[AUTONOMOUS PROMPT SKILL PRE-ROUTING (Turn 1)]", msg1)

            # Turn 2: Append research subagent finding + compaction CHECKPOINT in this workspace-less conversation
            with open(t_file, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "step_index": 10,
                    "type": "SYSTEM_MESSAGE",
                    "created_at": "2026-11-07T18:05:00Z",
                    "content": '<SYSTEM_MESSAGE>\n[Message] sender=26ab1fb5-1f78-4f78-9a36-e19d48279da6 priority=HIGH content=Jura E8 und Nivona NICR 790 sind Testsieger unter 800 Euro mit Keramikmahlwerk.\n</SYSTEM_MESSAGE>',
                }) + "\n")
                f.write(json.dumps({
                    "step_index": 20,
                    "type": "CHECKPOINT",
                    "created_at": "2026-11-07T18:10:00Z",
                    "content": "Vergleich von Kaffeevollautomaten unter 800 Euro: Jura E8 vs. Nivona NICR 790.",
                }) + "\n")

            msg2 = _invoke_hook("Welcher der beiden hat den geringeren Wartungsaufwand?")
            self.assertIn("subagent_finding_step_10", msg2)
            self.assertIn("Nivona NICR 790", msg2)

            conv_mem_db = brain_dir / cid / ".agents" / "memory.db"
            self.assertTrue(conv_mem_db.exists())
            with managed_connection(conv_mem_db, is_project_db=True) as pconn:
                cp_cnt = pconn.execute("SELECT COUNT(*) FROM compaction_archive;").fetchone()[0]
                self.assertEqual(cp_cnt, 1)
            stop_all_writer_queues()


if __name__ == "__main__":
    unittest.main()
