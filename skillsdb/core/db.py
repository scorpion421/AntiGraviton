"""
Database connection pooling, pragmas, and schema initialization for SQLite.
"""

import sqlite3
from pathlib import Path
from ..config import DB_PATH


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Primary connection configured with SQLite WAL mode and busy timeout."""
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def get_thread_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Thread-safe SQLite connection configured for concurrent readers under WAL mode."""
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def init_db(conn: sqlite3.Connection):
    """Initializes tables and FTS5 virtual tables in customizations.db."""
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
