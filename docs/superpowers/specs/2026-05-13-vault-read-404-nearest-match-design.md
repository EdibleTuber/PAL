# Vault read 404 nearest-match suggestions

**Date:** 2026-05-13
**Status:** Design (brainstorm paused, awaiting user review)
**Author:** Brainstormed with Claude
**Audit:** `docs/superpowers/audits/2026-05-11-tool-audit-report.md` (path-determinism cluster, item 2 of 3)
**Cluster predecessor:** `docs/superpowers/specs/2026-05-12-search-vault-json-result-format-design.md` (merged 2026-05-13)

## Problem

PAL's vault read tools (`cat`, `head`, `tail`, `read_lines`, single-file `grep`) and PAL's vault write tools (`delete_file`, `move_file`, `replace_in_file`) return a bare "File not found: {path}" string when given a path that doesn't exist. PAL has no recovery hint; it re-enters the "guessing loop" PAL itself named as friction ask #2 in the path-determinism feedback.

This is the second of three specs in the path-determinism cluster. The first (search_vault JSON migration) gives PAL deterministic paths in retrieval results so it stops needing to guess. This spec catches the remaining case where PAL DOES end up with a stale or typo'd path (from conversation history, from a prior tool result, or from its own reconstruction).

PAL's own asks (verbatim from `project_pal_path_determinism` memory):
> 2. **Batch tool errors (e.g., `propose_compile_batch` "File not found") should include a "Did you mean?" or list of closest matches in the directory.** Today they're "blind" -- they say what failed but not what was actually there. PAL ends up loop-guessing.

This spec implements that recovery hint across single-file read tools and PAL's vault write tools.

## Goals

1. Append "Did you mean: ..." to the existing 404 error message when at least one candidate vault file has a sufficiently-similar path.
2. Use a shared helper so all 8 affected tools render the suggestion identically.
3. No new dependencies (use Python stdlib `difflib`).
4. Performance: one filesystem walk per 404, bounded by vault size. For a personal vault (low thousands of `.md` files), under 100ms.
5. Respect existing path-safety rules: never suggest `_*` system paths or files outside the vault.
6. No-op when the vault has no similar paths (no empty "Did you mean: " line).

## Non-goals

1. **Directory 404 suggestions** (`ls`, `find` on directory, `grep` on directory). Different shape, less common, can be a separate spec if it becomes friction.
2. **`edit_file` enhancement.** It already returns "use create_file for new files," which is a stronger hint than fuzzy matches. Don't replace it.
3. **Cross-extension fuzziness** (asking for `foo.txt` when `foo.md` exists). v1 only considers `*.md`.
4. **Semantic similarity.** If the user asks for `crypto-stuff.md` and the real file is `cryptography-notes.md`, difflib will not find it. PAL should use `search_vault` for that case. This spec is for typo recovery, not semantic recovery.
5. **Caching candidate lists across calls.** One-shot walk per 404 is fine at current vault sizes.
6. **Prompt updates documenting the convention.** PAL understands "did you mean" natively; not load-bearing.
7. **Retrieval-index-backed suggestions.** Considered and rejected during brainstorming: filesystem walk is fast enough for a personal vault and doesn't depend on retrieval being alive or up-to-date.

## Helper API

Lives at `agent_core/tools/_shell_helpers.py` alongside the existing path helpers (`resolve_safe`, `is_system_path`). PAL's vault tools import it from there.

```python
def suggest_nearest_paths(
    vault_path: Path,
    missing_path: str,
    *,
    max_suggestions: int = 3,
    score_cutoff: float = 0.6,
) -> list[str]:
    """Return up to max_suggestions vault-relative paths similar to missing_path.

    Walks the vault for *.md files, scores each against the missing path
    via difflib.SequenceMatcher (matching the FULL vault-relative path, not
    just the stem, so directory-level typos like `Software_Development/`
    vs `Software-Development/` are caught).

    Skips paths whose any segment starts with `_` (matches is_system_path).
    Skips the missing path itself (defensive). Returns [] when no candidate
    meets score_cutoff.

    score_cutoff=0.6 is difflib's default; catches typos and word-order
    swaps but rejects unrelated names.
    """


def format_not_found_with_suggestions(
    vault_path: Path,
    missing_path: str,
    base_message: str,
) -> str:
    """Build the 404 error string, appending 'Did you mean: ...' when matches exist.

    Returns base_message verbatim when suggest_nearest_paths returns [].
    """
```

