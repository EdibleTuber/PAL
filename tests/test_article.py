"""Tests for article module -- compiled truth + timeline format."""
from datetime import datetime, timezone

import pytest

from pal.article import (
    Article,
    TimelineEntry,
    serialize_article,
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
