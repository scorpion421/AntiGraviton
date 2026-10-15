"""
Unit tests for SkillsDB v3.3 Mistral AI Primitives:
- Fill-in-the-Middle (FIM) surgical code slicing & context reduction (Codestral pattern)
- Reusable Agent Templates catalog by agent_id (Mistral Agents API pattern)
- Native Model Context Protocol (MCP) tool execution
- Local Sovereignty & Offline Profile Management
"""

import sys
import json
import unittest
import tempfile
import sqlite3
from pathlib import Path

# Add project root to sys.path
repo_root = Path(__file__).parent.parent.resolve()
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from skillsdb.core.fim import (
    slice_fim,
    slice_fim_symbol,
    format_fim_block,
)
from skillsdb.memory.agent_templates import (
    init_agent_templates,
    list_agent_templates,
    get_agent_template,
    register_agent_template,
    format_agent_prompt,
)
from skillsdb.platform.mcp_server import handle_tool_call, TOOLS_REGISTRY
from skillsdb.core.detector import set_profile, detect_model_tier
from skillsdb.config import TIER_LOCAL, TIER_OFFLINE


class TestV33MistralFusion(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.proj_dir = Path(self.temp_dir.name)
        self.db_path = self.proj_dir / "customizations_test.db"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        init_agent_templates(self.conn)

    def tearDown(self):
        self.conn.close()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_fim_line_slicing_and_compression(self):
        """Verifies Codestral FIM minimal window extraction and token preservation."""
        sample_code = "\n".join([f"line_{i} = {i} * 2" for i in range(1, 101)])
        test_file = self.proj_dir / "sample_service.py"
        test_file.write_text(sample_code, encoding="utf-8")

        res = slice_fim(test_file, target_line=50, prefix_lines=10, suffix_lines=10)

        self.assertEqual(res["target_line"], 50)
        self.assertEqual(res["total_file_lines"], 100)
        self.assertIn("line_50", res["target_line_content"])
        self.assertIn("line_40", res["prefix"])
        self.assertIn("line_60", res["suffix"])
        self.assertGreater(res["tokens_saved"], 0)

        # Verify formatted Codestral block
        block = format_fim_block(res)
        self.assertIn("<fim_prefix>", block)
        self.assertIn("<fim_suffix>", block)
        self.assertIn("<fim_middle>", block)

    def test_fim_symbol_slicing(self):
        """Tests slicing code around a function or class definition."""
        code = (
            "import os\n\n"
            "def calculate_total(a, b):\n"
            "    return a + b\n\n"
            "def execute_pipeline(items):\n"
            "    # Core processing logic\n"
            "    total = sum(items)\n"
            "    return total * 1.2\n\n"
            "def finalize():\n"
            "    pass\n"
        )
        test_file = self.proj_dir / "pipeline.py"
        test_file.write_text(code, encoding="utf-8")

        res = slice_fim_symbol(test_file, "execute_pipeline", prefix_lines=3, suffix_lines=3)
        self.assertEqual(res["symbol"], "execute_pipeline")
        self.assertEqual(res["target_line"], 6)
        self.assertIn("def execute_pipeline", res["target_line_content"])

    def test_agent_templates_catalog(self):
        """Tests registration, retrieval, and formatting of reusable agent templates."""
        # 1. Built-in templates
        tmpls = list_agent_templates(self.conn)
        self.assertGreaterEqual(len(tmpls), 4)

        t_ids = {t["id"] for t in tmpls}
        self.assertIn("lead_architect", t_ids)
        self.assertIn("security_auditor", t_ids)
        self.assertIn("test_runner", t_ids)
        self.assertIn("flutter_expert", t_ids)

        # 2. Get specific template
        sec_tmpl = get_agent_template(self.conn, "security_auditor")
        self.assertIsNotNone(sec_tmpl)
        self.assertEqual(sec_tmpl["name"], "Security & Guardrails Auditor")

        prompt_block = format_agent_prompt(sec_tmpl)
        self.assertIn("=== AGENT TEMPLATE: [security_auditor]", prompt_block)
        self.assertIn("Security Auditor", prompt_block)

        # 3. Register custom template
        custom = register_agent_template(
            self.conn,
            agent_id="db_tuner",
            name="Database Performance Tuner",
            role="SQLite WAL & FTS Index Optimization",
            system_prompt="Optimize indexing and vacuuming routines.",
            skills=["customizations-db"],
            tools=["skillsdb_rules"]
        )
        self.assertEqual(custom["id"], "db_tuner")

        fetched = get_agent_template(self.conn, "db_tuner")
        self.assertEqual(fetched["name"], "Database Performance Tuner")

    def test_mcp_server_tool_registry_and_call(self):
        """Verifies native MCP Server tool definitions and execution."""
        tool_names = [t["name"] for t in TOOLS_REGISTRY]
        self.assertIn("skillsdb_get_rules", tool_names)
        self.assertIn("skillsdb_guardrail_check", tool_names)
        self.assertIn("skillsdb_fim_slice", tool_names)
        self.assertIn("skillsdb_agent_template", tool_names)

        # Test guardrail MCP execution
        res_json = handle_tool_call("skillsdb_guardrail_check", {"text": "Clean ASCII line.", "auto_fix": False})
        parsed = json.loads(res_json)
        self.assertTrue(parsed["passed"])

        # Test agent template listing via MCP
        res_tmpls = handle_tool_call("skillsdb_agent_template", {"list_all": True})
        self.assertIn("AVAILABLE AGENT TEMPLATES", res_tmpls)

    def test_local_sovereignty_profile(self):
        """Tests setting local / offline sovereignty tiers."""
        set_profile("local", db_path=self.db_path)
        tier, desc = detect_model_tier(db_path=self.db_path)
        self.assertEqual(tier, TIER_LOCAL)

        set_profile("offline", db_path=self.db_path)
        tier2, desc2 = detect_model_tier(db_path=self.db_path)
        self.assertEqual(tier2, TIER_OFFLINE)

        # Reset to auto
        set_profile("auto", db_path=self.db_path)


if __name__ == "__main__":
    unittest.main()
