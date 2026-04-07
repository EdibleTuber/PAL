# Discord Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Discord adapter so PAL can be accessed via Discord DMs and channel @mentions, using the existing daemon and unix socket protocol.

**Architecture:** A standalone `pal-discord` process connects to Discord via `discord.py` and to the PAL daemon via `PalClient` (the same unix socket client the CLI uses). Each allowed Discord user gets their own `PalClient` connection. Messages are translated between Discord and the daemon protocol.

**Tech Stack:** Python 3.12, discord.py>=2.0, existing PalClient, pytest

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `pal/discord_adapter.py` | Create | Discord client, user connection management, message translation, response formatting |
| `pal/discord_main.py` | Create | Entry point for `pal-discord` command |
| `tests/test_discord_adapter.py` | Create | Unit tests for message parsing, formatting, splitting, allowlist |
| `pyproject.toml` | Modify | Add `pal-discord` entry point, add `discord.py` dependency |
| `systemd/pal-discord.service` | Create | Systemd unit file |

---

### Task 1: Message parsing and response formatting (pure functions)

**Files:**
- Create: `pal/discord_adapter.py`
- Create: `tests/test_discord_adapter.py`

- [ ] **Step 1: Write failing tests for message parsing**

```python
# tests/test_discord_adapter.py
"""Tests for the Discord adapter message parsing and formatting."""
import pytest

from pal.discord_adapter import parse_discord_message, format_tool_progress, split_message


def test_parse_chat_message():
    """Regular text becomes a chat intent."""
    result = parse_discord_message("what files are in the vault?")
    assert result == ("chat", "what files are in the vault?")


def test_parse_command_message():
    """! prefix becomes a command intent."""
    result = parse_discord_message("!search quantum computing")
    assert result == ("command", "search", "quantum computing")


def test_parse_command_no_args():
    """! command with no arguments."""
    result = parse_discord_message("!status")
    assert result == ("command", "status", "")


def test_parse_command_help():
    """!help maps correctly."""
    result = parse_discord_message("!help")
    assert result == ("command", "help", "")


def test_parse_empty_message():
    """Empty or whitespace-only message."""
    result = parse_discord_message("   ")
    assert result is None


def test_parse_bang_only():
    """Just a ! with nothing after it."""
    result = parse_discord_message("!")
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_discord_adapter.py -v`
Expected: `ModuleNotFoundError: No module named 'pal.discord_adapter'`

- [ ] **Step 3: Implement parse_discord_message**

```python
# pal/discord_adapter.py
"""Discord adapter for PAL.

Bridges Discord messages to the PAL daemon via unix socket.
Each allowed Discord user gets their own daemon connection.
"""
import logging

logger = logging.getLogger(__name__)


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
```

- [ ] **Step 4: Run parse tests to verify they pass**

Run: `pytest tests/test_discord_adapter.py -v`
Expected: 6 pass (format/split tests still fail since not implemented yet)

- [ ] **Step 5: Write failing tests for tool progress formatting**

Add to `tests/test_discord_adapter.py`:

```python
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
```

- [ ] **Step 6: Implement format_tool_progress**

Add to `pal/discord_adapter.py`:

```python
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
```

- [ ] **Step 7: Run tests to verify progress formatting passes**

Run: `pytest tests/test_discord_adapter.py -v`
Expected: 10 pass (split tests still fail)

- [ ] **Step 8: Write failing tests for message splitting**

Add to `tests/test_discord_adapter.py`:

```python
def test_split_message_short():
    """Short messages are returned as-is in a single-element list."""
    result = split_message("Hello world")
    assert result == ["Hello world"]


def test_split_message_at_paragraph():
    """Long messages split at paragraph boundaries."""
    para1 = "A" * 1000
    para2 = "B" * 1000
    para3 = "C" * 500
    text = f"{para1}\n\n{para2}\n\n{para3}"
    result = split_message(text, limit=2000)
    assert len(result) == 2
    assert para1 in result[0]
    assert para3 in result[1]


def test_split_message_single_long_paragraph():
    """A single paragraph longer than the limit splits at the last space."""
    text = " ".join(["word"] * 500)  # ~2500 chars
    result = split_message(text, limit=2000)
    assert len(result) == 2
    assert all(len(chunk) <= 2000 for chunk in result)


def test_split_message_exact_limit():
    """Message exactly at limit stays as one."""
    text = "A" * 2000
    result = split_message(text, limit=2000)
    assert result == [text]
```

- [ ] **Step 9: Implement split_message**

Add to `pal/discord_adapter.py`:

