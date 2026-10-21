"""
Gitea synchronization and automated Git commit tracking for SkillsDB.
Seamlessly integrates Antigravity workspaces with local Gitea service.
"""

import os
import sys
import re
import time
import json
import shutil
import sqlite3
import tempfile
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

from ..config import (
    DB_PATH,
    GEMINI_DIR,
    GITEA_DEFAULT_URL,
    GITEA_DEFAULT_USER,
    GITEA_DEFAULT_TOKEN,
    GITEA_SERVICE_NAME,
)


def _is_temp_workspace(project_root: Path) -> bool:
    """Returns True if project_root is inside the OS temporary directory."""
    try:
        temp_root = Path(tempfile.gettempdir()).resolve()
        resolved = project_root.resolve()
        return resolved == temp_root or temp_root in resolved.parents
    except Exception:
        return False


def _is_excluded_workspace_path(project_root: Path) -> bool:
    """
    Returns True if project_root is inside OS temp, user home root, or protected
    ~/.gemini subdirectories (config, database, plugins_archive, or scratch root).
    """
    if not project_root or _is_temp_workspace(project_root):
        return True
    try:
        abs_root = Path(os.path.abspath(str(project_root)))
        home_abs = Path(os.path.abspath(str(Path.home())))
        gemini_abs = Path(os.path.abspath(str(GEMINI_DIR)))
        scratch_abs = gemini_abs / "antigravity" / "scratch"
        if abs_root in (home_abs, gemini_abs, scratch_abs):
            return True
        for excluded_sub in (
            gemini_abs / "config",
            gemini_abs / "database",
            gemini_abs / "plugins_archive",
        ):
            try:
                abs_root.relative_to(excluded_sub)
                return True
            except ValueError:
                pass
        return False
    except Exception:
        return True


def learn_workspace(project_root: Path, db_path: Path = DB_PATH) -> Optional[str]:
    """
    Organically learns and persists a project workspace path on demand into runtime_config.
    If the project moves to a new drive or folder, opening it in Antigravity automatically
    updates 'workspace:<name>' to the new location. Also auto-learns Gitea credentials
    from .git/config if runtime_config is empty on a fresh machine.
    """
    if not project_root or _is_excluded_workspace_path(project_root):
        return None
    try:
        abs_root = Path(os.path.abspath(str(project_root)))
        if not abs_root.exists() or not abs_root.is_dir():
            return None
        if not ((abs_root / ".agents").exists() or (abs_root / ".git" / "HEAD").exists()):
            return None
        ws_key = f"workspace:{abs_root.name}"
        now_iso = datetime.now(timezone.utc).isoformat()

        if db_path:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(db_path, timeout=3.0)
            cur = conn.cursor()
            cur.execute(
                "CREATE TABLE IF NOT EXISTS runtime_config (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT);"
            )
            scratch_abs = Path(os.path.abspath(str(GEMINI_DIR / "antigravity" / "scratch")))
            try:
                abs_root.relative_to(scratch_abs)
                new_is_scratch = True
            except ValueError:
                new_is_scratch = False

            cur.execute(
                "SELECT key, value FROM runtime_config WHERE LOWER(key) = LOWER(?);",
                (ws_key,),
            )
            existing_rows = cur.fetchall()
            skip_insert = False
            for ex_key, ex_val in existing_rows:
                if new_is_scratch and ex_val and Path(ex_val).exists():
                    try:
                        Path(os.path.abspath(str(ex_val))).relative_to(scratch_abs)
                    except ValueError:
                        skip_insert = True
                        break
                if ex_key != ws_key:
                    cur.execute("DELETE FROM runtime_config WHERE key = ?;", (ex_key,))

            if not skip_insert:
                cur.execute(
                    "INSERT OR REPLACE INTO runtime_config (key, value, updated_at) VALUES (?, ?, ?);",
                    (ws_key, str(abs_root), now_iso),
                )

            # If Gitea credentials are not yet learned on this machine, inspect .git/config
            cur.execute("SELECT COUNT(*) FROM runtime_config WHERE key = 'gitea_token';")
            has_token = cur.fetchone()[0] > 0
            git_cfg = abs_root / ".git" / "config"
            if not has_token and git_cfg.exists():
                raw_cfg = git_cfg.read_text(encoding="utf-8", errors="ignore")
                import re
                m = re.search(r"url\s*=\s*(https?://([^:@/\s]+):([^@/\s]+)@([^/\s]+)/([^/\s]+)/[^\s]+)", raw_cfg)
                if m:
                    _, auth_user, auth_tok, host_port, repo_owner = m.groups()
                    scheme = "https" if m.group(1).startswith("https://") else "http"
                    learned_url = f"{scheme}://{host_port}"
                    cur.executemany(
                        "INSERT OR IGNORE INTO runtime_config (key, value, updated_at) VALUES (?, ?, ?);",
                        [
                            ("gitea_url", learned_url, now_iso),
                            ("gitea_user", repo_owner, now_iso),
                            ("gitea_token", auth_tok, now_iso),
                        ],
                    )

            conn.commit()
            conn.close()
        return str(abs_root)
    except Exception:
        return None


def get_learned_workspaces(db_path: Path = DB_PATH, prune_missing: bool = True) -> Dict[str, str]:
    """
    Returns all organically learned project workspaces (name -> path) from runtime_config.
    Automatically prunes:
    1. Protected non-workspace paths (e.g. ~/.gemini/config/plugins/*, ~, temp dirs) left over from older versions
    2. Case-insensitive duplicate workspace keys (keeping the most recently updated or primary workspace path)
    3. Deleted project folders when the parent drive/directory is reachable
    """
    learned: Dict[str, str] = {}
    stale_keys: List[str] = []
    if not db_path or not Path(db_path).exists():
        return learned

    try:
        conn = sqlite3.connect(db_path, timeout=3.0)
        cur = conn.cursor()
        cur.execute("SELECT key, value, updated_at FROM runtime_config WHERE key LIKE 'workspace:%' ORDER BY updated_at DESC;")
        rows = cur.fetchall()
        seen_lower: Dict[str, str] = {}
        scratch_abs = Path(os.path.abspath(str(GEMINI_DIR / "antigravity" / "scratch")))

        for k, v, _ in rows:
            name = k.split("workspace:", 1)[1]
            if not v:
                stale_keys.append(k)
                continue
            p = Path(v)
            if _is_excluded_workspace_path(p):
                stale_keys.append(k)
                # Clean up any stray .agents folder created inside ~/.gemini/config/plugins/* by pre-v1.0.3 hooks
                try:
                    gemini_cfg = Path(os.path.abspath(str(GEMINI_DIR / "config")))
                    Path(os.path.abspath(str(p))).relative_to(gemini_cfg)
                    for stray_sub in (".agents", ".git"):
                        stray_path = p / stray_sub
                        if stray_path.exists():
                            _clear_readonly_in_git(p)
                            shutil.rmtree(stray_path, ignore_errors=True)
                except Exception:
                    pass
                continue

            if p.exists():
                lower_name = name.lower()
                if lower_name in seen_lower:
                    # If one entry is in scratch and the other is a full workspace, keep the non-scratch workspace
                    existing_key = seen_lower[lower_name]
                    existing_name = existing_key.split("workspace:", 1)[1]
                    existing_path = Path(learned[existing_name])
                    try:
                        Path(os.path.abspath(str(existing_path))).relative_to(scratch_abs)
                        existing_in_scratch = True
                    except ValueError:
                        existing_in_scratch = False
                    try:
                        Path(os.path.abspath(str(p))).relative_to(scratch_abs)
                        current_in_scratch = True
                    except ValueError:
                        current_in_scratch = False

                    if existing_in_scratch and not current_in_scratch:
                        stale_keys.append(existing_key)
                        del learned[existing_name]
                        seen_lower[lower_name] = k
                        learned[name] = v
                    else:
                        stale_keys.append(k)
                    continue

                seen_lower[lower_name] = k
                learned[name] = v
            elif prune_missing and p.parent.exists():
                # Parent directory is reachable, so the project folder itself was genuinely removed
                stale_keys.append(k)

        if stale_keys:
            cur.executemany("DELETE FROM runtime_config WHERE key = ?;", [(sk,) for sk in stale_keys])
            conn.commit()
        conn.close()
    except Exception:
        pass
    return learned


