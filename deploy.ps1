<#
.SYNOPSIS
    Deploys the Central Customizations Database and Antigravity Plugin.
.DESCRIPTION
    Installs the central SQLite database (customizations.db), management script,
    and Antigravity customizations-db plugin for the active user.
    Optimizes ~/.gemini/config/config.json to prevent token budget overflow.
    Uses dynamic path detection with zero hardcoded paths or usernames.
.PARAMETER SourceDir
    Optional. The directory containing the deployment assets. Defaults to the script directory.
.PARAMETER TargetDir
    Optional. The target .gemini configuration directory. Defaults to $env:USERPROFILE\.gemini.
#>

[CmdletBinding()]
param(
    [string]$SourceDir = "",
    [string]$TargetDir = ""
)

$ErrorActionPreference = "Stop"

# Dynamically resolve source directory
if (-not $SourceDir) {
    if ($PSScriptRoot) {
        $SourceDir = $PSScriptRoot
    } elseif ($MyInvocation.MyCommand.Path) {
        $SourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    } else {
        $SourceDir = (Get-Location).Path
    }
}

# Dynamically resolve target directory
if (-not $TargetDir) {
    $userHome = $env:USERPROFILE
    if (-not $userHome) {
        $userHome = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::UserProfile)
    }
    $TargetDir = Join-Path $userHome ".gemini"
}

$targetDbDir = Join-Path $TargetDir "database"
$targetConfigDir = Join-Path $TargetDir "config"
$targetPluginsDir = Join-Path $targetConfigDir "plugins"
$targetCustomizationsPlugin = Join-Path $targetPluginsDir "customizations-db"
$configJsonPath = Join-Path $targetConfigDir "config.json"

Write-Host "============================================================"
Write-Host "Antigravity Customizations Database Deployment"
Write-Host "Source: $SourceDir"
Write-Host "Target: $TargetDir"
Write-Host "============================================================"

# 1. Ensure target directories exist
Write-Host "[1/5] Ensuring target directories exist..."
New-Item -ItemType Directory -Force -Path $targetDbDir | Out-Null
New-Item -ItemType Directory -Force -Path $targetPluginsDir | Out-Null
New-Item -ItemType Directory -Force -Path $targetCustomizationsPlugin | Out-Null

# 2. Copy Database and Management CLI
Write-Host "[2/5] Deploying customizations database and manager..."
$sourceDb = Join-Path $SourceDir "database\customizations.db"
$sourceMgr = Join-Path $SourceDir "database\db_manager.py"

if (-not (Test-Path $sourceDb) -or -not (Test-Path $sourceMgr)) {
    throw "Source database files not found in $SourceDir\database"
}

Copy-Item $sourceDb -Destination (Join-Path $targetDbDir "customizations.db") -Force
Copy-Item $sourceMgr -Destination (Join-Path $targetDbDir "db_manager.py") -Force
Write-Host "      Database deployed to: $targetDbDir"

