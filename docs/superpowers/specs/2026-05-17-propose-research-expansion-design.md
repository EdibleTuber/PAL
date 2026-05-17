# propose_research expansion: inline-list topics + per-URL progress

**Date:** 2026-05-17
**Status:** Design (revised after implementation-feasibility review surfaced cross-repo scope, double-mutation hazard, and truncation gap)
**Author:** Brainstormed with Claude
**Related memory:** `project_chat_first_lens`, `feedback_terse_progress`, `feedback_agent_core_version_bump`, `feedback_restart_both_processes`
**Related work:**
- `docs/superpowers/specs/2026-05-16-slash-command-prune-design.md` (Held until this lands -- needs this expansion before `/research` can be safely deleted)
- `docs/superpowers/audits/2026-05-16-prompt-audit-chat-slash.md` (the audit that surfaced the `/research` regression risk)

## Why

The slash-prune spec is Held because deleting `/research` today would lose two workflows: (a) inline batch of multiple topics with cross-topic URL dedup, and (b) per-URL progress visibility during a research run. The chat-tool path (`propose_research` + `research_topic`) currently handles only one topic at a time and emits no progress events. This spec brings the chat-tool path to feature parity, after which `/research` is safe to delete.

## Goals

1. Add inline multi-topic mode to `propose_research`: accept `topics: list[str]` alongside the existing `topic: str`. Exactly one is required.
2. Wire `research_topic` to dispatch the existing `Researcher.research_topics()` batch method (which already has cross-topic URL dedup) when the proposal carries a list.
3. Add per-URL progress emission inside `Researcher` so the chat model and the human see fetch/summarize events in real time.
4. Wire the chat-tool path to surface those events as `ToolProgressMessage` for the duration of a `research_topic` run.
5. Render the parsed topic list in the approval prompt (Discord embed and CLI) so the user sees exactly what they're approving.
6. Preserve all existing single-topic behavior. Additive only.

## Non-goals

1. **A `topic_file` parameter.** The chat model handles file-named requests with `cat path/to/queue.md` followed by `propose_research(topics=[...])`. Extra round-trip but simpler tool surface and keeps markdown-parsing in the model rather than in PAL's tool validation.
2. **Edit-topic-list in the approval modal.** v1 only allows editing depth; editing topics requires re-proposing. Richer edit UI is its own design.
3. **Total-budget depth.** depth stays per-topic, matching existing `/research` semantics. Caller controls total fetches by setting depth.
4. **Concurrent topic execution.** Sequential per topic preserves current Researcher behavior. Cross-topic concurrency is a separate Researcher refactor.
5. **Per-URL progress in other Researcher consumers** (e.g., import flow). Only the chat-tool `research_topic` path wires up emission. Other internal callers stay quiet.
6. **Cleanup of `parse_topic_file()`** in `pal/researcher.py`. Becomes unused after the slash-prune deletes `/research`; remove as part of slash-prune cleanup, not here.

## API shape

`propose_research` parameters:

```
topic:     string     (optional)
topics:    array[str] (optional)
depth:     integer    (1-10, default 3; per-topic)
rationale: string     (required)
```

Required: exactly one of `topic` (non-empty) or `topics` (non-empty list). Validated at runtime with a clear error message naming the constraint. JSON Schema `oneOf` is not used because llama.cpp tool calling does not handle it reliably (established 2026-05-16).

Tool description spells out the constraint:

> "Propose a web research run. Provide either `topic` (single string) for one topic, or `topics` (array of strings) for a batch with cross-topic URL deduplication. Exactly one is required. Returns a proposal_id; pass it to research_topic after the user approves."

`research_topic` parameters unchanged -- still `proposal_id` only. It reads topic vs topic-list off the proposal record.

## Validation

In `ProposeResearch.run()`, validate:
- `rationale` non-empty.
- Exactly one of `topic` (non-empty after strip) or `topics` (non-empty list with at least one non-empty string after strip).
- `depth` clamped to [1, 10] (existing behavior).
- For `topics`: empty strings filtered out; resulting list must still be non-empty.

