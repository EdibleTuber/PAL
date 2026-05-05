# Phase F: Tool / Command / Prompt Scaffolding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lift PAL's tool/command/prompt scaffolding into `agent_core@v0.6.0` plus seven read-only shell builtins and five framework-manager-backed builtins. Then migrate PAL incrementally onto the new API across seven independently-revertable PRs.

**Architecture:** Agent_core gains three sub-packages (`tools/`, `commands/`, `prompts/`) implementing class-based registries with `requires`-validation at boot and `ctx.agent.X` access at runtime. Twelve builtin tools ship enabled by default; each agent opts out with `disabled_builtins`. PAL adopts incrementally — one tool category per PR — with a dual-dispatch period that ends with a cleanup PR. PAL stays shippable throughout.

**Tech Stack:** Python 3.12+, hatchling, pytest, pytest-asyncio. No new runtime/dev deps.

**Repos involved:**
- agent_core: `/home/edible/Projects/agent_core` (currently `v0.5.1` on main)
- PAL: `/home/edible/Projects/PAL` (main checkout). PAL-side migration uses per-PR worktrees at `/home/edible/Projects/PAL/.worktrees/phase-f-prN`.

**Reference:** spec at `docs/superpowers/specs/2026-05-05-phase-f-tool-command-prompt-scaffolding-design.md`. Builds on Phase E (`docs/superpowers/plans/2026-04-30-phase-e-runtime-infrastructure.md`).

**Phase E status (recently shipped):** agent_core v0.5.1 has `Agent` base class, `BaseConfig`, `run_daemon`, the transport-only `Daemon`, the CLI adapter, and `handle_other` for non-Chat/non-Command messages. PAL has been migrated to consume them via `PALAgent`. All of this carries forward into Phase F.

---

## Pre-flight: Code map

Mapped during planning. Use this as the migration target list.

### agent_core changes (Part 1)

| Path | Change |
|---|---|
| `agent_core/agent_core/tools/__init__.py` | NEW. Re-exports `Tool`, `ToolExecutor`. |
| `agent_core/agent_core/tools/base.py` | NEW. `Tool` base class. |
| `agent_core/agent_core/tools/executor.py` | NEW. `ToolExecutor` with `build()`. |
| `agent_core/agent_core/tools/builtin.py` | NEW. `BUILTIN_TOOLS` list (12 tools). |
| `agent_core/agent_core/tools/_shell.py` | NEW. The 7 shell tools (Grep, Head, Tail, Cat, Ls, Find, ReadLines). |
| `agent_core/agent_core/tools/_framework.py` | NEW. The 5 framework-backed tools (FetchUrl, SearchVault, SearchWeb, UpdateScratch, AddLearning). |
| `agent_core/agent_core/commands/__init__.py` | NEW. Re-exports `Command`, `CommandRegistry`. |
| `agent_core/agent_core/commands/base.py` | NEW. `Command` base class. |
| `agent_core/agent_core/commands/registry.py` | NEW. `CommandRegistry` with `build()`. |
| `agent_core/agent_core/commands/builtin.py` | NEW. `BUILTIN_COMMANDS` list. |
| `agent_core/agent_core/commands/_builtin_impls.py` | NEW. The 12 builtin command implementations. |
| `agent_core/agent_core/prompts/__init__.py` | NEW. Re-exports `SystemPromptBuilder`. |
| `agent_core/agent_core/prompts/builder.py` | NEW. `SystemPromptBuilder` + render helpers. |
| `agent_core/agent_core/agent.py` | MODIFY. Add `tools`, `commands`, `disabled_builtins` ClassVars; extend `HandlerContext` with `agent` + `emit` fields. |
| `agent_core/agent_core/runtime.py` | MODIFY. Add registration phase: build executor / registry / prompt_builder, attach to agent before `setup()`. |
| `agent_core/agent_core/daemon.py` | MODIFY. `_handle_connection` populates `HandlerContext.agent` and `.emit` on every dispatch. |
| `agent_core/tests/test_tools_*.py` | NEW. Per-tool unit tests + executor tests. |
| `agent_core/tests/test_commands_*.py` | NEW. Per-command tests + registry tests. |
| `agent_core/tests/test_prompts.py` | NEW. Section render tests. |
| `agent_core/tests/test_contract.py` | EXTEND. Tool/command registration contract tests. |
| `agent_core/pyproject.toml` | Bump version to `0.6.0`. |
| `agent_core/CHANGELOG.md` | Add `0.6.0` entry. |

### PAL changes (Parts 2-8)

| PR | Path | Change |
|---|---|---|
| PR1 | `pyproject.toml` | Bump agent_core dep to `v0.6.0`. |
| PR1 | `pal/agent.py` | Add `tools = []`, `commands = []` ClassVars; wire framework executor alongside legacy. |
| PR2 | `pal/tools/__init__.py` | NEW. Re-exports vault Tool classes. |
| PR2 | `pal/tools/vault.py` | NEW. ReadFile, ListDirectory, SearchContent, EditFile, CreateFile, MoveFile. |
| PR2 | `pal/tools.py` | Strip migrated `_method_name` blocks + `_search_vault`. |
| PR2 | `pal/agent.py` | Register vault tools. |
| PR3 | `pal/tools/research.py` | NEW. ProposeResearch, ResearchTopic. |
| PR3 | `pal/tools.py` | Strip research + web methods. |
| PR4 | `pal/tools/compile.py` | NEW. CompileSummary, ProposeCompileBatch, CompileBatch. |
| PR4 | `pal/tools/consolidate.py` | NEW. ProposeConsolidate, Consolidate. |
| PR4 | `pal/tools/reorg.py` | NEW. ProposeReorg, ProposePromote, Reorg. |
| PR4 | `pal/tools/wait.py` | NEW. WaitForReindex. |
| PR4 | `pal/tools.py` | Strip wiki-shaping methods. |
| PR5 | `pal/commands/__init__.py` | NEW. Re-exports Command classes. |
| PR5 | `pal/commands/research.py` | NEW. Research. |
| PR5 | `pal/commands/compile.py` | NEW. Compile, CompileBatch. |
| PR5 | `pal/commands/domain.py` | NEW. Import, Summarize, Read, Search, Get, Note, Lint, SearchWeb, Fetch, Learn. |
| PR5 | `pal/commands.py` | DELETE. |
| PR5 | `pal/agent.py` | Strip `_handle_X` methods; collapse `handle_command`. |
| PR5 | `pal/discord_adapter.py` | Read command metadata from `agent.command_registry.metadata()`. |
| PR6 | `pal/prompts/__init__.py` | NEW. Empty package marker. |
| PR6 | `pal/prompts/system.py` | NEW. PAL_BASE_PROMPT (the identity + policy + style prose). |
| PR6 | `pal/agent.py` | Rewrite `system_prompt` to use `prompt_builder.render_*`. |
| PR6 | `pal/prompt_builder.py` | DELETE. |
| PR7 | `pal/tools.py` | DELETE. |
| PR7 | `pal/agent.py` | Remove dual-dispatch fallback in `handle_chat`. |

### Symbol map (what moves where)

| Symbol | Source | Destination |
|---|---|---|
| `Tool`, `ToolExecutor` | (new) | `agent_core.tools` |
| `Command`, `CommandRegistry` | (new) | `agent_core.commands` |
| `SystemPromptBuilder` (generic) | `pal.prompt_builder` | `agent_core.prompts.builder` |
| `BUILTIN_TOOLS` | (new) | `agent_core.tools.builtin` |
| `BUILTIN_COMMANDS` | (new) | `agent_core.commands.builtin` |
| `_search_vault`, `_search_web`, `_update_scratch`, `_add_learning` (PAL methods) | `pal.tools.ToolExecutor` | `agent_core.tools._framework` (lifted as builtin Tool subclasses) |
| `read_file`, `list_directory`, `search_content`, `edit_file`, `create_file`, `move_file` | `pal.tools.ToolExecutor` | `pal.tools.vault` (PAL-specific Tool subclasses) |
| Research / compile / consolidate / reorg / promote / wait_for_reindex tool methods | `pal.tools.ToolExecutor` | `pal.tools.{research,compile,consolidate,reorg,wait}` |
| `_handle_X` slash command methods | `pal.agent.PALAgent` | `pal.commands.{research,compile,domain}` |
| `BASE_PROMPT` constant | `pal.prompt_builder` | `pal.prompts.system` |
| `COMMANDS` static list | `pal.commands` | DELETED. Metadata derived from registered Command classes. |

---

## Worktree convention

Each PR gets its own worktree under `/home/edible/Projects/PAL/.worktrees/phase-f-prN`. Create at the start of each PR's task block; remove after merge. The agent_core PR uses `/home/edible/Projects/agent_core` directly with a feature branch (matching Phase E precedent).

---

# Part 1: agent_core v0.6.0 framework PR

Working directory throughout Part 1: `/home/edible/Projects/agent_core`. Use `.venv/bin/pytest`.

## Task 1: agent_core pre-flight

**Files:** none modified.

- [ ] **Step 1: Confirm clean state**

```bash
cd /home/edible/Projects/agent_core
git status
git log --oneline -3
```

Expected: working tree clean; HEAD is the v0.5.1 release commit on main.

- [ ] **Step 2: Confirm full test suite passes on baseline**

```bash
.venv/bin/pytest -q
```

Expected: all green. Capture the test count as a baseline.

- [ ] **Step 3: Create the feature branch**

```bash
git checkout -b feature/phase-f-tool-command-prompt-scaffolding
```

Expected: switched to new branch.

- [ ] **Step 4: Create empty package directories**

```bash
mkdir -p agent_core/tools agent_core/commands agent_core/prompts
```

- [ ] **Step 5: Commit the empty scaffolding**

```bash
touch agent_core/tools/__init__.py agent_core/commands/__init__.py agent_core/prompts/__init__.py
git add agent_core/tools agent_core/commands agent_core/prompts
git commit -m "chore: scaffold tools/commands/prompts subpackages"
```

Expected: commit succeeds; tree status clean.

---

## Task 2: Add `agent_core.tools.base.Tool`

**Files:**
- Create: `agent_core/agent_core/tools/base.py`
- Test: `agent_core/tests/test_tools_base.py`

- [ ] **Step 1: Write the failing test**

Create `agent_core/tests/test_tools_base.py`:

```python
"""Tests for agent_core.tools.base."""
import pytest

from agent_core.tools.base import Tool


def test_tool_subclass_inherits_classvars():
    class MyTool(Tool):
        name = "my_tool"
        description = "A test tool"
        parameters = {"type": "object", "properties": {}, "required": []}

    assert MyTool.name == "my_tool"
    assert MyTool.description == "A test tool"
    assert MyTool.requires == ()


def test_tool_to_openai_schema():
    class MyTool(Tool):
        name = "my_tool"
        description = "A test tool"
        parameters = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}

    schema = MyTool.to_openai_schema()
    assert schema == {
        "type": "function",
        "function": {
            "name": "my_tool",
            "description": "A test tool",
            "parameters": {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
        },
    }


def test_tool_run_not_implemented():
    class MyTool(Tool):
        name = "my_tool"
        description = "A test tool"
        parameters = {}

    import asyncio
    with pytest.raises(NotImplementedError):
        asyncio.run(MyTool().run({}, ctx=None))
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_tools_base.py -v
```

