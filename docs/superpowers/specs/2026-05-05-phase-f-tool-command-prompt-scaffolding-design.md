# Phase F: Tool / Command / Prompt Scaffolding Extraction

Status: design approved, ready for implementation planning
Date: 2026-05-05
Related: `docs/superpowers/specs/2026-04-25-agent-core-extraction-design.md` (overall extraction plan), `docs/superpowers/specs/2026-04-30-phase-e-runtime-infrastructure-design.md` (immediate predecessor)

## Context

Phases A through E moved utilities, stateless clients, stateful managers, conversation/scratchpad/channels, and the daemon/protocol/CLI runtime into `agent_core` (current release: v0.5.1). The original extraction spec named tool/command/prompt scaffolding as Phase D, but it was deferred during sequencing — the daemon/protocol/CLI work landed first as v0.5.0 because it had fewer moving parts. v0.5.0's CHANGELOG records the renaming: "Phase F (next) extracts tool/command/prompt scaffolding."

Today PAL has:
- `pal/tools.py` — 1544 LOC. A `ToolExecutor` whose constructor takes 12 collaborators, a `TOOL_DEFINITIONS` list of ~25 OpenAI function-calling schemas, and ~25 `_method_name` handler bodies. Sync/async dispatch is split across two methods (`run`, `run_async`). Side effects (auto-reindex on writes) hardcoded by name.
- `pal/commands.py` — 47 LOC. A static list of `(name, args, description)` named tuples. No execution logic; just metadata for `/help`, the splash screen, and the system prompt's commands section.
- A ~200 LOC `if/elif` dispatch tree in `PALAgent.handle_command` (lifted from the daemon in Phase E) routing slash commands to `_handle_X` methods.
- `pal/prompt_builder.py` — 135 LOC. Composes a fixed-order system prompt: BASE_PROMPT (PAL identity + hand-curated tool catalog + policy + style) + profile + wisdom + scratchpad + commands list.

Phase F extracts the dispatch and assembly layers into `agent_core`, lifts a small set of read-only shell-style tools and the framework-manager-backed tools as builtins, and refactors PAL incrementally onto the new API. PAL stays shippable throughout.

## Goals

1. Land tool, command, and prompt scaffolding in `agent_core` v0.6.0 with contract tests against a stub agent. No PAL adoption in the framework PR.
2. Lift seven read-only shell-style tools (`grep`, `head`, `tail`, `cat`, `ls`, `find`, `read_lines`) into `agent_core.tools.builtin`, scoped to the agent's vault, pure-Python, available to any agent by default.
3. Lift five framework-manager-backed tools (`fetch_url`, `search_vault`, `search_web`, `update_scratch`, `add_learning`) into `agent_core.tools.builtin` since they only touch state that already lives in agent_core.
4. Migrate PAL's tools, commands, and prompt to the new API in seven small PRs. Each PR is independently revertable. Every PR keeps PAL fully functional via a dual-dispatch period that ends with the cleanup PR.
5. Make the registration API obvious enough that a future agent (RE Lab) can list `tools = [...]` and `commands = [...]` on its Agent subclass and have a working tool surface without writing dispatch code.

## Non-Goals

- Tool search, categorical filtering, on-demand tool retrieval. PAL ships all registered tool schemas every turn today; that does not change in Phase F. Parked for a post-extraction phase.
- Type-safe `requires` validation (Protocols/ABCs for framework managers). Validation in Phase F is `hasattr`-based; type checking is deferred until concrete pain motivates it.
- Per-tool runtime configuration via constructor arguments. Tool classes are no-arg-constructible; per-instance config is a future extension if needed.
- Hardening `search_web` against prompt injection from SearxNG snippets. Behavior is unchanged from today; flagged for the broader post-extraction PAL review.
- RE Lab or any second agent. Phase F is foundation-laying; second-agent work follows extraction completion.
- Vault content reorganization or storage migrations.

## Architecture

`agent_core` v0.6.0 adds three new sub-packages, no breaking changes to v0.5.x.

```
agent_core/
    tools/
        __init__.py        re-exports: Tool, ToolExecutor
        base.py            Tool base class
        executor.py        ToolExecutor: registry + dispatch + exception containment
        builtin.py         BUILTIN_TOOLS list (12 tools: 7 shell + 5 framework-backed)

    commands/
        __init__.py        re-exports: Command, CommandRegistry
        base.py            Command base class
        registry.py        CommandRegistry: registry + dispatch
        builtin.py         BUILTIN_COMMANDS list (/help, /clear, /status, /profile, /scratch,
                           /wisdom, /learn, /learnings, /promote, /rate, /model, /think, /quit)

    prompts/
        __init__.py        re-exports: SystemPromptBuilder
        builder.py         section render helpers + builder class
```

`Agent` (in `agent_core/agent.py`) gains three ClassVars:

```python
class Agent:
    name: ClassVar[str]
    env_prefix: ClassVar[str | None] = None

    # NEW in v0.6.0
    tools: ClassVar[list[type[Tool]]] = []
    commands: ClassVar[list[type[Command]]] = []
    disabled_builtins: ClassVar[frozenset[str]] = frozenset()
```

