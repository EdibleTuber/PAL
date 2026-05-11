# Chat-Derived Knowledge Promotion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single-tool path (`propose_promote_synthesis`) that promotes user-approved chat synthesis (or existing orphan notes) into wiki articles with typed `chat` provenance, an in-body trust banner, and a companion system-prompt rule that makes PAL react to the banner during answer synthesis.

**Architecture:** New tool emits a proposal, blocks for approval, then writes a chat-derived summary file and invokes a new `Compiler.compile_chat_synthesis` entrypoint. The new entrypoint treats the user-approved synthesis as compiled truth directly (no LLM re-extraction), prepends a sentinel banner, and otherwise reuses the existing categorize / topic-match / write / index / commit / archive scaffolding. Provenance flows as `source_type` through the timeline serializer, parser, and `meta.sources` entries.

**Tech Stack:** Python 3.12, pytest, agent_core (cross-repo), PAL daemon + bridge.

**Cross-repo note:** Phase 1 modifies `agent_core` (a separate repo at `/home/edible/Projects/agent_core`). PAL imports it via editable install. Run agent_core tests in that repo, then verify PAL still imports cleanly. Subsequent phases all live in PAL.

---

## File Structure

**Cross-repo (`/home/edible/Projects/agent_core/`):**
- Modify `agent_core/approval_registry.py`: add `promote_synthesis` to `ProposalKind`, add `note_path` field to `Proposal`, add validation block.
- Modify `tests/test_approval_registry.py`: tests for the new kind.

**PAL repo (`/home/edible/Projects/PAL/`):**
- Modify `pal/article.py`: extend `TimelineEntry` with `source_type`, format/parse round-trip, `append_timeline_entry` plumbing.
- Modify `pal/compiler.py`: add `Compiler.compile_chat_synthesis` and `Compiler.merge_chat_synthesis_into_existing`; add `CHAT_BANNER_SENTINEL` constant and `make_chat_banner(date_str)` helper.
- Create `pal/tools/promote_synthesis.py`: new `PromoteSynthesisProposal` tool.
- Modify `pal/protocol.py`: add `PromoteSynthesisProposalMessage`, register in union.
- Modify `pal/agent.py` (or wherever tools are registered; see commit `2da344d` for the recent pattern with `delete_file`/`replace_in_file`): register the new tool.
- Modify `pal/discord_interactions.py`: handle `PromoteSynthesisProposalMessage` (mirror `_handle_promote_proposal`).
- Modify `pal/cli.py`: handle approval prompt for the new message in the CLI surface.
- Modify `pal/prompts/system.py`: add nudge addendum + banner-reaction rule.
- New tests under `tests/`.

---

## Phase 1: agent_core ApprovalRegistry extension

### Task 1: Add `promote_synthesis` proposal kind to agent_core

**Files:**
- Modify: `/home/edible/Projects/agent_core/agent_core/approval_registry.py:20`
- Modify: `/home/edible/Projects/agent_core/agent_core/approval_registry.py:97-103` (validation block)
- Modify: `/home/edible/Projects/agent_core/agent_core/approval_registry.py:25-48` (Proposal dataclass)
- Test: `/home/edible/Projects/agent_core/tests/test_approval_registry.py`

- [ ] **Step 1: Write failing tests**

Add to `/home/edible/Projects/agent_core/tests/test_approval_registry.py`:

```python
def test_create_promote_synthesis_proposal():
    ar = ApprovalRegistry()
    proposal_id = ar.create_proposal(
        kind="promote_synthesis",
        rationale="User asked to promote vibe-coding chat",
        note_path="raw/notes/vibe-coding.md",
        target_title="Vibe-coding comprehension strategies",
        slug="vibe-coding-comprehension-strategies",
    )
    p = ar.get(proposal_id)
    assert p is not None
    assert p.kind == "promote_synthesis"
    assert p.note_path == "raw/notes/vibe-coding.md"
    assert p.target_title == "Vibe-coding comprehension strategies"
    assert p.slug == "vibe-coding-comprehension-strategies"
    assert p.status == "pending"


def test_create_promote_synthesis_requires_note_path():
    ar = ApprovalRegistry()
    with pytest.raises(ValueError, match="note_path"):
        ar.create_proposal(
            kind="promote_synthesis",
            rationale="x",
            target_title="t",
            slug="s",
        )


def test_create_promote_synthesis_requires_target_title():
    ar = ApprovalRegistry()
    with pytest.raises(ValueError, match="target_title"):
        ar.create_proposal(
            kind="promote_synthesis",
            rationale="x",
            note_path="raw/notes/x.md",
            slug="s",
        )


def test_create_promote_synthesis_requires_slug():
    ar = ApprovalRegistry()
    with pytest.raises(ValueError, match="slug"):
        ar.create_proposal(
            kind="promote_synthesis",
            rationale="x",
            note_path="raw/notes/x.md",
            target_title="t",
        )
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/agent_core && pytest tests/test_approval_registry.py::test_create_promote_synthesis_proposal -v
```
Expected: FAIL (ProposalKind does not include "promote_synthesis", or note_path attribute does not exist).

- [ ] **Step 3: Update `ProposalKind` literal**

In `/home/edible/Projects/agent_core/agent_core/approval_registry.py:20`, change:

```python
ProposalKind = Literal["research", "compile", "reorg", "consolidate", "promote", "batch_fallback"]
```

to:

```python
ProposalKind = Literal["research", "compile", "reorg", "consolidate", "promote", "promote_synthesis", "batch_fallback"]
```

- [ ] **Step 4: Add `note_path` field to `Proposal` dataclass**

In `/home/edible/Projects/agent_core/agent_core/approval_registry.py`, in the `Proposal` dataclass (around line 25-48), add a new field after `slug` and `body`:

```python
    note_path: Optional[str] = None
```

- [ ] **Step 5: Add `note_path` parameter to `create_proposal` signature**

In `create_proposal` (around line 59-74), add `note_path: Optional[str] = None` to the keyword arguments.

- [ ] **Step 6: Add validation block for `promote_synthesis` kind**

In `create_proposal`, after the existing `if kind == "promote":` block (around line 97-103), add:

```python
        if kind == "promote_synthesis":
            if not note_path:
                raise ValueError("promote_synthesis proposals require note_path")
            if not target_title:
                raise ValueError("promote_synthesis proposals require target_title")
            if not slug:
                raise ValueError("promote_synthesis proposals require slug")
```

- [ ] **Step 7: Pass `note_path` through to the `Proposal` constructor**

In the `Proposal(...)` call at the end of `create_proposal`, add `note_path=note_path,` alongside the other fields.

- [ ] **Step 8: Run tests, verify they pass**

```bash
cd /home/edible/Projects/agent_core && pytest tests/test_approval_registry.py -v
```
Expected: all four new tests pass; existing tests still pass.

- [ ] **Step 9: Commit (in agent_core repo)**

```bash
cd /home/edible/Projects/agent_core && git add agent_core/approval_registry.py tests/test_approval_registry.py && git commit -m "$(cat <<'EOF'
feat(approval_registry): add promote_synthesis kind and note_path field

Supports a new PAL chat-derived promotion flow where the proposal carries
the path of a synthesis note to be promoted into a wiki article.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 10: Verify PAL picks up the change**

```bash
cd /home/edible/Projects/PAL && python -c "from agent_core.approval_registry import ProposalKind; import typing; assert 'promote_synthesis' in typing.get_args(ProposalKind)"
```
Expected: no output (assertion passes). If it fails, agent_core is not editable-installed; run `pip install -e /home/edible/Projects/agent_core` from inside the PAL venv first.

---

## Phase 2: Article.py source_type round-trip

### Task 2: Extend `TimelineEntry` dataclass with `source_type`

**Files:**
- Modify: `pal/article.py:20-27`
- Test: `tests/test_article.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_article.py`:

```python
def test_timeline_entry_default_source_type_is_external():
    entry = TimelineEntry(
        date="2026-05-10",
        source_label="example.com",
        source_url="https://example.com",
        source_hash="abc123",
        added="2026-05-10T15:00:00+00:00",
        summary="example summary",
    )
    assert entry.source_type == "external"


