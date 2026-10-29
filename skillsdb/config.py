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

__version__ = "3.7.9"
__suite_version__ = "1.4.9"

# Model capability tiers
TIER_LEAN = "lean"          # Gemini Flash, Flash-Lite, micro-skills, minimal token footprint
TIER_STANDARD = "standard"  # Gemini Pro, standard sequential execution
TIER_ULTRA = "ultra"        # Gemini Ultra, high-concurrency batching & subagent swarming
TIER_LOCAL = "local"        # Local / sovereign model (Ollama, Codestral, vLLM) with zero cloud egress
TIER_OFFLINE = "offline"    # Air-gapped offline operation

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

# Dynamically resolve user-relative paths
GEMINI_DIR = Path.home() / ".gemini"
DB_DIR = GEMINI_DIR / "database"
DB_PATH = DB_DIR / "customizations.db"
GLOBAL_PLUGINS_PATH = GEMINI_DIR / "config" / "plugins"
ARCHIVED_PLUGINS_PATH = GEMINI_DIR / "plugins_archive"
BUILTIN_SKILLS_PATH = GEMINI_DIR / "antigravity" / "builtin" / "skills"

# Default network share fallback for team synchronization
DEFAULT_REMOTE_SHARE = Path(os.environ.get("SKILLSDB_REMOTE_SHARE") or str(Path.home() / ".gemini" / "share" / "customizations.db"))

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
