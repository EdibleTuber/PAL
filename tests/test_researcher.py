"""Tests for Researcher -- search, fetch, summarize orchestration."""
import asyncio
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch, call

import pytest

from pal.researcher import Researcher, ResearchReport, ResearchResult, SourceResult
from pal.websearch import SearchResult
from pal.fetcher import FetchResult, FetchError


@dataclass
class MockInferenceResult:
    content: str
    reasoning: str = ""


@pytest.fixture
def mock_websearch():
    ws = AsyncMock()
    ws.search.return_value = [
        SearchResult(url="https://docs.python.org/asyncio", title="asyncio docs", snippet="Official docs"),
        SearchResult(url="https://realpython.com/asyncio", title="Real Python asyncio", snippet="Tutorial"),
        SearchResult(url="https://stackoverflow.com/asyncio", title="SO asyncio", snippet="Q&A"),
        SearchResult(url="https://extra.com/asyncio", title="Extra", snippet="Extra result"),
    ]
    return ws


@pytest.fixture
def mock_fetcher():
    f = AsyncMock()
    f.fetch.return_value = FetchResult(
        url="https://docs.python.org/asyncio",
        title="asyncio docs",
        text="# asyncio\n\nAsync I/O framework for Python.\n",
        content_hash="abcd1234" * 8,
        byte_size=1234,
    )
    return f


@pytest.fixture
def mock_inference():
    inference = AsyncMock()
    inference.complete.return_value = MockInferenceResult(
        content="Summary of asyncio documentation."
    )
    return inference


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    return v


@pytest.mark.asyncio
async def test_research_single_topic(mock_websearch, mock_fetcher, mock_inference, vault):
    """Basic single topic research gets 3 sources by default."""
    r = Researcher(mock_websearch, mock_fetcher, mock_inference, vault)
    report = await r.research_topic("asyncio")

    assert len(report.results) == 1
    result = report.results[0]
    assert result.topic == "asyncio"
    assert len(result.sources) == 3
    assert report.total_fetched == 3


@pytest.mark.asyncio
async def test_research_respects_depth(mock_websearch, mock_fetcher, mock_inference, vault):
    """depth=2 only fetches 2 sources."""
    r = Researcher(mock_websearch, mock_fetcher, mock_inference, vault)
    report = await r.research_topic("asyncio", depth=2)

    result = report.results[0]
    assert len(result.sources) == 2
    assert mock_fetcher.fetch.call_count == 2


@pytest.mark.asyncio
async def test_research_deduplicates_urls(mock_websearch, mock_fetcher, mock_inference, vault):
    """Same URL from refinement not fetched twice."""
    # Initial search returns 1 result, refinement returns overlapping URL + 1 new
    mock_websearch.search.side_effect = [
        [SearchResult(url="https://a.com/page", title="A", snippet="A")],
        [
            SearchResult(url="https://a.com/page", title="A", snippet="A"),
            SearchResult(url="https://b.com/page", title="B", snippet="B"),
        ],
        [SearchResult(url="https://c.com/page", title="C", snippet="C")],
        [SearchResult(url="https://a.com/page", title="A dup", snippet="A dup")],
    ]
    r = Researcher(mock_websearch, mock_fetcher, mock_inference, vault)
    report = await r.research_topic("test", depth=3)

    # a.com should only be fetched once even though it appeared twice
    fetched_urls = [c.args[0] for c in mock_fetcher.fetch.call_args_list]
    assert fetched_urls.count("https://a.com/page") == 1
    # Should have fetched a.com, b.com, c.com = 3 unique total, but only need depth=3
    assert mock_fetcher.fetch.call_count <= 3


