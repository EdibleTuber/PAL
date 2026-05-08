"""Tests for PAL consolidate tools (Phase F PR4)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pal.tools.consolidate import Consolidate, ProposeConsolidate


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
        consolidator=None,
    ):
        self.config = _Config(vault_path)
        self.approval_registry = approval_registry
        self.consolidator = consolidator


def _ctx(agent, emit=None):
    class _C:
        pass
    c = _C()
    c.agent = agent
    c.emit = emit or AsyncMock()
    return c


# ---------------------------------------------------------------------------
# ProposeConsolidate
# ---------------------------------------------------------------------------

async def test_propose_consolidate_approved(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry
    from pal.protocol import ConsolidateProposalMessage

    registry = ApprovalRegistry()
    emit = AsyncMock()

    async def _emit_and_approve(msg):
        registry.approve(msg.proposal_id)

    emit.side_effect = _emit_and_approve

    agent = _Agent(tmp_path, approval_registry=registry)
    result = await ProposeConsolidate().run(
        {
            "source_paths": ["Security/a.md", "Security/b.md"],
            "target_path": "Security/Combined.md",
            "target_title": "Combined",
            "rationale": "merge overlapping notes",
        },
        _ctx(agent, emit=emit),
    )
    parsed = json.loads(result)
    assert parsed["status"] == "approved"
    assert parsed["source_paths"] == ["Security/a.md", "Security/b.md"]
    assert parsed["target_path"] == "Security/Combined.md"
    assert parsed["target_title"] == "Combined"
    emit.assert_awaited_once()
    msg = emit.call_args[0][0]
    assert isinstance(msg, ConsolidateProposalMessage)
    assert msg.target_path == "Security/Combined.md"


async def test_propose_consolidate_requires_two_sources(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    agent = _Agent(tmp_path, approval_registry=ApprovalRegistry())
    result = await ProposeConsolidate().run(
        {
            "source_paths": ["Security/a.md"],
            "target_path": "Security/Combined.md",
            "target_title": "Combined",
            "rationale": "r",
        },
        _ctx(agent),
    )
    assert "at least two" in result.lower()


async def test_propose_consolidate_requires_target_path(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    agent = _Agent(tmp_path, approval_registry=ApprovalRegistry())
    result = await ProposeConsolidate().run(
        {
            "source_paths": ["Security/a.md", "Security/b.md"],
            "target_path": "",
            "target_title": "Combined",
            "rationale": "r",
        },
        _ctx(agent),
    )
    assert "target_path" in result


async def test_propose_consolidate_requires_target_title(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    agent = _Agent(tmp_path, approval_registry=ApprovalRegistry())
    result = await ProposeConsolidate().run(
        {
            "source_paths": ["Security/a.md", "Security/b.md"],
            "target_path": "Security/Combined.md",
            "target_title": "",
            "rationale": "r",
        },
        _ctx(agent),
    )
    assert "target_title" in result


async def test_propose_consolidate_requires_rationale(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    agent = _Agent(tmp_path, approval_registry=ApprovalRegistry())
    result = await ProposeConsolidate().run(
        {
            "source_paths": ["Security/a.md", "Security/b.md"],
            "target_path": "Security/Combined.md",
            "target_title": "Combined",
            "rationale": "",
        },
        _ctx(agent),
    )
    assert "rationale" in result


async def test_propose_consolidate_no_registry(tmp_path):
    agent = _Agent(tmp_path, approval_registry=None)
    result = await ProposeConsolidate().run(
        {
            "source_paths": ["a.md", "b.md"],
            "target_path": "c.md",
            "target_title": "C",
            "rationale": "r",
        },
        _ctx(agent),
    )
    assert "not available" in result.lower()


async def test_propose_consolidate_declined(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    registry = ApprovalRegistry()
    emit = AsyncMock()

    async def _emit_and_decline(msg):
        registry.decline(msg.proposal_id)

    emit.side_effect = _emit_and_decline

    agent = _Agent(tmp_path, approval_registry=registry)
    result = await ProposeConsolidate().run(
        {
            "source_paths": ["a.md", "b.md"],
            "target_path": "c.md",
            "target_title": "C",
            "rationale": "r",
        },
        _ctx(agent, emit=emit),
    )
    parsed = json.loads(result)
    assert parsed["status"] == "declined"


# ---------------------------------------------------------------------------
# Consolidate
# ---------------------------------------------------------------------------

async def test_consolidate_happy_path(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    registry = ApprovalRegistry()
    pid = registry.create_proposal(
        kind="consolidate",
        summary_paths=["Security/a.md", "Security/b.md"],
        target_path="Security/Combined.md",
        target_title="Combined",
        rationale="r",
    )
    registry.approve(pid)

    consolidator = MagicMock()
    consolidator.consolidate = AsyncMock(return_value={
        "status": "ok",
        "target_path": "Security/Combined.md",
        "vault_exists": True,
    })

    agent = _Agent(tmp_path, approval_registry=registry, consolidator=consolidator)
    result = await Consolidate().run({"proposal_id": pid}, _ctx(agent))
    parsed = json.loads(result)
    assert parsed["status"] == "ok"
    assert parsed["vault_exists"] is True
    assert "_note" in parsed
    assert registry.get(pid).status == "consumed"
    consolidator.consolidate.assert_awaited_once_with(
        source_paths=["Security/a.md", "Security/b.md"],
        target_path="Security/Combined.md",
        target_title="Combined",
    )


async def test_consolidate_missing_proposal_id(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    agent = _Agent(tmp_path, approval_registry=ApprovalRegistry(), consolidator=MagicMock())
    result = await Consolidate().run({}, _ctx(agent))
    assert "Error" in result and "proposal_id" in result


async def test_consolidate_unknown_proposal(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    agent = _Agent(tmp_path, approval_registry=ApprovalRegistry(), consolidator=MagicMock())
    result = await Consolidate().run({"proposal_id": "does-not-exist"}, _ctx(agent))
    assert "unknown proposal_id" in result.lower()


async def test_consolidate_wrong_kind(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    registry = ApprovalRegistry()
    pid = registry.create_proposal(kind="compile", summary_paths=["a.md"], rationale="r")
    registry.approve(pid)

    agent = _Agent(tmp_path, approval_registry=registry, consolidator=MagicMock())
    result = await Consolidate().run({"proposal_id": pid}, _ctx(agent))
    assert "not a consolidate proposal" in result.lower()


async def test_consolidate_pending_proposal(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    registry = ApprovalRegistry()
    pid = registry.create_proposal(
        kind="consolidate",
        summary_paths=["a.md", "b.md"],
        target_path="c.md",
        target_title="C",
        rationale="r",
    )
    agent = _Agent(tmp_path, approval_registry=registry, consolidator=MagicMock())
    result = await Consolidate().run({"proposal_id": pid}, _ctx(agent))
    assert "not approved" in result.lower()


async def test_consolidate_consumed_proposal(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    registry = ApprovalRegistry()
    pid = registry.create_proposal(
        kind="consolidate",
        summary_paths=["a.md", "b.md"],
        target_path="c.md",
        target_title="C",
        rationale="r",
    )
    registry.approve(pid)
    registry.consume(pid)

    consolidator = MagicMock()
    consolidator.consolidate = AsyncMock(return_value={"status": "ok", "target_path": "c.md", "vault_exists": True})
    agent = _Agent(tmp_path, approval_registry=registry, consolidator=consolidator)
    result = await Consolidate().run({"proposal_id": pid}, _ctx(agent))
    assert "already used" in result.lower() or "consumed" in result.lower()


async def test_consolidate_no_managers(tmp_path):
    agent = _Agent(tmp_path, approval_registry=None, consolidator=None)
    result = await Consolidate().run({"proposal_id": "p"}, _ctx(agent))
    assert "not available" in result.lower()


async def test_consolidate_exception_produces_error_outcome(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    registry = ApprovalRegistry()
    pid = registry.create_proposal(
        kind="consolidate",
        summary_paths=["a.md", "b.md"],
        target_path="c.md",
        target_title="C",
        rationale="r",
    )
    registry.approve(pid)

    consolidator = MagicMock()
    consolidator.consolidate = AsyncMock(side_effect=RuntimeError("disk full"))

    agent = _Agent(tmp_path, approval_registry=registry, consolidator=consolidator)
    result = await Consolidate().run({"proposal_id": pid}, _ctx(agent))
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert "disk full" in parsed["reason"]
    assert parsed["vault_exists"] is False
    assert "_note" in parsed
