"""Discord interactions: proposal embeds, modals, stream processor.

Separated from pal.discord_adapter so the adapter file stays focused on
connection lifecycle. This module handles all UI-side concerns of
consent-gated tool flows (ResearchProposalMessage, CompileProposalMessage,
their responses).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal, Optional

import discord

logger = logging.getLogger(__name__)

from pal.protocol import (
    CompileProposalMessage,
    ConsolidateProposalMessage,
    ResearchApprovalResponseMessage,
    ResearchProposalMessage,
    ReorgProposalMessage,
)

ProposalKind = Literal["research", "compile", "reorg", "consolidate"]

_DISCORD_FIELD_VALUE_LIMIT = 1024
_FIELD_BUDGET_HEADROOM = 40  # "+NNN more" plus newlines


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
    topic: str = ""
    depth: int = 3
    summary_paths: list[str] = field(default_factory=list)
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

    view = discord.ui.View(timeout=None)
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
    cap = _DISCORD_FIELD_VALUE_LIMIT - _FIELD_BUDGET_HEADROOM
    fitted: list[str] = []
    chars = 0
    for path in msg.summary_paths:
        add = len(path) + (1 if fitted else 0)  # +1 for newline separator
        if chars + add > cap:
            break
        fitted.append(path)
        chars += add
    dropped = total - len(fitted)
    paths_text = "\n".join(fitted)
    if dropped > 0:
        paths_text += f"\n+{dropped} more"
    embed.add_field(
        name=f"Summaries ({total})",
        value=paths_text if paths_text else "(empty)",
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


def build_reorg_proposal_embed(
    msg: ReorgProposalMessage,
) -> tuple[discord.Embed, discord.ui.View]:
    """Pure builder: returns the embed and a View with three buttons."""
    embed = discord.Embed(
        title="PAL proposes reorg",
        color=discord.Color.orange(),
    )
    total = len(msg.operations)
    # Build op-chunks as pairs of lines; fit whole chunks under budget.
    op_chunks: list[tuple[str, str]] = []
    for op in msg.operations:
        op_type = op.get("type", "?")
        src = op.get("src", "?")
        dst = op.get("dst", "?")
        op_chunks.append((f"[{op_type}] {src}", f"         -> {dst}"))

    cap = _DISCORD_FIELD_VALUE_LIMIT - _FIELD_BUDGET_HEADROOM
    fitted_lines: list[str] = []
    chars = 0
    dropped = 0
    for line_a, line_b in op_chunks:
        add = len(line_a) + 1 + len(line_b) + (1 if fitted_lines else 0)
        if chars + add > cap:
            dropped = len(op_chunks) - (len(fitted_lines) // 2)
            break
        fitted_lines.append(line_a)
        fitted_lines.append(line_b)
        chars += add
    if dropped > 0:
        fitted_lines.append(f"+{dropped} more")
    embed.add_field(
        name=f"Operations ({total})",
        value="\n".join(fitted_lines) if fitted_lines else "(empty)",
        inline=False,
    )
    embed.add_field(name="Rationale", value=msg.rationale, inline=False)
    embed.add_field(
        name="Link rewrites",
        value=str(msg.references_preview),
        inline=False,
    )

    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        style=discord.ButtonStyle.success,
        label="Approve",
        emoji="✅",
        custom_id=f"reorg:approve:{msg.proposal_id}",
    ))
    view.add_item(discord.ui.Button(
        style=discord.ButtonStyle.danger,
        label="Decline",
        emoji="❌",
        custom_id=f"reorg:decline:{msg.proposal_id}",
    ))
    view.add_item(discord.ui.Button(
        style=discord.ButtonStyle.secondary,
        label="Edit",
        emoji="✏️",
        custom_id=f"reorg:edit:{msg.proposal_id}",
    ))
    return embed, view


def build_consolidate_proposal_embed(
    msg: ConsolidateProposalMessage,
) -> tuple[discord.Embed, discord.ui.View]:
    """Pure builder: returns the embed and a View with three buttons."""
    embed = discord.Embed(
        title="PAL proposes consolidate",
        color=discord.Color.blurple(),
    )
    total = len(msg.source_paths)
    cap = _DISCORD_FIELD_VALUE_LIMIT - _FIELD_BUDGET_HEADROOM
    fitted: list[str] = []
    chars = 0
    for path in msg.source_paths:
        add = len(path) + (1 if fitted else 0)
        if chars + add > cap:
            break
        fitted.append(path)
        chars += add
    dropped = total - len(fitted)
    paths_text = "\n".join(fitted)
    if dropped > 0:
        paths_text += f"\n+{dropped} more"
    embed.add_field(
        name=f"Sources ({total})",
        value=paths_text if paths_text else "(empty)",
        inline=False,
    )
    embed.add_field(name="Target", value=msg.target_path, inline=False)
    embed.add_field(name="Title", value=msg.target_title, inline=False)
    embed.add_field(name="Rationale", value=msg.rationale, inline=False)

    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        style=discord.ButtonStyle.success,
        label="Approve",
        emoji="✅",
        custom_id=f"consolidate:approve:{msg.proposal_id}",
    ))
    view.add_item(discord.ui.Button(
        style=discord.ButtonStyle.danger,
        label="Decline",
        emoji="❌",
        custom_id=f"consolidate:decline:{msg.proposal_id}",
    ))
    view.add_item(discord.ui.Button(
        style=discord.ButtonStyle.secondary,
        label="Edit",
        emoji="✏️",
        custom_id=f"consolidate:edit:{msg.proposal_id}",
    ))
    return embed, view


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
            elif isinstance(msg, ReorgProposalMessage):
                await self._handle_reorg_proposal(msg)
            elif isinstance(msg, ConsolidateProposalMessage):
                await self._handle_consolidate_proposal(msg)
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
        try:
            posted = await self.channel.send(embed=embed, view=view)
        except Exception as exc:
            logger.exception("Failed to post research proposal: %s", exc)
            try:
                await self.client.send(ResearchApprovalResponseMessage(
                    proposal_id=msg.proposal_id,
                    decision="decline",
                ))
            except Exception:
                logger.exception("Also failed to send decline for failed research emit")
            try:
                await self.channel.send(
                    f"Couldn't render research proposal ({exc}). Declined. "
                    f"Try again with a shorter topic or rationale."
                )
            except Exception:
                pass
            return
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
        try:
            posted = await self.channel.send(embed=embed, view=view)
        except Exception as exc:
            logger.exception("Failed to post compile proposal: %s", exc)
            try:
                await self.client.send(ResearchApprovalResponseMessage(
                    proposal_id=msg.proposal_id,
                    decision="decline",
                ))
            except Exception:
                logger.exception("Also failed to send decline for failed compile emit")
            try:
                await self.channel.send(
                    f"Couldn't render compile proposal ({exc}). Declined. "
                    f"Try again with fewer/shorter paths."
                )
            except Exception:
                pass
            return
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

    async def _handle_reorg_proposal(
        self, msg: ReorgProposalMessage,
    ) -> None:
        embed, view = build_reorg_proposal_embed(msg)
        try:
            posted = await self.channel.send(embed=embed, view=view)
        except Exception as exc:
            logger.exception("Failed to post reorg proposal: %s", exc)
            try:
                await self.client.send(ResearchApprovalResponseMessage(
                    proposal_id=msg.proposal_id,
                    decision="decline",
                ))
            except Exception:
                logger.exception("Also failed to send decline for failed reorg emit")
            try:
                await self.channel.send(
                    f"Couldn't render reorg proposal ({exc}). Declined. "
                    f"Try again with fewer/shorter operations."
                )
            except Exception:
                pass
            return
        ctx = ProposalContext(
            proposal_id=msg.proposal_id,
            kind="reorg",
            triggerer_id=self.triggerer_id,
            rationale=msg.rationale,
            discord_message_id=posted.id,
            channel_id=getattr(self.channel, "id", None),
        )
        # Stash operations on the context for completeness even though
        # v1 edit-as-decline does not use them.
        setattr(ctx, "operations", [dict(op) for op in msg.operations])
        self.bot.active_proposals[msg.proposal_id] = ctx
        self.current_proposal_id = msg.proposal_id
        self.current_proposal_message = posted

    async def _handle_consolidate_proposal(
        self, msg: ConsolidateProposalMessage,
    ) -> None:
        embed, view = build_consolidate_proposal_embed(msg)
        try:
            posted = await self.channel.send(embed=embed, view=view)
        except Exception as exc:
            logger.exception("Failed to post consolidate proposal: %s", exc)
            try:
                await self.client.send(ResearchApprovalResponseMessage(
                    proposal_id=msg.proposal_id,
                    decision="decline",
                ))
            except Exception:
                logger.exception("Also failed to send decline for failed consolidate emit")
            try:
                await self.channel.send(
                    f"Couldn't render consolidate proposal ({exc}). Declined. "
                    f"Try again with fewer/shorter source paths."
                )
            except Exception:
                pass
            return
        ctx = ProposalContext(
            proposal_id=msg.proposal_id,
            kind="consolidate",
            triggerer_id=self.triggerer_id,
            rationale=msg.rationale,
            summary_paths=list(msg.source_paths),
            discord_message_id=posted.id,
            channel_id=getattr(self.channel, "id", None),
        )
        setattr(ctx, "target_path", msg.target_path)
        setattr(ctx, "target_title", msg.target_title)
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
                ctx = self.bot.active_proposals.get(self.current_proposal_id)
                if ctx is not None:
                    ctx.thread_id = self.current_thread.id
            except discord.HTTPException:
                self.current_thread = None

        from pal.discord_adapter import format_tool_progress
        label = format_tool_progress(msg.tool, msg.arguments)
        if self.current_thread is not None:
            try:
                await self.current_thread.send(label)
                return
            except discord.HTTPException:
                pass
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
        if ctx.kind == "reorg":
            ops = getattr(ctx, "operations", [])
            return f"reorg: {len(ops)} operations"
        if ctx.kind == "consolidate":
            target = getattr(ctx, "target_path", "?")
            return f"consolidate: {len(ctx.summary_paths)} sources -> {target}"
        return f"compile: {len(ctx.summary_paths)} summaries"


def parse_button_custom_id(
    cid: str,
) -> Optional[tuple[ProposalKind, str, str]]:
    """Parse 'research:approve:abc-123' into ('research', 'approve', 'abc-123').
    Returns None for malformed input."""
    parts = cid.split(":", 2)
    if len(parts) != 3:
        return None
    kind, action, proposal_id = parts
    if kind not in ("research", "compile", "reorg", "consolidate"):
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
    if kind not in ("research", "compile", "reorg", "consolidate"):
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
