# Prompt R6c — correct the PR #24 body

Hand this to opencode as-is. Small, self-contained, no code.

## Why, given it is already merged

PR #24 merged at 2026-11-11 04:39:48Z as a rebase merge; `origin/main` is now `2cbab3b`.
A merged PR's body is still editable, and it is the permanent explanation of why five
commits with no merge commit appeared on `main`. Three things in it are wrong or
incomplete. Fix the record while the context is fresh.

The body is otherwise accurate — every verification claim in it was independently
reproduced (656 collected, 656 passed / 0 failed, 630 base + 26 new, conflict
resolutions, byte-identical fix content). Change only the three things below. Do not
rewrite the rest, do not reflow prose that is already correct.

## Edit 1 — the X1 row understates its own fix (most important)

The round table's X1 row currently reads:

> `pal/tools/vault.py`: `MoveFile.run` catches `OSError` as a structured error;
> `pal/reorg.py`: `move_single` blocks `raw` at any path component

Both clauses are true, and together they omit the actual headline: `move_single` was
rewritten to resolve both paths against `vault_path.resolve()` and reject anything not
`is_relative_to` the root (`pal/reorg.py:38-47` as merged), which closes four escape
vectors — `..` in `dst`, `..` in `src`, an absolute `dst`, and a nested `_`-prefixed
system directory — plus symlink escapes on both sides.

A reader of that table learns about an `OSError` catch and a `raw` check, and does not
learn that a vault escape was closed. Rewrite the row to lead with the containment fix.
Keep it to one or two sentences; the detail lives in the commit.

## Edit 2 — "exactly two files" contradicts the four bullets that follow

The Rebase section says the commits "replay with conflicts in exactly two files, resolved
as follows" and then lists four bullets: `pyproject.toml`, `tests/test_config.py`,
`README.md`, `pal/commands/domain.py`.

The underlying facts are right — `pyproject.toml` auto-merged (both sides carried the
identical `@v1.13.0` line) and `pal/commands/domain.py` merged cleanly (upstream deleted
`PALModel`; the A2/Q1 fix is in the `/import` area and does not overlap). The true
conflicts were `tests/test_config.py` and `README.md`.

Reword so the sentence and the list agree: two conflicts, plus two files worth noting
that merged without one. Keep all four bullets — they are useful.

## Edit 3 — add the batch escape to Known residuals

The residuals list names A5, F1, F2 and F4. It omits the finding that matters most, found
during review of the fix diff and still open on `main`:

> **Batch reorg path escapes the vault via symlink (pre-existing, not a regression).**
> `_path_inside_vault` (`pal/reorg.py:130-135`) is a string check for a leading `/` and
> `..` components with no `resolve()`, while `move_single` is now resolve-based. Its only
> caller is `validate_operations` (`pal/reorg.py:87`), reached through the `Reorg` tool
> (`pal/tools/reorg.py:293-298`). Observed: `validate_operations` returns `[]` and
> `execute_operations_async` reports `status: "ok"` while the file lands outside the
> vault; `move_single` rejects the identical op. Requires an **approved** proposal
> (`pal/tools/reorg.py:275-287`), so it is not an unattended escape. Consequence:
> `docs/security.md:22` ("Any resolved path outside the vault root") remains false for
> this path, so NEW-1 is only partly discharged. Tracked for round R6b.

Two notes to fold in where they fit:

- The existing F1 entry covers `docs/security.md:24` only. `:22` is a separate line and a
  live code gap, not a doc overstatement — keep them distinct.
- This gap was missed because the fix brief (`docs/audit/fix_brief.md`) asserted that the
  batch path "is already contained by `validate_operations` → `_path_inside_vault`",
  which is true for `..` and absolute paths and false for symlinks. The implementation
  followed the brief correctly. Say that plainly — it is a brief defect, not an
  implementer miss, and the record should not read as the latter.

## How

Read the current body first, edit it, write it back:

```
gh pr view 24 --json body --jq .body > /tmp/pr24_body.md
# edit /tmp/pr24_body.md
gh pr edit 24 --body-file /tmp/pr24_body.md
```

Then read it back from the API and confirm all three edits are present — do not trust the
editor's or the command's success output:

```
gh pr view 24 --json body --jq .body | grep -nE 'resolve|is_relative_to|two conflicts|symlink'
```

## Report back

The three changed passages, quoted before and after, plus the grep output confirming they
landed.
