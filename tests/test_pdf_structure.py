"""Tests for pal.pdf_structure: PDF chapter detection and extraction."""
from unittest.mock import MagicMock

from pal.pdf_structure import ChapterBoundary, detect_from_toc, detect_from_typography


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
