# PAL Reindex Integration Implementation Plan (client side)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire PAL to call the inference server's new `/reindex` endpoint after every wiki write, so freshly-created or modified articles are searchable without a server restart. Surface reindex status in tool results so the LLM can decide whether to wait or report `reindex_pending`.

**Architecture:** Extend `RetrievalClient` with three new HTTP methods (`trigger_reindex`, `get_reindex_status`, `get_reindex_job`) that fail gracefully when the server is unreachable. Inject the client into `Compiler`, `Consolidator`, and `Reorganizer` so they trigger a scoped reindex after their writes complete; merge the response into their structured outcome dicts. The tool layer passes the new `reindex` key through unchanged. Add a `wait_for_reindex` tool that polls until the job finishes or a timeout, so the model can request "definitely searchable" semantics when it matters.

**Tech Stack:** Python 3.11+, asyncio, httpx (existing), pytest, pytest-asyncio.

---

## File Structure

**Modify:**
- `pal/retrieval.py` — add `trigger_reindex`, `get_reindex_status`, `get_reindex_job` methods. Best-effort: return `None` on connection error rather than raising, so a downed inference server does not break the write path.
- `pal/compiler.py` — `Compiler.__init__` gains optional `retrieval` kwarg; after a successful first-compile or merge-into-existing, call `trigger_reindex([absolute target path])` and stash the response under `outcome["reindex"]`.
- `pal/consolidator.py` — same shape: optional `retrieval` kwarg, post-write trigger, `outcome["reindex"]`.
- `pal/reorg.py` — `Reorganizer.__init__` gains optional `retrieval` kwarg; after `execute_operations_async`, gather dst paths from the per-op results, fire one reindex covering all of them, and add a top-level `reindex` key to the report returned by `_reorg` in `tools.py`.
- `pal/tools.py` — wire reindex trigger into `_edit_file` and `_create_file` (these write directly through `WikiManager`, not through the business classes). Add a new tool spec + handler `wait_for_reindex(job_id, timeout_seconds=30)`.
- `pal/daemon.py` — pass `self.retrieval` into the `Compiler`, `Consolidator`, `Reorganizer` constructors; add a single `_trigger_reindex_for_paths(paths)` helper used by slash-command handlers (`/note`, `/import`, `/learn`, `/promote`) that already write directly to the wiki.
- `pal/prompt_builder.py` — short note in the BASE_PROMPT explaining that `compile_batch`, `consolidate`, `reorg`, `compile_summary` results now include a `reindex` field, and that `wait_for_reindex` exists for when the model needs to be sure new content is searchable.
- `README.md` (top level) — update the chat-tools table to include `wait_for_reindex` and add a short paragraph on the reindex flow.

**Tests modified:**
- `tests/test_retrieval.py` — three new tests for the new methods (mock httpx).
- `tests/test_compiler.py` — happy-path test that asserts `outcome["reindex"]` is populated when a fake retrieval client is wired.
- `tests/test_consolidator.py` — same.
- `tests/test_reorg.py` — same, plus aggregate-paths assertion.
- `tests/test_chat_compile_tools.py`, `tests/test_chat_consolidate_tools.py`, `tests/test_chat_reorg_tools.py` — the JSON tool result now contains `reindex`.
- `tests/test_tools.py` — new tests for `_edit_file` / `_create_file` reindex wiring + `wait_for_reindex` tool.
- `tests/test_prompt_builder.py` — assert prompt mentions `wait_for_reindex`.
- `tests/test_consolidate_integration.py` — extend the e2e smoke to cover the reindex-trigger pass-through (with a fake retrieval client).

**No new modules.** All work lives in existing files.

---

## Task 1: Extend `RetrievalClient` with reindex methods

**Files:**
- Modify: `pal/retrieval.py`
- Test: `tests/test_retrieval.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_retrieval.py` (use the same async + httpx-mock pattern used by existing tests in that file — read the existing tests first to confirm the fixture style):

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pal.retrieval import RetrievalClient


@pytest.mark.asyncio
async def test_trigger_reindex_full_scan_posts_empty_body():
    client = RetrievalClient(base_url="http://server", collection_id="vault")
    fake_response = MagicMock(status_code=202)
    fake_response.json.return_value = {
        "job_id": "j1", "collection_id": "vault", "status": "queued",
        "paths": None, "stats": {"new": 0, "updated": 0, "removed": 0, "unchanged": 0},
        "started_at": "2026-04-15T00:00:00Z", "finished_at": None, "error": None,
    }
    client._client.post = AsyncMock(return_value=fake_response)

    result = await client.trigger_reindex()

    client._client.post.assert_awaited_once_with(
        "http://server/collections/vault/reindex",
        json={},
    )
    assert result == fake_response.json.return_value
    assert result["job_id"] == "j1"


@pytest.mark.asyncio
async def test_trigger_reindex_with_paths_posts_paths():
    client = RetrievalClient(base_url="http://server", collection_id="vault")
    fake_response = MagicMock(status_code=202)
    fake_response.json.return_value = {
        "job_id": "j2", "collection_id": "vault", "status": "queued",
        "paths": ["/abs/x.md"],
    }
    client._client.post = AsyncMock(return_value=fake_response)

    result = await client.trigger_reindex(paths=["/abs/x.md"])

    client._client.post.assert_awaited_once_with(
        "http://server/collections/vault/reindex",
        json={"paths": ["/abs/x.md"]},
    )
    assert result["paths"] == ["/abs/x.md"]


