# Changelog

All notable changes to the **AntiGraviton** project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