Expected: ImportError (base.py doesn't exist yet).

- [ ] **Step 3: Write minimal implementation**

Create `agent_core/agent_core/tools/base.py`:

```python
"""Tool base class.

Tools are class-based extension points. An agent registers a list of Tool
subclasses on its class via `tools = [...]`. The framework instantiates them,
validates their `requires` against the agent's attributes, and exposes an
executor that dispatches by tool name.

Tools access dependencies through `ctx.agent.X` at runtime. The `requires`
tuple lists attribute names that must exist on the agent at registration time;
missing requirements fail fast inside `run_daemon()`, before any user message
is processed.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from agent_core.agent import HandlerContext


class Tool:
    """Base class for agent tools.

    Subclasses set `name`, `description`, `parameters` (JSON Schema, OpenAI
    function-calling format), and optionally `requires` (a tuple of attribute
    names that must exist on the agent).

    Implement `run(args, ctx)` as an async method. It must return a string;
    errors that should reach the LLM are returned as descriptive strings, not
    raised. Unhandled exceptions are caught by the executor and converted.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    parameters: ClassVar[dict]
    requires: ClassVar[tuple[str, ...]] = ()

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
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

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_tools_base.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add agent_core/tools/base.py tests/test_tools_base.py
git commit -m "feat(tools): add Tool base class"
```

---

## Task 3: Add `agent_core.tools.executor.ToolExecutor`

**Files:**
- Create: `agent_core/agent_core/tools/executor.py`
- Test: `agent_core/tests/test_tools_executor.py`

- [ ] **Step 1: Write the failing test**

Create `agent_core/tests/test_tools_executor.py`:

```python
"""Tests for agent_core.tools.executor."""
import asyncio
import pytest

from agent_core.tools.base import Tool
from agent_core.tools.executor import ToolExecutor


class _Echo(Tool):
    name = "echo"
    description = "Echoes its input"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}

    async def run(self, args, ctx):
        return f"echo: {args['text']}"


class _NeedsCompiler(Tool):
    name = "needs_compiler"
    description = "Requires compiler"
    parameters = {}
    requires = ("compiler",)

    async def run(self, args, ctx):
        return f"compiler: {ctx.agent.compiler}"


class _Boom(Tool):
    name = "boom"
    description = "Raises"
    parameters = {}

    async def run(self, args, ctx):
        raise ValueError("kaboom")


class _CancelledTool(Tool):
    name = "cancel"
    description = "Raises CancelledError"
    parameters = {}

    async def run(self, args, ctx):
        raise asyncio.CancelledError()


class _StubAgent:
    pass


def test_build_validates_requires_present():
    agent = _StubAgent()
    agent.compiler = object()
    executor = ToolExecutor.build(agent, [_NeedsCompiler])
    assert "needs_compiler" in executor.names()


def test_build_fails_when_requires_missing():
    agent = _StubAgent()  # no .compiler
    with pytest.raises(RuntimeError, match="needs_compiler.*compiler"):
        ToolExecutor.build(agent, [_NeedsCompiler])


def test_build_excludes_disabled():
    agent = _StubAgent()
    executor = ToolExecutor.build(agent, [_Echo], disabled=frozenset({"echo"}))
    assert executor.names() == []


def test_run_unknown_returns_string():
    executor = ToolExecutor({})
    result = asyncio.run(executor.run("nope", {}, ctx=None))
    assert result == "Unknown tool: nope"


def test_run_executes_tool():
    executor = ToolExecutor({"echo": _Echo()})
    result = asyncio.run(executor.run("echo", {"text": "hi"}, ctx=None))
    assert result == "echo: hi"


def test_run_catches_exceptions():
    executor = ToolExecutor({"boom": _Boom()})
    result = asyncio.run(executor.run("boom", {}, ctx=None))
    assert result == "Error in boom: kaboom"


def test_run_does_not_swallow_cancellation():
    executor = ToolExecutor({"cancel": _CancelledTool()})
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(executor.run("cancel", {}, ctx=None))


def test_schemas_returns_openai_format():
    executor = ToolExecutor({"echo": _Echo()})
    schemas = executor.schemas()
    assert schemas == [{
        "type": "function",
        "function": {
            "name": "echo",
            "description": "Echoes its input",
            "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        },
    }]


def test_names_preserves_insertion_order():
    class A(Tool):
        name = "a"; description = ""; parameters = {}
        async def run(self, args, ctx): return ""
    class B(Tool):
        name = "b"; description = ""; parameters = {}
        async def run(self, args, ctx): return ""
    class C(Tool):
        name = "c"; description = ""; parameters = {}
        async def run(self, args, ctx): return ""
    agent = _StubAgent()
    # Build with no builtins; the test stubs in builtins=[] via a monkeypatch below.
    import agent_core.tools.executor as ex_mod
    original = ex_mod.BUILTIN_TOOLS if hasattr(ex_mod, "BUILTIN_TOOLS") else None
    try:
        # If BUILTIN_TOOLS lives in builtin module, we want to neutralize it for ordering test:
        from agent_core.tools import builtin as b_mod
        saved = b_mod.BUILTIN_TOOLS
        b_mod.BUILTIN_TOOLS = []
        executor = ToolExecutor.build(agent, [A, B, C])
        assert executor.names() == ["a", "b", "c"]
    finally:
        b_mod.BUILTIN_TOOLS = saved
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_tools_executor.py -v
```

Expected: ImportError (executor.py doesn't exist yet) or builtin module missing.

- [ ] **Step 3: Write minimal implementation**

Create `agent_core/agent_core/tools/builtin.py` (placeholder; will be filled in later tasks):

```python
"""Builtin tool list. Populated as tools are added in subsequent tasks."""
from agent_core.tools.base import Tool

BUILTIN_TOOLS: list[type[Tool]] = []
```

Create `agent_core/agent_core/tools/executor.py`:

```python
"""Tool executor: registry + dispatch + exception containment.

The executor is constructed at agent startup via `ToolExecutor.build()`, which
unions builtins with agent-supplied tool classes, drops anything in the
`disabled` set, validates each tool's `requires` against the agent's attrs,
and instantiates the surviving classes. The executor is then attached to the
agent as `agent.tool_executor` and used by the agent's `handle_chat` to
dispatch tool calls returned by the model.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from agent_core.tools.base import Tool
from agent_core.tools.builtin import BUILTIN_TOOLS

if TYPE_CHECKING:
    from agent_core.agent import HandlerContext


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
        all_classes = [
            t for t in BUILTIN_TOOLS + list(agent_tool_classes) if t.name not in disabled
        ]
        instances: dict[str, Tool] = {}
        for tool_cls in all_classes:
            for attr in tool_cls.requires:
                if not hasattr(agent, attr):
                    raise RuntimeError(
                        f"Tool {tool_cls.name!r} requires agent.{attr!r}, "
                        f"but {type(agent).__name__} has no such attribute. "
                        f"Set it in setup() or add {tool_cls.name!r} to disabled_builtins."
                    )
            instances[tool_cls.name] = tool_cls()
        return cls(instances)

    async def run(self, name: str, arguments: dict, ctx: "HandlerContext") -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"Unknown tool: {name}"
        try:
            return await tool.run(arguments, ctx)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return f"Error in {name}: {exc}"

    def schemas(self) -> list[dict]:
        return [type(t).to_openai_schema() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)
```

Create `agent_core/agent_core/tools/__init__.py`:

```python
"""Tool registry, executor, and builtins."""
from agent_core.tools.base import Tool
from agent_core.tools.executor import ToolExecutor

__all__ = ["Tool", "ToolExecutor"]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_tools_executor.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add agent_core/tools/__init__.py agent_core/tools/executor.py agent_core/tools/builtin.py tests/test_tools_executor.py
git commit -m "feat(tools): add ToolExecutor with build() and exception containment"
```

---

## Task 4: Add shell tool helpers (path safety, output capping)

**Files:**
- Create: `agent_core/agent_core/tools/_shell_helpers.py`
- Test: `agent_core/tests/test_tools_shell_helpers.py`

These helpers are shared across all seven shell tools.

- [ ] **Step 1: Write the failing test**

Create `agent_core/tests/test_tools_shell_helpers.py`:

```python
"""Tests for shell tool helpers."""
import pytest

from agent_core.tools._shell_helpers import (
    OUTPUT_CAP_BYTES,
    cap_output,
    is_system_path,
    resolve_safe,
)


def test_resolve_safe_inside_vault(tmp_path):
    f = tmp_path / "Notes" / "x.md"
    f.parent.mkdir()
    f.write_text("hi")
    resolved = resolve_safe(tmp_path, "Notes/x.md")
    assert resolved == f.resolve()


def test_resolve_safe_rejects_escape(tmp_path):
    assert resolve_safe(tmp_path, "../../../etc/passwd") is None


def test_resolve_safe_rejects_absolute_outside(tmp_path):
    assert resolve_safe(tmp_path, "/etc/passwd") is None


def test_resolve_safe_handles_dotdot(tmp_path):
    (tmp_path / "Notes").mkdir()
    (tmp_path / "Notes" / "x.md").write_text("hi")
    # Notes/../Notes/x.md normalizes to Notes/x.md inside the vault
    assert resolve_safe(tmp_path, "Notes/../Notes/x.md") == (tmp_path / "Notes/x.md").resolve()


def test_is_system_path():
    assert is_system_path("_index.md")
    assert is_system_path("_channels/foo")
    assert is_system_path("Notes/_private.md")
    assert not is_system_path("Notes/x.md")
    assert not is_system_path("Notes")


def test_cap_output_under_limit():
    text = "hello"
    assert cap_output(text) == text


def test_cap_output_truncates_when_over():
    text = "x" * (OUTPUT_CAP_BYTES + 100)
    capped = cap_output(text)
    assert len(capped.encode("utf-8")) <= OUTPUT_CAP_BYTES + 200  # +footer
    assert "[output truncated:" in capped
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_tools_shell_helpers.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write minimal implementation**

Create `agent_core/agent_core/tools/_shell_helpers.py`:

```python
"""Shared helpers for shell-style builtin tools.

Path resolution rooted at the agent's vault. System paths (any component
starting with `_`) are rejected. Output is capped at 32 KB with a truncation
footer.
"""
from __future__ import annotations

from pathlib import Path

OUTPUT_CAP_BYTES = 32 * 1024


def resolve_safe(vault_path: Path, arg: str) -> Path | None:
    """Resolve `arg` against `vault_path`. Returns None if it escapes."""
    try:
        full = (vault_path / arg).resolve()
    except (OSError, ValueError):
        return None
    try:
        full.relative_to(vault_path.resolve())
    except ValueError:
        return None
    return full


def is_system_path(path: str) -> bool:
    """True if any path component starts with `_`."""
    return any(part.startswith("_") for part in Path(path).parts)


def cap_output(text: str) -> str:
    """Cap text at OUTPUT_CAP_BYTES, append a truncation footer if cut."""
    encoded = text.encode("utf-8")
    if len(encoded) <= OUTPUT_CAP_BYTES:
        return text
    truncated_bytes = encoded[:OUTPUT_CAP_BYTES]
    # Don't split a multi-byte character at the boundary
    truncated = truncated_bytes.decode("utf-8", errors="ignore")
    dropped = len(encoded) - len(truncated.encode("utf-8"))
    return truncated + f"\n\n[output truncated: {dropped} bytes dropped]"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_tools_shell_helpers.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add agent_core/tools/_shell_helpers.py tests/test_tools_shell_helpers.py
git commit -m "feat(tools): add shell tool path/output helpers"
```

---

## Task 5: Add `Cat` shell tool

**Files:**
- Create: `agent_core/agent_core/tools/_shell.py`
- Test: `agent_core/tests/test_tool_cat.py`

- [ ] **Step 1: Write the failing test**

Create `agent_core/tests/test_tool_cat.py`:

```python
"""Tests for the cat shell tool."""
import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent_core.tools._shell import Cat


@dataclass
class _Config:
    vault_path: Path


class _Agent:
    def __init__(self, vault_path):
        self.config = _Config(vault_path)


def _ctx(agent):
    class _C:
        pass
    c = _C()
    c.agent = agent
    return c


def test_cat_returns_file_contents(tmp_path):
    (tmp_path / "x.md").write_text("hello world")
    agent = _Agent(tmp_path)
    result = asyncio.run(Cat().run({"path": "x.md"}, _ctx(agent)))
    assert result == "hello world"


def test_cat_rejects_path_escape(tmp_path):
    agent = _Agent(tmp_path)
    result = asyncio.run(Cat().run({"path": "../../../etc/passwd"}, _ctx(agent)))
    assert "outside vault" in result.lower() or "escape" in result.lower()


def test_cat_rejects_system_path(tmp_path):
    (tmp_path / "_index.md").write_text("internal")
    agent = _Agent(tmp_path)
    result = asyncio.run(Cat().run({"path": "_index.md"}, _ctx(agent)))
    assert "system path" in result.lower()


def test_cat_missing_file(tmp_path):
    agent = _Agent(tmp_path)
    result = asyncio.run(Cat().run({"path": "nope.md"}, _ctx(agent)))
    assert "not found" in result.lower()


def test_cat_truncates_large_file(tmp_path):
    big = "x" * (40 * 1024)
    (tmp_path / "big.md").write_text(big)
    agent = _Agent(tmp_path)
    result = asyncio.run(Cat().run({"path": "big.md"}, _ctx(agent)))
    assert "[output truncated:" in result


def test_cat_requires_path(tmp_path):
    agent = _Agent(tmp_path)
    result = asyncio.run(Cat().run({}, _ctx(agent)))
    assert "path" in result.lower() and "required" in result.lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_tool_cat.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write minimal implementation**

Create `agent_core/agent_core/tools/_shell.py`:

```python
"""Read-only shell-style builtin tools, scoped to the agent's vault."""
from __future__ import annotations

from agent_core.tools.base import Tool
from agent_core.tools._shell_helpers import cap_output, is_system_path, resolve_safe


class Cat(Tool):
    name = "cat"
    description = "Read the full contents of a vault file. For files larger than 32 KB the output is truncated; use head/tail/read_lines to slice."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Vault-relative file path."},
        },
        "required": ["path"],
    }
    requires = ("config",)

    async def run(self, args, ctx):
        path = (args.get("path") or "").strip()
        if not path:
            return "Error: 'path' parameter is required."
        if is_system_path(path):
            return f"Error: system path is not readable: {path}"
        resolved = resolve_safe(ctx.agent.config.vault_path, path)
        if resolved is None:
            return f"Error: path escapes outside vault: {path}"
        if not resolved.exists():
            return f"File not found: {path}"
        if not resolved.is_file():
            return f"Not a file: {path}"
        try:
            content = resolved.read_text(errors="replace")
        except OSError as exc:
            return f"Error reading {path}: {exc}"
        return cap_output(content)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_tool_cat.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add agent_core/tools/_shell.py tests/test_tool_cat.py
git commit -m "feat(tools): add cat builtin tool"
```

---

## Task 6: Add `Head` and `Tail` shell tools

**Files:**
- Modify: `agent_core/agent_core/tools/_shell.py`
- Test: `agent_core/tests/test_tool_head_tail.py`

- [ ] **Step 1: Write the failing test**

Create `agent_core/tests/test_tool_head_tail.py`:

```python
"""Tests for head and tail builtin tools."""
import asyncio
from dataclasses import dataclass
from pathlib import Path

from agent_core.tools._shell import Head, Tail


@dataclass
class _Config:
    vault_path: Path


class _Agent:
    def __init__(self, vault_path):
        self.config = _Config(vault_path)


def _ctx(agent):
    class _C: pass
    c = _C(); c.agent = agent; return c


def test_head_default_20_lines(tmp_path):
    (tmp_path / "x.md").write_text("\n".join(f"line {i}" for i in range(50)))
    agent = _Agent(tmp_path)
    result = asyncio.run(Head().run({"path": "x.md"}, _ctx(agent)))
    assert result.splitlines() == [f"line {i}" for i in range(20)]


def test_head_explicit_lines(tmp_path):
    (tmp_path / "x.md").write_text("\n".join(f"line {i}" for i in range(50)))
    agent = _Agent(tmp_path)
    result = asyncio.run(Head().run({"path": "x.md", "lines": 5}, _ctx(agent)))
    assert result.splitlines() == [f"line {i}" for i in range(5)]


def test_head_short_file(tmp_path):
    (tmp_path / "x.md").write_text("only\ntwo")
    agent = _Agent(tmp_path)
    result = asyncio.run(Head().run({"path": "x.md", "lines": 100}, _ctx(agent)))
    assert result == "only\ntwo"


def test_tail_default_20_lines(tmp_path):
    (tmp_path / "x.md").write_text("\n".join(f"line {i}" for i in range(50)))
    agent = _Agent(tmp_path)
    result = asyncio.run(Tail().run({"path": "x.md"}, _ctx(agent)))
    assert result.splitlines() == [f"line {i}" for i in range(30, 50)]


def test_tail_explicit_lines(tmp_path):
    (tmp_path / "x.md").write_text("\n".join(f"line {i}" for i in range(50)))
    agent = _Agent(tmp_path)
    result = asyncio.run(Tail().run({"path": "x.md", "lines": 3}, _ctx(agent)))
    assert result.splitlines() == ["line 47", "line 48", "line 49"]


def test_head_rejects_escape(tmp_path):
    agent = _Agent(tmp_path)
    result = asyncio.run(Head().run({"path": "../../etc/passwd"}, _ctx(agent)))
    assert "outside vault" in result.lower()


def test_tail_missing_file(tmp_path):
    agent = _Agent(tmp_path)
    result = asyncio.run(Tail().run({"path": "nope.md"}, _ctx(agent)))
    assert "not found" in result.lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_tool_head_tail.py -v
```

Expected: ImportError (Head/Tail not yet defined).

- [ ] **Step 3: Append Head and Tail to `_shell.py`**

Append to `agent_core/agent_core/tools/_shell.py`:

```python
def _read_safe(args, vault_path):
    """Resolve and validate a path arg; return (resolved, error_str_or_none)."""
    path = (args.get("path") or "").strip()
    if not path:
        return None, "Error: 'path' parameter is required."
    if is_system_path(path):
        return None, f"Error: system path is not readable: {path}"
    resolved = resolve_safe(vault_path, path)
    if resolved is None:
        return None, f"Error: path escapes outside vault: {path}"
    if not resolved.exists():
        return None, f"File not found: {path}"
    if not resolved.is_file():
        return None, f"Not a file: {path}"
    return resolved, None


class Head(Tool):
    name = "head"
    description = "Read the first N lines of a vault file (default 20)."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Vault-relative file path."},
            "lines": {"type": "integer", "description": "Number of lines (default 20)."},
        },
        "required": ["path"],
    }
    requires = ("config",)

    async def run(self, args, ctx):
        resolved, err = _read_safe(args, ctx.agent.config.vault_path)
        if err is not None:
            return err
        n = max(1, int(args.get("lines", 20)))
        out = []
        with resolved.open("r", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= n:
                    break
                out.append(line.rstrip("\n"))
        return cap_output("\n".join(out))


class Tail(Tool):
    name = "tail"
    description = "Read the last N lines of a vault file (default 20)."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Vault-relative file path."},
            "lines": {"type": "integer", "description": "Number of lines (default 20)."},
        },
        "required": ["path"],
    }
    requires = ("config",)

    async def run(self, args, ctx):
        from collections import deque
        resolved, err = _read_safe(args, ctx.agent.config.vault_path)
        if err is not None:
            return err
        n = max(1, int(args.get("lines", 20)))
        with resolved.open("r", errors="replace") as f:
            tail = deque(f, maxlen=n)
        return cap_output("\n".join(line.rstrip("\n") for line in tail))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_tool_head_tail.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add agent_core/tools/_shell.py tests/test_tool_head_tail.py
git commit -m "feat(tools): add head and tail builtin tools"
```

---

## Task 7: Add `Ls` shell tool

**Files:**
- Modify: `agent_core/agent_core/tools/_shell.py`
- Test: `agent_core/tests/test_tool_ls.py`

- [ ] **Step 1: Write the failing test**

Create `agent_core/tests/test_tool_ls.py`:

```python
"""Tests for the ls builtin tool."""
import asyncio
from dataclasses import dataclass
from pathlib import Path

from agent_core.tools._shell import Ls


@dataclass
class _Config:
    vault_path: Path


class _Agent:
    def __init__(self, vault_path):
        self.config = _Config(vault_path)


def _ctx(agent):
    class _C: pass
    c = _C(); c.agent = agent; return c


def test_ls_root(tmp_path):
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "b.md").write_text("b")
    (tmp_path / "Notes").mkdir()
    agent = _Agent(tmp_path)
    result = asyncio.run(Ls().run({}, _ctx(agent)))
    lines = set(result.splitlines())
    assert "a.md" in lines
    assert "b.md" in lines
    assert "Notes/" in lines


def test_ls_subdir(tmp_path):
    (tmp_path / "Notes").mkdir()
    (tmp_path / "Notes" / "x.md").write_text("x")
    agent = _Agent(tmp_path)
    result = asyncio.run(Ls().run({"path": "Notes"}, _ctx(agent)))
    assert result.strip() == "x.md"


def test_ls_hides_system_paths_by_default(tmp_path):
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "_index.md").write_text("internal")
    agent = _Agent(tmp_path)
    result = asyncio.run(Ls().run({}, _ctx(agent)))
    assert "_index.md" not in result
    assert "a.md" in result


def test_ls_show_hidden(tmp_path):
    (tmp_path / "_index.md").write_text("internal")
    agent = _Agent(tmp_path)
    result = asyncio.run(Ls().run({"show_hidden": True}, _ctx(agent)))
    assert "_index.md" in result


def test_ls_long_format(tmp_path):
    (tmp_path / "a.md").write_text("hello")
    agent = _Agent(tmp_path)
    result = asyncio.run(Ls().run({"long": True}, _ctx(agent)))
    assert "a.md" in result
    # Long format includes size + mtime; check at least one number is present
    assert any(c.isdigit() for c in result)


def test_ls_rejects_escape(tmp_path):
    agent = _Agent(tmp_path)
    result = asyncio.run(Ls().run({"path": "../.."}, _ctx(agent)))
    assert "outside vault" in result.lower()


def test_ls_caps_at_500_entries(tmp_path):
    for i in range(600):
        (tmp_path / f"f{i:04}.md").write_text(".")
    agent = _Agent(tmp_path)
    result = asyncio.run(Ls().run({}, _ctx(agent)))
    assert "[output truncated:" in result or "more entries" in result.lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_tool_ls.py -v
```

Expected: ImportError.

- [ ] **Step 3: Append Ls to `_shell.py`**

```python
class Ls(Tool):
    name = "ls"
    description = "List files and subdirectories in a vault directory. Capped at 500 entries."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Vault-relative directory path; empty for root."},
            "show_hidden": {"type": "boolean", "description": "Show _-prefixed entries (default false)."},
            "long": {"type": "boolean", "description": "Include size and mtime per entry (default false)."},
        },
        "required": [],
    }
    requires = ("config",)

    async def run(self, args, ctx):
        from datetime import datetime, timezone

        path = (args.get("path") or "").strip()
        show_hidden = bool(args.get("show_hidden", False))
        long_fmt = bool(args.get("long", False))
        max_entries = 500

        vault = ctx.agent.config.vault_path
        if path:
            if is_system_path(path) and not show_hidden:
                return f"Error: system path is not listable without show_hidden: {path}"
            resolved = resolve_safe(vault, path)
            if resolved is None:
                return f"Error: path escapes outside vault: {path}"
        else:
            resolved = vault.resolve()
        if not resolved.exists():
            return f"Directory not found: {path or '/'}"
        if not resolved.is_dir():
            return f"Not a directory: {path or '/'}"

        try:
            entries = sorted(resolved.iterdir(), key=lambda p: p.name)
        except OSError as exc:
            return f"Error listing {path or '/'}: {exc}"

        out_lines = []
        truncated = False
        for entry in entries:
            if not show_hidden and entry.name.startswith("_"):
                continue
            if len(out_lines) >= max_entries:
                truncated = True
                break
            display = entry.name + ("/" if entry.is_dir() else "")
            if long_fmt:
                try:
                    st = entry.stat()
                    size = st.st_size
                    mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
                    out_lines.append(f"{size:>10} {mtime} {display}")
                except OSError:
                    out_lines.append(f"         ?           ? {display}")
            else:
                out_lines.append(display)
        if truncated:
            out_lines.append(f"[output truncated: more than {max_entries} entries]")
        return cap_output("\n".join(out_lines))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_tool_ls.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add agent_core/tools/_shell.py tests/test_tool_ls.py
git commit -m "feat(tools): add ls builtin tool"
```

---

## Task 8: Add `Grep` shell tool

**Files:**
- Modify: `agent_core/agent_core/tools/_shell.py`
- Test: `agent_core/tests/test_tool_grep.py`

- [ ] **Step 1: Write the failing test**

Create `agent_core/tests/test_tool_grep.py`:

```python
"""Tests for the grep builtin tool."""
import asyncio
from dataclasses import dataclass
from pathlib import Path

from agent_core.tools._shell import Grep


@dataclass
class _Config:
    vault_path: Path


class _Agent:
    def __init__(self, vault_path):
        self.config = _Config(vault_path)


def _ctx(agent):
    class _C: pass
    c = _C(); c.agent = agent; return c


def test_grep_finds_substring(tmp_path):
    (tmp_path / "x.md").write_text("apple\nbanana\ncherry")
    agent = _Agent(tmp_path)
    result = asyncio.run(Grep().run({"pattern": "banana"}, _ctx(agent)))
    assert "x.md:2:" in result
    assert "banana" in result


def test_grep_no_match(tmp_path):
    (tmp_path / "x.md").write_text("apple")
    agent = _Agent(tmp_path)
    result = asyncio.run(Grep().run({"pattern": "zzz"}, _ctx(agent)))
    assert "no match" in result.lower() or result.strip() == ""


def test_grep_case_insensitive(tmp_path):
    (tmp_path / "x.md").write_text("APPLE")
    agent = _Agent(tmp_path)
    result = asyncio.run(Grep().run({"pattern": "apple", "ignore_case": True}, _ctx(agent)))
    assert "x.md:1:" in result


def test_grep_regex(tmp_path):
    (tmp_path / "x.md").write_text("foo123\nfoo456\nbar")
    agent = _Agent(tmp_path)
    result = asyncio.run(Grep().run({"pattern": r"foo\d+", "regex": True}, _ctx(agent)))
    assert "foo123" in result
    assert "foo456" in result
    assert "bar" not in result


def test_grep_subdir(tmp_path):
    (tmp_path / "Notes").mkdir()
    (tmp_path / "Notes" / "x.md").write_text("hit")
    (tmp_path / "Other" / "y.md").parent.mkdir()
    (tmp_path / "Other" / "y.md").write_text("hit")
    agent = _Agent(tmp_path)
    result = asyncio.run(Grep().run({"pattern": "hit", "path": "Notes"}, _ctx(agent)))
    assert "Notes/x.md" in result
    assert "Other/y.md" not in result


def test_grep_skips_system_paths(tmp_path):
    (tmp_path / "_index.md").write_text("hit")
    (tmp_path / "x.md").write_text("hit")
    agent = _Agent(tmp_path)
    result = asyncio.run(Grep().run({"pattern": "hit"}, _ctx(agent)))
    assert "_index.md" not in result
    assert "x.md" in result


def test_grep_invalid_regex(tmp_path):
    agent = _Agent(tmp_path)
    result = asyncio.run(Grep().run({"pattern": "[", "regex": True}, _ctx(agent)))
    assert "regex" in result.lower() or "invalid" in result.lower()


def test_grep_max_hits(tmp_path):
    (tmp_path / "x.md").write_text("\n".join(["match"] * 200))
    agent = _Agent(tmp_path)
    result = asyncio.run(Grep().run({"pattern": "match", "max_hits": 10}, _ctx(agent)))
    # Count number of "match" hits in the output (each on its own line)
    hit_lines = [l for l in result.splitlines() if l.startswith("x.md:")]
    assert len(hit_lines) == 10
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_tool_grep.py -v
```

Expected: ImportError.

- [ ] **Step 3: Append Grep to `_shell.py`**

```python
class Grep(Tool):
    name = "grep"
    description = "Keyword or regex search across vault files. Returns path:lineno:line per hit, capped at 100 hits by default."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Plain string by default; regex when regex=true."},
            "path": {"type": "string", "description": "Subdir or file to search (vault-relative). Empty = vault root."},
            "regex": {"type": "boolean", "description": "Treat pattern as Python regex (default false)."},
            "ignore_case": {"type": "boolean", "description": "Case-insensitive match (default false)."},
            "max_hits": {"type": "integer", "description": "Cap on number of hits (default 100)."},
        },
        "required": ["pattern"],
    }
    requires = ("config",)

    async def run(self, args, ctx):
        import re

        pattern_str = args.get("pattern", "")
        if not pattern_str:
            return "Error: 'pattern' parameter is required."
        path = (args.get("path") or "").strip()
        as_regex = bool(args.get("regex", False))
        ignore_case = bool(args.get("ignore_case", False))
        max_hits = max(1, min(int(args.get("max_hits", 100)), 1000))

        vault = ctx.agent.config.vault_path
        if path:
            resolved = resolve_safe(vault, path)
            if resolved is None:
                return f"Error: path escapes outside vault: {path}"
            if is_system_path(path):
                return f"Error: system path is not searchable: {path}"
        else:
            resolved = vault.resolve()
        if not resolved.exists():
            return f"Path not found: {path or '/'}"

        flags = re.IGNORECASE if ignore_case else 0
        try:
            if as_regex:
                regex = re.compile(pattern_str, flags)
            else:
                regex = re.compile(re.escape(pattern_str), flags)
        except re.error as exc:
            return f"Error: invalid regex: {exc}"

        targets: list[Path] = []
        if resolved.is_file():
            targets = [resolved]
        else:
            for p in resolved.rglob("*"):
                if not p.is_file():
                    continue
                rel = p.relative_to(vault.resolve())
                if any(part.startswith("_") for part in rel.parts):
                    continue
                targets.append(p)

        hits = []
        for target in targets:
            if len(hits) >= max_hits:
                break
            try:
                with target.open("r", errors="replace") as f:
                    for lineno, line in enumerate(f, start=1):
                        if regex.search(line):
                            rel = target.relative_to(vault.resolve())
                            hits.append(f"{rel}:{lineno}: {line.rstrip()}")
                            if len(hits) >= max_hits:
                                break
            except OSError:
                continue
        if not hits:
            return f"No match for: {pattern_str}"
        if len(hits) >= max_hits:
            hits.append(f"[output truncated: hit cap of {max_hits} reached]")
        return cap_output("\n".join(hits))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_tool_grep.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add agent_core/tools/_shell.py tests/test_tool_grep.py
git commit -m "feat(tools): add grep builtin tool"
```

---

## Task 9: Add `Find` and `ReadLines` shell tools

**Files:**
- Modify: `agent_core/agent_core/tools/_shell.py`
- Test: `agent_core/tests/test_tool_find_readlines.py`

- [ ] **Step 1: Write the failing test**

Create `agent_core/tests/test_tool_find_readlines.py`:

```python
"""Tests for find and read_lines builtin tools."""
import asyncio
from dataclasses import dataclass
from pathlib import Path

from agent_core.tools._shell import Find, ReadLines


@dataclass
class _Config:
    vault_path: Path


class _Agent:
    def __init__(self, vault_path):
        self.config = _Config(vault_path)


def _ctx(agent):
    class _C: pass
    c = _C(); c.agent = agent; return c


def test_find_glob_match(tmp_path):
    (tmp_path / "agent-1.md").write_text("a")
    (tmp_path / "agent-2.md").write_text("b")
    (tmp_path / "other.md").write_text("c")
    agent = _Agent(tmp_path)
    result = asyncio.run(Find().run({"pattern": "agent-*.md"}, _ctx(agent)))
    lines = set(result.splitlines())
    assert "agent-1.md" in lines
    assert "agent-2.md" in lines
    assert "other.md" not in lines


def test_find_recursive_glob(tmp_path):
    (tmp_path / "Notes").mkdir()
    (tmp_path / "Notes" / "quantum-1.md").write_text("a")
    (tmp_path / "Other").mkdir()
    (tmp_path / "Other" / "quantum-2.md").write_text("b")
    agent = _Agent(tmp_path)
    result = asyncio.run(Find().run({"pattern": "**/quantum*"}, _ctx(agent)))
    lines = set(result.splitlines())
    assert "Notes/quantum-1.md" in lines
    assert "Other/quantum-2.md" in lines


def test_find_skips_system_paths(tmp_path):
    (tmp_path / "_index.md").write_text("a")
    (tmp_path / "x.md").write_text("b")
    agent = _Agent(tmp_path)
    result = asyncio.run(Find().run({"pattern": "*.md"}, _ctx(agent)))
    lines = set(result.splitlines())
    assert "_index.md" not in lines
    assert "x.md" in lines


def test_find_type_filter(tmp_path):
    (tmp_path / "f.md").write_text("a")
    (tmp_path / "d").mkdir()
    agent = _Agent(tmp_path)
    files_only = asyncio.run(Find().run({"pattern": "*", "type": "f"}, _ctx(agent)))
    dirs_only = asyncio.run(Find().run({"pattern": "*", "type": "d"}, _ctx(agent)))
    assert "f.md" in files_only and "d" not in files_only.splitlines()
    assert "d" in dirs_only.splitlines() and "f.md" not in dirs_only


def test_find_no_match(tmp_path):
    agent = _Agent(tmp_path)
    result = asyncio.run(Find().run({"pattern": "nope*"}, _ctx(agent)))
    assert "no match" in result.lower() or result.strip() == ""


def test_read_lines_range(tmp_path):
    (tmp_path / "x.md").write_text("\n".join(f"line {i}" for i in range(1, 21)))
    agent = _Agent(tmp_path)
    result = asyncio.run(ReadLines().run({"path": "x.md", "start": 5, "end": 7}, _ctx(agent)))
    assert "5: line 5" in result
    assert "6: line 6" in result
    assert "7: line 7" in result
    assert "4: line 4" not in result
    assert "8: line 8" not in result


def test_read_lines_bounds_clamped(tmp_path):
    (tmp_path / "x.md").write_text("a\nb\nc")
    agent = _Agent(tmp_path)
    result = asyncio.run(ReadLines().run({"path": "x.md", "start": 1, "end": 100}, _ctx(agent)))
    assert "1: a" in result
    assert "2: b" in result
    assert "3: c" in result


def test_read_lines_invalid_range(tmp_path):
    (tmp_path / "x.md").write_text("a")
    agent = _Agent(tmp_path)
    result = asyncio.run(ReadLines().run({"path": "x.md", "start": 5, "end": 2}, _ctx(agent)))
    assert "invalid" in result.lower() or "range" in result.lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_tool_find_readlines.py -v
```

Expected: ImportError.

- [ ] **Step 3: Append Find and ReadLines to `_shell.py`**

```python
class Find(Tool):
    name = "find"
    description = "Filename glob search. Patterns like 'agent-*.md' or '**/quantum*'. Capped at 500 results."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern."},
            "path": {"type": "string", "description": "Subdir to search (vault-relative). Empty = vault root."},
            "type": {"type": "string", "description": "'f' for files, 'd' for dirs, '' for both (default)."},
            "max_results": {"type": "integer", "description": "Cap on number of results (default 500)."},
        },
        "required": ["pattern"],
    }
    requires = ("config",)

    async def run(self, args, ctx):
        pattern = args.get("pattern", "")
        if not pattern:
            return "Error: 'pattern' parameter is required."
        path = (args.get("path") or "").strip()
        type_filter = (args.get("type") or "").strip()
        max_results = max(1, min(int(args.get("max_results", 500)), 5000))

        vault = ctx.agent.config.vault_path
        if path:
            resolved = resolve_safe(vault, path)
            if resolved is None:
                return f"Error: path escapes outside vault: {path}"
        else:
            resolved = vault.resolve()
        if not resolved.exists() or not resolved.is_dir():
            return f"Directory not found: {path or '/'}"

        results = []
        for p in resolved.rglob(pattern):
            rel = p.relative_to(vault.resolve())
            if any(part.startswith("_") for part in rel.parts):
                continue
            if type_filter == "f" and not p.is_file():
                continue
            if type_filter == "d" and not p.is_dir():
                continue
            results.append(str(rel))
            if len(results) >= max_results:
                break
        if not results:
            return f"No match for: {pattern}"
        if len(results) >= max_results:
            results.append(f"[output truncated: result cap of {max_results} reached]")
        return cap_output("\n".join(results))


class ReadLines(Tool):
    name = "read_lines"
    description = "Read a specific 1-indexed line range from a vault file. Pairs with grep hits."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Vault-relative file path."},
            "start": {"type": "integer", "description": "Starting line number (1-indexed, inclusive)."},
            "end": {"type": "integer", "description": "Ending line number (1-indexed, inclusive)."},
        },
        "required": ["path", "start", "end"],
    }
    requires = ("config",)

    async def run(self, args, ctx):
        resolved, err = _read_safe(args, ctx.agent.config.vault_path)
        if err is not None:
            return err
        try:
            start = int(args["start"])
            end = int(args["end"])
        except (KeyError, TypeError, ValueError):
            return "Error: 'start' and 'end' integers are required."
        if start < 1 or end < start:
            return f"Error: invalid line range: start={start}, end={end}."
        out = []
        with resolved.open("r", errors="replace") as f:
            for lineno, line in enumerate(f, start=1):
                if lineno < start:
                    continue
                if lineno > end:
                    break
                out.append(f"{lineno}: {line.rstrip()}")
        if not out:
            return f"No lines in range {start}..{end}."
        return cap_output("\n".join(out))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_tool_find_readlines.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add agent_core/tools/_shell.py tests/test_tool_find_readlines.py
git commit -m "feat(tools): add find and read_lines builtin tools"
```

---

## Task 10: Add the 5 framework-backed builtin tools

**Files:**
- Create: `agent_core/agent_core/tools/_framework.py`
- Test: `agent_core/tests/test_tools_framework.py`

These tools are lifted from PAL's existing executor methods. Each only touches state already wired by `run_daemon`.

- [ ] **Step 1: Write the failing test**

Create `agent_core/tests/test_tools_framework.py`:

```python
"""Tests for framework-backed builtin tools."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_core.tools._framework import (
    AddLearning,
    FetchUrl,
    SearchVault,
    SearchWeb,
    UpdateScratch,
)


def _ctx(agent):
    class _C: pass
    c = _C(); c.agent = agent; c.channel_id = "default"; return c


def test_search_web_formats_results():
    from agent_core.websearch import SearchResult
    websearch = MagicMock()
    websearch.search = AsyncMock(return_value=[
        SearchResult(url="http://a.com", title="A", snippet="snippet A"),
        SearchResult(url="http://b.com", title="B", snippet="snippet B"),
    ])
    agent = MagicMock(websearch=websearch)
    result = asyncio.run(SearchWeb().run({"query": "test"}, _ctx(agent)))
    assert "A" in result and "http://a.com" in result and "snippet A" in result
    assert "B" in result and "http://b.com" in result


def test_search_web_requires_query():
    agent = MagicMock()
    result = asyncio.run(SearchWeb().run({}, _ctx(agent)))
    assert "query" in result.lower() and "required" in result.lower()


def test_search_vault_calls_retrieval():
    retrieval = MagicMock()
    retrieval.query = AsyncMock(return_value=[
        {"path": "Notes/a.md", "snippet": "matched"},
    ])
    agent = MagicMock(retrieval=retrieval)
    result = asyncio.run(SearchVault().run({"query": "test"}, _ctx(agent)))
    assert "Notes/a.md" in result
    assert "matched" in result


def test_fetch_url_calls_fetcher(monkeypatch):
    from agent_core.utils import fetcher as fetcher_mod
    captured = {}
    async def fake_fetch(url, *, allowlist, **kw):
        captured["url"] = url
        captured["allowlist"] = allowlist
        return MagicMock(text="page body", title="Page")
    monkeypatch.setattr(fetcher_mod, "fetch_and_extract", fake_fetch)
    agent = MagicMock(allowlist=MagicMock())
    result = asyncio.run(FetchUrl().run({"url": "http://example.com"}, _ctx(agent)))
    assert "page body" in result
    assert captured["url"] == "http://example.com"


def test_update_scratch_writes():
    sp = MagicMock()
    sp.write = MagicMock()
    channels = MagicMock()
    channels.scratchpad = MagicMock(return_value=sp)
    agent = MagicMock(channels=channels)
    result = asyncio.run(UpdateScratch().run({"text": "new content"}, _ctx(agent)))
    sp.write.assert_called_once_with("new content")
    assert "updated" in result.lower() or "ok" in result.lower()


def test_add_learning_stores():
    learning = MagicMock()
    learning.add_candidate = MagicMock(return_value="learn_123")
    agent = MagicMock(learning=learning)
    result = asyncio.run(AddLearning().run(
        {"title": "T", "body": "B"}, _ctx(agent)
    ))
    learning.add_candidate.assert_called_once()
    assert "learn_123" in result or "added" in result.lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_tools_framework.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write the implementations**

Create `agent_core/agent_core/tools/_framework.py`:

```python
"""Framework-manager-backed builtin tools.

Each tool only touches state already wired onto the Agent by run_daemon
(retrieval, websearch, allowlist, channels, learning). Lifted from PAL where
the implementation was already framework-only.
"""
from __future__ import annotations

from agent_core.tools.base import Tool


class FetchUrl(Tool):
    name = "fetch_url"
    description = "Fetch a URL through the agent's allowlist and return extracted text content."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch."},
        },
        "required": ["url"],
    }
    requires = ("allowlist",)

    async def run(self, args, ctx):
        from agent_core.utils import fetcher as fetcher_mod
        url = (args.get("url") or "").strip()
        if not url:
            return "Error: 'url' parameter is required."
        try:
            doc = await fetcher_mod.fetch_and_extract(url, allowlist=ctx.agent.allowlist)
        except Exception as exc:
            return f"Fetch error: {exc}"
        title = getattr(doc, "title", "") or ""
        body = getattr(doc, "text", "") or ""
        if title:
            return f"# {title}\n\n{body}"
        return body


