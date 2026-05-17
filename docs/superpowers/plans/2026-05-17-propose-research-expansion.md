# propose_research expansion -- Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the chat-tool research path (`propose_research` + `research_topic`) to feature parity with the deleted `/research` slash command: inline multi-topic batch with cross-topic URL dedup, and per-URL fetch/summarize progress emission.

**Architecture:** Cross-repo change. agent_core gains a `topics` field on `Proposal` + `create_proposal` + `edit`. PAL bumps the pin, adds a `topics` field to `ResearchProposalMessage`, expands `propose_research` validation, simplifies `research_topic` dispatch to always call the batch path, adds per-URL progress emission inside `Researcher`, and adds multi-topic rendering (Discord embed + CLI prompt) with embed-length truncation.

**Tech Stack:** Python 3.12, pytest, agent_core, PAL (daemon + Discord adapter + CLI), discord.py.

**Spec:** `docs/superpowers/specs/2026-05-17-propose-research-expansion-design.md`

**Repos touched:** `agent_core` (Task 1) and `PAL` (Tasks 2-10).

---

## File Structure

**agent_core repo (`/home/edible/Projects/agent_core/`):**
- Modify: `agent_core/approval_registry.py` -- add `topics` field to `Proposal` (line 25-49), add `topics` kwarg to `create_proposal` (line 60-135), update `edit` to copy `topics` (line 179-226).
- Modify: `agent_core/tests/test_approval_registry.py` -- add 2 tests (edit preserves topics; edit topics=None unchanged).
- Modify: `pyproject.toml` -- version bump (1.3.0 -> 1.3.1; v1.3.1 was already cut by the parallel Phase 2 MCP workstream, so this is a patch on top).

**PAL repo (`/home/edible/Projects/PAL/`):**
- Modify: `pyproject.toml` -- agent_core pin bump to v1.3.1.
- Modify: `pal/protocol.py` -- add `topics` field to `ResearchProposalMessage` (line 31-36).
- Modify: `pal/tools/research.py` -- validation expansion, multi-topic creation, dispatch simplification, return shape (lines 43-177).
- Modify: `pal/researcher.py` -- add `_short_url` helper and per-URL progress emission inside `_fetch_and_save` and `_summarize` (around lines 131-200).
- Modify: `pal/discord_interactions.py` -- multi-topic embed rendering + truncation (lines 60-91).
- Modify: `pal/cli.py` -- multi-topic CLI prompt rendering (lines 68-78).
- Test: `tests/test_tools_research.py` -- 8+ new tests for validation, dispatch, return shape.
- Test: `tests/test_researcher.py` -- 3 new tests for per-URL emission.
- Test: `tests/test_discord_interactions.py` -- 2 new tests for multi-topic + truncation embed.
- Test: `tests/test_cli_research_proposal.py` -- 1 new test for multi-topic CLI render.

---

## Task 1: agent_core -- add `topics` to Proposal + create_proposal + edit + tests + bump

**Files (all in `/home/edible/Projects/agent_core/`):**
- Modify: `agent_core/approval_registry.py`
- Modify: `agent_core/tests/test_approval_registry.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write the failing tests**

Append to `/home/edible/Projects/agent_core/tests/test_approval_registry.py`:

```python
def test_edit_preserves_topics_when_set():
    """edit() must copy `topics` to the successor proposal so multi-topic
    edits don't silently drop the topic list."""
    reg = ApprovalRegistry(expiry_minutes=15)
    pid = reg.create_proposal(
        topic="3 topics: a, b, c",
        topics=["a", "b", "c"],
        depth=3,
        rationale="batch research",
    )
    new_id = reg.edit(pid, new_depth=5)
    assert new_id is not None
    successor = reg.get(new_id)
    assert successor is not None
    assert successor.topics == ["a", "b", "c"]
    assert successor.depth == 5
    assert successor.topic == "3 topics: a, b, c"


def test_edit_topics_none_unchanged():
    """edit() on a single-topic proposal (topics=None) keeps topics=None
    on the successor; regression pin."""
    reg = ApprovalRegistry(expiry_minutes=15)
    pid = reg.create_proposal(
        topic="docker networking",
        depth=3,
        rationale="single-topic research",
    )
    new_id = reg.edit(pid, new_depth=5)
    assert new_id is not None
    successor = reg.get(new_id)
    assert successor is not None
    assert successor.topics is None
    assert successor.topic == "docker networking"
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/agent_core && .venv/bin/pytest tests/test_approval_registry.py -k "topics" -v
```

Expected: BOTH fail. `test_edit_preserves_topics_when_set` fails on `create_proposal(..., topics=["a","b","c"], ...)` with `TypeError: unexpected keyword argument 'topics'`. `test_edit_topics_none_unchanged` fails on `successor.topics` with `AttributeError: 'Proposal' object has no attribute 'topics'`.

- [ ] **Step 3: Add `topics` field to `Proposal` dataclass**

In `/home/edible/Projects/agent_core/agent_core/approval_registry.py`, locate the `Proposal` dataclass (lines 25-49). Add `topics` as the last field BEFORE the `event` field (so it stays positional-safe; existing positional construction in tests doesn't break):

Find this block at lines 25-49:

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
    slug: Optional[str] = None
    body: Optional[str] = None
    caller: Optional[str] = None
    context: Optional[str] = None
    note_path: Optional[str] = None
    approval_choice: Optional[str] = None
    # asyncio.Event is set when the proposal reaches a terminal state
    # (approved, declined, or expired). Not part of the public dataclass
    # fields -- carried separately for awaiting.
    event: asyncio.Event = field(default_factory=asyncio.Event, repr=False, compare=False)
```

Add `topics: Optional[list[str]] = None` after `approval_choice` and before `event`:

```python
    approval_choice: Optional[str] = None
    topics: Optional[list[str]] = None
    event: asyncio.Event = field(default_factory=asyncio.Event, repr=False, compare=False)
```

