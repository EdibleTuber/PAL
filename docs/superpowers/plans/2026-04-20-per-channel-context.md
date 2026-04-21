# Per-Channel Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make PAL's Discord bridge and daemon scope conversation history and a free-form scratchpad per Discord channel, persisted across daemon restarts, so the same user's channels stay isolated and PAL has working project context.

**Architecture:** Extend the protocol with an optional `channel_id` string. Replace per-connection `Conversation` state with a `ChannelStore` keyed by channel. Add a `Scratchpad` component backed by a `_channels/<channel_id>/scratch.md` file in the vault. PAL updates the scratchpad via a new `update_scratch` tool; the user can also append notes via a new `/note` slash command. 2 KB size cap.

**Tech Stack:** Python 3.12, asyncio, dataclasses, existing `WikiManager` for vault commits, pytest, pytest-asyncio.

**Reference spec:** `docs/superpowers/specs/2026-04-20-per-channel-context-design.md`

---

## Task 1: Extend protocol with `channel_id`

**Files:**
- Modify: `pal/protocol.py`
- Modify: `tests/test_protocol.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_protocol.py`:

```python
def test_chat_message_defaults_channel_id_to_none():
    from pal.protocol import ChatMessage
    msg = ChatMessage(text="hi")
    assert msg.channel_id is None
    assert msg.text == "hi"


def test_chat_message_carries_channel_id():
    from pal.protocol import ChatMessage, encode_message, decode_message
    msg = ChatMessage(text="hi", channel_id="C1")
    line = encode_message(msg)
    decoded = decode_message(line.strip())
    assert isinstance(decoded, ChatMessage)
    assert decoded.text == "hi"
    assert decoded.channel_id == "C1"


def test_command_message_defaults_channel_id_to_none():
    from pal.protocol import CommandMessage
    msg = CommandMessage(name="note", args="hello")
    assert msg.channel_id is None


def test_command_message_round_trip_with_channel_id():
    from pal.protocol import CommandMessage, encode_message, decode_message
    msg = CommandMessage(name="note", args="foo", channel_id="C1")
    line = encode_message(msg)
    decoded = decode_message(line.strip())
    assert isinstance(decoded, CommandMessage)
    assert decoded.name == "note"
    assert decoded.args == "foo"
    assert decoded.channel_id == "C1"


def test_chat_message_round_trip_without_channel_id_backward_compat():
    """A ChatMessage without channel_id serializes and deserializes cleanly."""
    from pal.protocol import ChatMessage, encode_message, decode_message
    msg = ChatMessage(text="hi")
    decoded = decode_message(encode_message(msg).strip())
    assert isinstance(decoded, ChatMessage)
    assert decoded.channel_id is None
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_protocol.py -v -k channel_id
```

Expected: FAIL — `TypeError: ChatMessage.__init__() got an unexpected keyword argument 'channel_id'` or similar.

- [ ] **Step 3: Add `channel_id` field to `ChatMessage` and `CommandMessage`**

In `pal/protocol.py`, modify the existing dataclasses:

```python
@dataclass
class ChatMessage:
    text: str
    channel_id: str | None = None
    type: str = "chat"


@dataclass
class CommandMessage:
    name: str
    args: str
    channel_id: str | None = None
    type: str = "command"
```

`encode_message` and `decode_message` use `asdict` / construct from the dict, so adding the field is self-integrating. No codec changes needed.

- [ ] **Step 4: Verify tests pass**

```bash
python -m pytest tests/test_protocol.py -v
```

Expected: all tests pass, including the new ones.

Full suite:

```bash
python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: no regressions.

- [ ] **Step 5: Commit**

```bash
git add pal/protocol.py tests/test_protocol.py
git commit -m "feat(protocol): add optional channel_id to ChatMessage and CommandMessage"
```

---

## Task 2: Add config fields for channels dir and scratchpad cap

**Files:**
- Modify: `pal/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_config.py`:

```python
def test_config_default_channels_dir():
    from pal.config import Config
    from pathlib import Path
    cfg = Config()
    assert cfg.channels_dir == Path.home() / ".local/share/pal/channels"


def test_config_default_scratchpad_max_bytes():
    from pal.config import Config
    cfg = Config()
    assert cfg.scratchpad_max_bytes == 2048


def test_config_env_overrides_channels_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("PAL_CHANNELS_DIR", str(tmp_path / "custom"))
    from pal.config import load_config
    cfg = load_config()
    assert cfg.channels_dir == tmp_path / "custom"


def test_config_env_overrides_scratchpad_max_bytes(monkeypatch):
    monkeypatch.setenv("PAL_SCRATCHPAD_MAX_BYTES", "4096")
    from pal.config import load_config
    cfg = load_config()
    assert cfg.scratchpad_max_bytes == 4096
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_config.py -v -k "channels_dir or scratchpad"
```

Expected: FAIL (field doesn't exist).

- [ ] **Step 3: Add fields to `Config` and wire env loading**

In `pal/config.py`, add to the `Config` dataclass (after existing fields):

```python
    channels_dir: Path = field(
        default_factory=lambda: Path.home() / ".local/share/pal/channels"
    )
    scratchpad_max_bytes: int = 2048
```

Update `load_config()` to handle the env overrides. Add at the end of the existing if-chain (before `return Config(**kwargs)`):

```python
    if cd := os.environ.get("PAL_CHANNELS_DIR"):
        kwargs["channels_dir"] = Path(cd)
    if smb := os.environ.get("PAL_SCRATCHPAD_MAX_BYTES"):
        kwargs["scratchpad_max_bytes"] = int(smb)
```

- [ ] **Step 4: Verify tests pass**

```bash
python -m pytest tests/test_config.py -v
python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: new tests pass, no regressions.

- [ ] **Step 5: Commit**

```bash
git add pal/config.py tests/test_config.py
git commit -m "feat(config): add channels_dir and scratchpad_max_bytes"
```

---

## Task 3: Extend `Conversation` with optional history persistence

**Files:**
- Modify: `pal/conversation.py`
- Modify: `tests/test_conversation.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_conversation.py`:

