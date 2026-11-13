# Verified: pal_tools_research p1+p2+p5 (task 1/1)

## Ruled out

- p1:134 — Bounded wait with `max(0.1, remaining)` derived from `expires_at`, `TimeoutError` handler calls `ar.expire_stale()`, then status re-fetched at :138. Established good pattern.
- p2:36 — `except ValueError` around `Path.relative_to` is a sound narrow handler (relative_to raises ValueError iff not under the base); fallback prints the raw path.
- p2:135 — `except asyncio.TimeoutError` → `ar.expire_stale()` — the same established good pattern as p1:134's tail.
- p2:210 — Broad `except Exception` around `research_topics` returns a clear `f"Research error: {exc}"`. The consume-first at :203 (before the try) means a failed run leaves the proposal consumed — deliberate per the :202 comment ("Consume first so even an exception during run() prevents reuse"): the run is expensive (SearxNG fetch + per-URL LLM summarization) and may have partial side effects (summaries already written to raw/ — Q5's adjudicated territory), so re-running a consumed proposal could double-fetch/double-write. The clean recovery path is re-propose + re-approve, which the error message supports. Contrast with url_fix's consume-AFTER-write (also ruled out, but there the write was cheap and idempotent so retryability was the better trade). Documented design; ruled out.
- p5:16 — "preserves the exact shape from pal._legacy_tools.ToolExecutor._format_research_report": `pal/_legacy_tools.py` does not exist in the repo (verified by ls + grep — only this file's own docstring references it). Historical/intent claim about removed code; no drift is testable against a nonexistent reference.
- p5:118 — "Single-topic mode (unchanged)": the "(unchanged)" half is the same historical-intent class; the code half (a single-topic branch exists, :117–121, creating a proposal without `topics`) is verifiable and true.
- p5:48 / p5:58 — the "exactly one of topic/topics" validation (the two mutually-exclusive checks + the non-empty-list check) enforces exactly what the descriptions claim.
- p5:50 — `json.dumps(result)` at the tail of ProposeResearch.run returns JSON carrying `proposal_id` and `status` (plus echoed topic/depth/topics when approved or when a declined-with-successor edit occurred).
- p5:108 — multi-topic branch builds the human-readable summary (≤3 topics joined; more → first three + "...") and passes `topics=topics` to `create_proposal`.
- p5:131 — "Block until the user signals a terminal status (or expiry)": the bounded wait covers approve/decline via the event; expiry via TimeoutError → `expire_stale()` → re-fetch; the result reports `final.status` verbatim (honest — an expired/pending status is surfaced as-is, not masked).
- p5:166 — `research_topic` "Returns a structured report": `_format_research_report` returns the per-topic/per-source formatted text; consistent as a description of the return.
- p5:198 / p5:202 — "Each proposal is single-use" / "Consume first so even an exception during run() prevents reuse": `ar.consume(proposal_id)` at :203 runs before the try, and the status checks at :192–197 reject a "consumed" proposal with "Each proposal is single-use." — code matches both claims.

## Notes

- p5 verdicts observed in input: consistent=8, contradicted=0, cannot_tell=2 (lines 16, 118); after chain-following both cannot_tells resolve as historical-intent claims about removed code (ruled out), net consistent-with-code=8.
- The `get_successor` edit-flow branch (ProposeResearch.run, the declined-with-successor case returning the successor id as approved) is NOT a lead in any pass and was not filed — noted for the audit trail.
- Subagent's write tool was denied; report persisted by synthesis (verbatim from the subagent return, which was complete).

## Synthesis adjudication (2026-11-10)

- 0 findings; 14/14 leads ruled out. All subagent rulings confirmed by synthesis ranged reads.
- p2:210 (the one substantive call) verified against source: consume at :203 precedes the try at :205; the :202 comment documents the intent; handler at :210–211 returns a clear error. Ruling out as deliberate documented design is correct — the consume-first choice trades re-runnability for no-double-execution of an expensive, partially-side-effectful run, and the re-propose path is the clean recovery. Not a QoL defect: the user is never misinformed (they see "Research error: {exc}" and know to ask for a new proposal).
- p5:16/p5:118 legacy references verified: `pal/_legacy_tools.py` absent (synthesis `ls` + grep — the only in-repo references are research.py's own docstring at :17). Intent claims about removed code; same ruling class as scratch.py p5:12/:85.
- p5:131 expiry tail verified: after TimeoutError + expire_stale, `final = ar.get(proposal_id)` and `result["status"] = final.status` — the LLM sees the real terminal status, no masking.
