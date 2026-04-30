# Phase E Design: Runtime Infrastructure

**Status:** Approved 2026-04-30
**Phase:** E of agent_core extraction (umbrella spec: `docs/superpowers/specs/2026-04-25-agent-core-extraction-design.md`)
**Predecessors:** Phases A (utils, v0.1.1), B (stateless clients, v0.2.0), C (stateful managers, v0.3.0), D (per-channel state + protocol split, v0.4.0). Plus the inference safety v0.3.1 release.
**Target release:** `agent_core` v0.5.0

## Goal

Extract the agent runtime infrastructure from PAL into `agent_core` so PAL's daemon stops being a 2000-line monolith and becomes a small `Agent` subclass. Specifically: ship `Agent` base class, `BaseConfig`, `run_daemon` entry point, generic daemon core, socket client, and CLI REPL adapter.

## Non-Goals

- Extracting tool/command/prompt scaffolding. That's Phase F.
- Discord adapter. That's Phase G.
- Agent template repo. That's Phase H.
- Per-channel preemption logic in the daemon (deferred safety fix territory; Phase E reserves the `_chat_tasks` field but doesn't wire preemption yet).
- Splash polish. PAL's existing splash text lifts unchanged. Cosmetic improvements happen as a follow-up commit on PAL after Phase E ships.

## Phase scope decision

The umbrella spec originally split this work into Step 5 (tool/command/prompt scaffolding) and Step 6 (daemon, runtime, Agent, CLI). We split similarly into Phase E (this spec, runtime infrastructure) and Phase F (tool/command/prompt scaffolding). This adds one phase to the count (8 → 9), shifting Discord/template/burn-in to Phases G/H/I.

Reasons for the split:
- Each half is independently shippable and reviewable.
- Phase E defines the API surface (`Agent`, `BaseConfig`, `run_daemon`); Phase F can move dispatch internals (tool executor, command registry, prompt builder) against a known target API.
- Doing both at once means designing two APIs simultaneously while moving 4500+ lines of PAL code, which compounds rebase risk.

## Architecture

agent_core adds five new modules and gains an `adapters/` package. PAL gets one new file (`pal/agent.py`), one major shrink (`pal/cli.py`), one rename (`pal/daemon.py` → split between `pal/agent.py` and `pal/daemon_main.py`), and three deletions.

```
agent_core/
├── agent.py        # Agent base class (the extension surface)
├── config.py       # BaseConfig dataclass + env-var loader
├── client.py       # socket client, moved from pal/client.py
├── daemon.py       # connection lifecycle, dispatch, per-channel registry
├── runtime.py      # run_daemon(agent) entry point
└── adapters/
    ├── __init__.py
    └── cli.py      # generic REPL with Renderer protocol
```

The dependency arrow stays one-way: `agent_core` never imports from PAL.

## Decisions log

| # | Question | Decision | Rationale |
|---|---|---|---|
| 1 | Phase E scope | Split into Phase E (runtime) + Phase F (scaffolding). | Two independent units; smaller rebase blast radius; Phase F can target a known API. |
| 2 | Agent class shape | Hybrid (option C): daemon constructs framework managers from BaseConfig; Agent subclass constructs domain-specific resources in `setup()`. | agent_core managers all take `(vault_path, agent_name)`; no reason to duplicate construction in every agent. Domain stuff (PAL's WikiManager, Categorizer) genuinely needs custom construction. |
| 3 | BaseConfig scope | Generous (option A): all of PAL's currently-shared fields go in BaseConfig. Env prefix derived from `Agent.name` unless explicitly overridden. PAL adds one field (`max_inference_body_chars`) via `PALConfig(BaseConfig)`. | Matches umbrella spec. Mixin alternatives add cognitive load against the umbrella's "no plugin magic" principle. |
| 4 | Daemon dispatch architecture | Thin daemon, Agent controller (option A): daemon does only connection lifecycle + decode + dispatch + encode + per-channel registry. Agent.handle_chat / Agent.handle_command do everything else. | Cleanest seam; PAL's `_handle_chat` lifts mostly intact to `PALAgent.handle_chat`; Phase F can later add a default `Agent.handle_chat` that uses registries. |
| 5 | CLI REPL | Generic REPL with Renderer plug-in (option A): `agent_core.adapters.cli.run_repl(socket_path, renderer)`. Renderer protocol has `splash()` and `format_message(msg)`. PAL ships `PALRenderer`. | Mirrors Q4's pattern (thin generic + agent-provides-handler). New agents get a working REPL with a small renderer class. |

## `agent_core.config.BaseConfig`

A dataclass mirroring PAL's current `Config` but with name-derived env-var prefix machinery. The fields are the universally-shared infrastructure ones; agents subclass to add domain fields.

```python
@dataclass
class BaseConfig:
    inference_url: str = "http://192.168.1.14:11434"
    model: str = "Qwen3.5-35B-A3B-Q4_K_M"
    socket_path: Path | None = None         # None: derive from agent_name
    history_depth: int = 50
    vault_path: Path = field(default_factory=lambda: Path.home() / "vault")
    collection_id: str = "vault"
    username: str = "user"
    searxng_url: str = "http://192.168.1.14:8080"
    fetch_max_bytes: int = 2_000_000
    fetch_timeout: int = 30
    max_response_tokens: int = 4096
    batch_enabled: bool = False
    batch_inference_url: str = "http://192.168.1.14:11434"
    batch_model: str = "gemma-4-E4B-it-Q4_K_M"
    scratchpad_max_bytes: int = 2048
```

Notes:
- `channels_dir` is removed (Phase D made it derived as `vault_path / "_channels" / agent_name`).
- `max_response_tokens` is the cap that the inference safety stopgap hardcoded; Phase E lifts it into config.
- `socket_path` defaults to `None`; the loader derives `${XDG_RUNTIME_DIR:-/run/user/$UID}/{agent_name}.sock` when not explicitly set.

The loader:

```python
def load_config(
    config_cls: type[BaseConfig], agent_name: str, env_prefix: str | None = None,
) -> BaseConfig:
    """Load config from env vars. env_prefix derived from agent_name unless overridden."""
    prefix = env_prefix if env_prefix is not None else f"{agent_name.upper().replace('-', '_')}_"
    kwargs: dict = {}
    for f in fields(config_cls):
        env_name = f"{prefix}{f.name.upper()}"
        if env_name not in os.environ:
            continue
        raw = os.environ[env_name]
        kwargs[f.name] = _coerce(f.type, raw)
    cfg = config_cls(**kwargs)
    if cfg.socket_path is None:
        cfg.socket_path = _default_socket_path(agent_name)
    return cfg
```

`_coerce` uses `typing.get_type_hints` to dispatch by field type: int, bool, Path, str. Tests cover each.

PAL's subclass:

```python
@dataclass
class PALConfig(BaseConfig):
    max_inference_body_chars: int = 20_000

def load_config() -> PALConfig:
    from agent_core.config import load_config as _load
    return _load(PALConfig, agent_name="pal")
```

Backward compatibility: every `PAL_*` env var keeps working unchanged.

## `agent_core.agent.Agent`

The base class. Framework managers are populated by `run_daemon` before `setup()` runs.

```python
@dataclass
class HandlerContext:
    conversation: Conversation
    channel_id: str
    writer: object   # asyncio.StreamWriter; framework-internal


class Agent:
    name: ClassVar[str]
    env_prefix: ClassVar[str | None] = None  # None: derive from name

    # Populated by run_daemon before setup() is called.
    config: BaseConfig
    profile: ProfileManager
    wisdom: WisdomManager
    learning: LearningManager
    allowlist: AllowlistManager
    approval_registry: ApprovalRegistry
    channels: ChannelStore
    inference: InferenceClient
    retrieval: RetrievalClient
    websearch: WebSearchClient

    def setup(self) -> None: pass

    def system_prompt(self, ctx: HandlerContext) -> str:
        raise NotImplementedError

    async def handle_chat(
        self, msg: ChatMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        raise NotImplementedError

    async def handle_command(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        raise NotImplementedError

    def decide_mode(self, conversation: Conversation) -> str:
        from agent_core.reasoning import decide_mode
        return decide_mode(conversation)
```

Phase E intentionally does NOT include `commands: list[Command] = []` and `tools: list[Tool] = []` class attributes. Those become meaningful in Phase F when registries exist.

## `agent_core.daemon` and `agent_core.runtime`

Thin transport-only daemon. Per Q4 (option A), the daemon does connection lifecycle + decode + dispatch + encode + per-channel state. The Agent owns chat and command logic.

`agent_core/daemon.py` includes a `Daemon` class with `serve()`, `_handle_connection()`, and `_run_handler()` methods. The handler runner does:

```python
async def _run_handler(self, handler, msg, ctx, writer):
    try:
        async for response in handler(msg, ctx):
            writer.write(encode_message(response))
            await writer.drain()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("handler error: %s", exc)
        try:
            err = ErrorMessage(error=f"{type(exc).__name__}: {exc}")
            writer.write(encode_message(err))
            await writer.drain()
        except Exception:
            pass
```

The daemon reserves `self._chat_tasks: dict[str, asyncio.Task] = {}` for the deferred safety-fix's per-channel preemption. Phase E does not use it (uses per-connection `owned_tasks` list, matching today's behavior).

`agent_core/runtime.py` provides `run_daemon`:

```python
def run_daemon(agent: Agent, config_cls: type[BaseConfig] = BaseConfig) -> None:
    config = load_config(config_cls, agent_name=agent.name, env_prefix=agent.env_prefix)
    agent.config = config
    agent.profile = ProfileManager(config.vault_path, agent_name=agent.name, username=config.username)
    agent.wisdom = WisdomManager(config.vault_path, agent_name=agent.name)
    agent.learning = LearningManager(config.vault_path, agent_name=agent.name)
    agent.allowlist = AllowlistManager(config.vault_path, agent_name=agent.name)
    agent.approval_registry = ApprovalRegistry()
    agent.channels = ChannelStore(
        vault_path=config.vault_path,
        agent_name=agent.name,
        history_depth=config.history_depth,
    )
    agent.inference = InferenceClient(base_url=config.inference_url, model=config.model)
    agent.retrieval = RetrievalClient(base_url=config.inference_url, collection_id=config.collection_id)
    agent.websearch = WebSearchClient(base_url=config.searxng_url)
    agent.setup()

    logging.basicConfig(level=logging.INFO)
    daemon = Daemon(agent)
    asyncio.run(daemon.serve())
```

PAL's daemon entry shrinks to:

```python
# pal/daemon_main.py
from agent_core.runtime import run_daemon
from pal.agent import PALAgent
from pal.config import PALConfig

def main():
    run_daemon(PALAgent(), config_cls=PALConfig)
```

## `agent_core.client`

Mechanical move of `pal/client.py`. Provides `DaemonConnection` with `connect()`, `send(msg)`, `receive() -> AsyncIterator[Message]`, `close()`. Tests carry over.

If PAL's `tests/test_client.py` hang (pre-existing) traces to a PAL-specific use, agent_core's copy should be hang-free. If it's intrinsic, agent_core's copy will hang too and we'll add it to agent_core's known-flaky list. Validation happens during the move.

## `agent_core.adapters.cli`

Generic REPL with a Renderer protocol.

```python
@runtime_checkable
class Renderer(Protocol):
    def splash(self) -> str: ...
    def format_message(self, msg: object) -> str | None: ...


async def run_repl(socket_path: Path, renderer: Renderer) -> None:
    """Connect, prompt-toolkit input loop, decode/encode NDJSON, render messages
    via renderer, fall back to default rendering when renderer returns None."""
```

Default rendering inside `agent_core.adapters.cli` handles the seven generic message types from Phase D's protocol (Chat, Command, StreamChunk, Response, Error, ToolProgress, LearningCandidateProposal).

PAL's `pal/cli.py` post-Phase-E shrinks to a `PALRenderer` class with the six PAL-specific proposal formatters and the splash, plus a 4-line `main()`. Total ~120 LOC (down from 462).

CLI history file: `~/.local/state/agent_core/cli_history` (single shared history; per-agent history is a Phase F+ refinement if it ever matters).

## PAL migration

| File | Phase E change |
|---|---|
| `pal/agent.py` | NEW. `PALAgent(Agent)` with `setup()`, `system_prompt()`, `handle_chat()`, `handle_command()`. ~400 LOC. |
| `pal/daemon.py` | DELETED. Body splits between `pal/agent.py` (handlers) and `pal/daemon_main.py` (entry). |
| `pal/daemon_main.py` | Body shrinks to `run_daemon(PALAgent(), config_cls=PALConfig)`. |
| `pal/client.py` | DELETED. Imports update to `agent_core.client`. |
| `pal/cli.py` | Shrinks 462 → ~120 LOC. `PALRenderer` + `main()`. |
| `pal/config.py` | Shrinks 74 → ~20 LOC. `PALConfig(BaseConfig)` + thin `load_config()`. |
| `pal/commands.py`, `pal/tools.py`, `pal/prompt_builder.py` | UNCHANGED in Phase E. (Move in Phase F.) |
| `pal/discord_adapter.py`, `pal/discord_interactions.py` | UNCHANGED. (Move in Phase G.) |

### Lifting `_handle_chat`

PAL's current `_handle_chat` (around `pal/daemon.py:415-660`) becomes `PALAgent.handle_chat`. The body change pattern:

| Before (in `Daemon._handle_chat`) | After (in `PALAgent.handle_chat`) |
|---|---|
| `self.config` | `self.config` (same; config is on PALAgent) |
| `self.wiki`, `self.categorizer`, `self.researcher`, `self.compiler` | Same; constructed in `setup()` |
| `self.profile`, `self.wisdom`, `self.learning`, `self.allowlist`, `self.approval_registry`, `self.channels`, `self.inference`, `self.retrieval`, `self.websearch` | Same; populated by run_daemon |
| `conv` (param) | `ctx.conversation` |
| `channel_id` (param) | `ctx.channel_id` |
| `writer` (param) | `ctx.writer` |
| `tool_executor` (param) | `self.tool_executor` (set in `setup()`) |
| `scanner` (param) | `self.scanner` (set in `setup()`) |
| Direct `writer.write(encode_message(chunk))` | Same. (Phase E preserves writer-passing for compatibility.) |
| Final return | Replace with end-of-iteration; the daemon detects iterator exhaustion. |

The handler is an `async def` returning an `AsyncIterator`, so it `yield`s response messages. Stream chunks written via `writer.write` directly during the turn (for streaming UX); the iterator yield is mostly used for final responses and proposal messages.

### Lifting command dispatch

PAL's current `_handle_connection`'s command branch (the `isinstance(msg, CommandMessage)` block plus the `_handle_command` body) becomes `PALAgent.handle_command`. It dispatches via `pal.commands.COMMANDS` direct lookup. Phase F replaces this with a registry.

## Testing strategy

### agent_core (new)

| File | Coverage |
|---|---|
| `tests/test_config.py` | BaseConfig defaults, env-var override per type, prefix derivation from `name` (with hyphen-to-underscore), explicit env_prefix override, subclassing adds fields, `socket_path` derivation when None. |
| `tests/test_agent.py` | Framework attrs are populated correctly, `setup()` runs after population, `decide_mode` default delegates to reasoning, subclassing without errors. |
| `tests/test_daemon.py` | Connection lifecycle, NDJSON encode/decode round-trip with a mock agent, dispatch routes ChatMessage to `agent.handle_chat`, dispatch routes CommandMessage to `agent.handle_command`, disconnect cancels owned tasks, malformed messages produce ErrorMessage. |
| `tests/test_runtime.py` | `run_daemon` populates all framework attrs, calls `setup()`, starts daemon. Mocks `asyncio.run` to assert wiring without actually running. |
| `tests/test_client.py` | DaemonConnection round-trip against an in-process server. (If hang issue is intrinsic, mark known-flaky.) |
| `tests/test_cli.py` | REPL with mock renderer + mock daemon: splash printed, chat message sent, response messages rendered via renderer, fallback to default rendering when renderer returns None, /command parsing. |

### agent_core contract tests (new umbrella requirement)

These live in agent_core forever as the API guarantee:
- `test_minimal_agent_boots`: define a one-line `Agent` subclass, call `run_daemon` with mocks, assert socket comes up.
- `test_agent_receives_chat`: send a chat message, verify `Agent.handle_chat` is called.
- `test_agent_handle_chat_yields_responses`: verify the daemon writes each yielded message to the socket.

### PAL test changes

- `tests/test_daemon_*` and `tests/test_chat_*` carry over with the handler logic now living on `PALAgent`. Behavior is unchanged.
- Tests asserting the old `current_chat_task` rejection ("A previous turn is still being processed") are deleted.
- `tests/test_config.py` updates to test `PALConfig` (just the `max_inference_body_chars` field) since the rest moves to agent_core's tests.
- `tests/test_cli.py` (PAL) covers PALRenderer formatters in isolation.

### Smoke checklist (manual on server)

After deploy: daemon starts, CLI connects and shows splash, `/help` works, chat round-trips, `/research` round-trips, channel history loads, scratchpad works, learning scanner emits a candidate, restart-and-resume works.

## Risks

1. **PAL's `_handle_chat` is ~500 LOC of tangled logic.** Lifting is mechanical but the seams (writer, conv, scratchpad, tool_executor, scanner, approval_registry) all need to come over. Mitigation: incremental cutover, focused test slice after each chunk.
2. **The `writer` reference in `HandlerContext` is awkward** but necessary for streaming chunks mid-turn. Phase F may revisit.
3. **`PALAgent.handle_command` references `pal.commands.COMMANDS` directly.** Replaced by a registry in Phase F.
4. **`test_client.py` hangs.** Pre-existing pal-side issue; agent_core's moved copy needs validation. If hang persists, add agent_core's test file to its known-flaky list.
5. **PAL's existing tests for the rejection guard** ("A previous turn is still being processed") need deletion. The safety fix's preemption already removed that string from PAL's behavior; this just removes test noise.

## Out of scope (Phase F backlog)

- `agent_core.tools.executor` and `agent_core.tools.builtin`
- `agent_core.commands.registry` and `agent_core.commands.builtin`
- `agent_core.prompts.builder`
- `Agent.commands` and `Agent.tools` class attributes
- Default `Agent.handle_chat` and `Agent.handle_command` implementations using registries
- PAL's `tools.py`, `commands.py`, `prompt_builder.py` migration

## Out of scope (further-future)

- Discord adapter (Phase G)
- Agent_Template repo (Phase H)
- Burn-in + v1.0.0 (Phase I)
- Per-channel preemption (deferred safety fix; resumes after Phase I per project memory)
- CLI splash polish (cosmetic; lands as a follow-up commit on PAL after Phase E ships)

## Phase E target version: `agent_core@v0.5.0`

PAL bumps the agent_core dep after merge.
