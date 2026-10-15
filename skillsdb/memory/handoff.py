"""
Agent Handoff protocol for multi-agent delegation in SkillsDB.
Enforces scoped state transfer, filtered context variables, and zero prompt pollution.
Inspired by the OpenAI Agents SDK & Swarm handoff primitives.
"""

import json
import uuid
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

from .project_memory import (
    find_project_root,
    get_project_db_path,
    get_project_connection,
)
from ..config import DB_PATH


def generate_handoff_id() -> str:
    """Generates a human-readable unique handoff identifier."""
    now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short_uid = uuid.uuid4().hex[:6]
    return f"ho_{now_str}_{short_uid}"


def create_handoff(
    task_id: int,
    target_role: str,
    source_role: str = "lead",
    notes: str = "",
    context_vars: Optional[Dict[str, Any]] = None,
    project_root: Optional[Path] = None
) -> Dict[str, Any]:
    """
    Creates an agent handoff payload for clean context delegation.
    Isolates task state, relevant facts, and targeted skill recommendations
    without hauling bloated transcript history.
    """
    root = project_root or find_project_root()
    conn = get_project_connection(root)
    cursor = conn.cursor()

    # 1. Fetch task
    cursor.execute("SELECT id, title, description, status, priority FROM project_tasks WHERE id = ?;", (task_id,))
    task_row = cursor.fetchone()
    if not task_row:
        conn.close()
        raise ValueError(f"Task ID {task_id} not found on project task board.")

    task_data = dict(task_row)

    # 2. Fetch active facts (excluding deprecated ones)
    cursor.execute("""
    SELECT key, value FROM project_facts
    WHERE deprecated_at IS NULL
    ORDER BY updated_at DESC LIMIT 5;
    """)
    facts = {r["key"]: r["value"] for r in cursor.fetchall()}

    # 3. Fetch latest active architectural decisions
    cursor.execute("""
    SELECT title, content FROM project_decisions
    WHERE is_active = 1
    ORDER BY updated_at DESC LIMIT 3;
    """)
    decisions = [{"title": r["title"], "content": r["content"]} for r in cursor.fetchall()]

    # 4. Filtered context variables
    scoped_vars = context_vars or {}
    scoped_vars.setdefault("task_id", task_id)
    scoped_vars.setdefault("priority", task_data["priority"])

    # 5. Insert handoff record
    handoff_id = generate_handoff_id()
    now_iso = datetime.now(timezone.utc).isoformat()
    vars_json = json.dumps(scoped_vars, ensure_ascii=True)

    cursor.execute("""
    INSERT INTO task_handoffs (id, task_id, source_role, target_role, context_variables, notes, status, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?);
    """, (handoff_id, task_id, source_role, target_role, vars_json, notes, now_iso, now_iso))

    conn.commit()
    conn.close()

    payload = {
        "handoff_id": handoff_id,
        "source_role": source_role,
        "target_role": target_role,
        "task": task_data,
        "notes": notes,
        "context_variables": scoped_vars,
        "active_facts": facts,
        "active_decisions": decisions,
        "status": "pending",
        "created_at": now_iso,
    }

    return payload


def format_handoff_block(payload: Dict[str, Any]) -> str:
    """Formats a handoff payload into a compact, zero-bloat markdown block for subagent prompts."""
    lines = [
        f"=== AGENT HANDOFF: {payload['handoff_id']} ===",
        f"Delegation: [{payload['source_role']}] -> [{payload['target_role']}]",
        f"Active Task: #{payload['task']['id']} [{payload['task']['status'].upper()}] ({payload['task']['priority'].upper()}): {payload['task']['title']}",
    ]
    if payload.get("notes"):
        lines.append(f"Instructions: {payload['notes']}")

    ctx_vars = payload.get("context_variables", {})
    if ctx_vars:
        lines.append("Context Variables:")
        for k, v in ctx_vars.items():
            lines.append(f"  * {k}: {v}")

    facts = payload.get("active_facts", {})
    if facts:
        lines.append("Active Project Facts:")
        for k, v in facts.items():
            lines.append(f"  * {k}: {v}")

    decisions = payload.get("active_decisions", [])
    if decisions:
        lines.append("Architectural Invariants:")
        for d in decisions:
            lines.append(f"  * [{d['title']}]: {d['content']}")

    lines.append("=============================================")
    return "\n".join(lines)


def list_handoffs(project_root: Optional[Path] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Lists recent agent handoffs."""
    root = project_root or find_project_root()
    conn = get_project_connection(root)
    cursor = conn.cursor()

    query = "SELECT id, task_id, source_role, target_role, status, notes, created_at FROM task_handoffs"
    params = []
    if status:
        query += " WHERE status = ?"
        params.append(status.lower())
    query += " ORDER BY created_at DESC LIMIT 20;"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def read_handoff(handoff_id: str, project_root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Retrieves full details of a specific handoff."""
    root = project_root or find_project_root()
    conn = get_project_connection(root)
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM task_handoffs WHERE id = ?;", (handoff_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None

    hdict = dict(row)
    if hdict.get("context_variables"):
        try:
            hdict["context_variables"] = json.loads(hdict["context_variables"])
        except Exception:
            pass

    # Fetch task details
    cursor.execute("SELECT id, title, description, status, priority FROM project_tasks WHERE id = ?;", (hdict["task_id"],))
    trow = cursor.fetchone()
    hdict["task"] = dict(trow) if trow else {}

    conn.close()
    return hdict


def update_handoff(handoff_id: str, status: str, project_root: Optional[Path] = None) -> bool:
    """Updates the lifecycle status of a handoff (pending, accepted, completed, rejected)."""
    status_clean = status.lower()
    if status_clean not in ("pending", "accepted", "completed", "rejected"):
        print(f"Error: Invalid handoff status '{status}'. Allowed: pending, accepted, completed, rejected")
        return False

    root = project_root or find_project_root()
    conn = get_project_connection(root)
    cursor = conn.cursor()
    now_iso = datetime.now(timezone.utc).isoformat()

    cursor.execute("""
    UPDATE task_handoffs
    SET status = ?, updated_at = ?
    WHERE id = ?;
    """, (status_clean, now_iso, handoff_id))

    changed = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return changed
