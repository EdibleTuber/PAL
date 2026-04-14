"""Tests for the Discord adapter message parsing and formatting."""
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

