"""
Full-Text Search (FTS5) queries, skill suggestions, rules retrieval, and exports.
"""

import json
import sqlite3
from pathlib import Path
from ..config import TIER_ULTRA, estimate_tokens
from ..core.detector import detect_model_tier
from ..core.concurrency import extract_skill_sections
from .synonyms import expand_query_with_synonyms


def detect_cwd_hints(cwd_path: Path = None) -> list[str]:
    """Detects domain hints from current working directory path and project markers."""
    cwd = (cwd_path or Path.cwd()).resolve()
    hints = []
    path_str = str(cwd).lower()
    parts = [p.lower() for p in cwd.parts]
    if "flutter" in path_str or "lib" in parts or (cwd / "pubspec.yaml").exists():
        hints.extend(["flutter", "dart", "widget"])
    if "data" in path_str or "sql" in parts or "bigquery" in path_str or (cwd / "dbt_project.yml").exists():
        hints.extend(["bigquery", "sql", "data", "dbt"])
    if "docker" in path_str or (cwd / "Dockerfile").exists() or (cwd / "compose.yaml").exists():
        hints.extend(["docker", "container"])
    if "android" in path_str or (cwd / "build.gradle").exists() or (cwd / "build.gradle.kts").exists():
        hints.extend(["android", "kotlin", "gradle"])
    if "web" in path_str or "frontend" in parts or (cwd / "package.json").exists():
        hints.extend(["web", "ui", "frontend"])
    if "firebase" in path_str or (cwd / "firebase.json").exists():
        hints.extend(["firebase", "firestore"])
    if "security" in path_str or "admin" in path_str:
        hints.extend(["admin", "security"])
    return list(dict.fromkeys(hints))


def suggest_skills(conn: sqlite3.Connection, task_description: str, limit: int = 3, as_json: bool = False, cwd_path: Path = None):
    """Auto-recommends relevant workflow skills using multilingual synonym-expanded FTS5 ranking and directory scoping."""
    words = expand_query_with_synonyms(task_description, conn)

    # Blend in directory scoping hints
    cwd_hints = detect_cwd_hints(cwd_path)
    if cwd_hints:
        for h in cwd_hints:
            if h not in words:
                words.append(h)

    if not words:
        if as_json:
            print("[]")
        else:
            print("No searchable keywords identified.")
        return

    fts_query = " OR ".join(words[:16])

    cursor = conn.cursor()
    cursor.execute("""
    SELECT s.name, s.plugin_name, s.category, s.description, s.token_estimate, rank
    FROM skills_fts f
    JOIN skills s ON f.rowid = s.id
    WHERE skills_fts MATCH ?
    ORDER BY rank
    LIMIT ?;
    """, (fts_query, limit))
    matches = cursor.fetchall()

    if as_json:
        result = [dict(m) for m in matches]
        print(json.dumps(result, indent=2))
        return

    scope_banner = f" [Directory Scope: {', '.join(cwd_hints)}]" if cwd_hints else ""
    print(f"\n--- Recommended Skills for: '{task_description}'{scope_banner} ---\n")
    if not matches:
        print("No specific workflow skills matched this task.")
        return

    for idx, m in enumerate(matches, 1):
        desc = (m["description"][:110] + "...") if m["description"] and len(m["description"]) > 110 else m["description"]
        print(f"{idx}. [{m['plugin_name']}] {m['name']} (~{m['token_estimate']} tokens)")
        print(f"   Desc: {desc}")
        print(f"   Command to view: skillsdb get-skill {m['name']}\n")

    tier, _ = detect_model_tier()
    if tier == TIER_ULTRA and len(matches) > 1:
        skill_names_str = " ".join([m['name'] for m in matches])
        print(f"  [Ultra Concurrency Tip] Retrieve all recommended skills simultaneously in 1 call:")
        print(f"  skillsdb get-skills {skill_names_str}\n")


def search(conn: sqlite3.Connection, query: str):
    """Searches rules and skills via FTS5 with synonym expansion."""
    words = expand_query_with_synonyms(query, conn)
    fts_query = " OR ".join(words[:12]) if words else query

    cursor = conn.cursor()
    print(f"\n--- Search results for '{query}' in Customizations Database ---\n")

    cursor.execute("""
    SELECT r.key, r.name, r.category, r.summary, r.token_estimate
    FROM rules_fts f
    JOIN rules r ON f.rowid = r.id
    WHERE rules_fts MATCH ?
    ORDER BY rank
    LIMIT 5;
    """, (fts_query,))
    rules = cursor.fetchall()
    if rules:
        print(f"Matching Rules ({len(rules)}):")
        for r in rules:
            print(f"  * [{r['category']}] {r['name']} (Key: {r['key']}, ~{r['token_estimate']} tokens)")
            print(f"    Summary: {r['summary']}")
    else:
        print("No matching rules.")

    cursor.execute("""
    SELECT s.name, s.plugin_name, s.description, s.token_estimate
    FROM skills_fts f
    JOIN skills s ON f.rowid = s.id
    WHERE skills_fts MATCH ?
    ORDER BY rank
    LIMIT 10;
    """, (fts_query,))
    skills = cursor.fetchall()
    if skills:
        print(f"\nMatching Skills ({len(skills)}):")
        for s in skills:
            desc = (s['description'][:110] + "...") if s['description'] and len(s['description']) > 110 else s['description']
            print(f"  * [{s['plugin_name']}] {s['name']} (~{s['token_estimate']} tokens)")
            print(f"    Desc: {desc}")
    else:
        print("\nNo matching skills.")


