# Vault Reorganization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add consent-gated reorg operations (move, merge) to PAL so the model can propose, and the user can approve, safe batches of renames/consolidations with automatic markdown-link rewriting.

**Architecture:** A new `Reorganizer` class in `pal/reorg.py` owns validation and execution. Move operations rewrite markdown link references via regex, then rename the file, committing per-op. Merge operations delegate to a new public `Compiler.merge_into_existing` (extracted from `_compile_one`), then archive the source and redirect links. A new `ReorgProposalMessage` plus `Proposal.kind="reorg"` extends the existing consent-gate machinery to both CLI and Discord.

**Tech Stack:** Python 3.11+, asyncio, pytest, existing PAL modules (Compiler, WikiManager, ApprovalRegistry, ToolExecutor, discord.py).

**Spec:** `docs/superpowers/specs/2026-04-14-vault-reorg-design.md`

---

## Task R1: Extract `Compiler.merge_into_existing`

Refactor the merge-path logic inside `Compiler._compile_one` into a reusable public method. Behavior must stay identical for the compile tool; the method becomes a seam `Reorganizer` uses for merge ops.

**Files:**
- Modify: `pal/compiler.py`
- Modify: `tests/test_compiler.py`

- [ ] **Step 1: Write failing test for the new public method**

Append to `tests/test_compiler.py`:

```python
@pytest.mark.asyncio
async def test_merge_into_existing_updates_article_body(tmp_path):
    """The extracted merge_into_existing method should run the LLM
    synthesis against an existing article and write the merged body."""
    from pal.compiler import Compiler
    from unittest.mock import MagicMock, AsyncMock

    # Seed an existing article
    vault = tmp_path
    article_dir = vault / "AI-Security"
    article_dir.mkdir(parents=True)
    article_path_rel = "AI-Security/mcp-notes.md"
    (vault / article_path_rel).write_text(
        "---\ntitle: MCP Notes\n---\n\n## Overview\n\nExisting content.\n"
    )

    wiki = MagicMock()
    wiki.read_article = MagicMock(return_value=(
        {"title": "MCP Notes"},
        "## Overview\n\nExisting content.\n",
    ))
    wiki.write_article = MagicMock()
    wiki.rebuild_index = MagicMock()
    wiki.git_init = MagicMock()
    wiki.git_commit = MagicMock()
    wiki.list_articles = MagicMock(return_value=[])

    inference = MagicMock()
    inference.complete = AsyncMock(return_value=(
        "## Overview\n\nMerged content combining existing and new.\n"
    ))

    categorizer = MagicMock()
    prompt_builder = MagicMock()
    prompt_builder.build = MagicMock(return_value="system prompt")

    compiler = Compiler(
        vault_path=vault,
        wiki=wiki,
        inference=inference,
        categorizer=categorizer,
        prompt_builder=prompt_builder,
    )

    result = await compiler.merge_into_existing(
        new_content="New content to fold in.",
        new_title="MCP Additional Notes",
        existing_article_path=article_path_rel,
    )
    assert result["status"] == "merged"
    assert result["article_path_rel"] == article_path_rel
    # Verify the inference was called (the LLM synthesis)
    inference.complete.assert_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_compiler.py -v -k merge_into_existing`
Expected: FAIL — `AttributeError: 'Compiler' object has no attribute 'merge_into_existing'`.

- [ ] **Step 3: Read current `_compile_one` existing-match path**

Use the Read tool on `pal/compiler.py`, focusing on the `_compile_one` method. Identify the block that runs when `existing_match` is truthy — the "merge compile" branch. This block builds a merge-specific system prompt, calls `self.inference.complete(...)`, parses the result, writes the updated article, and appends a timeline entry.

- [ ] **Step 4: Extract the method**

In `pal/compiler.py`, add a new public async method on `Compiler`:

```python
    async def merge_into_existing(
        self,
        new_content: str,
        new_title: str,
        existing_article_path: str,
    ) -> dict[str, Any]:
        """Merge new content into an existing wiki article via LLM synthesis.

        Returns the same result shape as _compile_one:
          status: "merged" | "insufficient" | "error"
          title: str
          article_path_rel: str
          reason: str (on failure)

        Used by both _compile_one's existing-match branch and Reorganizer
        for merge operations.
        """
        from pal.article import parse_article, append_timeline_entry
        from pal.frontmatter import parse_frontmatter

        full_path = self.vault_path / existing_article_path
        if not full_path.exists():
            return {
                "status": "error",
                "title": new_title,
                "article_path_rel": existing_article_path,
                "reason": f"Existing article not found: {existing_article_path}",
            }

        existing_meta, existing_body = parse_frontmatter(full_path.read_text())
        existing_title = existing_meta.get("title", full_path.stem)
        existing_article = parse_article(existing_body)

        timeline_context = "\n".join(
            f"- {e.date} {e.source_label}: {e.summary[:200]}"
            for e in existing_article.timeline
        )

        base_prompt = self.prompt_builder.build()
        system_prompt = (
            f"{base_prompt}\n\n"
            "You are updating a wiki article with new information. "
            "Rewrite the compiled truth sections to incorporate the new source material. "
            "Keep the same section structure. Do not drop existing knowledge unless "
            "the new source directly contradicts it.\n\n"
            "Required sections: ## Overview, ## Key Concepts\n"
            "Optional sections (include if relevant): ## Usage, ## Configuration, "
            "## Gotchas, ## Related\n\n"
            "Use ONLY information from the existing article and the new source material."
        )
        user_prompt = (
            f"Existing article: {existing_title}\n\n"
            f"Existing timeline:\n{timeline_context}\n\n"
            f"Existing body:\n{existing_article.body}\n\n"
            f"--- New source material ---\n"
            f"Title: {new_title}\n\n"
            f"{new_content}\n"
        )

        try:
            merged_body = await self.inference.complete(
                system=system_prompt,
                user=user_prompt,
            )
        except Exception as exc:
            return {
                "status": "error",
                "title": new_title,
                "article_path_rel": existing_article_path,
                "reason": f"LLM merge failed: {exc}",
            }

        merged_body = merged_body.strip()
        if not merged_body or "insufficient" in merged_body.lower()[:50]:
            return {
                "status": "insufficient",
                "title": new_title,
                "article_path_rel": existing_article_path,
                "reason": "LLM refused to merge (insufficient content)",
            }

        # Append timeline entry and write the updated article
        from datetime import datetime, timezone
        updated = append_timeline_entry(
            existing_article,
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            source_label=new_title,
            summary=new_content[:400],
        )
        updated.body = merged_body

        from pal.article import serialize_article
        full_path.write_text(serialize_article(updated))
        self.wiki.git_init()
        self.wiki.git_commit(f"merge: {new_title} into {existing_title}")

        return {
            "status": "merged",
            "title": existing_title,
            "article_path_rel": existing_article_path,
        }
```

Now update `_compile_one`'s existing-match branch to delegate:

```python
        # Inside _compile_one, in the branch where existing_match is set:
        if existing_match:
            return await self.merge_into_existing(
                new_content=summary_body,
                new_title=title,
                existing_article_path=existing_match["path"],
            )
```

The rest of `_compile_one` (new-article path) stays unchanged.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest tests/test_compiler.py -v && .venv/bin/pytest -x`
Expected: new merge_into_existing test passes; existing compile tests still pass (compile's merge path is now a thin wrapper, behavior preserved).

If the refactor changes any observable behavior — e.g., an existing compile test fails — the extraction wasn't behavior-preserving. Investigate and restore parity before continuing.

- [ ] **Step 6: Commit**

```bash
git add pal/compiler.py tests/test_compiler.py
git commit -m "refactor: extract Compiler.merge_into_existing for reorg reuse"
```

---

## Task R2: ApprovalRegistry supports reorg kind + operations field

**Files:**
- Modify: `pal/approval_registry.py`
- Modify: `tests/test_approval_registry.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_approval_registry.py`:

```python
def test_create_proposal_with_reorg_kind():
    registry = ApprovalRegistry()
    ops = [
        {"type": "move", "src": "A.md", "dst": "B.md"},
        {"type": "merge", "src": "C.md", "dst": "D.md"},
    ]
    pid = registry.create_proposal(
        kind="reorg",
        operations=ops,
        rationale="consolidate duplicates",
    )
    proposal = registry.get(pid)
    assert proposal.kind == "reorg"
    assert proposal.operations == ops
    assert proposal.rationale == "consolidate duplicates"


def test_create_reorg_proposal_rejects_empty_operations():
    registry = ApprovalRegistry()
    import pytest
    with pytest.raises(ValueError):
        registry.create_proposal(
            kind="reorg",
            operations=[],
            rationale="r",
        )


