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
from collections import Counter

# Tunable thresholds. Module-level so they're easy to find and adjust
# once we have real-corpus feedback.
TYPOGRAPHY_HEADING_MULTIPLIER = 1.4  # heading font size >= multiplier * body baseline
TYPOGRAPHY_MAX_HEADING_CHARS = 150   # headings are short
TYPOGRAPHY_MIN_CANDIDATES = 2        # fewer than this = not useful structure


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


def _iter_blocks(doc):
    """Yield (page_index, block) for each text block in the document."""
    for page_index in range(len(doc)):
        page = doc[page_index]
        page_dict = page.get_text("dict")
        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:
                continue  # not a text block (e.g. image)
            yield page_index, block


def _block_text_and_size(block) -> tuple[str, float] | None:
    """Extract the concatenated text and max span size for a text block."""
    texts = []
    max_size = 0.0
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            texts.append(span.get("text", ""))
            size = float(span.get("size", 0))
            if size > max_size:
                max_size = size
    text = "".join(texts).strip()
    if not text or max_size <= 0:
        return None
    return text, max_size


def detect_from_typography(doc) -> list[ChapterBoundary] | None:
    """Tier 2: infer chapter boundaries from font-size transitions.

    Self-calibrates: the modal font size across all text blocks is taken
    as the body baseline (ties broken toward the smaller size, which is
    almost always body text), and candidate headings are blocks at
    >= TYPOGRAPHY_HEADING_MULTIPLIER * baseline that are short enough to
    plausibly be a title.

    Returns None if fewer than TYPOGRAPHY_MIN_CANDIDATES are found.
    """
    # Pass 1: collect all block (page, text, size) tuples.
    rows: list[tuple[int, str, float]] = []
    for page_index, block in _iter_blocks(doc):
        parsed = _block_text_and_size(block)
        if parsed is None:
            continue
        text, size = parsed
        rows.append((page_index, text, size))

    if not rows:
        return None

    # Body baseline: the most common rounded font size. Tie-break toward
    # the smaller size so a short doc with matched heading and body
    # counts still treats body as the baseline.
    size_counts = Counter(round(s, 1) for _, _, s in rows)
    baseline = min(size_counts, key=lambda s: (-size_counts[s], s))
    threshold = baseline * TYPOGRAPHY_HEADING_MULTIPLIER

    # Candidates: short, large-font blocks.
    candidates: list[ChapterBoundary] = []
    for page_index, text, size in rows:
        if size < threshold:
            continue
        if len(text) > TYPOGRAPHY_MAX_HEADING_CHARS:
            continue
        # Dedupe multiple candidates on the same page: take the first one
        # per page, which is almost always the chapter title.
        if candidates and candidates[-1].start_page == page_index:
            continue
        candidates.append(ChapterBoundary(title=text, start_page=page_index))

    if len(candidates) < TYPOGRAPHY_MIN_CANDIDATES:
        return None
    return candidates
