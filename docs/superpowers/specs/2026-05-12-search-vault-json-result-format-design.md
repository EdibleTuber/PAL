# search_vault JSON result format + search_web disable

**Date:** 2026-05-12
**Status:** Design
**Author:** Brainstormed with Claude
**Audit:** `docs/superpowers/audits/2026-05-11-tool-audit-report.md` (path-determinism cluster, item 1 of 3)

## Problem

PAL self-reported on 2026-05-10 that **path determinism** is its dominant tool-use bottleneck (memory: `project_pal_path_determinism`). The pattern: `search_vault` returns a snippet plus a human-readable `name` (frontmatter title or stem fallback), and PAL synthesizes a filename from that snippet to feed into `cat` / `edit_file` / `grep`. The synthesized filename does not match the messy auto-generated string actually on disk, the next tool call fails, and PAL enters a "guessing loop."

Concretely, `agent_core/agent_core/tools/_framework.py:96-107` builds a prose result block from `RetrievalClient.search` output but discards three fields the client already returns: `id` (the deterministic path-without-extension), `score`, and `tags`. Snippets are hard-truncated at 200 chars with no ellipsis marker, so PAL cannot tell whether a match continued past the cut. The tool's docstring does not document the response shape, so PAL has no contract to bind to.

The audit's cross-cutting recommendation is "JSON-everywhere as the tool-output convention," starting with this tool. The user also confirmed during brainstorming that `search_web` (the LLM-facing tool, distinct from the `/search-web` slash command) is functionally unused: the user prefers `propose_research` for web work, and `search_web`'s output URLs are mostly unfetchable due to an existing allowlist mismatch. This spec disables that tool in PAL.

## Goals

1. Migrate `search_vault` to a structured JSON result envelope so the LLM has a deterministic, unambiguous contract.
2. Surface the file `path` (with `.md` extension) as the primary identifier per result, so PAL can feed it directly into vault read/write tools without inference.
3. Surface `score` so PAL has a ranking signal it can reason about.
4. Add `…` truncation markers so PAL can tell when a snippet was cut.
5. Disable `search_web` from PAL's LLM tool surface (keep the agent_core class intact for other agents; keep the `/search-web` slash command for direct user invocation).
6. Update `PAL_BASE_PROMPT` so PAL learns the new shape at the point of use.

## Non-goals

1. **Migrate other tools to JSON in this spec.** The "JSON-everywhere" convention is a stated direction; this spec applies it to one tool. Other tools migrate via their own specs as the audit's needs-spec items are taken up.
2. **Touch agent_core's `SearchWeb` class.** Future agents may want it. We use the existing `disabled_builtins` mechanism to remove it from PAL only.
3. **Touch the `/search-web` slash command.** It's a separate user-facing surface, already applies the allowlist filter correctly, and isn't disabled.
4. **Disable `fetch_url`.** The user flagged it as similarly unused; deferred to the next audit batch.
5. **Expose `tags` as an input parameter or output field.** Tags exist in the data but are not consistently populated, PAL has no current way to use them, and the retrieval client's tag-filter capability is unexposed. Future workstream (tag taxonomy + tag-filtered search).
6. **Implement vault read 404 nearest-match suggestions.** Separate spec in the same path-determinism cluster.
7. **Implement vault-write success shape + reindex propagation.** Separate spec in the same path-determinism cluster.
8. **Recovering from "no inference server" gracefully.** Existing behavior (returns `"Search error: <exc>"` on exception) is preserved; we just restructure as JSON.

## Result format

### Success

`search_vault` returns a JSON string (`json.dumps(...)`) wrapping this envelope:

```json
{
  "status": "ok",
  "query": "vibe-coding strategies",
  "count": 3,
  "results": [
    {
      "path": "Software-Development/vibe-coding-strategies.md",
      "name": "Vibe-coding comprehension strategies",
      "summary": "Synthesis of strategies for reading vibe-coded systems...",
      "score": 0.873
    },
    {
      "path": "PAL/architecture-overview.md",
      "name": "PAL Architecture Overview",
      "summary": "PAL is a personal knowledge agent built on agent_core...",
      "score": 0.821
    },
    ...
  ]
}
```

### Empty (no matches)

```json
{
  "status": "ok",
  "query": "obscure topic with no matches",
  "count": 0,
  "results": []
}
```

### Error

