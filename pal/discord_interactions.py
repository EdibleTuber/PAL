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
