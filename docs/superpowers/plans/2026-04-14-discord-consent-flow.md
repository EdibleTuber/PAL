# Discord Consent Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Discord-native approval UX for both research and compile proposals: embed with Approve/Decline/Edit buttons, edit modal for structured field collection, thread for progress updates during execution.

**Architecture:** Replace `collect_response()` in the Discord adapter with a `DiscordStreamProcessor` that handles proposal messages inline, posts Discord UI, and routes button/modal interactions back to the daemon via the existing `client.send()` path. Triggerer-only consent enforced at interaction time. 15-minute timeout across both CLI and Discord surfaces.

**Tech Stack:** Python 3.11+, asyncio, pytest, discord.py 2.x, existing PAL modules (daemon, client, protocol, approval_registry).

**Spec:** `docs/superpowers/specs/2026-04-14-discord-consent-flow-design.md`

---

## Task 1: Add summary_paths field to ResearchApprovalResponseMessage

**Files:**
- Modify: `pal/protocol.py`
- Modify: `tests/test_protocol.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_protocol.py`:

```python
def test_research_approval_response_carries_summary_paths():
    msg = ResearchApprovalResponseMessage(
        proposal_id="abc",
        decision="edit",
        summary_paths=["raw/summaries/a.md", "raw/summaries/b.md"],
    )
    decoded = decode_message(encode_message(msg).strip())
    assert isinstance(decoded, ResearchApprovalResponseMessage)
    assert decoded.summary_paths == ["raw/summaries/a.md", "raw/summaries/b.md"]
    assert decoded.new_topic is None
    assert decoded.new_depth is None


def test_research_approval_response_summary_paths_defaults_to_none():
    msg = ResearchApprovalResponseMessage(
        proposal_id="abc",
        decision="approve",
    )
    decoded = decode_message(encode_message(msg).strip())
    assert decoded.summary_paths is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_protocol.py -v -k summary_paths`
Expected: FAIL — `TypeError: unexpected keyword argument 'summary_paths'`.

- [ ] **Step 3: Add the field**

In `pal/protocol.py`, modify `ResearchApprovalResponseMessage`:

```python
@dataclass
class ResearchApprovalResponseMessage:
    proposal_id: str
    decision: str  # "approve" | "decline" | "edit"
    new_topic: str | None = None
    new_depth: int | None = None
    summary_paths: list[str] | None = None
    type: str = "research_approval_response"
```

Only one line added (`summary_paths: list[str] | None = None`). Field goes before `type` (the discriminator must stay last).

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_protocol.py -v && .venv/bin/pytest -x`
Expected: new tests pass, full suite passes.

- [ ] **Step 5: Commit**

```bash
git add pal/protocol.py tests/test_protocol.py
git commit -m "feat: add summary_paths field to ResearchApprovalResponseMessage for compile-edit"
```

---

## Task 2: Lower default proposal expiry to 15 minutes

**Files:**
- Modify: `pal/approval_registry.py`
- Modify: `tests/test_approval_registry.py` (if any test hard-codes 30 minutes)

- [ ] **Step 1: Check for hard-coded 30 references**

Use the Grep tool with pattern `DEFAULT_EXPIRY_MINUTES|expiry_minutes=30|30.*minutes` on paths `pal/` and `tests/`. Identify any tests that would break if the default changes.

Spec-relevant sites to verify:
- `pal/approval_registry.py` — the `DEFAULT_EXPIRY_MINUTES = 30` constant.
- Any test that constructs `ApprovalRegistry()` without an explicit `expiry_minutes` kwarg and relies on the 30-minute window.

- [ ] **Step 2: Update the constant**

In `pal/approval_registry.py`, change:

```python
DEFAULT_EXPIRY_MINUTES = 30
```

to:

```python
DEFAULT_EXPIRY_MINUTES = 15
```

- [ ] **Step 3: Run tests**

Run: `.venv/bin/pytest -x`
Expected: all tests pass. The expiry-related tests in `tests/test_approval_registry.py` use `ApprovalRegistry(expiry_minutes=0)` explicitly (to test immediate expiry), so they aren't affected by the default change. If any test IS affected, fix it in place — prefer explicit `expiry_minutes=...` kwargs over relying on the default.

- [ ] **Step 4: Commit**

```bash
git add pal/approval_registry.py tests/test_approval_registry.py
git commit -m "feat: lower default proposal expiry to 15 minutes for CLI/Discord parity"
```

Drop `tests/test_approval_registry.py` from the stage if unchanged.

---

## Task 3: Daemon routes compile-edit via summary_paths

**Files:**
- Modify: `pal/daemon.py`
- Modify: `tests/test_chat_research_integration.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_chat_research_integration.py`:

```python
@pytest.mark.asyncio
async def test_route_compile_edit_replaces_summary_paths(tmp_path):
    """Daemon routes a compile-edit approval by passing summary_paths
    to ApprovalRegistry.edit, producing a new approved compile proposal
    with the replacement paths."""
    from pal.daemon import Daemon
    from pal.config import Config
    from pal.protocol import ResearchApprovalResponseMessage

    # We don't need a full daemon for this — just the routing helper.
    # Construct a throwaway Daemon-like object with only what _route_approval_response uses.
    registry = ApprovalRegistry()
    old_pid = registry.create_proposal(
        kind="compile",
        summary_paths=["raw/summaries/a.md"],
        rationale="r",
    )

    # Build a lightweight stub that has _route_approval_response as a bound method.
    class _Stub:
        _route_approval_response = Daemon._route_approval_response

    stub = _Stub()
    msg = ResearchApprovalResponseMessage(
        proposal_id=old_pid,
        decision="edit",
        summary_paths=["raw/summaries/a.md", "raw/summaries/b.md"],
    )
    stub._route_approval_response(msg, registry)

    # Old proposal is declined, and a new approved successor exists
    # with the replacement paths.
    old = registry.get(old_pid)
    assert old.status == "declined"
    new_pid = old.successor_id
    assert new_pid is not None
    new = registry.get(new_pid)
    assert new.status == "approved"
    assert new.kind == "compile"
    assert new.summary_paths == ["raw/summaries/a.md", "raw/summaries/b.md"]


