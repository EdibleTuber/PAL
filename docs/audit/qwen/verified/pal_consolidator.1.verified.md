# Verified: pal_consolidator p2+p3+p4+p5 (task 1/1)

## F1 [DISCOUNTED by synthesis — not a finding] Prompt "Required sections" vs warning-only validation (subagent filed [W][Low])

Subagent's block, verbatim: "pal/consolidator.py:120-121 defines the system prompt with the text 'Required sections: ## Overview, ## Key Concepts' and 'Optional sections (include if relevant): ...'. The word 'Required' and the imperative 'must include' phrasing imply hard enforcement. However, pal/consolidator.py:159-164 calls `validate_compiled_truth(content)` and the result `missing` drives ONLY a `logger.warning` — the article is written to disk regardless of whether required sections are present. By contrast, `pal/compiler.py:381-387` hard-fails with `status: "insufficient"` when `validate_compiled_truth` finds missing sections, while compiler.py:230-232 and :681-683 use the same soft-validation (warning-only) pattern. ... This is a docs-vs-code drift: the prompt text promises hard enforcement that the code does not provide."

Synthesis ruling: the subagent's cross-file facts are all accurate, but the conclusion is wrong. (1) The compiler's OWN prompts use the identical wording — compiler.py:196 and :641 both say "Required sections: ## Overview, ## Key Concepts" — and the compiler's compile path (:230-232) and merge path (:681-683) are equally warning-only; the compiler file was adjudicated with 0 findings. (2) `_REQUIRED_SECTIONS = ["## Overview", "## Key Concepts"]` (pal/article.py:289) — the validator checks exactly the two sections the prompt names; no hidden drift. (3) The single hard-fail site (compiler.py:381-387, promote-synthesis) carries an explicit comment: "If sections are missing, refuse to promote (return insufficient) rather than emit a malformed article" — a deliberate distinction for user-approved content, not evidence that warn-only is a bug elsewhere. (4) "Required sections" in a system prompt is an INSTRUCTION to the model, not an assertion of system enforcement; the p5 pass extracted it as the behavioral guarantee "the generated article must include ...", which the file never claims. The consolidator's prompt wording + soft validation is the established, consistent pattern for LLM-generated content.

Leads: p5:120, p5:121

## F2 [DISCOUNTED by synthesis — not a finding] write+commit share one try; commit-failure yields status "error" (subagent filed [O][Low])

Subagent's block, verbatim: "pal/consolidator.py:167-179 wraps both `self.wiki.write_article(...)` (:168) and `self.wiki.git_commit(...)` (:169-170) in a single try block. If `write_article` succeeds ... but `git_commit` raises — e.g. `subprocess.CalledProcessError` from `git commit -m` at wiki.py:198-203 — the exception handler at :172-179 returns `status: "error"` with `"vault_exists": True` (line 178). ... the combination of `status: "error"` + `vault_exists: True` is ambiguous ... An uninformed caller that only checks `status` will treat the operation as a complete failure and may retry unnecessarily, only to hit the 'target already exists' guard at :64."

Synthesis ruling: the mechanism is real (git_commit runs `git add .` and `git commit -m` with `check=True`, wiki.py:183-203, so it can raise when changes exist) but the consequence the finding rests on does not survive reading the consumer. The execute tool (pal/tools/consolidate.py:163-172) wraps `consolidator.consolidate` in its own try/except AND appends to every outcome: `"_note": "Trust vault_exists: if false, the target file was not written to disk."` The LLM-facing contract explicitly instructs the caller to treat `vault_exists` as ground truth — the "uninformed caller that only checks status" the finding hypothesizes is not the actual consumer. The handler also logs via `logger.exception` and the `reason` string embeds the git stderr via the exception. The written-but-uncommitted article self-heals: the next `git_commit` runs `git add .` over the whole tree and commits it. Status "error" for a write+commit unit of work that did not complete is defensible. The residual (reason string says "write failed" when the commit step failed) is error-string wording on a rare path with the ground-truth field present — not a finding.

Leads: p2:172

## F3 [DISCOUNTED by synthesis — not a finding] Unguarded `await trigger_reindex` after write+commit (subagent filed [A][Low])