```python
_DISCORD_MSG_LIMIT = 2000


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

        # Try to split at a paragraph boundary
        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at > 0:
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at + 2:]  # skip the \n\n
            continue

        # Fall back to splitting at last space
        split_at = remaining.rfind(" ", 0, limit)
        if split_at > 0:
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at + 1:]  # skip the space
            continue

        # No good split point, hard cut
        chunks.append(remaining[:limit])
        remaining = remaining[limit:]

    return chunks
```

- [ ] **Step 10: Run all tests**

Run: `pytest tests/test_discord_adapter.py -v`
Expected: All 14 tests pass.

- [ ] **Step 11: Commit**

```bash
git add pal/discord_adapter.py tests/test_discord_adapter.py
git commit -m "feat(discord): message parsing, tool progress formatting, message splitting"
```

---

### Task 2: User connection manager

**Files:**
- Modify: `pal/discord_adapter.py`
- Modify: `tests/test_discord_adapter.py`

- [ ] **Step 1: Write failing tests for allowlist and connection manager**

Add to `tests/test_discord_adapter.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_discord_adapter.py::test_allowlist_blocks_unknown_user -v`
Expected: `ImportError: cannot import name 'UserConnectionManager'`

- [ ] **Step 3: Implement UserConnectionManager**

Add to `pal/discord_adapter.py`:

```python
from pathlib import Path

from pal.client import PalClient
from pal.protocol import (
    StreamChunkMessage,
    ResponseMessage,
    ErrorMessage,
    ToolProgressMessage,
)


class UserConnectionManager:
    """Manages per-user PalClient connections to the daemon.

    Each allowed Discord user gets their own connection and conversation history.
    Connections are created lazily on first message and reused.
    """

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
            # Check if connection is still alive
            if client._writer and not client._writer.is_closing():
                return client
            # Connection dead, remove and recreate
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_discord_adapter.py -v`
Expected: All 16 tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/discord_adapter.py tests/test_discord_adapter.py
git commit -m "feat(discord): user connection manager with allowlist"
```

---

### Task 3: Response collection helper

**Files:**
- Modify: `pal/discord_adapter.py`
- Modify: `tests/test_discord_adapter.py`

- [ ] **Step 1: Write failing test for collect_response**

Add to `tests/test_discord_adapter.py`:

```python
from pal.protocol import StreamChunkMessage, ResponseMessage, ToolProgressMessage, ErrorMessage
from pal.discord_adapter import collect_response


@pytest.mark.asyncio
async def test_collect_response_text_only():
    """Collects streamed text into a single response."""
    async def fake_chat(text):
        yield StreamChunkMessage(token="Hello ")
        yield StreamChunkMessage(token="world")
        yield ResponseMessage(text="Hello world")

    progress, text = await collect_response(fake_chat("hi"))
    assert progress == []
    assert text == "Hello world"


@pytest.mark.asyncio
async def test_collect_response_with_tools():
    """Collects tool progress and final response."""
    async def fake_chat(text):
        yield ToolProgressMessage(tool="read_file", arguments={"path": "Research/quantum.md"})
        yield ResponseMessage(text="Here is the file content.")

    progress, text = await collect_response(fake_chat("read it"))
    assert len(progress) == 1
    assert progress[0].tool == "read_file"
    assert text == "Here is the file content."


@pytest.mark.asyncio
async def test_collect_response_error():
    """Errors are returned as the text."""
    async def fake_chat(text):
        yield ErrorMessage(error="Inference error: timeout")

    progress, text = await collect_response(fake_chat("hi"))
    assert "error" in text.lower() or "Error" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_discord_adapter.py::test_collect_response_text_only -v`
Expected: `ImportError: cannot import name 'collect_response'`

- [ ] **Step 3: Implement collect_response**

Add to `pal/discord_adapter.py`:

```python
from collections.abc import AsyncGenerator
from pal.protocol import Message