@pytest.mark.asyncio
async def test_research_refines_query_on_thin_results(mock_websearch, mock_fetcher, mock_inference, vault):
    """Calls search multiple times if initial results < depth."""
    mock_websearch.search.side_effect = [
        # Initial search: only 1 result
        [SearchResult(url="https://a.com/page", title="A", snippet="A")],
        # "test tutorial": 1 result
        [SearchResult(url="https://b.com/page", title="B", snippet="B")],
        # "test documentation": 1 result
        [SearchResult(url="https://c.com/page", title="C", snippet="C")],
        # "test guide": 1 result
        [SearchResult(url="https://d.com/page", title="D", snippet="D")],
    ]
    r = Researcher(mock_websearch, mock_fetcher, mock_inference, vault)
    report = await r.research_topic("test", depth=3)

    sources = report.results[0].sources
    assert mock_websearch.search.call_count > 1
    assert len(sources) == 3


@pytest.mark.asyncio
async def test_research_flags_topic_with_no_results(mock_websearch, mock_fetcher, mock_inference, vault):
    """Empty results flag the topic."""
    mock_websearch.search.return_value = []
    r = Researcher(mock_websearch, mock_fetcher, mock_inference, vault)
    report = await r.research_topic("obscure nonexistent topic")

    result = report.results[0]
    assert result.flagged is True
    assert "obscure nonexistent topic" in report.flagged_topics


@pytest.mark.asyncio
async def test_research_handles_fetch_failure(mock_websearch, mock_fetcher, mock_inference, vault):
    """FetchError doesn't crash, reports failures."""
    mock_fetcher.fetch.side_effect = FetchError("connection refused")
    r = Researcher(mock_websearch, mock_fetcher, mock_inference, vault)
    report = await r.research_topic("asyncio", depth=2)

    result = report.results[0]
    assert len(result.sources) == 2
    assert all(s.status == "fetch_error" for s in result.sources)
    assert report.total_failed == 2
    assert report.total_fetched == 0


@pytest.mark.asyncio
async def test_research_batch_from_list(mock_websearch, mock_fetcher, mock_inference, vault):
    """Multiple topics, results for each."""
    r = Researcher(mock_websearch, mock_fetcher, mock_inference, vault)
    report = await r.research_topics(["asyncio", "dataclasses"], depth=2)

    assert len(report.results) == 2
    assert report.results[0].topic == "asyncio"
    assert report.results[1].topic == "dataclasses"


@pytest.mark.asyncio
async def test_research_progress_callback(mock_websearch, mock_fetcher, mock_inference, vault):
    """on_progress is called during research."""
    progress_messages = []

    def on_progress(msg):
        progress_messages.append(msg)

    r = Researcher(mock_websearch, mock_fetcher, mock_inference, vault, on_progress=on_progress)
    await r.research_topic("asyncio", depth=1)

    assert len(progress_messages) > 0


@pytest.mark.asyncio
async def test_research_saves_raw_files(mock_websearch, mock_fetcher, mock_inference, vault):
    """Files actually created in raw/web/."""
    r = Researcher(mock_websearch, mock_fetcher, mock_inference, vault)
    await r.research_topic("asyncio", depth=1)

    raw_dir = vault / "raw" / "web"
    assert raw_dir.exists()
    raw_files = list(raw_dir.glob("*.md"))
    assert len(raw_files) >= 1


@pytest.mark.asyncio
async def test_research_cross_topic_dedup(mock_websearch, mock_fetcher, mock_inference, vault):
    """URLs fetched in topic 1 not re-fetched in topic 2."""
    mock_websearch.search.return_value = [
        SearchResult(url="https://shared.com/page", title="Shared", snippet="Shared"),
        SearchResult(url="https://unique.com/page", title="Unique", snippet="Unique"),
    ]
    r = Researcher(mock_websearch, mock_fetcher, mock_inference, vault)
    report = await r.research_topics(["topic1", "topic2"], depth=2)

    fetched_urls = [c.args[0] for c in mock_fetcher.fetch.call_args_list]
    assert fetched_urls.count("https://shared.com/page") == 1
    assert fetched_urls.count("https://unique.com/page") == 1