# Deploy global CLI wrapper to ~/.gemini/antigravity/bin (in system PATH)
$targetBinDir = Join-Path $TargetDir "antigravity\bin"
New-Item -ItemType Directory -Force -Path $targetBinDir | Out-Null
$cmdPath = Join-Path $targetBinDir "skillsdb.cmd"
$cmdContent = "@echo off`r`npython `"%USERPROFILE%\.gemini\database\db_manager.py`" %*`r`n"
Set-Content -Path $cmdPath -Value $cmdContent -Encoding ASCII
Write-Host "      Global CLI wrapper deployed to: $cmdPath"

# 3. Deploy Antigravity Plugin
Write-Host "[3/5] Deploying customizations-db plugin..."
$sourcePluginDir = Join-Path $SourceDir "plugin"
if (-not (Test-Path $sourcePluginDir)) {
    throw "Source plugin directory not found at $sourcePluginDir"
}

Copy-Item "$sourcePluginDir\*" -Destination $targetCustomizationsPlugin -Recurse -Force
Write-Host "      Plugin deployed to: $targetCustomizationsPlugin"

# 4. Isolate legacy plugins and update config.json to prevent token budget overflow
Write-Host "[4/6] Isolating legacy plugins and updating config.json..."

$targetArchivePluginsDir = Join-Path $TargetDir "plugins_archive"
New-Item -ItemType Directory -Force -Path $targetArchivePluginsDir | Out-Null

# Clean up any legacy plugins_disabled inside config/ to prevent Antigravity discovery
$legacyDisabledInConfig = Join-Path $targetConfigDir "plugins_disabled"
if (Test-Path $legacyDisabledInConfig) {
    Remove-Item -Path $legacyDisabledInConfig -Recurse -Force
}

# Physically archive legacy plugins to plugins_archive (outside config/) so Antigravity never discovers them or launches unneeded MCP servers
if (Test-Path $targetPluginsDir) {
    $existingPlugins = Get-ChildItem -Path $targetPluginsDir -Directory -ErrorAction SilentlyContinue
    foreach ($pItem in $existingPlugins) {
        if ($pItem.Name -ne "customizations-db") {
            $destDir = Join-Path $targetArchivePluginsDir $pItem.Name
            if (Test-Path $destDir) {
                Remove-Item -Path $destDir -Recurse -Force
            }
            Move-Item -Path $pItem.FullName -Destination $targetArchivePluginsDir -Force
            Write-Host "      Archived plugin to plugins_archive: $($pItem.Name)"
        }
    }
}

$pluginsToDisable = @(
    "communication-style",
    "local-admin",
    "scripting-rules",
    "android-cli-plugin",
    "chrome-devtools-plugin",
    "data-agent-kit-plugin",
    "firebase",
    "flutter",
    "gemini-api",
    "google-antigravity-sdk",
    "google_maps_platform",
    "modern-web-guidance-plugin",
    "science"
)

# Parse or create config object preserving all existing properties across PS 5.1 and 7+
$parsedConfig = $null
if (Test-Path $configJsonPath) {
    try {
        $rawJson = Get-Content -Path $configJsonPath -Raw -Encoding UTF8
        $parsedConfig = ConvertFrom-Json $rawJson
    } catch {
        Write-Warning "Could not parse existing config.json. Initializing clean object."
    }
}

if (-not $parsedConfig) {
    $parsedConfig = New-Object PSObject
}

if (-not $parsedConfig.PSObject.Properties['plugins']) {
    $parsedConfig | Add-Member -NotePropertyName 'plugins' -NotePropertyValue (New-Object PSObject)
}

# Update plugin states
$parsedConfig.plugins | Add-Member -NotePropertyName 'customizations-db' -NotePropertyValue ([PSCustomObject]@{ enabled = $true }) -Force

foreach ($p in $pluginsToDisable) {
    $parsedConfig.plugins | Add-Member -NotePropertyName $p -NotePropertyValue ([PSCustomObject]@{ enabled = $false }) -Force
}

$updatedJson = ConvertTo-Json $parsedConfig -Depth 10
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($configJsonPath, $updatedJson, $utf8NoBom)
Write-Host "      config.json updated successfully (UTF-8 without BOM)."

# 5. Verification
Write-Host "[5/6] Verifying database connectivity..."
$pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if ($pythonExe) {
    $mgrScript = Join-Path $targetDbDir "db_manager.py"
    & python $mgrScript stats
} else {
    Write-Host "Python executable not found in PATH. Skipping runtime verification."
}

# 6. Global Git Ignore configuration for .agents/memory.db
Write-Host "[6/6] Ensuring global Git ignore for project memory..."
$gitExe = (Get-Command git -ErrorAction SilentlyContinue).Source
if ($gitExe) {
    try {
        Push-Location $userHome
        try {
            $globalIgnore = & git config --global core.excludesfile
            if (-not $globalIgnore) {
                $globalIgnore = (Join-Path $userHome ".gitignore_global") -replace "\\", "/"
                & git config --global core.excludesfile "$globalIgnore"
            } else {
                if ($globalIgnore.StartsWith("~")) {
                    $globalIgnore = Join-Path $userHome $globalIgnore.Substring(1).TrimStart('\', '/')
                }
            }
        } finally {
            Pop-Location
        }
        
        $entriesToAdd = @(
            ".agents/memory.db",
            ".agents/memory.db-wal",
            ".agents/memory.db-shm"
        )
        
        $existingContent = ""
        if (Test-Path $globalIgnore) {
            $existingContent = Get-Content -Path $globalIgnore -Raw
        }
        
        $missingEntries = @()
        foreach ($entry in $entriesToAdd) {
            if ($existingContent -notmatch [regex]::Escape($entry)) {
                $missingEntries += $entry
            }
        }
        
        if ($missingEntries.Count -gt 0) {
            $header = "`n# Antigravity project memory databases`n"
            Add-Content -Path $globalIgnore -Value ($header + ($missingEntries -join "`n"))
            Write-Host "      Added project memory ignore rules to: $globalIgnore"
        } else {
            Write-Host "      Global Git ignore already configured at: $globalIgnore"
        }
    } catch {
        Write-Host "      Notice: Could not configure global git ignore: $_"
    }
} else {
    Write-Host "      Git executable not found. Skipping global git ignore."
}

Write-Host "============================================================"
Write-Host "Deployment completed successfully!"
Write-Host "Customizations database is now active for this user profile."
Write-Host "============================================================"
