# PAL Phase 3: Retrieval Layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let PAL do semantic search over the vault via the inference server's collection endpoints, with a `/search` slash command that returns ranked results and a two-step pattern (summaries first, full doc on demand).

**Architecture:** A `RetrievalClient` class wraps the inference server's `POST /collections/{id}/search` and `GET /collections/{id}/docs/{doc_id}` endpoints with an async httpx client. The daemon exposes `/search <query>` and `/get <doc_id>` slash commands. Configuration adds a `collection_id` setting (default: `"vault"`). The vault must be registered as a collection in the inference server's `/etc/llama/collections.json` — this is server-side config outside PAL.

**Tech Stack:** Python 3.12, httpx (existing), existing PAL modules (daemon, protocol, config), starlette mock fixtures for testing

---

## File Structure

```
pal/
├── retrieval.py         # RetrievalClient — thin HTTP wrapper over collection endpoints
├── daemon.py            # Modified — wire /search and /get into _handle_command
├── config.py            # Modified — add collection_id setting
tests/
├── test_retrieval.py    # RetrievalClient tests against mock server
├── test_retrieval_commands.py  # Integration: /search and /get via client
├── conftest.py          # Modified — add mock collection search endpoint
```

---

### Task 1: Add collection_id to Config

**Files:**
- Modify: `pal/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

APPEND to `tests/test_config.py`:
```python
def test_default_config_has_collection_id():
    cfg = Config()
    assert cfg.collection_id == "vault"


def test_load_config_collection_id_from_env(monkeypatch):
    monkeypatch.setenv("PAL_COLLECTION_ID", "my-vault")
    # Clear other env vars so other defaults don't interfere
    for key in ["PAL_INFERENCE_URL", "PAL_MODEL", "PAL_SOCKET_PATH", "PAL_HISTORY_DEPTH", "PAL_VAULT_PATH"]:
        monkeypatch.delenv(key, raising=False)
    cfg = load_config()
    assert cfg.collection_id == "my-vault"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py::test_default_config_has_collection_id -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'collection_id'`

- [ ] **Step 3: Add collection_id to Config dataclass**

In `pal/config.py`, add to the `Config` dataclass:
```python
    collection_id: str = "vault"
```

And in `load_config()`, add after the vault_path block:
```python
    if cid := os.environ.get("PAL_COLLECTION_ID"):
        kwargs["collection_id"] = cid
```

The full updated Config class:
```python
@dataclass
class Config:
    inference_url: str = "http://192.168.1.14:11434"
    model: str = "Qwen3.5-35B-A3B-Q4_K_M"
    socket_path: Path = field(default_factory=_default_socket_path)
    history_depth: int = 50
    vault_path: Path = field(default_factory=lambda: Path.home() / "vault")
    collection_id: str = "vault"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pal/config.py tests/test_config.py
git commit -m "feat: add collection_id config setting for retrieval"
```

---

### Task 2: RetrievalClient — Search and Get Document

**Files:**
- Create: `pal/retrieval.py`
- Create: `tests/test_retrieval.py`
- Modify: `tests/conftest.py` (add mock collection endpoints)

- [ ] **Step 1: Add mock collection endpoints to conftest.py**

APPEND to `tests/conftest.py` (do NOT modify existing content). First locate the `mock_app = Starlette(...)` line and you'll need to REPLACE the existing `mock_app = Starlette(routes=[...])` definition with the one that includes new routes.

Find this block in `tests/conftest.py`:
```python
mock_app = Starlette(routes=[
    Route("/v1/chat/completions", mock_chat_completions, methods=["POST"]),
])
```

And REPLACE it with:
```python
async def mock_collection_search(request: Request):
    """Mock POST /collections/{collection_id}/search endpoint."""
    body = await request.json()
    query = body.get("query", "")
    limit = body.get("limit", 5)
    # Return fake results based on the query
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


mock_app = Starlette(routes=[
    Route("/v1/chat/completions", mock_chat_completions, methods=["POST"]),
    Route("/collections/{collection_id}/search", mock_collection_search, methods=["POST"]),
    Route("/collections/{collection_id}/docs/{doc_id:path}", mock_collection_get_doc, methods=["GET"]),
])
```

- [ ] **Step 2: Write the failing tests**

`tests/test_retrieval.py`:
```python
"""Tests for the retrieval client — collection search and doc fetch."""
import pytest

from pal.retrieval import RetrievalClient


@pytest.mark.asyncio
async def test_search_returns_results(mock_inference_server):
    client = RetrievalClient(base_url=mock_inference_server, collection_id="vault")
    results = await client.search("quantum computing")
    assert len(results) == 3
    assert results[0]["id"] == "doc-0"
    assert "quantum computing" in results[0]["summary"]
    assert results[0]["score"] > results[1]["score"]  # sorted by score


@pytest.mark.asyncio
async def test_search_respects_limit(mock_inference_server):
    client = RetrievalClient(base_url=mock_inference_server, collection_id="vault")
    results = await client.search("anything", limit=1)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_get_document(mock_inference_server):
    client = RetrievalClient(base_url=mock_inference_server, collection_id="vault")
    doc = await client.get_document("Projects/alpha.md")
    assert doc["id"] == "Projects/alpha.md"
    assert "Full content" in doc["content"]


