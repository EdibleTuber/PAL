# Compile Chat Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the existing `/compile` pipeline to chat as three new tools — direct `compile_summary`, consent-gated `propose_compile_batch` + `compile_batch` — so the model can promote research findings into the wiki without the user leaving the chat flow.

**Architecture:** Extract `Daemon._compile_one` into a `Compiler` class (`pal/compiler.py`). Generalize `ApprovalRegistry.ResearchProposal` to `Proposal` with a `kind` field so compile proposals reuse the same lifecycle (create/approve/decline/consume/edit/expire). Add a `CompileProposalMessage` protocol type. Wire three new handlers in `ToolExecutor`. Update `pal/cli.py` to render compile proposals. Tune the chat prompt to route wiki-promotion requests through the new tools instead of `create_file`.

**Tech Stack:** Python 3.11+, asyncio, pytest, existing PAL modules (`Researcher`, `ApprovalRegistry`, `WikiManager`, `Categorizer`, `InferenceClient`).

**Spec:** `docs/superpowers/specs/2026-04-14-compile-chat-tools-design.md`

---

## Task 1: Rename ResearchProposal → Proposal with kind field

Mechanical rename plus a `kind` field on the dataclass. Preserves all existing behavior. No compile support yet — that lands in Task 2.

**Files:**
- Modify: `pal/approval_registry.py`
- Modify: `tests/test_approval_registry.py`
- Modify: `pal/tools.py` (only if it references `ResearchProposal` by name — it shouldn't, but verify)

- [ ] **Step 1: Write failing test for the new kind field**

Append to `tests/test_approval_registry.py`:

```python
def test_create_proposal_defaults_to_research_kind():
    registry = ApprovalRegistry()
    pid = registry.create_proposal(topic="t", depth=3, rationale="r")
    proposal = registry.get(pid)
    assert proposal.kind == "research"


def test_proposal_is_new_dataclass_name():
    from pal.approval_registry import Proposal
    registry = ApprovalRegistry()
    pid = registry.create_proposal(topic="t", depth=3, rationale="r")
    assert isinstance(registry.get(pid), Proposal)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_approval_registry.py -v -k "kind or new_dataclass_name"`
Expected: FAIL — `AttributeError` on `.kind` and `ImportError` on `Proposal`.

- [ ] **Step 3: Rename and add kind field**

In `pal/approval_registry.py`:

Rename the class `ResearchProposal` to `Proposal`. Add `kind: Literal["research", "compile"] = "research"` as a field, placed before `event` (which has a default). Also add `ProposalKind = Literal["research", "compile"]` as a module-level type alias alongside `ProposalStatus`.

Resulting class:

```python
ProposalKind = Literal["research", "compile"]


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
    event: asyncio.Event = field(default_factory=asyncio.Event, repr=False, compare=False)
```

In every construction site inside `ApprovalRegistry` (`create_proposal` and `edit`), pass `kind="research"` explicitly until Task 2 adds compile support — keep behavior identical.

Preserve `ResearchProposal` as a deprecated alias so any external imports keep working:

```python
ResearchProposal = Proposal  # deprecated alias; remove after callers migrate
```

- [ ] **Step 4: Update existing tests that import ResearchProposal**

In `tests/test_approval_registry.py`, the existing import statement:

```python
from pal.approval_registry import ApprovalRegistry, ResearchProposal
```

Leave it as-is. The alias makes it keep working. No change needed.

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/pytest -x`
Expected: all tests pass, including the two new ones.

- [ ] **Step 6: Commit**

```bash
git add pal/approval_registry.py tests/test_approval_registry.py
git commit -m "refactor: rename ResearchProposal to Proposal with kind field"
```

---

## Task 2: create_proposal accepts kind="compile" with summary_paths

Extend `create_proposal` and `edit` to support compile proposals. Research proposals keep working unchanged.

**Files:**
- Modify: `pal/approval_registry.py`
- Modify: `tests/test_approval_registry.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_approval_registry.py`:

```python
def test_create_proposal_with_compile_kind():
    registry = ApprovalRegistry()
    paths = ["raw/summaries/a.md", "raw/summaries/b.md"]
    pid = registry.create_proposal(
        kind="compile",
        summary_paths=paths,
        rationale="promote research findings",
    )
    proposal = registry.get(pid)
    assert proposal.kind == "compile"
    assert proposal.summary_paths == paths
    assert proposal.rationale == "promote research findings"
    assert proposal.status == "pending"


def test_edit_compile_proposal_carries_kind_and_paths():
    registry = ApprovalRegistry()
    old_pid = registry.create_proposal(
        kind="compile",
        summary_paths=["raw/summaries/a.md"],
        rationale="r",
    )
    new_pid = registry.edit(
        old_pid,
        summary_paths=["raw/summaries/a.md", "raw/summaries/b.md"],
    )
    assert new_pid is not None
    new = registry.get(new_pid)
    assert new.kind == "compile"
    assert new.summary_paths == ["raw/summaries/a.md", "raw/summaries/b.md"]
    assert new.status == "approved"


def test_create_compile_proposal_rejects_empty_paths():
    registry = ApprovalRegistry()
    import pytest
    with pytest.raises(ValueError):
        registry.create_proposal(
            kind="compile",
            summary_paths=[],
            rationale="r",
        )


def test_create_research_proposal_without_topic_raises():
    registry = ApprovalRegistry()
    import pytest
    with pytest.raises(ValueError):
        registry.create_proposal(kind="research", rationale="r")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_approval_registry.py -v -k "compile or kind"`
Expected: new tests FAIL (signature doesn't accept `kind`/`summary_paths`).

- [ ] **Step 3: Add summary_paths field and kind-aware create_proposal**

In `pal/approval_registry.py`:

Add `summary_paths: Optional[list[str]] = None` to `Proposal`, placed after `successor_id`.

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
    ) -> str:
        if kind == "research" and not topic:
            raise ValueError("research proposals require a non-empty topic")
        if kind == "compile":
            if not summary_paths:
                raise ValueError("compile proposals require a non-empty summary_paths list")

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
        )
        return proposal_id
```

Note: making params keyword-only (`*,`) prevents positional-arg accidents and lets us keep the old call sites (`create_proposal(topic="t", depth=3, rationale="r")`) working unchanged since `kind` defaults to `"research"`.

Extend `edit` to carry kind and summary_paths forward. New signature:

```python
    def edit(
        self,
        proposal_id: str,
        *,
        new_topic: Optional[str] = None,
        new_depth: Optional[int] = None,
        summary_paths: Optional[list[str]] = None,
    ) -> Optional[str]:
```

Body:

```python
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
        )
        new_proposal.event.set()
        self._proposals[new_id] = new_proposal
        old.successor_id = new_id
        return new_id
```

- [ ] **Step 4: Check existing callers in pal/tools.py**

The existing `_propose_research` handler in `pal/tools.py` calls `self.approval_registry.create_proposal(topic=..., depth=..., rationale=...)`. These calls stay working because `kind` defaults to `"research"`. Verify by scanning the file — no change required.

If there are positional calls like `create_proposal("topic", 3, "rationale")` anywhere, they'll break because params are now keyword-only. Grep first:

Run: `.venv/bin/grep -n "create_proposal" pal/ tests/`

Update any positional callers to keyword form.

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/pytest -x`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add pal/approval_registry.py tests/test_approval_registry.py
git commit -m "feat: ApprovalRegistry supports compile-kind proposals with summary_paths"
```

---

## Task 3: CompileProposalMessage protocol

**Files:**
- Modify: `pal/protocol.py`
- Modify: `tests/test_protocol.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_protocol.py`:

```python
def test_compile_proposal_message_roundtrip():
    from pal.protocol import CompileProposalMessage
    msg = CompileProposalMessage(
        proposal_id="abc",
        summary_paths=["raw/summaries/a.md", "raw/summaries/b.md"],
        rationale="promote findings",
    )
    line = encode_message(msg)
    decoded = decode_message(line.strip())
    assert isinstance(decoded, CompileProposalMessage)
    assert decoded.proposal_id == "abc"
    assert decoded.summary_paths == ["raw/summaries/a.md", "raw/summaries/b.md"]
    assert decoded.rationale == "promote findings"
    assert decoded.type == "compile_proposal"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_protocol.py -v -k compile`
Expected: FAIL with `ImportError: cannot import name 'CompileProposalMessage'`.

- [ ] **Step 3: Add the message type**

In `pal/protocol.py`, add after `ResearchApprovalResponseMessage`:

```python
@dataclass
class CompileProposalMessage:
    proposal_id: str
    summary_paths: list[str]
    rationale: str
    type: str = "compile_proposal"
```

Add to `_MESSAGE_TYPES`:

```python
    "compile_proposal": CompileProposalMessage,
```

Extend `Message` union:

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
)
```

Also update the module docstring's message-type list to include the new type, matching the existing style.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_protocol.py -v`
Expected: all tests pass including the new one.

