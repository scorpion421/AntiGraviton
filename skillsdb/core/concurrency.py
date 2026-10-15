"""
High-concurrency parallel retrieval engine, domain clusters, and benchmarking.
"""

import time
import json
import re
import concurrent.futures
from pathlib import Path
from ..config import DB_PATH, SKILL_CLUSTERS, estimate_tokens
from .db import get_connection, get_thread_connection


def extract_skill_sections(content: str) -> list[dict]:
    """Parses markdown headers into sections with estimated token counts."""
    lines = content.splitlines()
    sections = []
    current_title = "Overview"
    current_level = 1
    current_lines = []

    in_fm = False
    body_lines = []
    for idx, line in enumerate(lines):
        if idx == 0 and line.strip() == "---":
            in_fm = True
            continue
        if in_fm:
            if line.strip() == "---":
                in_fm = False
            continue
        body_lines.append(line)

    for line in body_lines:
        header_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if header_match:
            if current_lines:
                sec_text = "\n".join(current_lines).strip()
                if sec_text:
                    sections.append({
                        "level": current_level,
                        "title": current_title,
                        "content": sec_text,
                        "tokens": estimate_tokens(sec_text)
                    })
            current_level = len(header_match.group(1))
            current_title = header_match.group(2).strip()
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        sec_text = "\n".join(current_lines).strip()
        if sec_text:
            sections.append({
                "level": current_level,
                "title": current_title,
                "content": sec_text,
                "tokens": estimate_tokens(sec_text)
            })

    return sections


