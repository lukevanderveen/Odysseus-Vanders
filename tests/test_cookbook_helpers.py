import json
import subprocess
import sys

import pytest
from fastapi import HTTPException

from routes.cookbook_helpers import (
    _cached_model_scan_script,
    _append_serve_exit_code_lines,
    _append_serve_preflight_exit_lines,
    _force_utf8_io_bash_lines,
    _find_live_download_session,
    _python_download_script,
    _local_tooling_path_export,
    _ollama_model_name,
    _pip_install_fallback_chain,
    _ollama_bind_from_cmd,
    _safe_env_prefix,
    _validate_gpus,
    _validate_repo_id,
    _validate_serve_cmd,
    _validate_serve_model_id,
    _validate_ssh_port,
)


def test_safe_env_prefix_accepts_quoted_venv_path():
    assert (
        _safe_env_prefix("source '~/vllm-env/bin/activate'")
        == '[ -f "$HOME/vllm-env/bin/activate" ] && source "$HOME/vllm-env/bin/activate" || true'
    )


def test_safe_env_prefix_leaves_compound_conda_prefix_unchanged():
    prefix = 'eval "$(conda shell.bash hook)" && conda activate qwen35'
    assert _safe_env_prefix(prefix) == prefix


def test_safe_env_prefix_rejects_freeform_shell():
    with pytest.raises(HTTPException):
        _safe_env_prefix("echo ok; curl https://example.invalid")


def test_safe_env_prefix_accepts_powershell_activation_path():
    assert (
        _safe_env_prefix("& 'C:\\Users\\me\\venv\\Scripts\\Activate.ps1'")
        == "& 'C:\\Users\\me\\venv\\Scripts\\Activate.ps1'"
    )


def test_validate_ssh_port_rejects_shell_payload():
    with pytest.raises(HTTPException):
        _validate_ssh_port("22; touch /tmp/pwned")
    assert _validate_ssh_port("2222") == "2222"


def test_validate_gpus_accepts_indexes_only():
    assert _validate_gpus("0,1,2") == "0,1,2"
    with pytest.raises(HTTPException):
        _validate_gpus("0; rm -rf /")


def test_validate_repo_id_stays_strict_for_hf_downloads():
    assert _validate_repo_id("Qwen/Qwen3-8B") == "Qwen/Qwen3-8B"
    with pytest.raises(HTTPException):
        _validate_repo_id("DeepSeek-R1-UD-IQ4_XS")


def test_validate_serve_model_id_accepts_cached_local_model_names():
    assert _validate_serve_model_id("Qwen/Qwen3-8B") == "Qwen/Qwen3-8B"
    assert _validate_serve_model_id("DeepSeek-R1-UD-IQ4_XS") == "DeepSeek-R1-UD-IQ4_XS"
    with pytest.raises(HTTPException):
        _validate_serve_model_id("../escape")


def test_local_tooling_path_export_prepends_interpreter_bin():
    """The cookbook runners must see the venv's bin (where `hf`/`python` live)
    so tmux shells can find them without an activated venv."""
    assert (
        _local_tooling_path_export("/opt/venv/bin/python")
        == 'export PATH="/opt/venv/bin:$PATH"'
    )


def test_local_tooling_path_export_preserves_spaces_and_expands_path():
    line = _local_tooling_path_export("/Users/John Smith/.venv/bin/python3")
    assert line == 'export PATH="/Users/John Smith/.venv/bin:$PATH"'
    assert line.endswith(':$PATH"')  # $PATH stays expandable in double quotes


def test_local_tooling_path_export_converts_windows_path_to_bash_form():
    """The runner is a bash script even on native Windows, so a Windows venv
    path (D:\\...\\Scripts) must be emitted as a Git-Bash POSIX path
    (/d/.../Scripts). A backslash path is unusable as a bash PATH entry, so
    `hf` is never found and downloads die with "command not found"."""
    line = _local_tooling_path_export(r"D:\odysseus\venv\Scripts\python.exe")
    assert line == 'export PATH="/d/odysseus/venv/Scripts:$PATH"'


def test_python_download_script_emits_parseable_byte_progress():
    """The detached download must show real byte progress. hf's CLI prints no
    progress in a non-TTY log (or only a file-count bar stuck at 0%), so we drive
    snapshot_download from Python with a tqdm subclass that prints 'Downloading
    N%' lines the UI bar parses. Xet is disabled so the classic tqdm path runs."""
    src = _python_download_script("org/Model-GGUF", "*Q4_K_M*", None)
    assert "snapshot_download" in src
    assert "org/Model-GGUF" in src
    assert "*Q4_K_M*" in src
    assert "HF_HUB_DISABLE_XET" in src
    assert "Downloading" in src  # the parseable progress prefix
    # Must be syntactically valid Python.
    compile(src, "<dl>", "exec")


