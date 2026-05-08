"""Tests for PAL reorg and promote tools (Phase F PR4)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pal.tools.reorg import ProposePromote, ProposeReorg, Reorg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _Config:
    def __init__(self, vault_path):
        self.vault_path = vault_path


class _Agent:
    def __init__(
        self,
        vault_path,
        approval_registry=None,
        reorganizer=None,
        learning=None,
        wisdom=None,
        wiki=None,
    ):
        self.config = _Config(vault_path)
        self.approval_registry = approval_registry
        self.reorganizer = reorganizer
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


# ---------------------------------------------------------------------------
# ProposeReorg
# ---------------------------------------------------------------------------

async def test_propose_reorg_approved(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry
    from pal.protocol import ReorgProposalMessage

    registry = ApprovalRegistry()
    emit = AsyncMock()

    async def _emit_and_approve(msg):
        registry.approve(msg.proposal_id)

    emit.side_effect = _emit_and_approve

    reorganizer = MagicMock()
    reorganizer.validate_operations = MagicMock(return_value=[])
    reorganizer.count_references = MagicMock(return_value=3)

    agent = _Agent(tmp_path, approval_registry=registry, reorganizer=reorganizer)
    ops = [{"type": "move", "src": "A.md", "dst": "B.md"}]
    result = await ProposeReorg().run(
        {"operations": ops, "rationale": "rename"},
        _ctx(agent, emit=emit),
    )
    parsed = json.loads(result)
    assert parsed["status"] == "approved"
    assert parsed["operations"] == ops
    emit.assert_awaited_once()
    msg = emit.call_args[0][0]
    assert isinstance(msg, ReorgProposalMessage)
    assert msg.references_preview == 3


async def test_propose_reorg_declined(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    registry = ApprovalRegistry()
    emit = AsyncMock()

    async def _emit_and_decline(msg):
        registry.decline(msg.proposal_id)

    emit.side_effect = _emit_and_decline

    reorganizer = MagicMock()
    reorganizer.validate_operations = MagicMock(return_value=[])
    reorganizer.count_references = MagicMock(return_value=0)

    agent = _Agent(tmp_path, approval_registry=registry, reorganizer=reorganizer)
    result = await ProposeReorg().run(
        {"operations": [{"type": "move", "src": "A.md", "dst": "B.md"}], "rationale": "r"},
        _ctx(agent, emit=emit),
    )
    parsed = json.loads(result)
    assert parsed["status"] == "declined"


async def test_propose_reorg_empty_operations(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    agent = _Agent(tmp_path, approval_registry=ApprovalRegistry(), reorganizer=MagicMock())
    result = await ProposeReorg().run(
        {"operations": [], "rationale": "r"},
        _ctx(agent),
    )
    assert "Error" in result


async def test_propose_reorg_missing_rationale(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    agent = _Agent(tmp_path, approval_registry=ApprovalRegistry(), reorganizer=MagicMock())
    result = await ProposeReorg().run(
        {"operations": [{"type": "move", "src": "A.md", "dst": "B.md"}]},
        _ctx(agent),
    )
    assert "Error" in result and "rationale" in result


async def test_propose_reorg_validation_errors(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    registry = ApprovalRegistry()
    reorganizer = MagicMock()
    reorganizer.validate_operations = MagicMock(return_value=["src does not exist: A.md"])

    agent = _Agent(tmp_path, approval_registry=registry, reorganizer=reorganizer)
    emit = AsyncMock()
    result = await ProposeReorg().run(
        {"operations": [{"type": "move", "src": "A.md", "dst": "B.md"}], "rationale": "r"},
        _ctx(agent, emit=emit),
    )
    assert "Error" in result
    assert "src does not exist" in result
    emit.assert_not_awaited()


async def test_propose_reorg_no_managers(tmp_path):
    agent = _Agent(tmp_path, approval_registry=None, reorganizer=None)
    result = await ProposeReorg().run(
        {"operations": [{"type": "move", "src": "A.md", "dst": "B.md"}], "rationale": "r"},
        _ctx(agent),
    )
    assert "not available" in result.lower()


# ---------------------------------------------------------------------------
# Reorg
# ---------------------------------------------------------------------------

async def test_reorg_happy_path(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    registry = ApprovalRegistry()
    ops = [{"type": "move", "src": "A.md", "dst": "B.md"}]
    pid = registry.create_proposal(kind="reorg", operations=ops, rationale="r")
    registry.approve(pid)

    reorganizer = MagicMock()
    reorganizer.validate_operations = MagicMock(return_value=[])
    reorganizer.execute_operations_async = AsyncMock(return_value=[
        {"op": "move", "src": "A.md", "dst": "B.md", "status": "ok", "references_rewritten": 2}
    ])

    agent = _Agent(tmp_path, approval_registry=registry, reorganizer=reorganizer)
    result = await Reorg().run({"proposal_id": pid}, _ctx(agent))
    parsed = json.loads(result)
    assert parsed["total"] == 1
    assert parsed["ok"] == 1
    assert parsed["references_rewritten"] == 2
    assert registry.get(pid).status == "consumed"


async def test_reorg_missing_proposal_id(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    agent = _Agent(tmp_path, approval_registry=ApprovalRegistry(), reorganizer=MagicMock())
    result = await Reorg().run({}, _ctx(agent))
    assert "Error" in result and "proposal_id" in result


async def test_reorg_unknown_proposal(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    agent = _Agent(tmp_path, approval_registry=ApprovalRegistry(), reorganizer=MagicMock())
    result = await Reorg().run({"proposal_id": "unknown"}, _ctx(agent))
    assert "unknown" in result.lower()


async def test_reorg_wrong_kind(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    registry = ApprovalRegistry()
    pid = registry.create_proposal(kind="research", topic="t", depth=3, rationale="r")
    registry.approve(pid)

    agent = _Agent(tmp_path, approval_registry=registry, reorganizer=MagicMock())
    result = await Reorg().run({"proposal_id": pid}, _ctx(agent))
    assert "not a reorg proposal" in result.lower()


async def test_reorg_pending_proposal(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    registry = ApprovalRegistry()
    pid = registry.create_proposal(
        kind="reorg",
        operations=[{"type": "move", "src": "A.md", "dst": "B.md"}],
        rationale="r",
    )

    agent = _Agent(tmp_path, approval_registry=registry, reorganizer=MagicMock())
    result = await Reorg().run({"proposal_id": pid}, _ctx(agent))
    assert "not approved" in result.lower()


async def test_reorg_post_approval_validation_blocks_execute(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    registry = ApprovalRegistry()
    ops = [{"type": "move", "src": "A.md", "dst": "B.md"}]
    pid = registry.create_proposal(kind="reorg", operations=ops, rationale="r")
    registry.approve(pid)

    reorganizer = MagicMock()
    reorganizer.validate_operations = MagicMock(return_value=["src missing"])
    reorganizer.execute_operations_async = AsyncMock()

    agent = _Agent(tmp_path, approval_registry=registry, reorganizer=reorganizer)
    result = await Reorg().run({"proposal_id": pid}, _ctx(agent))
    assert "invalid" in result.lower() or "src missing" in result.lower()
    reorganizer.execute_operations_async.assert_not_called()


async def test_reorg_no_managers(tmp_path):
    agent = _Agent(tmp_path, approval_registry=None, reorganizer=None)
    result = await Reorg().run({"proposal_id": "p"}, _ctx(agent))
    assert "not available" in result.lower()


# ---------------------------------------------------------------------------
# ProposePromote
# ---------------------------------------------------------------------------

async def test_propose_promote_approves_and_promotes(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry
    from agent_core.learning import LearningManager
    from agent_core.wisdom import WisdomManager
    from pal.protocol import PromoteProposalMessage

    lm = LearningManager(tmp_path, "pal")
    slug = lm.add("Granularity", "keep it focused", source="conversation")
    wm = WisdomManager(tmp_path, "test-agent")
    registry = ApprovalRegistry()
    emit = AsyncMock()

    async def _emit_and_approve(msg):
        registry.approve(msg.proposal_id)

    emit.side_effect = _emit_and_approve

    agent = _Agent(
        tmp_path,
        approval_registry=registry,
        learning=lm,
        wisdom=wm,
    )
    result = await ProposePromote().run(
        {"slug": slug, "rationale": "User reiterated."},
        _ctx(agent, emit=emit),
    )
    parsed = json.loads(result)
    assert parsed["status"] == "promoted"
    assert parsed["slug"] == slug
    assert lm.get_meta(slug)["status"] == "promoted"
    titles = {e["title"] for e in wm.list()}
    assert "Granularity" in titles
    emit.assert_awaited_once()
    msg = emit.call_args[0][0]
    assert isinstance(msg, PromoteProposalMessage)


async def test_propose_promote_declined(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry
    from agent_core.learning import LearningManager
    from agent_core.wisdom import WisdomManager

    lm = LearningManager(tmp_path, "pal")
    slug = lm.add("Temp", "body", source="conversation")
    wm = WisdomManager(tmp_path, "test-agent")
    registry = ApprovalRegistry()
    emit = AsyncMock()

    async def _emit_and_decline(msg):
        registry.decline(msg.proposal_id)

    emit.side_effect = _emit_and_decline

    agent = _Agent(tmp_path, approval_registry=registry, learning=lm, wisdom=wm)
    result = await ProposePromote().run(
        {"slug": slug, "rationale": "no."},
        _ctx(agent, emit=emit),
    )
    parsed = json.loads(result)
    assert parsed["status"] == "declined"
    assert lm.get_meta(slug)["status"] == "active"


async def test_propose_promote_missing_slug(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry
    from agent_core.learning import LearningManager
    from agent_core.wisdom import WisdomManager

    agent = _Agent(
        tmp_path,
        approval_registry=ApprovalRegistry(),
        learning=LearningManager(tmp_path, "pal"),
        wisdom=WisdomManager(tmp_path, "test-agent"),
    )
    result = await ProposePromote().run({"rationale": "r"}, _ctx(agent))
    assert "Error" in result and "slug" in result


async def test_propose_promote_no_such_learning(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry
    from agent_core.learning import LearningManager
    from agent_core.wisdom import WisdomManager

    agent = _Agent(
        tmp_path,
        approval_registry=ApprovalRegistry(),
        learning=LearningManager(tmp_path, "pal"),
        wisdom=WisdomManager(tmp_path, "test-agent"),
    )
    result = await ProposePromote().run({"slug": "no-such", "rationale": "r"}, _ctx(agent))
    parsed = json.loads(result)
    assert "error" in parsed
    assert "no such" in parsed["error"].lower()


async def test_propose_promote_already_promoted(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry
    from agent_core.learning import LearningManager
    from agent_core.wisdom import WisdomManager

    lm = LearningManager(tmp_path, "pal")
    slug = lm.add("X", "body", source="conversation")
    lm.mark_promoted(slug)

    agent = _Agent(
        tmp_path,
        approval_registry=ApprovalRegistry(),
        learning=lm,
        wisdom=WisdomManager(tmp_path, "test-agent"),
    )
    result = await ProposePromote().run({"slug": slug, "rationale": "r"}, _ctx(agent))
    parsed = json.loads(result)
    assert "error" in parsed
    assert "already promoted" in parsed["error"].lower()


async def test_propose_promote_no_managers(tmp_path):
    agent = _Agent(tmp_path, approval_registry=None, learning=None, wisdom=None)
    result = await ProposePromote().run({"slug": "x", "rationale": "r"}, _ctx(agent))
    assert "not available" in result.lower()
