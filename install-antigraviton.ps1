<#
.SYNOPSIS
    Unified One-Command Installer for the AntiGraviton Complete Suite (v1.0.3).

.DESCRIPTION
    Deploys the complete AntiGraviton environment for Google Antigravity IDE:
    1. SkillsDB Context & Memory Engine (v3.5.1) + customizations-db Plugin
    2. Non-destructive upgrade preservation of existing runtime_config, Gitea tokens, learned workspaces, and user-learned rules
    3. Antigravity Gitea Local Windows Service (v1.1.1) with 3-tier elevation handling (direct admin, optional DPAPI, or UAC), SCM auto-recovery, and non-elevated start/stop permissions
    4. Zero-touch local Gitea Admin user & API Token provisioning into SkillsDB (no Active Directory required by default)
    5. Organic workspace discovery, health verification, and interactive capability briefing

.PARAMETER GiteaInstallPath
    Optional. Target directory for the Gitea Windows Service (e.g. C:\Gitea).
    If omitted, automatically detects an existing 'gitea' Windows Service installation path,
    or defaults to "$env:USERPROFILE\Gitea".

.PARAMETER GiteaPort
    Optional. Local loopback HTTP port for Gitea (default: 3000).

.PARAMETER GiteaAdminUser
    Optional. Local admin username for initial Gitea setup (defaults to $env:USERNAME).

.PARAMETER SkipGiteaService
    Optional switch. If set, deploys only SkillsDB and the Antigravity plugin without provisioning the Gitea Windows Service.

.PARAMETER SyncAllWorkspaces
    Optional switch. If set, immediately runs 'skillsdb gitea-sync --all' after installation to mirror all discovered Antigravity projects.

.EXAMPLE
    .\install-antigraviton.ps1
    .\install-antigraviton.ps1 -GiteaInstallPath "C:\Gitea" -SyncAllWorkspaces
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$GiteaInstallPath = "",

    [Parameter(Mandatory = $false)]
    [int]$GiteaPort = 3000,

    [Parameter(Mandatory = $false)]
    [string]$GiteaAdminUser = $env:USERNAME,

    [Parameter(Mandatory = $false)]
    [switch]$SkipGiteaService,

    [Parameter(Mandatory = $false)]
    [switch]$SyncAllWorkspaces
)

$ErrorActionPreference = "Stop"

$suiteRoot = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$versionFile = Join-Path $suiteRoot "VERSION"
$suiteVersion = if (Test-Path $versionFile) { (Get-Content $versionFile -Raw).Trim() } else { "1.0.3" }
$userHome = if ($env:USERPROFILE) { $env:USERPROFILE } else { [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::UserProfile) }
$geminiDir = Join-Path $userHome ".gemini"
$targetDbPath = Join-Path $geminiDir "database\customizations.db"
$targetMgrPath = Join-Path $geminiDir "database\db_manager.py"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "AntiGraviton Complete Suite Installer v$suiteVersion" -ForegroundColor Cyan
Write-Host "Source Suite : $suiteRoot" -ForegroundColor Cyan
Write-Host "Target Profile: $geminiDir" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# STEP 1: Preserve existing state (Gitea config, tokens, workspaces, learned rules)
# ---------------------------------------------------------------------------
$backupStateFile = Join-Path ([System.IO.Path]::GetTempPath()) "antigraviton_state_backup_$([guid]::NewGuid().ToString('N')).json"
if (Test-Path $targetDbPath) {
    Write-Host "[1/5] Backing up existing runtime configuration, learned workspaces, and user-learned rules..."
    $backupPy = @"
import sqlite3, json, sys
db_path, out_path = sys.argv[1], sys.argv[2]
state = {"runtime_config": {}, "learned_rules": []}
try:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT key, value FROM runtime_config")
        for k, v in cur.fetchall():
            state["runtime_config"][k] = v
    except Exception:
        pass
    try:
        cur.execute("SELECT key, name, category, scope, is_active, summary, content, token_estimate, updated_at FROM rules WHERE category = 'learned'")
        for row in cur.fetchall():
            state["learned_rules"].append(list(row))
    except Exception:
        pass
    conn.close()
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(state, f)
except Exception as e:
    print(f"Notice: Could not export previous state: {e}")
"@
    $backupPy | & python - $targetDbPath $backupStateFile
} else {
    Write-Host "[1/5] Clean installation detected (no previous customizations.db)."
}

