"""Tests for PAL compile tools (Phase F PR4)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pal.tools.compile import CompileBatch, CompileSummary, ProposeCompileBatch


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
        compiler=None,
        approval_registry=None,
    ):
        self.config = _Config(vault_path)
        self.compiler = compiler
        self.approval_registry = approval_registry


def _ctx(agent, emit=None):
    class _C:
        pass
    c = _C()
    c.agent = agent
    c.emit = emit or AsyncMock()
    return c


# ---------------------------------------------------------------------------
# CompileSummary
# ---------------------------------------------------------------------------

async def test_compile_summary_happy_path(tmp_path):
    compiler = MagicMock()
    compiler.compile_one = AsyncMock(return_value={
        "status": "ok",
        "title": "Example Article",
        "article_path_rel": "AI-Agents/Example.md",
    })
    agent = _Agent(tmp_path, compiler=compiler)
    result = await CompileSummary().run(
        {"summary_path": "raw/summaries/foo.md"},
        _ctx(agent),
    )
    parsed = json.loads(result)
    assert parsed["status"] == "ok"
    assert parsed["title"] == "Example Article"
    compiler.compile_one.assert_awaited_once_with("raw/summaries/foo.md")


async def test_compile_summary_missing_path(tmp_path):
    agent = _Agent(tmp_path, compiler=MagicMock())
    result = await CompileSummary().run({}, _ctx(agent))
    assert "Error" in result and "summary_path" in result


async def test_compile_summary_no_compiler(tmp_path):
    agent = _Agent(tmp_path, compiler=None)
    result = await CompileSummary().run(
        {"summary_path": "raw/summaries/foo.md"},
        _ctx(agent),
    )
    assert "not available" in result.lower()


# ---------------------------------------------------------------------------
# ProposeCompileBatch
# ---------------------------------------------------------------------------

async def test_propose_compile_batch_approved(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry
    from pal.protocol import CompileProposalMessage

    registry = ApprovalRegistry()
    emit = AsyncMock()

    async def _emit_and_approve(msg):
        registry.approve(msg.proposal_id)

    emit.side_effect = _emit_and_approve

    agent = _Agent(tmp_path, approval_registry=registry)
    result = await ProposeCompileBatch().run(
        {
            "summary_paths": ["raw/summaries/a.md", "raw/summaries/b.md"],
            "rationale": "promote findings",
        },
        _ctx(agent, emit=emit),
    )
    parsed = json.loads(result)
    assert parsed["status"] == "approved"
    assert parsed["summary_paths"] == ["raw/summaries/a.md", "raw/summaries/b.md"]
    emit.assert_awaited_once()
    msg = emit.call_args[0][0]
    assert isinstance(msg, CompileProposalMessage)
    assert msg.rationale == "promote findings"


async def test_propose_compile_batch_declined(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    registry = ApprovalRegistry()
    emit = AsyncMock()

    async def _emit_and_decline(msg):
        registry.decline(msg.proposal_id)

    emit.side_effect = _emit_and_decline

    agent = _Agent(tmp_path, approval_registry=registry)
    result = await ProposeCompileBatch().run(
        {"summary_paths": ["raw/summaries/a.md"], "rationale": "r"},
        _ctx(agent, emit=emit),
    )
    parsed = json.loads(result)
    assert parsed["status"] == "declined"


async def test_propose_compile_batch_empty_paths(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    agent = _Agent(tmp_path, approval_registry=ApprovalRegistry())
    result = await ProposeCompileBatch().run(
        {"summary_paths": [], "rationale": "r"},
        _ctx(agent),
    )
    assert "Error" in result
    assert "non-empty" in result.lower() or "empty" in result.lower()


async def test_propose_compile_batch_missing_rationale(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    agent = _Agent(tmp_path, approval_registry=ApprovalRegistry())
    result = await ProposeCompileBatch().run(
        {"summary_paths": ["raw/summaries/a.md"]},
        _ctx(agent),
    )
    assert "Error" in result and "rationale" in result


async def test_propose_compile_batch_no_registry(tmp_path):
    agent = _Agent(tmp_path, approval_registry=None)
    result = await ProposeCompileBatch().run(
        {"summary_paths": ["raw/summaries/a.md"], "rationale": "r"},
        _ctx(agent),
    )
    assert "not available" in result.lower()


async def test_propose_compile_batch_declined_with_edit(tmp_path):
    """Declined + edited successor returns successor's approved result."""
    import json
    from agent_core.approval_registry import ApprovalRegistry
    from pal.protocol import CompileProposalMessage

    registry = ApprovalRegistry()
    emit = AsyncMock()
    original_pid = None

    async def _emit_and_decline(msg):
        nonlocal original_pid
        original_pid = msg.proposal_id
        registry.decline(msg.proposal_id)

    emit.side_effect = _emit_and_decline

    agent = _Agent(tmp_path, approval_registry=registry)
    # Patch get_successor to return an edited proposal
    edited = MagicMock()
    edited.proposal_id = "p-edit"
    edited.summary_paths = ["raw/summaries/edited.md"]
    registry.get_successor = MagicMock(return_value=edited)

    result = await ProposeCompileBatch().run(
        {"summary_paths": ["raw/summaries/a.md"], "rationale": "r"},
        _ctx(agent, emit=emit),
    )
    parsed = json.loads(result)
    assert parsed["proposal_id"] == "p-edit"
    assert parsed["status"] == "approved"
    assert parsed["summary_paths"] == ["raw/summaries/edited.md"]


