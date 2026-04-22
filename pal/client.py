"""Unix socket client for talking to the PAL daemon.

Used by the CLI and potentially other clients (Signal adapter in v2).
"""
import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

from pal.protocol import (
    ChatMessage,
    CommandMessage,
    StreamChunkMessage,
    ResponseMessage,
    ErrorMessage,
    ToolProgressMessage,
    Message,
    STREAM_BUFFER_LIMIT,
    encode_message,
    decode_message,
)


class PalClient:
    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Connect to the daemon's unix socket."""
        self._reader, self._writer = await asyncio.open_unix_connection(
            str(self.socket_path),
            limit=STREAM_BUFFER_LIMIT,
        )

    @property
    def is_connected(self) -> bool:
        """Check if the connection to the daemon is alive."""
        return self._writer is not None and not self._writer.is_closing()

    async def close(self) -> None:
        """Close the connection."""
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()
            self._writer = None
            self._reader = None

    async def send(self, msg: Message) -> None:
        """Send a protocol Message to the daemon over this client's connection."""
        self._writer.write(encode_message(msg))
        await self._writer.drain()

    async def chat(
        self,
        text: str,
        *,
        channel_id: str | None = None,
    ) -> AsyncGenerator[Message, None]:
        """Send a chat message and yield streaming chunks + final response.

        Acquires a read lock so concurrent callers (e.g. multiple Discord
        channels) wait instead of crashing with concurrent readline().
        """
        if not self._writer or not self._reader:
            raise RuntimeError("Not connected")
        async with self._read_lock:
            msg = ChatMessage(text=text, channel_id=channel_id)
            self._writer.write(encode_message(msg))
            await self._writer.drain()

            while True:
                line = await self._reader.readline()
                if not line:
                    break
                decoded = decode_message(line.strip())
                yield decoded
                if isinstance(decoded, (ResponseMessage, ErrorMessage)):
                    break

    async def command(
        self,
        name: str,
        args: str = "",
        *,
        channel_id: str | None = None,
    ) -> ResponseMessage:
        """Send a slash command and return the response."""
        if not self._writer or not self._reader:
            raise RuntimeError("Not connected")
        async with self._read_lock:
            msg = CommandMessage(name=name, args=args, channel_id=channel_id)
            self._writer.write(encode_message(msg))
            await self._writer.drain()

            while True:
                line = await self._reader.readline()
                if not line:
                    raise ConnectionError("Connection closed")
                decoded = decode_message(line.strip())
                if isinstance(decoded, ResponseMessage):
                    return decoded
                if isinstance(decoded, ErrorMessage):
                    raise RuntimeError(decoded.error)

    async def command_stream(
        self, name: str, args: str = "", *, channel_id: str | None = None
    ) -> AsyncGenerator[Message, None]:
        """Send a slash command and yield all messages including progress."""
        if not self._writer or not self._reader:
            raise RuntimeError("Not connected")
        async with self._read_lock:
            msg = CommandMessage(name=name, args=args, channel_id=channel_id)
            self._writer.write(encode_message(msg))
            await self._writer.drain()

            while True:
                line = await self._reader.readline()
                if not line:
                    break
                decoded = decode_message(line.strip())
                yield decoded
                if isinstance(decoded, (ResponseMessage, ErrorMessage)):
                    break
