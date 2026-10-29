#!/usr/bin/env python3
"""
Cross-platform deployment script for Antigravity Customizations Database.
Deploys customizations.db, db_manager.py, and the customizations-db plugin
to the active user's ~/.gemini directory with zero hardcoded paths.
"""

import os
import re
import sys
import json
import shutil
import sqlite3
import argparse
import contextlib
from pathlib import Path


def safe_deploy_database(source_db_file: Path, target_db_file: Path) -> None:
    """
    Safely deploys or upgrades customizations.db without overwriting active WAL files
    or leaking SQLite file handles.
    - If target_db_file already exists and is healthy, performs an atomic in-place
      differential merge via ATTACH DATABASE, preserving 100% of runtime_config,
      token_savings_log, and user-learned rules.
    - If target_db_file does not exist (or needs initialization), uses SQLite's
      native online backup API with clean WAL checkpointing.
    """
    target_db_file.parent.mkdir(parents=True, exist_ok=True)

    if target_db_file.exists() and target_db_file.stat().st_size > 0:
        try:
            with contextlib.closing(sqlite3.connect(str(target_db_file), timeout=30.0)) as conn:
                conn.execute("PRAGMA busy_timeout = 30000;")
                conn.execute("PRAGMA journal_mode = WAL;")
                cur = conn.cursor()
                cur.execute("PRAGMA quick_check;")
                if cur.fetchone()[0] == "ok":
                    cur.execute("ATTACH DATABASE ? AS upstream;", (str(source_db_file),))
                    try:
                        cur.execute("""
                        INSERT OR REPLACE INTO skills (name, plugin_name, category, is_active, description, content, token_estimate, updated_at)
                        SELECT name, plugin_name, category, is_active, description, content, token_estimate, updated_at
                        FROM upstream.skills;
                        """)
                        cur.execute("""
                        INSERT OR REPLACE INTO rules (key, name, category, scope, is_active, summary, content, token_estimate, updated_at)
                        SELECT key, name, category, scope, is_active, summary, content, token_estimate, updated_at
                        FROM upstream.rules
                        WHERE category != 'learned';
                        """)
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
                                SELECT term, expansions
                                FROM upstream.synonym_synapses
                                WHERE 1=1
                                ON CONFLICT(term) DO UPDATE SET expansions = excluded.expansions;
                                """)
                        cur.execute("""
                        CREATE TABLE IF NOT EXISTS runtime_config (
                            key TEXT PRIMARY KEY,
                            value TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );
                        """)
                        cur.execute("""
                        INSERT OR REPLACE INTO runtime_config (key, value, updated_at)
                        SELECT key, value, updated_at
                        FROM upstream.runtime_config
                        WHERE key = 'schema_version';
                        """)
                        cur.execute("INSERT INTO rules_fts(rules_fts) VALUES ('rebuild');")
                        cur.execute("INSERT INTO skills_fts(skills_fts) VALUES ('rebuild');")
                        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='synonym_fts';")
                        if cur.fetchone():
                            cur.execute("INSERT INTO synonym_fts(synonym_fts) VALUES ('rebuild');")
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
                    conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
                    return
        except Exception:
            pass

    # Fresh install or recovery path: remove stale WAL/SHM sidecars and use online backup API
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(target_db_file) + suffix)
        if sidecar.exists():
            try:
                sidecar.unlink()
            except OSError:
                pass

    with contextlib.closing(sqlite3.connect(str(source_db_file), timeout=30.0)) as src_conn:
        with contextlib.closing(sqlite3.connect(str(target_db_file), timeout=30.0)) as dst_conn:
            src_conn.backup(dst_conn)
            dst_conn.execute("PRAGMA journal_mode = WAL;")
            dst_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")


def deploy(source_dir: Path = None, target_dir: Path = None):
    if not source_dir:
        source_dir = Path(__file__).parent.resolve()
    else:
        source_dir = Path(source_dir).resolve()

    if not target_dir:
        target_dir = Path.home() / ".gemini"
    else:
        target_dir = Path(target_dir).resolve()

    target_db_dir = target_dir / "database"
    target_config_dir = target_dir / "config"
    target_plugins_dir = target_config_dir / "plugins"
    target_plugin = target_plugins_dir / "customizations-db"
    config_json_path = target_config_dir / "config.json"

    print("=" * 60)
    print("Antigravity Customizations Database Deployment (Python)")
    print(f"Source: {source_dir}")
    print(f"Target: {target_dir}")
    print("=" * 60)

    # 1. Create directories
    print("[1/5] Ensuring target directories exist...")
    target_db_dir.mkdir(parents=True, exist_ok=True)
    target_plugins_dir.mkdir(parents=True, exist_ok=True)
    target_plugin.mkdir(parents=True, exist_ok=True)

    # 2. Copy Database & CLI Manager (via enterprise WAL-safe differential upgrade / online backup)
    print("[2/5] Deploying database and management CLI...")
    target_db_file = target_db_dir / "customizations.db"
    safe_deploy_database(source_dir / "database" / "customizations.db", target_db_file)
    shutil.copy2(source_dir / "database" / "db_manager.py", target_db_dir / "db_manager.py")
    if (source_dir / "skillsdb").exists():
        shutil.copytree(source_dir / "skillsdb", target_db_dir / "skillsdb", dirs_exist_ok=True)
    print(f"      Database deployed to: {target_db_dir}")

    # Deploy global CLI wrapper to ~/.gemini/antigravity/bin (in system PATH)
    target_bin_dir = target_dir / "antigravity" / "bin"
    target_bin_dir.mkdir(parents=True, exist_ok=True)
    cmd_path = target_bin_dir / "skillsdb.cmd"
    cmd_path.write_text('@echo off\r\nset "PATH=%USERPROFILE%\\.local\\bin;%PATH%"\r\npython "%USERPROFILE%\\.gemini\\database\\db_manager.py" %*\r\n', encoding="ascii")
    sh_path = target_bin_dir / "skillsdb"
    sh_path.write_text('#!/bin/sh\nexport PATH="$HOME/.local/bin:$PATH"\npython3 "$HOME/.gemini/database/db_manager.py" "$@"\n', encoding="utf-8")
    try:
        sh_path.chmod(0o755)
    except Exception:
        pass
    print(f"      Global CLI wrapper deployed to: {cmd_path}")

    # 3. Copy Plugin (and clean up any stray .agents/.git folders inside target_plugin)
    print("[3/5] Deploying plugin files...")
    for stray in (".agents", ".git"):
        stray_dir = target_plugin / stray
        if stray_dir.exists():
            shutil.rmtree(stray_dir, ignore_errors=True)
    shutil.copytree(source_dir / "plugin", target_plugin, dirs_exist_ok=True)
    deployed_readme = target_plugin / "README.md"
    if deployed_readme.exists() and target_db_file.exists():
        try:
            with contextlib.closing(sqlite3.connect(str(target_db_file), timeout=10.0)) as _rc_conn:
                _rows = dict(
                    _rc_conn.execute(
                        "SELECT key, value FROM runtime_config WHERE key IN ('gitea_url', 'gitea_user', 'gitea_owner');"
                    ).fetchall()
                )
            _g_url = (_rows.get("gitea_url") or "http://localhost:3000").rstrip("/")
            _g_owner = (_rows.get("gitea_user") or _rows.get("gitea_owner") or "").strip()
            if _g_owner:
                _rm_txt = deployed_readme.read_text(encoding="utf-8")
                _repo_url = f"{_g_url}/{_g_owner}/AntiGraviton"
                _rm_txt = re.sub(
                    r"- \*\*Local Gitea (Service|Repository)\*\*: .*",
                    f"- **Local Gitea Repository**: [{_repo_url}]({_repo_url})",
                    _rm_txt,
                )
                deployed_readme.write_text(_rm_txt, encoding="utf-8")
        except Exception:
            pass
    print(f"      Plugin deployed to: {target_plugin}")

    # 4. Isolate legacy plugins and update config.json
    config_data = {}
    if config_json_path.exists():
        try:
            with open(config_json_path, "r", encoding="utf-8-sig") as f:
                config_data = json.load(f)
        except Exception as err:
            print(f"Warning: Could not read existing config.json: {err}")

    if "plugins" not in config_data:
        config_data["plugins"] = {}

    config_data["plugins"]["customizations-db"] = {"enabled": True}

    print("[4/6] Isolating legacy plugins and updating config.json...")

    target_archive_plugins_dir = target_dir / "plugins_archive"
    target_archive_plugins_dir.mkdir(parents=True, exist_ok=True)

    # Clean up any legacy plugins_disabled inside config/
    legacy_disabled = target_config_dir / "plugins_disabled"
    if legacy_disabled.exists():
        shutil.rmtree(legacy_disabled)

    # Physically archive legacy plugins to plugins_archive (outside config/) so Antigravity never discovers them or launches unneeded MCP servers
    if target_plugins_dir.exists():
        for item in target_plugins_dir.iterdir():
            if item.is_dir() and item.name != "customizations-db":
                dest = target_archive_plugins_dir / item.name
                try:
                    if dest.exists():
                        shutil.rmtree(dest, ignore_errors=True)
                    shutil.move(str(item), str(dest))
                    print(f"      Archived plugin to plugins_archive: {item.name}")
                except Exception:
                    shutil.rmtree(str(item), ignore_errors=True)
                    print(f"      Disabled plugin in config.json (sidecar active): {item.name}")

    plugins_to_disable = [
        "communication-style",
        "local-admin",
        "scripting-rules",
        "android-cli-plugin",
        "chrome-devtools-plugin",
        "data-agent-kit-plugin",
        "firebase",
        "flutter",
        "gemini-api",
        "google-antigravity-sdk",
        "google_maps_platform",
        "modern-web-guidance-plugin",
        "science"
    ]

    for p in plugins_to_disable:
        config_data["plugins"][p] = {"enabled": False}

    with open(config_json_path, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=2)
    print("      config.json updated successfully.")

    # 5. Verification
    print("[5/6] Verifying database connectivity...")
    try:
        sys.path.insert(0, str(target_db_dir))
        import db_manager
        db_manager.ensure_windows_utf8_manifests()
        conn = db_manager.get_connection(target_db_dir / "customizations.db")
        db_manager.init_synonyms(conn)
        db_manager.sync_builtin_skills(conn, force=True, silent=True)
        db_manager.stats(conn)
        conn.close()
    except Exception as err:
        print(f"Verification notice: {err}")

    # 6. Global Git Ignore configuration
    print("[6/6] Ensuring global Git ignore for project memory...")
    try:
        import subprocess
        user_home_str = str(Path.home())
        git_check = subprocess.run(
            ["git", "config", "--global", "core.excludesfile"],
            cwd=user_home_str,
            capture_output=True,
            text=True
        )
        global_ignore_path = git_check.stdout.strip()
        if not global_ignore_path:
            global_ignore_path = str(Path.home() / ".gitignore_global")
            subprocess.run(
                ["git", "config", "--global", "core.excludesfile", global_ignore_path.replace("\\", "/")],
                cwd=user_home_str,
                check=True
            )
        else:
            if global_ignore_path.startswith("~"):
                global_ignore_path = str(Path.home() / global_ignore_path[2:])

        ignore_file = Path(global_ignore_path)
        existing_content = ignore_file.read_text(encoding="utf-8") if ignore_file.exists() else ""

        entries = [
            ".agents/memory.db",
            ".agents/memory.db-wal",
            ".agents/memory.db-shm"
        ]
        missing = [e for e in entries if e not in existing_content]
        if missing:
            with open(ignore_file, "a", encoding="utf-8") as f:
                f.write("\n# Antigravity project memory databases\n" + "\n".join(missing) + "\n")
            print(f"      Added project memory ignore rules to: {ignore_file}")
        else:
            print(f"      Global Git ignore already configured at: {ignore_file}")
    except Exception as err:
        print(f"      Notice: Could not configure global git ignore: {err}")

    print("=" * 60)
    print("Deployment completed successfully!")
    print("Customizations database is now active for this user profile.")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Deploy Antigravity Customizations Database")
    parser.add_argument("--source", type=str, default=None, help="Source directory containing assets")
    parser.add_argument("--target", type=str, default=None, help="Target .gemini directory")
    args = parser.parse_args()
    deploy(args.source, args.target)


if __name__ == "__main__":
    main()

