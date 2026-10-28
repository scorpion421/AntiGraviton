"""
Reusable Agent Templates catalog and instantiation engine.
Inspired by Mistral AI's Agents API (reusable agent definitions by agent_id).
Enables persistent agent roles, instructions, and skill bindings without prompt reconstruction.
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from pathlib import Path


DEFAULT_TEMPLATES = [
    {
        "id": "lead_architect",
        "name": "Lead Solutions Architect",
        "role": "Architectural Design, High-Level Planning & System Decisions",
        "system_prompt": "You are a Lead Solutions Architect. Your role is to design resilient systems, evaluate architectural trade-offs, and ensure strict adherence to project standards and formatting invariants.",
        "skills": ["customizations-db", "antigravity-guide"],
        "tools": ["skillsdb_rules", "skillsdb_suggest", "skillsdb_context"],
    },
    {
        "id": "security_auditor",
        "name": "Security & Guardrails Auditor",
        "role": "Vulnerability Assessment, Credential Leak Detection & Policy Verification",
        "system_prompt": "You are a Security Auditor. Inspect all file edits and git staged content for credential leaks, secret exposures, and strict compliance with coding and safety invariants.",
        "skills": ["admin-elevation", "credentials", "customizations-db"],
        "tools": ["skillsdb_guardrail", "skillsdb_rules"],
    },
    {
        "id": "test_runner",
        "name": "Quality Assurance & Verifier",
        "role": "Evaluator-Optimizer, Automated Testing & Test-Driven Verification",
        "system_prompt": "You are a QA Verification Specialist. Execute test assertion suites, verify regression coverage, and confirm task completion only when all gates pass.",
        "skills": ["customizations-db"],
        "tools": ["skillsdb_task_board", "skillsdb_guardrail"],
    },
    {
        "id": "flutter_expert",
        "name": "Mobile & Flutter Engineer",
        "role": "Declarative Routing, Responsive Layouts & Cross-Platform UI",
        "system_prompt": "You are an expert Flutter & Mobile Engineer. Follow strict declarative routing, responsive layout patterns, and widget testing best practices.",
        "skills": ["flutter-apply-architecture-best-practices", "flutter-add-widget-test", "flutter-build-responsive-layout"],
        "tools": ["skillsdb_get_skill", "skillsdb_fim_slice"],
    }
]


def _safe_parse_json_list(val) -> List[str]:
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def init_agent_templates(conn: sqlite3.Connection):
    """Initializes the agent_templates table and seeds default built-in templates."""
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'agent_templates';")
    if not cursor.fetchone():
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS agent_templates (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            system_prompt TEXT NOT NULL,
            skills TEXT DEFAULT '[]',
            tools TEXT DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """)

    now_iso = datetime.now(timezone.utc).isoformat()
    changed = False
    for tmpl in DEFAULT_TEMPLATES:
        cursor.execute("SELECT id FROM agent_templates WHERE id = ?;", (tmpl["id"],))
        if not cursor.fetchone():
            skills_json = json.dumps(tmpl["skills"], ensure_ascii=True)
            tools_json = json.dumps(tmpl["tools"], ensure_ascii=True)
            cursor.execute("""
            INSERT INTO agent_templates (id, name, role, system_prompt, skills, tools, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """, (tmpl["id"], tmpl["name"], tmpl["role"], tmpl["system_prompt"], skills_json, tools_json, now_iso, now_iso))
            changed = True

    if changed or conn.in_transaction:
        conn.commit()


def list_agent_templates(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Lists all registered agent templates."""
    init_agent_templates(conn)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, role, system_prompt, skills, tools, updated_at FROM agent_templates ORDER BY id ASC;")
    rows = cursor.fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["skills"] = _safe_parse_json_list(d.get("skills"))
        d["tools"] = _safe_parse_json_list(d.get("tools"))
        results.append(d)
    return results


def get_agent_template(conn: sqlite3.Connection, agent_id: str) -> Optional[Dict[str, Any]]:
    """Retrieves a specific agent template by its unique identifier."""
    init_agent_templates(conn)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, role, system_prompt, skills, tools, updated_at FROM agent_templates WHERE id = ?;", (agent_id,))
    row = cursor.fetchone()
    if not row:
        return None
    d = dict(row)
    d["skills"] = _safe_parse_json_list(d.get("skills"))
    d["tools"] = _safe_parse_json_list(d.get("tools"))
    return d


def register_agent_template(
    conn: sqlite3.Connection,
    agent_id: str,
    name: str,
    role: str,
    system_prompt: str,
    skills: Optional[List[str]] = None,
    tools: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Registers or updates a custom agent template."""
    init_agent_templates(conn)
    cursor = conn.cursor()
    now_iso = datetime.now(timezone.utc).isoformat()
    skills_json = json.dumps(skills or [], ensure_ascii=True)
    tools_json = json.dumps(tools or [], ensure_ascii=True)

    cursor.execute("""
    INSERT INTO agent_templates (id, name, role, system_prompt, skills, tools, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        name = excluded.name,
        role = excluded.role,
        system_prompt = excluded.system_prompt,
        skills = excluded.skills,
        tools = excluded.tools,
        updated_at = excluded.updated_at;
    """, (agent_id, name, role, system_prompt, skills_json, tools_json, now_iso, now_iso))
    conn.commit()

    return {
        "id": agent_id,
        "name": name,
        "role": role,
        "system_prompt": system_prompt,
        "skills": skills or [],
        "tools": tools or [],
        "updated_at": now_iso,
    }


def format_agent_prompt(template: Dict[str, Any]) -> str:
    """Formats an agent template into a structured subagent system prompt."""
    skills_list = ", ".join(template.get("skills", [])) or "None"
    tools_list = ", ".join(template.get("tools", [])) or "Standard"
    lines = [
        f"=== AGENT TEMPLATE: [{template['id']}] {template['name']} ===",
        f"Role: {template['role']}",
        f"Pre-Bound Skills: {skills_list}",
        f"Permitted Tools: {tools_list}",
        "--- Instructions ---",
        template["system_prompt"].strip(),
        "============================================================",
    ]
    return "\n".join(lines)
