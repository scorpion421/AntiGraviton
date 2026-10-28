"""
Non-destructive differential database merger, GitHub update checking, and safe update orchestrator.
"""

import sys
import re
import shutil
import sqlite3
import subprocess
import urllib.request
import json
from datetime import datetime, timezone
from pathlib import Path
from ..config import (
    __version__,
    __suite_version__,
    DB_DIR,
    DB_PATH,
    GLOBAL_PLUGINS_PATH,
    ARCHIVED_PLUGINS_PATH,
    BUILTIN_SKILLS_PATH,
    DEFAULT_REMOTE_SHARE,
    estimate_tokens,
)
from ..core.db import get_connection, init_db, managed_connection
from ..core.detector import detect_model_tier
from ..core.concurrency import fetch_single_skill
from ..memory.project_memory import find_project_root, _is_protected_non_workspace
from ..memory.gitea_sync import get_gitea_config, get_gitea_service_status, check_gitea_health, get_learned_workspaces
from ..platform.windows_utf8 import check_windows_utf8, ensure_windows_utf8_manifests


def find_repo_root() -> Path:
    """Finds the root of the skillsdb git repository if executed from within it."""
    curr = Path(__file__).resolve()
    for parent in [curr] + list(curr.parents):
        if (parent / ".git").exists() and (parent / "database" / "customizations.db").exists():
            return parent
    return None


def _extract_skillsdb_version_from_release(tag: str, name: str, body: str) -> str:
    """Extracts the SkillsDB engine version (e.g. 3.7.2) from an AntiGraviton release title or body."""
    combined = f"{name or ''}\n{body or ''}"
    m = re.search(r"SkillsDB\s+[(`'\"]*v?(\d+\.\d+\.\d+)", combined, re.IGNORECASE)
    if m:
        return m.group(1)
    m2 = re.search(r"\bv(3\.\d+\.\d+)\b", combined)
    if m2:
        return m2.group(1)
    return tag


def get_latest_release_info() -> dict:
    """Queries GitHub API for the latest release of AntiGraviton / SkillsDB."""
    urls = [
        "https://api.github.com/repos/scorpion421/AntiGraviton/releases/latest",
        "https://api.github.com/repos/scorpion421/skillsdb/releases/latest",
    ]
    for url in urls:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": f"SkillsDB-Updater/{__version__}", "Accept": "application/vnd.github.v3+json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8"))
                    raw_tag = data.get("tag_name", "").lstrip("v")
                    rel_name = data.get("name", "")
                    rel_body = data.get("body", "")
                    engine_ver = _extract_skillsdb_version_from_release(raw_tag, rel_name, rel_body)
                    return {
                        "tag": raw_tag,
                        "skillsdb_version": engine_ver,
                        "name": rel_name,
                        "published_at": data.get("published_at", "")[:10],
                        "body": rel_body,
                    }
        except Exception:
            continue
    return None


def check_update(quiet: bool = False) -> dict:
    """Compares local version with latest GitHub release (comparing both SkillsDB engine and AntiGraviton suite versions)."""
    info = get_latest_release_info()
    if not info or not info["tag"]:
        if not quiet:
            print("[INFO] Could not check for updates (GitHub offline or rate-limited).")
        return None

    suite_tag = info["tag"]
    latest_engine = info.get("skillsdb_version") or suite_tag
    local_suite = globals().get("__suite_version__", "1.4.4")
    repo_root = find_repo_root()
    if repo_root and (repo_root / "VERSION").exists():
        try:
            v_txt = (repo_root / "VERSION").read_text(encoding="utf-8-sig").strip().lstrip("v")
            if v_txt:
                local_suite = v_txt
        except Exception:
            pass

    has_update = False
    try:
        def parse_ver(v):
            return [int(x) for x in re.sub(r"[^0-9.]", "", v).split(".") if x]

        p_latest_engine = parse_ver(latest_engine)
        p_local_engine = parse_ver(__version__)
        p_suite_tag = parse_ver(suite_tag)
        p_local_suite = parse_ver(local_suite)

        if p_latest_engine and p_latest_engine[0] >= 3:
            has_update = p_latest_engine > p_local_engine
        if not has_update and p_suite_tag and p_local_suite and p_suite_tag[0] == p_local_suite[0]:
            has_update = p_suite_tag > p_local_suite
    except Exception:
        has_update = (latest_engine != __version__) and (suite_tag != local_suite)

    if not quiet:
        print("\n================ SKILLSDB UPDATE CHECK ================")
        print(f"Current Version: AntiGraviton v{local_suite} (SkillsDB v{__version__})")
        print(f"Latest Version:  AntiGraviton v{suite_tag} (SkillsDB v{latest_engine}, Published: {info['published_at']})")
        if has_update:
            print(f"\n[UPDATE AVAILABLE] A newer version of AntiGraviton / SkillsDB is available!")
            print(f"Release Name: {info['name']}")
            print("\nRun 'skillsdb update' to safely upgrade.")
        else:
            print("\n[OK] SkillsDB is up to date!")
        print("=======================================================\n")

    return {
        "has_update": has_update,
        "current": __version__,
        "latest": latest_engine,
        "suite_current": local_suite,
        "suite_latest": suite_tag,
        "name": info["name"]
    }