Error messages:
- "Error: 'rationale' parameter is required."
- "Error: provide exactly one of 'topic' or 'topics'."
- "Error: 'topics' must be a non-empty list of non-empty strings."

## Model return shape (`propose_research`)

The tool returns JSON. Current single-topic shape:

```json
{"proposal_id": "...", "status": "approved", "topic": "docker networking", "depth": 3}
```

Multi-topic mode adds `topics` and keeps `topic` (now the human-readable summary, as on the protocol message). On approve:

```json
{
  "proposal_id": "...",
  "status": "approved",
  "topic": "3 topics: docker networking, k8s ingress, ...",
  "topics": ["docker networking", "k8s ingress", "service mesh"],
  "depth": 3
}
```

Single-topic mode return is unchanged (no `topics` key) so existing model-side handling that reads `topic` keeps working.

Declined / edited-into-different / expired branches preserve their current shape (just `proposal_id` + `status`).

## Protocol change

`pal/protocol.py` -- additive field on `ResearchProposalMessage`. **Field must be added as the LAST positional before `type`** to preserve existing positional-construction callsites in tests (`tests/test_protocol.py:25`, `tests/test_cli_research_proposal.py:6`, `tests/test_discord_interactions.py:26/41/188/233`, `tests/test_tools_research.py:74`). Confirmed by feasibility review.

```python
@register_message
@dataclass
class ResearchProposalMessage:
    proposal_id: str
    topic: str                           # always set: human-readable summary
    depth: int
    rationale: str
    topics: list[str] | None = None      # populated only in multi-topic mode
    type: str = "research_proposal"
```

Conventions:
- Single-topic mode: `topic="docker networking"`, `topics=None`.
- Multi-topic mode: `topic="3 topics: docker networking, k8s ingress, ..."` (human-readable summary; first 3 names + "..." if more), `topics=["docker networking", "k8s ingress", "service mesh"]` (full list).

Restart both PAL processes (pal-daemon, pal-discord) on deploy per `feedback_restart_both_processes`. Cross-repo scope and version bumps documented in the Migration section below.

## Approval registry (agent_core change)

`ApprovalRegistry` lives in **agent_core** (`agent_core/approval_registry.py`), NOT in PAL. Feasibility review caught this. The `Proposal` dataclass has a fixed field set; `create_proposal()` has a fixed kwarg list. Adding `topics` is therefore a cross-repo change.

**Changes in agent_core:**

1. `Proposal` dataclass -- add `topics: list[str] | None = None` as the last field (before any internal `event` / `expires_at` fields; preserve existing positional order).
2. `ApprovalRegistry.create_proposal()` -- accept `topics: list[str] | None = None` kwarg, store it on the proposal record.
3. `ApprovalRegistry.edit()` -- **also copy `topics`** when creating a successor proposal. Without this, approve-with-edit on a multi-topic proposal silently drops the topic list (feasibility review caught).
4. agent_core version bump (1.2.x -> 1.3.0; this is the first additive field on Proposal, treat as minor not patch). Per `feedback_agent_core_version_bump`.

**Changes in PAL:**

1. Pin bump in `pyproject.toml` to the new agent_core version. Coordinate via the existing slash-prune-spec timing or in a single combined deploy.
2. `ProposeResearch.run()` -- pass `topics=topics_list` to `ar.create_proposal(...)` in multi-topic mode.

**Note on user's parallel agent_core workstream:** the user has WIP on `agent-core-v1.3.0-pin-bump` branch in PAL plus parallel agent_core work. This spec's agent_core change should coordinate with that workstream (could share the same v1.3.0 bump, or be a separate v1.2.3 patch -- user decides at execution time).

`research_topic.run()` dispatch -- simplified per feasibility review (`research_topic` is a 1-element wrapper around `research_topics`; the branch is dead complexity):

```python
topics = proposal.topics or [proposal.topic]
report = await researcher.research_topics(topics, depth=proposal.depth)
```

Single-topic mode produces `topics=[proposal.topic]`; multi-topic mode uses `proposal.topics` directly. Both go through the same batch path. Behavior is identical to today for single-topic (Researcher.research_topic is literally `research_topics([topic])` per `pal/researcher.py:235-237`).