@pytest.mark.asyncio
async def test_route_research_edit_still_uses_new_topic_and_depth(tmp_path):
    """Research-edit routing path is unchanged."""
    from pal.daemon import Daemon
    from pal.protocol import ResearchApprovalResponseMessage

    registry = ApprovalRegistry()
    old_pid = registry.create_proposal(
        topic="original",
        depth=3,
        rationale="r",
    )

    class _Stub:
        _route_approval_response = Daemon._route_approval_response

    stub = _Stub()
    msg = ResearchApprovalResponseMessage(
        proposal_id=old_pid,
        decision="edit",
        new_topic="refined",
        new_depth=5,
    )
    stub._route_approval_response(msg, registry)

    old = registry.get(old_pid)
    new_pid = old.successor_id
    new = registry.get(new_pid)
    assert new.kind == "research"
    assert new.topic == "refined"
    assert new.depth == 5
```

Ensure `ApprovalRegistry` is imported at the top of the file if not already.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_chat_research_integration.py -v -k "route_compile_edit or route_research_edit"`
Expected: the compile test FAILs because the current `_route_approval_response` ignores `summary_paths`. The research test may pass or fail depending on current wording — both should pass after the change.

- [ ] **Step 3: Update _route_approval_response**

Use the Grep tool to find `_route_approval_response` in `pal/daemon.py`. Replace the body with:

```python
    def _route_approval_response(
        self,
        msg: ResearchApprovalResponseMessage,
        registry: ApprovalRegistry,
    ) -> None:
        if msg.decision == "approve":
            registry.approve(msg.proposal_id)
        elif msg.decision == "decline":
            registry.decline(msg.proposal_id)
        elif msg.decision == "edit":
            if msg.summary_paths is not None:
                registry.edit(msg.proposal_id, summary_paths=msg.summary_paths)
            else:
                registry.edit(
                    msg.proposal_id,
                    new_topic=msg.new_topic or None,
                    new_depth=msg.new_depth or None,
                )
```

The two-branch edit path matches the spec: compile-edit carries `summary_paths`, research-edit carries `new_topic`/`new_depth`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_chat_research_integration.py -v && .venv/bin/pytest -x`
Expected: both new tests pass, full suite passes.

- [ ] **Step 5: Commit**

```bash
git add pal/daemon.py tests/test_chat_research_integration.py
git commit -m "feat: daemon routes compile-edit approval responses via summary_paths"
```

---

## Task 4: ProposalContext and pure embed/modal builders

**Files:**
- Create: `pal/discord_interactions.py`
- Create: `tests/test_discord_interactions.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_discord_interactions.py`:

```python
from pal.discord_interactions import (
    ProposalContext,
    build_research_proposal_embed,
    build_compile_proposal_embed,
)
from pal.protocol import ResearchProposalMessage, CompileProposalMessage


def test_research_embed_has_title_and_fields():
    msg = ResearchProposalMessage(
        proposal_id="abc-123",
        topic="prompt injection in MCP",
        depth=3,
        rationale="vault has no sources",
    )
    embed, view = build_research_proposal_embed(msg)
    assert "research" in embed.title.lower()
    # Field values present
    field_values = {f.name: f.value for f in embed.fields}
    assert field_values.get("Topic") == "prompt injection in MCP"
    assert field_values.get("Depth") == "3"
    assert field_values.get("Rationale") == "vault has no sources"


def test_research_embed_view_has_three_buttons_with_proposal_id():
    msg = ResearchProposalMessage(
        proposal_id="abc-123",
        topic="t",
        depth=3,
        rationale="r",
    )
    embed, view = build_research_proposal_embed(msg)
    custom_ids = [child.custom_id for child in view.children]
    assert "research:approve:abc-123" in custom_ids
    assert "research:decline:abc-123" in custom_ids
    assert "research:edit:abc-123" in custom_ids


def test_compile_embed_includes_truncated_path_list_and_buttons():
    paths = [f"raw/summaries/file-{i}.md" for i in range(15)]
    msg = CompileProposalMessage(
        proposal_id="xyz",
        summary_paths=paths,
        rationale="promote findings",
    )
    embed, view = build_compile_proposal_embed(msg)
    assert "compile" in embed.title.lower()
    summaries_field = next(
        f for f in embed.fields if "Summaries" in f.name
    )
    assert "raw/summaries/file-0.md" in summaries_field.value
    # With 15 paths and a 10-entry cap, a "+N more" indicator appears
    assert "+5 more" in summaries_field.value or "+5" in summaries_field.value
    custom_ids = [child.custom_id for child in view.children]
    assert "compile:approve:xyz" in custom_ids
    assert "compile:decline:xyz" in custom_ids
    assert "compile:edit:xyz" in custom_ids


def test_proposal_context_preserves_fields():
    ctx = ProposalContext(
        proposal_id="abc",
        kind="research",
        triggerer_id="user-42",
        topic="t",
        depth=3,
        rationale="r",
    )
    assert ctx.proposal_id == "abc"
    assert ctx.kind == "research"
    assert ctx.triggerer_id == "user-42"


