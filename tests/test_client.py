"""Tests for the unix socket client."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from pal.client import PalClient
from pal.protocol import StreamChunkMessage, ResponseMessage, ChatMessage, decode_message


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


@pytest.mark.asyncio
async def test_client_send_writes_encoded_message(tmp_path):
    client = PalClient(socket_path=tmp_path / "fake.sock")
    # Inject a mock writer to avoid real socket I/O.
    mock_writer = MagicMock()
    mock_writer.drain = AsyncMock()
    client._writer = mock_writer

    msg = ChatMessage(text="hello")
    await client.send(msg)

    # writer.write was called once with encoded bytes that round-trip to msg.
    mock_writer.write.assert_called_once()
    written = mock_writer.write.call_args[0][0]
    decoded = decode_message(written.strip())
    assert isinstance(decoded, ChatMessage)
    assert decoded.text == "hello"
    mock_writer.drain.assert_awaited_once()
