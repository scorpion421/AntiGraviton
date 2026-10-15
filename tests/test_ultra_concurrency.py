import io
import os
import sys
import json
import shutil
import sqlite3
import unittest
from pathlib import Path

# Add database dir to path
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "database"))
import db_manager


class TestUltraConcurrency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_db_dir = REPO_ROOT / "tests" / "scratch_concurrency"
        cls.test_db_dir.mkdir(parents=True, exist_ok=True)
        cls.test_db_path = cls.test_db_dir / "test_concurrency.db"
        if cls.test_db_path.exists():
            cls.test_db_path.unlink()

        # Copy real database for testing if available
        real_db = REPO_ROOT / "database" / "customizations.db"
        if not real_db.exists():
            real_db = Path.home() / ".gemini" / "database" / "customizations.db"

        if real_db.exists():
            shutil.copy2(real_db, cls.test_db_path)
            cls.conn = db_manager.get_connection(cls.test_db_path)
        else:
            cls.conn = db_manager.get_connection(cls.test_db_path)
            db_manager.init_db(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.test_db_dir, ignore_errors=True)

    def test_thread_safe_connection(self):
        t_conn = db_manager.get_thread_connection(self.test_db_path)
        cursor = t_conn.cursor()
        cursor.execute("PRAGMA journal_mode;")
        mode = cursor.fetchone()[0]
        self.assertEqual(mode.lower(), "wal")
        cursor.execute("PRAGMA busy_timeout;")
        timeout = cursor.fetchone()[0]
        self.assertEqual(timeout, 5000)
        t_conn.close()

    def test_model_tier_env_override(self):
        old_env = os.environ.get("SKILLSDB_MODEL_TIER")
        try:
            os.environ["SKILLSDB_MODEL_TIER"] = "ultra"
            tier, desc = db_manager.detect_model_tier()
            self.assertEqual(tier, db_manager.TIER_ULTRA)
            self.assertIn("Environment variable", desc)

            os.environ["SKILLSDB_MODEL_TIER"] = "lean"
            tier, desc = db_manager.detect_model_tier()
            self.assertEqual(tier, db_manager.TIER_LEAN)
        finally:
            if old_env is not None:
                os.environ["SKILLSDB_MODEL_TIER"] = old_env
            else:
                os.environ.pop("SKILLSDB_MODEL_TIER", None)

    def test_model_tier_runtime_config(self):
        # Override DB_PATH temporarily for isolated runtime_config test
        old_db_path = db_manager.DB_PATH
        db_manager.DB_PATH = self.test_db_path
        old_env = os.environ.pop("SKILLSDB_MODEL_TIER", None)
        try:
            db_manager.set_profile("ultra")
            tier, desc = db_manager.detect_model_tier()
            self.assertEqual(tier, db_manager.TIER_ULTRA)
            self.assertIn("runtime_config", desc)

            db_manager.set_profile("auto")
            tier, desc = db_manager.detect_model_tier()
            self.assertIn(tier, [db_manager.TIER_LEAN, db_manager.TIER_STANDARD, db_manager.TIER_ULTRA])
        finally:
            db_manager.DB_PATH = old_db_path
            if old_env is not None:
                os.environ["SKILLSDB_MODEL_TIER"] = old_env

    def test_fetch_single_skill(self):
        res = db_manager.fetch_single_skill("customizations-db", db_path=self.test_db_path, summary=True)
        self.assertTrue(res["found"])
        self.assertEqual(res["name"], "customizations-db")
        self.assertIn("Skill Summary", res["content"])
        self.assertGreater(res["token_estimate"], 0)

        missing = db_manager.fetch_single_skill("nonexistent-skill-xyz", db_path=self.test_db_path)
        self.assertFalse(missing["found"])
        self.assertIsNone(missing["content"])

    def test_get_skills_parallel(self):
        old_db_path = db_manager.DB_PATH
        db_manager.DB_PATH = self.test_db_path
        try:
            skills_to_test = ["customizations-db", "antigravity-guide", "nonexistent-xyz"]
            results = db_manager.get_skills_parallel(skills_to_test, max_workers=4, as_json=False, summary=True)
            self.assertEqual(len(results), 3)
            found_names = [r["name"] for r in results if r["found"]]
            self.assertIn("customizations-db", found_names)
            missing_names = [r["name"] for r in results if not r["found"]]
            self.assertIn("nonexistent-xyz", missing_names)
        finally:
            db_manager.DB_PATH = old_db_path

    def test_search_multi_parallel(self):
        old_db_path = db_manager.DB_PATH
        db_manager.DB_PATH = self.test_db_path
        try:
            queries = ["rules standards", "database skills", "nonexistenttermxyz999"]
            results = db_manager.search_multi_parallel(queries, max_workers=4, limit_per_query=3, as_json=False)
            self.assertEqual(len(results), 3)
            self.assertIn("rules standards", results)
            self.assertIn("database skills", results)
            self.assertIn("nonexistenttermxyz999", results)
            self.assertEqual(len(results["nonexistenttermxyz999"]), 0)
        finally:
            db_manager.DB_PATH = old_db_path

    def test_skill_clusters_integrity(self):
        for cluster_name, skills in db_manager.SKILL_CLUSTERS.items():
            self.assertIsInstance(skills, list)
            self.assertGreater(len(skills), 0)

    def test_pre_invocation_hook_ultra_injection(self):
        old_env = os.environ.get("SKILLSDB_MODEL_TIER")
        os.environ["SKILLSDB_MODEL_TIER"] = "ultra"
        old_stdin = sys.stdin
        old_stdout = sys.stdout
        try:
            sys.stdin = io.StringIO(json.dumps({"invocationNum": 1, "workspacePaths": [str(REPO_ROOT)]}))
            sys.stdout = io.StringIO()
            db_manager.handle_pre_invocation_hook()
            output = json.loads(sys.stdout.getvalue().strip())
            self.assertIn("injectSteps", output)
            self.assertGreater(len(output["injectSteps"]), 0)
            ephemeral = output["injectSteps"][0]["ephemeralMessage"]
            self.assertIn("GEMINI ULTRA", ephemeral)
            self.assertIn("get-skills", ephemeral)
        finally:
            sys.stdin = old_stdin
            sys.stdout = old_stdout
            if old_env is not None:
                os.environ["SKILLSDB_MODEL_TIER"] = old_env
            else:
                os.environ.pop("SKILLSDB_MODEL_TIER", None)


if __name__ == "__main__":
    unittest.main()