**Rendered format:**
```
File not found: foo.md
Did you mean: Software-Development/vibe-coding.md, PAL/architecture-overview.md
```

Single line of suggestions, comma-separated, prepended with `\nDid you mean: `.

**Match semantics:**
- Full vault-relative path matching (catches directory typos as well as filename typos).
- `*.md` extension filter for v1.
- Skip `_*` system paths.
- difflib `score_cutoff=0.6` default.
- `max_suggestions=3` default.

## Call sites

### agent_core (`agent_core/tools/_shell.py`)

| Tool | Current line | Current message |
|------|--------------|-----------------|
| `Cat.run` | 32 | `return f"File not found: {path}"` |
| Helper `_read_safe` (Head, Tail, ReadLines, Find) | 53 | `return None, f"File not found: {path}"` |
| `Grep.run` (file mode only) | 212 | `return f"Path not found: {path or '/'}"` |

Each becomes a call to `format_not_found_with_suggestions(ctx.agent.config.vault_path, path, <existing base message>)`.

### PAL (`pal/tools/vault.py`)

| Tool | Current line | Current message |
|------|--------------|-----------------|
| `delete_file` | 316 | `return f"Error: file does not exist: {path}"` |
| `move_file` | 429 | `return f"Error: file does not exist: {path}"` |
| `replace_in_file` | ~429 | `return f"Error: file does not exist: {path}"` |

Each becomes a call to `format_not_found_with_suggestions(vault_path, path, <existing base message>)`. Import from `agent_core.tools._shell_helpers`.

### Deliberately skipped

- `edit_file` (`vault.py:92`) already says "use create_file for new files." More useful than fuzzy match for that case.
- All directory-not-found cases (`ls`, `find` on directory, `grep` on directory).

## Test coverage

### Helper unit tests (`agent_core/tests/test_shell_helpers.py` or extend existing)

- `test_suggest_nearest_paths_finds_typo` -- vault has `Software-Development/vibe-coding.md`, query `Software_Development/vibe-coding.md` returns it as top match.
- `test_suggest_nearest_paths_respects_score_cutoff` -- query for `totally-unrelated.md` against a vault of `foo.md`, `bar.md` returns `[]` (nothing crosses the 0.6 cutoff).
- `test_suggest_nearest_paths_respects_max` -- vault has 10 similar names; result has exactly `max_suggestions` entries.
- `test_suggest_nearest_paths_skips_system_paths` -- vault has `_archive/foo.md` and `foo.md`; query `bar.md` returns `foo.md` only, never the `_archive` one.
- `test_suggest_nearest_paths_skips_missing_path_itself` -- defensive: query path exists in candidate list, helper drops it.
- `test_suggest_nearest_paths_empty_vault` -- no `*.md` files in vault, returns `[]`.
- `test_format_not_found_with_suggestions_appends_when_matches` -- formatter produces `base + "\nDid you mean: ..."` shape.
- `test_format_not_found_with_suggestions_unchanged_when_no_matches` -- formatter returns `base` verbatim when suggestions list is empty.

### agent_core tool integration tests (`agent_core/tests/test_tools_shell.py`)