- [ ] **Step 5: Commit**

```bash
git add pal/protocol.py tests/test_protocol.py
git commit -m "feat: CompileProposalMessage for compile approval prompts"
```

---

## Task 4: Extract Compiler class from daemon

Move `_compile_one` out of `Daemon` into `pal/compiler.py` without changing its behavior. Daemon's `/compile` and `/compile-batch` slash commands route through the new class.

**Files:**
- Create: `pal/compiler.py`
- Create: `tests/test_compiler.py`
- Modify: `pal/daemon.py`

- [ ] **Step 1: Read the existing _compile_one**

The method lives at `pal/daemon.py:943` (approximately). Read it in full before extracting so the move is behavior-preserving. Note the dependencies it uses on `self`: `config.vault_path`, `wiki`, `inference`, `categorizer`, `prompt_builder`. The return dict keys are `status`, `title`, `article_path_rel`, `compiled_truth`, `reason`.

- [ ] **Step 2: Write a failing test for the new Compiler class**

Create `tests/test_compiler.py`:

```python
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pal.compiler import Compiler


@pytest.mark.asyncio
async def test_compile_one_returns_not_found_for_missing_summary(tmp_path: Path):
    wiki = MagicMock()
    inference = MagicMock()
    categorizer = MagicMock()
    prompt_builder = MagicMock()
    prompt_builder.build = MagicMock(return_value="")

    compiler = Compiler(
        vault_path=tmp_path,
        wiki=wiki,
        inference=inference,
        categorizer=categorizer,
        prompt_builder=prompt_builder,
    )
    result = await compiler.compile_one("raw/summaries/does-not-exist.md")
    assert result["status"] == "not_found"


@pytest.mark.asyncio
async def test_compile_one_rejects_path_traversal(tmp_path: Path):
    compiler = Compiler(
        vault_path=tmp_path,
        wiki=MagicMock(),
        inference=MagicMock(),
        categorizer=MagicMock(),
        prompt_builder=MagicMock(),
    )
    result = await compiler.compile_one("../escape.md")
    assert result["status"] == "invalid_path"


@pytest.mark.asyncio
async def test_compile_one_rejects_absolute_path(tmp_path: Path):
    compiler = Compiler(
        vault_path=tmp_path,
        wiki=MagicMock(),
        inference=MagicMock(),
        categorizer=MagicMock(),
        prompt_builder=MagicMock(),
    )
    result = await compiler.compile_one("/etc/passwd")
    assert result["status"] == "invalid_path"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_compiler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pal.compiler'`.

