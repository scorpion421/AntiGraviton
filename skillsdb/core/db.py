"""
Enterprise SQLite connection management, WAL/mmap pragma tuning, deterministic
context managers, lock-retry execution, and zero-lock schema verification.
"""

import os
import time
import random
import sqlite3
import contextlib
from pathlib import Path
from typing import Iterator, Optional, Set, Dict, Any
from ..config import DB_PATH

# Process-local cache of verified database file paths to avoid redundant DDL checks
_VERIFIED_CENTRAL_DBS: Set[str] = set()
_NETWORK_ROOT_CACHE: Dict[str, bool] = {}

_REQUIRED_CENTRAL_TABLES = frozenset({
    "rules",
    "skills",
    "runtime_config",
    "synonym_synapses",
    "rules_fts",
    "skills_fts",
    "synonym_fts",
})


def _is_network_path(db_path: Path) -> bool:
    """
    Detects whether db_path resides on a network share (UNC path or mapped SMB/CIFS drive).
    On network shares, SQLite shared-memory files (*.db-shm) and mmap can fail or corrupt
    when SMB oplocks/leases break, so journal_mode=TRUNCATE and mmap_size=0 are used instead.
    """
    try:
        raw = os.path.abspath(str(db_path))
        if raw.startswith("\\\\?\\UNC\\"):
            return True
        if raw.startswith("\\\\?\\"):
            raw = raw[4:]
        if raw.startswith("\\\\"):
            return True
        drive, _ = os.path.splitdrive(raw)
        if not drive:
            return False
        root = drive.upper() + "\\"
        if root in _NETWORK_ROOT_CACHE:
            return _NETWORK_ROOT_CACHE[root]
        is_net = False
        if os.name == "nt":
            import ctypes
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(root)
            # DRIVE_REMOTE == 4
            is_net = (drive_type == 4)
        _NETWORK_ROOT_CACHE[root] = is_net
        return is_net
    except Exception:
        return False


def _apply_pragmas(conn: sqlite3.Connection, is_project_db: bool = False, is_network: bool = False) -> None:
    """Applies enterprise SQLite pragmas for concurrent WAL readers on local drives or SMB-safe TRUNCATE mode on network shares."""
    try:
        conn.execute("PRAGMA busy_timeout = 30000;")
    except sqlite3.Error:
        pass
    try:
        if is_project_db:
            conn.execute("PRAGMA auto_vacuum = INCREMENTAL;")
    except sqlite3.Error:
        pass
    try:
        if is_network:
            conn.execute("PRAGMA journal_mode = TRUNCATE;")
        else:
            conn.execute("PRAGMA journal_mode = WAL;")
    except sqlite3.Error:
        pass
    try:
        if is_network:
            conn.execute("PRAGMA synchronous = FULL;")
            conn.execute("PRAGMA temp_store = MEMORY;")
            conn.execute("PRAGMA mmap_size = 0;")
        else:
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA temp_store = MEMORY;")
            conn.execute("PRAGMA mmap_size = 67108864;")
        conn.execute("PRAGMA foreign_keys = ON;")
    except sqlite3.Error:
        pass


def get_connection(db_path: Path = DB_PATH, is_project_db: bool = False) -> sqlite3.Connection:
    """Primary SQLite connection configured with WAL/mmap on local drives or SMB-safe TRUNCATE mode on network drives."""
    target = Path(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=30.0)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn, is_project_db=is_project_db, is_network=_is_network_path(target))
    return conn


