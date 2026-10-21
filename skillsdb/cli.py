"""
SkillsDB command-line interface argument parsing and dispatching.
"""

import os
import sys
import json
import argparse
from pathlib import Path

from .config import SKILL_CLUSTERS, DB_PATH
from .core.db import get_connection, init_db
from .core.detector import get_profile, set_profile
from .core.concurrency import (
    get_skills_parallel,
    search_multi_parallel,
    get_cluster,
    benchmark_concurrency,
)
from .core.guardrails import (
    check_workspace,
    verify_task,
    fix_file,
    handle_pre_tool_guardrail_hook,
)
from .core.fim import (
    slice_fim,
    slice_fim_symbol,
    format_fim_block,
)
from .memory.handoff import (
    create_handoff,
    format_handoff_block,
    list_handoffs,
    read_handoff,
    update_handoff,
)
from .memory.agent_templates import (
    init_agent_templates,
    list_agent_templates,
    get_agent_template,
    register_agent_template,
    format_agent_prompt,
)
from .memory.gitea_sync import (
    gitea_status_summary,
    gitea_config_summary,
    sync_workspace,
    sync_all_workspaces,
    sync_changelog_releases,
    init_workspace_gitea,
    publish_gitea_release,
    setup_workspace_git,
    set_gitea_config,
    gitea_credential_helper,
)
from .platform.mcp_server import run_mcp_server
from .search.synonyms import init_synonyms
from .search.fts import (
    suggest_skills,
    search,
    get_skill,
    get_active_rules,
    get_rule,
    list_all,
    export_skill,
)
from .memory.writer_queue import stop_all_writer_queues
from .memory.project_memory import (
    find_project_root,
    get_project_connection,
    init_project_db,
    mem_init,
    mem_save_decision,
    mem_save_snapshot,
    mem_save_fact,
    mem_deprecate_fact,
    mem_fact_history,
    mem_reconcile,
    mem_task_add,
    mem_task_update,
    mem_task_list,
    mem_task_clear,
    mem_compact,
    mem_get_context,
    mem_search,
    mem_prune,
    handle_pre_invocation_hook,
    stats,
)
from .updater.merger import (
    doctor,
    check_update,
    update_plugin,
    import_from_plugins,
    learn_rule,
    import_file,
    remove_item,
    sync_database,
)
from .platform.windows_utf8 import fix_utf8


