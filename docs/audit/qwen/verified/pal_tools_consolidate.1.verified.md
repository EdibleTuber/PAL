# Verified: pal_tools_consolidate p1+p2+p3+p5 (task 1/1)

## Ruled out

- **p1:93** — Bounded wait `wait_for(event.wait(), max(0.1, remaining))` is the established good pattern (url_fix/consolidate/reorg/research all use it); no unbounded wait.
- **p2:77** — `except ValueError` around `ar.create_proposal` (:69–76) returns `f"Error: {exc}"` (:78), a narrow honest error; handler scope is limited to creation validation.
- **p2:94** — `except asyncio.TimeoutError` (:94) calls `ar.expire_stale()` (:95) then re-fetches status (:97); this is the established bounded-wait tail, not a state-write hazard.
- **p2:168** — `except Exception` (:168) builds a structured outcome with `vault_exists: False` (:173) and a `reason` string (:172), plus `_note` at :176–178 telling the LLM to trust `vault_exists`; this is the consolidator-F2 ruling class (failure reported with disk-state ground truth) and executor containment ensures escapes become `Error in {name}: {exc}`.
- **p3:95** — `ar.expire_stale()` is the synchronous registry cleanup that is an integral part of the established bounded-wait tail (:92–97); not a standalone state-write hazard.

## p5 verdicts

All 4 unique claims are **consistent** with the code:

- **p5:22** "Source articles are NOT deleted" — `grep` of `pal/consolidator.py` and `pal/compiler.py` for `unlink|git_rm|os.remove|\.remove(` returns **no matches**; the claim is TRUE.
- **p5:120** "Fails if not approved, already used, or expired." — status checks at :148–155 each return a distinct error message before any execution; the code also guards declined, unknown, and unexpected states. Claim is TRUE.
- **p5:121** "Returns a structured report including vault_exists" — error path builds `vault_exists: False` explicitly (:173); success path's `vault_exists` comes from the already-adjudicated `consolidator.consolidate()` outcome. Claim is TRUE.
- **p5:159** "Consume first — single-use even on failure" — comment at :159 matches code `ar.consume(proposal_id)` at :160 which precedes the try at :162; same documented-design class as the research tool. Claim is TRUE.

## Notes

- **p5 row duplication**: The p5 JSONL contained 8 rows but only 4 unique line numbers (22, 120×3, 121×2, 159×2). Each unique claim was judged once; the duplicates are consistent with the single judgment.
- **p2:94 mislabeled handler**: The lead says handler "set field X" — the actual handler calls `ar.expire_stale()` (:95), which is the bounded-wait tail cleanup, not a field-setting operation.
- **p3 mislabeled as state-write hazard**: `ar.expire_stale()` (:95) is not a concurrent state-write risk; it is the synchronous tail of the bounded-wait pattern and is part of the established good design.
- **p2:168 is the only substantive lead** — but it is covered by the consolidator-F2 ruling class (structured error outcome with `vault_exists` ground truth + `_note` directive) and tool-executor containment.

## Synthesis adjudication (2026-11-10)

- 0 findings; all leads ruled out (5 p1/p2/p3 + 4 unique p5 claims verified consistent; 13 inventory rows → 9 unique).
- Subagent's line cites for the except block verified by synthesis against its own read: `except Exception as exc:` :168, `reason` :172, `vault_exists: False` :173, `_note` :176–178, `return json.dumps(outcome)` :179. Exact match.
- p5:22 no-deletion claim re-verified by synthesis with its own grep of pal/consolidator.py + pal/compiler.py (unlink/git_rm/os.remove/.remove → no matches). Sources are preserved; the "propose_reorg can archive them" half points at the already-adjudicated reorg tool.
- p2:168 ruling confirmed: every path out of the try carries `vault_exists` ground truth (error path explicit at :173; success path from the already-adjudicated consolidator outcome) and the `_note` instructs the LLM to trust it — the LLM can never mistake a failed consolidate for a written article. Same ruling as consolidator F2.
- Subagent's write tool was denied; report persisted by synthesis (verbatim from the subagent return, which was complete).
