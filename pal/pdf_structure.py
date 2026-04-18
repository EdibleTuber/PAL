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
import json as _json
import logging

logger = logging.getLogger(__name__)

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
    """Extract the concatenated text and max span size for a text block.

    Spans within a single line are joined without a separator (they are
    typically run-of-text). Lines within a block are joined with a space
    so multi-line chapter titles like "The First / Pattern" do not
    collapse to "The FirstPattern".
    """
    line_texts: list[str] = []
    max_size = 0.0
    for line in block.get("lines", []):
        spans_text: list[str] = []
        for span in line.get("spans", []):
            spans_text.append(span.get("text", ""))
            size = float(span.get("size", 0))
            if size > max_size:
                max_size = size
        joined_line = "".join(spans_text).strip()
        if joined_line:
            line_texts.append(joined_line)
    text = " ".join(line_texts).strip()
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


def build_llm_sample(doc, head_chars: int = 120) -> str:
    """Build a compact per-page sample for LLM-TOC reconstruction.

    One line per page: `p.<1-indexed-num> [size=NN] <first head_chars of text>`.
    Intended to be small enough to fit in ctx even for long books.
    """
    lines = []
    for page_index in range(len(doc)):
        page = doc[page_index]
        page_dict = page.get_text("dict")
        first_text = ""
        first_size = 0.0
        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            parsed = _block_text_and_size(block)
            if parsed is None:
                continue
            first_text, first_size = parsed
            break
        snippet = first_text[:head_chars].replace("\n", " ")
        lines.append(f"p.{page_index + 1} [size={first_size:.0f}] {snippet}")
    return "\n".join(lines)


async def detect_from_llm_toc(doc, inference) -> list[ChapterBoundary] | None:
    """Tier 3: ask the LLM to reconstruct a TOC from a compact per-page sample.

    Used only when tiers 1 and 2 both return None. Returns None on empty
    response, malformed JSON, or zero/one candidate.
    """
    sample = build_llm_sample(doc)
    system_prompt = (
        "You are given a page-by-page sample of a PDF. Each line shows a "
        "page number, the font size of the first text block on that page, "
        "and up to 120 characters of that first block. Identify the pages "
        "that look like chapter starts based on font-size jumps, short "
        "title-like text, and position at the top of a page. Return ONLY "
        'a JSON array of objects: [{"page": <1-indexed-page>, "title": "..."}]. '
        "Return an empty array [] if you cannot find clear chapter boundaries."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": sample},
    ]
    try:
        result = await inference.complete(messages, reasoning="off")
    except Exception as exc:
        logger.warning("LLM-TOC detection inference failed: %s", exc)
        return None

    content = (getattr(result, "content", "") or "").strip()
    # Accept either a bare JSON array or one wrapped in extra prose.
    first_bracket = content.find("[")
    last_bracket = content.rfind("]")
    if first_bracket == -1 or last_bracket == -1 or last_bracket < first_bracket:
        return None
    try:
        entries = _json.loads(content[first_bracket : last_bracket + 1])
    except _json.JSONDecodeError:
        return None

    if not isinstance(entries, list) or len(entries) < 2:
        return None

    boundaries: list[ChapterBoundary] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw_page = entry.get("page")
        title = entry.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        # Coerce page to int: tolerate float (1.0) and string ("1") from the LLM.
        # bool is a subclass of int in Python; reject it explicitly.
        if isinstance(raw_page, bool):
            continue
        try:
            page = int(raw_page)
        except (TypeError, ValueError):
            continue
        if page < 1 or page > len(doc):
            continue
        boundaries.append(ChapterBoundary(title=title.strip(), start_page=page - 1))

    if len(boundaries) < 2:
        return None
    return boundaries
