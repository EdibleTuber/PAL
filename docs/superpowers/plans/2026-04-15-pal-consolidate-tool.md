# PAL Consolidate Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `propose_consolidate` / `consolidate` tool pair so PAL can fuse 2+ existing wiki articles into a new grounded article, closing the gap that caused PAL to fabricate "manually synthesized" articles when `compile_batch` refused already-promoted paths.

**Architecture:** Mirror the existing compile flow. A new `Consolidator` class in `pal/consolidator.py` parallels `Compiler.compile_one` but takes N source article paths and a user-specified target path, runs a grounded inference prompt that requires inline citations, and writes a single new article. A new `ConsolidateProposalMessage` protocol type, a `"consolidate"` kind in `ApprovalRegistry`, CLI + Discord renderings, and the tool handlers in `pal/tools.py` complete the consent-gated pathway. Cleanup of source files is explicitly out of scope — callers can follow up with `propose_reorg` move ops to archive sources.

**Tech Stack:** Python 3.11+, asyncio, dataclasses, httpx (existing `InferenceClient`), discord.py, pytest.

---

## File Structure

**Create:**
- `pal/consolidator.py` — `Consolidator` class. Reads N source articles, runs grounded inference, writes a new article with proper frontmatter. Mirrors `pal/compiler.py` in shape.
- `tests/test_consolidator.py` — unit tests for validation, inference path, article write, and error branches.

**Modify:**
- `pal/protocol.py` — add `ConsolidateProposalMessage`, register it in `_MESSAGE_TYPES` and the `Message` union.
- `pal/approval_registry.py` — extend `ProposalKind` with `"consolidate"`; add `target_path` and `target_title` fields to `Proposal`; extend `create_proposal` validation; extend `edit` to pass the new fields through.
- `pal/tools.py` — add two tool specs (`propose_consolidate`, `consolidate`) to the `TOOLS` list, two handlers (`_propose_consolidate`, `_consolidate`) on `ToolExecutor`, a `consolidator` constructor arg, and register both handlers in `run_async`.
- `pal/daemon.py` — construct a `Consolidator` after the `Compiler` is built and pass it into `ToolExecutor`.
- `pal/cli.py` — add `format_consolidate_proposal`; route `ConsolidateProposalMessage` through the existing proposal render dispatch.
- `pal/discord_interactions.py` — add `build_consolidate_proposal_embed`, extend `parse_button_custom_id` / `parse_modal_custom_id` to accept `"consolidate"`, dispatch the message in `_handle_proposal_message` (or its moral equivalent).
- `pal/prompt_builder.py` — list `consolidate` in the tool inventory; remove the "no consolidate tool exists" caveat added in the preceding hardening pass.
- `tests/test_protocol.py` — round-trip test for the new message.
- `tests/test_approval_registry.py` — cover the new kind and field passthrough.
- `tests/test_tools.py` — cover the two new handlers.

---

## Task 1: Protocol message type

**Files:**
- Modify: `pal/protocol.py`
- Test: `tests/test_protocol.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_protocol.py`:

```python
def test_consolidate_proposal_roundtrip():
    from pal.protocol import ConsolidateProposalMessage, encode_message, decode_message
    msg = ConsolidateProposalMessage(
        proposal_id="abc-123",
        source_paths=["Security/a.md", "Security/b.md"],
        target_path="Security/Combined.md",
        target_title="Combined",
        rationale="Merge overlapping notes",
    )
    encoded = encode_message(msg)
    decoded = decode_message(encoded.strip())
    assert decoded == msg
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_protocol.py::test_consolidate_proposal_roundtrip -v`
Expected: FAIL with `ImportError` on `ConsolidateProposalMessage`.

- [ ] **Step 3: Add the message class**

In `pal/protocol.py`, after the `ReorgProposalMessage` dataclass block, add:

```python
@dataclass
class ConsolidateProposalMessage:
    proposal_id: str
    source_paths: list[str]
    target_path: str
    target_title: str
    rationale: str
    type: str = "consolidate_proposal"
```

In the same file, extend `_MESSAGE_TYPES`:

```python
_MESSAGE_TYPES: dict[str, type] = {
    "chat": ChatMessage,
    "command": CommandMessage,
    "stream_chunk": StreamChunkMessage,
    "response": ResponseMessage,
    "error": ErrorMessage,
    "tool_progress": ToolProgressMessage,
    "research_proposal": ResearchProposalMessage,
    "research_approval_response": ResearchApprovalResponseMessage,
    "compile_proposal": CompileProposalMessage,
    "reorg_proposal": ReorgProposalMessage,
    "consolidate_proposal": ConsolidateProposalMessage,
}
```

And extend the `Message` union alias:

```python
Message = (
    ChatMessage
    | CommandMessage
    | StreamChunkMessage
    | ResponseMessage
    | ErrorMessage
    | ToolProgressMessage
    | ResearchProposalMessage
    | ResearchApprovalResponseMessage
    | CompileProposalMessage
    | ReorgProposalMessage
    | ConsolidateProposalMessage
)
```

Also update the docstring at the top of `pal/protocol.py` to list `consolidate_proposal`.

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_protocol.py -v`
Expected: all tests pass including the new one.

- [ ] **Step 5: Commit**

```bash
git add pal/protocol.py tests/test_protocol.py
git commit -m "feat: ConsolidateProposalMessage protocol type"
```

---

## Task 2: Approval registry — consolidate kind

**Files:**
- Modify: `pal/approval_registry.py`
- Test: `tests/test_approval_registry.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_approval_registry.py`:

```python
def test_create_consolidate_proposal():
    reg = ApprovalRegistry()
    pid = reg.create_proposal(
        kind="consolidate",
        summary_paths=["Security/a.md", "Security/b.md"],
        target_path="Security/Combined.md",
        target_title="Combined",
        rationale="merge overlapping notes",
    )
    p = reg.get(pid)
    assert p.kind == "consolidate"
    assert p.summary_paths == ["Security/a.md", "Security/b.md"]
    assert p.target_path == "Security/Combined.md"
    assert p.target_title == "Combined"
    assert p.status == "pending"


