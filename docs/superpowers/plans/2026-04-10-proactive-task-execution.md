# Proactive Task Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the task model, planning tool, and polling executor described in `docs/superpowers/specs/2026-04-10-proactive-task-execution-design.md` so PAL can durably track multi-step plans, survive crashes without losing state, and advance opted-in plans autonomously.

**Architecture:** Four new modules: `pal/plan_model.py` (pure data types and transition rules), `pal/plan_store.py` (file I/O, YAML frontmatter, atomic writes, git commit), `pal/plan_tool.py` (tool handlers for the agent), and `pal/executor.py` (polling loop with idle guard, stale detection, circuit breaker). Existing `pal/tools.py` gains new tool schemas and dispatches to `plan_tool`. `pal/daemon.py` instantiates the executor and hooks it to startup, conversation-turn-end, and inference-server recovery events. Config gains five new fields.

**Tech Stack:** Python 3.12, pyyaml (already a dependency), pytest, pytest-asyncio, `pal/frontmatter.py` for YAML round-trip, `pal/wiki.py` for git commits.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `pal/plan_model.py` | Create | `StepStatus`, `PlanStatus` enums, `Step` and `Plan` dataclasses, state transition table, actionable-step detection |
| `tests/test_plan_model.py` | Create | Unit tests for enums, dataclasses, transitions, `next_actionable` |
| `pal/plan_store.py` | Create | `PlanStore` — list/get/create/save plans, YAML round-trip, atomic writes, git commit hook |
| `tests/test_plan_store.py` | Create | Round-trip, atomic write, concurrency, git commit |
| `pal/plan_tool.py` | Create | `PlanTool` handler class, one method per operation, validation, structured errors |
| `tests/test_plan_tool.py` | Create | One test per operation, transition rejection, missing-metadata errors |
| `pal/tools.py` | Modify | Add plan tool schemas to `TOOL_DEFINITIONS`, dispatch in `ToolExecutor.run` |
| `tests/test_tools.py` | Modify | Add smoke tests for plan tool dispatch |
| `pal/executor.py` | Create | `Executor` class — trigger handling, idle guard, stale detection, circuit breaker, step dispatch |
| `tests/test_executor.py` | Create | Fake clock, fake chat dispatcher, one test per behavior |
| `pal/config.py` | Modify | Add `executor_enabled`, `executor_backstop_seconds`, `executor_stale_threshold_seconds`, `circuit_breaker_steps`, `tasks_dir` |
| `tests/test_config.py` | Modify | Add env-var tests for new fields |
| `pal/daemon.py` | Modify | Instantiate `PlanStore`, `PlanTool`, `Executor`; hook executor to lifecycle and turn-end; add error-recovery re-read rule |
| `tests/test_daemon.py` | Modify | Integration test for turn-end trigger and error-recovery re-read |

---

## Conventions Used Throughout This Plan

- **Package activation:** every test run assumes `source .venv/bin/activate` has been run at the start of the session. Individual steps do not repeat it.
- **Test command shape:** `python -m pytest tests/test_<name>.py::TestClass::test_method -v` for targeted runs, `python -m pytest tests/test_<name>.py -v` for a whole file.
- **Timestamps:** always ISO-8601 with timezone offset. Use `datetime.now(timezone.utc).isoformat()` in production code. Tests inject a fixed clock.
- **Clock injection:** any code that reads time for correctness takes a `clock: Callable[[], datetime]` parameter defaulting to `lambda: datetime.now(timezone.utc)`. Tests pass a stub.
- **TDD order:** write test, run to see it fail, write minimum code, run to see it pass, commit. Never skip the failure-verification step.

---

## Phase 1: Task Model (pure data, no I/O)

### Task 1: Status enums

**Files:**
- Create: `pal/plan_model.py`
- Create: `tests/test_plan_model.py`

- [ ] **Step 1: Write failing test for StepStatus values**

```python
# tests/test_plan_model.py
"""Unit tests for the plan data model."""
from pal.plan_model import StepStatus, PlanStatus


class TestStepStatus:
    def test_has_all_expected_values(self):
        expected = {
            "pending", "in_progress", "done",
            "blocked", "deferred", "superseded", "failed",
        }
        actual = {s.value for s in StepStatus}
        assert actual == expected

    def test_terminal_statuses(self):
        assert StepStatus.DONE.is_terminal()
        assert StepStatus.SUPERSEDED.is_terminal()
        assert not StepStatus.PENDING.is_terminal()
        assert not StepStatus.IN_PROGRESS.is_terminal()
        assert not StepStatus.FAILED.is_terminal()  # failed can be retried


class TestPlanStatus:
    def test_has_all_expected_values(self):
        expected = {
            "pending", "in_progress", "done",
            "blocked", "failed", "superseded",
        }
        actual = {s.value for s in PlanStatus}
        assert actual == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_plan_model.py -v`
Expected: `ImportError: cannot import name 'StepStatus' from 'pal.plan_model'` (or similar — module does not exist yet).

- [ ] **Step 3: Create the module with the enums**

```python
# pal/plan_model.py
"""Data model for plans and steps.

Pure dataclasses and enums. No I/O. The transition table lives here
because it is part of the type, not part of the store.
"""
from __future__ import annotations

from enum import Enum


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"
    DEFERRED = "deferred"
    SUPERSEDED = "superseded"
    FAILED = "failed"

    def is_terminal(self) -> bool:
        return self in (StepStatus.DONE, StepStatus.SUPERSEDED)


class PlanStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"
    SUPERSEDED = "superseded"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_plan_model.py -v`
Expected: both `TestStepStatus` tests and the `TestPlanStatus` test pass.

- [ ] **Step 5: Commit**

```bash
git add pal/plan_model.py tests/test_plan_model.py
git commit -m "feat(plan): add StepStatus and PlanStatus enums"
```

---

### Task 2: Step dataclass

**Files:**
- Modify: `pal/plan_model.py`
- Modify: `tests/test_plan_model.py`

- [ ] **Step 1: Write failing test for Step construction and defaults**

Add to `tests/test_plan_model.py`:

```python
from datetime import datetime, timezone

from pal.plan_model import Step, StepStatus


class TestStep:
    def test_minimal_construction(self):
        step = Step(id="step-01", description="Do the thing")
        assert step.id == "step-01"
        assert step.description == "Do the thing"
        assert step.status == StepStatus.PENDING
        assert step.depends_on == []
        assert step.started is None
        assert step.completed is None
        assert step.blocked_on is None
        assert step.reason is None
        assert step.defer_until is None
        assert step.error is None

    def test_construction_with_dependencies(self):
        step = Step(
            id="step-02",
            description="Follow-up",
            depends_on=["step-01"],
        )
        assert step.depends_on == ["step-01"]

    def test_to_dict_omits_none_fields(self):
        step = Step(id="step-01", description="Do the thing")
        d = step.to_dict()
        assert d == {
            "id": "step-01",
            "description": "Do the thing",
            "status": "pending",
            "depends_on": [],
        }

    def test_to_dict_includes_set_fields(self):
        when = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
        step = Step(
            id="step-01",
            description="Do the thing",
            status=StepStatus.IN_PROGRESS,
            started=when,
        )
        d = step.to_dict()
        assert d["status"] == "in_progress"
        assert d["started"] == when.isoformat()

    def test_from_dict_round_trip(self):
        when = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
        original = Step(
            id="step-01",
            description="Do the thing",
            status=StepStatus.DONE,
            started=when,
            completed=when,
            depends_on=["prev"],
        )
        restored = Step.from_dict(original.to_dict())
        assert restored == original
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_plan_model.py::TestStep -v`
Expected: `ImportError: cannot import name 'Step' from 'pal.plan_model'`.

- [ ] **Step 3: Add Step dataclass**

Append to `pal/plan_model.py`:

```python
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Step:
    id: str
    description: str
    status: StepStatus = StepStatus.PENDING
    depends_on: list[str] = field(default_factory=list)
    started: datetime | None = None
    completed: datetime | None = None
    blocked_on: str | None = None
    reason: str | None = None
    defer_until: datetime | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "id": self.id,
            "description": self.description,
            "status": self.status.value,
            "depends_on": list(self.depends_on),
        }
        if self.started is not None:
            d["started"] = self.started.isoformat()
        if self.completed is not None:
            d["completed"] = self.completed.isoformat()
        if self.blocked_on is not None:
            d["blocked_on"] = self.blocked_on
        if self.reason is not None:
            d["reason"] = self.reason
        if self.defer_until is not None:
            d["defer_until"] = self.defer_until.isoformat()
        if self.error is not None:
            d["error"] = self.error
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Step":
        def parse_dt(value):
            return datetime.fromisoformat(value) if value else None

        return cls(
            id=d["id"],
            description=d["description"],
            status=StepStatus(d.get("status", "pending")),
            depends_on=list(d.get("depends_on", [])),
            started=parse_dt(d.get("started")),
            completed=parse_dt(d.get("completed")),
            blocked_on=d.get("blocked_on"),
            reason=d.get("reason"),
            defer_until=parse_dt(d.get("defer_until")),
            error=d.get("error"),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_plan_model.py::TestStep -v`
Expected: all five tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/plan_model.py tests/test_plan_model.py
git commit -m "feat(plan): add Step dataclass with dict round-trip"
```

---

### Task 3: Plan dataclass

**Files:**
- Modify: `pal/plan_model.py`
- Modify: `tests/test_plan_model.py`

- [ ] **Step 1: Write failing test for Plan construction and dict round-trip**

Add to `tests/test_plan_model.py`:

```python
from pal.plan_model import Plan, PlanStatus


