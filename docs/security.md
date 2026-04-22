# PAL Security Model

PAL is an agentic system that processes untrusted content and executes LLM-directed tool calls. Several layers of defense keep the vault and host safe.

## Prompt Injection

Fetched web content is wrapped in GUID-tagged `<untrusted-content>` boundaries with a per-request random UUID the attacker cannot predict. A sanitizer runs defense-in-depth before wrapping:

- Unicode NFC normalization
- Zero-width and bidirectional character stripping
- Model special-token removal
- Token-budget truncation

The BASE_PROMPT also explicitly instructs the model to treat any imperative directives found inside web content as data, not commands.

## Path Traversal

All file operations resolve paths through a safe-path check that rejects:

- `..` components
- Leading `/` (absolute paths)
- Any resolved path outside the vault root

System directories (underscore-prefixed like `_wisdom/`, `_profile/`, `_channels/`) are additionally blocked from write operations. The same guards apply to chat tools, slash commands, and the retrieval client.

## Web Fetch

- Domain allowlist (`_config/allowlist.md`) gates both `/search-web` results and `/fetch` targets
- Only `http` and `https` schemes are accepted
- Redirects are not followed (prevents SSRF via open redirects to internal hosts)
- Content-Type is validated and response size is capped (`PAL_FETCH_MAX_BYTES`)

## Access Control

- The daemon listens on a Unix socket; filesystem permissions are the access boundary
- Discord access is restricted to an explicit user ID allowlist (`PAL_DISCORD_ALLOWED_USERS`)
- Each Discord user gets an isolated daemon connection (one `PalClient` per user)
- Per-channel conversation history is keyed by channel ID so different channels of the same user stay isolated

## Channel ID Validation

Incoming `channel_id` strings from the protocol are validated against `^[A-Za-z0-9_-]+$` before use as a filesystem path. Invalid IDs fall back to the `cli-default` channel with a warning log. This prevents path-traversal pranks via a compromised or misbehaving client.

## Git Safety Net

Every vault write is automatically git-committed (via `WikiManager`), so any unwanted change can be reviewed with `git log` / `git diff` and reverted with `git revert` or `git checkout`.

## Scratchpad Size Cap

The per-channel scratchpad (`_channels/<id>/scratch.md`) is capped at `PAL_SCRATCHPAD_MAX_BYTES` (default 2 KB). Oversized writes fail cleanly with a clear error message rather than succeeding and silently bloating the system prompt. This limits the damage from a model or user trying to stuff unbounded content into the context.

## Explicit Non-Goals

- **No sandboxing of LLM-generated content** beyond the prompt-injection defenses above. PAL assumes the human reading the output is the final check.
- **No cryptographic signing** of vault commits — git's integrity and the local filesystem permissions are the trust boundary.
- **No network-level authentication** between PAL components. The Unix socket's filesystem permissions are the ACL.
