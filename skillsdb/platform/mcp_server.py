"""
Native Model Context Protocol (MCP) JSON-RPC 2.0 Server for SkillsDB.
Inspired by Mistral AI's native MCP managed connectors.
Enables any MCP-compatible agent or client (Google Antigravity, Cursor, Continue.dev, Claude Desktop, VS Code)
to connect directly to SkillsDB's knowledge engine, guardrails, FIM slicing, and memory over stdio.
"""

import sys
import json
import sqlite3
from pathlib import Path
from typing import Dict, Any, List

from ..config import DB_PATH, __version__
from ..core.db import get_connection, init_db
from ..core.guardrails import check_text, fix_text
from ..core.fim import slice_fim, slice_fim_symbol, format_fim_block
from ..search.fts import suggest_skills, get_skill, get_active_rules
from ..memory.agent_templates import list_agent_templates, get_agent_template, format_agent_prompt
from ..memory.gitea_sync import gitea_status_summary, save_code_commit


SERVER_NAME = "skillsdb-mcp-server"

TOOLS_REGISTRY = [
    {
        "name": "skillsdb_get_rules",
        "description": "Retrieve active system rules and global formatting invariants from central customizations database.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        }
    },
    {
        "name": "skillsdb_suggest_skills",
        "description": "Auto-recommend relevant domain micro-skills for any software engineering task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Description of the task to perform"},
                "cwd": {"type": "string", "description": "Optional working directory path for directory-scoped recommendations"}
            },
            "required": ["task"]
        }
    },
    {
        "name": "skillsdb_get_skill",
        "description": "Fetch a specific skill or micro-skill section to guide agent execution.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the skill to fetch"},
                "section": {"type": "string", "description": "Optional section name to retrieve only a micro-skill (~150-300 tokens)"},
                "summary": {"type": "boolean", "description": "If true, returns only the skill outline and section list"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "skillsdb_guardrail_check",
        "description": "Inspect text or code for safety invariants (no em/en-dashes, no emojis, no secret leaks, clean German compounds).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Content string to inspect"},
                "auto_fix": {"type": "boolean", "description": "If true, automatically remediates formatting violations"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "skillsdb_fim_slice",
        "description": "Surgically slice a code file around a target line or symbol using Codestral Fill-in-the-Middle (FIM) to slash prompt context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the code file"},
                "line": {"type": "integer", "description": "Target line number to slice around"},
                "symbol": {"type": "string", "description": "Symbol name (function, class, variable) to slice around"}
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "skillsdb_agent_template",
        "description": "Fetch a reusable agent template by agent_id (e.g. lead_architect, security_auditor, test_runner, flutter_expert).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Unique agent template ID"},
                "list_all": {"type": "boolean", "description": "If true, lists all available agent templates"}
            }
        }
    },
    {
        "name": "skillsdb_gitea_status",
        "description": "Check local Gitea service status, API health, and tracked workspace repositories.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        }
    },
    {
        "name": "skillsdb_gitea_sync",
        "description": "Stage, commit, and push workspace changes to local Gitea server.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_path": {"type": "string", "description": "Workspace root directory path"},
                "message": {"type": "string", "description": "Commit message describing the milestone"}
            },
            "required": ["project_path", "message"]
        }
    }
]


