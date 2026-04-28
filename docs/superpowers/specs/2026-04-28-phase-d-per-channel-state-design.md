# Phase D Design: Per-Channel State + Protocol Split

**Status:** Approved 2026-04-28
**Phase:** D of agent_core extraction (umbrella spec: `docs/superpowers/specs/2026-04-25-agent-core-extraction-design.md`)
**Predecessors:** Phase A (utils, v0.1.1), Phase B (stateless clients, v0.2.0), Phase C (stateful managers, v0.3.0)
**Target release:** `agent_core` v0.4.0

## Goal

Move PAL's per-channel state (`channels`, `scratchpad`, `conversation`) and the parked `learning_scanner` into `agent_core`. Resolve the protocol-module coupling by splitting `pal/protocol.py` into a generic transport layer (in `agent_core`) and PAL-specific message types (in `pal`).

## Scope decisions

### 1. Protocol split (Option B)

`pal/protocol.py` is two things stuck together:
- **Generic transport** (encode/decode, message-type registry, `STREAM_BUFFER_LIMIT`) and **generic message primitives** (`Chat`, `Command`, `StreamChunk`, `Response`, `Error`, `ToolProgress`). Reusable by any agent with a daemon-CLI architecture.
- **PAL-domain proposals** (Research, Compile, Reorg, Consolidate, Promote, BatchFallback x2). PAL workflow types.

