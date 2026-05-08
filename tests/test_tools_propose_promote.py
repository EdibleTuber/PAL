"""Tests for propose_promote Tool subclass (Phase F PR4).

Migrated from ToolExecutor.run_async calls to direct ProposePromote.run calls.
"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

from agent_core.approval_registry import ApprovalRegistry
from agent_core.learning import LearningManager
from agent_core.wisdom import WisdomManager
from pal.tools.reorg import ProposePromote


class _Config:
    def __init__(self, vault_path):
        self.vault_path = vault_path


class _Agent:
    def __init__(self, vault_path, approval_registry, learning, wisdom, wiki=None):
        self.config = _Config(vault_path)
        self.approval_registry = approval_registry
        self.learning = learning
        self.wisdom = wisdom
        self.wiki = wiki


def _ctx(agent, emit=None):
    class _C:
        pass
    c = _C()
    c.agent = agent
    c.emit = emit or AsyncMock()
    return c


def test_propose_promote_emits_and_promotes_on_approve(tmp_path: Path):
    lm = LearningManager(tmp_path, "pal")
    slug = lm.add("Granularity", "keep it focused", source="conversation")

    registry = ApprovalRegistry()
    wm = WisdomManager(tmp_path, "test-agent")

    emit = AsyncMock()

    async def _emit_and_approve(msg):
        registry.approve(msg.proposal_id)

    emit.side_effect = _emit_and_approve

    agent = _Agent(tmp_path, approval_registry=registry, learning=lm, wisdom=wm)
    result = asyncio.run(ProposePromote().run(
        {"slug": slug, "rationale": "User reiterated."},
        _ctx(agent, emit=emit),
    ))
    parsed = json.loads(result)

    assert parsed["status"] == "promoted"
    assert parsed["slug"] == slug
    assert lm.get_meta(slug)["status"] == "promoted"
    titles = {e["title"] for e in wm.list()}
    assert "Granularity" in titles


def test_propose_promote_returns_declined_on_decline(tmp_path: Path):
    lm = LearningManager(tmp_path, "pal")
    slug = lm.add("Temp", "body", source="conversation")
    registry = ApprovalRegistry()
    wm = WisdomManager(tmp_path, "test-agent")

    emit = AsyncMock()

    async def _emit_and_decline(msg):
        registry.decline(msg.proposal_id)

    emit.side_effect = _emit_and_decline

    agent = _Agent(tmp_path, approval_registry=registry, learning=lm, wisdom=wm)
    result = asyncio.run(ProposePromote().run(
        {"slug": slug, "rationale": "no."},
        _ctx(agent, emit=emit),
    ))
    parsed = json.loads(result)

    assert parsed["status"] == "declined"
    assert lm.get_meta(slug)["status"] == "active"


def test_propose_promote_errors_on_missing_slug(tmp_path: Path):
    registry = ApprovalRegistry()
    agent = _Agent(
        tmp_path,
        approval_registry=registry,
        learning=LearningManager(tmp_path, "pal"),
        wisdom=WisdomManager(tmp_path, "test-agent"),
    )
    result = asyncio.run(ProposePromote().run(
        {"slug": "no-such", "rationale": "r"},
        _ctx(agent),
    ))
    parsed = json.loads(result)
    assert "error" in parsed
    assert "no such" in parsed["error"].lower()


def test_propose_promote_errors_on_already_promoted(tmp_path: Path):
    lm = LearningManager(tmp_path, "pal")
    slug = lm.add("X", "body", source="conversation")
    lm.mark_promoted(slug)

    registry = ApprovalRegistry()
    agent = _Agent(
        tmp_path,
        approval_registry=registry,
        learning=lm,
        wisdom=WisdomManager(tmp_path, "test-agent"),
    )
    result = asyncio.run(ProposePromote().run(
        {"slug": slug, "rationale": "r"},
        _ctx(agent),
    ))
    parsed = json.loads(result)
    assert "error" in parsed
    assert "already promoted" in parsed["error"].lower()