def test_timeline_entry_explicit_source_type_chat():
    entry = TimelineEntry(
        date="2026-05-10",
        source_label="chat",
        source_url="",
        source_hash="abc123",
        added="2026-05-10T15:00:00+00:00",
        summary="example summary",
        source_type="chat",
    )
    assert entry.source_type == "chat"
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_article.py::test_timeline_entry_default_source_type_is_external -v
```
Expected: FAIL with `TypeError: TimelineEntry.__init__() got an unexpected keyword argument 'source_type'` or `AttributeError`.

- [ ] **Step 3: Add `source_type` field to `TimelineEntry`**

In `pal/article.py:20-27`, change:

```python
@dataclass
class TimelineEntry:
    date: str            # YYYY-MM-DD
    source_label: str    # hostname or short label
    source_url: str      # full URL
    source_hash: str     # content hash from raw file
    added: str           # ISO timestamp
    summary: str         # thorough summary text
```

to:

```python
@dataclass
class TimelineEntry:
    date: str            # YYYY-MM-DD
    source_label: str    # hostname or short label
    source_url: str      # full URL
    source_hash: str     # content hash from raw file
    added: str           # ISO timestamp
    summary: str         # thorough summary text
    source_type: str = "external"  # provenance class: "external" or "chat"
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_article.py -v
```
Expected: new tests pass; all existing tests pass (back-compat default).

- [ ] **Step 5: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/article.py tests/test_article.py && git commit -m "$(cat <<'EOF'
feat(article): add source_type field to TimelineEntry, default external

Foundation for chat-derived provenance. Defaults preserve existing
behavior for all current call sites.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 3: Format and parse `source_type` in timeline entries

**Files:**
- Modify: `pal/article.py:37-47` (`_format_timeline_entry`)
- Modify: `pal/article.py:70-108` (`_parse_timeline_entries`)
- Test: `tests/test_article.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_article.py`:

```python
def test_format_timeline_entry_includes_source_type_when_chat():
    entry = TimelineEntry(
        date="2026-05-10",
        source_label="chat",
        source_url="",
        source_hash="abc123",
        added="2026-05-10T15:00:00+00:00",
        summary="example",
        source_type="chat",
    )
    formatted = _format_timeline_entry(entry)
    assert "**Source type:** chat" in formatted


def test_format_timeline_entry_omits_source_type_when_external():
    entry = TimelineEntry(
        date="2026-05-10",
        source_label="example.com",
        source_url="https://example.com",
        source_hash="abc123",
        added="2026-05-10T15:00:00+00:00",
        summary="example",
        source_type="external",
    )
    formatted = _format_timeline_entry(entry)
    assert "**Source type:**" not in formatted


def test_parse_timeline_reads_source_type():
    timeline_text = """
### 2026-05-10 - chat
**Source:** 
**Added:** 2026-05-10T15:00:00+00:00
**Source hash:** abc123
**Source type:** chat

example summary
"""
    entries = _parse_timeline_entries(timeline_text)
    assert len(entries) == 1
    assert entries[0].source_type == "chat"


def test_parse_timeline_defaults_source_type_external():
    timeline_text = """
### 2026-05-10 - example.com
**Source:** https://example.com
**Added:** 2026-05-10T15:00:00+00:00
**Source hash:** abc123

example summary
"""
    entries = _parse_timeline_entries(timeline_text)
    assert len(entries) == 1
    assert entries[0].source_type == "external"


def test_timeline_round_trip_preserves_source_type():
    """Critical: serialize → parse → re-serialize must preserve source_type."""
    article = Article(
        meta={"title": "x", "sources": []},
        compiled_truth="## Overview\nfoo\n## Key Concepts\nbar\n",
        timeline=[
            TimelineEntry(
                date="2026-05-10",
                source_label="chat",
                source_url="",
                source_hash="abc123",
                added="2026-05-10T15:00:00+00:00",
                summary="synth",
                source_type="chat",
            ),
        ],
    )
    serialized = serialize_article(article)
    reparsed = parse_article(serialized)
    assert reparsed.timeline[0].source_type == "chat"
    re_serialized = serialize_article(reparsed)
    assert re_serialized == serialized
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_article.py::test_format_timeline_entry_includes_source_type_when_chat tests/test_article.py::test_parse_timeline_reads_source_type tests/test_article.py::test_timeline_round_trip_preserves_source_type -v
```
Expected: FAIL.

- [ ] **Step 3: Update `_format_timeline_entry` to write `**Source type:**` when non-external**

Replace `_format_timeline_entry` in `pal/article.py:37-47` with:

```python
def _format_timeline_entry(entry: TimelineEntry) -> str:
    """Format a single timeline entry as markdown."""
    lines = [
        f"### {entry.date} - {entry.source_label}",
        f"**Source:** {entry.source_url}",
        f"**Added:** {entry.added}",
        f"**Source hash:** {entry.source_hash}",
    ]
    if entry.source_type != "external":
        lines.append(f"**Source type:** {entry.source_type}")
    lines.extend(["", entry.summary.strip()])
    return "\n".join(lines)
```

- [ ] **Step 4: Update `_parse_timeline_entries` to read `**Source type:**`**

In `_parse_timeline_entries` (`pal/article.py:70-108`), change the per-line parsing block. Find:

```python
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
```

Replace with (note: `**Source type:**` check must come before `**Source:**` because it's a prefix superset):

```python
        source_type = "external"
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("**Source type:**"):
                source_type = stripped.replace("**Source type:**", "").strip() or "external"
            elif stripped.startswith("**Source:**"):
                source_url = stripped.replace("**Source:**", "").strip()
            elif stripped.startswith("**Added:**"):
                added = stripped.replace("**Added:**", "").strip()
            elif stripped.startswith("**Source hash:**"):
                source_hash = stripped.replace("**Source hash:**", "").strip()
            elif stripped:
                summary_lines.append(stripped)
```

Then in the `entries.append(TimelineEntry(...))` call, add `source_type=source_type,`.

- [ ] **Step 5: Run tests, verify they pass**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_article.py -v
```
Expected: all tests (new and existing) pass. The round-trip test is the critical one.

- [ ] **Step 6: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/article.py tests/test_article.py && git commit -m "$(cat <<'EOF'
feat(article): round-trip source_type through timeline serialize/parse

Adds **Source type:** line to formatted entries when non-external, parses
it back, and verifies serialize → parse → serialize stability. Closes the
silent-drop risk flagged by panel review.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 4: Plumb `source_type` through `append_timeline_entry`

**Files:**
- Modify: `pal/article.py:131-179`
- Test: `tests/test_article.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_article.py`:

```python
def test_append_timeline_entry_propagates_source_type_to_entry_and_meta():
    article = Article(
        meta={"title": "x", "sources": []},
        compiled_truth="## Overview\nfoo\n",
        timeline=[],
    )
    updated = append_timeline_entry(
        article=article,
        source_url="",
        source_hash="abc123",
        summary="synth",
        source_file="raw/notes/foo.md",
        source_type="chat",
    )
    assert updated.timeline[-1].source_type == "chat"
    assert updated.meta["sources"][-1]["source_type"] == "chat"
    assert updated.meta["sources"][-1]["source_file"] == "raw/notes/foo.md"


def test_append_timeline_entry_default_source_type_external():
    """Existing call sites with no source_type kwarg get external."""
    article = Article(meta={"title": "x", "sources": []}, compiled_truth="", timeline=[])
    updated = append_timeline_entry(
        article=article,
        source_url="https://example.com",
        source_hash="abc",
        summary="s",
    )
    assert updated.timeline[-1].source_type == "external"
    assert updated.meta["sources"][-1].get("source_type", "external") == "external"
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_article.py::test_append_timeline_entry_propagates_source_type_to_entry_and_meta -v
```
Expected: FAIL with `unexpected keyword argument 'source_type'`.

- [ ] **Step 3: Update `append_timeline_entry` signature and body**

In `pal/article.py:131-179`, change the signature:

```python
def append_timeline_entry(
    article: Article,
    source_url: str,
    source_hash: str,
    summary: str,
    source_file: str = "",
    source_type: str = "external",
) -> Article:
```

In the `TimelineEntry(...)` construction inside the function, add `source_type=source_type,`.

In the `source_entry = {...}` dict, add the line `if source_type != "external": source_entry["source_type"] = source_type` after the existing `if source_file: source_entry["source_file"] = source_file` block.

- [ ] **Step 4: Run tests, verify they pass**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_article.py -v
```
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/article.py tests/test_article.py && git commit -m "$(cat <<'EOF'
feat(article): propagate source_type through append_timeline_entry

Stores source_type on both the TimelineEntry and the meta.sources entry
when non-external. Default behavior unchanged for existing callers.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 3: Compiler chat-aware path

### Task 5: Add chat banner constants and `Compiler.compile_chat_synthesis`

**Files:**
- Modify: `pal/compiler.py`
- Create: `tests/test_compile_chat_synthesis.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_compile_chat_synthesis.py`:

```python
"""Tests for Compiler.compile_chat_synthesis (chat-derived promotion path)."""
from __future__ import annotations

from pathlib import Path

import pytest

from pal.compiler import Compiler, CHAT_BANNER_SENTINEL, make_chat_banner
from pal.article import parse_article


class FakeWiki:
    def __init__(self):
        self.commits = []
        self.indexed = False
    def list_articles(self):
        return []
    def rebuild_index(self):
        self.indexed = True
    def git_init(self):
        pass
    def git_commit(self, msg):
        self.commits.append(msg)


class FakeInference:
    """Should never be called by the chat-aware path."""
    async def complete(self, *args, **kwargs):
        raise AssertionError("compile_chat_synthesis must not call inference")


class FakeCategorizer:
    async def categorize(self, **kwargs):
        return "Software-Development"


class FakePromptBuilder:
    def build(self):
        return "system prompt"


def _write_summary(vault: Path, slug: str, body: str) -> str:
    summaries = vault / "raw" / "summaries"
    summaries.mkdir(parents=True, exist_ok=True)
    (vault / "raw" / "notes").mkdir(parents=True, exist_ok=True)
    note_path = vault / "raw" / "notes" / f"{slug}.md"
    note_path.write_text(body)
    summary_path = summaries / f"{slug}.md"
    summary_path.write_text(
        "---\n"
        f"title: \"{slug}\"\n"
        f"source_file: \"raw/notes/{slug}.md\"\n"
        "source_url: \"\"\n"
        "source_type: chat\n"
        "source_hash: \"abc123\"\n"
        f"source_raw: \"raw/notes/{slug}.md\"\n"
        "---\n"
        f"{body}"
    )
    return f"raw/summaries/{slug}.md"


@pytest.mark.asyncio
async def test_compile_chat_synthesis_writes_article_with_banner(tmp_path):
    body = "## Overview\nVibe-coding comprehension is X.\n\n## Key Concepts\n- A\n- B\n"
    summary_rel = _write_summary(tmp_path, "vibe-coding", body)

    compiler = Compiler(
        vault_path=tmp_path,
        wiki=FakeWiki(),
        inference=FakeInference(),
        categorizer=FakeCategorizer(),
        prompt_builder=FakePromptBuilder(),
    )

    result = await compiler.compile_chat_synthesis(summary_rel)

    assert result["status"] == "ok"
    article_rel = result["article_path_rel"]
    assert article_rel.startswith("Software-Development/")
    article_full = tmp_path / article_rel
    assert article_full.exists()

    article = parse_article(article_full.read_text())
    assert article.compiled_truth.lstrip().startswith(CHAT_BANNER_SENTINEL)
    assert "## Overview" in article.compiled_truth
    assert "## Key Concepts" in article.compiled_truth
    assert article.meta["sources"][-1]["source_type"] == "chat"


@pytest.mark.asyncio
async def test_compile_chat_synthesis_returns_insufficient_when_sections_missing(tmp_path):
    body = "Just a paragraph with no required sections.\n"
    summary_rel = _write_summary(tmp_path, "no-sections", body)

    compiler = Compiler(
        vault_path=tmp_path,
        wiki=FakeWiki(),
        inference=FakeInference(),
        categorizer=FakeCategorizer(),
        prompt_builder=FakePromptBuilder(),
    )
    result = await compiler.compile_chat_synthesis(summary_rel)
    assert result["status"] == "insufficient"


def test_make_chat_banner_format():
    banner = make_chat_banner("2026-05-10")
    assert banner.startswith(CHAT_BANNER_SENTINEL)
    assert "2026-05-10" in banner
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_compile_chat_synthesis.py -v
```
Expected: FAIL with `ImportError: cannot import name 'CHAT_BANNER_SENTINEL'`.

- [ ] **Step 3: Add banner constants and helper to `pal/compiler.py`**

Near the top of `pal/compiler.py`, after the imports, add:

```python
CHAT_BANNER_SENTINEL = "> _Source: chat-derived synthesis"


def make_chat_banner(date_str: str) -> str:
    """Return the in-body trust banner for a chat-derived article.

    The sentinel substring (CHAT_BANNER_SENTINEL) must appear at the
    start of compiled_truth for the system prompt's banner-reaction rule
    to trigger and for merge_chat_synthesis_into_existing to detect and
    preserve it.
    """
    return (
        f"{CHAT_BANNER_SENTINEL} (no transcript). User-approved on {date_str}._"
    )
