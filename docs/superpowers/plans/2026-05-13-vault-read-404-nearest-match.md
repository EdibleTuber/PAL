# Vault read 404 nearest-match -- Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Append "Did you mean: ..." suggestions to 404 errors across 8 vault read/write tools (5 in agent_core, 3 in PAL), using a shared `suggest_nearest_paths` helper that walks the vault and scores candidates with stdlib `difflib`. Closes PAL's friction ask #2 from the path-determinism feedback.

**Architecture:** Two helpers in `agent_core/tools/_shell_helpers.py`. `suggest_nearest_paths` does the filesystem walk + difflib scoring with system-path filtering and a `.md` extension filter. `format_not_found_with_suggestions` builds the final error string, appending a single-line "Did you mean: a.md, b.md" only when at least one candidate crosses the 0.6 score cutoff. Eight tool call sites swap their existing 404 returns for calls to the formatter.

**Tech Stack:** Python 3.12 stdlib (`difflib`, `pathlib`), pytest, agent_core (cross-repo), PAL daemon.

**Cross-repo note:** Tasks 1-3 + Task 5 modify `/home/edible/Projects/agent_core`. Task 4 modifies `/home/edible/Projects/PAL`. PAL imports the helper via the existing editable install. Per `feedback_agent_core_version_bump` memory, the agent_core version bump in Task 5 is what signals the server to refresh its wheel.

**Spec:** `docs/superpowers/specs/2026-05-13-vault-read-404-nearest-match-design.md`

---

## File Structure

**agent_core repo (`/home/edible/Projects/agent_core/`):**
- Modify: `agent_core/tools/_shell_helpers.py` -- add `suggest_nearest_paths` and `format_not_found_with_suggestions`.
- Modify: `tests/test_tools_shell_helpers.py` -- 8 unit tests for the new helpers.
- Modify: `agent_core/tools/_shell.py` -- update Cat 404, `_read_safe` 404 (covers Head/Tail/ReadLines/Find), Grep 404.
- Modify: `tests/test_tools_shell.py` -- 6 integration tests for tool-level 404 behavior with and without nearby matches.
- Modify: `pyproject.toml` -- version bump 1.1.1 → 1.1.2.

**PAL repo (`/home/edible/Projects/PAL/`):**
- Modify: `pal/tools/vault.py` -- update delete_file, move_file, replace_in_file 404 returns.
- Modify: `tests/test_tools_vault.py` -- 3 integration tests for PAL-side 404 behavior.

---

## Task 1: Add `suggest_nearest_paths` and `format_not_found_with_suggestions` helpers

**Files:**
- Modify: `/home/edible/Projects/agent_core/agent_core/tools/_shell_helpers.py`
- Modify: `/home/edible/Projects/agent_core/tests/test_tools_shell_helpers.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_tools_shell_helpers.py`:

