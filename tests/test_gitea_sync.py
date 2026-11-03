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
        repo_root = Path(os.path.abspath(__file__)).parent.parent
        workspaces = discover_all_workspaces(base_dir=repo_root.parent)
        self.assertGreaterEqual(len(workspaces), 1)

    def test_gitea_service_templates_and_relocation_regex(self):
        """Verifies app.example.ini includes LevelDB-free immediate queues and deploy-gitea.ps1 self-heals WORK_PATH."""
        repo_root = Path(os.path.abspath(__file__)).parent.parent
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

        from unittest.mock import patch
        with patch(
            "skillsdb.memory.gitea_config.get_gitea_config",
            return_value={"url": "http://127.0.0.1:3000", "user": "MIckyFoley", "token": "secret123456", "service_name": "gitea"},
        ):
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

    def test_enterprise_sqlite_connection_and_deploy_safety(self):
        """Verifies managed_connection closing, 30s busy_timeout, safe_deploy_database WAL preservation, and CLI decision flag aliases."""
        from skillsdb.core.db import managed_connection, init_db
        from skillsdb.cli import build_parser
        import deploy as dep

        test_central_db = self.test_dir / "central_test.db"
        with managed_connection(test_central_db, init_schema=True) as conn:
            bt = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
            self.assertEqual(bt, 30000)
            jmode = conn.execute("PRAGMA journal_mode;").fetchone()[0].lower()
            self.assertEqual(jmode, "wal")
            conn.execute(
                "INSERT INTO runtime_config (key, value, updated_at) VALUES ('gitea_token', 'keep_me_safe', '2026-10-21');"
            )
            conn.execute(
                "INSERT INTO rules (key, name, category, scope, is_active, summary, content, token_estimate, updated_at) "
                "VALUES ('custom-learned-1', 'Custom Rule', 'learned', 'global', 1, 'Sum', 'Body', 10, '2026-10-21');"
            )

        # Connection must be closed after exiting context manager
        with self.assertRaises(Exception):
            conn.execute("SELECT 1;")

        # Second init_db call must be a zero-write-lock fast check
        with managed_connection(test_central_db) as conn2:
            init_db(conn2)
            self.assertFalse(conn2.in_transaction)

        # Verify safe_deploy_database preserves runtime_config and learned rules in-place
        source_template_db = Path(__file__).parent.parent / "database" / "customizations.db"
        if source_template_db.exists():
            dep.safe_deploy_database(source_template_db, test_central_db)
            with managed_connection(test_central_db) as conn3:
                tok = conn3.execute("SELECT value FROM runtime_config WHERE key = 'gitea_token';").fetchone()
                self.assertIsNotNone(tok)
                self.assertEqual(tok[0], "keep_me_safe")
                learned = conn3.execute("SELECT COUNT(*) FROM rules WHERE key = 'custom-learned-1';").fetchone()[0]
                self.assertEqual(learned, 1)

        # Verify CLI parser accepts both positional and flag-based mem-save-decision arguments
        parser = build_parser()
        args_flags = parser.parse_args([
            "mem-save-decision",
            "--topic", "SQLite Architecture",
            "--decision", "Use WAL and managed_connection",
            "--rationale", "Prevents handle leaks on Windows",
        ])
        self.assertEqual(args_flags.opt_title, "SQLite Architecture")
        self.assertEqual(args_flags.opt_content, "Use WAL and managed_connection")
        self.assertEqual(args_flags.rationale, "Prevents handle leaks on Windows")


    def test_semver_immutability_guard(self):
        """Verifies that publish_gitea_release and PreToolUse guardrails block overwriting an existing release tag."""
        import subprocess
        from skillsdb.memory.gitea_sync import publish_gitea_release
        from skillsdb.core.guardrails import handle_pre_tool_guardrail_hook

        repo_dir = self.test_dir / "semver_repo"
        repo_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=str(repo_dir), check=True, capture_output=True)
        (repo_dir / "VERSION").write_text("1.1.0\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=str(repo_dir), check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=t@t.dev", "commit", "-m", "v1.1.0 commit"],
            cwd=str(repo_dir),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=t@t.dev", "tag", "-a", "v1.1.0", "-m", "v1.1.0"],
            cwd=str(repo_dir),
            check=True,
            capture_output=True,
        )

        # Advance HEAD without incrementing VERSION
        (repo_dir / "feature.txt").write_text("new feature\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=str(repo_dir), check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=t@t.dev", "commit", "-m", "new feature without version bump"],
            cwd=str(repo_dir),
            check=True,
            capture_output=True,
        )

        res = publish_gitea_release(str(repo_dir), tag_name="v1.1.0", sync_first=False)
        self.assertIsNone(res)

        # Verify PreToolUse hook blocks git tag -f v1.1.0
        import io
        import json
        from unittest.mock import patch
        fake_stdin = io.StringIO(json.dumps({"toolCall": {"args": {"CommandLine": "git tag -f v1.1.0"}}}))
        fake_stdout = io.StringIO()
        with patch("sys.stdin", fake_stdin), patch("sys.stdout", fake_stdout):
            handle_pre_tool_guardrail_hook()
        hook_res = json.loads(fake_stdout.getvalue())
        self.assertEqual(hook_res.get("decision"), "deny")
        self.assertIn("semver-immutability", hook_res.get("reason", ""))

    def test_v1_4_1_network_pragmas_and_stale_origin_ref_pruning(self):
        """Verifies SMB/UNC network pragma fallback and stale origin tracking ref pruning."""
        import sqlite3
        import subprocess
        from pathlib import Path
        from skillsdb.core.db import _is_network_path, _apply_pragmas
        from skillsdb.memory.gitea_sync import _prune_stale_origin_refs, _extract_repo_name_from_remote_url

        self.assertTrue(_is_network_path(Path(r"\\fileserver\share\customizations.db")))
        self.assertFalse(_is_network_path(self.test_dir / "local.db"))

        net_db = self.test_dir / "net_sim.db"
        conn = sqlite3.connect(str(net_db))
        try:
            _apply_pragmas(conn, is_project_db=False, is_network=True)
            jmode = conn.execute("PRAGMA journal_mode;").fetchone()[0].lower()
            mmap_val = conn.execute("PRAGMA mmap_size;").fetchone()[0]
            self.assertEqual(jmode, "truncate")
            self.assertEqual(mmap_val, 0)
        finally:
            conn.close()

        self.assertEqual(
            _extract_repo_name_from_remote_url("https://github.com/scorpion421/AA-Tachometer.git"),
            "AA-Tachometer",
        )

        repo_dir = self.test_dir / "stale_ref_repo"
        repo_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=str(repo_dir), check=True, capture_output=True)
        (repo_dir / "README.md").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=str(repo_dir), check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=t@t.dev", "commit", "-m", "init"],
            cwd=str(repo_dir),
            check=True,
            capture_output=True,
        )
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(repo_dir), check=True, capture_output=True, text=True
        ).stdout.strip()
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/main", head_sha],
            cwd=str(repo_dir),
            check=True,
            capture_output=True,
        )
        _prune_stale_origin_refs(repo_dir)
        refs_after = subprocess.run(
            ["git", "for-each-ref", "refs/remotes/origin/"],
            cwd=str(repo_dir),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(refs_after, "")


    def test_release_deliverable_artifact_discovery_and_attachment(self):
        """Verifies universal deliverable artifact discovery (.apk, .exe, releases/) and exclusion of unsigned/vendor binaries."""
        from skillsdb.memory.gitea_releases import _discover_project_release_artifacts, _attach_release_binary_assets
        from unittest.mock import patch, MagicMock

        proj = self.test_dir / "Nova-Launcher"
        proj.mkdir(parents=True, exist_ok=True)
        rel_dir = proj / "releases"
        rel_dir.mkdir(parents=True, exist_ok=True)

        # Should be excluded
        (proj / "nova-unsigned.apk").write_bytes(b"unsigned")
        (proj / "nova-aligned.apk").write_bytes(b"aligned")
        (proj / "gitea-28.2.0-windows-amd64.exe").write_bytes(b"vendor")

        # Should be discovered
        clean_apk = proj / "nova-launcher-8.9.7-clean.apk"
        clean_apk.write_bytes(b"PK\x03\x04signed-apk-payload")
        rel_exe = rel_dir / "Nova-Launcher-v8.9.7-setup.exe"
        rel_exe.write_bytes(b"MZ-installer")

        found = _discover_project_release_artifacts(proj, "Nova-Launcher", "v8.9.7")
        found_names = [p.name for p in found]
        self.assertIn("nova-launcher-8.9.7-clean.apk", found_names)
        self.assertIn("Nova-Launcher-v8.9.7-setup.exe", found_names)
        self.assertNotIn("nova-unsigned.apk", found_names)
        self.assertNotIn("nova-aligned.apk", found_names)
        self.assertNotIn("gitea-28.2.0-windows-amd64.exe", found_names)

        mock_resp = MagicMock()
        mock_resp.status = 201
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = False

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            uploaded = _attach_release_binary_assets(
                "http://localhost:3000",
                "MickyFoley",
                "Nova-Launcher",
                42,
                "v8.9.7",
                [],
                "tok123",
                proj,
            )
            self.assertEqual(uploaded, ["Nova-Launcher-v8.9.7-clean-signed.apk"])
            self.assertTrue(mock_urlopen.called)


    def test_v1_5_2_idempotent_tags_historical_release_sync_and_amend_push(self):
        """Verifies v1.5.2 fixes: idempotent annotated tag objects, historical release sync, and snapshot amend race guard."""
        import json
        import subprocess
        from unittest.mock import patch, MagicMock
        from skillsdb.memory.gitea_sync import (
            publish_gitea_release,
            _can_safely_force_replace_remote_snapshot,
        )

        repo_dir = self.test_dir / "v152_repo"
        repo_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=str(repo_dir), check=True, capture_output=True)
        (repo_dir / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        (repo_dir / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [1.0.0] - 2026-10-30\n\n### Added\n- Initial release.\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "."], cwd=str(repo_dir), check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=Antigravity", "-c", "user.email=antigravity@local.dev", "commit", "-m", "Release v1.0.0"],
            cwd=str(repo_dir),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-c", "user.name=Antigravity", "-c", "user.email=antigravity@local.dev", "tag", "-a", "v1.0.0", "-m", "v1.0.0"],
            cwd=str(repo_dir),
            check=True,
            capture_output=True,
        )
        tag_obj_sha_before = subprocess.run(
            ["git", "rev-parse", "v1.0.0"],
            cwd=str(repo_dir),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        # Advance to v1.1.0
        (repo_dir / "VERSION").write_text("1.1.0\n", encoding="utf-8")
        (repo_dir / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [1.1.0] - 2026-10-31\n\n### Added\n- Next release.\n\n## [1.0.0] - 2026-10-30\n\n### Added\n- Initial release.\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "."], cwd=str(repo_dir), check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=Antigravity", "-c", "user.email=antigravity@local.dev", "commit", "-m", "Release v1.1.0"],
            cwd=str(repo_dir),
            check=True,
            capture_output=True,
        )

        mock_resp = MagicMock()
        mock_resp.status = 201
        mock_resp.read.return_value = json.dumps({
            "id": 99,
            "tag_name": "v1.0.0",
            "html_url": "http://localhost:3000/MickyFoley/v152_repo/releases/tag/v1.0.0",
            "assets": [],
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = False

        with patch("urllib.request.urlopen", return_value=mock_resp), patch(
            "skillsdb.memory.gitea_sync.get_gitea_config",
            return_value={"url": "http://localhost:3000", "user": "MickyFoley", "token": "tok123"},
        ):
            rel_url = publish_gitea_release(repo_dir, tag_name="v1.0.0", sync_first=True, sync_github=False)
            self.assertEqual(rel_url, "http://localhost:3000/MickyFoley/v152_repo/releases/tag/v1.0.0")

        tag_obj_sha_after = subprocess.run(
            ["git", "rev-parse", "v1.0.0"],
            cwd=str(repo_dir),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(
            tag_obj_sha_before,
            tag_obj_sha_after,
            "Annotated tag object SHA must remain identical (no git tag -f -a re-creation)",
        )

        # Verify _can_safely_force_replace_remote_snapshot detects an amended auto-snapshot commit
        subprocess.run(
            ["git", "-c", "user.name=Antigravity", "-c", "user.email=antigravity@local.dev", "commit", "--allow-empty", "-m", "[SkillsDB] Auto-snapshot before amend"],
            cwd=str(repo_dir),
            check=True,
            capture_output=True,
        )
        snap_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(repo_dir), check=True, capture_output=True, text=True
        ).stdout.strip()
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/main", snap_sha],
            cwd=str(repo_dir),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-c", "user.name=Antigravity", "-c", "user.email=antigravity@local.dev", "commit", "--amend", "--allow-empty", "-m", "Release v1.1.0 final"],
            cwd=str(repo_dir),
            check=True,
            capture_output=True,
        )
        self.assertTrue(_can_safely_force_replace_remote_snapshot(repo_dir, "main"))


    def test_v1_5_5_workspace_inference_and_mid_session_memory_rehydration(self):
        """Verifies v1.5.5 transcript workspace inference when workspacePaths=[], mid-session memory re-hydration, and mem-add-task alias."""
        import io
        import json
        from unittest.mock import patch
        from skillsdb.cli import build_parser
        from skillsdb.memory.project_memory import (
            handle_pre_invocation_hook,
            mem_save_fact,
            mem_task_add,
        )

        # 1. Verify CLI alias mem-add-task
        parser = build_parser()
        parsed = parser.parse_args(["mem-add-task", "Map Smali classes", "--project", str(self.test_dir)])
        self.assertIn(parsed.command, ("mem-task-add", "mem-add-task"))

        # 2. Populate self.test_dir/.agents/memory.db with a fact and task
        mem_save_fact(self.conn, "surgical_deep_shrink.py:entrypoints", "Lines 1-420 core patcher, 421-890 smali verifier")
        mem_task_add(self.conn, "Verify b1.smali analytics strip", priority="high", status="in_progress")

        # 3. Create fake transcript in fake brain dir referencing self.test_dir files with repeated reads at step 60
        fake_brain = self.test_dir / "fake_brain"
        cid = "conv-v155-test"
        log_dir = fake_brain / cid / ".system_generated" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        t_file = log_dir / "transcript.jsonl"
        target_py = (self.test_dir / "surgical_deep_shrink.py").as_posix()
        lines = [
            json.dumps({"step_index": 1, "type": "USER_INPUT", "source": "USER_EXPLICIT", "created_at": "2026-10-31T10:00:00Z", "content": f"Analyze {target_py}"}),
            json.dumps({"step_index": 55, "type": "PLANNER_RESPONSE", "tool_calls": [{"name": "read_file", "args": {"Path": target_py}}]}),
            json.dumps({"step_index": 56, "type": "PLANNER_RESPONSE", "tool_calls": [{"name": "read_file", "args": {"Path": target_py}}]}),
            json.dumps({"step_index": 57, "type": "PLANNER_RESPONSE", "tool_calls": [{"name": "read_file", "args": {"Path": target_py}}]}),
        ]
        t_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # Turn 1 with workspacePaths=[] must infer self.test_dir and auto-load .agents/memory.db
        payload_t1 = json.dumps({"conversationId": cid, "workspacePaths": [], "brainDir": str(fake_brain), "invocationNum": 1})
        out_t1 = io.StringIO()
        with patch("skillsdb.memory.project_memory._HOOK_STATE_FILE", self.test_dir / "hook_state.json"), patch("sys.stdin", io.StringIO(payload_t1)), patch("sys.stdout", out_t1):
            handle_pre_invocation_hook()
        res_t1 = json.loads(out_t1.getvalue())
        msg_t1 = res_t1["injectSteps"][0]["ephemeralMessage"]
        self.assertIn("PROJECT MEMORY CONTEXT", msg_t1)
        self.assertIn("surgical_deep_shrink.py:entrypoints", msg_t1)
        self.assertIn("Verify b1.smali analytics strip", msg_t1)

        # Advance transcript to step 105 (next 50-step bucket) within the same epoch and verify mid-session re-hydration + alert
        lines.append(json.dumps({"step_index": 105, "type": "PLANNER_RESPONSE", "tool_calls": [{"name": "read_file", "args": {"Path": target_py}}]}))
        t_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        out_t2 = io.StringIO()
        with patch("skillsdb.memory.project_memory._HOOK_STATE_FILE", self.test_dir / "hook_state.json"), patch("sys.stdin", io.StringIO(payload_t1)), patch("sys.stdout", out_t2):
            handle_pre_invocation_hook()
        res_t2 = json.loads(out_t2.getvalue())
        msg_t2 = res_t2["injectSteps"][0]["ephemeralMessage"]
        self.assertIn("SKILLSDB TURN-ECONOMY & DRIFT GUARD (Step 105)", msg_t2)
        self.assertIn("PROJECT MEMORY ALERT", msg_t2)
        self.assertIn("PROJECT MEMORY CONTEXT (Auto-Hydrated from .agents/memory.db)", msg_t2)
        self.assertIn("surgical_deep_shrink.py:entrypoints", msg_t2)


    def test_v1_5_6_pre_tool_use_overwrite_and_unversioned_sync(self):
        """Verifies v1.5.6 PreToolUse overwrite for read_file salami-slicing, run_command WaitMsBeforeAsync=10000, subagent firewall, and --allow-unversioned."""
        import io
        import json
        from unittest.mock import patch
        from skillsdb.cli import build_parser
        from skillsdb.core.guardrails import handle_pre_tool_guardrail_hook

        # 1. Verify CLI flag --allow-unversioned
        parser = build_parser()
        parsed = parser.parse_args(["gitea-sync", "-m", "Doc update", "--allow-unversioned", "--github"])
        self.assertTrue(parsed.allow_unversioned)
        self.assertTrue(parsed.github)

        # 2. Verify read_file window expansion on <= 800 line file
        small_file = self.test_dir / "small_module.py"
        small_file.write_text("\n".join(f"line_{i} = {i}" for i in range(1, 451)) + "\n", encoding="utf-8")
        p_small = json.dumps({
            "toolCall": {"name": "read_file", "args": {"Path": str(small_file), "StartLine": 20, "EndLine": 60}}
        })
        out_s = io.StringIO()
        with patch("sys.stdin", io.StringIO(p_small)), patch("sys.stdout", out_s):
            handle_pre_tool_guardrail_hook()
        res_s = json.loads(out_s.getvalue())
        self.assertEqual(res_s["decision"], "allow")
        self.assertEqual(res_s["overwrite"], {"StartLine": 1, "EndLine": 450})

        # 3. Verify read_file window expansion on > 800 line file (e.g. 2310 lines like d.smali)
        large_file = self.test_dir / "d.smali"
        large_file.write_text("\n".join(f".line {i}" for i in range(1, 2311)) + "\n", encoding="utf-8")
        p_large = json.dumps({
            "toolCall": {"name": "read_file", "args": {"Path": str(large_file), "StartLine": 1780, "EndLine": 1820}}
        })
        out_l = io.StringIO()
        with patch("sys.stdin", io.StringIO(p_large)), patch("sys.stdout", out_l):
            handle_pre_tool_guardrail_hook()
        res_l = json.loads(out_l.getvalue())
        self.assertEqual(res_l["decision"], "allow")
        self.assertIn("overwrite", res_l)
        self.assertLessEqual(res_l["overwrite"]["StartLine"], 1500)
        self.assertGreaterEqual(res_l["overwrite"]["EndLine"], 2100)
        self.assertEqual(res_l["overwrite"]["EndLine"] - res_l["overwrite"]["StartLine"] + 1, 650)

        # 4. Verify run_command WaitMsBeforeAsync=5000 auto-upgrades to 10000
        p_cmd = json.dumps({
            "toolCall": {"name": "run_command", "args": {"CommandLine": "python build.py", "WaitMsBeforeAsync": 5000}}
        })
        out_c = io.StringIO()
        with patch("sys.stdin", io.StringIO(p_cmd)), patch("sys.stdout", out_c):
            handle_pre_tool_guardrail_hook()
        res_c = json.loads(out_c.getvalue())
        self.assertEqual(res_c["decision"], "allow")
        self.assertEqual(res_c["overwrite"], {"WaitMsBeforeAsync": 10000})

        # 5. Verify Subagent Firewall blocks 5th consecutive exploratory search command
        t_file = self.test_dir / "search_transcript.jsonl"
        search_step = json.dumps({
            "step_index": 10,
            "type": "PLANNER_RESPONSE",
            "tool_calls": [{"name": "run_command", "args": {"CommandLine": "python -c \"from pathlib import Path; print(Path('a').read_text())\""}}],
        })
        t_file.write_text("\n".join([search_step] * 4) + "\n", encoding="utf-8")
        p_search = json.dumps({
            "transcriptPath": str(t_file),
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "python -c \"from pathlib import Path; print(Path('b').read_text())\"", "WaitMsBeforeAsync": 5000},
            },
        })
        out_sf = io.StringIO()
        with patch("sys.stdin", io.StringIO(p_search)), patch("sys.stdout", out_sf):
            handle_pre_tool_guardrail_hook()
        res_sf = json.loads(out_sf.getvalue())
        self.assertEqual(res_sf["decision"], "deny")
        self.assertIn("Subagent Firewall", res_sf["reason"])

    def test_v1_5_7_epoch_aware_overwrite_and_subagent_auto_persistence(self):
        """Verifies v1.5.7 epoch-aware read_file overwrite deduplication and automatic subagent finding persistence into .agents/memory.db."""
        import io
        import json
        from unittest.mock import patch
        from skillsdb.core.guardrails import handle_pre_tool_guardrail_hook
        from skillsdb.memory.project_memory import handle_pre_invocation_hook

        # 1. Epoch-aware read_file overwrite: 1st read in epoch expands, 2nd read in same epoch stays compact, read after <CONTEXT_SUMMARY> expands again
        mod_file = self.test_dir / "patcher_script.py"
        mod_file.write_text("\n".join(f"x_{i} = {i}" for i in range(1, 501)) + "\n", encoding="utf-8")
        t_file = self.test_dir / "epoch_read_transcript.jsonl"
        t_file.write_text("", encoding="utf-8")

        req_payload = json.dumps({
            "transcriptPath": str(t_file),
            "toolCall": {"name": "read_file", "args": {"Path": str(mod_file), "StartLine": 400, "EndLine": 430}},
        })
        out1 = io.StringIO()
        with patch("sys.stdin", io.StringIO(req_payload)), patch("sys.stdout", out1):
            handle_pre_tool_guardrail_hook()
        res1 = json.loads(out1.getvalue())
        self.assertEqual(res1.get("overwrite"), {"StartLine": 1, "EndLine": 500})

        # Record the 1st read in transcript.jsonl (same uncompacted epoch)
        t_file.write_text(
            json.dumps({
                "step_index": 12,
                "type": "PLANNER_RESPONSE",
                "tool_calls": [{"name": "read_file", "args": {"Path": str(mod_file), "StartLine": 1, "EndLine": 500}}],
            }) + "\n",
            encoding="utf-8",
        )
        out2 = io.StringIO()
        with patch("sys.stdin", io.StringIO(req_payload)), patch("sys.stdout", out2):
            handle_pre_tool_guardrail_hook()
        res2 = json.loads(out2.getvalue())
        self.assertEqual(res2.get("decision"), "allow")
        self.assertNotIn("overwrite", res2, "Follow-up read in same uncompacted epoch must not re-expand to 500 lines")

        # Append a <CONTEXT_SUMMARY> compaction boundary -> next read must expand again
        with open(t_file, "a", encoding="utf-8") as tf:
            tf.write(json.dumps({"step_index": 20, "type": "USER_INPUT", "content": "<CONTEXT_SUMMARY>Compacted</CONTEXT_SUMMARY>"}) + "\n")
        out3 = io.StringIO()
        with patch("sys.stdin", io.StringIO(req_payload)), patch("sys.stdout", out3):
            handle_pre_tool_guardrail_hook()
        res3 = json.loads(out3.getvalue())
        self.assertEqual(res3.get("overwrite"), {"StartLine": 1, "EndLine": 500})

        # 2. Automatic subagent finding persistence into .agents/memory.db
        fake_brain = self.test_dir / "brain_v157"
        cid = "conv-v157-subagent"
        log_dir = fake_brain / cid / ".system_generated" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        sub_t = log_dir / "transcript.jsonl"
        sub_t.write_text(
            json.dumps({
                "step_index": 1,
                "type": "USER_INPUT",
                "source": "USER_EXPLICIT",
                "created_at": "2026-11-01T12:00:00Z",
                "content": f"Fix crash in {mod_file.as_posix()}",
            }) + "\n" +
            json.dumps({
                "step_index": 42,
                "type": "SYSTEM_MESSAGE",
                "source": "SYSTEM",
                "created_at": "2026-11-01T12:05:00Z",
                "content": "<SYSTEM_MESSAGE>\n[Message] timestamp=2026-11-01T12:05:00Z sender=32634716-175d-4825-bcb2-870ae13bcdaf priority=MESSAGE_PRIORITY_HIGH content=Root cause in a10/o.smali line 591: invoke-direct on nulled register v3 must be replaced with nop.\n</SYSTEM_MESSAGE>",
            }) + "\n",
            encoding="utf-8",
        )
        hook_in = json.dumps({
            "conversationId": cid,
            "workspacePaths": [str(self.test_dir)],
            "brainDir": str(fake_brain),
            "invocationNum": 1,
        })
        hook_out = io.StringIO()
        with patch("skillsdb.memory.project_memory._HOOK_STATE_FILE", self.test_dir / "hook_state_157.json"), patch("sys.stdin", io.StringIO(hook_in)), patch("sys.stdout", hook_out):
            handle_pre_invocation_hook()
        hook_res = json.loads(hook_out.getvalue())
        eph = hook_res["injectSteps"][0]["ephemeralMessage"]
        self.assertIn("subagent_finding_step_42", eph)
        self.assertIn("Root cause in a10/o.smali line 591", eph)

    def test_v1_5_8_standalone_skillsdb_without_gitea(self):
        """Verifies that standalone SkillsDB-only users (without Gitea) work 100% cleanly with zero false Gitea alerts or push warnings."""
        import io
        from unittest.mock import patch
        from skillsdb.memory.gitea_sync import (
            is_gitea_configured,
            get_workspace_git_telemetry,
            sync_workspace,
        )
        from skillsdb.memory.project_memory import mem_save_snapshot

        standalone_cfg = {
            "url": "http://127.0.0.1:3000",
            "user": "",
            "token": "",
            "workspaces_root": "",
            "remote_share": "",
            "service_name": "gitea",
            "enabled": "false",
        }
        setup_workspace_git(self.test_dir, configure_remote=False)
        (self.test_dir / "core_engine.py").write_text("VALUE = 42\n", encoding="utf-8")

        with patch("skillsdb.memory.gitea_sync.get_gitea_config", return_value=standalone_cfg), patch(
            "skillsdb.memory.gitea_releases.get_gitea_config", return_value=standalone_cfg
        ):
            self.assertFalse(is_gitea_configured(self.test_dir))

            # 1. mem_save_snapshot must commit locally, emit (saved locally) + [COMPACT MILESTONE TIP], and NO Gitea push warning
            snap_out = io.StringIO()
            with patch("sys.stdout", snap_out):
                mem_save_snapshot(
                    self.conn,
                    summary="Milestone: Standalone SkillsDB commit",
                    conversation_id="conv-standalone-158",
                    db_path=self.db_path,
                    auto_commit=True,
                    silent=False,
                )
            snap_txt = snap_out.getvalue()
            self.assertIn("(saved locally)", snap_txt)
            self.assertIn("[COMPACT MILESTONE TIP]", snap_txt)
            self.assertNotIn("WARNING: Gitea push FAILED", snap_txt)

            # 2. get_workspace_git_telemetry must return local Git status without [CRITICAL GITEA ALERT]
            with patch("skillsdb.memory.gitea_sync._is_temp_workspace", return_value=False):
                telem = get_workspace_git_telemetry(self.test_dir)
            self.assertIsNotNone(telem)
            self.assertIn("Git Telemetry: branch 'main'", telem)
            self.assertIn("local only", telem)
            self.assertNotIn("CRITICAL GITEA ALERT", telem)

            # 3. sync_workspace in standalone mode must commit locally and create local Keep a Changelog tag without warning
            (self.test_dir / "VERSION").write_text("1.0.0\n", encoding="utf-8")
            (self.test_dir / "CHANGELOG.md").write_text(
                "# Changelog\n\n## [1.0.0] - 2026-11-01\n\n### Added\n- Standalone SkillsDB mode.\n",
                encoding="utf-8",
            )
            sync_out = io.StringIO()
            with patch("skillsdb.memory.gitea_sync._is_temp_workspace", return_value=False), patch(
                "skillsdb.memory.gitea_releases._is_temp_workspace", return_value=False
            ), patch("sys.stdout", sync_out):
                ok = sync_workspace(self.test_dir, message="Release v1.0.0 standalone")
            self.assertTrue(ok)
            sync_txt = sync_out.getvalue()
            self.assertIn("Saved locally in Git (Standalone SkillsDB-Only Mode)", sync_txt)
            self.assertIn("[COMPACT MILESTONE TIP]", sync_txt)
            self.assertNotIn("[WARN] Commit created locally, push skipped or failed", sync_txt)
            from skillsdb.memory.gitea_config import _is_excluded_workspace_path
            self.assertTrue(_is_excluded_workspace_path(Path("C:/")))
            self.assertTrue(_is_excluded_workspace_path(Path("D:/")))



    def test_v1_5_11_home_and_enterprise_ad_dual_scenario_hardening(self):
        """Verifies v1.5.11 Home vs Enterprise AD profile resolution, UNC-to-drive normalization, POSIX wrapper, and fast service path detection."""
        from unittest.mock import patch, MagicMock
        from skillsdb.config import resolve_user_home
        from skillsdb.memory.gitea_config import _normalize_workspace_path, _is_excluded_workspace_path
        import install_antigraviton as ia
        import deploy as dep

        # 1. Enterprise AD scenario: HOME=Q:\ but USERPROFILE=C:\Users\corpuser
        with patch.dict(os.environ, {"USERPROFILE": r"C:\Users\corpuser", "HOME": "Q:\\", "HOMEDRIVE": "Q:", "HOMEPATH": "\\", "HOMESHARE": r"\\fileserver01.corp.example.com\home$\corpuser"}):
            if os.name == "nt":
                self.assertEqual(str(resolve_user_home()), r"C:\Users\corpuser")
                self.assertEqual(str(dep.resolve_user_home()), r"C:\Users\corpuser")
                self.assertEqual(str(ia.resolve_user_home()), r"C:\Users\corpuser")
                self.assertTrue(_is_excluded_workspace_path(Path(r"\\fileserver01.corp.example.com\home$\corpuser")))
                self.assertTrue(_is_excluded_workspace_path(Path("Q:\\")))
                with patch.object(Path, "exists", return_value=True):
                    norm_p = _normalize_workspace_path(Path(r"\\fileserver01.corp.example.com\home$\corpuser\Antigravity\AntiGraviton"))
                    self.assertEqual(str(norm_p).lower(), r"q:\antigravity\antigraviton")

        # 2. Home scenario: USERPROFILE=C:\Users\MickyFoley, secondary drive D:\Gitea
        with patch.dict(os.environ, {"USERPROFILE": r"C:\Users\MickyFoley", "HOME": r"C:\Users\MickyFoley", "HOMEDRIVE": "C:", "HOMEPATH": r"\Users\MickyFoley", "HOMESHARE": ""}):
            if os.name == "nt":
                self.assertEqual(str(resolve_user_home()), r"C:\Users\MickyFoley")
                self.assertTrue(_is_excluded_workspace_path(Path(r"C:\Users\MickyFoley")))
                home_proj = _normalize_workspace_path(Path(r"D:\Antigravity\AntiGraviton"))
                self.assertEqual(str(home_proj).lower(), r"d:\antigravity\antigraviton")

        # 3. Verify sc.exe qc gitea binary path parser for both D:\Gitea (Home) and C:\Gitea (Enterprise)
        if os.name == "nt":
            mock_sc_home = MagicMock(returncode=0, stdout='        BINARY_PATH_NAME   : "D:\\Gitea\\gitea.exe" web --config "D:\\Gitea\\custom\\conf\\app.ini"\n')
            with patch("subprocess.run", return_value=mock_sc_home):
                self.assertEqual(ia.detect_windows_gitea_service_path(), r"D:\Gitea")
            mock_sc_ent = MagicMock(returncode=0, stdout='        BINARY_PATH_NAME   : "C:\\Gitea\\gitea.exe" web --config "C:\\Gitea\\custom\\conf\\app.ini"\n')
            with patch("subprocess.run", return_value=mock_sc_ent):
                self.assertEqual(ia.detect_windows_gitea_service_path(), r"C:\Gitea")


    def test_v1_5_12_subagent_firewall_remote_wmi_host_exemption(self):
        """Verifies v1.5.12 exemption of targeted single-host / remote WMI / CIM / DPAPI / UNC commands from Subagent Firewall and consecutive streak reset."""
        import io
        import json
        from unittest.mock import patch
        from skillsdb.core.guardrails import _is_exploratory_search_cmd, handle_pre_tool_guardrail_hook

        # 1. Remote WMI/CIM, Invoke-Command, DPAPI Import-Clixml, and UNC share commands must NOT be classified as local exploratory searches
        remote_cmds = [
            "$cred = Import-Clixml \"$env:USERPROFILE\\.gemini\\config\\credentials\\corpuser.adm.credential.xml\"; Get-CimInstance -ComputerName ITX001396 -ClassName CCM_SoftUpdate | Select-String 'State'",
            "Invoke-Command -ComputerName ITX001396 -Credential $cred -ScriptBlock { Get-ChildItem C:\\Windows\\CCM\\Logs\\*.log | Select-String 'Error' }",
            "New-PSDrive -Name ITXC -PSProvider FileSystem -Root '\\\\192.168.236.100\\c$' -Credential $lapsCred; Get-ChildItem ITXC:\\Windows\\Logs",
            "Get-WmiObject -Namespace 'root\\ccm\\ClientSDK' -Class CCM_SoftwareUpdate -ComputerName ITX001396",
            "Remove-CimInstance -CimSession $sess -Query 'SELECT * FROM CCM_UpdateStatus'",
        ]
        for rcmd in remote_cmds:
            self.assertFalse(_is_exploratory_search_cmd(rcmd), f"Remote host command should be exempt: {rcmd}")

        # 2. 5+ consecutive remote WMI/host diagnostic commands on ITX001396 must be ALLOWED by handle_pre_tool_guardrail_hook
        t_file = self.test_dir / "wmi_host_transcript.jsonl"
        wmi_steps = [
            json.dumps({
                "step_index": 10 + idx,
                "type": "PLANNER_RESPONSE",
                "tool_calls": [{"name": "run_command", "args": {"CommandLine": rcmd}}],
            })
            for idx, rcmd in enumerate(remote_cmds[:4])
        ]
        t_file.write_text("\n".join(wmi_steps) + "\n", encoding="utf-8")
        p_wmi = json.dumps({
            "transcriptPath": str(t_file),
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": remote_cmds[4], "WaitMsBeforeAsync": 10000},
            },
        })
        out_wmi = io.StringIO()
        with patch("sys.stdin", io.StringIO(p_wmi)), patch("sys.stdout", out_wmi):
            handle_pre_tool_guardrail_hook()
        res_wmi = json.loads(out_wmi.getvalue())
        self.assertEqual(res_wmi["decision"], "allow")

        # 3. A non-exploratory run_command turn between local searches resets the consecutive search_streak
        local_search = "Get-ChildItem -Recurse src | Select-String 'TODO'"
        self.assertTrue(_is_exploratory_search_cmd(local_search))
        mixed_steps = [
            json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command", "args": {"CommandLine": local_search}}]}),
            json.dumps({"step_index": 2, "type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command", "args": {"CommandLine": local_search}}]}),
            json.dumps({"step_index": 3, "type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command", "args": {"CommandLine": "python build.py"}}]}),
            json.dumps({"step_index": 4, "type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command", "args": {"CommandLine": local_search}}]}),
            json.dumps({"step_index": 5, "type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command", "args": {"CommandLine": local_search}}]}),
        ]
        t_file.write_text("\n".join(mixed_steps) + "\n", encoding="utf-8")
        p_mixed = json.dumps({
            "transcriptPath": str(t_file),
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": local_search, "WaitMsBeforeAsync": 10000},
            },
        })
        out_mix = io.StringIO()
        with patch("sys.stdin", io.StringIO(p_mixed)), patch("sys.stdout", out_mix):
            handle_pre_tool_guardrail_hook()
        res_mix = json.loads(out_mix.getvalue())
        self.assertEqual(res_mix["decision"], "allow")


    def test_v1_5_13_single_call_release_sync_and_force_all_flag(self):
        """Verifies v1.5.13 single-call batch release lookup on Gitea/GitHub, O(1) skip of unchanged historical releases, and --force-all CLI flag."""
        import json
        from unittest.mock import patch, MagicMock
        from skillsdb.cli import build_parser
        from skillsdb.memory.gitea_releases import sync_changelog_releases, parse_keep_a_changelog

        # 1. Verify --force-all CLI flag on gitea-sync
        parser = build_parser()
        args_default = parser.parse_args(["gitea-sync", "--releases", "--github"])
        self.assertTrue(args_default.releases)
        self.assertFalse(args_default.force_all)
        args_force = parser.parse_args(["gitea-sync", "--releases", "--force-all", "--github"])
        self.assertTrue(args_force.force_all)

        # 2. Create a workspace with 3 Keep a Changelog versions (v1.0.0, v1.0.1, v1.0.2)
        repo_dir = self.test_dir / "fast_rel_repo"
        repo_dir.mkdir(parents=True, exist_ok=True)
        cl_content = (
            "# Changelog\n\n"
            "## [1.0.2] - 2026-11-02\n\n### Added\n- Fast batch release sync.\n\n"
            "## [1.0.1] - 2026-11-01\n\n### Fixed\n- Bug fix.\n\n"
            "## [1.0.0] - 2026-10-30\n\n### Added\n- Initial release.\n"
        )
        (repo_dir / "CHANGELOG.md").write_text(cl_content, encoding="utf-8")
        entries = parse_keep_a_changelog(repo_dir / "CHANGELOG.md")
        self.assertEqual([e["tag"] for e in entries], ["v1.0.2", "v1.0.1", "v1.0.0"])

        # Simulate v1.0.0 and v1.0.1 already existing on Gitea and GitHub, while v1.0.2 is new
        existing_gitea = [
            {"id": 1, "tag_name": "v1.0.0", "body": entries[2]["body"], "assets": []},
            {"id": 2, "tag_name": "v1.0.1", "body": entries[1]["body"], "assets": []},
        ]
        http_calls = []

        def fake_urlopen(req, timeout=5.0):
            method = req.get_method()
            url = req.full_url
            http_calls.append((method, url))
            m = MagicMock()
            m.status = 200 if method == "GET" else 201
            if method == "GET":
                m.read.return_value = json.dumps(existing_gitea).encode("utf-8")
            else:
                m.read.return_value = json.dumps({"id": 3, "tag_name": "v1.0.2", "assets": []}).encode("utf-8")
            m.__enter__.return_value = m
            m.__exit__.return_value = False
            return m

        gh_sub_calls = []

        def fake_sub_run(cmd, *a, **kw):
            gh_sub_calls.append(list(cmd))
            res = MagicMock(returncode=0, stdout="", stderr="")
            if cmd[:2] == ["gh", "api"]:
                res.stdout = json.dumps([
                    {"tag_name": "v1.0.0", "body": entries[2]["body"]},
                    {"tag_name": "v1.0.1", "body": entries[1]["body"]},
                ])
            return res

        with patch("skillsdb.memory.gitea_releases._is_temp_workspace", return_value=False), patch(
            "skillsdb.memory.gitea_releases.get_gitea_config",
            return_value={"url": "http://localhost:3000", "user": "CorpUser", "token": "tok123", "enabled": "true"},
        ), patch("skillsdb.memory.gitea_releases._resolve_github_repo_slug", return_value="scorpion421/fast_rel_repo"), patch(
            "urllib.request.urlopen", side_effect=fake_urlopen
        ), patch("subprocess.run", side_effect=fake_sub_run):
            synced = sync_changelog_releases(repo_dir, sync_github=True, overwrite_existing=False)

        self.assertEqual(synced, ["v1.0.2"])
        # Gitea must only receive 1 GET + 1 POST (zero PATCH calls for v1.0.0 and v1.0.1)
        self.assertEqual(len(http_calls), 2)
        self.assertEqual(http_calls[0][0], "GET")
        self.assertEqual(http_calls[1][0], "POST")
        # GitHub must only receive git push --tags, 1 'gh api', and 1 'gh release create v1.0.2' (zero 'gh release view' or 'edit' on v1.0.0/v1.0.1)
        gh_release_cmds = [c for c in gh_sub_calls if len(c) >= 3 and c[0] == "gh" and c[1] == "release"]
        self.assertEqual(len(gh_release_cmds), 1)
        self.assertEqual(gh_release_cmds[0][:4], ["gh", "release", "create", "v1.0.2"])


    def test_v1_5_14_hierarchical_it_admin_logs_and_personal_identifier_guardrail(self):
        """Verifies v1.5.14 C:\\IT_ADMIN_LOGS\\<USERNAME>\\<TASK>\\<TARGET>\\<Date>\\<hh_mm_ss>\\ standard and personal identifier leak guardrail."""
        from skillsdb.core.guardrails import check_text

        agents_md = (Path(__file__).resolve().parent.parent / "plugin" / "rules" / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("C:\\IT_ADMIN_LOGS\\<USERNAME>\\<TASK>\\<TARGET>\\<Date>\\<hh_mm_ss>\\", agents_md)
        self.assertIn("\\IT_ADMIN_LOGS\\<USERNAME>\\", agents_md)
        self.assertEqual(check_text(agents_md, filename="AGENTS.md"), [])

        leaked_snippet = "Origin: " + "ITX000" + "133." + "atli" + "west.local"
        violations = check_text(leaked_snippet, filename="script.ps1")
        self.assertTrue(any(v["rule"] == "secret-leak-detection" for v in violations))


    def test_v1_5_15_multi_pool_quota_router_and_13_model_effort_matrix(self):
        """Verifies v1.5.15 13-combination model+effort catalog, 4-pool quota router, PreToolUse invoke_subagent overwrite, and gemini-digest."""
        import io
        import json
        from unittest.mock import patch
        from skillsdb.core.detector import (
            parse_antigravity_model_spec,
            set_profile,
        )
        from skillsdb.core.quota_router import (
            compute_routing_decision,
            execute_gemini_digest,
            execute_ast_map,
            get_ledger_24h_summary,
        )
        from skillsdb.core.guardrails import handle_pre_tool_guardrail_hook

        # 1. Verify all 13 internal Antigravity model + effort combinations (4 models x 3 efforts + GPT-OSS 120B fixed at Medium)
        four_three_models = [
            ("Gemini 4 Argon", "gemini-4-argon", "agy_gemini_high"),
            ("Gemini 3.8 Flash", "gemini-3.8-flash", "agy_gemini_fast"),
            ("Claude Opus 5.5", "claude-opus-5.5", "agy_claude"),
            ("Claude Sonnet 5.5", "claude-sonnet-5.5", "agy_claude"),
        ]
        for base_label, fam_key, expected_pool in four_three_models:
            weights = {}
            for eff in ("Fast", "Medium", "High"):
                spec = parse_antigravity_model_spec(f"{base_label} ({eff})")
                self.assertIsNotNone(spec)
                self.assertEqual(spec["model_family"], fam_key)
                self.assertEqual(spec["pool"], expected_pool)
                self.assertEqual(spec["effort_level"], eff.lower())
                self.assertEqual(spec["supported_efforts"], ["fast", "medium", "high"])
                weights[eff.lower()] = spec["quota_weight"]
            self.assertLess(weights["fast"], weights["medium"])
            self.assertLess(weights["medium"], weights["high"])

        # GPT-OSS 120B must always be pinned to Medium even if Fast or High is passed
        for raw_gpt in ("GPT-OSS 120B (Medium)", "GPT-OSS 120B (High)", "GPT-OSS 120B Fast"):
            gpt_spec = parse_antigravity_model_spec(raw_gpt)
            self.assertIsNotNone(gpt_spec)
            self.assertEqual(gpt_spec["model_family"], "gpt-oss-120b")
            self.assertEqual(gpt_spec["pool"], "agy_open_weight")
            self.assertEqual(gpt_spec["effort_level"], "medium")
            self.assertEqual(gpt_spec["supported_efforts"], ["medium"])

        # 2. Verify obsolete Gemini 3.1 Pro is removed from AGENTS.md
        agents_md = (Path(__file__).resolve().parent.parent / "plugin" / "rules" / "AGENTS.md").read_text(encoding="utf-8")
        self.assertNotIn("3.1 Pro", agents_md)
        self.assertIn("Gemini 4 Argon", agents_md)
        self.assertIn("Gemini 3.8 Flash", agents_md)
        self.assertIn("Claude Opus 5.5", agents_md)
        self.assertIn("Claude Sonnet 5.5", agents_md)
        self.assertIn("GPT-OSS 120B", agents_md)

        # 3. Verify 2D Account Tier (ultra/pro/lite) x Effort routing decision & PreToolUse invoke_subagent overwrite
        temp_central_db = self.test_dir / "central_v1515.db"
        fake_brain = self.test_dir / "brain_v1515"
        fake_brain.mkdir(parents=True, exist_ok=True)

        set_profile(tier_name="ultra", account_tier="ultra", effort_level="high", quota_mode="balanced", db_path=temp_central_db)
        dec_ultra = compute_routing_decision(db_path=temp_central_db, model_name_hint="Gemini 4 Argon (High)", brain_dir=fake_brain)
        self.assertEqual(dec_ultra["account_tier"], "ultra")
        self.assertEqual(dec_ultra["digest_line_threshold"], 250)
        self.assertEqual(dec_ultra["subagent_research_model"], "flash")

        # PreToolUse hook on invoke_subagent(TypeName='research', Model='inherit') must overwrite Model -> 'flash'
        sub_req_res = json.dumps({
            "dbPath": str(temp_central_db),
            "brainDir": str(fake_brain),
            "modelName": "Gemini 4 Argon (High)",
            "toolCall": {
                "name": "invoke_subagent",
                "args": {"TypeName": "research", "Role": "Codebase Researcher", "Prompt": "Find all callers", "Model": "inherit"},
            },
        })
        out_sub1 = io.StringIO()
        with patch("sys.stdin", io.StringIO(sub_req_res)), patch("sys.stdout", out_sub1):
            handle_pre_tool_guardrail_hook()
        res_sub1 = json.loads(out_sub1.getvalue())
        self.assertEqual(res_sub1["decision"], "allow")
        self.assertEqual(res_sub1.get("overwrite"), {"Model": "flash"})

        # Cross-pool protection: When Claude Opus 5.5 is active in main chat, a general subagent with Model='inherit' must overwrite to 'pro'
        sub_req_claude = json.dumps({
            "dbPath": str(temp_central_db),
            "brainDir": str(fake_brain),
            "modelName": "Claude Opus 5.5 (Medium)",
            "toolCall": {
                "name": "invoke_subagent",
                "args": {"TypeName": "self", "Role": "Module Builder", "Prompt": "Implement helper", "Model": "inherit"},
            },
        })
        out_sub2 = io.StringIO()
        with patch("sys.stdin", io.StringIO(sub_req_claude)), patch("sys.stdout", out_sub2):
            handle_pre_tool_guardrail_hook()
        res_sub2 = json.loads(out_sub2.getvalue())
        self.assertEqual(res_sub2["decision"], "allow")
        self.assertEqual(res_sub2.get("overwrite"), {"Model": "pro"})

        # Non-coding subscription tiers ('lite') must normalize to 'pro', and coding subagents (TypeName='self') must never downgrade to Flash
        set_profile(account_tier="lite", quota_mode="golden_ratio", db_path=temp_central_db)
        dec_pro = compute_routing_decision(db_path=temp_central_db, model_name_hint="Gemini 4 Argon (High)", brain_dir=fake_brain)
        self.assertEqual(dec_pro["account_tier"], "pro")
        self.assertEqual(dec_pro["effective_mode"], "golden_ratio")
        self.assertEqual(dec_pro["subagent_research_model"], "flash")
        self.assertEqual(dec_pro["subagent_general_model"], "inherit")
        self.assertEqual(dec_pro["digest_line_threshold"], 120)

        # 4. Verify execute_gemini_digest context-shield execution, fact persistence, and quota_ledger accounting
        sample_log = self.test_dir / "sample_cbs.log"
        sample_log.write_text(
            "\n".join(
                [f"2026-11-03 08:00:{i:02d} Info CBS Normal line {i}" for i in range(60)]
                + ["2026-11-03 08:01:00 Error CBS Failed to process package_123 with HRESULT 0x800f0922"]
            )
            + "\n",
            encoding="utf-8",
        )
        digest_res = execute_gemini_digest(
            files=[str(sample_log)],
            query="Find CBS error and HRESULT code",
            execution_tier="pro",
            preferred_pool="local",
            save_fact_key="cbs_error_digest",
            project_root=self.test_dir,
            db_path=temp_central_db,
        )
        self.assertTrue(digest_res["success"])
        self.assertIn("0x800f0922", digest_res["digest"])
        self.assertIn("L61", digest_res["digest"])

        # 5. Verify AST Contract & Line-Offset Mapper (skillsdb ast-map) and Context-Backpack telemetry
        self.assertIn("backpack", dec_pro)
        self.assertIn(dec_pro["backpack"]["backpack_state"], ("LIGHT", "MODERATE", "HEAVY"))
        ast_res = execute_ast_map(
            files=[str(Path(__file__).resolve().parent.parent / "skillsdb" / "core" / "fim.py")],
            symbol_filter="extract_ast_contract_map",
            save_fact_key="fim_ast_contract",
            project_root=self.test_dir,
            db_path=temp_central_db,
        )
        self.assertTrue(ast_res["success"])
        self.assertIn("def extract_ast_contract_map", ast_res["contract_map"])
        self.assertGreater(ast_res["compression_pct"], 50.0)

        l24 = get_ledger_24h_summary(db_path=temp_central_db)
        self.assertGreaterEqual(l24["subagent_24h_rewrites"], 2)
        self.assertGreaterEqual(l24["ast_map_24h_calls"], 1)
        self.assertGreater(l24["saved_agy_tokens_total"], 0)


if __name__ == "__main__":
    unittest.main()
