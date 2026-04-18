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