@pytest.mark.asyncio
async def test_get_document_not_found(mock_inference_server):
    client = RetrievalClient(base_url=mock_inference_server, collection_id="vault")
    with pytest.raises(FileNotFoundError):
        await client.get_document("missing")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_retrieval.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pal.retrieval'`

- [ ] **Step 4: Implement retrieval.py**

`pal/retrieval.py`:
```python
"""HTTP client for the inference server's collection search endpoints.

Thin wrapper over:
  POST /collections/{collection_id}/search
  GET  /collections/{collection_id}/docs/{doc_id}

The inference server handles embedding generation and vector search.
PAL's retrieval layer is used when the wiki outgrows index-file navigation
or for fuzzy/semantic queries.
"""
import httpx


class RetrievalClient:
    def __init__(self, base_url: str, collection_id: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.collection_id = collection_id
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def search(
        self,
        query: str,
        limit: int = 5,
        tags: list[str] | None = None,
    ) -> list[dict]:
        """Search the collection for documents matching the query.

        Returns a list of result dicts with keys: id, name, collection,
        summary, tags, score. Results are sorted by score (descending).
        """
        payload: dict = {"query": query, "limit": limit}
        if tags:
            payload["tags"] = tags
        resp = await self._client.post(
            f"{self.base_url}/collections/{self.collection_id}/search",
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", [])

    async def get_document(self, doc_id: str) -> dict:
        """Fetch the full content of a document by its ID.

        Returns a dict with keys: id, name, collection, summary, content, metadata.
        Raises FileNotFoundError if the document doesn't exist.
        """
        resp = await self._client.get(
            f"{self.base_url}/collections/{self.collection_id}/docs/{doc_id}"
        )
        if resp.status_code == 404:
            raise FileNotFoundError(f"Document not found: {doc_id}")
        resp.raise_for_status()
        return resp.json()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_retrieval.py -v`
Expected: all 4 tests PASS

- [ ] **Step 6: Commit**

```bash
git add pal/retrieval.py tests/test_retrieval.py tests/conftest.py
git commit -m "feat: RetrievalClient — collection search and document fetch"
```

---

### Task 3: Wire /search and /get into Daemon

**Files:**
- Modify: `pal/daemon.py`
- Create: `tests/test_retrieval_commands.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_retrieval_commands.py`:
```python
"""Integration tests for /search and /get slash commands via the daemon."""
import asyncio

import pytest

from pal.client import PalClient
from pal.config import Config
from pal.daemon import Daemon


@pytest.fixture()
async def retrieval_daemon(socket_path, mock_inference_server, tmp_path):
    """Start a daemon with a mock inference server (which also serves collection endpoints)."""
    cfg = Config(
        inference_url=mock_inference_server,
        model="test-model",
        socket_path=socket_path,
        history_depth=50,
        vault_path=tmp_path / "vault",
        collection_id="vault",
    )
    daemon = Daemon(cfg)
    task = asyncio.create_task(daemon.serve())
    for _ in range(100):
        if socket_path.exists():
            break
        await asyncio.sleep(0.01)
    yield daemon
    daemon.shutdown()
    await task


@pytest.mark.asyncio
async def test_search_command_returns_results(retrieval_daemon, socket_path):
    """/search query returns ranked results."""
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("search", "quantum computing")
    # Mock returns 3 results
    assert "doc-0" in resp.text
    assert "Summary for quantum computing" in resp.text

    await client.close()


@pytest.mark.asyncio
async def test_search_command_empty_query(retrieval_daemon, socket_path):
    """/search with no args returns usage error."""
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="Usage"):
        await client.command("search", "")

    await client.close()


@pytest.mark.asyncio
async def test_get_command_returns_document(retrieval_daemon, socket_path):
    """/get <doc_id> returns full document content."""
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("get", "Projects/alpha.md")
    assert "Full content" in resp.text
    assert "Projects/alpha.md" in resp.text

    await client.close()


@pytest.mark.asyncio
async def test_get_command_document_not_found(retrieval_daemon, socket_path):
    """/get with a missing doc_id returns an error."""
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="not found"):
        await client.command("get", "missing")

    await client.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_retrieval_commands.py -v`
Expected: FAIL — daemon doesn't handle /search or /get yet

- [ ] **Step 3: Wire retrieval into daemon**

In `pal/daemon.py`:

1. Add import at the top:
```python
from pal.retrieval import RetrievalClient
```

2. In `Daemon.__init__`, add after `self.wiki.init_vault()`:
```python
        self.retrieval = RetrievalClient(
            base_url=config.inference_url,
            collection_id=config.collection_id,
        )
```

3. In `_handle_command`, add two new elif branches before the `else:` (unknown command) branch:
```python
        elif msg.name == "search":
            await self._handle_search(msg.args, writer)
        elif msg.name == "get":
            await self._handle_get(msg.args, writer)
```

