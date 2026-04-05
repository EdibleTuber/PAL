"""Integration tests for /search-web and /fetch commands."""
import asyncio

import pytest

from pal.client import PalClient
from pal.config import Config
from pal.daemon import Daemon


@pytest.fixture()
async def web_daemon(socket_path, mock_inference_server, tmp_path):
    """Daemon using mock_inference_server as SearxNG endpoint too."""
    cfg = Config(
        inference_url=mock_inference_server,
        model="test-model",
        socket_path=socket_path,
        history_depth=50,
        vault_path=tmp_path / "vault",
        collection_id="vault",
        username="testuser",
        searxng_url=mock_inference_server,
        fetch_max_bytes=2_000_000,
        fetch_timeout=10,
    )
    daemon = Daemon(cfg)
    task = asyncio.create_task(daemon.serve())
    for _ in range(100):
        if socket_path.exists():
            break
        await asyncio.sleep(0.01)
    yield daemon, tmp_path / "vault"
    daemon.shutdown()
    await task


@pytest.mark.asyncio
async def test_search_web_returns_allowed_results(web_daemon, socket_path):
    """/search-web returns only results from allowlisted domains."""
    daemon, vault = web_daemon
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("search-web", "python")
    # Mock returns wikipedia, arxiv, and evil.example.com
    # Only wikipedia and arxiv should appear (allowlist filters evil.example.com)
    assert "wikipedia.org" in resp.text
    assert "arxiv.org" in resp.text
    assert "evil.example.com" not in resp.text

    await client.close()


@pytest.mark.asyncio
async def test_search_web_empty_query(web_daemon, socket_path):
    daemon, vault = web_daemon
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="Usage"):
        await client.command("search-web", "")

    await client.close()


@pytest.mark.asyncio
async def test_search_web_seeds_allowlist_on_first_use(web_daemon, socket_path):
    daemon, vault = web_daemon
    # Seeding happens in Daemon __init__, so it should already exist
    assert (vault / "_config" / "allowlist.md").exists()

    client = PalClient(socket_path)
    await client.connect()
    await client.command("search-web", "test")
    await client.close()

    assert (vault / "_config" / "allowlist.md").exists()
