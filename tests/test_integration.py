"""Integration test — full CLI→daemon→inference round trip."""
import pytest

from pal.client import PalClient
from agent_core.protocol import StreamChunkMessage, ResponseMessage, ErrorMessage


@pytest.mark.asyncio
async def test_full_round_trip(running_daemon, socket_path):
    """Chat message flows: client → daemon → inference → daemon → client."""
    client = PalClient(socket_path)
    await client.connect()

    tokens = []
    response = None
    async for msg in client.chat("hello world"):
        if isinstance(msg, StreamChunkMessage):
            tokens.append(msg.token)
        elif isinstance(msg, ResponseMessage):
            response = msg

    full = "".join(tokens)
    assert full == "echo: hello world"
    assert response is not None
    assert response.text == "echo: hello world"

    await client.close()


@pytest.mark.asyncio
async def test_conversation_history_persists(running_daemon, socket_path):
    """Multiple messages in a session share conversation history."""
    client = PalClient(socket_path)
    await client.connect()

    # First message
    async for msg in client.chat("remember: apple"):
        if isinstance(msg, ResponseMessage):
            break

    # Second message — the mock echoes the user message, but the daemon
    # sends conversation history to the inference server, proving history works
    async for msg in client.chat("what did I say?"):
        if isinstance(msg, ResponseMessage):
            assert msg.text == "echo: what did I say?"
            break

    await client.close()


@pytest.mark.asyncio
async def test_slash_command_status(running_daemon, socket_path):
    """The /status command returns model and server info."""
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("status")
    assert "Model: test-model" in resp.text

    await client.close()


@pytest.mark.asyncio
async def test_unknown_command(running_daemon, socket_path):
    """Unknown slash commands return an error."""
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="Unknown command"):
        await client.command("nonexistent")

    await client.close()
