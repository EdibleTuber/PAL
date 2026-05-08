"""Legacy ToolExecutor tests for reorg tools.

Phase F PR4: propose_reorg, propose_promote, reorg have been migrated to
pal.tools.reorg Tool subclasses. Their tests now live in
tests/test_tools_reorg.py. This file retains only:
  - test_tool_executor_accepts_reorganizer: confirms the ToolExecutor
    constructor still accepts the reorganizer kwarg (backward compat for
    setup() in agent.py until PR7 removes legacy_tool_executor entirely).
"""
from pathlib import Path
from unittest.mock import MagicMock

from pal._legacy_tools import ToolExecutor


def test_tool_executor_accepts_reorganizer(tmp_path: Path):
    reorganizer = MagicMock()
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        reorganizer=reorganizer,
    )
    assert executor.reorganizer is reorganizer