def test_consolidate_requires_two_sources():
    import pytest
    reg = ApprovalRegistry()
    with pytest.raises(ValueError, match="at least two"):
        reg.create_proposal(
            kind="consolidate",
            summary_paths=["Security/a.md"],
            target_path="Security/Combined.md",
            target_title="Combined",
            rationale="r",
        )


def test_consolidate_requires_target():
    import pytest
    reg = ApprovalRegistry()
    with pytest.raises(ValueError, match="target_path"):
        reg.create_proposal(
            kind="consolidate",
            summary_paths=["Security/a.md", "Security/b.md"],
            target_path="",
            target_title="Combined",
            rationale="r",
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_approval_registry.py -v -k consolidate`
Expected: FAIL (kind rejected, fields don't exist).

- [ ] **Step 3: Extend `ProposalKind` and `Proposal`**

In `pal/approval_registry.py`:

Change:
```python
ProposalKind = Literal["research", "compile", "reorg"]
```
to:
```python
ProposalKind = Literal["research", "compile", "reorg", "consolidate"]
```

Extend the `Proposal` dataclass (add two fields before `event`):

```python
@dataclass
class Proposal:
    proposal_id: str
    topic: str
    depth: int
    rationale: str
    status: ProposalStatus
    created_at: datetime
    expires_at: datetime
    kind: ProposalKind = "research"
    successor_id: Optional[str] = None
    summary_paths: Optional[list[str]] = None
    operations: Optional[list[dict]] = None
    target_path: Optional[str] = None
    target_title: Optional[str] = None
    event: asyncio.Event = field(default_factory=asyncio.Event, repr=False, compare=False)
```

- [ ] **Step 4: Extend `create_proposal` validation + storage**

Replace the `create_proposal` method signature and body (keep the existing research/compile/reorg branches, add consolidate):

```python
    def create_proposal(
        self,
        *,
        kind: ProposalKind = "research",
        topic: str = "",
        depth: int = 3,
        rationale: str,
        summary_paths: Optional[list[str]] = None,
        operations: Optional[list[dict]] = None,
        target_path: Optional[str] = None,
        target_title: Optional[str] = None,
    ) -> str:
        if kind == "research" and not topic:
            raise ValueError("research proposals require a non-empty topic")
        if kind == "compile":
            if not summary_paths:
                raise ValueError("compile proposals require a non-empty summary_paths list")
        if kind == "reorg":
            if not operations:
                raise ValueError("reorg proposals require a non-empty operations list")
            for op in operations:
                if not isinstance(op, dict):
                    raise ValueError(f"each operation must be a dict, got {type(op).__name__}")
                if op.get("type") not in ("move", "merge"):
                    raise ValueError(f"operation type must be 'move' or 'merge', got {op.get('type')!r}")
                if not op.get("src") or not op.get("dst"):
                    raise ValueError("every operation requires 'src' and 'dst' fields")
        if kind == "consolidate":
            if not summary_paths or len(summary_paths) < 2:
                raise ValueError("consolidate proposals require at least two source paths")
            if not target_path:
                raise ValueError("consolidate proposals require target_path")
            if not target_title:
                raise ValueError("consolidate proposals require target_title")

        proposal_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        self._proposals[proposal_id] = Proposal(
            proposal_id=proposal_id,
            topic=topic,
            depth=depth,
            rationale=rationale,
            status="pending",
            created_at=now,
            expires_at=now + timedelta(minutes=self._expiry_minutes),
            kind=kind,
            summary_paths=list(summary_paths) if summary_paths else None,
            operations=[dict(op) for op in operations] if operations else None,
            target_path=target_path,
            target_title=target_title,
        )
        return proposal_id
```

- [ ] **Step 5: Extend `edit` to pass new fields through**

In the existing `edit` method, within the `Proposal(...)` construction for `new_proposal`, add two lines (preserving the old proposal's values on edit):

```python
            target_path=old.target_path,
            target_title=old.target_title,
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_approval_registry.py -v`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add pal/approval_registry.py tests/test_approval_registry.py
git commit -m "feat: consolidate proposal kind in approval registry"
```

---

## Task 3: Consolidator class — validation + error branches

**Files:**
- Create: `pal/consolidator.py`
- Test: `tests/test_consolidator.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_consolidator.py` with:

```python
import pytest
from pathlib import Path

from pal.consolidator import Consolidator


class _FakeInference:
    def __init__(self, response: str):
        self.response = response
        self.calls = []

    async def complete(self, messages, reasoning=None, tools=None, model=None):
        self.calls.append({"messages": list(messages), "reasoning": reasoning})
        class R:
            type = "text"
            content = self.response
            reasoning = ""
        return R()


class _FakeWiki:
    def __init__(self, vault_path: Path):
        self.vault_path = vault_path
        self.written = []

    def write_article(self, path, title, content, tags=None):
        full = self.vault_path / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        self.written.append({"path": path, "title": title})

    def git_commit(self, message: str) -> None:
        pass


class _StubPromptBuilder:
    def build(self) -> str:
        return "BASE"


def _make(tmp_path, inference_response="## Overview\n\ncontent"):
    inference = _FakeInference(inference_response)
    wiki = _FakeWiki(tmp_path)
    return Consolidator(
        vault_path=tmp_path,
        wiki=wiki,
        inference=inference,
        prompt_builder=_StubPromptBuilder(),
    ), inference, wiki


@pytest.mark.asyncio
async def test_rejects_target_outside_vault(tmp_path):
    c, _, _ = _make(tmp_path)
    out = await c.consolidate(
        source_paths=["a.md", "b.md"],
        target_path="../evil.md",
        target_title="Evil",
    )
    assert out["status"] == "invalid_path"


@pytest.mark.asyncio
async def test_rejects_target_in_raw(tmp_path):
    c, _, _ = _make(tmp_path)
    out = await c.consolidate(
        source_paths=["a.md", "b.md"],
        target_path="raw/notes/out.md",
        target_title="Out",
    )
    assert out["status"] == "invalid_path"
    assert "raw/" in out["reason"]


@pytest.mark.asyncio
async def test_rejects_system_target(tmp_path):
    c, _, _ = _make(tmp_path)
    out = await c.consolidate(
        source_paths=["a.md", "b.md"],
        target_path="_internal/out.md",
        target_title="Out",
    )
    assert out["status"] == "invalid_path"


@pytest.mark.asyncio
async def test_target_must_not_exist(tmp_path):
    (tmp_path / "Security").mkdir()
    (tmp_path / "Security" / "out.md").write_text("exists")
    (tmp_path / "Security" / "a.md").write_text("---\ntitle: A\n---\nA body")
    (tmp_path / "Security" / "b.md").write_text("---\ntitle: B\n---\nB body")
    c, _, _ = _make(tmp_path)
    out = await c.consolidate(
        source_paths=["Security/a.md", "Security/b.md"],
        target_path="Security/out.md",
        target_title="Out",
    )
    assert out["status"] == "invalid_path"
    assert "exists" in out["reason"]


@pytest.mark.asyncio
async def test_source_missing(tmp_path):
    (tmp_path / "Security").mkdir()
    (tmp_path / "Security" / "a.md").write_text("---\ntitle: A\n---\nA body")
    c, _, _ = _make(tmp_path)
    out = await c.consolidate(
        source_paths=["Security/a.md", "Security/missing.md"],
        target_path="Security/out.md",
        target_title="Out",
    )
    assert out["status"] == "not_found"
    assert "missing.md" in out["reason"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_consolidator.py -v`
Expected: `ImportError` / `ModuleNotFoundError` for `pal.consolidator`.

- [ ] **Step 3: Implement Consolidator skeleton with validation**

Create `pal/consolidator.py`:

```python
"""Consolidator -- synthesize one new grounded article from N existing ones.

Parallel to pal.compiler.Compiler, but with different topology:
  - Compiler: 1 raw summary -> 1 article (create or merge into existing).
  - Consolidator: N existing articles -> 1 new article at a caller-specified path.

Never merges into existing articles (target must not exist). Does not use
the categorizer (the caller names the target path). Source cleanup is
explicitly out of scope; callers use propose_reorg afterwards.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pal.frontmatter import parse_frontmatter

logger = logging.getLogger(__name__)


class Consolidator:
    def __init__(
        self,
        vault_path: Path,
        wiki,              # WikiManager
        inference,         # InferenceClient
        prompt_builder,    # SystemPromptBuilder
    ) -> None:
        self.vault_path = vault_path
        self.wiki = wiki
        self.inference = inference
        self.prompt_builder = prompt_builder

    async def consolidate(
        self,
        source_paths: list[str],
        target_path: str,
        target_title: str,
    ) -> dict[str, Any]:
        """Synthesize `target_path` from the content of `source_paths`.

        Returns a dict with keys: status, target_path, article_path_rel,
        vault_exists, reason? Statuses: ok, insufficient, invalid_path,
        not_found, error.
        """
        # Validate target
        bad = self._validate_target(target_path)
        if bad is not None:
            return {"status": "invalid_path", "target_path": target_path, "reason": bad, "vault_exists": False}

        target_full = self.vault_path / target_path
        if target_full.exists():
            return {
                "status": "invalid_path",
                "target_path": target_path,
                "reason": f"target already exists: {target_path}",
                "vault_exists": True,
            }

        # Validate each source exists
        source_bodies: list[tuple[str, str]] = []
        for src in source_paths:
            if ".." in src.split("/") or src.startswith("/"):
                return {
                    "status": "invalid_path",
                    "target_path": target_path,
                    "reason": f"invalid source path: {src}",
                    "vault_exists": False,
                }
            src_full = self.vault_path / src
            if not src_full.exists():
                return {
                    "status": "not_found",
                    "target_path": target_path,
                    "reason": f"source not found: {src}",
                    "vault_exists": False,
                }
            text = src_full.read_text()
            _meta, body = parse_frontmatter(text)
            source_bodies.append((src, body))

        # Subsequent steps wired in Task 4.
        return {
            "status": "error",
            "target_path": target_path,
            "reason": "inference pipeline not wired",
            "vault_exists": False,
        }

    def _validate_target(self, target_path: str) -> str | None:
        if not target_path:
            return "target_path is required"
        if target_path.startswith("/"):
            return f"absolute paths not allowed: {target_path}"
        parts = Path(target_path).parts
        if ".." in parts:
            return f"path traversal not allowed: {target_path}"
        if parts and parts[0].startswith("_"):
            return f"system directory not allowed: {target_path}"
        if target_path.startswith("raw/"):
            return f"raw/ is for unpromoted material; target must be a promoted category (got {target_path})"
        if not target_path.endswith(".md"):
            return f"target must be a .md file: {target_path}"
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_consolidator.py -v`
Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/consolidator.py tests/test_consolidator.py
git commit -m "feat: Consolidator skeleton with target+source validation"
```

---

## Task 4: Consolidator — inference + article write

**Files:**
- Modify: `pal/consolidator.py`
- Test: `tests/test_consolidator.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_consolidator.py`:

```python
@pytest.mark.asyncio
async def test_happy_path_writes_article(tmp_path):
    (tmp_path / "Security").mkdir()
    (tmp_path / "Security" / "a.md").write_text("---\ntitle: A\n---\nBody A")
    (tmp_path / "Security" / "b.md").write_text("---\ntitle: B\n---\nBody B")
    c, inference, wiki = _make(tmp_path, inference_response="## Overview\n\nFused (from Security/a.md)")

    out = await c.consolidate(
        source_paths=["Security/a.md", "Security/b.md"],
        target_path="Security/Combined.md",
        target_title="Combined",
    )

    assert out["status"] == "ok", out
    assert out["target_path"] == "Security/Combined.md"
    assert out["article_path_rel"] == "Security/Combined.md"
    assert out["vault_exists"] is True
    assert (tmp_path / "Security" / "Combined.md").exists()
    assert wiki.written and wiki.written[0]["title"] == "Combined"

    # Inference saw both source bodies and the path labels in the user message
    assert inference.calls, "inference was not invoked"
    messages = inference.calls[0]["messages"]
    user_content = next(m["content"] for m in messages if m["role"] == "user")
    assert "Security/a.md" in user_content
    assert "Security/b.md" in user_content
    assert "Body A" in user_content
    assert "Body B" in user_content


@pytest.mark.asyncio
async def test_insufficient_response(tmp_path):
    (tmp_path / "Security").mkdir()
    (tmp_path / "Security" / "a.md").write_text("---\ntitle: A\n---\nA")
    (tmp_path / "Security" / "b.md").write_text("---\ntitle: B\n---\nB")
    c, _, wiki = _make(tmp_path, inference_response="INSUFFICIENT: sources too thin")

    out = await c.consolidate(
        source_paths=["Security/a.md", "Security/b.md"],
        target_path="Security/Combined.md",
        target_title="Combined",
    )

    assert out["status"] == "insufficient"
    assert "thin" in out["reason"].lower()
    assert not wiki.written
    assert not (tmp_path / "Security" / "Combined.md").exists()


@pytest.mark.asyncio
async def test_prompt_demands_inline_citations(tmp_path):
    (tmp_path / "Security").mkdir()
    (tmp_path / "Security" / "a.md").write_text("---\ntitle: A\n---\nA")
    (tmp_path / "Security" / "b.md").write_text("---\ntitle: B\n---\nB")
    c, inference, _ = _make(tmp_path, inference_response="## Overview\n\nFused")

    await c.consolidate(
        source_paths=["Security/a.md", "Security/b.md"],
        target_path="Security/Combined.md",
        target_title="Combined",
    )

    messages = inference.calls[0]["messages"]
    system_content = next(m["content"] for m in messages if m["role"] == "system")
    assert "ONLY information" in system_content
    assert "INSUFFICIENT" in system_content
    assert "cite" in system_content.lower() or "citation" in system_content.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_consolidator.py -v`
Expected: the 3 new tests fail (status returns "error" / inference not called).

- [ ] **Step 3: Wire the inference + write path**

Replace the placeholder return at the bottom of `consolidate()` in `pal/consolidator.py`:

```python
        # Build the grounded system + user prompt
        base = self.prompt_builder.build()
        system_prompt = (
            f"{base}\n\n"
            "You are consolidating multiple existing wiki articles into ONE new article. RULES:\n"
            "- Use ONLY information from the SOURCES below. Do NOT add facts that aren't in sources.\n"
            "- Deduplicate overlap across sources; do not repeat the same fact twice.\n"
            "- For every substantive claim, cite the specific source path inline, e.g. (from Security/a.md).\n"
            "- If the combined source material lacks sufficient detail for a grounded article, respond "
            "with exactly: INSUFFICIENT: <one-sentence reason>\n\n"
            "Required sections: ## Overview, ## Key Concepts\n"
            "Optional sections (include if relevant): ## Usage, ## Configuration, ## Gotchas, ## Related"
        )

        labeled_sources = "\n\n".join(
            f"### SOURCE: {path}\n{body.strip()}" for path, body in source_bodies
        )
        user_prompt = (
            f"Target title: {target_title}\n"
            f"Target path: {target_path}\n\n"
            f"SOURCES ({len(source_bodies)}):\n\n{labeled_sources}"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            result = await self.inference.complete(messages, reasoning="off")
        except Exception as exc:
            logger.exception("Consolidate inference failed: %s", exc)
            return {
                "status": "error",
                "target_path": target_path,
                "reason": f"inference failed: {type(exc).__name__}: {exc}",
                "vault_exists": False,
            }

        content = (getattr(result, "content", "") or "").strip()
        if content.startswith("INSUFFICIENT:"):
            reason = content[len("INSUFFICIENT:"):].strip() or "model reported insufficient material"
            return {
                "status": "insufficient",
                "target_path": target_path,
                "reason": reason,
                "vault_exists": False,
            }

        tags = ["consolidated"]
        try:
            self.wiki.write_article(target_path, target_title, content, tags=tags)
            self.wiki.git_commit(
                f"consolidate {len(source_bodies)} sources -> {target_path}"
            )
        except Exception as exc:
            return {
                "status": "error",
                "target_path": target_path,
                "reason": f"write failed: {type(exc).__name__}: {exc}",
                "vault_exists": (self.vault_path / target_path).exists(),
            }

        return {
            "status": "ok",
            "target_path": target_path,
            "article_path_rel": target_path,
            "vault_exists": (self.vault_path / target_path).exists(),
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_consolidator.py -v`
Expected: all 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/consolidator.py tests/test_consolidator.py
git commit -m "feat: Consolidator inference + article write"
```

---

## Task 5: Tool specs + handlers

**Files:**
- Modify: `pal/tools.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing tests**

Create a new file `tests/test_chat_consolidate_tools.py` (parallel to `tests/test_chat_compile_tools.py`):

```python
import asyncio
import json
import pytest
from pathlib import Path

from pal.approval_registry import ApprovalRegistry
from pal.tools import ToolExecutor


class _StubConsolidator:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def consolidate(self, *, source_paths, target_path, target_title):
        self.calls.append({"source_paths": list(source_paths), "target_path": target_path, "target_title": target_title})
        return dict(self.outcome)


def _executor(tmp_path, *, stub=None, auto_approve=True):
    registry = ApprovalRegistry()
    emitted = []

    def emit(msg):
        emitted.append(msg)
        if auto_approve:
            registry.approve(msg.proposal_id)

    if stub is None:
        stub = _StubConsolidator({
            "status": "ok",
            "target_path": "Security/Combined.md",
            "article_path_rel": "Security/Combined.md",
            "vault_exists": True,
        })
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        wiki=None,
        approval_registry=registry,
        proposal_emitter=emit,
        consolidator=stub,
    )
    return executor, registry, emitted, stub


@pytest.mark.asyncio
async def test_propose_consolidate_requires_two_sources(tmp_path):
    executor, _, _, _ = _executor(tmp_path, auto_approve=False)
    result = await executor.run_async("propose_consolidate", {
        "source_paths": ["Security/a.md"],
        "target_path": "Security/Combined.md",
        "target_title": "Combined",
        "rationale": "r",
    })
    assert "at least two" in result.lower()


@pytest.mark.asyncio
async def test_propose_consolidate_requires_target(tmp_path):
    executor, _, _, _ = _executor(tmp_path, auto_approve=False)
    result = await executor.run_async("propose_consolidate", {
        "source_paths": ["Security/a.md", "Security/b.md"],
        "target_path": "",
        "target_title": "Combined",
        "rationale": "r",
    })
    assert "target_path" in result.lower() or "'target_path'" in result


@pytest.mark.asyncio
async def test_propose_then_execute_happy_path(tmp_path):
    executor, registry, emitted, stub = _executor(tmp_path)

    propose_result = await executor.run_async("propose_consolidate", {
        "source_paths": ["Security/a.md", "Security/b.md"],
        "target_path": "Security/Combined.md",
        "target_title": "Combined",
        "rationale": "merge overlapping notes",
    })
    payload = json.loads(propose_result)
    assert payload["status"] == "approved"
    assert payload["source_paths"] == ["Security/a.md", "Security/b.md"]
    assert payload["target_path"] == "Security/Combined.md"
    assert payload["target_title"] == "Combined"

    # One proposal message was emitted to the (fake) CLI/Discord layer.
    assert len(emitted) == 1
    assert emitted[0].target_path == "Security/Combined.md"

    exec_result = await executor.run_async("consolidate", {"proposal_id": payload["proposal_id"]})
    exec_payload = json.loads(exec_result)
    assert exec_payload["status"] == "ok"
    assert exec_payload["vault_exists"] is True
    assert exec_payload["target_path"] == "Security/Combined.md"
    assert "_note" in exec_payload  # ground-truth echo footer

    # Registry proposal is consumed.
    final = registry.get(payload["proposal_id"])
    assert final.status == "consumed"
    assert stub.calls == [{
        "source_paths": ["Security/a.md", "Security/b.md"],
        "target_path": "Security/Combined.md",
        "target_title": "Combined",
    }]


@pytest.mark.asyncio
async def test_execute_rejects_unknown_proposal(tmp_path):
    executor, _, _, _ = _executor(tmp_path, auto_approve=False)
    result = await executor.run_async("consolidate", {"proposal_id": "does-not-exist"})
    assert "unknown proposal_id" in result.lower()


@pytest.mark.asyncio
async def test_execute_rejects_reused_proposal(tmp_path):
    executor, _, _, _ = _executor(tmp_path)
    propose = await executor.run_async("propose_consolidate", {
        "source_paths": ["Security/a.md", "Security/b.md"],
        "target_path": "Security/Combined.md",
        "target_title": "Combined",
        "rationale": "r",
    })
    pid = json.loads(propose)["proposal_id"]
    first = await executor.run_async("consolidate", {"proposal_id": pid})
    assert json.loads(first)["status"] == "ok"
    second = await executor.run_async("consolidate", {"proposal_id": pid})
    assert "already used" in second.lower() or "consumed" in second.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_chat_consolidate_tools.py -v`
Expected: FAIL (`Unknown tool: propose_consolidate` or `TypeError: ... unexpected keyword argument 'consolidator'`).

- [ ] **Step 3: Add tool specs**

In `pal/tools.py`, inside the `TOOLS` list, after the reorg entries, append two entries:

```python
    {
        "type": "function",
        "function": {
            "name": "propose_consolidate",
            "description": (
                "Propose synthesizing 2+ existing wiki articles into a new article. "
                "Use when the user wants to merge or combine already-promoted articles "
                "(not raw summaries — use compile_batch for raw/summaries/). Blocks until "
                "the user approves, declines, or edits. After approval, immediately call "
                "consolidate(proposal_id). Source articles are NOT deleted by this tool; "
                "after consolidation completes, propose_reorg can archive them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Existing article paths to fuse (at least two). Must not be in raw/ or system dirs.",
                    },
                    "target_path": {
                        "type": "string",
                        "description": "New article path (must not exist, must not start with raw/ or _).",
                    },
                    "target_title": {
                        "type": "string",
                        "description": "Frontmatter title for the new article.",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "One-line reason shown to the user in the approval prompt.",
                    },
                },
                "required": ["source_paths", "target_path", "target_title", "rationale"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consolidate",
            "description": (
                "Execute a consolidate previously approved via propose_consolidate. "
                "Takes a proposal_id. Fails if not approved, already used, or expired. "
                "Returns a structured report including vault_exists for the target."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "proposal_id": {
                        "type": "string",
                        "description": "proposal_id returned by propose_consolidate.",
                    },
                },
                "required": ["proposal_id"],
            },
        },
    },
```

- [ ] **Step 4: Add constructor arg + handler registration**

In `ToolExecutor.__init__` in `pal/tools.py`, add a new parameter after `reorganizer`:

```python
        consolidator: "Consolidator | None" = None,
```

and store it:

```python
        self.consolidator = consolidator
```

In `run_async`, add two dispatch branches (near the other proposal/execute branches):

```python
        if name == "propose_consolidate":
            return await self._propose_consolidate(arguments)
        if name == "consolidate":
            return await self._consolidate(arguments)
```

- [ ] **Step 5: Implement `_propose_consolidate`**

Add to `ToolExecutor` in `pal/tools.py` (mirror `_propose_compile_batch`):

```python
    async def _propose_consolidate(self, arguments: dict) -> str:
        import json as _json
        from pal.protocol import ConsolidateProposalMessage

        if self.approval_registry is None or self.proposal_emitter is None:
            return "Error: consolidate proposals are not available in this session."
        source_paths = arguments.get("source_paths")
        if not isinstance(source_paths, list) or len(source_paths) < 2:
            return "Error: 'source_paths' must be a list with at least two entries."
        target_path = (arguments.get("target_path") or "").strip()
        if not target_path:
            return "Error: 'target_path' is required."
        target_title = (arguments.get("target_title") or "").strip()
        if not target_title:
            return "Error: 'target_title' is required."
        rationale = (arguments.get("rationale") or "").strip()
        if not rationale:
            return "Error: 'rationale' is required."

        try:
            proposal_id = self.approval_registry.create_proposal(
                kind="consolidate",
                summary_paths=source_paths,
                target_path=target_path,
                target_title=target_title,
                rationale=rationale,
            )
        except ValueError as exc:
            return f"Error: {exc}"

        proposal = self.approval_registry.get(proposal_id)
        self.proposal_emitter(
            ConsolidateProposalMessage(
                proposal_id=proposal_id,
                source_paths=list(source_paths),
                target_path=target_path,
                target_title=target_title,
                rationale=rationale,
            )
        )

        remaining = (proposal.expires_at - datetime.now(timezone.utc)).total_seconds()
        try:
            await asyncio.wait_for(proposal.event.wait(), timeout=max(0.1, remaining))
        except asyncio.TimeoutError:
            self.approval_registry.expire_stale()

        final = self.approval_registry.get(proposal_id)
        result = {"proposal_id": proposal_id, "status": final.status}
        if final.status == "declined":
            edited = self.approval_registry.get_successor(proposal_id)
            if edited is not None:
                result = {
                    "proposal_id": edited.proposal_id,
                    "status": "approved",
                    "source_paths": list(edited.summary_paths or []),
                    "target_path": edited.target_path or "",
                    "target_title": edited.target_title or "",
                }
        elif final.status == "approved":
            result["source_paths"] = list(final.summary_paths or [])
            result["target_path"] = final.target_path or ""
            result["target_title"] = final.target_title or ""
        return _json.dumps(result)
```

- [ ] **Step 6: Implement `_consolidate`**

Add to `ToolExecutor` (mirror `_compile_batch`):

```python
    async def _consolidate(self, arguments: dict) -> str:
        import json as _json
        proposal_id = (arguments.get("proposal_id") or "").strip()
        if not proposal_id:
            return "Error: 'proposal_id' is required."
        if self.approval_registry is None or self.consolidator is None:
            return "Error: consolidate execution is not available in this session."

        proposal = self.approval_registry.get(proposal_id)
        if proposal is None:
            return f"Error: unknown proposal_id: {proposal_id}"
        if proposal.kind != "consolidate":
            return f"Error: proposal_id {proposal_id} is not a consolidate proposal."
        if proposal.status == "pending":
            return "Error: proposal is not approved yet."
        if proposal.status == "declined":
            return "Error: proposal was declined."
        if proposal.status == "expired":
            return "Error: proposal expired; propose again."
        if proposal.status == "consumed":
            return "Error: proposal was already used. Each proposal is single-use."
        if proposal.status != "approved":
            return f"Error: proposal in unexpected state: {proposal.status}"

        self.approval_registry.consume(proposal_id)

        try:
            outcome = await self.consolidator.consolidate(
                source_paths=list(proposal.summary_paths or []),
                target_path=proposal.target_path or "",
                target_title=proposal.target_title or "",
            )
        except Exception as exc:
            outcome = {
                "status": "error",
                "target_path": proposal.target_path or "",
                "reason": f"{type(exc).__name__}: {exc}",
                "vault_exists": False,
            }

        outcome["_note"] = (
            "Trust vault_exists: if false, the target file was not written to disk."
        )
        return _json.dumps(outcome)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_chat_consolidate_tools.py -v`
Expected: all 5 tests pass (two guardrails, happy path, unknown proposal, reuse rejection).

- [ ] **Step 8: Commit**

```bash
git add pal/tools.py tests/test_chat_consolidate_tools.py
git commit -m "feat: propose_consolidate / consolidate tool handlers"
```

---

## Task 6: Daemon wire-up

**Files:**
- Modify: `pal/daemon.py`
- Test: `tests/test_daemon.py` (smoke check only — full integration in Task 9)

- [ ] **Step 1: Construct the Consolidator in `PalDaemon.__init__`**

In `pal/daemon.py`, after the `Reorganizer` construction block (around line 105), add:

```python
        from pal.consolidator import Consolidator
        self.consolidator = Consolidator(
            vault_path=config.vault_path,
            wiki=self.wiki,
            inference=self.inference,
            prompt_builder=self.prompt_builder,
        )
```

- [ ] **Step 2: Pass it into ToolExecutor**

In the `ToolExecutor(...)` construction further down in the file (around line 178), add a kwarg:

```python
            consolidator=self.consolidator,
```

- [ ] **Step 3: Run the existing daemon import smoke test**

Run: `source .venv/bin/activate && python -m pytest tests/test_import.py tests/test_daemon.py -v`
Expected: PASS. No regressions.

- [ ] **Step 4: Commit**

```bash
git add pal/daemon.py
git commit -m "feat: wire Consolidator into daemon + ToolExecutor"
```

---

## Task 7: CLI approval rendering

**Files:**
- Modify: `pal/cli.py`
- Test: None (pure formatter; covered indirectly)

- [ ] **Step 1: Add `format_consolidate_proposal`**

In `pal/cli.py`, after `format_reorg_proposal`, add:

```python
def format_consolidate_proposal(msg: "ConsolidateProposalMessage") -> str:
    """Render a consolidate proposal approval prompt. Pure formatter."""
    lines = [
        "",
        "────────── PAL proposes consolidate ──────────",
        f"  Sources ({len(msg.source_paths)}):",
    ]
    for path in msg.source_paths:
        lines.append(f"    {path}")
    lines.extend([
        f"  Target:    {msg.target_path}",
        f"  Title:     {msg.target_title}",
        f"  Rationale: {msg.rationale}",
        "  [a]pprove  [d]ecline  [e]dit",
        "> ",
    ])
    return "\n".join(lines)
```

- [ ] **Step 2: Import the message type + route it**

At the top of `pal/cli.py`, extend the existing protocol import:

```python
from pal.protocol import (
    ...existing types...,
    ConsolidateProposalMessage,
)
```

In the proposal dispatch block (search for `elif isinstance(msg, CompileProposalMessage):` around line 289), add a branch:

```python
                    elif isinstance(msg, ConsolidateProposalMessage):
                        # same pattern as the CompileProposalMessage branch
                        print(format_consolidate_proposal(msg), end="", flush=True)
                        # (mirror whatever approval loop the existing branch uses,
                        # including sending ResearchApprovalResponseMessage with
                        # the correct proposal_id and decision)
```

Read the surrounding block for the exact approval-reply shape and copy it verbatim, swapping only the class name.

- [ ] **Step 3: Smoke test**

Run: `source .venv/bin/activate && python -m pytest tests/ -v -x 2>&1 | tail -30`
Expected: all existing tests pass. No cli-specific test yet; verify in Task 9.

- [ ] **Step 4: Commit**

```bash
git add pal/cli.py
git commit -m "feat: CLI renders consolidate proposal"
```

---

## Task 8: Discord approval rendering + button routing

**Files:**
- Modify: `pal/discord_interactions.py`
- Test: `tests/test_discord_interactions.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_discord_interactions.py`:

```python
def test_parse_button_custom_id_accepts_consolidate():
    from pal.discord_interactions import parse_button_custom_id
    assert parse_button_custom_id("consolidate:approve:abc-1") == ("consolidate", "approve", "abc-1")
    assert parse_button_custom_id("consolidate:decline:abc-1") == ("consolidate", "decline", "abc-1")


def test_build_consolidate_proposal_embed():
    from pal.protocol import ConsolidateProposalMessage
    from pal.discord_interactions import build_consolidate_proposal_embed
    msg = ConsolidateProposalMessage(
        proposal_id="abc-1",
        source_paths=["Security/a.md", "Security/b.md"],
        target_path="Security/Combined.md",
        target_title="Combined",
        rationale="merge overlapping notes",
    )
    embed, view = build_consolidate_proposal_embed(msg)
    assert embed.title == "PAL proposes consolidate"
    # sources are rendered as a field
    field_names = [f.name for f in embed.fields]
    assert any("Sources" in n for n in field_names)
    assert any("Target" in n for n in field_names)
    # buttons carry the consolidate kind
    ids = [item.custom_id for item in view.children]
    assert "consolidate:approve:abc-1" in ids
    assert "consolidate:decline:abc-1" in ids
    assert "consolidate:edit:abc-1" in ids
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_discord_interactions.py -v -k consolidate`
Expected: FAIL.

- [ ] **Step 3: Extend `parse_button_custom_id` and `parse_modal_custom_id`**

In `pal/discord_interactions.py`, change both kind-validation tuples:

```python
    if kind not in ("research", "compile", "reorg"):
```
to:
```python
    if kind not in ("research", "compile", "reorg", "consolidate"):
```

(There are two occurrences — one in each parser.)

- [ ] **Step 4: Add `build_consolidate_proposal_embed`**

In `pal/discord_interactions.py`, after `build_reorg_proposal_embed`, add (mirror the compile version, adapt fields):

```python
def build_consolidate_proposal_embed(
    msg: ConsolidateProposalMessage,
) -> tuple[discord.Embed, discord.ui.View]:
    """Pure builder: returns the embed and a View with three buttons."""
    embed = discord.Embed(
        title="PAL proposes consolidate",
        color=discord.Color.blurple(),
    )
    total = len(msg.source_paths)
    cap = _DISCORD_FIELD_VALUE_LIMIT - _FIELD_BUDGET_HEADROOM
    fitted: list[str] = []
    chars = 0
    for path in msg.source_paths:
        add = len(path) + (1 if fitted else 0)
        if chars + add > cap:
            break
        fitted.append(path)
        chars += add
    dropped = total - len(fitted)
    paths_text = "\n".join(fitted)
    if dropped > 0:
        paths_text += f"\n+{dropped} more"
    embed.add_field(
        name=f"Sources ({total})",
        value=paths_text if paths_text else "(empty)",
        inline=False,
    )
    embed.add_field(name="Target", value=msg.target_path, inline=False)
    embed.add_field(name="Title", value=msg.target_title, inline=False)
    embed.add_field(name="Rationale", value=msg.rationale, inline=False)

    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        style=discord.ButtonStyle.success,
        label="Approve",
        emoji="✅",
        custom_id=f"consolidate:approve:{msg.proposal_id}",
    ))
    view.add_item(discord.ui.Button(
        style=discord.ButtonStyle.danger,
        label="Decline",
        emoji="❌",
        custom_id=f"consolidate:decline:{msg.proposal_id}",
    ))
    view.add_item(discord.ui.Button(
        style=discord.ButtonStyle.secondary,
        label="Edit",
        emoji="✏️",
        custom_id=f"consolidate:edit:{msg.proposal_id}",
    ))
    return embed, view
