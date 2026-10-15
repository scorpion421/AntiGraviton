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

        # 3. Deppenbindestrich invariant
        deppen_match = DEPPENBINDESTRICH_PATTERN.search(line)
        if deppen_match:
            compound = deppen_match.group()
            corrected = compound.replace("-", "")
            violations.append({
                "file": filename,
                "line": idx,
                "rule": "no-deppenbindestrich",
                "severity": "HIGH",
                "message": f"Incorrect German compound hyphenation '{compound}'. Write joined as '{corrected}'.",
                "snippet": line[:100].strip(),
            })

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

    return violations


def fix_text(text: str) -> Tuple[str, int]:
    """
    Auto-remediates guardrail violations automatically (zero friction):
    - Converts em-dashes and en-dashes to standard ASCII hyphens.
    - Strips emojis and decorative symbols.
    - Resolves German compound Deppenbindestriche.
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

    # 3. Fix Deppenbindestriche
    def _repl_deppen(m):
        nonlocal fixes
        fixes += 1
        parts = m.group().split("-", 1)
        if len(parts) == 2:
            return parts[0] + parts[1].lower()
        return m.group().replace("-", "")

    text = DEPPENBINDESTRICH_PATTERN.sub(_repl_deppen, text)

    return text, fixes


def fix_file(file_path: Path) -> int:
    """Auto-fixes formatting and invariant violations in a file in-place."""
    if file_path.suffix.lower() in SKIP_EXTENSIONS:
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
    if "tests" in file_path.parts or file_path.name.startswith("test_"):
        return []
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        return check_text(content, filename=file_path.name)
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
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, status, priority FROM project_tasks WHERE id = ?;", (task_id,))
    task = cursor.fetchone()
    if not task:
        conn.close()
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
            conn.close()
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
                conn.close()
                err_snippet = (test_proc.stderr or test_proc.stdout)[:300].strip()
                return {
                    "verified": False,
                    "task_id": task_id,
                    "reason": f"Test command failed with exit code {test_proc.returncode}.",
                    "snippet": err_snippet,
                }
            verification_details.append(f"Test command passed: '{test_cmd}' (exit 0)")
        except Exception as e:
            conn.close()
            return {
                "verified": False,
                "task_id": task_id,
                "reason": f"Test command execution error: {e}",
            }

    # 3. Successful Verification Gate Passed: Update Task State
    details_str = "; ".join(verification_details) if verification_details else "Verified successfully"
    cursor.execute("""
    UPDATE project_tasks
    SET status = 'completed', updated_at = ?
    WHERE id = ?;
    """, (now_iso, task_id))

    cursor.execute("""
    INSERT INTO task_verifications (task_id, verifier_type, passed, details, created_at)
    VALUES (?, 'evaluator_optimizer', 1, ?, ?);
    """, (task_id, details_str, now_iso))

    conn.commit()
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
    Validates tool arguments for write_to_file and replace_file_content to enforce:
    1. Zero em-dashes (Unicode U+2014) and zero en-dashes (Unicode U+2013).
    2. Zero emojis or decorative Unicode symbols.
    3. Secret/credential leak prevention.
    Returns: {"decision": "allow"} or {"decision": "deny", "reason": "..."}
    """
    try:
        payload_raw = sys.stdin.read()
        payload = json.loads(payload_raw) if payload_raw.strip() else {}
    except Exception:
        print(json.dumps({"decision": "allow"}))
        return

    tool_call = payload.get("toolCall", {})
    args = tool_call.get("args", {})
    target_file = args.get("TargetFile", "<tool_input>")

    content_items = []
    if "CodeContent" in args and isinstance(args["CodeContent"], str):
        content_items.append(("CodeContent", args["CodeContent"]))
    if "ReplacementContent" in args and isinstance(args["ReplacementContent"], str):
        content_items.append(("ReplacementContent", args["ReplacementContent"]))

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

    print(json.dumps({"decision": "allow"}))