def fetch_single_skill(skill_name: str, db_path: Path = DB_PATH, summary: bool = False, section: str = None) -> dict:
    """Thread-safe single skill reader for parallel executors."""
    conn = get_thread_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
    SELECT name, plugin_name, category, description, content, token_estimate
    FROM skills
    WHERE name = ? OR name LIKE ?
    LIMIT 1;
    """, (skill_name, f"%{skill_name}%"))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return {"name": skill_name, "found": False, "content": None, "token_estimate": 0, "tokens": 0}

    content = row["content"]
    tokens = row["token_estimate"]
    if summary:
        sections = extract_skill_sections(content)
        sec_list = "\n".join([f"  * {s['title']} (~{s['tokens']} tokens)" for s in sections])
        content = f"# Skill Summary: {row['name']} ({row['plugin_name']})\nDescription: {row['description']}\n\nSections:\n{sec_list}"
        tokens = estimate_tokens(content)
    elif section:
        sections = extract_skill_sections(content)
        matched = next((s for s in sections if section.lower() in s["title"].lower()), None)
        if matched:
            content = f"# Skill: {row['name']} > {matched['title']}\n\n{matched['content']}"
            tokens = matched["tokens"]

    return {
        "name": row["name"],
        "plugin_name": row["plugin_name"],
        "category": row["category"],
        "description": row["description"],
        "token_estimate": tokens,
        "tokens": tokens,
        "found": True,
        "content": content
    }


def get_skills_parallel(skill_names: list[str], max_workers: int = 16, as_json: bool = False, summary: bool = False, db_path: Path = DB_PATH) -> list[dict]:
    """Retrieves multiple skills concurrently using ThreadPoolExecutor under SQLite WAL mode."""
    unique_names = list(dict.fromkeys(skill_names))
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(unique_names) or 1, max_workers)) as executor:
        future_to_name = {executor.submit(fetch_single_skill, name, db_path, summary): name for name in unique_names}
        for future in concurrent.futures.as_completed(future_to_name):
            try:
                results.append(future.result())
            except Exception as e:
                name = future_to_name[future]
                results.append({"name": name, "found": False, "error": str(e), "token_estimate": 0, "tokens": 0, "content": None})

    name_order = {name: i for i, name in enumerate(unique_names)}
    results.sort(key=lambda r: name_order.get(r.get("name"), 999))

    if as_json:
        print(json.dumps(results, indent=2))
        return results

    total_tokens = sum(r.get("token_estimate", 0) for r in results if r.get("found"))
    print(f"\n=== Batch Skills Retrieval ({len(results)} skills, ~{total_tokens} tokens total) ===\n")
    for r in results:
        if r.get("found"):
            print(f"--- Skill: {r['name']} (Plugin: {r['plugin_name']}, ~{r['token_estimate']} tokens) ---")
            print(r["content"])
            print("-" * 60 + "\n")
        else:
            print(f"--- Skill: {r['name']} [NOT FOUND] ---\n")
    return results


def search_single_query(query: str, db_path: Path = DB_PATH, limit: int = 4) -> list[dict]:
    """Thread-safe FTS5 search query."""
    conn = get_thread_connection(db_path)
    cursor = conn.cursor()
    words = re.findall(r"[a-zA-Z0-9_-]{3,}", query)
    if not words:
        conn.close()
        return []
    fts_query = " OR ".join(words[:10])
    cursor.execute("""
    SELECT s.name, s.plugin_name, s.category, s.description, s.token_estimate, rank
    FROM skills_fts f
    JOIN skills s ON f.rowid = s.id
    WHERE skills_fts MATCH ?
    ORDER BY rank
    LIMIT ?;
    """, (fts_query, limit))
    rows = cursor.fetchall()
    conn.close()
    return [{"name": r["name"], "plugin": r["plugin_name"], "category": r["category"], "description": r["description"], "tokens": r["token_estimate"]} for r in rows]


def search_multi_parallel(queries: list[str], max_workers: int = 16, limit_per_query: int = 4, as_json: bool = False, db_path: Path = DB_PATH) -> dict:
    """Executes multiple full-text search queries concurrently."""
    all_results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(queries) or 1, max_workers)) as executor:
        future_to_q = {executor.submit(search_single_query, q, db_path, limit_per_query): q for q in queries}
        for future in concurrent.futures.as_completed(future_to_q):
            q = future_to_q[future]
            try:
                all_results[q] = future.result()
            except Exception as e:
                all_results[q] = [{"error": str(e)}]

    if as_json:
        print(json.dumps(all_results, indent=2))
        return all_results

    print(f"\n=== Multi-Query Parallel FTS5 Search ({len(queries)} queries) ===\n")
    for q in queries:
        matches = all_results.get(q, [])
        print(f"Results for query: '{q}' ({len(matches)} matches)")
        if matches:
            for m in matches:
                if "error" in m:
                    print(f"  * Error: {m['error']}")
                else:
                    print(f"  * [{m['plugin']}] {m['name']} (~{m['tokens']} tokens) - {m['description'][:80]}...")
        else:
            print("  * No matches.")
        print()
    return all_results


def get_cluster(domain: str, max_workers: int = 16, as_json: bool = False, summary: bool = False, db_path: Path = DB_PATH):
    """Retrieves all pre-indexed skills belonging to a domain cluster concurrently in a single call."""
    dom_clean = domain.strip().lower()
    if dom_clean not in SKILL_CLUSTERS:
        print(f"Error: Unknown skill cluster '{domain}'. Available clusters: {', '.join(SKILL_CLUSTERS.keys())}")
        return

    cluster_skills = SKILL_CLUSTERS[dom_clean]
    print(f"[Ultra Concurrency] Prefetching complete '{dom_clean}' cluster ({len(cluster_skills)} skills: {', '.join(cluster_skills)})...\n")
    get_skills_parallel(cluster_skills, max_workers=max_workers, as_json=as_json, summary=summary, db_path=db_path)


def benchmark_concurrency(num_queries: int = 20, max_workers: int = 16, db_path: Path = DB_PATH):
    """Compares sequential vs. parallel multi-query throughput on customizations.db."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM skills ORDER BY id LIMIT ?;", (num_queries,))
    names = [r[0] for r in cursor.fetchall()]
    conn.close()

    if not names:
        print("Not enough skills in database for benchmark.")
        return

    print(f"\n================ SKILLSDB CONCURRENCY BENCHMARK ================")
    print(f"Dataset: {len(names)} skills | Max ThreadPool Workers: {max_workers}")

    # Sequential benchmark
    start_seq = time.perf_counter()
    for name in names:
        fetch_single_skill(name, db_path, summary=True)
    seq_duration = time.perf_counter() - start_seq
    print(f"Sequential Execution Time: {seq_duration * 1000:.2f} ms ({len(names) / (seq_duration or 0.001):.1f} queries/sec)")

    # Parallel benchmark
    start_par = time.perf_counter()
    unique_names = list(dict.fromkeys(names))
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(unique_names) or 1, max_workers)) as executor:
        list(executor.map(lambda n: fetch_single_skill(n, db_path, summary=True), unique_names))
    par_duration = time.perf_counter() - start_par
    print(f"Parallel Execution Time:   {par_duration * 1000:.2f} ms ({len(names) / (par_duration or 0.001):.1f} queries/sec)")

    speedup = seq_duration / par_duration if par_duration > 0 else 1.0
    print(f"High-Concurrency Speedup:  {speedup:.2f}x faster with ThreadPoolExecutor")
    print("=================================================================\n")
