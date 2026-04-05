"""Tests for the unix socket client."""
import pytest

from pal.client import PalClient
from pal.protocol import StreamChunkMessage, ResponseMessage


@pytest.mark.asyncio
async def test_client_send_chat(running_daemon, socket_path):
    """Client sends a chat message and receives streaming tokens + response."""
    client = PalClient(socket_path)
    await client.connect()

    tokens = []
    response = None
    async for msg in client.chat("hello world"):
        if isinstance(msg, StreamChunkMessage):
            tokens.append(msg.token)
        elif isinstance(msg, ResponseMessage):
            response = msg

    assert "".join(tokens) == "echo: hello world"
    assert response is not None
    assert response.text == "echo: hello world"

    await client.close()


@pytest.mark.asyncio
async def test_client_send_command(running_daemon, socket_path):
    """Client sends a command and gets a response."""
    client = PalClient(socket_path)
    await client.connect()

    response = await client.command("status")
    assert "Model:" in response.text

    await client.close()


@pytest.mark.asyncio
async def test_client_multiple_chats(running_daemon, socket_path):
    """Client can send multiple messages on the same connection."""
    client = PalClient(socket_path)
    await client.connect()

    # First chat
    async for msg in client.chat("first"):
        if isinstance(msg, ResponseMessage):
            assert msg.text == "echo: first"
            break

    # Second chat
    async for msg in client.chat("second"):
        if isinstance(msg, ResponseMessage):
            assert msg.text == "echo: second"
            break

    await client.close()
