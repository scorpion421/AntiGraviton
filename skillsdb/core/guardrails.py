"""
Guardrails and deterministic execution verifiers for SkillsDB.
Enforces content safety, anti-bloat invariants, formatting standards, and task verification gates.
Designed to assist and safeguard the developer without bureaucratic friction or restrictions.
Inspired by the OpenAI Agents SDK & Evaluator-Optimizer architectural patterns.
"""

import os
import re
import sys
import json
import sqlite3
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple, Optional
from ..config import resolve_user_home, DB_PATH, read_jsonl_tail_lines
from .quota_router import compute_routing_decision, record_quota_ledger_entry

# Invariant characters
EM_DASH = "\u2014"
EN_DASH = "\u2013"

# Emoji Unicode ranges (Pictographs, symbols, supplementary planes)
EMOJI_PATTERN = re.compile(
    r"[\U00010000-\U0010ffff]"
    r"|[\u2600-\u27BF]"
    r"|[\u2300-\u23FF]"
    r"|[\u2B50-\u2B55]"
    r"|[\u203C-\u2049]"
)

# Deppenbindestrich patterns in German compound nouns (compounds that must not use hyphens)
DEPPENBINDESTRICH_PATTERN = re.compile(
    r"\b(?:"
    r"Token|Browser|Tab|Kontext|Subagenten|Abteilungs|Standard|Sitzungs|"
    r"Produktions|Speicher|Projekt|Entwickler|System|Architektur|Benchmark|"
    r"Changelog|Release|Reallife|Test|Unittest|Code|Skript|Prompt|Daten"
    r")-(?:"
    r"Reduktion|Tool|Wechsel|Kompaktierung|Isolation|Kontingent|Einstellung|"
    r"Dauer|Daten|Manager|Speicher|Fakten|Entscheidungen|Regeln|Protokoll|"
    r"Vergleich|Ersparnis|Suite|Ausführung|Verifikation|Analyse|Status|"
    r"Invariante|Muster|Board|Schranke"
    r")\b",
    re.IGNORECASE
)

# Secret and credential leak patterns
SECRET_PATTERNS = [
    ("OpenAI API Key", re.compile(r"\bsk-[a-zA-Z0-9_\-]{20,}\b")),
    ("OpenAI Project API Key", re.compile(r"\bsk-proj-[a-zA-Z0-9_\-]{20,}\b")),
    ("GitHub Personal Access Token", re.compile(r"\bgh[pousr]-[a-zA-Z0-9]{36,}\b")),
    ("AWS Access Key ID", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Private Key Header", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    (
        "Hardcoded Personal or Corporate Identifier",
        re.compile(
            r"\b(?:"
            + "moerth" + "j"
            + r"|"
            + "atli" + "west"
            + r"|"
            + "itx000" + "133"
            + r"|"
            + "fileserver" + "02"
            + r")\b",
            re.IGNORECASE,
        ),
    ),
]

SKIP_EXTENSIONS = {
    ".exe", ".dll", ".so", ".dylib", ".bin", ".obj", ".o", ".pyc",
    ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".tar", ".gz"
}

SKIP_DIRS = {
    ".git", ".agents", "__pycache__", ".venv", "venv", "node_modules", ".idea", ".vscode"
}


def check_text(text: str, filename: str = "<memory>") -> List[Dict[str, Any]]:
    """Checks a string against all deterministic guardrail invariants."""
    violations = []
    lines = text.splitlines()
    seen_changelog_versions = set()

    for idx, line in enumerate(lines, 1):
        # 1. Em-dash and En-dash invariant
        if EM_DASH in line or EN_DASH in line:
            dash_type = "Em-dash (U+2014)" if EM_DASH in line else "En-dash (U+2013)"
            violations.append({
                "file": filename,
                "line": idx,
                "rule": "no-dash-invariants",
                "severity": "CRITICAL",
                "message": f"Found forbidden {dash_type}. Use standard ASCII hyphen '-' or ':' instead.",
                "snippet": line[:100].strip(),
            })

        # 2. Emoji invariant
        emoji_match = EMOJI_PATTERN.search(line)
        if emoji_match:
            violations.append({
                "file": filename,
                "line": idx,
                "rule": "no-emojis",
                "severity": "CRITICAL",
                "message": f"Found forbidden emoji/symbol '{emoji_match.group()}'. Output must be clean text without emojis.",
                "snippet": line[:100].strip(),
            })

        # 3. Deppenbindestrich invariant (skip negative rule documentation examples like "never 'Abteilungs-Kontingent'")
        for deppen_match in DEPPENBINDESTRICH_PATTERN.finditer(line):
            compound = deppen_match.group()
            prefix = line[max(0, deppen_match.start() - 12):deppen_match.start()].lower()
            if "never '" not in prefix and 'never "' not in prefix:
                corrected = compound.replace("-", "")
                violations.append({
                    "file": filename,
                    "line": idx,
                    "rule": "no-deppenbindestrich",
                    "severity": "HIGH",
                    "message": f"Incorrect German compound hyphenation '{compound}'. Write joined as '{corrected}'.",
                    "snippet": line[:100].strip(),
                })
                break

        # 4. Secret leak checks
        for secret_name, pat in SECRET_PATTERNS:
            if pat.search(line):
                violations.append({
                    "file": filename,
                    "line": idx,
                    "rule": "secret-leak-detection",
                    "severity": "CRITICAL",
                    "message": f"Potential credential leak detected: {secret_name}.",
                    "snippet": pat.sub("[REDACTED]", line[:100]).strip(),
                })

        # 5. Keep a Changelog 1.1.0 invariant for CHANGELOG.md
        if Path(filename).name.lower() == "changelog.md":
            stripped = line.strip()
            if stripped.startswith("### "):
                if not re.match(r"^### (Added|Changed|Deprecated|Removed|Fixed|Security)$", stripped):
                    violations.append({
                        "file": filename,
                        "line": idx,
                        "rule": "keep-a-changelog",
                        "severity": "CRITICAL",
                        "message": (
                            f"Invalid Keep a Changelog section '{stripped}'. "
                            "Only '### Added', '### Changed', '### Deprecated', '### Removed', '### Fixed', or '### Security' are allowed (https://keepachangelog.com/en/1.1.0/)."
                        ),
                        "snippet": stripped[:100],
                    })
            elif stripped.startswith("## "):
                m_ver = re.match(r"^## \[(Unreleased|\d+\.\d+\.\d+(?:-[0-9A-Za-z.\-]+)?(?:\+[0-9A-Za-z.\-]+)?)\](?: - \d{4}-\d{2}-\d{2})?$", stripped)
                if not m_ver:
                    violations.append({
                        "file": filename,
                        "line": idx,
                        "rule": "keep-a-changelog",
                        "severity": "CRITICAL",
                        "message": (
                            f"Invalid Keep a Changelog version header '{stripped}'. "
                            "Use '## [X.Y.Z] - YYYY-MM-DD' or '## [Unreleased]' (https://keepachangelog.com/en/1.1.0/)."
                        ),
                        "snippet": stripped[:100],
                    })
                else:
                    ver_key = m_ver.group(1)
                    if ver_key in seen_changelog_versions:
                        violations.append({
                            "file": filename,
                            "line": idx,
                            "rule": "keep-a-changelog",
                            "severity": "CRITICAL",
                            "message": (
                                f"Duplicate Keep a Changelog version header '[{ver_key}]'. "
                                "Each release version must appear strictly once in CHANGELOG.md."
                            ),
                            "snippet": stripped[:100],
                        })
                    seen_changelog_versions.add(ver_key)

    return violations


