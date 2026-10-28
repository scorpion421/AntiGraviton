"""
Core workspace Git synchronization, commit tracking, telemetry, and release orchestration for SkillsDB.
Re-exports configuration, service, and release helpers for full backward compatibility.
"""

import os
import re
import sys
import time
import json
import shutil
import tempfile
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

from ..config import DB_PATH
from .gitea_config import (
    _is_temp_workspace,
    _is_excluded_workspace_path,
    _strip_credentials_from_url,
    _build_auth_clone_url,
    _get_git_auth_cmd_prefix,
    register_git_credential_helper,
    gitea_credential_helper,
    _scrub_workspace_git_config_credentials,
    learn_workspace,
    get_learned_workspaces,
    get_gitea_config,
    set_gitea_config,
    discover_all_workspaces,
    gitea_config_summary,
)
from .gitea_service import (
    check_gitea_health,
    get_gitea_service_status,
    inspect_remote_repository,
    ensure_remote_repository,
    delete_remote_repository,
    list_gitea_repositories,
)
from .gitea_releases import (
    parse_keep_a_changelog,
    _extract_changelog_section,
    sync_changelog_releases,
)


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
    Verifies Gitea service and API health. If the service is STOPPED or the API
    is unreachable, automatically attempts to start the service across Windows (sc.exe),
    Linux (systemd --user), and macOS (launchctl) and optionally flushes pending commits.
    """
    svc_status = get_gitea_service_status(db_path=db_path)
    online, v_info = check_gitea_health(timeout=1.5, db_path=db_path)
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
                    encoding="utf-8",
                    errors="replace",
                    timeout=8,
                )
                if start_res.returncode == 0 or "1056" in start_res.stdout:
                    for _ in range(6):
                        time.sleep(0.8)
                        online, v_info = check_gitea_health(timeout=1.0, db_path=db_path)
                        if online:
                            recovered = True
                            break
                    svc_status = get_gitea_service_status(db_path=db_path)
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
                        online, v_info = check_gitea_health(timeout=1.0, db_path=db_path)
                        if online:
                            recovered = True
                            break
                    svc_status = get_gitea_service_status(db_path=db_path)
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


def _get_configured_gitea_repo_url(project_root: Path) -> Optional[str]:
    """Inspects .git/config to see if origin or gitea remote points to the local Gitea server and returns its clean browser URL."""
    git_cfg = project_root / ".git" / "config"
    if not git_cfg.exists():
        return None
    try:
        _scrub_workspace_git_config_credentials(project_root)
        raw = git_cfg.read_text(encoding="utf-8", errors="ignore")
        cfg = get_gitea_config()
        base_url = cfg.get("url", "http://localhost:3000").rstrip("/")
        for m in re.finditer(r"^\s*url\s*=\s*(https?://(?:[^@/\s]+@)?([^/\s]+)/([^/\s]+)/([^\s/]+?)(?:\.git)?)\s*$", raw, re.MULTILINE):
            _, host_port, owner, rname = m.groups()
            if "localhost:3000" in host_port or "127.0.0.1:3000" in host_port or host_port in base_url:
                return f"{base_url}/{owner}/{rname}"
    except Exception:
        pass
    return None


def get_workspace_git_telemetry(project_root: Path, flush_pending: bool = False, auto_recover: bool = True) -> Optional[str]:
    """
    Returns a compact Git & Gitea status string (~20 tokens), auto-recovers the Gitea
    service if STOPPED (when auto_recover=True), flushes any pending unpushed commits upon
    recovery or when flush_pending=True, and emits an unmissable alert if Gitea is offline
    or if a repository name collision exists on Gitea.
    """
    if not project_root or not (project_root / ".git" / "HEAD").exists():
        return None
    if not shutil.which("git"):
        return None

    extra_notices: List[str] = []
    gitea_online = False
    if not _is_temp_workspace(project_root):
        try:
            health = ensure_gitea_online(auto_recover=auto_recover, flush_pending=flush_pending)
            gitea_online = bool(health.get("online"))
            if not gitea_online:
                svc_st = health.get("service_status", "STOPPED")
                extra_notices.append(
                    f"[CRITICAL GITEA ALERT]: Local Gitea server (http://127.0.0.1:3000) is OFFLINE (Service: {svc_st}) and could not be auto-started (fileserver or service unreachable). Commits are saved locally only. YOU MUST IMMEDIATELY WARN THE USER IN YOUR RESPONSE THAT GITEA IS CURRENTLY OFFLINE!"
                )
            else:
                if health.get("recovered"):
                    extra_notices.append(
                        "[GITEA AUTO-RECOVERY]: Service 'gitea' was STOPPED and has been automatically restarted by SkillsDB."
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


def _get_current_branch(project_root: Path) -> str:
    """Returns the currently checked-out branch name in project_root (fallback: 'main')."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        branch = res.stdout.strip()
        if res.returncode == 0 and branch and branch != "HEAD":
            return branch
    except Exception:
        pass
    return "main"