@pytest.mark.asyncio
async def test_trigger_reindex_returns_none_on_connection_error():
    """A downed inference server must not break the write path. trigger_reindex
    returns None and logs a warning."""
    import httpx
    client = RetrievalClient(base_url="http://server", collection_id="vault")
    client._client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

    result = await client.trigger_reindex(paths=["/abs/x.md"])
    assert result is None


@pytest.mark.asyncio
async def test_get_reindex_status_returns_dict():
    client = RetrievalClient(base_url="http://server", collection_id="vault")
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {
        "job_id": "j1", "status": "done", "collection_id": "vault",
    }
    client._client.get = AsyncMock(return_value=fake_response)

    result = await client.get_reindex_status()

    client._client.get.assert_awaited_once_with(
        "http://server/collections/vault/reindex/status",
    )
    assert result["status"] == "done"


@pytest.mark.asyncio
async def test_get_reindex_status_returns_none_on_404():
    """No job recorded yet returns None (not an error)."""
    client = RetrievalClient(base_url="http://server", collection_id="vault")
    fake_response = MagicMock(status_code=404)
    client._client.get = AsyncMock(return_value=fake_response)

    result = await client.get_reindex_status()
    assert result is None


@pytest.mark.asyncio
async def test_get_reindex_job_by_id():
    client = RetrievalClient(base_url="http://server", collection_id="vault")
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {
        "job_id": "abc", "status": "running", "collection_id": "vault",
    }
    client._client.get = AsyncMock(return_value=fake_response)

    result = await client.get_reindex_job("abc")

    client._client.get.assert_awaited_once_with(
        "http://server/collections/vault/reindex/abc",
    )
    assert result["status"] == "running"


@pytest.mark.asyncio
async def test_get_reindex_job_404_returns_none():
    client = RetrievalClient(base_url="http://server", collection_id="vault")
    fake_response = MagicMock(status_code=404)
    client._client.get = AsyncMock(return_value=fake_response)

    result = await client.get_reindex_job("missing")
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_retrieval.py -v -k reindex`
Expected: FAIL (`AttributeError: 'RetrievalClient' object has no attribute 'trigger_reindex'`).

- [ ] **Step 3: Add the three methods to `RetrievalClient`**

In `pal/retrieval.py`, add to the `RetrievalClient` class (after `get_document`):

```python
    async def trigger_reindex(
        self,
        paths: list[str] | None = None,
    ) -> dict | None:
        """Ask the inference server to reindex the collection.

        With `paths` omitted: full incremental scan of the collection's
        source_dir. With `paths` provided: only those absolute paths are
        rescanned; stale-deletion is skipped.

        Returns the server's response dict on success (HTTP 202), or None
        on connection error. A None return is intentional best-effort:
        a downed inference server must never break the write path.
        """
        body: dict = {}
        if paths is not None:
            body["paths"] = list(paths)
        try:
            resp = await self._client.post(
                f"{self.base_url}/collections/{self.collection_id}/reindex",
                json=body,
            )
        except Exception as exc:
            logger.warning("trigger_reindex failed: %s", exc)
            return None
        if resp.status_code != 202:
            logger.warning(
                "trigger_reindex unexpected status %s: %s",
                resp.status_code, resp.text[:200],
            )
            return None
        return resp.json()

    async def get_reindex_status(self) -> dict | None:
        """Fetch the current/most-recent reindex job for this collection.

        Returns the job dict or None (404 = no job yet, connection error,
        unexpected status).
        """
        try:
            resp = await self._client.get(
                f"{self.base_url}/collections/{self.collection_id}/reindex/status",
            )
        except Exception as exc:
            logger.warning("get_reindex_status failed: %s", exc)
            return None
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            logger.warning("get_reindex_status status %s", resp.status_code)
            return None
        return resp.json()

    async def get_reindex_job(self, job_id: str) -> dict | None:
        """Fetch a specific job by id. Returns None on 404 or error."""
        try:
            resp = await self._client.get(
                f"{self.base_url}/collections/{self.collection_id}/reindex/{job_id}",
            )
        except Exception as exc:
            logger.warning("get_reindex_job(%s) failed: %s", job_id, exc)
            return None
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            logger.warning("get_reindex_job(%s) status %s", job_id, resp.status_code)
            return None
        return resp.json()
```

Add at the top of `pal/retrieval.py`, after the existing imports:

```python
import logging