def _is_test_file_path(file_path: Path) -> bool:
    """Returns True if file_path is a unit test file or inside a test directory."""
    try:
        name_low = file_path.name.lower()
        if name_low.startswith("test_") or name_low.endswith(("_test.py", "_test.dart", ".test.ts", ".test.js", ".spec.ts", ".spec.js")):
            return True
        tail_parts = [p.lower() for p in file_path.parts[-3:]]
        return "tests" in tail_parts
    except Exception:
        return False


def _is_scratch_file_path(file_path: Path) -> bool:
    """Returns True if file_path is a temporary scratch script inside a scratch/ directory."""
    try:
        parts_low = [p.lower() for p in file_path.parts]
        if "scratch" in parts_low[-3:]:
            return True
        norm_str = str(file_path).lower().replace("/", "\\")
        if "\\antigravity\\brain\\" in norm_str and "\\scratch\\" in norm_str:
            return True
        return False
    except Exception:
        return False


def fix_text(text: str) -> Tuple[str, int]:
    """
    Auto-remediates guardrail violations automatically (zero friction):
    - Converts em-dashes and en-dashes to standard ASCII hyphens.
    - Strips emojis and decorative symbols.
    - Resolves German compound Deppenbindestriche (preserving quoted negative rule examples).
    Returns (cleaned_text, total_fixes_applied).
    """
    fixes = 0

    # 1. Replace dashes
    if EM_DASH in text or EN_DASH in text:
        orig = text
        text = text.replace(EM_DASH, "-").replace(EN_DASH, "-")
        fixes += (orig.count(EM_DASH) + orig.count(EN_DASH))

    # 2. Strip emojis
    emojis = EMOJI_PATTERN.findall(text)
    if emojis:
        fixes += len(emojis)
        text = EMOJI_PATTERN.sub("", text)

    # 3. Fix Deppenbindestriche line-by-line (skipping negative rule examples like "never 'Abteilungs-Kontingent'")
    out_lines = []
    for line in text.splitlines(keepends=True):
        def _repl_deppen(m, current_line=line):
            nonlocal fixes
            compound = m.group()
            prefix = current_line[max(0, m.start() - 12):m.start()].lower()
            if "never '" in prefix or 'never "' in prefix:
                return compound
            fixes += 1
            parts = compound.split("-", 1)
            if len(parts) == 2:
                return parts[0] + parts[1].lower()
            return compound.replace("-", "")

        out_lines.append(DEPPENBINDESTRICH_PATTERN.sub(_repl_deppen, line))
    text = "".join(out_lines)

    return text, fixes


def fix_file(file_path: Path) -> int:
    """Auto-fixes formatting and invariant violations in a file in-place."""
    if file_path.suffix.lower() in SKIP_EXTENSIONS or _is_test_file_path(file_path):
        return 0
    try:
        content = file_path.read_text(encoding="utf-8")
        cleaned, fixes = fix_text(content)
        if fixes > 0:
            file_path.write_text(cleaned, encoding="utf-8")
        return fixes
    except Exception:
        return 0


def check_file(file_path: Path) -> List[Dict[str, Any]]:
    """Inspects a single file for guardrail violations."""
    if file_path.suffix.lower() in SKIP_EXTENSIONS:
        return []
    if _is_test_file_path(file_path):
        return []
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        violations = check_text(content, filename=file_path.name)
        if file_path.name.lower() == "changelog.md" and "keepachangelog.com" not in content.lower():
            violations.append({
                "file": file_path.name,
                "line": 1,
                "rule": "keep-a-changelog",
                "severity": "CRITICAL",
                "message": "CHANGELOG.md must reference https://keepachangelog.com/en/1.1.0/ and https://semver.org/ in its header.",
                "snippet": content.splitlines()[0][:100] if content.splitlines() else "",
            })
        return violations
    except Exception as e:
        return [{
            "file": file_path.name,
            "line": 0,
            "rule": "file-read-error",
            "severity": "LOW",
            "message": f"Could not read file: {e}",
            "snippet": "",
        }]