```python
def test_conversation_without_history_path_is_in_memory_only(tmp_path):
    """Backward compat: no history_path means no file written."""
    from pal.conversation import Conversation
    conv = Conversation(history_depth=10)
    conv.add_user("hello")
    conv.add_assistant("hi back")
    # No file should exist anywhere.
    assert not list(tmp_path.iterdir())


def test_conversation_with_history_path_appends_every_message(tmp_path):
    import json
    from pal.conversation import Conversation
    history_path = tmp_path / "history.jsonl"
    conv = Conversation(history_depth=10, history_path=history_path)

    conv.add_user("hello")
    conv.add_assistant("hi back")
    conv.add_assistant_tool_calls([{"id": "c1", "type": "function",
                                     "function": {"name": "foo", "arguments": "{}"}}])
    conv.add_tool_result("c1", "result")

    lines = history_path.read_text().splitlines()
    assert len(lines) == 4

    parsed = [json.loads(line) for line in lines]
    assert parsed[0] == {"role": "user", "content": "hello"}
    assert parsed[1] == {"role": "assistant", "content": "hi back"}
    assert parsed[2]["role"] == "assistant"
    assert parsed[2]["tool_calls"][0]["id"] == "c1"
    assert parsed[3] == {"role": "tool", "tool_call_id": "c1", "content": "result"}


def test_conversation_history_path_creates_parent_dir(tmp_path):
    from pal.conversation import Conversation
    nested = tmp_path / "a" / "b" / "history.jsonl"
    conv = Conversation(history_depth=10, history_path=nested)
    conv.add_user("hi")
    assert nested.exists()


def test_conversation_truncation_does_not_truncate_history_file(tmp_path):
    """Truncation only affects the in-memory window; the on-disk log keeps everything."""
    from pal.conversation import Conversation
    history_path = tmp_path / "history.jsonl"
    conv = Conversation(history_depth=2, history_path=history_path)
    for i in range(5):
        conv.add_user(f"msg-{i}")
    # In-memory: only last 2
    assert len(conv.messages) == 2
    # On-disk: all 5
    assert len(history_path.read_text().splitlines()) == 5
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_conversation.py -v -k "history_path or in_memory_only or truncation_does_not"
```

