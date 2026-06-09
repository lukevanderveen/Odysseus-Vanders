"""Guards the front-end memory-import flow (no JS test runner — assert on source).

The import endpoint now returns a background job id for LLM extractions, so the
client must poll the status endpoint instead of expecting inline suggestions.
"""

from pathlib import Path

import pytest

MEMORY_JS = Path(__file__).resolve().parent.parent / "static" / "js" / "memory.js"


@pytest.fixture(scope="module")
def memory_js() -> str:
    return MEMORY_JS.read_text(encoding="utf-8")


def test_import_polls_status_endpoint(memory_js: str):
    assert "/api/memory/import/status/" in memory_js


def test_import_handles_job_id_response(memory_js: str):
    assert "data.job_id" in memory_js
    assert "pollImportJob" in memory_js


def test_poller_stops_on_done_and_error(memory_js: str):
    assert "'done'" in memory_js
    assert "'error'" in memory_js