## Approval prompt rendering

When `msg.topics` is non-empty, the proposal embed/CLI prompt shows the full list (subject to truncation; see below):

```
Research proposal
=================
Topics (3):
- docker networking
- k8s ingress
- service mesh

Depth: 3 sources per topic
Rationale: building wiki for new platform onboarding

[Approve] [Decline] [Edit]
```

Single-topic case unchanged.

### Truncation (v1, required)

Discord rejects embeds >4096 chars. A 30-topic proposal would silently never reach the user -- `propose_research` blocks until the 15-minute expiry then errors back to the model. Feasibility review flagged this; truncation ships in v1.

Crib the existing `cap`/`fitted`/`dropped` pattern from `pal/discord_interactions.py:94+` (used by the compile/reorg builders):
- Render topics until the running embed body length reaches the cap (e.g., 3500 chars to leave headroom for headers and the Decline/Edit/Approve buttons section).
- Append `"... (N more not shown; total M)"` trailer when truncation fires.
- CLI render has no length limit but uses the same truncation logic so the approval prompt stays scannable in a terminal (e.g., cap at 30 topics shown + trailer).

Test target: a synthetic 50-topic proposal renders within Discord's limit, includes the trailer, and the approval message reaches the user.

### Files that render proposals

- `pal/discord_interactions.py:60-91` (embed builder) -- conditional: if `msg.topics`, render with truncation; else single-topic existing path.
- `pal/cli.py:68-78` (CLI render) -- four-line conditional addition.

### Edit modal

Multi-topic mode: edit allows changing depth only. Editing topics requires re-proposing (out of scope for v1). Implementation note: the agent_core `ApprovalRegistry.edit()` change above must copy `topics` from the original proposal to the successor so depth-only edits preserve the topic list.

## Per-URL progress (Researcher changes)

Add emission points inside `Researcher`:

In `_fetch_and_save(url, topic_slug)` -- emit at the end:
- on success: `self._progress(f"Fetched: {_short_url(url)}")`
- on FetchError or other Exception: `self._progress(f"Fetch failed ({_short_url(url)}): {exc}")`

In `_summarize(source)`:
- on success: `self._progress(f"Summarized: {_short_url(source.url)}")`
- on failure: skip the new emission (the fetch failure was already announced; redundant noise to repeat)

New helper `_short_url(url: str) -> str`:
- hostname + path truncated to 40 chars total
- example: `https://kubernetes.io/docs/concepts/services-networking/ingress/` -> `kubernetes.io/docs/concepts/services-net...`

Existing topic-phase events stay untouched (`"Researching: X"`, `"Fetching N sources for: X"`, `"Summarizing sources for: X"`, etc.).

Estimated emission volume for a 5-topic x depth=3 run:
- 5 topic-start + 5 fetch-phase + 5 summarize-phase = 15 existing
- 3*5 = 15 fetch-end + 15 summarize-end = 30 new
- Total: ~45 events
- Plus the final "Research complete" tally

Acceptable transparency; matches `feedback_terse_progress` preference.

## Wiring the chat-tool path

**Correction from initial draft:** `_emit_progress` is ALREADY wired in `handle_chat` at `pal/agent.py:448-462` -- the per-turn closure over the connection's `writer` is assigned to `self.researcher.on_progress` for every chat turn. Feasibility review caught this. My initial spec said "currently None; set in ResearchTopic.run()", which would create competing callbacks fighting for the same slot.

**Correct approach:** the chat-loop's existing per-turn `_emit_progress` callback already fires for Researcher's progress emissions. After the Researcher changes below add new emission points (per-URL fetch/summarize), the existing wire automatically surfaces them to the writer. **No tool-side mutation needed.**

`ResearchTopic.run()` simplifies to:

```python
async def run(self, args, ctx):
    # ... existing validation + proposal pull ...

    topics = proposal.topics or [proposal.topic]
    report = await ctx.agent.researcher.research_topics(
        topics, depth=proposal.depth,
    )
    return _format_research_report(report, ctx.agent.config.vault_path)
```