- [ ] **Step 4: Add `topics` kwarg to `create_proposal`**

In the same file, locate `create_proposal` (lines 60-135). Add `topics: Optional[list[str]] = None` as a new kwarg (just before `body` for organization). Then in the `Proposal(...)` construction at line 116-134, pass `topics=list(topics) if topics else None`.

Updated signature (full block):

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
        slug: Optional[str] = None,
        body: Optional[str] = None,
        caller: Optional[str] = None,
        context: Optional[str] = None,
        note_path: Optional[str] = None,
        topics: Optional[list[str]] = None,
    ) -> str:
```

Updated construction (full block, lines 114-134 area):

```python
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
            slug=slug,
            body=body,
            caller=caller,
            context=context,
            note_path=note_path,
            topics=list(topics) if topics else None,
        )
        return proposal_id
```

- [ ] **Step 5: Update `edit` to copy `topics`**

In the same file, locate `edit` (lines 179-226). Find the `Proposal(...)` construction inside (around line 203-222) and add `topics` copying:

```python
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
            target_path=old.target_path,
            target_title=old.target_title,
            topics=list(old.topics) if old.topics else None,
        )
```

- [ ] **Step 6: Run new tests, verify pass**

```bash
cd /home/edible/Projects/agent_core && .venv/bin/pytest tests/test_approval_registry.py -k "topics" -v
```

Expected: both pass.

- [ ] **Step 7: Full agent_core suite regression sweep**

```bash
cd /home/edible/Projects/agent_core && .venv/bin/pytest tests/ -q 2>&1 | tail -5
```

Expected: all pass (currently 615 + 2 new = 617). If any test fails because it constructs `Proposal(...)` positionally and our new field shifts a position, the field placement (last field before `event`) should have prevented it. If a test does fail this way, fix it -- but check the failure carefully first.

- [ ] **Step 8: Bump agent_core version**

In `/home/edible/Projects/agent_core/pyproject.toml`, change:

```
version = "1.3.0"
```

to:

```
version = "1.3.1"
```

(Patch bump. v1.3.1 was cut by the parallel Phase 2 MCP workstream before this change reached agent_core. Adding `topics` to Proposal is additive and non-breaking, so a patch is appropriate.)

- [ ] **Step 9: Em-dash sweep on the diff**

```bash
cd /home/edible/Projects/agent_core && git diff | grep -cP '[\x{2014}\x{2013}]'
```

Expected: `0`.

- [ ] **Step 10: Commit + tag**

```bash
cd /home/edible/Projects/agent_core && git add agent_core/approval_registry.py tests/test_approval_registry.py pyproject.toml && git commit -m "$(cat <<'EOF'
feat(approval_registry): add topics field for multi-topic research proposals

PAL's propose_research is gaining inline-list mode (research multiple
topics in one approval). The Proposal dataclass and create_proposal()
need a topics field to carry the list; edit() needs to copy it so
multi-topic edits don't silently drop the list.

Additive change: existing single-topic flows pass topics=None and
behave identically. Tests cover both the edit-preserves and the
edit-topics-none-unchanged cases.

Bumps to 1.3.0 (minor: additive public dataclass field).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)" && git tag -a v1.3.1 -m "agent_core v1.3.1 -- Proposal.topics field for multi-topic research" && git push origin main && git push origin v1.3.1 2>&1 | tail -5
```

Expected: commit + tag pushed to origin.

**CRITICAL: Verify branch before commit.** Run `git branch --show-current` first. If on a feature branch, switch to `main` (or coordinate with user). Memory: `feedback_check_branch_before_commit`.

---

## Task 2: PAL -- bump agent_core pin to v1.3.1

**Files (all in `/home/edible/Projects/PAL/`):**
- Modify: `pyproject.toml`

- [ ] **Step 1: Bump the pin**

Read `/home/edible/Projects/PAL/pyproject.toml` and find the agent_core dependency line. It currently pins `@v1.2.1`. Change to `@v1.3.1`.

- [ ] **Step 2: Reinstall agent_core in PAL's venv**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pip install -e /home/edible/Projects/agent_core --force-reinstall --no-deps 2>&1 | tail -3
```

Expected: `Successfully installed agent_core-1.3.0` (or similar).

Verify the new field is available:

```bash
cd /home/edible/Projects/PAL && .venv/bin/python -c "from agent_core.approval_registry import Proposal; import inspect; assert 'topics' in {f.name for f in __import__('dataclasses').fields(Proposal)}; print('ok')"
```

Expected: `ok`.

- [ ] **Step 3: Full PAL suite regression sweep**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/ \
    --ignore=tests/test_chat_research_integration.py \
    --ignore=tests/test_client.py \
    --ignore=tests/test_daemon.py \
    --ignore=tests/test_integration.py \
    --ignore=tests/test_prompt_injection.py \
    -q 2>&1 | tail -5
```

Expected: all pass (no behavioral change yet; pin bump is structural only).

- [ ] **Step 4: Commit**

```bash
cd /home/edible/Projects/PAL && git branch --show-current
```

If not on `main`, switch first. Then:

```bash
cd /home/edible/Projects/PAL && git add pyproject.toml && git commit -m "$(cat <<'EOF'
chore: bump agent_core pin to v1.3.1

Picks up the Proposal.topics field needed for the propose_research
expansion (inline-list multi-topic research with cross-topic URL
dedup).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: PAL -- add `topics` field to ResearchProposalMessage

**Files:**
- Modify: `/home/edible/Projects/PAL/pal/protocol.py`

- [ ] **Step 1: Write the failing test**

Append to `/home/edible/Projects/PAL/tests/test_protocol.py`:

```python
def test_research_proposal_message_topics_field_default_none():
    """ResearchProposalMessage.topics defaults to None for single-topic mode."""
    from pal.protocol import ResearchProposalMessage
    msg = ResearchProposalMessage(
        proposal_id="abc",
        topic="docker networking",
        depth=3,
        rationale="test",
    )
    assert msg.topics is None


def test_research_proposal_message_topics_field_accepts_list():
    """ResearchProposalMessage.topics carries the list in multi-topic mode."""
    from pal.protocol import ResearchProposalMessage
    msg = ResearchProposalMessage(
        proposal_id="abc",
        topic="3 topics: a, b, c",
        depth=3,
        rationale="test",
        topics=["a", "b", "c"],
    )
    assert msg.topics == ["a", "b", "c"]
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_protocol.py -k "topics" -v
```

Expected: both fail (AttributeError or TypeError on the `topics` field).

- [ ] **Step 3: Add the field**

In `/home/edible/Projects/PAL/pal/protocol.py`, locate `ResearchProposalMessage` (lines 29-36). Add `topics` field as the LAST positional field before `type`:

```python
@register_message
@dataclass
class ResearchProposalMessage:
    proposal_id: str
    topic: str
    depth: int
    rationale: str
    topics: list[str] | None = None
    type: str = "research_proposal"
```

- [ ] **Step 4: Run new tests, verify they pass**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_protocol.py -k "topics" -v
```

Expected: both pass.

- [ ] **Step 5: Full protocol test regression sweep**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_protocol.py -v 2>&1 | tail -10
```

Expected: all pass. If anything fails because a test constructs `ResearchProposalMessage` positionally and the new field shifts positions, the placement (last before `type`) should have prevented it. Investigate any failure.

- [ ] **Step 6: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/protocol.py tests/test_protocol.py && git commit -m "$(cat <<'EOF'
feat(protocol): add topics field to ResearchProposalMessage

Additive optional field carrying the parsed topic list in multi-topic
research mode. Single-topic flows leave it None.

Field positioned last before `type` to preserve existing positional
construction in tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: PAL -- propose_research validation (rejects neither/both/empty/whitespace topics)

**Files:**
- Modify: `/home/edible/Projects/PAL/pal/tools/research.py`
- Test: `/home/edible/Projects/PAL/tests/test_tools_research.py`

- [ ] **Step 1: Write the failing validation tests**

Append to `/home/edible/Projects/PAL/tests/test_tools_research.py`:

```python
import pytest
from pal.tools.research import ProposeResearch


@pytest.mark.asyncio
async def test_propose_research_rejects_neither_topic_nor_topics():
    """Validation: at least one of topic/topics required."""
    tool = ProposeResearch()
    ctx = _build_ctx_with_registry()  # helper that wires approval_registry
    result = await tool.run({"rationale": "test"}, ctx)
    assert "exactly one of 'topic' or 'topics'" in result.lower() or "Error" in result


@pytest.mark.asyncio
async def test_propose_research_rejects_both_topic_and_topics():
    """Validation: mutually exclusive."""
    tool = ProposeResearch()
    ctx = _build_ctx_with_registry()
    result = await tool.run(
        {"topic": "docker", "topics": ["a", "b"], "rationale": "test"},
        ctx,
    )
    assert "exactly one of" in result.lower() or "Error" in result


@pytest.mark.asyncio
async def test_propose_research_rejects_empty_topics_list():
    tool = ProposeResearch()
    ctx = _build_ctx_with_registry()
    result = await tool.run({"topics": [], "rationale": "test"}, ctx)
    assert "Error" in result


@pytest.mark.asyncio
async def test_propose_research_rejects_topics_all_whitespace():
    tool = ProposeResearch()
    ctx = _build_ctx_with_registry()
    result = await tool.run(
        {"topics": ["", "  ", "\n"], "rationale": "test"},
        ctx,
    )
    assert "Error" in result
```

If `_build_ctx_with_registry` doesn't exist as a helper, write it inline above the tests:

```python
from unittest.mock import MagicMock
from agent_core.approval_registry import ApprovalRegistry


def _build_ctx_with_registry():
    """Build a minimal HandlerContext with an ApprovalRegistry on the agent."""
    ctx = MagicMock()
    ctx.agent.approval_registry = ApprovalRegistry(expiry_minutes=15)
    return ctx
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_research.py -k "rejects" -v
```

Expected: all 4 fail or behave unexpectedly (current `propose_research` would treat empty/missing topics differently).

- [ ] **Step 3: Update propose_research validation**

In `/home/edible/Projects/PAL/pal/tools/research.py`, locate `ProposeResearch.run()` (lines 72-120). Replace the validation block (lines 78-86) with:

```python
        rationale = args.get("rationale", "").strip()
        if not rationale:
            return "Error: 'rationale' parameter is required."

        raw_topic = args.get("topic", "")
        topic = raw_topic.strip() if isinstance(raw_topic, str) else ""
        raw_topics = args.get("topics")
        topics: list[str] = []
        if isinstance(raw_topics, list):
            topics = [t.strip() for t in raw_topics if isinstance(t, str) and t.strip()]

        if not topic and not topics:
            return "Error: provide exactly one of 'topic' or 'topics'."
        if topic and topics:
            return "Error: provide exactly one of 'topic' or 'topics'."
        if raw_topics is not None and isinstance(raw_topics, list) and not topics:
            return "Error: 'topics' must be a non-empty list of non-empty strings."

        depth = int(args.get("depth", 3))
        depth = max(1, min(depth, 10))
```

Also update the `parameters` schema (lines 52-69) to add `topics`:

```python
    parameters = {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "Single topic string. Use this OR `topics`, not both.",
            },
            "topics": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of topics for batch research with cross-topic URL dedup. Use this OR `topic`, not both.",
            },
            "depth": {
                "type": "integer",
                "description": "Number of sources to fetch per topic (1-10, default 3).",
            },
            "rationale": {
                "type": "string",
                "description": "One-line reason shown to the user.",
            },
        },
        "required": ["rationale"],
    }
```

