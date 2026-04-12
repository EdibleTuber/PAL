# Compiled Truth + Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a structural convention to compiled wiki articles that separates "current best understanding" (compiled truth) from "evidence trail" (timeline), with merge-on-compile when new sources cover existing topics.

**Architecture:** New `pal/article.py` module owns the article format - parsing, validation, timeline management, and serialization. The `/compile` handler gains a topic matching step (checks wiki index for existing articles) and branches into first-compile vs merge-compile. The model writes compiled truth prose using a flexible template; code builds timeline entries deterministically.

**Tech Stack:** Python 3.12, existing PAL modules (frontmatter, wiki, categorizer, inference)

---

## File Structure

```
pal/
├── article.py             # NEW — article format: parse, validate, timeline, serialize
├── daemon.py              # MODIFY — /compile handler: topic matching + merge flow + new prompts
tests/
├── test_article.py        # NEW — unit tests for article module
├── test_compile.py        # MODIFY — update compile tests for new format
```

---

### Task 1: Article Data Structures and Serialization

**Files:**
- Create: `pal/article.py`
- Create: `tests/test_article.py`

- [ ] **Step 1: Write failing tests for Article data structures and serialization**

Create `tests/test_article.py`:

```python
"""Tests for article module — compiled truth + timeline format."""
from datetime import datetime, timezone

import pytest

from pal.article import (
    Article,
    TimelineEntry,
    serialize_article,
    parse_article,
    append_timeline_entry,
    validate_compiled_truth,
    TIMELINE_MARKER,
)


def _make_entry(date="2026-04-12", label="example.com", url="https://example.com/page",
                hash="abc12345", added="2026-04-12T14:30:00+00:00",
                summary="Key findings from this source."):
    return TimelineEntry(
        date=date, source_label=label, source_url=url,
        source_hash=hash, added=added, summary=summary,
    )


def test_serialize_empty_article():
    article = Article(
        meta={"title": "Test", "status": "compiled"},
        compiled_truth="## Overview\n\nTest article.\n\n## Key Concepts\n\n- Concept A\n",
        timeline=[],
    )
    text = serialize_article(article)
    assert "---" in text
    assert "title: Test" in text
    assert "## Overview" in text
    assert TIMELINE_MARKER in text


def test_serialize_article_with_timeline():
    entry = _make_entry()
    article = Article(
        meta={"title": "Test", "status": "compiled"},
        compiled_truth="## Overview\n\nTest.\n\n## Key Concepts\n\n- A\n",
        timeline=[entry],
    )
    text = serialize_article(article)
    assert TIMELINE_MARKER in text
    assert "### 2026-04-12 - example.com" in text
    assert "**Source:** https://example.com/page" in text
    assert "**Added:** 2026-04-12T14:30:00+00:00" in text
    assert "**Source hash:** abc12345" in text
    assert "Key findings from this source." in text


def test_serialize_preserves_multiple_timeline_entries():
    entries = [
        _make_entry(date="2026-04-10", label="first.com", summary="First source."),
        _make_entry(date="2026-04-12", label="second.com", summary="Second source."),
    ]
    article = Article(
        meta={"title": "Test", "status": "compiled"},
        compiled_truth="## Overview\n\nTest.\n\n## Key Concepts\n\n- A\n",
        timeline=entries,
    )
    text = serialize_article(article)
    assert "### 2026-04-10 - first.com" in text
    assert "### 2026-04-12 - second.com" in text
    # First entry should appear before second
    assert text.index("first.com") < text.index("second.com")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/home/edible/Projects/PAL/.venv/bin/pytest tests/test_article.py -v`
Expected: FAIL -- `pal.article` does not exist

- [ ] **Step 3: Create `pal/article.py` with data structures and serialization**

