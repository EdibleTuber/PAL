"""Tests for PAL vault tools (Phase F PR2)."""
import json
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


_UNSET = object()


class _Agent:
    def __init__(self, vault_path, retrieval=None, wiki=_UNSET, reorganizer=_UNSET):
        self.config = _Config(vault_path)
        self.retrieval = retrieval
        self.wiki = MagicMock() if wiki is _UNSET else wiki
        self.reorganizer = MagicMock() if reorganizer is _UNSET else reorganizer


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

async def test_edit_file_happy_path(tmp_path):
    """Rewrite body: read_article called, write_article called with preserved title+tags, reindex triggered."""
    (tmp_path / "x.md").write_text("---\ntitle: X\n---\n\nold body")
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    wiki = MagicMock()
    wiki.read_article.return_value = ({"title": "X", "tags": ["t1"]}, "old body")
    wiki.write_article = MagicMock()
    wiki.git_commit = MagicMock()
    result = await EditFile().run(
        {"path": "x.md", "content": "new body"},
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=wiki)),
    )
    assert result == "Updated: x.md"
    wiki.read_article.assert_called_once_with("x.md")
    wiki.write_article.assert_called_once_with("x.md", "X", "new body", tags=["t1"])
    wiki.git_commit.assert_called_once()
    retrieval.trigger_reindex.assert_awaited_once()


async def test_edit_file_missing(tmp_path):
    """File not found returns descriptive error; reindex NOT called."""
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    wiki = MagicMock()
    result = await EditFile().run(
        {"path": "nope.md", "content": "new body"},
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=wiki)),
    )
    assert "does not exist" in result.lower()
    retrieval.trigger_reindex.assert_not_awaited()
    wiki.write_article.assert_not_called()


async def test_edit_file_no_wiki(tmp_path):
    """No wiki manager returns appropriate error."""
    (tmp_path / "x.md").write_text("body")
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    result = await EditFile().run(
        {"path": "x.md", "content": "new body"},
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=None)),
    )
    assert "no wiki manager" in result.lower()
    retrieval.trigger_reindex.assert_not_awaited()


async def test_edit_file_empty_content(tmp_path):
    (tmp_path / "x.md").write_text("body")
    result = await EditFile().run(
        {"path": "x.md", "content": ""},
        _ctx(_Agent(tmp_path)),
    )
    assert "'content' parameter is required" in result


# --- CreateFile (must trigger reindex on success) ---

async def test_create_file_happy_path(tmp_path):
    """write_article called with all four args; commit called; reindex triggered."""
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    wiki = MagicMock()
    wiki.write_article = MagicMock()
    wiki.git_commit = MagicMock()
    result = await CreateFile().run(
        {"path": "raw/notes/test.md", "title": "Test Note", "content": "body text", "tags": ["tag1"]},
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=wiki)),
    )
    assert result == "Created: raw/notes/test.md"
    wiki.write_article.assert_called_once_with(
        "raw/notes/test.md", "Test Note", "body text", tags=["tag1"]
    )
    wiki.git_commit.assert_called_once()
    retrieval.trigger_reindex.assert_awaited_once()


async def test_create_file_refuses_overwrite(tmp_path):
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "exists.md").write_text("original")
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    wiki = MagicMock()
    result = await CreateFile().run(
        {"path": "raw/exists.md", "title": "T", "content": "new"},
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=wiki)),
    )
    assert "already" in result.lower() or "exists" in result.lower()
    retrieval.trigger_reindex.assert_not_awaited()
    wiki.write_article.assert_not_called()


async def test_create_file_missing_title(tmp_path):
    result = await CreateFile().run(
        {"path": "raw/x.md", "title": "", "content": "body"},
        _ctx(_Agent(tmp_path)),
    )
    assert "'title' parameter is required" in result


async def test_create_file_no_wiki(tmp_path):
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    result = await CreateFile().run(
        {"path": "raw/x.md", "title": "T", "content": "body"},
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=None)),
    )
    assert "no wiki manager" in result.lower()
    retrieval.trigger_reindex.assert_not_awaited()


# --- MoveFile (must trigger reindex on success) ---

async def test_move_file_happy_path(tmp_path):
    """reorganizer.move_single called; wiki.git_commit called; reindex triggered; returns JSON."""
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    wiki = MagicMock()
    wiki.git_commit = MagicMock()
    reorganizer = MagicMock()
    reorganizer.move_single = MagicMock()
    result = await MoveFile().run(
        {"src": "Security/old.md", "dst": "IoT/old.md"},
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=wiki, reorganizer=reorganizer)),
    )
    parsed = json.loads(result)
    assert parsed == {"moved": "Security/old.md -> IoT/old.md", "reindex_queued": True}
    reorganizer.move_single.assert_called_once_with("Security/old.md", "IoT/old.md")
    wiki.git_commit.assert_called_once()
    retrieval.trigger_reindex.assert_awaited_once()


