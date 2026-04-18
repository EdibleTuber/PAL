"""Tests for pal.pdf_structure: PDF chapter detection and extraction."""
import json
from unittest.mock import MagicMock

import pytest

from pal.inference import CompletionResult
from pal.pdf_structure import (
    ChapterBoundary,
    build_llm_sample,
    detect_from_llm_toc,
    detect_from_toc,
    detect_from_typography,
)
from pal.pdf_structure import DetectionResult, detect_chapters
from pal.pdf_structure import Chapter, compute_chapter_ranges, extract_chapters


def _fake_page(blocks):
    """Build a fake page whose get_text("dict") returns the given blocks.

    `blocks` is a list of (size, text, bbox_y_top) tuples. Each becomes a
    single-line, single-span block in the dict shape pymupdf returns.
    """
    page = MagicMock()
    page_dict = {
        "blocks": [
            {
                "type": 0,
                "bbox": [0, bbox_y, 100, bbox_y + size + 2],
                "lines": [{
                    "spans": [{"size": size, "text": text, "font": "TestFont", "flags": 0}],
                }],
            }
            for size, text, bbox_y in blocks
        ]
    }
    page.get_text.return_value = page_dict
    return page


def _fake_doc(pages):
    doc = MagicMock()
    doc.__len__.return_value = len(pages)
    doc.__iter__.return_value = iter(pages)
    doc.__getitem__.side_effect = lambda i: pages[i]
    return doc


def test_chapter_boundary_has_title_and_start_page():
    b = ChapterBoundary(title="Introduction", start_page=2)
    assert b.title == "Introduction"
    assert b.start_page == 2


def test_detect_from_toc_returns_level_one_entries():
    doc = MagicMock()
    doc.get_toc.return_value = [
        [1, "Introduction", 3],
        [2, "Background", 5],
        [1, "The First Pattern", 17],
        [2, "Example", 20],
        [1, "Conclusion", 98],
    ]
    boundaries = detect_from_toc(doc)
    assert boundaries is not None
    assert len(boundaries) == 3
    assert boundaries[0].title == "Introduction"
    assert boundaries[0].start_page == 2  # pymupdf TOC is 1-indexed, we use 0-indexed
    assert boundaries[1].title == "The First Pattern"
    assert boundaries[1].start_page == 16
    assert boundaries[2].title == "Conclusion"
    assert boundaries[2].start_page == 97


def test_detect_from_toc_returns_none_when_toc_empty():
    doc = MagicMock()
    doc.get_toc.return_value = []
    assert detect_from_toc(doc) is None


def test_detect_from_toc_returns_none_when_only_one_level_one():
    doc = MagicMock()
    doc.get_toc.return_value = [
        [1, "Only Chapter", 1],
        [2, "Section A", 2],
    ]
    assert detect_from_toc(doc) is None


def test_detect_from_toc_ignores_level_two_plus():
    doc = MagicMock()
    doc.get_toc.return_value = [
        [2, "Subsection Before Chapter 1", 1],
        [1, "Chapter One", 3],
        [3, "Deep Subsection", 4],
        [1, "Chapter Two", 10],
    ]
    boundaries = detect_from_toc(doc)
    assert boundaries is not None
    assert [b.title for b in boundaries] == ["Chapter One", "Chapter Two"]


def test_detect_from_typography_finds_chapters_at_large_font():
    # Three pages: each starts with a big heading then body text
    pages = [
        _fake_page([
            (18, "Introduction", 50),
            (11, "Body paragraph one.", 100),
            (11, "Body paragraph two.", 150),
        ]),
        _fake_page([
            (11, "More body text.", 50),
            (11, "Still body text.", 100),
        ]),
        _fake_page([
            (18, "The First Pattern", 50),
            (11, "Pattern body.", 100),
        ]),
    ]
    doc = _fake_doc(pages)
    boundaries = detect_from_typography(doc)
    assert boundaries is not None
    assert len(boundaries) == 2
    assert boundaries[0].title == "Introduction"
    assert boundaries[0].start_page == 0
    assert boundaries[1].title == "The First Pattern"
    assert boundaries[1].start_page == 2


def test_detect_from_typography_returns_none_when_flat_typography():
    # All body size, no candidates.
    pages = [
        _fake_page([(11, "All body.", 50), (11, "More.", 100)]),
        _fake_page([(11, "Still body.", 50)]),
    ]
    doc = _fake_doc(pages)
    assert detect_from_typography(doc) is None


def test_detect_from_typography_requires_at_least_two_candidates():
    # Only one big-font block across the whole doc.
    pages = [
        _fake_page([(18, "Only Heading", 50), (11, "Body.", 100)]),
        _fake_page([(11, "All body.", 50)]),
    ]
    doc = _fake_doc(pages)
    assert detect_from_typography(doc) is None


