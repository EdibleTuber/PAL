# PAL Tool Audit Report

**Date:** 2026-05-11
**Status:** Accepted (panel reviewed 2026-05-11)
**Panel:** Architecture coherence, YAGNI skeptic, Implementation realist, API consumer (PAL's-eye)
**Revisions applied from panel review:** see "Panel revisions applied" section below.
**Spec:** docs/superpowers/specs/2026-05-11-tool-audit-design.md

## Summary

- Total tools audited: 24 (2 retrieval, 5 file ops, 3 compile, 3 consolidate/promote, 5 knowledge management, 2 utility, 5 slash command surfaces; 24 distinct entries across the seven category inputs)
- Verdicts: 24 keep / 0 consolidate / 0 delete
- Cross-cutting must-fixes (deduped): 6
- Tool-specific must-fixes: 12
- Items needing their own specs: 10

## Deletes

None. Every audited tool earned a `keep` verdict; each was load-bearing for at least one user-facing workflow that no other tool covered.

## Consolidate clusters

None. Subagents identified overlap (e.g., edit_file/replace_in_file/create_file; propose_consolidate/propose_compile_batch/propose_promote_synthesis; /scratch vs update_scratch) but in every case the tools were judged to serve distinct call-sites or contracts cheap enough to retain. No merge cluster was proposed.

## Cross-cutting must-fix (deduped)

### Validation timing: propose-tools validate after user approval

- **Affected tools:** propose_compile_batch, propose_consolidate, propose_promote_synthesis (and indirectly url_fix's CLI edit branch)
- **Recommendation:** Move all input validation (path shape, file existence, raw/system-prefix checks, target collisions, size guards, required-section checks for compiled_truth) to BEFORE `create_proposal`. Today the user spends an approval round-trip only for the executor to surface insufficient/invalid_path/too_large/summary_collision after the fact. Each tool should resolve and stat its inputs, run any required-section validator (e.g., `validate_compiled_truth` for promote_synthesis), and short-circuit with a structured error before the proposal is ever created. Smoke-confirmed for promote_synthesis on 2026-05-11.
- **Invariant introduced (named per panel review):** the propose-tool and the executor must agree on validation. After this fix, both code paths run the same checks. Extract a shared `validate_<kind>_inputs(...)` helper per proposal kind so they can't drift; the executor's call becomes defense-in-depth, not primary validation.
- **TOCTOU caveat (named per panel review):** for `propose_promote_synthesis`, the `summary_collision` check has a 15-minute approval window during which another tool call could land the same slug. Pre-stat catches the common case but the executor must still re-stat after approval (and either re-propose or reserve the slug at proposal time). Spec must address this; pre-stat alone does not eliminate the failure mode.
- **Needs spec:** yes (TOCTOU window + slug-reservation strategy needs design before implementation)

### Vault-write success shape and reindex propagation (BUNDLED)

- **Affected tools:** edit_file, create_file, delete_file, replace_in_file, move_file (consumer: wait_for_reindex; promise made in pal/prompts/system.py:100; same fix unblocks url_fix's missing reindex call at url_fix.py:200)
- **Recommendation:** Five tools currently emit four shapes (bare "Updated: {path}" and "Created: {path}" strings; JSON for delete/replace; `{moved, reindex_queued: bool}` for move). The reindex contract advertised by the system prompt (`{job_id, status}` from any write tool) is also broken: three tools return bare strings; the other two return JSON without the reindex field; `move_file` already uses a third name `reindex_queued`. **Land as one spec, in this order:** (1) pin the canonical success shape `{"status": ..., "path": ..., "reindex": {...}}` in a single source-of-truth location, with errors also as JSON; (2) propagate `RetrievalClient.trigger_reindex(...)` return values into the `reindex` field on every vault tool. Critically, the audit assumes `RetrievalClient.trigger_reindex` returns `{job_id, status}` but no caller in `vault.py` captures any return today -- verify the client interface BEFORE depending on it; if the client returns `None`, the client itself needs to change first. Also affects `url_fix.py:200` which never calls `trigger_reindex` at all.
- **Needs spec:** yes (architecture-level shape decision plus client-interface verification; bundle reindex + return-shape into one workstream)

### Commit-failure handling missing on three vault tools

- **Affected tools:** edit_file, create_file, move_file (delete_file and replace_in_file already handle this)
- **Recommendation:** Capture pre-state (old body+meta for edit; absence for create; old src path for move) before mutating; on `git_commit` exception, restore the pre-state and surface `..._uncommitted` status with a recovery hint. Mirror the pattern already shipping in replace_in_file (vault.py:472) and the post-commit warning shape in delete_file (vault.py:329-334).
- **Needs spec:** no

### Snippet truncation without ellipsis

- **Affected tools:** search_vault, search_web
- **Recommendation:** Both formatters hard-truncate at 200 chars with no marker, leaving the LLM unable to tell whether the match continued past the cut. Append "..." (or equivalent) when truncated. Single-character fix in two locations (`agent_core/agent_core/tools/_framework.py:106` and `:147-149`).
- **Needs spec:** no

### Vault read 404s lack nearest-match suggestions

- **Affected tools:** cat (`agent_core/agent_core/tools/_shell.py:32`), edit_file (vault.py), grep (any read path that 404s)
- **Recommendation:** PAL's explicit ask #2 from the path-determinism feedback. When a vault read tool 404s, run a cheap stem/dirname fuzzy match against the index (or filesystem) and append "nearest matches: a.md, b.md" to the error. Without this, even after the `search_vault` id fix lands, PAL still re-enters guessing loops on cat/edit/grep failures. Promoted to must-fix from should-fix on panel review (API consumer): the two friction asks are complementary, not substitutes.
- **Needs spec:** yes (shared helper across read tools, story for index-vs-filesystem fuzzy match)

### Stale or missing prompt catalog entries

- **Affected tools:** edit_file, create_file, replace_in_file, move_file, delete_file, propose_promote, propose_promote_synthesis
- **Recommendation:** Four prompt-level inconsistencies, all in `pal/prompts/system.py`:
  1. Line 18 catalog only lists `edit_file` and `create_file`; add a "vault writes" group naming all five write tools (one line each).
  2. Line 18 description says create_file is "for arbitrary notes" but the runtime guard restricts it to `raw/`; rewrite to read "scratch notes under raw/notes/ ONLY" and point promoted-category writes at compile_*/consolidate/propose_promote_synthesis.
  3. Line 84 still tells PAL to refuse delete requests and suggest manual deletion, contradicting the shipped delete_file tool (commit 96ad1c7). Rewrite to describe delete_file (atomic git rm, recoverable via git revert, refuses system dirs).
  4. Line 26 entry for propose_promote_synthesis omits the required ## Overview / ## Key Concepts contract; append the section requirement. Also add a one-line entry for propose_promote (learning -> wisdom), currently absent entirely.
- **Needs spec:** no

## Must-fix by category

### Retrieval

- **search_vault** drops the deterministic `id` (path-without-extension) and emits `name` (frontmatter title or stem) as the primary label, so the LLM cannot feed results back into cat/edit/grep without guessing the path. `agent_core/agent_core/tools/_framework.py:97-104`. Render `id` (or `path = id + ".md"`) as the leading field; keep `name` as parenthetical secondary. Backing data already present (`vectordb.py:136-143`).
- **search_vault** silently drops `score`, `tags`, and `collection` from each result, leaving PAL with no ranking signal. `agent_core/agent_core/tools/_framework.py:96-107`. Emit structured per-result lines (path, score to 3dp, snippet) or return JSON; mirror what the retrieval-eval script already consumes (`scripts/retrieval_eval/run.py:38-47`).
- **search_web** returns ALL SearxNG results unfiltered, including domains the user has not allowlisted. FetchUrl rejects them, so PAL confidently suggests URLs the next tool refuses. `agent_core/agent_core/tools/_framework.py:131-150`. Either filter through `allowlist.is_allowed` (matching `pal/commands/domain.py:218`) or tag each result `allowed: true|false`. Same name, two contracts today.

### Compile

- **propose_compile_batch** does no path pre-validation before creating the approval proposal; bad paths surface only when compile_batch executes. `pal/tools/compile.py:77-89`. Resolve each `summary_path`, reject `..`/leading `/`, require `raw/summaries/` prefix, stat existence; only proceed if all pass. (Generalized version of this is in cross-cutting validation-timing item; this is the per-tool entry-point.)

### Consolidate / Promote synthesis

- **propose_consolidate** does not validate sources exist or reject raw/system-prefix paths before approval. `pal/tools/consolidate.py:50-89`. Resolve each `source_path` against vault, reject missing/raw/_-prefixed entries pre-`create_proposal`. (See cross-cutting validation-timing item.)
- **propose_consolidate** does not preview total source body size pre-approval; consolidator returns `too_large` only after the user approves. `pal/tools/consolidate.py:50-89`, `pal/consolidator.py:97`. Pre-read sizes; reject or warn before `create_proposal`.
- **propose_promote_synthesis** required-section validation fires AFTER user approval (smoke-confirmed 2026-05-11). `pal/tools/promote_synthesis.py:85-98` (validation absent), `pal/compiler.py:378-384` (where it currently runs). Call `validate_compiled_truth(note_body)` after reading the note and return `insufficient` with missing sections BEFORE `create_proposal`. (See cross-cutting validation-timing item.)
- **propose_promote_synthesis** consume-before-write is a correctness bug, not polish. `pal/tools/promote_synthesis.py:135` calls `ar.consume(proposal_id)` BEFORE `summary_full.write_text` (line 157) and `compile_chat_synthesis` (line 160). If either raises, the proposal is consumed (single-use) but no article exists. Wrap write+compile in try/except; on failure, surface `error` so PAL can re-propose without ambiguity. **Promoted from should-fix to must-fix on panel review** (Implementation realist's correctness escalation).

### File ops

- **move_file** description claims it rejects raw/ and underscore dirs but the rejection happens inside `Reorganizer.move_single` and surfaces as a generic ValueError; the tool itself does no `_is_system_path` check. `pal/tools/vault.py:243-256`. Add the same `_is_system_path` precheck other vault tools use, for symmetry.

### Utility

- **url_fix** writes the article but never triggers reindex; search_vault returns stale content. `pal/tools/url_fix.py:200`. After `write_text`, call `retrieval.trigger_reindex(paths=[full_path])` and include `job_id` in the response.
- **url_fix**: `agent_core.approval_registry.ProposalKind` Literal does not include `"url_fix"` (or `"learning_candidate"`). `/home/edible/Projects/agent_core/agent_core/approval_registry.py:20`. Add both to the Literal.

### Slash commands

- **/scratch read** does not work; PAL's `Scratch` override has no `read` or `clear` branch (the framework builtin does, but PAL's subclass shadows it). `pal/commands/domain.py:861-907`. Port the framework builtin's read/clear branches into PAL's override.
- **/think on** toggles correctly and PALAgent emits `ResponseMessage(reasoning=...)`, but `DiscordStreamProcessor.run` discards `msg.reasoning` entirely. `pal/discord_interactions.py:645-647`. When `ResponseMessage` has non-empty reasoning AND the channel's effective display is "show", prepend a quoted reasoning block to `final_text`. CLI does this at `cli.py:529-535`; mirror that branch.

## Should-fix / nice-to-have

### Retrieval

- search_web: no engine/category surfaced; SearxNG returns engine attribution and PAL strips it. Add to SearchResult and render.
- search_vault: optional `tags` parameter exposed by RetrievalClient.search isn't surfaced.

### File ops

- replace_in_file: empty-`old_string` check conflates "missing parameter" with "empty string" (`pal/tools/vault.py:416-417`). Use `if old_string is None`.
- replace_in_file: tests don't exercise reindex-failure path explicitly; add test mirroring `test_delete_file_surfaces_reindex_failure`.
- replace_in_file: when count > 1, error tells PAL to widen `old_string` but doesn't show match line numbers.
- edit_file: no test for commit-failure path; add `test_edit_file_restores_on_commit_failure`.
- edit_file: parameter `content` rejects empty string as missing (`pal/tools/vault.py:81-82`).
- create_file: required `title` parameter is friction-prone; default to `Path(path).stem`.
- delete_file: when commit fails, warning doesn't include a concrete recovery hint string for PAL to relay.
- delete_file: JSON warning omits whether reindex was attempted; include `"reindex":"skipped"`.
- move_file: no commit-failure handling (covered in cross-cutting; listed here for category completeness).
- move_file: progress-label dict in `pal/discord_adapter.py:75-85` has no entry for move_file/delete_file/replace_in_file.

### Compile

- compile_summary: returns full `compiled_truth` body to LLM with no documented use; strip from tool result (`pal/compiler.py:286-291`).
- compile_summary: no `vault_exists` ground-truth echo; mirror compile_batch (`pal/tools/compile.py:36-43`).
- compile_summary: description doesn't mention `insufficient`/`too_large`/`not_found` outcomes.
- propose_compile_batch: approval prompt shows paths + rationale but no titles or sizes; peek frontmatter and render.
- propose_compile_batch: declined-with-edited-successor branch reads `edited.summary_paths` without re-validation (`pal/tools/compile.py:108-114`).
- propose_compile_batch: description doesn't tell LLM that decline-with-edits returns as approved with edited paths.
- compile_batch: when all per-file outcomes are errors/insufficient, no `overall_status` field; LLM may misnarrate success (`pal/tools/compile.py:196-208`).
- compile_batch: `not_found`/`invalid_path` entries don't surface why path was bad.

### Consolidate / Promote synthesis

- propose_consolidate: target_path collision discovered only after approval (`pal/tools/consolidate.py:55-67`). Stat target pre-approval.
- propose_consolidate: declined-with-successor branch is plausibly dead (CLI/Discord both treat [e]dit as unconditional decline). Either remove or wire the modal.
- propose_consolidate: approval prompt doesn't show that source articles will NOT be archived; add one-line footer.
- propose_consolidate: blurple color shared with research embed; pick distinct color.
- consolidate: when inner Consolidator raises, catch-all hard-codes `vault_exists` to False; should stat target (`pal/tools/consolidate.py:160-179`).
- consolidate: `_note` field injected into outcome dict mutates the consolidator return shape; drop or formalize.
- propose_promote_synthesis: `summary_collision` detected post-approval; stat `summary_full` pre-`create_proposal`.
- propose_promote_synthesis: no size guard pre-approval; oversized notes fail with `too_large` after approval.
- propose_promote_synthesis: no test exercises missing-required-sections path through the tool (add after must-fix lands).
- propose_promote_synthesis: `needs_consolidate` status bubbles up but tool description doesn't mention this branch and result has no field naming the existing article path explicitly.
- propose_promote_synthesis: description says "directly under raw/notes/" but check allows nested subdirs.

### Knowledge management

- propose_research: depth bound (1-10) is silently clamped (`pal/tools/research.py:84-85`). Surface "depth clamped to N" in returned JSON.
- propose_reorg: `references_preview` total is a single int across all srcs (`pal/tools/reorg.py:84`); return per-src counts.
- propose_promote: `propose_promote` not mentioned in `pal/prompts/system.py` (covered in cross-cutting prompt fix; listed here for category).
- propose_promote: success path returns only `{status, slug, title}`; include new wisdom file path (`pal/tools/reorg.py:219`).

### Utility

- url_fix: `_thread_name_for_current_proposal` has no branch for url_fix; falls through to "compile: 0 summaries" misnomer (`pal/discord_interactions.py:784`).
- wait_for_reindex: returns "unknown job_id" as plain `Error:`-prefix string while other paths return JSON (`pal/tools/wait.py:43, 55, 46`).

### Slash commands

- /scratch: args descriptor is `<text>` so /help hides that read/clear are even possibilities (`pal/commands/domain.py:864`); update to `[clear | <text>]`.
- /scratch: empty scratchpad with no args could explicitly say "(scratchpad is empty)" after read branch lands.
- /context: reports bytes for everything but tokens only for last-turn (`agent_core/agent_core/commands/_builtin_impls.py:404-410`); add "Approx total" tokens line.
- /context: no comparison to model's context window cap; if config carries `context_window`, add "/ <cap>".
- /status: doesn't show reasoning display preference; expose per-channel state once Discord reasoning ships.
- /research: argument grammar mixes flag and positional; `/research deep <topic>` works but `/research <topic> deep` treats deep as part of topic (`pal/commands/research.py:32-40`). Promote `deep` to `--deep`.
- /research: depth=10 vs depth=3 binary opaque from command surface; mention "(depth 3, or 10 with --deep)" in description.
- /model: `/model <name>` with unknown name silently sets `default_model`; typo leaves agent pointed at non-existent model (`pal/commands/domain.py:951-952`). Validate against `/v1/models` before mutating.

## Candidates for individual brainstorming (needs_spec items)

- **Validation timing + slug reservation** (cross-cutting): the validation-timing fix introduces a duplicate-validation invariant and a TOCTOU window for `summary_collision`. Spec must address shared validator extraction and slug reservation (or post-approval re-stat) before implementation. Promoted from no-spec to needs_spec on panel review.
- **Vault-write success shape and reindex propagation** (cross-cutting): bundled spec covering canonical JSON shape across all five vault tools PLUS reindex `{job_id, status}` propagation. Includes pre-spec verification of `RetrievalClient.trigger_reindex` return contract. Replaces the original two separate items per panel review.
- **search_vault structured/JSON result format** (retrieval): the shift from human-prose to structured per-result lines (path, score, snippet) or JSON changes the LLM-facing contract; worth its own design pass.
- **Vault-tool 404 nearest-match suggestion** (retrieval): cuts across all vault read tools (cat/edit/grep), not just search_vault; needs a shared helper and a story for index-vs-filesystem fuzzy matching.
- **search_web allowlist filter contract** (retrieval): two surfaces (slash command and LLM tool) with the same name and divergent filtering; pick a single contract before diverging further.
- **propose_consolidate dead [e]dit branch** (consolidate): decide whether to remove `get_successor` handling or actually wire the edit modal across CLI and Discord.
- **/think Discord reasoning rendering** (slash commands): touches DiscordStreamProcessor, channel-scoped overrides store, and the show/hide control surface; Phase 2 inference investigation territory.
- **/think show / /think hide per-channel persistence on Discord** (slash commands): currently CLI-only; Discord needs `conv.overrides["reasoning_display"]` plumbing and an interaction with the cross-cutting reasoning render fix.
- **/model PAL override vs framework builtin shadowing** (slash commands): decide whether to delete the framework builtin or pull PAL's branches up; affects every other command that follows the override pattern.
- **url_fix CLI [e]dit branch** (utility): silently decodes to decline today; same shape as the consolidate dead-branch question; either implement or change the prompt.

## Disagreements surfaced during synthesis

### Splash-page memory: RESOLVED (stale)

The slash-commands subagent flagged that the user-facing memory item "Splash page needs cleanup" (missing /research /model /think) may be out of date because `cli.py:53-65` already iterates the dynamic command union. **Confirmed during panel-review revision pass:** `render_splash_commands()` at `pal/cli.py:53-65` calls `_all_command_classes()` which dynamically merges `BUILTIN_COMMANDS + PALAgent.commands`. The splash page IS dynamic; the memory item is stale. Retire the `project_splash_cleanup` memory note.

### Phase E learning-flow concern: RESOLVED (wire-up intact)

The knowledge-management subagent recorded that the previously flagged Phase E learning-flow wire-up concern was resolved by code-reading. No conflict with other reports. Treat the `project_phase_e_post_extraction_review` memory item's learning-flow bullet as resolved for the purposes of this audit; /scratch read and Discord /think rendering remain as the active Phase E carry-overs (both captured as must-fixes above).

No other contradictions between subagents were detected during synthesis; the seven reports were largely orthogonal by category and converged on the cross-cutting patterns enumerated above.

## Panel revisions applied

This report was revised after a 4-reviewer panel (Architecture coherence, YAGNI skeptic, Implementation realist, API consumer (PAL's-eye)). Five revisions were applied before acceptance:

1. **Bundled reindex-contract + return-shape into one cross-cutting must-fix** (Architecture concern #1, Realist confirmed move_file uses a third name `reindex_queued`). Land canonical success shape first, then propagate reindex into it.
2. **Promoted "Vault read 404s lack nearest-match suggestions" from should-fix to must-fix** (API consumer: PAL's explicit ask #2 from the path-determinism feedback was demoted in error). Now a cross-cutting must-fix in its own right.
3. **Promoted `propose_promote_synthesis` consume-before-write from should-fix to must-fix** (Implementation realist's correctness escalation). It's an unrecoverable-state correctness bug, not polish.
4. **Named the duplicate-validation invariant and the TOCTOU window in the validation-timing fix** (Architecture concern #2 + Realist concern #1). Pre-stat alone does not eliminate `summary_collision`; spec must address slug reservation or post-approval re-stat. Fix bumped to `needs_spec: yes`.
5. **Added verification gap for `RetrievalClient.trigger_reindex` return shape** (Realist verification gap). Audit assumes `{job_id, status}` return; confirm before depending. Inlined into the bundled vault-write spec.

Panel suggestions NOT applied:
- YAGNI: delete `propose_promote_synthesis` outright. Rejected; the tool is one day old and friction is implementation-incomplete (the cross-cutting validation-timing and consume-before-write fixes resolve the named concerns). Distinct contract is genuinely worth keeping.
- YAGNI: drop most prompt catalog fixes. Rejected; Realist verified the staleness verbatim, all four are real bugs. Bundle into one prompt-edit task to address YAGNI's cost concern.
- YAGNI keystone: pull telemetry before defending the surface. Deferred; no logs to mine right now. Future re-audit can refine verdicts with usage data.
