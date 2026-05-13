# Vault-write canonical success shape + reindex propagation

**Date:** 2026-05-13
**Status:** Design
**Author:** Brainstormed with Claude
**Audit:** `docs/superpowers/audits/2026-05-11-tool-audit-report.md` (path-determinism cluster, item 3 of 3 -- the bundled "vault-write success shape and reindex propagation" cross-cutting must-fix)
**Cluster predecessors:** `2026-05-12-search-vault-json-result-format-design.md` (merged), `2026-05-13-vault-read-404-nearest-match-design.md` (merged)

## Problem

PAL's five vault write tools (`edit_file`, `create_file`, `delete_file`, `move_file`, `replace_in_file`) return five different output shapes today:

| Tool | Today's shape |
|------|---------------|
| edit_file | `f"Updated: {path}"` (bare prose string) |
| create_file | `f"Created: {path}"` (bare prose string) |
| move_file | JSON `{"moved": "src -> dst", "reindex_queued": True}` |
| delete_file | JSON `{"status": "deleted", "path", "reindex": "ok"\|"failed"}` |
| replace_in_file | JSON `{"status": "replaced", "path", "occurrences", "reindex": "ok"\|"failed"}` |

Net surface: 5 tools, 4 distinct shapes (two prose, three JSON with three different key sets). The system prompt at `pal/prompts/system.py:100` promises every write tool returns a `reindex` field with `job_id` and `status` for `wait_for_reindex` to consume. That promise is broken across all five tools today: two return no JSON at all, and the three that do collapse the inference server's `{job_id, status, paths}` response into a string flag (`"ok"` or `"failed"`).

Each tool calls `RetrievalClient.trigger_reindex(...)` but discards the return value, using only exception semantics. The server's response dict (verified during the search_vault spec brainstorming: `{job_id, status, paths}` on success, `None` on connection error / non-202) carries the `job_id` PAL needs to call `wait_for_reindex`. Today PAL cannot get that `job_id` from any vault write because no tool surfaces it.

This spec is the third and final item in the path-determinism cluster from the 2026-05-11 tool audit. It pins one canonical envelope across all five tools and propagates the reindex dict end to end.

## Goals

1. One canonical JSON envelope across all five vault write tools so PAL learns one shape, not five.
2. Propagate `RetrievalClient.trigger_reindex(...)`'s return value (the inference server's `{job_id, status, paths}` dict) verbatim into a `reindex` field, so `wait_for_reindex` becomes usable after small writes (today only the major-pipeline tools like compile_one carry the dict through correctly).
3. Normalize error returns into the same envelope so PAL's mental model of "a vault write returns this shape" applies to both success and failure.
4. Update the system prompt to match the new contract.
5. No agent_core changes; the retrieval client already returns the right shape.
6. No new dependencies.

## Non-goals

1. **agent_core changes.** The retrieval client's return shape was verified during the 2026-05-12 search_vault brainstorming and matches what we need. No version bump.
2. **Migrating other tool families to the envelope** (compile/consolidate/research/etc.). The cluster-wide JSON convention adoption proceeds incrementally; this spec is vault-tools only.
3. **Tag-aware or content-aware reindex routing.** Reindex is a black box: PAL passes paths, gets back a job_id (or null), uses it via `wait_for_reindex`.
4. **Caching reindex job_ids across tool calls.** Each call's reindex is independent.
5. **Retry logic on `trigger_reindex` failure.** Failures are surfaced as `reindex: null`; PAL decides whether to retry, wait, or proceed without indexing.
6. **Discriminated-union envelope** (where tool-specific fields live under a `result` sub-object). Considered and rejected: extras at top level keep the envelope flat and predictable, and only two tools (`move_file`, `replace_in_file`) have extras today.
7. **Updating READMEs / runbooks / external docs that describe the old shapes.** Separate cleanup workstream.

## Canonical envelope

### Success

```json
{
  "status": "<verb>",
  "path": "<vault-relative path>",
  "reindex": <dict | null>
}
```

`<verb>` is one of: `"updated"`, `"created"`, `"deleted"`, `"moved"`, `"replaced"`.