Expected: FAIL (field doesn't exist, or file not written).

- [ ] **Step 3: Extend `Conversation` with `history_path`**

In `pal/conversation.py`, modify the dataclass:

```python
from pathlib import Path
import json


@dataclass
class Conversation:
    history_depth: int
    history_path: Path | None = None
    _messages: list[dict] = field(default_factory=list)
    reasoning_override: Literal["on", "off"] | None = None

    def _append_to_history_file(self, message: dict) -> None:
        """Append a single message to the history JSONL file, if configured."""
        if self.history_path is None:
            return
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(message, ensure_ascii=False) + "\n")
```

Then modify each of `add_user`, `add_assistant`, `add_assistant_tool_calls`, and `add_tool_result` to call `self._append_to_history_file(message)` immediately before `self._truncate()`. Example for `add_user`:

```python
    def add_user(self, text: str) -> None:
        message = {"role": "user", "content": text}
        self._messages.append(message)
        self._append_to_history_file(message)
        self._truncate()
```

Apply the same pattern to the other three. The in-memory truncation stays unchanged — only the in-memory window is bounded; the on-disk file grows forever.

- [ ] **Step 4: Verify tests pass**

```bash
python -m pytest tests/test_conversation.py -v
python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/conversation.py tests/test_conversation.py
git commit -m "feat(conversation): optional history persistence via history_path"
```

---

## Task 4: Create `pal/channels.py` with `ChannelStore`

**Files:**
- Create: `pal/channels.py`
- Create: `tests/test_channels.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_channels.py`:

```python
"""Tests for ChannelStore — per-channel Conversation container with persistence."""
import asyncio
import json
import pytest
from pathlib import Path
from pal.channels import ChannelStore, validate_channel_id


def test_validate_channel_id_accepts_alphanumeric():
    assert validate_channel_id("abc123") is True
    assert validate_channel_id("ABC-123") is True
    assert validate_channel_id("cli-default") is True
    assert validate_channel_id("1234567890") is True  # Discord snowflake


def test_validate_channel_id_rejects_path_traversal():
    assert validate_channel_id("../etc") is False
    assert validate_channel_id("/absolute") is False
    assert validate_channel_id("a/b") is False
    assert validate_channel_id("") is False
    assert validate_channel_id("has space") is False
    assert validate_channel_id("has.dot") is False


@pytest.mark.asyncio
async def test_get_or_create_creates_directory(tmp_path):
    store = ChannelStore(channels_dir=tmp_path, history_depth=10)
    conv = await store.get_or_create("C1")
    assert (tmp_path / "C1").is_dir()
    # Fresh conversation has no messages
    assert conv.messages == []


@pytest.mark.asyncio
async def test_get_or_create_caches_instance(tmp_path):
    store = ChannelStore(channels_dir=tmp_path, history_depth=10)
    conv1 = await store.get_or_create("C1")
    conv2 = await store.get_or_create("C1")
    assert conv1 is conv2


@pytest.mark.asyncio
async def test_get_or_create_replays_existing_history(tmp_path):
    channel_dir = tmp_path / "C1"
    channel_dir.mkdir()
    history_path = channel_dir / "history.jsonl"
    history_path.write_text(
        '{"role": "user", "content": "hi"}\n'
        '{"role": "assistant", "content": "hello"}\n'
    )

    store = ChannelStore(channels_dir=tmp_path, history_depth=10)
    conv = await store.get_or_create("C1")

    assert len(conv.messages) == 2
    assert conv.messages[0] == {"role": "user", "content": "hi"}
    assert conv.messages[1] == {"role": "assistant", "content": "hello"}


@pytest.mark.asyncio
async def test_get_or_create_skips_malformed_lines_with_warning(tmp_path, caplog):
    import logging
    channel_dir = tmp_path / "C1"
    channel_dir.mkdir()
    (channel_dir / "history.jsonl").write_text(
        '{"role": "user", "content": "hi"}\n'
        'this is not json\n'
        '{"role": "assistant", "content": "hello"}\n'
    )

    store = ChannelStore(channels_dir=tmp_path, history_depth=10)
    with caplog.at_level(logging.WARNING):
        conv = await store.get_or_create("C1")

    # Good lines replayed; bad line skipped.
    assert len(conv.messages) == 2
    assert any("malformed" in rec.message.lower() or "skip" in rec.message.lower()
               for rec in caplog.records)


@pytest.mark.asyncio
async def test_get_or_create_renames_unreadable_history(tmp_path, monkeypatch):
    """If the history file can't even be opened, rename it and start fresh."""
    channel_dir = tmp_path / "C1"
    channel_dir.mkdir()
    history_path = channel_dir / "history.jsonl"
    history_path.write_text('{"role": "user", "content": "hi"}')

    # Force Path.open to raise for this one file.
    real_open = Path.open
    def patched_open(self, *args, **kwargs):
        if self == history_path and "r" in (args[0] if args else kwargs.get("mode", "r")):
            raise OSError("simulated read failure")
        return real_open(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", patched_open)

    store = ChannelStore(channels_dir=tmp_path, history_depth=10)
    conv = await store.get_or_create("C1")

    # Original was renamed
    assert not history_path.exists()
    corrupt_files = list(channel_dir.glob("history.jsonl.corrupt-*"))
    assert len(corrupt_files) == 1
    # Fresh conversation
    assert conv.messages == []


@pytest.mark.asyncio
async def test_get_or_create_rejects_invalid_channel_id(tmp_path):
    store = ChannelStore(channels_dir=tmp_path, history_depth=10)
    with pytest.raises(ValueError, match="invalid channel_id"):
        await store.get_or_create("../etc")


@pytest.mark.asyncio
async def test_conversation_appends_to_history_file(tmp_path):
    """The Conversation returned from get_or_create is wired to persist new messages."""
    store = ChannelStore(channels_dir=tmp_path, history_depth=10)
    conv = await store.get_or_create("C1")
    conv.add_user("hello")

    history_path = tmp_path / "C1" / "history.jsonl"
    assert history_path.exists()
    line = history_path.read_text().strip()
    assert json.loads(line) == {"role": "user", "content": "hello"}
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_channels.py -v
```

Expected: `ModuleNotFoundError: No module named 'pal.channels'`.

- [ ] **Step 3: Create `pal/channels.py`**

```python
"""Per-channel Conversation container with on-disk persistence.

Each channel (identified by a free-form string — Discord channel ID,
`cli-default` for CLI, etc.) gets its own Conversation instance, backed
by a jsonl file at <channels_dir>/<channel_id>/history.jsonl. On first
access for a channel, if the file exists, its contents are replayed into
a fresh Conversation. Subsequent accesses return the same cached instance.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from pal.conversation import Conversation

logger = logging.getLogger(__name__)

_CHANNEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_channel_id(channel_id: str) -> bool:
    """Return True if the id matches the allowed character set and is non-empty."""
    return bool(_CHANNEL_ID_PATTERN.match(channel_id))


class ChannelStore:
    """Caches Conversation instances per channel, loading from disk as needed."""

    def __init__(self, channels_dir: Path, history_depth: int) -> None:
        self._channels_dir = channels_dir
        self._history_depth = history_depth
        self._cache: dict[str, Conversation] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, channel_id: str) -> Conversation:
        """Return the Conversation for channel_id, loading or creating as needed."""
        if not validate_channel_id(channel_id):
            raise ValueError(f"invalid channel_id: {channel_id!r}")
        async with self._lock:
            if channel_id in self._cache:
                return self._cache[channel_id]

            channel_dir = self._channels_dir / channel_id
            channel_dir.mkdir(parents=True, exist_ok=True)
            history_path = channel_dir / "history.jsonl"

            conv = Conversation(
                history_depth=self._history_depth,
                history_path=history_path,
            )

            if history_path.exists():
                self._replay_into(conv, history_path)

            self._cache[channel_id] = conv
            return conv

    def _replay_into(self, conv: Conversation, history_path: Path) -> None:
        """Replay existing messages into the Conversation. Safe on bad data."""
        try:
            raw = history_path.open("r", encoding="utf-8").read()
        except OSError as exc:
            logger.warning(
                "slot=%s history unreadable (%s) — renaming and starting fresh",
                history_path, exc,
            )
            self._rename_corrupt(history_path)
            return

        for lineno, line in enumerate(raw.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                logger.warning(
                    "channel %s history.jsonl line %d malformed, skipping",
                    history_path.parent.name, lineno,
                )
                continue
            # Append directly to _messages (bypass _append_to_history_file to
            # avoid rewriting what we just read). _truncate still enforces
            # the in-memory window.
            conv._messages.append(message)
        conv._truncate()

    def _rename_corrupt(self, history_path: Path) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        corrupt = history_path.with_name(f"{history_path.name}.corrupt-{ts}")
        try:
            history_path.rename(corrupt)
        except OSError as exc:
            logger.warning("could not rename corrupt history %s: %s", history_path, exc)
```

- [ ] **Step 4: Verify tests pass**

```bash
python -m pytest tests/test_channels.py -v
python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all 9 tests in test_channels.py pass, no regressions.

- [ ] **Step 5: Commit**

```bash
git add pal/channels.py tests/test_channels.py
git commit -m "feat(channels): ChannelStore with per-channel Conversation and replay"
```

---

## Task 5: Create `pal/scratchpad.py`

**Files:**
- Create: `pal/scratchpad.py`
- Create: `tests/test_scratchpad.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_scratchpad.py`:

```python
"""Tests for Scratchpad — per-channel free-form markdown file in the vault."""
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from pal.scratchpad import Scratchpad, ScratchpadTooLarge


@pytest.fixture
def wiki_mock():
    m = MagicMock()
    m.git_commit = MagicMock()
    return m


def test_read_returns_empty_when_file_missing(tmp_path, wiki_mock):
    sp = Scratchpad(vault_path=tmp_path, channel_id="C1",
                    wiki=wiki_mock, max_bytes=1024)
    assert sp.read() == ""


def test_write_creates_directory_and_file(tmp_path, wiki_mock):
    sp = Scratchpad(vault_path=tmp_path, channel_id="C1",
                    wiki=wiki_mock, max_bytes=1024)
    sp.write("# hello\n")

    expected_path = tmp_path / "_channels" / "C1" / "scratch.md"
    assert expected_path.exists()
    assert expected_path.read_text() == "# hello\n"


def test_write_calls_wiki_commit(tmp_path, wiki_mock):
    sp = Scratchpad(vault_path=tmp_path, channel_id="C1",
                    wiki=wiki_mock, max_bytes=1024)
    sp.write("# hello\n")
    wiki_mock.git_commit.assert_called_once()
    # Commit message includes the channel id for traceability
    args = wiki_mock.git_commit.call_args[0]
    assert "C1" in args[0]


def test_read_after_write_round_trip(tmp_path, wiki_mock):
    sp = Scratchpad(vault_path=tmp_path, channel_id="C1",
                    wiki=wiki_mock, max_bytes=1024)
    sp.write("content")
    assert sp.read() == "content"


def test_write_raises_when_over_cap(tmp_path, wiki_mock):
    sp = Scratchpad(vault_path=tmp_path, channel_id="C1",
                    wiki=wiki_mock, max_bytes=10)
    with pytest.raises(ScratchpadTooLarge) as exc_info:
        sp.write("x" * 11)
    assert "11" in str(exc_info.value)
    assert "10" in str(exc_info.value)
    # File was not touched
    assert not (tmp_path / "_channels" / "C1" / "scratch.md").exists()
    # No commit
    wiki_mock.git_commit.assert_not_called()


def test_append_adds_to_existing_content(tmp_path, wiki_mock):
    sp = Scratchpad(vault_path=tmp_path, channel_id="C1",
                    wiki=wiki_mock, max_bytes=1024)
    sp.write("line1\n")
    sp.append("line2\n")
    assert sp.read() == "line1\nline2\n"


def test_append_respects_cap(tmp_path, wiki_mock):
    sp = Scratchpad(vault_path=tmp_path, channel_id="C1",
                    wiki=wiki_mock, max_bytes=10)
    sp.write("short")
    with pytest.raises(ScratchpadTooLarge):
        sp.append(" and more bytes than allowed")
    # Contents unchanged
    assert sp.read() == "short"


def test_read_unreadable_file_returns_empty(tmp_path, wiki_mock, monkeypatch, caplog):
    import logging
    # Write the file, then mock open to raise on read
    scratch_path = tmp_path / "_channels" / "C1" / "scratch.md"
    scratch_path.parent.mkdir(parents=True)
    scratch_path.write_text("hi")

    real_open = Path.open
    def patched_open(self, *args, **kwargs):
        if self == scratch_path and "r" in (args[0] if args else kwargs.get("mode", "r")):
            raise OSError("simulated")
        return real_open(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", patched_open)

    sp = Scratchpad(vault_path=tmp_path, channel_id="C1",
                    wiki=wiki_mock, max_bytes=1024)
    with caplog.at_level(logging.WARNING):
        assert sp.read() == ""
    assert any("unreadable" in rec.message.lower() for rec in caplog.records)
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_scratchpad.py -v
```

Expected: `ModuleNotFoundError: No module named 'pal.scratchpad'`.

- [ ] **Step 3: Create `pal/scratchpad.py`**

```python
"""Per-channel scratchpad — a free-form markdown file in the vault.

Lives at <vault>/_channels/<channel_id>/scratch.md. Committed via
WikiManager on every write so history is inspectable in git. Size-capped
to prevent drift into a second wiki.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ScratchpadTooLarge(Exception):
    """Raised when a write would exceed the scratchpad size cap."""

    def __init__(self, current_bytes: int, proposed_bytes: int, max_bytes: int) -> None:
        super().__init__(
            f"scratchpad would be {proposed_bytes} bytes (cap {max_bytes}, "
            f"current {current_bytes})"
        )
        self.current_bytes = current_bytes
        self.proposed_bytes = proposed_bytes
        self.max_bytes = max_bytes


class Scratchpad:
    """File-backed free-form markdown owned by one channel."""

    def __init__(
        self,
        vault_path: Path,
        channel_id: str,
        wiki,                 # WikiManager
        max_bytes: int,
    ) -> None:
        self._vault_path = vault_path
        self._channel_id = channel_id
        self._wiki = wiki
        self._max_bytes = max_bytes

    @property
    def _path(self) -> Path:
        return self._vault_path / "_channels" / self._channel_id / "scratch.md"

    def read(self) -> str:
        """Return the scratchpad content, or empty string if missing/unreadable."""
        path = self._path
        if not path.exists():
            return ""
        try:
            return path.open("r", encoding="utf-8").read()
        except OSError as exc:
            logger.warning(
                "scratchpad %s unreadable (%s) — treating as empty",
                path, exc,
            )
            return ""

    def write(self, content: str) -> None:
        """Replace scratchpad content. Raises ScratchpadTooLarge if over cap."""
        size = len(content.encode("utf-8"))
        if size > self._max_bytes:
            raise ScratchpadTooLarge(
                current_bytes=len(self.read().encode("utf-8")),
                proposed_bytes=size,
                max_bytes=self._max_bytes,
            )
        path = self._path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        try:
            self._wiki.git_commit(f"scratch: update {self._channel_id}")
        except Exception as exc:
            logger.warning(
                "scratchpad git commit failed for %s: %s",
                self._channel_id, exc,
            )

    def append(self, text: str) -> None:
        """Append text to the scratchpad. Raises ScratchpadTooLarge if resulting size over cap."""
        combined = self.read() + text
        self.write(combined)
```

- [ ] **Step 4: Verify tests pass**

```bash
python -m pytest tests/test_scratchpad.py -v
python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/scratchpad.py tests/test_scratchpad.py
git commit -m "feat(scratchpad): per-channel scratch.md with size cap and git commit"
```

---

## Task 6: Inject scratchpad into system prompt

**Files:**
- Modify: `pal/prompt_builder.py`
- Modify: `tests/test_prompt_builder.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_prompt_builder.py`:

```python
def test_build_with_scratchpad_renders_section():
    from unittest.mock import MagicMock
    from pal.prompt_builder import SystemPromptBuilder
    profile = MagicMock()
    profile.read.return_value = ""
    wisdom = MagicMock()
    wisdom.bodies.return_value = []
    builder = SystemPromptBuilder(profile=profile, wisdom=wisdom)

    prompt = builder.build(channel_scratchpad="Phase 2 in progress")
    assert "Phase 2 in progress" in prompt
    assert "Channel Scratchpad" in prompt  # section header


def test_build_empty_scratchpad_omits_section():
    from unittest.mock import MagicMock
    from pal.prompt_builder import SystemPromptBuilder
    profile = MagicMock()
    profile.read.return_value = ""
    wisdom = MagicMock()
    wisdom.bodies.return_value = []
    builder = SystemPromptBuilder(profile=profile, wisdom=wisdom)

    prompt = builder.build(channel_scratchpad="")
    assert "Channel Scratchpad" not in prompt


def test_build_none_scratchpad_omits_section():
    from unittest.mock import MagicMock
    from pal.prompt_builder import SystemPromptBuilder
    profile = MagicMock()
    profile.read.return_value = ""
    wisdom = MagicMock()
    wisdom.bodies.return_value = []
    builder = SystemPromptBuilder(profile=profile, wisdom=wisdom)

    prompt = builder.build()  # no arg
    assert "Channel Scratchpad" not in prompt


def test_build_scratchpad_appears_between_wisdom_and_commands():
    """Scratchpad section sits between wisdom and commands, not before either."""
    from unittest.mock import MagicMock
    from pal.prompt_builder import SystemPromptBuilder
    profile = MagicMock()
    profile.read.return_value = ""
    wisdom = MagicMock()
    wisdom.bodies.return_value = ["some wisdom"]
    builder = SystemPromptBuilder(profile=profile, wisdom=wisdom)

    prompt = builder.build(channel_scratchpad="scratch content")
    wisdom_idx = prompt.find("Active Wisdom")
    scratch_idx = prompt.find("Channel Scratchpad")
    commands_idx = prompt.find("Available Commands")

    assert wisdom_idx < scratch_idx < commands_idx
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_prompt_builder.py -v -k scratchpad
```

Expected: FAIL — `build()` doesn't accept the kwarg, or section not rendered.

- [ ] **Step 3: Update `SystemPromptBuilder.build`**

In `pal/prompt_builder.py`, change `build()`:

```python
    def build(self, channel_scratchpad: str | None = None) -> str:
        """Compose the current system prompt from base + profile + wisdom + scratchpad."""
        sections = [BASE_PROMPT]

        profile_body = self.profile.read()
        if profile_body:
            sections.append(f"## About the User\n\n{profile_body}")

        wisdom_bodies = self.wisdom.bodies()
        if wisdom_bodies:
            wisdom_text = "\n".join(f"- {body}" for body in wisdom_bodies)
            sections.append(f"## Active Wisdom\n\n{wisdom_text}")

        if channel_scratchpad:
            sections.append(f"## Channel Scratchpad\n\n{channel_scratchpad}")

        from pal.commands import COMMANDS
        cmd_lines = [f"- `/{c.name} {c.args}`".rstrip() + f" - {c.description}"
                     for c in COMMANDS]
        sections.append(
            "## Available Commands\n\n"
            "The user can invoke these slash commands (they appear as `!cmd` "
            "in Discord). When the user asks what commands exist, cite from "
            "this list verbatim.\n\n"
            + "\n".join(cmd_lines)
        )

        return "\n\n".join(sections)
```

- [ ] **Step 4: Verify tests pass**

```bash
python -m pytest tests/test_prompt_builder.py -v
python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/prompt_builder.py tests/test_prompt_builder.py
git commit -m "feat(prompt): inject channel_scratchpad section between wisdom and commands"
```

---

## Task 7: Thread `channel_id` through `PalClient`

**Files:**
- Modify: `pal/client.py`
- Modify: `tests/test_client.py` (create if missing)

- [ ] **Step 1: Write failing tests**

Create or extend `tests/test_client.py`:

```python
"""Tests for PalClient protocol message construction."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path


@pytest.mark.asyncio
async def test_chat_sends_channel_id_when_provided():
    """Confirm the ChatMessage wire payload includes channel_id."""
    from pal.client import PalClient
    client = PalClient(socket_path=Path("/tmp/unused.sock"))
    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    writer.is_closing = MagicMock(return_value=False)
    client._writer = writer

    reader = MagicMock()
    # Return an empty line so the generator terminates immediately.
    reader.readline = AsyncMock(return_value=b"")
    client._reader = reader

    async for _ in client.chat("hello", channel_id="C1"):
        pass

    written = writer.write.call_args_list[0][0][0]  # bytes
    import json
    payload = json.loads(written.decode("utf-8").strip())
    assert payload["text"] == "hello"
    assert payload["channel_id"] == "C1"


@pytest.mark.asyncio
async def test_chat_defaults_channel_id_to_none():
    from pal.client import PalClient
    client = PalClient(socket_path=Path("/tmp/unused.sock"))
    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    writer.is_closing = MagicMock(return_value=False)
    client._writer = writer
    reader = MagicMock()
    reader.readline = AsyncMock(return_value=b"")
    client._reader = reader

    async for _ in client.chat("hello"):
        pass

    import json
    payload = json.loads(writer.write.call_args_list[0][0][0].decode("utf-8").strip())
    assert payload["channel_id"] is None


@pytest.mark.asyncio
async def test_command_sends_channel_id_when_provided():
    from pal.client import PalClient
    from pal.protocol import ResponseMessage, encode_message
    client = PalClient(socket_path=Path("/tmp/unused.sock"))
    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    writer.is_closing = MagicMock(return_value=False)
    client._writer = writer

    reader = MagicMock()
    reader.readline = AsyncMock(return_value=encode_message(ResponseMessage(text="ok")))
    client._reader = reader

    await client.command("note", "hello there", channel_id="C1")

    import json
    payload = json.loads(writer.write.call_args_list[0][0][0].decode("utf-8").strip())
    assert payload["name"] == "note"
    assert payload["channel_id"] == "C1"
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_client.py -v
```

Expected: FAIL — `chat()` and `command()` don't accept `channel_id` kwarg.

- [ ] **Step 3: Add `channel_id` kwarg to `PalClient.chat` and `PalClient.command`**

In `pal/client.py`, update both method signatures:

```python
    async def chat(self, text: str, *, channel_id: str | None = None) -> AsyncGenerator[Message, None]:
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
```

Update `command` similarly — look at the existing signature, add `*, channel_id: str | None = None` before the return type, and pass it into the `CommandMessage(...)` constructor.

- [ ] **Step 4: Verify tests pass**

```bash
python -m pytest tests/test_client.py -v
python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all client tests pass, no regressions in downstream tests.

- [ ] **Step 5: Commit**

```bash
git add pal/client.py tests/test_client.py
git commit -m "feat(client): thread channel_id through chat() and command()"
```

---

## Task 8: CLI sends `channel_id="cli-default"`

**Files:**
- Modify: `pal/cli.py`

- [ ] **Step 1: Find existing CLI chat / command calls**

Read `pal/cli.py`. Find every call to `client.chat(...)` and `client.command(...)`. There are typically two or three: the main REPL loop plus any one-shot invocations.

- [ ] **Step 2: Update the calls**

Every `client.chat(text)` becomes `client.chat(text, channel_id="cli-default")`. Every `client.command(name, args)` becomes `client.command(name, args, channel_id="cli-default")`.

Alternative: define a module-level constant at the top of `cli.py`:

```python
CLI_CHANNEL_ID = "cli-default"
```

and use it at each call site. Do that to keep the value in one place.

- [ ] **Step 3: Run existing CLI tests**

```bash
python -m pytest tests/ -q -k cli 2>&1 | tail -5
```

Expected: existing CLI tests still pass. If any test instantiates a mock writer and inspects the written ChatMessage, it may now see `channel_id="cli-default"` instead of None — update the assertion.

- [ ] **Step 4: Smoke-test manually (optional)**

If `.venv` is set up:

```bash
python -m pal.cli --help
```

Expected: CLI starts without errors.

- [ ] **Step 5: Commit**

```bash
git add pal/cli.py
git commit -m "feat(cli): send channel_id=cli-default for all chat and command calls"
```

---

## Task 9: Discord adapter forwards `message.channel.id`

**Files:**
- Modify: `pal/discord_adapter.py`
- Modify: `tests/test_discord_adapter.py` (if it exists; if not, extend whatever test file covers the bridge)

- [ ] **Step 1: Locate the message handler**

Read `pal/discord_adapter.py`. Find `on_message` (likely a method on the bot class that calls `client.chat(...)`). Find any slash-command handlers that call `client.command(...)`.

- [ ] **Step 2: Write failing tests**

Check which test file covers this. Likely `tests/test_discord_adapter.py` or `tests/test_discord_interactions.py`. Extend whichever exists; if neither, create `tests/test_discord_adapter_channel.py`.

Append a test that constructs a fake Discord message and asserts the call forwards channel_id:

```python
@pytest.mark.asyncio
async def test_on_message_forwards_channel_id_to_client():
    """Bridge passes message.channel.id through to client.chat as a string."""
    from unittest.mock import AsyncMock, MagicMock
    from pal.discord_adapter import UserConnectionManager

    fake_client = AsyncMock()
    async def fake_chat(text, *, channel_id=None):
        yield  # empty generator
    fake_client.chat = fake_chat

    manager = UserConnectionManager(
        allowed_users={"user123"},
        socket_path="/tmp/unused.sock",
    )
    # Substitute the user's client with our fake.
    manager._clients["user123"] = fake_client

    # Build a fake message object.
    fake_message = MagicMock()
    fake_message.author.id = 12345  # note: Discord gives int
    fake_message.channel.id = 99999
    fake_message.content = "hi"

    # The actual call path depends on the bridge's handler structure.
    # Replace this with however on_message is invoked in a test context.
    # For example:
    # bot = PalBot(allowed_users={"user123"}, socket_path="...")
    # bot.connections._clients["user123"] = fake_client
    # await bot.on_message(fake_message)
    # assert fake_chat_calls[0]["channel_id"] == "99999"
```

Because the discord bridge has many moving parts (decorators, discord.py event registration), writing a clean unit test here may require more scaffolding than other tasks. Use whatever fixture/mock pattern the existing discord tests use. If the existing tests mock at the `client.chat` call level, do the same.

If no clean unit test can be written without refactoring, document an integration test manually in Step 5 and move forward.

- [ ] **Step 3: Update `on_message` handler**

In `pal/discord_adapter.py`, find the place where `client.chat(message.content)` is called inside `on_message`. Change to:

```python
client.chat(message.content, channel_id=str(message.channel.id))
```

Do the same for any slash-command dispatch paths. For a slash command handler that already has access to `interaction.channel_id` or equivalent, pass `channel_id=str(interaction.channel_id)`.

- [ ] **Step 4: Run tests and smoke-verify**

```bash
python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: no regressions. New test (if added) passes.

- [ ] **Step 5: Commit**

```bash
git add pal/discord_adapter.py tests/test_discord_adapter*.py
git commit -m "feat(discord): forward channel.id as channel_id to daemon"
```

---

## Task 10: Daemon routes messages via `ChannelStore` and injects scratchpad

**Files:**
- Modify: `pal/daemon.py`
- Modify: `tests/test_daemon.py` (or create `tests/test_daemon_channels.py`)

This is the biggest task: replaces the per-connection `Conversation` with per-message `ChannelStore` dispatch, and wires scratchpad reads into the system-prompt build.

- [ ] **Step 1: Write failing tests**

Create `tests/test_daemon_channels.py`:

```python
"""Tests for per-channel routing in the daemon."""
import asyncio
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_two_channels_get_separate_conversations(tmp_path):
    """Messages on C1 and C2 populate different Conversation instances."""
    from pal.channels import ChannelStore

    store = ChannelStore(channels_dir=tmp_path, history_depth=50)
    conv1 = await store.get_or_create("C1")
    conv1.add_user("hello from C1")
    conv2 = await store.get_or_create("C2")
    conv2.add_user("hello from C2")

    assert conv1.messages != conv2.messages
    assert conv1.messages[0]["content"] == "hello from C1"
    assert conv2.messages[0]["content"] == "hello from C2"


@pytest.mark.asyncio
async def test_channel_id_none_falls_back_to_cli_default(tmp_path):
    """Daemon helper that resolves channel_id uses 'cli-default' for None."""
    from pal.daemon import resolve_channel_id
    assert resolve_channel_id(None) == "cli-default"
    assert resolve_channel_id("") == "cli-default"
    assert resolve_channel_id("C1") == "C1"


@pytest.mark.asyncio
async def test_channel_id_invalid_falls_back_to_cli_default_with_log(tmp_path, caplog):
    """Invalid channel_id substrings fall back with a warning."""
    import logging
    from pal.daemon import resolve_channel_id
    with caplog.at_level(logging.WARNING):
        resolved = resolve_channel_id("../evil")
    assert resolved == "cli-default"
    assert any("invalid" in rec.message.lower() for rec in caplog.records)


@pytest.mark.asyncio
async def test_daemon_restart_replays_history(tmp_path):
    """Simulate restart: create store, drop it, create a new store on same dir."""
    from pal.channels import ChannelStore

    store1 = ChannelStore(channels_dir=tmp_path, history_depth=50)
    conv1 = await store1.get_or_create("C1")
    conv1.add_user("turn 1")
    conv1.add_assistant("turn 2")

    # Simulate daemon restart: drop the store, make a new one.
    del store1
    store2 = ChannelStore(channels_dir=tmp_path, history_depth=50)
    conv2 = await store2.get_or_create("C1")

    assert len(conv2.messages) == 2
    assert conv2.messages[0]["content"] == "turn 1"
    assert conv2.messages[1]["content"] == "turn 2"
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_daemon_channels.py -v
```

Expected: FAIL — `resolve_channel_id` doesn't exist yet.

- [ ] **Step 3: Add `resolve_channel_id` helper in `pal/daemon.py`**

At module level in `pal/daemon.py` (alongside other helpers, not inside a class):

```python
from pal.channels import validate_channel_id

CLI_DEFAULT_CHANNEL = "cli-default"


def resolve_channel_id(raw: str | None) -> str:
    """Return a safe, in-range channel_id.

    None or empty -> CLI_DEFAULT_CHANNEL (the backward-compat fallback).
    Invalid characters -> logged warning, falls back to CLI_DEFAULT_CHANNEL.
    Valid -> returned as-is.
    """
    if not raw:
        return CLI_DEFAULT_CHANNEL
    if not validate_channel_id(raw):
        logger.warning(
            "invalid channel_id %r received; falling back to %s",
            raw, CLI_DEFAULT_CHANNEL,
        )
        return CLI_DEFAULT_CHANNEL
    return raw
```

- [ ] **Step 4: Construct `ChannelStore` on the `Daemon`**

In `Daemon.__init__` (or the equivalent setup spot), add:

```python
from pal.channels import ChannelStore

self.channel_store = ChannelStore(
    channels_dir=self.config.channels_dir,
    history_depth=self.config.history_depth,
)
```

And ensure `self.config.channels_dir` exists on disk:

```python
self.config.channels_dir.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 5: Replace per-connection `Conversation` in `_handle_connection`**

Find this line in `_handle_connection`:

```python
conv = Conversation(history_depth=self.config.history_depth)
```

Remove it. The connection no longer owns a single conversation; each incoming message looks up the right one via `ChannelStore`.

Pass `self.channel_store` into wherever `conv` was being used. Specifically:
- `_handle_chat` currently takes a `conv` parameter — change it to take `channel_id` instead (resolved via `resolve_channel_id`).
- `_handle_command` currently takes `conv` — same treatment.

Update the message-dispatch block (`elif isinstance(msg, ChatMessage):` and `elif isinstance(msg, CommandMessage):`) to resolve the channel_id from the message and pass it through:

```python
elif isinstance(msg, ChatMessage):
    channel_id = resolve_channel_id(msg.channel_id)
    conv = await self.channel_store.get_or_create(channel_id)
    # ... existing guard against in-flight chat ...
    current_chat_task = asyncio.create_task(
        self._handle_chat(msg, conv, channel_id, writer, tool_executor, scanner)
    )
elif isinstance(msg, CommandMessage):
    channel_id = resolve_channel_id(msg.channel_id)
    conv = await self.channel_store.get_or_create(channel_id)
    # ... existing guard ...
    await self._handle_command(
        msg, conv, channel_id, writer, approval_registry, emit_proposal,
    )
```

Update `_handle_chat` signature to accept `channel_id`:

```python
async def _handle_chat(
    self,
    msg: ChatMessage,
    conv: Conversation,
    channel_id: str,
    writer: asyncio.StreamWriter,
    tool_executor,
    scanner,
) -> None:
```

- [ ] **Step 6: Wire scratchpad into the system prompt**

Near the top of `_handle_chat`, after `conv.add_user(msg.text)`, build the scratchpad instance and read it. Add the import at the module top:

```python
from pal.scratchpad import Scratchpad
```

In `_handle_chat`:

```python
scratchpad = Scratchpad(
    vault_path=self.config.vault_path,
    channel_id=channel_id,
    wiki=self.wiki,
    max_bytes=self.config.scratchpad_max_bytes,
)
scratchpad_content = scratchpad.read()

# ... existing:
# mode = decide_mode(conv)
messages = conv.get_messages_for_api(
    system_prompt=self.prompt_builder.build(channel_scratchpad=scratchpad_content),
)
```

Pass `scratchpad` into the `tool_executor` so the `update_scratch` tool (added next task) can reach it. Either set it on `tool_executor` as an attribute (`tool_executor.scratchpad = scratchpad`) or pass it through the tool invocation path. Add an attribute on `ToolExecutor.__init__` in `pal/tools.py` — see Task 11.

- [ ] **Step 7: Run tests**

```bash
python -m pytest tests/test_daemon_channels.py -v
python -m pytest tests/test_daemon.py -v 2>&1 | tail -20
python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: new channel tests pass. Existing daemon tests may need updates if any construct `Conversation` directly and call `_handle_chat` with the old signature. Update them to provide a `channel_id` positional arg.

- [ ] **Step 8: Commit**

```bash
git add pal/daemon.py tests/test_daemon_channels.py tests/test_daemon.py
git commit -m "feat(daemon): route messages by channel_id via ChannelStore, inject scratchpad"
```

---

## Task 11: Add `update_scratch` tool

**Files:**
- Modify: `pal/tools.py`
- Modify: `tests/test_tools.py`

- [ ] **Step 1: Add `scratchpad` attribute to `ToolExecutor`**

In `pal/tools.py`, find `ToolExecutor.__init__`. Add an optional keyword arg:

```python
def __init__(
    self,
    ...,
    scratchpad=None,  # Scratchpad | None — injected per-turn by daemon
):
    ...
    self.scratchpad = scratchpad
```

In `pal/daemon.py`'s `_handle_chat`, assign it just before the tool loop runs:

```python
tool_executor.scratchpad = scratchpad
```

- [ ] **Step 2: Write failing tests**

Append to `tests/test_tools.py`:

```python
@pytest.mark.asyncio
async def test_update_scratch_writes_content(tmp_path):
    from unittest.mock import MagicMock
    from pal.scratchpad import Scratchpad
    from pal.tools import ToolExecutor
    import json as _json

    wiki = MagicMock()
    wiki.git_commit = MagicMock()
    sp = Scratchpad(vault_path=tmp_path, channel_id="C1",
                    wiki=wiki, max_bytes=1024)
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        wiki=wiki,
        scratchpad=sp,
    )

    result = await executor.run_async("update_scratch", {"content": "new notes"})
    assert "updated" in result.lower() or "ok" in result.lower()
    assert sp.read() == "new notes"


@pytest.mark.asyncio
async def test_update_scratch_returns_error_on_oversize(tmp_path):
    from unittest.mock import MagicMock
    from pal.scratchpad import Scratchpad
    from pal.tools import ToolExecutor

    wiki = MagicMock()
    wiki.git_commit = MagicMock()
    sp = Scratchpad(vault_path=tmp_path, channel_id="C1",
                    wiki=wiki, max_bytes=10)
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        wiki=wiki,
        scratchpad=sp,
    )

    result = await executor.run_async(
        "update_scratch", {"content": "x" * 20}
    )
    assert "error" in result.lower() or "too large" in result.lower()
    # File not touched
    assert sp.read() == ""