```

- [ ] **Step 4: Add `compile_chat_synthesis` method to `Compiler` class**

In `pal/compiler.py`, inside the `Compiler` class (after `compile_one`, before `merge_into_existing`), add:

```python
    async def compile_chat_synthesis(self, summary_path: str) -> dict[str, Any]:
        """Promote a chat-derived synthesis summary into a wiki article.

        Differs from compile_one: the summary body IS the compiled truth
        (no LLM re-extraction). A trust banner is prepended. source_type
        propagates through to the article's meta.sources entry.

        Returns the same shape as compile_one.
        """
        # Path traversal guard (mirrors compile_one).
        if ".." in summary_path.split("/") or summary_path.startswith("/"):
            return {"status": "invalid_path", "reason": f"Invalid path: {summary_path}"}

        full_path = self.vault_path / summary_path
        if not full_path.exists():
            return {"status": "not_found", "reason": f"File not found: {summary_path}"}

        try:
            resolved = full_path.resolve()
            vault_resolved = self.vault_path.resolve()
            if not str(resolved).startswith(str(vault_resolved) + "/"):
                return {"status": "invalid_path", "reason": f"Invalid path: {summary_path}"}
        except Exception:
            return {"status": "invalid_path", "reason": f"Invalid path: {summary_path}"}

        summary_meta, summary_body = parse_frontmatter(full_path.read_text())

        if len(summary_body) > self.max_body_chars:
            return {
                "status": "too_large",
                "title": summary_meta.get("title", full_path.stem),
                "reason": (
                    f"Source body is {len(summary_body)} characters; "
                    f"exceeds compile limit of {self.max_body_chars}."
                ),
            }

        title = summary_meta.get("title", full_path.stem)
        source_url = summary_meta.get("source_url", "")
        source_hash = summary_meta.get("source_hash", "")
        source_file = summary_meta.get("source_file", "")
        source_type = summary_meta.get("source_type", "external")

        # Defensive: this entrypoint is only for chat-derived; callers must
        # not invoke it for external content.
        if source_type != "chat":
            return {
                "status": "error",
                "title": title,
                "reason": (
                    f"compile_chat_synthesis called on non-chat summary "
                    f"(source_type={source_type!r}). Use compile_one."
                ),
            }

        # Validate required sections: synthesis IS the compiled truth, so
        # the user-approved body must already contain them.
        issues = validate_compiled_truth(summary_body)
        if issues:
            return {
                "status": "insufficient",
                "title": title,
                "reason": (
                    "Chat synthesis missing required sections "
                    f"({', '.join(issues)}). Edit the note to include "
                    "## Overview and ## Key Concepts before promoting."
                ),
            }

        category = await self.categorizer.categorize(
            title=title,
            body=summary_body,
            vault_path=self.vault_path,
        )

        all_articles = self.wiki.list_articles()
        existing_match = await find_existing_article(
            summary_title=title,
            summary_preview=summary_body[:400],
            category=category,
            articles=all_articles,
            inference=self.inference,
        )

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        date_str = now[:10]
        banner = make_chat_banner(date_str)

        if existing_match:
            return await self.merge_chat_synthesis_into_existing(
                new_synthesis=summary_body,
                new_title=title,
                existing_article_path=existing_match["path"],
                source_url=source_url,
                source_hash=source_hash,
                source_file=source_file,
                summary_path=summary_path,
            )

        # First compile: synthesis IS the compiled truth, banner prepended.
        compiled_truth = f"{banner}\n\n{summary_body.strip()}\n"

        article = Article(
            meta={
                "title": title,
                "created": now,
                "updated": now,
                "compiled_at": now,
                "status": "compiled",
                "sources": [],
            },
            compiled_truth=compiled_truth,
            timeline=[],
        )

        article = append_timeline_entry(
            article=article,
            source_url=source_url,
            source_hash=source_hash,
            source_file=source_file,
            source_type="chat",
            summary=summary_body.strip(),
        )

        # Slug from title (mirrors compile_one).
        slug_source = _clip_title_for_slug(title)
        slug = slug_source.lower().replace("_", "-").replace(" ", "-")
        slug = "".join(c for c in slug if c.isalnum() or c == "-").strip("-") or "untitled"
        if len(slug.encode("utf-8")) > MAX_SLUG_BYTES:
            h = hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
            truncated = (
                slug.encode("utf-8")[: MAX_SLUG_BYTES - 9]
                .decode("utf-8", errors="ignore")
                .rstrip("-")
            )
            slug = f"{truncated}-{h}"
        target_dir = self.vault_path / category
        target_dir.mkdir(parents=True, exist_ok=True)
        article_path_rel = f"{category}/{slug}.md"
        article_full_path = target_dir / f"{slug}.md"

        article_full_path.write_text(serialize_article(article))
        logger.info("Compiled (chat) %s -> %s", summary_path, article_path_rel)

        self.wiki.rebuild_index()
        self.wiki.git_init()
        self.wiki.git_commit(f"compile: {title}")

        source_raw = summary_meta.get("source_raw", "")
        archive_raw_files(self.vault_path, raw_path=source_raw, summary_path=summary_path)
        self.wiki.git_commit(f"archive: {title}")

        outcome = {
            "status": "ok",
            "title": title,
            "article_path_rel": article_path_rel,
            "compiled_truth": compiled_truth.strip(),
        }
        if self.retrieval is not None:
            absolute_target = str((self.vault_path / article_path_rel).resolve())
            outcome["reindex"] = await self.retrieval.trigger_reindex(paths=[absolute_target])
        return outcome
```

- [ ] **Step 5: Run tests, verify they pass**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_compile_chat_synthesis.py -v
```
Expected: `test_compile_chat_synthesis_writes_article_with_banner`, `test_compile_chat_synthesis_returns_insufficient_when_sections_missing`, and `test_make_chat_banner_format` all pass. (The merge test added next phase will also need to compile.)

- [ ] **Step 6: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/compiler.py tests/test_compile_chat_synthesis.py && git commit -m "$(cat <<'EOF'
feat(compiler): add compile_chat_synthesis (no LLM re-extraction)

New entrypoint for chat-derived promotion. Treats user-approved
synthesis as compiled truth directly, prepends a sentinel banner,
validates required sections, propagates source_type=chat through
the article. Topic-match merge branch added in next task.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 6: Add `Compiler.merge_chat_synthesis_into_existing` with banner preservation

**Files:**
- Modify: `pal/compiler.py`
- Test: `tests/test_compile_chat_synthesis.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_compile_chat_synthesis.py`:

```python
@pytest.mark.asyncio
async def test_merge_chat_synthesis_preserves_banner(tmp_path):
    """When chat synthesis topic-matches an existing chat-derived article,
    the merged article must still begin with the chat banner sentinel."""
    # Pre-seed an existing chat-derived article.
    cat_dir = tmp_path / "Software-Development"
    cat_dir.mkdir(parents=True)
    existing_article_text = (
        "---\n"
        "title: \"Vibe-coding comprehension strategies\"\n"
        "sources: []\n"
        "---\n"
        f"{make_chat_banner('2026-05-09')}\n\n"
        "## Overview\nOriginal overview.\n\n"
        "## Key Concepts\n- existing point\n\n"
        "<!-- TIMELINE -->\n"
    )
    existing_path = cat_dir / "vibe-coding-comprehension-strategies.md"
    existing_path.write_text(existing_article_text)

    # New synthesis on the same topic.
    body = "## Overview\nUpdated overview.\n\n## Key Concepts\n- new point\n"
    summary_rel = _write_summary(tmp_path, "vibe-coding-comprehension-strategies", body)

    class MatchingWiki(FakeWiki):
        def list_articles(self):
            return [{"path": "Software-Development/vibe-coding-comprehension-strategies.md",
                     "title": "Vibe-coding comprehension strategies"}]

    compiler = Compiler(
        vault_path=tmp_path,
        wiki=MatchingWiki(),
        inference=FakeInference(),
        categorizer=FakeCategorizer(),
        prompt_builder=FakePromptBuilder(),
    )

    # Force find_existing_article to return our seeded article by
    # monkey-patching the inference-based matcher; for this test the
    # MatchingWiki single result and a deterministic match is enough
    # if we patch find_existing_article. Simpler: patch on module.
    import pal.article
    async def fake_find(**kwargs):
        return {"path": "Software-Development/vibe-coding-comprehension-strategies.md"}
    monkeypatched = pal.article.find_existing_article
    pal.article.find_existing_article = fake_find
    # Compiler imports it at top of module, so patch there too.
    import pal.compiler
    pal.compiler.find_existing_article = fake_find
    try:
        result = await compiler.compile_chat_synthesis(summary_rel)
    finally:
        pal.article.find_existing_article = monkeypatched
        pal.compiler.find_existing_article = monkeypatched

    assert result["status"] == "merged"
    merged_text = existing_path.read_text()
    article = parse_article(merged_text)
    assert article.compiled_truth.lstrip().startswith(CHAT_BANNER_SENTINEL)
    # The new synthesis content must have landed in the article.
    assert "Updated overview." in article.compiled_truth or "new point" in article.compiled_truth
```

