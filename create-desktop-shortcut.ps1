#Requires -Version 5.1
<#
  Odysseus - put an "Odysseus" icon on the Windows desktop.

  The shortcut runs odysseus-desktop.ps1 with a hidden console, so a
  double-click opens Odysseus straight into its own app window.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\create-desktop-shortcut.ps1
    powershell -ExecutionPolicy Bypass -File .\create-desktop-shortcut.ps1 -Port 7000 -StartMenu

  Re-running overwrites the existing shortcut. Delete it like any other icon.
#>
param(
    [int]$Port = 7000,
    [string]$Name = "Odysseus",
    [switch]$StartMenu
)

$ErrorActionPreference = "Stop"

$launcher = Join-Path $PSScriptRoot "odysseus-desktop.ps1"
$icon = Join-Path $PSScriptRoot "docs\odysseus.ico"
if (-not (Test-Path $launcher)) { throw "Launcher not found: $launcher" }

$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`" -Port $Port"

function New-OdysseusShortcut([string]$folder) {
    if (-not (Test-Path $folder)) { New-Item -ItemType Directory -Path $folder | Out-Null }
    $path = Join-Path $folder "$Name.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $powershell
    $lnk.Arguments = $arguments
    $lnk.WorkingDirectory = $PSScriptRoot
    $lnk.Description = "Odysseus - local AI workspace"
    $lnk.WindowStyle = 7   # minimized; the launcher hides itself anyway
    if (Test-Path $icon) { $lnk.IconLocation = "$icon,0" }
    $lnk.Save()
    return $path
}

# GetFolderPath follows OneDrive "Known Folder" redirection; $env:USERPROFILE\Desktop does not.
$desktop = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
$created = @(New-OdysseusShortcut $desktop)

if ($StartMenu) {
    $programs = [Environment]::GetFolderPath([Environment+SpecialFolder]::Programs)
    $created += New-OdysseusShortcut $programs
}

Write-Host "Created:" -ForegroundColor Green
$created | ForEach-Object { Write-Host "  $_" }
if (-not (Test-Path $icon)) {
    Write-Host "NOTE: docs\odysseus.ico not found - shortcut uses the PowerShell icon." -ForegroundColor Yellow
}
