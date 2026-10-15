# Antigravity Gitea: Local Workstation & Enterprise Installation Guide

Comprehensive runbook for installing, configuring, and running Gitea as an isolated local Windows Service on developer workstations (standalone default) or enterprise domain environments (optional Active Directory / LDAP integration), harmonized with SkillsDB automated commit tracking.

---

## Architecture Overview (Default: Standalone Developer Workstation)

```text
+------------------------------------------------------------------------+
|                     Windows Workstation (Localhost)                    |
|                                                                        |
|  +--------------------------------+  +-------------------------------+ |
|  |     Antigravity Workspaces     |  |       SkillsDB Engine         | |
|  |   (Local or Network Drives)    |  |   (~/.gemini/database/)       | |
|  +---------------+----------------+  +---------------+---------------+ |
|                  |                                   |                 |
|                  | git push / commit                 | REST API / Auth |
|                  v                                   v                 |
|  +-------------------------------------------------------------------+ |
|  |           Gitea Windows Service (gitea on 127.0.0.1:3000)         | |
|  |   * Loopback isolation (zero external network exposure)           | |
|  |   * Standalone local admin user ($env:USERNAME) + API Token       | |
|  |   * Non-elevated service control & auto-recovery (sc.exe sdset)   | |
|  |   * Public registration & SSH server disabled                     | |
|  |   * Local SQLite storage (data/gitea.db)                          | |
|  +---------------------------------+---------------------------------+ |
+------------------------------------|-----------------------------------+
                                     | (Optional Enterprise Add-On)
                                     | LDAP Bind (389 / 636)
                                     v
                  +--------------------------------------+
                  |   Optional Active Directory (LDAP)   |
                  |   * Domain SSO Authentication        |
                  |   * Role Separation (User / User.adm)|
                  |   * Avatar Sync (thumbnailPhoto)     |
                  +--------------------------------------+
```

---

## 1. Prerequisites

1. **Operating System**: Windows 10, Windows 11, or Windows Server 2019/2022/2025.
2. **Git for Windows**: Installed with `git.exe` in system PATH (`C:\Program Files\Git\cmd`).
3. **PowerShell**: PowerShell 5.1 or PowerShell 7+.

---

## 2. Automated Deployment (Standalone Default)

In most developer setups, run the unified installer (`install-antigraviton.ps1` from the repository root) or execute `deploy-gitea.ps1`:

```powershell
# Standard local developer workstation deployment (runs as SYSTEM, no AD required)
.\scripts\deploy-gitea.ps1 -InstallPath "$env:USERPROFILE\Gitea" -HttpPort 3000

# Optional: Enterprise network share deployment with a dedicated service account
.\scripts\deploy-gitea.ps1 `
    -InstallPath "\\fileserver.domain.local\share\home\username\Gitea" `
    -ServiceAccount "DOMAIN\username.adm" `
    -ServicePassword "SecretPassword" `
    -GitCeilingDirectories "$env:USERPROFILE;\\fileserver.domain.local\share\home\username"
```

What `deploy-gitea.ps1` does automatically:
1. Verifies directory structure (`custom\conf`, `data\gitea-repositories`, `data\avatars`, `log`).
2. Generates `custom\conf\app.ini` from `custom\conf\app.example.ini` with freshly generated cryptographic secrets (`SECRET_KEY`, `INTERNAL_TOKEN`, `JWT_SECRET`).
3. Self-heals existing `app.ini` paths and regenerates Git hooks if the Gitea directory was moved to another drive or folder.
4. Registers the `gitea` Windows Service with SCM crash auto-recovery (`15s/30s/60s`) and configures `sc.exe sdset gitea` so standard non-elevated sessions and the Antigravity agent can start/stop/heal the service without UAC prompts.

---

## 3. Standalone User & Token Provisioning (Default Mode)

When running `install-antigraviton.ps1`, the installer automatically creates your local Windows user (`$env:USERNAME`) as a local Gitea administrator and provisions a scoped API token into SkillsDB.

To perform this step manually on a standalone installation:

```powershell
# 1. Create local administrator account in Gitea
.\gitea.exe admin user create `
    --username "$env:USERNAME" `
    --email "$env:USERNAME@local.dev" `
    --admin `
    --random-password `
    --must-change-password=false `
    --config ".\custom\conf\app.ini" `
    --work-path "."

