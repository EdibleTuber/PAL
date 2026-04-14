# Discord Consent Flow for Research and Compile

**Date:** 2026-04-14
**Status:** Draft

## Overview

PAL's Discord adapter currently can't handle consent-gated tool flows. When the daemon emits a `ResearchProposalMessage` or `CompileProposalMessage` over the socket, the adapter's `collect_response()` doesn't know the type, falls through to the exception handler, and shows the user `"Something went wrong: Unknown message type: 'research_proposal'"`. The tool coroutine inside the daemon then waits the full proposal timeout before returning "declined." This leaves Discord users stuck at CLI-only for anything beyond plain chat.

This spec wires Discord-native approval UX for both research and compile proposals. PAL posts an embed with Approve/Decline/Edit buttons; the triggering user clicks a button; the adapter sends an `ResearchApprovalResponseMessage` back to the daemon over the existing socket; the blocked tool coroutine wakes and continues. Progress during research flows into a thread on the proposal message so the channel stays clean. Compile follows the same pattern with its own proposal embed.

The change is scoped to the Discord adapter and a minor additive protocol field. Daemon, registry, tools, compiler, and researcher are unchanged.

## Goals

- A Discord user can propose, approve, decline, or edit research and compile proposals entirely from Discord.
- Consent is explicit per action (a button click, not text agreement).
- Only the user who triggered PAL can approve their own proposal.
- Progress during research execution streams into a Discord thread, keeping the main channel clean.
- The approval timeout is 15 minutes — short enough to match Discord's button interaction lifetime, long enough for mobile "glance, read, approve" flows.
- The existing CLI behavior is unchanged. Daemon, ApprovalRegistry, and tool handlers are unchanged.

## Non-Goals

- Structured in-place editing of compile path lists beyond a paragraph-text modal. A select-menu based path removal UX is a follow-up.
- Per-deployment configurable proposal expiry. 15 minutes for both surfaces.
- Research/compile invocation via Discord slash commands. @mention in channels and plain messages in DMs (the existing invocation paths) continue to be the only entry points.
- Support for proposals older than 15 minutes. After expiry, the button click responds with "interaction failed" at the Discord level and PAL's next turn will explain via text.
- Retrying failed Discord API posts. Best-effort; failures log and let the proposal expire naturally.

## Architecture

```
┌─────────────┐  "@PAL research X"  ┌──────────┐    ChatMessage    ┌────────┐
│   Discord   │ ───────────────────▶│  adapter │ ─────────────────▶│ daemon │
└─────────────┘                     └──────────┘                   └────────┘
                                          ▲                              │
                                          │                              │
                                          │      stream of messages      │
                                          │◀─────────────────────────────┘
                                          │
                     ┌────────────────────┴──────────────────────┐
                     │                                            │
                     ▼                                            ▼
          ResearchProposalMessage                       ToolProgressMessage
          CompileProposalMessage                        StreamChunkMessage
                     │                                  ResponseMessage
                     ▼
        Post embed with buttons;
        create thread for progress
                     │
     Approve/Decline/Edit button click
                     │
                     ▼
        ResearchApprovalResponseMessage ──▶ back over socket to daemon
                     │
                     ▼
        ApprovalRegistry.approve/decline/edit
                     │
                     ▼
        Tool coroutine wakes from proposal.event.wait()
```

**Key architectural shift in the adapter:** the current `collect_response()` function (in `pal/discord_adapter.py`) reads the stream linearly until `ResponseMessage` or `ErrorMessage` and returns. This breaks for proposal flows because the stream pauses mid-turn while the tool awaits approval. The replacement, `DiscordStreamProcessor`, handles proposal messages inline by posting Discord UI and continuing to consume the stream.

**Per-user state.** The existing `UserConnectionManager` gives each Discord user their own `PalClient` connection to the daemon. Proposal state in the daemon's `ApprovalRegistry` is already per-session. Discord-side proposal message references (for later edits and thread creation) are kept in the `DiscordStreamProcessor` instance, which lives for the duration of one chat turn.

## Consent primitives

### Approval primitive: Discord buttons

Buttons are chosen over text replies because:

- A button click is an explicit user action Discord itself authenticates. The adapter knows which Discord user_id clicked, preventing "someone else typed 'approve' in the channel" ambiguity.
- Mobile-friendly: one tap instead of typing a reply.
- The Edit button naturally triggers a Discord modal, which fits structured field collection (topic + depth, or new path list) without multi-turn chat state.

### Authorization: only the triggerer