```

Import the message at the top alongside the others:

```python
from pal.protocol import (
    ...existing types...,
    ConsolidateProposalMessage,
)
```

- [ ] **Step 5: Dispatch `ConsolidateProposalMessage` in the stream handler**

In `pal/discord_interactions.py`, find the block that branches on proposal message type (search for `elif isinstance(msg, CompileProposalMessage):` around line 299). Add a sibling branch:

```python
            elif isinstance(msg, ConsolidateProposalMessage):
                await self._handle_consolidate_proposal(msg)
```

And add the handler method, mirroring `_handle_compile_proposal`:

```python
    async def _handle_consolidate_proposal(
        self, msg: ConsolidateProposalMessage,
    ) -> None:
        embed, view = build_consolidate_proposal_embed(msg)
        # ... mirror the rest of _handle_compile_proposal verbatim,
        # swapping the kind/label strings where they appear.
```

Read `_handle_compile_proposal` in the same file and translate it 1:1 — the structure (sending the embed, tracking proposal_id → message mapping, etc.) is shared.

- [ ] **Step 6: Extend any kind-aware helpers**

Search `pal/discord_interactions.py` for places that branch on `msg.kind` or handle `ctx.summary_paths` vs `ctx.operations` (e.g. the `reorg: N operations` string around line 471). Add a consolidate branch there:

```python
        if getattr(ctx, "kind", None) == "consolidate":
            srcs = getattr(ctx, "summary_paths", [])
            return f"consolidate: {len(srcs)} sources -> {getattr(ctx, 'target_path', '?')}"
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_discord_interactions.py -v`
Expected: all pass including the two new tests.

- [ ] **Step 8: Commit**

```bash
git add pal/discord_interactions.py tests/test_discord_interactions.py
git commit -m "feat: Discord embed + button routing for consolidate proposals"
```

---

## Task 9: Prompt builder updates

**Files:**
- Modify: `pal/prompt_builder.py`
- Test: `tests/test_prompt_builder.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_prompt_builder.py`:

```python
def test_base_prompt_mentions_consolidate_tool():
    from pal.prompt_builder import BASE_PROMPT
    assert "consolidate" in BASE_PROMPT.lower()
    assert "propose_consolidate" in BASE_PROMPT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_prompt_builder.py::test_base_prompt_mentions_consolidate_tool -v`
Expected: FAIL.

- [ ] **Step 3: Update the tool inventory in `BASE_PROMPT`**

In `pal/prompt_builder.py`, find the "Wiki promotion (grounded, source-linked)" section and replace it with:

```
Wiki promotion (grounded, source-linked):
- compile_summary: promote a single raw summary into a wiki article
- propose_compile_batch: propose promoting multiple summaries; blocks on user approval
- compile_batch: execute an approved compile batch
- propose_consolidate: propose fusing 2+ existing wiki articles into a new article; blocks on user approval
- consolidate: execute an approved consolidate proposal
```

- [ ] **Step 4: Update the "what you cannot do" caveat**

Find the caveat added during the preceding hardening pass (the bullet that begins with "Consolidate multiple existing wiki articles"). Replace it with:

```
- When asked to fuse already-promoted articles, use propose_consolidate (not compile_batch, which is only for raw/summaries/). The consolidate tool creates a new grounded article from the sources you name; afterwards, use propose_reorg with move ops if the user wants the sources archived.
```

- [ ] **Step 5: Run test + full suite to verify no regression**

Run: `source .venv/bin/activate && python -m pytest tests/test_prompt_builder.py tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pal/prompt_builder.py tests/test_prompt_builder.py
git commit -m "feat: prompt lists consolidate tool + updated guidance"
```

---

## Task 10: End-to-end smoke test

**Files:**
- Create: `tests/test_consolidate_integration.py`

- [ ] **Step 1: Write the integration test**

Create `tests/test_consolidate_integration.py`:

```python
"""End-to-end: propose_consolidate -> approval -> consolidate -> article exists."""
import asyncio
import json
import pytest
from pathlib import Path