def handle_tool_call(name: str, args: Dict[str, Any]) -> str:
    """Executes an MCP tool call and returns text content."""
    conn = get_connection(DB_PATH)
    init_db(conn)

    try:
        if name == "skillsdb_get_rules":
            cursor = conn.cursor()
            cursor.execute("SELECT key, name, content FROM rules WHERE is_active = 1 ORDER BY key;")
            rows = cursor.fetchall()
            lines = ["# Active Global Rules from Central Database:"]
            for r in rows:
                lines.append(f"\n## Rule: {r['name']} ({r['key']})\n{r['content']}")
            return "\n".join(lines)

        elif name == "skillsdb_suggest_skills":
            task = args.get("task", "")
            cwd_str = args.get("cwd")
            cwd_p = Path(cwd_str) if cwd_str else None
            # Capture output in memory
            import io
            from contextlib import redirect_stdout
            f = io.StringIO()
            with redirect_stdout(f):
                suggest_skills(conn, task, cwd_path=cwd_p)
            return f.getvalue()

        elif name == "skillsdb_get_skill":
            sname = args.get("name", "")
            sec = args.get("section")
            summ = args.get("summary", False)
            import io
            from contextlib import redirect_stdout
            f = io.StringIO()
            with redirect_stdout(f):
                get_skill(conn, sname, summary=summ, section=sec)
            return f.getvalue()

        elif name == "skillsdb_guardrail_check":
            text = args.get("text", "")
            auto_fix = args.get("auto_fix", False)
            violations = check_text(text)
            if auto_fix:
                cleaned, count = fix_text(text)
                return json.dumps({
                    "passed": len(violations) == 0,
                    "violations": violations,
                    "auto_fixed": True,
                    "fix_count": count,
                    "cleaned_text": cleaned
                }, indent=2)
            return json.dumps({
                "passed": len(violations) == 0,
                "violations_count": len(violations),
                "violations": violations
            }, indent=2)

        elif name == "skillsdb_fim_slice":
            fpath = Path(args.get("file_path", ""))
            line_num = args.get("line")
            sym = args.get("symbol")
            if sym:
                res = slice_fim_symbol(fpath, sym)
            elif line_num:
                res = slice_fim(fpath, line_num)
            else:
                return "Error: Specify either 'line' or 'symbol' to slice."
            return format_fim_block(res)

        elif name == "skillsdb_agent_template":
            aid = args.get("agent_id")
            if args.get("list_all") or not aid:
                tmpls = list_agent_templates(conn)
                lines = ["=== AVAILABLE AGENT TEMPLATES ==="]
                for t in tmpls:
                    lines.append(f"  * [{t['id']}] {t['name']}: {t['role']}")
                return "\n".join(lines)
            tmpl = get_agent_template(conn, aid)
            if not tmpl:
                return f"Error: Agent template '{aid}' not found."
            return format_agent_prompt(tmpl)

        elif name == "skillsdb_gitea_status":
            import io
            from contextlib import redirect_stdout
            f = io.StringIO()
            with redirect_stdout(f):
                gitea_status_summary()
            return f.getvalue()

        elif name == "skillsdb_gitea_sync":
            proj_path = Path(args.get("project_path", "."))
            msg = args.get("message", "Synchronize workspace via MCP")
            res = save_code_commit(proj_path, message=msg, push=True)
            return json.dumps(res, indent=2)

        return f"Unknown tool: {name}"
    finally:
        conn.close()


def run_mcp_server():
    """Main stdio JSON-RPC 2.0 loop for Model Context Protocol."""
    sys.stderr.write(f"[{SERVER_NAME}] Starting SkillsDB native MCP server (v{__version__})...\n")
    sys.stderr.flush()

    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue

            req = json.loads(line)
            req_id = req.get("id")
            method = req.get("method")
            params = req.get("params", {})

            # Notification handling (no response)
            if req_id is None:
                continue

            if method == "initialize":
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {
                            "tools": {}
                        },
                        "serverInfo": {
                            "name": SERVER_NAME,
                            "version": __version__
                        }
                    }
                }
            elif method == "ping":
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {}
                }
            elif method == "tools/list":
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "tools": TOOLS_REGISTRY
                    }
                }
            elif method == "tools/call":
                tool_name = params.get("name", "")
                tool_args = params.get("arguments", {})
                result_text = handle_tool_call(tool_name, tool_args)
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": result_text
                            }
                        ]
                    }
                }
            else:
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {method}"
                    }
                }

            out_line = json.dumps(resp) + "\n"
            sys.stdout.write(out_line)
            sys.stdout.flush()

        except Exception as e:
            sys.stderr.write(f"[{SERVER_NAME}] Error: {e}\n")
            sys.stderr.flush()
