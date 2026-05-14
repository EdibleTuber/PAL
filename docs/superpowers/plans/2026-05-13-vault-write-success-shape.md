# Vault-write canonical success shape + reindex propagation -- Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pin one canonical JSON envelope (`{status, path, reindex}`) across all 5 PAL vault write tools and propagate `RetrievalClient.trigger_reindex(...)`'s server response dict into the `reindex` field, so PAL gets `job_id` for `wait_for_reindex` after every write.

**Architecture:** PAL-only. Add a small `_maybe_reindex` helper in `pal/tools/vault.py` that captures the retrieval client's return dict (or null on any failure). Migrate each of the 5 vault tools to the canonical envelope one task at a time, updating affected tests as they're touched. Update the system prompt to teach the new shape. Tool-specific extras (`dst` for move_file, `occurrences` for replace_in_file) live at the top level of the envelope.

**Tech Stack:** Python 3.12 stdlib (`json`, `logging`), pytest, PAL daemon (no agent_core changes).

**Spec:** `docs/superpowers/specs/2026-05-13-vault-write-success-shape-design.md`

**No agent_core, no Discord adapter restart, no version bump.** PAL git pull + daemon restart only.

---

## File Structure

**PAL repo (`/home/edible/Projects/PAL/`):**
- Modify: `pal/tools/vault.py` -- add `_maybe_reindex` helper; rewrite return statements across all 5 tool functions.
- Modify: `tests/test_tools_vault.py` -- update existing tests for shape change; add envelope tests per tool.
- Modify: `tests/test_tools_move_file.py` -- update existing move_file tests for new envelope shape.
- Modify: `pal/prompts/system.py` -- rewrite line 100 + add shape callout near tool catalog.
- Modify: `tests/test_prompt_builder.py` -- add 2 tests for the prompt updates.

**No agent_core changes.**

---

## Task 1: Add `_maybe_reindex` helper

**Files:**
- Modify: `/home/edible/Projects/PAL/pal/tools/vault.py` (add helper near top)
- Modify: `/home/edible/Projects/PAL/tests/test_tools_vault.py` (3 unit tests)

The helper collapses 5 copies of the `getattr(ctx.agent, "retrieval", None) + try/except` pattern that are scattered across the file's 5 vault tool functions. Land this first so the per-tool migrations in Tasks 2-6 can call into it.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_tools_vault.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from pal.tools.vault import _maybe_reindex


@pytest.mark.asyncio
async def test_maybe_reindex_returns_none_when_no_client():
    result = await _maybe_reindex(None, ["/some/path"])
    assert result is None


@pytest.mark.asyncio
async def test_maybe_reindex_returns_none_on_exception():
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock(side_effect=RuntimeError("server down"))
    result = await _maybe_reindex(retrieval, ["/some/path"])
    assert result is None


@pytest.mark.asyncio
async def test_maybe_reindex_passes_through_dict_on_success():
    retrieval = MagicMock()
    server_response = {"job_id": "abc-123", "status": "queued", "paths": ["/some/path"]}
    retrieval.trigger_reindex = AsyncMock(return_value=server_response)
    result = await _maybe_reindex(retrieval, ["/some/path"])
    assert result == server_response
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_vault.py -k "maybe_reindex" -v
```

Expected: FAIL with `ImportError: cannot import name '_maybe_reindex' from 'pal.tools.vault'`.

- [ ] **Step 3: Add the helper to `pal/tools/vault.py`**

Near the top of `/home/edible/Projects/PAL/pal/tools/vault.py`, after the existing imports and `logger = logging.getLogger(__name__)`, before any class definitions, add:

```python
async def _maybe_reindex(retrieval, paths: list[str]) -> dict | None:
    """Trigger reindex for the given absolute paths.

    Returns the inference server's response dict on success
    (`{job_id, status, paths}`), or None on any failure (no retrieval
    client, server unreachable, exception). Logs failures at WARN.

    Used by the 5 vault write tools to propagate `wait_for_reindex`-ready
    job_ids into their canonical {status, path, reindex} envelope.
    """
    if retrieval is None:
        return None
    try:
        return await retrieval.trigger_reindex(paths=paths)
    except Exception as exc:
        logger.warning("reindex trigger failed: %s", exc)
        return None
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_vault.py -k "maybe_reindex" -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/tools/vault.py tests/test_tools_vault.py && git commit -m "$(cat <<'EOF'
feat(vault): add _maybe_reindex helper

Collapses 5 copies of the `getattr(ctx.agent, "retrieval", None) +
try/except` pattern that exist today across edit_file, create_file,
delete_file, move_file, and replace_in_file. Returns the inference
server's response dict on success or None on any failure (no client,
unreachable, exception). Logs failures at WARN.

Will be used by the per-tool envelope migrations in the next 5 tasks
to propagate wait_for_reindex-ready job_ids into the canonical
{status, path, reindex} response shape.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Migrate edit_file to canonical envelope

**Files:**
- Modify: `/home/edible/Projects/PAL/pal/tools/vault.py` (the `edit_file` Tool subclass)
- Modify: `/home/edible/Projects/PAL/tests/test_tools_vault.py` (update 6 existing tests + add 4 new)

This task sets the migration pattern that Tasks 3-6 follow.

- [ ] **Step 1: Write failing tests for the new envelope**

Append to `tests/test_tools_vault.py`:

```python
import json


@pytest.mark.asyncio
async def test_edit_file_success_returns_canonical_envelope(tmp_path):
    """edit_file returns {status: 'updated', path, reindex} on success."""
    # Set up vault with a wiki manager and an existing file
    from pal.wiki import WikiManager
    from pal.tools.vault import EditFile
    (tmp_path / "foo.md").write_text("---\ntitle: foo\n---\noriginal body\n")
    wiki = WikiManager(tmp_path)
    retrieval = MagicMock()
    server_response = {"job_id": "xyz", "status": "queued", "paths": [str(tmp_path / "foo.md")]}
    retrieval.trigger_reindex = AsyncMock(return_value=server_response)
    agent = MagicMock(config=MagicMock(vault_path=tmp_path), wiki=wiki, retrieval=retrieval)
    ctx = MagicMock(agent=agent)
    result = await EditFile().run({"path": "foo.md", "content": "new body"}, ctx)
    payload = json.loads(result)
    assert payload["status"] == "updated"
    assert payload["path"] == "foo.md"
    assert payload["reindex"] == server_response


@pytest.mark.asyncio
async def test_edit_file_error_returns_canonical_envelope(tmp_path):
    """edit_file returns {status: 'error', path, reason} on parameter error."""
    from pal.tools.vault import EditFile
    agent = MagicMock(config=MagicMock(vault_path=tmp_path))
    ctx = MagicMock(agent=agent)
    result = await EditFile().run({}, ctx)
    payload = json.loads(result)
    assert payload["status"] == "error"
    assert payload["path"] == ""
    assert "path" in payload["reason"].lower()
    assert "required" in payload["reason"].lower()


@pytest.mark.asyncio
async def test_edit_file_reindex_null_when_no_client(tmp_path):
    """edit_file returns reindex: null when agent has no retrieval client."""
    from pal.wiki import WikiManager
    from pal.tools.vault import EditFile
    (tmp_path / "foo.md").write_text("---\ntitle: foo\n---\noriginal\n")
    wiki = WikiManager(tmp_path)
    agent = MagicMock(config=MagicMock(vault_path=tmp_path), wiki=wiki, retrieval=None)
    ctx = MagicMock(agent=agent)
    result = await EditFile().run({"path": "foo.md", "content": "new"}, ctx)
    payload = json.loads(result)
    assert payload["status"] == "updated"
    assert payload["reindex"] is None


@pytest.mark.asyncio
async def test_edit_file_reindex_null_on_trigger_exception(tmp_path):
    """edit_file returns reindex: null when trigger_reindex raises."""
    from pal.wiki import WikiManager
    from pal.tools.vault import EditFile
    (tmp_path / "foo.md").write_text("---\ntitle: foo\n---\noriginal\n")
    wiki = WikiManager(tmp_path)
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock(side_effect=RuntimeError("down"))
    agent = MagicMock(config=MagicMock(vault_path=tmp_path), wiki=wiki, retrieval=retrieval)
    ctx = MagicMock(agent=agent)
    result = await EditFile().run({"path": "foo.md", "content": "new"}, ctx)
    payload = json.loads(result)
    assert payload["status"] == "updated"
    assert payload["reindex"] is None
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_vault.py -k "edit_file_success_returns_canonical or edit_file_error_returns_canonical or edit_file_reindex_null" -v
```

Expected: FAIL because edit_file currently returns `"Updated: foo.md"` and bare error strings.

- [ ] **Step 3: Find and update existing edit_file tests that assert the old shape**

```bash
cd /home/edible/Projects/PAL && grep -n "Updated:\|test_edit_file" tests/test_tools_vault.py | head -10
```

For each existing edit_file test that asserts on the bare `"Updated: ..."` or bare `"Error: ..."` string, rewrite the assertion to parse JSON and check the envelope. Specifically:

- `test_edit_file_happy_path` (around line 43): change `assert "Updated: foo.md" in result` to:
  ```python
  payload = json.loads(result)
  assert payload["status"] == "updated"
  assert payload["path"] == "foo.md"
  ```
- `test_edit_file_missing` (line 63): change `assert "file does not exist" in result` to:
  ```python
  payload = json.loads(result)
  assert payload["status"] == "error"
  assert "does not exist" in payload["reason"]
  ```
  (The 404 case still includes the "Did you mean" suggestion tail inside `reason`; the substring still matches.)
- `test_edit_file_no_wiki` (line 77): change `assert "no wiki manager" in result` to:
  ```python
  payload = json.loads(result)
  assert payload["status"] == "error"
  assert "no wiki manager" in payload["reason"]
  ```
- `test_edit_file_empty_content` (line 90): same pattern, parse JSON and check `payload["reason"]`.
- `test_edit_file_system_dir` (line 229): same pattern.
- `test_edit_file_path_traversal` (line 239): same pattern.
- `test_edit_file_no_reindex_when_no_retrieval` (line 247): rewrite to assert `payload["reindex"] is None` and `payload["status"] == "updated"`.

- [ ] **Step 4: Migrate edit_file in `pal/tools/vault.py`**

Find the `EditFile` class and its `run` method (around line 60-115). Locate each `return` statement and rewrite per the spec.

The new `run` body shape:

```python
    async def run(self, args, ctx):
        path = (args.get("path") or "").strip()
        content = args.get("content")
        if not path:
            return json.dumps({"status": "error", "path": "", "reason": "'path' parameter is required."})
        if content is None:
            return json.dumps({"status": "error", "path": path, "reason": "'content' parameter is required."})
        if _is_system_path(path):
            return json.dumps({"status": "error", "path": path, "reason": f"writing to system directories is not allowed: {path}"})

        vault = ctx.agent.config.vault_path.resolve()
        resolved = _resolve_safe(vault, path)
        if resolved is None:
            return json.dumps({"status": "error", "path": path, "reason": f"path escapes outside vault: {path}"})
        if not resolved.exists():
            base = f"file does not exist: {path} (use create_file for new files)"
            reason = format_not_found_with_suggestions(ctx.agent.config.vault_path, path, base)
            return json.dumps({"status": "error", "path": path, "reason": reason})

        wiki = getattr(ctx.agent, "wiki", None)
        if wiki is None:
            return json.dumps({"status": "error", "path": path, "reason": "write operations are not available (no wiki manager)."})

        meta, _ = wiki.read_article(path)
        title = meta.get("title", Path(path).stem)
        wiki.write_article(path, body=content, title=title)
        wiki.git_commit(f"Edit {path} via chat")

        reindex = await _maybe_reindex(getattr(ctx.agent, "retrieval", None), [str(resolved)])
        return json.dumps({"status": "updated", "path": path, "reindex": reindex})
```

Drop the existing reindex try/except block (lines ~105-111) since `_maybe_reindex` handles it. Drop the bare `return f"Updated: {path}"` (line 113).

- [ ] **Step 5: Run all edit_file tests, verify they pass**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_vault.py -k "edit_file" -v
```

Expected: all edit_file tests pass (4 new + ~7 updated existing).

- [ ] **Step 6: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/tools/vault.py tests/test_tools_vault.py && git commit -m "$(cat <<'EOF'
feat(vault): edit_file emits canonical {status, path, reindex} envelope

Replaces the bare "Updated: foo.md" string with JSON
`{status: "updated", path, reindex}`. Errors return the canonical
{status: "error", path, reason} envelope; the 404 path still includes
the "Did you mean" suggestion tail inside `reason`. Reindex is the
inference server's response dict on success or null on any failure
(no retrieval client, server unreachable, exception) -- captured via
the new _maybe_reindex helper.

Updates 7 existing tests that asserted the old prose shape; adds 4
new tests for the canonical envelope behavior.

First per-tool migration in the path-determinism cluster's vault-write
shape spec; sets the pattern for create_file/delete_file/move_file/
replace_in_file to follow.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Migrate create_file to canonical envelope

**Files:**
- Modify: `/home/edible/Projects/PAL/pal/tools/vault.py` (the `create_file` Tool subclass)
- Modify: `/home/edible/Projects/PAL/tests/test_tools_vault.py`

Mirrors Task 2 with `status: "created"` and `create_file`-specific error messages.

- [ ] **Step 1: Write failing tests for the new envelope**

Append to `tests/test_tools_vault.py`:

```python
@pytest.mark.asyncio
async def test_create_file_success_returns_canonical_envelope(tmp_path):
    """create_file returns {status: 'created', path, reindex} on success."""
    from pal.wiki import WikiManager
    from pal.tools.vault import CreateFile
    raw = tmp_path / "raw" / "notes"
    raw.mkdir(parents=True)
    wiki = WikiManager(tmp_path)
    retrieval = MagicMock()
    server_response = {"job_id": "abc", "status": "queued", "paths": [str(tmp_path / "raw" / "notes" / "foo.md")]}
    retrieval.trigger_reindex = AsyncMock(return_value=server_response)
    agent = MagicMock(config=MagicMock(vault_path=tmp_path), wiki=wiki, retrieval=retrieval)
    ctx = MagicMock(agent=agent)
    result = await CreateFile().run({"path": "raw/notes/foo.md", "title": "Foo", "content": "body"}, ctx)
    payload = json.loads(result)
    assert payload["status"] == "created"
    assert payload["path"] == "raw/notes/foo.md"
    assert payload["reindex"] == server_response


@pytest.mark.asyncio
async def test_create_file_error_returns_canonical_envelope(tmp_path):
    """create_file missing-path error uses canonical envelope."""
    from pal.tools.vault import CreateFile
    agent = MagicMock(config=MagicMock(vault_path=tmp_path))
    ctx = MagicMock(agent=agent)
    result = await CreateFile().run({}, ctx)
    payload = json.loads(result)
    assert payload["status"] == "error"
    assert payload["path"] == ""
    assert "path" in payload["reason"].lower()