- [ ] **Step 4: Create pal/compiler.py by moving the logic**

Create `pal/compiler.py`. Copy the entire body of `Daemon._compile_one` into a method on a new `Compiler` class, replacing every `self.config.vault_path` with `self.vault_path`, every `self.wiki` with `self.wiki`, etc. The constructor holds the five dependencies as plain attributes.

```python
"""Compiler — promote raw summaries into grounded wiki articles.

Extracted from pal.daemon so both the /compile slash command and the
chat compile tools call the same implementation.
"""
from pathlib import Path
from typing import Any

from pal.frontmatter import parse_frontmatter
from pal.article import parse_article, find_existing_article


class Compiler:
    def __init__(
        self,
        vault_path: Path,
        wiki,              # WikiManager
        inference,         # InferenceClient
        categorizer,       # Categorizer
        prompt_builder,    # SystemPromptBuilder
    ) -> None:
        self.vault_path = vault_path
        self.wiki = wiki
        self.inference = inference
        self.categorizer = categorizer
        self.prompt_builder = prompt_builder

    async def compile_one(self, summary_path: str) -> dict[str, Any]:
        """Compile a single summary into a wiki article.

        Returns a dict with status, title, article_path_rel, compiled_truth,
        reason. Status values: ok, merged, insufficient, not_found,
        invalid_path, error.
        """
        # Path traversal guard
        if ".." in summary_path.split("/") or summary_path.startswith("/"):
            return {"status": "invalid_path", "reason": f"Invalid path: {summary_path}"}

        full_path = self.vault_path / summary_path
        if not full_path.exists():
            return {"status": "not_found", "reason": f"File not found: {summary_path}"}

        # Resolve + boundary check
        try:
            resolved = full_path.resolve()
            vault_resolved = self.vault_path.resolve()
            if not str(resolved).startswith(str(vault_resolved) + "/"):
                return {"status": "invalid_path", "reason": f"Invalid path: {summary_path}"}
        except Exception:
            return {"status": "invalid_path", "reason": f"Invalid path: {summary_path}"}

        # ... (rest of the existing _compile_one body, referenced via self.* on
        # this class rather than on Daemon)
```

Copy the remainder of `_compile_one` verbatim, rewriting the `self.*` references to match the Compiler fields. The existing logic handles categorization, topic matching, prompt building, inference, merge vs new decision, write, and archive — all of that moves over unchanged.

- [ ] **Step 5: Update Daemon to use Compiler**

In `pal/daemon.py`:

Add import:
```python
from pal.compiler import Compiler
```

In `Daemon.__init__`, after `self.categorizer = Categorizer(self.inference)`, add:

```python
        self.compiler = Compiler(
            vault_path=config.vault_path,
            wiki=self.wiki,
            inference=self.inference,
            categorizer=self.categorizer,
            prompt_builder=self.prompt_builder,
        )
```

Delete the `_compile_one` method from `Daemon` (it's now on `Compiler`).

In `_handle_compile` and `_handle_compile_batch`, change every `await self._compile_one(...)` call to `await self.compiler.compile_one(...)`.

- [ ] **Step 6: Run all tests**

Run: `.venv/bin/pytest -x`
Expected: all existing tests pass (including any for `/compile` behavior), plus the 3 new tests in `test_compiler.py`.

- [ ] **Step 7: Commit**

```bash
git add pal/compiler.py tests/test_compiler.py pal/daemon.py
git commit -m "refactor: extract Compiler class from daemon for chat-tool reuse"
```

---

## Task 5: ToolExecutor constructor accepts compiler

Widen the constructor. Tool handlers land in later tasks.

**Files:**
- Modify: `pal/tools.py`
- Create: `tests/test_chat_compile_tools.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_chat_compile_tools.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock

from pal.approval_registry import ApprovalRegistry
from pal.tools import ToolExecutor


def test_tool_executor_accepts_compiler(tmp_path: Path):
    compiler = MagicMock()
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        compiler=compiler,
    )
    assert executor.compiler is compiler
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_chat_compile_tools.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'compiler'`.

- [ ] **Step 3: Extend ToolExecutor.__init__**

In `pal/tools.py`, add `compiler: "Compiler | None" = None` to the constructor signature, alongside the other optional deps. Store as `self.compiler = compiler`.

Add the forward-ref import:

```python
if TYPE_CHECKING:
    from pal.compiler import Compiler
```

(Alongside the existing TYPE_CHECKING imports for `ApprovalRegistry`, `WebSearchClient`, `Researcher`.)

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_chat_compile_tools.py -v && .venv/bin/pytest -x`
Expected: new test passes, no regressions.

- [ ] **Step 5: Commit**

```bash
git add pal/tools.py tests/test_chat_compile_tools.py
git commit -m "refactor: extend ToolExecutor to accept Compiler dependency"
```

---

## Task 6: compile_summary tool (direct)

**Files:**
- Modify: `pal/tools.py`
- Modify: `tests/test_chat_compile_tools.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_chat_compile_tools.py`:

```python
import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_compile_summary_happy_path(tmp_path):
    compiler = MagicMock()

    async def fake_compile_one(path):
        return {
            "status": "ok",
            "title": "Example Article",
            "article_path_rel": "AI-Agents/Example.md",
        }

    compiler.compile_one = fake_compile_one
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        compiler=compiler,
    )
    output = await executor.run_async(
        "compile_summary", {"summary_path": "raw/summaries/foo.md"}
    )
    assert '"status": "ok"' in output
    assert '"title": "Example Article"' in output
    assert "AI-Agents/Example.md" in output