def test_create_reorg_proposal_rejects_invalid_op_type():
    registry = ApprovalRegistry()
    import pytest
    with pytest.raises(ValueError):
        registry.create_proposal(
            kind="reorg",
            operations=[{"type": "delete", "src": "A.md", "dst": "B.md"}],
            rationale="r",
        )


def test_create_reorg_proposal_rejects_missing_src_dst():
    registry = ApprovalRegistry()
    import pytest
    with pytest.raises(ValueError):
        registry.create_proposal(
            kind="reorg",
            operations=[{"type": "move", "src": "A.md"}],  # no dst
            rationale="r",
        )


def test_edit_reorg_proposal_carries_kind_and_operations():
    registry = ApprovalRegistry()
    old_ops = [{"type": "move", "src": "A.md", "dst": "B.md"}]
    new_ops = [{"type": "move", "src": "A.md", "dst": "C.md"}]
    old_pid = registry.create_proposal(
        kind="reorg", operations=old_ops, rationale="r",
    )
    new_pid = registry.edit(old_pid, operations=new_ops)
    new = registry.get(new_pid)
    assert new.kind == "reorg"
    assert new.operations == new_ops
    assert new.status == "approved"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_approval_registry.py -v -k "reorg"`
Expected: FAIL — current `create_proposal` doesn't accept `kind="reorg"` or an `operations` parameter.

- [ ] **Step 3: Extend Proposal dataclass + ProposalKind**

In `pal/approval_registry.py`:

Extend `ProposalKind`:
```python
ProposalKind = Literal["research", "compile", "reorg"]
```

Add `operations` field on `Proposal`, placed after `summary_paths`:
```python
@dataclass
class Proposal:
    # ... existing fields ...
    summary_paths: Optional[list[str]] = None
    operations: Optional[list[dict]] = None
    event: asyncio.Event = field(default_factory=asyncio.Event, repr=False, compare=False)
```

- [ ] **Step 4: Extend create_proposal and edit**

Rewrite `create_proposal` signature and body:

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
        )
        return proposal_id
```

Extend `edit` to carry `operations`:

```python
    def edit(
        self,
        proposal_id: str,
        *,
        new_topic: Optional[str] = None,
        new_depth: Optional[int] = None,
        summary_paths: Optional[list[str]] = None,
        operations: Optional[list[dict]] = None,
    ) -> Optional[str]:
        old = self._proposals.get(proposal_id)
        if old is None or old.status != "pending":
            return None
        old.status = "declined"
        old.event.set()

        new_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        new_proposal = Proposal(
            proposal_id=new_id,
            topic=new_topic if new_topic is not None else old.topic,
            depth=new_depth if new_depth is not None else old.depth,
            rationale=old.rationale,
            status="approved",
            created_at=now,
            expires_at=now + timedelta(minutes=self._expiry_minutes),
            kind=old.kind,
            summary_paths=(
                list(summary_paths) if summary_paths is not None
                else (list(old.summary_paths) if old.summary_paths else None)
            ),
            operations=(
                [dict(op) for op in operations] if operations is not None
                else ([dict(op) for op in old.operations] if old.operations else None)
            ),
        )
        new_proposal.event.set()
        self._proposals[new_id] = new_proposal
        old.successor_id = new_id
        return new_id
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest tests/test_approval_registry.py -v && .venv/bin/pytest -x`
Expected: 5 new tests pass, existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add pal/approval_registry.py tests/test_approval_registry.py
git commit -m "feat: ApprovalRegistry supports reorg-kind proposals with operations list"
```

---

## Task R3: ReorgProposalMessage protocol

**Files:**
- Modify: `pal/protocol.py`
- Modify: `tests/test_protocol.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_protocol.py`:

```python
def test_reorg_proposal_message_roundtrip():
    from pal.protocol import ReorgProposalMessage
    msg = ReorgProposalMessage(
        proposal_id="abc",
        operations=[
            {"type": "move", "src": "A.md", "dst": "B.md"},
            {"type": "merge", "src": "C.md", "dst": "D.md"},
        ],
        rationale="consolidate and rename",
        references_preview=7,
    )
    decoded = decode_message(encode_message(msg).strip())
    assert isinstance(decoded, ReorgProposalMessage)
    assert decoded.proposal_id == "abc"
    assert decoded.operations == [
        {"type": "move", "src": "A.md", "dst": "B.md"},
        {"type": "merge", "src": "C.md", "dst": "D.md"},
    ]
    assert decoded.rationale == "consolidate and rename"
    assert decoded.references_preview == 7
    assert decoded.type == "reorg_proposal"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_protocol.py -v -k reorg`
Expected: FAIL — `ImportError: cannot import name 'ReorgProposalMessage'`.

- [ ] **Step 3: Add the dataclass**

In `pal/protocol.py`, add after `CompileProposalMessage`:

```python
@dataclass
class ReorgProposalMessage:
    proposal_id: str
    operations: list[dict]
    rationale: str
    references_preview: int
    type: str = "reorg_proposal"
```

Add to `_MESSAGE_TYPES`:

```python
    "reorg_proposal": ReorgProposalMessage,
```

Extend the `Message` union:

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
)
```

Update the module docstring's message-type list to include `reorg_proposal`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_protocol.py -v && .venv/bin/pytest -x`
Expected: new test passes, full suite passes.

- [ ] **Step 5: Commit**

```bash
git add pal/protocol.py tests/test_protocol.py
git commit -m "feat: ReorgProposalMessage protocol type"
```

---

## Task R4: Reorganizer validation and count_references

**Files:**
- Create: `pal/reorg.py`
- Create: `tests/test_reorg.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_reorg.py`:

```python
from pathlib import Path

import pytest

from pal.reorg import Reorganizer


def _seed_vault(tmp_path: Path, files: dict[str, str]) -> Path:
    """Write a set of markdown files under tmp_path. Returns tmp_path."""
    for rel, content in files.items():
        full = tmp_path / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    return tmp_path


def test_validate_rejects_missing_src(tmp_path):
    vault = _seed_vault(tmp_path, {})
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    errors = reorg.validate_operations(
        [{"type": "move", "src": "does-not-exist.md", "dst": "new.md"}]
    )
    assert errors
    assert any("does-not-exist" in e for e in errors)


def test_validate_rejects_dst_collision(tmp_path):
    vault = _seed_vault(tmp_path, {"A.md": "---\ntitle: A\n---\n", "B.md": "---\ntitle: B\n---\n"})
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    errors = reorg.validate_operations(
        [{"type": "move", "src": "A.md", "dst": "B.md"}]
    )
    assert errors
    assert any("exists" in e.lower() or "collision" in e.lower() for e in errors)


def test_validate_rejects_path_traversal(tmp_path):
    vault = _seed_vault(tmp_path, {"A.md": "---\ntitle: A\n---\n"})
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    errors = reorg.validate_operations(
        [{"type": "move", "src": "A.md", "dst": "../escape.md"}]
    )
    assert errors
    assert any("escape" in e.lower() or "invalid" in e.lower() or "outside" in e.lower() for e in errors)


def test_validate_rejects_system_path(tmp_path):
    vault = _seed_vault(tmp_path, {"A.md": "---\ntitle: A\n---\n"})
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    errors = reorg.validate_operations(
        [{"type": "move", "src": "A.md", "dst": "_config/settings.md"}]
    )
    assert errors
    assert any("system" in e.lower() or "underscore" in e.lower() for e in errors)


def test_validate_rejects_self_move(tmp_path):
    vault = _seed_vault(tmp_path, {"A.md": "---\ntitle: A\n---\n"})
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    errors = reorg.validate_operations(
        [{"type": "move", "src": "A.md", "dst": "A.md"}]
    )
    assert errors


def test_validate_rejects_duplicate_src_in_batch(tmp_path):
    vault = _seed_vault(tmp_path, {"A.md": "---\ntitle: A\n---\n"})
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    errors = reorg.validate_operations([
        {"type": "move", "src": "A.md", "dst": "B.md"},
        {"type": "move", "src": "A.md", "dst": "C.md"},
    ])
    assert errors
    assert any("duplicate" in e.lower() for e in errors)


def test_validate_simulates_execution_state(tmp_path):
    """If op 1 moves A to B, op 2 can reference B (it's produced by op 1)."""
    vault = _seed_vault(tmp_path, {"A.md": "---\ntitle: A\n---\n"})
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    errors = reorg.validate_operations([
        {"type": "move", "src": "A.md", "dst": "B.md"},
        {"type": "move", "src": "B.md", "dst": "C.md"},
    ])
    assert errors == []


def test_validate_passes_valid_batch(tmp_path):
    vault = _seed_vault(tmp_path, {
        "A.md": "---\ntitle: A\n---\n",
        "C.md": "---\ntitle: C\n---\n",
    })
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    errors = reorg.validate_operations([
        {"type": "move", "src": "A.md", "dst": "AI-Agents/renamed-a.md"},
    ])
    assert errors == []


def test_count_references_finds_markdown_links(tmp_path):
    vault = _seed_vault(tmp_path, {
        "A.md": "---\ntitle: A\n---\n\nLink: [x](target.md)\n",
        "B.md": "---\ntitle: B\n---\n\nAnother [y](target.md)\n",
        "C.md": "---\ntitle: C\n---\n\nNo link here\n",
        "target.md": "---\ntitle: Target\n---\n\n",
    })
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    count = reorg.count_references(["target.md"])
    assert count == 2


def test_count_references_ignores_literal_prose(tmp_path):
    vault = _seed_vault(tmp_path, {
        "A.md": "---\ntitle: A\n---\n\nSomewhere I mention target.md in prose but not as a link.\n",
    })
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    count = reorg.count_references(["target.md"])
    assert count == 0


def test_count_references_skips_raw_archived(tmp_path):
    vault = _seed_vault(tmp_path, {
        "A.md": "---\n---\n\n[x](target.md)\n",
        "raw/archived/B.md": "---\n---\n\n[y](target.md)\n",
    })
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    count = reorg.count_references(["target.md"])
    assert count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_reorg.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pal.reorg'`.