- [ ] **Step 2: Run test, verify it fails**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_compile_chat_synthesis.py::test_merge_chat_synthesis_preserves_banner -v
```
Expected: FAIL with `AttributeError: 'Compiler' object has no attribute 'merge_chat_synthesis_into_existing'`.

- [ ] **Step 3: Implement `merge_chat_synthesis_into_existing`**

In `pal/compiler.py`, inside `Compiler` (place after `compile_chat_synthesis`, before `merge_into_existing`):

```python
    async def merge_chat_synthesis_into_existing(
        self,
        new_synthesis: str,
        new_title: str,
        existing_article_path: str,
        source_url: str,
        source_hash: str,
        source_file: str,
        summary_path: str,
    ) -> dict[str, Any]:
        """Merge a chat synthesis into an existing article.

        Banner preservation is load-bearing. The merged compiled_truth
        must still begin with CHAT_BANNER_SENTINEL so the system prompt's
        banner-reaction rule continues to trigger.

        Strategy: keep the existing article's banner (or generate one if
        absent), then replace the rest of the compiled_truth with the new
        synthesis. The previous synthesis is preserved in the timeline.
        """
        existing_text = (self.vault_path / existing_article_path).read_text()
        existing_article = parse_article(existing_text)

        existing_truth = existing_article.compiled_truth.lstrip()
        if existing_truth.startswith(CHAT_BANNER_SENTINEL):
            # Extract existing banner line (first paragraph).
            banner_end = existing_truth.find("\n\n")
            existing_banner = (
                existing_truth[:banner_end] if banner_end != -1 else existing_truth
            )
        else:
            # Existing article is external-source; banner-ify it because the
            # merged content is now chat-derived and the trust signal must
            # surface to the reader.
            now_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            existing_banner = make_chat_banner(now_date)

        compiled_truth = f"{existing_banner}\n\n{new_synthesis.strip()}\n"

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        article = Article(
            meta=dict(existing_article.meta),
            compiled_truth=compiled_truth,
            timeline=list(existing_article.timeline),
        )
        article.meta["updated"] = now
        article.meta["compiled_at"] = now

        article = append_timeline_entry(
            article=article,
            source_url=source_url,
            source_hash=source_hash,
            source_file=source_file,
            source_type="chat",
            summary=new_synthesis.strip(),
        )

        article_full_path = self.vault_path / existing_article_path
        article_full_path.write_text(serialize_article(article))
        logger.info("Merged (chat) -> %s", existing_article_path)

        self.wiki.rebuild_index()
        self.wiki.git_init()
        self.wiki.git_commit(f"compile: {new_title}")

        source_raw_meta = ""  # caller can pass via summary_meta if needed
        # Read summary frontmatter once more to find source_raw for archive
        summary_full = self.vault_path / summary_path
        if summary_full.exists():
            sm, _ = parse_frontmatter(summary_full.read_text())
            source_raw_meta = sm.get("source_raw", "")
        archive_raw_files(
            self.vault_path,
            raw_path=source_raw_meta,
            summary_path=summary_path,
        )
        self.wiki.git_commit(f"archive: {new_title}")

        outcome = {
            "status": "merged",
            "title": new_title,
            "article_path_rel": existing_article_path,
            "compiled_truth": compiled_truth.strip(),
        }
        if self.retrieval is not None:
            absolute_target = str((self.vault_path / existing_article_path).resolve())
            outcome["reindex"] = await self.retrieval.trigger_reindex(paths=[absolute_target])
        return outcome
```

- [ ] **Step 4: Run test, verify it passes**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_compile_chat_synthesis.py -v
```
Expected: all tests pass, including the merge banner-preservation test.

- [ ] **Step 5: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/compiler.py tests/test_compile_chat_synthesis.py && git commit -m "$(cat <<'EOF'
feat(compiler): merge_chat_synthesis_into_existing preserves banner

When chat synthesis topic-matches an existing article, merge while
preserving (or adding) the chat banner sentinel. Trust signal survives
the merge path, addressing the panel review's load-bearing concern.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 4: Tool, protocol, wiring

### Task 7: Add `PromoteSynthesisProposalMessage` to `pal/protocol.py`

**Files:**
- Modify: `pal/protocol.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_promote_synthesis_protocol.py`:

```python
from pal.protocol import PromoteSynthesisProposalMessage, MESSAGE_REGISTRY


def test_promote_synthesis_proposal_message_fields():
    msg = PromoteSynthesisProposalMessage(
        proposal_id="abc",
        title="Vibe-coding",
        rationale="user asked",
        note_path="raw/notes/vibe-coding.md",
        note_body_preview="## Overview\nfoo...",
    )
    assert msg.type == "promote_synthesis_proposal"
    assert msg.proposal_id == "abc"
    assert msg.note_path == "raw/notes/vibe-coding.md"


def test_promote_synthesis_proposal_message_registered():
    assert "promote_synthesis_proposal" in MESSAGE_REGISTRY
```

- [ ] **Step 2: Run test, verify it fails**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_promote_synthesis_protocol.py -v
```
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Add the message class to `pal/protocol.py`**

In `pal/protocol.py`, after `PromoteProposalMessage` (around line 99), add:

```python
@register_message
@dataclass
class PromoteSynthesisProposalMessage:
    """Daemon → client: a chat-derived synthesis is proposed for promotion to a wiki article."""
    proposal_id: str
    title: str
    rationale: str
    note_path: str
    note_body_preview: str
    type: str = "promote_synthesis_proposal"
```

In the union type alias at line 151 (`| PromoteProposalMessage`), add:

```python
    | PromoteSynthesisProposalMessage
```

- [ ] **Step 4: Run test, verify it passes**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_promote_synthesis_protocol.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/protocol.py tests/test_promote_synthesis_protocol.py && git commit -m "$(cat <<'EOF'
feat(protocol): add PromoteSynthesisProposalMessage

Carries proposal_id, title, rationale, note_path, and a body preview
from daemon to client (Discord/CLI) for the chat-derived promotion
approval prompt.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 8: Implement `PromoteSynthesisProposal` tool

**Files:**
- Create: `pal/tools/promote_synthesis.py`
- Test: `tests/test_promote_synthesis_tool.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_promote_synthesis_tool.py`:

```python
"""Tests for PromoteSynthesisProposal tool."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_core.approval_registry import ApprovalRegistry
from pal.tools.promote_synthesis import PromoteSynthesisProposal


def _make_ctx(tmp_path: Path, ar: ApprovalRegistry, compiler):
    ctx = MagicMock()
    ctx.agent.approval_registry = ar
    ctx.agent.compiler = compiler
    ctx.agent.config.vault_path = tmp_path
    ctx.emit = AsyncMock()
    return ctx


@pytest.mark.asyncio
async def test_promote_synthesis_rejects_missing_note(tmp_path):
    ctx = _make_ctx(tmp_path, ApprovalRegistry(), MagicMock())
    tool = PromoteSynthesisProposal()
    result_str = await tool.run(
        {"title": "x", "rationale": "y", "note_path": "raw/notes/missing.md"},
        ctx,
    )
    result = json.loads(result_str) if result_str.startswith("{") else result_str
    assert "note_not_found" in result_str or "not found" in result_str.lower()


@pytest.mark.asyncio
async def test_promote_synthesis_rejects_path_traversal(tmp_path):
    (tmp_path / "raw" / "notes").mkdir(parents=True)
    ctx = _make_ctx(tmp_path, ApprovalRegistry(), MagicMock())
    tool = PromoteSynthesisProposal()
    result_str = await tool.run(
        {"title": "x", "rationale": "y", "note_path": "../../etc/passwd"},
        ctx,
    )
    assert "invalid" in result_str.lower() or "note_not_found" in result_str


@pytest.mark.asyncio
async def test_promote_synthesis_creates_proposal_and_emits_message(tmp_path):
    notes = tmp_path / "raw" / "notes"
    notes.mkdir(parents=True)
    (notes / "vibe.md").write_text("## Overview\nfoo\n## Key Concepts\nbar\n")

    compiler = MagicMock()
    compiler.compile_chat_synthesis = AsyncMock(return_value={
        "status": "ok",
        "title": "Vibe",
        "article_path_rel": "Software-Development/vibe.md",
    })

    ar = ApprovalRegistry()
    ctx = _make_ctx(tmp_path, ar, compiler)

    tool = PromoteSynthesisProposal()

    async def auto_approve():
        await asyncio.sleep(0.05)
        proposals = list(ar._proposals.values())
        if proposals:
            ar.approve(proposals[0].proposal_id)

    asyncio.create_task(auto_approve())
    result_str = await tool.run(
        {"title": "Vibe", "rationale": "user asked", "note_path": "raw/notes/vibe.md"},
        ctx,
    )
    result = json.loads(result_str)
    assert result["status"] == "ok"
    assert result["article_path_rel"] == "Software-Development/vibe.md"

    ctx.emit.assert_awaited()
    compiler.compile_chat_synthesis.assert_awaited_once()


@pytest.mark.asyncio
async def test_promote_synthesis_declined_returns_status(tmp_path):
    notes = tmp_path / "raw" / "notes"
    notes.mkdir(parents=True)
    (notes / "vibe.md").write_text("## Overview\nfoo\n## Key Concepts\nbar\n")

    compiler = MagicMock()
    compiler.compile_chat_synthesis = AsyncMock()

    ar = ApprovalRegistry()
    ctx = _make_ctx(tmp_path, ar, compiler)

    tool = PromoteSynthesisProposal()

    async def auto_decline():
        await asyncio.sleep(0.05)
        proposals = list(ar._proposals.values())
        if proposals:
            ar.decline(proposals[0].proposal_id)

    asyncio.create_task(auto_decline())
    result_str = await tool.run(
        {"title": "Vibe", "rationale": "user asked", "note_path": "raw/notes/vibe.md"},
        ctx,
    )
    result = json.loads(result_str)
    assert result["status"] == "declined"
    compiler.compile_chat_synthesis.assert_not_awaited()
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_promote_synthesis_tool.py -v
```
Expected: FAIL with `ImportError: cannot import name 'PromoteSynthesisProposal'`.

- [ ] **Step 3: Create `pal/tools/promote_synthesis.py`**

```python
"""PAL chat-derived promotion tool: propose_promote_synthesis."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from agent_core.tools.base import Tool