# 2. Generate scoped API token
$token = .\gitea.exe admin user generate-access-token `
    --username "$env:USERNAME" `
    --token-name "antigraviton-local" `
    --scopes "all" `
    --raw `
    --config ".\custom\conf\app.ini" `
    --work-path "."

# 3. Register token in SkillsDB runtime_config
skillsdb gitea-config --url "http://127.0.0.1:3000" --user "$env:USERNAME" --token $token --auto-detect
```

---

## 4. Optional Enterprise Mode: Active Directory (LDAP) & Role Separation (`User` vs. `User.adm`)

If your workstation is part of a corporate Active Directory domain and you want to use domain credentials, automatic profile picture synchronization (`thumbnailPhoto`), or strict privilege separation between your normal user account (`username`) and your admin account (`username.adm`), you can enable LDAP at any time.

### Step 1: Enable External User Sync in `custom/conf/app.ini`

Uncomment the `[cron.sync_external_users]` section in `custom\conf\app.ini`:

```ini
[cron.sync_external_users]
RUN_AT_START = true
SCHEDULE = @every 1h
UPDATE_EXISTING = true
```

### Step 2: Register Active Directory Authentication Source via Gitea CLI

```powershell
.\gitea.exe admin auth add-ldap `
    --name "ActiveDirectory" `
    --host "dc01.domain.local" `
    --port 389 `
    --security-protocol "Unencrypted" `
    --bind-dn "serviceaccount@domain.local" `
    --bind-password "SecretPassword" `
    --user-search-base "DC=domain,DC=local" `
    --user-filter "(&(objectCategory=Person)(sAMAccountName=%s))" `
    --admin-filter "(sAMAccountName=username.adm)" `
    --username-attribute "sAMAccountName" `
    --firstname-attribute "givenName" `
    --surname-attribute "sn" `
    --email-attribute "mail" `
    --avatar-attribute "thumbnailPhoto" `
    --synchronize-users `
    --config ".\custom\conf\app.ini" `
    --work-path "."
```

### Privilege & Role Handling Options in AD Mode:
- **Single Account (Standard User Is Also Gitea Admin)**:
  Omit `--admin-filter` or set `--admin-filter "(sAMAccountName=username)"`.
- **Strict Role Separation (`username` vs. `username.adm`)**:
  Set `--admin-filter "(sAMAccountName=username.adm)"`. Your daily account (`username`) logs in as a standard developer user for all code repositories, while only `username.adm` receives Gitea site administrator privileges.
- **Avatar Privacy**:
  `--avatar-attribute "thumbnailPhoto"` syncs your Active Directory photo locally, while `DISABLE_GRAVATAR = true` prevents any external avatar requests.

---

## 5. Enterprise & Network Share Troubleshooting Reference

### Issue A: `fatal: Permission denied` during Git operations on DFS shares
* **Cause**: Git climbs parent directories up to hidden administrative shares (`\\server\home$\`).
* **Fix**: Ensure `GIT_CEILING_DIRECTORIES` is set in the system environment containing the base workspace directory and share path.

### Issue B: Windows Service fails with `The system cannot find the drive specified`
* **Cause**: Windows Services run in Session 0 and do not have access to user-mapped drive letters.
* **Fix**: Use canonical UNC paths (`\\server\share\...`) in service `binPath` and `app.ini` when storing Gitea on a network share.

### Issue C: `go-sqlite3: invalid URI authority` error
* **Cause**: `go-sqlite3` parses forward slashes `//server/share/...` as URI authorities (`file://server`).
* **Fix**: Use native Windows backslash UNC paths in `app.ini` (`PATH = \\server\share\...\gitea.db`).

### Issue D: Service cannot start with custom user account (`1069: Logon failure`)
* **Cause**: The custom service account lacks the `SeServiceLogonRight` Windows privilege.
* **Fix**: Run the service as `NT AUTHORITY\SYSTEM` (default) or grant `Log on as a service` in Local Security Policy (`secpol.msc`).

### Issue E: `gitea.exe dump` fails with `LOCK: The process cannot access the file because it is being used by another process`
* **Cause**: By default, Gitea creates on-disk LevelDB queues in `data/queues/common` that hold exclusive Windows file locks while the service is running.
* **Fix**: Configure `[queue]` and `[queue.common]` with `TYPE = immediate` in `custom\conf\app.ini` (enabled by default in `app.example.ini` and automatically self-healed by `deploy-gitea.ps1`).
