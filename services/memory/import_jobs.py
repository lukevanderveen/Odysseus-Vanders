"""In-memory tracking for asynchronous memory-import jobs.

Importing a .md/.pdf/.txt file runs an LLM extraction that can exceed the
server's hard request timeout on slow local models. The route therefore creates
a job here, returns its id immediately, and runs the extraction as a background
task that fills the job in. The frontend polls the job until it is done.

Jobs are owner-scoped and held in process memory (like the research handler's
active-task map); they do not need to survive a restart — an in-flight import
that is lost on restart is simply re-run by the user.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Awaitable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Jobs older than this (seconds) are pruned on the next create() to keep the
# map from growing without bound across a long-running process.
_MAX_AGE_SECONDS = 3600


class ImportJobStore:
    def __init__(self) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {}

    def create(self, owner: Optional[str], filename: str) -> str:
        self._prune()
        job_id = uuid.uuid4().hex
        self._jobs[job_id] = {
            "job_id": job_id,
            "owner": owner,
            "filename": filename,
            "status": "running",
            "suggestions": None,
            "error": None,
            "created": time.time(),
        }
        return job_id

    def complete(self, job_id: str, suggestions: List[Dict[str, Any]]) -> None:
        job = self._jobs.get(job_id)
        if not job:
            return
        job["suggestions"] = suggestions
        job["status"] = "done"

    def fail(self, job_id: str, error: Any) -> None:
        job = self._jobs.get(job_id)
        if not job:
            return
        job["error"] = str(error)
        job["status"] = "error"

    def get(self, job_id: str, owner: Optional[str] = None) -> Optional[Dict[str, Any]]:
        job = self._jobs.get(job_id)
        if not job:
            return None
        # Owner scoping mirrors the memory routes: when auth is enabled the
        # caller's owner must match; owner=None (auth disabled) sees all.
        if owner is not None and job.get("owner") != owner:
            return None
        return dict(job)

    async def run(self, job_id: str, coro: Awaitable[List[Dict[str, Any]]]) -> None:
        """Await an extraction coroutine and store its result on the job.

        Any exception is captured onto the job as an error rather than
        propagating, so a background task never crashes silently.
        """
        try:
            suggestions = await coro
            self.complete(job_id, suggestions)
        except Exception as e:  # noqa: BLE001 - record any failure on the job
            logger.error("Memory import job %s failed: %s", job_id, e)
            self.fail(job_id, e)

    def _prune(self) -> None:
        cutoff = time.time() - _MAX_AGE_SECONDS
        stale = [jid for jid, j in self._jobs.items() if j.get("created", 0) < cutoff]
        for jid in stale:
            self._jobs.pop(jid, None)
