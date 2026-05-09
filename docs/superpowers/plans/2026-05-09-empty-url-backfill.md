# Empty-URL Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the 43 articles with empty `url: ''` source entries in the vault and prevent regressions at compile time so future PDF/local-source articles always carry a usable provenance pointer.

**Architecture:** Two pieces.
1. **Compile-time fix.** The compile pipeline currently extracts `source_url` from summary frontmatter and ignores `source_file`. Update it to extract both, pass both through to `append_timeline_entry()`, write whichever is present to the sources entry, and refuse to compile when both are empty.
2. **Backfill tool.** A new propose/execute pair (`ProposeUrlFix` / `UrlFix`) that walks the vault, finds articles whose every sources entry has empty `url` and no `source_file`, proposes a fix per article (let the user supply the missing URL or local path), and rewrites the frontmatter atomically.

**Tech Stack:** Python 3, pytest with asyncio, `agent_core.utils.frontmatter` for parse/serialize, `agent_core.approval_registry` for proposal lifecycle, `pal/article.py` and `pal/wiki.py` for vault read/write.

---

## File Structure

**Created:**
- `pal/tools/url_fix.py`: `ProposeUrlFix` and `UrlFix` tool classes
- `tests/test_tools_url_fix.py`: tool-side tests
- `tests/test_compile_source_file.py`: compile-side tests for source_file plumbing

**Modified:**
- `pal/article.py`: `append_timeline_entry()` accepts `source_file`; sources entries include it when present
- `pal/compiler.py`: `compile_one()` and `merge_into_existing()` extract `source_file` from summary meta and pass it through; refuse compile when both `source_url` and `source_file` are empty
- `pal/protocol.py`: add `UrlFixProposalMessage`
- `pal/agent.py` (or wherever tools are registered): register the new tool pair

**No changes needed to:**
- `pal/wiki.py`: `read_article()` and `list_articles()` already do what the backfill tool needs
- `agent_core`: uses existing approval_registry API

---

## Task 1: Inspect existing code and confirm the structural map

**Files (read-only):**
- Read: `pal/compiler.py:74-259`
- Read: `pal/compiler.py:268-368`
- Read: `pal/article.py:50-64`, `pal/article.py:111-128`, `pal/article.py:159-167`
- Read: `pal/tools/consolidate.py` (full file, model for new tool)
- Read: `pal/protocol.py` (look at `ConsolidateProposalMessage` ~line 71-77)
- Read: `tests/test_tools_consolidate.py:1-77` (test scaffolding pattern)

- [ ] **Step 1: Read `pal/compiler.py:74-259`**

Open the file. Confirm that:
- Line ~116-117 reads `source_url = summary_meta.get("source_url", "")` and `source_hash = summary_meta.get("source_hash", "")`.
- Line ~220-225 calls `append_timeline_entry(article, source_url=..., source_hash=...)` with no `source_file` parameter.
- Line ~213 initializes `sources: []`.

Note any differences from the structural map; the line numbers may have drifted.

- [ ] **Step 2: Read `pal/article.py:159-167` (`append_timeline_entry`)**

Confirm the signature and that the sources entry is built as `{"url": source_url, "hash": source_hash, "added": ...}`. Note the exact key names used.

- [ ] **Step 3: Read `pal/tools/consolidate.py` end to end**

Pay attention to:
- The `requires = ("approval_registry",)` declaration on `ProposeConsolidate`.
- The shape of `ar.create_proposal(kind=..., **fields)` and the fields stored.
- How `ProposeConsolidate.run()` blocks on `proposal.event.wait()`.
- How `Consolidate.run()` validates `approved` and not `consumed`, then calls `ar.consume(proposal_id)`.
- The structured return dict shape.

- [ ] **Step 4: Read `pal/protocol.py` around `ConsolidateProposalMessage`**

Note the `@register_message` decorator and the field set (`proposal_id`, plus the proposal-specific fields, plus `type`).

- [ ] **Step 5: Read `tests/test_tools_consolidate.py:1-77`**

Note the `_Config`, `_Agent`, `_ctx()` helpers and how tests instantiate the approval registry. Save these patterns for the new test file.

- [ ] **Step 6: Commit (no code changes)**

Skip. This task is read-only. Move to Task 2.

---

## Task 2: Plumb `source_file` through `append_timeline_entry()`

**Files:**
- Modify: `pal/article.py:159-167`
- Test: `tests/test_compile_source_file.py` (new file)

- [ ] **Step 1: Create the test file with a failing test**

Create `tests/test_compile_source_file.py`:

