"""Regression tests for the two deferred audit items (brief: A3, A4).

A3: /model default mutates the shared inference client's default_model
    without synchronisation (the daemon runs each message in its own task),
    so an in-flight chat turn could complete its later rounds on a different
    model. The turn must pin the model it started with.
A4: the four fire-and-forget learning-scan tasks in handle_chat had no
    done-callback, so a scanner failure became an un-retrieved task
    exception and the feature failed silently per turn.
"""
import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from agent_core.inference import CompletionResult, ToolCall
from agent_core.protocol import ChatMessage
from pal.config import Config

from tests.conftest import make_pal_agent


class _Writer:
    """Minimal ctx.writer stub (write/drain) for driving handle_chat directly."""

    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        pass


def _make_turn_context(tmp_path: Path):
    """Agent + ctx/msg stubs for driving PALAgent.handle_chat directly.

    decide_mode is forced to "on" (non-streaming path) and system_prompt /
    tool execution are stubbed so the test isolates the inference-call and
    scan-task behaviour under test.
    """
    cfg = Config(socket_path=tmp_path / "pal.sock", vault_path=tmp_path / "vault")
    agent = make_pal_agent(cfg)
    agent.decide_mode = lambda conv: "on"
    agent.system_prompt = lambda ctx: "SYS"
    agent.tool_executor.run = AsyncMock(return_value='{"status": "ok"}')

    conv = MagicMock()
    conv.get_messages_for_api.return_value = [{"role": "user", "content": "hi"}]
    ctx = MagicMock()
    ctx.conversation = conv
    ctx.channel_id = "test"
    ctx.writer = _Writer()
    msg = ChatMessage(text="hi")
    return agent, ctx, msg


async def _spin(iters: int = 10) -> None:
    """Yield to the loop enough times for a fire-and-forget task to run and
    its done-callbacks to fire."""
    for _ in range(iters):
        await asyncio.sleep(0)


async def test_model_switch_mid_turn_keeps_in_flight_turn_on_turn_start_model(tmp_path):
    """A3: a mid-turn /model default must retarget the NEXT turn, not this
    one — every completion in the in-flight turn is pinned to the turn-start
    model."""
    agent, ctx, msg = _make_turn_context(tmp_path)
    agent.inference.default_model = "turn-start-model"
    agent.scanner.maybe_scan = AsyncMock()

    calls: list[str | None] = []

    async def fake_complete(messages, tools=None, model=None, reasoning=None, max_tokens=None):
        calls.append(model)
        if len(calls) == 1:
            # A concurrent /model default lands between the first and second
            # completion (the daemon runs commands in their own task).
            agent.inference.default_model = "reset-model"
            return CompletionResult(
                type="tool_calls",
                tool_calls=[ToolCall(id="c1", name="cat", arguments={"path": "a.md"})],
            )
        return CompletionResult(type="text", content="done")

    agent.inference.complete = fake_complete

    async for _ in agent.handle_chat(msg, ctx):
        pass

    assert len(calls) == 2
    assert all(m == "turn-start-model" for m in calls), (
        f"in-flight turn must pin the turn-start model on every completion; got {calls}"
    )


async def test_learning_scan_failure_is_logged_not_silent(tmp_path, caplog):
    """A4: a failing fire-and-forget learning scan must leave a log record
    (done-callback), not an un-retrieved task exception."""
    agent, ctx, msg = _make_turn_context(tmp_path)

    async def _boom(**kwargs):
        raise RuntimeError("scan blew up")

    agent.scanner.maybe_scan = _boom
    agent.inference.complete = AsyncMock(
        return_value=CompletionResult(type="text", content="done"),
    )

    with caplog.at_level(logging.WARNING, logger="pal.agent"):
        async for _ in agent.handle_chat(msg, ctx):
            pass
        await _spin()

    assert any("Learning scan failed" in rec.getMessage() for rec in caplog.records), (
        "scan failure left no log record (un-retrieved task exception)"
    )
