---
name: install-antigraviton
description: >-
  Automated installer and self-healing onboarding workflow for the AntiGraviton Complete Suite
  (SkillsDB v3.7.3 + Antigravity Gitea Service v1.1.2). Trigger this skill whenever the user asks
  to install, set up, configure, repair, or upgrade AntiGraviton, SkillsDB, or the local Gitea service.
---

# AntiGraviton Complete Suite Onboarding Skill

When a user asks to install, configure, or repair **AntiGraviton** in Antigravity IDE (for example: *"Installiere AntiGraviton"*, *"Install AntiGraviton"*, *"Gitea bitte in D:\Gitea installieren"*, or *"Update AntiGraviton"*), follow these steps autonomously:

## 1. Execute the Unified Installer

Run `install-antigraviton.ps1` from the repository root (prefers `pwsh` if installed, or `powershell`, and automatically bootstraps PowerShell 7 and Python 3.12 if missing):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-antigraviton.ps1
```

### Optional Parameters (Pass Directly When Requested by the User)
- `-GiteaInstallPath "<path>"`: Target directory for the local Gitea Windows Service (e.g. `"C:\Gitea"`, `"D:\Gitea"`, or `"\\server\share\Gitea"`; defaults to existing `gitea` service path if installed, or `"$env:USERPROFILE\Gitea"`).
- `-GiteaPort 3000`: Local loopback HTTP port for Gitea (default: `3000`).
- `-GiteaAdminUser "<username>"`: Local administrator username for Gitea (defaults to `$env:USERNAME`).
- `-GiteaAdminPassword "<password>"`: Explicit password for the local Gitea administrator account (creates the user with this password or updates it if the user already exists; defaults to `--random-password` if omitted).
- `-ServiceAccount "<DOMAIN\user.adm>"` / `-ServicePassword "<password>"`: Optional Windows service account for enterprise network/UNC share installations (defaults to `"NT AUTHORITY\SYSTEM"`).
- `-BackupTargetPath "<path>"` / `-BackupTime "03:00"`: Optional daily online Gitea backup (`gitea.exe dump`) scheduled task destination and time.
- `-SkipGiteaService`: Installs only the SkillsDB engine and Antigravity plugin without provisioning a local Windows service.
- `-SyncAllWorkspaces`: Immediately discovers all Antigravity IDE projects in `~/.gemini/config/projects/*.json` and mirrors them to Gitea.

## 2. Automatic Environment Bootstrap & 3-Tier Elevation

`install-antigraviton.ps1` automatically handles:
0. **PowerShell 7 & Python 3 Bootstrap**: Installs `Microsoft.PowerShell` (`pwsh`) via `winget` if missing, sets PowerShell 7 as the default terminal in Windows Terminal and Antigravity IDE, and bootstraps standalone Python 3.12 via `uv` into `~/.local/bin` if Python 3 is missing or shadowed by the Windows 11 Microsoft Store alias stub.
1. **Direct Execution**: If the current session already runs with local Administrator privileges, it registers the `gitea` service directly.
2. **Optional DPAPI Credential**: If not elevated, it checks for an encrypted credential file in `~/.gemini/config/credentials/*.credential.xml` or `~/.gemini/credentials/*.credential.xml` and runs `deploy-gitea.ps1` via `Invoke-Command -ComputerName localhost` without UAC prompts.
3. **Standard UAC Fallback**: Otherwise, it triggers a one-time Windows UAC prompt (`Start-Process -Verb RunAs`). Once registered, `sc.exe sdset gitea` grants standard non-elevated sessions permission to start, stop, and self-heal the service without future UAC prompts, and `icacls.exe` ensures full read/write access on local secondary drives (such as `D:\Gitea`).

## 3. Post-Install Verification

Verify the installation by running:

```powershell
skillsdb stats
skillsdb gitea-config
skillsdb gitea-status
skillsdb doctor
```

## 4. Mandatory Post-Install User Briefing ("The Full Power of AntiGraviton")

Immediately after the installation finishes, you **MUST** present a concise, well-structured overview to the user (in their conversational language) covering:

1. **Active Core Capabilities**:
   - **SkillsDB Context & Memory Engine (`v3.7.3`)**: ~95% lower base token consumption (~1,500 instead of ~35,000 tokens/turn) via on-demand micro-skill routing (`--summary`, `--section`), FTS5 search with synonym expansion, and adaptive model concurrency profiles (`ultra`, `standard`, `lean`, `local`).
   - **Episodic Project Memory (`.agents/memory.db`)**: Persists architectural decisions (`mem-save-decision`), project facts (`mem-save-fact`), tasks (`mem-task-*`), and milestone snapshots (`mem-save-snapshot`) across sessions and `/compact` boundaries.
   - **Local Gitea Backup Service (`http://127.0.0.1:3000`)**: Every milestone snapshot automatically creates a Git commit and pushes it in `< 0.5s` to the local Gitea server, including 3-tier self-healing (Windows SCM + non-elevated service start permissions + catch-up sync after offline periods).
   - **Deterministic Guardrails**: Automated protection against secret leaks and formatting violations (`skillsdb guardrail check --fix`).

2. **Active User & Security Mode (Standalone Default vs. Optional Extensions)**:
   - Explain that AntiGraviton and Gitea are configured by default for a **standalone developer workstation (Local User / Admin)** without Active Directory (the local Gitea admin account is automatically provisioned with a scoped API token).
   - Proactively inform the user that the following extensions can be enabled **on demand via chat** at any time:
     - **Optional Active Directory / LDAP Integration for Gitea**: Including automatic AD profile picture synchronization (`thumbnailPhoto`) and strict role separation (e.g. standard domain account operates as regular user while a separate admin account such as `User.adm` is granted Gitea site administrator privileges).
     - **Optional Unattended DPAPI Elevation**: If the user has a separate admin account (`User.adm` or local admin) and wants background system/service tasks executed without interactive UAC prompts.
     - **Optional Daily 03:00 Gitea Backup Task**: Automated daily `gitea dump` to a local or network backup path (`-BackupTargetPath`).
     - **Multi-Workspace & Team Synchronization**: Automated mirroring of all Antigravity projects (`skillsdb gitea-sync --all`) or team skill sharing (`skillsdb sync push/pull`).

3. **Deep-Dive Offer ("Full Briefing")**:
   - Ask the user whether they would like the complete in-depth guide on any of these areas (CLI power commands, Active Directory / `User.adm` role separation, episodic memory & taskboard workflow, or guardrails).