4. Add these two new handler methods to the `Daemon` class:
```python
    async def _handle_search(self, query: str, writer: asyncio.StreamWriter) -> None:
        """Handle /search <query> — semantic search over the vault collection."""
        query = query.strip()
        if not query:
            error = ErrorMessage(error="Usage: /search <query>")
            writer.write(encode_message(error))
            await writer.drain()
            return
        try:
            results = await self.retrieval.search(query, limit=5)
        except Exception as exc:
            logger.exception("Search failed: %s", exc)
            error = ErrorMessage(error=f"Search failed: {exc}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        if not results:
            resp = ResponseMessage(text="No results found.", command="search")
        else:
            lines = [f"Found {len(results)} result(s):\n"]
            for r in results:
                score = r.get("score", 0.0)
                summary = r.get("summary", "")
                lines.append(f"- **{r['id']}** (score: {score:.2f})")
                if summary:
                    lines.append(f"  {summary}")
            resp = ResponseMessage(text="\n".join(lines), command="search")
        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_get(self, doc_id: str, writer: asyncio.StreamWriter) -> None:
        """Handle /get <doc_id> — fetch full document content."""
        doc_id = doc_id.strip()
        if not doc_id:
            error = ErrorMessage(error="Usage: /get <doc_id>")
            writer.write(encode_message(error))
            await writer.drain()
            return
        try:
            doc = await self.retrieval.get_document(doc_id)
        except FileNotFoundError:
            error = ErrorMessage(error=f"Document not found: {doc_id}")
            writer.write(encode_message(error))
            await writer.drain()
            return
        except Exception as exc:
            logger.exception("Get document failed: %s", exc)
            error = ErrorMessage(error=f"Get failed: {exc}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        content = doc.get("content", "")
        name = doc.get("name", doc_id)
        resp = ResponseMessage(
            text=f"**{name}** ({doc_id})\n\n{content}",
            command="get",
        )
        writer.write(encode_message(resp))
        await writer.drain()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_retrieval_commands.py -v`
Expected: all 4 tests PASS

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest tests/ -v`
Expected: all tests pass (previously passing tests + 4 new retrieval command tests + 4 new retrieval client tests + 2 new config tests)

- [ ] **Step 6: Commit**

```bash
git add pal/daemon.py tests/test_retrieval_commands.py
git commit -m "feat: wire /search and /get commands into daemon"
```

---

### Task 4: Update /status to Include Collection Info

**Files:**
- Modify: `pal/daemon.py`
- Modify: `tests/test_wiki_commands.py` (update existing test)

- [ ] **Step 1: Update the status handler**

In `pal/daemon.py`, find the `/status` branch in `_handle_command` and update it to include the collection ID:

Current code:
```python
        elif msg.name == "status":
            articles = self.wiki.list_articles()
            resp = ResponseMessage(
                text=(
                    f"Model: {self.inference.model}\n"
                    f"Server: {self.inference.base_url}\n"
                    f"Vault: {self.wiki.vault_path} ({len(articles)} articles)"
                ),
                command="status",
            )
            writer.write(encode_message(resp))
            await writer.drain()
```

Replace with:
```python
        elif msg.name == "status":
            articles = self.wiki.list_articles()
            resp = ResponseMessage(
                text=(
                    f"Model: {self.inference.model}\n"
                    f"Server: {self.inference.base_url}\n"
                    f"Vault: {self.wiki.vault_path} ({len(articles)} articles)\n"
                    f"Collection: {self.retrieval.collection_id}"
                ),
                command="status",
            )
            writer.write(encode_message(resp))
            await writer.drain()
```

- [ ] **Step 2: Run the existing status test to confirm it still passes**

Run: `python -m pytest tests/test_wiki_commands.py::test_status_command_includes_vault -v`
Expected: PASS (test just checks that "vault" appears, which still does)

- [ ] **Step 3: Add a test for collection info in status**

APPEND to `tests/test_retrieval_commands.py`:
```python
@pytest.mark.asyncio
async def test_status_includes_collection(retrieval_daemon, socket_path):
    """/status now shows the collection id."""
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("status")
    assert "Collection:" in resp.text
    assert "vault" in resp.text

    await client.close()
```

- [ ] **Step 4: Run the new test**

Run: `python -m pytest tests/test_retrieval_commands.py::test_status_includes_collection -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pal/daemon.py tests/test_retrieval_commands.py
git commit -m "feat: /status now includes collection id"
```

---

### Task 5: Final Verification

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: all tests pass, no warnings beyond the existing websockets deprecations

- [ ] **Step 2: Verify the new commands are wired**

The new commands `/search` and `/get` should be routed through `client.command()` in the CLI. Review `pal/cli.py` — the existing code sends any non-quit slash command through `client.command()`, which is exactly what we need. No CLI changes are required.

- [ ] **Step 3: Update inline help text**

In `pal/cli.py`, find:
```python
    console.print("[dim]Type /quit or /exit to exit, /status for daemon info[/dim]\n")
```

Replace with:
```python
    console.print("[dim]Commands: /note /read /search /get /lint /status /quit[/dim]\n")
```

- [ ] **Step 4: Commit**

```bash
git add pal/cli.py
git commit -m "docs: update CLI help text with all available commands"
```