class SearchVault(Tool):
    name = "search_vault"
    description = "Semantic search over the vault. Returns matching files with snippets."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "max_results": {"type": "integer", "description": "Cap on results (default 5)."},
        },
        "required": ["query"],
    }
    requires = ("retrieval",)

    async def run(self, args, ctx):
        query = (args.get("query") or "").strip()
        if not query:
            return "Error: 'query' parameter is required."
        max_results = max(1, min(int(args.get("max_results", 5)), 20))
        try:
            results = await ctx.agent.retrieval.query(query, top_k=max_results)
        except Exception as exc:
            return f"Search error: {exc}"
        if not results:
            return f"No vault matches for: {query}"
        lines = [f"Found {len(results)} match(es) for '{query}':"]
        for r in results:
            path = r.get("path", "?") if isinstance(r, dict) else getattr(r, "path", "?")
            snippet = r.get("snippet", "") if isinstance(r, dict) else getattr(r, "snippet", "")
            lines.append(f"  {path}")
            if snippet:
                lines.append(f"    {snippet[:200]}")
        return "\n".join(lines)


class SearchWeb(Tool):
    name = "search_web"
    description = "Search the web via the agent's SearxNG instance. Returns title/url/snippet (no fetch)."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "max_results": {"type": "integer", "description": "Cap on results (default 5, max 10)."},
        },
        "required": ["query"],
    }
    requires = ("websearch",)

    async def run(self, args, ctx):
        query = (args.get("query") or "").strip()
        if not query:
            return "Error: 'query' parameter is required."
        max_results = max(1, min(int(args.get("max_results", 5)), 10))
        try:
            results = await ctx.agent.websearch.search(query)
        except Exception as exc:
            return f"Search error: {exc}"
        results = results[:max_results]
        if not results:
            return f"No web results for: {query}"
        lines = [f"Found {len(results)} result(s) for '{query}':"]
        for r in results:
            lines.append(f"  {r.title}")
            lines.append(f"    {r.url}")
            snippet = (r.snippet or "").strip().replace("\n", " ")[:200]
            if snippet:
                lines.append(f"    {snippet}")
        return "\n".join(lines)


class UpdateScratch(Tool):
    name = "update_scratch"
    description = "Replace the scratchpad for the current channel. Persisted across turns; capped per channels config."
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Replacement scratchpad content."},
        },
        "required": ["text"],
    }
    requires = ("channels",)

    async def run(self, args, ctx):
        text = args.get("text", "")
        try:
            sp = ctx.agent.channels.scratchpad(ctx.channel_id)
            sp.write(text)
        except Exception as exc:
            return f"Scratchpad error: {exc}"
        return "Scratchpad updated."


class AddLearning(Tool):
    name = "add_learning"
    description = "Capture a durable lesson as a learning candidate. Title + body."
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short title."},
            "body": {"type": "string", "description": "1-3 sentence body."},
        },
        "required": ["title", "body"],
    }
    requires = ("learning",)

    async def run(self, args, ctx):
        title = (args.get("title") or "").strip()
        body = (args.get("body") or "").strip()
        if not title or not body:
            return "Error: 'title' and 'body' are required."
        try:
            cid = ctx.agent.learning.add_candidate(title=title, body=body)
        except Exception as exc:
            return f"Learning error: {exc}"
        return f"Added learning: {cid}"
```

> **Note:** the exact method names on `RetrievalClient` (`query` here), `LearningManager` (`add_candidate`), and the `fetcher` module (`fetch_and_extract`) may differ. Read the actual modules in `agent_core/agent_core/retrieval.py`, `agent_core/agent_core/learning.py`, and `agent_core/agent_core/utils/fetcher.py` and adjust the call sites and tests to match. Keep the test shape — call site + return shape — even if names need adjustment.

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_tools_framework.py -v
```

Expected: 6 passed (after adjusting call site names if needed).

- [ ] **Step 5: Commit**

```bash
git add agent_core/tools/_framework.py tests/test_tools_framework.py
git commit -m "feat(tools): add framework-backed builtin tools (fetch_url, search_vault, search_web, update_scratch, add_learning)"
```

---

## Task 11: Wire up `BUILTIN_TOOLS`

**Files:**
- Modify: `agent_core/agent_core/tools/builtin.py`
- Test: `agent_core/tests/test_builtin_tools.py`

- [ ] **Step 1: Write the failing test**

Create `agent_core/tests/test_builtin_tools.py`:

```python
"""Test that BUILTIN_TOOLS is correctly populated."""
from agent_core.tools.builtin import BUILTIN_TOOLS


def test_builtin_tools_count():
    assert len(BUILTIN_TOOLS) == 12


def test_builtin_tools_names():
    names = {t.name for t in BUILTIN_TOOLS}
    expected = {
        "cat", "head", "tail", "ls", "grep", "find", "read_lines",  # shell
        "fetch_url", "search_vault", "search_web", "update_scratch", "add_learning",  # framework-backed
    }
    assert names == expected


def test_builtin_tools_unique_names():
    names = [t.name for t in BUILTIN_TOOLS]
    assert len(names) == len(set(names))
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_builtin_tools.py -v
```

Expected: 3 failed (BUILTIN_TOOLS empty).

- [ ] **Step 3: Populate `BUILTIN_TOOLS`**

Replace `agent_core/agent_core/tools/builtin.py`:

```python
"""Builtin tools shipped with agent_core.

Seven read-only shell-style tools (vault-scoped, pure-Python) plus five
tools backed by framework managers already wired by run_daemon. All can be
opted out via the agent's `disabled_builtins` ClassVar.
"""
from agent_core.tools.base import Tool
from agent_core.tools._framework import (
    AddLearning,
    FetchUrl,
    SearchVault,
    SearchWeb,
    UpdateScratch,
)
from agent_core.tools._shell import Cat, Find, Grep, Head, Ls, ReadLines, Tail


BUILTIN_TOOLS: list[type[Tool]] = [
    Cat, Head, Tail, Ls, Grep, Find, ReadLines,
    FetchUrl, SearchVault, SearchWeb, UpdateScratch, AddLearning,
]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_builtin_tools.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add agent_core/tools/builtin.py tests/test_builtin_tools.py
git commit -m "feat(tools): wire up BUILTIN_TOOLS list (12 tools)"
```