Subagent's block, verbatim (abridged): "pal/consolidator.py:187-189 calls `await self.retrieval.trigger_reindex(paths=[absolute_target])` without a try block ... If `trigger_reindex` raises — it is an HTTP call to a retrieval server — the exception propagates uncaught to the executor wrapper ... The identical unguarded pattern exists in `pal/compiler.py:297` and `pal/reorg.py:291`, making it a consistent cross-file design choice — but the risk (lost structured report + unindexed article) remains."

Synthesis ruling: FALSE premise — the helper cannot raise. `trigger_reindex` (agent_core/retrieval.py:74-100) wraps the HTTP post in `try/except Exception → logger.warning + return None`, and returns None for non-202 statuses as well. Its docstring: "A None return is intentional best-effort: a downed inference server must never break the write path." The call site therefore needs no guard; the worst case is `outcome["reindex"] = None`. This is the THIRD instance of the cross-file-helper misruling pattern (first: "drain blocks the loop"; second: the same trigger_reindex claim at the four compiler sites, corrected in pal_compiler.2.verified.md — which rules p4:297/507/596/722 out on exactly this ground). Notably the subagent cited "the identical unguarded pattern exists in pal/compiler.py:297" without checking that file's adjudication or the helper itself. The [A] classification is also wrong for the same reason: a helper that cannot raise creates no async exposure.

Leads: p4:189

## Ruled out

- p2:140 — Handler at :140-147 properly logs and returns structured error (status "error", reason, vault_exists=False). Correctly guarded.
- p3:92 — `source_bodies.append((src, body))` is a local list append in a synchronous loop with zero awaits between read and write; no concurrency issue.
- p4:64, p4:83, p4:178, p4:185 — `.exists()` calls are harmless queries; CPython swallows OSError on these; no state change, no consequence. (The subagent's list also names "p4:78" — no such lead exists; phantom line-number artifact.)
- p4:90 — `read_text()` is unguarded but occurs pre-inference, pre-write; a failure raises to the executor as an opaque error. No partial state written yet — consequence is bounded.
- p4:139 — `inference.complete()` is already guarded by try at :138-147.
- p4:168, p4:169 — `write_article` and `git_commit` are covered by the try at :167-171.
- p4:188 — `.resolve()` on a path verified to exist 2 lines earlier (:186); cannot raise in practice.
- p5:7 — Docstring "Never merges into existing articles" confirmed by :64 exists check + early return.
- p5:56 — `article_path_rel` only set in ok branch (:184) confirmed.
- p5:159 — `validate_compiled_truth` is called at :159 confirmed.
- p5:187 — `if self.retrieval is not None` guard at :187 confirmed.

## Notes

- p5 verdicts observed: consistent=4, contradicted=0, cannot_tell=2 (p5:120, p5:121).
- The cannot_tell verdicts at p5:120/p5:121 were correctly flagged by the p5 pass; the cross-file comparison resolves them as CONSISTENT-with-codebase (see F1 ruling), not as drift.
- p4:189 was labeled kind="http" — correct, but the HTTP call is fully contained inside the helper (see F3).
- p4:169 labeled kind="subprocess" — correct (git_commit uses subprocess.run).
- Subagent's "Unverifiable: none" was inaccurate for F3: the helper's guard was checkable and decisive; the finding failed because the check was skipped.

## Synthesis adjudication (2026-11-10)

- All three subagent findings DISCOUNTED; 0 findings; 19/19 leads ruled out.
- F1 verified against: compiler.py:196/:641 (identical prompt wording), compiler.py:230-232/:681-683 (warn-only), compiler.py:378-387 (hard-fail + deliberate comment), article.py:289-302 (`_REQUIRED_SECTIONS` = exactly the two prompt sections, validator returns issue list).
- F2 verified against: wiki.py:171-203 (git_commit internals — check=True on add/commit), tools/consolidate.py:163-172 (consumer try/except + `_note` "Trust vault_exists" guidance).
- F3 verified against: agent_core/retrieval.py:74-100 (full exception containment + docstring); precedent pal_compiler.2.verified.md (same claim rejected at all four compiler sites).
- Subagent's write tool was denied; report persisted by synthesis.
