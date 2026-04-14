from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pal.approval_registry import ApprovalRegistry
from pal.tools import ToolExecutor


def test_tool_executor_accepts_compiler(tmp_path: Path):
    compiler = MagicMock()
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        compiler=compiler,
    )
    assert executor.compiler is compiler


@pytest.mark.asyncio
async def test_compile_summary_happy_path(tmp_path):
    compiler = MagicMock()

    async def fake_compile_one(path):
        return {
            "status": "ok",
            "title": "Example Article",
            "article_path_rel": "AI-Agents/Example.md",
        }

    compiler.compile_one = fake_compile_one
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        compiler=compiler,
    )
    output = await executor.run_async(
        "compile_summary", {"summary_path": "raw/summaries/foo.md"}
    )
    assert '"status": "ok"' in output
    assert '"title": "Example Article"' in output
    assert "AI-Agents/Example.md" in output


@pytest.mark.asyncio
async def test_compile_summary_not_found_propagates(tmp_path):
    compiler = MagicMock()

    async def fake_compile_one(path):
        return {"status": "not_found", "reason": f"File not found: {path}"}

    compiler.compile_one = fake_compile_one
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        compiler=compiler,
    )
    output = await executor.run_async(
        "compile_summary", {"summary_path": "raw/summaries/missing.md"}
    )
    assert '"status": "not_found"' in output


@pytest.mark.asyncio
async def test_compile_summary_requires_path(tmp_path):
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        compiler=MagicMock(),
    )
    output = await executor.run_async("compile_summary", {})
    assert "Error" in output and "summary_path" in output


@pytest.mark.asyncio
async def test_compile_summary_unavailable_without_compiler(tmp_path):
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        compiler=None,
    )
    output = await executor.run_async(
        "compile_summary", {"summary_path": "raw/summaries/foo.md"}
    )
    assert "not available" in output.lower()


import asyncio


@pytest.mark.asyncio
async def test_propose_compile_batch_approved(tmp_path):
    registry = ApprovalRegistry()
    emitted = []
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        proposal_emitter=emitted.append,
    )

    async def approve_later():
        for _ in range(50):
            if emitted:
                break
            await asyncio.sleep(0.01)
        assert emitted, "proposal was not emitted"
        registry.approve(emitted[0].proposal_id)

    asyncio.create_task(approve_later())
    output = await executor.run_async(
        "propose_compile_batch",
        {
            "summary_paths": ["raw/summaries/a.md", "raw/summaries/b.md"],
            "rationale": "promote findings",
        },
    )
    assert '"status": "approved"' in output
    assert emitted[0].summary_paths == ["raw/summaries/a.md", "raw/summaries/b.md"]
    assert emitted[0].rationale == "promote findings"
    from pal.protocol import CompileProposalMessage
    assert isinstance(emitted[0], CompileProposalMessage)


@pytest.mark.asyncio
async def test_propose_compile_batch_declined(tmp_path):
    registry = ApprovalRegistry()
    emitted = []
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        proposal_emitter=emitted.append,
    )

    async def decline_later():
        for _ in range(50):
            if emitted:
                break
            await asyncio.sleep(0.01)
        registry.decline(emitted[0].proposal_id)

    asyncio.create_task(decline_later())
    output = await executor.run_async(
        "propose_compile_batch",
        {"summary_paths": ["raw/summaries/a.md"], "rationale": "r"},
    )
    assert '"status": "declined"' in output


@pytest.mark.asyncio
async def test_propose_compile_batch_rejects_empty_paths(tmp_path):
    registry = ApprovalRegistry()
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        proposal_emitter=MagicMock(),
    )
    output = await executor.run_async(
        "propose_compile_batch",
        {"summary_paths": [], "rationale": "r"},
    )
    assert "Error" in output
    assert "empty" in output.lower() or "at least one" in output.lower() or "non-empty" in output.lower()


@pytest.mark.asyncio
async def test_propose_compile_batch_requires_rationale(tmp_path):
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=ApprovalRegistry(),
        proposal_emitter=MagicMock(),
    )
    output = await executor.run_async(
        "propose_compile_batch",
        {"summary_paths": ["raw/summaries/a.md"]},
    )
    assert "Error" in output and "rationale" in output