async def collect_response(
    message_stream: AsyncGenerator[Message, None],
) -> tuple[list[ToolProgressMessage], str]:
    """Collect all messages from a daemon response stream.

    Returns (tool_progress_list, final_text). Accumulates streaming tokens
    and tool progress messages. Returns the final text from either the
    accumulated stream or the ResponseMessage.
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_discord_adapter.py -v`
Expected: All 19 tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/discord_adapter.py tests/test_discord_adapter.py
git commit -m "feat(discord): response collection helper for daemon message streams"
```

---

### Task 4: Discord bot class

**Files:**
- Modify: `pal/discord_adapter.py`

- [ ] **Step 1: Implement PalDiscordBot**

Add to `pal/discord_adapter.py`:

```python
import discord


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
```

- [ ] **Step 2: Run full test suite to check for import issues**

Run: `pytest -v`
Expected: All tests pass. The `discord` import only runs when PalDiscordBot is instantiated, so tests that don't create one won't fail even if discord.py isn't installed yet. But if tests do fail due to missing import, proceed to Task 5 (pyproject.toml) first.

- [ ] **Step 3: Commit**

```bash
git add pal/discord_adapter.py
git commit -m "feat(discord): PalDiscordBot class bridging Discord to daemon"
```

---

### Task 5: Entry point, config, and deployment

**Files:**
- Create: `pal/discord_main.py`
- Modify: `pyproject.toml`
- Create: `systemd/pal-discord.service`

- [ ] **Step 1: Create discord_main.py**

```python
# pal/discord_main.py
"""Entry point for the PAL Discord adapter."""
import logging
import os
import sys

from pal.config import load_config


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    token = os.environ.get("PAL_DISCORD_TOKEN")
    if not token:
        print("Error: PAL_DISCORD_TOKEN environment variable is required.")
        sys.exit(1)

    allowed_str = os.environ.get("PAL_DISCORD_ALLOWED_USERS", "")
    allowed_users = {uid.strip() for uid in allowed_str.split(",") if uid.strip()}
    if not allowed_users:
        print("Warning: PAL_DISCORD_ALLOWED_USERS is empty. Bot will not respond to anyone.")

    config = load_config()

    from pal.discord_adapter import PalDiscordBot

    bot = PalDiscordBot(
        allowed_users=allowed_users,
        socket_path=config.socket_path,
    )
    bot.run(token)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Update pyproject.toml**

Add `discord.py` to dependencies and `pal-discord` entry point:

```toml
[project]
name = "pal"
version = "0.1.0"
description = "Personal Agentic Librarian — CLI conversational companion"
requires-python = ">=3.12"
dependencies = [
    "httpx>=0.27.0",
    "prompt-toolkit>=3.0.0",
    "rich>=13.0.0",
    "pyyaml>=6.0",
    "trafilatura>=1.12.0",
    "discord.py>=2.0",
]

[project.scripts]
pal = "pal.cli:main"
pal-daemon = "pal.daemon_main:main"
pal-discord = "pal.discord_main:main"
```

- [ ] **Step 3: Create systemd service file**

```ini
# systemd/pal-discord.service
[Unit]
Description=PAL Discord Adapter
After=network.target pal-daemon.service
Requires=pal-daemon.service

[Service]
Type=simple
WorkingDirectory=/mnt/secondary/PAL
ExecStart=/mnt/secondary/PAL/.venv/bin/python -m pal.discord_main
Restart=on-failure
RestartSec=5
EnvironmentFile=/etc/pal/discord.env

[Install]
WantedBy=default.target
```

- [ ] **Step 4: Install updated dependencies**

Run: `pip install -e ".[dev]"`

- [ ] **Step 5: Run full test suite**

Run: `pytest -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add pal/discord_main.py pyproject.toml systemd/pal-discord.service
git commit -m "feat(discord): entry point, dependency, and systemd service"
```

---

### Task 6: End-to-end manual test

- [ ] **Step 1: Create a Discord bot**

1. Go to https://discord.com/developers/applications
2. Create a new application (e.g., "PAL")
3. Go to Bot settings, create a bot
4. Enable "Message Content Intent" under Privileged Gateway Intents
5. Copy the bot token
6. Go to OAuth2 > URL Generator, select "bot" scope with "Send Messages" and "Read Message History" permissions
7. Use the generated URL to invite the bot to your server

- [ ] **Step 2: Configure and start the adapter**

```bash
export PAL_DISCORD_TOKEN="your-bot-token-here"
export PAL_DISCORD_ALLOWED_USERS="your-discord-user-id"

# Make sure daemon is running
pal-daemon &

# Start the Discord adapter
pal-discord
```

- [ ] **Step 3: Test DM chat**

Open Discord, DM the bot:
```
you: Hey, what files are in the vault?
PAL: *[listing vault...]*

Contents of (vault root):
  Research/
  ...
```

- [ ] **Step 4: Test commands**

```
you: !status
PAL: Model: Qwen3.5-35B-A3B-Q4_K_M
     Server: http://192.168.1.14:11434
     ...
```

- [ ] **Step 5: Test channel @mention**

In a server channel:
```
you: @PAL what do you know about quantum computing?
PAL: *[searching vault for "quantum computing"...]*
     ...
```

- [ ] **Step 6: Test non-allowed user**

Have someone else (or a second account) try to DM the bot. Should get no response.

- [ ] **Step 7: Set up systemd (optional)**

```bash
# Create env file
sudo mkdir -p /etc/pal
sudo tee /etc/pal/discord.env << 'EOF'
PAL_DISCORD_TOKEN=your-token
PAL_DISCORD_ALLOWED_USERS=your-user-id
EOF

# Install and start service
sudo cp systemd/pal-discord.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pal-discord
```
