"""Tests for PAL wait_for_reindex tool (Phase F PR4)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from pal.tools.wait import WaitForReindex


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _Agent:
    def __init__(self, retrieval=None):
        self.retrieval = retrieval


def _ctx(agent, emit=None):
    class _C:
        pass
    c = _C()
    c.agent = agent
    c.emit = emit or AsyncMock()
    return c


# ---------------------------------------------------------------------------
# WaitForReindex
# ---------------------------------------------------------------------------

async def test_wait_for_reindex_done(tmp_path):
    retrieval = MagicMock()
    retrieval.get_reindex_job = AsyncMock(return_value={
        "job_id": "j-1",
        "status": "done",
        "indexed": 5,
    })
    agent = _Agent(retrieval=retrieval)
    result = await WaitForReindex().run(
        {"job_id": "j-1"},
        _ctx(agent),
    )
    parsed = json.loads(result)
    assert parsed["status"] == "done"
    assert parsed["job_id"] == "j-1"


async def test_wait_for_reindex_error_status(tmp_path):
    retrieval = MagicMock()
    retrieval.get_reindex_job = AsyncMock(return_value={
        "job_id": "j-2",
        "status": "error",
        "reason": "index failure",
    })
    agent = _Agent(retrieval=retrieval)
    result = await WaitForReindex().run(
        {"job_id": "j-2"},
        _ctx(agent),
    )
    parsed = json.loads(result)
    assert parsed["status"] == "error"


async def test_wait_for_reindex_missing_job_id(tmp_path):
    agent = _Agent(retrieval=MagicMock())
    result = await WaitForReindex().run({}, _ctx(agent))
    assert "Error" in result and "job_id" in result


async def test_wait_for_reindex_no_retrieval(tmp_path):
    agent = _Agent(retrieval=None)
    result = await WaitForReindex().run({"job_id": "j-1"}, _ctx(agent))
    assert "not configured" in result.lower()


async def test_wait_for_reindex_unknown_job(tmp_path):
    retrieval = MagicMock()
    retrieval.get_reindex_job = AsyncMock(return_value=None)
    agent = _Agent(retrieval=retrieval)
    result = await WaitForReindex().run({"job_id": "no-such-job"}, _ctx(agent))
    assert "unknown job_id" in result.lower()


async def test_wait_for_reindex_polls_multiple_times(tmp_path):
    """Polls until done; count confirms the loop ran 3 times."""
    retrieval = MagicMock()
    retrieval.get_reindex_job = AsyncMock(side_effect=[
        {"job_id": "j", "status": "running"},
        {"job_id": "j", "status": "running"},
        {"job_id": "j", "status": "done", "stats": {"new": 1}},
    ])
    agent = _Agent(retrieval=retrieval)
    result = await WaitForReindex().run(
        {"job_id": "j", "timeout_seconds": 5},
        _ctx(agent),
    )
    parsed = json.loads(result)
    assert parsed["status"] == "done"
    assert retrieval.get_reindex_job.await_count == 3


async def test_wait_for_reindex_timeout(tmp_path):
    """When deadline is exceeded before completion, returns timeout response."""
    import asyncio

    call_count = 0

    async def _slow_job(job_id):
        nonlocal call_count
        call_count += 1
        return {"job_id": job_id, "status": "running"}

    retrieval = MagicMock()
    retrieval.get_reindex_job = _slow_job
    agent = _Agent(retrieval=retrieval)

    # Use timeout_seconds=1 to force quick timeout; the poll loop
    # will return the timeout result.
    result = await WaitForReindex().run(
        {"job_id": "j-slow", "timeout_seconds": 1},
        _ctx(agent),
    )
    parsed = json.loads(result)
    assert parsed["status"] == "timeout"
    assert parsed["job_id"] == "j-slow"
    assert parsed["last_seen_status"] == "running"
    assert "_note" in parsed
