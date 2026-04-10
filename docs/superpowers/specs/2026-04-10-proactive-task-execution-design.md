# PAL — Proactive Task Execution

**Date:** 2026-04-10
**Status:** Draft

## Overview

This spec describes how PAL moves from a conversational agent that forgets plans between errors into a daemon that durably tracks multi-step work, survives crashes, and continues opted-in plans autonomously without user prompting.

It replaces the current ad-hoc `tasks/current.md` convention with a structured task model, a dedicated planning tool, and a daemon-side polling loop governed by event triggers, idle checks, and a circuit breaker. It is gap #2 on the PAL roadmap (after the chunker fix), and it is the piece that turns a "query tool" into something that feels proactive.

## Motivation

Field evidence from a 2026-04-09 Discord cleanup session exposed four failure modes in the current approach:

1. **Amnesia after errors.** After a 500 from the inference server, PAL came back without memory of what it had been doing and asked the user "what were you working on?" instead of reading `tasks/current.md`. State lived in the conversation, not on disk.
2. **Premature completion claims.** PAL declared Chapter 2 cleanup done when the verification step had been skipped. The task file could not distinguish "finished" from "partially finished."
3. **Order-of-operations drift.** PAL archived fragments before verifying their content was in the main chapter, the opposite of the user's explicit rule. Nothing mechanical enforced sequencing.
4. **Babysitting requirement.** Between every chapter, PAL stopped to ask "Option A, B, or C?" even when the user had already given a directive. The user had to stay in the loop to re-confirm the plan.

The current `tasks/current.md` is free-form markdown with no structure a program can read. Every decision about "what's next" and "is this done" is delegated to an LLM that might have lost state. The fix is to put enough structure on disk that the daemon can answer those questions without asking the model.

## Goals

- Multi-step plans survive daemon crashes, inference server outages, and session boundaries without losing state
- The daemon can mechanically answer: "is this plan actionable now, what's the next step, is anything half-done, is the plan complete?"
- Plans explicitly opted in by the user can advance autonomously without user prompting
- Richer status vocabulary prevents the polling loop from re-acting on work that is blocked, deferred, or in an ambiguous mid-execution state
- Valid state transitions are enforced by the daemon, not the model, so LLM drift cannot corrupt the task tree
- The task files remain inside the vault, remain git-tracked, and remain rendered as markdown in Obsidian

## Non-Goals (v1)

- Parallel step execution (one step at a time per plan)
- Cross-plan scheduling or priority (plans are independent; the loop picks whichever is actionable)
- External triggers beyond daemon-internal events (no webhooks, no cron)
- A UI for editing plans (the agent or direct file edits are the only editors)
- Retry policies beyond "mark failed, wait for human" (no automatic exponential backoff of failed steps)
- Rich task-graph features like critical-path analysis or slack time

## Task Model

### File Format

One markdown file per plan in `tasks/`, named `YYYY-MM-DD-<slug>.md`. The structured state lives in YAML frontmatter; the markdown body is a free-form scratchpad the agent writes to during execution.

```markdown
---
plan_id: 2026-04-10-agentic-book-cleanup
title: Agentic Design Patterns book cleanup
description: Reconcile ~500 fragment files into the 10 main chapters, verify coverage before archiving.
status: in_progress
autonomous: false
created: 2026-04-09T18:29:00-04:00
updated: 2026-04-10T09:15:00-04:00
steps:
  - id: step-01
    description: Analyze fragment naming patterns and map to chapters
    status: done
    started: 2026-04-09T18:35:00-04:00
    completed: 2026-04-09T18:48:00-04:00
    depends_on: []
  - id: step-02
    description: Verify Chapter 1 fragment content coverage in main chapter
    status: done
    started: 2026-04-09T18:49:00-04:00
    completed: 2026-04-09T19:00:00-04:00
    depends_on: [step-01]
  - id: step-03
    description: Archive Chapter 1 fragments
    status: done
    started: 2026-04-09T19:00:00-04:00
    completed: 2026-04-09T19:15:00-04:00
    depends_on: [step-02]
  - id: step-04
    description: Verify Chapter 2 fragment content coverage
    status: in_progress
    started: 2026-04-10T09:15:00-04:00
    depends_on: [step-03]
  - id: step-05
    description: Archive Chapter 2 fragments
    status: pending
    depends_on: [step-04]
---

## Scratchpad

Notes on Chapter 1: most fragments were numbered code comments, not real sections. Confirmed the book's code samples use `# 1. Step description` conventions. Two files (`1-define-the-block-of-tasks...`) were actually Chapter 3 content due to the chunker bug and were moved there instead.

