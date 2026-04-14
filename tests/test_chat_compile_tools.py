from pathlib import Path
from unittest.mock import MagicMock

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
