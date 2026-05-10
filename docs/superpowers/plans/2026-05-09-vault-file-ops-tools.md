# Vault File-Ops Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two new vault-write tools (`delete_file`, `replace_in_file`) and tighten the existing `edit_file` description so the agent has efficient targeted-edit and delete primitives, with explicit routing between the new and existing edit paths.

**Architecture:** Two new `Tool` subclasses in `pal/tools/vault.py` plus a description rewrite on the existing `EditFile`. Both new tools follow the existing `EditFile`/`CreateFile`/`MoveFile` pattern. `delete_file` uses `git rm` for atomic stage-and-remove. `replace_in_file` operates on body only (frontmatter parsed out and reattached unchanged) and restores the original body on commit failure. Both surface reindex failure in the response. UTF-8 encoding explicit on every read/write.

**Tech Stack:** Python 3, pytest with pytest-asyncio in auto mode, `agent_core.tools.base.Tool`, `agent_core.utils.frontmatter.parse_frontmatter` / `serialize_frontmatter`, `subprocess` for `git rm` if `wiki.git_rm()` is not yet a helper.

**Scope note:** This is the trimmed scope from the panel review on 2026-05-09. The original four-tool design (`delete_file`, `replace_in_file`, `append_to_file`, `edit_frontmatter`) was reduced to two after a four-expert review found `append_to_file` redundant with `replace_in_file` and `edit_frontmatter` premature without observed demand. See spec for details.

---

## File Structure

**Modified (additions and one description rewrite):**
- `pal/tools/vault.py`: two new classes appended after `MoveFile`; description rewrite on existing `EditFile`
- `pal/tools/__init__.py`: export the two new names
- `pal/agent.py`: register the two in `PALAgent.tools`
- `tests/test_tools_vault.py`: extend with ~10 new tests

**Possibly modified:**
- The wiki helper module that owns `git_commit` (likely `pal/wiki.py` based on prior session work). If `git_rm()` does not already exist there, add it as a thin wrapper around `subprocess.run(["git", "rm", "--", path], cwd=vault_path, check=True)`.

---

## Task 1: Confirm structural map and add `git_rm` helper if needed

**Files (read-only investigation, then optional helper add):**
- Read: `pal/tools/vault.py` end to end
- Read: `tests/test_tools_vault.py` first 80 lines for scaffolding
- Read: `pal/wiki.py` (or wherever `wiki.git_commit` lives) to check for `git_rm`

- [ ] **Step 1: Read `pal/tools/vault.py` end to end**

Confirm:
- `_resolve_safe(vault, path)` returns `Path | None`, returns None on path escape.
- `_is_system_path(path)` returns True if any path component starts with `_`.
- `Tool` is imported from `agent_core.tools.base`.
- `json` and `logging` already imported at module top.
- Existing classes are `EditFile`, `CreateFile`, `MoveFile`, in that order.
- Each existing tool returns plain strings; new tools will return JSON strings (matches the `consolidate`/`url_fix` pattern).

Note any drift from this map; line numbers may have shifted since the spec was written.

- [ ] **Step 2: Read `tests/test_tools_vault.py:1-80` for scaffolding**

Confirm:
- `@dataclass _Config(vault_path: Path)`
- `_Agent(vault_path, retrieval=None, wiki=_UNSET, reorganizer=_UNSET)` with `wiki` defaulting to `MagicMock()`
- `_ctx(agent)` returns object with `.agent`
- Tests use plain `async def test_*` (no `@pytest.mark.asyncio` markers; pytest-asyncio auto mode)
- Real filesystem via `tmp_path`, mocked wiki/retrieval

- [ ] **Step 3: Check whether `wiki.git_rm()` already exists**

Find the wiki helper module (likely `pal/wiki.py`). Search for `def git_rm` or `git_rm`.

If it exists: note the signature, use it from `delete_file`.

If it does NOT exist: this task adds it. Implementation:

```python
def git_rm(self, path: str) -> None:
    """Remove a file via 'git rm', staging the deletion atomically."""
    import subprocess
    subprocess.run(
        ["git", "rm", "--", path],
        cwd=self.vault_path,
        check=True,
        capture_output=True,
        text=True,
    )
```

