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
    Message,
    encode_message,
    decode_message,
)


class PalClient:
    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self) -> None:
        """Connect to the daemon's unix socket."""
        self._reader, self._writer = await asyncio.open_unix_connection(
            str(self.socket_path)
        )

    async def close(self) -> None:
        """Close the connection."""
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()
            self._writer = None
            self._reader = None

    async def chat(self, text: str) -> AsyncGenerator[Message, None]:
        """Send a chat message and yield streaming chunks + final response."""
        if not self._writer or not self._reader:
            raise RuntimeError("Not connected")
        msg = ChatMessage(text=text)
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

    async def command(self, name: str, args: str = "") -> ResponseMessage:
        """Send a slash command and return the response."""
        if not self._writer or not self._reader:
            raise RuntimeError("Not connected")
        msg = CommandMessage(name=name, args=args)
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
