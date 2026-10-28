"""
Keep a Changelog 1.1.0 parsing and automated Gitea/GitHub release publishing for SkillsDB.
"""

import re
import json
import shutil
import tempfile
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Optional, Tuple, List

from ..config import DB_PATH
from .gitea_config import (
    _is_temp_workspace,
    _get_git_auth_cmd_prefix,
    get_gitea_config,
)


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
                v_txt = ver_file.read_text(encoding="utf-8-sig", errors="ignore").strip()
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



def _resolve_github_repo_slug(project_root: Path) -> Optional[str]:
    """Resolves the 'owner/repo' slug from a GitHub remote ('github' or 'origin') if configured."""
    for rem in ("github", "origin"):
        try:
            res = subprocess.run(
                ["git", "remote", "get-url", rem],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0 and "github.com" in (res.stdout or "").lower():
                m = re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?\s*$", res.stdout.strip(), re.IGNORECASE)
                if m:
                    return m.group(1)
        except Exception:
            pass
    return None


def sync_changelog_releases(
    project_root: Path,
    sync_github: bool = False,
    overwrite_existing: bool = False,
    db_path: Path = DB_PATH,
    repo_name: Optional[str] = None,
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
    resolved_repo_name = repo_name or project_root.name
    git_cfg = project_root / ".git" / "config"
    if not repo_name and git_cfg.exists():
        try:
            raw_cfg = git_cfg.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(r"^\s*url\s*=\s*https?://(?:[^@/\s]+@)?([^/\s]+)/([^/\s]+)/([^\s/]+?)(?:\.git)?\s*$", raw_cfg, re.MULTILINE):
                host_port, _, rname = m.groups()
                if "localhost:3000" in host_port or "127.0.0.1:3000" in host_port or (base_url and host_port in base_url):
                    resolved_repo_name = rname
                    break
        except Exception:
            pass
    repo_name = resolved_repo_name
    synced_tags: List[str] = []

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
                    _get_git_auth_cmd_prefix(db_path=db_path) + ["push", "origin", "--tags"],
                    cwd=str(project_root),
                    capture_output=True,
                    timeout=30,
                )
        except Exception:
            pass

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
                    gh_slug = _resolve_github_repo_slug(project_root)
                    repo_args = ["--repo", gh_slug] if gh_slug else []
                    chk = subprocess.run(
                        ["gh", "release", "view", tag] + repo_args,
                        cwd=str(project_root),
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                    if chk.returncode == 0:
                        if overwrite_existing or "keepachangelog.com" not in (chk.stdout or ""):
                            subprocess.run(
                                ["gh", "release", "edit", tag, "--title", rel_title, "--notes-file", tmp_path] + repo_args,
                                cwd=str(project_root),
                                capture_output=True,
                                timeout=20,
                            )
                    else:
                        subprocess.run(
                            ["gh", "release", "create", tag, "--title", rel_title, "--notes-file", tmp_path] + repo_args,
                            cwd=str(project_root),
                            capture_output=True,
                            timeout=20,
                        )
                finally:
                    Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass

    return synced_tags
