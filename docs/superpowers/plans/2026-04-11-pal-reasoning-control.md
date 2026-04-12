# PAL Reasoning Control and Model Switching — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-conversation control over reasoning mode and model selection, starting with Gemma 4's `enable_thinking` toggle.

**Architecture:** New `pal/reasoning.py` module owns model-family dispatch and reasoning extraction. `InferenceClient` grows optional `model` and `reasoning` params that flow through `reasoning.shape_request()`. `Conversation` tracks per-conversation overrides. Two new slash commands (`/model`, `/think`) set the overrides. CLI renders reasoning as dim text.

**Tech Stack:** Python 3.12, pytest, pytest-asyncio, httpx, Rich (for CLI rendering)

**Spec:** `docs/superpowers/specs/2026-04-11-pal-reasoning-control-design.md`

---

## File Map

| File | Responsibility | Change |
|---|---|---|
| `pal/reasoning.py` | Model family lookup, `shape_request`, `extract_reasoning`, `decide_mode` | **Create** |
| `tests/test_reasoning.py` | Unit tests for the reasoning module | **Create** |
| `pal/conversation.py` | Per-conversation state: `model_override`, `reasoning_override` | Modify |
| `tests/test_conversation.py` | Tests for new conversation fields | Modify |
| `pal/inference.py` | `default_model` rename, optional `model`/`reasoning` params, wire reasoning module | Modify |
| `tests/test_inference.py` | Tests for reasoning pass-through on complete/stream | Modify |
| `tests/conftest.py` | Mock server gains `reasoning_content` response variant | Modify |
| `pal/daemon.py` | `/model` and `/think` command handlers, chat path wiring, toggle event logging, status update | Modify |
| `pal/protocol.py` | `ResponseMessage` gains optional `reasoning` field | Modify |
| `pal/cli.py` | Dim text rendering for reasoning blocks, display pref state | Modify |

---

## Pre-implementation: History stripping verification

Before writing any code, run this curl test against the live inference server to determine whether llama.cpp auto-strips historical `reasoning_content` from replayed messages. This determines whether `pal/conversation.py` needs a defensive guard.

```bash
curl -s http://192.168.1.14:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemma-4-26b-a4b-it-q4_k_m",
    "messages": [
      {"role": "user", "content": "Say hello"},
      {"role": "assistant", "content": "Hello!", "reasoning_content": "The user wants a greeting. I should say hello."},
      {"role": "user", "content": "Now say goodbye"}
    ],
    "max_tokens": 256
  }'
```

**If the response is normal** (model responds with goodbye, no errors): llama.cpp handles it. No guard needed in `conversation.py`. Skip the optional guard steps in Task 2.

**If the response is degraded or errors**: Add the strip guard in Task 2 (the optional steps are marked below).

Document the result in a comment at the top of the PR description.

---

## Task 1: Create `pal/reasoning.py` with tests

**Files:**
- Create: `pal/reasoning.py`
- Create: `tests/test_reasoning.py`

- [ ] **Step 1: Write the test file**

```python
# tests/test_reasoning.py
"""Tests for the reasoning module."""
from pal.conversation import Conversation
from pal.reasoning import shape_request, extract_reasoning, decide_mode, _identify_family


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
    conv = Conversation(history_depth=10)
    conv.reasoning_override = "on"
    assert decide_mode(conv) == "on"


def test_decide_mode_override_off():
    conv = Conversation(history_depth=10)
    conv.reasoning_override = "off"
    assert decide_mode(conv) == "off"


def test_decide_mode_no_override():
    conv = Conversation(history_depth=10)
    assert decide_mode(conv) == "off"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/edible/Projects/PAL && .venv/bin/python -m pytest tests/test_reasoning.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pal.reasoning'`

- [ ] **Step 3: Create `pal/reasoning.py`**

