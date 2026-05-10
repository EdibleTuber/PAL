---
title: Vault File-Ops Tools (delete_file + replace_in_file + edit_file description tightening)
date: 2026-05-09
type: design
status: draft
revision: 2 post panel-review trim
---

# Vault File-Ops Tools

## Purpose

Add two new vault-write tools to PAL and tighten the existing `edit_file` description, so the agent has efficient targeted-edit and delete primitives. The user's stated "major sticking point" (2026-05-09) is that PAL needs basic file-ops competence over the vault. Reads and search are already covered by `agent_core` builtins (`cat`, `head`, `tail`, `read_lines`, `ls`, `find`, `grep`) and `search_vault`. Move is covered by `move_file`. The two largest remaining gaps are: (1) targeted updates without full-body rewrite, and (2) deletes.

This revised scope (post panel review on 2026-05-09) ships only those two and tightens `edit_file`'s description to redirect targeted edits explicitly to the new tool. Two additional tools considered in the original design (`append_to_file`, `edit_frontmatter`) have been deferred. Reasons documented below.

## Background

PAL's existing vault-write tools (`pal/tools/vault.py`):

- `edit_file` rewrites the whole body of an existing file (frontmatter `title` and `tags` preserved). Wasteful for small changes; the description does not say "use only for restructuring," so the agent has no routing signal between this and the new `replace_in_file`.
- `create_file` creates new files only under `raw/notes/`. Hard-coded refusal elsewhere.
- `move_file` moves any vault file. Triggers reindex.

Missing primitives the user named:

- **Delete:** no tool. Vault is git-backed; reversibility is fine.
- **Targeted edit:** small changes (typo fix, link update) require the LLM to retransmit the entire body via `edit_file`.

These gaps make active-curator and active-pruner workflows (per the 2026-05-09 holistic assessment) more expensive than they need to be.

## Panel review summary

The original design proposed four tools: `delete_file`, `replace_in_file`, `append_to_file`, `edit_frontmatter`. A four-expert panel review (Python engineer, LLM tool-design specialist, security reviewer, YAGNI skeptic) ran on 2026-05-09 against this design. The panel found:

**Strong consensus to drop two tools:**

- **`append_to_file` is a degenerate case of `replace_in_file`.** Two reviewers independently flagged it as redundant. Append is expressible via `replace_in_file` with the trailing body content as `old_string`. Keeping both creates the same kind of overlapping-affordance ambiguity that drove the bsides 18-minute-loop incident. Drop; prompt-guide the append pattern in `replace_in_file`'s description. The Python engineer separately found a latent data-loss bug in the proposed `AppendToFile` (it threaded only `title` and `tags` through `wiki.write_article`, silently dropping any other frontmatter fields), which is a third independent reason to cut it.
- **`edit_frontmatter` has narrow real demand and unresolved design issues.** Three of four reviewers raised concerns: protected-keys logic missing (only `title` was guarded; `compiled_at`, `sources`, `created` could be silently removed), list-replace-vs-merge semantics under-described, no-op commits possible. The YAGNI reviewer argued for shipping after observed demand. Defer until evidence shows recurring frontmatter-edit requests; ship then with hardening already identified.

**Strong consensus to add a description tightening:**

- **`edit_file` description redirects to `replace_in_file`.** Both descriptions today read like they apply to targeted edits. The model has no routing signal. Two reviewers (LLM tool-design and YAGNI) flagged this as the most likely source of a future 18-minute-loop pattern. The fix lands in this same workstream so the routing is explicit at the moment `replace_in_file` is introduced.

**Hardening recommendations (applied below):**

- Use `git rm` for `delete_file` (atomic stage + remove) instead of `Path.unlink()` followed by stage-on-commit. Mitigates the failure mode where `unlink` succeeds but `git_commit` fails, leaving the file gone with no commit to revert.
- Surface reindex failure in the JSON response. Don't silently swallow.
- `replace_in_file` operates on the body only, with frontmatter parsed out and reattached. Prevents `replace_all=True` with a short `old_string` from corrupting frontmatter (e.g., replacing "AI" globally on an article tagged `[AI, hardware]`).
- UTF-8 encoding explicit on every read/write call.
- Tighten `replace_in_file`'s uniqueness error to suggest widening `old_string` (actionable for the LLM).
- Drop the prose phrase "Same shape as the Edit tool used by Claude Code" from `replace_in_file`'s description (irrelevant in PAL's context, invites cross-context confusion).
- Tests assert file state, not mock call arguments.

## Design overview

Two new `Tool` subclasses in `pal/tools/vault.py`. One existing tool gets a description rewrite. All follow the same pattern as existing `EditFile`/`CreateFile`/`MoveFile`:

- Inherit `Tool` base class.
- Class-variable `parameters` dict for OpenAI-style schema (NOT a `@property`; this is the parameters-class-var-vs-schema-property pitfall caught during the empty-URL backfill execution).
- Use `_resolve_safe()` and `_is_system_path()` for path safety.
- Refuse writes to underscore-prefixed system directories.
- Refuse paths that resolve outside the vault root.
- Trigger `retrieval.trigger_reindex()` after content changes. Surface reindex failure in the response.
- UTF-8 encoding explicit on every `read_text` / `write_text` call.
- Return JSON-string results matching the broader propose/execute tool pattern.

| Tool | Action | Required params | Optional params |
|---|---|---|---|
| `delete_file` | Remove a vault file via atomic `git rm` | `path` | none |
| `replace_in_file` | Replace exact string match in body (frontmatter excluded) | `path`, `old_string`, `new_string` | `replace_all` (default False) |
| `edit_file` (existing, description rewrite only) | Full-body rewrite (kept for structural overhauls) | unchanged | unchanged |

None require approval gates. Git is the safety net for delete; for replace, the original content is restored on commit failure (per the panel's safety reviewer).

## Tool specifications

### `delete_file`

**Description (for the LLM):**

> Delete a vault file. Stages the removal atomically via `git rm` and commits. Recoverable from git history with `git revert`. Refuses underscore-prefixed system directories (`_wisdom`, `_learning`, `_config`, `_channels`, `_profile`). Triggers reindex to remove the file from the embedding store. Reports if reindex fails so the caller knows the embedding store is temporarily stale.

**Parameters:**

```json
{
  "type": "object",
  "properties": {
    "path": {
      "type": "string",
      "description": "Path relative to vault root (e.g. 'Hardware/old-article.md'). Must already exist. Must not be in a system directory."
    }
  },
  "required": ["path"]
}
```

**Behavior:**

1. Resolve path. Refuse if outside vault, in system dir, or nonexistent.
2. Verify file exists.
3. Run `wiki.git_rm(path)` (atomic stage + remove). If `git_rm` raises, return error without further action; the file is untouched.
4. `wiki.git_commit(f"Delete {path} via chat")`. On commit failure, the file is gone from disk and the removal is staged in git's index but not committed; surface via the response status.
5. Trigger `retrieval.trigger_reindex(paths=[<deleted-path>])`. On failure, log a warning AND set `reindex: "failed"` in the response.
6. Return JSON: `{"status": "deleted", "path": "<path>", "reindex": "ok" | "failed"}`. If commit failed: `{"status": "deleted_uncommitted", "path": "<path>", "warning": "git commit failed; staged removal in index, manual recovery required"}`.

**Errors:**

- `Error: 'path' parameter is required.`
- `Error: writing to system directories is not allowed: <path>`
- `Error: path escapes outside vault: <path>`
- `Error: file does not exist: <path>`
- `Error: git rm failed: <reason>` (e.g., file not tracked)

**If `wiki.git_rm()` does not exist** as a helper today, the implementation either adds it (one-line wrapper around `subprocess.run(["git", "rm", "--", path], cwd=vault)`) or invokes `git rm` directly inside the tool. Don't substitute `Path.unlink()` and rely on commit-time staging; the panel specifically flagged that as the failure mode.

### `replace_in_file`

**Description (for the LLM):**

> Replace an exact string match in the body of an existing vault file. Frontmatter is parsed and reattached unchanged; this tool does not modify YAML metadata (use the existing `edit_file` if a frontmatter rewrite is genuinely needed, or wait for the planned `edit_frontmatter` tool). Whitespace-sensitive. Requires `old_string` to be unique in the body unless `replace_all` is true. Useful for targeted edits without rewriting the whole body, including appending content (use the trailing portion of the body as `old_string` and the same trailing portion plus your new content as `new_string`).

**Parameters:**

```json
{
  "type": "object",
  "properties": {
    "path": {
      "type": "string",
      "description": "Path relative to vault root. Must already exist. Must not be in a system directory."
    },
    "old_string": {
      "type": "string",
      "description": "Exact string to find in the body. Must appear in the body. Must be unique unless replace_all is true. Whitespace-sensitive (preserve indentation and newlines exactly). To make a non-unique match unique, widen old_string to include surrounding lines."
    },
    "new_string": {
      "type": "string",
      "description": "Replacement string. Empty string deletes the matched content."
    },
    "replace_all": {
      "type": "boolean",
      "description": "If true, replace every occurrence of old_string in the body. If false (default), require old_string to be unique and replace one occurrence.",
      "default": false
    }
  },
  "required": ["path", "old_string", "new_string"]
}
```

**Behavior:**

1. Resolve path. Refuse if outside vault, in system dir, or nonexistent.
2. Read file with `parse_frontmatter()` from `agent_core.utils.frontmatter` to get `(meta, body)`.
3. Capture `original_body` for restore-on-commit-failure.
4. Count occurrences of `old_string` in `body` (NOT in raw file content; frontmatter excluded).
5. If zero: error `old_string not found in body of <path>`.
6. If more than one and `replace_all` is False: error `old_string appears N times in body of <path>; pass replace_all=true, or widen old_string to include surrounding lines until it is unique in the body.`
7. Perform replacement: single replace if `replace_all` is False, all-replace if True.
8. Reserialize via `serialize_frontmatter(meta, new_body)` and write back via `Path.write_text(..., encoding="utf-8")`.
9. `wiki.git_commit(f"Edit {path} via chat (replace_in_file)")`. On commit failure: restore `original_body` (write back via `serialize_frontmatter(meta, original_body)`), then return error.
10. Trigger reindex; surface `"reindex": "ok" | "failed"` in response.
11. Return JSON: `{"status": "replaced", "path": "<path>", "occurrences": N, "reindex": "ok" | "failed"}`.

**Errors:**

- `Error: 'path' parameter is required.`
- `Error: 'old_string' parameter is required.`
- `Error: 'new_string' parameter is required.` (Empty string is allowed.)
- `Error: writing to system directories is not allowed: <path>`
- `Error: path escapes outside vault: <path>`
- `Error: file does not exist: <path>`
- `Error: old_string not found in body of <path>`
- `Error: old_string appears N times in body of <path>; pass replace_all=true, or widen old_string to include surrounding lines until it is unique in the body.`
- `Error: git commit failed; original content restored: <reason>` (after restore)

**Edge cases:**

- old_string equal to new_string: detected up front, return early as a no-op success.
- new_string is empty (deletion via match): supported.
- old_string spans multiple lines: supported (string match is not line-aware).
- old_string appears in frontmatter only: returns "not found in body" (intentional; this tool is body-only).

### `edit_file` (existing tool, description rewrite only)

**New description:**

> Rewrite the entire body of an existing vault file. Preserves frontmatter (title, tags). Use ONLY for structural overhauls where most of the body is being replaced (e.g., reorganizing sections, swapping a draft for a final version). For targeted changes (typo fix, link update, single-line edit, adding a paragraph), use `replace_in_file` instead. The cost difference is significant: `edit_file` requires retransmitting the entire body; `replace_in_file` only the changed strings.

The behavior of `edit_file` does not change. Only the description is rewritten to add the explicit routing signal to `replace_in_file`.

## Shared concerns

### Path safety

Both new tools use the existing helpers `_resolve_safe(vault, path)` and `_is_system_path(path)` from `pal/tools/vault.py:26-36`.

System directories (`_wisdom`, `_learning`, `_config`, `_channels`, `_profile`) are refused. The `raw/` directory is NOT refused; the user explicitly wants to be able to delete contaminated raw content (e.g., the audit's contaminated templates) and edit raw notes. This is asymmetric with `create_file` (which is scoped to `raw/notes/`-only); the asymmetry is intentional. Curator/pruner work needs to operate on raw content; creation is gated to enforce promotion discipline.

Symlink handling in `_resolve_safe` is unchanged. The current relative-path check is sufficient for the personal-vault threat model. If the threat model later includes prompt-injection with crafted symlinks, that is a separate hardening workstream.

### Git commit and atomicity

`delete_file` uses `wiki.git_rm(path)` for atomic stage-and-remove. If `wiki.git_rm` does not exist, the implementer adds it as a thin wrapper around `subprocess.run(["git", "rm", "--", path], cwd=self.vault_path, check=True)`.

`replace_in_file` saves the original body before write. On commit failure, restores the original body. This is a bounded recovery path (one operation worth of state), distinct from git-based recovery (`git revert`).

### Reindex failure surfacing

After every content change, `retrieval.trigger_reindex(paths=[absolute])` is called. Failures are logged AND surfaced in the response as `"reindex": "failed"`. Callers (the LLM, downstream agents) thereby know the embedding store may be stale until the next reindex. This addresses the panel's concern about silent inconsistency.

### Encoding

All `read_text` and `write_text` calls explicitly pass `encoding="utf-8"`. Vault content is markdown with potential non-ASCII content (per the audit, includes Chinese-language titles, em dashes, encoded HTML entities); locale-default encoding can mangle this on systems where the default isn't UTF-8.

### No approval gates

Neither new tool requires a propose/execute pair. Direct ops, like `edit_file` today.

## Test approach

Add ~10 tests to `tests/test_tools_vault.py` (extend, don't create a new file):

**`delete_file` (4 tests):**
- `test_delete_file_removes_file_via_git_rm_and_commits`: happy path; verifies file removed AND git_rm called AND commit called AND reindex triggered.
- `test_delete_file_refuses_system_dirs`: refuses `_wisdom/...`. Asserts file untouched.
- `test_delete_file_refuses_path_escape`: refuses `../escape.md`. Asserts no git_rm.
- `test_delete_file_surfaces_reindex_failure`: reindex raises; tool returns `reindex: "failed"` in JSON; file still deleted.

**`replace_in_file` (5 tests):**
- `test_replace_in_file_replaces_in_body_only`: file with frontmatter `tags: [AI, hardware]` and body containing "AI"; replace "AI" with "ML"; assert frontmatter unchanged AND body changed.
- `test_replace_in_file_refuses_non_unique_without_replace_all`: error mentions widening `old_string`.
- `test_replace_in_file_replace_all_only_in_body`: body has 3 occurrences, frontmatter has 1; `replace_all=True` replaces only the 3 in body; frontmatter unchanged.
- `test_replace_in_file_restores_on_commit_failure`: simulate `git_commit` raise; assert file content reverted to original AND error returned.
- `test_replace_in_file_empty_new_string_deletes_match`: supports deletion via empty new_string.

**`edit_file` description rewrite (1 test):**
- `test_edit_file_description_mentions_replace_in_file`: load `EditFile.description` and assert it contains `"replace_in_file"`. Cheap test that prevents accidental description regressions.

Total: ~10 tests. All assert file state primarily (per the Python engineer's feedback that mock-call assertions are weak tests). Mock assertions remain only as secondary checks where the wiki/retrieval interface needs verification.

Test scaffolding follows the existing `tests/test_tools_vault.py` pattern (`@dataclass _Config`, `_Agent` with `wiki` and `retrieval` defaults, `_ctx` helper, plain `async def test_*` with pytest-asyncio in auto mode).

## What this does not do

- **Does not add `append_to_file`.** Deferred per panel review. Append is expressible via `replace_in_file` with trailing-body anchor. If after a few weeks of use it becomes clear the prompt-guided append pattern is too unreliable, this can be revisited as its own design.
- **Does not add `edit_frontmatter`.** Deferred per panel review. Wait for observed recurring demand for tag/status/source edits. When it lands, it ships with: protected-keys set (`compiled_at`, `sources`, `created` not removable), no-op short-circuit (don't commit empty changes), explicit list-replace-vs-merge documentation in the schema, and worked examples in the parameter description.
- **Does not deprecate `edit_file`.** Stays in the registry with the new description redirecting targeted edits to `replace_in_file`. If usage of `edit_file` drops to zero after a few weeks, deprecation is a separate workstream.
- **Does not change `create_file`.** Stays scoped to `raw/notes/` per the promotion discipline.
- **Does not audit other tool descriptions.** A full description audit (motivated by the 18-min-loop incident) is queued as a separate workstream.
- **Does not drop existing tools.** `propose_promote`, `fetch_url`, etc. drop decisions are queued separately.
- **Does not add `git_log_file`, `git_recover`, or `diff_files`.**
- **Does not provide directory operations.**
- **Does not loosen system-directory write protection.**

## Future workstreams (queued)

- **`append_to_file`** if prompt-guided append in `replace_in_file` proves unreliable.
- **`edit_frontmatter`** if frontmatter-edit demand recurs in observed sessions; ship with protected-keys hardening per panel review.
- **`edit_file` deprecation** if usage drops after the new tools land.
- **Full tool-description audit** to prevent more 18-min-loop patterns across the surface.
- **Tool-surface drop decisions** (`propose_promote`, `fetch_url`, others) driven by usage telemetry.

## Cross-references

- Holistic assessment: `docs/superpowers/specs/2026-05-09-pal-research-assistant-assessment.md` (sections 3 and 7)
- Audit: `docs/pal-vault-audit-2026-05-09.md` (active-curator and active-pruner workflow needs)
- Memory: `project_pal_overview.md`, `project_articles_are_substrate.md`
- Existing tools: `pal/tools/vault.py` (`EditFile`, `CreateFile`, `MoveFile`)
- Test patterns: `tests/test_tools_vault.py` (existing scaffolding)
- Related incident: `docs/bsides_18_minute_tool_loop.md` (description-ambiguity lesson; informs the `edit_file` description rewrite)