Update the description (lines 45-51) to spell out the constraint:

```python
    description = (
        "Propose a web research run. Provide either `topic` (single string) "
        "for one topic, or `topics` (array of strings) for a batch with "
        "cross-topic URL deduplication. Exactly one is required. Emits a "
        "proposal to the user and blocks until they approve, decline, or "
        "edit it. Returns a JSON object with the final status and proposal_id. "
        "Use research_topic to execute an approved proposal."
    )
```

- [ ] **Step 4: Run validation tests, verify they pass**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_research.py -k "rejects" -v
```

Expected: all 4 pass.

- [ ] **Step 5: Existing-test regression check**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_research.py -v 2>&1 | tail -10
```

Expected: existing tests still pass. If any test fails because it sent `topic=""` with no topics, it was relying on the "empty topic is required-but-missing" error -- update the test to check the new error wording or pass a non-empty topic.

- [ ] **Step 6: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/tools/research.py tests/test_tools_research.py && git commit -m "$(cat <<'EOF'
feat(propose_research): accept `topics` list parameter + tighten validation

Adds the `topics: list[str]` schema field and runtime exactly-one-of
constraint (topic xor topics). Includes filter for whitespace-only
list entries.

Tool description spells out the constraint so the model knows not to
pass both.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: PAL -- propose_research multi-topic creation + return shape

**Files:**
- Modify: `/home/edible/Projects/PAL/pal/tools/research.py`
- Test: `/home/edible/Projects/PAL/tests/test_tools_research.py`

- [ ] **Step 1: Write the failing tests**

Append to `/home/edible/Projects/PAL/tests/test_tools_research.py`:

```python
@pytest.mark.asyncio
async def test_propose_research_topics_list_populates_proposal_topics():
    """Multi-topic mode stores the list on the proposal record."""
    tool = ProposeResearch()
    ctx = _build_ctx_with_registry()
    # Pre-approve any created proposal so run() returns instead of blocking
    ctx.emit = AsyncMock()
    async def auto_approve_after_emit(msg):
        ctx.agent.approval_registry.approve(msg.proposal_id)
    ctx.emit.side_effect = auto_approve_after_emit

    import json
    result_json = await tool.run(
        {"topics": ["a", "b", "c"], "rationale": "test"},
        ctx,
    )
    result = json.loads(result_json)
    assert result["status"] == "approved"
    assert result["topics"] == ["a", "b", "c"]
    assert "topic" in result  # human-readable summary
    proposal = ctx.agent.approval_registry.get(result["proposal_id"])
    assert proposal.topics == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_propose_research_topics_summary_truncates_after_three():
    """Topic summary string shows first 3 + '...' for longer lists."""
    tool = ProposeResearch()
    ctx = _build_ctx_with_registry()
    ctx.emit = AsyncMock()
    async def auto_approve_after_emit(msg):
        ctx.agent.approval_registry.approve(msg.proposal_id)
    ctx.emit.side_effect = auto_approve_after_emit

    import json
    result_json = await tool.run(
        {"topics": ["a", "b", "c", "d", "e"], "rationale": "test"},
        ctx,
    )
    result = json.loads(result_json)
    assert "5 topics" in result["topic"]
    assert "a" in result["topic"]
    assert "b" in result["topic"]
    assert "c" in result["topic"]
    assert "..." in result["topic"]


@pytest.mark.asyncio
async def test_propose_research_return_shape_single_topic_no_topics_key():
    """Regression: single-topic return shape does NOT include `topics` key."""
    tool = ProposeResearch()
    ctx = _build_ctx_with_registry()
    ctx.emit = AsyncMock()
    async def auto_approve_after_emit(msg):
        ctx.agent.approval_registry.approve(msg.proposal_id)
    ctx.emit.side_effect = auto_approve_after_emit

    import json
    result_json = await tool.run(
        {"topic": "docker networking", "rationale": "test"},
        ctx,
    )
    result = json.loads(result_json)
    assert result["status"] == "approved"
    assert result["topic"] == "docker networking"
    assert "topics" not in result
```

Make sure `AsyncMock` is imported at the top of the test file: `from unittest.mock import MagicMock, AsyncMock`.

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_research.py -k "populates or summary or return_shape_single" -v
```

Expected: all 3 fail.

- [ ] **Step 3: Implement multi-topic creation and return shape**

In `/home/edible/Projects/PAL/pal/tools/research.py`, replace the rest of `ProposeResearch.run()` (lines 87-120) with:

```python
        ar = ctx.agent.approval_registry

        if topics:
            # Multi-topic mode: build human-readable summary and store list.
            if len(topics) <= 3:
                summary = f"{len(topics)} topics: " + ", ".join(topics)
            else:
                first_three = ", ".join(topics[:3])
                summary = f"{len(topics)} topics: {first_three}, ..."
            proposal_id = ar.create_proposal(
                topic=summary, depth=depth, rationale=rationale, topics=topics,
            )
        else:
            # Single-topic mode (unchanged).
            proposal_id = ar.create_proposal(
                topic=topic, depth=depth, rationale=rationale,
            )

        proposal = ar.get(proposal_id)
        await ctx.emit(ResearchProposalMessage(
            proposal_id=proposal_id,
            topic=proposal.topic,
            depth=depth,
            rationale=rationale,
            topics=topics if topics else None,
        ))
        # Block until the user signals a terminal status (or expiry).
        remaining = (proposal.expires_at - datetime.now(timezone.utc)).total_seconds()
        try:
            await asyncio.wait_for(proposal.event.wait(), timeout=max(0.1, remaining))
        except asyncio.TimeoutError:
            ar.expire_stale()

        final = ar.get(proposal_id)
        result = {"proposal_id": proposal_id, "status": final.status}
        if final.status == "declined":
            edited = ar.get_successor(proposal_id)
            if edited is not None:
                result = {
                    "proposal_id": edited.proposal_id,
                    "status": "approved",
                    "topic": edited.topic,
                    "depth": edited.depth,
                }
                if edited.topics:
                    result["topics"] = list(edited.topics)
        elif final.status == "approved":
            result["topic"] = final.topic
            result["depth"] = final.depth
            if final.topics:
                result["topics"] = list(final.topics)
        return json.dumps(result)
