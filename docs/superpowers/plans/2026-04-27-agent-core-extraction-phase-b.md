# agent_core Extraction Phase B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move four stateless-client modules (`reasoning`, `inference`, `retrieval`, `websearch`) from PAL into `agent_core`, tag `agent_core@v0.2.0`, and migrate PAL to consume the new tag. After merge, PAL ships with all 9 of agent_core's modules in active use.

**Architecture:** Same two-repo split as Phase A. agent_core is the library (private GitHub repo, git+tag pinning); PAL is the consumer. Phase B's modules are already constructor-injected (no env reads internally), so the migration is byte-identical for `inference`/`retrieval`/`websearch`. `reasoning.py` gets a small `Protocol` fix to remove its `pal.conversation` import. PAL's tests for these modules move alongside their code into agent_core, with two minor adaptations: `test_reasoning.py` swaps `pal.conversation.Conversation` for a local stub class, and `test_inference.py` inlines its own minimal tool definitions instead of pulling `pal.tools.TOOL_DEFINITIONS`.

**Tech Stack:** Python 3.12+, hatchling, pytest, GitHub Actions CI, git tags. No new agent_core runtime or dev deps; httpx, uvicorn, starlette already declared from Phase A. PAL adds nothing new beyond the version bump.

**Repos involved:**
- agent_core: `/home/edible/Projects/agent_core` (existing from Phase A; currently at `v0.1.1`)
- PAL: `/home/edible/Projects/PAL` (main checkout). PAL-side migration work happens in a feature-branch worktree at `/home/edible/Projects/PAL/.worktrees/agent-core-phase-b`.

**Reference:** spec at `docs/superpowers/specs/2026-04-27-phase-b-stateless-clients-design.md`. Builds on Phase A's plan at `docs/superpowers/plans/2026-04-25-agent-core-extraction-phase-a.md` (merged 2026-04-26 in PR #1).

**Pre-flight: PAL caller graph (mapped during planning, source of truth for the Step-1 grep in each migration task):**

| Module | PAL source callers | PAL test callers |
|---|---|---|
| `reasoning` | `pal/inference.py` (moves with this phase), `pal/daemon.py` | `tests/test_reasoning.py` (deleted) |
| `inference` | `pal/backfill_main.py`, `pal/pdf_structure.py`, `pal/categorizer.py`, `pal/learning_scanner.py`, `pal/daemon.py` (3 import sites including 2 local) | `tests/test_import.py` (2 sites), `tests/test_strict_note.py`, `tests/test_learning_scanner.py`, `tests/test_pdf_structure.py`, `tests/test_learning_commands.py`, `tests/test_compile.py`, `tests/test_batch_inference.py` (3 sites including 2 string-form `monkeypatch.setattr`), `tests/test_categorizer.py`, `tests/test_inference.py` (deleted), `tests/test_summarize.py` |
| `retrieval` | `pal/tools.py`, `pal/daemon.py` | `tests/test_retrieval.py` (deleted) |
| `websearch` | `pal/tools.py` (local import inside function), `pal/daemon.py` | `tests/test_chat_research_tools.py`, `tests/test_websearch.py` (deleted), `tests/test_researcher.py` |

---

## Task 1: Move `reasoning.py` into agent_core

**Files:**
- Create: `/home/edible/Projects/agent_core/agent_core/reasoning.py`
- Create: `/home/edible/Projects/agent_core/tests/test_reasoning.py`

**Working directory:** `/home/edible/Projects/agent_core`. Use `.venv/bin/pytest`.

- [ ] **Step 1: Pre-flight, confirm fresh main**

```bash
cd /home/edible/Projects/agent_core
git fetch origin
git checkout main
git pull
git status
```

Expected: clean working tree on `main`, HEAD matches origin. The latest commit should be the README v0.1.1 fix from Phase A's merge tail (commit `306730d` or whatever has been pushed since).

- [ ] **Step 2: Create the migrated `reasoning.py`**

Write `/home/edible/Projects/agent_core/agent_core/reasoning.py` (NOT a copy; the `pal.conversation` import is replaced with a local Protocol):

```python
# agent_core/reasoning.py
"""Reasoning model control -- per-request toggle and response extraction.

Maps model names to families and dispatches reasoning control per family.
Today: Gemma family uses chat_template_kwargs.enable_thinking.
"""
from __future__ import annotations

from typing import Literal, Protocol


class _ConversationLike(Protocol):
    """Duck-typed contract for what `decide_mode` reads from its argument.

    Any object with an optional `reasoning_override` attribute set to
    "on", "off", or None will satisfy this. Concrete agents (e.g. PAL's
    Conversation class) match without explicit subclassing.
    """
    reasoning_override: Literal["on", "off"] | None


_MODEL_FAMILIES: dict[str, str] = {
    "gemma-4": "gemma",
    "gemma-3": "gemma",
    "qwen3":   "qwen3",
}


def _identify_family(model: str) -> str | None:
    for prefix, family in _MODEL_FAMILIES.items():
        if model.startswith(prefix):
            return family
    return None


def shape_request(body: dict, model: str, mode: Literal["on", "off"]) -> dict:
    body = dict(body)
    if "chat_template_kwargs" in body:
        body["chat_template_kwargs"] = dict(body["chat_template_kwargs"])
    match _identify_family(model):
        case "gemma":
            body.setdefault("chat_template_kwargs", {})["enable_thinking"] = (mode == "on")
        case "qwen3":
            pass
        case None:
            pass
    return body


def extract_reasoning(response: dict) -> str | None:
    msg = response["choices"][0]["message"]
    return msg.get("reasoning_content") or None


def decide_mode(conversation: _ConversationLike) -> Literal["on", "off"]:
    override = getattr(conversation, "reasoning_override", None)
    if override in ("on", "off"):
        return override
    return "off"
```

- [ ] **Step 3: Verify the only behavioral diff vs PAL is the Protocol replacement**

```bash
diff /home/edible/Projects/PAL/pal/reasoning.py /home/edible/Projects/agent_core/agent_core/reasoning.py
```

Expected diff: only the import block (`TYPE_CHECKING`/`if TYPE_CHECKING: from pal.conversation import Conversation` replaced by `Protocol` import + `_ConversationLike` class), and the type annotation on `decide_mode` (`Conversation` → `_ConversationLike`). No body changes to `_identify_family`, `shape_request`, `extract_reasoning`, or the body of `decide_mode`.

- [ ] **Step 4: Create the migrated test file**

PAL's `tests/test_reasoning.py` imports `from pal.conversation import Conversation`. The migrated test uses a minimal stub class instead.

Write `/home/edible/Projects/agent_core/tests/test_reasoning.py`:

```python
# tests/test_reasoning.py
"""Tests for the reasoning module."""
from dataclasses import dataclass
from typing import Literal

from agent_core.reasoning import shape_request, extract_reasoning, decide_mode, _identify_family


@dataclass
class _StubConversation:
    """Minimal stand-in for an agent's Conversation type.

    Matches the duck-typed _ConversationLike Protocol that decide_mode reads.
    """
    reasoning_override: Literal["on", "off"] | None = None


def test_identify_family_gemma4():
    assert _identify_family("gemma-4-26b-a4b-it-q4_k_m") == "gemma"


def test_identify_family_gemma3():
    assert _identify_family("gemma-3-27b-it-q4_k_m") == "gemma"


def test_identify_family_qwen3():
    assert _identify_family("qwen3-35b-a3b-q4_k_m") == "qwen3"


def test_identify_family_unknown():
    assert _identify_family("llama-3.1-8b") is None


def test_shape_request_gemma_on():
    body = {"model": "gemma-4-26b", "messages": []}
    result = shape_request(body, "gemma-4-26b-a4b-it-q4_k_m", "on")
    assert result["chat_template_kwargs"]["enable_thinking"] is True


def test_shape_request_gemma_off():
    body = {"model": "gemma-4-26b", "messages": []}
    result = shape_request(body, "gemma-4-26b-a4b-it-q4_k_m", "off")
    assert result["chat_template_kwargs"]["enable_thinking"] is False


def test_shape_request_preserves_existing_kwargs():
    body = {"model": "gemma-4-26b", "messages": [], "chat_template_kwargs": {"other": 42}}
    result = shape_request(body, "gemma-4-26b-a4b-it-q4_k_m", "on")
    assert result["chat_template_kwargs"]["other"] == 42
    assert result["chat_template_kwargs"]["enable_thinking"] is True


def test_shape_request_unknown_model_noop():
    body = {"model": "llama-3.1-8b", "messages": []}
    original = dict(body)
    result = shape_request(body, "llama-3.1-8b", "on")
    assert result == original


def test_shape_request_qwen3_noop_for_now():
    body = {"model": "qwen3-35b", "messages": []}
    original = dict(body)
    result = shape_request(body, "qwen3-35b-a3b-q4_k_m", "on")
    assert result == original


def test_extract_reasoning_present():
    response = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "The answer is 42.",
                "reasoning_content": "Let me think about this step by step...",
            }
        }]
    }
    assert extract_reasoning(response) == "Let me think about this step by step..."


def test_extract_reasoning_absent():
    response = {
        "choices": [{
            "message": {"role": "assistant", "content": "hello"}
        }]
    }
    assert extract_reasoning(response) is None


def test_extract_reasoning_empty_string():
    response = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "hello",
                "reasoning_content": "",
            }
        }]
    }
    assert extract_reasoning(response) is None


def test_decide_mode_override_on():
    conv = _StubConversation(reasoning_override="on")
    assert decide_mode(conv) == "on"


def test_decide_mode_override_off():
    conv = _StubConversation(reasoning_override="off")
    assert decide_mode(conv) == "off"


def test_decide_mode_no_override():
    conv = _StubConversation()
    assert decide_mode(conv) == "off"


def test_shape_request_does_not_mutate_input():
    body = {"model": "gemma-4-26b", "messages": []}
    shape_request(body, "gemma-4-26b-a4b-it-q4_k_m", "on")
    assert "chat_template_kwargs" not in body
```

The `_StubConversation` dataclass replaces the PAL `Conversation` import. Behavior of every test is preserved (the underlying stub satisfies the same `reasoning_override` attribute access pattern).

- [ ] **Step 5: Run the tests**

```bash
cd /home/edible/Projects/agent_core
.venv/bin/pytest tests/test_reasoning.py -v
.venv/bin/pytest -v 2>&1 | tail -5
```

Expected: 16 reasoning tests pass. Full suite: 62 prior + 16 new = 78 passing, zero failures.

- [ ] **Step 6: Commit**

```bash
cd /home/edible/Projects/agent_core
git add agent_core/reasoning.py tests/test_reasoning.py
git status
```

Verify only those two files staged. Then:

```bash
git commit -m "$(cat <<'EOF'
feat: add reasoning module

Per-model reasoning control (gemma chat_template_kwargs, qwen3 noop today)
plus extract_reasoning helper. Migrated from PAL with one design change:
the pal.conversation import becomes a local Protocol describing the
duck-typed contract decide_mode actually relies on. Behavior unchanged.
EOF
)"
```

Do NOT push or tag. Tasks 2-4 follow before the v0.2.0 tag in Task 5.

---

## Task 2: Move `inference.py` into agent_core

**Files:**
- Modify: `/home/edible/Projects/agent_core/tests/conftest.py` (add chat completions + models routes + REQUEST_LOG)
- Create: `/home/edible/Projects/agent_core/agent_core/inference.py`
- Create: `/home/edible/Projects/agent_core/tests/test_inference.py`

- [ ] **Step 1: Read the current agent_core conftest**

Use the Read tool on `/home/edible/Projects/agent_core/tests/conftest.py`. Confirm it currently has the page-related routes from Phase A (`/page.html`, `/too-large`, `/binary`, `/missing`, `/redirect`, `/no-content-type`, `/page-with-code.html`) and the `mock_inference_server` fixture. Note the existing imports and `Starlette` `Route` list location, you'll be adding to both.

- [ ] **Step 2: Extend conftest with chat completions, models route, and REQUEST_LOG**

Use the Edit tool on `/home/edible/Projects/agent_core/tests/conftest.py`.

First, add a module-level `REQUEST_LOG` and an autouse fixture to clear it between tests. After the existing imports, add:

```python
# Captures every /v1/chat/completions body sent to the mock server.
# Tests that care about what model/payload hit the wire read from this list.
# Cleared automatically before each test by the autouse _clear_request_log fixture.
REQUEST_LOG: list[dict] = []
```

Add the chat completions route handler (drop in alongside the existing `mock_page_*` handlers, before the `Starlette` app construction). The handler matches PAL's behavior so the migrated `test_inference.py` works without changes:

```python
async def mock_chat_completions(request: Request):
    """Mock OpenAI-compatible /v1/chat/completions endpoint."""
    body = await request.json()
    REQUEST_LOG.append(body)
    stream = body.get("stream", False)
    messages = body.get("messages", [])
    last_user = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"),
        "",
    )

    has_tool_result = any(m.get("role") == "tool" for m in messages)
    if has_tool_result:
        tool_content = next(
            (m["content"] for m in messages if m.get("role") == "tool"), ""
        )
        summary = tool_content[:50] if tool_content else "no content"
        if not stream:
            return JSONResponse({
                "choices": [{"message": {"role": "assistant", "content": f"Tool result: {summary}"}}]
            })
        async def generate_after_tool():
            text = f"Tool result: {summary}"
            tokens = text.split(" ")
            for i, token in enumerate(tokens):
                prefix = "" if i == 0 else " "
                chunk = {
                    "choices": [{
                        "delta": {"content": prefix + token},
                        "finish_reason": None,
                    }]
                }
                yield f"data: {json.dumps(chunk)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(generate_after_tool(), media_type="text/event-stream")

    tools = body.get("tools", [])
    if tools and last_user.startswith("TOOLCALL:"):
        tool_name = last_user.split(":", 1)[1].strip()
        if not stream:
            return JSONResponse({
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "call_001",
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": '{"path": "Research/quantum.md"}',
                            },
                        }],
                    },
                    "finish_reason": "tool_calls",
                }]
            })
        async def generate_tool():
            chunk = {
                "choices": [{
                    "delta": {
                        "tool_calls": [{
                            "index": 0,
                            "id": "call_001",
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": '{"path": "Res',
                            },
                        }]
                    },
                    "finish_reason": None,
                }]
            }
            yield f"data: {json.dumps(chunk)}\n\n"
            chunk2 = {
                "choices": [{
                    "delta": {
                        "tool_calls": [{
                            "index": 0,
                            "function": {
                                "arguments": 'earch/quantum.md"}',
                            },
                        }]
                    },
                    "finish_reason": None,
                }]
            }
            yield f"data: {json.dumps(chunk2)}\n\n"
            done_chunk = {
                "choices": [{"delta": {}, "finish_reason": "tool_calls"}]
            }
            yield f"data: {json.dumps(done_chunk)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(generate_tool(), media_type="text/event-stream")

    if last_user.startswith("REASON:"):
        actual_query = last_user.split(":", 1)[1].strip()
        if not stream:
            return JSONResponse({
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": f"answer: {actual_query}",
                        "reasoning_content": f"thinking about {actual_query}",
                    },
                    "finish_reason": "stop",
                }]
            })

    if not stream:
        return JSONResponse({
            "choices": [{"message": {"role": "assistant", "content": f"echo: {last_user}"}}]
        })

    async def generate():
        tokens = [t for t in f"echo: {last_user}".split(" ") if t]
        for i, token in enumerate(tokens):
            prefix = "" if i == 0 else " "
            chunk = {
                "choices": [{
                    "delta": {"content": prefix + token},
                    "finish_reason": None,
                }]
            }
            yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


async def mock_list_models(request: Request):
    """Mock /v1/models endpoint."""
    return JSONResponse({
        "object": "list",
        "data": [
            {"id": "test-model", "object": "model", "created": 0, "owned_by": "local"},
            {"id": "gemma-4-26b-a4b-it-q4_k_m", "object": "model", "created": 0, "owned_by": "local"},
        ],
    })
```

