#!/usr/bin/env python3
"""
Cross-platform deployment script for Antigravity Customizations Database.
Deploys customizations.db, db_manager.py, and the customizations-db plugin
to the active user's ~/.gemini directory with zero hardcoded paths.
"""

import os
import sys
import json
import shutil
import argparse
from pathlib import Path


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

    # 2. Copy Database & CLI Manager
    print("[2/5] Deploying database and management CLI...")
    shutil.copy2(source_dir / "database" / "customizations.db", target_db_dir / "customizations.db")
    shutil.copy2(source_dir / "database" / "db_manager.py", target_db_dir / "db_manager.py")
    if (source_dir / "skillsdb").exists():
        shutil.copytree(source_dir / "skillsdb", target_db_dir / "skillsdb", dirs_exist_ok=True)
    print(f"      Database deployed to: {target_db_dir}")

    # Deploy global CLI wrapper to ~/.gemini/antigravity/bin (in system PATH)
    target_bin_dir = target_dir / "antigravity" / "bin"
    target_bin_dir.mkdir(parents=True, exist_ok=True)
    cmd_path = target_bin_dir / "skillsdb.cmd"
    cmd_path.write_text('@echo off\npython "%USERPROFILE%\\.gemini\\database\\db_manager.py" %*\n', encoding="ascii")
    sh_path = target_bin_dir / "skillsdb"
    sh_path.write_text('#!/bin/sh\npython3 "$HOME/.gemini/database/db_manager.py" "$@"\n', encoding="utf-8")
    try:
        sh_path.chmod(0o755)
    except Exception:
        pass
    print(f"      Global CLI wrapper deployed to: {cmd_path}")

    # 3. Copy Plugin
    print("[3/5] Deploying plugin files...")
    shutil.copytree(source_dir / "plugin", target_plugin, dirs_exist_ok=True)
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
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.move(str(item), str(dest))
                print(f"      Archived plugin to plugins_archive: {item.name}")

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
        conn = db_manager.get_connection(target_db_dir / "customizations.db")
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
