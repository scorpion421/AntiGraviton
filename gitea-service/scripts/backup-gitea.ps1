<#
.SYNOPSIS
    Automated online Gitea backup script and optional Windows Scheduled Task installer.

.DESCRIPTION
    Executes a live 'gitea.exe dump' (conflict-free thanks to LevelDB-free immediate queues),
    stores the timestamped zip archive in BackupTargetPath, prunes archives older than
    RetentionDays, and optionally registers a daily Windows Scheduled Task (default 03:00).

.PARAMETER InstallPath
    The Gitea installation directory containing gitea.exe and custom\conf\app.ini.

.PARAMETER BackupTargetPath
    Destination directory (local drive or UNC network share) where backup archives are stored.

.PARAMETER RetentionDays
    Number of days to keep backup archives before pruning (default: 14).

.PARAMETER RegisterScheduledTask
    If set, registers or updates the daily 'Antigravity-Gitea-Backup' Windows Scheduled Task.

.PARAMETER TaskTime
    Daily execution time in HH:mm format when RegisterScheduledTask is used (default: "03:00").
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$InstallPath = (Split-Path -Parent $PSScriptRoot),

    [Parameter(Mandatory = $true)]
    [string]$BackupTargetPath,

    [Parameter(Mandatory = $false)]
    [int]$RetentionDays = 14,

    [Parameter(Mandatory = $false)]
    [switch]$RegisterScheduledTask,

    [Parameter(Mandatory = $false)]
    [string]$TaskTime = "03:00"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$resolvedInstall = (Resolve-Path $InstallPath).Path
$giteaExe = Join-Path $resolvedInstall "gitea.exe"
$configPath = Join-Path $resolvedInstall "custom\conf\app.ini"

if (-not (Test-Path $giteaExe)) {
    throw "gitea.exe not found at $giteaExe"
}

if ($RegisterScheduledTask) {
    $taskName = "Antigravity-Gitea-Backup"
    $pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
    $shellExe = if ($pwshCmd) { $pwshCmd.Source } else { "powershell.exe" }
    $scriptPath = $MyInvocation.MyCommand.Path
    $argStr = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -InstallPath `"$resolvedInstall`" -BackupTargetPath `"$BackupTargetPath`" -RetentionDays $RetentionDays"

    $action = New-ScheduledTaskAction -Execute $shellExe -Argument $argStr -WorkingDirectory $resolvedInstall
    $trigger = New-ScheduledTaskTrigger -Daily -At $TaskTime
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Daily Antigravity Gitea online backup to $BackupTargetPath" -Force | Out-Null
    Write-Host "Registered daily scheduled task '$taskName' at $TaskTime -> $BackupTargetPath" -ForegroundColor Green
    return
}

New-Item -ItemType Directory -Force -Path $BackupTargetPath | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$hostTag = $env:COMPUTERNAME.ToLower()
$archiveFile = Join-Path $BackupTargetPath "gitea-backup-$hostTag-$timestamp.zip"
$tempWorkDir = Join-Path ([System.IO.Path]::GetTempPath()) "gitea_dump_$timestamp"
New-Item -ItemType Directory -Force -Path $tempWorkDir | Out-Null

try {
    & $giteaExe dump --config $configPath --work-path $resolvedInstall --tempdir $tempWorkDir --file $archiveFile
    if (-not (Test-Path $archiveFile)) {
        throw "Gitea dump completed without creating archive at $archiveFile"
    }
    Write-Host "Created Gitea backup archive: $archiveFile" -ForegroundColor Green
} finally {
    if (Test-Path $tempWorkDir) {
        Remove-Item -Path $tempWorkDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if ($RetentionDays -gt 0) {
    $cutoff = (Get-Date).AddDays(-$RetentionDays)
    Get-ChildItem -Path $BackupTargetPath -Filter "gitea-backup-*.zip" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt $cutoff } |
        ForEach-Object {
            Remove-Item -Path $_.FullName -Force -ErrorAction SilentlyContinue
            Write-Host "Pruned old backup archive: $($_.Name)"
        }
}