@pytest.mark.asyncio
async def test_compile_summary_not_found_propagates(tmp_path):
    compiler = MagicMock()

    async def fake_compile_one(path):
        return {"status": "not_found", "reason": f"File not found: {path}"}

    compiler.compile_one = fake_compile_one
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        compiler=compiler,
    )
    output = await executor.run_async(
        "compile_summary", {"summary_path": "raw/summaries/missing.md"}
    )
    assert '"status": "not_found"' in output


@pytest.mark.asyncio
async def test_compile_summary_requires_path(tmp_path):
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        compiler=MagicMock(),
    )
    output = await executor.run_async("compile_summary", {})
    assert "Error" in output and "summary_path" in output


@pytest.mark.asyncio
async def test_compile_summary_unavailable_without_compiler(tmp_path):
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        compiler=None,
    )
    output = await executor.run_async(
        "compile_summary", {"summary_path": "raw/summaries/foo.md"}
    )
    assert "not available" in output.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_chat_compile_tools.py -v -k compile_summary`
Expected: FAIL with `Unknown tool: compile_summary`.

- [ ] **Step 3: Add tool definition and handler**

In `pal/tools.py`, append to `TOOL_DEFINITIONS`:

```python
    {
        "type": "function",
        "function": {
            "name": "compile_summary",
            "description": (
                "Promote a single raw summary into a grounded wiki "
                "article. Categorizes, merges with any existing article "
                "on the same topic, and archives the raw+summary on "
                "success. Use when the user wants one specific summary "
                "ingested. For batches, use propose_compile_batch."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary_path": {
                        "type": "string",
                        "description": "Relative path under raw/summaries/ (e.g. 'raw/summaries/foo.md').",
                    },
                },
                "required": ["summary_path"],
            },
        },
    },
```

Extend `run_async` dispatch:

```python
        if name == "compile_summary":
            return await self._compile_summary(arguments)
```

Add the handler:

```python
    async def _compile_summary(self, arguments: dict) -> str:
        import json as _json
        summary_path = arguments.get("summary_path", "").strip()
        if not summary_path:
            return "Error: 'summary_path' parameter is required."
        if self.compiler is None:
            return "Error: compile is not available in this session."
        result = await self.compiler.compile_one(summary_path)
        return _json.dumps(result)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_chat_compile_tools.py -v && .venv/bin/pytest -x`
Expected: 4 new tests pass, no regressions.

- [ ] **Step 5: Commit**

```bash
git add pal/tools.py tests/test_chat_compile_tools.py
git commit -m "feat: compile_summary chat tool for single-article promotion"
```

---

## Task 7: propose_compile_batch tool

Blocking tool that emits `CompileProposalMessage` and awaits user approval.

**Files:**
- Modify: `pal/tools.py`
- Modify: `tests/test_chat_compile_tools.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_chat_compile_tools.py`:

```python
import asyncio


@pytest.mark.asyncio
async def test_propose_compile_batch_approved(tmp_path):
    registry = ApprovalRegistry()
    emitted = []
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        proposal_emitter=emitted.append,
    )

    async def approve_later():
        for _ in range(50):
            if emitted:
                break
            await asyncio.sleep(0.01)
        assert emitted, "proposal was not emitted"
        registry.approve(emitted[0].proposal_id)

    asyncio.create_task(approve_later())
    output = await executor.run_async(
        "propose_compile_batch",
        {
            "summary_paths": ["raw/summaries/a.md", "raw/summaries/b.md"],
            "rationale": "promote findings",
        },
    )
    assert '"status": "approved"' in output
    assert emitted[0].summary_paths == ["raw/summaries/a.md", "raw/summaries/b.md"]
    assert emitted[0].rationale == "promote findings"
    # And the emitted message is a CompileProposalMessage, not research.
    from pal.protocol import CompileProposalMessage
    assert isinstance(emitted[0], CompileProposalMessage)


@pytest.mark.asyncio
async def test_propose_compile_batch_declined(tmp_path):
    registry = ApprovalRegistry()
    emitted = []
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        proposal_emitter=emitted.append,
    )

    async def decline_later():
        for _ in range(50):
            if emitted:
                break
            await asyncio.sleep(0.01)
        registry.decline(emitted[0].proposal_id)

    asyncio.create_task(decline_later())
    output = await executor.run_async(
        "propose_compile_batch",
        {"summary_paths": ["raw/summaries/a.md"], "rationale": "r"},
    )
    assert '"status": "declined"' in output


