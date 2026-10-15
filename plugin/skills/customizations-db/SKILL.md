---
name: customizations-db
description: >-
  Mandatory skill to query the centralized SQLite database (customizations.db) for active system rules,
  scripting standards, local admin elevation credentials, and specialized workflow runbooks.
  Use whenever starting tasks, writing scripts, performing admin operations, or seeking domain skills.
---

# Central Customizations Database Integration

All project rules, scripting standards, administrative credentials, and workflow skills are maintained in the SQLite database at:
`~/.gemini/database/customizations.db`

The database contains all guidelines and skills previously loaded statically into Antigravity. It allows on-demand inspection without consuming context tokens in the system prompt.

## How to Query the Database

Execute these commands in pwsh via `run_command` whenever needed. Use the global CLI shortcut `skillsdb` (or fallback `python "$env:USERPROFILE\.gemini\database\db_manager.py"`):

### 1. Retrieve Active Global Rules
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

### 2. Auto-Suggest Skills for Any User Task
```powershell
skillsdb suggest "<user task description>"
```
Rank-matches the best skills using FTS5 and returns quick summaries with direct view commands.

### 3. Search for Rules or Skills by Keyword
```powershell
skillsdb search <query>
```
Example:
```powershell
skillsdb search flutter
skillsdb search bigquery
skillsdb search elevation
```

### 4. Retrieve Full Skill Instructions / Runbooks
```powershell
skillsdb get-skill <skill_name>
```
Example:
```powershell
skillsdb get-skill admin-elevation
skillsdb get-skill bigquery-sql
```

### 5. Export Skill into a Project Workspace
When working inside a specific project that needs a particular skill loaded directly in its local `.agents/`:
```powershell
skillsdb export-skill <skill_name> <target_project_dir>
```

### 6. Project-Level Episodic Memory (.agents/memory.db)
Each project maintains its own isolated SQLite memory database with FTS5 search and auto-vacuum. Databases auto-initialize on first write:

* **Retrieve Project Context (Decisions & Last Snapshot)**:
  ```powershell
  skillsdb mem-get-context
  ```
* **Save Architectural Decision**:
  ```powershell
  skillsdb mem-save-decision "<title>" "<content>" --category "<cat>"
  ```
* **Save Key-Value Fact**:
  ```powershell
  skillsdb mem-save-fact "<key>" "<value>"
  ```
* **Save Milestone Snapshot**:
  ```powershell
  skillsdb mem-save-snapshot "<summary>" --next-steps "<next>"
  ```
* **Task State Machine (Task Board)**:
  ```powershell
  skillsdb mem-task-add "<title>" --priority high --status in_progress
  skillsdb mem-task-list [--status in_progress|pending|completed]
  skillsdb mem-task-update <id> --status completed
  skillsdb mem-task-clear
  ```
* **Autonomous Context Compaction**:
  ```powershell
  skillsdb mem-compact --summary "<milestone summary>" --next-steps "<next objectives>"
  ```
* **Search Project Memory**:
  ```powershell
  skillsdb mem-search "<query>"
  ```
* **Prune Deleted Conversations & Vacuum**:
  ```powershell
  skillsdb mem-prune
  ```
* **Sync Central Database with Team Share**:
  ```powershell
  skillsdb sync push   # Push local DB updates to network share
  skillsdb sync pull   # Pull latest updates from network share
  ```

### 7. Adaptive Model Concurrency & Ultra Batching (v2.3.0)
* **Check/Set Active Model Profile**:
  ```powershell
  skillsdb profile          # View active model profile and concurrency settings
  skillsdb profile set ultra
  skillsdb profile auto     # Reset to automatic transcript/runtime detection
  ```
* **Batch Fetch Multiple Skills Concurrently**:
  ```powershell
  skillsdb get-skills <skill1> <skill2> ... --summary
  ```
* **Prefetch Pre-Indexed Domain Cluster**:
  ```powershell
  skillsdb get-cluster flutter --summary
  skillsdb get-cluster data --json
  ```
* **Multi-Query Parallel Search**:
  ```powershell
  skillsdb search-multi "<query1>" "<query2>" ...
  ```
* **Benchmark Concurrency Engine**:
  ```powershell
  skillsdb benchmark-concurrency --queries 20 --workers 16
  ```
