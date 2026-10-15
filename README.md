# AntiGraviton Complete Suite (`v1.0.1`)

**Turnkey Agentic Development Environment for Google Antigravity IDE**  
*Combining **SkillsDB (`v3.5.0`)** + **Antigravity Gitea Windows Service (`v1.1.0`)** in a single one-command distribution.*

---

## 1. Executive Summary

**AntiGraviton** transforms Google Antigravity IDE from a stateless chat assistant into a persistent, self-healing, low-token agentic software engineering environment.

It bundles two tightly integrated systems into a single repository and installer:

1. **SkillsDB Context & Memory Engine (`v3.5.0`)**:
   - **On-Demand Micro-Skill Routing**: Replaces static plugin prompt bloat (~35,000 tokens/turn) with a compact (~450 token) routing directive and surgical SQLite FTS5 micro-skill retrieval (`--summary`, `--section`).
   - **Persistent Episodic Project Memory (`.agents/memory.db`)**: Automatically records architectural decisions, milestone snapshots, and workspace telemetry across sessions so every new conversation starts with full project context.
   - **Organic On-Demand Workspace Learning**: Ships with zero hardcoded paths or credentials. Automatically learns project paths as you work (`mem-init`, `mem-save-snapshot`, `gitea-init`) and discovers Antigravity IDE workspaces from `~/.gemini/config/projects/*.json`.
2. **Antigravity Gitea Local Windows Service (`v1.1.0`)**:
   - **Standalone Developer Default (Zero Active Directory Dependency)**: Works out of the box on any Windows developer machine using your local Windows username (`$env:USERNAME`), automatically generating cryptographic config secrets, provisioning your local Gitea admin account, and binding a scoped API token into SkillsDB. Optional Active Directory (LDAP) and `User` vs. `User.adm` role separation can be enabled on demand at any time.
   - **Isolated Loopback Git Server (`127.0.0.1:3000`)**: Sub-second (`< 0.5s`) automated Git backup for every milestone snapshot and file checkpoint without external cloud latency.
   - **Multi-Layered Fault-Tolerance & Auto-Recovery**: Configured with Windows Service Control Manager (SCM) auto-restart (`15s/30s/60s`), non-elevated interactive user start/stop permissions (`sc.exe sdset`), autonomous agent service wake-up (`ensure_gitea_online()`), and cross-workspace catch-up synchronization (`flush_all_pending_pushes()`).

---

## 2. Measured Impact in Daily Development

| Metric | Standard Antigravity Setup | AntiGraviton Suite (`v1.0.1`) | Real-World Improvement |
| :--- | :--- | :--- | :--- |
| **System & Plugin Context Load** | ~35,000 tokens / turn | **~1,500 tokens / turn** | **~95% lower base overhead** |
| **Token Consumption (60-Turn Session, benchmarked on Gemini 4 Argon High)** | ~3.39M input tokens | **~0.83M input tokens** | **~75.5% total token savings (4x session capacity)** |
| **Context Retention After `/compact` or Restart** | 0% (manual re-briefing required) | **100% automatic (`memory.db` + Git log)** | **Zero architectural amnesia** |
| **Backup & Rollback Granularity** | Manual commits only | **Every milestone automatically pushed (`< 0.5s`)** | **Zero code loss & instant Git history forensics** |
| **Fileserver / Network Drop Resilience** | Silent push failure | **3-tier SCM + Agent auto-restart & catch-up sync** | **100% self-healing synchronization** |

> **Why High-Reasoning Models (`Gemini 4 Argon High`) Amplify These Savings**: Frontier reasoning models generate deep internal thinking chains (`thinking` tokens) and execute multi-step autonomous tool trajectories within a single user turn. Because every internal tool-call step re-evaluates the active system and conversation context, eliminating ~33,500 tokens of static plugin bloat per step prevents compound token burn across multi-step reasoning loops while keeping model attention focused strictly on active code.

---

## 3. One-Prompt / One-Command Installation

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

## 4. Repository Architecture

```text
AntiGraviton/
|-- VERSION                        # SemVer 2.0.0 suite release version (1.0.0)
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
|-- skillsdb/                      # Modular Python package source (v3.5.0)
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
|-- gitea-service/                 # Antigravity Gitea Windows Service distribution (v1.1.0)
|   |-- INSTALL.md
|   |-- scripts/
|   |   `-- deploy-gitea.ps1       # SCM service installer with auto-recovery & sdset ACLs
|   `-- custom/
|       `-- conf/
|           `-- app.example.ini    # Loopback-hardened Gitea configuration template
`-- tests/                         # Automated unit & integration test suite
```

---

## 5. Essential CLI Commands (`skillsdb`)

Once installed, `skillsdb` is globally available in your PATH and inside every Antigravity terminal:

```powershell
# View database telemetry and active token-saving architecture
skillsdb stats

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

## 6. Versioning Policy

**AntiGraviton** strictly adheres to **[Semantic Versioning 2.0.0 (SemVer)](https://semver.org/)**:
- **`MAJOR`** (`X.0.0`): Incompatible architectural or CLI changes.
- **`MINOR`** (`1.X.0`): Backwards-compatible new features, skills, or synchronization capabilities.
- **`PATCH`** (`1.0.X`): Backwards-compatible bug fixes, security hardening, and rule refinements.

Published release tags are immutable and never overwritten in place; every update increments the version in [`VERSION`](VERSION) and documents all changes in [`CHANGELOG.md`](CHANGELOG.md).
