#!/usr/bin/env python3
"""
Unified Cross-Platform One-Command Installer for the AntiGraviton Complete Suite (v1.4.11).

Supports Linux (systemd --user / rootless daemon), macOS (launchd LaunchAgent), and Windows.
Deploys:
1. Non-destructive upgrade preservation of existing runtime_config, Gitea tokens, learned workspaces, and user-learned rules
2. SkillsDB Context & Memory Engine (v3.7.11) + customizations-db Plugin + shell PATH integration
3. Antigravity Gitea Local Service (v1.1.2) on loopback (127.0.0.1:3000) with crash auto-recovery and optional 03:00 backup timer
4. Zero-touch local Gitea Admin user (with optional explicit password) & API Token provisioning into SkillsDB
5. Strict /api/healthz verification gate, organic workspace discovery, and interactive capability briefing
"""

import os
import re
import sys
import json
import time
import uuid
import stat
import shutil
import sqlite3
import getpass
import platform
import tempfile
import argparse
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

GITEA_VERSION = "1.22.6"


def get_gitea_download_url(version: str = GITEA_VERSION, sys_platform: str = None, machine: str = None) -> str:
    """Returns the official Gitea binary download URL for the target OS and CPU architecture."""
    plat = (sys_platform or sys.platform).lower()
    arch_raw = (machine or platform.machine()).lower()
    arch = "arm64" if arch_raw in ("aarch64", "arm64") else "amd64"

    if plat.startswith("linux"):
        return f"https://dl.gitea.com/gitea/{version}/gitea-{version}-linux-{arch}"
    if plat == "darwin":
        return f"https://dl.gitea.com/gitea/{version}/gitea-{version}-darwin-10.12-{arch}"
    if plat.startswith("win"):
        return f"https://dl.gitea.com/gitea/{version}/gitea-{version}-windows-4.0-{arch}.exe"
    raise RuntimeError(f"Unsupported platform for automatic Gitea binary download: {plat} ({arch_raw})")


def generate_gitea_app_ini(
    template_content: str,
    install_path: Path,
    http_port: int,
    run_user: str,
    sec_key: str,
    int_tok: str,
    jwt_sec: str,
    git_path: str = "",
) -> str:
    """Generates a cross-platform Gitea app.ini from app.example.ini."""
    resolved_str = str(install_path.resolve())
    resolved_posix = resolved_str.replace("\\", "/")

    content = template_content.replace("C:\\Gitea", resolved_str)
    content = re.sub(r"(?m)^(\s*RUN_USER\s*=\s*)[^\r\n]+$", rf"\g<1>{run_user}", content)
    content = re.sub(r"(?m)^(\s*HTTP_PORT\s*=\s*)3000\s*$", rf"\g<1>{http_port}", content)
    content = content.replace("ROOT_URL = http://localhost:3000/", f"ROOT_URL = http://localhost:{http_port}/")
    content = content.replace("GENERATE_A_RANDOM_SECRET_KEY_HERE", sec_key)
    content = content.replace("GENERATE_A_RANDOM_JWT_TOKEN_HERE", int_tok)
    content = content.replace("GENERATE_A_RANDOM_JWT_SECRET_HERE", jwt_sec)

    if os.name != "nt":
        content = content.replace(f"{resolved_str}\\data\\gitea-repositories", f"{resolved_posix}/data/gitea-repositories")
        content = content.replace(f"{resolved_str}\\data\\gitea.db", f"{resolved_posix}/data/gitea.db")
        content = content.replace(f"{resolved_str}\\log", f"{resolved_posix}/log")

    resolved_git = git_path or shutil.which("git") or ""
    if resolved_git:
        resolved_git_posix = resolved_git.replace("\\", "/")
        content = re.sub(r"(?m)^(\s*PATH\s*=\s*).*git(\.exe)?\s*$", rf"\g<1>{resolved_git_posix}", content)

    if "[queue]" not in content:
        content = content.rstrip() + "\n\n[queue]\nTYPE = immediate\n\n[queue.common]\nTYPE = immediate\n"

    return content


