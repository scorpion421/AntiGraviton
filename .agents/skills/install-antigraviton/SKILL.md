---
name: install-antigraviton
description: Automated installer and self-healing onboarding workflow for the AntiGraviton Complete Suite (SkillsDB v3.5.0 + Antigravity Gitea Service v1.1.1). Trigger this skill whenever the user asks to install, set up, configure, repair, or upgrade AntiGraviton, SkillsDB, or the local Gitea service.
---

# AntiGraviton Complete Suite Onboarding Skill

When a user asks to install, configure, or repair **AntiGraviton** in Antigravity IDE (for example: *"Installiere AntiGraviton"*, *"Install AntiGraviton"*, *"Set up AntiGraviton on this machine"*, or *"Update AntiGraviton"*), follow these steps autonomously:

## 1. Execute the Unified Installer

Run `install-antigraviton.ps1` from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\install-antigraviton.ps1
```

### Optional Parameters
- `-GiteaInstallPath "<path>"`: Target directory for the local Gitea Windows Service (defaults to existing `gitea` service path if installed, or `$env:USERPROFILE\Gitea`).
- `-GiteaPort 3000`: Local loopback HTTP port for Gitea (default: `3000`).
- `-GiteaAdminUser "<username>"`: Local administrator username for Gitea (defaults to `$env:USERNAME`).
- `-SkipGiteaService`: Installs only the SkillsDB engine and Antigravity plugin without provisioning a local Windows service.
- `-SyncAllWorkspaces`: Immediately discovers all Antigravity IDE projects in `~/.gemini/config/projects/*.json` and mirrors them to Gitea.

## 2. 3-Tier Elevation Handling for Initial Service Registration

`install-antigraviton.ps1` handles Windows Service registration (`sc.exe create gitea`) automatically across three tiers:
1. **Direct Execution**: If the current session already runs with local Administrator privileges (common on developer workstations), it registers the service directly.
2. **Optional DPAPI Credential**: If not elevated, it checks for an encrypted credential file in `~/.gemini/config/credentials/*.credential.xml` or `~/.gemini/credentials/*.credential.xml` and runs `deploy-gitea.ps1` via `Invoke-Command -ComputerName localhost` without UAC prompts.
3. **Standard UAC Fallback**: Otherwise, it triggers a one-time Windows UAC prompt (`Start-Process powershell.exe -Verb RunAs`). Once registered, `sc.exe sdset gitea` grants standard non-elevated sessions permission to start, stop, and self-heal the service without future UAC prompts.

## 3. Post-Install Verification

Verify the installation by running:

```powershell
skillsdb stats
skillsdb gitea-config
skillsdb gitea-sync
```

## 4. Mandatory Post-Install User Briefing ("Die volle Macht von AntiGraviton")

Immediately after the installation finishes, you **MUST** present a concise, well-structured overview to the user covering:

1. **Was jetzt aktiv ist (Kernfunktionen)**:
   - **SkillsDB Context & Memory Engine (`v3.5.0`)**: ~95 % weniger Basis-Tokenverbrauch (~1.500 statt ~35.000 Tokens/Turn) durch On-Demand Micro-Skill-Routing (`--summary`, `--section`), FTS5-Suche mit Synonym-Synapsen und adaptivem Modellprofil (`ultra`, `standard`, `lean`, `local`).
   - **Episodisches Projektgedächtnis (`.agents/memory.db`)**: Speichert Architekturentscheidungen (`mem-save-decision`), Projektfakten (`mem-save-fact`), Aufgaben (`mem-task-*`) und Meilenstein-Snapshots (`mem-save-snapshot`) dauerhaft über Sitzungen und `/compact` hinweg.
   - **Lokaler Gitea-Backup-Service (`http://127.0.0.1:3000`)**: Jeder Meilenstein-Snapshot erzeugt automatisch einen Git-Commit und pusht ihn in `< 0.5s` auf den lokalen Gitea-Server - inklusive 3-stufiger Selbstheilung (Windows SCM + Non-Elevated Startrecht + Catch-Up-Sync bei Offline-Phasen).
   - **Deterministische Guardrails**: Automatischer Schutz vor Secret-Leaks und Formatierungsfehlern (`skillsdb guardrail check --fix`).

2. **Aktiver Benutzer- & Sicherheitsmodus (Standalone-Standard vs. Optionale Erweiterungen)**:
   - Erkläre dem Benutzer, dass AntiGraviton und Gitea standardmäßig für einen **normalen Entwicklerrechner (Standalone Local User / Admin)** ohne Active Directory konfiguriert wurden (sein lokaler Windows-Benutzer `$env:USERNAME` wurde automatisch als lokaler Gitea-Admin mit eigenem API-Token eingerichtet).
   - Weise den Benutzer aktiv darauf hin, dass **jederzeit auf Zuruf im Chat** folgende Erweiterungen eingerichtet werden können (dazu muss er nur kurz sagen, wie sein Benutzer agieren soll):
     - **Optionale Active Directory / LDAP-Anbindung für Gitea**: Inklusive automatischer Synchronisation von AD-Profilbildern (`thumbnailPhoto`) und strikter Rechtetrennung (z. B. normaler Domänen-User arbeitet als Standardbenutzer, während ein separates Admin-Konto wie `User.adm` Gitea-Site-Admin wird).
     - **Optionale unbeaufsichtigte DPAPI-Elevation**: Falls der Benutzer ein getrenntes Admin-Konto (`User.adm` oder lokaler Admin) besitzt und künftig auch System-/Dienstaufgaben komplett ohne UAC-Abfrage im Hintergrund ausführen lassen möchte.
     - **Multi-Workspace- & Team-Synchronisation**: Automatische Spiegelung aller Antigravity-Projekte (`skillsdb gitea-sync --all`) oder Teilen gelernter Skills im Team (`skillsdb sync push/pull`).

3. **Angebot zur Vertiefung ("Volles Programm")**:
   - Frage den Benutzer abschließend, ob er zu einem dieser Themenbereiche (z. B. alle CLI-Power-Kommandos, Umstellung auf Active Directory / `User.adm`-Rechtehandling, Memory- & Taskboard-Workflow oder Guardrails) das **"volle Programm"** mit allen Details wünscht.
