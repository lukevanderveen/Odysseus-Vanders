"""Guards for the Windows desktop launcher (odysseus-desktop.ps1) and the
shortcut installer (create-desktop-shortcut.ps1).

There is no PowerShell test runner in this repo, so, like the *_js.py tests,
these assert on the script source directly and run a PowerShell parse when a
powershell.exe is available.
"""
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(ROOT, "odysseus-desktop.ps1")
SHORTCUT = os.path.join(ROOT, "create-desktop-shortcut.ps1")
ICON = os.path.join(ROOT, "docs", "odysseus.ico")
README = os.path.join(ROOT, "README.md")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8-sig") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def launcher() -> str:
    return _read(LAUNCHER)


@pytest.fixture(scope="module")
def shortcut() -> str:
    return _read(SHORTCUT)


# ── odysseus-desktop.ps1 ────────────────────────────────────────────────────

SERVER_START = "Start-Process -FilePath $VenvPy"
OPEN_UI_CALL = "$BrowserProc = Open-Ui"


def test_launcher_reuses_a_server_that_is_already_healthy(launcher):
    assert "/api/health" in launcher
    reuse_idx = launcher.index("if (Test-Healthy) {")
    start_idx = launcher.index(SERVER_START)
    assert reuse_idx < start_idx, "health probe must run before starting uvicorn"
    assert '"uvicorn"' in launcher


def test_launcher_opens_ui_in_chromium_app_mode(launcher):
    assert "--app=" in launcher


def test_launcher_falls_back_to_default_browser(launcher):
    assert "Start-Process $Url" in launcher or "Start-Process -FilePath $Url" in launcher


def test_launcher_uses_dedicated_browser_profile_under_data(launcher):
    assert "--user-data-dir=" in launcher
    assert "desktop-browser-profile" in launcher


def test_launcher_starts_uvicorn_hidden_with_log_files(launcher):
    assert "-WindowStyle Hidden" in launcher
    assert "odysseus-app.out.log" in launcher
    assert "odysseus-app.err.log" in launcher


def test_launcher_stops_the_server_it_started_when_window_closes(launcher):
    assert "Wait-Process" in launcher
    assert "Stop-Process" in launcher


def test_launcher_reports_errors_with_a_message_box(launcher):
    assert "MessageBox" in launcher


def test_launcher_points_at_launch_windows_when_venv_is_missing(launcher):
    assert "launch-windows.ps1" in launcher


def test_launcher_waits_for_health_before_opening_ui(launcher):
    start_idx = launcher.index(SERVER_START)
    ready_idx = launcher.index("if (-not $ready) {")
    open_idx = launcher.index(OPEN_UI_CALL)
    assert start_idx < ready_idx < open_idx


# ── create-desktop-shortcut.ps1 ─────────────────────────────────────────────

def test_shortcut_targets_launcher_with_bypass_and_hidden_window(shortcut):
    assert "odysseus-desktop.ps1" in shortcut
    assert "-ExecutionPolicy Bypass" in shortcut
    assert "-WindowStyle Hidden" in shortcut
    assert "WScript.Shell" in shortcut


def test_shortcut_uses_bundled_icon(shortcut):
    assert "odysseus.ico" in shortcut


def test_shortcut_resolves_desktop_via_environment_folder(shortcut):
    # Handles OneDrive-redirected desktops, unlike $env:USERPROFILE\Desktop.
    assert "GetFolderPath" in shortcut


# ── assets & docs ───────────────────────────────────────────────────────────

def test_bundled_icon_is_a_valid_ico_file():
    with open(ICON, "rb") as fh:
        header = fh.read(4)
    assert header == b"\x00\x00\x01\x00"


def test_readme_documents_desktop_shortcut():
    text = _read(README)
    assert "create-desktop-shortcut.ps1" in text


# ── PowerShell syntax ───────────────────────────────────────────────────────

@pytest.mark.skipif(shutil.which("powershell") is None, reason="powershell.exe not available")
@pytest.mark.parametrize("script", [LAUNCHER, SHORTCUT])
def test_powershell_scripts_parse_without_errors(script):
    probe = (
        "$errs = $null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile('"
        + script.replace("'", "''")
        + "', [ref]$null, [ref]$errs); "
        "if ($errs.Count) { $errs | ForEach-Object { Write-Output $_.Message }; exit 1 }"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", probe],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