---

## Task 12: Add `agent_core.commands.base.Command` + `CommandRegistry`

**Files:**
- Create: `agent_core/agent_core/commands/base.py`, `agent_core/agent_core/commands/registry.py`, `agent_core/agent_core/commands/builtin.py` (placeholder), `agent_core/agent_core/commands/__init__.py`
- Test: `agent_core/tests/test_commands_base.py`, `agent_core/tests/test_commands_registry.py`

- [ ] **Step 1: Write the failing test**

Create `agent_core/tests/test_commands_base.py`:

```python
"""Tests for Command base class."""
from agent_core.commands.base import Command


def test_command_subclass_inherits_classvars():
    class Help(Command):
        name = "help"
        args = ""
        description = "Show help"

    assert Help.name == "help"
    assert Help.args == ""
    assert Help.description == "Show help"
    assert Help.requires == ()
```

Create `agent_core/tests/test_commands_registry.py`:

```python
"""Tests for CommandRegistry."""
import asyncio
from typing import AsyncIterator

import pytest

from agent_core.commands.base import Command
from agent_core.commands.registry import CommandRegistry
from agent_core.protocol.messages import ResponseMessage


class _Hello(Command):
    name = "hello"
    args = "[<name>]"
    description = "Say hello"

    async def run(self, raw_args, ctx) -> AsyncIterator:
        target = raw_args.strip() or "world"
        yield ResponseMessage(text=f"hi {target}")


class _Multi(Command):
    name = "multi"
    args = ""
    description = "Yields multiple"

    async def run(self, raw_args, ctx) -> AsyncIterator:
        yield ResponseMessage(text="one")
        yield ResponseMessage(text="two")
        yield ResponseMessage(text="three")


class _NeedsCompiler(Command):
    name = "needs_compiler"
    args = ""
    description = "Requires compiler"
    requires = ("compiler",)

    async def run(self, raw_args, ctx) -> AsyncIterator:
        yield ResponseMessage(text=f"compiler: {ctx.agent.compiler}")


class _StubAgent:
    pass


async def _collect(it):
    out = []
    async for m in it:
        out.append(m)
    return out


def test_dispatch_known_command():
    registry = CommandRegistry({"hello": _Hello()})
    msgs = asyncio.run(_collect(registry.dispatch("hello", "PAL", ctx=None)))
    assert len(msgs) == 1
    assert msgs[0].text == "hi PAL"


def test_dispatch_yields_multiple_messages():
    registry = CommandRegistry({"multi": _Multi()})
    msgs = asyncio.run(_collect(registry.dispatch("multi", "", ctx=None)))
    assert [m.text for m in msgs] == ["one", "two", "three"]


def test_dispatch_unknown_command_yields_response():
    registry = CommandRegistry({})
    msgs = asyncio.run(_collect(registry.dispatch("nope", "", ctx=None)))
    assert len(msgs) == 1
    assert isinstance(msgs[0], ResponseMessage)
    assert "unknown" in msgs[0].text.lower()


def test_metadata_returns_tuples():
    registry = CommandRegistry({"hello": _Hello()})
    assert registry.metadata() == [("hello", "[<name>]", "Say hello")]


def test_metadata_preserves_registration_order():
    class A(Command):
        name = "a"; args = ""; description = ""
        async def run(self, raw_args, ctx): yield None
    class B(Command):
        name = "b"; args = ""; description = ""
        async def run(self, raw_args, ctx): yield None
    class C(Command):
        name = "c"; args = ""; description = ""
        async def run(self, raw_args, ctx): yield None
    agent = _StubAgent()
    from agent_core.commands import builtin as b_mod
    saved = b_mod.BUILTIN_COMMANDS
    b_mod.BUILTIN_COMMANDS = []
    try:
        registry = CommandRegistry.build(agent, [A, B, C])
        assert [m[0] for m in registry.metadata()] == ["a", "b", "c"]
    finally:
        b_mod.BUILTIN_COMMANDS = saved


def test_build_validates_requires():
    agent = _StubAgent()
    with pytest.raises(RuntimeError, match="needs_compiler.*compiler"):
        CommandRegistry.build(agent, [_NeedsCompiler])


def test_build_excludes_disabled():
    agent = _StubAgent()
    registry = CommandRegistry.build(agent, [_Hello], disabled=frozenset({"hello"}))
    assert registry.metadata() == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_commands_base.py tests/test_commands_registry.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write minimal implementation**

Create `agent_core/agent_core/commands/base.py`:

```python
"""Command base class.

Commands are user-typed slash commands. Each Command class declares its
`name`, `args` (template string for /help, e.g. "<title>"), `description`,
and optional `requires`. The `run` method takes a raw string arg and yields
zero or more messages.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from agent_core.agent import HandlerContext


class Command:
    name: ClassVar[str]
    args: ClassVar[str]
    description: ClassVar[str]
    requires: ClassVar[tuple[str, ...]] = ()

    async def run(self, raw_args: str, ctx: "HandlerContext") -> AsyncIterator:
        raise NotImplementedError
        yield   # makes this an async generator
```

Create placeholder `agent_core/agent_core/commands/builtin.py`:

```python
"""Builtin command list. Populated as commands are added in subsequent tasks."""
from agent_core.commands.base import Command

BUILTIN_COMMANDS: list[type[Command]] = []
```

Create `agent_core/agent_core/commands/registry.py`:

```python
"""Command registry: registration + dispatch.

Mirror of ToolExecutor. `build()` validates requires and instantiates
commands. `dispatch()` looks up by name and yields the command's messages.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from agent_core.commands.base import Command
from agent_core.commands.builtin import BUILTIN_COMMANDS
from agent_core.protocol.messages import ResponseMessage

if TYPE_CHECKING:
    from agent_core.agent import HandlerContext


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
        all_classes = [
            c for c in BUILTIN_COMMANDS + list(agent_command_classes) if c.name not in disabled
        ]
        instances: dict[str, Command] = {}
        for cmd_cls in all_classes:
            for attr in cmd_cls.requires:
                if not hasattr(agent, attr):
                    raise RuntimeError(
                        f"Command {cmd_cls.name!r} requires agent.{attr!r}, "
                        f"but {type(agent).__name__} has no such attribute. "
                        f"Set it in setup() or add {cmd_cls.name!r} to disabled_builtins."
                    )
            instances[cmd_cls.name] = cmd_cls()
        return cls(instances)

    async def dispatch(self, name: str, raw_args: str, ctx: "HandlerContext"):
        command = self._commands.get(name)
        if command is None:
            yield ResponseMessage(text=f"Unknown command: {name}")
            return
        async for msg in command.run(raw_args, ctx):
            yield msg

    def metadata(self) -> list[tuple[str, str, str]]:
        return [
            (type(c).name, type(c).args, type(c).description)
            for c in self._commands.values()
        ]

    def names(self) -> list[str]:
        return list(self._commands)
```

Create `agent_core/agent_core/commands/__init__.py`:

```python
"""Commands registry, base class, builtins."""
from agent_core.commands.base import Command
from agent_core.commands.registry import CommandRegistry

__all__ = ["Command", "CommandRegistry"]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_commands_base.py tests/test_commands_registry.py -v
```

Expected: 1 + 7 = 8 passed.

> **Note:** `ResponseMessage` may have a different field name (e.g. `body` instead of `text`) in `agent_core.protocol.messages`. Read the actual definition and adjust the tests + registry implementation. Keep semantics: a single text-bearing response message.

- [ ] **Step 5: Commit**

```bash
git add agent_core/commands/ tests/test_commands_base.py tests/test_commands_registry.py
git commit -m "feat(commands): add Command base and CommandRegistry"
```

---

## Task 13: Add the 12 builtin command implementations

**Files:**
- Create: `agent_core/agent_core/commands/_builtin_impls.py`
- Test: `agent_core/tests/test_builtin_commands.py`

This task lifts the simple wrappers from PAL's `_handle_X` methods into Command subclasses. Twelve commands; each is small.

- [ ] **Step 1: Write the failing test**

Create `agent_core/tests/test_builtin_commands.py`:

```python
"""Smoke tests for builtin commands. Each test verifies the command yields
at least one message and reads from the right manager.
"""
import asyncio
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_core.commands._builtin_impls import (
    Clear, Help, Learnings, Model, Profile, Promote, Quit, Rate, Scratch,
    Status, Think, Wisdom,
)


async def _collect(it):
    return [m async for m in it]


def _ctx(agent, channel_id="default"):
    class _C: pass
    c = _C(); c.agent = agent; c.channel_id = channel_id; c.conversation = MagicMock(); return c


def test_help_lists_commands():
    cr = MagicMock()
    cr.metadata.return_value = [("hello", "", "Say hi"), ("quit", "", "Exit")]
    agent = MagicMock(command_registry=cr)
    msgs = asyncio.run(_collect(Help().run("", _ctx(agent))))
    assert len(msgs) >= 1
    body = "\n".join(getattr(m, "text", "") for m in msgs)
    assert "hello" in body and "Say hi" in body and "quit" in body


def test_clear_resets_conversation():
    conv = MagicMock()
    channels = MagicMock()
    channels.conversation.return_value = conv
    agent = MagicMock(channels=channels)
    msgs = asyncio.run(_collect(Clear().run("", _ctx(agent, channel_id="c1"))))
    conv.clear.assert_called_once()


def test_status_shows_basic_info():
    cfg = MagicMock(model="m1", vault_path="/v")
    agent = MagicMock(config=cfg, name="pal")
    msgs = asyncio.run(_collect(Status().run("", _ctx(agent))))
    body = "\n".join(getattr(m, "text", "") for m in msgs)
    assert "m1" in body or "pal" in body


def test_profile_read():
    profile = MagicMock()
    profile.read.return_value = "I am a user."
    agent = MagicMock(profile=profile)
    msgs = asyncio.run(_collect(Profile().run("", _ctx(agent))))
    body = "\n".join(getattr(m, "text", "") for m in msgs)
    assert "I am a user." in body


def test_scratch_read_default():
    sp = MagicMock()
    sp.read.return_value = "scratch contents"
    channels = MagicMock()
    channels.scratchpad.return_value = sp
    agent = MagicMock(channels=channels)
    msgs = asyncio.run(_collect(Scratch().run("", _ctx(agent, channel_id="c1"))))
    body = "\n".join(getattr(m, "text", "") for m in msgs)
    assert "scratch contents" in body


def test_wisdom_list():
    wisdom = MagicMock()
    wisdom.list.return_value = [{"id": "w1", "body": "Wisdom one."}]
    agent = MagicMock(wisdom=wisdom)
    msgs = asyncio.run(_collect(Wisdom().run("", _ctx(agent))))
    body = "\n".join(getattr(m, "text", "") for m in msgs)
    assert "w1" in body and "Wisdom one." in body


def test_learnings_list():
    learning = MagicMock()
    learning.list_candidates.return_value = [{"id": "l1", "title": "T", "body": "B"}]
    agent = MagicMock(learning=learning)
    msgs = asyncio.run(_collect(Learnings().run("", _ctx(agent))))
    body = "\n".join(getattr(m, "text", "") for m in msgs)
    assert "l1" in body


def test_promote():
    learning = MagicMock()
    learning.get_candidate.return_value = {"id": "l1", "title": "T", "body": "B"}
    wisdom = MagicMock()
    wisdom.add.return_value = "w1"
    agent = MagicMock(learning=learning, wisdom=wisdom)
    msgs = asyncio.run(_collect(Promote().run("l1", _ctx(agent))))
    wisdom.add.assert_called_once()


def test_rate():
    learning = MagicMock()
    agent = MagicMock(learning=learning)
    msgs = asyncio.run(_collect(Rate().run("l1 5", _ctx(agent))))
    learning.rate.assert_called_once_with("l1", 5)


def test_model_show():
    inf = MagicMock(model="model-a")
    agent = MagicMock(inference=inf)
    msgs = asyncio.run(_collect(Model().run("", _ctx(agent))))
    body = "\n".join(getattr(m, "text", "") for m in msgs)
    assert "model-a" in body


def test_think_set_mode():
    conv = MagicMock()
    conv.overrides = {}
    channels = MagicMock()
    channels.conversation.return_value = conv
    agent = MagicMock(channels=channels)
    msgs = asyncio.run(_collect(Think().run("on", _ctx(agent, channel_id="c1"))))
    assert conv.overrides.get("reasoning") == "on"


def test_quit_yields_response():
    agent = MagicMock()
    msgs = asyncio.run(_collect(Quit().run("", _ctx(agent))))
    assert len(msgs) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_builtin_commands.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write the implementations**

Create `agent_core/agent_core/commands/_builtin_impls.py`:

```python
"""Implementations of the 12 builtin commands.

Each command is a thin wrapper over a framework manager. Commands that need
state across turns (Help reading the registry, Clear resetting the channel
conversation, etc.) read from `ctx.agent.X`.
"""
from __future__ import annotations

from typing import AsyncIterator

from agent_core.commands.base import Command
from agent_core.protocol.messages import ResponseMessage


class Help(Command):
    name = "help"
    args = ""
    description = "Show this message"
    requires = ("command_registry",)

    async def run(self, raw_args, ctx) -> AsyncIterator:
        lines = ["Available commands:"]
        for name, args, desc in ctx.agent.command_registry.metadata():
            lines.append(f"  /{name} {args}".rstrip() + f"  -  {desc}")
        yield ResponseMessage(text="\n".join(lines))


class Clear(Command):
    name = "clear"
    args = ""
    description = "Reset the current channel's conversation"
    requires = ("channels",)

    async def run(self, raw_args, ctx) -> AsyncIterator:
        conv = ctx.agent.channels.conversation(ctx.channel_id)
        conv.clear()
        yield ResponseMessage(text="Conversation cleared.")


class Status(Command):
    name = "status"
    args = ""
    description = "Show daemon status"
    requires = ("config",)

    async def run(self, raw_args, ctx) -> AsyncIterator:
        cfg = ctx.agent.config
        agent_name = getattr(ctx.agent, "name", "agent")
        lines = [
            f"agent: {agent_name}",
            f"model: {getattr(cfg, 'model', '?')}",
            f"vault: {getattr(cfg, 'vault_path', '?')}",
        ]
        yield ResponseMessage(text="\n".join(lines))


class Profile(Command):
    name = "profile"
    args = ""
    description = "Show the user profile"
    requires = ("profile",)

    async def run(self, raw_args, ctx) -> AsyncIterator:
        body = ctx.agent.profile.read() or "(empty)"
        yield ResponseMessage(text=body)


class Scratch(Command):
    name = "scratch"
    args = "[<text> | clear | read]"
    description = "Manage the channel's scratchpad. No args = read; 'clear' empties; otherwise text appended."
    requires = ("channels",)

    async def run(self, raw_args, ctx) -> AsyncIterator:
        sp = ctx.agent.channels.scratchpad(ctx.channel_id)
        arg = raw_args.strip()
        if not arg or arg == "read":
            yield ResponseMessage(text=sp.read() or "(empty)")
            return
        if arg == "clear":
            sp.write("")
            yield ResponseMessage(text="Scratchpad cleared.")
            return
        # Otherwise: append a timestamped note
        from datetime import datetime, timezone
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        existing = sp.read() or ""
        new = (existing + ("\n" if existing else "") + f"[{ts}] {arg}").strip()
        sp.write(new)
        yield ResponseMessage(text="Scratchpad updated.")


class Wisdom(Command):
    name = "wisdom"
    args = "[add <text> | remove <id>]"
    description = "List/add/remove wisdom entries"
    requires = ("wisdom",)

    async def run(self, raw_args, ctx) -> AsyncIterator:
        arg = raw_args.strip()
        if arg.startswith("add "):
            body = arg[4:].strip()
            if not body:
                yield ResponseMessage(text="Usage: /wisdom add <text>")
                return
            new_id = ctx.agent.wisdom.add(body)
            yield ResponseMessage(text=f"Added wisdom {new_id}.")
            return
        if arg.startswith("remove "):
            wid = arg[7:].strip()
            ctx.agent.wisdom.remove(wid)
            yield ResponseMessage(text=f"Removed {wid}.")
            return
        # List
        entries = ctx.agent.wisdom.list()
        if not entries:
            yield ResponseMessage(text="(no wisdom)")
            return
        lines = []
        for e in entries:
            wid = e.get("id", "?") if isinstance(e, dict) else getattr(e, "id", "?")
            body = e.get("body", "") if isinstance(e, dict) else getattr(e, "body", "")
            lines.append(f"  {wid}: {body}")
        yield ResponseMessage(text="\n".join(lines))


class Learnings(Command):
    name = "learnings"
    args = ""
    description = "List captured learning candidates"
    requires = ("learning",)

    async def run(self, raw_args, ctx) -> AsyncIterator:
        candidates = ctx.agent.learning.list_candidates()
        if not candidates:
            yield ResponseMessage(text="(no learning candidates)")
            return
        lines = []
        for c in candidates:
            cid = c.get("id", "?") if isinstance(c, dict) else getattr(c, "id", "?")
            title = c.get("title", "") if isinstance(c, dict) else getattr(c, "title", "")
            lines.append(f"  {cid}: {title}")
        yield ResponseMessage(text="\n".join(lines))


class Promote(Command):
    name = "promote"
    args = "<id>"
    description = "Promote a learning candidate to wisdom"
    requires = ("learning", "wisdom")

    async def run(self, raw_args, ctx) -> AsyncIterator:
        cid = raw_args.strip()
        if not cid:
            yield ResponseMessage(text="Usage: /promote <id>")
            return
        cand = ctx.agent.learning.get_candidate(cid)
        if not cand:
            yield ResponseMessage(text=f"Candidate {cid} not found.")
            return
        body = cand.get("body") if isinstance(cand, dict) else getattr(cand, "body", "")
        wid = ctx.agent.wisdom.add(body)
        yield ResponseMessage(text=f"Promoted {cid} -> wisdom {wid}.")


class Rate(Command):
    name = "rate"
    args = "<id> <1-5>"
    description = "Rate a learning candidate"
    requires = ("learning",)

    async def run(self, raw_args, ctx) -> AsyncIterator:
        parts = raw_args.split()
        if len(parts) != 2:
            yield ResponseMessage(text="Usage: /rate <id> <1-5>")
            return
        cid, score_str = parts
        try:
            score = int(score_str)
        except ValueError:
            yield ResponseMessage(text="Score must be an integer 1-5.")
            return
        if not 1 <= score <= 5:
            yield ResponseMessage(text="Score must be 1-5.")
            return
        ctx.agent.learning.rate(cid, score)
        yield ResponseMessage(text=f"Rated {cid} = {score}.")


class Model(Command):
    name = "model"
    args = "[<name>]"
    description = "Show or switch active model"
    requires = ("inference",)

    async def run(self, raw_args, ctx) -> AsyncIterator:
        target = raw_args.strip()
        if not target:
            current = getattr(ctx.agent.inference, "model", "?")
            yield ResponseMessage(text=f"model: {current}")
            return
        ctx.agent.inference.model = target
        yield ResponseMessage(text=f"model: {target}")


class Think(Command):
    name = "think"
    args = "[on | off | auto | show | hide]"
    description = "Control reasoning mode for this channel"
    requires = ("channels",)

    async def run(self, raw_args, ctx) -> AsyncIterator:
        mode = raw_args.strip().lower()
        valid = {"on", "off", "auto", "show", "hide"}
        if not mode:
            conv = ctx.agent.channels.conversation(ctx.channel_id)
            current = (conv.overrides or {}).get("reasoning", "auto")
            yield ResponseMessage(text=f"reasoning: {current}")
            return
        if mode not in valid:
            yield ResponseMessage(text=f"Usage: /think [{' | '.join(sorted(valid))}]")
            return
        conv = ctx.agent.channels.conversation(ctx.channel_id)
        if not conv.overrides:
            conv.overrides = {}
        conv.overrides["reasoning"] = mode
        yield ResponseMessage(text=f"reasoning: {mode}")


class Quit(Command):
    name = "quit"
    args = ""
    description = "End the session"

    async def run(self, raw_args, ctx) -> AsyncIterator:
        yield ResponseMessage(text="Goodbye.")
```

