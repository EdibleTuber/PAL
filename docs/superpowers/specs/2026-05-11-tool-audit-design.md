# PAL Tool Audit

**Date:** 2026-05-11
**Status:** Design
**Author:** Brainstormed with Claude

## Problem

PAL has accreted roughly twenty tool surfaces (chat tools + slash commands) over its lifetime. Some are daily drivers, some are vestigial, several overlap. PAL itself self-reported (on 2026-05-10) that **path determinism** is its dominant tool-use bottleneck: `search_vault` returns semantic snippets without exact paths, batch tools return "blind" errors with no "did you mean" suggestions, and PAL ends up looping on guessed filenames.

The chat-derived promotion workstream surfaced a related observation: the existing compile pipeline had a gate (`source_url` or `source_file` required) doing real work, but no clear path for valid-but-non-URL provenance. That problem class likely repeats across the tool surface.

A systematic audit is overdue. Without one, fixes are reactive, friction accumulates, and PAL's second agent (planned, per memory) inherits the same blind error messages and guessable-string parameters.

## Goals

1. Produce a unified, prioritized fix queue across all chat tools and slash commands.
2. Apply a deletion lens: for every tool, ask whether it earns its place. Cumulative blast radius of all proposed deletes is part of the report.
3. Apply a consolidation lens: identify tools whose jobs overlap and could merge.
4. Apply a friction lens informed by PAL's stated bottlenecks (path determinism, blind errors, parameter producibility).
5. Surface items big enough to need their own design specs separately from small fixes.
6. Run the unified report through an independent expert review panel before treating it as the source of decisions.

## Non-goals

1. **Implementing fixes.** This audit produces a report and a queue, not code changes. Items flagged `needs_spec: true` get their own brainstorming workstreams. Small items get queued for direct implementation.
2. **Re-litigating the chat-derived promotion design.** The chat-promotion workstream just shipped; its tools (`propose_promote_synthesis`, `compile_chat_synthesis`) are in scope for review but their architecture is settled.
3. **Restructuring agent_core.** Cross-package boundary changes are out of scope. The audit may surface "this would be cleaner if agent_core exposed X" but won't drive agent_core changes from this report.
4. **Auditing the inference path or prompt construction.** Those are deferred to the Phase 2 inference investigation (per memory: `project_phase2_inference_investigation`). The prompt audit specifically can ride on this audit pass when convenient but isn't a goal.
5. **Producing complete fix specs inside this report.** The audit identifies what needs fixing; brainstorming produces specs for the load-bearing items.

## Rubric

Each tool gets evaluated against these questions. Daily-driver categories answer all bullets; lighter-pass categories answer the starred ones only.

### Existence and overlap (the deletion lens)

- **\* Does it still earn its place?** Is there a real-recent use case, or is it vestigial?
- **\* Does it overlap another tool?** Could one tool's contract subsume another's (e.g., move with empty dest replacing delete)?
- **\* If we deleted it tomorrow, what breaks?** "Nothing" is a valid and decisive answer.

### Interface quality (the friction lens)

- **\* Are the parameters things PAL can reliably produce?** Specifically, anything where the LLM has to predict a literal string (paths, slugs, message IDs). PAL's path-determinism feedback is the cardinal example.
- **\* Does the success result give PAL what it needs to act next?** A write tool that returns the path it wrote saves PAL from predicting it later.
- **\* Are error messages actionable?** "File not found" alone is blind. "File not found; nearest matches: foo.md, foo-v2.md" is actionable.
- **Does the prompt description match what the tool actually does?** Drift is common after refactors.
- **Does the tool's docstring match its description string in the prompt?** Two sources of truth, often diverge.

### Behavior quality

- **Does it have tests that exercise the happy path AND the documented error paths?**
- **Does it correctly handle async coordination?** Post-write reindex, approval blocking, etc.
- **For propose/execute pairs: does the approval prompt show enough info for an informed yes?**

### Per-tool output

- **Verdict:** `keep` / `consolidate-with-<other>` / `delete`
- **If keep:** severity-tiered findings (must-fix / should-fix / nice-to-have), each with code pointer, one-line recommendation, `needs_spec` flag.
- **If consolidate:** which other tool, and why the merge is cheaper than fixing separately.
- **If delete:** what breaks, what replaces it.

