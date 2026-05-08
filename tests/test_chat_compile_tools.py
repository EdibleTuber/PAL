"""Legacy ToolExecutor tests for compile tools.

Phase F PR4: compile_summary, propose_compile_batch, compile_batch have been
migrated to pal.tools.compile Tool subclasses. Their tests now live in
tests/test_tools_compile.py. This file retains only:
  - test_tool_executor_accepts_compiler: confirms the ToolExecutor constructor
    still accepts the compiler kwarg (backward compat for setup() in agent.py
    until PR7 removes legacy_tool_executor entirely).
"""
from pathlib import Path
from unittest.mock import MagicMock

from pal._legacy_tools import ToolExecutor


def test_tool_executor_accepts_compiler(tmp_path: Path):
    compiler = MagicMock()
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        compiler=compiler,
    )
    assert executor.compiler is compiler