> **Note:** Manager method names (`wisdom.add`, `wisdom.remove`, `wisdom.list`, `learning.list_candidates`, `learning.get_candidate`, `learning.rate`) are best guesses based on PAL's existing usage. Verify against the actual `agent_core.wisdom`, `agent_core.learning`, and `agent_core.channels` interfaces and adjust both the implementations and the tests to match. The shape — a Command class wrapping a single manager call — is what matters.

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_builtin_commands.py -v
```

Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add agent_core/commands/_builtin_impls.py tests/test_builtin_commands.py
git commit -m "feat(commands): add 12 builtin command implementations"
```

---

## Task 14: Wire up `BUILTIN_COMMANDS`

**Files:**
- Modify: `agent_core/agent_core/commands/builtin.py`
- Test: `agent_core/tests/test_builtin_commands_list.py`

- [ ] **Step 1: Write the failing test**

Create `agent_core/tests/test_builtin_commands_list.py`:

```python
"""Test that BUILTIN_COMMANDS is correctly populated."""
from agent_core.commands.builtin import BUILTIN_COMMANDS


def test_builtin_commands_count():
    assert len(BUILTIN_COMMANDS) == 12


def test_builtin_commands_names():
    names = {c.name for c in BUILTIN_COMMANDS}
    expected = {
        "help", "clear", "status", "profile", "scratch", "wisdom",
        "learnings", "promote", "rate", "model", "think", "quit",
    }
    assert names == expected


def test_builtin_commands_unique():
    names = [c.name for c in BUILTIN_COMMANDS]
    assert len(names) == len(set(names))
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_builtin_commands_list.py -v
```

Expected: 3 failed.

- [ ] **Step 3: Populate BUILTIN_COMMANDS**

Replace `agent_core/agent_core/commands/builtin.py`:

```python
"""Builtin commands shipped with agent_core."""
from agent_core.commands._builtin_impls import (
    Clear, Help, Learnings, Model, Profile, Promote, Quit, Rate, Scratch,
    Status, Think, Wisdom,
)
from agent_core.commands.base import Command


BUILTIN_COMMANDS: list[type[Command]] = [
    Help, Clear, Status, Profile, Scratch, Wisdom,
    Learnings, Promote, Rate, Model, Think, Quit,
]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_builtin_commands_list.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add agent_core/commands/builtin.py tests/test_builtin_commands_list.py
git commit -m "feat(commands): wire up BUILTIN_COMMANDS list (12 commands)"
```

---

## Task 15: Add `agent_core.prompts.builder.SystemPromptBuilder`

**Files:**
- Create: `agent_core/agent_core/prompts/builder.py`, `agent_core/agent_core/prompts/__init__.py`
- Test: `agent_core/tests/test_prompts.py`

- [ ] **Step 1: Write the failing test**

Create `agent_core/tests/test_prompts.py`:

```python
"""Tests for SystemPromptBuilder render helpers."""
from unittest.mock import MagicMock

from agent_core.prompts.builder import SystemPromptBuilder


def _builder(profile=None, wisdom=None, channels=None, tool_executor=None, command_registry=None):
    return SystemPromptBuilder(
        profile=profile or MagicMock(),
        wisdom=wisdom or MagicMock(),
        channels=channels or MagicMock(),
        tool_executor=tool_executor or MagicMock(),
        command_registry=command_registry or MagicMock(),
    )


def test_render_profile_empty():
    profile = MagicMock(); profile.read.return_value = ""
    assert _builder(profile=profile).render_profile() == ""


def test_render_profile_populated():
    profile = MagicMock(); profile.read.return_value = "I am Shane."
    out = _builder(profile=profile).render_profile()
    assert "## About the User" in out and "I am Shane." in out


def test_render_wisdom_empty():
    wisdom = MagicMock(); wisdom.bodies.return_value = []
    assert _builder(wisdom=wisdom).render_wisdom() == ""


def test_render_wisdom_populated():
    wisdom = MagicMock(); wisdom.bodies.return_value = ["W1.", "W2."]
    out = _builder(wisdom=wisdom).render_wisdom()
    assert "## Active Wisdom" in out and "- W1." in out and "- W2." in out


def test_render_scratchpad_empty():
    sp = MagicMock(); sp.read.return_value = ""
    channels = MagicMock(); channels.scratchpad.return_value = sp
    assert _builder(channels=channels).render_scratchpad("c1") == ""


def test_render_scratchpad_populated():
    sp = MagicMock(); sp.read.return_value = "scratch contents"
    channels = MagicMock(); channels.scratchpad.return_value = sp
    out = _builder(channels=channels).render_scratchpad("c1")
    assert "## Channel Scratchpad" in out and "scratch contents" in out


def test_render_commands_catalog():
    cr = MagicMock()
    cr.metadata.return_value = [("hello", "[<name>]", "Say hi"), ("quit", "", "Exit")]
    out = _builder(command_registry=cr).render_commands_catalog()
    assert "## Available Commands" in out
    assert "/hello [<name>]" in out and "Say hi" in out
    assert "/quit" in out and "Exit" in out


def test_render_commands_catalog_preserves_order():
    cr = MagicMock()
    cr.metadata.return_value = [("a", "", ""), ("b", "", ""), ("c", "", "")]
    out = _builder(command_registry=cr).render_commands_catalog()
    a_pos = out.index("/a")
    b_pos = out.index("/b")
    c_pos = out.index("/c")
    assert a_pos < b_pos < c_pos


def test_render_tools_catalog():
    te = MagicMock()
    te.schemas.return_value = [
        {"type": "function", "function": {"name": "cat", "description": "Read file", "parameters": {}}},
        {"type": "function", "function": {"name": "grep", "description": "Search files", "parameters": {}}},
    ]
    out = _builder(tool_executor=te).render_tools_catalog()
    assert "## Available Tools" in out
    assert "`cat`" in out and "Read file" in out
    assert "`grep`" in out and "Search files" in out


def test_render_tools_catalog_empty():
    te = MagicMock(); te.schemas.return_value = []
    assert _builder(tool_executor=te).render_tools_catalog() == ""


def test_render_commands_catalog_empty():
    cr = MagicMock(); cr.metadata.return_value = []
    assert _builder(command_registry=cr).render_commands_catalog() == ""
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_prompts.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write the implementation**

Create `agent_core/agent_core/prompts/builder.py`:

```python
"""SystemPromptBuilder: composable system-prompt section helpers.

The builder provides `render_*` methods, one per standard section. Agents
assemble their full system prompt by calling whichever they want in
whichever order from `Agent.system_prompt(ctx)`. Each render method returns
an empty string when its data is empty, so consumers can `filter(None, ...)`
freely.
"""
from __future__ import annotations


class SystemPromptBuilder:
    def __init__(self, profile, wisdom, channels, tool_executor, command_registry) -> None:
        self.profile = profile
        self.wisdom = wisdom
        self.channels = channels
        self.tool_executor = tool_executor
        self.command_registry = command_registry

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
        sp = self.channels.scratchpad(channel_id)
        body = sp.read()
        return f"## Channel Scratchpad\n\n{body}" if body else ""

    def render_commands_catalog(self) -> str:
        meta = self.command_registry.metadata()
        if not meta:
            return ""
        lines = []
        for name, args, desc in meta:
            lines.append(f"- `/{name} {args}`".rstrip() + f" - {desc}")
        return "## Available Commands\n\n" + "\n".join(lines)

    def render_tools_catalog(self) -> str:
        schemas = self.tool_executor.schemas()
        if not schemas:
            return ""
        lines = []
        for s in schemas:
            f = s["function"]
            lines.append(f"- `{f['name']}` - {f['description']}")
        return "## Available Tools\n\n" + "\n".join(lines)
```

Create `agent_core/agent_core/prompts/__init__.py`:

```python
"""Prompt builder."""
from agent_core.prompts.builder import SystemPromptBuilder

__all__ = ["SystemPromptBuilder"]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_prompts.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add agent_core/prompts/ tests/test_prompts.py
git commit -m "feat(prompts): add SystemPromptBuilder with section render helpers"
```

---

## Task 16: Extend `Agent` with ClassVars and `HandlerContext` with `agent` + `emit`

**Files:**
- Modify: `agent_core/agent_core/agent.py`
- Test: extend `agent_core/tests/test_agent.py`

- [ ] **Step 1: Read the existing Agent and HandlerContext**

```bash
sed -n '1,100p' agent_core/agent.py
```

- [ ] **Step 2: Write the failing test**

Append to `agent_core/tests/test_agent.py`:

```python
def test_agent_classvars_default():
    from agent_core.agent import Agent
    assert Agent.tools == []
    assert Agent.commands == []
    assert Agent.disabled_builtins == frozenset()


def test_agent_classvars_subclass():
    from agent_core.agent import Agent
    from agent_core.tools.base import Tool
    from agent_core.commands.base import Command

    class T1(Tool):
        name = "t1"; description = ""; parameters = {}
        async def run(self, args, ctx): return ""
    class C1(Command):
        name = "c1"; args = ""; description = ""
        async def run(self, raw_args, ctx): yield None

    class MyAgent(Agent):
        name = "myagent"
        tools = [T1]
        commands = [C1]
        disabled_builtins = frozenset({"grep"})

    assert MyAgent.tools == [T1]
    assert MyAgent.commands == [C1]
    assert MyAgent.disabled_builtins == frozenset({"grep"})


def test_handler_context_has_agent_and_emit():
    from agent_core.agent import HandlerContext
    fields = {f.name for f in HandlerContext.__dataclass_fields__.values()}
    assert "agent" in fields
    assert "emit" in fields
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_agent.py -v -k "classvars_default or classvars_subclass or handler_context_has_agent"
```

Expected: 3 failed (attributes don't exist yet).

- [ ] **Step 4: Modify `agent_core/agent_core/agent.py`**

Add three ClassVars to the `Agent` class (place after `env_prefix`):

```python
    # NEW in v0.6.0
    tools: ClassVar[list[type["Tool"]]] = []
    commands: ClassVar[list[type["Command"]]] = []
    disabled_builtins: ClassVar[frozenset[str]] = frozenset()
```

Add `TYPE_CHECKING` imports at the top of the file:

```python
if TYPE_CHECKING:
    from agent_core.tools.base import Tool
    from agent_core.commands.base import Command
```

Modify the `HandlerContext` dataclass to add two fields. Since these are used everywhere a HandlerContext is constructed, and the existing fields are positional, give the new fields defaults (a sentinel) so existing call sites don't break. Then update construction sites in Task 18.

```python
@dataclass
class HandlerContext:
    conversation: Conversation
    channel_id: str
    writer: object
    agent: object = None        # NEW: back-reference for tools accessing ctx.agent.X
    emit: object = None         # NEW: Callable[[object], Awaitable[None]]; None until populated
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_agent.py -v
```

Expected: all green; the three new tests pass.

- [ ] **Step 6: Commit**

```bash
git add agent_core/agent.py tests/test_agent.py
git commit -m "feat(agent): add tools/commands/disabled_builtins ClassVars; extend HandlerContext with agent + emit"
```

---

## Task 17: Update `run_daemon` to wire executor / registry / prompt_builder

**Files:**
- Modify: `agent_core/agent_core/runtime.py`
- Test: extend `agent_core/tests/test_runtime.py`

- [ ] **Step 1: Read the existing run_daemon**

```bash
sed -n '1,200p' agent_core/runtime.py
```

- [ ] **Step 2: Write the failing test**

Append to `agent_core/tests/test_runtime.py`:

```python
def test_run_daemon_attaches_executor_and_registry():
    """run_daemon constructs ToolExecutor, CommandRegistry, SystemPromptBuilder
    and attaches them to the agent before setup() is called."""
    from agent_core.agent import Agent
    from agent_core.tools.base import Tool

    class _Probe(Tool):
        name = "probe"; description = ""; parameters = {}
        async def run(self, args, ctx): return "ok"

    captured = {}

    class _ProbeAgent(Agent):
        name = "probe-agent"
        tools = [_Probe]

        def setup(self):
            captured["has_executor"] = hasattr(self, "tool_executor")
            captured["has_registry"] = hasattr(self, "command_registry")
            captured["has_prompt_builder"] = hasattr(self, "prompt_builder")
            captured["probe_in_executor"] = "probe" in self.tool_executor.names()

    # Use the existing test harness for run_daemon (likely a fixture in conftest
    # that mocks framework managers and skips the actual socket listen). Match
    # the pattern used by other test_runtime.py tests.
    # Pseudocode:
    # _run_daemon_under_test(_ProbeAgent())
    # assert captured["has_executor"]
    # assert captured["has_registry"]
    # assert captured["has_prompt_builder"]
    # assert captured["probe_in_executor"]


def test_run_daemon_fails_when_required_attr_missing():
    """If a tool requires an attr the agent doesn't have, run_daemon raises
    inside the registration phase, before setup() is called."""
    from agent_core.agent import Agent
    from agent_core.tools.base import Tool

    class _NeedsCompiler(Tool):
        name = "needs_compiler"; description = ""; parameters = {}
        requires = ("compiler",)
        async def run(self, args, ctx): return ""

    class _BadAgent(Agent):
        name = "bad-agent"
        tools = [_NeedsCompiler]
        # Doesn't set self.compiler in setup, doesn't disable.

    import pytest
    with pytest.raises(RuntimeError, match="needs_compiler.*compiler"):
        # _run_daemon_under_test(_BadAgent())
        ...
```

> **Note:** The existing `test_runtime.py` likely has a helper (or conftest fixture) that exercises `run_daemon` without actually opening a socket. Mirror its pattern. If no such helper exists, the cleanest approach is to test the registration phase as a separate function — refactor `run_daemon` so the wiring step is `_attach_registries(agent)` and test that directly.

- [ ] **Step 3: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_runtime.py -v
```

Expected: new tests fail or are pending.

- [ ] **Step 4: Modify `runtime.py` to add the registration phase**

In `agent_core/agent_core/runtime.py`, between the existing framework-manager wiring and the call to `agent.setup()`, add:

```python
from agent_core.tools.executor import ToolExecutor
from agent_core.commands.registry import CommandRegistry
from agent_core.prompts.builder import SystemPromptBuilder


def _attach_registries(agent) -> None:
    """Build executor + registry + prompt_builder from agent's ClassVars."""
    agent.tool_executor = ToolExecutor.build(
        agent,
        list(type(agent).tools),
        disabled=type(agent).disabled_builtins,
    )
    agent.command_registry = CommandRegistry.build(
        agent,
        list(type(agent).commands),
        disabled=type(agent).disabled_builtins,
    )
    agent.prompt_builder = SystemPromptBuilder(
        profile=agent.profile,
        wisdom=agent.wisdom,
        channels=agent.channels,
        tool_executor=agent.tool_executor,
        command_registry=agent.command_registry,
    )
```

Then call `_attach_registries(agent)` in `run_daemon` after the framework managers are populated and *before* `agent.setup()` runs.

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_runtime.py -v
```

Expected: all green, including the new tests.

- [ ] **Step 6: Commit**

```bash
git add agent_core/runtime.py tests/test_runtime.py
git commit -m "feat(runtime): attach tool_executor / command_registry / prompt_builder before setup()"
```

---

## Task 18: Update `Daemon._handle_connection` to populate `ctx.agent` and `ctx.emit`

**Files:**
- Modify: `agent_core/agent_core/daemon.py`
- Test: extend `agent_core/tests/test_daemon.py`

`HandlerContext` now has `agent` and `emit` fields (Task 16). `Daemon._handle_connection` constructs HandlerContext per turn — it needs to populate them.

- [ ] **Step 1: Read the existing daemon HandlerContext construction**

```bash
grep -n "HandlerContext\|ctx = " agent_core/daemon.py
```

- [ ] **Step 2: Write the failing test**

Append to `agent_core/tests/test_daemon.py`:

```python
def test_daemon_populates_ctx_agent_and_emit(monkeypatch):
    """When the daemon dispatches a chat message, the HandlerContext passed
    to handle_chat carries `agent` and an awaitable `emit`."""
    from agent_core.agent import Agent, HandlerContext

    captured = {}

    class _ProbeAgent(Agent):
        name = "probe"
        async def handle_chat(self, msg, ctx):
            captured["agent_attr"] = ctx.agent is self
            # Use emit to send a message
            from agent_core.protocol.messages import ResponseMessage
            await ctx.emit(ResponseMessage(text="probe"))
            return
            yield  # async generator
        def system_prompt(self, ctx): return "p"

    # Use the existing test harness for Daemon (probably a fixture that simulates
    # a connection). Send a ChatMessage; assert captured["agent_attr"] is True
    # and that the emitted response was written to the connection.
    ...
```

- [ ] **Step 3: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_daemon.py -v
```

Expected: new test fails.

- [ ] **Step 4: Modify `daemon.py` to populate the two fields**

Find the HandlerContext construction site in `_handle_connection`. Add `agent=` and `emit=` to the call. The `emit` callable encodes the message as NDJSON and writes to the connection writer:

```python
from agent_core.protocol.transport import encode_message

async def _make_emit(writer):
    async def emit(message):
        writer.write(encode_message(message))
        await writer.drain()
    return emit

# in _handle_connection, where the per-turn ctx is built:
emit = await _make_emit(writer)
ctx = HandlerContext(
    conversation=conv,
    channel_id=channel_id,
    writer=writer,
    agent=self.agent,
    emit=emit,
)
```

- [ ] **Step 5: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_daemon.py -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add agent_core/daemon.py tests/test_daemon.py
git commit -m "feat(daemon): populate ctx.agent and ctx.emit per turn"
```

---

## Task 19: Add contract tests for the registration API

**Files:**
- Modify: `agent_core/tests/test_contract.py`

These tests pin the public API surface from a consumer's perspective.

- [ ] **Step 1: Append contract tests**

Append to `agent_core/tests/test_contract.py`:

```python
def test_minimal_agent_with_tools_boots():
    """An agent that lists tools = [...] gets a populated tool_executor after run_daemon's wiring step."""
    from agent_core.agent import Agent
    from agent_core.tools.base import Tool

    class _T(Tool):
        name = "probe"; description = ""; parameters = {}
        async def run(self, args, ctx): return "ok"

    class _A(Agent):
        name = "a"
        tools = [_T]

    # Use the same _attach_registries helper from runtime
    from agent_core.runtime import _attach_registries
    a = _A()
    # Stub the framework managers run_daemon would normally populate
    from unittest.mock import MagicMock
    a.profile = MagicMock(); a.wisdom = MagicMock(); a.channels = MagicMock()
    a.learning = MagicMock(); a.allowlist = MagicMock(); a.approval_registry = MagicMock()
    a.inference = MagicMock(); a.retrieval = MagicMock(); a.websearch = MagicMock()
    a.config = MagicMock()

    _attach_registries(a)
    assert "probe" in a.tool_executor.names()
    assert "help" in a.command_registry.names()  # builtin


def test_disabled_builtins_excluded_from_executor():
    from agent_core.agent import Agent
    from agent_core.runtime import _attach_registries
    from unittest.mock import MagicMock

    class _A(Agent):
        name = "a"
        disabled_builtins = frozenset({"grep"})

    a = _A()
    a.profile = MagicMock(); a.wisdom = MagicMock(); a.channels = MagicMock()
    a.learning = MagicMock(); a.allowlist = MagicMock(); a.approval_registry = MagicMock()
    a.inference = MagicMock(); a.retrieval = MagicMock(); a.websearch = MagicMock()
    a.config = MagicMock()

    _attach_registries(a)
    assert "grep" not in a.tool_executor.names()
    assert "cat" in a.tool_executor.names()  # other shell tools still present


