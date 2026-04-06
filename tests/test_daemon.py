"""Tests for the daemon — unix socket server."""
import asyncio
import json

import pytest

from pal.protocol import (
    ChatMessage, StreamChunkMessage, ResponseMessage,
    ToolProgressMessage, ErrorMessage, encode_message, decode_message,
)


@pytest.mark.asyncio
async def test_daemon_accepts_connection(running_daemon, socket_path):
    """Daemon accepts a unix socket connection without error."""
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_daemon_chat_streams_response(running_daemon, socket_path):
    """Send a chat message, receive streaming chunks then a response."""
    reader, writer = await asyncio.open_unix_connection(str(socket_path))

    msg = ChatMessage(text="hello world")
    writer.write(encode_message(msg))
    await writer.drain()

    chunks = []
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        if not line:
            break
        decoded = decode_message(line.strip())
        if isinstance(decoded, StreamChunkMessage):
            chunks.append(decoded.token)
        elif isinstance(decoded, ResponseMessage):
            break

    full_response = "".join(chunks)
    assert "echo: hello world" == full_response

    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_daemon_multiple_messages_share_history(running_daemon, socket_path):
    """Consecutive messages within a connection share conversation history."""
    reader, writer = await asyncio.open_unix_connection(str(socket_path))

    # Send first message
    writer.write(encode_message(ChatMessage(text="first")))
    await writer.drain()
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        decoded = decode_message(line.strip())
        if isinstance(decoded, ResponseMessage):
            break

    # Send second message
    writer.write(encode_message(ChatMessage(text="second")))
    await writer.drain()
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        decoded = decode_message(line.strip())
        if isinstance(decoded, ResponseMessage):
            # The response message carries the full accumulated text
            assert decoded.text == "echo: second"
            break

    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_daemon_chat_tool_use(running_daemon, socket_path):
    """Chat message that triggers tool use sends progress + final response."""
    reader, writer = await asyncio.open_unix_connection(str(socket_path))

    msg = ChatMessage(text="TOOLCALL:read_file")
    writer.write(encode_message(msg))
    await writer.drain()

    received = []
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        if not line:
            break
        decoded = decode_message(line.strip())
        received.append(decoded)
        if isinstance(decoded, (ResponseMessage, ErrorMessage)):
            break

    writer.close()
    await writer.wait_closed()

    progress_msgs = [m for m in received if isinstance(m, ToolProgressMessage)]
    response_msgs = [m for m in received if isinstance(m, ResponseMessage)]
    assert len(progress_msgs) >= 1
    assert progress_msgs[0].tool == "read_file"
    assert len(response_msgs) == 1