def test_proposal_context_for_compile():
    ctx = ProposalContext(
        proposal_id="xyz",
        kind="compile",
        triggerer_id="user-42",
        summary_paths=["raw/summaries/a.md"],
        rationale="r",
    )
    assert ctx.summary_paths == ["raw/summaries/a.md"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_discord_interactions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pal.discord_interactions'`.

- [ ] **Step 3: Create pal/discord_interactions.py with builders**

Create `pal/discord_interactions.py`:

```python
"""Discord interactions: proposal embeds, modals, stream processor.

Separated from pal.discord_adapter so the adapter file stays focused on
connection lifecycle. This module handles all UI-side concerns of
consent-gated tool flows (ResearchProposalMessage, CompileProposalMessage,
their responses).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

import discord

from pal.protocol import (
    CompileProposalMessage,
    ResearchProposalMessage,
)

ProposalKind = Literal["research", "compile"]

# Cap the number of summary paths shown in the compile embed to keep the
# field value under Discord's 1024-char limit.
_COMPILE_PATHS_DISPLAY_CAP = 10


@dataclass
class ProposalContext:
    """Per-proposal state kept on PalDiscordBot.active_proposals.

    Used by the interaction handler to (a) authorize clicks (only the
    triggerer), (b) populate edit-modal defaults, and (c) reference the
    original proposal message for later edits (disabling buttons, status
    updates).
    """
    proposal_id: str
    kind: ProposalKind
    triggerer_id: str
    rationale: str = ""
    # Research-specific
    topic: str = ""
    depth: int = 3
    # Compile-specific
    summary_paths: list[str] = field(default_factory=list)
    # Set by the stream processor after posting; None until then.
    discord_message_id: Optional[int] = None
    channel_id: Optional[int] = None
    thread_id: Optional[int] = None


def build_research_proposal_embed(
    msg: ResearchProposalMessage,
) -> tuple[discord.Embed, discord.ui.View]:
    """Pure builder: returns the embed and a View with three buttons."""
    embed = discord.Embed(
        title="PAL proposes research",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Topic", value=msg.topic, inline=False)
    embed.add_field(name="Depth", value=str(msg.depth), inline=True)
    embed.add_field(name="Rationale", value=msg.rationale, inline=False)

    view = discord.ui.View(timeout=None)  # we manage expiry via the registry
    view.add_item(discord.ui.Button(
        style=discord.ButtonStyle.success,
        label="Approve",
        emoji="✅",
        custom_id=f"research:approve:{msg.proposal_id}",
    ))
    view.add_item(discord.ui.Button(
        style=discord.ButtonStyle.danger,
        label="Decline",
        emoji="❌",
        custom_id=f"research:decline:{msg.proposal_id}",
    ))
    view.add_item(discord.ui.Button(
        style=discord.ButtonStyle.secondary,
        label="Edit",
        emoji="✏️",
        custom_id=f"research:edit:{msg.proposal_id}",
    ))
    return embed, view


def build_compile_proposal_embed(
    msg: CompileProposalMessage,
) -> tuple[discord.Embed, discord.ui.View]:
    """Pure builder: returns the embed and a View with three buttons."""
    embed = discord.Embed(
        title="PAL proposes compile",
        color=discord.Color.green(),
    )
    total = len(msg.summary_paths)
    shown = msg.summary_paths[:_COMPILE_PATHS_DISPLAY_CAP]
    paths_text = "\n".join(shown)
    if total > _COMPILE_PATHS_DISPLAY_CAP:
        paths_text += f"\n+{total - _COMPILE_PATHS_DISPLAY_CAP} more"
    embed.add_field(
        name=f"Summaries ({total})",
        value=paths_text,
        inline=False,
    )
    embed.add_field(name="Rationale", value=msg.rationale, inline=False)

    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        style=discord.ButtonStyle.success,
        label="Approve",
        emoji="✅",
        custom_id=f"compile:approve:{msg.proposal_id}",
    ))
    view.add_item(discord.ui.Button(
        style=discord.ButtonStyle.danger,
        label="Decline",
        emoji="❌",
        custom_id=f"compile:decline:{msg.proposal_id}",
    ))
    view.add_item(discord.ui.Button(
        style=discord.ButtonStyle.secondary,
        label="Edit",
        emoji="✏️",
        custom_id=f"compile:edit:{msg.proposal_id}",
    ))
    return embed, view
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_discord_interactions.py -v && .venv/bin/pytest -x`
Expected: 5 new tests pass, no regressions.

- [ ] **Step 5: Commit**

```bash
git add pal/discord_interactions.py tests/test_discord_interactions.py
git commit -m "feat: ProposalContext and proposal embed builders"
```

---

## Task 5: Edit modal builders

**Files:**
- Modify: `pal/discord_interactions.py`
- Modify: `tests/test_discord_interactions.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_discord_interactions.py`:

```python
from pal.discord_interactions import (
    build_research_edit_modal,
    build_compile_edit_modal,
)


def test_research_edit_modal_has_topic_and_depth_inputs_with_defaults():
    ctx = ProposalContext(
        proposal_id="abc",
        kind="research",
        triggerer_id="u1",
        topic="original topic",
        depth=4,
        rationale="r",
    )
    modal = build_research_edit_modal(ctx)
    assert modal.custom_id == "research:abc"
    labels = [c.label for c in modal.children]
    assert "New topic" in labels
    assert "New depth" in labels
    # Defaults populated
    topic_input = next(c for c in modal.children if c.label == "New topic")
    depth_input = next(c for c in modal.children if c.label == "New depth")
    assert topic_input.default == "original topic"
    assert depth_input.default == "4"


def test_compile_edit_modal_has_paths_input_with_default():
    ctx = ProposalContext(
        proposal_id="xyz",
        kind="compile",
        triggerer_id="u1",
        summary_paths=["raw/summaries/a.md", "raw/summaries/b.md"],
        rationale="r",
    )
    modal = build_compile_edit_modal(ctx)
    assert modal.custom_id == "compile:xyz"
    labels = [c.label for c in modal.children]
    assert any("paths" in l.lower() for l in labels)
    paths_input = modal.children[0]
    assert "raw/summaries/a.md" in paths_input.default
    assert "raw/summaries/b.md" in paths_input.default
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_discord_interactions.py -v -k "edit_modal"`
Expected: FAIL — `cannot import name 'build_research_edit_modal'`.

- [ ] **Step 3: Add modal builders**

Append to `pal/discord_interactions.py`:

```python
def build_research_edit_modal(ctx: ProposalContext) -> discord.ui.Modal:
    """Research-edit modal: new topic + new depth, with current values
    as defaults. custom_id format: 'research:<proposal_id>' so the
    submit handler can route by proposal_id."""
    modal = discord.ui.Modal(
        title="Edit research proposal",
        custom_id=f"research:{ctx.proposal_id}",
    )
    modal.add_item(discord.ui.TextInput(
        label="New topic",
        style=discord.TextStyle.paragraph,
        default=ctx.topic,
        required=True,
        max_length=200,
    ))
    modal.add_item(discord.ui.TextInput(
        label="New depth",
        style=discord.TextStyle.short,
        default=str(ctx.depth),
        required=False,
        placeholder="3",
        max_length=3,
    ))
    return modal


def build_compile_edit_modal(ctx: ProposalContext) -> discord.ui.Modal:
    """Compile-edit modal: paragraph-text field for newline-separated
    summary paths, defaulting to the current path list."""
    modal = discord.ui.Modal(
        title="Edit compile proposal",
        custom_id=f"compile:{ctx.proposal_id}",
    )
    default_paths = "\n".join(ctx.summary_paths) if ctx.summary_paths else ""
    modal.add_item(discord.ui.TextInput(
        label="Summary paths (one per line)",
        style=discord.TextStyle.paragraph,
        default=default_paths,
        required=True,
    ))
    return modal
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_discord_interactions.py -v && .venv/bin/pytest -x`
Expected: 2 new tests pass, no regressions.

- [ ] **Step 5: Commit**

```bash
git add pal/discord_interactions.py tests/test_discord_interactions.py
git commit -m "feat: edit modal builders for research and compile proposals"
```

---

## Task 6: DiscordStreamProcessor

Stream processor replaces `collect_response`. Handles both proposal-involved and non-proposal chat turns. Non-proposal behavior mirrors the old `collect_response` (progress accumulated and returned alongside final text). Proposal-involved behavior posts the embed, creates a thread lazily for progress, and returns the final text separately.

**Files:**
- Modify: `pal/discord_interactions.py`
- Modify: `tests/test_discord_interactions.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_discord_interactions.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from pal.discord_interactions import DiscordStreamProcessor
from pal.protocol import (
    ResponseMessage,
    StreamChunkMessage,
    ToolProgressMessage,
)


@pytest.mark.asyncio
async def test_stream_processor_plain_chat_returns_final_text():
    """Non-proposal chat: accumulate progress, return (progress, final_text).
    Matches the legacy collect_response shape."""
    channel = MagicMock()
    bot = MagicMock()
    bot.active_proposals = {}
    client = MagicMock()

    processor = DiscordStreamProcessor(
        channel=channel,
        triggerer_id="user-1",
        bot=bot,
        client=client,
    )

    async def stream():
        yield ToolProgressMessage(tool="read_file", arguments={"path": "foo.md"})
        yield StreamChunkMessage(token="Hello ")
        yield StreamChunkMessage(token="world")
        yield ResponseMessage(text="")

    progress, final_text = await processor.run(stream())
    assert final_text == "Hello world"
    assert len(progress) == 1
    assert progress[0].tool == "read_file"
    # No embed posted for plain chat
    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_stream_processor_posts_research_proposal_and_records_context():
    channel = MagicMock()
    bot = MagicMock()
    bot.active_proposals = {}
    bot.connections = MagicMock()
    client = MagicMock()

    # The message returned by channel.send(...) needs an id and .create_thread.
    posted_message = MagicMock()
    posted_message.id = 555
    posted_message.create_thread = AsyncMock()
    channel.send = AsyncMock(return_value=posted_message)
    channel.id = 100

    processor = DiscordStreamProcessor(
        channel=channel,
        triggerer_id="user-1",
        bot=bot,
        client=client,
    )

    async def stream():
        yield ResearchProposalMessage(
            proposal_id="abc",
            topic="t",
            depth=3,
            rationale="r",
        )
        yield ResponseMessage(text="")

    progress, final_text = await processor.run(stream())
    # Proposal posted
    channel.send.assert_awaited_once()
    # Context recorded on bot.active_proposals
    ctx = bot.active_proposals.get("abc")
    assert ctx is not None
    assert ctx.kind == "research"
    assert ctx.triggerer_id == "user-1"
    assert ctx.topic == "t"
    assert ctx.discord_message_id == 555
    assert ctx.channel_id == 100


@pytest.mark.asyncio
async def test_stream_processor_posts_progress_to_thread_after_proposal():
    """Once a proposal has been posted, subsequent progress events route
    to the thread (created lazily on first progress event), not the
    channel's main stream."""
    channel = MagicMock()
    bot = MagicMock()
    bot.active_proposals = {}
    client = MagicMock()

    thread_mock = MagicMock()
    thread_mock.send = AsyncMock()
    posted_message = MagicMock()
    posted_message.id = 777
    posted_message.create_thread = AsyncMock(return_value=thread_mock)
    channel.send = AsyncMock(return_value=posted_message)
    channel.id = 200

    processor = DiscordStreamProcessor(
        channel=channel,
        triggerer_id="u1",
        bot=bot,
        client=client,
    )

    async def stream():
        yield ResearchProposalMessage(
            proposal_id="p1", topic="t", depth=3, rationale="r",
        )
        yield ToolProgressMessage(
            tool="research_topic",
            arguments={"status": "Researching: t"},
        )
        yield ResponseMessage(text="done")

    progress, final_text = await processor.run(stream())
    # Thread was created once
    posted_message.create_thread.assert_awaited_once()
    # Progress posted into the thread, not the channel
    thread_mock.send.assert_awaited()
    # Returned progress list is empty for proposal-mode runs (all progress
    # went to the thread; nothing to prepend to the final text).
    assert progress == []
    assert final_text == "done"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_discord_interactions.py -v -k stream_processor`
Expected: FAIL — `cannot import name 'DiscordStreamProcessor'`.

- [ ] **Step 3: Add DiscordStreamProcessor**

Append to `pal/discord_interactions.py`:

```python
from collections.abc import AsyncGenerator
from typing import Any

from pal.protocol import (
    ErrorMessage,
    Message,
    ResponseMessage,
    StreamChunkMessage,
    ToolProgressMessage,
)


class DiscordStreamProcessor:
    """Consume a chat stream from the daemon, posting Discord UI for
    proposal messages inline.

    Two modes:
    - Plain chat (no proposals): accumulates progress events, returns
      them alongside the final text so the adapter can prepend them to
      the user-visible reply. Matches the legacy collect_response()
      shape.
    - Proposal-involved chat: once a proposal message arrives, posts
      the embed + buttons to the channel, creates a thread lazily on
      the first subsequent progress event, and routes further progress
      to the thread. Final text still returns; the adapter posts it
      as a channel reply to the proposal message.
    """

    def __init__(
        self,
        channel: discord.abc.Messageable,
        triggerer_id: str,
        bot: Any,          # PalDiscordBot; using Any to avoid circular import
        client: Any,       # PalClient
    ) -> None:
        self.channel = channel
        self.triggerer_id = triggerer_id
        self.bot = bot
        self.client = client
        # Populated when a proposal is seen
        self.current_proposal_id: Optional[str] = None
        self.current_proposal_message: Optional[discord.Message] = None
        self.current_thread: Optional[discord.Thread] = None

    async def run(
        self,
        stream: AsyncGenerator[Message, None],
    ) -> tuple[list[ToolProgressMessage], str]:
        progress_buffer: list[ToolProgressMessage] = []
        text_buffer: list[str] = []
        final_text = ""

        async for msg in stream:
            if isinstance(msg, ResearchProposalMessage):
                await self._handle_research_proposal(msg)
            elif isinstance(msg, CompileProposalMessage):
                await self._handle_compile_proposal(msg)
            elif isinstance(msg, ToolProgressMessage):
                if self.current_proposal_id is not None:
                    await self._post_progress_to_thread(msg)
                else:
                    progress_buffer.append(msg)
            elif isinstance(msg, StreamChunkMessage):
                text_buffer.append(msg.token)
            elif isinstance(msg, ResponseMessage):
                final_text = "".join(text_buffer) if text_buffer else msg.text
                break
            elif isinstance(msg, ErrorMessage):
                final_text = f"Error: {msg.error}"
                break

        return progress_buffer, final_text

    async def _handle_research_proposal(
        self, msg: ResearchProposalMessage,
    ) -> None:
        embed, view = build_research_proposal_embed(msg)
        posted = await self.channel.send(embed=embed, view=view)
        ctx = ProposalContext(
            proposal_id=msg.proposal_id,
            kind="research",
            triggerer_id=self.triggerer_id,
            rationale=msg.rationale,
            topic=msg.topic,
            depth=msg.depth,
            discord_message_id=posted.id,
            channel_id=getattr(self.channel, "id", None),
        )
        self.bot.active_proposals[msg.proposal_id] = ctx
        self.current_proposal_id = msg.proposal_id
        self.current_proposal_message = posted

    async def _handle_compile_proposal(
        self, msg: CompileProposalMessage,
    ) -> None:
        embed, view = build_compile_proposal_embed(msg)
        posted = await self.channel.send(embed=embed, view=view)
        ctx = ProposalContext(
            proposal_id=msg.proposal_id,
            kind="compile",
            triggerer_id=self.triggerer_id,
            rationale=msg.rationale,
            summary_paths=list(msg.summary_paths),
            discord_message_id=posted.id,
            channel_id=getattr(self.channel, "id", None),
        )
        self.bot.active_proposals[msg.proposal_id] = ctx
        self.current_proposal_id = msg.proposal_id
        self.current_proposal_message = posted

    async def _post_progress_to_thread(
        self, msg: ToolProgressMessage,
    ) -> None:
        """Lazily create a thread on the proposal message; post progress
        into it. On thread creation failure (e.g., permission denied),
        fall back to posting directly to the channel as an italic line."""
        if self.current_thread is None and self.current_proposal_message is not None:
            try:
                name = self._thread_name_for_current_proposal()
                self.current_thread = await self.current_proposal_message.create_thread(
                    name=name,
                )
                # Remember thread_id on the context
                ctx = self.bot.active_proposals.get(self.current_proposal_id)
                if ctx is not None:
                    ctx.thread_id = self.current_thread.id
            except discord.HTTPException:
                # Permission denied or API error; fall back to channel
                self.current_thread = None

        from pal.discord_adapter import format_tool_progress
        label = format_tool_progress(msg.tool, msg.arguments)
        if self.current_thread is not None:
            try:
                await self.current_thread.send(label)
                return
            except discord.HTTPException:
                pass
        # Fallback: channel
        try:
            await self.channel.send(label)
        except discord.HTTPException:
            pass

    def _thread_name_for_current_proposal(self) -> str:
        if self.current_proposal_id is None:
            return "progress"
        ctx = self.bot.active_proposals.get(self.current_proposal_id)
        if ctx is None:
            return "progress"
        if ctx.kind == "research":
            return f"research: {ctx.topic[:80]}"
        return f"compile: {len(ctx.summary_paths)} summaries"
```

Also ensure `from pal.protocol import ResearchProposalMessage` is already imported at the top of the file (it is, from Task 4's initial imports). If `Optional` was imported earlier, reuse; otherwise add `from typing import Any, Optional`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_discord_interactions.py -v && .venv/bin/pytest -x`
Expected: 3 new stream-processor tests pass, no regressions.

- [ ] **Step 5: Commit**

```bash
git add pal/discord_interactions.py tests/test_discord_interactions.py
git commit -m "feat: DiscordStreamProcessor handles proposals with thread progress"
```

---

## Task 7: Interaction routing (buttons and modal submit)

Button clicks and modal submits arrive through `discord.Client.on_interaction`. This task adds the routing logic. It lives on `PalDiscordBot` for access to `self.active_proposals` and `self.connections` — the implementation calls pure helpers defined in `discord_interactions.py`.

**Files:**
- Modify: `pal/discord_interactions.py` (add pure routing helpers)
- Modify: `tests/test_discord_interactions.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_discord_interactions.py`:

```python
from pal.discord_interactions import (
    parse_button_custom_id,
    parse_modal_custom_id,
    extract_modal_field_values,
)


def test_parse_button_custom_id_research_approve():
    kind, action, proposal_id = parse_button_custom_id("research:approve:abc-123")
    assert kind == "research"
    assert action == "approve"
    assert proposal_id == "abc-123"


def test_parse_button_custom_id_compile_edit():
    kind, action, proposal_id = parse_button_custom_id("compile:edit:xyz")
    assert kind == "compile"
    assert action == "edit"
    assert proposal_id == "xyz"


def test_parse_button_custom_id_invalid_returns_none():
    assert parse_button_custom_id("") is None
    assert parse_button_custom_id("bogus") is None
    assert parse_button_custom_id("research:approve") is None  # only 2 parts
    assert parse_button_custom_id("wrong:kind:abc") is None


def test_parse_modal_custom_id():
    kind, proposal_id = parse_modal_custom_id("research:abc-123")
    assert kind == "research"
    assert proposal_id == "abc-123"
    kind, proposal_id = parse_modal_custom_id("compile:xyz")
    assert kind == "compile"
    assert proposal_id == "xyz"


def test_parse_modal_custom_id_invalid_returns_none():
    assert parse_modal_custom_id("bogus") is None
    assert parse_modal_custom_id("") is None


def test_extract_modal_field_values_multi_field():
    """Given discord.py's modal submit interaction.data shape, extract
    the text values in order, one per action-row."""
    interaction_data = {
        "components": [
            {"components": [{"value": "new topic text"}]},
            {"components": [{"value": "5"}]},
        ],
    }
    values = extract_modal_field_values(interaction_data)
    assert values == ["new topic text", "5"]


def test_extract_modal_field_values_single_field():
    interaction_data = {
        "components": [
            {"components": [{"value": "path1\npath2\npath3"}]},
        ],
    }
    values = extract_modal_field_values(interaction_data)
    assert values == ["path1\npath2\npath3"]


def test_extract_modal_field_values_handles_missing():
    assert extract_modal_field_values({}) == []
    assert extract_modal_field_values({"components": []}) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_discord_interactions.py -v -k "custom_id or modal_field"`
Expected: FAIL — those helpers don't exist yet.

- [ ] **Step 3: Add routing helpers**

Append to `pal/discord_interactions.py`:

```python
def parse_button_custom_id(
    cid: str,
) -> Optional[tuple[ProposalKind, str, str]]:
    """Parse 'research:approve:abc-123' into ('research', 'approve', 'abc-123').
    Returns None for malformed input."""
    parts = cid.split(":", 2)
    if len(parts) != 3:
        return None
    kind, action, proposal_id = parts
    if kind not in ("research", "compile"):
        return None
    if action not in ("approve", "decline", "edit"):
        return None
    if not proposal_id:
        return None
    return kind, action, proposal_id  # type: ignore[return-value]


def parse_modal_custom_id(cid: str) -> Optional[tuple[ProposalKind, str]]:
    """Parse 'research:abc-123' into ('research', 'abc-123')."""
    parts = cid.split(":", 1)
    if len(parts) != 2:
        return None
    kind, proposal_id = parts
    if kind not in ("research", "compile"):
        return None
    if not proposal_id:
        return None
    return kind, proposal_id  # type: ignore[return-value]


def extract_modal_field_values(interaction_data: dict) -> list[str]:
    """Pull text values out of discord.py's modal interaction.data.

    Shape: interaction_data['components'] is a list of action-row dicts,
    each containing its own 'components' list with text-input dicts
    carrying a 'value' field. We flatten to a list of values in order.
    """
    rows = interaction_data.get("components", [])
    values: list[str] = []
    for row in rows:
        inner = row.get("components", [])
        for component in inner:
            values.append(component.get("value", ""))
    return values
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_discord_interactions.py -v && .venv/bin/pytest -x`
Expected: 8 new tests pass, no regressions.

- [ ] **Step 5: Commit**

```bash
git add pal/discord_interactions.py tests/test_discord_interactions.py
git commit -m "feat: pure helpers for Discord interaction id parsing and modal field extraction"
```

---

## Task 8: Wire Discord adapter to use the new stream processor and interactions

Replaces `collect_response` with `DiscordStreamProcessor`. Adds `active_proposals` on the bot. Adds `on_interaction` override that authorizes, parses, and routes button/modal events using the helpers from Task 7. On approve/decline clicks, sends `ResearchApprovalResponseMessage` via `client.send()`. On edit clicks, sends a modal. On modal submits, parses values and sends the appropriate edit response.

**Files:**
- Modify: `pal/discord_adapter.py`

This is the largest single-file change in the plan. It's not easily TDD'd because it requires a live Discord event loop; correctness is validated by the existing unit tests on helpers and by the manual smoke test in Task 9.

- [ ] **Step 1: Use the Grep tool to locate `collect_response` and `PalDiscordBot`**

Find the existing definitions in `pal/discord_adapter.py`. Note the shape of `on_message` which currently calls `collect_response`.

- [ ] **Step 2: Update imports**

At the top of `pal/discord_adapter.py`, add imports from the new module:

```python
from pal.discord_interactions import (
    DiscordStreamProcessor,
    ProposalContext,
    build_research_edit_modal,
    build_compile_edit_modal,
    parse_button_custom_id,
    parse_modal_custom_id,
    extract_modal_field_values,
)
```

Also import `ResearchApprovalResponseMessage` (the adapter currently imports several protocol types; add this to the existing import block):

```python
from pal.protocol import (
    StreamChunkMessage,
    ResponseMessage,
    ErrorMessage,
    ToolProgressMessage,
    ResearchApprovalResponseMessage,
)
```

- [ ] **Step 3: Add active_proposals to PalDiscordBot.__init__**

In `PalDiscordBot.__init__`, after existing attribute setup:

```python
        self.active_proposals: dict[str, ProposalContext] = {}
```

- [ ] **Step 4: Replace collect_response usage in on_message**

In `on_message`, find the block that currently does:

```python
                    _, chat_text = parsed
                    progress, reply_text = await collect_response(client.chat(chat_text))
                    if progress:
                        progress_lines = "\n".join(
                            format_tool_progress(p.tool, p.arguments) for p in progress
                        )
                        reply_text = f"{progress_lines}\n\n{reply_text}"
```

Replace with:

```python
                    _, chat_text = parsed
                    processor = DiscordStreamProcessor(
                        channel=message.channel,
                        triggerer_id=user_id,
                        bot=self,
                        client=client,
                    )
                    progress, reply_text = await processor.run(client.chat(chat_text))
                    if progress:
                        progress_lines = "\n".join(
                            format_tool_progress(p.tool, p.arguments) for p in progress
                        )
                        reply_text = f"{progress_lines}\n\n{reply_text}"
```

The existing `collect_response` function can be removed from the file — it's superseded. Use the Grep tool to verify no other callers reference it.

- [ ] **Step 5: Add on_interaction to PalDiscordBot**

Append a new method to `PalDiscordBot`:

```python
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Route button clicks and modal submits to the appropriate handler."""
        if interaction.type == discord.InteractionType.component:
            await self._handle_button_interaction(interaction)
        elif interaction.type == discord.InteractionType.modal_submit:
            await self._handle_modal_submit(interaction)

    async def _handle_button_interaction(
        self, interaction: discord.Interaction,
    ) -> None:
        cid = interaction.data.get("custom_id", "") if interaction.data else ""
        parsed = parse_button_custom_id(cid)
        if parsed is None:
            return
        kind, action, proposal_id = parsed

        ctx = self.active_proposals.get(proposal_id)
        if ctx is None:
            await interaction.response.send_message(
                "This proposal is no longer active.", ephemeral=True,
            )
            return
        if str(interaction.user.id) != ctx.triggerer_id:
            await interaction.response.send_message(
                f"This proposal is for <@{ctx.triggerer_id}>.", ephemeral=True,
            )
            return

        if action == "edit":
            if kind == "research":
                modal = build_research_edit_modal(ctx)
            else:
                modal = build_compile_edit_modal(ctx)
            await interaction.response.send_modal(modal)
            return

        # approve or decline
        try:
            client = await self.connections.get_client(str(interaction.user.id))
            await client.send(ResearchApprovalResponseMessage(
                proposal_id=proposal_id,
                decision=action,
            ))
        except Exception as exc:
            logger.exception("Failed to send approval response: %s", exc)
            await interaction.response.send_message(
                "Something went wrong sending your decision. Try again.",
                ephemeral=True,
            )
            return

        status_text = {
            "approve": "✅ Approved, running — see thread for progress",
            "decline": "❌ Declined",
        }[action]
        try:
            await interaction.response.edit_message(content=status_text, view=None)
        except discord.HTTPException:
            pass
        # Clean up the active_proposals entry on terminal actions
        if action == "decline":
            self.active_proposals.pop(proposal_id, None)

    async def _handle_modal_submit(
        self, interaction: discord.Interaction,
    ) -> None:
        cid = interaction.data.get("custom_id", "") if interaction.data else ""
        parsed = parse_modal_custom_id(cid)
        if parsed is None:
            return
        kind, proposal_id = parsed

        ctx = self.active_proposals.get(proposal_id)
        if ctx is None or str(interaction.user.id) != ctx.triggerer_id:
            await interaction.response.defer()
            return

        values = extract_modal_field_values(interaction.data)

        if kind == "research":
            new_topic = values[0].strip() if len(values) >= 1 else ""
            new_depth_raw = values[1].strip() if len(values) >= 2 else ""
            if not new_topic:
                # Empty topic from the modal: treat as decline
                response = ResearchApprovalResponseMessage(
                    proposal_id=proposal_id, decision="decline",
                )
            else:
                try:
                    new_depth = int(new_depth_raw) if new_depth_raw else ctx.depth
                except ValueError:
                    new_depth = ctx.depth
                response = ResearchApprovalResponseMessage(
                    proposal_id=proposal_id,
                    decision="edit",
                    new_topic=new_topic,
                    new_depth=new_depth,
                )
        else:  # compile
            raw = values[0] if len(values) >= 1 else ""
            paths = [line.strip() for line in raw.splitlines() if line.strip()]
            if not paths:
                response = ResearchApprovalResponseMessage(
                    proposal_id=proposal_id, decision="decline",
                )
            else:
                response = ResearchApprovalResponseMessage(
                    proposal_id=proposal_id,
                    decision="edit",
                    summary_paths=paths,
                )

        try:
            client = await self.connections.get_client(str(interaction.user.id))
            await client.send(response)
        except Exception as exc:
            logger.exception("Failed to send modal response: %s", exc)
            await interaction.response.send_message(
                "Something went wrong sending your edit. Try again.",
                ephemeral=True,
            )
            return

        status_text = (
            "❌ Declined" if response.decision == "decline"
            else "✏️ Edited, running — see thread for progress"
        )
        try:
            await interaction.response.edit_message(content=status_text, view=None)
        except discord.HTTPException:
            # Modal submits sometimes need a defer rather than edit_message;
            # fall back gracefully.
            try:
                await interaction.response.defer()
            except discord.HTTPException:
                pass
```

- [ ] **Step 6: Run tests**

Run: `.venv/bin/pytest -x`
Expected: all existing tests pass. There is no new automated test for the adapter wiring itself — the unit tests on `DiscordStreamProcessor`, button/modal id parsing, and field extraction (Tasks 4-7) cover the logic. The live integration is validated by the smoke test in Task 9.

Use the Grep tool to verify `collect_response` has no remaining callers in the codebase after removal — if any test file imports it, decide whether to remove that test or migrate it to exercise `DiscordStreamProcessor` directly.

- [ ] **Step 7: Commit**

```bash
git add pal/discord_adapter.py
git commit -m "feat: wire Discord adapter to DiscordStreamProcessor and on_interaction"
```

---

## Task 9: Discord bot intents — verify direct messages

The existing `pal/discord_main.py` uses `discord.Intents.default()`. Per Discord docs, `Intents.default()` includes `direct_messages=True`. We want to make this explicit to prevent accidental stripping in future edits.

**Files:**
- Modify: `pal/discord_adapter.py` (the `PalDiscordBot.__init__` is where intents are set)

- [ ] **Step 1: Use the Grep tool to find the intents setup**

Find where `discord.Intents` is configured in `pal/discord_adapter.py`. The current code sets `intents.message_content = True`.

- [ ] **Step 2: Add explicit direct_messages setting**

Modify the intents setup:

```python
        intents = discord.Intents.default()
        intents.message_content = True
        intents.direct_messages = True  # explicit; default() already includes this
```

The added line is a no-op in practice (default already has it) but explicit documentation for future maintainers.

- [ ] **Step 3: Run tests**

Run: `.venv/bin/pytest -x`
Expected: all tests pass. This is a declarative-only change.

- [ ] **Step 4: Commit**

```bash
git add pal/discord_adapter.py
git commit -m "chore: make direct_messages intent explicit in PalDiscordBot"
```

---

## Task 10: Manual smoke test

**Files:** none modified

Run against a real Discord server and live PAL daemon.

- [ ] **Step 1: Pull latest and restart the services**

```bash
cd /home/edible/Projects/PAL
git pull
# Restart daemon and Discord bot (deployment-specific: systemd, tmux, etc.)
```

- [ ] **Step 2: Research happy path in a channel**

@mention PAL in a channel:

```
@PAL research indirect prompt injection in MCP
```

Expected:
1. PAL responds with a research proposal embed: title "PAL proposes research", topic/depth/rationale fields, three buttons (Approve/Decline/Edit).
2. Click ✅ Approve.
3. Message edits to "✅ Approved, running — see thread for progress."
4. Thread appears on the message named like `research: indirect prompt injection...`.
5. Progress messages post into the thread as italic lines (Researching, Fetching, Summarizing).
6. Final message replies in the main channel with the research summary and summary paths.

- [ ] **Step 3: Decline flow**

@mention PAL:

```
@PAL research something I don't actually want
```

At the proposal prompt, click ❌ Decline.

Expected: message edits to "❌ Declined", buttons disappear, PAL's next reply in the channel explains the decline.

- [ ] **Step 4: Edit flow — research**

@mention PAL:

```
@PAL research AI and home automation
```

At the proposal, click ✏️ Edit. A modal appears with "New topic" (defaulting to the current topic) and "New depth" fields. Change the topic, submit.

Expected: modal closes, proposal message edits to "✏️ Edited, running — see thread for progress", research runs with the new topic.

- [ ] **Step 5: Compile flow**

Assuming the research from step 2 produced summaries, trigger compile:

```
@PAL please add those research findings to the vault
```

Expected: PAL emits a compile proposal embed listing the summary paths with Approve/Decline/Edit buttons. Approve. A thread is created for compile progress. Compile runs, writes wiki articles, archives raw summaries.

- [ ] **Step 6: Compile edit flow**

Trigger a compile as above, click ✏️ Edit. The modal appears with paths listed one per line, defaulting to the proposed paths. Remove one path, submit.

Expected: compile runs with the remaining paths.

- [ ] **Step 7: Triggerer-only authorization**

With a second Discord account (also on the allowlist) in the same channel:

- Account A: `@PAL research X`
- Account B: tries to click Approve on Account A's proposal.

Expected: Account B sees an ephemeral message: "This proposal is for <@account-a-id>." No approval sent to the daemon.

- [ ] **Step 8: Expiry behavior**

Trigger a research proposal. Walk away for 16+ minutes. Return and click Approve.

Expected: Discord shows "This interaction failed." PAL's next message eventually explains the proposal expired.

- [ ] **Step 9: DM flow (if bot has DM_MESSAGES intent)**

Send PAL a direct message:

```
research quantum key distribution
```

Expected: same flow as the channel case but in the 1:1 DM. Proposal, approval, thread, results.

- [ ] **Step 10: Capture notes**

Record any behavioral drift in a notes file. Likely items to watch:
- Embed rendering (field truncation, color, emoji)
- Thread creation failures (permissions)
- Modal field defaults population
- Button-after-expiry UX
- Any "interaction failed" patterns

Commit notes if captured:

```bash
git add docs/<notes-path>.md
git commit -m "docs: Discord consent flow smoke-test notes"
```

---

## Self-review

**Spec coverage:**
- Protocol `summary_paths` field → Task 1.
- 15-minute registry timeout → Task 2.
- Daemon routes compile-edit → Task 3.
- Proposal embed builders and `ProposalContext` → Task 4.
- Edit modal builders → Task 5.
- DiscordStreamProcessor with thread progress → Task 6.
- Button/modal id parsing and field extraction helpers → Task 7.
- Adapter wiring (stream processor, on_interaction, authorization, approve/decline/edit) → Task 8.
- Intents declaration → Task 9.
- Manual smoke test → Task 10.

**Placeholder scan:** no TBDs, TODOs, vague "handle edge cases" language. Every code step shows code; every test shows assertions.

**Type consistency:** `ProposalContext` fields, `ProposalKind` literal, `parse_button_custom_id` / `parse_modal_custom_id` return tuples, `DiscordStreamProcessor.run` return shape (`tuple[list[ToolProgressMessage], str]`) used consistently from Task 4 onward. `ResearchApprovalResponseMessage`'s extended field list (with `summary_paths`) used consistently from Task 1.

**One note:** Task 8 is the largest change and isn't unit-tested in isolation — the adapter code is intertwined with discord.py's event machinery. The unit tests in Tasks 4-7 cover the pure logic (embed shape, modal shape, id parsing, field extraction), and the manual smoke test in Task 10 covers the integration. Accept that gap; automated Discord integration testing is an industry-wide pain point.