```python
from agent_core.tools._shell_helpers import (
    suggest_nearest_paths,
    format_not_found_with_suggestions,
)


def test_suggest_nearest_paths_finds_typo(tmp_path):
    """Underscore-vs-hyphen typo in directory name finds the real path."""
    (tmp_path / "Software-Development").mkdir()
    (tmp_path / "Software-Development" / "vibe-coding.md").write_text("body")
    matches = suggest_nearest_paths(tmp_path, "Software_Development/vibe-coding.md")
    assert "Software-Development/vibe-coding.md" in matches


def test_suggest_nearest_paths_respects_score_cutoff(tmp_path):
    """Totally unrelated query returns [] (nothing crosses 0.6 cutoff)."""
    (tmp_path / "foo.md").write_text("x")
    (tmp_path / "bar.md").write_text("x")
    assert suggest_nearest_paths(tmp_path, "totally-unrelated-xyz.md") == []


def test_suggest_nearest_paths_respects_max(tmp_path):
    """When many close matches exist, result is capped at max_suggestions."""
    for i in range(10):
        (tmp_path / f"vibe-coding-{i}.md").write_text("x")
    matches = suggest_nearest_paths(tmp_path, "vibe-coding.md", max_suggestions=3)
    assert len(matches) == 3


def test_suggest_nearest_paths_skips_system_paths(tmp_path):
    """Files under _archive (or any _-prefixed segment) are never suggested."""
    (tmp_path / "_archive").mkdir()
    (tmp_path / "_archive" / "foo.md").write_text("x")
    (tmp_path / "foo.md").write_text("x")
    matches = suggest_nearest_paths(tmp_path, "fooo.md")
    assert "foo.md" in matches
    assert all("_archive" not in m for m in matches)


def test_suggest_nearest_paths_skips_missing_path_itself(tmp_path):
    """Defensive: if the missing path happens to be in the candidate scan
    (race condition), it is not suggested as a match for itself."""
    (tmp_path / "foo.md").write_text("x")
    # Query for foo.md; even though it exists, it shouldn't suggest itself.
    matches = suggest_nearest_paths(tmp_path, "foo.md")
    assert "foo.md" not in matches


def test_suggest_nearest_paths_empty_vault(tmp_path):
    """No .md files in vault returns []."""
    assert suggest_nearest_paths(tmp_path, "anything.md") == []


def test_format_not_found_with_suggestions_appends_when_matches(tmp_path):
    """Formatter produces base + newline + 'Did you mean: ...' when matches exist."""
    (tmp_path / "foo.md").write_text("x")
    result = format_not_found_with_suggestions(
        tmp_path, "fooo.md", "File not found: fooo.md"
    )
    assert result.startswith("File not found: fooo.md")
    assert "\nDid you mean: " in result
    assert "foo.md" in result


def test_format_not_found_with_suggestions_unchanged_when_no_matches(tmp_path):
    """Formatter returns base verbatim when suggestions list is empty."""
    result = format_not_found_with_suggestions(
        tmp_path, "anything.md", "File not found: anything.md"
    )
    assert result == "File not found: anything.md"
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/agent_core && pytest tests/test_tools_shell_helpers.py -k "suggest_nearest or format_not_found" -v
```

Expected: FAIL with `ImportError: cannot import name 'suggest_nearest_paths' from 'agent_core.tools._shell_helpers'`.

- [ ] **Step 3: Add the helpers to `_shell_helpers.py`**

At the end of `/home/edible/Projects/agent_core/agent_core/tools/_shell_helpers.py`, append:

```python
import difflib


def suggest_nearest_paths(
    vault_path: Path,
    missing_path: str,
    *,
    max_suggestions: int = 3,
    score_cutoff: float = 0.6,
) -> list[str]:
    """Return up to max_suggestions vault-relative paths similar to missing_path.

    Walks the vault for *.md files, scores each against the missing path
    via difflib.SequenceMatcher. Matches against the FULL vault-relative
    path (not just the stem) so directory-level typos like
    `Software_Development/` vs `Software-Development/` are caught.

    Skips paths whose any segment starts with `_` (matches is_system_path).
    Skips the missing path itself (defensive; could occur in a race).
    Returns [] when no candidate meets score_cutoff or the vault is empty.

    score_cutoff=0.6 is difflib's default; catches typos and word-order
    swaps but rejects unrelated names.
    """
    try:
        vault_resolved = vault_path.resolve()
    except (OSError, ValueError):
        return []

    candidates: list[str] = []
    for path in vault_resolved.rglob("*.md"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(vault_resolved)
        except ValueError:
            continue
        rel_str = str(rel)
        # Skip system paths (any segment starts with `_`).
        if any(part.startswith("_") for part in rel.parts):
            continue
        # Defensive: never suggest the path the caller said was missing.
        if rel_str == missing_path:
            continue
        candidates.append(rel_str)

    if not candidates:
        return []

    return difflib.get_close_matches(
        missing_path, candidates, n=max_suggestions, cutoff=score_cutoff
    )


def format_not_found_with_suggestions(
    vault_path: Path,
    missing_path: str,
    base_message: str,
) -> str:
    """Build the 404 error string, appending 'Did you mean: ...' when matches exist.

    Returns base_message verbatim when suggest_nearest_paths returns [].
    """
    suggestions = suggest_nearest_paths(vault_path, missing_path)
    if not suggestions:
        return base_message
    return f"{base_message}\nDid you mean: {', '.join(suggestions)}"
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
cd /home/edible/Projects/agent_core && pytest tests/test_tools_shell_helpers.py -v
```