Add it next to the existing `git_commit` method.

- [ ] **Step 4: If `git_rm` was added, run an existing test that exercises `wiki` to confirm nothing regressed**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_vault.py -v
```

Expected: all existing tests pass.

- [ ] **Step 5: If `git_rm` was added, commit it separately**

```bash
git add pal/wiki.py
git commit -m "feat(wiki): add git_rm helper for atomic file removal"
```

If `git_rm` already existed, no commit. Move to Task 2.

---

## Task 2: Implement `delete_file`

**Files:**
- Modify: `pal/tools/vault.py` (append `DeleteFile` class after `MoveFile`)
- Modify: `tests/test_tools_vault.py` (append four new tests)

- [ ] **Step 1: Add failing tests for `DeleteFile`**

Append to `tests/test_tools_vault.py` after the existing `MoveFile` tests:

```python
# --- DeleteFile (atomic git rm, surfaces reindex failure) ---

async def test_delete_file_removes_file_via_git_rm_and_commits(tmp_path):
    """Happy path: file removed via git_rm, committed, reindex triggered, JSON ok."""
    from pal.tools.vault import DeleteFile

    (tmp_path / "old.md").write_text("---\ntitle: Old\n---\n\nbody", encoding="utf-8")
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    wiki = MagicMock()
    wiki.git_rm = MagicMock()
    wiki.git_commit = MagicMock()

    result = await DeleteFile().run(
        {"path": "old.md"},
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=wiki)),
    )
    parsed = json.loads(result)
    assert parsed["status"] == "deleted"
    assert parsed["path"] == "old.md"
    assert parsed["reindex"] == "ok"
    wiki.git_rm.assert_called_once_with("old.md")
    wiki.git_commit.assert_called_once()
    retrieval.trigger_reindex.assert_awaited_once()


async def test_delete_file_refuses_system_dirs(tmp_path):
    """Refuses paths in underscore-prefixed system directories. File untouched."""
    from pal.tools.vault import DeleteFile

    (tmp_path / "_wisdom").mkdir()
    (tmp_path / "_wisdom" / "rule.md").write_text("body", encoding="utf-8")
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    wiki = MagicMock()
    wiki.git_rm = MagicMock()

    result = await DeleteFile().run(
        {"path": "_wisdom/rule.md"},
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=wiki)),
    )
    assert "system directories" in result.lower()
    assert (tmp_path / "_wisdom" / "rule.md").exists()
    wiki.git_rm.assert_not_called()
    retrieval.trigger_reindex.assert_not_awaited()


async def test_delete_file_refuses_path_escape(tmp_path):
    """Refuses paths that resolve outside the vault. No git_rm call."""
    from pal.tools.vault import DeleteFile

    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    wiki = MagicMock()
    wiki.git_rm = MagicMock()

    result = await DeleteFile().run(
        {"path": "../escape.md"},
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=wiki)),
    )
    assert "escapes outside vault" in result.lower()
    wiki.git_rm.assert_not_called()


async def test_delete_file_surfaces_reindex_failure(tmp_path):
    """Reindex failure: response sets reindex=failed but file is still deleted."""
    from pal.tools.vault import DeleteFile

    (tmp_path / "x.md").write_text("body", encoding="utf-8")
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock(side_effect=RuntimeError("reindex broken"))
    wiki = MagicMock()
    wiki.git_rm = MagicMock()
    wiki.git_commit = MagicMock()

    result = await DeleteFile().run(
        {"path": "x.md"},
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=wiki)),
    )
    parsed = json.loads(result)
    assert parsed["status"] == "deleted"
    assert parsed["reindex"] == "failed"
    wiki.git_rm.assert_called_once_with("x.md")
    wiki.git_commit.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_vault.py -v -k delete_file
```

Expected: 4 failures with `ImportError: cannot import name 'DeleteFile'`.

- [ ] **Step 3: Implement `DeleteFile`**

Append to `pal/tools/vault.py` after `MoveFile`:

```python
# ---------------------------------------------------------------------------
# DeleteFile
# ---------------------------------------------------------------------------

