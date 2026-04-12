"""Tests for article module -- compiled truth + timeline format."""
from datetime import datetime, timezone

import pytest

from pal.article import (
    Article,
    TimelineEntry,
    serialize_article,
    parse_article,
    TIMELINE_MARKER,
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
