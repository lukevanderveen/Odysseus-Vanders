#Requires -Version 5.1
<#
  Odysseus - desktop launcher for Windows.

  Double-click (via the shortcut from create-desktop-shortcut.ps1) and Odysseus
  opens in its own chrome-less app window, like a native program:

    1. If the server is already running, just open (or re-open) the window.
    2. Otherwise start ChromaDB (if installed) and uvicorn hidden in the
       background, wait until /api/health answers, then open the window.
    3. When the app window is closed, stop the server processes this launcher
       started. Nothing is left running in the background.

  The window is a Chromium "--app=" window (Edge / Chrome / Brave / Chromium)
  with its own browser profile under data\desktop-browser-profile, so it gets
  its own taskbar entry, icon and cookies. If no Chromium browser is found the
  UI opens in the default browser instead.

  This is a *launcher*: it drives the venv that launch-windows.ps1 created and
  does not install anything. Run launch-windows.ps1 once first.

  Usage:
    powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File .\odysseus-desktop.ps1
    powershell -ExecutionPolicy Bypass -File .\odysseus-desktop.ps1 -Port 7000
#>
param(
    [int]$Port = 7000,
    [string]$BindHost = "127.0.0.1",
    [int]$StartupTimeoutSec = 180
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
Add-Type -AssemblyName System.Windows.Forms

$Url = "http://127.0.0.1:$Port"
$HealthUrl = "$Url/api/health"
$VenvPy = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
$ProfileDir = Join-Path $PSScriptRoot "data\desktop-browser-profile"
$LogDir = Join-Path $PSScriptRoot "logs"

function Show-Error($msg) {
    [System.Windows.Forms.MessageBox]::Show($msg, "Odysseus",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
    exit 1
}

function Test-Healthy {
    try {
        $resp = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 2
        return $resp.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Test-PortListening([int]$p) {
    try {
        $probe = New-Object Net.Sockets.TcpClient
        $probe.Connect("127.0.0.1", $p); $probe.Close()
        return $true
    } catch {
        return $false
    }
}

# Locate a Chromium-based browser for app mode. First hit wins.
function Find-ChromiumBrowser {
    $pf = ${env:ProgramFiles}
    $pf86 = ${env:ProgramFiles(x86)}
    $local = $env:LOCALAPPDATA
    $candidates = @(
        (Join-Path $pf "Google\Chrome\Application\chrome.exe"),
        (Join-Path $local "Google\Chrome\Application\chrome.exe"),
        (Join-Path $pf86 "Microsoft\Edge\Application\msedge.exe"),
        (Join-Path $pf "Microsoft\Edge\Application\msedge.exe"),
        (Join-Path $pf "BraveSoftware\Brave-Browser\Application\brave.exe"),
        (Join-Path $local "BraveSoftware\Brave-Browser\Application\brave.exe"),
        (Join-Path $local "Chromium\Application\chrome.exe")
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) { return $c }
    }
    return $null
}

# Open the UI. Returns the browser process when one was spawned in app mode,
# $null when the default browser was used (nothing to wait on in that case).
function Open-Ui {
    $browser = Find-ChromiumBrowser
    if (-not $browser) {
        Start-Process $Url
        return $null
    }
    if (-not (Test-Path $ProfileDir)) { New-Item -ItemType Directory -Path $ProfileDir | Out-Null }
    $browserArgs = @(
        "--app=$Url",
        "--user-data-dir=$ProfileDir",
        "--new-window",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=msEdgeSidebarV2,msHubApps"
    )
    return Start-Process -FilePath $browser -ArgumentList $browserArgs -PassThru
}

# Browser subprocesses all carry our --user-data-dir on their command line, so
# this finds every process belonging to the app window, including the case
# where the launch was forwarded to an already-running instance.
function Get-AppWindowProcesses {
    $needle = $ProfileDir.ToLowerInvariant()
    Get-CimInstance Win32_Process -Filter "Name='chrome.exe' OR Name='msedge.exe' OR Name='brave.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine.ToLowerInvariant().Contains($needle) } |
        ForEach-Object { Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue }
}

function Wait-AppWindowClosed($spawned) {
    Start-Sleep -Seconds 3
    $procs = @(Get-AppWindowProcesses)
    if ($spawned -and -not $spawned.HasExited) { $procs += $spawned }
    while ($procs.Count -gt 0) {
        $procs | Wait-Process -ErrorAction SilentlyContinue
        $procs = @(Get-AppWindowProcesses)
    }
}

function Stop-Started($proc) {
    if (-not $proc) { return }
    try {
        if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    } catch { }
}

# ── 1. Already running? Just open the window. ──────────────────────────────
if (Test-Healthy) {
    Open-Ui | Out-Null
    exit 0
}

if (-not (Test-Path $VenvPy)) {
    Show-Error "Odysseus isn't set up yet.`n`nOpen PowerShell in:`n$PSScriptRoot`n`nand run:`npowershell -ExecutionPolicy Bypass -File .\launch-windows.ps1"
}

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

# ── 2. ChromaDB (optional - the app degrades gracefully without it). ───────
$ChromaProc = $null
$chromaExe = Join-Path $PSScriptRoot ".chroma-venv\Scripts\chroma.exe"
if ((Test-Path $chromaExe) -and -not (Test-PortListening 8100)) {
    $ChromaProc = Start-Process -FilePath $chromaExe `
        -ArgumentList @("run", "--host", "localhost", "--port", "8100", "--path", (Join-Path $PSScriptRoot "data\chroma")) `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $LogDir "chroma.out.log") `
        -RedirectStandardError (Join-Path $LogDir "chroma.err.log")
}

# ── 3. Start the server hidden. ────────────────────────────────────────────
$ServerProc = Start-Process -FilePath $VenvPy `
    -ArgumentList @("-m", "uvicorn", "app:app", "--host", $BindHost, "--port", "$Port") `
    -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $LogDir "odysseus-app.out.log") `
    -RedirectStandardError (Join-Path $LogDir "odysseus-app.err.log")

# ── 4. Wait for readiness (first run may download an embedding model). ─────
$deadline = (Get-Date).AddSeconds($StartupTimeoutSec)
$ready = $false
while ((Get-Date) -lt $deadline) {
    if ($ServerProc.HasExited) {
        Stop-Started $ChromaProc
        Show-Error "Odysseus failed to start.`n`nSee:`n$(Join-Path $LogDir 'odysseus-app.err.log')"
    }
    if (Test-Healthy) { $ready = $true; break }
    Start-Sleep -Milliseconds 500
}
if (-not $ready) {
    Stop-Started $ServerProc
    Stop-Started $ChromaProc
    Show-Error "Odysseus did not become ready within $StartupTimeoutSec seconds.`n`nSee:`n$(Join-Path $LogDir 'odysseus-app.err.log')"
}

# ── 5. Open the window; closing it stops what we started. ──────────────────
$BrowserProc = Open-Ui
if (-not $BrowserProc) {
    # Default browser: we can't tell when the tab closes, so leave the server
    # running. Stop it from Task Manager (python.exe) or by re-running
    # launch-windows.ps1 when you want it gone.
    exit 0
}

try {
    Wait-AppWindowClosed $BrowserProc
} finally {
    Stop-Started $ServerProc
    Stop-Started $ChromaProc
}
exit 0
