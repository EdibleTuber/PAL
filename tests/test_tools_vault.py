"""Tests for PAL vault tools (Phase F PR2)."""
import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pal.tools.vault import (
    CreateFile,
    EditFile,
    MoveFile,
    DeleteFile,
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


# --- DeleteFile (atomic git rm, surfaces reindex failure) ---

async def test_delete_file_removes_file_via_git_rm_and_commits(tmp_path):
    """Happy path: file removed via git_rm, committed, reindex triggered, JSON ok."""
    from pal.tools.vault import DeleteFile

    (tmp_path / "old.md").write_text("---\ntitle: Old\n---\n\nbody", encoding="utf-8")
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    wiki = MagicMock()
    wiki.git_rm = MagicMock()
    wiki.git_commit = MagicMock()

    result = await DeleteFile().run(
        {"path": "old.md"},
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=wiki)),
    )
    parsed = json.loads(result)
    assert parsed["status"] == "deleted"
    assert parsed["path"] == "old.md"
    assert parsed["reindex"] == "ok"
    wiki.git_rm.assert_called_once_with("old.md")
    wiki.git_commit.assert_called_once()
    retrieval.trigger_reindex.assert_awaited_once()


async def test_delete_file_refuses_system_dirs(tmp_path):
    """Refuses paths in underscore-prefixed system directories. File untouched."""
    from pal.tools.vault import DeleteFile

    (tmp_path / "_wisdom").mkdir()
    (tmp_path / "_wisdom" / "rule.md").write_text("body", encoding="utf-8")
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    wiki = MagicMock()
    wiki.git_rm = MagicMock()

    result = await DeleteFile().run(
        {"path": "_wisdom/rule.md"},
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=wiki)),
    )
    assert "system directories" in result.lower()
    assert (tmp_path / "_wisdom" / "rule.md").exists()
    wiki.git_rm.assert_not_called()
    retrieval.trigger_reindex.assert_not_awaited()


async def test_delete_file_refuses_path_escape(tmp_path):
    """Refuses paths that resolve outside the vault. No git_rm call."""
    from pal.tools.vault import DeleteFile

    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    wiki = MagicMock()
    wiki.git_rm = MagicMock()

    result = await DeleteFile().run(
        {"path": "../escape.md"},
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=wiki)),
    )
    assert "escapes outside vault" in result.lower()
    wiki.git_rm.assert_not_called()


async def test_delete_file_surfaces_reindex_failure(tmp_path):
    """Reindex failure: response sets reindex=failed but file is still deleted."""
    from pal.tools.vault import DeleteFile

    (tmp_path / "x.md").write_text("body", encoding="utf-8")
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock(side_effect=RuntimeError("reindex broken"))
    wiki = MagicMock()
    wiki.git_rm = MagicMock()
    wiki.git_commit = MagicMock()

    result = await DeleteFile().run(
        {"path": "x.md"},
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=wiki)),
    )
    parsed = json.loads(result)
    assert parsed["status"] == "deleted"
    assert parsed["reindex"] == "failed"
    wiki.git_rm.assert_called_once_with("x.md")
    wiki.git_commit.assert_called_once()


async def test_delete_file_surfaces_commit_failure(tmp_path):
    """git_commit failure: file is git_rm'd but JSON reports deleted_uncommitted."""
    from pal.tools.vault import DeleteFile

    (tmp_path / "x.md").write_text("body", encoding="utf-8")
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    wiki = MagicMock()
    wiki.git_rm = MagicMock()
    wiki.git_commit = MagicMock(side_effect=RuntimeError("git locked"))

    result = await DeleteFile().run(
        {"path": "x.md"},
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=wiki)),
    )
    parsed = json.loads(result)
    assert parsed["status"] == "deleted_uncommitted"
    assert "git commit failed" in parsed["warning"].lower()
    wiki.git_rm.assert_called_once_with("x.md")
    # Reindex should NOT be triggered when commit failed (file not actually committed-deleted)
    retrieval.trigger_reindex.assert_not_awaited()


# --- ReplaceInFile (body-only, frontmatter preserved, restore on commit failure) ---

