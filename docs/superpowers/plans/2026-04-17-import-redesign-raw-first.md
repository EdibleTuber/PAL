# Import Redesign: Raw-First Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reshape `/import` so every supported document type lands as clean chapter files in `raw/sources/<doc-slug>/`, replacing the magic-one-shot convert+chunk+categorize+wiki-write flow with a deterministic raw-first pipeline. For PDFs, swap MarkItDown for pymupdf4llm and use PDF TOC, typography heuristics, or LLM-reconstructed TOC to detect chapters (no more 265-fragment disasters).

**Architecture:** New module `pal/pdf_structure.py` encapsulates chapter detection (three tiers: TOC, typography, LLM-TOC) plus per-chapter extraction via pymupdf4llm. `pal/daemon.py::_handle_import` is rewritten to: always write to `raw/sources/`, skip the categorizer, skip wiki writes, route PDFs through pdf_structure, keep non-PDFs on the existing markdown-heading chunker.

**Tech Stack:** Python 3.12, pymupdf4llm (new dep), pymupdf/fitz (transitive), existing MarkItDown (non-PDF only), existing `chunk_markdown` (non-PDF only), pytest + pytest-asyncio.

**Reference spec:** `docs/superpowers/specs/2026-04-17-import-redesign-raw-first-design.md`

---

## File Structure

### New files

- `pal/pdf_structure.py`: Chapter detection (three tiers) and per-chapter extraction. Single responsibility: take a PDF path, return a list of `(title, start_page, end_page, markdown)` tuples.
- `tests/test_pdf_structure.py`: Unit tests for each detection tier plus the orchestrator.
- `tests/fixtures/pdfs/README.md`: Documents how to add real-PDF fixtures; actual PDFs stay out of git because of size.

### Modified files

- `pyproject.toml`: Add `pymupdf4llm` to dependencies.
- `pal/daemon.py`: Rewrite `_handle_import` (currently lines 1116-1250). Remove categorizer call, remove wiki-article writes, route PDF through `pdf_structure`, route non-PDF through existing chunker, write all output to `raw/sources/<doc-slug>/`.
- `tests/test_import.py`: Update existing tests to match new contract (raw/sources/ output instead of categorized wiki dirs, no categorizer mock needed).

### Files NOT touched

- `pal/converter.py`: Unchanged; still used for non-PDF conversion.
- `pal/chunker.py`: Unchanged; still used for non-PDF chunking.
- `pal/categorizer.py`: Unchanged on disk; just no longer called from `_handle_import`.
- `pal/wiki.py`: Unchanged on disk; just no longer called from `_handle_import`.
- `pal/frontmatter.py`: Unchanged; existing `serialize_frontmatter` handles the new keys.
- `pal/archive.py`: Unchanged; existing `archive_raw_files` still used for the source file.

---

## Task 1: Add pymupdf4llm dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add pymupdf4llm to dependencies**

In `pyproject.toml`, add to the `dependencies` array:

```toml
dependencies = [
    "httpx>=0.27.0",
    "prompt-toolkit>=3.0.0",
    "rich>=13.0.0",
    "pyyaml>=6.0",
    "trafilatura>=1.12.0",
    "markitdown[pdf,docx,pptx,xlsx]>=0.1.0",
    "pymupdf4llm>=0.0.17",
]
```

- [ ] **Step 2: Install into the project venv**

Run:
```bash
source .venv/bin/activate && pip install -e .
```

Expected: output ending with `Successfully installed pymupdf4llm-... pymupdf-...`

- [ ] **Step 3: Verify the import works**

Run:
```bash
source .venv/bin/activate && python -c "import pymupdf4llm, fitz; print(pymupdf4llm.__version__, fitz.__version__)"
```

Expected: two version strings printed, no error.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "deps: add pymupdf4llm for PDF structural extraction"
```

---

## Task 2: Create pal/pdf_structure.py skeleton with ChapterBoundary

**Files:**
- Create: `pal/pdf_structure.py`
- Create: `tests/test_pdf_structure.py`

- [ ] **Step 1: Write the failing smoke test**

Create `tests/test_pdf_structure.py`:

```python
"""Tests for pal.pdf_structure: PDF chapter detection and extraction."""
from pal.pdf_structure import ChapterBoundary


