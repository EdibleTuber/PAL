# Discord Adapter for PAL

**Date:** 2026-04-07
**Status:** Draft

## Overview

PAL currently has one client: the CLI. This feature adds a Discord adapter so you can talk to PAL from your phone (or anywhere Discord runs). The adapter is a thin translation layer between Discord and the existing PAL daemon. The daemon doesn't change at all.

## Goals

- Mobile access to PAL via Discord DMs
- Full PAL capability: chat with vault tools, all commands
- User allowlist so only authorized Discord users can interact
- Channel support for future expansion (project channels, etc.)

## Non-Goals

- Streaming responses in Discord (the protocol doesn't support it well)
- Daemon changes
- Multi-server support (single server is fine)
- Voice or media handling
- Discord slash command registration (too heavyweight, use `!` prefix instead)

## Architecture

```
Discord API
    |
    v
pal-discord (adapter process)
    |
    v
Unix Socket (same protocol as CLI)
    |
    v
pal-daemon (unchanged)
    |
    v
Vault, Inference Server, etc.
```

The adapter is a standalone process that maintains two connections:

1. **Discord side**: `discord.py` client, listens for DMs and @mentions
2. **Daemon side**: unix socket connection per user, same `ChatMessage`/`CommandMessage` protocol the CLI uses

### Message Flow

1. Discord user sends a DM or @mentions PAL in a channel
2. Adapter checks if the user is in the allowlist. If not, ignore silently.
3. Adapter parses the message:
   - Starts with `!`: parse as command (`!search quantum` becomes `CommandMessage(name="search", args="quantum")`)
   - Otherwise: send as `ChatMessage`
4. Adapter sends the message to the daemon over the user's socket connection
5. Adapter collects all responses:
   - `ToolProgressMessage`: accumulate as status lines
   - `StreamChunkMessage`: accumulate tokens into full text
   - `ResponseMessage`: final complete response
   - `ErrorMessage`: error text
6. Adapter formats and sends the result back to Discord

### Per-User Connections

Each allowed Discord user gets their own persistent daemon socket connection and conversation history. The adapter maintains a dict mapping Discord user IDs to `PalClient` instances. Connections are established lazily on first message and kept alive for the session.

If a connection drops, it is re-established on the next message.

## Response Rendering

### Accumulation (no streaming)

Discord doesn't support fast message editing for simulated streaming. The adapter accumulates the full response before sending. For tool-assisted responses, the adapter can send a brief "thinking" indicator (typing status or a placeholder message) while waiting.

### Tool Progress

Tool progress is prepended to the response as dim-styled lines:

```
*[reading Research/quantum.md...]*
*[editing Research/quantum.md...]*

Done. I've restructured the article with clear sections for...
```

Progress lines use Discord italic formatting to visually distinguish them from the main response.

### Message Splitting

Discord has a 2000-character limit per message. Long responses are split across multiple messages at paragraph boundaries (double newline). If a single paragraph exceeds 2000 characters, it is split at the last space before the limit.

### Markdown Differences

Discord markdown is mostly compatible with standard markdown. Key differences to handle:
- Code blocks work the same (triple backtick)
- Headers work but are less visually distinct
- No need for special translation; PAL's markdown output works in Discord as-is

## Commands

Commands use `!` prefix instead of `/` to avoid conflicting with Discord's built-in slash commands.

| CLI | Discord | Notes |
|-----|---------|-------|
| `/search quantum` | `!search quantum` | Same args |
| `/read Research/foo.md` | `!read Research/foo.md` | Same args |
| `/note topic` | `!note topic` | Same args |
| `/status` | `!status` | No args |
| `/quit` | (not needed) | Connection is persistent |
| `/help` | `!help` | Same output |
| All other commands | `!<name> <args>` | Direct mapping |

## User Allowlist

- Config: `PAL_DISCORD_ALLOWED_USERS` environment variable
- Format: comma-separated Discord user IDs (e.g., `123456789,987654321`)
- If empty or unset: bot responds to nobody (safe default)
- Non-allowed users are silently ignored (no error, no reaction)
- Discord user IDs are numeric strings, available via Discord's developer mode

## Bot Behavior

### When to respond

- **DMs**: always respond (if user is allowed)
- **Channel messages**: only respond when @mentioned (if user is allowed)
- **Channel messages without mention**: ignore

### Typing indicator

While waiting for the daemon's response, the adapter shows Discord's "bot is typing..." indicator. This gives the user feedback that PAL is working.

### Error handling

- Daemon connection failure: reply with "I can't reach the PAL daemon right now. Is it running?"
- Inference error: forward the daemon's error message
- Discord API error: log it, don't crash

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PAL_DISCORD_TOKEN` | Yes | (none) | Discord bot token |
| `PAL_DISCORD_ALLOWED_USERS` | Yes | (none) | Comma-separated Discord user IDs |
| `PAL_SOCKET_PATH` | No | `$XDG_RUNTIME_DIR/pal.sock` | Daemon socket (shared with CLI) |

## Deployment

### Entry point

New command: `pal-discord` (defined in pyproject.toml entry points), runs `pal.discord_main:main`.

### Systemd service

`systemd/pal-discord.service`:
- Type: simple
- After: pal-daemon.service
- Restart: on-failure
- Environment: reads from `/etc/pal/discord.env` or similar for token and user IDs

### Process model

`pal-discord` runs alongside `pal-daemon` as a separate process. Both can run on the same machine. The adapter connects to the daemon's unix socket just like the CLI does.

## Files

| File | Action | Responsibility |
|------|--------|---------------|
| `pal/discord_adapter.py` | Create | Discord client, user connection management, message translation, response formatting |
| `pal/discord_main.py` | Create | Entry point for `pal-discord` command |
| `systemd/pal-discord.service` | Create | Systemd unit file |
| `tests/test_discord_adapter.py` | Create | Unit tests for message parsing, response formatting, splitting, allowlist |
| `pyproject.toml` | Modify | Add `pal-discord` entry point, add `discord.py` dependency |

## Dependencies

- `discord.py>=2.0` (the standard Python Discord library)
- No other new dependencies. `httpx` and the rest are already present.

## Testing

- **Message parsing**: `!search foo` correctly becomes `CommandMessage(name="search", args="foo")`
- **Response formatting**: tool progress renders as italic, response follows
- **Message splitting**: long responses split at paragraph boundaries, each under 2000 chars
- **Allowlist**: allowed user gets response, non-allowed user is ignored
- **Connection management**: lazy connection creation, reconnection on drop
- **No integration tests against real Discord**: mock `discord.py` client in tests

## Future Considerations

- Project-specific channels where PAL monitors and participates
- Multiple PAL personalities or modes per channel
- Image/file attachment handling (vault screenshots, etc.)
- Shared conversation history across CLI and Discord