def merge_upstream_database(local_db_path: Path, upstream_db_path: Path) -> dict:
    """Non-destructive differential merge preserving all user-learned rules and runtime configuration."""
    with managed_connection(local_db_path) as conn:
        init_db(conn)
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM rules WHERE category = 'learned';")
        learned_before = cur.fetchone()[0]

        cur.execute("ATTACH DATABASE ? AS upstream;", (str(upstream_db_path),))
        try:
            cur.execute("""
            INSERT OR REPLACE INTO skills (name, plugin_name, category, is_active, description, content, token_estimate, updated_at)
            SELECT name, plugin_name, category, is_active, description, content, token_estimate, updated_at
            FROM upstream.skills;
            """)
            merged_skills = cur.rowcount

            cur.execute("""
            INSERT OR REPLACE INTO rules (key, name, category, scope, is_active, summary, content, token_estimate, updated_at)
            SELECT key, name, category, scope, is_active, summary, content, token_estimate, updated_at
            FROM upstream.rules
            WHERE category != 'learned';
            """)
            merged_rules = cur.rowcount

            cur.execute("SELECT name FROM upstream.sqlite_master WHERE type='table' AND name='synonym_synapses';")
            if cur.fetchone():
                cur.execute("""
                CREATE TABLE IF NOT EXISTS synonym_synapses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    term TEXT UNIQUE NOT NULL,
                    synonyms TEXT NOT NULL,
                    language TEXT DEFAULT 'de',
                    category TEXT DEFAULT 'general'
                );
                """)
                main_cols = {r[1] for r in cur.execute("PRAGMA main.table_info(synonym_synapses);").fetchall()}
                up_cols = {r[1] for r in cur.execute("PRAGMA upstream.table_info(synonym_synapses);").fetchall()}
                if "synonyms" in main_cols and "synonyms" in up_cols:
                    cur.execute("""
                    INSERT INTO synonym_synapses (term, synonyms, language, category)
                    SELECT term, synonyms, COALESCE(language, 'de'), COALESCE(category, 'general')
                    FROM upstream.synonym_synapses
                    WHERE 1=1
                    ON CONFLICT(term) DO UPDATE SET synonyms = excluded.synonyms;
                    """)
                elif "expansions" in main_cols and "expansions" in up_cols:
                    cur.execute("""
                    INSERT INTO synonym_synapses (term, expansions)
                    SELECT term, expansions FROM upstream.synonym_synapses
                    WHERE 1=1
                    ON CONFLICT(term) DO UPDATE SET expansions = excluded.expansions;
                    """)

            cur.execute("INSERT INTO rules_fts(rules_fts) VALUES ('rebuild');")
            cur.execute("INSERT INTO skills_fts(skills_fts) VALUES ('rebuild');")
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='synonym_fts';")
            if cur.fetchone():
                cur.execute("INSERT INTO synonym_fts(synonym_fts) VALUES ('rebuild');")

            cur.execute("SELECT COUNT(*) FROM rules WHERE category = 'learned';")
            learned_after = cur.fetchone()[0]

            if learned_after < learned_before:
                conn.rollback()
                raise RuntimeError("Differential merge aborted: User-learned rules count dropped! Rolled back.")

            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            try:
                cur.execute("DETACH DATABASE upstream;")
            except sqlite3.Error:
                pass

        try:
            conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
        except sqlite3.Error:
            pass

    return {
        "merged_skills": merged_skills,
        "merged_rules": merged_rules,
        "preserved_learned_rules": learned_after
    }


