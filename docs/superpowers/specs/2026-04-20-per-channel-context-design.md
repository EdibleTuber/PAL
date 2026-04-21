---
title: Per-Channel Context and Scratchpad for PAL
date: 2026-04-20
status: draft
---

# Per-Channel Context and Scratchpad for PAL

## Context

PAL's Discord bridge keeps one `PalClient` connection per Discord user (`UserConnectionManager` in `pal/discord_adapter.py`). That connection feeds a single `Conversation` on the daemon side, in-memory, rolling window of the last 50 turns, lost on disconnect or daemon restart. `channel_id` is available in the bridge's message handlers but dropped at the socket boundary — not forwarded to the daemon, not used to scope any state.

The consequence is that a user who posts in `#gdb-mcp` and then in `#general` sees PAL treat both as one continuous conversation. There is no project-level separation, no persistence across daemon restarts, and no per-channel working notes.

Three concurrent work streams inform how this spec is scoped:

1. The user's prior planning (`docs/agent_ecosystem_direction.md`) already decided PAL stays as one agent among several, with RE Lab as the likely MVP second agent. Channels as a dispatch mechanism for multi-agent Discord landed in that earlier discussion.
2. PAL's existing wisdom system already provides curated, durable, typed knowledge injected into every system prompt. Adding a parallel "per-channel structured memory" would overlap significantly with wisdom and create a 2D scoping problem once wisdom becomes per-agent.
3. The user wants "short-term memory about the project" — working context that survives daemon restarts and keeps per-channel project state straight.

This spec scopes the work to conversation-history persistence plus a per-channel free-form scratchpad. Retrieval bias, per-channel wisdom, per-channel profile, and any form of structured/typed channel memory are explicitly out of scope. If the persistent-history plus scratchpad combination turns out to be insufficient in real use, a follow-up spec can add structured memory then.

## Non-goals

- Retrieval bias per channel. Vault retrieval stays global for now; revisit after observing real usage.
- Per-channel wisdom, profile, or learnings. These stay global within PAL. (They become per-agent when the ecosystem extraction happens, per `agent_ecosystem_direction.md`.)
- Typed or categorized channel memory. The scratchpad is free-form markdown.
- Multi-user support beyond single-user Discord + CLI. The daemon still trusts the connecting caller.
- Channel renaming UX. Discord channel IDs are stable; renaming the channel does not affect the key.
- Migration of any existing conversation state. The daemon starts fresh when this lands; there is no prior persistent history to migrate.
- Cross-channel summarization or "what did we talk about in #other?" tooling.

## Architecture

### Two storage surfaces per channel

| Surface | Location | Git-tracked | Purpose |
|---------|----------|-------------|---------|
| History | `<daemon_data_dir>/channels/<channel_id>/history.jsonl` | No | Append-only chat log, replayed on daemon start to rebuild in-memory `Conversation` |
| Scratchpad | `<vault>/_channels/<channel_id>/scratch.md` | Yes | Free-form markdown PAL maintains, auto-injected into system prompt |

The split puts audit-worthy, human-readable state in the vault (on the existing backup path) and puts mechanical turn-by-turn state out of the commit log. The `_` prefix on `_channels/` follows PAL's existing convention for system-managed vault directories (alongside `_wisdom`, `_profile`, `_learning`, `_config`).

### Channel identity

Keyed by a single `channel_id` string. Discord bridge forwards the Discord channel ID (numeric snowflake as string). CLI uses the fixed sentinel `"cli-default"`, which also serves as the fallback for any caller that omits `channel_id` in the protocol (backward compat for unmodified callers).

DMs and threads from Discord are just channels — Discord gives them channel IDs, and we do not treat them specially. If the user wants thread-scoped context, the thread's channel ID naturally provides it; if they don't, they stay in the parent channel.

Channel IDs are defensively validated: `[A-Za-z0-9_-]+` only. Anything else is rejected at `ChannelStore`/`Scratchpad` entry and the daemon falls back to `"cli-default"` with a warning log. This prevents path-traversal issues from a malicious future caller.

### Daemon state shape

`Conversation` objects keyed by channel_id live in a dict on the `Daemon` via a new `ChannelStore` component, not per-connection. A single `PalClient` connection carries messages from multiple channels; the daemon routes each message to the correct conversation based on the `channel_id` field in `ChatMessage`.

