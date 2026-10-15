"""
Unit tests for SkillsDB v3.1.0 Architecture Fusion:
- Task State Machine (project_tasks, mem_task_add, mem_task_update, mem_task_list, mem_task_clear)
- Project Context Integration with Active Task Board (mem_get_context)
- Autonomous Context Compaction (mem_compact)
- Subdirectory Scoping & Path Hints (detect_cwd_hints, suggest_skills)
"""

import sys
import io
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from skillsdb.memory.project_memory import (
    init_project_db,
    mem_save_decision,
    mem_save_fact,
    mem_task_add,
    mem_task_update,
    mem_task_list,
    mem_task_clear,
    mem_compact,
    mem_get_context,
)
from skillsdb.search.fts import detect_cwd_hints, suggest_skills
from skillsdb.core.db import init_db
from skillsdb.search.synonyms import init_synonyms


class TestTaskStateMachine(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / ".agents" / "memory.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        init_project_db(self.conn)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_task_add_and_list(self):
        t1_id = mem_task_add(self.conn, "Implement Task Board", description="Add table and CRUD", priority="high", status="pending")
        t2_id = mem_task_add(self.conn, "Write Unit Tests", description="Ensure 100% coverage", priority="critical", status="in_progress")
        self.assertEqual(t1_id, 1)
        self.assertEqual(t2_id, 2)

        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM project_tasks;")
        self.assertEqual(cur.fetchone()[0], 2)

        # Verify FTS index
        cur.execute("SELECT COUNT(*) FROM project_memory_fts WHERE source_table = 'task';")
        self.assertEqual(cur.fetchone()[0], 2)

    def test_task_update(self):
        t_id = mem_task_add(self.conn, "Fix Bug", priority="med", status="pending")
        # Update status
        ok = mem_task_update(self.conn, t_id, status="in_progress")
        self.assertTrue(ok)
        cur = self.conn.cursor()
        cur.execute("SELECT status, priority FROM project_tasks WHERE id = ?;", (t_id,))
        row = cur.fetchone()
        self.assertEqual(row["status"], "in_progress")
        self.assertEqual(row["priority"], "med")

        # Update priority & title
        ok = mem_task_update(self.conn, t_id, priority="critical", title="Fix Urgent Crash")
        self.assertTrue(ok)
        cur.execute("SELECT title, priority FROM project_tasks WHERE id = ?;", (t_id,))
        row = cur.fetchone()
        self.assertEqual(row["title"], "Fix Urgent Crash")
        self.assertEqual(row["priority"], "critical")

        # Invalid task ID
        self.assertFalse(mem_task_update(self.conn, 999, status="completed"))
        # Invalid status
        self.assertFalse(mem_task_update(self.conn, t_id, status="invalid_status"))
        # Invalid priority
        self.assertFalse(mem_task_update(self.conn, t_id, priority="ultra_mega"))

    def test_task_clear(self):
        t1 = mem_task_add(self.conn, "Task 1", status="completed")
        t2 = mem_task_add(self.conn, "Task 2", status="pending")
        t3 = mem_task_add(self.conn, "Task 3", status="completed")

        # Clear only completed
        mem_task_clear(self.conn, only_completed=True)
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM project_tasks;")
        self.assertEqual(cur.fetchone()[0], 1)
        cur.execute("SELECT id, status FROM project_tasks;")
        remaining = cur.fetchone()
        self.assertEqual(remaining["id"], t2)
        self.assertEqual(remaining["status"], "pending")

        # Clear all
        mem_task_clear(self.conn, only_completed=False)
        cur.execute("SELECT COUNT(*) FROM project_tasks;")
        self.assertEqual(cur.fetchone()[0], 0)

    def test_mem_get_context_with_tasks(self):
        mem_save_fact(self.conn, "version", "3.1.0")
        mem_save_decision(self.conn, "Task Architecture", "Use SQLite project_tasks table")
        mem_task_add(self.conn, "Active Core Refactoring", priority="high", status="in_progress")
        mem_task_add(self.conn, "Documentation Update", priority="med", status="pending")
        mem_task_add(self.conn, "Old Finished Task", status="completed")

        old_stdout = sys.stdout
        sys.stdout = buffer = io.StringIO()
        try:
            mem_get_context(self.conn, project_root=Path(self.temp_dir))
        finally:
            sys.stdout = old_stdout

        output = buffer.getvalue()
        self.assertIn("Active Task Board:", output)
        self.assertIn("Active Core Refactoring", output)
        self.assertIn("Documentation Update", output)
        self.assertNotIn("Old Finished Task", output)  # Completed tasks are hidden from active board


class TestAutoCompaction(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / ".agents" / "memory.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        init_project_db(self.conn)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_mem_compact(self):
        mem_task_add(self.conn, "Done 1", status="completed")
        mem_task_add(self.conn, "Done 2", status="completed")
        mem_task_add(self.conn, "Still Working", status="in_progress")
        mem_save_decision(self.conn, "Decision 1", "WAL Mode enabled")
        mem_save_fact(self.conn, "env", "production")

        old_stdout = sys.stdout
        sys.stdout = buffer = io.StringIO()
        try:
            mem_compact(self.conn, summary="Milestone v3.1 achieved", next_steps="Deploy to production", archive_completed=True, project_root=Path(self.temp_dir))
        finally:
            sys.stdout = old_stdout

        output = buffer.getvalue()
        self.assertIn("SESSION AUTO-COMPACTION COMPLETED", output)
        self.assertIn("Active Tasks:     1", output)
        self.assertIn("Completed Pruned: 2", output)

        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM project_tasks;")
        self.assertEqual(cur.fetchone()[0], 1)

        cur.execute("SELECT summary, next_steps FROM session_snapshots ORDER BY id DESC LIMIT 1;")
        snap = cur.fetchone()
        self.assertEqual(snap["summary"], "Milestone v3.1 achieved")
        self.assertEqual(snap["next_steps"], "Deploy to production")


class TestDirectoryScoping(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.flutter_dir = Path(self.temp_dir) / "app" / "lib" / "screens"
        self.flutter_dir.mkdir(parents=True, exist_ok=True)
        (Path(self.temp_dir) / "app" / "pubspec.yaml").write_text("name: test_app", encoding="utf-8")

        self.db_path = Path(self.temp_dir) / "customizations.db"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)
        init_synonyms(self.conn)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_detect_cwd_hints(self):
        hints = detect_cwd_hints(self.flutter_dir)
        self.assertIn("flutter", hints)
        self.assertIn("dart", hints)

        data_dir = Path(self.temp_dir) / "analytics" / "sql"
        data_dir.mkdir(parents=True, exist_ok=True)
        data_hints = detect_cwd_hints(data_dir)
        self.assertIn("sql", data_hints)
        self.assertIn("data", data_hints)


class TestAutonomousHook(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / ".agents" / "memory.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        init_project_db(self.conn)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_hook_auto_compaction_and_task_injection(self):
        import json
        from skillsdb.memory.project_memory import handle_pre_invocation_hook

        # Add 3 completed tasks to trigger auto-compaction
        mem_task_add(self.conn, "Done 1", status="completed")
        mem_task_add(self.conn, "Done 2", status="completed")
        mem_task_add(self.conn, "Done 3", status="completed")
        mem_task_add(self.conn, "Urgent Core Task", priority="critical", status="in_progress")
        mem_save_decision(self.conn, "Hook Architecture", "Autonomous PreInvocation injection")

        payload = json.dumps({"invocationNum": 1, "workspacePaths": [self.temp_dir]})
        old_stdin = sys.stdin
        old_stdout = sys.stdout
        sys.stdin = io.StringIO(payload)
        sys.stdout = buffer = io.StringIO()
        try:
            handle_pre_invocation_hook()
        finally:
            sys.stdin = old_stdin
            sys.stdout = old_stdout

        raw_output = buffer.getvalue().strip()
        data = json.loads(raw_output)
        self.assertIn("injectSteps", data)
        msg = data["injectSteps"][0]["ephemeralMessage"]
        self.assertIn("Active Task Board", msg)
        self.assertIn("Urgent Core Task", msg)
        self.assertIn("Hook Architecture", msg)

        # Verify that completed tasks were autonomously pruned!
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM project_tasks WHERE status = 'completed';")
        self.assertEqual(cur.fetchone()[0], 0)
        cur.execute("SELECT COUNT(*) FROM project_tasks WHERE status = 'in_progress';")
        self.assertEqual(cur.fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
