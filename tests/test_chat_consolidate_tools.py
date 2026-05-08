"""Legacy ToolExecutor tests for consolidate tools.

Phase F PR4: propose_consolidate, consolidate have been migrated to
pal.tools.consolidate Tool subclasses. Their tests now live in
tests/test_tools_consolidate.py. This file retains only:
  - test_tool_executor_accepts_consolidator: confirms the ToolExecutor
    constructor still accepts the consolidator kwarg (backward compat for
    setup() in agent.py until PR7 removes legacy_tool_executor entirely).
"""
from pathlib import Path
from unittest.mock import MagicMock

from pal._legacy_tools import ToolExecutor


def test_tool_executor_accepts_consolidator(tmp_path: Path):
    consolidator = MagicMock()
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        consolidator=consolidator,
    )
    assert executor.consolidator is consolidator