Expected: all 8 new tests pass; pre-existing helper tests still pass.

- [ ] **Step 5: Commit**

```bash
cd /home/edible/Projects/agent_core && git add agent_core/tools/_shell_helpers.py tests/test_tools_shell_helpers.py && git commit -m "$(cat <<'EOF'
feat(shell): add suggest_nearest_paths + format_not_found_with_suggestions

Shared helpers in agent_core/tools/_shell_helpers.py. The first walks
the vault for *.md files and uses stdlib difflib to score candidates
against a missing path; the second wraps it to append a "Did you mean:"
line to a base 404 message when matches exist (returns base verbatim
when no candidates cross the 0.6 score cutoff).

Match strategy: full vault-relative path comparison so directory-level
typos (Software_Development/ vs Software-Development/) are caught.
System paths (segments starting with _) are excluded from candidates.

Closes friction ask #2 from PAL's path-determinism feedback (memory:
project_pal_path_determinism). Will be wired into Cat, _read_safe-using
tools, Grep, and PAL's delete_file/move_file/replace_in_file in the
next two tasks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Critical correctness notes

- The helper accepts `vault_path: Path` (a `pathlib.Path`, not a string). Callers pass `ctx.agent.config.vault_path` directly.
- `difflib.get_close_matches` already returns at most `n` results sorted by score descending. No additional sort/slice needed.
- The `.md` extension filter is hard-coded for v1 per the spec. If a tool ever needs a different extension, broaden then.
- Real `…` Unicode character is NOT used in this code (unlike the search_vault truncation work). The suggestion separator is a literal `, `.

---

## Task 2: Wire suggestions into agent_core shell tools

**Files:**
- Modify: `/home/edible/Projects/agent_core/agent_core/tools/_shell.py` (3 call sites: Cat, `_read_safe`, Grep)
- Modify: `/home/edible/Projects/agent_core/tests/test_tools_shell.py` (6 integration tests)

- [ ] **Step 1: Write failing tests**

Append to `/home/edible/Projects/agent_core/tests/test_tools_shell.py`:

```python
async def test_cat_404_includes_suggestions_when_similar_path_exists(tmp_path):
    """Cat's 404 error gets a 'Did you mean: ...' line when a near-match exists."""
    (tmp_path / "foo.md").write_text("body")
    agent = _agent_with_vault(tmp_path)
    result = await Cat().run({"path": "fooo.md"}, _ctx(agent))
    assert "File not found: fooo.md" in result
    assert "Did you mean: " in result
    assert "foo.md" in result


async def test_cat_404_bare_when_no_similar_path(tmp_path):
    """Empty vault: Cat's 404 has no Did-you-mean tail."""
    agent = _agent_with_vault(tmp_path)
    result = await Cat().run({"path": "anything.md"}, _ctx(agent))
    assert result == "File not found: anything.md"


async def test_head_404_includes_suggestions(tmp_path):
    """Head 404 (via _read_safe) gets the suggestion treatment too."""
    (tmp_path / "vibe-coding.md").write_text("body")
    agent = _agent_with_vault(tmp_path)
    result = await Head().run({"path": "vibe_coding.md"}, _ctx(agent))
    assert "File not found: vibe_coding.md" in result
    assert "Did you mean: " in result
    assert "vibe-coding.md" in result


async def test_tail_404_includes_suggestions(tmp_path):
    """Tail 404 (via _read_safe) gets the suggestion treatment."""
    (tmp_path / "notes.md").write_text("body")
    agent = _agent_with_vault(tmp_path)
    result = await Tail().run({"path": "nottes.md"}, _ctx(agent))
    assert "File not found: nottes.md" in result
    assert "notes.md" in result


