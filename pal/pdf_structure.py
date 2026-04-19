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
from typing import Literal
import json as _json
import logging
import re

import pymupdf4llm

logger = logging.getLogger(__name__)

# Tunable thresholds. Module-level so they're easy to find and adjust
# once we have real-corpus feedback.
TYPOGRAPHY_HEADING_MULTIPLIER = 1.4  # heading font size >= multiplier * body baseline
TYPOGRAPHY_MAX_HEADING_CHARS = 150   # headings are short
TYPOGRAPHY_MIN_CANDIDATES = 2        # fewer than this = not useful structure

# A TOC entry that spans this many pages or more AND has at least two child
# entries one level deeper is descended into: its children replace it in
# the boundary list. This handles books shaped as "Part I: The Patterns"
# (316 pages) containing 20 level-2 chapters, where using the Part as one
# chunk would produce a single unusably-large raw file.
TOC_DESCEND_MIN_PAGES = 40
TOC_DESCEND_MIN_CHILDREN = 2


@dataclass
class ChapterBoundary:
    """A candidate chapter start: a title and the 0-indexed page it begins on."""
    title: str
    start_page: int


def _descend_into_toc(
    entries: list[tuple[int, str, int]],
    level: int,
    range_start: int,
    range_end: int,
) -> list[ChapterBoundary]:
    """Recursively build a boundary list from TOC entries.

    Takes all valid entries pre-normalized as (level, title, 0-indexed page).
    Walks siblings at `level` whose page falls within [range_start, range_end].
    For each sibling, computes its page span (to the next sibling or
    range_end). If the span is >= TOC_DESCEND_MIN_PAGES AND at least
    TOC_DESCEND_MIN_CHILDREN entries exist one level deeper within that
    span, replace the sibling with the descended children; otherwise keep
    the sibling as-is.
    """
    siblings = [
        (title, page)
        for entry_level, title, page in entries
        if entry_level == level and range_start <= page <= range_end
    ]
    if not siblings:
        return []

    result: list[ChapterBoundary] = []
    for i, (title, page) in enumerate(siblings):
        entry_start = page
        if i + 1 < len(siblings):
            entry_end = siblings[i + 1][1] - 1
        else:
            entry_end = range_end
        span = entry_end - entry_start + 1

        if span >= TOC_DESCEND_MIN_PAGES:
            children = _descend_into_toc(
                entries,
                level=level + 1,
                range_start=entry_start,
                range_end=entry_end,
            )
            if len(children) >= TOC_DESCEND_MIN_CHILDREN:
                result.extend(children)
                continue

        result.append(ChapterBoundary(title=title, start_page=entry_start))

    return result


def detect_from_toc(doc) -> list[ChapterBoundary] | None:
    """Tier 1: use the PDF's embedded table of contents.

    Walks the TOC starting at level 1. For any level-1 entry that spans
    TOC_DESCEND_MIN_PAGES or more and has at least TOC_DESCEND_MIN_CHILDREN
    level-2 entries nested within, descends into those children instead of
    emitting the level-1 entry. Applies recursively one level deeper, so a
    huge level-2 section with level-3 chapters also descends.

    Returns None when the TOC is missing, has fewer than two level-1
    entries to anchor detection, or descent produces fewer than two
    boundaries overall.

    `doc` is a pymupdf.Document (aka fitz.Document). Accepts a duck-typed
    object exposing get_toc() and __len__() for testability.
    """
    toc = doc.get_toc()
    if not toc:
        return None

    total_pages = len(doc)
    if total_pages <= 0:
        return None

    valid_entries = [
        (level, title.strip(), page - 1)
        for level, title, page in toc
        if isinstance(level, int)
        and isinstance(page, int)
        and page >= 1
        and title
        and title.strip()
    ]

    level_one_count = sum(1 for level, _, _ in valid_entries if level == 1)
    if level_one_count < 2:
        return None

    boundaries = _descend_into_toc(
        valid_entries,
        level=1,
        range_start=0,
        range_end=total_pages - 1,
    )

    if len(boundaries) < 2:
        return None
    return boundaries


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