async def test_replace_in_file_replaces_in_body_only(tmp_path):
    """Frontmatter containing the same string is preserved; only body is replaced."""
    from pal.tools.vault import ReplaceInFile

    # Frontmatter has 'AI' in tags. Body has 'AI' too. Replace only in body.
    (tmp_path / "x.md").write_text(
        "---\ntitle: X\ntags:\n  - AI\n  - hardware\n---\n\nThis is about AI.",
        encoding="utf-8",
    )
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    wiki = MagicMock()
    wiki.git_commit = MagicMock()

    result = await ReplaceInFile().run(
        {"path": "x.md", "old_string": "AI", "new_string": "ML"},
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=wiki)),
    )
    parsed = json.loads(result)
    assert parsed["status"] == "replaced"
    assert parsed["occurrences"] == 1

    text = (tmp_path / "x.md").read_text(encoding="utf-8")
    assert "tags:" in text and "- AI" in text  # frontmatter preserved
    assert "This is about ML." in text  # body changed
    assert "This is about AI." not in text


async def test_replace_in_file_refuses_non_unique_without_replace_all(tmp_path):
    """Multiple body matches without replace_all returns error mentioning widening."""
    from pal.tools.vault import ReplaceInFile

    (tmp_path / "x.md").write_text(
        "---\ntitle: X\n---\n\nfoo bar foo bar",
        encoding="utf-8",
    )
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    wiki = MagicMock()

    result = await ReplaceInFile().run(
        {"path": "x.md", "old_string": "bar", "new_string": "baz"},
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=wiki)),
    )
    assert "appears" in result.lower()
    assert "widen" in result.lower() or "replace_all" in result.lower()
    text = (tmp_path / "x.md").read_text(encoding="utf-8")
    assert "foo bar foo bar" in text  # unchanged
    wiki.git_commit.assert_not_called()


async def test_replace_in_file_replace_all_only_in_body(tmp_path):
    """replace_all replaces every body occurrence; frontmatter occurrences untouched."""
    from pal.tools.vault import ReplaceInFile

    # 1 occurrence of 'foo' in frontmatter (as a tag), 3 in body
    (tmp_path / "x.md").write_text(
        "---\ntitle: X\ntags:\n  - foo\n---\n\nfoo bar foo bar foo",
        encoding="utf-8",
    )
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    wiki = MagicMock()
    wiki.git_commit = MagicMock()

    result = await ReplaceInFile().run(
        {
            "path": "x.md",
            "old_string": "foo",
            "new_string": "qux",
            "replace_all": True,
        },
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=wiki)),
    )
    parsed = json.loads(result)
    assert parsed["status"] == "replaced"
    assert parsed["occurrences"] == 3  # body only

    text = (tmp_path / "x.md").read_text(encoding="utf-8")
    assert "tags:" in text and "- foo" in text  # frontmatter foo preserved
    assert "qux bar qux bar qux" in text


async def test_replace_in_file_restores_on_commit_failure(tmp_path):
    """git_commit failure restores original body content."""
    from pal.tools.vault import ReplaceInFile

    original = "---\ntitle: X\n---\n\nhello world"
    (tmp_path / "x.md").write_text(original, encoding="utf-8")
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    wiki = MagicMock()
    wiki.git_commit = MagicMock(side_effect=RuntimeError("git locked"))

    result = await ReplaceInFile().run(
        {"path": "x.md", "old_string": "hello", "new_string": "goodbye"},
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=wiki)),
    )
    assert "git commit failed" in result.lower()
    text = (tmp_path / "x.md").read_text(encoding="utf-8")
    # Exact restore (no round-trip mutation)
    assert text == original
    assert "goodbye" not in text


async def test_replace_in_file_empty_new_string_deletes_match(tmp_path):
    """Empty new_string deletes the matched content."""
    from pal.tools.vault import ReplaceInFile

    (tmp_path / "x.md").write_text(
        "---\ntitle: X\n---\n\nkeep this DELETE_ME and this",
        encoding="utf-8",
    )
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    wiki = MagicMock()
    wiki.git_commit = MagicMock()

    result = await ReplaceInFile().run(
        {"path": "x.md", "old_string": " DELETE_ME", "new_string": ""},
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=wiki)),
    )
    parsed = json.loads(result)
    assert parsed["status"] == "replaced"
    text = (tmp_path / "x.md").read_text(encoding="utf-8")
    assert "keep this and this" in text
    assert "DELETE_ME" not in text


# --- EditFile description rewrite (regression guard) ---

def test_edit_file_description_mentions_replace_in_file():
    """The edit_file description must redirect targeted edits to replace_in_file."""
    from pal.tools.vault import EditFile
    desc = EditFile.description
    assert "replace_in_file" in desc