def _extract_repo_name_from_remote_url(url: str) -> Optional[str]:
    """Extracts the bare repository name (without .git suffix) from an HTTP(S) or SSH remote URL."""
    if not url:
        return None
    cleaned = url.strip().rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    tail = cleaned.split("/")[-1].split(":")[-1]
    return tail or None


def _resolve_workspace_repo_name(project_root: Path, repo_name: Optional[str] = None) -> str:
    """
    Resolves the target Gitea repository name, preserving any existing non-empty Gitea remote name,
    matching existing GitHub remote names or hyphen/underscore variants on Gitea before falling back
    to the folder name.
    """
    if repo_name:
        return repo_name

    configured_url = _get_configured_gitea_repo_url(project_root)
    configured_name = _extract_repo_name_from_remote_url(configured_url) if configured_url else None

    # Check if the workspace has a github remote (or non-Gitea origin) whose name exists on Gitea
    gh_candidate: Optional[str] = None
    if (project_root / ".git").exists():
        for rem_alias in ("github", "origin"):
            try:
                res = subprocess.run(
                    ["git", "remote", "get-url", rem_alias],
                    cwd=str(project_root),
                    capture_output=True,
                    text=True,
                    timeout=1.5,
                )
                if res.returncode == 0 and "github.com" in res.stdout.lower():
                    gh_candidate = _extract_repo_name_from_remote_url(res.stdout)
                    if gh_candidate:
                        break
            except Exception:
                pass

    if configured_name:
        if gh_candidate and gh_candidate != configured_name:
            try:
                cfg_info = inspect_remote_repository(configured_name)
                gh_info = inspect_remote_repository(gh_candidate)
                if cfg_info.get("empty") and gh_info.get("exists") and not gh_info.get("empty"):
                    return gh_candidate
            except Exception:
                pass
        return configured_name

    if gh_candidate and gh_candidate != project_root.name:
        try:
            gh_info = inspect_remote_repository(gh_candidate)
            if gh_info.get("exists") and not gh_info.get("empty"):
                return gh_candidate
        except Exception:
            pass

    # Check hyphen <-> underscore variant on Gitea if folder name has not been populated yet
    alt_name: Optional[str] = None
    if "_" in project_root.name:
        alt_name = project_root.name.replace("_", "-")
    elif "-" in project_root.name:
        alt_name = project_root.name.replace("-", "_")
    if alt_name:
        try:
            cur_info = inspect_remote_repository(project_root.name)
            if not cur_info.get("exists") or cur_info.get("empty"):
                alt_info = inspect_remote_repository(alt_name)
                if alt_info.get("exists") and not alt_info.get("empty"):
                    return alt_name
        except Exception:
            pass

    return project_root.name


def _prune_stale_origin_refs(project_root: Path) -> None:
    """Deletes cached local refs/remotes/origin/* tracking refs when origin is repointed to a new remote URL."""
    try:
        refs_res = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname)", "refs/remotes/origin/"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=3,
        )
        for ref in refs_res.stdout.splitlines():
            ref = ref.strip()
            if ref:
                subprocess.run(
                    ["git", "update-ref", "-d", ref],
                    cwd=str(project_root),
                    capture_output=True,
                    timeout=2,
                )
    except Exception:
        pass


