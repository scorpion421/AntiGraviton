# Changelog

All notable changes to the **AntiGraviton** project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.2.1] - 2026-11-07

### Fixed
- **Persistent `inferred_workspace` across incremental `O(1)` `f.seek()` transcript scans (`skillsdb/memory/project_memory.py`, `v4.2.1`)**:
  - Restores `stored_inferred_workspace` from `transcript_cursors.inferred_workspace` when an incremental post-compaction `f.seek(last_byte_offset)` delta contains no new file paths (`candidate_roots == {}`), guaranteeing that `.agents/memory.db` (including `github_owner_handle = https://github.com/scorpion421`) is always re-hydrated after compaction even when `workspacePaths == []`.
- **Explicit GitHub remote URL output in `skillsdb gitea-sync --github` (`skillsdb/memory/gitea_sync.py`, `v4.2.1`)**:
  - Updated `sync_workspace()` to print the resolved GitHub remote URL alongside the push confirmation (`[OK] Pushed branch 'main' to GitHub remote (https://github.com/scorpion421/AntiGraviton)`), preventing confusion between the local Gitea owner (`MickyFoley`) and the GitHub account (`scorpion421`).

---

## [2.2.0] - 2026-11-07

### Added
- **Incremental `O(1)` byte-offset transcript cursor & lossless `compaction_archive` (Step 1, `skillsdb/core/db.py`, `skillsdb/memory/project_memory.py`, `v4.2.0`)**:
  - Added the `transcript_cursors` table to `customizations.db` and replaced `%TEMP%\skillsdb_hook_state.json` with ACID-safe SQLite cursor tracking (`last_byte_offset`, `max_step_index`, `checkpoint_count`, `hydrated_checkpoint_count`, `cached_state_json`).
  - Upgraded `analyze_conversation_epochs_and_scope()` to seek directly to `last_byte_offset` (`f.seek(last_byte_offset)`), reducing warm transcript analysis latency on the `8.82 MB` (`4,346`-step) benchmark from `80.05 ms` to **`2.04 ms`** (`39.3x` faster).
  - Added the `compaction_archive` table to `.agents/memory.db` to automatically persist every `"type": "CHECKPOINT"` compaction summary and index it into `project_memory_fts` (`31/31` checkpoints archived on benchmark `ab05cd72-aa2b-4ffd-b0a7-dfce798a8295`, reducing generational compaction loss from `96.7%` to **`0%`**).
- **Zero-turn `active_working_set`, automatic `CRLF -> LF` pre-normalization, live post-edit `symbol_index` line shifts & BM25 fact ranking (Step 2, `skillsdb/memory/project_memory.py`, `skillsdb/core/guardrails.py`, `v4.2.0`)**:
  - Added the `active_working_set` table (`file_path`, `symbols_touched`, `line_ranges`, `edit_count`, `updated_at`) to `.agents/memory.db` and wired `record_working_set_file_edit()` into `PreToolUse` (`handle_pre_tool_guardrail_hook()`).
  - Automatically normalizes mixed `CRLF -> LF` line endings on disk when an incoming `edit_file` `Target` uses `LF` (eliminating `target content not found` retry turns) and immediately re-indexes post-edit symbol `[start_line, end_line]` ranges in memory (`~14.2 ms` total latency, `0` extra prompt tokens).
  - Upgraded `_collect_project_memory_lines()` to rank `project_facts` via `project_memory_fts` BM25 when a project exceeds `12` active facts while strictly preserving the `12`-fact prompt cap.
- **Relational codebase dependency graph (`dependency_edges`) & blast-radius impact queries (Step 3, `skillsdb/memory/project_memory.py`, `skillsdb/cli.py`, `v4.2.0`)**:
  - Added the `dependency_edges` table (`source_file`, `source_symbol`, `target_symbol`, `edge_kind` in `calls|imports|inherits`, `line_no`, `file_mtime`) to `.agents/memory.db`, populated automatically during `mem_index_symbols()` and single-file `record_working_set_file_edit()` across Python AST and multi-language regex/Smali (`invoke-*`, `.super`) files (`10,541` edges indexed across `33` files in AntiGraviton).
  - Added `skillsdb impact <symbol>` (alias `mem-impact`) and `skillsdb locate <symbol> --impact` (`1.75 ms` query latency).
- **Pre-compiled `skill_sections` FTS5 index, `skillsdb slice-skill`, and federated `skillsdb pulse` (Step 4, `skillsdb/core/db.py`, `skillsdb/search/fts.py`, `skillsdb/updater/merger.py`, `skillsdb/memory/project_memory.py`, `skillsdb/cli.py`, `v4.2.0`)**:
  - Added `skill_sections` and `skill_sections_fts` to `customizations.db`, decomposing all `124` skills into `2,931` atomic `##`/`###` micro-sections (`142` tokens/section vs. `3,477` tokens/full skill = **`-95.9%`** token reduction).
  - Added `skillsdb slice-skill "<query>"` (`0.85 ms` BM25 section retrieval) and `skillsdb pulse "<query>"` (`mem_pulse()`, single-process federated retrieval across `symbol_index`, `dependency_edges`, `project_memory_fts`, `active_working_set`, and `skill_sections_fts`).
- **Universal 12-language polyglot symbol, call-graph, and live line-shift engine (`skillsdb/core/fim.py`, `skillsdb/memory/project_memory.py`, `v4.2.0`)**:
  - Expanded `_INDEX_CODE_EXTS` to 29 file extensions and upgraded `_extract_generic_regex_contracts()`, `_extract_file_symbols_for_index()`, and `_extract_file_edges_for_index()` to index classes, methods, Go struct receivers (`func (s *Server) Handle`), C++ out-of-line `Class::Method` definitions, Smali bytecode (`.class`, `.method`, `invoke-*`), unprefixed TypeScript/Dart/Java methods, PowerShell `Verb-Noun` cmdlet calls, and Shell/Bash functions (`12/12` languages verified at `100%` symbol, edge, and live line-shift accuracy; `2,349` symbols and `37,070` edges indexed across `250` APK `.smali` files in `533.81 ms`).
- **Non-coding conversation & general research memory isolation (`skillsdb/memory/project_memory.py`, `skillsdb/search/synonyms.py`, `v4.2.0`)**:
  - Added automatic fallback to `~/.gemini/antigravity/brain/<conversation-id>/.agents/memory.db` when Antigravity is used without an active coding workspace (`workspacePaths: []`), preserving `compaction_archive` checkpoints and `research` subagent findings across compactions while expanding German/English conversational stopwords (`_STOPWORDS_2`) and Turn-1 skill relevance filtering so normal non-coding questions inject `0` false-positive coding skills (`109/109` unit tests passing).

---

## [2.1.4] - 2026-11-07

### Fixed
- **Checkpoint-synchronized post-compaction memory re-hydration (`skillsdb/memory/project_memory.py`, `v4.1.4`)**:
  - Audited against the 4,346-step benchmark transcript (`ab05cd72-aa2b-4ffd-b0a7-dfce798a8295`, 31 `CHECKPOINT` compactions) and fixed `analyze_conversation_epochs_and_scope()` to detect `"type": "CHECKPOINT"` steps in `transcript.jsonl` (in addition to `<CONTEXT_SUMMARY>`) and track `checkpoint_count` and `last_checkpoint_step`.
  - Updated `handle_pre_invocation_hook()` to re-hydrate `=== PROJECT MEMORY CONTEXT (Auto-Hydrated Post-Compaction from .agents/memory.db) ===` strictly once immediately after a `CHECKPOINT` compaction event while keeping all uncompacted Turn-2+ turns at `0` injected tokens (`{"injectSteps": []}`). This guarantees 100% `.agents/memory.db` retention across compactions while eliminating the `78%` redundant mid-window re-injections observed in the benchmark.
- **Multi-language symbol indexing for non-Python files (`skillsdb/memory/project_memory.py`, `v4.1.4`)**:
  - Fixed `_extract_file_symbols_for_index()` to invoke `_extract_generic_regex_contracts()` directly on non-Python source files (`.ps1`, `.ts`, `.js`, `.cs`, `.go`, `.rs`, `.kt`, `.java`), preventing `TypeError: 'WindowsPath' object is not iterable` during `skillsdb mem-index` / `skillsdb locate`.

---

## [2.1.3] - 2026-11-07

### Added
- **Incremental symbol and line-offset locator in `.agents/memory.db` (`skillsdb locate`, `skillsdb mem-index`, `skillsdb/memory/project_memory.py`, `v4.1.3`)**:
  - Added the `symbol_index` SQLite table and `mem_index_symbols()` / `mem_locate_symbol()` to index classes, functions, and `Class.method` definitions with exact `[start_line, end_line]` boundaries (including decorator spans) and `file_mtime` tracking.
  - Added `skillsdb locate <symbol>` (alias `mem-locate`) so the agent can resolve exact `read_file(Path, StartLine, EndLine)` line ranges in `< 5 ms` without burning exploratory grep or `ast-map` turns.
- **Single-process batch memory CLI (`skillsdb mem-batch`, `skillsdb/memory/project_memory.py`, `v4.1.3`)**:
  - Added `mem_batch()` and `skillsdb mem-batch` (alias `batch`) to combine multiple `--fact`, `--decision`, `--task-add`, `--task-in-progress`, `--task-done`, `--task-delete`, `--snapshot`, `--locate`, and `--context` operations into a single Python process startup (`< 30 ms`), preventing multi-command PowerShell chains from exceeding `WaitMsBeforeAsync` and triggering extra background wakeup turns.

### Changed
- **Condensed `<user_rules>` footprint in `plugin/rules/AGENTS.md` (`-54%` per-turn rule tokens, `v4.1.3`)**:
  - Removed the obsolete Section 7 background Agent Control Plane instructions and condensed `plugin/rules/AGENTS.md` from `8,370` bytes (`~2,092` tokens) to `4,020` bytes (`~1,005` tokens), saving `~1,087` static system-prompt tokens on every single turn while preserving 100% of formatting, German orthography, SemVer/Keep a Changelog, SOC IT audit, and pure-Pull SkillsDB invariants.

---

## [2.1.2] - 2026-11-07

### Changed
- **Engineering apology and post-mortem on the background Agent Control Plane (`v1.9.x` - `v2.0.9` -> `v2.1.2`)**:
  - **Sincere apology for abnormal token and background hook consumption**: We owe a direct and sincere apology for the abnormal token burn, extra intermediate turns, and hook latency introduced by the experimental Agent Control Plane in `v1.9.x` through `v2.0.9`. Significant time and tokens were invested into an automated background steering layer that ultimately produced the exact opposite of optimization: higher load and turn multiplication instead of savings.
  - **What the original design idea was (the intent)**: The goal was to build a proactive background safety net around the LLM via `PreInvocation` and `PreToolUse` hooks that would automatically compute 5-hour multi-pool quota routing (`Pool 1A/1B/1C`), inject dynamic Turn-2+ Drift-Guard reminders and mid-session database re-hydrations, and enforce an `AST-First & Read-Before-Edit` gate on `edit_file`.
  - **Why it failed in practice (root cause)**:
    1. **Architectural contradiction ("Push" vs. "Pull")**: SkillsDB saves `98.1%` of static prompt bloat because it is a strict **Pull** architecture (`customizations.db` and `.agents/memory.db` queried on demand). The Agent Control Plane inverted this back into a continuous background **Push** engine that competed with the host IDE's native agent loop.
    2. **Subprocess cold-starts and transcript regex scanning**: Intercepting `read_file`, `view_file`, `run_command`, `invoke_subagent`, and `manage_task` in `PreToolUse` spawned a Python subprocess on almost every tool call and scanned multi-megabyte `transcript.jsonl` files with regexes (compounded by catastrophic backtracking in `cmd_arg_re`).
    3. **Prompt tail mutation and `EPHEMERAL_MESSAGE` overhead**: Dynamic Turn-2+ `PreInvocation` injections (`[SKILLSDB MULTI-POOL QUOTA ROUTER]`, Drift-Guard warnings, and mid-session DB re-hydrations) injected extra tokens and disturbed prompt caching.
    4. **Turn multiplication misreported as savings**: Blocking `edit_file` calls when an earlier `read_file` or `ast-map` had scrolled outside the 180-line transcript tail forced the LLM into 2-3 extra corrective turns per edit, while the legacy `skillsdb stats` calculation naively multiplied `model_turns * 19,900` and misreported turn inflation as higher savings.
  - **Measured empirical recovery (`v2.1.0` - `v2.1.2`)**: Stripping the background Agent Control Plane back to the pure SkillsDB core reduced real uncached input token consumption by **`-50.9%`** (`10,283 -> 5,046` uncached input tokens/turn), dropped Turn-2+ hook injection overhead from **`87 -> 0` tokens/turn**, and reduced background-task wakeup rates from **`8% -> 0%`**.
- **Hardened empirical change-impact and overhead scan (`skillsdb/memory/project_memory.py`, `v4.1.2`)**:
  - Upgraded `calculate_transcript_savings()` and `stats()` to extract real empirical LLM token and control-plane telemetry directly from `transcript.jsonl` (`user_turns`, `turns_per_user`, `uncached_input_tokens`, `cache_read_tokens`, `kv_cache_hit_pct`, `cache_busts`, `ephemeral_injections`, `ephemeral_tokens`, `backgrounded_tasks`, `net_tokens_saved`, and before/after window delta comparisons).
  - Hardened `analyze_conversation_epochs_and_scope()` and `calculate_transcript_savings()` to inspect JSON line headers (`line[:320]` / `line[:600]`) rather than truncating transcripts at `64 KB` or running `json.loads()` on multi-kilobyte `GENERIC` tool outputs.

---

## [2.1.1] - 2026-11-07

### Fixed
- **Compact Turn-1 section header alignment (`skillsdb/memory/project_memory.py`, `v4.1.1`)**: Included `guardrail_lines[17]` (`4. TURN-ECONOMY, PARALLEL BATCHING & DIAGNOSTIC PROTOCOL (MANDATORY):`) in the compact Turn-1 invariant pointer so all 102/102 unit tests pass while preserving the `-550` token Turn-1 footprint reduction.

---

## [2.1.0] - 2026-11-07

### Changed
- **Back to the roots: pure zero-overhead SkillsDB and `.agents/memory.db` (`plugin/hooks.json`, `skillsdb/memory/project_memory.py`, `skillsdb/core/guardrails.py`, `v4.1.0`)**:
  - Narrowed the `PreToolUse` matcher in `plugin/hooks.json` exclusively to file-writing tools (`write_file|edit_file|write_to_file|replace_file_content|multi_replace_file_content`) so `read_file`, `view_file`, `run_command`, `invoke_subagent`, and `manage_task` execute with zero Python subprocess cold-start latency.
  - Removed automatic `compute_routing_decision()` 5-hour transcript scanning, `[SKILLSDB MULTI-POOL QUOTA ROUTER]` prompt injection, and mid-session Turn-2+ Drift-Guard / DB re-hydration spam from `handle_pre_invocation_hook()`, ensuring Turn 2+ is 100% silent (`{"injectSteps": []}`).
  - Added bounded head/tail seeking (`8 KB + 64 KB`) for transcripts exceeding `256 KB` in `analyze_conversation_epochs_and_scope()` and removed the catastrophic backtracking regex in `cmd_arg_re` (`skillsdb/core/guardrails.py`).

---

## [2.0.9] - 2026-11-05

### Fixed
- **Zero-Mutation `merge_upstream_database()` in `merger.py` (`skillsdb/updater/merger.py`)**: Extended the temporary `.upstream.<pid>.tmp` copy-before-attach pattern from `deploy.py` to `merge_upstream_database()` in `skillsdb/updater/merger.py` (with deterministic `.tmp` / `-wal` / `-shm` cleanup in `finally:`), ensuring that `skillsdb update` and unit tests calling `merge_upstream_database(target_db, repo_db)` never touch the SQLite WAL/header metadata of `database/customizations.db` in the Git working tree.

---

## [2.0.8] - 2026-11-05

### Added
- **Claude (`Pool 1C`) cross-pool anchor metrics in `compute_routing_decision()` (`skillsdb/core/quota_router.py`, `v4.0.8`)**:
  - Added `pool_1c_anchor` (`turns_5h`, `weighted_units_5h`, `coding_share_pct`, `target_share_pct`, `recommend_failover`) to `compute_routing_decision()` so `Pool 1C` (`Claude Opus 5.5` / `Claude Sonnet 5.5`) is tracked as a quantitative cross-pool coding anchor alongside `Pool 1A` (`Gemini 4 Argon`) and `Pool 1B` (`Gemini 3.8 Flash`).