The split:
- `agent_core.protocol.transport` gets the encode/decode machinery and a `register_message(cls)` function that populates a module-level `_MESSAGE_TYPES` registry.
- `agent_core.protocol.messages` gets the six generic primitives plus `LearningCandidateProposalMessage` (the only proposal message that's general enough to belong in a shared library: any agent that watches conversations for behavioral signals would propose the same shape).
- `pal/protocol.py` shrinks to PAL-specific proposal dataclasses that register themselves with `agent_core.protocol`'s registry at import time.

The `Message` union type from `pal/protocol.py` is dropped from `agent_core`. Callers either branch on `isinstance` or use `decode_message`'s registry-driven dispatch. PAL keeps a local union if it needs one for type hints.

PAL imports are rewritten across the codebase: every `from pal.protocol import X` where `X` is a generic primitive becomes `from agent_core.protocol import X`. No back-compat re-exports in `pal/protocol.py`.

### 2. Scratchpad commit decoupling (Option A, refined contract)

`Scratchpad` currently takes a `WikiManager` dependency and calls `wiki.git_commit(message)` after every write. WikiManager is firmly PAL-specific.

Replacement: optional callback `commit_callback: Callable[[Path, str], None] | None = None`. Signature is `(path, message)` rather than `(message,)` so generic helpers can do per-file commits cleanly. PAL's adapter ignores the path and forwards to `wiki.git_commit`.

`agent_core.git_helpers.make_commit_callback(vault_path)` returns a callable that does `git -C <vault> add <path> && git commit -m <message>`. Future agents that want bare git tracking use it; PAL doesn't (its WikiManager-backed callback is more sophisticated).

When `commit_callback` is `None`, scratchpad writes succeed with no git activity. Agents without git-tracked vaults pass `None`.

### 3. Conversation overrides (Option C)

`Conversation.reasoning_override: Literal["on", "off"] | None` is a PAL-ism (reasoning-control feature). Replaced with a generic `overrides: dict[str, Any] = field(default_factory=dict)`. PAL stores `{"reasoning": "on"}` or `{"reasoning": "off"}`.

This is forward-compatible: future per-conversation toggles slot in without further extraction work.

### 4. Storage convention

Following the Phase C decision (vault-rooted, per-agent subdir):

| Today | After Phase D |
|---|---|
| `<vault>/_channels/<channel_id>/history.jsonl` | `<vault>/_channels/<agent_name>/<channel_id>/history.jsonl` |
| `<vault>/_channels/<channel_id>/scratch.md` | `<vault>/_channels/<agent_name>/<channel_id>/scratch.md` |

PAL passes `agent_name="pal"`. Existing data is migrated in place by a one-shot script.

## Module layout

```
agent_core/
├── conversation.py         # Conversation dataclass: rolling message buffer + JSONL persistence
├── channels.py             # ChannelStore: per-channel Conversation cache, vault-rooted
├── scratchpad.py           # Scratchpad: vault-bound markdown file with optional commit callback
├── learning_scanner.py     # LearningScanner: signal+extract+dedupe+emit pipeline
├── git_helpers.py          # make_commit_callback(vault_path) helper for Scratchpad consumers
├── protocol/
│   ├── __init__.py         # re-exports the public surface
│   ├── transport.py        # encode_message, decode_message, STREAM_BUFFER_LIMIT, register_message
│   └── messages.py         # ChatMessage, CommandMessage, StreamChunkMessage, ResponseMessage,
│                           # ErrorMessage, ToolProgressMessage, LearningCandidateProposalMessage
├── utils/                  # Phase A
└── ...                     # Phase B/C modules
```

In `pal/`:
- `pal/protocol.py` shrinks to PAL-specific proposal dataclasses + registration calls.
- `pal/conversation.py`, `pal/channels.py`, `pal/scratchpad.py`, `pal/learning_scanner.py` are deleted.

## Public API

### `agent_core.conversation.Conversation`

```python
@dataclass
class Conversation:
    history_depth: int
    history_path: Path | None = None
    overrides: dict[str, Any] = field(default_factory=dict)
    _messages: list[dict] = field(default_factory=list)

    @property
    def messages(self) -> list[dict]: ...
    def add_user(self, text: str) -> None: ...
    def add_assistant(self, text: str) -> None: ...
    def add_assistant_tool_calls(self, tool_calls: list[dict]) -> None: ...
    def add_tool_result(self, tool_call_id: str, content: str) -> None: ...
    def get_messages_for_api(self, system_prompt: str) -> list[dict]: ...
    def clear(self) -> None: ...
```

Behavior unchanged from `pal.conversation` apart from `reasoning_override` being replaced by `overrides`.

### `agent_core.channels.ChannelStore`

```python
class ChannelStore:
    def __init__(self, vault_path: Path, agent_name: str, history_depth: int) -> None: ...
    async def get_or_create(self, channel_id: str) -> Conversation: ...
```

Was `(channels_dir, history_depth)`. Now resolves the channels directory internally as `vault_path / "_channels" / agent_name`.

### `agent_core.scratchpad.Scratchpad`

```python
class Scratchpad:
    def __init__(
        self,
        vault_path: Path,
        agent_name: str,
        channel_id: str,
        max_bytes: int,
        commit_callback: Callable[[Path, str], None] | None = None,
    ) -> None: ...

    def read(self) -> str: ...
    def write(self, content: str) -> None: ...   # raises ScratchpadTooLarge if over cap
    def append(self, text: str) -> None: ...
```

Path resolves as `vault_path / "_channels" / agent_name / channel_id / "scratch.md"`.

`ScratchpadTooLarge` exception is re-exported from the module.

### `agent_core.git_helpers.make_commit_callback`

```python
def make_commit_callback(vault_path: Path) -> Callable[[Path, str], None]:
    """Return a callback that runs `git -C <vault> add <path> && git commit -m <message>`."""
```

### `agent_core.learning_scanner.LearningScanner`

Unchanged shape:

```python
class LearningScanner:
    def __init__(
        self,
        learning_manager,
        extractor: Callable[..., Awaitable],
        emit: Callable[[LearningCandidateProposalMessage], None],
    ) -> None: ...
    def mark_pending(self, proposal_id: str) -> None: ...
    def clear_pending(self) -> None: ...
    def take_pending(self, proposal_id: str) -> LearningCandidateProposalMessage | None: ...
    async def maybe_scan(self, recent_turns: list[dict], latest_user_message: str) -> None: ...
```

`extract_candidate`, `has_signal`, `is_duplicate_candidate` move along as module-level helpers.

### `agent_core.protocol`

Public surface (re-exported from `__init__.py`):
- `STREAM_BUFFER_LIMIT`
- `register_message(cls)`
- `encode_message(msg) -> bytes`
- `decode_message(data) -> object`
- `ChatMessage`, `CommandMessage`, `StreamChunkMessage`, `ResponseMessage`, `ErrorMessage`, `ToolProgressMessage`, `LearningCandidateProposalMessage`

PAL's `pal/protocol.py` after the split looks like:

```python
from agent_core.protocol import register_message
# (and re-imports of generic types via existing call sites that move to agent_core.protocol)

@dataclass
class ResearchProposalMessage:
    ...

register_message(ResearchProposalMessage)
# ... and so on for the eight PAL-specific message types
# (Research, ResearchApprovalResponse, Compile, Reorg, Consolidate, Promote,
#  BatchFallbackProposal, BatchFallbackApproval)
```

## Migration

A one-shot script `scripts/migrate_phase_d.py` lives in PAL and runs once on the server (the user runs it; we don't SSH).

Behavior:
1. Walk `<vault>/_channels/`. For each entry that is a directory whose name is a valid channel id (matches `_CHANNEL_ID_PATTERN`) and is not `pal/`:
2. Move `<vault>/_channels/<channel_id>/` into `<vault>/_channels/pal/<channel_id>/`.
3. Idempotent: if `<vault>/_channels/pal/<channel_id>/` already exists, skip with a warning. If both exist (interrupted run), refuse to overwrite and surface a clear error.
4. Logs each move.

Both `history.jsonl` and `scratch.md` move together because they live in the same per-channel directory. Channels with only one file are handled correctly (the directory move is the unit of work).

## PAL-side adapter changes

PAL's `daemon.py` (and any related construction sites) updates:

```python
# Before
self.channels = ChannelStore(
    channels_dir=self.vault_path / "_channels",
    history_depth=self.config.history_depth,
)
# After
self.channels = ChannelStore(
    vault_path=self.vault_path,
    agent_name="pal",
    history_depth=self.config.history_depth,
)
```

```python
# Before
scratchpad = Scratchpad(self.vault_path, channel_id, self.wiki, max_bytes)
# After
def _commit_scratchpad(path: Path, message: str) -> None:
    self.wiki.git_commit(message)

scratchpad = Scratchpad(
    vault_path=self.vault_path,
    agent_name="pal",
    channel_id=channel_id,
    max_bytes=max_bytes,
    commit_callback=_commit_scratchpad,
)
```

Reasoning-override call sites (in `daemon.py` and possibly `commands.py`):

```python
# Before
conv.reasoning_override = "on"
if conv.reasoning_override == "on": ...
# After
conv.overrides["reasoning"] = "on"
if conv.overrides.get("reasoning") == "on": ...
```

LearningScanner emit construction in `daemon.py`:

```python
# Before
from pal.protocol import LearningCandidateProposalMessage
# After
from agent_core.protocol import LearningCandidateProposalMessage
```

## Testing

### agent_core (new)

- `tests/test_conversation.py`: rolling-window truncation, tool-call/tool-result truncation guards, JSONL persistence and replay, `overrides` dict round-trips.
- `tests/test_channels.py`: `validate_channel_id`, `get_or_create` cache, history replay, corrupt-file rename. Path assertions match `<vault>/_channels/<agent_name>/<channel_id>/`.
- `tests/test_scratchpad.py`: read/write/append, size cap raising `ScratchpadTooLarge`, callback receives `(path, message)`, `commit_callback=None` works (write succeeds with no commit).
- `tests/test_learning_scanner.py`: `has_signal`, `extract_candidate` (timeout, BatchUnavailable, JSON parse failures), `is_duplicate_candidate`, `maybe_scan` queueing/draining behavior. Imports `LearningCandidateProposalMessage` from `agent_core.protocol`.
- `tests/test_protocol.py`: registry round-trips for each generic message type, `decode_message` raises `ValueError` on unknown type, external registration works (proves the PAL pattern).
- `tests/test_git_helpers.py`: `make_commit_callback` against a `tmp_path` git repo verifies commits land.

### PAL (updated)

- Existing tests update imports to point at `agent_core` for moved symbols.
- `tests/test_protocol.py` shrinks to cover only PAL-specific message types and verifies they register correctly with `agent_core`'s registry.
- Reasoning-control tests update to use `overrides["reasoning"]`.
- `tests/test_phase_d_migration.py`: builds a tmp vault with old-style `_channels/<id>/` directories, runs the migration script, verifies result is `_channels/pal/<id>/` and is idempotent on a second run.

The five known-flaky integration tests (`tests/test_daemon.py`, `tests/test_integration.py`, `tests/test_chat_research_integration.py`, `tests/test_consolidate_integration.py`, `tests/test_learning_e2e.py`) stay ignored in broad runs.

## Execution shape

Mirrors Phases B and C: agent_core ships first as a tagged release, then PAL bumps the dependency and migrates call sites in a single feature branch.

### agent_core (PR, then tag v0.4.0)

1. Add `agent_core/protocol/` (transport + generic messages, including `LearningCandidateProposalMessage`). Tests.
2. Add `agent_core/conversation.py`. Tests.
3. Add `agent_core/channels.py`. Tests.
4. Add `agent_core/git_helpers.py` and `agent_core/scratchpad.py`. Tests.
5. Add `agent_core/learning_scanner.py`. Tests.
6. Bump `pyproject.toml` version to `0.4.0`. Update CHANGELOG. PR, merge, tag `v0.4.0`.

### PAL (`feature/agent-core-extraction-phase-d`)

1. Bump `agent_core` dependency to `0.4.0`.
2. Rewrite `pal/protocol.py`: import generics from `agent_core.protocol`, keep PAL-specific dataclasses, register them. Update all `from pal.protocol import X` imports across PAL where X is a generic primitive to point at `agent_core.protocol`.
3. Migrate `pal/conversation.py` usage to `agent_core.conversation`. Delete `pal/conversation.py`. Swap `reasoning_override` for `overrides["reasoning"]`.
4. Migrate `pal/channels.py` usage to `agent_core.channels`. Delete `pal/channels.py`. Update construction to pass `vault_path` and `agent_name="pal"`.
5. Migrate `pal/scratchpad.py` usage to `agent_core.scratchpad`. Delete `pal/scratchpad.py`. Replace `wiki=` with `commit_callback=`.
6. Migrate `pal/learning_scanner.py` usage to `agent_core.learning_scanner`. Delete `pal/learning_scanner.py`.
7. Add `scripts/migrate_phase_d.py`. The user runs it on the server.
8. Server smoke test: daemon starts, channels load history, scratchpad writes commit, learning scanner emits proposals, reasoning override works, full chat turn round-trips correctly.

## Risks

- **Protocol rewrite is the largest single grep-and-replace pass in this phase.** Per the Phase C lesson, broad grep needs to catch quoted string usages (`"pal.protocol.X"`, `'pal.protocol.X'`) in `monkeypatch.setattr(...)` and `mock.patch(...)` calls, plus bare attribute references like `pal.protocol.X.foo`.
- **The migration script must handle all directory shapes:** channels with only `history.jsonl`, only `scratch.md`, both, or neither (empty channel directory). Idempotency on partial-failure resumption is mandatory.
- **Reasoning-override migration touches files I haven't fully audited.** The implementation plan inventories them. Likely candidates: `daemon.py`, `commands.py`, possibly `prompt_builder.py`.
- **`pal/protocol.py` re-export removal is a hard cut.** Any leftover `from pal.protocol import ChatMessage` after the import-rewrite will fail at import time. CI catches it.

## Out of scope

- Daemon skeleton, BaseConfig, Agent base class, CLI REPL extraction. **Phase E.**
- Discord adapter. **Phase F.**
- WikiManager extraction. PAL keeps it; future phases may revisit.

## Decisions log

| # | Question | Decision | Rationale |
|---|---|---|---|
| 1 | How to decouple `learning_scanner` from `pal.protocol`? | Move generic protocol machinery + `LearningCandidateProposalMessage` to `agent_core` (Option B). | Single refactor instead of two; `LearningCandidateProposalMessage` is a generic concept any agent's learning loop would use. |
| 2 | How to decouple `Scratchpad` from `WikiManager`? | Inject `commit_callback: (Path, str) -> None` (Option A, refined). | One-line indirection; `(path, message)` signature lets generic helpers do per-file commits cleanly. |
| 3 | Generalize `Conversation.reasoning_override`? | Replace with `overrides: dict[str, Any]` (Option C). | Forward-compatible; future per-conversation toggles slot in without further extraction. |
| 4 | Channel/scratchpad storage layout? | `<vault>/_channels/<agent_name>/<channel_id>/` (Phase C convention). | Consistent with the rest of agent_core's vault layout. |
| 5 | Permanent re-exports from `pal/protocol.py` or rewrite imports across PAL? | Rewrite imports. | Clean cut; avoids ambiguous forwarding module long-term. |