The proposal embed stores `triggerer_user_id` (the user who originally messaged PAL) in the `DiscordStreamProcessor`'s state. On interaction, the adapter compares `str(interaction.user.id)` against the stored value. Mismatches respond with an ephemeral `"This proposal is for <@triggerer_id>"` and do not forward to the daemon. Allowlisted users who are not the triggerer cannot approve for each other.

### Timeout: 15 minutes

The `ApprovalRegistry` expiry stays at 15 minutes across both surfaces. This is long enough for mobile "check, read, decide" flows and short enough to match Discord's button interaction window (15 minutes of reliable button response). Proposals that expire before a click simply return `{status: "timed_out"}` to the tool coroutine; the model's subsequent `ResponseMessage` explains.

**Note:** this spec slightly changes the existing CLI timeout from 30 minutes to 15 minutes. The registry uses a single `DEFAULT_EXPIRY_MINUTES` constant today; the change applies to both surfaces together.

## Messages and UI

### Research proposal embed

```
Embed:
  Title:       PAL proposes research
  Color:       (default or light blue)
  Fields:
    Topic:     <topic string>
    Depth:     <depth>
    Rationale: <one-line rationale>
Buttons:
  [✅ Approve]  custom_id: "research:approve:<proposal_id>"
  [❌ Decline]  custom_id: "research:decline:<proposal_id>"
  [✏️ Edit]     custom_id: "research:edit:<proposal_id>"
```

### Compile proposal embed

```
Embed:
  Title:       PAL proposes compile
  Color:       (default or light green)
  Fields:
    Summaries (N): raw/summaries/a.md
                   raw/summaries/b.md
                   raw/summaries/c.md
                   ...
                   (truncate to ~10 entries, add "+M more" if longer)
    Rationale:     <one-line rationale>
Buttons:
  [✅ Approve]  custom_id: "compile:approve:<proposal_id>"
  [❌ Decline]  custom_id: "compile:decline:<proposal_id>"
  [✏️ Edit]     custom_id: "compile:edit:<proposal_id>"
```

Discord embed field values cap at 1024 characters. Long path lists truncate with "+N more" suffix to stay within that limit. The full list remains in the proposal state — truncation is cosmetic only.

### Edit modal — research

Triggered by "✏️ Edit" button on a research proposal.

```
Modal:
  Title: "Edit research proposal"
  TextInput 1:
    Label:       New topic
    Style:       paragraph
    Default:     <current topic>
    Required:    true
  TextInput 2:
    Label:       New depth
    Style:       short
    Default:     <current depth as string>
    Required:    false
    Placeholder: "3"
```

On submit:

- Parse `new_depth` via `int()`. Fall back to the current depth on parse failure.
- Send `ResearchApprovalResponseMessage(proposal_id, decision="edit", new_topic=..., new_depth=...)`.
- Edit the original proposal message to disable buttons and show "✏️ Edited, re-running → see thread."

### Edit modal — compile

Triggered by "✏️ Edit" button on a compile proposal.

```
Modal:
  Title: "Edit compile proposal"
  TextInput 1:
    Label:       Summary paths (one per line)
    Style:       paragraph
    Default:     <current paths joined by newlines>
    Required:    true
```

On submit:

- Split the value on newlines, strip each line, filter empty strings.
- If the resulting list is empty, treat as decline and post a followup message explaining.
- Send `ResearchApprovalResponseMessage(proposal_id, decision="edit", summary_paths=[...])`.

### Progress thread

When approval is clicked (or modal submitted):