@pytest.mark.asyncio
async def test_propose_compile_batch_rejects_empty_paths(tmp_path):
    registry = ApprovalRegistry()
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        proposal_emitter=MagicMock(),
    )
    output = await executor.run_async(
        "propose_compile_batch",
        {"summary_paths": [], "rationale": "r"},
    )
    assert "Error" in output
    assert "empty" in output.lower() or "at least one" in output.lower()


@pytest.mark.asyncio
async def test_propose_compile_batch_requires_rationale(tmp_path):
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=ApprovalRegistry(),
        proposal_emitter=MagicMock(),
    )
    output = await executor.run_async(
        "propose_compile_batch",
        {"summary_paths": ["raw/summaries/a.md"]},
    )
    assert "Error" in output and "rationale" in output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_chat_compile_tools.py -v -k propose_compile_batch`
Expected: FAIL with `Unknown tool`.

- [ ] **Step 3: Add tool definition and handler**

In `pal/tools.py`, append to `TOOL_DEFINITIONS`:

```python
    {
        "type": "function",
        "function": {
            "name": "propose_compile_batch",
            "description": (
                "Propose compiling multiple raw summaries into wiki "
                "articles. Blocks until the user approves, declines, "
                "or edits in the CLI. Use for multi-summary promotion. "
                "After approval, immediately call compile_batch with "
                "the returned proposal_id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Relative paths under raw/summaries/ (non-empty).",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "One-line reason shown to the user in the approval prompt.",
                    },
                },
                "required": ["summary_paths", "rationale"],
            },
        },
    },
```

Extend `run_async` dispatch:

```python
        if name == "propose_compile_batch":
            return await self._propose_compile_batch(arguments)
```

Add handler:

```python
    async def _propose_compile_batch(self, arguments: dict) -> str:
        import json as _json
        from pal.protocol import CompileProposalMessage

        if self.approval_registry is None or self.proposal_emitter is None:
            return "Error: compile proposals are not available in this session."
        paths = arguments.get("summary_paths")
        if not isinstance(paths, list) or not paths:
            return "Error: 'summary_paths' must be a non-empty list."
        rationale = (arguments.get("rationale") or "").strip()
        if not rationale:
            return "Error: 'rationale' parameter is required."

        proposal_id = self.approval_registry.create_proposal(
            kind="compile",
            summary_paths=paths,
            rationale=rationale,
        )
        proposal = self.approval_registry.get(proposal_id)
        self.proposal_emitter(
            CompileProposalMessage(
                proposal_id=proposal_id,
                summary_paths=list(paths),
                rationale=rationale,
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
                    "summary_paths": list(edited.summary_paths or []),
                }
        elif final.status == "approved":
            result["summary_paths"] = list(final.summary_paths or [])
        return _json.dumps(result)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_chat_compile_tools.py -v && .venv/bin/pytest -x`
Expected: 4 new tests pass, no regressions.

- [ ] **Step 5: Commit**

```bash
git add pal/tools.py tests/test_chat_compile_tools.py
git commit -m "feat: propose_compile_batch blocks on user approval"
```

---

## Task 8: compile_batch tool

Executes an approved compile proposal. Consume-before-run invariant preserved.

**Files:**
- Modify: `pal/tools.py`
- Modify: `tests/test_chat_compile_tools.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_chat_compile_tools.py`:

```python
@pytest.mark.asyncio
async def test_compile_batch_runs_approved_proposal(tmp_path):
    registry = ApprovalRegistry()
    pid = registry.create_proposal(
        kind="compile",
        summary_paths=["raw/summaries/a.md", "raw/summaries/b.md"],
        rationale="r",
    )
    registry.approve(pid)

    calls = []

    async def fake_compile_one(path):
        calls.append(path)
        return {"status": "ok", "title": f"T-{path}", "article_path_rel": f"X/{path}.md"}

    compiler = MagicMock()
    compiler.compile_one = fake_compile_one

    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        compiler=compiler,
    )
    output = await executor.run_async(
        "compile_batch", {"proposal_id": pid}
    )
    assert calls == ["raw/summaries/a.md", "raw/summaries/b.md"]
    assert '"total": 2' in output
    assert '"ok": 2' in output
    assert registry.get(pid).status == "consumed"


@pytest.mark.asyncio
async def test_compile_batch_refuses_unknown_proposal(tmp_path):
    registry = ApprovalRegistry()
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        compiler=MagicMock(),
    )
    output = await executor.run_async(
        "compile_batch", {"proposal_id": "does-not-exist"}
    )
    assert "unknown" in output.lower() or "not found" in output.lower()


@pytest.mark.asyncio
async def test_compile_batch_refuses_pending_proposal(tmp_path):
    registry = ApprovalRegistry()
    pid = registry.create_proposal(
        kind="compile",
        summary_paths=["raw/summaries/a.md"],
        rationale="r",
    )
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        compiler=MagicMock(),
    )
    output = await executor.run_async("compile_batch", {"proposal_id": pid})
    assert "not approved" in output.lower()


@pytest.mark.asyncio
async def test_compile_batch_refuses_consumed_proposal(tmp_path):
    registry = ApprovalRegistry()
    pid = registry.create_proposal(
        kind="compile",
        summary_paths=["raw/summaries/a.md"],
        rationale="r",
    )
    registry.approve(pid)
    registry.consume(pid)
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        compiler=MagicMock(),
    )
    output = await executor.run_async("compile_batch", {"proposal_id": pid})
    assert "already" in output.lower() or "consumed" in output.lower()