# ---------------------------------------------------------------------------
# STEP 2: Deploy SkillsDB Core & Antigravity Plugin
# ---------------------------------------------------------------------------
Write-Host "[2/5] Deploying SkillsDB Engine (v3.5.0) and customizations-db Plugin..."
$deployPy = Join-Path $suiteRoot "deploy.py"
$deployPs1 = Join-Path $suiteRoot "deploy.ps1"

if (Test-Path $deployPy) {
    & python $deployPy --source $suiteRoot --target $geminiDir
} elseif (Test-Path $deployPs1) {
    & $deployPs1 -SourceDir $suiteRoot -TargetDir $geminiDir
} else {
    throw "Neither deploy.py nor deploy.ps1 found in $suiteRoot"
}

# Restore preserved user state into newly deployed database
if (Test-Path $backupStateFile) {
    $restorePy = @"
import sqlite3, json, sys, os
db_path, in_path = sys.argv[1], sys.argv[2]
try:
    with open(in_path, "r", encoding="utf-8") as f:
        state = json.load(f)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    for k, v in state.get("runtime_config", {}).items():
        if v and v not in ("{}", "[]"):
            cur.execute(
                "INSERT INTO runtime_config (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
                (k, v)
            )
    learned_rules = state.get("learned_rules", [])
    for r in learned_rules:
        cur.execute(
            "INSERT OR REPLACE INTO rules (key, name, category, scope, is_active, summary, content, token_estimate, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(r)
        )
    if learned_rules:
        cur.execute("INSERT INTO rules_fts(rules_fts) VALUES ('rebuild');")
    conn.commit()
    conn.close()
    print(f"      Restored existing runtime configuration, Gitea token, learned workspaces, and {len(learned_rules)} user-learned rules.")
finally:
    if os.path.exists(in_path):
        os.remove(in_path)
"@
    $restorePy | & python - $targetDbPath $backupStateFile
}

