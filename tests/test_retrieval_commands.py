"""Integration tests for /search and /get slash commands via the daemon."""
import asyncio

import pytest

from pal.client import PalClient
from pal.config import Config
from pal.daemon import Daemon


@pytest.fixture()
async def retrieval_daemon(socket_path, mock_inference_server, tmp_path):
    """Start a daemon with a mock inference server (which also serves collection endpoints)."""
    cfg = Config(
        inference_url=mock_inference_server,
        model="test-model",
        socket_path=socket_path,
        history_depth=50,
        vault_path=tmp_path / "vault",
        collection_id="vault",
        channels_dir=tmp_path / "channels",
    )
    daemon = Daemon(cfg)
    task = asyncio.create_task(daemon.serve())
    for _ in range(100):
        if socket_path.exists():
            break
        await asyncio.sleep(0.01)
    yield daemon
    daemon.shutdown()
    await task


@pytest.mark.asyncio
async def test_search_command_returns_results(retrieval_daemon, socket_path):
    """/search query returns ranked results."""
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("search", "quantum computing")
    assert "doc-0" in resp.text
    assert "Summary for quantum computing" in resp.text

    await client.close()


@pytest.mark.asyncio
async def test_search_command_empty_query(retrieval_daemon, socket_path):
    """/search with no args returns usage error."""
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="Usage"):
        await client.command("search", "")

    await client.close()


@pytest.mark.asyncio
async def test_get_command_returns_document(retrieval_daemon, socket_path):
    """/get <doc_id> returns full document content."""
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("get", "Projects/alpha.md")
    assert "Full content" in resp.text
    assert "Projects/alpha.md" in resp.text

    await client.close()


@pytest.mark.asyncio
async def test_get_command_document_not_found(retrieval_daemon, socket_path):
    """/get with a missing doc_id returns an error."""
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="not found"):
        await client.command("get", "missing")

    await client.close()


@pytest.mark.asyncio
async def test_status_includes_collection(retrieval_daemon, socket_path):
    """/status now shows the collection id."""
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("status")
    assert "Collection:" in resp.text
    assert "vault" in resp.text

    await client.close()