- [ ] **Step 3: Create pal/reorg.py with validation + count_references**

Create `pal/reorg.py`:

```python
"""Reorganizer — consent-gated vault reorg operations.

Owns validation, link-reference scanning, and execution of move/merge
operations. Pure of protocol concerns; a separate layer (tools.py)
handles proposal/approval lifecycle.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class Reorganizer:
    def __init__(
        self,
        vault_path: Path,
        wiki,              # WikiManager or None (tests may pass None)
        compiler,          # Compiler or None (only needed for merge ops)
    ) -> None:
        self.vault_path = vault_path
        self.wiki = wiki
        self.compiler = compiler

    # ---- validation ----

    def validate_operations(self, operations: list[dict]) -> list[str]:
        """Return list of validation errors. Empty list means valid."""
        errors: list[str] = []
        if not operations:
            errors.append("operations list is empty")
            return errors

        # Simulate execution state: track which srcs are "consumed"
        # (moved/merged away) and which dsts are "produced" as we walk.
        consumed: set[str] = set()
        produced: set[str] = set()
        seen_srcs: set[str] = set()
        seen_dsts: set[str] = set()

        for idx, op in enumerate(operations):
            op_type = op.get("type")
            src = op.get("src", "")
            dst = op.get("dst", "")
            prefix = f"op {idx+1} ({op_type})"

            if op_type not in ("move", "merge"):
                errors.append(f"{prefix}: unknown type {op_type!r}")
                continue

            # Path legality
            if not self._path_inside_vault(src) or not self._path_inside_vault(dst):
                errors.append(f"{prefix}: path outside vault (src={src!r} dst={dst!r})")
                continue
            if self._is_system_path(src) or self._is_system_path(dst):
                errors.append(f"{prefix}: system/underscore path not allowed")
                continue
            if src == dst:
                errors.append(f"{prefix}: src and dst are identical")
                continue

            # Batch uniqueness
            if src in seen_srcs:
                errors.append(f"{prefix}: duplicate src in batch: {src}")
                continue
            seen_srcs.add(src)
            if dst in seen_dsts:
                errors.append(f"{prefix}: duplicate dst in batch: {dst}")
                continue
            seen_dsts.add(dst)

            # Existence relative to simulated state
            src_exists_now = (src in produced) or (
                (self.vault_path / src).exists() and src not in consumed
            )
            if not src_exists_now:
                errors.append(f"{prefix}: src does not exist: {src}")
                continue

            # For move: dst must not exist
            # For merge: dst must exist
            dst_exists_now = (dst in produced) or (
                (self.vault_path / dst).exists() and dst not in consumed
            )
            if op_type == "move":
                if dst_exists_now:
                    errors.append(f"{prefix}: dst already exists (collision): {dst}")
                    continue
                consumed.add(src)
                produced.add(dst)
            else:  # merge
                if not dst_exists_now:
                    errors.append(f"{prefix}: dst does not exist for merge: {dst}")
                    continue
                consumed.add(src)
                # merge leaves dst in place; dst stays produced/existing

        return errors

    def _path_inside_vault(self, rel: str) -> bool:
        if rel.startswith("/"):
            return False
        if ".." in rel.split("/"):
            return False
        return True

    def _is_system_path(self, rel: str) -> bool:
        parts = Path(rel).parts
        return any(p.startswith("_") for p in parts)

    # ---- reference scanning ----

    _LINK_PATTERN_TEMPLATE = r"\]\(\s*{}\s*\)"

    def count_references(self, paths: list[str]) -> int:
        """Count markdown-link references across the vault to any of the
        given paths. Excludes raw/archived/."""
        total = 0
        for md_file in self.vault_path.rglob("*.md"):
            rel = md_file.relative_to(self.vault_path)
            if any(part == "archived" and rel.parts[0] == "raw" for part in rel.parts):
                continue
            try:
                content = md_file.read_text(errors="replace")
            except OSError:
                continue
            for path in paths:
                pattern = self._LINK_PATTERN_TEMPLATE.format(re.escape(path))
                total += len(re.findall(pattern, content))
        return total
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_reorg.py -v && .venv/bin/pytest -x`
Expected: 10 new tests pass, no regressions.

- [ ] **Step 5: Commit**

```bash
git add pal/reorg.py tests/test_reorg.py
git commit -m "feat: Reorganizer scaffold with validation and reference counting"
```

---

## Task R5: Reorganizer — execute_operations for move

**Files:**
- Modify: `pal/reorg.py`
- Modify: `tests/test_reorg.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_reorg.py`:

```python
from unittest.mock import MagicMock


def test_execute_move_renames_file_and_rewrites_links(tmp_path):
    vault = _seed_vault(tmp_path, {
        "AI-Agents/old-name.md": "---\ntitle: Old\n---\n\nBody.\n",
        "Other.md": "---\ntitle: Other\n---\n\nRefers to [thing](AI-Agents/old-name.md) here.\n",
    })
    wiki = MagicMock()
    wiki.git_commit = MagicMock()
    wiki.rebuild_index = MagicMock()
    reorg = Reorganizer(vault_path=vault, wiki=wiki, compiler=None)

    ops = [{"type": "move", "src": "AI-Agents/old-name.md", "dst": "AI-Agents/new-name.md"}]
    results = reorg.execute_operations(ops)

    # File renamed
    assert not (vault / "AI-Agents/old-name.md").exists()
    assert (vault / "AI-Agents/new-name.md").exists()

    # Link rewritten
    other_content = (vault / "Other.md").read_text()
    assert "(AI-Agents/new-name.md)" in other_content
    assert "(AI-Agents/old-name.md)" not in other_content

    # Report
    assert len(results) == 1
    assert results[0]["status"] == "ok"
    assert results[0]["op"] == "move"
    assert results[0]["references_rewritten"] == 1

    # git commit called
    wiki.git_commit.assert_called()


def test_execute_move_handles_zero_references(tmp_path):
    vault = _seed_vault(tmp_path, {
        "Lonely.md": "---\ntitle: Lonely\n---\n\nNo one links to me.\n",
    })
    wiki = MagicMock()
    reorg = Reorganizer(vault_path=vault, wiki=wiki, compiler=None)

    results = reorg.execute_operations([
        {"type": "move", "src": "Lonely.md", "dst": "Renamed.md"},
    ])
    assert results[0]["status"] == "ok"
    assert results[0]["references_rewritten"] == 0
    assert (vault / "Renamed.md").exists()


def test_execute_move_partial_failure_isolation(tmp_path):
    """If op 2 fails for a filesystem reason, op 1 should still have landed."""
    vault = _seed_vault(tmp_path, {
        "A.md": "---\n---\n",
        "B.md": "---\n---\n",
    })
    wiki = MagicMock()
    reorg = Reorganizer(vault_path=vault, wiki=wiki, compiler=None)

    # Second op has src that won't exist after first op runs (duplicates
    # are caught by validation, so we test via a crafted bad op).
    # Instead: we test a scenario where op 2's src was never seeded.
    ops = [
        {"type": "move", "src": "A.md", "dst": "A-new.md"},
        {"type": "move", "src": "ghost.md", "dst": "ghost-new.md"},
    ]
    # Skip pre-validation by calling execute_operations directly —
    # in the normal flow, validate_operations would catch this. The test
    # asserts execute's per-op error isolation.
    results = reorg.execute_operations(ops)
    assert results[0]["status"] == "ok"
    assert results[1]["status"] == "failed"
    assert (vault / "A-new.md").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_reorg.py -v -k "execute_move"`