```python
# pal/reasoning.py
"""Reasoning model control — per-request toggle and response extraction.

Maps model names to families and dispatches reasoning control per family.
Today: Gemma family uses chat_template_kwargs.enable_thinking.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pal.conversation import Conversation

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


def decide_mode(conversation: Conversation) -> Literal["on", "off"]:
    if conversation.reasoning_override in ("on", "off"):
        return conversation.reasoning_override
    return "off"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/edible/Projects/PAL && .venv/bin/python -m pytest tests/test_reasoning.py -v`
Expected: All 14 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pal/reasoning.py tests/test_reasoning.py
git commit -m "feat: add reasoning module with model family dispatch"
```

---

## Task 2: Add `model_override` and `reasoning_override` to `Conversation`

**Files:**
- Modify: `pal/conversation.py:1-13`
- Modify: `tests/test_conversation.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_conversation.py`:

```python
def test_conversation_overrides_default_none():
    conv = Conversation(history_depth=10)
    assert conv.model_override is None
    assert conv.reasoning_override is None


def test_conversation_overrides_settable():
    conv = Conversation(history_depth=10)
    conv.model_override = "gemma-4-26b-a4b-it-q4_k_m"
    conv.reasoning_override = "on"
    assert conv.model_override == "gemma-4-26b-a4b-it-q4_k_m"
    assert conv.reasoning_override == "on"


def test_conversation_overrides_clearable():
    conv = Conversation(history_depth=10)
    conv.model_override = "gemma-4-26b"
    conv.reasoning_override = "on"
    conv.model_override = None
    conv.reasoning_override = None
    assert conv.model_override is None
    assert conv.reasoning_override is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/edible/Projects/PAL && .venv/bin/python -m pytest tests/test_conversation.py::test_conversation_overrides_default_none -v`
Expected: FAIL — `AttributeError: 'Conversation' object has no attribute 'model_override'`

- [ ] **Step 3: Add the fields to `Conversation`**

In `pal/conversation.py`, change the import and dataclass definition:

```python
"""In-memory conversation history management.

Maintains a rolling window of messages, truncated to history_depth.
No persistence — memorable content goes into the wiki or learning system,
not a chat log.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Conversation:
    history_depth: int
    _messages: list[dict] = field(default_factory=list)
    model_override: str | None = None
    reasoning_override: Literal["on", "off"] | None = None
```

The rest of the file stays unchanged.

- [ ] **Step 4: Run all conversation tests**

Run: `cd /home/edible/Projects/PAL && .venv/bin/python -m pytest tests/test_conversation.py -v`
Expected: All tests PASS (existing + 3 new)

- [ ] **Step 5: Commit**

```bash
git add pal/conversation.py tests/test_conversation.py
git commit -m "feat: add model and reasoning overrides to Conversation"
```

---

## Task 3: Extend `InferenceClient` with reasoning support

**Files:**
- Modify: `pal/inference.py:24-31, 37-43, 93-127, 129-198`
- Modify: `tests/conftest.py:19-134` (mock server)
- Modify: `tests/test_inference.py`

- [ ] **Step 1: Update `CompletionResult` and rename `model` to `default_model`**

In `pal/inference.py`, update the imports and data classes:

```python
"""HTTP client for the inference server's OpenAI-compatible API.

