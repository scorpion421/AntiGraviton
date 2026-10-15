"""
Safe update engine and non-destructive differential merge.
"""

from .merger import (
    find_repo_root,
    get_latest_release_info,
    check_update,
    merge_upstream_database,
    update_plugin,
    import_from_plugins,
    add_or_update_rule,
    add_or_update_skill,
    import_file,
    remove_item,
    learn_rule,
    sync_database,
    doctor,
)

__all__ = [
    "find_repo_root",
    "get_latest_release_info",
    "check_update",
    "merge_upstream_database",
    "update_plugin",
    "import_from_plugins",
    "add_or_update_rule",
    "add_or_update_skill",
    "import_file",
    "remove_item",
    "learn_rule",
    "sync_database",
    "doctor",
]
