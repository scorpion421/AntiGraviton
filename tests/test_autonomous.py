import io
import sys
import json
import shutil
import sqlite3
import unittest
import subprocess
from pathlib import Path

# Add database dir to path
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "database"))
import db_manager


class TestSkillsDBAutonomous(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        cls._temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.test_db_dir = Path(cls._temp_dir.name)
        cls.test_db_path = cls.test_db_dir / "test_customizations.db"
        
        # Copy real database for testing
        real_db = REPO_ROOT / "database" / "customizations.db"
        if real_db.exists():
            shutil.copy2(real_db, cls.test_db_path)
            cls.conn = db_manager.get_connection(cls.test_db_path)
        else:
            cls.conn = db_manager.get_connection(cls.test_db_path)
            db_manager.init_db(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        if hasattr(db_manager, "stop_all_writer_queues"):
            db_manager.stop_all_writer_queues()
        cls._temp_dir.cleanup()

    def test_extract_skill_sections(self):
        sample_md = """---
name: test-skill
description: Test skill description
---
# Test Skill Title

## Section One
Content of section one with details.

## Section Two
Content of section two with code:
```python
print("Hello World")
```
"""
        sections = db_manager.extract_skill_sections(sample_md)
        self.assertTrue(len(sections) >= 2)
        titles = [s["title"] for s in sections]
        self.assertIn("Section One", titles)
        self.assertIn("Section Two", titles)

    def test_get_skill_summary(self):
        buf = io.StringIO()
        orig_stdout = sys.stdout
        try:
            sys.stdout = buf
            db_manager.get_skill(self.conn, "flutter-fix-layout-issues", summary=True)
        finally:
            sys.stdout = orig_stdout
        out = buf.getvalue()
        self.assertIn("Micro-Skill Sections", out)
        self.assertIn("flutter-fix-layout-issues", out)

    def test_get_skill_section(self):
        buf = io.StringIO()
        orig_stdout = sys.stdout
        try:
            sys.stdout = buf
            db_manager.get_skill(self.conn, "flutter-fix-layout-issues", section="Constraint Violation Diagnostics")
        finally:
            sys.stdout = orig_stdout
        out = buf.getvalue()
        self.assertIn("Constraint Violation Diagnostics", out)
        self.assertIn("Vertical viewport was given unbounded height", out)

    def test_learn_rule(self):
        test_key = "test-auto-rule"
        test_name = "Automated Learning Test"
        test_content = "# Test Rule\nThis is a learned rule."
        
        db_manager.learn_rule(self.conn, test_key, test_name, test_content, category="testing")
        
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM rules WHERE key = ?", (test_key,))
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["name"], test_name)
        self.assertEqual(row["category"], "testing")

    def test_doctor_execution(self):
        buf = io.StringIO()
        orig_stdout = sys.stdout
        try:
            sys.stdout = buf
            db_manager.doctor(self.conn)
        finally:
            sys.stdout = orig_stdout
        out = buf.getvalue()
        self.assertIn("SKILLSDB SYSTEM DIAGNOSTICS", out)
        self.assertIn("[OK] Central Database", out)

    def test_pre_invocation_hook(self):
        # Create temporary project with memory.db
        test_proj = self.test_db_dir / "test_project"
        test_proj.mkdir(parents=True, exist_ok=True)
        pconn = db_manager.get_project_connection(test_proj)
        db_manager.mem_save_fact(pconn, "framework", "DirectML")
        db_manager.mem_save_decision(pconn, "Scaling", "Native D3D11 VP", category="architecture")
        db_manager.mem_save_snapshot(pconn, "Release v1.0 deployed successfully")
        pconn.close()

        # Run hook logic with mock payload
        mock_payload = json.dumps({
            "invocationNum": 1,
            "workspacePaths": [str(test_proj)]
        })
        
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "database" / "db_manager.py"), "mem-pre-invocation-hook"],
            input=mock_payload,
            capture_output=True,
            text=True
        )
        self.assertEqual(proc.returncode, 0)
        resp = json.loads(proc.stdout.strip())
        self.assertIn("injectSteps", resp)
        self.assertTrue(len(resp["injectSteps"]) > 0)
        msg = resp["injectSteps"][0]["ephemeralMessage"]
        self.assertIn("DirectML", msg)
        self.assertIn("Native D3D11 VP", msg)
        self.assertIn("Release v1.0 deployed", msg)

    def test_differential_merge_preserves_user_data(self):
        # 1. Create a simulated local database with user-learned rules & custom skill
        local_db_path = self.test_db_dir / "sim_local.db"
        lconn = db_manager.get_connection(local_db_path)
        db_manager.init_db(lconn)
        db_manager.add_or_update_rule(lconn, "local-admin", "Local Admin", "security", "Original admin rule")
        db_manager.learn_rule(lconn, "user-custom-rule", "My Secret Rule", "Never expose port 22", category="learned")
        
        # Add custom user skill
        lcur = lconn.cursor()
        lcur.execute("""
        INSERT INTO skills (name, plugin_name, category, is_active, description, content, token_estimate, updated_at)
        VALUES ('user-private-skill', 'custom', 'workflow', 1, 'My custom private skill', 'Content of private skill', 20, '2026-09-22')
        """)
        lconn.commit()
        lconn.close()

        # 2. Create a simulated upstream database with updated official rules and skills
        upstream_db_path = self.test_db_dir / "sim_upstream.db"
        uconn = db_manager.get_connection(upstream_db_path)
        db_manager.init_db(uconn)
        db_manager.add_or_update_rule(uconn, "local-admin", "Local Admin", "security", "UPSTREAM UPDATED admin rule v2.1")
        db_manager.add_or_update_rule(uconn, "new-official-rule", "New Official Rule", "general", "Brand new official rule")
        
        ucur = uconn.cursor()
        ucur.execute("""
        INSERT INTO skills (name, plugin_name, category, is_active, description, content, token_estimate, updated_at)
        VALUES ('official-new-skill', 'core', 'workflow', 1, 'New official skill', 'Content of new official skill', 30, '2026-09-22')
        """)
        uconn.commit()
        uconn.close()

        # 3. Execute non-destructive differential merge
        merge_result = db_manager.merge_upstream_database(local_db_path, upstream_db_path)

        # 4. Verify results
        vconn = db_manager.get_connection(local_db_path)
        vcur = vconn.cursor()

        # User learned rule must be 100% preserved
        vcur.execute("SELECT * FROM rules WHERE key = 'user-custom-rule'")
        learned_rule = vcur.fetchone()
        self.assertIsNotNone(learned_rule)
        self.assertEqual(learned_rule["content"], "Never expose port 22")
        self.assertEqual(learned_rule["category"], "learned")

        # Custom private skill must be 100% preserved
        vcur.execute("SELECT * FROM skills WHERE name = 'user-private-skill'")
        priv_skill = vcur.fetchone()
        self.assertIsNotNone(priv_skill)
        self.assertEqual(priv_skill["content"], "Content of private skill")

        # Official rule must be updated from upstream
        vcur.execute("SELECT * FROM rules WHERE key = 'local-admin'")
        admin_rule = vcur.fetchone()
        self.assertIsNotNone(admin_rule)
        self.assertEqual(admin_rule["content"], "UPSTREAM UPDATED admin rule v2.1")

        # New official rule and skill must be added
        vcur.execute("SELECT * FROM rules WHERE key = 'new-official-rule'")
        self.assertIsNotNone(vcur.fetchone())
        vcur.execute("SELECT * FROM skills WHERE name = 'official-new-skill'")
        self.assertIsNotNone(vcur.fetchone())

        vconn.close()
        self.assertEqual(merge_result["preserved_learned_rules"], 1)

    def test_check_update_functionality(self):
        # Verify check_update handles versions gracefully
        res = db_manager.check_update(quiet=True)
        if res:
            self.assertIn("has_update", res)
            self.assertIn("current", res)
            self.assertIn("latest", res)

    def test_fix_utf8_functionality(self):
        # Test check_windows_utf8 and fix_utf8
        ok, msg = db_manager.check_windows_utf8()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(msg, str)
        # Should succeed in test environment
        res = subprocess.run([sys.executable, str(REPO_ROOT / "database" / "db_manager.py"), "fix-utf8", "--check"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("SKILLSDB UTF-8 ENCODING FIX", res.stdout)


if __name__ == "__main__":
    unittest.main()


