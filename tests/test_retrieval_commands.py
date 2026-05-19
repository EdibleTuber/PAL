"""Integration tests for retrieval-aware slash commands via the daemon."""
import asyncio

import pytest

from agent_core.client import DaemonConnection as PalClient
from pal.config import Config

from tests.conftest import make_pal_agent, start_pal_daemon


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
    )
    daemon = make_pal_agent(cfg)
    task = await start_pal_daemon(daemon)
    for _ in range(100):
        if socket_path.exists():
            break
        await asyncio.sleep(0.01)
    yield daemon
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_status_includes_collection(retrieval_daemon, socket_path):
    """/status now shows the collection id."""
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("status")
    assert "Collection:" in resp.text
    assert "vault" in resp.text

    await client.close()