# ---------------------------------------------------------------------------
# STEP 3: Provision or Verify Antigravity Gitea Windows Service (v1.1.1)
# ---------------------------------------------------------------------------
if (-not $SkipGiteaService) {
    Write-Host "[3/5] Checking Antigravity Gitea Service (v1.1.1)..."
    $existingSvc = Get-Service -Name "gitea" -ErrorAction SilentlyContinue

    if (-not $GiteaInstallPath) {
        if ($existingSvc) {
            try {
                $wmiSvc = Get-CimInstance Win32_Service -Filter "Name='gitea'" -ErrorAction SilentlyContinue
                if ($wmiSvc -and $wmiSvc.PathName) {
                    if ($wmiSvc.PathName -match '^"([^"]+)"') {
                        $GiteaInstallPath = Split-Path -Parent $Matches[1]
                    } else {
                        $GiteaInstallPath = Split-Path -Parent ($wmiSvc.PathName.Split(' ')[0])
                    }
                }
            } catch {}
        }
        if (-not $GiteaInstallPath) {
            $GiteaInstallPath = Join-Path $userHome "Gitea"
        }
    }

    Write-Host "      Target Gitea Service Path: $GiteaInstallPath"
    New-Item -ItemType Directory -Force -Path $GiteaInstallPath | Out-Null

    # Copy service scripts and templates if not already in place
    $suiteGiteaDir = Join-Path $suiteRoot "gitea-service"
    if ((Test-Path $suiteGiteaDir) -and ($suiteGiteaDir -ne $GiteaInstallPath)) {
        New-Item -ItemType Directory -Force -Path (Join-Path $GiteaInstallPath "scripts") | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $GiteaInstallPath "custom\conf") | Out-Null
        Copy-Item (Join-Path $suiteGiteaDir "scripts\deploy-gitea.ps1") -Destination (Join-Path $GiteaInstallPath "scripts\deploy-gitea.ps1") -Force
        if (Test-Path (Join-Path $suiteGiteaDir "custom\conf\app.example.ini")) {
            Copy-Item (Join-Path $suiteGiteaDir "custom\conf\app.example.ini") -Destination (Join-Path $GiteaInstallPath "custom\conf\app.example.ini") -Force
        }
    }

    $giteaExe = Join-Path $GiteaInstallPath "gitea.exe"
    if (-not (Test-Path $giteaExe)) {
        Write-Host "      gitea.exe not found at $giteaExe. Downloading official Gitea Windows binary..."
        $downloadUrl = "https://dl.gitea.com/gitea/1.22.6/gitea-1.22.6-windows-4.0-amd64.exe"
        try {
            Invoke-WebRequest -Uri $downloadUrl -OutFile $giteaExe -UseBasicParsing
            Write-Host "      Downloaded Gitea binary to $giteaExe"
        } catch {
            Write-Warning "Could not download Gitea binary automatically: $_. Place gitea.exe in $GiteaInstallPath and re-run."
        }
    }

    if ($existingSvc) {
        if ($existingSvc.Status -ne "Running") {
            Write-Host "      Starting existing 'gitea' Windows service..."
            try {
                Start-Service -Name "gitea" -ErrorAction Stop
            } catch {
                & sc.exe start gitea | Out-Null
            }
            Start-Sleep -Seconds 3
        }
        $svcNow = Get-Service -Name "gitea" -ErrorAction SilentlyContinue
        Write-Host "      Gitea Windows Service Status: $($svcNow.Status)" -ForegroundColor Green
    } elseif (Test-Path $giteaExe) {
        $deployGiteaScript = Join-Path $GiteaInstallPath "scripts\deploy-gitea.ps1"
        $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

        if ($isAdmin) {
            Write-Host "      Current session has Administrator privileges. Registering 'gitea' service directly..."
            & $deployGiteaScript -InstallPath $GiteaInstallPath -HttpPort $GiteaPort
        } else {
            # Check for optional DPAPI credential file (supports standard local admin or .adm account)
            $credFile = Get-ChildItem "$geminiDir\config\credentials\*.credential.xml", "$geminiDir\credentials\*.credential.xml" -ErrorAction SilentlyContinue | Select-Object -First 1
            $elevatedOk = $false

            if ($credFile) {
                Write-Host "      Using stored DPAPI credential ($($credFile.Name)) for unattended service registration..."
                try {
                    $stored = Import-Clixml $credFile.FullName
                    $adminAccount = if ($stored.UserName) { $stored.UserName } else {
                        $dom = if ($env:USERDOMAIN) { $env:USERDOMAIN } else { $env:COMPUTERNAME }
                        $base = [System.IO.Path]::GetFileNameWithoutExtension([System.IO.Path]::GetFileNameWithoutExtension($credFile.Name))
                        "$dom\$base"
                    }
                    $cred = New-Object System.Management.Automation.PSCredential ($adminAccount, $stored.Password)
                    Invoke-Command -ComputerName localhost -Credential $cred -ScriptBlock {
                        param($scriptPath, $instPath, $port)
                        & $scriptPath -InstallPath $instPath -HttpPort $port
                    } -ArgumentList $deployGiteaScript, $GiteaInstallPath, $GiteaPort
                    $elevatedOk = [bool](Get-Service -Name "gitea" -ErrorAction SilentlyContinue)
                } catch {
                    Write-Warning "DPAPI elevation attempt did not complete: $_"
                }
            }

            if (-not $elevatedOk) {
                Write-Host "      Requesting standard Windows UAC elevation to register the 'gitea' service once..."
                try {
                    $argList = "-NoProfile -ExecutionPolicy Bypass -File `"$deployGiteaScript`" -InstallPath `"$GiteaInstallPath`" -HttpPort $GiteaPort"
                    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $argList -Wait -WindowStyle Hidden
                } catch {
                    Write-Warning "UAC elevation was canceled or unavailable: $_"
                    Write-Warning "Run 'powershell -ExecutionPolicy Bypass -File `"$deployGiteaScript`" -InstallPath `"$GiteaInstallPath`"' as Administrator once to register the service."
                }
            }
        }
    }
} else {
    Write-Host "[3/5] Skipping Gitea Windows Service provisioning (-SkipGiteaService)."
}

# ---------------------------------------------------------------------------
# STEP 4: Verify or Auto-Provision Local Gitea Admin User & API Token
# ---------------------------------------------------------------------------
Write-Host "[4/5] Verifying SkillsDB <-> Gitea Synchronization Configuration..."
$checkTokenPy = @"
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
row = conn.execute("SELECT value FROM runtime_config WHERE key='gitea_token'").fetchone()
conn.close()
print("True" if row and row[0] else "False")
"@
$hasTokenOut = ($checkTokenPy | & python - $targetDbPath | Out-String)
$hasToken = if ($hasTokenOut) { $hasTokenOut.Trim() } else { "False" }

