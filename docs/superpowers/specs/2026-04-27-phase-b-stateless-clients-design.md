# Phase B: Stateless Clients Migration Design

Status: design approved, ready for implementation planning
Date: 2026-04-27
Origin: PAL repo. Move to agent_core when that repo takes over docs ownership.
Parent: `docs/superpowers/specs/2026-04-25-agent-core-extraction-design.md` (umbrella extraction design)
Predecessor: `docs/superpowers/plans/2026-04-25-agent-core-extraction-phase-a.md` (Phase A, merged 2026-04-26)

## Context

Phase A extracted PAL's leaf utilities (`frontmatter`, `chunker`, `sanitizer`, `converter`, `fetcher`) into `agent_core` as the first installment of the umbrella extraction. PAL now consumes `agent_core@v0.1.1` and ships on `main`.

Phase B continues the leaves-inward sequencing from the umbrella spec by moving PAL's stateless HTTP clients into `agent_core`. These modules talk to the inference server, the vector collections API, and SearxNG. They are pure clients with no PAL-specific behavior, and crucially they are already constructor-injected today: PAL's `daemon.py` reads its `Config` at startup and passes the relevant fields into each client. The clients themselves do not read environment variables. That makes Phase B nearly as mechanical as Phase A.

One transitive dependency forces a small early move: `pal/inference.py` imports `pal/reasoning.py` for per-model reasoning control. `reasoning.py` was originally listed for a later phase, but it has to travel with `inference` to avoid a forbidden import direction (agent_core importing from pal). It is small (52 LOC) and not actually stateful, so promoting it into Phase B is the cleaner of the available options.

## Goals

1. Move four modules from PAL into agent_core: `reasoning`, `inference`, `retrieval`, `websearch`.
2. Preserve byte-identical migration where possible. The only edit required to the source modules is a `Protocol`-based fix to `reasoning.py` so it does not import from `pal.conversation`.
3. Tag `agent_core@v0.2.0` and migrate PAL to consume it. PAL keeps shipping throughout; per-module commits preserve bisect.
4. Maintain Phase A's commit hygiene and verification discipline (per-module commits, broad smoke checklist, clean-install probe before merge).

## Non-Goals

1. Changing the public API of any of the four modules. `InferenceClient(base_url, model, is_batch)`, `RetrievalClient(base_url, collection_id)`, `WebSearchClient(base_url, timeout=30.0)` keep their constructor signatures. Callers see no behavior change.
2. Designing or introducing `agent_core.BaseConfig`. That belongs to Phase E (daemon migration) when there is a real consumer for shared config loading. Phase B clients continue to receive primitive fields from whichever caller constructs them.
3. Any per-agent env var prefix machinery, agent class scaffolding, or template repo work. Those remain umbrella-spec items for later phases.
4. Folding any other Phase C modules (`wisdom`, `learning`, `profile`, `allowlist`, `approval_registry`, `learning_scanner`) into this phase. Those have stateful storage decisions that warrant their own design pass.
5. Refactoring `pal/inference.py`'s retry, batch-fallback, or tool-call parsing logic. Migration is byte-identical other than the import path.

## Architecture

The dependency direction stays the same: PAL depends on agent_core via a git-pinned tag.

```
inference_server (HTTP, on 192.168.1.14)
        ^
        | httpx
        |
   +----+--------+
   |  agent_core | <- pip dep -- PAL
   |  v0.2.0     |
   +-------------+
```

After Phase B, `agent_core` exposes nine modules:

```
agent_core/
    utils/
        frontmatter.py     (Phase A)
        chunker.py         (Phase A)
        sanitizer.py       (Phase A)
        converter.py       (Phase A)
        fetcher.py         (Phase A)
    reasoning.py           (Phase B)
    inference.py           (Phase B)
    retrieval.py           (Phase B)
    websearch.py           (Phase B)
```

The four Phase B modules live at the top level of the package, matching the umbrella spec's package layout. They are not in `utils/` because they are not utilities; they are clients/control surfaces specific to their respective remote services.

## Modules and Migration Order

Order is forced by `inference.py`'s import of `reasoning`. Reasoning must reach agent_core before inference does. The other two modules have no internal PAL imports and can land in any order; alphabetical after reasoning keeps the sequence predictable.

### 1. `reasoning.py` (52 LOC)

Moves to `agent_core/reasoning.py`. Required edit: replace the `TYPE_CHECKING` import from `pal.conversation` with a local `Protocol` describing exactly what `decide_mode` reads from its argument.

