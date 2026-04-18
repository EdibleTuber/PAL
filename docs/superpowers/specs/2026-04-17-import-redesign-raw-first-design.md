---
title: Import Redesign - Raw-First Ingestion with PDF Structural Detection
date: 2026-04-17
status: draft
---

# Import Redesign: Raw-First Ingestion with PDF Structural Detection

## Context

The existing `/import` command is a magic-one-shot that conflates three separable steps: ingest, summarize, compile. It converts a local document via MarkItDown, chunks on markdown headings, invokes the LLM-powered categorizer, and writes categorized wiki articles in one flow.

For PDFs specifically this produces disaster output. On 2026-04-17, importing a 22 MB book (`Agentic_Design_Patterns.pdf`) produced 265 "articles" in `AI/`, most with filenames that were single Python comment lines like `for-better-security-load-environment-variables-from-a.md`. Root cause: MarkItDown's PDF extractor loses code-block fence delimiters, every `#` line in a code sample becomes a false H1, the chunker splits on those, and the categorizer applies category labels to one-line fragments.

The deeper issue is that the pipeline treats all inputs as distillation-ingest material (source is ephemeral, distilled article is the asset). For books, technical references, and long-form source documents, the inverse is true: the source itself is the asset. PAL's existing web flow already models ingestion as three separate steps (`/fetch` to raw, `/summarize` to summary, `/compile` to wiki article). `/import` should match that shape: land data in raw, let downstream tools decide whether and how to promote.

This spec redesigns `/import` as raw-first ingestion with a new PDF pipeline that uses structural cues from the PDF itself rather than heading detection on converted markdown.

## Non-goals

- Automatic summarization, compilation, or categorization during import. Those remain separate, user-driven or agent-driven steps.
- LLM editorial passes on chapter content. Output is whatever the extractor produces, unmodified.
- Backward compatibility with the current `/import` output shape. Existing imported articles stay where they are; the user can clean them up manually or start fresh.
- Vision / OCR for tables. Tables come through as pymupdf4llm produces them.
- CPU-routed inference for the import pipeline. This is explicitly Phase B, a separate spec.
- Adding a new `ask_file` tool. That was the original framing of this work; it became unnecessary once the pipeline was reshaped as raw-first with deterministic chunking.

## Architecture

### The new `/import` contract

For any supported file type:

1. Convert to markdown (pymupdf4llm for PDF, MarkItDown for DOCX, EPUB, HTML, XLSX, PPTX, CSV).
2. Split into sections using format-appropriate detection.
3. Write each section to `raw/sources/<doc-slug>/NN-section-slug.md` with minimal frontmatter.
4. Archive the source file (existing `archive_raw_files` logic, unchanged).
5. Trigger reindex (existing path, unchanged).
6. Return a detection report listing sections written, their page ranges for PDFs, and the detection method used.

What gets deleted from the current flow:

- The `self.categorizer.categorize(...)` call in `_handle_import`.
- The `target_dir = self.config.vault_path / category` and subsequent `mkdir(parents=True, exist_ok=True)`.
- The `self.wiki.write_article(...)` path with `title`, `tags`, and per-chunk `article_meta` construction for imports.
- The `self.wiki.rebuild_index()` + `self.wiki.git_init()` + `self.wiki.git_commit(f"import: ...")` for wiki writes. Git commits still happen in the archive path and are unaffected.

What stays unchanged:

- Path validation, `raw/` scope check, path traversal guard.
- Extension whitelist (defined in `pal/converter.py::SUPPORTED_EXTENSIONS`).
- `archive_raw_files` of the source file post-import.
- Reindex trigger via `self._trigger_reindex_for_paths(absolute_paths)`.
- Per-phase `ToolProgressMessage` emission.

### PDF pipeline (new)

#### Dependency

Add `pymupdf4llm` to `pyproject.toml`. It depends on `pymupdf` (aka `fitz`), which becomes an indirect dependency. MarkItDown stays in the deps because non-PDF imports still use it.

#### Module layout

New module `pal/pdf_structure.py` holds chapter-detection logic. Three tiers, each a separate function with a single responsibility. Detection tiers run in order; first tier to produce at least two candidate boundaries wins.

#### Tier 1: TOC extraction

```python
def detect_from_toc(doc: fitz.Document) -> list[ChapterBoundary] | None:
    toc = doc.get_toc()
    if not toc:
        return None
    # Keep only level-1 entries as chapter boundaries; deeper levels stay inside.
    level_one = [(title, page - 1) for level, title, page in toc if level == 1]
    if len(level_one) < 2:
        return None
    return [ChapterBoundary(title=t, start_page=p) for t, p in level_one]
```

Most published books and well-generated technical documents include a TOC. Reliable, cheap, runs in milliseconds.

#### Tier 2: Typography heuristic

