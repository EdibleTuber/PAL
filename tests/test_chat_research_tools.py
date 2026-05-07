"""Tests for the legacy ToolExecutor's remaining surface area.

Phase F PR3: search_web, propose_research, research_topic have been
migrated to pal.tools.research Tool subclasses. Their tests now live in
tests/test_tools_research.py. This file retains only:
  - test_tool_executor_accepts_new_dependencies: confirms the ToolExecutor
    constructor still accepts all of its keyword arguments (backwards compat
    for the remaining legacy tools that haven't migrated yet).
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_core.approval_registry import ApprovalRegistry
from pal._legacy_tools import ToolExecutor


def test_tool_executor_accepts_new_dependencies(tmp_path: Path):
    registry = ApprovalRegistry()
    websearch = MagicMock()
    researcher = MagicMock()
    proposal_emitter = MagicMock()

    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        wiki=None,
        approval_registry=registry,
        websearch=websearch,
        researcher=researcher,
        proposal_emitter=proposal_emitter,
    )

    assert executor.approval_registry is registry
    assert executor.websearch is websearch
    assert executor.researcher is researcher
    assert executor.proposal_emitter is proposal_emitter