Expected: FAIL — `execute_operations` not yet implemented.

- [ ] **Step 3: Add execute_operations with move path**

Append to the `Reorganizer` class in `pal/reorg.py`:

```python
    # ---- execution ----

    def execute_operations(self, operations: list[dict]) -> list[dict]:
        """Execute a batch of operations sequentially. Returns per-op results."""
        results: list[dict] = []
        for op in operations:
            op_type = op.get("type")
            if op_type == "move":
                results.append(self._execute_move(op))
            elif op_type == "merge":
                # merge implementation lands in R6
                results.append({
                    "op": "merge",
                    "src": op.get("src", ""),
                    "dst": op.get("dst", ""),
                    "status": "failed",
                    "reason": "merge not implemented yet",
                    "references_rewritten": 0,
                })
            else:
                results.append({
                    "op": str(op_type),
                    "src": op.get("src", ""),
                    "dst": op.get("dst", ""),
                    "status": "failed",
                    "reason": f"unknown op type: {op_type!r}",
                    "references_rewritten": 0,
                })
        # Final index rebuild
        if self.wiki is not None:
            try:
                self.wiki.rebuild_index()
            except Exception as exc:
                logger.warning("rebuild_index failed after reorg: %s", exc)
        return results

    def _execute_move(self, op: dict) -> dict:
        src = op.get("src", "")
        dst = op.get("dst", "")
        src_full = self.vault_path / src
        dst_full = self.vault_path / dst

        if not src_full.exists():
            return {
                "op": "move", "src": src, "dst": dst,
                "status": "failed",
                "reason": f"src does not exist: {src}",
                "references_rewritten": 0,
            }
        if dst_full.exists():
            return {
                "op": "move", "src": src, "dst": dst,
                "status": "failed",
                "reason": f"dst already exists: {dst}",
                "references_rewritten": 0,
            }

        # Rewrite link references first, then rename
        try:
            refs = self._rewrite_references(src, dst)
        except Exception as exc:
            return {
                "op": "move", "src": src, "dst": dst,
                "status": "failed",
                "reason": f"link rewrite failed: {exc}",
                "references_rewritten": 0,
            }

        try:
            dst_full.parent.mkdir(parents=True, exist_ok=True)
            src_full.rename(dst_full)
        except Exception as exc:
            return {
                "op": "move", "src": src, "dst": dst,
                "status": "failed",
                "reason": f"rename failed: {exc}",
                "references_rewritten": refs,
            }

        if self.wiki is not None:
            try:
                self.wiki.git_commit(f"reorg: move {src} -> {dst}")
            except Exception as exc:
                logger.warning("git commit failed after move: %s", exc)

        return {
            "op": "move", "src": src, "dst": dst,
            "status": "ok",
            "references_rewritten": refs,
        }

    def _rewrite_references(self, old_path: str, new_path: str) -> int:
        """Rewrite `](old_path)` occurrences to `](new_path)` across the
        vault (excluding raw/archived/). Returns the number of rewrites."""
        pattern = re.compile(self._LINK_PATTERN_TEMPLATE.format(re.escape(old_path)))
        replacement = f"]({new_path})"
        total = 0
        for md_file in self.vault_path.rglob("*.md"):
            rel = md_file.relative_to(self.vault_path)
            if rel.parts[:2] == ("raw", "archived"):
                continue
            try:
                content = md_file.read_text(errors="replace")
            except OSError:
                continue
            new_content, n = pattern.subn(replacement, content)
            if n > 0:
                md_file.write_text(new_content)
                total += n
        return total
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_reorg.py -v && .venv/bin/pytest -x`
Expected: move-execution tests pass, previously-passing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add pal/reorg.py tests/test_reorg.py
git commit -m "feat: Reorganizer.execute_operations move path with link rewriting"
```

---

## Task R6: Reorganizer — merge path

**Files:**
- Modify: `pal/reorg.py`
- Modify: `tests/test_reorg.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_reorg.py`:

```python
@pytest.mark.asyncio
async def test_execute_merge_folds_src_into_dst(tmp_path):
    """Merge should delegate to compiler.merge_into_existing, then
    archive src and rewrite references."""
    vault = _seed_vault(tmp_path, {
        "AI-Security/src.md": "---\ntitle: Src\n---\n\nSrc body.\n",
        "AI-Security/dst.md": "---\ntitle: Dst\n---\n\nDst body.\n",
        "Other.md": "---\n---\n\nLinks to [x](AI-Security/src.md) here.\n",
    })
    archived_dir = vault / "raw" / "archived"
    archived_dir.mkdir(parents=True)

    wiki = MagicMock()
    wiki.git_commit = MagicMock()
    wiki.rebuild_index = MagicMock()

    # Mock compiler.merge_into_existing to succeed
    compiler = MagicMock()
    async def fake_merge(new_content, new_title, existing_article_path):
        return {
            "status": "merged",
            "title": "Dst",
            "article_path_rel": existing_article_path,
        }
    compiler.merge_into_existing = fake_merge

    reorg = Reorganizer(vault_path=vault, wiki=wiki, compiler=compiler)
    ops = [{"type": "merge", "src": "AI-Security/src.md", "dst": "AI-Security/dst.md"}]
    results = await reorg.execute_operations_async(ops)

    # src moved to archived
    assert not (vault / "AI-Security/src.md").exists()
    # dst still there
    assert (vault / "AI-Security/dst.md").exists()
    # references to src rewritten to dst
    other = (vault / "Other.md").read_text()
    assert "(AI-Security/dst.md)" in other
    assert "(AI-Security/src.md)" not in other
    # result
    assert results[0]["status"] == "ok"
    assert results[0]["op"] == "merge"
    assert results[0]["references_rewritten"] == 1


@pytest.mark.asyncio
async def test_execute_merge_leaves_files_on_insufficient(tmp_path):
    """If compiler.merge_into_existing returns 'insufficient', src and
    dst should both stay in place and references should NOT be rewritten."""
    vault = _seed_vault(tmp_path, {
        "src.md": "---\n---\n",
        "dst.md": "---\n---\n",
        "Other.md": "---\n---\n\n[x](src.md)\n",
    })
    (vault / "raw" / "archived").mkdir(parents=True)

    wiki = MagicMock()
    compiler = MagicMock()
    async def fake_merge(new_content, new_title, existing_article_path):
        return {"status": "insufficient", "title": "t",
                "article_path_rel": existing_article_path,
                "reason": "LLM refused"}
    compiler.merge_into_existing = fake_merge

    reorg = Reorganizer(vault_path=vault, wiki=wiki, compiler=compiler)
    ops = [{"type": "merge", "src": "src.md", "dst": "dst.md"}]
    results = await reorg.execute_operations_async(ops)

    assert (vault / "src.md").exists()
    assert (vault / "dst.md").exists()
    assert "(src.md)" in (vault / "Other.md").read_text()
    assert results[0]["status"] == "insufficient"
    assert results[0]["references_rewritten"] == 0
```

Note: we're introducing a new async variant `execute_operations_async` because merge requires an async `compiler.merge_into_existing` call. The sync `execute_operations` from R5 stays for callers who only do move ops; the async variant covers heterogeneous batches. We'll unify in Step 3.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_reorg.py -v -k "execute_merge"`
Expected: FAIL — `execute_operations_async` not yet defined.

- [ ] **Step 3: Replace execute_operations with async version**

In `pal/reorg.py`, rename the existing `execute_operations` to `execute_operations_async` and make it async. Add merge handling.

