"""Tests for pal.pdf_structure: PDF chapter detection and extraction."""
from unittest.mock import MagicMock

from pal.pdf_structure import ChapterBoundary, detect_from_toc


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
