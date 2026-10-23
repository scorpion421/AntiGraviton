<#
.SYNOPSIS
    Automated deployment and Windows Service provisioning script for Antigravity Gitea.

.DESCRIPTION
    Installs Gitea as an isolated local Windows Service on loopback (127.0.0.1:3000),
    configures environment variables to prevent DFS traversal issues, ensures UNC paths
    for Session 0 compatibility, provisions SQLite storage and local drive ACLs, and starts the service.

.PARAMETER InstallPath
    The directory containing gitea.exe and configuration.
    For network or home shares, provide the canonical UNC path (e.g. \\server\share\Gitea).

.PARAMETER ServiceAccount
    The service user account (e.g. DOMAIN\User.adm or NT AUTHORITY\SYSTEM).
    If using a domain admin account, ensure SeServiceLogonRight is assigned.

.PARAMETER ServicePassword
    Password for the service account (if not running as SYSTEM).

.PARAMETER HttpPort
    Local HTTP port for Gitea (default: 3000).

.PARAMETER GitCeilingDirectories
    Semicolon-separated directories preventing Git from ascending to share roots.

.EXAMPLE
    .\deploy-gitea.ps1 -InstallPath "C:\Gitea" -ServiceAccount "NT AUTHORITY\SYSTEM"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$InstallPath = (Split-Path -Parent $PSScriptRoot),

    [Parameter(Mandatory = $false)]
    [string]$ServiceAccount = "NT AUTHORITY\SYSTEM",

    [Parameter(Mandatory = $false)]
    [string]$ServicePassword = "",

    [Parameter(Mandatory = $false)]
    [int]$HttpPort = 3000,

    [Parameter(Mandatory = $false)]
    [string]$ServiceName = "gitea",

    [Parameter(Mandatory = $false)]
    [string]$ServiceDisplayName = "Antigravity Gitea Service",

    [Parameter(Mandatory = $false)]
    [string]$GitCeilingDirectories = "$env:USERPROFILE"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Antigravity Gitea Windows Service Deployment" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# 1. Resolve canonical path
$resolvedPath = (Resolve-Path $InstallPath).Path
Write-Host "[1/6] Target Installation Path: $resolvedPath"

$binaryPath = Join-Path $resolvedPath "gitea.exe"
if (-not (Test-Path $binaryPath)) {
    throw "gitea.exe not found at: $binaryPath. Please download gitea-windows-amd64.exe and place it in the installation directory."
}

# 2. Directory structure
Write-Host "[2/6] Verifying directory structure..."
$dirs = @(
    (Join-Path $resolvedPath "custom\conf"),
    (Join-Path $resolvedPath "data\gitea-repositories"),
    (Join-Path $resolvedPath "data\avatars"),
    (Join-Path $resolvedPath "log")
)
foreach ($d in $dirs) {
    if (-not (Test-Path $d)) {
        New-Item -Path $d -ItemType Directory -Force | Out-Null
        Write-Host "  Created: $d"
    }
}

# 3. Environment configuration: GIT_CEILING_DIRECTORIES
Write-Host "[3/6] Configuring system-wide GIT_CEILING_DIRECTORIES..."
$envKey = "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
try {
    $currentVal = (Get-ItemProperty -Path $envKey -Name "GIT_CEILING_DIRECTORIES" -ErrorAction SilentlyContinue).GIT_CEILING_DIRECTORIES
    if (-not $currentVal) {
        Set-ItemProperty -Path $envKey -Name "GIT_CEILING_DIRECTORIES" -Value $GitCeilingDirectories
        Write-Host "  Set GIT_CEILING_DIRECTORIES to: $GitCeilingDirectories"
    } else {
        Write-Host "  GIT_CEILING_DIRECTORIES already present: $currentVal"
    }
} catch {
    Write-Warning "Could not set machine-wide GIT_CEILING_DIRECTORIES (requires administrator privileges): $_"
}