def test_detect_from_typography_skips_long_blocks():
    # A big-font block but longer than the 150-char ceiling should not count.
    long_text = "x" * 200
    pages = [
        _fake_page([(18, long_text, 50), (11, "Body.", 100)]),
        _fake_page([(18, "Real Heading Two", 50), (11, "Body.", 100)]),
        _fake_page([(18, "Real Heading Three", 50), (11, "Body.", 100)]),
    ]
    doc = _fake_doc(pages)
    boundaries = detect_from_typography(doc)
    assert boundaries is not None
    assert [b.title for b in boundaries] == ["Real Heading Two", "Real Heading Three"]


def test_detect_from_typography_preserves_multiline_title():
    """A chapter title that wraps across two lines within one block
    should be preserved as "line one line two", not collapsed."""
    page_one = MagicMock()
    page_one.get_text.return_value = {
        "blocks": [
            {
                "type": 0,
                "bbox": [0, 50, 100, 90],
                "lines": [
                    {"spans": [{"size": 18, "text": "Chapter One:", "font": "F", "flags": 0}]},
                    {"spans": [{"size": 18, "text": "A Beginning", "font": "F", "flags": 0}]},
                ],
            },
            {
                "type": 0,
                "bbox": [0, 100, 100, 120],
                "lines": [{"spans": [{"size": 11, "text": "Body text.", "font": "F", "flags": 0}]}],
            },
        ]
    }
    page_two = MagicMock()
    page_two.get_text.return_value = {
        "blocks": [
            {
                "type": 0,
                "bbox": [0, 50, 100, 90],
                "lines": [{"spans": [{"size": 18, "text": "Chapter Two", "font": "F", "flags": 0}]}],
            },
            {
                "type": 0,
                "bbox": [0, 100, 100, 120],
                "lines": [{"spans": [{"size": 11, "text": "Body text.", "font": "F", "flags": 0}]}],
            },
        ]
    }
    doc = _fake_doc([page_one, page_two])
    boundaries = detect_from_typography(doc)
    assert boundaries is not None
    assert len(boundaries) == 2
    assert boundaries[0].title == "Chapter One: A Beginning"
    assert boundaries[0].start_page == 0
    assert boundaries[1].title == "Chapter Two"


def test_build_llm_sample_compact_per_page():
    pages = [
        _fake_page([(14, "Front Matter", 50), (11, "Acknowledgements text.", 100)]),
        _fake_page([(18, "Introduction", 50), (11, "Welcome to the book.", 100)]),
        _fake_page([(11, "Body continues.", 50)]),
    ]
    doc = _fake_doc(pages)
    sample = build_llm_sample(doc, head_chars=40)
    # Each line has page number, leading font size, snippet.
    lines = sample.strip().splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("p.1")
    assert "Front Matter" in lines[0]
    assert lines[1].startswith("p.2")
    assert "Introduction" in lines[1]
    assert lines[2].startswith("p.3")


@pytest.mark.asyncio
async def test_detect_from_llm_toc_parses_page_numbers():
    pages = [
        _fake_page([(18, "Chapter One", 50), (11, "Body.", 100)]),
        _fake_page([(11, "More body.", 50)]),
        _fake_page([(18, "Chapter Two", 50), (11, "Body.", 100)]),
    ]
    doc = _fake_doc(pages)

    class FakeInference:
        async def complete(self, messages, **kwargs):
            return CompletionResult(
                type="text",
                content=json.dumps([
                    {"page": 1, "title": "Chapter One"},
                    {"page": 3, "title": "Chapter Two"},
                ]),
            )

    boundaries = await detect_from_llm_toc(doc, FakeInference())
    assert boundaries is not None
    assert len(boundaries) == 2
    assert boundaries[0].title == "Chapter One"
    assert boundaries[0].start_page == 0  # 1-indexed in LLM response, 0-indexed internally
    assert boundaries[1].title == "Chapter Two"
    assert boundaries[1].start_page == 2


@pytest.mark.asyncio
async def test_detect_from_llm_toc_returns_none_on_empty_response():
    pages = [_fake_page([(11, "Body.", 50)])]
    doc = _fake_doc(pages)

    class FakeInference:
        async def complete(self, messages, **kwargs):
            return CompletionResult(type="text", content="[]")

    assert await detect_from_llm_toc(doc, FakeInference()) is None


@pytest.mark.asyncio
async def test_detect_from_llm_toc_returns_none_on_malformed_json():
    pages = [_fake_page([(11, "Body.", 50)])]
    doc = _fake_doc(pages)

    class FakeInference:
        async def complete(self, messages, **kwargs):
            return CompletionResult(type="text", content="not json at all")

    assert await detect_from_llm_toc(doc, FakeInference()) is None


@pytest.mark.asyncio
async def test_detect_from_llm_toc_coerces_page_types():
    """Tolerate float and string pages in LLM output (1.0 and '1' both mean page 1)."""
    pages = [
        _fake_page([(18, "Chapter One", 50), (11, "Body.", 100)]),
        _fake_page([(11, "More body.", 50)]),
        _fake_page([(18, "Chapter Two", 50), (11, "Body.", 100)]),
    ]
    doc = _fake_doc(pages)

    class FakeInference:
        async def complete(self, messages, **kwargs):
            return CompletionResult(
                type="text",
                content=json.dumps([
                    {"page": 1.0, "title": "Chapter One"},
                    {"page": "3", "title": "Chapter Two"},
                ]),
            )

    boundaries = await detect_from_llm_toc(doc, FakeInference())
    assert boundaries is not None
    assert len(boundaries) == 2
    assert boundaries[0].title == "Chapter One"
    assert boundaries[0].start_page == 0
    assert boundaries[1].title == "Chapter Two"
    assert boundaries[1].start_page == 2