```python
"""Tests for source_file plumbing through the compile path."""

from pal.article import Article, append_timeline_entry


def _make_article():
    return Article(
        meta={
            "title": "Test",
            "compiled_at": "2026-05-09T00:00:00+00:00",
            "status": "compiled",
            "sources": [],
        },
        body="## Overview\n\nbody\n",
    )


def test_append_timeline_entry_writes_source_file_when_url_empty():
    article = _make_article()
    updated = append_timeline_entry(
        article,
        source_url="",
        source_hash="abc123",
        source_file="raw/archived/Agentic_Design_Patterns.pdf",
        timeline_text="### 2026-05-09 - local\n",
    )
    sources = updated.meta["sources"]
    assert len(sources) == 1
    assert sources[0]["url"] == ""
    assert sources[0]["source_file"] == "raw/archived/Agentic_Design_Patterns.pdf"
    assert sources[0]["hash"] == "abc123"


def test_append_timeline_entry_writes_url_when_present():
    article = _make_article()
    updated = append_timeline_entry(
        article,
        source_url="https://example.com/a",
        source_hash="def456",
        source_file="",
        timeline_text="### 2026-05-09 - example.com\n",
    )
    sources = updated.meta["sources"]
    assert len(sources) == 1
    assert sources[0]["url"] == "https://example.com/a"
    assert sources[0].get("source_file", "") == ""
    assert sources[0]["hash"] == "def456"


def test_append_timeline_entry_omits_empty_source_file_key():
    """When source_file is empty, the key should not appear at all (avoid bloating frontmatter)."""
    article = _make_article()
    updated = append_timeline_entry(
        article,
        source_url="https://example.com/a",
        source_hash="def456",
        source_file="",
        timeline_text="### 2026-05-09 - example.com\n",
    )
    sources = updated.meta["sources"]
    assert "source_file" not in sources[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_compile_source_file.py -v
```
Expected: FAIL because `append_timeline_entry()` does not accept `source_file` parameter.

- [ ] **Step 3: Update `append_timeline_entry()` in `pal/article.py`**

Open `pal/article.py:159-167`. Modify the function to accept `source_file: str = ""` and conditionally include it in the sources entry. Preserve the existing parameter order; add `source_file` after `source_hash`. The new sources-entry construction:

```python
def append_timeline_entry(
    article: Article,
    source_url: str,
    source_hash: str,
    timeline_text: str,
    source_file: str = "",
) -> Article:
    new_sources = list(article.meta.get("sources", []))
    entry = {
        "url": source_url,
        "hash": source_hash,
        "added": _utc_now_iso(),
    }
    if source_file:
        entry["source_file"] = source_file
    new_sources.append(entry)
    new_meta = dict(article.meta)
    new_meta["sources"] = new_sources
    new_body = article.body + "\n" + timeline_text
    return Article(meta=new_meta, body=new_body)
```

(Keep whatever helper `_utc_now_iso()` or equivalent is currently used; do not introduce a new one. If the existing function uses a different field for the timestamp, preserve it.)

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_compile_source_file.py -v
```
Expected: 3 PASS.

- [ ] **Step 5: Run the full pal/article test suite to catch regressions**

Run:
```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/ -k article -v
```
Expected: all green. If anything fails, the change to `append_timeline_entry` broke an existing call site; inspect and fix.

- [ ] **Step 6: Commit**

```bash
git add pal/article.py tests/test_compile_source_file.py
git commit -m "feat(article): plumb source_file through append_timeline_entry"
```

---

## Task 3: Read `source_file` in the compile path and pass it through

**Files:**
- Modify: `pal/compiler.py:74-259` (compile_one), `pal/compiler.py:268-368` (merge_into_existing)
- Test: `tests/test_compile_source_file.py` (extend with compile-path tests)

- [ ] **Step 1: Add a failing test for compile-path source_file extraction**

Append to `tests/test_compile_source_file.py`:

```python
import asyncio
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_compile_one_extracts_source_file_from_summary_meta(tmp_path, monkeypatch):
    """When summary has source_file but no source_url, compile passes source_file through."""
    from pal.compiler import Compiler

    vault = tmp_path / "vault"
    vault.mkdir()
    raw = vault / "raw" / "summaries"
    raw.mkdir(parents=True)

    summary_path = raw / "test-pdf-summary.md"
    summary_path.write_text(
        "---\n"
        "title: Test PDF Summary\n"
        "source_url: ''\n"
        "source_file: raw/archived/test.pdf\n"
        "source_hash: abc123\n"
        "---\n"
        "\n"
        "Summary body.\n"
    )

    inference = _FakeInference(article_body="## Overview\n\nFrom PDF.\n")
    compiler = Compiler(vault_path=vault, inference=inference, categorizer=_FakeCategorizer("AI"))

    result = await compiler.compile_one(summary_path)

    assert result["status"] in ("compiled", "merged")
    article_path = vault / result["path"]
    text = article_path.read_text()
    assert "source_file: raw/archived/test.pdf" in text
    assert "url: ''" in text  # explicit empty preserved


class _FakeInference:
    def __init__(self, article_body):
        self.article_body = article_body
    async def chat(self, *args, **kwargs):
        return self.article_body


class _FakeCategorizer:
    def __init__(self, category):
        self.category = category
    async def categorize(self, *args, **kwargs):
        return self.category
```

(Adjust `_FakeInference` and `_FakeCategorizer` to match the actual interface used by `Compiler`. If `Compiler` takes different injected dependencies, model them on what `tests/test_tools_compile.py` does.)

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_compile_source_file.py::test_compile_one_extracts_source_file_from_summary_meta -v
```
Expected: FAIL because `Compiler.compile_one` does not yet read `source_file`.

