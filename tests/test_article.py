"""Tests for article module -- compiled truth + timeline format."""
from datetime import datetime, timezone

import pytest

from pal.article import (
    Article,
    TimelineEntry,
    serialize_article,
    parse_article,
    TIMELINE_MARKER,
    append_timeline_entry,
    validate_compiled_truth,
)


def _make_entry(date="2026-04-12", label="example.com", url="https://example.com/page",
                hash="abc12345", added="2026-04-12T14:30:00+00:00",
                summary="Key findings from this source."):
    return TimelineEntry(
        date=date, source_label=label, source_url=url,
        source_hash=hash, added=added, summary=summary,
    )


def test_serialize_empty_article():
    article = Article(
        meta={"title": "Test", "status": "compiled"},
        compiled_truth="## Overview\n\nTest article.\n\n## Key Concepts\n\n- Concept A\n",
        timeline=[],
    )
    text = serialize_article(article)
    assert "---" in text
    assert "title: Test" in text
    assert "## Overview" in text
    assert TIMELINE_MARKER in text


def test_serialize_article_with_timeline():
    entry = _make_entry()
    article = Article(
        meta={"title": "Test", "status": "compiled"},
        compiled_truth="## Overview\n\nTest.\n\n## Key Concepts\n\n- A\n",
        timeline=[entry],
    )
    text = serialize_article(article)
    assert TIMELINE_MARKER in text
    assert "### 2026-04-12 - example.com" in text
    assert "**Source:** https://example.com/page" in text
    assert "**Added:** 2026-04-12T14:30:00+00:00" in text
    assert "**Source hash:** abc12345" in text
    assert "Key findings from this source." in text


def test_serialize_preserves_multiple_timeline_entries():
    entries = [
        _make_entry(date="2026-04-10", label="first.com", summary="First source."),
        _make_entry(date="2026-04-12", label="second.com", summary="Second source."),
    ]
    article = Article(
        meta={"title": "Test", "status": "compiled"},
        compiled_truth="## Overview\n\nTest.\n\n## Key Concepts\n\n- A\n",
        timeline=entries,
    )
    text = serialize_article(article)
    assert "### 2026-04-10 - first.com" in text
    assert "### 2026-04-12 - second.com" in text
    assert text.index("first.com") < text.index("second.com")


def test_parse_article_with_timeline():
    text = (
        "---\n"
        "title: Test\n"
        "status: compiled\n"
        "---\n"
        "## Overview\n\nSome content.\n\n"
        "## Key Concepts\n\n- A\n\n"
        "<!-- TIMELINE -->\n\n"
        "### 2026-04-12 - example.com\n"
        "**Source:** https://example.com/page\n"
        "**Added:** 2026-04-12T14:30:00+00:00\n"
        "**Source hash:** abc12345\n\n"
        "Key findings from this source.\n"
    )
    article = parse_article(text)
    assert article.meta["title"] == "Test"
    assert "## Overview" in article.compiled_truth
    assert "## Key Concepts" in article.compiled_truth
    assert TIMELINE_MARKER not in article.compiled_truth
    assert len(article.timeline) == 1
    assert article.timeline[0].source_url == "https://example.com/page"
    assert article.timeline[0].source_hash == "abc12345"
    assert "Key findings" in article.timeline[0].summary


def test_parse_article_without_timeline():
    """Legacy article with no TIMELINE marker -- entire body is compiled truth."""
    text = (
        "---\n"
        "title: Legacy\n"
        "status: compiled\n"
        "---\n"
        "# Legacy Article\n\nOld style content.\n"
    )
    article = parse_article(text)
    assert article.meta["title"] == "Legacy"
    assert "Legacy Article" in article.compiled_truth
    assert article.timeline == []