@pytest.mark.asyncio
async def test_create_file_reindex_null_when_no_client(tmp_path):
    """create_file returns reindex: null when agent has no retrieval client."""
    from pal.wiki import WikiManager
    from pal.tools.vault import CreateFile
    raw = tmp_path / "raw" / "notes"
    raw.mkdir(parents=True)
    wiki = WikiManager(tmp_path)
    agent = MagicMock(config=MagicMock(vault_path=tmp_path), wiki=wiki, retrieval=None)
    ctx = MagicMock(agent=agent)
    result = await CreateFile().run({"path": "raw/notes/x.md", "title": "X", "content": "body"}, ctx)
    payload = json.loads(result)
    assert payload["status"] == "created"
    assert payload["reindex"] is None
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_vault.py -k "create_file_success_returns_canonical or create_file_error_returns_canonical or create_file_reindex_null" -v
```

Expected: FAIL.

- [ ] **Step 3: Update existing create_file tests**

Find existing create_file tests asserting `"Created: ..."` or bare `"Error: ..."`. Each becomes a JSON parse + envelope check:

- `test_create_file_happy_path` (line 101): `assert json.loads(result)["status"] == "created"` and `["path"] == <path>`.
- `test_create_file_refuses_overwrite` (line 120): `assert json.loads(result)["status"] == "error"` and `"already exists" in payload["reason"]`.
- `test_create_file_missing_title` (line 135): same JSON pattern with `"title" in payload["reason"]`.
- `test_create_file_no_wiki` (line 143): `"no wiki manager" in payload["reason"]`.
- `test_create_file_system_dir` (line 264): `"system directories" in payload["reason"]`.
- `test_create_file_rejects_promoted_category` (line 272): same pattern with the relevant message.
- `test_create_file_wiki_write_not_called_outside_raw` (line 280): assertion on payload status.
- `test_create_file_path_traversal` (line 291): same pattern.

- [ ] **Step 4: Migrate create_file in `pal/tools/vault.py`**

Locate `CreateFile.run` (around line 156-208). Rewrite each `return` per the spec, mirroring edit_file's pattern. The success line (210) becomes:

```python
        reindex = await _maybe_reindex(getattr(ctx.agent, "retrieval", None), [str(resolved)])
        return json.dumps({"status": "created", "path": path, "reindex": reindex})
```

All bare error strings become `json.dumps({"status": "error", "path": <path or "">, "reason": <message>})`.

Drop the existing reindex try/except block (lines ~200-206).

- [ ] **Step 5: Run all create_file tests**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_vault.py -k "create_file" -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/tools/vault.py tests/test_tools_vault.py && git commit -m "$(cat <<'EOF'
feat(vault): create_file emits canonical {status, path, reindex} envelope

Mirrors the edit_file migration. Bare "Created: foo.md" string becomes
JSON `{status: "created", path, reindex}`. Errors use the same
canonical envelope. Reindex via _maybe_reindex helper.

Updates 8 existing create_file tests; adds 3 new envelope tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Migrate delete_file (minor: swap reindex string for dict|null)

**Files:**
- Modify: `/home/edible/Projects/PAL/pal/tools/vault.py` (the `delete_file` Tool subclass)
- Modify: `/home/edible/Projects/PAL/tests/test_tools_vault.py`

Smaller delta: delete_file already returns the right envelope shape. Just swap the `reindex_status` string for the helper's `dict|null`, plus normalize bare error strings to JSON.

- [ ] **Step 1: Write failing tests for the new reindex shape**

Append to `tests/test_tools_vault.py`:

```python
@pytest.mark.asyncio
async def test_delete_file_reindex_passes_through_dict(tmp_path):
    """delete_file's reindex field is the inference server's response dict, not a string."""
    from pal.wiki import WikiManager
    from pal.tools.vault import DeleteFile
    (tmp_path / "foo.md").write_text("---\ntitle: foo\n---\nbody\n")
    wiki = WikiManager(tmp_path)
    wiki.git_init()
    retrieval = MagicMock()
    server_response = {"job_id": "del-1", "status": "queued", "paths": [str(tmp_path / "foo.md")]}
    retrieval.trigger_reindex = AsyncMock(return_value=server_response)
    agent = MagicMock(config=MagicMock(vault_path=tmp_path), wiki=wiki, retrieval=retrieval)
    ctx = MagicMock(agent=agent)
    result = await DeleteFile().run({"path": "foo.md"}, ctx)
    payload = json.loads(result)
    assert payload["status"] == "deleted"
    assert payload["path"] == "foo.md"
    assert payload["reindex"] == server_response


@pytest.mark.asyncio
async def test_delete_file_reindex_null_on_failure(tmp_path):
    """delete_file's reindex field is null when trigger_reindex raises."""
    from pal.wiki import WikiManager
    from pal.tools.vault import DeleteFile
    (tmp_path / "foo.md").write_text("---\ntitle: foo\n---\nbody\n")
    wiki = WikiManager(tmp_path)
    wiki.git_init()
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock(side_effect=RuntimeError("down"))
    agent = MagicMock(config=MagicMock(vault_path=tmp_path), wiki=wiki, retrieval=retrieval)
    ctx = MagicMock(agent=agent)
    result = await DeleteFile().run({"path": "foo.md"}, ctx)
    payload = json.loads(result)
    assert payload["status"] == "deleted"
    assert payload["reindex"] is None