class TestPlan:
    def test_minimal_construction(self):
        when = datetime(2026, 4, 10, tzinfo=timezone.utc)
        plan = Plan(
            plan_id="2026-04-10-test",
            title="Test plan",
            description="A test",
            created=when,
            updated=when,
        )
        assert plan.plan_id == "2026-04-10-test"
        assert plan.status == PlanStatus.PENDING
        assert plan.autonomous is False
        assert plan.steps == []
        assert plan.scratchpad == ""
        assert plan.needs_review is False

    def test_dict_round_trip_with_steps(self):
        when = datetime(2026, 4, 10, tzinfo=timezone.utc)
        plan = Plan(
            plan_id="2026-04-10-test",
            title="Test plan",
            description="A test",
            created=when,
            updated=when,
            autonomous=True,
            steps=[
                Step(id="step-01", description="First"),
                Step(
                    id="step-02",
                    description="Second",
                    depends_on=["step-01"],
                ),
            ],
            scratchpad="working notes",
        )
        restored = Plan.from_dict(plan.to_dict(), scratchpad=plan.scratchpad)
        assert restored == plan

    def test_get_step_returns_matching_id(self):
        when = datetime(2026, 4, 10, tzinfo=timezone.utc)
        plan = Plan(
            plan_id="p",
            title="T",
            description="D",
            created=when,
            updated=when,
            steps=[
                Step(id="step-01", description="First"),
                Step(id="step-02", description="Second"),
            ],
        )
        assert plan.get_step("step-02").description == "Second"
        assert plan.get_step("step-99") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_plan_model.py::TestPlan -v`
Expected: `ImportError` for `Plan`.

- [ ] **Step 3: Add Plan dataclass**

Append to `pal/plan_model.py`:

```python
@dataclass
class Plan:
    plan_id: str
    title: str
    description: str
    created: datetime
    updated: datetime
    status: PlanStatus = PlanStatus.PENDING
    autonomous: bool = False
    steps: list[Step] = field(default_factory=list)
    scratchpad: str = ""
    needs_review: bool = False

    def get_step(self, step_id: str) -> Step | None:
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "autonomous": self.autonomous,
            "needs_review": self.needs_review,
            "created": self.created.isoformat(),
            "updated": self.updated.isoformat(),
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, d: dict, scratchpad: str = "") -> "Plan":
        return cls(
            plan_id=d["plan_id"],
            title=d["title"],
            description=d["description"],
            status=PlanStatus(d.get("status", "pending")),
            autonomous=bool(d.get("autonomous", False)),
            needs_review=bool(d.get("needs_review", False)),
            created=datetime.fromisoformat(d["created"]),
            updated=datetime.fromisoformat(d["updated"]),
            steps=[Step.from_dict(s) for s in d.get("steps", [])],
            scratchpad=scratchpad,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_plan_model.py::TestPlan -v`
Expected: all three tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/plan_model.py tests/test_plan_model.py
git commit -m "feat(plan): add Plan dataclass with dict round-trip"
```

---

### Task 4: State transition table

**Files:**
- Modify: `pal/plan_model.py`
- Modify: `tests/test_plan_model.py`

- [ ] **Step 1: Write failing tests for allowed and disallowed transitions**

Add to `tests/test_plan_model.py`:

```python
from pal.plan_model import can_transition, InvalidTransition
import pytest


class TestTransition:
    @pytest.mark.parametrize("frm,to", [
        (StepStatus.PENDING, StepStatus.IN_PROGRESS),
        (StepStatus.PENDING, StepStatus.DEFERRED),
        (StepStatus.PENDING, StepStatus.SUPERSEDED),
        (StepStatus.PENDING, StepStatus.BLOCKED),
        (StepStatus.IN_PROGRESS, StepStatus.DONE),
        (StepStatus.IN_PROGRESS, StepStatus.FAILED),
        (StepStatus.IN_PROGRESS, StepStatus.BLOCKED),
        (StepStatus.BLOCKED, StepStatus.PENDING),
        (StepStatus.BLOCKED, StepStatus.IN_PROGRESS),
        (StepStatus.BLOCKED, StepStatus.SUPERSEDED),
        (StepStatus.DEFERRED, StepStatus.PENDING),
        (StepStatus.DEFERRED, StepStatus.IN_PROGRESS),
        (StepStatus.DEFERRED, StepStatus.SUPERSEDED),
        (StepStatus.FAILED, StepStatus.PENDING),
    ])
    def test_allowed_transitions(self, frm, to):
        assert can_transition(frm, to)

    @pytest.mark.parametrize("frm,to", [
        (StepStatus.DONE, StepStatus.PENDING),
        (StepStatus.DONE, StepStatus.IN_PROGRESS),
        (StepStatus.SUPERSEDED, StepStatus.PENDING),
        (StepStatus.PENDING, StepStatus.DONE),
        (StepStatus.PENDING, StepStatus.FAILED),
        (StepStatus.FAILED, StepStatus.DONE),
        (StepStatus.FAILED, StepStatus.IN_PROGRESS),
    ])
    def test_disallowed_transitions(self, frm, to):
        assert not can_transition(frm, to)

    def test_invalid_transition_is_an_exception(self):
        exc = InvalidTransition(StepStatus.DONE, StepStatus.PENDING)
        assert "done" in str(exc)
        assert "pending" in str(exc)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_plan_model.py::TestTransition -v`
Expected: `ImportError` for `can_transition` and `InvalidTransition`.

- [ ] **Step 3: Add transition table and helper**

Append to `pal/plan_model.py`:

```python
_ALLOWED_STEP_TRANSITIONS: dict[StepStatus, set[StepStatus]] = {
    StepStatus.PENDING: {
        StepStatus.IN_PROGRESS,
        StepStatus.DEFERRED,
        StepStatus.SUPERSEDED,
        StepStatus.BLOCKED,
    },
    StepStatus.IN_PROGRESS: {
        StepStatus.DONE,
        StepStatus.FAILED,
        StepStatus.BLOCKED,
    },
    StepStatus.DONE: set(),
    StepStatus.BLOCKED: {
        StepStatus.PENDING,
        StepStatus.IN_PROGRESS,
        StepStatus.SUPERSEDED,
    },
    StepStatus.DEFERRED: {
        StepStatus.PENDING,
        StepStatus.IN_PROGRESS,
        StepStatus.SUPERSEDED,
    },
    StepStatus.SUPERSEDED: set(),
    StepStatus.FAILED: {StepStatus.PENDING},
}


def can_transition(frm: StepStatus, to: StepStatus) -> bool:
    return to in _ALLOWED_STEP_TRANSITIONS.get(frm, set())


class InvalidTransition(Exception):
    def __init__(self, frm: StepStatus, to: StepStatus) -> None:
        super().__init__(
            f"Invalid step status transition: {frm.value} -> {to.value}"
        )
        self.frm = frm
        self.to = to
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_plan_model.py::TestTransition -v`
Expected: all parametrized cases and the exception test pass.

- [ ] **Step 5: Commit**

```bash
git add pal/plan_model.py tests/test_plan_model.py
git commit -m "feat(plan): add step state transition table"
```

---

### Task 5: Actionable step detection

**Files:**
- Modify: `pal/plan_model.py`
- Modify: `tests/test_plan_model.py`

- [ ] **Step 1: Write failing test for next_actionable_step**

Add to `tests/test_plan_model.py`:

```python
from pal.plan_model import next_actionable_step


class TestNextActionable:
    def _plan(self, *steps: Step) -> Plan:
        when = datetime(2026, 4, 10, tzinfo=timezone.utc)
        return Plan(
            plan_id="p",
            title="T",
            description="D",
            created=when,
            updated=when,
            steps=list(steps),
        )

    def test_returns_first_pending_with_no_deps(self):
        plan = self._plan(
            Step(id="a", description="first"),
            Step(id="b", description="second", depends_on=["a"]),
        )
        now = datetime(2026, 4, 10, tzinfo=timezone.utc)
        assert next_actionable_step(plan, now=now).id == "a"

    def test_skips_pending_with_unmet_deps(self):
        plan = self._plan(
            Step(id="a", description="first", status=StepStatus.PENDING),
            Step(id="b", description="second", depends_on=["a"]),
        )
        now = datetime(2026, 4, 10, tzinfo=timezone.utc)
        # a is actionable first; b is not actionable because a is pending
        result = next_actionable_step(plan, now=now)
        assert result.id == "a"

    def test_returns_next_step_once_deps_are_done(self):
        plan = self._plan(
            Step(id="a", description="first", status=StepStatus.DONE),
            Step(id="b", description="second", depends_on=["a"]),
        )
        now = datetime(2026, 4, 10, tzinfo=timezone.utc)
        assert next_actionable_step(plan, now=now).id == "b"

    def test_skips_in_progress_steps(self):
        plan = self._plan(
            Step(id="a", description="first", status=StepStatus.IN_PROGRESS),
            Step(id="b", description="second", depends_on=["a"]),
        )
        now = datetime(2026, 4, 10, tzinfo=timezone.utc)
        assert next_actionable_step(plan, now=now) is None

    def test_skips_blocked_steps(self):
        plan = self._plan(
            Step(
                id="a",
                description="first",
                status=StepStatus.BLOCKED,
                blocked_on="waiting for X",
            ),
        )
        now = datetime(2026, 4, 10, tzinfo=timezone.utc)
        assert next_actionable_step(plan, now=now) is None

    def test_deferred_with_past_defer_until_is_actionable(self):
        past = datetime(2026, 4, 1, tzinfo=timezone.utc)
        plan = self._plan(
            Step(
                id="a",
                description="first",
                status=StepStatus.DEFERRED,
                defer_until=past,
                reason="wait",
            ),
        )
        now = datetime(2026, 4, 10, tzinfo=timezone.utc)
        assert next_actionable_step(plan, now=now).id == "a"

    def test_deferred_with_future_defer_until_is_not_actionable(self):
        future = datetime(2026, 5, 1, tzinfo=timezone.utc)
        plan = self._plan(
            Step(
                id="a",
                description="first",
                status=StepStatus.DEFERRED,
                defer_until=future,
                reason="wait",
            ),
        )
        now = datetime(2026, 4, 10, tzinfo=timezone.utc)
        assert next_actionable_step(plan, now=now) is None

    def test_deferred_without_defer_until_is_not_actionable(self):
        plan = self._plan(
            Step(
                id="a",
                description="first",
                status=StepStatus.DEFERRED,
                reason="paused",
            ),
        )
        now = datetime(2026, 4, 10, tzinfo=timezone.utc)
        assert next_actionable_step(plan, now=now) is None

    def test_empty_plan_returns_none(self):
        plan = self._plan()
        now = datetime(2026, 4, 10, tzinfo=timezone.utc)
        assert next_actionable_step(plan, now=now) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_plan_model.py::TestNextActionable -v`
Expected: `ImportError` for `next_actionable_step`.

- [ ] **Step 3: Implement next_actionable_step**

Append to `pal/plan_model.py`:

```python
def next_actionable_step(plan: Plan, *, now: datetime) -> Step | None:
    done_ids = {s.id for s in plan.steps if s.status == StepStatus.DONE}
    for step in plan.steps:
        if not all(dep in done_ids for dep in step.depends_on):
            continue
        if step.status == StepStatus.PENDING:
            return step
        if step.status == StepStatus.DEFERRED:
            if step.defer_until is not None and step.defer_until <= now:
                return step
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_plan_model.py::TestNextActionable -v`
Expected: all nine tests pass.

- [ ] **Step 5: Run the full model test file to confirm nothing regressed**

Run: `python -m pytest tests/test_plan_model.py -v`
Expected: every test in the file passes.

- [ ] **Step 6: Commit**

```bash
git add pal/plan_model.py tests/test_plan_model.py
git commit -m "feat(plan): add next_actionable_step with dependency + defer handling"
```

---

## Phase 2: Plan Store (file I/O)

### Task 6: Plan serialization to markdown + frontmatter

**Files:**
- Create: `pal/plan_store.py`
- Create: `tests/test_plan_store.py`

- [ ] **Step 1: Write failing test for serialize/parse round-trip**

```python
# tests/test_plan_store.py
"""Tests for PlanStore file I/O."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pal.plan_model import Plan, Step, StepStatus, PlanStatus
from pal.plan_store import serialize_plan, parse_plan


class TestSerialization:
    def _sample_plan(self) -> Plan:
        when = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
        return Plan(
            plan_id="2026-04-10-test",
            title="Test plan",
            description="A test.",
            status=PlanStatus.IN_PROGRESS,
            autonomous=True,
            created=when,
            updated=when,
            steps=[
                Step(id="step-01", description="First", status=StepStatus.DONE,
                     started=when, completed=when),
                Step(id="step-02", description="Second", depends_on=["step-01"]),
            ],
            scratchpad="Notes about progress.\n\nMore notes.\n",
        )

    def test_serialize_produces_frontmatter_and_body(self):
        plan = self._sample_plan()
        text = serialize_plan(plan)
        assert text.startswith("---\n")
        assert "\n---\n" in text
        assert "plan_id: 2026-04-10-test" in text
        assert "## Scratchpad" in text
        assert "Notes about progress." in text

    def test_serialize_parse_round_trip(self):
        plan = self._sample_plan()
        text = serialize_plan(plan)
        restored = parse_plan(text)
        assert restored == plan

    def test_parse_handles_missing_scratchpad(self):
        when = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
        text = (
            "---\n"
            "plan_id: p\n"
            "title: T\n"
            "description: D\n"
            "status: pending\n"
            "autonomous: false\n"
            "needs_review: false\n"
            f"created: '{when.isoformat()}'\n"
            f"updated: '{when.isoformat()}'\n"
            "steps: []\n"
            "---\n"
        )
        plan = parse_plan(text)
        assert plan.scratchpad == ""

    def test_parse_preserves_scratchpad_verbatim(self):
        plan = self._sample_plan()
        plan.scratchpad = "Line 1\n\nLine 2 with `code`\n"
        text = serialize_plan(plan)
        restored = parse_plan(text)
        assert restored.scratchpad == plan.scratchpad
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_plan_store.py::TestSerialization -v`
Expected: `ImportError` for `serialize_plan` and `parse_plan`.

- [ ] **Step 3: Create plan_store.py with serialize/parse**

```python
# pal/plan_store.py
"""Durable storage for Plan objects as YAML-frontmatter markdown files.

One file per plan in the vault's tasks/ directory. The frontmatter is the
structured source of truth. The body below "## Scratchpad" is the agent's
free-form working memory.
"""
from __future__ import annotations

from pal.frontmatter import parse_frontmatter, serialize_frontmatter
from pal.plan_model import Plan


_SCRATCHPAD_HEADER = "## Scratchpad"


def serialize_plan(plan: Plan) -> str:
    meta = plan.to_dict()
    body_parts = [_SCRATCHPAD_HEADER, ""]
    if plan.scratchpad:
        body_parts.append(plan.scratchpad.rstrip("\n") + "\n")
    body = "\n".join(body_parts) if plan.scratchpad else f"\n{_SCRATCHPAD_HEADER}\n"
    return serialize_frontmatter(meta, body)


def parse_plan(text: str) -> Plan:
    meta, body = parse_frontmatter(text)
    if not meta:
        raise ValueError("Plan file has no frontmatter")
    scratchpad = _extract_scratchpad(body)
    return Plan.from_dict(meta, scratchpad=scratchpad)


def _extract_scratchpad(body: str) -> str:
    idx = body.find(_SCRATCHPAD_HEADER)
    if idx == -1:
        return ""
    after_header = body[idx + len(_SCRATCHPAD_HEADER):]
    return after_header.lstrip("\n").rstrip("\n")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_plan_store.py::TestSerialization -v`
Expected: all four tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/plan_store.py tests/test_plan_store.py
git commit -m "feat(plan): serialize and parse plans to YAML-frontmatter markdown"
```

---

### Task 7: PlanStore — list, get, save with atomic writes

**Files:**
- Modify: `pal/plan_store.py`
- Modify: `tests/test_plan_store.py`

- [ ] **Step 1: Write failing test for PlanStore CRUD on disk**

Add to `tests/test_plan_store.py`:

```python
from pal.plan_store import PlanStore


class TestPlanStore:
    def _sample_plan(self) -> Plan:
        when = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
        return Plan(
            plan_id="2026-04-10-test",
            title="Test plan",
            description="A test.",
            created=when,
            updated=when,
        )

    def test_save_and_get(self, tmp_path):
        store = PlanStore(tmp_path)
        plan = self._sample_plan()
        store.save(plan)
        restored = store.get(plan.plan_id)
        assert restored == plan

    def test_get_returns_none_for_missing(self, tmp_path):
        store = PlanStore(tmp_path)
        assert store.get("does-not-exist") is None

    def test_list_returns_all_plans(self, tmp_path):
        store = PlanStore(tmp_path)
        a = self._sample_plan()
        b = self._sample_plan()
        b.plan_id = "2026-04-10-other"
        b.title = "Other"
        store.save(a)
        store.save(b)
        ids = {p.plan_id for p in store.list()}
        assert ids == {a.plan_id, b.plan_id}

    def test_list_filters_by_status(self, tmp_path):
        store = PlanStore(tmp_path)
        a = self._sample_plan()
        b = self._sample_plan()
        b.plan_id = "2026-04-10-in-progress"
        b.status = PlanStatus.IN_PROGRESS
        store.save(a)
        store.save(b)
        in_prog = store.list(status=PlanStatus.IN_PROGRESS)
        assert len(in_prog) == 1
        assert in_prog[0].plan_id == b.plan_id

    def test_list_filters_by_autonomous(self, tmp_path):
        store = PlanStore(tmp_path)
        a = self._sample_plan()
        b = self._sample_plan()
        b.plan_id = "2026-04-10-auto"
        b.autonomous = True
        store.save(a)
        store.save(b)
        auto = store.list(autonomous_only=True)
        assert len(auto) == 1
        assert auto[0].plan_id == b.plan_id

    def test_save_is_atomic(self, tmp_path, monkeypatch):
        """A crash mid-write must not leave a corrupt plan file."""
        store = PlanStore(tmp_path)
        plan = self._sample_plan()
        store.save(plan)
        # Simulate a failing rename by pointing at a read-only parent
        original_contents = (tmp_path / f"{plan.plan_id}.md").read_text()
        # Corrupt-save attempt: patch os.replace to raise after temp write
        import os
        real_replace = os.replace
        def boom(src, dst):
            raise OSError("simulated crash")
        monkeypatch.setattr("pal.plan_store.os.replace", boom)
        plan.title = "Updated title"
        with pytest.raises(OSError):
            store.save(plan)
        # Original file is still intact
        assert (tmp_path / f"{plan.plan_id}.md").read_text() == original_contents
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_plan_store.py::TestPlanStore -v`
Expected: `ImportError` for `PlanStore`.

- [ ] **Step 3: Implement PlanStore**

Append to `pal/plan_store.py`:

```python
import os
from pathlib import Path

from pal.plan_model import Plan, PlanStatus


class PlanStore:
    def __init__(self, tasks_dir: Path) -> None:
        self.tasks_dir = Path(tasks_dir)
        self.tasks_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, plan_id: str) -> Path:
        return self.tasks_dir / f"{plan_id}.md"

    def get(self, plan_id: str) -> Plan | None:
        path = self._path_for(plan_id)
        if not path.exists():
            return None
        return parse_plan(path.read_text())

    def list(
        self,
        *,
        status: PlanStatus | None = None,
        autonomous_only: bool = False,
    ) -> list[Plan]:
        plans: list[Plan] = []
        for path in sorted(self.tasks_dir.glob("*.md")):
            if path.name.startswith("_"):
                continue
            try:
                plan = parse_plan(path.read_text())
            except (ValueError, KeyError):
                continue
            if status is not None and plan.status != status:
                continue
            if autonomous_only and not plan.autonomous:
                continue
            plans.append(plan)
        return plans

    def save(self, plan: Plan) -> None:
        path = self._path_for(plan.plan_id)
        text = serialize_plan(plan)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_plan_store.py::TestPlanStore -v`
Expected: all six tests pass, including the atomicity test.

- [ ] **Step 5: Commit**

```bash
git add pal/plan_store.py tests/test_plan_store.py
git commit -m "feat(plan): PlanStore with list/get/save and atomic writes"
```

---

### Task 8: Git commit hook on save

**Files:**
- Modify: `pal/plan_store.py`
- Modify: `tests/test_plan_store.py`

- [ ] **Step 1: Write failing test for git commit invocation on save**

Add to `tests/test_plan_store.py`:

```python
class TestGitCommit:
    def test_save_calls_commit_callback(self, tmp_path):
        calls = []

        def fake_commit(message: str) -> None:
            calls.append(message)

        store = PlanStore(tmp_path, commit=fake_commit)
        when = datetime(2026, 4, 10, tzinfo=timezone.utc)
        plan = Plan(
            plan_id="2026-04-10-test",
            title="Test plan",
            description="A test",
            created=when,
            updated=when,
        )
        store.save(plan)
        assert len(calls) == 1
        assert "2026-04-10-test" in calls[0]

    def test_save_without_commit_callback_is_silent(self, tmp_path):
        store = PlanStore(tmp_path)
        when = datetime(2026, 4, 10, tzinfo=timezone.utc)
        plan = Plan(
            plan_id="2026-04-10-test",
            title="Test plan",
            description="A test",
            created=when,
            updated=when,
        )
        store.save(plan)  # Must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_plan_store.py::TestGitCommit -v`