def generate_systemd_user_unit(install_path: Path, gitea_bin: Path, config_path: Path) -> str:
    """Returns a rootless systemd --user unit file with automatic crash recovery."""
    work_dir = str(install_path.resolve())
    bin_str = str(gitea_bin.resolve())
    cfg_str = str(config_path.resolve())
    return (
        "[Unit]\n"
        "Description=Antigravity Local Git and SkillsDB Synchronization Engine (Rootless Gitea)\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={work_dir}\n"
        f'ExecStart="{bin_str}" web --config "{cfg_str}" --work-path "{work_dir}"\n'
        "Restart=on-failure\n"
        "RestartSec=5s\n"
        "Environment=GITEA_WORK_DIR=" + work_dir + "\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def generate_launchd_plist(install_path: Path, gitea_bin: Path, config_path: Path) -> str:
    """Returns a macOS LaunchAgent plist for rootless local Gitea execution."""
    work_dir = str(install_path.resolve())
    bin_str = str(gitea_bin.resolve())
    cfg_str = str(config_path.resolve())
    log_out = str((install_path / "log" / "launchd.out.log").resolve())
    log_err = str((install_path / "log" / "launchd.err.log").resolve())
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "    <key>Label</key>\n"
        "    <string>dev.local.antigraviton.gitea</string>\n"
        "    <key>ProgramArguments</key>\n"
        "    <array>\n"
        f"        <string>{bin_str}</string>\n"
        "        <string>web</string>\n"
        "        <string>--config</string>\n"
        f"        <string>{cfg_str}</string>\n"
        "        <string>--work-path</string>\n"
        f"        <string>{work_dir}</string>\n"
        "    </array>\n"
        "    <key>WorkingDirectory</key>\n"
        f"    <string>{work_dir}</string>\n"
        "    <key>RunAtLoad</key>\n"
        "    <true/>\n"
        "    <key>KeepAlive</key>\n"
        "    <true/>\n"
        "    <key>StandardOutPath</key>\n"
        f"    <string>{log_out}</string>\n"
        "    <key>StandardErrorPath</key>\n"
        f"    <string>{log_err}</string>\n"
        "</dict>\n"
        "</plist>\n"
    )


def ensure_posix_shell_path(bin_dir: Path) -> None:
    """Ensures ~/.gemini/antigravity/bin is exported in user shell profiles on Linux/macOS."""
    if os.name == "nt":
        return
    export_line = 'export PATH="$HOME/.gemini/antigravity/bin:$PATH"'
    home = Path.home()
    for rc_name in (".bashrc", ".zshrc", ".profile"):
        rc_path = home / rc_name
        if rc_path.exists() or rc_name == ".profile":
            try:
                existing = rc_path.read_text(encoding="utf-8", errors="ignore") if rc_path.exists() else ""
                if ".gemini/antigravity/bin" not in existing:
                    with open(rc_path, "a", encoding="utf-8") as f:
                        f.write(f"\n# Antigravity SkillsDB CLI\n{export_line}\n")
            except Exception:
                pass