def test_find_live_download_session_dedupes_same_repo(tmp_path):
    """A second download of a repo already being fetched must be deduped — two
    concurrent `hf download` of one repo deadlock on the HF cache lock and both
    stall at 0%. Returns the live session id for the same repo, else None."""
    # session A: downloading repo X, process alive
    (tmp_path / "cookbook-aaaa.repo").write_text("org/Model-GGUF", encoding="utf-8")
    (tmp_path / "cookbook-aaaa.pid").write_text("111", encoding="utf-8")
    # session B: downloading repo X but its process is dead (stale)
    (tmp_path / "cookbook-bbbb.repo").write_text("org/Model-GGUF", encoding="utf-8")
    (tmp_path / "cookbook-bbbb.pid").write_text("222", encoding="utf-8")
    alive = lambda pid: pid == 111  # only A is alive

    assert _find_live_download_session("org/Model-GGUF", tmp_path, alive) == "cookbook-aaaa"
    # A different repo has no live session.
    assert _find_live_download_session("org/Other-GGUF", tmp_path, alive) is None


def test_find_live_download_session_ignores_dead_sessions(tmp_path):
    """If the only same-repo session is dead, don't dedupe — let a fresh download
    proceed (the stale one isn't holding anything live)."""
    (tmp_path / "cookbook-dead.repo").write_text("org/Model-GGUF", encoding="utf-8")
    (tmp_path / "cookbook-dead.pid").write_text("999", encoding="utf-8")
    assert _find_live_download_session("org/Model-GGUF", tmp_path, lambda pid: False) is None


def test_ollama_model_name_strips_gguf_suffix_and_lowercases():
    """Cookbook auto-registers a downloaded GGUF into Ollama. The Ollama model
    name is derived from the repo: drop the org, strip the '-GGUF' suffix, and
    lowercase (Ollama names are lowercase)."""
    assert _ollama_model_name("bartowski/Qwen2.5-Math-7B-Instruct-GGUF") == "qwen2.5-math-7b-instruct"
    assert _ollama_model_name("unsloth/LFM2-8B-A1B-GGUF") == "lfm2-8b-a1b"
    assert _ollama_model_name("unsloth/gpt-oss-20b-GGUF") == "gpt-oss-20b"
    # No org, no GGUF suffix: still lowercased, unchanged otherwise.
    assert _ollama_model_name("My-Model") == "my-model"


def test_force_utf8_io_bash_lines_force_utf8_streams():
    """`hf download` finishes the transfer then prints a Unicode '✓'. On Windows
    a detached process has no console, so Python's stdout falls back to cp1252
    and crashes with a 'charmap' UnicodeEncodeError — making a *successful*
    download report DOWNLOAD_FAILED. Forcing UTF-8 IO prevents that."""
    lines = _force_utf8_io_bash_lines()
    assert "export PYTHONUTF8=1" in lines
    assert "export PYTHONIOENCODING=utf-8" in lines


def test_pip_install_fallback_chain_prefers_venv_safe_install():
    chain = _pip_install_fallback_chain("huggingface_hub", upgrade=True)
    assert chain.startswith("python3 -m pip install -q -U huggingface_hub")
    assert "|| python3 -m pip install --user --break-system-packages -q -U huggingface_hub" in chain


def test_pip_install_fallback_chain_allows_custom_python_command():
    chain = _pip_install_fallback_chain("hf_transfer", python_cmd="pip", upgrade=False)
    assert chain == (
        'pip install -q hf_transfer 2>/dev/null || { '
        'python -c "import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)"'
        ' || pip install --user --break-system-packages -q hf_transfer 2>/dev/null; }'
    )


def test_serve_preflight_failure_keeps_tmux_pane_visible():
    """Dependency preflight failures should remain visible in tmux output.

    A bare `exit 127` kills the tmux pane before the browser/status poller can
    capture the helpful error, leaving users with a blank "crashed" card.
    """
    runner_lines = [
        'ODYSSEUS_PREFLIGHT_EXIT=""',
        'echo "ERROR: vLLM is not installed. Open Cookbook -> Dependencies and install vllm on this server, then launch again."',
        'ODYSSEUS_PREFLIGHT_EXIT=127',
    ]
    _append_serve_preflight_exit_lines(runner_lines, keep_shell_open=True)
    script = "\n".join(runner_lines)

    assert "ERROR: vLLM is not installed" in script
    assert 'ODYSSEUS_PREFLIGHT_EXIT=127' in script
    assert 'echo "=== Process exited with code $ODYSSEUS_PREFLIGHT_EXIT ==="' in script
    assert 'exec "${SHELL:-/bin/bash}"' in script
    assert "exit 127" not in script


def test_serve_runner_preserves_command_exit_code():
    """The serve wrapper must capture `$?` before any echo resets it."""
    runner_lines = ["vllm serve Qwen/Qwen3.6-35B-A3B-NVFP4 --host 0.0.0.0 --port 8000"]
    _append_serve_exit_code_lines(runner_lines, keep_shell_open=True)
    script = "\n".join(runner_lines)

    assert "ODYSSEUS_CMD_EXIT=$?" in script
    assert 'echo "=== Process exited with code $ODYSSEUS_CMD_EXIT ==="' in script
    assert 'echo "=== Process exited with code $? ==="' not in script


