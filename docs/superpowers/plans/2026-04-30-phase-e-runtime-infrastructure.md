# Phase E Runtime Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract PAL's runtime infrastructure (Agent base class, BaseConfig, run_daemon entry point, generic daemon core, socket client, CLI REPL) into `agent_core@v0.5.0`, so PAL's daemon stops being a 2000-line monolith and becomes a thin Agent subclass.

**Architecture:** Two-repo migration. `agent_core` v0.5.0 ships a thin transport-only `Daemon` class plus `Agent` base class with `setup()`, `system_prompt()`, `handle_chat()`, `handle_command()` extension points. `BaseConfig` provides env-var-driven config with name-derived prefix machinery. `run_daemon(agent)` constructs framework managers and starts the daemon. `agent_core.adapters.cli` provides a generic REPL with a Renderer protocol. PAL consumes by creating `pal/agent.py` (`PALAgent(Agent)`), shrinking `pal/cli.py` to a `PALRenderer`, deleting `pal/daemon.py` (functionality lifts to PALAgent), and updating `pal/config.py` to a `PALConfig(BaseConfig)` subclass.

**Tech Stack:** Python 3.12+, hatchling, pytest, pytest-asyncio, prompt-toolkit, rich, asyncio. No new runtime/dev deps.

**Repos involved:**
- agent_core: `/home/edible/Projects/agent_core` (currently `v0.4.0` on main)
- PAL: `/home/edible/Projects/PAL` (main checkout). PAL-side migration work happens in a feature-branch worktree at `/home/edible/Projects/PAL/.worktrees/phase-e-runtime`.

**Reference:** spec at `docs/superpowers/specs/2026-04-30-phase-e-runtime-infrastructure-design.md`. Builds on Phase D (`docs/superpowers/plans/2026-04-28-agent-core-extraction-phase-d.md`).

**Phase D status (recently shipped):** agent_core v0.4.0 has all per-channel state and protocol primitives. PAL has been migrated to consume them. The inference safety stopgap is in place (max_tokens=4096 in three call sites in `pal/daemon.py`); the StreamEnd filter ships in PAL too. All of this carries forward into Phase E.

---

## Pre-flight: Code map

Mapped during planning. Use this as the migration target list.

### Files modified or created

| Repo | Path | Change |
|---|---|---|
| agent_core | `agent_core/agent_core/config.py` | NEW. BaseConfig dataclass + load_config. |
| agent_core | `agent_core/agent_core/agent.py` | NEW. Agent base class + HandlerContext. |
| agent_core | `agent_core/agent_core/client.py` | NEW. DaemonConnection (lifted from pal/client.py). |
| agent_core | `agent_core/agent_core/daemon.py` | NEW. Daemon class (transport-only). |
| agent_core | `agent_core/agent_core/runtime.py` | NEW. run_daemon entry point. |
| agent_core | `agent_core/agent_core/adapters/__init__.py` | NEW. Empty package marker. |
| agent_core | `agent_core/agent_core/adapters/cli.py` | NEW. run_repl + Renderer protocol + default rendering. |
| agent_core | `agent_core/tests/test_config.py` | NEW. |
| agent_core | `agent_core/tests/test_agent.py` | NEW. |
| agent_core | `agent_core/tests/test_client.py` | NEW. |
| agent_core | `agent_core/tests/test_daemon.py` | NEW. |
| agent_core | `agent_core/tests/test_runtime.py` | NEW. |
| agent_core | `agent_core/tests/test_cli.py` | NEW. |
| agent_core | `agent_core/tests/test_contract.py` | NEW. The contract tests for the API. |
| agent_core | `agent_core/pyproject.toml` | Bump version to 0.5.0. |
| agent_core | `agent_core/CHANGELOG.md` | Add 0.5.0 entry. |
| PAL | `pyproject.toml` | Bump agent_core dep to v0.5.0. |
| PAL | `pal/agent.py` | NEW. PALAgent(Agent). |
| PAL | `pal/config.py` | Shrink to PALConfig(BaseConfig). |
| PAL | `pal/cli.py` | Shrink to PALRenderer + main(). |
| PAL | `pal/daemon_main.py` | Body becomes `run_daemon(PALAgent(), config_cls=PALConfig)`. |
| PAL | `pal/daemon.py` | DELETE. |
| PAL | `pal/client.py` | DELETE. |
| PAL | `tests/test_daemon_*.py`, `tests/test_chat_*.py` | Update imports + fixtures to point at PALAgent. |

### Symbol map (what moves where)

| Symbol | Source (Phase D state) | Destination (Phase E target) |
|---|---|---|
| `STREAM_BUFFER_LIMIT`, `encode_message`, `decode_message`, generic Message types | `agent_core.protocol` (already there) | unchanged |
| `Conversation`, `ChannelStore`, `Scratchpad`, `LearningScanner` | `agent_core.*` (already there) | unchanged |
| `ProfileManager`, `WisdomManager`, `LearningManager`, `AllowlistManager`, `ApprovalRegistry`, `InferenceClient`, `RetrievalClient`, `WebSearchClient` | `agent_core.*` (already there) | unchanged |
| `Config`, `load_config` | `pal.config` | `agent_core.config.BaseConfig` + `agent_core.config.load_config`. PAL keeps `pal.config.PALConfig` + `pal.config.load_config`. |
| `Daemon`, `_handle_connection`, `_handle_chat`, `_handle_command`, `resolve_channel_id` | `pal.daemon` | `Daemon` → `agent_core.daemon`. `_handle_chat`, `_handle_command` → methods on `pal.agent.PALAgent`. `resolve_channel_id` → `agent_core.daemon`. |
| `DaemonConnection` | `pal.client` | `agent_core.client` |
| `run_repl` (or equivalent CLI loop) | `pal.cli` | `agent_core.adapters.cli.run_repl` |
| `format_*_proposal`, splash | `pal.cli` | stays in `pal.cli` (becomes `PALRenderer`) |

---

# Part 1: agent_core changes (target v0.5.0)

Working directory throughout Part 1: `/home/edible/Projects/agent_core`. Use `.venv/bin/pytest`.

## Task 1: agent_core pre-flight

**Files:** none modified.

- [ ] **Step 1: Confirm clean state**

```bash
cd /home/edible/Projects/agent_core
git status
git log --oneline -3
```

Expected: clean working tree on `main`. HEAD is at or near the v0.4.0 merge.

- [ ] **Step 2: Pull latest**

```bash
git fetch origin
git checkout main
git pull
```

Expected: HEAD includes the Phase D merge commit.

- [ ] **Step 3: Create the feature branch**

```bash
git checkout -b feature/phase-e-runtime
```

- [ ] **Step 4: Run baseline tests**

```bash
.venv/bin/pytest -x
```

Expected: all 290 (or more) tests pass.

---

## Task 2: Add `agent_core.config.BaseConfig`

**Files:**
- Create: `agent_core/agent_core/config.py`
- Create: `agent_core/tests/test_config.py`

A dataclass that mirrors PAL's currently-shared fields, plus a name-derived env-var loader.

- [ ] **Step 1: Write the failing tests**

Create `agent_core/tests/test_config.py`:

```python
"""Tests for agent_core.config.BaseConfig and load_config."""
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent_core.config import BaseConfig, load_config


def test_baseconfig_defaults():
    cfg = BaseConfig()
    assert cfg.inference_url == "http://192.168.1.14:11434"
    assert cfg.history_depth == 50
    assert cfg.max_response_tokens == 4096
    assert cfg.batch_enabled is False
    assert cfg.socket_path is None  # Resolved by load_config based on agent_name.


def test_load_config_no_env_uses_defaults(monkeypatch, tmp_path):
    # Wipe any PAL_* env vars that might leak in.
    for key in list(os.environ):
        if key.startswith("PAL_"):
            monkeypatch.delenv(key, raising=False)
    cfg = load_config(BaseConfig, agent_name="pal")
    assert cfg.inference_url == "http://192.168.1.14:11434"
    # socket_path should be derived since not explicitly set.
    assert cfg.socket_path is not None
    assert str(cfg.socket_path).endswith("pal.sock")


def test_load_config_env_override_str(monkeypatch):
    monkeypatch.setenv("PAL_INFERENCE_URL", "http://example:1234")
    cfg = load_config(BaseConfig, agent_name="pal")
    assert cfg.inference_url == "http://example:1234"


def test_load_config_env_override_int(monkeypatch):
    monkeypatch.setenv("PAL_HISTORY_DEPTH", "200")
    cfg = load_config(BaseConfig, agent_name="pal")
    assert cfg.history_depth == 200


def test_load_config_env_override_bool(monkeypatch):
    monkeypatch.setenv("PAL_BATCH_ENABLED", "true")
    cfg = load_config(BaseConfig, agent_name="pal")
    assert cfg.batch_enabled is True


def test_load_config_env_override_path(monkeypatch, tmp_path):
    monkeypatch.setenv("PAL_VAULT_PATH", str(tmp_path))
    cfg = load_config(BaseConfig, agent_name="pal")
    assert cfg.vault_path == tmp_path


def test_load_config_prefix_derived_from_agent_name(monkeypatch):
    monkeypatch.setenv("RELAB_INFERENCE_URL", "http://relab:5555")
    cfg = load_config(BaseConfig, agent_name="re-lab")  # hyphen
    # Hyphen converted to underscore in prefix derivation.
    assert cfg.inference_url == "http://relab:5555"


def test_load_config_prefix_explicit_override(monkeypatch):
    monkeypatch.setenv("MYPREFIX_INFERENCE_URL", "http://my:6666")
    cfg = load_config(BaseConfig, agent_name="ignored", env_prefix="MYPREFIX_")
    assert cfg.inference_url == "http://my:6666"


def test_load_config_socket_path_explicit(monkeypatch):
    monkeypatch.setenv("PAL_SOCKET_PATH", "/tmp/custom.sock")
    cfg = load_config(BaseConfig, agent_name="pal")
    assert cfg.socket_path == Path("/tmp/custom.sock")


def test_load_config_subclass_extra_field(monkeypatch):
    @dataclass
    class MyConfig(BaseConfig):
        my_extra: int = 42

    monkeypatch.setenv("PAL_MY_EXTRA", "999")
    cfg = load_config(MyConfig, agent_name="pal")
    assert cfg.my_extra == 999
    # BaseConfig fields still load correctly via the same prefix.
    assert cfg.inference_url == "http://192.168.1.14:11434"  # unchanged from default
```

- [ ] **Step 2: Run tests to verify failure**

```bash
.venv/bin/pytest tests/test_config.py -v
```

Expected: all fail with `ModuleNotFoundError: agent_core.config`.

- [ ] **Step 3: Create the config module**

Create `agent_core/agent_core/config.py`:

```python
"""Base configuration for agent_core agents.

Defines a dataclass with the universally-shared infrastructure fields and an
env-var loader that derives env-var prefix from the agent's name (e.g. `pal` →
`PAL_`, `re-lab` → `RELAB_`). Agents subclass `BaseConfig` to add domain
fields; the same loader supports any subclass.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import get_type_hints


def _default_socket_path(agent_name: str) -> Path:
    """Derive the default socket path: $XDG_RUNTIME_DIR/<agent_name>.sock."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return Path(runtime_dir) / f"{agent_name}.sock"


@dataclass
class BaseConfig:
    """Universally-shared agent configuration.

    Subclass to add domain fields. Field names map to env vars as
    `<PREFIX><FIELD_NAME_UPPER>`, where the prefix is derived from the agent
    name unless explicitly overridden.
    """
    inference_url: str = "http://192.168.1.14:11434"
    model: str = "Qwen3.5-35B-A3B-Q4_K_M"
    socket_path: Path | None = None
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


def _coerce(field_type, raw: str):
    """Coerce a raw env-var string to a typed value based on the field type."""
    # Resolve forward-referenced type strings (PEP 563-ish behavior).
    if isinstance(field_type, type):
        if field_type is int:
            return int(raw)
        if field_type is bool:
            return raw.strip().lower() in ("true", "1", "yes")
        if field_type is Path:
            return Path(raw)
        if field_type is str:
            return raw
    # Unions like `Path | None`: try each member.
    origin = getattr(field_type, "__origin__", None)
    args = getattr(field_type, "__args__", ())
    if args:
        for a in args:
            if a is type(None):
                continue
            try:
                return _coerce(a, raw)
            except (TypeError, ValueError):
                continue
    return raw  # fallback: treat as string


def load_config(
    config_cls: type[BaseConfig],
    agent_name: str,
    env_prefix: str | None = None,
) -> BaseConfig:
    """Load `config_cls` from env vars.

    The env-var prefix is `<agent_name.upper().replace('-', '_')>_` unless
    `env_prefix` is supplied explicitly. The returned config has its
    `socket_path` derived from `agent_name` if not set via env var.
    """
    prefix = (
        env_prefix
        if env_prefix is not None
        else f"{agent_name.upper().replace('-', '_')}_"
    )
    type_hints = get_type_hints(config_cls)
    kwargs: dict = {}
    for f in fields(config_cls):
        env_name = f"{prefix}{f.name.upper()}"
        if env_name not in os.environ:
            continue
        raw = os.environ[env_name]
        field_type = type_hints.get(f.name, str)
        kwargs[f.name] = _coerce(field_type, raw)
    cfg = config_cls(**kwargs)
    if cfg.socket_path is None:
        cfg.socket_path = _default_socket_path(agent_name)
    return cfg
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_config.py -v
```

Expected: all 10 tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent_core/config.py tests/test_config.py
git commit -m "feat(config): add BaseConfig + name-prefixed env loader"
```

---

## Task 3: Add `agent_core.agent.Agent` and `HandlerContext`

**Files:**
- Create: `agent_core/agent_core/agent.py`
- Create: `agent_core/tests/test_agent.py`

The Agent base class declares the framework-attribute slots (populated by `run_daemon`), exposes the four override points (`setup`, `system_prompt`, `handle_chat`, `handle_command`), and provides a default `decide_mode` that delegates to `agent_core.reasoning`.

- [ ] **Step 1: Write the failing tests**

Create `agent_core/tests/test_agent.py`:

```python
"""Tests for agent_core.agent.Agent + HandlerContext."""
import asyncio

import pytest

from agent_core.agent import Agent, HandlerContext
from agent_core.config import BaseConfig
from agent_core.conversation import Conversation


def test_agent_setup_default_is_noop():
    """Default Agent.setup() does nothing and doesn't raise."""
    class MyAgent(Agent):
        name = "test"

    a = MyAgent()
    a.setup()  # should not raise


def test_agent_handle_chat_default_raises():
    class MyAgent(Agent):
        name = "test"

    a = MyAgent()

    async def consume():
        async for _ in a.handle_chat(None, None):
            pass

    with pytest.raises(NotImplementedError):
        asyncio.run(consume())


def test_agent_handle_command_default_raises():
    class MyAgent(Agent):
        name = "test"

    a = MyAgent()

    async def consume():
        async for _ in a.handle_command(None, None):
            pass

    with pytest.raises(NotImplementedError):
        asyncio.run(consume())


def test_agent_system_prompt_default_raises():
    class MyAgent(Agent):
        name = "test"

    a = MyAgent()
    with pytest.raises(NotImplementedError):
        a.system_prompt(None)


def test_agent_decide_mode_delegates_to_reasoning():
    """Default decide_mode returns whatever agent_core.reasoning.decide_mode returns."""
    class MyAgent(Agent):
        name = "test"

    a = MyAgent()
    conv = Conversation(history_depth=10)
    # No reasoning override on conversation; default mode is "auto".
    result = a.decide_mode(conv)
    assert result in ("on", "off", "auto")


def test_agent_subclass_can_override_setup():
    class MyAgent(Agent):
        name = "test"

        def setup(self):
            self.custom = "wired"

    a = MyAgent()
    a.setup()
    assert a.custom == "wired"


def test_handler_context_carries_conversation_and_channel():
    conv = Conversation(history_depth=10)
    ctx = HandlerContext(conversation=conv, channel_id="C1", writer=None)
    assert ctx.conversation is conv
    assert ctx.channel_id == "C1"
    assert ctx.writer is None
```

- [ ] **Step 2: Run tests to verify failure**

```bash
.venv/bin/pytest tests/test_agent.py -v
```

Expected: all fail with `ModuleNotFoundError: agent_core.agent`.

- [ ] **Step 3: Create the agent module**

Create `agent_core/agent_core/agent.py`:

```python
"""Agent base class and HandlerContext.

The Agent is the extension surface for agent_core consumers. Subclasses set
`name` (and optionally `env_prefix`), implement the four override points, and
pass an instance to `run_daemon`. Framework managers (profile, wisdom,
learning, allowlist, approval_registry, channels, inference, retrieval,
websearch) are populated on the agent instance by `run_daemon` before
`setup()` runs, so `setup()` can use them to construct domain-specific
resources.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, ClassVar

from agent_core.config import BaseConfig
from agent_core.conversation import Conversation


@dataclass
class HandlerContext:
    """Per-turn context passed to handle_chat / handle_command.

    Carries the live Conversation, the resolved channel_id, and an
    `asyncio.StreamWriter` reference for streaming partial responses
    (StreamChunkMessage etc.) to the connected client mid-turn.
    """
    conversation: Conversation
    channel_id: str
    writer: object   # asyncio.StreamWriter; framework-internal


class Agent:
    """Base class for agent_core agents.

    Required attributes (set by subclasses):
        name: short slug for the agent (e.g. "pal", "re-lab")
        env_prefix: optional explicit env var prefix; if None, derived from name

    Framework attributes (populated by run_daemon before setup):
        config, profile, wisdom, learning, allowlist, approval_registry,
        channels, inference, retrieval, websearch
    """

    name: ClassVar[str]
    env_prefix: ClassVar[str | None] = None

    config: BaseConfig

    def setup(self) -> None:
        """Override to construct domain-specific resources. Framework managers
        are already populated when this runs."""
        pass

    def system_prompt(self, ctx: HandlerContext) -> str:
        """Return the system prompt for this turn. Override per agent."""
        raise NotImplementedError

    async def handle_chat(
        self, msg, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle a ChatMessage. Yield response messages (StreamChunk, Response,
        Error, ToolProgress, agent-specific proposal types)."""
        raise NotImplementedError
        yield  # pragma: no cover  (makes the function an async generator)

    async def handle_command(
        self, msg, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle a CommandMessage. Yield response messages."""
        raise NotImplementedError
        yield  # pragma: no cover

    def decide_mode(self, conversation: Conversation) -> str:
        """Return 'on' / 'off' / 'auto' for reasoning mode. Default delegates
        to agent_core.reasoning.decide_mode."""
        from agent_core.reasoning import decide_mode
        return decide_mode(conversation)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_agent.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent_core/agent.py tests/test_agent.py
git commit -m "feat(agent): add Agent base class + HandlerContext"
```

---

## Task 4: Move `pal/client.py` → `agent_core/client.py`

**Files:**
- Create: `agent_core/agent_core/client.py`
- Create: `agent_core/tests/test_client.py`

Mechanical move. The PAL client just wraps an asyncio unix socket connection with NDJSON encoding via `agent_core.protocol`.

- [ ] **Step 1: Read PAL's current client to confirm shape**

Use the Read tool on `/home/edible/Projects/PAL/pal/client.py`. Confirm it provides a `DaemonConnection` class with `connect()`, `send(msg)`, `receive() -> AsyncIterator`, `close()`. If the shape differs, STOP and ask.

- [ ] **Step 2: Write the failing tests**

Create `agent_core/tests/test_client.py`:

```python
"""Tests for agent_core.client.DaemonConnection."""
import asyncio
from pathlib import Path

import pytest

from agent_core.client import DaemonConnection
from agent_core.protocol import (
    ChatMessage,
    ResponseMessage,
    encode_message,
)


@pytest.mark.asyncio
async def test_connection_round_trip(tmp_path):
    """Connect to a fake unix server, send a chat, receive a response."""
    socket_path = tmp_path / "test.sock"

    async def fake_server(reader, writer):
        line = await reader.readline()
        # Echo back a ResponseMessage.
        writer.write(encode_message(ResponseMessage(text="echo")))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(fake_server, path=str(socket_path))
    try:
        conn = DaemonConnection(socket_path)
        await conn.connect()
        await conn.send(ChatMessage(text="hi"))

        responses = []
        async for msg in conn.receive():
            responses.append(msg)
        await conn.close()

        assert len(responses) == 1
        assert isinstance(responses[0], ResponseMessage)
        assert responses[0].text == "echo"
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_connection_send_before_connect_raises(tmp_path):
    conn = DaemonConnection(tmp_path / "missing.sock")
    with pytest.raises(AssertionError):
        await conn.send(ChatMessage(text="hi"))


@pytest.mark.asyncio
async def test_connection_receive_streams_multiple(tmp_path):
    """A server sending N messages results in N items from receive()."""
    socket_path = tmp_path / "multi.sock"

    async def fake_server(reader, writer):
        for i in range(3):
            writer.write(encode_message(ResponseMessage(text=f"msg{i}")))
            await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(fake_server, path=str(socket_path))
    try:
        conn = DaemonConnection(socket_path)
        await conn.connect()
        msgs = [m async for m in conn.receive()]
        await conn.close()
        assert [m.text for m in msgs] == ["msg0", "msg1", "msg2"]
    finally:
        server.close()
        await server.wait_closed()
```

- [ ] **Step 3: Run tests to verify failure**

```bash
.venv/bin/pytest tests/test_client.py -v
```

Expected: fail with `ModuleNotFoundError: agent_core.client`.

- [ ] **Step 4: Create the client module**

Create `agent_core/agent_core/client.py`:

```python
"""Socket client for connecting to an agent_core daemon."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator

from agent_core.protocol import (
    STREAM_BUFFER_LIMIT,
    decode_message,
    encode_message,
)


class DaemonConnection:
    """Async unix-socket connection to an agent_core daemon.

    Use as `await conn.connect()`, then `await conn.send(msg)` and
    `async for msg in conn.receive()`. Call `await conn.close()` when done.
    """

    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None

    async def connect(self) -> None:
        """Open the unix socket connection."""
        self.reader, self.writer = await asyncio.open_unix_connection(
            path=str(self.socket_path), limit=STREAM_BUFFER_LIMIT,
        )

    async def send(self, msg: object) -> None:
        """Send one message (NDJSON-encoded) to the daemon."""
        assert self.writer is not None, "connect() before send()"
        self.writer.write(encode_message(msg))
        await self.writer.drain()

    async def receive(self) -> AsyncIterator[object]:
        """Yield messages from the daemon until the connection closes."""
        assert self.reader is not None, "connect() before receive()"
        while not self.reader.at_eof():
            line = await self.reader.readline()
            if not line:
                break
            yield decode_message(line.rstrip(b"\n"))

    async def close(self) -> None:
        """Close the writer half cleanly. Safe to call multiple times."""
        if self.writer is not None:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_client.py -v
```

Expected: 3 tests pass.

- [ ] **Step 6: Commit**

```bash
git add agent_core/client.py tests/test_client.py
git commit -m "feat(client): add DaemonConnection (lifted from pal/client.py)"
```

---

## Task 5: Add `agent_core.daemon.Daemon`

**Files:**
- Create: `agent_core/agent_core/daemon.py`
- Create: `agent_core/tests/test_daemon.py`

Thin transport-only daemon. Connection lifecycle, NDJSON decode, dispatch to agent handlers, NDJSON encode, disconnect cleanup. Per-channel preemption is reserved for the deferred safety fix.

- [ ] **Step 1: Write the failing tests**

Create `agent_core/tests/test_daemon.py`:

```python
"""Tests for agent_core.daemon.Daemon."""
import asyncio
from pathlib import Path
from typing import AsyncIterator

import pytest

from agent_core.agent import Agent, HandlerContext
from agent_core.channels import ChannelStore
from agent_core.config import BaseConfig
from agent_core.daemon import Daemon, resolve_channel_id
from agent_core.protocol import (
    ChatMessage,
    CommandMessage,
    ErrorMessage,
    ResponseMessage,
    decode_message,
    encode_message,
)


def test_resolve_channel_id_default_for_none():
    assert resolve_channel_id(None) == "cli-default"


def test_resolve_channel_id_default_for_empty():
    assert resolve_channel_id("") == "cli-default"


def test_resolve_channel_id_passes_valid():
    assert resolve_channel_id("C1") == "C1"


def test_resolve_channel_id_falls_back_for_invalid():
    assert resolve_channel_id("../etc/passwd") == "cli-default"


class _StubAgent(Agent):
    """Minimal Agent that records dispatched messages."""
    name = "test"

    def __init__(self):
        self.chat_msgs: list = []
        self.command_msgs: list = []

    async def handle_chat(self, msg, ctx) -> AsyncIterator[object]:
        self.chat_msgs.append((msg, ctx.channel_id))
        yield ResponseMessage(text=f"handled: {msg.text}")

    async def handle_command(self, msg, ctx) -> AsyncIterator[object]:
        self.command_msgs.append((msg, ctx.channel_id))
        yield ResponseMessage(text=f"cmd: {msg.name}")


def _wire_minimal_agent(tmp_path: Path) -> _StubAgent:
    """Construct a stub agent with the minimum framework attrs the daemon needs."""
    agent = _StubAgent()
    cfg = BaseConfig()
    cfg.vault_path = tmp_path
    cfg.socket_path = tmp_path / "test.sock"
    cfg.history_depth = 50
    agent.config = cfg
    agent.channels = ChannelStore(
        vault_path=tmp_path, agent_name="test", history_depth=50,
    )
    return agent


@pytest.mark.asyncio
async def test_daemon_dispatches_chat(tmp_path):
    agent = _wire_minimal_agent(tmp_path)
    daemon = Daemon(agent)
    server = await asyncio.start_unix_server(
        daemon._handle_connection, path=str(agent.config.socket_path),
    )
    try:
        reader, writer = await asyncio.open_unix_connection(
            path=str(agent.config.socket_path),
        )
        writer.write(encode_message(ChatMessage(text="hello", channel_id="C1")))
        await writer.drain()

        line = await reader.readline()
        msg = decode_message(line.rstrip(b"\n"))
        assert isinstance(msg, ResponseMessage)
        assert msg.text == "handled: hello"

        writer.close()
        await writer.wait_closed()
        # Allow handler task to record before assertion.
        await asyncio.sleep(0.05)
    finally:
        server.close()
        await server.wait_closed()

    assert len(agent.chat_msgs) == 1
    assert agent.chat_msgs[0][1] == "C1"


@pytest.mark.asyncio
async def test_daemon_dispatches_command(tmp_path):
    agent = _wire_minimal_agent(tmp_path)
    daemon = Daemon(agent)
    server = await asyncio.start_unix_server(
        daemon._handle_connection, path=str(agent.config.socket_path),
    )
    try:
        reader, writer = await asyncio.open_unix_connection(
            path=str(agent.config.socket_path),
        )
        writer.write(encode_message(CommandMessage(
            name="help", args="", channel_id="C1",
        )))
        await writer.drain()

        line = await reader.readline()
        msg = decode_message(line.rstrip(b"\n"))
        assert isinstance(msg, ResponseMessage)
        assert msg.text == "cmd: help"

        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.05)
    finally:
        server.close()
        await server.wait_closed()

    assert len(agent.command_msgs) == 1