- **Complete `delete` and `clear` actions in `skillsdb_task_board` MCP tool (`skillsdb/platform/mcp_server.py`, `v4.0.8`)**:
  - Added `action="delete"` and `action="clear"` (with optional `all=True`) to `skillsdb_task_board`, plus strict validation for missing `title` on `add`, `#`-prefixed or non-integer `task_id` on `update`/`delete`, and unknown action names.

### Changed
- **Multi-line function signature and `/* ... */` block-comment support in `ast-map` (`skillsdb/core/fim.py`, `v4.0.8`)**:
  - Updated `_strip_strings_and_comments_for_braces()` and `_find_block_end_line()` to track multi-line `/* ... */` block comments across lines so stray braces inside comments never skew `[Lstart-Lend]` block boundaries.
  - Updated `_extract_generic_regex_contracts()` to join multi-line parameter lists (`(` across up to 8 continuation lines) before matching function/method signatures in TypeScript/JavaScript, Rust, Go, C#, Java, Kotlin, and Dart.
- **Multi-deliverable release asset upload in `_attach_release_binary_assets()` (`skillsdb/memory/gitea_releases.py`, `v4.0.8`)**:
  - Updated `_attach_release_binary_assets()` to iterate over all distinct deliverable artifacts discovered for a release (e.g. `.apk` + `.aab`, `.exe` + `.msi`, or `.whl` + `.tar.gz`) instead of uploading only `candidates[0]`.

### Fixed
- **Zero-mutation upstream database attachment in `safe_deploy_database()` (`deploy.py`, `v4.0.8`)**:
  - Fixed `safe_deploy_database()` dirtying `database/customizations.db` in the Git working tree by copying `source_db_file` to a temporary file (`target_db_file.with_suffix(".upstream.<pid>.tmp")`) before executing `ATTACH DATABASE ? AS upstream;` and cleaning it up in `finally:`.
- **Self-inflicted `[SEMVER IMMUTABILITY ERROR]` in `publish_gitea_release()` and `Path(".")` repo name resolution (`skillsdb/memory/gitea_sync.py`, `skillsdb/memory/gitea_releases.py`, `skillsdb/cli.py`, `skillsdb/platform/mcp_server.py`, `v4.0.8`)**:
  - Fixed `publish_gitea_release()` so `sync_first = False` is always enforced whenever `existing_tag_sha == head_sha`, preventing an auto-generated `chore(release)` commit from advancing `HEAD` past an already tagged release commit.
  - Normalized `project_root` via `Path(os.path.abspath(str(project_root)))` across `gitea_sync.py`, `gitea_releases.py`, `cli.py`, and `mcp_server.py` so passing `--project .` (`Path(".").name == ""`) always resolves the actual folder name.
  - Added consistent `urllib.parse.quote(..., safe="")` encoding for `owner` and `repo_name` across all Gitea REST API endpoints in `gitea_service.py`, `gitea_releases.py`, and `gitea_sync.py`.
- **Cross-workstation snapshot retention in `mem_prune()`, status aliases in `handoff`, custom drive discovery in `windows_utf8.py`, and LAN host credential scrubbing (`skillsdb/memory/project_memory.py`, `skillsdb/memory/handoff.py`, `skillsdb/cli.py`, `skillsdb/platform/windows_utf8.py`, `skillsdb/memory/gitea_config.py`, `v4.0.8`)**:
  - Updated `mem_prune()` so snapshots from remote workstations (`conversation_id` not in local `~/.gemini/antigravity/brain/`) are retained until `age_days > max_age_days` or `max_snapshots` is exceeded.
  - Wired `_HANDOFF_STATUS_ALIASES` (`in_progress`, `active`, `done`, `cancelled`, `canceled`, `failed`) into `list_handoffs()`, `update_handoff()` (via `WriterQueue`), and `cli.py` `argparse` choices.
  - Updated `get_antigravity_paths()` in `windows_utf8.py` to inspect `ANTIGRAVITY_AGENTAPI_EXE` parent directories, and updated `_scrub_workspace_git_config_credentials()` in `gitea_config.py` to scrub embedded tokens from configured LAN/hostname Gitea URLs.

## [2.0.7] - 2026-11-05

### Added
- **CLI & Python task deletion and handoff lifecycle shortcuts (`skillsdb/memory/project_memory.py`, `skillsdb/memory/handoff.py`, `skillsdb/cli.py`, `v4.0.7`)**:
  - Added `mem_task_delete(conn, task_id, db_path=None)` and CLI subcommand `skillsdb mem-task-delete <id>` (alias `mem-delete-task`) for surgical task removal.
  - Added `accept_handoff(handoff_id, project_root=None)` and `complete_handoff(handoff_id, project_root=None)` plus CLI subcommands `skillsdb handoff accept <id>` and `skillsdb handoff complete <id>`.
  - Exported `guardrail_pre_tool_hook` alias in `skillsdb/core/guardrails.py` and `_extract_description` alias in `skillsdb/updater/merger.py`.

### Changed
- **Upgraded `WaitMsBeforeAsync` synchronous threshold across all non-daemon commands (`skillsdb/core/guardrails.py`, `v4.0.7`)**:
  - Relaxed the `PreToolUse` `WaitMsBeforeAsync` auto-upgrade range from `1000 <= wait_int < 10000` to `0 < wait_int < 10000` whenever `IsDaemon` is `False`, preventing `WaitMsBeforeAsync=500` on normal commands from prematurely backgrounding 1-second scripts into multi-turn wait loops.
- **2-character technical token expansion in `expand_query_with_synonyms()` and pure JSON output in `get_cluster()` (`skillsdb/search/synonyms.py`, `skillsdb/core/concurrency.py`, `v4.0.7`)**:
  - Lowered the tokenizer threshold in `expand_query_with_synonyms()` from `{3,}` to `{2,}` (matching `build_safe_fts_query()`) and added 2-character domain synonyms (`"ui"`, `"db"`, `"ci"`, `"cd"`, `"ai"`).
  - Suppressed the human-readable stdout banner in `get_cluster()` when `as_json=True` and preserved input skill ordering in `get_skills_parallel()`.

### Fixed
- **Windows `[WinError 32]` SQLite connection release and case-normalized queue keys in `WriterQueue` (`skillsdb/memory/writer_queue.py`, `v4.0.7`)**:
  - Fixed a Windows file-locking race condition where `WriterQueue._worker()` held an open SQLite connection to `.agents/memory.db` while idle or signaled `done_event` before `conn.close()` completed, causing `PermissionError: [WinError 32]` when temporary workspace directories were cleaned up immediately after `mem_save_fact` or `mem_task_add`. `WriterQueue._worker()` now drains each active batch, checkpoints (`PASSIVE`), closes `conn` in `finally:`, and signals `done_event` only after the file handle is released.
  - Normalized `_QUEUES` keys with `os.path.normcase(os.path.abspath(...))`, added automatic dead-worker recovery in `execute_write()`, and isolated line-level `json.loads()` in `flush_journals()`.
- **Ancestor marker discovery, system-path noise exclusion, and SQL `LIKE` wildcard escaping (`skillsdb/search/fts.py`, `skillsdb/core/concurrency.py`, `v4.0.7`)**:
  - Fixed `detect_cwd_hints()` matching `"data"` inside Windows system paths (`C:\Users\...\AppData\Local\Temp\...`) by excluding system folder names (`appdata`, `local`, `roaming`, `temp`, `users`) while inspecting up to 3 ancestor directories for project markers (`pubspec.yaml`, `dbt_project.yml`, `Dockerfile`, `package.json`, `firebase.json`).
  - Added `_escape_sql_like()` (`ESCAPE '\'`) and `is_active = 1` filtering across `get_skill()`, `get_rule()`, `suggest_skills()`, `search()`, `fetch_single_skill()`, and `search_single_query()`.
- **Path disambiguation, scoped `ast-map` verification, and user-prompt model-hijack prevention (`skillsdb/core/guardrails.py`, `skillsdb/core/detector.py`, `v4.0.7`)**:
  - Fixed `_was_file_inspected_before_edit()` so identical basenames in different directories (`README.md` vs. `plugin/README.md`) are compared by normalized path, `skillsdb ast-map` commands are checked per `CommandLine` segment, `USER_INPUT` lines are ignored, and `write_file` / `write_to_file` on existing `> 250`-line files is also guarded.
  - Updated `detect_model_and_pool()` in `detector.py` to skip `USER_INPUT` lines during transcript header scanning and restricted `parse_antigravity_model_spec()` so external `gemini-2.5-flash` strings are not misclassified as `gemini-3.8-flash`.
- **Multi-language `Class.method` resolution and `@decorator` prefix window inclusion in `slice_fim_symbol()` (`skillsdb/core/fim.py`, `v4.0.7`)**:
  - Added two-stage `ClassName.method_name` resolution to the regex fallback in `slice_fim_symbol()` for non-Python files (`.ts`, `.kt`, `.dart`, `.cs`, `.go`, `.rs`) and syntax-broken Python files, automatically expanded `prefix_lines` when `symbol_start_line < match_line` so leading `@decorator` lines are never clipped, and included `"total_lines"` alongside `"total_file_lines"`.
- **FTS5 ghost cleanup in `mem_prune()`, duplicate-title superseding in `mem_save_decision()`, `[vX.Y.Z]` changelog parsing, and stale WAL cleanup in `sync_database()` (`skillsdb/memory/project_memory.py`, `skillsdb/memory/gitea_releases.py`, `skillsdb/updater/merger.py`, `v4.0.7`)**:
  - Synchronized `project_memory_fts` during `mem_prune()` so pruned snapshots never remain as ghost FTS5 hits in `mem_search()`.
  - Updated `mem_save_decision()` to supersede existing active decisions with the same `title` (`is_active = 0`) and replace their FTS entry instead of creating duplicate active rows.
  - Coerced `changelog_path = Path(changelog_path)` and accepted optional `[vX.Y.Z]` tag prefixes in `parse_keep_a_changelog()`, and unlinked stale `-wal`/`-shm` files before copying in `sync_database(conn, "pull")`.

## [2.0.6] - 2026-11-05

### Added
- **AST-First & Read-Before-Edit `PreToolUse` guardrail for files `> 250` lines (`skillsdb/core/guardrails.py`, `v4.0.6`)**:
  - Added `_was_file_inspected_before_edit()` and wired `handle_pre_tool_guardrail_hook()` to verify that existing files exceeding `250` lines have been inspected via `read_file`, `view_file`, `skillsdb ast-map`, `skillsdb fim slice`, or `skillsdb gemini-digest` in the current uncompacted conversation epoch before allowing `edit_file`, `replace_file_content`, or `multi_replace_file_content` (Rule 5 / Rule 7).
- **CLI `skillsdb context` subparser alias (`skillsdb/cli.py`, `v4.0.6`)**:
  - Added `context` as a direct CLI alias for `skillsdb mem-get-context`, aligning the CLI command with the `skillsdb_context` MCP tool.

### Changed
- **CamelCase and snake_case hook payload support in `handle_pre_tool_guardrail_hook()` (`skillsdb/core/guardrails.py`, `v4.0.6`)**:
  - Added top-level and nested CamelCase/snake_case field resolution (`toolCall` / `tool_call`, `toolName` / `tool_name` / `name`, `toolInput` / `tool_input` / `args` / `arguments`) so `PreToolUse` hooks accept all payload conventions seamlessly.
- **MCP tool parameter alias ergonomics and standard `isError` responses (`skillsdb/platform/mcp_server.py`, `v4.0.6`)**:
  - Added seamless CLI-style parameter aliases across `handle_tool_call()`: `file` and `path` on `skillsdb_fim_slice` (with graceful `FileNotFoundError` / `ValueError` handling and standard MCP `isError: True` responses instead of unhandled `-32603` exceptions); `project` and `cwd` across `skillsdb_gitea_sync`, `skillsdb_gemini_digest`, `skillsdb_ast_map`, `skillsdb_context`, `skillsdb_task_board`, `skillsdb_gemini_handover`, and `skillsdb_gemini_ingest`; `filter` and `query` on `skillsdb_ast_map`; and `id` / `desc` on `skillsdb_task_board` and `skillsdb_agent_template`.
- **Top-level convenience keys in `compute_routing_decision()` and default parameters in `_scan_single_transcript_5h()` (`skillsdb/core/quota_router.py`, `v4.0.6`)**:
  - Made `cutoff_iso`, `cutoff_minute_bucket`, and `settings_re` optional in `_scan_single_transcript_5h()` (supporting both ISO strings and Unix float timestamps, and returning `p1a_turns`, `p1b_turns`, `p1b_weighted`, and `models` summary fields), and added top-level `pool_1a`, `pool_1b`, and `routing` convenience dictionaries to `compute_routing_decision()`.

## [2.0.5] - 2026-11-05

### Added
- **Global Rule 7 (`adaptive-model-concurrency-quota-routing`) persisted in `database/customizations.db` (`v4.0.5`)**:
  - Added the official `adaptive-model-concurrency-quota-routing` rule entry (`category = 'performance'`, `scope = 'global'`) to `database/customizations.db` and rebuilt `rules_fts`, ensuring 100% parity between all 7 global rule sections in `plugin/rules/AGENTS.md` and `skillsdb get-active-rules` / `skillsdb_get_rules`.

### Changed
- **Complete MCP `inputSchema` & parameter pass-through for `skillsdb_gemini_digest`, `skillsdb_ast_map`, and `skillsdb_task_board` (`skillsdb/platform/mcp_server.py`, `v4.0.5`)**:
  - Exposed `pool`, `git_diff`, `save_fact_key`, and `project_path` on `skillsdb_gemini_digest`; `include_private` and `project_path` on `skillsdb_ast_map`; and `description` on `skillsdb_task_board` in `TOOLS_REGISTRY`, and wired all parameters through `handle_tool_call()`.
- **Synchronized `README.md` Section 5 architecture tree and `build.py` version replacement patterns (`README.md`, `plugin/README.md`, `build.py`, `v4.0.5`)**:
  - Updated the ASCII repository tree in `README.md` Section 5 to list the exact 21 modular files in `skillsdb/`, and expanded `sync_project_versions()` in `build.py` to keep inline version badges and capability labels in `README.md` and `plugin/README.md` synchronized automatically on every build.

### Fixed
- **CLI `skillsdb stats --today` UTC date filter wiring (`skillsdb/cli.py`, `v4.0.5`)**:
  - Wired the `--today` flag in `skillsdb stats` (`skillsdb/cli.py:840`) to resolve `datetime.now(timezone.utc).strftime("%Y-%m-%d")` when `--date` is omitted, matching the CLI parser specification.

## [2.0.4] - 2026-11-05

### Added
- **Zero-polling `PreToolUse` guardrail for `manage_task(Action="status")` loops (`skillsdb/core/guardrails.py`, `plugin/hooks.json`, `v4.0.4`)**:
  - Added `manage_task` to the `PreToolUse` matcher in `plugin/hooks.json` and wired `handle_pre_tool_guardrail_hook` to block repeated `manage_task(Action="status")` calls within the same wait cycle (`status_polls >= 1` since the last `USER_INPUT` / `SYSTEM_MESSAGE`), instructing the agent to end its turn and rely on automatic `Reactive Wakeup` (Rule 5).
- **Scratch script path exemption (`_is_scratch_file_path`) in `PreToolUse` guardrail (`skillsdb/core/guardrails.py`, `v4.0.4`)**:
  - Added `_is_scratch_file_path()` so temporary scratch scripts written to `brain/<cid>/scratch/` can reference local user profile paths without triggering false-positive `secret-leak-detection` (`Hardcoded Personal or Corporate Identifier`) blocks.

### Changed
- **Automatic 2-entry rotation for `subagent_finding_step_*` facts in `.agents/memory.db` (`skillsdb/memory/project_memory.py`, `v4.0.4`)**:
  - Updated `_collect_project_memory_lines()` so inserting new `subagent_finding_step_*` facts automatically deprecates older `subagent_finding_step_*` rows beyond the 2 most recent ones, preventing historical subagent findings from crowding out architectural facts in the 12-fact `PreInvocation` context window.
- **Full-path disambiguation and partial-read verification in `_was_file_region_read_in_current_epoch()` (`skillsdb/core/guardrails.py`, `v4.0.4`)**:
  - Updated `_was_file_region_read_in_current_epoch()` to compare normalized full paths (`os.path.normcase(os.path.abspath(...))`) when paths contain directory components (so `README.md` and `plugin/README.md` never collide) and to verify line range coverage (`prev_s <= s_line and prev_e >= e_line`) for files `<= 800` lines.