@pytest.mark.asyncio
async def test_update_scratch_without_scratchpad_errors(tmp_path):
    """If executor wasn't given a scratchpad, tool returns a clear error."""
    from pal.tools import ToolExecutor
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        wiki=None,
        scratchpad=None,
    )
    result = await executor.run_async("update_scratch", {"content": "x"})
    assert "scratchpad" in result.lower() and "not" in result.lower()
```

- [ ] **Step 3: Run tests — verify they fail**

```bash
python -m pytest tests/test_tools.py -v -k update_scratch
```

Expected: FAIL — `Unknown tool: update_scratch`.

- [ ] **Step 4: Add tool spec to `TOOL_DEFINITIONS`**

In `pal/tools.py`, add to the `TOOL_DEFINITIONS` list:

```python
{
    "type": "function",
    "function": {
        "name": "update_scratch",
        "description": (
            "Replace the scratchpad contents for the current channel. "
            "Use this to record short-term project state, current decisions, "
            "or context you want to remember on the next turn. The scratchpad "
            "is automatically included in your system prompt on every turn in "
            "this channel. Content must be 2048 bytes or less. Calling this "
            "REPLACES the scratchpad wholesale -- prior content is discarded "
            "unless you include it in the new content."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": (
                        "New scratchpad content. Markdown is fine. Keep it "
                        "terse -- it's working state, not a wiki article."
                    ),
                },
            },
            "required": ["content"],
        },
    },
},
```

- [ ] **Step 5: Add dispatch branch in `run_async`**

In `pal/tools.py`'s `run_async`, add:

```python
if name == "update_scratch":
    return await self._update_scratch(arguments)
