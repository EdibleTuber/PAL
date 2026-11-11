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

Containment is enforced per entry point. Every check resolves the requested path and rejects it if the resolved location is outside the vault root — which covers `..` escapes, absolute paths, and symlinks inside the vault that point outside:

- The five vault write tools (`edit_file`, `create_file`, `move_file`, `delete_file`, `replace_in_file`) resolve every target with `_resolve_safe` (`pal/tools/vault.py:46`).
- Single moves are checked in `Reorganizer.move_single`, which raises `ValueError` for an escaping `src` or `dst` (`pal/reorg.py:44-46`). Batch reorgs are checked in `Reorganizer.validate_operations` (`pal/reorg.py:87-104`), which the tool layer runs both at propose time (`pal/tools/reorg.py:90`) and again immediately before execution (`pal/tools/reorg.py:293`), behind the approval gate (`pal/tools/reorg.py:275-287`) — so an escaping op is rejected before the user is ever asked to approve it, and re-checked in case the vault changed since the proposal.
- The `/import` slash command rejects `..` components and leading `/` on the raw string, then re-checks the resolved path against the vault root (`pal/commands/domain.py:72,82`).
- `WikiManager` resolves every article path it reads or writes through `_resolve_safe` (`pal/wiki.py:21`); the vault write tools and the consolidator write articles through it, so article writes are resolved before they touch the filesystem.

System directories (underscore-prefixed like `_wisdom/`, `_profile/`, `_channels/`) are blocked from the write tools by `_is_system_path` (`pal/tools/vault.py:54`); reorg ops reject them — plus `raw/` — on both `src` and `dst` (`pal/reorg.py:42`, `:99`).

The retrieval client is an HTTP client that never touches the vault filesystem. Its only traversal guard is the `doc_id` input check that runs before URL interpolation (`agent_core/retrieval.py:64`).

## Web Fetch

- Domain allowlist (`_config/allowlist.md`) gates web fetches in the chat-driven research pipeline (`propose_research` / `research_topic`)
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