async def test_read_lines_404_includes_suggestions(tmp_path):
    """ReadLines 404 (via _read_safe) gets the suggestion treatment."""
    (tmp_path / "foo.md").write_text("line1\nline2")
    agent = _agent_with_vault(tmp_path)
    result = await ReadLines().run(
        {"path": "fooo.md", "start": 1, "end": 1}, _ctx(agent)
    )
    assert "File not found: fooo.md" in result
    assert "foo.md" in result


async def test_grep_404_on_missing_file_includes_suggestions(tmp_path):
    """Grep 404 when path arg is a missing file gets the suggestion treatment."""
    (tmp_path / "foo.md").write_text("hello world")
    agent = _agent_with_vault(tmp_path)
    result = await Grep().run(
        {"pattern": "hello", "path": "fooo.md"}, _ctx(agent)
    )
    assert "Path not found: fooo.md" in result
    assert "Did you mean: " in result
    assert "foo.md" in result
```

Note: tests assume `_agent_with_vault(tmp_path)` and `_ctx(agent)` test helpers already exist in this file (they're used by the pre-existing tool tests). Import `Cat`, `Head`, `Tail`, `ReadLines`, `Grep` from `agent_core.tools._shell` if not already imported. If `_agent_with_vault` doesn't exist verbatim, follow the same pattern other tests use (typically a `MagicMock` with `config.vault_path` set).

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/agent_core && pytest tests/test_tools_shell.py -k "404_includes_suggestions or 404_bare" -v
```

Expected: most fail (the suggestion-bearing assertions fail because the tool still returns bare 404 strings). The `test_cat_404_bare_when_no_similar_path` test passes by coincidence today since today's behavior IS bare.

- [ ] **Step 3: Update Cat in `_shell.py`**

In `/home/edible/Projects/agent_core/agent_core/tools/_shell.py`, change the import block at the top to include the new helper:

```python
from agent_core.tools._shell_helpers import (
    cap_output,
    format_not_found_with_suggestions,
    is_system_path,
    resolve_safe,
)
```

Then change `Cat.run` (around line 32). Find:

```python
        if not resolved.exists():
            return f"File not found: {path}"
```

Replace with:

```python
        if not resolved.exists():
            return format_not_found_with_suggestions(
                ctx.agent.config.vault_path, path, f"File not found: {path}"
            )
```

- [ ] **Step 4: Update `_read_safe` (shared by Head/Tail/ReadLines/Find)**

In `_shell.py`, around line 42-56, change `_read_safe`. The current shape:

```python
def _read_safe(args, vault_path):
    """Resolve and validate a path arg; return (resolved, error_str_or_none)."""
    path = (args.get("path") or "").strip()
    if not path:
        return None, "Error: 'path' parameter is required."
    if is_system_path(path):
        return None, f"Error: system path is not readable: {path}"
    resolved = resolve_safe(vault_path, path)
    if resolved is None:
        return None, f"Error: path escapes outside vault: {path}"
    if not resolved.exists():
        return None, f"File not found: {path}"
    if not resolved.is_file():
        return None, f"Not a file: {path}"
    return resolved, None
```

Change the `not resolved.exists()` branch to:

```python
    if not resolved.exists():
        return None, format_not_found_with_suggestions(
            vault_path, path, f"File not found: {path}"
        )
```

- [ ] **Step 5: Update Grep in `_shell.py`**

In `Grep.run` (around line 211-212), find:

```python
        if not resolved.exists():
            return f"Path not found: {path or '/'}"
```

Replace with:

```python
        if not resolved.exists():
            return format_not_found_with_suggestions(
                vault, path or "", f"Path not found: {path or '/'}"
            )
```

Note: passes the user-supplied `path` (or empty string for vault-root case) to the helper. The empty-path case won't produce useful suggestions and the helper will return `[]` (since the empty string compares poorly against real candidates), so the error stays bare. That's the right behavior.

- [ ] **Step 6: Run tests, verify they pass**

```bash
cd /home/edible/Projects/agent_core && pytest tests/test_tools_shell.py -v
```

