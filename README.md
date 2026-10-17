# AntiGraviton Complete Suite (`v1.0.3`)

**Turnkey Agentic Development Environment for Google Antigravity IDE**  
*Combining **SkillsDB (`v3.5.1`)** + **Antigravity Gitea Windows Service (`v1.1.1`)** in a single one-command distribution.*

---

## 1. Executive Summary

**AntiGraviton** transforms Google Antigravity IDE from a stateless chat assistant into a persistent, self-healing, low-token agentic software engineering environment.

It bundles two tightly integrated systems into a single repository and installer:

1. **SkillsDB Context & Memory Engine (`v3.5.1`)**:
   - **On-Demand Micro-Skill Routing**: Replaces static plugin prompt bloat (~35,000 tokens/turn) with a compact (~450 token) routing directive and surgical SQLite FTS5 micro-skill retrieval (`--summary`, `--section`).
   - **Persistent Episodic Project Memory (`.agents/memory.db`)**: Automatically records architectural decisions, milestone snapshots, and workspace telemetry across sessions so every new conversation starts with full project context.
   - **Organic On-Demand Workspace Learning**: Ships with zero hardcoded paths or credentials. Automatically learns project paths as you work (`mem-init`, `mem-save-snapshot`, `gitea-init`) and discovers Antigravity IDE workspaces from `~/.gemini/config/projects/*.json` and `~/.gemini/antigravity/scratch`.
2. **Antigravity Gitea Local Windows Service (`v1.1.1`)**:
   - **Standalone Developer Default (Zero Active Directory Dependency)**: Works out of the box on any Windows developer machine using your local Windows username (`$env:USERNAME`), automatically generating cryptographic config secrets, provisioning your local Gitea admin account, and binding a scoped API token into SkillsDB. Optional Active Directory (LDAP) and `User` vs. `User.adm` role separation can be enabled on demand at any time.
   - **Isolated Loopback Git Server (`127.0.0.1:3000`)**: Sub-second (`< 0.5s`) automated Git backup for every milestone snapshot and file checkpoint without external cloud latency, pre-configured with LevelDB-free immediate queues (`TYPE = immediate`) for conflict-free `gitea dump` backups on Windows.
   - **Multi-Layered Fault-Tolerance & Auto-Recovery**: Configured with Windows Service Control Manager (SCM) auto-restart (`15s/30s/60s`), non-elevated interactive user start/stop permissions (`sc.exe sdset`), autonomous agent service wake-up (`ensure_gitea_online()`), and cross-workspace catch-up synchronization (`flush_all_pending_pushes()`).

---

## 2. Measured Impact in Daily Development

| Metric | Standard Antigravity Setup | AntiGraviton Suite (`v1.0.3`) | Real-World Improvement |
| :--- | :--- | :--- | :--- |
| **System & Plugin Context Load** | ~35,000 tokens / turn | **~1,500 tokens / turn** | **~95% lower base overhead** |
| **Token Consumption (60-Turn Session, benchmarked on Gemini 4 Argon High)** | ~3.39M input tokens | **~0.83M input tokens** | **~75.5% total token savings (4x session capacity)** |
| **Context Retention After `/compact` or Restart** | 0% (manual re-briefing required) | **100% automatic (`memory.db` + Git log)** | **Zero architectural amnesia** |
| **Backup & Rollback Granularity** | Manual commits only | **Every milestone automatically pushed (`< 0.5s`)** | **Zero code loss & instant Git history forensics** |
| **Fileserver / Network Drop Resilience** | Silent push failure | **3-tier SCM + Agent auto-restart & catch-up sync** | **100% self-healing synchronization** |

> **Why High-Reasoning Models (`Gemini 4 Argon High`) Amplify These Savings**: Frontier reasoning models generate deep internal thinking chains (`thinking` tokens) and execute multi-step autonomous tool trajectories within a single user turn. Because every internal tool-call step re-evaluates the active system and conversation context, eliminating ~33,500 tokens of static plugin bloat per step prevents compound token burn across multi-step reasoning loops while keeping model attention focused strictly on active code.

---

## 3. Why AntiGraviton? Shielding Against Antigravity IDE Token Traps & Client Bugs

Beyond providing persistent project memory and automated Gitea backups, **AntiGraviton** acts as an active protective layer between the Google Antigravity IDE client and frontier models (`Gemini 4 Argon`, `Gemini 3.8 Flash`). Real-world telemetry (`transcript.jsonl` inspection) revealed several IDE client behaviors that silently multiply token consumption and latency if left unmitigated:

### A. The 35,000-Token Static Plugin Bloat
* **The IDE Default**: Out of the box, Antigravity enables 13 bundled plugins (`firebase`, `flutter`, `android-cli-plugin`, `chrome-devtools-plugin`, `data-agent-kit-plugin`, `science`, etc.) and their MCP servers simultaneously. This injects ~35,000 tokens of static instructions and tool schemas into every single model call.
* **How AntiGraviton Fixes It**: Physically archives unused default plugins outside the active discovery path (`~/.gemini/plugins_archive`) and indexes all 120+ domain skills and rules inside `customizations.db`. The model receives a ~450-token routing directive and fetches only the exact micro-skill section (`skillsdb get-skill <name> --section "<Section>"`) needed for the current task.

### B. The `PreInvocation` Hook `proto3` & KV-Cache Trap (Introduced in IDE Update `2026-10-16`)
* **What Changed in Antigravity (`2026-10-16`)**:
  1. Antigravity's built-in documentation (`hooks.md`) advises plugin authors to inspect `invocationNum` to run initialization logic only once on Turn 1.
  2. After the `2026-10-16` IDE update, `PreInvocation` fires before **every internal tool step** within a turn, and due to `proto3` JSON serialization omitting default `0` values on call #1 and sending `"invocationNum": 1` on call #2, `payload.get("invocationNum", 1)` evaluates to `1` on **every tool call**.
  3. Worse, Antigravity persists every `ephemeralMessage` returned by `PreInvocation` permanently into `transcript.jsonl` as a `SYSTEM_SDK` (`EPHEMERAL_MESSAGE`) step.
* **The Impact (Full Transparency)**:
  - Any standard hook relying on Google's documented `invocationNum == 1` check (including AntiGraviton `v1.0.2` prior to `v1.0.3`) was triggered on every single tool call inside a multi-step turn.
  - Even though AntiGraviton's ~600-token hook payload was still far smaller than standard Antigravity's 35,000-token baseline, injecting an `EPHEMERAL_MESSAGE` into the tail of `transcript.jsonl` on every tool step invalidated the LLM's tail prompt KV-cache (causing `input_tokens` to spike to 10k-13k per tool step) and added ~2-3 seconds of subprocess overhead per tool call.
* **How AntiGraviton `v1.0.3` Fixes It**:
  - Completely bypasses Google's broken `invocationNum` counter by maintaining an independent, sub-millisecond state tracker (`skillsdb_hook_state.json`) keyed by `(conversationId, workspace)`.
  - Guardrails and project memory are injected **strictly once** per conversation (or workspace switch). All subsequent tool steps and turns within that conversation return `{"injectSteps": []}` in `< 1 ms`, restoring 100% tail KV-cache continuity.

### C. Hook Working Directory (`cwd`) Escalation When No Workspace Is Open
* **The IDE Behavior**: Antigravity executes plugin hooks with the working directory (`cwd`) set to the plugin folder (`~/.gemini/config/plugins/customizations-db`). When a user chats without an open project folder (`"workspacePaths": []`), naive upward directory traversal walks out of `~/.gemini` directly into the user's Windows profile root (`C:\Users\<User>`), mistaking the entire OS user profile for a software project.
* **How AntiGraviton `v1.0.3` Fixes It**: Enforces a strict `_is_protected_non_workspace()` boundary guard that blocks `Path.home()`, `~/.gemini`, `~/.gemini/config/*`, `~/.gemini/database/*`, the `~/.gemini/antigravity/scratch` container root, and the OS temp root from ever being scanned or initialized as a project workspace.

### D. Frontier Model Renaming (`Gemini 4 Argon`) & Reverse Transcript Inspection
* **The IDE Behavior**: When Google introduces new frontier model identifiers (such as `Gemini 4 Argon`) or when a developer switches models mid-session via the IDE dropdown, a `<USER_SETTINGS_CHANGE>` event is appended to `transcript.jsonl`.
* **How AntiGraviton `v1.0.3` Fixes It**: `detect_model_tier()` scans the tail of `transcript.jsonl` in reverse chronological order for `<USER_SETTINGS_CHANGE>` events and maps `Gemini 4 Argon` (`argon`, `gemini 4`) directly to `TIER_ULTRA` (16-thread high-concurrency mode).

### E. Windows UTF-8 Manifest Reset on Every IDE Update
* **The IDE Behavior**: Every Antigravity IDE update replaces `Antigravity.exe` and `language_server_windows_x64.exe` and wipes out external `.manifest` files. On Windows, child processes then fall back to legacy codepages (`cp1252`/`cp850`), corrupting umlauts and UTF-8 characters (`Mojibake`) and forcing the LLM into expensive retry loops.
* **How AntiGraviton Fixes It**: `skillsdb doctor` automatically detects missing `<activeCodePage>UTF-8</activeCodePage>` manifests after an IDE update, and `skillsdb fix-utf8` restores them in a single command.

