# Inference Safety Guards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound the worst case of model misbehavior in PAL's inference path and make user cancellation work, by adding a `max_tokens` cap, replacing the busy-channel rejection with preemption, and emitting one structured log per turn.

**Architecture:** Two repos. `agent_core` v0.3.1 adds a `max_tokens` parameter on `complete()` and `stream()`, plus a new `StreamEnd` sentinel that `stream()` yields after the SSE stream completes (carrying `finish_reason` and `chunks_yielded`). PAL consumes v0.3.1 and replaces the per-connection `current_chat_task` with a per-channel `_chat_tasks` registry; new chat/command messages preempt any in-flight turn on the same channel. PAL's `_handle_chat` consumes `StreamEnd`, catches `asyncio.CancelledError`, writes forensic JSONL records on abort, and emits a structured `chat_turn_ended` log on every turn exit.

**Tech Stack:** Python 3.12+, hatchling, pytest, pytest-asyncio, httpx (already in use). No new runtime/dev deps.

**Repos involved:**
- agent_core: `/home/edible/Projects/agent_core` (currently `v0.3.0` on main; the Phase D feature branch `feature/phase-d-per-channel-state` has unmerged Phase D work; this plan branches a separate `feature/inference-safety-guards` from main and targets `v0.3.1`).
- PAL: `/home/edible/Projects/PAL` (main checkout). PAL-side migration work happens in a feature-branch worktree at `/home/edible/Projects/PAL/.worktrees/inference-safety-guards`.

**Reference:**
- Spec: `docs/superpowers/specs/2026-04-28-inference-safety-guards-design.md`.
- Triggering incident transcript: `discord_output.txt` (10,910 lines of "I'll just call the tool" loop).
- Phase 2 backlog memory: `project_phase2_inference_investigation.md`.

**Phase D status (paused for this work):**
- Phase D agent_core branch has commits `7fa4501` (protocol package) and `9c97224` (Conversation module). Untouched by this plan.
- Phase D PAL work has not started.
- After this fix ships, Phase D resumes by rebasing `feature/phase-d-per-channel-state` onto the new `agent_core` main (which will be at `v0.3.1`) and updating Phase D's plan to carry the channels.py replay-skip change forward.

---

## Pre-flight: code map

| Concern | Location | Notes |
|---|---|---|
| Inference client | `agent_core/agent_core/inference.py` | `complete()` lines 128-174; `stream()` lines 176-252; payload built lines 189-197; SSE loop lines 204-239. |
| PAL daemon, dispatch | `pal/daemon.py:335-341` | Existing rejection guard ("A previous turn is still being processed"). Replaced by preemption. |
| PAL daemon, current_chat_task | `pal/daemon.py:305, 344-346, 370-375` | Per-connection task storage; widens to per-channel registry, broader disconnect cleanup. |
| PAL daemon, chat handler | `pal/daemon.py:415` (`_handle_chat`) and stream loop at `:478-487` | StreamEnd handling, CancelledError, forensic, log. |
| PAL channels replay | `pal/channels.py:62-91` (`_replay_into`) | Add role-filter to skip forensic records. |
| PAL config | `pal/config.py` (BaseConfig dataclass) | New `max_response_tokens: int = 4096` field with env override. |

---

# Part 1: agent_core changes (target: v0.3.1)

Working directory throughout Part 1: `/home/edible/Projects/agent_core`. Use `.venv/bin/pytest`.

## Task 1: agent_core pre-flight

**Files:**
- None modified.

- [ ] **Step 1: Confirm working state and switch to main**

```bash
cd /home/edible/Projects/agent_core
git status
git branch --show-current
```

Expected: a clean working tree. Current branch may be `main` or `feature/phase-d-per-channel-state` (Phase D work). If on the Phase D branch, switch back:

```bash
git checkout main
```

- [ ] **Step 2: Fetch and confirm parity with origin**

```bash
git fetch origin
git pull
git log --oneline -3
```

Expected: HEAD is at or near `8b4679e` ("chore: bump version to 0.3.0") on `main`, matching `origin/main`.

- [ ] **Step 3: Create the safety-fix feature branch**

```bash
git checkout -b feature/inference-safety-guards
```

- [ ] **Step 4: Run baseline tests**

```bash
.venv/bin/pytest -x
```

Expected: all 187 (or more) tests pass.

---

## Task 2: Add `max_tokens` parameter to `complete()`

**Files:**
- Modify: `/home/edible/Projects/agent_core/agent_core/inference.py:128-174` (the `complete()` method) and `:189-197` (payload construction shared by both).
- Test: `/home/edible/Projects/agent_core/tests/test_inference.py`

This task wires `max_tokens` into the request payload for non-streaming completions.

- [ ] **Step 1: Write the failing test**

Add to `/home/edible/Projects/agent_core/tests/test_inference.py`:

```python
import json
import pytest
import httpx
from agent_core.inference import InferenceClient


@pytest.mark.asyncio
async def test_complete_includes_max_tokens_when_set():
    """When max_tokens is passed, it appears in the request payload."""
    captured: dict = {}

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }],
            },
        )

    transport = httpx.MockTransport(mock_handler)
    client = InferenceClient(base_url="http://test", model="m")
    client._client = httpx.AsyncClient(transport=transport)

    await client.complete(messages=[{"role": "user", "content": "hi"}], max_tokens=512)

    assert captured["body"].get("max_tokens") == 512


@pytest.mark.asyncio
async def test_complete_omits_max_tokens_when_none():
    """When max_tokens is None (default), no max_tokens key is in the payload."""
    captured: dict = {}

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }],
            },
        )

    transport = httpx.MockTransport(mock_handler)
    client = InferenceClient(base_url="http://test", model="m")
    client._client = httpx.AsyncClient(transport=transport)

    await client.complete(messages=[{"role": "user", "content": "hi"}])

    assert "max_tokens" not in captured["body"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_inference.py::test_complete_includes_max_tokens_when_set tests/test_inference.py::test_complete_omits_max_tokens_when_none -v
```

Expected: FAIL because `complete()` does not yet accept `max_tokens`.

- [ ] **Step 3: Update `complete()` signature**

Open `/home/edible/Projects/agent_core/agent_core/inference.py`. Find the `complete()` method (around line 128). Update its signature to add `max_tokens: int | None = None` after `reasoning`. Update payload construction inside `complete()` to add `max_tokens` to the payload when it's not None. The relevant block (around `:189-197` is shared between `complete()` and `stream()`) is what builds the payload; both methods will need the same treatment, but in this task you only modify the non-streaming path's payload-construction code.

The signature becomes:

```python
async def complete(
    self,
    messages: list[dict],
    tools: list[dict] | None = None,
    model: str | None = None,
    reasoning: Literal["on", "off"] | None = None,
    max_tokens: int | None = None,
) -> CompletionResult:
```

In the payload construction inside `complete()`:

```python
payload: dict = {"model": resolved_model, "messages": messages}
if tools:
    payload["tools"] = tools
    payload["tool_choice"] = "auto"
if reasoning is not None:
    payload = shape_request(payload, resolved_model, reasoning)
if max_tokens is not None:
    payload["max_tokens"] = max_tokens
```

