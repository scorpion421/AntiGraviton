# Changelog

All notable changes to the **AntiGraviton** project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
  - Removed the custom preamble above the canonical `GNU AFFERO GENERAL PUBLIC LICENSE` header in `LICENSE` and placed the `Copyright (C) 2026 scorpion421 (MickyFoley / MoerthJ)` attribution inside the standard GNU appendix block so GitHub's Licensee parser automatically displays the `AGPL-3.0` license badge.

## [1.4.10] - 2026-10-29

### Changed
- **Relicensed Under GNU Affero General Public License v3.0 (`LICENSE`, `README.md`, `plugin/plugin.json`, `plugin/README.md`, `v3.7.10`)**:
  - Replaced the permissive MIT license with the strict copyleft **GNU AGPL v3.0 (`AGPL-3.0-only`)** license so AntiGraviton and SkillsDB remain permanently free and open source with mandatory author attribution (`scorpion421`), preventing proprietary closed-source commercialization or relicensing by third parties.

## [1.4.9] - 2026-10-29

### Changed
- **Single-Source-of-Truth Version Synchronization in `build.py` (`build.py`, `skillsdb/config.py`, `v3.7.9`)**:
  - Added `sync_project_versions()` to `build.py` so updating `__version__` and `__suite_version__` in `skillsdb/config.py` and running `python build.py` automatically synchronizes `VERSION`, `plugin/plugin.json`, `plugin/README.md`, `README.md`, `install-antigraviton.ps1`, `install_antigraviton.py`, `.agents/skills/install-antigraviton/SKILL.md`, `database/db_manager.py`, and `runtime_config.schema_version` in `database/customizations.db`.
- **Site-Neutral Tracked `plugin/README.md` with Dynamic Deployment Enrichment (`plugin/README.md`, `deploy.py`)**:
  - Replaced the hardcoded site-specific Gitea username in `plugin/README.md` with a neutral `http://localhost:3000` service reference in Git while teaching `deploy.py` to dynamically inject the active machine's `gitea_user` (`MickyFoley` or `MoerthJ`) from `runtime_config` into `~/.gemini/config/plugins/customizations-db/README.md`.

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