- [ ] **Step 3: Update `Compiler.compile_one` to extract source_file**

In `pal/compiler.py` around line 116-117, add a third extraction:

```python
source_url = summary_meta.get("source_url", "")
source_hash = summary_meta.get("source_hash", "")
source_file = summary_meta.get("source_file", "")
```

Then update the call to `append_timeline_entry()` (around line 220-225) to pass `source_file`:

```python
article = append_timeline_entry(
    article,
    source_url=source_url,
    source_hash=source_hash,
    source_file=source_file,
    timeline_text=timeline_text,
)
```

- [ ] **Step 4: Update `merge_into_existing` similarly**

In `pal/compiler.py:358-363`, the same `append_timeline_entry()` call exists for the merge path. Update it to also pass `source_file`. Make sure `source_file` is in scope (extract it the same way at the top of the merge function if needed).

- [ ] **Step 5: Run the test to verify it passes**

Run:
```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_compile_source_file.py -v
```
Expected: all tests in this file PASS.

- [ ] **Step 6: Run the full compile test suite**

Run:
```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_compile.py tests/test_compile_source_file.py -v
```
Expected: all green. If existing compile tests break, the change introduced a regression; inspect.

- [ ] **Step 7: Commit**

```bash
git add pal/compiler.py tests/test_compile_source_file.py
git commit -m "feat(compiler): pass source_file from summary meta through to article sources"
```

---

## Task 4: Refuse to compile when both source_url and source_file are empty

**Files:**
- Modify: `pal/compiler.py` (add validation in `compile_one` before article creation, around line 203)
- Test: `tests/test_compile_source_file.py`

- [ ] **Step 1: Add a failing test for the empty-both rejection**

Append to `tests/test_compile_source_file.py`:

```python
@pytest.mark.asyncio
async def test_compile_one_rejects_empty_source_url_and_source_file(tmp_path):
    """When both source_url and source_file are empty, compile must refuse, not produce empty-URL articles."""
    from pal.compiler import Compiler, EmptySourceError

    vault = tmp_path / "vault"
    vault.mkdir()
    raw = vault / "raw" / "summaries"
    raw.mkdir(parents=True)

    summary_path = raw / "no-source.md"
    summary_path.write_text(
        "---\n"
        "title: No Source\n"
        "source_url: ''\n"
        "source_file: ''\n"
        "source_hash: ''\n"
        "---\n"
        "\n"
        "Summary body.\n"
    )

    inference = _FakeInference(article_body="## Overview\n\nbody\n")
    compiler = Compiler(vault_path=vault, inference=inference, categorizer=_FakeCategorizer("AI"))

    with pytest.raises(EmptySourceError):
        await compiler.compile_one(summary_path)
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_compile_source_file.py::test_compile_one_rejects_empty_source_url_and_source_file -v
```
Expected: FAIL because `EmptySourceError` does not exist and no validation rejects the input.

- [ ] **Step 3: Define `EmptySourceError` and add validation**

At the top of `pal/compiler.py` (with other exceptions if any, otherwise after the imports), add:

```python
class EmptySourceError(ValueError):
    """Raised when a summary has neither source_url nor source_file populated."""
```

In `compile_one`, immediately after the three `summary_meta.get(...)` extractions:

```python
if not source_url and not source_file:
    raise EmptySourceError(
        f"Summary {summary_path} has empty source_url and empty source_file. "
        f"Compile refuses to emit articles without provenance."
    )
```

Add the same validation in `merge_into_existing` after its source extractions, with the same error class.

- [ ] **Step 4: Run the test to verify it passes**

Run:
```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_compile_source_file.py -v
```
Expected: all PASS.

- [ ] **Step 5: Run the full compile test suite**

Run:
```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_compile.py tests/test_compile_source_file.py -v
```
Expected: all green. Existing tests use real source URLs so should not trip the new validation.

- [ ] **Step 6: Commit**

```bash
git add pal/compiler.py tests/test_compile_source_file.py
git commit -m "feat(compiler): refuse compile when both source_url and source_file are empty"
```

---

## Task 5: Build the vault-walk helper for empty-source articles

**Files:**
- Modify: `pal/wiki.py` (add `find_articles_missing_source()`)
- Test: `tests/test_wiki_missing_source.py` (new file)

- [ ] **Step 1: Create the test file with a failing test**

Create `tests/test_wiki_missing_source.py`:

```python
"""Tests for find_articles_missing_source helper."""

from pathlib import Path

from pal.wiki import find_articles_missing_source


def _write(path: Path, frontmatter: str, body: str = "## Overview\n\nbody\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}---\n\n{body}")


def test_returns_articles_with_all_empty_url_and_no_source_file(tmp_path):
    vault = tmp_path / "vault"

    _write(
        vault / "AI" / "good.md",
        "title: Good\nsources:\n  - url: 'https://example.com'\n    hash: abc\n",
    )
    _write(
        vault / "Hardware" / "bad.md",
        "title: Bad\nsources:\n  - url: ''\n    hash: ''\n",
    )
    _write(
        vault / "Hardware" / "alsobad.md",
        "title: AlsoBad\nsources:\n  - url: ''\n    hash: ''\n  - url: ''\n    hash: ''\n",
    )
    _write(
        vault / "Hardware" / "rescued.md",
        "title: Rescued\nsources:\n  - url: ''\n    source_file: 'raw/archived/x.pdf'\n    hash: ''\n",
    )

    results = find_articles_missing_source(vault)
    paths = sorted(p.relative_to(vault).as_posix() for p in results)

    assert paths == ["Hardware/alsobad.md", "Hardware/bad.md"]


def test_skips_system_directories(tmp_path):
    vault = tmp_path / "vault"

    _write(
        vault / "_wisdom" / "rule.md",
        "title: Rule\n",  # no sources at all
    )
    _write(
        vault / "raw" / "notes" / "scratch.md",
        "title: Scratch\nsources:\n  - url: ''\n    hash: ''\n",
    )

    results = find_articles_missing_source(vault)
    assert results == []  # both are in skipped dirs


def test_handles_articles_with_no_sources_array(tmp_path):
    vault = tmp_path / "vault"

    _write(
        vault / "AI" / "no-sources.md",
        "title: NoSources\n",  # no sources key at all
    )

    results = find_articles_missing_source(vault)
    # Articles with no sources array are not "missing source," they predate the convention.
    # Callers can decide what to do with them via a separate helper if needed.
    assert results == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_wiki_missing_source.py -v
```
Expected: FAIL because `find_articles_missing_source` does not exist.

- [ ] **Step 3: Add `find_articles_missing_source` to `pal/wiki.py`**

Append to `pal/wiki.py` (after `list_articles()` around line 114):

```python
def find_articles_missing_source(vault_path: Path) -> list[Path]:
    """Return paths of articles where every sources entry has empty url and no source_file.

    Skips underscore-prefixed system directories (_wisdom, _learning, etc.) and raw/.
    Articles with no sources array are not returned (predate the convention).
    """
    from agent_core.utils.frontmatter import parse_frontmatter

    results = []
    for path in vault_path.rglob("*.md"):
        rel = path.relative_to(vault_path)
        first = rel.parts[0] if rel.parts else ""
        if first.startswith("_") or first == "raw":
            continue

        try:
            meta, _ = parse_frontmatter(path.read_text())
        except Exception:
            continue

        sources = meta.get("sources")
        if not sources:
            continue

        all_empty = all(
            not entry.get("url", "").strip()
            and not entry.get("source_file", "").strip()
            for entry in sources
        )
        if all_empty:
            results.append(path)

    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_wiki_missing_source.py -v
```
Expected: all 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add pal/wiki.py tests/test_wiki_missing_source.py
git commit -m "feat(wiki): add find_articles_missing_source helper"
```

---

## Task 6: Add `UrlFixProposalMessage` to the protocol

**Files:**
- Modify: `pal/protocol.py`

- [ ] **Step 1: Add a failing test for the protocol message**

Create `tests/test_protocol_url_fix.py`:

```python
"""Tests for UrlFixProposalMessage."""

from pal.protocol import UrlFixProposalMessage


def test_url_fix_proposal_message_fields():
    msg = UrlFixProposalMessage(
        proposal_id="test-id",
        article_path="Hardware/arm-architecture.md",
        proposed_url="",
        proposed_source_file="raw/archived/arm-arm.pdf",
        rationale="ARM ARM PDF found in archived sources",
    )
    assert msg.proposal_id == "test-id"
    assert msg.article_path == "Hardware/arm-architecture.md"
    assert msg.proposed_source_file == "raw/archived/arm-arm.pdf"
    assert msg.type == "url_fix_proposal"


def test_url_fix_proposal_message_serializes_to_dict():
    msg = UrlFixProposalMessage(
        proposal_id="abc",
        article_path="AI/x.md",
        proposed_url="https://example.com",
        proposed_source_file="",
        rationale="found via search",
    )
    d = msg.to_dict() if hasattr(msg, "to_dict") else msg.__dict__
    assert d.get("proposal_id") == "abc"
    assert d.get("article_path") == "AI/x.md"
    assert d.get("proposed_url") == "https://example.com"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_protocol_url_fix.py -v
```
Expected: FAIL with `ImportError: cannot import name 'UrlFixProposalMessage'`.

- [ ] **Step 3: Add `UrlFixProposalMessage` to `pal/protocol.py`**

Find the `ConsolidateProposalMessage` definition (around line 71-77). Below it, mirror the same pattern:

```python
@register_message
@dataclass
class UrlFixProposalMessage:
    proposal_id: str
    article_path: str
    proposed_url: str
    proposed_source_file: str
    rationale: str
    type: str = "url_fix_proposal"
```

(If the existing `ConsolidateProposalMessage` uses different decorators or a different base, mirror that exactly. Use the same imports.)

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_protocol_url_fix.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pal/protocol.py tests/test_protocol_url_fix.py
git commit -m "feat(protocol): add UrlFixProposalMessage"
```