`reindex`:
- **Success** (server returned 202 with `{job_id, status, paths}`): pass the dict through verbatim.
- **All failure cases** (no retrieval client configured / `trigger_reindex` returned `None` / `trigger_reindex` raised): `null`.

### Tool-specific extras at the top level

- `move_file` adds `"dst": "<vault-relative dest path>"`. The `path` field holds the source.
- `replace_in_file` adds `"occurrences": <int>` (the count of replacements made).

### Error

```json
{
  "status": "error",
  "path": "<path or empty string>",
  "reason": "<one-line message>"
}
```

When the user's call has no `path` arg (parameter validation error before we know the target file), `path: ""`. The field is always present so PAL never has to check for key absence.

For 404 errors, the `format_not_found_with_suggestions` helper (from the previous spec in this cluster) produces a multi-line string with a `\nDid you mean: ...` tail. The full helper output goes into `reason` verbatim. PAL parses the JSON and reads `reason` line-by-line if needed.

### Partial-failure cases

`delete_file` git-commit-failed branch (today returns `{status: "deleted_uncommitted", path, warning}`):

```json
{
  "status": "deleted_uncommitted",
  "path": "<path>",
  "warning": "git commit failed; staged removal in index, manual recovery required: <exc>"
}
```

`reindex` is omitted in this branch (today's behavior preserved). The `warning` field is the disambiguator. PAL sees status="deleted_uncommitted" and knows to surface a recovery hint to the user.

`replace_in_file` git-commit-failed branch (today returns a bare error string after restoring original content):

```json
{
  "status": "error",
  "path": "<path>",
  "reason": "git commit failed; original content restored: <exc>"
}
```

The rollback behavior (restore original content on disk before returning) is preserved.

## Per-tool delta

| Tool | Today | After | Breaking? |
|------|-------|-------|-----------|
| edit_file | `"Updated: foo.md"` (string) | `{status, path, reindex}` (JSON) | yes -- string to JSON |
| create_file | `"Created: foo.md"` (string) | `{status, path, reindex}` (JSON) | yes -- string to JSON |
| delete_file | `{status, path, reindex: str}` | `{status, path, reindex: dict|null}` | partial -- reindex shape |
| move_file | `{moved: "src -> dst", reindex_queued: bool}` | `{status: "moved", path, dst, reindex}` | yes -- key set changes |
| replace_in_file | `{status, path, occurrences, reindex: str}` | `{status, path, occurrences, reindex: dict|null}` | partial -- reindex shape |

## Implementation strategy

### Shared helper

Add to `pal/tools/vault.py`:

```python
async def _maybe_reindex(retrieval, paths: list[str]) -> dict | None:
    """Trigger reindex for the given absolute paths, returning the server's
    response dict on success or None on any failure (no retrieval client,
    server unreachable, exception). Logs failures at WARN.
    """
    if retrieval is None:
        return None
    try:
        return await retrieval.trigger_reindex(paths=paths)
    except Exception as exc:
        logger.warning("reindex trigger failed: %s", exc)
        return None
```

This collapses the 5 copies of the `getattr(ctx.agent, "retrieval", None) + try/except` pattern scattered across the file. Each tool's reindex call site becomes:

```python
reindex = await _maybe_reindex(getattr(ctx.agent, "retrieval", None), [str(resolved)])
```

### Per-tool changes

1. **edit_file** (`pal/tools/vault.py:113`): replace the bare `f"Updated: {path}"` return with `json.dumps({"status": "updated", "path": path, "reindex": reindex})` after capturing `reindex` via the helper. Existing reindex try/except (lines 105-111) collapses into the helper call.
2. **create_file** (line 208): same shape change. Existing reindex block (lines 200-206) collapses into helper.
3. **delete_file** (line 359): minimal change. Already returns the envelope; swap `reindex_status` (string) for `reindex` (dict from helper). Preserve `"deleted_uncommitted"` partial-failure path verbatim. Lines 350-357 collapse.
4. **move_file** (line 280): bigger change. Replace `{moved, reindex_queued}` with `{status: "moved", path: src, dst, reindex}`. Lines 268-278 collapse. Note: `path` holds the source path; `dst` holds the destination. This convention matches "the operation subject is `path`."
5. **replace_in_file** (line 502): minimal change like delete_file. Swap `reindex_status` (string) for `reindex` (dict). Lines 493-500 collapse. The git-commit-failed branch (line 491) becomes a JSON error envelope (currently a bare string).

### Error envelope normalization

All currently-bare error returns (`return f"Error: ..."`) get rewrapped:

```python
return json.dumps({"status": "error", "path": path, "reason": "<message-without-Error:-prefix>"})
```

The `Error: ` prefix is dropped from `reason` since `status: "error"` already conveys the error class. Examples:
- `"Error: 'path' parameter is required."` becomes `{"status": "error", "path": "", "reason": "'path' parameter is required."}`
- `"Error: writing to system directories is not allowed: foo.md"` becomes `{"status": "error", "path": "foo.md", "reason": "writing to system directories is not allowed"}`
- The `format_not_found_with_suggestions(...)` output (multi-line with `\nDid you mean: ...` tail) goes into `reason` verbatim. JSON serialization handles the embedded newline.

### Boundary cases

- **No `path` arg supplied:** envelope has `path: ""` (empty string, not null). PAL learns: `if path is empty, the error is at parameter validation, not the file level.`
- **`format_not_found_with_suggestions` output is multi-line:** `json.dumps` handles the newline correctly; PAL parses the JSON and reads `reason` as one string with embedded `\n`.
- **`reindex` is `null` even on success:** happens when the agent has no retrieval client (test fixtures, smoke harnesses). Behavior matches today; PAL learns to handle it.

## Prompt updates

In `pal/prompts/system.py`, two changes:

### 1. Rewrite line 100

Today:
> After a write tool succeeds, its result includes a `reindex` field with a `job_id` and current `status`. The inference server reindexes the new content automatically; the `status` field tells you whether it has finished. You normally do not need to wait -- by the time the next user message arrives, the reindex will be done. Call `wait_for_reindex` only when you need to `search_vault` for the just-written content within the SAME response.

Replace with:

> After a write tool returns, its result is JSON `{status, path, reindex}`. The `reindex` field is either `null` (content was not indexed, e.g. retrieval server unreachable) or a dict like `{job_id, status, paths}`. Use `wait_for_reindex` with the `job_id` only when you need to `search_vault` for the just-written content within the SAME response. The inference server reindexes automatically; by the time the next user message arrives, the reindex will be done.

### 2. Add a one-line shape callout earlier in the tool catalog

Near the existing edit_file/create_file mentions (around `pal/prompts/system.py:18`), add:

> All vault writes (edit_file, create_file, delete_file, move_file, replace_in_file) return JSON with the same envelope: `{status: "<verb>", path: "<vault-rel>", reindex: <dict|null>}`. Errors use the same envelope with `status: "error"` and a `reason` field. move_file adds `dst`; replace_in_file adds `occurrences`.

## Tests

### Per-tool success envelope tests (5)

For each of `edit_file`, `create_file`, `delete_file`, `move_file`, `replace_in_file`:
- `test_<tool>_success_returns_canonical_envelope`: happy path with retrieval mocked to return `{job_id: "x", status: "queued", paths: [...]}`. Assert JSON parses; `status` matches the verb; `path` matches input; `reindex` is the mocked dict verbatim.

### Per-tool error envelope tests (5)

For each tool:
- `test_<tool>_error_returns_canonical_envelope`: trigger a parameter-validation error (missing `path`, system path attempt, etc.). Assert `status: "error"`, `path` populated (or empty string for missing-path errors), `reason` contains the human-readable message.

### Tool-specific extras (3)

- `test_move_file_envelope_includes_dst`: success result has `dst` populated with the destination path.
- `test_replace_in_file_envelope_includes_occurrences`: success result has `occurrences` matching the count.
- `test_delete_file_uncommitted_status`: git-commit-failure path returns `status: "deleted_uncommitted"` with `warning` present and no `reindex` key.

### Reindex behavior (4)

- `test_<tool>_reindex_null_when_no_client`: agent has no retrieval attribute; envelope's `reindex` is `null`, `status` is the success verb.
- `test_<tool>_reindex_null_when_trigger_raises`: `trigger_reindex` raises; envelope's `reindex` is `null`, success verb still set.
- `test_<tool>_reindex_passes_through_server_dict`: `trigger_reindex` returns `{job_id, status, paths}`; envelope's `reindex` is that dict verbatim.
- `test_replace_in_file_commit_failure_returns_error_envelope`: git commit raises; envelope is `{status: "error", path, reason}`; original content restored on disk (assert via file content check).

These four tests can be parametrized across the 5 tools where applicable; only one or two need to be exhaustive per tool.

### Helper unit tests (3)

- `test_maybe_reindex_returns_none_when_no_client`
- `test_maybe_reindex_returns_none_on_exception`
- `test_maybe_reindex_passes_through_dict_on_success`

### Prompt tests (2)

In `tests/test_prompt_builder.py`:
- `test_base_prompt_describes_vault_write_envelope`: `PAL_BASE_PROMPT` contains `'{status, path, reindex}'` (or substring of the new callout).
- `test_base_prompt_reindex_field_documents_dict_or_null`: `PAL_BASE_PROMPT` contains both `"null"` and `"job_id"` near the reindex description (verifying line 100 rewrite).

### Existing tests that need updates

Some existing tests assert on the old return shapes:
- Tests asserting `result == "Updated: foo.md"` or `result == "Created: foo.md"`: update to parse JSON and assert on `status`.
- Tests asserting `result.startswith("Updated:")`: rewrite to JSON-parse.
- Tests asserting `parsed["moved"]` for move_file: rewrite to assert `parsed["status"] == "moved"` and `parsed["dst"] == ...`.
- Tests asserting `parsed["reindex"] == "ok"` (string): rewrite to assert `parsed["reindex"] is None` or `isinstance(parsed["reindex"], dict)`.

The implementation plan will enumerate these. Estimated ~10 existing tests across `test_tools_vault.py` and `test_tools_move_file.py`.

## Migration / back-compat

- No agent_core changes.
- All five PAL vault tools change shape. No internal PAL caller depends on the old shapes (compile/consolidate/etc. use the underlying engines, not the LLM-facing tool outputs).
- Existing tests get updated as part of the implementation plan.
- Server deploy: PAL `git pull` + daemon restart. **No** agent_core wheel reinstall. **No** Discord adapter restart (no protocol message changes; this is purely tool output).

## Risks

1. **PAL's in-context mental model is stale on first interaction post-deploy.** It expects `"Updated: foo.md"` from edit_file. Mitigation: the prompt callout near the tool catalog teaches the new shape immediately at startup. PAL adapted within one or two turns to the search_vault JSON migration; same dynamic expected here.
2. **Tool-specific extras at top level (`dst`, `occurrences`) limit future extension.** A future tool with conflicting extras would need a different key. Acceptable for v1; if it becomes an issue, those extras move under a `result` sub-object later.
3. **`reindex` dict shape comes from the inference server.** If the server's response shape changes, the `reindex` field shape changes implicitly. This dependency exists today (the tools rely on the same server semantics for exception handling); making it explicit just exposes what was already implicit.
4. **`format_not_found_with_suggestions` newline-in-`reason`** is correctly JSON-serialized but the multi-line `reason` string is unusual for a JSON value. PAL has shown it can read multi-line JSON values fine. Worth a smoke check on first deploy.

## Verification

- `pal/tools/vault.py` test suite passes (helper + 5 success tests + 5 error tests + tool-specific + reindex behavior).
- `pal/tools/test_tools_move_file.py` updated tests pass.
- `pal/prompts/test_prompt_builder.py` new prompt-shape tests pass.
- Full PAL test suite passes (excluding documented pre-existing failures).
- Manual smoke after deploy: ask PAL to edit a file, observe the JSON return; ask PAL to follow up by reading the file via cat (it should reuse the path from the JSON envelope without guessing).

## Out of scope

- agent_core changes (verified upfront with the user; not needed).
- Migrating other tool families to the envelope (compile, consolidate, research, etc.).
- Tag-aware or content-aware reindex routing.
- Caching reindex job_ids across tool calls.
- Retry logic on `trigger_reindex` failure.
- Updating downstream documentation (READMEs, runbooks) that describe the old shapes.
- Discriminated-union envelope (rejected during brainstorming as overkill for 5 tools).