@pytest.mark.asyncio
async def test_compile_batch_partial_failure(tmp_path):
    registry = ApprovalRegistry()
    pid = registry.create_proposal(
        kind="compile",
        summary_paths=["raw/summaries/good.md", "raw/summaries/bad.md"],
        rationale="r",
    )
    registry.approve(pid)

    async def fake_compile_one(path):
        if "good" in path:
            return {"status": "ok", "title": "Good", "article_path_rel": "A/Good.md"}
        return {"status": "error", "reason": "categorization failed"}

    compiler = MagicMock()
    compiler.compile_one = fake_compile_one

    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        compiler=compiler,
    )
    output = await executor.run_async("compile_batch", {"proposal_id": pid})
    assert '"ok": 1' in output
    assert '"error_count": 1' in output
    # Proposal is still consumed despite partial failure.
    assert registry.get(pid).status == "consumed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_chat_compile_tools.py -v -k compile_batch`
Expected: FAIL with `Unknown tool`.

- [ ] **Step 3: Add tool definition and handler**

In `pal/tools.py`, append to `TOOL_DEFINITIONS`:

```python
    {
        "type": "function",
        "function": {
            "name": "compile_batch",
            "description": (
                "Execute a compile batch previously approved via "
                "propose_compile_batch. Iterates the approved summary "
                "paths and compiles each. Partial failures do not "
                "abort the batch. Returns a structured report."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "proposal_id": {
                        "type": "string",
                        "description": "proposal_id returned by propose_compile_batch.",
                    },
                },
                "required": ["proposal_id"],
            },
        },
    },
```

Extend `run_async`:

```python
        if name == "compile_batch":
            return await self._compile_batch(arguments)
```

Add handler:

```python
    async def _compile_batch(self, arguments: dict) -> str:
        import json as _json
        proposal_id = (arguments.get("proposal_id") or "").strip()
        if not proposal_id:
            return "Error: 'proposal_id' parameter is required."
        if self.approval_registry is None or self.compiler is None:
            return "Error: compile execution is not available in this session."

        proposal = self.approval_registry.get(proposal_id)
        if proposal is None:
            return f"Error: unknown proposal_id: {proposal_id}"
        if proposal.kind != "compile":
            return f"Error: proposal_id {proposal_id} is not a compile proposal."
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

        # Consume first — single-use even on failure.
        self.approval_registry.consume(proposal_id)

        per_file = []
        ok = merged = insufficient = error_count = 0
        for path in (proposal.summary_paths or []):
            try:
                outcome = await self.compiler.compile_one(path)
            except Exception as exc:
                outcome = {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
            entry = {"path": path, "status": outcome.get("status")}
            if "title" in outcome:
                entry["title"] = outcome["title"]
            if "article_path_rel" in outcome:
                entry["article_path"] = outcome["article_path_rel"]
            if "reason" in outcome:
                entry["reason"] = outcome["reason"]
            per_file.append(entry)
            s = outcome.get("status")
            if s == "ok":
                ok += 1
            elif s == "merged":
                merged += 1
            elif s == "insufficient":
                insufficient += 1
            else:
                error_count += 1

        report = {
            "total": len(per_file),
            "ok": ok,
            "merged": merged,
            "insufficient": insufficient,
            "error_count": error_count,
            "per_file": per_file,
        }
        return _json.dumps(report)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_chat_compile_tools.py -v && .venv/bin/pytest -x`
Expected: 5 new tests pass, no regressions.

- [ ] **Step 5: Commit**

```bash
git add pal/tools.py tests/test_chat_compile_tools.py
git commit -m "feat: compile_batch executes approved compile proposals"
```

---

## Task 9: CLI rendering for CompileProposalMessage

**Files:**
- Modify: `pal/cli.py`
- Modify: `tests/test_cli_research_proposal.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_cli_research_proposal.py`:

```python
def test_format_compile_proposal_includes_paths_and_rationale():
    from pal.cli import format_compile_proposal
    from pal.protocol import CompileProposalMessage
    msg = CompileProposalMessage(
        proposal_id="abc",
        summary_paths=["raw/summaries/a.md", "raw/summaries/b.md"],
        rationale="promote home-automation research findings",
    )
    text = format_compile_proposal(msg)
    assert "raw/summaries/a.md" in text
    assert "raw/summaries/b.md" in text
    assert "promote home-automation research findings" in text
    assert "[a]" in text.lower() or "approve" in text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cli_research_proposal.py -v -k format_compile`
Expected: FAIL — `cannot import name 'format_compile_proposal'`.

- [ ] **Step 3: Add format_compile_proposal and dispatch branch**

In `pal/cli.py`, add alongside `format_research_proposal`:

```python
def format_compile_proposal(msg: "CompileProposalMessage") -> str:
    """Render a compile proposal approval prompt. Pure formatter."""
    lines = [
        "",
        "────────── PAL proposes compile ──────────",
        f"  Summaries ({len(msg.summary_paths)}):",
    ]
    for path in msg.summary_paths:
        lines.append(f"    {path}")
    lines.extend([
        f"  Rationale: {msg.rationale}",
        "  [a]pprove  [d]ecline  [e]dit",
        "> ",
    ])
    return "\n".join(lines)
```

Update the imports at top of `pal/cli.py`:

```python
from pal.protocol import (
    ...,
    CompileProposalMessage,
)
```