`ChannelStore.get_or_create(channel_id)` is lazy: on first access for a channel, it reads the history.jsonl (if present) and replays turns to rebuild the in-memory `Conversation`. On subsequent accesses it returns the cached instance.

### Protocol change

`ChatMessage` and `CommandMessage` gain an optional `channel_id: str | None = None` field. When None, the daemon substitutes `"cli-default"`. Old clients that don't send the field continue to work.

### System prompt assembly

```
[base prompt: tools list, honesty rules, style]
[profile — global, unchanged]
[wisdom — global, unchanged]
[SCRATCHPAD for channel <id>]  ← new, auto-injected (omitted if empty)
[available commands]
```

`prompt_builder.build(...)` gains a `channel_scratchpad: str | None` parameter. When non-empty, renders a scratchpad section between wisdom and commands. When empty or None, the section is omitted entirely (no empty-section noise).

### Scratchpad update model

Hybrid: auto-injected into the system prompt, updated via a new `update_scratch` tool call PAL initiates when it judges a note is worth recording. The user can also append notes manually via a new `/note` slash command.

Every `update_scratch` tool call writes the file and commits through `WikiManager` (same machinery as other vault writes). Commit messages are `scratch: update <channel_id>`. Commit noise is bounded because updates happen only when PAL decides to record something, not on every turn.

### Scratchpad size cap

Default cap: 2 KB (2048 bytes). `update_scratch` returns an error if the proposed content exceeds the cap; the model must prune or summarize and retry in the same turn. `/note` returns a user-visible error if the append would exceed the cap.

The cap is intentional: it forces the scratchpad to stay terse working state, not drift into a second wiki. Configurable via `config.scratchpad_max_bytes`.

## Components

### New modules

**`pal/channels.py`** — `ChannelStore`:

```python
class ChannelStore:
    def __init__(self, channels_dir: Path, history_depth: int): ...
    async def get_or_create(self, channel_id: str) -> Conversation: ...
    # plus helpers: _replay(path) -> Conversation, _validate_channel_id(id) -> bool
```

One `Conversation` instance per channel_id, cached in memory. `asyncio.Lock` per channel for serialized writes to its history file.

**`pal/scratchpad.py`** — `Scratchpad`:

```python
class Scratchpad:
    def __init__(self, vault_path: Path, channel_id: str, wiki: WikiManager, max_bytes: int): ...
    def read(self) -> str: ...
    def write(self, content: str) -> None: ...  # raises if over max_bytes
    def append(self, text: str) -> None: ...    # convenience for /note; raises if resulting size over max_bytes
```

Writes go through `WikiManager.commit(...)` for git tracking. One instance per channel (created on demand from the daemon or tool executor).

### Modified modules

**`pal/protocol.py`** — add `channel_id: str | None = None` to `ChatMessage` and `CommandMessage`.

**`pal/conversation.py`** — `Conversation.__init__` gains `history_path: Path | None = None`. When set, every `add_message` also appends a JSON line to that file. Replay is handled by the `ChannelStore`, not `Conversation` itself, to keep this class focused.

**`pal/daemon.py`** — `Daemon.__init__` constructs one `ChannelStore` for the process. Per-connection state changes: the old `self.conv` becomes a dict accessed via `ChannelStore.get_or_create(channel_id)` at message-handle time. `_handle_chat` reads `msg.channel_id` (defaulting to `"cli-default"`), loads the right conversation, reads `Scratchpad(channel_id).read()`, passes it to `prompt_builder.build(channel_scratchpad=...)`.

Add `_handle_note` handler for the `/note` command.

**`pal/prompt_builder.py`** — `build()` gains `channel_scratchpad: str | None = None`. Renders a scratchpad section when non-empty.

**`pal/tools.py`** — new `update_scratch` tool. Tool context must carry the current `channel_id` so the tool knows which scratchpad to write. Extend `ToolExecutor` with a `channel_id` attribute set per-turn. Tool spec:

```json
{
  "type": "function",
  "function": {
    "name": "update_scratch",
    "description": "Replace the scratchpad contents for the current channel with <content>. Use this to record short-term project state, current decisions, or anything you want to remember on the next turn. Content must be 2048 bytes or less.",
    "parameters": {
      "type": "object",
      "properties": {
        "content": {"type": "string"}
      },
      "required": ["content"]
    }
  }
}
```