```python
    # Replace the existing execute_operations with this async version:
    async def execute_operations_async(self, operations: list[dict]) -> list[dict]:
        """Execute a batch of operations sequentially. Returns per-op
        results. Async because merge ops call the LLM via Compiler."""
        results: list[dict] = []
        for op in operations:
            op_type = op.get("type")
            if op_type == "move":
                results.append(self._execute_move(op))
            elif op_type == "merge":
                results.append(await self._execute_merge(op))
            else:
                results.append({
                    "op": str(op_type),
                    "src": op.get("src", ""),
                    "dst": op.get("dst", ""),
                    "status": "failed",
                    "reason": f"unknown op type: {op_type!r}",
                    "references_rewritten": 0,
                })
        if self.wiki is not None:
            try:
                self.wiki.rebuild_index()
            except Exception as exc:
                logger.warning("rebuild_index failed after reorg: %s", exc)
        return results

    # Keep a sync wrapper for R5's existing tests
    def execute_operations(self, operations: list[dict]) -> list[dict]:
        """Synchronous execution for move-only batches. Raises if any op
        is a merge (use execute_operations_async for those)."""
        for op in operations:
            if op.get("type") == "merge":
                raise RuntimeError(
                    "merge ops require execute_operations_async; use that instead"
                )
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self.execute_operations_async(operations)
        )

    async def _execute_merge(self, op: dict) -> dict:
        src = op.get("src", "")
        dst = op.get("dst", "")
        src_full = self.vault_path / src
        dst_full = self.vault_path / dst

        if not src_full.exists():
            return {"op": "merge", "src": src, "dst": dst,
                    "status": "failed",
                    "reason": f"src does not exist: {src}",
                    "references_rewritten": 0}
        if not dst_full.exists():
            return {"op": "merge", "src": src, "dst": dst,
                    "status": "failed",
                    "reason": f"dst does not exist: {dst}",
                    "references_rewritten": 0}
        if self.compiler is None:
            return {"op": "merge", "src": src, "dst": dst,
                    "status": "failed",
                    "reason": "compiler not available",
                    "references_rewritten": 0}

        # Read src body and title
        from pal.frontmatter import parse_frontmatter
        src_meta, src_body = parse_frontmatter(src_full.read_text())
        src_title = src_meta.get("title", src_full.stem)

        # Delegate to Compiler.merge_into_existing
        merge_result = await self.compiler.merge_into_existing(
            new_content=src_body,
            new_title=src_title,
            existing_article_path=dst,
        )
        if merge_result.get("status") != "merged":
            # Preserve the compiler's status (insufficient, error)
            return {
                "op": "merge", "src": src, "dst": dst,
                "status": merge_result.get("status", "failed"),
                "reason": merge_result.get("reason", "merge failed"),
                "references_rewritten": 0,
            }

        # Rewrite references from src to dst
        try:
            refs = self._rewrite_references(src, dst)
        except Exception as exc:
            return {"op": "merge", "src": src, "dst": dst,
                    "status": "failed",
                    "reason": f"link rewrite failed after merge: {exc}",
                    "references_rewritten": 0}

        # Archive src: move it to raw/archived/<stem>.archived.md
        archived_dir = self.vault_path / "raw" / "archived"
        archived_dir.mkdir(parents=True, exist_ok=True)
        archive_dest = archived_dir / f"{src_full.stem}.archived.md"
        # If an archive with this name exists, append the proposal-ish hash
        if archive_dest.exists():
            import hashlib
            h = hashlib.sha1(src.encode("utf-8")).hexdigest()[:8]
            archive_dest = archived_dir / f"{src_full.stem}.{h}.archived.md"
        src_full.rename(archive_dest)

        if self.wiki is not None:
            try:
                self.wiki.git_commit(f"reorg: merge {src} into {dst}")
            except Exception as exc:
                logger.warning("git commit failed after merge: %s", exc)

        return {"op": "merge", "src": src, "dst": dst,
                "status": "ok",
                "references_rewritten": refs}
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_reorg.py -v && .venv/bin/pytest -x`
Expected: merge tests pass, prior move tests still pass, no regressions.

- [ ] **Step 5: Commit**

```bash
git add pal/reorg.py tests/test_reorg.py
git commit -m "feat: Reorganizer merge op delegates to Compiler.merge_into_existing"
```

---

## Task R7: ToolExecutor accepts reorganizer + propose_reorg tool

**Files:**
- Modify: `pal/tools.py`
- Create: `tests/test_chat_reorg_tools.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_chat_reorg_tools.py`:

```python
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pal.approval_registry import ApprovalRegistry
from pal.tools import ToolExecutor


def test_tool_executor_accepts_reorganizer(tmp_path: Path):
    reorganizer = MagicMock()
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        reorganizer=reorganizer,
    )
    assert executor.reorganizer is reorganizer


@pytest.mark.asyncio
async def test_propose_reorg_approved(tmp_path):
    registry = ApprovalRegistry()
    emitted = []
    reorganizer = MagicMock()
    reorganizer.count_references = MagicMock(return_value=3)

    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        proposal_emitter=emitted.append,
        reorganizer=reorganizer,
    )

    async def approve_later():
        for _ in range(50):
            if emitted:
                break
            await asyncio.sleep(0.01)
        registry.approve(emitted[0].proposal_id)

    ops = [{"type": "move", "src": "A.md", "dst": "B.md"}]
    asyncio.create_task(approve_later())
    output = await executor.run_async(
        "propose_reorg",
        {"operations": ops, "rationale": "rename"},
    )
    assert '"status": "approved"' in output
    from pal.protocol import ReorgProposalMessage
    assert isinstance(emitted[0], ReorgProposalMessage)
    assert emitted[0].operations == ops
    assert emitted[0].references_preview == 3


@pytest.mark.asyncio
async def test_propose_reorg_rejects_empty_operations(tmp_path):
    registry = ApprovalRegistry()
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        proposal_emitter=MagicMock(),
        reorganizer=MagicMock(),
    )
    output = await executor.run_async(
        "propose_reorg",
        {"operations": [], "rationale": "r"},
    )
    assert "Error" in output


@pytest.mark.asyncio
async def test_propose_reorg_rejects_missing_rationale(tmp_path):
    registry = ApprovalRegistry()
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        proposal_emitter=MagicMock(),
        reorganizer=MagicMock(),
    )
    output = await executor.run_async(
        "propose_reorg",
        {"operations": [{"type": "move", "src": "A.md", "dst": "B.md"}]},
    )
    assert "Error" in output and "rationale" in output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_chat_reorg_tools.py -v`
Expected: FAIL — ToolExecutor doesn't accept `reorganizer` kwarg, and `propose_reorg` tool doesn't exist.

- [ ] **Step 3: Extend ToolExecutor and add propose_reorg**

In `pal/tools.py`:

Add `reorganizer` to the TYPE_CHECKING imports:

```python
if TYPE_CHECKING:
    from pal.approval_registry import ApprovalRegistry
    from pal.websearch import WebSearchClient
    from pal.researcher import Researcher
    from pal.compiler import Compiler
    from pal.reorg import Reorganizer
```

Extend `ToolExecutor.__init__`:

```python
    def __init__(
        self,
        vault_path: Path,
        retrieval: "RetrievalClient | None",
        wiki: "WikiManager | None" = None,
        approval_registry: "ApprovalRegistry | None" = None,
        websearch: "WebSearchClient | None" = None,
        researcher: "Researcher | None" = None,
        proposal_emitter=None,
        compiler: "Compiler | None" = None,
        reorganizer: "Reorganizer | None" = None,
    ) -> None:
        self.vault_path = vault_path.resolve()
        self.retrieval = retrieval
        self.wiki = wiki
        self.approval_registry = approval_registry
        self.websearch = websearch
        self.researcher = researcher
        self.proposal_emitter = proposal_emitter
        self.compiler = compiler
        self.reorganizer = reorganizer
```

Add to `TOOL_DEFINITIONS`:

```python
    {
        "type": "function",
        "function": {
            "name": "propose_reorg",
            "description": (
                "Propose a batch of vault reorganization operations "
                "(move/rename articles or merge duplicates). Blocks "
                "until the user approves, declines, or edits. After "
                "approval, call reorg(proposal_id) to execute."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["move", "merge"],
                                },
                                "src": {"type": "string"},
                                "dst": {"type": "string"},
                            },
                            "required": ["type", "src", "dst"],
                        },
                        "description": "List of reorg operations (non-empty).",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "One-line reason shown to the user.",
                    },
                },
                "required": ["operations", "rationale"],
            },
        },
    },
```

Extend `run_async` dispatch:

```python
        if name == "propose_reorg":
            return await self._propose_reorg(arguments)
```

Add handler:

```python
    async def _propose_reorg(self, arguments: dict) -> str:
        import json as _json
        from pal.protocol import ReorgProposalMessage

        if (self.approval_registry is None or self.proposal_emitter is None
                or self.reorganizer is None):
            return "Error: reorg proposals are not available in this session."
        operations = arguments.get("operations")
        if not isinstance(operations, list) or not operations:
            return "Error: 'operations' must be a non-empty list."
        rationale = (arguments.get("rationale") or "").strip()
        if not rationale:
            return "Error: 'rationale' parameter is required."

        # Pre-validate to surface errors before prompting the user
        try:
            validation_errors = self.reorganizer.validate_operations(operations)
        except Exception as exc:
            return f"Error: operation validation failed: {exc}"
        if validation_errors:
            return "Error: invalid operations:\n" + "\n".join(validation_errors)

        # Reference-count preview
        src_paths = [op["src"] for op in operations if "src" in op]
        try:
            references_preview = self.reorganizer.count_references(src_paths)
        except Exception:
            references_preview = 0

        try:
            proposal_id = self.approval_registry.create_proposal(
                kind="reorg",
                operations=operations,
                rationale=rationale,
            )
        except ValueError as exc:
            return f"Error: {exc}"

        proposal = self.approval_registry.get(proposal_id)
        self.proposal_emitter(
            ReorgProposalMessage(
                proposal_id=proposal_id,
                operations=[dict(op) for op in operations],
                rationale=rationale,
                references_preview=references_preview,
            )
        )

        import asyncio
        from datetime import datetime, timezone
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
                    "operations": list(edited.operations or []),
                }
        elif final.status == "approved":
            result["operations"] = list(final.operations or [])
        return _json.dumps(result)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_chat_reorg_tools.py -v && .venv/bin/pytest -x`
Expected: 4 new tests pass, no regressions.

- [ ] **Step 5: Commit**

```bash
git add pal/tools.py tests/test_chat_reorg_tools.py
git commit -m "feat: propose_reorg tool with validation and reference-count preview"
```

---

## Task R8: reorg tool

**Files:**
- Modify: `pal/tools.py`
- Modify: `tests/test_chat_reorg_tools.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_chat_reorg_tools.py`:

```python
@pytest.mark.asyncio
async def test_reorg_runs_approved_proposal(tmp_path):
    registry = ApprovalRegistry()
    ops = [{"type": "move", "src": "A.md", "dst": "B.md"}]
    pid = registry.create_proposal(kind="reorg", operations=ops, rationale="r")
    registry.approve(pid)

    reorganizer = MagicMock()
    async def fake_exec(ops):
        return [{"op": "move", "src": "A.md", "dst": "B.md",
                 "status": "ok", "references_rewritten": 2}]
    reorganizer.execute_operations_async = fake_exec
    reorganizer.validate_operations = MagicMock(return_value=[])

    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        reorganizer=reorganizer,
    )
    output = await executor.run_async("reorg", {"proposal_id": pid})
    assert '"total": 1' in output
    assert '"ok": 1' in output
    assert '"references_rewritten": 2' in output
    assert registry.get(pid).status == "consumed"


@pytest.mark.asyncio
async def test_reorg_refuses_unknown_proposal(tmp_path):
    registry = ApprovalRegistry()
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        reorganizer=MagicMock(),
    )
    output = await executor.run_async("reorg", {"proposal_id": "unknown"})
    assert "unknown" in output.lower() or "not found" in output.lower()


@pytest.mark.asyncio
async def test_reorg_refuses_wrong_kind(tmp_path):
    registry = ApprovalRegistry()
    pid = registry.create_proposal(
        kind="research", topic="t", depth=3, rationale="r",
    )
    registry.approve(pid)
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        reorganizer=MagicMock(),
    )
    output = await executor.run_async("reorg", {"proposal_id": pid})
    assert "not a reorg proposal" in output.lower()


@pytest.mark.asyncio
async def test_reorg_pre_validation_blocks_execution(tmp_path):
    """If validate_operations returns errors post-approval (e.g., vault
    state changed between proposal and execute), reorg should not
    run execute_operations_async."""
    registry = ApprovalRegistry()
    ops = [{"type": "move", "src": "A.md", "dst": "B.md"}]
    pid = registry.create_proposal(kind="reorg", operations=ops, rationale="r")
    registry.approve(pid)

    reorganizer = MagicMock()
    reorganizer.validate_operations = MagicMock(return_value=["src missing"])
    reorganizer.execute_operations_async = AsyncMock()

    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        reorganizer=reorganizer,
    )
    output = await executor.run_async("reorg", {"proposal_id": pid})
    assert "invalid" in output.lower() or "src missing" in output.lower()
    reorganizer.execute_operations_async.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_chat_reorg_tools.py -v -k reorg_`
Expected: FAIL — reorg tool not defined.

- [ ] **Step 3: Add reorg tool**

In `pal/tools.py`, append to `TOOL_DEFINITIONS`:

```python
    {
        "type": "function",
        "function": {
            "name": "reorg",
            "description": (
                "Execute a reorg batch previously approved via "
                "propose_reorg. Pre-validates the operations against "
                "current vault state before any mutation. Partial "
                "failures don't abort the batch. Returns a structured "
                "report."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "proposal_id": {
                        "type": "string",
                        "description": "proposal_id returned by propose_reorg.",
                    },
                },
                "required": ["proposal_id"],
            },
        },
    },
```

Extend `run_async`:

```python
        if name == "reorg":
            return await self._reorg(arguments)
```

Add handler:

```python
    async def _reorg(self, arguments: dict) -> str:
        import json as _json
        proposal_id = (arguments.get("proposal_id") or "").strip()
        if not proposal_id:
            return "Error: 'proposal_id' parameter is required."
        if self.approval_registry is None or self.reorganizer is None:
            return "Error: reorg execution is not available in this session."

        proposal = self.approval_registry.get(proposal_id)
        if proposal is None:
            return f"Error: unknown proposal_id: {proposal_id}"
        if proposal.kind != "reorg":
            return f"Error: proposal_id {proposal_id} is not a reorg proposal."
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

        # Consume first — single-use invariant
        self.approval_registry.consume(proposal_id)

        ops = list(proposal.operations or [])

        # Re-validate against current vault state. State may have changed
        # between proposal and execute.
        validation_errors = self.reorganizer.validate_operations(ops)
        if validation_errors:
            return "Error: invalid operations:\n" + "\n".join(validation_errors)

        try:
            per_op = await self.reorganizer.execute_operations_async(ops)
        except Exception as exc:
            return f"Error: reorg execution failed: {exc}"

        ok = sum(1 for r in per_op if r.get("status") == "ok")
        failed = sum(1 for r in per_op if r.get("status") not in ("ok",))
        refs = sum(int(r.get("references_rewritten", 0)) for r in per_op)

        report = {
            "total": len(per_op),
            "ok": ok,
            "failed": failed,
            "references_rewritten": refs,
            "per_op": per_op,
        }
        return _json.dumps(report)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_chat_reorg_tools.py -v && .venv/bin/pytest -x`
Expected: 4 new tests pass, no regressions.

- [ ] **Step 5: Commit**

```bash
git add pal/tools.py tests/test_chat_reorg_tools.py
git commit -m "feat: reorg tool executes approved proposals with re-validation"
```

---

## Task R9: Daemon wires Reorganizer

**Files:**
- Modify: `pal/daemon.py`

- [ ] **Step 1: Use the Grep tool to find the Daemon.__init__ and ToolExecutor construction**

Find where `self.compiler = Compiler(...)` is constructed in `Daemon.__init__` and where `ToolExecutor(...)` is built inside `_handle_connection`.

- [ ] **Step 2: Add Reorganizer construction and pass it into ToolExecutor**

In `Daemon.__init__`, after `self.compiler = Compiler(...)` construction, add:

```python
        from pal.reorg import Reorganizer
        self.reorganizer = Reorganizer(
            vault_path=config.vault_path,
            wiki=self.wiki,
            compiler=self.compiler,
        )
```

In `_handle_connection`, find the `tool_executor = ToolExecutor(...)` call and add `reorganizer=self.reorganizer` to the kwargs:

```python
        tool_executor = ToolExecutor(
            vault_path=self.config.vault_path,
            retrieval=self.retrieval,
            wiki=self.wiki,
            approval_registry=approval_registry,
            websearch=self.websearch,
            researcher=researcher,
            proposal_emitter=emit_proposal,
            compiler=self.compiler,
            reorganizer=self.reorganizer,
        )
```

- [ ] **Step 3: Run tests**

Run: `.venv/bin/pytest -x`
Expected: all tests pass, no regressions.

- [ ] **Step 4: Commit**

```bash
git add pal/daemon.py
git commit -m "feat: daemon constructs Reorganizer and injects into ToolExecutor"
```

---

## Task R10: CLI renders ReorgProposalMessage

**Files:**
- Modify: `pal/cli.py`
- Modify: `tests/test_cli_research_proposal.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_cli_research_proposal.py`:

```python
def test_format_reorg_proposal_includes_ops_and_preview():
    from pal.cli import format_reorg_proposal
    from pal.protocol import ReorgProposalMessage
    msg = ReorgProposalMessage(
        proposal_id="xyz",
        operations=[
            {"type": "move", "src": "AI-Agents/old.md", "dst": "AI-Agents/new.md"},
            {"type": "merge", "src": "AI-Security/a.md", "dst": "AI-Security/b.md"},
        ],
        rationale="clean up names and dedupe",
        references_preview=5,
    )
    text = format_reorg_proposal(msg)
    assert "reorg" in text.lower()
    assert "[move]" in text
    assert "[merge]" in text
    assert "AI-Agents/old.md" in text
    assert "AI-Agents/new.md" in text
    assert "AI-Security/a.md" in text
    assert "AI-Security/b.md" in text
    assert "clean up names and dedupe" in text
    assert "5" in text  # references_preview
    assert "[a]" in text.lower() or "approve" in text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cli_research_proposal.py -v -k format_reorg`
Expected: FAIL — `cannot import name 'format_reorg_proposal'`.

- [ ] **Step 3: Add format_reorg_proposal and dispatch branch**

In `pal/cli.py`:

Add `ReorgProposalMessage` to the protocol imports.

Add `format_reorg_proposal` next to the existing formatters:

```python
def format_reorg_proposal(msg: "ReorgProposalMessage") -> str:
    """Render a reorg proposal approval prompt. Pure formatter."""
    lines = [
        "",
        "────────── PAL proposes reorg ──────────",
        f"  Operations ({len(msg.operations)}):",
    ]
    for op in msg.operations:
        op_type = op.get("type", "?")
        src = op.get("src", "?")
        dst = op.get("dst", "?")
        tag = f"[{op_type}]"
        lines.append(f"    {tag:<8} {src}")
        lines.append(f"             -> {dst}")
    lines.extend([
        f"  Rationale: {msg.rationale}",
        f"  Would rewrite {msg.references_preview} link references.",
        "  [a]pprove  [d]ecline  [e]dit",
        "> ",
    ])
    return "\n".join(lines)
```

Add a new dispatch branch in the chat message loop, right after the `CompileProposalMessage` branch:

```python
                    elif isinstance(msg, ReorgProposalMessage):
                        if live is not None:
                            live.stop()
                            live = None
                        print(format_reorg_proposal(msg), end="", flush=True)
                        loop = asyncio.get_running_loop()
                        choice = (await loop.run_in_executor(None, input)).strip().lower()
                        if choice in ("a", "approve"):
                            response = ResearchApprovalResponseMessage(
                                proposal_id=msg.proposal_id, decision="approve"
                            )
                        elif choice in ("e", "edit"):
                            response = ResearchApprovalResponseMessage(
                                proposal_id=msg.proposal_id, decision="decline"
                            )
                        else:
                            response = ResearchApprovalResponseMessage(
                                proposal_id=msg.proposal_id, decision="decline"
                            )
                        await client.send(response)
                        continue
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_cli_research_proposal.py -v && .venv/bin/pytest -x`
Expected: new test passes, no regressions.

- [ ] **Step 5: Commit**

```bash
git add pal/cli.py tests/test_cli_research_proposal.py
git commit -m "feat: CLI renders reorg proposal prompt"
```

---

## Task R11: Discord renders ReorgProposalMessage

**Files:**
- Modify: `pal/discord_interactions.py`
- Modify: `tests/test_discord_interactions.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_discord_interactions.py`:

```python
from pal.discord_interactions import build_reorg_proposal_embed
from pal.protocol import ReorgProposalMessage


def test_reorg_embed_includes_operations_and_references_count():
    msg = ReorgProposalMessage(
        proposal_id="xyz-1",
        operations=[
            {"type": "move", "src": "A.md", "dst": "B.md"},
            {"type": "merge", "src": "C.md", "dst": "D.md"},
        ],
        rationale="test rationale",
        references_preview=4,
    )
    embed, view = build_reorg_proposal_embed(msg)
    assert "reorg" in embed.title.lower()
    ops_field = next(f for f in embed.fields if "Operations" in f.name)
    assert "[move]" in ops_field.value
    assert "[merge]" in ops_field.value
    assert "A.md" in ops_field.value
    assert "B.md" in ops_field.value
    refs_field = next(f for f in embed.fields if "rewrite" in f.name.lower() or "link" in f.name.lower())
    assert "4" in refs_field.value

    custom_ids = [child.custom_id for child in view.children]
    assert "reorg:approve:xyz-1" in custom_ids
    assert "reorg:decline:xyz-1" in custom_ids
    assert "reorg:edit:xyz-1" in custom_ids


def test_reorg_embed_truncates_long_operation_lists():
    ops = [
        {"type": "move", "src": f"a-{i}.md", "dst": f"b-{i}.md"}
        for i in range(15)
    ]
    msg = ReorgProposalMessage(
        proposal_id="p1",
        operations=ops,
        rationale="r",
        references_preview=0,
    )
    embed, view = build_reorg_proposal_embed(msg)
    ops_field = next(f for f in embed.fields if "Operations" in f.name)
    assert "+5 more" in ops_field.value or "+5" in ops_field.value
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_discord_interactions.py -v -k reorg_embed`
Expected: FAIL — `cannot import name 'build_reorg_proposal_embed'`.

- [ ] **Step 3: Add build_reorg_proposal_embed**

In `pal/discord_interactions.py`, add imports at the top if not already present:

```python
from pal.protocol import (
    CompileProposalMessage,
    ResearchProposalMessage,
    ReorgProposalMessage,
)
```

Add a constant for the ops display cap near the existing `_COMPILE_PATHS_DISPLAY_CAP`:

```python
_REORG_OPS_DISPLAY_CAP = 10
```

Add the builder function:

```python
def build_reorg_proposal_embed(
    msg: ReorgProposalMessage,
) -> tuple[discord.Embed, discord.ui.View]:
    """Pure builder: returns the embed and a View with three buttons."""
    embed = discord.Embed(
        title="PAL proposes reorg",
        color=discord.Color.orange(),
    )
    total = len(msg.operations)
    shown = msg.operations[:_REORG_OPS_DISPLAY_CAP]
    lines: list[str] = []
    for op in shown:
        op_type = op.get("type", "?")
        src = op.get("src", "?")
        dst = op.get("dst", "?")
        lines.append(f"[{op_type}] {src}")
        lines.append(f"         → {dst}")
    if total > _REORG_OPS_DISPLAY_CAP:
        lines.append(f"+{total - _REORG_OPS_DISPLAY_CAP} more")
    embed.add_field(
        name=f"Operations ({total})",
        value="\n".join(lines) if lines else "(empty)",
        inline=False,
    )
    embed.add_field(name="Rationale", value=msg.rationale, inline=False)
    embed.add_field(
        name="Link rewrites",
        value=str(msg.references_preview),
        inline=False,
    )

    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        style=discord.ButtonStyle.success,
        label="Approve",
        emoji="✅",
        custom_id=f"reorg:approve:{msg.proposal_id}",
    ))
    view.add_item(discord.ui.Button(
        style=discord.ButtonStyle.danger,
        label="Decline",
        emoji="❌",
        custom_id=f"reorg:decline:{msg.proposal_id}",
    ))
    view.add_item(discord.ui.Button(
        style=discord.ButtonStyle.secondary,
        label="Edit",
        emoji="✏️",
        custom_id=f"reorg:edit:{msg.proposal_id}",
    ))
    return embed, view
```

Extend `parse_button_custom_id` to accept `"reorg"` kind. The current implementation uses a whitelist:

```python
    if kind not in ("research", "compile", "reorg"):
        return None
```

Same for `parse_modal_custom_id`:

```python
    if kind not in ("research", "compile", "reorg"):
        return None
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_discord_interactions.py -v && .venv/bin/pytest -x`
Expected: new reorg tests pass plus existing tests (some of which re-assert the custom_id parser's kind whitelist — check those still pass).

- [ ] **Step 5: Commit**

```bash
git add pal/discord_interactions.py tests/test_discord_interactions.py
git commit -m "feat: Discord embed builder for reorg proposals"
```

---

## Task R12: Discord stream processor + interaction handler wire reorg

**Files:**
- Modify: `pal/discord_interactions.py` (`DiscordStreamProcessor`)
- Modify: `pal/discord_adapter.py` (`_handle_button_interaction`)

- [ ] **Step 1: Extend DiscordStreamProcessor to handle ReorgProposalMessage**

In `pal/discord_interactions.py`, find the `DiscordStreamProcessor.run` method. It already handles `ResearchProposalMessage` and `CompileProposalMessage`. Add a branch for `ReorgProposalMessage`:

```python
            elif isinstance(msg, ReorgProposalMessage):
                await self._handle_reorg_proposal(msg)
```

Add the handler method on the class:

```python
    async def _handle_reorg_proposal(
        self, msg: ReorgProposalMessage,
    ) -> None:
        embed, view = build_reorg_proposal_embed(msg)
        posted = await self.channel.send(embed=embed, view=view)
        ctx = ProposalContext(
            proposal_id=msg.proposal_id,
            kind="reorg",
            triggerer_id=self.triggerer_id,
            rationale=msg.rationale,
            discord_message_id=posted.id,
            channel_id=getattr(self.channel, "id", None),
        )
        # Stash the operations list on the context for the edit modal default
        # (even though v1 edit-as-decline doesn't use a modal, subsequent
        # versions can).
        ctx_operations = [dict(op) for op in msg.operations]
        # ProposalContext doesn't have an operations field; stash as a
        # generic attribute for v1.
        setattr(ctx, "operations", ctx_operations)
        self.bot.active_proposals[msg.proposal_id] = ctx
        self.current_proposal_id = msg.proposal_id
        self.current_proposal_message = posted
```

- [ ] **Step 2: Extend button handler in discord_adapter.py to accept reorg kind**

Use the Grep tool to find `_handle_button_interaction` in `pal/discord_adapter.py`. The existing logic checks `if kind == "research" ...` and `if kind == "compile" ...`.

The existing flow builds an edit modal via `build_research_edit_modal` or `build_compile_edit_modal`. For reorg, v1 maps `[e]dit` to decline without a modal. Extend the button handler:

Find the block that, on `action == "edit"`, calls `build_research_edit_modal` or `build_compile_edit_modal`. Add a reorg branch that does NOT build a modal — it sends a decline instead:

```python
        if action == "edit":
            if kind == "research":
                modal = build_research_edit_modal(ctx)
                await interaction.response.send_modal(modal)
                return
            elif kind == "compile":
                modal = build_compile_edit_modal(ctx)
                await interaction.response.send_modal(modal)
                return
            else:  # reorg — v1 edit-as-decline, no modal
                try:
                    client = await self.connections.get_client(str(interaction.user.id))
                    await client.send(ResearchApprovalResponseMessage(
                        proposal_id=proposal_id,
                        decision="decline",
                    ))
                except Exception as exc:
                    logger.exception("Failed to send reorg edit-decline: %s", exc)
                    await interaction.response.send_message(
                        "Something went wrong. Try again.", ephemeral=True,
                    )
                    return
                try:
                    await interaction.response.edit_message(
                        content="✏️ Edit requested (reorg); re-propose in chat",
                        view=None,
                    )
                except discord.HTTPException:
                    pass
                return
```

The approve/decline path for reorg is identical to the existing approve/decline code for research/compile — the kind whitelist check from R11 makes the parser accept reorg custom_ids, and the generic send-approval-response block handles them uniformly.

- [ ] **Step 3: Run tests**

Run: `.venv/bin/pytest -x`
Expected: all tests pass. The stream-processor handling of `ReorgProposalMessage` is structurally equivalent to the existing compile handler; if the existing stream-processor tests still pass, the extension is safe.

- [ ] **Step 4: Commit**

```bash
git add pal/discord_interactions.py pal/discord_adapter.py
git commit -m "feat: DiscordStreamProcessor + button handler wire reorg proposals"
```

---

## Task R13: Manual smoke test

**Files:** none modified

- [ ] **Step 1: Pull latest and restart services**

```bash
cd /mnt/secondary/PAL   # or your deployment path
git pull
sudo systemctl restart pal-daemon
sudo systemctl restart pal-discord
```

- [ ] **Step 2: Move-only smoke test (CLI)**

Start a chat session. Ask:

```
PAL, rename AI-Agents/github---codeaashuclaude-code-...-f3c17ca2.md
to AI-Agents/claude-code.md.
```

Expected:
1. PAL emits a `propose_reorg` tool call with one move op.
2. CLI shows the proposal prompt with the `[move]` tag, src, dst, rationale, and "Would rewrite N link references."
3. Type `a` to approve.
4. PAL emits `reorg`.
5. Tool executes: references rewritten, file renamed, git commits landed.
6. Final report: `total: 1, ok: 1, references_rewritten: N`.

Verify:
- `git log --oneline -5` shows at least one `reorg: move ...` commit.
- The old filename is gone; the new filename is present.
- Any article that previously referenced the old path now points at the new path.

- [ ] **Step 3: Merge smoke test (CLI)**

Identify two articles on the same topic (or seed the vault with two duplicates). Ask:

```
PAL, merge AI-Security/duplicate-a.md into AI-Security/canonical.md.
```

Expected:
1. PAL emits `propose_reorg` with one merge op.
2. Approval prompt shows `[merge]`, paths, rationale, reference count.
3. Approve. PAL emits `reorg`.
4. The LLM merge runs (a few seconds on the local model).
5. Report: `total: 1, ok: 1`. If status is `insufficient`, src/dst are both intact and the user sees a clear reason.

Verify:
- `raw/archived/duplicate-a.archived.md` (or similar) exists.
- `AI-Security/duplicate-a.md` is gone.
- `AI-Security/canonical.md` contains folded-in content from the source.
- References to the source now point at the canonical.

- [ ] **Step 4: Mixed batch smoke test (Discord)**

In Discord:

```
@PAL please clean up a few things: rename the long claude-code filename,
rename the long llm-context-limits filename, and merge the MCP notes
article into the MCP threat-modeling article.
```

Expected:
1. PAL researches/inspects the vault (multiple `list_directory` and `search_vault` calls).
2. PAL emits `propose_reorg` with 3 operations.
3. Discord embed renders with `[move]`, `[move]`, `[merge]` tags, each src/dst, rationale, link-rewrite count.
4. Click ✅ Approve.
5. Thread created with "reorg: 3 operations" or similar.
6. Progress posts to thread per op.
7. Final report in channel.

Verify operations landed, check git log.

- [ ] **Step 5: Decline smoke test**

Ask for a reorg. At the Discord approval, click ❌ Decline. Verify PAL acknowledges the decline and doesn't call `reorg`.

- [ ] **Step 6: Edit-as-decline smoke test**

Ask for a reorg. At the Discord approval, click ✏️ Edit. Verify the message updates to "Edit requested; re-propose in chat" and PAL's next message indicates it's waiting for new instructions.

- [ ] **Step 7: Triggerer-only authorization (if a second account is available)**

Account A asks for a reorg in a channel. Account B clicks Approve. Verify ephemeral error: "This proposal is for @A."

- [ ] **Step 8: Invalid-batch smoke test**

Ask PAL to move a file that doesn't exist:

```
PAL, rename NonExistent.md to Something.md.
```

Expected:
- PAL calls `propose_reorg`.
- `propose_reorg` pre-validates, fails, returns error string with "src does not exist."
- PAL reports the error to you without posting an approval prompt.

- [ ] **Step 9: Capture notes**

Record any behavioral drift, filesystem-surprise moments, or prompt-tuning observations. Likely items:
- Does the model understand which articles to merge without extra prompting?
- Does the approval embed truncation read well for large batches?
- Does the LLM-driven merge produce sensible output on your local model?

Commit any captured notes:

```bash
git add docs/<notes-path>.md
git commit -m "docs: reorg smoke-test observations"
```

---

## Self-review

**Spec coverage:**
- `Compiler.merge_into_existing` extraction → R1.
- `ApprovalRegistry` reorg kind + operations field → R2.
- `ReorgProposalMessage` protocol → R3.
- `Reorganizer` validation + count_references → R4.
- `Reorganizer` move execution → R5.
- `Reorganizer` merge execution (calls `Compiler.merge_into_existing`) → R6.
- `propose_reorg` tool → R7.
- `reorg` tool → R8.
- Daemon wiring → R9.
- CLI rendering → R10.
- Discord embed builder + stream processor + button handler → R11, R12.
- Manual smoke test → R13.

**Placeholder scan:** no TBDs, TODOs, vague edge-case language. Every code step shows code.

**Type consistency:** `Reorganizer` method names consistent from R4 onward (`validate_operations`, `count_references`, `execute_operations`, `execute_operations_async`, `_execute_move`, `_execute_merge`, `_rewrite_references`). `ProposalKind` literal, `operations` field, and `ReorgProposalMessage` fields used consistently in downstream tasks.

**One note:** R6 introduces `execute_operations_async` as the canonical executor, keeping `execute_operations` as a sync wrapper that refuses merge ops. R7 and R8 both call `execute_operations_async`, so the async path is the live one. The sync wrapper exists only to keep R5's first-pass tests passing without churn; future cleanup could drop it.
