# Prompt R6b — close the batch-path vault escape (2 items)

Hand this to opencode as-is.

**Two items. Do not expand the round.** Rounds of four come back clean; a round of seven
comes back half-addressed. If you find a third thing, write it down and report it — do
not fix it here.

## Base

Branch from `origin/main` = `2cbab3b` (verify with
`git -C /mnt/secondary/projects/PAL rev-parse origin/main` — fetch first). PR #24 landed
as a rebase merge, so there is no merge commit and the five fix commits have upstream
SHAs `200c446`, `dde9f74`, `bec5d17`, `a3cf96b`, `2cbab3b`.

The local checkout sits on branch `fix-audit-rounds` at `c18d7cb`, **five commits
ahead of `origin/main`** (the pre-rebase PR #24 versions) with a different test set.
Branch from `origin/main`, never from `main`/`HEAD` — the 656-test baseline below
holds only on a branch from `origin/main`.

## No code sketches in this brief — deliberately

This is a security invariant spanning two entry points. Code written into prose is
unexecuted and remote from the real file: no compiler, no test, and no sight of the
neighbouring guard that would have shown which input is untrusted. You get the
invariant, the interfaces and what the tests must discriminate. Write the code against
the actual file.

---

## Item 1 — `_path_inside_vault` is a string check; `move_single` is not

### The defect

`pal/reorg.py:130-135`:

```python
def _path_inside_vault(self, rel: str) -> bool:
    if rel.startswith("/"):
        return False
    if ".." in rel.split("/"):
        return False
    return True
```

It rejects a leading `/` and literal `..` components. It never calls `resolve()`, so a
**symlink inside the vault pointing outside it** passes. Its only caller is
`pal/reorg.py:87`, inside `validate_operations`.

Meanwhile `move_single` (`pal/reorg.py:38-47`, as merged) resolves properly:

```
root = self.vault_path.resolve()
...
full = (self.vault_path / rel).resolve()
if not full.is_relative_to(root):
    raise ValueError(f"path outside vault: {side}={rel}")
```

So the two entry points disagree, and the weaker one wins for batch operations.

### Reproducer — observed, not theorised

Run against `origin/main` before changing anything. Create a vault, a file in it, a
directory outside it, and a symlink inside the vault pointing at that outside directory.
Then submit one move op with `dst` traversing the symlink
(`{"type": "move", "src": "notes/secret.md", "dst": "link/escaped.md"}`) through
`validate_operations` followed by `execute_operations_async`.

Observed at `2cbab3b`:

```
validate_operations errors: []
per-op result: [{'op': 'move', 'src': 'notes/secret.md', 'dst': 'link/escaped.md',
                 'status': 'ok', 'references_rewritten': 0}]
landed OUTSIDE vault: True
still in vault: False
move_single, same op: REJECTED ValueError path outside vault
```

The file leaves the vault and the batch path reports `ok`.

### Reachability and severity — state it accurately

Reachable through the `Reorg` tool (`pal/tools/reorg.py:293-298`), which is registered
and exported (`pal/tools/__init__.py:27,49`). **It requires an approved proposal** —
`pal/tools/reorg.py:275-287` gates on `proposal.status == "approved"`, so a human
approves the op list first. That is a real mitigation and the fix report must say so.
What the approval does *not* show is that `link/` is a symlink; the path reads as
ordinary. `validate_operations` is called at both propose time
(`pal/tools/reorg.py:90`) and execute time (`:293`).

### The invariant to establish

> For every operation that reaches `execute_operations_async`, both `src` and `dst`,
> resolved against the vault root, are inside `vault_path.resolve()`.

Containment must be decided on the **resolved** path, not on the string. The two entry
points must agree: an op rejected by `move_single` must be rejected by
`validate_operations`, and vice versa.

### Constraints

- `_path_inside_vault` currently takes only `rel` and returns `bool`. It needs the vault
  root to do its job. Changing its signature is fine — it has one caller. Keep whatever
  shape makes the invariant checkable, and keep `validate_operations`'s error strings
  usable: it returns a list of human-readable errors that surface to the model and to
  the user at propose time, so "path outside vault" must name which op and which side.
- `resolve()` on a path whose parent does not exist yet is correct and intended —
  Python's default is non-strict. `move_single` already depends on this (it creates
  `dst_path.parent` afterwards at `:57`). Do not add `strict=True`.
