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
        self.assertTrue("127.0.0.1:3000" in clone_url or "localhost:3000" in clone_url)
        self.assertNotIn("@", clone_url, "Clone URL must be free of embedded plaintext credentials")

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

    def test_partial_or_broken_dot_git_auto_healing(self):
        """Verifies that a partial .git directory (e.g. read-only packfile remnant without HEAD) is automatically healed."""
        import stat
        pack_dir = self.test_dir / ".git" / "objects" / "pack"
        pack_dir.mkdir(parents=True, exist_ok=True)
        dummy_pack = pack_dir / "pack-readonly.pack"
        dummy_pack.write_bytes(b"PACK")
        os.chmod(str(dummy_pack), stat.S_IREAD)

        # Telemetry should gracefully return None when .git/HEAD is missing
        self.assertIsNone(get_workspace_git_telemetry(self.test_dir))

        # Creating a file and calling save_code_commit should heal .git and commit cleanly
        (self.test_dir / "shader.hlsl").write_text("float4 main() : SV_Target { return 0; }\n", encoding="utf-8")
        res = save_code_commit(self.test_dir, message="Heal partial .git", push=False)
        self.assertTrue(res.get("success"), f"Expected auto-healed commit to succeed, got: {res}")
        self.assertTrue((self.test_dir / ".git" / "HEAD").exists())
        self.assertTrue(res.get("committed"))

    def test_prune_excluded_and_duplicate_workspaces_from_runtime_config(self):
        """Verifies get_learned_workspaces automatically prunes legacy protected non-workspaces and case duplicates."""
        import sqlite3
        from skillsdb.memory.gitea_sync import get_learned_workspaces

        temp_db = self.test_dir / "test_runtime.db"
        conn = sqlite3.connect(temp_db)
        conn.execute("CREATE TABLE runtime_config (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT);")
        plugin_path = str(cfg.GEMINI_DIR / "config" / "plugins" / "customizations-db")
        repo_root = str(Path(__file__).resolve().parent.parent)
        conn.executemany(
            "INSERT INTO runtime_config (key, value, updated_at) VALUES (?, ?, ?);",
            [
                ("workspace:customizations-db", plugin_path, "2026-10-15T10:00:00Z"),
                ("workspace:antigraviton", repo_root, "2026-10-15T09:00:00Z"),
                ("workspace:AntiGraviton", repo_root, "2026-10-16T10:00:00Z"),
            ],
        )
        conn.commit()
        conn.close()

        learned = get_learned_workspaces(db_path=temp_db, prune_missing=True)
        self.assertNotIn("customizations-db", learned)
        self.assertIn("AntiGraviton", learned)
        self.assertNotIn("antigraviton", learned)

    def test_keep_a_changelog_guardrail_and_parser(self):
        """Verifies Keep a Changelog 1.1.0 guardrail validation and release section parsing."""
        from skillsdb.core.guardrails import check_text
        from skillsdb.memory.gitea_sync import parse_keep_a_changelog

        bad_changelog = (
            "# Changelog\n\n"
            "## [1.0.6] - 2026-10-19\n"
            "### Fixed & Improved\n"
            "- Something custom\n"
        )
        violations = check_text(bad_changelog, filename="CHANGELOG.md")
        self.assertTrue(any(v["rule"] == "keep-a-changelog" for v in violations))

        good_changelog = (
            "# Changelog\n\n"
            "The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).\n\n"
            "## [1.0.7] - 2026-10-19\n\n"
            "### Added\n"
            "- Enforced Keep a Changelog 1.1.0 across Gitea and GitHub.\n"
        )
        self.assertEqual(check_text(good_changelog, filename="CHANGELOG.md"), [])

        cl_file = self.test_dir / "CHANGELOG.md"
        cl_file.write_text(good_changelog, encoding="utf-8")
        entries = parse_keep_a_changelog(cl_file)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["version"], "1.0.7")
        self.assertEqual(entries[0]["tag"], "v1.0.7")
        self.assertIn("## [1.0.7] - 2026-10-19", entries[0]["body"])
        self.assertIn("### Added", entries[0]["body"])
        self.assertIn("keepachangelog.com", entries[0]["body"])

    def test_explicit_message_amends_snapshot_commit(self):
        """Verifies that an explicit commit message right after a snapshot auto-commit amends HEAD instead of being dropped."""
        import subprocess
        setup_workspace_git(self.test_dir, configure_remote=False)
        (self.test_dir / "scroll.cpp").write_text("int scroll = 1;\n", encoding="utf-8")

        # Simulate mem_save_snapshot auto-commit
        res1 = save_code_commit(self.test_dir, message="Milestone: Smooth scroll v1.0.0", push=False, explicit_message=False)
        self.assertTrue(res1.get("committed"))

        # Immediately call save_code_commit with explicit_message=True on clean tree
        res2 = save_code_commit(self.test_dir, message="feat(smooth-scroll): add v1.0.0 implementation", push=False, explicit_message=True)
        self.assertTrue(res2.get("committed"))
        self.assertTrue(res2.get("amended"))

        # Check git log -1 subject
        subj = subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            cwd=str(self.test_dir),
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(subj, "feat(smooth-scroll): add v1.0.0 implementation")

    def test_gitea_collision_detection_and_telemetry(self):
        """Verifies inspect_remote_repository and init_workspace_gitea halt when a non-empty repo already exists on Gitea."""
        from skillsdb.memory.gitea_sync import inspect_remote_repository, init_workspace_gitea
        info = inspect_remote_repository("AntiGraviton")
        if info.get("exists") and not info.get("empty"):
            setup_workspace_git(self.test_dir, configure_remote=False)
            (self.test_dir / "main.py").write_text("print(1)\n", encoding="utf-8")
            # Calling init_workspace_gitea with an existing non-empty repo name and without --use-existing/--overwrite must halt
            ok = init_workspace_gitea(self.test_dir, repo_name="AntiGraviton", use_existing=False, overwrite=False)
            self.assertFalse(ok, "init_workspace_gitea must return False and halt on repository name collision")


    def test_token_free_git_config_and_credential_scrubbing(self):
        """Verifies that legacy embedded tokens in .git/config are automatically scrubbed and transient auth headers are generated."""
        from skillsdb.memory.gitea_config import (
            _strip_credentials_from_url,
            _scrub_workspace_git_config_credentials,
            _get_git_auth_cmd_prefix,
        )
        raw_url = "http://MIckyFoley:secret123456@127.0.0.1:3000/MIckyFoley/SampleRepo.git"
        self.assertEqual(
            _strip_credentials_from_url(raw_url),
            "http://127.0.0.1:3000/MIckyFoley/SampleRepo.git",
        )

        setup_workspace_git(self.test_dir, configure_remote=False)
        git_cfg = self.test_dir / ".git" / "config"
        git_cfg.write_text(
            '[core]\n\trepositoryformatversion = 0\n[remote "origin"]\n\turl = http://MIckyFoley:secret123456@127.0.0.1:3000/MIckyFoley/SampleRepo.git\n',
            encoding="utf-8",
        )
        scrubbed = _scrub_workspace_git_config_credentials(self.test_dir, db_path=self.db_path)
        self.assertTrue(scrubbed)
        cleaned_text = git_cfg.read_text(encoding="utf-8")
        self.assertNotIn("secret123456", cleaned_text)
        self.assertIn("url = http://127.0.0.1:3000/MIckyFoley/SampleRepo.git", cleaned_text)

        prefix = _get_git_auth_cmd_prefix()
        self.assertEqual(prefix[0], "git")
        self.assertIn("http.extraHeader=Authorization: Basic ", " ".join(prefix))

    def test_cross_platform_python_installer_helpers(self):
        """Verifies Linux/macOS/Windows URL resolution, systemd user unit, launchd plist, and app.ini generation in install_antigraviton.py."""
        import install_antigraviton as ia
        self.assertIn("linux-amd64", ia.get_gitea_download_url("1.22.6", "linux", "x86_64"))
        self.assertIn("linux-arm64", ia.get_gitea_download_url("1.22.6", "linux", "aarch64"))
        self.assertIn("darwin-10.12-arm64", ia.get_gitea_download_url("1.22.6", "darwin", "arm64"))
        self.assertIn("windows-4.0-amd64.exe", ia.get_gitea_download_url("1.22.6", "win32", "AMD64"))

        unit = ia.generate_systemd_user_unit(self.test_dir, self.test_dir / "gitea", self.test_dir / "custom/conf/app.ini")
        self.assertIn("Restart=on-failure", unit)
        self.assertIn("WantedBy=default.target", unit)

        plist = ia.generate_launchd_plist(self.test_dir, self.test_dir / "gitea", self.test_dir / "custom/conf/app.ini")
        self.assertIn("dev.local.antigraviton.gitea", plist)
        self.assertIn("<key>KeepAlive</key>", plist)

        tpl = Path(__file__).parent.parent / "gitea-service" / "custom" / "conf" / "app.example.ini"
        ini = ia.generate_gitea_app_ini(
            tpl.read_text(encoding="utf-8"),
            self.test_dir,
            3000,
            "micky",
            "sec123",
            "tok123",
            "jwt123",
            "/usr/bin/git",
        )
        self.assertIn("RUN_USER = micky", ini)
        self.assertIn("SECRET_KEY = sec123", ini)
        self.assertIn("TYPE = immediate", ini)


if __name__ == "__main__":
    unittest.main()