Add the route entries to the existing `Starlette` `routes=[...]` list (just append; order doesn't matter):

```python
    Route("/v1/chat/completions", mock_chat_completions, methods=["POST"]),
    Route("/v1/models", mock_list_models, methods=["GET"]),
```

Add the autouse fixture for clearing `REQUEST_LOG` (place near the bottom alongside other fixtures):

```python
@pytest.fixture(autouse=True)
def _clear_request_log():
    """Clear REQUEST_LOG before each test to prevent cross-test leakage."""
    REQUEST_LOG.clear()
    yield
```

If `StreamingResponse` is not yet imported in conftest, add it to the existing `from starlette.responses import ...` line. Same for `JSONResponse` if missing.

- [ ] **Step 3: Verify Phase A's existing tests still pass with the extended conftest**

```bash
cd /home/edible/Projects/agent_core
.venv/bin/pytest -v 2>&1 | tail -10
```

Expected: 78 passed (62 from Phase A + 16 reasoning tests from Task 1). The added routes and fixture do not break anything since they only fire when accessed. If anything fails, the conftest extension introduced a regression; investigate before proceeding.

- [ ] **Step 4: Create the migrated `inference.py`**

Copy the source from PAL with one import-line rewrite:

```bash
cp /home/edible/Projects/PAL/pal/inference.py /home/edible/Projects/agent_core/agent_core/inference.py
```

Then use the Edit tool on `/home/edible/Projects/agent_core/agent_core/inference.py`:

Old:
```python
from pal.reasoning import shape_request, extract_reasoning
```

New:
```python
from agent_core.reasoning import shape_request, extract_reasoning
```

Verify byte-equivalence ignoring that one line:

```bash
diff /home/edible/Projects/PAL/pal/inference.py /home/edible/Projects/agent_core/agent_core/inference.py
```

Expected: exactly one line changed (the import).

- [ ] **Step 5: Create the migrated test file**

PAL's `tests/test_inference.py` imports `from pal.tools import TOOL_DEFINITIONS`. `pal.tools` is staying in PAL (Phase D scope). The migrated test inlines a minimal tool definition.

Write `/home/edible/Projects/agent_core/tests/test_inference.py`:

```python
"""Tests for the inference server HTTP client."""
import pytest

from agent_core.inference import InferenceClient, CompletionResult, ToolCall


# Minimal tool definitions for tests. Mirrors the shape that PAL's full
# TOOL_DEFINITIONS uses but only includes one entry, since the inference
# tests only verify dispatch behavior, not which tools exist.
_TEST_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the vault.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
]


@pytest.mark.asyncio
async def test_complete_non_streaming(mock_inference_server):
    client = InferenceClient(base_url=mock_inference_server, model="test-model")
    result = await client.complete(
        messages=[{"role": "user", "content": "hello world"}],
    )
    assert result.type == "text"
    assert result.content == "echo: hello world"


@pytest.mark.asyncio
async def test_complete_streaming(mock_inference_server):
    client = InferenceClient(base_url=mock_inference_server, model="test-model")
    tokens = []
    async for token in client.stream(
        messages=[{"role": "user", "content": "hello world"}],
    ):
        tokens.append(token)
    full = "".join(tokens)
    assert full == "echo: hello world"


@pytest.mark.asyncio
async def test_complete_streaming_empty_response(mock_inference_server):
    """Streaming an empty user message still produces output."""
    client = InferenceClient(base_url=mock_inference_server, model="test-model")
    tokens = []
    async for token in client.stream(
        messages=[{"role": "user", "content": ""}],
    ):
        tokens.append(token)
    full = "".join(tokens)
    assert full == "echo:"


@pytest.mark.asyncio
async def test_complete_returns_text_result(mock_inference_server):
    client = InferenceClient(base_url=mock_inference_server, model="test-model")
    result = await client.complete(
        messages=[{"role": "user", "content": "hello"}],
        tools=_TEST_TOOL_DEFINITIONS,
    )
    assert isinstance(result, CompletionResult)
    assert result.type == "text"
    assert result.content == "echo: hello"
    assert result.tool_calls is None


@pytest.mark.asyncio
async def test_complete_returns_tool_calls(mock_inference_server):
    client = InferenceClient(base_url=mock_inference_server, model="test-model")
    result = await client.complete(
        messages=[{"role": "user", "content": "TOOLCALL:read_file"}],
        tools=_TEST_TOOL_DEFINITIONS,
    )
    assert isinstance(result, CompletionResult)
    assert result.type == "tool_calls"
    assert result.content is None
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "read_file"
    assert result.tool_calls[0].arguments == {"path": "Research/quantum.md"}


@pytest.mark.asyncio
async def test_complete_without_tools_returns_text(mock_inference_server):
    client = InferenceClient(base_url=mock_inference_server, model="test-model")
    result = await client.complete(
        messages=[{"role": "user", "content": "hello"}],
    )
    assert isinstance(result, CompletionResult)
    assert result.type == "text"
    assert result.content == "echo: hello"


@pytest.mark.asyncio
async def test_stream_returns_text_tokens(mock_inference_server):
    client = InferenceClient(base_url=mock_inference_server, model="test-model")
    items = []
    async for item in client.stream(
        messages=[{"role": "user", "content": "hello"}],
        tools=_TEST_TOOL_DEFINITIONS,
    ):
        items.append(item)
    assert all(isinstance(item, str) for item in items)
    assert "".join(items) == "echo: hello"


@pytest.mark.asyncio
async def test_stream_detects_tool_calls(mock_inference_server):
    client = InferenceClient(base_url=mock_inference_server, model="test-model")
    items = []
    async for item in client.stream(
        messages=[{"role": "user", "content": "TOOLCALL:read_file"}],
        tools=_TEST_TOOL_DEFINITIONS,
    ):
        items.append(item)
    assert len(items) == 1
    assert isinstance(items[0], list)
    assert len(items[0]) == 1
    assert items[0][0].name == "read_file"
    assert items[0][0].arguments == {"path": "Research/quantum.md"}


@pytest.mark.asyncio
async def test_complete_extracts_reasoning(mock_inference_server):
    client = InferenceClient(base_url=mock_inference_server, model="test-model")
    result = await client.complete(
        messages=[{"role": "user", "content": "REASON:deep question"}],
    )
    assert result.type == "text"
    assert result.content == "answer: deep question"
    assert result.reasoning == "thinking about deep question"


@pytest.mark.asyncio
async def test_complete_no_reasoning_returns_none(mock_inference_server):
    client = InferenceClient(base_url=mock_inference_server, model="test-model")
    result = await client.complete(
        messages=[{"role": "user", "content": "hello"}],
    )
    assert result.reasoning is None


@pytest.mark.asyncio
async def test_complete_uses_override_model(mock_inference_server):
    client = InferenceClient(base_url=mock_inference_server, model="default-model")
    result = await client.complete(
        messages=[{"role": "user", "content": "hello"}],
        model="override-model",
    )
    assert result.type == "text"


@pytest.mark.asyncio
async def test_default_model_attribute(mock_inference_server):
    client = InferenceClient(base_url=mock_inference_server, model="my-model")
    assert client.default_model == "my-model"
```

- [ ] **Step 6: Run inference tests**

```bash
cd /home/edible/Projects/agent_core
.venv/bin/pytest tests/test_inference.py -v
.venv/bin/pytest -v 2>&1 | tail -5
```

Expected: 12 inference tests pass. Full suite: 78 + 12 = 90 passing, zero failures.

- [ ] **Step 7: Commit**

```bash
cd /home/edible/Projects/agent_core
git add agent_core/inference.py tests/test_inference.py tests/conftest.py
git status
```

Verify three files staged. Then:

```bash
git commit -m "$(cat <<'EOF'
feat: add inference module

OpenAI-compatible HTTP client for the inference server: streaming and
non-streaming completions, tool-call dispatch, batch-mode error
distinction. Migrated from PAL with one import rewrite (pal.reasoning
to agent_core.reasoning); otherwise byte-identical. Test conftest
extended with /v1/chat/completions and /v1/models routes plus a
REQUEST_LOG capture for payload assertions.
EOF
)"
```

Do NOT push or tag.

---

## Task 3: Move `retrieval.py` into agent_core

**Files:**
- Modify: `/home/edible/Projects/agent_core/tests/conftest.py` (add collection routes)
- Create: `/home/edible/Projects/agent_core/agent_core/retrieval.py`
- Create: `/home/edible/Projects/agent_core/tests/test_retrieval.py`

- [ ] **Step 1: Extend conftest with collection routes**

Use the Edit tool on `/home/edible/Projects/agent_core/tests/conftest.py`. Add two route handlers alongside the existing mock handlers:

```python
async def mock_collection_search(request: Request):
    """Mock POST /collections/{collection_id}/search endpoint."""
    body = await request.json()
    query = body.get("query", "")
    limit = body.get("limit", 5)
    results = [
        {
            "id": f"doc-{i}",
            "name": f"Document {i}",
            "collection": request.path_params["collection_id"],
            "summary": f"Summary for {query} result {i}",
            "tags": ["mock"],
            "score": 0.9 - (i * 0.1),
        }
        for i in range(min(limit, 3))
    ]
    return JSONResponse({"results": results})


async def mock_collection_get_doc(request: Request):
    """Mock GET /collections/{collection_id}/docs/{doc_id} endpoint."""
    doc_id = request.path_params["doc_id"]
    collection_id = request.path_params["collection_id"]
    if doc_id == "missing":
        return JSONResponse({"error": f"Document not found: {doc_id}"}, status_code=404)
    return JSONResponse({
        "id": doc_id,
        "name": f"Name of {doc_id}",
        "collection": collection_id,
        "summary": f"Summary of {doc_id}",
        "content": f"# {doc_id}\n\nFull content of {doc_id}.\n",
        "metadata": {"tags": ["mock"]},
    })
```

Add the route entries to the `Starlette` `routes=[...]` list:

```python
    Route("/collections/{collection_id}/search", mock_collection_search, methods=["POST"]),
    Route("/collections/{collection_id}/docs/{doc_id:path}", mock_collection_get_doc, methods=["GET"]),
```

- [ ] **Step 2: Copy `retrieval.py` byte-identically**

```bash
cp /home/edible/Projects/PAL/pal/retrieval.py /home/edible/Projects/agent_core/agent_core/retrieval.py
```

Verify byte-identity:

```bash
diff /home/edible/Projects/PAL/pal/retrieval.py /home/edible/Projects/agent_core/agent_core/retrieval.py
```

Expected: zero output. `retrieval.py` has no internal `pal` imports.

- [ ] **Step 3: Copy and adapt the test file**

```bash
cp /home/edible/Projects/PAL/tests/test_retrieval.py /home/edible/Projects/agent_core/tests/test_retrieval.py
```

Use the Edit tool on `/home/edible/Projects/agent_core/tests/test_retrieval.py`:

Old:
```python
from pal.retrieval import RetrievalClient
```

New:
```python
from agent_core.retrieval import RetrievalClient
```

Verify no other `pal.retrieval` references remain:

```bash
grep -nE "pal\.retrieval|\"pal\.retrieval|'pal\.retrieval" /home/edible/Projects/agent_core/tests/test_retrieval.py
```

Expected: zero matches.

- [ ] **Step 4: Run retrieval tests**

```bash
cd /home/edible/Projects/agent_core
.venv/bin/pytest tests/test_retrieval.py -v
.venv/bin/pytest -v 2>&1 | tail -5
```

Expected: ~15 retrieval tests pass (PAL has 15 in test_retrieval.py based on the file structure). Full suite: 90 + retrieval count.

- [ ] **Step 5: Commit**

```bash
cd /home/edible/Projects/agent_core
git add agent_core/retrieval.py tests/test_retrieval.py tests/conftest.py
git status
```

Verify three files staged. Then:

```bash
git commit -m "$(cat <<'EOF'
feat: add retrieval module

Thin HTTP client for the inference server's collection search and
document fetch endpoints, plus the reindex-trigger and reindex-status
endpoints. Migrated from PAL byte-identically. Test conftest extended
with collection routes for the integration-style tests.
EOF
)"
```

Do NOT push or tag.

---

## Task 4: Move `websearch.py` into agent_core

**Files:**
- Modify: `/home/edible/Projects/agent_core/tests/conftest.py` (add searxng route)
- Create: `/home/edible/Projects/agent_core/agent_core/websearch.py`
- Create: `/home/edible/Projects/agent_core/tests/test_websearch.py`

- [ ] **Step 1: Extend conftest with the SearxNG route**

Use the Edit tool on `/home/edible/Projects/agent_core/tests/conftest.py`. Add the route handler:

```python
async def mock_searxng_search(request: Request):
    """Mock SearxNG /search endpoint.

    Returns URLs pointing back to the mock server so research-style
    tests can fetch them through the same fixture.
    """
    query = request.query_params.get("q", "")
    base = str(request.base_url).rstrip("/")
    return JSONResponse({
        "query": query,
        "results": [
            {
                "url": f"{base}/page.html?topic={query.replace(' ', '-')}&src=1",
                "title": f"{query} - Overview",
                "content": f"Overview of {query}.",
            },
            {
                "url": f"{base}/page.html?topic={query.replace(' ', '-')}&src=2",
                "title": f"{query} - Tutorial",
                "content": f"Tutorial on {query}.",
            },
            {
                "url": f"{base}/page.html?topic={query.replace(' ', '-')}&src=3",
                "title": f"{query} - Reference",
                "content": f"Reference for {query}.",
            },
            {
                "url": "https://evil.example.com/junk",
                "title": "Not allowlisted",
                "content": "Should be filtered by allowlist.",
            },
        ],
    })
```

Add the route entry:

```python
    Route("/search", mock_searxng_search, methods=["GET"]),
```

- [ ] **Step 2: Copy `websearch.py` byte-identically**

```bash
cp /home/edible/Projects/PAL/pal/websearch.py /home/edible/Projects/agent_core/agent_core/websearch.py
diff /home/edible/Projects/PAL/pal/websearch.py /home/edible/Projects/agent_core/agent_core/websearch.py
```

Expected: zero output.

- [ ] **Step 3: Copy and adapt the test file**

```bash
cp /home/edible/Projects/PAL/tests/test_websearch.py /home/edible/Projects/agent_core/tests/test_websearch.py
```

Use the Edit tool on `/home/edible/Projects/agent_core/tests/test_websearch.py`:

Old:
```python
from pal.websearch import WebSearchClient, SearchResult
```

New:
```python
from agent_core.websearch import WebSearchClient, SearchResult
```

Verify zero residual references:

```bash
grep -nE "pal\.websearch|\"pal\.websearch|'pal\.websearch" /home/edible/Projects/agent_core/tests/test_websearch.py
```

- [ ] **Step 4: Run websearch tests**

```bash
cd /home/edible/Projects/agent_core
.venv/bin/pytest tests/test_websearch.py -v
.venv/bin/pytest -v 2>&1 | tail -5
```

Expected: ~3-5 websearch tests pass (the file is short, ~30 LOC). Full suite total accumulating across all four Phase B tasks.

- [ ] **Step 5: Commit**

```bash
cd /home/edible/Projects/agent_core
git add agent_core/websearch.py tests/test_websearch.py tests/conftest.py
git status
```

Verify three files staged. Then:

```bash
git commit -m "$(cat <<'EOF'
feat: add websearch module

Thin SearxNG HTTP client for queries. Migrated from PAL byte-identically.
Test conftest extended with the /search route.
EOF
)"
```

Do NOT push or tag.

---

## Task 5: Bump version, tag v0.2.0, push

**Files:**
- Modify: `/home/edible/Projects/agent_core/pyproject.toml`

- [ ] **Step 1: Run the full agent_core suite one final time**

```bash
cd /home/edible/Projects/agent_core
.venv/bin/pytest -v 2>&1 | tail -10
```

Expected: full suite green. Cumulative count = 62 (Phase A) + ~46 (Phase B: 16 reasoning + 12 inference + ~15 retrieval + ~3 websearch) ≈ 108 passing tests, zero failures.

- [ ] **Step 2: Bump pyproject version to 0.2.0**

Use the Edit tool on `/home/edible/Projects/agent_core/pyproject.toml`:

Old:
```
version = "0.1.1"
```

New:
```
version = "0.2.0"
```

Exactly one match expected.

- [ ] **Step 3: Reinstall editable to confirm version bump propagates**

```bash
cd /home/edible/Projects/agent_core
.venv/bin/pip install -e ".[dev]" 2>&1 | tail -3
.venv/bin/pip show agent_core | grep -E "Name|Version"
```

Expected: `Name: agent_core`, `Version: 0.2.0`.

- [ ] **Step 4: Commit, tag, push**

```bash
cd /home/edible/Projects/agent_core
git add pyproject.toml
git commit -m "$(cat <<'EOF'
chore: bump version to 0.2.0

Phase B release: adds reasoning, inference, retrieval, websearch
modules alongside Phase A's utils. PAL pins this tag in its Phase B
migration.
EOF
)"

git tag v0.2.0
git push origin main
git push origin v0.2.0
```

Expected: pushes succeed. Tag visible at https://github.com/EdibleTuber/agent_core/tags.

- [ ] **Step 5: Verify CI on the tagged commit**

```bash
sleep 15
gh run list --repo EdibleTuber/agent_core --limit 3
```

Note the most recent run id and status. If `in_progress`, wait or come back. If `failure`, stop and investigate before proceeding to PAL-side work; likely a missing dep that didn't surface in the local venv.

- [ ] **Step 6: Verify v0.2.0 install works in a fresh venv**

```bash
mkdir -p /tmp/agent_core_v020_test && cd /tmp/agent_core_v020_test
python3 -m venv .venv && source .venv/bin/activate
pip install --quiet "agent_core @ git+https://github.com/EdibleTuber/agent_core.git@v0.2.0"
pip show agent_core | grep -E "Name|Version"
python -c "
from agent_core.utils.frontmatter import parse_frontmatter
from agent_core.utils.chunker import chunk_markdown
from agent_core.utils.sanitizer import sanitize
from agent_core.utils.converter import DocumentConverter
from agent_core.utils.fetcher import URLFetcher
from agent_core.reasoning import shape_request, extract_reasoning, decide_mode
from agent_core.inference import InferenceClient, CompletionResult, ToolCall
from agent_core.retrieval import RetrievalClient
from agent_core.websearch import WebSearchClient, SearchResult
print('OK')
"
deactivate
rm -rf /tmp/agent_core_v020_test
```

Expected: `Name: agent_core`, `Version: 0.2.0`, `OK`. All 9 modules import cleanly.

---

## Task 6: Set up PAL worktree and add agent_core@v0.2.0 dependency

**Files:**
- Create worktree: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-b` on new branch `feature/agent-core-extraction-phase-b`
- Modify: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-b/pyproject.toml`

- [ ] **Step 1: Create the PAL worktree on a fresh feature branch**

```bash
cd /home/edible/Projects/PAL
git fetch origin
git checkout main
git pull
git worktree add .worktrees/agent-core-phase-b -b feature/agent-core-extraction-phase-b
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
git status
```

Expected: clean worktree on the new branch, HEAD matches PAL `main`.

- [ ] **Step 2: Set up venv in the worktree**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
python3 -m venv .venv
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install -e ".[dev]" 2>&1 | tail -3
```

Expected: install succeeds. PAL is currently pinned to `agent_core@v0.1.1` from Phase A; this install pulls that version transitively.

- [ ] **Step 3: Run baseline targeted tests**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
.venv/bin/pytest tests/test_reasoning.py tests/test_inference.py tests/test_retrieval.py tests/test_websearch.py -v 2>&1 | tail -5
```

Expected: all four test files pass (PAL still has its local copies of the modules at this point). This is the "before" baseline. Note the count.

- [ ] **Step 4: Update PAL's pyproject.toml dep pin**

Use the Edit tool on `/home/edible/Projects/PAL/.worktrees/agent-core-phase-b/pyproject.toml`:

Old:
```
"agent_core @ git+https://github.com/EdibleTuber/agent_core.git@v0.1.1",
```

New:
```
"agent_core @ git+https://github.com/EdibleTuber/agent_core.git@v0.2.0",
```

Exactly one match expected.

- [ ] **Step 5: Reinstall PAL editable to fetch v0.2.0**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
.venv/bin/pip install -e ".[dev]" 2>&1 | tail -10
.venv/bin/pip show agent_core | grep -E "Name|Version"
```

Expected: pip resolves agent_core@v0.2.0 from the git tag. `pip show` reports `Version: 0.2.0`.

- [ ] **Step 6: Verify the new modules are importable from PAL's env**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
.venv/bin/python -c "
from agent_core.reasoning import shape_request
from agent_core.inference import InferenceClient
from agent_core.retrieval import RetrievalClient
from agent_core.websearch import WebSearchClient
print('OK')
"
```

Expected: `OK` printed.

- [ ] **Step 7: Run the same baseline targeted tests with the new dep**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
.venv/bin/pytest tests/test_reasoning.py tests/test_inference.py tests/test_retrieval.py tests/test_websearch.py -v 2>&1 | tail -5
```

Expected: same pass count as Step 3. PAL has not yet migrated its imports; this confirms the new dep does not break PAL's existing usage of its local `pal.reasoning`, `pal.inference`, etc.

- [ ] **Step 8: Commit on the worktree branch**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
git add pyproject.toml
git status
```

Verify only `pyproject.toml` staged. Then:

```bash
git commit -m "$(cat <<'EOF'
chore: bump agent_core dependency to v0.2.0

Adds the four Phase B modules (reasoning, inference, retrieval,
websearch) to PAL's transitive surface. PAL's own copies of these
modules are still in use; subsequent commits in this branch switch
each one over.
EOF
)"
```

Do NOT push.

---

## Task 7: Migrate PAL's `reasoning` usage to agent_core

**Files modified:** `pal/inference.py` (will move in Task 8 anyway), `pal/daemon.py`, possibly other PAL files
**Files deleted:** `pal/reasoning.py`, `tests/test_reasoning.py`

- [ ] **Step 1: Pre-flight broad grep**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
grep -rnE "pal\.reasoning|\"pal\.reasoning|'pal\.reasoning" pal/ tests/
```

Expected matches (per planning):
- `pal/inference.py:17` (this file moves in Task 8, but we still rewrite the import here for consistency)
- `pal/daemon.py:42`
- `tests/test_reasoning.py:4` (file deleted entirely)

If matches differ, adjust scope and report the difference; do not skip migration of unexpected callers.

- [ ] **Step 2: Bulk rewrite imports + string-form references**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
find pal tests -type f -name '*.py' -exec sed -i 's|from pal\.reasoning|from agent_core.reasoning|g' {} +
find pal tests -type f -name '*.py' -exec sed -i 's|import pal\.reasoning|import agent_core.reasoning|g' {} +
find pal tests -type f -name '*.py' -exec sed -i 's|"pal\.reasoning|"agent_core.reasoning|g' {} +
find pal tests -type f -name '*.py' -exec sed -i "s|'pal\.reasoning|'agent_core.reasoning|g" {} +
```

- [ ] **Step 3: Verify zero remaining `pal.reasoning` references**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
grep -rnE "pal\.reasoning" pal/ tests/
```

Expected: zero matches.

- [ ] **Step 4: Delete PAL's local reasoning module and its dedicated test**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
rm pal/reasoning.py tests/test_reasoning.py
```

- [ ] **Step 5: Verify daemon imports cleanly**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
.venv/bin/python -c "from pal.daemon import Daemon; from agent_core.reasoning import decide_mode; print('OK')"
```

Expected: `OK` printed.

- [ ] **Step 6: Run targeted PAL tests that exercise reasoning indirectly**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
.venv/bin/pytest tests/test_compile.py tests/test_summarize.py tests/test_summarizer.py tests/test_categorizer.py 2>&1 | tail -5
```

Expected: tests pass. These exercise inference (which imports reasoning) so any breakage in the reasoning rewrite surfaces here.

- [ ] **Step 7: Stage and commit**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
git add -u pal tests
git status
```

Expected staged: `pal/inference.py` (modified; will move in Task 8), `pal/daemon.py` (modified), `pal/reasoning.py` (deleted), `tests/test_reasoning.py` (deleted). Plus any other source files the grep caught.

```bash
git commit -m "$(cat <<'EOF'
refactor: migrate reasoning usage to agent_core

All callers now import from agent_core.reasoning. Deletes PAL's copy
of the module and its dedicated test (test_reasoning.py lives in
agent_core now with a stub Conversation class replacing the PAL import).
EOF
)"
```

Do NOT push.

---

## Task 8: Migrate PAL's `inference` usage to agent_core

**Files modified (per planning grep):**
- `pal/backfill_main.py`, `pal/pdf_structure.py`, `pal/categorizer.py`, `pal/learning_scanner.py`, `pal/daemon.py` (3 import sites including local imports)
- `tests/test_import.py` (2 sites), `tests/test_strict_note.py`, `tests/test_learning_scanner.py`, `tests/test_pdf_structure.py`, `tests/test_learning_commands.py`, `tests/test_compile.py`, `tests/test_batch_inference.py` (3 sites including 2 string-form `monkeypatch.setattr`), `tests/test_categorizer.py`, `tests/test_summarize.py`

**Files deleted:** `pal/inference.py`, `tests/test_inference.py`

- [ ] **Step 1: Pre-flight broad grep**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
grep -rnE "pal\.inference|\"pal\.inference|'pal\.inference" pal/ tests/
```