Current code:
```python
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pal.conversation import Conversation

def decide_mode(conversation: Conversation) -> Literal["on", "off"]:
    override = getattr(conversation, "reasoning_override", None)
    if override in ("on", "off"):
        return override
    return "off"
```

Migrated code:
```python
from typing import Literal, Protocol

class _ConversationLike(Protocol):
    reasoning_override: Literal["on", "off"] | None

def decide_mode(conversation: _ConversationLike) -> Literal["on", "off"]:
    override = getattr(conversation, "reasoning_override", None)
    if override in ("on", "off"):
        return override
    return "off"
```

The runtime behavior is unchanged. The Protocol documents the duck-typed contract that already existed implicitly. PAL's `Conversation` class continues to satisfy it without any annotation work on PAL's side.

`shape_request` and `extract_reasoning` move byte-identically.

### 2. `inference.py` (252 LOC)

Moves to `agent_core/inference.py`. The only change is the import line `from pal.reasoning import shape_request, extract_reasoning` becoming `from agent_core.reasoning import shape_request, extract_reasoning`. Otherwise byte-identical.

`InferenceClient`, `BatchUnavailableError`, `ToolCall`, and `CompletionResult` keep their public API.

### 3. `retrieval.py` (134 LOC)

Moves to `agent_core/retrieval.py`. Byte-identical. No PAL imports to rewrite.

### 4. `websearch.py` (41 LOC)

Moves to `agent_core/websearch.py`. Byte-identical. No PAL imports to rewrite.

## Test Migration

Each PAL test file for a moved module follows the same pattern as Phase A:

1. Source-module test file moves to `agent_core/tests/test_<module>.py` with import-line rewrites (`from pal.<module>` becomes `from agent_core.<module>`, plus any `monkeypatch.setattr("pal.<module>.foo", ...)` string-form references).
2. PAL's local copy of the test file is deleted in the PAL-side migration commit.

Expected test files to move (verify during planning):
- `tests/test_reasoning.py` if it exists (PAL has reasoning-control tests)
- `tests/test_inference.py`
- `tests/test_retrieval.py` if it exists
- `tests/test_websearch.py` if it exists

Tests that import the modules from other PAL test files (for instance, `tests/test_research_commands.py` may monkey-patch `pal.fetcher.check_url_safety`-style references for inference/retrieval) get their imports rewritten in place but stay in PAL.

## agent_core Release

After all four module-add commits land, tag `v0.2.0` on agent_core. The version bump in `pyproject.toml` happens in the same commit as the tag (lesson from Phase A: do not let `version` lag behind the tag). Push tag, verify CI green, verify clean install of `agent_core@v0.2.0` resolves all 9 modules (Phase A's 5 plus Phase B's 4).

No new agent_core runtime dependencies. `httpx` is already declared from Phase A. `reasoning.py` and `websearch.py` use only stdlib plus httpx. No new dev dependencies either.

## PAL-Side Tasks

Six commits on a feature branch, mirroring Phase A's structure:

1. **Bump PAL dependency to `agent_core@v0.2.0`** in `pyproject.toml`. Reinstall editable. Run targeted PAL tests for the four affected modules to confirm baseline (PAL still uses its local `pal.<module>` paths at this point).

2. **Migrate `reasoning` usage.** Rewrite `from pal.reasoning` and `import pal.reasoning` (plus string-form references such as `monkeypatch.setattr("pal.reasoning....", ...)`) to `agent_core.reasoning`. Delete `pal/reasoning.py` and `tests/test_reasoning.py` if it exists. Run tests.

3. **Migrate `inference` usage.** Same pattern. PAL's daemon and several other modules import `InferenceClient`. Delete `pal/inference.py` and `tests/test_inference.py`. Run tests.

4. **Migrate `retrieval` usage.** Same pattern. Delete `pal/retrieval.py` and any matching test file. Run tests.

5. **Migrate `websearch` usage.** Same pattern. Delete `pal/websearch.py` and any matching test file. Run tests.

6. **Final smoke and clean install verification.** Daemon starts and accepts `/help`. The five Phase A utilities and the four Phase B modules all import in a fresh venv. Broad PAL test suite passes (excluding the known-flaky integration files identified during Phase A: `test_daemon.py`, `test_integration.py`, `test_chat_research_integration.py`, `test_consolidate_integration.py`, `test_learning_e2e.py`). Open PR to main.

