"""
Full-Text Search (FTS5) queries, skill suggestions, rules retrieval, and exports.
"""

import json
import re
import sqlite3
from pathlib import Path
from ..config import TIER_ULTRA, estimate_tokens
from ..core.detector import detect_model_and_pool, detect_model_tier, tier_to_display_label
from ..core.concurrency import extract_skill_sections
from .synonyms import expand_query_with_synonyms, build_safe_fts_query


_SYSTEM_PATH_NOISE = frozenset({
    "appdata", "local", "roaming", "temp", "tmp", "users", "home", "var", "opt", "programdata", "windows",
})


def _escape_sql_like(term: str) -> str:
    return (term or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def detect_cwd_hints(cwd_path: Path = None) -> list[str]:
    """Detects domain hints from the active workspace leaf/parent directory names and explicit project marker files."""
    cwd = (cwd_path or Path.cwd()).resolve()
    hints = []
    check_dirs = [cwd, *list(cwd.parents)[:3]]
    dir_tokens: set[str] = set()
    for idx, d in enumerate(check_dirs[:3]):
        dname = d.name.lower()
        if not dname or dname in _SYSTEM_PATH_NOISE:
            continue
        toks = set(re.findall(r"[a-z0-9]+", dname))
        if toks & _SYSTEM_PATH_NOISE:
            continue
        if idx <= 2:
            dir_tokens.update(toks)

    def _has_marker(*names: str) -> bool:
        return any((d / n).exists() for d in check_dirs for n in names)

    if "flutter" in dir_tokens or "dart" in dir_tokens or _has_marker("pubspec.yaml"):
        hints.extend(["flutter", "dart", "widget"])
    if (
        "bigquery" in dir_tokens
        or "sql" in dir_tokens
        or "dbt" in dir_tokens
        or "analytics" in dir_tokens
        or "data" in dir_tokens
        or _has_marker("dbt_project.yml")
    ):
        hints.extend(["bigquery", "sql", "data", "dbt"])
    if "docker" in dir_tokens or _has_marker("Dockerfile", "compose.yaml", "docker-compose.yml"):
        hints.extend(["docker", "container"])
    if "android" in dir_tokens or "kotlin" in dir_tokens or _has_marker("build.gradle", "build.gradle.kts"):
        hints.extend(["android", "kotlin", "gradle"])
    if "web" in dir_tokens or "frontend" in dir_tokens or _has_marker("package.json", "tsconfig.json"):
        hints.extend(["web", "ui", "frontend"])
    if "firebase" in dir_tokens or "firestore" in dir_tokens or _has_marker("firebase.json", ".firebaserc"):
        hints.extend(["firebase", "firestore"])
    if "security" in dir_tokens or "admin" in dir_tokens:
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

    fts_query = build_safe_fts_query(words, max_terms=16)
    if not fts_query:
        if as_json:
            print("[]")
        else:
            print("No searchable keywords identified.")
        return

    cursor = conn.cursor()
    cursor.execute("""
    SELECT s.name, s.plugin_name, s.category, s.description, s.token_estimate, rank
    FROM skills_fts f
    JOIN skills s ON f.rowid = s.id
    WHERE skills_fts MATCH ? AND s.is_active = 1
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

    minfo = detect_model_and_pool()
    tier = minfo.get("model_tier", "")
    acc_tier = minfo.get("account_tier", "ultra")
    if (acc_tier == "ultra" or tier == TIER_ULTRA) and len(matches) > 1:
        skill_names_str = " ".join([m['name'] for m in matches])
        print("  [Ultra Concurrency Tip] Retrieve all recommended skills simultaneously in 1 call (16 threads):")
        print(f"  skillsdb get-skills {skill_names_str}\n")
    elif acc_tier == "pro" and len(matches) > 1:
        skill_names_str = " ".join([m['name'] for m in matches])
        print("  [Pro Concurrency & Quota Tip] Batch-fetch summaries (8 threads) or surgical micro-sections to save up to 85% context tokens:")
        print(f"  skillsdb get-skills {skill_names_str} --summary\n")
    elif matches:
        lbl = tier_to_display_label(tier)
        print(f"  [{lbl} Quota Conservation Tip] Fetch surgical micro-skill sections to save up to 85% context tokens:")
        print(f"  skillsdb get-skill {matches[0]['name']} --summary\n")


def search(conn: sqlite3.Connection, query: str):
    """Searches rules and skills via FTS5 with synonym expansion."""
    words = expand_query_with_synonyms(query, conn)
    fts_query = build_safe_fts_query(words if words else [query], max_terms=12)

    print(f"\n--- Search results for '{query}' in Customizations Database ---\n")
    if not fts_query:
        print("No matching rules.")
        print("\nNo matching skills.")
        return

    cursor = conn.cursor()

    cursor.execute("""
    SELECT r.key, r.name, r.category, r.summary, r.token_estimate
    FROM rules_fts f
    JOIN rules r ON f.rowid = r.id
    WHERE rules_fts MATCH ? AND r.is_active = 1
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
    WHERE skills_fts MATCH ? AND s.is_active = 1
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
    if not name or not str(name).strip("_% "):
        print(f"Skill '{name}' not found in database.")
        return
    cursor = conn.cursor()
    esc = _escape_sql_like(name)
    cursor.execute(
        "SELECT * FROM skills WHERE is_active = 1 AND (name = ? OR name LIKE ? ESCAPE '\\') ORDER BY CASE WHEN name = ? THEN 0 ELSE 1 END LIMIT 1;",
        (name, f"%{esc}%", name),
    )
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
    if not key or not str(key).strip("_% "):
        print(f"Rule '{key}' not found in database.")
        return
    cursor = conn.cursor()
    esc = _escape_sql_like(key)
    cursor.execute(
        "SELECT * FROM rules WHERE is_active = 1 AND (key = ? OR name LIKE ? ESCAPE '\\') ORDER BY CASE WHEN key = ? THEN 0 ELSE 1 END LIMIT 1;",
        (key, f"%{esc}%", key),
    )
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


def rebuild_skill_sections_index(conn: sqlite3.Connection, force: bool = False) -> int:
    """
    Decomposes all active skills in customizations.db into atomic ## / ### sections
    stored in skill_sections and indexed in skill_sections_fts for BM25 surgical retrieval.
    """
    cur = conn.cursor()
    if not force:
        try:
            cur.execute("SELECT COUNT(*) FROM skill_sections;")
            existing_cnt = int(cur.fetchone()[0] or 0)
            if existing_cnt > 0:
                return existing_cnt
        except sqlite3.Error:
            pass

    cur.execute("SELECT name, plugin_name, category, content FROM skills WHERE is_active = 1;")
    skills_rows = cur.fetchall()
    cur.execute("DELETE FROM skill_sections;")

    to_insert = []
    for srow in skills_rows:
        sname = srow["name"]
        pname = srow["plugin_name"]
        cat = srow["category"]
        sections = extract_skill_sections(srow["content"] or "")
        seen_titles: dict[str, int] = {}
        for sec in sections:
            raw_title = (sec.get("title") or "Overview").strip()
            cnt = seen_titles.get(raw_title, 0) + 1
            seen_titles[raw_title] = cnt
            unique_title = raw_title if cnt == 1 else f"{raw_title} ({cnt})"
            sec_content = sec.get("content") or ""
            sec_tokens = int(sec.get("tokens") or estimate_tokens(sec_content))
            lvl = int(sec.get("level") or 2)
            to_insert.append((sname, pname, cat, unique_title, lvl, sec_content, sec_tokens))

    if to_insert:
        cur.executemany(
            """
            INSERT OR REPLACE INTO skill_sections
            (skill_name, plugin_name, category, section_title, heading_level, content, token_estimate)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            to_insert,
        )
    cur.execute("INSERT INTO skill_sections_fts(skill_sections_fts) VALUES ('rebuild');")
    conn.commit()
    return len(to_insert)


def slice_skill(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 3,
    as_json: bool = False,
) -> list[dict]:
    """
    Retrieves the top-K matching atomic micro-skill sections via BM25 over skill_sections_fts
    (~160 tokens/section vs. ~3,479 tokens/full skill = -95.4% token reduction).
    """
    rebuild_skill_sections_index(conn, force=False)
    words = expand_query_with_synonyms(query, conn)
    fts_query = build_safe_fts_query(words if words else [query], max_terms=16)
    if not fts_query:
        if as_json:
            print("[]")
        else:
            print("No searchable keywords identified for slice-skill.")
        return []

    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            ss.skill_name,
            ss.plugin_name,
            ss.category,
            ss.section_title,
            ss.heading_level,
            ss.content,
            ss.token_estimate,
            COALESCE(s.token_estimate, ss.token_estimate) AS full_skill_tokens,
            f.rank
        FROM skill_sections_fts f
        JOIN skill_sections ss ON f.rowid = ss.id
        LEFT JOIN skills s ON s.name = ss.skill_name
        WHERE skill_sections_fts MATCH ?
        ORDER BY f.rank
        LIMIT ?;
        """,
        (fts_query, limit),
    )
    rows = cur.fetchall()
    results = []
    for r in rows:
        sec_tok = int(r["token_estimate"] or 0)
        full_tok = int(r["full_skill_tokens"] or sec_tok)
        saved_tok = max(0, full_tok - sec_tok)
        results.append({
            "skill_name": r["skill_name"],
            "plugin_name": r["plugin_name"],
            "category": r["category"],
            "section_title": r["section_title"],
            "heading_level": int(r["heading_level"] or 2),
            "token_estimate": sec_tok,
            "full_skill_tokens": full_tok,
            "saved_tokens": saved_tok,
            "content": r["content"],
        })

    if as_json:
        print(json.dumps(results, indent=2))
        return results

    if not results:
        print(f"No micro-skill sections matched '{query}'.")
        return results

    total_sec_tok = sum(x["token_estimate"] for x in results)
    total_full_tok = sum({x["skill_name"]: x["full_skill_tokens"] for x in results}.values())
    saved_pct = round((1.0 - (total_sec_tok / max(1, total_full_tok))) * 100.0, 1) if total_full_tok > total_sec_tok else 0.0
    print(
        f"\n=== SURGICAL MICRO-SKILL SLICE: '{query}' "
        f"({len(results)} sections, ~{total_sec_tok} tokens vs. ~{total_full_tok} full-skill tokens, -{saved_pct}%) ==="
    )
    for idx, item in enumerate(results, 1):
        print(
            f"\n--- [{idx}] {item['skill_name']} > {item['section_title']} "
            f"(~{item['token_estimate']} tokens | saved ~{item['saved_tokens']} tokens) ---"
        )
        print(item["content"])
    print("\n================================================================================\n")
    return results
