---
title: Vault File-Ops Tools (delete, replace, append, edit_frontmatter)
date: 2026-05-09
type: design
status: draft
---

# Vault File-Ops Tools

## Purpose

Add four new tools to PAL's vault-write surface so the agent has efficient, targeted file-operation primitives instead of the current full-body-rewrite-only `edit_file`. The user's "major sticking point" (stated 2026-05-09) is that PAL needs basic file-ops competence over the vault: read, search, targeted updates, deletes, moves. Reads and search are already covered by `agent_core` builtins (`cat`, `head`, `tail`, `read_lines`, `ls`, `find`, `grep`) and `search_vault`. Move is covered by `move_file`. The remaining gaps are: targeted updates, deletes, appends, and frontmatter-only edits.

This design closes those four gaps with one tool each.

## Background

PAL's existing vault-write tools (`pal/tools/vault.py`):

- `edit_file` rewrites the whole body of an existing file (frontmatter `title` and `tags` preserved). Wasteful for small changes (full body retransmitted to fix a typo); error-prone (LLM can drop content while reproducing a 58 KB body). Used for restructuring more than for targeted edits.
- `create_file` creates new files only under `raw/notes/`. Hard-coded refusal elsewhere to enforce the promotion discipline (wiki articles come from compile/consolidate, never from chat).
- `move_file` moves any vault file. Triggers reindex.

What's missing per the workflow review:

- **Delete:** no tool. Vault is git-backed, so reversibility is fine. The user said directly that removing files should be possible.
- **Targeted edit:** small changes (typo fix, link update) require the LLM to retransmit the entire body via `edit_file`.
- **Append:** adding a section, paragraph, or timeline entry forces a read-and-rewrite of the whole body.
- **Frontmatter-only edit:** updating a tag, status, or source URL forces a full-body rewrite via `edit_file` even though the body did not change.

These gaps make active-curator and active-pruner workflows (per the 2026-05-09 holistic assessment, section 3) more expensive than they need to be. The fix is four single-purpose tools.

## Design overview

Four tools added to `pal/tools/vault.py`. Each follows the existing module's patterns:

- Inherits the `Tool` base class.
- Has a class-variable `parameters` dict for the OpenAI-style schema.
- Uses `_is_system_path()` and `_resolve_safe()` (already in the module) for path safety.
- Refuses writes to underscore-prefixed system directories (`_wisdom`, `_learning`, `_config`, `_channels`, `_profile`).
- Refuses paths that resolve outside the vault root.
- Triggers `retrieval.trigger_reindex()` on successful changes that affect file content (delete, replace, append, frontmatter changes that affect the indexed text).
- Commits to the vault git repo on every successful operation.
- Returns a JSON-string result (matching the rest of the tool surface).

| Tool | Purpose | Required params | Optional params |
|---|---|---|---|
| `delete_file` | Remove a vault file | `path` | none |
| `replace_in_file` | Replace exact string match | `path`, `old_string`, `new_string` | `replace_all` (default False) |
| `append_to_file` | Append to body | `path`, `content` | none |
| `edit_frontmatter` | Update frontmatter fields | `path`, `updates` (object) | none |

None require approval gates. Git is the safety net (any operation can be reverted via `git revert`).

## Tool specifications

### `delete_file`

**Description (for the LLM):**
> Delete a vault file. Permanent within the working tree but recoverable from git history with `git revert`. Refuses underscore-prefixed system directories. Triggers reindex to remove the file from the embedding store.

**Parameters:**

```json
{
  "type": "object",
  "properties": {
    "path": {
      "type": "string",
      "description": "Path relative to vault root (e.g. 'Hardware/old-article.md'). Must already exist. Must not be in a system directory (_wisdom, _learning, _config, _channels, _profile)."
    }
  },
  "required": ["path"]
}
```

**Behavior:**

1. Resolve path. Refuse if outside vault or in a system directory.
2. Refuse if file does not exist.
3. Delete the file via `Path.unlink()`.
4. Call `wiki.git_commit(f"Delete {path} via chat")`.
5. Trigger reindex with the deleted path.
6. Return `{"status": "deleted", "path": "<path>"}` as JSON string.

