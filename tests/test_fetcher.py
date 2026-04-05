"""Tests for URLFetcher — fetch + extract + validate."""
import pytest

from pal.fetcher import URLFetcher, FetchResult, FetchError


@pytest.mark.asyncio
async def test_fetch_extracts_main_content(mock_inference_server):
    fetcher = URLFetcher(max_bytes=2_000_000, timeout=10)
    result = await fetcher.fetch(f"{mock_inference_server}/page.html")
    assert isinstance(result, FetchResult)
    assert "main content" in result.text.lower() or "extract me" in result.text.lower()
    assert "nav junk" not in result.text.lower()
    assert result.url == f"{mock_inference_server}/page.html"
    assert result.title == "Test Page"


@pytest.mark.asyncio
async def test_fetch_rejects_too_large(mock_inference_server):
    fetcher = URLFetcher(max_bytes=2_000_000, timeout=10)
    with pytest.raises(FetchError, match="too large"):
        await fetcher.fetch(f"{mock_inference_server}/too-large")


@pytest.mark.asyncio
async def test_fetch_rejects_binary(mock_inference_server):
    fetcher = URLFetcher(max_bytes=2_000_000, timeout=10)
    with pytest.raises(FetchError, match="content type"):
        await fetcher.fetch(f"{mock_inference_server}/binary")


@pytest.mark.asyncio
async def test_fetch_404_raises(mock_inference_server):
    fetcher = URLFetcher(max_bytes=2_000_000, timeout=10)
    with pytest.raises(FetchError, match="404"):
        await fetcher.fetch(f"{mock_inference_server}/missing")


@pytest.mark.asyncio
async def test_fetch_result_has_hash(mock_inference_server):
    fetcher = URLFetcher(max_bytes=2_000_000, timeout=10)
    result = await fetcher.fetch(f"{mock_inference_server}/page.html")
    assert result.content_hash
    assert len(result.content_hash) == 64  # sha256 hex


@pytest.mark.asyncio
async def test_fetch_respects_max_bytes_during_download(mock_inference_server):
    """If response streams more than max_bytes, fetch should abort."""
    fetcher = URLFetcher(max_bytes=1, timeout=10)
    with pytest.raises(FetchError, match="too large"):
        await fetcher.fetch(f"{mock_inference_server}/page.html")