### Fixed
- **Subagent 5-hour quota pool attribution (10x over-weighting fix) and main-conversation transcript selection (`skillsdb/core/quota_router.py`, `skillsdb/core/detector.py`, `v4.0.4`)**:
  - Fixed `_scan_single_transcript_5h()` where isolated `research` subagent transcripts (which lack `<USER_SETTINGS_CHANGE>` entries) defaulted to `Gemini 4 Argon (High)` (`Pool 1A`, weight `3.5x`) instead of `Gemini 3.8 Flash (Medium)` (`Pool 1B`, weight `0.35x`), inflating 5-hour quota consumption by 10x during multi-subagent research runs.
  - Added `_inspect_subagent_transcript_head()` and updated `measure_session_backpack()` and `detect_model_and_pool()` to skip subagent transcripts when resolving the active user conversation by modification time.
- **Calibration anchor linear decay formula and UTC timestamp boundary comparison (`skillsdb/core/quota_router.py`, `v4.0.4`)**:
  - Fixed `_resolve_active_calibration()` so `delta_units = max(0.0, raw_total_5h_units - base_u)` instead of `raw_total_5h_units - (base_u * decay)`, ensuring calibrated quota load decays linearly over the 5-hour window when zero new turns are executed.
  - Normalized `cutoff_iso` to UTC `'Z'` format and compared `ts_str[:19] < cutoff_iso[:19]` in `_scan_single_transcript_5h()`.
- **Subagent parent-prompt exclusion in `analyze_conversation_epochs_and_scope()` and zero `ResourceWarning` socket cleanup (`skillsdb/memory/project_memory.py`, `skillsdb/memory/gitea_service.py`, `skillsdb/memory/gitea_releases.py`, `skillsdb/memory/gitea_sync.py`, `skillsdb/updater/merger.py`, `tests/test_gitea_sync.py`, `v4.0.4`)**:
  - Guarded `subagent_findings` extraction in `analyze_conversation_epochs_and_scope()` with `if user_steps:` and `s_idx > 2` so a subagent's own Step 0/1 parent prompt (`<SYSTEM_MESSAGE> ... sender=<parent-cid>`) is never saved as `subagent_finding_step_1`.
  - Explicitly closed `urllib.error.HTTPError` sockets and `urlopen` handles across `gitea_service.py`, `gitea_releases.py`, `gitea_sync.py`, `quota_router.py`, `detector.py`, and `merger.py`, and switched `test_gitea_sync.py` to `managed_connection()`, eliminating all Python 3.14 `ResourceWarning` messages.

## [2.0.3] - 2026-11-05

### Fixed
- **Subagent Firewall exemption for isolated `research` / `self` subagents and main-context streak reset (`skillsdb/core/guardrails.py`, `v4.0.3`)**:
  - Fixed a false-positive Subagent Firewall trigger where `handle_pre_tool_guardrail_hook` counted and blocked 5+ exploratory shell search commands (`Get-ChildItem`, `Select-String`, `rg`, `grep`, `python -c`) inside an already isolated `research` subagent conversation because subagent transcripts start with a parent `SYSTEM_MESSAGE` (`sender=<parent-cid>`) rather than `USER_INPUT`.
  - Added `_is_subagent_context(payload, t_path)` using $O(1)$ transcript header inspection (first 8 lines containing `SYSTEM_MESSAGE` with `sender=` and no `USER_INPUT` / `USER_EXPLICIT`) alongside payload metadata (`isSubagent`, `parentConversationId`, `subagentType`, `typeName`, `agentType`, `role`) so isolated subagents are 100% exempt from the Subagent Firewall.
  - Updated the main-context `search_streak` scanner to also reset whenever a subagent reply (`SYSTEM_MESSAGE` with `sender=`) arrives in the parent conversation.
- **Windows-safe atomic deployment when `db_manager.py` is memory-mapped (`deploy.py`, `v4.0.3`)**:
  - Added `safe_copy_file()` in `deploy.py` to prevent `WinError 1224` (`ERROR_USER_MAPPED_FILE`) on Windows when `~/.gemini/database/db_manager.py` is concurrently held open by an active MCP server or hook process.

## [2.0.2] - 2026-11-05

### Fixed
- **SemVer immutability preservation and release tag synchronization after `mem-save-snapshot` (`skillsdb/memory/gitea_sync.py`, `v4.0.2`)**:
  - Fixed a false-positive SemVer immutability collision where calling `skillsdb gitea-release` immediately after `skillsdb mem-save-snapshot` triggered `[SEMVER IMMUTABILITY ERROR]` because post-commit `.agents/memory.db` metadata updates or recent snapshot HEADs caused `sync_workspace` to amend or commit onto an already tagged release.
  - Added a dedicated target-tag check in `publish_gitea_release`: if HEAD already matches the target release tag (`existing_tag_sha == head_sha`) and the working tree is either clean or only contains uncommitted internal `.agents/memory.db` updates (`only_agents_changed`), `sync_first` is automatically bypassed so HEAD is never amended or moved away from the release tag.
  - Added internal `.agents/` commit-diff tolerance: if HEAD has moved ahead of the release tag solely due to internal `.agents/memory.db` episodic memory commits (`only_agents_diff`), `publish_gitea_release` automatically pins `target_commit` to `existing_tag_sha` without error.
  - Added a strict tag check (`git tag --points-at HEAD`) in `save_code_commit` to guarantee that Git commits pointing to existing release tags are never amended via `commit --amend`.

## [2.0.1] - 2026-11-03

### Added
- **Live 5-hour quota calibration anchor and cross-workstation heartbeat synchronization (`skillsdb/core/quota_router.py`, `skillsdb/cli.py`, `v4.0.1`)**:
  - Added `calibrate_quota_load()` and CLI flags `skillsdb quota-status --calibrate-pct <0..100>` and `--reset-calibration` to anchor the 5-hour rolling window to the exact server-side percentage shown in the Antigravity UI or after switching devices, decaying linearly over the remaining 5-hour window while accumulating new turns on top.
  - Added `record_remote_workstation_heartbeat()` and `_merge_and_sync_workstation_heartbeats()` (`runtime_config.quota_workstation_heartbeats`) so developers working across Home and Enterprise workstations (`skillsdb sync` / shared `customizations.db`) automatically merge active 5-hour quota usage across hosts.
  - Added context-size turn weighting (`1.0x..1.35x`) in `_scan_single_transcript_5h()` so massive tool/thinking turns reflect their higher server-side token burn compared to lightweight turns.

### Changed
- **Decorator-aware Python AST contracts, conditional top-level discovery, and multi-language `[Lstart-Lend]` block-end mapping (`skillsdb/core/fim.py`, `v4.0.1`)**:
  - Upgraded `_extract_python_ast_contracts()` and `slice_fim_symbol()` to include `@decorator` lines in `[Lstart-Lend]` boundaries (`_node_start_lineno()`), surface `@staticmethod` / `@classmethod` / `@property` / `@dataclass` tags, traverse top-level `if` / `try` / `with` blocks (`_iter_module_top_nodes()`), and resolve `ClassName.method_name` symbols directly via Python's `ast` module.
  - Upgraded `_extract_generic_regex_contracts()` with a string/comment-safe brace and SQL block-end tracker (`_find_block_end_line()`) that outputs full `[Lstart-Lend]` line spans and groups class/struct/`impl` methods under their enclosing type across TypeScript/JavaScript, PowerShell, Go, Rust, C#, Java, Kotlin, Dart, and SQL (`CREATE [OR ALTER] TABLE/VIEW/PROCEDURE/FUNCTION/TRIGGER`).
- **O(1) binary tail-seek and in-memory stat caching for sub-30ms hook latency (`skillsdb/config.py`, `skillsdb/core/guardrails.py`, `skillsdb/core/quota_router.py`, `v4.0.1`)**:
  - Added `read_jsonl_tail_lines()` in `skillsdb/config.py` using binary `SEEK_END` tail reads instead of full-file iteration.
  - Wired `_was_file_region_read_in_current_epoch()` and the Subagent Firewall streak check in `guardrails.py`, plus `measure_session_backpack()` (`_BACKPACK_CACHE`) and `scan_rolling_5h_antigravity_usage()` (`_TRANSCRIPT_5H_CACHE`) in `quota_router.py`, to use O(1) tail seeks and `(mtime_ns, size)` caching so multi-megabyte transcripts never add latency to `PreToolUse` or `PreInvocation` hooks.

### Fixed
- **Explicit module-level import of `detect_model_and_pool` in `project_memory.py` (`skillsdb/memory/project_memory.py`, `v4.0.1`)**:
  - Imported `detect_model_and_pool` directly in `skillsdb/memory/project_memory.py` so `stats()` resolves active model and pool telemetry identically in both modular `python -m skillsdb` and compiled `db_manager.py` execution modes.

## [2.0.0] - 2026-11-03

### Added
- **Multi-pool Golden-Ratio (`61.8%` / `85.0%`) agent control plane (`skillsdb/core/quota_router.py`, `skillsdb/core/detector.py`, `skillsdb/core/guardrails.py`, `v4.0.0`)**:
  - Promotes the complete `v2.0.0-beta.1..4` architecture to the first stable Major Release **`AntiGraviton v2.0.0` (`SkillsDB v4.0.0`)**.
  - Governs `Ultra` (`Google AI Ultra`, 16 parallel reader threads) and `Pro` (`Google AI Pro`, 8 parallel reader threads) coding subscriptions across all 4 quota pools: Pool 1 (internal Antigravity 5-hour rolling window across `Pool 1A: Gemini 4 Argon`, `Pool 1B: Gemini 3.8 Flash`, `Pool 1C: Claude Opus 5.5 / Sonnet 5.5`, and `Pool 1D: GPT-OSS 120B`), Pool 2 (`@google/gemini-cli` OAuth), Pool 3 (`Google AI Studio` REST API), and Pool 4 (`gemini.google.com` Deep Research clipboard handover bridge).
  - Implements the 13-combination model and reasoning effort catalog (`Fast`, `Medium`, `High` on `Argon`, `Flash 3.8`, `Opus 5.5`, `Sonnet 5.5`, and fixed `Medium` on `GPT-OSS 120B`) with live mid-session `<USER_SETTINGS_CHANGE>` transcript detection.
- **Multi-file AST contract mapper (`skillsdb ast-map`), Context-Backpack detector, and Context-Shield CLI/MCP suite (`skillsdb/core/fim.py`, `skillsdb/core/quota_router.py`, `skillsdb/platform/mcp_server.py`, `v4.0.0`)**:
  - Added `skillsdb ast-map`, `skillsdb quota-status` (alias `quota`), `skillsdb gemini-digest`, `skillsdb gemini-handover`, `skillsdb gemini-ingest`, and full 15-tool `stdio` MCP server coverage (`skillsdb_ast_map`, `skillsdb_quota_status`, `skillsdb_gemini_digest`, `skillsdb_gemini_handover`, `skillsdb_gemini_ingest`, `skillsdb_context`, `skillsdb_task_board`).
  - Added `measure_session_backpack()` to track active uncompacted context weight (`LIGHT`, `MODERATE`, `HEAVY`) and automatically recommend contract-first AST mapping or fresh-context Argon subagent splitting (`TypeName="self", Model="pro"`).

### Changed
- **Strict Argon/Claude coding guarantee and dynamic concurrency scaling (`skillsdb/core/guardrails.py`, `skillsdb/core/concurrency.py`, `plugin/rules/AGENTS.md`, `v4.0.0`)**:
  - Enforces 0-turn `PreToolUse` rewriting of exploratory `TypeName="research"` subagents from `Model="inherit"` to `"flash"` (`Pool 1B`), while guaranteeing that `TypeName="self"` coding subagents are never downgraded to `Flash`.
  - Dynamically scales parallel SQLite WAL reader workers via `resolve_optimal_workers()` (`16` on `Ultra`, `8` on `Pro`, `1` on `Local`/`Offline`).

### Removed
- **Breaking: Removed legacy `Gemini 3.1 Pro` model definitions and non-coding `Lite` / `Free` subscription tiers (`skillsdb/core/detector.py`, `skillsdb/core/quota_router.py`, `plugin/rules/AGENTS.md`, `v4.0.0`)**:
  - Focusing AntiGraviton v2.0.0 exclusively on the 5 active Antigravity models and professional `Ultra` / `Pro` coding subscriptions.

### Fixed
- **Standalone bundler relative-import transformation and end-to-end control plane diagnostics (`build.py`, `skillsdb/updater/merger.py`, `v4.0.0`)**:
  - Updated `build.py` to convert indented function-level relative imports into `pass` when compiling standalone `database/db_manager.py`, and updated `_extract_skillsdb_version_from_release()` in `merger.py` to recognize `SkillsDB v4.x.y` releases.

## [2.0.0-beta.4] - 2026-11-03

### Added
- **End-to-end core and agent control plane integration suite (`tests/test_gitea_sync.py`, `v4.0.0-beta.4`)**:
  - Added `test_v2_0_0_beta_4_full_core_and_control_plane_wiring_e2e` verifying all 15 MCP server tools, all 4 agent templates, dynamic `Ultra (16)` / `Pro (8)` / `Local (1)` concurrency scaling, `PreToolUse` coding subagent protection (`TypeName="self"` never downgraded to `Flash`), AST contract mapping with `project_root` and Kotlin `fun` support, and `doctor()` control plane health checks.
- **Complete 15-tool MCP server and agent template alignment (`skillsdb/platform/mcp_server.py`, `skillsdb/memory/agent_templates.py`, `v4.0.0-beta.4`)**:
  - Added `skillsdb_context`, `skillsdb_task_board`, `skillsdb_gemini_handover`, and `skillsdb_gemini_ingest` to `TOOLS_REGISTRY` and `handle_tool_call()`, aligning all `DEFAULT_TEMPLATES` tool bindings 1:1 with the MCP server.
- **Canonical quota router public API aliases (`skillsdb/core/quota_router.py`, `v4.0.0-beta.4`)**:
  - Exported `evaluate_quota_route`, `prepare_web_handover`, and `ingest_web_handover` alongside `compute_routing_decision`, `create_web_handover`, and `ingest_web_response`.

### Changed
- **Dynamic tier-aware concurrency worker scaling (`skillsdb/core/concurrency.py`, `skillsdb/cli.py`, `skillsdb/search/fts.py`, `v4.0.0-beta.4`)**:
  - Added `resolve_optimal_workers()` (`16` threads on `Ultra`, `8` threads on `Pro`, `1` thread on `Local`/`Offline`) and wired `get_skills_parallel()`, `search_multi_parallel()`, `get_cluster()`, `benchmark_concurrency()`, and `suggest_skills()` to scale automatically with the active subscription and model tier.
- **Enhanced `doctor()`, `stats()`, and `SKILL.md` control plane diagnostics (`skillsdb/updater/merger.py`, `skillsdb/memory/project_memory.py`, `plugin/skills/customizations-db/SKILL.md`, `v4.0.0-beta.4`)**:
  - Expanded `skillsdb doctor` to verify `quota_ledger` health, the 13-model & effort catalog, and `invoke_subagent` `PreToolUse` hook presence; updated `skillsdb stats` to report active `ULTRA` and `PRO` profile details; updated `SKILL.md` Section 7 with all v4.0.0 CLI commands.

### Fixed
- **Strict Argon/Claude coding subagent guarantee in `PreToolUse` hook (`skillsdb/core/guardrails.py`, `v4.0.0-beta.4`)**:
  - Hardened `handle_pre_tool_guardrail_hook()` so `invoke_subagent(TypeName="self")` is never classified as exploratory even if `Role` contains `"research"` or `"auditor"`, and automatically upgrades explicit `Model="flash"` / `"flash_lite"` on `TypeName="self"` to `"inherit"` (`Gemini 4 Argon`) or `"pro"` (`Pool 1C`).
- **Explicit model effort preservation and Stage 4 Ultra baseline (`skillsdb/core/detector.py`, `v4.0.0-beta.4`)**:
  - Fixed `parse_antigravity_model_spec()` so explicit `(Fast)`, `(Medium)`, or `(High)` tags in model selection strings take precedence over fallback `effort_override`, and fixed Stage 4 fallback in `detect_model_and_pool()` to preserve `TIER_ULTRA` on `Ultra` accounts and display `8 parallel threads` in `get_profile()` on `Pro` accounts.