Expected: all new tests pass; existing tool tests still pass.

- [ ] **Step 7: Commit**

```bash
cd /home/edible/Projects/agent_core && git add agent_core/tools/_shell.py tests/test_tools_shell.py && git commit -m "$(cat <<'EOF'
feat(shell): append nearest-match suggestions to 404 errors

Wires the format_not_found_with_suggestions helper into three call sites
in agent_core/tools/_shell.py:
- Cat.run line 32 (single-file read 404)
- _read_safe (shared by Head, Tail, ReadLines, Find) line 53
- Grep.run line 212 (when path arg resolves to a missing file)

Six integration tests added covering cat, head, tail, read_lines, grep
plus the bare-404-when-no-matches case for cat. Existing happy-path tests
unaffected.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Critical correctness notes

- `_read_safe` is called by multiple tools (Head, Tail, ReadLines, Find). One change there covers all four. Find lives at `tools/_shell.py:256` and uses `_read_safe`; its tests are already covered by the `_read_safe` change.
- Pre-existing tests that asserted exact `"File not found: ..."` string equality will need updating. Find them in step 6 (some may have started with `result == "File not found: x"`). Update those to use `result.startswith(...)` or `"File not found" in result` to tolerate the optional suggestion tail.
- The `vault` variable used in the Grep call site is the unresolved vault path. That's fine -- `suggest_nearest_paths` resolves it internally.

---

## Task 3: Wire suggestions into PAL vault write tools

**Files:**
- Modify: `/home/edible/Projects/PAL/pal/tools/vault.py` (3 call sites)
- Modify: `/home/edible/Projects/PAL/tests/test_tools_vault.py` (3 integration tests)

- [ ] **Step 1: Write failing tests**

Append to `/home/edible/Projects/PAL/tests/test_tools_vault.py`:

```python
import pytest
from unittest.mock import MagicMock


@pytest.mark.asyncio
async def test_delete_file_404_includes_suggestions(tmp_path):
    """delete_file's 'file does not exist' error gets a Did-you-mean tail."""
    from pal.tools.vault import DeleteFile
    (tmp_path / "foo.md").write_text("body")
    agent = MagicMock()
    agent.config.vault_path = tmp_path
    agent.wiki = MagicMock()
    ctx = MagicMock()
    ctx.agent = agent
    result = await DeleteFile().run({"path": "fooo.md"}, ctx)
    assert "file does not exist: fooo.md" in result
    assert "Did you mean: " in result
    assert "foo.md" in result


@pytest.mark.asyncio
async def test_move_file_404_on_missing_source_includes_suggestions(tmp_path):
    """move_file's 'src file does not exist' error gets a Did-you-mean tail."""
    from pal.tools.vault import MoveFile
    (tmp_path / "foo.md").write_text("body")
    agent = MagicMock()
    agent.config.vault_path = tmp_path
    agent.reorganizer = MagicMock()
    ctx = MagicMock()
    ctx.agent = agent
    result = await MoveFile().run(
        {"src": "fooo.md", "dst": "other.md"}, ctx
    )
    assert "file does not exist: fooo.md" in result
    assert "Did you mean: " in result
    assert "foo.md" in result


@pytest.mark.asyncio
async def test_replace_in_file_404_includes_suggestions(tmp_path):
    """replace_in_file's 'file does not exist' error gets a Did-you-mean tail."""
    from pal.tools.vault import ReplaceInFile
    (tmp_path / "foo.md").write_text("body")
    agent = MagicMock()
    agent.config.vault_path = tmp_path
    agent.wiki = MagicMock()
    ctx = MagicMock()
    ctx.agent = agent
    result = await ReplaceInFile().run(
        {"path": "fooo.md", "old_string": "a", "new_string": "b"}, ctx
    )
    assert "file does not exist: fooo.md" in result
    assert "Did you mean: " in result
    assert "foo.md" in result
```

If these tests need different import paths or class names, adapt to match the actual class names in `pal/tools/vault.py` (the names in the existing tests in `test_tools_vault.py` are authoritative).

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_vault.py -k "404_includes_suggestions or 404_on_missing_source" -v
```

