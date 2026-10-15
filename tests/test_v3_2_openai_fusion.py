"""
Unit tests for SkillsDB v3.2 OpenAI Primitives:
- Deterministic Guardrails (advisory & friction-free auto-fix)
- Evaluator-Optimizer Task Verification Gates
- Agent Handoff Protocol (scoped context transfer)
- Active Fact Reconciliation & Memory Tombstoning
"""

import sys
import unittest
import tempfile
import sqlite3
from pathlib import Path

# Add project root to sys.path
repo_root = Path(__file__).parent.parent.resolve()
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from skillsdb.core.guardrails import (
    check_text,
    fix_text,
    check_workspace,
    verify_task,
)
from skillsdb.memory.handoff import (
    create_handoff,
    format_handoff_block,
    list_handoffs,
    read_handoff,
    update_handoff,
)
from skillsdb.memory.project_memory import (
    init_project_db,
    mem_save_fact,
    mem_deprecate_fact,
    mem_fact_history,
    mem_reconcile,
    mem_task_add,
    mem_task_update,
    mem_save_decision,
)


class TestV32OpenAIFusion(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.proj_dir = Path(self.temp_dir.name)
        self.agents_dir = self.proj_dir / ".agents"
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.agents_dir / "memory.db"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        init_project_db(self.conn)

    def tearDown(self):
        self.conn.close()
        from skillsdb.memory.writer_queue import stop_all_writer_queues
        stop_all_writer_queues()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_guardrails_detection_and_autofix(self):
        """Validates detection of dashes, emojis, Deppenbindestriche, and secrets, plus auto-fix."""
        bad_text = (
            "This is an em\u2014dash and en\u2013dash.\n"
            "Here is an emoji: \U0001F680.\n"
            "German error: Wir brauchen Token-Reduktion und ein Browser-Tool.\n"
            "Secret leak: sk-proj-1234567890abcdefghijklmnop\n"
        )
        violations = check_text(bad_text)
        self.assertGreaterEqual(len(violations), 4)

        rules = {v["rule"] for v in violations}
        self.assertIn("no-dash-invariants", rules)
        self.assertIn("no-emojis", rules)
        self.assertIn("no-deppenbindestrich", rules)
        self.assertIn("secret-leak-detection", rules)

        # Test zero-friction auto-fix
        fixed, fix_count = fix_text(bad_text)
        self.assertNotIn("\u2014", fixed)
        self.assertNotIn("\u2013", fixed)
        self.assertNotIn("\U0001F680", fixed)
        self.assertIn("Tokenreduktion", fixed)
        self.assertIn("Browsertool", fixed)
        self.assertGreaterEqual(fix_count, 4)

    def test_guardrail_workspace_advisory_mode(self):
        """Verifies that guardrail workspace check is advisory and non-blocking by default."""
        test_file = self.proj_dir / "sample.py"
        test_file.write_text("token = 'clean_string'\n", encoding="utf-8")

        res = check_workspace(root_path=self.proj_dir, staged_only=False, strict=False)
        self.assertTrue(res["passed"])
        self.assertEqual(res["violations_count"], 0)

    def test_task_verification_gate(self):
        """Evaluator-Optimizer: verify task transitions to completed only when assertions pass."""
        task_id = mem_task_add(self.conn, "Implement Feature X", priority="high", status="in_progress")
        self.assertEqual(task_id, 1)

        # 1. Failing verification command should not complete task
        fail_res = verify_task(task_id, test_cmd="python -c \"import sys; sys.exit(1)\"", project_root=self.proj_dir)
        self.assertFalse(fail_res["verified"])

        cur = self.conn.cursor()
        cur.execute("SELECT status FROM project_tasks WHERE id = ?;", (task_id,))
        self.assertEqual(cur.fetchone()[0], "in_progress")

        # 2. Passing verification command transitions task to completed
        pass_res = verify_task(task_id, test_cmd="python -c \"print('all clear')\"", project_root=self.proj_dir)
        self.assertTrue(pass_res["verified"])
        self.assertEqual(pass_res["status"], "completed")

        cur.execute("SELECT status FROM project_tasks WHERE id = ?;", (task_id,))
        self.assertEqual(cur.fetchone()[0], "completed")

        # Check verification log
        cur.execute("SELECT verifier_type, passed FROM task_verifications WHERE task_id = ?;", (task_id,))
        vrow = cur.fetchone()
        self.assertIsNotNone(vrow)
        self.assertEqual(vrow["passed"], 1)

    def test_agent_handoff_protocol(self):
        """Tests scoped context handoffs between specialized agents."""
        task_id = mem_task_add(self.conn, "Security Audit", priority="critical", status="in_progress")
        mem_save_fact(self.conn, "auth_provider", "Firebase Auth")
        mem_save_decision(self.conn, "Token Standard", "Strict JWT validation with short-lived tokens")

        handoff = create_handoff(
            task_id=task_id,
            target_role="Security Auditor",
            source_role="Lead Architect",
            notes="Please perform deep penetration analysis on token endpoints",
            context_vars={"scope": "auth_endpoints", "strict_mode": True},
            project_root=self.proj_dir
        )

        self.assertTrue(handoff["handoff_id"].startswith("ho_"))
        self.assertEqual(handoff["target_role"], "Security Auditor")
        self.assertEqual(handoff["source_role"], "Lead Architect")
        self.assertEqual(handoff["context_variables"]["scope"], "auth_endpoints")
        self.assertIn("auth_provider", handoff["active_facts"])

        # Format markdown block check
        block = format_handoff_block(handoff)
        self.assertIn("AGENT HANDOFF", block)
        self.assertIn("Security Auditor", block)
        self.assertIn("Strict JWT validation", block)

        # Check listing and reading
        hos = list_handoffs(project_root=self.proj_dir)
        self.assertEqual(len(hos), 1)
        self.assertEqual(hos[0]["id"], handoff["handoff_id"])

        read_res = read_handoff(handoff["handoff_id"], project_root=self.proj_dir)
        self.assertIsNotNone(read_res)
        self.assertEqual(read_res["task"]["title"], "Security Audit")

        # Update status
        self.assertTrue(update_handoff(handoff["handoff_id"], "accepted", project_root=self.proj_dir))
        read_updated = read_handoff(handoff["handoff_id"], project_root=self.proj_dir)
        self.assertEqual(read_updated["status"], "accepted")

    def test_fact_reconciliation_and_tombstoning(self):
        """Tests fact update history, conflict reconciliation, and tombstone isolation."""
        # 1. Initial fact creation
        mem_save_fact(self.conn, "python_version", "3.10")

        # 2. Fact update (active reconciliation)
        mem_save_fact(self.conn, "python_version", "3.12", reason="Platform upgrade to 3.12")

        # Verify fact_history recorded previous state
        cur = self.conn.cursor()
        cur.execute("SELECT old_value, new_value, reason FROM fact_history WHERE key = ?;", ("python_version",))
        hist_row = cur.fetchone()
        self.assertIsNotNone(hist_row)
        self.assertEqual(hist_row["old_value"], "3.10")
        self.assertEqual(hist_row["new_value"], "3.12")
        self.assertEqual(hist_row["reason"], "Platform upgrade to 3.12")

        # 3. Deprecate / tombstone a fact
        mem_save_fact(self.conn, "legacy_flag", "enabled")
        self.assertTrue(mem_deprecate_fact(self.conn, "legacy_flag", reason="Removed in v3.2"))

        cur.execute("SELECT deprecated_at FROM project_facts WHERE key = ?;", ("legacy_flag",))
        self.assertIsNotNone(cur.fetchone()[0])

        # 4. Reconciliation report
        rep = mem_reconcile(self.conn)
        self.assertEqual(rep["active_facts"], 1)  # Only python_version is active
        self.assertEqual(rep["deprecated_facts"], 1)  # legacy_flag is tombstoned
        self.assertEqual(rep["history_reconciliation_events"], 2)


if __name__ == "__main__":
    unittest.main()
