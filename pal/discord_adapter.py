"""Discord adapter for PAL.

Bridges Discord messages to the PAL daemon via unix socket.
Each allowed Discord user gets their own daemon connection.
"""
import logging
from pathlib import Path

from pal.client import PalClient
from pal.protocol import (
    StreamChunkMessage,
    ResponseMessage,
    ErrorMessage,
    ToolProgressMessage,
)

logger = logging.getLogger(__name__)


class UserConnectionManager:
    """Manages per-user PalClient connections to the daemon."""

    def __init__(self, allowed_users: set[str], socket_path: str | Path) -> None:
        self.allowed_users = allowed_users
        self.socket_path = Path(socket_path)
        self._clients: dict[str, PalClient] = {}

    def is_allowed(self, user_id: str) -> bool:
        return user_id in self.allowed_users

    async def get_client(self, user_id: str) -> PalClient:
        """Get or create a PalClient for a Discord user."""
        if user_id in self._clients:
            client = self._clients[user_id]
            if client._writer and not client._writer.is_closing():
                return client
            del self._clients[user_id]

        client = PalClient(self.socket_path)
        await client.connect()
        self._clients[user_id] = client
        return client

    async def close_all(self) -> None:
        """Close all daemon connections."""
        for client in self._clients.values():
            await client.close()
        self._clients.clear()


from collections.abc import AsyncGenerator
from pal.protocol import Message


async def collect_response(
    message_stream: AsyncGenerator[Message, None],
) -> tuple[list[ToolProgressMessage], str]:
    """Collect all messages from a daemon response stream.

    Returns (tool_progress_list, final_text).
    """
    progress: list[ToolProgressMessage] = []
    accumulated: list[str] = []
    final_text = ""

    async for msg in message_stream:
        if isinstance(msg, ToolProgressMessage):
            progress.append(msg)
        elif isinstance(msg, StreamChunkMessage):
            accumulated.append(msg.token)
        elif isinstance(msg, ResponseMessage):
            final_text = "".join(accumulated) if accumulated else msg.text
            break
        elif isinstance(msg, ErrorMessage):
            final_text = f"Error: {msg.error}"
            break

    return progress, final_text


_DISCORD_MSG_LIMIT = 2000


def parse_discord_message(text: str) -> tuple | None:
    """Parse a Discord message into a PAL intent.

    Returns:
        ("chat", text) for regular messages
        ("command", name, args) for ! commands
        None for empty/invalid messages
    """
    text = text.strip()
    if not text:
        return None
    if text.startswith("!"):
        rest = text[1:].strip()
        if not rest:
            return None
        parts = rest.split(None, 1)
        name = parts[0]
        args = parts[1] if len(parts) > 1 else ""
        return ("command", name, args)
    return ("chat", text)


def format_tool_progress(tool: str, arguments: dict) -> str:
    """Format a tool progress message for Discord (italic text)."""
    if tool == "read_file":
        label = f"reading {arguments.get('path', '?')}..."
    elif tool == "list_directory":
        path = arguments.get("path", "")
        label = f"listing {path or 'vault'}..."
    elif tool == "search_content":
        label = f"searching for \"{arguments.get('query', '?')}\"..."
    elif tool == "search_vault":
        label = f"searching vault for \"{arguments.get('query', '?')}\"..."
    elif tool == "edit_file":
        label = f"editing {arguments.get('path', '?')}..."
    elif tool == "create_file":
        label = f"creating {arguments.get('path', '?')}..."
    else:
        label = f"{tool}..."
    return f"*[{label}]*"


def split_message(text: str, limit: int = _DISCORD_MSG_LIMIT) -> list[str]:
    """Split a message into chunks that fit within Discord's character limit.

    Prefers splitting at paragraph boundaries (double newline).
    Falls back to splitting at the last space before the limit.
    """
    if len(text) <= limit:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at > 0:
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at + 2:]
            continue

        split_at = remaining.rfind(" ", 0, limit)
        if split_at > 0:
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at + 1:]
            continue

        chunks.append(remaining[:limit])
        remaining = remaining[limit:]

    return chunks