(If the payload-building code is currently shared with `stream()` via a helper, only adjust `complete()`'s call site for now; `stream()` is updated in Task 3.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_inference.py::test_complete_includes_max_tokens_when_set tests/test_inference.py::test_complete_omits_max_tokens_when_none -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/inference.py tests/test_inference.py
git commit -m "feat(inference): accept max_tokens parameter in complete()"
```

---

## Task 3: Add `max_tokens` and `StreamEnd` to `stream()`

**Files:**
- Modify: `/home/edible/Projects/agent_core/agent_core/inference.py:176-252` (the `stream()` method).
- Test: `/home/edible/Projects/agent_core/tests/test_inference.py`

This task adds a new `StreamEnd` dataclass, exports it, threads `max_tokens` through `stream()`, and yields `StreamEnd` after the SSE stream completes.

- [ ] **Step 1: Write the failing tests**

Add to `/home/edible/Projects/agent_core/tests/test_inference.py`:

```python
from agent_core.inference import StreamEnd


def _sse_response(chunks: list[dict]) -> httpx.Response:
    """Build a streaming Response that emits the given chunk objects as SSE lines."""
    body_lines = []
    for c in chunks:
        body_lines.append(f"data: {json.dumps(c)}")
    body_lines.append("data: [DONE]")
    body = "\n".join(body_lines).encode("utf-8")
    return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})