Confirm the matches against the planning expectations above. Pay special attention to the two `monkeypatch.setattr("pal.inference._INITIAL_BACKOFF", ...)` and similar calls in `tests/test_batch_inference.py`. Phase A surfaced exactly this pattern as the "string-form references" the simple grep can miss.

- [ ] **Step 2: Bulk rewrite imports + string-form references**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
find pal tests -type f -name '*.py' -exec sed -i 's|from pal\.inference|from agent_core.inference|g' {} +
find pal tests -type f -name '*.py' -exec sed -i 's|import pal\.inference|import agent_core.inference|g' {} +
find pal tests -type f -name '*.py' -exec sed -i 's|"pal\.inference|"agent_core.inference|g' {} +
find pal tests -type f -name '*.py' -exec sed -i "s|'pal\.inference|'agent_core.inference|g" {} +
```

- [ ] **Step 3: Verify zero remaining `pal.inference` references**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
grep -rnE "pal\.inference" pal/ tests/
```

Expected: zero matches.

- [ ] **Step 4: Delete PAL's local inference module and its dedicated test**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
rm pal/inference.py tests/test_inference.py
```

- [ ] **Step 5: Verify daemon and other inference consumers import cleanly**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
.venv/bin/python -c "
from pal.daemon import Daemon
from pal.backfill_main import main
from pal.pdf_structure import detect_chapters
from pal.categorizer import Categorizer
from pal.learning_scanner import LearningScanner
from agent_core.inference import InferenceClient
print('OK')
"
```