`run_daemon()` gains a registration phase between framework-manager wiring and `agent.setup()`:

1. Wire framework managers onto agent (existing).
2. **NEW:** Build `agent.tool_executor = ToolExecutor.build(agent, agent.tools, disabled=agent.disabled_builtins)`.
3. **NEW:** Build `agent.command_registry = CommandRegistry.build(agent, agent.commands, disabled=agent.disabled_builtins)`.
4. **NEW:** Build `agent.prompt_builder = SystemPromptBuilder(profile=agent.profile, wisdom=agent.wisdom, channels=agent.channels, tool_executor=agent.tool_executor, command_registry=agent.command_registry)`.
5. Call `agent.setup()` (existing). Setup sees the executor/registry/builder already in place.
6. Start daemon (existing).

`HandlerContext` (in `agent_core/agent.py`) gains two fields:

```python
@dataclass
class HandlerContext:
    conversation: Conversation
    channel_id: str
    writer: object              # asyncio.StreamWriter; framework-internal

    # NEW in v0.6.0
    agent: "Agent"              # back-reference for tools accessing ctx.agent.X
    emit: Callable[[object], Awaitable[None]]   # send a message mid-call
```

Existing code that constructs `HandlerContext` (in `Daemon._handle_connection` and the test fixtures) updates to populate the new fields. The factory wires `emit` to a closure that NDJSON-encodes the message and writes it to `writer` with the same `_chat_tasks` lifecycle protection the daemon already uses.

## Tool API

### Base class

```python
# agent_core/tools/base.py
from typing import ClassVar
from agent_core.agent import HandlerContext


class Tool:
    name: ClassVar[str]
    description: ClassVar[str]
    parameters: ClassVar[dict]                  # JSON Schema, OpenAI function-calling format
    requires: ClassVar[tuple[str, ...]] = ()    # attribute names that must exist on agent

    async def run(self, args: dict, ctx: HandlerContext) -> str:
        raise NotImplementedError

    @classmethod
    def to_openai_schema(cls) -> dict:
        return {
            "type": "function",
            "function": {
                "name": cls.name,
                "description": cls.description,
                "parameters": cls.parameters,
            },
        }
```

Tools are stateless; the only per-call state is what comes off `ctx`. `to_openai_schema` is a classmethod so consumers can render schemas without instantiating.

### Dependency model

Tools declare `requires` as a tuple of attribute names. At registration time, `ToolExecutor.build()` checks `hasattr(agent, attr)` for each name and fails fast if any is missing. At runtime, the tool accesses dependencies through `ctx.agent.X`. The framework does not inject anything onto the tool instance.

This is a hybrid: declarative for validation (catches misconfiguration at boot, before any user message lands), runtime-explicit for access (no service registry, no injection magic, ~30 lines of framework code total).

The validation is deliberately shallow — `hasattr` only, no type or interface check. A tool whose `requires` lists `"compiler"` passes validation if `agent.compiler = None`; the type error surfaces at first call. Adding type validation requires Protocols/ABCs for every framework manager; that is a Phase H+ lift.

### Executor

```python
# agent_core/tools/executor.py
class ToolExecutor:
    def __init__(self, tools: dict[str, Tool]) -> None:
        self._tools = tools

    @classmethod
    def build(
        cls,
        agent,
        agent_tool_classes: list[type[Tool]],
        disabled: frozenset[str] = frozenset(),
    ) -> "ToolExecutor":
        from agent_core.tools.builtin import BUILTIN_TOOLS
        all_classes = [
            t for t in BUILTIN_TOOLS + agent_tool_classes if t.name not in disabled
        ]
        instances: dict[str, Tool] = {}
        for tool_cls in all_classes:
            for attr in tool_cls.requires:
                if not hasattr(agent, attr):
                    raise RuntimeError(
                        f"Tool {tool_cls.name!r} requires agent.{attr!r}, "
                        f"but {type(agent).__name__} has no such attribute. "
                        f"Set it in setup() or add it to disabled_builtins."
                    )
            instances[tool_cls.name] = tool_cls()
        return cls(instances)

    async def run(self, name: str, arguments: dict, ctx: HandlerContext) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"Unknown tool: {name}"
        try:
            return await tool.run(arguments, ctx)
        except Exception as exc:
            return f"Error in {name}: {exc}"

    def schemas(self) -> list[dict]:
        """For passing as the tools= parameter to inference."""
        return [type(t).to_openai_schema() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)
```

Generic and small. The exception containment catches `Exception`, not `BaseException`, so `KeyboardInterrupt` and `asyncio.CancelledError` propagate. Insertion order is preserved by `dict`, so `schemas()` ordering is deterministic and matches the order builtins-then-agent-tools-minus-disabled.

### Tool.run signature and side effects

`run` is uniformly `async def run(self, args: dict, ctx: HandlerContext) -> str`. There is no sync variant.

