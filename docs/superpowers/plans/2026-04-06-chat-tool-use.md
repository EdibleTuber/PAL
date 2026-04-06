# Chat Tool Use Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give PAL's chat mode read-only access to the vault via OpenAI-compatible function calling, so the LLM can autonomously look up files, list directories, and search content mid-conversation.

**Architecture:** Four read-only tools (`read_file`, `list_directory`, `search_content`, `search_vault`) defined in a new `pal/tools.py` module. `InferenceClient` extended to pass tool definitions and parse tool-call responses. Chat handler in the daemon gets a tool-use loop (max 10 iterations). A new `ToolProgressMessage` protocol message sends brief indicators to the CLI.

**Tech Stack:** Python 3.12, httpx, dataclasses, pytest, starlette (test fixtures)

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `pal/tools.py` | Create | Tool schemas (OpenAI format) + `ToolExecutor` class |
| `tests/test_tools.py` | Create | Unit tests for `ToolExecutor` |
| `pal/inference.py` | Modify | Accept `tools` param, return `CompletionResult` from `complete()`, detect tool calls in `stream()` |
| `tests/test_inference.py` | Modify | Add tests for tool-call parsing |
| `pal/protocol.py` | Modify | Add `ToolProgressMessage` dataclass |
| `tests/test_protocol.py` | Modify | Add encode/decode tests for new message type |
| `pal/conversation.py` | Modify | Support tool-call and tool-result message formats |
| `tests/test_conversation.py` | Modify | Test new message types in history |
| `pal/daemon.py` | Modify | Rewrite `_handle_chat()` with tool-use loop |
| `tests/test_daemon.py` | Modify | Add tool-use integration tests |
| `pal/cli.py` | Modify | Render `ToolProgressMessage` as dim status text |
| `pal/client.py` | Modify | Handle `ToolProgressMessage` in chat stream |
| `tests/conftest.py` | Modify | Mock server returns tool-call responses for specific prompts |

---

### Task 1: ToolExecutor — read_file and list_directory

**Files:**
- Create: `pal/tools.py`
- Create: `tests/test_tools.py`

- [ ] **Step 1: Write failing tests for read_file**

```python
# tests/test_tools.py
"""Tests for vault tool execution."""
import pytest
from pathlib import Path

from pal.tools import ToolExecutor


@pytest.fixture()
def vault(tmp_path) -> Path:
    """Create a minimal vault structure for tool tests."""
    # Public articles
    research = tmp_path / "Research"
    research.mkdir()
    (research / "quantum.md").write_text(
        "---\ntitle: Quantum Computing\n---\n\n# Quantum Computing\n\nQubits are neat.\n"
    )
    (research / "ml.md").write_text(
        "---\ntitle: Machine Learning\n---\n\n# Machine Learning\n\nNeural nets.\n"
    )
    # Raw directory
    raw = tmp_path / "raw" / "web"
    raw.mkdir(parents=True)
    (raw / "page-abc.md").write_text(
        "---\ntitle: Fetched Page\n---\n\nRaw fetched content.\n"
    )
    # System directory (should be hidden from list_directory)
    wisdom = tmp_path / "_wisdom"
    wisdom.mkdir()
    (wisdom / "be-kind.md").write_text("---\ntitle: Be Kind\n---\n\nBe kind.\n")
    return tmp_path


def test_read_file(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("read_file", {"path": "Research/quantum.md"})
    assert "Quantum Computing" in result
    assert "Qubits are neat." in result


def test_read_file_not_found(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("read_file", {"path": "Research/nonexistent.md"})
    assert "not found" in result.lower()


def test_read_file_path_traversal(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("read_file", {"path": "../../etc/passwd"})
    assert "outside vault" in result.lower() or "escapes" in result.lower()


def test_list_directory_root(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("list_directory", {})
    assert "Research/" in result
    assert "raw/" in result
    # System dirs should be excluded
    assert "_wisdom" not in result


def test_list_directory_subdir(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("list_directory", {"path": "Research"})
    assert "quantum.md" in result
    assert "ml.md" in result


def test_list_directory_not_found(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("list_directory", {"path": "nonexistent"})
    assert "not found" in result.lower()


def test_unknown_tool(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("delete_everything", {})
    assert "unknown tool" in result.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools.py -v`