```

- [ ] **Step 4: Run new tests, verify they pass**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_research.py -k "populates or summary or return_shape_single" -v
```

Expected: all 3 pass.

- [ ] **Step 5: Full propose_research test regression sweep**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_research.py -v 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/tools/research.py tests/test_tools_research.py && git commit -m "$(cat <<'EOF'
feat(propose_research): multi-topic proposal creation + return shape

Multi-topic mode (topics=[...]) creates the proposal with both a
human-readable summary string (topic) and the full list (topics).
Approval message carries both fields.

JSON return adds `topics` key in multi-topic mode; single-topic
return shape unchanged (regression pin in tests).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: PAL -- research_topic dispatch simplification (always-call-research_topics)

**Files:**
- Modify: `/home/edible/Projects/PAL/pal/tools/research.py`
- Test: `/home/edible/Projects/PAL/tests/test_tools_research.py`

- [ ] **Step 1: Write the failing tests**

Append to `/home/edible/Projects/PAL/tests/test_tools_research.py`:

```python
@pytest.mark.asyncio
async def test_research_topic_batch_calls_research_topics_with_list():
    """research_topic on a multi-topic proposal passes the full list to Researcher."""
    from pal.tools.research import ResearchTopic
    from agent_core.approval_registry import ApprovalRegistry

    reg = ApprovalRegistry(expiry_minutes=15)
    pid = reg.create_proposal(
        topic="3 topics: a, b, c",
        depth=3,
        rationale="test",
        topics=["a", "b", "c"],
    )
    reg.approve(pid)

    ctx = MagicMock()
    ctx.agent.approval_registry = reg
    ctx.agent.researcher = MagicMock()
    ctx.agent.researcher.research_topics = AsyncMock(return_value=_empty_report())
    ctx.agent.config = MagicMock()
    ctx.agent.config.vault_path = Path("/tmp")

    tool = ResearchTopic()
    await tool.run({"proposal_id": pid}, ctx)

    ctx.agent.researcher.research_topics.assert_awaited_once()
    call_args = ctx.agent.researcher.research_topics.call_args
    assert call_args.args[0] == ["a", "b", "c"] or call_args.kwargs.get("topics") == ["a", "b", "c"] or call_args.args[0] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_research_topic_single_calls_research_topics_with_one_element():
    """research_topic on a single-topic proposal wraps the topic in a 1-element list."""
    from pal.tools.research import ResearchTopic
    from agent_core.approval_registry import ApprovalRegistry

    reg = ApprovalRegistry(expiry_minutes=15)
    pid = reg.create_proposal(topic="docker networking", depth=3, rationale="test")
    reg.approve(pid)

    ctx = MagicMock()
    ctx.agent.approval_registry = reg
    ctx.agent.researcher = MagicMock()
    ctx.agent.researcher.research_topics = AsyncMock(return_value=_empty_report())
    ctx.agent.config = MagicMock()
    ctx.agent.config.vault_path = Path("/tmp")

    tool = ResearchTopic()
    await tool.run({"proposal_id": pid}, ctx)

    ctx.agent.researcher.research_topics.assert_awaited_once()
    call_args = ctx.agent.researcher.research_topics.call_args
    assert call_args.args[0] == ["docker networking"]
```

Add a helper at the top of the test file:

```python
from pathlib import Path
from pal.researcher import ResearchReport


def _empty_report() -> ResearchReport:
    return ResearchReport()
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_research.py -k "calls_research_topics" -v
```

Expected: both fail (current `ResearchTopic.run()` calls `researcher.research_topic`, not `research_topics`).

- [ ] **Step 3: Simplify dispatch in `ResearchTopic.run()`**

In `/home/edible/Projects/PAL/pal/tools/research.py`, locate `ResearchTopic.run()` (lines 144-177). Replace the `researcher.research_topic(...)` call (lines 169-173) with:

```python
        try:
            topics = proposal.topics if proposal.topics else [proposal.topic]
            report = await ctx.agent.researcher.research_topics(
                topics, depth=proposal.depth,
            )
        except Exception as exc:
            return f"Research error: {exc}"
```

- [ ] **Step 4: Run new tests, verify they pass**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_research.py -k "calls_research_topics" -v
```

Expected: both pass.

- [ ] **Step 5: Regression sweep on `tests/test_tools_research.py`**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_research.py -v 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/tools/research.py tests/test_tools_research.py && git commit -m "$(cat <<'EOF'
refactor(research_topic): always dispatch through research_topics

research_topic is already a 1-element-list wrapper around
research_topics (researcher.py:235-237). Calling research_topics
directly with [topic] (single) or proposal.topics (multi) collapses
the dispatch branch into one path with no behavioral change.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: PAL -- Researcher per-URL progress emission

**Files:**
- Modify: `/home/edible/Projects/PAL/pal/researcher.py`
- Test: `/home/edible/Projects/PAL/tests/test_researcher.py`

- [ ] **Step 1: Write the failing tests**

Append to `/home/edible/Projects/PAL/tests/test_researcher.py`:

```python
@pytest.mark.asyncio
async def test_researcher_emits_per_url_fetch_success(tmp_path):
    """Researcher._fetch_and_save emits 'Fetched: ...' on success."""
    from unittest.mock import MagicMock, AsyncMock
    from pal.researcher import Researcher

    captured = []
    def on_progress(msg):
        captured.append(msg)

    fetcher = MagicMock()
    fetcher.fetch = AsyncMock(return_value=MagicMock(
        text="some content",
        title="A page",
        url="https://example.com/page",
        content_hash="abcd1234abcd",
    ))
    researcher = Researcher(
        websearch=MagicMock(),
        fetcher=fetcher,
        inference=MagicMock(),
        vault_path=tmp_path,
        on_progress=on_progress,
    )
    await researcher._fetch_and_save("https://example.com/page", "topic-slug")
    assert any("Fetched:" in m for m in captured), captured