@dataclass
class DetectionResult:
    """The outcome of detect_chapters.

    `method` names which tier won. `boundaries` is empty iff method is
    'single-file', signaling the caller should write the whole markdown
    as one raw/sources/<slug>/full.md file.
    """
    method: Literal["toc", "typography", "llm-toc", "single-file"]
    boundaries: list[ChapterBoundary]


async def detect_chapters(doc, inference=None) -> DetectionResult:
    """Run the three detection tiers in order; return the first hit.

    `inference` is optional; when None, tier 3 is skipped and we fall
    straight to single-file. This keeps the function testable without
    an inference client and gives callers a lightweight path when they
    don't want to spend an LLM call on ingestion.
    """
    toc_result = detect_from_toc(doc)
    if toc_result is not None:
        return DetectionResult(method="toc", boundaries=toc_result)

    typo_result = detect_from_typography(doc)
    if typo_result is not None:
        return DetectionResult(method="typography", boundaries=typo_result)

    if inference is not None:
        llm_result = await detect_from_llm_toc(doc, inference)
        if llm_result is not None:
            return DetectionResult(method="llm-toc", boundaries=llm_result)

    return DetectionResult(method="single-file", boundaries=[])


# Indirect so tests can monkeypatch without poking pymupdf4llm directly.
def _pymupdf4llm_to_markdown(path: str, pages: list[int]) -> str:
    return pymupdf4llm.to_markdown(path, pages=pages)


@dataclass
class Chapter:
    title: str
    start_page: int  # 0-indexed, inclusive
    end_page: int    # 0-indexed, inclusive
    markdown: str


def compute_chapter_ranges(
    boundaries: list[ChapterBoundary], total_pages: int
) -> list[tuple[int, int]]:
    """For each boundary, return (start_page, end_page) spanning to the
    page before the next boundary, or to the last page for the final one.
    Both ends inclusive, 0-indexed.
    """
    ranges: list[tuple[int, int]] = []
    for i, b in enumerate(boundaries):
        start = b.start_page
        if i + 1 < len(boundaries):
            end = boundaries[i + 1].start_page - 1
        else:
            end = total_pages - 1
        ranges.append((start, end))
    return ranges


def extract_chapters(
    pdf_path: str,
    boundaries: list[ChapterBoundary],
    total_pages: int,
) -> list[Chapter]:
    """Extract per-chapter markdown using pymupdf4llm.

    Each chapter's markdown covers the page range computed from the
    boundary list. Boundaries whose computed range is empty (end < start,
    which happens when two boundaries collide on the same page) or
    entirely out of bounds (start >= total_pages) are skipped with a
    warning. Caller is responsible for writing the returned chapters to
    disk.
    """
    ranges = compute_chapter_ranges(boundaries, total_pages)
    chapters: list[Chapter] = []
    for b, (start, end) in zip(boundaries, ranges):
        if start >= total_pages or end < start:
            logger.warning(
                "skipping chapter %r with invalid range p.%d-p.%d (total_pages=%d)",
                b.title, start, end, total_pages,
            )
            continue
        pages = list(range(start, end + 1))
        markdown = _pymupdf4llm_to_markdown(pdf_path, pages=pages)
        chapters.append(Chapter(
            title=b.title,
            start_page=start,
            end_page=end,
            markdown=markdown,
        ))
    return chapters


_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Lowercase, non-alphanumerics -> hyphens, collapse runs, trim ends.

    Returns 'untitled' for empty or whitespace-only input so callers
    always get a valid path component.
    """
    lowered = text.lower()
    collapsed = _SLUG_STRIP_RE.sub("-", lowered).strip("-")
    return collapsed or "untitled"