from pal.approval_registry import ApprovalRegistry
from pal.consolidator import Consolidator
from pal.tools import ToolExecutor
from pal.wiki import WikiManager


class _FakeInference:
    async def generate(self, *, system: str, user: str):
        class R:
            content = "## Overview\n\nFused content (from Security/a.md)(from Security/b.md)"
        return R()


class _StubPromptBuilder:
    def build(self) -> str:
        return "BASE"


@pytest.mark.asyncio
async def test_consolidate_end_to_end(tmp_path):
    (tmp_path / "Security").mkdir()
    (tmp_path / "Security" / "a.md").write_text("---\ntitle: A\n---\nBody A")
    (tmp_path / "Security" / "b.md").write_text("---\ntitle: B\n---\nBody B")

    wiki = WikiManager(tmp_path)
    wiki.init_vault()
    registry = ApprovalRegistry()
    consolidator = Consolidator(
        vault_path=tmp_path,
        wiki=wiki,
        inference=_FakeInference(),
        prompt_builder=_StubPromptBuilder(),
    )

    emitted = []

    def emit(msg):
        emitted.append(msg)
        # Simulate immediate user approval.
        registry.approve(msg.proposal_id)

    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        wiki=wiki,
        approval_registry=registry,
        proposal_emitter=emit,
        consolidator=consolidator,
    )

    propose_result = await executor.run_async("propose_consolidate", {
        "source_paths": ["Security/a.md", "Security/b.md"],
        "target_path": "Security/Combined.md",
        "target_title": "Combined",
        "rationale": "merge overlapping notes",
    })
    propose_payload = json.loads(propose_result)
    assert propose_payload["status"] == "approved"
    pid = propose_payload["proposal_id"]

    exec_result = await executor.run_async("consolidate", {"proposal_id": pid})
    exec_payload = json.loads(exec_result)
    assert exec_payload["status"] == "ok"
    assert exec_payload["vault_exists"] is True
    assert (tmp_path / "Security" / "Combined.md").exists()
