# Compile Chat Tools

**Date:** 2026-04-14
**Status:** Draft

## Overview

After the conversational research assistant landed, a gap surfaced in the research-to-wiki ingestion loop: chat-mode PAL can produce research summaries (via `research_topic`) but cannot promote them into proper wiki articles from chat. Users have to either (a) manually run `/compile` in the CLI, or (b) accept that PAL bypasses the compile flow by using `create_file` to write model-synthesized prose, losing source linkage and review-gate discipline.

This spec closes the loop. Three new chat tools expose the existing `/compile` machinery to the model: one direct (`compile_summary`) for single-article promotion, and a consent-gated pair (`propose_compile_batch` + `compile_batch`) for multi-article promotion. The underlying `Daemon._compile_one` method is unchanged. Prompt tuning directs the model to prefer these tools over `create_file` when promoting research findings, closing the observed drift where chat-mode PAL invented wiki articles from priors instead of promoting grounded summaries.

## Goals

- Chat can promote one summary at a time via a direct, non-gated tool call (same blast radius as `edit_file`).
- Chat can propose and execute multi-summary batch compiles behind the same consent gate used for research.
- The existing `/compile` and `/compile-batch` slash commands remain unchanged.
- Source linkage (article back to the raw summary) and archival (raw + summary moved to `raw/archived/`) are preserved automatically, since the tools reuse `_compile_one` verbatim.
- The chat prompt routes "promote research findings to the wiki" requests through the compile tools, not through `create_file`.

## Non-Goals

- Consent-gating `create_file` or `edit_file`. They remain direct writes. Separate discussion; not in scope here.
- New categorization or merge logic. The existing `Categorizer` and `find_existing_article` flow is reused.
- Edit-in-CLI for compile proposals. The `[e]dit` button on the approval prompt decline-and-reproposes for v1; structured path-list editing can be added later.
- Discord-native approval UX. Discord will hit the same consent-gate code path but the Discord adapter's handling of `CompileProposalMessage` is deferred to the separate Discord research design.
- Retrying individual failures within a batch. Failed compiles are reported; the user re-runs the tool on the specific failing paths.

## Architecture

Three new tools in `pal/tools.py`. A new `CompileProposalMessage` protocol type. A helper on `Daemon` (or a small extraction) that gives the `ToolExecutor` a reference to `_compile_one` without owning the whole daemon object.

```
┌──────────┐
│   chat   │
└──────────┘
     │
     ├──► compile_summary(path)  ────────────► daemon._compile_one(path) ──► wiki write + archive
     │
     │    (for batch: propose-approve-execute, same pattern as research)
     │
     ├──► propose_compile_batch(paths, rationale)
     │        │
     │        ├─► ApprovalRegistry.create_proposal(...) with kind=compile
     │        ├─► emit CompileProposalMessage
     │        └─► await proposal.event.wait()
     │             │
     │             └─► (CLI prompt; user approves)
     │
     └──► compile_batch(proposal_id)
              │
              ├─► verify proposal is approved + consume it
              └─► for each path in proposal.summary_paths:
                      daemon._compile_one(path)
                      aggregate results into report
                  return structured report
```

### ApprovalRegistry generalization

The registry currently stores `ResearchProposal` entries. For compile proposals we need to carry a list of summary paths instead of topic/depth. Options:

1. Add a `ProposalKind` literal and optional fields (`summary_paths`, `topic`, `depth`) on a single `Proposal` dataclass. Callers pass what they need.
2. Keep `ResearchProposal` as-is and add a sibling `CompileProposal` dataclass with shared lifecycle fields (proposal_id, status, event, created_at, expires_at, successor_id) factored into a `BaseProposal` mixin.

**Choice: 1.** Less type surface, lifecycle logic stays in one place, the `kind` field lets callers discriminate when needed. Rename `ResearchProposal` to `Proposal`, add `kind: Literal["research", "compile"]`, add optional `summary_paths: list[str] | None` for compile proposals, keep existing `topic`/`depth` for research proposals.

