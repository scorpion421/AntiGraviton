"""
Project-level episodic memory (.agents/memory.db), lifecycle hook handler, and statistics.
"""

import os
import re
import sys
import json
import sqlite3
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from ..config import TIER_ULTRA, DB_PATH, GEMINI_DIR, resolve_user_home, __version__
from ..core.db import get_connection, managed_connection
from ..core.detector import detect_model_tier, detect_model_and_pool
from ..core.quota_router import compute_routing_decision, format_hook_routing_banner, get_ledger_24h_summary
from .writer_queue import get_writer_queue, flush_journals
from ..platform.windows_utf8 import ensure_windows_utf8_manifests
from .gitea_config import (
    _infer_workspace_last_active_iso,
    get_learned_workspaces,
    get_learned_workspaces_with_timestamps,
)
from .gitea_sync import _is_temp_workspace, _is_excluded_workspace_path, is_gitea_configured, save_code_commit, get_workspace_git_telemetry, learn_workspace, flush_all_pending_pushes, setup_workspace_git
from ..search.fts import detect_cwd_hints, slice_skill
from ..search.synonyms import expand_query_with_synonyms, build_safe_fts_query

_DEFAULT_HOOK_STATE_FILE = Path(tempfile.gettempdir()) / "skillsdb_hook_state.json"
_HOOK_STATE_FILE = _DEFAULT_HOOK_STATE_FILE
_VERIFIED_PROJECT_DBS: set[str] = set()


def _resolve_db_file(conn: sqlite3.Connection, db_path: Path = None) -> Path | None:
    """Resolves the filesystem Path for a SQLite connection, returning None for :memory: databases."""
    if db_path:
        return Path(db_path)
    if not conn:
        return None
    try:
        row = conn.execute("PRAGMA database_list;").fetchone()
        if row and row[2]:
            return Path(row[2])
    except sqlite3.Error:
        pass
    return None
_REQUIRED_PROJECT_TABLES = frozenset({
    "project_decisions",
    "session_snapshots",
    "project_facts",
    "fact_history",
    "project_tasks",
    "task_verifications",
    "task_handoffs",
    "symbol_index",
    "compaction_archive",
    "active_working_set",
    "dependency_edges",
    "project_memory_fts",
})


def _is_protected_non_workspace(path: Path) -> bool:
    """
    Returns True if path is a system/user profile/container/application directory that must never
    be treated as a project workspace (e.g., user home, ~/.gemini, scratch container root,
    root OS temp dir, or %LOCALAPPDATA%/Programs/antigravity).
    """
    return _is_excluded_workspace_path(path, allow_temp_subdirs=True)


def find_project_root(start_path: Path = None) -> Path:
    """Traverses upward to identify project root boundary without crossing user home or system directories."""
    current = Path(os.path.abspath(str(start_path or Path.cwd())))
    if _is_protected_non_workspace(current):
        return current
    project_markers = [
        ".agents", ".git", ".antigravity",
        "package.json", "pyproject.toml", "requirements.txt",
        "pubspec.yaml", "go.mod", "pom.xml", "build.gradle"
    ]
    for parent in [current] + list(current.parents):
        if _is_protected_non_workspace(parent):
            break
        if any((parent / m).exists() for m in project_markers):
            return parent
    return current


def get_project_db_path(project_root: Path = None) -> Path:
    root = project_root or find_project_root()
    if project_root is None and _is_protected_non_workspace(root):
        cid_env = os.environ.get("ANTIGRAVITY_CONVERSATION_ID", "").strip()
        if cid_env and cid_env != "default":
            conv_dir = resolve_user_home() / ".gemini" / "antigravity" / "brain" / cid_env
            if conv_dir.exists():
                return conv_dir / ".agents" / "memory.db"
    return root / ".agents" / "memory.db"


def get_project_connection(project_root: Path = None) -> sqlite3.Connection:
    """Returns a WAL-enabled, memory-mapped SQLite connection to the project memory database."""
    db_path = get_project_db_path(project_root)
    conn = get_connection(db_path, is_project_db=True)
    init_project_db(conn)
    return conn


def _project_schema_ready(conn: sqlite3.Connection) -> bool:
    """Fast read-only check verifying all project memory tables and columns exist without taking a write lock."""
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name IN "
            "('project_decisions', 'session_snapshots', 'project_facts', 'fact_history', "
            "'project_tasks', 'task_verifications', 'task_handoffs', 'symbol_index', 'compaction_archive', 'active_working_set', 'dependency_edges', 'project_memory_fts');"
        )
        found = {row[0] for row in cur.fetchall()}
        if not _REQUIRED_PROJECT_TABLES.issubset(found):
            return False
        cur.execute("PRAGMA table_info(session_snapshots);")
        snap_cols = {row[1] for row in cur.fetchall()}
        if "commit_hash" not in snap_cols:
            return False
        cur.execute("PRAGMA table_info(project_facts);")
        fact_cols = {row[1] for row in cur.fetchall()}
        return "deprecated_at" in fact_cols and "superseded_by" in fact_cols
    except sqlite3.Error:
        return False