```

- [ ] **Step 2: Run the integration test**

Run: `source .venv/bin/activate && python -m pytest tests/test_consolidate_integration.py -v`
Expected: PASS.

- [ ] **Step 3: Run the full test suite**

Run: `source .venv/bin/activate && python -m pytest tests/ -q 2>&1 | tail -10`
Expected: PASS. If anything else breaks, it's a regression from Tasks 1-9; fix before committing.

- [ ] **Step 4: Commit**

```bash
git add tests/test_consolidate_integration.py
git commit -m "test: end-to-end consolidate propose/approve/execute"
```

---

## Task 11: Live verification with running daemon

**Files:** none

- [ ] **Step 1: Restart the daemon on the server** (or locally if running locally).

This picks up the new tool definitions so the model sees them.

- [ ] **Step 2: In Discord, run a consolidate through**

Ask PAL in the same channel used in the original Discord transcript:

> Please consolidate Reverse-Engineering/ai-assisted-reverse-engineering-overview.md and Reverse-Engineering/agentic-reverse-engineering-and-vulnerability-research-course.md into Reverse-Engineering/Combined-AI-Assisted-RE-Test.md titled "Combined AI-Assisted RE (test)".

Expected: PAL proposes a consolidate with the Approve / Decline / Edit buttons visible.

- [ ] **Step 3: Approve and verify**

After approving, confirm via the inference server API that the new article landed:

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  http://192.168.1.14:11434/collections/notes/docs/Reverse-Engineering/Combined-AI-Assisted-RE-Test
```

Expected: `200` (after next server reindex; until then the file is on disk but not in the vector index — PAL's prompt now handles this framing).

- [ ] **Step 4: Clean up the test article**

Since this is a smoke test, ask PAL to propose a reorg moving the test article to `raw/archived/Combined-AI-Assisted-RE-Test.md` to demonstrate the cleanup flow too.

---

## Notes on scope explicitly excluded from this plan

- **Source file archival/deletion after consolidate.** The `consolidate` tool writes only the new article. If the user wants the source files removed or moved, they run a separate `propose_reorg` afterwards. Keeping the primitives separated avoids the "delete without consent" failure mode and makes each tool's blast radius small.
- **Automatic categorization of the target.** The caller names `target_path` directly. This is intentional — consolidation is editorial, not categorical; we trust the user (or the LLM acting on the user's behalf via the proposal) to pick the location.
- **Merging INTO an existing article.** If the user wants to fold sources into an existing article, that's `propose_reorg` with `merge` ops — already supported.
