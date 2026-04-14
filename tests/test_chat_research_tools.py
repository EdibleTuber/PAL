from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pal.approval_registry import ApprovalRegistry
from pal.tools import ToolExecutor
from pal.websearch import SearchResult


def test_tool_executor_accepts_new_dependencies(tmp_path: Path):
    registry = ApprovalRegistry()
    websearch = MagicMock()
    researcher = MagicMock()
    proposal_emitter = MagicMock()

    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        wiki=None,
        approval_registry=registry,
        websearch=websearch,
        researcher=researcher,
        proposal_emitter=proposal_emitter,
    )

    assert executor.approval_registry is registry
    assert executor.websearch is websearch
    assert executor.researcher is researcher
    assert executor.proposal_emitter is proposal_emitter


@pytest.mark.asyncio
async def test_search_web_formats_results(tmp_path):
    websearch = MagicMock()
    websearch.search = MagicMock(return_value=_async_result([
        SearchResult(url="https://a.example/1", title="Title 1", snippet="Snippet 1"),
        SearchResult(url="https://b.example/2", title="Title 2", snippet="Snippet 2"),
    ]))
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        websearch=websearch,
    )
    output = await executor.run_async("search_web", {"query": "prompt injection"})
    assert "Title 1" in output
    assert "https://a.example/1" in output
    assert "Snippet 1" in output
    assert "Title 2" in output
    websearch.search.assert_called_once_with("prompt injection")


@pytest.mark.asyncio
async def test_search_web_requires_query(tmp_path):
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        websearch=MagicMock(),
    )
    output = await executor.run_async("search_web", {})
    assert "Error" in output and "query" in output


@pytest.mark.asyncio
async def test_search_web_caps_max_results(tmp_path):
    websearch = MagicMock()
    results = [
        SearchResult(url=f"https://x.example/{i}", title=f"T{i}", snippet=f"S{i}")
        for i in range(20)
    ]
    websearch.search = MagicMock(return_value=_async_result(results))
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        websearch=websearch,
    )
    output = await executor.run_async(
        "search_web", {"query": "q", "max_results": 50}
    )
    # Cap is 10 regardless of requested value
    assert output.count("https://x.example/") == 10


@pytest.mark.asyncio
async def test_search_web_unavailable_without_client(tmp_path):
    executor = ToolExecutor(vault_path=tmp_path, retrieval=None, websearch=None)
    output = await executor.run_async("search_web", {"query": "q"})
    assert "not available" in output.lower()


async def _async_result(value):
    return value


import asyncio


@pytest.mark.asyncio
async def test_propose_research_emits_message_and_waits_for_approval(tmp_path):
    registry = ApprovalRegistry()
    emitted = []
    def emitter(msg):
        emitted.append(msg)
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        proposal_emitter=emitter,
    )

    async def approve_later():
        # Wait for the proposal to be created, then approve it.
        for _ in range(50):
            if emitted:
                break
            await asyncio.sleep(0.01)
        assert emitted, "proposal was not emitted"
        registry.approve(emitted[0].proposal_id)

    approval_task = asyncio.create_task(approve_later())
    output = await executor.run_async(
        "propose_research",
        {"topic": "prompt injection", "depth": 3, "rationale": "user asked"},
    )
    await approval_task

    assert emitted[0].topic == "prompt injection"
    assert emitted[0].depth == 3
    assert emitted[0].rationale == "user asked"
    assert '"status": "approved"' in output
    assert '"proposal_id"' in output


@pytest.mark.asyncio
async def test_propose_research_returns_declined(tmp_path):
    registry = ApprovalRegistry()
    emitted = []
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        proposal_emitter=emitted.append,
    )

    async def decline_later():
        for _ in range(50):
            if emitted:
                break
            await asyncio.sleep(0.01)
        registry.decline(emitted[0].proposal_id)

    asyncio.create_task(decline_later())
    output = await executor.run_async(
        "propose_research",
        {"topic": "t", "depth": 3, "rationale": "r"},
    )
    assert '"status": "declined"' in output


@pytest.mark.asyncio
async def test_propose_research_returns_edited_with_new_id(tmp_path):
    registry = ApprovalRegistry()
    emitted = []
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        proposal_emitter=emitted.append,
    )

    async def edit_later():
        for _ in range(50):
            if emitted:
                break
            await asyncio.sleep(0.01)
        registry.edit(
            emitted[0].proposal_id, new_topic="refined", new_depth=5
        )

    asyncio.create_task(edit_later())
    output = await executor.run_async(
        "propose_research",
        {"topic": "t", "depth": 3, "rationale": "r"},
    )
    assert '"status": "approved"' in output  # edited -> new proposal approved
    assert '"topic": "refined"' in output
    assert '"depth": 5' in output


@pytest.mark.asyncio
async def test_propose_research_requires_registry(tmp_path):
    executor = ToolExecutor(vault_path=tmp_path, retrieval=None)
    output = await executor.run_async(
        "propose_research",
        {"topic": "t", "depth": 3, "rationale": "r"},
    )
    assert "not available" in output.lower()