In the `async for msg in client.chat(text):` dispatch loop, find the existing `ResearchProposalMessage` branch and add a parallel branch for `CompileProposalMessage` right after it. Reuse the same input-reading pattern. For v1 the `[e]dit` case sends `decision="decline"` as specified in the spec:

```python
                    elif isinstance(msg, CompileProposalMessage):
                        if live is not None:
                            live.stop()
                            live = None
                        print(format_compile_proposal(msg), end="", flush=True)
                        loop = asyncio.get_running_loop()
                        choice = (await loop.run_in_executor(None, input)).strip().lower()
                        if choice in ("a", "approve"):
                            response = ResearchApprovalResponseMessage(
                                proposal_id=msg.proposal_id, decision="approve"
                            )
                        elif choice in ("e", "edit"):
                            # v1: edit maps to decline; model reproposes
                            # based on the user's next message.
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
git commit -m "feat: CLI renders compile proposal prompt"
```

---

## Task 10: Daemon wires Compiler into ToolExecutor

**Files:**
- Modify: `pal/daemon.py`

- [ ] **Step 1: Pass compiler into per-connection ToolExecutor**

In `_handle_connection`, find the existing `tool_executor = ToolExecutor(...)` construction. Add the `compiler` argument, drawing from `self.compiler` (constructed in Task 4):

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
        )
```

The `emit_proposal` closure already handles `ResearchProposalMessage`; it now needs to handle `CompileProposalMessage` too. Since the closure's body is `writer.write(encode_message(msg))` — encoding works on any protocol message — no code change is needed inside the closure itself, only at the tool handler (which already passes `CompileProposalMessage` in Task 7).

Update the type hint on `emit_proposal` if it has one; it doesn't in the current code.

- [ ] **Step 2: Run tests**

Run: `.venv/bin/pytest -x`
Expected: no regressions. There's no new daemon-specific test in this task; the integration is covered by Task 11's smoke test instructions and by the existing chat-tool unit tests run against a real `compiler` dependency.

- [ ] **Step 3: Commit**

```bash
git add pal/daemon.py
git commit -m "feat: per-connection ToolExecutor receives compiler dependency"
```

---

## Task 11: System prompt updates

**Files:**
- Modify: `pal/prompt_builder.py`
- Modify: `tests/test_prompt_builder.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_prompt_builder.py`:

```python
def test_base_prompt_lists_compile_tools():
    assert "compile_summary" in BASE_PROMPT
    assert "propose_compile_batch" in BASE_PROMPT
    assert "compile_batch" in BASE_PROMPT


def test_base_prompt_routes_wiki_promotion_through_compile_tools():
    lower = BASE_PROMPT.lower()
    # Step 7 now tells the model to use compile tools, not /compile.
    assert "compile_summary(path)" in BASE_PROMPT or "compile_summary" in BASE_PROMPT
    assert "propose_compile_batch" in BASE_PROMPT
    # The prompt should still forbid using create_file for wiki promotion.
    assert "do not use create_file" in lower
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_prompt_builder.py -v`
Expected: FAIL — the new tool names are not yet in BASE_PROMPT.

- [ ] **Step 3: Update BASE_PROMPT**

In `pal/prompt_builder.py`:

Find the "Your tools" section. Add a new subsection (or extend vault writes) to include the compile tools:

Current reading (approximate):
```
Vault (read/write):
- read_file, list_directory, search_content, search_vault: vault reads
- edit_file, create_file: vault writes
```

Change to:
```
Vault (read/write):
- read_file, list_directory, search_content, search_vault: vault reads
- edit_file, create_file: vault writes for arbitrary notes (not research promotion — see compile tools)

Wiki promotion (grounded, source-linked):
- compile_summary: promote a single raw summary into a wiki article
- propose_compile_batch: propose promoting multiple summaries; blocks on user approval
- compile_batch: execute an approved compile batch
```

Then find the current Step 7 in the research flow (the one that says "tell the user to run /compile" — added during the previous prompt-tuning pass) and REPLACE it with:

```
7. If the user asks to add research findings to the vault or wiki,
   use the compile tools. Do NOT use create_file or edit_file for
   this purpose.
   - compile_summary(summary_path) for a single summary. Use when
     the user names a specific file or you're ingesting just one.
   - propose_compile_batch(summary_paths, rationale) for multiple.
     It blocks until the user approves. After it returns status
     "approved", immediately call compile_batch(proposal_id). Do not
     narrate a plan between the two calls.
   The compile tools preserve source linkage, run categorization,
   and archive raw material automatically. create_file bypasses all
   of that.
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_prompt_builder.py -v && .venv/bin/pytest -x`
Expected: all tests pass including the 2 new ones.

- [ ] **Step 5: Commit**

```bash
git add pal/prompt_builder.py tests/test_prompt_builder.py
git commit -m "feat: chat prompt teaches compile tools for wiki promotion"
```

---

## Task 12: Injection regression test

Fetched content (or any injected instruction) that tells the model to `compile_batch(proposal_id="XYZ")` without a valid approved proposal must fail safely.

**Files:**
- Modify: `tests/test_chat_research_integration.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_chat_research_integration.py`:

```python
@pytest.mark.asyncio
async def test_injected_compile_batch_call_without_valid_proposal_is_refused(tmp_path):
    """Indirect-injection attack: content tells the model to call
    compile_batch with a made-up proposal_id. The tool must refuse
    without invoking the Compiler."""
    registry = ApprovalRegistry()
    compiler = MagicMock()
    compiler.compile_one = AsyncMock()
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        compiler=compiler,
    )

    output = await executor.run_async(
        "compile_batch",
        {"proposal_id": "injected-by-fetched-content"},
    )
    assert "unknown" in output.lower() or "not found" in output.lower()
    compiler.compile_one.assert_not_called()