if TYPE_CHECKING:
    from agent_core.agent import HandlerContext


_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def _slugify(title: str) -> str:
    s = title.lower().replace("_", "-").replace(" ", "-")
    s = _SLUG_RE.sub("", s).strip("-")
    return s or "untitled"


class PromoteSynthesisProposal(Tool):
    name = "propose_promote_synthesis"
    description = (
        "Propose promoting a chat-derived synthesis note (or an existing "
        "orphan note in raw/notes/) into a wiki article. The note body "
        "becomes the compiled truth directly (no LLM re-extraction). "
        "Required: a synthesis note already at raw/notes/<slug>.md "
        "containing ## Overview and ## Key Concepts sections. Blocks for "
        "user approval; on approval, writes a chat-derived summary and "
        "invokes the chat-aware compile path. Source path must be a file "
        "directly under raw/notes/."
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Proposed article title.",
            },
            "rationale": {
                "type": "string",
                "description": "One-line reason shown to the user in the approval prompt.",
            },
            "note_path": {
                "type": "string",
                "description": "Path to synthesis note under raw/notes/ (e.g. 'raw/notes/foo.md').",
            },
        },
        "required": ["title", "rationale", "note_path"],
    }
    requires = ("approval_registry", "compiler")

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        from pal.protocol import PromoteSynthesisProposalMessage

        if ctx.agent.approval_registry is None or ctx.agent.compiler is None:
            return "Error: promote_synthesis is not available in this session."

        title = (args.get("title") or "").strip()
        rationale = (args.get("rationale") or "").strip()
        note_path = (args.get("note_path") or "").strip()

        if not title:
            return "Error: 'title' parameter is required."
        if not rationale:
            return "Error: 'rationale' parameter is required."
        if not note_path:
            return "Error: 'note_path' parameter is required."

        # Path discipline: must be under raw/notes/, no traversal.
        if ".." in note_path.split("/") or note_path.startswith("/"):
            return json.dumps({"status": "invalid_path", "reason": f"Invalid note_path: {note_path}"})
        if not note_path.startswith("raw/notes/"):
            return json.dumps({
                "status": "invalid_path",
                "reason": "note_path must be under raw/notes/",
            })

        vault_path = ctx.agent.config.vault_path
        full_note = vault_path / note_path
        if not full_note.exists() or not full_note.is_file():
            return json.dumps({"status": "note_not_found", "reason": f"No file at {note_path}"})

        # Resolved-path boundary check.
        try:
            resolved = full_note.resolve()
            if not str(resolved).startswith(str(vault_path.resolve()) + "/"):
                return json.dumps({"status": "invalid_path", "reason": "note_path escapes vault"})
        except Exception:
            return json.dumps({"status": "invalid_path", "reason": "note_path resolution failed"})

        note_body = full_note.read_text()
        slug = _slugify(title)
        body_preview = note_body[:600]

        ar = ctx.agent.approval_registry
        try:
            proposal_id = ar.create_proposal(
                kind="promote_synthesis",
                rationale=rationale,
                note_path=note_path,
                target_title=title,
                slug=slug,
            )
        except ValueError as exc:
            return json.dumps({"status": "error", "reason": str(exc)})

        proposal = ar.get(proposal_id)
        await ctx.emit(
            PromoteSynthesisProposalMessage(
                proposal_id=proposal_id,
                title=title,
                rationale=rationale,
                note_path=note_path,
                note_body_preview=body_preview,
            )
        )

        remaining = (proposal.expires_at - datetime.now(timezone.utc)).total_seconds()
        try:
            await asyncio.wait_for(proposal.event.wait(), timeout=max(0.1, remaining))
        except asyncio.TimeoutError:
            ar.expire_stale()

        final = ar.get(proposal_id)
        if final.status != "approved":
            return json.dumps({"status": final.status, "title": title})

        ar.consume(proposal_id)

        # Write the chat-derived summary file.
        note_hash = hashlib.sha1(note_body.encode("utf-8")).hexdigest()
        summary_rel = f"raw/summaries/{slug}.md"
        summary_full = vault_path / summary_rel
        if summary_full.exists():
            return json.dumps({
                "status": "summary_collision",
                "reason": f"raw/summaries/{slug}.md already exists; pick a different title",
            })
        summary_full.parent.mkdir(parents=True, exist_ok=True)

        summary_text = (
            "---\n"
            f"title: \"{title}\"\n"
            f"source_file: \"{note_path}\"\n"
            "source_url: \"\"\n"
            "source_type: chat\n"
            f"source_hash: \"{note_hash}\"\n"
            f"source_raw: \"{note_path}\"\n"
            "---\n"
            f"{note_body}"
        )
        summary_full.write_text(summary_text)

        try:
            outcome = await ctx.agent.compiler.compile_chat_synthesis(summary_rel)
        except Exception as exc:
            outcome = {
                "status": "error",
                "title": title,
                "reason": f"{type(exc).__name__}: {exc}",
            }

        outcome["_note"] = (
            "Trust article_path_rel; if vault_exists is false (when set), the "
            "file was not written to disk."
        )
        article_rel = outcome.get("article_path_rel")
        if article_rel:
            outcome["vault_exists"] = (vault_path / article_rel).exists()
        return json.dumps(outcome)
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_promote_synthesis_tool.py -v
```
Expected: all four tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/tools/promote_synthesis.py tests/test_promote_synthesis_tool.py && git commit -m "$(cat <<'EOF'
feat(tools): add propose_promote_synthesis tool

Single tool handling both forward chat promotion and orphan-note backfill
via a note_path parameter. Validates path discipline, creates the
approval proposal, blocks for approval, writes the chat-derived summary,
and invokes Compiler.compile_chat_synthesis on approval.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 9: Register the tool and wire approval handlers

**Files:**
- Modify: `pal/agent.py` (or wherever tools are registered; mirror commit `2da344d`)
- Modify: `pal/discord_interactions.py` (add handler mirroring `_handle_promote_proposal` at line 654-655)
- Modify: `pal/cli.py` (add CLI approval prompt for the new message)

- [ ] **Step 1: Locate the tool registry**

```bash
cd /home/edible/Projects/PAL && git show 2da344d --stat
git show 2da344d -- pal/agent.py
```
Expected: shows the diff that registered `delete_file` and `replace_in_file`. Note the file and the registration pattern.

- [ ] **Step 2: Register `PromoteSynthesisProposal` following the same pattern**

In the same file/section that registers other tools (typically `pal/agent.py`), add an import:

```python
from pal.tools.promote_synthesis import PromoteSynthesisProposal
```

And register the tool instance in the same dict/list/registry the existing tools use (mirror the lines added in commit `2da344d`).

- [ ] **Step 3: Add Discord handler in `pal/discord_interactions.py`**

Mirror the existing `_handle_promote_proposal` (line 654) by adding a `_handle_promote_synthesis_proposal` that builds an embed showing title, rationale, note_path, and the body preview. The approval/decline buttons reuse the same `ar.approve(proposal_id)` / `ar.decline(proposal_id)` callbacks the existing handlers use.

Add to the message dispatch (mirror `if isinstance(msg, PromoteProposalMessage):` at line 596):

```python
            elif isinstance(msg, PromoteSynthesisProposalMessage):
                await self._handle_promote_synthesis_proposal(msg)