## Testing Strategy

### agent_core

Each migration commit on agent_core moves the module's tests alongside it. After Phase B, agent_core's suite covers:

- Phase A's 5 utility test files (62 tests as of v0.1.1)
- Phase B's 4 client test files (count to be determined when test files move; estimate 30-50 additional tests based on PAL's current coverage)

CI runs the full agent_core suite on every push and PR. The `[project.optional-dependencies] dev` group already includes everything needed (`pytest`, `pytest-asyncio`, `uvicorn[standard]`, `starlette`); no additions expected.

### PAL

Each PAL migration commit must satisfy:

- The module being migrated has zero remaining `pal.<module>` references in `pal/` and `tests/` (broad grep including string-form patterns).
- PAL's local copy of the module is deleted.
- The targeted PAL test files for callers of the migrated module pass.
- Daemon imports cleanly via `python -c "from pal.daemon import Daemon"` even before tests run.

Final commit must satisfy:

- Broad PAL pytest excluding the known-flaky integration files passes.
- `pal-daemon` starts cleanly, listens on socket, `/help` round-trips through `pal` CLI.
- Clean-install probe in a fresh venv: all 9 agent_core modules and `pal.daemon.Daemon` import without errors.
- Manual inference smoke (chat turn, `/research`, `/think`) deferred to the user's environment with the inference server reachable; documented as required pre-merge step in the PR description.

## Migration Risk and Mitigation

The migration is mechanical, but Phase A surfaced a few issues worth pre-empting:

1. **String-form references.** PAL has tests that monkeypatch via string paths like `"pal.fetcher.check_url_safety"`. Phase A discovered this only when Task 6's tests failed. Phase B's grep pre-flight uses the broader pattern from the start: `grep -rnE "pal\.<module>|\"pal\.<module>|'pal\.<module>" pal/ tests/`.

2. **Hidden conftest fixtures.** Phase A's Task 6 needed a trimmed `conftest.py` in agent_core for the fetcher's mock HTTP server fixture. The inference and retrieval tests likely have similar fixtures (PAL's conftest has a `mock_inference_server` already used by Phase A's fetcher tests). The Phase B implementer should expect to either reuse the existing trimmed conftest or extend it for the new tests.

3. **Test fixtures on disk.** Phase A's Task 5 needed a 89-byte CSV fixture. Inference/retrieval tests may need similar small fixtures. Implementer should grep test files for `fixtures/` references before assuming a clean copy.

4. **The `test_daemon.py` hang is pre-existing.** Verified during Phase A: it hangs on PAL's `main` checkout regardless of Phase A or B work. Skip it in test runs; do not let it block migration commits.

5. **Dev-dep gaps.** Phase A discovered uvicorn/starlette were missing from agent_core's dev extras only after the fetcher tests failed. Phase B's CI run on the v0.2.0 tag is the gate that catches any analogous gap before PAL bumps its pin.

## Out of Scope (parked for later phases)

These items are intentionally not addressed in Phase B:

- `agent_core.BaseConfig` and the configurable env-prefix machinery. Phase E (daemon).
- `agent_core.Agent` base class with extension points (system prompt, commands, tools). Phase E.
- Stateful manager migrations (`wisdom`, `learning`, `profile`, `allowlist`, `approval_registry`, `channels`, `scratchpad`, `conversation`). Phase C and Phase D.
- The `Agent_Template` repo. Phase G.
- PAL's `pyyaml` dep cleanup (currently direct, only used in tests). Tracked as a follow-up; revisit when convenient.

## Decisions Summary

| Decision | Choice |
|---|---|
| Modules in scope | `reasoning`, `inference`, `retrieval`, `websearch` (4 modules) |
| Migration sequencing | `reasoning` first (forced by inference's import), then alphabetical |
| Reasoning's TYPE_CHECKING fix | Replace pal.conversation import with a local Protocol |
| Module placement in agent_core | Top level (peer of `utils/`), matches umbrella spec |
| agent_core release tag | `v0.2.0` with matching pyproject version |
| New runtime deps | None |
| New dev deps | None expected; Phase A already added uvicorn/starlette |
| PAL-side commit count | 6 (1 dep bump, 4 migrations, 1 final smoke) |
| agent_core commit count | 5 (4 modules, 1 version bump) plus the v0.2.0 tag |
| Pace | Per-module commits, leaves-inward, same as Phase A |
| Bundling other Phase C modules | No, hold the line |