@pytest.mark.asyncio
async def test_delete_file_error_uses_canonical_envelope(tmp_path):
    """delete_file's parameter-validation errors use the canonical envelope."""
    from pal.tools.vault import DeleteFile
    agent = MagicMock(config=MagicMock(vault_path=tmp_path))
    ctx = MagicMock(agent=agent)
    result = await DeleteFile().run({}, ctx)
    payload = json.loads(result)
    assert payload["status"] == "error"
    assert payload["path"] == ""
    assert "path" in payload["reason"].lower()
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_vault.py -k "delete_file_reindex_passes_through or delete_file_reindex_null or delete_file_error_uses_canonical" -v
```

Expected: FAIL.

- [ ] **Step 3: Update existing delete_file tests**

- `test_delete_file_removes_file_via_git_rm_and_commits` (line 313): if it asserts `payload["reindex"] == "ok"`, change to `assert payload["reindex"] is not None and "job_id" in payload["reindex"]` (or similar shape check based on the test's mock).
- `test_delete_file_refuses_system_dirs` (line 337): change bare-string assertion to JSON parse + `payload["reason"]` substring.
- `test_delete_file_refuses_path_escape` (line 358): same pattern.
- `test_delete_file_surfaces_reindex_failure` (line 375): rewrite to assert `payload["reindex"] is None` (the new failure semantics).

- [ ] **Step 4: Migrate delete_file in `pal/tools/vault.py`**

Locate `DeleteFile.run` (around line 308-363). Update three things:

1. Bare error returns become JSON:
   ```python
   if not path:
       return json.dumps({"status": "error", "path": "", "reason": "'path' parameter is required."})
   if _is_system_path(path):
       return json.dumps({"status": "error", "path": path, "reason": f"writing to system directories is not allowed: {path}"})
   if resolved is None:
       return json.dumps({"status": "error", "path": path, "reason": f"path escapes outside vault: {path}"})
   ```
   The 404 path (currently uses `format_not_found_with_suggestions` returning a bare string) becomes:
   ```python
   if not resolved.exists():
       base = f"file does not exist: {path}"
       reason = format_not_found_with_suggestions(ctx.agent.config.vault_path, path, base)
       return json.dumps({"status": "error", "path": path, "reason": reason})
   ```
   The "no wiki manager" return:
   ```python
   if wiki is None:
       return json.dumps({"status": "error", "path": path, "reason": "write operations are not available (no wiki manager)."})
   ```
   The git rm failure:
   ```python
   except Exception as exc:
       return json.dumps({"status": "error", "path": path, "reason": f"git rm failed: {exc}"})
   ```

2. The `deleted_uncommitted` partial-failure JSON return is preserved verbatim (already uses `status` field with a `warning`, just no `reindex` key for that branch).

3. The success return (line 359-363) swaps:
   ```python
   reindex = await _maybe_reindex(getattr(ctx.agent, "retrieval", None), [str(resolved)])
   return json.dumps({"status": "deleted", "path": path, "reindex": reindex})
   ```
   Drop the existing `reindex_status = "ok"` block (lines 350-357).

- [ ] **Step 5: Run all delete_file tests**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_vault.py -k "delete_file" -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/tools/vault.py tests/test_tools_vault.py && git commit -m "$(cat <<'EOF'
feat(vault): delete_file aligns reindex shape (str -> dict|null) + normalize errors

Already returned the right envelope shape; this just swaps the reindex
field from a string flag ("ok"/"failed") to the inference server's
response dict (or null on failure) via _maybe_reindex. All previously-
bare error strings now use the canonical {status: "error", path, reason}
envelope. The "deleted_uncommitted" partial-failure path is preserved
verbatim (no reindex key for that branch).

Updates 4 existing delete_file tests; adds 3 new tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Migrate replace_in_file (swap reindex shape + git-commit-failure to JSON envelope)

**Files:**
- Modify: `/home/edible/Projects/PAL/pal/tools/vault.py` (the `replace_in_file` Tool subclass)
- Modify: `/home/edible/Projects/PAL/tests/test_tools_vault.py`

Like delete_file, replace_in_file already returns the right envelope. Migration: swap `reindex_status` string for `dict|null`, normalize bare errors to JSON, and convert the git-commit-failed path (which today returns a bare error string) to the canonical error envelope.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_tools_vault.py`:

```python
@pytest.mark.asyncio
async def test_replace_in_file_reindex_passes_through_dict(tmp_path):
    """replace_in_file's reindex field is the server's response dict."""
    from pal.tools.vault import ReplaceInFile
    (tmp_path / "foo.md").write_text("hello world")
    retrieval = MagicMock()
    server_response = {"job_id": "rep-1", "status": "queued", "paths": [str(tmp_path / "foo.md")]}
    retrieval.trigger_reindex = AsyncMock(return_value=server_response)
    agent = MagicMock(config=MagicMock(vault_path=tmp_path), wiki=MagicMock(), retrieval=retrieval)
    ctx = MagicMock(agent=agent)
    result = await ReplaceInFile().run(
        {"path": "foo.md", "old_string": "hello", "new_string": "hi"}, ctx,
    )
    payload = json.loads(result)
    assert payload["status"] == "replaced"
    assert payload["path"] == "foo.md"
    assert payload["occurrences"] == 1
    assert payload["reindex"] == server_response


@pytest.mark.asyncio
async def test_replace_in_file_commit_failure_returns_error_envelope(tmp_path):
    """replace_in_file's git-commit-failed path returns the canonical error envelope.

    Original content must be restored on disk.
    """
    from pal.tools.vault import ReplaceInFile
    (tmp_path / "foo.md").write_text("hello world")
    wiki = MagicMock()
    wiki.git_commit = MagicMock(side_effect=RuntimeError("git failed"))
    agent = MagicMock(config=MagicMock(vault_path=tmp_path), wiki=wiki, retrieval=None)
    ctx = MagicMock(agent=agent)
    result = await ReplaceInFile().run(
        {"path": "foo.md", "old_string": "hello", "new_string": "hi"}, ctx,
    )
    payload = json.loads(result)
    assert payload["status"] == "error"
    assert payload["path"] == "foo.md"
    assert "git commit failed" in payload["reason"]
    assert "restored" in payload["reason"]
    # Original content restored
    assert (tmp_path / "foo.md").read_text() == "hello world"


@pytest.mark.asyncio
async def test_replace_in_file_error_uses_canonical_envelope(tmp_path):
    """replace_in_file's parameter-validation errors use the canonical envelope."""
    from pal.tools.vault import ReplaceInFile
    agent = MagicMock(config=MagicMock(vault_path=tmp_path))
    ctx = MagicMock(agent=agent)
    result = await ReplaceInFile().run({}, ctx)
    payload = json.loads(result)
    assert payload["status"] == "error"
    assert payload["path"] == ""
    assert "path" in payload["reason"].lower()
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_vault.py -k "replace_in_file_reindex_passes or replace_in_file_commit_failure or replace_in_file_error_uses" -v
```