Chapter 2 analysis in progress. Fragments look to be CrewAI-style agent definitions plus LangGraph state examples. Need to check against 02-Agent-Fundamentals.md before archiving.
```

The daemon reads and writes only the frontmatter. The scratchpad body is purely for the agent's own working memory and is never parsed by the daemon. The agent is free to write anything helpful to itself there, including intermediate findings, links to vault articles, or reasoning chains.

### Status Vocabulary

**Plan-level status** applies to the whole plan:

- `pending` — created but no steps started
- `in_progress` — at least one step is in progress or done, not all steps terminal
- `done` — all steps in a terminal state (`done` on the happy path) with no failures blocking completion
- `blocked` — every actionable step is blocked on external factors
- `failed` — one or more steps failed and cannot proceed, human attention required
- `superseded` — plan is no longer relevant, kept for history

**Step-level status** applies to individual steps:

- `pending` — not started, may or may not have unmet dependencies
- `in_progress` — started, not yet terminal. Must have a `started` timestamp.
- `done` — completed successfully. Must have a `completed` timestamp.
- `blocked` — cannot proceed. Must record `blocked_on` (free-text description of the blocker, or a step id).
- `deferred` — explicitly postponed. Must record `reason`. May record `defer_until` (ISO-8601 date); if present, the polling loop will treat the step as `pending` once that date has passed.
- `superseded` — made irrelevant by a later decision. Never picked up. Must record `reason`.
- `failed` — attempted, errored. Must record `error` summary. Picked up only by explicit retry.

The separation of `pending` and `in_progress` is load-bearing for crash recovery. If the daemon wakes up and sees a step in `in_progress` with a `started` timestamp older than a threshold (default 10 minutes, configurable), it does not assume the step is still running. It transitions the step to `failed` with an error of "stale in-progress on restart" and stops. The human or a subsequent conversation can decide whether to retry.

### Dependency Model

Each step has a `depends_on` list of step ids within the same plan. A step is actionable if and only if:

1. Its status is `pending`, or `deferred` with `defer_until` in the past
2. Every step in `depends_on` has status `done`

Dependencies do not cross plans. If cross-plan sequencing is needed, users express it by creating a dependent plan in `pending` and promoting it once the predecessor completes.

There is no implicit ordering based on list position. A step with no `depends_on` is always actionable regardless of where it appears in the list. This keeps the model simple and makes parallel planning in the future a non-breaking addition.

### State Transition Rules

The daemon enforces these transitions. Illegal transitions are rejected by the planning tool.

| From | Allowed to | Notes |
|---|---|---|
| `pending` | `in_progress`, `deferred`, `superseded`, `blocked` | Normal start, or early decision to skip |
| `in_progress` | `done`, `failed`, `blocked` | Must set appropriate terminal metadata |
| `done` | (none) | Terminal. Cannot be reverted. |
| `blocked` | `pending`, `in_progress`, `superseded` | Unblock returns to pending; direct resume allowed |
| `deferred` | `pending`, `in_progress`, `superseded` | Explicit un-defer or time-based |
| `superseded` | (none) | Terminal. History only. |
| `failed` | `pending` | Retry path. No other direct transitions. |

Terminal statuses (`done`, `superseded`) are genuinely final. If a "done" step needs to be reworked, the user creates a new step that depends on it. This prevents the LLM from "un-completing" work.

## Planning Tool

A new tool exposed to the agent, called by the LLM with structured arguments. The daemon owns the file writes and enforces state transitions. The LLM never writes plan files directly.

### Operations

| Tool call | Purpose |
|---|---|
| `plan_create(title, description, autonomous=false)` | Creates a new plan file. Returns `plan_id`. |
| `plan_add_step(plan_id, description, depends_on=[])` | Appends a step. Returns `step_id`. |
| `plan_start_step(plan_id, step_id)` | Transitions `pending` → `in_progress`, records `started`. |
| `plan_complete_step(plan_id, step_id)` | Transitions `in_progress` → `done`, records `completed`. |
| `plan_fail_step(plan_id, step_id, error)` | Transitions to `failed` with error summary. |
| `plan_block_step(plan_id, step_id, blocked_on)` | Transitions to `blocked`. |
| `plan_defer_step(plan_id, step_id, reason, defer_until=null)` | Transitions to `deferred`. |
| `plan_supersede_step(plan_id, step_id, reason)` | Transitions to `superseded`. |
| `plan_retry_step(plan_id, step_id)` | Transitions `failed` → `pending`. |
| `plan_append_note(plan_id, markdown_text)` | Appends to the scratchpad body. Newline-separated. |
| `plan_set_autonomous(plan_id, autonomous)` | Flips the autonomy flag. |
| `plan_list(status_filter=null, autonomous_only=false)` | Returns plan summaries. |
| `plan_get(plan_id)` | Returns full frontmatter plus scratchpad body. |
| `plan_next_actionable(plan_id)` | Returns the next actionable step, or null. Daemon-side, no LLM judgment. |

### Validation

The daemon rejects a tool call if:

- The transition is not in the allowed table
- Required metadata is missing (e.g. `blocked_on` when blocking, `reason` when deferring)
- `depends_on` references a non-existent step id
- `autonomous` is set true on a plan with no steps

Rejections return structured errors the LLM can read and recover from. The file is never written in a partial or invalid state.

### Concurrency

The daemon holds a per-plan lock for the duration of any mutation. Two tool calls on the same plan serialize. Two tool calls on different plans run in parallel. File writes are atomic: write to a temp file, rename, then commit to git. If a crash occurs between write and commit, the next read sees the write and a subsequent mutation produces the commit.

## Polling Loop

The daemon runs a background task (the `executor`) that picks up actionable work on autonomous plans without user intervention.

### Trigger Events

The executor wakes on any of these:

1. **Daemon startup** — immediately after initialization, once the inference server is reachable
2. **Inference server recovery** — a health check transitioning from unreachable to reachable
3. **Conversation turn end** — after any user message is fully handled and the conversation is idle
4. **Backstop timer** — every `executor_backstop_seconds` (default 900, i.e. 15 minutes) regardless of other events

Events coalesce: if three triggers fire in quick succession, the executor runs once.

### Tick Behavior

On each tick the executor:

1. Checks the **idle guard**: if the daemon is currently handling a user conversation turn, skip and exit. Do not queue; the conversation-turn-end trigger will fire the next tick when safe.
2. Calls `plan_list(status_filter=in_progress, autonomous_only=true)` to find candidates.
3. For each candidate, calls `plan_next_actionable(plan_id)`. If the step is in `in_progress` and older than the stale threshold, transitions it to `failed` ("stale in-progress on restart") and skips the plan for human attention.
4. If an actionable step is returned, dispatches a single execution of that step through the normal agent chat pipeline, with a system message like: "You are executing step {step_id} of autonomous plan {plan_id}. The step description is: {description}. Work on this step only. When done, call `plan_complete_step`. If you cannot complete it, call `plan_fail_step`, `plan_block_step`, or `plan_defer_step`."
5. Records the result and moves on.
6. Increments a per-plan **circuit breaker** counter. If a plan advances `circuit_breaker_steps` (default 10) consecutive steps without a user interaction, the executor refuses further ticks on that plan and sets a `needs_review` flag in the frontmatter. Only a user conversation can clear the flag. This bounds the blast radius if the agent has gone subtly wrong.

### Hard Rule: Always Re-Read After Errors

Independent of the executor, the daemon enforces one hard rule for any chat turn that follows an error in the previous turn (inference 500, tool failure, connection drop): before generating a response, the daemon injects a system message instructing the agent to call `plan_list` and `plan_get` on any `in_progress` plans, and to summarize current state before taking action. This alone would have prevented the "what were you working on?" moment in the Discord transcript.

### Autonomy Flag

A plan defaults to `autonomous: false`. The executor ignores non-autonomous plans entirely. Users flip the flag explicitly via conversation ("make this plan autonomous") or by editing the file directly. Once autonomous, the executor picks it up on the next trigger.

This is the primary safety valve. The user decides which plans are safe to run unsupervised. The default is always "no."

## Crash Recovery Flow

Scenario: executor is running step-04 when the inference server 500s.

1. `plan_start_step` has already transitioned step-04 to `in_progress` with a `started` timestamp
2. The agent's step-execution turn fails mid-way; no `plan_complete_step` or `plan_fail_step` is called
3. The daemon logs the error but leaves the plan file as-is (step-04 remains `in_progress`)
4. The inference-server-recovery trigger fires
5. Executor tick runs, sees step-04 `in_progress`
6. Stale threshold check: if `now - started > 10 minutes`, transition to `failed` with error "stale in-progress on restart" and stop for human attention
7. Otherwise (recent start), re-dispatch the step execution

This makes the ambiguous-state problem mechanical. The daemon never has to guess whether a step is "really" in progress or "actually" abandoned; the timestamp decides.

## Vault Layout

```
~/vault/
└── tasks/
    ├── 2026-04-09-agentic-book-cleanup.md
    ├── 2026-04-10-chunker-fix.md
    └── ...
