# agent_core Extraction Phase D Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move PAL's per-channel state modules (`conversation`, `channels`, `scratchpad`) and `learning_scanner` into `agent_core`, split `pal.protocol` into a generic transport layer in `agent_core` and a slimmed PAL-specific subset in PAL, tag `agent_core@v0.4.0`, migrate PAL to consume the new tag, and run a one-time server-side data migration to put existing channel data under per-agent subdirectories.

**Architecture:** Same two-repo split as Phases A/B/C. agent_core grows a `protocol/` package (transport machinery + generic message primitives + `LearningCandidateProposalMessage`), four new top-level modules (`conversation`, `channels`, `scratchpad`, `learning_scanner`), and a `git_helpers` module. PAL's `pal/protocol.py` becomes a thin module containing only PAL-specific proposal dataclasses that register with agent_core's registry. PAL keeps a local `Message` union for backwards compatibility with its `decode_message` callers. PAL's existing channel data on the server moves from `<vault>/_channels/<id>/` to `<vault>/_channels/pal/<id>/` via a one-time migration script.

**Tech Stack:** Python 3.12+, hatchling, pytest, GitHub Actions CI, git tags. No new agent_core or PAL runtime/dev deps.

**Repos involved:**
- agent_core: `/home/edible/Projects/agent_core` (existing; currently at `v0.3.0`)
- PAL: `/home/edible/Projects/PAL` (main checkout). PAL-side migration work happens in a feature-branch worktree at `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d`.

