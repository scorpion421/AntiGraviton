<#
.SYNOPSIS
    Automated deployment and Windows Service provisioning script for Antigravity Gitea.

.DESCRIPTION
    Installs Gitea as an isolated local Windows Service on loopback (127.0.0.1:3000),
    configures environment variables to prevent DFS traversal issues, ensures UNC paths
    for Session 0 compatibility, provisions SQLite storage, and starts the service.

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
    [string]$InstallPath = $PSScriptRoot | Split-Path -Parent,

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
        $content = $content -replace "C:\\Gitea", $resolvedPath
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
    Write-Host "[4/6] Existing app.ini detected at $configPath. Verifying path alignment..."
    $escapedResolved = ($resolvedPath -replace "\\", "/")
    $rawIni = Get-Content $configPath -Raw
    $updatedIni = $rawIni
    $updatedIni = [regex]::Replace($updatedIni, "(?m)^(\s*ROOT\s*=\s*).+[/\\]data[/\\]gitea-repositories\s*$", "`${1}$escapedResolved/data/gitea-repositories")
    $updatedIni = [regex]::Replace($updatedIni, "(?m)^(\s*LFS_CONTENT_PATH\s*=\s*).+[/\\]data[/\\]lfs\s*$", "`${1}$escapedResolved/data/lfs")
    $updatedIni = [regex]::Replace($updatedIni, "(?m)^(\s*PATH\s*=\s*).+[/\\]data[/\\]gitea\.db\s*$", "`${1}$escapedResolved/data/gitea.db")
    $updatedIni = [regex]::Replace($updatedIni, "(?m)^(\s*APP_DATA_PATH\s*=\s*).+[/\\]data\s*$", "`${1}$escapedResolved/data")
    $updatedIni = [regex]::Replace($updatedIni, "(?m)^(\s*ROOT_PATH\s*=\s*).+[/\\]log\s*$", "`${1}$escapedResolved/log")
    if ($updatedIni -ne $rawIni) {
        Set-Content -Path $configPath -Value $updatedIni -Encoding UTF8
        Write-Host "  Relocated paths in app.ini updated to: $escapedResolved" -ForegroundColor Yellow
        try {
            & $binaryPath admin regenerate hooks --config "$configPath" --work-path "$resolvedPath" | Out-Null
            Write-Host "  Regenerated Git hooks for new location." -ForegroundColor Green
        } catch {
            Write-Warning "Hook regeneration skipped: $_"
        }
    } else {
        Write-Host "  All paths in app.ini match $escapedResolved."
    }
}

# 5. Windows Service creation or update
Write-Host "[5/6] Configuring Windows Service '$ServiceName'..."
$workPath = $resolvedPath
$binPathCmd = "`"$binaryPath`" web --config `"$configPath`" --work-path `"$workPath`""

$serviceExists = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($serviceExists) {
    Write-Host "  Service '$ServiceName' already exists. Updating binary path..."
    & sc.exe config $ServiceName binPath= $binPathCmd start= delayed-auto depend= LanmanWorkstation | Out-Null
} else {
    Write-Host "  Creating new service '$ServiceName'..."
    if ($ServiceAccount -ne "NT AUTHORITY\SYSTEM" -and $ServicePassword) {
        & sc.exe create $ServiceName binPath= $binPathCmd start= delayed-auto depend= LanmanWorkstation DisplayName= $ServiceDisplayName obj= $ServiceAccount password= $ServicePassword | Out-Null
    } else {
        & sc.exe create $ServiceName binPath= $binPathCmd start= delayed-auto depend= LanmanWorkstation DisplayName= $ServiceDisplayName obj= $ServiceAccount | Out-Null
    }
}

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