Expected: FAIL.

- [ ] **Step 3: Update existing replace_in_file tests**

Search for existing replace_in_file tests (around lines 415-541 in the file):
```bash
grep -n "test_replace_in_file" tests/test_tools_vault.py
```

For each, if it asserts on bare strings or `payload["reindex"] == "ok"`, update to use the new shape (JSON parse + envelope check + `reindex is dict|None`).

- [ ] **Step 4: Migrate replace_in_file in `pal/tools/vault.py`**

Locate `ReplaceInFile.run` (around line 425-507). Update:

1. Bare error returns to JSON envelope (same pattern as delete_file).
2. The git-commit-failed branch (line 488-491) becomes:
   ```python
   except Exception as exc:
       resolved.write_text(original_text, encoding="utf-8")
       return json.dumps({
           "status": "error",
           "path": path,
           "reason": f"git commit failed; original content restored: {exc}",
       })
   ```
3. The success return (line 502-507):
   ```python
   reindex = await _maybe_reindex(getattr(ctx.agent, "retrieval", None), [str(resolved)])
   return json.dumps({
       "status": "replaced",
       "path": path,
       "occurrences": occurrences,
       "reindex": reindex,
   })
   ```
   Drop the existing `reindex_status` block (lines 493-500).

- [ ] **Step 5: Run all replace_in_file tests**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_vault.py -k "replace_in_file" -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/tools/vault.py tests/test_tools_vault.py && git commit -m "$(cat <<'EOF'
feat(vault): replace_in_file aligns reindex shape + git-commit-failure JSON

Already returned the canonical envelope; this swaps reindex from a
string flag to the inference server's response dict (or null on
failure) via _maybe_reindex. The git-commit-failure branch now returns
the canonical {status: "error", path, reason} envelope instead of a
bare error string; the rollback behavior (restore original content
on disk before returning) is preserved.

Updates existing replace_in_file tests; adds 3 new envelope tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Migrate move_file (biggest delta: keys change)

**Files:**
- Modify: `/home/edible/Projects/PAL/pal/tools/vault.py` (the `move_file` Tool subclass)
- Modify: `/home/edible/Projects/PAL/tests/test_tools_vault.py`
- Modify: `/home/edible/Projects/PAL/tests/test_tools_move_file.py`

Largest delta of the 5: today's `{moved: "src -> dst", reindex_queued: bool}` becomes `{status: "moved", path: <src>, dst, reindex}`. Key set changes substantially.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_tools_vault.py`:

```python
@pytest.mark.asyncio
async def test_move_file_success_returns_canonical_envelope(tmp_path):
    """move_file returns {status: 'moved', path: src, dst, reindex}."""
    from pal.tools.vault import MoveFile
    (tmp_path / "src.md").write_text("body")
    reorganizer = MagicMock()
    reorganizer.move_single = MagicMock()  # success
    retrieval = MagicMock()
    server_response = {"job_id": "mv-1", "status": "queued", "paths": [str(tmp_path / "dst.md")]}
    retrieval.trigger_reindex = AsyncMock(return_value=server_response)
    agent = MagicMock(config=MagicMock(vault_path=tmp_path), reorganizer=reorganizer, retrieval=retrieval)
    ctx = MagicMock(agent=agent)
    result = await MoveFile().run({"src": "src.md", "dst": "dst.md"}, ctx)
    payload = json.loads(result)
    assert payload["status"] == "moved"
    assert payload["path"] == "src.md"
    assert payload["dst"] == "dst.md"
    assert payload["reindex"] == server_response


@pytest.mark.asyncio
async def test_move_file_error_uses_canonical_envelope(tmp_path):
    """move_file's parameter-validation errors use the canonical envelope."""
    from pal.tools.vault import MoveFile
    agent = MagicMock(config=MagicMock(vault_path=tmp_path), reorganizer=MagicMock())
    ctx = MagicMock(agent=agent)
    result = await MoveFile().run({}, ctx)
    payload = json.loads(result)
    assert payload["status"] == "error"
    # path is "" because no src was provided
    assert payload["path"] == ""
    assert "src" in payload["reason"].lower() or "dst" in payload["reason"].lower()
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_vault.py -k "move_file_success_returns_canonical or move_file_error_uses_canonical" -v
```

Expected: FAIL.

