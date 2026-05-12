# PAL Tool Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the five-stage tool audit pipeline defined in `docs/superpowers/specs/2026-05-11-tool-audit-design.md`, producing a panel-reviewed, user-accepted audit report at `docs/superpowers/audits/2026-05-11-tool-audit-report.md`.

**Architecture:** This is an orchestration plan, not a code-change plan. The controller (running this plan) dispatches subagents in three rounds: seven category reviewers in parallel, one synthesis agent, then four panel reviewers in parallel. The controller integrates the panel debate, presents it to the user, and finalizes the report. No PAL or agent_core source code changes; the only file produced is the audit report itself.

**Tech Stack:** Subagent dispatch (Agent tool), shell-side grep/find, git for committing the report.

---

## Scope check

Single coherent workstream. The audit report is a single artifact, even though it covers many tools. No decomposition needed.

## File Structure

**Created:**
- `docs/superpowers/audits/` (new directory)
- `docs/superpowers/audits/2026-05-11-tool-audit-report.md` (the unified report; written by Stage 2 synthesis subagent)
- `docs/superpowers/audits/README.md` (one-line directory description)

**Not modified:** no PAL or agent_core source files. Implementation of fixes happens in follow-up workstreams keyed off the report.

---

## Task 1: Create the audits directory and stub README

**Files:**
- Create: `docs/superpowers/audits/README.md`

- [ ] **Step 1: Create the directory and stub README**

```bash
mkdir -p /home/edible/Projects/PAL/docs/superpowers/audits
```

Then write `docs/superpowers/audits/README.md`:

```markdown
# Tool and System Audits

This directory holds generated audit reports about PAL's tool surface,
prompt construction, and other systemic concerns. Each report is panel-
reviewed before being treated as the source of decisions for follow-up
workstreams.

Audit specs live in `docs/superpowers/specs/`.
```

- [ ] **Step 2: Commit**

```bash
cd /home/edible/Projects/PAL && git add docs/superpowers/audits/README.md && git commit -m "$(cat <<'EOF'
docs: scaffold superpowers/audits directory

Houses panel-reviewed audit reports. First inhabitant will be the tool
audit landing in a follow-up commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Dispatch 7 category subagents in parallel

**Files:** None modified. Output is captured in subagent reports (returned as tool results to the controller).

This is one task with seven concurrent subagent dispatches. All seven must be sent in a single message containing seven Agent tool calls so they run in parallel.

- [ ] **Step 1: Construct the shared rubric block (used in every dispatch)**

Every category subagent gets this rubric in its prompt. Do NOT abbreviate it; use the full text:

```
## Audit rubric

For each tool in your category, answer the rubric questions and produce
a YAML report block.

### Existence and overlap (the deletion lens) -- ALL categories

* Does it still earn its place? Is there a real-recent use case, or is it vestigial?
* Does it overlap another tool? Could one tool's contract subsume another's?
* If we deleted it tomorrow, what breaks? "Nothing" is a valid answer.

### Interface quality (the friction lens) -- ALL categories

* Are the parameters things PAL can reliably produce? (literal strings the LLM has to predict)
* Does the success result give PAL what it needs to act next?
* Are error messages actionable? ("File not found" alone is blind; "File not found; nearest matches: foo.md, foo-v2.md" is actionable.)

### Behavior quality -- DEEP categories only

* Does the prompt description match what the tool actually does?
* Does the tool's docstring match its description string in the prompt?
* Does it have tests that exercise happy path AND documented error paths?
* Does it correctly handle async coordination (post-write reindex, approval blocking)?
* For propose/execute pairs: does the approval prompt show enough info for an informed yes?

### Per-tool YAML output (one block per tool)

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

### Discipline

You MUST read the actual implementation files for each tool, not just
the docstring. The implementation realist's review of the chat-promotion
spec caught fictional plumbing the first reviewers missed by reading
code instead of trusting summaries. Same discipline here.
```

- [ ] **Step 2: Construct per-category dispatch prompts and send all 7 in one message**

Each subagent's prompt is the rubric block above + a category preamble below. Dispatch all seven Agent calls in one message so they run concurrently.

**Subagent 1: Retrieval (DEEP)**
```
You are reviewing PAL's RETRIEVAL category as part of a tool audit.

Tools to audit: search_vault, search_web

