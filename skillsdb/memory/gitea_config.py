"""
Gitea configuration, organic workspace learning, credential scrubbing,
and transient/helper-based Git authentication for SkillsDB.
"""

import os
import re
import sys
import json
import base64
import shutil
import sqlite3
import tempfile
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from ..config import (
    DB_PATH,
    GEMINI_DIR,
    GITEA_DEFAULT_URL,
    GITEA_DEFAULT_USER,
    GITEA_DEFAULT_TOKEN,
    GITEA_SERVICE_NAME,
    resolve_user_home,
)
from ..core.db import managed_connection, execute_with_retry

_UNC_TO_DRIVE_CACHE: Optional[Dict[str, str]] = None


def _get_unc_to_drive_map() -> Dict[str, str]:
    """
    Builds and caches a mapping of UNC share roots (lowercase, without trailing slash)
    to their mounted Windows drive roots (e.g. '\\\\fileserver...\\home$\\user' -> 'Q:\\')
    using both HOMESHARE/HOMEDRIVE environment variables and active DRIVE_REMOTE mounts.
    """
    global _UNC_TO_DRIVE_CACHE
    if _UNC_TO_DRIVE_CACHE is not None:
        return _UNC_TO_DRIVE_CACHE
    mapping: Dict[str, str] = {}
    if os.name == "nt":
        homeshare = os.environ.get("HOMESHARE", "").strip().rstrip("\\/")
        homedrive = os.environ.get("HOMEDRIVE", "").strip().rstrip("\\/")
        if homeshare and homedrive and len(homedrive) == 2 and homedrive[1] == ":":
            mapping[homeshare.lower()] = homedrive.upper() + "\\"
        try:
            import ctypes
            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            for i in range(26):
                if bitmask & (1 << i):
                    drv = f"{chr(65 + i)}:\\"
                    # DRIVE_REMOTE == 4
                    if ctypes.windll.kernel32.GetDriveTypeW(drv) == 4:
                        try:
                            unc_root = str(Path(drv).resolve()).rstrip("\\/")
                            if unc_root.startswith("\\\\?\\UNC\\"):
                                unc_root = "\\\\" + unc_root[8:]
                            if unc_root.startswith("\\\\"):
                                mapping[unc_root.lower()] = drv
                        except Exception:
                            pass
        except Exception:
            pass
    _UNC_TO_DRIVE_CACHE = mapping
    return mapping


def _normalize_workspace_path(project_root: Path) -> Path:
    """
    Normalizes a workspace path across both Home (local drives C:\\, D:\\) and Enterprise
    Active Directory environments (where Path.resolve() converts a mapped network drive
    like Q:\\ into its underlying SMB UNC share \\\\server\\home$\\user).
    Maps UNC paths under mounted network drives or HOMESHARE back to their drive letter.
    """
    raw = os.path.abspath(str(project_root))
    if os.name == "nt" and raw.startswith("\\\\"):
        raw_lower = raw.rstrip("\\/").lower()
        # Check explicit HOMESHARE env override first (also supports unit test mocking), then cached drive mounts
        unc_map = dict(_get_unc_to_drive_map())
        homeshare = os.environ.get("HOMESHARE", "").strip().rstrip("\\/")
        homedrive = os.environ.get("HOMEDRIVE", "").strip().rstrip("\\/")
        if homeshare and homedrive and len(homedrive) == 2 and homedrive[1] == ":":
            unc_map[homeshare.lower()] = homedrive.upper() + "\\"
        for share_lower, drive_root in sorted(unc_map.items(), key=lambda x: len(x[0]), reverse=True):
            if raw_lower == share_lower or raw_lower.startswith(share_lower + "\\"):
                rel = raw.rstrip("\\/")[len(share_lower):].lstrip("\\/")
                mapped_candidate = Path(drive_root + rel) if rel else Path(drive_root)
                try:
                    if mapped_candidate.exists():
                        return mapped_candidate
                except Exception:
                    pass
    return Path(raw)


def _is_temp_workspace(project_root: Path) -> bool:
    """Returns True if project_root is inside the OS temporary directory."""
    try:
        temp_root = Path(tempfile.gettempdir()).resolve()
        resolved = project_root.resolve()
        return resolved == temp_root or temp_root in resolved.parents
    except Exception:
        return False


