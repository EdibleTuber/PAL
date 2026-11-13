# Verified: pal_tools_reorg p1+p2+p4+p5 (task 1/1)

## F1 [O][Medium] Partial application + opaque error in Reorg.run

The `except Exception` at line 290-292 wraps `execute_operations_async` and returns a generic error string `"Error: reorg execution failed: {exc}"`. If the underlying reorganizer partially applies operations on disk (some moves succeed, then a later operation fails), the exception propagates and the `per_op` variable is never assigned. The ground-truth echo loop at lines 295-302 is skipped, and the LLM/user receives only the opaque error string with no information about which operations succeeded and which failed. The tool description itself states "Partial failures don't abort the batch" (line 237-238), implying partial success is an expected scenario, yet the exception handler discards any per-op results that might have been produced before the failure. The re-validator at line 286 runs before execution but does not help diagnose partial failures.

Leads: p2:292

## F2 [Q][Low] Silent fallback to 0 references in ProposeReorg.run

The `except Exception:` at line 97 wraps `count_references(src_paths)` and silently sets `references_preview = 0` with no logging. If `count_references` raises (e.g., filesystem error, permission denied on a vault file), the approval message emitted at line 109-114 will show `references_preview=0`, which may be false — the actual reference count could be nonzero. The user approves or declines based on this preview, so the decision is made against incorrect information. However, execution re-validates against current vault state at line 286, so the actual operation is safe. The absence of a log statement means the silent fallback is also invisible in operational logs.

Leads: p2:97

## Ruled out

p1:122, p1:122, p1:216, p1:216 — bounded waits with real expiry (DEFAULT_EXPIRY_MINUTES=15 in agent_core/approval_registry.py:22); TimeoutError caught and handled via ar.expire_stale().
p2:88 — exception handled, returns structured error string to caller.
p2:107 — ValueError handled, returns error string to caller.
p2:123 — bounded wait with real expiry, TimeoutError handled.
p2:200 — ValueError handled, returns error string to caller.
p2:217 — bounded wait with real expiry, TimeoutError handled.
p4:300, p4:302 — `.exists()` is a filesystem read, not an external effect; lead mislabels kind.

## Notes

- p4 leads mislabel `.exists()` as kind "fs_query" with "atomic_or_partial: n/a"; these are reads with no external effect and no failure mode to handle.
- p5: 25 leads seen, 25 consistent, 0 contradicted. The 6 originally marked "cannot_tell_from_this_file" resolved as follows:
  - :25 "Prefer batches of 3-5 operations" — consistent; stated in tool description text (line 25), a preference for LLM behavior.
  - :285 "State may have changed between proposal and execute" — consistent; comment at :283-284 accurately describes why re-validation at :286 is needed.
  - :316, :317 "A successful move or merge must show src_exists_after=false / dst_exists_after=true" — consistent; these describe the expected meaning of echo fields in the `_note` string (line 315-317), not a claim of enforcement.
  - :145 "Wisdom is injected into every future system prompt" — consistent; verified via agent_core/prompts/builder.py:41-47 (`render_wisdom` calls `self.wisdom.bodies()`) and pal/agent.py:108-111 (wisdom bodies formatted and added to prompt sections), pal/agent.py:401 (builder called in system prompt assembly).
  - :146 "Wisdom should be treated as durable guidance" — consistent; stated in tool description text (line 146).
- approval_registry `create_proposal` sets `expires_at = now + timedelta(minutes=self._expiry_minutes)` with DEFAULT_EXPIRY_MINUTES=15 (agent_core/approval_registry.py:22, 125), confirming the bounded wait is real.
- approval_registry `expire_stale` (agent_core/approval_registry.py:167-174) marks pending proposals past expiry as "expired" and signals waiters via `proposal.event.set()`.

## Synthesis adjudication (2026-11-10)

- F1 (Reorg exception path) — CONFIRMED, reclassified [O]→[Q], recorded as **Q3 [Medium]**. Synthesis ranged reads (pal/tools/reorg.py:234-325, pal/reorg.py:210-330):
  - The op loop (`execute_operations_async`, pal/reorg.py:269-282) does NOT wrap `_execute_move`/`_execute_merge`. Expected per-op failures are structured (move: src/dst checks :218-227, `_rewrite_references` :233-241, rename :243-251 all caught; merge: src/dst/compiler checks :310-327).
  - Realistic escape paths: `_execute_merge`'s `src_full.read_text()` (reorg.py:317) is unguarded (TOCTOU OSError), and `compiler.merge_into_existing` (an LLM call, 5 retries × 600 s) raises on inference failure — a mid-batch merge op during an inference outage aborts the remaining ops after earlier moves are applied and individually git-committed (reorg.py:253-257).
  - On escape: tools/reorg.py:291-292 returns only `Error: reorg execution failed: {exc}`; ground-truth echo (:295-302), ok/failed/references report, AND the post-loop `rebuild_index`/`trigger_reindex` (reorg.py:284-293) are all skipped → `_index.md` stale versus applied moves. The tool description (:237-240) promises "Partial failures don't abort the batch. Returns a structured report" — the exception path violates both.
  - Reclassification rationale: no ordering/race is involved — it is a failure-reporting/observability gap plus stale index on the exception path, no data loss (per-move commits; git recoverable). Q (QoL/stability), Medium.
- F2 (silent references_preview=0) — CONFIRMED as **Q4 [Low]**, trigger refined: `count_references` (pal/reorg.py:156-173) already absorbs per-file OSErrors internally (`except OSError: continue`), so the outer except (tools/reorg.py:95-98) is a rare backstop (rglob-level failure, non-str path), not the "filesystem error, permission denied" case the report's F2 text suggested. Substance unchanged: no log, approval message may show a false 0; execution re-validates so ops are safe — misleading preview only.
- p5 spot checks (synthesis): :25 → ProposeReorg description text "Prefer batches of 3-5 operations so the approval prompt stays scannable" (:24-25) — stated preference, consistent; :145/:146 → ProposePromote description (:144-147) + agent_core/prompts/builder.py:41-42 `render_wisdom` → `self.wisdom.bodies()` (builder holds the WisdomManager, :22/:31) and pal/prompts/system.py:4 docstring (wisdom is a standard prompt section) — resolved TRUE as reported; :316/:317 → the `_note` string inside the report dict ("Trust src_exists_after and dst_exists_after...") — describes meaning, not enforcement, as reported; :285 → comment :283-284 + re-validation :286 — as reported.
- p1 bound verified: DEFAULT_EXPIRY_MINUTES=15 (approval_registry.py:22), `expires_at=now+15min` in create_proposal (:125), expire_stale signals at :171 — the wait bound is real; distinct from A2's unbounded wait.
- Verdict: 2 findings (Q3, Q4), 36/38 leads ruled out stands. All 38 leads accounted for (2 findings + 11 p1/p2/p4 ruled out + 25 p5 verified consistent).
