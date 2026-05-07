import json
from pathlib import Path

import pytest

from agent_core.learning import LearningManager
from pal._legacy_tools import ToolExecutor


def _make_executor(vault: Path) -> ToolExecutor:
    return ToolExecutor(
        vault_path=vault,
        retrieval=None,
        wiki=None,
        learning=LearningManager(vault, "pal"),
    )


def test_add_learning_writes_file(tmp_path: Path):
    executor = _make_executor(tmp_path)
    result = executor.run("add_learning", {
        "title": "Granularity Over Consolidation",
        "body": "Keep articles focused, not merged into master guides.",
    })
    parsed = json.loads(result)
    slug = parsed["slug"]
    assert parsed["title"] == "Granularity Over Consolidation"

    lm = LearningManager(tmp_path, "pal")
    assert lm.exists(slug)
    assert "focused" in lm.get(slug)


def test_add_learning_rejects_empty_title(tmp_path: Path):
    executor = _make_executor(tmp_path)
    result = executor.run("add_learning", {"title": "", "body": "x"})
    parsed = json.loads(result)
    assert "error" in parsed
    assert "title" in parsed["error"].lower()


def test_add_learning_rejects_empty_body(tmp_path: Path):
    executor = _make_executor(tmp_path)
    result = executor.run("add_learning", {"title": "x", "body": ""})
    parsed = json.loads(result)
    assert "error" in parsed
    assert "body" in parsed["error"].lower()


def test_add_learning_errors_without_learning_manager(tmp_path: Path):
    executor = ToolExecutor(vault_path=tmp_path, retrieval=None, wiki=None)
    result = executor.run("add_learning", {"title": "x", "body": "y"})
    parsed = json.loads(result)
    assert "error" in parsed