def test_chapter_boundary_has_title_and_start_page():
    b = ChapterBoundary(title="Introduction", start_page=2)
    assert b.title == "Introduction"
    assert b.start_page == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
source .venv/bin/activate && python -m pytest tests/test_pdf_structure.py -v
```

Expected: `ModuleNotFoundError: No module named 'pal.pdf_structure'`

- [ ] **Step 3: Create the module with the dataclass**

Create `pal/pdf_structure.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
source .venv/bin/activate && python -m pytest tests/test_pdf_structure.py -v
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add pal/pdf_structure.py tests/test_pdf_structure.py
git commit -m "feat: add pdf_structure module skeleton with ChapterBoundary"
```

---

## Task 3: Implement detect_from_toc (tier 1)

**Files:**
- Modify: `pal/pdf_structure.py`
- Modify: `tests/test_pdf_structure.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pdf_structure.py`:

```python
from unittest.mock import MagicMock

from pal.pdf_structure import detect_from_toc


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
source .venv/bin/activate && python -m pytest tests/test_pdf_structure.py -v
```

Expected: the four new tests fail with `ImportError: cannot import name 'detect_from_toc'`.

- [ ] **Step 3: Implement detect_from_toc**

Append to `pal/pdf_structure.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
source .venv/bin/activate && python -m pytest tests/test_pdf_structure.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/pdf_structure.py tests/test_pdf_structure.py
git commit -m "feat: detect_from_toc tier 1 for PDF chapter detection"
```

---

## Task 4: Implement detect_from_typography (tier 2)

**Files:**
- Modify: `pal/pdf_structure.py`
- Modify: `tests/test_pdf_structure.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pdf_structure.py`:

```python
from pal.pdf_structure import detect_from_typography


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
source .venv/bin/activate && python -m pytest tests/test_pdf_structure.py -v
```

Expected: new tests fail with `ImportError: cannot import name 'detect_from_typography'`.

- [ ] **Step 3: Implement detect_from_typography**

Append to `pal/pdf_structure.py`:

```python
from collections import Counter

# Tunable thresholds. Module-level so they're easy to find and adjust
# once we have real-corpus feedback.
TYPOGRAPHY_HEADING_MULTIPLIER = 1.4  # heading font size >= multiplier * body baseline
TYPOGRAPHY_MAX_HEADING_CHARS = 150   # headings are short
TYPOGRAPHY_MIN_CANDIDATES = 2        # fewer than this = not useful structure


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
    as the body baseline, and candidate headings are blocks at
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

    # Body baseline: the most common rounded font size.
    size_counts = Counter(round(s, 1) for _, _, s in rows)
    baseline = max(size_counts, key=size_counts.get)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
source .venv/bin/activate && python -m pytest tests/test_pdf_structure.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/pdf_structure.py tests/test_pdf_structure.py
git commit -m "feat: detect_from_typography tier 2 for PDF chapter detection"
```

---

## Task 5: Implement detect_from_llm_toc (tier 3)

**Files:**
- Modify: `pal/pdf_structure.py`
- Modify: `tests/test_pdf_structure.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pdf_structure.py`:

```python
import json

import pytest

from pal.pdf_structure import build_llm_sample, detect_from_llm_toc
from pal.inference import CompletionResult


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
source .venv/bin/activate && python -m pytest tests/test_pdf_structure.py -v
```

Expected: four new tests fail with `ImportError`.

- [ ] **Step 3: Implement build_llm_sample and detect_from_llm_toc**

Append to `pal/pdf_structure.py`:

```python
import json as _json
import logging