def get_gitea_config(db_path: Path = DB_PATH) -> Dict[str, str]:
    """
    Retrieves active Gitea configuration with priority:
    1. Environment variables (SKILLSDB_GITEA_*)
    2. Learned runtime_config table in customizations.db
    3. Safe generic defaults from config.py (zero hardcoded paths at release)
    """
    db_cfg: Dict[str, str] = {}
    if db_path and Path(db_path).exists():
        try:
            conn = sqlite3.connect(db_path, timeout=3.0)
            cur = conn.cursor()
            cur.execute(
                "SELECT key, value FROM runtime_config WHERE key IN ('gitea_url', 'gitea_user', 'gitea_token', 'gitea_workspaces_root', 'remote_share_path');"
            )
            for k, v in cur.fetchall():
                if v:
                    db_cfg[k] = v
            conn.close()
        except Exception:
            pass

    url = (
        os.environ.get("SKILLSDB_GITEA_URL")
        or db_cfg.get("gitea_url")
        or GITEA_DEFAULT_URL
    ).rstrip("/")
    user = (
        os.environ.get("SKILLSDB_GITEA_USER")
        or db_cfg.get("gitea_user")
        or GITEA_DEFAULT_USER
    )
    token = (
        os.environ.get("SKILLSDB_GITEA_TOKEN")
        or db_cfg.get("gitea_token")
        or GITEA_DEFAULT_TOKEN
    )
    workspaces_root = (
        os.environ.get("SKILLSDB_WORKSPACES_ROOT")
        or db_cfg.get("gitea_workspaces_root")
        or ""
    )
    remote_share = (
        os.environ.get("SKILLSDB_REMOTE_SHARE")
        or db_cfg.get("remote_share_path")
        or ""
    )

    return {
        "url": url,
        "user": user,
        "token": token,
        "workspaces_root": workspaces_root,
        "remote_share": remote_share,
        "service_name": GITEA_SERVICE_NAME,
    }


def set_gitea_config(
    url: Optional[str] = None,
    user: Optional[str] = None,
    token: Optional[str] = None,
    workspaces_root: Optional[str] = None,
    remote_share: Optional[str] = None,
    db_path: Path = DB_PATH,
) -> Dict[str, str]:
    """Persists Gitea and workspace settings in the runtime_config table of customizations.db."""
    now_iso = datetime.now(timezone.utc).isoformat()
    updates = []
    if url is not None:
        updates.append(("gitea_url", url.rstrip("/"), now_iso))
    if user is not None:
        updates.append(("gitea_user", user.strip(), now_iso))
    if token is not None:
        updates.append(("gitea_token", token.strip(), now_iso))
    if workspaces_root is not None:
        updates.append(("gitea_workspaces_root", str(workspaces_root).strip(), now_iso))
    if remote_share is not None:
        updates.append(("remote_share_path", str(remote_share).strip(), now_iso))

    if updates and db_path:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=5.0)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS runtime_config (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT);"
        )
        conn.executemany(
            "INSERT OR REPLACE INTO runtime_config (key, value, updated_at) VALUES (?, ?, ?);",
            updates,
        )
        conn.commit()
        conn.close()

    return get_gitea_config(db_path=db_path)


def gitea_config_summary(auto_detect: bool = False, db_path: Path = DB_PATH):
    """Displays active Gitea configuration and organically learned project paths."""
    if auto_detect:
        discover_all_workspaces(db_path=db_path)

    cfg = get_gitea_config(db_path=db_path)
    learned = get_learned_workspaces(db_path=db_path, prune_missing=True)
    tok = cfg.get("token", "")
    masked_tok = f"{tok[:6]}...{tok[-4:]}" if len(tok) > 10 else ("(set)" if tok else "(not set)")

    print("=" * 60)
    print("SkillsDB - Self-Learning Gitea & Workspace Configuration")
    print("=" * 60)
    print(f"Gitea URL:          {cfg.get('url')}")
    print(f"Repository Owner:   {cfg.get('user') or '(auto-learned on init)'}")
    print(f"API Token:          {masked_tok}")
    print(f"Learned Workspaces: {len(learned)} projects tracked on demand")
    for name, path_str in sorted(learned.items(), key=lambda x: x[0].lower()):
        print(f"  * {name:30} -> {path_str}")
    print("=" * 60)