def test_validate_serve_cmd_accepts_llama_advanced_controls():
    cmd = (
        "MODEL_FILE=$(printf %s ${HOME}'/.cache/huggingface/hub/models--Qwen--Qwen3-GGUF/snapshots/model.gguf') "
        '&& { [ -n "$MODEL_FILE" ] && [ -f "$MODEL_FILE" ]; } '
        '|| { echo "ERROR: No GGUF found on this host."; exit 1; } && '
        'GGML_CUDA_ENABLE_UNIFIED_MEMORY=1 CUDA_VISIBLE_DEVICES=0,1 llama-server '
        '--model "$MODEL_FILE" --host 0.0.0.0 --port 8000 -ngl 99 -c 131072 '
        '--n-cpu-moe 0 --cache-type-k q8_0 --cache-type-v q8_0 --flash-attn on '
        '--fit off --split-mode tensor --tensor-split 50,50 --main-gpu 0 '
        '--parallel 1 --batch-size 2048 --ubatch-size 512 --no-mmap --no-warmup '
        '--spec-type draft-mtp --spec-draft-n-max 3 '
        '|| python3 -m llama_cpp.server --model "$MODEL_FILE" --host 0.0.0.0 --port 8000'
    )

    assert _validate_serve_cmd(cmd) == cmd


def test_ollama_serve_defaults_to_loopback_bind():
    assert _ollama_bind_from_cmd("ollama serve") == ("127.0.0.1", "11434")
    assert _ollama_bind_from_cmd("ollama run qwen2.5:0.5b") == ("127.0.0.1", "11434")


def test_ollama_serve_accepts_remote_reachable_default_bind():
    assert (
        _ollama_bind_from_cmd("ollama serve", default_host="0.0.0.0")
        == ("0.0.0.0", "11434")
    )


def test_ollama_serve_preserves_explicit_bind_opt_in():
    assert (
        _ollama_bind_from_cmd("OLLAMA_HOST=0.0.0.0:12345 ollama serve")
        == ("0.0.0.0", "12345")
    )
    assert (
        _ollama_bind_from_cmd("OLLAMA_HOST=[::1]:11435 ollama serve")
        == ("[::1]", "11435")
    )


def test_ollama_serve_rejects_unsafe_bind_values():
    assert (
        _ollama_bind_from_cmd("OLLAMA_HOST='$HOST:11434' ollama serve")
        == ("127.0.0.1", "11434")
    )
    assert (
        _ollama_bind_from_cmd("OLLAMA_HOST=127.0.0.1:99999 ollama serve")
        == ("127.0.0.1", "11434")
    )


def test_cached_model_scan_reports_plain_dir_gguf(tmp_path):
    """Custom download dirs may sit inside the HF hub cache and contain plain
    per-model folders. They must show up in Serve and keep the GGUF signal."""
    plain = tmp_path / "Qwen3.6-27B"
    plain.mkdir()
    (plain / "Qwen3.6-27B-Q4_K_M.gguf").write_bytes(b"gguf")
    (plain / "Qwen3.6-27B-Q5_K_M-00001-of-00003.gguf").write_bytes(b"part1")
    (plain / "Qwen3.6-27B-Q5_K_M-00002-of-00003.gguf").write_bytes(b"part2")
    (plain / "Qwen3.6-27B-Q5_K_M-00003-of-00003.gguf").write_bytes(b"part3")
    (plain / "Qwen3.6-27B-Q6_K_XL.gguf").write_bytes(b"ggufgguf")
    (plain / "mmproj-BF16.gguf").write_bytes(b"projector")

    hf_internal = tmp_path / "models--Qwen--Qwen3.6-27B"
    (hf_internal / "snapshots" / "abc").mkdir(parents=True)
    (hf_internal / "snapshots" / "abc" / "model.safetensors").write_bytes(b"safe")

    scan_py = tmp_path / "scan_cache.py"
    scan_py.write_text(_cached_model_scan_script([str(tmp_path)]), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(scan_py)],
        check=True,
        capture_output=True,
        text=True,
    )

    by_repo = {m["repo_id"]: m for m in json.loads(proc.stdout)}
    assert "models--Qwen--Qwen3.6-27B" not in by_repo
    assert by_repo["Qwen3.6-27B"]["is_local_dir"] is True
    assert by_repo["Qwen3.6-27B"]["is_gguf"] is True
    ggufs = by_repo["Qwen3.6-27B"]["gguf_files"]
    assert [f["rel_path"] for f in ggufs] == [
        "Qwen3.6-27B-Q4_K_M.gguf",
        "Qwen3.6-27B-Q5_K_M-00001-of-00003.gguf",
        "Qwen3.6-27B-Q6_K_XL.gguf",
        "mmproj-BF16.gguf",
    ]
    assert [f["role"] for f in ggufs] == ["model", "model", "model", "projector"]
    assert ggufs[0]["quant"] == "Q4_K_M"
    assert ggufs[1]["quant"] == "Q5_K_M"
    assert ggufs[1]["split"] is True
    assert ggufs[1]["parts"] == 3
    assert ggufs[1]["size_bytes"] == len(b"part1part2part3")
    assert ggufs[2]["quant"] == "Q6_K_XL"
    assert ggufs[3]["quant"] == "BF16"
