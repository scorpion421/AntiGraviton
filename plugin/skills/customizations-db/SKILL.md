---
name: customizations-db
description: >-
  Mandatory skill to query the centralized SQLite database (customizations.db) for active system rules,
  scripting standards, local admin elevation credentials, specialized workflow runbooks, and local Gitea repository sync.
  Use whenever starting tasks, writing scripts, uploading/syncing directories to Gitea, performing admin operations, or seeking domain skills.
---

# Central customizations database integration

All project rules, scripting standards, administrative credentials, Gitea synchronization configs, and workflow skills are maintained in the SQLite database at:
`~/.gemini/database/customizations.db`

The database contains all guidelines and skills previously loaded statically into Antigravity. It allows on-demand inspection without consuming context tokens in the system prompt.

## How to query the database

Execute these commands in pwsh via `run_command` whenever needed. Use the global CLI shortcut `skillsdb` (or fallback `python "$env:USERPROFILE\.gemini\database\db_manager.py"`):

### 1. Retrieve active global rules
```powershell
skillsdb get-active-rules
```
This outputs all mandatory rules:
* `global-formatting`: Universal output formatting invariants (strictly no emojis, strictly no em/en-dashes across all outputs).
* `communication-style`: Always informal German ("Du", never "Sie") with natural umlauts (ä, ö, ü, ß) and strict Komposita writing rules.
* `no-deppenbindestrich`: Strict German Komposita orthography: compound words must be joined as single words (strictly no Deppenbindestriche).
* `scripting-rules`: English only, proper error handling, strictly no VBScript.
* `local-admin`: Flexible admin elevation (direct execution when elevated, optional zero-UAC DPAPI credentials for local admin or `.adm` accounts, or standard UAC fallback).
* `token-efficiency`: Context hygiene, sliced file views, bounded command outputs, subagent isolation.
* `project-memory`: Persistent episodic memory management.
* `sentence-case-headings`: Natural sentence case in all headings, subheadings, and bold bullet labels.
* `git-commit-author-antigravity`: AI-authored commits attributed to `Antigravity <antigravity@local.dev>`.

### 2. Auto-suggest skills for any user task
```powershell
skillsdb suggest "<user task description>"
```
Rank-matches the best skills using FTS5 and returns quick summaries with direct view commands.

### 3. Search for rules or skills by keyword
```powershell
skillsdb search <query>
```
Example:
```powershell
skillsdb search flutter
skillsdb search bigquery
skillsdb search gitea
```

### 4. Retrieve full skill instructions / runbooks
```powershell
skillsdb get-skill <skill_name>
```
Example:
```powershell
skillsdb get-skill admin-elevation
skillsdb get-skill bigquery-sql
```

### 5. Export skill into a project workspace
When working inside a specific project that needs a particular skill loaded directly in its local `.agents/`:
```powershell
skillsdb export-skill <skill_name> <target_project_dir>
```

### 6. Project-level episodic memory (.agents/memory.db)
Each project maintains its own isolated SQLite memory database with FTS5 search and auto-vacuum. Databases auto-initialize on first write:

* **Retrieve project context (decisions and last snapshot)**:
  ```powershell
  skillsdb mem-get-context
  ```
* **Save architectural decision**:
  ```powershell
  skillsdb mem-save-decision "<title>" "<content>" --category "<cat>"
  ```
* **Save key-value fact**:
  ```powershell
  skillsdb mem-save-fact "<key>" "<value>"
  ```
* **Save milestone snapshot**:
  ```powershell
  skillsdb mem-save-snapshot "<summary>" --next-steps "<next>"
  ```
* **Task state machine (task board)**:
  ```powershell
  skillsdb mem-task-add "<title>" --priority high --status in_progress
  skillsdb mem-task-list [--status in_progress|pending|completed]
  skillsdb mem-task-update <id> --status completed
  skillsdb mem-task-clear
  ```
* **Autonomous context compaction**:
  ```powershell
  skillsdb mem-compact --summary "<milestone summary>" --next-steps "<next objectives>"
  ```
* **Search project memory**:
  ```powershell
  skillsdb mem-search "<query>"
  ```
* **Prune deleted conversations and vacuum**:
  ```powershell
  skillsdb mem-prune
  ```
* **Sync central database with team share**:
  ```powershell
  skillsdb sync push   # Push local DB updates to network share
  skillsdb sync pull   # Pull latest updates from network share
  ```

### 7. Adaptive model concurrency and Ultra batching
* **Check or set active model profile**:
  ```powershell
  skillsdb profile          # View active model profile and concurrency settings
  skillsdb profile set ultra
  skillsdb profile auto     # Reset to automatic transcript/runtime detection
  ```
* **Batch fetch multiple skills concurrently**:
  ```powershell
  skillsdb get-skills <skill1> <skill2> ... --summary
  ```
* **Prefetch pre-indexed domain cluster**:
  ```powershell
  skillsdb get-cluster flutter --summary
  skillsdb get-cluster data --json
  ```
* **Multi-query parallel search**:
  ```powershell
  skillsdb search-multi "<query1>" "<query2>" ...
  ```
* **Benchmark concurrency engine**:
  ```powershell
  skillsdb benchmark-concurrency --queries 20 --workers 16
  ```

### 8. Local Gitea backup and repository synchronization
SkillsDB includes built-in, zero-prompt integration with the local Gitea server (`http://localhost:3000`). Credentials and server URLs are stored in `runtime_config` inside `customizations.db` - you never need to hunt for tokens or `.git/config` files manually.

* **Initialize and upload any folder or project to Gitea (creates remote repo if missing, commits as `Antigravity <antigravity@local.dev>`, and pushes to `origin` and `gitea`)**:
  ```powershell
  skillsdb gitea-init "<target_directory>"
  ```
* **Commit, push changes, and auto-sync Keep a Changelog releases to Gitea (and GitHub)**:
  ```powershell
  skillsdb gitea-sync -m "<commit message>" --project "<target_directory>" [--releases] [--github]
  ```
* **Synchronize all discovered workspaces to Gitea at once**:
  ```powershell
  skillsdb gitea-sync --all
  ```
* **Check Gitea Windows service, API health, and hosted repositories**:
  ```powershell
  skillsdb gitea-status
  ```
* **Inspect or auto-heal Gitea configuration and workspace paths**:
  ```powershell
  skillsdb gitea-config --auto-detect
  ```
* **Mandatory Keep a Changelog 1.1.0 & SemVer 2.0.0 standard**: Every versioned project MUST maintain a `CHANGELOG.md` strictly formatted according to `https://keepachangelog.com/en/1.1.0/` (`## [X.Y.Z] - YYYY-MM-DD` with only `### Added`, `### Changed`, `### Deprecated`, `### Removed`, `### Fixed`, `### Security`) and `https://semver.org/`, and publish matching Releases on both Gitea and GitHub (`skillsdb gitea-sync --releases --github`).
* **Formatting rule**: Always present Gitea (`http://localhost:3000/...`) and GitHub URLs as clickable Markdown links `[Label](url)`, never as inline code.

