# Phase B: PAL Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire PAL to a second inference backend (the batch endpoint) without disturbing chat, route three initial callers (categorizer, learning scanner, `detect_from_llm_toc`) through it, and surface batch unavailability as a user-facing proposal for interactive callers or a silent skip for the background scanner.

**Architecture:** Add a second `InferenceClient` instance (`self.batch_inference`) to the daemon, guarded by `PAL_BATCH_ENABLED`. Each offloaded caller accepts an `inference` argument injected by the daemon; when the flag is off, they receive the main client (no behavior change). A new `BatchUnavailableError` exception is raised by the client on batch-endpoint failure and handled per-caller. User-facing callers emit a `BatchFallbackProposal` via the existing `approval_registry` infrastructure with retry / run-on-main / skip options; the learning scanner silently logs and skips.

**Tech Stack:** Python 3.12, httpx, pytest + pytest-asyncio, discord.py (existing), existing PAL protocol / approval_registry / inference client.

**Reference spec:** `docs/superpowers/specs/2026-04-19-phase-b-dual-backend-batch-model-design.md`

**Prerequisite for end-to-end usage:** server-side Phase B (separate plan, `~/Projects/inference_server` repo) to stand up the batch llama-server backend and teach the manager to route by model name. Until that ships, this plan is gated by `PAL_BATCH_ENABLED=false` and introduces no runtime behavior change.

---

## File Structure

### New files

- `tests/test_batch_inference.py`: Unit tests for `BatchUnavailableError` classification and the `batch_inference` wiring.
- `tests/test_batch_fallback_proposal.py`: Unit tests for `BatchFallbackProposal` protocol and approval flow.

### Modified files

- `pal/config.py`: Adds `batch_enabled`, `batch_inference_url`, `batch_model` fields and env loaders.
- `pal/inference.py`: Adds `BatchUnavailableError` exception; classifies httpx errors into that type when the client is configured as batch.
- `pal/daemon.py`: Constructs `self.batch_inference` when `batch_enabled`; injects it into Categorizer / LearningScanner / `detect_chapters` calls; handles `BatchFallbackProposal` approvals.
- `pal/categorizer.py`: Changes `categorize` to accept an `inference` override, or alternatively reads `self.inference` set at construction. Existing call sites pass batch client when enabled.
- `pal/learning_scanner.py`: Catches `BatchUnavailableError`, logs warning, returns "no candidate."
- `pal/pdf_structure.py`: No signature change; the caller (daemon) already passes the inference client. Document that `detect_from_llm_toc` raising is caught upstream by the daemon's PDF import path.
- `pal/protocol.py`: Adds `BatchFallbackProposal` dataclass and serializer entry.
- `pal/approval_registry.py`: Adds `batch_fallback` proposal kind support.
- `pal/commands.py`: `/model` help text includes `--target batch` syntax.
- `pal/daemon.py::_handle_model`: Parses `--target` option, dispatches to manager with the target field; renders status with both slots.
- `pal/client.py` / `pal/discord_interactions.py`: Wire up the new proposal message shape for CLI and Discord.

### Files NOT touched

- `pal/compiler.py`, `pal/consolidator.py`, `pal/summarizer.py`, `pal/researcher.py`: explicitly NOT moved to batch in Phase B.
- `pal/tools.py`: no new tool; the batch fallback proposal is driven by internal callers, not LLM-initiated.

---

## Task 1: Config fields for batch inference

**Files:**
- Modify: `pal/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_config.py`:

```python
def test_config_default_batch_disabled():
    cfg = Config()
    assert cfg.batch_enabled is False
    assert cfg.batch_inference_url == "http://192.168.1.14:11434"
    assert cfg.batch_model == "gemma-3-4b-it-q4_k_m"


def test_config_env_enables_batch(monkeypatch):
    monkeypatch.setenv("PAL_BATCH_ENABLED", "true")
    monkeypatch.setenv("PAL_BATCH_INFERENCE_URL", "http://localhost:9000")
    monkeypatch.setenv("PAL_BATCH_MODEL", "qwen3-4b-instruct")
    cfg = load_config()
    assert cfg.batch_enabled is True
    assert cfg.batch_inference_url == "http://localhost:9000"
    assert cfg.batch_model == "qwen3-4b-instruct"


def test_config_env_batch_enabled_falsy_values(monkeypatch):
    """PAL_BATCH_ENABLED only enables on explicit true-ish strings."""
    for v in ("false", "0", "no", ""):
        monkeypatch.setenv("PAL_BATCH_ENABLED", v)
        cfg = load_config()
        assert cfg.batch_enabled is False, f"value {v!r} should leave batch disabled"
    for v in ("true", "1", "yes", "TRUE"):
        monkeypatch.setenv("PAL_BATCH_ENABLED", v)
        cfg = load_config()
        assert cfg.batch_enabled is True, f"value {v!r} should enable batch"
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_config.py -v
```

Expected: three new tests fail with AttributeError on `batch_enabled`.

- [ ] **Step 3: Implement the new config fields**

In `pal/config.py`, inside the `Config` dataclass:

```python
    batch_enabled: bool = False
    batch_inference_url: str = "http://192.168.1.14:11434"
    batch_model: str = "gemma-3-4b-it-q4_k_m"
```

In the `load_config()` body, after existing env reads:

```python
    if (v := os.environ.get("PAL_BATCH_ENABLED")) is not None:
        kwargs["batch_enabled"] = v.strip().lower() in ("true", "1", "yes")
    if url := os.environ.get("PAL_BATCH_INFERENCE_URL"):
        kwargs["batch_inference_url"] = url
    if model := os.environ.get("PAL_BATCH_MODEL"):
        kwargs["batch_model"] = model
```

- [ ] **Step 4: Run tests to confirm pass**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_config.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add pal/config.py tests/test_config.py
git commit -m "feat: add batch inference config fields (batch_enabled, batch_inference_url, batch_model)"
```

---

## Task 2: BatchUnavailableError exception and classification

**Files:**
- Modify: `pal/inference.py`
- Create: `tests/test_batch_inference.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_batch_inference.py`:

```python
"""Tests for batch-specific inference failure classification."""
import httpx
import pytest
from unittest.mock import AsyncMock

from pal.inference import InferenceClient, BatchUnavailableError