logger = logging.getLogger(__name__)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_retrieval.py -v`
Expected: all tests pass (existing + 7 new).

Also full suite: `python -m pytest tests/ -q 2>&1 | tail -5` — no regressions.

- [ ] **Step 5: Commit**

```bash
git add pal/retrieval.py tests/test_retrieval.py
git commit -m "feat: RetrievalClient reindex methods (best-effort)"
```

---

## Task 2: Inject `retrieval` into `Compiler`; trigger after write

**Files:**
- Modify: `pal/compiler.py`
- Test: `tests/test_compiler.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_compiler.py` (read existing tests first to find the fixture pattern; you'll need to construct a `Compiler` with a fake `retrieval`):

```python
@pytest.mark.asyncio
async def test_compile_one_triggers_reindex_with_target_path(tmp_path, monkeypatch):
    """After a successful first-compile, Compiler calls retrieval.trigger_reindex
    with the absolute path of the new article and includes the response in outcome."""
    # Set up the same minimal Compiler fixture other tests in this file use.
    # If a helper exists, reuse it. Otherwise build inline:
    from unittest.mock import AsyncMock
    from pal.compiler import Compiler
    from pal.wiki import WikiManager

    wiki = WikiManager(tmp_path)
    wiki.init_vault()
    raw_dir = tmp_path / "raw" / "summaries"
    raw_dir.mkdir(parents=True)
    raw_path = raw_dir / "test.md"
    raw_path.write_text(
        "---\ntitle: Test\nsource_url: https://example.com\nsource_hash: abc\n---\n\n"
        "Substantive content for grounded compilation.\n"
    )

    inference = AsyncMock()
    inference.complete = AsyncMock(return_value=type("R", (), {
        "type": "text",
        "content": "## Overview\n\nReal content.\n\n## Key Concepts\n\nA point.",
        "reasoning": "",
    })())

    categorizer = AsyncMock()
    categorizer.categorize = AsyncMock(return_value="Research")

    prompt_builder = type("PB", (), {"build": lambda self: "BASE"})()

    retrieval = AsyncMock()
    retrieval.trigger_reindex = AsyncMock(return_value={
        "job_id": "j1", "status": "queued", "paths": None,
    })

    compiler = Compiler(
        vault_path=tmp_path,
        wiki=wiki,
        inference=inference,
        categorizer=categorizer,
        prompt_builder=prompt_builder,
        retrieval=retrieval,
    )

    outcome = await compiler.compile_one("raw/summaries/test.md")
    assert outcome["status"] == "ok", outcome
    assert "reindex" in outcome
    assert outcome["reindex"]["job_id"] == "j1"

    # Trigger was called with the absolute path of the new article.
    retrieval.trigger_reindex.assert_awaited_once()
    call_kwargs = retrieval.trigger_reindex.await_args.kwargs
    paths = call_kwargs.get("paths") or retrieval.trigger_reindex.await_args.args[0]
    assert any(str(tmp_path) in p and outcome["article_path_rel"] in p for p in paths)


def test_compiler_constructor_default_retrieval_is_none():
    """Compiler constructed without a retrieval kwarg has self.retrieval is None.
    This protects every existing call site from breaking — they all omit the kwarg."""
    from pal.compiler import Compiler
    from unittest.mock import MagicMock
    compiler = Compiler(
        vault_path=Path("/tmp"),
        wiki=MagicMock(),
        inference=MagicMock(),
        categorizer=MagicMock(),
        prompt_builder=MagicMock(),
    )
    assert compiler.retrieval is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_compiler.py -v -k "triggers_reindex or no_retrieval"`
Expected: FAIL (`Compiler() got an unexpected keyword argument 'retrieval'`).

- [ ] **Step 3: Update `Compiler.__init__` and the success branches**

In `pal/compiler.py`, change `Compiler.__init__` to accept an optional retrieval client:

```python
class Compiler:
    def __init__(
        self,
        vault_path: Path,
        wiki,           # WikiManager
        inference,      # InferenceClient
        categorizer,    # Categorizer
        prompt_builder, # SystemPromptBuilder
        retrieval=None, # RetrievalClient | None
    ) -> None:
        self.vault_path = vault_path
        self.wiki = wiki
        self.inference = inference
        self.categorizer = categorizer
        self.prompt_builder = prompt_builder
        self.retrieval = retrieval
```

Find both success branches in `compile_one` — the first-compile path (around line 220) and the merge-into-existing path inside `merge_into_existing` (around line 339). Each currently returns an outcome dict. After the wiki write succeeds, before returning, add:

```python
        outcome = {
            "status": "ok",
            "title": title,
            "article_path_rel": article_path_rel,
            "compiled_truth": compiled_truth,
        }
        if self.retrieval is not None:
            absolute_target = str((self.vault_path / article_path_rel).resolve())
            outcome["reindex"] = await self.retrieval.trigger_reindex(paths=[absolute_target])
        return outcome
```

(Adapt to the actual existing return-shape at each call site; the key addition is the `if self.retrieval is not None` block that adds `reindex` to whatever dict is being returned. Do this for BOTH success returns. Do NOT add it to error/insufficient/not_found/invalid_path branches.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_compiler.py -v`
Expected: all existing tests still pass AND the two new tests pass. Existing `Compiler()` constructions in the test file may need `retrieval=None` added explicitly — only if they currently break; the default is None so they should be unaffected.

Also full suite: `python -m pytest tests/ -q 2>&1 | tail -5` — no regressions.

- [ ] **Step 5: Commit**

```bash
git add pal/compiler.py tests/test_compiler.py
git commit -m "feat: Compiler triggers reindex after successful write"
```

---

## Task 3: Inject `retrieval` into `Consolidator`; trigger after write

**Files:**
- Modify: `pal/consolidator.py`
- Test: `tests/test_consolidator.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_consolidator.py`:

```python
@pytest.mark.asyncio
async def test_consolidate_triggers_reindex_with_target_path(tmp_path):
    from unittest.mock import AsyncMock

    (tmp_path / "Security").mkdir()
    (tmp_path / "Security" / "a.md").write_text("---\ntitle: A\n---\nBody A")
    (tmp_path / "Security" / "b.md").write_text("---\ntitle: B\n---\nBody B")

    inference = _FakeInference("## Overview\n\nFused.\n\n## Key Concepts\n\nA point.")
    wiki = _FakeWiki(tmp_path)
    retrieval = AsyncMock()
    retrieval.trigger_reindex = AsyncMock(return_value={
        "job_id": "j2", "status": "queued",
    })

    c = Consolidator(
        vault_path=tmp_path,
        wiki=wiki,
        inference=inference,
        prompt_builder=_StubPromptBuilder(),
        retrieval=retrieval,
    )

    out = await c.consolidate(
        source_paths=["Security/a.md", "Security/b.md"],
        target_path="Security/Combined.md",
        target_title="Combined",
    )

    assert out["status"] == "ok"
    assert out.get("reindex", {}).get("job_id") == "j2"
    retrieval.trigger_reindex.assert_awaited_once()
    paths = retrieval.trigger_reindex.await_args.kwargs.get("paths") \
        or retrieval.trigger_reindex.await_args.args[0]
    assert any("Security/Combined.md" in p for p in paths)


@pytest.mark.asyncio
async def test_consolidate_no_retrieval_omits_reindex_key(tmp_path):
    (tmp_path / "Security").mkdir()
    (tmp_path / "Security" / "a.md").write_text("---\ntitle: A\n---\nBody A")
    (tmp_path / "Security" / "b.md").write_text("---\ntitle: B\n---\nBody B")

    c, _, _ = _make(tmp_path, inference_response="## Overview\n\nx\n\n## Key Concepts\n\ny")
    out = await c.consolidate(
        source_paths=["Security/a.md", "Security/b.md"],
        target_path="Security/Combined.md",
        target_title="Combined",
    )
    assert out["status"] == "ok"
    assert "reindex" not in out or out.get("reindex") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_consolidator.py -v -k "triggers_reindex or no_retrieval"`
Expected: FAIL.

- [ ] **Step 3: Update `Consolidator.__init__` and `consolidate`**

In `pal/consolidator.py`, change `Consolidator.__init__` to accept the retrieval kwarg:

```python
    def __init__(
        self,
        vault_path: Path,
        wiki,              # WikiManager
        inference,         # InferenceClient
        prompt_builder,    # SystemPromptBuilder
        retrieval=None,    # RetrievalClient | None
    ) -> None:
        self.vault_path = vault_path
        self.wiki = wiki
        self.inference = inference
        self.prompt_builder = prompt_builder
        self.retrieval = retrieval
```

In `consolidate`, find the success-path return (the `return {"status": "ok", ...}` block at the bottom). Just before returning, replace with:

```python
        outcome = {
            "status": "ok",
            "target_path": target_path,
            "article_path_rel": target_path,
            "vault_exists": (self.vault_path / target_path).exists(),
        }
        if self.retrieval is not None:
            absolute_target = str((self.vault_path / target_path).resolve())
            outcome["reindex"] = await self.retrieval.trigger_reindex(paths=[absolute_target])
        return outcome
```

Do NOT add reindex to the insufficient/error/invalid_path/not_found branches.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_consolidator.py -v`
Expected: all 10 tests pass.

Also full suite: no regressions.

- [ ] **Step 5: Commit**

```bash
git add pal/consolidator.py tests/test_consolidator.py
git commit -m "feat: Consolidator triggers reindex after successful write"
```

---

## Task 4: Inject `retrieval` into `Reorganizer`; trigger after ops

**Files:**
- Modify: `pal/reorg.py`
- Modify: `pal/tools.py` (the `_reorg` handler that wraps Reorganizer)
- Test: `tests/test_reorg.py`
- Test: `tests/test_chat_reorg_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_reorg.py`:

```python
@pytest.mark.asyncio
async def test_execute_operations_async_triggers_reindex(tmp_path):
    """After move/merge ops succeed, Reorganizer fires one reindex covering
    all dst paths and returns a dict on the report root."""
    from unittest.mock import AsyncMock
    from pal.reorg import Reorganizer
    from pal.wiki import WikiManager

    wiki = WikiManager(tmp_path)
    wiki.init_vault()

    src = tmp_path / "Research" / "old.md"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("---\ntitle: Old\n---\nBody")

    retrieval = AsyncMock()
    retrieval.trigger_reindex = AsyncMock(return_value={
        "job_id": "rj1", "status": "queued",
    })

    reorg = Reorganizer(vault_path=tmp_path, wiki=wiki, compiler=None, retrieval=retrieval)
    per_op = await reorg.execute_operations_async([
        {"type": "move", "src": "Research/old.md", "dst": "Research/new.md"},
    ])

    assert per_op[0]["status"] == "ok"
    # Each op result carries its own reindex (or there is one aggregate at the call site).
    # Assertion target depends on the design choice — see Step 3.
    retrieval.trigger_reindex.assert_awaited_once()
    paths = retrieval.trigger_reindex.await_args.kwargs.get("paths") \
        or retrieval.trigger_reindex.await_args.args[0]
    # Both src (now gone) and dst (just created) should be in the reindex call,
    # because the server needs to delete the src row AND index the dst.
    assert any("Research/new.md" in p for p in paths)
    assert any("Research/old.md" in p for p in paths)
