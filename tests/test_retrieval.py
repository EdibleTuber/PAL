"""Tests for the retrieval client — collection search and doc fetch."""
import pytest

from pal.retrieval import RetrievalClient


@pytest.mark.asyncio
async def test_search_returns_results(mock_inference_server):
    client = RetrievalClient(base_url=mock_inference_server, collection_id="vault")
    results = await client.search("quantum computing")
    assert len(results) == 3
    assert results[0]["id"] == "doc-0"
    assert "quantum computing" in results[0]["summary"]
    assert results[0]["score"] > results[1]["score"]


@pytest.mark.asyncio
async def test_search_respects_limit(mock_inference_server):
    client = RetrievalClient(base_url=mock_inference_server, collection_id="vault")
    results = await client.search("anything", limit=1)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_get_document(mock_inference_server):
    client = RetrievalClient(base_url=mock_inference_server, collection_id="vault")
    doc = await client.get_document("Projects/alpha.md")
    assert doc["id"] == "Projects/alpha.md"
    assert "Full content" in doc["content"]


@pytest.mark.asyncio
async def test_get_document_not_found(mock_inference_server):
    client = RetrievalClient(base_url=mock_inference_server, collection_id="vault")
    with pytest.raises(FileNotFoundError):
        await client.get_document("missing")


@pytest.mark.asyncio
async def test_get_document_rejects_path_traversal(mock_inference_server):
    client = RetrievalClient(base_url=mock_inference_server, collection_id="vault")
    with pytest.raises(ValueError, match="Invalid doc_id"):
        await client.get_document("../../etc/passwd")
    with pytest.raises(ValueError, match="Invalid doc_id"):
        await client.get_document("/absolute/path")