## Category breakdown

Seven categories, four deep + three light. One subagent per category, dispatched in parallel.

### Deep categories (full rubric)

| # | Category | Tools |
|---|----------|-------|
| 1 | Retrieval | `search_vault`, `search_web` |
| 2 | File ops | `edit_file`, `create_file`, `delete_file`, `replace_in_file`, `move_file` |
| 3 | Compile | `compile_summary`, `propose_compile_batch`, `compile_batch` |
| 4 | Consolidate + Promote synthesis | `propose_consolidate`, `consolidate`, `propose_promote_synthesis` |

### Light categories (starred bullets only)

| # | Category | Tools |
|---|----------|-------|
| 5 | Knowledge management | `propose_research`, `research_topic`, `propose_reorg`, `reorg`, `propose_promote` (learning→wisdom) |
| 6 | Utility tools | `url_fix`, `wait_for_reindex` |
| 7 | Slash commands | `/scratch`, `/context`, `/status`, `/think`, `/research`, `/model` |

### Notes on category placement

- `propose_promote_synthesis` is in category 4 even though brand new. It's being smoke-tested in prod by the user concurrent with this audit; smoke results inform its review.
- `wait_for_reindex` is a likely delete candidate per PAL's feedback that the `reindex` field already signals completion.
- `learning→wisdom propose_promote` stays light despite memory flagging concerns from Phase E smoke; if the audit surfaces real friction, the verdict can promote it to a deeper follow-up audit later.
- Slash commands are user-facing not LLM-facing, so the rubric applies differently. The "Are parameters things PAL can reliably produce?" bullet becomes "Are arguments things the user can remember?" The "Does success result give PAL what it needs?" bullet becomes "Does success output give the user clear feedback?"

## Subagent dispatch shape

### Stage 1: 7 category subagents (parallel)

Each subagent receives:

1. Category name and tool list.
2. Depth tier (deep / light) and the corresponding rubric bullets.
3. Code pointers per tool: the source file, the test file(s), the prompt description in `pal/prompts/system.py`, and the call sites (grep'd ahead of time and included in the prompt).
4. Memory excerpts relevant to their category. The retrieval subagent gets the path-determinism feedback verbatim. The compile subagent gets the consolidate/article framing memories.
5. The per-tool YAML output template (below).

### Per-tool YAML output template

```yaml
tool: <name>
verdict: keep | consolidate-with-<other> | delete
overlap_with: [<other tools that do similar work>]
if_deleted_breaks: <one line; "nothing" is a valid answer>
findings:
  must_fix:
    - issue: <one line>
      where: <file:line>
      recommendation: <one line>
      needs_spec: true | false
  should_fix:
    - <same shape>
  nice_to_have:
    - <same shape>
notes: <free text>
```

### Subagent discipline

Each subagent **must read the actual implementation**, not just the docstring. The implementation realist's review of the chat-promotion spec caught fictional plumbing the first reviewers missed by reading code instead of trusting summaries. Same discipline applies here.

### Stage 2: 1 synthesis subagent

Consumes the seven category reports. Produces the unified prioritized fix queue. Mechanical synthesis, not new judgment. Specifically:

- Reads all seven YAML outputs.
- Builds four cross-cutting sections of the unified doc:
  1. **Deletes:** all `delete` verdicts, with cumulative blast radius.
  2. **Consolidate clusters:** `consolidate-with-X` verdicts as a graph, identifying merge clusters.
  3. **Must-fix deduped:** must-fixes across categories, deduped where one fix unblocks multiple tools (e.g., path-determinism change that lands once and affects retrieval + file ops + compile).
  4. **Should-fix + nice-to-have:** grouped by category, light formatting.
- Flags any items where two subagents disagree.
- Flags items marked `needs_spec: true` in a final "candidates for brainstorming" section.
- Writes the unified doc to `docs/superpowers/audits/2026-05-11-tool-audit-report.md` and commits it.

The synthesis subagent gets fresh context. It does NOT inherit the brainstorming session.

### Stage 3: 4-reviewer expert panel (parallel)

Each reviewer gets the unified report and a distinct lens.

| Reviewer | Lens | Key question |
|----------|------|--------------|
| Architecture coherence | Boundaries, coupling, second-order effects | Do the verdicts hang together as a surface? Does any delete or consolidation break a cross-tool invariant the audit didn't see? |
| YAGNI skeptic | Aggressive deletion | Are there `keep` verdicts that should really be `delete`? Are there `must-fix` items that aren't load-bearing? Push hard for less. |
| Implementation realist | What breaks in practice | Sample 2-3 high-impact verdicts, read the actual code (not just the report), confirm nothing was fabricated or missed. |
| API consumer (PAL's-eye) | Friction reduction from LLM consumer's view | For each proposed fix, would PAL's reasoning actually have an easier time, or is the fix just shuffling complexity? |

Each panel reviewer returns a bounded report: top 2-3 concerns, top 1-2 defenses, one keystone question. Caps at ~600 words.

### Stage 4: Human-readable synthesis

I (the controller) read the four panel reports and produce a debate summary:

- Where reviewers unanimously pushed back.
- Where reviewers disagreed with each other.
- Where the panel agrees the audit got it right.
- A recommended set of revisions to the audit report (or "accept as-is" if no substantive concerns).

The user reviews the synthesis and decides whether to revise the audit, accept as-is, or change direction.

### Stage 5: Audit report becomes the source of decisions

After approval:

- Items flagged `needs_spec: true` become their own brainstorming workstreams (one spec per item).
- Items small enough get added to a backlog (or implemented directly if trivial).
- The `deletes` and `consolidates` sections become a single follow-up implementation plan.

## Output document structure

The unified audit report at `docs/superpowers/audits/2026-05-11-tool-audit-report.md` has this shape:

```markdown
# PAL Tool Audit Report

**Date:** 2026-05-11
**Status:** Draft → Reviewed → Accepted

## Summary
- Total tools audited: N
- Verdicts: K keep / C consolidate / D delete
- Cross-cutting fixes: M
- Items needing their own specs: S

## Deletes (D tools)
For each: tool name, blast radius, replacement (if any)

## Consolidate clusters (C tools across G clusters)
For each cluster: tools, merged contract sketch, why merge is cheaper

## Cross-cutting must-fix (deduped)
Findings that touch multiple tools, with affected tools listed

## Must-fix by category
(remaining must-fixes that are tool-specific)

## Should-fix / nice-to-have
Grouped by category, terse

## Candidates for individual brainstorming (needs_spec items)
One line each, with category and rationale

## Disagreements surfaced during synthesis
Cases where two subagents took conflicting positions
```

## Risks

1. **Subagent inconsistency.** Two subagents may apply the rubric differently. Mitigation: shared rubric in every dispatch prompt, synthesis subagent flags disagreements, panel review catches drift.
2. **Audit fatigue.** Twenty tools is a lot; subagents may pattern-match to "looks fine" without reading carefully. Mitigation: each subagent has to fill the YAML template, including the `if_deleted_breaks` field, which forces real engagement per tool.
3. **Over-deletion.** YAGNI skeptic on the panel could push the report toward deletes that lose useful capability. Mitigation: blast-radius answer per delete; user has final say.
4. **Under-deletion.** Conservative subagents may default to `keep` for tools they don't fully understand. Mitigation: YAGNI panel reviewer's whole job is to challenge keeps.
5. **Spec sprawl.** If too many items get marked `needs_spec`, the follow-up workload balloons. Mitigation: synthesis subagent and panel both push to merge related needs_spec items into single specs where possible.

## Verification

After the panel debate and user acceptance:
- The audit report committed at `docs/superpowers/audits/2026-05-11-tool-audit-report.md` is the canonical source for tool-related decisions until superseded.
- Each `needs_spec` item that becomes a workstream references this report in its own spec.
- The deletes/consolidates implementation plan references this report.
- A future re-audit can diff its findings against this one to measure progress.

## Migration

No migration. This is a process document and a generated report. The report's recommendations are implemented separately.

## Out of scope

- Implementing any fix found by the audit (separate workstream per item).
- Prompt audit details (deferred to Phase 2 inference investigation; this audit may surface prompt-description-mismatch findings but won't redesign the prompt).
- agent_core changes (cross-package; out of scope for this PAL audit).
- The 5+ pre-existing `pal.client` test collection failures (`project_pal_client_test_cleanup` workstream).
- Recommending new tools that don't exist yet.