Locations to read:
- search_vault: /home/edible/Projects/agent_core/agent_core/tools/_framework.py (look for class with `name = "search_vault"`)
- search_web: pal/tools/research.py and pal/researcher.py (search for "search_web")
- PAL prompt entries: grep "search_vault\|search_web" /home/edible/Projects/PAL/pal/prompts/system.py
- Test files: ls /home/edible/Projects/PAL/tests/ | grep -i "search\|retriev"
- Real call sites: grep -rn "search_vault\|search_web" /home/edible/Projects/PAL/pal/ /home/edible/Projects/PAL/scripts/ --include="*.py"

Memory context (verbatim -- read carefully, this is the consumer's stated friction):

[paste content of /home/edible/.claude/projects/-home-edible-Projects-PAL/memory/project_pal_path_determinism.md]

Apply the FULL rubric (deep tier). Pay particular attention to whether
search_vault returns exact paths in its results (the path-determinism
concern) and whether search_web's success format gives PAL what it
needs to act next.

[shared rubric block]

Return your YAML report blocks (one per tool) plus a brief notes section
on cross-tool patterns within retrieval.
```

**Subagent 2: File ops (DEEP)**
```
You are reviewing PAL's FILE OPS category as part of a tool audit.

Tools to audit: edit_file, create_file, delete_file, replace_in_file, move_file

Locations to read:
- /home/edible/Projects/PAL/pal/tools/vault.py (most file ops live here)
- PAL prompt entries: grep "edit_file\|create_file\|delete_file\|replace_in_file\|move_file" /home/edible/Projects/PAL/pal/prompts/system.py
- Test files: ls /home/edible/Projects/PAL/tests/ | grep -i "vault\|file"
- Real call sites: grep -rn "edit_file\|create_file\|delete_file\|replace_in_file\|move_file" /home/edible/Projects/PAL/pal/ /home/edible/Projects/PAL/scripts/ --include="*.py"
- Recent changes: git log --oneline -10 -- pal/tools/vault.py

Apply the FULL rubric. This category is the biggest deletion-lens
target: five tools that all write to the vault. Specifically evaluate
whether some can subsume others (move with empty dest replacing delete,
replace_in_file replacing targeted edit_file).

[shared rubric block]

Return your YAML report blocks plus cross-tool pattern notes.
```

**Subagent 3: Compile (DEEP)**
```
You are reviewing PAL's COMPILE category as part of a tool audit.

Tools to audit: compile_summary, propose_compile_batch, compile_batch

Locations to read:
- /home/edible/Projects/PAL/pal/tools/compile.py
- /home/edible/Projects/PAL/pal/compiler.py (the underlying engine)
- PAL prompt entries: grep "compile_summary\|propose_compile_batch\|compile_batch" /home/edible/Projects/PAL/pal/prompts/system.py
- Test files: ls /home/edible/Projects/PAL/tests/ | grep -i compile
- Real call sites: grep -rn "compile_summary\|propose_compile_batch\|compile_batch" /home/edible/Projects/PAL/pal/ --include="*.py"

Memory context (verbatim):

[paste content of /home/edible/.claude/projects/-home-edible-Projects-PAL/memory/project_articles_are_substrate.md]

Apply the FULL rubric. The chat-promotion workstream just shipped a
sibling compile path (compile_chat_synthesis); compare validation
timing, approval flow, and provenance handling across the three batch
tools. The "validation fires after user approval" pattern observed in
the smoke test for promote_synthesis may have analogs here.

[shared rubric block]
```

**Subagent 4: Consolidate + Promote synthesis (DEEP)**
```
You are reviewing PAL's CONSOLIDATE + PROMOTE SYNTHESIS category as part
of a tool audit.

Tools to audit: propose_consolidate, consolidate, propose_promote_synthesis

Locations to read:
- /home/edible/Projects/PAL/pal/tools/consolidate.py
- /home/edible/Projects/PAL/pal/tools/promote_synthesis.py
- /home/edible/Projects/PAL/pal/consolidator.py
- /home/edible/Projects/PAL/pal/compiler.py (compile_chat_synthesis and merge_chat_synthesis_into_existing live here)
- PAL prompt entries: grep "propose_consolidate\|consolidate\|propose_promote_synthesis" /home/edible/Projects/PAL/pal/prompts/system.py
- Test files: tests/test_consolidate*.py, tests/test_compile_chat_synthesis.py, tests/test_promote_synthesis*.py
- Real call sites: grep -rn

Memory context (verbatim):

[paste content of /home/edible/.claude/projects/-home-edible-Projects-PAL/memory/project_promote_synthesis_smoke_findings.md]
[paste content of /home/edible/.claude/projects/-home-edible-Projects-PAL/memory/project_consolidate_tool_shipped.md]
[paste content of /home/edible/.claude/projects/-home-edible-Projects-PAL/memory/project_promotion_requires_source.md]

Apply the FULL rubric. Pay particular attention to the validation-timing
issue surfaced in propose_promote_synthesis smoke testing (validation
fires AFTER user approval, forcing recovery loops). Check whether
propose_consolidate has the same shape problem.

[shared rubric block]
```

**Subagent 5: Knowledge management (LIGHT)**
```
You are reviewing PAL's KNOWLEDGE MANAGEMENT category as part of a tool
audit. LIGHT tier: rubric STARRED bullets only.

Tools to audit: propose_research, research_topic, propose_reorg, reorg, propose_promote (learning to wisdom)

Locations to read:
- pal/tools/research.py
- pal/tools/reorg.py
- pal/researcher.py
- pal/reorg.py
- PAL prompt entries: grep -n "propose_research\|research_topic\|propose_reorg\|^- reorg\|propose_promote\b" /home/edible/Projects/PAL/pal/prompts/system.py
- Test files: ls /home/edible/Projects/PAL/tests/ | grep -i "research\|reorg\|promote\|learning\|wisdom"

Memory context (verbatim):

[paste content of /home/edible/.claude/projects/-home-edible-Projects-PAL/memory/project_phase_e_post_extraction_review.md]
[paste content of /home/edible/.claude/projects/-home-edible-Projects-PAL/memory/project_url_fix_followups.md]

LIGHT TIER: Apply only the STARRED rubric bullets (existence/overlap +
parameter producibility + success-output usefulness + error
actionability). Do NOT spend time on docstring/prompt drift, test
coverage details, or async coordination unless something is obviously
broken.

[shared rubric block]

Note: propose_promote (learning -> wisdom) is a different concept from
propose_promote_synthesis (chat -> wiki). Don't conflate them.
```

**Subagent 6: Utility tools (LIGHT)**
```
You are reviewing PAL's UTILITY TOOLS category as part of a tool audit.
LIGHT tier: rubric STARRED bullets only.

Tools to audit: url_fix, wait_for_reindex

Locations to read:
- pal/tools/url_fix.py
- pal/tools/wait.py
- PAL prompt entries: grep -n "url_fix\|wait_for_reindex" /home/edible/Projects/PAL/pal/prompts/system.py
- Test files: tests/test_url_fix*.py, tests/test_wait*.py (if they exist)

Memory context (verbatim):

[paste content of /home/edible/.claude/projects/-home-edible-Projects-PAL/memory/project_vector_index_freshness.md]
[paste content of /home/edible/.claude/projects/-home-edible-Projects-PAL/memory/project_url_fix_followups.md]

LIGHT TIER: starred bullets only.

Specifically evaluate whether wait_for_reindex still earns its place now
that the reindex field on write tool outputs signals completion. PAL's
own feedback (path-determinism memory) explicitly named the reindex
field as working well, so the explicit wait tool may be vestigial.

[shared rubric block]
```

**Subagent 7: Slash commands (LIGHT)**
```
You are reviewing PAL's SLASH COMMANDS category as part of a tool audit.
LIGHT tier: rubric STARRED bullets only.

Slash commands to audit: /scratch, /context, /status, /think, /research, /model

Locations to read:
- /home/edible/Projects/PAL/pal/commands/ (slash command implementations)
- /home/edible/Projects/PAL/pal/cli.py (CLI dispatch)
- /home/edible/Projects/PAL/pal/discord_interactions.py (Discord command handling)

Memory context (verbatim):

[paste content of /home/edible/.claude/projects/-home-edible-Projects-PAL/memory/project_splash_cleanup.md]
[paste content of /home/edible/.claude/projects/-home-edible-Projects-PAL/memory/project_reasoning_not_shown_in_discord.md]
[paste content of /home/edible/.claude/projects/-home-edible-Projects-PAL/memory/project_per_channel_context.md]

LIGHT TIER with adapted rubric for user-facing tools:

* Does it still earn its place? (Same as for chat tools.)
* Does it overlap another command?
* Are arguments things THE USER can remember? (Adapted from "things PAL can reliably produce".)
* Does success output give the USER clear feedback? (Adapted from "give PAL what it needs to act next".)
* Are error messages actionable for the USER?

PAL feedback names /context and /status as vital state-awareness tools.
Don't propose deleting them. /think on does NOT currently render
reasoning in Discord (per reasoning-not-shown memory) -- flag as a
must-fix or document the limitation.

[shared rubric block]
```

- [ ] **Step 3: Wait for all seven subagent reports to return**

The Agent tool will notify when each completes. Capture all seven YAML reports verbatim (do not paraphrase) for use in Task 3.

- [ ] **Step 4: Verify completeness**

Quick check before proceeding:
- All 7 reports returned (no BLOCKED status)
- Each report contains YAML blocks for every tool in its category
- No report is empty or trivially short (under 500 words is suspicious)

If any subagent returned BLOCKED or trivially short, dispatch a fix subagent for just that category.

---

## Task 3: Dispatch the synthesis subagent

**Files:**
- Create: `docs/superpowers/audits/2026-05-11-tool-audit-report.md` (written by the synthesis subagent)

- [ ] **Step 1: Dispatch the synthesis subagent with all 7 category reports**

Single Agent call with this prompt:

```
You are the synthesis subagent for PAL's tool audit. You consume seven
category reports and produce ONE unified prioritized fix queue.

Your job is MECHANICAL synthesis, not new judgment. Do not introduce
findings the category subagents did not surface. Your value is
deduplication, cross-cutting pattern detection, and clear sectioning.

## Inputs

Below are the seven category reports. Each contains YAML blocks (one per
tool) with verdict, findings, and notes.

### Category 1: Retrieval
[paste subagent 1 output verbatim]

### Category 2: File ops
[paste subagent 2 output verbatim]

### Category 3: Compile
[paste subagent 3 output verbatim]

### Category 4: Consolidate + Promote synthesis
[paste subagent 4 output verbatim]

### Category 5: Knowledge management
[paste subagent 5 output verbatim]

### Category 6: Utility tools
[paste subagent 6 output verbatim]

### Category 7: Slash commands
[paste subagent 7 output verbatim]

## Your output

Write the unified audit report to /home/edible/Projects/PAL/docs/superpowers/audits/2026-05-11-tool-audit-report.md
with this exact structure:

```markdown
# PAL Tool Audit Report

**Date:** 2026-05-11
**Status:** Draft (panel review pending)
**Spec:** docs/superpowers/specs/2026-05-11-tool-audit-design.md

## Summary

- Total tools audited: <N>
- Verdicts: <K> keep / <C> consolidate / <D> delete
- Cross-cutting fixes (deduped): <M>
- Items needing their own specs: <S>

## Deletes (<D> tools)

For each tool with verdict `delete`, in a subsection:
### <tool-name>
- **Blast radius:** <one line from `if_deleted_breaks`>
- **Replacement:** <if any>

## Consolidate clusters (<C> tools across <G> clusters)

For each merge cluster:
### Cluster <N>: <cluster summary>
- **Tools involved:** <list>
- **Merged contract sketch:** <one paragraph>
- **Why merge is cheaper than fixing separately:** <one line>

## Cross-cutting must-fix (deduped)

For findings that recur across multiple tools, listed ONCE with the
affected tools enumerated:
### <fix description>
- **Affected tools:** <list>
- **Recommendation:** <one paragraph>
- **Needs spec:** yes/no

## Must-fix by category

For tool-specific must-fixes, grouped by category:
### Retrieval
- <tool>: <issue> (<file:line>) -- <recommendation>
[etc]

## Should-fix / nice-to-have

Grouped by category, terse one-line items.

## Candidates for individual brainstorming (needs_spec items)

One line each, with category and rationale for why this needs its own
spec rather than direct implementation.

## Disagreements surfaced during synthesis

Cases where two subagents took conflicting positions (e.g., one says
keep, another says consolidate-into-it). State both positions and which
the synthesis chose to adopt (or flag as unresolved for panel review).
```

After writing the file:

```bash
cd /home/edible/Projects/PAL && git add docs/superpowers/audits/2026-05-11-tool-audit-report.md && git commit -m "$(cat <<'EOF'
audit: tool audit report (draft, panel review pending)

Synthesized from seven parallel category subagent reports per
docs/superpowers/specs/2026-05-11-tool-audit-design.md. Pre-panel
draft; revisions follow review.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Report back: commit SHA, total tool/verdict/fix counts, and any disagreement notes.
```

- [ ] **Step 2: Verify the report has the required structure**

Read the produced file (`Read` tool) and confirm it has these sections:
- Summary with non-zero counts
- Deletes section
- Consolidate clusters section
- Cross-cutting must-fix section
- Must-fix by category section
- Should-fix / nice-to-have section
- Candidates for individual brainstorming section
- Disagreements section (may be empty)

If a section is missing or trivially short, dispatch a fix subagent.

---

## Task 4: Dispatch 4-reviewer expert panel in parallel

**Files:** None modified. Outputs are panel reports captured for Task 5.

This task dispatches all four reviewers in one message containing four Agent tool calls so they run concurrently.

- [ ] **Step 1: Read the audit report so each panel prompt can include it inline**

```bash
# (no command -- use Read tool to load the file's content into context)
```

Pass the full report content into each panel reviewer's prompt.

- [ ] **Step 2: Dispatch all four reviewers in parallel**

Each prompt has the same inline structure: brief on the project, paste the full audit report, give them a distinct lens, ask for a bounded report.

**Reviewer 1: Architecture coherence**
```
You are reviewing PAL's tool audit report from an ARCHITECTURE COHERENCE
lens. Project background: PAL is a personal knowledge-management agent
with ~20 tool surfaces accreted over time. The report below proposes
deletes, consolidations, and fixes across the surface.

Your role: do the verdicts hang together as a system? Does any delete
or consolidation break a cross-tool invariant the audit didn't see?
Does the proposed surface (after all changes) have clean boundaries?

[paste full audit report here]

Return:
- Top 3 architectural concerns (with concrete fix suggestions)
- Top 2 design choices you'd defend against naive pushback
- One keystone question you'd want answered before approving

Cite specific items from the report by name. Under 600 words.
```

**Reviewer 2: YAGNI skeptic**
```
You are reviewing PAL's tool audit report from a YAGNI lens.
Background: PAL is a personal knowledge-management agent with ~20 tool
surfaces accreted over time.

Your role: aggressively challenge the keep verdicts and the must-fix
items. Push for more deletes. Identify must-fixes that aren't actually
load-bearing. Shrink the surface and the work queue.

[paste full audit report here]

Return:
- Top 3 things to cut further (more deletes, weaker must-fixes)
- Top 1-2 things the report is right to keep (defend against your own
  skepticism)
- One keystone question

Cite specific items by name. Under 600 words.
```

**Reviewer 3: Implementation realist**
```
You are reviewing PAL's tool audit report from an IMPLEMENTATION
REALIST lens. Background: PAL is a personal knowledge-management agent.
The report below was synthesized from category subagent reports.

Your role: sample 2-3 high-impact verdicts (especially deletes and
cross-cutting must-fixes) and READ THE ACTUAL CODE in
/home/edible/Projects/PAL/ to verify the audit didn't fabricate or miss
anything. The chat-promotion design's panel review found that one
reviewer caught fictional plumbing the others missed by reading code
instead of trusting summaries. Be that reviewer.

[paste full audit report here]

Return:
- Top 3 implementation risks (places the audit underspecifies, hand-
  waves, or got wrong on a code-reading check)
- Top 2 verification gaps in the proposed fixes (what tests will be
  needed that the audit didn't name)
- One concrete code-shape concern (cite file:line)

Under 700 words.
```

**Reviewer 4: API consumer (PAL's-eye)**
```
You are reviewing PAL's tool audit report from a CONSUMER perspective:
how would PAL (the LLM that calls these tools) actually experience the
proposed changes?

Background: PAL self-reported on 2026-05-10 that path determinism is
its dominant friction class. The relevant memory:

[paste content of /home/edible/.claude/projects/-home-edible-Projects-PAL/memory/project_pal_path_determinism.md]

The audit report is below. For each proposed fix, ask: would PAL's
reasoning actually have an easier time after this change, or is the fix
just shuffling complexity? Are there places the audit accepted as
"good" that PAL would still trip over?

[paste full audit report here]

Return:
- Top 3 friction-reduction wins in the report (correctly identified)
- Top 3 friction concerns the report missed or underweighted
- One keystone question about the consumer-perspective gap

Under 600 words.
```

- [ ] **Step 3: Wait for all four reports to return; capture verbatim for Task 5**

---

## Task 5: Synthesize the panel debate, present to user, finalize

**Files:** Possibly modified: `docs/superpowers/audits/2026-05-11-tool-audit-report.md` (revised based on user's call).

This is in-controller work, not a subagent dispatch.

- [ ] **Step 1: Read all four panel reports and extract**

For each report:
- The reviewer's top concerns
- The reviewer's top defenses
- The keystone question

Then identify:
- **Unanimous concerns** (issues 3+ reviewers raised independently)
- **Split disagreements** (issues where reviewers contradict each other)
- **Unanimous defenses** (parts of the audit nobody pushed back on)

- [ ] **Step 2: Write a debate summary to present to the user**

Template:

```
## Where reviewers UNANIMOUSLY pushed back
- <issue 1>: <N of 4 reviewers flagged>. Suggested fixes: <list>.
- <issue 2>: ...

## Where reviewers DISAGREED with each other
### Debate A: <topic>
- <Reviewer name>: <position>
- <Reviewer name>: <position>
- My read: <recommendation>

## What the panel agrees the audit got right
- <item 1>
- <item 2>

## My recommendation
- <accept as-is | revise specific sections | partial revision>
- Specifically:
  - <change 1>
  - <change 2>
```

Present this summary to the user and ask: accept, revise (specific items), or change direction?

- [ ] **Step 3: Apply user's call**

Three branches:

**If accept:** flip the report's status from `Draft (panel review pending)` to `Accepted` and amend the existing commit (or commit a status bump).

```bash
# Edit the status line in the report file:
# `**Status:** Draft (panel review pending)` -> `**Status:** Accepted (panel reviewed 2026-05-11)`
cd /home/edible/Projects/PAL && git add docs/superpowers/audits/2026-05-11-tool-audit-report.md && git commit -m "$(cat <<'EOF'
audit: accept tool audit report after panel review

Panel reviewers (architecture, YAGNI, implementation realist, API
consumer) reviewed the draft. <one-line summary of unanimous
agreement>. Report is now the canonical source for tool-related
decisions until superseded.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

**If revise:** apply the specific revisions inline, then commit:

```bash
cd /home/edible/Projects/PAL && git add docs/superpowers/audits/2026-05-11-tool-audit-report.md && git commit -m "$(cat <<'EOF'
audit: revise tool audit report from panel feedback

<one-line description of the revisions>

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Then loop back to Step 1 of this task with another (smaller) panel review if user wants, or proceed to accept.

**If change direction:** go back to brainstorming (audit shape itself was wrong). Beyond the scope of this plan.

- [ ] **Step 4: Surface the candidates-for-individual-brainstorming list to the user**

Once accepted, the items in the report's `## Candidates for individual brainstorming` section become the next workstreams. Present that list to the user as the natural follow-up. Do not pre-create brainstorming sessions for them; let the user pick which to tackle first.

---

## Self-review checklist (run before declaring the plan complete)

- [ ] Every task has explicit file paths or "no files modified."
- [ ] Every dispatched subagent prompt is fully written out (no placeholder phrases like "describe the project").
- [ ] Memory excerpts in subagent prompts are referenced by file path so the implementer pastes the actual content (not just the summary).
- [ ] Synthesis subagent's output structure is fully specified, not handwaved.
- [ ] Panel reviewers each have a distinct lens (no overlap between any two).
- [ ] No TDD-shaped placeholder tasks (this is a process plan; tests-as-such don't apply, but each stage's output gets a verification step).
- [ ] No em dashes in any of the user-facing prompt text (per user preference).

## Out of scope

- Implementing any fix the audit identifies (separate workstreams keyed off the report).
- Auditing prompts (deferred to Phase 2 inference investigation).
- Auditing agent_core (cross-package; out of scope for this PAL audit).
- Re-running the audit if priorities change later (would be a fresh plan with the same shape).
