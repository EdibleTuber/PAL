# Document Import & Auto-Categorization

**Date:** 2026-04-08
**Status:** Draft

## Overview

Three changes to PAL's ingestion system:

1. **`/import` command** - converts a local file (PDF, DOCX, XLSX, PPTX, HTML, EPUB, CSV) to markdown via MarkItDown, then runs the full sanitize/summarize/compile pipeline in one step.
2. **Auto-categorization** - after compilation from any intake path (`/import`, `/compile`, `/note`), the LLM picks the best vault directory based on existing structure and content.
3. **Raw file lifecycle** - compiled intermediates move to `raw/archived/` and are deleted after 30 days.

Additionally, a minor fix to the URL fetcher to reduce 401 errors on web fetches.

## Goals

- Drop a PDF/DOCX/etc. into `raw/` and run one command to get a wiki article
- Articles land in the right directory automatically, no manual sorting
- Raw intermediates don't accumulate forever
- Fewer 401s on web fetches

## Non-Goals

- File watching / auto-detection of new files in `raw/` (explicit command is fine)
- Image OCR or audio transcription (wishlist, needs LLM vision/audio integration)
- JSON, XML, ZIP support (edge cases, add later if needed)
- Changing the existing `/fetch` -> `/summarize` -> `/compile` manual pipeline (it still works, `/import` is additive)

## Architecture

### `/import` Pipeline

```
User drops file into raw/
  -> /import raw/report.pdf
    -> DocumentConverter (MarkItDown) converts to markdown
    -> Sanitizer + boundary wrapper (existing)
    -> LLM summarizes (existing prompt)
    -> LLM compiles wiki article (existing prompt)
    -> LLM picks target directory (new)
    -> Save article, archive intermediates, git commit
```

The `/import` handler chains the existing sanitize, summarize, and compile logic internally. The same security layers (sanitization, GUID boundary wrapping, grounding rules) apply to imported documents as they do to web fetches.

### Auto-Categorization

A short follow-up LLM call after article compilation:

- **Input:** article title, first ~200 words of the article, list of existing non-system vault directories
- **Output:** a single directory path (e.g., `Research`, `Projects/infrastructure`)
- **Behavior:** picks the best existing directory, or suggests a new one if nothing fits. The directory is created if it doesn't exist.

Applied to all intake paths: `/import`, `/compile`, and `/note`. Replaces the current hardcoded `Research/` (compile) and root directory (note) destinations.

### Raw File Lifecycle

1. **Archive on compile:** After successful compilation (from `/compile` or `/import`), the source raw file and its summary file are moved to `raw/archived/`, preserving original filenames.
2. **Cleanup on startup:** When the daemon starts, it scans `raw/archived/` and deletes any file with an `mtime` older than 30 days. Deletions are logged at INFO level.

### Fetcher Fix

Add a User-Agent header to `URLFetcher` to reduce 401/403 rejections from sites that block bare requests:

```python
headers = {"User-Agent": "PAL/1.0 (+personal knowledge base)"}
```

## New Module: `pal/converter.py`

Wraps MarkItDown with supported-format validation.

```python
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx", ".html", ".htm", ".epub", ".csv"}
```

**Class:** `DocumentConverter`
- `convert(path: Path) -> ConvertResult` - validates the file extension, calls MarkItDown, returns the title and markdown text.
- `ConvertResult` dataclass: `title: str`, `text: str`, `source_path: str`
- Raises `ConversionError` for unsupported formats or MarkItDown failures.

MarkItDown is synchronous, so the converter is called in a thread executor from the async daemon handler.

## Command Interface

### `/import <path>`

- **Input:** relative path to a file in the vault (e.g., `raw/report.pdf`)
- **Path safety:** same traversal guards as `/summarize` and `/compile`
- **Allowed locations:** file must be under `raw/` (prevents accidentally importing vault articles)
- **Progress:** sends `ToolProgressMessage` at each stage (converting, sanitizing, summarizing, compiling, categorizing)
- **Output:** `ResponseMessage` with the final article path and content
- **On failure:** stops at the failing stage, reports the error, no partial files left behind

### Changes to `/compile`

- After successful article generation, run auto-categorization instead of hardcoding `Research/`
- After saving the article, archive the source summary and its linked raw file to `raw/archived/`

### Changes to `/note`

- After successful article generation, run auto-categorization instead of saving to vault root

## Auto-Categorization Prompt

```
System: You are choosing where to file an article in a wiki vault.
        Given the article details and existing directories, respond with
        ONLY the directory path (e.g., "Research" or "Projects/tools").
        If no existing directory fits, suggest a short, descriptive new one.
        Never use underscore-prefixed directories (those are system directories).

User:   Article title: {title}
        Content preview: {first_200_words}

        Existing directories:
        {directory_list}

        Which directory should this article go in?
```

## Dependencies

Add to `pyproject.toml`:

```toml
markitdown[pdf,docx,pptx,xlsx]
```

This pulls in MarkItDown with the optional dependencies for PDF (pdfminer), Word (python-docx), PowerPoint (python-pptx), and Excel (openpyxl) support. HTML, EPUB, and CSV are handled by MarkItDown's core without extras.

## Error Handling

- **Unsupported format:** immediate error with list of supported extensions
- **MarkItDown failure:** error with the underlying exception message
- **Empty conversion:** if MarkItDown produces no text, error "conversion produced no content"
- **LLM failures:** same handling as existing `/summarize` and `/compile` (log + error message)
- **Categorization failure:** if the categorization LLM call fails or returns nonsense, fall back to `Research/`

## Testing

- `DocumentConverter`: unit tests with small fixture files (tiny PDF, DOCX, CSV)
- Auto-categorization: unit test the prompt construction and directory parsing
- Archive/cleanup: unit test file moves and age-based deletion
- `/import` handler: integration test mocking the inference client
