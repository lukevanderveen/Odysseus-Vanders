#Requires -Version 5.1
<#
  Odysseus - native Windows launcher (no Docker).

  One command to: create a virtualenv, install dependencies, run first-time
  setup (prints an admin password on first run), and start the server.
  Safe to re-run - it skips whatever already exists.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1
    powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1 -Port 7000 -BindHost 127.0.0.1

  Tip: bind 127.0.0.1 (default) for local-only use. Use 0.0.0.0 only when you
  intentionally want other devices on your LAN to reach it.
#>
param(
    [int]$Port = 7000,
    [string]$BindHost = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Step($msg) { Write-Host ""; Write-Host ("==> " + $msg) -ForegroundColor Cyan }
function Fail($msg) {
    Write-Host ""
    Write-Host ("ERROR: " + $msg) -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

# 1. Locate a Python interpreter (3.11+ required)
Write-Step "Checking for Python"
function Get-PythonVersionText($launcher, $launcherArgs) {
    try {
        return (& $launcher @launcherArgs -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null).Trim()
    } catch {
        return $null
    }
}

$pyExe = $null
$pyArgs = @()
$pyVersion = $null

$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    foreach ($v in @("-3.13", "-3.12", "-3.11")) {
        $ver = Get-PythonVersionText $pyLauncher.Source @($v)
        if ($ver) {
            $pyExe = $pyLauncher.Source
            $pyArgs = @($v)
            $pyVersion = $ver
            break
        }
    }
}

if (-not $pyExe) {
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        $ver = Get-PythonVersionText $pythonCmd.Source @()
        if ($ver) {
            $versionParts = $ver.Split('.')
            $major = [int]$versionParts[0]
            $minor = [int]$versionParts[1]
            if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 11)) {
                $pyExe = $pythonCmd.Source
                $pyVersion = $ver
            }
        }
    }
}

if (-not $pyExe) {
    Fail "Couldn't find Python 3.11+ for Windows setup. Install Python 3.11+ (or open the Python launcher with 'py -3.11') from https://www.python.org/downloads/, then re-run this script."
}
$pythonLabel = ("Using Python {0}: {1} {2}" -f $pyVersion, $pyExe, ($pyArgs -join ' ')).TrimEnd()
Write-Host $pythonLabel

# 2. Create the virtualenv if missing
$venvPy = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Step "Creating virtual environment (venv)"
    & $pyExe @pyArgs -m venv venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) { Fail "Failed to create the virtual environment." }
} else {
    Write-Host "venv already exists - skipping creation."
}

# 3. Install / update dependencies
Write-Step "Installing dependencies (first run can take a few minutes)"
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail "Dependency install failed. Scroll up for the pip error." }

# 4. First-time setup (creates data dirs, DB, .env, admin user)
Write-Step "Running first-time setup"
& $venvPy setup.py
if ($LASTEXITCODE -ne 0) { Fail "setup.py failed." }

# 5. Friendly note about Git Bash (full Cookbook / agent-shell parity)
if (-not (Get-Command bash -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "NOTE: Git Bash (bash.exe) was not found on PATH." -ForegroundColor Yellow
    Write-Host "      The core app works without it. For full Cookbook background" -ForegroundColor Yellow
    Write-Host "      downloads and the agent shell tool, install Git for Windows:" -ForegroundColor Yellow
    Write-Host "      https://git-scm.com/download/win" -ForegroundColor Yellow
}

# 6. Start the ChromaDB vector server (powers memory, document RAG, tool search).
#    Native (non-Docker) runs need a standalone Chroma server on localhost:8100 -
#    the app ships only the thin chromadb-client. The full server has its own
#    dependency pins, so it lives in a separate .chroma-venv to keep the app's
#    venv pristine. Idempotent: skipped if the port is already serving.
Write-Step "Starting ChromaDB vector server (localhost:8100)"
$chromaPort = 8100
$chromaListening = $false
try {
    $chromaListening = [bool](Get-NetTCPConnection -LocalPort $chromaPort -State Listen -ErrorAction SilentlyContinue)
} catch {
    try {
        $probe = New-Object Net.Sockets.TcpClient
        $probe.Connect("127.0.0.1", $chromaPort); $chromaListening = $true; $probe.Close()
    } catch { $chromaListening = $false }
}

if ($chromaListening) {
    Write-Host ("Something is already listening on {0} - reusing it." -f $chromaPort)
} else {
    $chromaPy = Join-Path $PSScriptRoot ".chroma-venv\Scripts\python.exe"
    $chromaExe = Join-Path $PSScriptRoot ".chroma-venv\Scripts\chroma.exe"
    if (-not (Test-Path $chromaExe)) {
        Write-Host "Setting up the Chroma server venv (first run only, downloads ~150MB)..."
        & $pyExe @pyArgs -m venv .chroma-venv
        & $chromaPy -m pip install --upgrade pip --quiet
        & $chromaPy -m pip install chromadb --quiet
    }
    if (Test-Path $chromaExe) {
        if (-not (Test-Path (Join-Path $PSScriptRoot "logs"))) { New-Item -ItemType Directory -Path (Join-Path $PSScriptRoot "logs") | Out-Null }
        $chromaData = Join-Path $PSScriptRoot "data\chroma"
        $chromaOut = Join-Path $PSScriptRoot "logs\chroma.out.log"
        $chromaErr = Join-Path $PSScriptRoot "logs\chroma.err.log"
        Start-Process -FilePath $chromaExe `
            -ArgumentList @("run", "--host", "localhost", "--port", "$chromaPort", "--path", $chromaData) `
            -WindowStyle Hidden -RedirectStandardOutput $chromaOut -RedirectStandardError $chromaErr | Out-Null
        Write-Host "Waiting for ChromaDB to become ready..."
        for ($i = 0; $i -lt 20; $i++) {
            Start-Sleep -Milliseconds 750
            try {
                $resp = Invoke-WebRequest -Uri ("http://localhost:{0}/api/v2/heartbeat" -f $chromaPort) -UseBasicParsing -TimeoutSec 2
                if ($resp.StatusCode -eq 200) { Write-Host "ChromaDB is ready." -ForegroundColor Green; break }
            } catch { }
        }
    } else {
        Write-Host "WARNING: Chroma server install failed; memory/RAG/tool-search will run degraded." -ForegroundColor Yellow
    }
}

# 7. Start the server (use `python -m uvicorn` - bare `uvicorn` may not be on PATH)
Write-Step ("Starting Odysseus at http://{0}:{1}" -f $BindHost, $Port)
Write-Host "Press Ctrl+C to stop."
Write-Host ""
& $venvPy -m uvicorn app:app --host $BindHost --port $Port