def test_disabled_builtins_excluded_from_commands():
    from agent_core.agent import Agent
    from agent_core.runtime import _attach_registries
    from unittest.mock import MagicMock

    class _A(Agent):
        name = "a"
        disabled_builtins = frozenset({"quit"})

    a = _A()
    a.profile = MagicMock(); a.wisdom = MagicMock(); a.channels = MagicMock()
    a.learning = MagicMock(); a.allowlist = MagicMock(); a.approval_registry = MagicMock()
    a.inference = MagicMock(); a.retrieval = MagicMock(); a.websearch = MagicMock()
    a.config = MagicMock()

    _attach_registries(a)
    assert "quit" not in a.command_registry.names()
    assert "help" in a.command_registry.names()


def test_missing_dep_fails_at_attach():
    """If a tool requires an attr the agent doesn't have, _attach_registries raises."""
    import pytest
    from agent_core.agent import Agent
    from agent_core.tools.base import Tool
    from agent_core.runtime import _attach_registries
    from unittest.mock import MagicMock

    class _NeedsXyz(Tool):
        name = "needs_xyz"; description = ""; parameters = {}
        requires = ("xyz",)
        async def run(self, args, ctx): return ""

    class _A(Agent):
        name = "a"
        tools = [_NeedsXyz]

    a = _A()
    a.profile = MagicMock(); a.wisdom = MagicMock(); a.channels = MagicMock()
    a.learning = MagicMock(); a.allowlist = MagicMock(); a.approval_registry = MagicMock()
    a.inference = MagicMock(); a.retrieval = MagicMock(); a.websearch = MagicMock()
    a.config = MagicMock()
    # Don't set a.xyz

    with pytest.raises(RuntimeError, match="needs_xyz.*xyz"):
        _attach_registries(a)
```

- [ ] **Step 2: Run tests**

```bash
.venv/bin/pytest tests/test_contract.py -v
```

Expected: all green (4 new tests pass; existing pinned-API tests still pass).

- [ ] **Step 3: Commit**

```bash
git add tests/test_contract.py
git commit -m "test(contract): pin tool/command registration API"
```

---

## Task 20: Bump version, update CHANGELOG, run full suite

**Files:**
- Modify: `agent_core/pyproject.toml`, `agent_core/CHANGELOG.md`

- [ ] **Step 1: Bump version**

In `agent_core/pyproject.toml`, change `version = "0.5.1"` to `version = "0.6.0"`.

- [ ] **Step 2: Add CHANGELOG entry**

Prepend to `agent_core/CHANGELOG.md` (above the `## [0.5.1]` entry):

```markdown
## [0.6.0] - 2026-05-05

### Added
- `agent_core.tools` subpackage: `Tool` base class, `ToolExecutor` with `build()` (validates `requires` against agent attributes at registration time), 12 builtin tools (7 read-only shell tools — cat/head/tail/ls/grep/find/read_lines — and 5 framework-manager-backed tools — fetch_url/search_vault/search_web/update_scratch/add_learning).
- `agent_core.commands` subpackage: `Command` base class, `CommandRegistry` with `build()`, 12 builtin commands (help, clear, status, profile, scratch, wisdom, learnings, promote, rate, model, think, quit).
- `agent_core.prompts.builder.SystemPromptBuilder`: section render helpers (render_profile, render_wisdom, render_scratchpad, render_commands_catalog, render_tools_catalog) for agents to assemble system prompts.
- `Agent` ClassVars: `tools = []`, `commands = []`, `disabled_builtins = frozenset()` for declarative registration with opt-out by name.
- `HandlerContext` fields: `agent` (back-reference) and `emit` (NDJSON-encoding writer callback) — populated per turn by `Daemon._handle_connection`.
- `run_daemon` registration phase: builds tool_executor / command_registry / prompt_builder before `agent.setup()` runs. Misconfiguration (missing required attrs) fails fast at boot instead of at first call.

### Notes
- This release lets agents declare tools and commands as class lists and consume framework defaults out of the box. PAL adopts incrementally across seven PRs (see `docs/superpowers/plans/2026-05-05-phase-f-tool-command-prompt-scaffolding.md` in the PAL repo).
- Builtin tools are opt-out, not opt-in. An agent that doesn't want one of the framework-backed tools (e.g. `search_web`) lists its name in `disabled_builtins`.
- The `requires` validation is shallow (`hasattr`-based); type validation is deferred.
- Phase G (next) is the Discord gateway adapter extraction.
```

- [ ] **Step 3: Run full suite**

```bash
.venv/bin/pytest -q
```

Expected: all green. Compare test count to the Task 1 baseline plus the new tests added in Tasks 2-19.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore: bump agent_core to v0.6.0"
```

---

## Task 21: Push branch + open PR

- [ ] **Step 1: Push the branch**

```bash
git push -u origin feature/phase-f-tool-command-prompt-scaffolding
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --title "Phase F: tool/command/prompt scaffolding (v0.6.0)" --body "$(cat <<'EOF'
## Summary

- Adds `agent_core.tools`, `agent_core.commands`, `agent_core.prompts` subpackages for declarative tool/command registration with `requires`-validation at boot and `ctx.agent.X` access at runtime.
- Ships 12 builtin tools (7 shell-style read-only — cat/head/tail/ls/grep/find/read_lines — plus 5 framework-backed — fetch_url/search_vault/search_web/update_scratch/add_learning) and 12 builtin commands (help/clear/status/profile/scratch/wisdom/learnings/promote/rate/model/think/quit), all opt-out via `Agent.disabled_builtins`.
- Extends `HandlerContext` with `agent` and `emit` fields, wires the registration phase into `run_daemon` before `setup()` runs.

## Test plan
- [ ] `pytest -q` green on agent_core
- [ ] Contract tests cover register/disable/missing-dep paths
- [ ] PAL bumps to v0.6.0 in a follow-up PR (Phase F PR1)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Return the PR URL when complete.

---

## Task 22: Merge agent_core PR + tag v0.6.0

After review:

- [ ] **Step 1: Merge the PR**

Use the GitHub UI or `gh pr merge --squash`.

- [ ] **Step 2: Pull main and tag**

```bash
git checkout main
git pull --ff-only origin main
git tag v0.6.0
git push origin v0.6.0
```

Expected: tag visible on GitHub releases page.

---

# Part 2: PAL PR1 — Bump dep, register builtins alongside legacy executor

Working directory throughout Part 2: `/home/edible/Projects/PAL/.worktrees/phase-f-pr1`. Use `.venv/bin/pytest`.

## Task 23: Create PR1 worktree

- [ ] **Step 1: From PAL main, confirm clean state**

```bash
cd /home/edible/Projects/PAL
git status
```

Expected: clean tree on main; latest commit is the spec/plan commits from Phase F brainstorming.

- [ ] **Step 2: Create the worktree**

```bash
git worktree add .worktrees/phase-f-pr1 -b feature/phase-f-pr1-builtin-tools
cd .worktrees/phase-f-pr1
```

- [ ] **Step 3: Verify the venv is shared**

```bash
ls -la .venv 2>/dev/null && .venv/bin/pytest --collect-only -q 2>&1 | tail -3
```

If `.venv` doesn't exist in the worktree, symlink: `ln -s ../../.venv .venv`.

---

## Task 24: Bump agent_core dep, add empty `tools` and `commands` ClassVars

**Files:**
- Modify: `pyproject.toml`
- Modify: `pal/agent.py`

- [ ] **Step 1: Bump dep**

In `pyproject.toml`, change:

```
"agent_core @ git+https://github.com/EdibleTuber/agent_core.git@v0.5.1",
```

to:

```
"agent_core @ git+https://github.com/EdibleTuber/agent_core.git@v0.6.0",
```

- [ ] **Step 2: Reinstall dep**

```bash
.venv/bin/pip install --upgrade --force-reinstall --no-deps "agent_core @ git+https://github.com/EdibleTuber/agent_core.git@v0.6.0"
```

Expected: agent_core 0.6.0 installed.

- [ ] **Step 3: Add empty ClassVars to PALAgent**

In `pal/agent.py`, find the `class PALAgent(Agent):` declaration and add (just below `name = "pal"`):

```python
    tools = []          # PAL registers nothing here yet; PR2-PR4 populate.
    commands = []       # PAL registers nothing here yet; PR5 populates.
    disabled_builtins = frozenset()
```

- [ ] **Step 4: Run PAL test suite**

```bash
.venv/bin/pytest -q
```

Expected: green. The framework's builtin tools and commands are now available through `self.tool_executor` and `self.command_registry` (set up by `run_daemon`).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml pal/agent.py
git commit -m "chore: bump agent_core to v0.6.0; add empty tools/commands ClassVars"
```

---

## Task 25: Wire framework executor alongside legacy in `handle_chat`

**Files:**
- Modify: `pal/agent.py`

PR1's job is the dual-dispatch wiring: framework `tool_executor` services calls for any tool name it knows about; legacy `pal.tools.ToolExecutor` handles everything else.

- [ ] **Step 1: Read the current handle_chat tool dispatch**

```bash
grep -n "tool_executor\|run_async\|TOOL_DEFINITIONS" pal/agent.py
```

- [ ] **Step 2: Add a dual-dispatch helper**

In `pal/agent.py`, add a method on `PALAgent`:

```python
async def _run_tool(self, name: str, arguments: dict, ctx) -> str:
    """Phase F dual-dispatch: framework executor first, legacy fallback.

    Removed in PR7 (cleanup) once every PAL tool has migrated to the framework executor.
    """
    if name in self.tool_executor.names():
        return await self.tool_executor.run(name, arguments, ctx)
    # Legacy path: PAL's ToolExecutor (still owns the un-migrated tools).
    return await self.legacy_tool_executor.run_async(name, arguments)
```

Rename PAL's existing `self.tool_executor = ToolExecutor(...)` line in `setup()` to `self.legacy_tool_executor = ToolExecutor(...)` so the framework's `tool_executor` (set by `run_daemon`) isn't shadowed. Update any other references to the legacy executor inside `setup()` and `handle_chat`.

- [ ] **Step 3: Update `handle_chat`'s tool-call site to use the helper**

Find the inference loop in `handle_chat` where tool calls are dispatched:

```python
result = await self.tool_executor.run_async(tc.name, tc.arguments)
```

Change to:

```python
result = await self._run_tool(tc.name, tc.arguments, ctx)
```

- [ ] **Step 4: Update the inference call's `tools=` parameter**

Find both `messages, tools=TOOL_DEFINITIONS, reasoning=mode` lines and change `TOOL_DEFINITIONS` to merge framework + legacy schemas:

```python
all_tool_schemas = self.tool_executor.schemas() + TOOL_DEFINITIONS
# ...
self.inference.complete(messages, tools=all_tool_schemas, reasoning=mode, ...)
self.inference.stream(messages, tools=all_tool_schemas, reasoning=mode, ...)
```

This gives the LLM the full union of available tools.

- [ ] **Step 5: Run the tests**

```bash
.venv/bin/pytest -q
```

Expected: green. Tool dispatch now flows through both executors.

- [ ] **Step 6: Smoke test the framework builtins via chat**

In one terminal:

```bash
.venv/bin/pal-daemon
```

In another terminal:

```bash
.venv/bin/pal
```

In the CLI, send: `cat README.md` (or any small vault file). Then: `grep apple Notes`. Then: `ls`.

Expected: framework shell tools are reachable; the model successfully calls `cat`, `grep`, `ls` and shows their output.

If the model doesn't naturally pick the new tools (because PAL's hand-curated tool catalog in BASE_PROMPT doesn't mention them), that's fine for PR1 — they're available via the schema list, just not promoted in prose. PR6 (prompt builder migration) is where prose / catalog updates happen.

- [ ] **Step 7: Smoke test legacy tools still work**

In the CLI: `read_file README.md`. Then: `search_content quantum`.

Expected: legacy executor still services these (PAL's hand-curated names).

- [ ] **Step 8: Commit**

```bash
git add pal/agent.py
git commit -m "feat(agent): dual-dispatch tool execution via framework executor + legacy fallback"
```

---

## Task 26: Verify command registry wiring

**Files:**
- Modify: `pal/agent.py`

PAL's `handle_command` already exists from Phase E (an if/elif tree). The framework's `command_registry` is also wired by `run_daemon`. We want the framework's builtin slash commands to be reachable too.

- [ ] **Step 1: Read the current `handle_command`**

```bash
grep -n "def handle_command\|_handle_X\|command_registry" pal/agent.py | head -20
```

- [ ] **Step 2: Add a dual-dispatch fallback to `handle_command`**

At the *top* of PAL's `handle_command` if/elif tree, before any of PAL's `_handle_X` calls, add:

```python
async def handle_command(self, msg, ctx):
    name = msg.command
    raw_args = msg.args

    # Phase F: framework builtins first (only for names PAL doesn't define itself).
    pal_handlers = self._command_dispatch_table()  # the existing if/elif keys
    if name not in pal_handlers and name in self.command_registry.names():
        async for out in self.command_registry.dispatch(name, raw_args, ctx):
            yield out
        return

    # Existing PAL dispatch follows (unchanged this PR).
    ...
```

If `_command_dispatch_table` doesn't already exist, extract the if/elif keys into a method that returns `set[str]`. This avoids name collisions: PAL's existing commands win, framework builtins fill in gaps (`/help`, `/clear`, `/status`, `/profile`, `/scratch`, `/wisdom`, `/learnings`, `/promote`, `/rate`, `/model`, `/think`, `/quit` — many of these PAL already implements; PAL's wins).

- [ ] **Step 3: Run the tests**

```bash
.venv/bin/pytest -q
```

- [ ] **Step 4: Smoke test `/help`**

In the CLI: `/help`.

Expected: PAL's existing /help still works (PAL implements `/help`). The framework version is *not* reached because PAL's name wins.

PR5 (commands migration) is where PAL's commands move to the framework registry and the if/elif tree disappears.

- [ ] **Step 5: Commit**

```bash
git add pal/agent.py
git commit -m "feat(agent): allow framework command_registry to service slash commands PAL does not define"
```

---

## Task 27: Push PR1 + open PR

- [ ] **Step 1: Push branch**

```bash
git push -u origin feature/phase-f-pr1-builtin-tools
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --title "Phase F PR1: bump agent_core to v0.6.0; register framework builtins" --body "$(cat <<'EOF'
## Summary

- Bumps agent_core to v0.6.0.
- Adds empty `tools = []`, `commands = []`, `disabled_builtins = frozenset()` ClassVars on `PALAgent` so framework builtin tools and commands are reachable through `self.tool_executor` / `self.command_registry`.
- Wires dual-dispatch in `handle_chat`: framework executor handles names it knows; legacy executor handles everything else.

## Test plan
- [ ] All existing PAL tests green
- [ ] Smoke: `cat`, `grep`, `ls` reachable via chat
- [ ] Smoke: legacy tools (`read_file`, `search_content`, `compile_summary`) still work
- [ ] Smoke: `/help` still PAL's version

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Task 28: Merge PR1, clean up worktree

- [ ] **Step 1: After review, merge PR1**

```bash
gh pr merge --squash --delete-branch
```

- [ ] **Step 2: Remove the worktree**

```bash
cd /home/edible/Projects/PAL
git worktree remove .worktrees/phase-f-pr1
git pull --ff-only origin main
```

---

# Part 3: PAL PR2 — Vault tools migration

Working directory: `/home/edible/Projects/PAL/.worktrees/phase-f-pr2`.

This part lifts six PAL tool methods (`_read_file`, `_list_directory`, `_search_content`, `_edit_file`, `_create_file`, `_move_file`) into Tool subclasses under `pal/tools/vault.py`, plus drops `_search_vault` (replaced by the framework builtin from PR1).

The pattern is the same for each tool:
1. Read the existing `_method_name` body in `pal/tools.py`
2. Define a `Tool` subclass in `pal/tools/vault.py` with the same name/parameters and `requires` derived from the methods it accesses
3. Replace the method body with a delegation to the new class (or remove it; see below)
4. Register the class in `PALAgent.tools = [...]`
5. Test

Rather than replacing each method's body, we **delete** the method from `pal/tools.py` and let the framework executor service the call (it has the same name). The dual-dispatch wiring from PR1 means the framework path now wins.

## Task 29: Create PR2 worktree + scaffold `pal/tools/`

- [ ] **Step 1: Worktree**

```bash
cd /home/edible/Projects/PAL
git worktree add .worktrees/phase-f-pr2 -b feature/phase-f-pr2-vault-tools
cd .worktrees/phase-f-pr2
ln -s ../../.venv .venv 2>/dev/null || true
```

- [ ] **Step 2: Create the package skeleton**

```bash
mkdir -p pal/tools
touch pal/tools/__init__.py
```

- [ ] **Step 3: Commit scaffolding**

```bash
git add pal/tools/__init__.py
git commit -m "chore(tools): create pal/tools package"
```

---

## Task 30: Migrate `read_file` to `pal.tools.vault.ReadFile`

**Files:**
- Create: `pal/tools/vault.py`
- Test: `tests/test_tools_read_file.py`
- Modify: `pal/tools.py` (delete `_read_file`)
- Modify: `pal/agent.py` (register tool)

- [ ] **Step 1: Read PAL's existing `_read_file` body**

```bash
sed -n '/def _read_file/,/^    def [^_]\|^class /p' pal/tools.py | head -50
```

Capture: the parameters schema (under `TOOL_DEFINITIONS`) and the method body.

- [ ] **Step 2: Write the failing test**

Create `tests/test_tools_read_file.py`:

```python
"""Tests for pal.tools.vault.ReadFile."""
import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from pal.tools.vault import ReadFile


@dataclass
class _Config:
    vault_path: Path


class _Agent:
    def __init__(self, vault_path):
        self.config = _Config(vault_path)


def _ctx(agent):
    class _C: pass
    c = _C(); c.agent = agent; return c


def test_read_file_returns_content(tmp_path):
    (tmp_path / "x.md").write_text("---\ntitle: X\n---\n\nbody")
    result = asyncio.run(ReadFile().run({"path": "x.md"}, _ctx(_Agent(tmp_path))))
    assert "body" in result


def test_read_file_rejects_escape(tmp_path):
    result = asyncio.run(ReadFile().run({"path": "../../etc/passwd"}, _ctx(_Agent(tmp_path))))
    assert "outside vault" in result.lower()


def test_read_file_missing(tmp_path):
    result = asyncio.run(ReadFile().run({"path": "nope.md"}, _ctx(_Agent(tmp_path))))
    assert "not found" in result.lower()
```

- [ ] **Step 3: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_tools_read_file.py -v
```

Expected: ImportError.

- [ ] **Step 4: Write the implementation**

Create `pal/tools/vault.py`:

```python
"""PAL vault tools — read/write tool surface for the LLM.

These tools have PAL-specific semantics distinct from the agent_core shell
builtins:
- ReadFile parses frontmatter and returns body separately.
- ListDirectory paginates with offset/limit/prefix.
- SearchContent is a simple substring scan (search_vault is the semantic
  search; that's a framework builtin).
- EditFile and CreateFile auto-trigger reindex via ctx.agent.retrieval.
"""
from __future__ import annotations

import logging
from pathlib import Path

from agent_core.tools.base import Tool

logger = logging.getLogger(__name__)

_READ_LIMIT = 32_000


def _resolve_safe(vault: Path, path: str) -> Path | None:
    full = (vault / path).resolve()
    try:
        full.relative_to(vault.resolve())
    except ValueError:
        return None
    return full


def _is_system_path(path: str) -> bool:
    return any(part.startswith("_") for part in Path(path).parts)