@pytest.mark.asyncio
async def test_detect_from_llm_toc_rejects_bool_page():
    """bool is int in Python; reject it explicitly to avoid confusing True -> page 1."""
    pages = [_fake_page([(11, "Body.", 50)])]
    doc = _fake_doc(pages)

    class FakeInference:
        async def complete(self, messages, **kwargs):
            return CompletionResult(
                type="text",
                content=json.dumps([
                    {"page": True, "title": "A"},
                    {"page": False, "title": "B"},
                ]),
            )

    assert await detect_from_llm_toc(doc, FakeInference()) is None


@pytest.mark.asyncio
async def test_detect_chapters_uses_toc_when_available():
    doc = MagicMock()
    doc.get_toc.return_value = [
        [1, "A", 1], [1, "B", 10], [1, "C", 20],
    ]
    doc.__len__.return_value = 30
    result = await detect_chapters(doc, inference=None)
    assert result.method == "toc"
    assert len(result.boundaries) == 3


@pytest.mark.asyncio
async def test_detect_chapters_falls_through_to_typography():
    pages = [
        _fake_page([(18, "Intro", 50), (11, "body", 100)]),
        _fake_page([(18, "Next", 50), (11, "body", 100)]),
    ]
    doc = _fake_doc(pages)
    doc.get_toc.return_value = []
    result = await detect_chapters(doc, inference=None)
    assert result.method == "typography"
    assert len(result.boundaries) == 2


@pytest.mark.asyncio
async def test_detect_chapters_falls_through_to_llm_when_no_typography():
    # Flat typography (no font-size transitions).
    pages = [_fake_page([(11, "body", 50)]), _fake_page([(11, "body", 50)])]
    doc = _fake_doc(pages)
    doc.get_toc.return_value = []

    class FakeInference:
        async def complete(self, messages, **kwargs):
            return CompletionResult(
                type="text",
                content=json.dumps([
                    {"page": 1, "title": "One"},
                    {"page": 2, "title": "Two"},
                ]),
            )

    result = await detect_chapters(doc, inference=FakeInference())
    assert result.method == "llm-toc"
    assert len(result.boundaries) == 2


@pytest.mark.asyncio
async def test_detect_chapters_returns_single_file_when_all_tiers_fail():
    pages = [_fake_page([(11, "body", 50)])]
    doc = _fake_doc(pages)
    doc.get_toc.return_value = []

    class FakeInference:
        async def complete(self, messages, **kwargs):
            return CompletionResult(type="text", content="[]")

    result = await detect_chapters(doc, inference=FakeInference())
    assert result.method == "single-file"
    assert result.boundaries == []


@pytest.mark.asyncio
async def test_detect_chapters_skips_llm_when_inference_is_none():
    pages = [_fake_page([(11, "body", 50)])]
    doc = _fake_doc(pages)
    doc.get_toc.return_value = []
    result = await detect_chapters(doc, inference=None)
    assert result.method == "single-file"


def test_compute_chapter_ranges_spans_from_boundary_to_next_start():
    boundaries = [
        ChapterBoundary(title="A", start_page=0),
        ChapterBoundary(title="B", start_page=5),
        ChapterBoundary(title="C", start_page=12),
    ]
    ranges = compute_chapter_ranges(boundaries, total_pages=20)
    assert ranges == [(0, 4), (5, 11), (12, 19)]


def test_compute_chapter_ranges_single_chapter_spans_whole_doc():
    boundaries = [ChapterBoundary(title="Only", start_page=2)]
    ranges = compute_chapter_ranges(boundaries, total_pages=10)
    assert ranges == [(2, 9)]


def test_extract_chapters_uses_pymupdf4llm_per_range(monkeypatch):
    calls = []

    def fake_to_markdown(path, pages=None):
        calls.append((path, tuple(pages)))
        return f"## content for pages {pages[0]}-{pages[-1]}\n"

    import pal.pdf_structure as ps
    monkeypatch.setattr(ps, "_pymupdf4llm_to_markdown", fake_to_markdown)

    boundaries = [
        ChapterBoundary(title="A", start_page=0),
        ChapterBoundary(title="B", start_page=3),
    ]
    chapters = extract_chapters("/fake/path.pdf", boundaries, total_pages=6)
    assert len(chapters) == 2
    assert chapters[0].title == "A"
    assert chapters[0].start_page == 0
    assert chapters[0].end_page == 2
    assert "pages 0-2" in chapters[0].markdown
    assert chapters[1].start_page == 3
    assert chapters[1].end_page == 5
    assert "pages 3-5" in chapters[1].markdown
    assert calls[0] == ("/fake/path.pdf", (0, 1, 2))
    assert calls[1] == ("/fake/path.pdf", (3, 4, 5))