```python
"""Article format — compiled truth + timeline.

Every compiled wiki article has two zones separated by a marker:
- Compiled truth: current best understanding, rewritten on new evidence
- Timeline: append-only evidence trail, one entry per source

The model writes compiled truth prose. Code builds timeline entries.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from pal.frontmatter import parse_frontmatter, serialize_frontmatter

TIMELINE_MARKER = "<!-- TIMELINE -->"


@dataclass
class TimelineEntry:
    date: str            # YYYY-MM-DD
    source_label: str    # hostname or short label
    source_url: str      # full URL
    source_hash: str     # content hash from raw file
    added: str           # ISO timestamp
    summary: str         # thorough summary text


@dataclass
class Article:
    meta: dict                      # YAML frontmatter
    compiled_truth: str             # everything above TIMELINE marker
    timeline: list[TimelineEntry] = field(default_factory=list)


def _format_timeline_entry(entry: TimelineEntry) -> str:
    """Format a single timeline entry as markdown."""
    lines = [
        f"### {entry.date} - {entry.source_label}",
        f"**Source:** {entry.source_url}",
        f"**Added:** {entry.added}",
        f"**Source hash:** {entry.source_hash}",
        "",
        entry.summary.strip(),
    ]
    return "\n".join(lines)


def serialize_article(article: Article) -> str:
    """Assemble an Article into a complete markdown string with frontmatter."""
    truth = article.compiled_truth.strip() + "\n"

    timeline_parts = []
    for entry in article.timeline:
        timeline_parts.append(_format_timeline_entry(entry))

    timeline_text = "\n\n".join(timeline_parts)

    body = f"{truth}\n{TIMELINE_MARKER}\n"
    if timeline_text:
        body += f"\n{timeline_text}\n"

    return serialize_frontmatter(article.meta, body)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/home/edible/Projects/PAL/.venv/bin/pytest tests/test_article.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add pal/article.py tests/test_article.py
git commit -m "feat: article module with data structures and serialization

TimelineEntry, Article dataclasses, and serialize_article for the
compiled truth + timeline format."
```

---

### Task 2: Article Parsing

**Files:**
- Modify: `pal/article.py`
- Modify: `tests/test_article.py`

- [ ] **Step 1: Write failing tests for parse_article**

Add to `tests/test_article.py`:

```python
def test_parse_article_with_timeline():
    text = (
        "---\n"
        "title: Test\n"
        "status: compiled\n"
        "---\n"
        "## Overview\n\nSome content.\n\n"
        "## Key Concepts\n\n- A\n\n"
        "<!-- TIMELINE -->\n\n"
        "### 2026-04-12 - example.com\n"
        "**Source:** https://example.com/page\n"
        "**Added:** 2026-04-12T14:30:00+00:00\n"
        "**Source hash:** abc12345\n\n"
        "Key findings from this source.\n"
    )
    article = parse_article(text)
    assert article.meta["title"] == "Test"
    assert "## Overview" in article.compiled_truth
    assert "## Key Concepts" in article.compiled_truth
    assert TIMELINE_MARKER not in article.compiled_truth
    assert len(article.timeline) == 1
    assert article.timeline[0].source_url == "https://example.com/page"
    assert article.timeline[0].source_hash == "abc12345"
    assert "Key findings" in article.timeline[0].summary


def test_parse_article_without_timeline():
    """Legacy article with no TIMELINE marker — entire body is compiled truth."""
    text = (
        "---\n"
        "title: Legacy\n"
        "status: compiled\n"
        "---\n"
        "# Legacy Article\n\nOld style content.\n"
    )
    article = parse_article(text)
    assert article.meta["title"] == "Legacy"
    assert "Legacy Article" in article.compiled_truth
    assert article.timeline == []


def test_parse_article_multiple_entries():
    text = (
        "---\n"
        "title: Multi\n"
        "---\n"
        "## Overview\n\nContent.\n\n"
        "<!-- TIMELINE -->\n\n"
        "### 2026-04-10 - first.com\n"
        "**Source:** https://first.com/a\n"
        "**Added:** 2026-04-10T10:00:00+00:00\n"
        "**Source hash:** aaa\n\n"
        "First source findings.\n\n"
        "### 2026-04-12 - second.com\n"
        "**Source:** https://second.com/b\n"
        "**Added:** 2026-04-12T12:00:00+00:00\n"
        "**Source hash:** bbb\n\n"
        "Second source findings.\n"
    )
    article = parse_article(text)
    assert len(article.timeline) == 2
    assert article.timeline[0].source_label == "first.com"
    assert article.timeline[1].source_label == "second.com"
    assert "First source" in article.timeline[0].summary
    assert "Second source" in article.timeline[1].summary


def test_parse_roundtrip():
    """serialize -> parse -> serialize should produce the same output."""
    entry = _make_entry()
    original = Article(
        meta={"title": "Roundtrip", "status": "compiled"},
        compiled_truth="## Overview\n\nTest.\n\n## Key Concepts\n\n- A\n",
        timeline=[entry],
    )
    text1 = serialize_article(original)
    parsed = parse_article(text1)
    text2 = serialize_article(parsed)
    assert text1 == text2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/home/edible/Projects/PAL/.venv/bin/pytest tests/test_article.py -v -k "parse"`
Expected: FAIL -- `parse_article` not implemented

- [ ] **Step 3: Implement parse_article**

Add to `pal/article.py`:

```python
import re

_ENTRY_HEADER_RE = re.compile(r"^### (\d{4}-\d{2}-\d{2}) - (.+)$", re.MULTILINE)


def _parse_timeline_entries(timeline_text: str) -> list[TimelineEntry]:
    """Parse the timeline section into a list of TimelineEntry objects."""
    entries = []
    # Split on entry headers
    parts = _ENTRY_HEADER_RE.split(timeline_text)
    # parts[0] is text before first header (usually empty/whitespace)
    # then triples: (date, label, body)
    i = 1
    while i + 2 < len(parts):
        date = parts[i]
        label = parts[i + 1]
        body = parts[i + 2].strip()

        source_url = ""
        source_hash = ""
        added = ""
        summary_lines = []

        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("**Source:**"):
                source_url = stripped.replace("**Source:**", "").strip()
            elif stripped.startswith("**Added:**"):
                added = stripped.replace("**Added:**", "").strip()
            elif stripped.startswith("**Source hash:**"):
                source_hash = stripped.replace("**Source hash:**", "").strip()
            elif stripped:
                summary_lines.append(stripped)

        entries.append(TimelineEntry(
            date=date,
            source_label=label,
            source_url=source_url,
            source_hash=source_hash,
            added=added,
            summary="\n".join(summary_lines),
        ))
        i += 3

    return entries


def parse_article(text: str) -> Article:
    """Parse a markdown article into an Article with compiled truth and timeline.

    If no TIMELINE marker exists (legacy article), the entire body is
    compiled truth and timeline is empty.
    """
    meta, body = parse_frontmatter(text)

    if TIMELINE_MARKER in body:
        parts = body.split(TIMELINE_MARKER, 1)
        compiled_truth = parts[0].strip() + "\n"
        timeline_text = parts[1]
        timeline = _parse_timeline_entries(timeline_text)
    else:
        compiled_truth = body
        timeline = []

    return Article(meta=meta, compiled_truth=compiled_truth, timeline=timeline)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/home/edible/Projects/PAL/.venv/bin/pytest tests/test_article.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add pal/article.py tests/test_article.py
git commit -m "feat: parse_article for compiled truth + timeline format

Splits articles at <!-- TIMELINE --> marker. Parses timeline entries
from structured markdown. Legacy articles without marker are handled
gracefully."
```

---

### Task 3: Timeline Append and Compiled Truth Validation

**Files:**
- Modify: `pal/article.py`
- Modify: `tests/test_article.py`

- [ ] **Step 1: Write failing tests for append_timeline_entry and validate_compiled_truth**

Add to `tests/test_article.py`:

```python
def test_append_timeline_entry():
    article = Article(
        meta={"title": "Test", "status": "compiled", "sources": []},
        compiled_truth="## Overview\n\nTest.\n\n## Key Concepts\n\n- A\n",
        timeline=[],
    )
    updated = append_timeline_entry(
        article=article,
        source_url="https://new.com/doc",
        source_hash="new123",
        summary="New findings from this source.",
    )
    assert len(updated.timeline) == 1
    assert updated.timeline[0].source_url == "https://new.com/doc"
    assert updated.timeline[0].source_hash == "new123"
    assert "New findings" in updated.timeline[0].summary
    assert updated.timeline[0].date  # should have a date
    assert updated.timeline[0].added  # should have a timestamp
    # Source should be added to frontmatter sources list
    assert len(updated.meta["sources"]) == 1
    assert updated.meta["sources"][0]["url"] == "https://new.com/doc"


def test_append_timeline_entry_preserves_existing():
    existing = _make_entry(label="old.com", url="https://old.com/page", hash="old123")
    article = Article(
        meta={
            "title": "Test", "status": "compiled",
            "sources": [{"url": "https://old.com/page", "hash": "old123", "added": "2026-04-10T10:00:00+00:00"}],
        },
        compiled_truth="## Overview\n\nTest.\n\n## Key Concepts\n\n- A\n",
        timeline=[existing],
    )
    updated = append_timeline_entry(
        article=article,
        source_url="https://new.com/doc",
        source_hash="new456",
        summary="New findings.",
    )
    assert len(updated.timeline) == 2
    assert updated.timeline[0].source_label == "old.com"
    assert updated.timeline[1].source_url == "https://new.com/doc"
    assert len(updated.meta["sources"]) == 2


def test_append_timeline_entry_extracts_hostname():
    article = Article(
        meta={"title": "Test", "status": "compiled", "sources": []},
        compiled_truth="## Overview\n\nTest.\n\n## Key Concepts\n\n- A\n",
        timeline=[],
    )
    updated = append_timeline_entry(
        article=article,
        source_url="https://docs.python.org/3/library/asyncio.html",
        source_hash="xyz",
        summary="Asyncio docs.",
    )
    assert updated.timeline[0].source_label == "docs.python.org"


def test_validate_compiled_truth_valid():
    text = "## Overview\n\nGood article.\n\n## Key Concepts\n\n- Concept\n"
    issues = validate_compiled_truth(text)
    assert issues == []


def test_validate_compiled_truth_missing_overview():
    text = "## Key Concepts\n\n- Something\n"
    issues = validate_compiled_truth(text)
    assert any("Overview" in i for i in issues)


def test_validate_compiled_truth_missing_key_concepts():
    text = "## Overview\n\nSomething.\n"
    issues = validate_compiled_truth(text)
    assert any("Key Concepts" in i for i in issues)


def test_validate_compiled_truth_allows_optional_sections():
    text = (
        "## Overview\n\nGood.\n\n"
        "## Key Concepts\n\n- A\n\n"
        "## Usage\n\nSome usage.\n\n"
        "## Gotchas\n\n- Watch out.\n"
    )
    issues = validate_compiled_truth(text)
    assert issues == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/home/edible/Projects/PAL/.venv/bin/pytest tests/test_article.py -v -k "append or validate"`
