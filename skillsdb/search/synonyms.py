"""
Zero-dependency multilingual synonym synapses and query expansion engine.
Maps German concepts, German compound stems, and technical aliases to English skill keywords.
"""

import re
import sqlite3

# Default curated cross-lingual synonyms dictionary
DEFAULT_SYNONYMS = {
    # Localization & Languages
    "mehrsprachig": "localization internationalization intl arb translation",
    "mehrsprachigkeit": "localization internationalization intl arb translation",
    "übersetzung": "localization internationalization intl translation",
    "uebersetzung": "localization internationalization intl translation",
    "sprache": "localization internationalization language",
    "i18n": "localization internationalization intl",
    "l10n": "localization internationalization intl",

    # State & Architecture
    "zustand": "state management bloc architecture viewModel",
    "zustandsverwaltung": "state management bloc reactive architecture",
    "reaktiv": "reactive state management stream listener",
    "architektur": "architecture layered repository viewModel",

    # Layout & UI
    "ueberlauf": "overflow layout renderflex unbounded",
    "überlauf": "overflow layout renderflex unbounded",
    "umbruch": "overflow layout constraints responsive",
    "darstellung": "widget preview layout responsive",
    "oberflaeche": "ui widget layout screen responsive",
    "oberfläche": "ui widget layout screen responsive",

    # Admin & Security
    "berechtigung": "admin elevation credentials security",
    "erhöhung": "admin elevation dpapi privilege",
    "erhoehung": "admin elevation dpapi privilege",
    "rechte": "admin elevation security credentials",
    "kennwort": "credentials password secret dpapi",

    # Testing & Quality
    "einheitentest": "unit test mock assertions checks",
    "integrationstest": "integration test driver e2e mcp",
    "komponententest": "widget test tester interaction",
    "testen": "unit test widget test integration test",
    "abdeckung": "coverage lcov test report",

    # Cloud, Data & Pipelines
    "datenbank": "database bigquery sql firestore dataform",
    "abfrage": "query sql bigquery firestore optimization",
    "datenpipeline": "pipeline orchestration dataflow dbt dataform",
    "bereitstellung": "deployment cloud hosting hosting basics",
    "anmeldung": "auth authentication login credentials",
    "authentifizierung": "auth authentication security firebase",

    # Performance & Optimization
    "leistung": "performance optimization profiling memory leak",
    "leistungsoptimierung": "performance optimization profiling benchmark",
    "speicher": "memory leak debugging allocation gc",
    "speicherleck": "memory leak debugging allocation profiler",
    "geschwindigkeit": "performance optimization latency lcp",
}


def init_synonyms(conn: sqlite3.Connection):
    """Populates synonym_synapses table in database if empty."""
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM synonym_synapses;")
    count = cursor.fetchone()[0]
    if count == 0:
        cursor.execute("DELETE FROM synonym_fts;")
        for term, syns in DEFAULT_SYNONYMS.items():
            cursor.execute("""
            INSERT OR REPLACE INTO synonym_synapses (term, synonyms, language, category)
            VALUES (?, ?, 'de', 'general');
            """, (term.lower(), syns.lower()))
            row_id = cursor.lastrowid
            cursor.execute("""
            INSERT INTO synonym_fts (rowid, term, synonyms)
            VALUES (?, ?, ?);
            """, (row_id, term.lower(), syns.lower()))
        conn.commit()


def expand_query_with_synonyms(query: str, conn: sqlite3.Connection = None) -> list[str]:
    """
    Extracts search terms from query and expands with cross-lingual synonyms.
    Returns list of terms ready for FTS5 'OR' conjunction.
    """
    raw_words = re.findall(r"[a-zA-Z0-9äöüÄÖÜß_-]{3,}", query.lower())
    if not raw_words:
        return []

    expanded = list(raw_words)

    # 1. In-memory dictionary lookup (fast path)
    for word in raw_words:
        if word in DEFAULT_SYNONYMS:
            syn_words = DEFAULT_SYNONYMS[word].split()
            expanded.extend(syn_words)

    # 2. Database lookup (dynamic custom synonyms if table exists)
    if conn:
        try:
            cursor = conn.cursor()
            for word in raw_words:
                cursor.execute("SELECT synonyms FROM synonym_synapses WHERE term = ? LIMIT 1;", (word,))
                row = cursor.fetchone()
                if row and row[0]:
                    expanded.extend(row[0].split())
        except Exception:
            pass

    return list(dict.fromkeys(expanded))