- Both call sites (`:90` propose, `:293` execute) must stay consistent. A proposal that
  validates at propose time and fails at execute time is a worse failure than rejecting
  it up front.

### The `raw` asymmetry — decide and record

`validate_operations:90` calls `_is_system_path`, which (`pal/reorg.py:137-139`) only
checks for `_`-prefixed components. `move_single:42` additionally blocks `"raw" in
parts`. So `notes/raw/x.md` is rejected on the single-file path and allowed on the batch
path.

Pick one and make both paths agree. Note that the single-file widening already changed
behaviour — `notes/raw/x.md` used to be an allowed move and now raises. If you extend
`raw` blocking to the batch path, check `tests/` for existing batch cases that move
things under a `raw/` directory before you do, and say in the report what the decision
was and what it breaks.

### Tests — TDD-gated

Every new test must be **observed failing against `2cbab3b`** before the fix, and the
failure must be the containment failure, not a setup error. Paste the RED output into
the report.

Discriminators required:

1. Symlink `dst` escape through the batch path — assert on **where the file landed**
   (outside path does not exist, in-vault path still does), not on the error string.
   This is the one that is currently green-and-wrong.
2. Symlink `src` escape — an outside file pulled into the vault through a symlinked src.
3. Propose-time rejection: `validate_operations` returns a non-empty error list for the
   same op, so the proposal never reaches approval.
4. Agreement: an op rejected by `move_single` is rejected by `validate_operations`.

Positive controls — these must still pass, or the fix is a "reject everything"
regression:

- a plain in-vault batch move
- a batch move creating new nested parent directories
- an inner `..` that stays inside the vault (`notes/sub/../other.md`)
- a directory named `drawer` — guard against `raw` being matched as a substring rather
  than a path component
- a multi-op batch where every op is legitimate

---

## Item 2 — `docs/security.md:16-24` is still partly false

Do Item 1 first. This item is a read-and-correct pass over nine lines, once the code
behind them is true.

Current text at `:18-22`:

```
All file operations resolve paths through a safe-path check that rejects:

- `..` components
- Leading `/` (absolute paths)
- Any resolved path outside the vault root
```

- `:22` "Any resolved path outside the vault root" — true for `move_single` after PR #24,
  **false for the batch path** until Item 1 lands. After Item 1, re-read it and confirm
  it is true rather than assuming the fix made it so.
- `:24` "The same guards apply to chat tools, slash commands, and the retrieval client" —
  this is reviewer finding **F1**, carried over unfixed. The retrieval client's guard is
  an input check on `doc_id`, not path resolution. Read the retrieval client and correct
  the sentence to what the code actually does. Cite the `path:line` you read.

Walk all of `:16-24` line by line and verify each clause against source. Prefer wording
that **stops the clock**: name the function that enforces the guarantee, rather than
restating the guarantee in prose that silently rots when the function changes.

---

## Report back

- RED output for each new test against `2cbab3b`, then green after.
- Full suite at the branch tip. Baseline: `origin/main` collects **656** and runs
  **656 passed, 0 failed** (~135 s) with these five ignores — the same 5 collection
  errors are pre-existing and not yours to fix: four are `No module named 'pal.client'`
  (`test_client`, `test_daemon`, `test_integration`, `test_prompt_injection`) and one
  is `No module named 'pal._legacy_tools'` (`test_chat_research_integration`):

```
/mnt/secondary/PAL/.venv/bin/python -m pytest -q \
  --ignore=tests/test_chat_research_integration.py \
  --ignore=tests/test_client.py \
  --ignore=tests/test_daemon.py \
  --ignore=tests/test_integration.py \
  --ignore=tests/test_prompt_injection.py
```

  Report collected/passed/failed as three numbers, and say explicitly that the 5
  collection errors persist — do not report a filtered count as a green suite.
- The reproducer re-run at the branch tip, showing `validate_operations` now returning an
  error and the file still in the vault.
- The `raw` decision and what it changes.
- For Item 2, the `path:line` you read for each corrected clause.
- `agent_core` is a **dependency, not this repo**. The authoritative copy is
  `/mnt/secondary/PAL/.venv/lib/python3.12/site-packages/agent_core/` (1.13.0, pinned
  `@v1.13.0`). Two other divergent copies exist on disk; line numbers from them are
  wrong. Do not edit the venv copy.
