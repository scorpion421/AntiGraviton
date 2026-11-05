"""
Keep a Changelog 1.1.0 parsing and automated Gitea/GitHub release publishing for SkillsDB.
"""

import os
import re
import json
import uuid
import shutil
import urllib.parse
import tempfile
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

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
    changelog_path = Path(changelog_path)
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

    ver_re = re.compile(r"^## \[[vV]?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.\-]+)?)\](?: - (\d{4}-\d{2}-\d{2}))?\s*$")
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
    project_root = Path(os.path.abspath(str(project_root)))
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


RELEASE_DELIVERABLE_EXTENSIONS = (
    ".apk", ".aab", ".ipa",
    ".exe", ".msi", ".msix",
    ".dmg", ".pkg", ".deb", ".rpm", ".appimage",
    ".xpi", ".crx", ".vsix",
    ".whl", ".jar", ".nupkg",
    ".zip", ".7z", ".tar.gz",
)

DIRECT_ROOT_ARTIFACT_EXTENSIONS = (
    ".apk", ".aab", ".ipa",
    ".msi", ".msix", ".dmg", ".pkg", ".deb", ".rpm", ".appimage",
    ".xpi", ".crx", ".vsix", ".whl", ".nupkg",
)


def _discover_project_release_artifacts(
    project_root: Path,
    repo_name: str,
    tag: str,
) -> List[Path]:
    """
    Discovers compiled deliverable artifacts belonging to the project (APKs, binaries,
    installers, extension packages, wheels, or release archives in releases/ and dist/).
    Excludes intermediate unsigned/unaligned build files and third-party vendor binaries.
    """
    project_root = Path(project_root)
    clean_ver = tag.lstrip("vV")
    repo_lower = repo_name.lower()
    found: List[Path] = []
    seen: set = set()

    def _is_excluded_name(fname: str) -> bool:
        low = fname.lower()
        if low.endswith(("-unsigned.apk", "-aligned.apk", "-unaligned.apk", ".tmp", ".bak")):
            return True
        if low.startswith(("gitea-", "apktool", "jadx", "uber-apk-signer", "bundletool")) and "gitea" not in repo_lower:
            return True
        return False

    dedicated_dirs = [
        project_root / "releases",
        project_root / "dist",
        project_root / "out",
        project_root / "build" / "outputs" / "apk" / "release",
        project_root / "build" / "outputs" / "bundle" / "release",
    ]
    for sdir in dedicated_dirs:
        if sdir.exists() and sdir.is_dir():
            for p in sorted(sdir.iterdir()):
                if not p.is_file() or _is_excluded_name(p.name):
                    continue
                low_name = p.name.lower()
                if any(low_name.endswith(ext) for ext in RELEASE_DELIVERABLE_EXTENSIONS):
                    rp = p.resolve()
                    if rp not in seen:
                        seen.add(rp)
                        found.append(p)

    if project_root.exists() and project_root.is_dir():
        for p in sorted(project_root.iterdir()):
            if not p.is_file() or _is_excluded_name(p.name):
                continue
            low_name = p.name.lower()
            if any(low_name.endswith(ext) for ext in DIRECT_ROOT_ARTIFACT_EXTENSIONS):
                rp = p.resolve()
                if rp not in seen:
                    seen.add(rp)
                    found.append(p)
            elif low_name.endswith((".exe", ".jar")):
                if (
                    repo_lower in low_name
                    or "clean" in low_name
                    or "signed" in low_name
                    or "release" in low_name
                    or "setup" in low_name
                    or "installer" in low_name
                ):
                    rp = p.resolve()
                    if rp not in seen:
                        seen.add(rp)
                        found.append(p)

    tag_matched = [
        p for p in found
        if (tag.lower() in p.name.lower() or (clean_ver and clean_ver.lower() in p.name.lower()))
    ]
    candidates = tag_matched if tag_matched else found
    candidates.sort(
        key=lambda p: (
            0 if ("clean" in p.name.lower() or "signed" in p.name.lower() or "release" in p.name.lower()) else 1,
            p.name,
        )
    )
    return candidates