Expected: `OK` printed.

- [ ] **Step 6: Run targeted tests for inference consumers**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
.venv/bin/pytest tests/test_categorizer.py tests/test_pdf_structure.py tests/test_learning_scanner.py tests/test_learning_commands.py tests/test_batch_inference.py tests/test_compile.py tests/test_summarize.py tests/test_strict_note.py tests/test_import.py 2>&1 | tail -5
```

Expected: all listed test files pass. This is the broadest sanity check before commit.

- [ ] **Step 7: Stage and commit**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
git add -u pal tests
git status
```

Expected staged: 5 modified pal/ files (`backfill_main`, `pdf_structure`, `categorizer`, `learning_scanner`, `daemon`), 1 deleted (`inference.py`), several modified test files, 1 deleted (`test_inference.py`).

```bash
git commit -m "$(cat <<'EOF'
refactor: migrate inference usage to agent_core

All callers (daemon, categorizer, learning_scanner, pdf_structure,
backfill_main, plus tests) now import from agent_core.inference.
Deletes PAL's copy of the module and its dedicated test. The string-
form monkeypatch references in test_batch_inference.py rewritten to
the new path.
EOF
)"
```

Do NOT push.

---

## Task 9: Migrate PAL's `retrieval` usage to agent_core

**Files modified:** `pal/tools.py`, `pal/daemon.py`
**Files deleted:** `pal/retrieval.py`, `tests/test_retrieval.py`

