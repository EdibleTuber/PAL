"""Tests for source_file plumbing through the compile path."""

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
    article = _make_article()
    updated = append_timeline_entry(
        article,
        source_url="https://example.com/a",
        source_hash="def456",
        source_file="",
        summary="Summary of web source.",
    )
    sources = updated.meta["sources"]
    assert len(sources) == 1
    assert sources[0]["url"] == "https://example.com/a"
    assert sources[0].get("source_file", "") == ""
    assert sources[0]["hash"] == "def456"


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
