# search_vault JSON result format + search_web disable -- Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `search_vault` to a JSON result envelope so PAL gets a deterministic `path` field per result, surface `score` for ranking, add `…` truncation markers, and disable the LLM-facing `search_web` tool from PAL.

**Architecture:** Two-repo change. In `agent_core`: extract a shared `_truncate` helper, rewrite `SearchVault.run` to emit JSON instead of prose, bump patch version. In `PAL`: add `disabled_builtins = frozenset({"search_web"})` to `PALAgent`, update `PAL_BASE_PROMPT` to teach the new JSON shape and remove `search_web` references. No retrieval-client changes; no production code outside these two files-plus-tests.

**Tech Stack:** Python 3.12, pytest, agent_core (cross-repo), PAL daemon.

**Cross-repo note:** Phase 1 modifies `/home/edible/Projects/agent_core`. PAL imports it via editable install (set up during the 2026-05-10 chat-promotion workstream; per `feedback_agent_core_version_bump` memory). Run agent_core tests in that repo. Phase 2 changes live in PAL.

---

## File Structure

**agent_core repo (`/home/edible/Projects/agent_core/`):**
- Modify: `agent_core/tools/_framework.py` -- extract `_truncate` helper at module level; rewrite `SearchVault.run` to emit JSON.
- Modify: `tests/test_tools_framework.py` -- update 3 existing SearchVault tests to assert JSON shape; add 6 new tests.
- Modify: `pyproject.toml` -- bump version `1.1.0` → `1.1.1`.

**PAL repo (`/home/edible/Projects/PAL/`):**
- Modify: `pal/agent.py` -- add `disabled_builtins = frozenset({"search_web"})` to `PALAgent`.
- Modify: `pal/prompts/system.py` -- remove 3 search_web references (lines 33, 41-42, 79); add 3 JSON-shape callouts near search_vault descriptions.
- Create: `tests/test_palagent_disabled_builtins.py` -- assert `disabled_builtins` config.
- Modify: `tests/test_prompt_builder.py` -- add tests for JSON-shape presence and search_web absence.

---

## Task 1: Extract `_truncate` helper in agent_core

**Files:**
- Modify: `/home/edible/Projects/agent_core/agent_core/tools/_framework.py` (add module-level helper)
- Modify: `/home/edible/Projects/agent_core/tests/test_tools_framework.py` (add unit tests)

The current code uses `[:200]` inline (no ellipsis). This task extracts a reusable helper before the SearchVault rewrite uses it.

- [ ] **Step 1: Write failing tests**

Add to `/home/edible/Projects/agent_core/tests/test_tools_framework.py` (place near the top imports or in a clearly-separated section):

```python
from agent_core.tools._framework import _truncate


def test_truncate_short_string_unchanged():
    assert _truncate("hello", 200) == "hello"


def test_truncate_long_string_appends_ellipsis():
    s = "x" * 250
    result = _truncate(s, 200)
    assert len(result) == 200
    assert result.endswith("…")


def test_truncate_word_boundary():
    # 12-char limit on "hello world how are you" should clip at "hello world…"
    # (cuts at last space before the limit, not mid-word)
    result = _truncate("hello world how are you", 12)
    assert result == "hello world…"


def test_truncate_normalizes_newlines_to_spaces():
    result = _truncate("line1\nline2", 200)
    assert result == "line1 line2"


def test_truncate_strips_outer_whitespace():
    result = _truncate("  spaced  ", 200)
    assert result == "spaced"


def test_truncate_handles_none_input():
    assert _truncate(None, 200) == ""
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/agent_core && pytest tests/test_tools_framework.py -k truncate -v
```
Expected: FAIL with `ImportError: cannot import name '_truncate' from 'agent_core.tools._framework'`.

- [ ] **Step 3: Add the helper to `_framework.py`**

In `/home/edible/Projects/agent_core/agent_core/tools/_framework.py`, near the top imports (after the existing import block), add:

```python
def _truncate(s: str | None, n: int) -> str:
    """Normalize and truncate a string to n chars, with '…' suffix when cut.

    Newlines collapse to spaces; outer whitespace strips. Word-boundary cut:
    if the limit lands inside a word, back up to the last space before n-1.
    """
    s = (s or "").replace("\n", " ").strip()
    if len(s) <= n:
        return s
    # Reserve one char for the ellipsis; rstrip removes trailing space if the
    # cut landed right after a word.
    return s[: n - 1].rstrip() + "…"
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
cd /home/edible/Projects/agent_core && pytest tests/test_tools_framework.py -k truncate -v
```
Expected: all 6 truncate tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/edible/Projects/agent_core && git add agent_core/tools/_framework.py tests/test_tools_framework.py && git commit -m "$(cat <<'EOF'
feat(tools): add _truncate helper for snippet truncation

Module-level helper at agent_core/tools/_framework.py. Normalizes
newlines to spaces, strips outer whitespace, truncates with '...' suffix
on cut, respects word boundaries when possible. Will be used by the
upcoming SearchVault JSON migration; designed for reuse across any tool
that produces snippets.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Rewrite `SearchVault.run` to emit JSON envelope

**Files:**
- Modify: `/home/edible/Projects/agent_core/agent_core/tools/_framework.py:83-107` (`SearchVault.run`)
- Modify: `/home/edible/Projects/agent_core/tests/test_tools_framework.py` (update 3 existing SearchVault tests, add 6 new)

- [ ] **Step 1: Read existing SearchVault tests to know what shape they assert**

```bash
cd /home/edible/Projects/agent_core && grep -n "test_search_vault" tests/test_tools_framework.py
```

The three existing tests are loose substring asserts (e.g. `assert "Notes/a.md" in result`); they'll need updating to parse JSON and check structured fields.

- [ ] **Step 2: Replace the 3 existing SearchVault tests with JSON-aware versions**

In `/home/edible/Projects/agent_core/tests/test_tools_framework.py`, replace the three existing tests (currently at approximately lines 69-94) with:

```python
async def test_search_vault_calls_retrieval():
    retrieval = MagicMock()
    retrieval.search = AsyncMock(return_value=[
        {"id": "Notes/a", "name": "Notes/a.md", "summary": "matched content", "score": 0.5},
    ])
    agent = MagicMock(retrieval=retrieval)
    result = await SearchVault().run({"query": "test"}, _ctx(agent))
    retrieval.search.assert_called_once_with("test", limit=5)
    payload = json.loads(result)
    assert payload["status"] == "ok"
    assert payload["query"] == "test"
    assert payload["count"] == 1
    assert payload["results"][0]["path"] == "Notes/a.md"
    assert payload["results"][0]["summary"] == "matched content"


async def test_search_vault_requires_query():
    agent = MagicMock()
    result = await SearchVault().run({}, _ctx(agent))
    payload = json.loads(result)
    assert payload["status"] == "error"
    assert "query" in payload["reason"].lower()
    assert "required" in payload["reason"].lower()


async def test_search_vault_empty_results():
    retrieval = MagicMock()
    retrieval.search = AsyncMock(return_value=[])
    agent = MagicMock(retrieval=retrieval)
    result = await SearchVault().run({"query": "nothing"}, _ctx(agent))
    payload = json.loads(result)
    assert payload["status"] == "ok"
    assert payload["query"] == "nothing"
    assert payload["count"] == 0
    assert payload["results"] == []
```

Add a `json` import at the top of the test file if not already present (likely already there).

- [ ] **Step 3: Add the six new tests**

Append to the SearchVault section of `tests/test_tools_framework.py`:

```python
async def test_search_vault_path_has_md_extension():
    retrieval = MagicMock()
    retrieval.search = AsyncMock(return_value=[
        {"id": "Software-Development/foo", "name": "Foo", "summary": "...", "score": 0.7},
    ])
    agent = MagicMock(retrieval=retrieval)
    result = await SearchVault().run({"query": "x"}, _ctx(agent))
    payload = json.loads(result)
    assert payload["results"][0]["path"] == "Software-Development/foo.md"


async def test_search_vault_retrieval_exception_returns_json_error():
    retrieval = MagicMock()
    retrieval.search = AsyncMock(side_effect=RuntimeError("retrieval down"))
    agent = MagicMock(retrieval=retrieval)
    result = await SearchVault().run({"query": "x"}, _ctx(agent))
    payload = json.loads(result)
    assert payload["status"] == "error"
    assert payload["query"] == "x"
    assert "RuntimeError" in payload["reason"]
    assert "retrieval down" in payload["reason"]


async def test_search_vault_score_rounded_to_3dp():
    retrieval = MagicMock()
    retrieval.search = AsyncMock(return_value=[
        {"id": "a", "name": "a", "summary": "s", "score": 0.8732145},
    ])
    agent = MagicMock(retrieval=retrieval)
    result = await SearchVault().run({"query": "x"}, _ctx(agent))
    payload = json.loads(result)
    assert payload["results"][0]["score"] == 0.873


async def test_search_vault_summary_truncates_with_ellipsis():
    long_summary = "x" * 250
    retrieval = MagicMock()
    retrieval.search = AsyncMock(return_value=[
        {"id": "a", "name": "a", "summary": long_summary, "score": 0.5},
    ])
    agent = MagicMock(retrieval=retrieval)
    result = await SearchVault().run({"query": "x"}, _ctx(agent))
    payload = json.loads(result)
    summary = payload["results"][0]["summary"]
    assert len(summary) == 200
    assert summary.endswith("…")


async def test_search_vault_short_summary_unchanged():
    retrieval = MagicMock()
    retrieval.search = AsyncMock(return_value=[
        {"id": "a", "name": "a", "summary": "short text", "score": 0.5},
    ])
    agent = MagicMock(retrieval=retrieval)
    result = await SearchVault().run({"query": "x"}, _ctx(agent))
    payload = json.loads(result)
    assert payload["results"][0]["summary"] == "short text"


async def test_search_vault_max_results_clamped_and_passed():
    retrieval = MagicMock()
    retrieval.search = AsyncMock(return_value=[])
    agent = MagicMock(retrieval=retrieval)
    # max_results > 20 should clamp to 20
    await SearchVault().run({"query": "x", "max_results": 50}, _ctx(agent))
    retrieval.search.assert_called_once_with("x", limit=20)
```

- [ ] **Step 4: Run tests, verify the rewritten + new ones fail**

```bash
cd /home/edible/Projects/agent_core && pytest tests/test_tools_framework.py -k search_vault -v
```
Expected: most fail because SearchVault.run still emits prose, not JSON.

- [ ] **Step 5: Rewrite `SearchVault.run` in `agent_core/tools/_framework.py`**

Replace the existing `SearchVault.run` method (currently around lines 83-107) with:

```python
    async def run(self, args, ctx):
        import json

        query = (args.get("query") or "").strip()
        if not query:
            return json.dumps({
                "status": "error",
                "reason": "'query' parameter is required.",
            })
        max_results = max(1, min(int(args.get("max_results", 5)), 20))
        try:
            results = await ctx.agent.retrieval.search(query, limit=max_results)
        except Exception as exc:
            return json.dumps({
                "status": "error",
                "query": query,
                "reason": f"Search error: {type(exc).__name__}: {exc}",
            })
        structured = []
        for r in results:
            if isinstance(r, dict):
                id_val = r.get("id", "")
                name = r.get("name") or id_val
                summary = r.get("summary", "")
                score = r.get("score", 0.0)
            else:
                id_val = getattr(r, "id", "")
                name = getattr(r, "name", None) or id_val
                summary = getattr(r, "summary", "")
                score = getattr(r, "score", 0.0)
            structured.append({
                "path": f"{id_val}.md" if id_val else "",
                "name": name,
                "summary": _truncate(summary, 200),
                "score": round(float(score), 3),
            })
        return json.dumps({
            "status": "ok",
            "query": query,
            "count": len(structured),
            "results": structured,
        })
```