def build_parser() -> argparse.ArgumentParser:
    """Builds and returns the argparse ArgumentParser for SkillsDB."""
    parser = argparse.ArgumentParser(
        prog="skillsdb",
        description="SkillsDB: Central Customizations, Autonomous Project Memory & High-Concurrency Knowledge Engine for Google Antigravity",
    )
    subparsers = parser.add_subparsers(dest="command")

    # Central Database Commands
    subparsers.add_parser("import-all", help="Import/re-import all rules and skills into SQLite")
    subparsers.add_parser("get-active-rules", help="Output all active global rules")

    get_rule_parser = subparsers.add_parser("get-rule", help="Output a specific rule")
    get_rule_parser.add_argument("key", help="Rule key")

    get_skill_parser = subparsers.add_parser("get-skill", help="Output a specific skill or micro-skill section")
    get_skill_parser.add_argument("name", help="Skill name")
    get_skill_parser.add_argument("--summary", action="store_true", help="Output only skill outline and section list (~80 tokens)")
    get_skill_parser.add_argument("--section", default=None, help="Output only a specific section/micro-skill (~150-300 tokens)")

    learn_rule_parser = subparsers.add_parser("learn-rule", help="Persist a new learned rule into central database")
    learn_rule_parser.add_argument("key", help="Unique rule key")
    learn_rule_parser.add_argument("name", help="Rule name")
    learn_rule_parser.add_argument("content", help="Rule markdown content")
    learn_rule_parser.add_argument("--category", default="learned", help="Rule category")
    learn_rule_parser.add_argument("--scope", default="global", help="Rule scope")

    subparsers.add_parser("doctor", help="Run system diagnostics and verify installation health")

    fix_utf8_parser = subparsers.add_parser("fix-utf8", help="Configure Antigravity to run natively with UTF-8 code page on Windows")
    fix_utf8_parser.add_argument("--check", action="store_true", help="Only check UTF-8 status without modifying files")

    subparsers.add_parser("check-update", help="Check for available SkillsDB updates on GitHub")

    update_parser = subparsers.add_parser("update", help="Safely update SkillsDB to the latest version without data loss")
    update_parser.add_argument("--force", action="store_true", help="Force update even if on latest version")
    update_parser.add_argument("--check", action="store_true", help="Only check for updates (alias for check-update)")

    subparsers.add_parser("mem-pre-invocation-hook", help="Internal Antigravity lifecycle hook handler")
    subparsers.add_parser("guardrail-pre-tool-hook", help="Internal Antigravity PreToolUse lifecycle hook handler")

    # Native MCP Server Command (Mistral / Multi-Client Connector)
    subparsers.add_parser("mcp-serve", help="Run SkillsDB as a native Model Context Protocol (MCP) server over stdio")

    # FIM (Fill-in-the-Middle) Command (Codestral Surgical Slicing)
    fim_parser = subparsers.add_parser("fim", help="Fill-in-the-Middle surgical code chunking (Codestral pattern)")
    fim_sub = fim_parser.add_subparsers(dest="fim_action")
    fim_slice = fim_sub.add_parser("slice", help="Slice code file into minimal prefix/suffix window around target")
    fim_slice.add_argument("file_path", help="Path to target code file")
    fim_slice.add_argument("--line", type=int, default=None, help="Target line number (1-indexed)")
    fim_slice.add_argument("--symbol", default=None, help="Target symbol name (function, class, variable)")
    fim_slice.add_argument("--prefix", type=int, default=25, help="Prefix line window size (default: 25)")
    fim_slice.add_argument("--suffix", type=int, default=25, help="Suffix line window size (default: 25)")
    fim_slice.add_argument("--json", action="store_true", help="Output raw JSON instead of Codestral FIM block")

    # Agent Templates Command (Mistral Agents API pattern)
    agent_parser = subparsers.add_parser("agent", help="Reusable agent template catalog (Mistral Agents API pattern)")
    agent_sub = agent_parser.add_subparsers(dest="agent_action")
    agent_list = agent_sub.add_parser("list", help="List all registered agent templates")
    agent_list.add_argument("--json", action="store_true", help="Output as JSON")

    agent_get = agent_sub.add_parser("get", help="Get agent template details and prompt block")
    agent_get.add_argument("id", help="Agent template ID")
    agent_get.add_argument("--json", action="store_true", help="Output as JSON")

    agent_reg = agent_sub.add_parser("register", help="Register or update an agent template")
    agent_reg.add_argument("id", help="Unique agent ID")
    agent_reg.add_argument("--name", required=True, help="Agent display name")
    agent_reg.add_argument("--role", required=True, help="Agent role description")
    agent_reg.add_argument("--prompt", required=True, help="Agent system prompt instructions")
    agent_reg.add_argument("--skills", default="", help="Comma-separated skill names")
    agent_reg.add_argument("--tools", default="", help="Comma-separated tool names")

    search_parser = subparsers.add_parser("search", help="Full-text search in rules and skills")
    search_parser.add_argument("query", help="Search query")

    suggest_parser = subparsers.add_parser("suggest", help="Auto-recommend relevant skills for a user task")
    suggest_parser.add_argument("task", help="Description of the task")
    suggest_parser.add_argument("--cwd", default=None, help="Working directory path for directory-scoped recommendations")
    suggest_parser.add_argument("--json", action="store_true", help="Output as JSON")

    import_file_parser = subparsers.add_parser("import-file", help="Import any markdown skill or rule file")
    import_file_parser.add_argument("file_path", help="Path to markdown file")

    remove_parser = subparsers.add_parser("remove", help="Remove an item from the database")
    remove_parser.add_argument("type", choices=["skill", "rule"], help="Item type")
    remove_parser.add_argument("name", help="Item name or key")

    sync_parser = subparsers.add_parser("sync", help="Synchronize database with network team share")
    sync_parser.add_argument("direction", choices=["push", "pull"], help="Sync direction")
    sync_parser.add_argument("--remote", default=None, help="Remote database path (optional)")

    subparsers.add_parser("list", help="List all rules and plugins/skills summary")

    stats_parser = subparsers.add_parser("stats", help="Show database statistics and token savings")
    stats_parser.add_argument("--savings", action="store_true", help="Display measured token and cost savings")

    export_parser = subparsers.add_parser("export-skill", help="Export a skill into a project workspace")
    export_parser.add_argument("name", help="Skill name")
    export_parser.add_argument("target", help="Target project root directory")

    # Guardrail Commands
    guard_parser = subparsers.add_parser("guardrail", help="Deterministic guardrails, safety invariants, and task verification")
    guard_sub = guard_parser.add_subparsers(dest="guard_action")

    guard_chk = guard_sub.add_parser("check", help="Check workspace or staged files against guardrails")
    guard_chk.add_argument("--path", default=None, help="Root path to inspect (default: CWD)")
    guard_chk.add_argument("--staged", action="store_true", help="Only check git staged files")
    guard_chk.add_argument("--strict", action="store_true", help="Strict blocking mode (exits 1 on warnings)")
    guard_chk.add_argument("--fix", action="store_true", help="Auto-fix formatting violations in-place")
    guard_chk.add_argument("--json", action="store_true", help="Output results as JSON")

    guard_ver = guard_sub.add_parser("verify-task", help="Verify task with guardrails and test command before completing")
    guard_ver.add_argument("task_id", type=int, help="Task ID")
    guard_ver.add_argument("--cmd", default=None, help="Automated test assertion command (e.g. 'pytest' or 'flutter test')")
    guard_ver.add_argument("--strict", action="store_true", help="Strict mode: fail on any advisory warnings")
    guard_ver.add_argument("--no-fix", action="store_true", help="Disable automatic formatting remediation")
    guard_ver.add_argument("--project", default=None, help="Project root directory (optional)")

    # Handoff Commands
    ho_parser = subparsers.add_parser("handoff", help="Clean multi-agent delegation and context handoffs")
    ho_sub = ho_parser.add_subparsers(dest="ho_action")

    ho_create = ho_sub.add_parser("create", help="Create an agent handoff payload for a task")
    ho_create.add_argument("--task", type=int, required=True, help="Task ID to hand off")
    ho_create.add_argument("--role", required=True, help="Target specialist agent role (e.g. 'Test Engineer', 'Security Auditor')")
    ho_create.add_argument("--source", default="lead", help="Source agent role (default: lead)")
    ho_create.add_argument("--notes", default="", help="Handoff instructions or constraints")
    ho_create.add_argument("--vars", default=None, help="JSON-encoded context variables dictionary")
    ho_create.add_argument("--project", default=None, help="Project root directory (optional)")
    ho_create.add_argument("--json", action="store_true", help="Output raw JSON instead of markdown block")

    ho_list = ho_sub.add_parser("list", help="List recent agent handoffs")
    ho_list.add_argument("--status", choices=["pending", "accepted", "completed", "rejected"], default=None, help="Filter by status")
    ho_list.add_argument("--project", default=None, help="Project root directory (optional)")
    ho_list.add_argument("--json", action="store_true", help="Output as JSON")

    ho_read = ho_sub.add_parser("read", help="Read full details and prompt block for a handoff")
    ho_read.add_argument("id", help="Handoff ID")
    ho_read.add_argument("--project", default=None, help="Project root directory (optional)")
    ho_read.add_argument("--json", action="store_true", help="Output as raw JSON")

    ho_update = ho_sub.add_parser("update", help="Update the status of a handoff")
    ho_update.add_argument("id", help="Handoff ID")
    ho_update.add_argument("status", choices=["pending", "accepted", "completed", "rejected"], help="New status")
    ho_update.add_argument("--project", default=None, help="Project root directory (optional)")

    # Project Memory Commands
    mem_init_parser = subparsers.add_parser("mem-init", help="Initialize project memory database (.agents/memory.db)")
    mem_init_parser.add_argument("--project", default=None, help="Project root directory (optional)")

    mem_dec_parser = subparsers.add_parser("mem-save-decision", help="Save an architectural or setup decision")
    mem_dec_parser.add_argument("title", help="Decision title")
    mem_dec_parser.add_argument("content", help="Decision content/rationale")
    mem_dec_parser.add_argument("--category", default="architecture", help="Category (default: architecture)")
    mem_dec_parser.add_argument("--project", default=None, help="Project root directory (optional)")

    mem_snap_parser = subparsers.add_parser("mem-save-snapshot", help="Save a session milestone snapshot")
    mem_snap_parser.add_argument("summary", help="Summary of work completed")
    mem_snap_parser.add_argument("--cid", default=None, help="Antigravity conversation ID")
    mem_snap_parser.add_argument("--next-steps", default=None, help="Next steps or open items")
    mem_snap_parser.add_argument("--files", default=None, help="Key files touched")
    mem_snap_parser.add_argument("--project", default=None, help="Project root directory (optional)")
    mem_snap_parser.add_argument("--no-commit", action="store_true", help="Disable automated Git commit and Gitea push")

    mem_fact_parser = subparsers.add_parser("mem-save-fact", help="Save a key-value fact or configuration")
    mem_fact_parser.add_argument("key", help="Fact key")
    mem_fact_parser.add_argument("value", help="Fact value")
    mem_fact_parser.add_argument("--reason", default=None, help="Reason if updating/superseding an existing fact")
    mem_fact_parser.add_argument("--project", default=None, help="Project root directory (optional)")

    mem_dep_fact_parser = subparsers.add_parser("mem-deprecate-fact", help="Tombstone a fact so it no longer pollutes LLM context")
    mem_dep_fact_parser.add_argument("key", help="Fact key to deprecate")
    mem_dep_fact_parser.add_argument("--reason", default="deprecated", help="Reason for deprecation")
    mem_dep_fact_parser.add_argument("--project", default=None, help="Project root directory (optional)")

    mem_hist_parser = subparsers.add_parser("mem-fact-history", help="Show audit log of fact changes and reconciliation")
    mem_hist_parser.add_argument("--key", default=None, help="Filter history by fact key")
    mem_hist_parser.add_argument("--project", default=None, help="Project root directory (optional)")

    mem_rec_parser = subparsers.add_parser("mem-reconcile", help="Report fact lifecycle and tombstone health")
    mem_rec_parser.add_argument("--project", default=None, help="Project root directory (optional)")

    mem_task_add_parser = subparsers.add_parser("mem-task-add", help="Add a task to the project task board")
    mem_task_add_parser.add_argument("title", help="Task title")
    mem_task_add_parser.add_argument("--desc", default="", help="Task description")
    mem_task_add_parser.add_argument("--priority", choices=["low", "med", "high", "critical"], default="med", help="Priority (default: med)")
    mem_task_add_parser.add_argument("--status", choices=["pending", "in_progress", "completed", "blocked"], default="pending", help="Status (default: pending)")
    mem_task_add_parser.add_argument("--project", default=None, help="Project root directory (optional)")

    mem_task_update_parser = subparsers.add_parser("mem-task-update", help="Update task status, priority, or details")
    mem_task_update_parser.add_argument("id", type=int, help="Task ID")
    mem_task_update_parser.add_argument("--status", choices=["pending", "in_progress", "completed", "blocked"], default=None, help="New status")
    mem_task_update_parser.add_argument("--priority", choices=["low", "med", "high", "critical"], default=None, help="New priority")
    mem_task_update_parser.add_argument("--title", default=None, help="Updated title")
    mem_task_update_parser.add_argument("--desc", default=None, help="Updated description")
    mem_task_update_parser.add_argument("--project", default=None, help="Project root directory (optional)")

    mem_task_list_parser = subparsers.add_parser("mem-task-list", help="List tasks from the project task board")
    mem_task_list_parser.add_argument("--status", default=None, help="Filter by status (pending, in_progress, completed, blocked, all)")
    mem_task_list_parser.add_argument("--json", action="store_true", help="Output as JSON")
    mem_task_list_parser.add_argument("--project", default=None, help="Project root directory (optional)")

    mem_task_clear_parser = subparsers.add_parser("mem-task-clear", help="Clear completed tasks from task board")
    mem_task_clear_parser.add_argument("--all", action="store_true", help="Clear all tasks, not just completed")
    mem_task_clear_parser.add_argument("--project", default=None, help="Project root directory (optional)")

    mem_compact_parser = subparsers.add_parser("mem-compact", help="Compact episodic project memory and vacuum database")
    mem_compact_parser.add_argument("--summary", default=None, help="Optional custom summary for compacted snapshot")
    mem_compact_parser.add_argument("--next-steps", default=None, help="Optional next steps to record")
    mem_compact_parser.add_argument("--keep-completed", action="store_true", help="Do not prune completed tasks during compaction")
    mem_compact_parser.add_argument("--project", default=None, help="Project root directory (optional)")

    mem_ctx_parser = subparsers.add_parser("mem-get-context", help="Retrieve compact project context")
    mem_ctx_parser.add_argument("--project", default=None, help="Project root directory (optional)")

    mem_srch_parser = subparsers.add_parser("mem-search", help="Search project memory")
    mem_srch_parser.add_argument("query", help="Search query")
    mem_srch_parser.add_argument("--project", default=None, help="Project root directory (optional)")

    mem_prune_parser = subparsers.add_parser("mem-prune", help="Prune deleted conversation snapshots and vacuum DB")
    mem_prune_parser.add_argument("--max-snapshots", type=int, default=10, help="Maximum snapshots to keep")
    mem_prune_parser.add_argument("--max-age", type=int, default=30, help="Max age in days")
    mem_prune_parser.add_argument("--project", default=None, help="Project root directory (optional)")

    # Model Profile Commands
    profile_parser = subparsers.add_parser("profile", help="View or configure model tier concurrency profile (ultra, standard, lean, local, offline, auto)")
    profile_parser.add_argument("action", nargs="?", default="get", choices=["get", "set", "auto"], help="Profile action (default: get)")
    profile_parser.add_argument("tier", nargs="?", default=None, help="Model tier when action is 'set' (ultra, standard, lean, local, offline)")

    # Parallel Concurrency Commands
    get_skills_parser = subparsers.add_parser("get-skills", help="Retrieve multiple skills concurrently in a single batch call")
    get_skills_parser.add_argument("names", nargs="+", help="Skill names to retrieve concurrently")
    get_skills_parser.add_argument("--summary", action="store_true", help="Output only outlines and section lists")
    get_skills_parser.add_argument("--workers", type=int, default=16, help="Max worker threads (default: 16)")
    get_skills_parser.add_argument("--json", action="store_true", help="Output results as JSON")

    search_multi_parser = subparsers.add_parser("search-multi", help="Execute multiple search queries concurrently")
    search_multi_parser.add_argument("queries", nargs="+", help="Search queries")
    search_multi_parser.add_argument("--workers", type=int, default=16, help="Max worker threads (default: 16)")
    search_multi_parser.add_argument("--limit", type=int, default=4, help="Limit per query (default: 4)")
    search_multi_parser.add_argument("--json", action="store_true", help="Output results as JSON")

    cluster_parser = subparsers.add_parser("get-cluster", help="Prefetch all skills belonging to a domain cluster concurrently")
    cluster_parser.add_argument("domain", help=f"Domain cluster ({', '.join(SKILL_CLUSTERS.keys())})")
    cluster_parser.add_argument("--summary", action="store_true", help="Output only outlines and section lists")
    cluster_parser.add_argument("--workers", type=int, default=16, help="Max worker threads (default: 16)")
    cluster_parser.add_argument("--json", action="store_true", help="Output results as JSON")

    bench_parser = subparsers.add_parser("benchmark-concurrency", help="Benchmark sequential vs. parallel multi-threaded retrieval")
    bench_parser.add_argument("--queries", type=int, default=20, help="Number of queries/skills to test (default: 20)")
    bench_parser.add_argument("--workers", type=int, default=16, help="Max worker threads (default: 16)")

    # Gitea Service and Automated Commit Sync Commands
    subparsers.add_parser("gitea-status", help="Show status of Gitea service, API health, and repositories")

    p_gitea_sync = subparsers.add_parser("gitea-sync", help="Synchronize workspace code history and Keep a Changelog releases to Gitea")
    p_gitea_sync.add_argument("--message", "-m", default=None, help="Custom commit message")
    p_gitea_sync.add_argument("--no-push", action="store_true", help="Commit only, do not push to Gitea")
    p_gitea_sync.add_argument("--all", action="store_true", help="Synchronize all discovered Antigravity workspaces to Gitea")
    p_gitea_sync.add_argument("--releases", action="store_true", help="Force sync all Keep a Changelog releases from CHANGELOG.md to Gitea (and GitHub if --github is set)")
    p_gitea_sync.add_argument("--github", action="store_true", help="Also synchronize Keep a Changelog releases to GitHub via gh CLI")
    p_gitea_sync.add_argument("--project", default=None, help="Project root directory (optional)")
    p_gitea_sync.add_argument("--name", default=None, help="Target Gitea repository name (optional)")
    p_gitea_sync.add_argument("--use-existing", action="store_true", help="Proceed if a repository with this name already exists on Gitea")

    p_gitea_init = subparsers.add_parser("gitea-init", help="Initialize Gitea repository and tracking for workspace")
    p_gitea_init.add_argument("path", nargs="?", default=".", help="Workspace path to initialize")
    p_gitea_init.add_argument("--name", default=None, help="Custom Gitea repository name (default: folder name)")
    p_gitea_init.add_argument("--use-existing", action="store_true", help="Use and update an existing Gitea repository with the same name")
    p_gitea_init.add_argument("--overwrite", action="store_true", help="Delete and recreate an existing Gitea repository with the same name")
    p_gitea_init.add_argument("--url", default=None, help="Configure Gitea server URL in runtime_config")
    p_gitea_init.add_argument("--user", default=None, help="Configure Gitea repository owner in runtime_config")
    p_gitea_init.add_argument("--token", default=None, help="Configure Gitea API token in runtime_config")
    p_gitea_init.add_argument("--workspaces-root", default=None, help="Configure primary workspaces root directory in runtime_config")

    p_gitea_cfg = subparsers.add_parser("gitea-config", help="View, update, or auto-detect Gitea and drive paths in runtime_config")
    p_gitea_cfg.add_argument("--url", default=None, help="Update Gitea server URL")
    p_gitea_cfg.add_argument("--user", default=None, help="Update Gitea repository owner")
    p_gitea_cfg.add_argument("--token", default=None, help="Update Gitea API token")
    p_gitea_cfg.add_argument("--workspaces-root", default=None, help="Update primary workspaces root directory (auto-aligns remote_share_path)")
    p_gitea_cfg.add_argument("--remote-share", default=None, help="Update remote customizations.db share path")
    p_gitea_cfg.add_argument("--auto-detect", action="store_true", help="Automatically scan drives and registered projects to heal paths")

    p_gitea_rel = subparsers.add_parser("gitea-release", help="Publish or update a Gitea release with Git tag and CHANGELOG.md notes")
    p_gitea_rel.add_argument("tag", nargs="?", default=None, help="Version tag (e.g. v1.0.8; defaults to VERSION / CHANGELOG.md)")
    p_gitea_rel.add_argument("--title", "-t", default=None, help="Release title (default: '<RepoName> <Tag>')")
    p_gitea_rel.add_argument("--notes", "-n", default=None, help="Release notes body (default: auto-extracted from CHANGELOG.md)")
    p_gitea_rel.add_argument("--project", default=None, help="Project root directory (optional)")
    p_gitea_rel.add_argument("--name", default=None, help="Target Gitea repository name (optional)")
    p_gitea_rel.add_argument("--prerelease", action="store_true", help="Mark release as pre-release")
    p_gitea_rel.add_argument("--github", action="store_true", help="Also publish or update the release on GitHub via gh CLI")

    p_gitea_cred = subparsers.add_parser("gitea-credential-helper", help="Git credential helper for token-free .git/config authentication")
    p_gitea_cred.add_argument("action", nargs="?", default="get", help="Git credential helper action (get, store, erase)")

    return parser