```

- [ ] **Step 6: Add handler**

In `pal/tools.py`:

```python
async def _update_scratch(self, arguments: dict) -> str:
    content = arguments.get("content", "")
    if self.scratchpad is None:
        return "Error: scratchpad not configured for this session."
    try:
        self.scratchpad.write(content)
    except ScratchpadTooLarge as exc:
        return (
            f"Error: scratchpad too large. Proposed {exc.proposed_bytes} bytes, "
            f"cap is {exc.max_bytes}. Prune or summarize and retry."
        )
    return f"Scratchpad updated ({len(content)} bytes)."
```

Add the import at the top of `pal/tools.py`:

```python
from pal.scratchpad import ScratchpadTooLarge
```

- [ ] **Step 7: Verify tests pass**

```bash
python -m pytest tests/test_tools.py -v -k update_scratch
python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all new tests pass, no regressions.

- [ ] **Step 8: Update BASE_PROMPT to mention the tool**

In `pal/prompt_builder.py`, the `## Your tools` section lists current tools. Add a bullet for `update_scratch` under a new subsection "Channel-scoped state":

```
Channel-scoped state:
- update_scratch: replace the scratchpad for this channel (terse, <=2 KB). Use to record working project state you want to remember next turn. Automatically included in your system prompt.
```

