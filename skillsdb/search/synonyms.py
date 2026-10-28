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

    # Git, Gitea & Repository Synchronization
    "gitea": "gitea git repository sync init push backup customizations",
    "hochladen": "gitea git push sync upload repository backup",
    "sichern": "gitea git backup sync snapshot repository",
    "versionierung": "gitea git repository commit semver changelog",
    "backup": "gitea git backup sync snapshot repository",

    # Antigravity 2.0, Automations, Plugins & Generative UI
    "automatisierung": "automation schedule cron sidecar timer recurring",
    "zeitplan": "automation schedule cron sidecar timer",
    "erweiterung": "plugin customization skill hook mcp sidecar",
    "widget": "generative_ui html embed tailwind card chart",
}


_FTS5_RESERVED_WORDS = frozenset({"and", "or", "not", "near"})


def init_synonyms(conn: sqlite3.Connection):
    """Populates or updates synonym_synapses table in database."""
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM synonym_synapses;")
    count = cursor.fetchone()[0]
    if count < len(DEFAULT_SYNONYMS):
        for term, syns in DEFAULT_SYNONYMS.items():
            cursor.execute("""
            INSERT INTO synonym_synapses (term, synonyms, language, category)
            VALUES (?, ?, 'de', 'general')
            ON CONFLICT(term) DO UPDATE SET synonyms = excluded.synonyms;
            """, (term.lower(), syns.lower()))
        cursor.execute("INSERT INTO synonym_fts(synonym_fts) VALUES ('rebuild');")
        conn.commit()


def build_safe_fts_query(terms: list[str], max_terms: int = 12) -> str:
    """Formats a list of search terms into a safe SQLite FTS5 OR expression with quoted tokens."""
    safe_tokens = []
    for t in terms:
        if not t:
            continue
        for sub in re.findall(r"[a-zA-Z0-9äöüÄÖÜß_]{2,}", str(t).lower()):
            if sub not in _FTS5_RESERVED_WORDS and sub not in safe_tokens:
                safe_tokens.append(sub)
                if len(safe_tokens) >= max_terms:
                    break
        if len(safe_tokens) >= max_terms:
            break
    if not safe_tokens:
        return ""
    return " OR ".join(f'"{tok}"' for tok in safe_tokens)


def expand_query_with_synonyms(query: str, conn: sqlite3.Connection = None) -> list[str]:
    """
    Extracts search terms from query and expands with cross-lingual synonyms.
    Returns list of sanitized alphanumeric terms ready for FTS5 'OR' conjunction.
    """
    if not query or not isinstance(query, str):
        return []
    raw_words = [
        w for w in re.findall(r"[a-zA-Z0-9äöüÄÖÜß_]{3,}", query.lower())
        if w not in _FTS5_RESERVED_WORDS
    ]
    if not raw_words:
        return []

    expanded = list(raw_words)

    # 1. In-memory dictionary lookup (fast path)
    for word in raw_words:
        if word in DEFAULT_SYNONYMS:
            for sw in re.findall(r"[a-zA-Z0-9äöüÄÖÜß_]{2,}", DEFAULT_SYNONYMS[word].lower()):
                if sw not in _FTS5_RESERVED_WORDS:
                    expanded.append(sw)

    # 2. Database lookup (dynamic custom synonyms if table exists)
    if conn:
        try:
            cursor = conn.cursor()
            for word in raw_words:
                cursor.execute("SELECT synonyms FROM synonym_synapses WHERE term = ? LIMIT 1;", (word,))
                row = cursor.fetchone()
                if row and row[0]:
                    for sw in re.findall(r"[a-zA-Z0-9äöüÄÖÜß_]{2,}", str(row[0]).lower()):
                        if sw not in _FTS5_RESERVED_WORDS:
                            expanded.append(sw)
        except Exception:
            pass

    return list(dict.fromkeys(expanded))