@pytest.mark.asyncio
async def test_stream_includes_max_tokens_when_set():
    captured: dict = {}

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _sse_response([
            {"choices": [{"delta": {"content": "hi"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ])

    transport = httpx.MockTransport(mock_handler)
    client = InferenceClient(base_url="http://test", model="m")
    client._client = httpx.AsyncClient(transport=transport)

    out = []
    async for item in client.stream(messages=[{"role": "user", "content": "hi"}], max_tokens=256):
        out.append(item)

    assert captured["body"].get("max_tokens") == 256


@pytest.mark.asyncio
async def test_stream_yields_streamend_with_stop_reason():
    async def mock_handler(request: httpx.Request) -> httpx.Response:
        return _sse_response([
            {"choices": [{"delta": {"content": "alpha"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "beta"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ])

    transport = httpx.MockTransport(mock_handler)
    client = InferenceClient(base_url="http://test", model="m")
    client._client = httpx.AsyncClient(transport=transport)

    out = []
    async for item in client.stream(messages=[{"role": "user", "content": "hi"}]):
        out.append(item)

    assert out[:-1] == ["alpha", "beta"]
    assert isinstance(out[-1], StreamEnd)
    assert out[-1].finish_reason == "stop"
    assert out[-1].chunks_yielded == 2


@pytest.mark.asyncio
async def test_stream_yields_streamend_with_length_reason_when_capped():
    async def mock_handler(request: httpx.Request) -> httpx.Response:
        return _sse_response([
            {"choices": [{"delta": {"content": "x"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "y"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "length"}]},
        ])

    transport = httpx.MockTransport(mock_handler)
    client = InferenceClient(base_url="http://test", model="m")
    client._client = httpx.AsyncClient(transport=transport)

    out = []
    async for item in client.stream(messages=[{"role": "user", "content": "hi"}], max_tokens=2):
        out.append(item)

    end = out[-1]
    assert isinstance(end, StreamEnd)
    assert end.finish_reason == "length"
    assert end.chunks_yielded == 2


@pytest.mark.asyncio
async def test_stream_tool_calls_does_not_yield_streamend():
    """When the model emits tool_calls, no StreamEnd follows."""
    async def mock_handler(request: httpx.Request) -> httpx.Response:
        return _sse_response([
            {"choices": [{"delta": {
                "tool_calls": [{
                    "index": 0, "id": "tc1",
                    "function": {"name": "search", "arguments": "{\"q\":\"x\"}"},
                }]
            }, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ])

    transport = httpx.MockTransport(mock_handler)
    client = InferenceClient(base_url="http://test", model="m")
    client._client = httpx.AsyncClient(transport=transport)

    out = []
    async for item in client.stream(messages=[{"role": "user", "content": "hi"}]):
        out.append(item)

    # Last item is the tool-calls list, not a StreamEnd.
    assert isinstance(out[-1], list)
    assert all(not isinstance(x, StreamEnd) for x in out)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_inference.py::test_stream_includes_max_tokens_when_set tests/test_inference.py::test_stream_yields_streamend_with_stop_reason tests/test_inference.py::test_stream_yields_streamend_with_length_reason_when_capped tests/test_inference.py::test_stream_tool_calls_does_not_yield_streamend -v
```

Expected: FAIL — `StreamEnd` does not yet exist; `stream()` does not yet accept `max_tokens`.

- [ ] **Step 3: Add the `StreamEnd` dataclass**

Open `/home/edible/Projects/agent_core/agent_core/inference.py`. Near the existing dataclasses (e.g. `CompletionResult`, `ToolCall`), add:

```python
@dataclass
class StreamEnd:
    """Sentinel yielded as the final item by `InferenceClient.stream()` after the
    SSE stream completes (text-output path only). Not yielded when the model
    emitted tool calls; the tool-call list itself signals end-of-stream there.
    """
    finish_reason: str   # "stop" | "length" | "tool_calls" | "content_filter" | "unknown"
    chunks_yielded: int
```

Make sure `StreamEnd` is importable from `agent_core.inference` (e.g. it's at module scope, and if there's an `__all__` list, add it; otherwise no extra step needed).

- [ ] **Step 4: Update `stream()` signature and body**

Find the `stream()` method (around line 176). Update its signature to add `max_tokens: int | None = None` after `reasoning`:

```python
async def stream(
    self,
    messages: list[dict],
    tools: list[dict] | None = None,
    model: str | None = None,
    reasoning: Literal["on", "off"] | None = None,
    max_tokens: int | None = None,
):
```

Update the type annotation in the docstring or method's stated return generator type to include `StreamEnd` if present (the existing annotation pattern looks like `AsyncGenerator[str | list[ToolCall], None]`; change it to `AsyncGenerator[str | list[ToolCall] | StreamEnd, None]`).

In `stream()`'s payload construction (around lines 189-197):

```python
payload: dict = {"model": resolved_model, "messages": messages, "stream": True}
if tools:
    payload["tools"] = tools
    payload["tool_choice"] = "auto"
if reasoning is not None:
    payload = shape_request(payload, resolved_model, reasoning)
if max_tokens is not None:
    payload["max_tokens"] = max_tokens
```

In `stream()`'s SSE loop (around lines 204-239), add `chunks_yielded` and `finish_reason` accumulators. Capture `finish_reason` from each chunk. After the loop ends, yield `StreamEnd` if no tool calls were emitted. The relevant section becomes:

```python
chunks_yielded = 0
finish_reason = "unknown"
is_tool_response = False
tool_call_acc: dict = {}

async with self._stream_with_retry(url, payload) as resp:
    async for line in resp.aiter_lines():
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            break
        chunk = json.loads(data)
        choice = chunk["choices"][0]
        if choice.get("finish_reason"):
            finish_reason = choice["finish_reason"]
        delta = choice.get("delta", {})

        # Tool-call deltas
        tc_deltas = delta.get("tool_calls")
        if tc_deltas is not None:
            is_tool_response = True
            for tcd in tc_deltas:
                idx = tcd.get("index", 0)
                if idx not in tool_call_acc:
                    tool_call_acc[idx] = {"id": tcd.get("id", ""), "name": "", "arguments_str": ""}
                acc = tool_call_acc[idx]
                if tcd.get("id"):
                    acc["id"] = tcd["id"]
                func = tcd.get("function", {})
                if func.get("name"):
                    acc["name"] = func["name"]
                if func.get("arguments"):
                    acc["arguments_str"] += func["arguments"]
            continue

        # Regular text content
        content = delta.get("content")
        if content is not None:
            chunks_yielded += 1
            yield content

# After the SSE stream ends:
if is_tool_response:
    # Emit the assembled tool calls list (existing behavior).
    yield [
        ToolCall(
            id=acc["id"],
            name=acc["name"],
            arguments=json.loads(acc["arguments_str"]) if acc["arguments_str"] else {},
        )
        for acc in tool_call_acc.values()
    ]
else:
    yield StreamEnd(finish_reason=finish_reason, chunks_yielded=chunks_yielded)
```

(Adapt to the existing tool-call-yield code style. The key new behavior is: when not a tool response, yield `StreamEnd` once.)

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_inference.py -v
```

Expected: all tests pass, including the four new stream tests and the two complete tests from Task 2.

- [ ] **Step 6: Run the full agent_core suite**

```bash
.venv/bin/pytest -x
```

Expected: all green (no regressions to Phase B/C tests).

- [ ] **Step 7: Commit**

```bash
git add agent_core/inference.py tests/test_inference.py
git commit -m "feat(inference): add max_tokens + StreamEnd sentinel to stream()"
```

---

## Task 4: Bump agent_core version to 0.3.1

**Files:**
- Modify: `/home/edible/Projects/agent_core/pyproject.toml`
- Modify: `/home/edible/Projects/agent_core/CHANGELOG.md`

- [ ] **Step 1: Bump version**

Edit `/home/edible/Projects/agent_core/pyproject.toml`. Change `version = "0.3.0"` to `version = "0.3.1"`.

- [ ] **Step 2: Update CHANGELOG**

Edit `/home/edible/Projects/agent_core/CHANGELOG.md`. Prepend under the most recent entry:

```markdown
## [0.3.1] - 2026-04-29

### Added
- `InferenceClient.complete()` and `InferenceClient.stream()` accept an optional `max_tokens: int | None` parameter that flows into the request payload when set.
- New `agent_core.inference.StreamEnd` dataclass yielded as the final item by `InferenceClient.stream()` on the text-output path. Carries `finish_reason` (one of "stop", "length", "tool_calls", "content_filter", "unknown") and `chunks_yielded`.

### Notes
- Tool-call streams continue to yield the assembled `list[ToolCall]` as their final item; no `StreamEnd` follows tool calls. Existing consumers that break on `isinstance(item, list)` are unaffected.
```

- [ ] **Step 3: Run the full suite**

```bash
.venv/bin/pytest
```

Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore: bump version to 0.3.1"
```

---

## Task 5: Push agent_core branch and open PR

- [ ] **Step 1: Push the feature branch**

```bash
cd /home/edible/Projects/agent_core
git push -u origin feature/inference-safety-guards
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --title "Inference safety: max_tokens + StreamEnd sentinel" --body "$(cat <<'EOF'
## Summary
- Adds `max_tokens` parameter to `complete()` and `stream()`. Forwards into the request payload when set; absent otherwise.
- Adds `StreamEnd` dataclass yielded as the final item by `stream()` on the text-output path. Captures the server's `finish_reason` and the `chunks_yielded` count.
- Bumps version to 0.3.1.

Spec: PAL repo `docs/superpowers/specs/2026-04-28-inference-safety-guards-design.md`.

## Test plan
- [ ] CI passes
- [ ] PAL safety-fix branch lands successfully against this tag

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Wait for CI**

```bash
gh pr checks
```

Wait until green.

---

## Task 6: Merge agent_core PR and tag v0.3.1

- [ ] **Step 1: Merge**

If running from the agent_core checkout (not a worktree):

```bash
cd /home/edible/Projects/agent_core
gh pr merge --merge
```

If from a worktree (per the Phase C lesson, `gh pr merge` fails inside worktrees):

```bash
PR_NUM=$(gh pr view --json number --jq .number)
gh api -X PUT repos/EdibleTuber/agent_core/pulls/$PR_NUM/merge --field merge_method=merge
```

- [ ] **Step 2: Update local main and tag**

```bash
cd /home/edible/Projects/agent_core
git checkout main
git pull
git tag v0.3.1
git push origin v0.3.1
```

Expected: tag exists on remote at the merge commit.

- [ ] **Step 3: Verify version metadata**

```bash
grep -E '^version' pyproject.toml
```

Expected: `version = "0.3.1"`.

---

# Part 2: PAL changes (feature branch)

Working directory: `/home/edible/Projects/PAL/.worktrees/inference-safety-guards`. Use `/home/edible/Projects/PAL/.venv/bin/pytest`.

## Task 7: Create PAL worktree and pre-flight

**Files:**
- None modified.

- [ ] **Step 1: Create the feature-branch worktree**

```bash
cd /home/edible/Projects/PAL
git fetch origin
git worktree add .worktrees/inference-safety-guards -b feature/inference-safety-guards origin/main
cd .worktrees/inference-safety-guards
```

- [ ] **Step 2: Confirm clean working tree**

```bash
git status
```

Expected: clean, on branch `feature/inference-safety-guards`.

- [ ] **Step 3: Confirm baseline tests pass**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest -x \
  --ignore=tests/test_daemon.py \
  --ignore=tests/test_integration.py \
  --ignore=tests/test_chat_research_integration.py \
  --ignore=tests/test_consolidate_integration.py \
  --ignore=tests/test_learning_e2e.py
```

Expected: all green (the 5 known-flaky integration tests are excluded per project memory).

---

## Task 8: Bump agent_core dependency to 0.3.1

**Files:**
- Modify: `/home/edible/Projects/PAL/.worktrees/inference-safety-guards/pyproject.toml`

- [ ] **Step 1: Update dependency**

Read `pyproject.toml`. Find the `agent_core` dependency line. Change the pinned tag from `v0.3.0` to `v0.3.1`. The line typically looks like:

```toml
"agent_core @ git+https://github.com/EdibleTuber/agent_core.git@v0.3.0",
```

Becomes:

```toml
"agent_core @ git+https://github.com/EdibleTuber/agent_core.git@v0.3.1",
```

- [ ] **Step 2: Reinstall**

```bash
/home/edible/Projects/PAL/.venv/bin/pip install -e .
```

- [ ] **Step 3: Verify**

```bash
/home/edible/Projects/PAL/.venv/bin/python -c "from agent_core.inference import StreamEnd; print('StreamEnd present')"
```

Expected: prints `StreamEnd present`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: bump agent_core dependency to v0.3.1"
```

---

## Task 9: Add `max_response_tokens` to `BaseConfig`

**Files:**
- Modify: `/home/edible/Projects/PAL/.worktrees/inference-safety-guards/pal/config.py`
- Test: `/home/edible/Projects/PAL/.worktrees/inference-safety-guards/tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py`:

```python
import os
from unittest.mock import patch

from pal.config import BaseConfig, load_config  # adapt to actual public symbols


def test_default_max_response_tokens_is_4096():
    cfg = BaseConfig()
    assert cfg.max_response_tokens == 4096


def test_max_response_tokens_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("PAL_MAX_RESPONSE_TOKENS", "2048")
    cfg = load_config(vault_path=tmp_path)  # adapt to actual loader signature
    assert cfg.max_response_tokens == 2048
```

(If `load_config` takes different arguments, adapt the call. Use the Read tool first to inspect `pal/config.py`'s actual loader.)

- [ ] **Step 2: Run tests to verify failure**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest tests/test_config.py::test_default_max_response_tokens_is_4096 tests/test_config.py::test_max_response_tokens_env_override -v
```

Expected: FAIL with `AttributeError: 'BaseConfig' object has no attribute 'max_response_tokens'`.

- [ ] **Step 3: Add the field to `BaseConfig`**

Open `pal/config.py`. Add the field to the `BaseConfig` dataclass:

```python
@dataclass
class BaseConfig:
    # ... existing fields ...
    max_response_tokens: int = 4096
```

(Add it alongside other tunables; preserve the alphabetical or logical ordering used by the existing dataclass.)

- [ ] **Step 4: Wire the env-var override**

In the same file, find the function that applies environment-variable overrides (commonly `load_config` or similar). Add:

```python
if env_val := os.environ.get("PAL_MAX_RESPONSE_TOKENS"):
    config.max_response_tokens = int(env_val)
```

If PAL uses a TOML or YAML config file loader, it should already read the new field automatically (dataclass-style loaders typically do); verify by inspecting the loader.

- [ ] **Step 5: Run tests to verify they pass**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest tests/test_config.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pal/config.py tests/test_config.py
git commit -m "feat(config): add max_response_tokens with PAL_MAX_RESPONSE_TOKENS env override"
```

---

## Task 10: Update `pal/channels.py` replay to skip non-message roles

**Files:**
- Modify: `/home/edible/Projects/PAL/.worktrees/inference-safety-guards/pal/channels.py:62-91` (the `_replay_into` method).
- Test: `/home/edible/Projects/PAL/.worktrees/inference-safety-guards/tests/test_channels.py`

The replay logic must skip records whose `role` is not `user`, `assistant`, or `tool`. This makes future forensic records (Task 12) safe to write.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_channels.py`:

```python
import json
import pytest

from pal.channels import ChannelStore


@pytest.mark.asyncio
async def test_replay_skips_non_message_roles(tmp_path):
    """Records with role values other than user/assistant/tool are skipped on replay."""
    channels_dir = tmp_path
    channel_dir = channels_dir / "C1"
    channel_dir.mkdir()
    history = channel_dir / "history.jsonl"

    # Mix of valid messages and an "abort" forensic record.
    lines = [
        {"role": "user", "content": "hello"},
        {"role": "abort", "reason": "user_preempt", "partial_chars": 42, "ts": "2026-04-29T00:00:00Z"},
        {"role": "assistant", "content": "hi back"},
        {"role": "system_meta", "data": "should also be skipped"},
        {"role": "user", "content": "another"},
    ]
    history.write_text("\n".join(json.dumps(l) for l in lines) + "\n")

    store = ChannelStore(channels_dir=channels_dir, history_depth=10)
    conv = await store.get_or_create("C1")

    roles = [m["role"] for m in conv.messages]
    assert roles == ["user", "assistant", "user"]
    assert all(m.get("role") in ("user", "assistant", "tool") for m in conv.messages)
```

- [ ] **Step 2: Run the test to verify failure**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest tests/test_channels.py::test_replay_skips_non_message_roles -v
```

Expected: FAIL — current replay appends every parsed JSON line regardless of role.

- [ ] **Step 3: Update `_replay_into`**

Open `pal/channels.py`. Find `_replay_into` (around line 62). The current loop body looks like:

```python
for lineno, line in enumerate(raw.splitlines(), 1):
    line = line.strip()
    if not line:
        continue
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        logger.warning(
            "channel %s history.jsonl line %d malformed, skipping",
            history_path.parent.name, lineno,
        )
        continue
    conv._messages.append(message)
conv._truncate()
```

Add a role-filter check before appending. Replace the loop body with:

```python
for lineno, line in enumerate(raw.splitlines(), 1):
    line = line.strip()
    if not line:
        continue
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        logger.warning(
            "channel %s history.jsonl line %d malformed, skipping",
            history_path.parent.name, lineno,
        )
        continue
    if message.get("role") not in ("user", "assistant", "tool"):
        # Forensic records (e.g., role="abort") and unknown roles are
        # persisted but not part of the model-visible conversation.
        continue
    conv._messages.append(message)
conv._truncate()
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest tests/test_channels.py -v
```

Expected: all channels tests pass, including the new one.

- [ ] **Step 5: Commit**

```bash
git add pal/channels.py tests/test_channels.py
git commit -m "feat(channels): replay skips non-message roles for forensic compatibility"
```

---

## Task 11: Per-channel preemption infrastructure in daemon

**Files:**
- Modify: `/home/edible/Projects/PAL/.worktrees/inference-safety-guards/pal/daemon.py`
  - Add `_chat_tasks` field on `Daemon`
  - Replace the per-connection `current_chat_task` pattern in the connection-handling loop (around line 305, 335-346)
  - Broaden disconnect cleanup at lines 370-375
  - Add `_preempt_existing_turn` helper method
- Test: `/home/edible/Projects/PAL/.worktrees/inference-safety-guards/tests/test_daemon_cancellation.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_daemon_cancellation.py`:

```python
"""Tests for per-channel chat task preemption."""
import asyncio
import pytest

from pal.daemon import Daemon  # adapt if Daemon lives at a different name


class _SlowFakeInference:
    """Fakes InferenceClient.stream() that runs forever until cancelled."""
    async def stream(self, *args, **kwargs):
        while True:
            await asyncio.sleep(0.05)
            yield "."


@pytest.mark.asyncio
async def test_preempt_cancels_in_flight_task(tmp_path):
    """When a new turn arrives on a channel, the in-flight task is cancelled."""
    daemon = Daemon.__new__(Daemon)  # bypass __init__ for this isolated unit test
    daemon._chat_tasks = {}

    ran_to_completion = asyncio.Event()

    async def fake_chat():
        try:
            await asyncio.sleep(10)
            ran_to_completion.set()
        except asyncio.CancelledError:
            raise

    task = asyncio.create_task(fake_chat())
    daemon._chat_tasks["C1"] = task

    await daemon._preempt_existing_turn("C1")

    assert task.cancelled() or task.done()
    assert not ran_to_completion.is_set()


@pytest.mark.asyncio
async def test_preempt_no_op_when_task_already_done(tmp_path):
    """If the existing task is already done, _preempt_existing_turn returns immediately."""
    daemon = Daemon.__new__(Daemon)
    daemon._chat_tasks = {}

    async def quick():
        return None

    task = asyncio.create_task(quick())
    await task
    daemon._chat_tasks["C1"] = task

    await daemon._preempt_existing_turn("C1")
    # No exception, nothing to assert beyond completion.


@pytest.mark.asyncio
async def test_preempt_no_op_when_no_existing_task():
    """If the channel has no entry, the helper returns immediately."""
    daemon = Daemon.__new__(Daemon)
    daemon._chat_tasks = {}

    await daemon._preempt_existing_turn("never-was-a-task")
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest tests/test_daemon_cancellation.py -v
```

Expected: FAIL with `AttributeError: 'Daemon' object has no attribute '_preempt_existing_turn'`.

- [ ] **Step 3: Add the per-channel registry to `Daemon.__init__`**

Open `pal/daemon.py`. Find `Daemon.__init__`. Add the registry alongside other instance attributes:

```python
self._chat_tasks: dict[str, asyncio.Task] = {}
```

- [ ] **Step 4: Add the `_preempt_existing_turn` helper method**

Add this method to the `Daemon` class:

```python
async def _preempt_existing_turn(self, channel_id: str) -> None:
    """Cancel an in-flight chat task on this channel and wait briefly for unwind."""
    existing = self._chat_tasks.get(channel_id)
    if existing is None or existing.done():
        return
    existing.cancel()
    try:
        await asyncio.wait_for(existing, timeout=2.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    except Exception as exc:
        logger.warning("preempted task raised on cancel: %s", exc)
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest tests/test_daemon_cancellation.py -v
```

Expected: PASS for the three preempt-helper tests.

- [ ] **Step 6: Replace the per-connection guard with preemption dispatch**

Find the existing rejection guard at lines 335-341:

```python
if current_chat_task is not None and not current_chat_task.done():
    error = ErrorMessage(
        error="A previous turn is still being processed. Wait for it to complete."
    )
    writer.write(encode_message(error))
    await writer.drain()
    continue
```

And the existing chat-task creation at 344-346:

```python
current_chat_task = asyncio.create_task(
    self._handle_chat(msg, conv, channel_id, writer, tool_executor, scanner)
)
```

Replace both with the preemption pattern. Read the surrounding code first to understand the structure (typically inside `async def _handle_connection(...)` with branches for `ChatMessage` and `CommandMessage`). For each branch that creates a chat or command task, become:

```python
elif isinstance(msg, ChatMessage):
    channel_id = msg.channel_id or "cli-default"
    await self._preempt_existing_turn(channel_id)
    task = asyncio.create_task(
        self._handle_chat(msg, conv, channel_id, writer, tool_executor, scanner)
    )
    self._chat_tasks[channel_id] = task
elif isinstance(msg, CommandMessage):
    channel_id = msg.channel_id or "cli-default"
    await self._preempt_existing_turn(channel_id)
    task = asyncio.create_task(
        self._handle_command(msg, conv, channel_id, writer, tool_executor, scanner)
    )
    self._chat_tasks[channel_id] = task
```

The local `current_chat_task` variable can be removed if it's no longer referenced (or kept as a per-connection alias to the latest task if other code reads it; verify before removing).

- [ ] **Step 7: Broaden disconnect cleanup**

Find the disconnect-handling block at lines 370-375. The current code looks like:

```python
if current_chat_task is not None and not current_chat_task.done():
    current_chat_task.cancel()
    try:
        await current_chat_task
    except (asyncio.CancelledError, Exception):
        pass
```

This was per-connection cleanup of one task. Now multiple channels' tasks can be in flight on the same connection. Replace with:

```python
# Cancel all in-flight chat tasks on disconnect. Note: in this single-task-per-channel
# design, each connection is the sole owner of its tasks. If multiple connections were
# ever to share `_chat_tasks` we'd track ownership; for now, cancel all entries.
for channel_id, task in list(self._chat_tasks.items()):
    if not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
self._chat_tasks.clear()
```

(If your daemon has multiple concurrent connections that share the registry, attach a writer reference to the task at creation time and only cancel tasks belonging to the disconnected writer. The simpler "cancel all" pattern above works if the daemon only handles one connection at a time, which is PAL's current behavior. Verify by reading the surrounding code.)

- [ ] **Step 8: Run the existing PAL tests to detect regressions**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest tests/test_daemon_cancellation.py tests/test_daemon_channels.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add pal/daemon.py tests/test_daemon_cancellation.py
git commit -m "feat(daemon): per-channel chat task preemption replaces busy-channel rejection"
```

---

## Task 12: Instrument `_handle_chat` for safety + logging

**Files:**
- Modify: `/home/edible/Projects/PAL/.worktrees/inference-safety-guards/pal/daemon.py:415-...` (the `_handle_chat` method).
- Test: extend `/home/edible/Projects/PAL/.worktrees/inference-safety-guards/tests/test_daemon_cancellation.py`

This task threads `max_tokens` into the inference call, consumes the new `StreamEnd` sentinel, catches `asyncio.CancelledError`, writes forensic JSONL records on abort, and emits the structured `chat_turn_ended` log.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_daemon_cancellation.py`:

```python
import logging
from datetime import datetime, timezone
from pathlib import Path

from agent_core.inference import StreamEnd
from pal.conversation import Conversation


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_record_abort_forensic_writes_role_abort_line(tmp_path):
    """The forensic helper appends a role=abort record with reason and partial preview."""
    daemon = Daemon.__new__(Daemon)
    history = tmp_path / "C1" / "history.jsonl"
    conv = Conversation(history_depth=10, history_path=history)

    daemon._record_abort_forensic(conv, "user_preempt", "partial text here")

    records = _read_jsonl(history)
    assert len(records) == 1
    rec = records[0]
    assert rec["role"] == "abort"
    assert rec["reason"] == "user_preempt"
    assert rec["partial_chars"] == len("partial text here")
    assert rec["partial_preview"].startswith("partial text here")
    assert "ts" in rec


def test_record_abort_forensic_truncates_preview_to_200_chars(tmp_path):
    daemon = Daemon.__new__(Daemon)
    history = tmp_path / "C1" / "history.jsonl"
    conv = Conversation(history_depth=10, history_path=history)
    long_text = "x" * 500

    daemon._record_abort_forensic(conv, "length", long_text)

    records = _read_jsonl(history)
    assert records[0]["partial_chars"] == 500
    assert len(records[0]["partial_preview"]) == 200


def test_record_abort_forensic_noop_when_no_history_path(tmp_path):
    """If conv has no history_path, the helper does nothing (does not raise)."""
    daemon = Daemon.__new__(Daemon)
    conv = Conversation(history_depth=10, history_path=None)
    daemon._record_abort_forensic(conv, "user_preempt", "partial")
```

(These tests cover the forensic helper in isolation. The full `_handle_chat` exercise runs in Task 13's integration test.)

- [ ] **Step 2: Run the tests to verify they fail**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest tests/test_daemon_cancellation.py::test_record_abort_forensic_writes_role_abort_line tests/test_daemon_cancellation.py::test_record_abort_forensic_truncates_preview_to_200_chars tests/test_daemon_cancellation.py::test_record_abort_forensic_noop_when_no_history_path -v
```

Expected: FAIL with `AttributeError: 'Daemon' object has no attribute '_record_abort_forensic'`.

- [ ] **Step 3: Add the forensic helper to `Daemon`**

In `pal/daemon.py`, add the helper method (place near the existing `_handle_chat` or with other helpers):

```python
def _record_abort_forensic(self, conv, reason: str, partial: str) -> None:
    """Append a non-message forensic line to the JSONL history.

    Does NOT add to the in-memory conversation window. The role is set to
    "abort", which is filtered out by `ChannelStore._replay_into`. Useful
    for post-mortem when a turn ends abnormally.
    """
    if conv.history_path is None:
        return
    record = {
        "role": "abort",
        "reason": reason,
        "partial_chars": len(partial),
        "partial_preview": partial[:200],
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    try:
        conv.history_path.parent.mkdir(parents=True, exist_ok=True)
        with conv.history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("failed to write abort forensic for %s: %s", reason, exc)
```

Make sure `datetime` and `timezone` are imported at the top of the file (`from datetime import datetime, timezone`); add the imports if missing.

- [ ] **Step 4: Update `_handle_chat` to consume `StreamEnd`, handle `CancelledError`, and emit the log**

Find `_handle_chat` (around line 415). The current chat stream loop (around 478-487) looks like:

```python
async for item in self.inference.stream(
    messages, tools=TOOL_DEFINITIONS, reasoning=mode,
):
    if isinstance(item, list):
        tool_calls = item
        break
    else:
        chunk = StreamChunkMessage(token=item)
        writer.write(encode_message(chunk))
        await writer.drain()
        full_response.append(item)
```

Wrap the entire turn (the body of `_handle_chat`) in a try/except/finally that:

1. Initializes timing and counters at entry.
2. Catches `asyncio.CancelledError` separately (mark `terminated_reason="user_preempt"`, write forensic, send `[stopped]`).
3. Catches `Exception` (mark `terminated_reason="error"`, write forensic, send `ErrorMessage`).
4. Always emits the `chat_turn_ended` log in `finally`.

Add `StreamEnd` import at the top of `pal/daemon.py`:

```python
from agent_core.inference import StreamEnd
```

Update `_handle_chat`'s top:

```python
async def _handle_chat(self, msg, conv, channel_id, writer, tool_executor, scanner):
    import time
    start = time.monotonic()
    chunk_count = 0
    finish_reason = "unknown"
    terminated_reason = "complete"
    full_response: list[str] = []
    tool_calls = None
    try:
        # ... existing pre-stream setup (build messages, resolve mode, etc.) ...

        async for item in self.inference.stream(
            messages,
            tools=TOOL_DEFINITIONS,
            reasoning=mode,
            max_tokens=self.config.max_response_tokens,
        ):
            if isinstance(item, StreamEnd):
                finish_reason = item.finish_reason
                if finish_reason == "length":
                    terminated_reason = "length"
                break
            if isinstance(item, list):
                tool_calls = item
                terminated_reason = "tool_call"
                break
            # str token
            chunk_count += 1
            chunk = StreamChunkMessage(token=item)
            writer.write(encode_message(chunk))
            await writer.drain()
            full_response.append(item)

        # ... existing post-stream logic for the tool_calls branch (unchanged) ...

        if tool_calls is None:
            if terminated_reason == "complete":
                full_text = "".join(full_response)
                conv.add_assistant(full_text)
                response = ResponseMessage(text=full_text, command="chat")
                writer.write(encode_message(response))
                await writer.drain()
            elif terminated_reason == "length":
                # Drop the partial; write forensic; tell the client.
                self._record_abort_forensic(conv, "length", "".join(full_response))
                response = ResponseMessage(
                    text="[response truncated by max_tokens]", command="chat",
                )
                writer.write(encode_message(response))
                await writer.drain()

        # ... existing tool-call handling continues unchanged when tool_calls is not None ...

    except asyncio.CancelledError:
        terminated_reason = "user_preempt"
        self._record_abort_forensic(conv, "user_preempt", "".join(full_response))
        try:
            stopped = ResponseMessage(text="[stopped]", command="chat")
            writer.write(encode_message(stopped))
            await writer.drain()
        except Exception:
            pass  # writer may be closed
        # Do NOT re-raise: the caller (preemption helper) treats clean unwind as success.
    except Exception as exc:
        terminated_reason = "error"
        self._record_abort_forensic(conv, "error", "".join(full_response))
        logger.exception("chat turn failed: %s", exc)
        try:
            err = ErrorMessage(error=str(exc))
            writer.write(encode_message(err))
            await writer.drain()
        except Exception:
            pass
    finally:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "chat_turn_ended",
            extra={
                "channel_id": channel_id,
                "agent_name": "pal",
                "duration_ms": duration_ms,
                "chunk_count": chunk_count,
                "terminated_reason": terminated_reason,
                "finish_reason": finish_reason,
                "max_tokens_cap": self.config.max_response_tokens,
                "model": getattr(self.config, "inference_model", "unknown"),
                "reasoning_mode": getattr(msg, "reasoning_mode", None) or "auto",
            },
        )
```

Adapt the splice points to fit the existing `_handle_chat` body. The pre-stream setup (building messages, resolving the reasoning mode) and the tool-call branch's body (executing tools, looping back to inference, etc.) stay as-is; only the stream loop, the post-loop response-emission, and the wrapping try/except/finally are new.

- [ ] **Step 5: Run the focused tests**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest tests/test_daemon_cancellation.py -v
```

Expected: all six new tests pass (three preempt-helper + three forensic).

- [ ] **Step 6: Run a broader regression slice**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest tests/test_daemon_cancellation.py tests/test_daemon_channels.py tests/test_daemon_scanner_hook.py tests/test_daemon_scanner_approval.py tests/test_channels.py tests/test_config.py -v
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add pal/daemon.py tests/test_daemon_cancellation.py
git commit -m "feat(daemon): instrument _handle_chat with StreamEnd handling, abort forensics, structured log"
```

---

## Task 13: Integration test for safety behavior

**Files:**
- Create: `/home/edible/Projects/PAL/.worktrees/inference-safety-guards/tests/test_inference_safety_integration.py`

End-to-end exercise of the abort paths against an in-process fake SSE server.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_inference_safety_integration.py`:

```python
"""Integration tests for the inference-safety guards: max_tokens truncation,
preemption mid-stream, and disconnect handling.
"""
import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import patch

import httpx

from agent_core.inference import StreamEnd
from pal.conversation import Conversation


def _make_sse_response(chunks: list[dict]) -> bytes:
    body = []
    for c in chunks:
        body.append(f"data: {json.dumps(c)}")
    body.append("data: [DONE]")
    return "\n".join(body).encode("utf-8")


class _FakeInfiniteStreamInference:
    """Stream that yields tokens forever until cancelled."""
    async def stream(self, *args, **kwargs):
        i = 0
        while True:
            await asyncio.sleep(0.02)
            yield f"tok{i} "
            i += 1


class _FakeLengthCappedInference:
    """Stream that yields N tokens then a StreamEnd with finish_reason=length."""
    def __init__(self, n: int):
        self.n = n
    async def stream(self, *args, **kwargs):
        for i in range(self.n):
            yield f"t{i} "
        yield StreamEnd(finish_reason="length", chunks_yielded=self.n)


@pytest.mark.asyncio
async def test_max_tokens_truncation_drops_partial_writes_forensic(tmp_path):
    """When the stream yields finish_reason='length', the partial is dropped from
    in-memory history, a forensic line is written, and the client receives a
    truncation marker.
    """
    from pal.daemon import Daemon

    history = tmp_path / "C1" / "history.jsonl"
    conv = Conversation(history_depth=10, history_path=history)

    daemon = Daemon.__new__(Daemon)
    daemon._chat_tasks = {}
    daemon.config = type("Cfg", (), {"max_response_tokens": 5, "inference_model": "test"})()
    daemon.inference = _FakeLengthCappedInference(n=3)
    # Stub other dependencies _handle_chat needs (tool_executor, scanner) with no-op fakes.
    # Adapt to the actual _handle_chat signature; provide minimal stubs that pass argument-isinstance checks.
    # ...
    # (The exact stubs depend on Daemon's internals; see existing test_daemon_channels.py
    # for the established pattern of building a partial Daemon for unit tests.)

    # Run a chat turn and observe the conversation + forensic file.
    # ...

    # Assertions:
    assert len(conv.messages) == 0  # partial dropped
    forensics = [json.loads(l) for l in history.read_text().splitlines() if l.strip()]
    abort_records = [r for r in forensics if r.get("role") == "abort"]
    assert len(abort_records) == 1
    assert abort_records[0]["reason"] == "length"


@pytest.mark.asyncio
async def test_preemption_cancels_in_flight_stream(tmp_path):
    """A second chat message on the same channel cancels the first within ~2s."""
    from pal.daemon import Daemon

    daemon = Daemon.__new__(Daemon)
    daemon._chat_tasks = {}
    # Inject the infinite-stream fake inference.
    daemon.inference = _FakeInfiniteStreamInference()

    # Start the first turn.
    # ...
    # (Build a minimal _handle_chat invocation; track that it's in-flight.)

    # Trigger preemption.
    await daemon._preempt_existing_turn("C1")

    # Verify the first task was cancelled within the helper's 2s budget.
    # ...


@pytest.mark.asyncio
async def test_chat_turn_ended_log_emitted_for_each_terminated_reason(caplog, tmp_path):
    """For each abort path, exactly one chat_turn_ended log is emitted with the
    expected terminated_reason."""
    from pal.daemon import Daemon

    # Run three controlled turns: complete, length, user_preempt. After each,
    # filter caplog for the chat_turn_ended record and assert its
    # terminated_reason field.
    # ...
```

These tests are scaffolds; the executing engineer will need to wire them to the actual `_handle_chat` invocation pattern used in PAL's other tests. The key is the assertions at the end.

(If wiring the full integration is too involved for an integration-style harness, downgrade to unit-level tests that exercise `_handle_chat` directly with a mocked `self.inference`. The assertions remain the same.)

- [ ] **Step 2: Iterate until tests pass**

Build out the test stubs with the smallest viable harness. Look at `tests/test_daemon_channels.py` and `tests/test_daemon_scanner_hook.py` for the established pattern of building a partial `Daemon` for unit/integration tests.

```bash
/home/edible/Projects/PAL/.venv/bin/pytest tests/test_inference_safety_integration.py -v
```

Iterate until all three tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_inference_safety_integration.py
git commit -m "test: integration coverage for inference safety guards"
```

---

## Task 14: Run full PAL suite and fix old test assertions

**Files:**
- Modify: any test file that asserts the old "previous turn" rejection error.

The pre-flight inventory noted that `tests/test_daemon.py` (already flaky-skipped) and possibly `tests/test_integration.py` may assert the old behavior. They need an update to reflect that messages now preempt instead of being rejected.

- [ ] **Step 1: Search for the old error string**

Use the Grep tool (per memory `feedback_use_grep_tool`) to search for:

```
A previous turn is still being processed
```

Across the PAL repo. Report file:line for each match.

- [ ] **Step 2: For each match, update the test**

For each test that asserts this error, replace the assertion with one that exercises the new preemption behavior. The new expectation is: sending a second chat message in a channel does NOT produce an error; instead, the first task is cancelled and the second runs.

If the test cannot be reasonably updated without significant rewriting, mark it skipped with a clear comment pointing at this plan, and add a TODO to revisit in a follow-up. (Acceptable only if the test is already in the flaky-skipped list.)

- [ ] **Step 3: Run the full PAL test suite**

```bash
/home/edible/Projects/PAL/.venv/bin/pytest \
  --ignore=tests/test_daemon.py \
  --ignore=tests/test_integration.py \
  --ignore=tests/test_chat_research_integration.py \
  --ignore=tests/test_consolidate_integration.py \
  --ignore=tests/test_learning_e2e.py \
  -v
```

Expected: all green.

- [ ] **Step 4: Commit any test updates**

```bash
git add tests/
git commit -m "test: update assertions for new preemption behavior"
```

---

## Task 15: Push PAL branch, open PR, and merge

- [ ] **Step 1: Push the feature branch**

```bash
cd /home/edible/Projects/PAL/.worktrees/inference-safety-guards
git push -u origin feature/inference-safety-guards
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --title "Inference safety: max_tokens cap + preemption + structured logging" --body "$(cat <<'EOF'
## Summary
- Bumps agent_core dependency to v0.3.1 (adds max_tokens parameter and StreamEnd sentinel).
- Adds `BaseConfig.max_response_tokens` (default 4096; `PAL_MAX_RESPONSE_TOKENS` env override).
- Replaces per-connection "previous turn is still being processed" rejection with per-channel preemption: a new chat or command on a channel cancels any in-flight turn.
- `_handle_chat` consumes the new StreamEnd sentinel, catches `asyncio.CancelledError`, writes role="abort" forensic JSONL records on abort, and emits a structured `chat_turn_ended` log per turn.
- Channels replay skips JSONL records whose role is not user/assistant/tool, so forensic records don't pollute model context on daemon restart.

Spec: `docs/superpowers/specs/2026-04-28-inference-safety-guards-design.md`.

Triggered by the 2026-04-28 incident where the model fell into an 18-minute "I'll just call the tool" narration loop and PAL stayed unresponsive after the stream ended.

## Test plan
- [x] PAL test suite green (with 5 known-flaky integration tests excluded per project memory).
- [ ] Server-side smoke: see Task 16 in the plan.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Wait for CI**

```bash
gh pr checks
```

Wait until green.

- [ ] **Step 4: Merge from the parent (non-worktree) checkout**

```bash
cd /home/edible/Projects/PAL  # NOT the worktree
PR_NUM=$(gh pr view feature/inference-safety-guards --json number --jq .number)
gh api -X PUT repos/EdibleTuber/PAL/pulls/$PR_NUM/merge --field merge_method=merge
```

- [ ] **Step 5: Update local main**

```bash
cd /home/edible/Projects/PAL
git checkout main
git pull
```

- [ ] **Step 6: Clean up the worktree**

```bash
git worktree remove .worktrees/inference-safety-guards
git branch -d feature/inference-safety-guards  # optional; branch is merged
```

---

# Part 3: Cleanup

## Task 16: Server-side smoke test runbook

This task is run by the user on the inference server (192.168.1.14). The agent does not SSH; provide the exact commands.

- [ ] **Step 1: Hand the user the runbook**

Provide this text to the user verbatim, asking them to run it on the server:

```
Server-side inference safety smoke (you run these on 192.168.1.14):

1. Stop the PAL daemon:
   systemctl --user stop pal-daemon

2. cd /mnt/secondary/PAL

3. git fetch origin && git checkout main && git pull
   # confirms the safety-fix merge is present

4. Reinstall to pull agent_core 0.3.1:
   .venv/bin/pip install -e .

5. (Optional) override the cap to a smaller value for the smoke test:
   export PAL_MAX_RESPONSE_TOKENS=512
   # Smaller cap makes truncation easier to trigger during the test.

6. Restart the daemon:
   systemctl --user start pal-daemon

7. Tail the logs:
   journalctl --user -u pal-daemon -f

Smoke checks (from your CLI session against the server):

  Test 1: normal chat completes cleanly.
    - Send a short chat message ("what's 2+2?"). Verify reply arrives.
    - Confirm one chat_turn_ended log with terminated_reason="complete".

  Test 2: max_tokens truncation.
    - With PAL_MAX_RESPONSE_TOKENS=512 (or whatever low cap), ask a question
      that produces a long answer ("explain how transformers work, in detail").
    - Verify the reply ends with "[response truncated by max_tokens]".
    - Confirm one chat_turn_ended log with terminated_reason="length".

  Test 3: preemption.
    - Start a chat that takes a few seconds. Mid-stream, send a second message.
    - Verify the first task is cancelled (no further tokens for it).
    - Verify the second message is processed and replied to.
    - Confirm two chat_turn_ended logs: one with terminated_reason="user_preempt"
      for the first turn and one with "complete" (or whatever) for the second.

  Test 4: forensic JSONL on abort.
    - cat /mnt/secondary/PAL/vault/_channels/<channel_id>/history.jsonl | tail -20
    - Verify role="abort" records exist with reasons matching the tests above.

  Test 5: replay across restart.
    - systemctl --user restart pal-daemon
    - Send a normal chat. Verify reply.
    - Confirm the channel's history replayed cleanly (no spurious "abort" turns
      surfaced as user/assistant messages).

  Test 6: unset the cap override after testing.
    - unset PAL_MAX_RESPONSE_TOKENS
    - systemctl --user restart pal-daemon
    - Verify defaults are back (4096).

If anything fails: the changes are isolated to PAL main + agent_core 0.3.1.
Roll back PAL with: git checkout HEAD~1 (pre-merge); restart daemon.
Roll back agent_core: pin v0.3.0 in PAL's pyproject.toml; reinstall.
```

- [ ] **Step 2: Wait for user confirmation**

User reports back. If anything fails: diagnose, fix, ship a follow-up.

---

## Task 17: Sync Phase D plan and resume Phase D

**Files:**
- Modify: `/home/edible/Projects/PAL/docs/superpowers/plans/2026-04-28-agent-core-extraction-phase-d.md` (Task 4: ChannelStore migration)
- Modify (optional): `/home/edible/Projects/PAL/docs/superpowers/specs/2026-04-28-phase-d-per-channel-state-design.md`

The replay-skip change made in Task 10 of THIS plan must be carried into Phase D's `agent_core/agent_core/channels.py` port. Update Phase D's plan to include it.

- [ ] **Step 1: Update Phase D's Task 4 source code**

In `docs/superpowers/plans/2026-04-28-agent-core-extraction-phase-d.md`, find the section "Task 4: Move ChannelStore into agent_core" and the source code listed for `agent_core/agent_core/channels.py`. Update the `_replay_into` method to include the role-filter check (matching the version produced in this plan's Task 10):

```python
for lineno, line in enumerate(raw.splitlines(), 1):
    line = line.strip()
    if not line:
        continue
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        logger.warning(
            "channel %s history.jsonl line %d malformed, skipping",
            history_path.parent.name, lineno,
        )
        continue
    if message.get("role") not in ("user", "assistant", "tool"):
        continue
    conv._messages.append(message)
conv._truncate()
```

- [ ] **Step 2: Add a test for the replay-skip in Phase D's Task 4**

In Phase D's plan, in the test step for Task 4, add the test from this plan's Task 10 (`test_replay_skips_non_message_roles`) so it carries forward in the agent_core port.

- [ ] **Step 3: Commit the Phase D plan update**

```bash
cd /home/edible/Projects/PAL
git add docs/superpowers/plans/2026-04-28-agent-core-extraction-phase-d.md
git commit -m "docs: sync Phase D plan with channels.py replay-skip change"
```

- [ ] **Step 4: Rebase the Phase D agent_core branch onto new agent_core main**

```bash
cd /home/edible/Projects/agent_core
git checkout feature/phase-d-per-channel-state
git fetch origin
git rebase origin/main
```

Expected: clean rebase. The two existing commits (`7fa4501` protocol, `9c97224` Conversation) should apply cleanly on top of v0.3.1.

If conflicts arise (unlikely for these small Phase D commits), resolve them and continue.

- [ ] **Step 5: Verify the Phase D tests still pass on the rebased branch**

```bash
cd /home/edible/Projects/agent_core
.venv/bin/pytest -x
```

Expected: all green.

---

## Task 18: Update memory and close out

- [ ] **Step 1: Update `project_phase2_inference_investigation.md`**

Annotate the memory at `/home/edible/.claude/projects/-home-edible-Projects-PAL/memory/project_phase2_inference_investigation.md` to mark the Phase 1 fix as shipped. Add a note at the top:

```markdown
**Phase 1 status:** Shipped 2026-04-29 (PAL main, agent_core v0.3.1). Structured `chat_turn_ended` logs are now being collected. Phase 2 work resumes after all 8 agent_core extraction phases complete.
```

- [ ] **Step 2: Update `project_agent_core_extraction.md`**

Annotate the memory at `/home/edible/.claude/projects/-home-edible-Projects-PAL/memory/project_agent_core_extraction.md` with a one-line note that agent_core is now at v0.3.1 (with max_tokens + StreamEnd) before Phase D resumes:

```markdown
**Note (2026-04-29):** Inference safety guards shipped as agent_core v0.3.1 (separate from Phase D). Phase D feature branch rebased onto new main. Phase D's plan was updated to include the `_replay_into` role-filter change.
```

- [ ] **Step 3: Final summary to the user**

Report Phase 1 inference safety guards complete: PRs merged on agent_core (v0.3.1) and PAL, server smoke tests passed, Phase D ready to resume from where it was paused (Tasks 1-3 done on the feature branch, Task 4 next).

---

## Notes for the executing agent

- **Use the Grep tool, not bash grep.** Per memory `feedback_use_grep_tool`.
- **Never `git add -A` or `git add .` in the PAL repo.** Per memory `feedback_git_add_explicit`. Stage files by explicit path.
- **The 5 known-flaky tests** stay excluded in broad runs (per project memory). They have pre-existing infrastructure issues unrelated to this work.
- **Worktree cleanup**: after PAL PR merge, remove `.worktrees/inference-safety-guards`.
- **No SSH from agent**: server-side smoke (Task 16) is the user's job. The agent provides the runbook only.
- **No em dashes** in user-facing output. Per memory `feedback_no_em_dashes`.
- **Phase D is paused but not stale**: feature branch `feature/phase-d-per-channel-state` exists with two commits. After this fix ships, resume Phase D from its plan at `docs/superpowers/plans/2026-04-28-agent-core-extraction-phase-d.md` Task 4.