Tools that need to send messages mid-call (proposal emissions, progress updates) call `await ctx.emit(msg)`. The framework wires `ctx.emit` to NDJSON-encode and write to the connection's writer. Examples:

```python
class ProposeResearch(Tool):
    name = "propose_research"
    requires = ("approval_registry",)

    async def run(self, args, ctx):
        proposal_id = ctx.agent.approval_registry.create(...)
        await ctx.emit(ResearchProposalMessage(proposal_id, args["topic"], ...))
        status = await ctx.agent.approval_registry.wait(proposal_id)
        return f"Status: {status}"
```

Tools own their own side effects. The auto-reindex behavior currently hardcoded in `ToolExecutor.run_async` (`if name in {"edit_file", "create_file"}: await retrieval.trigger_reindex(...)`) moves into `EditFile.run` and `CreateFile.run`:

```python
class EditFile(Tool):
    name = "edit_file"
    requires = ("config", "retrieval")

    async def run(self, args, ctx):
        path = args["path"]
        # ... write logic ...
        result = "ok"
        if "error" not in result.lower()[:30]:
            absolute = str((ctx.agent.config.vault_path / path).resolve())
            await ctx.agent.retrieval.trigger_reindex(paths=[absolute])
        return result
```

The executor stays generic. No name-based branching, no special cases.

## Command API

### Base class

```python
# agent_core/commands/base.py
from collections.abc import AsyncIterator
from typing import ClassVar
from agent_core.agent import HandlerContext


class Command:
    name: ClassVar[str]
    args: ClassVar[str]                         # arg-string template, e.g. "<title>"
    description: ClassVar[str]
    requires: ClassVar[tuple[str, ...]] = ()

    async def run(self, raw_args: str, ctx: HandlerContext) -> AsyncIterator:
        raise NotImplementedError
        yield   # makes this an async generator
```

Two key differences from `Tool`:
- Argument is a free-form string (`raw_args`), not a parsed dict. Each command parses its own arguments per its own conventions.
- Return type is an `AsyncIterator` of messages. Commands often stream (`/research` emits progress messages; `/help` emits a single response). The dispatch path yields the command's messages straight to the connection.

### Registry

```python
# agent_core/commands/registry.py
class CommandRegistry:
    def __init__(self, commands: dict[str, Command]) -> None:
        self._commands = commands

    @classmethod
    def build(
        cls,
        agent,
        agent_command_classes: list[type[Command]],
        disabled: frozenset[str] = frozenset(),
    ) -> "CommandRegistry":
        # mirror of ToolExecutor.build: requires-validation, opt-out by name set
        ...

    async def dispatch(
        self, name: str, raw_args: str, ctx: HandlerContext,
    ):
        command = self._commands.get(name)
        if command is None:
            from agent_core.protocol.messages import ResponseMessage
            yield ResponseMessage(f"Unknown command: {name}")
            return
        async for msg in command.run(raw_args, ctx):
            yield msg

    def metadata(self) -> list[tuple[str, str, str]]:
        """(name, args, description) for /help and the prompt builder."""
        return [(type(c).name, type(c).args, type(c).description)
                for c in self._commands.values()]
```

Registration order is preserved. Unknown commands yield a `ResponseMessage` (not `ErrorMessage`, not silent) so the user sees a clear "Unknown command" rather than a stack trace.

### Builtin commands

`agent_core.commands.builtin.BUILTIN_COMMANDS` ships with thin wrappers over framework managers:

| Command | Purpose | `requires` |
|---|---|---|
| `/help` | List registered commands with descriptions | `command_registry` |
| `/clear` | Reset the current channel's conversation | `channels` |
| `/status` | Show daemon model, vault path, agent name | `config` |
| `/profile` | Read/query the user profile | `profile` |
| `/scratch` | Append/read/clear the channel scratchpad | `channels` |
| `/wisdom` | List/add/remove wisdom entries | `wisdom` |
| `/learnings` | List captured learning candidates | `learning` |
| `/promote` | Promote a learning to wisdom | `learning`, `wisdom` |
| `/rate` | Rate a learning (1-5) | `learning` |
| `/model` | Show or switch active model | `inference` |
| `/think` | Set reasoning mode (on/off/auto/show/hide) | `channels` |
| `/quit` | End the session | (none) |

`/learn` (trigger learning extraction) is intentionally *not* in the builtin set for v0.6.0. It *could* be — only `inference` and `learning` are needed, both already wired. The blocker is design clarity: PAL's current `/learn` uses a custom one-shot extraction prompt and bypasses `agent_core.learning_scanner`'s vetted extraction logic entirely (the scanner runs proactively after each turn; `/learn` is a manual fallback with a different prompt). The right shape for a generic builtin — call into the scanner vs. roll its own prompt vs. let agents override — is exactly the kind of policy question the post-extraction PAL review is meant to answer. Committing to a shape now locks every future agent into whichever choice we pick. PAL keeps `/learn` as a domain command for v0.6.0; lift to builtin once the post-extraction review settles the shape. See Risk 10.