Expected: FAIL -- functions not implemented

- [ ] **Step 3: Implement append_timeline_entry and validate_compiled_truth**

Add to `pal/article.py`:

```python
def append_timeline_entry(
    article: Article,
    source_url: str,
    source_hash: str,
    summary: str,
) -> Article:
    """Append a new timeline entry and update frontmatter sources list.

    Returns a new Article with the entry appended (does not mutate input).
    """
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    added_str = now.isoformat(timespec="seconds")

    parsed_url = urlparse(source_url)
    label = parsed_url.hostname or source_url

    entry = TimelineEntry(
        date=date_str,
        source_label=label,
        source_url=source_url,
        source_hash=source_hash,
        added=added_str,
        summary=summary.strip(),
    )

    new_timeline = list(article.timeline) + [entry]

    new_sources = list(article.meta.get("sources", []))
    new_sources.append({
        "url": source_url,
        "hash": source_hash,
        "added": added_str,
    })

    new_meta = dict(article.meta)
    new_meta["sources"] = new_sources

    return Article(
        meta=new_meta,
        compiled_truth=article.compiled_truth,
        timeline=new_timeline,
    )


_REQUIRED_SECTIONS = ["## Overview", "## Key Concepts"]


def validate_compiled_truth(text: str) -> list[str]:
    """Check compiled truth text for required sections.

    Returns a list of issues. Empty list means valid.
    """
    issues = []
    for section in _REQUIRED_SECTIONS:
        if section not in text:
            section_name = section.replace("## ", "")
            issues.append(f"Missing required section: {section_name}")
    return issues
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/home/edible/Projects/PAL/.venv/bin/pytest tests/test_article.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add pal/article.py tests/test_article.py
git commit -m "feat: append_timeline_entry and validate_compiled_truth

Timeline entries built by code with auto-extracted hostname and
timestamp. Validation checks for required Overview and Key Concepts
sections."
```

---

### Task 4: Topic Matching

**Files:**
- Modify: `pal/article.py`
- Modify: `tests/test_article.py`

- [ ] **Step 1: Write failing tests for find_existing_article**

Add to `tests/test_article.py`:

```python
from unittest.mock import AsyncMock
from dataclasses import dataclass


@dataclass
class MockInferenceResult:
    content: str
    reasoning: str = ""


@pytest.mark.asyncio
async def test_find_existing_article_match():
    """Should find a matching article when model confirms a match."""
    from pal.article import find_existing_article
    inference = AsyncMock()
    inference.complete.return_value = MockInferenceResult(content="sqlite-vec-search.md")

    articles = [
        {"path": "Research/sqlite-vec-search.md", "title": "SQLite-vec Similarity Search"},
        {"path": "Research/faiss-indexing.md", "title": "FAISS Indexing Strategies"},
    ]
    result = await find_existing_article(
        summary_title="SQLite Vec Search Queries",
        summary_preview="How to query vectors in SQLite-vec...",
        category="Research",
        articles=articles,
        inference=inference,
    )
    assert result is not None
    assert "sqlite-vec" in result["path"]


@pytest.mark.asyncio
async def test_find_existing_article_no_match():
    """Should return None when model says no match."""
    from pal.article import find_existing_article
    inference = AsyncMock()
    inference.complete.return_value = MockInferenceResult(content="NONE")

    articles = [
        {"path": "Research/faiss-indexing.md", "title": "FAISS Indexing Strategies"},
    ]
    result = await find_existing_article(
        summary_title="Quantum Computing Basics",
        summary_preview="Quantum computers use qubits...",
        category="Research",
        articles=articles,
        inference=inference,
    )
    assert result is None


@pytest.mark.asyncio
async def test_find_existing_article_empty_category():
    """Should return None if there are no articles in the category."""
    from pal.article import find_existing_article
    inference = AsyncMock()

    result = await find_existing_article(
        summary_title="New Topic",
        summary_preview="Content...",
        category="Research",
        articles=[],
        inference=inference,
    )
    assert result is None
    inference.complete.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/home/edible/Projects/PAL/.venv/bin/pytest tests/test_article.py -v -k "find_existing"`
