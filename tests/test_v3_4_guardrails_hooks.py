"""
Unit tests for SkillsDB Turn-1 Invariant Guardrails & PreToolUse Gatekeeper:
- PreInvocation Turn-1 Guardrail Bulletin injection (Language separation, no em-dashes, no emojis)
- PreToolUse Gatekeeper blocking forbidden characters (em-dash, en-dash, emojis) in write_to_file/replace_file_content
"""

import io
import sys
import json
import unittest
import tempfile
from pathlib import Path

# Add project root to path
REPO_ROOT = Path(__file__).parent.parent.resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from skillsdb.core.guardrails import handle_pre_tool_guardrail_hook
from skillsdb.memory.project_memory import handle_pre_invocation_hook


class TestGuardrailsAndHooks(unittest.TestCase):
    """Verifies Turn-1 invariant injection and PreToolUse gatekeeper behavior."""

    def test_pre_invocation_hook_turn_1(self):
        """PreInvocation hook on Turn 1 must always inject the guardrail bulletin."""
        payload = {
            "invocationNum": 1,
            "workspacePaths": [tempfile.gettempdir()],
        }
        old_stdin = sys.stdin
        old_stdout = sys.stdout
        try:
            sys.stdin = io.StringIO(json.dumps(payload))
            captured_stdout = io.StringIO()
            sys.stdout = captured_stdout

            handle_pre_invocation_hook()

            output = json.loads(captured_stdout.getvalue())
            self.assertIn("injectSteps", output)
            self.assertTrue(len(output["injectSteps"]) > 0)
            msg = output["injectSteps"][0]["ephemeralMessage"]
            self.assertIn("SKILLSDB CRITICAL GUARDRAIL & INVARIANTS", msg)
            self.assertIn("STRICT LANGUAGE SEPARATION", msg)
            self.assertIn("NEVER translate UI dialogs", msg)
        finally:
            sys.stdin = old_stdin
            sys.stdout = old_stdout

    def test_pre_tool_guardrail_hook_allow_clean_code(self):
        """PreToolUse hook must allow clean English code with standard ASCII hyphens."""
        payload = {
            "toolCall": {
                "name": "write_to_file",
                "args": {
                    "TargetFile": "C:/test/file.js",
                    "CodeContent": "const buttonText = 'Preview Active'; // clean code",
                }
            }
        }
        old_stdin = sys.stdin
        old_stdout = sys.stdout
        try:
            sys.stdin = io.StringIO(json.dumps(payload))
            captured_stdout = io.StringIO()
            sys.stdout = captured_stdout

            handle_pre_tool_guardrail_hook()

            res = json.loads(captured_stdout.getvalue())
            self.assertEqual(res.get("decision"), "allow")
        finally:
            sys.stdin = old_stdin
            sys.stdout = old_stdout

    def test_pre_tool_guardrail_hook_deny_em_dash(self):
        """PreToolUse hook must deny code containing Unicode em-dashes (U+2014)."""
        payload = {
            "toolCall": {
                "name": "replace_file_content",
                "args": {
                    "TargetFile": "C:/test/file.py",
                    "ReplacementContent": "title = 'Editor \u2014 Pro Version'",
                }
            }
        }
        old_stdin = sys.stdin
        old_stdout = sys.stdout
        try:
            sys.stdin = io.StringIO(json.dumps(payload))
            captured_stdout = io.StringIO()
            sys.stdout = captured_stdout

            handle_pre_tool_guardrail_hook()

            res = json.loads(captured_stdout.getvalue())
            self.assertEqual(res.get("decision"), "deny")
            self.assertIn("Em-dash", res.get("reason", ""))
        finally:
            sys.stdin = old_stdin
            sys.stdout = old_stdout

    def test_pre_tool_guardrail_hook_deny_emoji(self):
        """PreToolUse hook must deny code containing Unicode emojis."""
        payload = {
            "toolCall": {
                "name": "write_to_file",
                "args": {
                    "TargetFile": "C:/test/file.ts",
                    "CodeContent": "console.log('Done \U0001F680');",
                }
            }
        }
        old_stdin = sys.stdin
        old_stdout = sys.stdout
        try:
            sys.stdin = io.StringIO(json.dumps(payload))
            captured_stdout = io.StringIO()
            sys.stdout = captured_stdout

            handle_pre_tool_guardrail_hook()

            res = json.loads(captured_stdout.getvalue())
            self.assertEqual(res.get("decision"), "deny")
            self.assertIn("emoji", res.get("reason", "").lower())
        finally:
            sys.stdin = old_stdin
            sys.stdout = old_stdout


if __name__ == "__main__":
    unittest.main()