**Errors:**

- `Error: 'path' parameter is required.`
- `Error: writing to system directories is not allowed: <path>`
- `Error: path escapes outside vault: <path>`
- `Error: file does not exist: <path>`

### `replace_in_file`

**Description (for the LLM):**
> Replace an exact string match in an existing vault file. Whitespace-sensitive. Requires `old_string` to be unique in the file unless `replace_all` is true. Useful for targeted edits without rewriting the whole body. Same shape as the Edit tool used by Claude Code.

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
      "description": "Exact string to find. Must appear in the file. Must be unique unless replace_all is true. Whitespace-sensitive (preserve indentation and newlines exactly)."
    },
    "new_string": {
      "type": "string",
      "description": "Replacement string. Empty string deletes the matched content."
    },
    "replace_all": {
      "type": "boolean",
      "description": "If true, replace every occurrence of old_string. If false (default), require old_string to be unique and replace one occurrence.",
      "default": false
    }
  },
  "required": ["path", "old_string", "new_string"]
}
```

**Behavior:**

1. Resolve path. Refuse if outside vault, in a system directory, or nonexistent.
2. Read the entire file content (frontmatter + body, no parsing).
3. Count occurrences of `old_string` in content.
4. If zero: error `old_string not found in <path>`.
5. If more than one and `replace_all` is False: error `old_string appears N times in <path>; pass replace_all=true or provide more context`.
6. Perform the replacement (`content.replace(old_string, new_string)` if `replace_all`, else single-replace by string-index).
7. Write back via `Path.write_text()` (NOT through `wiki.write_article` because that would re-serialize and could change frontmatter formatting).
8. Call `wiki.git_commit(f"Edit {path} via chat (replace_in_file)")`.
9. Trigger reindex.
10. Return `{"status": "replaced", "path": "<path>", "occurrences": N}` as JSON string.

**Errors:**

- `Error: 'path' parameter is required.`
- `Error: 'old_string' parameter is required.`
- `Error: 'new_string' parameter is required.` (Empty string is allowed.)
- `Error: writing to system directories is not allowed: <path>`
- `Error: path escapes outside vault: <path>`
- `Error: file does not exist: <path>`
- `Error: old_string not found in <path>`
- `Error: old_string appears N times in <path>; pass replace_all=true or provide more context`

**Edge cases:**

- old_string equal to new_string: detected up front, return early with a no-op success.
- new_string is empty (deletion via match): supported.
- old_string spans multiple lines: supported (string match is not line-aware).

### `append_to_file`

**Description (for the LLM):**
> Append content to the body of an existing vault file. Frontmatter is unchanged. A blank line separator is inserted between existing body and new content. Useful for adding sections, paragraphs, or timeline entries without rewriting the whole body.

**Parameters:**

```json
{
  "type": "object",
  "properties": {
    "path": {
      "type": "string",
      "description": "Path relative to vault root. Must already exist. Must not be in a system directory."
    },
    "content": {
      "type": "string",
      "description": "Content to append to the body (markdown, no frontmatter). A blank line separator is added between the existing body and this content."
    }
  },
  "required": ["path", "content"]
}
```

**Behavior:**

1. Resolve path. Refuse if outside vault, in a system directory, or nonexistent.
2. Use `wiki.read_article(path)` to get `(meta, body)`.
3. Compute new body: `body.rstrip() + "\n\n" + content`.
4. Use `wiki.write_article(path, title=meta["title"], body=new_body, tags=meta.get("tags"))`.
5. Call `wiki.git_commit(f"Append to {path} via chat")`.
6. Trigger reindex.
7. Return `{"status": "appended", "path": "<path>", "appended_chars": len(content)}` as JSON string.

**Errors:**

- `Error: 'path' parameter is required.`
- `Error: 'content' parameter is required.`
- `Error: writing to system directories is not allowed: <path>`
- `Error: path escapes outside vault: <path>`
- `Error: file does not exist: <path>`

### `edit_frontmatter`

**Description (for the LLM):**
> Update frontmatter fields on an existing vault article without touching the body. Pass an `updates` object whose keys are frontmatter field names. Setting a key to null removes it. Setting `tags` to a list replaces the entire tag list (no append-merge). Use this for adding tags, updating status, fixing source URLs, or other metadata-only edits.

**Parameters:**

```json
{
  "type": "object",
  "properties": {
    "path": {
      "type": "string",
      "description": "Path relative to vault root. Must already exist. Must not be in a system directory."
    },
    "updates": {
      "type": "object",
      "description": "Map of frontmatter keys to new values. null value removes the key. Lists replace existing list values entirely (no merge). Cannot remove the title field (PAL requires title)."
    }
  },
  "required": ["path", "updates"]
}
```

**Behavior:**

1. Resolve path. Refuse if outside vault, in a system directory, or nonexistent.
2. Use `parse_frontmatter()` from `agent_core.utils.frontmatter` to read `(meta, body)`.
3. For each key in `updates`:
   - If value is None: remove the key from `meta`.
   - Otherwise: set `meta[key] = value`.
4. Refuse if `title` would be removed (`updates["title"] is None`).
5. Use `serialize_frontmatter(meta, body)` and write back.
6. Call `wiki.git_commit(f"Edit frontmatter on {path} via chat")`.
7. Trigger reindex.
8. Return `{"status": "updated", "path": "<path>", "changed_keys": [...]}` as JSON string. `changed_keys` lists keys that were added, modified, or removed.

**Errors:**

- `Error: 'path' parameter is required.`
- `Error: 'updates' parameter is required and must be a non-empty object.`
- `Error: writing to system directories is not allowed: <path>`
- `Error: path escapes outside vault: <path>`
- `Error: file does not exist: <path>`
- `Error: cannot remove title field; PAL articles require a title.`

**Edge cases:**

- `updates` is empty: error (no-op edits should not commit).
- Non-string non-list non-null values (numbers, booleans): allowed; serialized as YAML.
- Key not currently present in frontmatter: added with the new value.

## Shared concerns

### Path safety

All four tools use the existing helpers in `pal/tools/vault.py`:

- `_resolve_safe(vault, path)` returns `None` if path escapes the vault root.
- `_is_system_path(path)` returns True for paths whose first relative component starts with `_`.

System directories (`_wisdom`, `_learning`, `_config`, `_channels`, `_profile`) are never writable by these tools. Wisdom rules are added via the existing `/wisdom` slash command path that calls `add_learning` + promote. Channel state is managed by the daemon. Direct LLM access to system dirs is intentionally absent.

If a workflow ever needs to bypass system-dir refusal, the answer is a dedicated tool with a focused purpose, not a flag on these.

### Git commit

Each tool commits on success. Commit messages follow the existing pattern (`Edit <path> via chat`, `Move <src> to <dst> via chat`):

- `delete_file`: `Delete <path> via chat`
- `replace_in_file`: `Edit <path> via chat (replace_in_file)`
- `append_to_file`: `Append to <path> via chat`
- `edit_frontmatter`: `Edit frontmatter on <path> via chat`

Git is the safety net. Any of these can be reverted with `git revert <commit-sha>` in the vault repo.

### Reindex

After every successful change, the tool calls `retrieval.trigger_reindex(paths=[absolute])` (matching the existing `edit_file` and `move_file` pattern). For deletes, the path is passed so the reindex can remove the file from the embedding store.

If the retrieval client is None (test environments, unconfigured agent), the trigger is skipped silently with a debug log entry, matching the existing pattern.

### No approval gates

None of these tools require a propose/execute pair. Direct ops, like `edit_file` today. Reversibility comes from git, not from approval. This matches the user's stated preference and the existing patterns in the module.

## Test approach

Add `tests/test_tools_vault_extended.py` (or extend `tests/test_tools_vault.py` if it exists). Test scaffolding follows the pattern in `tests/test_tools_consolidate.py:1-77` and `tests/test_tools_url_fix.py` (post-Task 7 fix from the empty-URL backfill plan).

Per tool, three to four tests:

**`delete_file`:**
- `test_delete_file_removes_file_and_commits`: happy path with reindex assertion.
- `test_delete_file_refuses_system_dirs`: refuses `_wisdom/...`.
- `test_delete_file_refuses_path_escape`: refuses `../../etc/passwd`.
- `test_delete_file_refuses_nonexistent`: error for missing path.

**`replace_in_file`:**
- `test_replace_in_file_single_occurrence`: happy path.
- `test_replace_in_file_refuses_non_unique_without_replace_all`: error.
- `test_replace_in_file_replace_all`: replaces all when flag is true.
- `test_replace_in_file_refuses_missing_old_string`: error.
- `test_replace_in_file_empty_new_string_deletes_match`: supports deletion via empty new_string.
- `test_replace_in_file_refuses_system_dirs`: path safety.

**`append_to_file`:**
- `test_append_to_file_appends_with_separator`: happy path; verifies blank line between existing and new content.
- `test_append_to_file_preserves_frontmatter`: happy path; verifies meta unchanged.
- `test_append_to_file_refuses_nonexistent`: error.
- `test_append_to_file_refuses_system_dirs`: path safety.

**`edit_frontmatter`:**
- `test_edit_frontmatter_adds_field`: adds a new key.
- `test_edit_frontmatter_modifies_field`: changes existing value.
- `test_edit_frontmatter_removes_field_with_null`: null deletes.
- `test_edit_frontmatter_replaces_tag_list`: list replacement, no merge.
- `test_edit_frontmatter_refuses_title_removal`: error.
- `test_edit_frontmatter_refuses_empty_updates`: error.
- `test_edit_frontmatter_preserves_body`: body unchanged.

Total: ~17 tests. All use real `tmp_path` vaults, real `parse_frontmatter`/`serialize_frontmatter`, and `MagicMock`-stubbed `wiki.git_commit` and `retrieval.trigger_reindex` (matching how existing vault tests work).

## What this does not do

- **Does not deprecate `edit_file`.** Stays in the registry for full-body rewrites. If usage drops to zero after the new tools land, deprecation is a separate workstream.
- **Does not change `create_file`.** Stays scoped to `raw/notes/` per the promotion discipline.
- **Does not audit or rewrite descriptions of existing tools.** Description-clarity audit (motivated by the 18-minute-loop incident) is a separate workstream queued for after this lands.
- **Does not drop any existing tools.** `propose_promote`, `fetch_url`, etc. drop decisions are queued separately.
- **Does not add `git_log_file`, `git_recover`, or `diff_files`.** Borderline-useful tools deferred until a specific workflow demands them (YAGNI).
- **Does not provide directory operations** (`delete_directory`, `mkdir`). Directories materialize when files land in them; they vanish when empty after `move_file` or the new `delete_file` empties them. If empty-directory cleanup turns out to matter, a `cleanup_empty_dirs` helper can be added later.
- **Does not loosen system-directory write protection.** `_wisdom`, `_learning`, `_config`, `_channels`, `_profile` remain off-limits. The `/wisdom` slash command path is the supported way to add wisdom rules; the daemon manages channel and config state.

## Cross-references

- Holistic assessment: `docs/superpowers/specs/2026-05-09-pal-research-assistant-assessment.md` (sections 3 and 7)
- Audit: `docs/pal-vault-audit-2026-05-09.md` (active-curator and active-pruner workflow needs)
- Memory: `project_pal_overview.md`, `project_articles_are_substrate.md`
- Existing tools: `pal/tools/vault.py` (`EditFile`, `CreateFile`, `MoveFile`)
- Test patterns: `tests/test_tools_consolidate.py`, `tests/test_tools_url_fix.py` (post-Task 7 fix shape)
- Related incident: `docs/bsides_18_minute_tool_loop.md` (description-ambiguity lesson; informs why we keep these tools single-purpose)