Expected: FAIL -- `find_existing_article` not defined

- [ ] **Step 3: Implement find_existing_article**

Add to `pal/article.py`:

```python
TOPIC_MATCH_PROMPT = (
    "You are checking if a new source covers the same topic as an existing "
    "wiki article. Below is the new source title and preview, followed by a "
    "list of existing articles in this category.\n\n"
    "If an existing article covers the same topic (even with different "
    "phrasing), respond with ONLY the filename (e.g., 'sqlite-vec-search.md').\n"
    "If no existing article matches, respond with exactly: NONE"
)


async def find_existing_article(
    summary_title: str,
    summary_preview: str,
    category: str,
    articles: list[dict],
    inference,
) -> dict | None:
    """Check if an existing article covers the same topic as the new source.

    Args:
        summary_title: title of the summary being compiled
        summary_preview: first ~200 words of the summary
        category: target category directory
        articles: list of dicts with 'path' and 'title' keys (from wiki.list_articles)
        inference: InferenceClient

    Returns:
        The matching article dict, or None if no match.
    """
    # Filter to articles in the target category
    category_articles = [a for a in articles if a["path"].startswith(f"{category}/")]
    if not category_articles:
        return None

    article_list = "\n".join(
        f"- {a['path'].split('/')[-1]}: {a['title']}" for a in category_articles
    )

    user_prompt = (
        f"New source title: {summary_title}\n"
        f"New source preview: {summary_preview[:400]}\n\n"
        f"Existing articles in {category}/:\n{article_list}"
    )

    messages = [
        {"role": "system", "content": TOPIC_MATCH_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    try:
        result = await inference.complete(messages, reasoning="off")
        response = (result.content or "").strip()
    except Exception:
        return None

    if not response or response.upper() == "NONE":
        return None

    # Find the article whose filename matches the response
    response_clean = response.strip().strip("'\"")
    for a in category_articles:
        filename = a["path"].split("/")[-1]
        if filename == response_clean or filename.replace(".md", "") == response_clean.replace(".md", ""):
            return a

    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/home/edible/Projects/PAL/.venv/bin/pytest tests/test_article.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add pal/article.py tests/test_article.py
git commit -m "feat: topic matching for merge-on-compile

Checks wiki index for existing articles covering the same topic.
Uses a cheap model call with category-filtered article list."
```

---

### Task 5: Refactor /compile Handler for New Format

**Files:**
- Modify: `pal/daemon.py`
- Modify: `tests/test_compile.py`

- [ ] **Step 1: Write failing tests for new compile behavior**

Update `tests/test_compile.py`. Replace the `_write_summary_file` helper and add new tests. Keep existing tests that test error paths (missing file, empty args, path traversal, insufficient) since those don't change.

Add at the top of the file:

```python
from pal.article import parse_article, TIMELINE_MARKER
```

Replace `_write_summary_file`:

```python
def _write_summary_file(vault, path: str, body: str, title="Quantum Computing Basics",
                        source_url="https://example.com/quantum",
                        source_raw="raw/web/quantum-abc.md",
                        source_hash="abc123") -> None:
    """Helper: write a raw/summaries/ file with frontmatter."""
    from pal.frontmatter import serialize_frontmatter
    full_path = vault / path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "title": title,
        "source_url": source_url,
        "source_raw": source_raw,
        "source_hash": source_hash,
        "summarized_at": "2026-04-05T12:00:00+00:00",
        "sanitization_issues": [],
        "status": "summary",
    }
    full_path.write_text(serialize_frontmatter(meta, body + "\n"))
```

Add new tests:

```python
@pytest.mark.asyncio
async def test_compile_produces_timeline_format(compile_daemon, socket_path, monkeypatch):
    """Compiled articles should have compiled truth + timeline sections."""
    daemon, vault = compile_daemon

    call_count = 0

    async def fake_complete(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Compile: model returns structured compiled truth
            return CompletionResult(
                type="text",
                content=(
                    "## Overview\n\n"
                    "Quantum computers use qubits instead of classical bits.\n\n"
                    "## Key Concepts\n\n"
                    "- **Superposition** - qubits can be in multiple states\n"
                    "- **Entanglement** - qubits can be correlated\n"
                ),
            )
        elif call_count == 2:
            # Categorization
            return CompletionResult(type="text", content="Research")
        else:
            # Topic matching: no existing articles
            return CompletionResult(type="text", content="NONE")

    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    _write_summary_file(
        vault,
        "raw/summaries/quantum-abc.md",
        "Quantum computers use qubits instead of bits. They leverage superposition.",
    )

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("compile", "raw/summaries/quantum-abc.md")
    await client.close()

    research_files = list((vault / "Research").glob("*.md"))
    assert len(research_files) == 1
    content = research_files[0].read_text()
    assert TIMELINE_MARKER in content
    assert "## Overview" in content
    assert "## Key Concepts" in content
    assert "**Source:** https://example.com/quantum" in content
    assert "**Source hash:** abc123" in content


@pytest.mark.asyncio
async def test_compile_merge_updates_existing_article(compile_daemon, socket_path, monkeypatch):
    """Compiling a source that matches an existing article should merge."""
    daemon, vault = compile_daemon

    # Create an existing article in the vault
    from pal.article import Article, TimelineEntry, serialize_article
    existing = Article(
        meta={
            "title": "Quantum Computing Basics",
            "created": "2026-04-10T10:00:00+00:00",
            "updated": "2026-04-10T10:00:00+00:00",
            "compiled_at": "2026-04-10T10:00:00+00:00",
            "status": "compiled",
            "sources": [{"url": "https://old.com/quantum", "hash": "old123", "added": "2026-04-10T10:00:00+00:00"}],
        },
        compiled_truth=(
            "## Overview\n\nOld quantum overview.\n\n"
            "## Key Concepts\n\n- Old concepts\n"
        ),
        timeline=[TimelineEntry(
            date="2026-04-10", source_label="old.com",
            source_url="https://old.com/quantum", source_hash="old123",
            added="2026-04-10T10:00:00+00:00", summary="Old source findings.",
        )],
    )
    research_dir = vault / "Research"
    research_dir.mkdir(parents=True, exist_ok=True)
    (research_dir / "quantum-computing-basics.md").write_text(serialize_article(existing))

    # Rebuild index so the daemon can find it
    daemon.wiki.rebuild_index()

    call_count = 0

    async def fake_complete(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Categorization
            return CompletionResult(type="text", content="Research")
        elif call_count == 2:
            # Topic matching: finds existing article
            return CompletionResult(type="text", content="quantum-computing-basics.md")
        else:
            # Merge compile: model produces updated compiled truth
            return CompletionResult(
                type="text",
                content=(
                    "## Overview\n\n"
                    "Merged quantum overview with new info.\n\n"
                    "## Key Concepts\n\n"
                    "- Old concepts\n- New concepts from new source\n"
                ),
            )

    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    _write_summary_file(
        vault,
        "raw/summaries/quantum-new.md",
        "New quantum findings about error correction.",
        title="Quantum Computing Basics",
        source_url="https://new.com/quantum",
        source_hash="new456",
    )

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("compile", "raw/summaries/quantum-new.md")
    await client.close()

    # Should have updated the existing article, not created a new one
    research_files = list(research_dir.glob("*.md"))
    assert len(research_files) == 1

    article = parse_article(research_files[0].read_text())
    assert "Merged quantum overview" in article.compiled_truth
    assert len(article.timeline) == 2
    assert article.timeline[0].source_url == "https://old.com/quantum"
    assert article.timeline[1].source_url == "https://new.com/quantum"
    assert article.meta["created"] == "2026-04-10T10:00:00+00:00"  # preserved
    assert article.meta["sources"] is not None
    assert len(article.meta["sources"]) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/home/edible/Projects/PAL/.venv/bin/pytest tests/test_compile.py -v -k "timeline or merge"`
Expected: FAIL -- daemon doesn't produce new format yet

- [ ] **Step 3: Update existing compile tests for new format**

The existing tests check for things like `"source_url:" in content` (checking frontmatter) and `"Quantum computers use qubits" in content`. These will need to account for the new format:

- `test_compile_creates_research_article`: The mock model needs to return structured compiled truth (with `## Overview` etc.) and the assertions should check for the timeline marker. Update the `fake_complete` to return structured output, and add `NONE` response for topic matching.

- `test_compile_preserves_provenance_chain`: The frontmatter shape changes -- `source_url` becomes part of the `sources` list, and `source_summary`/`source_raw` move out since that info is in the timeline entry. Update assertions.

- `test_compile_uses_auto_categorization`: Same fake_complete update pattern.

- `test_compile_archives_raw_files`: Same pattern.

Update `fake_complete` in `test_compile_creates_research_article`:

```python
@pytest.mark.asyncio
async def test_compile_creates_research_article(compile_daemon, socket_path, monkeypatch):
    daemon, vault = compile_daemon

    call_count = 0

    async def fake_complete(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Categorization
            return CompletionResult(type="text", content="Research")
        elif call_count == 2:
            # Topic matching
            return CompletionResult(type="text", content="NONE")
        else:
            # Compilation
            return CompletionResult(
                type="text",
                content="## Overview\n\nQuantum computers use qubits.\n\n## Key Concepts\n\n- Superposition\n",
            )

    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    _write_summary_file(
        vault,
        "raw/summaries/quantum-abc.md",
        "Quantum computers use qubits instead of bits.",
    )

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("compile", "raw/summaries/quantum-abc.md")
    assert "Research/" in resp.text
    await client.close()

    research_files = list((vault / "Research").glob("*.md"))
    assert len(research_files) == 1
    content = research_files[0].read_text()
    assert "Quantum computers use qubits" in content
    assert TIMELINE_MARKER in content
```

Apply the same pattern to the other existing tests: update `fake_complete` to include categorization as call 1, topic matching as call 2, and compilation as call 3. Update frontmatter assertions to use the new `sources` list format.

Note: The exact call ordering depends on implementation -- the daemon may call categorization first (to know which directory to check for topic matching), then topic matching, then compilation. This is the order the spec describes.

- [ ] **Step 4: Refactor _handle_compile in daemon.py**

Add import at top of daemon.py:
```python
from pal.article import (
    parse_article, serialize_article, append_timeline_entry,
    validate_compiled_truth, find_existing_article, Article,
)
```

Replace the body of `_handle_compile` (after the path validation and summary reading, from the `# Build messages` comment at line 884 onward) with the new compile flow:

```python
        # Read summary
        from pal.frontmatter import parse_frontmatter
        summary_meta, summary_body = parse_frontmatter(full_path.read_text())

        title = summary_meta.get("title", full_path.stem)

        # Step 1: Categorize
        category = await self.categorizer.categorize(
            title=title,
            body=summary_body,
            vault_path=self.config.vault_path,
        )

        # Step 2: Topic matching — check for existing article
        all_articles = self.wiki.list_articles()
        existing_match = await find_existing_article(
            summary_title=title,
            summary_preview=summary_body[:400],
            category=category,
            articles=all_articles,
            inference=self.inference,
        )

        # Step 3: Compile
        base_prompt = self.prompt_builder.build()
        source_url = summary_meta.get("source_url", "")
        source_hash = summary_meta.get("source_hash", "")

        if existing_match:
            # Merge compile — rewrite compiled truth with new evidence
            existing_text = (self.config.vault_path / existing_match["path"]).read_text()
            existing_article = parse_article(existing_text)

            timeline_context = "\n".join(
                f"- {e.date} {e.source_label}: {e.summary[:200]}"
                for e in existing_article.timeline
            )

            system_prompt = (
                f"{base_prompt}\n\n"
                "You are updating a wiki article with new information. "
                "Rewrite the compiled truth sections to incorporate the new source material. "
                "Keep the same section structure. Do not drop existing knowledge unless "
                "the new source directly contradicts it.\n\n"
                "Required sections: ## Overview, ## Key Concepts\n"
                "Optional sections (include if relevant): ## Usage, ## Configuration, "
                "## Gotchas, ## Related\n\n"
                "Use ONLY information from the existing article and the new source material. "
                "Do NOT add facts not present in either."
            )

            user_prompt = (
                f"CURRENT COMPILED TRUTH:\n\n{existing_article.compiled_truth.strip()}\n\n"
                f"PREVIOUS SOURCES:\n{timeline_context}\n\n"
                f"NEW SOURCE MATERIAL:\n"
                f"Title: {title}\n"
                f"Source URL: {source_url}\n\n"
                f"{summary_body.strip()}\n\n"
                "---\n\n"
                "Rewrite the compiled truth incorporating the new information."
            )
        else:
            # First compile — produce initial article
            existing_article = None

            system_prompt = (
                f"{base_prompt}\n\n"
                "You are compiling a wiki article from source material. RULES:\n"
                "- Use ONLY information from the SOURCE MATERIAL below.\n"
                "- Do NOT add facts that aren't in the source.\n"
                "- If the source lacks sufficient detail, respond with exactly: "
                "INSUFFICIENT: <one-sentence reason>\n\n"
                "Required sections: ## Overview, ## Key Concepts\n"
                "Optional sections (include if relevant): ## Usage, ## Configuration, "
                "## Gotchas, ## Related"
            )

            user_prompt = (
                f"SOURCE MATERIAL (reviewed summary):\n\n"
                f"Title: {title}\n"
                f"Source URL: {source_url}\n\n"
                f"{summary_body.strip()}\n\n"
                f"---\n\n"
                f"Write a grounded wiki article based on this source material."
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            result = await self.inference.complete(messages, reasoning="off")
            compiled_truth = result.content or ""
        except Exception as exc:
            logger.exception("Compile inference failed: %s", exc)
            error = ErrorMessage(error=f"Compile failed: {exc}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        if compiled_truth.strip().startswith("INSUFFICIENT:"):
            resp = ResponseMessage(
                text=(
                    f"{compiled_truth.strip()}\n\n"
                    "No article saved. The source summary may need more detail."
                ),
                command="compile",
            )
            writer.write(encode_message(resp))
            await writer.drain()
            return

        # Validate required sections
        issues = validate_compiled_truth(compiled_truth)
        if issues:
            logger.warning("Compiled truth validation issues: %s", issues)

        # Build article
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        if existing_article:
            # Merge: update existing article
            article = Article(
                meta=dict(existing_article.meta),
                compiled_truth=compiled_truth.strip() + "\n",
                timeline=list(existing_article.timeline),
            )
            article.meta["updated"] = now
            article.meta["compiled_at"] = now
        else:
            # New article
            article = Article(
                meta={
                    "title": title,
                    "created": now,
                    "updated": now,
                    "compiled_at": now,
                    "status": "compiled",
                    "sources": [],
                },
                compiled_truth=compiled_truth.strip() + "\n",
                timeline=[],
            )

        # Append timeline entry (handles sources list update)
        article = append_timeline_entry(
            article=article,
            source_url=source_url,
            source_hash=source_hash,
            summary=summary_body.strip(),
        )

        # Determine save path
        if existing_match:
            article_path_rel = existing_match["path"]
            article_full_path = self.config.vault_path / article_path_rel
        else:
            slug = title.lower().replace("_", "-").replace(" ", "-")
            slug = "".join(c for c in slug if c.isalnum() or c == "-").strip("-") or "untitled"
            target_dir = self.config.vault_path / category
            target_dir.mkdir(parents=True, exist_ok=True)
            article_path_rel = f"{category}/{slug}.md"
            article_full_path = target_dir / f"{slug}.md"

        article_full_path.write_text(serialize_article(article))
        logger.info("Compiled %s -> %s", summary_path, article_path_rel)

        # Rebuild index and commit
        self.wiki.rebuild_index()
        self.wiki.git_init()
        self.wiki.git_commit(f"compile: {title}")

        # Archive raw intermediates
        source_raw = summary_meta.get("source_raw", "")
        archive_raw_files(self.config.vault_path, raw_path=source_raw, summary_path=summary_path)
        self.wiki.git_commit(f"archive: {title}")

        resp = ResponseMessage(
            text=(
                f"{'Updated' if existing_match else 'Saved to'} {article_path_rel}\n\n"
                f"{compiled_truth.strip()}"
            ),
            command="compile",
        )
        writer.write(encode_message(resp))
        await writer.drain()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `/home/edible/Projects/PAL/.venv/bin/pytest tests/test_compile.py -v`
Expected: ALL PASS

- [ ] **Step 6: Run full test suite**

Run: `/home/edible/Projects/PAL/.venv/bin/pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add pal/daemon.py tests/test_compile.py
git commit -m "feat: compiled truth + timeline in /compile

