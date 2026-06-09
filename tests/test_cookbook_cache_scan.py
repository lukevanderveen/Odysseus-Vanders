"""The cached-model scanner must honor HF_HOME / HF_HUB_CACHE.

Regression: downloads respect HF_HOME (we moved the cache to D: to spare a full
C:), but the scanner was hardcoded to ``~/.cache/huggingface/hub``. Result: a
model downloaded to ``$HF_HOME/hub`` never appeared in Odysseus' model list.
"""

import json
import os
import subprocess
import sys

from routes.cookbook_helpers import _cached_model_scan_script


def _run_scan(env_overrides, tmp_path):
    env = dict(os.environ)
    # Neutralize any inherited HF cache vars so the test controls them fully.
    for k in ("HF_HOME", "HF_HUB_CACHE", "XDG_CACHE_HOME"):
        env.pop(k, None)
    env.update(env_overrides)
    script = _cached_model_scan_script()
    out = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return {m["repo_id"] for m in json.loads(out.stdout)}


def _make_fake_model(hub_dir, repo_dir_name):
    snap = hub_dir / repo_dir_name / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    (snap / "demo-Q4_K_M.gguf").write_bytes(b"\x00" * 16)


def test_scan_finds_model_under_hf_home(tmp_path):
    """A model in ``$HF_HOME/hub`` is discovered when HF_HOME is set."""
    hf_home = tmp_path / "hfhome"
    _make_fake_model(hf_home / "hub", "models--acme--demo-GGUF")
    repos = _run_scan({"HF_HOME": str(hf_home)}, tmp_path)
    assert "acme/demo-GGUF" in repos


def test_scan_finds_model_under_hf_hub_cache(tmp_path):
    """HF_HUB_CACHE points directly at the hub dir and takes precedence."""
    hub = tmp_path / "directhub"
    _make_fake_model(hub, "models--acme--other-GGUF")
    repos = _run_scan({"HF_HUB_CACHE": str(hub)}, tmp_path)
    assert "acme/other-GGUF" in repos
