# Verified: pal_archive p4+p5 (task 1/1)

## Ruled out

- **p4:24** — `mkdir(parents=True, exist_ok=True)` is idempotent; no risk of partial state or race.
- **p4:30** — `raw_full.rename(dest)` is unguarded but all five callers are post-success in already-adjudicated files; `exists()` guard prevents no-op; on failure files remain at original paths (benign, self-healing via later git commit).
- **p4:39** — `summary_full.rename(dest)` — same reasoning as p4:30; additionally the `.summary.md` suffix at line 37 (`dest_name = summary_full.stem + ".summary.md"`) prevents POSIX rename-overwrite collision with the raw file.
- **p4:46, p4:50, p4:51, p4:52** — `cleanup_archived` has zero product-code callers: repo-wide grep finds only the `def` at `pal/archive.py:43` and calls in `tests/test_archive.py` (lines 63, 74, 80). The function is tests-only dead code in production; the mtime-based retention policy is never executed outside tests.

## Notes

- **p5:22 (×2 duplicate)** — Two p5 rows cite line 22 with slightly different claims ("moves raw and summary files" / "moves summary files"). Both verdicts are `consistent`. The function does move both raw and summary files to `raw/archived/` after successful compile/import (verified against all five caller contexts in `pal/compiler.py:180`, `:286`, `:496`, `:581` and `pal/commands/domain.py:310`).
- **p5:36** — Verdict `consistent`. The comment at line 36 (`# Use .summary.md suffix to avoid name collision with the raw file`) matches the code at line 37 (`dest_name = summary_full.stem + ".summary.md"`). The collision is real: raw and summary files share the same slug stem; without the suffix the second rename would overwrite the first.
- **cleanup_archived dead code** — The function (`pal/archive.py:43–53`) implements a documented 30-day retention policy but is unreachable in production. The tests exercise all three branches (deletes old, keeps recent, handles missing dir). This is a maintenance observation, not an audit finding.
- **p4:30/p4:39 failure mode** — If a rename raises (e.g., disk full, permission denied), the article is already committed (post-success path), and the raw/summary files simply remain at their original paths. A later whole-tree git commit picks them up. No data loss or corruption.