def init_project_db(conn: sqlite3.Connection):
    """Initializes tables for architectural decisions, snapshots, project facts, tasks, verifications, and handoffs."""
    db_file = None
    try:
        row = conn.execute("PRAGMA database_list;").fetchone()
        if row and row[2]:
            db_file = os.path.abspath(row[2])
            file_size = os.path.getsize(db_file) if os.path.exists(db_file) else 0
            if file_size > 0 and f"{db_file}:{file_size}" in _VERIFIED_PROJECT_DBS:
                return
    except sqlite3.Error:
        pass

    if _project_schema_ready(conn):
        if db_file and os.path.exists(db_file):
            file_size = os.path.getsize(db_file)
            if file_size > 0:
                _VERIFIED_PROJECT_DBS.add(f"{db_file}:{file_size}")
        return

    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS project_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        category TEXT DEFAULT 'architecture',
        is_active INTEGER DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS session_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT,
        summary TEXT NOT NULL,
        next_steps TEXT,
        files_touched TEXT,
        commit_hash TEXT,
        created_at TEXT NOT NULL
    );
    """)
    # Check if commit_hash column exists in existing databases
    cursor.execute("PRAGMA table_info(session_snapshots);")
    snap_cols = {row[1] for row in cursor.fetchall()}
    if "commit_hash" not in snap_cols:
        cursor.execute("ALTER TABLE session_snapshots ADD COLUMN commit_hash TEXT DEFAULT NULL;")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS project_facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE NOT NULL,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deprecated_at TEXT DEFAULT NULL,
        superseded_by TEXT DEFAULT NULL
    );
    """)
    # Check if deprecated_at column exists in existing databases
    cursor.execute("PRAGMA table_info(project_facts);")
    cols = {row[1] for row in cursor.fetchall()}
    if "deprecated_at" not in cols:
        cursor.execute("ALTER TABLE project_facts ADD COLUMN deprecated_at TEXT DEFAULT NULL;")
    if "superseded_by" not in cols:
        cursor.execute("ALTER TABLE project_facts ADD COLUMN superseded_by TEXT DEFAULT NULL;")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS fact_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT NOT NULL,
        old_value TEXT,
        new_value TEXT NOT NULL,
        reason TEXT,
        created_at TEXT NOT NULL
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS project_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'in_progress', 'completed', 'blocked')),
        priority TEXT NOT NULL DEFAULT 'med' CHECK(priority IN ('low', 'med', 'high', 'critical')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS task_verifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER NOT NULL,
        verifier_type TEXT NOT NULL,
        passed INTEGER NOT NULL,
        details TEXT,
        created_at TEXT NOT NULL
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS task_handoffs (
        id TEXT PRIMARY KEY,
        task_id INTEGER,
        source_role TEXT NOT NULL DEFAULT 'lead',
        target_role TEXT NOT NULL DEFAULT 'worker',
        context_variables TEXT,
        notes TEXT,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'accepted', 'completed', 'rejected')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS symbol_index (
        file_path TEXT NOT NULL,
        symbol_name TEXT NOT NULL,
        qualified_name TEXT NOT NULL,
        kind TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        signature TEXT DEFAULT '',
        file_mtime REAL NOT NULL,
        PRIMARY KEY (file_path, qualified_name, start_line)
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_symbol_name ON symbol_index(symbol_name COLLATE NOCASE);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_symbol_qual ON symbol_index(qualified_name COLLATE NOCASE);")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS compaction_archive (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL,
        checkpoint_num INTEGER NOT NULL,
        step_index INTEGER NOT NULL,
        summary TEXT NOT NULL,
        extracted_refs TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        UNIQUE(conversation_id, step_index)
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS active_working_set (
        file_path TEXT PRIMARY KEY,
        symbols_touched TEXT DEFAULT '',
        line_ranges TEXT DEFAULT '',
        edit_count INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS dependency_edges (
        source_file TEXT NOT NULL,
        source_symbol TEXT NOT NULL,
        target_symbol TEXT NOT NULL,
        edge_kind TEXT NOT NULL,
        line_no INTEGER NOT NULL,
        file_mtime REAL NOT NULL,
        PRIMARY KEY (source_file, source_symbol, target_symbol, edge_kind, line_no)
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_dep_target ON dependency_edges(target_symbol COLLATE NOCASE);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_dep_source ON dependency_edges(source_file);")
    cursor.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS project_memory_fts USING fts5(
        source_table,
        title_or_key,
        content
    );
    """)
    conn.commit()
    if db_file and os.path.exists(db_file):
        file_size = os.path.getsize(db_file)
        if file_size > 0:
            _VERIFIED_PROJECT_DBS.add(f"{db_file}:{file_size}")


def mem_init(project_root: Path = None):
    root = project_root or find_project_root()
    if _is_protected_non_workspace(root):
        print(f"Skipped: '{root}' is a protected system/profile directory, not a project workspace.")
        return
    db_path = get_project_db_path(root)
    with managed_connection(db_path, is_project_db=True) as conn:
        init_project_db(conn)
    if not _is_temp_workspace(root) and not (root / ".git" / "HEAD").exists():
        setup_workspace_git(root, configure_remote=False)
    learn_workspace(root)
    print(f"Project memory database initialized at: {db_path}")


def mem_save_decision(
    conn: sqlite3.Connection,
    title: str,
    content: str,
    category: str = "architecture",
    db_path: Path = None,
    rationale: str = None,
):
    """Saves an architectural decision using WriterQueue if path is available to prevent lock contention."""
    now_iso = datetime.now(timezone.utc).isoformat()
    full_content = f"{content.strip()}\nRationale: {rationale.strip()}" if rationale and rationale.strip() else content
    target_path = _resolve_db_file(conn, db_path)
    if conn and conn.in_transaction:
        conn.commit()

    if target_path and target_path.is_file():
        wq = get_writer_queue(target_path)
        def _write(cur):
            cur.execute("UPDATE project_decisions SET is_active = 0, updated_at = ? WHERE title = ? AND is_active = 1;", (now_iso, title))
            cur.execute("""
            INSERT INTO project_decisions (title, content, category, is_active, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?);
            """, (title, full_content, category, now_iso, now_iso))
            row_id = cur.lastrowid
            cur.execute("DELETE FROM project_memory_fts WHERE source_table = 'decision' AND title_or_key = ?;", (title,))
            cur.execute("""
            INSERT INTO project_memory_fts (source_table, title_or_key, content)
            VALUES ('decision', ?, ?);
            """, (title, full_content))
            return row_id
        row_id = wq.execute_write(_write)
        print(f"Project decision saved (ID {row_id}): '{title}'")
        return

    cursor = conn.cursor()
    cursor.execute("UPDATE project_decisions SET is_active = 0, updated_at = ? WHERE title = ? AND is_active = 1;", (now_iso, title))
    cursor.execute("""
    INSERT INTO project_decisions (title, content, category, is_active, created_at, updated_at)
    VALUES (?, ?, ?, 1, ?, ?);
    """, (title, full_content, category, now_iso, now_iso))
    row_id = cursor.lastrowid
    cursor.execute("DELETE FROM project_memory_fts WHERE source_table = 'decision' AND title_or_key = ?;", (title,))
    cursor.execute("""
    INSERT INTO project_memory_fts (source_table, title_or_key, content)
    VALUES ('decision', ?, ?);
    """, (title, full_content))
    conn.commit()
    print(f"Project decision saved (ID {row_id}): '{title}'")


def mem_save_snapshot(conn: sqlite3.Connection, summary: str, conversation_id: str = None, next_steps: str = None, files_touched: str = None, db_path: Path = None, silent: bool = False, auto_commit: bool = True):
    """Saves a session milestone snapshot and links it with an automated Git/Gitea commit."""
    now_iso = datetime.now(timezone.utc).isoformat()
    cid = conversation_id or os.environ.get("ANTIGRAVITY_CONVERSATION_ID") or "default"
    target_path = _resolve_db_file(conn, db_path)
    if conn and conn.in_transaction:
        conn.commit()

    commit_hash = None
    auto_files = files_touched
    if auto_commit:
        root = None
        if target_path:
            p_target = Path(target_path)
            if p_target.parent.name == ".agents":
                root = p_target.parent.parent
            else:
                root = p_target.parent
        else:
            root = find_project_root()

        # Skip auto-commit in temporary unittest directories unless git was explicitly initialized there
        should_commit = bool(root) and (not _is_temp_workspace(root) or (root / ".git" / "HEAD").exists())
        if should_commit:
            learn_workspace(root)
            try:
                trailers = {"Snapshot-Commit": "true"}
                if cid and cid != "default":
                    trailers["Conversation-ID"] = cid
                if next_steps:
                    trailers["Next-Steps"] = next_steps
                gitea_active = is_gitea_configured(root, db_path=DB_PATH)
                c_res = save_code_commit(root, message=summary, push=gitea_active, async_push=False, trailers=trailers)
                if c_res.get("success"):
                    commit_hash = c_res.get("commit_hash")
                    if not auto_files and c_res.get("changed_files"):
                        auto_files = ", ".join(c_res["changed_files"][:15])
                    if not silent and c_res.get("committed"):
                        if c_res.get("pushed"):
                            print(f"Git commit created: {commit_hash[:7]} (pushed to Gitea)")
                        elif not gitea_active:
                            print(f"Git commit created: {commit_hash[:7]} (saved locally)")
                        elif not _is_temp_workspace(root):
                            print(f"Git commit created: {commit_hash[:7]} [WARNING: Gitea push FAILED - server or fileserver unreachable! Commit queued locally for automatic catch-up sync]")
                        else:
                            print(f"Git commit created: {commit_hash[:7]}")
                    if c_res.get("pushed") and not _is_temp_workspace(root):
                        flushed = flush_all_pending_pushes()
                        if flushed and not silent:
                            print(f"Gitea Catch-Up Sync: Flushed pending commits for {', '.join(flushed)}")
            except Exception as e:
                if not silent and not _is_temp_workspace(root):
                    print(f"[WARNING] Gitea synchronization error: {e}")

    fts_parts = [summary]
    if next_steps:
        fts_parts.append(next_steps)
    if auto_files:
        fts_parts.append(auto_files)
    if commit_hash:
        fts_parts.append(commit_hash[:7])
    fts_content = " ".join(fts_parts)

    if target_path and target_path.is_file():
        wq = get_writer_queue(target_path)
        def _write(cur):
            cur.execute("""
            INSERT INTO session_snapshots (conversation_id, summary, next_steps, files_touched, commit_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """, (cid, summary, next_steps or "", auto_files or "", commit_hash, now_iso))
            row_id = cur.lastrowid
            cur.execute("""
            INSERT INTO project_memory_fts (source_table, title_or_key, content)
            VALUES ('snapshot', ?, ?);
            """, (cid, fts_content))
            return row_id
        row_id = wq.execute_write(_write)
        if not silent:
            print(f"Session snapshot saved (ID {row_id}) for conversation: {cid}")
            print("  [COMPACT MILESTONE TIP] Milestone state & facts are safely persisted in .agents/memory.db. If starting a new feature next, running '/compact' now will shed old context weight with zero knowledge loss.")
        return

    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO session_snapshots (conversation_id, summary, next_steps, files_touched, commit_hash, created_at)
    VALUES (?, ?, ?, ?, ?, ?);
    """, (cid, summary, next_steps or "", auto_files or "", commit_hash, now_iso))
    row_id = cursor.lastrowid
    cursor.execute("""
    INSERT INTO project_memory_fts (source_table, title_or_key, content)
    VALUES ('snapshot', ?, ?);
    """, (cid, fts_content))
    conn.commit()
    if not silent:
        print(f"Session snapshot saved (ID {row_id}) for conversation: {cid}")


def mem_save_fact(conn: sqlite3.Connection, key: str, value: str, reason: str = None, db_path: Path = None):
    """Saves a key-value fact or configuration item with active reconciliation and tombstone history."""
    now_iso = datetime.now(timezone.utc).isoformat()
    target_path = _resolve_db_file(conn, db_path)

    cursor = conn.cursor() if conn else None
    existing_val = None
    if cursor:
        try:
            cursor.execute("SELECT value FROM project_facts WHERE key = ?;", (key,))
            erow = cursor.fetchone()
            if erow:
                existing_val = erow[0]
        except Exception:
            pass
    if conn and conn.in_transaction:
        conn.commit()

    if target_path and target_path.is_file():
        wq = get_writer_queue(target_path)
        def _write(cur):
            cur.execute("SELECT value FROM project_facts WHERE key = ?;", (key,))
            er = cur.fetchone()
            if er and er[0] != value:
                cur.execute("""
                INSERT INTO fact_history (key, old_value, new_value, reason, created_at)
                VALUES (?, ?, ?, ?, ?);
                """, (key, er[0], value, reason or "superseded", now_iso))
            cur.execute("""
            INSERT OR REPLACE INTO project_facts (key, value, updated_at, deprecated_at, superseded_by)
            VALUES (?, ?, ?, NULL, NULL);
            """, (key, value, now_iso))
            cur.execute("DELETE FROM project_memory_fts WHERE source_table = 'fact' AND title_or_key = ?;", (key,))
            cur.execute("""
            INSERT INTO project_memory_fts (source_table, title_or_key, content)
            VALUES ('fact', ?, ?);
            """, (key, value))
        wq.execute_write(_write)
    else:
        if existing_val and existing_val != value:
            cursor.execute("""
            INSERT INTO fact_history (key, old_value, new_value, reason, created_at)
            VALUES (?, ?, ?, ?, ?);
            """, (key, existing_val, value, reason or "superseded", now_iso))
        cursor.execute("""
        INSERT OR REPLACE INTO project_facts (key, value, updated_at, deprecated_at, superseded_by)
        VALUES (?, ?, ?, NULL, NULL);
        """, (key, value, now_iso))
        cursor.execute("DELETE FROM project_memory_fts WHERE source_table = 'fact' AND title_or_key = ?;", (key,))
        cursor.execute("""
        INSERT INTO project_memory_fts (source_table, title_or_key, content)
        VALUES ('fact', ?, ?);
        """, (key, value))
        conn.commit()

    if existing_val and existing_val != value:
        print(f"Project fact updated & reconciled: '{key}' = '{value}' (Previous: '{existing_val}' archived to history)")
    else:
        print(f"Project fact saved: '{key}' = '{value}'")


def mem_deprecate_fact(conn: sqlite3.Connection, key: str, reason: str = "deprecated", db_path: Path = None):
    """Marks a project fact as deprecated/tombstoned so it no longer pollutes the active LLM context."""
    now_iso = datetime.now(timezone.utc).isoformat()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM project_facts WHERE key = ?;", (key,))
    row = cursor.fetchone()
    if not row:
        print(f"Error: Fact '{key}' not found.")
        return False

    old_val = row[0]
    target_path = _resolve_db_file(conn, db_path)
    if conn and conn.in_transaction:
        conn.commit()
    if target_path and target_path.is_file():
        wq = get_writer_queue(target_path)
        def _write(cur):
            cur.execute("""
            UPDATE project_facts SET deprecated_at = ? WHERE key = ?;
            """, (now_iso, key))
            cur.execute("""
            INSERT INTO fact_history (key, old_value, new_value, reason, created_at)
            VALUES (?, ?, ?, ?, ?);
            """, (key, old_val, "[TOMBSTONE]", reason, now_iso))
            cur.execute("DELETE FROM project_memory_fts WHERE source_table = 'fact' AND title_or_key = ?;", (key,))
        wq.execute_write(_write)
    else:
        cursor.execute("""
        UPDATE project_facts SET deprecated_at = ? WHERE key = ?;
        """, (now_iso, key))
        cursor.execute("""
        INSERT INTO fact_history (key, old_value, new_value, reason, created_at)
        VALUES (?, ?, ?, ?, ?);
        """, (key, old_val, "[TOMBSTONE]", reason, now_iso))
        cursor.execute("DELETE FROM project_memory_fts WHERE source_table = 'fact' AND title_or_key = ?;", (key,))
        conn.commit()

    print(f"Project fact deprecated (tombstoned): '{key}' (Reason: {reason})")
    return True


def mem_fact_history(conn: sqlite3.Connection, key: str = None):
    """Displays the audit trail of fact updates, supersessions, and tombstoning."""
    cursor = conn.cursor()
    query = "SELECT id, key, old_value, new_value, reason, created_at FROM fact_history"
    params = []
    if key:
        query += " WHERE key = ?"
        params.append(key)
    query += " ORDER BY id DESC LIMIT 20;"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    print(f"\n=== PROJECT FACT RECONCILIATION AUDIT LOG ({len(rows)} entries) ===")
    if not rows:
        print("  No fact history records found.")
    else:
        for r in rows:
            print(f"  * [#{r['id']}] '{r['key']}': '{r['old_value']}' -> '{r['new_value']}' ({r['reason']}) [{r['created_at'][:19]}]")
    print()


def mem_reconcile(conn: sqlite3.Connection) -> dict:
    """Scans and reports fact lifecycle health, active vs deprecated items, and history count."""
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM project_facts WHERE deprecated_at IS NULL;")
    active_facts = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM project_facts WHERE deprecated_at IS NOT NULL;")
    deprecated_facts = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM fact_history;")
    history_events = cursor.fetchone()[0]

    report = {
        "active_facts": active_facts,
        "deprecated_facts": deprecated_facts,
        "history_reconciliation_events": history_events,
    }
    print(f"\n=== PROJECT FACT RECONCILIATION REPORT ===")
    print(f"  Active Facts:       {active_facts}")
    print(f"  Tombstoned Facts:   {deprecated_facts}")
    print(f"  Reconciled Events:  {history_events}\n")
    return report


def mem_task_add(conn: sqlite3.Connection, title: str, description: str = "", priority: str = "med", status: str = "pending", db_path: Path = None):
    """Adds a new task to the project task board."""
    now_iso = datetime.now(timezone.utc).isoformat()
    priority = (priority or "med").lower()
    if priority not in ('low', 'med', 'high', 'critical'):
        priority = 'med'
    status = (status or "pending").lower()
    if status not in ('pending', 'in_progress', 'completed', 'blocked'):
        status = 'pending'

    target_path = _resolve_db_file(conn, db_path)
    if conn and conn.in_transaction:
        conn.commit()
    if target_path and target_path.is_file():
        wq = get_writer_queue(target_path)
        def _write(cur):
            cur.execute("""
            INSERT INTO project_tasks (title, description, status, priority, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """, (title, description or "", status, priority, now_iso, now_iso))
            row_id = cur.lastrowid
            cur.execute("""
            INSERT INTO project_memory_fts (source_table, title_or_key, content)
            VALUES ('task', ?, ?);
            """, (f"Task #{row_id}: {title}", f"{status} {priority} {description or ''}"))
            return row_id
        row_id = wq.execute_write(_write)
        print(f"Task created (ID {row_id}): [{status.upper()}] ({priority.upper()}) '{title}'")
        return row_id

    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO project_tasks (title, description, status, priority, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?);
    """, (title, description or "", status, priority, now_iso, now_iso))
    row_id = cursor.lastrowid
    cursor.execute("""
    INSERT INTO project_memory_fts (source_table, title_or_key, content)
    VALUES ('task', ?, ?);
    """, (f"Task #{row_id}: {title}", f"{status} {priority} {description or ''}"))
    conn.commit()
    print(f"Task created (ID {row_id}): [{status.upper()}] ({priority.upper()}) '{title}'")
    return row_id


def mem_task_update(conn: sqlite3.Connection, task_id: int, status: str = None, priority: str = None, title: str = None, description: str = None, db_path: Path = None):
    """Updates the status, priority, title, or description of a task."""
    now_iso = datetime.now(timezone.utc).isoformat()
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, description, status, priority FROM project_tasks WHERE id = ?;", (task_id,))
    row = cursor.fetchone()
    if not row:
        print(f"Error: Task ID {task_id} not found.")
        return False

    new_title = title if title is not None else row["title"]
    new_desc = description if description is not None else row["description"]
    new_status = status.lower() if status else row["status"]
    new_priority = priority.lower() if priority else row["priority"]

    if new_status not in ('pending', 'in_progress', 'completed', 'blocked'):
        print(f"Error: Invalid status '{new_status}'. Allowed: pending, in_progress, completed, blocked")
        return False
    if new_priority not in ('low', 'med', 'high', 'critical'):
        print(f"Error: Invalid priority '{new_priority}'. Allowed: low, med, high, critical")
        return False

    target_path = _resolve_db_file(conn, db_path)
    if conn and conn.in_transaction:
        conn.commit()
    if target_path and target_path.is_file():
        wq = get_writer_queue(target_path)
        def _write(cur):
            cur.execute("""
            UPDATE project_tasks
            SET title = ?, description = ?, status = ?, priority = ?, updated_at = ?
            WHERE id = ?;
            """, (new_title, new_desc, new_status, new_priority, now_iso, task_id))
            cur.execute("DELETE FROM project_memory_fts WHERE source_table = 'task' AND title_or_key LIKE ?;", (f"Task #{task_id}:%",))
            cur.execute("""
            INSERT INTO project_memory_fts (source_table, title_or_key, content)
            VALUES ('task', ?, ?);
            """, (f"Task #{task_id}: {new_title}", f"{new_status} {new_priority} {new_desc}"))
        wq.execute_write(_write)
    else:
        cursor.execute("""
        UPDATE project_tasks
        SET title = ?, description = ?, status = ?, priority = ?, updated_at = ?
        WHERE id = ?;
        """, (new_title, new_desc, new_status, new_priority, now_iso, task_id))
        cursor.execute("DELETE FROM project_memory_fts WHERE source_table = 'task' AND title_or_key LIKE ?;", (f"Task #{task_id}:%",))
        cursor.execute("""
        INSERT INTO project_memory_fts (source_table, title_or_key, content)
        VALUES ('task', ?, ?);
        """, (f"Task #{task_id}: {new_title}", f"{new_status} {new_priority} {new_desc}"))
        conn.commit()

    status_change = f" -> [{new_status.upper()}]" if status else ""
    print(f"Task updated (ID {task_id}){status_change}: '{new_title}'")
    return True


def mem_task_list(conn: sqlite3.Connection, status: str = None, as_json: bool = False):
    """Lists tasks filtered by status (or all active tasks by default)."""
    cursor = conn.cursor()
    params = []
    query = "SELECT id, title, description, status, priority, created_at, updated_at FROM project_tasks"
    if status and status.lower() != "all":
        query += " WHERE status = ?"
        params.append(status.lower())
    query += """
    ORDER BY 
        CASE status WHEN 'in_progress' THEN 1 WHEN 'pending' THEN 2 WHEN 'blocked' THEN 3 ELSE 4 END,
        CASE priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'med' THEN 3 ELSE 4 END,
        id ASC;
    """
    cursor.execute(query, params)
    rows = cursor.fetchall()

    if as_json:
        data = [dict(r) for r in rows]
        print(json.dumps(data, indent=2))
        return

    filter_info = f" (Filter: {status.upper()})" if status else ""
    print(f"\n=== PROJECT TASK BOARD{filter_info} ({len(rows)} tasks) ===")
    if not rows:
        print("  No tasks found.")
    else:
        for r in rows:
            stat = r['status'].upper()
            prio = r['priority'].upper()
            desc = f" - {r['description']}" if r['description'] else ""
            print(f"  [#{r['id']}] [{stat:11}] ({prio:8}) {r['title']}{desc}")
    print("=====================================================\n")


def mem_task_clear(conn: sqlite3.Connection, only_completed: bool = True, db_path: Path = None):
    """Clears completed tasks (or all tasks)."""
    target_path = _resolve_db_file(conn, db_path)
    if conn and conn.in_transaction:
        conn.commit()
    if target_path and target_path.is_file():
        wq = get_writer_queue(target_path)
        def _write(cur):
            if only_completed:
                cur.execute("SELECT id FROM project_tasks WHERE status = 'completed';")
                cids = [r[0] for r in cur.fetchall()]
                cur.execute("DELETE FROM project_tasks WHERE status = 'completed';")
                deleted_rows = cur.rowcount
                for cid_val in cids:
                    cur.execute("DELETE FROM project_memory_fts WHERE source_table = 'task' AND title_or_key LIKE ?;", (f"Task #{cid_val}:%",))
                return deleted_rows
            else:
                cur.execute("DELETE FROM project_tasks;")
                deleted_rows = cur.rowcount
                cur.execute("DELETE FROM project_memory_fts WHERE source_table = 'task';")
                return deleted_rows
        deleted = wq.execute_write(_write)
    else:
        cursor = conn.cursor()
        if only_completed:
            cursor.execute("SELECT id FROM project_tasks WHERE status = 'completed';")
            cids = [r[0] for r in cursor.fetchall()]
            cursor.execute("DELETE FROM project_tasks WHERE status = 'completed';")
            deleted = cursor.rowcount
            for cid_val in cids:
                cursor.execute("DELETE FROM project_memory_fts WHERE source_table = 'task' AND title_or_key LIKE ?;", (f"Task #{cid_val}:%",))
        else:
            cursor.execute("DELETE FROM project_tasks;")
            deleted = cursor.rowcount
            cursor.execute("DELETE FROM project_memory_fts WHERE source_table = 'task';")
        conn.commit()

    target_str = "completed tasks" if only_completed else "all tasks"
    print(f"Cleared {deleted} {target_str} from project task board.")


def mem_task_delete(conn: sqlite3.Connection, task_id: int, db_path: Path = None) -> bool:
    """Deletes a single task by ID and removes its entry from project_memory_fts."""
    target_path = _resolve_db_file(conn, db_path)
    if conn and conn.in_transaction:
        conn.commit()
    if target_path and target_path.is_file():
        wq = get_writer_queue(target_path)
        def _write(cur):
            cur.execute("DELETE FROM project_tasks WHERE id = ?;", (task_id,))
            deleted_rows = cur.rowcount
            cur.execute("DELETE FROM project_memory_fts WHERE source_table = 'task' AND title_or_key LIKE ?;", (f"Task #{task_id}:%",))
            return deleted_rows > 0
        deleted = wq.execute_write(_write)
    else:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM project_tasks WHERE id = ?;", (task_id,))
        deleted = cursor.rowcount > 0
        cursor.execute("DELETE FROM project_memory_fts WHERE source_table = 'task' AND title_or_key LIKE ?;", (f"Task #{task_id}:%",))
        conn.commit()
    if deleted:
        print(f"Task #{task_id} deleted from project task board.")
    else:
        print(f"Error: Task ID {task_id} not found.")
    return deleted


def mem_compact(conn: sqlite3.Connection, summary: str = None, next_steps: str = None, archive_completed: bool = True, project_root: Path = None, db_path: Path = None, silent: bool = False, auto_commit: bool = True):
    """Compacts episodic project memory into a distilled snapshot and vacuums DB."""
    root = project_root or find_project_root()
    flush_journals(root, conn)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM project_tasks WHERE status = 'completed';")
    completed_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM project_tasks WHERE status IN ('pending', 'in_progress', 'blocked');")
    active_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM project_decisions WHERE is_active = 1;")
    decisions_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM project_facts WHERE deprecated_at IS NULL;")
    facts_count = cursor.fetchone()[0]

    cid = os.environ.get("ANTIGRAVITY_CONVERSATION_ID") or "default"

    default_summary = (
        f"[AUTO-COMPACTED] Project context consolidated. "
        f"{active_count} active tasks, {decisions_count} architectural decisions, {facts_count} facts."
    )
    snap_summary = summary or default_summary

    target_path = _resolve_db_file(conn, db_path)
    if conn and conn.in_transaction:
        conn.commit()

    if archive_completed and completed_count > 0:
        if target_path and target_path.is_file():
            wq = get_writer_queue(target_path)
            def _write_del(cur):
                cur.execute("SELECT id FROM project_tasks WHERE status = 'completed';")
                cids = [r[0] for r in cur.fetchall()]
                cur.execute("DELETE FROM project_tasks WHERE status = 'completed';")
                deleted_rows = cur.rowcount
                for cid_val in cids:
                    cur.execute("DELETE FROM project_memory_fts WHERE source_table = 'task' AND title_or_key LIKE ?;", (f"Task #{cid_val}:%",))
                return deleted_rows
            wq.execute_write(_write_del)
        else:
            cursor.execute("SELECT id FROM project_tasks WHERE status = 'completed';")
            cids = [r[0] for r in cursor.fetchall()]
            cursor.execute("DELETE FROM project_tasks WHERE status = 'completed';")
            for cid_val in cids:
                cursor.execute("DELETE FROM project_memory_fts WHERE source_table = 'task' AND title_or_key LIKE ?;", (f"Task #{cid_val}:%",))
            conn.commit()

    mem_save_snapshot(conn, snap_summary, conversation_id=cid, next_steps=next_steps, db_path=target_path, silent=silent, auto_commit=auto_commit)

    try:
        conn.execute("PRAGMA incremental_vacuum;")
        conn.commit()
    except Exception:
        pass

    if not silent:
        print("\n=== SESSION AUTO-COMPACTION COMPLETED ===")
        print(f"  Summary:          {snap_summary}")
        if next_steps:
            print(f"  Next Steps:       {next_steps}")
        print(f"  Active Tasks:     {active_count}")
        print(f"  Completed Pruned: {completed_count if archive_completed else 0}")
        print(f"  Active Decisions: {decisions_count}")
        print(f"  Active Facts:     {facts_count}")
        print("=========================================\n")


def mem_get_context(conn: sqlite3.Connection, max_decisions: int = 5, project_root: Path = None):
    """Outputs compact context: active architectural decisions, facts, open tasks, and latest snapshot."""
    root = project_root or find_project_root()
    flush_journals(root, conn)
    cursor = conn.cursor()

    print("\n=== PROJECT MEMORY CONTEXT ===")
    git_telem = get_workspace_git_telemetry(root)
    if git_telem:
        print(f"  {git_telem}")

    cursor.execute("SELECT summary, next_steps, files_touched, commit_hash, created_at FROM session_snapshots ORDER BY id DESC LIMIT 1;")
    last_snap = cursor.fetchone()
    if last_snap:
        dt = last_snap['created_at'][:10]
        print(f"\nLatest Session Milestone ({dt}):")
        print(f"  Summary:    {last_snap['summary']}")
        if "commit_hash" in last_snap.keys() and last_snap['commit_hash']:
            print(f"  Commit:     {last_snap['commit_hash'][:7]}")
        if "files_touched" in last_snap.keys() and last_snap['files_touched']:
            print(f"  Files:      {last_snap['files_touched']}")
        if last_snap['next_steps']:
            print(f"  Next Steps: {last_snap['next_steps']}")

    cursor.execute("""
    SELECT id, title, status, priority, updated_at FROM project_tasks
    WHERE status IN ('pending', 'in_progress', 'blocked')
    ORDER BY 
        CASE status WHEN 'in_progress' THEN 1 WHEN 'pending' THEN 2 ELSE 3 END,
        CASE priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'med' THEN 3 ELSE 4 END,
        id ASC;
    """)
    open_tasks = cursor.fetchall()
    if open_tasks:
        print("\nActive Task Board:")
        for t in open_tasks:
            t_dt = f" [{str(t['updated_at'])[:10]}]" if t['updated_at'] else ""
            print(f"  * [#{t['id']}] [{t['status'].upper():11}] ({t['priority'].upper():8}){t_dt}: {t['title']}")

    cursor.execute("""
    SELECT title, content, category, updated_at FROM project_decisions
    WHERE is_active = 1 ORDER BY updated_at DESC LIMIT ?;
    """, (max_decisions,))
    decisions = cursor.fetchall()
    if decisions:
        print("\nActive Architectural Decisions:")
        for d in decisions:
            d_dt = f" [{str(d['updated_at'])[:10]}]" if d['updated_at'] else ""
            print(f"  * [{d['category'].upper()}]{d_dt} {d['title']}: {d['content']}")

    cursor.execute("SELECT key, value, updated_at FROM project_facts WHERE deprecated_at IS NULL ORDER BY updated_at DESC;")
    facts = cursor.fetchall()
    if facts:
        print("\nProject Facts & Configs:")
        for f in facts:
            f_dt = f" [{str(f['updated_at'])[:10]}]" if f['updated_at'] else ""
            print(f"  * {f['key']}{f_dt}: {f['value']}")

    if not last_snap and not decisions and not facts and not open_tasks:
        print("No memory records found in project database.")
    print("==============================\n")


def mem_search(conn: sqlite3.Connection, query: str):
    """FTS5 search across project facts, decisions, and session milestones."""
    cursor = conn.cursor()
    print(f"\n--- Search results for '{query}' in Project Memory ---\n")
    safe_fts = build_safe_fts_query([query])
    if not safe_fts:
        print("No matching project memory entries.")
        return
    try:
        cursor.execute("""
        SELECT source_table, title_or_key, content
        FROM project_memory_fts
        WHERE project_memory_fts MATCH ?
        LIMIT 10;
        """, (safe_fts,))
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        rows = []
    if not rows:
        print("No matching project memory entries.")
        return

    for r in rows:
        print(f"[{r['source_table'].upper()}] {r['title_or_key']}")
        print(f"  {r['content'].strip()}\n")


def mem_prune(conn: sqlite3.Connection, max_snapshots: int = 10, max_age_days: int = 30):
    """Reconciles deleted conversation IDs, prunes expired snapshots, and vacuums database."""
    brain_dir = resolve_user_home() / ".gemini" / "antigravity" / "brain"
    existing_cids = set()
    if brain_dir.exists():
        existing_cids = {f.name for f in brain_dir.iterdir() if f.is_dir()}

    cursor = conn.cursor()
    cursor.execute("SELECT id, conversation_id, created_at FROM session_snapshots;")
    snapshots = cursor.fetchall()

    pruned_orphans = 0
    pruned_expired = 0
    now = datetime.now(timezone.utc)

    for s in snapshots:
        cid = s["conversation_id"]
        sid = s["id"]
        age_days = 0
        try:
            snap_time = datetime.fromisoformat(s["created_at"].replace("Z", "+00:00"))
            age_days = (now - snap_time).days
        except Exception:
            pass

        if cid and cid != "default" and existing_cids and cid not in existing_cids and age_days > max_age_days:
            cursor.execute("DELETE FROM session_snapshots WHERE id = ?;", (sid,))
            pruned_orphans += 1
            continue

        if age_days > max_age_days:
            cursor.execute("DELETE FROM session_snapshots WHERE id = ?;", (sid,))
            pruned_expired += 1

    cursor.execute("SELECT id FROM session_snapshots ORDER BY id DESC;")
    all_remaining = [r[0] for r in cursor.fetchall()]
    if len(all_remaining) > max_snapshots:
        excess_ids = all_remaining[max_snapshots:]
        cursor.executemany("DELETE FROM session_snapshots WHERE id = ?;", [(eid,) for eid in excess_ids])
        pruned_expired += len(excess_ids)

    if pruned_orphans > 0 or pruned_expired > 0:
        cursor.execute("DELETE FROM project_memory_fts WHERE source_table = 'snapshot';")
        cursor.execute("SELECT conversation_id, summary, next_steps, files_touched, commit_hash FROM session_snapshots ORDER BY id ASC;")
        for rem in cursor.fetchall():
            rem_cid = rem["conversation_id"] or "default"
            parts = [rem["summary"] or ""]
            if rem["next_steps"]:
                parts.append(rem["next_steps"])
            if rem["files_touched"]:
                parts.append(rem["files_touched"])
            if rem["commit_hash"]:
                parts.append(rem["commit_hash"][:7])
            cursor.execute(
                "INSERT INTO project_memory_fts (source_table, title_or_key, content) VALUES ('snapshot', ?, ?);",
                (rem_cid, " ".join(p for p in parts if p)),
            )

    conn.commit()
    cursor.execute("PRAGMA incremental_vacuum;")
    conn.commit()
    print(f"Project memory pruned: {pruned_orphans} orphaned conversation snapshots removed, {pruned_expired} old snapshots trimmed. Vacuum complete.")


_INDEX_SKIP_DIRS = frozenset({
    ".git", ".agents", "__pycache__", "node_modules", ".venv", "venv",
    "build", "dist", "out", ".dart_tool", ".idea", ".vscode", "target",
})
_INDEX_CODE_EXTS = frozenset({
    ".py", ".pyw", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".cs",
    ".java", ".kt", ".kts", ".dart", ".smali", ".ps1", ".psm1", ".sh", ".bash",
    ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".sql", ".rb", ".php", ".swift",
})


def _extract_file_symbols_for_index(file_path: Path, rel_posix: str, mtime: float, content_override: str = None) -> list[tuple]:
    """Extracts (file_path, symbol_name, qualified_name, kind, start_line, end_line, signature, file_mtime) rows."""
    import ast as _ast
    from ..core.fim import _extract_generic_regex_contracts

    rows: list[tuple] = []
    ext = file_path.suffix.lower()
    if content_override is not None:
        text = content_override
    else:
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return rows
    lines = text.splitlines()
    if not lines:
        return rows

    if ext == ".py":
        try:
            tree = _ast.parse(text)
            for node in tree.body:
                if isinstance(node, _ast.ClassDef):
                    decs = getattr(node, "decorator_list", None) or []
                    c_start = min([node.lineno] + [getattr(d, "lineno", node.lineno) for d in decs])
                    c_end = getattr(node, "end_lineno", None) or node.lineno
                    c_sig = lines[node.lineno - 1].strip()[:120] if 1 <= node.lineno <= len(lines) else f"class {node.name}"
                    rows.append((rel_posix, node.name, node.name, "class", c_start, c_end, c_sig, mtime))
                    for item in node.body:
                        if isinstance(item, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                            m_decs = getattr(item, "decorator_list", None) or []
                            m_start = min([item.lineno] + [getattr(d, "lineno", item.lineno) for d in m_decs])
                            m_end = getattr(item, "end_lineno", None) or item.lineno
                            m_sig = lines[item.lineno - 1].strip()[:120] if 1 <= item.lineno <= len(lines) else f"def {item.name}"
                            qual = f"{node.name}.{item.name}"
                            rows.append((rel_posix, item.name, qual, "method", m_start, m_end, m_sig, mtime))
                elif isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    f_decs = getattr(node, "decorator_list", None) or []
                    f_start = min([node.lineno] + [getattr(d, "lineno", node.lineno) for d in f_decs])
                    f_end = getattr(node, "end_lineno", None) or node.lineno
                    f_sig = lines[node.lineno - 1].strip()[:120] if 1 <= node.lineno <= len(lines) else f"def {node.name}"
                    rows.append((rel_posix, node.name, node.name, "function", f_start, f_end, f_sig, mtime))
            return rows
        except SyntaxError:
            pass
    elif ext == ".smali":
        cname = file_path.stem
        c_start = 1
        c_sig = f".class {cname}"
        m_start_line = 0
        m_name = ""
        m_sig = ""
        for idx, raw_line in enumerate(lines, start=1):
            stripped = raw_line.strip()
            if stripped.startswith(".class "):
                cm = re.match(r"^\.class\s+.*?\s+L([^;]+);", stripped)
                if cm:
                    full_cls = cm.group(1).strip()
                    cname = full_cls.rsplit("/", 1)[-1]
                c_start = idx
                c_sig = stripped[:120]
            elif stripped.startswith(".method "):
                mm = re.match(r"^\.method\s+(?:[^\n]*?\s+)?([A-Za-z0-9_$<>-]+)\(([^)]*)\)(.+)$", stripped)
                if mm:
                    m_name = mm.group(1)
                    m_start_line = idx
                    m_sig = stripped[:120]
            elif stripped.startswith(".end method") and m_start_line > 0 and m_name:
                qual = f"{cname}.{m_name}" if cname else m_name
                rows.append((rel_posix, m_name, qual, "method", m_start_line, idx, m_sig, mtime))
                m_start_line = 0
                m_name = ""
                m_sig = ""
        rows.insert(0, (rel_posix, cname, cname, "class", c_start, len(lines), c_sig, mtime))
        return rows

    loc_re = re.compile(r"\[L(\d+)(?:-L(\d+))?\]\s*$")
    gmap = _extract_generic_regex_contracts(text, suffix=ext)
    for c in gmap.get("classes", []):
        hdr = str(c.get("header") or "").strip()
        m_loc = loc_re.search(hdr)
        s_l = int(m_loc.group(1)) if m_loc else 1
        e_l = int(m_loc.group(2) or s_l) if m_loc else s_l
        hdr_no_loc = loc_re.sub("", hdr).strip()
        cm = re.search(
            r"(?:(?:class|interface|struct|enum|trait|record|object|actor|extension|module)\s+([A-Za-z_][A-Za-z0-9_]*)"
            r"|type\s+([A-Za-z_][A-Za-z0-9_]*)\s+(?:struct|interface)\b"
            r"|impl(?:\s*<[^>]*>)?\s+(?:[A-Za-z0-9_:<>]+\s+for\s+)?([A-Za-z_][A-Za-z0-9_]*))",
            hdr_no_loc,
        )
        cname = (cm.group(1) or cm.group(2) or cm.group(3) or "") if cm else ""
        if cname:
            rows.append((rel_posix, cname, cname, "class", s_l, e_l, hdr_no_loc[:120], mtime))
        for m_str in c.get("methods", []):
            m_s = str(m_str).strip()
            ml = loc_re.search(m_s)
            ms_l = int(ml.group(1)) if ml else s_l
            me_l = int(ml.group(2) or ms_l) if ml else ms_l
            m_no_loc = loc_re.sub("", m_s).strip()
            mm = re.search(r"([A-Za-z_~][A-Za-z0-9_:-]*)\s*\(", m_no_loc)
            if not mm:
                mm = re.search(r"(?:function|filter|workflow|def|func|fn|fun)\s+([A-Za-z_][A-Za-z0-9_:-]*)", m_no_loc)
            mname = mm.group(1) if mm else ""
            if mname:
                qual = f"{cname}.{mname}" if cname else mname
                rows.append((rel_posix, mname, qual, "method", ms_l, me_l, m_no_loc[:120], mtime))
    for f_str in gmap.get("functions", []):
        f_s = str(f_str).strip()
        fl = loc_re.search(f_s)
        fs_l = int(fl.group(1)) if fl else 1
        fe_l = int(fl.group(2) or fs_l) if fl else fs_l
        f_no_loc = loc_re.sub("", f_s).strip()
        # 1. Go receiver method: func (s *Server) HandleRequest(...)
        go_m = re.match(r"^\s*func\s+\(\s*(?:[A-Za-z_][A-Za-z0-9_]*\s+)?\*?([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*([A-Za-z_][A-Za-z0-9_]*)", f_no_loc)
        if go_m:
            r_cls, r_fn = go_m.group(1), go_m.group(2)
            rows.append((rel_posix, r_fn, f"{r_cls}.{r_fn}", "method", fs_l, fe_l, f_no_loc[:120], mtime))
            continue
        # 2. C++ out-of-line method: RetType ClassName::methodName(...)
        cpp_m = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)::([A-Za-z_~][A-Za-z0-9_]*)\s*\(", f_no_loc)
        if cpp_m:
            c_cls, c_fn = cpp_m.group(1), cpp_m.group(2)
            rows.append((rel_posix, c_fn, f"{c_cls}.{c_fn}", "method", fs_l, fe_l, f_no_loc[:120], mtime))
            continue
        # 3. SQL CREATE TABLE/VIEW/FUNCTION/PROCEDURE/TRIGGER
        sql_m = re.match(r"^CREATE\s+(TABLE|VIEW|PROCEDURE|PROC|FUNCTION|TRIGGER)\s+([A-Za-z0-9_.\[\]\"]+)", f_no_loc, re.IGNORECASE)
        if sql_m:
            skind = sql_m.group(1).lower()
            sname = sql_m.group(2).strip("[]\"").split(".")[-1]
            rows.append((rel_posix, sname, sname, skind, fs_l, fe_l, f_no_loc[:120], mtime))
            continue
        fm = re.search(
            r"(?:function|filter|workflow|def|func|fn|fun|const|let|var)\s+(?:<[^>]+>\s*)?(?:\([^)]+\)\s*)?(?:[A-Za-z_][A-Za-z0-9_<>.*]*\.)?([A-Za-z_][A-Za-z0-9_:-]*)",
            f_no_loc,
        )
        if not fm:
            fm = re.search(r"([A-Za-z_~][A-Za-z0-9_:-]*)\s*\(", f_no_loc)
        fname = fm.group(1) if fm else ""
        if fname:
            rows.append((rel_posix, fname, fname, "function", fs_l, fe_l, f_no_loc[:120], mtime))
    return rows


_BUILTIN_CALL_NOISE = frozenset({
    "len", "str", "int", "float", "bool", "dict", "list", "set", "tuple", "frozenset",
    "print", "range", "isinstance", "issubclass", "getattr", "setattr", "hasattr",
    "min", "max", "sum", "any", "all", "enumerate", "zip", "sorted", "reversed",
    "super", "open", "abs", "round", "type", "id", "ord", "chr", "hex", "oct", "bin",
    "repr", "format", "iter", "next", "callable", "vars", "dir", "map", "filter",
    "append", "extend", "strip", "lstrip", "rstrip", "split", "splitlines", "join",
    "replace", "lower", "upper", "startswith", "endswith", "get", "items", "keys", "values",
    "add", "update", "pop", "remove", "clear", "copy", "encode", "decode", "format",
})


def _extract_file_edges_for_index(
    file_path: Path,
    rel_posix: str,
    mtime: float,
    content_override: str = None,
) -> list[tuple]:
    """
    Extracts relational codebase graph edges:
    (source_file, source_symbol, target_symbol, edge_kind, line_no, file_mtime)
    where edge_kind is 'calls', 'imports', or 'inherits'.
    """
    ext = file_path.suffix.lower()
    if content_override is not None:
        text = content_override
    else:
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return []

    edges: list[tuple] = []
    seen_edges: set[tuple] = set()

    def _add_edge(src_sym: str, tgt_sym: str, kind: str, line_no: int):
        if not tgt_sym or tgt_sym in _BUILTIN_CALL_NOISE or len(tgt_sym) < 2:
            return
        key = (rel_posix, src_sym, tgt_sym, kind, int(line_no))
        if key not in seen_edges:
            seen_edges.add(key)
            edges.append((rel_posix, src_sym, tgt_sym, kind, int(line_no), mtime))

    if ext in (".py", ".pyw"):
        import ast as _ast
        try:
            tree = _ast.parse(text)

            def _call_target_name(func_node) -> tuple[str, str]:
                if isinstance(func_node, _ast.Name):
                    return func_node.id, ""
                if isinstance(func_node, _ast.Attribute):
                    attr = func_node.attr
                    if isinstance(func_node.value, _ast.Name):
                        val_id = func_node.value.id
                        if val_id not in ("self", "cls"):
                            return attr, f"{val_id}.{attr}"
                        return attr, ""
                    return attr, ""
                return "", ""

            def _walk_scope_calls(body_nodes, scope_name: str):
                for stmt in body_nodes:
                    for sub in _ast.walk(stmt):
                        if isinstance(sub, _ast.Call):
                            short_t, qual_t = _call_target_name(sub.func)
                            lno = getattr(sub, "lineno", 1) or 1
                            if short_t:
                                _add_edge(scope_name, short_t, "calls", lno)
                            if qual_t:
                                _add_edge(scope_name, qual_t, "calls", lno)
                        elif isinstance(sub, _ast.Import):
                            lno = getattr(sub, "lineno", 1) or 1
                            for alias in sub.names:
                                _add_edge(scope_name, alias.name.split(".")[-1], "imports", lno)
                        elif isinstance(sub, _ast.ImportFrom):
                            lno = getattr(sub, "lineno", 1) or 1
                            for alias in sub.names:
                                if alias.name != "*":
                                    _add_edge(scope_name, alias.name, "imports", lno)

            for node in tree.body:
                if isinstance(node, _ast.Import):
                    lno = getattr(node, "lineno", 1) or 1
                    for alias in node.names:
                        _add_edge("<module>", alias.name.split(".")[-1], "imports", lno)
                elif isinstance(node, _ast.ImportFrom):
                    lno = getattr(node, "lineno", 1) or 1
                    for alias in node.names:
                        if alias.name != "*":
                            _add_edge("<module>", alias.name, "imports", lno)
                elif isinstance(node, _ast.ClassDef):
                    lno = getattr(node, "lineno", 1) or 1
                    for base in node.bases:
                        if isinstance(base, _ast.Name):
                            _add_edge(node.name, base.id, "inherits", lno)
                        elif isinstance(base, _ast.Attribute):
                            _add_edge(node.name, base.attr, "inherits", lno)
                    for item in node.body:
                        if isinstance(item, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                            qual = f"{node.name}.{item.name}"
                            _walk_scope_calls(item.body, qual)
                elif isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    _walk_scope_calls(node.body, node.name)
            return edges
        except SyntaxError:
            pass

    # Generic fast regex edge extractor for Smali, Kotlin, Java, TS/JS, Go, Rust, C/C++, C#, Dart, PowerShell, SQL, Bash
    lines = text.splitlines()
    current_cls = ""
    current_scope = "<module>"
    for idx, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith(("//", "#", "/*", "*", "--")):
            continue
        if ext == ".smali":
            if line.startswith(".class "):
                m_c = re.search(r"L([^;\s]+);", line)
                if m_c:
                    current_cls = m_c.group(1).split("/")[-1]
                    current_scope = current_cls
            elif line.startswith(".super "):
                m_s = re.search(r"L([^;\s]+);", line)
                if m_s:
                    _add_edge(current_cls or current_scope, m_s.group(1).split("/")[-1], "inherits", idx)
            elif line.startswith(".method "):
                m_m = re.search(r"([A-Za-z0-9_$<>-]+)\([^)]*\)", line)
                if m_m:
                    cls_prefix = current_cls or current_scope.split(".")[0]
                    current_scope = f"{cls_prefix}.{m_m.group(1)}"
            elif line.startswith("invoke-"):
                m_inv = re.search(r"L([^;\s]+);->([A-Za-z0-9_$<>-]+)\(", line)
                if m_inv:
                    t_cls = m_inv.group(1).split("/")[-1]
                    t_m = m_inv.group(2)
                    _add_edge(current_scope, t_m, "calls", idx)
                    _add_edge(current_scope, f"{t_cls}.{t_m}", "calls", idx)
            continue

        # ES6 / TS import { X, Y } from 'mod' or import X from 'mod'
        m_es_imp = re.match(r"^import\s+(?:type\s+)?\{([^}]+)\}\s+from\s+", line)
        if m_es_imp:
            for item in m_es_imp.group(1).split(","):
                tok = item.strip().split(" as ")[0].strip()
                if tok:
                    _add_edge("<module>", tok, "imports", idx)
            continue

        m_imp = re.match(r'^(?:import|using|use|#include|require)\s+([A-Za-z0-9_.:/*"<>-]+)', line)
        if m_imp:
            imp_tok = m_imp.group(1).strip("\"'<>").split(".")[-1].split("/")[-1].split("::")[-1]
            if imp_tok and imp_tok != "*":
                _add_edge("<module>", imp_tok, "imports", idx)
            continue

        m_cls = re.search(
            r"\b(?:(?:class|interface|struct|trait|record|object|actor|extension|module)\s+([A-Za-z_][A-Za-z0-9_]*)"
            r"|type\s+([A-Za-z_][A-Za-z0-9_]*)\s+(?:struct|interface)\b"
            r"|impl(?:\s*<[^>]*>)?\s+(?:([A-Za-z_][A-Za-z0-9_]*)\s+for\s+)?([A-Za-z_][A-Za-z0-9_]*))"
            r"(?:\s*(?:extends|implements|:|<)\s*([A-Za-z_][A-Za-z0-9_.]*))?",
            line,
        )
        if m_cls:
            cname = m_cls.group(1) or m_cls.group(2) or m_cls.group(4) or ""
            if cname:
                current_cls = cname
                current_scope = cname
            inh = m_cls.group(3) or m_cls.group(5) or ""
            if cname and inh:
                _add_edge(cname, inh.split(".")[-1], "inherits", idx)

        # Go receiver method: func (s *Server) Handle(...)
        m_go = re.match(r"^func\s+\(\s*(?:[A-Za-z_][A-Za-z0-9_]*\s+)?\*?([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(", line)
        if m_go:
            current_cls = m_go.group(1)
            current_scope = f"{m_go.group(1)}.{m_go.group(2)}"
            continue

        # C++ out-of-line method: RetType ClassName::methodName(...)
        m_cpp = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)::([A-Za-z_~][A-Za-z0-9_]*)\s*\([^;]*\)\s*(?:const\s*)?(?:noexcept\s*)?\{", line)
        if m_cpp:
            current_cls = m_cpp.group(1)
            current_scope = f"{m_cpp.group(1)}.{m_cpp.group(2)}"
            continue

        m_fn = re.search(r"\b(?:def|fun|func|fn|function|filter|workflow)\s+(?:<[^>]+>\s*)?([A-Za-z_][A-Za-z0-9_:-]*)\s*(?:\(|\{)", line)
        if m_fn:
            fn_id = m_fn.group(1)
            current_scope = f"{current_cls}.{fn_id}" if current_cls else fn_id
            continue

        # Class method or C/C++ function definition ending with '{'
        if line.endswith("{") and "(" in line and not line.startswith(("if", "for", "while", "switch", "catch")):
            m_mdef = re.search(r"([A-Za-z_~][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*(?:const\s*)?(?:async\s*)?(?::\s*[^{;]+|throws\s+[^{;]+)?\{$", line)
            if m_mdef:
                m_id = m_mdef.group(1)
                if m_id not in ("if", "for", "while", "switch", "catch", "synchronized", "using", "lock"):
                    current_scope = f"{current_cls}.{m_id}" if current_cls else m_id
                    continue

        # PowerShell Verb-Noun invocations
        if ext in (".ps1", ".psm1"):
            for m_ps in re.finditer(r"\b([A-Z][a-z0-9]+-[A-Z][A-Za-z0-9]+)\b", line):
                ps_cmd = m_ps.group(1)
                if not line.startswith(f"function {ps_cmd}"):
                    _add_edge(current_scope, ps_cmd, "calls", idx)

        # Shell / Bash bare function invocations
        if ext in (".sh", ".bash"):
            m_sh_call = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)(?:\s+|$)", line)
            if m_sh_call:
                sh_cmd = m_sh_call.group(1)
                if sh_cmd not in ("echo", "return", "local", "export", "cd", "if", "then", "else", "elif", "fi", "for", "while", "do", "done", "case", "esac", "set", "exit", "source"):
                    _add_edge(current_scope, sh_cmd, "calls", idx)

        # Standard function / method / :: static calls
        for m_call in re.finditer(r"(?:([A-Za-z_][A-Za-z0-9_]*)(?:\.|::))?([A-Za-z_][A-Za-z0-9_]*)\s*\(", line):
            obj_name = m_call.group(1) or ""
            fn_name = m_call.group(2)
            if fn_name in ("if", "for", "while", "switch", "catch", "when", "return", "sizeof", "typeof"):
                continue
            _add_edge(current_scope, fn_name, "calls", idx)
            if obj_name and obj_name not in ("this", "self", "super"):
                _add_edge(current_scope, f"{obj_name}.{fn_name}", "calls", idx)

    return edges


def mem_index_symbols(
    conn: sqlite3.Connection,
    project_root: Path = None,
    force: bool = False,
    silent: bool = False,
) -> dict:
    """Incrementally indexes workspace symbols, [start_line, end_line] ranges, and dependency_edges into .agents/memory.db."""
    root = Path(os.path.abspath(str(project_root or find_project_root())))
    init_project_db(conn)
    cur = conn.cursor()

    existing_mtimes: dict[str, float] = {}
    edges_empty = False
    if not force:
        try:
            cur.execute("SELECT file_path, MAX(file_mtime) FROM symbol_index GROUP BY file_path;")
            existing_mtimes = {str(r[0]): float(r[1] or 0.0) for r in cur.fetchall()}
            if existing_mtimes:
                cur.execute("SELECT 1 FROM dependency_edges LIMIT 1;")
                edges_empty = (cur.fetchone() is None)
        except sqlite3.Error:
            existing_mtimes = {}

    seen_files: set[str] = set()
    updated_files = 0
    total_symbols = 0
    to_delete_files: list[str] = []
    to_insert_rows: list[tuple] = []
    to_insert_edges: list[tuple] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in _INDEX_SKIP_DIRS and not d.startswith(".pytest")
        ]
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in _INDEX_CODE_EXTS:
                continue
            full_p = Path(dirpath) / fname
            try:
                rel_posix = full_p.relative_to(root).as_posix()
            except ValueError:
                continue
            if rel_posix == "database/db_manager.py":
                continue
            seen_files.add(rel_posix)
            try:
                st = full_p.stat()
                if st.st_size > 1_500_000:
                    continue
                mtime = round(float(st.st_mtime), 3)
            except OSError:
                continue
            prev_mtime = existing_mtimes.get(rel_posix)
            if not force and not edges_empty and prev_mtime is not None and abs(prev_mtime - mtime) < 0.005:
                continue
            updated_files += 1
            to_delete_files.append(rel_posix)
            try:
                content_txt = full_p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            to_insert_rows.extend(_extract_file_symbols_for_index(full_p, rel_posix, mtime, content_override=content_txt))
            to_insert_edges.extend(_extract_file_edges_for_index(full_p, rel_posix, mtime, content_override=content_txt))

    stale_files = [fp for fp in existing_mtimes if fp not in seen_files]
    all_del = to_delete_files + stale_files

    if all_del or to_insert_rows or to_insert_edges or force:
        target_path = _resolve_db_file(conn)
        if conn and conn.in_transaction:
            conn.commit()
        if target_path and target_path.is_file():
            wq = get_writer_queue(target_path)
            def _write(wcur):
                if force:
                    wcur.execute("DELETE FROM symbol_index;")
                    wcur.execute("DELETE FROM dependency_edges;")
                elif all_del:
                    wcur.executemany("DELETE FROM symbol_index WHERE file_path = ?;", [(fp,) for fp in all_del])
                    wcur.executemany("DELETE FROM dependency_edges WHERE source_file = ?;", [(fp,) for fp in all_del])
                if to_insert_rows:
                    wcur.executemany(
                        "INSERT OR REPLACE INTO symbol_index "
                        "(file_path, symbol_name, qualified_name, kind, start_line, end_line, signature, file_mtime) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
                        to_insert_rows,
                    )
                if to_insert_edges:
                    wcur.executemany(
                        "INSERT OR REPLACE INTO dependency_edges "
                        "(source_file, source_symbol, target_symbol, edge_kind, line_no, file_mtime) "
                        "VALUES (?, ?, ?, ?, ?, ?);",
                        to_insert_edges,
                    )
            wq.execute_write(_write)
        else:
            if force:
                cur.execute("DELETE FROM symbol_index;")
                cur.execute("DELETE FROM dependency_edges;")
            elif all_del:
                cur.executemany("DELETE FROM symbol_index WHERE file_path = ?;", [(fp,) for fp in all_del])
                cur.executemany("DELETE FROM dependency_edges WHERE source_file = ?;", [(fp,) for fp in all_del])
            if to_insert_rows:
                cur.executemany(
                    "INSERT OR REPLACE INTO symbol_index "
                    "(file_path, symbol_name, qualified_name, kind, start_line, end_line, signature, file_mtime) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
                    to_insert_rows,
                )
            if to_insert_edges:
                cur.executemany(
                    "INSERT OR REPLACE INTO dependency_edges "
                    "(source_file, source_symbol, target_symbol, edge_kind, line_no, file_mtime) "
                    "VALUES (?, ?, ?, ?, ?, ?);",
                    to_insert_edges,
                )
            conn.commit()

    cur.execute("SELECT COUNT(*), COUNT(DISTINCT file_path) FROM symbol_index;")
    cnt_row = cur.fetchone()
    total_symbols = int(cnt_row[0] or 0) if cnt_row else 0
    indexed_files = int(cnt_row[1] or 0) if cnt_row else 0
    cur.execute("SELECT COUNT(*) FROM dependency_edges;")
    edge_row = cur.fetchone()
    total_edges = int(edge_row[0] or 0) if edge_row else 0
    res = {
        "indexed_files": indexed_files,
        "updated_files": updated_files,
        "removed_files": len(stale_files),
        "total_symbols": total_symbols,
        "total_edges": total_edges,
    }
    if not silent:
        print(
            f"[OK] Symbol & dependency index ready: {total_symbols} symbols, {total_edges} edges across {indexed_files} files "
            f"({updated_files} files updated, {len(stale_files)} stale removed)."
        )
    return res


def _query_symbol_impact_rows(cur: sqlite3.Cursor, symbol_query: str, limit: int = 30) -> list[dict]:
    """Queries dependency_edges for callers, subclasses, and importers of symbol_query."""
    q = (symbol_query or "").strip()
    if not q:
        return []
    short_q = q.split(".")[-1]
    cur.execute(
        """
        SELECT source_file, source_symbol, target_symbol, edge_kind, line_no
        FROM dependency_edges
        WHERE target_symbol = ? COLLATE NOCASE
           OR target_symbol = ? COLLATE NOCASE
           OR target_symbol LIKE ? COLLATE NOCASE
        ORDER BY
            CASE
                WHEN LOWER(target_symbol) = LOWER(?) THEN 0
                WHEN LOWER(target_symbol) = LOWER(?) THEN 1
                ELSE 2
            END,
            CASE edge_kind
                WHEN 'calls' THEN 0
                WHEN 'inherits' THEN 1
                ELSE 2
            END,
            source_file ASC,
            line_no ASC
        LIMIT ?;
        """,
        (q, short_q, f"%.{short_q}", q, short_q, limit),
    )
    return [
        {
            "source_file": r["source_file"],
            "source_symbol": r["source_symbol"],
            "target_symbol": r["target_symbol"],
            "edge_kind": r["edge_kind"],
            "line_no": int(r["line_no"]),
        }
        for r in cur.fetchall()
    ]


def mem_symbol_impact(
    conn: sqlite3.Connection,
    query: str,
    project_root: Path = None,
    auto_index: bool = True,
    as_json: bool = False,
    limit: int = 30,
) -> list[dict]:
    """Returns relational blast-radius impact (callers, subclasses, importers) for a symbol in < 5 ms."""
    root = Path(os.path.abspath(str(project_root or find_project_root())))
    init_project_db(conn)
    if auto_index:
        mem_index_symbols(conn, project_root=root, force=False, silent=True)

    q = (query or "").strip()
    if not q:
        if as_json:
            print("[]")
        else:
            print("Error: Specify a symbol name for impact analysis.")
        return []

    cur = conn.cursor()
    impacts = _query_symbol_impact_rows(cur, q, limit=limit)
    if as_json:
        print(json.dumps(impacts, indent=2))
        return impacts

    if not impacts:
        print(f"No callers, subclasses, or imports referencing '{q}' found in {root.name}.")
        return impacts

    print(f"\n=== RELATIONAL IMPACT GRAPH: '{q}' ({len(impacts)} references) ===")
    for imp in impacts:
        print(
            f"  * {imp['source_file']}:{imp['line_no']} "
            f"[{imp['edge_kind'].upper()}] in {imp['source_symbol']} -> {imp['target_symbol']}"
        )
    print("==================================================================\n")
    return impacts


def mem_locate_symbol(
    conn: sqlite3.Connection,
    query: str,
    project_root: Path = None,
    auto_index: bool = True,
    as_json: bool = False,
    limit: int = 20,
    include_impact: bool = False,
) -> list[dict]:
    """Locates exact file paths and [start_line, end_line] offsets for a symbol name or Class.method, optionally including relational impact."""
    root = Path(os.path.abspath(str(project_root or find_project_root())))
    init_project_db(conn)
    if auto_index:
        mem_index_symbols(conn, project_root=root, force=False, silent=True)

    q = (query or "").strip()
    if not q:
        if as_json:
            print("[]")
        else:
            print("Error: Specify a symbol name to locate.")
        return []

    cur = conn.cursor()
    like_pat = f"%{q}%"
    cur.execute(
        """
        SELECT file_path, symbol_name, qualified_name, kind, start_line, end_line, signature
        FROM symbol_index
        WHERE symbol_name LIKE ? COLLATE NOCASE
           OR qualified_name LIKE ? COLLATE NOCASE
           OR file_path LIKE ? COLLATE NOCASE
        ORDER BY
            CASE
                WHEN LOWER(qualified_name) = LOWER(?) THEN 0
                WHEN LOWER(symbol_name) = LOWER(?) THEN 1
                WHEN LOWER(qualified_name) LIKE LOWER(?) THEN 2
                ELSE 3
            END,
            file_path ASC,
            start_line ASC
        LIMIT ?;
        """,
        (like_pat, like_pat, like_pat, q, q, f"{q}%", limit),
    )
    matches = []
    for r in cur.fetchall():
        s_line = int(r["start_line"])
        e_line = int(r["end_line"])
        abs_p = (root / r["file_path"]).resolve()
        item = {
            "file_path": r["file_path"],
            "abs_path": str(abs_p),
            "symbol_name": r["symbol_name"],
            "qualified_name": r["qualified_name"],
            "kind": r["kind"],
            "start_line": s_line,
            "end_line": e_line,
            "line_span": max(1, e_line - s_line + 1),
            "signature": r["signature"],
        }
        if include_impact:
            item["impact"] = _query_symbol_impact_rows(cur, r["qualified_name"], limit=15)
        matches.append(item)

    if as_json:
        print(json.dumps(matches, indent=2))
        return matches

    if not matches:
        print(f"No symbols matching '{q}' found in {root.name}.")
        return matches

    print(f"\n=== SYMBOL OFFSET LOCATOR: '{q}' ({len(matches)} matches) ===")
    for m in matches:
        print(
            f"  * {m['file_path']}:{m['start_line']}-{m['end_line']} "
            f"[{m['kind'].upper()}] {m['qualified_name']} ({m['line_span']} lines)"
        )
        print(f"      Signature: {m['signature']}")
        print(f"      Read Call: Path=\"{m['abs_path']}\", StartLine={m['start_line']}, EndLine={m['end_line']}")
        if include_impact:
            imps = m.get("impact") or []
            if imps:
                print(f"      Relational Impact ({len(imps)} callers/references):")
                for imp in imps[:8]:
                    print(f"        -> {imp['source_file']}:{imp['line_no']} [{imp['edge_kind'].upper()}] in {imp['source_symbol']}")
            else:
                print("      Relational Impact: 0 callers/references")
    print("==============================================================\n")
    return matches


def mem_pulse(
    conn: sqlite3.Connection,
    query: str,
    project_root: Path = None,
    central_db_path: Path = DB_PATH,
    as_json: bool = False,
    limit: int = 5,
) -> dict:
    """
    Single-call federated retrieval ('skillsdb pulse <query>') across:
    1. symbol_index + dependency_edges (codebase offsets & callers)
    2. project_memory_fts (facts, decisions, snapshots, and archived checkpoints)
    3. active_working_set (recent edited files & shifted line ranges)
    4. skill_sections_fts (surgical ~160-token micro-skill sections)
    Eliminates 3-4 separate CLI turns per task.
    """
    import time as _time
    t0 = _time.perf_counter()
    root = Path(os.path.abspath(str(project_root or find_project_root())))
    init_project_db(conn)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM symbol_index LIMIT 1;")
    if cur.fetchone() is None:
        mem_index_symbols(conn, project_root=root, force=False, silent=True)

    q = (query or "").strip()
    cur = conn.cursor()

    # 1. Symbol & relational impact matches
    symbols = []
    if q:
        for tok in [q] + [w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", q) if len(w) >= 3][:4]:
            like_pat = f"%{tok}%"
            cur.execute(
                """
                SELECT file_path, symbol_name, qualified_name, kind, start_line, end_line, signature
                FROM symbol_index
                WHERE symbol_name LIKE ? COLLATE NOCASE OR qualified_name LIKE ? COLLATE NOCASE
                ORDER BY CASE WHEN LOWER(symbol_name) = LOWER(?) THEN 0 ELSE 1 END, file_path ASC, start_line ASC
                LIMIT ?;
                """,
                (like_pat, like_pat, tok, limit),
            )
            for r in cur.fetchall():
                key = (r["file_path"], r["qualified_name"], int(r["start_line"]))
                if not any((s["file_path"], s["qualified_name"], s["start_line"]) == key for s in symbols):
                    symbols.append({
                        "file_path": r["file_path"],
                        "symbol_name": r["symbol_name"],
                        "qualified_name": r["qualified_name"],
                        "kind": r["kind"],
                        "start_line": int(r["start_line"]),
                        "end_line": int(r["end_line"]),
                        "signature": r["signature"],
                        "impact": _query_symbol_impact_rows(cur, r["qualified_name"], limit=5),
                    })
                if len(symbols) >= limit:
                    break
            if len(symbols) >= limit:
                break

    # 2. Project memory FTS5 (facts, decisions, snapshots, compaction_archive)
    memory_hits = []
    if q:
        words = [w.lower() for w in re.findall(r"[A-Za-z0-9_./:-]+", q) if len(w) >= 2]
        fts_q = build_safe_fts_query(words if words else [q], max_terms=12)
        if fts_q:
            try:
                cur.execute(
                    """
                    SELECT source_table, title_or_key, content
                    FROM project_memory_fts
                    WHERE project_memory_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?;
                    """,
                    (fts_q, limit),
                )
                for r in cur.fetchall():
                    memory_hits.append({
                        "source": r["source_table"],
                        "key": r["title_or_key"],
                        "content": (r["content"] or "")[:240],
                    })
            except sqlite3.Error:
                pass

    # 3. Active working set
    working_set = []
    try:
        cur.execute(
            "SELECT file_path, symbols_touched, line_ranges, edit_count, updated_at "
            "FROM active_working_set ORDER BY updated_at DESC LIMIT 5;"
        )
        for r in cur.fetchall():
            working_set.append({
                "file_path": r["file_path"],
                "symbols_touched": r["symbols_touched"],
                "line_ranges": r["line_ranges"],
                "edit_count": int(r["edit_count"] or 1),
            })
    except sqlite3.Error:
        pass

    # 4. Surgical micro-skill sections from customizations.db
    skill_slices = []
    if q and central_db_path and Path(central_db_path).exists():
        try:
            with managed_connection(central_db_path, init_schema=True) as cconn:
                import io as _io
                old_stdout = sys.stdout
                try:
                    sys.stdout = _io.StringIO()
                    skill_slices = slice_skill(cconn, q, limit=2, as_json=True)
                finally:
                    sys.stdout = old_stdout
        except Exception:
            skill_slices = []

    elapsed_ms = round((_time.perf_counter() - t0) * 1000.0, 2)
    payload = {
        "query": q,
        "workspace": str(root),
        "latency_ms": elapsed_ms,
        "symbols": symbols,
        "memory_hits": memory_hits,
        "working_set": working_set,
        "skill_slices": [
            {
                "skill_name": s["skill_name"],
                "section_title": s["section_title"],
                "token_estimate": s["token_estimate"],
                "saved_tokens": s["saved_tokens"],
                "content": s["content"],
            }
            for s in skill_slices
        ],
    }

    if as_json:
        print(json.dumps(payload, indent=2))
        return payload

    print(f"\n=== ANTIGRAVITON FEDERATED PULSE: '{q}' ({elapsed_ms} ms | Workspace: {root.name}) ===")
    if working_set:
        print("  [Active Working Set]")
        for ws in working_set:
            print(f"    * {ws['file_path']} ({ws['edit_count']}x edited): {ws['symbols_touched']}")
    if symbols:
        print(f"  [Codebase Symbols & Impact ({len(symbols)} matches)]")
        for s in symbols:
            print(f"    * {s['file_path']}:{s['start_line']}-{s['end_line']} [{s['kind'].upper()}] {s['qualified_name']}")
            for imp in (s.get("impact") or [])[:3]:
                print(f"        <- {imp['source_file']}:{imp['line_no']} [{imp['edge_kind'].upper()}] in {imp['source_symbol']}")
    if memory_hits:
        print(f"  [Episodic Memory & Compaction Archive ({len(memory_hits)} hits)]")
        for mh in memory_hits:
            print(f"    * [{mh['source'].upper()}] {mh['key']}: {mh['content']}")
    if skill_slices:
        print(f"  [Surgical Micro-Skill Slices ({len(skill_slices)} sections)]")
        for ss in skill_slices:
            print(f"    * {ss['skill_name']} > {ss['section_title']} (~{ss['token_estimate']} tok, saved ~{ss['saved_tokens']} tok)")
    print("===============================================================================\n")
    return payload


def record_working_set_file_edit(
    file_path: Path,
    args: dict = None,
    project_root: Path = None,
) -> dict:
    """
    Zero-turn PreToolUse helper for write_file / edit_file:
    1. Auto-normalizes CRLF -> LF on disk when edit_file Target uses LF so edit_file never fails with CRLF mismatch.
    2. Computes post-edit source in memory and updates symbol_index [start_line, end_line] for this single file in < 1.5 ms.
    3. Records the modified file and shifted symbol line ranges into active_working_set in .agents/memory.db.
    """
    res = {"crlf_normalized": False, "symbols_indexed": 0, "working_set_updated": False}
    try:
        fpath = Path(os.path.abspath(str(file_path)))
    except Exception:
        return res

    args_d = args if isinstance(args, dict) else {}
    disk_text = None
    if fpath.exists() and fpath.is_file():
        try:
            st = fpath.stat()
            if st.st_size <= 2_000_000:
                raw_b = fpath.read_bytes()
                if raw_b.startswith(b"\xef\xbb\xbf"):
                    raw_b = raw_b[3:]
                    fpath.write_bytes(raw_b.replace(b"\r\n", b"\n"))
                    raw_b = fpath.read_bytes()
                    res["crlf_normalized"] = True
                if b"\r\n" in raw_b:
                    lf_text = raw_b.replace(b"\r\n", b"\n").decode("utf-8", errors="replace")
                    need_lf_write = False
                    for t_key in ("Target", "TargetContent", "old_string", "target"):
                        t_val = args_d.get(t_key)
                        if isinstance(t_val, str) and t_val and "\r\n" not in t_val and "\n" in t_val and t_val in lf_text:
                            need_lf_write = True
                            break
                    if not need_lf_write and isinstance(args_d.get("ReplacementChunks"), list):
                        for ch in args_d["ReplacementChunks"]:
                            if isinstance(ch, dict):
                                tc_val = ch.get("TargetContent") or ch.get("Target")
                                if isinstance(tc_val, str) and tc_val and "\r\n" not in tc_val and "\n" in tc_val and tc_val in lf_text:
                                    need_lf_write = True
                                    break
                    if need_lf_write:
                        fpath.write_bytes(raw_b.replace(b"\r\n", b"\n"))
                        res["crlf_normalized"] = True
                    disk_text = lf_text
                else:
                    disk_text = raw_b.decode("utf-8", errors="replace")
        except Exception:
            disk_text = None

    post_text = disk_text
    for w_key in ("Content", "CodeContent", "content"):
        if isinstance(args_d.get(w_key), str):
            post_text = args_d[w_key]
            break
    if post_text is not None and disk_text is not None:
        t_str = args_d.get("Target") or args_d.get("TargetContent") or args_d.get("old_string")
        r_str = args_d.get("Replacement") or args_d.get("ReplacementContent") or args_d.get("new_string")
        if isinstance(t_str, str) and t_str and isinstance(r_str, str) and t_str in post_text:
            post_text = post_text.replace(t_str, r_str)

    probe_dir = fpath.parent if fpath.parent.exists() else Path.cwd()
    root = find_project_root(project_root or probe_dir)
    if not root or not root.exists() or _is_protected_non_workspace(root):
        return res
    db_file = get_project_db_path(root)
    if not db_file.exists():
        return res

    try:
        rel_posix = fpath.relative_to(root).as_posix()
    except ValueError:
        return res

    ext = fpath.suffix.lower()
    sym_rows = []
    # Use a future mtime tolerance (+2.0s) when simulating pre-write post_text so post-edit mtime matches
    import time as _time
    eff_mtime = round(_time.time() + 2.0, 3)
    if ext in _INDEX_CODE_EXTS and post_text:
        sym_rows = _extract_file_symbols_for_index(fpath, rel_posix, eff_mtime, content_override=post_text)

    touched_syms = []
    line_spans = []
    t_Check = (
        str(args_d.get("Replacement") or args_d.get("ReplacementContent") or args_d.get("Content") or "")
        + " "
        + str(args_d.get("Target") or args_d.get("TargetContent") or "")
    )
    for r_row in sym_rows:
        qname = r_row[2]
        sname = r_row[1]
        sl, el = r_row[4], r_row[5]
        if not t_Check.strip() or (sname in t_Check):
            touched_syms.append(f"{qname}[L{sl}-L{el}]")
            line_spans.append(f"L{sl}-L{el}")
    if not touched_syms and sym_rows:
        for r_row in sym_rows[:4]:
            touched_syms.append(f"{r_row[2]}[L{r_row[4]}-L{r_row[5]}]")
            line_spans.append(f"L{r_row[4]}-L{r_row[5]}")

    syms_summary = ", ".join(touched_syms[:5])[:160]
    ranges_summary = ", ".join(line_spans[:5])[:80]
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        with managed_connection(db_file, is_project_db=True) as conn:
            init_project_db(conn)
            cur = conn.cursor()
            if ext in _INDEX_CODE_EXTS and post_text is not None:
                edge_rows = _extract_file_edges_for_index(fpath, rel_posix, eff_mtime, content_override=post_text)
                cur.execute("DELETE FROM symbol_index WHERE file_path = ?;", (rel_posix,))
                cur.execute("DELETE FROM dependency_edges WHERE source_file = ?;", (rel_posix,))
                if sym_rows:
                    cur.executemany(
                        "INSERT OR REPLACE INTO symbol_index "
                        "(file_path, symbol_name, qualified_name, kind, start_line, end_line, signature, file_mtime) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
                        sym_rows,
                    )
                if edge_rows:
                    cur.executemany(
                        "INSERT OR REPLACE INTO dependency_edges "
                        "(source_file, source_symbol, target_symbol, edge_kind, line_no, file_mtime) "
                        "VALUES (?, ?, ?, ?, ?, ?);",
                        edge_rows,
                    )
                res["symbols_indexed"] = len(sym_rows)
                res["edges_indexed"] = len(edge_rows)
            cur.execute(
                """
                INSERT INTO active_working_set (file_path, symbols_touched, line_ranges, edit_count, updated_at)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    symbols_touched = CASE WHEN excluded.symbols_touched != '' THEN excluded.symbols_touched ELSE active_working_set.symbols_touched END,
                    line_ranges = CASE WHEN excluded.line_ranges != '' THEN excluded.line_ranges ELSE active_working_set.line_ranges END,
                    edit_count = active_working_set.edit_count + 1,
                    updated_at = excluded.updated_at;
                """,
                (rel_posix, syms_summary, ranges_summary, now_iso),
            )
            cur.execute(
                "DELETE FROM active_working_set WHERE file_path NOT IN "
                "(SELECT file_path FROM active_working_set ORDER BY updated_at DESC LIMIT 6);"
            )
            conn.commit()
            res["working_set_updated"] = True
    except Exception:
        pass
    return res


def mem_batch(
    conn: sqlite3.Connection,
    facts: list[str] = None,
    decisions: list[str] = None,
    tasks_add: list[str] = None,
    tasks_done: list[int] = None,
    tasks_in_progress: list[int] = None,
    tasks_delete: list[int] = None,
    snapshot: str = None,
    next_steps: str = None,
    files_touched: str = None,
    auto_commit: bool = False,
    locate_queries: list[str] = None,
    show_context: bool = False,
    project_root: Path = None,
    db_path: Path = None,
    as_json: bool = False,
) -> dict:
    """
    Executes multiple project memory operations (facts, decisions, task updates, snapshot,
    symbol location, and context retrieval) in a SINGLE Python process to eliminate
    multi-command PowerShell startup overhead and WaitMsBeforeAsync backgrounding.
    """
    root = Path(os.path.abspath(str(project_root or find_project_root())))
    init_project_db(conn)
    summary_res = {
        "facts_saved": [],
        "decisions_saved": [],
        "tasks_added": [],
        "tasks_completed": [],
        "tasks_in_progress": [],
        "tasks_deleted": [],
        "snapshot_saved": False,
        "located": {},
    }

    for item in (facts or []):
        if "=" in item:
            k, v = item.split("=", 1)
        elif ":" in item:
            k, v = item.split(":", 1)
        else:
            continue
        k, v = k.strip(), v.strip()
        if k:
            mem_save_fact(conn, k, v, db_path=db_path)
            summary_res["facts_saved"].append(k)

    for dec in (decisions or []):
        if "::" in dec:
            t_str, c_str = dec.split("::", 1)
        elif "=" in dec:
            t_str, c_str = dec.split("=", 1)
        else:
            t_str, c_str = dec, dec
        t_str, c_str = t_str.strip(), c_str.strip()
        if t_str and c_str:
            mem_save_decision(conn, t_str, c_str, db_path=db_path)
            summary_res["decisions_saved"].append(t_str)

    for title in (tasks_add or []):
        t_clean = str(title).strip()
        if t_clean:
            tid = mem_task_add(conn, t_clean, status="in_progress", db_path=db_path)
            summary_res["tasks_added"].append(tid)

    for tid in (tasks_in_progress or []):
        mem_task_update(conn, int(tid), status="in_progress", db_path=db_path)
        summary_res["tasks_in_progress"].append(int(tid))

    for tid in (tasks_done or []):
        mem_task_update(conn, int(tid), status="completed", db_path=db_path)
        summary_res["tasks_completed"].append(int(tid))

    for tid in (tasks_delete or []):
        if mem_task_delete(conn, int(tid), db_path=db_path):
            summary_res["tasks_deleted"].append(int(tid))

    if snapshot and str(snapshot).strip():
        mem_save_snapshot(
            conn,
            str(snapshot).strip(),
            next_steps=next_steps,
            files_touched=files_touched,
            db_path=db_path,
            auto_commit=auto_commit,
        )
        summary_res["snapshot_saved"] = True

    for lq in (locate_queries or []):
        q_clean = str(lq).strip()
        if q_clean:
            summary_res["located"][q_clean] = mem_locate_symbol(
                conn,
                q_clean,
                project_root=root,
                auto_index=True,
                as_json=False,
            )

    if show_context:
        mem_get_context(conn, project_root=root)

    if as_json:
        print(json.dumps(summary_res, indent=2))
    return summary_res


def _extract_hook_user_message(payload: dict) -> str:
    """Extracts the current user message from an Antigravity v2.28.0+ hook payload."""
    if not isinstance(payload, dict):
        return ""
    for key in ("userMessage", "userPrompt", "prompt", "message"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, dict):
            for subkey in ("userMessage", "content", "text", "prompt"):
                subval = val.get(subkey)
                if isinstance(subval, str) and subval.strip():
                    return subval.strip()
    return ""


def analyze_conversation_epochs_and_scope(
    cid: str = None,
    active_workspace: Path = None,
    db_path: Path = DB_PATH,
    brain_dir: Path = None,
    epoch_gap_hours: float = 4.0,
) -> dict:
    """
    Slices a conversation's transcript.jsonl into temporal epochs (separated by >4h gaps
    or UTC date changes) and detects foreign workspace mentions in historical epochs to
    prevent cross-project context bleeding across multi-day conversations without adding
    extra conversation_id columns to SQLite tables.
    """
    result = {
        "cid": str(cid or ""),
        "epoch_key": "epoch-1",
        "epoch_count": 0,
        "active_epoch": None,
        "epochs": [],
        "foreign_project_mentions": [],
        "max_step_index": 0,
        "step_bucket": 0,
        "recent_read_streak": 0,
        "recent_poll_streak": 0,
        "repeated_file_warning": "",
        "inferred_workspace": None,
        "steps_since_compaction": 0,
        "checkpoint_count": 0,
        "last_checkpoint_step": 0,
        "epoch_touched_files": [],
        "subagent_findings": [],
    }
    if not cid:
        return result

    b_dir = Path(brain_dir) if brain_dir else (resolve_user_home() / ".gemini" / "antigravity" / "brain")
    t_file = b_dir / str(cid) / ".system_generated" / "logs" / "transcript.jsonl"
    if not t_file.exists():
        return result

    try:
        curr_fsize = t_file.stat().st_size
    except OSError:
        return result

    target_central_db = Path(db_path) if db_path else DB_PATH
    stored_offset = 0
    stored_fsize = 0
    cached_Accumulator = None
    existing_cursor_row = None

    stored_inferred_ws = ""
    if target_central_db and target_central_db.exists():
        try:
            with managed_connection(target_central_db, init_schema=True) as cconn:
                ccur = cconn.cursor()
                ccur.execute(
                    "SELECT file_size, last_byte_offset, inferred_workspace, cached_state_json FROM transcript_cursors WHERE conversation_id = ?;",
                    (str(cid),),
                )
                existing_cursor_row = ccur.fetchone()
                if existing_cursor_row:
                    stored_fsize = int(existing_cursor_row["file_size"] or 0)
                    stored_offset = int(existing_cursor_row["last_byte_offset"] or 0)
                    stored_inferred_ws = str(existing_cursor_row["inferred_workspace"] or "")
                    raw_cjson = existing_cursor_row["cached_state_json"] or "{}"
                    loaded_c = json.loads(raw_cjson)
                    if isinstance(loaded_c, dict) and "user_steps" in loaded_c:
                        cached_Accumulator = loaded_c
                        ws_key_check = str(active_workspace or "")
                        if stored_fsize == curr_fsize and curr_fsize > 0 and loaded_c.get("active_ws_str") == ws_key_check and "cached_result" in loaded_c:
                            return loaded_c["cached_result"]
        except Exception:
            cached_Accumulator = None

    user_steps = []
    recent_lines = []
    subagent_raw_lines = []
    steps_since_compaction = 0
    checkpoint_count = 0
    last_checkpoint_step = 0
    max_step_idx = 0
    new_checkpoints = []

    if cached_Accumulator is not None and 0 < stored_offset <= curr_fsize:
        recent_lines = list(cached_Accumulator.get("recent_lines") or [])[-30:]
        subagent_raw_lines = list(cached_Accumulator.get("subagent_raw_lines") or [])[-5:]
        steps_since_compaction = int(cached_Accumulator.get("steps_since_compaction") or 0)
        checkpoint_count = int(cached_Accumulator.get("checkpoint_count") or 0)
        last_checkpoint_step = int(cached_Accumulator.get("last_checkpoint_step") or 0)
        max_step_idx = int(cached_Accumulator.get("max_step_idx") or 0)
        for ust in (cached_Accumulator.get("user_steps") or []):
            ts_s = str(ust.get("created_at") or "")
            try:
                dt_obj = datetime.fromisoformat(ts_s.replace("Z", "+00:00")) if ts_s else None
            except Exception:
                dt_obj = None
            user_steps.append({
                "step_index": int(ust.get("step_index") or 0),
                "created_at": ts_s,
                "date": str(ust.get("date") or ts_s[:10]),
                "dt": dt_obj,
                "content": str(ust.get("content") or ""),
            })
        seek_offset = stored_offset
    else:
        seek_offset = 0

    if seek_offset < curr_fsize:
        try:
            with open(t_file, "rb") as bf:
                if seek_offset > 0:
                    bf.seek(seek_offset)
                delta_text = bf.read().decode("utf-8", errors="ignore")
            for line in delta_text.splitlines():
                if not line:
                    continue
                steps_since_compaction += 1
                if '"CHECKPOINT"' in line[:320] or "<CONTEXT_SUMMARY>" in line:
                    steps_since_compaction = 0
                    checkpoint_count += 1
                    try:
                        cd = json.loads(line)
                        cs_idx = int(cd.get("step_index") or 0)
                        if cs_idx > last_checkpoint_step:
                            last_checkpoint_step = cs_idx
                        if cs_idx > max_step_idx:
                            max_step_idx = cs_idx
                        cp_content = str(cd.get("content") or "").strip()
                        cp_ts = str(cd.get("created_at") or datetime.now(timezone.utc).isoformat())
                        if cp_content:
                            new_checkpoints.append({
                                "checkpoint_num": checkpoint_count,
                                "step_index": cs_idx,
                                "summary": cp_content,
                                "created_at": cp_ts,
                            })
                    except Exception:
                        pass
                recent_lines.append(line)
                if len(recent_lines) > 30:
                    recent_lines.pop(0)
                if '"SYSTEM_MESSAGE"' in line[:320] and "sender=" in line[:640] and "/task-" not in line[:640]:
                    subagent_raw_lines.append(line)
                    if len(subagent_raw_lines) > 5:
                        subagent_raw_lines.pop(0)
                if '"USER_INPUT"' not in line[:320] and '"USER_EXPLICIT"' not in line[:320]:
                    continue
                try:
                    d = json.loads(line) if (len(line) <= 4096 or '"USER_INPUT"' in line[:320] or '"USER_EXPLICIT"' in line[:320]) else {}
                except Exception:
                    continue
                s_idx = int(d.get("step_index") or 0)
                if s_idx > max_step_idx:
                    max_step_idx = s_idx
                if d.get("type") != "USER_INPUT" and d.get("source") != "USER_EXPLICIT":
                    continue
                ts_str = str(d.get("created_at") or "").strip()
                if not ts_str:
                    continue
                try:
                    ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except Exception:
                    ts_dt = None
                user_steps.append({
                    "step_index": int(d.get("step_index") or 0),
                    "created_at": ts_str,
                    "date": ts_str[:10],
                    "dt": ts_dt,
                    "content": str(d.get("content") or ""),
                })
        except Exception:
            if seek_offset == 0:
                return result

    read_streak = 0
    poll_streak = 0
    read_file_counts = {}
    candidate_roots = {}

    bs = chr(92)
    def _record_candidate_path(raw_p: str):
        try:
            cleaned = raw_p.strip().rstrip(',.;:)]}"' + "'")
            while (bs + bs) in cleaned:
                cleaned = cleaned.replace(bs + bs, bs)
            if len(cleaned) < 4:
                return
            low_c = cleaned.lower().replace('/', bs)
            if ((bs + 'appdata' + bs) in low_c and (bs + 'temp' + bs) not in low_c) or (bs + 'windows' + bs) in low_c or (bs + 'program files') in low_c:
                return
            if (bs + '.gemini' + bs) in low_c and (bs + '.gemini' + bs + 'antigravity' + bs + 'scratch' + bs) not in low_c:
                return
            p_obj = Path(cleaned)
            probe = p_obj if p_obj.exists() else (p_obj.parent if p_obj.parent.exists() else None)
            if not probe:
                return
            c_root = find_project_root(probe if probe.is_dir() else probe.parent)
            if c_root and len(c_root.parts) >= 2 and c_root.exists() and not _is_protected_non_workspace(c_root):
                key = str(c_root)
                weight = 3 if (c_root / '.agents' / 'memory.db').exists() else (2 if (c_root / '.git').exists() else 1)
                candidate_roots[key] = candidate_roots.get(key, 0) + weight
        except Exception:
            pass

    win_path_re = re.compile(r'[A-Za-z]:[\\/][^\s"\'<>|*?]+')
    path_arg_re = re.compile(r'"Path"\s*:\s*"([^"]+)"')
    for rline in recent_lines:
        if '"step_index"' in rline:
            try:
                rd = json.loads(rline)
                rs_idx = int(rd.get('step_index') or 0)
                if rs_idx > max_step_idx:
                    max_step_idx = rs_idx
            except Exception:
                pass
        if '"read_file"' in rline:
            read_streak += 1
            for m_path in path_arg_re.findall(rline):
                fname = Path(m_path.replace(bs + bs, bs)).name
                if fname:
                    read_file_counts[fname] = read_file_counts.get(fname, 0) + 1
                _record_candidate_path(m_path)
        if '"manage_task"' in rline and '"status"' in rline:
            poll_streak += 1
        if not active_workspace and ('"PLANNER_RESPONSE"' in rline or '"USER_INPUT"' in rline or '"USER_EXPLICIT"' in rline):
            for m_win in win_path_re.findall(rline):
                _record_candidate_path(m_win)

    if not active_workspace and user_steps:
        for ust in user_steps[-3:]:
            for m_win in win_path_re.findall(ust.get('content', '')):
                _record_candidate_path(m_win)

    if not active_workspace and candidate_roots:
        best_root = max(candidate_roots.items(), key=lambda kv: kv[1])[0]
        result["inferred_workspace"] = best_root
    elif not active_workspace and stored_inferred_ws:
        result["inferred_workspace"] = stored_inferred_ws

    if read_file_counts:
        top_fname, top_cnt = max(read_file_counts.items(), key=lambda kv: kv[1])
        if top_cnt >= 3:
            result["repeated_file_warning"] = f"{top_fname} ({top_cnt}x in recent steps)"

    touched_files = []
    edit_path_re = re.compile(r'"(?:Path|TargetFile|AbsolutePath)"\s*:\s*"([^"]+)"')
    for rline in recent_lines:
        for mp in edit_path_re.findall(rline):
            fn = Path(mp.replace(bs + bs, bs)).name
            if fn and fn not in touched_files and not fn.endswith(".log"):
                touched_files.append(fn)
                if len(touched_files) >= 8:
                    break

    parsed_subagents = []
    if user_steps:
        sub_re = re.compile(r"sender=([0-9a-fA-F\-]{8,})\s+priority=\S+\s+content=(.*?)(?:</SYSTEM_MESSAGE>|$)", re.DOTALL)
        for sline in subagent_raw_lines:
            try:
                sd = json.loads(sline)
                s_idx = int(sd.get("step_index") or 0)
                if s_idx <= 2:
                    continue
                scontent = str(sd.get("content") or "")
                m_sub = sub_re.search(scontent)
                if m_sub:
                    sender_id = m_sub.group(1)[:8]
                    body = m_sub.group(2).strip()
                    body_clean = re.sub(r"```.*?```", " ", body, flags=re.DOTALL)
                    body_clean = re.sub(r"\s+", " ", body_clean).strip()
                    if body_clean:
                        parsed_subagents.append({
                            "step_index": s_idx,
                            "sender": sender_id,
                            "summary": body_clean[:280],
                        })
            except Exception:
                pass

    result["max_step_index"] = max_step_idx
    result["step_bucket"] = max_step_idx // 50
    result["recent_read_streak"] = read_streak
    result["recent_poll_streak"] = poll_streak
    result["steps_since_compaction"] = steps_since_compaction
    result["checkpoint_count"] = checkpoint_count
    result["last_checkpoint_step"] = last_checkpoint_step
    result["epoch_touched_files"] = touched_files
    result["subagent_findings"] = parsed_subagents

    if not user_steps:
        return result

    raw_epochs = []
    curr = {
        "epoch_index": 1,
        "date": user_steps[0]["date"],
        "start_ts": user_steps[0]["created_at"],
        "end_ts": user_steps[0]["created_at"],
        "step_start": user_steps[0]["step_index"],
        "step_end": user_steps[0]["step_index"],
        "turn_count": 1,
        "steps": [user_steps[0]],
    }
    prev_step = user_steps[0]
    gap_seconds = max(0.25, float(epoch_gap_hours)) * 3600.0

    for st in user_steps[1:]:
        new_epoch = False
        if st["date"] != prev_step["date"]:
            new_epoch = True
        elif st["dt"] is not None and prev_step["dt"] is not None:
            if (st["dt"] - prev_step["dt"]).total_seconds() > gap_seconds:
                new_epoch = True
        if new_epoch:
            raw_epochs.append(curr)
            curr = {
                "epoch_index": len(raw_epochs) + 1,
                "date": st["date"],
                "start_ts": st["created_at"],
                "end_ts": st["created_at"],
                "step_start": st["step_index"],
                "step_end": st["step_index"],
                "turn_count": 1,
                "steps": [st],
            }
        else:
            curr["end_ts"] = st["created_at"]
            curr["step_end"] = st["step_index"]
            curr["turn_count"] += 1
            curr["steps"].append(st)
        prev_step = st
    raw_epochs.append(curr)

    clean_epochs = [{k: v for k, v in ep.items() if k != "steps"} for ep in raw_epochs]
    result["epochs"] = clean_epochs
    result["epoch_count"] = len(clean_epochs)
    result["active_epoch"] = clean_epochs[-1]
    result["epoch_key"] = f"epoch-{len(clean_epochs)}-{clean_epochs[-1]['date']}"

    # Cross-project contamination guard across historical epochs (epochs[:-1])
    if len(raw_epochs) > 1:
        stop_tokens = {
            "antigravity", "workspace", "workspaces", "project", "projects", "python",
            "gemini", "skillsdb", "gitea", "github", "users", "appdata", "local",
            "roaming", "program", "programs", "source", "repos", "documents", "desktop",
            "scratch", "default", "master", "main", "build", "release", "enterprise",
            "portable", "service", "server", "client", "system", "config", "database",
        }
        active_norm = ""
        active_tokens = set()
        if active_workspace:
            try:
                active_norm = os.path.normcase(os.path.abspath(str(active_workspace)))
                for part in Path(active_norm).parts:
                    p_clean = part.strip("\\/:").lower()
                    if p_clean:
                        active_tokens.add(p_clean)
                        for sub in p_clean.replace("-", "_").split("_"):
                            if len(sub) >= 4:
                                active_tokens.add(sub)
            except Exception:
                pass

        learned_ws = get_learned_workspaces(db_path=db_path, prune_missing=False)
        ws_paths_iter = learned_ws.values() if isinstance(learned_ws, dict) else learned_ws
        foreign_candidates = []
        for ws_str in ws_paths_iter:
            try:
                ws_norm = os.path.normcase(os.path.abspath(ws_str))
            except Exception:
                continue
            if active_norm and ws_norm == active_norm:
                continue
            ws_p = Path(ws_str)
            ws_name = ws_p.name
            parent_name = ws_p.parent.name if ws_p.parent else ""
            label = f"{ws_name} ({parent_name})" if parent_name and parent_name.lower() not in stop_tokens else ws_name
            tokens = set()
            if len(ws_name) >= 5 and ws_name.lower() not in stop_tokens and ws_name.lower() not in active_tokens:
                tokens.add(ws_name.lower())
            for raw_part in (ws_name, parent_name):
                for sub in raw_part.replace("-", "_").split("_"):
                    s_low = sub.lower()
                    if len(s_low) >= 5 and s_low not in stop_tokens and s_low not in active_tokens:
                        tokens.add(s_low)
            if tokens:
                foreign_candidates.append((label, sorted(tokens, key=len, reverse=True)))

        seen_mentions = set()
        mentions = []
        for ep in raw_epochs[:-1]:
            for st in ep["steps"]:
                text_low = st["content"].lower()
                if not text_low:
                    continue
                for label, tok_list in foreign_candidates:
                    for tok in tok_list:
                        if tok in text_low:
                            m_key = (ep["date"], label)
                            if m_key not in seen_mentions:
                                seen_mentions.add(m_key)
                                mentions.append({
                                    "date": ep["date"],
                                    "epoch_index": ep["epoch_index"],
                                    "project": label,
                                    "matched_token": tok,
                                    "step_index": st["step_index"],
                                })
                            break
        result["foreign_project_mentions"] = mentions

    # Persist O(1) incremental byte-offset cursor into customizations.db (transcript_cursors)
    if target_central_db and (seek_offset < curr_fsize or existing_cursor_row is None):
        try:
            serializable_steps = [
                {
                    "step_index": u["step_index"],
                    "created_at": u["created_at"],
                    "date": u["date"],
                    "content": u["content"][:1000],
                }
                for u in user_steps
            ]
            acc_payload = {
                "user_steps": serializable_steps,
                "recent_lines": recent_lines[-30:],
                "subagent_raw_lines": subagent_raw_lines[-5:],
                "steps_since_compaction": steps_since_compaction,
                "checkpoint_count": checkpoint_count,
                "last_checkpoint_step": last_checkpoint_step,
                "max_step_idx": max_step_idx,
                "active_ws_str": str(active_workspace or ""),
                "cached_result": result,
            }
            now_cursor_iso = datetime.now(timezone.utc).isoformat()
            act_ep_date = result["active_epoch"]["date"] if result.get("active_epoch") else ""
            inf_ws_str = str(result.get("inferred_workspace") or "")
            with managed_connection(target_central_db, init_schema=True) as cconn:
                ccon_cur = cconn.cursor()
                ccon_cur.execute(
                    """
                    INSERT INTO transcript_cursors (
                        conversation_id, file_size, last_byte_offset, max_step_index,
                        checkpoint_count, last_checkpoint_step, epoch_count, active_epoch_date,
                        inferred_workspace, cached_state_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(conversation_id) DO UPDATE SET
                        file_size = excluded.file_size,
                        last_byte_offset = excluded.last_byte_offset,
                        max_step_index = excluded.max_step_index,
                        checkpoint_count = excluded.checkpoint_count,
                        last_checkpoint_step = excluded.last_checkpoint_step,
                        epoch_count = excluded.epoch_count,
                        active_epoch_date = excluded.active_epoch_date,
                        inferred_workspace = CASE WHEN excluded.inferred_workspace != '' THEN excluded.inferred_workspace ELSE transcript_cursors.inferred_workspace END,
                        cached_state_json = excluded.cached_state_json,
                        updated_at = excluded.updated_at;
                    """,
                    (
                        str(cid),
                        curr_fsize,
                        curr_fsize,
                        max_step_idx,
                        checkpoint_count,
                        last_checkpoint_step,
                        int(result.get("epoch_count") or 0),
                        act_ep_date,
                        inf_ws_str,
                        json.dumps(acc_payload),
                        now_cursor_iso,
                    ),
                )
        except Exception:
            pass

    # Archive any newly discovered CHECKPOINT compaction summaries into .agents/memory.db (compaction_archive + project_memory_fts)
    if new_checkpoints:
        target_ws_for_ckpt = active_workspace or (Path(result["inferred_workspace"]) if result.get("inferred_workspace") else None)
        ckpt_db_path = None
        if target_ws_for_ckpt:
            try:
                ws_root_p = find_project_root(Path(target_ws_for_ckpt))
                if ws_root_p and ws_root_p.exists() and not _is_protected_non_workspace(ws_root_p):
                    cand_db = get_project_db_path(ws_root_p)
                    if cand_db.exists():
                        ckpt_db_path = cand_db
            except Exception:
                ckpt_db_path = None
        if ckpt_db_path is None and cid and t_file.exists():
            conv_root_dir = t_file.parent.parent.parent
            if conv_root_dir.exists():
                ckpt_db_path = conv_root_dir / ".agents" / "memory.db"
        if ckpt_db_path is not None:
            try:
                with managed_connection(ckpt_db_path, is_project_db=True) as pconn:
                    init_project_db(pconn)
                    pcur = pconn.cursor()
                    for cp in new_checkpoints:
                        refs = ", ".join(dict.fromkeys(win_path_re.findall(cp["summary"])))[:300]
                        pcur.execute(
                            """
                            INSERT OR IGNORE INTO compaction_archive
                            (conversation_id, checkpoint_num, step_index, summary, extracted_refs, created_at)
                            VALUES (?, ?, ?, ?, ?, ?);
                            """,
                            (str(cid), cp["checkpoint_num"], cp["step_index"], cp["summary"], refs, cp["created_at"]),
                        )
                        if pcur.rowcount > 0:
                            fts_key = f"Checkpoint #{cp['checkpoint_num']} (step {cp['step_index']}, {cp['created_at'][:10]})"
                            pcur.execute(
                                "INSERT INTO project_memory_fts (source_table, title_or_key, content) VALUES ('checkpoint', ?, ?);",
                                (fts_key, cp["summary"][:4000]),
                            )
                    pconn.commit()
            except Exception:
                pass

    return result


def handle_pre_invocation_hook():
    """
    Handles Antigravity PreInvocation lifecycle hook with 100% autonomy:
    1. Runtime Model Concurrency Profile (Ultra vs Standard).
    2. Temporal Epoch Slicing & Cross-Project Contamination Guard.
    3. Autonomous Directory Scoping: Auto-detects domain markers (Flutter, BigQuery, Docker, etc.) and injects micro-skills.
    4. Autonomous Auto-Compaction: Prunes completed tasks and consolidates memory if threshold reached.
    5. Deterministic Task Board: Injects active tasks with states (pending, in_progress, blocked) and priorities.
    6. Episodic Decisions, Facts, and latest Milestone Snapshot with [YYYY-MM-DD] timestamps.
    """
    try:
        payload_raw = sys.stdin.read()
        payload = json.loads(payload_raw) if payload_raw.strip() else {}
    except Exception:
        payload = {}

    invocation_num = payload.get("invocationNum", 1)
    if invocation_num is not None and invocation_num > 1:
        print(json.dumps({"injectSteps": []}))
        return

    workspace_paths = payload.get("workspacePaths", [])
    proj_path = Path(workspace_paths[0]) if workspace_paths else None
    has_valid_workspace = bool(proj_path) and not _is_protected_non_workspace(proj_path)
    root_path = find_project_root(proj_path) if has_valid_workspace else None
    if root_path and _is_protected_non_workspace(root_path):
        has_valid_workspace = False
        root_path = None
    db_path = get_project_db_path(root_path) if (has_valid_workspace and root_path) else None

    cid = payload.get("conversationId")
    brain_dir_override = Path(payload["brainDir"]) if payload.get("brainDir") else None
    central_db_override = Path(payload["dbPath"]) if payload.get("dbPath") else DB_PATH
    cursor_db_path = (
        Path(payload["dbPath"])
        if payload.get("dbPath")
        else (_HOOK_STATE_FILE.with_suffix(".db") if _HOOK_STATE_FILE != _DEFAULT_HOOK_STATE_FILE else DB_PATH)
    )
    epoch_info = analyze_conversation_epochs_and_scope(
        cid=cid,
        active_workspace=root_path,
        db_path=cursor_db_path,
        brain_dir=brain_dir_override,
    )

    explicit_ws_key = str(root_path) if (has_valid_workspace and root_path) else "__no_workspace__"

    # Auto-infer active project workspace from transcript file paths when no IDE workspace is open (workspacePaths == [])
    if not has_valid_workspace and epoch_info.get("inferred_workspace"):
        inferred_p = Path(epoch_info["inferred_workspace"])
        if inferred_p.exists() and not _is_protected_non_workspace(inferred_p):
            root_path = inferred_p
            has_valid_workspace = True
            proj_path = root_path
            db_path = get_project_db_path(root_path)

    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    active_ep_date = epoch_info["active_epoch"]["date"] if epoch_info.get("active_epoch") else today_utc
    conv_brain_dir = (
        ((brain_dir_override or (resolve_user_home() / ".gemini" / "antigravity" / "brain")) / str(cid))
        if cid else None
    )
    conv_db_path = (conv_brain_dir / ".agents" / "memory.db") if (conv_brain_dir and conv_brain_dir.exists()) else None

    def _collect_project_memory_lines() -> tuple[list[str], int]:
        mem_lines = []
        facts_cnt = 0
        target_mem_db = None
        if has_valid_workspace and root_path and db_path and db_path.exists():
            target_mem_db = db_path
        elif not has_valid_workspace and conv_db_path and (
            conv_db_path.exists()
            or epoch_info.get("subagent_findings")
            or int(epoch_info.get("checkpoint_count", 0) or 0) > 0
        ):
            target_mem_db = conv_db_path
        if not target_mem_db:
            return mem_lines, facts_cnt
        try:
            with managed_connection(target_mem_db, is_project_db=True) as conn:
                init_project_db(conn)
                cursor = conn.cursor()
                now_iso_hook = datetime.now(timezone.utc).isoformat()
                # 1. Auto-persist research subagent findings into .agents/memory.db so compactions never lose them,
                #    while rotating older subagent_finding_step_* rows so at most 2 occupy the active 12-fact window.
                inserted_sf = False
                for sf in (epoch_info.get("subagent_findings") or []):
                    sf_key = f"subagent_finding_step_{sf['step_index']}"
                    cursor.execute("SELECT 1 FROM project_facts WHERE key = ?;", (sf_key,))
                    if not cursor.fetchone():
                        sf_val = f"[Subagent {sf['sender']}] {sf['summary']}"
                        cursor.execute(
                            "INSERT OR REPLACE INTO project_facts (key, value, updated_at, deprecated_at, superseded_by) VALUES (?, ?, ?, NULL, NULL);",
                            (sf_key, sf_val, now_iso_hook),
                        )
                        cursor.execute("DELETE FROM project_memory_fts WHERE source_table = 'fact' AND title_or_key = ?;", (sf_key,))
                        cursor.execute(
                            "INSERT INTO project_memory_fts (source_table, title_or_key, content) VALUES ('fact', ?, ?);",
                            (sf_key, sf_val),
                        )
                        inserted_sf = True
                if inserted_sf:
                    cursor.execute(
                        "SELECT key FROM project_facts WHERE key LIKE 'subagent_finding_step_%' AND deprecated_at IS NULL ORDER BY updated_at DESC, id DESC;"
                    )
                    active_sf_keys = [r[0] for r in cursor.fetchall()]
                    for stale_sf_key in active_sf_keys[2:]:
                        cursor.execute(
                            "UPDATE project_facts SET deprecated_at = ?, superseded_by = ? WHERE key = ?;",
                            (now_iso_hook, active_sf_keys[0], stale_sf_key),
                        )
                        cursor.execute("DELETE FROM project_memory_fts WHERE source_table = 'fact' AND title_or_key = ?;", (stale_sf_key,))
                    conn.commit()
                # 2. Auto-checkpoint active working set before auto-compaction when steps_since_compaction >= 150
                if epoch_info.get("steps_since_compaction", 0) >= 150 and epoch_info.get("epoch_touched_files"):
                    ws_key_fact = "auto_checkpoint_working_set"
                    ws_val_fact = (
                        f"Step {epoch_info.get('max_step_index', 0)} ({epoch_info.get('steps_since_compaction', 0)} uncompacted steps) active files: "
                        + ", ".join(epoch_info["epoch_touched_files"])
                    )
                    cursor.execute(
                        "INSERT OR REPLACE INTO project_facts (key, value, updated_at, deprecated_at, superseded_by) VALUES (?, ?, ?, NULL, NULL);",
                        (ws_key_fact, ws_val_fact, now_iso_hook),
                    )
                    cursor.execute("DELETE FROM project_memory_fts WHERE source_table = 'fact' AND title_or_key = ?;", (ws_key_fact,))
                    cursor.execute(
                        "INSERT INTO project_memory_fts (source_table, title_or_key, content) VALUES ('fact', ?, ?);",
                        (ws_key_fact, ws_val_fact),
                    )
                    conn.commit()
                cursor.execute("SELECT COUNT(*) FROM project_tasks WHERE status = 'completed';")
                completed_count = cursor.fetchone()[0]
                if completed_count >= 3 and has_valid_workspace and root_path:
                    mem_compact(conn, summary=f"[AUTO-COMPACTED] Pruned {completed_count} completed tasks and consolidated state.", archive_completed=True, project_root=root_path, db_path=target_mem_db, silent=True, auto_commit=False)

                if has_valid_workspace and root_path:
                    git_telem = get_workspace_git_telemetry(root_path, auto_recover=False)
                    if git_telem:
                        mem_lines.append(git_telem)

                cursor.execute("""
                SELECT id, title, status, priority, updated_at FROM project_tasks
                WHERE status IN ('pending', 'in_progress', 'blocked')
                ORDER BY 
                    CASE status WHEN 'in_progress' THEN 1 WHEN 'pending' THEN 2 ELSE 3 END,
                    CASE priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'med' THEN 3 ELSE 4 END,
                    id ASC LIMIT 5;
                """)
                open_tasks = cursor.fetchall()
                if open_tasks:
                    if mem_lines:
                        mem_lines.append("")
                    mem_lines.append("Active Task Board (Deterministic Progress):")
                    for t in open_tasks:
                        t_date = str(t["updated_at"] or "")[:10]
                        t_tag = f" [{t_date}]" if t_date else ""
                        mem_lines.append(f"  * [#{t['id']}] [{t['status'].upper():11}] ({t['priority'].upper():8}){t_tag}: {t['title']}")

                cursor.execute("SELECT id, source_role, target_role, notes FROM task_handoffs WHERE status = 'pending' ORDER BY created_at DESC LIMIT 3;")
                pending_hos = cursor.fetchall()
                if pending_hos:
                    if mem_lines:
                        mem_lines.append("")
                    mem_lines.append("Pending Agent Handoffs (Delegation Queue):")
                    for ho in pending_hos:
                        note_s = f" - '{ho['notes'][:60]}...'" if ho['notes'] else ""
                        mem_lines.append(f"  * [{ho['id']}] [{ho['source_role']}] -> [{ho['target_role']}]{note_s}")

                cursor.execute("SELECT COUNT(*) FROM project_facts WHERE deprecated_at IS NULL;")
                total_active_facts = int(cursor.fetchone()[0] or 0)
                prompt_hint = _extract_hook_user_message(payload)
                if total_active_facts > 12 and prompt_hint:
                    cursor.execute("SELECT key, value, updated_at FROM project_facts WHERE deprecated_at IS NULL ORDER BY updated_at DESC LIMIT 6;")
                    recent_f = list(cursor.fetchall())
                    seen_fkeys = {r["key"] for r in recent_f}
                    fts_q = build_safe_fts_query(expand_query_with_synonyms(prompt_hint), max_terms=10)
                    if fts_q:
                        try:
                            cursor.execute(
                                """
                                SELECT p.key, p.value, p.updated_at
                                FROM project_memory_fts f
                                JOIN project_facts p ON f.title_or_key = p.key
                                WHERE f.source_table = 'fact' AND p.deprecated_at IS NULL AND project_memory_fts MATCH ?
                                ORDER BY rank LIMIT 6;
                                """,
                                (fts_q,),
                            )
                            for mr in cursor.fetchall():
                                if mr["key"] not in seen_fkeys and len(recent_f) < 12:
                                    seen_fkeys.add(mr["key"])
                                    recent_f.append(mr)
                        except Exception:
                            pass
                    if len(recent_f) < 12:
                        cursor.execute("SELECT key, value, updated_at FROM project_facts WHERE deprecated_at IS NULL ORDER BY updated_at DESC LIMIT 12;")
                        for ar in cursor.fetchall():
                            if ar["key"] not in seen_fkeys and len(recent_f) < 12:
                                seen_fkeys.add(ar["key"])
                                recent_f.append(ar)
                    facts = recent_f
                else:
                    cursor.execute("SELECT key, value, updated_at FROM project_facts WHERE deprecated_at IS NULL ORDER BY updated_at DESC LIMIT 12;")
                    facts = cursor.fetchall()
                facts_cnt = len(facts)
                if facts:
                    if mem_lines:
                        mem_lines.append("")
                    mem_lines.append("Project Facts & Configs (.agents/memory.db):")
                    for f in facts:
                        f_date = str(f["updated_at"] or "")[:10]
                        f_tag = f" [{f_date}]" if f_date else ""
                        mem_lines.append(f"  * {f['key']}{f_tag}: {f['value']}")

                cursor.execute("SELECT title, content, updated_at FROM project_decisions WHERE is_active = 1 ORDER BY updated_at DESC LIMIT 5;")
                decs = cursor.fetchall()
                if decs:
                    if mem_lines:
                        mem_lines.append("")
                    mem_lines.append("Active Architectural Decisions:")
                    for d in decs:
                        d_date = str(d["updated_at"] or "")[:10]
                        d_tag = f" [{d_date}]" if d_date else ""
                        mem_lines.append(f"  * [{d['title']}]{d_tag}: {d['content']}")

                cursor.execute("SELECT summary, next_steps, files_touched, commit_hash, created_at FROM session_snapshots ORDER BY id DESC LIMIT 1;")
                snap = cursor.fetchone()
                if snap:
                    if mem_lines:
                        mem_lines.append("")
                    snap_date = str(snap["created_at"] or "")[:10]
                    ep_marker = " [ACTIVE EPOCH]" if snap_date and snap_date >= active_ep_date else ""
                    snap_hdr = f"Latest Project Milestone Snapshot ({snap_date}{ep_marker}):" if snap_date else "Latest Project Milestone Snapshot:"
                    mem_lines.append(snap_hdr)
                    mem_lines.append(f"  Summary: {snap['summary']}")
                    if "commit_hash" in snap.keys() and snap["commit_hash"]:
                        mem_lines.append(f"  Commit: {snap['commit_hash'][:7]}")
                    if "files_touched" in snap.keys() and snap["files_touched"]:
                        mem_lines.append(f"  Files Touched: {snap['files_touched']}")
                    if snap["next_steps"]:
                        mem_lines.append(f"  Next Steps: {snap['next_steps']}")
                else:
                    cursor.execute(
                        "SELECT checkpoint_num, step_index, summary, created_at FROM compaction_archive ORDER BY checkpoint_num DESC LIMIT 1;"
                    )
                    cp_row = cursor.fetchone()
                    if cp_row:
                        if mem_lines:
                            mem_lines.append("")
                        cp_dt = str(cp_row["created_at"] or "")[:10]
                        cp_sum = " ".join(str(cp_row["summary"] or "").split())[:220]
                        mem_lines.append(
                            f"Latest Archived Compaction Checkpoint (#{cp_row['checkpoint_num']}, step {cp_row['step_index']}, {cp_dt}):"
                        )
                        mem_lines.append(f"  Summary: {cp_sum}")
        except Exception:
            pass
        return mem_lines, facts_cnt

    # Always run fast (<0.2ms) Windows UTF-8 manifest self-healing check before hook
    # deduplication so mid-conversation Antigravity IDE updates ('Restart to Update')
    # immediately restore wiped .manifest files on the very next turn.
    utf8_healed = ensure_windows_utf8_manifests()
    _sync_builtin_fn = globals().get("sync_builtin_skills")
    if not _sync_builtin_fn:
        try:
            import importlib
            _sync_builtin_fn = getattr(importlib.import_module("skillsdb.updater.merger"), "sync_builtin_skills", None)
        except Exception:
            _sync_builtin_fn = None

    model_hint = payload.get("modelName") or payload.get("model")
    routing_dec = None
    current_routing_sig = ""
    try:
        routing_dec = None and compute_routing_decision(
            cid=cid,
            db_path=central_db_override,
            model_name_hint=model_hint,
            brain_dir=brain_dir_override,
        )
        current_routing_sig = (
            f"{routing_dec['account_tier']}:{routing_dec['effective_mode']}:{routing_dec['state']['model_name']}"
        )
    except Exception:
        routing_dec = None

    # Deduplicate per (conversationId, workspace, temporal_epoch) so Antigravity's
    # per-tool-step PreInvocation calls within the same session epoch do not bloat
    # transcript.jsonl, while a new day, >4h temporal epoch, mid-session model/effort switch,
    # or every 50-step bucket automatically re-hydrates the Turn-Economy Guardrail,
    # Multi-Pool Quota Router, and .agents/memory.db context.
    workspace_key = explicit_ws_key
    dedup_token = f"{workspace_key}@{epoch_info['epoch_key']}"
    if cid:
        try:
            curr_ckpt_cnt = int(epoch_info.get("checkpoint_count", 0) or 0)
            stored_dedup = ""
            last_ckpt_cnt = 0
            with managed_connection(cursor_db_path, init_schema=True) as hconn:
                hcur = hconn.cursor()
                hcur.execute(
                    "SELECT dedup_token, hydrated_checkpoint_count FROM transcript_cursors WHERE conversation_id = ?;",
                    (str(cid),),
                )
                hrow = hcur.fetchone()
                if hrow:
                    stored_dedup = str(hrow["dedup_token"] or "")
                    last_ckpt_cnt = int(hrow["hydrated_checkpoint_count"] or 0)

                if stored_dedup == dedup_token:
                    need_checkpoint_rehydrate = False
                    if curr_ckpt_cnt > last_ckpt_cnt:
                        need_checkpoint_rehydrate = True
                        hcur.execute(
                            "UPDATE transcript_cursors SET hydrated_checkpoint_count = ?, updated_at = ? WHERE conversation_id = ?;",
                            (curr_ckpt_cnt, datetime.now(timezone.utc).isoformat(), str(cid)),
                        )
                        hconn.commit()
                    inject_msgs = []
                    if utf8_healed:
                        if _sync_builtin_fn:
                            try:
                                _sync_builtin_fn(silent=True)
                            except Exception:
                                pass
                        inject_msgs.append("[SKILLSDB SELF-HEALING: Restored Windows UTF-8 application manifests after mid-session Antigravity IDE update]")
                    if need_checkpoint_rehydrate:
                        rehydrated_lines, _ = _collect_project_memory_lines()
                        if rehydrated_lines:
                            inject_msgs.append("=== PROJECT MEMORY CONTEXT (Auto-Hydrated Post-Compaction from .agents/memory.db) ===\n" + "\n".join(rehydrated_lines))
                    if inject_msgs:
                        print(json.dumps({
                            "injectSteps": [{
                                "ephemeralMessage": "\n".join(inject_msgs)
                            }]
                        }))
                    else:
                        print(json.dumps({"injectSteps": []}))
                    return

                now_h_iso = datetime.now(timezone.utc).isoformat()
                hcur.execute(
                    """
                    INSERT INTO transcript_cursors (
                        conversation_id, dedup_token, hydrated_checkpoint_count, checkpoint_count, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(conversation_id) DO UPDATE SET
                        dedup_token = excluded.dedup_token,
                        hydrated_checkpoint_count = excluded.hydrated_checkpoint_count,
                        updated_at = excluded.updated_at;
                    """,
                    (str(cid), dedup_token, curr_ckpt_cnt, curr_ckpt_cnt, now_h_iso),
                )
                hconn.commit()
        except Exception:
            pass

    if _sync_builtin_fn:
        try:
            _sync_builtin_fn(silent=True)
        except Exception:
            pass

    tier, tdesc = detect_model_tier(cid=cid, model_name_hint=model_hint)
    lines = []
    if utf8_healed:
        lines.append("[SKILLSDB SELF-HEALING: Restored Windows UTF-8 application manifests after Antigravity IDE update]")

    # 0. Global Turn-1 Invariant Guardrails (Strict English for maximum LLM attention weight & adherence)
    guardrail_lines = [
        "[SKILLSDB CRITICAL GUARDRAIL & INVARIANTS (TURN 1)]",
        "MANDATORY OPERATIONAL DIRECTIVES FOR ALL CONVERSATION TURNS:",
        "1. STRICT LANGUAGE SEPARATION & TYPOGRAPHY (ZERO TOLERANCE):",
        "   - Conversational Chat: Strictly informal German (\"Du\"/\"Dein\" for the user, and \"wir\"/\"unser\" for our joint work; strictly NEVER formal \"Sie\" and NEVER plural \"Ihr\"/\"Euer\"/\"Eurer\"/\"Euch\", as there are only the two of us; natural spelling with umlauts: ä, ö, ü, ß).",
        "   - Code, Artifacts & UI: 100% English (ASCII only). ALL code, comments, console outputs, logs, UI labels, buttons, dialogs, user-facing error strings, and commit messages MUST BE IN GRAMMATICALLY CORRECT ENGLISH. NEVER translate UI dialogs, labels, or buttons to German, even when conversing in German.",
        "   - Clean Typography: Strictly NO em-dashes (Unicode U+2014) or en-dashes (Unicode U+2013); always use standard ASCII hyphens (-), colons (:), or parentheses. Strictly NO emojis.",
        "   - Sentence Case: Always use natural sentence case for headings, subheadings, and bold bullet labels (never newspaper-style Title Case).",
        "   - German Orthography: Strictly NO 'Deppenbindestriche' in German compound words (e.g. write 'Abteilungskontingent' never 'Abteilungs-Kontingent', 'Kontextkompaktierung' never 'Kontext-Kompaktierung').",
        "2. AUTONOMOUS PROJECT MEMORY, GITEA SYNC & CONTINUITY:",
        "   - On any non-trivial technical task or workspace: Silently initialize/inspect project memory ('skillsdb mem-get-context' or 'skillsdb mem-init') and query domain skills ('skillsdb suggest \"<task>\"') without waiting for user prompting.",
        "   - Mandatory Fact, Offset & Task Indexing (.agents/memory.db): Immediately persist discovered file structures, class/method mappings, and line offsets via 'skillsdb mem-save-fact <key> <value> --project <dir>' and track multi-step tasks via 'skillsdb mem-task-add <title> --project <dir>', alongside architectural decisions ('skillsdb mem-save-decision') and milestone snapshots ('skillsdb mem-save-snapshot'), so context compaction never causes repeated file reads.",
        "   - Local Git & Optional Gitea Sync: SkillsDB works 100% standalone with local Git and .agents/memory.db even when Gitea is not installed. When local Gitea is configured (or requested by the user), use 'skillsdb gitea-init \"<target_dir>\"' or 'skillsdb gitea-sync -m \"<msg>\" --project \"<target_dir>\" [--releases] [--github]' (or 'skillsdb gitea-release' / 'skillsdb gitea-status') without manual token hunting. Always author commits as 'Antigravity <antigravity@local.dev>' and format Gitea/GitHub URLs as clickable Markdown links.",
        "   - Strict SemVer 2.0.0 & Keep a Changelog 1.1.0: Always version according to https://semver.org/ (MAJOR.MINOR.PATCH) and format CHANGELOG.md and releases strictly according to https://keepachangelog.com/en/1.1.0/ ('## [X.Y.Z] - YYYY-MM-DD' with only '### Added', '### Changed', '### Deprecated', '### Removed', '### Fixed', '### Security'). Never overwrite an existing release tag; increment VERSION + CHANGELOG.md and publish matching Git tags/releases (to local Git, and to Gitea/GitHub when configured).",
        "   - Gitea Repository Collision Guard: If 'skillsdb gitea-init' or 'skillsdb gitea-sync' outputs '[GITEA REPOSITORY COLLISION]', or if Git Telemetry shows '[GITEA COLLISION ALERT]' (an unlinked local workspace whose name already exists on Gitea), STOP immediately and ask the user in informal German how to proceed before pushing: reuse the existing repository (--use-existing), create it under a new name (--name <NewName>), or overwrite it (--overwrite).",
        "3. VERIFICATION INTEGRITY & PROACTIVE ERROR REPORTING:",
        "   - Never deliver untested code, broken shortcuts, or unverified event listeners. Always verify DOM and UI behavior in a real or simulated harness (e.g. jsdom / test harness) before declaring success.",
        "   - Tooling Anomaly Note (No Recurring Silent Workarounds): If an internal AntiGraviton/SkillsDB command, hook, or Gitea/GitHub sync exhibits a bug that forces a manual workaround in the background, finish the task first and add a brief note at the end of your response suggesting a root-cause fix in AntiGraviton.",
        "4. TURN-ECONOMY, PARALLEL BATCHING & DIAGNOSTIC PROTOCOL (MANDATORY):",
        "   - Anti-Salami-Slicing (Read Budget): Read files <= 800 lines completely in 1 call. For files > 800 lines, locate exact line numbers via grep/script first and batch all read_file calls in parallel within a single turn. Never slice a file across sequential 80-120 line turns.",
        "   - Subagent Firewall (invoke_subagent): Solve known 1-3 file fixes and targeted single-host / remote WMI / CIM / system admin diagnostics directly in the main context. Delegate open-ended local codebase root-cause hunting (>= 4 search steps) or scanning > 5 local files to a 'research' subagent (returning exact file paths, line numbers, and root cause) so search noise never bloats the main context.",
        "   - Cluster-Fix & Chained Pipelines: When a bug pattern is found, check the entire project for all occurrences of that bug class before rebuilding, and chain build/test/verify into one command ('build && test && verify').",
        "   - Zero-Polling & Local Indexing: Use WaitMsBeforeAsync=5000-10000 for fast commands; when a task backgrounds, NEVER poll 'manage_task status' (wait for automatic Reactive Wakeup). For codebases > 300 files requiring >= 3 analysis rounds, build a local SQLite index in scratch/ instead of repeated brute-force file scans.\n"
    ]
    lines.append("\n".join(guardrail_lines[:5] + [guardrail_lines[17]]))

    # 0.5 Temporal Epoch Slicing & Cross-Project Isolation Anchor
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ws_display = f"{root_path.name} ({root_path})" if (has_valid_workspace and root_path) else "None (Global)"
    anchor_lines = [
        "[TEMPORAL & WORKSPACE ISOLATION ANCHOR]",
        f"Current Date (UTC): {today_utc} | Active Workspace: {ws_display}",
    ]
    if epoch_info["epoch_count"] > 1 and epoch_info["active_epoch"]:
        first_ep = epoch_info["epochs"][0]
        act_ep = epoch_info["active_epoch"]
        anchor_lines.append(
            f"Conversation Timeline: {epoch_info['epoch_count']} temporal epochs ({first_ep['date']} -> {act_ep['date']}) | "
            f"Active Epoch #{act_ep['epoch_index']} started {act_ep['date']} (steps {act_ep['step_start']}..{act_ep['step_end']}, {act_ep['turn_count']} user turns)"
        )
    if epoch_info["foreign_project_mentions"]:
        summary_items = [
            f"'{m['project']}' on {m['date']} (step {m['step_index']})"
            for m in epoch_info["foreign_project_mentions"][:5]
        ]
        ws_short = root_path.name if (has_valid_workspace and root_path) else "current session"
        anchor_lines.append(
            f"[CROSS-PROJECT CONTAMINATION GUARD]: Earlier epochs in this conversation ID (and <CONTEXT_SUMMARY>) mention foreign workspace(s): "
            f"{', '.join(summary_items)}. Those prompts belong to historical sessions on earlier dates. "
            f"Do NOT attribute them to today's date ({today_utc}) or mix their architecture/facts with active workspace '{ws_short}'."
        )
    anchor_lines.append("")
    lines.append("\n".join(anchor_lines))

    if tier == TIER_ULTRA:
        lines.append("[SKILLSDB RUNTIME PROFILE: GEMINI ULTRA (16-thread high-concurrency mode)]")
        lines.append("Parallel batch fetching active: 'skillsdb get-skills <s1> <s2>' | Clusters: 'skillsdb get-cluster <domain>' | Parallel search: 'skillsdb search-multi <q1> <q2>' | Concurrent subagents: 8-16 parallel workers supported.")
    try:
        routing_dec = None and compute_routing_decision(
            cid=cid,
            db_path=central_db_override,
            model_name_hint=model_hint,
            brain_dir=brain_dir_override,
        )
        lines.append(format_hook_routing_banner(routing_dec))
    except Exception:
        pass

    # Auto-initialize project memory and local Git repository if a valid project workspace is open and has files
    if has_valid_workspace and root_path and db_path:
        if root_path.exists():
            try:
                has_files = any(f for f in root_path.iterdir() if f.name not in (".agents", ".git"))
                if has_files:
                    if not db_path.exists():
                        with managed_connection(db_path, is_project_db=True) as conn:
                            init_project_db(conn)
                    if not _is_temp_workspace(root_path) and not (root_path / ".git" / "HEAD").exists():
                        setup_workspace_git(root_path, configure_remote=False)
            except Exception:
                pass

        learn_workspace(root_path)

    # 1. Autonomous Directory Scoping & Antigravity v2.28.0+ User Prompt Pre-Routing
    cwd_hints = detect_cwd_hints(proj_path) if has_valid_workspace else []
    user_msg = _extract_hook_user_message(payload)
    if (cwd_hints or user_msg) and DB_PATH.exists():
        try:
            from ..search.synonyms import DEFAULT_SYNONYMS
            with managed_connection(DB_PATH) as gconn:
                gcursor = gconn.cursor()
                query_terms = []
                if user_msg:
                    query_terms.extend(expand_query_with_synonyms(user_msg, gconn)[:10])
                for h in cwd_hints[:8]:
                    if h not in query_terms:
                        query_terms.append(h)
                matched_skills = []
                if query_terms:
                    fts_query = build_safe_fts_query(query_terms, max_terms=12)
                    if fts_query:
                        gcursor.execute("""
                        SELECT s.name, s.description, s.category FROM skills_fts f
                        JOIN skills s ON f.rowid = s.id
                        WHERE skills_fts MATCH ?
                        ORDER BY rank LIMIT 3;
                        """, (fts_query,))
                        raw_matched = gcursor.fetchall()
                        if cwd_hints or has_valid_workspace:
                            matched_skills = raw_matched
                        else:
                            # In workspace-less conversations, filter out accidental prose matches on generic description words
                            raw_msg_tokens = set(re.findall(r"[a-zA-Z0-9äöüÄÖÜß_-]{2,}", (user_msg or "").lower()))
                            has_syn_trigger = bool(raw_msg_tokens.intersection(DEFAULT_SYNONYMS.keys()))
                            q_set = {t.lower() for t in query_terms if len(t) >= 3}
                            for ms in raw_matched:
                                sname_l = (ms["name"] or "").lower()
                                scat_l = (ms["category"] or "").lower()
                                name_parts = set(re.findall(r"[a-z0-9]{3,}", sname_l + " " + scat_l))
                                if has_syn_trigger or q_set.intersection(name_parts):
                                    matched_skills.append(ms)
            if matched_skills:
                if cwd_hints:
                    lines.append(f"[AUTONOMOUS DIRECTORY SCOPING: {', '.join(cwd_hints)}]")
                else:
                    lines.append("[AUTONOMOUS PROMPT SKILL PRE-ROUTING (Turn 1)]")
                lines.append("Domain micro-skills pre-routed for current workspace:")
                for ms in matched_skills:
                    raw_desc = ms["description"] or ""
                    desc = raw_desc[:95] + "..." if len(raw_desc) > 95 else raw_desc
                    lines.append(f"  * {ms['name']}: {desc}")
                lines.append("")
        except Exception:
            pass

    mem_lines, _ = _collect_project_memory_lines()
    if mem_lines:
        lines.append("=== PROJECT MEMORY CONTEXT (Auto-Loaded via Hook) ===")
        lines.extend(mem_lines)

    if lines:
        context_msg = "\n".join(lines).strip()
        output = {
            "injectSteps": [
                {
                    "ephemeralMessage": context_msg
                }
            ]
        }
        print(json.dumps(output))
        return

    print(json.dumps({"injectSteps": []}))


def _compute_dynamic_turn_savings(conn: sqlite3.Connection = None, db_path: Path = DB_PATH) -> dict:
    """
    Dynamically computes per-turn prompt token savings and reduction percentage from the
    live rules and skills inventory in customizations.db instead of a static constant:
      - Unconsolidated rules baseline: SUM(token_estimate) of all active rules
      - Unconsolidated plugin catalog header: metadata/description tokens of all non-builtin skills
      - Unsliced full-skill context footprint: 2x average full skill body vs. surgical micro-sections
    """
    rules_tokens = 0
    catalog_tokens = 0
    avg_skill_tokens = 0
    total_skills_tokens = 0

    def _query(c: sqlite3.Connection):
        nonlocal rules_tokens, catalog_tokens, avg_skill_tokens, total_skills_tokens
        cur = c.cursor()
        cur.execute("SELECT COALESCE(SUM(token_estimate), 0) FROM rules WHERE is_active = 1;")
        rules_tokens = int(cur.fetchone()[0] or 0)
        cur.execute(
            "SELECT COALESCE(SUM(CAST((LENGTH(COALESCE(name, '')) + LENGTH(COALESCE(description, ''))) / 4 AS INT) + 28), 0) "
            "FROM skills WHERE is_active = 1 AND plugin_name != 'builtin';"
        )
        catalog_tokens = int(cur.fetchone()[0] or 0)
        cur.execute("SELECT COALESCE(CAST(AVG(token_estimate) AS INT), 0), COALESCE(SUM(token_estimate), 0) FROM skills WHERE is_active = 1;")
        row = cur.fetchone()
        if row:
            avg_skill_tokens = int(row[0] or 0)
            total_skills_tokens = int(row[1] or 0)

    try:
        if conn is not None:
            _query(conn)
        elif db_path and Path(db_path).exists():
            with managed_connection(Path(db_path)) as tmp_conn:
                _query(tmp_conn)
    except Exception:
        pass

    raw_turn_baseline = rules_tokens + catalog_tokens + (avg_skill_tokens * 2)
    if raw_turn_baseline <= 500:
        raw_turn_baseline = 14810

    active_pointer_tokens = 380
    tokens_saved_per_turn = max(500, raw_turn_baseline - active_pointer_tokens)
    reduction_pct = round((tokens_saved_per_turn / float(raw_turn_baseline)) * 100.0, 1)

    return {
        "rules_tokens": rules_tokens,
        "catalog_tokens": catalog_tokens,
        "avg_skill_tokens": avg_skill_tokens,
        "total_skills_tokens": total_skills_tokens,
        "raw_turn_baseline": raw_turn_baseline,
        "active_pointer_tokens": active_pointer_tokens,
        "tokens_saved_per_turn": tokens_saved_per_turn,
        "reduction_pct": reduction_pct,
    }


def calculate_transcript_savings(
    target_date: str = None,
    brain_dir: Path = None,
    conn: sqlite3.Connection = None,
    db_path: Path = DB_PATH,
):
    """
    Scans Antigravity transcript files to compute both date-scoped (default: today UTC)
    and cumulative all-time prompt tokens avoided using dynamic database baselines.
    """
    b_dir = Path(brain_dir) if brain_dir else (resolve_user_home() / ".gemini" / "antigravity" / "brain")
    if not b_dir.exists():
        return None

    day_str = (target_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"))[:10]

    total_turns = 0
    total_steps = 0
    session_count = 0

    day_turns = 0
    day_steps = 0
    day_sessions = 0
    total_user_turns = 0
    day_user_turns = 0
    total_ephemeral_count = 0
    total_ephemeral_tokens = 0
    day_ephemeral_count = 0
    day_ephemeral_tokens = 0
    total_bg_tasks = 0
    day_bg_tasks = 0
    total_uncached_input = 0
    total_cache_read = 0
    total_output_tokens = 0
    total_cache_busts = 0
    day_uncached_input = 0
    day_cache_read = 0
    day_output_tokens = 0
    day_cache_busts = 0
    day_turn_events = []
    _ts_re = re.compile(r'"created_at"\s*:\s*"([^"]+)"')
    _src_re = re.compile(r'"source"\s*:\s*"([^"]+)"')
    _typ_re = re.compile(r'"type"\s*:\s*"([^"]+)"')
    _in_re = re.compile(r'"input_tokens"\s*:\s*(\d+)')
    _cr_re = re.compile(r'"cache_read_tokens"\s*:\s*(\d+)')
    _out_re = re.compile(r'"output_tokens"\s*:\s*(\d+)')

    for folder in b_dir.iterdir():
        if not folder.is_dir() or folder.name == "tempmediaStorage":
            continue
        t_file = folder / ".system_generated" / "logs" / "transcript.jsonl"
        if not t_file.exists():
            continue

        has_post_deploy = False
        has_day_activity = False
        s_turns = 0
        s_steps = 0
        s_day_turns = 0
        s_day_steps = 0
        s_user_turns = 0
        s_day_user_turns = 0
        s_eph_cnt = 0
        s_eph_tok = 0
        s_day_eph_cnt = 0
        s_day_eph_tok = 0
        s_bg_tasks = 0
        s_day_bg_tasks = 0
        s_uncached_in = 0
        s_cache_read = 0
        s_out_tok = 0
        s_cache_busts = 0
        s_day_uncached_in = 0
        s_day_cache_read = 0
        s_day_out_tok = 0
        s_day_cache_busts = 0
        pending_eph_tok = 0
        pending_bg_task = 0

        try:
            with open(t_file, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    d = json.loads(line) if (len(line) <= 4096 or '"USER_INPUT"' in line[:320] or '"USER_EXPLICIT"' in line[:320]) else {}
                    s_steps += 1
                    if len(line) <= 4096:
                        d = json.loads(line) if (len(line) <= 4096 or '"USER_INPUT"' in line[:320] or '"USER_EXPLICIT"' in line[:320]) else {}
                        ts = str(d.get("created_at") or "")
                        src = d.get("source")
                        stype = d.get("type")
                        in_tok = int(d.get("input_tokens") or 0)
                        cr_tok = int(d.get("cache_read_tokens") or 0)
                        out_tok = int(d.get("output_tokens") or 0)
                    else:
                        hdr = line[:600]
                        m_ts = _ts_re.search(hdr)
                        m_src = _src_re.search(hdr)
                        m_typ = _typ_re.search(hdr)
                        m_in = _in_re.search(hdr)
                        m_cr = _cr_re.search(hdr)
                        m_out = _out_re.search(hdr)
                        ts = m_ts.group(1) if m_ts else ""
                        src = m_src.group(1) if m_src else ""
                        stype = m_typ.group(1) if m_typ else ""
                        in_tok = int(m_in.group(1)) if m_in else 0
                        cr_tok = int(m_cr.group(1)) if m_cr else 0
                        out_tok = int(m_out.group(1)) if m_out else 0
                        d = {"created_at": ts, "source": src, "type": stype}
                    ts = str(d.get("created_at") or "")
                    if ts >= "2026-09-18T14:00:00Z":
                        has_post_deploy = True
                    is_day = ts.startswith(day_str)
                    if is_day:
                        has_day_activity = True
                        s_day_steps += 1
                    src = d.get("source")
                    stype = d.get("type")
                    if stype == "USER_INPUT" or src == "USER_EXPLICIT":
                        s_user_turns += 1
                        if is_day:
                            s_day_user_turns += 1
                    elif stype == "EPHEMERAL_MESSAGE":
                        eph_est = max(0, len(line) - 140) // 4
                        s_eph_cnt += 1
                        s_eph_tok += eph_est
                        if is_day:
                            s_day_eph_cnt += 1
                            s_day_eph_tok += eph_est
                            pending_eph_tok += eph_est
                    elif stype == "SYSTEM_MESSAGE" and "/task-" in line[:800]:
                        s_bg_tasks += 1
                        if is_day:
                            s_day_bg_tasks += 1
                            pending_bg_task += 1
                    if src == "MODEL" or stype == "PLANNER_RESPONSE":
                        if stype == "GENERIC" or (stype in ("EPHEMERAL_MESSAGE", "SYSTEM_MESSAGE", "USER_INPUT")):
                            continue
                        s_turns += 1
                        s_uncached_in += in_tok
                        s_cache_read += cr_tok
                        s_out_tok += out_tok
                        is_bust = (s_turns > 1 and cr_tok == 0 and in_tok >= 20000)
                        if is_bust:
                            s_cache_busts += 1
                        if is_day:
                            s_day_turns += 1
                            s_day_uncached_in += in_tok
                            s_day_cache_read += cr_tok
                            s_day_out_tok += out_tok
                            if is_bust:
                                s_day_cache_busts += 1
                            day_turn_events.append({
                                "uncached_in": in_tok,
                                "cache_read": cr_tok,
                                "out_tok": out_tok,
                                "eph_tok": pending_eph_tok,
                                "bg_task": pending_bg_task,
                                "cache_bust": 1 if is_bust else 0,
                            })
                            pending_eph_tok = 0
                            pending_bg_task = 0
                        continue
                    if False and (src == "MODEL" or stype == "PLANNER_RESPONSE"):
                        s_turns += 1
                        if is_day:
                            s_day_turns += 1
            if has_post_deploy:
                session_count += 1
                total_steps += s_steps
                total_turns += s_turns
                total_user_turns += s_user_turns
                total_ephemeral_count += s_eph_cnt
                total_ephemeral_tokens += s_eph_tok
                total_bg_tasks += s_bg_tasks
                total_uncached_input += s_uncached_in
                total_cache_read += s_cache_read
                total_output_tokens += s_out_tok
                total_cache_busts += s_cache_busts
            if has_day_activity:
                day_sessions += 1
                day_steps += s_day_steps
                day_turns += s_day_turns
                day_user_turns += s_day_user_turns
                day_ephemeral_count += s_day_eph_cnt
                day_ephemeral_tokens += s_day_eph_tok
                day_bg_tasks += s_day_bg_tasks
                day_uncached_input += s_day_uncached_in
                day_cache_read += s_day_cache_read
                day_output_tokens += s_day_out_tok
                day_cache_busts += s_day_cache_busts
        except Exception:
            pass

    if total_turns == 0 and day_turns == 0:
        return None

    dyn = _compute_dynamic_turn_savings(conn=conn, db_path=db_path)
    per_turn = dyn["tokens_saved_per_turn"]
    reduction_pct = dyn["reduction_pct"]

    tokens_saved = total_turns * per_turn
    dollars_saved_pro = (tokens_saved / 1_000_000) * 2.00
    dollars_saved_ultra = (tokens_saved / 1_000_000) * 7.50

    day_tokens_saved = day_turns * per_turn
    day_dollars_pro = (day_tokens_saved / 1_000_000) * 2.00
    day_dollars_ultra = (day_tokens_saved / 1_000_000) * 7.50

    return {
        "sessions": session_count,
        "model_turns": total_turns,
        "steps": total_steps,
        "tokens_saved": tokens_saved,
        "tokens_saved_per_turn": per_turn,
        "unbundled_turn_baseline": dyn["raw_turn_baseline"],
        "reduction_pct": reduction_pct,
        "dollars_saved": dollars_saved_pro,
        "dollars_saved_pro": dollars_saved_pro,
        "dollars_saved_ultra": dollars_saved_ultra,
        "target_date": day_str,
        "day_sessions": day_sessions,
        "day_model_turns": day_turns,
        "day_steps": day_steps,
        "day_tokens_saved": day_tokens_saved,
        "day_dollars_pro": day_dollars_pro,
        "day_dollars_ultra": day_dollars_ultra,
        "user_turns": total_user_turns,
        "day_user_turns": day_user_turns,
        "turns_per_user": round(total_turns / float(max(1, total_user_turns)), 2),
        "day_turns_per_user": round(day_turns / float(max(1, day_user_turns)), 2),
        "ephemeral_injections": total_ephemeral_count,
        "ephemeral_tokens": total_ephemeral_tokens,
        "day_ephemeral_injections": day_ephemeral_count,
        "day_ephemeral_tokens": day_ephemeral_tokens,
        "backgrounded_tasks": total_bg_tasks,
        "day_backgrounded_tasks": day_bg_tasks,
        "uncached_input_tokens": total_uncached_input,
        "cache_read_tokens": total_cache_read,
        "output_tokens": total_output_tokens,
        "cache_busts": total_cache_busts,
        "kv_cache_hit_pct": round((total_cache_read / float(max(1, total_uncached_input + total_cache_read))) * 100.0, 1),
        "day_uncached_input_tokens": day_uncached_input,
        "day_cache_read_tokens": day_cache_read,
        "day_output_tokens": day_output_tokens,
        "day_cache_busts": day_cache_busts,
        "day_kv_cache_hit_pct": round((day_cache_read / float(max(1, day_uncached_input + day_cache_read))) * 100.0, 1),
        "net_tokens_saved": max(0, tokens_saved - total_ephemeral_tokens - (total_bg_tasks * dyn["active_pointer_tokens"])),
        "day_net_tokens_saved": max(0, day_tokens_saved - day_ephemeral_tokens - (day_bg_tasks * dyn["active_pointer_tokens"])),
        "day_turn_events": day_turn_events,
    }


def stats(conn: sqlite3.Connection, show_savings: bool = False, target_date: str = None):
    """Outputs database inventory stats and both date-scoped and cumulative token/cost savings."""
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as count, SUM(token_estimate) as tokens FROM rules;")
    r_stat = cursor.fetchone()
    cursor.execute("SELECT COUNT(*) as count, SUM(token_estimate) as tokens FROM skills;")
    s_stat = cursor.fetchone()

    print("\n================ CUSTOMIZATIONS DATABASE STATS ================")
    print(f"Database Path: {DB_PATH}")
    print(f"Rules:         {r_stat['count']} rules (Active content: ~{r_stat['tokens']} tokens)")
    print(f"Skills:        {s_stat['count']} skills (Total content: ~{s_stat['tokens']} tokens)")

    savings = calculate_transcript_savings(target_date=target_date, conn=conn)
    if savings:
        day_lbl = savings["target_date"]
        red_pct = savings["reduction_pct"]
        print(
            f"Turn Baseline: ~{savings['unbundled_turn_baseline']:,} raw tokens/turn -> "
            f"~{savings['tokens_saved_per_turn']:,} tokens saved/turn (-{red_pct:.1f}%)"
        )
        print(f"\n--- Measured Activity for {day_lbl} (Strictly Date-Scoped) ---")
        print(f"Active Sessions ({day_lbl}): {savings['day_sessions']} sessions")
        print(f"Model Turns ({day_lbl}):     {savings['day_model_turns']:,} turns ({savings['day_steps']:,} steps)")
        print(f"Prompt Bloat Avoided:         ~{savings['day_tokens_saved']:,} tokens (-{red_pct:.1f}% per turn)")
        print(f"Cost Saved (Gemini Pro):      ~${savings['day_dollars_pro']:.2f} USD")
        print(f"Cost Saved (Gemini Ultra):    ~${savings['day_dollars_ultra']:.2f} USD")
        print(f"\n--- Empirical Overhead & Change-Impact Audit ({day_lbl}) ---")
        print(
            f"User Requests -> Model Turns: {savings['day_user_turns']:,} prompts -> {savings['day_model_turns']:,} turns "
            f"({savings['day_turns_per_user']:.2f}x turn multiplier | {savings['day_backgrounded_tasks']} backgrounded task wakeups)"
        )
        print(
            f"Real LLM Token Telemetry:     {savings['day_uncached_input_tokens']:,} uncached in | "
            f"{savings['day_cache_read_tokens']:,} cache read ({savings['day_kv_cache_hit_pct']:.1f}% KV hit, {savings['day_cache_busts']} cache busts) | "
            f"{savings['day_output_tokens']:,} out"
        )
        print(
            f"Hook Control-Plane Overhead:  {savings['day_ephemeral_injections']} ephemeral injections "
            f"(~{savings['day_ephemeral_tokens']:,} tokens injected) -> Net Saved: ~{savings['day_net_tokens_saved']:,} tokens"
        )
        evts = savings.get("day_turn_events") or []
        if len(evts) >= 6:
            split_idx = max(3, len(evts) - min(8, len(evts) // 2))
            prev_w = evts[:split_idx]
            curr_w = evts[split_idx:]
            prev_eph_avg = sum(e["eph_tok"] for e in prev_w) / float(len(prev_w))
            curr_eph_avg = sum(e["eph_tok"] for e in curr_w) / float(len(curr_w))
            prev_bg_rate = (sum(e["bg_task"] for e in prev_w) / float(len(prev_w))) * 100.0
            curr_bg_rate = (sum(e["bg_task"] for e in curr_w) / float(len(curr_w))) * 100.0
            prev_in_avg = sum(e["uncached_in"] for e in prev_w) / float(len(prev_w))
            curr_in_avg = sum(e["uncached_in"] for e in curr_w) / float(len(curr_w))
            verdict = "GENUINE OPTIMIZATION (Lower Load)" if (curr_eph_avg <= prev_eph_avg and curr_in_avg <= prev_in_avg) else "LOAD WARNING (Check Hook / Backgrounding Overhead)"
            print(
                f"Window Delta (Prior {len(prev_w)} vs Last {len(curr_w)} turns): "
                f"Hook {prev_eph_avg:.0f} -> {curr_eph_avg:.0f} tok/turn | "
                f"Uncached In {prev_in_avg:,.0f} -> {curr_in_avg:,.0f} tok/turn | "
                f"Bg-Rate {prev_bg_rate:.0f}% -> {curr_bg_rate:.0f}% [{verdict}]"
            )

        print("\n--- Cumulative Token Savings (Since SkillsDB Deployment) ---")
        print(f"Tracked Sessions:     {savings['sessions']} active sessions")
        print(f"Total Model Turns:    {savings['model_turns']:,} turns ({savings['steps']:,} steps)")
        print(f"Prompt Bloat Avoided: ~{savings['tokens_saved']:,} tokens (-{red_pct:.1f}% per turn)")
        print(f"Cost Saved (Gemini Pro):   ~${savings['dollars_saved_pro']:.2f} USD")
        print(f"Cost Saved (Gemini Ultra): ~${savings['dollars_saved_ultra']:.2f} USD")
        minfo = detect_model_and_pool()
        acc_tier = minfo.get("account_tier", "ultra")
        if acc_tier == "ultra" or minfo.get("model_tier") == TIER_ULTRA:
            print(
                f"Active Profile:       ULTRA [Model: {minfo['model_name']} | Pool: {minfo['pool']}] "
                f"(~${savings['dollars_saved_ultra']:.2f} USD saved / 16-thread high concurrency)"
            )
        else:
            print(
                f"Active Profile:       PRO [Model: {minfo['model_name']} | Pool: {minfo['pool']}] "
                f"(~${savings['dollars_saved_pro']:.2f} USD saved / 8-thread Golden-Ratio protection)"
            )
    try:
        l24 = get_ledger_24h_summary(db_path=DB_PATH)
        if l24["saved_agy_tokens_total"] > 0 or l24["subagent_24h_rewrites"] > 0:
            print("\n--- Multi-Pool Quota Router & External Offload Savings ---")
            print(f"24h External Offloads: CLI-OAuth={l24['cli_24h_requests']}, AIStudio-API={l24['aistudio_24h_requests']}, Web-Handovers={l24['web_24h_handovers']}, Subagent-Rewrites={l24['subagent_24h_rewrites']}")
            print(f"Offload Quota Saved:   ~{l24['saved_agy_tokens_total']:,} AGY tokens ({l24['saved_agy_turns_total']} turns avoided)")
    except Exception:
        pass
    print(f"[OK] Version: v{__version__} (Up to date)")
    print("================================================================\n")