# 4. Generate or self-heal app.ini on drive/folder relocation
$configPath = Join-Path $resolvedPath "custom\conf\app.ini"
if (-not (Test-Path $configPath)) {
    Write-Host "[4/6] Generating initial app.ini configuration with fresh cryptographic secrets..."
    $examplePath = Join-Path $resolvedPath "custom\conf\app.example.ini"
    if (Test-Path $examplePath) {
        $content = Get-Content $examplePath -Raw
        $content = $content.Replace("C:\Gitea", $resolvedPath)
        $content = $content -replace "HTTP_PORT = 3000", "HTTP_PORT = $HttpPort"
        $content = $content -replace "ROOT_URL = http://localhost:3000/", "ROOT_URL = http://localhost:$HttpPort/"

        # Generate unique cryptographic secrets via gitea.exe (with CSPRNG fallback)
        try {
            $secKey = (& $binaryPath generate secret SECRET_KEY 2>$null | Select-Object -Last 1).Trim()
            $intTok = (& $binaryPath generate secret INTERNAL_TOKEN 2>$null | Select-Object -Last 1).Trim()
            $jwtSec = (& $binaryPath generate secret JWT_SECRET 2>$null | Select-Object -Last 1).Trim()
        } catch {}
        if (-not $secKey) { $secKey = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N") }
        if (-not $intTok) { $intTok = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N") }
        if (-not $jwtSec) { $jwtSec = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N") }

        $content = $content -replace "GENERATE_A_RANDOM_SECRET_KEY_HERE", $secKey
        $content = $content -replace "GENERATE_A_RANDOM_JWT_TOKEN_HERE", $intTok
        $content = $content -replace "GENERATE_A_RANDOM_JWT_SECRET_HERE", $jwtSec

        Set-Content -Path $configPath -Value $content -Encoding UTF8
        Write-Host "  Generated $configPath from template with unique cryptographic secrets."
    } else {
        Write-Warning "Template $examplePath not found. Please provide custom\conf\app.ini manually."
    }
} else {
    Write-Host "[4/6] Existing app.ini detected at $configPath. Verifying path alignment and queue configuration..."
    $escapedResolvedSlash = ($resolvedPath -replace "\\", "/") -replace '\$', '$$'
    $escapedResolvedBackslash = $resolvedPath -replace '\$', '$$'
    $rawIni = Get-Content $configPath -Raw
    $updatedIni = $rawIni
    $updatedIni = [regex]::Replace($updatedIni, "(?m)^(\s*WORK_PATH\s*=\s*)[^\r\n]+$", "`${1}$escapedResolvedBackslash")
    $updatedIni = [regex]::Replace($updatedIni, "(?m)^(\s*ROOT\s*=\s*).+[/\\]data[/\\]gitea-repositories\s*$", "`${1}$escapedResolvedBackslash\data\gitea-repositories")
    $updatedIni = [regex]::Replace($updatedIni, "(?m)^(\s*LFS_CONTENT_PATH\s*=\s*).+[/\\]data[/\\]lfs\s*$", "`${1}$escapedResolvedSlash/data/lfs")
    $updatedIni = [regex]::Replace($updatedIni, "(?m)^(\s*PATH\s*=\s*).+[/\\]data[/\\]gitea\.db\s*$", "`${1}$escapedResolvedBackslash\data\gitea.db")
    $updatedIni = [regex]::Replace($updatedIni, "(?m)^(\s*APP_DATA_PATH\s*=\s*).+[/\\]data\s*$", "`${1}$escapedResolvedSlash/data")
    $updatedIni = [regex]::Replace($updatedIni, "(?m)^(\s*ROOT_PATH\s*=\s*).+[/\\]log\s*$", "`${1}$escapedResolvedBackslash\log")

    # Ensure LevelDB-free immediate queue and db indexer configuration is present to prevent Windows file lock errors during 'gitea dump'
    if ($updatedIni -notmatch "(?m)^\s*\[queue\]") {
        $updatedIni = $updatedIni.TrimEnd() + "`r`n`r`n[queue]`r`nTYPE = immediate`r`n`r`n[queue.common]`r`nTYPE = immediate`r`n"
    }
    if ($updatedIni -notmatch "(?m)^\s*\[indexer\]") {
        $updatedIni = $updatedIni.TrimEnd() + "`r`n`r`n[indexer]`r`nISSUE_INDEXER_TYPE = db`r`nREPO_INDEXER_ENABLED = false`r`n"
    }

    if ($updatedIni -ne $rawIni) {
        Set-Content -Path $configPath -Value $updatedIni -Encoding UTF8
        Write-Host "  Relocated paths and queue settings in app.ini updated for: $resolvedPath" -ForegroundColor Yellow
        try {
            & $binaryPath admin regenerate hooks --config "$configPath" --work-path "$resolvedPath" | Out-Null
            Write-Host "  Regenerated Git hooks for new location." -ForegroundColor Green
        } catch {
            Write-Warning "Hook regeneration skipped: $_"
        }
    } else {
        Write-Host "  All paths and queue settings in app.ini match $resolvedPath."
    }
}

# Grant standard local users Modify permissions on local drive installations (e.g. C:\Gitea, D:\Gitea)
# so non-elevated CLI user/token provisioning and hook execution never hit NTFS ownership locks.
if ($resolvedPath -match '^[A-Za-z]:\\') {
    & icacls.exe $resolvedPath /grant "*S-1-5-32-545:(OI)(CI)M" /T /C /Q 2>$null | Out-Null
}

# 5. Windows Service creation or update
Write-Host "[5/6] Configuring Windows Service '$ServiceName'..."
$workPath = $resolvedPath
$binPathCmd = "`"$binaryPath`" web --config `"$configPath`" --work-path `"$workPath`""

$serviceExists = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($serviceExists) {
    Write-Host "  Service '$ServiceName' already exists. Updating service configuration..."
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    & sc.exe delete $ServiceName | Out-Null
    Start-Sleep -Seconds 1
}

Write-Host "  Registering service '$ServiceName'..."
if ($ServiceAccount -ne "NT AUTHORITY\SYSTEM" -and $ServicePassword) {
    $secPwd = ConvertTo-SecureString $ServicePassword -AsPlainText -Force
    $svcCred = New-Object System.Management.Automation.PSCredential ($ServiceAccount, $secPwd)
    New-Service -Name $ServiceName -BinaryPathName $binPathCmd -DisplayName $ServiceDisplayName -Description "Antigravity Local Git and SkillsDB Synchronization Engine" -StartupType Automatic -Credential $svcCred | Out-Null
} else {
    New-Service -Name $ServiceName -BinaryPathName $binPathCmd -DisplayName $ServiceDisplayName -Description "Antigravity Local Git and SkillsDB Synchronization Engine" -StartupType Automatic | Out-Null
}
& sc.exe config $ServiceName start= delayed-auto depend= LanmanWorkstation | Out-Null

# Set service description, auto-recovery actions (15s/30s/60s), and interactive user start/stop permissions
& sc.exe description $ServiceName "Antigravity Local Git & SkillsDB Synchronization Engine" | Out-Null
& sc.exe failure $ServiceName reset= 300 actions= restart/15000/restart/30000/restart/60000 | Out-Null
& sc.exe failureflag $ServiceName 1 | Out-Null
& sc.exe sdset $ServiceName "D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCLCSWRPWPDTLOCRRC;;;IU)(A;;CCLCSWRPWPDTLOCRRC;;;AU)(A;;CCLCSWLOCRRC;;;SU)" | Out-Null

# 6. Service start & health check
Write-Host "[6/6] Starting service and checking health..."
try {
    Start-Service -Name $ServiceName -ErrorAction Stop
} catch {
    Write-Warning "Could not start service directly: $_"
}

Start-Sleep -Seconds 3
$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
Write-Host "  Service Status: $(if ($svc) { $svc.Status } else { 'NOT_FOUND' })" -ForegroundColor Green

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Deployment completed." -ForegroundColor Cyan
Write-Host "Service URL: http://127.0.0.1:$HttpPort" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