- [ ] **Step 3: Update existing move_file tests in `test_tools_vault.py`**

Find and update:
- `test_move_file_happy_path` (line 156): assert `payload["status"] == "moved"`, `payload["path"] == "src.md"`, `payload["dst"] == "dst.md"`.
- `test_move_file_no_reorganizer` (line 177): JSON parse, status="error", reason mentions reorganizer.
- `test_move_file_move_single_raises_*` tests (lines 187-225): JSON parse, status="error", reason matches the underlying error message.
- `test_move_file_empty_args` (line 301): JSON parse, status="error".

- [ ] **Step 4: Update existing move_file tests in `test_tools_move_file.py`**

Find:
```bash
grep -n "test_move_file" tests/test_tools_move_file.py
```

Update each:
- `test_move_file_moves_and_triggers_reindex` (line 65): assert envelope shape; reindex is dict (since reindex was triggered with a mock).
- `test_move_file_rejects_missing_src` (line 85): JSON parse; assert status="error" and reason includes "does not exist" + "Did you mean".
- `test_move_file_rejects_existing_dst` (line 99): JSON parse; status="error"; reason mentions dst exists.
- `test_move_file_rejects_empty_args` (line 114): JSON parse; status="error".
- `test_move_file_rejects_system_dirs` (line 124): JSON parse; status="error".

- [ ] **Step 5: Migrate move_file in `pal/tools/vault.py`**

Locate `MoveFile.run` (around line 240-280). Rewrite:

```python
    async def run(self, args, ctx):
        src = (args.get("src") or "").strip()
        dst = (args.get("dst") or "").strip()
        if not src or not dst:
            return json.dumps({"status": "error", "path": "", "reason": "src and dst are required"})

        reorganizer = getattr(ctx.agent, "reorganizer", None)
        if reorganizer is None:
            return json.dumps({"status": "error", "path": src, "reason": "reorganizer not available"})

        # Pre-existence check (added in the previous cluster spec for the
        # nearest-match suggestion). Now also returns the canonical envelope.
        full_src = ctx.agent.config.vault_path / src
        if not full_src.exists():
            base = f"file does not exist: {src}"
            reason = format_not_found_with_suggestions(ctx.agent.config.vault_path, src, base)
            return json.dumps({"status": "error", "path": src, "reason": reason})

        try:
            reorganizer.move_single(src, dst)
        except Exception as exc:
            return json.dumps({"status": "error", "path": src, "reason": str(exc)})

        absolute_dst = str((ctx.agent.config.vault_path / dst).resolve())
        reindex = await _maybe_reindex(getattr(ctx.agent, "retrieval", None), [absolute_dst])
        return json.dumps({
            "status": "moved",
            "path": src,
            "dst": dst,
            "reindex": reindex,
        })
```

Drop the existing reindex try/except block (lines 268-278).

- [ ] **Step 6: Run all move_file tests across both files**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_vault.py tests/test_tools_move_file.py -k "move_file" -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/tools/vault.py tests/test_tools_vault.py tests/test_tools_move_file.py && git commit -m "$(cat <<'EOF'
feat(vault): move_file emits canonical {status, path, dst, reindex} envelope

Largest of the 5 vault-write migrations. Today's
`{moved: "src -> dst", reindex_queued: True}` becomes
`{status: "moved", path: src, dst, reindex}`. The `path` field now
holds the source (matching "the operation subject is `path`" convention
used by edit/create/delete/replace_in_file). The `dst` field holds
the destination. Reindex is the inference server's response dict via
_maybe_reindex (or null on failure).

All bare error strings are converted to the canonical
{status: "error", path, reason} envelope. The 404-on-source case
preserves the "Did you mean" suggestion tail inside `reason`.

Updates ~9 existing tests across test_tools_vault.py and
test_tools_move_file.py; adds 2 new envelope tests.

Closes the path-determinism cluster's vault-write success shape +
reindex propagation cross-cutting must-fix from the 2026-05-11 audit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Update PAL system prompt

**Files:**
- Modify: `/home/edible/Projects/PAL/pal/prompts/system.py` (rewrite line 100 + add tool catalog callout)
- Modify: `/home/edible/Projects/PAL/tests/test_prompt_builder.py` (2 new tests)

- [ ] **Step 1: Write failing tests**

Append to `/home/edible/Projects/PAL/tests/test_prompt_builder.py`:

```python
def test_base_prompt_describes_vault_write_envelope():
    """The prompt teaches PAL the canonical {status, path, reindex} envelope."""
    assert "{status, path, reindex}" in PAL_BASE_PROMPT or "{status: " in PAL_BASE_PROMPT


def test_base_prompt_reindex_field_documents_dict_or_null():
    """The prompt explains reindex is a dict (with job_id) OR null."""
    lower = PAL_BASE_PROMPT.lower()
    assert "null" in lower
    assert "job_id" in lower
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_prompt_builder.py::test_base_prompt_describes_vault_write_envelope tests/test_prompt_builder.py::test_base_prompt_reindex_field_documents_dict_or_null -v
```

