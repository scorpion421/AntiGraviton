"""
SkillsDB configuration, model tier constants, and dynamic path discovery.
"""

import os
import sys
from pathlib import Path

# Ensure UTF-8 output encoding across Windows PowerShell and CMD
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

__version__ = "4.0.9"
__suite_version__ = "2.0.9"

# Model capability & subscription tiers
TIER_LEAN = "lean"          # Lite / Gemini 3.8 Flash (Fast/Medium) / Flash-Lite, minimal token footprint
TIER_LITE = "lean"          # Explicit alias for Lite tier
TIER_STANDARD = "standard"  # Pro / Claude Sonnet 5.5 / Gemini 3.8 Flash (High) / GPT-OSS 120B (Medium)
TIER_PRO = "standard"       # Explicit alias for Pro tier
TIER_ULTRA = "ultra"        # Ultra / Gemini 4 Argon (Medium/High) / Claude Opus 5.5 (Medium/High)
TIER_LOCAL = "local"        # Local / sovereign model (Ollama, Codestral, vLLM) with zero cloud egress
TIER_OFFLINE = "offline"    # Air-gapped offline operation

TIER_ALIASES = {
    "ultra": TIER_ULTRA,
    "pro": TIER_STANDARD,
    "standard": TIER_STANDARD,
    "lite": TIER_LEAN,
    "lean": TIER_LEAN,
    "local": TIER_LOCAL,
    "offline": TIER_OFFLINE,
}

# Reasoning effort levels (supported on Argon, Flash 3.8, Opus 5.5, Sonnet 5.5; GPT-OSS 120B fixed at medium)
EFFORT_FAST = "fast"
EFFORT_MEDIUM = "medium"
EFFORT_HIGH = "high"

# Quota pool identifiers
POOL_AGY_GEMINI_HIGH = "agy_gemini_high"
POOL_AGY_GEMINI_FAST = "agy_gemini_fast"
POOL_AGY_CLAUDE = "agy_claude"
POOL_AGY_OPEN_WEIGHT = "agy_open_weight"
POOL_EXT_CLI_OAUTH = "ext_cli_oauth"
POOL_EXT_AISTUDIO_API = "ext_aistudio_api"
POOL_EXT_WEB_HANDOVER = "ext_web_handover"

# Pre-indexed skill clusters for instant parallel prefetching
SKILL_CLUSTERS = {
    "flutter": ["flutter-apply-architecture-best-practices", "flutter-add-widget-test", "flutter-setup-declarative-routing", "flutter-fix-layout-issues", "flutter-build-responsive-layout"],
    "android": ["android-cli", "flutter-apply-architecture-best-practices", "flutter-build-responsive-layout"],
    "data": ["bigquery-sql", "bigquery-ai-ml", "bigquery-bigframes", "dataform-bigquery", "dbt-bigquery"],
    "firebase": ["firebase-firestore", "firebase-auth-basics", "firebase-data-connect", "firebase-security-rules-auditor", "firebase-basics"],
    "web": ["chrome-devtools", "modern-web-guidance", "a11y-debugging", "memory-leak-debugging"],
    "admin": ["admin-elevation", "customizations-db", "credentials", "permissioned-github"],
    "security": ["admin-elevation", "credentials", "firebase-security-rules-auditor", "gcs-security-assessment"],
    "antigravity": ["antigravity-guide", "agy-customizations", "plugin", "automation", "generative_ui", "ui-plugin-navigation"]
}


def resolve_user_home() -> Path:
    """
    Resolves the local user profile directory reliably across both Home workstations
    and Enterprise Active Directory workstations (where Git/MSYS2 may set HOME to a
    mapped network drive like Q:\\ while .gemini resides in %USERPROFILE%).
    """
    if os.name == "nt":
        up = os.environ.get("USERPROFILE", "").strip()
        if up:
            return Path(up)
    return Path.home()


# Dynamically resolve user-relative paths
GEMINI_DIR = resolve_user_home() / ".gemini"
DB_DIR = GEMINI_DIR / "database"
DB_PATH = DB_DIR / "customizations.db"
GLOBAL_PLUGINS_PATH = GEMINI_DIR / "config" / "plugins"
ARCHIVED_PLUGINS_PATH = GEMINI_DIR / "plugins_archive"
BUILTIN_SKILLS_PATH = GEMINI_DIR / "antigravity" / "builtin" / "skills"

# Default network share fallback for team synchronization
DEFAULT_REMOTE_SHARE = Path(os.environ.get("SKILLSDB_REMOTE_SHARE") or str(GEMINI_DIR / "share" / "customizations.db"))

# Gitea integration settings (configured via runtime_config in customizations.db or environment variables)
GITEA_DEFAULT_URL = os.environ.get("SKILLSDB_GITEA_URL") or "http://127.0.0.1:3000"
GITEA_DEFAULT_USER = os.environ.get("SKILLSDB_GITEA_USER", "")
GITEA_DEFAULT_TOKEN = os.environ.get("SKILLSDB_GITEA_TOKEN", "")
GITEA_SERVICE_NAME = os.environ.get("SKILLSDB_GITEA_SERVICE") or "gitea"


def estimate_tokens(text: str) -> int:
    """Rough estimate of token count based on string length (average ~4 chars per token)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def read_jsonl_tail_lines(
    file_path: Path,
    max_bytes: int = 262144,
    max_lines: int = 120,
) -> list[str]:
    """
    O(1) binary tail-seek reader for large JSONL transcripts.
    Reads at most the final max_bytes from file_path, discards any partial leading line
    when seeking mid-file, and returns up to the last max_lines lines in < 1 ms.
    """
    try:
        fpath = Path(file_path)
        if not fpath.exists() or not fpath.is_file():
            return []
        with open(fpath, "rb") as bf:
            bf.seek(0, os.SEEK_END)
            file_size = bf.tell()
            if file_size <= 0:
                return []
            read_len = min(file_size, max(4096, int(max_bytes)))
            offset = file_size - read_len
            bf.seek(offset, os.SEEK_SET)
            raw_chunk = bf.read(read_len)
        text = raw_chunk.decode("utf-8", errors="ignore")
        lines = text.splitlines()
        if offset > 0 and lines:
            lines = lines[1:]
        if max_lines and max_lines > 0 and len(lines) > max_lines:
            return lines[-max_lines:]
        return lines
    except Exception:
        return []