def get_thread_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Thread-safe SQLite connection configured for concurrent readers under WAL mode."""
    return get_connection(db_path=db_path, is_project_db=False)


@contextlib.contextmanager
def managed_connection(
    db_path: Path = DB_PATH,
    init_schema: bool = False,
    is_project_db: bool = False,
) -> Iterator[sqlite3.Connection]:
    """
    Deterministic SQLite context manager that commits pending transactions on success,
    rolls back on exception, and always closes the underlying file handle on exit.
    """
    conn = get_connection(db_path=db_path, is_project_db=is_project_db)
    try:
        if init_schema and not is_project_db:
            init_db(conn)
        yield conn
        if conn.in_transaction:
            conn.commit()
    except Exception:
        try:
            if conn.in_transaction:
                conn.rollback()
        except sqlite3.Error:
            pass
        raise
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def execute_with_retry(
    target: Any,
    sql: str,
    params: tuple = (),
    max_retries: int = 5,
    base_delay: float = 0.04,
):
    """Executes a SQL statement with exponential backoff + jitter on transient SQLite lock errors."""
    last_err: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            return target.execute(sql, params)
        except sqlite3.OperationalError as err:
            msg = str(err).lower()
            if ("locked" in msg or "busy" in msg) and attempt < max_retries:
                last_err = err
                sleep_sec = base_delay * (2 ** attempt) + random.uniform(0.0, 0.02)
                time.sleep(sleep_sec)
                continue
            raise
    if last_err:
        raise last_err


def _central_schema_ready(conn: sqlite3.Connection) -> bool:
    """Checks whether all required central tables exist without acquiring a write lock."""
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name IN "
            "('rules', 'skills', 'runtime_config', 'synonym_synapses', 'rules_fts', 'skills_fts', 'synonym_fts');"
        )
        found = {row[0] for row in cur.fetchall()}
        return _REQUIRED_CENTRAL_TABLES.issubset(found)
    except sqlite3.Error:
        return False


def init_db(conn: sqlite3.Connection) -> None:
    """
    Ensures tables and FTS5 virtual tables exist in customizations.db.
    Performs a fast read-only check first so read invocations acquire zero write locks.
    """
    db_file_path = None
    try:
        row = conn.execute("PRAGMA database_list;").fetchone()
        if row and row[2]:
            db_file_path = os.path.abspath(row[2])
            fsize = os.path.getsize(db_file_path) if os.path.exists(db_file_path) else 0
            if fsize > 0 and f"{db_file_path}:{fsize}" in _VERIFIED_CENTRAL_DBS:
                return
    except sqlite3.Error:
        pass

    if _central_schema_ready(conn):
        if db_file_path and os.path.exists(db_file_path):
            fsize = os.path.getsize(db_file_path)
            if fsize > 0:
                _VERIFIED_CENTRAL_DBS.add(f"{db_file_path}:{fsize}")
        return

    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        scope TEXT NOT NULL,
        is_active INTEGER DEFAULT 1,
        summary TEXT,
        content TEXT NOT NULL,
        token_estimate INTEGER,
        updated_at TEXT
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS skills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        plugin_name TEXT NOT NULL,
        category TEXT NOT NULL,
        is_active INTEGER DEFAULT 1,
        description TEXT,
        content TEXT NOT NULL,
        token_estimate INTEGER,
        updated_at TEXT
    );
    """)

    cursor.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS rules_fts USING fts5(
        key,
        name,
        category,
        summary,
        content,
        content='rules',
        content_rowid='id'
    );
    """)

    cursor.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS skills_fts USING fts5(
        name,
        plugin_name,
        category,
        description,
        content,
        content='skills',
        content_rowid='id'
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS runtime_config (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS synonym_synapses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        term TEXT UNIQUE NOT NULL,
        synonyms TEXT NOT NULL,
        language TEXT DEFAULT 'de',
        category TEXT DEFAULT 'general'
    );
    """)

    cursor.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS synonym_fts USING fts5(
        term,
        synonyms,
        content='synonym_synapses',
        content_rowid='id'
    );
    """)

    conn.commit()
    if db_file_path and os.path.exists(db_file_path):
        try:
            fsize = os.path.getsize(db_file_path)
            if fsize > 0:
                _VERIFIED_CENTRAL_DBS.add(f"{db_file_path}:{fsize}")
        except OSError:
            pass


def checkpoint_wal(db_path: Path = DB_PATH, mode: str = "PASSIVE") -> bool:
    """Safely checkpoints the WAL file for the specified SQLite database."""
    if not db_path or not Path(db_path).exists():
        return False
    mode_clean = mode.upper() if mode.upper() in ("PASSIVE", "FULL", "RESTART", "TRUNCATE") else "PASSIVE"
    try:
        with managed_connection(db_path) as conn:
            conn.execute(f"PRAGMA wal_checkpoint({mode_clean});")
        return True
    except sqlite3.Error:
        return False