@pytest.mark.asyncio
async def test_daemon_emits_error_on_decode_failure(tmp_path):
    agent = _wire_minimal_agent(tmp_path)
    daemon = Daemon(agent)
    server = await asyncio.start_unix_server(
        daemon._handle_connection, path=str(agent.config.socket_path),
    )
    try:
        reader, writer = await asyncio.open_unix_connection(
            path=str(agent.config.socket_path),
        )
        # Garbage that's not valid JSON.
        writer.write(b"not-json-at-all\n")
        await writer.drain()

        line = await reader.readline()
        msg = decode_message(line.rstrip(b"\n"))
        assert isinstance(msg, ErrorMessage)
        assert "decode failed" in msg.error

        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()
```

- [ ] **Step 2: Run tests to verify failure**

```bash
.venv/bin/pytest tests/test_daemon.py -v
```

Expected: fail with `ModuleNotFoundError: agent_core.daemon`.

- [ ] **Step 3: Create the daemon module**

Create `agent_core/agent_core/daemon.py`:

```python
"""Generic agent daemon: unix socket server with NDJSON message protocol.

Transport-only. Connection lifecycle, message decode, dispatch to agent
handlers, message encode, disconnect cleanup. The agent owns chat and command
logic; the daemon does not.
"""
from __future__ import annotations

import asyncio
import logging

from agent_core.agent import Agent, HandlerContext
from agent_core.channels import validate_channel_id
from agent_core.protocol import (
    STREAM_BUFFER_LIMIT,
    ChatMessage,
    CommandMessage,
    ErrorMessage,
    decode_message,
    encode_message,
)

logger = logging.getLogger(__name__)


def resolve_channel_id(raw: str | None, default: str = "cli-default") -> str:
    """Validate channel_id, falling back to a default if missing or invalid."""
    if not raw:
        return default
    if not validate_channel_id(raw):
        logger.warning(
            "invalid channel_id %r received; falling back to %s", raw, default,
        )
        return default
    return raw


class Daemon:
    """Transport-only daemon. Owns the socket and dispatches to an Agent."""

    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        # Reserved for the deferred per-channel preemption safety fix; not
        # used by Phase E.
        self._chat_tasks: dict[str, asyncio.Task] = {}

    async def serve(self) -> None:
        """Bind the socket and accept connections forever."""
        socket_path = self.agent.config.socket_path
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        if socket_path.exists():
            socket_path.unlink()
        server = await asyncio.start_unix_server(
            self._handle_connection,
            path=str(socket_path),
            limit=STREAM_BUFFER_LIMIT,
        )
        logger.info("agent %s listening on %s", self.agent.name, socket_path)
        async with server:
            await server.serve_forever()

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        """Per-connection loop: read NDJSON, dispatch, write responses."""
        owned_tasks: list[asyncio.Task] = []
        try:
            while not reader.at_eof():
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = decode_message(line.rstrip(b"\n"))
                except Exception as exc:
                    err = ErrorMessage(error=f"decode failed: {exc}")
                    writer.write(encode_message(err))
                    await writer.drain()
                    continue

                channel_id = resolve_channel_id(getattr(msg, "channel_id", None))
                conv = await self.agent.channels.get_or_create(channel_id)
                ctx = HandlerContext(
                    conversation=conv, channel_id=channel_id, writer=writer,
                )

                if isinstance(msg, ChatMessage):
                    task = asyncio.create_task(
                        self._run_handler(self.agent.handle_chat, msg, ctx, writer),
                    )
                    owned_tasks.append(task)
                elif isinstance(msg, CommandMessage):
                    task = asyncio.create_task(
                        self._run_handler(self.agent.handle_command, msg, ctx, writer),
                    )
                    owned_tasks.append(task)
                else:
                    err = ErrorMessage(
                        error=f"unexpected message type: {type(msg).__name__}",
                    )
                    writer.write(encode_message(err))
                    await writer.drain()

        except (asyncio.CancelledError, ConnectionResetError):
            pass
        except Exception as exc:
            logger.exception("connection handler error: %s", exc)
        finally:
            for t in owned_tasks:
                if not t.done():
                    t.cancel()
            for t in owned_tasks:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _run_handler(self, handler, msg, ctx: HandlerContext, writer) -> None:
        """Invoke an Agent handler, encode each yielded message, write to socket."""
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

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_daemon.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent_core/daemon.py tests/test_daemon.py
git commit -m "feat(daemon): add transport-only Daemon class"
```

---

## Task 6: Add `agent_core.runtime.run_daemon`

**Files:**
- Create: `agent_core/agent_core/runtime.py`
- Create: `agent_core/tests/test_runtime.py`

The entry point that wires framework managers onto the agent and starts the daemon.

- [ ] **Step 1: Write the failing tests**

Create `agent_core/tests/test_runtime.py`:

```python
"""Tests for agent_core.runtime.run_daemon."""
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_core.agent import Agent
from agent_core.config import BaseConfig
from agent_core.runtime import run_daemon


class _StubAgent(Agent):
    name = "test"
    setup_was_called: bool = False
    setup_saw_managers: dict = {}

    def setup(self):
        type(self).setup_was_called = True
        type(self).setup_saw_managers = {
            "config": self.config,
            "profile": self.profile,
            "wisdom": self.wisdom,
            "channels": self.channels,
            "inference": self.inference,
        }


def test_run_daemon_populates_managers_then_calls_setup(monkeypatch, tmp_path):
    """run_daemon wires every framework manager before invoking setup."""
    monkeypatch.setenv("TEST_VAULT_PATH", str(tmp_path))
    monkeypatch.setenv("TEST_SOCKET_PATH", str(tmp_path / "test.sock"))

    # Stub out asyncio.run so the daemon doesn't actually serve.
    with patch("agent_core.runtime.asyncio.run") as mock_run:
        agent = _StubAgent()
        run_daemon(agent)
        mock_run.assert_called_once()

    assert _StubAgent.setup_was_called
    seen = _StubAgent.setup_saw_managers
    assert seen["config"] is not None
    assert seen["profile"] is not None
    assert seen["wisdom"] is not None
    assert seen["channels"] is not None
    assert seen["inference"] is not None


