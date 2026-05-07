"""Tests for MoveFile Tool subclass (Phase F PR2).

Previously tested via pal._legacy_tools.ToolExecutor.run_async("move_file").
Now tests the pal.tools.vault.MoveFile Tool subclass directly.

MoveFile delegates to Reorganizer.move_single, which handles system-path
guards, parent-mkdir, and the actual rename.
"""
import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pal.tools.vault import MoveFile


@dataclass
class _Config:
    vault_path: Path


_UNSET = object()


class _Agent:
    def __init__(self, vault_path, retrieval=None, reorganizer=_UNSET, wiki=_UNSET):
        self.config = _Config(vault_path)
        self.retrieval = retrieval
        self.reorganizer = MagicMock() if reorganizer is _UNSET else reorganizer
        self.wiki = MagicMock() if wiki is _UNSET else wiki


def _ctx(agent):
    class _C:
        pass
    c = _C()
    c.agent = agent
    return c


def _make_vault(tmp_path: Path) -> Path:
    (tmp_path / "Security").mkdir()
    (tmp_path / "IoT").mkdir()
    (tmp_path / "Security" / "methodology.md").write_text("---\ntitle: M\n---\nbody\n")
    return tmp_path


def _make_reorganizer_that_renames(vault: Path):
    """Return a MagicMock reorganizer whose move_single performs the actual rename."""
    reorganizer = MagicMock()

    def _do_move(src, dst):
        src_path = vault / src
        dst_path = vault / dst
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        src_path.rename(dst_path)

    reorganizer.move_single.side_effect = _do_move
    return reorganizer


@pytest.mark.asyncio
async def test_move_file_moves_and_triggers_reindex(tmp_path: Path):
    vault = _make_vault(tmp_path)
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    reorganizer = _make_reorganizer_that_renames(vault)

    result = await MoveFile().run(
        {"src": "Security/methodology.md", "dst": "IoT/methodology.md"},
        _ctx(_Agent(vault, retrieval=retrieval, reorganizer=reorganizer)),
    )
    parsed = json.loads(result)

    assert parsed["moved"] == "Security/methodology.md -> IoT/methodology.md"
    assert parsed["reindex_queued"] is True
    assert (vault / "IoT" / "methodology.md").exists()
    assert not (vault / "Security" / "methodology.md").exists()
    retrieval.trigger_reindex.assert_awaited_once()


@pytest.mark.asyncio
async def test_move_file_rejects_missing_src(tmp_path: Path):
    vault = _make_vault(tmp_path)
    reorganizer = MagicMock()
    reorganizer.move_single.side_effect = FileNotFoundError("Security/ghost.md not found")
    result = await MoveFile().run(
        {"src": "Security/ghost.md", "dst": "IoT/ghost.md"},
        _ctx(_Agent(vault, reorganizer=reorganizer)),
    )
    parsed = json.loads(result)
    assert "error" in parsed


@pytest.mark.asyncio
async def test_move_file_rejects_existing_dst(tmp_path: Path):
    vault = _make_vault(tmp_path)
    (vault / "IoT" / "methodology.md").write_text("existing")
    reorganizer = MagicMock()
    reorganizer.move_single.side_effect = FileExistsError("destination already exists")
    result = await MoveFile().run(
        {"src": "Security/methodology.md", "dst": "IoT/methodology.md"},
        _ctx(_Agent(vault, reorganizer=reorganizer)),
    )
    parsed = json.loads(result)
    assert "error" in parsed
    assert "exist" in parsed["error"].lower()


@pytest.mark.asyncio
async def test_move_file_rejects_empty_args(tmp_path: Path):
    result = await MoveFile().run(
        {"src": "", "dst": ""},
        _ctx(_Agent(tmp_path)),
    )
    parsed = json.loads(result)
    assert "error" in parsed


@pytest.mark.asyncio
async def test_move_file_rejects_system_dirs(tmp_path: Path):
    """Reorganizer rejects system-path moves by raising ValueError."""
    vault = tmp_path
    (vault / "_wisdom").mkdir()
    (vault / "_wisdom" / "x.md").write_text("x")
    (vault / "IoT").mkdir()
    reorganizer = MagicMock()
    reorganizer.move_single.side_effect = ValueError("moves into system directories are not allowed")
    result = await MoveFile().run(
        {"src": "_wisdom/x.md", "dst": "IoT/x.md"},
        _ctx(_Agent(vault, reorganizer=reorganizer)),
    )
    parsed = json.loads(result)
    assert "error" in parsed
    # The error text comes from the reorganizer's ValueError message.
    assert "system" in parsed["error"].lower() or "not allowed" in parsed["error"].lower()