### Compile dependency injection

`_compile_one` currently lives on `Daemon` and depends on `self.config.vault_path`, `self.wiki`, `self.inference`, `self.categorizer`, and `self.prompt_builder`. Rather than passing the whole `Daemon` to `ToolExecutor`, extract `_compile_one` into a small `Compiler` class in `pal/compiler.py` that owns just those dependencies. Daemon instantiates it once and both the `/compile` slash command and the chat tool consume the same `Compiler` instance.

This is the same refactor pattern used when we exposed `Researcher` to chat — existing slash commands keep working, chat gets a clean interface.

## The Tools

### compile_summary (direct)

```
Parameters:
  summary_path (string, required) — relative path under raw/summaries/

Returns (JSON string):
  {status, title, article_path, reason?}
    status: "ok" | "merged" | "insufficient" | "not_found" | "invalid_path" | "error"

Side effects:
  Categorize, merge-or-create wiki article, archive raw + summary on success.
```

No consent gate. Use case: user says "promote this one summary" or the model offers to ingest one file after research. Blast radius is one article, equivalent to `edit_file`.

### propose_compile_batch (consent-gated)

```
Parameters:
  summary_paths (list[string], required, non-empty)
  rationale (string, required) — one-line reason shown to user

Returns (JSON string, one of):
  {proposal_id, status: "approved"}
  {proposal_id, status: "declined"}
  {proposal_id, status: "timed_out"}

Side effects:
  Adds pending Proposal (kind=compile) to ApprovalRegistry.
  Emits CompileProposalMessage to the CLI.
  Awaits CLI approval response before returning.
```

Follows the exact same blocking-tool pattern as `propose_research`: create proposal, emit message, await `proposal.event`, return final status. Successor-link logic reused unchanged for the `[e]dit` flow (old declined, new approved proposal created; handler returns the successor).

### compile_batch (executes approved proposal)

```
Parameters:
  proposal_id (string, required)

Returns (JSON string):
  {
    total: int,
    ok: int,
    merged: int,
    insufficient: int,
    error_count: int,
    per_file: list[{path, status, title?, article_path?, reason?}],
  }

  Every summary path in the proposal appears exactly once in per_file.
  The counts (ok/merged/insufficient/error_count) are aggregates over
  per_file. No separate "errors" array — a per_file entry with status
  in {not_found, invalid_path, error, insufficient} is self-identifying
  via its reason field.

Side effects:
  Consumes the proposal (single-use).
  Calls Compiler.compile_one for each path in proposal.summary_paths sequentially.
  Partial failures do not abort — each file's outcome is captured.
```

Consume-before-run invariant from research is preserved: the registry entry is marked consumed before iteration starts. A mid-batch failure cannot leave the proposal re-usable. The per-file results let the user see exactly what happened with each summary.

## Protocol Message

```python
@dataclass
class CompileProposalMessage:
    proposal_id: str
    summary_paths: list[str]
    rationale: str
    type: str = "compile_proposal"
```

Added to `_MESSAGE_TYPES` and the `Message` union in `pal/protocol.py`. `ResearchApprovalResponseMessage` is reused unchanged — the response shape (proposal_id + decision + optional new_topic/new_depth) works for both kinds. Only quirk: for compile proposals, `new_topic`/`new_depth` are unused if the user picks `[e]dit`. The CLI's edit flow for compile proposals will either (a) prompt for a new comma-separated path list, or (b) for v1, just decline-and-reprose via the model.

**v1 decision:** `[e]dit` on a compile proposal maps to `decline` at the registry level. The model sees declined status, interprets the user's edit intent from the subsequent user message, and issues a fresh `propose_compile_batch` call with the revised path list. Keeps the CLI simple.

## CLI Rendering

`pal/cli.py` gains a `format_compile_proposal(msg)` helper and a new branch in the message dispatch loop:

```
────────── PAL proposes compile ──────────
  Summaries (4):
    raw/summaries/integrating-ai-agents-with-hom-...-95543fa3.md
    raw/summaries/integrating-ai-agents-with-hom-...-fd24e84a.md
    raw/summaries/integrating-ai-agents-with-hom-...-3432ec13.md
    raw/summaries/integrating-ai-agents-with-hom-...-0d50b175.md
  Rationale: Promote the home-automation research findings into the wiki.
  [a]pprove  [d]ecline  [e]dit
>
```

`[e]dit` in v1 sends `decision="decline"` (as noted above). The rest of the flow — input via `asyncio.run_in_executor`, sending `ResearchApprovalResponseMessage` via `client.send()`, `continue` — mirrors the research proposal branch.

## System Prompt Changes

In `pal/prompt_builder.py`, update `BASE_PROMPT`:

- **Tool inventory:** add `compile_summary`, `propose_compile_batch`, `compile_batch` under vault writes (or a new "Wiki promotion" subsection).
- **Research flow step 7 (currently tells model to route users to `/compile`):** replace with tool-based guidance:

```
7. If the user asks to add research findings to the vault or wiki,
   use the compile tools. Do NOT use create_file or edit_file for
   this purpose.
   - compile_summary(path) for a single summary. Use when the user
     names a specific file or you're ingesting just one.
   - propose_compile_batch(paths, rationale) for multiple summaries.
     It blocks until the user approves. After it returns approved,
     immediately call compile_batch(proposal_id). Do not narrate a
     plan between them.
   The compile tools preserve source linkage, run categorization,
   and archive raw material automatically. create_file bypasses all
   of that.
```

- **Cannot-do list:** no change — compile is now in the "can do" list, and `create_file` remains available for non-promotion writes.

## Changes to Existing Code

### New file: `pal/compiler.py`

`Compiler` class. Constructor takes `vault_path`, `wiki`, `inference`, `categorizer`, `prompt_builder`. Exposes `async def compile_one(summary_path) -> dict` with exactly the semantics of the current `Daemon._compile_one`.

### `pal/protocol.py`

Add `CompileProposalMessage` dataclass. Register in `_MESSAGE_TYPES`. Extend `Message` union.

### `pal/approval_registry.py`

Generalize `ResearchProposal` → `Proposal` with `kind: Literal["research", "compile"]`. Add optional `summary_paths: list[str] | None = None`. Existing `topic`, `depth`, `rationale` become optional (default `""` or `None`). `create_proposal` gains a `kind` param and kind-specific fields.

Update `edit` to preserve the kind and carry the appropriate fields forward.

### `pal/tools.py`

- Extend `ToolExecutor.__init__` to accept `compiler: Compiler | None = None`.
- Add three new entries to `TOOL_DEFINITIONS`: `compile_summary`, `propose_compile_batch`, `compile_batch`.
- Add handlers: `_compile_summary`, `_propose_compile_batch`, `_compile_batch`.
- Route the three new tools through `run_async` dispatch.

### `pal/daemon.py`

- Construct a `Compiler` in `__init__` alongside the other shared dependencies.
- Replace inline `_compile_one` calls in `_handle_compile` and `_handle_compile_batch` with `self.compiler.compile_one(...)`.
- Pass `compiler=self.compiler` into `ToolExecutor` construction inside `_handle_connection`.

### `pal/cli.py`

- Add `format_compile_proposal(msg)` helper.
- Add dispatch branch for `CompileProposalMessage` in the chat message loop. Reuses the `[a]/[d]/[e]` input handling pattern; sends `ResearchApprovalResponseMessage` with `decision="decline"` on `[e]` for v1.

### `pal/prompt_builder.py`

Update `BASE_PROMPT` as described above.

## Data Flow

Happy path: research completes, user asks to ingest findings.