def test_run_daemon_uses_subclassed_config(monkeypatch, tmp_path):
    """run_daemon accepts a subclass of BaseConfig and reads its extra fields."""
    from dataclasses import dataclass

    @dataclass
    class MyConfig(BaseConfig):
        my_extra: str = "default"

    monkeypatch.setenv("TEST_MY_EXTRA", "from-env")
    monkeypatch.setenv("TEST_VAULT_PATH", str(tmp_path))
    monkeypatch.setenv("TEST_SOCKET_PATH", str(tmp_path / "test.sock"))

    with patch("agent_core.runtime.asyncio.run"):
        agent = _StubAgent()
        run_daemon(agent, config_cls=MyConfig)

    assert agent.config.my_extra == "from-env"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
.venv/bin/pytest tests/test_runtime.py -v
```

Expected: fail with `ModuleNotFoundError: agent_core.runtime`.

- [ ] **Step 3: Create the runtime module**

Create `agent_core/agent_core/runtime.py`:

```python
"""run_daemon: the agent_core entry point.

Constructs framework managers from BaseConfig, populates them on the agent,
calls Agent.setup() to let the agent construct domain-specific resources,
then starts the daemon.
"""
from __future__ import annotations

import asyncio
import logging

from agent_core.agent import Agent
from agent_core.allowlist import AllowlistManager
from agent_core.approval_registry import ApprovalRegistry
from agent_core.channels import ChannelStore
from agent_core.config import BaseConfig, load_config
from agent_core.daemon import Daemon
from agent_core.inference import InferenceClient
from agent_core.learning import LearningManager
from agent_core.profile import ProfileManager
from agent_core.retrieval import RetrievalClient
from agent_core.websearch import WebSearchClient
from agent_core.wisdom import WisdomManager

logger = logging.getLogger(__name__)


def run_daemon(
    agent: Agent, config_cls: type[BaseConfig] = BaseConfig,
) -> None:
    """Construct managers, wire onto agent, call setup, start daemon."""
    config = load_config(
        config_cls, agent_name=agent.name, env_prefix=agent.env_prefix,
    )
    agent.config = config
    agent.profile = ProfileManager(
        config.vault_path, agent_name=agent.name, username=config.username,
    )
    agent.wisdom = WisdomManager(config.vault_path, agent_name=agent.name)
    agent.learning = LearningManager(config.vault_path, agent_name=agent.name)
    agent.allowlist = AllowlistManager(config.vault_path, agent_name=agent.name)
    agent.approval_registry = ApprovalRegistry()
    agent.channels = ChannelStore(
        vault_path=config.vault_path,
        agent_name=agent.name,
        history_depth=config.history_depth,
    )
    agent.inference = InferenceClient(
        base_url=config.inference_url, model=config.model,
    )
    agent.retrieval = RetrievalClient(
        base_url=config.inference_url, collection_id=config.collection_id,
    )
    agent.websearch = WebSearchClient(base_url=config.searxng_url)
    agent.setup()

    logging.basicConfig(level=logging.INFO)
    daemon = Daemon(agent)
    asyncio.run(daemon.serve())
```

Note: the manager constructor signatures (`ProfileManager(vault_path, agent_name, username=...)`, `WisdomManager(vault_path, agent_name=...)`, etc.) match what Phase C established. Verify by reading each manager's `__init__` if uncertain.

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_runtime.py -v
```

Expected: 2 tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent_core/runtime.py tests/test_runtime.py
git commit -m "feat(runtime): add run_daemon entry point"
```

---

## Task 7: Add `agent_core.adapters.cli`

**Files:**
- Create: `agent_core/agent_core/adapters/__init__.py`
- Create: `agent_core/agent_core/adapters/cli.py`
- Create: `agent_core/tests/test_cli.py`

Generic REPL with a Renderer plug-in protocol.

- [ ] **Step 1: Create the adapters package marker**

Create `agent_core/agent_core/adapters/__init__.py` (empty file).

- [ ] **Step 2: Write the failing tests**

Create `agent_core/tests/test_cli.py`:

```python
"""Tests for agent_core.adapters.cli."""
import asyncio

import pytest

from agent_core.adapters.cli import Renderer, _default_format
from agent_core.protocol import (
    ChatMessage,
    CommandMessage,
    ErrorMessage,
    LearningCandidateProposalMessage,
    ResponseMessage,
    StreamChunkMessage,
    ToolProgressMessage,
)


def test_renderer_protocol_satisfied_by_simple_class():
    class MyRenderer:
        def splash(self) -> str:
            return "hi"
        def format_message(self, msg) -> str | None:
            return None

    assert isinstance(MyRenderer(), Renderer)


def test_default_format_stream_chunk():
    out = _default_format(StreamChunkMessage(token="hello "))
    assert out == "hello "


def test_default_format_response():
    out = _default_format(ResponseMessage(text="answer"))
    assert out == "answer"


def test_default_format_error():
    out = _default_format(ErrorMessage(error="boom"))
    assert "Error:" in out
    assert "boom" in out


def test_default_format_tool_progress():
    out = _default_format(ToolProgressMessage(tool="search", arguments={"q": "x"}))
    assert "search" in out


def test_default_format_learning_candidate():
    out = _default_format(LearningCandidateProposalMessage(
        proposal_id="a", title="T", body="B", trigger_excerpt="t",
    ))
    assert "T" in out
    assert "B" in out


def test_default_format_unknown_type_falls_back_to_repr():
    """Unknown message types render with type-name fallback so nothing crashes."""
    class Unknown:
        type = "unknown"

    out = _default_format(Unknown())
    assert "unrendered" in out
    assert "Unknown" in out
```

(We test `_default_format` directly because end-to-end REPL testing would require prompt-toolkit interaction. The integration smoke happens during PAL's manual test on the server.)

- [ ] **Step 3: Run tests to verify failure**

```bash
.venv/bin/pytest tests/test_cli.py -v
```

Expected: fail with `ModuleNotFoundError: agent_core.adapters.cli`.

- [ ] **Step 4: Create the cli module**

Create `agent_core/agent_core/adapters/cli.py`:

```python
"""Generic terminal REPL for agent_core daemons.

Connects to the daemon's socket, reads input via prompt-toolkit, sends chat or
command messages, renders streamed responses. Agent-specific message rendering
is delegated to a Renderer protocol; the REPL falls back to default rendering
for messages the renderer doesn't claim.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory

from agent_core.client import DaemonConnection
from agent_core.protocol import (
    ChatMessage,
    CommandMessage,
    ErrorMessage,
    LearningCandidateProposalMessage,
    ResponseMessage,
    StreamChunkMessage,
    ToolProgressMessage,
)


@runtime_checkable
class Renderer(Protocol):
    """Plug-in for agent-specific rendering. The REPL falls back to default
    formatting when format_message returns None."""
    def splash(self) -> str:
        """Return banner text printed at REPL start."""
        ...

    def format_message(self, msg: object) -> str | None:
        """Return formatted text for an agent-specific message, or None to
        defer to the default rendering."""
        ...


def _default_format(msg: object) -> str:
    """Default rendering for the seven generic message types."""
    if isinstance(msg, StreamChunkMessage):
        return msg.token  # printed without newline; concatenated by caller
    if isinstance(msg, ResponseMessage):
        return msg.text
    if isinstance(msg, ErrorMessage):
        return f"Error: {msg.error}"
    if isinstance(msg, ToolProgressMessage):
        return f"  [{msg.tool}({msg.arguments})]"
    if isinstance(msg, LearningCandidateProposalMessage):
        return f"\n[Learning candidate: {msg.title}]\n{msg.body}\n"
    return f"[unrendered {type(msg).__name__}]"


async def run_repl(socket_path: Path, renderer: Renderer) -> None:
    """Connect, run the input loop, render messages until the user exits."""
    print(renderer.splash())
    conn = DaemonConnection(socket_path)
    await conn.connect()
    history_path = Path.home() / ".local" / "state" / "agent_core" / "cli_history"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    session: PromptSession = PromptSession(history=FileHistory(str(history_path)))
    try:
        while True:
            try:
                line = await session.prompt_async("> ")
            except (EOFError, KeyboardInterrupt):
                break
            line = line.strip()
            if not line:
                continue

            if line.startswith("/"):
                parts = line[1:].split(None, 1)
                name = parts[0]
                args = parts[1] if len(parts) > 1 else ""
                await conn.send(CommandMessage(name=name, args=args))
            else:
                await conn.send(ChatMessage(text=line))

            # Drain responses until the daemon signals end-of-turn.
            async for msg in conn.receive():
                rendered = renderer.format_message(msg)
                if rendered is None:
                    rendered = _default_format(msg)
                if isinstance(msg, StreamChunkMessage):
                    print(rendered, end="", flush=True)
                else:
                    print(rendered, flush=True)
                if isinstance(msg, (ResponseMessage, ErrorMessage)):
                    break
    finally:
        await conn.close()
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_cli.py -v
```

Expected: 7 tests pass.

- [ ] **Step 6: Commit**

```bash
git add agent_core/adapters/__init__.py agent_core/adapters/cli.py tests/test_cli.py
git commit -m "feat(cli): add generic REPL with Renderer plug-in"
```

---

## Task 8: Add contract tests

**Files:**
- Create: `agent_core/tests/test_contract.py`

The umbrella spec requires contract tests for the API surface. They live in agent_core forever as the API guarantee.

- [ ] **Step 1: Write the contract tests**

Create `agent_core/tests/test_contract.py`:

```python
"""Contract tests: pinning the agent_core public API surface.