```json
{
  "status": "error",
  "query": "...",
  "reason": "Search error: <exception class>: <exception message>"
}
```

Errors omit `count` and `results` keys entirely. The single `reason` field carries the diagnostic.

### Parameter validation errors (pre-flight)

Missing or empty `query`:
```json
{
  "status": "error",
  "reason": "'query' parameter is required."
}
```

(No `query` field since none was provided.)

## Field semantics

| Field | Type | Source | Notes |
|-------|------|--------|-------|
| `path` | str | derived from client `id` | `id + ".md"` -- the actual vault-relative path PAL feeds to `cat`/`edit_file`/`grep`. Never an empty string for a real result. |
| `name` | str | client `name` | Frontmatter title, with stem fallback. May equal `path` minus extension. Both are surfaced so PAL can choose which to cite. |
| `summary` | str | client `summary`, then `_truncate(s, 200)` | The retrieval snippet, truncated at a word boundary with `…` suffix if cut. Empty string is possible (the client occasionally returns no summary). |
| `score` | float | client `score`, rounded | `round(score, 3)` for stable serialization. Higher is more similar; do not threshold mechanically. |

Deliberately omitted fields:
- `id` (path carries it)
- `tags` (deferred to tag-taxonomy workstream)
- `collection` (internal, no use case)

## Truncation policy

Shared helper extracted from `scripts/retrieval_eval/run.py:27-29` (currently duplicated, this consolidates it):

```python
def _truncate(s: str, n: int) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"
```

Lives as a module-level utility in `agent_core/agent_core/tools/_framework.py`. The `search_vault` formatter uses it for `summary`. The eval script can be refactored to import from there in a separate cleanup (not blocking this spec).

The 200-char limit is preserved from current behavior. The truncation marker is the new behavior. Per spec: a summary ending in `…` signals to PAL that the full text was longer; PAL is told via the prompt addendum to `cat` the path if it needs the rest.

## search_web disable mechanism

PAL's `disabled_builtins` mechanism (already implemented in `agent_core/tools/executor.py:34`) filters tools out of the auto-unioned `BUILTIN_TOOLS` at `ToolExecutor.build()` time. The change:

```python
# pal/agent.py
class PALAgent(Agent):
    ...
    disabled_builtins = frozenset({"search_web"})
```

After this lands:
- `search_web` no longer appears in PAL's tool registry.
- The LLM cannot invoke it.
- agent_core's `SearchWeb` class is untouched (a future second agent could enable it).
- `pal/commands/domain.py:199` (the `SearchWeb` slash command class) is unaffected; `/search-web` continues to work via its own code path with proper allowlist filtering.

## Prompt updates

In `pal/prompts/system.py`, three classes of edit:

**Add (near existing search_vault mentions):** three new bullet additions next to the line-16 description of search_vault, summarizing the new contract. Exact text:

```
- search_vault returns JSON: {status, query, count, results: [{path, name, summary, score}]}. Use the `path` field directly for cat/edit/grep; do not derive paths from `name`.
- Scores are similarity values; treat higher as more relevant but do not threshold mechanically.
- Summaries ending with `…` were truncated; call cat on the path to see the full article if needed.
```

**Remove (search_web references):** delete lines 33, 41-42, and any line 79 mention of `search_web`. The research-flow paragraph that currently says "optionally call search_web to preview" rewrites to point at `propose_research` directly.

**Verify (no orphaned cross-references):** grep `pal/prompts/system.py` for any other `search_web` occurrence and remove or rewrite.

## Behavior preservation

- The retrieval client interface is unchanged. No agent_core retrieval changes.
- `max_results` parameter is unchanged (default 5, max 20).
- Per-result ordering is unchanged (whatever the retrieval client returns).
- Tool name (`search_vault`) is unchanged.
- The `requires = ("retrieval",)` is unchanged.

## Tests

### `agent_core/tests/test_tools_framework.py`