def download_file(url: str, dest_path: Path, is_binary: bool = False):
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": f"SkillsDB-Updater/{__version__}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        content = resp.read()
        if is_binary:
            dest_path.write_bytes(content)
        else:
            dest_path.write_text(content.decode("utf-8", errors="replace"), encoding="utf-8")


def update_plugin(force: bool = False):
    """Safely updates SkillsDB to the latest version with zero data loss guarantee."""
    print("\n================ SKILLSDB SAFE UPDATE ================")
    if not force:
        check = check_update(quiet=True)
        if check and not check["has_update"]:
            print(f"[OK] SkillsDB is already on the latest version (v{__version__}).")
            print("To re-sync anyway, run: skillsdb update --force")
            print("======================================================\n")
            return

    backup_path = None
    if DB_PATH.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = DB_DIR / f"customizations.db.bak_{timestamp}"
        shutil.copy2(DB_PATH, backup_path)
        print(f"[1/4] Safety backup created: {backup_path.name}")
    else:
        print("[1/4] Initializing fresh installation.")

    repo_root = find_repo_root()
    temp_dir = None

    try:
        if repo_root and (repo_root / ".git").exists():
            print(f"[2/4] Pulling latest repository updates via git: {repo_root}")
            subprocess.run(["git", "pull", "origin", "main"], cwd=str(repo_root), check=True, capture_output=True)
            source_db = repo_root / "database" / "customizations.db"
            source_mgr = repo_root / "database" / "db_manager.py"
            source_plugin = repo_root / "plugin"
        else:
            print("[2/4] Downloading latest assets directly from GitHub...")
            import tempfile
            temp_dir = Path(tempfile.mkdtemp(prefix="skillsdb_update_"))
            source_db = temp_dir / "customizations.db"
            source_mgr = temp_dir / "db_manager.py"
            source_plugin = temp_dir / "plugin"

            base_raw = "https://raw.githubusercontent.com/scorpion421/AntiGraviton/main"
            download_file(f"{base_raw}/database/customizations.db", source_db, is_binary=True)
            download_file(f"{base_raw}/database/db_manager.py", source_mgr)
            download_file(f"{base_raw}/plugin/plugin.json", source_plugin / "plugin.json")
            download_file(f"{base_raw}/plugin/hooks.json", source_plugin / "hooks.json")
            download_file(f"{base_raw}/plugin/rules/AGENTS.md", source_plugin / "rules" / "AGENTS.md")
            download_file(f"{base_raw}/plugin/skills/customizations-db/SKILL.md", source_plugin / "skills" / "customizations-db" / "SKILL.md")

        print("[3/4] Performing non-destructive differential merge on database...")
        if DB_PATH.exists() and source_db.exists():
            stats_res = merge_upstream_database(DB_PATH, source_db)
            print(f"      * Official skills synced: {stats_res['merged_skills']}")
            print(f"      * Official rules synced:  {stats_res['merged_rules']}")
            print(f"      * User-learned rules:     {stats_res['preserved_learned_rules']} (100% preserved)")
        elif source_db.exists():
            shutil.copy2(source_db, DB_PATH)
            print("      * Base database initialized.")

        print("[4/4] Updating engine, hooks, and plugin files...")
        target_plugin = GLOBAL_PLUGINS_PATH / "customizations-db"
        target_plugin.mkdir(parents=True, exist_ok=True)
        for stray in (".agents", ".git"):
            stray_dir = target_plugin / stray
            if stray_dir.exists():
                shutil.rmtree(stray_dir, ignore_errors=True)
        shutil.copytree(source_plugin, target_plugin, dirs_exist_ok=True)

        if source_mgr.exists():
            shutil.copy2(source_mgr, DB_DIR / "db_manager.py")

        # Automatically prune any legacy excluded non-workspace paths or case duplicates from runtime_config
        get_learned_workspaces(db_path=DB_PATH, prune_missing=True)
        ensure_windows_utf8_manifests()
        sync_builtin_skills(force=True, silent=True)

        print("\n[OK] Safe update completed successfully! Project memory & learned rules remain intact.")
        print("Running system diagnostics:")
        conn = get_connection(DB_PATH)
        doctor(conn)
        conn.close()

    except Exception as err:
        print(f"\n[ERROR] Update interrupted: {err}")
        if backup_path and backup_path.exists():
            print(f"[ROLLBACK] Restoring database from safety backup: {backup_path.name}")
            shutil.copy2(backup_path, DB_PATH)
            print("[ROLLBACK] Database restored to previous state. No data lost.")
        raise
    finally:
        if temp_dir and temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


