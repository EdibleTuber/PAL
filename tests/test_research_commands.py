"""Integration tests for /research command."""
import asyncio

import pytest

from agent_core.client import DaemonConnection as PalClient
from pal.config import Config

from tests.conftest import make_pal_agent, start_pal_daemon


@pytest.fixture(autouse=True)
def _disable_blocklist(monkeypatch):
    """Tests use 127.0.0.1 mock server -- disable blocklist."""
    monkeypatch.setattr("agent_core.utils.fetcher.check_url_safety", lambda url: None)


@pytest.fixture()
async def research_daemon(socket_path, mock_inference_server, tmp_path):
    """Daemon configured for research tests."""
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
    daemon = make_pal_agent(cfg)
    task = await start_pal_daemon(daemon)
    for _ in range(100):
        if socket_path.exists():
            break
        await asyncio.sleep(0.01)
    yield daemon, tmp_path / "vault"
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_research_single_topic(research_daemon, socket_path):
    """/research <topic> should fetch and summarize sources."""
    daemon, vault = research_daemon
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("research", "Python asyncio")
    assert "Research complete" in resp.text or "sources" in resp.text.lower()
    # Should have created files in raw/web/
    raw_web = vault / "raw" / "web"
    assert raw_web.exists()
    assert len(list(raw_web.glob("*.md"))) >= 1

    await client.close()


@pytest.mark.asyncio
async def test_research_from_file(research_daemon, socket_path):
    """/research <path> should read topics from file."""
    daemon, vault = research_daemon
    # Create a topic list file in the vault
    topics_file = vault / "research-queue.md"
    vault.mkdir(parents=True, exist_ok=True)
    topics_file.write_text("# Topics\n- Python asyncio\n- FAISS indexing\n")

    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("research", "research-queue.md")
    assert "Research complete" in resp.text or "topics" in resp.text.lower()

    await client.close()


@pytest.mark.asyncio
async def test_research_deep_flag(research_daemon, socket_path):
    """/research deep <topic> should accept the deep flag."""
    daemon, vault = research_daemon
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("research", "deep Python asyncio")
    assert "Research complete" in resp.text or "sources" in resp.text.lower()

    await client.close()


@pytest.mark.asyncio
async def test_research_empty_args(research_daemon, socket_path):
    daemon, vault = research_daemon
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="Usage"):
        await client.command("research", "")

    await client.close()


@pytest.mark.asyncio
async def test_research_help_includes_research(research_daemon, socket_path):
    """/help should list /research."""
    daemon, vault = research_daemon
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("help", "")
    assert "/research" in resp.text

    await client.close()