```python
def detect_from_typography(doc: fitz.Document) -> list[ChapterBoundary] | None:
    # 1. Walk all text blocks across all pages, collecting font sizes.
    # 2. Modal font size is the body baseline.
    # 3. Candidate heading: block with size >= 1.4 * baseline, text length <= 150 chars,
    #    and either starts a new page or has significant whitespace before it.
    # 4. Collapse adjacent candidates (title + subtitle).
    # 5. Return boundaries. If fewer than 2 candidates, return None.
```

Self-calibrates per document: the baseline is derived from the PDF's own font distribution, not a hard-coded number. This handles books at 9pt body, 11pt body, or anything else equally well without per-document configuration.

The 1.4x multiplier, 150-char length ceiling, and "starts a new page or has whitespace before it" test are starting heuristics. They will need tuning once the pipeline runs against real documents. Each parameter lives as a module-level constant in `pal/pdf_structure.py` for easy adjustment.

#### Tier 3: LLM-TOC reconstruction

When tiers 1 and 2 both return None:

```python
async def detect_from_llm_toc(
    doc: fitz.Document,
    inference: InferenceClient,
) -> list[ChapterBoundary] | None:
    # 1. Build a compact sample: for each page, (page_num, first 120 chars, first-block font-size).
    # 2. Send to the LLM with a prompt asking for candidate chapter start page numbers.
    # 3. Parse a JSON list of page numbers out of the response.
    # 4. Fabricate titles from the first line at each start page.
    # 5. If response is empty or unparseable, return None.
```

The sample for a 300-page book is roughly 36 KB of text, well inside ctx. This tier costs one LLM call per import and only runs when the structural tiers both fail. Not intended as the common path.

#### Tier 4: Single-file fallback

If all three tiers return None, write the entire markdown extract as `raw/sources/<doc-slug>/full.md` with frontmatter `detection_method: single-file`. Not an error. The document is in raw and downstream tools can still work with it.

#### Per-chapter extraction

