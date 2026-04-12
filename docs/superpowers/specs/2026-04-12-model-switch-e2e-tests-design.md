# End-to-End Tests for Global Model Switching

**Date:** 2026-04-12
**Status:** Draft

## Overview

The global-active-model refactor (commit 9114d75) centralized model selection on `InferenceClient.default_model` and removed `Conversation.model_override`. New unit tests verify the attribute changes, but no existing test proves that a subsequent background operation (`/summarize`, `/compile`, etc.) actually sends the switched model name to the inference server.

This spec adds two focused end-to-end tests that inspect the payload hitting the mock server, closing the regression-coverage gap.

## Goals

- Prove that after `/model <name>`, a subsequent `/summarize` call sends `<name>` in its `/v1/chat/completions` payload
- Prove that after `/model <name>`, a subsequent `/compile` call sends `<name>` in every inference request it generates (categorize + topic match + compile)
- Add the minimal mock-server instrumentation needed to verify the above

## Non-Goals

- Testing `/research` end-to-end (too many moving parts — SearxNG mock, URL fetcher, etc. Not a good unit-test target.)
- Testing chat end-to-end (already covered implicitly by existing daemon tests and the attribute-change test)
- Testing Discord adapter model switching (separate transport, separate concern)

## Design

### Mock Server Instrumentation

In `tests/conftest.py`:

- Add module-level `REQUEST_LOG: list[dict] = []`.
- At the top of `mock_chat_completions`, append the parsed request body to `REQUEST_LOG` before any branching logic.

The list captures every `/v1/chat/completions` request the daemon makes. Each entry is the full JSON body (includes `model`, `messages`, `stream`, `tools`, `chat_template_kwargs`, etc.). Only `/v1/chat/completions` entries are recorded — `/model` validation hits `/v1/models` which is a separate handler and does not touch `REQUEST_LOG`.

### Test-Local State Management

Each test file that uses `REQUEST_LOG` defines its own autouse fixture to clear the list:

```python
@pytest.fixture(autouse=True)
def _clear_request_log():
    from tests.conftest import REQUEST_LOG
    REQUEST_LOG.clear()
    yield
```

Scoped per-file rather than globally because most tests don't care about request inspection; clearing only where needed avoids surprising interactions.

### Tests

Both tests live in `tests/test_daemon.py` alongside the existing `/model` tests.

#### `test_model_switch_routes_summarize_to_new_model`

```
1. running_daemon fixture starts with config.model = "test-model"
2. Write a raw file with frontmatter to tmp_path/vault/raw/web/test.md
3. PalClient: send /model gemma-4-26b-a4b-it-q4_k_m
4. PalClient: send /summarize raw/web/test.md
5. Inspect REQUEST_LOG (all captured entries from this test are chat/completions)
6. Assert at least one entry has body["model"] == "gemma-4-26b-a4b-it-q4_k_m"
7. Assert no entry uses "test-model"
```

#### `test_model_switch_routes_compile_to_new_model`

```
1. running_daemon fixture with config.model = "test-model"
2. Write a summary file to tmp_path/vault/raw/summaries/test.md
3. PalClient: /model gemma-4-26b-a4b-it-q4_k_m
4. PalClient: /compile raw/summaries/test.md
   (generates at least 2 inference calls: categorize, then compile.
    topic matching may or may not fire depending on whether the category
    has existing articles; with a fresh vault it short-circuits.)
5. Inspect REQUEST_LOG (all captured entries from this test are chat/completions)
6. Assert every captured entry has body["model"] == "gemma-4-26b-a4b-it-q4_k_m"
```

The per-entry assertion is stronger than checking "at least one" — it proves no code path accidentally falls back to a hardcoded or config-default model.

### What the Tests Prove

- Model switching propagates to `/summarize` (single inference call site)
- Model switching propagates to `/compile` across multiple inference call sites (categorize, compile, and topic-match when applicable)
- No call site in these paths bypasses `InferenceClient.default_model`

If a future change accidentally reintroduces a hardcoded model argument somewhere in the compile or summarize pipeline, these tests fail.

## Files Modified

- `tests/conftest.py`:
  - Add `REQUEST_LOG: list[dict] = []` at module scope
  - Modify `mock_chat_completions` to append `body` to `REQUEST_LOG` at entry
- `tests/test_daemon.py`:
  - Add autouse fixture `_clear_request_log`
  - Add `test_model_switch_routes_summarize_to_new_model`
  - Add `test_model_switch_routes_compile_to_new_model`

No production code changes.

## Verification

```bash
cd /home/edible/Projects/PAL
.venv/bin/pytest tests/test_daemon.py -v
.venv/bin/pytest tests/ -v
```

Expected: both new tests pass. Full suite count goes from 380 to 382. No regressions.