@pytest.mark.asyncio
async def test_batch_client_raises_batch_unavailable_on_connect_error(monkeypatch):
    client = InferenceClient(
        base_url="http://127.0.0.1:9999",
        model="test-batch-model",
        is_batch=True,
    )

    async def fake_post(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(client._client, "post", fake_post)

    with pytest.raises(BatchUnavailableError, match="connection refused"):
        await client.complete([{"role": "user", "content": "x"}])

    await client.close()


@pytest.mark.asyncio
async def test_batch_client_raises_batch_unavailable_on_503(monkeypatch):
    client = InferenceClient(
        base_url="http://127.0.0.1:9999",
        model="test-batch-model",
        is_batch=True,
    )

    async def fake_post(*args, **kwargs):
        request = httpx.Request("POST", "http://test/")
        return httpx.Response(status_code=503, request=request, text="batch slot unhealthy")

    monkeypatch.setattr(client._client, "post", fake_post)
    # Disable retry sleep to keep test fast.
    monkeypatch.setattr("pal.inference._INITIAL_BACKOFF", 0)
    monkeypatch.setattr("pal.inference._MAX_BACKOFF", 0)

    with pytest.raises(BatchUnavailableError):
        await client.complete([{"role": "user", "content": "x"}])

    await client.close()


@pytest.mark.asyncio
async def test_non_batch_client_raises_original_exception(monkeypatch):
    """A non-batch InferenceClient should raise the original httpx error,
    not wrap it as BatchUnavailableError."""
    client = InferenceClient(
        base_url="http://127.0.0.1:9999",
        model="test-main-model",
    )

    async def fake_post(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(client._client, "post", fake_post)

    with pytest.raises(httpx.ConnectError):
        await client.complete([{"role": "user", "content": "x"}])

    await client.close()
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_batch_inference.py -v
```

Expected: `ImportError: cannot import name 'BatchUnavailableError'`.

- [ ] **Step 3: Add BatchUnavailableError and is_batch flag**

In `pal/inference.py`, near the top of the file (after imports, before class definitions):

```python
class BatchUnavailableError(RuntimeError):
    """Raised when a batch-mode InferenceClient cannot reach the batch
    backend (connection error, repeated 503, or timeout past retries).

    Callers distinguish this from other RuntimeErrors to decide between
    silent-skip (background scanners) or user-facing fallback proposals
    (interactive callers).
    """
```

Update `InferenceClient.__init__` to accept `is_batch`:

```python
class InferenceClient:
    def __init__(self, base_url: str, model: str, is_batch: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_model = model
        self.is_batch = is_batch
        self._client = httpx.AsyncClient(timeout=600.0)
```

Update `_post_with_retry` to wrap exceptions for batch clients. Find the existing implementation (around line 75) and replace its body with:

```python
    async def _post_with_retry(self, payload: dict) -> httpx.Response:
        """POST to /v1/chat/completions with exponential backoff on 503.

        For batch clients, any unrecoverable failure (connection error,
        repeated 503, timeout) is re-raised as BatchUnavailableError so
        callers can distinguish batch-backend outages from other errors.
        """
        url = f"{self.base_url}/v1/chat/completions"
        backoff = _INITIAL_BACKOFF
        try:
            for attempt in range(_MAX_RETRIES):
                resp = await self._client.post(url, json=payload)
                if resp.status_code != 503:
                    resp.raise_for_status()
                    return resp
                retry_after = float(resp.headers.get("Retry-After", backoff))
                wait = min(retry_after, _MAX_BACKOFF)
                logger.warning(
                    "503 from inference server (attempt %d/%d), retrying in %.1fs",
                    attempt + 1, _MAX_RETRIES, wait,
                )
                await asyncio.sleep(wait)
                backoff = min(backoff * 2, _MAX_BACKOFF)
            # Final attempt - let it raise on any error
            resp = await self._client.post(url, json=payload)
            resp.raise_for_status()
            return resp
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            if self.is_batch:
                raise BatchUnavailableError(str(exc)) from exc
            raise
```

Also update `_stream_with_retry` to convert errors similarly (find the existing definition and replace its body). The streaming client is used by chat only, but we update it for symmetry in case batch_inference ever gets used for streaming later:

```python
    @asynccontextmanager
    async def _stream_with_retry(
        self, url: str, payload: dict
    ) -> AsyncGenerator[httpx.Response, None]:
        """Open a streaming POST, retrying on 503 before yielding."""
        backoff = _INITIAL_BACKOFF
        try:
            for attempt in range(_MAX_RETRIES):
                async with self._client.stream("POST", url, json=payload) as resp:
                    if resp.status_code != 503:
                        resp.raise_for_status()
                        yield resp
                        return
                    retry_after = resp.headers.get("Retry-After")
                wait = min(float(retry_after) if retry_after else backoff, _MAX_BACKOFF)
                logger.warning(
                    "503 from inference server on stream (attempt %d/%d), retrying in %.1fs",
                    attempt + 1, _MAX_RETRIES, wait,
                )
                await asyncio.sleep(wait)
                backoff = min(backoff * 2, _MAX_BACKOFF)
            # Final attempt
            async with self._client.stream("POST", url, json=payload) as resp:
                resp.raise_for_status()
                yield resp
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            if self.is_batch:
                raise BatchUnavailableError(str(exc)) from exc
            raise
```

- [ ] **Step 4: Run tests to confirm pass**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_batch_inference.py tests/test_inference.py -v
```

Expected: new tests pass; no regressions in the existing inference tests.

- [ ] **Step 5: Commit**

```bash
git add pal/inference.py tests/test_batch_inference.py
git commit -m "feat: add BatchUnavailableError and is_batch flag on InferenceClient"
```

---

## Task 3: Wire up self.batch_inference in the daemon

**Files:**
- Modify: `pal/daemon.py`

- [ ] **Step 1: Read the existing inference client construction**

Run:
```bash
cd /home/edible/Projects/PAL && grep -n "self.inference" pal/daemon.py | head -10
```

Note where the main `InferenceClient` is constructed (early in `Daemon.__init__`). The batch client construction goes right after it.

- [ ] **Step 2: Add batch_inference construction**

Find the line in `pal/daemon.py` that constructs the main inference client (a line like `self.inference = InferenceClient(base_url=config.inference_url, model=config.model)`). Immediately after that line, add:

```python
        if config.batch_enabled:
            self.batch_inference: InferenceClient | None = InferenceClient(
                base_url=config.batch_inference_url,
                model=config.batch_model,
                is_batch=True,
            )
        else:
            self.batch_inference = None
```

- [ ] **Step 3: Close the batch client on shutdown**

Find the daemon's cleanup / shutdown path (search for `self.inference.close()`). Add a matching line for `batch_inference`:

```bash
cd /home/edible/Projects/PAL && grep -n "inference.close" pal/daemon.py
```

In each location that closes `self.inference`, add (guarded):

```python
        if self.batch_inference is not None:
            await self.batch_inference.close()
```

- [ ] **Step 4: Run daemon import smoke test**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -c "import pal.daemon; print('ok')"
```

Expected: `ok`.

- [ ] **Step 5: Run full suite to confirm no regression**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest
```

Expected: same pass count as before (all tests pass).

- [ ] **Step 6: Commit**

```bash
git add pal/daemon.py
git commit -m "feat: construct batch_inference in Daemon guarded by batch_enabled"
```

---

## Task 4: Inject configurable inference into Categorizer

**Files:**
- Modify: `pal/categorizer.py`
- Modify: `pal/daemon.py`
- Modify: `tests/test_categorizer.py` (may or may not exist; create if missing)

- [ ] **Step 1: Inspect current Categorizer shape**

```bash
cd /home/edible/Projects/PAL && head -80 pal/categorizer.py
```

Note the constructor's `inference` parameter. It already takes an inference client as the sole dependency. No refactor needed; just pass the right one in the daemon.

- [ ] **Step 2: Read how Categorizer is constructed in the daemon**

```bash
cd /home/edible/Projects/PAL && grep -n "Categorizer(" pal/daemon.py
```

- [ ] **Step 3: Update daemon to pass batch_inference when available**

In `pal/daemon.py`, find the line `self.categorizer = Categorizer(self.inference)` and change to:

```python
        self.categorizer = Categorizer(
            self.batch_inference if self.batch_inference is not None else self.inference
        )
```

- [ ] **Step 4: Add a test verifying wiring**

Append to `tests/test_batch_inference.py`:

```python
from pal.daemon import Daemon
from pal.config import Config


def test_daemon_categorizer_uses_main_when_batch_disabled(tmp_path):
    cfg = Config(
        socket_path=tmp_path / "pal.sock",
        vault_path=tmp_path / "vault",
        batch_enabled=False,
    )
    daemon = Daemon(cfg)
    assert daemon.categorizer.inference is daemon.inference
    assert daemon.batch_inference is None


def test_daemon_categorizer_uses_batch_when_enabled(tmp_path):
    cfg = Config(
        socket_path=tmp_path / "pal.sock",
        vault_path=tmp_path / "vault",
        batch_enabled=True,
    )
    daemon = Daemon(cfg)
    assert daemon.batch_inference is not None
    assert daemon.categorizer.inference is daemon.batch_inference
```

Note: `Categorizer` must expose `.inference` as an attribute for these assertions. If it stores the inference internally with a different name, either rename it or add a `self.inference = inference` assignment in `Categorizer.__init__`.

- [ ] **Step 5: Ensure Categorizer stores inference as self.inference**

Open `pal/categorizer.py` and confirm its `__init__` has `self.inference = inference`. If the attribute name is different, rename it in the constructor and any internal uses.

- [ ] **Step 6: Run tests**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_batch_inference.py tests/test_categorizer.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add pal/daemon.py pal/categorizer.py tests/test_batch_inference.py
git commit -m "feat: route Categorizer through batch_inference when enabled"
```

---

## Task 5: Route learning scanner through batch_inference

**Files:**
- Modify: `pal/learning_scanner.py`
- Modify: `pal/daemon.py`

- [ ] **Step 1: Inspect current LearningScanner**

```bash
cd /home/edible/Projects/PAL && head -80 pal/learning_scanner.py
```

Note how the scanner is constructed and what inference it uses.

- [ ] **Step 2: Identify the daemon construction site**

```bash
cd /home/edible/Projects/PAL && grep -n "LearningScanner" pal/daemon.py
```

- [ ] **Step 3: Update daemon construction**

In `pal/daemon.py`, find the `LearningScanner(...)` construction and change the inference argument:

```python
        from pal.learning_scanner import LearningScanner
        effective_inference = self.batch_inference if self.batch_inference is not None else self.inference
        scanner = LearningScanner(
            inference=effective_inference,
            # ... other existing args unchanged ...
        )
```

Replace `inference=self.inference` if it appears. If the scanner is constructed in a function rather than `__init__`, apply the same pattern.

- [ ] **Step 4: Add test**

In `tests/test_batch_inference.py`:

```python
def test_daemon_scanner_uses_batch_when_enabled(tmp_path):
    cfg = Config(
        socket_path=tmp_path / "pal.sock",
        vault_path=tmp_path / "vault",
        batch_enabled=True,
    )
    daemon = Daemon(cfg)
    # The scanner's inference attribute should match the batch client.
    # If the scanner is constructed inside a handler rather than __init__,
    # assert on the factory function used in the handler instead.
    # For now: the scanner ctor should accept the batch_inference.
    from pal.learning_scanner import LearningScanner
    scanner = LearningScanner(
        inference=daemon.batch_inference,
        extractor=None,  # adjust to actual signature if needed
    )
    assert scanner.inference is daemon.batch_inference
```

Note: adapt the LearningScanner construction to the actual signature in the code; the key assertion is that the scanner's inference attribute matches `daemon.batch_inference`.

- [ ] **Step 5: Run tests**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_batch_inference.py tests/test_learning_scanner.py -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add pal/daemon.py pal/learning_scanner.py tests/test_batch_inference.py
git commit -m "feat: route LearningScanner through batch_inference when enabled"
```

---

## Task 6: Route detect_from_llm_toc through batch_inference

**Files:**
- Modify: `pal/daemon.py`

- [ ] **Step 1: Inspect the call site**

```bash
cd /home/edible/Projects/PAL && grep -n "detect_chapters" pal/daemon.py
```

`detect_chapters` takes an `inference` argument that flows through to `detect_from_llm_toc`. Replace the passed argument.

- [ ] **Step 2: Update the call site**

In `pal/daemon.py`, find the line:

```python
                detection = await detect_chapters(doc, inference=self.inference)
```

Change to:

```python
                detection = await detect_chapters(
                    doc,
                    inference=self.batch_inference if self.batch_inference is not None else self.inference,
                )
```

- [ ] **Step 3: Run tests**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_pdf_structure.py tests/test_import.py -v
```

Expected: pass unchanged; no PDF test exercises the LLM-TOC tier directly with the real batch client (existing tests use injected fakes).

- [ ] **Step 4: Commit**

```bash
git add pal/daemon.py
git commit -m "feat: route detect_chapters through batch_inference when enabled"
```

---

## Task 7: BatchFallbackProposal protocol message

**Files:**
- Modify: `pal/protocol.py`
- Create: `tests/test_batch_fallback_proposal.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_batch_fallback_proposal.py`:

```python
"""Tests for BatchFallbackProposal protocol message."""
import json
import pytest

from pal.protocol import BatchFallbackProposal, encode_message, decode_message


def test_batch_fallback_proposal_round_trips():
    proposal = BatchFallbackProposal(
        proposal_id="abc123",
        caller="categorizer",
        context="categorizing compile for raw/summaries/X.md",
        original_request={"messages": [{"role": "user", "content": "hi"}], "reasoning": "off"},
    )
    wire = encode_message(proposal)
    restored = decode_message(wire)
    assert isinstance(restored, BatchFallbackProposal)
    assert restored.proposal_id == "abc123"
    assert restored.caller == "categorizer"
    assert restored.context == "categorizing compile for raw/summaries/X.md"
    assert restored.original_request["reasoning"] == "off"


def test_batch_fallback_proposal_caller_is_restricted():
    """Only categorizer and llm_toc are valid callers in Phase B."""
    BatchFallbackProposal(
        proposal_id="1",
        caller="categorizer",
        context="x",
        original_request={},
    )
    BatchFallbackProposal(
        proposal_id="1",
        caller="llm_toc",
        context="x",
        original_request={},
    )
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_batch_fallback_proposal.py -v
```

Expected: `ImportError: cannot import name 'BatchFallbackProposal'`.

- [ ] **Step 3: Add BatchFallbackProposal to the protocol**

In `pal/protocol.py`, near other proposal message dataclasses (search for existing `Proposal` classes like `CompileProposal`):

```python
from typing import Literal


@dataclass
class BatchFallbackProposal(Message):
    """Emitted when a user-facing call to the batch inference backend
    fails and the user should choose: retry on batch, run on main, or
    skip this step.

    Approval states on the proposal:
      - approved (state "retry"): retry on batch
      - approved (state "main"): run on main for this one call
      - declined / state "skip": caller uses its default fallback
    """
    proposal_id: str
    caller: Literal["categorizer", "llm_toc"]
    context: str
    original_request: dict
```

Register it in the encode/decode dispatch near the existing proposal types. Find `encode_message` and `decode_message` and add branches:

```python
# In encode_message, find the existing type dispatch and add:
    elif isinstance(msg, BatchFallbackProposal):
        return _encode({
            "type": "batch_fallback_proposal",
            "proposal_id": msg.proposal_id,
            "caller": msg.caller,
            "context": msg.context,
            "original_request": msg.original_request,
        })

# In decode_message, similar addition:
    elif typ == "batch_fallback_proposal":
        return BatchFallbackProposal(
            proposal_id=payload["proposal_id"],
            caller=payload["caller"],
            context=payload["context"],
            original_request=payload["original_request"],
        )
```

- [ ] **Step 4: Run tests to confirm pass**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_batch_fallback_proposal.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add pal/protocol.py tests/test_batch_fallback_proposal.py
git commit -m "feat: add BatchFallbackProposal protocol message"
```

---

## Task 8: ApprovalRegistry support for batch_fallback kind

**Files:**
- Modify: `pal/approval_registry.py`
- Modify: `tests/test_batch_fallback_proposal.py`

- [ ] **Step 1: Inspect existing approval_registry kinds**

```bash
cd /home/edible/Projects/PAL && grep -n "kind" pal/approval_registry.py | head -20
```

Note the existing kind strings (`compile`, `research`, `consolidate`, etc.) and the resolution API.

- [ ] **Step 2: Add batch_fallback handling test**

Append to `tests/test_batch_fallback_proposal.py`:

```python
def test_approval_registry_accepts_batch_fallback_kind():
    from pal.approval_registry import ApprovalRegistry
    reg = ApprovalRegistry()
    pid = reg.create_proposal(kind="batch_fallback", caller="categorizer", context="x")
    reg.approve(pid, state="main")
    proposal = reg.get(pid)
    assert proposal.state == "approved"
    assert proposal.approval_choice == "main"


def test_approval_registry_batch_fallback_skip():
    from pal.approval_registry import ApprovalRegistry
    reg = ApprovalRegistry()
    pid = reg.create_proposal(kind="batch_fallback", caller="llm_toc", context="x")
    reg.decline(pid)
    proposal = reg.get(pid)
    assert proposal.state == "declined"
```

- [ ] **Step 3: Run tests to confirm failure**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_batch_fallback_proposal.py -v
```

Expected: the two new tests fail depending on registry's current API shape.

- [ ] **Step 4: Extend ApprovalRegistry**

In `pal/approval_registry.py`, verify the existing API. If `create_proposal(kind, ...)` accepts arbitrary string kinds, no code change is needed and the tests will pass as soon as they use the right arguments. If the registry has an enum or fixed set, add `"batch_fallback"` to that set.

Also ensure the approval API supports an optional `state` argument to `approve()` so callers can distinguish "retry" from "main":

```python
    def approve(self, proposal_id: str, state: str = "approved") -> None:
        """Mark a proposal approved. `state` is a freeform string carried
        alongside the state; used by batch_fallback to distinguish 'retry'
        from 'main'. Standard proposals use the default."""
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise KeyError(proposal_id)
        proposal.state = "approved"
        proposal.approval_choice = state
```

If the existing `Proposal` dataclass does not have an `approval_choice` field, add it with a default of `None`.

- [ ] **Step 5: Run tests**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_batch_fallback_proposal.py tests/test_approval_registry.py -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add pal/approval_registry.py tests/test_batch_fallback_proposal.py
git commit -m "feat: ApprovalRegistry batch_fallback kind with approval_choice state"
```

---

## Task 9: Categorizer batch-fallback proposal flow

**Files:**
- Modify: `pal/categorizer.py`
- Modify: `tests/test_categorizer.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_categorizer.py` (create if missing with the usual imports):

```python
import pytest
from unittest.mock import AsyncMock

from pal.categorizer import Categorizer
from pal.inference import BatchUnavailableError


class _FakeApproval:
    def __init__(self, emit_choice: str | None):
        self.emitted: list = []
        self._choice = emit_choice  # "retry" / "main" / "skip"
        self._next_pid = "p1"

    def create_proposal(self, kind, **kwargs):
        return self._next_pid

    async def wait(self, proposal_id: str):
        # Simulate user choosing.
        class R:
            pass
        r = R()
        if self._choice == "skip":
            r.state = "declined"
            r.approval_choice = None
        else:
            r.state = "approved"
            r.approval_choice = self._choice
        return r


@pytest.mark.asyncio
async def test_categorizer_retries_on_batch_after_user_chooses_retry(tmp_path):
    call_count = 0

    async def fake_complete(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise BatchUnavailableError("down")
        from pal.inference import CompletionResult
        return CompletionResult(type="text", content="Research")

    inference = AsyncMock()
    inference.complete.side_effect = fake_complete
    inference.is_batch = True

    cat = Categorizer(
        inference=inference,
        approval=_FakeApproval(emit_choice="retry"),
    )
    category = await cat.categorize(
        title="Quantum", body="text", vault_path=tmp_path,
    )
    assert category == "Research"
    assert call_count == 2  # first raised, retry succeeded


@pytest.mark.asyncio
async def test_categorizer_runs_on_main_after_user_chooses_main(tmp_path):
    async def batch_fail(messages, **kwargs):
        raise BatchUnavailableError("down")

    from pal.inference import CompletionResult

    async def main_ok(messages, **kwargs):
        return CompletionResult(type="text", content="Technology")

    batch_inference = AsyncMock()
    batch_inference.complete.side_effect = batch_fail
    batch_inference.is_batch = True

    main_inference = AsyncMock()
    main_inference.complete.side_effect = main_ok
    main_inference.is_batch = False

    cat = Categorizer(
        inference=batch_inference,
        approval=_FakeApproval(emit_choice="main"),
        main_inference=main_inference,
    )
    category = await cat.categorize(
        title="Widgets", body="text", vault_path=tmp_path,
    )
    assert category == "Technology"
    main_inference.complete.assert_called_once()


@pytest.mark.asyncio
async def test_categorizer_returns_default_when_user_chooses_skip(tmp_path):
    async def fake_complete(messages, **kwargs):
        raise BatchUnavailableError("down")

    inference = AsyncMock()
    inference.complete.side_effect = fake_complete
    inference.is_batch = True

    cat = Categorizer(
        inference=inference,
        approval=_FakeApproval(emit_choice="skip"),
    )
    category = await cat.categorize(
        title="Mystery", body="text", vault_path=tmp_path,
    )
    assert category == "Unfiled"  # or whatever the existing default is
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_categorizer.py -v
```

Expected: tests fail because Categorizer doesn't yet take `approval` / `main_inference` and doesn't handle `BatchUnavailableError`.

- [ ] **Step 3: Extend Categorizer with fallback handling**

In `pal/categorizer.py`, update `__init__` and `categorize`:

```python
class Categorizer:
    def __init__(
        self,
        inference,
        approval=None,
        main_inference=None,
    ) -> None:
        self.inference = inference
        self.approval = approval
        self.main_inference = main_inference

    async def categorize(self, title: str, body: str, vault_path) -> str:
        messages = self._build_messages(title, body, vault_path)
        try:
            result = await self.inference.complete(messages, reasoning="off")
            return self._parse_category(result)
        except BatchUnavailableError:
            if self.approval is None:
                # No approval surface wired up; degrade to default.
                return self._default_category()
            pid = self.approval.create_proposal(
                kind="batch_fallback",
                caller="categorizer",
                context=f"categorizing {title!r}",
                original_request={"messages": messages, "reasoning": "off"},
            )
            choice = await self.approval.wait(pid)
            if choice.state == "declined":
                return self._default_category()
            if choice.approval_choice == "retry":
                # User wants to retry on batch.
                result = await self.inference.complete(messages, reasoning="off")
                return self._parse_category(result)
            if choice.approval_choice == "main" and self.main_inference is not None:
                result = await self.main_inference.complete(messages, reasoning="off")
                return self._parse_category(result)
            return self._default_category()

    def _default_category(self) -> str:
        return "Unfiled"

    # _build_messages and _parse_category remain unchanged (refactored from existing body).
```

Refactor the current body of `categorize` to extract `_build_messages` (returns the list of messages) and `_parse_category` (takes the LLM result and returns the category string). Preserve existing behavior exactly.

If the existing default category is something other than `"Unfiled"` (check the existing code), use that; the test expectation must match.

- [ ] **Step 4: Update daemon construction**

In `pal/daemon.py`, update the Categorizer construction to pass `approval` and `main_inference`:

```python
        effective_inference = self.batch_inference if self.batch_inference is not None else self.inference
        self.categorizer = Categorizer(
            inference=effective_inference,
            approval=self.approval_registry if self.batch_inference is not None else None,
            main_inference=self.inference if self.batch_inference is not None else None,
        )
```

Note: `self.approval_registry` must exist; it is the same registry used for consolidate / research proposals. Verify with:

```bash
cd /home/edible/Projects/PAL && grep -n "approval_registry" pal/daemon.py | head
```

If the registry does not yet have a `wait()` coroutine, add one or adapt the Categorizer to use whatever existing pattern (e.g., polling + `asyncio.Event`) the current consolidate flow uses.

- [ ] **Step 5: Run tests**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_categorizer.py -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add pal/categorizer.py pal/daemon.py tests/test_categorizer.py
git commit -m "feat: Categorizer batch-fallback proposal flow"
```

---

## Task 10: detect_from_llm_toc batch-fallback proposal flow

**Files:**
- Modify: `pal/daemon.py` (the PDF import path that calls `detect_chapters`)
- Modify: `tests/test_import.py` or `tests/test_pdf_structure.py`

`detect_from_llm_toc` itself catches general `Exception` and returns None. That's fine for silent failure but not for the proposal flow. Instead of modifying `pdf_structure.py`, we handle the fallback in the daemon's import path, where we have access to the approval registry.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_import.py`:

```python
@pytest.mark.asyncio
async def test_import_pdf_llm_toc_fallback_to_main_on_batch_unavailable(import_daemon, socket_path, tmp_path, monkeypatch):
    """When LLM-TOC would fire (TOC and typography both fail) and the
    batch backend is unavailable, user gets a BatchFallbackProposal.
    Choosing 'main' retries the detection on the main inference."""
    import fitz
    daemon, vault = import_daemon

    # Build a synthetic PDF with NO TOC and flat typography so tiers 1
    # and 2 both return None, forcing tier 3.
    pdf_path = vault / "raw" / "no-structure.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((72, 72), f"uniform body text for page {i}", fontsize=11)
    doc.save(str(pdf_path))
    doc.close()

    # Set up: batch_inference raises, main inference returns a valid response.
    from pal.inference import BatchUnavailableError, CompletionResult

    async def batch_fail(messages, **kwargs):
        raise BatchUnavailableError("down")

    async def main_ok(messages, **kwargs):
        import json
        return CompletionResult(type="text", content=json.dumps([]))

    # Enable batch and inject fakes.
    daemon.batch_inference = AsyncMock()
    daemon.batch_inference.complete.side_effect = batch_fail
    daemon.batch_inference.is_batch = True
    monkeypatch.setattr(daemon.inference, "complete", main_ok)

    # Auto-approve any batch_fallback proposal with "main" for this test.
    original_emit = daemon.proposal_emitter
    def auto_emit(msg):
        if getattr(msg, "__class__", type(msg)).__name__ == "BatchFallbackProposal":
            daemon.approval_registry.approve(msg.proposal_id, state="main")
        else:
            original_emit(msg)
    daemon.proposal_emitter = auto_emit

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("import", "raw/no-structure.pdf")
    await client.close()

    # Expected outcome: main inference was called (fallback happened),
    # detection returned None empty-list, and import fell through to
    # single-file (method "single-file" appears in response text).
    assert "single-file" in resp.text or "single_file" in resp.text
```

Note: the exact assertion depends on how the response formats the detection method; adjust after running the test to match the actual response text.

- [ ] **Step 2: Run test to confirm failure**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_import.py::test_import_pdf_llm_toc_fallback_to_main_on_batch_unavailable -v
```

Expected: failure, likely because the fallback flow is not yet implemented.

- [ ] **Step 3: Implement the fallback flow in the daemon's PDF import path**

In `pal/daemon.py::_handle_import`, find the call:

```python
                detection = await detect_chapters(
                    doc,
                    inference=self.batch_inference if self.batch_inference is not None else self.inference,
                )
```

Wrap it with a try/except that, on `BatchUnavailableError`, emits a proposal and retries based on the response:

```python
                from pal.inference import BatchUnavailableError
                try:
                    detection = await detect_chapters(
                        doc,
                        inference=self.batch_inference if self.batch_inference is not None else self.inference,
                    )
                except BatchUnavailableError:
                    from pal.protocol import BatchFallbackProposal
                    pid = self.approval_registry.create_proposal(
                        kind="batch_fallback",
                        caller="llm_toc",
                        context=f"detecting chapters for {full_path.name}",
                        original_request={},
                    )
                    proposal = BatchFallbackProposal(
                        proposal_id=pid,
                        caller="llm_toc",
                        context=f"detecting chapters for {full_path.name}",
                        original_request={},
                    )
                    self.proposal_emitter(proposal)
                    resolved = await self.approval_registry.wait(pid)
                    if resolved.state == "approved" and resolved.approval_choice == "main":
                        detection = await detect_chapters(doc, inference=self.inference)
                    elif resolved.state == "approved" and resolved.approval_choice == "retry":
                        detection = await detect_chapters(
                            doc,
                            inference=self.batch_inference,
                        )
                    else:
                        # Skip: treat as if all tiers failed, fall to single-file.
                        from pal.pdf_structure import DetectionResult
                        detection = DetectionResult(method="single-file", boundaries=[])
```

- [ ] **Step 4: Run the test to confirm pass**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_import.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add pal/daemon.py tests/test_import.py
git commit -m "feat: detect_from_llm_toc batch-fallback proposal flow"
```

---

## Task 11: Learning scanner silent-skip on BatchUnavailableError

**Files:**
- Modify: `pal/learning_scanner.py`
- Modify: `tests/test_learning_scanner.py` (create if missing)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_learning_scanner.py`:

```python
import pytest
from unittest.mock import AsyncMock

from pal.learning_scanner import LearningScanner
from pal.inference import BatchUnavailableError


@pytest.mark.asyncio
async def test_scanner_silently_skips_when_batch_unavailable(caplog):
    async def boom(*args, **kwargs):
        raise BatchUnavailableError("batch down")

    inference = AsyncMock()
    inference.complete.side_effect = boom
    inference.is_batch = True

    scanner = LearningScanner(inference=inference, extractor=None)
    # Adapt to the real signature of maybe_scan; this is the interface
    # the daemon calls after each turn.
    result = await scanner.maybe_scan(
        recent_turns=[{"role": "user", "content": "hi"}],
        latest_user_message="hi",
    )
    assert result is None  # scanner produces no candidate
    assert any("batch unavailable" in r.getMessage().lower() for r in caplog.records)
```

- [ ] **Step 2: Run test to confirm failure**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_learning_scanner.py -v
```

Expected: fails because the scanner does not yet catch `BatchUnavailableError`.

- [ ] **Step 3: Add silent-skip handler to the scanner**

In `pal/learning_scanner.py`, find the extraction function that calls `inference.complete(...)`. Wrap that call:

```python
from pal.inference import BatchUnavailableError
import logging

logger = logging.getLogger(__name__)


async def extract_candidate(inference, recent_turns, trigger_message):
    # existing message construction
    try:
        result = await inference.complete(messages, reasoning="off")
    except BatchUnavailableError as exc:
        logger.warning("Learning scan skipped, batch unavailable: %s", exc)
        return None
    # existing parse logic
```

The exact function name and location depend on the current scanner code. Put the try/except around whichever function body calls `inference.complete(...)` inside the scanner module.

- [ ] **Step 4: Run test to confirm pass**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_learning_scanner.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add pal/learning_scanner.py tests/test_learning_scanner.py
git commit -m "feat: learning scanner silently skips on BatchUnavailableError"
```

---

## Task 12: CLI handler for BatchFallbackProposal

**Files:**
- Modify: `pal/client.py`
- Modify: `pal/cli.py` (the CLI render/prompt path)
- Modify: `tests/test_cli.py` (create if missing)

- [ ] **Step 1: Find the CLI's existing proposal rendering**

```bash
cd /home/edible/Projects/PAL && grep -n "CompileProposal\|ConsolidateProposal\|ResearchProposal" pal/cli.py pal/client.py
```

Note how existing proposals are surfaced to the CLI user (likely a text prompt with single-letter approval input).

- [ ] **Step 2: Add handling for the new message type**

In `pal/cli.py`, in the message-receive loop, add a branch for `BatchFallbackProposal`:

```python
                    elif isinstance(msg, BatchFallbackProposal):
                        print(f"\nBatch model unavailable.")
                        print(f"The {msg.caller} step can:")
                        print(f"  [r] Retry on batch")
                        print(f"  [m] Run on main instead (one-off)")
                        print(f"  [s] Skip this step")
                        choice = await self._prompt_choice("Choice [r/m/s]: ", ["r", "m", "s"])
                        if choice == "r":
                            await self.client.approve(msg.proposal_id, state="retry")
                        elif choice == "m":
                            await self.client.approve(msg.proposal_id, state="main")
                        else:
                            await self.client.decline(msg.proposal_id)
```

Adapt to the actual dispatch loop shape in `pal/cli.py`. If the CLI has an async single-letter prompt helper, reuse it; otherwise implement one alongside this change.

If `self.client.approve` does not yet support a `state` argument, add it by finding the `approve` method in `pal/client.py` and extending it:

```python
    async def approve(self, proposal_id: str, state: str = "approved") -> None:
        # existing approve logic, threading state to the registry
```

- [ ] **Step 3: Add a smoke test that the CLI branch dispatches**

In `tests/test_cli.py`:

```python
# A test that constructs a BatchFallbackProposal, feeds it through the CLI
# dispatcher with simulated user input "m", and asserts that
# client.approve was called with state="main".
```

Write the test against the actual CLI's dispatch shape.

- [ ] **Step 4: Run tests**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_cli.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add pal/cli.py pal/client.py tests/test_cli.py
git commit -m "feat: CLI handler for BatchFallbackProposal"
```

---

## Task 13: Discord handler for BatchFallbackProposal

**Files:**
- Modify: `pal/discord_interactions.py`
- Modify: `tests/test_discord_interactions.py`

- [ ] **Step 1: Inspect existing proposal handlers**

```bash
cd /home/edible/Projects/PAL && grep -n "CompileProposalMessage\|ConsolidateProposal\|_handle.*proposal" pal/discord_interactions.py | head
```

Note the UI pattern (button view, approval message formatting).

- [ ] **Step 2: Write the failing test**

In `tests/test_discord_interactions.py`, add:

```python
def test_batch_fallback_view_has_three_buttons():
    from pal.discord_interactions import BatchFallbackView
    view = BatchFallbackView(proposal_id="p1", caller="categorizer")
    labels = [c.label for c in view.children]
    assert any("retry" in l.lower() for l in labels)
    assert any("main" in l.lower() for l in labels)
    assert any("skip" in l.lower() for l in labels)
```

- [ ] **Step 3: Run test to confirm failure**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_discord_interactions.py::test_batch_fallback_view_has_three_buttons -v
```

Expected: `ImportError: cannot import name 'BatchFallbackView'`.

- [ ] **Step 4: Implement the view**

In `pal/discord_interactions.py`, near other `...View` classes:

```python
class BatchFallbackView(discord.ui.View):
    """Three-button view for BatchFallbackProposal.

    Retry on batch / Run on main / Skip.
    """

    def __init__(self, proposal_id: str, caller: str, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.proposal_id = proposal_id
        self.caller = caller
        retry = discord.ui.Button(style=discord.ButtonStyle.primary, label="Retry on batch")
        main = discord.ui.Button(style=discord.ButtonStyle.secondary, label="Run on main")
        skip = discord.ui.Button(style=discord.ButtonStyle.danger, label="Skip")
        retry.callback = self._on_retry
        main.callback = self._on_main
        skip.callback = self._on_skip
        self.add_item(retry)
        self.add_item(main)
        self.add_item(skip)

    async def _on_retry(self, interaction):
        await self._dispatch(interaction, state="retry")

    async def _on_main(self, interaction):
        await self._dispatch(interaction, state="main")

    async def _on_skip(self, interaction):
        await self._dispatch(interaction, state=None, decline=True)

    async def _dispatch(self, interaction, state, decline: bool = False):
        # Implementation depends on the adapter's existing approval
        # dispatch pattern. See how ConsolidateView routes decisions
        # back to the daemon through the unix-socket client, and mirror.
        raise NotImplementedError("Wire to existing adapter dispatch")
```

Replace `raise NotImplementedError` with the real dispatch to the PalClient, following the same pattern used by `ConsolidateView` or `CompileView`.

Add a handler entry point that accepts a `BatchFallbackProposal` message and posts the embed + view. Mirror the existing `_handle_consolidate_proposal` pattern.

- [ ] **Step 5: Run tests**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_discord_interactions.py -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add pal/discord_interactions.py tests/test_discord_interactions.py
git commit -m "feat: Discord handler for BatchFallbackProposal"
```

---

## Task 14: /model command shows both slots

**Files:**
- Modify: `pal/daemon.py` (the `_handle_model` handler)
- Modify: `pal/commands.py` (if help text lives there)
- Modify: `tests/test_model_command.py` (create if missing)

- [ ] **Step 1: Locate the /model handler**

```bash
cd /home/edible/Projects/PAL && grep -n "_handle_model\|handle_model" pal/daemon.py
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_model_command.py`:

```python
"""Tests for /model command and dual-slot status."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from pal.daemon import Daemon
from pal.config import Config


@pytest.mark.asyncio
async def test_model_command_shows_both_slots_when_enabled(tmp_path, monkeypatch):
    cfg = Config(
        socket_path=tmp_path / "pal.sock",
        vault_path=tmp_path / "vault",
        batch_enabled=True,
    )
    daemon = Daemon(cfg)

    async def fake_status():
        return {
            "slots": {
                "main": {"loaded_model": "gemma-4-26b-a4b-it-q4_k_m", "healthy": True},
                "batch": {"loaded_model": "gemma-3-4b-it-q4_k_m", "healthy": True},
            }
        }

    monkeypatch.setattr(daemon.inference, "get_status", fake_status, raising=False)

    # Call the handler directly (or through a helper), capture output.
    output = await daemon._model_status_text()
    assert "main: gemma-4-26b" in output
    assert "batch: gemma-3-4b" in output


@pytest.mark.asyncio
async def test_model_command_shows_only_main_when_disabled(tmp_path, monkeypatch):
    cfg = Config(
        socket_path=tmp_path / "pal.sock",
        vault_path=tmp_path / "vault",
        batch_enabled=False,
    )
    daemon = Daemon(cfg)

    async def fake_status():
        return {"slots": {"main": {"loaded_model": "gemma-4-26b-a4b-it-q4_k_m", "healthy": True}}}

    monkeypatch.setattr(daemon.inference, "get_status", fake_status, raising=False)

    output = await daemon._model_status_text()
    assert "main:" in output
    assert "batch:" not in output
```

- [ ] **Step 3: Extract a `_model_status_text` helper**

In `pal/daemon.py`, add a helper method on `Daemon`:

```python
    async def _model_status_text(self) -> str:
        """Render the /model status text. Queries the manager's /status
        endpoint and formats each slot's loaded model and health."""
        status = await self._get_manager_status()
        slots = status.get("slots", {})
        lines = []
        for slot_name in ("main", "batch"):
            slot = slots.get(slot_name)
            if slot is None:
                continue
            loaded = slot.get("loaded_model", "?")
            healthy = slot.get("healthy", False)
            marker = "healthy" if healthy else "UNHEALTHY"
            lines.append(f"  {slot_name}: {loaded} ({marker})")
        return "Loaded models:\n" + "\n".join(lines)

    async def _get_manager_status(self) -> dict:
        """Fetch /status from the manager. Returns empty dict on error."""
        import httpx
        try:
            async with httpx.AsyncClient() as c:
                resp = await c.get(f"{self.config.inference_url}/status")
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning("manager /status fetch failed: %s", exc)
            return {}
```

Update the existing `_handle_model` handler (when invoked with no args) to use `_model_status_text()` rather than its current formatting.

- [ ] **Step 4: Run tests**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_model_command.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add pal/daemon.py tests/test_model_command.py
git commit -m "feat: /model shows both main and batch slots"
```

---

## Task 15: /model --target batch swap

**Files:**
- Modify: `pal/daemon.py::_handle_model`
- Modify: `tests/test_model_command.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_model_command.py`:

```python
@pytest.mark.asyncio
async def test_model_command_target_batch_swap(tmp_path, monkeypatch):
    cfg = Config(
        socket_path=tmp_path / "pal.sock",
        vault_path=tmp_path / "vault",
        batch_enabled=True,
    )
    daemon = Daemon(cfg)

    swap_calls = []

    async def fake_swap(model, target="main"):
        swap_calls.append((model, target))
        return {"ok": True}

    monkeypatch.setattr(daemon, "_request_model_swap", fake_swap, raising=False)

    # Simulate a /model invocation with --target batch.
    result = await daemon._dispatch_model_command("--target batch qwen3-4b-instruct")
    assert swap_calls == [("qwen3-4b-instruct", "batch")]


@pytest.mark.asyncio
async def test_model_command_default_targets_main(tmp_path, monkeypatch):
    cfg = Config(
        socket_path=tmp_path / "pal.sock",
        vault_path=tmp_path / "vault",
        batch_enabled=True,
    )
    daemon = Daemon(cfg)

    swap_calls = []

    async def fake_swap(model, target="main"):
        swap_calls.append((model, target))
        return {"ok": True}

    monkeypatch.setattr(daemon, "_request_model_swap", fake_swap, raising=False)

    result = await daemon._dispatch_model_command("gemma-4-26b-a4b-it-q4_k_m")
    assert swap_calls == [("gemma-4-26b-a4b-it-q4_k_m", "main")]
```

- [ ] **Step 2: Implement `_dispatch_model_command` and `_request_model_swap`**

In `pal/daemon.py`, add parsing and dispatch:

```python
    async def _dispatch_model_command(self, args: str) -> str:
        """Parse /model args and dispatch. Supports:
          - empty → show status
          - <name> → swap main
          - --target <slot> <name> → swap that slot
        """
        parts = args.strip().split()
        if not parts:
            return await self._model_status_text()
        target = "main"
        if parts[0] == "--target":
            if len(parts) < 3:
                return "Usage: /model [--target main|batch] <model-name>"
            target = parts[1]
            model_name = " ".join(parts[2:])
            if target not in ("main", "batch"):
                return f"Unknown target: {target}. Use main or batch."
        else:
            model_name = " ".join(parts)
        await self._request_model_swap(model_name, target=target)
        return f"Requested swap: {target} -> {model_name}"

    async def _request_model_swap(self, model: str, target: str = "main") -> dict:
        """POST to the manager's swap endpoint with the target slot."""
        import httpx
        async with httpx.AsyncClient() as c:
            resp = await c.post(
                f"{self.config.inference_url}/swap",
                json={"model": model, "target": target},
            )
            resp.raise_for_status()
            return resp.json()
```

Update the existing `_handle_model` to call `_dispatch_model_command` with the parsed args.

- [ ] **Step 3: Run tests**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest tests/test_model_command.py -v
```

Expected: pass.

- [ ] **Step 4: Run full suite**

```bash
cd /home/edible/Projects/PAL && source .venv/bin/activate && python -m pytest
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add pal/daemon.py tests/test_model_command.py
git commit -m "feat: /model --target batch swap support"
```

---

## Task 16: Manual verification runbook

**Files:**
- Create: `docs/superpowers/runbooks/2026-04-19-phase-b-pal-verification.md`

- [ ] **Step 1: Create the runbook**

Create `docs/superpowers/runbooks/2026-04-19-phase-b-pal-verification.md`:

```markdown
# Phase B PAL Integration - Manual Verification Runbook

Ordered steps to verify the PAL-side Phase B changes work against a
running dual-slot manager. Assumes the server-side Phase B plan has
landed and the batch endpoint is healthy.

## Prerequisites

- Server-side Phase B deployed: manager with dual slots, `llama-server-batch.service` running on port 8083, Gemma 3 4B loaded.
- `/mnt/secondary/PAL` on the server is on the latest PAL commit.
- Server-side systemd units restarted after PAL pull.

## 1. Flag off: confirm no regressions

On the server:

```
systemctl --user stop pal-daemon
# ensure PAL_BATCH_ENABLED is unset or false in the service environment
systemctl --user start pal-daemon
pal
> /help
> /model
```

Expected: `/model` shows only the main slot. All existing flows work unchanged.

## 2. Flag on: confirm batch construction

Set `PAL_BATCH_ENABLED=true` in the daemon's environment (edit the user systemd unit environment or set in ~/.pal/env), restart.

```
pal
> /model
```

Expected: `/model` shows both slots. Batch slot shows `gemma-3-4b-it-q4_k_m (healthy)`.

## 3. Categorizer happy path

Trigger a compile of an existing raw summary:

```
> /compile raw/summaries/<some-file>.md
```

Expected: the compile succeeds; its category is chosen by the batch model. No user prompts appear. Chat latency unaffected.

## 4. Categorizer batch outage

On the server:

```
sudo systemctl stop llama-server-batch
```

Trigger another compile. Expected:

- PAL surfaces a BatchFallbackProposal in the CLI / Discord.
- Choose "Retry on batch" → eventually fails again (batch still down).
- Choose "Run on main" → compile succeeds using the main model for categorize.
- Choose "Skip" → compile succeeds with category "Unfiled" (or whatever the default is).

Restart the batch service:

```
sudo systemctl start llama-server-batch
```

## 5. Learning scanner silent skip

Stop the batch service again. Have a normal chat turn. Expected:

- No user-visible prompt about batch.
- `journalctl --user -u pal-daemon -n 50` shows a `Learning scan skipped, batch unavailable` warning.
- Chat response lands normally; learning extraction just didn't run for that turn.

## 6. /model swap on batch

```
> /model --target batch qwen3-4b-instruct
```

Expected: manager swaps the batch slot. `/model` reflects the new loaded model. Chat latency on main is unaffected during the swap.

## 7. Concurrent load test

With batch enabled and healthy, run two things at once:

- Terminal 1: a long chat session in the CLI.
- Terminal 2 (on same user): `for i in $(seq 1 10); do pal -c 'hi'; done` simulating learning scans.

Expected: terminal 1 chat latency stays at normal P40 speeds (~40 tok/s); learning scans land on the batch endpoint. `nvidia-smi` shows the P40 unchanged.

## Rollback

If anything goes wrong:

```
# Flip the flag off
sed -i 's/PAL_BATCH_ENABLED=true/PAL_BATCH_ENABLED=false/' ~/.config/systemd/user/pal-daemon.service.d/override.conf
systemctl --user daemon-reload
systemctl --user restart pal-daemon
```

All offloaded callers fall back to the main inference. No code deployment required.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/runbooks/2026-04-19-phase-b-pal-verification.md
git commit -m "docs: Phase B PAL integration manual verification runbook"
```

---

## Self-review

### Spec coverage

Each requirement in the spec maps to at least one task:

- **Two InferenceClient instances**: Tasks 2, 3.
- **Three offloaded callers (categorizer, learning_scanner, detect_from_llm_toc)**: Tasks 4, 5, 6.
- **BatchUnavailableError exception**: Task 2.
- **BatchFallbackProposal and approval_registry kind**: Tasks 7, 8.
- **Categorizer proposal flow**: Task 9.
- **detect_from_llm_toc proposal flow**: Task 10.
- **Learning scanner silent-skip**: Task 11.
- **CLI handler for proposal**: Task 12.
- **Discord handler for proposal**: Task 13.
- **/model shows both slots**: Task 14.
- **/model --target batch**: Task 15.
- **PAL_BATCH_ENABLED env flag with off-by-default**: Task 1.
- **Gated rollout (flag on = no-op when off)**: Tasks 3, 4, 5, 6 (all guard by `batch_inference is not None`).
- **Manual verification**: Task 16.

Not covered here (server-side, out of scope per the Plan B/Plan A split):
- llama.cpp rebuild with Vulkan + CUDA.
- `llama-server-batch.service` systemd unit.
- Manager slot-aware routing.
- Manager `/status` shape with slots.
- Manager `/swap?target=batch` endpoint.

These are prerequisites for Task 16's end-to-end verification and must be implemented via a separate plan in the `inference_server` repo.

### Placeholder scan

No "TBD" or "fill in details" strings in the plan body. A few tasks note "adapt to the actual X in the code" where the existing codebase shape drives small implementation choices (e.g., the learning scanner's exact function name). These are not placeholders; they are "look at the file you are about to edit" instructions.

### Type consistency

- `BatchFallbackProposal` fields (`proposal_id`, `caller`, `context`, `original_request`) are consistent across Tasks 7, 9, 10, 12, 13.
- `BatchUnavailableError` raised by `InferenceClient` when `is_batch=True` is consistent across Tasks 2, 9, 10, 11.
- `Categorizer.__init__` signature after Task 4 (`inference`) and after Task 9 (`inference`, `approval`, `main_inference`) is consistent; Task 9 extends without renaming.
- `_dispatch_model_command` and `_request_model_swap` method names are used consistently in Tasks 14 and 15.