def setup_workspace_git(
    project_root: Path,
    repo_name: Optional[str] = None,
    configure_remote: bool = True,
) -> bool:
    """Initializes Git in the workspace, heals partial/corrupt .git directories, scrubs legacy tokens, and configures a clean Gitea remote."""
    if not shutil.which("git"):
        return False

    name = _resolve_workspace_repo_name(project_root, repo_name)
    dot_git = project_root / ".git"

    if dot_git.exists():
        _ensure_git_safe_directory(project_root)
        _scrub_workspace_git_config_credentials(project_root)

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

    clean_url = ensure_remote_repository(name)
    if clean_url:
        clean_url = _strip_credentials_from_url(clean_url)
        register_git_credential_helper()
        rem_res = subprocess.run(
            ["git", "remote"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
        )
        remotes = rem_res.stdout.split()
        if "origin" in remotes:
            old_url_res = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=str(project_root),
                capture_output=True,
                text=True,
            )
            old_url = (old_url_res.stdout or "").strip()
            if old_url and "github.com" in old_url.lower() and "github" not in remotes:
                subprocess.run(
                    ["git", "remote", "add", "github", old_url],
                    cwd=str(project_root),
                    capture_output=True,
                )
            subprocess.run(
                ["git", "remote", "set-url", "origin", clean_url],
                cwd=str(project_root),
                capture_output=True,
            )
            old_norm = (_strip_credentials_from_url(old_url) or "").rstrip("/").lower()
            if old_norm.endswith(".git"):
                old_norm = old_norm[:-4]
            new_norm = clean_url.rstrip("/").lower()
            if new_norm.endswith(".git"):
                new_norm = new_norm[:-4]
            if old_norm and old_norm != new_norm:
                _prune_stale_origin_refs(project_root)
        else:
            subprocess.run(
                ["git", "remote", "add", "origin", clean_url],
                cwd=str(project_root),
                capture_output=True,
            )
        return True
    return False