```

The old `tasks/current.md` convention is retired. A plan's "currency" is expressed by its `status` and `autonomous` fields, not by its filename. Done and superseded plans stay in the directory as history.

Optional: an auto-maintained `tasks/_index.md` that the daemon regenerates on every mutation, listing active plans with their current step. Read-only from the user's perspective. Nice for skimming in Obsidian but not required for the daemon to function.

## Wisdom Update

The existing wisdom entry that tells PAL to track tasks in `tasks/current.md` is rewritten to point at the planning tool. Key content:

- Always use the planning tool, never hand-edit task frontmatter
- Create a plan at the start of any multi-step work
- Fine-grained steps: each step should be atomic enough that completing it leaves the vault in a consistent state
- Explicit verification steps are their own steps, not inline assumptions
- When a step requires an irreversible action (archive, delete, git commit), the preceding step must be a verification step that it depends on
- The scratchpad body is for reasoning notes, not for tracking step state

## Configuration

New fields in the daemon config:

| Field | Default | Purpose |
|---|---|---|
| `executor_enabled` | `true` | Master kill switch for the polling loop |
| `executor_backstop_seconds` | `900` | Backstop timer interval |
| `executor_stale_threshold_seconds` | `600` | When to mark an `in_progress` step as stale on restart |
| `circuit_breaker_steps` | `10` | Consecutive autonomous steps per plan before pausing for review |
| `tasks_dir` | `tasks` | Vault subdirectory for plan files |

All values tunable without code changes. Starting values are conservative; expected to loosen as trust builds.

## Security Considerations

- **No new network surface.** The executor is a daemon-internal background task. It calls the same inference server the interactive agent uses and runs inside the same process.
- **No new write authority.** The executor uses the existing agent tool pipeline. It can do exactly what the interactive agent can do, subject to the same path-traversal and allowlist protections.
- **Autonomy is opt-in.** A compromised or drifting agent cannot make itself autonomous on a plan; the `plan_set_autonomous` tool is callable, but setting `autonomous=true` requires prior user intent (the wisdom entry will instruct the agent to confirm with the user before flipping the flag).
- **Circuit breaker bounds blast radius.** Even if the agent goes subtly wrong on an autonomous plan, the consecutive-step cap prevents runaway action without human review.
- **Git safety net applies unchanged.** Every file write from executor-driven steps goes through the vault's git auto-commit path, so all autonomous changes are reversible.

## Open Questions

1. **Dependency direction.** Should `depends_on` be mirrored with a `blocks` field for easier graph traversal, or computed on demand? Leaning toward computed on demand — one source of truth, no drift.
2. **Plan archival.** Do done plans move to `tasks/archive/` after some interval, or stay in `tasks/` forever? Leaning toward staying — cheap storage, and grep history is valuable.
3. **Executor reentrancy across plans.** Can the executor work on plan A while the interactive agent is doing something unrelated to plan A? Current spec says no (idle guard blocks the executor entirely during any conversation). Alternative: plan-level locks only. Start with the simpler "no."
4. **Scratchpad rotation.** If the scratchpad body grows large, is there a rotation policy? Deferred; unlikely to matter for v1.

## Success Criteria

The implementation is successful when:

1. The Discord-transcript scenarios no longer cause lost state. Specifically: after an inference 500, PAL resumes from the last `done` step without asking "what were you working on?"
2. A plan marked `autonomous` advances across daemon restarts without user prompting
3. A step marked `in_progress` for longer than the stale threshold is mechanically transitioned to `failed` on the next tick, with no LLM judgment involved
4. The circuit breaker fires on a runaway plan and prevents further autonomous action until reviewed
5. State transition rules are enforced by the daemon, and an agent attempting an illegal transition receives a structured error rather than a silent write
6. All plan files remain human-readable in Obsidian and git-tracked in the vault

## Lineage and References

- **PAL design spec** (`docs/superpowers/specs/2026-04-04-pal-design.md`) — the containing architecture. This spec plugs into the daemon as a new subsystem, alongside the existing conversation manager, wiki manager, and learning system.
- **Agentic librarian summary** (`agentic_librarian_summary.md`, 2026-04-10) — identifies proactive task execution as the second named gap in the roadmap and sketches the polling-loop direction.
- **Discord cleanup session** (2026-04-09) — field evidence of every failure mode this spec addresses.
- **Superpowers planning skills** — the fine-grained-step, verification-before-completion, and executing-plans skills directly inspire the step-granularity discipline encoded in the task model.