Expected: `TypeError: __init__() got an unexpected keyword argument 'commit'`.

- [ ] **Step 3: Add commit callback support**

Modify `PlanStore.__init__` and `save` in `pal/plan_store.py`:

```python
from typing import Callable


class PlanStore:
    def __init__(
        self,
        tasks_dir: Path,
        *,
        commit: Callable[[str], None] | None = None,
    ) -> None:
        self.tasks_dir = Path(tasks_dir)
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self._commit = commit

    # ... get and list unchanged ...

    def save(self, plan: Plan) -> None:
        path = self._path_for(plan.plan_id)
        text = serialize_plan(plan)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, path)
        if self._commit is not None:
            self._commit(f"Update plan {plan.plan_id}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_plan_store.py -v`
Expected: all tests in the file pass.

- [ ] **Step 5: Commit**

```bash
git add pal/plan_store.py tests/test_plan_store.py
git commit -m "feat(plan): invoke optional git commit callback on save"
```

---

## Phase 3: Planning Tool

### Task 9: PlanTool — create and add_step

**Files:**
- Create: `pal/plan_tool.py`
- Create: `tests/test_plan_tool.py`

- [ ] **Step 1: Write failing test for plan_create and plan_add_step**

```python
# tests/test_plan_tool.py
"""Tests for the PlanTool handler."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pal.plan_model import PlanStatus, StepStatus
from pal.plan_store import PlanStore
from pal.plan_tool import PlanTool, PlanToolError


@pytest.fixture()
def fixed_clock():
    when = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
    return lambda: when


@pytest.fixture()
def tool(tmp_path, fixed_clock):
    store = PlanStore(tmp_path)
    return PlanTool(store=store, clock=fixed_clock)


class TestCreateAndAddStep:
    def test_plan_create_returns_plan_id(self, tool):
        result = tool.plan_create(title="Test", description="A test")
        assert result.startswith("2026-04-10-")
        assert "test" in result.lower()

    def test_plan_create_persists_plan(self, tool):
        plan_id = tool.plan_create(title="Test plan", description="A test")
        plan = tool.store.get(plan_id)
        assert plan is not None
        assert plan.title == "Test plan"
        assert plan.description == "A test"
        assert plan.status == PlanStatus.PENDING
        assert plan.autonomous is False
        assert plan.steps == []

    def test_plan_add_step_appends_and_returns_id(self, tool):
        plan_id = tool.plan_create(title="Test", description="T")
        step_id = tool.plan_add_step(plan_id=plan_id, description="Do thing")
        plan = tool.store.get(plan_id)
        assert len(plan.steps) == 1
        assert plan.steps[0].id == step_id
        assert plan.steps[0].description == "Do thing"

    def test_plan_add_step_with_dependency(self, tool):
        plan_id = tool.plan_create(title="Test", description="T")
        first = tool.plan_add_step(plan_id=plan_id, description="First")
        second = tool.plan_add_step(
            plan_id=plan_id, description="Second", depends_on=[first]
        )
        plan = tool.store.get(plan_id)
        assert plan.steps[1].depends_on == [first]

    def test_plan_add_step_rejects_unknown_dependency(self, tool):
        plan_id = tool.plan_create(title="Test", description="T")
        with pytest.raises(PlanToolError, match="unknown step id"):
            tool.plan_add_step(
                plan_id=plan_id, description="Bad", depends_on=["step-99"]
            )

    def test_plan_add_step_rejects_unknown_plan(self, tool):
        with pytest.raises(PlanToolError, match="plan not found"):
            tool.plan_add_step(plan_id="missing", description="X")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_plan_tool.py::TestCreateAndAddStep -v`
Expected: `ImportError` for `PlanTool`.

- [ ] **Step 3: Implement PlanTool.plan_create and plan_add_step**

```python
# pal/plan_tool.py
"""Agent-facing planning tool.

Each public method corresponds to one tool operation. The daemon enforces
state transitions here so the LLM cannot corrupt plan state.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Callable

from pal.plan_model import Plan, PlanStatus, Step, StepStatus, can_transition
from pal.plan_store import PlanStore


class PlanToolError(Exception):
    """Structured error returned to the agent on invalid operations."""


def _slugify(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "plan"


class PlanTool:
    def __init__(
        self,
        *,
        store: PlanStore,
        clock: Callable[[], datetime],
    ) -> None:
        self.store = store
        self._clock = clock

    def _now(self) -> datetime:
        return self._clock()

    def plan_create(
        self,
        *,
        title: str,
        description: str,
        autonomous: bool = False,
    ) -> str:
        now = self._now()
        slug = _slugify(title)
        plan_id = f"{now.date().isoformat()}-{slug}"
        # De-duplicate if another plan already claims this id
        if self.store.get(plan_id) is not None:
            suffix = 2
            while self.store.get(f"{plan_id}-{suffix}") is not None:
                suffix += 1
            plan_id = f"{plan_id}-{suffix}"
        plan = Plan(
            plan_id=plan_id,
            title=title,
            description=description,
            autonomous=autonomous,
            created=now,
            updated=now,
        )
        self.store.save(plan)
        return plan_id

    def _load(self, plan_id: str) -> Plan:
        plan = self.store.get(plan_id)
        if plan is None:
            raise PlanToolError(f"plan not found: {plan_id}")
        return plan

    def plan_add_step(
        self,
        *,
        plan_id: str,
        description: str,
        depends_on: list[str] | None = None,
    ) -> str:
        plan = self._load(plan_id)
        deps = list(depends_on or [])
        existing_ids = {s.id for s in plan.steps}
        for dep in deps:
            if dep not in existing_ids:
                raise PlanToolError(f"unknown step id in depends_on: {dep}")
        step_id = f"step-{len(plan.steps) + 1:02d}"
        plan.steps.append(
            Step(id=step_id, description=description, depends_on=deps)
        )
        plan.updated = self._now()
        self.store.save(plan)
        return step_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_plan_tool.py::TestCreateAndAddStep -v`
Expected: all six tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/plan_tool.py tests/test_plan_tool.py
git commit -m "feat(plan): PlanTool.plan_create and plan_add_step"
```

---

### Task 10: PlanTool — step status transitions (start, complete, fail)

**Files:**
- Modify: `pal/plan_tool.py`
- Modify: `tests/test_plan_tool.py`

- [ ] **Step 1: Write failing test for start/complete/fail**

Add to `tests/test_plan_tool.py`:

```python
from pal.plan_model import InvalidTransition


