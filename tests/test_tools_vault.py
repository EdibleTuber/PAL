"""Tests for PAL vault tools (Phase F PR2)."""
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pal.tools.vault import (
    CreateFile,
    EditFile,
    ListDirectory,
    MoveFile,
    ReadFile,
    SearchContent,
)


@dataclass
class _Config:
    vault_path: Path


class _Agent:
    def __init__(self, vault_path, retrieval=None):
        self.config = _Config(vault_path)
        self.retrieval = retrieval


def _ctx(agent):
    class _C:
        pass
    c = _C()
    c.agent = agent
    return c


# --- ReadFile ---

async def test_read_file_returns_content(tmp_path):
    (tmp_path / "x.md").write_text("---\ntitle: X\n---\n\nbody")
    result = await ReadFile().run({"path": "x.md"}, _ctx(_Agent(tmp_path)))
    assert "body" in result


async def test_read_file_rejects_escape(tmp_path):
    result = await ReadFile().run({"path": "../../etc/passwd"}, _ctx(_Agent(tmp_path)))
    assert "outside vault" in result.lower() or "escape" in result.lower()


async def test_read_file_missing(tmp_path):
    result = await ReadFile().run({"path": "nope.md"}, _ctx(_Agent(tmp_path)))
    assert "not found" in result.lower()


async def test_read_file_truncates_large(tmp_path):
    (tmp_path / "big.md").write_text("x" * 40_000)
    result = await ReadFile().run({"path": "big.md"}, _ctx(_Agent(tmp_path)))
    # PAL's _READ_LIMIT = 32_000; the lift retains the same limit and footer text.
    assert "truncated" in result.lower()


# --- ListDirectory ---

async def test_list_directory_root(tmp_path):
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "b.md").write_text("b")
    (tmp_path / "Notes").mkdir()
    result = await ListDirectory().run({}, _ctx(_Agent(tmp_path)))
    assert "a.md" in result
    assert "b.md" in result


async def test_list_directory_prefix(tmp_path):
    (tmp_path / "agent-1.md").write_text(".")
    (tmp_path / "agent-2.md").write_text(".")
    (tmp_path / "other.md").write_text(".")
    result = await ListDirectory().run({"prefix": "agent-"}, _ctx(_Agent(tmp_path)))
    assert "agent-1.md" in result
    assert "agent-2.md" in result
    assert "other.md" not in result


async def test_list_directory_pagination(tmp_path):
    for i in range(10):
        (tmp_path / f"f{i:02}.md").write_text(".")
    result = await ListDirectory().run({"offset": 5, "limit": 3}, _ctx(_Agent(tmp_path)))
    # Should include at least one file from index 5 onward.
    assert "f05.md" in result or "f06.md" in result or "f07.md" in result


# --- SearchContent ---

async def test_search_content_finds(tmp_path):
    (tmp_path / "x.md").write_text("apple\nbanana")
    result = await SearchContent().run({"query": "banana"}, _ctx(_Agent(tmp_path)))
    assert "x.md" in result
    assert "banana" in result


async def test_search_content_no_match(tmp_path):
    (tmp_path / "x.md").write_text("apple")
    result = await SearchContent().run({"query": "zzz"}, _ctx(_Agent(tmp_path)))
    assert "no match" in result.lower() or result.strip() == "" or "no results" in result.lower()


# --- EditFile (must trigger reindex on success) ---

async def test_edit_file_replaces(tmp_path):
    (tmp_path / "x.md").write_text("hello world")
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    result = await EditFile().run(
        {"path": "x.md", "old_str": "world", "new_str": "PAL"},
        _ctx(_Agent(tmp_path, retrieval=retrieval)),
    )
    assert "error" not in result.lower()[:30]
    assert (tmp_path / "x.md").read_text() == "hello PAL"
    retrieval.trigger_reindex.assert_awaited_once()


async def test_edit_file_missing(tmp_path):
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    result = await EditFile().run(
        {"path": "nope.md", "old_str": "x", "new_str": "y"},
        _ctx(_Agent(tmp_path, retrieval=retrieval)),
    )
    assert "not found" in result.lower()
    retrieval.trigger_reindex.assert_not_awaited()


# --- CreateFile (must trigger reindex on success) ---

async def test_create_file_writes(tmp_path):
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    result = await CreateFile().run(
        {"path": "raw/new.md", "content": "hello"},
        _ctx(_Agent(tmp_path, retrieval=retrieval)),
    )
    assert "error" not in result.lower()[:30]
    assert (tmp_path / "raw" / "new.md").read_text() == "hello"
    retrieval.trigger_reindex.assert_awaited_once()


async def test_create_file_refuses_overwrite(tmp_path):
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "exists.md").write_text("original")
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    result = await CreateFile().run(
        {"path": "raw/exists.md", "content": "new"},
        _ctx(_Agent(tmp_path, retrieval=retrieval)),
    )
    assert "already" in result.lower() or "exists" in result.lower()
    retrieval.trigger_reindex.assert_not_awaited()


# --- MoveFile (must trigger reindex on success) ---

async def test_move_file_renames(tmp_path):
    (tmp_path / "src.md").write_text("content")
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    result = await MoveFile().run(
        {"src": "src.md", "dst": "dst.md"},
        _ctx(_Agent(tmp_path, retrieval=retrieval)),
    )
    assert "error" not in result.lower()[:30]
    assert (tmp_path / "dst.md").read_text() == "content"
    assert not (tmp_path / "src.md").exists()
    retrieval.trigger_reindex.assert_awaited()


# --- Extra coverage for ListDirectory ---