1. Adapter edits the proposal message to disable buttons and show "✅ Approved, running → see thread."
2. Adapter creates a Discord thread on the proposal message. Thread name: `research: <first 80 chars of topic>` or `compile: <N> summaries`.
3. `ToolProgressMessage` events arriving during execution post into the thread as italic text (matches existing `format_tool_progress` output).
4. The final `ResponseMessage` (the model's summary of research results or compile report) posts as a reply to the original proposal message in the main channel, so the user sees the outcome without opening the thread.

Thread creation failure (e.g., missing "Create Public Threads" permission): gracefully degrade to posting progress as new messages in the channel. Log the permission issue.

### Decline / expiry

**Decline click:** edit the proposal message to show "❌ Declined" and disable buttons. Tool coroutine returns `{status: "declined"}` to the model. Model's `ResponseMessage` explains.

**Expiry:** if the registry expires a proposal before any button click, no Discord event fires. The tool returns `{status: "timed_out"}` and the model's `ResponseMessage` mentions the expiry. The proposal message in Discord remains showing the original embed with active-looking buttons; clicking them after expiry results in Discord's "interaction failed" toast. Optional polish for a later iteration: a background task edits the proposal message to "⌛ Expired" on the 15-minute mark. Not required for v1.

## Stream processor

Replaces `collect_response()` in `pal/discord_adapter.py`.

```python
class DiscordStreamProcessor:
    """Consumes a chat stream from the daemon, handling proposal
    messages inline by posting Discord UI and registering interaction
    handlers."""

    def __init__(
        self,
        channel: discord.abc.Messageable,
        triggerer_id: str,
        bot: "PalDiscordBot",
        client: PalClient,
    ) -> None:
        self.channel = channel
        self.triggerer_id = triggerer_id
        self.bot = bot
        self.client = client
        self.proposal_messages: dict[str, discord.Message] = {}
        self.progress_threads: dict[str, discord.Thread] = {}
        self.current_proposal_id: str | None = None

    async def run(self, stream: AsyncGenerator[Message, None]) -> str:
        """Consume the stream, return final text. Side effects: posts
        proposal embeds, thread progress updates, registers Discord
        interaction state with self.bot via triggerer_id indexing."""
        text_buffer: list[str] = []
        async for msg in stream:
            if isinstance(msg, ResearchProposalMessage):
                await self._post_research_proposal(msg)
                self.current_proposal_id = msg.proposal_id
            elif isinstance(msg, CompileProposalMessage):
                await self._post_compile_proposal(msg)
                self.current_proposal_id = msg.proposal_id
            elif isinstance(msg, ToolProgressMessage):
                await self._post_progress(msg)
            elif isinstance(msg, StreamChunkMessage):
                text_buffer.append(msg.token)
            elif isinstance(msg, ResponseMessage):
                return "".join(text_buffer) if text_buffer else msg.text
            elif isinstance(msg, ErrorMessage):
                return f"Error: {msg.error}"
        return "".join(text_buffer)
```

`_post_research_proposal` and `_post_compile_proposal` construct the embed + `discord.ui.View` with three buttons, post to `self.channel`, store the returned `discord.Message` in `self.proposal_messages[proposal_id]`, and register the proposal in a bot-level registry keyed by `proposal_id` so the bot-level `on_interaction` handler can authorize clicks.

`_post_progress` creates the thread lazily on the first progress event for a given `current_proposal_id`, then posts the formatted label into it.

## Interaction handling

Bot-level `on_interaction` method on `PalDiscordBot`. Discord's `discord.py` library routes component and modal interactions through this callback.

```python
async def on_interaction(self, interaction: discord.Interaction) -> None:
    if interaction.type == discord.InteractionType.component:
        await self._handle_button(interaction)
    elif interaction.type == discord.InteractionType.modal_submit:
        await self._handle_modal_submit(interaction)
```

**Button handler:**

```python
async def _handle_button(self, interaction: discord.Interaction) -> None:
    cid = interaction.data.get("custom_id", "")
    try:
        kind, action, proposal_id = cid.split(":", 2)
    except ValueError:
        return
    if kind not in ("research", "compile") or action not in ("approve", "decline", "edit"):
        return

    # Authorization
    proposal_ctx = self.active_proposals.get(proposal_id)
    if proposal_ctx is None:
        await interaction.response.send_message(
            "This proposal is no longer active.", ephemeral=True
        )
        return
    if str(interaction.user.id) != proposal_ctx.triggerer_id:
        await interaction.response.send_message(
            f"This proposal is for <@{proposal_ctx.triggerer_id}>.",
            ephemeral=True,
        )
        return

    if action == "edit":
        modal = self._build_edit_modal(kind, proposal_id, proposal_ctx)
        await interaction.response.send_modal(modal)
        return

    # approve or decline
    client = await self.connections.get_client(str(interaction.user.id))
    await client.send(ResearchApprovalResponseMessage(
        proposal_id=proposal_id,
        decision=action,
    ))
    # Edit original message to disable buttons and show status
    status_text = {"approve": "✅ Approved, running → see thread",
                   "decline": "❌ Declined"}[action]
    await interaction.response.edit_message(content=status_text, view=None)
```

**Modal submit handler:**

```python
async def _handle_modal_submit(self, interaction: discord.Interaction) -> None:
    cid = interaction.data.get("custom_id", "")
    try:
        kind, proposal_id = cid.split(":", 1)  # "research:<pid>" or "compile:<pid>"
    except ValueError:
        return
    proposal_ctx = self.active_proposals.get(proposal_id)
    if proposal_ctx is None or str(interaction.user.id) != proposal_ctx.triggerer_id:
        return

    # discord.py gives us interaction.data.components (a list of action-row
    # dicts). Each action-row has its own components list with value fields.
    # Walk the nested structure to extract per-field text values. The
    # implementation will use whatever accessor discord.py exposes for the
    # running library version (e.g., interaction.data["components"][0]
    # ["components"][0]["value"]).

    if kind == "research":
        new_topic = _extract_modal_value(interaction, field_index=0)
        new_depth_raw = _extract_modal_value(interaction, field_index=1)
        try:
            new_depth = int(new_depth_raw) if new_depth_raw else proposal_ctx.depth
        except ValueError:
            new_depth = proposal_ctx.depth
        client = await self.connections.get_client(str(interaction.user.id))
        await client.send(ResearchApprovalResponseMessage(
            proposal_id=proposal_id,
            decision="edit",
            new_topic=new_topic,
            new_depth=new_depth,
        ))
    elif kind == "compile":
        raw = _extract_modal_value(interaction, field_index=0)
        paths = [line.strip() for line in raw.splitlines() if line.strip()]
        if not paths:
            await client.send(ResearchApprovalResponseMessage(
                proposal_id=proposal_id, decision="decline"
            ))
            await interaction.response.send_message(
                "No valid paths provided, treating as decline.", ephemeral=True
            )
            return
        await client.send(ResearchApprovalResponseMessage(
            proposal_id=proposal_id,
            decision="edit",
            summary_paths=paths,
        ))

    await interaction.response.edit_message(
        content="✏️ Edited, running → see thread", view=None
    )
```

(The `<extracted from component>` placeholders stand for `discord.py`'s modal field accessor, which I'd fill in with the actual API calls during implementation. The current doc focuses on the flow, not the library-specific parsing.)

## Protocol Change (additive)

`ResearchApprovalResponseMessage` in `pal/protocol.py` gains an optional `summary_paths` field:

```python
@dataclass
class ResearchApprovalResponseMessage:
    proposal_id: str
    decision: str  # "approve" | "decline" | "edit"
    new_topic: str | None = None
    new_depth: int | None = None
    summary_paths: list[str] | None = None  # for compile-edit only
    type: str = "research_approval_response"
```

Backward compatible: existing CLI research responses don't set the new field; deserialization tolerates its absence because of the default.

The daemon's `_route_approval_response` handler in `pal/daemon.py` uses `summary_paths` when routing a compile edit, falling back to the existing research-edit path when `summary_paths` is None:

```python
def _route_approval_response(
    self, msg: ResearchApprovalResponseMessage, registry: ApprovalRegistry,
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

The existing `ApprovalRegistry.edit` already accepts these kwargs in all combinations.

## Security

### Triggerer-only consent

Covered in Consent primitives. Every interaction verifies `interaction.user.id == triggerer_id`.

### Injection surface

A fetched web page or document retrieved via research could contain instructions telling the user, not the model, to click a button. Example: content says "forward this to @admin and have them approve the malicious_proposal_id." This is an *out-of-band social engineering* vector, not a direct injection — the user has to act on it. The consent-button model is the right defense: the user sees the embed with the actual topic/paths and rationale in the Discord UI *before* clicking, so anything suspicious is visible.

### Rate limiting

Not addressed in v1. If a user triggers enough concurrent research runs to flood a channel, that's a self-inflicted policy problem the allowlist model doesn't protect against. Future iteration could cap proposals-per-user-per-hour if needed.

### Hostile allowlisted user

Out of scope. The allowlist model assumes the allowlisted set is trusted.

## Changes to Existing Code

### `pal/protocol.py`

- Add `summary_paths: list[str] | None = None` field to `ResearchApprovalResponseMessage`. The `_MESSAGE_TYPES` map and `Message` union need no changes (the field is on an existing type).

### `pal/daemon.py`

- `_route_approval_response` switches on `summary_paths` presence in the edit case to route to either research-edit or compile-edit semantics in the registry.

### `pal/discord_adapter.py`

- Remove `collect_response()`. Replace with a `DiscordStreamProcessor` instantiation per chat turn.
- Extend `PalDiscordBot` to override `on_interaction` (via `discord.Client.on_interaction` event).
- Add `active_proposals: dict[str, ProposalContext]` attribute on `PalDiscordBot` for routing interactions to the correct proposal context. `ProposalContext` is a small internal dataclass carrying `triggerer_id`, `kind`, current topic/depth/paths (for modal defaults), and the `discord.Message` reference for later edits.
- Proposal context lifecycle: `DiscordStreamProcessor` registers a new entry when it posts a proposal embed. The entry is removed after a terminal button interaction (approve/decline/edit-submitted) or on a periodic sweep keyed by proposal age (>30 minutes since registration, well past the 15-minute registry expiry). This prevents unbounded growth without requiring tight coupling to the registry's lifecycle events.
- `on_message` flow uses the new processor instead of `collect_response`.

### `pal/discord_main.py`

- Verify `intents.direct_messages = True` is set. Current code sets `intents.message_content = True` and uses `discord.Intents.default()`. `Intents.default()` includes `direct_messages`, but this should be explicit in the code comment so future readers don't strip it.

### New: `pal/discord_interactions.py`

The `DiscordStreamProcessor`, proposal embed builders, edit modal builders, and the button/modal handler helpers. Moving these out of `discord_adapter.py` keeps the adapter focused on connection management; interactions logic lives in a dedicated module.

### New: `tests/test_discord_interactions.py`

Unit tests covering the stream processor (with a mocked Discord channel) and the interaction routing logic (with mocked `discord.Interaction` objects).

### Unchanged

`pal/tools.py`, `pal/approval_registry.py`, `pal/compiler.py`, `pal/researcher.py`, `pal/cli.py`, `pal/prompt_builder.py`, `pal/websearch.py`, `pal/fetcher.py`.

## Error Handling

- **Button click from non-triggerer:** ephemeral error response. Never forwarded to daemon.
- **Button click for unknown proposal_id** (already cleaned up from `active_proposals`): ephemeral "This proposal is no longer active."
- **Modal submit with invalid depth:** fall back to current depth. Adapter-side validation.
- **Modal submit with empty path list:** treat as decline, ephemeral explanation.
- **Discord API failure posting proposal embed:** log. Tool coroutine times out naturally at 15 min.
- **Discord API failure posting to thread:** log and fall back to posting as new channel messages for the rest of the run.
- **Thread creation permission denied:** log, fall back to channel messages, continue.
- **Second chat message from same user while a proposal is pending:** daemon rejects via existing "previous turn in flight" error. Adapter surfaces the rejection as a clean Discord message.
- **Daemon disconnect mid-run:** existing `ConnectionRefusedError`/`FileNotFoundError` handling in the adapter catches this; the user sees the existing "I can't reach the PAL daemon right now" message.

## Testing

### Unit tests

`tests/test_discord_interactions.py`:

- `DiscordStreamProcessor` with a mocked channel receives a synthetic stream of (research proposal → progress × 3 → response); asserts one embed posted, thread created, three progress posts in thread, final message in channel.
- Same as above but compile proposal.
- Button click from triggerer approves: assert `client.send()` called with the correct `ResearchApprovalResponseMessage`.
- Button click from non-triggerer: assert ephemeral error, no `client.send()`.
- Button click for unknown proposal_id: assert ephemeral error.
- Modal submit — research edit with valid fields: assert correct response sent.
- Modal submit — research edit with invalid depth: falls back to current depth.
- Modal submit — compile edit with empty paths: converts to decline.

### Integration

Realistic end-to-end Discord integration is hard to automate (requires a real Discord test server and bot account). The manual smoke test below is the acceptance gate.

### Manual smoke test (ships as Task N of implementation plan)

Against a real Discord server and live PAL daemon:

1. `@PAL research X` in a channel → approve via button → research runs → progress appears in thread → final reply appears in channel.
2. Same flow, decline button → no research runs, model acknowledges decline.
3. Same flow, edit button → modal appears → submit with new topic → research re-runs with edited values.
4. `@PAL research Y then ingest to vault` → research + compile proposals both render and both get approved.
5. With two accounts: account A triggers a research, account B tries to click approve → ephemeral "this proposal is for @A" error.
6. Trigger research, walk away for 16+ minutes, return → button click produces Discord "interaction failed" and next message from PAL explains the expiry.
7. DM `PAL research X` (if DM support works) → same flow in a DM conversation.

## Future Extensions

- **Structured compile-edit UX.** Replace the paragraph-text paths modal with a select-menu of summaries from `raw/summaries/`. Parked until the text-input flow proves bad in practice.
- **Slash command invocation.** `/research topic:...` and `/compile paths:...` as first-class Discord commands. More discoverable than @mentions.
- **Proposal expiry message edit.** Background task that edits expired proposal messages to show "⌛ Expired." Cosmetic polish.
- **Per-user proposal rate limiting.** Cap concurrent or per-hour proposals per user if flooding becomes a concern.
- **Shared-channel research with visibility controls.** Let PAL post research results publicly in a channel (for team visibility) while still gating approval to the triggerer.