def test_parse_article_multiple_entries():
    text = (
        "---\n"
        "title: Multi\n"
        "---\n"
        "## Overview\n\nContent.\n\n"
        "<!-- TIMELINE -->\n\n"
        "### 2026-04-10 - first.com\n"
        "**Source:** https://first.com/a\n"
        "**Added:** 2026-04-10T10:00:00+00:00\n"
        "**Source hash:** aaa\n\n"
        "First source findings.\n\n"
        "### 2026-04-12 - second.com\n"
        "**Source:** https://second.com/b\n"
        "**Added:** 2026-04-12T12:00:00+00:00\n"
        "**Source hash:** bbb\n\n"
        "Second source findings.\n"
    )
    article = parse_article(text)
    assert len(article.timeline) == 2
    assert article.timeline[0].source_label == "first.com"
    assert article.timeline[1].source_label == "second.com"
    assert "First source" in article.timeline[0].summary
    assert "Second source" in article.timeline[1].summary


def test_parse_roundtrip():
    """serialize -> parse -> serialize should produce the same output."""
    entry = _make_entry()
    original = Article(
        meta={"title": "Roundtrip", "status": "compiled"},
        compiled_truth="## Overview\n\nTest.\n\n## Key Concepts\n\n- A\n",
        timeline=[entry],
    )
    text1 = serialize_article(original)
    parsed = parse_article(text1)
    text2 = serialize_article(parsed)
    assert text1 == text2


def test_append_timeline_entry():
    article = Article(
        meta={"title": "Test", "status": "compiled", "sources": []},
        compiled_truth="## Overview\n\nTest.\n\n## Key Concepts\n\n- A\n",
        timeline=[],
    )
    updated = append_timeline_entry(
        article=article,
        source_url="https://new.com/doc",
        source_hash="new123",
        summary="New findings from this source.",
    )
    assert len(updated.timeline) == 1
    assert updated.timeline[0].source_url == "https://new.com/doc"
    assert updated.timeline[0].source_hash == "new123"
    assert "New findings" in updated.timeline[0].summary
    assert updated.timeline[0].date  # should have a date
    assert updated.timeline[0].added  # should have a timestamp
    assert len(updated.meta["sources"]) == 1
    assert updated.meta["sources"][0]["url"] == "https://new.com/doc"


def test_append_timeline_entry_preserves_existing():
    existing = _make_entry(label="old.com", url="https://old.com/page", hash="old123")
    article = Article(
        meta={
            "title": "Test", "status": "compiled",
            "sources": [{"url": "https://old.com/page", "hash": "old123", "added": "2026-04-10T10:00:00+00:00"}],
        },
        compiled_truth="## Overview\n\nTest.\n\n## Key Concepts\n\n- A\n",
        timeline=[existing],
    )
    updated = append_timeline_entry(
        article=article,
        source_url="https://new.com/doc",
        source_hash="new456",
        summary="New findings.",
    )
    assert len(updated.timeline) == 2
    assert updated.timeline[0].source_label == "old.com"
    assert updated.timeline[1].source_url == "https://new.com/doc"
    assert len(updated.meta["sources"]) == 2


def test_append_timeline_entry_extracts_hostname():
    article = Article(
        meta={"title": "Test", "status": "compiled", "sources": []},
        compiled_truth="## Overview\n\nTest.\n\n## Key Concepts\n\n- A\n",
        timeline=[],
    )
    updated = append_timeline_entry(
        article=article,
        source_url="https://docs.python.org/3/library/asyncio.html",
        source_hash="xyz",
        summary="Asyncio docs.",
    )
    assert updated.timeline[0].source_label == "docs.python.org"


def test_validate_compiled_truth_valid():
    text = "## Overview\n\nGood article.\n\n## Key Concepts\n\n- Concept\n"
    issues = validate_compiled_truth(text)
    assert issues == []


def test_validate_compiled_truth_missing_overview():
    text = "## Key Concepts\n\n- Something\n"
    issues = validate_compiled_truth(text)
    assert any("Overview" in i for i in issues)


def test_validate_compiled_truth_missing_key_concepts():
    text = "## Overview\n\nSomething.\n"
    issues = validate_compiled_truth(text)
    assert any("Key Concepts" in i for i in issues)


def test_validate_compiled_truth_allows_optional_sections():
    text = (
        "## Overview\n\nGood.\n\n"
        "## Key Concepts\n\n- A\n\n"
        "## Usage\n\nSome usage.\n\n"
        "## Gotchas\n\n- Watch out.\n"
    )
    issues = validate_compiled_truth(text)
    assert issues == []