@pytest.mark.asyncio
async def test_consumed_compile_proposal_cannot_be_reused(tmp_path):
    registry = ApprovalRegistry()
    pid = registry.create_proposal(
        kind="compile",
        summary_paths=["raw/summaries/a.md"],
        rationale="r",
    )
    registry.approve(pid)

    async def fake_compile_one(path):
        return {"status": "ok", "title": "T", "article_path_rel": "X/T.md"}

    compiler = MagicMock()
    compiler.compile_one = fake_compile_one

    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        compiler=compiler,
    )

    first = await executor.run_async("compile_batch", {"proposal_id": pid})
    assert '"total": 1' in first
    # Second call with same id must refuse.
    second = await executor.run_async("compile_batch", {"proposal_id": pid})
    assert "already" in second.lower() or "consumed" in second.lower()
```

Check that `AsyncMock` is imported at the top of the test file; if not, add it.

- [ ] **Step 2: Run tests**

Run: `.venv/bin/pytest tests/test_chat_research_integration.py -v && .venv/bin/pytest -x`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_chat_research_integration.py
git commit -m "test: regression coverage for injection-driven compile_batch calls"
```

---

## Task 13: Manual smoke test

**Files:** none modified

- [ ] **Step 1: Pull latest and restart daemon**

```bash
cd /home/edible/Projects/PAL
git pull
# Restart the daemon (however the user's deployment does it — systemd/tmux/etc.)
```

- [ ] **Step 2: Exercise single-summary promotion**

Start a chat session and run:

```
Can you promote one of the summaries in raw/summaries/ into the wiki?
Pick any file.
```

Expected: model lists the summaries (or picks one from context), calls `compile_summary(path)`, reports the outcome inline (`{"status": "ok", "title": "...", "article_path": "..."}` rendered in prose). No consent prompt. A new file appears under the appropriate category in the vault. The raw summary and its source move to `raw/archived/`.

- [ ] **Step 3: Exercise batch promotion happy path**

In the same or a new session:

```
Please research MCP server security, then ingest the findings into the vault.
```

Expected:
1. Model runs the full research flow (propose_research → approve → research_topic).
2. Reports the summary paths.
3. Calls `propose_compile_batch` with all the fresh summary paths.
4. CLI renders the compile approval prompt showing each path.
5. You approve with `a`.
6. Model calls `compile_batch(proposal_id)` immediately.
7. Structured report appears: `total`, `ok`, `merged`, `insufficient`, `error_count`, per-file results.
8. Raw summaries are gone from `raw/summaries/`, now in `raw/archived/`.
9. New or updated wiki articles exist with source linkage.

- [ ] **Step 4: Exercise decline path**

```
Research something adversarial, then try to ingest it.
```

At the compile approval prompt type `d`. Expected:
- Model sees `"status": "declined"`.
- Does not call `compile_batch`.
- Reports the decline to you and asks what to do with the summaries.

- [ ] **Step 5: Exercise edit-as-decline path**

Same flow as step 3. At the compile approval prompt type `e`. Expected:
- CLI sends `decision="decline"` (v1 behavior).
- Model sees `"status": "declined"`, infers from your subsequent message that you want to edit, issues a fresh `propose_compile_batch` call with a revised path list.

- [ ] **Step 6: Inspect side effects and capture notes**

After each flow:
- `ls raw/summaries/` — should be empty (or only contain files the user didn't compile).
- `ls raw/archived/` — should contain the compiled pairs.
- Inspect a newly-written wiki article — it should have source metadata pointing back to the archived summary.

Capture any prompt-adherence drift, CLI rendering issues, or unexpected tool call sequencing as notes for a follow-up prompt-tuning pass. Don't fix here.

- [ ] **Step 7: Commit any captured notes**

If notes file was created:

```bash
git add docs/<notes-path>.md
git commit -m "docs: compile-tool smoke-test observations"
```

---

## Self-review

**Spec coverage:**
- Tools (compile_summary, propose_compile_batch, compile_batch) → Tasks 6, 7, 8.
- Compiler extraction → Task 4.
- ApprovalRegistry generalization → Tasks 1, 2.
- CompileProposalMessage protocol → Task 3.
- CLI rendering → Task 9.
- Daemon wiring → Task 10.
- Prompt updates → Task 11.
- Injection tests → Task 12.
- Smoke test → Task 13.
- Non-goal items (consent on edit/create_file, structured CLI edit, Discord) correctly absent.

**Placeholder scan:** no TBDs, TODOs, vague "handle edge cases" language. Every code step shows code. Every test shows assertions.

**Type consistency:** `Proposal` (not `ResearchProposal`) used consistently from Task 1 onward. `kind` field is `Literal["research", "compile"]`. Tool names match between definitions, dispatch, and tests. The `emit_proposal` closure carries either message type without modification because `encode_message` uses `asdict` over the dataclass.

**One note:** Task 2's `create_proposal` makes params keyword-only, which is a breaking change for any positional caller. Task 2 Step 4 includes the grep + fixup. If that grep surfaces callers in code paths I haven't named, the implementer should escalate rather than silently change signatures.