def check_workspace(root_path: Optional[Path] = None, staged_only: bool = False, strict: bool = False, auto_fix: bool = False) -> Dict[str, Any]:
    """
    Inspects all workspace files (or git staged files) against guardrails.
    In advisory mode (strict=False), provides helpful hints without blocking execution.
    If auto_fix=True, resolves formatting violations automatically.
    """
    target_root = (root_path or Path.cwd()).resolve()
    all_violations = []
    files_checked = 0
    total_fixes = 0

    if staged_only:
        try:
            res = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                cwd=target_root,
                capture_output=True,
                text=True,
                check=False
            )
            staged_files = [f.strip() for f in res.stdout.splitlines() if f.strip()]
            for rel_file in staged_files:
                fpath = target_root / rel_file
                if fpath.exists() and fpath.is_file():
                    files_checked += 1
                    if auto_fix:
                        total_fixes += fix_file(fpath)
                    all_violations.extend(check_file(fpath))
        except Exception as e:
            all_violations.append({
                "file": "git",
                "line": 0,
                "rule": "git-staged-error",
                "severity": "HIGH",
                "message": f"Failed to check git staged files: {e}",
                "snippet": "",
            })
    else:
        for dirpath, dirnames, filenames in os.walk(target_root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fname in filenames:
                fpath = Path(dirpath) / fname
                if fpath.suffix.lower() not in SKIP_EXTENSIONS:
                    files_checked += 1
                    if auto_fix:
                        total_fixes += fix_file(fpath)
                    all_violations.extend(check_file(fpath))

    passed = len(all_violations) == 0
    return {
        "passed": passed,
        "target_root": str(target_root),
        "staged_only": staged_only,
        "strict": strict,
        "auto_fix": auto_fix,
        "total_fixes": total_fixes,
        "total_files_checked": files_checked,
        "violations_count": len(all_violations),
        "violations": all_violations,
    }


def verify_task(
    task_id: int,
    test_cmd: Optional[str] = None,
    project_root: Optional[Path] = None,
    strict: bool = False,
    auto_fix: bool = True
) -> Dict[str, Any]:
    """
    Evaluator-Optimizer gate: Validates a task against deterministic guardrails and test commands.
    Designed with zero developer friction:
    - In standard mode (strict=False), guardrails automatically auto-fix formatting or warn without blocking.
    - Transitions task to 'completed' with verified telemetry audit.
    """
    from ..memory.project_memory import get_project_connection, get_project_db_path, find_project_root

    root = project_root or find_project_root()
    db_path = get_project_db_path(root)
    if not db_path.exists():
        return {
            "verified": False,
            "task_id": task_id,
            "reason": f"Project memory not initialized at {db_path}",
        }

    conn = get_project_connection(root)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, description, status, priority FROM project_tasks WHERE id = ?;", (task_id,))
        task = cursor.fetchone()
    finally:
        conn.close()
    if not task:
        return {
            "verified": False,
            "task_id": task_id,
            "reason": f"Task #{task_id} not found on project task board.",
        }

    now_iso = datetime.now(timezone.utc).isoformat()
    verification_details = []

    # 1. Guardrail Check & Optional Auto-Fix (non-blocking unless strict=True)
    guard_res = check_workspace(root_path=root, staged_only=False, auto_fix=auto_fix)
    if guard_res["total_fixes"] > 0:
        verification_details.append(f"Auto-fixed {guard_res['total_fixes']} formatting issues")

    if not guard_res["passed"]:
        if strict:
            return {
                "verified": False,
                "task_id": task_id,
                "reason": f"Strict guardrail check blocked completion with {guard_res['violations_count']} policy violations.",
                "violations": guard_res["violations"][:5],
            }
        else:
            verification_details.append(f"Guardrails advisory: {guard_res['violations_count']} warnings noted (non-blocking)")
    else:
        verification_details.append(f"Guardrails clean ({guard_res['total_files_checked']} files verified)")

    # 2. Test Command Execution Check
    if test_cmd:
        try:
            test_proc = subprocess.run(
                test_cmd,
                shell=True,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=120
            )
            if test_proc.returncode != 0:
                err_snippet = (test_proc.stderr or test_proc.stdout)[:300].strip()
                return {
                    "verified": False,
                    "task_id": task_id,
                    "reason": f"Test command failed with exit code {test_proc.returncode}.",
                    "snippet": err_snippet,
                }
            verification_details.append(f"Test command passed: '{test_cmd}' (exit 0)")
        except Exception as e:
            return {
                "verified": False,
                "task_id": task_id,
                "reason": f"Test command execution error: {e}",
            }

    # 3. Successful Verification Gate Passed: Update Task State
    details_str = "; ".join(verification_details) if verification_details else "Verified successfully"
    conn = get_project_connection(root)
    try:
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE project_tasks
        SET status = 'completed', updated_at = ?
        WHERE id = ?;
        """, (now_iso, task_id))
        cursor.execute(
            "DELETE FROM project_memory_fts WHERE source_table = 'task' AND title_or_key LIKE ?;",
            (f"Task #{task_id}:%",),
        )
        cursor.execute(
            "INSERT INTO project_memory_fts (source_table, title_or_key, content) VALUES ('task', ?, ?);",
            (f"Task #{task_id}: {task['title']}", f"completed {task['priority']} {task['description'] or ''}"),
        )
        cursor.execute("""
        INSERT INTO task_verifications (task_id, verifier_type, passed, details, created_at)
        VALUES (?, 'evaluator_optimizer', 1, ?, ?);
        """, (task_id, details_str, now_iso))
        conn.commit()
    finally:
        conn.close()

    return {
        "verified": True,
        "task_id": task_id,
        "task_title": task["title"],
        "status": "completed",
        "details": details_str,
    }


_HOST_ADMIN_EXEMPT_KEYWORDS = (
    "invoke-command",
    "icm ",
    "get-ciminstance",
    "gcim ",
    "set-ciminstance",
    "remove-ciminstance",
    "invoke-cimmethod",
    "new-cimsession",
    "remove-cimsession",
    "get-wmiobject",
    "gwmi ",
    "invoke-wmimethod",
    "remove-wmiobject",
    "set-wmiinstance",
    "enter-pssession",
    "new-pssession",
    "remove-pssession",
    "-computername",
    "-cimsession",
    "import-clixml",
    "pscredential",
    "convertto-securestring",
    "new-psdrive",
    ".credential.xml",
    "\\\\",
    "\\c$",
    "\\admin$",
    "\\ipc$",
    "get-service",
    "set-service",
    "restart-service",
    "stop-service",
    "start-service",
    "get-process",
    "stop-process",
    "get-winevent",
    "get-eventlog",
    "get-itemproperty",
    "set-itemproperty",
    "new-itemproperty",
    "remove-itemproperty",
    "test-connection",
    "test-netconnection",
    "resolve-dnsname",
    "invoke-sqlcmd",
    "sqlconnection",
    "sqlcommand",
    "sqldataadapter",
    "reg.exe",
    "gpupdate",
    "wevtutil",
    "dism",
    "sfc ",
    "msiexec",
    "wusa",
    "pnputil",
    "root\\ccm",
    "root/ccm",
    "sms_",
    "cmpivot",
    "transcript.jsonl",
    "transcript_full.jsonl",
)


def _is_exploratory_search_cmd(cmd: str) -> bool:
    """
    Returns True if cmd is an ad-hoc local codebase shell/python search one-liner rather than
    a build, test, git, skillsdb, or targeted single-host / remote WMI / CIM / system admin command.
    """
    if not cmd or not isinstance(cmd, str):
        return False
    low = cmd.lower()
    for allow_kw in (
        "skillsdb",
        "db_manager.py",
        "unittest",
        "pytest",
        "git ",
        "build.py",
        "deploy.py",
        "install_antigraviton",
        "gitea",
        "gradle",
        "flutter ",
        "npm ",
        "cargo ",
    ):
        if allow_kw in low:
            return False
    for admin_kw in _HOST_ADMIN_EXEMPT_KEYWORDS:
        if admin_kw in low:
            return False
    if any(k in low for k in ("select-string", "get-childitem", "grep ", "rg ", "findstr ")):
        return True
    if "python" in low and "-c" in low:
        if any(k in low for k in (".read_text", ".glob(", "re.search", "re.findall", ".splitlines()", ".find(", "os.walk")):
            return True
    return False


def _is_subagent_context(payload: Dict[str, Any], t_path: Optional[Path] = None) -> bool:
    """
    Returns True if the current PreToolUse invocation originates from an isolated subagent
    (e.g., TypeName='research' or 'self') rather than the primary user-facing conversation.
    Checks both explicit hook payload metadata and the O(1) transcript header signature
    (subagent transcripts begin with a parent SYSTEM_MESSAGE containing 'sender=' and have
    no USER_INPUT / USER_EXPLICIT header steps).
    """
    if not isinstance(payload, dict):
        return False
    if payload.get("isSubagent") or payload.get("parentConversationId"):
        return True
    sub_type = str(
        payload.get("subagentType")
        or payload.get("typeName")
        or payload.get("agentType")
        or payload.get("TypeName")
        or ""
    ).strip().lower()
    if sub_type in ("research", "self", "image-generator", "subagent"):
        return True
    role_val = str(payload.get("role") or payload.get("agentRole") or "").strip()
    if role_val:
        return True

    if t_path and t_path.exists():
        try:
            head_lines = []
            with open(t_path, "r", encoding="utf-8", errors="ignore") as f:
                for _ in range(16):
                    line = f.readline()
                    if not line:
                        break
                    head_lines.append(line)
            has_user_input = any(
                '"USER_INPUT"' in ln or '"USER_EXPLICIT"' in ln
                for ln in head_lines
            )
            has_parent_sys_msg = any(
                '"SYSTEM_MESSAGE"' in ln and ("sender=" in ln or "<SYSTEM_MESSAGE>" in ln)
                for ln in head_lines
            )
            if has_parent_sys_msg and not has_user_input:
                return True
        except Exception:
            pass
    return False


def _resolve_hook_cid_and_transcript(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[Path]]:
    """Resolves (cid, transcript_path) from hook payload or ANTIGRAVITY_CONVERSATION_ID environment fallback."""
    t_path_str = str(payload.get("transcriptPath") or "").strip()
    cid = str(
        payload.get("conversationId")
        or payload.get("conversation_id")
        or os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
        or ""
    ).strip() or None
    t_path: Optional[Path] = None
    if t_path_str:
        t_path = Path(t_path_str)
    elif cid:
        brain_override = Path(payload["brainDir"]) if payload.get("brainDir") else (resolve_user_home() / ".gemini" / "antigravity" / "brain")
        t_path = brain_override / cid / ".system_generated" / "logs" / "transcript.jsonl"
    return cid, t_path


def _was_file_region_read_in_current_epoch(
    payload: Dict[str, Any],
    fpath: Path,
    s_line: int,
    e_line: int,
    total_lines: int,
) -> bool:
    """
    Returns True if fpath (or the 650-line region around s_line..e_line for files > 800 lines)
    was already read in the current uncompacted conversation window (since the last <CONTEXT_SUMMARY>).
    Uses O(1) binary tail-seek (read_jsonl_tail_lines) so multi-megabyte transcripts never add hook latency.
    """
    try:
        _, t_path = _resolve_hook_cid_and_transcript(payload)
        if not t_path or not t_path.exists():
            return False

        tail_lines = read_jsonl_tail_lines(t_path, max_bytes=393216, max_lines=120)
        fname_low = fpath.name.lower()
        try:
            target_norm = os.path.normcase(os.path.abspath(str(fpath)))
        except Exception:
            target_norm = ""
        req_mid = (s_line + e_line) // 2

        for rline in reversed(tail_lines):
            if "<CONTEXT_SUMMARY>" in rline:
                break
            if ('"read_file"' in rline or '"view_file"' in rline) and fname_low in rline.lower():
                try:
                    robj = json.loads(rline)
                    for tc in (robj.get("tool_calls") or []):
                        fn = (tc.get("name") or (tc.get("function") or {}).get("name") or "").lower()
                        if fn not in ("read_file", "view_file"):
                            continue
                        targs = tc.get("args") or tc.get("arguments") or (tc.get("function") or {}).get("arguments") or {}
                        if isinstance(targs, str):
                            targs = json.loads(targs)
                        if not isinstance(targs, dict):
                            continue
                        prev_p = str(targs.get("Path") or targs.get("AbsolutePath") or targs.get("TargetFile") or "")
                        prev_clean = prev_p.replace("\\\\", "\\")
                        prev_path_obj = Path(prev_clean)
                        if prev_path_obj.name.lower() != fname_low:
                            continue
                        if target_norm and len(prev_path_obj.parts) > 1:
                            try:
                                if os.path.normcase(os.path.abspath(prev_clean)) != target_norm:
                                    continue
                            except Exception:
                                pass
                        prev_s = int(targs.get("StartLine") or 1)
                        raw_e = targs.get("EndLine")
                        prev_e = int(raw_e) if raw_e is not None else min(total_lines, prev_s + 799)
                        if total_lines <= 800:
                            if prev_s <= s_line and prev_e >= e_line:
                                return True
                            continue
                        prev_mid = (prev_s + prev_e) // 2
                        if abs(req_mid - prev_mid) <= 250 or (prev_s <= s_line and prev_e >= e_line):
                            return True
                except Exception:
                    pass
    except Exception:
        pass
    return False


def _was_file_inspected_before_edit(payload: Any, fpath: Path) -> bool:
    """
    Returns True if fpath (> 250 lines) was already read ('read_file' / 'view_file'),
    AST-mapped ('ast-map' / 'skillsdb_ast_map'), FIM-sliced ('fim' / 'skillsdb_fim_slice'),
    digested ('gemini-digest' / 'skillsdb_gemini_digest'), or previously edited/written
    in the current uncompacted conversation epoch (since the last <CONTEXT_SUMMARY>).
    If no transcript exists in payload, returns True so standalone unit tests are unaffected.
    """
    try:
        if isinstance(payload, (str, Path)):
            payload = {"transcriptPath": str(payload)}
        _, t_path = _resolve_hook_cid_and_transcript(payload)
        if not t_path or not t_path.exists():
            return True
        if _is_subagent_context(payload, t_path):
            return True

        tail_lines = read_jsonl_tail_lines(t_path, max_bytes=524288, max_lines=150)
        if not tail_lines:
            return True

        fname_low = fpath.name.lower()
        bs = chr(92)
        fpath_norm = os.path.normcase(os.path.abspath(str(fpath))).replace("/", bs)
        parent_low = fpath.parent.name.lower() if fpath.parent else ""
        path_arg_re = re.compile(r'"(?:Path|TargetFile|AbsolutePath|path|file_path|file|target_file)"\s*:\s*"([^"]+)"')
        cmd_arg_re = re.compile(r'"(?:CommandLine|command)"\s*:\s*"([^"]{0,4000})"')

        def _path_matches_target(candidate_str: str) -> bool:
            c_clean = candidate_str.replace(bs + bs, bs).replace("/", bs).strip()
            if Path(c_clean).name.lower() != fname_low:
                return False
            if bs not in c_clean:
                return True
            c_norm = os.path.normcase(os.path.abspath(c_clean)).replace("/", bs)
            return c_norm == fpath_norm or fpath_norm.endswith(bs + c_clean.lstrip(bs).lower())

        has_valid_epoch_step = False
        for rline in reversed(tail_lines):
            if "<CONTEXT_SUMMARY>" in rline:
                break
            rlow = rline.lower()
            if '"user_input"' in rlow or '"user_explicit"' in rlow or '"planner_response"' in rlow:
                has_valid_epoch_step = True
            # Never treat plain user prompt text as a tool inspection of the file
            if ('"user_input"' in rlow or '"user_explicit"' in rlow) and '"planner_response"' not in rlow:
                continue
            if "ast-map" in rlow or "skillsdb_ast_map" in rlow:
                cmd_segments = [m.lower() for m in cmd_arg_re.findall(rline)]
                ast_segments = [s for s in cmd_segments if "ast-map" in s]
                if not ast_segments and ("ast-map" in rlow or "skillsdb_ast_map" in rlow):
                    ast_segments = [rlow]
                for aseg in ast_segments:
                    if fname_low in aseg:
                        # Check if a directory-qualified path was given in ast-map
                        dir_tokens = re.findall(r"[\w.\-/\\]+" + re.escape(fname_low), aseg)
                        if dir_tokens and any(("/" in dt or bs in dt) for dt in dir_tokens):
                            if any(_path_matches_target(dt) for dt in dir_tokens):
                                return True
                        else:
                            return True
                    elif not re.search(r"\b[\w\-]+\.(?:py|ts|tsx|js|jsx|kt|kts|dart|cs|go|rs|java|cpp|c|h|ps1|md)\b", aseg):
                        return True
                    elif parent_low and parent_low in aseg and ("*" in aseg or f"{parent_low}/" in aseg or f"{parent_low}\\" in aseg):
                        return True
            if fname_low in rlow and any(
                kw in rlow
                for kw in (
                    "read_file",
                    "view_file",
                    "fim slice",
                    "fim-slice",
                    "skillsdb_fim_slice",
                    "gemini-digest",
                    "gemini_digest",
                    "edit_file",
                    "write_file",
                    "write_to_file",
                    "replace_file_content",
                    "multi_replace_file_content",
                )
            ):
                # Disambiguate files with identical basenames in different directories (e.g. README.md vs plugin/README.md)
                matched_paths = path_arg_re.findall(rline)
                if matched_paths:
                    if any(_path_matches_target(mp) for mp in matched_paths):
                        return True
                else:
                    return True
        if not has_valid_epoch_step:
            return True
        return False
    except Exception:
        return True


def handle_pre_tool_guardrail_hook():
    """
    Handles Antigravity PreToolUse lifecycle hook.
    Validates and optimizes tool arguments for write_file, edit_file, write_to_file,
    replace_file_content, multi_replace_file_content, read_file, view_file, and run_command:
    1. Zero em-dashes (Unicode U+2014) and zero en-dashes (Unicode U+2013).
    2. Zero emojis or decorative Unicode symbols.
    3. Secret/credential leak prevention.
    4. SemVer 2.0.0 release tag immutability (blocks git tag -f vX.Y.Z).
    5. Anti-salami-slicing read_file window expansion via PreToolUse 'overwrite' (expands < 350 line slices to full file if <= 800 lines, or a 650-line window if > 800 lines).
    6. Zero-polling guard & automatic WaitMsBeforeAsync=10000 upgrade via PreToolUse 'overwrite' + Subagent Firewall enforcement on 5+ consecutive exploratory search commands.
    """
    try:
        payload_raw = sys.stdin.read()
        payload = json.loads(payload_raw) if payload_raw.strip() else {}
        if not isinstance(payload, dict):
            print(json.dumps({"decision": "allow"}))
            return

        tool_call = payload.get("toolCall") or payload.get("tool_call")
        if not isinstance(tool_call, dict):
            tool_call = {}
        tool_name = str(
            tool_call.get("name")
            or tool_call.get("toolName")
            or payload.get("tool_name")
            or payload.get("toolName")
            or payload.get("name")
            or ""
        ).strip().lower()
        raw_args = (
            tool_call.get("args")
            or tool_call.get("arguments")
            or tool_call.get("toolInput")
            or payload.get("tool_input")
            or payload.get("toolInput")
            or payload.get("args")
            or payload.get("arguments")
            or {}
        )
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except Exception:
                raw_args = {}
        args = raw_args if isinstance(raw_args, dict) else {}
        target_file = str(
            args.get("Path")
            or args.get("TargetFile")
            or args.get("AbsolutePath")
            or args.get("path")
            or args.get("file_path")
            or args.get("target_file")
            or "<tool_input>"
        )

        # A.0 Automatic 0-Turn Subagent Quota & Cross-Pool Routing via PreToolUse overwrite
        if tool_name == "invoke_subagent" or (not tool_name and "TypeName" in args and "Prompt" in args):
            try:
                req_model = str(args.get("Model") or "inherit").strip().lower()
                type_name = str(args.get("TypeName") or "").strip().lower()
                role_str = str(args.get("Role") or "")
                prompt_str = str(args.get("Prompt") or "")
                combined_text = f"{role_str}\n{prompt_str}"
                force_inherit = any(tag in combined_text.upper() for tag in ("[FORCE-INHERIT]", "[FORCE-ULTRA]"))
                is_coding_self = (type_name == "self")
                is_exploratory = (not is_coding_self) and (
                    type_name == "research"
                    or any(k in role_str.lower() for k in ("research", "auditor", "scanner", "finder", "search", "explorer", "log"))
                )
                if not force_inherit and (req_model in ("", "inherit") or (is_coding_self and req_model in ("flash", "flash_lite"))):
                    cid, _ = _resolve_hook_cid_and_transcript(payload)
                    model_hint = payload.get("modelName") or payload.get("model")
                    db_override = Path(payload["dbPath"]) if payload.get("dbPath") else DB_PATH
                    brain_override = Path(payload["brainDir"]) if payload.get("brainDir") else None
                    decision = compute_routing_decision(
                        cid=cid,
                        db_path=db_override,
                        model_name_hint=model_hint,
                        brain_dir=brain_override,
                    )
                    target_sub_model = (
                        decision["subagent_research_model"]
                        if is_exploratory
                        else decision["subagent_general_model"]
                    )
                    if is_coding_self and req_model in ("flash", "flash_lite"):
                        target_sub_model = target_sub_model or "inherit"
                    if target_sub_model and target_sub_model != req_model:
                        record_quota_ledger_entry(
                            pool="agy_subagent_rewrite",
                            execution_tier="lite" if "flash" in target_sub_model else "pro",
                            model_used=target_sub_model,
                            effort_level=decision["state"]["effort_level"],
                            input_tokens=0,
                            output_tokens=0,
                            saved_agy_tokens=4500,
                            saved_agy_turns=1,
                            latency_ms=1,
                            status="ok",
                            task_summary=f"PreToolUse overwrite invoke_subagent ({type_name or 'agent'}): {req_model or 'inherit'} -> {target_sub_model}",
                            db_path=db_override,
                        )
                        print(json.dumps({
                            "decision": "allow",
                            "overwrite": {"Model": target_sub_model},
                        }))
                        return
            except Exception:
                pass
            print(json.dumps({"decision": "allow"}))
            return

        # A.0.b Zero-Polling Guard for manage_task(Action='status') loops (Rule 5)
        if tool_name == "manage_task" or (not tool_name and "Action" in args and "TaskId" in args):
            act_val = str(args.get("Action") or "").strip().lower()
            if act_val == "status":
                try:
                    _, t_path = _resolve_hook_cid_and_transcript(payload)
                    if t_path and t_path.exists():
                        tail_lines = read_jsonl_tail_lines(t_path, max_bytes=65536, max_lines=15)
                        status_polls = 0
                        for rline in reversed(tail_lines):
                            if (
                                '"USER_INPUT"' in rline
                                or '"USER_EXPLICIT"' in rline
                                or '"SYSTEM_MESSAGE"' in rline
                                or "<CONTEXT_SUMMARY>" in rline
                            ):
                                break
                            if '"manage_task"' in rline and '"status"' in rline.lower():
                                status_polls += 1
                        if status_polls >= 1:
                            reason = (
                                "SkillsDB Zero-Polling Guard (Rule 5): "
                                "Never poll 'manage_task(Action=\"status\")' in a loop! "
                                "End your turn (call no more tools) and rely on automatic Reactive Wakeup to notify you when the background task finishes."
                            )
                            print(json.dumps({"decision": "deny", "reason": reason}))
                            return
                except Exception:
                    pass
            print(json.dumps({"decision": "allow"}))
            return

        # A. Automatic Anti-Salami-Slicing window expansion for read_file / view_file via PreToolUse overwrite
        if tool_name in ("read_file", "view_file") or (not tool_name and ("StartLine" in args or "EndLine" in args) and "Content" not in args and "Replacement" not in args):
            if args.get("ByteOffset") is None and (args.get("StartLine") is not None or args.get("EndLine") is not None) and target_file != "<tool_input>":
                try:
                    s_line = int(args.get("StartLine") or 1)
                    e_line = int(args.get("EndLine") or s_line)
                    req_span = e_line - s_line + 1
                    if 0 < req_span < 350:
                        fpath = Path(target_file)
                        if fpath.exists() and fpath.is_file() and fpath.suffix.lower() not in SKIP_EXTENSIONS and fpath.stat().st_size <= 2_500_000:
                            total_lines = len(fpath.read_text(encoding="utf-8", errors="ignore").splitlines())
                            if not _was_file_region_read_in_current_epoch(payload, fpath, s_line, e_line, total_lines):
                                if 0 < total_lines <= 800 and req_span < total_lines:
                                    print(json.dumps({
                                        "decision": "allow",
                                        "overwrite": {"StartLine": 1, "EndLine": total_lines},
                                    }))
                                    return
                                elif total_lines > 800:
                                    mid = (s_line + e_line) // 2
                                    new_start = max(1, min(s_line, mid - 324))
                                    new_end = min(total_lines, max(e_line, new_start + 649))
                                    new_start = max(1, min(new_start, new_end - 649))
                                    if (new_end - new_start) > (e_line - s_line):
                                        print(json.dumps({
                                            "decision": "allow",
                                            "overwrite": {"StartLine": new_start, "EndLine": new_end},
                                        }))
                                        return
                except Exception:
                    pass
            print(json.dumps({"decision": "allow"}))
            return

        cmd_line = args.get("CommandLine") or args.get("command") or ""
        if isinstance(cmd_line, str) and cmd_line:
            if re.search(
                r"\bgit\b[^\n;|&]*\btag\b(?=[^\n;|&]*(?:\s-f\b|\s--force\b|\s-[a-zA-Z]*f[a-zA-Z]*\b))[^\n;|&]*\bv?\d+\.\d+\.\d+\b",
                cmd_line,
            ):
                reason = (
                    "SkillsDB Guardrail Blocked Command (semver-immutability): "
                    "Never overwrite an already published release tag in place (git tag -f is forbidden by SemVer 2.0.0 rule). "
                    "Increment VERSION and CHANGELOG.md to a new MAJOR.MINOR.PATCH version and publish a new release instead."
                )
                print(json.dumps({"decision": "deny", "reason": reason}))
                return

            # Block shell polling of .system_generated/tasks/*.log via Get-Content / cat / type
            cmd_low = cmd_line.lower().replace("/", "\\")
            if ".system_generated\\tasks\\task-" in cmd_low and any(k in cmd_low for k in ("get-content", "cat ", "type ", "get-item")):
                reason = (
                    "SkillsDB Zero-Polling Guard (Rule 5): "
                    "Never use run_command (Get-Content/cat) to read or poll '.system_generated/tasks/*.log'! "
                    "Wait for automatic Reactive Wakeup when the background task finishes, or use read_file if the task has already completed."
                )
                print(json.dumps({"decision": "deny", "reason": reason}))
                return

            # Hard SOC/EDR Guardrail: Block Scheduled Tasks, remote service creation, PsExec, or C:\Windows\Temp script execution on remote servers/clients
            is_remote_ctx = any(
                rk in cmd_low
                for rk in ("invoke-command", "icm ", "-computername", "-cimsession", "enter-pssession", "new-pssession", "\\\\")
            )
            has_forbidden_lateral_pattern = any(
                fk in cmd_low
                for fk in (
                    "register-scheduledtask",
                    "start-scheduledtask",
                    "new-scheduledtaskaction",
                    "schtasks",
                    "psexec",
                    "sc.exe \\\\",
                    "windows\\temp\\",
                )
            )
            if (is_remote_ctx and has_forbidden_lateral_pattern) or "sc.exe \\\\" in cmd_low or "psexec" in cmd_low:
                reason = (
                    "SkillsDB Security Guardrail (no-server-scheduled-tasks-or-temp-scripts): "
                    "Creating Scheduled Tasks (Register-ScheduledTask / schtasks), remote services (sc.exe \\\\), PsExec, or staging scripts in C:\\Windows\\Temp "
                    "on MECM or remote hosts is strictly forbidden (triggers SOC/EDR lateral movement alerts). "
                    "Use exclusively official MECM native channels (SQL read queries on CM_CM2, WMI SMS_Client, CMPivot, or MECM Run Scripts) or ask the user."
                )
                print(json.dumps({"decision": "deny", "reason": reason}))
                return

            # Subagent Firewall enforcement for 5+ consecutive exploratory search commands in the main context
            # (Isolated subagents such as TypeName='research' are exempt so their searches never block or affect main-context streaks)
            if _is_exploratory_search_cmd(cmd_line):
                try:
                    _, t_path = _resolve_hook_cid_and_transcript(payload)
                    if not _is_subagent_context(payload, t_path) and t_path and t_path.exists():
                        tail_lines = read_jsonl_tail_lines(t_path, max_bytes=131072, max_lines=30)
                        search_streak = 0
                        for rline in reversed(tail_lines):
                            if (
                                '"USER_INPUT"' in rline
                                or '"USER_EXPLICIT"' in rline
                                or '"invoke_subagent"' in rline
                                or ('"SYSTEM_MESSAGE"' in rline and "sender=" in rline)
                                or "<CONTEXT_SUMMARY>" in rline
                            ):
                                break
                            if '"PLANNER_RESPONSE"' in rline and '"run_command"' in rline:
                                try:
                                    robj = json.loads(rline)
                                    turn_had_run_cmd = False
                                    turn_had_exploratory = False
                                    for tc in (robj.get("tool_calls") or []):
                                        fn = tc.get("name") or (tc.get("function") or {}).get("name") or ""
                                        if fn == "run_command":
                                            turn_had_run_cmd = True
                                            targs = tc.get("args") or tc.get("arguments") or (tc.get("function") or {}).get("arguments") or {}
                                            if isinstance(targs, str):
                                                targs = json.loads(targs)
                                            prev_cmd = (targs.get("CommandLine") or "") if isinstance(targs, dict) else ""
                                            if _is_exploratory_search_cmd(prev_cmd):
                                                turn_had_exploratory = True
                                    if turn_had_run_cmd:
                                        if turn_had_exploratory:
                                            search_streak += 1
                                        else:
                                            break
                                except Exception:
                                    pass
                        if search_streak >= 4:
                            reason = (
                                "SkillsDB Subagent Firewall (Rule 5): "
                                "You have already executed 4+ consecutive exploratory search commands in the main context. "
                                "Delegate open multi-file root-cause searching to invoke_subagent(TypeName='research', Model='flash') or offload large log/file analysis via 'skillsdb gemini-digest --files <paths> --query \"...\"' to prevent main-context token burn!"
                            )
                            print(json.dumps({"decision": "deny", "reason": reason}))
                            return
                except Exception:
                    pass

            # Automatic WaitMsBeforeAsync upgrade to 10000ms for normal synchronous commands via PreToolUse overwrite
            if not bool(args.get("IsDaemon")):
                try:
                    wait_val = args.get("WaitMsBeforeAsync")
                    if wait_val is not None:
                        wait_int = int(wait_val)
                        if 0 < wait_int < 10000:
                            out_val = "10000" if isinstance(wait_val, str) else 10000
                            print(json.dumps({
                                "decision": "allow",
                                "overwrite": {"WaitMsBeforeAsync": out_val},
                            }))
                            return
                except Exception:
                    pass

        # B. AST-First & Read-Before-Edit Guard for files > 250 lines (Rule 5 / Rule 7)
        if tool_name in ("edit_file", "replace_file_content", "multi_replace_file_content", "write_file", "write_to_file") and target_file != "<tool_input>":
            try:
                edit_fpath = Path(target_file)
                if (
                    not _is_scratch_file_path(edit_fpath)
                    and edit_fpath.exists()
                    and edit_fpath.is_file()
                    and edit_fpath.suffix.lower() not in SKIP_EXTENSIONS
                    and edit_fpath.stat().st_size <= 2_500_000
                ):
                    edit_total_lines = len(edit_fpath.read_text(encoding="utf-8", errors="ignore").splitlines())
                    if False and edit_total_lines > 250 and not _was_file_inspected_before_edit(payload, edit_fpath):
                        reason = (
                            f"SkillsDB AST-First & Read-Before-Edit Guard (Rule 5 / Rule 7): "
                            f"File '{edit_fpath.name}' has {edit_total_lines} lines (> 250 lines) and has not been read or AST-mapped in the current conversation epoch. "
                            f"Run 'skillsdb ast-map \"{edit_fpath.name}\"' or 'read_file' on the target region before calling {tool_name}."
                        )
                        print(json.dumps({"decision": "deny", "reason": reason}))
                        return
            except Exception:
                pass

        try:
            target_path = Path(target_file)
            if _is_test_file_path(target_path) or _is_scratch_file_path(target_path):
                print(json.dumps({"decision": "allow"}))
                return
        except Exception:
            pass

        content_items = []
        for field_key in ("Content", "Replacement", "CodeContent", "ReplacementContent", "content", "replacement", "new_string"):
            if field_key in args and isinstance(args[field_key], str):
                content_items.append((field_key, args[field_key]))
        if "ReplacementChunks" in args and isinstance(args["ReplacementChunks"], list):
            for chunk in args["ReplacementChunks"]:
                if isinstance(chunk, dict):
                    chunk_str = chunk.get("ReplacementContent") or chunk.get("Replacement")
                    if isinstance(chunk_str, str):
                        content_items.append(("ReplacementChunks", chunk_str))

        for field_name, content in content_items:
            violations = check_text(content, filename=target_file)
            critical_violations = [v for v in violations if v.get("severity") == "CRITICAL"]
            if critical_violations:
                first = critical_violations[0]
                reason = (
                    f"SkillsDB Guardrail Blocked Code Modification ({first['rule']}): "
                    f"{first['message']} In snippet: '{first['snippet']}'"
                )
                print(json.dumps({"decision": "deny", "reason": reason}))
                return
    except Exception:
        pass

    print(json.dumps({"decision": "allow"}))


guardrail_pre_tool_hook = handle_pre_tool_guardrail_hook