def get_skill(conn: sqlite3.Connection, name: str, summary: bool = False, section: str = None):
    """Retrieves a skill, skill summary outline, or targeted micro-skill section."""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM skills WHERE name = ? OR name LIKE ? LIMIT 1;", (name, f"%{name}%"))
    skill = cursor.fetchone()
    if not skill:
        print(f"Skill '{name}' not found in database.")
        return

    content_text = skill['content']
    sections = extract_skill_sections(content_text)

    # Mode 1: Summary / Micro-Skill Table of Contents
    if summary:
        print(f"=== Skill: {skill['name']} (Plugin: {skill['plugin_name']}) ===")
        print(f"Category: {skill['category']} | Total Content: ~{skill['token_estimate']} tokens")
        if skill['description']:
            print(f"Description: {skill['description']}\n")
        print("Micro-Skill Sections (fetch via: skillsdb get-skill " + skill['name'] + " --section \"<Title>\"):")
        for idx, s in enumerate(sections, 1):
            prefix = "#" * s["level"]
            print(f"  {idx:>2}. [{prefix}] {s['title']} (~{s['tokens']} tokens)")
        print("\nHint: Loading specific sections saves up to 85% context tokens.")
        return

    # Mode 2: Specific Micro-Skill Section
    if section:
        target_norm = section.lower().strip()
        matched = None
        for s in sections:
            if target_norm in s["title"].lower():
                matched = s
                break

        if matched:
            print(f"=== Skill: {skill['name']} > Section: {matched['title']} ===")
            print(f"Token estimate: ~{matched['tokens']} tokens (saved ~{skill['token_estimate'] - matched['tokens']} tokens)\n")
            print(matched['content'])
            return
        else:
            print(f"Section matching '{section}' not found in skill '{skill['name']}'.")
            print("Available sections:")
            for s in sections:
                print(f"  * {s['title']}")
            return

    # Mode 3: Full Skill (standard)
    print(f"=== Skill: {skill['name']} (Plugin: {skill['plugin_name']}) ===")
    print(f"Category: {skill['category']} | Active: {bool(skill['is_active'])}")
    print(f"Token estimate: ~{skill['token_estimate']} tokens\n")
    print(skill['content'])


def get_active_rules(conn: sqlite3.Connection):
    """Outputs all active global system rules."""
    cursor = conn.cursor()
    cursor.execute("""
    SELECT key, name, category, summary, content
    FROM rules
    WHERE is_active = 1
    ORDER BY id ASC;
    """)
    rows = cursor.fetchall()
    if not rows:
        print("No active rules found.")
        return

    print("========================= ACTIVE GLOBAL RULES =========================")
    for r in rows:
        print(f"\n--- [{r['category'].upper()}] {r['name']} (Key: {r['key']}) ---")
        print(r['content'].strip())
    print("\n=======================================================================")


def get_rule(conn: sqlite3.Connection, key: str):
    """Outputs a specific rule by key or name."""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM rules WHERE key = ? OR name LIKE ? LIMIT 1;", (key, f"%{key}%"))
    rule = cursor.fetchone()
    if not rule:
        print(f"Rule '{key}' not found in database.")
        return
    print(f"=== Rule: {rule['name']} ({rule['key']}) ===")
    print(f"Category: {rule['category']} | Scope: {rule['scope']} | Active: {bool(rule['is_active'])}")
    print(f"Token estimate: ~{rule['token_estimate']} tokens\n")
    print(rule['content'])


def list_all(conn: sqlite3.Connection):
    """Lists summary of all rules and skills grouped by plugin."""
    cursor = conn.cursor()
    print("\n--- ALL RULES IN DATABASE ---")
    cursor.execute("SELECT key, name, category, is_active, token_estimate FROM rules ORDER BY category, key;")
    for r in cursor.fetchall():
        status = "ACTIVE" if r["is_active"] else "INACTIVE"
        print(f"  [{status}] {r['category']:<15} {r['key']:<25} ({r['name']})")

    print("\n--- ALL SKILLS IN DATABASE (GROUPED BY PLUGIN) ---")
    cursor.execute("SELECT plugin_name, COUNT(*) as cnt, SUM(token_estimate) as tokens FROM skills GROUP BY plugin_name ORDER BY plugin_name;")
    for p in cursor.fetchall():
        print(f"  Plugin: {p['plugin_name']:<28} {p['cnt']:>3} skills (~{p['tokens']:>6} tokens)")


def export_skill(conn: sqlite3.Connection, skill_name: str, target_dir_str: str):
    """Exports a skill markdown file into a project workspace."""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM skills WHERE name = ? LIMIT 1;", (skill_name,))
    skill = cursor.fetchone()
    if not skill:
        print(f"Error: Skill '{skill_name}' not found.")
        return

    target_dir = Path(target_dir_str) / ".agents" / "skills" / skill["name"]
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / "SKILL.md"
    target_file.write_text(skill["content"], encoding="utf-8")
    print(f"Skill '{skill['name']}' exported successfully to:\n{target_file}")