```

Append to `tests/test_chat_reorg_tools.py` (read the existing tests in that file to find the executor fixture; mirror its setup):

```python
@pytest.mark.asyncio
async def test_reorg_tool_result_includes_reindex(tmp_path):
    """The JSON result returned by the reorg tool includes a 'reindex' key
    when retrieval is wired (Reorganizer stashes _reindex on the first per_op
    entry; the tool handler promotes it to the top-level report)."""
    import json as _json
    from unittest.mock import AsyncMock
    from pal.approval_registry import ApprovalRegistry
    from pal.tools import ToolExecutor

    registry = ApprovalRegistry()

    class _StubReorganizer:
        def __init__(self):
            self.calls = []
        def validate_operations(self, ops):
            return []
        async def execute_operations_async(self, ops):
            self.calls.append(ops)
            return [{
                "op": "move",
                "src": "Research/old.md",
                "dst": "Research/new.md",
                "status": "ok",
                "references_rewritten": 0,
                "_reindex": {"job_id": "rj1", "status": "queued"},
            }]

    emitted = []
    def emit(msg):
        emitted.append(msg)
        registry.approve(msg.proposal_id)

    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        wiki=None,
        approval_registry=registry,
        proposal_emitter=emit,
        reorganizer=_StubReorganizer(),
    )

    propose = await executor.run_async("propose_reorg", {
        "operations": [{"type": "move", "src": "Research/old.md", "dst": "Research/new.md"}],
        "rationale": "tidy",
    })
    pid = _json.loads(propose)["proposal_id"]
    result = await executor.run_async("reorg", {"proposal_id": pid})
    payload = _json.loads(result)
    assert payload["reindex"] == {"job_id": "rj1", "status": "queued"}
    # _reindex was promoted out of per_op into the top-level report
    assert "_reindex" not in payload["per_op"][0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_reorg.py -v -k triggers_reindex`
Expected: FAIL (Reorganizer doesn't accept `retrieval` kwarg).

- [ ] **Step 3: Update `Reorganizer.__init__` and `execute_operations_async`**

In `pal/reorg.py`, change the constructor:

```python
class Reorganizer:
    def __init__(
        self,
        vault_path: Path,
        wiki,              # WikiManager or None
        compiler,          # Compiler or None
        retrieval=None,    # RetrievalClient | None
    ) -> None:
        self.vault_path = vault_path
        self.wiki = wiki
        self.compiler = compiler
        self.retrieval = retrieval
```

At the end of `execute_operations_async`, after the loop completes, gather all touched paths (both src and dst, both unique) and trigger ONE reindex covering them all. The dst paths inform the server about new content; the src paths force the server to recheck them (they no longer exist on disk, so the server's per-file hash check sees `is_file() == False` and skips them — but this only works in the scoped mode if the path is included). Therefore: include both srcs and dsts.

Add a private method `_collect_touched_paths` and call it before returning:

```python
    def _collect_touched_paths(self, per_op: list[dict]) -> list[str]:
        """Absolute paths of every src/dst from successful ops."""
        touched: list[str] = []
        seen: set[str] = set()
        for r in per_op:
            if r.get("status") != "ok":
                continue
            for key in ("src", "dst"):
                rel = r.get(key, "")
                if not rel:
                    continue
                full = str((self.vault_path / rel).resolve())
                if full not in seen:
                    seen.add(full)
                    touched.append(full)
        return touched
```

In `execute_operations_async`, just before the final `return per_op`, wire the trigger:

```python
        if self.retrieval is not None:
            touched = self._collect_touched_paths(per_op)
            if touched:
                reindex_result = await self.retrieval.trigger_reindex(paths=touched)
                # Stash on the first result so callers can find it without
                # changing the function signature. Tool-layer wrapper extracts.
                if per_op and reindex_result is not None:
                    per_op[0]["_reindex"] = reindex_result
        return per_op
```

The leading-underscore key signals "metadata, not per-op data." The tool layer (`_reorg` handler in `tools.py`) extracts and promotes it to a top-level `reindex` field on the report.

In `pal/tools.py`, find the `_reorg` handler. After it builds `report` from `per_op`, extract the underscore key:

```python
        # Promote per-op _reindex (if any) to top-level
        for r in per_op:
            if "_reindex" in r:
                report["reindex"] = r.pop("_reindex")
                break
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_reorg.py tests/test_chat_reorg_tools.py -v`
Expected: all pass.

Also full suite: no regressions.

- [ ] **Step 5: Commit**

```bash
git add pal/reorg.py pal/tools.py tests/test_reorg.py tests/test_chat_reorg_tools.py
git commit -m "feat: Reorganizer triggers reindex after move/merge"
```

---

## Task 5: Wire reindex into `_edit_file` and `_create_file`

**Files:**
- Modify: `pal/tools.py`
- Test: `tests/test_tools.py`

`_edit_file` and `_create_file` write directly through `WikiManager`, not through Compiler/Consolidator. They need their own trigger.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools.py`:

```python
@pytest.mark.asyncio
async def test_create_file_triggers_reindex(wiki_executor_with_retrieval, vault):
    """After a successful create_file, the executor calls retrieval.trigger_reindex
    with the absolute path of the new file."""
    executor, retrieval = wiki_executor_with_retrieval
    result = await executor.run_async("create_file", {
        "path": "raw/notes/scratch.md",
        "title": "Scratch",
        "content": "# Scratch\n\nNote content.\n",
    })
    assert "created" in result.lower()
    retrieval.trigger_reindex.assert_awaited_once()
    paths = retrieval.trigger_reindex.await_args.kwargs.get("paths") \
        or retrieval.trigger_reindex.await_args.args[0]
    assert any("raw/notes/scratch.md" in p for p in paths)


@pytest.mark.asyncio
async def test_edit_file_triggers_reindex(wiki_executor_with_retrieval, vault):
    """After a successful edit_file, the executor calls retrieval.trigger_reindex."""
    executor, retrieval = wiki_executor_with_retrieval
    # Seed the file
    (vault / "raw" / "notes").mkdir(parents=True, exist_ok=True)
    (vault / "raw" / "notes" / "n.md").write_text(
        "---\ntitle: N\n---\nold body"
    )
    retrieval.trigger_reindex.reset_mock()  # ignore any prior calls
    result = await executor.run_async("edit_file", {
        "path": "raw/notes/n.md",
        "content": "new body",
    })
    assert "updated" in result.lower() or "edit" in result.lower()
    retrieval.trigger_reindex.assert_awaited_once()
```

The `wiki_executor_with_retrieval` fixture is new — add it to `tests/conftest.py` or near the existing `wiki_executor` fixture in `tests/test_tools.py`. It mirrors `wiki_executor` but additionally injects an `AsyncMock` retrieval client into the `ToolExecutor`. Since `_edit_file`/`_create_file` are sync methods today, you'll need to make them async (small change) or have the executor's reindex call happen via `asyncio.create_task`. Cleanest: convert these two handlers to async and dispatch them through `run_async`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_tools.py -v -k "create_file_triggers or edit_file_triggers"`
Expected: FAIL (no fixture, or sync handler can't await).

- [ ] **Step 3: Convert `_edit_file` and `_create_file` to async, register in `run_async`**

In `pal/tools.py`:

1. Change `_edit_file` and `_create_file` from `def` to `async def`.
2. Move their dispatch entries from the `run` handler dict to the `run_async` `if/elif` chain (mirror how `_search_vault` is already async-only).
3. After each successful write, before returning the success string, add:

```python
        if self.retrieval is not None:
            absolute = str((self.vault_path / path).resolve())
            await self.retrieval.trigger_reindex(paths=[absolute])
```

For backward compat, the `run` (sync) dispatch can keep returning an "Error: edit_file is async, call via run_async" so any stale caller surfaces clearly.

- [ ] **Step 4: Add the fixture**

In `tests/test_tools.py` (or `tests/conftest.py`), add:

```python
@pytest.fixture
def wiki_executor_with_retrieval(vault):
    from unittest.mock import AsyncMock
    from pal.tools import ToolExecutor
    from pal.wiki import WikiManager

    wiki = WikiManager(vault)
    wiki.init_vault()
    retrieval = AsyncMock()
    retrieval.trigger_reindex = AsyncMock(return_value={
        "job_id": "j", "status": "queued",
    })
    executor = ToolExecutor(
        vault_path=vault,
        retrieval=retrieval,
        wiki=wiki,
    )
    return executor, retrieval
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_tools.py -v`
Expected: all pass. Existing `_create_file`/`_edit_file` tests that called via `run` will need updating to `run_async` or you keep `run` as a thin wrapper that delegates. Pick whichever yields the smallest diff against existing tests.

Also full suite: no regressions.

- [ ] **Step 6: Commit**

```bash
git add pal/tools.py tests/test_tools.py tests/conftest.py
git commit -m "feat: edit_file/create_file trigger reindex"
```

---

## Task 6: Wire daemon-level slash command writes (`/note` and `/import` only)

**Files:**
- Modify: `pal/daemon.py`

`/note` (around line 605) and `/import` (around line 1158) write to user-visible category directories that are part of the inference server's indexed `source_dir`. These should trigger reindex.

`/learn` (line 1253) and `/promote` (line 1299) write to `_learning/` and `_wisdom/` — system directories prefixed with underscore. These are NOT part of the indexed source_dir (or if the user has configured the server to include them, they can trigger a full reindex manually). Skip these two.

- [ ] **Step 1: Add a `_trigger_reindex_for_paths` helper on `Daemon`**

In `pal/daemon.py`, inside the `Daemon` class, add (anywhere among the helper methods is fine):

```python
    async def _trigger_reindex_for_paths(self, paths: list[str]) -> None:
        """Best-effort reindex trigger for direct daemon writes (slash commands).
        Logs warnings on failure; never raises."""
        if not paths:
            return
        try:
            await self.retrieval.trigger_reindex(paths=paths)
        except Exception as exc:
            logger.warning("daemon reindex trigger failed: %s", exc)
```

- [ ] **Step 2: Wire `/note`**

In `/note`'s handler around line 607 (right after `self.wiki.git_commit(f"note: {topic}")`), add:

```python
        absolute = str((self.config.vault_path / path).resolve())
        await self._trigger_reindex_for_paths([absolute])
```

The variable `path` already holds the relative article path (`f"{category}/{slug}.md"`).

- [ ] **Step 3: Wire `/import`**

In `/import`'s handler around line 1158 (right after `self.wiki.git_commit(f"import: ...")`), add:

```python
        absolute_paths = [
            str((self.config.vault_path / rel).resolve())
            for rel in saved_articles
        ]
        await self._trigger_reindex_for_paths(absolute_paths)
```

`saved_articles` is the list of relative paths the import handler accumulates as it writes each chunk.

- [ ] **Step 4: Run the existing daemon tests**

Run: `cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_daemon.py tests/test_research_commands.py tests/test_learning_commands.py tests/test_wisdom_commands.py -v`
Expected: PASS. Slash command tests should not regress because the helper is best-effort.

Also full suite: no regressions.

- [ ] **Step 5: Commit**

```bash
git add pal/daemon.py
git commit -m "feat: /note and /import trigger reindex"
```

---

## Task 7: Daemon constructor wires `retrieval` into business classes

**Files:**
- Modify: `pal/daemon.py`

- [ ] **Step 1: Update Compiler/Consolidator/Reorganizer construction in `Daemon.__init__`**

Find the three construction sites in `pal/daemon.py::Daemon.__init__`:

- `Compiler(...)` around line 93
- `Consolidator(...)` around line 107
- `Reorganizer(...)` around line 100

Add `retrieval=self.retrieval` as a kwarg to each.

- [ ] **Step 2: Run regression tests**

Run: `cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/ -q 2>&1 | tail -5`
Expected: no regressions. (Existing Compiler/Consolidator/Reorganizer tests pass `retrieval=None` by default; the daemon-level wire-up is additive.)

- [ ] **Step 3: Commit**

```bash
git add pal/daemon.py
git commit -m "feat: daemon injects RetrievalClient into Compiler/Consolidator/Reorganizer"
```

---

## Task 8: New `wait_for_reindex` tool

**Files:**
- Modify: `pal/tools.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools.py`:

```python
@pytest.mark.asyncio
async def test_wait_for_reindex_returns_done_when_finished(tmp_path):
    """Polls until the job reports done; returns the final status."""
    from unittest.mock import AsyncMock
    from pal.tools import ToolExecutor

    retrieval = AsyncMock()
    # First two polls: still running. Third: done.
    retrieval.get_reindex_job = AsyncMock(side_effect=[
        {"job_id": "j", "status": "running"},
        {"job_id": "j", "status": "running"},
        {"job_id": "j", "status": "done", "stats": {"new": 1}},
    ])
    executor = ToolExecutor(vault_path=tmp_path, retrieval=retrieval)

    result = await executor.run_async("wait_for_reindex", {
        "job_id": "j",
        "timeout_seconds": 5,
    })
    import json as _json
    payload = _json.loads(result)
    assert payload["status"] == "done"
    assert payload["job_id"] == "j"
    assert retrieval.get_reindex_job.await_count == 3


@pytest.mark.asyncio
async def test_wait_for_reindex_times_out(tmp_path):
    from unittest.mock import AsyncMock
    from pal.tools import ToolExecutor

    retrieval = AsyncMock()
    retrieval.get_reindex_job = AsyncMock(return_value={"job_id": "j", "status": "running"})
    executor = ToolExecutor(vault_path=tmp_path, retrieval=retrieval)

    result = await executor.run_async("wait_for_reindex", {
        "job_id": "j",
        "timeout_seconds": 1,
    })
    import json as _json
    payload = _json.loads(result)
    assert payload["status"] == "timeout"
    assert payload["last_seen_status"] == "running"


@pytest.mark.asyncio
async def test_wait_for_reindex_unknown_job(tmp_path):
    from unittest.mock import AsyncMock
    from pal.tools import ToolExecutor

    retrieval = AsyncMock()
    retrieval.get_reindex_job = AsyncMock(return_value=None)
    executor = ToolExecutor(vault_path=tmp_path, retrieval=retrieval)

    result = await executor.run_async("wait_for_reindex", {
        "job_id": "missing",
        "timeout_seconds": 1,
    })
    assert "unknown job" in result.lower() or "not found" in result.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_tools.py -v -k wait_for_reindex`
Expected: FAIL (`Unknown tool: wait_for_reindex`).

- [ ] **Step 3: Add the tool spec and handler**

In `pal/tools.py`, add to the `TOOL_DEFINITIONS` list (near the consolidate tool, since these are all retrieval-related):

```python
    {
        "type": "function",
        "function": {
            "name": "wait_for_reindex",
            "description": (
                "Poll a reindex job until it finishes or times out. Use after "
                "compile/consolidate/reorg/edit/create when you need to be sure "
                "the new content is searchable via search_vault before answering. "
                "Most of the time you do NOT need this — the reindex runs "
                "automatically and finishes within a second or two. Call this "
                "only when latency to-searchable matters for the next answer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "job_id from a prior tool result's reindex field.",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Max seconds to wait. Default 30.",
                    },
                },
                "required": ["job_id"],
            },
        },
    },
