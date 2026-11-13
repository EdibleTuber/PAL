# Verified: pal_tools_compile p1+p2+p3+p5 (task 1/1)

## F1 [DISCOUNTED by synthesis — not a finding] Per-path exception handler only wraps compile_one, not post-processing (subagent filed [Q][Medium])

Subagent's block, verbatim: "In CompileBatch.run, the `try` at line 173 and `except Exception` at line 175 wrap only `await ctx.agent.compiler.compile_one(path)`. All subsequent per-path operations — entry dict construction (lines 177-183), vault_exists ground-truth check (line 187), `per_file.append(entry)` (line 188), and counter updates (lines 189-197) — are **outside** the try block. If compile_one succeeds but any post-processing operation raises (e.g., `ctx.agent.config.vault_path` is None, or `.exists()` raises on a filesystem error), the exception propagates unhandled, aborting the entire batch. The entry is never appended to per_file, so the failure is not recorded in the report. This contradicts the docstring claim at lines 128-129: 'Partial failures do not abort the batch. Returns a structured report.' The broader reorg finding Q3 (tools/reorg.py:290–292) covers the same pattern but with the try wrapping the entire loop body; this file's narrower guard is a real gap."

Synthesis tested both proposed triggers and both are unreachable/harmless: (1) if `vault_path` were None, `compile_one` (which writes into the vault) raises FIRST — caught by the :175 handler → error outcome has no `article_path_rel` → the `if article_rel:` guard at :186 skips `.exists()`, so the TypeError is never reached; (2) CPython's `Path.exists()` swallows OSError (returns False) — it does not raise on filesystem errors; (3) entry/counter construction is pure dict/list work. Every operation that can realistically raise (categorize/merge LLM calls, file I/O) is INSIDE `compile_one`, which is guarded. The docstring's promise holds for all real failure modes; there is also no post-loop work (rebuild_index/trigger_reindex) that an escape would skip — unlike Q3, whose unguarded region contained real I/O (`src_full.read_text()` reorg.py:317) plus the skipped index rebuild. Residual is a try-scope hygiene observation with no consequence — not a finding.

Leads: p2:175

## Ruled out

p1:104 — Bounded wait with min 0.1s, catches TimeoutError, calls ar.expire_stale; established good pattern.
p2:105 — asyncio.TimeoutError handler calls ar.expire_stale which signals waiters; established good pattern.
p3:168 — ar.consume sets status to "consumed" before batch execution; single-use semantics are intentional design.
p5:19 — Categorizes and merges: true via compiler.py compile_one (categorize → find_existing_article → merge_into_existing).
p5:20 — Archives on success: true via compiler.py compile_one (archive_raw_files called at lines 180-184 for merged, 286-287 for ok).
p5:18/21/22/52/54 — description text matches the tools' own descriptions and behavior (synthesis spot-checked against the class descriptions at :16-24, :50-58).
p5:53 — "blocks until the user approves, declines, or edits" — bounded wait + declined→edited-successor returns the edited proposal as approved (compile.py:102-116, synthesis verified).
p5:55/56/136 — proposal_id returned by propose and consumed by compile_batch — result dict at :109-120 carries proposal_id/summary_paths (synthesis verified).
p5:127/128/129 — loop over proposal.summary_paths with per-path catch and structured report — verified at :169-201 (this is what makes F1's docstring-contradiction claim fail: the promise holds for all real failure modes).
p5:163 — "each proposal is single-use" — consume-first at :167-168 with the comment "# Consume first — single-use even on failure"; registry consume (:159-165) requires status approved.
p5:207 — "If vault_exists is false, the file is not on disk regardless of status" — the `_note` field at :203-206 says exactly this; vault_exists is a real `.exists()` check at :187.

## Notes

- p5 verdicts: consistent=14, contradicted=0, cannot_tell=2 (lines 19, 20 — both actually consistent via compiler.py chain-following).
- The cannot_tell verdicts at lines 19 and 20 are false positives: the claims are true because compiler.py's compile_one performs categorize, find_existing_article, merge, and archive_raw_files. The pass correctly flagged them as unverifiable from compile.py alone; the chain-following confirms consistency.
- No quote/schema mismatches detected.
- All p1/p2/p3 leads were verified against actual source code.
- Subagent's "Ruled out: 4 leads" undercounted (20 leads total, 1 finding lead → 19 ruled out); the 14 consistent p5 leads are grouped in the Ruled-out section above.
- Subagent's write tool was denied; report persisted by synthesis after the re-adjudication below.

## Synthesis adjudication (2026-11-10)

- F1 DISCOUNTED (not a finding) — see the trigger analysis in the F1 block. Own ranged reads: CompileBatch.run in full (:143-211), the propose wait region (:95-122), and `ApprovalRegistry.consume` (agent_core/approval_registry.py:159-165). The subagent filed on untested trigger hypotheses ("e.g., vault_path is None, or .exists() raises") without checking either — exactly the failure mode the synthesis protocol guards against.
- p1:104/p2:105 verified as the established bounded-wait good pattern: `remaining` from `proposal.expires_at`, `wait_for(..., max(0.1, remaining))`, TimeoutError → `ar.expire_stale()`.
- p3:168 verified: consume is BEFORE the loop, intentional and commented; consistent with the sibling exec tools and the prompt's single-use claim (p5:163).
- p2:175 verified: handler produces a structured `{"status": "error", "reason": ...}` outcome that is counted in `error_count` and the batch continues — the partial-failure path the description promises.
- Verdict: 0 findings, 20/20 leads ruled out.