First compile produces structured articles with flexible template
and initial timeline entry. Merge compile rewrites compiled truth
when new source covers an existing topic. Topic matching via wiki
index + model confirmation."
```

---

### Task 6: Full Integration Test and Verification

**Files:**
- All modified files from previous tasks

- [ ] **Step 1: Run the complete test suite**

Run: `/home/edible/Projects/PAL/.venv/bin/pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 2: Manual smoke test (if inference server available)**

```bash
cd /home/edible/Projects/PAL
.venv/bin/pal
```

First compile (new article):
```
/compile raw/summaries/<any-summary>.md
```

Verify:
- Article has `## Overview` and `## Key Concepts` sections
- `<!-- TIMELINE -->` marker present
- Timeline entry with source URL, hash, date, summary
- Frontmatter has `sources` list

Second compile (merge):
```
/compile raw/summaries/<different-summary-same-topic>.md
```

Verify:
- Existing article updated (not duplicated)
- Compiled truth rewritten to incorporate new info
- Two timeline entries (old + new)
- `sources` list has two entries
- `created` timestamp preserved, `updated`/`compiled_at` changed

- [ ] **Step 3: Verify legacy compatibility**

Check that existing articles in the vault (without `<!-- TIMELINE -->` marker) are still readable:
```
/read <any-existing-article>
```
Expected: Works normally. The article module treats these as all-compiled-truth with empty timeline.

- [ ] **Step 4: Final commit if fixes needed**

Only if smoke testing reveals issues:
```bash
git add -A
git commit -m "fix: address issues found during compiled truth smoke test"
```