- [ ] **Step 9: Commit**

```bash
git add pal/tools.py tests/test_tools.py pal/prompt_builder.py pal/daemon.py
git commit -m "feat(tools): update_scratch tool with size cap and BASE_PROMPT update"
```

---

## Task 12: Add `/note` slash command

**Files:**
- Modify: `pal/commands.py`
- Modify: `pal/daemon.py`
- Modify: `tests/test_daemon.py` (or a new `tests/test_note_command.py`)

- [ ] **Step 1: Write failing tests**

Create `tests/test_note_command.py`:

```python
"""Tests for the /note slash command."""
import asyncio
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_note_appends_to_scratchpad(tmp_path):
    """`/note some text` appends to the current channel's scratchpad."""
    from pal.scratchpad import Scratchpad
    from pal.daemon import handle_note

    wiki = MagicMock()
    wiki.git_commit = MagicMock()
    sp = Scratchpad(vault_path=tmp_path, channel_id="C1",
                    wiki=wiki, max_bytes=1024)
    sp.write("existing content\n")

    result = await handle_note(scratchpad=sp, text="new observation")
    assert "added" in result.lower() or "appended" in result.lower()

    content = sp.read()
    assert "existing content" in content
    assert "new observation" in content


@pytest.mark.asyncio
async def test_note_returns_error_on_oversize(tmp_path):
    from pal.scratchpad import Scratchpad
    from pal.daemon import handle_note

    wiki = MagicMock()
    wiki.git_commit = MagicMock()
    sp = Scratchpad(vault_path=tmp_path, channel_id="C1",
                    wiki=wiki, max_bytes=20)
    sp.write("1234567890\n")  # 11 bytes

    # Appending something longer than remaining space should fail.
    result = await handle_note(scratchpad=sp, text="this is way too long to fit")
    assert "error" in result.lower() or "too large" in result.lower()

    # Content unchanged.
    assert sp.read() == "1234567890\n"


@pytest.mark.asyncio
async def test_note_empty_text_returns_usage(tmp_path):
    from pal.scratchpad import Scratchpad
    from pal.daemon import handle_note
    wiki = MagicMock()
    sp = Scratchpad(vault_path=tmp_path, channel_id="C1",
                    wiki=wiki, max_bytes=1024)
    result = await handle_note(scratchpad=sp, text="")
    assert "usage" in result.lower() or "empty" in result.lower()
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_note_command.py -v
```

