"""
Unit tests for SkillsDB Gitea Service and Automated Git Commit Integration.
Verifies health checks, zero-leakage privacy hardening, automatic test repo cleanup,
zero-turn Git telemetry, semantic files_touched indexing, and multi-workspace discovery.
"""

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import skillsdb.config as cfg
from skillsdb.memory.gitea_sync import (
    get_gitea_config,
    check_gitea_health,
    get_gitea_service_status,
    ensure_remote_repository,
    delete_remote_repository,
    setup_workspace_git,
    save_code_commit,
    get_workspace_git_telemetry,
    discover_all_workspaces,
)
from skillsdb.memory.project_memory import (
    init_project_db,
    mem_save_snapshot,
    get_project_connection,
)


class TestGiteaSync(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="skillsdb_test_gitea_"))
        (self.test_dir / ".agents").mkdir(parents=True, exist_ok=True)
        self.db_path = self.test_dir / ".agents" / "memory.db"
        self.conn = get_project_connection(self.test_dir)
        init_project_db(self.conn)
        self.created_repos = []

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)
        for repo_name in self.created_repos:
            delete_remote_repository(repo_name)

    def test_zero_hardcoded_secrets_in_config(self):
        """Verifies that skillsdb/config.py contains no hardcoded personal usernames or API tokens."""
        import re
        config_file = Path(cfg.__file__).read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\b[0-9a-f]{40}\b", config_file))
        self.assertIn('GITEA_DEFAULT_USER = os.environ.get("SKILLSDB_GITEA_USER", "")', config_file)
        self.assertIn('GITEA_DEFAULT_TOKEN = os.environ.get("SKILLSDB_GITEA_TOKEN", "")', config_file)

    def test_gitea_health_and_service(self):
        """Verifies Gitea Windows Service query and API endpoint health."""
        online, info = check_gitea_health()
        self.assertTrue(online, f"Gitea should be online: {info}")
        self.assertIn("version", info)

        svc_status = get_gitea_service_status()
        self.assertIn(svc_status, ["RUNNING", "START_PENDING"])

    def test_ensure_and_delete_remote_repository(self):
        """Verifies repository creation and automatic cleanup in Gitea via API."""
        repo_name = f"test-auto-{os.getpid()}"
        self.created_repos.append(repo_name)
        clone_url = ensure_remote_repository(repo_name, description="Automated test repo")
        self.assertIsNotNone(clone_url)
        self.assertIn(repo_name, clone_url)
        self.assertTrue("@127.0.0.1:3000" in clone_url or "@localhost:3000" in clone_url)

    def test_setup_workspace_commit_and_telemetry(self):
        """Verifies workspace git initialization, commit creation, trailers, and zero-turn telemetry."""
        ok = setup_workspace_git(self.test_dir, configure_remote=False)
        self.assertTrue(ok)
        self.assertTrue((self.test_dir / ".git").exists())
        self.assertTrue((self.test_dir / ".gitignore").exists())

        # Create dummy project file
        dummy_file = self.test_dir / "app.py"
        dummy_file.write_text("print('hello gitea')\n", encoding="utf-8")

        # Commit locally with episodic trailers
        res = save_code_commit(
            self.test_dir,
            message="Add app.py feature",
            push=False,
            trailers={"Conversation-ID": "conv-abc", "Next-Steps": "Deploy"},
        )
        self.assertTrue(res.get("success"))
        self.assertTrue(res.get("committed"))
        self.assertIn("app.py", res.get("changed_files", []))
        self.assertTrue(len(res.get("commit_hash", "")) >= 7)

        # Verify zero-turn Git telemetry string
        telem = get_workspace_git_telemetry(self.test_dir)
        self.assertIsNotNone(telem)
        self.assertIn("Git Telemetry: branch 'main'", telem)
        self.assertIn("clean", telem)

    def test_snapshot_with_auto_commit_and_fts_indexing(self):
        """Verifies that mem_save_snapshot records commit_hash, auto-captures files_touched, and indexes them in FTS5."""
        setup_workspace_git(self.test_dir, configure_remote=False)

        # Write a test file
        test_file = self.test_dir / "feature_module.py"
        test_file.write_text("def run():\n    return True\n", encoding="utf-8")

        # Save snapshot without explicitly passing files_touched
        mem_save_snapshot(
            self.conn,
            summary="Milestone: Feature module added",
            conversation_id="test-conv-123",
            next_steps="Add unit tests for feature_module",
            db_path=self.db_path,
            auto_commit=True,
            silent=True,
        )

        # Verify database record
        cur = self.conn.cursor()
        cur.execute("SELECT summary, files_touched, commit_hash FROM session_snapshots ORDER BY id DESC LIMIT 1;")
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["summary"], "Milestone: Feature module added")
        self.assertIn("feature_module.py", row["files_touched"])
        self.assertIsNotNone(row["commit_hash"])
        self.assertTrue(len(row["commit_hash"]) >= 7)

        # Verify FTS5 search finds the snapshot by touched filename
        cur.execute(
            "SELECT content FROM project_memory_fts WHERE project_memory_fts MATCH ?;",
            ('"feature_module.py"',),
        )
        fts_row = cur.fetchone()
        self.assertIsNotNone(fts_row)

    def test_discover_all_workspaces(self):
        """Verifies multi-root workspace discovery finds active Antigravity projects."""
        repo_root = Path(__file__).resolve().parent.parent
        workspaces = discover_all_workspaces(base_dir=repo_root.parent)
        self.assertGreaterEqual(len(workspaces), 1)

    def test_gitea_service_templates_and_relocation_regex(self):
        """Verifies app.example.ini includes LevelDB-free immediate queues and deploy-gitea.ps1 self-heals WORK_PATH."""
        repo_root = Path(__file__).resolve().parent.parent
        example_ini = (repo_root / "gitea-service" / "custom" / "conf" / "app.example.ini").read_text(encoding="utf-8")
        self.assertIn("[queue]", example_ini)
        self.assertIn("[queue.common]", example_ini)
        self.assertIn("TYPE = immediate", example_ini)

        deploy_ps1 = (repo_root / "gitea-service" / "scripts" / "deploy-gitea.ps1").read_text(encoding="utf-8")
        self.assertIn("WORK_PATH", deploy_ps1)
        self.assertIn("TYPE = immediate", deploy_ps1)

    def test_ensure_gitea_online_and_self_healing(self):
        """Verifies ensure_gitea_online checks service state and returns online status."""
        from skillsdb.memory.gitea_sync import ensure_gitea_online
        health = ensure_gitea_online(auto_recover=True, flush_pending=False)
        self.assertTrue(health.get("online"))
        self.assertIn(health.get("service_status"), ["RUNNING", "START_PENDING"])


if __name__ == "__main__":
    unittest.main()