- **Context-backpack session isolation, relative `ast-map` path resolution, and multi-file digest disambiguation (`skillsdb/core/quota_router.py`, `skillsdb/core/fim.py`, `v4.0.0-beta.4`)**:
  - Fixed `measure_session_backpack()` so a newly started `cid` without a transcript file returns `LIGHT (0 steps)` instead of reading an older conversation's transcript, and removed the 5-hour timestamp skip so uncompacted steps from earlier in a long session remain counted in the active context window.
  - Fixed `execute_ast_map()` and `extract_ast_contract_map()` to resolve relative paths and globs against `project_root`, added Kotlin `fun` extraction, and disambiguated relative file paths in `execute_gemini_digest()` when multiple files share the same basename.

## [2.0.0-beta.3] - 2026-11-03

### Added
- **Multi-file AST contract and line-offset mapper (`skillsdb/core/fim.py`, `skillsdb/core/quota_router.py`, `skillsdb/cli.py`, `skillsdb/platform/mcp_server.py`, `v4.0.0-beta.3`)**:
  - Added `extract_ast_contract_map()`, `format_ast_contract_map()`, `execute_ast_map()`, CLI command `skillsdb ast-map <files> [--query "<filter>"] [--project "<dir>"]`, and MCP tool `skillsdb_ast_map`.
  - Extracts 100% deterministic class, method, and function signatures with exact `[Lstart-Lend]` line boundaries and type contracts across Python, PowerShell, TypeScript/JavaScript, Go, Rust, C#/Java/Kotlin, and SQL at zero LLM API calls (~93-95% token compression in ~70ms), persisting discovered line offsets into `.agents/memory.db` (`ast_contract_<file>`) and recording token savings in `quota_ledger` (`local_ast_fim`).
- **Context-backpack detector (`measure_session_backpack()` in `skillsdb/core/quota_router.py`, `v4.0.0-beta.3`)**:
  - Added post-compaction active context window weight measurement (`LIGHT < 20k tokens`, `MODERATE 20k-45k tokens`, `HEAVY >= 45k tokens`) scoped to the active 5-hour rolling window and resetting on `<CONTEXT_SUMMARY>` compaction boundaries.
  - Integrated backpack status into `skillsdb quota-status` and the `PreInvocation` routing directive so `HEAVY` context backpacks automatically trigger surgical `[Lstart-Lend]` reading and `/compact` milestone guidance.
- **SemVer 2.0.0 pre-release (`-beta.N`) publishing support (`skillsdb/memory/gitea_releases.py`, `skillsdb/memory/gitea_sync.py`, `build.py`, `v4.0.0-beta.3`)**:
  - Updated `sync_changelog_releases()` and `publish_gitea_release()` to automatically detect SemVer pre-release versions (`-` in version string), set `"prerelease": True` and `"target_commitish"` to the active branch (`beta`) on Gitea, and pass `--prerelease` to `gh release create` / `gh release edit` on GitHub.

## [2.0.0-beta.2] - 2026-11-03

### Changed
- **Golden-Ratio (`61.8%` / `85.0%`) `Ultra / Pro`-only coding control plane (`skillsdb/core/quota_router.py`, `skillsdb/core/detector.py`, `skillsdb/core/guardrails.py`, `plugin/rules/AGENTS.md`, `v4.0.0-beta.2`)**:
  - Restricted subscription quota governance strictly to professional coding tiers (`ultra`: Google AI Ultra, and `pro`: Google AI Pro), normalizing any legacy `lite` input to `pro`.
  - Calibrated Golden-Ratio Pool 1A 5-hour rolling window thresholds (`0-61.8%` optimal zone on `Argon High`, `61.8%-85.0%` Golden-Ratio step-down to `Argon Medium` saving `-37%` pool burn at 100% Argon code quality, and `> 85.0%` failover to `Pool 1C: Claude Opus 5.5 / Sonnet 5.5`).
  - Enforced the **Strict Argon/Claude Coding Guarantee** in `handle_pre_tool_guardrail_hook()`: `TypeName="self"` coding subagents are never downgraded to `Gemini 3.8 Flash`; only read-only `TypeName="research"` subagents are rewritten to `Model="flash"` (`Pool 1B`).
- **Live mid-session model/effort switch detection (`skillsdb/memory/project_memory.py`, `v4.0.0-beta.2`)**:
  - Enhanced `handle_pre_invocation_hook()` to compare the live transcript model and effort signature against `.agents/memory.db` (`last_active_model_sig`) on every turn and immediately inject the updated `format_hook_routing_banner()` whenever the user switches models or effort levels mid-session (as well as on every 50-step drift-guard rehydration).

### Removed
- **Breaking: Dropped non-coding `Lite` / `Free` subscription tiers (`skillsdb/core/detector.py`, `skillsdb/core/quota_router.py`, `v4.0.0-beta.2`)**:
  - Removed `lite` / `free` subscription tier budgets and routing rules so AntiGraviton v2.0.0 focuses exclusively on `Ultra` and `Pro` coding subscriptions.

## [2.0.0-beta.1] - 2026-11-03

### Added
- **Multi-pool quota router and 4-pool governance engine (`skillsdb/core/quota_router.py`, `v4.0.0-beta.1`)**:
  - Implemented unified quota routing across all 4 quota pools: Pool 1 (internal Antigravity 5-hour rolling window across Pool 1A `Gemini 4 Argon`, Pool 1B `Gemini 3.8 Flash`, Pool 1C `Claude Opus 5.5` / `Claude Sonnet 5.5`, and Pool 1D `GPT-OSS 120B`), Pool 2 (standalone `@google/gemini-cli` 24-hour OAuth developer quota), Pool 3 (`Google AI Studio` low-latency REST API), and Pool 4 (`gemini.google.com` Deep Research clipboard handover bridge).
- **13-combination model and reasoning effort catalog (`skillsdb/core/detector.py`, `v4.0.0-beta.1`)**:
  - Added `parse_antigravity_model_spec()` and `detect_model_and_pool()` supporting `Fast`, `Medium`, and `High` reasoning effort levels (and effort-weighted quota multipliers) on `Gemini 4 Argon`, `Gemini 3.8 Flash`, `Claude Opus 5.5`, and `Claude Sonnet 5.5`, while pinning `GPT-OSS 120B` to `Medium`.
- **Zero-turn `PreToolUse` subagent model overwrite (`plugin/hooks.json`, `skillsdb/core/guardrails.py`, `v4.0.0-beta.1`)**:
  - Added `invoke_subagent` to the `PreToolUse` matcher so `handle_pre_tool_guardrail_hook()` automatically rewrites `Model="inherit"` to `"flash"` for exploratory `research` subagents at zero extra turns.
- **Context-shield CLI & MCP tools (`skillsdb/cli.py`, `skillsdb/platform/mcp_server.py`, `skillsdb/core/db.py`, `v4.0.0-beta.1`)**:
  - Added `skillsdb quota-status` (alias `quota`), `skillsdb gemini-digest`, `skillsdb gemini-handover`, `skillsdb gemini-ingest`, and the `quota_ledger` SQLite table tracking offloaded requests and saved Antigravity tokens/turns.

### Changed
- **Updated Rule 7 to Multi-Pool Quota Routing (`plugin/rules/AGENTS.md`, `skillsdb/core/detector.py`, `v4.0.0-beta.1`)**:
  - Upgraded Rule 7 to govern the 5 active Antigravity models and their effort levels (`Fast`, `Medium`, `High`).

### Removed
- **Breaking: Removed legacy `Gemini 3.1 Pro` model definitions (`skillsdb/core/detector.py`, `plugin/rules/AGENTS.md`, `v4.0.0-beta.1`)**:
  - Removed obsolete `Gemini 3.1 Pro` identifiers and mappings from the active model catalog.

### Fixed
- **Enterprise Active Directory transcript path resolution in `detector.py` (`v4.0.0-beta.1`)**:
  - Replaced `Path.home() / ".gemini"` with `GEMINI_DIR` (`resolve_user_home()`) in `detect_model_and_pool()` so live transcript model detection resolves `%USERPROFILE%\.gemini` reliably when `HOME=Q:\`.

## [1.5.14] - 2026-11-02

### Changed
- **Hierarchical IT Audit Directory Standard & 100% Live Runtime Identity Resolution (`plugin/rules/AGENTS.md`, `database/customizations.db`, `skillsdb/core/guardrails.py`, `v3.8.14`)**:
  - Updated Rule 4 (`Admin elevation and 100% transparent IT audit standard`) and `customizations.db` from `C:\IT_ADMIN_<USERNAME>\<Date>_<Target>_<Task>\` to the structured hierarchy `C:\IT_ADMIN_LOGS\<USERNAME>\<TASK>\<TARGET>\<Date>\<hh_mm_ss>\` (and task folder `\IT_ADMIN_LOGS\<USERNAME>\`) with persistent `AuditLog_<Target>_<Date>_<hh_mm_ss>.log` files.
  - Replaced all hardcoded personal usernames, workstation hostnames, and corporate Active Directory domain names across `AGENTS.md`, `customizations.db`, `README.md`, `LICENSE`, and unit tests with 100% live runtime environment queries (`$env:USERNAME`, `[System.Net.Dns]::GetHostEntry($env:COMPUTERNAME).HostName`, `[System.Security.Principal.WindowsIdentity]::GetCurrent().Name`, `$cred.UserName`) and neutral test fixtures.
  - Added a `Hardcoded Personal or Corporate Identifier` pattern to `SECRET_PATTERNS` in `skillsdb/core/guardrails.py` so `skillsdb guardrail check` and the `PreToolUse` hook automatically block accidental commits of personal or corporate domain identifiers.

## [1.5.13] - 2026-11-02

### Added
- **Explicit `--force-all` Release Re-Sync Flag (`skillsdb/cli.py`, `v3.8.13`)**:
  - Added `--force-all` to `skillsdb gitea-sync` (`skillsdb gitea-sync --releases --force-all [--github]`) for scenarios where all historical Keep a Changelog releases should be overwritten in place, while making `--releases` perform fast incremental release synchronization by default.

### Changed
- **Single-Call Batch Release Synchronization for Gitea & GitHub (`skillsdb/memory/gitea_releases.py`, `skillsdb/cli.py`, `v3.8.13`)**:
  - Optimized `sync_changelog_releases()` so both Gitea (`GET /api/v1/repos/{owner}/{repo}/releases?limit=100`) and GitHub (`gh api repos/{slug}/releases?per_page=100`) fetch all existing releases in a single API call and skip unchanged historical releases in O(1) time instead of issuing 40+ sequential `PATCH` requests and 80+ `gh release view` / `gh release edit` subprocesses on every release sync (~45x speedup, reducing `--releases --github` runtime from ~3.5 minutes to ~4 seconds).
  - Preserved automatic backfill of any missing historical releases and automatic `PATCH` / `gh release edit` updates whenever the latest release's `CHANGELOG.md` notes are modified.

## [1.5.12] - 2026-11-02

### Fixed
- **Subagent Firewall Exemption for Targeted Single-Host & Remote WMI/CIM Diagnostic Commands (`skillsdb/core/guardrails.py`, `plugin/rules/AGENTS.md`, `v3.8.12`)**:
  - Exempted targeted remote and local system administration, WMI/CIM, PowerShell remoting, DPAPI credentialed, SMB/UNC share, and Windows service/registry/EventLog/MECM commands (`Invoke-Command`, `Get-CimInstance`, `Set-CimInstance`, `Remove-CimInstance`, `Invoke-CimMethod`, `Get-WmiObject`, `Enter-PSSession`, `-ComputerName`, `-CimSession`, `Import-Clixml`, `PSCredential`, `New-PSDrive`, UNC `\\host\c$`, `sc.exe`, `reg.exe`, `Get-Service`, `Get-WinEvent`, `root\ccm`) from `_is_exploratory_search_cmd()`. Because `research` subagents execute in a read-only local sandbox without `run_command` or network/DPAPI access, single-host WMI and remote PowerShell diagnostics (even when piping to `Get-ChildItem` or `Select-String`) now run directly in the main context without triggering a false-positive `[RULE 5 SUBAGENT FIREWALL]` denial on the 5th command.
  - Hardened the `search_streak` counter in `handle_pre_tool_guardrail_hook()` so it resets when a non-exploratory `run_command` turn or a `<CONTEXT_SUMMARY>` compaction boundary occurs, ensuring only truly consecutive local codebase search turns count toward the 4-turn threshold.
  - Replaced remaining `Path.home()` transcript/brain directory lookups in `skillsdb/core/guardrails.py` and `skillsdb/memory/project_memory.py` with `resolve_user_home()`.

## [1.5.11] - 2026-11-02

### Added
- **Dual-Scenario Home & Enterprise Active Directory Environment Support (`skillsdb/config.py`, `skillsdb/memory/gitea_config.py`, `deploy.py`, `install_antigraviton.py`, `install-antigraviton.ps1`, `v3.8.11`)**:
  - Added `resolve_user_home()` across `skillsdb/config.py`, `deploy.py`, and `install_antigraviton.py` so Windows installations always resolve `.gemini` via `%USERPROFILE%` even when Git for Windows or MSYS2 sets `HOME` to a mapped corporate network drive (`HOMEDRIVE` + `HOMEPATH`, e.g. `Q:\`).
  - Added `_normalize_workspace_path()` in `skillsdb/memory/gitea_config.py` to automatically map Enterprise AD `HOMESHARE` UNC paths (`\\server\home$\user\...`) back to their mounted `HOMEDRIVE` (`Q:\...`) during workspace learning and discovery while keeping local drive paths (`C:\...`, `D:\...`) untouched on Home workstations.
  - Added `--standalone` (Python) and `-Standalone` (PowerShell) installer switches for explicit Standalone SkillsDB-Only Mode configuration, while preserving existing active Gitea tokens (`gitea-config --enable`) when `--skip-gitea-service` / `-SkipGiteaService` is used for fast SkillsDB upgrades on machines that already run Gitea.
  - Added `detect_windows_gitea_service_path()` (`sc.exe qc gitea`) for ~15ms Windows service binary path detection without WMI cold-start timeouts across both Home (`D:\Gitea`) and Enterprise (`C:\Gitea`) installations.

### Fixed
- **Git Credential Helper & POSIX Wrapper Path Resolution on Enterprise Domain Workstations (`deploy.py`, `skillsdb/memory/gitea_config.py`, `v3.8.11`)**:
  - Updated the deployed `~/.gemini/antigravity/bin/skillsdb` POSIX shell wrapper to resolve `_AGY_HOME` from `$USERPROFILE` (converted to forward slashes) before falling back to `$HOME`, and to prefer `python` over `python3` in `PATH`, preventing `can't open file 'Q:\\.gemini\\database\\db_manager.py'` errors when Git for Windows invokes `!skillsdb gitea-credential-helper` via `/bin/sh`.
  - Extended `_is_excluded_workspace_path()` to exclude Enterprise AD `HOMESHARE` and `HOMEDRIVE+HOMEPATH` root directories alongside `%USERPROFILE%`.

## [1.5.10] - 2026-11-01

### Changed
- **Strict Dyadic Pronoun Guard ('Du' & 'Wir' Only - Never 'Ihr'/'Euer') (`plugin/rules/AGENTS.md`, `skillsdb/memory/project_memory.py`, `database/customizations.db`, `v3.8.10`)**:
  - Explicitly anchored in `AGENTS.md`, the Turn-1 invariant guardrail, and `customizations.db` (`communication-style`) that all conversational references to our collaboration must use singular `Du`/`Dein` (for the user) or first-person plural `wir`/`unser` (for the user and the agent together), strictly prohibiting second-person plural forms (`Ihr`, `Euer`, `Eurer`, `Euch`).

## [1.5.9] - 2026-11-01

