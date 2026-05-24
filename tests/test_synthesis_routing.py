"""Tests for synthesis-side batch routing.

Wires Researcher/Compiler/Consolidator and /learn through effective_batch
so they route to the batch slot when batch_enabled. Companion to
test_batch_inference.py which already covers the categorizer side.

See docs/superpowers/specs/2026-05-24-synthesis-routing-wiring-design.md.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_core.client import DaemonConnection as PalClient
from agent_core.inference import CompletionResult
from pal.config import Config

from tests.conftest import make_pal_agent, start_pal_daemon


# ---------------------------------------------------------------------------
# Test 1: parameterized constructor wiring
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("service_attr", ["researcher", "compiler", "consolidator"])
def test_synthesis_service_uses_main_when_batch_disabled(tmp_path, service_attr):
    cfg = Config(
        socket_path=tmp_path / "pal.sock",
        vault_path=tmp_path / "vault",
        batch_enabled=False,
    )
    agent = make_pal_agent(cfg)
    assert agent.batch_inference is None
    service = getattr(agent, service_attr)
    assert service.inference is agent.inference


@pytest.mark.parametrize("service_attr", ["researcher", "compiler", "consolidator"])
def test_synthesis_service_uses_batch_when_enabled(tmp_path, service_attr):
    cfg = Config(
        socket_path=tmp_path / "pal.sock",
        vault_path=tmp_path / "vault",
        batch_enabled=True,
    )
    agent = make_pal_agent(cfg)
    assert agent.batch_inference is not None
    service = getattr(agent, service_attr)
    assert service.inference is agent.batch_inference


# ---------------------------------------------------------------------------
# Test 2: /learn parameterized batch fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "batch_enabled,expected_attr",
    [(False, "inference"), (True, "batch_inference")],
)
async def test_learn_routes_to_batch_when_enabled(tmp_path, batch_enabled, expected_attr):
    """When batch is enabled, /learn hits batch_inference; otherwise primary."""
    from pal.commands.domain import Learn

    cfg = Config(
        socket_path=tmp_path / "pal.sock",
        vault_path=tmp_path / "vault",
        batch_enabled=batch_enabled,
    )
    agent = make_pal_agent(cfg)

    primary_spy = AsyncMock(return_value=CompletionResult(type="text", content="NONE"))
    agent.inference.complete = primary_spy

    batch_spy = None
    if batch_enabled:
        batch_spy = AsyncMock(return_value=CompletionResult(type="text", content="NONE"))
        agent.batch_inference.complete = batch_spy

    conv = MagicMock()
    conv.messages = [{"role": "user", "content": "test message"}]
    ctx = MagicMock()
    ctx.agent = agent
    ctx.conversation = conv

    cmd = Learn()
    async for _ in cmd.run("", ctx):
        pass

    expected_spy = batch_spy if batch_enabled else primary_spy
    other_spy = primary_spy if batch_enabled else batch_spy
    assert expected_spy.await_count == 1
    if other_spy is not None:
        assert other_spy.await_count == 0


# ---------------------------------------------------------------------------
# Test 3: chat-loop negative -- tool-free chat does not touch batch
# ---------------------------------------------------------------------------

@pytest.fixture()
async def chat_negative_daemon(socket_path, mock_inference_server, tmp_path):
    cfg = Config(
        inference_url=mock_inference_server,
        model="test-model",
        socket_path=socket_path,
        history_depth=50,
        vault_path=tmp_path / "vault",
        batch_enabled=True,
    )
    agent = make_pal_agent(cfg)
    task = await start_pal_daemon(agent)
    for _ in range(100):
        if socket_path.exists():
            break
        await asyncio.sleep(0.01)
    yield agent
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_tool_free_chat_does_not_touch_batch(chat_negative_daemon, socket_path):
    """A chat round that produces no tool calls must not invoke batch_inference."""
    from agent_core.protocol import ResponseMessage

    agent = chat_negative_daemon
    batch_spy = AsyncMock(return_value=CompletionResult(type="text", content="ok"))
    agent.batch_inference.complete = batch_spy

    client = PalClient(socket_path)
    await client.connect()
    async for msg in client.chat("hello"):
        if isinstance(msg, ResponseMessage):
            break
    await client.close()

    assert batch_spy.await_count == 0
