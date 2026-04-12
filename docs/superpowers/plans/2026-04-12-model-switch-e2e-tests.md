# Model Switch End-to-End Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two end-to-end tests that prove `/model <name>` propagates to subsequent `/summarize` and `/compile` operations by inspecting the payloads the mock inference server receives.

**Architecture:** Instrument the existing mock server in `tests/conftest.py` with a module-level `REQUEST_LOG` that captures every `/v1/chat/completions` body. Add an autouse fixture that clears the log before each test. Add two integration tests in `tests/test_daemon.py` that switch the model via `/model`, trigger a background operation, and assert the captured payloads carry the switched model name.

**Tech Stack:** Python 3.12, pytest + pytest-asyncio, Starlette (existing mock server), PalClient (existing test helper)

---

## File Structure

```
tests/
├── conftest.py          # MODIFY — add REQUEST_LOG, append in mock, add autouse clear fixture
├── test_daemon.py       # MODIFY — add daemon-with-vault fixture + 2 tests
```

No production code changes.

---

### Task 1: Add REQUEST_LOG to Mock Server and Autouse Clear Fixture

**Files:**
- Modify: `tests/conftest.py`

- [ ] **Step 1: Add module-level REQUEST_LOG at top of conftest.py**

Find the imports block near the top of `tests/conftest.py` (after `import uvicorn`). After the existing imports, before the first `async def mock_*` function, add:

```python
# Captures every /v1/chat/completions body the daemon sends to the mock server.
# Tests that care about what model/payload hit the wire read from this list.
# Cleared automatically before each test by the autouse _clear_request_log fixture.
REQUEST_LOG: list[dict] = []
```

- [ ] **Step 2: Append to REQUEST_LOG at entry of mock_chat_completions**

Find the `mock_chat_completions` function (starts around line 19 of `tests/conftest.py`). Its current first two lines:

```python
async def mock_chat_completions(request: Request):
    """Mock OpenAI-compatible /v1/chat/completions endpoint."""
    body = await request.json()
```

Add a third line immediately after `body = await request.json()`:

```python
    REQUEST_LOG.append(body)
```

Result:

```python
async def mock_chat_completions(request: Request):
    """Mock OpenAI-compatible /v1/chat/completions endpoint."""
    body = await request.json()
    REQUEST_LOG.append(body)
    stream = body.get("stream", False)
```

- [ ] **Step 3: Add autouse fixture that clears the log**

At the bottom of `tests/conftest.py` (after the `running_daemon` fixture), add:

```python
@pytest.fixture(autouse=True)
def _clear_request_log():
    """Clear REQUEST_LOG before each test to prevent cross-test leakage."""
    REQUEST_LOG.clear()
    yield
```

Autouse=True applies to every test. The log is only appended to by `mock_chat_completions`, so this has no effect on tests that don't use the mock server.

- [ ] **Step 4: Verify existing tests still pass**

Run: `cd /home/edible/Projects/PAL && .venv/bin/pytest tests/ -v`
Expected: all 380 tests still pass. The instrumentation should not affect any existing test.

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py
git commit -m "test: instrument mock server with REQUEST_LOG capture

Adds module-level REQUEST_LOG list that captures every chat/completions
body the daemon sends. Autouse fixture clears it before each test.
Enables end-to-end tests that verify the model name hitting the wire."
```

---

### Task 2: Add Daemon-with-Vault Fixture to test_daemon.py

**Files:**
- Modify: `tests/test_daemon.py`

- [ ] **Step 1: Add a fixture that provides a daemon with a vault path**

The existing `running_daemon` fixture in `tests/conftest.py` doesn't set `vault_path`, so `/summarize` and `/compile` paths that write to the vault won't work with it. Add a local fixture to `tests/test_daemon.py` that provides a daemon with an isolated vault.

Find the `# ---------- /model command tests ----------` marker near line 106 of `tests/test_daemon.py`. Below the existing `from pal.client import PalClient` line, add:

```python
from pal.config import Config
from pal.daemon import Daemon


@pytest.fixture()
async def model_switch_daemon(socket_path, mock_inference_server, tmp_path):
    """Daemon with a tmp vault, for /model + background-op end-to-end tests."""
    cfg = Config(
        inference_url=mock_inference_server,
        model="test-model",
        socket_path=socket_path,
        history_depth=50,
        vault_path=tmp_path / "vault",
        collection_id="vault",
        username="testuser",
        searxng_url=mock_inference_server,
        fetch_max_bytes=2_000_000,
        fetch_timeout=10,
    )
    daemon = Daemon(cfg)
    task = asyncio.create_task(daemon.serve())
    for _ in range(100):
        if socket_path.exists():
            break
        await asyncio.sleep(0.01)
    yield daemon, tmp_path / "vault"
    daemon.shutdown()
    await task
```

- [ ] **Step 2: Verify the new fixture doesn't break existing tests**

Run: `cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_daemon.py -v`
Expected: all 9 existing daemon tests still pass. The new fixture is unused so far.

- [ ] **Step 3: Commit**

```bash
git add tests/test_daemon.py
git commit -m "test: add model_switch_daemon fixture with vault path

Standalone fixture for tests that need to run /summarize or /compile
end-to-end (vault writes required). Mirrors the pattern in test_summarize
and test_compile but lives in test_daemon.py where the model-switch
tests will live."
```

---

### Task 3: Add End-to-End Test for /summarize

**Files:**
- Modify: `tests/test_daemon.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_daemon.py`, after the existing `/model` tests:

```python
@pytest.mark.asyncio
async def test_model_switch_routes_summarize_to_new_model(model_switch_daemon, socket_path):
    """/model <name> must propagate to subsequent /summarize inference calls."""
    from pal.frontmatter import serialize_frontmatter
    from tests.conftest import REQUEST_LOG

    daemon, vault = model_switch_daemon

    # Write a raw file to the vault for /summarize to consume
    raw_dir = vault / "raw" / "web"
    raw_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "source_url": "https://example.com/test",
        "title": "Test Article",
        "fetched_at": "2026-04-05T12:00:00+00:00",
        "content_hash": "abc123",
        "byte_size": 100,
        "status": "raw",
    }
    raw_path = raw_dir / "test.md"
    raw_path.write_text(serialize_frontmatter(meta, "This is the article body.\n"))

    client = PalClient(socket_path)
    await client.connect()

    # Switch the active model
    await client.command("model", "gemma-4-26b-a4b-it-q4_k_m")

    # Clear any requests captured during /model validation + switch
    REQUEST_LOG.clear()

    # Trigger /summarize - this should hit the inference server with the new model
    await client.command("summarize", "raw/web/test.md")

    await client.close()

    # Every captured chat/completions request must carry the switched model
    assert len(REQUEST_LOG) >= 1, "expected at least one inference request during /summarize"
    for entry in REQUEST_LOG:
        assert entry.get("model") == "gemma-4-26b-a4b-it-q4_k_m", (
            f"expected model=gemma-4-26b-a4b-it-q4_k_m, got {entry.get('model')}"
        )
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_daemon.py::test_model_switch_routes_summarize_to_new_model -v`
Expected: PASS. The active-model refactor was already shipped, so this test should pass immediately. If it fails with the wrong model name, that indicates a regression in the refactor.

- [ ] **Step 3: Commit**

```bash
git add tests/test_daemon.py
git commit -m "test: verify /summarize uses switched model end-to-end

Writes a raw file, calls /model <name>, then /summarize. Inspects the
payload the mock server received and asserts it carries the new model.
Closes regression-coverage gap in the active-model refactor."
```

---

### Task 4: Add End-to-End Test for /compile

**Files:**
- Modify: `tests/test_daemon.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_daemon.py` after the previous test:

```python
@pytest.mark.asyncio
async def test_model_switch_routes_compile_to_new_model(model_switch_daemon, socket_path):
    """/model <name> must propagate across all inference calls inside /compile.

    /compile makes multiple inference calls: categorize, then compile (and
    topic-match when applicable). Every call must use the switched model.
    """
    from pal.frontmatter import serialize_frontmatter
    from tests.conftest import REQUEST_LOG

    daemon, vault = model_switch_daemon

    # Write a summary file to the vault for /compile to consume
    summaries_dir = vault / "raw" / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    summary_meta = {
        "title": "Test Article",
        "source_url": "https://example.com/test",
        "source_raw": "raw/web/test.md",
        "source_hash": "abc123",
        "summarized_at": "2026-04-05T12:00:00+00:00",
        "sanitization_issues": [],
        "status": "summary",
    }
    summary_path = summaries_dir / "test.md"
    summary_path.write_text(serialize_frontmatter(summary_meta, "Summary body about the article.\n"))

    client = PalClient(socket_path)
    await client.connect()

    # Switch the active model
    await client.command("model", "gemma-4-26b-a4b-it-q4_k_m")

    # Clear any requests captured during /model validation + switch
    REQUEST_LOG.clear()

    # Trigger /compile - this hits inference server multiple times
    await client.command("compile", "raw/summaries/test.md")

    await client.close()

    # /compile must produce at least 2 inference requests (categorize + compile);
    # with an empty target directory, topic matching short-circuits without a model call.
    assert len(REQUEST_LOG) >= 2, (
        f"expected at least 2 inference requests during /compile, got {len(REQUEST_LOG)}"
    )
    for entry in REQUEST_LOG:
        assert entry.get("model") == "gemma-4-26b-a4b-it-q4_k_m", (
            f"expected model=gemma-4-26b-a4b-it-q4_k_m on every request, "
            f"got {entry.get('model')}"
        )
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_daemon.py::test_model_switch_routes_compile_to_new_model -v`
Expected: PASS. Every inference request in the /compile flow should carry the switched model.

- [ ] **Step 3: Run full test suite to confirm no regressions**

Run: `cd /home/edible/Projects/PAL && .venv/bin/pytest tests/ -v`
Expected: 382 tests pass (380 + 2 new).

- [ ] **Step 4: Commit**

```bash
git add tests/test_daemon.py
git commit -m "test: verify /compile uses switched model across all calls

/compile fires multiple inference requests (categorize + compile,
and topic-match when articles exist). Asserts every captured
request carries the switched model, catching any future regression
where a call site bypasses inference.default_model."
```

---

## Self-Review

**Spec coverage:**
- Mock server instrumentation → Task 1
- Test-local state management (autouse fixture) → Task 1 Step 3 (placed in conftest for simplicity; the spec suggested per-file but global autouse is equivalent and simpler since only chat/completions writes to the log)
- `test_model_switch_routes_summarize_to_new_model` → Task 3
- `test_model_switch_routes_compile_to_new_model` → Task 4
- Files modified: conftest.py, test_daemon.py ✓

**Placeholder scan:** No TBD/TODO. All code blocks complete.

**Type consistency:** `REQUEST_LOG` is a `list[dict]` throughout. `model_switch_daemon` fixture yields `(daemon, vault)` tuple consistently. `PalClient`, `Config`, `Daemon` imports match their existing usage.

**One spec deviation noted:** The spec suggested the autouse clear fixture live in each test file that uses REQUEST_LOG. I placed it in `conftest.py` instead so it applies globally. Rationale: only `mock_chat_completions` writes to the log, so clearing before every test (including tests that don't inspect the log) has zero side effects and is simpler to maintain.
