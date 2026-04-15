import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pal.approval_registry import ApprovalRegistry
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