Expected: at least one fails (the `{status, path, reindex}` literal isn't in the prompt today).

- [ ] **Step 3: Locate and rewrite the existing reindex line**

```bash
cd /home/edible/Projects/PAL && grep -n "reindex" pal/prompts/system.py
```

Find the line that says (verbatim or close to):
```
- After a write tool succeeds, its result includes a `reindex` field with a `job_id` and current `status`. The inference server reindexes the new content automatically; the `status` field tells you whether it has finished. You normally do not need to wait -- by the time the next user message arrives, the reindex will be done. Call wait_for_reindex only when you need to search_vault for the just-written content within the SAME response.
```

Replace with:

```
- After a write tool returns, its result is JSON `{status, path, reindex}`. The `reindex` field is either `null` (content was not indexed, e.g. retrieval server unreachable) or a dict like `{job_id, status, paths}`. Use wait_for_reindex with the `job_id` only when you need to search_vault for the just-written content within the SAME response. The inference server reindexes automatically; by the time the next user message arrives, the reindex will be done.
```

- [ ] **Step 4: Add a one-line shape callout near the tool catalog**

Locate the existing edit_file/create_file mentions (the catalog around `pal/prompts/system.py:18`). Find:

```
- cat, ls, grep, search_vault: vault reads. ...
```

Add a new bullet AFTER the existing search_vault JSON-shape callouts (which were added in the search_vault spec) so vault-write callouts live alongside vault-read callouts:

```
- All vault writes (edit_file, create_file, delete_file, move_file, replace_in_file) return JSON with the same envelope: `{status: "<verb>", path: "<vault-rel>", reindex: <dict|null>}`. Errors use the same envelope with `status: "error"` and a `reason` field. move_file adds `dst`; replace_in_file adds `occurrences`.
```

- [ ] **Step 5: Run tests, verify they pass**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_prompt_builder.py -v
```

Expected: both new tests pass; all existing prompt tests still pass.

- [ ] **Step 6: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/prompts/system.py tests/test_prompt_builder.py && git commit -m "$(cat <<'EOF'
feat(prompts): teach PAL the vault-write canonical envelope

Two changes in PAL_BASE_PROMPT:

1. Rewrite the existing line about the `reindex` field to match the
   actual new shape. Today's prompt promised `{job_id, status}` from
   any write tool but tools delivered string flags or no JSON at all.
   New text: reindex is null (failure) or a dict like
   {job_id, status, paths} (success). Use wait_for_reindex with the
   job_id only for same-response search.

2. Add a tool-catalog bullet describing the canonical envelope:
   {status, path, reindex} across all 5 vault writes, with `dst` for
   move_file and `occurrences` for replace_in_file as inline extras,
   and {status: "error", path, reason} for all error returns.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Full-suite regression sweep

**Files:** None modified unless a non-preexisting failure surfaces.

- [ ] **Step 1: Run PAL suite**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/ \
    --ignore=tests/test_chat_research_integration.py \
    --ignore=tests/test_client.py \
    --ignore=tests/test_daemon.py \
    --ignore=tests/test_integration.py \
    --ignore=tests/test_prompt_injection.py \
    -q 2>&1 | tail -5
```

Expected: all pass. The five `--ignore` flags match the documented pre-existing pal.client collection failures.

- [ ] **Step 2: Diagnose any non-preexisting failures**

The most likely failure class for THIS workstream is a test that asserts `result == "Updated: foo.md"` (or similar bare-string equality) somewhere outside `test_tools_vault.py` / `test_tools_move_file.py` -- e.g., an integration test in another file that happens to call edit_file. Fix by parsing JSON and checking the envelope.

If the failure isn't shape-related, diagnose at the right layer; don't paper over.

- [ ] **Step 3: Em-dash sweep on the diff**

```bash
cd /home/edible/Projects/PAL && git diff main..HEAD | grep -P '[\x{2014}\x{2013}]' || echo "no em dashes in PAL diff"
```

Should print "no em dashes in PAL diff".

- [ ] **Step 4: Final commit only if a fix was needed**

```bash
git add -- <specific files> && git commit -m "fix(tests): adapt <area> to canonical vault-write envelope

<one-line description>

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
"
```

---

## Self-review checklist

- [ ] Every task has Files section with exact paths.
- [ ] Every test step shows the assertion code.
- [ ] Every implementation step shows the actual code change, not a description.
- [ ] No "TBD", "TODO", "implement later" anywhere.
- [ ] Names used in later tasks match earlier tasks (`_maybe_reindex`, the envelope key set, the verb for each tool).
- [ ] No agent_core changes; no version bump; no Discord adapter restart in deploy notes.
- [ ] Each per-tool task is independently committable (later task's changes don't depend on earlier task's tests passing -- though they do depend on Task 1's helper).
- [ ] Helper unit tests are in Task 1.
- [ ] Per-tool envelope tests are in each migration task (3-4 per tool).
- [ ] Existing test updates are enumerated, not hand-waved.
- [ ] Prompt update tests are in Task 7.
- [ ] All commit messages end with the Co-Authored-By line.
- [ ] No em dashes in any commit message or added prompt/comment text.

## Out of scope

- agent_core changes (verified upfront with the user).
- Migrating other tool families to the envelope (compile, consolidate, research, etc.).
- Tag-aware or content-aware reindex routing.
- Caching reindex job_ids across tool calls.
- Retry logic on `trigger_reindex` failure.
- Updating downstream documentation (READMEs, runbooks) that describe the old shapes.
- Discriminated-union envelope (rejected during brainstorming).
- Server-side deploy (the user handles deploy; PAL git pull + daemon restart only after merge).