- [ ] **Step 1: Pre-flight broad grep**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
grep -rnE "pal\.retrieval|\"pal\.retrieval|'pal\.retrieval" pal/ tests/
```

Expected matches:
- `pal/tools.py:14`
- `pal/daemon.py:22`
- `tests/test_retrieval.py:4` (file deleted)

- [ ] **Step 2: Bulk rewrite**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
find pal tests -type f -name '*.py' -exec sed -i 's|from pal\.retrieval|from agent_core.retrieval|g' {} +
find pal tests -type f -name '*.py' -exec sed -i 's|import pal\.retrieval|import agent_core.retrieval|g' {} +
find pal tests -type f -name '*.py' -exec sed -i 's|"pal\.retrieval|"agent_core.retrieval|g' {} +
find pal tests -type f -name '*.py' -exec sed -i "s|'pal\.retrieval|'agent_core.retrieval|g" {} +
```

- [ ] **Step 3: Verify zero remaining references**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
grep -rnE "pal\.retrieval" pal/ tests/
```

Expected: zero matches.

- [ ] **Step 4: Delete PAL's local module and test**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
rm pal/retrieval.py tests/test_retrieval.py
```

- [ ] **Step 5: Verify imports**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
.venv/bin/python -c "
from pal.tools import ToolExecutor
from pal.daemon import Daemon
from agent_core.retrieval import RetrievalClient
print('OK')
"
```

Expected: `OK` printed.

- [ ] **Step 6: Run targeted tests**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
.venv/bin/pytest tests/test_chat_research_tools.py tests/test_chat_compile_tools.py tests/test_researcher.py 2>&1 | tail -5
```

Expected: pass. These exercise tools.py (the main retrieval consumer).

- [ ] **Step 7: Commit**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
git add -u pal tests
git status
```

Expected staged: `pal/tools.py` (modified), `pal/daemon.py` (modified), `pal/retrieval.py` (deleted), `tests/test_retrieval.py` (deleted).

```bash
git commit -m "$(cat <<'EOF'
refactor: migrate retrieval usage to agent_core

tools.py and daemon.py now import from agent_core.retrieval. PAL's
copy of the module and its test deleted.
EOF
)"
```

Do NOT push.

---

## Task 10: Migrate PAL's `websearch` usage to agent_core

**Files modified:** `pal/tools.py` (local import), `pal/daemon.py`, `tests/test_chat_research_tools.py`, `tests/test_researcher.py`
**Files deleted:** `pal/websearch.py`, `tests/test_websearch.py`

- [ ] **Step 1: Pre-flight broad grep**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
grep -rnE "pal\.websearch|\"pal\.websearch|'pal\.websearch" pal/ tests/
```

Expected matches:
- `pal/tools.py:22` (local import inside a function)
- `pal/daemon.py:28`
- `tests/test_chat_research_tools.py:8`
- `tests/test_researcher.py:10`
- `tests/test_websearch.py:4` (file deleted)

- [ ] **Step 2: Bulk rewrite**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
find pal tests -type f -name '*.py' -exec sed -i 's|from pal\.websearch|from agent_core.websearch|g' {} +
find pal tests -type f -name '*.py' -exec sed -i 's|import pal\.websearch|import agent_core.websearch|g' {} +
find pal tests -type f -name '*.py' -exec sed -i 's|"pal\.websearch|"agent_core.websearch|g' {} +
find pal tests -type f -name '*.py' -exec sed -i "s|'pal\.websearch|'agent_core.websearch|g" {} +
```

- [ ] **Step 3: Verify zero remaining references**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
grep -rnE "pal\.websearch" pal/ tests/
```

Expected: zero matches.

- [ ] **Step 4: Delete PAL's local module and test**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
rm pal/websearch.py tests/test_websearch.py
```

- [ ] **Step 5: Verify imports**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
.venv/bin/python -c "
from pal.tools import ToolExecutor
from pal.daemon import Daemon
from agent_core.websearch import WebSearchClient, SearchResult
print('OK')
"
```

Expected: `OK` printed.

- [ ] **Step 6: Run targeted tests**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
.venv/bin/pytest tests/test_chat_research_tools.py tests/test_researcher.py 2>&1 | tail -5
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
git add -u pal tests
git status
```

Expected staged: `pal/tools.py` (modified), `pal/daemon.py` (modified), `tests/test_chat_research_tools.py` (modified), `tests/test_researcher.py` (modified), `pal/websearch.py` (deleted), `tests/test_websearch.py` (deleted).

```bash
git commit -m "$(cat <<'EOF'
refactor: migrate websearch usage to agent_core