def parse_skill_frontmatter(content: str) -> tuple[str, str]:
    name = None
    description = None
    m = re.match(r"^---\s*\r?\n(.*?)\r?\n---", content, re.DOTALL)
    if m:
        frontmatter = m.group(1)
        name_match = re.search(r"^name:\s*(.+)$", frontmatter, re.MULTILINE)
        if name_match:
            name = name_match.group(1).strip().strip("'\"")
        desc_match = re.search(r"^description:\s*(?:[>|][-+]?\s*\r?\n)?(.*?)(?=\r?\n[a-zA-Z0-9_-]+:|$)", frontmatter, re.DOTALL | re.MULTILINE)
        if desc_match:
            desc_raw = desc_match.group(1).strip()
            description = " ".join([line.strip() for line in desc_raw.splitlines() if line.strip()])
    return name, description


def compile_skill_directory_content(skill_folder: Path, default_plugin: str = "builtin") -> tuple[str, str, str]:
    """
    Reads SKILL.md from skill_folder and appends any bundled sub-documentation
    (docs/*.md, references/*.md) as markdown sections for surgical --section retrieval.
    Returns (skill_name, description, full_compiled_content).
    """
    skill_md = skill_folder / "SKILL.md"
    content = skill_md.read_text(encoding="utf-8", errors="replace")
    name, desc = parse_skill_frontmatter(content)
    name = name or skill_folder.name
    desc = desc or f"Workflow skill from {default_plugin}"

    sub_chunks = []
    for sub_dir_name in ("docs", "references"):
        sub_dir = skill_folder / sub_dir_name
        if sub_dir.exists() and sub_dir.is_dir():
            for md_file in sorted(sub_dir.glob("*.md")):
                try:
                    sub_raw = md_file.read_text(encoding="utf-8", errors="replace").strip()
                    sub_body = re.sub(r"^---\s*\n.*?\n---\s*\n?", "", sub_raw, count=1, flags=re.DOTALL).strip()
                    if sub_body:
                        sub_chunks.append(sub_body)
                except Exception:
                    pass

    if sub_chunks:
        content = content.rstrip() + "\n\n---\n\n" + "\n\n---\n\n".join(sub_chunks) + "\n"

    return name, desc, content


def sync_builtin_skills(conn: sqlite3.Connection = None, force: bool = False, silent: bool = False) -> int:
    """
    Synchronizes Antigravity builtin skills (~/.gemini/antigravity/builtin/skills)
    into customizations.db whenever builtin/.checksum changes or force=True.
    """
    if not BUILTIN_SKILLS_PATH.exists() or not BUILTIN_SKILLS_PATH.is_dir():
        return 0

    checksum_file = BUILTIN_SKILLS_PATH.parent / ".checksum"
    current_checksum = ""
    if checksum_file.exists():
        try:
            current_checksum = checksum_file.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            current_checksum = ""

    own_conn = conn is None
    active_conn = get_connection(DB_PATH) if own_conn else conn
    try:
        init_db(active_conn)
        cur = active_conn.cursor()
        if not force and current_checksum:
            cur.execute("SELECT value FROM runtime_config WHERE key = 'builtin_skills_checksum';")
            row = cur.fetchone()
            if row and row[0] == current_checksum:
                return 0

        now_iso = datetime.now(timezone.utc).isoformat()
        synced_count = 0
        for skill_folder in sorted(BUILTIN_SKILLS_PATH.iterdir()):
            if not skill_folder.is_dir():
                continue
            skill_md = skill_folder / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                name, desc, content = compile_skill_directory_content(skill_folder, default_plugin="builtin")
                tokens = estimate_tokens(content)
                cur.execute("""
                INSERT OR REPLACE INTO skills (name, plugin_name, category, is_active, description, content, token_estimate, updated_at)
                VALUES (?, 'builtin', 'builtin', 1, ?, ?, ?, ?);
                """, (name, desc, content, tokens, now_iso))
                synced_count += 1
            except Exception as err:
                if not silent:
                    print(f"Error syncing builtin skill {skill_folder.name}: {err}")

        if synced_count > 0:
            cur.execute("INSERT INTO skills_fts(skills_fts) VALUES ('rebuild');")
            if current_checksum:
                cur.execute("""
                INSERT OR REPLACE INTO runtime_config (key, value, updated_at)
                VALUES ('builtin_skills_checksum', ?, ?);
                """, (current_checksum, now_iso))
            active_conn.commit()
            if not silent:
                print(f"Synced {synced_count} builtin skills from {BUILTIN_SKILLS_PATH}")
        return synced_count
    finally:
        if own_conn:
            active_conn.close()