---

## Task 7: Implement `ProposeUrlFix` tool

**Files:**
- Create: `pal/tools/url_fix.py`
- Test: `tests/test_tools_url_fix.py` (new file)

- [ ] **Step 1: Create the test file with a failing test for proposal creation**

Create `tests/test_tools_url_fix.py`:

```python
"""Tests for the url_fix propose/execute tool pair."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


class _Config:
    def __init__(self, vault_path):
        self.vault_path = vault_path


class _Agent:
    def __init__(self, vault_path, approval_registry):
        self.config = _Config(vault_path)
        self.approval_registry = approval_registry


def _ctx(agent, emit=None):
    class _C:
        pass
    c = _C()
    c.agent = agent
    c.emit = emit or AsyncMock()
    return c


def _approval_registry_with_proposal(proposal_id="test-proposal-1"):
    """Returns a mock approval registry that auto-approves any proposal."""
    proposal = MagicMock()
    proposal.id = proposal_id
    proposal.status = "approved"
    proposal.consumed = False
    proposal.event = MagicMock()
    proposal.event.wait = AsyncMock(return_value=None)
    proposal.fields = {}

    ar = MagicMock()
    ar.create_proposal = MagicMock(return_value=proposal_id)
    ar.get = MagicMock(return_value=proposal)
    ar.consume = MagicMock()
    return ar, proposal


@pytest.mark.asyncio
async def test_propose_url_fix_creates_proposal_for_each_empty_article(tmp_path):
    from pal.tools.url_fix import ProposeUrlFix

    vault = tmp_path / "vault"
    (vault / "Hardware").mkdir(parents=True)
    (vault / "Hardware" / "arm-architecture.md").write_text(
        "---\n"
        "title: ARM Architecture\n"
        "sources:\n"
        "  - url: ''\n"
        "    hash: ''\n"
        "---\n\n## Overview\n\nbody\n"
    )

    ar, _ = _approval_registry_with_proposal()
    agent = _Agent(vault, ar)
    tool = ProposeUrlFix()

    result = await tool.run(
        ctx=_ctx(agent),
        article_path="Hardware/arm-architecture.md",
        proposed_url="",
        proposed_source_file="raw/archived/arm-arm.pdf",
        rationale="Found in archived sources",
    )

    assert result["status"] == "proposed"
    assert "proposal_id" in result
    ar.create_proposal.assert_called_once()
    call_kwargs = ar.create_proposal.call_args.kwargs
    assert call_kwargs["kind"] == "url_fix"
    assert call_kwargs["article_path"] == "Hardware/arm-architecture.md"
    assert call_kwargs["proposed_source_file"] == "raw/archived/arm-arm.pdf"


@pytest.mark.asyncio
async def test_propose_url_fix_rejects_when_both_proposed_fields_empty(tmp_path):
    from pal.tools.url_fix import ProposeUrlFix

    vault = tmp_path / "vault"
    (vault / "Hardware").mkdir(parents=True)
    (vault / "Hardware" / "arm-architecture.md").write_text(
        "---\ntitle: ARM\nsources:\n  - url: ''\n    hash: ''\n---\n\nbody\n"
    )

    ar, _ = _approval_registry_with_proposal()
    agent = _Agent(vault, ar)
    tool = ProposeUrlFix()

    result = await tool.run(
        ctx=_ctx(agent),
        article_path="Hardware/arm-architecture.md",
        proposed_url="",
        proposed_source_file="",
        rationale="nothing",
    )

    assert result["status"] == "error"
    assert "must provide" in result["message"].lower()
    ar.create_proposal.assert_not_called()


@pytest.mark.asyncio
async def test_propose_url_fix_rejects_when_article_does_not_exist(tmp_path):
    from pal.tools.url_fix import ProposeUrlFix

    vault = tmp_path / "vault"
    vault.mkdir()

    ar, _ = _approval_registry_with_proposal()
    agent = _Agent(vault, ar)
    tool = ProposeUrlFix()

    result = await tool.run(
        ctx=_ctx(agent),
        article_path="Hardware/nonexistent.md",
        proposed_url="https://example.com",
        proposed_source_file="",
        rationale="x",
    )

    assert result["status"] == "error"
    assert "not found" in result["message"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_url_fix.py -v
```
Expected: FAIL with `ImportError: cannot import name 'ProposeUrlFix'`.

- [ ] **Step 3: Create `pal/tools/url_fix.py` with `ProposeUrlFix`**

Create the file. Mirror `pal/tools/consolidate.py:15-113`. The implementation:

```python
"""URL-fix tool pair for backfilling empty-source articles."""

from pathlib import Path
from typing import Any

from pal.protocol import UrlFixProposalMessage


class ProposeUrlFix:
    """Propose a URL or source_file fix for an article missing both."""

    name = "propose_url_fix"
    description = (
        "Propose filling a missing source URL or source_file path for a vault article "
        "whose sources entries are all empty. Blocks until the user approves, edits, or declines. "
        "Returns a proposal_id for use with url_fix."
    )
    requires = ("approval_registry",)

    async def run(
        self,
        ctx,
        article_path: str,
        proposed_url: str,
        proposed_source_file: str,
        rationale: str,
    ) -> dict[str, Any]:
        if not proposed_url.strip() and not proposed_source_file.strip():
            return {
                "status": "error",
                "message": "Must provide at least one of proposed_url or proposed_source_file.",
            }

        vault_path = ctx.agent.config.vault_path
        full_path = vault_path / article_path
        if not full_path.exists():
            return {
                "status": "error",
                "message": f"Article not found: {article_path}",
            }

        ar = ctx.agent.approval_registry
        proposal_id = ar.create_proposal(
            kind="url_fix",
            article_path=article_path,
            proposed_url=proposed_url,
            proposed_source_file=proposed_source_file,
            rationale=rationale,
        )

        await ctx.emit(
            UrlFixProposalMessage(
                proposal_id=proposal_id,
                article_path=article_path,
                proposed_url=proposed_url,
                proposed_source_file=proposed_source_file,
                rationale=rationale,
            )
        )

        proposal = ar.get(proposal_id)
        await proposal.event.wait()

        if proposal.status == "approved":
            return {"status": "proposed", "proposal_id": proposal_id}
        elif proposal.status == "declined":
            return {"status": "declined", "proposal_id": proposal_id}
        else:
            return {"status": proposal.status, "proposal_id": proposal_id}
```

(Compare to `pal/tools/consolidate.py:15-113` and mirror its exact base class, decorators, and emit pattern. If `ProposeConsolidate` inherits from a `Tool` base class, do the same here.)

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_url_fix.py -v
```
Expected: all 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add pal/tools/url_fix.py tests/test_tools_url_fix.py
git commit -m "feat(tools): add ProposeUrlFix for empty-source backfill"
```

---

## Task 8: Implement `UrlFix` execution tool

**Files:**
- Modify: `pal/tools/url_fix.py` (add `UrlFix` class)
- Test: `tests/test_tools_url_fix.py`

**Architectural note (revised after Task 7 review).** The `agent_core.approval_registry.Proposal` dataclass does not store arbitrary fields. `ProposeConsolidate` works because `target_path`, `target_title`, etc. are real fields on the dataclass. The url-fix equivalents (`article_path`, `proposed_url`, `proposed_source_file`) are NOT on `Proposal`, so `ProposeUrlFix` does not (and cannot) pass them as kwargs to `create_proposal`. Instead, `ProposeUrlFix` returns those values in its result JSON when the proposal is approved. The agent reads them from that JSON and passes them to `UrlFix` as direct tool parameters. The registry is used only to verify the proposal is approved and not yet consumed.

- [ ] **Step 1: Add failing tests for execution that rewrites the article**