tools.py, daemon.py, and the research-related test files now import
from agent_core.websearch. PAL's copy of the module and its test
deleted.
EOF
)"
```

Do NOT push.

---

## Task 11: Final smoke + clean install + open PR

**Files:** No file modifications. Verification and PR creation only.

- [ ] **Step 1: Final agent_core import probe from PAL's worktree env**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
.venv/bin/python -c "
from agent_core.utils.frontmatter import parse_frontmatter
from agent_core.utils.chunker import chunk_markdown
from agent_core.utils.sanitizer import sanitize
from agent_core.utils.converter import DocumentConverter
from agent_core.utils.fetcher import URLFetcher
from agent_core.reasoning import shape_request, decide_mode
from agent_core.inference import InferenceClient, CompletionResult
from agent_core.retrieval import RetrievalClient
from agent_core.websearch import WebSearchClient, SearchResult
from pal.daemon import Daemon
print('OK')
"
```

Expected: `OK` printed. All 9 agent_core modules and PAL's daemon import cleanly.

- [ ] **Step 2: Confirm zero residual `pal.<migrated_module>` references in tracked PAL files**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
grep -rnE "pal\.(reasoning|inference|retrieval|websearch)" pal/ tests/
```

Expected: zero matches anywhere.

- [ ] **Step 3: Run the broad PAL test suite**

The following test files are known-flaky integration tests that hang or require a live inference server, identified during Phase A. Skip them.

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
timeout 240 .venv/bin/pytest \
    --ignore=tests/test_daemon.py \
    --ignore=tests/test_integration.py \
    --ignore=tests/test_chat_research_integration.py \
    --ignore=tests/test_consolidate_integration.py \
    --ignore=tests/test_learning_e2e.py \
    -q 2>&1 | tail -20
```

Expected: all pass. The Phase A baseline was 758 passed; Phase B's deletion of 4 PAL test files (test_reasoning, test_inference, test_retrieval, test_websearch) reduces that count by approximately 16 + 12 + 15 + 5 = 48, giving ~710 expected. The exact number is fine as long as zero failures.

If anything fails, investigate. Phase A surfaced two patterns to look for first:
1. A sed missed a string-form reference somewhere (re-run Step 2 grep with broader scope).
2. A test relies on a fixture or constant that moved (less likely here, but possible with the `_INITIAL_BACKOFF`/`_MAX_BACKOFF` monkeypatches in test_batch_inference.py if the sed somehow split them).

- [ ] **Step 4: Daemon startup smoke**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b

.venv/bin/pal-daemon > /tmp/pal_daemon_phase_b_smoke.log 2>&1 &
DAEMON_PID=$!
echo "Daemon PID: $DAEMON_PID"
sleep 3

if ! kill -0 $DAEMON_PID 2>/dev/null; then
    echo "FAIL: daemon died on startup"
    cat /tmp/pal_daemon_phase_b_smoke.log
    exit 1
fi

echo "=== daemon log ==="
cat /tmp/pal_daemon_phase_b_smoke.log

SOCKET="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/pal.sock"
ls -la "$SOCKET" 2>&1

echo "/help" | timeout 5 .venv/bin/pal 2>&1 | head -30 || echo "(pal CLI may not handle piped input cleanly; not blocking)"

kill $DAEMON_PID 2>/dev/null
wait $DAEMON_PID 2>/dev/null
echo "Daemon stopped"
```

Required outcome: daemon log shows `Daemon listening on /run/user/<uid>/pal.sock` with no Python tracebacks. The `/help` step is a bonus; if it works it confirms the CLI piped input still works as it did in Phase A.

- [ ] **Step 5: Clean install probe**

```bash
mkdir -p /tmp/pal_phase_b_install_test && cd /tmp/pal_phase_b_install_test
python3 -m venv .venv && source .venv/bin/activate
pip install --quiet -e /home/edible/Projects/PAL/.worktrees/agent-core-phase-b 2>&1 | tail -3
python -c "
from agent_core.utils.frontmatter import parse_frontmatter
from agent_core.reasoning import decide_mode
from agent_core.inference import InferenceClient
from agent_core.retrieval import RetrievalClient
from agent_core.websearch import WebSearchClient
from pal.daemon import Daemon
print('OK')
"
deactivate
rm -rf /tmp/pal_phase_b_install_test
```

Expected: `OK` printed.

- [ ] **Step 6: Push the feature branch**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
git push -u origin feature/agent-core-extraction-phase-b
```

Expected: push succeeds. GitHub prints the PR-creation URL.

- [ ] **Step 7: Open the PR**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-b
gh pr create --title "Phase B: extract reasoning, inference, retrieval, websearch into agent_core" --body "$(cat <<'EOF'
## Summary

Phase B of the agent_core extraction (see `docs/superpowers/specs/2026-04-27-phase-b-stateless-clients-design.md`). This branch:

- Bumps `agent_core` pin from `v0.1.1` to `v0.2.0`
- Migrates four stateless-client modules out of PAL into `agent_core`: `reasoning`, `inference`, `retrieval`, `websearch`
- All migrations are byte-identical except `reasoning.py`, which gets a small Protocol-based fix to remove its `pal.conversation` import

After merge, PAL ships using all 9 of agent_core's modules.

## Commits

- chore: bump agent_core dependency to v0.2.0
- refactor: migrate reasoning usage to agent_core
- refactor: migrate inference usage to agent_core
- refactor: migrate retrieval usage to agent_core
- refactor: migrate websearch usage to agent_core

## Test plan

- [x] agent_core's own pytest suite passes on the v0.2.0 tag (CI run linked from agent_core release notes)
- [x] PAL targeted tests for the four migrated modules pass
- [x] PAL broad suite passes (~710 tests, excluding the 5 known-flaky integration files identified during Phase A)
- [x] PAL daemon starts cleanly with the new agent_core dep
- [x] Clean install probe in a fresh venv: all 9 agent_core modules + PAL daemon import
- [ ] Manual smoke against the inference server at 192.168.1.14: chat turn, `/think`, `/research`, `/fetch <url>`, `/summarize` (couldn't be automated)
- [ ] Discord adapter end-to-end (`pal-discord` + a real guild + slash command)

## Notes

- agent_core's pyproject `version` field was bumped to `0.2.0` in the same commit as the v0.2.0 tag, matching the lesson from Phase A's v0.1.1 reroll.
- Phase A's known-flaky `test_daemon.py` hang persists (pre-existing infrastructure issue unrelated to this work).
- No new agent_core runtime or dev dependencies needed; httpx + uvicorn + starlette were already declared.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL printed. Stop here; the user reviews the PR, runs the manual inference smoke on their server, and merges when satisfied.

---

## Phase B complete

At the end of Phase B:
- agent_core has 9 modules: 5 utilities + reasoning + inference + retrieval + websearch
- agent_core@v0.2.0 is tagged, CI green, fresh-install verified
- PAL ships on agent_core@v0.2.0 with all four module callers migrated
- PAL has 4 fewer files in `pal/` (`reasoning.py`, `inference.py`, `retrieval.py`, `websearch.py`) and 4 fewer test files
- Worktree at `.worktrees/agent-core-phase-b/` survives until the PR merges; cleanup with `git worktree remove` after.

Next phase plan (Phase C: stateful managers `wisdom`, `learning`, `learning_scanner`, `profile`, `allowlist`, `approval_registry`) gets written when this phase lands.