Once boundaries are known as `(start_page, end_page)` ranges (where `end_page` is derived from the next boundary's start, or the last page for the final chapter):

```python
for boundary, (start, end) in zip(boundaries, ranges):
    chapter_md = pymupdf4llm.to_markdown(path, pages=list(range(start, end + 1)))
    # Write to raw/sources/<doc-slug>/NN-section-slug.md
```

`pymupdf4llm.to_markdown` accepts a `pages` parameter and emits markdown with preserved code fences and native markdown tables.

#### Slug generation

- Doc slug: filename stem, lowercased, non-alphanumeric characters replaced with hyphens, leading/trailing hyphens stripped.
- Section slug: title from TOC, typography heading text, or LLM-generated title, same slugification.
- Section prefix: zero-padded two-digit ordinal. `01-introduction.md`, `02-the-pattern-taxonomy.md`, etc. Lexicographic order matches document order.

### Non-PDF path

DOCX, EPUB, HTML, HTM, XLSX, PPTX, CSV continue through MarkItDown and the existing `chunk_markdown` function. These formats carry real structural headings (DOCX paragraph styles, EPUB chapter splits, HTML `<h1>`/`<h2>`) that MarkItDown preserves faithfully in its markdown output. The 265-fragment failure mode was PDF-specific.

Only two things change for non-PDF inputs:

1. Output goes to `raw/sources/<doc-slug>/` instead of categorized wiki dirs.
2. No categorizer call, no wiki write.

`detection_method` in the frontmatter for these is `headings`.

## Data shapes

### ChapterBoundary

```python
@dataclass
class ChapterBoundary:
    title: str
    start_page: int  # 0-indexed
```

Returned from each detection tier. Used to compute page ranges and drive extraction.

### Chapter frontmatter (raw source files)

```yaml
title: <section-title>
source_file: <original-filename>
source_type: pdf | docx | epub | html | htm | xlsx | pptx | csv
section_number: 1
section_range: "p.3-p.16"          # PDFs only; omitted for other formats
detection_method: toc | typography | llm-toc | single-file | headings
imported: 2026-04-17T14:23:00Z
```

Deliberately minimal. No `status`, no `tags`, no `category`. Raw-source files are data, not information.

### Directory layout

```
raw/
  sources/
    agentic-design-patterns/
      01-introduction.md
      02-the-pattern-taxonomy.md
      03-the-routing-pattern.md
      ...
    some-other-book/
      01-preface.md
      02-chapter-one.md
      ...
```

## Error handling

- **Conversion failure.** `ConversionError` returned to user as an `ErrorMessage`. Nothing written. Same as current behavior.
- **Zero chapter candidates from all three tiers.** Single-file fallback. Not an error; the document is in raw.
- **Exactly one chapter candidate.** Same treatment. One chapter is not useful structure. Single-file fallback with a note in the detection report.
- **Individual chapter write fails mid-loop.** Log, skip that chapter, continue. Return a summary distinguishing succeeded and failed chapters. User can re-import if needed.
- **Archive of source file fails.** Log, do not fail the import. Raw files are written, source file remaining is cosmetic.
- **LLM-TOC tier produces malformed JSON.** Treat as None and fall through to single-file.

## Progress UX

Existing `ToolProgressMessage` infrastructure, one per phase:

- `Converting <filename>...`
- `Detecting chapters (pymupdf TOC)...` or `(typography)` or `(LLM-TOC)` or `(fallback: single file)`
- `Writing chapter 3 of 14: 03-the-routing-pattern.md`
- `Archiving source...`
- `Triggering reindex...`

Final response includes a detection report the user sees directly:

```
Imported 14 sections from Agentic_Design_Patterns.pdf (detection: pymupdf TOC):
- raw/sources/agentic-design-patterns/01-introduction.md (p.3-p.16)
- raw/sources/agentic-design-patterns/02-the-pattern-taxonomy.md (p.17-p.34)
- raw/sources/agentic-design-patterns/03-the-routing-pattern.md (p.35-p.52)
- ...
```

This lets the user sanity-check the chapter list immediately without digging into file listings.

## Test coverage

- **Unit tests in `tests/test_pdf_structure.py`** for each detection tier with synthetic inputs (constructed `fitz` TOC data, constructed page block dicts). Exercises: tier 1 happy path, tier 1 with fewer than 2 level-1 entries falls through, tier 2 self-calibrates on different baseline sizes, tier 2 returns None on flat-typography documents, tier 3 builds the expected compact sample shape.
- **Integration tests in `tests/test_import.py`** for the full pipeline:
  - One fixture per non-PDF format (DOCX, EPUB, HTML) confirming the existing chunker path still works under the new raw/sources/ output.
  - One synthetic-PDF fixture (built programmatically via pymupdf with a controlled TOC) exercising tier 1 end-to-end.
  - One real-PDF fixture directory: `tests/fixtures/pdfs/`, starting with `Agentic_Design_Patterns.pdf` if the user can commit it (or pointing at the local path). Assertion is approximate chapter count and that chapter titles match expected patterns. Each time detection misses on a real document, that document becomes a fixture.
- **No tests for tier 3 LLM quality.** Unit test confirms the sample shape is built correctly and the call is made. Actual LLM output is not unit-testable.

## Implementation sequencing

1. Add `pymupdf4llm` dependency.
2. Build `pal/pdf_structure.py` with the three tier functions and `ChapterBoundary` dataclass. Unit tests first.
3. Rewrite `pal/daemon.py::_handle_import` to use the new contract: route PDF through pdf_structure, keep non-PDF on the existing chunker, write everything to `raw/sources/`, drop the categorizer and wiki-write calls.
4. Update `pal/frontmatter` serialization to include the new frontmatter fields (likely no code change, just convention).
5. Update integration tests in `tests/test_import.py`.
6. Manual verification: re-import `Agentic_Design_Patterns.pdf` and eyeball the output.
7. Commit each step separately.

## Follow-ups (out of scope for this spec)

- **Phase B: CPU-resident small model for background ingestion.** Add a `batch` model profile to the inference manager, run a ~3B model on CPU, route heavy jobs there so chat stays snappy on the GPU. Enables future LLM-powered editorial passes on chapter content without blocking interactive use.
- **`ask_file` tool.** General-purpose one-shot Q&A over a file path, using delegated sub-inference. Originally scoped as part of this work; became unnecessary for Phase A when the pipeline was reshaped as raw-first with deterministic chunking. Still valuable as a standalone tool.
- **Table extraction quality audit.** Once pymupdf4llm is running on real corpus, assess whether tables come through usably. If not, evaluate docling (ML-based, good tables) or Gemma 4 vision integration (requires manager mmproj wiring, see `gemma4-deployment-notes.md`).
- **Raw/ indexing policy refinement.** Known existing issue: raw/ files leak into search results. Book chapters in raw/sources/ are actually useful hits, but transient web scraps in raw/web/ are noise. Decide the subtree policy separately.

## Success criteria

- Importing the Agentic Design Patterns PDF produces between roughly 10 and 30 chapter files in `raw/sources/agentic-design-patterns/` (the book's actual chapter count), not 265 fragments.
- Chapter filenames and titles correspond to real chapter titles (derived from the PDF's TOC).
- Code blocks and tables inside chapters are preserved in usable form (markdown fences present, table structure recognizable even if imperfect).
- Importing a DOCX or EPUB produces similar output shape (raw/sources/<doc-slug>/ with chapter files) without regressions from current behavior on those formats.
- The whole flow runs without needing LLM editorial passes, keeping Phase A's promise: deterministic, fast, no Gemma 4 time consumed beyond the optional tier 3 fallback.