def add_or_update_rule(conn: sqlite3.Connection, key: str, name: str, category: str, content: str, summary: str = None, scope: str = "global"):
    cursor = conn.cursor()
    now_iso = datetime.now(timezone.utc).isoformat()
    tokens = estimate_tokens(content)

    cursor.execute("""
    INSERT OR REPLACE INTO rules (key, name, category, scope, is_active, summary, content, token_estimate, updated_at)
    VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
    """, (key, name, category, scope, summary or name, content, tokens, now_iso))

    cursor.execute("INSERT INTO rules_fts(rules_fts) VALUES ('rebuild');")
    conn.commit()
    print(f"Rule '{key}' saved successfully (~{tokens} tokens).")


def add_or_update_skill(conn: sqlite3.Connection, name: str, description: str, content: str, plugin_name: str = "custom", category: str = "workflow"):
    cursor = conn.cursor()
    now_iso = datetime.now(timezone.utc).isoformat()
    tokens = estimate_tokens(content)

    cursor.execute("""
    INSERT OR REPLACE INTO skills (name, plugin_name, category, is_active, description, content, token_estimate, updated_at)
    VALUES (?, ?, ?, 1, ?, ?, ?, ?)
    """, (name, plugin_name, category, description, content, tokens, now_iso))

    cursor.execute("INSERT INTO skills_fts(skills_fts) VALUES ('rebuild');")
    conn.commit()
    print(f"Skill '{name}' saved successfully (~{tokens} tokens).")


def learn_rule(conn: sqlite3.Connection, key: str, name: str, content: str, category: str = "learned", scope: str = "global"):
    add_or_update_rule(conn, key=key, name=name, category=category, content=content, summary=name, scope=scope)
    print(f"Rule '{name}' (Key: {key}) successfully learned and persisted to central database.")


def import_file(conn: sqlite3.Connection, file_path_str: str):
    path = Path(file_path_str)
    if not path.exists():
        print(f"Error: File not found at {path}")
        return

    content = path.read_text(encoding="utf-8", errors="replace")
    name, description = parse_skill_frontmatter(content)

    if name and description:
        add_or_update_skill(conn, name=name, description=description, content=content, plugin_name="imported", category="imported")
    else:
        key = path.stem.lower().replace(" ", "-")
        add_or_update_rule(conn, key=key, name=path.stem, category="imported", content=content, summary=f"Imported from {path.name}")


def remove_item(conn: sqlite3.Connection, item_type: str, identifier: str):
    cursor = conn.cursor()
    if item_type == "skill":
        cursor.execute("SELECT id FROM skills WHERE name = ?;", (identifier,))
        row = cursor.fetchone()
        if row:
            cursor.execute("DELETE FROM skills WHERE id = ?;", (row["id"],))
            cursor.execute("INSERT INTO skills_fts(skills_fts) VALUES ('rebuild');")
            conn.commit()
            print(f"Skill '{identifier}' removed.")
        else:
            print(f"Skill '{identifier}' not found.")
    elif item_type == "rule":
        cursor.execute("SELECT id FROM rules WHERE key = ?;", (identifier,))
        row = cursor.fetchone()
        if row:
            cursor.execute("DELETE FROM rules WHERE id = ?;", (row["id"],))
            cursor.execute("INSERT INTO rules_fts(rules_fts) VALUES ('rebuild');")
            conn.commit()
            print(f"Rule '{identifier}' removed.")
        else:
            print(f"Rule '{identifier}' not found.")