class DeleteFile(Tool):
    """Delete a vault file. Atomic git rm. Reversible via git history."""

    name = "delete_file"
    description = (
        "Delete a vault file. Stages the removal atomically via git rm and commits. "
        "Recoverable from git history with `git revert`. Refuses underscore-prefixed "
        "system directories (_wisdom, _learning, _config, _channels, _profile). "
        "Triggers reindex to remove the file from the embedding store. Reports if "
        "reindex fails so the caller knows the embedding store is temporarily stale."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Path relative to vault root (e.g. 'Hardware/old-article.md'). "
                    "Must already exist. Must not be in a system directory."
                ),
            },
        },
        "required": ["path"],
    }
    requires = ("config",)

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        path = args.get("path", "")
        if not path:
            return "Error: 'path' parameter is required."

        if _is_system_path(path):
            return f"Error: writing to system directories is not allowed: {path}"

        vault = ctx.agent.config.vault_path.resolve()
        resolved = _resolve_safe(vault, path)
        if resolved is None:
            return f"Error: path escapes outside vault: {path}"
        if not resolved.exists():
            return f"Error: file does not exist: {path}"

        wiki = getattr(ctx.agent, "wiki", None)
        if wiki is None:
            return "Error: write operations are not available (no wiki manager)."

        try:
            wiki.git_rm(path)
        except Exception as exc:
            return f"Error: git rm failed: {exc}"

        wiki.git_commit(f"Delete {path} via chat")

        reindex_status = "ok"
        retrieval = getattr(ctx.agent, "retrieval", None)
        if retrieval is not None:
            try:
                await retrieval.trigger_reindex(paths=[str(resolved)])
            except Exception as exc:
                logger.warning("reindex trigger failed after delete_file: %s", exc)
                reindex_status = "failed"

        return json.dumps({
            "status": "deleted",
            "path": path,
            "reindex": reindex_status,
        })
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_vault.py -v -k delete_file
```

Expected: 4 PASS.

- [ ] **Step 5: Run the full vault test suite for regressions**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_vault.py -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add pal/tools/vault.py tests/test_tools_vault.py
git commit -m "feat(tools): add delete_file vault tool with atomic git rm"
```

---

## Task 3: Implement `replace_in_file`

**Files:**
- Modify: `pal/tools/vault.py` (append `ReplaceInFile` class after `DeleteFile`)
- Modify: `tests/test_tools_vault.py` (append five new tests)

- [ ] **Step 1: Add failing tests for `ReplaceInFile`**

Append to `tests/test_tools_vault.py`:

```python
# --- ReplaceInFile (body-only, frontmatter preserved, restore on commit failure) ---

async def test_replace_in_file_replaces_in_body_only(tmp_path):
    """Frontmatter containing the same string is preserved; only body is replaced."""
    from pal.tools.vault import ReplaceInFile

    # Frontmatter has 'AI' in tags. Body has 'AI' too. Replace only in body.
    (tmp_path / "x.md").write_text(
        "---\ntitle: X\ntags:\n  - AI\n  - hardware\n---\n\nThis is about AI.",
        encoding="utf-8",
    )
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    wiki = MagicMock()
    wiki.git_commit = MagicMock()

    result = await ReplaceInFile().run(
        {"path": "x.md", "old_string": "AI", "new_string": "ML"},
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=wiki)),
    )
    parsed = json.loads(result)
    assert parsed["status"] == "replaced"
    assert parsed["occurrences"] == 1

    text = (tmp_path / "x.md").read_text(encoding="utf-8")
    assert "tags:" in text and "- AI" in text  # frontmatter preserved
    assert "This is about ML." in text  # body changed
    assert "This is about AI." not in text


async def test_replace_in_file_refuses_non_unique_without_replace_all(tmp_path):
    """Multiple body matches without replace_all returns error mentioning widening."""
    from pal.tools.vault import ReplaceInFile

    (tmp_path / "x.md").write_text(
        "---\ntitle: X\n---\n\nfoo bar foo bar",
        encoding="utf-8",
    )
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    wiki = MagicMock()

    result = await ReplaceInFile().run(
        {"path": "x.md", "old_string": "bar", "new_string": "baz"},
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=wiki)),
    )
    assert "appears" in result.lower()
    assert "widen" in result.lower() or "replace_all" in result.lower()
    text = (tmp_path / "x.md").read_text(encoding="utf-8")
    assert "foo bar foo bar" in text  # unchanged
    wiki.git_commit.assert_not_called()


async def test_replace_in_file_replace_all_only_in_body(tmp_path):
    """replace_all replaces every body occurrence; frontmatter occurrences untouched."""
    from pal.tools.vault import ReplaceInFile

    # 1 occurrence of 'foo' in frontmatter (as a tag), 3 in body
    (tmp_path / "x.md").write_text(
        "---\ntitle: X\ntags:\n  - foo\n---\n\nfoo bar foo bar foo",
        encoding="utf-8",
    )
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    wiki = MagicMock()
    wiki.git_commit = MagicMock()

    result = await ReplaceInFile().run(
        {
            "path": "x.md",
            "old_string": "foo",
            "new_string": "qux",
            "replace_all": True,
        },
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=wiki)),
    )
    parsed = json.loads(result)
    assert parsed["status"] == "replaced"
    assert parsed["occurrences"] == 3  # body only

    text = (tmp_path / "x.md").read_text(encoding="utf-8")
    assert "tags:" in text and "- foo" in text  # frontmatter foo preserved
    assert "qux bar qux bar qux" in text


async def test_replace_in_file_restores_on_commit_failure(tmp_path):
    """git_commit failure restores original body content."""
    from pal.tools.vault import ReplaceInFile

    original = "---\ntitle: X\n---\n\nhello world"
    (tmp_path / "x.md").write_text(original, encoding="utf-8")
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    wiki = MagicMock()
    wiki.git_commit = MagicMock(side_effect=RuntimeError("git locked"))

    result = await ReplaceInFile().run(
        {"path": "x.md", "old_string": "hello", "new_string": "goodbye"},
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=wiki)),
    )
    assert "git commit failed" in result.lower()
    text = (tmp_path / "x.md").read_text(encoding="utf-8")
    # Original body is restored (frontmatter format may normalize but content preserved)
    assert "hello world" in text
    assert "goodbye" not in text


async def test_replace_in_file_empty_new_string_deletes_match(tmp_path):
    """Empty new_string deletes the matched content."""
    from pal.tools.vault import ReplaceInFile

    (tmp_path / "x.md").write_text(
        "---\ntitle: X\n---\n\nkeep this DELETE_ME and this",
        encoding="utf-8",
    )
    retrieval = MagicMock()
    retrieval.trigger_reindex = AsyncMock()
    wiki = MagicMock()
    wiki.git_commit = MagicMock()

    result = await ReplaceInFile().run(
        {"path": "x.md", "old_string": " DELETE_ME", "new_string": ""},
        _ctx(_Agent(tmp_path, retrieval=retrieval, wiki=wiki)),
    )
    parsed = json.loads(result)
    assert parsed["status"] == "replaced"
    text = (tmp_path / "x.md").read_text(encoding="utf-8")
    assert "keep this and this" in text
    assert "DELETE_ME" not in text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_vault.py -v -k replace_in_file
```

Expected: 5 failures with `ImportError: cannot import name 'ReplaceInFile'`.

- [ ] **Step 3: Implement `ReplaceInFile`**

Append to `pal/tools/vault.py` after `DeleteFile`:

```python
# ---------------------------------------------------------------------------
# ReplaceInFile
# ---------------------------------------------------------------------------

class ReplaceInFile(Tool):
    """Replace exact string match in body of a vault file. Frontmatter preserved."""

    name = "replace_in_file"
    description = (
        "Replace an exact string match in the body of an existing vault file. "
        "Frontmatter is parsed and reattached unchanged; this tool does not modify "
        "YAML metadata (use the existing edit_file if a frontmatter rewrite is "
        "genuinely needed). Whitespace-sensitive. Requires old_string to be unique "
        "in the body unless replace_all is true. Useful for targeted edits without "
        "rewriting the whole body, including appending content (use the trailing "
        "portion of the body as old_string and the same trailing portion plus your "
        "new content as new_string)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Path relative to vault root. Must already exist. Must not be in "
                    "a system directory."
                ),
            },
            "old_string": {
                "type": "string",
                "description": (
                    "Exact string to find in the body. Must appear in the body. Must "
                    "be unique unless replace_all is true. Whitespace-sensitive (preserve "
                    "indentation and newlines exactly). To make a non-unique match unique, "
                    "widen old_string to include surrounding lines."
                ),
            },
            "new_string": {
                "type": "string",
                "description": (
                    "Replacement string. Empty string deletes the matched content."
                ),
            },
            "replace_all": {
                "type": "boolean",
                "description": (
                    "If true, replace every occurrence of old_string in the body. If "
                    "false (default), require old_string to be unique and replace one "
                    "occurrence."
                ),
                "default": False,
            },
        },
        "required": ["path", "old_string", "new_string"],
    }
    requires = ("config",)

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        path = args.get("path", "")
        old_string = args.get("old_string", "")
        new_string = args.get("new_string")
        replace_all = bool(args.get("replace_all", False))

        if not path:
            return "Error: 'path' parameter is required."
        if not old_string:
            return "Error: 'old_string' parameter is required."
        if new_string is None:
            return "Error: 'new_string' parameter is required."

        if _is_system_path(path):
            return f"Error: writing to system directories is not allowed: {path}"

        vault = ctx.agent.config.vault_path.resolve()
        resolved = _resolve_safe(vault, path)
        if resolved is None:
            return f"Error: path escapes outside vault: {path}"
        if not resolved.exists():
            return f"Error: file does not exist: {path}"

        wiki = getattr(ctx.agent, "wiki", None)
        if wiki is None:
            return "Error: write operations are not available (no wiki manager)."

        from agent_core.utils.frontmatter import parse_frontmatter, serialize_frontmatter

        original_text = resolved.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(original_text)
        original_body = body

        count = body.count(old_string)
        if count == 0:
            return f"Error: old_string not found in body of {path}"
        if count > 1 and not replace_all:
            return (
                f"Error: old_string appears {count} times in body of {path}; "
                f"pass replace_all=true, or widen old_string to include surrounding "
                f"lines until it is unique in the body."
            )

        if old_string == new_string:
            return json.dumps({
                "status": "replaced",
                "path": path,
                "occurrences": 0,
                "reindex": "ok",
                "note": "no-op (old_string equals new_string)",
            })

        if replace_all:
            new_body = body.replace(old_string, new_string)
            occurrences = count
        else:
            new_body = body.replace(old_string, new_string, 1)
            occurrences = 1

        resolved.write_text(serialize_frontmatter(meta, new_body), encoding="utf-8")

        try:
            wiki.git_commit(f"Edit {path} via chat (replace_in_file)")
        except Exception as exc:
            # Restore original content
            resolved.write_text(serialize_frontmatter(meta, original_body), encoding="utf-8")
            return f"Error: git commit failed; original content restored: {exc}"

        reindex_status = "ok"
        retrieval = getattr(ctx.agent, "retrieval", None)
        if retrieval is not None:
            try:
                await retrieval.trigger_reindex(paths=[str(resolved)])
            except Exception as exc:
                logger.warning("reindex trigger failed after replace_in_file: %s", exc)
                reindex_status = "failed"

        return json.dumps({
            "status": "replaced",
            "path": path,
            "occurrences": occurrences,
            "reindex": reindex_status,
        })
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_vault.py -v -k replace_in_file
```

Expected: 5 PASS.