async def test_move_file_no_reorganizer(tmp_path):
    result = await MoveFile().run(
        {"src": "src.md", "dst": "dst.md"},
        _ctx(_Agent(tmp_path, reorganizer=None)),
    )
    parsed = json.loads(result)
    assert "error" in parsed
    assert "reorganizer" in parsed["error"].lower()


async def test_move_file_move_single_raises_file_not_found(tmp_path):
    reorganizer = MagicMock()
    reorganizer.move_single.side_effect = FileNotFoundError("src not found")
    result = await MoveFile().run(
        {"src": "ghost.md", "dst": "dst.md"},
        _ctx(_Agent(tmp_path, reorganizer=reorganizer)),
    )
    parsed = json.loads(result)
    assert "error" in parsed
    assert "src not found" in parsed["error"]


async def test_move_file_move_single_raises_file_exists_error(tmp_path):
    reorganizer = MagicMock()
    reorganizer.move_single.side_effect = FileExistsError("dst already exists")
    result = await MoveFile().run(
        {"src": "src.md", "dst": "dst.md"},
        _ctx(_Agent(tmp_path, reorganizer=reorganizer)),
    )
    parsed = json.loads(result)
    assert "error" in parsed
    assert "dst already exists" in parsed["error"]


async def test_move_file_move_single_raises_value_error(tmp_path):
    reorganizer = MagicMock()
    reorganizer.move_single.side_effect = ValueError("invalid path")
    result = await MoveFile().run(
        {"src": "src.md", "dst": "raw/dst.md"},
        _ctx(_Agent(tmp_path, reorganizer=reorganizer)),
    )
    parsed = json.loads(result)
    assert "error" in parsed


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
        {"path": "_wisdom/x.md", "content": "hacked"},
        _ctx(_Agent(tmp_path)),
    )
    assert "not allowed" in result.lower()


async def test_edit_file_path_traversal(tmp_path):
    result = await EditFile().run(
        {"path": "../../etc/passwd", "content": "hacked"},
        _ctx(_Agent(tmp_path)),
    )
    assert "escapes" in result.lower() or "outside vault" in result.lower()


async def test_edit_file_no_reindex_when_no_retrieval(tmp_path):
    (tmp_path / "x.md").write_text("body")
    wiki = MagicMock()
    wiki.read_article.return_value = ({"title": "X", "tags": None}, "body")
    wiki.write_article = MagicMock()
    wiki.git_commit = MagicMock()
    # No retrieval — should still succeed without error
    result = await EditFile().run(
        {"path": "x.md", "content": "new body"},
        _ctx(_Agent(tmp_path, retrieval=None, wiki=wiki)),
    )
    assert "updated" in result.lower()
    wiki.write_article.assert_called_once()


# --- Extra coverage for CreateFile ---

async def test_create_file_system_dir(tmp_path):
    result = await CreateFile().run(
        {"path": "_wisdom/new.md", "title": "T", "content": "content"},
        _ctx(_Agent(tmp_path)),
    )
    assert "not allowed" in result.lower()


async def test_create_file_rejects_promoted_category(tmp_path):
    result = await CreateFile().run(
        {"path": "Research/newtons-laws.md", "title": "T", "content": "Three laws."},
        _ctx(_Agent(tmp_path)),
    )
    assert "scoped to raw/" in result


async def test_create_file_wiki_write_not_called_outside_raw(tmp_path):
    """wiki.write_article must NOT be called when path is not under raw/."""
    wiki = MagicMock()
    result = await CreateFile().run(
        {"path": "Security/new.md", "title": "T", "content": "body"},
        _ctx(_Agent(tmp_path, wiki=wiki)),
    )
    wiki.write_article.assert_not_called()
    assert "scoped to raw/" in result


async def test_create_file_path_traversal(tmp_path):
    result = await CreateFile().run(
        {"path": "../../etc/evil.md", "title": "T", "content": "hacked"},
        _ctx(_Agent(tmp_path)),
    )
    assert "escapes" in result.lower() or "outside vault" in result.lower()


# --- MoveFile extra coverage ---

async def test_move_file_empty_args(tmp_path):
    reorganizer = MagicMock()
    result = await MoveFile().run(
        {"src": "", "dst": ""},
        _ctx(_Agent(tmp_path, reorganizer=reorganizer)),
    )
    parsed = json.loads(result)
    assert "error" in parsed
