<#
.SYNOPSIS
    Unified One-Command Installer for the AntiGraviton Complete Suite (v1.4.9).

.DESCRIPTION
    Deploys the complete AntiGraviton environment for Google Antigravity IDE across
    all three target scenarios (Enterprise AD/UNC, Desktop with scheduled backup, and Laptop/Secondary Drive):
    0. Automatic PowerShell 7 (pwsh) & Python 3 runtime bootstrap + IDE/Windows Terminal default shell configuration
    1. Non-destructive upgrade preservation of existing runtime_config, Gitea tokens, learned workspaces, and user-learned rules
    2. SkillsDB Context & Memory Engine (v3.7.9) + customizations-db Plugin + UTF-8 manifest deployment
    3. Antigravity Gitea Local Windows Service (v1.1.2) with 3-tier elevation handling (direct admin, optional DPAPI, or UAC),
       secondary drive NTFS ACL setup, SCM auto-recovery, non-elevated start/stop permissions, and optional 03:00 backup task
    4. Zero-touch local Gitea Admin user (with optional explicit password) & API Token provisioning into SkillsDB
    5. Strict health verification gate, organic workspace discovery, and interactive capability briefing

.PARAMETER GiteaInstallPath
    Optional. Target directory for the Gitea Windows Service (e.g. C:\Gitea, D:\Gitea, or \\server\share\Gitea).
    If omitted, automatically detects an existing 'gitea' Windows Service installation path,
    or defaults to "$env:USERPROFILE\Gitea".

.PARAMETER GiteaPort
    Optional. Local loopback HTTP port for Gitea (default: 3000).

.PARAMETER GiteaAdminUser
    Optional. Local admin username for initial Gitea setup (defaults to $env:USERNAME).

.PARAMETER GiteaAdminPassword
    Optional. Explicit password for the local Gitea admin user. If omitted, a random password is generated for new accounts.

.PARAMETER ServiceAccount
    Optional. Windows service account for Gitea (defaults to "NT AUTHORITY\SYSTEM"; supports "DOMAIN\user.adm" for UNC shares).

.PARAMETER ServicePassword
    Optional. Password for a custom Windows service account.

.PARAMETER BackupTargetPath
    Optional. Destination directory (local or UNC network share) for automated daily 'gitea dump' backups.

.PARAMETER BackupTime
    Optional. Daily execution time for the scheduled Gitea backup task when BackupTargetPath is provided (default: "03:00").

.PARAMETER SkipGiteaService
    Optional switch. If set, deploys only SkillsDB and the Antigravity plugin without provisioning the Gitea Windows Service.

.PARAMETER SyncAllWorkspaces
    Optional switch. If set, immediately runs 'skillsdb gitea-sync --all' after installation to mirror all discovered Antigravity projects.

.EXAMPLE
    .\install-antigraviton.ps1
    .\install-antigraviton.ps1 -GiteaInstallPath "D:\Gitea" -GiteaAdminUser "MIckyFoley" -GiteaAdminPassword "SecretPassword" -SyncAllWorkspaces
    .\install-antigraviton.ps1 -GiteaInstallPath "C:\Gitea" -BackupTargetPath "\\nas\backup\Gitea" -BackupTime "03:00"
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
    [string]$GiteaAdminPassword = "",

    [Parameter(Mandatory = $false)]
    [string]$ServiceAccount = "NT AUTHORITY\SYSTEM",

    [Parameter(Mandatory = $false)]
    [string]$ServicePassword = "",

    [Parameter(Mandatory = $false)]
    [string]$BackupTargetPath = "",

    [Parameter(Mandatory = $false)]
    [string]$BackupTime = "03:00",

    [Parameter(Mandatory = $false)]
    [switch]$SkipGiteaService,

    [Parameter(Mandatory = $false)]
    [switch]$SyncAllWorkspaces
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
$ProgressPreference = "SilentlyContinue"

$suiteRoot = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$versionFile = Join-Path $suiteRoot "VERSION"
$suiteVersion = if (Test-Path $versionFile) { (Get-Content $versionFile -Raw).Trim() } else { "1.4.9" }
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
# STEP 0: Bootstrap PowerShell 7 (pwsh) & Python 3 Runtime Environment
# ---------------------------------------------------------------------------
Write-Host "[0/5] Verifying PowerShell 7 and Python 3 runtime environment..."

$localBin = Join-Path $userHome ".local\bin"
$agyBin = Join-Path $geminiDir "antigravity\bin"
$ps7ProgDir = "C:\Program Files\PowerShell\7"