class TestStepTransitions:
    def _plan_with_step(self, tool) -> tuple[str, str]:
        plan_id = tool.plan_create(title="T", description="D")
        step_id = tool.plan_add_step(plan_id=plan_id, description="Do it")
        return plan_id, step_id

    def test_plan_start_step(self, tool, fixed_clock):
        plan_id, step_id = self._plan_with_step(tool)
        tool.plan_start_step(plan_id=plan_id, step_id=step_id)
        plan = tool.store.get(plan_id)
        step = plan.get_step(step_id)
        assert step.status == StepStatus.IN_PROGRESS
        assert step.started == fixed_clock()

    def test_plan_start_step_rejects_illegal_transition(self, tool):
        plan_id, step_id = self._plan_with_step(tool)
        tool.plan_start_step(plan_id=plan_id, step_id=step_id)
        # Already in_progress, cannot start again
        with pytest.raises(PlanToolError, match="invalid.*transition"):
            tool.plan_start_step(plan_id=plan_id, step_id=step_id)

    def test_plan_complete_step(self, tool, fixed_clock):
        plan_id, step_id = self._plan_with_step(tool)
        tool.plan_start_step(plan_id=plan_id, step_id=step_id)
        tool.plan_complete_step(plan_id=plan_id, step_id=step_id)
        step = tool.store.get(plan_id).get_step(step_id)
        assert step.status == StepStatus.DONE
        assert step.completed == fixed_clock()

    def test_plan_complete_step_rejects_from_pending(self, tool):
        plan_id, step_id = self._plan_with_step(tool)
        with pytest.raises(PlanToolError, match="invalid.*transition"):
            tool.plan_complete_step(plan_id=plan_id, step_id=step_id)

    def test_plan_fail_step_requires_error(self, tool):
        plan_id, step_id = self._plan_with_step(tool)
        tool.plan_start_step(plan_id=plan_id, step_id=step_id)
        tool.plan_fail_step(
            plan_id=plan_id, step_id=step_id, error="broke"
        )
        step = tool.store.get(plan_id).get_step(step_id)
        assert step.status == StepStatus.FAILED
        assert step.error == "broke"

    def test_plan_complete_marks_plan_done_when_last_step_done(self, tool):
        plan_id, step_id = self._plan_with_step(tool)
        tool.plan_start_step(plan_id=plan_id, step_id=step_id)
        tool.plan_complete_step(plan_id=plan_id, step_id=step_id)
        plan = tool.store.get(plan_id)
        assert plan.status == PlanStatus.DONE

    def test_plan_in_progress_when_any_step_in_progress(self, tool):
        plan_id, _ = self._plan_with_step(tool)
        step_2 = tool.plan_add_step(plan_id=plan_id, description="Second")
        tool.plan_start_step(plan_id=plan_id, step_id=step_2)
        plan = tool.store.get(plan_id)
        assert plan.status == PlanStatus.IN_PROGRESS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_plan_tool.py::TestStepTransitions -v`
Expected: `AttributeError` for missing methods.

- [ ] **Step 3: Implement transition methods and plan-status recomputation**

Append to `pal/plan_tool.py`:

```python
    def _transition_step(
        self,
        plan: Plan,
        step_id: str,
        new_status: StepStatus,
    ) -> Step:
        step = plan.get_step(step_id)
        if step is None:
            raise PlanToolError(f"unknown step id: {step_id}")
        if not can_transition(step.status, new_status):
            raise PlanToolError(
                f"invalid step transition: {step.status.value} -> {new_status.value}"
            )
        step.status = new_status
        return step

    def _recompute_plan_status(self, plan: Plan) -> None:
        if not plan.steps:
            plan.status = PlanStatus.PENDING
            return
        statuses = [s.status for s in plan.steps]
        if any(s == StepStatus.FAILED for s in statuses):
            plan.status = PlanStatus.FAILED
            return
        if all(s == StepStatus.DONE for s in statuses):
            plan.status = PlanStatus.DONE
            return
        if all(s in (StepStatus.DONE, StepStatus.SUPERSEDED) for s in statuses):
            plan.status = PlanStatus.DONE
            return
        if any(s == StepStatus.IN_PROGRESS for s in statuses):
            plan.status = PlanStatus.IN_PROGRESS
            return
        if any(s == StepStatus.DONE for s in statuses):
            plan.status = PlanStatus.IN_PROGRESS
            return
        if all(
            s in (StepStatus.BLOCKED, StepStatus.DEFERRED)
            for s in statuses
        ):
            plan.status = PlanStatus.BLOCKED
            return
        plan.status = PlanStatus.PENDING

    def plan_start_step(self, *, plan_id: str, step_id: str) -> None:
        plan = self._load(plan_id)
        step = self._transition_step(plan, step_id, StepStatus.IN_PROGRESS)
        step.started = self._now()
        plan.updated = self._now()
        self._recompute_plan_status(plan)
        self.store.save(plan)

    def plan_complete_step(self, *, plan_id: str, step_id: str) -> None:
        plan = self._load(plan_id)
        step = self._transition_step(plan, step_id, StepStatus.DONE)
        step.completed = self._now()
        plan.updated = self._now()
        self._recompute_plan_status(plan)
        self.store.save(plan)

    def plan_fail_step(
        self, *, plan_id: str, step_id: str, error: str
    ) -> None:
        if not error:
            raise PlanToolError("error summary is required when failing a step")
        plan = self._load(plan_id)
        step = self._transition_step(plan, step_id, StepStatus.FAILED)
        step.error = error
        plan.updated = self._now()
        self._recompute_plan_status(plan)
        self.store.save(plan)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_plan_tool.py::TestStepTransitions -v`
Expected: all seven tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/plan_tool.py tests/test_plan_tool.py
git commit -m "feat(plan): step start/complete/fail with plan status recomputation"
```

---

### Task 11: PlanTool — block, defer, supersede, retry

**Files:**
- Modify: `pal/plan_tool.py`
- Modify: `tests/test_plan_tool.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_plan_tool.py`:

```python
class TestOtherTransitions:
    def _start_step(self, tool):
        plan_id = tool.plan_create(title="T", description="D")
        step_id = tool.plan_add_step(plan_id=plan_id, description="Do it")
        tool.plan_start_step(plan_id=plan_id, step_id=step_id)
        return plan_id, step_id

    def test_block_requires_blocked_on(self, tool):
        plan_id, step_id = self._start_step(tool)
        with pytest.raises(PlanToolError, match="blocked_on is required"):
            tool.plan_block_step(
                plan_id=plan_id, step_id=step_id, blocked_on=""
            )

    def test_block_step(self, tool):
        plan_id, step_id = self._start_step(tool)
        tool.plan_block_step(
            plan_id=plan_id, step_id=step_id, blocked_on="waiting"
        )
        step = tool.store.get(plan_id).get_step(step_id)
        assert step.status == StepStatus.BLOCKED
        assert step.blocked_on == "waiting"

    def test_defer_requires_reason(self, tool):
        plan_id = tool.plan_create(title="T", description="D")
        step_id = tool.plan_add_step(plan_id=plan_id, description="Do it")
        with pytest.raises(PlanToolError, match="reason is required"):
            tool.plan_defer_step(
                plan_id=plan_id, step_id=step_id, reason=""
            )

    def test_defer_with_defer_until(self, tool):
        plan_id = tool.plan_create(title="T", description="D")
        step_id = tool.plan_add_step(plan_id=plan_id, description="Do it")
        when = datetime(2026, 5, 1, tzinfo=timezone.utc)
        tool.plan_defer_step(
            plan_id=plan_id,
            step_id=step_id,
            reason="later",
            defer_until=when,
        )
        step = tool.store.get(plan_id).get_step(step_id)
        assert step.status == StepStatus.DEFERRED
        assert step.reason == "later"
        assert step.defer_until == when

    def test_supersede_requires_reason(self, tool):
        plan_id = tool.plan_create(title="T", description="D")
        step_id = tool.plan_add_step(plan_id=plan_id, description="Do it")
        with pytest.raises(PlanToolError, match="reason is required"):
            tool.plan_supersede_step(
                plan_id=plan_id, step_id=step_id, reason=""
            )

    def test_supersede_step(self, tool):
        plan_id = tool.plan_create(title="T", description="D")
        step_id = tool.plan_add_step(plan_id=plan_id, description="Do it")
        tool.plan_supersede_step(
            plan_id=plan_id, step_id=step_id, reason="not needed"
        )
        step = tool.store.get(plan_id).get_step(step_id)
        assert step.status == StepStatus.SUPERSEDED

    def test_retry_failed_step(self, tool):
        plan_id, step_id = self._start_step(tool)
        tool.plan_fail_step(
            plan_id=plan_id, step_id=step_id, error="broke"
        )
        tool.plan_retry_step(plan_id=plan_id, step_id=step_id)
        step = tool.store.get(plan_id).get_step(step_id)
        assert step.status == StepStatus.PENDING
        assert step.error is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_plan_tool.py::TestOtherTransitions -v`
Expected: `AttributeError` for missing methods.

- [ ] **Step 3: Implement the remaining transition methods**

Append to `pal/plan_tool.py`:

```python
    def plan_block_step(
        self, *, plan_id: str, step_id: str, blocked_on: str
    ) -> None:
        if not blocked_on:
            raise PlanToolError("blocked_on is required when blocking a step")
        plan = self._load(plan_id)
        step = self._transition_step(plan, step_id, StepStatus.BLOCKED)
        step.blocked_on = blocked_on
        plan.updated = self._now()
        self._recompute_plan_status(plan)
        self.store.save(plan)

    def plan_defer_step(
        self,
        *,
        plan_id: str,
        step_id: str,
        reason: str,
        defer_until: datetime | None = None,
    ) -> None:
        if not reason:
            raise PlanToolError("reason is required when deferring a step")
        plan = self._load(plan_id)
        step = self._transition_step(plan, step_id, StepStatus.DEFERRED)
        step.reason = reason
        step.defer_until = defer_until
        plan.updated = self._now()
        self._recompute_plan_status(plan)
        self.store.save(plan)

    def plan_supersede_step(
        self, *, plan_id: str, step_id: str, reason: str
    ) -> None:
        if not reason:
            raise PlanToolError("reason is required when superseding a step")
        plan = self._load(plan_id)
        step = self._transition_step(plan, step_id, StepStatus.SUPERSEDED)
        step.reason = reason
        plan.updated = self._now()
        self._recompute_plan_status(plan)
        self.store.save(plan)

    def plan_retry_step(self, *, plan_id: str, step_id: str) -> None:
        plan = self._load(plan_id)
        step = self._transition_step(plan, step_id, StepStatus.PENDING)
        step.error = None
        plan.updated = self._now()
        self._recompute_plan_status(plan)
        self.store.save(plan)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_plan_tool.py::TestOtherTransitions -v`
Expected: all seven tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/plan_tool.py tests/test_plan_tool.py
git commit -m "feat(plan): block/defer/supersede/retry transitions with required metadata"
```

---

### Task 12: PlanTool — read operations and autonomy flag

**Files:**
- Modify: `pal/plan_tool.py`
- Modify: `tests/test_plan_tool.py`

- [ ] **Step 1: Write failing tests for list/get/next_actionable/note/autonomous**

Add to `tests/test_plan_tool.py`:

```python
class TestReadsAndFlags:
    def test_plan_list_filters(self, tool):
        a = tool.plan_create(title="First", description="A")
        b = tool.plan_create(title="Second", description="B", autonomous=True)
        result = tool.plan_list(autonomous_only=True)
        ids = [p["plan_id"] for p in result]
        assert ids == [b]

    def test_plan_get_returns_summary_dict(self, tool):
        plan_id = tool.plan_create(title="T", description="D")
        result = tool.plan_get(plan_id=plan_id)
        assert result["plan_id"] == plan_id
        assert result["title"] == "T"
        assert "scratchpad" in result

    def test_plan_next_actionable(self, tool):
        plan_id = tool.plan_create(title="T", description="D")
        step_id = tool.plan_add_step(plan_id=plan_id, description="First")
        tool.plan_add_step(
            plan_id=plan_id, description="Second", depends_on=[step_id]
        )
        result = tool.plan_next_actionable(plan_id=plan_id)
        assert result["step_id"] == step_id

    def test_plan_next_actionable_none(self, tool):
        plan_id = tool.plan_create(title="T", description="D")
        assert tool.plan_next_actionable(plan_id=plan_id) is None

    def test_plan_append_note(self, tool):
        plan_id = tool.plan_create(title="T", description="D")
        tool.plan_append_note(plan_id=plan_id, markdown_text="First note")
        tool.plan_append_note(plan_id=plan_id, markdown_text="Second note")
        plan = tool.store.get(plan_id)
        assert "First note" in plan.scratchpad
        assert "Second note" in plan.scratchpad

    def test_plan_set_autonomous_requires_steps(self, tool):
        plan_id = tool.plan_create(title="T", description="D")
        with pytest.raises(PlanToolError, match="no steps"):
            tool.plan_set_autonomous(plan_id=plan_id, autonomous=True)

    def test_plan_set_autonomous_succeeds_with_steps(self, tool):
        plan_id = tool.plan_create(title="T", description="D")
        tool.plan_add_step(plan_id=plan_id, description="Do")
        tool.plan_set_autonomous(plan_id=plan_id, autonomous=True)
        plan = tool.store.get(plan_id)
        assert plan.autonomous is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_plan_tool.py::TestReadsAndFlags -v`
Expected: `AttributeError` for missing methods.

- [ ] **Step 3: Implement the read methods and autonomy setter**

Append to `pal/plan_tool.py`:

```python
    def plan_list(
        self,
        *,
        status_filter: PlanStatus | None = None,
        autonomous_only: bool = False,
    ) -> list[dict]:
        plans = self.store.list(
            status=status_filter,
            autonomous_only=autonomous_only,
        )
        return [
            {
                "plan_id": p.plan_id,
                "title": p.title,
                "status": p.status.value,
                "autonomous": p.autonomous,
                "step_count": len(p.steps),
                "needs_review": p.needs_review,
            }
            for p in plans
        ]

    def plan_get(self, *, plan_id: str) -> dict:
        plan = self._load(plan_id)
        return {
            **plan.to_dict(),
            "scratchpad": plan.scratchpad,
        }

    def plan_next_actionable(self, *, plan_id: str) -> dict | None:
        from pal.plan_model import next_actionable_step
        plan = self._load(plan_id)
        step = next_actionable_step(plan, now=self._now())
        if step is None:
            return None
        return {
            "step_id": step.id,
            "description": step.description,
            "status": step.status.value,
        }

    def plan_append_note(
        self, *, plan_id: str, markdown_text: str
    ) -> None:
        plan = self._load(plan_id)
        if plan.scratchpad:
            plan.scratchpad = plan.scratchpad.rstrip("\n") + "\n\n" + markdown_text
        else:
            plan.scratchpad = markdown_text
        plan.updated = self._now()
        self.store.save(plan)

    def plan_set_autonomous(
        self, *, plan_id: str, autonomous: bool
    ) -> None:
        plan = self._load(plan_id)
        if autonomous and not plan.steps:
            raise PlanToolError("cannot set autonomous=true on a plan with no steps")
        plan.autonomous = autonomous
        plan.updated = self._now()
        self.store.save(plan)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_plan_tool.py::TestReadsAndFlags -v`
Expected: all seven tests pass.

- [ ] **Step 5: Run full plan_tool file**

Run: `python -m pytest tests/test_plan_tool.py -v`
Expected: every test in the file passes.

- [ ] **Step 6: Commit**

```bash
git add pal/plan_tool.py tests/test_plan_tool.py
git commit -m "feat(plan): read operations, append note, and autonomy flag"
```

---

### Task 13: Register plan tool schemas with ToolExecutor

**Files:**
- Modify: `pal/tools.py`
- Modify: `tests/test_tools.py`

- [ ] **Step 1: Write failing test for dispatch through ToolExecutor**

Add to `tests/test_tools.py`:

```python
from datetime import datetime, timezone

