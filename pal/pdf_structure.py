"""PDF chapter detection and per-chapter extraction.

Three-tier detection with fallback, for replacing MarkItDown's fragile
heading-on-extracted-markdown chunking with structural cues from the
PDF itself.

Tiers, tried in order, first to produce >= 2 candidate boundaries wins:
  1. TOC: use the PDF's embedded table of contents via pymupdf.
  2. Typography: infer boundaries from font-size transitions.
  3. LLM-TOC: send a compact per-page sample to the LLM and have it
     reconstruct a candidate TOC from structural cues.

If all three fail, the caller falls back to single-file output.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChapterBoundary:
    """A candidate chapter start: a title and the 0-indexed page it begins on."""
    title: str
    start_page: int


def detect_from_toc(doc) -> list[ChapterBoundary] | None:
    """Tier 1: use the PDF's embedded table of contents.

    Returns a list of ChapterBoundary for each top-level (level == 1) TOC
    entry, with start_page converted to 0-indexed. Returns None if the
    TOC is missing or has fewer than two top-level entries.

    `doc` is a pymupdf.Document (aka fitz.Document). Accepts a duck-typed
    object exposing get_toc() for testability.
    """
    toc = doc.get_toc()
    if not toc:
        return None
    level_one = [
        ChapterBoundary(title=title.strip(), start_page=page - 1)
        for level, title, page in toc
        if level == 1
    ]
    if len(level_one) < 2:
        return None
    return level_one