**`pal/discord_adapter.py`** — `on_message` passes `channel_id=str(message.channel.id)` into `client.chat(...)`. Slash command handlers likewise pass `channel_id` when sending `CommandMessage`.

**`pal/client.py`** — `chat(text, *, channel_id=None)` and `command(name, args, *, channel_id=None)` signatures extended. Threaded into the `ChatMessage`/`CommandMessage` constructor.

**`pal/cli.py`** — sets `channel_id="cli-default"` explicitly when calling client methods.

**`pal/config.py`** — new fields:
- `channels_dir: Path = field(default_factory=lambda: Path.home() / ".local/share/pal/channels")` — conventional user data location, not vault-local (history is mechanical, not knowledge)
- `scratchpad_max_bytes: int = 2048`

Env overrides: `PAL_CHANNELS_DIR`, `PAL_SCRATCHPAD_MAX_BYTES`.

### Files untouched

- `pal/retrieval.py`, `pal/retrieval_client.py` — retrieval stays global this round
- `pal/wisdom.py`, `pal/profile.py` — still global
- `pal/wiki.py` — scratchpad writes reuse existing machinery

## Data shapes

### `ChatMessage` (after protocol extension)

```python
@dataclass
class ChatMessage:
    text: str
    channel_id: str | None = None  # NEW; daemon substitutes "cli-default" when None
```

### `history.jsonl` file format

One JSON object per line. OpenAI-compatible message shape (matches what `Conversation` stores internally):

```jsonl
{"role": "user", "content": "Plan phase 2"}
{"role": "assistant", "content": "...", "tool_calls": [...]}
{"role": "tool", "tool_call_id": "...", "content": "..."}
{"role": "assistant", "content": "Done"}
```

Malformed lines are skipped on replay with a warning log, not fatal.

### `scratch.md` file format

Plain markdown. Any content is valid. Size enforced by the writer, not parsed or schema-validated. Typical shape (not required):

```markdown
# Channel scratch: #gdb-mcp

- 2026-04-20: Phase 2 in progress — chose FastMCP over raw SDK
- Current next step: prototype with r2pipe integration
- Pending: define worker API contract for RE Lab integration
```

## Build, Deploy, Config

### Config additions (`pal/config.py` + env)

```python
channels_dir: Path = Path.home() / ".local/share/pal/channels"
scratchpad_max_bytes: int = 2048
```

Env: `PAL_CHANNELS_DIR`, `PAL_SCRATCHPAD_MAX_BYTES`.

### Directory creation

Daemon creates `channels_dir` and `<vault_path>/_channels/` on startup if missing. Fails fast with a clear error if it can't.

### Migration / rollout

No persistent state exists today. On deploy:
1. Daemon restart picks up the new protocol and state machinery.
2. First message on any channel creates fresh state (no history to load, no scratchpad yet).
3. History and scratchpads accumulate organically.

Old clients (unupdated CLI) that don't send `channel_id` all land in `"cli-default"`. Behavior is identical to pre-change behavior except state now persists. No breaking changes.

## Error Handling

**Caller omits `channel_id`**: daemon substitutes `"cli-default"`. Logged at info level once per connection to surface unexpected fallbacks.

**History file corruption** (malformed JSONL line): log warning, skip line, continue replay. Unreadable file: rename to `history.jsonl.corrupt-<timestamp>`, create fresh conversation, log warning. User sees no interruption.

**Scratchpad unreadable**: return empty string for this turn, log warning. User can fix the file manually in Obsidian.

**Scratchpad size-cap exceeded**: `update_scratch` tool returns `{"error": "scratchpad would exceed N bytes; current is M, proposed was K. Prune or summarize first."}`. Model sees the error and retries. `/note` command returns a user-visible error with suggestion to clear or edit manually. Neither modifies the file.

**`_channels/<channel_id>/` doesn't exist**: `Scratchpad.write` creates it. `.read` returns empty string. No errors surfaced.

**Daemon data dir doesn't exist or unwritable**: startup-time: fail fast. Runtime: per-channel fallback to in-memory-only (log warning, no persistence this session).

**Concurrent writes to the same channel**: `ChannelStore` uses an `asyncio.Lock` per channel. `Scratchpad` likewise. Both go through existing `WikiManager` lock for vault writes.