def configure_posix_backup_schedule(
    install_path: Path,
    gitea_bin: Path,
    config_path: Path,
    backup_target: Path,
    backup_time: str = "03:00",
    retention_days: int = 14,
) -> None:
    """Creates backup_gitea.py and registers a systemd --user timer or cron job on Linux/macOS."""
    scripts_dir = install_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    backup_script = scripts_dir / "backup_gitea.py"

    script_code = f'''#!/usr/bin/env python3
"""Automated online Gitea backup script (LevelDB-free immediate queue compatible)."""
import os
import sys
import socket
import shutil
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime, timedelta

INSTALL_PATH = Path({repr(str(install_path.resolve()))})
GITEA_BIN = Path({repr(str(gitea_bin.resolve()))})
CONFIG_PATH = Path({repr(str(config_path.resolve()))})
BACKUP_TARGET = Path(sys.argv[1]) if len(sys.argv) > 1 else Path({repr(str(backup_target.resolve()))})
RETENTION_DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else {int(retention_days)}

def main():
    BACKUP_TARGET.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    host = socket.gethostname().lower()
    archive = BACKUP_TARGET / f"gitea-backup-{{host}}-{{ts}}.zip"
    tmp_dir = Path(tempfile.mkdtemp(prefix="gitea_dump_"))
    try:
        subprocess.run(
            [str(GITEA_BIN), "dump", "--config", str(CONFIG_PATH), "--work-path", str(INSTALL_PATH), "--tempdir", str(tmp_dir), "--file", str(archive)],
            check=True,
        )
        print(f"Created Gitea backup archive: {{archive}}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if RETENTION_DAYS > 0:
        cutoff = datetime.now().timestamp() - (RETENTION_DAYS * 86400)
        for old_zip in BACKUP_TARGET.glob("gitea-backup-*.zip"):
            try:
                if old_zip.stat().st_mtime < cutoff:
                    old_zip.unlink()
                    print(f"Pruned old backup archive: {{old_zip.name}}")
            except Exception:
                pass

if __name__ == "__main__":
    main()
'''
    backup_script.write_text(script_code, encoding="utf-8")
    try:
        backup_script.chmod(backup_script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception:
        pass

    if sys.platform.startswith("linux") and shutil.which("systemctl"):
        systemd_user_dir = Path.home() / ".config" / "systemd" / "user"
        systemd_user_dir.mkdir(parents=True, exist_ok=True)
        svc_file = systemd_user_dir / "gitea-backup.service"
        tmr_file = systemd_user_dir / "gitea-backup.timer"
        svc_file.write_text(
            "[Unit]\nDescription=Daily Antigravity Gitea Online Backup\n\n"
            "[Service]\nType=oneshot\n"
            f'ExecStart="{sys.executable}" "{backup_script}" "{backup_target}" {retention_days}\n',
            encoding="utf-8",
        )
        tmr_file.write_text(
            "[Unit]\nDescription=Daily Antigravity Gitea Backup Timer\n\n"
            "[Timer]\n"
            f"OnCalendar=*-*-* {backup_time}:00\n"
            "Persistent=true\n\n"
            "[Install]\nWantedBy=timers.target\n",
            encoding="utf-8",
        )
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "gitea-backup.timer"], capture_output=True)
        print(f"      Registered systemd --user timer 'gitea-backup.timer' at {backup_time} -> {backup_target}")


def backup_existing_state(db_path: Path) -> Dict:
    """Exports runtime_config and user-learned rules before database upgrade."""
    state = {"runtime_config": {}, "learned_rules": []}
    if not db_path.exists():
        return state
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        try:
            cur.execute("SELECT key, value, updated_at FROM runtime_config")
            for k, v, ts in cur.fetchall():
                state["runtime_config"][k] = [v, ts]
        except Exception:
            pass
        try:
            cur.execute(
                "SELECT key, name, category, scope, is_active, summary, content, token_estimate, updated_at "
                "FROM rules WHERE category = 'learned'"
            )
            for row in cur.fetchall():
                state["learned_rules"].append(list(row))
        except Exception:
            pass
        conn.close()
    except Exception as err:
        print(f"Notice: Could not export previous state: {err}")
    return state


def restore_existing_state(db_path: Path, state: Dict) -> None:
    """Restores runtime_config and user-learned rules into newly deployed database."""
    if not db_path.exists() or (not state.get("runtime_config") and not state.get("learned_rules")):
        return
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    for k, entry in state.get("runtime_config", {}).items():
        if isinstance(entry, list) and len(entry) >= 2:
            v, ts = entry[0], entry[1]
        else:
            v, ts = entry, None
        if v and v not in ("{}", "[]"):
            if ts:
                cur.execute(
                    "INSERT INTO runtime_config (key, value, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (k, v, ts),
                )
            else:
                cur.execute(
                    "INSERT INTO runtime_config (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
                    (k, v),
                )
    learned_rules = state.get("learned_rules", [])
    for r in learned_rules:
        cur.execute(
            "INSERT OR REPLACE INTO rules (key, name, category, scope, is_active, summary, content, token_estimate, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(r),
        )
    if learned_rules:
        cur.execute("INSERT INTO rules_fts(rules_fts) VALUES ('rebuild');")
    conn.commit()
    conn.close()
    print(
        f"      Restored existing runtime configuration, Gitea token, learned workspaces, "
        f"and {len(learned_rules)} user-learned rules."
    )


