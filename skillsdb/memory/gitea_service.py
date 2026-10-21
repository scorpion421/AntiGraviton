"""
Gitea service monitoring, cross-platform auto-recovery (Windows sc.exe, Linux systemd, macOS launchd),
and Gitea REST API repository management for SkillsDB.
"""

import os
import sys
import time
import json
import shutil
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

from ..config import DB_PATH
from .gitea_config import (
    get_gitea_config,
    _strip_credentials_from_url,
    _build_auth_clone_url,
)


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
        healthz_url = f"{config['url']}/api/healthz"
        try:
            h_req = urllib.request.Request(healthz_url, headers={"User-Agent": "SkillsDB-GiteaSync/1.0"})
            with urllib.request.urlopen(h_req, timeout=timeout) as h_resp:
                if h_resp.status == 200:
                    h_data = json.loads(h_resp.read().decode("utf-8"))
                    if h_data.get("status") == "pass":
                        return True, {"version": h_data.get("description", "online"), "status": "pass"}
        except Exception:
            pass
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
                encoding="utf-8",
                errors="replace",
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
        "updated_at": None,
    }
    if not token or not user:
        return result

    repo_url = f"{base_url}/api/v1/repos/{user}/{repo_name}"
    req = urllib.request.Request(
        repo_url,
        headers={
            "Authorization": f"token {token}",
            "User-Agent": "SkillsDB-GiteaSync/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                result["exists"] = True
                result["empty"] = bool(data.get("empty", False))
                result["full_name"] = data.get("full_name") or result["full_name"]
                result["html_url"] = data.get("html_url") or default_html
                result["clone_url"] = _strip_credentials_from_url(data.get("clone_url"))
                result["updated_at"] = data.get("updated_at")
    except Exception:
        pass
    return result


def ensure_remote_repository(repo_name: str, description: Optional[str] = None) -> Optional[str]:
    """Ensures repository exists in Gitea; creates it if missing and returns a clean, credential-free clone URL."""
    config = get_gitea_config()
    token = config["token"]
    user = config["user"]
    base_url = config["url"]

    if not token or not user:
        return None

    info = inspect_remote_repository(repo_name)
    if info.get("exists") and info.get("clone_url"):
        return _strip_credentials_from_url(info["clone_url"])

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
                    return _strip_credentials_from_url(data.get("clone_url"))
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
