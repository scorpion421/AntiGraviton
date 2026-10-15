"""
Core engine modules: database connections, model tier detection, parallel execution, guardrails, and FIM slicing.
"""

from .db import get_connection, get_thread_connection, init_db
from .detector import detect_model_tier, set_profile, get_profile
from .concurrency import get_skills_parallel, search_multi_parallel, get_cluster, benchmark_concurrency
from .guardrails import check_text, check_file, check_workspace, verify_task, fix_file, fix_text, handle_pre_tool_guardrail_hook
from .fim import slice_fim, slice_fim_symbol, format_fim_block

__all__ = [
    "get_connection",
    "get_thread_connection",
    "init_db",
    "detect_model_tier",
    "set_profile",
    "get_profile",
    "get_skills_parallel",
    "search_multi_parallel",
    "get_cluster",
    "benchmark_concurrency",
    "check_text",
    "check_file",
    "check_workspace",
    "verify_task",
    "fix_file",
    "fix_text",
    "handle_pre_tool_guardrail_hook",
    "slice_fim",
    "slice_fim_symbol",
    "format_fim_block",
]