def _push_tags_to_origin_if_present(project_root: Path) -> None:
    """Pushes local Git tags to local Gitea origin (never to github automatically)."""
    try:
        tag_res = subprocess.run(
            ["git", "tag", "-l"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=3,
        )
        if tag_res.returncode == 0 and tag_res.stdout.strip():
            tag_cmd = _get_git_auth_cmd_prefix() + ["push", "origin", "--tags"]
            subprocess.run(
                tag_cmd,
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=60,
            )
    except Exception:
        pass


def _push_to_origin(project_root: Path, async_push: bool = False, _allow_retry: bool = True) -> bool:
    """Pushes local active branch and tags to Gitea origin using transient in-memory auth headers; auto-heals Gitea service on failure."""
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
    else:
        _scrub_workspace_git_config_credentials(project_root)

    branch = _get_current_branch(project_root)
    push_cmd = _get_git_auth_cmd_prefix() + ["push", "-u", "origin", branch]

    try:
        if async_push:
            creation_flags = 0x08000000 if os.name == "nt" else 0
            subprocess.Popen(
                push_cmd,
                cwd=str(project_root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
            return True

        push_res = subprocess.run(
            push_cmd,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if push_res.returncode == 0:
            _push_tags_to_origin_if_present(project_root)
            return True

        if _allow_retry and not _is_temp_workspace(project_root):
            health = ensure_gitea_online(auto_recover=True, flush_pending=False)
            if health.get("online"):
                setup_workspace_git(project_root)
                retry_cmd = _get_git_auth_cmd_prefix() + ["push", "-u", "origin", branch]
                retry_res = subprocess.run(
                    retry_cmd,
                    cwd=str(project_root),
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if retry_res.returncode == 0:
                    _push_tags_to_origin_if_present(project_root)
                    return True
        return False
    except Exception:
        return False


def _has_unpushed_commits(project_root: Path) -> bool:
    """Checks if local HEAD has commits not yet pushed to origin/<branch> or if the Gitea repository is still empty."""
    rem_res = subprocess.run(
        ["git", "remote"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
    )
    if "origin" not in rem_res.stdout.split():
        return True

    head_res = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
    )
    if head_res.returncode != 0:
        return False

    branch = _get_current_branch(project_root)
    rev_res = subprocess.run(
        ["git", "rev-list", "--count", f"origin/{branch}..HEAD"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
    )
    if rev_res.returncode != 0:
        return True

    try:
        if int(rev_res.stdout.strip()) > 0:
            return True
    except ValueError:
        return False

    # Guard against stale local refs/remotes/origin/<branch> when the target Gitea repo is still empty
    try:
        repo_name = _resolve_workspace_repo_name(project_root)
        rem_info = inspect_remote_repository(repo_name)
        if rem_info.get("exists") and rem_info.get("empty"):
            _prune_stale_origin_refs(project_root)
            return True
    except Exception:
        pass

    return False


def save_code_commit(
    project_root: Path,
    message: str,
    push: bool = True,
    async_push: bool = False,
    trailers: Optional[Dict[str, str]] = None,
    explicit_message: bool = False,
) -> Dict[str, Any]:
    """
    Stages all modified/new files in the workspace, creates a structured Git commit
    authored by Antigravity <antigravity@local.dev>, and optionally pushes to Gitea.
    """
    result = {
        "success": False,
        "committed": False,
        "amended": False,
        "pushed": False,
        "commit_hash": None,
        "files_changed": 0,
        "changed_files": [],
        "error": None,
    }

    if not project_root or not project_root.exists():
        result["error"] = "Invalid project root"
        return result

    if not shutil.which("git"):
        result["error"] = "Git binary not found in PATH"
        return result

    is_temp = _is_temp_workspace(project_root)

    if not (project_root / ".git" / "HEAD").exists():
        if not setup_workspace_git(project_root, configure_remote=push and not is_temp):
            result["error"] = "Failed to initialize Git repository in workspace"
            return result
    else:
        _ensure_git_safe_directory(project_root)
        _scrub_workspace_git_config_credentials(project_root)
        if not is_temp:
            learn_workspace(project_root)

    try:
        status_res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        changed_lines = [line.strip() for line in status_res.stdout.splitlines() if line.strip()]
        if not changed_lines:
            rev_res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(project_root),
                capture_output=True,
                text=True,
            )
            if rev_res.returncode == 0:
                result["commit_hash"] = rev_res.stdout.strip()

            if explicit_message and result["commit_hash"] and _has_unpushed_commits(project_root):
                subj_res = subprocess.run(
                    ["git", "log", "-1", "--format=%s%n%ct%n%B"],
                    cwd=str(project_root),
                    capture_output=True,
                    text=True,
                )
                if subj_res.returncode == 0:
                    log_lines = subj_res.stdout.splitlines()
                    head_subj = log_lines[0].strip() if len(log_lines) >= 1 else ""
                    head_ts = int(log_lines[1].strip()) if len(log_lines) >= 2 and log_lines[1].strip().isdigit() else 0
                    head_body = "\n".join(log_lines[2:]).strip() if len(log_lines) > 2 else ""
                    age_sec = abs(time.time() - head_ts)
                    if head_subj.startswith(("[SkillsDB]", "Milestone:", "Synchronize workspace")) and age_sec <= 120:
                        existing_trailers = [
                            tl for tl in head_body.splitlines()
                            if re.match(r"^(Conversation-ID|Snapshot-ID|Next-Steps):\s+", tl)
                        ]
                        amend_msg = message.strip()
                        if existing_trailers:
                            amend_msg = amend_msg + "\n\n" + "\n".join(existing_trailers)
                        amend_res = subprocess.run(
                            [
                                "git",
                                "-c", "user.name=Antigravity",
                                "-c", "user.email=antigravity@local.dev",
                                "commit", "--amend", "-m", amend_msg,
                            ],
                            cwd=str(project_root),
                            capture_output=True,
                            text=True,
                            timeout=15,
                        )
                        if amend_res.returncode == 0:
                            result["committed"] = True
                            result["amended"] = True
                            new_rev = subprocess.run(
                                ["git", "rev-parse", "HEAD"],
                                cwd=str(project_root),
                                capture_output=True,
                                text=True,
                            )
                            if new_rev.returncode == 0:
                                result["commit_hash"] = new_rev.stdout.strip()

            result["success"] = True
            if push and not is_temp and (result["amended"] or _has_unpushed_commits(project_root)):
                result["pushed"] = _push_to_origin(project_root, async_push=async_push)
            return result

        parsed_files = []
        for line in changed_lines:
            fpath = line[2:].strip().strip('"')
            if " -> " in fpath:
                fpath = fpath.split(" -> ")[-1].strip().strip('"')
            if fpath:
                parsed_files.append(fpath)

        result["files_changed"] = len(changed_lines)
        result["changed_files"] = parsed_files

        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=15,
        )

        full_msg = message.strip()
        if trailers:
            trailer_lines = [f"{k}: {v}" for k, v in trailers.items() if v]
            if trailer_lines:
                full_msg = full_msg + "\n\n" + "\n".join(trailer_lines)

        commit_res = subprocess.run(
            [
                "git",
                "-c", "user.name=Antigravity",
                "-c", "user.email=antigravity@local.dev",
                "commit",
                "-m",
                full_msg,
            ],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if commit_res.returncode != 0:
            result["error"] = commit_res.stderr.strip() or commit_res.stdout.strip()
            return result

        result["committed"] = True
        result["success"] = True

        rev_res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
        )
        if rev_res.returncode == 0:
            result["commit_hash"] = rev_res.stdout.strip()

        if push and not is_temp:
            result["pushed"] = _push_to_origin(project_root, async_push=async_push)

        return result
    except Exception as e:
        result["error"] = str(e)
        return result


def _print_collision_prompt(project_root: Path, rem_info: Dict[str, Any]):
    """Prints an unmissable repository collision warning instructing the agent to ask the user how to proceed."""
    print(f"  [GITEA REPOSITORY COLLISION] Hold on! A repository named '{rem_info['full_name']}' already exists on Gitea ({rem_info['html_url']}).")
    print("  STOP and ask the user how to proceed before pushing:")
    print(f"    1. Use and update the existing repository:  skillsdb gitea-init \"{project_root}\" --use-existing")
    print(f"    2. Create a repository under a new name:    skillsdb gitea-init \"{project_root}\" --name \"<new_repo_name>\"")
    print(f"    3. Recreate/overwrite from scratch:         skillsdb gitea-init \"{project_root}\" --overwrite")


def publish_gitea_release(
    project_root: Path,
    tag_name: Optional[str] = None,
    title: Optional[str] = None,
    notes: Optional[str] = None,
    repo_name: Optional[str] = None,
    prerelease: bool = False,
    sync_first: bool = True,
    sync_github: bool = False,
    target_commit: Optional[str] = None,
) -> Optional[str]:
    """
    Creates or updates a full Gitea release (with Git tag and CHANGELOG.md notes) for a workspace.
    Enforces SemVer 2.0.0 immutability: refuses to move an already published release tag to a newer HEAD
    unless an explicit historical target_commit is specified.
    Returns the HTML URL of the published Gitea release, or None on failure.
    """
    project_root = Path(project_root)
    target_name = _resolve_workspace_repo_name(project_root, repo_name)
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

    # SemVer 2.0.0 immutability check: prevent overwriting an existing release tag onto a different HEAD commit
    head_res = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
    )
    head_sha = head_res.stdout.strip() if head_res.returncode == 0 else ""
    existing_tag_res = subprocess.run(
        ["git", "rev-list", "-n", "1", final_tag],
        cwd=str(project_root),
        capture_output=True,
        text=True,
    )
    existing_tag_sha = existing_tag_res.stdout.strip() if existing_tag_res.returncode == 0 else ""

    if not target_commit and existing_tag_sha and head_sha and existing_tag_sha != head_sha:
        print(
            f"  [SEMVER IMMUTABILITY ERROR] Release tag '{final_tag}' already exists at commit {existing_tag_sha[:7]} "
            f"(current HEAD is {head_sha[:7]})."
        )
        print(
            "  Never overwrite an already published release version in place! "
            "Increment VERSION and CHANGELOG.md to a new SemVer version (MAJOR.MINOR.PATCH) and publish a new release."
        )
        return None

    if sync_first and not target_commit:
        sync_workspace(
            project_root,
            message=f"chore(release): {final_tag}",
            push=True,
            repo_name=target_name,
            allow_existing=True,
            _skip_auto_release=True,
        )
        post_head_res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
        )
        post_head_sha = post_head_res.stdout.strip() if post_head_res.returncode == 0 else ""
        if existing_tag_sha and post_head_sha and existing_tag_sha != post_head_sha:
            print(
                f"  [SEMVER IMMUTABILITY ERROR] Release tag '{final_tag}' already exists at commit {existing_tag_sha[:7]} "
                f"(current HEAD after sync is {post_head_sha[:7]})."
            )
            print(
                "  Never overwrite an already published release version in place! "
                "Increment VERSION and CHANGELOG.md to a new SemVer version (MAJOR.MINOR.PATCH) and publish a new release."
            )
            return None

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

    tag_cmd = [
        "git",
        "-c", "user.name=Antigravity",
        "-c", "user.email=antigravity@local.dev",
        "tag", "-f", "-a", final_tag, "-m", final_title,
    ]
    if target_commit:
        tag_cmd.append(target_commit)

    subprocess.run(
        tag_cmd,
        cwd=str(project_root),
        capture_output=True,
        text=True,
    )
    subprocess.run(
        _get_git_auth_cmd_prefix() + ["push", "-f", "origin", final_tag],
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
        "target_commitish": target_commit or "main",
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
    target_name = _resolve_workspace_repo_name(project_root, repo_name)
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
    target_name = _resolve_workspace_repo_name(project_root, repo_name)
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
            rel_tags = sync_changelog_releases(project_root, sync_github=sync_github, repo_name=target_name)
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