Expected: FAIL because the tool currently returns bare error strings.

- [ ] **Step 3: Add the helper import to `pal/tools/vault.py`**

At the top of `/home/edible/Projects/PAL/pal/tools/vault.py`, in the existing import block, add:

```python
from agent_core.tools._shell_helpers import format_not_found_with_suggestions
```

(Place it alongside any other `agent_core.tools._shell_helpers` import. If none exists yet, add it after the other agent_core imports.)

- [ ] **Step 4: Update delete_file's 404 return**

In `pal/tools/vault.py`, around line 316, find:

```python
            return f"Error: file does not exist: {path}"
```

Replace with:

```python
            return format_not_found_with_suggestions(
                ctx.agent.config.vault_path,
                path,
                f"Error: file does not exist: {path}",
            )
```

- [ ] **Step 5: Update move_file's 404 return**

Around line 429 in `pal/tools/vault.py`, find:

```python
            return f"Error: file does not exist: {path}"
```

(There are now two similar lines; use git diff against the original to confirm you're editing the move_file one, not delete_file which you already changed.)

Replace with:

```python
            return format_not_found_with_suggestions(
                ctx.agent.config.vault_path,
                path,
                f"Error: file does not exist: {path}",
            )
```

Note: move_file's parameter name may be `src` rather than `path`; use whatever the existing error string interpolates. Adapt the variable name in the f-string accordingly. The `missing_path` argument to the helper should match what the user passed (typically `src` for move_file).

- [ ] **Step 6: Update replace_in_file's 404 return**

Around line 429 in `pal/tools/vault.py` (the second occurrence). Find:

```python
            return f"Error: file does not exist: {path}"
```

Replace with the same shape as steps 4-5.

- [ ] **Step 7: Run tests, verify they pass**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_tools_vault.py -v
```

Expected: all new tests pass; existing tool tests still pass.

- [ ] **Step 8: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/tools/vault.py tests/test_tools_vault.py && git commit -m "$(cat <<'EOF'
feat(vault): append nearest-match suggestions to vault-write 404 errors

Wires format_not_found_with_suggestions into three PAL vault write
tools: delete_file, move_file, replace_in_file. Each now appends a
"Did you mean: ..." line to its existing "file does not exist" error
when at least one similar path crosses the 0.6 score cutoff.

edit_file is intentionally left alone; its existing "use create_file
for new files" hint is more useful than fuzzy matches for that flow.

Three integration tests added. Existing tests unaffected (they assert
the prefix string with `in`, not strict equality).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Critical correctness notes

- The exact parameter name in move_file might be `src` (not `path`). Read the actual function signature before editing; use the right variable name in the f-string.
- The exact line numbers may have shifted since the spec was written; rely on the surrounding context (the `file does not exist` literal) to find the correct site.
- The existing tests in `test_tools_vault.py` use the actual class names (`DeleteFile`, `MoveFile`, `ReplaceInFile`); confirm the import paths match before running.

---

## Task 4: Bump agent_core version

**Files:**
- Modify: `/home/edible/Projects/agent_core/pyproject.toml`

Per the `feedback_agent_core_version_bump` memory, every behavior change to agent_core needs a version bump so wheel-installed environments pick up the new code.

- [ ] **Step 1: Bump version**

In `/home/edible/Projects/agent_core/pyproject.toml`, change:

```toml
version = "1.1.1"
```

to:

```toml
version = "1.1.2"
```

- [ ] **Step 2: Verify PAL's editable install picks it up**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pip show agent_core | head -3
```

Expected: `Version: 1.1.2`. If it still shows `1.1.1`, force-reinstall:

```bash
cd /home/edible/Projects/PAL && .venv/bin/pip install -e /home/edible/Projects/agent_core --force-reinstall --no-deps
```

- [ ] **Step 3: Spot-check the helpers are visible from PAL's venv**

```bash
cd /home/edible/Projects/PAL && .venv/bin/python -c "
from pathlib import Path
from agent_core.tools._shell_helpers import suggest_nearest_paths, format_not_found_with_suggestions
import tempfile
with tempfile.TemporaryDirectory() as d:
    vault = Path(d)
    (vault / 'foo.md').write_text('body')
    matches = suggest_nearest_paths(vault, 'fooo.md')
    assert 'foo.md' in matches, f'expected foo.md, got {matches}'
    msg = format_not_found_with_suggestions(vault, 'fooo.md', 'File not found: fooo.md')
    assert 'Did you mean: ' in msg, f'expected suggestion tail, got {msg!r}'
print('helpers visible from PAL venv: ok')
"
```

Expected: prints `helpers visible from PAL venv: ok`.

- [ ] **Step 4: Commit**

```bash
cd /home/edible/Projects/agent_core && git add pyproject.toml && git commit -m "$(cat <<'EOF'
chore: bump version to 1.1.2

Signals the suggest_nearest_paths + format_not_found_with_suggestions
addition to wheel-installed environments (production server uses a
regular pip install of agent_core rather than editable). Patch bump
because the change is additive helpers plus four 404-message extensions,
not a breaking API change.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Full-suite regression sweep

**Files:** None modified unless a non-preexisting failure surfaces.

- [ ] **Step 1: Run agent_core suite**

```bash
cd /home/edible/Projects/agent_core && pytest tests/ --ignore=tests/test_converter.py -q 2>&1 | tail -5
```

Expected: all pass. The single `--ignore` matches the pre-existing markitdown environment issue.

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

Expected: all pass. The five `--ignore` flags match the documented pre-existing pal.client collection failures.

- [ ] **Step 3: Diagnose any non-preexisting failures**

The most likely failure class is a test that asserted exact equality on a 404 string (`result == "File not found: x"`). After this work the result may have a `\nDid you mean: ...` tail. Fix shape (not weaken assertions):

```python
# Before (brittle)
assert result == "File not found: foo.md"

# After (tolerant of optional suggestion tail)
assert result.startswith("File not found: foo.md")
```

If the failure is not a 404-string assertion, diagnose at the right layer; don't paper over.

- [ ] **Step 4: Em-dash sweep**

```bash
cd /home/edible/Projects/PAL && git diff main..HEAD | grep -P '[\x{2014}\x{2013}]' || echo "no em dashes in PAL diff"
cd /home/edible/Projects/agent_core && git diff main..HEAD | grep -P '[\x{2014}\x{2013}]' || echo "no em dashes in agent_core diff"
```

Both should print "no em dashes...".

- [ ] **Step 5: Final commit (only if anything was fixed in step 3)**

```bash
git add -- <specific files> && git commit -m "fix(tests): adapt <area> to optional 404 suggestion tail

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
- [ ] Names used in later tasks match earlier tasks (`suggest_nearest_paths`, `format_not_found_with_suggestions`).
- [ ] Cross-repo dependency on agent_core is called out at the top and Task 4 verifies the version bump propagates.
- [ ] The 8 helper unit tests cover typo-finds-match, score cutoff, max suggestions cap, system-path skip, missing-path-itself skip, empty vault, formatter with matches, formatter without matches.
- [ ] The 6 agent_core integration tests cover cat, head, tail, read_lines, grep with suggestions plus cat bare-404 case.
- [ ] The 3 PAL integration tests cover delete_file, move_file, replace_in_file.
- [ ] All commit messages end with the Co-Authored-By line.
- [ ] No em dashes anywhere added (use `--` instead).

## Out of scope

- Directory 404 suggestions (ls, find on directory, grep on directory).
- edit_file 404 enhancement (its "use create_file for new files" hint is better than fuzzy matches).
- Cross-extension fuzziness (asking for `foo.txt` when `foo.md` exists).
- Semantic similarity matching (use search_vault for that).
- Caching candidate lists across calls.
- Prompt updates documenting the convention.
- Retrieval-index-backed suggestions (filesystem walk is fast enough at current vault sizes).
- Server-side deploy (the user handles deploy; the version bump in Task 4 signals when).