```

In `run_async`, add the dispatch branch:

```python
        if name == "wait_for_reindex":
            return await self._wait_for_reindex(arguments)
```

Add the handler:

```python
    async def _wait_for_reindex(self, arguments: dict) -> str:
        import asyncio
        import json as _json

        job_id = (arguments.get("job_id") or "").strip()
        if not job_id:
            return "Error: 'job_id' parameter is required."
        if self.retrieval is None:
            return "Error: retrieval client is not configured."

        timeout_seconds = int(arguments.get("timeout_seconds") or 30)
        timeout_seconds = max(1, min(timeout_seconds, 120))  # clamp 1-120s

        deadline = asyncio.get_event_loop().time() + timeout_seconds
        last_status = "unknown"
        while True:
            job = await self.retrieval.get_reindex_job(job_id)
            if job is None:
                return f"Error: unknown job_id (not found): {job_id}"
            last_status = job.get("status", "unknown")
            if last_status in ("done", "error"):
                return _json.dumps(job)
            if asyncio.get_event_loop().time() >= deadline:
                return _json.dumps({
                    "job_id": job_id,
                    "status": "timeout",
                    "last_seen_status": last_status,
                    "_note": (
                        "Job did not finish within timeout. The job may still complete; "
                        "poll again with a longer timeout if needed."
                    ),
                })
            await asyncio.sleep(0.25)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_tools.py -v -k wait_for_reindex`
Expected: all 3 new tests pass.

Also full suite: no regressions.

- [ ] **Step 5: Commit**

```bash
git add pal/tools.py tests/test_tools.py
git commit -m "feat: wait_for_reindex tool"
```

---

## Task 9: Prompt + README updates

**Files:**
- Modify: `pal/prompt_builder.py`
- Modify: `README.md`
- Test: `tests/test_prompt_builder.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_prompt_builder.py`:

```python
def test_base_prompt_mentions_wait_for_reindex():
    from pal.prompt_builder import BASE_PROMPT
    assert "wait_for_reindex" in BASE_PROMPT
    assert "reindex" in BASE_PROMPT.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_prompt_builder.py::test_base_prompt_mentions_wait_for_reindex -v`
Expected: FAIL.

- [ ] **Step 3: Update `BASE_PROMPT`**

In `pal/prompt_builder.py`, find the "Wiki promotion (grounded, source-linked)" section and add a new bullet at the bottom of that block:

```
- wait_for_reindex: poll a reindex job (job_id from a prior tool result's `reindex` field) until done or timeout. Use only when you need new content to be searchable BEFORE your next answer; usually unnecessary because reindex runs automatically and finishes within a second or two.
```

Then update the existing `## Honesty rules` section's "index freshness" caveat (currently says the index updates only on server restart). Replace that bullet with:

```
- After a write tool succeeds, its result includes a `reindex` field with a `job_id` and current `status`. The inference server reindexes the new content automatically; the `status` field tells you whether it has finished. You normally do not need to wait — by the time the next user message arrives, the reindex will be done. Call wait_for_reindex only when you need to search_vault for the just-written content within the SAME response.
```

- [ ] **Step 4: Update `README.md`**

In `/home/edible/Projects/PAL/README.md`, find the "Chat Tools" table (around line 139). Add a row:

```
| `wait_for_reindex` | Poll a reindex job until done or timeout (use only when freshness matters mid-turn) |
```

Below the table, add a short paragraph (after the "Write tools are restricted..." line):

```
Write tools (`compile_summary`, `compile_batch`, `consolidate`, `reorg`, `create_file`, `edit_file`) automatically trigger an incremental reindex on the inference server after success. The tool result includes a `reindex` field with a `job_id` and current status; the new content is typically searchable within a second or two without any further action. For mid-turn cases that require certainty, `wait_for_reindex` polls until the job completes.
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_prompt_builder.py -v`
Expected: PASS.

Full suite: no regressions.

- [ ] **Step 6: Commit**

```bash
git add pal/prompt_builder.py README.md tests/test_prompt_builder.py
git commit -m "docs: prompt + README cover reindex flow and wait_for_reindex"
```

---

## Task 10: End-to-end smoke test against the live server

**Files:** none.

- [ ] **Step 1: Restart the daemon** so it picks up the new code (the user does this; do not attempt yourself).

- [ ] **Step 2: In Discord, run a simple compile or consolidate**

Ask PAL to consolidate two existing articles. The proposal should appear normally. After approving, the tool result should include something like:

```
reindex: {job_id: "...", status: "queued", paths: ["/mnt/.../target.md"]}
```

(Surfaced naturally in PAL's response narration.)

- [ ] **Step 3: Verify reindex landed**

```bash
# Use the job_id from PAL's response
curl -s http://192.168.1.14:11434/collections/vault/reindex/<job_id> | python3 -m json.tool
```

Expected: `status: done`, `stats.new` or `stats.updated` is 1.

- [ ] **Step 4: Verify the new article is searchable**

```bash
curl -s -X POST http://192.168.1.14:11434/collections/vault/search \
  -H 'Content-Type: application/json' \
  -d '{"query": "<topic of the new article>", "limit": 5}' | python3 -m json.tool
```

Expected: the new article appears in the results.

- [ ] **Step 5: Test `wait_for_reindex` from chat**

In Discord, ask PAL: "After your next consolidate, use wait_for_reindex to confirm it's searchable, then search_vault to verify."

Expected: PAL chains `consolidate` → `wait_for_reindex` → `search_vault` and reports the new article in the search results in a single response.

---

## Notes on scope explicitly excluded from this plan

- **Per-tool reindex opt-out.** Every write tool triggers reindex unconditionally. If a future workflow needs to defer reindexing (e.g., a bulk import where the model knows it'll fire 50 writes), we can add an opt-out flag then. Not now.
- **Aggregate batch reindex.** `compile_batch` triggers reindex per file (since `Compiler.compile_one` is per-call). For 10 files this is 10 trigger calls. The server handles this fine because the lock returns the in-flight job — all 10 calls coalesce onto the first job started. No optimization needed.
- **Reindex on read.** Out of scope — adds latency to every search. The server's startup-and-on-demand reindex model is the deliberate choice.
- **Cross-collection reindex.** Each `RetrievalClient` instance is bound to one collection_id. The PAL daemon has one client (`collection_id="vault"`); `skills` collection is the inference server's concern.
