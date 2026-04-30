"""Tests for the Discord adapter message parsing and formatting."""
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pal.discord_adapter import parse_discord_message, format_tool_progress, split_message


def test_parse_chat_message():
    result = parse_discord_message("what files are in the vault?")
    assert result == ("chat", "what files are in the vault?")


def test_parse_command_message():
    result = parse_discord_message("!search quantum computing")
    assert result == ("command", "search", "quantum computing")


def test_parse_command_no_args():
    result = parse_discord_message("!status")
    assert result == ("command", "status", "")


def test_parse_command_help():
    result = parse_discord_message("!help")
    assert result == ("command", "help", "")


def test_parse_empty_message():
    result = parse_discord_message("   ")
    assert result is None


def test_parse_bang_only():
    result = parse_discord_message("!")
    assert result is None


def test_format_tool_progress_read():
    result = format_tool_progress("read_file", {"path": "Research/quantum.md"})
    assert "reading" in result.lower()
    assert "Research/quantum.md" in result


def test_format_tool_progress_list():
    result = format_tool_progress("list_directory", {"path": ""})
    assert "listing" in result.lower()


def test_format_tool_progress_edit():
    result = format_tool_progress("edit_file", {"path": "Research/quantum.md"})
    assert "editing" in result.lower()


def test_format_tool_progress_unknown():
    result = format_tool_progress("some_tool", {})
    assert "some_tool" in result


def test_split_message_short():
    result = split_message("Hello world")
    assert result == ["Hello world"]


def test_split_message_at_paragraph():
    para1 = "A" * 1000
    para2 = "B" * 1000
    para3 = "C" * 500
    text = f"{para1}\n\n{para2}\n\n{para3}"
    result = split_message(text, limit=2000)
    assert len(result) == 2
    assert para1 in result[0]
    assert para3 in result[1]


def test_split_message_single_long_paragraph():
    text = " ".join(["word"] * 500)
    result = split_message(text, limit=2000)
    assert len(result) == 2
    assert all(len(chunk) <= 2000 for chunk in result)


def test_split_message_exact_limit():
    text = "A" * 2000
    result = split_message(text, limit=2000)
    assert result == [text]


# --- Task 2: UserConnectionManager ---

from pal.discord_adapter import UserConnectionManager


@pytest.mark.asyncio
async def test_allowlist_blocks_unknown_user():
    mgr = UserConnectionManager(
        allowed_users={"111", "222"},
        socket_path="/tmp/fake.sock",
    )
    assert mgr.is_allowed("111")
    assert mgr.is_allowed("222")
    assert not mgr.is_allowed("999")


@pytest.mark.asyncio
async def test_allowlist_empty_blocks_all():
    mgr = UserConnectionManager(
        allowed_users=set(),
        socket_path="/tmp/fake.sock",
    )
    assert not mgr.is_allowed("111")


# --- Task 9: channel_id forwarding ---

from pal.discord_adapter import PalDiscordBot
from agent_core.protocol import ResponseMessage, StreamChunkMessage


def _make_fake_message(content: str, author_id: int, channel_id: int, *, in_guild: bool = False):
    """Build a minimal fake discord.Message for on_message tests."""
    msg = MagicMock()
    msg.content = content
    msg.author.id = author_id
    msg.author.bot = False
    msg.channel.id = channel_id
    msg.channel.typing = MagicMock(return_value=contextlib.asynccontextmanager(
        lambda: (x async for x in _null_cm())
    )())
    msg.channel.send = AsyncMock()
    msg.guild = MagicMock() if in_guild else None
    msg.mentions = []
    return msg


async def _null_cm():
    """Yield nothing — used as a body for an async context manager."""
    yield  # pragma: no cover


def _make_bot_with_fake_user() -> "PalDiscordBot":
    """Create a PalDiscordBot whose .user property returns a stable fake user."""
    bot = PalDiscordBot(allowed_users={"12345"}, socket_path="/tmp/fake.sock")
    # discord.Client.user reads from self._connection.user; patch that.
    fake_user = MagicMock()
    fake_user.id = 99  # different from test message author ids
    bot._connection.user = fake_user
    return bot


@pytest.mark.asyncio
async def test_on_message_chat_forwards_channel_id():
    """on_message passes str(message.channel.id) as channel_id to client.chat."""
    bot = _make_bot_with_fake_user()

    captured: dict = {}

    async def fake_chat(text, *, channel_id=None):
        captured["text"] = text
        captured["channel_id"] = channel_id
        yield StreamChunkMessage(token="hi")
        yield ResponseMessage(text="hi")

    fake_client = MagicMock()
    fake_client.chat = fake_chat

    bot.connections = MagicMock()
    bot.connections.is_allowed = MagicMock(return_value=True)
    bot.connections.get_client = AsyncMock(return_value=fake_client)
    bot.active_proposals = {}

    fake_msg = _make_fake_message("hello world", author_id=12345, channel_id=99999)
    fake_msg.author = MagicMock()
    fake_msg.author.id = 12345
    fake_msg.author.bot = False
    # Make sure message.author != bot.user (the equality check uses `is` too)
    fake_msg.author.__eq__ = lambda self, other: False

    await bot.on_message(fake_msg)

    assert captured.get("channel_id") == "99999"
    assert captured.get("text") == "hello world"


@pytest.mark.asyncio
async def test_on_message_command_forwards_channel_id():
    """on_message passes str(message.channel.id) as channel_id to client.command."""
    bot = _make_bot_with_fake_user()

    captured: dict = {}

    async def fake_command(name, args="", *, channel_id=None):
        captured["name"] = name
        captured["args"] = args
        captured["channel_id"] = channel_id
        return ResponseMessage(text="done")

    fake_client = MagicMock()
    fake_client.command = fake_command

    bot.connections = MagicMock()
    bot.connections.is_allowed = MagicMock(return_value=True)
    bot.connections.get_client = AsyncMock(return_value=fake_client)
    bot.active_proposals = {}

    fake_msg = _make_fake_message("!status", author_id=12345, channel_id=77777)
    fake_msg.author = MagicMock()
    fake_msg.author.id = 12345
    fake_msg.author.bot = False
    fake_msg.author.__eq__ = lambda self, other: False

    await bot.on_message(fake_msg)

    assert captured.get("channel_id") == "77777"
    assert captured.get("name") == "status"