# Ensure PowerShell 7 (pwsh.exe) is installed and configured as system/IDE default shell
$pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
if (-not $pwshCmd -and (Test-Path (Join-Path $ps7ProgDir "pwsh.exe"))) {
    $env:Path = "$ps7ProgDir;$env:Path"
    $pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
}
if (-not $pwshCmd) {
    $wingetCmd = Get-Command winget -ErrorAction SilentlyContinue
    if ($wingetCmd) {
        Write-Host "      Installing latest PowerShell 7 (Microsoft.PowerShell) via winget..."
        try {
            & winget install --id Microsoft.PowerShell --source winget --accept-package-agreements --accept-source-agreements --silent | Out-Null
            if (Test-Path (Join-Path $ps7ProgDir "pwsh.exe")) {
                $env:Path = "$ps7ProgDir;$env:Path"
            }
            $pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
        } catch {
            Write-Warning "Could not auto-install PowerShell 7 via winget: $_"
        }
    }
}
$preferredShellExe = if ($pwshCmd) { $pwshCmd.Source } else { "powershell.exe" }
if ($pwshCmd) {
    $pwshVer = (& $preferredShellExe --version 2>$null | Out-String).Trim()
    Write-Host "      Active PowerShell 7 binary: $preferredShellExe ($pwshVer)" -ForegroundColor Green
}

# Prepend .local\bin and .gemini\antigravity\bin ahead of WindowsApps in User PATH and current session PATH
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$pathParts = ($userPath -split ';') | Where-Object { $_ -and ($_ -ne $localBin) -and ($_ -ne $agyBin) }
$newUserPath = (@($localBin, $agyBin) + $pathParts) -join ';'
if ($newUserPath -ne $userPath) {
    [Environment]::SetEnvironmentVariable("Path", $newUserPath, "User")
}
$env:Path = "$localBin;$agyBin;$env:Path"

# Test whether a real Python 3 interpreter is active (ignoring the Windows 11 Microsoft Store alias stub)
$hasRealPython = $false
try {
    $pyMajor = (& python -c "import sys; print(sys.version_info.major)" 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -eq 0 -and $pyMajor -eq "3") {
        $hasRealPython = $true
    }
} catch {}

if (-not $hasRealPython) {
    Write-Host "      Python 3 not found (or shadowed by Microsoft Store alias). Bootstrapping standalone Python 3.12 via uv..."
    $uvExe = Join-Path $localBin "uv.exe"
    if (-not (Test-Path $uvExe)) {
        Invoke-RestMethod -Uri "https://astral.sh/uv/install.ps1" | Invoke-Expression
    }
    if (Test-Path $uvExe) {
        & $uvExe python install 3.12 --default
        $env:Path = "$localBin;$agyBin;$env:Path"
    }
    $pyMajorAfter = (& python -c "import sys; print(sys.version_info.major)" 2>$null | Out-String).Trim()
    if ($pyMajorAfter -ne "3") {
        throw "Failed to bootstrap a working Python 3 runtime in $localBin."
    }
}
$pyVerStr = (& python --version 2>$null | Out-String).Trim()
Write-Host "      Active Python runtime     : $pyVerStr" -ForegroundColor Green

