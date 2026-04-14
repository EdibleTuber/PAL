"""Discord adapter for PAL.

Bridges Discord messages to the PAL daemon via unix socket.
Each allowed Discord user gets their own daemon connection.
"""
import logging
from pathlib import Path

import discord

from pal.client import PalClient
from pal.discord_interactions import (
    DiscordStreamProcessor,
    ProposalContext,
    build_research_edit_modal,
    build_compile_edit_modal,
    parse_button_custom_id,
    parse_modal_custom_id,
    extract_modal_field_values,
)
from pal.protocol import (
    StreamChunkMessage,
    ResponseMessage,
    ErrorMessage,
    ToolProgressMessage,
    ResearchApprovalResponseMessage,
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
    elif tool == "research_topic":
        status = arguments.get("status")
        label = status if status else "running research..."
    elif tool == "propose_research":
        topic = arguments.get("topic", "")
        label = f"proposing research on \"{topic}\"..." if topic else "proposing research..."
    elif tool == "search_web":
        query = arguments.get("query", "")
        label = f"searching web for \"{query}\"..." if query else "searching web..."
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
        intents.direct_messages = True  # explicit; default() already includes this
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
                    processor = DiscordStreamProcessor(
                        channel=message.channel,
                        triggerer_id=user_id,
                        bot=self,
                        client=client,
                    )
                    progress, reply_text = await processor.run(client.chat(chat_text))

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
        for chunk in split_message(reply_text):
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