def check_gitea_health(timeout: float = 3.0) -> Tuple[bool, Dict[str, Any]]:
    """Checks whether the Gitea service is online and returns version info."""
    config = get_gitea_config()
    version_url = f"{config['url']}/api/v1/version"
    headers = {"User-Agent": "SkillsDB-GiteaSync/1.0"}
    if config.get("token"):
        headers["Authorization"] = f"token {config['token']}"
    req = urllib.request.Request(
        version_url,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return True, data
    except Exception as e:
        return False, {"error": str(e)}
    return False, {"error": "Unknown status"}


def get_gitea_service_status() -> str:
    """Checks service state for Gitea across Windows (sc.exe), Linux (systemd/pgrep), and macOS (launchctl/pgrep)."""
    config = get_gitea_config()
    svc_name = config["service_name"]
    if os.name == "nt":
        try:
            proc = subprocess.run(
                ["sc.exe", "query", svc_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in proc.stdout.splitlines():
                if "STATE" in line:
                    parts = line.strip().split()
                    if len(parts) >= 4:
                        return parts[3]  # e.g., RUNNING, STOPPED
                    return line.strip()
            return "NOT_FOUND" if proc.returncode != 0 else "UNKNOWN"
        except Exception:
            return "UNKNOWN"

    try:
        if sys.platform.startswith("linux") and shutil.which("systemctl"):
            for cmd in (
                ["systemctl", "--user", "is-active", svc_name],
                ["systemctl", "is-active", svc_name],
            ):
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
                state = proc.stdout.strip().lower()
                if state == "active":
                    return "RUNNING"
                if state in ("activating", "reloading"):
                    return "START_PENDING"
                if state in ("inactive", "failed", "deactivating"):
                    user_unit = Path.home() / ".config" / "systemd" / "user" / f"{svc_name}.service"
                    sys_unit = Path(f"/etc/systemd/system/{svc_name}.service")
                    if user_unit.exists() or sys_unit.exists():
                        return "STOPPED"
        if shutil.which("pgrep"):
            pg = subprocess.run(["pgrep", "-f", "gitea.*web"], capture_output=True, text=True, timeout=3)
            if pg.returncode == 0 and pg.stdout.strip():
                return "RUNNING"
        return "NOT_FOUND"
    except Exception:
        return "UNKNOWN"


def flush_all_pending_pushes(db_path: Path = DB_PATH) -> List[str]:
    """
    Scans all organically learned workspaces for unpushed local commits (created while
    Gitea or the fileserver was temporarily offline) and pushes them to Gitea.
    Returns the list of workspace names that were successfully flushed.
    """
    flushed: List[str] = []
    learned = get_learned_workspaces(db_path=db_path, prune_missing=False)
    for name, path_str in sorted(learned.items(), key=lambda x: x[0].lower()):
        try:
            ws_path = Path(path_str)
            if ws_path.exists() and (ws_path / ".git" / "HEAD").exists() and not _is_temp_workspace(ws_path):
                if _has_unpushed_commits(ws_path):
                    if _push_to_origin(ws_path, async_push=False, _allow_retry=False):
                        flushed.append(name)
        except Exception:
            continue
    return flushed


def ensure_gitea_online(
    auto_recover: bool = True,
    flush_pending: bool = False,
    db_path: Path = DB_PATH,
) -> Dict[str, Any]:
    """
    Verifies Gitea service and API health. If the Windows Service is STOPPED or the API
    is unreachable (e.g. after a brief fileserver disconnect), automatically attempts
    to start the service and optionally flushes pending commits across all workspaces.
    """
    svc_status = get_gitea_service_status()
    online, v_info = check_gitea_health(timeout=1.5)
    recovered = False
    flushed_repos: List[str] = []

    if auto_recover and (not online or svc_status in ("STOPPED", "STOP_PENDING")):
        config = get_gitea_config(db_path=db_path)
        svc_name = config["service_name"]
        if os.name == "nt":
            try:
                if svc_status in ("STOPPED", "STOP_PENDING") and online:
                    subprocess.run(["taskkill", "/F", "/IM", "gitea.exe"], capture_output=True, timeout=5)
                    time.sleep(0.5)
                start_res = subprocess.run(
                    ["sc.exe", "start", svc_name],
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
                if start_res.returncode == 0 or "1056" in start_res.stdout:
                    for _ in range(6):
                        time.sleep(0.8)
                        online, v_info = check_gitea_health(timeout=1.0)
                        if online:
                            recovered = True
                            break
                    svc_status = get_gitea_service_status()
            except Exception:
                pass
        else:
            try:
                started = False
                if sys.platform.startswith("linux") and shutil.which("systemctl"):
                    res = subprocess.run(
                        ["systemctl", "--user", "start", svc_name],
                        capture_output=True,
                        text=True,
                        timeout=8,
                    )
                    started = res.returncode == 0
                elif sys.platform == "darwin" and shutil.which("launchctl"):
                    plist = Path.home() / "Library" / "LaunchAgents" / "dev.local.antigraviton.gitea.plist"
                    if plist.exists():
                        res = subprocess.run(["launchctl", "load", "-w", str(plist)], capture_output=True, text=True, timeout=8)
                        started = res.returncode == 0
                if started:
                    for _ in range(6):
                        time.sleep(0.8)
                        online, v_info = check_gitea_health(timeout=1.0)
                        if online:
                            recovered = True
                            break
                    svc_status = get_gitea_service_status()
            except Exception:
                pass

    if online and (flush_pending or recovered):
        flushed_repos = flush_all_pending_pushes(db_path=db_path)

    return {
        "online": online,
        "service_status": svc_status,
        "recovered": recovered,
        "flushed_repos": flushed_repos,
        "version": v_info.get("version") if online else None,
        "error": None if online else v_info.get("error", "Gitea service unreachable"),
    }


def inspect_remote_repository(repo_name: str) -> Dict[str, Any]:
    """Checks whether a repository with repo_name already exists on Gitea and returns its metadata."""
    config = get_gitea_config()
    token = config["token"]
    user = config["user"]
    base_url = config["url"]
    default_html = f"{base_url}/{user}/{repo_name}" if user else base_url
    result = {
        "exists": False,
        "empty": True,
        "name": repo_name,
        "full_name": f"{user}/{repo_name}" if user else repo_name,
        "html_url": default_html,
        "clone_url": None,
    }
    if not token or not user:
        return result

    check_url = f"{base_url}/api/v1/repos/{user}/{repo_name}"
    req = urllib.request.Request(
        check_url,
        headers={
            "Authorization": f"token {token}",
            "User-Agent": "SkillsDB-GiteaSync/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                result["exists"] = True
                result["empty"] = bool(data.get("empty", False))
                result["name"] = data.get("name") or repo_name
                result["full_name"] = data.get("full_name") or result["full_name"]
                result["html_url"] = data.get("html_url") or default_html
                result["clone_url"] = _build_auth_clone_url(data.get("clone_url"), user, token)
    except Exception:
        pass
    return result


def ensure_remote_repository(repo_name: str, description: Optional[str] = None) -> Optional[str]:
    """Ensures repository exists in Gitea; creates it if missing and returns authenticated clone URL."""
    config = get_gitea_config()
    token = config["token"]
    user = config["user"]
    base_url = config["url"]

    if not token or not user:
        return None

    # 1. Check if repository already exists
    info = inspect_remote_repository(repo_name)
    if info.get("exists") and info.get("clone_url"):
        return info["clone_url"]

    # 2. Create repository if not found
    create_urls = []
    if user and user.lower() != "antigravity":
        create_urls.append(f"{base_url}/api/v1/admin/users/{user}/repos")
    create_urls.append(f"{base_url}/api/v1/user/repos")
    payload = json.dumps({
        "name": repo_name,
        "description": description or f"Antigravity workspace {repo_name}",
        "private": True,
        "auto_init": False,
        "default_branch": "main",
    }).encode("utf-8")

    for c_url in create_urls:
        create_req = urllib.request.Request(
            c_url,
            data=payload,
            headers={
                "Authorization": f"token {token}",
                "Content-Type": "application/json",
                "User-Agent": "SkillsDB-GiteaSync/1.0",
            },
        )
        try:
            with urllib.request.urlopen(create_req, timeout=5.0) as resp:
                if resp.status in (200, 201):
                    data = json.loads(resp.read().decode("utf-8"))
                    return _build_auth_clone_url(data.get("clone_url"), user, token)
        except Exception:
            continue
    return None


def delete_remote_repository(repo_name: str, owner: Optional[str] = None) -> bool:
    """Deletes a repository in Gitea via API (primarily used for cleaning up temporary test repositories)."""
    config = get_gitea_config()
    token = config["token"]
    target_owner = owner or config["user"]
    base_url = config["url"]
    if not token or not target_owner:
        return False

    del_url = f"{base_url}/api/v1/repos/{target_owner}/{repo_name}"
    req = urllib.request.Request(
        del_url,
        method="DELETE",
        headers={
            "Authorization": f"token {token}",
            "User-Agent": "SkillsDB-GiteaSync/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return resp.status in (200, 204)
    except Exception:
        return False


def _build_auth_clone_url(clone_url: Optional[str], user: str, token: str) -> Optional[str]:
    """Embeds basic auth token credentials into HTTP clone URL for headless git operations."""
    if not clone_url:
        return None
    if clone_url.startswith("http://"):
        return clone_url.replace("http://", f"http://{user}:{token}@")
    if clone_url.startswith("https://"):
        return clone_url.replace("https://", f"https://{user}:{token}@")
    return clone_url


def _get_configured_gitea_repo_url(project_root: Path) -> Optional[str]:
    """Inspects .git/config to see if origin or gitea remote points to the local Gitea server and returns its clean browser URL."""
    git_cfg = project_root / ".git" / "config"
    if not git_cfg.exists():
        return None
    try:
        raw = git_cfg.read_text(encoding="utf-8", errors="ignore")
        cfg = get_gitea_config()
        base_url = cfg.get("url", "http://localhost:3000").rstrip("/")
        for m in re.finditer(r"url\s*=\s*(https?://(?:[^@/\s]+@)?([^/\s]+)/([^/\s]+)/([^\s/]+?)(?:\.git)?)\s*$", raw, re.MULTILINE):
            _, host_port, owner, rname = m.groups()
            if "localhost:3000" in host_port or "127.0.0.1:3000" in host_port or host_port in base_url:
                return f"{base_url}/{owner}/{rname}"
    except Exception:
        pass
    return None


def get_workspace_git_telemetry(project_root: Path, flush_pending: bool = False) -> Optional[str]:
    """
    Returns a compact Git & Gitea status string (~20 tokens), auto-recovers the Gitea
    service if STOPPED, flushes any pending unpushed commits upon recovery or when
    flush_pending=True, and emits an unmissable alert if Gitea is offline or if a repository
    name collision exists on Gitea.
    """
    if not project_root or not (project_root / ".git" / "HEAD").exists():
        return None
    if not shutil.which("git"):
        return None

    extra_notices: List[str] = []
    gitea_online = False
    if not _is_temp_workspace(project_root):
        try:
            health = ensure_gitea_online(auto_recover=True, flush_pending=flush_pending)
            gitea_online = bool(health.get("online"))
            if not gitea_online:
                svc_st = health.get("service_status", "STOPPED")
                extra_notices.append(
                    f"[CRITICAL GITEA ALERT]: Local Gitea server (http://127.0.0.1:3000) is OFFLINE (Service: {svc_st}) and could not be auto-started (fileserver or service unreachable). Commits are saved locally only. YOU MUST IMMEDIATELY WARN THE USER IN YOUR RESPONSE THAT GITEA IS CURRENTLY OFFLINE!"
                )
            else:
                if health.get("recovered"):
                    extra_notices.append(
                        "[GITEA AUTO-RECOVERY]: Windows Service 'gitea' was STOPPED and has been automatically restarted by SkillsDB."
                    )
                if health.get("flushed_repos"):
                    extra_notices.append(
                        f"[GITEA CATCH-UP SYNC]: Automatically pushed pending local commits from offline window for: {', '.join(health['flushed_repos'])}."
                    )
        except Exception:
            pass

    try:
        st_res = subprocess.run(
            ["git", "status", "--porcelain", "--branch"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if st_res.returncode != 0:
            return "\n".join(extra_notices) if extra_notices else None

        lines = [l for l in st_res.stdout.splitlines() if l.strip()]
        branch_info = "main"
        sync_state = "synced"
        dirty_files = []

        configured_gitea_url = _get_configured_gitea_repo_url(project_root)

        for line in lines:
            if line.startswith("## "):
                header = line[3:].strip()
                if "..." in header:
                    branch_part, rest = header.split("...", 1)
                    branch_info = branch_part.strip()
                    if "[" in rest and "]" in rest:
                        bracket = rest[rest.find("[") + 1 : rest.find("]")]
                        sync_state = f"{bracket} | {configured_gitea_url}" if configured_gitea_url else bracket
                    else:
                        sync_state = f"synced with Gitea: {configured_gitea_url}" if configured_gitea_url else "synced with Gitea"
                else:
                    branch_info = header.split()[0] if header else "main"
                    sync_state = f"Gitea remote: {configured_gitea_url}" if configured_gitea_url else "local only"
            else:
                if len(line) > 3:
                    fpath = line[3:].strip().strip('"')
                    if " -> " in fpath:
                        fpath = fpath.split(" -> ")[-1]
                    dirty_files.append(Path(fpath).name)

        if not _is_temp_workspace(project_root) and gitea_online and not configured_gitea_url:
            rem_info = inspect_remote_repository(project_root.name)
            if rem_info.get("exists"):
                extra_notices.append(
                    f"[GITEA COLLISION ALERT]: Local workspace is not yet linked to Gitea, but a repository named '{rem_info['full_name']}' ALREADY EXISTS on Gitea ({rem_info['html_url']}). Ask the user how to proceed before linking or pushing!"
                )

        rev_res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=1.0,
        )
        short_hash = rev_res.stdout.strip() if rev_res.returncode == 0 else "unborn"

        if dirty_files:
            preview = ", ".join(dirty_files[:4])
            if len(dirty_files) > 4:
                preview += f" (+{len(dirty_files) - 4} more)"
            tree_str = f"{len(dirty_files)} modified: {preview}"
        else:
            tree_str = "clean"

        base_line = f"Git Telemetry: branch '{branch_info}' @ {short_hash} ({tree_str} | {sync_state})"
        if extra_notices:
            return "\n".join([base_line] + [f"  {n}" for n in extra_notices])
        return base_line
    except Exception:
        return "\n".join(extra_notices) if extra_notices else None


def _ensure_git_safe_directory(project_root: Path):
    """Automatically registers project_root in git global safe.directory if dubious ownership is detected (e.g. exFAT or secondary drives)."""
    chk = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
    )
    if chk.returncode != 0 and "dubious ownership" in (chk.stderr or ""):
        posix_path = str(project_root.resolve()).replace("\\", "/")
        subprocess.run(
            ["git", "config", "--global", "--add", "safe.directory", posix_path],
            capture_output=True,
        )


def _clear_readonly_in_git(dot_git: Path):
    """Clears Windows ReadOnly file attributes inside a partial or corrupted .git directory so git init can heal it."""
    import stat
    if not dot_git.exists():
        return
    try:
        for root_dir, dirs, files in os.walk(str(dot_git)):
            for fname in files:
                fpath = os.path.join(root_dir, fname)
                try:
                    os.chmod(fpath, stat.S_IWRITE | stat.S_IREAD)
                except Exception:
                    pass
    except Exception:
        pass


def setup_workspace_git(
    project_root: Path,
    repo_name: Optional[str] = None,
    configure_remote: bool = True,
) -> bool:
    """Initializes Git in the workspace, heals partial/corrupt .git directories, sets default gitignore, user info, and optional Gitea remote."""
    if not shutil.which("git"):
        return False

    name = repo_name or project_root.name
    dot_git = project_root / ".git"

    # 1. Check safe.directory first if .git exists
    if dot_git.exists():
        _ensure_git_safe_directory(project_root)

    # 2. Verify whether Git recognizes project_root as a valid repository
    needs_init = not (dot_git / "HEAD").exists()
    if not needs_init:
        chk = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
        )
        if chk.returncode != 0:
            needs_init = True

    if needs_init:
        if dot_git.exists():
            _clear_readonly_in_git(dot_git)
        res = subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            res = subprocess.run(
                ["git", "init"],
                cwd=str(project_root),
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                return False
            subprocess.run(
                ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
                cwd=str(project_root),
                capture_output=True,
            )
        _ensure_git_safe_directory(project_root)

    # 2. Local config for commit author
    subprocess.run(
        ["git", "config", "user.name", "Antigravity"],
        cwd=str(project_root),
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "antigravity@local.dev"],
        cwd=str(project_root),
        capture_output=True,
    )

    # 3. Ensure sensible .gitignore
    gitignore = project_root / ".gitignore"
    if not gitignore.exists():
        default_ignore = (
            "__pycache__/\n"
            "*.pyc\n"
            "*.tmp\n"
            "*.log\n"
            "*.db-shm\n"
            "*.db-wal\n"
            "*.db-journal\n"
            "node_modules/\n"
            ".venv/\n"
            "env/\n"
        )
        try:
            gitignore.write_text(default_ignore, encoding="utf-8")
        except Exception:
            pass

    if not configure_remote:
        return True

    # 4. Configure Gitea remote
    auth_url = ensure_remote_repository(name)
    if auth_url:
        rem_res = subprocess.run(
            ["git", "remote"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
        )
        remotes = rem_res.stdout.split()
        if "origin" in remotes:
            subprocess.run(
                ["git", "remote", "set-url", "origin", auth_url],
                cwd=str(project_root),
                capture_output=True,
            )
        else:
            subprocess.run(
                ["git", "remote", "add", "origin", auth_url],
                cwd=str(project_root),
                capture_output=True,
            )
        return True
    return False


def _push_to_origin(project_root: Path, async_push: bool = False, _allow_retry: bool = True) -> bool:
    """Pushes local main branch to origin if origin remote is configured; auto-heals Gitea service on failure."""
    rem_res = subprocess.run(
        ["git", "remote"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
    )
    if "origin" not in rem_res.stdout.split():
        if _is_temp_workspace(project_root):
            return False
        setup_workspace_git(project_root)

    try:
        if async_push:
            creation_flags = 0x08000000 if os.name == "nt" else 0
            subprocess.Popen(
                ["git", "push", "-u", "origin", "main"],
                cwd=str(project_root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
            return True

        push_res = subprocess.run(
            ["git", "push", "-u", "origin", "main"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if push_res.returncode == 0:
            return True

        # If push failed on a real workspace, attempt to auto-recover the Gitea service and retry once
        if _allow_retry and not _is_temp_workspace(project_root):
            health = ensure_gitea_online(auto_recover=True, flush_pending=False)
            if health.get("online"):
                setup_workspace_git(project_root)
                retry_res = subprocess.run(
                    ["git", "push", "-u", "origin", "main"],
                    cwd=str(project_root),
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                return retry_res.returncode == 0
        return False
    except Exception:
        return False


def _has_unpushed_commits(project_root: Path) -> bool:
    """Checks if local HEAD has commits not yet pushed to origin/main."""
    rem_res = subprocess.run(
        ["git", "remote"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
    )
    if "origin" not in rem_res.stdout.split():
        return not _is_temp_workspace(project_root)

    rev_res = subprocess.run(
        ["git", "rev-list", "--count", "origin/main..HEAD"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
    )
    if rev_res.returncode != 0:
        # origin/main ref does not exist locally yet, so local commits are unpushed
        head_chk = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
        )
        return head_chk.returncode == 0
    try:
        return int(rev_res.stdout.strip()) > 0
    except ValueError:
        return False


def save_code_commit(
    project_root: Path,
    message: str,
    push: bool = True,
    async_push: bool = False,
    files: Optional[List[str]] = None,
    trailers: Optional[Dict[str, str]] = None,
    explicit_message: bool = False,
) -> Dict[str, Any]:
    """
    Stages changes, creates a Git commit with optional episodic trailers, and pushes to Gitea.
    Returns commit status, hash, changed_files list, and push metrics.
    """
    if not shutil.which("git"):
        return {"success": False, "error": "git executable not found in PATH"}

    if not project_root.exists():
        return {"success": False, "error": f"Project root {project_root} does not exist"}

    is_temp = _is_temp_workspace(project_root)
    dot_git = project_root / ".git"
    if not (dot_git / "HEAD").exists():
        setup_workspace_git(project_root, configure_remote=not is_temp)

    # Check status (auto-healing dubious ownership or uninitialized/corrupt .git if needed)
    st_res = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
    )
    err_lower = (st_res.stderr or "").lower()
    if st_res.returncode != 0 and ("dubious ownership" in err_lower or "not a git repository" in err_lower):
        if "not a git repository" in err_lower:
            subprocess.run(["git", "init", "-b", "main"], cwd=str(project_root), capture_output=True)
        _ensure_git_safe_directory(project_root)
        setup_workspace_git(project_root, configure_remote=not is_temp)
        st_res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
        )
    if st_res.returncode != 0:
        return {
            "success": False,
            "error": st_res.stderr.strip() or "Failed to query git status",
        }
    status_lines = [l for l in st_res.stdout.splitlines() if l.strip()]

    # Extract changed file paths for semantic FTS indexing
    changed_files: List[str] = []
    for line in status_lines:
        if len(line) > 3:
            raw_path = line[3:].strip().strip('"')
            if " -> " in raw_path:
                raw_path = raw_path.split(" -> ")[-1]
            changed_files.append(raw_path)

    clean_msg = message.replace('"', '\\"').strip()
    if explicit_message or clean_msg.startswith("[SkillsDB]"):
        commit_title = clean_msg
    else:
        commit_title = f"[SkillsDB] {clean_msg}"

    # If working tree is clean, check if an explicit -m message should amend a just-created snapshot commit,
    # or if any prior local commits still need to be pushed
    if not status_lines:
        head_res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
        )
        current_hash = head_res.stdout.strip() if head_res.returncode == 0 else ""

        if explicit_message and current_hash:
            log_res = subprocess.run(
                ["git", "log", "-1", "--format=%ct%n%s%n%b"],
                cwd=str(project_root),
                capture_output=True,
                text=True,
            )
            if log_res.returncode == 0:
                log_lines = log_res.stdout.splitlines()
                if len(log_lines) >= 2:
                    try:
                        commit_ts = int(log_lines[0].strip())
                    except ValueError:
                        commit_ts = 0
                    prev_subject = log_lines[1].strip()
                    prev_body = "\n".join(log_lines[2:]).strip()
                    if (time.time() - commit_ts) <= 120 and prev_subject != commit_title:
                        amend_cmd = [
                            "git",
                            "-c", "user.name=Antigravity",
                            "-c", "user.email=antigravity@local.dev",
                            "commit", "--amend",
                            "--author=Antigravity <antigravity@local.dev>",
                            "-m", commit_title,
                        ]
                        if prev_body:
                            amend_cmd.extend(["-m", prev_body])
                        am_res = subprocess.run(amend_cmd, cwd=str(project_root), capture_output=True, text=True)
                        if am_res.returncode == 0:
                            rev2 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(project_root), capture_output=True, text=True)
                            new_h = rev2.stdout.strip() if rev2.returncode == 0 else current_hash
                            pushed_am = False
                            if push:
                                p_res = subprocess.run(["git", "push", "--force-with-lease", "origin", "main"], cwd=str(project_root), capture_output=True, text=True, timeout=60)
                                pushed_am = (p_res.returncode == 0)
                            return {
                                "success": True,
                                "committed": True,
                                "amended": True,
                                "commit_hash": new_h,
                                "files_changed": 0,
                                "changed_files": [],
                                "pushed": pushed_am,
                                "message": commit_title,
                            }

        pushed_pending = False
        if push and current_hash and _has_unpushed_commits(project_root):
            pushed_pending = _push_to_origin(project_root, async_push=async_push)
        return {
            "success": True,
            "committed": False,
            "commit_hash": current_hash,
            "files_changed": 0,
            "changed_files": [],
            "pushed": pushed_pending,
            "message": "Working tree clean, pending commits pushed" if pushed_pending else "Working tree clean, no changes to commit",
        }

    # Stage files
    if files:
        for f in files:
            subprocess.run(["git", "add", f], cwd=str(project_root), capture_output=True)
    else:
        subprocess.run(["git", "add", "-A"], cwd=str(project_root), capture_output=True)

    # Build commit command with optional episodic trailers
    commit_cmd = [
        "git",
        "-c", "user.name=Antigravity",
        "-c", "user.email=antigravity@local.dev",
        "commit",
        "--author=Antigravity <antigravity@local.dev>",
        "-m",
        commit_title,
    ]

    if trailers:
        trailer_lines = [f"{k}: {v}" for k, v in trailers.items() if v]
        if trailer_lines:
            commit_cmd.extend(["-m", "\n".join(trailer_lines)])

    cm_res = subprocess.run(
        commit_cmd,
        cwd=str(project_root),
        capture_output=True,
        text=True,
    )
    if cm_res.returncode != 0:
        return {
            "success": False,
            "error": cm_res.stderr.strip() or "Failed to commit changes",
        }

    # Get new commit hash
    rev_res = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
    )
    new_hash = rev_res.stdout.strip()

    # Push to Gitea
    pushed = False
    if push:
        pushed = _push_to_origin(project_root, async_push=async_push)

    return {
        "success": True,
        "committed": True,
        "commit_hash": new_hash,
        "files_changed": len(changed_files),
        "changed_files": changed_files,
        "pushed": pushed,
        "message": commit_title,
    }


def list_gitea_repositories() -> List[Dict[str, Any]]:
    """Retrieves all repositories owned by the configured Gitea user."""
    config = get_gitea_config()
    target_user = config.get("user")
    token = config.get("token")
    if not token:
        return []

    urls = []
    if target_user:
        urls.append(f"{config['url']}/api/v1/users/{target_user}/repos?limit=100")
    urls.append(f"{config['url']}/api/v1/user/repos?limit=100")

    for url in urls:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"token {token}",
                "User-Agent": "SkillsDB-GiteaSync/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                if resp.status == 200:
                    repos = json.loads(resp.read().decode("utf-8"))
                    if repos:
                        return repos
        except Exception:
            continue
    return []


def gitea_status_summary():
    """Prints comprehensive health and status report for Gitea integration, auto-healing if stopped."""
    config = get_gitea_config()
    health = ensure_gitea_online(auto_recover=True, flush_pending=True)
    svc_status = health["service_status"]
    online = health["online"]
    repos = list_gitea_repositories() if online else []

    print("=" * 60)
    print("SkillsDB - Gitea Integration Status")
    print("=" * 60)
    if health.get("recovered"):
        print("Auto-Recovery:    Service 'gitea' was STOPPED and automatically restarted!")
    if health.get("flushed_repos"):
        print(f"Catch-Up Sync:    Pushed pending commits for: {', '.join(health['flushed_repos'])}")
    print(f"Windows Service:  {config['service_name']} ({svc_status})")
    print(f"Server URL:       {config['url']}")
    print(f"API Health:       {'ONLINE' if online else 'OFFLINE'}")
    if online:
        print(f"Gitea Version:    {health.get('version') or 'unknown'}")
        print(f"Repository Owner: {config['user'] or '(not configured)'}")
        print(f"Tracked Repos:    {len(repos)}")
        for r in sorted(repos, key=lambda x: x.get("name", "").lower()):
            print(f"  * {r.get('full_name')} ({r.get('html_url')})")
    else:
        print(f"Diagnostics:      [CRITICAL] {health.get('error', 'Server unreachable')}")
    print("=" * 60)


def _print_collision_prompt(project_root: Path, rem_info: Dict[str, Any]):
    """Prints an unmissable repository collision warning instructing the agent to ask the user how to proceed."""
    print(f"  [GITEA REPOSITORY COLLISION] Hold on! A repository named '{rem_info['full_name']}' already exists on Gitea ({rem_info['html_url']}).")
    print("  STOP and ask the user how to proceed before pushing:")
    print(f"    1. Use and update the existing repository:  skillsdb gitea-init \"{project_root}\" --use-existing")
    print(f"    2. Create a repository under a new name:    skillsdb gitea-init \"{project_root}\" --name \"<new_repo_name>\"")
    print(f"    3. Recreate/overwrite from scratch:         skillsdb gitea-init \"{project_root}\" --overwrite")


def init_workspace_gitea(
    project_root: Path,
    repo_name: Optional[str] = None,
    use_existing: bool = False,
    overwrite: bool = False,
) -> bool:
    """
    Initializes a workspace for Gitea with explicit repository collision detection.
    If a non-empty repository with the target name already exists on Gitea and neither
    use_existing nor overwrite was specified, halts and prompts the agent/user for a decision.
    """
    target_name = repo_name or project_root.name
    print(f"Initializing Gitea repository '{target_name}' for workspace: {project_root}")

    rem_info = inspect_remote_repository(target_name)
    if rem_info.get("exists") and not rem_info.get("empty"):
        if overwrite:
            delete_remote_repository(target_name)
            print(f"  [OK] Deleted existing repository '{rem_info['full_name']}' for clean re-initialization.")
        elif not use_existing:
            _print_collision_prompt(project_root, rem_info)
            return False

    setup_workspace_git(project_root, repo_name=target_name, configure_remote=True)
    learn_workspace(project_root)
    sync_workspace(
        project_root,
        message=f"Initialize {target_name} on Gitea",
        push=True,
        repo_name=target_name,
        allow_existing=True,
    )
    return True


def parse_keep_a_changelog(changelog_path: Path) -> List[Dict[str, str]]:
    """
    Parses a CHANGELOG.md formatted according to Keep a Changelog 1.1.0 (https://keepachangelog.com/en/1.1.0/).
    Returns a list of dicts (newest first): [{'version': '1.0.6', 'tag': 'v1.0.6', 'date': '2026-10-19', 'body': '...'}].
    """
    if not changelog_path.exists():
        return []
    try:
        lines = changelog_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []

    entries: List[Dict[str, str]] = []
    current_ver: Optional[str] = None
    current_date: str = ""
    current_lines: List[str] = []

    ver_re = re.compile(r"^## \[(\d+\.\d+\.\d+(?:-[0-9A-Za-z.\-]+)?)\](?: - (\d{4}-\d{2}-\d{2}))?\s*$")
    link_ref_re = re.compile(r"^\[[^\]]+\]:\s*https?://")

    def _flush():
        if current_ver and current_lines:
            body_text = "\n".join(current_lines).strip()
            footer = "\n\n---\n*Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).*"
            if "keepachangelog.com" not in body_text:
                body_text += footer
            entries.append({
                "version": current_ver,
                "tag": f"v{current_ver}",
                "date": current_date,
                "body": body_text,
            })

    for line in lines:
        if line.startswith("## "):
            _flush()
            m = ver_re.match(line.strip())
            if m:
                current_ver = m.group(1)
                current_date = m.group(2) or ""
                current_lines = [line.strip()]
            else:
                current_ver = None
                current_date = ""
                current_lines = []
        elif current_ver is not None:
            if link_ref_re.match(line.strip()):
                continue
            current_lines.append(line)

    _flush()
    return entries


def _extract_changelog_section(
    project_root: Path,
    target_version: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Reads VERSION and CHANGELOG.md in project_root and extracts:
    (normalized_tag, release_title, markdown_body).
    """
    raw_ver = target_version.strip() if target_version else None
    if not raw_ver:
        ver_file = project_root / "VERSION"
        if ver_file.exists():
            try:
                v_txt = ver_file.read_text(encoding="utf-8", errors="ignore").strip()
                if v_txt and len(v_txt) <= 32:
                    raw_ver = v_txt
            except Exception:
                pass

    changelog_file = project_root / "CHANGELOG.md"
    entries = parse_keep_a_changelog(changelog_file) if changelog_file.exists() else []
    clean_target = raw_ver.lstrip("vV") if raw_ver else None

    for entry in entries:
        if clean_target is None or entry["version"] == clean_target:
            tag_name = entry["tag"]
            title = f"{project_root.name} {tag_name}"
            return tag_name, title, entry["body"]

    if not raw_ver:
        return None, None, None

    tag_name = raw_ver if raw_ver.startswith("v") else f"v{raw_ver}"
    title = f"{project_root.name} {tag_name}"
    return tag_name, title, None


def publish_gitea_release(
    project_root: Path,
    tag_name: Optional[str] = None,
    title: Optional[str] = None,
    notes: Optional[str] = None,
    repo_name: Optional[str] = None,
    prerelease: bool = False,
    sync_first: bool = True,
    sync_github: bool = False,
) -> Optional[str]:
    """
    Creates or updates a full Gitea release (with Git tag and CHANGELOG.md notes) for a workspace.
    Returns the HTML URL of the published Gitea release, or None on failure.
    """
    target_name = repo_name or project_root.name
    auto_tag, auto_title, auto_notes = _extract_changelog_section(project_root, tag_name)
    final_tag = (tag_name.strip() if tag_name else auto_tag)
    if not final_tag:
        print("  [ERROR] Could not determine version tag (pass a tag or create VERSION / CHANGELOG.md).")
        return None
    if not final_tag.startswith("v") and re.match(r"^[0-9]+\.", final_tag):
        final_tag = f"v{final_tag}"

    if auto_tag != final_tag:
        _, _, matched_notes = _extract_changelog_section(project_root, final_tag)
        if matched_notes:
            auto_notes = matched_notes

    final_title = (title.strip() if title else None) or auto_title or f"{target_name} {final_tag}"
    final_notes = (notes.strip() if notes else None) or auto_notes

    if sync_first:
        sync_workspace(
            project_root,
            message=f"chore(release): {final_tag}",
            push=True,
            repo_name=target_name,
            allow_existing=True,
            _skip_auto_release=True,
        )

    if not final_notes:
        log_res = subprocess.run(
            ["git", "log", "-5", "--pretty=format:- %s (%h)"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
        )
        final_notes = log_res.stdout.strip() if log_res.returncode == 0 else f"Release {final_tag}"

    footer = "\n\n---\n*Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).*"
    if "keepachangelog.com" not in final_notes:
        final_notes += footer

    # Create/update annotated Git tag locally and push to origin
    subprocess.run(
        [
            "git",
            "-c", "user.name=Antigravity",
            "-c", "user.email=antigravity@local.dev",
            "tag", "-f", "-a", final_tag, "-m", final_title,
        ],
        cwd=str(project_root),
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "push", "-f", "origin", final_tag],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=60,
    )

    config = get_gitea_config()
    token = config.get("token", "")
    user = config.get("user", "")
    base_url = config.get("url", "http://localhost:3000").rstrip("/")
    if not token or not user:
        return None

    headers = {
        "Authorization": f"token {token}",
        "Content-Type": "application/json",
        "User-Agent": "SkillsDB-GiteaSync/1.0",
    }

    existing_rel = None
    try:
        list_req = urllib.request.Request(
            f"{base_url}/api/v1/repos/{user}/{target_name}/releases?limit=50",
            headers=headers,
        )
        with urllib.request.urlopen(list_req, timeout=5.0) as resp:
            rels = json.loads(resp.read().decode("utf-8"))
            for r in rels:
                if r.get("tag_name") == final_tag:
                    existing_rel = r
                    break
    except Exception:
        pass

    payload_dict = {
        "tag_name": final_tag,
        "target_commitish": "main",
        "name": final_title,
        "body": final_notes,
        "draft": False,
        "prerelease": bool(prerelease),
    }

    html_url = None
    if existing_rel and existing_rel.get("id"):
        rel_id = existing_rel["id"]
        try:
            patch_req = urllib.request.Request(
                f"{base_url}/api/v1/repos/{user}/{target_name}/releases/{rel_id}",
                data=json.dumps(payload_dict).encode("utf-8"),
                method="PATCH",
                headers=headers,
            )
            with urllib.request.urlopen(patch_req, timeout=8.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                html_url = data.get("html_url") or f"{base_url}/{user}/{target_name}/releases/tag/{final_tag}"
                print(f"  [OK] Updated Gitea Release {final_tag}: {html_url}")
        except Exception:
            pass

    if not html_url:
        try:
            post_req = urllib.request.Request(
                f"{base_url}/api/v1/repos/{user}/{target_name}/releases",
                data=json.dumps(payload_dict).encode("utf-8"),
                method="POST",
                headers=headers,
            )
            with urllib.request.urlopen(post_req, timeout=8.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                html_url = data.get("html_url") or f"{base_url}/{user}/{target_name}/releases/tag/{final_tag}"
                print(f"  [OK] Published Gitea Release {final_tag}: {html_url}")
        except Exception as e:
            print(f"  [WARN] Could not publish Gitea release {final_tag}: {e}")
            return None

    if sync_github and shutil.which("gh"):
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as tf:
                tf.write(final_notes)
                tmp_path = tf.name
            try:
                chk = subprocess.run(["gh", "release", "view", final_tag], cwd=str(project_root), capture_output=True, text=True, timeout=15)
                if chk.returncode == 0:
                    subprocess.run(["gh", "release", "edit", final_tag, "--title", final_title, "--notes-file", tmp_path], cwd=str(project_root), capture_output=True, timeout=20)
                else:
                    subprocess.run(["gh", "release", "create", final_tag, "--title", final_title, "--notes-file", tmp_path], cwd=str(project_root), capture_output=True, timeout=20)
            finally:
                Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass

    return html_url


def sync_changelog_releases(
    project_root: Path,
    sync_github: bool = False,
    overwrite_existing: bool = False,
    db_path: Path = DB_PATH,
) -> List[str]:
    """
    Synchronizes all version entries from CHANGELOG.md (Keep a Changelog 1.1.0 format)
    as formal Releases on Gitea (and optionally GitHub if a github remote and gh CLI exist).
    Returns the list of synced release tags.
    """
    changelog_path = project_root / "CHANGELOG.md"
    if not changelog_path.exists() or _is_temp_workspace(project_root):
        return []

    entries = parse_keep_a_changelog(changelog_path)
    if not entries:
        return []

    config = get_gitea_config(db_path=db_path)
    token = config.get("token")
    owner = config.get("user")
    base_url = config.get("url")
    repo_name = project_root.name
    synced_tags: List[str] = []

    # Ensure local git tags exist for each CHANGELOG version and push tags to origin
    if shutil.which("git") and (project_root / ".git" / "HEAD").exists():
        try:
            tag_res = subprocess.run(
                ["git", "tag"],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=5,
            )
            local_tags = set(tag_res.stdout.split()) if tag_res.returncode == 0 else set()
            created_tag = False
            # Tag the latest version at HEAD if not yet tagged
            latest_tag = entries[0]["tag"]
            if latest_tag not in local_tags:
                subprocess.run(
                    ["git", "-c", "user.name=Antigravity", "-c", "user.email=antigravity@local.dev", "tag", "-a", latest_tag, "-m", f"{repo_name} {latest_tag}"],
                    cwd=str(project_root),
                    capture_output=True,
                    timeout=5,
                )
                created_tag = True
            if created_tag:
                subprocess.run(
                    ["git", "push", "origin", "--tags"],
                    cwd=str(project_root),
                    capture_output=True,
                    timeout=30,
                )
        except Exception:
            pass

    # 1. Sync releases to Gitea
    if token and owner and base_url:
        try:
            rel_url = f"{base_url}/api/v1/repos/{owner}/{repo_name}/releases?limit=100"
            req = urllib.request.Request(
                rel_url,
                headers={
                    "Authorization": f"token {token}",
                    "User-Agent": "SkillsDB-GiteaSync/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                existing_list = json.loads(resp.read().decode("utf-8"))
            existing_map = {r.get("tag_name"): r for r in existing_list if isinstance(r, dict)}

            for entry in reversed(entries):
                tag = entry["tag"]
                body = entry["body"]
                rel_title = f"{repo_name} {tag}"
                if tag in existing_map:
                    if not overwrite_existing and "keepachangelog.com" in (existing_map[tag].get("body") or ""):
                        continue
                    rel_id = existing_map[tag]["id"]
                    target_url = f"{base_url}/api/v1/repos/{owner}/{repo_name}/releases/{rel_id}"
                    method = "PATCH"
                else:
                    target_url = f"{base_url}/api/v1/repos/{owner}/{repo_name}/releases"
                    method = "POST"

                payload = json.dumps({
                    "tag_name": tag,
                    "target_commitish": "main",
                    "name": rel_title,
                    "body": body,
                    "draft": False,
                    "prerelease": False,
                }).encode("utf-8")
                up_req = urllib.request.Request(
                    target_url,
                    data=payload,
                    method=method,
                    headers={
                        "Authorization": f"token {token}",
                        "Content-Type": "application/json",
                        "User-Agent": "SkillsDB-GiteaSync/1.0",
                    },
                )
                with urllib.request.urlopen(up_req, timeout=5.0) as up_resp:
                    if up_resp.status in (200, 201):
                        synced_tags.append(tag)
        except Exception:
            pass

    # 2. Optional GitHub Release sync (if sync_github=True and gh CLI is available)
    if sync_github and shutil.which("gh"):
        try:
            for entry in reversed(entries):
                tag = entry["tag"]
                body = entry["body"]
                rel_title = f"{repo_name} {tag}"
                with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as tf:
                    tf.write(body)
                    tmp_path = tf.name
                try:
                    chk = subprocess.run(
                        ["gh", "release", "view", tag],
                        cwd=str(project_root),
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                    if chk.returncode == 0:
                        if overwrite_existing or "keepachangelog.com" not in (chk.stdout or ""):
                            subprocess.run(
                                ["gh", "release", "edit", tag, "--title", rel_title, "--notes-file", tmp_path],
                                cwd=str(project_root),
                                capture_output=True,
                                timeout=20,
                            )
                    else:
                        subprocess.run(
                            ["gh", "release", "create", tag, "--title", rel_title, "--notes-file", tmp_path],
                            cwd=str(project_root),
                            capture_output=True,
                            timeout=20,
                        )
                finally:
                    Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass

    return synced_tags


def sync_workspace(
    project_root: Path,
    message: Optional[str] = None,
    push: bool = True,
    sync_github: bool = False,
    repo_name: Optional[str] = None,
    allow_existing: bool = False,
    _skip_auto_release: bool = False,
) -> bool:
    """Initializes, learns, and synchronizes a specific workspace (including Keep a Changelog releases and collision checks) to Gitea."""
    has_explicit_msg = bool(message and message.strip() and message != "Synchronize workspace code history")
    msg = message or "Synchronize workspace code history"
    target_name = repo_name or project_root.name
    print(f"Synchronizing workspace: {project_root.name} ({project_root})")

    configured_url = _get_configured_gitea_repo_url(project_root)
    if not configured_url and push and not allow_existing and not _is_temp_workspace(project_root):
        rem_info = inspect_remote_repository(target_name)
        if rem_info.get("exists") and not rem_info.get("empty"):
            setup_workspace_git(project_root, repo_name=target_name, configure_remote=False)
            learn_workspace(project_root)
            _print_collision_prompt(project_root, rem_info)
            return False

    setup_workspace_git(project_root, repo_name=target_name)
    learn_workspace(project_root)
    result = save_code_commit(project_root, message=msg, push=push, explicit_message=has_explicit_msg)
    config = get_gitea_config()
    repo_url = _get_configured_gitea_repo_url(project_root) or (
        f"{config['url']}/{config['user']}/{target_name}" if config.get("user") else config["url"]
    )
    if result.get("success"):
        if result.get("committed"):
            action_lbl = "Commit amended" if result.get("amended") else "Commit created"
            print(f"  [OK] {action_lbl}: {result.get('commit_hash', '')[:7]}")
            if not result.get("amended"):
                print(f"  [OK] Files tracked:  {result.get('files_changed', 0)}")
            if result.get("pushed"):
                print(f"  [OK] Pushed successfully to Gitea ({repo_url})")
            else:
                print("  [WARN] Commit created locally, push skipped or failed")
        else:
            if result.get("pushed"):
                print(f"  [OK] Flushed pending commits to Gitea (HEAD: {result.get('commit_hash', '')[:7]} | {repo_url})")
            else:
                print(f"  [OK] Already up to date (HEAD: {result.get('commit_hash', '')[:7]} | {repo_url})")

        if push and not _skip_auto_release and not _is_temp_workspace(project_root) and (project_root / "CHANGELOG.md").exists():
            rel_tags = sync_changelog_releases(project_root, sync_github=sync_github)
            if rel_tags:
                print(f"  [OK] Synced Keep a Changelog releases: {', '.join(rel_tags)}")
            auto_tag, _, _ = _extract_changelog_section(project_root)
            if auto_tag:
                ahead_chk = subprocess.run(
                    ["git", "rev-list", "--count", f"{auto_tag}..HEAD"],
                    cwd=str(project_root),
                    capture_output=True,
                    text=True,
                )
                try:
                    ahead_cnt = int(ahead_chk.stdout.strip()) if ahead_chk.returncode == 0 else 0
                except ValueError:
                    ahead_cnt = 0
                if ahead_cnt > 0:
                    print(
                        f"  [RELEASE REMINDER] HEAD is {ahead_cnt} commit(s) ahead of release {auto_tag}. "
                        f"Increment VERSION + CHANGELOG.md and run 'skillsdb gitea-release' to publish a new release."
                    )
        return True
    else:
        print(f"  [ERROR] {result.get('error')}")
        return False


def discover_all_workspaces(base_dir: Optional[Path] = None, db_path: Path = DB_PATH) -> List[Path]:
    """
    Discovers all active Antigravity workspaces organically from:
    1. Learned project workspaces in runtime_config (workspace:<name>)
    2. Registered Antigravity IDE project definitions in ~/.gemini/config/projects/*.json
    3. Optional base_dir or configured workspaces_root
    Automatically learns newly discovered projects and prunes deleted ones.
    """
    config = get_gitea_config(db_path=db_path)
    seen_resolved = set()
    workspaces: List[Path] = []

    def _add_candidate(p: Path):
        try:
            if not p.exists() or not p.is_dir():
                return
            abs_p = Path(os.path.abspath(str(p)))
            norm_key = str(abs_p).lower()
            if norm_key in seen_resolved:
                return
            has_agents = (abs_p / ".agents").exists()
            has_git = (abs_p / ".git" / "HEAD").exists()
            has_files = any(f.is_file() for f in abs_p.iterdir())
            if has_agents or has_git or has_files:
                seen_resolved.add(norm_key)
                workspaces.append(abs_p)
                learn_workspace(abs_p, db_path=db_path)
        except Exception:
            pass

    # 1. Load organically learned workspaces from runtime_config
    learned_map = get_learned_workspaces(db_path=db_path, prune_missing=True)
    for _, path_str in sorted(learned_map.items(), key=lambda x: x[0].lower()):
        _add_candidate(Path(path_str))

    # 2. Scan registered projects in ~/.gemini/config/projects/*.json
    projects_cfg_dir = GEMINI_DIR / "config" / "projects"
    if projects_cfg_dir.exists():
        import urllib.parse
        for jfile in sorted(projects_cfg_dir.glob("*.json")):
            try:
                data = json.loads(jfile.read_text(encoding="utf-8", errors="ignore"))
                root_path_str = data.get("rootPath") or data.get("path")
                if root_path_str:
                    _add_candidate(Path(root_path_str))
                resources = data.get("projectResources", {}).get("resources", [])
                for res in resources:
                    f_uri = res.get("folderUri") or res.get("gitFolder", {}).get("folderUri", "")
                    if f_uri.startswith("file:///"):
                        decoded_path = urllib.parse.unquote(f_uri[8:])
                        if os.name == "nt":
                            decoded_path = decoded_path.replace("/", "\\")
                        _add_candidate(Path(decoded_path))
            except Exception:
                continue

    # 3. Scan default Antigravity scratch workspaces directory (~/.gemini/antigravity/scratch)
    scratch_dir = GEMINI_DIR / "antigravity" / "scratch"
    if scratch_dir.exists():
        for item in sorted(scratch_dir.iterdir(), key=lambda x: x.name.lower()):
            if item.is_dir() and not item.name.startswith("."):
                _add_candidate(item)

    # 4. Scan optional base_dir or workspaces_root if configured
    root_candidate = base_dir
    if not root_candidate and config.get("workspaces_root"):
        root_candidate = Path(config["workspaces_root"])
    if root_candidate and root_candidate.exists():
        for item in sorted(root_candidate.iterdir(), key=lambda x: x.name.lower()):
            if item.is_dir() and not item.name.startswith("."):
                _add_candidate(item)

    return workspaces


def sync_all_workspaces(base_dir: Optional[Path] = None):
    """Discovers and synchronizes all active workspaces across base_dir and registered projects to Gitea."""
    workspaces = discover_all_workspaces(base_dir=base_dir)
    if not workspaces:
        print("No active workspaces discovered to synchronize.")
        return

    print("=" * 60)
    print(f"SkillsDB - Synchronizing {len(workspaces)} discovered workspaces to Gitea")
    print("=" * 60)

    for ws in workspaces:
        print(f"\nProcessing workspace: {ws.name} ({ws})")
        sync_workspace(ws, message=f"SkillsDB automated sync for {ws.name}")
    print(f"\nAll {len(workspaces)} workspaces synchronized with Gitea.")