Append to `tests/test_tools_url_fix.py`. The tests use a real `ApprovalRegistry` (mirroring the consolidate test pattern from Task 7's fixes) and pass tool parameters directly:

```python
@pytest.mark.asyncio
async def test_url_fix_writes_source_file_and_preserves_other_frontmatter(tmp_path):
    """An approved proposal causes the first sources entry to be rewritten with the approved fields."""
    from pal.tools.url_fix import UrlFix
    from agent_core.approval_registry import ApprovalRegistry

    vault = tmp_path / "vault"
    (vault / "Hardware").mkdir(parents=True)
    article_full_path = vault / "Hardware" / "arm-architecture.md"
    article_full_path.write_text(
        "---\n"
        "title: ARM Architecture\n"
        "compiled_at: '2026-04-01T00:00:00+00:00'\n"
        "status: compiled\n"
        "sources:\n"
        "  - url: ''\n"
        "    hash: 'oldhash'\n"
        "---\n"
        "\n"
        "## Overview\n\nbody\n"
    )

    ar = ApprovalRegistry()
    proposal_id = ar.create_proposal(kind="url_fix", rationale="ARM ARM PDF found")
    ar.approve(proposal_id)  # Test simulates user approval. Real flow waits on event.

    agent = _Agent(vault, ar)
    tool = UrlFix()

    result_json = await tool.run(
        {
            "proposal_id": proposal_id,
            "article_path": "Hardware/arm-architecture.md",
            "proposed_url": "",
            "proposed_source_file": "raw/archived/arm-arm.pdf",
        },
        _ctx(agent),
    )
    result = json.loads(result_json)

    assert result["status"] == "fixed"
    text = article_full_path.read_text()
    assert "source_file: raw/archived/arm-arm.pdf" in text
    assert "title: ARM Architecture" in text
    assert "hash: oldhash" in text  # other sources fields preserved
    # Proposal should be consumed after successful execution.
    assert ar.get(proposal_id).consumed


@pytest.mark.asyncio
async def test_url_fix_refuses_unapproved_proposal(tmp_path):
    from pal.tools.url_fix import UrlFix
    from agent_core.approval_registry import ApprovalRegistry

    vault = tmp_path / "vault"
    (vault / "Hardware").mkdir(parents=True)
    (vault / "Hardware" / "x.md").write_text(
        "---\ntitle: X\nsources:\n  - url: ''\n    hash: ''\n---\n\nbody\n"
    )

    ar = ApprovalRegistry()
    proposal_id = ar.create_proposal(kind="url_fix", rationale="z")
    # Do NOT approve.

    agent = _Agent(vault, ar)
    tool = UrlFix()

    result_json = await tool.run(
        {
            "proposal_id": proposal_id,
            "article_path": "Hardware/x.md",
            "proposed_url": "https://example.com",
            "proposed_source_file": "",
        },
        _ctx(agent),
    )
    result = json.loads(result_json)

    assert result["status"] == "error"
    assert "approved" in result["message"].lower()
    assert not ar.get(proposal_id).consumed


@pytest.mark.asyncio
async def test_url_fix_refuses_consumed_proposal(tmp_path):
    from pal.tools.url_fix import UrlFix
    from agent_core.approval_registry import ApprovalRegistry

    vault = tmp_path / "vault"
    (vault / "Hardware").mkdir(parents=True)
    (vault / "Hardware" / "x.md").write_text(
        "---\ntitle: X\nsources:\n  - url: ''\n    hash: ''\n---\n\nbody\n"
    )

    ar = ApprovalRegistry()
    proposal_id = ar.create_proposal(kind="url_fix", rationale="z")
    ar.approve(proposal_id)
    ar.consume(proposal_id)  # Mark already consumed.

    agent = _Agent(vault, ar)
    tool = UrlFix()

    result_json = await tool.run(
        {
            "proposal_id": proposal_id,
            "article_path": "Hardware/x.md",
            "proposed_url": "https://example.com",
            "proposed_source_file": "",
        },
        _ctx(agent),
    )
    result = json.loads(result_json)

    assert result["status"] == "error"
    assert ("consumed" in result["message"].lower()) or ("already" in result["message"].lower())
```

(The exact `ApprovalRegistry` API and the test helpers `_Agent` / `_ctx` come from the patterns established in Task 7's fix commit. Mirror them. If the real `ApprovalRegistry.approve()` or `consume()` signature differs, adapt to match.)

Add `import json` at the top of the test file if it isn't already present (Task 7's tests likely added it already).

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_url_fix.py -v
```
Expected: 3 new tests FAIL with `ImportError: cannot import name 'UrlFix'`.

- [ ] **Step 3: Add `UrlFix` to `pal/tools/url_fix.py`**

Append to `pal/tools/url_fix.py` (mirror the `Tool` base class pattern that `ProposeUrlFix` uses, including the `args: dict, ctx` signature and JSON-string return shape):

```python
class UrlFix(Tool):
    """Execute an approved url_fix proposal: rewrite the article's first sources entry."""

    name = "url_fix"
    description = (
        "Execute an approved propose_url_fix proposal. Pass the proposal_id (from "
        "propose_url_fix's return) plus the approved article_path and proposed url/source_file. "
        "Rewrites the first sources entry in the target article to include the approved values."
    )
    requires = ("approval_registry",)

    @property
    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string"},
                "article_path": {"type": "string"},
                "proposed_url": {"type": "string"},
                "proposed_source_file": {"type": "string"},
            },
            "required": ["proposal_id", "article_path"],
        }

    async def run(self, args: dict, ctx) -> str:
        proposal_id = args["proposal_id"]
        article_path_rel = args["article_path"]
        proposed_url = args.get("proposed_url", "")
        proposed_source_file = args.get("proposed_source_file", "")

        if not proposed_url.strip() and not proposed_source_file.strip():
            return json.dumps({
                "status": "error",
                "message": "Must provide at least one of proposed_url or proposed_source_file.",
            })

        ar = ctx.agent.approval_registry
        proposal = ar.get(proposal_id)

        if proposal is None:
            return json.dumps({"status": "error", "message": f"Proposal not found: {proposal_id}"})

        if proposal.status != "approved":
            return json.dumps({
                "status": "error",
                "message": f"Proposal {proposal_id} is not approved (status={proposal.status}).",
            })

        if proposal.consumed:
            return json.dumps({
                "status": "error",
                "message": f"Proposal {proposal_id} has already been consumed.",
            })

        vault_path = ctx.agent.config.vault_path
        full_path = vault_path / article_path_rel
        if not full_path.exists():
            return json.dumps({
                "status": "error",
                "message": f"Article not found at execute time: {article_path_rel}",
            })

        from agent_core.utils.frontmatter import parse_frontmatter, serialize_frontmatter

        meta, body = parse_frontmatter(full_path.read_text())
        sources = meta.get("sources", [])
        if not sources:
            return json.dumps({
                "status": "error",
                "message": f"Article {article_path_rel} has no sources array to fix.",
            })

        first = dict(sources[0])
        if proposed_url:
            first["url"] = proposed_url
        if proposed_source_file:
            first["source_file"] = proposed_source_file
        sources[0] = first
        meta["sources"] = sources

        full_path.write_text(serialize_frontmatter(meta, body))
        ar.consume(proposal_id)

        return json.dumps({
            "status": "fixed",
            "article_path": article_path_rel,
            "wrote_url": bool(proposed_url),
            "wrote_source_file": bool(proposed_source_file),
        })