def sync_database(conn: sqlite3.Connection, direction: str, remote_path_str: str = None):
    if remote_path_str:
        remote_path = Path(remote_path_str)
    else:
        cfg_share = None
        try:
            cur = conn.cursor()
            cur.execute("SELECT value FROM runtime_config WHERE key = 'remote_share_path';")
            row = cur.fetchone()
            if row and row[0]:
                cfg_share = Path(row[0])
        except Exception:
            pass
        remote_path = cfg_share or DEFAULT_REMOTE_SHARE

    if direction == "push":
        if not DB_PATH.exists():
            print(f"Error: Local database {DB_PATH} does not exist.")
            return
        remote_path.parent.mkdir(parents=True, exist_ok=True)
        if remote_path.exists():
            bak_path = remote_path.with_suffix(f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
            shutil.copy2(remote_path, bak_path)
            print(f"Created remote backup at: {bak_path}")

        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        shutil.copy2(DB_PATH, remote_path)
        print(f"Successfully pushed local database to network share:\n{remote_path}")
    elif direction == "pull":
        if not remote_path.exists():
            print(f"Error: Remote database {remote_path} does not exist.")
            return
        conn.close()
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        if DB_PATH.exists():
            bak_path = DB_PATH.with_suffix(f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
            shutil.copy2(DB_PATH, bak_path)
            print(f"Created local backup at: {bak_path}")
        shutil.copy2(remote_path, DB_PATH)
        print(f"Successfully pulled database from network share:\n{remote_path} -> {DB_PATH}")


def doctor(conn: sqlite3.Connection):
    """System health diagnostics."""
    try:
        sync_builtin_skills(conn, silent=True)
    except Exception:
        pass
    print("\n================ SKILLSDB SYSTEM DIAGNOSTICS ================")
    # 1. Central DB Check
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        res = cursor.fetchone()[0]
        cursor.execute("PRAGMA journal_mode;")
        jmode = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM rules;")
        rcnt = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM skills;")
        scnt = cursor.fetchone()[0]
        print(f"[OK] Central Database: Healthy (Integrity: {res}, Journal: {jmode}, Rules: {rcnt}, Skills: {scnt})")
    except Exception as e:
        print(f"[FAIL] Central Database error: {e}")

    # 2. Model Profile & Concurrency Health Check
    try:
        tier, tdesc = detect_model_tier()
        print(f"[OK] Model Concurrency Profile: {tier.upper()} ({tdesc})")
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            futures = [ex.submit(fetch_single_skill, "customizations-db", DB_PATH, True) for _ in range(8)]
            c_results = [f.result() for f in futures]
        if all(r.get("found") for r in c_results):
            print("[OK] SQLite Concurrency Engine: 8-thread parallel WAL read test passed")
        else:
            print("[WARN] SQLite Concurrency Engine: Concurrency test had partial fetch misses")
    except Exception as ce:
        print(f"[FAIL] SQLite Concurrency Engine error: {ce}")

    # 3. PATH check
    skillsdb_in_path = shutil.which("skillsdb")
    if skillsdb_in_path:
        print(f"[OK] Global CLI: Found in PATH ({skillsdb_in_path})")
    else:
        print("[WARN] Global CLI: 'skillsdb' not found in current PATH.")

    # 4. Active Plugin check
    active_plugin = GLOBAL_PLUGINS_PATH / "customizations-db"
    if active_plugin.exists() and (active_plugin / "plugin.json").exists():
        has_hooks = (active_plugin / "hooks.json").exists()
        hook_str = "with lifecycle hooks" if has_hooks else "without hooks"
        print(f"[OK] Antigravity Plugin: Deployed at {active_plugin} ({hook_str})")
    else:
        print(f"[WARN] Antigravity Plugin: Not found in {GLOBAL_PLUGINS_PATH}/customizations-db")

    # 5. Global Git Ignore
    try:
        chk = subprocess.run(["git", "config", "--global", "core.excludesfile"], capture_output=True, text=True)
        ign_path_str = chk.stdout.strip()
        ign_path = Path(ign_path_str).expanduser() if ign_path_str else None
        if ign_path and ign_path.exists():
            txt = ign_path.read_text(encoding="utf-8", errors="ignore")
            if ".agents/memory.db" in txt:
                print(f"[OK] Global Git Ignore: Configured in {ign_path}")
            else:
                print(f"[WARN] Global Git Ignore: {ign_path} does not contain .agents/memory.db")
        else:
            print("[INFO] Global Git Ignore: Not set or file does not exist.")
    except Exception as e:
        print(f"[INFO] Git check skipped: {e}")

    # 6. Project Memory in current directory
    proj_root = find_project_root()
    proj_db = proj_root / ".agents" / "memory.db"
    if _is_protected_non_workspace(proj_root):
        print(f"[INFO] Project Memory: No active project workspace in current directory (protected path: {proj_root})")
    elif proj_db.exists():
        try:
            with managed_connection(proj_db, is_project_db=True) as pconn:
                pcur = pconn.cursor()
                pcur.execute("SELECT COUNT(*) FROM session_snapshots;")
                snaps = pcur.fetchone()[0]
                pcur.execute("SELECT COUNT(*) FROM project_decisions;")
                decs = pcur.fetchone()[0]
                pcur.execute("SELECT COUNT(*) FROM project_facts;")
                facts = pcur.fetchone()[0]
            print(f"[OK] Project Memory: Active in {proj_root} ({facts} facts, {decs} decisions, {snaps} snapshots)")
        except Exception as e:
            print(f"[WARN] Project Memory DB present but error reading: {e}")
    else:
        print(f"[INFO] Project Memory: No .agents/memory.db in {proj_root} (auto-inits on first write)")

    # 7. Windows UTF-8 Configuration
    if sys.platform == "win32":
        utf8_ok, utf8_msg = check_windows_utf8()
        if utf8_ok:
            print(f"[OK] Windows UTF-8: {utf8_msg}")
        else:
            print(f"[WARN] Windows UTF-8: {utf8_msg} (Run 'skillsdb fix-utf8' to resolve)")

    # 8. Gitea Local Code Backup Service
    try:
        g_cfg = get_gitea_config()
        g_svc = get_gitea_service_status()
        g_online, g_info = check_gitea_health(timeout=1.5)
        if g_online:
            g_ver = g_info.get("version", "unknown")
            g_user = g_cfg.get("user") or "unconfigured"
            print(f"[OK] Gitea Integration: Service {g_svc}, API ONLINE (v{g_ver} at {g_cfg['url']}, Owner: {g_user})")
        else:
            print(f"[INFO] Gitea Integration: Service {g_svc}, API OFFLINE ({g_cfg['url']})")
    except Exception as ge:
        print(f"[INFO] Gitea Integration check skipped: {ge}")

    print("================================================================\n")


def import_from_plugins(conn: sqlite3.Connection):
    """Non-destructively imports/updates skills from active plugins, plugins_archive, and builtin directories into customizations.db."""
    init_db(conn)
    cursor = conn.cursor()
    now_iso = datetime.now(timezone.utc).isoformat()

    skills_count = 0
    search_dirs = []
    if GLOBAL_PLUGINS_PATH.exists():
        search_dirs.append(GLOBAL_PLUGINS_PATH)
    if ARCHIVED_PLUGINS_PATH.exists():
        search_dirs.append(ARCHIVED_PLUGINS_PATH)

    for base_dir in search_dirs:
        for plugin_dir in sorted(base_dir.iterdir()):
            if not plugin_dir.is_dir():
                continue
            skills_dir = plugin_dir / "skills"
            if skills_dir.exists() and skills_dir.is_dir():
                for skill_folder in sorted(skills_dir.iterdir()):
                    if not skill_folder.is_dir():
                        continue
                    skill_md = skill_folder / "SKILL.md"
                    if skill_md.exists():
                        try:
                            name, desc, content = compile_skill_directory_content(skill_folder, default_plugin=plugin_dir.name)
                            tokens = estimate_tokens(content)
                            cursor.execute("""
                            INSERT OR REPLACE INTO skills (name, plugin_name, category, is_active, description, content, token_estimate, updated_at)
                            VALUES (?, ?, ?, 1, ?, ?, ?, ?);
                            """, (name, plugin_dir.name, plugin_dir.name, desc, content, tokens, now_iso))
                            skills_count += 1
                        except Exception as err:
                            print(f"Error inserting skill {skill_folder.name}: {err}")

    builtin_synced = sync_builtin_skills(conn, force=True, silent=True)
    skills_count += builtin_synced

    cursor.execute("INSERT INTO rules_fts(rules_fts) VALUES ('rebuild');")
    cursor.execute("INSERT INTO skills_fts(skills_fts) VALUES ('rebuild');")
    conn.commit()

    cursor.execute("SELECT COUNT(*) FROM rules;")
    total_rules = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM skills;")
    total_skills = cursor.fetchone()[0]
    print(f"Database synchronized ({skills_count} plugin/builtin skills updated; total: {total_rules} rules, {total_skills} skills) at {DB_PATH}")