# ---------------------------------------------------------------------------
# CompileBatch
# ---------------------------------------------------------------------------

async def test_compile_batch_happy_path(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    registry = ApprovalRegistry()
    pid = registry.create_proposal(
        kind="compile",
        summary_paths=["raw/summaries/a.md", "raw/summaries/b.md"],
        rationale="r",
    )
    registry.approve(pid)

    compiler = MagicMock()
    compiler.compile_one = AsyncMock(return_value={
        "status": "ok",
        "title": "T",
        "article_path_rel": "AI/T.md",
    })

    agent = _Agent(tmp_path, compiler=compiler, approval_registry=registry)
    result = await CompileBatch().run({"proposal_id": pid}, _ctx(agent))
    parsed = json.loads(result)
    assert parsed["total"] == 2
    assert parsed["ok"] == 2
    assert registry.get(pid).status == "consumed"


async def test_compile_batch_missing_proposal_id(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    agent = _Agent(tmp_path, compiler=MagicMock(), approval_registry=ApprovalRegistry())
    result = await CompileBatch().run({}, _ctx(agent))
    assert "Error" in result and "proposal_id" in result


async def test_compile_batch_unknown_proposal(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    agent = _Agent(tmp_path, compiler=MagicMock(), approval_registry=ApprovalRegistry())
    result = await CompileBatch().run({"proposal_id": "does-not-exist"}, _ctx(agent))
    assert "unknown" in result.lower()


async def test_compile_batch_pending_proposal(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    registry = ApprovalRegistry()
    pid = registry.create_proposal(kind="compile", summary_paths=["a.md"], rationale="r")
    agent = _Agent(tmp_path, compiler=MagicMock(), approval_registry=registry)
    result = await CompileBatch().run({"proposal_id": pid}, _ctx(agent))
    assert "not approved" in result.lower()


async def test_compile_batch_consumed_proposal(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    registry = ApprovalRegistry()
    pid = registry.create_proposal(kind="compile", summary_paths=["a.md"], rationale="r")
    registry.approve(pid)
    registry.consume(pid)

    agent = _Agent(tmp_path, compiler=MagicMock(), approval_registry=registry)
    result = await CompileBatch().run({"proposal_id": pid}, _ctx(agent))
    assert "already" in result.lower() or "consumed" in result.lower()


async def test_compile_batch_wrong_kind(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    registry = ApprovalRegistry()
    pid = registry.create_proposal(
        kind="reorg",
        operations=[{"type": "move", "src": "A.md", "dst": "B.md"}],
        rationale="r",
    )
    registry.approve(pid)

    agent = _Agent(tmp_path, compiler=MagicMock(), approval_registry=registry)
    result = await CompileBatch().run({"proposal_id": pid}, _ctx(agent))
    assert "not a compile proposal" in result.lower()


async def test_compile_batch_partial_failure(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry

    registry = ApprovalRegistry()
    pid = registry.create_proposal(
        kind="compile",
        summary_paths=["raw/summaries/good.md", "raw/summaries/bad.md"],
        rationale="r",
    )
    registry.approve(pid)

    async def fake_compile_one(path):
        if "good" in path:
            return {"status": "ok", "title": "Good", "article_path_rel": "A/Good.md"}
        return {"status": "error", "reason": "categorization failed"}

    compiler = MagicMock()
    compiler.compile_one = fake_compile_one

    agent = _Agent(tmp_path, compiler=compiler, approval_registry=registry)
    result = await CompileBatch().run({"proposal_id": pid}, _ctx(agent))
    parsed = json.loads(result)
    assert parsed["ok"] == 1
    assert parsed["error_count"] == 1
    assert registry.get(pid).status == "consumed"


async def test_compile_batch_no_managers(tmp_path):
    agent = _Agent(tmp_path, compiler=None, approval_registry=None)
    result = await CompileBatch().run({"proposal_id": "p"}, _ctx(agent))
    assert "not available" in result.lower()