```

Add the handler method (mirror `_handle_promote_proposal` at line 654-655):

```python
    async def _handle_promote_synthesis_proposal(self, msg: PromoteSynthesisProposalMessage) -> None:
        embed, view = build_promote_synthesis_proposal_embed(msg)
        await self._post_proposal_embed(embed, view, msg.proposal_id)
```

Add the embed builder near `build_promote_proposal_embed` (around line 299):

```python
def build_promote_synthesis_proposal_embed(msg: PromoteSynthesisProposalMessage):
    embed = discord.Embed(
        title=f"Promote chat synthesis → wiki: {msg.title}",
        description=msg.rationale,
        color=discord.Color.gold(),
    )
    embed.add_field(name="Note", value=msg.note_path, inline=False)
    preview = msg.note_body_preview
    if len(preview) > 1000:
        preview = preview[:1000] + "…"
    embed.add_field(name="Preview", value=f"```{preview}```", inline=False)
    view = ProposalApprovalView(msg.proposal_id)
    return embed, view
```

(Use the exact `ProposalApprovalView` or equivalent class the existing `build_promote_proposal_embed` returns; read that function to copy the pattern.)

- [ ] **Step 4: Add CLI handler in `pal/cli.py`**

Locate the CLI dispatch where `PromoteProposalMessage` (or `ConsolidateProposalMessage`) is handled. Add a parallel handler for `PromoteSynthesisProposalMessage` that prints the title, rationale, note path, and body preview, then prompts for approve/decline/edit-title.

- [ ] **Step 5: Smoke test (manual)**

Run the CLI:
```bash
cd /home/edible/Projects/PAL && .venv/bin/python -m pal.cli --vault-path /tmp/test-vault
```

Inside the CLI, ask PAL to promote a synthesis (after first writing a test note via the prompt). Verify:
- Tool is registered (no "unknown tool" error)
- Approval prompt appears
- Approving runs the compile path
- Article appears in the test vault

- [ ] **Step 6: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/agent.py pal/discord_interactions.py pal/cli.py && git commit -m "$(cat <<'EOF'
feat(agent): register propose_promote_synthesis; wire Discord + CLI handlers

Tool registration follows the delete_file/replace_in_file pattern from
2da344d. Discord and CLI surfaces both render the approval prompt with
title, rationale, note path, and body preview.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 5: System prompt addendums

### Task 10: Add nudge addendum and banner-reaction rule to system prompt

**Files:**
- Modify: `pal/prompts/system.py`
- Test: `tests/test_prompt_builder.py`

- [ ] **Step 1: Write failing tests**

The existing tests in `tests/test_prompt_builder.py` assert against a module-level constant `PAL_BASE_PROMPT` (not a builder instance). Mirror that pattern. Add:

```python
def test_base_prompt_mentions_promote_synthesis_tool():
    assert "propose_promote_synthesis" in PAL_BASE_PROMPT


def test_base_prompt_includes_chat_promotion_nudge():
    lower = PAL_BASE_PROMPT.lower()
    assert "promote this thread" in lower or "promote this chat" in lower
    assert "once per conversation" in lower


def test_base_prompt_includes_banner_reaction_rule():
    assert "chat-derived synthesis" in PAL_BASE_PROMPT
    lower = PAL_BASE_PROMPT.lower()
    assert "previous chat" in lower or "prior conversation" in lower
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_prompt_builder.py::test_system_prompt_includes_chat_promotion_nudge tests/test_prompt_builder.py::test_system_prompt_includes_banner_reaction_rule -v
```
Expected: FAIL.

- [ ] **Step 3: Add the two prompt additions in `pal/prompts/system.py`**

The prompt lives as a module-level string `PAL_BASE_PROMPT`. Locate it (around line 1-100) and add the two additions in the appropriate sections.

In the tool list section (find the line listing `compile_summary` around line 21), add a new bullet immediately after the compile-related entries:

```
- propose_promote_synthesis(title, rationale, note_path): promote a chat-derived synthesis note (or an existing orphan note in raw/notes/) into a wiki article. When a conversation has produced durable factual knowledge worth keeping, especially on a topic without an existing wiki article, you may suggest once per conversation: "Want me to promote this thread about <topic> into the wiki?" Do not call propose_promote_synthesis unprompted; wait for the user to say yes.
```

In the retrieval-usage section (find the area around line 92-98 that talks about `search_vault` usage), add a new bullet:

```
- When a retrieved article's body begins with `> _Source: chat-derived synthesis`, that article was synthesized from a prior conversation rather than external research. When citing or relying on it, briefly note this provenance to the user (e.g., "in a previous chat we discussed..."). Do not treat chat-derived articles as having the same evidentiary weight as articles compiled from external documents.
```

Both additions go directly into the `PAL_BASE_PROMPT` string literal.

- [ ] **Step 4: Run tests, verify they pass**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_prompt_builder.py -v
```
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/prompts/system.py tests/test_prompt_builder.py && git commit -m "$(cat <<'EOF'
feat(prompts): nudge for chat promotion + banner-reaction rule

System prompt now (a) tells PAL it may suggest promotion once per
conversation when the chat produced durable knowledge, and (b) tells
PAL to surface the chat-derived provenance when citing articles whose
body starts with the chat banner sentinel. The banner becomes a
behavioral hook rather than decoration.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 6: Integration tests

### Task 11: End-to-end forward promotion integration test

**Files:**
- Create: `tests/test_promote_synthesis_integration.py`

- [ ] **Step 1: Write the integration test**

Create `tests/test_promote_synthesis_integration.py`:

```python
"""End-to-end integration tests for chat-derived promotion."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_core.approval_registry import ApprovalRegistry
from pal.compiler import Compiler, CHAT_BANNER_SENTINEL
from pal.tools.promote_synthesis import PromoteSynthesisProposal
from pal.article import parse_article


class FakeWiki:
    def __init__(self, existing=None):
        self.existing = existing or []
        self.commits = []
    def list_articles(self):
        return list(self.existing)
    def rebuild_index(self):
        pass
    def git_init(self):
        pass
    def git_commit(self, msg):
        self.commits.append(msg)


class FakeCategorizer:
    async def categorize(self, **kwargs):
        return "Software-Development"


class FakePromptBuilder:
    def build(self):
        return "system prompt"


def _build_compiler(tmp_path, wiki=None):
    return Compiler(
        vault_path=tmp_path,
        wiki=wiki or FakeWiki(),
        inference=MagicMock(),
        categorizer=FakeCategorizer(),
        prompt_builder=FakePromptBuilder(),
    )


@pytest.mark.asyncio
async def test_forward_promotion_end_to_end(tmp_path):
    notes = tmp_path / "raw" / "notes"
    notes.mkdir(parents=True)
    note_body = "## Overview\nVibe-coding is a comprehension strategy.\n\n## Key Concepts\n- Read aloud\n- Rubber duck\n"
    (notes / "vibe-coding.md").write_text(note_body)

    compiler = _build_compiler(tmp_path)
    ar = ApprovalRegistry()

    ctx = MagicMock()
    ctx.agent.approval_registry = ar
    ctx.agent.compiler = compiler
    ctx.agent.config.vault_path = tmp_path
    ctx.emit = AsyncMock()

    tool = PromoteSynthesisProposal()

    async def auto_approve():
        await asyncio.sleep(0.05)
        for p in ar._proposals.values():
            if p.status == "pending":
                ar.approve(p.proposal_id)
                return

    asyncio.create_task(auto_approve())
    result_str = await tool.run(
        {
            "title": "Vibe-coding comprehension strategies",
            "rationale": "user asked",
            "note_path": "raw/notes/vibe-coding.md",
        },
        ctx,
    )
    result = json.loads(result_str)

    assert result["status"] == "ok"
    article_path = tmp_path / result["article_path_rel"]
    assert article_path.exists()
    article = parse_article(article_path.read_text())
    assert article.compiled_truth.lstrip().startswith(CHAT_BANNER_SENTINEL)
    assert article.meta["sources"][-1]["source_type"] == "chat"
    assert article.timeline[-1].source_type == "chat"
```