- `test_cat_404_includes_suggestions_when_similar_path_exists` -- write `foo.md` to a tmp vault, call `Cat.run({"path": "fooo.md"})`, assert result contains "File not found" AND "Did you mean: foo.md".
- `test_cat_404_bare_when_no_similar_path` -- empty tmp vault (no markdown files), call `Cat.run({"path": "anything.md"})`, assert result is exactly `"File not found: anything.md"` with no suggestions line.
- `test_head_404_includes_suggestions` -- same shape via `_read_safe`.
- `test_tail_404_includes_suggestions` -- same.
- `test_read_lines_404_includes_suggestions` -- same.
- `test_grep_404_on_missing_file_includes_suggestions` -- grep with a path arg pointing at a missing file when a similar file exists.

### PAL tool integration tests (`tests/test_tools_vault.py`)

- `test_delete_file_404_includes_suggestions` -- fixture file present, try to delete a typo path, assert suggestions appear.
- `test_move_file_404_on_missing_source_includes_suggestions` -- same shape.
- `test_replace_in_file_404_includes_suggestions` -- same.

## Behavior preservation

- Bare `File not found: {path}` is unchanged when no similar paths exist (avoids polluting the error with empty hints).
- All existing path-safety guards (resolve_safe, is_system_path, system-dir rejection) run BEFORE the suggestion lookup. Suggestions are an enhancement to the failure path, not a new failure path.
- The 32 KB output cap on `cat` doesn't apply to error strings; the suggestion is a few hundred bytes at most.

## Migration / back-compat

- No client API changes. The tool output is still a string, just with an optional appended line.
- agent_core version bump: 1.1.1 → 1.1.2 per the `feedback_agent_core_version_bump` memory.
- No prompt changes (the convention is self-explanatory in the error string).
- Existing tests that assert the exact bare `"File not found: ..."` string will need updating. The suggestion line is appended after a newline, so a `result.startswith("File not found:")` style assertion still passes; only strict equality assertions break.

## Risks

1. **Suggestion misleads PAL when the missing path is genuinely new** (PAL is about to create it but tries cat first by mistake). Mitigation: suggestions are advisory, not authoritative; PAL retains the original "File not found" prefix. Score cutoff prevents nonsense matches.
2. **Performance on growing vault.** A vault of 10k `.md` files takes ~50-100ms to walk and score; acceptable. If the vault reaches 50k+, switch to retrieval-index lookup as a follow-up.
3. **Test fixture noise.** Tests that build small tmp vaults need at least one `.md` file even for the "no suggestions" case (an empty directory triggers the empty-vault path correctly; verify).
4. **Cross-package coupling.** PAL's vault tools now import a helper from agent_core. This is consistent with the existing pattern (PAL's vault tools already import `resolve_safe`, `is_system_path`). Not a new coupling class.

## Verification

- agent_core test suite passes (helper + tool integration tests added).
- PAL test suite passes (vault tool integration tests added).
- Manual smoke: launch PAL, ask it to `cat` a typo path in a vault that has a near-match, confirm the suggestion appears in the error string.

## Out of scope

- Directory 404 suggestions for ls/find/grep.
- edit_file enhancement (already has the create_file hint).
- Cross-extension fuzziness.
- Semantic similarity matching.
- Caching the candidate list across calls.
- Prompt updates.
- Retrieval-index-backed suggestions.

## Bookmark notes (paused 2026-05-13)

This spec is the result of brainstorming through Sections 1, 2, and 3. All design choices below are user-approved:

- Scope: single-file readers only (cat, head, tail, read_lines, grep when path is a file) plus PAL's vault write tools (delete_file, move_file, replace_in_file). 8 tools total. Skip edit_file (already has create_file hint), skip directory cases.
- Match strategy: filesystem walk + stdlib difflib. No retrieval-index dependency.
- Match against full vault-relative path (not just stem) so directory typos are caught.
- 3 suggestions max, difflib's 0.6 score cutoff, *.md filter, system-path filter.
- Single-line "Did you mean: a.md, b.md, c.md" appended after newline.
- Helper at `agent_core/tools/_shell_helpers.py`; PAL imports it.

**To resume:** ask for user review of this spec; on approval, invoke the writing-plans skill to produce the implementation plan.
