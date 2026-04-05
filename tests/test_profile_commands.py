"""Integration tests for /profile slash command via the daemon."""
import asyncio

import pytest

from pal.client import PalClient
from pal.config import Config
from pal.daemon import Daemon


@pytest.fixture()
async def profile_daemon(socket_path, mock_inference_server, tmp_path):
    cfg = Config(
        inference_url=mock_inference_server,
        model="test-model",
        socket_path=socket_path,
        history_depth=50,
        vault_path=tmp_path / "vault",
        collection_id="vault",
        username="testuser",
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
async def test_profile_show_empty(profile_daemon, socket_path):
    """/profile with no args shows the current profile (empty by default)."""
    daemon, vault = profile_daemon
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("profile", "")
    assert "empty" in resp.text.lower() or "no profile" in resp.text.lower()

    await client.close()


@pytest.mark.asyncio
async def test_profile_set_and_show(profile_daemon, socket_path):
    """/profile set <text> writes the profile, then /profile shows it."""
    daemon, vault = profile_daemon
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("profile", "set ## Bio\n\nSoftware engineer.")
    assert "updated" in resp.text.lower() or "saved" in resp.text.lower()

    resp = await client.command("profile", "")
    assert "Software engineer." in resp.text

    await client.close()


@pytest.mark.asyncio
async def test_profile_persists_on_disk(profile_daemon, socket_path):
    """Profile writes reach the vault filesystem."""
    daemon, vault = profile_daemon
    client = PalClient(socket_path)
    await client.connect()

    await client.command("profile", "set ## World\n\nLinux user.")

    profile_path = vault / "_profile" / "testuser.md"
    assert profile_path.exists()
    content = profile_path.read_text()
    assert "Linux user." in content

    await client.close()
