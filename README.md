# AntiGraviton complete suite (`v2.0.2`)

[![Version](https://img.shields.io/badge/version-2.0.2-blue.svg)](CHANGELOG.md)
[![SkillsDB](https://img.shields.io/badge/SkillsDB-v4.0.2-007ec6.svg)](skillsdb/)
[![Gitea Service](https://img.shields.io/badge/Gitea_Service-v1.1.2-green.svg)](gitea-service/)
[![Tests](https://img.shields.io/badge/tests-96%2F96%20passing-brightgreen.svg)](tests/)
[![Antigravity](https://img.shields.io/badge/compatible-Google%20Antigravity%202.0-orange.svg)](https://deepmind.google/)
[![Database](https://img.shields.io/badge/database-SQLite3%20FTS5%20%7C%20WAL-lightgrey.svg)](https://www.sqlite.org/)
[![Python](https://img.shields.io/badge/python-3.10%2B-brightgreen.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-AGPLv3_%2B_Commons_Clause-blue.svg)](LICENSE)

**Turnkey agentic development environment for Google Antigravity IDE**  
*Combining **SkillsDB (`v4.0.2`)** + **Antigravity Gitea Windows service (`v1.1.2`)** in a single one-command distribution.*

---

## Table of contents

1. [Executive summary](#1-executive-summary)
2. [Measured impact in daily development](#2-measured-impact-in-daily-development)
3. [Why AntiGraviton? Shielding against Antigravity IDE token traps and client bugs](#3-why-antigraviton-shielding-against-antigravity-ide-token-traps-and-client-bugs)
4. [One-prompt and one-command installation](#4-one-prompt-and-one-command-installation)
5. [Repository architecture](#5-repository-architecture)
6. [Essential CLI commands (`skillsdb`)](#6-essential-cli-commands-skillsdb)
7. [Versioning policy](#7-versioning-policy)
8. [License](#8-license)

---

## 1. Executive summary

**AntiGraviton** transforms Google Antigravity IDE from a stateless chat assistant into a persistent, self-healing, low-token agentic software engineering environment.

It bundles two tightly integrated systems into a single repository and installer:

1. **SkillsDB context and memory engine (`v4.0.2`)**:
   - **Multi-pool Golden-Ratio (`61.8%` / `85.0%`) agent control plane**: Governs `Ultra` (16 parallel reader threads) and `Pro` (8 parallel reader threads) coding subscriptions across all 4 quota pools (`Pool 1A: Gemini 4 Argon [Fast|Med|High]`, `Pool 1B: Gemini 3.8 Flash [Fast|Med|High]`, `Pool 1C: Claude Opus/Sonnet 5.5 [Fast|Med|High]`, `Pool 1D: GPT-OSS 120B [Medium]`, `Pool 2: @google/gemini-cli`, `Pool 3: AI Studio REST`, `Pool 4: gemini.google.com`), keeping 100% of code generation on `Gemini 4 Argon` (or `Claude 5.5`) while routing `research` subagents to `Flash` at zero extra turns via `PreToolUse` overwrite. Includes live 5-hour server-percentage calibration (`--calibrate-pct` / `--reset-calibration`), context-size turn weighting (`1.0x..1.35x`), and cross-workstation heartbeat sync (`quota_workstation_heartbeats`).
   - **Decorator-aware AST contract mapper (`skillsdb ast-map`), surgical FIM slicer (`skillsdb fim slice`), and Context-Shield (`skillsdb gemini-digest`)**: Extracts exact class, method, and function signatures with `[Lstart-Lend]` line boundaries across Python (including `@decorator` spans, conditional `if`/`try`/`with` top-level blocks, and `ClassName.method_name` resolution) and 9 additional languages (TypeScript/JavaScript, PowerShell, Go, Rust, C#, Java, Kotlin, Dart, and SQL via `_find_block_end_line`) at ~95-98% token compression in ~50-70ms before editing, offloads large log/diff digests, and keeps hook latency `< 5-30 ms` on multi-megabyte transcripts via `O(1)` binary `SEEK_END` tail-seeks (`read_jsonl_tail_lines`).
   - **On-demand micro-skill routing and 15-tool `stdio` MCP server (`skillsdb mcp-serve`)**: Replaces static plugin prompt bloat (~35,000 tokens/turn) with a compact (~450 token) routing directive, Antigravity `v2.28.0+` Turn-1 prompt pre-routing, surgical SQLite FTS5 micro-skill retrieval (`--summary`, `--section`), and a zero-dependency 15-tool `stdio` MCP server.
   - **Persistent episodic project memory (`.agents/memory.db`)**: Automatically records structural file/offset facts (`mem-save-fact`), returned `[SUBAGENT REPORT]` findings, multi-step work items (`mem-task-add`), architectural decisions, and milestone snapshots (`mem-save-snapshot`), and re-hydrates them every 50 steps so mid-session IDE `<CONTEXT_SUMMARY>` compactions never cause the model to re-read files repeatedly.
   - **Organic on-demand workspace learning and transcript inference**: Ships with zero hardcoded paths or credentials. Automatically learns project paths as you work (`mem-init`, `mem-save-fact`, `mem-save-snapshot`, `gitea-init`), discovers Antigravity IDE workspaces from `~/.gemini/config/projects/*.json` and `~/.gemini/antigravity/scratch`, and infers the active project root directly from `transcript.jsonl` file paths when no formal IDE folder is open (`workspacePaths: []`).
2. **Antigravity Gitea local Windows service (`v1.1.2`)**:
   - **Standalone developer default (zero Active Directory dependency)**: Works out of the box on any Windows developer machine using your local Windows username (`$env:USERNAME`), automatically generating cryptographic config secrets, provisioning your local Gitea admin account, and binding a scoped API token into SkillsDB. Optional Active Directory (LDAP) and `User` vs. `User.adm` role separation can be enabled on demand at any time.
   - **Isolated loopback Git server (`127.0.0.1:3000`)**: Sub-second (`< 0.5s`) automated Git backup for every milestone snapshot and file checkpoint without external cloud latency, pre-configured with LevelDB-free immediate queues (`TYPE = immediate`) and SQLite database issue indexing (`ISSUE_INDEXER_TYPE = db`) for conflict-free `gitea dump` backups on Windows.
   - **Multi-layered fault-tolerance and auto-recovery**: Configured with Windows Service Control Manager (SCM) auto-restart (`15s/30s/60s`), non-elevated interactive user start/stop permissions (`sc.exe sdset`), autonomous agent service wake-up (`ensure_gitea_online()`), and cross-workspace catch-up synchronization (`flush_all_pending_pushes()`).

---

## 2. Measured impact in daily development

| Metric | Standard Antigravity setup | AntiGraviton suite (`v2.0.2`) | Real-world improvement |
| :--- | :--- | :--- | :--- |
| **System and plugin context load** | ~35,000 tokens / turn | **~1,500 tokens / turn** | **~95% lower base overhead** |
| **Token consumption (60-turn session, benchmarked on Gemini 4 Argon High)** | ~3.39M input tokens | **~0.83M input tokens** | **~75.5% total token savings (4x session capacity)** |
| **Marathon deep-analysis sessions (9h / 1,249 turns / 3,175 steps)** | ~99% weekly quota (raw IDE) / 33% (`v1.4.2`) | **~10-12% weekly quota (`v2.0.2`)** | **Golden-Ratio 4-pool control plane, 800-line read budget, subagent firewall & 50-step `memory.db` re-hydration** |
| **Pre-edit symbol and signature discovery (`skillsdb ast-map` & `fim slice`)** | Full-file reads (`500-2,000` lines / call) | **~95-98% token compression in ~50 ms** | **Exact `[Lstart-Lend]` spans across Python (decorator-aware) & 9 languages without full-file reads** |
| **Subagent routing and 5-hour rolling quota control (`quota_router.py`)** | `Model="inherit"` burns `Pool 1A` (`Argon High`, `3.5x`) on research | **0-turn `PreToolUse` `research -> flash` rewrite (`Pool 1B`, `0.35x`)** | **100% `Argon` / `Claude 5.5` for coding, `-90%` subagent quota weight, live `--calibrate-pct` & cross-PC heartbeat sync** |
| **Hook latency on multi-MB transcripts (`> 2.4 MB` / `2,000+` steps)** | Sequential full-file JSONL scan (`200-500 ms` / step) | **`< 5-30 ms` via `O(1)` binary `SEEK_END` + stat cache** | **Zero hook slowdown in marathon sessions (`read_jsonl_tail_lines`, `_BACKPACK_CACHE`, `_TRANSCRIPT_5H_CACHE`)** |
| **Context retention after `/compact` or mid-session `<CONTEXT_SUMMARY>`** | 0% (repeated file re-reading) | **100% automatic (`memory.db` + 50-step re-hydration)** | **Zero architectural or mid-session amnesia** |
| **Backup and rollback granularity** | Manual commits only | **Every milestone automatically pushed (`< 0.5s`)** | **Zero code loss and instant Git history forensics** |
| **Fileserver and network drop resilience** | Silent push failure | **3-tier SCM + agent auto-restart and catch-up sync** | **100% self-healing synchronization** |

> **Why high-reasoning models (`Gemini 4 Argon High`) amplify these savings**: Frontier reasoning models generate deep internal thinking chains (`thinking` tokens) and execute multi-step autonomous tool trajectories within a single user turn. Because every internal tool-call step re-evaluates the active system and conversation context, eliminating ~33,500 tokens of static plugin bloat per step prevents compound token burn across multi-step reasoning loops while keeping model attention focused strictly on active code.

---

## 3. Why AntiGraviton? Shielding against Antigravity IDE token traps and client bugs

Beyond providing persistent project memory and automated Gitea backups, **AntiGraviton** acts as an active protective layer between the Google Antigravity IDE client and frontier models (`Gemini 4 Argon`, `Gemini 3.8 Flash`). Real-world telemetry (`transcript.jsonl` inspection) revealed several IDE client behaviors that silently multiply token consumption and latency if left unmitigated:

### A. The 35,000-token static plugin bloat
* **The IDE default**: Out of the box, Antigravity enables 13 bundled plugins (`firebase`, `flutter`, `android-cli-plugin`, `chrome-devtools-plugin`, `data-agent-kit-plugin`, `science`, etc.) and their MCP servers simultaneously. This injects ~35,000 tokens of static instructions and tool schemas into every single model call.
* **How AntiGraviton fixes it**: Physically archives unused default plugins outside the active discovery path (`~/.gemini/plugins_archive`) and indexes all 120+ domain skills and rules inside `customizations.db`. The model receives a ~450-token routing directive and fetches only the exact micro-skill section (`skillsdb get-skill <name> --section "<Section>"`) needed for the current task.

### B. The `PreInvocation` hook `proto3` and KV-cache trap (introduced in IDE update `2026-10-16`)
* **What changed in Antigravity (`2026-10-16`)**:
  1. Antigravity's built-in documentation (`hooks.md`) advises plugin authors to inspect `invocationNum` to run initialization logic only once on Turn 1.
  2. After the `2026-10-16` IDE update, `PreInvocation` fires before **every internal tool step** within a turn, and due to `proto3` JSON serialization omitting default `0` values on call #1 and sending `"invocationNum": 1` on call #2, `payload.get("invocationNum", 1)` evaluates to `1` on **every tool call**.
  3. Worse, Antigravity persists every `ephemeralMessage` returned by `PreInvocation` permanently into `transcript.jsonl` as a `SYSTEM_SDK` (`EPHEMERAL_MESSAGE`) step.
* **The impact (full transparency)**:
  - Any standard hook relying on Google's documented `invocationNum == 1` check (including AntiGraviton `v1.0.2` prior to `v1.0.3`) was triggered on every single tool call inside a multi-step turn.
  - Even though AntiGraviton's ~600-token hook payload was still far smaller than standard Antigravity's 35,000-token baseline, injecting an `EPHEMERAL_MESSAGE` into the tail of `transcript.jsonl` on every tool step invalidated the LLM's tail prompt KV-cache (causing `input_tokens` to spike to 10k-13k per tool step) and added ~2-3 seconds of subprocess overhead per tool call.
* **How AntiGraviton `v1.0.3+` fixes it**:
  - Completely bypasses Google's broken `invocationNum` counter by maintaining an independent, sub-millisecond state tracker (`skillsdb_hook_state.json`) keyed by `(conversationId, workspace)`.
  - Guardrails and project memory are injected **strictly once** per conversation (or workspace switch). All subsequent tool steps and turns within that conversation return `{"injectSteps": []}` in `< 1 ms`, restoring 100% tail KV-cache continuity.

### C. Hook and terminal working directory (`cwd`) escalation when no workspace is open (`v2.26.0+`)
* **The IDE behavior**: Antigravity executes plugin hooks with `cwd` set to `~/.gemini/config/plugins/customizations-db`, and in Antigravity `v2.26.0+`, terminal commands spawned outside of a project (`ANTIGRAVITY_PROJECT_ID=outside-of-project`) start directly inside the application installation folder (`%LOCALAPPDATA%\Programs\antigravity`). Naive directory detection would mistake either the user profile (`C:\Users\<User>`) or the Antigravity binary directory itself for a software project.
* **How AntiGraviton `v1.4.0+` fixes it**: Enforces a comprehensive `_is_protected_non_workspace()` and `_is_excluded_workspace_path()` boundary guard blocking `Path.home()`, `~/.gemini/*`, the `~/.gemini/antigravity/scratch` container root, OS temp directories, and all application installation roots (`%LOCALAPPDATA%\Programs\*`, `%APPDATA%\Antigravity`, `%ProgramFiles%`, and `ANTIGRAVITY_AGENTAPI_EXE` parent paths) from ever being scanned or initialized as a project workspace.

### D. Frontier model renaming (`Gemini 4 Argon`, `Gemini 4 Carbon`, `Claude Opus 5.5`) and reverse transcript inspection
* **The IDE behavior**: When Google introduces new frontier model identifiers (such as `Gemini 4 Argon`, `Gemini 4 Carbon`, `gemini-3.6-flash`, and Vertex AI `Claude Opus 5.5` / `Claude Sonnet 5.5` in `v2.24.0` through `v2.28.0`) or when a developer switches models mid-session via the IDE dropdown, a `<USER_SETTINGS_CHANGE>` event is appended to `transcript.jsonl`.
* **How AntiGraviton `v1.4.2+` fixes it**: `detect_model_tier()` scans the tail of `transcript.jsonl` in reverse chronological order for `<USER_SETTINGS_CHANGE>` events and maps `Gemini 4 Argon`, `Gemini 4 Carbon`, and `Claude Opus 5.5` (`argon`, `carbon`, `gemini 4`, `claude opus`) directly to `TIER_ULTRA` (16-thread high-concurrency mode), `Claude Sonnet 5.5` and `Gemini Pro` to `TIER_STANDARD`, and `Gemini 3.8/3.6 Flash` and `Flash Lite` presets to `TIER_LEAN`.

### E. Windows UTF-8 manifest reset, builtin skill drift, and `v2.27.0+` tool schema updates
* **The IDE behavior**: Every Antigravity IDE update replaces `Antigravity.exe` and `language_server.exe` (wiping out external `<activeCodePage>UTF-8</activeCodePage>` `.manifest` files so Windows child processes fall back to legacy codepages `cp1252`/`cp850`), updates bundled skills under `~/.gemini/antigravity/builtin/skills` (`builtin/.checksum`), and in `v2.27.0`/`v2.28.0` transitioned file modification tools to `write_file` (`Path`, `Content`) and `edit_file` (`Path`, `Replacement`) while adding user prompt pass-through to `PreInvocation` hooks.
* **How AntiGraviton `v1.4.2+` fixes it**: On Turn 1 (`handle_pre_invocation_hook()`), SkillsDB automatically verifies and restores missing Windows UTF-8 `.manifest` files (`ensure_windows_utf8_manifests()`), synchronizes updated Antigravity builtin skills and their `docs/*.md` and `references/*.md` sub-documents into `customizations.db` (`sync_builtin_skills()`), pre-routes domain micro-skills directly from the incoming `v2.28.0` user prompt, and enforces deterministic `PreToolUse` guardrails across both modern (`write_file`, `edit_file`) and legacy (`write_to_file`, `replace_file_content`, `multi_replace_file_content`) tool schemas.

### F. Salami-slicing file reads and main-context search pollution in marathon runs (`v1.5.0+`)
* **The IDE behavior**: During complex multi-hour debugging or binary/Smali reverse-engineering sessions (1,000+ model turns), models naturally tend to inspect files in tiny sequential 80-120 line slices (`read_file`) and run dozens of exploratory `grep` and shell commands directly in the main conversation context. Because every tool step re-bills the entire accumulated prompt history, slicing a single 600-line file into 7 sequential calls multiplies context token billing by 7x and rapidly fills the context window.
* **How AntiGraviton `v1.5.0+` fixes it**: Enforces a strict **turn-economy and context hygiene protocol** via Turn-1 guardrails and active drift monitoring: files `<= 800 lines` must be read completely in a single call, files `> 800 lines` must be targeted via grep first and read in parallel within a single turn, and open-ended root-cause exploration (`>= 4` search steps or `> 5` files) must be delegated to an isolated `research` subagent (`invoke_subagent`) that returns only exact file paths, line offsets, and the root cause to the main context.

### G. Mid-session `<CONTEXT_SUMMARY>` compaction amnesia and `workspacePaths: []` blindness (`v1.5.5+`)
* **The IDE behavior**:
  1. When a developer works on a standalone directory (for example `D:\Android`) without formally opening it via *File -> Open Folder* in the IDE, Antigravity passes `"workspacePaths": []` in the hook payload. Without workspace inference, `.agents/memory.db` in that directory is never discovered or hydrated.
  2. In multi-hour sessions, Antigravity periodically truncates the conversation history via `<CONTEXT_SUMMARY>` compactions. If `.agents/memory.db` was only injected on Turn 1, any compaction after step 100 wipes the injected project memory from the active context window, forcing the model to re-read the same scripts and source files up to 25-29 times across a 9-hour session.
* **How AntiGraviton `v1.5.5+` fixes it**:
  - **Transcript-based workspace inference (`inferred_workspace`)**: When `workspacePaths` is empty, `handle_pre_invocation_hook()` inspects recent file paths in `transcript.jsonl` and automatically binds to the active project root (prioritizing directories containing `.agents/memory.db` or `.git`).
  - **50-step re-hydration and repeat-read circuit breaker**: Automatically re-injects `=== PROJECT MEMORY CONTEXT (Auto-Hydrated from .agents/memory.db) ===` every 50 steps and immediately triggers a `[PROJECT MEMORY ALERT]` whenever the same file is read `>= 3` times within 30 steps or fewer than 5 structural facts are indexed by step 25, enforcing immediate `skillsdb mem-save-fact` and `skillsdb mem-task-add` persistence.

### H. Unmanaged 5-hour quota burn, blind full-file reads, and multi-megabyte transcript hook lag (`v2.0.0+` / `v2.0.1+`)
* **The IDE behavior**:
  1. By default, Antigravity's `invoke_subagent` tool sets `Model` to `"inherit"`. When the main coding agent runs on `Gemini 4 Argon High` (`3.5x` quota weight in `Pool 1A`), every read-only `research` subagent inherits `Argon High` unless explicitly overridden, rapidly draining the 5-hour rolling quota window on file searches.
  2. Before editing a class or function in a large file (`> 250` lines), models frequently read hundreds of unrelated lines just to locate method signatures and line numbers, and when switching between Home and Enterprise workstations, local transcript counters have no awareness of server-side quota consumed on the other machine.
  3. As a session grows past 1,500-2,500 steps (`> 2.4 MB` `transcript.jsonl`), scanning the full JSONL transcript on every `PreToolUse` and `PreInvocation` hook call introduces cumulative I/O latency.
* **How AntiGraviton `v2.0.1` (`SkillsDB v4.0.1`) fixes it**:
  - **Golden-Ratio (`61.8%` / `85.0%`) 4-pool control plane & 0-turn `PreToolUse` subagent router**: Automatically intercepts `invoke_subagent(TypeName="research")` in `PreToolUse` and rewrites `Model="inherit"` to `Model="flash"` (`Pool 1B`, `0.35x` weight) at zero extra turns while keeping 100% of code generation (`write_file`, `edit_file`, `TypeName="self"`) on `Gemini 4 Argon` (or `Claude 5.5`). Returned `[SUBAGENT REPORT]` file paths and line offsets are automatically persisted into `.agents/memory.db` on `PreInvocation`.
  - **Live 5-hour quota calibration & cross-workstation heartbeat sync**: Combines context-size turn weighting (`1.0x..1.35x`), manual server-percentage anchoring (`skillsdb quota-status --calibrate-pct <0..100>` with linear 5-hour decay), and cross-workstation heartbeat merging (`runtime_config.quota_workstation_heartbeats` via `skillsdb sync`).
  - **Decorator-aware AST contract mapper (`ast-map`), surgical FIM (`fim slice`), and Context-Shield (`gemini-digest`)**: Extracts `[Lstart-Lend]` symbol contracts across Python (including `@decorator` lines, `if`/`try`/`with` top-level blocks, and `ClassName.method_name` resolution) and 9 additional languages (`_find_block_end_line` for TS/JS, PowerShell, Go, Rust, C#, Java, Kotlin, Dart, and SQL) at `95-98%` token compression in `~50 ms`, enforced by an AST-first `PreToolUse` check on files `> 250` lines.
  - **`O(1)` binary `SEEK_END` tail-seek (`read_jsonl_tail_lines`) & `(mtime_ns, size)` stat caching**: Replaces full-file JSONL scans with binary tail seeks and in-memory stat caches (`_BACKPACK_CACHE`, `_TRANSCRIPT_5H_CACHE`), keeping hook latency under `5-30 ms` regardless of transcript size.

---

## 4. One-prompt and one-command installation

### Option A: Directly via Antigravity IDE chat (recommended)

Open the `AntiGraviton` folder in Google Antigravity IDE and type:

> *"Installiere AntiGraviton"* (or *"Install AntiGraviton"*)

Antigravity automatically reads [`.agents/skills/install-antigraviton/SKILL.md`](.agents/skills/install-antigraviton/SKILL.md), executes the unified installer, and presents a post-install briefing covering all active capabilities and optional customizations.

### Option B: Via PowerShell CLI

```powershell
powershell -ExecutionPolicy Bypass -File .\install-antigraviton.ps1
```

#### Installer options
```powershell
# Specify custom Gitea service path and immediately mirror all Antigravity IDE workspaces:
.\install-antigraviton.ps1 -GiteaInstallPath "C:\Gitea" -SyncAllWorkspaces

# Deploy only SkillsDB and the Antigravity plugin (without local Gitea service):
.\install-antigraviton.ps1 -SkipGiteaService
```

> **Non-destructive upgrades and 3-tier elevation**: Running `install-antigraviton.ps1` on an already configured machine automatically backs up and preserves your existing `runtime_config` (Gitea token, URL, learned workspaces) and all user-learned rules (`category = 'learned'`). Initial Windows service creation automatically uses direct admin rights if elevated, optional DPAPI credentials (`*.credential.xml`), or a one-time standard Windows UAC prompt.

---

## 5. Repository architecture

```text
AntiGraviton/
|-- LICENSE                        # GNU AGPL v3.0 + Commons Clause v1.0 (no commercial resale)
|-- VERSION                        # SemVer 2.0.0 suite release version (2.0.2)
|-- CHANGELOG.md                   # SemVer release history
|-- README.md                      # Suite documentation
|-- install-antigraviton.ps1       # Unified 1-command installer (SkillsDB + Gitea + Auto-Token)
|-- deploy.py                      # Cross-platform SkillsDB deployer (Python)
|-- deploy.ps1                     # Windows SkillsDB deployer (PowerShell)
|-- build.py                       # Compiler bundling skillsdb/ package into database/db_manager.py
|-- .agents/
|   `-- skills/
|       `-- install-antigraviton/
|           `-- SKILL.md           # Antigravity IDE 1-prompt onboarding skill
|-- skillsdb/                      # Modular Python package source (v4.0.2)
|   |-- config.py                  # Paths, constants, and O(1) JSONL binary tail-seek
|   |-- schema.py                  # SQLite WAL + FTS5 schema & migrations
|   |-- guardrails.py              # Backwards-compatible guardrail shim
|   |-- cli.py                     # Unified CLI entrypoint & subcommands
|   |-- core/                      # Control plane, AST/FIM, quota router, guardrails, rules & skills
|   |   |-- detector.py            # Subscription tier, model family & 4-pool quota detection
|   |   |-- quota_router.py        # Golden-Ratio 5h router, calibration & cross-PC heartbeats
|   |   |-- fim.py                 # Decorator-aware Python AST & 10-language [Lstart-Lend] mapper
|   |   |-- gemini_offload.py      # Context-Shield (gemini-digest) external offload engine
|   |   |-- guardrails.py          # PreInvocation & PreToolUse hooks (subagent rewrite & AST check)
|   |   |-- rules.py               # Active rules, formatting checks & learned rule persistence
|   |   |-- skills.py              # Micro-skill FTS5 search, prompt pre-routing & cluster loader
|   |   |-- credentials.py         # DPAPI admin credentials & vault management
|   |   |-- sync.py                # Cross-workstation database & heartbeat synchronization
|   |   `-- doctor.py              # 9-point end-to-end control-plane & environment diagnostics
|   |-- memory/                    # Episodic project memory + self-healing Gitea/GitHub sync
|   |   |-- project_memory.py      # .agents/memory.db facts, tasks, snapshots & 50-step hydration
|   |   `-- gitea_sync.py          # Automated Git/Gitea/GitHub sync & Keep a Changelog releases
|   `-- platform/                  # IDE integration, 15-tool MCP server, plugins & UTF-8 manifests
|       |-- mcp_server.py          # Native 15-tool stdio JSON-RPC MCP server
|       |-- plugins.py             # Plugin bloat archiver & builtin skill synchronizer
|       |-- utf8.py                # Windows UTF-8 manifest verification & auto-repair
|       `-- workflows.py           # Legacy workflow-to-skill migration utilities
|-- database/
|   |-- customizations.db          # Sanitized central SQLite database (zero hardcoded paths)
|   `-- db_manager.py              # Standalone compiled CLI manager
|-- plugin/                        # Antigravity IDE customizations-db plugin
|-- gitea-service/                 # Antigravity Gitea Windows service distribution (v1.1.2)
|   |-- INSTALL.md
|   |-- scripts/
|   |   `-- deploy-gitea.ps1       # SCM service installer with auto-recovery & sdset ACLs
|   `-- custom/
|       `-- conf/
|           `-- app.example.ini    # Loopback-hardened Gitea configuration template
`-- tests/                         # Automated unit & integration test suite
```

---

## 6. Essential CLI commands (`skillsdb`)

Once installed, `skillsdb` is globally available in your PATH and inside every Antigravity terminal:

```powershell
# View database telemetry, 4-pool quota router status, and live 5h calibration
skillsdb stats
skillsdb quota-status --calibrate-pct 50
skillsdb quota-status --reset-calibration
skillsdb profile

# Extract multi-file AST contract map (Lstart-Lend) or FIM slice before coding
skillsdb ast-map "skillsdb/core/quota_router.py" "skillsdb/core/detector.py" --filter "detect"
skillsdb fim slice "skillsdb/core/guardrails.py" --symbol "handle_pre_tool_guardrail_hook"

# Offload large log, file, or git diff analysis (Context Shield)
skillsdb gemini-digest --files "logs/*.log" --query "Summarize root cause and line numbers"

# Start the native 15-tool stdio MCP server or run 9-point system diagnostics
skillsdb mcp-serve
skillsdb doctor
skillsdb fix-utf8

# View or configure Gitea synchronization endpoint & learned workspaces
skillsdb gitea-config
skillsdb gitea-config --url "http://127.0.0.1:3000" --user "<username>" --token "<api-token>"

# Initialize and upload any standalone folder or project to local Gitea
skillsdb gitea-init "<target_directory>"

# Initialize episodic memory (.agents/memory.db) and persist structural facts & tasks
skillsdb mem-init --project .
skillsdb mem-save-fact "hotspot:main_entry" "src/main.py:L1-L140" --project .
skillsdb mem-task-add "Refactor authentication handler" --project .

# Save a milestone snapshot (automatically commits, pushes to Gitea, and flushes pending repos)
skillsdb mem-save-snapshot "Implemented feature X"

# Synchronize current workspace or publish releases to Gitea and GitHub
skillsdb gitea-sync
skillsdb gitea-sync -m "Document architecture updates" --releases --github
skillsdb gitea-sync --all

# Run automated test suite and text/formatting guardrails
python -m unittest discover -s tests -v
skillsdb guardrail check --path .
```

---

## 7. Versioning policy

**AntiGraviton** strictly adheres to **[Semantic Versioning 2.0.0 (SemVer)](https://semver.org/)** and **[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)**:
- **`MAJOR`** (`X.0.0`): Incompatible architectural or CLI changes.
- **`MINOR`** (`1.X.0`): Backwards-compatible new features, skills, or synchronization capabilities.
- **`PATCH`** (`1.0.X`): Backwards-compatible bug fixes, security hardening, and rule refinements.

Published release tags are immutable and never overwritten in place; every update increments the version in [`VERSION`](VERSION), documents all changes in [`CHANGELOG.md`](CHANGELOG.md) using standard Keep a Changelog categories (`Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`), and publishes matching Releases on both Gitea and GitHub (`skillsdb gitea-sync --releases --github`).

---

## 8. License

Copyright (C) 2026 `scorpion421`.

**AntiGraviton** and **SkillsDB** are licensed under the **[GNU Affero General Public License v3.0 (AGPL-3.0-only) with Commons Clause License Condition v1.0](LICENSE)** (`AGPL-3.0-only WITH Commons-Clause-1.0`).
- **Free & Open Source with Mandatory Attribution**: You are free to use, study, share, and modify this software for personal, educational, and internal non-commercial use with full attribution to the original author (`scorpion421`).
- **Strict Copyleft**: Any derivative work, modification, or redistribution must be released in full source code form under the exact same license and Commons Clause condition.
- **Commercial Sale & Marketplace Resale Strictly Prohibited (Commons Clause v1.0)**: Repackaging, listing, or selling AntiGraviton, SkillsDB, or any of its `SKILL.md` workflows, rules, or scripts for a fee on commercial marketplaces (such as Agensi.io or similar platforms) or as a paid product/service is strictly prohibited.