logger = logging.getLogger(__name__)


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
        page = entry.get("page")
        title = entry.get("title")
        if not isinstance(page, int) or not isinstance(title, str) or not title.strip():
            continue
        if page < 1 or page > len(doc):
            continue
        boundaries.append(ChapterBoundary(title=title.strip(), start_page=page - 1))

    if len(boundaries) < 2:
        return None
    return boundaries
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
source .venv/bin/activate && python -m pytest tests/test_pdf_structure.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/pdf_structure.py tests/test_pdf_structure.py
git commit -m "feat: detect_from_llm_toc tier 3 fallback for PDF chapter detection"
```

---

## Task 6: Implement detect_chapters orchestrator

**Files:**
- Modify: `pal/pdf_structure.py`
- Modify: `tests/test_pdf_structure.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pdf_structure.py`:

```python
from pal.pdf_structure import DetectionResult, detect_chapters


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
source .venv/bin/activate && python -m pytest tests/test_pdf_structure.py -v
```

Expected: new tests fail with `ImportError`.

- [ ] **Step 3: Implement DetectionResult and detect_chapters**

Append to `pal/pdf_structure.py`:

```python
from typing import Literal


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
source .venv/bin/activate && python -m pytest tests/test_pdf_structure.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/pdf_structure.py tests/test_pdf_structure.py
git commit -m "feat: detect_chapters orchestrator with three-tier fallback"
```

---

## Task 7: Implement extract_chapters (per-chapter markdown extraction)

**Files:**
- Modify: `pal/pdf_structure.py`
- Modify: `tests/test_pdf_structure.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pdf_structure.py`:

```python
from pal.pdf_structure import Chapter, compute_chapter_ranges, extract_chapters


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
source .venv/bin/activate && python -m pytest tests/test_pdf_structure.py -v
```

Expected: new tests fail with `ImportError`.

- [ ] **Step 3: Implement Chapter, compute_chapter_ranges, extract_chapters**

Append to `pal/pdf_structure.py`:

```python
import pymupdf4llm


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
    boundary list. Caller is responsible for writing these to disk.
    """
    ranges = compute_chapter_ranges(boundaries, total_pages)
    chapters: list[Chapter] = []
    for b, (start, end) in zip(boundaries, ranges):
        pages = list(range(start, end + 1))
        markdown = _pymupdf4llm_to_markdown(pdf_path, pages=pages)
        chapters.append(Chapter(
            title=b.title,
            start_page=start,
            end_page=end,
            markdown=markdown,
        ))
    return chapters
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
source .venv/bin/activate && python -m pytest tests/test_pdf_structure.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/pdf_structure.py tests/test_pdf_structure.py
git commit -m "feat: extract_chapters using pymupdf4llm per boundary range"
```

---

## Task 8: Add slugify helper for doc and section slugs

**Files:**
- Modify: `pal/pdf_structure.py`
- Modify: `tests/test_pdf_structure.py`

Rationale: the daemon handler needs slug generation; putting it in `pdf_structure` keeps import semantics consistent and testable.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pdf_structure.py`:

```python
from pal.pdf_structure import slugify


def test_slugify_lowercases_and_replaces_spaces():
    assert slugify("Agentic Design Patterns") == "agentic-design-patterns"


def test_slugify_strips_punctuation():
    assert slugify("Chapter 1: The Pattern!") == "chapter-1-the-pattern"


def test_slugify_collapses_multiple_separators():
    assert slugify("foo --- bar   baz") == "foo-bar-baz"


def test_slugify_trims_leading_trailing_hyphens():
    assert slugify("-foo-") == "foo"


def test_slugify_handles_empty_and_whitespace_only():
    assert slugify("") == "untitled"
    assert slugify("   ") == "untitled"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
source .venv/bin/activate && python -m pytest tests/test_pdf_structure.py -v
```

Expected: new tests fail with `ImportError`.

- [ ] **Step 3: Implement slugify**

Append to `pal/pdf_structure.py`:

```python
import re

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Lowercase, non-alphanumerics → hyphens, collapse runs, trim ends.

    Returns 'untitled' for empty or whitespace-only input so callers
    always get a valid path component.
    """
    lowered = text.lower()
    collapsed = _SLUG_STRIP_RE.sub("-", lowered).strip("-")
    return collapsed or "untitled"
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
source .venv/bin/activate && python -m pytest tests/test_pdf_structure.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/pdf_structure.py tests/test_pdf_structure.py
git commit -m "feat: slugify helper for pdf_structure"
```

---

## Task 9: Rewrite _handle_import for the new raw-first contract

**Files:**
- Modify: `pal/daemon.py` (replace the body of `_handle_import`, currently lines 1116-1250)

This is the largest single task. It removes the categorizer call, removes wiki-article writes, and routes all formats into `raw/sources/<doc-slug>/`. PDFs go through `pdf_structure`; non-PDFs stay on the existing `chunk_markdown` path.

- [ ] **Step 1: Read the current `_handle_import` so you know exactly what's being replaced**

Run:
```bash
source .venv/bin/activate && sed -n '1116,1250p' pal/daemon.py | head -150
```

Note the current structure: conversion → chunk → categorize → per-chunk wiki write → archive → reindex. You're replacing the categorize + wiki-write with raw/sources/ writes, and swapping the conversion path for PDFs.

- [ ] **Step 2: Add the import statements at the top of `pal/daemon.py`**

Near the existing `from pal.chunker import chunk_markdown` line, add imports for pymupdf and the new pdf_structure module. Open `pal/daemon.py`, find the existing imports section, and add:

```python
import fitz  # pymupdf
from pal.pdf_structure import (
    detect_chapters,
    extract_chapters,
    slugify,
)
```

- [ ] **Step 3: Replace `_handle_import` in `pal/daemon.py`**

Replace the entire `_handle_import` method (currently lines ~1116-1250 based on the snapshot at plan-writing time; re-check with the sed command above before editing) with:

```python
    async def _handle_import(self, file_path: str, writer: asyncio.StreamWriter) -> None:
        """Handle /import <path> - raw-first ingestion.

        Converts the source to markdown, splits into sections using
        format-appropriate detection, writes each section to
        raw/sources/<doc-slug>/NN-slug.md, archives the source, and
        triggers reindex. No categorization, no wiki-article writes.
        Promotion to wiki articles is a separate user/agent-driven step.
        """
        file_path = file_path.strip()
        if not file_path:
            error = ErrorMessage(error="Usage: /import <path-in-raw/>")
            writer.write(encode_message(error))
            await writer.drain()
            return

        if not file_path.startswith("raw/"):
            error = ErrorMessage(error=f"Files must be in raw/ directory: {file_path}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        if ".." in file_path.split("/") or file_path.startswith("/"):
            error = ErrorMessage(error=f"Invalid path: {file_path}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        full_path = self.config.vault_path / file_path
        if not full_path.exists():
            error = ErrorMessage(error=f"File not found: {file_path}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        try:
            resolved = full_path.resolve()
            vault_resolved = self.config.vault_path.resolve()
            if not str(resolved).startswith(str(vault_resolved) + "/"):
                error = ErrorMessage(error=f"Invalid path: {file_path}")
                writer.write(encode_message(error))
                await writer.drain()
                return
        except Exception:
            error = ErrorMessage(error=f"Invalid path: {file_path}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        ext = full_path.suffix.lower()
        is_pdf = ext == ".pdf"
        doc_slug = slugify(full_path.stem)

        target_dir = self.config.vault_path / "raw" / "sources" / doc_slug
        target_dir.mkdir(parents=True, exist_ok=True)

        from datetime import datetime, timezone
        from pal.frontmatter import serialize_frontmatter
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        saved_articles: list[str] = []
        detection_method: str

        if is_pdf:
            # PDF path: pymupdf4llm + structural detection.
            progress = ToolProgressMessage(
                tool="import",
                arguments={"status": f"Converting {full_path.name} (pymupdf4llm)..."},
            )
            writer.write(encode_message(progress))
            await writer.drain()

            try:
                loop = asyncio.get_running_loop()
                doc = await loop.run_in_executor(None, fitz.open, str(full_path))
            except Exception as exc:
                error = ErrorMessage(error=f"PDF open failed: {exc}")
                writer.write(encode_message(error))
                await writer.drain()
                return

            try:
                total_pages = len(doc)

                progress = ToolProgressMessage(
                    tool="import",
                    arguments={"status": "Detecting chapters..."},
                )
                writer.write(encode_message(progress))
                await writer.drain()

                detection = await detect_chapters(doc, inference=self.inference)
                detection_method = detection.method

                if detection.method == "single-file":
                    progress = ToolProgressMessage(
                        tool="import",
                        arguments={"status": "No chapters detected; writing single file..."},
                    )
                    writer.write(encode_message(progress))
                    await writer.drain()

                    full_markdown = await loop.run_in_executor(
                        None,
                        lambda: __import__("pymupdf4llm").to_markdown(str(full_path)),
                    )
                    article_path_rel = f"raw/sources/{doc_slug}/full.md"
                    article_full = target_dir / "full.md"
                    meta = {
                        "title": full_path.stem,
                        "source_file": file_path,
                        "source_type": "pdf",
                        "section_number": 1,
                        "detection_method": detection_method,
                        "imported": now,
                    }
                    article_full.write_text(
                        serialize_frontmatter(meta, full_markdown.strip() + "\n"),
                    )
                    saved_articles.append(article_path_rel)
                else:
                    chapters = await loop.run_in_executor(
                        None,
                        extract_chapters,
                        str(full_path),
                        detection.boundaries,
                        total_pages,
                    )
                    for i, ch in enumerate(chapters, start=1):
                        progress = ToolProgressMessage(
                            tool="import",
                            arguments={
                                "status": f"Writing chapter {i} of {len(chapters)}: {ch.title}",
                            },
                        )
                        writer.write(encode_message(progress))
                        await writer.drain()

                        section_slug = slugify(ch.title)
                        filename = f"{i:02d}-{section_slug}.md"
                        article_path_rel = f"raw/sources/{doc_slug}/{filename}"
                        article_full = target_dir / filename
                        meta = {
                            "title": ch.title,
                            "source_file": file_path,
                            "source_type": "pdf",
                            "section_number": i,
                            "section_range": f"p.{ch.start_page + 1}-p.{ch.end_page + 1}",
                            "detection_method": detection_method,
                            "imported": now,
                        }
                        article_full.write_text(
                            serialize_frontmatter(meta, ch.markdown.strip() + "\n"),
                        )
                        saved_articles.append(article_path_rel)
            finally:
                doc.close()
        else:
            # Non-PDF path: existing MarkItDown + chunk_markdown flow, re-homed to raw/sources/.
            progress = ToolProgressMessage(
                tool="import",
                arguments={"status": f"Converting {full_path.name}..."},
            )
            writer.write(encode_message(progress))
            await writer.drain()

            try:
                loop = asyncio.get_running_loop()
                convert_result = await loop.run_in_executor(
                    None, self.converter.convert, full_path,
                )
            except ConversionError as exc:
                error = ErrorMessage(error=f"Conversion failed: {exc}")
                writer.write(encode_message(error))
                await writer.drain()
                return

            chunks = chunk_markdown(convert_result.text, fallback_title=convert_result.title)
            if not chunks:
                error = ErrorMessage(error="Conversion produced no content.")
                writer.write(encode_message(error))
                await writer.drain()
                return

            detection_method = "headings"
            source_type = ext.lstrip(".")

            for i, chunk in enumerate(chunks, start=1):
                section_slug = slugify(chunk.title)
                filename = f"{i:02d}-{section_slug}.md"
                article_path_rel = f"raw/sources/{doc_slug}/{filename}"
                article_full = target_dir / filename
                meta = {
                    "title": chunk.title,
                    "source_file": file_path,
                    "source_type": source_type,
                    "section_number": i,
                    "detection_method": detection_method,
                    "imported": now,
                }
                article_full.write_text(
                    serialize_frontmatter(meta, chunk.body.strip() + "\n"),
                )
                saved_articles.append(article_path_rel)

        # Commit and reindex.
        self.wiki.git_init()
        self.wiki.git_commit(f"import: {full_path.stem} ({len(saved_articles)} sections)")

        absolute_paths = [
            str((self.config.vault_path / rel).resolve())
            for rel in saved_articles
        ]
        await self._trigger_reindex_for_paths(absolute_paths)

        # Archive source.
        progress = ToolProgressMessage(
            tool="import",
            arguments={"status": "Archiving source..."},
        )
        writer.write(encode_message(progress))
        await writer.drain()
        archive_raw_files(self.config.vault_path, raw_path=file_path)
        self.wiki.git_commit(f"archive: {full_path.stem}")

        # Build detection report.
        lines = [
            f"Imported {len(saved_articles)} section(s) from {full_path.name} "
            f"(detection: {detection_method}):"
        ]
        for rel in saved_articles:
            lines.append(f"- {rel}")

        resp = ResponseMessage(text="\n".join(lines), command="import")
        writer.write(encode_message(resp))
        await writer.drain()
```

- [ ] **Step 4: Quick syntax check**

Run:
```bash
source .venv/bin/activate && python -c "import pal.daemon"
```

Expected: no error.

- [ ] **Step 5: Commit (tests in the next task)**

```bash
git add pal/daemon.py
git commit -m "feat: rewrite _handle_import for raw-first ingestion"
```

---

## Task 10: Update existing import integration tests

**Files:**
- Modify: `tests/test_import.py`

The existing tests expect categorized wiki output (e.g. `Research/`). The new contract writes to `raw/sources/`. Every assertion that checks for the old location needs to be updated. The categorizer is no longer called, so `fake_complete` for categorization can be removed from tests that do not need it.

- [ ] **Step 1: Read the current test file so you know what's there**

Run:
```bash
source .venv/bin/activate && sed -n '1,200p' tests/test_import.py
```

- [ ] **Step 2: Update the CSV import test**

Find `test_import_csv_creates_article` in `tests/test_import.py`. Replace its body with:

```python
@pytest.mark.asyncio
async def test_import_csv_creates_article(import_daemon, socket_path, monkeypatch):
    daemon, vault = import_daemon

    rel_path = _place_csv_in_raw(
        vault, "employees.csv",
        "Name,Role,Department\nAlice,Engineer,Platform\nBob,Designer,Product\n"
    )

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("import", rel_path)
    assert "raw/sources/employees/" in resp.text
    await client.close()

    articles = list((vault / "raw" / "sources" / "employees").glob("*.md"))
    assert len(articles) >= 1
    content = articles[0].read_text()
    assert "Alice" in content
    # Frontmatter should reflect raw-first shape.
    assert "source_file: raw/employees.csv" in content
    assert "source_type: csv" in content
    assert "detection_method: headings" in content
```

- [ ] **Step 3: Update the archive test**

Find `test_import_archives_source` and update assertions to match the new output location:

```python
@pytest.mark.asyncio
async def test_import_archives_source(import_daemon, socket_path, monkeypatch):
    daemon, vault = import_daemon

    rel_path = _place_csv_in_raw(
        vault, "data.csv",
        "a,b\n1,2\n"
    )

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("import", rel_path)
    await client.close()

    # Source has been moved to raw/archived/
    assert not (vault / rel_path).exists()
    assert (vault / "raw" / "archived" / "data.csv").exists()
    # Raw-sources output exists.
    assert (vault / "raw" / "sources" / "data").exists()
```

- [ ] **Step 4: Remove or update any test that depended on categorizer behavior**

Scan the rest of `tests/test_import.py` for:
- `monkeypatch.setattr(daemon.inference, "complete", fake_complete)` patterns used only to mock categorization.
- Assertions on specific wiki category directories like `Research/`, `Technology/`.

Remove the categorizer mocks where they were the only reason for the patch. For any test that asserts on a specific old location, replace the assertion with `raw/sources/<doc-slug>/`.

If a test becomes redundant after the update (e.g., it was purely testing categorization-dependent behavior that no longer exists), mark it with `@pytest.mark.skip(reason="covered by new raw-first test")` and leave a comment rather than deleting it, to make the diff reviewable.

- [ ] **Step 5: Run the import test module**

Run:
```bash
source .venv/bin/activate && python -m pytest tests/test_import.py -v
```

Expected: all non-skipped tests pass. If any fail, the error message should point at an assertion you missed updating.

- [ ] **Step 6: Run the full test suite**

Run:
```bash
source .venv/bin/activate && python -m pytest
```

Expected: same pass count as before the work started (712 passed at plan-writing time, plus the new pdf_structure tests), no new failures.

- [ ] **Step 7: Commit**

```bash
git add tests/test_import.py
git commit -m "test: update import tests for raw-first contract"
```

---

## Task 11: Add an integration test for the PDF path using a synthetic PDF

**Files:**
- Modify: `tests/test_import.py`

Real-world PDF regressions will be caught by the manual check in Task 12. This task adds a small programmatic test so the PDF path stays covered in CI.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_import.py`:

```python
@pytest.mark.asyncio
async def test_import_pdf_with_toc_produces_chapters(import_daemon, socket_path, tmp_path):
    import fitz

    daemon, vault = import_daemon

    # Build a synthetic PDF with a TOC and three trivial pages.
    pdf_path_on_disk = vault / "raw" / "test-book.pdf"
    pdf_path_on_disk.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for i, title in enumerate(["Introduction page", "Pattern page", "Conclusion page"]):
        page = doc.new_page()
        page.insert_text((72, 72), title, fontsize=18)
        page.insert_text((72, 120), f"Body text for page {i + 1}.", fontsize=11)
    doc.set_toc([
        [1, "Introduction", 1],
        [1, "The Pattern", 2],
        [1, "Conclusion", 3],
    ])
    doc.save(str(pdf_path_on_disk))
    doc.close()

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("import", "raw/test-book.pdf")
    await client.close()

    assert "detection: toc" in resp.text
    out_dir = vault / "raw" / "sources" / "test-book"
    assert out_dir.exists()
    files = sorted(f.name for f in out_dir.glob("*.md"))
    assert files == ["01-introduction.md", "02-the-pattern.md", "03-conclusion.md"]

    # Archived source.
    assert not pdf_path_on_disk.exists()
    assert (vault / "raw" / "archived" / "test-book.pdf").exists()

    # Frontmatter sanity on one chapter.
    first = (out_dir / "01-introduction.md").read_text()
    assert "source_type: pdf" in first
    assert "detection_method: toc" in first
    assert "section_range: p.1-p.1" in first
```

- [ ] **Step 2: Run the test**

Run:
```bash
source .venv/bin/activate && python -m pytest tests/test_import.py::test_import_pdf_with_toc_produces_chapters -v
```

Expected: pass. If it fails because the synthetic PDF's typography triggers tier 2 before tier 1, investigate and fix in pdf_structure (the TOC tier should always win when present).

- [ ] **Step 3: Run the full suite**

Run:
```bash
source .venv/bin/activate && python -m pytest
```

Expected: no new failures relative to Task 10.

- [ ] **Step 4: Commit**

```bash
git add tests/test_import.py
git commit -m "test: integration test for PDF import with TOC"
```

---

## Task 12: Add fixtures README and run manual verification on a real PDF

**Files:**
- Create: `tests/fixtures/pdfs/README.md`

- [ ] **Step 1: Create the fixtures README**

```bash
mkdir -p tests/fixtures/pdfs
```

Create `tests/fixtures/pdfs/README.md`:

```markdown
# PDF test fixtures

Real-PDF fixtures for the import pipeline's regression tests. The PDFs
themselves are not committed to git (they're usually large and often
copyrighted). Each fixture is referenced by absolute or user-relative
path from a test that asserts expected output shape.

## Adding a fixture

1. Place the PDF somewhere reachable (e.g. `~/Documents/`).
2. Run the import manually once and eyeball the output.
3. Write a test that skips when the PDF is absent:

```python
@pytest.mark.skipif(
    not Path.home().joinpath("Documents/Agentic_Design_Patterns.pdf").exists(),
    reason="fixture PDF not present on this machine",
)
def test_import_agentic_design_patterns(...):
    ...
```

4. Record the expected chapter count and a few expected chapter titles
   in the test's assertions.

## Current fixtures

None committed. The original regression case
(`Agentic_Design_Patterns.pdf`, the 265-fragment disaster) should be
added as a local fixture once the pipeline is working.
```

- [ ] **Step 2: Commit the fixtures README**

```bash
git add tests/fixtures/pdfs/README.md
git commit -m "docs: tests/fixtures/pdfs/ README for real-PDF regressions"
```

- [ ] **Step 3: Manual verification against the real offender**

This is a manual step, not automated. Run:

```bash
source .venv/bin/activate
# Copy the PDF into the vault's raw/ dir (adjust VAULT_PATH to your env):
# cp ~/Documents/Agentic_Design_Patterns.pdf "$VAULT_PATH/raw/"
# Then from PAL CLI:
# /import raw/Agentic_Design_Patterns.pdf
```

Expected behavior after the import finishes:

- The CLI prints `Imported N section(s) from Agentic_Design_Patterns.pdf (detection: toc)` where N is roughly the book's chapter count (expected somewhere between 10 and 25, not 265).
- `raw/sources/agentic-design-patterns/` contains N files named like `01-introduction.md`, `02-the-first-pattern.md`, ...
- Each chapter file has frontmatter with `source_type: pdf`, `section_range: p.N-p.M`, and `detection_method: toc`.
- The source PDF has moved to `raw/archived/Agentic_Design_Patterns.pdf`.
- `AI/` no longer receives any new junk files from this import.

If detection falls through to typography (or worse, single-file), that is useful information. Note it and decide whether the PDF's TOC was really missing or whether the tier-1 reader has a bug. No fix required in this pass; just capture what happened.

- [ ] **Step 4: No code commit for this step; finalize by pushing**

Push everything to origin:

```bash
git push
```

---

## Self-review

### Spec coverage

Each spec section is covered by at least one task:

- **Contract rewrite** → Task 9
- **PDF pipeline backend swap** → Task 1 (dep) + Task 9 (use site)
- **Tier 1 TOC** → Task 3
- **Tier 2 typography** → Task 4
- **Tier 3 LLM-TOC** → Task 5
- **Tier 4 single-file fallback** → Task 6 (orchestrator) + Task 9 (handler wiring)
- **Per-chapter extraction** → Task 7
- **Slug generation** → Task 8 + Task 9 (use site)
- **Non-PDF path** → Task 9 (non-PDF branch)
- **Frontmatter shape** → Task 9 (constructed in handler)
- **Directory layout** → Task 9 (`raw/sources/<doc-slug>/`)
- **Error handling** → Task 9 (each branch returns ErrorMessage on failure, falls through on partial)
- **Progress UX** → Task 9 (ToolProgressMessage per phase)
- **Test coverage: unit per tier** → Tasks 3, 4, 5, 6, 7, 8
- **Test coverage: integration non-PDF** → Task 10
- **Test coverage: integration PDF** → Task 11
- **Test coverage: real-PDF fixtures dir** → Task 12

### Placeholder scan

No TBDs, TODOs, or "implement later" markers in the plan. All steps that change code contain the code. All assertion targets are concrete.

### Type consistency

- `ChapterBoundary` is the dataclass used across Tasks 3-8.
- `Chapter` (with markdown attribute) is introduced in Task 7 and consumed in Task 9.
- `DetectionResult` (Task 6) is consumed by the handler in Task 9 via `detection.method` and `detection.boundaries`.
- `slugify` signature is `(str) -> str` in Task 8 and called with the same signature in Task 9.
- `extract_chapters(pdf_path: str, boundaries: list[ChapterBoundary], total_pages: int) -> list[Chapter]` matches between Task 7 implementation and Task 9 use site.

### Scope check

All tasks belong to one feature (rewriting `/import` to be raw-first). No cross-cutting or separable sub-projects inside this plan. Phase B (CPU model routing) is explicitly out of scope and lives in a future spec.