def _is_excluded_workspace_path(project_root: Path, allow_temp_subdirs: bool = False) -> bool:
    """
    Returns True if project_root is inside OS temp (unless allow_temp_subdirs=True),
    bare drive roots (C:\\, D:\\, Q:\\), local user home root (%USERPROFILE%), Enterprise AD
    network home root (%HOMESHARE% or mapped network drive root), protected ~/.gemini
    subdirectories, or OS/application installation directories.
    """
    if not project_root:
        return True
    if not allow_temp_subdirs and _is_temp_workspace(project_root):
        return True
    try:
        abs_root = Path(os.path.abspath(str(project_root)))
        user_home_abs = Path(os.path.abspath(str(resolve_user_home())))
        path_home_abs = Path(os.path.abspath(str(Path.home())))
        gemini_abs = Path(os.path.abspath(str(GEMINI_DIR)))
        ag_data_abs = gemini_abs / "antigravity"
        scratch_abs = ag_data_abs / "scratch"
        temp_abs = Path(os.path.abspath(str(tempfile.gettempdir())))
        if len(abs_root.parts) <= 1 or abs_root in (user_home_abs, path_home_abs, gemini_abs, ag_data_abs, scratch_abs, temp_abs):
            return True
        if os.name == "nt":
            abs_clean = str(abs_root).rstrip("\\/").lower()
            homeshare = os.environ.get("HOMESHARE", "").strip().rstrip("\\/")
            if homeshare and abs_clean == homeshare.lower():
                return True
            if abs_clean in _get_unc_to_drive_map():
                return True
            homedrive = os.environ.get("HOMEDRIVE", "").strip()
            homepath = os.environ.get("HOMEPATH", "").strip()
            if homedrive and homepath:
                hd_root = os.path.abspath(homedrive + homepath).rstrip("\\/").lower()
                if abs_clean == hd_root:
                    return True
        for sub in ("config", "database", "plugins_archive", "share", "credentials"):
            try:
                abs_root.relative_to(gemini_abs / sub)
                return True
            except ValueError:
                pass
        for ag_sub in ("annotations", "brain", "browser_recordings", "builtin", "bin", "conversations", "crashes", "knowledge", "lib", "mcp", "plugin_data", "presence", "sidecar_data"):
            try:
                abs_root.relative_to(ag_data_abs / ag_sub)
                return True
            except ValueError:
                pass
        for env_key in ("LOCALAPPDATA", "APPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "SYSTEMROOT"):
            env_val = os.environ.get(env_key)
            if not env_val:
                continue
            env_abs = Path(os.path.abspath(env_val))
            if abs_root == env_abs:
                return True
            for app_sub in (env_abs / "Programs", env_abs / "Antigravity", env_abs / "antigravity"):
                try:
                    abs_root.relative_to(app_sub)
                    return True
                except ValueError:
                    pass
        agent_exe = os.environ.get("ANTIGRAVITY_AGENTAPI_EXE")
        if agent_exe:
            try:
                exe_path = Path(os.path.abspath(agent_exe))
                if len(exe_path.parents) >= 3:
                    app_install_root = exe_path.parents[2]
                    if app_install_root not in (user_home_abs, path_home_abs) and len(app_install_root.parts) >= 2:
                        abs_root.relative_to(app_install_root)
                        return True
            except ValueError:
                pass
        return False
    except Exception:
        return True


def _strip_credentials_from_url(url: Optional[str]) -> Optional[str]:
    """Removes embedded user:token@ credentials from an HTTP(S) Git remote URL."""
    if not url:
        return url
    return re.sub(r"^(https?://)[^@/\s]+@", r"\1", url.strip())


def _build_auth_clone_url(clone_url: Optional[str], user: str, token: str) -> Optional[str]:
    """Returns a clean, credential-free HTTP(S) clone URL (credentials are passed transiently via Git headers/helper)."""
    return _strip_credentials_from_url(clone_url)


def _get_git_auth_cmd_prefix(db_path: Path = DB_PATH) -> List[str]:
    """
    Returns ['git', '-c', 'http.extraHeader=Authorization: Basic <base64>']
    so headless Git operations authenticate transiently in memory without storing
    plaintext tokens in .git/config.
    """
    cfg = get_gitea_config(db_path=db_path)
    user = cfg.get("user", "")
    token = cfg.get("token", "")
    if user and token:
        b64 = base64.b64encode(f"{user}:{token}".encode("utf-8")).decode("ascii")
        return ["git", "-c", f"http.extraHeader=Authorization: Basic {b64}"]
    return ["git"]


def register_git_credential_helper(gitea_url: Optional[str] = None) -> None:
    """
    Registers 'skillsdb gitea-credential-helper' in global Git config for the local Gitea
    endpoint so manual 'git push' / 'git pull' commands in terminals work seamlessly
    without storing tokens in .git/config across both Home and Enterprise AD workstations.
    """
    if not shutil.which("git"):
        return
    endpoints = {"http://127.0.0.1:3000", "http://localhost:3000"}
    if gitea_url:
        endpoints.add(gitea_url.rstrip("/"))
    for ep in endpoints:
        try:
            subprocess.run(
                ["git", "config", "--global", f"credential.{ep}.helper", "!skillsdb gitea-credential-helper"],
                capture_output=True,
                timeout=3,
            )
        except Exception:
            pass


def gitea_credential_helper(action: str = "get", db_path: Path = DB_PATH) -> None:
    """Git credential helper protocol implementation reading Gitea credentials from customizations.db."""
    if action != "get":
        return
    cfg = get_gitea_config(db_path=db_path)
    user = cfg.get("user", "")
    token = cfg.get("token", "")
    if user and token:
        sys.stdout.write(f"username={user}\npassword={token}\n\n")
        sys.stdout.flush()


def _scrub_workspace_git_config_credentials(project_root: Path, db_path: Path = DB_PATH) -> bool:
    """
    Inspects project_root/.git/config. If any remote URL contains embedded user:token@
    credentials, migrates the token into customizations.db (if not already present) and
    rewrites .git/config in place with the clean token-free URL.
    """
    git_cfg = project_root / ".git" / "config"
    if not git_cfg.exists():
        return False
    try:
        raw_cfg = git_cfg.read_text(encoding="utf-8", errors="ignore")
        pattern = re.compile(r"(url\s*=\s*)(https?://)([^:@/\s]+):([^@/\s]+)@((?:127\.0\.0\.1|localhost)(?::\d+)?/([^\s/]+)/[^\s]+)")
        matches = list(pattern.finditer(raw_cfg))
        if not matches:
            return False

        if db_path:
            now_iso = datetime.now(timezone.utc).isoformat()
            with managed_connection(db_path) as conn:
                cur = conn.cursor()
                execute_with_retry(
                    cur,
                    "CREATE TABLE IF NOT EXISTS runtime_config (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT);",
                )
                cur.execute("SELECT COUNT(*) FROM runtime_config WHERE key = 'gitea_token';")
                has_token = cur.fetchone()[0] > 0
                if not has_token:
                    m0 = matches[0]
                    scheme, _, auth_tok, host_and_path, repo_owner = m0.group(2), m0.group(3), m0.group(4), m0.group(5), m0.group(6)
                    host_port = host_and_path.split("/", 1)[0]
                    learned_url = f"{scheme}{host_port}"
                    cur.executemany(
                        "INSERT OR IGNORE INTO runtime_config (key, value, updated_at) VALUES (?, ?, ?);",
                        [
                            ("gitea_url", learned_url, now_iso),
                            ("gitea_user", repo_owner, now_iso),
                            ("gitea_token", auth_tok, now_iso),
                        ],
                    )
                    conn.commit()

        cleaned_cfg = pattern.sub(r"\1\2\5", raw_cfg)
        if cleaned_cfg != raw_cfg:
            git_cfg.write_text(cleaned_cfg, encoding="utf-8")
            return True
    except Exception:
        pass
    return False


def _infer_workspace_last_active_iso(ws_path: Path) -> str:
    """Infers the real last-active ISO-8601 UTC timestamp of a workspace from its .agents/memory.db, .git/logs/HEAD, or source files."""
    best_mtime = 0.0
    try:
        candidates = [
            ws_path / ".agents" / "memory.db",
            ws_path / ".git" / "logs" / "HEAD",
            ws_path / ".git" / "index",
            ws_path / ".git" / "HEAD",
        ]
        for c in candidates:
            if c.exists():
                mt = c.stat().st_mtime
                if mt > best_mtime:
                    best_mtime = mt
        if best_mtime == 0.0:
            for item in ws_path.iterdir():
                if item.name not in (".git", ".agents", "node_modules", "__pycache__"):
                    mt = item.stat().st_mtime
                    if mt > best_mtime:
                        best_mtime = mt
        if best_mtime == 0.0:
            best_mtime = ws_path.stat().st_mtime
    except Exception:
        pass
    if best_mtime > 0.0:
        return datetime.fromtimestamp(best_mtime, tz=timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def learn_workspace(project_root: Path, db_path: Path = DB_PATH, touch_timestamp: bool = True) -> Optional[str]:
    """
    Organically learns and persists a project workspace path on demand into runtime_config.
    If touch_timestamp=True (active session work), updates updated_at to current UTC time.
    If touch_timestamp=False (passive background discovery), preserves existing updated_at
    or infers the real last-modified timestamp from disk.
    """
    if not project_root or _is_excluded_workspace_path(project_root):
        return None
    try:
        abs_root = _normalize_workspace_path(project_root)
        if not abs_root.exists() or not abs_root.is_dir():
            return None
        if not ((abs_root / ".agents").exists() or (abs_root / ".git" / "HEAD").exists()):
            return None
        ws_key = f"workspace:{abs_root.name}"
        now_iso = datetime.now(timezone.utc).isoformat()

        _scrub_workspace_git_config_credentials(abs_root, db_path=db_path)

        if db_path:
            with managed_connection(db_path) as conn:
                cur = conn.cursor()
                execute_with_retry(
                    cur,
                    "CREATE TABLE IF NOT EXISTS runtime_config (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT);",
                )
                scratch_abs = Path(os.path.abspath(str(GEMINI_DIR / "antigravity" / "scratch")))
                try:
                    abs_root.relative_to(scratch_abs)
                    new_is_scratch = True
                except ValueError:
                    new_is_scratch = False

                cur.execute(
                    "SELECT key, value, updated_at FROM runtime_config WHERE LOWER(key) = LOWER(?);",
                    (ws_key,),
                )
                existing_rows = cur.fetchall()
                skip_insert = False
                preserved_ts = None
                for ex_key, ex_val, ex_ts in existing_rows:
                    if new_is_scratch and ex_val and Path(ex_val).exists():
                        try:
                            Path(os.path.abspath(str(ex_val))).relative_to(scratch_abs)
                        except ValueError:
                            skip_insert = True
                            break
                    if ex_key == ws_key and ex_ts:
                        preserved_ts = ex_ts
                    if ex_key != ws_key:
                        execute_with_retry(cur, "DELETE FROM runtime_config WHERE key = ?;", (ex_key,))

                if not skip_insert:
                    target_ts = now_iso if touch_timestamp else (preserved_ts or _infer_workspace_last_active_iso(abs_root))
                    execute_with_retry(
                        cur,
                        "INSERT OR REPLACE INTO runtime_config (key, value, updated_at) VALUES (?, ?, ?);",
                        (ws_key, str(abs_root), target_ts),
                    )
                conn.commit()
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
    Also normalizes Enterprise AD UNC paths back to their mounted drive letter.
    """
    learned: Dict[str, str] = {}
    stale_keys: List[str] = []
    normalized_updates: List[Tuple[str, str]] = []
    valid_paths_to_scrub: List[Path] = []
    if not db_path or not Path(db_path).exists():
        return learned
    try:
        with managed_connection(db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_config';")
            if cur.fetchone():
                cur.execute(
                    "SELECT key, value, updated_at FROM runtime_config WHERE key LIKE 'workspace:%' ORDER BY updated_at ASC;"
                )
                rows = cur.fetchall()
                scratch_abs = Path(os.path.abspath(str(GEMINI_DIR / "antigravity" / "scratch")))
                by_lower: Dict[str, Tuple[str, str]] = {}

                for k, v, _ in rows:
                    name = k.split("workspace:", 1)[1]
                    if not name or not v:
                        stale_keys.append(k)
                        continue
                    p = _normalize_workspace_path(Path(v))
                    if str(p) != v:
                        v = str(p)
                        normalized_updates.append((v, k))
                    if _is_excluded_workspace_path(p):
                        stale_keys.append(k)
                        for stray in (".agents", ".git"):
                            stray_dir = p / stray
                            if stray_dir.exists() and str(p).startswith(str(GEMINI_DIR / "config" / "plugins")):
                                shutil.rmtree(stray_dir, ignore_errors=True)
                        continue
                    if p.exists() and p.is_dir():
                        valid_paths_to_scrub.append(p)
                        lower_name = name.lower()
                        if lower_name in by_lower:
                            prev_key, prev_val = by_lower[lower_name]
                            try:
                                Path(os.path.abspath(v)).relative_to(scratch_abs)
                                curr_is_scratch = True
                            except ValueError:
                                curr_is_scratch = False
                            try:
                                Path(os.path.abspath(prev_val)).relative_to(scratch_abs)
                                prev_is_scratch = True
                            except ValueError:
                                prev_is_scratch = False

                            if curr_is_scratch and not prev_is_scratch:
                                if k != prev_key:
                                    stale_keys.append(k)
                                continue
                            else:
                                if prev_key != k:
                                    stale_keys.append(prev_key)
                                learned.pop(prev_key.split("workspace:", 1)[1], None)
                        by_lower[lower_name] = (k, v)
                        learned[name] = v
                    elif prune_missing:
                        try:
                            if p.parent.exists():
                                stale_keys.append(k)
                        except Exception:
                            pass
                if normalized_updates:
                    cur.executemany("UPDATE runtime_config SET value = ? WHERE key = ?;", normalized_updates)
                if stale_keys:
                    cur.executemany("DELETE FROM runtime_config WHERE key = ?;", [(sk,) for sk in set(stale_keys)])
                if normalized_updates or stale_keys:
                    conn.commit()
        for p in valid_paths_to_scrub:
            _scrub_workspace_git_config_credentials(p, db_path=db_path)
    except Exception:
        pass
    return learned


def get_gitea_config(db_path: Path = DB_PATH) -> Dict[str, str]:
    """
    Returns active Gitea and workspace configuration.
    Priority:
    1. Environment variables (SKILLSDB_GITEA_URL, SKILLSDB_GITEA_USER, SKILLSDB_GITEA_TOKEN, SKILLSDB_WORKSPACES_ROOT)
    2. runtime_config table in customizations.db
    3. Default fallback constants in config.py
    """
    db_cfg: Dict[str, str] = {}
    if db_path and Path(db_path).exists():
        try:
            with managed_connection(db_path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_config';")
                if cur.fetchone():
                    cur.execute("SELECT key, value FROM runtime_config;")
                    for k, v in cur.fetchall():
                        if v:
                            db_cfg[k] = v
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
    svc_name = (
        os.environ.get("SKILLSDB_GITEA_SERVICE")
        or db_cfg.get("gitea_service")
        or GITEA_SERVICE_NAME
    )
    enabled = (
        os.environ.get("SKILLSDB_GITEA_ENABLED")
        or db_cfg.get("gitea_enabled")
        or "auto"
    )

    return {
        "url": url,
        "user": user,
        "token": token,
        "workspaces_root": workspaces_root,
        "remote_share": remote_share,
        "service_name": svc_name,
        "enabled": enabled,
    }


def set_gitea_config(
    url: Optional[str] = None,
    user: Optional[str] = None,
    token: Optional[str] = None,
    workspaces_root: Optional[str] = None,
    remote_share: Optional[str] = None,
    service: Optional[str] = None,
    enabled: Optional[bool] = None,
    db_path: Path = DB_PATH,
) -> Dict[str, str]:
    """Persists Gitea and workspace settings in runtime_config and registers the Git credential helper."""
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
    if service is not None:
        updates.append(("gitea_service", str(service).strip(), now_iso))
    if enabled is not None:
        updates.append(("gitea_enabled", "true" if enabled else "false", now_iso))

    if updates and db_path:
        with managed_connection(db_path) as conn:
            cur = conn.cursor()
            execute_with_retry(
                cur,
                "CREATE TABLE IF NOT EXISTS runtime_config (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT);",
            )
            cur.executemany(
                "INSERT OR REPLACE INTO runtime_config (key, value, updated_at) VALUES (?, ?, ?);",
                updates,
            )
            conn.commit()

    cfg = get_gitea_config(db_path=db_path)
    register_git_credential_helper(cfg.get("url"))
    return cfg


def discover_all_workspaces(base_dir: Optional[Path] = None, db_path: Path = DB_PATH) -> List[Path]:
    """
    Discovers all active Antigravity workspaces organically from:
    1. Learned project workspaces in runtime_config (workspace:<name>)
    2. Registered Antigravity IDE project definitions in ~/.gemini/config/projects/*.json
    3. Default Antigravity scratch workspaces directory (~/.gemini/antigravity/scratch)
    4. Optional base_dir or configured workspaces_root
    Automatically learns newly discovered projects, scrubs legacy .git/config credentials, and prunes deleted ones.
    """
    config = get_gitea_config(db_path=db_path)
    seen_resolved = set()
    workspaces: List[Path] = []

    def _add_candidate(p: Path):
        try:
            if not p.exists() or not p.is_dir():
                return
            abs_p = _normalize_workspace_path(p)
            if _is_excluded_workspace_path(abs_p):
                return
            norm_key = str(abs_p).lower()
            if norm_key in seen_resolved:
                return
            has_agents = (abs_p / ".agents").exists()
            has_git = (abs_p / ".git" / "HEAD").exists()
            has_files = any(f.is_file() for f in abs_p.iterdir())
            if has_agents or has_git or has_files:
                seen_resolved.add(norm_key)
                workspaces.append(abs_p)
                learn_workspace(abs_p, db_path=db_path, touch_timestamp=False)
        except Exception:
            pass

    learned_map = get_learned_workspaces(db_path=db_path, prune_missing=True)
    for _, path_str in sorted(learned_map.items(), key=lambda x: x[0].lower()):
        _add_candidate(Path(path_str))

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

    scratch_dir = GEMINI_DIR / "antigravity" / "scratch"
    if scratch_dir.exists():
        for item in sorted(scratch_dir.iterdir(), key=lambda x: x.name.lower()):
            if item.is_dir() and not item.name.startswith("."):
                _add_candidate(item)

    root_candidate = base_dir
    if not root_candidate and config.get("workspaces_root"):
        root_candidate = Path(config["workspaces_root"])
    if root_candidate and root_candidate.exists():
        for item in sorted(root_candidate.iterdir(), key=lambda x: x.name.lower()):
            if item.is_dir() and not item.name.startswith("."):
                _add_candidate(item)

    return workspaces


def get_learned_workspaces_with_timestamps(db_path: Path = DB_PATH) -> Dict[str, Tuple[str, str]]:
    """Returns learned workspaces mapped as name -> (path_str, last_active_date_str)."""
    learned = get_learned_workspaces(db_path=db_path, prune_missing=True)
    ts_map: Dict[str, str] = {}
    if db_path and Path(db_path).exists():
        try:
            with managed_connection(db_path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT key, updated_at FROM runtime_config WHERE key LIKE 'workspace:%';")
                for k, upd in cur.fetchall():
                    ts_map[k.split("workspace:", 1)[1]] = (upd or "")[:10]
        except Exception:
            pass
    result: Dict[str, Tuple[str, str]] = {}
    for name, path_str in learned.items():
        dt = ts_map.get(name) or _infer_workspace_last_active_iso(Path(path_str))[:10]
        result[name] = (path_str, dt)
    return result


def gitea_config_summary(auto_detect: bool = False, db_path: Path = DB_PATH):
    """Displays active Gitea configuration and organically learned project paths with last-active timestamps."""
    if auto_detect:
        discover_all_workspaces(db_path=db_path)

    cfg = get_gitea_config(db_path=db_path)
    learned_ts = get_learned_workspaces_with_timestamps(db_path=db_path)
    tok = cfg.get("token", "")
    masked_tok = f"{tok[:6]}...{tok[-4:]}" if len(tok) > 10 else ("(set)" if tok else "(not set)")

    enabled_raw = str(cfg.get("enabled", "auto")).strip().lower()
    if enabled_raw in ("false", "0", "no", "off", "disabled"):
        mode_str = "Disabled (Standalone SkillsDB-Only Mode)"
    elif tok or cfg.get("user") or enabled_raw in ("true", "1", "yes", "on", "enabled"):
        mode_str = "Active (Gitea Integration Enabled)"
    else:
        mode_str = "Standalone SkillsDB-Only (Gitea Optional / Unconfigured)"
    print("=" * 60)
    print("SkillsDB - Self-Learning Gitea & Workspace Configuration")
    print("=" * 60)
    print(f"Operation Mode:     {mode_str}")
    print(f"Gitea URL:          {cfg.get('url')}")
    print(f"Repository Owner:   {cfg.get('user') or '(auto-learned on init)'}")
    print(f"API Token:          {masked_tok}")
    print(f"Learned Workspaces: {len(learned_ts)} projects tracked on demand")
    for name, (path_str, dt) in sorted(learned_ts.items(), key=lambda x: x[0].lower()):
        print(f"  * {name:30} [{dt}] -> {path_str}")
    print("=" * 60)