**Reference:** spec at `docs/superpowers/specs/2026-04-28-phase-d-per-channel-state-design.md`. Builds on Phase A (PR #1, merged 2026-04-26), Phase B (PR #2, merged 2026-04-27), and Phase C (PR #3, merged 2026-04-27).

---

## Pre-flight: Inventory of touched code

Mapped during planning. Use this as the migration target list.

### Production imports of moving modules

| Module | PAL source consumers |
|---|---|
| `pal.conversation` | `pal/channels.py:18`, `pal/daemon.py:18` |
| `pal.channels` | `pal/daemon.py:19`, plus a docstring reference in `pal/conversation.py:5` |
| `pal.scratchpad` | `pal/daemon.py:20`, `pal/tools.py:18` |
| `pal.learning_scanner` | `pal/daemon.py:264` |

### Production imports of `pal.protocol`

Generic primitives (move to `agent_core.protocol`):
- `pal/client.py:9-20` (Chat/Command/StreamChunk/Response/Error/ToolProgress/Message/STREAM_BUFFER_LIMIT/encode/decode)
- `pal/daemon.py:50-63` (Chat/Command/StreamChunk/Response/Error/ToolProgress + PAL-specific + Message/STREAM_BUFFER_LIMIT/encode/decode; mixed)
- `pal/discord_adapter.py:23-29` (StreamChunk/Response/Error/ToolProgress + PAL-specific; mixed)
- `pal/discord_interactions.py:444-450` (Error/Message/Response/StreamChunk/ToolProgress)
- `pal/cli.py:20-33` (StreamChunk/Response/Error/ToolProgress/Message + PAL-specific; mixed)
- `pal/learning_scanner.py:157` (LearningCandidateProposalMessage)

PAL-specific (stay in `pal.protocol`):
- `pal/categorizer.py:10` (BatchFallbackProposal)
- `pal/discord_interactions.py:19-29` (all PAL-specific proposal types)
- `pal/tools.py:945, 1066, 1183, 1251, 1390` (Research/Compile/Reorg/Promote/Consolidate proposals)
- `pal/daemon.py:1338` (BatchFallbackProposal local import)

### `reasoning_override` usages

| File:Line | Operation |
|---|---|
| `pal/conversation.py:21` | Field definition (deleted in this phase; replaced by `overrides` in agent_core.Conversation) |
| `pal/daemon.py:598` | Read: `reasoning_label = conv.reasoning_override or "auto"` |
| `pal/daemon.py:1955` | Write: `conv.reasoning_override = "on"` |
| `pal/daemon.py:1964` | Write: `conv.reasoning_override = "off"` |
| `pal/daemon.py:1973` | Write: `conv.reasoning_override = None` |
| `pal/daemon.py:1989` | Read: `text=f"Reasoning mode: {conv.reasoning_override or 'auto'} (effective: {mode})"` |
| `tests/test_conversation.py:104, 109, 110, 115, 116, 117` | Read/write in tests |

### Construction call sites (production)

- `ChannelStore`: `pal/daemon.py:189-192` (passes `channels_dir`, `history_depth` — needs change to `vault_path`, `agent_name`, `history_depth`)
- `Scratchpad`: `pal/daemon.py:436-440` and `pal/daemon.py:656-660` (passes `vault_path, channel_id, wiki, max_bytes` — needs change to `vault_path, agent_name, channel_id, max_bytes, commit_callback`)
- `LearningScanner`: `pal/daemon.py:284-288` (no signature change)
- `Conversation`: `pal/channels.py:51-54` (going away with channels.py)

### Test files involved (35 files)

- Per-module: `tests/test_conversation.py`, `tests/test_channels.py`, `tests/test_daemon_channels.py`, `tests/test_scratchpad.py`, `tests/test_scratch_command.py`, `tests/test_learning_scanner.py`, `tests/test_learning_scanner_orchestrator.py`, `tests/test_learning_scanner_extract.py`, `tests/test_learning_scanner_dedupe.py`, `tests/test_learning_scanner_prefilter.py`, `tests/test_daemon_scanner_hook.py`, `tests/test_daemon_scanner_approval.py`, `tests/test_scanner_take_pending.py`, `tests/test_learning_e2e.py` (flaky-skipped)
- Protocol: `tests/test_protocol.py`, `tests/test_protocol_promote_proposal.py`, `tests/test_protocol_learning_candidate.py`, `tests/test_batch_fallback_proposal.py`
- Touched indirectly: `tests/test_client.py`, `tests/test_daemon.py` (flaky-skipped), `tests/test_chat_research_integration.py` (flaky-skipped), `tests/test_chat_compile_tools.py`, `tests/test_chat_reorg_tools.py`, `tests/test_cli_research_proposal.py`, `tests/test_cli_batch_fallback.py`, `tests/test_discord_adapter.py`, `tests/test_discord_interactions.py`, `tests/test_discord_learning_candidate.py`, `tests/test_discord_promote_proposal.py`, `tests/test_integration.py` (flaky-skipped), `tests/test_learning_commands.py`, `tests/test_prompt_injection.py`, `tests/test_tools.py`, `tests/test_wiki_commands.py`

The five known-flaky integration tests (per project memory) stay ignored in broad runs: `tests/test_daemon.py`, `tests/test_integration.py`, `tests/test_chat_research_integration.py`, `tests/test_consolidate_integration.py`, `tests/test_learning_e2e.py`.

---

# Part 1: agent_core changes (target: v0.4.0)

Working directory throughout Part 1: `/home/edible/Projects/agent_core`. Use `.venv/bin/pytest`.

## Task 1: Pre-flight on agent_core

**Files:**
- None modified.

- [ ] **Step 1: Fetch and confirm clean main**

```bash
cd /home/edible/Projects/agent_core
git fetch origin
git checkout main
git pull
git status
```

Expected: clean working tree on `main`. HEAD matches Phase C tag (`v0.3.0`) or whatever was last pushed.

- [ ] **Step 2: Create feature branch**

```bash
git checkout -b feature/phase-d-per-channel-state
```

Expected: branched from current main.

- [ ] **Step 3: Verify Phase A/B/C modules are present**

```bash
ls agent_core/utils/
ls agent_core/
```

Expected: `agent_core/utils/` contains the Phase A leaf utilities; `agent_core/` contains the Phase B clients (`reasoning.py`, `inference.py`, `retrieval.py`, `websearch.py`) and Phase C managers (`approval_registry.py`, `profile.py`, `allowlist.py`, `wisdom.py`, `learning.py`).

- [ ] **Step 4: Run baseline tests**

```bash
.venv/bin/pytest -x
```

Expected: all green.

---

## Task 2: Add `agent_core.protocol` package

**Files:**
- Create: `/home/edible/Projects/agent_core/agent_core/protocol/__init__.py`
- Create: `/home/edible/Projects/agent_core/agent_core/protocol/transport.py`
- Create: `/home/edible/Projects/agent_core/agent_core/protocol/messages.py`
- Create: `/home/edible/Projects/agent_core/tests/test_protocol.py`

This is the foundational Phase D step: agent_core gets a self-registering message protocol. PAL's existing `pal.protocol` will plug into this in Part 2.

- [ ] **Step 1: Write the protocol tests first**

Create `/home/edible/Projects/agent_core/tests/test_protocol.py`:

```python
"""Tests for agent_core.protocol: transport + generic messages + registration."""
import json
import pytest

from agent_core.protocol import (
    STREAM_BUFFER_LIMIT,
    ChatMessage,
    CommandMessage,
    ErrorMessage,
    LearningCandidateProposalMessage,
    ResponseMessage,
    StreamChunkMessage,
    ToolProgressMessage,
    decode_message,
    encode_message,
    register_message,
)


def test_stream_buffer_limit_is_16_mib():
    assert STREAM_BUFFER_LIMIT == 16 * 1024 * 1024


def test_chat_round_trip():
    msg = ChatMessage(text="hello", channel_id="cli-default")
    line = encode_message(msg)
    assert line.endswith(b"\n")
    parsed = decode_message(line[:-1])
    assert isinstance(parsed, ChatMessage)
    assert parsed.text == "hello"
    assert parsed.channel_id == "cli-default"


def test_command_round_trip():
    msg = CommandMessage(name="research", args="topic foo", channel_id="C1")
    parsed = decode_message(encode_message(msg)[:-1])
    assert isinstance(parsed, CommandMessage)
    assert parsed.name == "research"
    assert parsed.args == "topic foo"


def test_stream_chunk_round_trip():
    msg = StreamChunkMessage(token="hello ")
    parsed = decode_message(encode_message(msg)[:-1])
    assert isinstance(parsed, StreamChunkMessage)
    assert parsed.token == "hello "


def test_response_round_trip():
    msg = ResponseMessage(text="done", command="research", reasoning="thought")
    parsed = decode_message(encode_message(msg)[:-1])
    assert isinstance(parsed, ResponseMessage)
    assert parsed.text == "done"
    assert parsed.reasoning == "thought"


def test_error_round_trip():
    msg = ErrorMessage(error="boom")
    parsed = decode_message(encode_message(msg)[:-1])
    assert isinstance(parsed, ErrorMessage)
    assert parsed.error == "boom"


def test_tool_progress_round_trip():
    msg = ToolProgressMessage(tool="search", arguments={"q": "foo"})
    parsed = decode_message(encode_message(msg)[:-1])
    assert isinstance(parsed, ToolProgressMessage)
    assert parsed.tool == "search"
    assert parsed.arguments == {"q": "foo"}


def test_learning_candidate_proposal_round_trip():
    msg = LearningCandidateProposalMessage(
        proposal_id="abc",
        title="Use venv",
        body="The user runs everything in .venv.",
        trigger_excerpt="just use the venv",
    )
    parsed = decode_message(encode_message(msg)[:-1])
    assert isinstance(parsed, LearningCandidateProposalMessage)
    assert parsed.title == "Use venv"


def test_decode_unknown_type_raises():
    raw = json.dumps({"type": "not_a_real_type", "x": 1}).encode("utf-8")
    with pytest.raises(ValueError, match="Unknown message type"):
        decode_message(raw)


def test_register_message_extends_registry():
    """Verify a downstream consumer can register their own message type."""
    from dataclasses import dataclass

    @dataclass
    class CustomMessage:
        payload: str
        type: str = "custom_test_message"

    register_message(CustomMessage)

    msg = CustomMessage(payload="hello")
    parsed = decode_message(encode_message(msg)[:-1])
    assert isinstance(parsed, CustomMessage)
    assert parsed.payload == "hello"


def test_encode_uses_ndjson_format():
    msg = ChatMessage(text="hi")
    line = encode_message(msg)
    assert line.endswith(b"\n")
    obj = json.loads(line[:-1])
    assert obj["type"] == "chat"
    assert obj["text"] == "hi"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_protocol.py -v
```

Expected: all tests fail with `ModuleNotFoundError: agent_core.protocol`.

- [ ] **Step 3: Create the transport module**

Create `/home/edible/Projects/agent_core/agent_core/protocol/transport.py`:

```python
"""Message transport: encode/decode + registration of message dataclasses."""
import json
from dataclasses import asdict

# asyncio StreamReader default is 64 KiB, which long NDJSON lines (e.g. /research
# results aggregated into a single response) can exceed. 16 MiB matches what PAL
# needs and is comfortable for any agent built on the same primitives.
STREAM_BUFFER_LIMIT = 16 * 1024 * 1024

_MESSAGE_TYPES: dict[str, type] = {}


def register_message(cls: type) -> type:
    """Register a dataclass type with the protocol registry. Returns the class
    unchanged so it can be used as a decorator or called directly."""
    type_field = cls.__dataclass_fields__["type"].default  # type: ignore[index]
    _MESSAGE_TYPES[type_field] = cls
    return cls


def encode_message(msg) -> bytes:
    """Serialize a registered message to a newline-terminated JSON bytes line."""
    return json.dumps(asdict(msg), ensure_ascii=False).encode("utf-8") + b"\n"


def decode_message(data: bytes):
    """Deserialize a JSON bytes line into a message object.

    Raises ValueError for unknown message types.
    """
    obj = json.loads(data)
    msg_type = obj.get("type")
    cls = _MESSAGE_TYPES.get(msg_type)
    if cls is None:
        raise ValueError(f"Unknown message type: {msg_type!r}")
    obj.pop("type", None)
    return cls(**obj)
```

- [ ] **Step 4: Create the generic messages module**

Create `/home/edible/Projects/agent_core/agent_core/protocol/messages.py`:

```python
"""Generic agent message primitives. Domain-specific messages are registered by
each agent's own protocol module."""
from dataclasses import dataclass

from agent_core.protocol.transport import register_message


@register_message
@dataclass
class ChatMessage:
    text: str
    channel_id: str | None = None
    type: str = "chat"


@register_message
@dataclass
class CommandMessage:
    name: str
    args: str
    channel_id: str | None = None
    type: str = "command"


@register_message
@dataclass
class StreamChunkMessage:
    token: str
    type: str = "stream_chunk"


@register_message
@dataclass
class ResponseMessage:
    text: str
    command: str = ""
    reasoning: str = ""
    type: str = "response"


@register_message
@dataclass
class ErrorMessage:
    error: str
    type: str = "error"


@register_message
@dataclass
class ToolProgressMessage:
    tool: str
    arguments: dict
    type: str = "tool_progress"


@register_message
@dataclass
class LearningCandidateProposalMessage:
    proposal_id: str
    title: str
    body: str
    trigger_excerpt: str  # user-message fragment that triggered the scan
    type: str = "learning_candidate_proposal"
```

- [ ] **Step 5: Create the package `__init__.py`**

Create `/home/edible/Projects/agent_core/agent_core/protocol/__init__.py`:

```python
"""Public surface of agent_core.protocol."""
from agent_core.protocol.messages import (
    ChatMessage,
    CommandMessage,
    ErrorMessage,
    LearningCandidateProposalMessage,
    ResponseMessage,
    StreamChunkMessage,
    ToolProgressMessage,
)
from agent_core.protocol.transport import (
    STREAM_BUFFER_LIMIT,
    decode_message,
    encode_message,
    register_message,
)

__all__ = [
    "STREAM_BUFFER_LIMIT",
    "ChatMessage",
    "CommandMessage",
    "ErrorMessage",
    "LearningCandidateProposalMessage",
    "ResponseMessage",
    "StreamChunkMessage",
    "ToolProgressMessage",
    "decode_message",
    "encode_message",
    "register_message",
]
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_protocol.py -v
```

Expected: all 10 tests pass.

- [ ] **Step 7: Commit**

```bash
git add agent_core/protocol/ tests/test_protocol.py
git commit -m "feat: add protocol package (transport + generic messages)"
```

---

## Task 3: Move `Conversation` into agent_core

**Files:**
- Create: `/home/edible/Projects/agent_core/agent_core/conversation.py`
- Create: `/home/edible/Projects/agent_core/tests/test_conversation.py`

The PAL field `reasoning_override: Literal["on", "off"] | None` is replaced with the generic `overrides: dict[str, Any]`. All other behavior is unchanged.

- [ ] **Step 1: Copy the test file from PAL as a starting point**

```bash
cp /home/edible/Projects/PAL/tests/test_conversation.py /home/edible/Projects/agent_core/tests/test_conversation.py
```

- [ ] **Step 2: Update test imports and reasoning_override references**

Edit `/home/edible/Projects/agent_core/tests/test_conversation.py`:

Change `from pal.conversation import Conversation` to `from agent_core.conversation import Conversation`.

Replace every `reasoning_override` usage in the file. The current PAL test (lines 100-118 area) reads/writes `conv.reasoning_override`. Update those tests to exercise the new `overrides` dict instead. Replace the entire `reasoning_override` test block with:

```python
def test_overrides_default_empty():
    conv = Conversation(history_depth=10)
    assert conv.overrides == {}


def test_overrides_can_be_set_and_read():
    conv = Conversation(history_depth=10)
    conv.overrides["reasoning"] = "on"
    assert conv.overrides["reasoning"] == "on"


def test_overrides_independent_per_conversation():
    a = Conversation(history_depth=10)
    b = Conversation(history_depth=10)
    a.overrides["reasoning"] = "on"
    assert b.overrides == {}


def test_overrides_can_hold_arbitrary_keys():
    conv = Conversation(history_depth=10)
    conv.overrides["reasoning"] = "off"
    conv.overrides["foo"] = "bar"
    conv.overrides["count"] = 42
    assert conv.overrides == {"reasoning": "off", "foo": "bar", "count": 42}
```

(Find and replace the existing block of tests that exercises `reasoning_override`. There were six references in PAL's test file; this block of four tests covers the same shape with the new field.)

- [ ] **Step 3: Run tests to verify failure**

```bash
.venv/bin/pytest tests/test_conversation.py -v
```

Expected: all tests fail with `ModuleNotFoundError: agent_core.conversation`.

- [ ] **Step 4: Create the conversation module**

Create `/home/edible/Projects/agent_core/agent_core/conversation.py`:

```python
"""In-memory conversation history with optional JSONL persistence.

Maintains a rolling in-memory window of messages, truncated to `history_depth`.
When `history_path` is set, every message is also appended to a JSONL file on
disk, enabling replay across daemon restarts (see agent_core.channels.ChannelStore).
The in-memory window is bounded; the on-disk log grows unbounded.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Conversation:
    history_depth: int
    history_path: Path | None = None
    overrides: dict[str, Any] = field(default_factory=dict)
    _messages: list[dict] = field(default_factory=list)

    @property
    def messages(self) -> list[dict]:
        return list(self._messages)

    def _append_to_history_file(self, message: dict) -> None:
        """Append a single message to the history JSONL file, if configured."""
        if self.history_path is None:
            return
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(message, ensure_ascii=False) + "\n")

    def add_user(self, text: str) -> None:
        message = {"role": "user", "content": text}
        self._messages.append(message)
        self._append_to_history_file(message)
        self._truncate()

    def add_assistant(self, text: str) -> None:
        message = {"role": "assistant", "content": text}
        self._messages.append(message)
        self._append_to_history_file(message)
        self._truncate()

    def add_assistant_tool_calls(self, tool_calls: list[dict]) -> None:
        """Record an assistant message that contains tool calls (no text content)."""
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": tool_calls,
        }
        self._messages.append(message)
        self._append_to_history_file(message)
        self._truncate()

    def add_tool_result(self, tool_call_id: str, content: str) -> None:
        """Record a tool result message."""
        message = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        }
        self._messages.append(message)
        self._append_to_history_file(message)
        self._truncate()

    def get_messages_for_api(self, system_prompt: str) -> list[dict]:
        """Return message list for the inference API: system + history."""
        return [{"role": "system", "content": system_prompt}] + self.messages

    def clear(self) -> None:
        self._messages.clear()

    def _truncate(self) -> None:
        if len(self._messages) > self.history_depth:
            self._messages = self._messages[-self.history_depth:]
            # Don't start with orphaned tool messages that lost their
            # matching counterpart during truncation. Drop leading
            # assistant(tool_calls) and tool result messages.
            changed = True
            while changed:
                changed = False
                if self._messages and self._messages[0].get("role") == "tool":
                    self._messages.pop(0)
                    changed = True
                elif (
                    self._messages
                    and self._messages[0].get("role") == "assistant"
                    and self._messages[0].get("tool_calls")
                ):
                    self._messages.pop(0)
                    changed = True
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_conversation.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add agent_core/conversation.py tests/test_conversation.py
git commit -m "feat: add Conversation with generic overrides field"
```

---

## Task 4: Move `ChannelStore` into agent_core

**Files:**
- Create: `/home/edible/Projects/agent_core/agent_core/channels.py`
- Create: `/home/edible/Projects/agent_core/tests/test_channels.py`

`ChannelStore` constructor changes from `(channels_dir, history_depth)` to `(vault_path, agent_name, history_depth)`. Internally it computes `channels_dir = vault_path / "_channels" / agent_name`.

- [ ] **Step 1: Copy the test file**

```bash
cp /home/edible/Projects/PAL/tests/test_channels.py /home/edible/Projects/agent_core/tests/test_channels.py
```

- [ ] **Step 2: Update the test file**

Edit `/home/edible/Projects/agent_core/tests/test_channels.py`:

1. Change `from pal.channels import ChannelStore, validate_channel_id` to `from agent_core.channels import ChannelStore, validate_channel_id`.
2. Update every `ChannelStore(channels_dir=tmp_path, history_depth=10)` call to `ChannelStore(vault_path=tmp_path, agent_name="testagent", history_depth=10)`.
3. Update path assertions: anywhere the test inspects `tmp_path / "<channel_id>"` (the old layout), change to `tmp_path / "_channels" / "testagent" / "<channel_id>"`. There are roughly 7 ChannelStore construction sites and a similar number of path assertions to update.
4. Add one new test verifying the per-agent subdir path:

```python
@pytest.mark.asyncio
async def test_channel_path_includes_agent_name(tmp_path):
    store = ChannelStore(vault_path=tmp_path, agent_name="myagent", history_depth=10)
    conv = await store.get_or_create("C1")
    expected = tmp_path / "_channels" / "myagent" / "C1" / "history.jsonl"
    conv.add_user("hi")
    assert expected.exists()
```

(Add at end of file. Adjust import of `pytest` if not already present.)

- [ ] **Step 3: Run tests to verify failure**

```bash
.venv/bin/pytest tests/test_channels.py -v
```

Expected: all fail with `ModuleNotFoundError`.

- [ ] **Step 4: Create the channels module**

Create `/home/edible/Projects/agent_core/agent_core/channels.py`:

```python
"""Per-channel Conversation container with on-disk persistence.

Each channel (identified by a free-form string, e.g. Discord channel ID,
`cli-default` for CLI) gets its own Conversation instance, backed by a jsonl
file at <vault>/_channels/<agent_name>/<channel_id>/history.jsonl. On first
access for a channel, if the file exists, its contents are replayed into a
fresh Conversation. Subsequent accesses return the same cached instance.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from agent_core.conversation import Conversation

logger = logging.getLogger(__name__)

_CHANNEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_channel_id(channel_id: str) -> bool:
    """Return True if the id matches the allowed character set and is non-empty."""
    return bool(_CHANNEL_ID_PATTERN.match(channel_id))


class ChannelStore:
    """Caches Conversation instances per channel, loading from disk as needed."""

    def __init__(
        self,
        vault_path: Path,
        agent_name: str,
        history_depth: int,
    ) -> None:
        self._channels_dir = vault_path / "_channels" / agent_name
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
            with history_path.open("r", encoding="utf-8") as f:
                raw = f.read()
        except OSError as exc:
            logger.warning(
                "slot=%s history unreadable (%s) renaming and starting fresh",
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

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_channels.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add agent_core/channels.py tests/test_channels.py
git commit -m "feat: add ChannelStore with vault-rooted per-agent layout"
```

---

## Task 5: Add `agent_core.git_helpers`

**Files:**
- Create: `/home/edible/Projects/agent_core/agent_core/git_helpers.py`
- Create: `/home/edible/Projects/agent_core/tests/test_git_helpers.py`

A small helper module that ships a default commit-callback factory for `Scratchpad` consumers without their own git story.

- [ ] **Step 1: Write the failing tests**

Create `/home/edible/Projects/agent_core/tests/test_git_helpers.py`:

```python
"""Tests for agent_core.git_helpers.make_commit_callback."""
import subprocess
from pathlib import Path

import pytest

from agent_core.git_helpers import make_commit_callback


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def test_commit_callback_creates_commit(tmp_path):
    _init_git_repo(tmp_path)
    cb = make_commit_callback(tmp_path)
    file_path = tmp_path / "note.md"
    file_path.write_text("hello\n")
    cb(file_path, "scratch: add note")
    log = subprocess.run(
        ["git", "log", "--format=%s"], cwd=tmp_path, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert log == "scratch: add note"


def test_commit_callback_handles_subsequent_writes(tmp_path):
    _init_git_repo(tmp_path)
    cb = make_commit_callback(tmp_path)
    file_path = tmp_path / "note.md"
    file_path.write_text("v1\n")
    cb(file_path, "v1")
    file_path.write_text("v2\n")
    cb(file_path, "v2")
    log = subprocess.run(
        ["git", "log", "--format=%s"], cwd=tmp_path, capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    assert log == ["v2", "v1"]


def test_commit_callback_no_op_when_no_changes(tmp_path):
    """Callback should not raise if there's nothing to commit."""
    _init_git_repo(tmp_path)
    file_path = tmp_path / "note.md"
    file_path.write_text("hello\n")
    cb = make_commit_callback(tmp_path)
    cb(file_path, "first")
    # Second call without changes should not raise
    cb(file_path, "second-noop")
    log = subprocess.run(
        ["git", "log", "--format=%s"], cwd=tmp_path, capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    assert log == ["first"]  # second commit skipped
```

- [ ] **Step 2: Run tests to verify failure**

```bash
.venv/bin/pytest tests/test_git_helpers.py -v
```

Expected: all fail with `ModuleNotFoundError`.

- [ ] **Step 3: Create the module**

Create `/home/edible/Projects/agent_core/agent_core/git_helpers.py`:

```python
"""Git helpers for agent_core consumers.

Currently exposes a single factory: `make_commit_callback(vault_path)` returns
a callable suitable for `Scratchpad.commit_callback`. Other helpers may be
added as future agents need them.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


def make_commit_callback(vault_path: Path) -> Callable[[Path, str], None]:
    """Return a callable that stages `path` and commits it with `message`.

    No-ops silently if the working tree has no changes (e.g. the file content
    didn't actually change between writes). Logs and swallows any subprocess
    failure so a transient git error doesn't break the surrounding operation.
    """

    def _commit(path: Path, message: str) -> None:
        try:
            subprocess.run(
                ["git", "-C", str(vault_path), "add", str(path)],
                check=True, capture_output=True,
            )
            # Use --allow-empty=false (default); if there's nothing to commit
            # git returns nonzero, which we treat as a benign no-op.
            result = subprocess.run(
                ["git", "-C", str(vault_path), "commit", "-m", message],
                capture_output=True, text=True,
            )
            if result.returncode != 0 and "nothing to commit" not in result.stdout:
                logger.warning(
                    "git commit failed in %s: rc=%d stderr=%s",
                    vault_path, result.returncode, result.stderr.strip(),
                )
        except (subprocess.CalledProcessError, OSError) as exc:
            logger.warning("git commit failed in %s: %s", vault_path, exc)

    return _commit
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_git_helpers.py -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent_core/git_helpers.py tests/test_git_helpers.py
git commit -m "feat: add git_helpers.make_commit_callback"
```

---

## Task 6: Move `Scratchpad` into agent_core

**Files:**
- Create: `/home/edible/Projects/agent_core/agent_core/scratchpad.py`
- Create: `/home/edible/Projects/agent_core/tests/test_scratchpad.py`

`Scratchpad` constructor changes from `(vault_path, channel_id, wiki, max_bytes)` to `(vault_path, agent_name, channel_id, max_bytes, commit_callback=None)`. The `commit_callback` signature is `Callable[[Path, str], None]`.

- [ ] **Step 1: Copy the test file**

```bash
cp /home/edible/Projects/PAL/tests/test_scratchpad.py /home/edible/Projects/agent_core/tests/test_scratchpad.py
```

- [ ] **Step 2: Update the test file**

Edit `/home/edible/Projects/agent_core/tests/test_scratchpad.py`:

1. Change `from pal.scratchpad import Scratchpad, ScratchpadTooLarge` to `from agent_core.scratchpad import Scratchpad, ScratchpadTooLarge`.
2. Replace every `Scratchpad(vault_path=tmp_path, channel_id="C1", wiki=wiki_mock, max_bytes=1024)` call with:

```python
commit_calls = []
def commit_cb(path: Path, message: str) -> None:
    commit_calls.append((path, message))
Scratchpad(
    vault_path=tmp_path,
    agent_name="testagent",
    channel_id="C1",
    max_bytes=1024,
    commit_callback=commit_cb,
)
```

(Adapt to each test's local needs. The original tests assert `wiki_mock.git_commit.assert_called_with(...)`; replace with `assert commit_calls == [(expected_path, expected_message)]` or similar.)

3. Update path assertions: `tmp_path / "_channels" / "C1" / "scratch.md"` becomes `tmp_path / "_channels" / "testagent" / "C1" / "scratch.md"`.
4. Add new test for `commit_callback=None`:

```python
def test_write_with_no_commit_callback_does_not_raise(tmp_path):
    sp = Scratchpad(
        vault_path=tmp_path,
        agent_name="testagent",
        channel_id="C1",
        max_bytes=1024,
        commit_callback=None,
    )
    sp.write("hello")
    assert (tmp_path / "_channels" / "testagent" / "C1" / "scratch.md").read_text() == "hello"


def test_commit_callback_receives_path_and_message(tmp_path):
    captured = []
    def cb(path: Path, message: str) -> None:
        captured.append((path, message))
    sp = Scratchpad(
        vault_path=tmp_path,
        agent_name="testagent",
        channel_id="C1",
        max_bytes=1024,
        commit_callback=cb,
    )
    sp.write("hello")
    expected_path = tmp_path / "_channels" / "testagent" / "C1" / "scratch.md"
    assert captured == [(expected_path, "scratch: update C1")]
```

- [ ] **Step 3: Run tests to verify failure**

```bash
.venv/bin/pytest tests/test_scratchpad.py -v
```

Expected: failure with `ModuleNotFoundError`.

- [ ] **Step 4: Create the scratchpad module**

Create `/home/edible/Projects/agent_core/agent_core/scratchpad.py`:

```python
"""Per-channel scratchpad: a free-form markdown file in the vault.

Lives at <vault>/_channels/<agent_name>/<channel_id>/scratch.md. Optionally
calls a commit callback after every write for git tracking. Size-capped to
prevent drift into a second wiki.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

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
        agent_name: str,
        channel_id: str,
        max_bytes: int,
        commit_callback: Callable[[Path, str], None] | None = None,
    ) -> None:
        self._vault_path = vault_path
        self._agent_name = agent_name
        self._channel_id = channel_id
        self._max_bytes = max_bytes
        self._commit_callback = commit_callback

    @property
    def _path(self) -> Path:
        return (
            self._vault_path
            / "_channels"
            / self._agent_name
            / self._channel_id
            / "scratch.md"
        )

    def read(self) -> str:
        """Return the scratchpad content, or empty string if missing/unreadable."""
        path = self._path
        if not path.exists():
            return ""
        try:
            with path.open("r", encoding="utf-8") as f:
                return f.read()
        except OSError as exc:
            logger.warning(
                "scratchpad %s unreadable (%s) treating as empty",
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
        if self._commit_callback is not None:
            try:
                self._commit_callback(path, f"scratch: update {self._channel_id}")
            except Exception as exc:
                logger.warning(
                    "scratchpad commit callback failed for %s: %s",
                    self._channel_id, exc,
                )

    def append(self, text: str) -> None:
        """Append text. Raises ScratchpadTooLarge if resulting size over cap."""
        combined = self.read() + text
        self.write(combined)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_scratchpad.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add agent_core/scratchpad.py tests/test_scratchpad.py
git commit -m "feat: add Scratchpad with commit_callback decoupling"
```

---

## Task 7: Move `LearningScanner` into agent_core

**Files:**
- Create: `/home/edible/Projects/agent_core/agent_core/learning_scanner.py`
- Create: `/home/edible/Projects/agent_core/tests/test_learning_scanner.py`
- Create: `/home/edible/Projects/agent_core/tests/test_learning_scanner_orchestrator.py`
- Create: `/home/edible/Projects/agent_core/tests/test_learning_scanner_extract.py`
- Create: `/home/edible/Projects/agent_core/tests/test_learning_scanner_dedupe.py`
- Create: `/home/edible/Projects/agent_core/tests/test_learning_scanner_prefilter.py`
- Create: `/home/edible/Projects/agent_core/tests/test_scanner_take_pending.py`

The scanner imports `LearningCandidateProposalMessage` from `agent_core.protocol` (where Task 2 placed it), and `BatchUnavailableError` from `agent_core.inference` (already present from Phase B). Otherwise byte-identical to PAL's version.

- [ ] **Step 1: Copy the test files from PAL**

```bash
cp /home/edible/Projects/PAL/tests/test_learning_scanner.py /home/edible/Projects/agent_core/tests/test_learning_scanner.py
cp /home/edible/Projects/PAL/tests/test_learning_scanner_orchestrator.py /home/edible/Projects/agent_core/tests/test_learning_scanner_orchestrator.py
cp /home/edible/Projects/PAL/tests/test_learning_scanner_extract.py /home/edible/Projects/agent_core/tests/test_learning_scanner_extract.py
cp /home/edible/Projects/PAL/tests/test_learning_scanner_dedupe.py /home/edible/Projects/agent_core/tests/test_learning_scanner_dedupe.py
cp /home/edible/Projects/PAL/tests/test_learning_scanner_prefilter.py /home/edible/Projects/agent_core/tests/test_learning_scanner_prefilter.py
cp /home/edible/Projects/PAL/tests/test_scanner_take_pending.py /home/edible/Projects/agent_core/tests/test_scanner_take_pending.py
```

- [ ] **Step 2: Update imports in each copied test file**

For each test file, replace:
- `from pal.learning_scanner import ...` → `from agent_core.learning_scanner import ...`
- `from pal.protocol import LearningCandidateProposalMessage` → `from agent_core.protocol import LearningCandidateProposalMessage`
- `from pal.learning import LearningManager` (if present) → `from agent_core.learning import LearningManager`

In `test_scanner_take_pending.py`, the `LearningManager` constructor has the agent_name argument from Phase C. Update construction calls if the PAL test file passes only `vault_path` to use `LearningManager(vault_path=tmp_path, agent_name="testagent")`.

(The Phase C migration already updated PAL's tests; verify what the current PAL tests pass and align if needed.)

- [ ] **Step 3: Run tests to verify failure**

```bash
.venv/bin/pytest tests/test_learning_scanner.py tests/test_learning_scanner_orchestrator.py tests/test_learning_scanner_extract.py tests/test_learning_scanner_dedupe.py tests/test_learning_scanner_prefilter.py tests/test_scanner_take_pending.py -v
```

Expected: all fail with `ModuleNotFoundError: agent_core.learning_scanner`.

- [ ] **Step 4: Create the learning_scanner module**

Create `/home/edible/Projects/agent_core/agent_core/learning_scanner.py`:

```python
"""Proactive scanner for learning candidates.

Fires after each LLM turn completes. A two-stage pipeline: a cheap regex
pre-filter gates an LLM extraction call. The extraction call decides whether
a durable lesson exists in the recent conversation and returns {title, body}
or null. Novel candidates are surfaced as approval proposals via
LearningCandidateProposalMessage.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections import deque
from typing import Awaitable, Callable, Optional

from agent_core.inference import BatchUnavailableError
from agent_core.protocol import LearningCandidateProposalMessage

logger = logging.getLogger(__name__)


# Signal patterns: phrases that plausibly indicate a correction, confirmation,
# or durable preference worth turning into a learning. Applied case-insensitively
# to the latest user message.
_SIGNAL_PATTERNS = [
    r"\bactually\b",
    r"\bno[,.\s]",
    r"\bstop\b",
    r"\byou\s+(always|never|should|shouldn[''`]?t|tend\s+to)\b",
    r"\bexactly\b",
    r"\bperfect\b",
    r"\bthank\s+you\b",
    r"\byou[''`]re\s+right\b",
    r"\bthat[''`]?s\s+wrong\b",
]

_SIGNAL_RE = re.compile("|".join(_SIGNAL_PATTERNS), re.IGNORECASE)


def has_signal(message: str) -> bool:
    """Return True if the message contains a learning-candidate signal."""
    if not message:
        return False
    return _SIGNAL_RE.search(message) is not None


_EXTRACTION_PROMPT = """You review a short conversation excerpt and decide whether a durable lesson is present.

A durable lesson is a behavioral preference, a correction, or a confirmed approach that should shape the agent's future behavior across sessions. It is NOT a one-off factual answer, a research topic, or a fleeting emotion.

Recent conversation (most recent last):
{conversation}

User signal message:
{trigger}

If a durable lesson is present, respond with JSON:
{{"title": "<short specific title>", "body": "<1-3 sentence lesson>"}}

If no durable lesson is present, respond with the bare word:
null

Respond with ONLY the JSON object or the word null. No prose."""


def _format_conversation(turns: list[dict]) -> str:
    if not turns:
        return "(no prior turns)"
    lines = []
    for t in turns:
        role = t.get("role", "user")
        content = (t.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else "(empty)"


async def extract_candidate(
    recent_turns: list[dict],
    trigger_message: str,
    inference_call: Callable,
    timeout: float = 15.0,
) -> Optional[dict]:
    """Ask the inference server whether a durable lesson is present.

    Returns {"title": str, "body": str} or None. Timeouts and
    BatchUnavailableError are logged and result in a silent skip (None).
    Other exceptions propagate to the caller.
    inference_call is an async callable that takes a single prompt string and
    returns the model's response text.
    """
    prompt = _EXTRACTION_PROMPT.format(
        conversation=_format_conversation(recent_turns),
        trigger=trigger_message,
    )
    try:
        raw = await asyncio.wait_for(inference_call(prompt), timeout=timeout)
    except BatchUnavailableError as exc:
        logger.warning("Learning scan skipped, batch unavailable: %s", exc)
        return None
    except asyncio.TimeoutError as exc:
        logger.warning("learning extraction timed out: %s", exc)
        return None

    text = (raw or "").strip()
    if text.lower() == "null" or not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.info("learning extraction returned non-JSON: %s", text[:100])
        return None
    if not isinstance(parsed, dict):
        return None
    title = (parsed.get("title") or "").strip()
    body = (parsed.get("body") or "").strip()
    if not title or not body:
        return None
    return {"title": title, "body": body}


def _slugify_title(title: str) -> str:
    slug = title.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _slug_tokens(slug: str) -> set[str]:
    return {t for t in slug.split("-") if len(t) > 2}


def is_duplicate_candidate(title: str, existing_slugs: list[str]) -> bool:
    """True if the candidate title matches an existing learning by exact slug
    or by high token overlap (Jaccard >= 0.6).
    """
    cand_slug = _slugify_title(title)
    if not cand_slug:
        return False
    if cand_slug in existing_slugs:
        return True
    cand_tokens = _slug_tokens(cand_slug)
    if not cand_tokens:
        return False
    for existing in existing_slugs:
        ex_tokens = _slug_tokens(existing)
        if not ex_tokens:
            continue
        overlap = len(cand_tokens & ex_tokens)
        union = len(cand_tokens | ex_tokens)
        if union and (overlap / union) >= 0.6:
            return True
    return False


class LearningScanner:
    """Orchestrates signal detection, extraction, dedupe, and proposal emission.

    At most one proposal is active at a time. Additional candidates are queued
    and drained when `clear_pending` is called.

    `extractor` is an async callable with signature:
        async (recent_turns: list[dict], trigger: str) -> dict | None
    where the returned dict has keys "title" and "body", or None if no
    durable lesson was found.
    """

    def __init__(
        self,
        learning_manager,
        extractor: Callable[..., Awaitable],
        emit: Callable[[LearningCandidateProposalMessage], None],
    ) -> None:
        self.lm = learning_manager
        self.extractor = extractor
        self.emit = emit
        self._pending_id: str | None = None
        self._pending_candidate: LearningCandidateProposalMessage | None = None
        self.queued: deque[LearningCandidateProposalMessage] = deque()

    def mark_pending(self, proposal_id: str) -> None:
        """Mark a proposal as pending; subsequent candidates will be queued."""
        self._pending_id = proposal_id

    def clear_pending(self) -> None:
        """Clear the active pending proposal and drain the next queued item, if any."""
        self._pending_id = None
        self._pending_candidate = None
        self._drain_queue()

    def take_pending(
        self, proposal_id: str,
    ) -> LearningCandidateProposalMessage | None:
        """Return and clear the pending candidate if proposal_id matches.
        Callers use this to reconstruct title/body on approve.
        """
        if self._pending_id != proposal_id:
            return None
        msg = self._pending_candidate
        self._pending_id = None
        self._pending_candidate = None
        self._drain_queue()
        return msg

    def _drain_queue(self) -> None:
        """Emit the next queued proposal (if any) and mark it pending."""
        if self._pending_id is None and self.queued:
            msg = self.queued.popleft()
            self._pending_id = msg.proposal_id
            self._pending_candidate = msg
            self.emit(msg)

    async def maybe_scan(
        self,
        recent_turns: list[dict],
        latest_user_message: str,
    ) -> None:
        """Run the full signal-extract-dedupe pipeline for one user turn.

        If a candidate is found:
        - When no proposal is pending, emit it immediately and mark it pending.
        - When a proposal is already pending, enqueue for later drain.
        """
        if not has_signal(latest_user_message):
            return

        candidate = await self.extractor(recent_turns, latest_user_message)
        if candidate is None:
            return

        existing = [e["slug"] for e in self.lm.list()]
        if is_duplicate_candidate(candidate["title"], existing):
            return

        msg = LearningCandidateProposalMessage(
            proposal_id=uuid.uuid4().hex,
            title=candidate["title"],
            body=candidate["body"],
            trigger_excerpt=latest_user_message[:200],
        )

        if self._pending_id is not None:
            self.queued.append(msg)
            return

        self._pending_id = msg.proposal_id
        self._pending_candidate = msg
        self.emit(msg)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_learning_scanner.py tests/test_learning_scanner_orchestrator.py tests/test_learning_scanner_extract.py tests/test_learning_scanner_dedupe.py tests/test_learning_scanner_prefilter.py tests/test_scanner_take_pending.py -v
```

Expected: all tests pass. If individual tests fail, inspect the specific test (likely a mock-patch path or `LearningManager` argument format that needs adjustment) and fix.

- [ ] **Step 6: Commit**

```bash
git add agent_core/learning_scanner.py tests/test_learning_scanner.py tests/test_learning_scanner_orchestrator.py tests/test_learning_scanner_extract.py tests/test_learning_scanner_dedupe.py tests/test_learning_scanner_prefilter.py tests/test_scanner_take_pending.py
git commit -m "feat: add LearningScanner consuming agent_core.protocol"
```

---

## Task 8: Bump version, update CHANGELOG, run full agent_core suite

**Files:**
- Modify: `/home/edible/Projects/agent_core/pyproject.toml`
- Modify: `/home/edible/Projects/agent_core/CHANGELOG.md`

- [ ] **Step 1: Bump version in `pyproject.toml`**

Edit `/home/edible/Projects/agent_core/pyproject.toml`. Change `version = "0.3.0"` to `version = "0.4.0"`.

- [ ] **Step 2: Update CHANGELOG**

Edit `/home/edible/Projects/agent_core/CHANGELOG.md`. Prepend under the most recent entry:

```markdown
## [0.4.0] - 2026-04-28

### Added
- `agent_core.protocol` package: `transport` (encode_message/decode_message/register_message/STREAM_BUFFER_LIMIT) and `messages` (ChatMessage, CommandMessage, StreamChunkMessage, ResponseMessage, ErrorMessage, ToolProgressMessage, LearningCandidateProposalMessage). Self-registering registry for downstream protocols.
- `agent_core.conversation.Conversation`: rolling in-memory message buffer with optional JSONL persistence and a generic `overrides: dict[str, Any]` field for per-conversation toggles.
- `agent_core.channels.ChannelStore`: per-channel Conversation cache, vault-rooted at `<vault>/_channels/<agent_name>/`.
- `agent_core.scratchpad.Scratchpad`: free-form per-channel markdown file with optional `commit_callback: Callable[[Path, str], None]` for git tracking.
- `agent_core.git_helpers.make_commit_callback`: helper factory for agents that want bare git tracking on scratchpad writes.
- `agent_core.learning_scanner.LearningScanner`: signal detection, extraction, dedupe, and proposal emission pipeline.
```

- [ ] **Step 3: Run the full agent_core test suite**

```bash
.venv/bin/pytest -v
```

Expected: all tests pass (Phase A/B/C tests still green, plus the new Phase D modules).

- [ ] **Step 4: Commit version bump**

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore: bump version to 0.4.0"
```

---

## Task 9: Push branch and open PR

- [ ] **Step 1: Push the feature branch**

```bash
cd /home/edible/Projects/agent_core
git push -u origin feature/phase-d-per-channel-state
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --title "Phase D: per-channel state + protocol split" --body "$(cat <<'EOF'
## Summary
- Adds the `agent_core.protocol` package (transport machinery + generic message primitives + LearningCandidateProposalMessage).
- Adds `Conversation`, `ChannelStore`, `Scratchpad`, `LearningScanner`, and `git_helpers` modules under per-agent vault layout.
- Bumps version to 0.4.0.

Spec: see PAL repo `docs/superpowers/specs/2026-04-28-phase-d-per-channel-state-design.md`.

## Test plan
- [ ] CI passes
- [ ] PAL Phase D feature branch lands successfully against this tag

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Wait for CI**

Wait for GitHub Actions to complete. Inspect with `gh pr checks` if needed.

Expected: green CI.

---

## Task 10: Merge PR and tag v0.4.0

- [ ] **Step 1: Merge the PR**

If running from the agent_core checkout (not a worktree):

```bash
cd /home/edible/Projects/agent_core
gh pr merge --merge
```

If running from a worktree (per Phase C lesson, `gh pr merge` fails inside worktrees):

```bash
PR_NUM=$(gh pr view --json number --jq .number)
gh api -X PUT repos/EdibleTuber/agent_core/pulls/$PR_NUM/merge --field merge_method=merge
```

- [ ] **Step 2: Update local main and tag**

```bash
cd /home/edible/Projects/agent_core
git checkout main
git pull
git tag v0.4.0
git push origin v0.4.0
```

Expected: tag exists on remote at the merge commit.

- [ ] **Step 3: Verify PyPI-style metadata**

```bash
.venv/bin/python -c "import agent_core; print(agent_core.__version__ if hasattr(agent_core, '__version__') else 'no version attr')"
grep -E '^version' pyproject.toml
```

Expected: `pyproject.toml` shows `version = "0.4.0"`. If `agent_core.__version__` is set (Phase B added it), it should also read 0.4.0.

---

# Part 2: PAL changes (feature branch)

Working directory: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d`. Use `.venv/bin/pytest`.

## Task 11: Create PAL worktree and pre-flight

**Files:**
- None modified yet.

- [ ] **Step 1: Create the feature-branch worktree**

```bash
cd /home/edible/Projects/PAL
git fetch origin
git worktree add .worktrees/agent-core-phase-d -b feature/agent-core-extraction-phase-d origin/main
cd .worktrees/agent-core-phase-d
```

- [ ] **Step 2: Set up the venv**

The repo's venv is at `/home/edible/Projects/PAL/.venv`. From the worktree, use the parent venv directly:

```bash
ls /home/edible/Projects/PAL/.venv/bin/python
```

Confirms the parent venv exists. Use `/home/edible/Projects/PAL/.venv/bin/pytest` for tests in this worktree.

- [ ] **Step 3: Confirm clean working tree**

```bash
git status
```

Expected: clean.

- [ ] **Step 4: Confirm baseline tests pass**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest -x \
  --ignore=tests/test_daemon.py \
  --ignore=tests/test_integration.py \
  --ignore=tests/test_chat_research_integration.py \
  --ignore=tests/test_consolidate_integration.py \
  --ignore=tests/test_learning_e2e.py
```

Expected: all green. (The 5 known-flaky tests are excluded per the project_agent_core_extraction memory.)

---

## Task 12: Bump agent_core dependency to 0.4.0

**Files:**
- Modify: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/pyproject.toml`

- [ ] **Step 1: Update dependency**

Edit `pyproject.toml`. Find the `agent_core` line under dependencies. Change from `agent_core @ git+https://github.com/EdibleTuber/agent_core.git@v0.3.0` (or similar) to `agent_core @ git+https://github.com/EdibleTuber/agent_core.git@v0.4.0`.

Use the Read tool first to confirm the exact line, then Edit to update.

- [ ] **Step 2: Reinstall**

```bash
/home/edible/Projects/PAL/.venv/bin/pip install -e .
```

Expected: agent_core 0.4.0 is installed. Verify:

```bash
/home/edible/Projects/PAL/.venv/bin/python -c "import agent_core.protocol; print('protocol present')"
/home/edible/Projects/PAL/.venv/bin/python -c "import agent_core.conversation; print('conversation present')"
/home/edible/Projects/PAL/.venv/bin/python -c "import agent_core.channels; print('channels present')"
/home/edible/Projects/PAL/.venv/bin/python -c "import agent_core.scratchpad; print('scratchpad present')"
/home/edible/Projects/PAL/.venv/bin/python -c "import agent_core.learning_scanner; print('learning_scanner present')"
/home/edible/Projects/PAL/.venv/bin/python -c "import agent_core.git_helpers; print('git_helpers present')"
```

Expected: all six prints succeed.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: bump agent_core dependency to v0.4.0"
```

---

## Task 13: Rewrite `pal/protocol.py`

**Files:**
- Modify: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/pal/protocol.py`

`pal/protocol.py` becomes a thin module containing only PAL-specific proposal dataclasses. Generic types are imported from `agent_core.protocol`. PAL keeps a local `Message` union for the benefit of consumers that need it for type hints (notably `pal/client.py`, `pal/cli.py`, `pal/discord_interactions.py`).

- [ ] **Step 1: Replace `pal/protocol.py` entirely**

Use the Write tool. Replace the file with:

```python
"""PAL protocol: PAL-specific message types registered with agent_core's
protocol registry, plus a local Message union over both generic and
PAL-specific message types for type hints.

Generic primitives (Chat, Command, StreamChunk, Response, Error, ToolProgress,
LearningCandidateProposal) live in agent_core.protocol. The transport
machinery (encode_message, decode_message, STREAM_BUFFER_LIMIT) does too.

Message types defined here are PAL-specific approval/proposal messages tied to
PAL's domain workflows (research, compile, reorg, consolidate, promote,
batch_fallback). They register with agent_core.protocol's registry at import
time so encode_message/decode_message round-trip them correctly.
"""
from dataclasses import dataclass
from typing import Literal

from agent_core.protocol import (
    ChatMessage,
    CommandMessage,
    ErrorMessage,
    LearningCandidateProposalMessage,
    ResponseMessage,
    StreamChunkMessage,
    ToolProgressMessage,
    register_message,
)


@register_message
@dataclass
class ResearchProposalMessage:
    proposal_id: str
    topic: str
    depth: int
    rationale: str
    type: str = "research_proposal"


@register_message
@dataclass
class ResearchApprovalResponseMessage:
    proposal_id: str
    decision: str  # "approve" | "decline" | "edit"
    new_topic: str | None = None
    new_depth: int | None = None
    summary_paths: list[str] | None = None
    type: str = "research_approval_response"


@register_message
@dataclass
class CompileProposalMessage:
    proposal_id: str
    summary_paths: list[str]
    rationale: str
    type: str = "compile_proposal"


@register_message
@dataclass
class ReorgProposalMessage:
    proposal_id: str
    operations: list[dict]
    rationale: str
    references_preview: int
    type: str = "reorg_proposal"


@register_message
@dataclass
class ConsolidateProposalMessage:
    proposal_id: str
    source_paths: list[str]
    target_path: str
    target_title: str
    rationale: str
    type: str = "consolidate_proposal"


@register_message
@dataclass
class PromoteProposalMessage:
    proposal_id: str
    slug: str
    title: str
    body: str
    rationale: str
    type: str = "promote_proposal"


@register_message
@dataclass
class BatchFallbackProposal:
    """Emitted when a user-facing call to the batch inference backend fails
    and the user should choose: retry on batch, run on main, or skip this step.

    Approval states carried via approval_choice in the approval registry:
      - approved with state "retry": retry on batch
      - approved with state "main": run on main for this one call
      - declined: caller uses its default fallback
    """
    proposal_id: str
    caller: Literal["categorizer", "llm_toc"]
    context: str
    original_request: dict
    type: str = "batch_fallback_proposal"


@register_message
@dataclass
class BatchFallbackApprovalMessage:
    """Client to daemon: the user's choice for a BatchFallbackProposal.

    choice values:
      - "retry": approve with state "retry" (retry on batch)
      - "main":  approve with state "main"  (run on main for this one call)
      - "skip":  decline (caller uses its default fallback)
    """
    proposal_id: str
    choice: Literal["retry", "main", "skip"]
    type: str = "batch_fallback_approval"


# Local Message union over BOTH generic and PAL-specific types for type hints.
# Consumers like pal/client.py and pal/cli.py import this for their isinstance
# branches and type annotations.
Message = (
    ChatMessage
    | CommandMessage
    | StreamChunkMessage
    | ResponseMessage
    | ErrorMessage
    | ToolProgressMessage
    | ResearchProposalMessage
    | ResearchApprovalResponseMessage
    | CompileProposalMessage
    | ReorgProposalMessage
    | ConsolidateProposalMessage
    | PromoteProposalMessage
    | LearningCandidateProposalMessage
    | BatchFallbackProposal
    | BatchFallbackApprovalMessage
)
```

- [ ] **Step 2: Verify the module imports cleanly**

```bash
/home/edible/Projects/PAL/.venv/bin/python -c "from pal.protocol import Message, ResearchProposalMessage; print('ok')"
```

Expected: prints `ok`. If you see `ImportError: cannot import name X`, the rewrite missed a type that PAL still imports from `pal.protocol`. Add it.

- [ ] **Step 3: Commit**

```bash
git add pal/protocol.py
git commit -m "refactor: split pal.protocol into pal-specific subset, generics from agent_core"
```

---

## Task 14: Sweep production imports for protocol generics

**Files (modify):**
- `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/pal/client.py`
- `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/pal/daemon.py`
- `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/pal/discord_adapter.py`
- `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/pal/discord_interactions.py`
- `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/pal/cli.py`

Generic primitives (Chat, Command, StreamChunk, Response, Error, ToolProgress, encode_message, decode_message, STREAM_BUFFER_LIMIT) come from `agent_core.protocol`. PAL-specific types (Research/Compile/Reorg/Consolidate/Promote/BatchFallback*) and the `Message` union still come from `pal.protocol`.

For each file, the existing import block needs to be split into two: one from `agent_core.protocol`, one from `pal.protocol`.

- [ ] **Step 1: `pal/client.py:9-20`**

Use the Read tool to confirm the current import block. Then Edit it to split. Current pattern (approximate, reading from inventory):

```python
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
```

Becomes:

```python
from agent_core.protocol import (
    ChatMessage,
    CommandMessage,
    StreamChunkMessage,
    ResponseMessage,
    ErrorMessage,
    ToolProgressMessage,
    STREAM_BUFFER_LIMIT,
    encode_message,
    decode_message,
)
from pal.protocol import Message
```

(`Message` stays from `pal.protocol` because it includes PAL-specific types in its union.)

- [ ] **Step 2: `pal/daemon.py:50-63`**

Read the current block. Split: generic types (Chat, Command, StreamChunk, Response, Error, ToolProgress, STREAM_BUFFER_LIMIT, encode_message, decode_message) come from `agent_core.protocol`; PAL-specific (ResearchApprovalResponseMessage, BatchFallbackApprovalMessage) and `Message` stay from `pal.protocol`.

Also, `pal/daemon.py:1338` has a local `from pal.protocol import BatchFallbackProposal`. This is PAL-specific, no change needed.

- [ ] **Step 3: `pal/discord_adapter.py:23-29`**

Generic: StreamChunkMessage, ResponseMessage, ErrorMessage, ToolProgressMessage move to `agent_core.protocol`. PAL-specific: ResearchApprovalResponseMessage stays in `pal.protocol`.

- [ ] **Step 4: `pal/discord_interactions.py:444-450`**

Generic: ErrorMessage, Message (wait — Message stays from pal.protocol), ResponseMessage, StreamChunkMessage, ToolProgressMessage. Update the imports: pull Error/Response/StreamChunk/ToolProgress from `agent_core.protocol`, keep Message from `pal.protocol`.

The earlier `pal/discord_interactions.py:19-29` imports are all PAL-specific proposal types; no change there.

- [ ] **Step 5: `pal/cli.py:20-33`**

Mixed imports. Generic: StreamChunkMessage, ResponseMessage, ErrorMessage, ToolProgressMessage move to `agent_core.protocol`. PAL-specific: ResearchProposalMessage, ResearchApprovalResponseMessage, CompileProposalMessage, ConsolidateProposalMessage, ReorgProposalMessage, BatchFallbackProposal, BatchFallbackApprovalMessage stay in `pal.protocol`. `Message` stays in `pal.protocol`.

- [ ] **Step 6: Verify imports across PAL load**

```bash
/home/edible/Projects/PAL/.venv/bin/python -c "import pal.client, pal.daemon, pal.discord_adapter, pal.discord_interactions, pal.cli; print('ok')"
```

Expected: prints `ok`. If `ImportError`, fix the missing import.

- [ ] **Step 7: Commit**

```bash
git add pal/client.py pal/daemon.py pal/discord_adapter.py pal/discord_interactions.py pal/cli.py
git commit -m "refactor: import protocol generics from agent_core"
```

---

## Task 15: Sweep test imports for protocol generics

**Files (modify):**
The full list, by inventory:
- `tests/test_protocol.py` (heavy: 13+ import sites; the file shrinks to PAL-specific tests only — see Step 4 below)
- `tests/test_protocol_promote_proposal.py`
- `tests/test_protocol_learning_candidate.py`
- `tests/test_batch_fallback_proposal.py`
- `tests/test_client.py`
- `tests/test_daemon.py` (flaky-skipped, but update for cleanliness)
- `tests/test_chat_research_integration.py` (flaky-skipped)
- `tests/test_chat_compile_tools.py`
- `tests/test_chat_reorg_tools.py`
- `tests/test_cli_research_proposal.py`
- `tests/test_cli_batch_fallback.py`
- `tests/test_discord_adapter.py`
- `tests/test_discord_interactions.py`
- `tests/test_discord_learning_candidate.py`
- `tests/test_discord_promote_proposal.py`
- `tests/test_integration.py` (flaky-skipped)
- `tests/test_learning_commands.py`
- `tests/test_prompt_injection.py`
- `tests/test_wiki_commands.py`

For each: split the `from pal.protocol import ...` into `from agent_core.protocol import ...` (generic types) and `from pal.protocol import ...` (PAL-specific types). The exact symbol category split is documented in the inventory section at the top of this plan.

- [ ] **Step 1: For each test file, read the import block and split it**

Use the Read tool to inspect the import lines, then Edit. Apply the same generic-vs-PAL-specific split documented above.

Key rule: the `LearningCandidateProposalMessage` is in `agent_core.protocol`, NOT `pal.protocol` (it moved with the generics). Tests like `test_protocol_learning_candidate.py`, `test_discord_learning_candidate.py`, `test_scanner_take_pending.py` (already covered in Task 19), and `test_daemon_scanner_approval.py` (Task 19) need that updated import.

- [ ] **Step 2: Special-case `tests/test_protocol.py`**

This test file currently exercises the entire `pal.protocol` module. After the split, the generic-message tests for that module are owned by `agent_core/tests/test_protocol.py` (Task 2). PAL's `tests/test_protocol.py` should be cut down to cover ONLY:

- PAL-specific message types' round-trip (ResearchProposal, ResearchApprovalResponse, CompileProposal, ReorgProposal, ConsolidateProposal, PromoteProposal, BatchFallbackProposal, BatchFallbackApprovalMessage).
- Verification that PAL-specific types are correctly registered (i.e. `decode_message(encode_message(ResearchProposalMessage(...)))` returns a ResearchProposalMessage instance).

Read the current `tests/test_protocol.py`, identify the test functions covering PAL-specific types vs generic primitives, and delete the generic-primitive tests (those are duplicated in agent_core's suite). Keep the PAL-specific tests and update the imports to pull `encode_message`, `decode_message` from `agent_core.protocol` and the dataclasses from `pal.protocol`.

- [ ] **Step 3: Search for quoted-string references**

The Phase C lesson notes that quoted module path strings can hide in `monkeypatch.setattr(...)` and `mock.patch(...)`. Use the Grep tool (subagents only — direct caller asks subagent) to search for these patterns:

- `"pal.protocol.ChatMessage"`, `"pal.protocol.CommandMessage"`, etc. for the seven generic types
- `"pal.conversation"`, `"pal.channels"`, `"pal.scratchpad"`, `"pal.learning_scanner"` (handled in Tasks 16-19)

Update each match to its agent_core equivalent. Most likely zero matches for the protocol generics (the inventory found none), but verify.

- [ ] **Step 4: Run all tests under tests/ that match `test_protocol*` and the protocol-touching tests**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest tests/test_protocol.py tests/test_protocol_promote_proposal.py tests/test_protocol_learning_candidate.py tests/test_batch_fallback_proposal.py tests/test_client.py tests/test_chat_compile_tools.py tests/test_chat_reorg_tools.py tests/test_cli_research_proposal.py tests/test_cli_batch_fallback.py tests/test_discord_adapter.py tests/test_discord_interactions.py tests/test_discord_learning_candidate.py tests/test_discord_promote_proposal.py tests/test_learning_commands.py tests/test_prompt_injection.py tests/test_wiki_commands.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "refactor: update test imports for protocol split"
```

---

## Task 16: Migrate `Conversation` usage and reasoning_override

**Files:**
- Delete: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/pal/conversation.py`
- Delete: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/tests/test_conversation.py`
- Modify: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/pal/daemon.py` (3 import + 5 reasoning_override sites)
- Modify: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/pal/channels.py` (1 import — but channels.py is deleted in Task 17, so this is moot if Task 17 runs after; we update it here anyway since they're independent commits)

Note: `pal/conversation.py:5` (docstring) references `pal.channels.ChannelStore`; the file is being deleted, so no edit needed.

- [ ] **Step 1: Update `pal/daemon.py:18` import**

Read the current line. Change `from pal.conversation import Conversation` to `from agent_core.conversation import Conversation`.

- [ ] **Step 2: Update `pal/daemon.py:598` (read site)**

Read the current line. Change:

```python
reasoning_label = conv.reasoning_override or "auto"
```

to:

```python
reasoning_label = conv.overrides.get("reasoning") or "auto"
```

- [ ] **Step 3: Update `pal/daemon.py:1955, 1964, 1973` (write sites)**

Change each in turn:

```python
conv.reasoning_override = "on"
```
→
```python
conv.overrides["reasoning"] = "on"
```

Same pattern for `"off"` (line 1964) and `None` (line 1973). For the `None` case:

```python
conv.reasoning_override = None
```
→
```python
conv.overrides.pop("reasoning", None)
```

- [ ] **Step 4: Update `pal/daemon.py:1989` (read site in f-string)**

Change:

```python
text=f"Reasoning mode: {conv.reasoning_override or 'auto'} (effective: {mode})"
```

to:

```python
text=f"Reasoning mode: {conv.overrides.get('reasoning') or 'auto'} (effective: {mode})"
```

- [ ] **Step 5: Update `pal/channels.py:18` import**

Read the current line. Change `from pal.conversation import Conversation` to `from agent_core.conversation import Conversation`.

(This file is deleted in Task 17; the change here ensures the file imports cleanly between commits if a subagent runs partial state.)

- [ ] **Step 6: Delete `pal/conversation.py`**

```bash
git rm pal/conversation.py
```

- [ ] **Step 7: Delete `tests/test_conversation.py`**

```bash
git rm tests/test_conversation.py
```

(Test coverage for `Conversation` lives in agent_core now.)

- [ ] **Step 8: Run a focused test slice**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest tests/test_channels.py tests/test_daemon_channels.py -v
```

Expected: pass (these tests still work because they exercise ChannelStore which uses Conversation transitively).

- [ ] **Step 9: Commit**

```bash
git add pal/daemon.py pal/channels.py
git commit -m "refactor: migrate Conversation usage to agent_core, switch to overrides dict"
```

---

## Task 17: Migrate `ChannelStore` usage

**Files:**
- Delete: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/pal/channels.py`
- Delete: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/tests/test_channels.py`
- Modify: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/pal/daemon.py:19` (import)
- Modify: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/pal/daemon.py:189-192` (construction)
- Modify: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/tests/test_daemon_channels.py` (3 construction sites + import)

- [ ] **Step 1: Update `pal/daemon.py:19` import**

Change `from pal.channels import ChannelStore, validate_channel_id` to `from agent_core.channels import ChannelStore, validate_channel_id`.

- [ ] **Step 2: Update `pal/daemon.py:189-192` construction**

Read the current lines. Change:

```python
self.channels = ChannelStore(
    channels_dir=self.config.channels_dir,
    history_depth=self.config.history_depth,
)
```

to:

```python
self.channels = ChannelStore(
    vault_path=self.config.vault_path,
    agent_name="pal",
    history_depth=self.config.history_depth,
)
```

(If `self.config.channels_dir` is referenced anywhere else, leave the config attribute itself alone — only the construction call changes. The migration script handles the on-disk path move.)

- [ ] **Step 3: Update `tests/test_daemon_channels.py:11, 13, 45, 47, 53` and the import**

Read the file. Update import: `from pal.channels import ChannelStore` (twice — at lines 11 and 45) → `from agent_core.channels import ChannelStore`.

For each ChannelStore construction call (3 sites: lines 13, 47, 53), change:

```python
ChannelStore(channels_dir=tmp_path, history_depth=50)
```

to:

```python
ChannelStore(vault_path=tmp_path, agent_name="pal", history_depth=50)
```

If the test inspects paths under `tmp_path / <channel_id>`, also update those expectations to `tmp_path / "_channels" / "pal" / <channel_id>`.

- [ ] **Step 4: Delete `pal/channels.py` and `tests/test_channels.py`**

```bash
git rm pal/channels.py tests/test_channels.py
```

- [ ] **Step 5: Run focused tests**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest tests/test_daemon_channels.py -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add pal/daemon.py tests/test_daemon_channels.py
git commit -m "refactor: migrate ChannelStore usage to agent_core with per-agent layout"
```

---

## Task 18: Migrate `Scratchpad` usage

**Files:**
- Delete: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/pal/scratchpad.py`
- Delete: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/tests/test_scratchpad.py`
- Modify: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/pal/daemon.py:20` (import)
- Modify: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/pal/daemon.py:436-440, 656-660` (construction)
- Modify: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/pal/tools.py:18` (import)
- Modify: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/tests/test_scratch_command.py` (3 construction sites + 3 imports)
- Modify: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/tests/test_tools.py` (2 construction sites + 2 imports)

- [ ] **Step 1: Update `pal/daemon.py:20` import**

Change `from pal.scratchpad import Scratchpad, ScratchpadTooLarge` to `from agent_core.scratchpad import Scratchpad, ScratchpadTooLarge`.

- [ ] **Step 2: Update `pal/daemon.py` Scratchpad construction sites (lines 436-440, 656-660)**

Both sites currently look like:

```python
scratchpad = Scratchpad(
    vault_path=self.config.vault_path,
    channel_id=channel_id,
    wiki=self.wiki,
    max_bytes=self.config.scratchpad_max_bytes,
)
```

Replace each with:

```python
def _commit_scratchpad(path, message):
    self.wiki.git_commit(message)

scratchpad = Scratchpad(
    vault_path=self.config.vault_path,
    agent_name="pal",
    channel_id=channel_id,
    max_bytes=self.config.scratchpad_max_bytes,
    commit_callback=_commit_scratchpad,
)
```

(The closure ignores `path` because `wiki.git_commit` knows what to stage from its own internal state.)

If both sites share a method, consider extracting `_commit_scratchpad` once at class scope; otherwise inline as shown.

- [ ] **Step 3: Update `pal/tools.py:18` import**

Change `from pal.scratchpad import ScratchpadTooLarge` to `from agent_core.scratchpad import ScratchpadTooLarge`.

- [ ] **Step 4: Update `tests/test_scratch_command.py` (lines 11, 16-17, 30, 35-36, 48, 51-52)**

Update all 3 imports: `from pal.scratchpad import Scratchpad` → `from agent_core.scratchpad import Scratchpad`.

For each Scratchpad construction (3 sites), replace `wiki=wiki` with `commit_callback=lambda path, msg: wiki.git_commit(msg)` (keep the existing wiki Mock for assertions; the assertion pattern changes from `wiki.git_commit.assert_called_with(...)` to the same — since the lambda still calls `wiki.git_commit`).

Add `agent_name="testagent"` argument to each construction. Path assertions update from `tmp_path / "_channels" / "C1" / "scratch.md"` (if any) to `tmp_path / "_channels" / "testagent" / "C1" / "scratch.md"`.

- [ ] **Step 5: Update `tests/test_tools.py` (lines 475, 480-482, 497, 502-504)**

Same pattern: import update, construction update with `agent_name`, `wiki` swapped for `commit_callback` lambda.

- [ ] **Step 6: Delete `pal/scratchpad.py` and `tests/test_scratchpad.py`**

```bash
git rm pal/scratchpad.py tests/test_scratchpad.py
```

- [ ] **Step 7: Run focused tests**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest tests/test_scratch_command.py tests/test_tools.py -v
```

Expected: pass.

- [ ] **Step 8: Commit**

```bash
git add pal/daemon.py pal/tools.py tests/test_scratch_command.py tests/test_tools.py
git commit -m "refactor: migrate Scratchpad usage with commit_callback decoupling"
```

---

## Task 19: Migrate `LearningScanner` usage

**Files:**
- Delete: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/pal/learning_scanner.py`
- Modify: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/pal/daemon.py:264` (import)
- Delete: 6 PAL test files now owned by agent_core: `tests/test_learning_scanner.py`, `tests/test_learning_scanner_orchestrator.py`, `tests/test_learning_scanner_extract.py`, `tests/test_learning_scanner_dedupe.py`, `tests/test_learning_scanner_prefilter.py`, `tests/test_scanner_take_pending.py`
- Modify: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/tests/test_daemon_scanner_hook.py:15` (import — still in PAL because it tests daemon integration)
- Modify: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/tests/test_daemon_scanner_approval.py:7-11` (imports)
- Modify: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/tests/test_learning_e2e.py:11` (import — flaky-skipped, update for cleanliness)

PAL keeps `tests/test_daemon_scanner_hook.py` and `tests/test_daemon_scanner_approval.py` because they exercise daemon integration with the scanner, not the scanner itself.

- [ ] **Step 1: Update `pal/daemon.py:264` import**

Change `from pal.learning_scanner import LearningScanner, extract_candidate` to `from agent_core.learning_scanner import LearningScanner, extract_candidate`.

- [ ] **Step 2: Delete `pal/learning_scanner.py`**

```bash
git rm pal/learning_scanner.py
```

- [ ] **Step 3: Delete the 6 unit-test files (owned by agent_core now)**

```bash
git rm tests/test_learning_scanner.py tests/test_learning_scanner_orchestrator.py tests/test_learning_scanner_extract.py tests/test_learning_scanner_dedupe.py tests/test_learning_scanner_prefilter.py tests/test_scanner_take_pending.py
```

- [ ] **Step 4: Update remaining test imports**

`tests/test_daemon_scanner_hook.py:15`: change `from pal.learning_scanner import LearningScanner, extract_candidate` to `from agent_core.learning_scanner import LearningScanner, extract_candidate`.

`tests/test_daemon_scanner_approval.py:7-11`:
- `from pal.learning_scanner import LearningScanner` → `from agent_core.learning_scanner import LearningScanner`
- `from pal.protocol import LearningCandidateProposalMessage, ResearchApprovalResponseMessage` → split: `from agent_core.protocol import LearningCandidateProposalMessage` + `from pal.protocol import ResearchApprovalResponseMessage`

`tests/test_learning_e2e.py:11`: `from pal.learning_scanner import LearningScanner` → `from agent_core.learning_scanner import LearningScanner`.

- [ ] **Step 5: Run focused tests**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest tests/test_daemon_scanner_hook.py tests/test_daemon_scanner_approval.py -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add pal/daemon.py tests/test_daemon_scanner_hook.py tests/test_daemon_scanner_approval.py tests/test_learning_e2e.py
git commit -m "refactor: migrate learning_scanner usage to agent_core"
```

---

## Task 20: Add migration script and its tests

**Files:**
- Create: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/scripts/migrate_phase_d.py`
- Create: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/tests/test_phase_d_migration.py`

The migration moves `<vault>/_channels/<channel_id>/` to `<vault>/_channels/pal/<channel_id>/` for every existing channel directory. Idempotent.

- [ ] **Step 1: Write the failing test**

Create `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/tests/test_phase_d_migration.py`:

```python
"""Tests for the Phase D server-side data migration script."""
import subprocess
import sys
from pathlib import Path


def _run_migration(vault: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "scripts/migrate_phase_d.py", str(vault)],
        capture_output=True, text=True, check=False,
    )


def test_migration_moves_existing_channels(tmp_path):
    channels = tmp_path / "_channels"
    channels.mkdir()
    (channels / "C1").mkdir()
    (channels / "C1" / "history.jsonl").write_text('{"role":"user","content":"hi"}\n')
    (channels / "C1" / "scratch.md").write_text("notes\n")
    (channels / "C2").mkdir()
    (channels / "C2" / "history.jsonl").write_text("")

    result = _run_migration(tmp_path)
    assert result.returncode == 0, result.stderr

    assert (channels / "pal" / "C1" / "history.jsonl").read_text().startswith('{"role":"user"')
    assert (channels / "pal" / "C1" / "scratch.md").read_text() == "notes\n"
    assert (channels / "pal" / "C2" / "history.jsonl").exists()
    assert not (channels / "C1").exists()
    assert not (channels / "C2").exists()


def test_migration_is_idempotent(tmp_path):
    channels = tmp_path / "_channels"
    channels.mkdir()
    (channels / "C1").mkdir()
    (channels / "C1" / "history.jsonl").write_text("data\n")

    _run_migration(tmp_path)
    result = _run_migration(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (channels / "pal" / "C1" / "history.jsonl").read_text() == "data\n"


def test_migration_skips_already_migrated_pal_dir(tmp_path):
    channels = tmp_path / "_channels"
    (channels / "pal" / "C1").mkdir(parents=True)
    (channels / "pal" / "C1" / "history.jsonl").write_text("already-migrated\n")

    result = _run_migration(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (channels / "pal" / "C1" / "history.jsonl").read_text() == "already-migrated\n"


def test_migration_refuses_overwrite_when_both_exist(tmp_path):
    """If both <vault>/_channels/C1/ and <vault>/_channels/pal/C1/ exist, refuse."""
    channels = tmp_path / "_channels"
    (channels / "C1").mkdir(parents=True)
    (channels / "C1" / "history.jsonl").write_text("legacy\n")
    (channels / "pal" / "C1").mkdir(parents=True)
    (channels / "pal" / "C1" / "history.jsonl").write_text("new\n")

    result = _run_migration(tmp_path)
    assert result.returncode != 0
    assert "would overwrite" in result.stderr.lower() or "would overwrite" in result.stdout.lower()
    # Both dirs untouched
    assert (channels / "C1" / "history.jsonl").read_text() == "legacy\n"
    assert (channels / "pal" / "C1" / "history.jsonl").read_text() == "new\n"


def test_migration_handles_empty_channels_dir(tmp_path):
    channels = tmp_path / "_channels"
    channels.mkdir()
    result = _run_migration(tmp_path)
    assert result.returncode == 0, result.stderr


def test_migration_skips_missing_channels_dir(tmp_path):
    """If _channels doesn't exist, exit cleanly."""
    result = _run_migration(tmp_path)
    assert result.returncode == 0, result.stderr
```

- [ ] **Step 2: Run tests to verify failure**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest tests/test_phase_d_migration.py -v
```

Expected: all fail with "No such file or directory: scripts/migrate_phase_d.py".

- [ ] **Step 3: Create the migration script**

Create `/home/edible/Projects/PAL/.worktrees/agent-core-phase-d/scripts/migrate_phase_d.py`:

```python
#!/usr/bin/env python3
"""One-shot migration: move <vault>/_channels/<channel_id>/ entries into
<vault>/_channels/pal/<channel_id>/.

Usage:
    python scripts/migrate_phase_d.py /path/to/vault

Idempotent. Refuses to overwrite if both old and new locations exist for the
same channel id.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

_CHANNEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def migrate(vault: Path) -> int:
    channels = vault / "_channels"
    if not channels.exists():
        print(f"_channels directory does not exist at {channels}, nothing to migrate.")
        return 0

    target_dir = channels / "pal"
    target_dir.mkdir(exist_ok=True)

    errors = 0
    moved = 0
    skipped = 0

    for entry in sorted(channels.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name == "pal":
            continue
        if not _CHANNEL_ID_PATTERN.match(entry.name):
            print(f"  skipping non-channel directory: {entry.name}")
            continue

        new_path = target_dir / entry.name
        if new_path.exists():
            print(
                f"ERROR: would overwrite {new_path} when moving {entry}.",
                file=sys.stderr,
            )
            errors += 1
            continue

        print(f"  moving {entry} -> {new_path}")
        try:
            shutil.move(str(entry), str(new_path))
            moved += 1
        except OSError as exc:
            print(f"ERROR: move failed for {entry}: {exc}", file=sys.stderr)
            errors += 1

    print(f"Done. moved={moved} skipped={skipped} errors={errors}")
    return 1 if errors else 0


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: migrate_phase_d.py <vault_path>", file=sys.stderr)
        return 2
    vault = Path(sys.argv[1]).resolve()
    if not vault.is_dir():
        print(f"vault path is not a directory: {vault}", file=sys.stderr)
        return 2
    return migrate(vault)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest tests/test_phase_d_migration.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/migrate_phase_d.py tests/test_phase_d_migration.py
git commit -m "feat: add Phase D server-side channel layout migration script"
```

---

## Task 21: Run the full PAL test suite

- [ ] **Step 1: Run with the 5 known-flaky tests excluded**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest \
  --ignore=tests/test_daemon.py \
  --ignore=tests/test_integration.py \
  --ignore=tests/test_chat_research_integration.py \
  --ignore=tests/test_consolidate_integration.py \
  --ignore=tests/test_learning_e2e.py \
  -v
```

Expected: all green.

- [ ] **Step 2: If any tests fail, diagnose and fix in place**

Common failure modes:
- `ImportError: cannot import name X from pal.protocol`: a generic primitive was missed in Task 14 or 15. Add the agent_core.protocol import.
- `TypeError: __init__() got an unexpected keyword argument 'channels_dir'`: a ChannelStore call still uses the old signature. Update.
- `TypeError: __init__() got an unexpected keyword argument 'wiki'`: a Scratchpad call still uses the old signature. Update.
- Path assertion mismatches: tests still expect `_channels/<id>/` instead of `_channels/<agent>/<id>/`.

Fix each, commit with a descriptive message (`fix: <specific issue>`).

- [ ] **Step 3: Spot-check importability of the trimmed `pal.protocol`**

```bash
/home/edible/Projects/PAL/.venv/bin/python -c "
from pal.protocol import (
    Message,
    ResearchProposalMessage,
    ResearchApprovalResponseMessage,
    CompileProposalMessage,
    ReorgProposalMessage,
    ConsolidateProposalMessage,
    PromoteProposalMessage,
    BatchFallbackProposal,
    BatchFallbackApprovalMessage,
)
from agent_core.protocol import (
    ChatMessage,
    CommandMessage,
    StreamChunkMessage,
    ResponseMessage,
    ErrorMessage,
    ToolProgressMessage,
    LearningCandidateProposalMessage,
    encode_message,
    decode_message,
    STREAM_BUFFER_LIMIT,
)
print('all imports ok')
"
```

Expected: prints `all imports ok`.

---

## Task 22: Push branch and open PR

- [ ] **Step 1: Push the feature branch**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-d
git push -u origin feature/agent-core-extraction-phase-d
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --title "Phase D: per-channel state + protocol split" --body "$(cat <<'EOF'
## Summary
- Bumps agent_core dependency to v0.4.0.
- Splits `pal.protocol` into PAL-specific subset + generic primitives consumed from `agent_core.protocol`.
- Migrates `Conversation`, `ChannelStore`, `Scratchpad`, `LearningScanner` usage to `agent_core` equivalents.
- Channels and scratchpads now live at `<vault>/_channels/pal/<channel_id>/`.
- Replaces `Conversation.reasoning_override` with the generic `Conversation.overrides` dict.
- Adds `scripts/migrate_phase_d.py` for the one-time data move; runs idempotently.

Spec: `docs/superpowers/specs/2026-04-28-phase-d-per-channel-state-design.md`.

## Test plan
- [x] PAL test suite green (with 5 known-flaky integration tests excluded per project memory).
- [ ] Server-side: run `python scripts/migrate_phase_d.py /mnt/secondary/PAL/vault` after deploy, before restarting the daemon.
- [ ] Server smoke: daemon starts, channel history loads, scratchpad write commits, learning scanner emits a proposal, `/think on` and `/think off` work.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Wait for CI**

```bash
gh pr checks
```

Expected: green (or running). Wait until green.

---

## Task 23: Merge PR

Per the Phase C lesson, `gh pr merge` fails inside a worktree. Use the API workaround from the parent repo or laptop.

- [ ] **Step 1: Merge from the parent (non-worktree) checkout**

```bash
cd /home/edible/Projects/PAL  # NOT the worktree
PR_NUM=$(gh pr view feature/agent-core-extraction-phase-d --json number --jq .number)
gh api -X PUT repos/EdibleTuber/PAL/pulls/$PR_NUM/merge --field merge_method=merge
```

- [ ] **Step 2: Update local main**

```bash
cd /home/edible/Projects/PAL
git checkout main
git pull
```

Expected: HEAD includes the Phase D merge commit.

- [ ] **Step 3: Clean up the worktree**

```bash
cd /home/edible/Projects/PAL
git worktree remove .worktrees/agent-core-phase-d
git branch -d feature/agent-core-extraction-phase-d  # optional, branch is merged
```

---

## Task 24: Server-side migration runbook

This task is run by the user on the inference server (192.168.1.14). The agent does not SSH; provide the exact commands.

- [ ] **Step 1: Hand the user the runbook**

Provide this text to the user verbatim, asking them to run it on the server:

```
Server-side Phase D migration steps (you run these on 192.168.1.14):

1. Stop the PAL daemon:
   systemctl --user stop pal-daemon

2. cd /mnt/secondary/PAL

3. git fetch origin && git checkout main && git pull
   # confirms the Phase D merge is present

4. Reinstall to pull agent_core 0.4.0:
   .venv/bin/pip install -e .

5. Run the migration script:
   .venv/bin/python scripts/migrate_phase_d.py /mnt/secondary/PAL/vault

   Expected output: lines like "moving .../C<id> -> .../pal/C<id>" for each
   existing channel, then "Done. moved=N skipped=0 errors=0".

6. Verify the new layout:
   ls /mnt/secondary/PAL/vault/_channels/pal/

   Expected: a directory per existing channel id.

7. Restart the daemon:
   systemctl --user start pal-daemon

8. Tail the logs and confirm clean startup:
   journalctl --user -u pal-daemon -f

   Expected: no errors, daemon listens on its socket.

Smoke checks (from your CLI session against the server):
- Send a chat message in an existing channel: previous history loads, new
  message appends.
- /scratch read in an existing channel: shows whatever was there before.
- /scratch write 'phase D smoke': commits, /scratch read confirms.
- /think on: reasoning override applies.
- Trigger a learning candidate (e.g. type "actually you're right, ...")
  in a way that previously surfaced a learning proposal: proposal still
  appears.

If anything fails, the migration script is idempotent and can be re-run
safely. If the daemon won't start, restore the most recent vault backup
and roll back agent_core: .venv/bin/pip install agent_core==0.3.0
```

- [ ] **Step 2: Wait for user confirmation**

The user will report back whether the migration ran cleanly and the smoke checks passed. If not, diagnose and fix; the migration script is idempotent so re-running after a code fix is safe.

---

## Task 25: Update memory and close out

- [ ] **Step 1: Update `project_agent_core_extraction.md` memory**

Update `/home/edible/.claude/projects/-home-edible-Projects-PAL/memory/project_agent_core_extraction.md` to reflect Phase D completion:

- Move Phase D from "remaining" to "done" with merge date and PR number.
- Update agent_core version to v0.4.0 and module count to 20 (Phase A: 5 utils, Phase B: 4 clients, Phase C: 5 managers, Phase D: 6 = protocol package counted as 1, plus conversation, channels, scratchpad, learning_scanner, git_helpers).
- Note any lessons accumulated specific to Phase D (e.g., the Message-union split decision: PAL kept a local union for type hints over both generic and PAL-specific message types).
- Update remaining-phases list (D removed, E/F/G/H still pending).

- [ ] **Step 2: Verify the umbrella spec is still accurate**

Read `docs/superpowers/specs/2026-04-25-agent-core-extraction-design.md`. If the storage convention or phase scope description from the umbrella spec is now stale because of Phase D's protocol split (which folded part of Phase E's scope into D), append a short Phase D outcome note.

- [ ] **Step 3: Final summary to user**

Report Phase D complete: PRs merged on agent_core (v0.4.0) and PAL, server migrated, all five known-flaky tests still ignored, agent_core now houses the daemon-protocol layer, only Phases E/F/G/H remain.

---

## Notes for the executing agent

- **Use the Grep tool, not bash grep.** Per project memory `feedback_use_grep_tool`, when searching for patterns across files, dispatch a subagent or use the Grep tool directly. Bash grep/rg is forbidden.
- **Never `git add -A` or `git add .` in the PAL repo.** Per memory `feedback_git_add_explicit`, stage files by explicit path. Many legitimately-untracked files exist.
- **The five known-flaky tests** (`test_daemon.py`, `test_integration.py`, `test_chat_research_integration.py`, `test_consolidate_integration.py`, `test_learning_e2e.py`) stay excluded in broad runs. They have pre-existing infrastructure issues unrelated to extraction work.
- **Quoted-string mock paths**: when sweeping imports, also search for `"pal.protocol.X"` and `'pal.protocol.X'` patterns in `monkeypatch.setattr` and `mock.patch` calls. Per the Phase C lesson, broad grep can miss these.
- **Worktree cleanup**: after PAL PR merge, remove `.worktrees/agent-core-phase-d` to avoid stale state.
- **No SSH from agent**: the server-side migration in Task 24 must be performed by the user. The agent provides the runbook only.
- **No em dashes** in any user-facing output. Per memory `feedback_no_em_dashes`.
