import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_core.approval_registry import ApprovalRegistry
from pal.tools import ToolExecutor


def test_tool_executor_accepts_reorganizer(tmp_path: Path):
    reorganizer = MagicMock()
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        reorganizer=reorganizer,
    )
    assert executor.reorganizer is reorganizer


@pytest.mark.asyncio
async def test_propose_reorg_approved(tmp_path):
    registry = ApprovalRegistry()
    emitted = []
    reorganizer = MagicMock()
    reorganizer.validate_operations = MagicMock(return_value=[])
    reorganizer.count_references = MagicMock(return_value=3)

    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        proposal_emitter=emitted.append,
        reorganizer=reorganizer,
    )

    async def approve_later():
        for _ in range(50):
            if emitted:
                break
            await asyncio.sleep(0.01)
        registry.approve(emitted[0].proposal_id)

    ops = [{"type": "move", "src": "A.md", "dst": "B.md"}]
    asyncio.create_task(approve_later())
    output = await executor.run_async(
        "propose_reorg",
        {"operations": ops, "rationale": "rename"},
    )
    assert '"status": "approved"' in output
    from pal.protocol import ReorgProposalMessage
    assert isinstance(emitted[0], ReorgProposalMessage)
    assert emitted[0].operations == ops
    assert emitted[0].references_preview == 3


@pytest.mark.asyncio
async def test_propose_reorg_rejects_empty_operations(tmp_path):
    registry = ApprovalRegistry()
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        proposal_emitter=MagicMock(),
        reorganizer=MagicMock(),
    )
    output = await executor.run_async(
        "propose_reorg",
        {"operations": [], "rationale": "r"},
    )
    assert "Error" in output


@pytest.mark.asyncio
async def test_propose_reorg_rejects_missing_rationale(tmp_path):
    registry = ApprovalRegistry()
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        proposal_emitter=MagicMock(),
        reorganizer=MagicMock(),
    )
    output = await executor.run_async(
        "propose_reorg",
        {"operations": [{"type": "move", "src": "A.md", "dst": "B.md"}]},
    )
    assert "Error" in output and "rationale" in output


@pytest.mark.asyncio
async def test_propose_reorg_surfaces_validation_errors(tmp_path):
    """Pre-validation failures from Reorganizer.validate_operations
    should surface as a clear error without creating a proposal."""
    registry = ApprovalRegistry()
    emitted = []
    reorganizer = MagicMock()
    reorganizer.validate_operations = MagicMock(return_value=["src does not exist: A.md"])

    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        proposal_emitter=emitted.append,
        reorganizer=reorganizer,
    )
    output = await executor.run_async(
        "propose_reorg",
        {"operations": [{"type": "move", "src": "A.md", "dst": "B.md"}],
         "rationale": "r"},
    )
    assert "Error" in output
    assert "src does not exist" in output
    assert emitted == []  # no proposal emitted


@pytest.mark.asyncio
async def test_reorg_runs_approved_proposal(tmp_path):
    registry = ApprovalRegistry()
    ops = [{"type": "move", "src": "A.md", "dst": "B.md"}]
    pid = registry.create_proposal(kind="reorg", operations=ops, rationale="r")
    registry.approve(pid)

    reorganizer = MagicMock()
    async def fake_exec(ops):
        return [{"op": "move", "src": "A.md", "dst": "B.md",
                 "status": "ok", "references_rewritten": 2}]
    reorganizer.execute_operations_async = fake_exec
    reorganizer.validate_operations = MagicMock(return_value=[])

    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        reorganizer=reorganizer,
    )
    output = await executor.run_async("reorg", {"proposal_id": pid})
    assert '"total": 1' in output
    assert '"ok": 1' in output
    assert '"references_rewritten": 2' in output
    assert registry.get(pid).status == "consumed"


@pytest.mark.asyncio
async def test_reorg_refuses_unknown_proposal(tmp_path):
    registry = ApprovalRegistry()
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        reorganizer=MagicMock(),
    )
    output = await executor.run_async("reorg", {"proposal_id": "unknown"})
    assert "unknown" in output.lower() or "not found" in output.lower()


@pytest.mark.asyncio
async def test_reorg_refuses_wrong_kind(tmp_path):
    registry = ApprovalRegistry()
    pid = registry.create_proposal(
        kind="research", topic="t", depth=3, rationale="r",
    )
    registry.approve(pid)
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        reorganizer=MagicMock(),
    )
    output = await executor.run_async("reorg", {"proposal_id": pid})
    assert "not a reorg proposal" in output.lower()


@pytest.mark.asyncio
async def test_reorg_pre_validation_blocks_execution(tmp_path):
    """If validate_operations returns errors post-approval (e.g., vault
    state changed between proposal and execute), reorg should not
    run execute_operations_async."""
    registry = ApprovalRegistry()
    ops = [{"type": "move", "src": "A.md", "dst": "B.md"}]
    pid = registry.create_proposal(kind="reorg", operations=ops, rationale="r")
    registry.approve(pid)

    reorganizer = MagicMock()
    reorganizer.validate_operations = MagicMock(return_value=["src missing"])
    reorganizer.execute_operations_async = AsyncMock()

    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        reorganizer=reorganizer,
    )
    output = await executor.run_async("reorg", {"proposal_id": pid})
    assert "invalid" in output.lower() or "src missing" in output.lower()
    reorganizer.execute_operations_async.assert_not_called()