`command_registry` and `config` are not wired by `run_daemon` in v0.5.x but become so in v0.6.0 as part of Phase F's `run_daemon` registration phase (step 2-4 in Architecture).

PAL keeps its domain-specific commands (`/research`, `/compile`, `/compile-batch`, `/import`, `/summarize`, `/read`, `/search`, `/get`, `/note`, `/lint`, `/search-web`, `/fetch`, `/learn`) as `Command` subclasses in `pal/commands/`.

## Builtin tools

### Common properties

All shell tools share:
- `requires = ("config",)` — pull `vault_path` off `ctx.agent.config.vault_path`
- Path resolution: `(vault / arg).resolve()` then reject if `not relative_to(vault)`
- System paths (any path component starting with `_`) rejected
- Output capped at 32 KB per call (matches PAL's `_READ_LIMIT`); truncation footer reports bytes dropped
- Result count caps where applicable (`grep`: 100 hits; `find`: 500 paths; `ls`: 500 entries)
- Pure-Python; no shell-out, no `subprocess`
- All errors return as descriptive strings, never raised

### The seven shell tools

**`grep`** — keyword/regex search across vault files
```
parameters:
  pattern: str (required)        plain string by default; regex if regex=true
  path: str = ""                 subdir or file to search; empty = vault root
  regex: bool = false            treat pattern as Python regex
  ignore_case: bool = false
  max_hits: int = 100            cap; client can request lower, not higher
```
Output: `path:lineno: matched line` per hit. Walks vault recursively, respects `_`-prefix rule, line-by-line reads to avoid OOM on large files.

**`head`** — first N lines of a vault file
```
parameters:
  path: str (required)
  lines: int = 20
```

**`tail`** — last N lines of a vault file
```
parameters:
  path: str (required)
  lines: int = 20
```

**`cat`** — full file contents
```
parameters:
  path: str (required)
```
Truncated at 32 KB with a footer noting dropped bytes. The system prompt should hint at `head`/`tail`/`read_lines` for large files.

**`ls`** — directory listing
```
parameters:
  path: str = ""                 empty = vault root
  show_hidden: bool = false      false hides _-prefixed entries
  long: bool = false             include size + mtime
```
One entry per line; directories suffixed with `/`. Capped at 500 with footer.

**`find`** — filename/glob search
```
parameters:
  pattern: str (required)        glob pattern, e.g. "agent-*.md", "**/quantum*"
  path: str = ""                 subdir; empty = vault root
  type: str = ""                 "f", "d", or "" for both
  max_results: int = 500
```
Uses `pathlib.Path.rglob`. Respects `_`-prefix rule. One vault-relative path per line.

**`read_lines`** — read a specific line range
```
parameters:
  path: str (required)
  start: int (required)          1-indexed, inclusive
  end: int (required)            1-indexed, inclusive
```
Returns lines `start..end` with line-number prefixes. Pairs with `grep` hits.

### The five framework-backed builtins

Lifted from PAL where the implementation already only touched framework state.

**`fetch_url`** — `requires = ("allowlist",)`. Wraps `agent_core.utils.fetcher`. Allowlist-gated fetch + sanitize, returns extracted text. Original spec called this out; PAL doesn't currently have it as a tool.

**`search_vault`** — `requires = ("retrieval",)`. Semantic search via RetrievalClient. Lifted from PAL's `_search_vault`.

**`search_web`** — `requires = ("websearch",)`. SearxNG. Lifted from PAL's `_search_web`. Security caveats unchanged from current behavior; flagged for post-extraction review.

**`update_scratch`** — `requires = ("channels",)`. Per-channel scratchpad write. Lifted from PAL's `_update_scratch`.

**`add_learning`** — `requires = ("learning",)`. Capture a learning candidate. Lifted from PAL's `_add_learning`.

### What stays PAL-specific

PAL's domain tools — read_file, list_directory, search_content, edit_file, create_file, move_file, propose_research, research_topic, compile_summary, propose_compile_batch, compile_batch, propose_consolidate, consolidate, propose_reorg, propose_promote, reorg, wait_for_reindex — touch `pal/wiki.py`, `pal/compiler.py`, `pal/researcher.py`, etc. They lift into `pal/tools/` as `Tool` subclasses but stay in PAL.

PAL's vault tools (`read_file`, `list_directory`, `search_content`) overlap with the shell builtins (`cat`, `ls`, `grep`) but are kept distinct. PAL's prompt is tuned around their specific output formats (frontmatter parsing, pagination footers); replacing them risks behavior regressions for no immediate gain. Both register; different names, no collision. Future deduplication is a prompt-rewrite question, not a Phase F question.

## Prompt API

Slot-based. The framework provides `render_*` helpers; the agent's `system_prompt(ctx)` calls whichever it wants in whichever order.

```python
# agent_core/prompts/builder.py
class SystemPromptBuilder:
    def __init__(
        self,
        profile,                # ProfileManager
        wisdom,                 # WisdomManager
        channels,               # ChannelStore
        tool_executor: ToolExecutor,
        command_registry: CommandRegistry,
    ):
        ...

    def render_profile(self) -> str:
        body = self.profile.read()
        return f"## About the User\n\n{body}" if body else ""

    def render_wisdom(self) -> str:
        bodies = self.wisdom.bodies()
        if not bodies:
            return ""
        text = "\n".join(f"- {b}" for b in bodies)
        return f"## Active Wisdom\n\n{text}"

    def render_scratchpad(self, channel_id: str) -> str:
        body = self.channels.scratchpad(channel_id).read()
        return f"## Channel Scratchpad\n\n{body}" if body else ""

    def render_commands_catalog(self) -> str:
        lines = [f"- `/{n} {a}`".rstrip() + f" - {d}"
                 for n, a, d in self.command_registry.metadata()]
        if not lines:
            return ""
        return "## Available Commands\n\n" + "\n".join(lines)

    def render_tools_catalog(self) -> str:
        lines = []
        for schema in self.tool_executor.schemas():
            f = schema["function"]
            lines.append(f"- `{f['name']}` - {f['description']}")
        if not lines:
            return ""
        return "## Available Tools\n\n" + "\n".join(lines)
```

PAL's `system_prompt` becomes:

```python
def system_prompt(self, ctx):
    pb = self.prompt_builder
    return "\n\n".join(filter(None, [
        PAL_BASE_PROMPT,                       # PAL identity + policy + style + hand-curated tool catalog
        pb.render_profile(),
        pb.render_wisdom(),
        pb.render_scratchpad(ctx.channel_id),
        pb.render_commands_catalog(),
    ]))
```

PAL keeps its hand-curated tool catalog inlined inside `PAL_BASE_PROMPT` and skips `render_tools_catalog()`. Reasoning: PAL's prompt is tuned around grouped-by-purpose tool descriptions, and replacing that with registry-order rendering risks tool-use regressions. The auto-rendered catalog is available for future agents and for PAL to A/B against later.

The fixed-order assembly logic from PAL's existing `prompt_builder.py` is gone. Each section is independently testable. Agents that want different orders or omitted sections control that in `system_prompt(ctx)` directly.

## PAL refactor surface

### Files deleted at the end

- `pal/tools.py` (1544 LOC) — methods become Tool subclasses under `pal/tools/`
- `pal/commands.py` (47 LOC) — metadata derives from registered Command classes
- `pal/prompt_builder.py` (135 LOC) — replaced by framework builder + agent-side assembly

### `pal/agent.py` changes

- `setup()` drops the `ToolExecutor(...)` construction (the 12-collaborator one). Framework builds the executor automatically.
- `handle_chat` switches `tools=TOOL_DEFINITIONS` to `tools=self.tool_executor.schemas()` and `await self.tool_executor.run_async(name, args)` to `await self.tool_executor.run(name, args, ctx)`. Per-turn `proposal_emitter` wiring goes away — tools call `await ctx.emit(msg)` directly.
- `handle_command` collapses from a ~200-line if/elif tree to: `async for msg in self.command_registry.dispatch(name, raw_args, ctx): yield msg`. Each `_handle_X` becomes a `Command.run` body or gets deleted.
- `system_prompt` drops the `from pal.prompt_builder import SystemPromptBuilder` import and assembles via `self.prompt_builder.render_*` calls.

Net: `pal/agent.py` likely loses 200-400 LOC; the bulk of that is the `handle_command` if/elif tree and the `_handle_X` method bodies. Final size depends on how much of `handle_chat`'s tool-call interleaving stays in agent.py vs gets factored further.

### New PAL packages

```
pal/tools/
    __init__.py            re-exports all PAL tool classes
    vault.py               ReadFile, ListDirectory, SearchContent, EditFile, CreateFile, MoveFile
    research.py            ProposeResearch, ResearchTopic
    compile.py             CompileSummary, ProposeCompileBatch, CompileBatch
    consolidate.py         ProposeConsolidate, Consolidate
    reorg.py               ProposeReorg, ProposePromote, Reorg
    wait.py                WaitForReindex

pal/commands/
    __init__.py            re-exports all PAL command classes
    research.py            Research
    compile.py             Compile, CompileBatch
    domain.py              Import, Summarize, Read, Search, Get, Note, Lint, SearchWeb, Fetch, Learn
```

### What does not change

- `pal/compiler.py`, `pal/researcher.py`, `pal/consolidator.py`, `pal/reorg.py`, `pal/wiki.py`, `pal/article.py`, `pal/categorizer.py`, `pal/summarizer.py`, `pal/title_cleanup.py`, `pal/backfill_*.py` — domain logic untouched. Tools reference them via `ctx.agent.X`.
- `pal/discord_*` — untouched, except for one update in PR5 to read command metadata from `agent.command_registry.metadata()` instead of `pal/commands.py`'s static list.
- `pal/protocol.py` — PAL-specific message types stay.
- Vault content, storage layout, inference server — no changes.

## Migration sequence

Framework changes ship as one PR (internally coherent, tightly coupled modules). PAL adoption is incremental: one tool category per PR, command migration as a single PR, prompt builder migration as a single PR, final cleanup as the last PR.

### Framework PR — agent_core v0.6.0

Branch: `feature/phase-f-tool-command-prompt-scaffolding`

Lands all of `agent_core/tools/`, `agent_core/commands/`, `agent_core/prompts/` plus the `Agent` ClassVar additions, `HandlerContext` field additions, and `run_daemon` registration phase. Contract tests against a stub agent. Tag v0.6.0.

Done when: agent_core's pytest is green; CHANGELOG entry written; tag pushed.

### PAL PR1 — bump dep, register builtins alongside legacy executor

Branch: `feature/phase-f-pr1-builtin-tools`

- `pyproject.toml`: bump `agent_core` to v0.6.0
- `pal/agent.py`: empty `tools = []` and `commands = []` (gets all builtins)
- `handle_chat`: framework executor checked first; falls back to legacy `pal.tools.ToolExecutor` for everything else
- `system_prompt`: now includes builtin commands automatically via `render_commands_catalog()`

Done when: existing PAL tests pass; smoke test confirms builtin shell tools reachable via chat; PAL's existing tools still work; `/help` includes builtin slash commands. Risk: medium-low (additive only).

### PAL PR2 — migrate vault tools

Branch: `feature/phase-f-pr2-vault-tools`

- `pal/tools/vault.py`: ReadFile, ListDirectory, SearchContent, EditFile, CreateFile, MoveFile
- `pal/agent.py`: `tools = [ReadFile, ListDirectory, SearchContent, EditFile, CreateFile, MoveFile]`
- Strip corresponding `_method_name` blocks from `pal/tools.py`
- Drop PAL's `_search_vault` method (the framework `search_vault` builtin registered in PR1 replaces it; PAL had no domain-specific behavior beyond wrapping `RetrievalClient.query()`)
- Update tests

Done when: vault tools route through new executor; legacy methods deleted; tests green. Risk: low (mechanical lift).

### PAL PR3 — migrate research and web tools

Branch: `feature/phase-f-pr3-research-web`

- `pal/tools/research.py`: ProposeResearch, ResearchTopic
- Drop PAL's `_search_web` method; the framework builtin (registered in PR1) replaces it. PAL had no domain-specific behavior beyond wrapping `WebSearchClient`.
- Strip from `pal/tools.py`; update tests

Done when: research approval cycle (propose → emit → wait → execute) works on new dispatch path. Risk: medium (most complex tool flow; emit semantics are first exercised here).

### PAL PR4 — migrate compile / wiki / consolidate / reorg / promote

Branch: `feature/phase-f-pr4-wiki-tools`

- `pal/tools/compile.py`, `pal/tools/consolidate.py`, `pal/tools/reorg.py`, `pal/tools/wait.py`
- Strip from `pal/tools.py`; update tests

Done when: all wiki-shaping tools route through new executor; smoke test of compile-batch flow. Risk: medium (multiple multi-step propose-then-execute pairs).

### PAL PR5 — migrate slash commands

Branch: `feature/phase-f-pr5-commands`

- `pal/commands/research.py`, `pal/commands/compile.py`, `pal/commands/domain.py`
- `pal/agent.py`: populate `commands = [...]`; strip `_handle_X` methods; collapse `handle_command` to dispatch loop
- Delete `pal/commands.py`
- Update `pal/discord_adapter.py` to read metadata from `agent.command_registry.metadata()`

Done when: all slash commands route through registry; agent.py drops ~200 LOC; smoke test every command at least once. Risk: medium (largest dispatch lift; subtle behaviors easy to drop).

### PAL PR6 — migrate prompt builder

Branch: `feature/phase-f-pr6-prompt-builder`

- Rewrite `pal/agent.py`'s `system_prompt` to use `self.prompt_builder.render_*`
- Inline what's left of PAL's BASE_PROMPT into a small module (e.g. `pal/prompts/system.py`)
- Delete `pal/prompt_builder.py`

Done when: prompt building goes through framework helpers; pre/post diff of rendered prompt for a representative turn shows no regression. Risk: low.

### PAL PR7 — delete legacy executor and cleanup

Branch: `feature/phase-f-pr7-cleanup`

- Delete `pal/tools.py` (any remaining `_method_name` cruft from framework-backed builtins like `_update_scratch` and `_add_learning` — unreachable since PR1 — goes here)
- Remove dual-dispatch fallback from `handle_chat`; only framework executor remains
- Final test pass

Done when: no PAL code references `pal.tools.ToolExecutor`; tests green; smoke test full feature surface. Risk: low (pure deletion of code that's already not running).

Each PAL PR is independently revertable until PR7. Worst-case rollback is per-category, not all-of-Phase-F.

## Testing strategy

### Framework tests (in `agent_core/tests/`)

**Per shell tool** — one test file per builtin, covering happy path, path escape rejection, system path rejection, output cap behavior, edge cases (binary files in `cat`, regex syntax errors in `grep`, missing files, empty directories, no matches).

**Per framework-backed builtin** — `fetch_url`, `search_vault`, `search_web`, `update_scratch`, `add_learning`. Use mock managers via existing fixtures.

**`tests/test_executor.py`:**
- `test_build_validates_requires` — tool with `requires = ("missing_attr",)` raises with agent class name in the message
- `test_build_excludes_disabled` — `disabled_builtins = {"grep"}` produces an executor where `grep` is unknown
- `test_run_unknown_returns_string` — `executor.run("nope", {}, ctx)` returns `"Unknown tool: nope"`
- `test_run_catches_exceptions` — tool that raises returns `"Error in <name>: <exc>"`
- `test_schemas_returns_openai_format` — schemas() output passes a JSON schema validator
- `test_run_does_not_swallow_cancellation` — `asyncio.CancelledError` propagates

**`tests/test_command_registry.py`** — mirror of executor tests, plus:
- `test_dispatch_yields_messages`
- `test_metadata_preserves_registration_order`
- `test_unknown_command_yields_response_message`

**`tests/test_prompt_builder.py`** — one test per `render_*` helper covering empty-data and populated-data cases; ordering tests for tools/commands catalogs.

**`tests/test_contract.py` (extends existing):**
- `test_minimal_agent_registers_tools`
- `test_disabled_builtins_excluded_from_executor`
- `test_disabled_builtins_excluded_from_commands`
- `test_missing_dep_fails_at_run_daemon` — fails before any chat is processed
- `test_handler_context_carries_agent_and_emit`

### PAL tests during migration

- Framework PR: agent_core pytest green
- PAL PR1: existing PAL tests green; smoke test that builtin shell tools reachable via chat
- PAL PR2-4: existing tool tests rewritten to instantiate Tool classes; pytest green; smoke test migrated category
- PAL PR5: every slash command tested manually after migration
- PAL PR6: pre/post rendered system prompt diff for a representative turn
- PAL PR7: full smoke test of every feature surface

### Manual smoke-test checklist per PR

PR1 minimum: pal-daemon starts; CLI connects; `/help` lists builtin commands; `cat`/`grep`/`ls` work via chat; existing PAL tool still works (legacy path).

PR2-4 minimum: migrated tools work via chat; approval flows still gate; reindex auto-triggers after writes (now via tool's own logic); Discord bridge still functional.

PR5: every slash command invokable; output matches pre-migration; `/research` still emits approval prompt; Discord prefix-rewrite still maps `!cmd` to slash.

PR6: profile/wisdom/scratchpad/commands rendered correctly; PAL's BASE_PROMPT identity content survived the move; quality unchanged on canonical questions.

PR7: full smoke test from PR1 plus PRs 2-6 specifics; no imports of `pal.tools.ToolExecutor` anywhere.

### What is not tested at this layer

- LLM tool-use behavior — model picking the right tool, formatting parameters correctly. Prompt-quality territory; migration is structural, not behavioral.
- Performance — registry adds a dict lookup per tool call (negligible).
- Inter-agent compatibility — RE Lab does not exist yet.

### CI

agent_core CI runs new tests. PAL CI runs PAL's tests against bumped dep. No new infrastructure.

### Rollback strategy

- Framework PR: revert release; PAL stays on v0.5.1 until something bumps it.
- PAL PRs 1-6: per-PR revert. Each leaves both dispatch paths working until PR7.
- PAL PR7: revert restores the legacy executor and dual-dispatch wiring; deleted `pal/tools.py` returns from git.

## Risks

**1. Shallow `requires` validation.** `hasattr` only, no type check. A Tool with `requires = ("compiler",)` passes if `agent.compiler = None`. Real type errors only surface at first call. Documented limitation; revisit if it bites.

**2. Auto-rendered tools catalog might surface tool-use regressions in PAL.** PAL keeps its hand-curated catalog through Phase F. The auto-rendered version is available for new agents and PAL to A/B against later. No forced change.

**3. Dual-dispatch period (PRs 1-6) has subtle bugs available.** A tool name in *both* dispatchers routes to the framework one (legacy stays unreachable but undeleted; cruft, not correctness). A name in *neither* is a real failure but caught by tests. Each PAL PR's checklist includes "removed from `pal/tools.py` AND added to `pal/tools/<file>.py`" as paired check.

**4. `ctx.emit` is a behavior shift from `proposal_emitter`.** Today's executor wires a `proposal_emitter` callback per-turn in `handle_chat`. Tools after Phase F call `await ctx.emit(msg)` instead. Subtle differences in ordering and awaitable nature. PR3 (research/web) is where this lands first; smoke-test the full research approval cycle on PR3 before moving to PR4.

**5. Phase F is the largest design lift in the extraction.** Per the original spec. Even with this design locked, implementation will surface unforeseen API friction. Expect 1-2 small framework patch releases (v0.6.1, v0.6.2) during PAL's migration.

**6. Implicit ordering in PAL's tool flows.** `propose_consolidate` → `consolidate` shares state via approval registry (a framework manager). New Tool subclasses are stateless except for `ctx`, so state lives in `agent.approval_registry` — fine in principle. PR4 explicitly tests propose-then-execute pairs end-to-end before merging.

**7. `handle_chat`'s tool-call interleaving with stream chunks.** ~200 LOC lifted in Phase E. Loop structure stays, but the call site changes from `run_async` to `run`. PR2 includes a code-review checklist item to diff `handle_chat` and confirm interleaving logic preserved.

**8. Circular import between `agent_core/agent.py` and `agent_core/tools/base.py`.** `Agent` declares `tools: ClassVar[list[type[Tool]]]`, requiring the `Tool` symbol; `Tool.run` takes a `HandlerContext`, which lives on `agent.py`. Implementation must resolve via `TYPE_CHECKING`-guarded imports plus string forward references (`tools: ClassVar[list[type["Tool"]]]`). Same applies to `Command` and `commands`. Trivial to handle but worth flagging so it doesn't surprise the implementer.

**9. `disabled_builtins` is a single namespace shared by tools and commands.** `frozenset[str]` of names; if a future builtin tool and builtin command end up with the same name (none collide today: tools are `grep`/`cat`/`ls`/etc., commands are `help`/`clear`/etc.), one entry would suppress both. Splitting into `disabled_tools` and `disabled_commands` is a straightforward future change if collision becomes real. Documented limitation, not blocking.

**10. Some builtin commands depend on framework managers wired by Phase F itself.** v0.5.0's wiring covers: profile, wisdom, learning, allowlist, approval_registry, channels, inference, retrieval, websearch. The spec's builtin command list also names `command_registry` and `config`; both get wired by Phase F itself (the `run_daemon` registration phase in Architecture step 2-4). No external dependency to add.

`/learn` is a separate consideration: it could ship as a builtin (its needs — `inference` and `learning` — are already wired), but PAL's current `/learn` implementation uses a custom extraction prompt that bypasses `agent_core.learning_scanner`'s vetted extraction logic. Committing to a generic builtin shape now means picking between "call into the scanner's extraction" vs "roll its own prompt" vs "let agents override," and that's exactly the policy question deferred to the post-extraction PAL review. PAL keeps `/learn` as a domain command for v0.6.0; lift to builtin once the post-extraction review settles the shape.

## Parked Open Questions

Recorded for revisit, not blockers for shipping:

1. **Tool search / categorical filtering / pruning.** Today PAL ships ~25 tool schemas every turn; Phase F adds 7-12 more. Prompt bloat is real. Solutions (categorical filtering, `find_tools` meta-tool, vector retrieval) deferred. The skill-retrieval-service plan in the tree may be the natural home.
2. **Type-safe `requires` declarations.** Possible solution: framework managers declare Protocols, tools' `requires` becomes a typed mapping. Defer until concrete pain.
3. **Per-tool config.** Tools are no-arg-constructible. If a tool ever needs per-instance config, the registration model has to support it. Trivial to add later.
4. **Agent-side tool ordering / priority.** Registration-order rendering is deterministic but coarse. If ordering influences model behavior, may want explicit `priority`. Defer.
5. **Builtin opt-in vs opt-out for non-PAL agents.** `disabled_builtins` is opt-out. A future agent might prefer opt-in. Trivial to add `enabled_builtins` later.
6. **`search_web` security model.** SearxNG snippets enter LLM context with no allowlist filtering. Unchanged from today; lifting to builtin makes it the precedent for future agents. On the post-extraction PAL review list.

## Hard Non-Goals

- Tool search / on-demand tool retrieval in Phase F.
- Type validation of `requires`.
- Per-tool runtime configuration.
- Hardening `search_web` against prompt injection.
- Building RE Lab or any second agent.

## Decisions Summary

| Decision | Choice |
|---|---|
| Tool shape | Class with `name` / `description` / `parameters` / `requires` / `async run(args, ctx)` |
| Dependency model | `requires` validated at registration via `hasattr`; runtime access via `ctx.agent.X` |
| Builtin tools | 7 shell + 5 framework-backed = 12 total, opt-out via `disabled_builtins` |
| Tool registration | Class-list attribute on Agent (`tools = [...]`); framework instantiates |
| Tool.run signature | `async def run(self, args: dict, ctx: HandlerContext) -> str`; emits via `ctx.emit` |
| Tool side effects | Owned by tool; executor stays generic (no name-based branching) |
| Executor | Generic ~50 LOC: registry + dispatch + exception containment |
| Commands | Parallel `Command` class; same registration model; `async def run(raw_args, ctx) -> AsyncIterator` |
| Builtin commands | 13 thin wrappers over framework managers; PAL keeps domain commands |
| Prompt builder | Slot helpers (`render_*`); agent assembles in `system_prompt(ctx)` |
| PAL prompt | Keeps hand-curated tool catalog; framework auto-renders commands only |
| Migration sequence | Framework big-bang, PAL incremental: 1 framework PR + 7 PAL PRs |
| Tool search | Out of scope; parked for post-extraction phase |