Expected: `ModuleNotFoundError: No module named 'pal.tools'`

- [ ] **Step 3: Implement ToolExecutor with read_file and list_directory**

```python
# pal/tools.py
"""Vault tools for chat — read-only access to wiki content.

Defines tool schemas (OpenAI function-calling format) and a ToolExecutor
that runs tool calls against the vault.
"""
from pathlib import Path

from pal.retrieval import RetrievalClient

# Maximum characters to return from a file read (~8000 tokens ≈ 32000 chars).
_READ_LIMIT = 32_000

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the vault. Returns frontmatter and body.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to vault root (e.g. 'Research/quantum.md')",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and subdirectories in a vault directory. Omit path to list the vault root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path relative to vault root (e.g. 'Research'). Empty or omitted for root.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_content",
            "description": "Keyword search across vault files. Returns matching filenames with line snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term or phrase to find in vault files.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_vault",
            "description": "Semantic search across the vault using natural language. Returns ranked results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language query (e.g. 'articles about machine learning').",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


class ToolExecutor:
    """Executes tool calls against the vault. All operations are read-only."""

    def __init__(self, vault_path: Path, retrieval: RetrievalClient | None) -> None:
        self.vault_path = vault_path.resolve()
        self.retrieval = retrieval

    def run(self, name: str, arguments: dict) -> str:
        """Dispatch a tool call and return the result as a string.

        Always returns a string — errors are returned as descriptive messages,
        never raised, so the LLM can see what went wrong and adjust.
        """
        handler = {
            "read_file": self._read_file,
            "list_directory": self._list_directory,
            "search_content": self._search_content,
        }.get(name)
        if handler is not None:
            return handler(arguments)
        if name == "search_vault":
            # search_vault is async — caller must use run_async
            return "Error: search_vault must be called via run_async()"
        return f"Unknown tool: {name}"

    async def run_async(self, name: str, arguments: dict) -> str:
        """Dispatch a tool call, supporting async tools like search_vault."""
        if name == "search_vault":
            return await self._search_vault(arguments)
        return self.run(name, arguments)

    def _resolve_safe(self, path: str) -> Path | None:
        """Resolve a path within the vault. Returns None if it escapes."""
        full = (self.vault_path / path).resolve()
        if not str(full).startswith(str(self.vault_path) + "/") and full != self.vault_path:
            return None
        return full

    def _read_file(self, arguments: dict) -> str:
        path = arguments.get("path", "")
        if not path:
            return "Error: 'path' parameter is required."
        resolved = self._resolve_safe(path)
        if resolved is None:
            return f"Error: path escapes outside vault: {path}"
        if not resolved.exists():
            return f"File not found: {path}"
        if not resolved.is_file():
            return f"Not a file: {path} (use list_directory for directories)"
        content = resolved.read_text(errors="replace")
        if len(content) > _READ_LIMIT:
            content = content[:_READ_LIMIT] + f"\n\n[Truncated — file exceeds {_READ_LIMIT} characters]"
        return content

    def _list_directory(self, arguments: dict) -> str:
        path = arguments.get("path", "")
        target = self._resolve_safe(path) if path else self.vault_path
        if target is None:
            return f"Error: path escapes outside vault: {path}"
        if not target.exists():
            return f"Directory not found: {path}"
        if not target.is_dir():
            return f"Not a directory: {path} (use read_file for files)"
        entries = []
        for child in sorted(target.iterdir()):
            name = child.name
            # Skip system dirs/files and hidden files
            if name.startswith("_") or name.startswith("."):
                continue
            if child.is_dir():
                entries.append(f"  {name}/")
            else:
                entries.append(f"  {name}")
        if not entries:
            return f"Directory is empty: {path or '(vault root)'}"
        header = f"Contents of {path or '(vault root)'}:"
        return header + "\n" + "\n".join(entries)

    def _search_content(self, arguments: dict) -> str:
        query = arguments.get("query", "")
        if not query:
            return "Error: 'query' parameter is required."
        query_lower = query.lower()
        matches = []
        for md_file in sorted(self.vault_path.rglob("*.md")):
            rel = md_file.relative_to(self.vault_path)
            # Skip system files
            if any(part.startswith("_") for part in rel.parts):
                continue
            try:
                content = md_file.read_text(errors="replace")
            except OSError:
                continue
            for i, line in enumerate(content.splitlines(), 1):
                if query_lower in line.lower():
                    snippet = line.strip()[:120]
                    matches.append(f"  {rel}:{i}  {snippet}")
                    if len(matches) >= 20:
                        break
            if len(matches) >= 20:
                break
        if not matches:
            return f"No results for: {query}"
        return f"Found {len(matches)} match(es) for '{query}':\n" + "\n".join(matches)

    async def _search_vault(self, arguments: dict) -> str:
        query = arguments.get("query", "")
        if not query:
            return "Error: 'query' parameter is required."
        if self.retrieval is None:
            return "Error: semantic search is not available (no retrieval client)."
        try:
            results = await self.retrieval.search(query)
        except Exception as exc:
            return f"Search error: {exc}"
        if not results:
            return f"No results for: {query}"
        lines = [f"Found {len(results)} result(s) for '{query}':"]
        for r in results:
            lines.append(f"  [{r.get('score', 0):.2f}] {r.get('name', '?')} — {r.get('summary', '')[:100]}")
        return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tools.py -v`