Expected: FAIL — `handle_note` doesn't exist.

- [ ] **Step 3: Add `/note` to the command registry**

In `pal/commands.py`, find the `COMMANDS` list (or registry). Add:

```python
Command(
    name="note",
    args="<text>",
    description="Append a note to this channel's scratchpad.",
),
```

(Adapt the Command constructor shape to what the module uses.)

- [ ] **Step 4: Add `handle_note` and `_handle_note` to daemon**

In `pal/daemon.py`, add a module-level async function for testability:

```python
from pal.scratchpad import ScratchpadTooLarge


async def handle_note(scratchpad, text: str) -> str:
    """Append a timestamped note to the given scratchpad. Returns user-facing message."""
    from datetime import datetime, timezone
    text = text.strip()
    if not text:
        return "Usage: /note <text>. Appends a line to the channel scratchpad."
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    appended = f"- {ts}: {text}\n"
    try:
        scratchpad.append(appended)
    except ScratchpadTooLarge as exc:
        return (
            f"Error: note would push scratchpad over {exc.max_bytes} bytes. "
            "Prune the scratchpad (edit in Obsidian or call update_scratch) and retry."
        )
    return f"Note added ({len(appended)} bytes)."
```

In `Daemon._handle_command`, add a branch for `"note"`:

```python
if msg.name == "note":
    channel_id = resolve_channel_id(msg.channel_id)
    scratchpad = Scratchpad(
        vault_path=self.config.vault_path,
        channel_id=channel_id,
        wiki=self.wiki,
        max_bytes=self.config.scratchpad_max_bytes,
    )
    text = await handle_note(scratchpad=scratchpad, text=msg.args)
    resp = ResponseMessage(text=text, command="note")
    writer.write(encode_message(resp))
    await writer.drain()
    return
```

