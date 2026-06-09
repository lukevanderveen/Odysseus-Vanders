"""Tests for bash interpreter resolution on Windows.

Regression: on a Windows host where WSL is present, ``C:\\Windows\\System32\\
bash.exe`` (the WSL launcher) is first on PATH, so a naive ``shutil.which("bash")``
returns it. That launcher is NOT Git Bash — it cannot read our Windows-path
``.sh`` wrappers (it expects ``/mnt/c/...``) and is frequently installed with no
Linux distro, so it dies instantly. The result was every Cookbook download (and
every background bash job) "crashing" with an empty log. ``find_bash`` must
prefer real Git Bash and never hand back the System32 WSL stub.
"""

from core.platform_compat import _select_bash, _is_wsl_launcher

WSL = r"C:\Windows\System32\bash.exe"
GIT_BIN = r"C:\Program Files\Git\bin\bash.exe"
GIT_USR = r"C:\Program Files\Git\usr\bin\bash.exe"


def _which_returns(value):
    return lambda name: value if name == "bash" else None


def _exists_in(present):
    present_lower = {p.lower() for p in present}
    return lambda p: p.lower() in present_lower


def test_select_bash_prefers_git_over_system32_wsl_stub():
    """When PATH resolves to the WSL launcher but Git Bash is installed, pick
    Git Bash — the WSL stub can't run our Windows-path wrappers."""
    chosen = _select_bash(
        which=_which_returns(WSL),
        exists=_exists_in([GIT_BIN, GIT_USR]),
        is_windows=True,
        probe=lambda p: True,
    )
    assert chosen == GIT_BIN


def test_select_bash_rejects_wsl_stub_when_distro_missing():
    """If ONLY the System32 WSL bash exists and it isn't functional (no distro),
    return None so callers surface an 'install Git Bash' message instead of
    silently failing every bash launch."""
    chosen = _select_bash(
        which=_which_returns(WSL),
        exists=_exists_in([]),
        is_windows=True,
        probe=lambda p: False,
    )
    assert chosen is None


def test_select_bash_uses_functional_wsl_as_last_resort():
    """A working WSL bash (has a distro) is better than nothing when no Git
    Bash is present."""
    chosen = _select_bash(
        which=_which_returns(WSL),
        exists=_exists_in([]),
        is_windows=True,
        probe=lambda p: True,
    )
    assert chosen == WSL


def test_select_bash_posix_passes_through():
    """On POSIX, resolution is the plain PATH lookup — no Windows heuristics."""
    chosen = _select_bash(
        which=_which_returns("/usr/bin/bash"),
        exists=_exists_in([]),
        is_windows=False,
        probe=lambda p: True,
    )
    assert chosen == "/usr/bin/bash"


def test_is_wsl_launcher_detects_system32():
    assert _is_wsl_launcher(r"C:\Windows\System32\bash.exe") is True
    assert _is_wsl_launcher(r"c:/windows/system32/bash.exe") is True
    assert _is_wsl_launcher(r"C:\Program Files\Git\bin\bash.exe") is False
    assert _is_wsl_launcher(None) is False