if ($hasToken -ne "True" -and -not $SkipGiteaService) {
    $giteaExe = Join-Path $GiteaInstallPath "gitea.exe"
    $giteaIni = Join-Path $GiteaInstallPath "custom\conf\app.ini"
    $giteaDb  = Join-Path $GiteaInstallPath "data\gitea.db"

    if ((Test-Path $giteaExe) -and (Test-Path $giteaIni)) {
        Write-Host "      Provisioning local Gitea admin user '$GiteaAdminUser' and scoped API token..."
        try {
            # Ensure SQLite schema is initialized on brand-new installations
            if (-not (Test-Path $giteaDb)) {
                & $giteaExe migrate --config $giteaIni --work-path $GiteaInstallPath 2>$null | Out-Null
            }

            # Ensure local admin user exists in Gitea (standalone local workstation mode by default)
            $userList = (& $giteaExe admin user list --config $giteaIni --work-path $GiteaInstallPath 2>$null | Out-String)
            if ($userList -notmatch "\b$([regex]::Escape($GiteaAdminUser))\b") {
                $localEmail = "$GiteaAdminUser@local.dev"
                & $giteaExe admin user create --username $GiteaAdminUser --email $localEmail --admin --random-password --must-change-password=false --config $giteaIni --work-path $GiteaInstallPath 2>$null | Out-Null
                Write-Host "      Created local Gitea administrator account '$GiteaAdminUser' ($localEmail)." -ForegroundColor Green
            }

            $tokenName = "antigraviton-$($env:COMPUTERNAME.ToLower())-$(Get-Date -Format 'yyyyMMddHHmmss')"
            $rawToken = (& $giteaExe admin user generate-access-token --username $GiteaAdminUser --token-name $tokenName --scopes "all" --raw --config $giteaIni --work-path $GiteaInstallPath 2>$null | Select-Object -Last 1).Trim()
            if ($rawToken -and $rawToken.Length -ge 20) {
                & python $targetMgrPath gitea-config --url "http://127.0.0.1:$GiteaPort" --user $GiteaAdminUser --token $rawToken | Out-Null
                Write-Host "      Automatically provisioned and registered Gitea API token for user '$GiteaAdminUser'." -ForegroundColor Green
            }
        } catch {
            Write-Host "      Notice: Automatic token generation skipped (configure manually via 'skillsdb gitea-config')."
        }
    }
} else {
    & python $targetMgrPath gitea-config
}

# ---------------------------------------------------------------------------
# STEP 5: Final Telemetry, Optional Multi-Workspace Sync & Capability Briefing
# ---------------------------------------------------------------------------
Write-Host "[5/5] Running final health checks and workspace learning..."
& python $targetMgrPath mem-init --project $suiteRoot | Out-Null
& python $targetMgrPath stats

if ($SyncAllWorkspaces) {
    Write-Host "      Synchronizing all discovered Antigravity workspaces..."
    & python $targetMgrPath gitea-sync --all
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "AntiGraviton Complete Suite v$suiteVersion is active!" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "1. Active Core Capabilities:" -ForegroundColor Green
Write-Host "   - SkillsDB Engine (v3.5.0): ~95% token reduction via on-demand FTS5 micro-skills"
Write-Host "   - Episodic Project Memory : .agents/memory.db (decisions, facts, tasks, snapshots)"
Write-Host "   - Local Gitea Service     : http://127.0.0.1:$GiteaPort (Owner: $GiteaAdminUser, auto-recovery active)"
Write-Host "   - Deterministic Guardrails: Secret-leak prevention & formatting invariants"
Write-Host ""
Write-Host "2. Active User & Security Mode (Standalone Local Workstation Default):" -ForegroundColor Green
Write-Host "   - Gitea runs locally with standalone user '$GiteaAdminUser' (no Active Directory required)."
Write-Host "   - Non-elevated service control (sc.exe sdset) is active for zero-UAC service self-healing."
Write-Host ""
Write-Host "3. Optional Customizations Available on Demand (Just Tell Antigravity in Chat):" -ForegroundColor Yellow
Write-Host "   - Active Directory / LDAP : Switch Gitea to AD/LDAP login & avatar sync at any time."
Write-Host "   - Role Separation         : Configure standard User vs. User.adm privilege handling."
Write-Host "   - Zero-UAC DPAPI Caching  : Store encrypted local/domain admin credentials for background tasks."
Write-Host "   - Multi-Workspace Sync    : Run 'skillsdb gitea-sync --all' or team DB sync ('skillsdb sync')."
Write-Host "============================================================" -ForegroundColor Cyan