# Configure PowerShell 7 as the default profile in Windows Terminal and Antigravity IDE
if ($pwshCmd) {
    $setDefaultShellPy = @"
import json, os, sys
from pathlib import Path

pwsh_path = sys.argv[1]
local_app = Path(os.environ.get("LOCALAPPDATA", ""))
for wt_settings in local_app.glob("Packages/Microsoft.WindowsTerminal_*/LocalState/settings.json"):
    try:
        data = json.loads(wt_settings.read_text(encoding="utf-8-sig"))
        ps7_guid = "{574e775e-4f2a-5b96-ac1e-a2962a402336}"
        profiles = data.setdefault("profiles", {})
        prof_list = profiles.setdefault("list", [])
        if not any(p.get("guid") == ps7_guid or p.get("source") == "Windows.Terminal.PowershellCore" for p in prof_list):
            prof_list.insert(0, {
                "guid": ps7_guid,
                "hidden": False,
                "name": "PowerShell",
                "source": "Windows.Terminal.PowershellCore"
            })
        if data.get("defaultProfile") != ps7_guid:
            data["defaultProfile"] = ps7_guid
            wt_settings.write_text(json.dumps(data, indent=4), encoding="utf-8")
    except Exception:
        pass

appdata = Path(os.environ.get("APPDATA", ""))
agy_settings = appdata / "Antigravity" / "User" / "settings.json"
try:
    agy_settings.parent.mkdir(parents=True, exist_ok=True)
    agy_data = {}
    if agy_settings.exists():
        try:
            agy_data = json.loads(agy_settings.read_text(encoding="utf-8-sig"))
        except Exception:
            agy_data = {}
    agy_data["terminal.integrated.defaultProfile.windows"] = "PowerShell"
    profiles_win = agy_data.setdefault("terminal.integrated.profiles.windows", {})
    profiles_win["PowerShell"] = {
        "path": pwsh_path,
        "icon": "terminal-powershell"
    }
    agy_settings.write_text(json.dumps(agy_data, indent=4), encoding="utf-8")
except Exception:
    pass
"@
    $setDefaultShellPy | & python - $preferredShellExe 2>$null | Out-Null
}

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
        cur.execute("SELECT key, value, updated_at FROM runtime_config")
        for k, v, ts in cur.fetchall():
            state["runtime_config"][k] = [v, ts]
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
Write-Host "[2/5] Deploying SkillsDB Engine (v3.7.9) and customizations-db Plugin..."
$deployPy = Join-Path $suiteRoot "deploy.py"
$deployPs1 = Join-Path $suiteRoot "deploy.ps1"

if (Test-Path $deployPy) {
    & python $deployPy --source $suiteRoot --target $geminiDir
} elseif (Test-Path $deployPs1) {
    & $deployPs1 -SourceDir $suiteRoot -TargetDir $geminiDir
} else {
    throw "Neither deploy.py nor deploy.ps1 found in $suiteRoot"
}

# Ensure global CLI wrapper prepends .local\bin so shells opened before IDE restart find Python 3
$cmdPath = Join-Path $agyBin "skillsdb.cmd"
if (Test-Path $cmdPath) {
    Set-Content -Path $cmdPath -Value "@echo off`r`nset `"PATH=%USERPROFILE%\.local\bin;%PATH%`"`r`npython `"%USERPROFILE%\.gemini\database\db_manager.py`" %*`r`n" -Encoding ASCII
}