- [ ] **Step 2: Run test**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_promote_synthesis_integration.py::test_forward_promotion_end_to_end -v
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
cd /home/edible/Projects/PAL && git add tests/test_promote_synthesis_integration.py && git commit -m "$(cat <<'EOF'
test: end-to-end forward chat promotion integration

Verifies the full flow from tool invocation through approval, summary
write, compile_chat_synthesis, article on disk, banner present, and
source_type propagated to meta.sources and timeline entry.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 12: Backfill (orphan-note) integration test

**Files:**
- Modify: `tests/test_promote_synthesis_integration.py`

- [ ] **Step 1: Add the test**

Append to `tests/test_promote_synthesis_integration.py`:

```python
@pytest.mark.asyncio
async def test_backfill_orphan_note_end_to_end(tmp_path):
    """Backfill an orphan note created days ago (no special handling)."""
    notes = tmp_path / "raw" / "notes"
    notes.mkdir(parents=True)
    orphan = notes / "old-orphan-note.md"
    orphan.write_text("## Overview\nOld knowledge.\n\n## Key Concepts\n- a\n- b\n")

    compiler = _build_compiler(tmp_path)
    ar = ApprovalRegistry()

    ctx = MagicMock()
    ctx.agent.approval_registry = ar
    ctx.agent.compiler = compiler
    ctx.agent.config.vault_path = tmp_path
    ctx.emit = AsyncMock()

    tool = PromoteSynthesisProposal()

    async def auto_approve():
        await asyncio.sleep(0.05)
        for p in ar._proposals.values():
            if p.status == "pending":
                ar.approve(p.proposal_id)
                return

    asyncio.create_task(auto_approve())
    result_str = await tool.run(
        {
            "title": "Old knowledge",
            "rationale": "backfill orphan",
            "note_path": "raw/notes/old-orphan-note.md",
        },
        ctx,
    )
    result = json.loads(result_str)
    assert result["status"] == "ok"
    article_path = tmp_path / result["article_path_rel"]
    assert article_path.exists()
    article = parse_article(article_path.read_text())
    assert article.compiled_truth.lstrip().startswith(CHAT_BANNER_SENTINEL)
```

- [ ] **Step 2: Run test**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_promote_synthesis_integration.py::test_backfill_orphan_note_end_to_end -v
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
cd /home/edible/Projects/PAL && git add tests/test_promote_synthesis_integration.py && git commit -m "$(cat <<'EOF'
test: backfill orphan note via same promote_synthesis path

Verifies that an orphan raw/notes/ file follows the identical promotion
flow as a freshly-written synthesis note. No separate tool, no special
handling.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 13: Topic-match merge integration test (banner preservation)

**Files:**
- Modify: `tests/test_promote_synthesis_integration.py`

- [ ] **Step 1: Add the test**

Append to `tests/test_promote_synthesis_integration.py`:

```python
@pytest.mark.asyncio
async def test_topic_match_merge_preserves_banner(tmp_path, monkeypatch):
    """Promoting onto an existing chat-derived article must preserve the banner."""
    # Pre-seed an existing chat-derived article.
    cat_dir = tmp_path / "Software-Development"
    cat_dir.mkdir(parents=True)
    from pal.compiler import make_chat_banner
    existing_path = cat_dir / "vibe-coding.md"
    existing_path.write_text(
        "---\n"
        "title: \"Vibe-coding\"\n"
        "sources: []\n"
        "---\n"
        f"{make_chat_banner('2026-05-09')}\n\n"
        "## Overview\nOriginal.\n\n## Key Concepts\n- a\n\n"
        "<!-- TIMELINE -->\n"
    )

    notes = tmp_path / "raw" / "notes"
    notes.mkdir(parents=True)
    (notes / "vibe-coding-v2.md").write_text(
        "## Overview\nUpdated.\n\n## Key Concepts\n- a\n- b\n"
    )

    wiki = FakeWiki(existing=[
        {"path": "Software-Development/vibe-coding.md", "title": "Vibe-coding"},
    ])
    compiler = _build_compiler(tmp_path, wiki=wiki)

    # Force topic match.
    async def fake_find(**kwargs):
        return {"path": "Software-Development/vibe-coding.md"}
    monkeypatch.setattr("pal.compiler.find_existing_article", fake_find)

    ar = ApprovalRegistry()
    ctx = MagicMock()
    ctx.agent.approval_registry = ar
    ctx.agent.compiler = compiler
    ctx.agent.config.vault_path = tmp_path
    ctx.emit = AsyncMock()

    tool = PromoteSynthesisProposal()

    async def auto_approve():
        await asyncio.sleep(0.05)
        for p in ar._proposals.values():
            if p.status == "pending":
                ar.approve(p.proposal_id)
                return

    asyncio.create_task(auto_approve())
    result_str = await tool.run(
        {
            "title": "Vibe-coding v2",
            "rationale": "extend with new concept",
            "note_path": "raw/notes/vibe-coding-v2.md",
        },
        ctx,
    )
    result = json.loads(result_str)
    assert result["status"] == "merged"

    merged = parse_article(existing_path.read_text())
    assert merged.compiled_truth.lstrip().startswith(CHAT_BANNER_SENTINEL)
    # New synthesis content present.
    assert "Updated." in merged.compiled_truth or "- b" in merged.compiled_truth
```

- [ ] **Step 2: Run test**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_promote_synthesis_integration.py::test_topic_match_merge_preserves_banner -v
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
cd /home/edible/Projects/PAL && git add tests/test_promote_synthesis_integration.py && git commit -m "$(cat <<'EOF'
test: topic-match merge preserves chat banner

Locks in the load-bearing invariant from the spec's panel review:
merging chat synthesis into an existing chat-derived article must keep
the banner sentinel at the top of compiled_truth.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 14: Regression sanity check on existing compile path

**Files:**
- Run existing tests, fix any regressions surfaced.

- [ ] **Step 1: Run the full PAL test suite**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/ -v
```
Expected: all tests pass. The only legitimate regressions should be the 5 stale `pal.client` import collection failures already documented in memory (`project_pal_client_test_cleanup`); those are pre-existing and out of scope.

- [ ] **Step 2: If any non-preexisting test fails, diagnose and fix**

For each new failure: read the test, identify whether it's a real regression in this work or a stale test that needs an update. Fix at the right layer. Do not skip or weaken tests to make them green.

- [ ] **Step 3: Run agent_core tests one more time too**

```bash
cd /home/edible/Projects/agent_core && pytest tests/ -v
```
Expected: all pass.

- [ ] **Step 4: Final commit (only if anything was fixed)**

```bash
cd /home/edible/Projects/PAL && git add -- <only the specific files you touched> && git commit -m "fix(<area>): <what you fixed and why>

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
"
```

---

## Self-review checklist (run before declaring the plan complete)

- [ ] Every task has a Files section with exact paths.
- [ ] Every test step shows the assertion code.
- [ ] Every implementation step shows the actual code change, not a description.
- [ ] No "TBD", "TODO", "implement later" anywhere in the plan.
- [ ] All function signatures used in later tasks match those defined in earlier tasks (`compile_chat_synthesis`, `merge_chat_synthesis_into_existing`, `make_chat_banner`, `CHAT_BANNER_SENTINEL`, `PromoteSynthesisProposalMessage`, `_slugify`).
- [ ] Cross-repo dependency on agent_core is called out at the top and Phase 1 verifies the import.
- [ ] Banner-preservation invariant (load-bearing per panel review) has at least one explicit test.
- [ ] Timeline parser round-trip (load-bearing per realist review) has at least one explicit test.
- [ ] Companion system prompt rule for the banner has a test.
- [ ] Forward, backfill, and merge integration tests all exist.

## Out of scope for this plan

- Render-time banner from `meta.sources[].source_type` (deferred to the prompt + tool audit pass).
- `search_vault` exact-path return and batch-error "did you mean" suggestions (separate workstream per `project_pal_path_determinism` memory).
- `raw/conversations/` snapshot machinery (deferred to v2 if transcript audit becomes load-bearing).
- Migration script for existing articles (default `source_type: external` handles back-compat).
