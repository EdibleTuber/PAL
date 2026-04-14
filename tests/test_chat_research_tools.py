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
