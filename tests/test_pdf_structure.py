"""Tests for pal.pdf_structure: PDF chapter detection and extraction."""
from pal.pdf_structure import ChapterBoundary


def test_chapter_boundary_has_title_and_start_page():
    b = ChapterBoundary(title="Introduction", start_page=2)
    assert b.title == "Introduction"
    assert b.start_page == 2