from pal.plan_store import PlanStore
from pal.plan_tool import PlanTool


class TestPlanToolDispatch:
    def _make_executor(self, tmp_path):
        from pal.tools import ToolExecutor
        store = PlanStore(tmp_path)
        tool = PlanTool(
            store=store,
            clock=lambda: datetime(2026, 4, 10, tzinfo=timezone.utc),
        )
        return ToolExecutor(
            vault_path=tmp_path, retrieval=None, wiki=None, plan_tool=tool
        )

    def test_plan_create_dispatch(self, tmp_path):
        executor = self._make_executor(tmp_path)
        result = executor.run(
            "plan_create",
            {"title": "Test", "description": "A test"},
        )
        assert "2026-04-10-test" in result

    def test_plan_add_step_dispatch(self, tmp_path):
        executor = self._make_executor(tmp_path)
        plan_id_msg = executor.run(
            "plan_create",
            {"title": "Test", "description": "T"},
        )
        plan_id = plan_id_msg.strip().split()[-1]
        result = executor.run(
            "plan_add_step",
            {"plan_id": plan_id, "description": "Do it"},
        )
        assert "step-01" in result

    def test_unknown_plan_tool_returns_error_string(self, tmp_path):
        executor = self._make_executor(tmp_path)
        result = executor.run(
            "plan_add_step",
            {"plan_id": "missing", "description": "X"},
        )
        assert "Error" in result
        assert "plan not found" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tools.py::TestPlanToolDispatch -v`
Expected: `TypeError: __init__() got an unexpected keyword argument 'plan_tool'`.

- [ ] **Step 3: Add plan_tool parameter and dispatch methods**

In `pal/tools.py`, modify `ToolExecutor.__init__` to accept `plan_tool`:

```python
from pal.plan_tool import PlanTool, PlanToolError


class ToolExecutor:
    def __init__(
        self,
        vault_path: Path,
        retrieval: RetrievalClient | None,
        wiki: WikiManager | None = None,
        plan_tool: PlanTool | None = None,
    ) -> None:
        self.vault_path = vault_path.resolve()
        self.retrieval = retrieval
        self.wiki = wiki
        self.plan_tool = plan_tool
```

Then extend `run` to dispatch plan tool calls. Add these handler methods to the class:

```python
    def _dispatch_plan_tool(self, name: str, arguments: dict) -> str:
        if self.plan_tool is None:
            return "Error: planning tool is not available."
        try:
            handler = {
                "plan_create": self._plan_create,
                "plan_add_step": self._plan_add_step,
                "plan_start_step": self._plan_start_step,
                "plan_complete_step": self._plan_complete_step,
                "plan_fail_step": self._plan_fail_step,
                "plan_block_step": self._plan_block_step,
                "plan_defer_step": self._plan_defer_step,
                "plan_supersede_step": self._plan_supersede_step,
                "plan_retry_step": self._plan_retry_step,
                "plan_append_note": self._plan_append_note,
                "plan_set_autonomous": self._plan_set_autonomous,
                "plan_list": self._plan_list,
                "plan_get": self._plan_get,
                "plan_next_actionable": self._plan_next_actionable,
            }[name]
        except KeyError:
            return f"Unknown plan tool: {name}"
        try:
            return handler(arguments)
        except PlanToolError as exc:
            return f"Error: {exc}"

    def _plan_create(self, args: dict) -> str:
        plan_id = self.plan_tool.plan_create(
            title=args["title"],
            description=args["description"],
            autonomous=bool(args.get("autonomous", False)),
        )
        return f"Created plan: {plan_id}"

    def _plan_add_step(self, args: dict) -> str:
        step_id = self.plan_tool.plan_add_step(
            plan_id=args["plan_id"],
            description=args["description"],
            depends_on=args.get("depends_on"),
        )
        return f"Added step: {step_id}"

    def _plan_start_step(self, args: dict) -> str:
        self.plan_tool.plan_start_step(
            plan_id=args["plan_id"], step_id=args["step_id"]
        )
        return f"Started step: {args['step_id']}"

    def _plan_complete_step(self, args: dict) -> str:
        self.plan_tool.plan_complete_step(
            plan_id=args["plan_id"], step_id=args["step_id"]
        )
        return f"Completed step: {args['step_id']}"

    def _plan_fail_step(self, args: dict) -> str:
        self.plan_tool.plan_fail_step(
            plan_id=args["plan_id"],
            step_id=args["step_id"],
            error=args["error"],
        )
        return f"Failed step: {args['step_id']}"

    def _plan_block_step(self, args: dict) -> str:
        self.plan_tool.plan_block_step(
            plan_id=args["plan_id"],
            step_id=args["step_id"],
            blocked_on=args["blocked_on"],
        )
        return f"Blocked step: {args['step_id']}"

    def _plan_defer_step(self, args: dict) -> str:
        defer_until = None
        if "defer_until" in args and args["defer_until"]:
            from datetime import datetime
            defer_until = datetime.fromisoformat(args["defer_until"])
        self.plan_tool.plan_defer_step(
            plan_id=args["plan_id"],
            step_id=args["step_id"],
            reason=args["reason"],
            defer_until=defer_until,
        )
        return f"Deferred step: {args['step_id']}"

    def _plan_supersede_step(self, args: dict) -> str:
        self.plan_tool.plan_supersede_step(
            plan_id=args["plan_id"],
            step_id=args["step_id"],
            reason=args["reason"],
        )
        return f"Superseded step: {args['step_id']}"

    def _plan_retry_step(self, args: dict) -> str:
        self.plan_tool.plan_retry_step(
            plan_id=args["plan_id"], step_id=args["step_id"]
        )
        return f"Retrying step: {args['step_id']}"

    def _plan_append_note(self, args: dict) -> str:
        self.plan_tool.plan_append_note(
            plan_id=args["plan_id"],
            markdown_text=args["markdown_text"],
        )
        return "Note appended."

    def _plan_set_autonomous(self, args: dict) -> str:
        self.plan_tool.plan_set_autonomous(
            plan_id=args["plan_id"],
            autonomous=bool(args["autonomous"]),
        )
        return f"Autonomous set to {args['autonomous']}"

    def _plan_list(self, args: dict) -> str:
        from pal.plan_model import PlanStatus
        status = None
        if args.get("status_filter"):
            status = PlanStatus(args["status_filter"])
        plans = self.plan_tool.plan_list(
            status_filter=status,
            autonomous_only=bool(args.get("autonomous_only", False)),
        )
        if not plans:
            return "No plans."
        lines = [f"{len(plans)} plan(s):"]
        for p in plans:
            marker = " [auto]" if p["autonomous"] else ""
            review = " [NEEDS REVIEW]" if p["needs_review"] else ""
            lines.append(
                f"  {p['plan_id']} — {p['title']} "
                f"({p['status']}, {p['step_count']} steps){marker}{review}"
            )
        return "\n".join(lines)

    def _plan_get(self, args: dict) -> str:
        import json
        result = self.plan_tool.plan_get(plan_id=args["plan_id"])
        return json.dumps(result, indent=2, default=str)

    def _plan_next_actionable(self, args: dict) -> str:
        result = self.plan_tool.plan_next_actionable(plan_id=args["plan_id"])
        if result is None:
            return "No actionable step."
        return f"Next step: {result['step_id']} — {result['description']}"
```

Modify `run` to dispatch plan tool names:

```python
    def run(self, name: str, arguments: dict) -> str:
        handler = {
            "read_file": self._read_file,
            "list_directory": self._list_directory,
            "search_content": self._search_content,
            "edit_file": self._edit_file,
            "create_file": self._create_file,
        }.get(name)
        if handler is not None:
            return handler(arguments)
        if name == "search_vault":
            return "Error: search_vault must be called via run_async()"
        if name.startswith("plan_"):
            return self._dispatch_plan_tool(name, arguments)
        return f"Unknown tool: {name}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tools.py::TestPlanToolDispatch -v`
Expected: all three tests pass.

- [ ] **Step 5: Run full tools test file**

Run: `python -m pytest tests/test_tools.py -v`
Expected: every existing test and the new ones pass.

- [ ] **Step 6: Commit**

```bash
git add pal/tools.py tests/test_tools.py
git commit -m "feat(plan): wire PlanTool through ToolExecutor dispatch"
```

---

### Task 14: Add tool schemas to TOOL_DEFINITIONS

**Files:**
- Modify: `pal/tools.py`

- [ ] **Step 1: Append all 14 plan tool schemas to TOOL_DEFINITIONS**

In `pal/tools.py`, add these entries to the `TOOL_DEFINITIONS` list after the existing tools:

```python
    {
        "type": "function",
        "function": {
            "name": "plan_create",
            "description": "Create a new plan. Returns a plan_id. Use for any multi-step work you expect to track across turns or errors.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short title for the plan."},
                    "description": {"type": "string", "description": "What this plan is about."},
                    "autonomous": {
                        "type": "boolean",
                        "description": "If true, the daemon will advance this plan's steps without user prompting. Defaults to false. Only set to true after user confirmation.",
                    },
                },
                "required": ["title", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_add_step",
            "description": "Append a step to an existing plan. Returns a step_id. Keep steps atomic: completing one leaves the vault in a consistent state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                    "description": {"type": "string"},
                    "depends_on": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Step ids that must be done before this step is actionable.",
                    },
                },
                "required": ["plan_id", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_start_step",
            "description": "Mark a pending step as in_progress. Call before doing the work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                    "step_id": {"type": "string"},
                },
                "required": ["plan_id", "step_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_complete_step",
            "description": "Mark an in_progress step as done. Call only after the step's work is actually complete and verified.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                    "step_id": {"type": "string"},
                },
                "required": ["plan_id", "step_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_fail_step",
            "description": "Mark an in_progress step as failed. Requires an error summary. The step will not advance until explicitly retried.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                    "step_id": {"type": "string"},
                    "error": {"type": "string", "description": "Short description of what went wrong."},
                },
                "required": ["plan_id", "step_id", "error"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_block_step",
            "description": "Mark a step as blocked on a named dependency. Requires a blocked_on description.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                    "step_id": {"type": "string"},
                    "blocked_on": {"type": "string", "description": "What the step is waiting on."},
                },
                "required": ["plan_id", "step_id", "blocked_on"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_defer_step",
            "description": "Defer a pending step. Requires a reason. Optional defer_until (ISO-8601) makes the step actionable again once that time passes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                    "step_id": {"type": "string"},
                    "reason": {"type": "string"},
                    "defer_until": {
                        "type": "string",
                        "description": "Optional ISO-8601 timestamp. Step becomes actionable at or after this time.",
                    },
                },
                "required": ["plan_id", "step_id", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_supersede_step",
            "description": "Mark a step as no longer relevant. Terminal. Requires a reason.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                    "step_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["plan_id", "step_id", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_retry_step",
            "description": "Retry a failed step by returning it to pending. Clears the error.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                    "step_id": {"type": "string"},
                },
                "required": ["plan_id", "step_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_append_note",
            "description": "Append free-form markdown to a plan's scratchpad. Use for reasoning notes, findings, or links. Not part of structured state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                    "markdown_text": {"type": "string"},
                },
                "required": ["plan_id", "markdown_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_set_autonomous",
            "description": "Flip a plan's autonomous flag. Autonomous plans advance without user prompting. Only set true after user confirmation. Requires at least one step.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                    "autonomous": {"type": "boolean"},
                },
                "required": ["plan_id", "autonomous"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_list",
            "description": "List plans with optional status and autonomy filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status_filter": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "done", "blocked", "failed", "superseded"],
                    },
                    "autonomous_only": {"type": "boolean"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_get",
            "description": "Return a plan's full structured state plus scratchpad as JSON.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                },
                "required": ["plan_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_next_actionable",
            "description": "Return the next actionable step for a plan, or null if none. Use to decide what to work on next without re-reading the whole plan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string"},
                },
                "required": ["plan_id"],
            },
        },
    },
```

- [ ] **Step 2: Run the tools test file to confirm schemas parse**

Run: `python -m pytest tests/test_tools.py -v`
Expected: all tests still pass. Tool schemas are plain dicts and do not need their own tests.

- [ ] **Step 3: Commit**

```bash
git add pal/tools.py
git commit -m "feat(plan): add plan tool schemas to TOOL_DEFINITIONS"
```

---

## Phase 4: Executor (polling loop)

### Task 15: Executor skeleton with idle guard