No try/finally, no on_progress mutation, no `asyncio.create_task` task-GC concerns. The existing wire handles everything.

### What the existing wire looks like (for context)

Per `pal/agent.py:448-462`, `handle_chat` already does (approximately):

```python
def _emit_progress(msg: str) -> None:
    progress = ToolProgressMessage(tool="research_topic", arguments={"status": msg})
    writer.write(encode_message(progress))
    drain_task = asyncio.create_task(writer.drain())
    drain_task.add_done_callback(_log_drain_failure)

self.researcher.on_progress = _emit_progress
```

This pattern writes synchronously to the writer (preserving event ordering) and drains asynchronously with a logged failure callback (preserving the task reference via add_done_callback to avoid GC). The Researcher's new per-URL emissions automatically inherit this pattern -- no double-wire needed.

### `ctx.emit` is not used here

The initial draft proposed `ctx.emit` from inside a sync callback wrapped in `asyncio.create_task`. The feasibility review correctly noted (a) `ctx.emit` is async, (b) the wrap creates ordering nondeterminism across emissions, and (c) un-referenced tasks risk GC. The existing `_emit_progress` pattern at `agent.py:448-462` avoids all three by writing directly to the writer and using `add_done_callback` on the drain task. Stick with that pattern; do not introduce a parallel mechanism.

## Tests

In `tests/test_research_tools.py` (or extend existing test_research file if convention prefers):

**Validation:**
1. `test_propose_research_rejects_neither_topic_nor_topics` -- both empty/missing, error.
2. `test_propose_research_rejects_both_topic_and_topics` -- both set, error.
3. `test_propose_research_rejects_empty_topics_list` -- topics=[], error.
4. `test_propose_research_rejects_topics_all_whitespace` -- topics=["", " ", "\n"], error after filter.
5. `test_propose_research_rejects_missing_rationale` -- existing; ensure still works.
6. `test_propose_research_clamps_depth` -- existing; ensure still works.

**Happy paths:**
7. `test_propose_research_single_topic_populates_topic` -- topic set, topics None on proposal.
8. `test_propose_research_topics_list_populates_topics` -- topics set, topic summary auto-generated.
9. `test_propose_research_topics_summary_truncates_after_three` -- topics=["a","b","c","d","e"]; assert topic field is "5 topics: a, b, c, ...".

**Dispatch:**
10. `test_research_topic_single_calls_research_topic` -- proposal.topics is None; researcher.research_topic called.
11. `test_research_topic_batch_calls_research_topics` -- proposal.topics is non-empty; researcher.research_topics called with the list.

**Progress emission:**
12. `test_researcher_emits_per_url_fetch_success` -- inject mock fetcher; assert progress callback received "Fetched: ..." event.
13. `test_researcher_emits_per_url_fetch_failure` -- mock fetcher raises; assert "Fetch failed (...): ..." event.
14. `test_researcher_emits_per_url_summarize` -- mock summarizer; assert "Summarized: ..." event.
15. `test_research_topic_resets_progress_callback_on_completion` -- assert on_progress is restored to prior value after run.
16. `test_research_topic_resets_progress_callback_on_exception` -- mock raises; finally still runs.

**Rendering:**
17. In `tests/test_discord_interactions.py` -- pin the multi-topic proposal embed includes all topics (small list).
18. CLI rendering test if there's a coverable surface (read existing CLI tests for the pattern).
19. **`test_discord_embed_truncates_long_topic_list`** -- synthetic 50-topic proposal; embed body length is under 4000 chars; trailer `"... (N more not shown; total 50)"` is present; the message would be accepted by Discord.

**Return shape:**
20. **`test_propose_research_return_shape_single_topic`** -- approve flow, single-topic, JSON return has `topic` key but no `topics` key (regression pin).
21. **`test_propose_research_return_shape_multi_topic`** -- approve flow, multi-topic, JSON return has both `topic` (summary) and `topics` (list).

