"""Tests for source_file plumbing through the compile path."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from pal.article import Article, append_timeline_entry


def _make_article():
    return Article(
        meta={
            "title": "Test",
            "compiled_at": "2026-05-09T00:00:00+00:00",
            "status": "compiled",
            "sources": [],
        },
        compiled_truth="## Overview\n\nbody\n",
    )


def test_append_timeline_entry_writes_source_file_when_url_empty():
    article = _make_article()
    updated = append_timeline_entry(
        article,
        source_url="",
        source_hash="abc123",
        source_file="raw/archived/Agentic_Design_Patterns.pdf",
        summary="Summary of local PDF.",
    )
    sources = updated.meta["sources"]
    assert len(sources) == 1
    assert sources[0]["url"] == ""
    assert sources[0]["source_file"] == "raw/archived/Agentic_Design_Patterns.pdf"
    assert sources[0]["hash"] == "abc123"


def test_append_timeline_entry_writes_url_when_present():
    """The URL supplied by the caller must appear in the recorded source entry."""
    article = _make_article()
    updated = append_timeline_entry(
        article,
        source_url="https://example.com/a",
        source_hash="def456",
        source_file="",
        summary="Summary of web source.",
    )
    sources = updated.meta["sources"]
    assert sources[0]["url"] == "https://example.com/a"


def test_append_timeline_entry_omits_empty_source_file_key():
    """When source_file is empty, the key should not appear at all (avoid bloating frontmatter)."""
    article = _make_article()
    updated = append_timeline_entry(
        article,
        source_url="https://example.com/a",
        source_hash="def456",
        source_file="",
        summary="Summary of web source.",
    )
    sources = updated.meta["sources"]
    assert "source_file" not in sources[0]


# ---------------------------------------------------------------------------
# Compile path: source_file read from summary frontmatter
# ---------------------------------------------------------------------------

def _make_wiki():
    """Minimal wiki fake that records calls and returns no existing articles."""
    wiki = MagicMock()
    wiki.list_articles.return_value = []
    wiki.rebuild_index.return_value = None
    wiki.git_init.return_value = None
    wiki.git_commit.return_value = None
    return wiki


def _make_inference(article_body: str):
    """Fake inference whose .complete() returns a SimpleNamespace with .content."""
    inference = MagicMock()
    result = SimpleNamespace(content=article_body)
    inference.complete = AsyncMock(return_value=result)
    return inference


def _make_categorizer(category: str):
    categorizer = MagicMock()
    categorizer.categorize = AsyncMock(return_value=category)
    return categorizer


def _make_prompt_builder():
    pb = MagicMock()
    pb.build.return_value = "You are a helpful assistant."
    return pb


def _build_test_compiler(vault, article_body=None):
    """Build a Compiler with MagicMock/AsyncMock fakes pointing at the given vault."""
    from pal.compiler import Compiler

    if article_body is None:
        article_body = "## Overview\n\nFrom PDF.\n\n## Key Concepts\n\nSome concepts.\n"
    return Compiler(
        vault_path=vault,
        wiki=_make_wiki(),
        inference=_make_inference(article_body),
        categorizer=_make_categorizer("AI"),
        prompt_builder=_make_prompt_builder(),
    )


@pytest.mark.asyncio
async def test_compile_one_extracts_source_file_from_summary_meta(tmp_path):
    """compile_one passes source_file from summary frontmatter into the article sources."""
    vault = tmp_path / "vault"
    vault.mkdir()
    raw_dir = vault / "raw" / "summaries"
    raw_dir.mkdir(parents=True)

    summary_path_rel = "raw/summaries/test-pdf-summary.md"
    summary_file = vault / summary_path_rel
    summary_file.write_text(
        "---\n"
        "title: Test PDF Summary\n"
        "source_url: ''\n"
        "source_file: raw/archived/test.pdf\n"
        "source_hash: abc123\n"
        "---\n"
        "\n"
        "## Overview\n\nSummary body.\n"
    )

    compiler = _build_test_compiler(vault)

    result = await compiler.compile_one(summary_path_rel)

    assert result["status"] == "ok", f"Unexpected status: {result}"

    article_path = vault / result["article_path_rel"]
    text = article_path.read_text()

    assert "source_file: raw/archived/test.pdf" in text, (
        "source_file from summary frontmatter should appear in serialized article"
    )
    assert "url: ''" in text, (
        "empty source_url should serialize as url: ''"
    )


@pytest.mark.asyncio
async def test_compile_one_rejects_empty_source_url_and_source_file(tmp_path):
    """When both source_url and source_file are empty, compile must refuse."""
    from pal.compiler import EmptySourceError

    vault = tmp_path / "vault"
    vault.mkdir()
    raw = vault / "raw" / "summaries"
    raw.mkdir(parents=True)

    summary_path = raw / "no-source.md"
    summary_path.write_text(
        "---\n"
        "title: No Source\n"
        "source_url: ''\n"
        "source_file: ''\n"
        "source_hash: ''\n"
        "---\n"
        "\n"
        "Summary body.\n"
    )

    compiler = _build_test_compiler(vault)

    with pytest.raises(EmptySourceError):
        await compiler.compile_one("raw/summaries/no-source.md")