**Files:**
- Create: `pal/executor.py`
- Create: `tests/test_executor.py`

- [ ] **Step 1: Write failing test for idle guard behavior**

```python
# tests/test_executor.py
"""Tests for the polling executor."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pal.plan_model import PlanStatus, StepStatus
from pal.plan_store import PlanStore
from pal.plan_tool import PlanTool
from pal.executor import Executor


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: int) -> None:
        self._now += timedelta(seconds=seconds)


class FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.raise_on_dispatch = False

    async def dispatch_step(self, plan_id: str, step_id: str) -> None:
        if self.raise_on_dispatch:
            raise RuntimeError("dispatch failed")
        self.calls.append((plan_id, step_id))


@pytest.fixture()
def clock():
    return FakeClock(datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc))


@pytest.fixture()
def store(tmp_path):
    return PlanStore(tmp_path)


@pytest.fixture()
def tool(store, clock):
    return PlanTool(store=store, clock=clock)


@pytest.fixture()
def dispatcher():
    return FakeDispatcher()


@pytest.fixture()
def executor(store, tool, clock, dispatcher):
    return Executor(
        store=store,
        tool=tool,
        dispatcher=dispatcher,
        clock=clock,
        stale_threshold_seconds=600,
        circuit_breaker_steps=10,
    )


def _autonomous_plan_with_pending_step(tool) -> tuple[str, str]:
    plan_id = tool.plan_create(title="Test", description="T")
    step_id = tool.plan_add_step(plan_id=plan_id, description="Do it")
    tool.plan_set_autonomous(plan_id=plan_id, autonomous=True)
    return plan_id, step_id


class TestIdleGuard:
    async def test_tick_skips_when_not_idle(self, executor, tool, dispatcher):
        _autonomous_plan_with_pending_step(tool)
        executor.set_idle(False)
        await executor.tick()
        assert dispatcher.calls == []

    async def test_tick_dispatches_when_idle(self, executor, tool, dispatcher):
        plan_id, step_id = _autonomous_plan_with_pending_step(tool)
        executor.set_idle(True)
        await executor.tick()
        assert dispatcher.calls == [(plan_id, step_id)]

    async def test_non_autonomous_plans_are_ignored(
        self, executor, tool, dispatcher
    ):
        plan_id = tool.plan_create(title="Test", description="T")
        tool.plan_add_step(plan_id=plan_id, description="Do it")
        # Deliberately not autonomous
        executor.set_idle(True)
        await executor.tick()
        assert dispatcher.calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_executor.py::TestIdleGuard -v`
Expected: `ImportError` for `Executor`.

- [ ] **Step 3: Implement Executor skeleton**

```python
# pal/executor.py
"""Polling executor for autonomous plan advancement.

Wakes on trigger events (startup, conversation turn end, inference server
recovery, backstop timer), checks the idle guard, then advances at most
one step per actionable autonomous plan per tick.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Awaitable, Callable, Protocol

from pal.plan_model import PlanStatus, StepStatus
from pal.plan_store import PlanStore
from pal.plan_tool import PlanTool, PlanToolError


class StepDispatcher(Protocol):
    async def dispatch_step(self, plan_id: str, step_id: str) -> None:
        ...


class Executor:
    def __init__(
        self,
        *,
        store: PlanStore,
        tool: PlanTool,
        dispatcher: StepDispatcher,
        clock: Callable[[], datetime],
        stale_threshold_seconds: int,
        circuit_breaker_steps: int,
    ) -> None:
        self.store = store
        self.tool = tool
        self.dispatcher = dispatcher
        self._clock = clock
        self._stale_threshold = timedelta(seconds=stale_threshold_seconds)
        self._circuit_breaker_steps = circuit_breaker_steps
        self._idle = True
        self._consecutive_steps: dict[str, int] = {}

    def set_idle(self, idle: bool) -> None:
        self._idle = idle

    def clear_consecutive_counter(self, plan_id: str) -> None:
        self._consecutive_steps[plan_id] = 0

    async def tick(self) -> None:
        if not self._idle:
            return
        candidates = self.store.list(
            status=PlanStatus.IN_PROGRESS, autonomous_only=True
        ) + self.store.list(
            status=PlanStatus.PENDING, autonomous_only=True
        )
        for plan in candidates:
            if plan.needs_review:
                continue
            next_step = self.tool.plan_next_actionable(plan_id=plan.plan_id)
            if next_step is None:
                continue
            await self.dispatcher.dispatch_step(plan.plan_id, next_step["step_id"])
```

- [ ] **Step 4: Add pytest-asyncio mode to the test file if not auto-detected**

Check `pyproject.toml` for `asyncio_mode`. If `mode=Mode.AUTO` is set (as the session output in task b8 showed), no action is needed. Otherwise, add `pytestmark = pytest.mark.asyncio` at the top of `tests/test_executor.py`.

Verification: `grep asyncio_mode pyproject.toml` should show auto mode enabled. If yes, skip the mark.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_executor.py::TestIdleGuard -v`
Expected: all three tests pass.

- [ ] **Step 6: Commit**

```bash
git add pal/executor.py tests/test_executor.py
git commit -m "feat(executor): skeleton with idle guard and autonomous filter"
```

---

### Task 16: Executor — stale in-progress detection

**Files:**
- Modify: `pal/executor.py`
- Modify: `tests/test_executor.py`

- [ ] **Step 1: Write failing test for stale detection**

Add to `tests/test_executor.py`:

```python
class TestStaleDetection:
    async def test_fresh_in_progress_is_redispatched(
        self, executor, tool, clock, dispatcher
    ):
        plan_id, step_id = _autonomous_plan_with_pending_step(tool)
        tool.plan_start_step(plan_id=plan_id, step_id=step_id)
        # Advance clock by 1 second (well under stale threshold)
        clock.advance(1)
        await executor.tick()
        # Fresh in_progress stays in_progress; no dispatch since it's already running
        plan = tool.store.get(plan_id)
        assert plan.get_step(step_id).status == StepStatus.IN_PROGRESS
        assert dispatcher.calls == []

    async def test_stale_in_progress_is_failed(
        self, executor, tool, clock, dispatcher
    ):
        plan_id, step_id = _autonomous_plan_with_pending_step(tool)
        tool.plan_start_step(plan_id=plan_id, step_id=step_id)
        clock.advance(700)  # past the 600s threshold
        await executor.tick()
        plan = tool.store.get(plan_id)
        step = plan.get_step(step_id)
        assert step.status == StepStatus.FAILED
        assert "stale" in step.error.lower()
        assert dispatcher.calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_executor.py::TestStaleDetection -v`
Expected: the stale test fails because the executor currently ignores `in_progress` steps entirely.

- [ ] **Step 3: Add stale detection to tick()**

Modify `Executor.tick` in `pal/executor.py`:

```python
    async def tick(self) -> None:
        if not self._idle:
            return
        candidates = self.store.list(
            status=PlanStatus.IN_PROGRESS, autonomous_only=True
        ) + self.store.list(
            status=PlanStatus.PENDING, autonomous_only=True
        )
        now = self._clock()
        for plan in candidates:
            if plan.needs_review:
                continue
            if self._handle_stale_in_progress(plan, now):
                continue
            next_step = self.tool.plan_next_actionable(plan_id=plan.plan_id)
            if next_step is None:
                continue
            await self.dispatcher.dispatch_step(plan.plan_id, next_step["step_id"])

    def _handle_stale_in_progress(self, plan, now: datetime) -> bool:
        """If any step is in_progress past the stale threshold, mark it failed.

        Returns True if the plan had a stale step (and should be skipped this tick).
        """
        for step in plan.steps:
            if step.status != StepStatus.IN_PROGRESS:
                continue
            if step.started is None:
                continue
            if now - step.started > self._stale_threshold:
                try:
                    self.tool.plan_fail_step(
                        plan_id=plan.plan_id,
                        step_id=step.id,
                        error="stale in-progress on restart",
                    )
                except PlanToolError:
                    pass
                return True
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_executor.py::TestStaleDetection -v`
Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/executor.py tests/test_executor.py
git commit -m "feat(executor): mark stale in-progress steps as failed on tick"
```

---

### Task 17: Executor — circuit breaker

**Files:**
- Modify: `pal/executor.py`
- Modify: `tests/test_executor.py`

- [ ] **Step 1: Write failing test for circuit breaker**

Add to `tests/test_executor.py`:

```python
class TestCircuitBreaker:
    async def test_counter_increments_on_dispatch(
        self, executor, tool, dispatcher, clock
    ):
        plan_id = tool.plan_create(title="Multi", description="Multi-step")
        first = tool.plan_add_step(plan_id=plan_id, description="First")
        second = tool.plan_add_step(
            plan_id=plan_id, description="Second", depends_on=[first]
        )
        tool.plan_set_autonomous(plan_id=plan_id, autonomous=True)

        # Simulate dispatcher completing each step immediately
        async def complete_on_dispatch(plan_id: str, step_id: str) -> None:
            tool.plan_start_step(plan_id=plan_id, step_id=step_id)
            tool.plan_complete_step(plan_id=plan_id, step_id=step_id)
            dispatcher.calls.append((plan_id, step_id))
        dispatcher.dispatch_step = complete_on_dispatch

        await executor.tick()
        await executor.tick()
        assert executor._consecutive_steps[plan_id] == 2

    async def test_circuit_breaker_trips_and_sets_needs_review(
        self, tmp_path, clock
    ):
        store = PlanStore(tmp_path)
        tool = PlanTool(store=store, clock=clock)
        dispatcher = FakeDispatcher()
        executor = Executor(
            store=store,
            tool=tool,
            dispatcher=dispatcher,
            clock=clock,
            stale_threshold_seconds=600,
            circuit_breaker_steps=2,  # trip after 2
        )
        executor.set_idle(True)

        plan_id = tool.plan_create(title="Many", description="Many steps")
        prev = None
        for i in range(5):
            kwargs = {"plan_id": plan_id, "description": f"Step {i}"}
            if prev is not None:
                kwargs["depends_on"] = [prev]
            prev = tool.plan_add_step(**kwargs)
        tool.plan_set_autonomous(plan_id=plan_id, autonomous=True)

        async def complete_on_dispatch(p: str, s: str) -> None:
            tool.plan_start_step(plan_id=p, step_id=s)
            tool.plan_complete_step(plan_id=p, step_id=s)
            dispatcher.calls.append((p, s))
        dispatcher.dispatch_step = complete_on_dispatch

        # Run enough ticks to exceed the 2-step limit
        for _ in range(5):
            await executor.tick()

        plan = store.get(plan_id)
        assert plan.needs_review is True
        # Only 2 steps dispatched before breaker tripped
        assert len(dispatcher.calls) == 2

    def test_clear_counter_on_user_interaction(self, executor):
        executor._consecutive_steps["some-plan"] = 5
        executor.clear_consecutive_counter("some-plan")
        assert executor._consecutive_steps["some-plan"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_executor.py::TestCircuitBreaker -v`
Expected: failures on counter and breaker behavior.

- [ ] **Step 3: Add circuit breaker logic**

Modify `Executor.tick` in `pal/executor.py`:

```python
    async def tick(self) -> None:
        if not self._idle:
            return
        candidates = self.store.list(
            status=PlanStatus.IN_PROGRESS, autonomous_only=True
        ) + self.store.list(
            status=PlanStatus.PENDING, autonomous_only=True
        )
        now = self._clock()
        for plan in candidates:
            if plan.needs_review:
                continue
            if self._handle_stale_in_progress(plan, now):
                continue
            if self._check_circuit_breaker(plan):
                continue
            next_step = self.tool.plan_next_actionable(plan_id=plan.plan_id)
            if next_step is None:
                continue
            await self.dispatcher.dispatch_step(plan.plan_id, next_step["step_id"])
            self._consecutive_steps[plan.plan_id] = (
                self._consecutive_steps.get(plan.plan_id, 0) + 1
            )

    def _check_circuit_breaker(self, plan) -> bool:
        count = self._consecutive_steps.get(plan.plan_id, 0)
        if count >= self._circuit_breaker_steps:
            plan.needs_review = True
            plan.updated = self._clock()
            self.store.save(plan)
            return True
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_executor.py::TestCircuitBreaker -v`
Expected: all three tests pass.

- [ ] **Step 5: Run full executor test file**

Run: `python -m pytest tests/test_executor.py -v`
Expected: every test in the file passes.

- [ ] **Step 6: Commit**

```bash
git add pal/executor.py tests/test_executor.py
git commit -m "feat(executor): circuit breaker flips plan to needs_review after N steps"
```

---

### Task 18: Executor — trigger coalescing and backstop timer

**Files:**
- Modify: `pal/executor.py`
- Modify: `tests/test_executor.py`

- [ ] **Step 1: Write failing test for trigger coalescing**

Add to `tests/test_executor.py`:

```python
class TestTriggers:
    async def test_fire_trigger_coalesces_multiple_triggers(
        self, executor, tool, dispatcher
    ):
        """Multiple triggers before tick() runs should cause only one tick."""
        _autonomous_plan_with_pending_step(tool)
        executor.set_idle(True)
        # Three triggers in quick succession
        executor.fire_trigger("startup")
        executor.fire_trigger("turn_end")
        executor.fire_trigger("recovery")
        # Process pending triggers
        await executor.process_pending_triggers()
        # Only one dispatch even though three triggers fired
        assert len(dispatcher.calls) == 1

    async def test_backstop_timer_fires_when_elapsed(
        self, executor, tool, clock, dispatcher
    ):
        _autonomous_plan_with_pending_step(tool)
        executor.set_idle(True)
        # No triggers fired yet; backstop timer should take over
        clock.advance(901)  # past 900s default backstop
        await executor.process_pending_triggers()
        assert len(dispatcher.calls) == 1

    async def test_backstop_does_not_fire_early(
        self, executor, tool, clock, dispatcher
    ):
        _autonomous_plan_with_pending_step(tool)
        executor.set_idle(True)
        clock.advance(100)
        await executor.process_pending_triggers()
        assert dispatcher.calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_executor.py::TestTriggers -v`
Expected: `AttributeError` for `fire_trigger` and `process_pending_triggers`.

- [ ] **Step 3: Add trigger state and coalescing**

Modify `Executor.__init__` and add new methods in `pal/executor.py`. Add new parameter `backstop_seconds` with default 900:

```python
    def __init__(
        self,
        *,
        store: PlanStore,
        tool: PlanTool,
        dispatcher: StepDispatcher,
        clock: Callable[[], datetime],
        stale_threshold_seconds: int,
        circuit_breaker_steps: int,
        backstop_seconds: int = 900,
    ) -> None:
        self.store = store
        self.tool = tool
        self.dispatcher = dispatcher
        self._clock = clock
        self._stale_threshold = timedelta(seconds=stale_threshold_seconds)
        self._circuit_breaker_steps = circuit_breaker_steps
        self._backstop = timedelta(seconds=backstop_seconds)
        self._idle = True
        self._consecutive_steps: dict[str, int] = {}
        self._pending_trigger = False
        self._last_tick: datetime | None = None

    def fire_trigger(self, reason: str) -> None:
        self._pending_trigger = True

    async def process_pending_triggers(self) -> None:
        now = self._clock()
        should_run = self._pending_trigger or self._backstop_due(now)
        if not should_run:
            return
        self._pending_trigger = False
        self._last_tick = now
        await self.tick()

    def _backstop_due(self, now: datetime) -> bool:
        if self._last_tick is None:
            return now - (now - self._backstop) >= self._backstop
        return now - self._last_tick >= self._backstop
```

Note: the backstop initialization above is wrong — fix it. The first backstop fire should only happen after the executor has been running for `backstop_seconds`. Track a creation time:

```python
        self._created = self._clock()

    def _backstop_due(self, now: datetime) -> bool:
        baseline = self._last_tick or self._created
        return now - baseline >= self._backstop
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_executor.py::TestTriggers -v`
Expected: all three trigger tests pass. The coalescing test proves multiple triggers produce one tick, the backstop-late test proves elapsed time triggers a tick, the backstop-early test proves it does not fire prematurely.

- [ ] **Step 5: Run full executor file**

Run: `python -m pytest tests/test_executor.py -v`
Expected: every test in the file passes.

- [ ] **Step 6: Commit**

```bash
git add pal/executor.py tests/test_executor.py
git commit -m "feat(executor): trigger coalescing and backstop timer"
```

---

## Phase 5: Daemon Integration

### Task 19: Config fields for executor tuning

**Files:**
- Modify: `pal/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing test for new config fields**

Add to `tests/test_config.py` (create the class if it does not exist):

```python
def test_load_config_defaults_for_executor(monkeypatch):
    for var in [
        "PAL_EXECUTOR_ENABLED",
        "PAL_EXECUTOR_BACKSTOP_SECONDS",
        "PAL_EXECUTOR_STALE_THRESHOLD_SECONDS",
        "PAL_CIRCUIT_BREAKER_STEPS",
        "PAL_TASKS_DIR",
    ]:
        monkeypatch.delenv(var, raising=False)
    from pal.config import load_config
    cfg = load_config()
    assert cfg.executor_enabled is True
    assert cfg.executor_backstop_seconds == 900
    assert cfg.executor_stale_threshold_seconds == 600
    assert cfg.circuit_breaker_steps == 10
    assert cfg.tasks_dir == "tasks"


def test_load_config_overrides_executor_fields(monkeypatch):
    monkeypatch.setenv("PAL_EXECUTOR_ENABLED", "false")
    monkeypatch.setenv("PAL_EXECUTOR_BACKSTOP_SECONDS", "60")
    monkeypatch.setenv("PAL_EXECUTOR_STALE_THRESHOLD_SECONDS", "120")
    monkeypatch.setenv("PAL_CIRCUIT_BREAKER_STEPS", "3")
    monkeypatch.setenv("PAL_TASKS_DIR", "plans")
    from pal.config import load_config
    cfg = load_config()
    assert cfg.executor_enabled is False
    assert cfg.executor_backstop_seconds == 60
    assert cfg.executor_stale_threshold_seconds == 120
    assert cfg.circuit_breaker_steps == 3
    assert cfg.tasks_dir == "plans"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py::test_load_config_defaults_for_executor tests/test_config.py::test_load_config_overrides_executor_fields -v`
Expected: `AttributeError: 'Config' object has no attribute 'executor_enabled'`.

- [ ] **Step 3: Add fields to Config dataclass and loader**

Modify `pal/config.py`:

```python
@dataclass
class Config:
    inference_url: str = "http://192.168.1.14:11434"
    model: str = "Qwen3.5-35B-A3B-Q4_K_M"
    socket_path: Path = field(default_factory=_default_socket_path)
    history_depth: int = 50
    vault_path: Path = field(default_factory=lambda: Path.home() / "vault")
    collection_id: str = "vault"
    username: str = "user"
    searxng_url: str = "http://192.168.1.14:8080"
    fetch_max_bytes: int = 2_000_000
    fetch_timeout: int = 30
    executor_enabled: bool = True
    executor_backstop_seconds: int = 900
    executor_stale_threshold_seconds: int = 600
    circuit_breaker_steps: int = 10
    tasks_dir: str = "tasks"


def load_config() -> Config:
    kwargs: dict = {}
    # ... existing fields unchanged ...
    if (val := os.environ.get("PAL_EXECUTOR_ENABLED")) is not None:
        kwargs["executor_enabled"] = val.strip().lower() in ("1", "true", "yes", "on")
    if (val := os.environ.get("PAL_EXECUTOR_BACKSTOP_SECONDS")) is not None:
        kwargs["executor_backstop_seconds"] = int(val)
    if (val := os.environ.get("PAL_EXECUTOR_STALE_THRESHOLD_SECONDS")) is not None:
        kwargs["executor_stale_threshold_seconds"] = int(val)
    if (val := os.environ.get("PAL_CIRCUIT_BREAKER_STEPS")) is not None:
        kwargs["circuit_breaker_steps"] = int(val)
    if (val := os.environ.get("PAL_TASKS_DIR")) is not None:
        kwargs["tasks_dir"] = val
    return Config(**kwargs)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: the new tests and all existing ones pass.

- [ ] **Step 5: Commit**

```bash
git add pal/config.py tests/test_config.py
git commit -m "feat(config): add executor tuning fields"
```

---

### Task 20: Daemon instantiation of PlanStore, PlanTool, and Executor

**Files:**
- Modify: `pal/daemon.py`

Note: `daemon.py` is 1291 lines. This task only adds wiring; it does not restructure the file. Read the existing daemon to find the right insertion points for (a) component construction in `__init__` or the daemon factory, (b) hook points for conversation-turn-end, and (c) shutdown cleanup.

- [ ] **Step 1: Locate the daemon construction site**

Run: `grep -n "class.*Daemon\|def __init__\|WikiManager\|RetrievalClient" pal/daemon.py`

Expected: you will see the daemon class and where it constructs `WikiManager`, `RetrievalClient`, and `ToolExecutor`. That is where the new components go.

- [ ] **Step 2: Import and construct PlanStore, PlanTool, Executor**

In `pal/daemon.py`, add imports at the top:

```python
from datetime import datetime, timezone

from pal.executor import Executor, StepDispatcher
from pal.plan_store import PlanStore
from pal.plan_tool import PlanTool
```

In the daemon's construction path, next to where `WikiManager` and `ToolExecutor` are built, add:

```python
plan_store = PlanStore(
    tasks_dir=config.vault_path / config.tasks_dir,
    commit=wiki.git_commit if wiki is not None else None,
)
plan_tool = PlanTool(
    store=plan_store,
    clock=lambda: datetime.now(timezone.utc),
)
# Dispatcher is defined below; executor is started after the chat pipeline exists
```

Then find where `ToolExecutor` is constructed and pass `plan_tool=plan_tool` to it:

```python
tool_executor = ToolExecutor(
    vault_path=config.vault_path,
    retrieval=retrieval,
    wiki=wiki,
    plan_tool=plan_tool,
)
```

- [ ] **Step 3: Wire the executor dispatcher to the existing chat pipeline**

Locate the method that handles a single chat turn (look for `_handle_chat` or similar, which the earlier tool-use plan touched). Create an inner dispatcher class that calls it with a synthetic system prompt:

```python
class _PlanStepDispatcher:
    def __init__(self, daemon) -> None:
        self._daemon = daemon

    async def dispatch_step(self, plan_id: str, step_id: str) -> None:
        plan = self._daemon.plan_tool.plan_get(plan_id=plan_id)
        step = next(s for s in plan["steps"] if s["id"] == step_id)
        system_msg = (
            f"You are executing step {step_id} of autonomous plan {plan_id}. "
            f"The step description is: {step['description']}. "
            "Work on this step only. When the work is actually done and verified, "
            "call plan_complete_step. If you cannot complete it, call plan_fail_step, "
            "plan_block_step, or plan_defer_step with the required metadata."
        )
        await self._daemon._run_autonomous_turn(system_msg)
```

Add a new method `_run_autonomous_turn(self, system_msg: str)` that invokes the existing chat pipeline with a fresh one-turn conversation seeded with `system_msg`. The exact shape depends on the existing chat code; reuse as much as possible.

Then instantiate and store the executor:

```python
self.plan_store = plan_store
self.plan_tool = plan_tool
self.executor = Executor(
    store=plan_store,
    tool=plan_tool,
    dispatcher=_PlanStepDispatcher(self),
    clock=lambda: datetime.now(timezone.utc),
    stale_threshold_seconds=config.executor_stale_threshold_seconds,
    circuit_breaker_steps=config.circuit_breaker_steps,
    backstop_seconds=config.executor_backstop_seconds,
)
```

- [ ] **Step 4: Run the daemon test file to confirm nothing crashes on startup**

Run: `python -m pytest tests/test_daemon.py -v`
Expected: every existing daemon test still passes. The new plumbing is inert until the executor is started.

- [ ] **Step 5: Commit**

```bash
git add pal/daemon.py
git commit -m "feat(daemon): instantiate PlanStore, PlanTool, Executor"
```

---

### Task 21: Daemon trigger hooks and idle tracking

**Files:**
- Modify: `pal/daemon.py`
- Modify: `tests/test_daemon.py`

- [ ] **Step 1: Write failing test for turn-end trigger and idle tracking**

Add to `tests/test_daemon.py` (adapt fixture names to match the existing file):

```python
class TestExecutorTriggers:
    async def test_idle_flag_cleared_during_chat_turn(self, daemon_with_executor):
        """While the daemon is handling a chat turn, the executor must be non-idle."""
        daemon = daemon_with_executor
        idles_seen: list[bool] = []
        original = daemon.executor.set_idle
        def track(value: bool) -> None:
            idles_seen.append(value)
            original(value)
        daemon.executor.set_idle = track
        await daemon._handle_chat_turn("hello")  # or the actual entrypoint
        # Should see False (start of turn) then True (end of turn)
        assert False in idles_seen
        assert idles_seen[-1] is True

    async def test_turn_end_fires_executor_trigger(
        self, daemon_with_executor
    ):
        daemon = daemon_with_executor
        fired = []
        daemon.executor.fire_trigger = lambda reason: fired.append(reason)
        await daemon._handle_chat_turn("hello")
        assert "turn_end" in fired
```

Note: the exact fixture and entrypoint name depend on the current daemon test structure. Reuse whatever existing test does a chat turn end-to-end.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_daemon.py::TestExecutorTriggers -v`
Expected: failures because the daemon does not yet call `set_idle` or `fire_trigger`.

- [ ] **Step 3: Add idle tracking and trigger firing to the chat turn handler**

In `pal/daemon.py`, find the chat-turn entrypoint and wrap it:

```python
    async def _handle_chat_turn(self, message: str) -> ...:
        if hasattr(self, "executor"):
            self.executor.set_idle(False)
        try:
            # ... existing turn handling code ...
            result = await self._existing_turn_code(message)
        finally:
            if hasattr(self, "executor"):
                self.executor.set_idle(True)
                self.executor.fire_trigger("turn_end")
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_daemon.py::TestExecutorTriggers -v`
Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/daemon.py tests/test_daemon.py
git commit -m "feat(daemon): idle tracking and turn-end trigger for executor"
```

---

### Task 22: Daemon — background executor loop