def main(argv: list[str] = None):
    """Main CLI entrypoint for SkillsDB."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "mem-pre-invocation-hook":
        handle_pre_invocation_hook()
        return

    if args.command == "guardrail-pre-tool-hook":
        handle_pre_tool_guardrail_hook()
        return

    # Route native MCP Server
    if args.command == "mcp-serve":
        run_mcp_server()
        return

    # Route FIM (Fill-in-the-Middle) Command
    if args.command == "fim":
        if args.fim_action == "slice":
            fpath = Path(args.file_path)
            try:
                if args.symbol:
                    res = slice_fim_symbol(fpath, args.symbol, prefix_lines=args.prefix, suffix_lines=args.suffix)
                elif args.line:
                    res = slice_fim(fpath, args.line, prefix_lines=args.prefix, suffix_lines=args.suffix)
                else:
                    print("Error: Specify either --line <number> or --symbol <name>.")
                    sys.exit(1)

                if args.json:
                    print(json.dumps(res, indent=2))
                else:
                    print(format_fim_block(res))
            except Exception as e:
                print(f"Error: {e}")
                sys.exit(1)
            return

    # Route Agent Templates Command
    if args.command == "agent":
        conn = get_connection(DB_PATH)
        init_db(conn)
        init_agent_templates(conn)
        try:
            if args.agent_action == "list" or not args.agent_action:
                tmpls = list_agent_templates(conn)
                if getattr(args, "json", False):
                    print(json.dumps(tmpls, indent=2))
                else:
                    print(f"\n=== REGISTERED AGENT TEMPLATES ({len(tmpls)} templates) ===")
                    for t in tmpls:
                        s_str = ", ".join(t['skills']) if t['skills'] else "None"
                        print(f"  * [{t['id']}] {t['name']}")
                        print(f"      Role:   {t['role']}")
                        print(f"      Skills: {s_str}")
                    print()
                return

            elif args.agent_action == "get":
                tmpl = get_agent_template(conn, args.id)
                if not tmpl:
                    print(f"Error: Agent template '{args.id}' not found.")
                    sys.exit(1)
                if args.json:
                    print(json.dumps(tmpl, indent=2))
                else:
                    print(format_agent_prompt(tmpl))
                return

            elif args.agent_action == "register":
                skills = [s.strip() for s in args.skills.split(",") if s.strip()] if args.skills else []
                tools = [t.strip() for t in args.tools.split(",") if t.strip()] if args.tools else []
                res = register_agent_template(conn, args.id, args.name, args.role, args.prompt, skills=skills, tools=tools)
                print(f"Agent template '{args.id}' ({args.name}) registered successfully.")
                return
        finally:
            conn.close()

    # Route guardrail commands
    if args.command == "guardrail":
        if args.guard_action == "check":
            root_p = Path(args.path) if args.path else None
            res = check_workspace(root_p, staged_only=args.staged, strict=args.strict, auto_fix=args.fix)
            if args.json:
                print(json.dumps(res, indent=2))
                return
            status_tag = "[PASSED]" if res["passed"] else ("[FAILED]" if args.strict else "[ADVISORY WARNINGS]")
            print(f"\n=== GUARDRAIL AUDIT REPORT: {status_tag} ===")
            print(f"  Files Checked:      {res['total_files_checked']}")
            print(f"  Violations Found:   {res['violations_count']}")
            if res.get("total_fixes", 0) > 0:
                print(f"  Auto-Fixes Applied: {res['total_fixes']}")
            if res["violations"]:
                print("\nViolations List:")
                for v in res["violations"]:
                    print(f"  * [{v['severity']}] {v['file']}:{v['line']} ({v['rule']}): {v['message']}")
                    if v['snippet']:
                        print(f"      Snippet: {v['snippet']}")
            print("============================================\n")
            if args.strict and not res["passed"]:
                sys.exit(1)
            return

        elif args.guard_action == "verify-task":
            proj_p = Path(args.project) if getattr(args, "project", None) else None
            res = verify_task(args.task_id, test_cmd=args.cmd, project_root=proj_p, strict=args.strict, auto_fix=not args.no_fix)
            if res["verified"]:
                print(f"[VERIFIED] Task #{res['task_id']} marked COMPLETED.")
                print(f"  Details: {res['details']}")
            else:
                print(f"[VERIFICATION FAILED] Task #{args.task_id} not completed.")
                print(f"  Reason: {res.get('reason', 'Unknown failure')}")
                if res.get("snippet"):
                    print(f"  Output: {res['snippet']}")
                sys.exit(1)
            return

    # Route handoff commands
    if args.command == "handoff":
        proj_p = Path(args.project) if getattr(args, "project", None) else None
        if args.ho_action == "create":
            context_vars = None
            if args.vars:
                try:
                    context_vars = json.loads(args.vars)
                except Exception as e:
                    print(f"Error parsing --vars JSON: {e}")
                    sys.exit(1)
            try:
                res = create_handoff(args.task, args.role, source_role=args.source, notes=args.notes, context_vars=context_vars, project_root=proj_p)
                if args.json:
                    print(json.dumps(res, indent=2))
                else:
                    print(format_handoff_block(res))
            except ValueError as e:
                print(f"Error: {e}")
                sys.exit(1)
            return

        elif args.ho_action == "list":
            rows = list_handoffs(project_root=proj_p, status=args.status)
            if args.json:
                print(json.dumps(rows, indent=2))
                return
            filter_s = f" (Status: {args.status})" if args.status else ""
            print(f"\n=== AGENT HANDOFFS{filter_s} ({len(rows)} records) ===")
            if not rows:
                print("  No handoff records found.")
            else:
                for r in rows:
                    print(f"  * [{r['id']}] [{r['status'].upper():9}] Task #{r['task_id']}: [{r['source_role']}] -> [{r['target_role']}] ({r['created_at'][:19]})")
            print()
            return

        elif args.ho_action == "read":
            res = read_handoff(args.id, project_root=proj_p)
            if not res:
                print(f"Error: Handoff '{args.id}' not found.")
                sys.exit(1)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print(format_handoff_block(res))
            return

        elif args.ho_action == "update":
            ok = update_handoff(args.id, args.status, project_root=proj_p)
            if ok:
                print(f"Handoff '{args.id}' updated -> [{args.status.upper()}]")
            else:
                print(f"Failed to update handoff '{args.id}'.")
                sys.exit(1)
            return

    # Route Gitea commands
    if args.command == "gitea-status":
        gitea_status_summary()
        return
    elif args.command == "gitea-sync":
        if getattr(args, "all", False):
            sync_all_workspaces()
        else:
            proj_root = Path(args.project) if getattr(args, "project", None) else find_project_root()
            sync_gh = getattr(args, "github", False)
            ok = sync_workspace(
                proj_root,
                message=args.message,
                push=not getattr(args, "no_push", False),
                sync_github=sync_gh,
                repo_name=getattr(args, "name", None),
                allow_existing=getattr(args, "use_existing", False),
            )
            if not ok:
                sys.exit(2)
            if getattr(args, "releases", False):
                tags = sync_changelog_releases(proj_root, sync_github=sync_gh, overwrite_existing=True)
                if tags:
                    print(f"  [OK] Force-synced Keep a Changelog releases: {', '.join(tags)}")
        return
    elif args.command == "gitea-init":
        if any(getattr(args, k, None) is not None for k in ("url", "user", "token", "workspaces_root")):
            set_gitea_config(
                url=getattr(args, "url", None),
                user=getattr(args, "user", None),
                token=getattr(args, "token", None),
                workspaces_root=getattr(args, "workspaces_root", None),
            )
        target_path = Path(os.path.abspath(str(args.path)))
        ok = init_workspace_gitea(
            target_path,
            repo_name=getattr(args, "name", None),
            use_existing=getattr(args, "use_existing", False),
            overwrite=getattr(args, "overwrite", False),
        )
        if not ok:
            sys.exit(2)
        return
    elif args.command == "gitea-config":
        if any(getattr(args, k, None) is not None for k in ("url", "user", "token", "workspaces_root", "remote_share")):
            set_gitea_config(
                url=getattr(args, "url", None),
                user=getattr(args, "user", None),
                token=getattr(args, "token", None),
                workspaces_root=getattr(args, "workspaces_root", None),
                remote_share=getattr(args, "remote_share", None),
            )
        gitea_config_summary(auto_detect=getattr(args, "auto_detect", False))
        return
    elif args.command == "gitea-credential-helper":
        gitea_credential_helper(getattr(args, "action", "get"))
        return
    elif args.command == "gitea-release":
        proj_root = Path(args.project) if getattr(args, "project", None) else find_project_root()
        url = publish_gitea_release(
            proj_root,
            tag_name=getattr(args, "tag", None),
            title=getattr(args, "title", None),
            notes=getattr(args, "notes", None),
            repo_name=getattr(args, "name", None),
            prerelease=getattr(args, "prerelease", False),
            sync_github=getattr(args, "github", False),
        )
        if not url:
            sys.exit(2)
        return

    # Route project memory commands directly to project database
    if args.command and args.command.startswith("mem-"):
        proj_root = Path(args.project) if getattr(args, "project", None) else find_project_root()
        if args.command == "mem-init":
            mem_init(proj_root)
            return

        pconn = get_project_connection(proj_root)
        init_project_db(pconn)

        if args.command == "mem-save-decision":
            mem_save_decision(pconn, args.title, args.content, args.category)
        elif args.command == "mem-save-snapshot":
            mem_save_snapshot(
                pconn,
                args.summary,
                conversation_id=args.cid,
                next_steps=args.next_steps,
                files_touched=args.files,
                auto_commit=not getattr(args, "no_commit", False),
            )
        elif args.command == "mem-save-fact":
            mem_save_fact(pconn, args.key, args.value, reason=args.reason)
        elif args.command == "mem-deprecate-fact":
            mem_deprecate_fact(pconn, args.key, reason=args.reason)
        elif args.command == "mem-fact-history":
            mem_fact_history(pconn, key=args.key)
        elif args.command == "mem-reconcile":
            mem_reconcile(pconn)
        elif args.command == "mem-task-add":
            mem_task_add(pconn, args.title, description=args.desc, priority=args.priority, status=args.status)
        elif args.command == "mem-task-update":
            mem_task_update(pconn, args.id, status=args.status, priority=args.priority, title=args.title, description=args.desc)
        elif args.command == "mem-task-list":
            mem_task_list(pconn, status=args.status, as_json=args.json)
        elif args.command == "mem-task-clear":
            mem_task_clear(pconn, only_completed=not getattr(args, "all", False))
        elif args.command == "mem-compact":
            mem_compact(pconn, summary=args.summary, next_steps=args.next_steps, archive_completed=not getattr(args, "keep_completed", False), project_root=proj_root)
        elif args.command == "mem-get-context":
            mem_get_context(pconn, project_root=proj_root)
        elif args.command == "mem-search":
            mem_search(pconn, args.query)
        elif args.command == "mem-prune":
            mem_prune(pconn, max_snapshots=args.max_snapshots, max_age_days=args.max_age)

        stop_all_writer_queues()
        pconn.close()
        return

    # Route central DB commands
    conn = get_connection(DB_PATH)
    init_db(conn)
    init_synonyms(conn)

    if args.command == "import-all" or not args.command:
        import_from_plugins(conn)
    elif args.command == "get-active-rules":
        get_active_rules(conn)
    elif args.command == "get-rule":
        get_rule(conn, args.key)
    elif args.command == "get-skill":
        get_skill(conn, args.name, summary=getattr(args, "summary", False), section=getattr(args, "section", None))
    elif args.command == "learn-rule":
        learn_rule(conn, args.key, args.name, args.content, category=args.category, scope=args.scope)
    elif args.command == "doctor":
        doctor(conn)
    elif args.command == "fix-utf8":
        fix_utf8(check_only=getattr(args, "check", False))
    elif args.command == "check-update":
        check_update()
    elif args.command == "update":
        if getattr(args, "check", False):
            check_update()
        else:
            update_plugin(force=getattr(args, "force", False))
    elif args.command == "search":
        search(conn, args.query)
    elif args.command == "suggest":
        suggest_skills(conn, args.task, as_json=args.json, cwd_path=Path(args.cwd) if getattr(args, "cwd", None) else None)
    elif args.command == "import-file":
        import_file(conn, args.file_path)
    elif args.command == "remove":
        remove_item(conn, args.type, args.name)
    elif args.command == "sync":
        sync_database(conn, args.direction, args.remote)
    elif args.command == "list":
        list_all(conn)
    elif args.command == "stats":
        stats(conn, show_savings=getattr(args, "savings", False))
    elif args.command == "export-skill":
        export_skill(conn, args.name, args.target)
    elif args.command == "profile":
        if args.action == "set":
            if not args.tier:
                print("Error: Specify tier to set (ultra, standard, lean, local, offline). Example: skillsdb profile set local")
            else:
                set_profile(args.tier, db_path=DB_PATH)
        elif args.action == "auto":
            set_profile("auto", db_path=DB_PATH)
        else:
            get_profile()
    elif args.command == "get-skills":
        get_skills_parallel(args.names, max_workers=args.workers, as_json=args.json, summary=args.summary, db_path=DB_PATH)
    elif args.command == "search-multi":
        search_multi_parallel(args.queries, max_workers=args.workers, limit_per_query=args.limit, as_json=args.json, db_path=DB_PATH)
    elif args.command == "get-cluster":
        get_cluster(args.domain, max_workers=args.workers, as_json=args.json, summary=args.summary, db_path=DB_PATH)
    elif args.command == "benchmark-concurrency":
        benchmark_concurrency(num_queries=args.queries, max_workers=args.workers, db_path=DB_PATH)

    conn.close()


if __name__ == "__main__":
    main()