@pytest.mark.asyncio
async def test_researcher_emits_per_url_fetch_failure(tmp_path):
    """Researcher._fetch_and_save emits 'Fetch failed (...)' on FetchError."""
    from unittest.mock import MagicMock, AsyncMock
    from agent_core.utils.fetcher import FetchError
    from pal.researcher import Researcher

    captured = []
    def on_progress(msg):
        captured.append(msg)

    fetcher = MagicMock()
    fetcher.fetch = AsyncMock(side_effect=FetchError("timeout"))
    researcher = Researcher(
        websearch=MagicMock(),
        fetcher=fetcher,
        inference=MagicMock(),
        vault_path=tmp_path,
        on_progress=on_progress,
    )
    await researcher._fetch_and_save("https://example.com/page", "topic-slug")
    assert any("Fetch failed" in m for m in captured), captured


@pytest.mark.asyncio
async def test_researcher_emits_per_url_summarize_success(tmp_path, monkeypatch):
    """Researcher._summarize emits 'Summarized: ...' on success."""
    from unittest.mock import MagicMock, AsyncMock
    from pathlib import Path
    from pal.researcher import Researcher, SourceResult
    from dataclasses import dataclass

    captured = []
    def on_progress(msg):
        captured.append(msg)

    # Mock summarize_raw_file at the import path researcher.py uses
    async def fake_summarize(raw_path, vault_path, inference, max_body_chars):
        return MagicMock(summary_path=tmp_path / "summary.md")

    monkeypatch.setattr("pal.researcher.summarize_raw_file", fake_summarize)

    researcher = Researcher(
        websearch=MagicMock(),
        fetcher=MagicMock(),
        inference=MagicMock(),
        vault_path=tmp_path,
        on_progress=on_progress,
    )
    source = SourceResult(
        url="https://example.com/page",
        title="page",
        raw_path=tmp_path / "raw.md",
        status="ok",
    )
    await researcher._summarize(source)
    assert any("Summarized:" in m for m in captured), captured
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_researcher.py -k "per_url" -v
```

Expected: all 3 fail (no `Fetched:` / `Fetch failed` / `Summarized:` events emitted today).

- [ ] **Step 3: Add `_short_url` helper and emission points**

In `/home/edible/Projects/PAL/pal/researcher.py`, add the `_short_url` helper function near the top of the file (after the existing `_url_slug` function around line 49):

```python
def _short_url(url: str, max_len: int = 40) -> str:
    """Compact URL for progress messages: hostname + truncated path."""
    parsed = urlparse(url)
    host = parsed.hostname or "unknown"
    path = parsed.path or ""
    combined = host + path
    if len(combined) <= max_len:
        return combined
    return combined[: max_len - 3] + "..."
```

In `_fetch_and_save` (around lines 131-200 -- read the current method to find exact insertion points), emit on the success and error paths:

After the successful save (after the file is written, just before `return SourceResult(...)` for the success case), add:

```python
        self._progress(f"Fetched: {_short_url(url)}")
```

In the FetchError except block and the bare Exception except block (the early returns for status="fetch_failed"), add BEFORE the return:

```python
            self._progress(f"Fetch failed ({_short_url(url)}): {exc}")
```

In `_summarize` (around line 184-200), after the successful summary write (just before `return source` on the success path), add:

```python
        self._progress(f"Summarized: {_short_url(source.url)}")
```

(Read the existing method carefully -- the success path is the one where `source.summary_path` gets set. Don't emit on the early-return cases where source is already in an error state.)

- [ ] **Step 4: Run new tests, verify they pass**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_researcher.py -k "per_url" -v
```

Expected: all 3 pass.

- [ ] **Step 5: Full Researcher test regression sweep**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_researcher.py -v 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/researcher.py tests/test_researcher.py && git commit -m "$(cat <<'EOF'
feat(researcher): per-URL fetch/summarize progress emission

Adds emissions inside _fetch_and_save and _summarize so chat-tool
research runs surface real-time per-source progress. Existing
topic-phase events stay intact; new events ride the same on_progress
callback already wired by handle_chat's per-turn _emit_progress.

URL truncation via _short_url keeps progress lines tight.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: PAL -- Discord embed multi-topic rendering + truncation

**Files:**
- Modify: `/home/edible/Projects/PAL/pal/discord_interactions.py`
- Test: `/home/edible/Projects/PAL/tests/test_discord_interactions.py`

- [ ] **Step 1: Write the failing tests**

Append to `/home/edible/Projects/PAL/tests/test_discord_interactions.py`:

```python
def test_research_embed_renders_topics_list():
    """Multi-topic proposal embed shows all topics as a bulleted list."""
    from pal.discord_interactions import build_research_proposal_embed
    from pal.protocol import ResearchProposalMessage

    msg = ResearchProposalMessage(
        proposal_id="abc",
        topic="3 topics: a, b, c",
        depth=3,
        rationale="test batch",
        topics=["docker networking", "k8s ingress", "service mesh"],
    )
    embed, _view = build_research_proposal_embed(msg)
    fields_text = "\n".join(f.value for f in embed.fields)
    assert "docker networking" in fields_text
    assert "k8s ingress" in fields_text
    assert "service mesh" in fields_text


def test_research_embed_truncates_long_topic_list():
    """A 50-topic proposal embed stays within Discord's 4096-char limit
    and includes a truncation trailer."""
    from pal.discord_interactions import build_research_proposal_embed
    from pal.protocol import ResearchProposalMessage

    topics = [f"topic-{i:02d}" for i in range(50)]
    msg = ResearchProposalMessage(
        proposal_id="abc",
        topic=f"50 topics: topic-00, topic-01, topic-02, ...",
        depth=3,
        rationale="big batch",
        topics=topics,
    )
    embed, _view = build_research_proposal_embed(msg)
    total_chars = sum(len(f.value) + len(f.name) for f in embed.fields) + len(embed.title or "")
    assert total_chars < 4000  # well under Discord's 4096 limit
    fields_text = "\n".join(f.value for f in embed.fields)
    assert "more not shown" in fields_text or "+" in fields_text  # truncation trailer
    assert "50" in fields_text  # total count preserved


def test_research_embed_single_topic_unchanged():
    """Regression pin: single-topic proposal embed shows topic only, no topics field."""
    from pal.discord_interactions import build_research_proposal_embed
    from pal.protocol import ResearchProposalMessage

    msg = ResearchProposalMessage(
        proposal_id="abc",
        topic="docker networking",
        depth=3,
        rationale="single",
    )
    embed, _view = build_research_proposal_embed(msg)
    fields_text = "\n".join(f.value for f in embed.fields)
    assert "docker networking" in fields_text
    assert "Topics" not in [f.name for f in embed.fields]  # no Topics field
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_discord_interactions.py -k "research_embed_renders or research_embed_truncates or research_embed_single" -v
```

Expected: the multi-topic and truncates tests fail; the single-topic regression pin probably passes.

- [ ] **Step 3: Update `build_research_proposal_embed`**

In `/home/edible/Projects/PAL/pal/discord_interactions.py`, locate `build_research_proposal_embed` (lines 60-91). Replace the body with:

```python
def build_research_proposal_embed(
    msg: ResearchProposalMessage,
) -> tuple[discord.Embed, discord.ui.View]:
    """Pure builder: returns the embed and a View with three buttons."""
    embed = discord.Embed(
        title="PAL proposes research",
        color=discord.Color.blurple(),
    )

    if msg.topics:
        # Multi-topic: render the full list with truncation.
        total = len(msg.topics)
        cap = _DISCORD_FIELD_VALUE_LIMIT - _FIELD_BUDGET_HEADROOM
        fitted: list[str] = []
        chars = 0
        for t in msg.topics:
            line = f"- {t}"
            add = len(line) + (1 if fitted else 0)  # +1 for newline separator
            if chars + add > cap:
                break
            fitted.append(line)
            chars += add
        dropped = total - len(fitted)
        topics_text = "\n".join(fitted)
        if dropped > 0:
            topics_text += f"\n... ({dropped} more not shown; total {total})"
        embed.add_field(
            name=f"Topics ({total})",
            value=topics_text if topics_text else "(empty)",
            inline=False,
        )
    else:
        embed.add_field(name="Topic", value=msg.topic, inline=False)

    embed.add_field(name="Depth", value=str(msg.depth), inline=True)
    embed.add_field(name="Rationale", value=msg.rationale, inline=False)

    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        style=discord.ButtonStyle.success,
        label="Approve",
        emoji="✅",
        custom_id=f"research:approve:{msg.proposal_id}",
    ))
    view.add_item(discord.ui.Button(
        style=discord.ButtonStyle.danger,
        label="Decline",
        emoji="❌",
        custom_id=f"research:decline:{msg.proposal_id}",
    ))
    view.add_item(discord.ui.Button(
        style=discord.ButtonStyle.secondary,
        label="Edit",
        emoji="✏️",
        custom_id=f"research:edit:{msg.proposal_id}",
    ))
    return embed, view
```

The `_DISCORD_FIELD_VALUE_LIMIT` and `_FIELD_BUDGET_HEADROOM` constants should already exist near the top of the file (used by the compile/reorg builders). If not, read the file and locate the existing truncation pattern -- it may use slightly different constant names.

- [ ] **Step 4: Run new tests, verify they pass**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_discord_interactions.py -k "research_embed_renders or research_embed_truncates or research_embed_single" -v
```

Expected: all 3 pass.

- [ ] **Step 5: Full Discord test regression sweep**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_discord_interactions.py -v 2>&1 | tail -10
```

Expected: all pass (existing single-topic tests still pass because the else branch preserves their behavior).

- [ ] **Step 6: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/discord_interactions.py tests/test_discord_interactions.py && git commit -m "$(cat <<'EOF'
feat(discord): render multi-topic research proposals with embed truncation

When ResearchProposalMessage.topics is non-empty, the embed shows the
full list as a Topics field with the existing cap/fitted/dropped
pattern used by compile/reorg builders. Long lists get a "... (N more
not shown; total M)" trailer so the embed stays under Discord's 4096-
char limit instead of silently failing.

Single-topic case unchanged; regression pinned in tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: PAL -- CLI multi-topic prompt rendering

**Files:**
- Modify: `/home/edible/Projects/PAL/pal/cli.py`
- Test: `/home/edible/Projects/PAL/tests/test_cli_research_proposal.py`

- [ ] **Step 1: Write the failing test**

Append to `/home/edible/Projects/PAL/tests/test_cli_research_proposal.py`:

```python
def test_format_research_proposal_renders_topics_list():
    """CLI prompt shows the full topic list when topics is set."""
    from pal.cli import format_research_proposal
    from pal.protocol import ResearchProposalMessage

    msg = ResearchProposalMessage(
        proposal_id="abc",
        topic="3 topics: a, b, c",
        depth=3,
        rationale="batch test",
        topics=["docker networking", "k8s ingress", "service mesh"],
    )
    prompt = format_research_proposal(msg)
    assert "docker networking" in prompt
    assert "k8s ingress" in prompt
    assert "service mesh" in prompt
    assert "Depth:     3" in prompt
    assert "Rationale: batch test" in prompt


def test_format_research_proposal_single_topic_unchanged():
    """Regression: single-topic CLI prompt shows the topic on Topic: line."""
    from pal.cli import format_research_proposal
    from pal.protocol import ResearchProposalMessage

    msg = ResearchProposalMessage(
        proposal_id="abc",
        topic="docker networking",
        depth=3,
        rationale="single",
    )
    prompt = format_research_proposal(msg)
    assert "Topic:     docker networking" in prompt
    # Multi-topic 'Topics:' header should NOT appear in single mode
    assert "Topics" not in prompt
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_cli_research_proposal.py -v
```