These tests verify the API guarantee. They live in agent_core forever.
A change that breaks one of these tests is by definition a breaking change.
"""
import asyncio
from pathlib import Path
from typing import AsyncIterator

import pytest

from agent_core.agent import Agent, HandlerContext
from agent_core.channels import ChannelStore
from agent_core.config import BaseConfig
from agent_core.daemon import Daemon
from agent_core.protocol import ChatMessage, ResponseMessage, decode_message, encode_message


class _MinimalAgent(Agent):
    """One-line agent: implements the minimum required to handle a chat."""
    name = "minimal"

    async def handle_chat(self, msg, ctx) -> AsyncIterator[object]:
        yield ResponseMessage(text=f"got: {msg.text}")


@pytest.mark.asyncio
async def test_minimal_agent_boots(tmp_path):
    """A trivial Agent subclass with manually-wired channels boots and serves."""
    agent = _MinimalAgent()
    cfg = BaseConfig()
    cfg.vault_path = tmp_path
    cfg.socket_path = tmp_path / "minimal.sock"
    agent.config = cfg
    agent.channels = ChannelStore(
        vault_path=tmp_path, agent_name="minimal", history_depth=10,
    )

    daemon = Daemon(agent)
    server = await asyncio.start_unix_server(
        daemon._handle_connection, path=str(cfg.socket_path),
    )
    try:
        # Verify the socket file exists and is reachable.
        reader, writer = await asyncio.open_unix_connection(path=str(cfg.socket_path))
        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_agent_receives_chat(tmp_path):
    """Sending a ChatMessage to the daemon causes Agent.handle_chat to run."""
    agent = _MinimalAgent()
    cfg = BaseConfig()
    cfg.vault_path = tmp_path
    cfg.socket_path = tmp_path / "minimal.sock"
    agent.config = cfg
    agent.channels = ChannelStore(
        vault_path=tmp_path, agent_name="minimal", history_depth=10,
    )

    daemon = Daemon(agent)
    server = await asyncio.start_unix_server(
        daemon._handle_connection, path=str(cfg.socket_path),
    )
    try:
        reader, writer = await asyncio.open_unix_connection(path=str(cfg.socket_path))
        writer.write(encode_message(ChatMessage(text="hello", channel_id="C1")))
        await writer.drain()
        line = await reader.readline()
        msg = decode_message(line.rstrip(b"\n"))
        assert isinstance(msg, ResponseMessage)
        assert msg.text == "got: hello"
        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_agent_handle_chat_yields_responses(tmp_path):
    """A handler yielding multiple messages results in multiple responses."""
    class MultiResponseAgent(Agent):
        name = "multi"

        async def handle_chat(self, msg, ctx) -> AsyncIterator[object]:
            yield ResponseMessage(text="first")
            yield ResponseMessage(text="second")

    agent = MultiResponseAgent()
    cfg = BaseConfig()
    cfg.vault_path = tmp_path
    cfg.socket_path = tmp_path / "multi.sock"
    agent.config = cfg
    agent.channels = ChannelStore(
        vault_path=tmp_path, agent_name="multi", history_depth=10,
    )

    daemon = Daemon(agent)
    server = await asyncio.start_unix_server(
        daemon._handle_connection, path=str(cfg.socket_path),
    )
    try:
        reader, writer = await asyncio.open_unix_connection(path=str(cfg.socket_path))
        writer.write(encode_message(ChatMessage(text="ping")))
        await writer.drain()

        responses = []
        for _ in range(2):
            line = await asyncio.wait_for(reader.readline(), timeout=2.0)
            responses.append(decode_message(line.rstrip(b"\n")))
        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()

    assert [r.text for r in responses] == ["first", "second"]
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_contract.py -v
```

Expected: 3 tests pass. (No "failing first" step here because each test exercises code that already exists from earlier tasks.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_contract.py
git commit -m "test(contract): pin Agent + Daemon API surface"
```

---

## Task 9: Bump version + CHANGELOG + full suite

**Files:**
- Modify: `agent_core/pyproject.toml`
- Modify: `agent_core/CHANGELOG.md`

- [ ] **Step 1: Bump version**

Edit `agent_core/pyproject.toml`. Change `version = "0.4.0"` to `version = "0.5.0"`.

- [ ] **Step 2: Update CHANGELOG**

Edit `agent_core/CHANGELOG.md`. Prepend above the `## [0.4.0]` entry:

```markdown
## [0.5.0] - 2026-04-30

### Added
- `agent_core.config.BaseConfig`: dataclass with universally-shared agent fields, plus `load_config()` with name-derived env-var prefix machinery (e.g. agent name "pal" reads `PAL_*` env vars).
- `agent_core.agent.Agent`: base class with extension points `setup()`, `system_prompt()`, `handle_chat()`, `handle_command()`, `decide_mode()`. Framework managers (profile, wisdom, learning, allowlist, approval_registry, channels, inference, retrieval, websearch) are populated by `run_daemon()` before `setup()` runs.
- `agent_core.agent.HandlerContext`: per-turn dataclass carrying conversation, channel_id, writer.
- `agent_core.client.DaemonConnection`: async unix-socket client (lifted from PAL).
- `agent_core.daemon.Daemon`: transport-only daemon. Connection lifecycle, NDJSON decode, dispatch to agent handlers, NDJSON encode, disconnect cleanup. Per-channel preemption is reserved (`_chat_tasks` field) for the deferred safety fix.
- `agent_core.runtime.run_daemon()`: entry point that wires framework managers onto the agent and starts the daemon.
- `agent_core.adapters.cli.run_repl()`: generic REPL with a `Renderer` Protocol for agent-specific message rendering. Falls back to default rendering for the seven generic message types.
- Contract tests in `tests/test_contract.py` pinning the API surface.

### Notes
- This release is opt-in for consumers: importing `agent_core.daemon` or `agent_core.runtime` is new and PAL only adopts them in PAL's Phase E migration.
```

- [ ] **Step 3: Run full agent_core suite**

```bash
.venv/bin/pytest -x
```

Expected: all tests pass (Phase A through D, plus all the new Phase E tests).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore: bump version to 0.5.0"
```

---

## Task 10: Push agent_core branch + open PR

- [ ] **Step 1: Push the feature branch**

```bash
cd /home/edible/Projects/agent_core
git push -u origin feature/phase-e-runtime
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --title "Phase E: runtime infrastructure (v0.5.0)" --body "$(cat <<'EOF'
## Summary
Adds the agent runtime infrastructure to agent_core: Agent base class, BaseConfig, run_daemon entry point, transport-only Daemon, socket client, and generic CLI REPL with a Renderer plug-in.

- \`agent_core.config.BaseConfig\` + \`load_config\` with name-derived env-var prefix.
- \`agent_core.agent.Agent\` + \`HandlerContext\` defining the four override points.
- \`agent_core.client.DaemonConnection\` (lifted from PAL).
- \`agent_core.daemon.Daemon\`: transport-only daemon.
- \`agent_core.runtime.run_daemon\`: framework manager wiring + daemon start.
- \`agent_core.adapters.cli.run_repl\` + \`Renderer\` protocol.
- Contract tests pinning the API surface.

Bumps version to 0.5.0.

Spec: PAL repo \`docs/superpowers/specs/2026-04-30-phase-e-runtime-infrastructure-design.md\`.

## Test plan
- [x] All tests pass on the feature branch (~310 tests)
- [ ] PAL Phase E consumer-side migration lands successfully against this tag

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Wait for CI**

```bash
gh pr checks
```

Expected: green.

---

## Task 11: Merge agent_core PR + tag v0.5.0

- [ ] **Step 1: Merge**

```bash
PR_NUM=$(gh pr view --json number --jq .number)
gh api -X PUT repos/EdibleTuber/agent_core/pulls/$PR_NUM/merge --field merge_method=merge
```

- [ ] **Step 2: Update local main and tag**

```bash
cd /home/edible/Projects/agent_core
git checkout main
git pull
MERGE_SHA=$(git rev-parse HEAD)
git tag v0.5.0 $MERGE_SHA
git push origin v0.5.0
```

Expected: tag exists on remote.

- [ ] **Step 3: Verify**

```bash
grep '^version' pyproject.toml
git log --oneline -3
```

Expected: `version = "0.5.0"`. HEAD is the merge commit.

---

# Part 2: PAL changes (consumer-side migration)

Working directory: `/home/edible/Projects/PAL/.worktrees/phase-e-runtime`. Use `/home/edible/Projects/PAL/.venv/bin/pytest`.

## Task 12: Create PAL worktree and pre-flight

**Files:** none modified.

- [ ] **Step 1: Create the feature-branch worktree**

```bash
cd /home/edible/Projects/PAL
git fetch origin
git worktree add .worktrees/phase-e-runtime -b feature/phase-e-runtime origin/main
cd .worktrees/phase-e-runtime
```

- [ ] **Step 2: Confirm clean state**

```bash
git status
```

Expected: clean, on branch `feature/phase-e-runtime`.

- [ ] **Step 3: Confirm baseline tests pass**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest -x \
  --ignore=tests/test_daemon.py \
  --ignore=tests/test_integration.py \
  --ignore=tests/test_chat_research_integration.py \
  --ignore=tests/test_consolidate_integration.py \
  --ignore=tests/test_learning_e2e.py \
  --ignore=tests/test_client.py \
  --ignore=tests/test_prompt_injection.py
```

Expected: all green (~543 tests). The seven excluded tests are pre-existing flakies/hangs.

---

## Task 13: Bump agent_core dep to v0.5.0

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Update dependency**

Read `pyproject.toml`. Find the `agent_core` dependency line. Change the pinned tag from `v0.4.0` to `v0.5.0`.

- [ ] **Step 2: Reinstall**

```bash
/home/edible/Projects/PAL/.venv/bin/pip install -e .
```

- [ ] **Step 3: Verify**

```bash
/home/edible/Projects/PAL/.venv/bin/python -c "
from agent_core.agent import Agent, HandlerContext
from agent_core.config import BaseConfig, load_config
from agent_core.runtime import run_daemon
from agent_core.daemon import Daemon
from agent_core.client import DaemonConnection
from agent_core.adapters.cli import run_repl, Renderer
print('all v0.5.0 imports ok')
"
```

Expected: prints `all v0.5.0 imports ok`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: bump agent_core dependency to v0.5.0"
```

---

## Task 14: Refactor `pal/config.py` to `PALConfig(BaseConfig)`

**Files:**
- Modify: `pal/config.py`
- Modify: `tests/test_config.py` (if it exists)

- [ ] **Step 1: Read current config**

Use Read on `pal/config.py` to confirm its shape. The current `Config` dataclass has 16 fields; PALConfig keeps only `max_inference_body_chars` and inherits everything else from `BaseConfig`. The current `load_config` is replaced by a thin wrapper.

- [ ] **Step 2: Rewrite the file**

Replace the entire contents of `pal/config.py` with:

```python
"""PAL configuration: subclasses agent_core.config.BaseConfig with PAL-specific fields."""
from __future__ import annotations

from dataclasses import dataclass

from agent_core.config import BaseConfig
from agent_core.config import load_config as _load_base_config


@dataclass
class PALConfig(BaseConfig):
    """PAL-specific configuration. All BaseConfig fields are inherited; we only
    add fields PAL alone needs."""
    max_inference_body_chars: int = 20_000


# Backwards-compatible alias for any code still importing `Config`.
Config = PALConfig


def load_config() -> PALConfig:
    """Load PAL config from PAL_* environment variables."""
    return _load_base_config(PALConfig, agent_name="pal")
```

- [ ] **Step 3: Verify imports still work**

```bash
/home/edible/Projects/PAL/.venv/bin/python -c "
from pal.config import Config, PALConfig, load_config
cfg = load_config()
print(type(cfg).__name__, cfg.inference_url, cfg.max_inference_body_chars)
"
```

Expected: `PALConfig http://192.168.1.14:11434 20000` (or whatever PAL_* env vars override to).

- [ ] **Step 4: Commit**

```bash
git add pal/config.py
git commit -m "refactor(config): subclass BaseConfig as PALConfig"
```

---

## Task 15: Create `pal/agent.py` with `PALAgent` skeleton

**Files:**
- Create: `pal/agent.py`

The PALAgent skeleton imports the existing PAL infrastructure (WikiManager, Categorizer, ToolExecutor, etc.) and constructs them in `setup()`. The `system_prompt`, `handle_chat`, and `handle_command` methods are stubs that raise NotImplementedError; they get filled in by Tasks 16 and 17.

- [ ] **Step 1: Read pal/daemon.py constructor for reference**

Use Read on `pal/daemon.py` to find `Daemon.__init__` (around line 100-300). This shows what PAL constructs at startup: `WikiManager`, `SystemPromptBuilder`, `ToolExecutor`, `Categorizer`, `Researcher`, `Compiler`, `LearningScanner` instance, etc. The exact set of attributes to construct in `PALAgent.setup()` is whatever Daemon.__init__ creates (excluding the ones the framework provides: profile, wisdom, learning, allowlist, approval_registry, channels, inference, retrieval, websearch).

- [ ] **Step 2: Create pal/agent.py with skeleton**

Create `pal/agent.py`:

```python
"""PALAgent: PAL's agent_core Agent subclass.

