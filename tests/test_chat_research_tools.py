from pathlib import Path
from unittest.mock import MagicMock

from pal.approval_registry import ApprovalRegistry
from pal.tools import ToolExecutor


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
