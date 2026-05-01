"""Integration tests for /wisdom slash command via the daemon."""
import asyncio

import pytest

from agent_core.client import DaemonConnection as PalClient
from pal.config import Config

from tests.conftest import make_pal_agent, start_pal_daemon


@pytest.fixture()
async def wisdom_daemon(socket_path, mock_inference_server, tmp_path):
    cfg = Config(
        inference_url=mock_inference_server,
        model="test-model",
        socket_path=socket_path,
        history_depth=50,
        vault_path=tmp_path / "vault",
        collection_id="vault",
        username="testuser",
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
async def test_wisdom_list_empty(wisdom_daemon, socket_path):
    daemon, vault = wisdom_daemon
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("wisdom", "")
    assert "no wisdom" in resp.text.lower() or "empty" in resp.text.lower()

    await client.close()


@pytest.mark.asyncio
async def test_wisdom_add_and_list(wisdom_daemon, socket_path):
    daemon, vault = wisdom_daemon
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("wisdom", "add Be Concise | Lead with the answer.")
    assert "added" in resp.text.lower()
    assert "be-concise" in resp.text.lower() or "Be Concise" in resp.text

    resp = await client.command("wisdom", "")
    assert "Be Concise" in resp.text

    await client.close()


@pytest.mark.asyncio
async def test_wisdom_remove(wisdom_daemon, socket_path):
    daemon, vault = wisdom_daemon
    client = PalClient(socket_path)
    await client.connect()

    await client.command("wisdom", "add Temp Rule | This will be removed.")
    resp = await client.command("wisdom", "remove temp-rule")
    assert "removed" in resp.text.lower()

    resp = await client.command("wisdom", "")
    assert "Temp Rule" not in resp.text

    await client.close()


@pytest.mark.asyncio
async def test_wisdom_add_invalid_format(wisdom_daemon, socket_path):
    daemon, vault = wisdom_daemon
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="Usage"):
        await client.command("wisdom", "add no-separator-here")

    await client.close()


@pytest.mark.asyncio
async def test_wisdom_remove_nonexistent(wisdom_daemon, socket_path):
    daemon, vault = wisdom_daemon
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="not found"):
        await client.command("wisdom", "remove nonexistent")

    await client.close()