- [ ] **Step 5: Run the full vault test suite**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_vault.py -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add pal/tools/vault.py tests/test_tools_vault.py
git commit -m "feat(tools): add replace_in_file vault tool (body-only, frontmatter preserved)"
```

---

## Task 4: Tighten `edit_file` description

**Files:**
- Modify: `pal/tools/vault.py` (rewrite `EditFile.description`)
- Modify: `tests/test_tools_vault.py` (add one regression test)

- [ ] **Step 1: Add a failing regression test**

Append to `tests/test_tools_vault.py`:

```python
# --- EditFile description rewrite (regression guard) ---

def test_edit_file_description_mentions_replace_in_file():
    """The edit_file description must redirect targeted edits to replace_in_file."""
    from pal.tools.vault import EditFile
    desc = EditFile.description
    assert "replace_in_file" in desc
```

This is a synchronous test (no async). It just imports the class and checks the description string.

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_vault.py::test_edit_file_description_mentions_replace_in_file -v
```

Expected: FAIL because the current description doesn't mention `replace_in_file`.

- [ ] **Step 3: Rewrite `EditFile.description`**

In `pal/tools/vault.py`, find the existing `EditFile` class. Replace its `description` string with:

```python
    description = (
        "Rewrite the entire body of an existing vault file. Preserves frontmatter "
        "(title, tags). Use ONLY for structural overhauls where most of the body is "
        "being replaced (e.g., reorganizing sections, swapping a draft for a final "
        "version). For targeted changes (typo fix, link update, single-line edit, "
        "adding a paragraph), use replace_in_file instead. The cost difference is "
        "significant: edit_file requires retransmitting the entire body; "
        "replace_in_file only the changed strings."
    )
```

Do NOT change anything else about `EditFile` (signature, behavior, parameters all stay).

- [ ] **Step 4: Run the regression test**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_vault.py::test_edit_file_description_mentions_replace_in_file -v
```

Expected: PASS.

- [ ] **Step 5: Run the full vault test suite**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_vault.py -v
```

Expected: all green. Existing `EditFile` tests unaffected because behavior didn't change.

- [ ] **Step 6: Commit**

```bash
git add pal/tools/vault.py tests/test_tools_vault.py
git commit -m "feat(tools): tighten edit_file description to redirect targeted edits"
```

---

## Task 5: Register the two new tools with the agent

**Files:**
- Modify: `pal/tools/__init__.py` (add two exports)
- Modify: `pal/agent.py` (add to `PALAgent.tools` list and to imports)

- [ ] **Step 1: Update `pal/tools/__init__.py`**

Open the file. Find the existing import line for vault tools (currently exports `EditFile`, `CreateFile`, `MoveFile`). Extend the import and `__all__` to include `DeleteFile` and `ReplaceInFile`. Maintain alphabetical order.

```python
from pal.tools.vault import (
    CreateFile,
    DeleteFile,
    EditFile,
    MoveFile,
    ReplaceInFile,
)

__all__ = [
    ...
    "CreateFile",
    "DeleteFile",
    "EditFile",
    "MoveFile",
    "ReplaceInFile",
    ...
]
```

- [ ] **Step 2: Update `pal/agent.py`**

Find the multi-name import block from `pal.tools` and the `PALAgent.tools` class attribute. Add `DeleteFile` and `ReplaceInFile` to the import block alphabetically. Add the two instances to the `PALAgent.tools` list, grouped near the existing `EditFile`, `CreateFile`, `MoveFile` instances:

```python
from pal.tools import (
    ...
    DeleteFile,
    ...
    ReplaceInFile,
    ...
)

# in PALAgent.tools list, near other vault-write tools:
tools = [
    ...
    EditFile(),
    DeleteFile(),
    ReplaceInFile(),
    CreateFile(),
    MoveFile(),
    ...
]
```

(Adapt grouping to whatever the existing list shape is.)

- [ ] **Step 3: Run the narrowed test suite**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/ \
    --ignore=tests/test_chat_research_integration.py \
    --ignore=tests/test_client.py \
    --ignore=tests/test_daemon.py \
    --ignore=tests/test_integration.py \
    --ignore=tests/test_prompt_injection.py \
    -q 2>&1 | tail -5
