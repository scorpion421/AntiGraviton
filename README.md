# AntiGraviton complete suite (`v1.0.4`)

**Turnkey agentic development environment for Google Antigravity IDE**  
*Combining **SkillsDB (`v3.5.2`)** + **Antigravity Gitea Windows service (`v1.1.1`)** in a single one-command distribution.*

---

## 1. Executive summary

**AntiGraviton** transforms Google Antigravity IDE from a stateless chat assistant into a persistent, self-healing, low-token agentic software engineering environment.

It bundles two tightly integrated systems into a single repository and installer:

1. **SkillsDB context and memory engine (`v3.5.2`)**:
   - **On-demand micro-skill routing**: Replaces static plugin prompt bloat (~35,000 tokens/turn) with a compact (~450 token) routing directive and surgical SQLite FTS5 micro-skill retrieval (`--summary`, `--section`).
   - **Persistent episodic project memory (`.agents/memory.db`)**: Automatically records architectural decisions, milestone snapshots, and workspace telemetry across sessions so every new conversation starts with full project context.
   - **Organic on-demand workspace learning**: Ships with zero hardcoded paths or credentials. Automatically learns project paths as you work (`mem-init`, `mem-save-snapshot`, `gitea-init`) and discovers Antigravity IDE workspaces from `~/.gemini/config/projects/*.json` and `~/.gemini/antigravity/scratch`.
2. **Antigravity Gitea local Windows service (`v1.1.1`)**:
   - **Standalone developer default (zero Active Directory dependency)**: Works out of the box on any Windows developer machine using your local Windows username (`$env:USERNAME`), automatically generating cryptographic config secrets, provisioning your local Gitea admin account, and binding a scoped API token into SkillsDB. Optional Active Directory (LDAP) and `User` vs. `User.adm` role separation can be enabled on demand at any time.
   - **Isolated loopback Git server (`127.0.0.1:3000`)**: Sub-second (`< 0.5s`) automated Git backup for every milestone snapshot and file checkpoint without external cloud latency, pre-configured with LevelDB-free immediate queues (`TYPE = immediate`) for conflict-free `gitea dump` backups on Windows.
   - **Multi-layered fault-tolerance and auto-recovery**: Configured with Windows Service Control Manager (SCM) auto-restart (`15s/30s/60s`), non-elevated interactive user start/stop permissions (`sc.exe sdset`), autonomous agent service wake-up (`ensure_gitea_online()`), and cross-workspace catch-up synchronization (`flush_all_pending_pushes()`).

---

## 2. Measured impact in daily development

| Metric | Standard Antigravity setup | AntiGraviton suite (`v1.0.4`) | Real-world improvement |
| :--- | :--- | :--- | :--- |
| **System and plugin context load** | ~35,000 tokens / turn | **~1,500 tokens / turn** | **~95% lower base overhead** |
| **Token consumption (60-turn session, benchmarked on Gemini 4 Argon High)** | ~3.39M input tokens | **~0.83M input tokens** | **~75.5% total token savings (4x session capacity)** |
| **Context retention after `/compact` or restart** | 0% (manual re-briefing required) | **100% automatic (`memory.db` + Git log)** | **Zero architectural amnesia** |
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

### C. Hook working directory (`cwd`) escalation when no workspace is open
* **The IDE behavior**: Antigravity executes plugin hooks with the working directory (`cwd`) set to the plugin folder (`~/.gemini/config/plugins/customizations-db`). When a user chats without an open project folder (`"workspacePaths": []`), naive upward directory traversal walks out of `~/.gemini` directly into the user's Windows profile root (`C:\Users\<User>`), mistaking the entire OS user profile for a software project.
* **How AntiGraviton `v1.0.3+` fixes it**: Enforces a strict `_is_protected_non_workspace()` boundary guard that blocks `Path.home()`, `~/.gemini`, `~/.gemini/config/*`, `~/.gemini/database/*`, the `~/.gemini/antigravity/scratch` container root, and the OS temp root from ever being scanned or initialized as a project workspace.

### D. Frontier model renaming (`Gemini 4 Argon`) and reverse transcript inspection
* **The IDE behavior**: When Google introduces new frontier model identifiers (such as `Gemini 4 Argon`) or when a developer switches models mid-session via the IDE dropdown, a `<USER_SETTINGS_CHANGE>` event is appended to `transcript.jsonl`.
* **How AntiGraviton `v1.0.3+` fixes it**: `detect_model_tier()` scans the tail of `transcript.jsonl` in reverse chronological order for `<USER_SETTINGS_CHANGE>` events and maps `Gemini 4 Argon` (`argon`, `gemini 4`) directly to `TIER_ULTRA` (16-thread high-concurrency mode).

### E. Windows UTF-8 manifest reset on every IDE update
* **The IDE behavior**: Every Antigravity IDE update replaces `Antigravity.exe` and `language_server_windows_x64.exe` and wipes out external `.manifest` files. On Windows, child processes then fall back to legacy codepages (`cp1252`/`cp850`), corrupting umlauts and UTF-8 characters (`Mojibake`) and forcing the LLM into expensive retry loops.
* **How AntiGraviton fixes it**: `skillsdb doctor` automatically detects missing `<activeCodePage>UTF-8</activeCodePage>` manifests after an IDE update, and `skillsdb fix-utf8` restores them in a single command.

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
|-- VERSION                        # SemVer 2.0.0 suite release version (1.0.4)
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
|-- skillsdb/                      # Modular Python package source (v3.5.2)
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
|-- gitea-service/                 # Antigravity Gitea Windows service distribution (v1.1.1)
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
# View database telemetry and active token-saving architecture
skillsdb stats

# Run full 9-point system diagnostics & restore UTF-8 manifests after IDE updates
skillsdb doctor
skillsdb fix-utf8

# View or configure Gitea synchronization endpoint & learned workspaces
skillsdb gitea-config
skillsdb gitea-config --url "http://127.0.0.1:3000" --user "<username>" --token "<api-token>"

# Initialize and upload any standalone folder or project to local Gitea
skillsdb gitea-init "<target_directory>"

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

## 7. Versioning policy

**AntiGraviton** strictly adheres to **[Semantic Versioning 2.0.0 (SemVer)](https://semver.org/)**:
- **`MAJOR`** (`X.0.0`): Incompatible architectural or CLI changes.
- **`MINOR`** (`1.X.0`): Backwards-compatible new features, skills, or synchronization capabilities.
- **`PATCH`** (`1.0.X`): Backwards-compatible bug fixes, security hardening, and rule refinements.

Published release tags are immutable and never overwritten in place; every update increments the version in [`VERSION`](VERSION) and documents all changes in [`CHANGELOG.md`](CHANGELOG.md).
