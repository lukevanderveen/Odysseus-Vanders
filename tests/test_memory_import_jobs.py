"""Background-job tracking for memory file imports.

Importing a .md/.pdf runs an LLM extraction that can exceed the server's hard
request timeout on slow local models. The import is therefore moved off the
request path into a tracked background job; these tests pin the job store's
contract (create -> running, complete/fail transitions, owner scoping, and
running a coroutine to populate the job).
"""

import pytest

from services.memory.import_jobs import ImportJobStore


def test_create_returns_running_job_with_id():
    store = ImportJobStore()
    job_id = store.create(owner="alice", filename="notes.md")
    assert job_id
    job = store.get(job_id, owner="alice")
    assert job["status"] == "running"
    assert job["filename"] == "notes.md"
    assert job["suggestions"] is None
    assert job["error"] is None


def test_complete_sets_suggestions_and_done():
    store = ImportJobStore()
    job_id = store.create(owner="alice", filename="notes.md")
    store.complete(job_id, [{"text": "a fact", "category": "fact"}])
    job = store.get(job_id, owner="alice")
    assert job["status"] == "done"
    assert job["suggestions"] == [{"text": "a fact", "category": "fact"}]


def test_fail_sets_error_status():
    store = ImportJobStore()
    job_id = store.create(owner="alice", filename="notes.md")
    store.fail(job_id, "LLM exploded")
    job = store.get(job_id, owner="alice")
    assert job["status"] == "error"
    assert "LLM exploded" in job["error"]


def test_get_is_owner_scoped():
    store = ImportJobStore()
    job_id = store.create(owner="alice", filename="notes.md")
    assert store.get(job_id, owner="bob") is None
    assert store.get(job_id, owner="alice") is not None


def test_get_with_no_owner_bypasses_scope():
    # Auth disabled => job owner is None and should still be retrievable.
    store = ImportJobStore()
    job_id = store.create(owner=None, filename="notes.md")
    assert store.get(job_id, owner=None) is not None


def test_get_unknown_job_returns_none():
    store = ImportJobStore()
    assert store.get("does-not-exist", owner="alice") is None


async def test_run_executes_extractor_and_stores_suggestions():
    store = ImportJobStore()

    async def fake_extract():
        return [{"text": "remembered", "category": "fact"}]

    job_id = store.create(owner="alice", filename="notes.md")
    await store.run(job_id, fake_extract())
    job = store.get(job_id, owner="alice")
    assert job["status"] == "done"
    assert job["suggestions"][0]["text"] == "remembered"


async def test_run_captures_extractor_error():
    store = ImportJobStore()

    async def boom():
        raise RuntimeError("nope")

    job_id = store.create(owner="alice", filename="x.md")
    await store.run(job_id, boom())
    job = store.get(job_id, owner="alice")
    assert job["status"] == "error"
    assert "nope" in job["error"]