```

Expected: all green.

- [ ] **Step 4: Smoke check the registered tool list**

```bash
cd /home/edible/Projects/PAL && .venv/bin/python -c "
from pal.agent import PALAgent
names = [getattr(t, 'name', None) for t in PALAgent.tools]
for n in ['delete_file', 'replace_in_file']:
    print(f'{n} present: {n in names}')
"
```

Expected: both `True`.

- [ ] **Step 5: Smoke check `to_openai_schema()` for both new tools**

```bash
cd /home/edible/Projects/PAL && .venv/bin/python -c "
from pal.tools.vault import DeleteFile, ReplaceInFile
for cls in [DeleteFile, ReplaceInFile]:
    schema = cls.to_openai_schema()
    print(f'{cls.__name__}: {schema[\"function\"][\"name\"]}')
"
```

Expected: prints both names with no AttributeError. (Catches the `parameters`-class-var-vs-`schema`-property pitfall surfaced in the empty-URL backfill execution.)

- [ ] **Step 6: Commit**

```bash
git add pal/tools/__init__.py pal/agent.py
git commit -m "feat(agent): register delete_file and replace_in_file tools"
```

---

## Task 6: Final verification

**Files (read-only):** none modified.

- [ ] **Step 1: Run the full narrowed test suite**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/ \
    --ignore=tests/test_chat_research_integration.py \
    --ignore=tests/test_client.py \
    --ignore=tests/test_daemon.py \
    --ignore=tests/test_integration.py \
    --ignore=tests/test_prompt_injection.py \
    -v 2>&1 | tail -10
```

Expected: all green. Expected new test count is +10 over the previous baseline (4 delete_file + 5 replace_in_file + 1 edit_file description regression).

- [ ] **Step 2: Sanity-check the descriptions read unambiguously**

Read each of these descriptions out loud. They should each have one clear job, and nothing in them should plausibly apply to a different tool's job:

- `delete_file`: "Delete a vault file..." Only one job.
- `replace_in_file`: "Replace an exact string match in the body..." Body-only is explicit. Append guidance is in the description for the LLM to use the tool for that case via the trailing-anchor pattern.
- `edit_file` (rewritten): "Rewrite the entire body... Use ONLY for structural overhauls... For targeted changes use replace_in_file." Explicit redirect in place.

If anything reads ambiguously (e.g., a description that could apply to two tools), tighten it before merging. The 18-min-loop incident is the precedent for taking this seriously.

- [ ] **Step 3: No commit (verification only)**

The plan is implementation-complete after this step. Operational use is the user invoking the new tools via natural-language prompts in chat ("delete that contaminated template", "fix the typo in the third paragraph of arm-architecture", etc.).

---

## Self-review notes (already applied during drafting)

- **Spec coverage:** every spec section maps to a task. Path safety, git-commit/reindex, encoding all uniformly applied across Tasks 2-3. Task 4 covers the `edit_file` description rewrite. Task 5 covers registration. Task 6 verifies. The `to_openai_schema()` smoke check in Task 5 catches the parameters-class-var-vs-schema-property pitfall.
- **Placeholder scan:** all code blocks are concrete. Task 1 step 3 has conditional behavior (add `git_rm` if missing) but with concrete code for both branches.
- **Type consistency:** `DeleteFile` and `ReplaceInFile` used consistently throughout. Tool names `delete_file` and `replace_in_file` consistent. JSON return shape consistent (`status`, `path`, `reindex`, plus tool-specific fields).
- **Panel feedback applied:** atomic `git rm` for delete (Expert 3), restore-on-commit-failure for replace (Expert 3), reindex-failure surfacing in JSON (Expert 3), body-only replace via parse_frontmatter (Expert 3), tightened uniqueness error mentioning widening (Expert 2), dropped "Same shape as Claude Code's Edit tool" prose (Expert 2), explicit `edit_file` redirect to `replace_in_file` (Expert 2 and Expert 4), UTF-8 encoding explicit (Expert 1), tests assert file state (Expert 1).
- **No em dashes** in the document.