class ReadFile(Tool):
    name = "read_file"
    description = "Read a file from the vault. Returns frontmatter and body."
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path relative to vault root (e.g. 'Research/quantum.md')",
            },
        },
        "required": ["path"],
    }
    requires = ("config",)

    async def run(self, args, ctx):
        path = (args.get("path") or "").strip()
        if not path:
            return "Error: 'path' parameter is required."
        resolved = _resolve_safe(ctx.agent.config.vault_path, path)
        if resolved is None:
            return f"Error: path escapes outside vault: {path}"
        if not resolved.exists():
            return f"File not found: {path}"
        if not resolved.is_file():
            return f"Not a file: {path} (use list_directory for directories)"
        content = resolved.read_text(errors="replace")
        if len(content) > _READ_LIMIT:
            content = content[:_READ_LIMIT] + f"\n\n[truncated: {len(content) - _READ_LIMIT} chars dropped]"
        return content
```

- [ ] **Step 5: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_tools_read_file.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Register the tool on PALAgent**

In `pal/agent.py`, change:

```python
tools = []
```

to:

```python
from pal.tools.vault import ReadFile

tools = [ReadFile]
```

- [ ] **Step 7: Delete `_read_file` from `pal/tools.py`**

Delete the method body and remove `"read_file": self._read_file` from the `run()` dispatch table.

- [ ] **Step 8: Run full PAL suite**

```bash
.venv/bin/pytest -q
```

Expected: green. The dispatch goes through framework executor now (PR1's wiring).

- [ ] **Step 9: Smoke test**

Restart pal-daemon and pal CLI; ask: "read README.md".

Expected: model calls `read_file`, framework executor routes to `pal.tools.vault.ReadFile`, output returned.

- [ ] **Step 10: Commit**

```bash
git add pal/tools/vault.py tests/test_tools_read_file.py pal/tools.py pal/agent.py
git commit -m "feat(tools): migrate read_file to pal.tools.vault.ReadFile"
```

---

## Task 31: Migrate `list_directory` to `pal.tools.vault.ListDirectory`

**Files:**
- Modify: `pal/tools/vault.py`, `pal/tools.py`, `pal/agent.py`
- Test: `tests/test_tools_list_directory.py`

Same pattern as Task 30. Read the existing body, write a test, define the Tool class, register, delete from `pal/tools.py`.

- [ ] **Step 1: Read the existing body**

```bash
sed -n '/def _list_directory/,/^    def /p' pal/tools.py | head -80
```

- [ ] **Step 2: Write the test**

Create `tests/test_tools_list_directory.py`:

```python
"""Tests for pal.tools.vault.ListDirectory."""
import asyncio
from dataclasses import dataclass
from pathlib import Path

from pal.tools.vault import ListDirectory


@dataclass
class _Config:
    vault_path: Path


class _Agent:
    def __init__(self, vault_path):
        self.config = _Config(vault_path)


def _ctx(agent):
    class _C: pass
    c = _C(); c.agent = agent; return c


def test_list_root(tmp_path):
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "Notes").mkdir()
    result = asyncio.run(ListDirectory().run({}, _ctx(_Agent(tmp_path))))
    assert "a.md" in result
    assert "Notes" in result


def test_list_paginated_offset(tmp_path):
    for i in range(10):
        (tmp_path / f"f{i:02}.md").write_text(".")
    result = asyncio.run(ListDirectory().run({"offset": 5, "limit": 3}, _ctx(_Agent(tmp_path))))
    # Should include entries starting at index 5
    assert "f05.md" in result or "f06.md" in result


def test_list_prefix(tmp_path):
    (tmp_path / "agent-1.md").write_text(".")
    (tmp_path / "agent-2.md").write_text(".")
    (tmp_path / "other.md").write_text(".")
    result = asyncio.run(ListDirectory().run({"prefix": "agent-"}, _ctx(_Agent(tmp_path))))
    assert "agent-1.md" in result and "agent-2.md" in result
    assert "other.md" not in result
```

- [ ] **Step 3: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_tools_list_directory.py -v
```

- [ ] **Step 4: Append `ListDirectory` to `pal/tools/vault.py`**

```python
class ListDirectory(Tool):
    name = "list_directory"
    description = (
        "List files and subdirectories in a vault directory. Paginated: by default "
        "returns up to 50 entries with a footer indicating the total and how to "
        "continue. Use prefix to filter when reorganizing large directories."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path relative to vault root."},
            "limit": {"type": "integer", "description": "Max entries to return (default 50, cap 500)."},
            "offset": {"type": "integer", "description": "Skip this many entries before returning."},
            "prefix": {"type": "string", "description": "Only return entries whose filename starts with this string."},
        },
        "required": [],
    }
    requires = ("config",)

    async def run(self, args, ctx):
        # Lifted from pal.tools.ToolExecutor._list_directory.
        path = (args.get("path") or "").strip()
        limit = max(1, min(int(args.get("limit", 50)), 500))
        offset = max(0, int(args.get("offset", 0)))
        prefix = (args.get("prefix") or "").strip()
        vault = ctx.agent.config.vault_path
        if path:
            resolved = _resolve_safe(vault, path)
            if resolved is None:
                return f"Error: path escapes outside vault: {path}"
        else:
            resolved = vault.resolve()
        if not resolved.exists() or not resolved.is_dir():
            return f"Directory not found: {path or '/'}"
        entries = sorted(resolved.iterdir(), key=lambda p: p.name)
        if prefix:
            entries = [e for e in entries if e.name.startswith(prefix)]
        total = len(entries)
        sliced = entries[offset:offset + limit]
        lines = []
        for e in sliced:
            display = e.name + ("/" if e.is_dir() else "")
            lines.append(display)
        footer = f"\n[showing {len(sliced)} of {total} entries; offset={offset} limit={limit}]"
        return "\n".join(lines) + footer
```

- [ ] **Step 5: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_tools_list_directory.py -v
```

- [ ] **Step 6: Register**

In `pal/agent.py`:

```python
from pal.tools.vault import ReadFile, ListDirectory

tools = [ReadFile, ListDirectory]
```

- [ ] **Step 7: Delete `_list_directory` from `pal/tools.py`**

- [ ] **Step 8: Full suite + smoke**

```bash
.venv/bin/pytest -q
```

Smoke: `list_directory Notes` via chat.

- [ ] **Step 9: Commit**

```bash
git add pal/tools/vault.py tests/test_tools_list_directory.py pal/tools.py pal/agent.py
git commit -m "feat(tools): migrate list_directory to pal.tools.vault.ListDirectory"
```

---

## Tasks 32-36: Migrate the remaining vault tools

The pattern is identical to Tasks 30-31. For each tool below:

1. Read existing body in `pal/tools.py`
2. Write a small test asserting the happy path + path escape rejection
3. Append a `Tool` subclass to `pal/tools/vault.py`
4. Register in `PALAgent.tools = [...]`
5. Delete the `_method_name` from `pal/tools.py`
6. Run full suite, smoke test, commit

Do them as separate tasks/commits, one per tool.

### Task 32: `search_content` → `SearchContent`

- `parameters`: `query: str (required)` from existing TOOL_DEFINITIONS.
- `requires = ("config",)`.
- Body: lift `_search_content` body. Substring scan across vault files; output capped at 32 KB.
- Test cases: matches found; no match; rejects escape.

Commit message: `feat(tools): migrate search_content to pal.tools.vault.SearchContent`

### Task 33: `edit_file` → `EditFile`

- `parameters`: `path, old_str, new_str, expected_replacements` per existing schema.
- `requires = ("config", "retrieval")` (retrieval needed for auto-reindex).
- Body: lift `_edit_file`. After successful write, call `await ctx.agent.retrieval.trigger_reindex(paths=[absolute])` (matching the legacy executor's auto-reindex behavior — now owned by the tool, not the executor).
- Test cases: replace happy path; missing file; mismatched expected_replacements; auto-reindex called.

Commit message: `feat(tools): migrate edit_file to pal.tools.vault.EditFile (owns its reindex side effect)`

### Task 34: `create_file` → `CreateFile`

- `parameters`: `path, content` per existing schema.
- `requires = ("config", "retrieval")`.
- Body: lift `_create_file`. After successful write, call `trigger_reindex` (same as EditFile).
- Test cases: create happy path; refuse to overwrite (existing); reject system path; reject escape.

Commit message: `feat(tools): migrate create_file to pal.tools.vault.CreateFile (owns its reindex side effect)`

### Task 35: `move_file` → `MoveFile`

- `parameters`: `src, dst` per existing schema.
- `requires = ("config", "retrieval")`.
- Body: lift `_move_file`. Auto-reindex both src (now gone) and dst.
- Test cases: rename within vault; cross-directory move; reject escape on either path.

Commit message: `feat(tools): migrate move_file to pal.tools.vault.MoveFile`

### Task 36: Drop PAL's `_search_vault`

PAL's `_search_vault` is replaced by `agent_core.tools._framework.SearchVault` (registered in PR1).

- [ ] Read PAL's `_search_vault` body and confirm it has no PAL-specific logic beyond wrapping `RetrievalClient.query()`.
- [ ] Delete the method from `pal/tools.py`.
- [ ] Confirm `search_vault` is reachable through the framework executor.
- [ ] Run full suite + smoke (`search_vault quantum` via chat).
- [ ] Commit: `feat(tools): drop pal._search_vault; use agent_core.tools.SearchVault builtin`

---

## Task 37: Re-export vault tools from `pal/tools/__init__.py`

**Files:**
- Modify: `pal/tools/__init__.py`

- [ ] **Step 1: Add re-exports**

```python
"""PAL tool implementations (Tool subclasses)."""
from pal.tools.vault import (
    ReadFile, ListDirectory, SearchContent, EditFile, CreateFile, MoveFile,
)

__all__ = [
    "ReadFile", "ListDirectory", "SearchContent", "EditFile", "CreateFile", "MoveFile",
]
```

- [ ] **Step 2: Update `pal/agent.py` to import from the package**

```python
from pal.tools import ReadFile, ListDirectory, SearchContent, EditFile, CreateFile, MoveFile

tools = [ReadFile, ListDirectory, SearchContent, EditFile, CreateFile, MoveFile]
```

- [ ] **Step 3: Run suite + commit**

```bash
.venv/bin/pytest -q
git add pal/tools/__init__.py pal/agent.py
git commit -m "chore(tools): re-export vault tools from pal.tools package"
```

---

## Task 38: Push PR2 + open + merge

- [ ] **Step 1: Push**

```bash
git push -u origin feature/phase-f-pr2-vault-tools
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --title "Phase F PR2: migrate vault tools to pal.tools.vault" --body "$(cat <<'EOF'
## Summary

- Lifts read_file/list_directory/search_content/edit_file/create_file/move_file to Tool subclasses under `pal/tools/vault.py`.
- Edit/Create/Move tools now own their reindex side effects (formerly hardcoded in `pal.tools.ToolExecutor`).
- Drops `_search_vault` (replaced by `agent_core.tools.SearchVault` builtin from PR1).

## Test plan
- [ ] All existing PAL tests green
- [ ] Smoke: read_file, list_directory, search_content reachable via chat with same shape
- [ ] Smoke: edit_file/create_file trigger reindex
- [ ] Smoke: search_vault still works via framework builtin

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: After review, merge + clean up**

```bash
gh pr merge --squash --delete-branch
cd /home/edible/Projects/PAL
git worktree remove .worktrees/phase-f-pr2
git pull --ff-only origin main
```

---

# Part 4: PAL PR3 — Research and web tools migration

Working directory: `/home/edible/Projects/PAL/.worktrees/phase-f-pr3`. Pattern is the same as PR2. Tools to migrate: `propose_research`, `research_topic`. Drop `_search_web` (use framework builtin).

## Task 39: Create PR3 worktree

- [ ] **Step 1: Worktree**

```bash
cd /home/edible/Projects/PAL
git worktree add .worktrees/phase-f-pr3 -b feature/phase-f-pr3-research-web
cd .worktrees/phase-f-pr3
ln -s ../../.venv .venv 2>/dev/null || true
```

---

## Task 40: Migrate `propose_research` to `pal.tools.research.ProposeResearch`

**Files:**
- Create: `pal/tools/research.py`
- Test: `tests/test_tools_propose_research.py`
- Modify: `pal/tools.py` (delete `_propose_research`), `pal/agent.py` (register)

ProposeResearch is the most complex tool flow — emit + await approval. It's the canonical exercise of `ctx.emit`.

- [ ] **Step 1: Read PAL's existing `_propose_research`**

```bash
sed -n '/async def _propose_research/,/async def _research_topic/p' pal/tools.py
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_tools_propose_research.py`:

```python
"""Tests for pal.tools.research.ProposeResearch."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from pal.tools.research import ProposeResearch


def _ctx(agent):
    class _C: pass
    c = _C(); c.agent = agent; c.channel_id = "default"; c.emit = AsyncMock(); return c


def test_propose_research_emits_proposal_and_awaits_approval():
    approval_registry = MagicMock()
    approval_registry.create.return_value = "prop_123"
    approval_registry.wait = AsyncMock(return_value="approved")
    agent = MagicMock(approval_registry=approval_registry)
    ctx = _ctx(agent)
    result = asyncio.run(ProposeResearch().run(
        {"topic": "X", "rationale": "Y", "depth": 3},
        ctx,
    ))
    # Emitted exactly one proposal message
    ctx.emit.assert_awaited_once()
    # Returned the resulting status
    assert "approved" in result.lower()
    assert "prop_123" in result


def test_propose_research_declined_returns_status():
    approval_registry = MagicMock()
    approval_registry.create.return_value = "prop_456"
    approval_registry.wait = AsyncMock(return_value="declined")
    agent = MagicMock(approval_registry=approval_registry)
    ctx = _ctx(agent)
    result = asyncio.run(ProposeResearch().run(
        {"topic": "X", "rationale": "Y", "depth": 3},
        ctx,
    ))
    assert "declined" in result.lower()


def test_propose_research_validates_required():
    agent = MagicMock()
    ctx = _ctx(agent)
    result = asyncio.run(ProposeResearch().run({}, ctx))
    assert "topic" in result.lower() or "rationale" in result.lower()
```

- [ ] **Step 3: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_tools_propose_research.py -v
```

- [ ] **Step 4: Write the implementation**

Create `pal/tools/research.py`:

```python
"""Research tools: propose-then-execute with user approval gating."""
from __future__ import annotations

from agent_core.tools.base import Tool


class ProposeResearch(Tool):
    name = "propose_research"
    description = (
        "Propose a research run. Returns a proposal_id and emits a CLI approval "
        "prompt. Requires explicit user approval via the CLI prompt. Blocks "
        "until the user responds."
    )
    parameters = {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "Topic to research."},
            "rationale": {"type": "string", "description": "One-line reason for researching."},
            "depth": {"type": "integer", "description": "Search depth (default 3, max 10)."},
        },
        "required": ["topic", "rationale"],
    }
    requires = ("approval_registry",)

    async def run(self, args, ctx):
        from pal.protocol import ResearchProposalMessage

        topic = (args.get("topic") or "").strip()
        rationale = (args.get("rationale") or "").strip()
        depth = max(1, min(int(args.get("depth", 3)), 10))
        if not topic:
            return "Error: 'topic' parameter is required."
        if not rationale:
            return "Error: 'rationale' parameter is required."

        proposal_id = ctx.agent.approval_registry.create(
            kind="research",
            payload={"topic": topic, "rationale": rationale, "depth": depth},
        )
        await ctx.emit(ResearchProposalMessage(
            proposal_id=proposal_id,
            topic=topic,
            rationale=rationale,
            depth=depth,
        ))
        status = await ctx.agent.approval_registry.wait(proposal_id)
        return f"Status: {status}; proposal_id: {proposal_id}"
```

> **Note:** `ApprovalRegistry.create()` and `wait()` method signatures should be verified against `agent_core.approval_registry`. The exact field names on `ResearchProposalMessage` (in `pal/protocol.py`) may also differ. Adjust to match existing definitions; preserve PAL's external behavior.

- [ ] **Step 5: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_tools_propose_research.py -v
```

- [ ] **Step 6: Register**

In `pal/agent.py`:

```python
from pal.tools.research import ProposeResearch

tools = [..., ProposeResearch]
```

- [ ] **Step 7: Delete `_propose_research` from `pal/tools.py`**

Also remove the per-turn `proposal_emitter` wiring in PAL's `handle_chat` (the legacy wiring that fed PAL's `_propose_research` body); the framework `ctx.emit` replaces it.

- [ ] **Step 8: Run suite + smoke**

```bash
.venv/bin/pytest -q
```

Smoke (full research flow): start daemon + CLI; ask PAL to research a topic; verify the approval prompt appears in the CLI; approve; verify the model proceeds to call `research_topic` next.

- [ ] **Step 9: Commit**

```bash
git add pal/tools/research.py tests/test_tools_propose_research.py pal/tools.py pal/agent.py
git commit -m "feat(tools): migrate propose_research to pal.tools.research.ProposeResearch (uses ctx.emit)"
```

---

## Task 41: Migrate `research_topic` to `pal.tools.research.ResearchTopic`

Same pattern as Task 40. ResearchTopic streams `ToolProgressMessage` per URL fetched.

- [ ] **Step 1**: Read existing `_research_topic` body.
- [ ] **Step 2**: Write test (`tests/test_tools_research_topic.py`) covering: takes a proposal_id, calls into `ctx.agent.researcher.run(...)`, emits progress messages, returns paths/titles. Stub the researcher and approval_registry; assert progress messages were emitted.
- [ ] **Step 3**: Append `ResearchTopic` class to `pal/tools/research.py`. `requires = ("approval_registry", "researcher")`. Inside `run`, validate the proposal_id is approved + unused via `ctx.agent.approval_registry`, call `await ctx.agent.researcher.run(payload)`, stream progress via `await ctx.emit(ToolProgressMessage(...))`, return formatted report.
- [ ] **Step 4**: Run test, register on PALAgent, delete `_research_topic`, full suite, smoke (the second half of the research flow).
- [ ] **Step 5**: Commit: `feat(tools): migrate research_topic to pal.tools.research.ResearchTopic`

---

## Task 42: Drop PAL's `_search_web`

The framework `search_web` builtin replaces it (registered in PR1).

- [ ] **Step 1**: Confirm PAL's `_search_web` has no domain-specific logic beyond wrapping `WebSearchClient.search()`.
- [ ] **Step 2**: Delete the method from `pal/tools.py` and the `"search_web"` branch from `run_async`.
- [ ] **Step 3**: Run suite + smoke (`search_web quantum` via chat).
- [ ] **Step 4**: Commit: `feat(tools): drop pal._search_web; use agent_core.tools.SearchWeb builtin`

---

## Task 43: Re-export research tools

In `pal/tools/__init__.py`, add `from pal.tools.research import ProposeResearch, ResearchTopic` and append to `__all__`.

Commit: `chore(tools): re-export research tools`

---

## Task 44: Push PR3 + open + merge

```bash
git push -u origin feature/phase-f-pr3-research-web
gh pr create --title "Phase F PR3: migrate research and web tools" --body "..."
# After review:
gh pr merge --squash --delete-branch
git worktree remove .worktrees/phase-f-pr3
git pull --ff-only origin main
```

PR body should describe: ProposeResearch and ResearchTopic migrated; `_search_web` dropped (framework builtin replaces); ctx.emit replaces per-turn proposal_emitter wiring.

---

# Part 5: PAL PR4 — Wiki, compile, consolidate, reorg, promote, wait

Working directory: `/home/edible/Projects/PAL/.worktrees/phase-f-pr4`.

Same pattern as PR2/PR3 for nine more tools. They split across four files:
- `pal/tools/compile.py`: CompileSummary, ProposeCompileBatch, CompileBatch
- `pal/tools/consolidate.py`: ProposeConsolidate, Consolidate
- `pal/tools/reorg.py`: ProposeReorg, ProposePromote, Reorg
- `pal/tools/wait.py`: WaitForReindex

## Task 45: Create PR4 worktree

```bash
cd /home/edible/Projects/PAL
git worktree add .worktrees/phase-f-pr4 -b feature/phase-f-pr4-wiki-tools
cd .worktrees/phase-f-pr4
ln -s ../../.venv .venv 2>/dev/null || true
```

---

## Tasks 46-54: Migrate wiki tools (one task per tool)