**Git commit failure on scratchpad write**: file is on disk, commit failed. Log warning. Next successful commit touching the file will include the stray change. Scratchpad consistency beats git cleanliness.

**Invalid channel_id (non-`[A-Za-z0-9_-]+`)**: `ChannelStore` and `Scratchpad` reject with a clear error. Daemon falls back to `"cli-default"` and logs a warning.

## Testing strategy

### Unit tests

- `tests/test_channels.py` — `ChannelStore`: `get_or_create` creates and caches, replay rebuilds conversation, malformed jsonl lines skipped with warning, unreadable file renamed to `.corrupt-*` and returns fresh conversation, invalid channel_id rejected.
- `tests/test_scratchpad.py` — `read` empty when file missing, `write` creates dir + file + commit, size cap enforced, unreadable file returns empty with warning, concurrent writes serialized.
- `tests/test_conversation.py` (extend) — `Conversation(history_path=path)` appends every message to jsonl; in-memory-only path preserved when `history_path=None`.
- `tests/test_protocol.py` (extend) — `ChatMessage` / `CommandMessage` round-trip with and without `channel_id`.
- `tests/test_prompt_builder.py` (extend) — `build(channel_scratchpad="...")` includes the section between wisdom and commands; empty/None omits the section entirely.
- `tests/test_tools.py` (extend) — `update_scratch` calls `Scratchpad.write` with current channel_id from tool context; returns error when content over cap.
- `tests/test_daemon_channels.py` (new) — `ChatMessage(channel_id="C1")` routes to C1's conversation; `channel_id=None` falls back to `"cli-default"`; two channels kept separate; daemon restart replays history; `/note` command appends to correct channel's scratchpad.
- `tests/test_discord_adapter.py` (extend) — `on_message` with Discord channel_id forwards it to `client.chat`; slash command handlers likewise.

### Manual validation

- Post in `#gdb-mcp` about the MCP tool contract; post in a DM about something unrelated. Verify conversations don't leak across. Check `_channels/<gdb-mcp-id>/scratch.md` accumulates notes.
- Kill daemon mid-conversation, restart, send another message in the same channel. Verify replay restores context (PAL references prior turn content).
- Trigger PAL to update the scratchpad (ask it to note a decision). Check file on disk and `git log` for commit.
- Send `/note something important` in a channel. Verify the line appears in that channel's scratchpad and a commit lands.
- Open a brand-new channel, send first message. Verify fresh conversation, empty scratchpad (or initialized with whatever PAL chooses to write first).
- Force a scratchpad over the 2 KB cap (either via `/note` or by prompting PAL to record a lot). Verify user-visible error or tool-level error, no partial write.

### Explicitly not tested automatically

- Real Discord channel_id format (numeric snowflakes are opaque strings to us).
- Vault in deliberately-broken states (bad git hooks, corrupt repo).
- Multi-user concurrency (single-user deployment).

## Success criteria

- Two simultaneous channels keep their conversations fully separate. What PAL says in `#gdb-mcp` doesn't bleed into `#general` and vice versa.
- Daemon restart preserves conversation context per channel. Next message in channel C1 references prior turns without starting over.
- PAL can update and read a per-channel scratchpad. The scratchpad lives in the vault at `_channels/<channel_id>/scratch.md`, is git-committed, visible in Obsidian.
- User can `/note <text>` to manually append to the current channel's scratchpad.
- CLI behavior is unchanged from the user's perspective: one long-running "default" conversation that now also persists across daemon restarts.
- Size cap on the scratchpad prevents drift into a second wiki; oversized writes fail cleanly.
- No retrieval changes. Wisdom and profile still global.

## Follow-up work (out of scope)

- **Retrieval bias per channel** (explicit vault paths or tag-based). Revisit after observing whether persistent history plus scratchpad is enough. User has expressed reservations; address when concrete pain shows up.
- **Structured / typed channel memory** (a per-channel version of the auto-memory pattern). Revisit if scratchpad turns out to be too unstructured for long projects.
- **Cross-channel summarization** or "what did we talk about in #other?" tooling.
- **Channel deletion / archival** — currently channels persist forever. No UI to prune.
- **Launcher UX for multi-agent** — this spec doesn't address "which agent does this channel talk to?" That lands in the ecosystem extraction.
- **Per-channel `/think` or model preferences** — started as option B in the brainstorming but deferred to keep scope tight. Straightforward to add later if needed.