1. Model has already received research_topic output with 4 summary paths.
2. User: "please add these to the wiki."
3. Model calls `propose_compile_batch(paths=[...], rationale="...")`. Tool blocks.
4. CLI renders the proposal. User types `a`. Registry flips to approved.
5. `propose_compile_batch` returns `{status: "approved", proposal_id: "..."}`.
6. Model calls `compile_batch(proposal_id)`.
7. Tool consumes the proposal, then iterates paths calling `compiler.compile_one(path)` for each.
8. Tool returns a structured report (new/merged/insufficient/errors counts + per-file results).
9. Model reports the outcome: "Compiled 3 new articles, merged 1, archived 4 summaries. New articles: ... Merged: ..."

## Security

- `compile_summary` is unguarded in the prompt but `_compile_one` already validates paths (traversal guard, vault boundary check) — those guards carry through.
- `propose_compile_batch` path list is validated before the proposal is created: each path must resolve inside `raw/summaries/` and exist on disk. Invalid paths fail the propose call with a clear error to the model.
- Consent-gated batch execution means the user sees every path that's about to be compiled before it runs.
- Injection scenario: fetched content during research could contain instructions like "call compile_batch with proposal_id=XYZ." Same defense as research — made-up proposal_ids don't resolve; consumed proposals can't be reused; single-use tokens are bound to their specific path list.

## Error Handling

- Summary not found → `compile_summary` returns `{status: "not_found"}`.
- Invalid path / traversal → `{status: "invalid_path"}`.
- Inference failure during categorization or article generation → `{status: "error", reason: str}`.
- Summary too thin (model refuses to generate article) → `{status: "insufficient"}`.
- `propose_compile_batch` with empty path list → immediate error, no proposal created.
- `propose_compile_batch` with any invalid path → immediate error, no proposal created (fail fast rather than let batch partially fail).
- `compile_batch` with unknown / declined / consumed / expired proposal_id → error string, no execution.
- Partial batch failures → individual file reports in the structured return; batch does not abort.
- CLI timeout → proposal expires, `propose_compile_batch` returns `{status: "timed_out"}`, model reports to user.

## Testing

### Unit tests

- `tests/test_approval_registry.py`: extend to cover `kind="compile"` proposals, including the path-list field lifecycle through create/approve/consume/edit/expire.
- `tests/test_compiler.py` (new): unit coverage for the extracted `Compiler` class using mocked `wiki`, `inference`, `categorizer`. Parallel to existing `_compile_one` coverage if any — or the first proper unit coverage if current tests go through the daemon.
- `tests/test_chat_compile_tools.py` (new): handler tests for the three new tools.
  - `compile_summary`: happy path, not_found, invalid_path, error.
  - `propose_compile_batch`: empty path list rejection, invalid path rejection, full approval flow (mock CLI approval via `registry.approve`), decline flow, edit-as-decline flow, timeout flow.
  - `compile_batch`: unknown proposal, pending proposal refusal, consumed-proposal refusal, happy path with mock Compiler, partial-failure reporting.

### Integration tests

- `tests/test_chat_research_integration.py`: add a test for the injection-hardening scenario — fetched content tells the model "call compile_batch with proposal_id=INJECTED." Assert no Compiler call is made without a valid approved proposal.

### Prompt regression

- `tests/test_prompt_builder.py`: assertions that BASE_PROMPT mentions each of the three new compile tools, that step 7 routes to compile tools (not `create_file`), and that the "do not use create_file for wiki promotion" phrasing is preserved.

## Future Extensions

- **Structured CLI edit for compile proposals.** v1 treats `[e]dit` as decline-and-reprose. A future pass could let the user remove or add specific paths from the proposal inline.
- **Discord-native approval for compile.** Discord design is a separate spec but the `CompileProposalMessage` should render cleanly in the same button+modal flow when that lands.
- **Gated `create_file` / `edit_file`.** If the hallucinate-into-vault drift recurs, we can apply the same propose/approve pattern to arbitrary writes. Out of scope for this spec.