def _attach_release_binary_assets(
    base_url: str,
    owner: str,
    repo_name: str,
    rel_id: Any,
    tag: str,
    existing_assets: Any,
    token: str,
    project_root: Path,
    overwrite: bool = False,
) -> List[str]:
    """
    Automatically attaches compiled project deliverable artifacts (*.apk, *.aab, *.exe, *.msi,
    *.xpi, *.whl, etc.) from project_root (or releases/ / dist/ or git tag history)
    as downloadable Gitea Release Assets via POST /api/v1/repos/{owner}/{repo}/releases/{id}/assets.
    """
    uploaded_names: List[str] = []
    if not rel_id or not base_url or not owner or not repo_name or not token:
        return uploaded_names

    project_root = Path(os.path.abspath(str(project_root)))
    existing_map = {
        a.get("name"): a.get("id")
        for a in (existing_assets or [])
        if isinstance(a, dict) and a.get("name")
    }

    candidates = _discover_project_release_artifacts(project_root, repo_name, tag)
    if not candidates:
        return uploaded_names

    clean_ver = tag.lstrip("vV")
    quoted_owner = urllib.parse.quote(str(owner), safe="")
    quoted_repo = urllib.parse.quote(str(repo_name), safe="")

    # Select distinct deliverables belonging to the primary artifact's ecosystem family
    _ecosystem_families = [
        {".apk", ".aab"},
        {".ipa"},
        {".exe", ".msi", ".msix"},
        {".dmg", ".pkg"},
        {".deb", ".rpm", ".appimage"},
        {".whl", ".tar.gz"},
        {".xpi", ".crx", ".vsix"},
    ]
    first_ext = ".tar.gz" if candidates[0].name.lower().endswith(".tar.gz") else candidates[0].suffix.lower()
    allowed_exts = {first_ext}
    for fam in _ecosystem_families:
        if first_ext in fam:
            allowed_exts = fam
            break

    selected_files: List[Path] = []
    seen_apk = False
    for cand in candidates:
        cand_ext = ".tar.gz" if cand.name.lower().endswith(".tar.gz") else cand.suffix.lower()
        if cand_ext not in allowed_exts:
            continue
        if cand_ext == ".apk":
            if seen_apk:
                continue
            seen_apk = True
        selected_files.append(cand)

    for primary_file in selected_files:
        ext = ".tar.gz" if primary_file.name.lower().endswith(".tar.gz") else primary_file.suffix
        low_primary = primary_file.name.lower()

        if low_primary.endswith(".apk"):
            asset_name = f"{repo_name}-{tag}-clean-signed.apk" if "clean" in low_primary else f"{repo_name}-{tag}.apk"
            content_type = "application/vnd.android.package-archive"
        else:
            if tag.lower() in low_primary or (clean_ver and clean_ver.lower() in low_primary) or len(selected_files) > 1:
                asset_name = primary_file.name if (tag.lower() in low_primary or (clean_ver and clean_ver.lower() in low_primary)) else f"{repo_name}-{tag}-{primary_file.name}"
            else:
                asset_name = f"{repo_name}-{tag}{ext}"
            content_type = "application/zip" if ext in (".zip", ".whl", ".jar", ".xpi") else "application/octet-stream"

        if asset_name in existing_map and not overwrite:
            continue

        file_bytes: Optional[bytes] = None
        is_tag_specific_file = tag.lower() in low_primary or (clean_ver and clean_ver.lower() in low_primary)
        if is_tag_specific_file:
            try:
                file_bytes = primary_file.read_bytes()
            except Exception:
                pass

        if not file_bytes and (project_root / ".git" / "HEAD").exists():
            try:
                rel_path = primary_file.relative_to(project_root).as_posix()
                git_show = subprocess.run(
                    ["git", "show", f"{tag}:{rel_path}"],
                    cwd=str(project_root),
                    capture_output=True,
                    timeout=15,
                )
                if git_show.returncode == 0 and git_show.stdout:
                    file_bytes = git_show.stdout
            except Exception:
                pass

        if not file_bytes:
            try:
                file_bytes = primary_file.read_bytes()
            except Exception:
                continue

        try:
            if asset_name in existing_map and existing_map[asset_name]:
                del_req = urllib.request.Request(
                    f"{base_url.rstrip('/')}/api/v1/repos/{quoted_owner}/{quoted_repo}/releases/{rel_id}/assets/{existing_map[asset_name]}",
                    method="DELETE",
                    headers={"Authorization": f"token {token}", "User-Agent": "SkillsDB-GiteaSync/1.0"},
                )
                with urllib.request.urlopen(del_req, timeout=10.0):
                    pass
            boundary = f"----SkillsDBAssetBoundary{uuid.uuid4().hex}"
            body = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="attachment"; filename="{asset_name}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
            up_url = f"{base_url.rstrip('/')}/api/v1/repos/{quoted_owner}/{quoted_repo}/releases/{rel_id}/assets?name={urllib.parse.quote(asset_name)}"
            up_req = urllib.request.Request(
                up_url,
                data=body,
                method="POST",
                headers={
                    "Authorization": f"token {token}",
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "User-Agent": "SkillsDB-GiteaSync/1.0",
                },
            )
            with urllib.request.urlopen(up_req, timeout=30.0) as resp:
                if resp.status in (200, 201):
                    uploaded_names.append(asset_name)
                    existing_map[asset_name] = True
        except Exception as e:
            if hasattr(e, "close"):
                try:
                    e.close()
                except Exception:
                    pass
    return uploaded_names


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
    project_root = Path(os.path.abspath(str(project_root)))
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
    quoted_owner = urllib.parse.quote(str(owner or ""), safe="")
    quoted_repo = urllib.parse.quote(str(repo_name or ""), safe="")
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
                if token and owner:
                    subprocess.run(
                        _get_git_auth_cmd_prefix(db_path=db_path) + ["push", "origin", "--tags"],
                        cwd=str(project_root),
                        capture_output=True,
                        timeout=30,
                    )
                else:
                    synced_tags.append(latest_tag)
        except Exception:
            pass

    latest_tag = entries[0]["tag"]
    gitea_enabled = str(config.get("enabled", "auto")).strip().lower() not in ("false", "0", "no", "off", "disabled")
    if gitea_enabled and token and owner and base_url:
        try:
            existing_map: Dict[str, Dict[str, Any]] = {}
            page = 1
            while page <= 5:
                rel_url = f"{base_url}/api/v1/repos/{quoted_owner}/{quoted_repo}/releases?limit=100&page={page}"
                req = urllib.request.Request(
                    rel_url,
                    headers={
                        "Authorization": f"token {token}",
                        "User-Agent": "SkillsDB-GiteaSync/1.0",
                    },
                )
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    page_list = json.loads(resp.read().decode("utf-8"))
                if not isinstance(page_list, list) or not page_list:
                    break
                for r in page_list:
                    if isinstance(r, dict) and r.get("tag_name"):
                        existing_map[str(r.get("tag_name"))] = r
                if len(page_list) < 100:
                    break
                page += 1

            for entry in reversed(entries):
                tag = entry["tag"]
                body = entry["body"]
                rel_title = f"{repo_name} {tag}"
                is_prerelease = "-" in entry["version"]
                if tag in existing_map:
                    existing_body = (existing_map[tag].get("body") or "").strip()
                    is_latest = (tag == latest_tag)
                    body_matches = (existing_body == body.strip()) if is_latest else ("keepachangelog.com" in existing_body)
                    if not overwrite_existing and body_matches and bool(existing_map[tag].get("prerelease")) == is_prerelease:
                        if is_latest:
                            _attach_release_binary_assets(base_url, owner, repo_name, existing_map[tag].get("id"), tag, existing_map[tag].get("assets", []), token, project_root)
                        continue
                    rel_id = existing_map[tag]["id"]
                    target_url = f"{base_url}/api/v1/repos/{quoted_owner}/{quoted_repo}/releases/{rel_id}"
                    method = "PATCH"
                else:
                    target_url = f"{base_url}/api/v1/repos/{quoted_owner}/{quoted_repo}/releases"
                    method = "POST"

                target_branch = "main"
                try:
                    br_res = subprocess.run(
                        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                        cwd=str(project_root),
                        capture_output=True,
                        text=True,
                        timeout=3,
                    )
                    br_name = (br_res.stdout or "").strip()
                    if is_prerelease and br_res.returncode == 0 and br_name and br_name != "HEAD":
                        target_branch = br_name
                except Exception:
                    pass

                payload = json.dumps({
                    "tag_name": tag,
                    "target_commitish": target_branch,
                    "name": rel_title,
                    "body": body,
                    "draft": False,
                    "prerelease": is_prerelease,
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
                        up_data = json.loads(up_resp.read().decode("utf-8"))
                        _attach_release_binary_assets(base_url, owner, repo_name, up_data.get("id"), tag, up_data.get("assets", []), token, project_root)
                        if tag not in synced_tags:
                            synced_tags.append(tag)
        except Exception as e:
            if hasattr(e, "close"):
                try:
                    e.close()
                except Exception:
                    pass

    if sync_github and shutil.which("gh"):
        try:
            gh_slug = _resolve_github_repo_slug(project_root)
            repo_args = ["--repo", gh_slug] if gh_slug else []
            if gh_slug:
                subprocess.run(
                    ["git", "push", "github", "--tags"],
                    cwd=str(project_root),
                    capture_output=True,
                    timeout=30,
                )

            # Fetch all existing GitHub releases in a single API call instead of N*2 subprocesses
            gh_existing_map: Optional[Dict[str, str]] = None
            if gh_slug:
                api_res = subprocess.run(
                    ["gh", "api", f"repos/{gh_slug}/releases?per_page=100"],
                    cwd=str(project_root),
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                if api_res.returncode == 0 and (api_res.stdout or "").strip():
                    try:
                        gh_list = json.loads(api_res.stdout)
                        if isinstance(gh_list, list):
                            gh_existing_map = {
                                str(r.get("tag_name")): str(r.get("body") or "")
                                for r in gh_list
                                if isinstance(r, dict) and r.get("tag_name")
                            }
                    except Exception:
                        gh_existing_map = None

            for entry in reversed(entries):
                tag = entry["tag"]
                body = entry["body"]
                rel_title = f"{repo_name} {tag}"
                is_latest = (tag == latest_tag)

                if gh_existing_map is not None:
                    if tag in gh_existing_map:
                        gh_body = gh_existing_map[tag].strip()
                        gh_matches = (gh_body == body.strip()) if is_latest else ("keepachangelog.com" in gh_body)
                        if not overwrite_existing and gh_matches:
                            continue
                        gh_action = "edit"
                    else:
                        gh_action = "create"
                else:
                    if not overwrite_existing and not is_latest:
                        continue
                    chk = subprocess.run(
                        ["gh", "release", "view", tag] + repo_args,
                        cwd=str(project_root),
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                    if chk.returncode == 0:
                        if not overwrite_existing and "keepachangelog.com" in (chk.stdout or ""):
                            continue
                        gh_action = "edit"
                    else:
                        gh_action = "create"

                with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as tf:
                    tf.write(body)
                    tmp_path = tf.name
                try:
                    prerelease_args = ["--prerelease"] if ("-" in entry["version"]) else []
                    if gh_action == "edit":
                        gh_e = subprocess.run(
                            ["gh", "release", "edit", tag, "--title", rel_title, "--notes-file", tmp_path] + prerelease_args + repo_args,
                            cwd=str(project_root),
                            capture_output=True,
                            timeout=20,
                        )
                        if gh_e.returncode == 0 and tag not in synced_tags:
                            synced_tags.append(tag)
                    else:
                        gh_c = subprocess.run(
                            ["gh", "release", "create", tag, "--title", rel_title, "--notes-file", tmp_path] + prerelease_args + repo_args,
                            cwd=str(project_root),
                            capture_output=True,
                            timeout=20,
                        )
                        if gh_c.returncode == 0 and tag not in synced_tags:
                            synced_tags.append(tag)
                finally:
                    Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass

    return synced_tags
