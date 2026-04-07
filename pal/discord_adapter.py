"""Discord adapter for PAL.

Bridges Discord messages to the PAL daemon via unix socket.
Each allowed Discord user gets their own daemon connection.
"""
import logging
from pathlib import Path

import discord

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
            if client.is_connected:
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


class PalDiscordBot(discord.Client):
    """Discord bot that bridges messages to the PAL daemon."""

    def __init__(self, allowed_users: set[str], socket_path: str | Path) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.connections = UserConnectionManager(
            allowed_users=allowed_users,
            socket_path=socket_path,
        )

    async def on_ready(self) -> None:
        logger.info("PAL Discord bot connected as %s", self.user)

    async def on_message(self, message: discord.Message) -> None:
        # Never respond to ourselves
        if message.author == self.user:
            return

        # Check allowlist
        user_id = str(message.author.id)
        if not self.connections.is_allowed(user_id):
            return

        # In channels, only respond to @mentions
        if message.guild is not None:
            if self.user not in message.mentions:
                return

        # Strip the @mention from the message text if present
        text = message.content
        if self.user and self.user.mentioned_in(message):
            text = text.replace(f"<@{self.user.id}>", "").strip()

        # Parse the message
        parsed = parse_discord_message(text)
        if parsed is None:
            return

        # Show typing indicator while working
        async with message.channel.typing():
            try:
                client = await self.connections.get_client(user_id)

                if parsed[0] == "command":
                    _, name, args = parsed
                    try:
                        resp = await client.command(name, args)
                        reply_text = resp.text
                    except RuntimeError as exc:
                        reply_text = f"Error: {exc}"
                else:
                    _, chat_text = parsed
                    progress, reply_text = await collect_response(client.chat(chat_text))

                    # Prepend tool progress lines
                    if progress:
                        progress_lines = "\n".join(
                            format_tool_progress(p.tool, p.arguments) for p in progress
                        )
                        reply_text = f"{progress_lines}\n\n{reply_text}"

            except (ConnectionRefusedError, FileNotFoundError, ConnectionError):
                reply_text = "I can't reach the PAL daemon right now. Is it running?"
            except Exception as exc:
                logger.exception("Error handling message: %s", exc)
                reply_text = f"Something went wrong: {exc}"

        # Send response, splitting if needed
        for chunk in split_message(reply_text):
            await message.channel.send(chunk)

    async def close(self) -> None:
        await self.connections.close_all()
        await super().close()
