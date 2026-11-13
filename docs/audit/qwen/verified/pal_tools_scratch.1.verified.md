# Verified: pal_tools_scratch p2+p5 (task 1/1)

## Q6 [Q][Low] AddLearning: wiki.git_commit() outside try/except — LLM sees error when learning file is written

`pal/tools/scratch.py:124` — `ctx.agent.wiki.git_commit(f"learn: add {slug}")` sits OUTSIDE the try/except block (lines 120–123). If the subprocess call in `wiki.git_commit` raises (e.g. `CalledProcessError` from `git commit -m` with `check=True` at `pal/wiki.py:198–203`), the exception propagates up and is caught by the framework executor's outer handler at `agent_core/tools/executor.py:131`, which returns `f"Error in {name}: {exc}"` to the LLM. The LLM sees an error message instead of the expected JSON `{slug, title}` — but the learning file WAS already written by `ctx.agent.learning.add()` at line 121. The uncommitted file self-heals on the next `git_commit` call because `git add .` stages the whole tree (same precedent as the consolidator adjudication). Severity: Low — no data loss, no session crash, but the LLM receives confusing feedback about a partial success. The inconsistency is that `learning.add` (the data-write step) is inside the try/except while `git_commit` (the bookkeeping step) is outside.

Leads: p2:122

## Ruled out

- p2:71 — ScratchpadTooLarge handler at line 71–75 returns structured error with exc.proposed_bytes and exc.max_bytes (confirmed on agent_core/scratchpad.py:16–27).
- p2:76 — except Exception at line 76 catches all remaining sp.write() failures (mkdir, write_text); the commit callback's exceptions are already swallowed by its own try/except in agent_core/scratchpad.py:83–88.
- p5:8 — Scratchpad IS constructed with PAL's commit_callback: agent.py:314–323 confirms _commit_scratchpad calls self.wiki.git_commit.
- p5:9 — Every write DOES attempt a git commit via the callback (agent_core/scratchpad.py:83–85), but failures are swallowed to a warning and the commit no-ops on a clean tree — claim is technically true.
- p5:12 — "keeps vault history consistent with PAL's existing behaviour" is a design-intent claim, not a testable code claim.
- p5:37 — Same as :8 — Scratchpad IS constructed with PAL's git-commit callback (agent.py:314–323).
- p5:38 — Every write DOES attempt a git commit (agent_core/scratchpad.py:83–85), but callback failures are swallowed and clean trees no-op — "recorded" is slightly overstated but not contradicted.
- p5:85 — "matches PAL's pre-migration behaviour" is a design-intent claim, not a testable code claim.
- p5:91 — Learnings DO stay as candidates until promoted: description at line 91–92 states this and the /promote builtin at agent_core/commands/_builtin_impls.py:218 confirms the promotion mechanism.
- p5:92 — Users promote learnings via /promote: confirmed by agent_core/commands/_builtin_impls.py:218 (name="promote", fetches learning body+meta, calls ctx.agent.wisdom.add).
- p5:105 — "The lesson itself, in 1-4 sentences" restates the parameter description verbatim (line 105) — a model-facing instruction, not an enforcement claim; consistent with the parameter schema.

## Notes

- p5 verdict counts observed: consistent=5 (lines 6, 11, 47, 48, 84), contradicted=0, cannot_tell=9 (lines 8, 9, 12, 37, 38, 85, 91, 92, 105).
- p5:12 and p5:85 are design-intent claims about historical consistency — not testable in code at all.
- p5:91 and p5:92 resolve to consistent after following the chain to agent_core/commands/_builtin_impls.py:218 (framework builtin /promote).
- p5:8 and p5:37 resolve to consistent after following the chain to pal/agent.py:314–323 (_build_scratchpad with commit_callback).
- p5:9 and p5:38 resolve to consistent with caveats: the callback DOES attempt git_commit, but failures are swallowed to a warning (agent_core/scratchpad.py:83–88) and clean trees no-op (pal/wiki.py:196–197).
- p5:105 is an instruction-to-LLM claim (restating the parameter description), not a system-behavior guarantee — consistent but not enforceable.
- The second _build_scratchpad construction site at pal/commands/domain.py:532–540 uses the same pattern (commit_callback calls ctx.agent.wiki.git_commit) but is for a different tool (/scratch) and not under review here.

## Synthesis adjudication (2026-11-10)

- Q6 ACCEPTED as filed ([Q][Low]). Synthesis re-verified: (1) scratch.py:120-124 — try wraps only `learning.add`; `wiki.git_commit` at :124 is outside it; (2) wiki.py:183-203 — `git add .` and `git commit -m` both run with check=True, so CalledProcessError is a realistic (rare) trigger; (3) executor containment at agent_core/tools/executor.py:127-131 (session survives, bare error string); (4) self-heal — every whole-tree `git add .` commit (any later tool's git_commit, or the scratchpad callback at agent.py:314-315 which calls wiki.git_commit directly) picks up the uncommitted learning file. Distinct from the consolidator F2 ruling (there the handler returned `vault_exists` ground truth and the consumer added a "Trust vault_exists" note; here there is no ground-truth field — the bare executor error is strictly worse, which is why this one is filed).
- All 9 p5 cannot_tells resolved TRUE/consistent per the chains above (synthesis independently traced: agent.py:310-323, scratchpad.py:73-89, _builtin_impls.py:218-247).
- Subagent's write tool was denied; report persisted by synthesis.