---

## 4. One-Prompt / One-Command Installation

### Option A: Directly via Antigravity IDE Chat (Recommended)

Open the `AntiGraviton` folder in Google Antigravity IDE and type:

> *"Installiere AntiGraviton"* (or *"Install AntiGraviton"*)

Antigravity automatically reads [`.agents/skills/install-antigraviton/SKILL.md`](.agents/skills/install-antigraviton/SKILL.md), executes the unified installer, and presents a post-install briefing covering all active capabilities and optional customizations.

### Option B: Via PowerShell CLI

```powershell
powershell -ExecutionPolicy Bypass -File .\install-antigraviton.ps1
```

#### Installer Options
```powershell
# Specify custom Gitea service path and immediately mirror all Antigravity IDE workspaces:
.\install-antigraviton.ps1 -GiteaInstallPath "C:\Gitea" -SyncAllWorkspaces

# Deploy only SkillsDB and the Antigravity plugin (without local Gitea service):
.\install-antigraviton.ps1 -SkipGiteaService
```

> **Non-Destructive Upgrades & 3-Tier Elevation**: Running `install-antigraviton.ps1` on an already configured machine automatically backs up and preserves your existing `runtime_config` (Gitea token, URL, learned workspaces) and all user-learned rules (`category = 'learned'`). Initial Windows Service creation automatically uses direct admin rights if elevated, optional DPAPI credentials (`*.credential.xml`), or a one-time standard Windows UAC prompt.

---

## 5. Repository Architecture

```text
AntiGraviton/
|-- VERSION                        # SemVer 2.0.0 suite release version (1.0.3)
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
|-- skillsdb/                      # Modular Python package source (v3.5.1)
|   |-- config.py
|   |-- schema.py
|   |-- guardrails.py
|   |-- cli.py
|   |-- core/                      # Rules, micro-skills, FTS5 search, DPAPI credentials
|   `-- memory/                    # Episodic project memory + self-healing Gitea sync
|-- database/
|   |-- customizations.db          # Sanitized central SQLite database (zero hardcoded paths)
|   `-- db_manager.py              # Standalone compiled CLI manager
|-- plugin/                        # Antigravity IDE customizations-db plugin
|-- gitea-service/                 # Antigravity Gitea Windows Service distribution (v1.1.1)
|   |-- INSTALL.md
|   |-- scripts/
|   |   `-- deploy-gitea.ps1       # SCM service installer with auto-recovery & sdset ACLs
|   `-- custom/
|       `-- conf/
|           `-- app.example.ini    # Loopback-hardened Gitea configuration template
`-- tests/                         # Automated unit & integration test suite
```

---

## 6. Essential CLI Commands (`skillsdb`)

Once installed, `skillsdb` is globally available in your PATH and inside every Antigravity terminal:

```powershell
# View database telemetry and active token-saving architecture
skillsdb stats

# Run full 9-point system diagnostics & restore UTF-8 manifests after IDE updates
skillsdb doctor
skillsdb fix-utf8

# View or configure Gitea synchronization endpoint & learned workspaces
skillsdb gitea-config
skillsdb gitea-config --url "http://127.0.0.1:3000" --user "<username>" --token "<api-token>"

# Initialize episodic memory (.agents/memory.db) for a project
skillsdb mem-init --project .

# Save a milestone snapshot (automatically commits, pushes to Gitea, and flushes pending repos)
skillsdb mem-save-snapshot "Implemented feature X"

# Synchronize current workspace or all discovered Antigravity workspaces to Gitea
skillsdb gitea-sync
skillsdb gitea-sync --all

# Run automated test suite and text/formatting guardrails
python -m unittest discover -s tests -v
skillsdb guardrail check --path .
```

---

## 7. Versioning Policy

**AntiGraviton** strictly adheres to **[Semantic Versioning 2.0.0 (SemVer)](https://semver.org/)**:
- **`MAJOR`** (`X.0.0`): Incompatible architectural or CLI changes.
- **`MINOR`** (`1.X.0`): Backwards-compatible new features, skills, or synchronization capabilities.
- **`PATCH`** (`1.0.X`): Backwards-compatible bug fixes, security hardening, and rule refinements.

Published release tags are immutable and never overwritten in place; every update increments the version in [`VERSION`](VERSION) and documents all changes in [`CHANGELOG.md`](CHANGELOG.md).