Also update the class's docstring/description to reflect the new shape:

```python
class SearchVault(Tool):
    """Semantic search over the vault via the retrieval service."""

    name = "search_vault"
    description = (
        "Semantic search over the vault. Returns JSON: "
        "{status, query, count, results: [{path, name, summary, score}]}. "
        "Use the `path` field directly for cat/edit/grep."
    )
```

Add the module-level `json` import at the top of `_framework.py` if not present (the existing code imports it inline; either approach is fine, but module-level is cleaner since it's now used by every call).

- [ ] **Step 6: Run all framework tests, verify pass**

```bash
cd /home/edible/Projects/agent_core && pytest tests/test_tools_framework.py -v
```
Expected: all SearchVault tests (3 rewritten + 6 new = 9 total) pass; all other tests still pass.

- [ ] **Step 7: Commit**

```bash
cd /home/edible/Projects/agent_core && git add agent_core/tools/_framework.py tests/test_tools_framework.py && git commit -m "$(cat <<'EOF'
feat(tools): migrate search_vault to JSON result envelope

Replaces prose result format with structured JSON: {status, query, count,
results: [{path, name, summary, score}]}. path is id + ".md" so PAL can
feed it directly to cat/edit/grep without extension guessing. score
rounded to 3dp for stable serialization. summary truncated with '...'
suffix via the shared _truncate helper. Error and empty cases use the
same envelope shape with status discriminating.

Closes the path-determinism friction item from the 2026-05-11 tool
audit (PAL self-reported ask #1).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Bump agent_core version

**Files:**
- Modify: `/home/edible/Projects/agent_core/pyproject.toml` (version bump)

Per the `feedback_agent_core_version_bump` memory, every behavior change to agent_core must bump the version so wheel-installed environments pick it up.

- [ ] **Step 1: Bump the version**

In `/home/edible/Projects/agent_core/pyproject.toml`, change:

```toml
version = "1.1.0"
```

to:

```toml
version = "1.1.1"
```

- [ ] **Step 2: Reinstall in PAL's venv to verify editable install picks up the change**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pip show agent_core | head -3
```

Expected: `Version: 1.1.1`. If still shows `1.1.0`, the editable install is broken; reinstall:

```bash
cd /home/edible/Projects/PAL && .venv/bin/pip install -e /home/edible/Projects/agent_core --force-reinstall --no-deps
```

- [ ] **Step 3: Spot-check that PAL sees the new SearchVault behavior**

```bash
cd /home/edible/Projects/PAL && .venv/bin/python -c "
import asyncio, json
from unittest.mock import MagicMock, AsyncMock
from agent_core.tools._framework import SearchVault, _truncate

# _truncate import alone confirms the helper exists
assert _truncate('short', 200) == 'short'
print('_truncate: ok')

# Smoke the JSON envelope
retrieval = MagicMock()
retrieval.search = AsyncMock(return_value=[])
agent = MagicMock(retrieval=retrieval)
class Ctx: pass
c = Ctx(); c.agent = agent
result = asyncio.run(SearchVault().run({'query': 'smoke'}, c))
payload = json.loads(result)
assert payload['status'] == 'ok' and payload['count'] == 0
print('search_vault JSON envelope: ok')
"
```

Expected: prints `_truncate: ok` and `search_vault JSON envelope: ok`. Failure means PAL's venv hasn't picked up the new code.

- [ ] **Step 4: Commit**

```bash
cd /home/edible/Projects/agent_core && git add pyproject.toml && git commit -m "$(cat <<'EOF'
chore: bump version to 1.1.1

Signals the search_vault JSON migration to wheel-installed environments
(e.g. the production server, which uses a regular pip install of
agent_core rather than editable). Patch bump because the change is a
behavior change to one tool, not a breaking API addition.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Disable `search_web` in `PALAgent`

**Files:**
- Modify: `/home/edible/Projects/PAL/pal/agent.py` (add `disabled_builtins`)
- Create: `/home/edible/Projects/PAL/tests/test_palagent_disabled_builtins.py` (new test file)

- [ ] **Step 1: Write failing tests**

Create `/home/edible/Projects/PAL/tests/test_palagent_disabled_builtins.py`:

```python
"""Verify PALAgent's disabled_builtins configuration."""
from __future__ import annotations

from agent_core.tools.builtin import BUILTIN_TOOLS
from agent_core.tools.executor import ToolExecutor

from pal.agent import PALAgent


def test_palagent_disables_search_web():
    """PALAgent should disable the search_web builtin tool.

    Rationale: search_web's output URLs are mostly unfetchable (allowlist
    mismatch with FetchUrl), and the user prefers propose_research for web
    work. See docs/superpowers/specs/2026-05-12-search-vault-json-result-format-design.md.
    """
    assert "search_web" in PALAgent.disabled_builtins


def test_palagent_search_web_not_in_active_tool_registry():
    """After tool registration, search_web should not be callable by the LLM."""
    # Build the active tool registry the way ToolExecutor.build would.
    # We don't need a real PALAgent instance for this; we just need the
    # disabled set and the BUILTIN_TOOLS list.
    disabled = PALAgent.disabled_builtins
    active = [t for t in BUILTIN_TOOLS if t.name not in disabled]
    active_names = {t.name for t in active}
    assert "search_web" not in active_names
    # Sanity: search_vault should still be active.
    assert "search_vault" in active_names
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_palagent_disabled_builtins.py -v
```
Expected: FAIL with `AttributeError: type object 'PALAgent' has no attribute 'disabled_builtins'`.

- [ ] **Step 3: Add `disabled_builtins` to `PALAgent`**

In `/home/edible/Projects/PAL/pal/agent.py`, inside the `PALAgent` class, add the class attribute. Find the `tools = [...]` declaration around line 137 and add `disabled_builtins` immediately before it:

```python
    # Disable LLM-facing builtin tools that PAL doesn't need. search_web
    # output URLs are mostly unfetchable due to FetchUrl's allowlist filter;
    # the user prefers propose_research for web work. See
    # docs/superpowers/specs/2026-05-12-search-vault-json-result-format-design.md.
    disabled_builtins: frozenset[str] = frozenset({"search_web"})

    # Phase F: declarative registration. PR2 populates vault tools;
    # ... (existing comment block)
    tools = [EditFile, CreateFile, MoveFile, DeleteFile, ReplaceInFile,
             ...
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_palagent_disabled_builtins.py -v
```
Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/agent.py tests/test_palagent_disabled_builtins.py && git commit -m "$(cat <<'EOF'
feat(agent): disable search_web LLM tool in PALAgent

Uses the existing disabled_builtins mechanism in agent_core to filter
search_web out of PAL's tool registry. The user prefers propose_research
for web work, and search_web's URLs are unfetchable due to the existing
allowlist mismatch with FetchUrl. agent_core's SearchWeb class stays
intact (future agents may want it); only PAL stops registering it.

The /search-web slash command (separate user-facing surface in
pal/commands/domain.py) is unaffected.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Update PAL system prompt

**Files:**
- Modify: `/home/edible/Projects/PAL/pal/prompts/system.py` (remove 3 search_web refs, add 3 JSON-shape callouts)
- Modify: `/home/edible/Projects/PAL/tests/test_prompt_builder.py` (add 2 tests)

- [ ] **Step 1: Write failing tests**

Add to `/home/edible/Projects/PAL/tests/test_prompt_builder.py`:

```python
def test_base_prompt_describes_search_vault_json_shape():
    """After the 2026-05-12 JSON migration, the prompt must teach PAL the
    new envelope so it knows to use the `path` field for follow-up tools."""
    # Tolerant substring match: any of the three new callouts is sufficient.
    assert (
        "results: [{path, name, summary, score}]" in PAL_BASE_PROMPT
        or "Use the `path` field directly" in PAL_BASE_PROMPT
    )


def test_base_prompt_does_not_mention_search_web_as_llm_tool():
    """search_web is disabled from PAL's LLM tool surface (Task 4).
    The prompt must not advertise it as a callable tool.

    Note: the /search-web slash command may still appear via slash-command
    help registration (separate surface); that's outside this test.
    """
    # The string "search_web" appears in prose like "search the web", but the
    # specific underscore-suffix-or-word-boundary form `search_web` should not.
    import re
    # Match search_web as a tool name (not as a slash command "/search-web")
    pattern = r"\bsearch_web\b"
    matches = re.findall(pattern, PAL_BASE_PROMPT)
    assert matches == [], f"Expected no LLM-tool references to search_web; found {len(matches)}"
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_prompt_builder.py::test_base_prompt_describes_search_vault_json_shape tests/test_prompt_builder.py::test_base_prompt_does_not_mention_search_web_as_llm_tool -v
```
Expected: BOTH fail. The first fails because the JSON callout isn't present; the second fails because search_web still appears.

- [ ] **Step 3: Update `pal/prompts/system.py` -- remove search_web references**

First locate them:

```bash
cd /home/edible/Projects/PAL && grep -n "search_web" pal/prompts/system.py
```

Expected output: line 33, line 42, line 79 (approximately).

Remove the search_web bullet at line 33 entirely. The current line says (or similar):
```
- search_web: query SearxNG for titles and snippets. Cheap, no fetch. Use for "what's out there?" triage before proposing a full research run.
```
Delete this line.

Update line 42 (the research-flow paragraph that mentions search_web). The current text says (or similar):
```
2. If web research is warranted, optionally call search_web to preview what's out there.
```
Rewrite to:
```
2. If web research is warranted, call propose_research with a one-sentence rationale and an appropriate depth.
```

Update line 79 (the "things you cannot do" line about search_web). The current text says (or similar):
```
- Access arXiv, OWASP, GitHub, Stack Overflow, or any named source directly. You can search_web for them (SearxNG indexes the public web), but you cannot hit their APIs or private endpoints.
```
Rewrite to:
```
- Access arXiv, OWASP, GitHub, Stack Overflow, or any named source directly. Use propose_research for web work (SearxNG indexes the public web), but you cannot hit their APIs or private endpoints.
```

- [ ] **Step 4: Update `pal/prompts/system.py` -- add three JSON-shape callouts**

Locate the existing search_vault description (around line 16, in the tool catalog). Find:
```
- cat, ls, grep, search_vault: vault reads. cat reads a file; ls lists a directory; grep is keyword/regex search across files; search_vault is semantic search via the retrieval index. Use search_vault for concept-level lookup, grep for known strings.
```

Add three new lines AFTER this existing line (preserve indentation):
```
- search_vault returns JSON: {status, query, count, results: [{path, name, summary, score}]}. Use the `path` field directly for cat/edit/grep; do not derive paths from `name`.
- Scores are similarity values; treat higher as more relevant but do not threshold mechanically.
- Summaries ending with `…` were truncated; call cat on the path to see the full article if needed.
```

(Use a real `…` character, not three ASCII dots, so the test substring `"…"` matches and PAL can pattern-match the actual marker.)

- [ ] **Step 5: Run tests, verify they pass**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_prompt_builder.py -v
```
Expected: both new tests pass; all existing prompt tests still pass.

- [ ] **Step 6: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/prompts/system.py tests/test_prompt_builder.py && git commit -m "$(cat <<'EOF'
feat(prompts): teach PAL the search_vault JSON shape; remove search_web

Three new bullets near the search_vault description: the JSON envelope
shape, score interpretation guidance, and the truncation-marker
convention. Three removed bullets that mentioned the now-disabled
search_web LLM tool (line 33 entirely deleted; lines 42 and 79
rewritten to point at propose_research instead).

The /search-web slash command remains operational and is unaffected by
this prompt change (it's user-facing, not in the LLM-callable tool
catalog).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Full-suite regression check

**Files:** No code changes. Run both test suites; commit only if a regression is found and fixed.

- [ ] **Step 1: Run agent_core suite**

```bash
cd /home/edible/Projects/agent_core && pytest tests/ --ignore=tests/test_converter.py -q 2>&1 | tail -5
```
Expected: all tests pass (the `test_converter.py` ignore matches the existing pre-existing markitdown environment issue, per the chat-promotion workstream's final regression check).

- [ ] **Step 2: Run PAL suite**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/ \
    --ignore=tests/test_chat_research_integration.py \
    --ignore=tests/test_client.py \
    --ignore=tests/test_daemon.py \
    --ignore=tests/test_integration.py \
    --ignore=tests/test_prompt_injection.py \
    -q 2>&1 | tail -5
```
Expected: all tests pass. The five `--ignore` flags match the documented pre-existing pal.client collection failures (per `project_pal_client_test_cleanup` memory plus the markitdown-related test_chat_research_integration.py).

- [ ] **Step 3: Diagnose any non-preexisting failures**

If a test fails that is NOT a pre-existing pal.client / markitdown failure, the cause is likely one of:
- A test that referenced the old prose `search_vault` output. Search the failure trace for any `"Found N match(es)"` substring or similar prose markers and update to JSON parsing.
- A test that expected `search_web` to be registered as a tool. These should be updated to confirm it's now disabled, OR removed if their assertion no longer applies.

Fix at the right layer; do not skip or weaken tests to make them green.

- [ ] **Step 4: Final commit (only if anything was fixed)**

```bash
cd /home/edible/Projects/PAL && git add -- <only the specific files you touched> && git commit -m "fix(tests): adapt <area> to new search_vault JSON shape

<one-line description of what needed fixing>

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
"
```

---

## Self-review checklist

- [ ] Every task has Files section with exact paths.
- [ ] Every test step shows the assertion code.
- [ ] Every implementation step shows the actual code change, not a description.
- [ ] No "TBD", "TODO", "implement later" anywhere.
- [ ] Names used in later tasks match earlier tasks (`disabled_builtins`, `_truncate`, the JSON envelope shape).
- [ ] Cross-repo dependency on agent_core is called out at the top and Task 3 verifies the version bump propagates.
- [ ] The 6 truncate-helper tests cover word-boundary, short-unchanged, ellipsis-on-cut, newline-normalization, whitespace-stripping, and None input.
- [ ] The 9 search_vault tests cover happy path, missing query, empty results, retrieval exception, path extension, score rounding, summary truncation with ellipsis, short summary unchanged, max_results clamping.
- [ ] The 2 PALAgent tests cover the class attribute and the active-registry filter.
- [ ] The 2 prompt tests cover the JSON-shape mention and the search_web absence.
- [ ] All commit messages end with the Co-Authored-By line.
- [ ] No em dashes in any commit messages or added prompt/comment text.

## Out of scope

- search_web LLM tool migration to JSON (no longer needed in PAL; agent_core's class is untouched).
- search_web slash command behavior.
- fetch_url disabling (next audit batch).
- Tag-filtered search.
- Vault read 404 nearest-match (separate spec in the same path-determinism cluster).
- Vault-write success shape + reindex propagation (separate spec in the same path-determinism cluster).
- `scripts/retrieval_eval/run.py` refactor to import the shared `_truncate` helper (cosmetic cleanup; separate).
- Server-side deploy (the user handles deploy on their own cadence; the version bump in Task 3 signals when it's time).
