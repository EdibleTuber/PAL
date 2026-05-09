"""Discord adapter for PAL.

Bridges Discord messages to the PAL daemon via unix socket. Generic
helpers (UserConnectionManager, message parsing, splitting, slash-prefix
rewriting, tool-progress formatting) live in agent_core.adapters.discord_gateway.
PAL keeps PalDiscordBot here because of its approval UX (button/modal
handlers, proposal threads) which are too domain-specific to lift.
"""
import contextlib
import logging
from pathlib import Path
from typing import Callable

import discord

from agent_core.adapters.discord_gateway import (
    UserConnectionManager,
    parse_discord_message,
    rewrite_slash_prefixes,
    split_message,
    format_tool_progress as _format_tool_progress_generic,
)
from agent_core.client import DaemonConnection as PalClient  # API-compatible alias
from agent_core.protocol import (
    StreamChunkMessage,
    ResponseMessage,
    ErrorMessage,
    ToolProgressMessage,
)
from pal.discord_interactions import (
    DiscordStreamProcessor,
    ProposalContext,
    build_research_edit_modal,
    build_compile_edit_modal,
    parse_button_custom_id,
    parse_modal_custom_id,
    extract_modal_field_values,
)
from pal.protocol import ResearchApprovalResponseMessage

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def _maybe_typing(channel):
    """Best-effort typing indicator. If Discord rate-limits or rejects the
    typing call, log and proceed without it — better than aborting on_message
    and ghosting the user with no response."""
    try:
        async with channel.typing():
            yield
    except discord.HTTPException as exc:
        logger.warning(
            "typing indicator failed (%s); proceeding without it", exc,
        )
        yield


def _discord_command_names() -> set[str]:
    """Return the set of registered command names for Discord prefix rewriting.

    Derives names from PALAgent.commands (PAL-specific) plus the framework's
    BUILTIN_COMMANDS. This replaces the old static pal.commands.COMMANDS import.
    """
    from agent_core.commands.builtin import BUILTIN_COMMANDS
    from pal.agent import PALAgent

    builtin_names = {cls.name for cls in BUILTIN_COMMANDS}
    pal_names = {cls.name for cls in PALAgent.commands}
    return builtin_names | pal_names


# PAL-specific tool progress labels. format_tool_progress falls through
# to the generic "tool..." label for any tool not in this dict.
_PAL_TOOL_FORMATTERS: dict[str, Callable[[dict], str]] = {
    "cat":               lambda a: f"reading {a.get('path', '?')}...",
    "ls":                lambda a: f"listing {a.get('path', '') or 'vault'}...",
    "grep":              lambda a: f"searching for \"{a.get('pattern', '?')}\"...",
    "search_vault":      lambda a: f"searching vault for \"{a.get('query', '?')}\"...",
    "edit_file":         lambda a: f"editing {a.get('path', '?')}...",
    "create_file":       lambda a: f"creating {a.get('path', '?')}...",
    "research_topic":    lambda a: a.get("status") or "running research...",
    "propose_research":  lambda a: f"proposing research on \"{a['topic']}\"..." if a.get("topic") else "proposing research...",
    "search_web":        lambda a: f"searching web for \"{a['query']}\"..." if a.get("query") else "searching web...",
}


def format_tool_progress(tool: str, arguments: dict) -> str:
    """PAL wrapper: applies PAL-specific tool labels via the generic helper."""
    return _format_tool_progress_generic(tool, arguments, custom_formatters=_PAL_TOOL_FORMATTERS)


class PalDiscordBot(discord.Client):
    """Discord bot that bridges messages to the PAL daemon."""

    def __init__(self, allowed_users: set[str], socket_path: str | Path) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True  # explicit; default() already includes this
        super().__init__(intents=intents)
        self.connections = UserConnectionManager(
            allowed_users=allowed_users,
            socket_path=socket_path,
        )
        self.active_proposals: dict[str, ProposalContext] = {}

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

        # Show typing indicator while working (best-effort: a 429 from
        # Discord on the typing call shouldn't abort the whole handler).
        async with _maybe_typing(message.channel):
            try:
                client = await self.connections.get_client(user_id)

                channel_id = str(message.channel.id)
                if parsed[0] == "command":
                    _, name, args = parsed
                    try:
                        resp = await client.command(name, args, channel_id=channel_id)
                        reply_text = resp.text
                    except RuntimeError as exc:
                        reply_text = f"Error: {exc}"
                else:
                    _, chat_text = parsed
                    processor = DiscordStreamProcessor(
                        channel=message.channel,
                        triggerer_id=user_id,
                        bot=self,
                        client=client,
                    )
                    progress, reply_text = await processor.run(client.chat(chat_text, channel_id=channel_id))

                    # Prepend tool progress lines (only for non-proposal chat;
                    # proposal progress is routed to thread by the processor).
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
        for chunk in split_message(rewrite_slash_prefixes(reply_text, _discord_command_names())):
            await message.channel.send(chunk)

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
                await interaction.response.send_modal(modal)
                return
            elif kind == "compile":
                modal = build_compile_edit_modal(ctx)
                await interaction.response.send_modal(modal)
                return
            else:  # reorg / consolidate / url_fix: v1 edit-as-decline, no modal
                try:
                    client = await self.connections.get_client(str(interaction.user.id))
                    await client.send(ResearchApprovalResponseMessage(
                        proposal_id=proposal_id,
                        decision="decline",
                    ))
                except Exception as exc:
                    logger.exception("Failed to send %s edit-decline: %s", kind, exc)
                    await interaction.response.send_message(
                        "Something went wrong. Try again.", ephemeral=True,
                    )
                    return
                try:
                    await interaction.response.edit_message(
                        content=f"✏️ Edit requested ({kind}); re-propose in chat",
                        view=None,
                    )
                except discord.HTTPException:
                    pass
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
            try:
                await interaction.response.defer()
            except discord.HTTPException:
                pass
            return

        values = extract_modal_field_values(interaction.data)

        if kind == "research":
            new_topic = values[0].strip() if len(values) >= 1 else ""
            new_depth_raw = values[1].strip() if len(values) >= 2 else ""
            if not new_topic:
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
            try:
                await interaction.response.send_message(
                    "Something went wrong sending your edit. Try again.",
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass
            return

        status_text = (
            "❌ Declined" if response.decision == "decline"
            else "✏️ Edited, running — see thread for progress"
        )
        try:
            await interaction.response.edit_message(content=status_text, view=None)
        except discord.HTTPException:
            try:
                await interaction.response.defer()
            except discord.HTTPException:
                pass

    async def close(self) -> None:
        await self.connections.close_all()
        await super().close()