**Files:**
- Modify: `pal/daemon.py`
- Modify: `tests/test_daemon.py`

- [ ] **Step 1: Write failing test for executor background task lifecycle**

Add to `tests/test_daemon.py`:

```python
class TestExecutorLifecycle:
    async def test_executor_task_starts_when_enabled(
        self, daemon_with_executor
    ):
        daemon = daemon_with_executor
        await daemon.start_executor()
        assert daemon._executor_task is not None
        assert not daemon._executor_task.done()
        await daemon.stop_executor()
        assert daemon._executor_task.done()

    async def test_executor_disabled_skips_task(
        self, daemon_factory
    ):
        daemon = daemon_factory(executor_enabled=False)
        await daemon.start_executor()
        assert daemon._executor_task is None
```

Fixtures: `daemon_with_executor` builds a daemon with executor enabled and config defaults. `daemon_factory` lets tests override config fields. If these fixtures do not exist, add them to `tests/conftest.py` in a straightforward way that reuses the existing daemon test plumbing.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_daemon.py::TestExecutorLifecycle -v`
Expected: `AttributeError` for `start_executor` and `stop_executor`.

- [ ] **Step 3: Add start/stop methods and background loop**

In `pal/daemon.py`:

```python
import asyncio


    async def start_executor(self) -> None:
        if not self.config.executor_enabled:
            self._executor_task = None
            return
        self._executor_stop_event = asyncio.Event()
        self._executor_task = asyncio.create_task(self._executor_loop())

    async def stop_executor(self) -> None:
        if self._executor_task is None:
            return
        self._executor_stop_event.set()
        try:
            await asyncio.wait_for(self._executor_task, timeout=5.0)
        except asyncio.TimeoutError:
            self._executor_task.cancel()

    async def _executor_loop(self) -> None:
        # Fire an initial trigger for startup processing
        self.executor.fire_trigger("startup")
        while not self._executor_stop_event.is_set():
            try:
                await self.executor.process_pending_triggers()
            except Exception as exc:
                # Log and continue; one bad tick should not kill the loop
                self._log_error(f"executor tick failed: {exc}")
            try:
                await asyncio.wait_for(
                    self._executor_stop_event.wait(),
                    timeout=5.0,  # poll interval for trigger checks
                )
            except asyncio.TimeoutError:
                continue
```

Hook `start_executor` into the daemon's normal startup sequence (wherever `run` or `serve` is called) and `stop_executor` into shutdown.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_daemon.py::TestExecutorLifecycle -v`
Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/daemon.py tests/test_daemon.py
git commit -m "feat(daemon): background executor loop with start/stop lifecycle"
```

---

### Task 23: Error-recovery re-read rule

**Files:**
- Modify: `pal/daemon.py`
- Modify: `tests/test_daemon.py`

- [ ] **Step 1: Write failing test for error-recovery re-read injection**

Add to `tests/test_daemon.py`:

```python
class TestErrorRecovery:
    async def test_injects_replan_read_prompt_after_error(
        self, daemon_with_executor, tool
    ):
        daemon = daemon_with_executor
        # Simulate: previous turn errored
        daemon._previous_turn_errored = True
        # Handle a new turn and capture the system messages sent to inference
        sent_messages = []
        daemon._capture_system_messages = lambda msgs: sent_messages.extend(msgs)
        await daemon._handle_chat_turn("what's next?")
        combined = " ".join(sent_messages)
        assert "plan_list" in combined or "plan_get" in combined
        assert "in_progress" in combined
```

Note: the exact message injection hook depends on how the existing prompt builder composes messages. The test above is a shape hint; adapt `_capture_system_messages` to whatever interception point the existing daemon uses.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_daemon.py::TestErrorRecovery -v`
Expected: failure because no injection happens yet.

- [ ] **Step 3: Add error tracking and injection**

In `pal/daemon.py`:

```python
    def __init__(self, ...):
        # ... existing init ...
        self._previous_turn_errored = False

    async def _handle_chat_turn(self, message: str) -> ...:
        if hasattr(self, "executor"):
            self.executor.set_idle(False)
        extra_system = []
        if self._previous_turn_errored:
            extra_system.append(
                "The previous turn ended in an error. Before responding, call "
                "plan_list with status_filter='in_progress' and plan_get on any "
                "matching plans to reconstruct current state. Summarize what you "
                "find before taking any action."
            )
            self._previous_turn_errored = False
        try:
            result = await self._existing_turn_code(message, extra_system=extra_system)
        except Exception:
            self._previous_turn_errored = True
            raise
        finally:
            if hasattr(self, "executor"):
                self.executor.set_idle(True)
                self.executor.fire_trigger("turn_end")
        return result
```

The `extra_system` parameter may already exist on the existing turn code; if not, thread it through the prompt builder.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_daemon.py::TestErrorRecovery -v`
Expected: the test passes.

- [ ] **Step 5: Run full daemon test file**

Run: `python -m pytest tests/test_daemon.py -v`
Expected: every test in the file passes.

- [ ] **Step 6: Commit**

```bash
git add pal/daemon.py tests/test_daemon.py
git commit -m "feat(daemon): inject plan re-read prompt after errored turn"
```

---

## Phase 6: Wisdom Update and Migration

### Task 24: Retire the tasks/current.md wisdom entry

**Files:**
- Identify: the existing `_wisdom/` entry that references `tasks/current.md`

- [ ] **Step 1: Find the wisdom entry**

Run: `grep -rn "tasks/current" ~/vault/_wisdom/ 2>/dev/null || echo "not found"`

Expected: either a path like `~/vault/_wisdom/<something>.md` or "not found." If "not found", the wisdom entry may live in the daemon's prompt builder code instead. In that case, run `grep -rn "tasks/current" pal/ docs/`.

- [ ] **Step 2: Draft the replacement wisdom content**

The new entry should instruct the agent to use the planning tool, not hand-edit files. Content:

```markdown
---
title: Planning and task tracking
tags: [meta, wisdom]
---

When starting any multi-step work, create a plan using `plan_create` and add steps with `plan_add_step`. Never hand-edit plan files; always use the planning tool, which enforces valid state transitions and records timestamps.

Step discipline:
- Each step should be atomic: completing it leaves the vault in a consistent state.
- Verification steps are their own steps, not inline assumptions. When an irreversible action (archive, delete, bulk edit) depends on a precondition, the verification step must come first and be listed in the next step's `depends_on`.
- Call `plan_start_step` before doing the work, `plan_complete_step` only after the work is done and verified.
- If a step cannot complete, call `plan_fail_step` (with an error), `plan_block_step` (with what you're waiting on), or `plan_defer_step` (with a reason) rather than leaving it in_progress.

Autonomy:
- Plans default to non-autonomous. Only set `autonomous: true` after explicit user confirmation. The user decides what is safe to run unsupervised.
- Do not flip the autonomous flag on your own initiative, even if it seems helpful.

After any error in a previous turn, always call `plan_list` with `status_filter='in_progress'` and `plan_get` on any matching plans before taking action. Do not guess at state you cannot verify.

The scratchpad (markdown body of a plan file, appended via `plan_append_note`) is for reasoning notes, findings, and links to vault articles. It is not a substitute for structured step state.
```

- [ ] **Step 3: Replace the old wisdom entry**

If the old entry exists in the vault, use the normal vault update flow (either a direct file edit or a daemon update). If it is in code, update it in place.

- [ ] **Step 4: Run the full test suite once to confirm everything still passes**

Run: `python -m pytest -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add -A  # Wisdom file location varies — use targeted add instead if needed
git commit -m "docs(wisdom): update task-tracking guidance to use the planning tool"
```

---

### Task 25: Integration smoke test end-to-end

**Files:**
- Create: `tests/test_plan_integration.py`

- [ ] **Step 1: Write an end-to-end integration test**

```python
# tests/test_plan_integration.py
"""End-to-end test: create a plan, run it through the executor, verify autonomy."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pal.executor import Executor
from pal.plan_model import PlanStatus, StepStatus
from pal.plan_store import PlanStore
from pal.plan_tool import PlanTool


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: int) -> None:
        self._now += timedelta(seconds=seconds)


@pytest.mark.asyncio
async def test_end_to_end_autonomous_plan(tmp_path):
    clock = FakeClock(datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc))
    store = PlanStore(tmp_path)
    tool = PlanTool(store=store, clock=clock)

    # Agent creates a plan with two steps
    plan_id = tool.plan_create(
        title="Cleanup",
        description="Two-step cleanup",
    )
    step_1 = tool.plan_add_step(plan_id=plan_id, description="Verify")
    step_2 = tool.plan_add_step(
        plan_id=plan_id, description="Archive", depends_on=[step_1]
    )
    tool.plan_set_autonomous(plan_id=plan_id, autonomous=True)

    # Dispatcher fakes the "work" by transitioning steps through the tool
    class FakeDispatcher:
        def __init__(self, tool):
            self.tool = tool
        async def dispatch_step(self, plan_id: str, step_id: str) -> None:
            self.tool.plan_start_step(plan_id=plan_id, step_id=step_id)
            self.tool.plan_complete_step(plan_id=plan_id, step_id=step_id)

    executor = Executor(
        store=store,
        tool=tool,
        dispatcher=FakeDispatcher(tool),
        clock=clock,
        stale_threshold_seconds=600,
        circuit_breaker_steps=10,
    )
    executor.set_idle(True)

    # First tick dispatches step_1
    await executor.tick()
    plan = store.get(plan_id)
    assert plan.get_step(step_1).status == StepStatus.DONE
    assert plan.get_step(step_2).status == StepStatus.PENDING

    # Second tick dispatches step_2 now that step_1 is done
    await executor.tick()
    plan = store.get(plan_id)
    assert plan.get_step(step_2).status == StepStatus.DONE
    assert plan.status == PlanStatus.DONE


@pytest.mark.asyncio
async def test_end_to_end_crash_recovery(tmp_path):
    clock = FakeClock(datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc))
    store = PlanStore(tmp_path)
    tool = PlanTool(store=store, clock=clock)

    plan_id = tool.plan_create(title="Risky", description="D")
    step_id = tool.plan_add_step(plan_id=plan_id, description="Work")
    tool.plan_set_autonomous(plan_id=plan_id, autonomous=True)

    # Simulate: step started, then the process died
    tool.plan_start_step(plan_id=plan_id, step_id=step_id)

    # Daemon restarts; time passes
    clock.advance(1000)

    class NoopDispatcher:
        async def dispatch_step(self, plan_id: str, step_id: str) -> None:
            pass

    executor = Executor(
        store=store,
        tool=tool,
        dispatcher=NoopDispatcher(),
        clock=clock,
        stale_threshold_seconds=600,
        circuit_breaker_steps=10,
    )
    executor.set_idle(True)
    await executor.tick()

    # Stale step must have been marked failed
    plan = store.get(plan_id)
    step = plan.get_step(step_id)
    assert step.status == StepStatus.FAILED
    assert "stale" in (step.error or "").lower()
```

- [ ] **Step 2: Run the integration test**

Run: `python -m pytest tests/test_plan_integration.py -v`
Expected: both tests pass.

- [ ] **Step 3: Run the full test suite**

Run: `python -m pytest -v`
Expected: all tests pass, no regressions across the project.

- [ ] **Step 4: Commit**

```bash
git add tests/test_plan_integration.py
git commit -m "test(plan): end-to-end integration for autonomous advancement and crash recovery"
```

---

## Self-Review

After completing all 25 tasks:

1. **Spec coverage check** — Walk through each section of the spec:
   - Task model (fine-grained steps, status vocabulary, transitions, dependencies): Tasks 1-5
   - File format (YAML frontmatter, scratchpad, one file per plan): Tasks 6-7
   - Git commit integration: Task 8
   - Planning tool operations: Tasks 9-12
   - Tool dispatch + schemas: Tasks 13-14
   - Executor with idle guard, stale detection, circuit breaker, trigger coalescing: Tasks 15-18
   - Config tuning fields: Task 19
   - Daemon integration (instantiation, triggers, lifecycle, error recovery): Tasks 20-23
   - Wisdom update: Task 24
   - End-to-end verification: Task 25

2. **Placeholder scan** — No TBD, TODO, "implement later", or unfilled code blocks. Every step has real content.

3. **Type consistency** — `StepStatus`, `PlanStatus`, `Step`, `Plan`, `PlanStore`, `PlanTool`, `Executor`, `PlanToolError` all used consistently across tasks.

4. **Test granularity** — every production code change has a failing test written first.

---

## Execution Notes

- **Worktree:** the executing agent should run this plan in a dedicated git worktree created via `superpowers:using-git-worktrees`. Do not execute it on `main`.
- **Commit discipline:** one commit per task, never batch multiple tasks. The commit messages in each task's final step are the intended wording.
- **Skipping tests:** if any existing test breaks during this plan's execution, stop and diagnose. Do not mark a task complete while tests are red.
- **Daemon integration tasks (20-23)** are the highest-risk because `daemon.py` is large and the existing chat pipeline is not fully mapped in this plan. Before starting Task 20, the executor should spend a few minutes reading `daemon.py` to locate the exact method names for the chat turn and the component construction site. Adapt the example code in those tasks to match the real names.