Supports both streaming (SSE) and non-streaming completions via
POST /v1/chat/completions. Tool-aware: can pass tool definitions
and parse tool-call responses.
"""
import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

import httpx

from pal.reasoning import shape_request, extract_reasoning

logger = logging.getLogger(__name__)

_MAX_RETRIES = 5
_INITIAL_BACKOFF = 2.0
_MAX_BACKOFF = 30.0


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class CompletionResult:
    type: str  # "text" or "tool_calls"
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    reasoning: str | None = None


class InferenceClient:
    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_model = model
        self._client = httpx.AsyncClient(timeout=600.0)
```

- [ ] **Step 2: Update `complete()` to accept `model` and `reasoning` params**

Replace the `complete` method in `pal/inference.py`:

```python
    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        reasoning: Literal["on", "off"] | None = None,
    ) -> CompletionResult:
        resolved_model = model or self.default_model
        payload: dict = {"model": resolved_model, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if reasoning is not None:
            shape_request(payload, resolved_model, reasoning)
            if reasoning == "on" and "chat_template_kwargs" not in payload:
                logger.debug("reasoning control requested but no-op for model %s", resolved_model)

        resp = await self._post_with_retry(payload)
        data = resp.json()
        message = data["choices"][0]["message"]

        raw_calls = message.get("tool_calls")
        if raw_calls:
            parsed = []
            for tc in raw_calls:
                func = tc["function"]
                args = func["arguments"]
                if isinstance(args, str):
                    args = json.loads(args)
                parsed.append(ToolCall(
                    id=tc["id"],
                    name=func["name"],
                    arguments=args,
                ))
            return CompletionResult(type="tool_calls", tool_calls=parsed)

        reasoning_text = extract_reasoning(data)
        return CompletionResult(
            type="text",
            content=message.get("content", ""),
            reasoning=reasoning_text,
        )
```

- [ ] **Step 3: Update `stream()` to accept `model` and `reasoning` params**

Replace the `stream` method in `pal/inference.py`:

```python
    async def stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        reasoning: Literal["on", "off"] | None = None,
    ) -> AsyncGenerator[str | list[ToolCall], None]:
        resolved_model = model or self.default_model
        payload: dict = {"model": resolved_model, "messages": messages, "stream": True}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if reasoning is not None:
            shape_request(payload, resolved_model, reasoning)
            if reasoning == "on" and "chat_template_kwargs" not in payload:
                logger.debug("reasoning control requested but no-op for model %s", resolved_model)

        tool_call_acc: dict[int, dict] = {}
        is_tool_response = False
        url = f"{self.base_url}/v1/chat/completions"

        async with self._stream_with_retry(url, payload) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                delta = chunk["choices"][0].get("delta", {})

                tc_deltas = delta.get("tool_calls")
                if tc_deltas is not None:
                    is_tool_response = True
                    for tcd in tc_deltas:
                        idx = tcd.get("index", 0)
                        if idx not in tool_call_acc:
                            tool_call_acc[idx] = {
                                "id": tcd.get("id", ""),
                                "name": "",
                                "arguments_str": "",
                            }
                        acc = tool_call_acc[idx]
                        if tcd.get("id"):
                            acc["id"] = tcd["id"]
                        func = tcd.get("function", {})
                        if func.get("name"):
                            acc["name"] = func["name"]
                        if func.get("arguments"):
                            acc["arguments_str"] += func["arguments"]
                    continue

                content = delta.get("content")
                if content is not None:
                    yield content

        if is_tool_response and tool_call_acc:
            calls = []
            for idx in sorted(tool_call_acc):
                acc = tool_call_acc[idx]
                args = json.loads(acc["arguments_str"]) if acc["arguments_str"] else {}
                calls.append(ToolCall(
                    id=acc["id"],
                    name=acc["name"],
                    arguments=args,
                ))
            yield calls
```

- [ ] **Step 4: Update the mock server to support `reasoning_content` responses**

In `tests/conftest.py`, add a new trigger in `mock_chat_completions`. After the `TOOLCALL:` block and before the default text response (around line 116), add:

```python
    # If message starts with REASON:, return a response with reasoning_content
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
        # Streaming doesn't support reasoning_content chunking in this mock
        # (real llama.cpp handles this internally), so fall through to normal text
```

- [ ] **Step 5: Write new inference tests**

Append to `tests/test_inference.py`:

```python
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

- [ ] **Step 6: Run all inference tests**

Run: `cd /home/edible/Projects/PAL && .venv/bin/python -m pytest tests/test_inference.py -v`
Expected: All tests PASS (existing + 4 new)

- [ ] **Step 7: Fix any references to `self.inference.model`**

The rename from `model` to `default_model` will break `pal/daemon.py:327` (the `/status` handler). Update it:

In `pal/daemon.py`, change line 327:
```python
                    f"Model: {self.inference.model}\n"
```
to:
```python
                    f"Model: {self.inference.default_model}\n"
```

Also check `pal/categorizer.py` — it holds a reference to `InferenceClient`. Grep for `.model` on it:

Run: `grep -n '\.model' pal/categorizer.py`

Fix any `.model` references the same way. (The categorizer likely doesn't reference `.model` directly since it calls `.complete()`, but verify.)

- [ ] **Step 8: Run full test suite to catch breakage**

Run: `cd /home/edible/Projects/PAL && .venv/bin/python -m pytest -x -q`
Expected: All tests PASS

- [ ] **Step 9: Commit**

```bash
git add pal/inference.py pal/daemon.py tests/conftest.py tests/test_inference.py
git commit -m "feat: extend InferenceClient with reasoning and model override support"
```

---

## Task 4: Add `/think` command to the daemon

**Files:**
- Modify: `pal/daemon.py:284-371` (the `_handle_command` method)

- [ ] **Step 1: Add the `/think` handler**

In `pal/daemon.py`, add the import at the top of the file (near line 15):

```python
from pal.reasoning import decide_mode
```

In `_handle_command`, before the final `else:` block (line 368), add a new branch:

```python
        elif msg.name == "think":
            await self._handle_think(msg.args, conv, writer)
```

Then add the handler method on the `Daemon` class (after the last existing handler):

```python
    async def _handle_think(
        self,
        args: str,
        conv: Conversation,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle /think — control reasoning mode for this conversation."""
        arg = args.strip().lower()
        if arg == "on":
            conv.reasoning_override = "on"
            logger.info(
                "reasoning_toggle conversation_id=%s action=on last_user_message=%.200s",
                id(conv),
                conv.messages[-1]["content"] if conv.messages else "",
            )
            resp = ResponseMessage(text="Reasoning: on", command="think")
        elif arg == "off":
            conv.reasoning_override = "off"
            logger.info(
                "reasoning_toggle conversation_id=%s action=off last_user_message=%.200s",
                id(conv),
                conv.messages[-1]["content"] if conv.messages else "",
            )
            resp = ResponseMessage(text="Reasoning: off", command="think")
        elif arg == "auto":
            conv.reasoning_override = None
            logger.info(
                "reasoning_toggle conversation_id=%s action=auto last_user_message=%.200s",
                id(conv),
                conv.messages[-1]["content"] if conv.messages else "",
            )
            resp = ResponseMessage(text="Reasoning: auto (off by default)", command="think")
        elif arg in ("show", "hide"):
            resp = ResponseMessage(text=f"Reasoning display: {arg}", command="think")
        elif arg == "":
            mode = decide_mode(conv)
            resp = ResponseMessage(
                text=f"Reasoning mode: {conv.reasoning_override or 'auto'} (effective: {mode})",
                command="think",
            )
        else:
            resp = ResponseMessage(
                text="Usage: /think [on|off|auto|show|hide]",
                command="think",
            )
        writer.write(encode_message(resp))
        await writer.drain()