- `test_search_vault_returns_json_envelope` -- fake retrieval returns 3 results, tool output parses as JSON, has `status="ok"`, `count=3`, `results` list of length 3, each result has `path`/`name`/`summary`/`score`.
- `test_search_vault_path_has_md_extension` -- retrieval `id` is `"Foo/bar"`, output `path` is `"Foo/bar.md"`.
- `test_search_vault_empty_results_json` -- fake retrieval returns `[]`, output has `count=0`, `results=[]`, `status="ok"`.
- `test_search_vault_retrieval_exception_returns_json_error` -- retrieval raises, output has `status="error"`, `reason` contains the exception class name.
- `test_search_vault_missing_query_returns_json_error` -- args `{}` returns `status="error"`, `reason="'query' parameter is required."`.
- `test_search_vault_score_rounded_to_3dp` -- retrieval returns `0.8732145`, output `score=0.873`.
- `test_search_vault_summary_truncates_with_ellipsis` -- long summary returns with `…` suffix; under-limit returns verbatim.
- `test_truncate_helper_word_boundary` -- `_truncate("hello world how are you", 12) == "hello world…"` (cuts at space, not mid-word).
- `test_truncate_helper_short_unchanged` -- `_truncate("short", 200) == "short"`.

### `tests/test_palagent.py` (new or extended)

- `test_palagent_disables_search_web` -- `PALAgent.disabled_builtins` contains `"search_web"`.
- `test_palagent_does_not_register_search_web_tool` -- instantiate the ToolExecutor (or inspect agent state) and assert `"search_web"` not in the active tool registry.

### `tests/test_prompt_builder.py`

- `test_prompt_describes_search_vault_json_shape` -- `PAL_BASE_PROMPT` contains `"results: [{path, name, summary, score}]"` (or a tolerant substring match).
- `test_prompt_does_not_mention_search_web_for_llm` -- `"search_web"` does not appear in `PAL_BASE_PROMPT` (note: `/search-web` slash command may still appear via slash-command help registration, that's separate).

## Migration / back-compat

- **Existing test fixtures**: any test that asserts `search_vault` returns a specific prose string needs updating. Search the test suite for "Found N match(es)" or similar prose markers and update assertions to parse JSON.
- **Existing PAL conversation logs**: prior LLM interactions captured prose-shaped output; they are historical artifacts and need no migration.
- **Existing docs that reference search_vault output**: any user-facing documentation (README, plans, runbooks) needs a sweep. Plan-level docs that quote old output can stay (historical record); user-facing docs that describe current behavior get updated.
- **agent_core version bump**: per the memory `feedback_agent_core_version_bump`, any change to agent_core requires a version bump so the server's pip wheel picks up the new code. Bump patch version (1.1.0 → 1.1.1).

## Risks

1. **PAL's existing prompt-encoded mental model assumes prose output.** After this change, PAL must learn the JSON shape. Mitigation: the three prompt additions explicitly describe the new shape at the point of use.
2. **JSON adds token cost vs prose.** For 5 results × 4 keys = ~20 quoted strings + braces. Measured against the prose version's `"Found N match(es) for 'query':"` header + per-result labels, it's a wash to slight overhead. Acceptable cost for deterministic field access.
3. **Removing search_web may surprise PAL mid-conversation.** If PAL has been trained (in-context) to reach for search_web during research flows, it will now fail. Mitigation: the prompt rewrites the research flow to point at `propose_research` directly so PAL's mental model is updated at startup.
4. **The `_truncate` helper currently lives in the eval script; moving it might break the script.** Mitigation: leave the eval script copy in place; the agent_core copy is the canonical one, the eval copy gets refactored later.
5. **score=0 from the retrieval client could be ambiguous** (no match? zero similarity?). Behavior preserved from current: zero passes through. Not a new concern introduced by this spec.

## Verification

- `agent_core` test suite passes (the framework changes).
- PAL test suite passes (the disable + prompt changes).
- Manual smoke: launch PAL, call search_vault from chat, confirm JSON output with paths, confirm PAL uses the paths in follow-up `cat`/`edit` calls without re-guessing.
- Verify search_web is no longer in PAL's tool list (`/status` or similar surface should reflect this if it shows tools; otherwise check the daemon log on startup).

## Out of scope

- Migrating other tools to JSON (separate specs as needed)
- search_web LLM tool migration / allowlist contract (deleted from PAL's surface; agent_core class untouched)
- `/search-web` slash command behavior
- `fetch_url` disabling (next audit batch)
- Tag-filtered search input parameter
- Tag-aware output fields
- Vault read 404 nearest-match (separate spec, same cluster)
- Vault-write success shape + reindex propagation (separate spec, same cluster)
- `scripts/retrieval_eval/run.py` refactor to import the shared `_truncate` helper (cosmetic cleanup, separate)