Expected: `test_format_research_proposal_renders_topics_list` fails (topics not rendered). Single-topic test passes (regression pin).

- [ ] **Step 3: Update `format_research_proposal`**

In `/home/edible/Projects/PAL/pal/cli.py`, locate `format_research_proposal` (lines 68-78). Replace with:

```python
def format_research_proposal(msg: ResearchProposalMessage) -> str:
    """Render a proposal approval prompt. Pure formatter for testability."""
    if msg.topics:
        # Multi-topic: render the full list under a Topics header.
        # Truncate at 30 topics for terminal scannability; trailer notes the rest.
        total = len(msg.topics)
        shown = msg.topics[:30]
        topic_lines = "\n".join(f"    - {t}" for t in shown)
        if total > 30:
            topic_lines += f"\n    ... ({total - 30} more not shown; total {total})"
        topic_block = f"  Topics ({total}):\n{topic_lines}"
    else:
        topic_block = f"  Topic:     {msg.topic}"

    return (
        "\n"
        "────────── PAL proposes research ──────────\n"
        f"{topic_block}\n"
        f"  Depth:     {msg.depth}\n"
        f"  Rationale: {msg.rationale}\n"
        "  [a]pprove  [d]ecline  [e]dit\n"
        "> "
    )
```

- [ ] **Step 4: Run new tests, verify they pass**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_cli_research_proposal.py -v
```

Expected: both pass.

- [ ] **Step 5: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/cli.py tests/test_cli_research_proposal.py && git commit -m "$(cat <<'EOF'
feat(cli): render multi-topic research proposals with topics list

When ResearchProposalMessage.topics is non-empty, the CLI prompt
shows the full list under a Topics header (capped at 30 for terminal
scannability; trailer notes the rest).

Single-topic case unchanged; regression pinned.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Full PAL suite + em-dash sweep + push

**Files:** none modified; verification only.

- [ ] **Step 1: Full PAL suite regression sweep**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/ \
    --ignore=tests/test_chat_research_integration.py \
    --ignore=tests/test_client.py \
    --ignore=tests/test_daemon.py \
    --ignore=tests/test_integration.py \
    --ignore=tests/test_prompt_injection.py \
    -q 2>&1 | tail -5
```

Expected: all pass.

- [ ] **Step 2: Em-dash sweep on the entire diff vs origin/main**

```bash
cd /home/edible/Projects/PAL && git diff origin/main..HEAD | grep -cP '[\x{2014}\x{2013}]'
```

Expected: `0`. If non-zero, locate and replace em dashes with `--` in the affected files; amend the relevant commit OR add a fixup commit.

- [ ] **Step 3: Verify branch**

```bash
cd /home/edible/Projects/PAL && git branch --show-current
```

Expected: `main`. If on a feature branch, coordinate with user before pushing.

- [ ] **Step 4: Push PAL main**

```bash
cd /home/edible/Projects/PAL && git push origin main 2>&1 | tail -5
```

Expected: the 8 new commits push to origin.

- [ ] **Step 5: Smoke test plan (user-driven, after server deploy)**

After the user pulls on the server and restarts BOTH PAL processes (pal-daemon + pal-discord), they should manually run:

1. **Single-topic** (regression): in chat, "research docker networking". Expect approval with Topic: docker networking, Depth: 3, Rationale. After approve, execution runs with per-URL "Fetched: ..." / "Summarized: ..." progress events visible.

2. **Inline multi-topic**: in chat, "research these topics: docker networking, k8s ingress, service mesh". Expect approval shows all 3 topics as a list. After approve, execution dedupes URLs across topics, per-URL progress visible.

3. **Multi-topic via file**: in chat, "research the topics in raw/notes/queue.md" (after creating the file with bullets). Expect model cats the file, parses bullets, calls propose_research with topics=[...], same approval/execution as inline case.

4. **Edit flow**: on a multi-topic proposal, click Edit (Discord) or press 'e' (CLI), change depth, approve. Execution should run all topics at the new depth (not drop the topic list).

5. **Long list (synthetic)**: in chat, "research these topics: t01, t02, t03, ..., t50" (or use a topic file with 50 bullets). Expect approval embed renders within Discord's limit with "... (N more not shown; total 50)" trailer.

---

## Self-review checklist (whole plan)

- [ ] Every task has exact file paths and exact commands.
- [ ] Every test step shows the assertion code in full.
- [ ] Every implementation step shows the actual code change.
- [ ] No "TBD", "TODO", "implement later" anywhere.
- [ ] Cross-repo coordination explicit: Task 1 in agent_core, Task 2 PAL pin bump, rest in PAL.
- [ ] Version bump in agent_core (1.2.2 -> 1.3.0) AND pin bump in PAL.
- [ ] Both PAL processes restart-required noted in smoke (Task 10 step 5).
- [ ] Branch-check reminder in every commit step (memory `feedback_check_branch_before_commit`).
- [ ] All commit messages end with the Co-Authored-By line.
- [ ] No em dashes in any commit message or added prompt/comment text.

## Out of scope (intentionally)

- `topic_file` parameter (model handles via cat).
- Editing the topic list in the approval modal (depth-only edit in v1).
- Concurrent topic execution.
- Per-URL progress for non-chat consumers of Researcher.
- Cleanup of `parse_topic_file()` (handled in slash-prune cleanup once that ships).
- Server-side deploy (user handles).
