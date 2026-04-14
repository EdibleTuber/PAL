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
