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
                if not re.match(r"^## \[(Unreleased|\d+\.\d+\.\d+(?:-[0-9A-Za-z.\-]+)?(?:\+[0-9A-Za-z.\-]+)?)\](?: - \d{4}-\d{2}-\d{2})?$", stripped):
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


def handle_pre_tool_guardrail_hook():
    """
    Handles Antigravity PreToolUse lifecycle hook.
    Validates tool arguments for write_file, edit_file, write_to_file, replace_file_content,
    multi_replace_file_content, and run_command to enforce:
    1. Zero em-dashes (Unicode U+2014) and zero en-dashes (Unicode U+2013).
    2. Zero emojis or decorative Unicode symbols.
    3. Secret/credential leak prevention.
    4. SemVer 2.0.0 release tag immutability (blocks git tag -f vX.Y.Z).
    Returns: {"decision": "allow"} or {"decision": "deny", "reason": "..."}
    """
    try:
        payload_raw = sys.stdin.read()
        payload = json.loads(payload_raw) if payload_raw.strip() else {}
        if not isinstance(payload, dict):
            print(json.dumps({"decision": "allow"}))
            return

        tool_call = payload.get("toolCall")
        if not isinstance(tool_call, dict):
            tool_call = {}
        raw_args = tool_call.get("args") or tool_call.get("arguments") or payload.get("tool_input") or {}
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

        cmd_line = args.get("CommandLine") or args.get("command") or ""
        if isinstance(cmd_line, str) and re.search(
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

        try:
            target_path = Path(target_file)
            if _is_test_file_path(target_path):
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