### Fixed
- **Drive-Root Workspace Exclusion & Multi-Escaped JSONL Path Normalization (`skillsdb/memory/gitea_config.py`, `skillsdb/memory/project_memory.py`, `v3.8.9`)**:
  - Excluded bare filesystem/drive roots (`len(abs_root.parts) <= 1`, e.g. `C:\`, `D:\`, `/`) in `_is_excluded_workspace_path()` so non-existent path references on a secondary drive never fall back to the parent drive root during transcript workspace inference.
  - Normalized multi-escaped JSONL backslashes (`\\\\` -> `\`) in `_record_candidate_path()` so paths inside nested JSON tool payloads resolve to the active project workspace.

## [1.5.8] - 2026-11-01

### Added
- **First-Class Standalone SkillsDB-Only Mode (`is_gitea_configured()`, `skillsdb gitea-config --disable/--enable`, `v3.8.8`)**:
  - Added `is_gitea_configured()` in `skillsdb/memory/gitea_sync.py` and `--enable` / `--disable` flags to `skillsdb gitea-config` (`skillsdb/memory/gitea_config.py`, `skillsdb/cli.py`), allowing SkillsDB to cleanly distinguish between an active Gitea installation and standalone SkillsDB-only operation (`-SkipGiteaService`, `--skip-gitea-service`, or standalone `python deploy.py`).
  - Added `[COMPACT MILESTONE TIP]` directly to `skillsdb mem-save-snapshot` (`skillsdb/memory/project_memory.py`) so standalone SkillsDB-only users receive the same milestone `/compact` guidance as Gitea users.

### Fixed
- **Zero False Gitea Warnings & Zero HTTP Timeouts in SkillsDB-Only Mode (`skillsdb/memory/gitea_sync.py`, `skillsdb/memory/project_memory.py`, `skillsdb/updater/merger.py`, `v3.8.8`)**:
  - Guarded `get_workspace_git_telemetry()` with `is_gitea_configured(project_root)` so standalone SkillsDB-only users never experience a 1.5s HTTP timeout on `http://127.0.0.1:3000` or a false `[CRITICAL GITEA ALERT]` on every `PreInvocation` hook call.
  - Updated `mem_save_snapshot()`, `sync_workspace()`, `sync_changelog_releases()`, `gitea_status_summary()`, `doctor()`, `install-antigraviton.ps1`, and `install_antigraviton.py` so SkillsDB-only installations commit cleanly to local Git (`(saved locally)`), create local SemVer tags from `CHANGELOG.md`, and report accurate standalone status without false push failures.

## [1.5.7] - 2026-11-01

### Added
- **Epoch-Aware `read_file` Overwrite Deduplication (`skillsdb/core/guardrails.py`, `v3.8.7`)**:
  - Added `_was_file_region_read_in_current_epoch()` to `handle_pre_tool_guardrail_hook()`: the first narrow `read_file` call on a file (or a new region of a `> 800` line file) in the current uncompacted epoch still expands to the full file (`<= 800 lines`) or a 650-line window (`> 800 lines`), while subsequent follow-up reads of the same file/region within the same uncompacted epoch stay compact instead of pumping duplicate 650-line blocks into the active context window.
- **Autonomous `research` Subagent Finding Persistence & Pre-Compaction Working-Set Checkpoint (`skillsdb/memory/project_memory.py`, `v3.8.7`)**:
  - Extended `analyze_conversation_epochs_and_scope()` and `handle_pre_invocation_hook()` to automatically capture incoming `research` subagent completion reports (`subagent_finding_step_<N>`) and pre-compaction working sets (`auto_checkpoint_working_set` when `steps_since_compaction >= 150`) directly into `.agents/memory.db` with zero extra model turns.
- **Milestone `/compact` Advisor on Release Sync (`skillsdb/memory/gitea_sync.py`, `v3.8.7`)**:
  - Added `[COMPACT MILESTONE TIP]` output when `skillsdb gitea-sync` publishes Keep a Changelog releases so the agent can recommend `/compact` at clean release boundaries.

## [1.5.6] - 2026-11-01

### Added
- **Deterministic `PreToolUse` Token-Economy Overwrite Engine & Subagent Firewall (`skillsdb/core/guardrails.py`, `plugin/hooks.json`, `v3.8.6`)**:
  - **Anti-Salami-Slicing Window Expansion (`read_file` / `view_file`)**: Automatically intercepts narrow line-slice requests (`< 350 lines`) via `PreToolUse` `overwrite` and expands them with zero extra model turns to the full file (`1..N` when `<= 800 lines`) or a centered 650-line window (when `> 800 lines`), eliminating sequential 20-40 line file slicing in marathon sessions.
  - **Automatic Synchronous Wait Upgrade (`run_command`)**: Automatically upgrades non-daemon `run_command` calls with `WaitMsBeforeAsync < 10000` (e.g. `5000` ms) to `10000` ms via `PreToolUse` `overwrite` so 5-9 second commands complete synchronously in 1 turn instead of backgrounding into 2 turns.
  - **Subagent Firewall & Zero-Polling Enforcement in Background Chains (`run_command`)**: Blocks shell polling of `.system_generated/tasks/*.log` (`Get-Content`/`cat`) and blocks the 5th consecutive exploratory search command (`python -c`, `Select-String`, `Get-ChildItem`, `grep`) in the main context, enforcing delegation to `invoke_subagent(TypeName='research')` even during continuous background-task wakeups where `PreInvocation` does not fire.
- **Unversioned Upload Confirmation Support (`skillsdb/memory/gitea_sync.py`, `skillsdb/cli.py`, `plugin/rules/AGENTS.md`, `v3.8.6`)**:
  - Added `--allow-unversioned` to `skillsdb gitea-sync` and anchored the double-confirmation policy in `AGENTS.md` and `customizations.db` (`gitea-local-sync`): when the user requests uploading without a SemVer version bump, the agent asks once for explicit confirmation and then pushes cleanly to Gitea and GitHub (`--allow-unversioned --github`) without creating or moving release tags.

### Fixed
- **Transcript Workspace Inference Isolation (`skillsdb/memory/project_memory.py`, `v3.8.6`)**:
  - Restricted `analyze_conversation_epochs_and_scope()` path extraction to `PLANNER_RESPONSE` tool calls and `USER_INPUT` steps so paths mentioned inside `MODEL:GENERIC` tool output logs never hijack the inferred workspace of another conversation.

## [1.5.5] - 2026-10-31

### Added
- **Transcript-Based Workspace Inference & Mid-Session Project Memory Re-Hydration (`skillsdb/memory/project_memory.py`, `skillsdb/cli.py`, `plugin/rules/AGENTS.md`, `v3.8.5`)**:
  - **Automatic Workspace Inference (`inferred_workspace`)**: When no formal IDE workspace is open (`workspacePaths: []`), `handle_pre_invocation_hook()` automatically infers the active project root (e.g. `D:\Android`) from recent file paths in `transcript.jsonl`, prioritizing directories with `.agents/memory.db` or `.git`.
  - **Mid-Session `.agents/memory.db` Re-Hydration**: Every 50 steps (`step_bucket = max_step_idx // 50`) or whenever repeated file reads (`>= 3x` on the same file) / read streaks occur, the hook now automatically re-injects `=== PROJECT MEMORY CONTEXT (Auto-Hydrated from .agents/memory.db) ===` (Active Task Board, Project Facts, Architectural Decisions, and Latest Snapshot) so IDE `<CONTEXT_SUMMARY>` compactions never wipe out project memory during multi-hour runs.
  - **Active Fact & Task Indexing Enforcement (`mem-save-fact` / `mem-task-add`)**: Mandated structural fact/offset indexing in `AGENTS.md`, Turn-1 guardrails, and the Drift Guard (`[PROJECT MEMORY ALERT]` when `< 5` facts exist or files are repeatedly re-read), and added CLI aliases `mem-add-task`, `mem-update-task`, `mem-list-tasks`, and `mem-clear-tasks`.

## [1.5.4] - 2026-10-31

### Changed
- **Refined Tooling Anomaly Reporting Directive (`plugin/rules/AGENTS.md`, `skillsdb/memory/project_memory.py`, `database/customizations.db`, `v3.8.4`)**:
  - Scoped `proactive-error-reporting` specifically to internal `AntiGraviton` / `SkillsDB` tooling, hook, or sync bugs that force a background workaround: instead of interrupting mid-task, the agent completes the task first and appends a brief note at the end of the response suggesting a permanent fix in `AntiGraviton`.

## [1.5.3] - 2026-10-31

### Fixed
- **Post-Push Snapshot Amend Detection (`skillsdb/memory/gitea_sync.py`, `skillsdb/memory/project_memory.py`, `v3.8.3`)**:
  - Removed the `_has_unpushed_commits()` gate when `save_code_commit(..., explicit_message=True)` checks whether to amend a recent (`<= 120s`) `mem-save-snapshot` commit, and added `Snapshot-Commit: true` / `Conversation-ID:` trailer detection so a synchronous snapshot push immediately followed by `gitea-sync -m "..."` cleanly amends the commit subject and updates `origin/main` via `--force-with-lease`.

## [1.5.2] - 2026-10-31

### Added
- **Proactive Error & Anomaly Reporting Directive (`plugin/rules/AGENTS.md`, `skillsdb/memory/project_memory.py`, `database/customizations.db`, `v3.8.2`)**:
  - Anchored the mandatory `proactive-error-reporting` rule across `AGENTS.md`, Turn-1 guardrails, and `customizations.db`, requiring the agent to immediately report any unexpected error, warning, push rejection, or race condition and its root cause to the user rather than silently working around it.

### Fixed
- **Gitea & GitHub Push Reliability (`skillsdb/memory/gitea_sync.py`, `skillsdb/memory/gitea_releases.py`, `v3.8.2`)**:
  - **Snapshot Amend Race Resolution**: Added `_can_safely_force_replace_remote_snapshot()` and `--force-with-lease` handling in `_push_to_origin()` so amending a recent auto-snapshot commit (`<= 120s`) that was concurrently pushed by a background `async_push` process cleanly updates `origin/main` (and any secondary `gitea` remote) instead of failing with `non-fast-forward`.
  - **Idempotent Annotated Tag Objects**: Stopped `publish_gitea_release()` from running `git tag -f -a` when the release tag already exists at the target commit, preserving identical tag-object SHAs across Gitea and GitHub.
  - **Historical Release Sync**: Allowed `publish_gitea_release()` to sync release notes and deliverable assets for an older historical tag (`final_tag != current VERSION`) on a clean working tree by automatically pinning `target_commit` to `existing_tag_sha` without moving the tag.

## [1.5.1] - 2026-10-31

### Changed
- **Unified English System Directives & Sentence-Case Rule Normalization (`database/customizations.db`, `plugin/rules/AGENTS.md`, `v3.8.1`)**:
  - Converted all German-language rule bodies (`no-deppenbindestrich` and `communication-style`) in `customizations.db` and mixed German/English passages in `plugin/rules/AGENTS.md` to 100% pure English instructions (preserving only short quoted German orthography examples) for consistent LLM instruction-following.
  - Normalized all rule titles, Markdown headings, and bold bullet labels across `customizations.db` to natural sentence case so active rules strictly model the `sentence-case-headings` invariant.

### Fixed
- **Deduplicated Active Global Rules (`database/customizations.db`, `v3.8.1`)**:
  - Removed the duplicated compound-noun paragraph between `no-deppenbindestrich` and `communication-style` and pruned the redundant `flutter-hot_reload` rule entry (superseded by `flutter-flutter-hot-reload`).

## [1.5.0] - 2026-10-31

### Added
- **3-Stage Turn-Economy, Diagnostic Protocol & Mid-Session Drift Guard (`skillsdb/memory/project_memory.py`, `plugin/rules/AGENTS.md`, `database/customizations.db`, `v3.8.0`)**:
  - **Stage 1 (Explicit Threshold Rules)**: Replaced ambiguous line-slicing wording with concrete thresholds across `AGENTS.md` and `customizations.db` (`token-efficiency`): read files `<= 800 lines` in a single call, batch multi-range reads for files `> 800 lines` in parallel within 1 turn, delegate open-ended debugging (`>= 4 search steps` or `> 5 files`) to `research` subagents (`invoke_subagent`), eliminate entire bug classes before rebuilding (`build && test && verify`), enforce zero `manage_task status` polling (`WaitMsBeforeAsync: 5000-10000` + `Reactive Wakeup`), and use local SQLite indexing in `scratch/` for projects `> 300 files` requiring `>= 3` analysis rounds.
  - **Stage 2 (Turn-1 Invariant Injection)**: Added Section `4. TURN-ECONOMY, PARALLEL BATCHING & DIAGNOSTIC PROTOCOL` directly into the `[SKILLSDB CRITICAL GUARDRAIL & INVARIANTS (TURN 1)]` block of `handle_pre_invocation_hook()`.
  - **Stage 3 (Mid-Session Drift Guard)**: Extended `analyze_conversation_epochs_and_scope()` and `handle_pre_invocation_hook()` to track conversation step buckets (every 150 steps) as well as consecutive `read_file` salami-slicing (`>= 6` in last 15 lines) and `manage_task` polling streaks (`>= 2`), automatically injecting a 2-line ephemeral drift-guard reminder during long marathon sessions.

## [1.4.16] - 2026-10-31

### Added
- **Universal Deliverable Release Artifact Attachment (`skillsdb/memory/gitea_releases.py`, `skillsdb/memory/gitea_sync.py`, `plugin/rules/AGENTS.md`, `v3.7.16`)**:
  - Added `_discover_project_release_artifacts()` and `_attach_release_binary_assets()` to automatically discover and attach project deliverable artifacts (`.apk`, `.aab`, `.ipa`, `.exe`, `.msi`, `.msix`, `.dmg`, `.pkg`, `.deb`, `.rpm`, `.appimage`, `.xpi`, `.crx`, `.vsix`, `.whl`, `.jar`, `.nupkg`, and release archives in `releases/` or `dist/`) as downloadable Gitea Release Assets during `skillsdb gitea-sync --releases` and `skillsdb gitea-release`.
  - Anchored the global rule requiring deliverable binaries and artifacts to be attached to every corresponding SemVer release and enforcing canonical `AntiGraviton` source builds (`build.py` + `deploy.py`) over in-place `db_manager.py` edits.

### Fixed
- **Release Asset Helper Parameter Alignment (`skillsdb/memory/gitea_releases.py`, `v3.7.16`)**:
  - Fixed the positional parameter order of `_attach_release_binary_assets(base_url, owner, repo_name, rel_id, tag, existing_assets, token, project_root)` across all call sites in `sync_changelog_releases()` and `publish_gitea_release()`, and excluded third-party vendor binaries (such as `gitea-*.exe`, `apktool*.jar`, and `-unsigned.apk`) from release asset discovery.

## [1.4.15] - 2026-10-30

### Changed
- **Upgraded Default Gitea Service Binary to `v28.2.0` (`install-antigraviton.ps1`, `install_antigraviton.py`, `v3.7.15`)**:
  - Updated the default Gitea binary version in `install-antigraviton.ps1` and `install_antigraviton.py` from `1.22.6` to `28.2.0` (`gitea-28.2.0-windows-amd64.exe` with SHA256 verification) and upgraded the local Gitea Windows service to `v28.2.0`.

## [1.4.14] - 2026-10-29

### Added
- **Standardized 9-Block `README.md` Blueprint with Clickable `Table of contents` Anchors (`README.md`, `plugin/rules/AGENTS.md`, `database/customizations.db`, `v3.7.14`)**:
  - Added a clickable `## Table of contents` section with GitHub/Gitea Markdown anchors directly below the header block in `README.md`.
  - Extended the global rule `readme-shields-badges-header` in `customizations.db` and `plugin/rules/AGENTS.md` to codify the complete 9-block `README.md` standard (Title, Shields.io Badges, Short Description, Table of Contents with anchors, Executive Summary & Impact Table, Installation, Usage, Architecture & ASCII Directory Tree, and Versioning/License).

## [1.4.13] - 2026-10-29

### Added
- **Standardized Shields.io Header Badges & Global Rule (`README.md`, `build.py`, `plugin/rules/AGENTS.md`, `database/customizations.db`, `v3.7.13`)**:
  - Added the clickable Shields.io badge header block (`Version`, `SkillsDB`, `Gitea Service`, `Tests`, `Antigravity`, `Database`, `Python`, and `License`) to the top of `README.md` and wired `sync_project_versions()` in `build.py` to keep the `version` and `SkillsDB` badges automatically synchronized on every build.
  - Registered the global rule `readme-shields-badges-header` in `customizations.db` and `plugin/rules/AGENTS.md` so every project `README.md` consistently includes top-level Shields.io badges.

## [1.4.12] - 2026-10-29

### Changed
- **Added Commons Clause v1.0 Commercial Resale Prohibition to `GNU AGPL v3.0` (`LICENSE`, `README.md`, `plugin/plugin.json`, `plugin/README.md`, `v3.7.12`)**:
  - Extended the `GNU AGPL v3.0` license with the **Commons Clause License Condition v1.0** (`AGPL-3.0-only WITH Commons-Clause-1.0`) to explicitly prohibit third parties and commercial skill marketplaces from repackaging or selling AntiGraviton, SkillsDB, or its `SKILL.md` workflows and rules for a fee.

## [1.4.11] - 2026-10-29

### Fixed
- **GitHub Licensee `AGPL-3.0` Badge Detection (`LICENSE`, `v3.7.11`)**:
  - Removed the custom preamble above the canonical `GNU AFFERO GENERAL PUBLIC LICENSE` header in `LICENSE` and placed the `Copyright (C) 2026 scorpion421 (MickyFoley)` attribution inside the standard GNU appendix block so GitHub's Licensee parser automatically displays the `AGPL-3.0` license badge.

## [1.4.10] - 2026-10-29

### Changed
- **Relicensed Under GNU Affero General Public License v3.0 (`LICENSE`, `README.md`, `plugin/plugin.json`, `plugin/README.md`, `v3.7.10`)**:
  - Replaced the permissive MIT license with the strict copyleft **GNU AGPL v3.0 (`AGPL-3.0-only`)** license so AntiGraviton and SkillsDB remain permanently free and open source with mandatory author attribution (`scorpion421`), preventing proprietary closed-source commercialization or relicensing by third parties.

## [1.4.9] - 2026-10-29

### Changed
- **Single-Source-of-Truth Version Synchronization in `build.py` (`build.py`, `skillsdb/config.py`, `v3.7.9`)**:
  - Added `sync_project_versions()` to `build.py` so updating `__version__` and `__suite_version__` in `skillsdb/config.py` and running `python build.py` automatically synchronizes `VERSION`, `plugin/plugin.json`, `plugin/README.md`, `README.md`, `install-antigraviton.ps1`, `install_antigraviton.py`, `.agents/skills/install-antigraviton/SKILL.md`, `database/db_manager.py`, and `runtime_config.schema_version` in `database/customizations.db`.
- **Site-Neutral Tracked `plugin/README.md` with Dynamic Deployment Enrichment (`plugin/README.md`, `deploy.py`)**:
  - Replaced the hardcoded site-specific Gitea username in `plugin/README.md` with a neutral `http://localhost:3000` service reference in Git while teaching `deploy.py` to dynamically inject the active machine's `gitea_user` (`MickyFoley` or active user) from `runtime_config` into `~/.gemini/config/plugins/customizations-db/README.md`.

## [1.4.8] - 2026-10-29

### Added
- **Antigravity Plugin Page Metadata & Documentation (`plugin/plugin.json`, `plugin/README.md`, `build.py`, `v3.7.8`)**:
  - Enriched `plugin/plugin.json` with `displayName`, `version` (`3.7.8`), and `suggestedPrompts` (3 starter prompts) matching the latest Antigravity Plugin manifest specification, and added `plugin/README.md` with developer and repository links.
  - Updated `build.py` to automatically synchronize `plugin/plugin.json`, `plugin/README.md`, and the bundled `database/db_manager.py` header version from `skillsdb/config.py`.

### Fixed
- **Mid-Epoch UTF-8 Manifest Self-Healing After Antigravity IDE Updates (`skillsdb/memory/project_memory.py`)**:
  - Moved the `<0.2ms` `ensure_windows_utf8_manifests()` check in `handle_pre_invocation_hook()` ahead of `_HOOK_STATE_FILE` deduplication so clicking *Restart to Update* inside an already active conversation epoch immediately restores wiped `Antigravity.exe.manifest` and `language_server.exe.manifest` files on the very next turn.

## [1.4.7] - 2026-10-29

### Fixed
- **Duplicate Keep a Changelog version header prevention and `CHANGELOG.md` cleanup (`CHANGELOG.md`, `skillsdb/core/guardrails.py`, `v3.7.7`)**:
  - Removed duplicate `1.4.6` and `1.4.5` release blocks from `CHANGELOG.md`.
  - Extended the `keep-a-changelog` guardrail in `check_text()` (`skillsdb/core/guardrails.py`) to track seen `## [X.Y.Z]` version headers and flag any duplicate version entry as a `CRITICAL` violation before commit or file write.
- **Pre-release branch and tag push to `github` remote during `skillsdb gitea-sync --releases --github` (`skillsdb/memory/gitea_releases.py`)**:
  - Updated `sync_changelog_releases()` to push `main` and `--tags` to the `github` remote before invoking `gh release create`, ensuring GitHub release tags always point to the exact release commit rather than the previous remote `HEAD`.

## [1.4.6] - 2026-10-28

### Changed
- **Dynamic Per-Turn Token Savings Baseline in `skillsdb stats` (`skillsdb/memory/project_memory.py`, `v3.7.6`)**:
  - Replaced the static `14,430` per-turn token constant in `calculate_transcript_savings()` with `_compute_dynamic_turn_savings()`, which dynamically computes the unconsolidated turn baseline (`rules_tokens + non_builtin_catalog_tokens + 2 * avg_skill_tokens`), net tokens saved per turn, and exact percentage reduction directly from the active `rules` and `skills` tables in `customizations.db`.
- **Cross-Platform Installer State Preservation Parity (`install_antigraviton.py`)**:
  - Synchronized `backup_existing_state()` and `restore_existing_state()` in `install_antigraviton.py` with `install-antigraviton.ps1` so `runtime_config.updated_at` timestamps are preserved identically across Windows, Linux, and macOS upgrades.

## [1.4.5] - 2026-10-28

### Added
- **Temporal Epoch Slicing & Cross-Project Contamination Guard (`skillsdb/memory/project_memory.py`, `v3.7.5`)**:
  - Added `analyze_conversation_epochs_and_scope()` to slice a conversation's `transcript.jsonl` into distinct chronological epochs (separated by `>4h` gaps or UTC date changes) and correlate historical turns against learned workspace paths without requiring extra `conversation_id` columns in SQLite tables.
  - Updated `handle_pre_invocation_hook()` to inject `[TEMPORAL & WORKSPACE ISOLATION ANCHOR]` on Turn 1 and automatically emit `[CROSS-PROJECT CONTAMINATION GUARD]` whenever historical epochs in the same `conversationId` (or `<CONTEXT_SUMMARY>`) mention foreign workspaces from earlier dates.
  - Updated hook state deduplication (`_HOOK_STATE_FILE`) to include the active temporal epoch (`f"{workspace_key}@{epoch_key}"`), keeping per-step hook noise at zero within an active epoch while automatically re-hydrating guardrails whenever a conversation resumes on a new day or after a `>4h` gap.
- **Strictly Date-Scoped Daily Telemetry & Timestamped Memory Context (`skillsdb/memory/project_memory.py`, `skillsdb/cli.py`, `skillsdb/memory/gitea_config.py`)**:
  - Updated `calculate_transcript_savings()`, `stats()`, and `skillsdb stats [--today] [--date YYYY-MM-DD]` to report both strictly date-scoped daily metrics (`Measured Activity for YYYY-MM-DD`) and all-time cumulative metrics (`Cumulative Token Savings`).
  - Added `[YYYY-MM-DD]` timestamps to active tasks, architectural decisions, project facts, and milestone snapshots (with `[ACTIVE EPOCH]` markers) in `handle_pre_invocation_hook()`, `mem_get_context()`, and `skillsdb gitea-config` (`get_learned_workspaces_with_timestamps()`).

### Fixed
- **Workspace Activity Timestamp Preservation Across Passive Discovery and Suite Upgrades (`skillsdb/memory/gitea_config.py`, `install-antigraviton.ps1`)**:
  - Added `_infer_workspace_last_active_iso()` and `touch_timestamp: bool = True` to `learn_workspace()` so passive workspace discovery (`discover_all_workspaces()`) preserves existing `updated_at` timestamps instead of stamping all workspaces with `now_iso`.
  - Updated `install-antigraviton.ps1` backup/restore steps to preserve `updated_at` in `runtime_config` and self-heal previously bulk-stamped `workspace:*` entries from disk.

## [1.4.4] - 2026-10-28

### Fixed
- **Antigravity Go Hook Runner Command Tokenization (`plugin/hooks.json`)**: Removed escaped double quotes around `%USERPROFILE%/.gemini/database/db_manager.py` in `PreInvocation` and `PreToolUse` hook definitions. Antigravity's native Go hook runner splits `command` strings via whitespace without stripping quotes (`strings.Fields`), which caused `python.exe` after a server restart to treat `""C:\Users\...\db_manager.py""` as a relative path with `[Errno 22] Invalid argument`.
- **Multi-Remote GitHub Release Sync (`skillsdb/memory/gitea_releases.py`, `skillsdb/memory/gitea_sync.py`)**: Added `_resolve_github_repo_slug()` so `skillsdb gitea-release --github` and `skillsdb gitea-sync --releases --github` automatically pass `--repo <owner>/<repo>` to `gh release` when `origin` points to local Gitea and `github` points to `github.com`.

## [1.4.3] - 2026-10-28

### Added
- **Safe FTS5 query sanitizer and exact-match ranking (`skillsdb/search/synonyms.py`, `skillsdb/search/fts.py`, `skillsdb/core/concurrency.py`, `skillsdb/memory/project_memory.py`, `v3.7.3`)**:
  - Added `build_safe_fts_query()` and `_FTS5_RESERVED_WORDS` to sanitize and double-quote FTS5 search tokens so queries containing hyphens (e.g. `Komposita-Regeln`), punctuation, empty whitespace, or reserved operators (`AND`, `OR`, `NOT`, `NEAR`) never raise `sqlite3.OperationalError`.
  - Added exact-name match prioritization (`ORDER BY CASE WHEN name = ? THEN 0 ELSE 1 END`) across `get_skill()`, `get_rule()`, `fetch_single_skill()`, and `benchmark_concurrency()`.
  - Updated `extract_skill_sections()` to track fenced Markdown code blocks (` ``` ` / `~~~`) so `# comments` inside embedded code blocks are never misparsed as Markdown section headings.
- **Native MCP JSON-RPC 2.0 `resources/list`, `prompts/list`, and tool alias support (`skillsdb/platform/mcp_server.py`)**:
  - Added handlers for `resources/list` and `prompts/list`, added `skillsdb_suggest` / `skillsdb_rules` / `skillsdb_guardrail` tool aliases matching built-in agent templates, and ensured JSON-RPC 2.0 error responses (`-32603`) are emitted if an exception occurs on a request with an `id`.

### Changed
- **Fill-in-the-Middle (`FIM`) payload completeness and expanded symbol discovery (`skillsdb/core/fim.py`)**:
  - Added `"middle"` alongside `"target_line_content"` in `slice_fim()`, clamped out-of-range line numbers safely, and expanded `slice_fim_symbol()` regex patterns to match `async def`, `func`, `fn`, `struct`, and `enum` declarations.

### Fixed
- **Agent handoff read/format key alignment and full context hydration (`skillsdb/memory/handoff.py`)**:
  - Fixed `read_handoff()` and `format_handoff_block()` so `skillsdb handoff read <id>` populates `handoff_id`, `active_facts`, and `active_decisions`, and gracefully formats handoffs even if the underlying task was archived or deleted.
- **Episodic project memory FTS5 synchronization, `:memory:` path resolution, and hook state self-healing (`skillsdb/memory/project_memory.py`, `skillsdb/memory/writer_queue.py`)**:
  - Added `_resolve_db_file()` so `:memory:` SQLite connections never resolve `PRAGMA database_list` to `Path(".")`.
  - Synchronized `project_memory_fts` across `mem_save_fact()`, `mem_deprecate_fact()`, `mem_task_update()`, `mem_task_clear()`, `mem_compact()`, `verify_task()`, and `flush_journals()` to eliminate duplicate or stale FTS5 entries.
  - Hardened `handle_pre_invocation_hook()` to self-heal corrupted `skillsdb_hook_state.json` files and pass `auto_commit=False` during Turn-1 auto-compaction so background task pruning never creates unexpected Git commits.
- **Differential database merge transaction safety and `synonym_synapses` schema alignment (`skillsdb/updater/merger.py`, `deploy.py`)**:
  - Fixed `deploy.py` (`safe_deploy_database()`) and `merge_upstream_database()` to use the canonical `synonym_synapses (term, expansions)` schema and verify `learned_after >= learned_before` before committing, and updated `remove_item()` to rebuild external-content FTS5 tables (`INSERT INTO skills_fts(skills_fts) VALUES ('rebuild')`).
- **Model detector decimal version regex, local extended-length path handling, and bundler import precision (`skillsdb/core/detector.py`, `skillsdb/core/db.py`, `skillsdb/core/guardrails.py`, `build.py`, `plugin/hooks.json`)**:
  - Updated `<USER_SETTINGS_CHANGE>` regex in `detector.py` to capture model names containing decimal points (`Gemini 3.8 Flash.`, `Claude Opus 5.5.`), and excluded local Windows extended-length paths (`\\?\C:\...`) from `_is_network_path()` in `db.py`.
  - Updated `guardrails.py` so `fix_file()` skips test suites (`tests/test_*.py`) and `fix_text()` preserves negative rule examples (`never '<compound>'`), quoted `%USERPROFILE%` in `plugin/hooks.json`, and tightened `build.py` relative-import stripping so docstrings starting with `from .git/config` are preserved verbatim.

## [1.4.2] - 2026-10-28

### Added
- **Antigravity `v2.28.0` Turn-1 user prompt FTS5 skill pre-routing (`skillsdb/memory/project_memory.py`, `v3.7.2`)**:
  - Added `_extract_hook_user_message()` and integrated incoming `v2.28.0+` user prompt payloads (`userMessage`, `userPrompt`, `prompt`, `message`) into `handle_pre_invocation_hook()` so top matching domain micro-skills are automatically pre-routed via synonym-expanded FTS5 on Turn 1 without requiring an extra CLI roundtrip.
- **Antigravity `v2.27.0` / `v2.28.0` `PreToolUse` guardrail support for `write_file` and `edit_file` (`skillsdb/core/guardrails.py`, `plugin/hooks.json`)**:
  - Extended `handle_pre_tool_guardrail_hook()` to inspect modern Antigravity tool schemas (`write_file` -> `Path`, `Content`; `edit_file` -> `Path`, `Replacement`) and `ReplacementChunks` (`multi_replace_file_content`) alongside legacy `TargetFile`, `CodeContent`, and `ReplacementContent` fields.
  - Registered the `PreToolUse` guardrail handler in `plugin/hooks.json` for `write_file|edit_file|write_to_file|replace_file_content|multi_replace_file_content`.

### Changed
- **Expanded model tier classification for Antigravity `v2.24.0` through `v2.28.0` and IDE `v2.6.0` (`skillsdb/core/detector.py`)**:
  - Updated `_classify_model_string()` to map Vertex AI `Claude Opus 5.5` (`claude opus`, `claude-opus`, `opus 5`) to `TIER_ULTRA`, `Claude Sonnet 5.5` (`claude sonnet`, `claude-sonnet`, `sonnet 5`) to `TIER_STANDARD`, and `gemini-3.6-flash` / `Gemini 3.6 Flash` to `TIER_LEAN`.
- **Strict YAML frontmatter formatting for Antigravity `v2.25.0+` and CLI `v1.4.0` `agy plugin validate` (`.agents/skills/install-antigraviton/SKILL.md`)**:
  - Converted the `description:` frontmatter field in `.agents/skills/install-antigraviton/SKILL.md` to a multiline YAML block scalar (`>-`) and updated all suite version references to `v1.4.2` (`SkillsDB v3.7.2`).

### Fixed
- **Dual suite and engine version comparison in `skillsdb check-update` (`skillsdb/updater/merger.py`, `skillsdb/config.py`)**:
  - Added `_extract_skillsdb_version_from_release()` and `__suite_version__` so `check_update()` accurately compares both the SkillsDB engine version (`3.7.2`) and the AntiGraviton suite tag (`1.4.2`) instead of comparing a `1.x.y` suite tag directly against `3.x.y`.
- **PowerShell 7.6 `NativeCommandError` resilience and Gitea `1.22+` `DISABLE_GRAVATAR` deprecation removal (`install-antigraviton.ps1`, `gitea-service/scripts/deploy-gitea.ps1`, `gitea-service/scripts/backup-gitea.ps1`, `gitea-service/custom/conf/app.example.ini`)**:
  - Set `$PSNativeCommandUseErrorActionPreference = $false` across PowerShell installers and wrapped Step 4 `gitea.exe` CLI invocations in `install-antigraviton.ps1` with `$ErrorActionPreference = "Continue"` so `stderr` notices never abort user/token provisioning under PowerShell 7.6.
  - Removed deprecated `DISABLE_GRAVATAR = true` from `gitea-service/custom/conf/app.example.ini` and added automatic cleanup of legacy `DISABLE_GRAVATAR` lines in `app.ini` during service deployment and upgrades.

## [1.4.1] - 2026-10-24

### Added
- **Network share (`DRIVE_REMOTE` / UNC) SQLite pragma auto-adaptation (`skillsdb/core/db.py`, `v3.7.1`)**:
  - Added `_is_network_path()` to detect UNC paths (`\\server\share`) and mapped Windows SMB/CIFS network drives (`GetDriveTypeW == DRIVE_REMOTE`).
  - Updated `_apply_pragmas()` to keep `journal_mode = WAL`, `synchronous = NORMAL`, and `mmap_size = 64 MB` on local drives while automatically switching to `journal_mode = TRUNCATE`, `synchronous = FULL`, and `mmap_size = 0` on network shares to prevent `SQLITE_IOERR_SHMOPEN` and shared-memory lock corruption over SMB.
- **LevelDB-free database indexer and dual-stage backup staging (`gitea-service/custom/conf/app.example.ini`, `gitea-service/scripts/deploy-gitea.ps1`, `gitea-service/scripts/backup-gitea.ps1`)**:
  - Added `[indexer]` (`ISSUE_INDEXER_TYPE = db`, `REPO_INDEXER_ENABLED = false`) to `app.example.ini` and `deploy-gitea.ps1` self-healing so Gitea stores issue indexes inside SQLite instead of Bleve/LevelDB files.
  - Updated `backup-gitea.ps1` to write dumps to local staging (`backups/`) first and synchronize to offsite targets with automatic fallback if the offsite share is temporarily offline.

### Fixed
- **Stale `refs/remotes/origin/*` ghost tracking refs and hyphen/underscore Gitea repository resolution (`skillsdb/memory/gitea_sync.py`)**:
  - Updated `setup_workspace_git()` to preserve pre-existing `github.com` `origin` URLs as a `github` remote before repointing `origin` to local Gitea, and added `_prune_stale_origin_refs()` to clear leftover `refs/remotes/origin/*` refs when `origin` changes URL.
  - Updated `_has_unpushed_commits()` to verify via `inspect_remote_repository()` whether the target Gitea repository is still `empty=True` even if a stale local tracking ref matches `HEAD`.
  - Updated `_resolve_workspace_repo_name()` to check existing `github` remote names and hyphen/underscore variants on Gitea before creating duplicate empty repositories.
  - Updated `_push_to_origin()` to push local Git tags (`--tags`) alongside the active branch to local Gitea (`origin`).

## [1.4.0] - 2026-10-23

### Added
- **Autonomous post-update Windows UTF-8 manifest self-healing (`skillsdb/platform/windows_utf8.py`, `skillsdb/memory/project_memory.py`, `skillsdb/updater/merger.py`, `deploy.py`, `v3.7.0`)**:
  - Added `ensure_windows_utf8_manifests()` to silently verify and restore `Antigravity.exe.manifest` and `resources/bin/language_server.exe.manifest` whenever an Antigravity IDE update overwrites the application directory.
  - Integrated `ensure_windows_utf8_manifests()` into Turn 1 of `handle_pre_invocation_hook()`, `deploy.py`, and `skillsdb update` so UTF-8 process code pages self-heal automatically without requiring manual `skillsdb fix-utf8` execution after IDE updates.
- **Checksum-driven Antigravity `v2.26.0` builtin skill and sub-documentation synchronization (`skillsdb/updater/merger.py`, `skillsdb/config.py`, `skillsdb/search/synonyms.py`)**:
  - Added `compile_skill_directory_content()` and `sync_builtin_skills()` to index all 9 Antigravity `v2.26.0` builtin skills (`agy-customizations`, `antigravity-guide`, `automation`, `generative_ui`, `migrate-workflows`, `permissioned-github`, `plugin`, `run-workflow`, `ui-plugin-navigation`) along with their bundled `docs/*.md` and `references/*.md` sub-documents into `customizations.db` for surgical `--summary` and `--section` micro-skill retrieval.
  - Tracked `~/.gemini/antigravity/builtin/.checksum` in `runtime_config` (`builtin_skills_checksum`) so `handle_pre_invocation_hook()` and `skillsdb doctor` automatically re-index builtin skills whenever Antigravity updates.
  - Added the `antigravity` skill cluster in `skillsdb/config.py` and German/English FTS5 search synonyms (`automatisierung`, `zeitplan`, `erweiterung`, `widget`) in `skillsdb/search/synonyms.py`.

### Changed
- **Frontier model tier classification for `Gemini 4 Carbon` and `Flash Lite` (`skillsdb/core/detector.py`)**:
  - Extended `_classify_model_string()` to map `Gemini 4 Carbon` (`carbon`) alongside `Gemini 4 Argon` to `TIER_ULTRA` (16-thread high-concurrency mode) and `Flash Lite` (space-separated automation preset) to `TIER_LEAN`.
- **Dynamic branch and Gitea repository name resolution (`skillsdb/memory/gitea_sync.py`)**:
  - Added `_get_current_branch()` and `_resolve_workspace_repo_name()` so workspaces on non-`main` branches push cleanly and existing Gitea remote repository names are preserved across sync and release operations.

### Fixed
- **Application installation directory (`%LOCALAPPDATA%/Programs/antigravity`) non-workspace boundary guard (`skillsdb/memory/gitea_config.py`, `skillsdb/memory/project_memory.py`, `skillsdb/updater/merger.py`)**:
  - Hardened `_is_excluded_workspace_path()`, `_is_protected_non_workspace()`, and `skillsdb doctor` to block `%LOCALAPPDATA%/Programs/*`, `%APPDATA%/Antigravity`, `%ProgramFiles%`, and `ANTIGRAVITY_AGENTAPI_EXE` parent directories when Antigravity `v2.26.0` runs shell commands outside of an active project (`ANTIGRAVITY_PROJECT_ID=outside-of-project`), preventing `.agents/memory.db` or `.git` from ever being initialized inside the Antigravity binary directory.
- **Non-destructive plugin and builtin skill re-indexing (`skillsdb/updater/merger.py`)**:
  - Updated `import_from_plugins()` to perform non-destructive `INSERT OR REPLACE` updates across `GLOBAL_PLUGINS_PATH`, `ARCHIVED_PLUGINS_PATH`, and `BUILTIN_SKILLS_PATH` without wiping pre-existing official rules or skills.

## [1.3.0] - 2026-10-21

### Added
- **Hard SemVer immutability enforcement for published releases (`skillsdb/memory/gitea_sync.py`, `skillsdb/core/guardrails.py`, `skillsdb/cli.py`, `v3.6.0`)**:
  - Updated `publish_gitea_release()` to check whether the target release tag (`vX.Y.Z`) already exists at an older commit than `HEAD`; if so (and `--commit` is not explicitly targeting a historical milestone), SkillsDB halts with `[SEMVER IMMUTABILITY ERROR]` and refuses to overwrite or move an already published release tag.
  - Added `--commit <sha>` to `skillsdb gitea-release` to support pinning historical release tags to specific milestone commits without mutating newer releases.
  - Extended `handle_pre_tool_guardrail_hook()` in `skillsdb/core/guardrails.py` to block `git tag -f vX.Y.Z` commands that attempt to overwrite existing semantic version tags.

### Changed
- **Enterprise SQLite connection lifecycle, WAL/mmap tuning, and zero-lock schema checks (`skillsdb/core/db.py`, `skillsdb/memory/writer_queue.py`, `skillsdb/memory/project_memory.py`, `skillsdb/memory/gitea_config.py`, `deploy.py`, `skillsdb/cli.py`)**:
  - Added `managed_connection()` and `execute_with_retry()` in `skillsdb/core/db.py` with consistent `30000 ms` `busy_timeout`, `64 MB` `mmap_size`, `temp_store = MEMORY`, and deterministic `conn.close()` on context exit to prevent Windows file-handle leaks.
  - Added fast read-only `sqlite_master` verification (`_central_schema_ready()` and `_project_schema_ready()`) in `init_db()` and `init_project_db()` so read invocations (`get-skill`, `suggest`, `mem-get-context`, `PreInvocation` hook) acquire zero write locks.
  - Registered `atexit` queue draining and passive WAL checkpointing in `WriterQueue` (`skillsdb/memory/writer_queue.py`).
  - Added `safe_deploy_database()` in `deploy.py` using in-place `ATTACH DATABASE` differential merges and online `sqlite3.Connection.backup()` instead of raw `shutil.copy2` file overwrites on active WAL databases.
  - Added fault-tolerant CLI flag aliases (`--title`/`--topic`, `--content`/`--decision`, `--rationale`/`--context`/`--reason` on `mem-save-decision`, `--rationale`/`--context` on `mem-save-fact`, and `--service` on `gitea-config`).

## [1.2.0] - 2026-10-21

### Security
- **Token-free `.git/config` remotes, transient in-memory Git auth, and automatic credential scrubbing (`skillsdb/memory/gitea_config.py`, `skillsdb/cli.py`)**:
  - Eliminated plaintext Gitea API tokens from `.git/config` remote URLs across all workspaces (`http://localhost:3000/<user>/<repo>.git` instead of `http://<user>:<token>@...`).
  - Added `_get_git_auth_cmd_prefix()` to pass transient `http.extraHeader=Authorization: Basic <base64>` headers during `skillsdb` Git pushes and tag syncs without writing credentials to disk.
  - Added `skillsdb gitea-credential-helper` and `register_git_credential_helper()` so manual `git push` / `git pull` terminal operations authenticate dynamically from `~/.gemini/database/customizations.db`.
  - Added `_scrub_workspace_git_config_credentials()` to automatically migrate and strip legacy embedded tokens from existing `.git/config` files.

### Changed
- **Modular decomposition of Gitea synchronization engine (`skillsdb/memory/gitea_config.py`, `skillsdb/memory/gitea_service.py`, `skillsdb/memory/gitea_releases.py`, `skillsdb/memory/gitea_sync.py`, `build.py`)**:
  - Split the 1,870-line `gitea_sync.py` monolith into four focused modules (`gitea_config.py`, `gitea_service.py`, `gitea_releases.py`, and `gitea_sync.py`) with full backward-compatible symbol re-exports and single-file bundling in `build.py`.

## [1.1.0] - 2026-10-21

### Added
- **Cross-platform Python installer for Linux/macOS/Windows (`install_antigraviton.py`, `skillsdb/memory/gitea_sync.py`)**:
  - Added `install_antigraviton.py` (`stdlib` only) supporting rootless `systemd --user` service and timer registration on Linux, `launchd` LaunchAgent provisioning on macOS, automatic CPU architecture detection (`amd64` / `arm64`), and cross-platform `app.ini` generation.
  - Extended `get_gitea_service_status()` and `ensure_gitea_online()` in `skillsdb/memory/gitea_sync.py` to detect and auto-recover `systemd --user` and `launchd` Gitea services on Linux and macOS alongside Windows `sc.exe`.
- **Unified multi-scenario PowerShell installer and automatic runtime bootstrap (`install-antigraviton.ps1`, `.agents/skills/install-antigraviton/SKILL.md`, `gitea-service/scripts/backup-gitea.ps1`)**:
  - Added automatic Step 0 runtime bootstrap in `install-antigraviton.ps1` that ensures PowerShell 7 (`pwsh.exe`) is installed via `winget`, configures `pwsh.exe` as the default terminal profile in Windows Terminal and Antigravity IDE, and automatically bootstraps standalone Python 3.12 via `uv` into `~/.local/bin` when Python 3 is missing or shadowed by the Windows 11 Microsoft Store alias stub.
  - Added `-GiteaAdminPassword` to `install-antigraviton.ps1` to support explicit administrator passwords on initial user creation or password updates on existing accounts (while preserving `--random-password` when omitted).
  - Added `-ServiceAccount` and `-ServicePassword` pass-through parameters for enterprise domain/UNC service deployments.
  - Added `gitea-service/scripts/backup-gitea.ps1` and optional `-BackupTargetPath` / `-BackupTime` parameters in `install-antigraviton.ps1` to register a daily `03:00` online `gitea.exe dump` Windows Scheduled Task (`Antigravity-Gitea-Backup`) with automated archive retention pruning.
  - Added a strict Step 5 verification gate in `install-antigraviton.ps1` that verifies `customizations.db`, the `gitea` Windows service state, and the local HTTP REST API before outputting the completion banner.

### Fixed
- **PowerShell 5.1 service installer parameter syntax, secondary drive NTFS ACLs, and native download speed (`gitea-service/scripts/deploy-gitea.ps1`, `install-antigraviton.ps1`, `deploy.py`)**:
  - Wrapped `(Split-Path -Parent $PSScriptRoot)` in parentheses inside `deploy-gitea.ps1` and `backup-gitea.ps1` `param(...)` blocks to prevent `MissingEndParenthesisInFunctionParameterList` parser errors under `powershell.exe -File`.
  - Added pre-service `app.ini` / `gitea.db` initialization and `icacls.exe` local drive Modify permission grants (`*S-1-5-32-545:(OI)(CI)M`) so installations on secondary drives (such as `D:\Gitea`) never fail with `SYSTEM`-only `SQLite` ownership locks during non-elevated user and token provisioning.
  - Enabled `$ProgressPreference = "SilentlyContinue"` and PowerShell 7 `HttpClient` downloads so `Invoke-WebRequest` downloads `gitea.exe` at native wire speed without requiring `curl.exe`.
  - Wrapped legacy plugin archiving moves in `deploy.py` in `try...except` so active MCP sidecar processes (`biomolecular_structure_viewer`) do not interrupt `config.json` and `.gitignore_global` deployment.

## [1.0.8] - 2026-10-19

### Added
- **Gitea repository collision guard and telemetry (`skillsdb/memory/gitea_sync.py`, `skillsdb/cli.py`, `plugin/rules/AGENTS.md`, `v3.5.6`)**:
  - Added `inspect_remote_repository()` and `init_workspace_gitea()` so `skillsdb gitea-init` and `skillsdb gitea-sync` automatically check whether a non-empty repository with the same name already exists on Gitea before linking or pushing an unlinked local folder.
  - When a name collision is detected, SkillsDB halts with `[GITEA REPOSITORY COLLISION]` (and exit code `2`) and instructs the agent to ask the user whether to reuse the existing repository (`--use-existing`), create a repository under a new name (`--name <NewRepoName>`), or recreate it cleanly (`--overwrite`).
  - Enhanced `get_workspace_git_telemetry()` (`skillsdb mem-get-context` and Turn-1 `PreInvocation` hook) to display the exact configured Gitea remote URL (`synced with Gitea: http://127.0.0.1:3000/<user>/<repo>`) or emit `[GITEA COLLISION ALERT]` if an unlinked local workspace shares its name with an existing Gitea repository.
- **Dedicated release publishing command and ahead-of-tag reminder (`skillsdb/memory/gitea_sync.py`, `skillsdb/cli.py`)**:
  - Added `publish_gitea_release()` and the `skillsdb gitea-release [vX.Y.Z] [--title] [--notes] [--github]` CLI subcommand to create or update annotated Git tags and Keep a Changelog releases on Gitea (and optionally GitHub).
  - Updated `sync_workspace()` to emit a `[RELEASE REMINDER]` whenever `HEAD` has commits ahead of the latest release tag defined in `VERSION` / `CHANGELOG.md`.

### Fixed
- **Explicit commit message preservation after milestone snapshots (`skillsdb/memory/gitea_sync.py`)**:
  - Fixed a race where running `skillsdb mem-save-snapshot "<summary>"` (which auto-commits with `[SkillsDB] <summary>`) immediately followed by `skillsdb gitea-sync -m "<explicit message>"` saw a clean working tree and ignored the explicit `-m` commit message. When `save_code_commit(..., explicit_message=True)` is called on a clean tree within 120 seconds of a snapshot commit, it now amends `HEAD` with the explicit commit message while preserving episodic trailers.
- **Windows SCM auto-recovery on pre-existing `gitea` services and bot avatar email linking (`install-antigraviton.ps1`)**:
  - Updated `install-antigraviton.ps1` to enforce `sc.exe failure gitea reset= 86400 actions= restart/5000/restart/10000/restart/30000` and `sc.exe failureflag gitea 1` even when an existing `gitea` Windows service is already registered on the machine, ensuring automatic service restart after unexpected process terminations (Event ID `7034`).
  - Added automatic `email_address` linking for `antigravity@local.dev` on the `Antigravity` account in `gitea.db` so commits authored by `Antigravity <antigravity@local.dev>` always render the custom `Antigravity` profile avatar in Gitea.

## [1.0.7] - 2026-10-19

### Added
- **Mandatory Keep a Changelog 1.1.0 enforcement and automated Gitea/GitHub release synchronization (`skillsdb/core/guardrails.py`, `skillsdb/memory/gitea_sync.py`, `skillsdb/memory/project_memory.py`, `plugin/rules/AGENTS.md`, `v3.5.5`)**:
  - Anchored [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/) alongside [Semantic Versioning 2.0.0](https://semver.org/) as a mandatory global rule across `customizations.db` (`semver-versioning`), `plugin/rules/AGENTS.md`, `plugin/skills/customizations-db/SKILL.md`, and the Turn-1 `PreInvocation` hook guardrail.
  - Added deterministic `keep-a-changelog` guardrail validation in `skillsdb/core/guardrails.py` (`check_text()` and `check_file()`) enforcing `## [X.Y.Z] - YYYY-MM-DD` version headers and strictly official `### Added`, `### Changed`, `### Deprecated`, `### Removed`, `### Fixed`, and `### Security` category headings in `CHANGELOG.md`.
  - Added `parse_keep_a_changelog()` and `sync_changelog_releases()` in `skillsdb/memory/gitea_sync.py` (integrated into `skillsdb gitea-sync [--releases] [--github]`) to automatically synchronize `CHANGELOG.md` entries as formal Releases on both Gitea and GitHub.
  - Added regression test `test_keep_a_changelog_guardrail_and_parser` in `tests/test_gitea_sync.py`.

## [1.0.6] - 2026-10-19

### Fixed
- **Automatic cleanup of legacy excluded non-workspaces and case duplicates during updates (`skillsdb/memory/gitea_sync.py`, `skillsdb/updater/merger.py`, `deploy.py`, `v3.5.4`)**:
  - Added `_is_excluded_workspace_path()` to `get_learned_workspaces()` so legacy protected paths stored in `runtime_config` prior to `v1.0.3` (such as `~/.gemini/config/plugins/customizations-db`, `~`, `~/.gemini`, or temp folders) are automatically pruned from `runtime_config` and any stray `.agents` or `.git` folders inside `~/.gemini/config/plugins/*` are automatically removed during deployment and updates.
  - Added case-insensitive workspace key deduplication (`workspace:<name>`) in `learn_workspace()` and `get_learned_workspaces()` on Windows so duplicate entries differing only in casing (e.g. `skillsdb` in `scratch` vs. `SkillsDB` in primary workspace) are automatically consolidated in favor of the primary project workspace.
  - Updated `get_latest_release_info()` and `update_plugin()` in `skillsdb/updater/merger.py` to query `scorpion421/AntiGraviton` releases directly and run automatic workspace pruning during `skillsdb update`.
  - Added regression test `test_prune_excluded_and_duplicate_workspaces_from_runtime_config` in `tests/test_gitea_sync.py`.

## [1.0.5] - 2026-10-18

### Fixed
- **Partial and uninitialized `.git` directory self-healing across all workspaces (`skillsdb/memory/gitea_sync.py`, `skillsdb/memory/project_memory.py`, `v3.5.3`)**:
  - Fixed a bug where partial `.git` directory remnants on Windows (caused when read-only `.git/objects/pack/*.pack` files survive directory deletion while `.git/HEAD` is removed) caused `setup_workspace_git()` to skip `git init -b main` because `(project_root / ".git").exists()` evaluated to `True`, resulting in `fatal: not a git repository (or any of the parent directories): .git`.
  - Updated `setup_workspace_git()`, `save_code_commit()`, `get_workspace_git_telemetry()`, `flush_all_pending_pushes()`, `learn_workspace()`, and `discover_all_workspaces()` to validate `(project_root / ".git" / "HEAD").exists()` and `git rev-parse --is-inside-work-tree`, automatically clearing Windows `ReadOnly` attributes (`_clear_readonly_in_git()`) and re-running `git init -b main` whenever a repository is missing or corrupted.
  - Updated `handle_pre_invocation_hook()` and `mem_init()` to automatically initialize a local Git repository (`setup_workspace_git(root_path, configure_remote=False)`) whenever an uninitialized project workspace is opened in Antigravity IDE, preventing `fatal: not a git repository` errors when agents or IDE tools run `git status` before the first milestone commit.
  - Added regression test `test_partial_or_broken_dot_git_auto_healing` in `tests/test_gitea_sync.py`.

## [1.0.4] - 2026-10-17

### Added
- **Zero-prompt Gitea CLI routing in plugin rules, skill runbook, and Turn-1 hook (`plugin/rules/AGENTS.md`, `plugin/skills/customizations-db/SKILL.md`, `skillsdb/memory/project_memory.py`, `v3.5.2`)**:
  - Added Section 8 (`Local Gitea backup and repository synchronization`) to `plugin/skills/customizations-db/SKILL.md` and explicit Gitea CLI instructions (`skillsdb gitea-init "<target_directory>"`, `skillsdb gitea-sync`, `skillsdb gitea-status`, `skillsdb gitea-config`) to `plugin/rules/AGENTS.md` and the Turn-1 `PreInvocation` hook guardrail so agent sessions immediately use the built-in Gitea CLI without manually hunting for `.git/config` or tokens.
  - Added German and English cross-lingual FTS5 synonyms (`gitea`, `hochladen`, `sichern`, `versionierung`, `backup`) in `skillsdb/search/synonyms.py` so `skillsdb search gitea` and `skillsdb suggest` immediately rank the `customizations-db` Gitea workflow.

### Fixed
- **Automatic Git `safe.directory` self-healing on secondary and exFAT drives (`skillsdb/memory/gitea_sync.py`)**:
  - Added `_ensure_git_safe_directory()` in `setup_workspace_git()` and `save_code_commit()` so directories on file systems that do not record POSIX/NTFS ownership (e.g. `D:\` exFAT drives) automatically register in `git config --global --add safe.directory` instead of failing with `fatal: detected dubious ownership`.
- **Workspace learning order during initial Gitea repository creation (`skillsdb/memory/gitea_sync.py`)**:
  - Updated `sync_workspace()` to execute `setup_workspace_git()` (creating `.git`) before calling `learn_workspace()` so brand-new standalone folders initialized via `skillsdb gitea-init "<path>"` are immediately registered in `runtime_config` (`workspace:<name>`) and display the clickable Gitea repository URL upon completion.
- **YAML block scalar frontmatter parsing (`skillsdb/updater/merger.py`)**:
  - Updated `parse_skill_frontmatter()` to cleanly strip multi-character YAML block scalar indicators (`>-`, `|-`, `>+`, `|+`) across both LF and CRLF files.
- **Natural sentence case across plugin documentation (`plugin/rules/AGENTS.md`, `plugin/skills/customizations-db/SKILL.md`, `README.md`, `CHANGELOG.md`)**:
  - Converted all remaining newspaper-style Title Case headings and bold labels in `plugin/rules/AGENTS.md` and `plugin/skills/customizations-db/SKILL.md` to natural sentence case.

## [1.0.3] - 2026-10-17

### Added
- **Technical deep-dive on Antigravity IDE token traps and client bugs (`README.md`)**:
  - Documented the 5 major Antigravity IDE client pitfalls (35,000-token static plugin bloat, the `2026-10-16` `PreInvocation` `proto3` and KV-cache trap, hook `cwd` escalation without an open workspace, `Gemini 4 Argon` model renaming, and Windows UTF-8 manifest resets on IDE updates) and how AntiGraviton shields against them.
- **AI commit author attribution (`skillsdb/memory/gitea_sync.py`)**:
  - Updated `save_code_commit()` to explicitly pass `-c user.name=Antigravity -c user.email=antigravity@local.dev --author="Antigravity <antigravity@local.dev>"` so AI-authored milestone commits are always attributed to the `Antigravity` account (`antigravity@local.dev`) even in pre-existing or cloned repositories.

### Fixed
- **Antigravity post-update `PreInvocation` hook deduplication (`skillsdb/memory/project_memory.py`, `v3.5.1`)**:
  - Following the Google Antigravity IDE update (`2026-10-16`), `PreInvocation` hooks fire before every internal tool step inside a turn with `invocationNum <= 1` (`proto3` omits `0` on call #1 and sends `1` on call #2), and each returned `ephemeralMessage` is permanently persisted into `transcript.jsonl` as a `SYSTEM_SDK` step.
  - Full transparency: Any hook relying on Google's documented `invocationNum == 1` check (including AntiGraviton `v1.0.2`) was triggered on every tool step, breaking tail KV-cache continuity and adding ~2-3s latency per tool call.
  - Added independent sub-millisecond `(conversationId, workspace)` state deduplication (`skillsdb_hook_state.json`) so Turn-1 guardrails and project memory context are injected strictly once per conversation/workspace transition (`< 1 ms` and `0` duplicate tokens on all subsequent tool calls).
- **System and user profile non-workspace boundary hardening (`skillsdb/memory/project_memory.py`, `skillsdb/memory/gitea_sync.py`)**:
  - Hardened `_is_protected_non_workspace()`, `find_project_root()`, `mem_init()`, and `learn_workspace()` so user home (`Path.home()`), `~/.gemini`, `~/.gemini/config/plugins/*` (where Antigravity executes plugin hooks when `workspacePaths` is empty), `~/.gemini/antigravity/scratch` root, and OS temp root are never treated as project workspaces.
- **Zero-latency hook Git telemetry (`skillsdb/memory/gitea_sync.py`)**:
  - Updated `get_workspace_git_telemetry()` so `flush_pending` defaults to `False` during normal hook telemetry checks while `ensure_gitea_online()` automatically flushes pending commits across all learned workspaces whenever the Gitea Windows service recovers from a `STOPPED` state.
- **Gemini 4 Argon (`TIER_ULTRA`) model detection (`skillsdb/core/detector.py`)**:
  - Updated `detect_model_tier()` to recognize `Gemini 4 Argon` (`argon`, `gemini 4`, `gemini-4`) as `TIER_ULTRA`, scan the last 4 MB of `transcript.jsonl` in reverse order for `<USER_SETTINGS_CHANGE>` model switches, and ignore false-positive `"flash"` substrings inside tool declarations.

## [1.0.2] - 2026-10-15

### Added
- **Antigravity scratch workspace auto-discovery (`skillsdb/memory/gitea_sync.py`)**:
  - `discover_all_workspaces()` now automatically scans `~/.gemini/antigravity/scratch` in addition to `~/.gemini/config/projects/*.json` and `workspaces_root`, ensuring all standard Antigravity scratch workspaces are discovered out of the box.
- **Automated regression tests (`tests/test_gitea_sync.py`)**:
  - Added `test_gitea_service_templates_and_relocation_regex` to verify LevelDB-free immediate queue settings in `app.example.ini` and `WORK_PATH` relocation self-healing in `deploy-gitea.ps1`.

### Fixed
- **Windows LevelDB queue lock prevention (`gitea-service` `v1.1.1`)**:
  - Added `[queue]` and `[queue.common]` with `TYPE = immediate` to `gitea-service/custom/conf/app.example.ini` and automatic injection into existing `app.ini` files via `gitea-service/scripts/deploy-gitea.ps1`. Prevents exclusive Windows LevelDB file lock errors (`LOCK: The process cannot access the file because it is being used by another process`) during `gitea.exe dump` backup operations.
  - Documented LevelDB lock troubleshooting (`Issue E`) in `gitea-service/INSTALL.md`.
- **Complete `WORK_PATH` and UNC path relocation self-healing (`gitea-service/scripts/deploy-gitea.ps1`)**:
  - Added automatic `WORK_PATH` rewriting when relocating an existing Gitea installation to a new drive or directory (e.g. moving between local SSD and corporate DFS shares).
  - Preserved native Windows backslashes for `WORK_PATH`, `ROOT`, `PATH`, and `ROOT_PATH` during relocation so UNC paths (`\\server\share\...`) never trigger `go-sqlite3: invalid URI authority` errors.
- **Guardrail negative-example exclusion (`skillsdb/core/guardrails.py`)**:
  - Excluded quoted negative documentation examples (e.g. `never "Abteilungs-Kontingent"`) from triggering false-positive `no-deppenbindestrich` warnings during repository audits.

## [1.0.1] - 2026-10-15

### Changed
- **Standalone local developer workstation default (zero Active Directory assumption)**:
  - Configured Gitea, SkillsDB rules (`local-admin`), and the `admin-elevation` skill to assume a standard local developer/administrator workstation by default without requiring Active Directory or `.adm` domain accounts.
  - Commented out `[cron.sync_external_users]` in `gitea-service/custom/conf/app.example.ini` as an optional setting for Active Directory / LDAP environments.
  - Updated `install-antigraviton.ps1` to automatically initialize `gitea.db` (`gitea.exe migrate`), create the local Windows user (`$env:USERNAME`) as a standalone Gitea administrator if not yet present, and generate a scoped API token on brand-new machines.
  - Updated `gitea-service/scripts/deploy-gitea.ps1` to automatically generate unique cryptographic secrets (`SECRET_KEY`, `INTERNAL_TOKEN`, `JWT_SECRET`) when creating `app.ini` from `app.example.ini`.
- **3-tier elevation handling in `install-antigraviton.ps1`**:
  - Tier 1: Direct execution if the current PowerShell session already runs with Administrator privileges.
  - Tier 2: Unattended execution via optional DPAPI credential file (`~/.gemini/config/credentials/*.credential.xml` or `~/.gemini/credentials/*.credential.xml`), supporting both standard admin accounts and `.adm` accounts.
  - Tier 3: Standard Windows UAC fallback (`Start-Process powershell.exe -Verb RunAs -Wait`).
- **Complete upgrade state preservation**:
  - `install-antigraviton.ps1` now preserves both `runtime_config` (Gitea URL, token, learned workspaces) and all user-learned rules (`category = 'learned'`) when upgrading an existing `~/.gemini/database/customizations.db`.
- **Mandatory post-install capability and configuration briefing**:
  - Enhanced `install-antigraviton.ps1` and `.agents/skills/install-antigraviton/SKILL.md` so the user is automatically informed after installation about all active capabilities and optional configurations (switching Gitea to Active Directory/LDAP, `User` vs. `User.adm` role separation, DPAPI background elevation, and multi-workspace synchronization).

### Fixed
- **Privacy and database sanitization**:
  - Removed personal learned rule (`git-author-pseudonym`) from the distributed `database/customizations.db` and vacuumed the SQLite file so zero personal names or company email addresses are shipped in the repository.
  - Replaced remaining domain-specific example identifiers in `gitea-service/INSTALL.md` with generic placeholders and restructured the guide into Standalone Mode (default) vs. Optional Enterprise AD/LDAP Mode.

## [1.0.0] - 2026-10-15

### Added
- **Unified AntiGraviton complete suite (`v1.0.0`)**: Combined the **SkillsDB context and memory engine (`v3.5.0`)** and the **Antigravity Gitea Windows service (`v1.1.0`)** into a single turnkey distribution and repository.
- **One-command / one-prompt unified installer (`install-antigraviton.ps1`)**:
  - Automates deployment of `customizations.db`, `db_manager.py`, the `customizations-db` Antigravity IDE plugin, and the global `skillsdb` CLI wrapper.
  - Preserves existing user credentials, learned workspaces, and Gitea tokens during upgrades.
  - Provisions or self-heals the local Gitea Windows service (`127.0.0.1:3000`) including Windows SCM auto-recovery (`15s/30s/60s`) and non-elevated user service control permissions (`sdset`).
  - Automatically generates and registers a Gitea API token into `skillsdb gitea-config` for zero-touch onboarding when a local `gitea.exe` instance is initialized.
- **Antigravity onboarding skill (`.agents/skills/install-antigraviton/SKILL.md`)**:
  - Allows any developer to install, repair, or upgrade the complete AntiGraviton ecosystem directly from the Antigravity IDE chat prompt.
- **Organic on-demand workspace learning**:
  - Ships with a sanitized zero-hardcoded-path database that dynamically learns project paths on first interaction (`skillsdb mem-init`, `skillsdb mem-save-snapshot`, `skillsdb gitea-init`) and discovers workspaces from `~/.gemini/config/projects/*.json`.
- **Multi-layered Gitea service fault-tolerance and catch-up sync**:
  - Automatic service restart on network/fileserver drops (`ensure_gitea_online()`), explicit offline telemetry alerts, and automatic cross-workspace catch-up pushing (`flush_all_pending_pushes()`).
