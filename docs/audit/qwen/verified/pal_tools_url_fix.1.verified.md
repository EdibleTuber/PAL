# Verified: pal_tools_url_fix p1+p2+p3+p4+p5 (task 1/1)

## Ruled out

- p1:100 — bounded wait with max(0.1, remaining) + TimeoutError handler + re-fetch; established good pattern.
- p2:101 — same as p1; handler calls ar.expire_stale() then line 104 re-fetches final status.
- p3:201,203,204,205 — synchronous local dict mutations on a copy (first=dict(sources[0])) with no await between read at :191 and write at :207.
- p3:207 — single-shot write, git-recoverable article, clean retry (consume at :208 is after write), executor containment; same rationale as consolidator write ruling.
- p3:208 — in-memory registry op after write; safe ordering (write then consume), idempotent retry if consume raises.
- p4:75 — .exists() is harmless query; OSError swallowed but not consequential.
- p4:183 — same as p4:75; harmless query.
- p4:191 — TOCTOU/permission rare trigger; consequence is executor error with nothing mutated yet and clean retry.
- p4:207 — same code line as p3:207; single judgment, deduped.
- p5:16 — docstring states intended use (LLM instruction), not enforcement claim; safety net is human approval gate.
- p5:21 — consistent: line 100 blocks on proposal.event.wait() with bounded timeout.
- p5:22 — consistent: line 110 returns JSON containing proposal_id.
- p5:114 — consistent: lines 199-204 copy and rewrite sources[0] (first sources entry).
- p5:120 — consistent: line 204 is sources[0] = first, first entry only.

## Notes

- p3 over-flag: lines 201/203/204/205 are plain local dict/list mutations flagged as "state writes"; synchronous block with no await between read and write — not a concurrency hazard.
- p4 over-labels kind: lines 75/183 are harmless .exists() queries; line 191 is a file read with rare TOCTOU risk; line 207 is the same write as p3:207.
- p5:16 original verdict was "cannot_tell_from_this_file" because the docstring states intended use for articles missing both URL and source_file, but the code does not enforce this precondition; the safety net is the human approval step (user sees article_path + proposed values in UrlFixProposalMessage and can decline). Docstring statements of INTENDED USE are instructions, not enforcement claims — consistent with the consolidator "Required sections" ruling.
- p5:21 event is set on approve/decline/edit-response via approval_registry emit chain (established context).
- p5 verdicts observed: consistent=4, cannot_tell=1 (line 16).

## Synthesis adjudication (2026-11-10)

- 0 findings; 15/15 leads ruled out. All subagent rulings confirmed by synthesis ranged reads.
- p3:207/p4:207 (the one substantive call) verified: single-shot write with NOTHING before it (no LLM call, no partial state); if it raises, the proposal is NOT consumed (consume at :208 is after) → clean retry; the article is git-committed so a mid-write disk-full truncation is git-recoverable; the executor contains the escape. The LLM sees an honest "Error in url_fix: {exc}" — the fix simply was not applied. Distinct from Q5 (multi-topic run aborted, in-progress report discarded) and Q6 (partial success reported as failure with no ground-truth field): here there is no lost work product and no misleading status. Ruling out is correct.
- p3:208 consume ordering verified safe both directions: write raises → proposal stays approved, retryable; consume (in-memory, approval_registry.py:159-165) raising after write → retry re-reads the updated article and re-writes idempotently.
- p5:16 ruled out as intended-use instruction (same class as the consolidator F1 ruling), with the human approval gate as the explicit design safety net of this tool pair.
- Subagent's write tool was denied; report persisted by synthesis.