PALAgent owns the PAL-specific infrastructure (wiki manager, categorizer,
researcher, compiler, tool executor, prompt builder, learning scanner) and
implements the chat/command/system-prompt extension points for the
agent_core daemon.
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

from agent_core.agent import Agent, HandlerContext
from agent_core.protocol import (
    ChatMessage,
    CommandMessage,
)

from pal.config import PALConfig

logger = logging.getLogger(__name__)


class PALAgent(Agent):
    """The PAL agent. Subclass of agent_core.agent.Agent."""

    name = "pal"
    config: PALConfig  # type-narrows the framework attr

    def setup(self) -> None:
        """Construct PAL-specific infrastructure. Framework managers
        (profile, wisdom, learning, allowlist, approval_registry, channels,
        inference, retrieval, websearch) are already populated."""
        # Imports inside setup() to avoid load-time cycles with pal.daemon
        # while pal/daemon.py still exists (it's deleted in a later task).
        from pal.wiki import WikiManager
        from pal.prompt_builder import SystemPromptBuilder
        from pal.categorizer import Categorizer
        from pal.researcher import Researcher
        from pal.compiler import Compiler
        from pal.tools import ToolExecutor
        from agent_core.learning_scanner import LearningScanner, extract_candidate
        from agent_core.utils.fetcher import URLFetcher
        from agent_core.utils.converter import DocumentConverter

        self.wiki = WikiManager(self.config.vault_path)

        self.prompt_builder = SystemPromptBuilder(
            profile=self.profile,
            wisdom=self.wisdom,
            learning=self.learning,
            username=self.config.username,
        )

        self.categorizer = Categorizer(
            inference=self.inference,
            allowlist=self.allowlist,
        )

        self.fetcher = URLFetcher(
            allowlist=self.allowlist,
            max_bytes=self.config.fetch_max_bytes,
            timeout=self.config.fetch_timeout,
        )
        self.converter = DocumentConverter()

        self.researcher = Researcher(
            websearch=self.websearch,
            inference=self.inference,
            fetcher=self.fetcher,
            converter=self.converter,
            wiki=self.wiki,
            categorizer=self.categorizer,
            max_inference_body_chars=self.config.max_inference_body_chars,
        )

        self.compiler = Compiler(
            wiki=self.wiki,
            inference=self.inference,
        )

        self.tool_executor = ToolExecutor(
            wiki=self.wiki,
            retrieval=self.retrieval,
            researcher=self.researcher,
            compiler=self.compiler,
            wisdom=self.wisdom,
            learning=self.learning,
            allowlist=self.allowlist,
            fetcher=self.fetcher,
            converter=self.converter,
            scratchpad=None,  # set per-turn in handle_chat
        )

        async def _scanner_extractor(recent_turns, trigger):
            # Closure over self.inference; one place to wire the batch fallback.
            async def _call(prompt):
                completion = await self.inference.complete(
                    [{"role": "user", "content": prompt}],
                    reasoning="off",
                )
                return completion.content or ""
            return await extract_candidate(recent_turns, trigger, _call)

        def _emit_proposal(proposal_msg):
            # Placeholder; the real emission flows through the per-connection
            # writer in handle_chat. We hold this attribute so the scanner
            # has somewhere to call.
            pass

        self.scanner = LearningScanner(
            learning_manager=self.learning,
            extractor=_scanner_extractor,
            emit=_emit_proposal,
        )

    def system_prompt(self, ctx: HandlerContext) -> str:
        """Return PAL's system prompt for this turn. Filled in by Task 16."""
        raise NotImplementedError

    async def handle_chat(
        self, msg: ChatMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle a chat message. Filled in by Task 16."""
        raise NotImplementedError
        yield  # pragma: no cover

    async def handle_command(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle a slash command. Filled in by Task 17."""
        raise NotImplementedError
        yield  # pragma: no cover
```

Note: the exact `setup()` body matches what `Daemon.__init__` constructs today. Read `pal/daemon.py:100-300` and translate each construction. The constructor signatures of `SystemPromptBuilder`, `Categorizer`, `Researcher`, `Compiler`, `ToolExecutor` need verification against their current `__init__` definitions; if any of them takes a different set of arguments than shown above, fix the call to match. Don't change the signatures; just match what they expect.

- [ ] **Step 2: Verify the file imports cleanly**

```bash
/home/edible/Projects/PAL/.venv/bin/python -c "from pal.agent import PALAgent; print('PALAgent import ok')"
```

Expected: prints `PALAgent import ok`. If `ImportError` from any PAL module, that's a real problem; STOP and ask. If `TypeError` from a manager constructor signature mismatch, fix the call to match the manager's actual signature.

- [ ] **Step 3: Commit**

```bash
git add pal/agent.py
git commit -m "feat(agent): add PALAgent skeleton with setup() wiring"
```

---

## Task 16: Lift `_handle_chat` and `system_prompt` from daemon to PALAgent

**Files:**
- Modify: `pal/agent.py`
- Modify: `pal/daemon.py` (no functional change yet; this task only LIFTS, doesn't delete)

This is the largest task in Phase E. PAL's current `Daemon._handle_chat` (around `pal/daemon.py:400-700`) is roughly 250-300 lines of orchestration: build messages, decide mode, complete or stream inference, tool-call loop, scratchpad updates, learning scanner trigger.

- [ ] **Step 1: Read the source**

Use Read on `pal/daemon.py` to locate the `_handle_chat` method (begins around line 415). Read the entire method through to its end (where `tool_calls` handling completes). Note every reference to `self.X` (these become `self.X` on PALAgent, since framework attrs are populated and domain attrs are constructed in setup), and every reference to `conv`, `channel_id`, `writer`, `tool_executor`, `scanner` (these become `ctx.conversation`, `ctx.channel_id`, `ctx.writer`, `self.tool_executor`, `self.scanner`).

Also read PAL's existing prompt-builder usage: search `pal/daemon.py` for `prompt_builder.build` (or similar) to identify what arguments PAL passes when constructing the system prompt. The result becomes `PALAgent.system_prompt`.

- [ ] **Step 2: Implement `system_prompt` on PALAgent**

Edit `pal/agent.py`. Replace the `system_prompt` stub:

```python
    def system_prompt(self, ctx: HandlerContext) -> str:
        """Return PAL's system prompt for this turn, including channel
        scratchpad content."""
        # Construct the per-channel scratchpad to read its current content.
        from agent_core.scratchpad import Scratchpad

        def _commit_scratchpad(path, message):
            self.wiki.git_commit(message)

        scratchpad = Scratchpad(
            vault_path=self.config.vault_path,
            agent_name="pal",
            channel_id=ctx.channel_id,
            max_bytes=self.config.scratchpad_max_bytes,
            commit_callback=_commit_scratchpad,
        )
        scratchpad_content = scratchpad.read()
        return self.prompt_builder.build(channel_scratchpad=scratchpad_content)
```

(The exact arguments to `prompt_builder.build` should match what PAL currently passes. If `pal/daemon.py` invokes `prompt_builder.build(...)` with additional arguments, mirror them here.)

- [ ] **Step 3: Implement `handle_chat` on PALAgent**

Replace the `handle_chat` stub in `pal/agent.py`:

The method body is lifted from `Daemon._handle_chat`. Read `pal/daemon.py:415-700` (approximate range; actual line numbers may differ) and translate:

| In daemon's `_handle_chat` | In PALAgent's `handle_chat` |
|---|---|
| `conv` parameter | `ctx.conversation` |
| `channel_id` parameter | `ctx.channel_id` |
| `writer` parameter | `ctx.writer` |
| `tool_executor` parameter | `self.tool_executor` |
| `scanner` parameter | `self.scanner` |
| `self.config`, `self.wiki`, `self.categorizer`, `self.researcher`, `self.compiler`, `self.prompt_builder` | unchanged (`self.X` on PALAgent) |
| `self.profile`, `self.wisdom`, `self.learning`, `self.allowlist`, `self.approval_registry`, `self.channels`, `self.inference`, `self.retrieval`, `self.websearch` | unchanged (framework-populated) |
| `messages = conv.get_messages_for_api(...)` | `messages = ctx.conversation.get_messages_for_api(...)` |
| `decide_mode(conv)` | `self.decide_mode(ctx.conversation)` (uses agent_core.reasoning via the Agent default) |
| `writer.write(encode_message(...))` for streaming chunks | unchanged |
| Final `return` after writing ResponseMessage | replaces with implicit end of async generator |

The current method signature is `async def _handle_chat(self, msg, conv, channel_id, writer, tool_executor, scanner)`. The new signature is `async def handle_chat(self, msg: ChatMessage, ctx: HandlerContext) -> AsyncIterator[object]`.

The new body needs to construct the per-turn scratchpad once and assign to `self.tool_executor.scratchpad` before any tool calls fire (the existing daemon does this; preserve it).

The stopgap-related lines from the safety fix (the `max_tokens=4096` in three places) come along verbatim. The StreamEnd filter from the hot-fix also comes along verbatim.

Concretely, the body looks roughly like (with the exact tool-call branch elided; lift it verbatim from daemon.py):

```python
    async def handle_chat(
        self, msg: ChatMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        from agent_core.scratchpad import Scratchpad
        from agent_core.protocol import (
            StreamChunkMessage, ResponseMessage, ErrorMessage, ToolProgressMessage,
        )
        from agent_core.inference import StreamEnd

        conv = ctx.conversation
        writer = ctx.writer
        channel_id = ctx.channel_id

        conv.add_user(msg.text)
        mode = self.decide_mode(conv)

        def _commit_scratchpad(path, message):
            self.wiki.git_commit(message)

        scratchpad = Scratchpad(
            vault_path=self.config.vault_path,
            agent_name="pal",
            channel_id=channel_id,
            max_bytes=self.config.scratchpad_max_bytes,
            commit_callback=_commit_scratchpad,
        )
        self.tool_executor.scratchpad = scratchpad

        messages = conv.get_messages_for_api(
            system_prompt=self.prompt_builder.build(
                channel_scratchpad=scratchpad.read(),
            ),
        )
        max_tool_rounds = 50

        full_response: list[str] = []
        tool_calls = None

        try:
            if mode == "on":
                # Reasoning-on path: non-streaming complete().
                completion = await self.inference.complete(
                    messages, tools=None,  # PAL's tool defs go here; lift from daemon
                    reasoning=mode, max_tokens=4096,
                )
                # ... lift the rest of the reasoning-on branch from daemon:425-475
                # (verbatim except for the parameter substitutions documented above)
            else:
                # Streaming path.
                async for item in self.inference.stream(
                    messages, tools=None,  # lift TOOL_DEFINITIONS reference
                    reasoning=mode, max_tokens=4096,
                ):
                    if isinstance(item, list):
                        tool_calls = item
                        break
                    if isinstance(item, StreamEnd):
                        break
                    chunk = StreamChunkMessage(token=item)
                    writer.write(...)  # lift from daemon
                    await writer.drain()
                    full_response.append(item)
                # ... lift post-stream logic (response if no tool_calls;
                # otherwise enter the tool-call loop)

            # The tool-call loop: lift from daemon's tool-call branch.
            # ...

            # Trigger the learning scan (fire-and-forget, async task).
            recent_turns = conv.get_messages_for_api(system_prompt="")[-6:]
            asyncio.create_task(self.scanner.maybe_scan(
                recent_turns=recent_turns,
                latest_user_message=msg.text,
            ))

        except Exception as exc:
            logger.exception("chat turn failed: %s", exc)
            yield ErrorMessage(error=f"Chat error: {exc}")
            return
```

The complete body is roughly 250 lines once all the tool-call branch and reasoning-on branch are lifted. The pattern is mechanical: read each section of `_handle_chat`, copy with parameter substitutions, paste.

The handler is an `async def` returning an `AsyncIterator`. Most of PAL's chat orchestration writes to `writer` directly mid-turn (for streaming UX) and only `yield`s for cases where a non-streaming message needs to come out (Error responses, the final Response message in some branches). This is consistent with the daemon's writer-passing approach. The daemon will iterate the generator and write whatever it yields, plus PAL's direct writes happen as side effects during the generator's body.

Note: the PAL daemon today doesn't yield messages back to a caller; it writes to the socket directly. To convert to the async-generator pattern without breaking streaming UX, the body keeps the direct `writer.write(...)` calls (these become side effects during iteration), and uses `yield` only for messages that the existing daemon would have called through the same `writer.write(encode_message(...))` path. In effect: lift the whole body, replace the few `return` statements with implicit end-of-generator, leave everything else unchanged.

- [ ] **Step 4: Verify imports and basic instantiation**

```bash
/home/edible/Projects/PAL/.venv/bin/python -c "
from pal.agent import PALAgent
agent = PALAgent()
print('PALAgent instantiates:', agent.name)
"
```

Expected: prints `PALAgent instantiates: pal`. If `setup()` fails (because framework attrs aren't populated), that's expected; we never call setup directly here.

- [ ] **Step 5: Run existing chat tests against the still-running old daemon path**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest tests/test_daemon_channels.py tests/test_daemon_scanner_hook.py tests/test_daemon_scanner_approval.py -v
```

Expected: pass. (These test the Daemon class which is still wired; PALAgent isn't called yet because daemon_main hasn't been switched.)

- [ ] **Step 6: Commit**

```bash
git add pal/agent.py
git commit -m "feat(agent): lift _handle_chat and system_prompt to PALAgent"
```

---

## Task 17: Lift `_handle_command` from daemon to PALAgent

**Files:**
- Modify: `pal/agent.py`

PAL's command dispatch is around `pal/daemon.py:660-1000` (approximate). It's a series of `if msg.name == "research":` branches plus a few helper methods.

- [ ] **Step 1: Read the source**

Use Read on `pal/daemon.py` around `_handle_connection`'s `CommandMessage` branch, plus any `_handle_*` methods it calls (e.g. `_handle_research`, `_handle_compile`, `_handle_consolidate`, `_handle_reorg`, `_handle_promote`, `_handle_think`, `_handle_scratch`, `_handle_help`, `_handle_clear`, `_handle_model`, `_handle_research_mode`, `_handle_import`, etc.).

Note all the methods. The `handle_command` method dispatches based on `msg.name` and calls each.

- [ ] **Step 2: Add method bodies**

Replace the `handle_command` stub on `PALAgent`:

```python
    async def handle_command(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Dispatch a slash command to its handler."""
        from pal.commands import COMMANDS

        # COMMANDS is a list of (name, handler_method_name, help_text) tuples
        # or similar. Use Read to confirm shape; if the dispatch is just a
        # string-key lookup, copy the dispatch logic from daemon._handle_connection
        # and replace the call sites' `self._handle_X` with method calls on
        # PALAgent itself.

        dispatcher = {name: getattr(self, handler_name) for name, handler_name, _ in COMMANDS}
        handler = dispatcher.get(msg.name)
        if handler is None:
            from agent_core.protocol import ErrorMessage
            yield ErrorMessage(error=f"unknown command: {msg.name}")
            return
        async for response in handler(msg, ctx):
            yield response
```

Then lift each `_handle_*` method body from `pal/daemon.py` into PALAgent. The methods become `async def _handle_X(self, msg, ctx) -> AsyncIterator[object]` with the same parameter substitutions documented in Task 16's table.

If any `_handle_X` method uses helpers PAL imports at module scope in `pal/daemon.py` (e.g., `archive_raw_files`, `summarize_raw_file`, helpers from `pal.article`), import them locally in the method body or at the top of `pal/agent.py`.

- [ ] **Step 3: Verify imports**

```bash
/home/edible/Projects/PAL/.venv/bin/python -c "
from pal.agent import PALAgent
agent = PALAgent()
# Verify the dispatcher attribute exists post-import.
print('handle_command attr present:', hasattr(agent, 'handle_command'))
print('lifted commands:', dir(agent))
"
```

Expected: prints handler methods (like `_handle_research`, `_handle_compile`, etc.) on the agent.

- [ ] **Step 4: Run command-related tests**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest tests/test_chat_compile_tools.py tests/test_chat_reorg_tools.py tests/test_cli_research_proposal.py tests/test_cli_batch_fallback.py tests/test_learning_commands.py -v
```

Expected: pass (these tests exercise command dispatch through the existing Daemon class, not PALAgent yet, but they verify the underlying handlers work).

- [ ] **Step 5: Commit**

```bash
git add pal/agent.py
git commit -m "feat(agent): lift command dispatch and _handle_* methods to PALAgent"
```

---

## Task 18: Switch `pal/daemon_main.py` to `run_daemon(PALAgent)`

**Files:**
- Modify: `pal/daemon_main.py`

This is the cutover. After this commit, `pal-daemon` is served by `agent_core.daemon.Daemon` calling `PALAgent.handle_chat` / `PALAgent.handle_command`, not by `pal.daemon.Daemon` anymore.

- [ ] **Step 1: Read current daemon_main**

Use Read on `pal/daemon_main.py`. It's likely an entry-point script that constructs `pal.daemon.Daemon` and runs it.

- [ ] **Step 2: Rewrite daemon_main**

Replace the entire file with:

```python
"""Entry point for pal-daemon.

Constructs PALAgent, hands it to agent_core.runtime.run_daemon, blocks on the
daemon's serve loop.
"""
from agent_core.runtime import run_daemon

from pal.agent import PALAgent
from pal.config import PALConfig


def main() -> None:
    run_daemon(PALAgent(), config_cls=PALConfig)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Verify it imports**

```bash
/home/edible/Projects/PAL/.venv/bin/python -c "from pal.daemon_main import main; print('main import ok')"
```

Expected: prints `main import ok`.

- [ ] **Step 4: Run a smoke check (the daemon starts but immediately exits because we don't actually invoke main)**

```bash
/home/edible/Projects/PAL/.venv/bin/python -c "
from pal.daemon_main import main
import inspect
# Just verify the function's reachable; don't run it (would block on serve_forever).
print('main is callable:', callable(main))
"
```

Expected: prints `main is callable: True`.

- [ ] **Step 5: Commit**

```bash
git add pal/daemon_main.py
git commit -m "refactor(daemon_main): switch to run_daemon(PALAgent)"
```

---

## Task 19: Delete `pal/daemon.py` and `pal/client.py`

**Files:**
- Delete: `pal/daemon.py`
- Delete: `pal/client.py`

After Task 18, no code path on the `pal-daemon` side imports `pal.daemon`. PAL's `pal/cli.py` is the only consumer of `pal.client` and we update that next; for now we leave the import broken (cli.py is also being rewritten in the next task).

- [ ] **Step 1: Confirm no remaining production imports of pal.daemon or pal.client**

Use the Grep tool to search:
- `from pal.daemon import` in `pal/` (should be zero matches; if any, fix the importer first)
- `from pal.client import` in `pal/` (should match only `pal/cli.py`, which we rewrite next)

If any other production file imports `pal.daemon`, STOP and fix that file's import to point to whatever it actually needs.

- [ ] **Step 2: Delete the files**

```bash
git rm pal/daemon.py pal/client.py
```

- [ ] **Step 3: Verify the daemon-side imports load**

```bash
/home/edible/Projects/PAL/.venv/bin/python -c "
from pal.daemon_main import main
from pal.agent import PALAgent
print('daemon side imports clean')
"
```

Expected: prints `daemon side imports clean`. (We don't test pal.cli yet because it still imports pal.client and breaks.)

- [ ] **Step 4: Commit**

```bash
git commit -m "refactor: remove pal/daemon.py and pal/client.py (now in agent_core)"
```

---

## Task 20: Refactor `pal/cli.py` to `PALRenderer` + `main()`

**Files:**
- Modify: `pal/cli.py`

PAL's current `pal/cli.py` is 462 LOC. After Phase E it's ~120 LOC: a `PALRenderer` class with the six format functions and the splash, plus `main()`.

- [ ] **Step 1: Read the current cli.py to identify the formatters and splash**

Use Read on `pal/cli.py`. Note the `format_*_proposal` functions, `_tool_progress_label`, `render_splash_commands`, and `main()`.

- [ ] **Step 2: Rewrite the file**

Replace the entire contents of `pal/cli.py` with:

```python
"""PAL CLI: thin wrapper around agent_core.adapters.cli.run_repl.

Provides PALRenderer (PAL splash + agent-specific message formatters) and the
main() entry point.
"""
from __future__ import annotations

import asyncio

from agent_core.adapters.cli import run_repl
from agent_core.protocol import ToolProgressMessage

from pal.protocol import (
    ResearchProposalMessage,
    CompileProposalMessage,
    ReorgProposalMessage,
    ConsolidateProposalMessage,
    BatchFallbackProposal,
)
from pal.config import load_config


def render_splash_commands() -> str:
    # Lift the existing splash text/banner from the old cli.py; preserve as-is.
    return """\
PAL — Personal Agentic Librarian
Commands: /help /research /compile /summarize /think /scratch /research-mode /import /model
"""


def format_research_proposal(msg: ResearchProposalMessage) -> str:
    # Lift verbatim from the old cli.py.
    return (
        f"\n[Research proposal {msg.proposal_id}]\n"
        f"Topic: {msg.topic}\n"
        f"Depth: {msg.depth}\n"
        f"Rationale: {msg.rationale}\n"
        f"Approve with /approve {msg.proposal_id}, decline with /decline {msg.proposal_id}\n"
    )


def format_compile_proposal(msg: CompileProposalMessage) -> str:
    # Lift verbatim.
    paths = "\n  ".join(msg.summary_paths)
    return (
        f"\n[Compile proposal {msg.proposal_id}]\n"
        f"Sources:\n  {paths}\n"
        f"Rationale: {msg.rationale}\n"
        f"Approve with /approve {msg.proposal_id}, decline with /decline {msg.proposal_id}\n"
    )


def format_reorg_proposal(msg: ReorgProposalMessage) -> str:
    # Lift verbatim.
    ops = "\n  ".join(str(op) for op in msg.operations)
    return (
        f"\n[Reorg proposal {msg.proposal_id}]\n"
        f"Operations:\n  {ops}\n"
        f"Rationale: {msg.rationale}\n"
        f"References preview: {msg.references_preview}\n"
        f"Approve with /approve {msg.proposal_id}, decline with /decline {msg.proposal_id}\n"
    )


def format_consolidate_proposal(msg: ConsolidateProposalMessage) -> str:
    # Lift verbatim.
    sources = "\n  ".join(msg.source_paths)
    return (
        f"\n[Consolidate proposal {msg.proposal_id}]\n"
        f"Sources:\n  {sources}\n"
        f"Target: {msg.target_path} ({msg.target_title})\n"
        f"Rationale: {msg.rationale}\n"
        f"Approve with /approve {msg.proposal_id}, decline with /decline {msg.proposal_id}\n"
    )


def format_batch_fallback_proposal(msg: BatchFallbackProposal) -> str:
    # Lift verbatim.
    return (
        f"\n[Batch fallback proposal {msg.proposal_id}]\n"
        f"Caller: {msg.caller}\n"
        f"Context: {msg.context}\n"
        f"Choose: /retry, /main, or /skip with the proposal id.\n"
    )


def _tool_progress_label(tool: str, arguments: dict) -> str:
    # Lift verbatim from the old cli.py (the existing labels for fetch_url,
    # search_vault, etc.).
    return f"  [{tool}]"  # placeholder; lift the actual labels


class PALRenderer:
    def splash(self) -> str:
        return render_splash_commands()

    def format_message(self, msg) -> str | None:
        if isinstance(msg, ResearchProposalMessage):
            return format_research_proposal(msg)
        if isinstance(msg, CompileProposalMessage):
            return format_compile_proposal(msg)
        if isinstance(msg, ReorgProposalMessage):
            return format_reorg_proposal(msg)
        if isinstance(msg, ConsolidateProposalMessage):
            return format_consolidate_proposal(msg)
        if isinstance(msg, BatchFallbackProposal):
            return format_batch_fallback_proposal(msg)
        if isinstance(msg, ToolProgressMessage):
            return _tool_progress_label(msg.tool, msg.arguments)
        return None  # fall through to agent_core's default rendering


def main() -> None:
    config = load_config()
    asyncio.run(run_repl(config.socket_path, PALRenderer()))


if __name__ == "__main__":
    main()
```

The exact content of each `format_*_proposal` function and `_tool_progress_label` should be lifted verbatim from the old `pal/cli.py`. The skeletons above are illustrative; the executor should replace each with the existing PAL-specific rendering content.

- [ ] **Step 3: Verify imports**

```bash
/home/edible/Projects/PAL/.venv/bin/python -c "
from pal.cli import main, PALRenderer
print('PAL CLI imports clean')
r = PALRenderer()
print('splash:', r.splash()[:50])
"
```

Expected: prints `PAL CLI imports clean` and a snippet of the splash.

- [ ] **Step 4: Commit**

```bash
git add pal/cli.py
git commit -m "refactor(cli): shrink to PALRenderer + run_repl"
```

---

## Task 21: Run full PAL test suite, fix breakages

**Files:** test files only.

- [ ] **Step 1: Run the full suite (with the known-flaky and pre-existing-broken excluded)**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest \
  --ignore=tests/test_daemon.py \
  --ignore=tests/test_integration.py \
  --ignore=tests/test_chat_research_integration.py \
  --ignore=tests/test_consolidate_integration.py \
  --ignore=tests/test_learning_e2e.py \
  --ignore=tests/test_client.py \
  --ignore=tests/test_prompt_injection.py \
  -v
```

Expected: most tests pass. Some tests that imported from `pal.daemon` (the deleted file) will fail with `ModuleNotFoundError`. Some tests asserting the old "previous turn is still being processed" rejection will fail. Address each.

- [ ] **Step 2: For each failing test, investigate and fix**

Common failure modes:

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: pal.daemon` | Update import to `pal.agent.PALAgent` or remove the import if the test no longer needs it. |
| `ImportError: cannot import name 'Daemon' from 'pal.daemon'` | Same fix; use `agent_core.daemon.Daemon` or `pal.agent.PALAgent` depending on what the test exercises. |
| Assertion failure on "previous turn is still being processed" | Delete the assertion; this rejection no longer happens (the safety stopgap removed it). |
| `TypeError: PALAgent() takes no arguments` | If a test instantiates `PALAgent(config=...)`, remove the kwargs; PALAgent takes no args (config is set by run_daemon). |
| `AttributeError: 'PALAgent' object has no attribute 'X'` | The test references a framework attr (profile, channels, etc.) that's set by `run_daemon`. If the test is unit-testing `PALAgent.handle_chat`, it needs to manually wire those attrs (or use the existing `_wire_minimal_agent` helper pattern from agent_core's tests). |

Update each failing test, run `pytest` again, repeat until green.

- [ ] **Step 3: Confirm full suite passes**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest \
  --ignore=tests/test_daemon.py \
  --ignore=tests/test_integration.py \
  --ignore=tests/test_chat_research_integration.py \
  --ignore=tests/test_consolidate_integration.py \
  --ignore=tests/test_learning_e2e.py \
  --ignore=tests/test_client.py \
  --ignore=tests/test_prompt_injection.py
```

Expected: all green.

- [ ] **Step 4: Commit any test updates**

```bash
git add tests/
git commit -m "test: update PAL tests for PALAgent migration"
```

---

## Task 22: Push PAL branch + open PR

- [ ] **Step 1: Push**

```bash
cd /home/edible/Projects/PAL/.worktrees/phase-e-runtime
git push -u origin feature/phase-e-runtime
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --title "Phase E: PAL consumer-side migration to agent_core v0.5.0" --body "$(cat <<'EOF'
## Summary
PAL consumer-side migration to consume agent_core 0.5.0's runtime infrastructure. Companion to agent_core PR for Phase E.

- Bumps agent_core dep to v0.5.0.
- Creates \`pal/agent.py\` with \`PALAgent(Agent)\` carrying the lifted \`_handle_chat\`, \`_handle_command\`, and helper methods from the old \`pal/daemon.py\`.
- Shrinks \`pal/cli.py\` from 462 LOC to ~120 LOC (PALRenderer + main).
- Refactors \`pal/config.py\` to \`PALConfig(BaseConfig)\` with one PAL-specific field (\`max_inference_body_chars\`).
- Updates \`pal/daemon_main.py\` to \`run_daemon(PALAgent(), config_cls=PALConfig)\`.
- Deletes \`pal/daemon.py\` (functionality lives on PALAgent now).
- Deletes \`pal/client.py\` (now in agent_core).

Net diff: roughly -1500 lines deleted, +400 lines added in pal/.

Spec: \`docs/superpowers/specs/2026-04-30-phase-e-runtime-infrastructure-design.md\`.

## Test plan
- [x] PAL test suite green (with pre-existing flakies excluded)
- [ ] Server smoke after deploy: pal-daemon starts, CLI connects, /help works, chat round-trips, /research works, channel history loads, scratchpad write commits, learning scanner emits, /think on/off works.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Wait for CI (if applicable)**

```bash
gh pr checks
```

---

## Task 23: Merge PAL PR

Per Phase D's lesson, `gh pr merge` from inside a worktree fails. Use the API workaround from the parent checkout.

- [ ] **Step 1: Merge from parent checkout**

```bash
cd /home/edible/Projects/PAL  # NOT the worktree
PR_NUM=$(gh pr view feature/phase-e-runtime --json number --jq .number)
gh api -X PUT repos/EdibleTuber/PAL/pulls/$PR_NUM/merge --field merge_method=merge
```

- [ ] **Step 2: Update local main**

```bash
cd /home/edible/Projects/PAL
git checkout main
git pull
```

- [ ] **Step 3: Clean up the worktree**

```bash
git worktree remove .worktrees/phase-e-runtime
git branch -d feature/phase-e-runtime
```

---

# Part 3: Cleanup

## Task 24: Server smoke runbook

This task is run by the user on the inference server (192.168.1.14). The agent does not SSH; provide the exact commands.

- [ ] **Step 1: Hand the user the runbook**

Provide this text verbatim:

```
Server-side Phase E smoke (you run these on 192.168.1.14):

1. Stop the PAL daemon:
   systemctl --user stop pal-daemon

2. cd /mnt/secondary/PAL

3. git fetch origin && git checkout main && git pull
   # confirms the Phase E merge is present

4. Reinstall to pull agent_core 0.5.0:
   .venv/bin/pip install -e .

5. Restart the daemon:
   systemctl --user start pal-daemon

6. Tail logs:
   journalctl --user -u pal-daemon -f

Smoke checks (from your CLI session against the server):
- /help: command works.
- Send a normal chat message: response comes back.
- /research <some topic>: research proposal flows through.
- /scratch read: shows existing scratch content.
- /scratch write 'phase E smoke': writes and commits.
- /think on: reasoning override applies.
- /think off: reverts.
- A learning candidate-style trigger ("actually you're right ..."): proposal appears.
- Restart the daemon: systemctl --user restart pal-daemon. Channel history reloads.

If anything fails:
- Roll back PAL: git checkout HEAD~1 (pre-Phase-E merge); .venv/bin/pip install -e .; restart daemon.
- agent_core 0.5.0 is independent; PAL on v0.4.0 keeps working.
```

- [ ] **Step 2: Wait for user confirmation**

User reports back. If anything fails, diagnose and fix in a follow-up.

---

## Task 25: Update memory and close out

- [ ] **Step 1: Update `project_agent_core_extraction.md` memory**

Edit `/home/edible/.claude/projects/-home-edible-Projects-PAL/memory/project_agent_core_extraction.md`:

- Bump "five of nine phases done" (or whatever the count is) and add Phase E to the done list with the merge date and PR numbers.
- Update agent_core version to v0.5.0 and module count.
- Update remaining-phases list (E removed, F/G/H/I still pending).
- Append Phase E lessons (e.g., the writer-passing pattern, the per-turn scratchpad construction in handle_chat, anything surprising during the lift).

- [ ] **Step 2: Final summary to user**

Report Phase E complete: PRs merged on agent_core (v0.5.0) and PAL, server smoke passed, agent_core now houses the runtime infrastructure, only Phases F (tool/command/prompt scaffolding), G (Discord), H (template), I (burn-in) remain.

---

## Notes for the executing agent

- **Use the Grep tool, not bash grep.** Per memory `feedback_use_grep_tool`.
- **Never `git add -A` or `git add .` in the PAL repo.** Per memory `feedback_git_add_explicit`.
- **The 7 known-flaky/hanging tests** stay excluded in broad runs: `tests/test_daemon.py`, `tests/test_integration.py`, `tests/test_chat_research_integration.py`, `tests/test_consolidate_integration.py`, `tests/test_learning_e2e.py`, `tests/test_client.py`, `tests/test_prompt_injection.py`.
- **Worktree cleanup**: after PAL PR merge, remove `.worktrees/phase-e-runtime`.
- **No SSH from agent**: server-side smoke (Task 24) is the user's job.
- **No em dashes** in user-facing output. Per memory `feedback_no_em_dashes`.
- **The "lift _handle_chat" task (Task 16) is the largest.** Read carefully, substitute parameters per the table, preserve the existing logic verbatim. The body is mechanical translation, not redesign.
- **The handler is an async generator.** It can both write to `ctx.writer` directly (for streaming chunks mid-turn) AND `yield` messages. The daemon iterates the generator and writes each yielded message; direct writer.write calls happen as side effects during iteration. This dual-channel pattern is intentional and matches how the existing daemon streamed chunks.
