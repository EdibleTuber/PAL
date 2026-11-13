# Verified: pal_reorg p2+p3 (task 1/2)

Source: /mnt/secondary/projects/PAL/pal/reorg.py (380 lines, HEAD 6531f32).
Leads: p2=9 (all part-A; the file has no create_task sites), p3=29 (38 total).

Note: reviewer-egpu write denied; synthesis session persisted this report after
spot-checking the two `except OSError: continue` sites (count_references :158–171
and _rewrite_references :363–380 — read with errors="replace", so OSError means
vanished/unreadable file that can't be rewritten anyway; the caller wraps the whole
rewrite in try/except returning a structured failure dict, :228–239) and the
structured-failure shape at :233.

## Confirmed

None. 0 findings.

## Ruled out

p2 A (all 9):
- p2-A:166, p2-A:374 — `except OSError: continue` skipping unreadable files in
  best-effort vault-wide scans; decode errors already absorbed by
  errors="replace"; catastrophic rewrite failure surfaces via the caller's
  structured-failure dict (:233).
- p2-A:206, p2-A:255, p2-A:286, p2-A:355 — `logger.warning` on rebuild_index /
  git-commit failures after move/merge; reorg result itself is unaffected.
- p2-A:233, p2-A:244, p2-A:337 — converted into structured `{"status": "failed",
  "reason": ...}` results the caller reports; not swallowed.

p3 (all 29):
- p3:61–120 (validate_operations) — local list/set appends in a synchronous
  function; no await, no concurrency.
- p3:148–149, 170 — local set/list/int in synchronous collection helpers.
- p3:184–195, 271–275 — `results.append` in (synchronous) execute_operations /
  execute_operations_async; `_execute_move` is sync (`def`, :210), `_execute_merge`
  properly awaited (:273); no await between read and write of shared state.
- p3:205, 285 — `wiki.rebuild_index()` call; failure handled (p2-A:206/286).
- p3:293 — `results[0]["_reindex"] = ...` guarded by `if results` (:292); local
  dict.
- p3:378–379 — synchronous write_text + local counter.

## Notes

- No p2-B/p2-C leads: reorg.py has no `asyncio.create_task` (verified).
- All quotes matched; nothing unverifiable.
