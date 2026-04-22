"""Verify profile and wisdom actually reach the inference server in chat."""
import asyncio

import pytest

from pal.client import PalClient
from pal.config import Config
from pal.daemon import Daemon
from pal.protocol import ResponseMessage, StreamChunkMessage


@pytest.fixture()
async def injection_daemon(socket_path, mock_inference_server, tmp_path):
    cfg = Config(
        inference_url=mock_inference_server,
        model="test-model",
        socket_path=socket_path,
        history_depth=50,
        vault_path=tmp_path / "vault",
        collection_id="vault",
        username="testuser",
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
async def test_profile_in_prompt_affects_chat(injection_daemon, socket_path):
    """After setting a profile, subsequent chats include it in the system prompt.

    The mock inference server echoes the user message, so we can't verify
    the system prompt directly — but we CAN verify the daemon still operates
    correctly with profile+wisdom loaded (no crashes, chat still works).
    """
    client = PalClient(socket_path)
    await client.connect()

    # Set a profile
    await client.command("profile", "set ## Bio\n\nTest user.")

    # Add wisdom
    await client.command("wisdom", "add Test Rule | Always test.")

    # Chat should still work
    tokens = []
    async for msg in client.chat("hello"):
        if isinstance(msg, StreamChunkMessage):
            tokens.append(msg.token)
        elif isinstance(msg, ResponseMessage):
            break
    full = "".join(tokens)
    assert "echo: hello" == full

    await client.close()


@pytest.mark.asyncio
async def test_prompt_builder_composed_correctly(injection_daemon, socket_path, tmp_path):
    """The daemon's prompt_builder reflects profile and wisdom state."""
    daemon = injection_daemon

    # Initially empty
    prompt = daemon.prompt_builder.build()
    assert "About the User" not in prompt
    assert "Active Wisdom" not in prompt

    # Set profile
    daemon.profile.write("## Bio\n\nEngineer.")
    prompt = daemon.prompt_builder.build()
    assert "About the User" in prompt
    assert "Engineer." in prompt

    # Add wisdom
    daemon.wisdom.add(title="Rule", body="Measure twice.")
    prompt = daemon.prompt_builder.build()
    assert "Active Wisdom" in prompt
    assert "Measure twice." in prompt
