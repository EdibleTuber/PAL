import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from pal.reorg import Reorganizer
from pal.tools import ToolExecutor


def _make_vault(tmp_path: Path) -> Path:
    (tmp_path / "Security").mkdir()
    (tmp_path / "IoT").mkdir()
    (tmp_path / "Security" / "methodology.md").write_text("---\ntitle: M\n---\nbody\n")
    return tmp_path


def _make_executor(vault: Path, wiki=None, retrieval=None) -> ToolExecutor:
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None, retrieval=None)
    return ToolExecutor(
        vault_path=vault,
        retrieval=retrieval,
        wiki=wiki,
        reorganizer=reorg,
    )


def test_move_file_moves_and_triggers_reindex(tmp_path: Path):
    vault = _make_vault(tmp_path)
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    executor = _make_executor(vault, retrieval=retrieval)

    result = asyncio.run(executor.run_async("move_file", {
        "src": "Security/methodology.md",
        "dst": "IoT/methodology.md",
    }))
    parsed = json.loads(result)

    assert parsed["moved"] == "Security/methodology.md -> IoT/methodology.md"
    assert parsed["reindex_queued"] is True
    assert (vault / "IoT" / "methodology.md").exists()
    assert not (vault / "Security" / "methodology.md").exists()
    retrieval.trigger_reindex.assert_awaited_once()


def test_move_file_rejects_system_dirs(tmp_path: Path):
    vault = tmp_path
    (vault / "_wisdom").mkdir()
    (vault / "_wisdom" / "x.md").write_text("x")
    (vault / "IoT").mkdir()
    executor = _make_executor(vault)
    result = asyncio.run(executor.run_async("move_file", {
        "src": "_wisdom/x.md",
        "dst": "IoT/x.md",
    }))
    parsed = json.loads(result)
    assert "error" in parsed
    assert "system" in parsed["error"].lower()


def test_move_file_rejects_missing_src(tmp_path: Path):
    vault = _make_vault(tmp_path)
    executor = _make_executor(vault)
    result = asyncio.run(executor.run_async("move_file", {
        "src": "Security/ghost.md",
        "dst": "IoT/ghost.md",
    }))
    parsed = json.loads(result)
    assert "error" in parsed


def test_move_file_rejects_existing_dst(tmp_path: Path):
    vault = _make_vault(tmp_path)
    (vault / "IoT" / "methodology.md").write_text("existing")
    executor = _make_executor(vault)
    result = asyncio.run(executor.run_async("move_file", {
        "src": "Security/methodology.md",
        "dst": "IoT/methodology.md",
    }))
    parsed = json.loads(result)
    assert "error" in parsed
    assert "exist" in parsed["error"].lower()


def test_move_file_rejects_empty_args(tmp_path: Path):
    executor = _make_executor(tmp_path)
    result = asyncio.run(executor.run_async("move_file", {"src": "", "dst": ""}))
    parsed = json.loads(result)
    assert "error" in parsed


def test_move_file_errors_without_reorganizer(tmp_path: Path):
    executor = ToolExecutor(vault_path=tmp_path, retrieval=None, wiki=None)
    result = asyncio.run(executor.run_async("move_file", {
        "src": "a/b.md", "dst": "c/d.md",
    }))
    parsed = json.loads(result)
    assert "error" in parsed