Expected: All 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/tools.py tests/test_tools.py
git commit -m "feat(tools): add ToolExecutor with read_file, list_directory, search_content, search_vault"
```

---

### Task 2: search_content and search_vault tests

**Files:**
- Modify: `tests/test_tools.py`

- [ ] **Step 1: Write failing tests for search_content and search_vault**

Add to `tests/test_tools.py`:

```python
def test_search_content_found(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("search_content", {"query": "Qubits"})
    assert "quantum.md" in result
    assert "Qubits" in result


def test_search_content_not_found(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("search_content", {"query": "zzznoexist"})
    assert "no results" in result.lower()


def test_search_content_skips_system_dirs(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("search_content", {"query": "Be kind"})
    # _wisdom/be-kind.md contains "Be kind" but should be excluded
    assert "no results" in result.lower()


def test_search_content_empty_query(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("search_content", {"query": ""})
    assert "error" in result.lower()


@pytest.mark.asyncio
async def test_search_vault_no_retrieval(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = await executor.run_async("search_vault", {"query": "quantum"})
    assert "not available" in result.lower()
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/test_tools.py -v`
Expected: All 12 tests pass (the implementation from Task 1 already covers these).

- [ ] **Step 3: Commit**

```bash
git add tests/test_tools.py
git commit -m "test(tools): add search_content and search_vault tests"
```

---

### Task 3: ToolProgressMessage protocol

**Files:**
- Modify: `pal/protocol.py`
- Modify: `tests/test_protocol.py`

- [ ] **Step 1: Read existing protocol tests for patterns**

Read: `tests/test_protocol.py`

- [ ] **Step 2: Write failing test for ToolProgressMessage**

Add to `tests/test_protocol.py`:

```python
def test_tool_progress_roundtrip():
    msg = ToolProgressMessage(tool="read_file", arguments={"path": "Research/quantum.md"})
    encoded = encode_message(msg)
    decoded = decode_message(encoded.strip())
    assert isinstance(decoded, ToolProgressMessage)
    assert decoded.tool == "read_file"
    assert decoded.arguments == {"path": "Research/quantum.md"}
    assert decoded.type == "tool_progress"
```

Update the import at the top of the test file to include `ToolProgressMessage`.

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_protocol.py::test_tool_progress_roundtrip -v`
Expected: `ImportError: cannot import name 'ToolProgressMessage'`

- [ ] **Step 4: Add ToolProgressMessage to protocol.py**

Add to `pal/protocol.py` after the `ErrorMessage` dataclass (before `_MESSAGE_TYPES`):

```python
@dataclass
class ToolProgressMessage:
    tool: str
    arguments: dict
    type: str = "tool_progress"
```

Update `_MESSAGE_TYPES` to include the new type:

```python
_MESSAGE_TYPES: dict[str, type] = {
    "chat": ChatMessage,
    "command": CommandMessage,
    "stream_chunk": StreamChunkMessage,
    "response": ResponseMessage,
    "error": ErrorMessage,
    "tool_progress": ToolProgressMessage,
}
```

Update the `Message` union type:

```python
Message = ChatMessage | CommandMessage | StreamChunkMessage | ResponseMessage | ErrorMessage | ToolProgressMessage
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_protocol.py -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add pal/protocol.py tests/test_protocol.py
git commit -m "feat(protocol): add ToolProgressMessage for chat tool-use indicators"
```

---

### Task 4: InferenceClient — tool-aware complete()

**Files:**
- Modify: `pal/inference.py`
- Modify: `tests/test_inference.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Add tool-call mock endpoint to conftest**

In `tests/conftest.py`, update `mock_chat_completions` to return tool calls when the last user message starts with `"TOOLCALL:"`:

```python
async def mock_chat_completions(request: Request):
    """Mock OpenAI-compatible /v1/chat/completions endpoint."""
    body = await request.json()
    stream = body.get("stream", False)
    messages = body.get("messages", [])
    last_user = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"),
        "",
    )

    # If tools are provided and message starts with TOOLCALL:, return a tool call
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
        # Streaming tool call response
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
```

- [ ] **Step 2: Write failing tests for tool-aware complete()**

Add to `tests/test_inference.py`:

```python
from pal.inference import InferenceClient, CompletionResult, ToolCall
from pal.tools import TOOL_DEFINITIONS


@pytest.mark.asyncio
async def test_complete_returns_text_result(mock_inference_server):
    client = InferenceClient(base_url=mock_inference_server, model="test-model")
    result = await client.complete(
        messages=[{"role": "user", "content": "hello"}],
        tools=TOOL_DEFINITIONS,
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
        tools=TOOL_DEFINITIONS,
    )
    assert isinstance(result, CompletionResult)
    assert result.type == "tool_calls"
    assert result.content is None
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "read_file"
    assert result.tool_calls[0].arguments == {"path": "Research/quantum.md"}


@pytest.mark.asyncio
async def test_complete_without_tools_returns_string(mock_inference_server):
    """Backwards compat: complete() without tools still works, returns CompletionResult with text."""
    client = InferenceClient(base_url=mock_inference_server, model="test-model")
    result = await client.complete(
        messages=[{"role": "user", "content": "hello"}],
    )
    assert isinstance(result, CompletionResult)
    assert result.type == "text"
    assert result.content == "echo: hello"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_inference.py -v`
Expected: `ImportError: cannot import name 'CompletionResult'`

- [ ] **Step 4: Implement tool-aware complete()**

Replace `pal/inference.py`:

```python
"""HTTP client for the inference server's OpenAI-compatible API.

Supports both streaming (SSE) and non-streaming completions via
POST /v1/chat/completions. Tool-aware: can pass tool definitions
and parse tool-call responses.
"""
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import httpx


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


class InferenceClient:
    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.AsyncClient(timeout=120.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> CompletionResult:
        """Send a non-streaming completion request.

        Returns a CompletionResult indicating either a text response
        or a list of tool calls the model wants to make.
        """
        payload: dict = {"model": self.model, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        resp = await self._client.post(
            f"{self.base_url}/v1/chat/completions",
            json=payload,
        )
        resp.raise_for_status()
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

        return CompletionResult(type="text", content=message.get("content", ""))

    async def stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncGenerator[str | list[ToolCall], None]:
        """Send a streaming completion request.

        Yields str tokens for text responses. If the model returns tool calls
        instead, accumulates all tool-call deltas and yields a single
        list[ToolCall] as the only item.
        """
        payload: dict = {"model": self.model, "messages": messages, "stream": True}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        # Accumulators for tool-call deltas
        tool_call_acc: dict[int, dict] = {}  # index -> {id, name, arguments_str}
        is_tool_response = False

        async with self._client.stream(
            "POST",
            f"{self.base_url}/v1/chat/completions",
            json=payload,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                delta = chunk["choices"][0].get("delta", {})

                # Check for tool call deltas
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

                # Regular text content
                content = delta.get("content")
                if content is not None:
                    yield content

        # If we accumulated tool calls, yield them as a single list
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

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_inference.py -v`
Expected: All tests pass (old and new).

- [ ] **Step 6: Commit**

```bash
git add pal/inference.py tests/test_inference.py tests/conftest.py
git commit -m "feat(inference): tool-aware complete() and stream() with CompletionResult"
```

---

### Task 5: stream() tool-call detection tests

**Files:**
- Modify: `tests/test_inference.py`

- [ ] **Step 1: Write failing test for stream() tool detection**

Add to `tests/test_inference.py`:

```python
@pytest.mark.asyncio
async def test_stream_returns_text_tokens(mock_inference_server):
    """Streaming a normal message yields string tokens."""
    client = InferenceClient(base_url=mock_inference_server, model="test-model")
    items = []
    async for item in client.stream(
        messages=[{"role": "user", "content": "hello"}],
        tools=TOOL_DEFINITIONS,
    ):
        items.append(item)
    # All items should be strings (text tokens)
    assert all(isinstance(item, str) for item in items)
    assert "".join(items) == "echo: hello"


@pytest.mark.asyncio
async def test_stream_detects_tool_calls(mock_inference_server):
    """Streaming a TOOLCALL message yields a list[ToolCall]."""
    client = InferenceClient(base_url=mock_inference_server, model="test-model")
    items = []
    async for item in client.stream(
        messages=[{"role": "user", "content": "TOOLCALL:read_file"}],
        tools=TOOL_DEFINITIONS,
    ):
        items.append(item)
    # Should get exactly one item: a list of ToolCalls
    assert len(items) == 1
    assert isinstance(items[0], list)
    assert len(items[0]) == 1
    assert items[0][0].name == "read_file"
    assert items[0][0].arguments == {"path": "Research/quantum.md"}
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/test_inference.py -v`
Expected: All tests pass (implementation from Task 4 already handles this).

- [ ] **Step 3: Commit**

```bash
git add tests/test_inference.py
git commit -m "test(inference): add stream() tool-call detection tests"
```

---

### Task 6: Conversation history — tool messages

**Files:**
- Modify: `pal/conversation.py`
- Modify: `tests/test_conversation.py`

- [ ] **Step 1: Write failing tests for tool-call message support**

Add to `tests/test_conversation.py`:

```python
def test_add_tool_call_and_result():
    """Conversation stores assistant tool_calls and tool results."""
    conv = Conversation(history_depth=50)
    conv.add_user("look at quantum.md")

    # Assistant responds with a tool call
    conv.add_assistant_tool_calls([{
        "id": "call_001",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path": "Research/quantum.md"}'},
    }])

    # Tool result
    conv.add_tool_result("call_001", "# Quantum Computing\n\nQubits are neat.")

    messages = conv.messages
    assert len(messages) == 3
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["tool_calls"][0]["id"] == "call_001"
    assert messages[2]["role"] == "tool"
    assert messages[2]["tool_call_id"] == "call_001"
    assert "Qubits" in messages[2]["content"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_conversation.py::test_add_tool_call_and_result -v`
Expected: `AttributeError: 'Conversation' object has no attribute 'add_assistant_tool_calls'`

- [ ] **Step 3: Implement tool message support in Conversation**

Add to `pal/conversation.py` in the `Conversation` class, after `add_assistant`:

```python
    def add_assistant_tool_calls(self, tool_calls: list[dict]) -> None:
        """Record an assistant message that contains tool calls (no text content)."""
        self._messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": tool_calls,
        })
        self._truncate()

    def add_tool_result(self, tool_call_id: str, content: str) -> None:
        """Record a tool result message."""
        self._messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        })
        self._truncate()
```

Also update the type hint on `_messages` from `list[dict[str, str]]` to `list[dict]` since tool messages have non-string values. Update `get_messages_for_api` similarly:

```python
    _messages: list[dict] = field(default_factory=list)

    def get_messages_for_api(self, system_prompt: str) -> list[dict]:
        """Return message list for the inference API: system + history."""
        return [{"role": "system", "content": system_prompt}] + self.messages
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_conversation.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/conversation.py tests/test_conversation.py
git commit -m "feat(conversation): support tool_calls and tool result messages in history"
```

---

### Task 7: Daemon chat handler — tool-use loop

**Files:**
- Modify: `pal/daemon.py`
- Modify: `tests/test_daemon.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Write failing integration test for tool-use in chat**

Add to `tests/test_daemon.py`:

```python
from pal.protocol import (
    ChatMessage, StreamChunkMessage, ResponseMessage,
    ToolProgressMessage, encode_message, decode_message,
)


@pytest.mark.asyncio
async def test_daemon_chat_tool_use(running_daemon, socket_path):
    """Chat message that triggers tool use sends progress + final response."""
    reader, writer = await asyncio.open_unix_connection(str(socket_path))

    # TOOLCALL: prefix triggers mock to return a tool call on first request,
    # then a text response on the second (after tool result is appended)
    msg = ChatMessage(text="TOOLCALL:read_file")
    writer.write(encode_message(msg))
    await writer.drain()

    received = []
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        if not line:
            break
        decoded = decode_message(line.strip())
        received.append(decoded)
        if isinstance(decoded, (ResponseMessage, ErrorMessage)):
            break

    writer.close()
    await writer.wait_closed()

    # Should have at least one ToolProgressMessage and one ResponseMessage
    progress_msgs = [m for m in received if isinstance(m, ToolProgressMessage)]
    response_msgs = [m for m in received if isinstance(m, ResponseMessage)]
    assert len(progress_msgs) >= 1
    assert progress_msgs[0].tool == "read_file"
    assert len(response_msgs) == 1
```

- [ ] **Step 2: Update conftest mock to support multi-turn tool use**

The mock needs to return a text response when it receives a message list containing tool results. Update the TOOLCALL logic in `mock_chat_completions` in `tests/conftest.py`:

After the existing `if tools and last_user.startswith("TOOLCALL:"):` block, add a check for tool result messages:

```python
    # If the messages contain a tool result, respond with text (loop completion)
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
        # For streaming, treat as normal text
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
```

Place this check **before** the existing `TOOLCALL:` check so that after tool results are appended, the mock returns text.

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_daemon.py::test_daemon_chat_tool_use -v`
Expected: Fails because `_handle_chat` doesn't send `ToolProgressMessage` yet.

- [ ] **Step 4: Rewrite _handle_chat with tool-use loop**

Replace `_handle_chat` in `pal/daemon.py` (lines 135-164):

```python
    async def _handle_chat(
        self,
        msg: ChatMessage,
        conv: Conversation,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Process a chat message with optional tool use.

        First call uses streaming. If the model returns tool calls instead of
        text, enters a non-streaming loop: execute tools, show progress, feed
        results back, repeat until the model returns text or the loop cap is hit.
        """
        from pal.inference import ToolCall
        from pal.tools import TOOL_DEFINITIONS

        conv.add_user(msg.text)
        messages = conv.get_messages_for_api(system_prompt=self.prompt_builder.build())
        max_tool_rounds = 10

        # First call: streaming — normal chat stays fast
        try:
            full_response = []
            tool_calls: list[ToolCall] | None = None

            async for item in self.inference.stream(messages, tools=TOOL_DEFINITIONS):
                if isinstance(item, list):
                    # Model returned tool calls
                    tool_calls = item
                    break
                else:
                    chunk = StreamChunkMessage(token=item)
                    writer.write(encode_message(chunk))
                    await writer.drain()
                    full_response.append(item)

            # If we got text, we're done
            if tool_calls is None:
                response_text = "".join(full_response)
                conv.add_assistant(response_text)
                done = ResponseMessage(text=response_text)
                writer.write(encode_message(done))
                await writer.drain()
                return

            # Tool-use loop
            for _round in range(max_tool_rounds):
                # Record the assistant's tool-call message in history
                raw_calls = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in tool_calls
                ]
                conv.add_assistant_tool_calls(raw_calls)

                # Execute each tool call
                for tc in tool_calls:
                    progress = ToolProgressMessage(tool=tc.name, arguments=tc.arguments)
                    writer.write(encode_message(progress))
                    await writer.drain()

                    result = await self.tool_executor.run_async(tc.name, tc.arguments)
                    conv.add_tool_result(tc.id, result)

                # Get next response (non-streaming)
                messages = conv.get_messages_for_api(
                    system_prompt=self.prompt_builder.build()
                )
                completion = await self.inference.complete(messages, tools=TOOL_DEFINITIONS)

                if completion.type == "text":
                    response_text = completion.content or ""
                    conv.add_assistant(response_text)
                    done = ResponseMessage(text=response_text)
                    writer.write(encode_message(done))
                    await writer.drain()
                    return

                # More tool calls — continue loop
                tool_calls = completion.tool_calls

            # Hit the loop cap
            conv.add_assistant("I've reached the limit of tool calls for this turn. Here's what I found so far.")
            done = ResponseMessage(
                text="I've reached the limit of tool calls for this turn. Here's what I found so far."
            )
            writer.write(encode_message(done))
            await writer.drain()

        except Exception as exc:
            logger.exception("Chat error: %s", exc)
            error = ErrorMessage(error=f"Chat error: {exc}")
            writer.write(encode_message(error))
            await writer.drain()
```

Also add `import json` to the top of daemon.py if not already present, and add `ToolProgressMessage` to the protocol imports:

```python
from pal.protocol import (
    ChatMessage,
    CommandMessage,
    StreamChunkMessage,
    ResponseMessage,
    ErrorMessage,
    ToolProgressMessage,
    Message,
    encode_message,
    decode_message,
)
```

And initialize the `ToolExecutor` in `Daemon.__init__` (after `self.retrieval` is set up):

```python
        from pal.tools import ToolExecutor
        self.tool_executor = ToolExecutor(
            vault_path=config.vault_path,
            retrieval=self.retrieval,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_daemon.py -v`
Expected: All tests pass (old streaming tests still work, new tool-use test passes).

- [ ] **Step 6: Run the full test suite**

Run: `pytest -v`
Expected: All tests pass. No regressions from the `complete()` return type change.

- [ ] **Step 7: Fix any regressions from complete() return type change**

The old `complete()` returned `str`. Now it returns `CompletionResult`. Find all callers of `self.inference.complete()` in `daemon.py` and update them to use `result.content`:

Search for `await self.inference.complete(` in `pal/daemon.py`. Each call site (e.g., `_handle_note`, `_handle_summarize`, `_handle_compile`, `_handle_learn`) does:

```python
body = await self.inference.complete(messages)
```

Change each to:

```python
result = await self.inference.complete(messages)
body = result.content
```

- [ ] **Step 8: Run full test suite again**

Run: `pytest -v`
Expected: All tests pass.

- [ ] **Step 9: Commit**

```bash
git add pal/daemon.py tests/test_daemon.py tests/conftest.py
git commit -m "feat(daemon): tool-use loop in chat handler with progress indicators"
```

---

### Task 8: CLI and client — render ToolProgressMessage

**Files:**
- Modify: `pal/client.py`
- Modify: `pal/cli.py`

- [ ] **Step 1: Update client.py to handle ToolProgressMessage**

In `pal/client.py`, add `ToolProgressMessage` to the imports:

```python
from pal.protocol import (
    ChatMessage,
    CommandMessage,
    StreamChunkMessage,
    ResponseMessage,
    ErrorMessage,
    ToolProgressMessage,
    Message,
    encode_message,
    decode_message,
)
```

In the `chat()` method, update the break condition to not break on `ToolProgressMessage` (it already won't since it only breaks on `ResponseMessage | ErrorMessage`, but the yield ensures the CLI gets it):

No code change needed — `chat()` already yields all decoded messages and only breaks on `ResponseMessage` or `ErrorMessage`. `ToolProgressMessage` will be yielded to the CLI naturally.

- [ ] **Step 2: Update cli.py to render progress indicators**

In `pal/cli.py`, add `ToolProgressMessage` to the import:

```python
from pal.protocol import StreamChunkMessage, ResponseMessage, ErrorMessage, ToolProgressMessage
```

Update the chat rendering section (the `async for msg in client.chat(text):` loop). The current code wraps everything in a `Live` context for markdown rendering, but progress messages should appear outside the live display. Restructure the chat section:

```python
            # Stream chat response with live markdown rendering
            accumulated = ""
            console.print()
            live = None
            try:
                async for msg in client.chat(text):
                    if isinstance(msg, ToolProgressMessage):
                        # Show progress indicator outside of live markdown
                        if live is not None:
                            live.stop()
                            live = None
                        label = _tool_progress_label(msg.tool, msg.arguments)
                        console.print(f"  [dim]{label}[/dim]")
                    elif isinstance(msg, StreamChunkMessage):
                        if live is None:
                            live = Live(Markdown(""), console=console, refresh_per_second=10)
                            live.start()
                        accumulated += msg.token
                        live.update(Markdown(accumulated))
                    elif isinstance(msg, ResponseMessage):
                        # Non-streamed final response (from tool-use loop)
                        if not accumulated and msg.text:
                            console.print(Markdown(msg.text))
                        break
                    elif isinstance(msg, ErrorMessage):
                        console.print(f"[red]{msg.error}[/red]")
                        break
            finally:
                if live is not None:
                    live.stop()
            console.print()
```

Add the helper function before `run_repl()`:

```python
def _tool_progress_label(tool: str, arguments: dict) -> str:
    """Format a brief progress label for a tool call."""
    if tool == "read_file":
        return f"[reading {arguments.get('path', '?')}...]"
    if tool == "list_directory":
        path = arguments.get("path", "")
        return f"[listing {path or 'vault'}...]"
    if tool == "search_content":
        return f"[searching for \"{arguments.get('query', '?')}\"...]"
    if tool == "search_vault":
        return f"[searching vault for \"{arguments.get('query', '?')}\"...]"
    return f"[{tool}...]"
```

- [ ] **Step 3: Run the full test suite**

Run: `pytest -v`
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add pal/client.py pal/cli.py
git commit -m "feat(cli): render tool progress indicators during chat"
```

---

### Task 9: End-to-end manual test

- [ ] **Step 1: Start the daemon and test normal chat still works**

```bash
# Terminal 1: start daemon
pal-daemon

# Terminal 2: test normal chat
pal
you> Hello, how are you?
# Should stream a normal response — no tool calls
```

- [ ] **Step 2: Test tool use in conversation**

```bash
you> What files are in the vault?
# Should see: [listing vault...]
# Then a response listing directories/files

you> Read the first article you see
# Should see: [reading <path>...]
# Then the file contents summarized

you> Search for anything about <topic in your vault>
# Should see: [searching for "..."]
# Then results
```

- [ ] **Step 3: Test multi-step tool use**

```bash
you> Compare the files in Research/ with what's in raw/
# Should see multiple progress indicators as it lists both dirs and reads files
```

- [ ] **Step 4: Commit any fixes**

```bash
git add -u
git commit -m "fix: adjustments from manual testing"
```