def install_antigraviton(
    gitea_install_path: str = "",
    gitea_port: int = 3000,
    gitea_admin_user: str = "",
    gitea_admin_password: str = "",
    backup_target_path: str = "",
    backup_time: str = "03:00",
    backup_retention_days: int = 14,
    skip_gitea_service: bool = False,
    sync_all_workspaces: bool = False,
) -> None:
    suite_root = Path(__file__).parent.resolve()
    version_file = suite_root / "VERSION"
    suite_version = version_file.read_text(encoding="utf-8-sig").strip() if version_file.exists() else "1.4.11"
    user_home = Path.home()
    gemini_dir = user_home / ".gemini"
    target_db_path = gemini_dir / "database" / "customizations.db"
    target_mgr_path = gemini_dir / "database" / "db_manager.py"
    admin_user = gitea_admin_user or os.environ.get("USER") or os.environ.get("USERNAME") or getpass.getuser()

    # On Windows, if PowerShell 7 / deploy-gitea.ps1 is preferred, we can still run the full Python pipeline
    # or delegate service creation cleanly.
    print("=" * 60)
    print(f"AntiGraviton Complete Suite Installer v{suite_version} (Python Cross-Platform)")
    print(f"Platform     : {sys.platform} ({platform.machine()})")
    print(f"Source Suite : {suite_root}")
    print(f"Target Profile: {gemini_dir}")
    print("=" * 60)

    # STEP 1: Backup existing state
    had_prev_db = target_db_path.exists()
    if had_prev_db:
        print("[1/5] Backing up existing runtime configuration, learned workspaces, and user-learned rules...")
        preserved_state = backup_existing_state(target_db_path)
    else:
        print("[1/5] Clean installation detected (no previous customizations.db).")
        preserved_state = {"runtime_config": {}, "learned_rules": []}

    # STEP 2: Deploy SkillsDB Core & Antigravity Plugin
    print("[2/5] Deploying SkillsDB Engine (v3.7.11) and customizations-db Plugin...")
    deploy_py = suite_root / "deploy.py"
    subprocess.run([sys.executable, str(deploy_py), "--source", str(suite_root), "--target", str(gemini_dir)], check=True)
    ensure_posix_shell_path(gemini_dir / "antigravity" / "bin")
    if os.name == "nt":
        subprocess.run([sys.executable, str(target_mgr_path), "fix-utf8"], capture_output=True)
    if had_prev_db:
        restore_existing_state(target_db_path, preserved_state)

    # STEP 3: Provision or Verify Local Gitea Service
    if not skip_gitea_service:
        print("[3/5] Checking Antigravity Gitea Service (v1.1.2)...")
        if not gitea_install_path:
            if os.name == "nt":
                # Check if existing Windows service defines path
                try:
                    ps_check = subprocess.run(
                        ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Service -Filter \"Name='gitea'\").PathName"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    raw_p = ps_check.stdout.strip()
                    if raw_p:
                        m = re.match(r'^"([^"]+)"', raw_p)
                        exe_p = m.group(1) if m else raw_p.split(" ")[0]
                        gitea_install_path = str(Path(exe_p).parent)
                except Exception:
                    pass
            if not gitea_install_path:
                gitea_install_path = str(user_home / "Gitea")

        install_dir = Path(gitea_install_path).expanduser().resolve()
        print(f"      Target Gitea Service Path: {install_dir}")
        for sub in ("scripts", "custom/conf", "data/gitea-repositories", "data/avatars", "log"):
            (install_dir / sub).mkdir(parents=True, exist_ok=True)

        suite_gitea_dir = suite_root / "gitea-service"
        if suite_gitea_dir.exists() and suite_gitea_dir.resolve() != install_dir:
            for item_rel in ("scripts/deploy-gitea.ps1", "scripts/backup-gitea.ps1", "custom/conf/app.example.ini"):
                src_item = suite_gitea_dir / item_rel
                if src_item.exists():
                    shutil.copy2(src_item, install_dir / item_rel)

        bin_name = "gitea.exe" if os.name == "nt" else "gitea"
        gitea_bin = install_dir / bin_name
        if not gitea_bin.exists():
            dl_url = get_gitea_download_url(GITEA_VERSION)
            print(f"      {bin_name} not found at {gitea_bin}. Downloading {dl_url} ...")
            req = urllib.request.Request(dl_url, headers={"User-Agent": "AntiGraviton-Installer/1.1"})
            with urllib.request.urlopen(req, timeout=120) as resp, open(gitea_bin, "wb") as out_f:
                shutil.copyfileobj(resp, out_f)
            if os.name != "nt":
                gitea_bin.chmod(gitea_bin.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            print(f"      Downloaded Gitea binary to {gitea_bin}")

        config_path = install_dir / "custom" / "conf" / "app.ini"
        example_path = install_dir / "custom" / "conf" / "app.example.ini"
        gitea_db = install_dir / "data" / "gitea.db"

        if not config_path.exists() and example_path.exists():
            sec_key = int_tok = jwt_sec = ""
            try:
                sec_key = subprocess.run([str(gitea_bin), "generate", "secret", "SECRET_KEY"], capture_output=True, text=True, timeout=5).stdout.strip().splitlines()[-1]
                int_tok = subprocess.run([str(gitea_bin), "generate", "secret", "INTERNAL_TOKEN"], capture_output=True, text=True, timeout=5).stdout.strip().splitlines()[-1]
                jwt_sec = subprocess.run([str(gitea_bin), "generate", "secret", "JWT_SECRET"], capture_output=True, text=True, timeout=5).stdout.strip().splitlines()[-1]
            except Exception:
                pass
            sec_key = sec_key or (uuid.uuid4().hex + uuid.uuid4().hex)
            int_tok = int_tok or (uuid.uuid4().hex + uuid.uuid4().hex)
            jwt_sec = jwt_sec or (uuid.uuid4().hex + uuid.uuid4().hex)

            run_usr = "SYSTEM" if os.name == "nt" else admin_user
            ini_text = generate_gitea_app_ini(
                example_path.read_text(encoding="utf-8"),
                install_dir,
                gitea_port,
                run_usr,
                sec_key,
                int_tok,
                jwt_sec,
            )
            config_path.write_text(ini_text, encoding="utf-8")
            print(f"      Generated {config_path} with fresh cryptographic secrets.")

        if not gitea_db.exists() and config_path.exists():
            subprocess.run([str(gitea_bin), "migrate", "--config", str(config_path), "--work-path", str(install_dir)], capture_output=True)

        if os.name == "nt":
            # Delegate Windows Service check/start to PowerShell helper
            deploy_ps1 = install_dir / "scripts" / "deploy-gitea.ps1"
            svc_q = subprocess.run(["sc.exe", "query", "gitea"], capture_output=True, text=True)
            if svc_q.returncode == 0:
                if "RUNNING" not in svc_q.stdout:
                    subprocess.run(["sc.exe", "start", "gitea"], capture_output=True)
                    time.sleep(2)
                print("      Gitea Windows Service Status: Running")
            elif deploy_ps1.exists():
                shell_exe = shutil.which("pwsh") or "powershell.exe"
                subprocess.run([shell_exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(deploy_ps1), "-InstallPath", str(install_dir), "-HttpPort", str(gitea_port)])
        elif sys.platform.startswith("linux"):
            started = False
            if shutil.which("systemctl"):
                systemd_user_dir = user_home / ".config" / "systemd" / "user"
                systemd_user_dir.mkdir(parents=True, exist_ok=True)
                unit_file = systemd_user_dir / "gitea.service"
                unit_file.write_text(generate_systemd_user_unit(install_dir, gitea_bin, config_path), encoding="utf-8")
                subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
                res = subprocess.run(["systemctl", "--user", "enable", "--now", "gitea.service"], capture_output=True, text=True)
                if res.returncode == 0:
                    started = True
                    if shutil.which("loginctl"):
                        subprocess.run(["loginctl", "enable-linger", admin_user], capture_output=True)
                    print("      Registered and started rootless systemd --user service 'gitea.service'.")
            if not started:
                # Fallback for containers / WSL without systemd
                pg = subprocess.run(["pgrep", "-f", "gitea.*web"], capture_output=True, text=True)
                if pg.returncode != 0 or not pg.stdout.strip():
                    log_f = open(install_dir / "log" / "gitea-daemon.log", "a", encoding="utf-8")
                    subprocess.Popen(
                        [str(gitea_bin), "web", "--config", str(config_path), "--work-path", str(install_dir)],
                        cwd=str(install_dir),
                        stdout=log_f,
                        stderr=log_f,
                        start_new_session=True,
                    )
                    time.sleep(2)
                print("      Gitea background daemon started.")
        elif sys.platform == "darwin":
            agents_dir = user_home / "Library" / "LaunchAgents"
            agents_dir.mkdir(parents=True, exist_ok=True)
            plist_file = agents_dir / "dev.local.antigraviton.gitea.plist"
            plist_file.write_text(generate_launchd_plist(install_dir, gitea_bin, config_path), encoding="utf-8")
            subprocess.run(["launchctl", "unload", str(plist_file)], capture_output=True)
            subprocess.run(["launchctl", "load", "-w", str(plist_file)], capture_output=True)
            print("      Registered and started macOS LaunchAgent 'dev.local.antigraviton.gitea'.")

        if backup_target_path:
            if os.name == "nt":
                b_ps1 = install_dir / "scripts" / "backup-gitea.ps1"
                shell_exe = shutil.which("pwsh") or "powershell.exe"
                if b_ps1.exists():
                    subprocess.run(
                        [shell_exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(b_ps1), "-InstallPath", str(install_dir), "-BackupTargetPath", backup_target_path, "-RegisterScheduledTask", "-TaskTime", backup_time]
                    )
            else:
                configure_posix_backup_schedule(install_dir, gitea_bin, config_path, Path(backup_target_path), backup_time, backup_retention_days)
    else:
        print("[3/5] Skipping Gitea service provisioning (--skip-gitea-service).")
        install_dir = Path(gitea_install_path or (user_home / "Gitea"))

    # STEP 4: Provision Local Gitea Admin User & API Token
    print("[4/5] Verifying SkillsDB <-> Gitea Synchronization Configuration...")
    conn = sqlite3.connect(target_db_path)
    tok_row = conn.execute("SELECT value FROM runtime_config WHERE key='gitea_token'").fetchone()
    usr_row = conn.execute("SELECT value FROM runtime_config WHERE key='gitea_user'").fetchone()
    conn.close()
    has_token = bool(tok_row and tok_row[0])
    existing_user = usr_row[0] if usr_row and usr_row[0] else ""

    if not skip_gitea_service:
        bin_name = "gitea.exe" if os.name == "nt" else "gitea"
        gitea_bin = install_dir / bin_name
        config_path = install_dir / "custom" / "conf" / "app.ini"
        gitea_db = install_dir / "data" / "gitea.db"

        if gitea_bin.exists() and config_path.exists():
            user_list = subprocess.run(
                [str(gitea_bin), "admin", "user", "list", "--config", str(config_path), "--work-path", str(install_dir)],
                capture_output=True,
                text=True,
            ).stdout
            user_exists = re.search(rf"\b{re.escape(admin_user)}\b", user_list) is not None
            local_email = f"{admin_user}@local.dev"

            if not user_exists:
                cmd = [
                    str(gitea_bin), "admin", "user", "create",
                    "--username", admin_user,
                    "--email", local_email,
                    "--admin",
                    "--must-change-password=false",
                    "--config", str(config_path),
                    "--work-path", str(install_dir),
                ]
                if gitea_admin_password:
                    cmd.extend(["--password", gitea_admin_password])
                else:
                    cmd.append("--random-password")
                subprocess.run(cmd, capture_output=True)
                print(f"      Created local Gitea administrator account '{admin_user}' ({local_email}).")
            elif gitea_admin_password:
                subprocess.run(
                    [
                        str(gitea_bin), "admin", "user", "change-password",
                        "--username", admin_user,
                        "--password", gitea_admin_password,
                        "--must-change-password=false",
                        "--config", str(config_path),
                        "--work-path", str(install_dir),
                    ],
                    capture_output=True,
                )
                print(f"      Updated password for local Gitea administrator account '{admin_user}'.")

            if not has_token or (existing_user and existing_user != admin_user):
                t_name = f"antigraviton-{platform.node().lower()}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
                tok_out = subprocess.run(
                    [
                        str(gitea_bin), "admin", "user", "generate-access-token",
                        "--username", admin_user,
                        "--token-name", t_name,
                        "--scopes", "all",
                        "--raw",
                        "--config", str(config_path),
                        "--work-path", str(install_dir),
                    ],
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                raw_token = tok_out.splitlines()[-1].strip() if tok_out else ""
                if len(raw_token) >= 20:
                    subprocess.run(
                        [sys.executable, str(target_mgr_path), "gitea-config", "--url", f"http://127.0.0.1:{gitea_port}", "--user", admin_user, "--token", raw_token],
                        capture_output=True,
                    )
                    print(f"      Automatically provisioned and registered Gitea API token for user '{admin_user}'.")
            else:
                subprocess.run([sys.executable, str(target_mgr_path), "gitea-config"])

    # STEP 5: Verification Gate & Telemetry
    print("[5/5] Running final health checks and workspace learning...")
    if not target_db_path.exists():
        raise RuntimeError(f"Verification failed: Central database not found at {target_db_path}")

    if not skip_gitea_service:
        api_online = False
        health_url = f"http://127.0.0.1:{gitea_port}/api/healthz"
        for _ in range(6):
            try:
                with urllib.request.urlopen(health_url, timeout=3) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("status") == "pass":
                        api_online = True
                        break
            except Exception:
                time.sleep(1)
        if not api_online:
            raise RuntimeError(f"Verification failed: Gitea HTTP health check at {health_url} did not pass.")

    subprocess.run([sys.executable, str(target_mgr_path), "mem-init", "--project", str(suite_root)], capture_output=True)
    subprocess.run([sys.executable, str(target_mgr_path), "stats"])

    if sync_all_workspaces:
        print("      Synchronizing all discovered Antigravity workspaces...")
        subprocess.run([sys.executable, str(target_mgr_path), "gitea-sync", "--all"])

    print("")
    print("=" * 60)
    print(f"AntiGraviton Complete Suite v{suite_version} is active!")
    print("=" * 60)
    print(f"   - Local Gitea Service : http://127.0.0.1:{gitea_port} (Owner: {admin_user})")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Unified Cross-Platform Installer for AntiGraviton Complete Suite.")
    parser.add_argument("--gitea-install-path", default="", help="Target directory for local Gitea service (defaults to ~/Gitea).")
    parser.add_argument("--gitea-port", type=int, default=3000, help="Local loopback HTTP port for Gitea (default: 3000).")
    parser.add_argument("--gitea-admin-user", default="", help="Local administrator username for Gitea (defaults to OS user).")
    parser.add_argument("--gitea-admin-password", default="", help="Optional explicit password for local Gitea administrator.")
    parser.add_argument("--backup-target-path", default="", help="Optional destination path for daily 03:00 Gitea dump backups.")
    parser.add_argument("--backup-time", default="03:00", help="Daily backup execution time in HH:MM format (default: 03:00).")
    parser.add_argument("--backup-retention-days", type=int, default=14, help="Days to retain backup archives (default: 14).")
    parser.add_argument("--skip-gitea-service", action="store_true", help="Deploy only SkillsDB and plugin without Gitea service.")
    parser.add_argument("--sync-all-workspaces", action="store_true", help="Immediately mirror all discovered workspaces to Gitea.")
    args = parser.parse_args()

    install_antigraviton(
        gitea_install_path=args.gitea_install_path,
        gitea_port=args.gitea_port,
        gitea_admin_user=args.gitea_admin_user,
        gitea_admin_password=args.gitea_admin_password,
        backup_target_path=args.backup_target_path,
        backup_time=args.backup_time,
        backup_retention_days=args.backup_retention_days,
        skip_gitea_service=args.skip_gitea_service,
        sync_all_workspaces=args.sync_all_workspaces,
    )


if __name__ == "__main__":
    main()