async def test_list_directory_subdir(tmp_path):
    (tmp_path / "Notes").mkdir()
    (tmp_path / "Notes" / "a.md").write_text("a")
    (tmp_path / "Notes" / "b.md").write_text("b")
    result = await ListDirectory().run({"path": "Notes"}, _ctx(_Agent(tmp_path)))
    assert "a.md" in result
    assert "b.md" in result


async def test_list_directory_not_found(tmp_path):
    result = await ListDirectory().run({"path": "nonexistent"}, _ctx(_Agent(tmp_path)))
    assert "not found" in result.lower()


async def test_list_directory_truncates_large_dir(tmp_path):
    big = tmp_path / "AI"
    big.mkdir()
    for i in range(120):
        (big / f"{i:03d}-topic.md").write_text(".")
    result = await ListDirectory().run({"path": "AI"}, _ctx(_Agent(tmp_path)))
    assert "000-topic.md" in result
    assert "049-topic.md" in result
    assert "050-topic.md" not in result
    assert "showing 1-50 of 120" in result.lower()
    assert "offset=50" in result


async def test_list_directory_custom_limit(tmp_path):
    big = tmp_path / "AI"
    big.mkdir()
    for i in range(30):
        (big / f"{i:02d}-topic.md").write_text(".")
    result = await ListDirectory().run({"path": "AI", "limit": 5}, _ctx(_Agent(tmp_path)))
    assert "00-topic.md" in result
    assert "04-topic.md" in result
    assert "05-topic.md" not in result
    assert "showing 1-5 of 30" in result.lower()


async def test_list_directory_hides_system_dirs(tmp_path):
    (tmp_path / "_wisdom").mkdir()
    (tmp_path / "_wisdom" / "x.md").write_text("x")
    (tmp_path / "public.md").write_text("y")
    result = await ListDirectory().run({}, _ctx(_Agent(tmp_path)))
    assert "_wisdom" not in result
    assert "public.md" in result


# --- Extra coverage for SearchContent ---

async def test_search_content_skips_system_dirs(tmp_path):
    (tmp_path / "_wisdom").mkdir()
    (tmp_path / "_wisdom" / "secret.md").write_text("secret content")
    result = await SearchContent().run({"query": "secret"}, _ctx(_Agent(tmp_path)))
    assert "no results" in result.lower()


async def test_search_content_empty_query(tmp_path):
    result = await SearchContent().run({"query": ""}, _ctx(_Agent(tmp_path)))
    assert "error" in result.lower()


# --- Extra coverage for EditFile ---

async def test_edit_file_system_dir(tmp_path):
    (tmp_path / "_wisdom").mkdir()
    (tmp_path / "_wisdom" / "x.md").write_text("content")
    result = await EditFile().run(
        {"path": "_wisdom/x.md", "old_str": "content", "new_str": "hacked"},
        _ctx(_Agent(tmp_path)),
    )
    assert "not allowed" in result.lower()


async def test_edit_file_path_traversal(tmp_path):
    result = await EditFile().run(
        {"path": "../../etc/passwd", "old_str": "root", "new_str": "hacked"},
        _ctx(_Agent(tmp_path)),
    )
    assert "escapes" in result.lower() or "outside vault" in result.lower()


async def test_edit_file_no_reindex_when_no_retrieval(tmp_path):
    (tmp_path / "x.md").write_text("hello world")
    # No retrieval — should still succeed without error
    result = await EditFile().run(
        {"path": "x.md", "old_str": "world", "new_str": "PAL"},
        _ctx(_Agent(tmp_path, retrieval=None)),
    )
    assert "updated" in result.lower()
    assert (tmp_path / "x.md").read_text() == "hello PAL"


# --- Extra coverage for CreateFile ---

async def test_create_file_system_dir(tmp_path):
    result = await CreateFile().run(
        {"path": "_wisdom/new.md", "content": "content"},
        _ctx(_Agent(tmp_path)),
    )
    assert "not allowed" in result.lower()


async def test_create_file_rejects_promoted_category(tmp_path):
    result = await CreateFile().run(
        {"path": "Research/newtons-laws.md", "content": "Three laws."},
        _ctx(_Agent(tmp_path)),
    )
    assert "scoped to raw/" in result


async def test_create_file_creates_parent_dirs(tmp_path):
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    result = await CreateFile().run(
        {"path": "raw/notes/subtopic/article.md", "content": "Deep content."},
        _ctx(_Agent(tmp_path, retrieval=retrieval)),
    )
    assert "error" not in result.lower()[:30]
    assert (tmp_path / "raw" / "notes" / "subtopic" / "article.md").exists()


async def test_create_file_path_traversal(tmp_path):
    result = await CreateFile().run(
        {"path": "../../etc/evil.md", "content": "hacked"},
        _ctx(_Agent(tmp_path)),
    )
    assert "escapes" in result.lower() or "outside vault" in result.lower()


# --- MoveFile extra coverage ---

async def test_move_file_missing_src(tmp_path):
    result = await MoveFile().run(
        {"src": "ghost.md", "dst": "dst.md"},
        _ctx(_Agent(tmp_path)),
    )
    import json
    parsed = json.loads(result)
    assert "error" in parsed


async def test_move_file_existing_dst(tmp_path):
    (tmp_path / "src.md").write_text("content")
    (tmp_path / "dst.md").write_text("existing")
    result = await MoveFile().run(
        {"src": "src.md", "dst": "dst.md"},
        _ctx(_Agent(tmp_path)),
    )
    import json
    parsed = json.loads(result)
    assert "error" in parsed
    assert "exist" in parsed["error"].lower()


async def test_move_file_empty_args(tmp_path):
    result = await MoveFile().run(
        {"src": "", "dst": ""},
        _ctx(_Agent(tmp_path)),
    )
    import json
    parsed = json.loads(result)
    assert "error" in parsed