```

(Adapt the `Tool` base class import and the exact return-shape conventions to match what `ProposeUrlFix` uses post-Task 7 fixes. The intent: the tool takes the four parameters from the agent, validates the proposal is approved and not consumed via the registry, performs the frontmatter rewrite, marks the proposal consumed, and returns a JSON status.)

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_url_fix.py -v
```
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add pal/tools/url_fix.py tests/test_tools_url_fix.py
git commit -m "feat(tools): add UrlFix execute side for empty-source backfill"
```

---

## Task 9: Register the tool pair with the agent

**Files:**
- Modify: wherever PAL tools are registered (likely `pal/agent.py` or `pal/tools/__init__.py`)

- [ ] **Step 1: Find the tool registration site**

Open `pal/tools/__init__.py` and `pal/agent.py`. Look for where `ProposeConsolidate` and `Consolidate` are imported and added to a tool list or registry. Note the exact pattern: imports at the top, registration at the bottom, or via decorator.

- [ ] **Step 2: Register `ProposeUrlFix` and `UrlFix` the same way**

Add the imports and registration entries mirroring `ProposeConsolidate` / `Consolidate`. Example shape (adapt to actual file):

```python
from pal.tools.url_fix import ProposeUrlFix, UrlFix

# in the tool list:
TOOLS = [
    ...
    ProposeConsolidate(),
    Consolidate(),
    ProposeUrlFix(),
    UrlFix(),
    ...
]
```

- [ ] **Step 3: Run the full test suite to verify nothing broke**

Run:
```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/ -v
```
Expected: all green.

- [ ] **Step 4: Smoke check the agent's tool list**

Start a quick CLI session or unit-call the agent's tool registration logic to verify both new tools appear in the exposed list. If there's a `/tools` command or `agent.list_tools()` method, use that.

```bash
cd /home/edible/Projects/PAL && .venv/bin/python -c "from pal.agent import Agent; a = Agent.__new__(Agent); print([t.name for t in a._build_tools() if hasattr(t, 'name')])"
```

(Adjust the snippet to whatever the actual agent's tool-listing API is. If unsure, skip and rely on the test suite.)

Expected: `propose_url_fix` and `url_fix` appear in the list.

- [ ] **Step 5: Commit**

```bash
git add pal/tools/__init__.py pal/agent.py
git commit -m "feat(agent): register propose_url_fix and url_fix tools"
```

(Adjust the staged paths to whichever file(s) actually changed.)

---

## Task 10: Run the full test suite and verify count of empty-URL articles in a fresh vault snapshot

**Files (read-only):**
- Sanity check: `~/pal-vault-prod` or wherever the audit's vault snapshot lives

- [ ] **Step 1: Run the entire test suite (excluding pre-broken collection)**

Five test files have stale `from pal.client import PalClient` imports left over from the `agent_core` extraction. They fail at collection time and are tracked as a separate workstream. Run with explicit ignores:

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/ \
    --ignore=tests/test_chat_research_integration.py \
    --ignore=tests/test_client.py \
    --ignore=tests/test_daemon.py \
    --ignore=tests/test_integration.py \
    --ignore=tests/test_prompt_injection.py \
    -v
```
Expected: all green. No skips beyond pre-existing ones.

- [ ] **Step 2: Run the new helper against the audit's vault snapshot**

Run:
```bash
cd /home/edible/Projects/PAL && .venv/bin/python -c "
from pathlib import Path
from pal.wiki import find_articles_missing_source

paths = find_articles_missing_source(Path.home() / 'pal-vault-prod')
print(f'Found {len(paths)} articles with empty source.')
for p in sorted(paths)[:10]:
    print(' ', p)
"
```
Expected: number close to the audit's reported 43. Names should overlap with the audit-cited examples (`Hardware/arm-architecture.md`, `Reverse-Engineering/radare2-overview-and-architecture.md`, etc.). If the count is dramatically different (e.g., 0 or 200+), the helper has a bug.

- [ ] **Step 3: No code changes, no commit**

This is a verification step. The plan is implementation-complete after this step. The actual backfill of the 43 articles is operational work driven by the user invoking `propose_url_fix` and `url_fix` over a session, not part of this plan.

---

## Self-review notes (already applied during drafting)

- Spec coverage: all elements of section 5c of the assessment are covered. Compile-side prevention is Tasks 2-4. Backfill tooling is Tasks 5-9. Operational backfill (running the 43) is correctly out of scope for an implementation plan.
- Placeholder scan: every code step has actual code; no "TODO" or "implement appropriate error handling" patterns. The single non-code step (Task 1) is read-only investigation, not a placeholder.
- Type consistency: `source_file` is the consistent field name throughout. `EmptySourceError` is defined in Task 4 and not referenced after; that is intentional.
- One known fragility: Task 9's commands use exact paths and snippets that may need to adapt to the agent's actual registration shape. The step explicitly tells the implementer to adapt.