For each tool, follow the Task 30 pattern: read existing body in `pal/tools.py`, write test (happy path + error cases), define class in the appropriate file, register in `PALAgent.tools`, delete from `pal/tools.py`, run suite, commit.

The `requires` tuple for each is non-trivial — derive from what `self.X` was used in the original method body.

### Task 46: `compile_summary` → `CompileSummary` (in `pal/tools/compile.py`)

- `requires`: `("compiler", "retrieval")` (compiler does the work; retrieval triggers reindex on the new article).
- Body: lift from PAL's `_compile_summary`. Calls `await ctx.agent.compiler.compile_one(summary_path)`, then `await ctx.agent.retrieval.trigger_reindex(...)`.

### Task 47: `propose_compile_batch` → `ProposeCompileBatch`

- `requires`: `("approval_registry", "compiler")`.
- Body: lift from `_propose_compile_batch`. Emits `BatchProposalMessage` via `ctx.emit`, awaits via `approval_registry.wait`.

### Task 48: `compile_batch` → `CompileBatch`

- `requires`: `("approval_registry", "compiler", "retrieval")`.
- Body: lift from `_compile_batch`. Looks up proposal, executes via compiler, triggers reindex.

### Task 49: `propose_consolidate` → `ProposeConsolidate` (in `pal/tools/consolidate.py`)

- `requires`: `("approval_registry", "consolidator")`.

### Task 50: `consolidate` → `Consolidate`

- `requires`: `("approval_registry", "consolidator", "retrieval")`.

### Task 51: `propose_reorg` → `ProposeReorg` (in `pal/tools/reorg.py`)

- `requires`: `("approval_registry", "reorganizer")`.

### Task 52: `propose_promote` → `ProposePromote`

- `requires`: `("approval_registry", "reorganizer")`.

### Task 53: `reorg` → `Reorg`

- `requires`: `("approval_registry", "reorganizer", "retrieval")`.

### Task 54: `wait_for_reindex` → `WaitForReindex` (in `pal/tools/wait.py`)

- `requires`: `("retrieval",)`.
- Body: lift from `_wait_for_reindex`. Polls `ctx.agent.retrieval.get_reindex_status(job_id)` until done or timeout.

Each task's commit message: `feat(tools): migrate <name> to pal.tools.<file>.<Class>`.

---

## Task 55: Re-export wiki tools

In `pal/tools/__init__.py`:

```python
from pal.tools.compile import CompileSummary, ProposeCompileBatch, CompileBatch
from pal.tools.consolidate import ProposeConsolidate, Consolidate
from pal.tools.reorg import ProposeReorg, ProposePromote, Reorg
from pal.tools.wait import WaitForReindex
```

Update `__all__`. Commit.

---

## Task 56: Push PR4 + open + merge

Smoke before merge: full compile-batch flow (propose → approve → execute → reindex). Full consolidate flow. Full reorg flow. Each at least once.

PR body summarizes the nine migrated tools and notes auto-reindex side effects now live in the tools themselves.

---

# Part 6: PAL PR5 — Slash commands migration

Working directory: `/home/edible/Projects/PAL/.worktrees/phase-f-pr5`.

Migrate PAL's slash commands to `Command` subclasses in `pal/commands/`. Delete `pal/commands.py` (the static metadata list) and the `_handle_X` method tree from `pal/agent.py`.

## Task 57: Create PR5 worktree + scaffold `pal/commands/`

```bash
cd /home/edible/Projects/PAL
git worktree add .worktrees/phase-f-pr5 -b feature/phase-f-pr5-commands
cd .worktrees/phase-f-pr5
ln -s ../../.venv .venv 2>/dev/null || true
mkdir -p pal/commands
touch pal/commands/__init__.py
git add pal/commands/__init__.py
git commit -m "chore(commands): create pal/commands package"
```

---

## Tasks 58-69: Migrate one PAL slash command at a time

For each of PAL's domain commands, follow the same pattern as the tool migrations:
1. Read existing `_handle_X` body in `pal/agent.py`
2. Write test (asserts the command yields the expected message shape)
3. Define `Command` subclass in the appropriate file
4. Register in `PALAgent.commands = [...]`
5. Strip the `_handle_X` method and the if/elif branch from `pal/agent.py`
6. Run suite + smoke + commit

PAL's domain commands and their target files:

### Task 58: `Research` → `pal/commands/research.py`
- `args = "<topic>"`, `requires = ("approval_registry", "researcher", "config")`
- Body: lift from `_handle_research`. Streams progress + final result.

### Task 59: `Compile` → `pal/commands/compile.py`
- `args = "<title>"`, `requires = ("compiler", "retrieval")`

### Task 60: `CompileBatch` (slash command, distinct from the tool) → `pal/commands/compile.py`
- `args = ""`, `requires = ("compiler", "approval_registry", "retrieval")`

### Tasks 61-69: Domain commands → `pal/commands/domain.py`

One task per command. All in `pal/commands/domain.py`:

- 61: `Import` (`<path>`, `requires = ("config", "categorizer", "retrieval")`)
- 62: `Summarize` (`<title>`, `requires = ("summarizer",)`)
- 63: `Read` (`<title>`, `requires = ("wiki",)`)
- 64: `Search` (`<q>`, `requires = ("retrieval",)`)
- 65: `Get` (`<title>`, `requires = ("wiki",)`)
- 66: `Note` (`<text>`, `requires = ("wiki",)`)
- 67: `Lint` (no args, `requires = ("wiki",)`)
- 68: `SearchWeb` (`<q>`, `requires = ("websearch",)`) — slash version; wraps `search_web` tool
- 69: `Fetch` (`<url>`, `requires = ("allowlist",)`) — wraps `fetch_url` tool
- 70: `Learn` (no args, `requires = ("inference", "learning")`) — PAL's manual extraction; stays domain because the post-extraction review needs to settle the shape

Each task: write test, define class, register, strip from agent.py, suite + smoke, commit.

---

## Task 70: Replace `handle_command` with registry dispatch

**Files:**
- Modify: `pal/agent.py`

After Tasks 58-69, every PAL command has a Command subclass and is in `commands = [...]`. The if/elif tree in `handle_command` is now dead code.

- [ ] **Step 1: Replace the if/elif tree**

Find `async def handle_command(...)` in `pal/agent.py`. Replace its body with:

```python
async def handle_command(self, msg, ctx):
    name = msg.command
    raw_args = msg.args
    async for out in self.command_registry.dispatch(name, raw_args, ctx):
        yield out
```

The dual-dispatch fallback from PR1 (Task 26) is no longer needed; the framework registry now contains everything.

- [ ] **Step 2: Delete the `_handle_X` methods**

All of them. After this delete, `pal/agent.py` should drop ~200 LOC.

- [ ] **Step 3: Run full suite**

```bash
.venv/bin/pytest -q
```

- [ ] **Step 4: Smoke every command**

Run `pal-daemon` + `pal`; invoke each slash command (just the no-args form for most; provide minimal args where required). Confirm each returns the expected output. The list to test:

```
/help
/clear
/status
/profile
/scratch
/wisdom
/learnings
/model
/think
/research foo
/compile some-title
/compile-batch
/import path/to/file.md
/summarize some-title
/read some-title
/search foo
/get some-title
/note hello
/lint
/search-web foo
/fetch http://example.com
/learn
```

- [ ] **Step 5: Commit**

```bash
git add pal/agent.py
git commit -m "feat(agent): replace handle_command if/elif tree with command_registry dispatch"
```

---

## Task 71: Delete `pal/commands.py`

The static `COMMANDS` list is replaced by registry metadata.

- [ ] **Step 1: Find references to the old module**

```bash
grep -rn "from pal.commands\|import pal.commands\|pal.commands" --include="*.py" .
```

- [ ] **Step 2: Update consumers**

Each reference now reads from `agent.command_registry.metadata()`. Common sites:
- `pal/discord_adapter.py`: the prefix-rewrite layer reads command names. Change to `agent.command_registry.names()`.
- `pal/cli.py` splash screen: reads command list. Change to `agent.command_registry.metadata()`.
- Any tests that import from `pal.commands`.

- [ ] **Step 3: Delete the file**

```bash
rm pal/commands.py
```

(The new `pal/commands/` package is unaffected — different name.)

- [ ] **Step 4: Run suite + smoke + commit**

```bash
.venv/bin/pytest -q
git add -u pal/commands.py pal/discord_adapter.py pal/cli.py tests/
git commit -m "feat(commands): delete pal/commands.py; consumers read from agent.command_registry"
```

---

## Task 72: Re-export PAL command classes

In `pal/commands/__init__.py`:

```python
"""PAL command implementations."""
from pal.commands.research import Research
from pal.commands.compile import Compile, CompileBatch
from pal.commands.domain import (
    Import, Summarize, Read, Search, Get, Note, Lint,
    SearchWeb, Fetch, Learn,
)

__all__ = [
    "Research", "Compile", "CompileBatch",
    "Import", "Summarize", "Read", "Search", "Get", "Note", "Lint",
    "SearchWeb", "Fetch", "Learn",
]
```

Update `pal/agent.py` to import from the package. Commit.

---

## Task 73: Push PR5 + open + merge

Smoke checklist before merge:
- Every slash command invokable; output matches pre-migration shape
- `/help` lists builtin + PAL commands
- `/research`, `/compile`, `/compile-batch` still emit approval prompts
- Discord prefix-rewrite (`!cmd`) maps correctly to slash command

PR body summarizes: 12 commands migrated to Command subclasses, handle_command if/elif tree replaced with registry dispatch, `pal/commands.py` deleted, agent.py drops ~200 LOC.

---

# Part 7: PAL PR6 — Prompt builder migration

Working directory: `/home/edible/Projects/PAL/.worktrees/phase-f-pr6`.

## Task 74: Create PR6 worktree + scaffold `pal/prompts/`

```bash
cd /home/edible/Projects/PAL
git worktree add .worktrees/phase-f-pr6 -b feature/phase-f-pr6-prompt-builder
cd .worktrees/phase-f-pr6
ln -s ../../.venv .venv 2>/dev/null || true
mkdir -p pal/prompts
touch pal/prompts/__init__.py
```

---

## Task 75: Move PAL's BASE_PROMPT to `pal/prompts/system.py`

**Files:**
- Create: `pal/prompts/system.py`
- Modify: `pal/agent.py` (rewrite `system_prompt`)
- Delete: `pal/prompt_builder.py`

- [ ] **Step 1: Read PAL's existing BASE_PROMPT**

```bash
sed -n '/^BASE_PROMPT/,/^"""$/p' pal/prompt_builder.py
```

- [ ] **Step 2: Move BASE_PROMPT into `pal/prompts/system.py`**

Create `pal/prompts/system.py`:

```python
"""PAL's identity, policy, tool catalog, and style prose.

This is the agent-specific portion of the system prompt. Standard sections
(profile / wisdom / scratchpad / commands catalog) are appended by
`SystemPromptBuilder` render helpers from `agent_core.prompts.builder`.
"""

PAL_BASE_PROMPT = """You are PAL, a personal AI librarian. ..."""  # full BASE_PROMPT body
```

(Copy the full BASE_PROMPT body from `pal/prompt_builder.py`.)

- [ ] **Step 3: Pre/post diff capture**

Before changing `system_prompt`, capture what it currently produces for a representative turn:

```bash
.venv/bin/python -c "
from pal.config import load_config
from pal.agent import PALAgent
import asyncio

cfg = load_config()
agent = PALAgent()
agent.config = cfg
# Stub managers + run setup minimally; or use a test fixture
# Capture: agent.prompt_builder.build(...) (legacy)
"
```

Save the output to a temp file for comparison after the rewrite.

- [ ] **Step 4: Rewrite `system_prompt`**

In `pal/agent.py`:

```python
from pal.prompts.system import PAL_BASE_PROMPT

def system_prompt(self, ctx) -> str:
    pb = self.prompt_builder
    return "\n\n".join(filter(None, [
        PAL_BASE_PROMPT,
        pb.render_profile(),
        pb.render_wisdom(),
        pb.render_scratchpad(ctx.channel_id),
        pb.render_commands_catalog(),
    ]))
```

PAL's `system_prompt` no longer reads from `pal.prompt_builder`. The `from pal.prompt_builder import SystemPromptBuilder` import gets removed.

- [ ] **Step 5: Delete `pal/prompt_builder.py`**

```bash
rm pal/prompt_builder.py
```

- [ ] **Step 6: Run suite**

```bash
.venv/bin/pytest -q
```

- [ ] **Step 7: Diff captured prompt vs new prompt**

Re-capture the rendered prompt for the same scenario as Step 3. Diff. Confirm:
- BASE_PROMPT body present and unchanged
- Profile/wisdom/scratchpad sections render the same content
- Commands catalog renders all PAL + builtin commands (with framework's auto-rendering)
- No tool catalog auto-render (PAL's hand-curated catalog is inlined in BASE_PROMPT)

If anything material changed beyond expected, fix before commit.

- [ ] **Step 8: Commit**

```bash
git add pal/prompts/ pal/agent.py
git rm pal/prompt_builder.py
git commit -m "feat(prompts): use agent_core SystemPromptBuilder; move PAL_BASE_PROMPT to pal.prompts.system"
```

---

## Task 76: Push PR6 + open + merge

Smoke: response quality unchanged on a few canonical questions:
- "What's in the vault about quantum?"
- "Compile this summary: raw/summaries/foo.md"
- "Search the web for X"

PR body: `pal/prompt_builder.py` deleted; PAL's BASE_PROMPT moved to `pal/prompts/system.py`; `system_prompt(ctx)` assembles via framework render helpers.

---

# Part 8: PAL PR7 — Cleanup

Working directory: `/home/edible/Projects/PAL/.worktrees/phase-f-pr7`.

## Task 77: Create PR7 worktree

```bash
cd /home/edible/Projects/PAL
git worktree add .worktrees/phase-f-pr7 -b feature/phase-f-pr7-cleanup
cd .worktrees/phase-f-pr7
ln -s ../../.venv .venv 2>/dev/null || true
```

---

## Task 78: Verify `pal/tools.py` is unreachable

By PR7, all PAL tools have migrated. Anything left in `pal/tools.py` is unreachable cruft (e.g. PAL's `_update_scratch` and `_add_learning` from PR1 — superseded by framework builtins but never explicitly deleted).

- [ ] **Step 1: Confirm no remaining live calls**

```bash
grep -rn "from pal.tools import\|import pal.tools\|pal.tools.ToolExecutor\|pal.tools._" --include="*.py" .
```

Should return only `pal.tools.<Tool subclass>` references (which live in `pal/tools/` directory now, not `pal/tools.py`).

- [ ] **Step 2: Read what's left in `pal/tools.py`**

```bash
cat pal/tools.py
```

Anything remaining is unreachable.

---

## Task 79: Delete `pal/tools.py` and remove dual-dispatch fallback

**Files:**
- Delete: `pal/tools.py`
- Modify: `pal/agent.py` (remove dual-dispatch helper and legacy_tool_executor)

- [ ] **Step 1: Delete `pal/tools.py`**

```bash
rm pal/tools.py
```

- [ ] **Step 2: Strip the dual-dispatch helper from `pal/agent.py`**

Remove the `_run_tool` method added in PR1 (Task 25). In `handle_chat`, change:

```python
result = await self._run_tool(tc.name, tc.arguments, ctx)
```

back to:

```python
result = await self.tool_executor.run(tc.name, tc.arguments, ctx)
```

Also strip the `legacy_tool_executor` attribute and the `all_tool_schemas = self.tool_executor.schemas() + TOOL_DEFINITIONS` line — `TOOL_DEFINITIONS` no longer exists. Use `self.tool_executor.schemas()` directly.

- [ ] **Step 3: Strip imports of `pal.tools`**

`pal/agent.py` no longer imports `from pal.tools import ToolExecutor, TOOL_DEFINITIONS`. Drop the imports.

- [ ] **Step 4: Run full suite**

```bash
.venv/bin/pytest -q
```

Expected: green.

- [ ] **Step 5: Full smoke test**

Restart pal-daemon + pal CLI. Hit every feature surface:
- Read/list/search/edit/create vault tools
- Search vault (semantic)
- Search web; fetch URL
- Research flow (propose → approve → execute)
- Compile flow (single + batch)
- Consolidate, reorg, promote
- Wait for reindex
- Update scratch; add learning
- Every slash command (`/help`, `/clear`, `/status`, ...)
- Discord bridge handles a chat turn (if Discord daemon is running)

Confirm nothing regressed.

- [ ] **Step 6: Commit**

```bash
git rm pal/tools.py
git add pal/agent.py
git commit -m "refactor(tools): delete pal/tools.py; remove dual-dispatch fallback"
```

---

## Task 80: Push PR7 + open + merge

PR body summarizes: legacy `pal/tools.py` deleted; dual-dispatch wiring removed; only the framework executor remains.

```bash
gh pr create --title "Phase F PR7: delete legacy executor; cleanup" --body "..."
# After review:
gh pr merge --squash --delete-branch
git worktree remove .worktrees/phase-f-pr7
git pull --ff-only origin main
```

---

# Part 9: Memory + close-out

## Task 81: Update memory and close out

- [ ] **Step 1: Update `MEMORY.md`**

Update the relevant memory entries to reflect Phase F shipped status:
- `project_agent_core_extraction.md`: bump to "Phases A-F done (v0.6.0); G/H remaining"
- `project_phase_f_builtin_tools.md`: replace with "Phase F shipped (v0.6.0): tool/command/prompt scaffolding extracted; 12 builtins (7 shell + 5 framework-backed); PAL adopted across 7 PRs"

- [ ] **Step 2: Update `project_phase_e_post_extraction_review.md`**

Now that all major extractions are done (only Phase G — Discord gateway — remains, which is small), the post-extraction review is the next major milestone.

- [ ] **Step 3: Confirm clean state**

```bash
cd /home/edible/Projects/PAL
git status
git worktree list
```

Expected: working tree clean; no Phase F worktrees remaining.

```bash
cd /home/edible/Projects/agent_core
git status
git tag --list 'v0.6.*'
```

Expected: clean; v0.6.0 tag present.

- [ ] **Step 4: Note Phase G readiness**

Phase G (Discord gateway adapter extraction) is the next extraction phase. The remaining work in PAL after Phase F is small enough that a broader post-extraction review is appropriate as the next major project. See `MEMORY.md` for the running queue.

---

## Notes for the executing agent

**Working directories.** Part 1 uses agent_core directly; PAL parts use per-PR worktrees. Always confirm the current `pwd` matches the part you're in. Worktrees share the `.venv`; symlink if needed.

**Test commands.** Always use `.venv/bin/pytest`. Never `pip install --break-system-packages`. Never `git add -A` in the PAL repo.

**Naming uncertainty.** Several manager method names in this plan (e.g. `RetrievalClient.query`, `LearningManager.add_candidate`, `WisdomManager.add`) are best guesses. If a test fails on an attribute error, read the actual class definition in agent_core and adjust. The test shape (call site + return shape) is what matters.

**Smoke testing.** Each PAL PR's smoke test is non-negotiable. The dispatch path matters; tests don't catch every interaction with the LLM. If a smoke test surfaces unexpected behavior, treat it as a finding worth investigating, not a flaky test.

**Reverting.** Each PAL PR is independently revertable. If PR3's research flow breaks something subtle, revert and re-cut. The cleanup PR (PR7) is the only one with no revert escape hatch — so save it for last.

**Out-of-band messages.** PR3 (research) is the first PR that exercises `ctx.emit`. Watch carefully for ordering issues between emitted proposal messages and stream chunks. The new emit semantics (awaitable, NDJSON-encoded) differ subtly from the legacy `proposal_emitter` callback.

**The `handle_chat` loop.** Lifted in Phase E. Loop structure is preserved across PR2-PR4; only the call site for tool dispatch changes. Read it carefully in PR2 and confirm interleaving of stream chunks with tool calls is preserved.