**ApprovalRegistry edit (agent_core repo):**
22. In `agent_core/tests/test_approval_registry.py` -- **`test_edit_preserves_topics_when_set`** -- create a proposal with topics, call edit() to change depth, assert successor proposal still has topics.
23. **`test_edit_topics_none_unchanged`** -- regression pin; create single-topic proposal (topics=None), edit depth, successor has topics=None.

That's 21-23 tests across PAL + agent_core. Larger than recent specs because the change touches multiple seams (validation, protocol, dispatch, emission, rendering, edit-flow, cross-repo). Each is small and focused.

## Migration / verification

**Cross-repo change** (corrected from initial draft):

1. **agent_core change first.** Add `topics` to `Proposal`, `create_proposal()`, and `edit()`. Bump version (1.2.x -> 1.3.0). Tag and push. Per `feedback_agent_core_version_bump`, same-version-with-changed-code is silently stale on the wheel-installed server.
2. **PAL pin bump** to the new agent_core version.
3. **PAL code change** -- propose_research validation + dispatch, protocol field, render + truncation.
4. **Both PAL processes restart** on deploy (`feedback_restart_both_processes` -- new protocol field on ResearchProposalMessage).

**Coordination with parallel workstream:** The user has a `agent-core-v1.3.0-pin-bump` branch in PAL already (from prior work). This spec's agent_core bump could share that v1.3.0 (combine with whatever other agent_core changes are queued) OR ship as its own v1.2.3 patch. User decides at execution time.

**Smoke tests after deploy:**
- "research docker networking" -- single-topic mode; expect existing behavior + per-URL fetch/summarize progress visible during execution.
- "research these topics: docker networking, k8s ingress, service mesh" -- batch mode; expect approval shows the list, execution dedupes URLs across topics, per-URL progress visible.
- "research the topics in raw/notes/queue.md" -- expect model cats the file, parses bullets, calls propose_research with topics=[...], same approval/execution as the inline case.
- Synthetic 50-topic batch (manual) -- proposal embed renders within Discord's 4096-char limit, shows truncation trailer.
- Approve-then-edit on a multi-topic proposal -- changing depth preserves the topic list (validates ApprovalRegistry.edit() change).

## Risks

1. **Per-URL emission cadence is too chatty for short runs.** For a single-topic depth=3 run, the new events add 6 messages (3 fetch + 3 summarize) on top of the existing 3-5 phase events. Acceptable per `feedback_terse_progress`; reconsider if users complain.
2. **Long topic lists.** Mitigated by truncation logic with explicit "N more not shown" trailer. Test #19 pins behavior at 50 topics.
3. **Concurrent research is shared state on `agent.researcher`.** No new risk from this spec -- the existing per-turn `_emit_progress` wire at agent.py:448-462 already has this property. PAL's chat loop is single-turn-at-a-time per channel; cross-channel concurrency on the same agent is not a current concern. If it becomes one, construct a per-call Researcher instance instead.
4. **Model misuses the new shape.** Model might pass both `topic` and `topics`, or pass `topics` with a single-element list when single-topic mode is cleaner. The validation error message names the constraint; small-model behavior may surprise. Acceptable for v1; the audit's Tier 2 path-determinism fix (standardized parameter descriptions) will help generally.
5. **Approval embed becomes the load-bearing surface.** Whatever the embed shows is what the user approves. Bugs in the embed renderer (e.g., showing topic but not topics, or vice versa) could cause silent approve-on-the-wrong-shape. Tests #17, #18, #19 pin the rendering; manual smoke before deploy.
6. **Approve-then-edit drops topics if `ApprovalRegistry.edit()` change is skipped.** Hidden silent failure: the user edits depth, the successor proposal has `topics=None`, the batch turns into a single-topic run on the summary string. Test #22 in agent_core's suite pins the fix; tightly coupled to the dispatch logic in this spec.

## Out of scope (intentionally)

- `topic_file` parameter (model handles via cat).
- Editing the topic list in the approval modal.
- Concurrent topic execution.
- Per-URL progress for non-chat consumers of Researcher.
- Cleanup of `parse_topic_file()` (handled in slash-prune cleanup).
- Backwards-compat for stored proposals with the old shape (additive change, old proposals just have topics=None which routes to single-topic path; safe).
