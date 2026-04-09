# Chunked Import, Token Budget, and Slug Fix

**Date:** 2026-04-09
**Status:** Draft

## Overview

Three changes to PAL's import pipeline:

1. **Chunked import** - Split large documents at heading boundaries, producing one article per section.
2. **Raise token budget** - Increase sanitizer default from 8,000 to 32,000 tokens.
3. **Fix slug generation** - Convert underscores to hyphens in all slug generation paths.

## Goals

- Large documents (books, long PDFs) produce multiple focused articles instead of one truncated summary
- Individual chapters/sections get full summarization coverage
- Slugs are readable and hyphen-separated

## Non-Goals

- Adaptive/intelligent chunk merging or splitting (simple heading-based split is sufficient)
- Per-path token budget configuration (single default for all paths)
- Changing the sanitization pipeline itself (just the budget)

## Chunked Import

### New Module: `pal/chunker.py`

Splits a markdown string at top-level headings.

**Logic:**
1. Scan the markdown for headings (lines starting with `#`)
2. Detect the highest heading level present (H1 if any exist, otherwise H2)
3. Split at each occurrence of that heading level
4. Return a list of `Chunk(title, body)` where `title` is the heading text and `body` is everything until the next heading at that level
5. Any content before the first heading becomes a chunk titled after the document (or "Introduction")

**Edge cases:**
- No headings found: return the entire document as a single chunk (preserves current behavior for CSVs, plain text, etc.)
- Single heading: return one chunk (same as current behavior)
- Empty body after heading: skip that chunk

### Changes to `_handle_import`

After MarkItDown conversion and before sanitization:

1. Call `chunk_markdown(text, fallback_title)` to split the converted text
2. If only one chunk, process as before (single article)
3. If multiple chunks, loop over each chunk and for each one:
   - Sanitize + boundary wrap
   - LLM summarize
   - LLM compile
   - LLM categorize
   - Save article
4. Send progress messages indicating which chunk is being processed (e.g., "Processing section 3/12: Core Reasoning Engine...")
5. Archive source file once after all chunks are processed
6. Git commit once at the end (not per-chunk)
7. Response lists all created articles

## Token Budget

In `pal/sanitizer.py`, change the `max_tokens` default parameter from 8,000 to 32,000:

```python
def sanitize(text, guid, min_chars=10, max_tokens=32000):
```

This applies to all callers (`/summarize`, `/import`) without any code changes at call sites.

## Slug Fix

In `_handle_import`, `_handle_compile`, and `_handle_note`, the slug generation currently does:

```python
slug = title.lower().replace(" ", "-")
slug = "".join(c for c in slug if c.isalnum() or c == "-").strip("-") or "untitled"
```

Change to:

```python
slug = title.lower().replace("_", "-").replace(" ", "-")
slug = "".join(c for c in slug if c.isalnum() or c == "-").strip("-") or "untitled"
```

This converts underscores to hyphens before the character filter, producing `agentic-design-patterns` instead of `agenticdesignpatterns`.

## Error Handling

- If any individual chunk fails during summarization or compilation, log the error, skip that chunk, and continue with the rest. Report skipped chunks in the final response.
- If all chunks fail, report the error (no articles saved, source not archived).
- If categorization fails for a chunk, fall back to `Research/` as usual.

## Testing

- `pal/chunker.py`: unit tests for heading detection, splitting at H1 vs H2, no-heading fallback, empty body skipping, content-before-first-heading handling
- Token budget: update existing sanitizer test that checks truncation threshold
- Slug fix: unit test that underscores become hyphens
- `/import` with chunks: integration test using a markdown file with multiple H1 headings, verify multiple articles created