The exact placement depends on the existing `_handle_command` structure — follow the pattern used by other slash commands like `/model`.

- [ ] **Step 5: Verify tests pass**

```bash
python -m pytest tests/test_note_command.py -v
python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add pal/commands.py pal/daemon.py tests/test_note_command.py
git commit -m "feat(commands): /note slash command to append to channel scratchpad"
```

---

## Task 13: End-to-end manual validation

**Files:** none (manual validation).

- [ ] **Step 1: Restart the daemon on agenthost** so it picks up the new code.

- [ ] **Step 2: Post in Discord channel A** (e.g., `#gdb-mcp`)

Send something substantive that PAL will want to remember, like: "We just decided to use FastMCP for the gateway. Remember that."

Expected: PAL acknowledges. May or may not call `update_scratch`. If it does, you'll see a vault commit `scratch: update <channel-id>`.

- [ ] **Step 3: Check the scratchpad on disk**

On agenthost:

```bash
find /mnt/secondary/PAL/vault/_channels/ -name scratch.md -exec ls -la {} \;
```

Or in Obsidian, navigate to `_channels/<channel_id>/scratch.md`. Verify the file exists for the channel you posted in.

- [ ] **Step 4: Post in Discord channel B** (a different channel)

Say something unrelated. Expected: PAL responds fresh, does NOT reference channel A's context.

- [ ] **Step 5: Check the second channel's scratchpad (if PAL wrote one)**

Should be a separate file from step 3.

- [ ] **Step 6: Use `/note` manually**

In Discord (whichever channel), send `/note Today we confirmed per-channel scoping works`.

Expected: PAL replies "Note added". Scratchpad in that channel now includes the line. `git log -p -1 <vault>/_channels/<id>/scratch.md` shows the commit.

- [ ] **Step 7: Restart the daemon mid-session**

```bash
sudo systemctl restart pal-daemon.service  # or however you restart PAL
```

Then in channel A, continue the conversation from Step 2. Expected: PAL references what was discussed before the restart (conversation replayed from history.jsonl).

Check:
```bash
find ~/.local/share/pal/channels -name history.jsonl | xargs ls -la
```

Verify history files exist per channel.

- [ ] **Step 8: Test the size cap**

Try sending `/note` with a very long line repeatedly until you exceed 2 KB. Expected: user-visible error explaining the cap, no partial write. Alternately, ask PAL to dump a large amount into the scratchpad and confirm it gets the tool-level error and responds sensibly.

- [ ] **Step 9: No commit.**

Document observations, anything surprising, perf hiccups, etc., in a follow-up note or an addendum to this plan.

---

## Appendix: Observations recorded during execution

Reserve this section for issues found during implementation that deserve follow-up but shouldn't block.