# Deploy UTF-8 process manifests for Windows
& python $targetMgrPath fix-utf8 | Out-Null

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
    sys.path.insert(0, os.path.dirname(db_path))
    try:
        from db_manager import _infer_workspace_last_active_iso
    except Exception:
        _infer_workspace_last_active_iso = None
    for k, entry in state.get("runtime_config", {}).items():
        if isinstance(entry, list) and len(entry) >= 2:
            v, ts = entry[0], entry[1]
        else:
            v, ts = entry, None
        if v and v not in ("{}", "[]"):
            if str(k).startswith("workspace:") and _infer_workspace_last_active_iso:
                if not ts or str(ts).startswith("2026-10-28 12:18") or str(ts).startswith("2026-10-28 10:14"):
                    try:
                        from pathlib import Path as _P
                        ts = _infer_workspace_last_active_iso(_P(str(v)))
                    except Exception:
                        pass
            if ts:
                cur.execute(
                    "INSERT INTO runtime_config (key, value, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (k, v, ts)
                )
            else:
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
# STEP 3: Provision or Verify Antigravity Gitea Windows Service (v1.1.2)
# ---------------------------------------------------------------------------
if (-not $SkipGiteaService) {
    Write-Host "[3/5] Checking Antigravity Gitea Service (v1.1.2)..."
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
    $giteaDirs = @(
        $GiteaInstallPath,
        (Join-Path $GiteaInstallPath "scripts"),
        (Join-Path $GiteaInstallPath "custom\conf"),
        (Join-Path $GiteaInstallPath "data\gitea-repositories"),
        (Join-Path $GiteaInstallPath "data\avatars"),
        (Join-Path $GiteaInstallPath "log")
    )
    foreach ($d in $giteaDirs) {
        New-Item -ItemType Directory -Force -Path $d | Out-Null
    }

    # Copy service scripts and templates if not already in place
    $suiteGiteaDir = Join-Path $suiteRoot "gitea-service"
    if ((Test-Path $suiteGiteaDir) -and ($suiteGiteaDir -ne $GiteaInstallPath)) {
        Copy-Item (Join-Path $suiteGiteaDir "scripts\deploy-gitea.ps1") -Destination (Join-Path $GiteaInstallPath "scripts\deploy-gitea.ps1") -Force
        if (Test-Path (Join-Path $suiteGiteaDir "scripts\backup-gitea.ps1")) {
            Copy-Item (Join-Path $suiteGiteaDir "scripts\backup-gitea.ps1") -Destination (Join-Path $GiteaInstallPath "scripts\backup-gitea.ps1") -Force
        }
        if (Test-Path (Join-Path $suiteGiteaDir "custom\conf\app.example.ini")) {
            Copy-Item (Join-Path $suiteGiteaDir "custom\conf\app.example.ini") -Destination (Join-Path $GiteaInstallPath "custom\conf\app.example.ini") -Force
        }
    }

    $giteaExe = Join-Path $GiteaInstallPath "gitea.exe"
    if (-not (Test-Path $giteaExe)) {
        Write-Host "      gitea.exe not found at $giteaExe. Downloading official Gitea Windows binary..."
        $downloadUrl = "https://dl.gitea.com/gitea/1.22.6/gitea-1.22.6-windows-4.0-amd64.exe"
        try {
            if ($pwshCmd) {
                & $preferredShellExe -NoProfile -Command "`$ProgressPreference = 'SilentlyContinue'; Invoke-WebRequest -Uri '$downloadUrl' -OutFile '$giteaExe'"
            } else {
                Invoke-WebRequest -Uri $downloadUrl -OutFile $giteaExe -UseBasicParsing
            }
            Write-Host "      Downloaded Gitea binary to $giteaExe" -ForegroundColor Green
        } catch {
            Write-Warning "Could not download Gitea binary automatically: $_. Place gitea.exe in $GiteaInstallPath and re-run."
        }
    }

    # Pre-initialize app.ini and SQLite database under current user context before service start
    # so secondary drive installations (e.g. D:\Gitea) never suffer from SYSTEM-only file ownership.
    $giteaIni = Join-Path $GiteaInstallPath "custom\conf\app.ini"
    $giteaExampleIni = Join-Path $GiteaInstallPath "custom\conf\app.example.ini"
    $giteaDb = Join-Path $GiteaInstallPath "data\gitea.db"
    if ((Test-Path $giteaExe) -and (-not (Test-Path $giteaIni)) -and (Test-Path $giteaExampleIni)) {
        $resolvedInstallPath = (Resolve-Path $GiteaInstallPath).Path
        $iniContent = Get-Content $giteaExampleIni -Raw
        $iniContent = $iniContent.Replace("C:\Gitea", $resolvedInstallPath)
        $iniContent = $iniContent -replace "HTTP_PORT = 3000", "HTTP_PORT = $GiteaPort"
        $iniContent = $iniContent -replace "ROOT_URL = http://localhost:3000/", "ROOT_URL = http://localhost:$GiteaPort/"
        try {
            $secKey = (& $giteaExe generate secret SECRET_KEY 2>$null | Select-Object -Last 1).Trim()
            $intTok = (& $giteaExe generate secret INTERNAL_TOKEN 2>$null | Select-Object -Last 1).Trim()
            $jwtSec = (& $giteaExe generate secret JWT_SECRET 2>$null | Select-Object -Last 1).Trim()
        } catch {}
        if (-not $secKey) { $secKey = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N") }
        if (-not $intTok) { $intTok = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N") }
        if (-not $jwtSec) { $jwtSec = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N") }
        $iniContent = $iniContent -replace "GENERATE_A_RANDOM_SECRET_KEY_HERE", $secKey
        $iniContent = $iniContent -replace "GENERATE_A_RANDOM_JWT_TOKEN_HERE", $intTok
        $iniContent = $iniContent -replace "GENERATE_A_RANDOM_JWT_SECRET_HERE", $jwtSec
        Set-Content -Path $giteaIni -Value $iniContent -Encoding UTF8
        if (-not (Test-Path $giteaDb)) {
            & $giteaExe migrate --config $giteaIni --work-path $resolvedInstallPath 2>$null | Out-Null
        }
    }

    if ($existingSvc) {
        & sc.exe failure gitea reset= 86400 actions= restart/5000/restart/10000/restart/30000 2>$null | Out-Null
        & sc.exe failureflag gitea 1 2>$null | Out-Null
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
            & $deployGiteaScript -InstallPath $GiteaInstallPath -HttpPort $GiteaPort -ServiceAccount $ServiceAccount -ServicePassword $ServicePassword
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
                        param($scriptPath, $instPath, $port, $svcAcc, $svcPwd)
                        & $scriptPath -InstallPath $instPath -HttpPort $port -ServiceAccount $svcAcc -ServicePassword $svcPwd
                    } -ArgumentList $deployGiteaScript, $GiteaInstallPath, $GiteaPort, $ServiceAccount, $ServicePassword
                    $elevatedOk = [bool](Get-Service -Name "gitea" -ErrorAction SilentlyContinue)
                } catch {
                    Write-Warning "DPAPI elevation attempt did not complete: $_"
                }
            }

            if (-not $elevatedOk) {
                Write-Host "      Requesting standard Windows UAC elevation to register the 'gitea' service once..."
                try {
                    $extraArgs = ""
                    if ($ServiceAccount -ne "NT AUTHORITY\SYSTEM") {
                        $extraArgs += " -ServiceAccount `"$ServiceAccount`""
                        if ($ServicePassword) {
                            $extraArgs += " -ServicePassword `"$ServicePassword`""
                        }
                    }
                    $argList = "-NoProfile -ExecutionPolicy Bypass -File `"$deployGiteaScript`" -InstallPath `"$GiteaInstallPath`" -HttpPort $GiteaPort$extraArgs"
                    Start-Process -FilePath $preferredShellExe -Verb RunAs -ArgumentList $argList -Wait -WindowStyle Hidden
                } catch {
                    Write-Warning "UAC elevation was canceled or unavailable: $_"
                    Write-Warning "Run '$preferredShellExe -ExecutionPolicy Bypass -File `"$deployGiteaScript`" -InstallPath `"$GiteaInstallPath`"' as Administrator once to register the service."
                }
            }
        }
    }

    if ($BackupTargetPath) {
        $backupScript = Join-Path $GiteaInstallPath "scripts\backup-gitea.ps1"
        if (Test-Path $backupScript) {
            Write-Host "      Configuring daily Gitea backup task ($BackupTime -> $BackupTargetPath)..."
            try {
                & $backupScript -InstallPath $GiteaInstallPath -BackupTargetPath $BackupTargetPath -RegisterScheduledTask -TaskTime $BackupTime
            } catch {
                Write-Warning "Could not register scheduled backup task automatically: $_"
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
$checkConfigPy = @"
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
tok = conn.execute("SELECT value FROM runtime_config WHERE key='gitea_token'").fetchone()
usr = conn.execute("SELECT value FROM runtime_config WHERE key='gitea_user'").fetchone()
conn.close()
t_val = tok[0] if tok and tok[0] else ""
u_val = usr[0] if usr and usr[0] else ""
print(f"{bool(t_val)}|{u_val}")
"@
$cfgOut = ($checkConfigPy | & python - $targetDbPath | Out-String).Trim()
$cfgParts = $cfgOut -split '\|', 2
$hasToken = if ($cfgParts.Count -ge 1) { $cfgParts[0] } else { "False" }
$existingGiteaUser = if ($cfgParts.Count -ge 2) { $cfgParts[1] } else { "" }

if (-not $SkipGiteaService) {
    $giteaExe = Join-Path $GiteaInstallPath "gitea.exe"
    $giteaIni = Join-Path $GiteaInstallPath "custom\conf\app.ini"
    $giteaDb  = Join-Path $GiteaInstallPath "data\gitea.db"

    if ((Test-Path $giteaExe) -and (Test-Path $giteaIni)) {
        $prevEap = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            $rawIniText = Get-Content $giteaIni -Raw
            $cleanedIniText = [regex]::Replace($rawIniText, "(?m)^\s*DISABLE_GRAVATAR\s*=[^\r\n]*\r?\n?", "")
            if ($cleanedIniText -ne $rawIniText) {
                Set-Content -Path $giteaIni -Value $cleanedIniText -Encoding UTF8
            }

            if (-not (Test-Path $giteaDb)) {
                & $giteaExe migrate --config $giteaIni --work-path $GiteaInstallPath 2>$null | Out-Null
            }

            $userList = (& $giteaExe admin user list --config $giteaIni --work-path $GiteaInstallPath 2>$null | Out-String)
            $userExists = $userList -match "\b$([regex]::Escape($GiteaAdminUser))\b"

            if (-not $userExists) {
                $localEmail = "$GiteaAdminUser@local.dev"
                if ($GiteaAdminPassword) {
                    & $giteaExe admin user create --username $GiteaAdminUser --password $GiteaAdminPassword --email $localEmail --admin --must-change-password=false --config $giteaIni --work-path $GiteaInstallPath 2>$null | Out-Null
                } else {
                    & $giteaExe admin user create --username $GiteaAdminUser --email $localEmail --admin --random-password --must-change-password=false --config $giteaIni --work-path $GiteaInstallPath 2>$null | Out-Null
                }
                Write-Host "      Created local Gitea administrator account '$GiteaAdminUser' ($localEmail)." -ForegroundColor Green
            } elseif ($GiteaAdminPassword) {
                & $giteaExe admin user change-password --username $GiteaAdminUser --password $GiteaAdminPassword --must-change-password=false --config $giteaIni --work-path $GiteaInstallPath 2>$null | Out-Null
                Write-Host "      Updated password for local Gitea administrator account '$GiteaAdminUser'." -ForegroundColor Green
            }

            if ($hasToken -ne "True" -or ($existingGiteaUser -and $existingGiteaUser -ne $GiteaAdminUser)) {
                $tokenName = "antigraviton-$($env:COMPUTERNAME.ToLower())-$(Get-Date -Format 'yyyyMMddHHmmss')"
                $rawToken = (& $giteaExe admin user generate-access-token --username $GiteaAdminUser --token-name $tokenName --scopes "all" --raw --config $giteaIni --work-path $GiteaInstallPath 2>$null | Select-Object -Last 1).Trim()
                if ($rawToken -and $rawToken.Length -ge 20) {
                    & python $targetMgrPath gitea-config --url "http://127.0.0.1:$GiteaPort" --user $GiteaAdminUser --token $rawToken | Out-Null
                    Write-Host "      Automatically provisioned and registered Gitea API token for user '$GiteaAdminUser'." -ForegroundColor Green
                }
            } else {
                & python $targetMgrPath gitea-config
            }
        } catch {
            Write-Host "      Notice: Automatic user/token provisioning encountered an issue: $_"
        } finally {
            $ErrorActionPreference = $prevEap
        }
    } else {
        & python $targetMgrPath gitea-config
    }

    # Ensure Antigravity bot user (if present in gitea.db) has antigravity@local.dev linked for commit avatar resolution
    if (Test-Path $giteaDb) {
        $linkBotEmailPy = @"
import sqlite3, sys
db_path = sys.argv[1]
try:
    conn = sqlite3.connect(db_path, timeout=5.0)
    cur = conn.cursor()
    row = cur.execute("SELECT id FROM user WHERE lower_name='antigravity'").fetchone()
    if row:
        uid = row[0]
        cur.execute("UPDATE user SET email='antigravity@local.dev', keep_email_private=0 WHERE id=?", (uid,))
        cur.execute("UPDATE email_address SET is_primary=0 WHERE uid=?", (uid,))
        cur.execute(
            "INSERT INTO email_address (uid, email, lower_email, is_activated, is_primary) VALUES (?, 'antigravity@local.dev', 'antigravity@local.dev', 1, 1) "
            "ON CONFLICT(lower_email) DO UPDATE SET uid=excluded.uid, is_activated=1, is_primary=1",
            (uid,)
        )
        conn.commit()
    conn.close()
except Exception:
    pass
"@
        $linkBotEmailPy | & python - $giteaDb 2>$null | Out-Null
    }
} else {
    & python $targetMgrPath gitea-config
}

# ---------------------------------------------------------------------------
# STEP 5: Final Telemetry, Verification Gate, Optional Multi-Workspace Sync & Briefing
# ---------------------------------------------------------------------------
Write-Host "[5/5] Running final health checks and workspace learning..."
if (-not (Test-Path $targetDbPath)) {
    throw "Verification failed: Central database not found at $targetDbPath"
}

if (-not $SkipGiteaService) {
    $svcFinal = Get-Service -Name "gitea" -ErrorAction SilentlyContinue
    if (-not $svcFinal -or $svcFinal.Status -ne "Running") {
        throw "Verification failed: Windows service 'gitea' is not running (Status: $(if ($svcFinal) { $svcFinal.Status } else { 'NOT_FOUND' })). Ensure UAC service registration completed."
    }
    $apiOnline = $false
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            $healthResp = Invoke-RestMethod -Uri "http://127.0.0.1:$GiteaPort/api/healthz" -TimeoutSec 3 -ErrorAction Stop
            if ($healthResp.status -eq "pass") {
                $apiOnline = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $apiOnline) {
        throw "Verification failed: Gitea service is running but HTTP health check at http://127.0.0.1:$GiteaPort/api/healthz did not pass."
    }
}

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
Write-Host "   - SkillsDB Engine (v3.7.9): ~95% token reduction via on-demand FTS5 micro-skills"
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