```

- [ ] **Step 2: Update the `/help` text**

In the `/help` handler (around line 291), add two new lines to the help string:

```python
                    "  /model [name]  — Show or switch the active model\n"
                    "  /think [mode]  — Control reasoning (on/off/auto/show/hide)\n"
```

- [ ] **Step 3: Run existing daemon tests to verify nothing breaks**

Run: `cd /home/edible/Projects/PAL && .venv/bin/python -m pytest tests/test_daemon.py -v`
Expected: All existing tests PASS

- [ ] **Step 4: Commit**

```bash
git add pal/daemon.py
git commit -m "feat: add /think command for per-conversation reasoning control"
```

---

## Task 5: Add `/model` command to the daemon

**Files:**
- Modify: `pal/daemon.py`

- [ ] **Step 1: Add the `/model` handler**

In `_handle_command`, before the `think` branch, add:

```python
        elif msg.name == "model":
            await self._handle_model(msg.args, conv, writer)
```

Then add the handler method:

```python
    async def _handle_model(
        self,
        args: str,
        conv: Conversation,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle /model — show or switch the active model."""
        arg = args.strip()

        if arg == "":
            current = conv.model_override or self.inference.default_model
            source = "override" if conv.model_override else "default"
            resp = ResponseMessage(
                text=f"Model: {current} ({source})",
                command="model",
            )
        elif arg == "list":
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.get(f"{self.inference.base_url}/v1/models")
                    r.raise_for_status()
                data = r.json()
                names = [m["id"] for m in data.get("data", [])]
                if names:
                    lines = ["Available models:"]
                    for i, name in enumerate(names, 1):
                        marker = " (active)" if name == (conv.model_override or self.inference.default_model) else ""
                        lines.append(f"  {i}. {name}{marker}")
                    resp = ResponseMessage(text="\n".join(lines), command="model")
                else:
                    resp = ResponseMessage(text="No models available.", command="model")
            except Exception as exc:
                logger.warning("Failed to list models: %s", exc)
                error = ErrorMessage(error=f"Could not reach inference server: {exc}")
                writer.write(encode_message(error))
                await writer.drain()
                return
        elif arg == "default":
            conv.model_override = None
            resp = ResponseMessage(
                text=f"Model reset to default: {self.inference.default_model}",
                command="model",
            )
        else:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.get(f"{self.inference.base_url}/v1/models")
                    r.raise_for_status()
                data = r.json()
                names = [m["id"] for m in data.get("data", [])]
            except Exception as exc:
                logger.warning("Failed to validate model: %s", exc)
                error = ErrorMessage(error=f"Could not reach inference server: {exc}")
                writer.write(encode_message(error))
                await writer.drain()
                return

            if arg not in names:
                error = ErrorMessage(
                    error=f"Model not found: {arg}. Use /model list to see available models.",
                )
                writer.write(encode_message(error))
                await writer.drain()
                return

            conv.model_override = arg
            resp = ResponseMessage(
                text=f"Model set to: {arg}",
                command="model",
            )

        writer.write(encode_message(resp))
        await writer.drain()
```

- [ ] **Step 2: Add `import httpx` to daemon.py**

At the top of `pal/daemon.py`, add `import httpx` to the imports (near line 6).

- [ ] **Step 3: Update `/status` to show per-conversation overrides**

Replace the `/status` handler (around line 323) with:

```python
        elif msg.name == "status":
            articles = self.wiki.list_articles()
            active_model = conv.model_override or self.inference.default_model
            model_source = "override" if conv.model_override else "default"
            reasoning_mode = decide_mode(conv)
            reasoning_label = conv.reasoning_override or "auto"
            resp = ResponseMessage(
                text=(
                    f"Model: {active_model} ({model_source})\n"
                    f"Default model: {self.inference.default_model}\n"
                    f"Reasoning: {reasoning_label} (effective: {reasoning_mode})\n"
                    f"Server: {self.inference.base_url}\n"
                    f"Vault: {self.wiki.vault_path} ({len(articles)} articles)\n"
                    f"Collection: {self.retrieval.collection_id}"
                ),
                command="status",
            )
            writer.write(encode_message(resp))
            await writer.drain()
```

- [ ] **Step 4: Run daemon tests**

Run: `cd /home/edible/Projects/PAL && .venv/bin/python -m pytest tests/test_daemon.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add pal/daemon.py
git commit -m "feat: add /model command for per-conversation model switching"
```

---

## Task 6: Wire reasoning into the chat path

**Files:**
- Modify: `pal/daemon.py:191-282` (the `_handle_chat` method)

- [ ] **Step 1: Update `_handle_chat` to resolve model and reasoning per turn**

At the top of `_handle_chat` (after `conv.add_user(msg.text)`, line 206), add model/reasoning resolution:

```python
        model = conv.model_override or self.inference.default_model
        mode = decide_mode(conv)
```

- [ ] **Step 2: Pass `model` and `reasoning` to stream() and complete()**

Update the streaming call (line 214):

```python
            async for item in self.inference.stream(messages, tools=TOOL_DEFINITIONS, model=model, reasoning=mode):
```

Update the tool-loop completion call (line 259):

```python
                completion = await self.inference.complete(messages, tools=TOOL_DEFINITIONS, model=model, reasoning=mode)
```

- [ ] **Step 3: Log reasoning on non-streaming responses in the tool loop**

After the `if completion.type == "text":` block (around line 261), add reasoning logging:

```python
                if completion.type == "text":
                    response_text = completion.content or ""
                    if completion.reasoning:
                        logger.debug("reasoning_content: %.500s", completion.reasoning)
                    conv.add_assistant(response_text)
```

- [ ] **Step 4: Ensure internal daemon operations use `reasoning="off"`**

Search for all `self.inference.complete(` calls outside of `_handle_chat`. Each one needs `reasoning="off"`. The key ones:

`_handle_note` (around line 440):
```python
            result = await self.inference.complete(messages, reasoning="off")
```

`_handle_learn` (around line 1141):
```python
            completion = await self.inference.complete(api_messages, reasoning="off")
```

`_handle_summarize` and `_handle_compile` — find each `self.inference.complete(` and add `reasoning="off"`.

Run: `grep -n 'self.inference.complete(' pal/daemon.py`

Update every call outside `_handle_chat` to include `reasoning="off"`.

- [ ] **Step 5: Run full test suite**

Run: `cd /home/edible/Projects/PAL && .venv/bin/python -m pytest -x -q`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add pal/daemon.py
git commit -m "feat: wire reasoning toggle into chat path and suppress for internal ops"
```

---

## Task 7: Add `reasoning` field to the protocol and update CLI rendering

**Files:**
- Modify: `pal/protocol.py:37-41`
- Modify: `pal/daemon.py` (send reasoning in ResponseMessage)
- Modify: `pal/cli.py:95-175`

- [ ] **Step 1: Add `reasoning` field to `ResponseMessage`**

In `pal/protocol.py`, update `ResponseMessage`:

```python
@dataclass
class ResponseMessage:
    text: str
    command: str = ""
    reasoning: str = ""
    type: str = "response"
```

- [ ] **Step 2: Update daemon to pass reasoning through ResponseMessage**

In `_handle_chat`, at the point where the daemon sends the final `ResponseMessage` after streaming (around line 228), the streaming path doesn't have `reasoning` yet (streaming `reasoning_content` extraction would require non-trivial changes to the SSE parser). For v1, only the tool-loop non-streaming path carries reasoning.

For the tool-loop path (around line 262):

```python
                if completion.type == "text":
                    response_text = completion.content or ""
                    if completion.reasoning:
                        logger.debug("reasoning_content: %.500s", completion.reasoning)
                    conv.add_assistant(response_text)
                    done = ResponseMessage(
                        text=response_text,
                        reasoning=completion.reasoning or "",
                    )
                    writer.write(encode_message(done))
                    await writer.drain()
                    return
```

- [ ] **Step 3: Update CLI to render reasoning as dim text**

In `pal/cli.py`, update the chat message rendering section. In the `run_repl` function, after the streaming loop finishes and before `console.print()` at the end (around line 161):

Replace the `elif isinstance(msg, ResponseMessage):` block in the streaming handler:

```python
                    elif isinstance(msg, ResponseMessage):
                        if msg.reasoning:
                            reasoning_lines = msg.reasoning.splitlines()
                            if len(reasoning_lines) > 20:
                                reasoning_lines = reasoning_lines[:20]
                                reasoning_lines.append("... (full reasoning in debug log)")
                            console.print(Text("\n".join(reasoning_lines), style="dim italic"))
                            console.print()
                        if not accumulated and msg.text:
                            console.print(Markdown(msg.text))
                        break
```

Also handle reasoning in the command response path (around line 138):

```python
                try:
                    resp = await _run_command(client, cmd_name, cmd_args, console)
                    if resp.reasoning:
                        reasoning_lines = resp.reasoning.splitlines()
                        if len(reasoning_lines) > 20:
                            reasoning_lines = reasoning_lines[:20]
                            reasoning_lines.append("... (full reasoning in debug log)")
                        console.print(Text("\n".join(reasoning_lines), style="dim italic"))
                        console.print()
                    console.print(f"\n{resp.text}\n")
```

- [ ] **Step 4: Add display preference tracking to CLI**

Add a module-level variable near the top of `pal/cli.py` (after the imports):

```python
_reasoning_display: str = "show"
```

In the command handler section (around line 128), intercept `/think show` and `/think hide` client-side before sending to daemon:

```python
            if text.startswith("/"):
                parts = text[1:].split(None, 1)
                cmd_name = parts[0]
                cmd_args = parts[1] if len(parts) > 1 else ""

                if cmd_name in ("quit", "exit"):
                    break

                # Handle display prefs client-side
                if cmd_name == "think" and cmd_args.strip() in ("show", "hide"):
                    global _reasoning_display
                    _reasoning_display = cmd_args.strip()
                    console.print(f"\nReasoning display: {_reasoning_display}\n")
                    continue
```

Then wrap the reasoning rendering with a display check:

```python
                        if msg.reasoning and _reasoning_display == "show":
```

(Apply this in both the streaming and command paths where reasoning is rendered.)

- [ ] **Step 5: Update protocol tests**

Run: `cd /home/edible/Projects/PAL && .venv/bin/python -m pytest tests/test_protocol.py -v`

If any tests construct `ResponseMessage` without the new `reasoning` field, they should still pass because it defaults to `""`. Verify.

- [ ] **Step 6: Run full test suite**

Run: `cd /home/edible/Projects/PAL && .venv/bin/python -m pytest -x -q`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add pal/protocol.py pal/daemon.py pal/cli.py
git commit -m "feat: render reasoning blocks in CLI with dim text and display toggle"
```

---

## Task 8: Handle Discord `/think show|hide` gracefully

**Files:**
- Modify: `pal/daemon.py` (the `/think` handler)

- [ ] **Step 1: Update `/think show|hide` response for non-CLI clients**

The daemon can't know which client is connected, so it always responds. The CLI intercepts `show`/`hide` client-side (added in Task 7). For Discord and other clients, the daemon's response is informational.

Update the `show`/`hide` branch in `_handle_think` to be explicit:

```python
        elif arg in ("show", "hide"):
            resp = ResponseMessage(
                text=f"Reasoning display: {arg} (CLI only — Discord reasoning display is not yet available)",
                command="think",
            )
```

- [ ] **Step 2: Run tests**

Run: `cd /home/edible/Projects/PAL && .venv/bin/python -m pytest tests/test_daemon.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add pal/daemon.py
git commit -m "feat: graceful /think show|hide message for non-CLI clients"
```

---

## Task 9: Final verification and cleanup

**Files:**
- All modified files

- [ ] **Step 1: Run the full test suite**

Run: `cd /home/edible/Projects/PAL && .venv/bin/python -m pytest -v`
Expected: All tests PASS

- [ ] **Step 2: Verify no stale `.model` references remain**

Run: `cd /home/edible/Projects/PAL && grep -rn '\.model[^_s]' pal/ --include='*.py' | grep -v 'default_model' | grep -v 'model_override' | grep -v '__pycache__' | grep -v '# '`

Any hits that reference `self.inference.model` or `InferenceClient(...).model` need to be updated to `default_model`. Expected: no hits (only `model_name`, `model_path`, `_MODEL_FAMILIES`, etc. which are unrelated).

- [ ] **Step 3: Verify daemon internal ops all pass `reasoning="off"`**

Run: `cd /home/edible/Projects/PAL && grep -n 'self.inference.complete(' pal/daemon.py`

Every call outside `_handle_chat` must have `reasoning="off"`. List them and verify.

- [ ] **Step 4: Run the existing test suite one final time**

Run: `cd /home/edible/Projects/PAL && .venv/bin/python -m pytest -x -q`
Expected: All tests PASS, no regressions

- [ ] **Step 5: Commit any cleanup**

If any fixes were needed:
```bash
git add -u
git commit -m "fix: cleanup stale model references and ensure reasoning=off on internal ops"
```
