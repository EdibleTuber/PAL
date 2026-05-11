# Chat-Derived Knowledge Promotion (YAGNI+)

**Date:** 2026-05-10
**Status:** Design (revised after panel review)
**Author:** Brainstormed with Claude

## Problem

PAL's promotion pipeline (`compile_summary`, `propose_compile_batch` → `compile_one`) gates every article on a non-empty `source_url` or `source_file` in the summary frontmatter. The check lives at `pal/compiler.py:124` and `:301` and exists to guarantee that every wiki article has verifiable provenance back to a fetched URL or an on-disk document.

Knowledge that emerges purely from conversation has neither. PAL's only currently-available reaction is to write a note in `raw/notes/` from memory and refuse to promote it, leaving the knowledge stuck in raw staging where it pollutes retrieval but never reaches the wiki. The accumulation observed on 2026-05-10 (the "vibe-coding-comprehension-strategies" example) is the predictable outcome of this gap.

## Goals

1. Allow chat-derived knowledge to be promoted into wiki articles through a first-class tool path.
2. Preserve the trust invariant that every article has typed, recorded provenance.
3. Provide a single tool that handles both forward promotion (synthesis from current conversation) and backfill (existing orphan notes in `raw/notes/`).
4. Make weaker provenance visible at read time via a banner so trust level is obvious.
5. Avoid the double-LLM problem: user-approved synthesis must reach the article verbatim, not be re-extracted by the compile prompt.

## Non-goals (deferred to v2)

1. **Conversation snapshots.** No `raw/conversations/` directory, no verbatim transcript capture. The article points at the synthesis note PAL wrote; the actual conversation is not preserved on disk. If transcript audit becomes load-bearing, add it additively in v2.
2. **Dereferenceable Discord pointers.** No message IDs, no `[msg:1234567890]` tagging in PAL's context, no bridge changes. PAL does not need to predict opaque Discord IDs.
3. **Surface-specific provenance types.** One source type (`chat`) covers Discord, CLI, and orphan notes. The spec previously distinguished `chat_discord` / `chat_cli` / `user_attested`; v1 collapses all three.
4. **Render-time banner.** v1 ships the banner in `compiled_truth` (in-body). Render-time rendering from `meta.sources[].source_type` is deferred to the broader prompt + tool audit pass (where `search_vault` will be touched anyway for the path-determinism fix). v1 mitigates the merge-fragility risk by making `merge_chat_synthesis_into_existing` explicitly preserve the banner sentinel.
5. **Auto-promotion.** PAL never calls the promotion tool unprompted; it may nudge once per conversation, execution always requires user approval.
6. **Fixing the unrelated `raw/` indexing leak** (already tracked separately in memory).

## Source semantics

Chat-derived content is recorded as: a synthesis note PAL wrote on disk, marked with `source_type: chat` in summary frontmatter and propagated to article metadata. The note is the source of truth; the article is the compiled product. There is no transcript.

This is a deliberate weakening of "external verifiability." The mitigation is the read-time banner, which makes the trust level visible to any future reader (human or LLM) consuming the article.

## Pipeline shape

Forward path (chat synthesis from current conversation):

```
PAL composes a synthesis from current conversation context
   │
   ▼
PAL writes synthesis to raw/notes/<slug>.md   (existing edit_file/create_file path)
   │
   ▼
PAL calls propose_promote_synthesis(title, rationale, note_path=raw/notes/<slug>.md)
   │  (blocking; user sees title, rationale, note body preview)
   ▼
On approval: writes raw/summaries/<slug>.md with source_file → note, source_type: chat
   │
   ▼
chat-aware compile path (NOT existing compile_one; see "Compiler changes")
   │  - skips LLM extraction step (synthesis IS compiled_truth)
   │  - propagates source_type into meta.sources
   ▼
<Category>/<slug>.md   (article; banner rendered at read time, not stored in body)
   │
   ▼
archive_raw_files moves note + summary into raw/_archive/
```

Backfill path (existing orphan note in `raw/notes/`):

```
User points PAL at an existing raw/notes/<orphan>.md
   │
   ▼
PAL calls propose_promote_synthesis(title, rationale, note_path=raw/notes/<orphan>.md)
   │
   ▼
(identical to forward path from this point)
```

The only difference between forward and backfill is who created the note. Forward = PAL just wrote it. Backfill = the note has been sitting there. The tool, the summary shape, and the compile path are identical.

## File model

### `raw/notes/<slug>.md`

No new directory. Existing path. PAL writes synthesis here as a normal note before calling the tool. For backfill, the note already exists.

### `raw/summaries/<slug>.md` (chat-derived)

Reuses the existing summary location. Frontmatter:
```yaml
title: "Vibe-coding comprehension strategies"
source_file: "raw/notes/vibe-coding-comprehension-strategies.md"
source_url: ""
source_type: chat
source_hash: "<sha1 of note body>"
source_raw: "raw/notes/vibe-coding-comprehension-strategies.md"
```

Body: the note body verbatim. The chat-aware compile path treats this body as the compiled truth, not as material to be re-extracted by an LLM.

### Article frontmatter (chat-derived)

```yaml
title: "Vibe-coding comprehension strategies"
created: 2026-05-10T15:23:00+00:00
updated: 2026-05-10T15:23:00+00:00
compiled_at: 2026-05-10T15:23:00+00:00
status: compiled
sources:
  - url: ""
    hash: "<sha1>"
    added: 2026-05-10T15:23:00+00:00
    source_file: "raw/notes/vibe-coding-comprehension-strategies.md"
    source_type: chat
```

`source_type` lives on each entry in `meta.sources`. Default is `external` when absent (back-compat). The article body contains only the synthesis text plus the timeline. The banner is *not* in the body.

## Tool contract

### `propose_promote_synthesis`

Single new tool. Mirrors the existing `propose_consolidate` pattern.

```python
parameters = {
    "title":     str,   # proposed article title
    "rationale": str,   # one-line for approval prompt
    "note_path": str,   # raw/notes/<slug>.md (must exist, must be under raw/notes/)
}
```

Behavior:
- Validates `note_path` exists, is under `raw/notes/`, is not a path-traversal escape.
- Reads note body, computes hash, generates summary slug from title.
- Creates a `kind="promote_synthesis"` proposal in the approval registry with `note_path`, `title`, `rationale`. User sees title, rationale, and note body preview in the approval CLI.
- Blocks until approved/declined/expired (existing pattern).
- On approval: writes `raw/summaries/<slug>.md` with `source_type: chat` and `source_file → note_path`, then invokes the chat-aware compile path (see "Compiler changes"). Returns the standard compile result shape.

Edit-on-decline supported (user can adjust title before approving).

Error shapes (returned as structured JSON):
- `note_not_found`: `note_path` doesn't exist or escapes `raw/notes/`.
- `note_too_large`: parallels existing `compile_one` `max_body_chars` guard. Suggests grep or split.
- `title_collision`: article already exists at the proposed slug; PAL should retry with a different title or use `propose_consolidate` to merge into the existing article.
- `summary_collision`: a summary at `raw/summaries/<slug>.md` already exists. PAL should pick a different title.

There is no separate `promote_synthesis` execution tool. Approval flips into execution synchronously inside the same tool call (the propose tool blocks for approval and then runs the rest of the pipeline). This matches the user's mental model ("approve = it happens") and avoids a second tool round-trip.

## Compiler changes

The gate at `compiler.py:124` and `:301` is preserved. Chat-derived summaries pass it because `source_file` is non-empty.

Three changes:

1. **New chat-aware compile entrypoint.** Add `Compiler.compile_chat_synthesis(summary_path)` alongside `compile_one`. Behavior:
   - Reads the summary frontmatter and body. Body IS the compiled truth (no LLM call).
   - Validates required sections (`## Overview`, `## Key Concepts`) via existing `validate_compiled_truth`. If missing, returns `insufficient` status with a hint that the synthesis needs the standard sections.
   - Categorizes via existing `categorizer.categorize`.
   - Topic-matches via existing `find_existing_article`. **Important fix:** the topic-match preview must be the synthesis body's first 400 chars, not the raw note's. For chat-derived this is the same body, so this is naturally satisfied.
   - On match: invokes a new `merge_chat_into_existing` that also skips the LLM step (the new compiled truth is the user-approved synthesis prepended/appended to the existing compiled truth, not LLM-rewritten). On no match: writes the synthesis as the article's compiled truth directly.
   - Calls existing `wiki.rebuild_index`, `wiki.git_commit`, `archive_raw_files`, `retrieval.trigger_reindex`.
   - Returns the same result shape as `compile_one` (`status`, `title`, `article_path_rel`, `vault_exists`, `reindex`, `_note`).

   **Why a new entrypoint, not a flag on `compile_one`:** the realist's review showed `compile_one` is tightly coupled to the LLM-extraction prompt at `pal/compiler.py:170-189`. Bypassing the LLM step via a flag would scatter conditionals through the function. A second entrypoint with shared helpers (categorizer, find_existing_article, archive) is cleaner.

2. **`source_type` propagation through `append_timeline_entry`.** The function gains an optional `source_type: str = "external"` keyword argument. The new field flows into the `meta.sources` entry dict. **Critical:** the timeline serializer/parser round-trip must handle `source_type`; see "Plumbing risks" below.

3. **`merge_into_existing` parity.** Both `compile_one` and `merge_into_existing` currently gate on `source_url`/`source_file` (lines 124 and 301). The chat path does not touch `merge_into_existing` directly, but a chat-derived summary that topic-matches an existing article triggers `merge_chat_into_existing` (the new sibling), not `merge_into_existing`. Existing call sites are unchanged.

## Plumbing risks the spec must address

The implementation realist surfaced one risk that's easy to under-budget: the timeline parser currently only reads `**Source:**`, `**Added:**`, `**Source hash:**` (`pal/article.py:70-108`). Without parser changes, any merge that reads → mutates → writes a chat-derived article will silently strip `source_type` from prior timeline entries.

Required:
- Extend `_format_timeline_entry` to write `**Source type:**` when `source_type != "external"`.
- Extend `_parse_timeline_entries` to read it (default `"external"` on absence).
- Extend `TimelineEntry` dataclass with `source_type: str = "external"`.
- Round-trip test: serialize → parse → serialize, assert `source_type` preserved on every entry.

This is the only "boring plumbing" YAGNI+ can't skip.

## Banner rendering (in-body for v1)

The banner is prepended to `compiled_truth` when `compile_chat_synthesis` writes a chat-derived article:

```
> _Source: chat-derived synthesis (no transcript). User-approved on 2026-05-10._

## Overview
...
```

The exact banner text uses `> _Source: chat-derived synthesis (no transcript). User-approved on <YYYY-MM-DD>._` as a sentinel. The leading blockquote + italic markers + the literal phrase "chat-derived synthesis" are the load-bearing parts; downstream code can detect a chat-derived article by matching the sentinel substring.

`merge_chat_synthesis_into_existing` and any future merge path that touches a chat-derived article must preserve the banner: read the existing compiled_truth, detect the sentinel, ensure the resulting compiled_truth still begins with it. A round-trip test enforces this.

`source_type` still lives on `meta.sources[]` entries (the structured-metadata source of truth). The in-body banner is the v1 display surface; render-time rendering from metadata is deferred to the broader tool/prompt audit pass.

### Companion system prompt rule

The banner alone is text PAL might or might not surface in answers. To make it functional, the per-channel system prompt builder gets one short addition:

> When a retrieved article's body begins with `> _Source: chat-derived synthesis`, this article was synthesized from a prior conversation rather than external research. When citing or relying on it, briefly note this provenance to the user (e.g., "in a previous chat we discussed..."). Do not treat chat-derived articles as having the same evidentiary weight as articles compiled from external documents.

Without this rule, the banner is decoration. With it, the banner becomes a behavioral hook that PAL can act on.

## Nudge mechanism

Soft, prompt-driven, no state machine. System-prompt addendum (in the per-channel system prompt builder):

> When a conversation has produced durable factual knowledge worth keeping, especially on a topic that doesn't already have a wiki article, you may suggest *once per conversation context*: *"Want me to promote this thread about &lt;topic&gt; into the wiki?"* Do not call `propose_promote_synthesis` unprompted; wait for the user to say yes.

The "once per conversation" cap is not enforced in code. If PAL nudges twice, that becomes a feedback-memory tuning moment.

## Approval registry

One new proposal kind: `promote_synthesis`.

Fields: `note_path`, `title`, `rationale`. Single-use, follows existing `pending → approved → consumed` lifecycle, expires on the same timeout as other proposals. Edit-on-decline supported.

## Source-type taxonomy

| `source_type` | `source_file` points at        | Banner | Verifiability                      |
|---------------|--------------------------------|--------|------------------------------------|
| `external`    | original URL or fetched doc    | No     | Re-fetchable from outside world    |
| `chat`        | `raw/notes/...` (synthesis)    | Yes    | User-approved, no transcript       |

Two types only. The trust invariant shifts from "every article has external provenance" to "every article has *typed* provenance, and `chat` is visibly marked at read time."

## Retrieval implications

- Compiled articles get indexed normally. No banner in body, so no banner-text noise in retrieval.
- `meta.sources[].source_type` is structured metadata; if it gets indexed alongside body content, that's a small amount of frontmatter text and acceptable.
- Future: `source_type` filter on retrieval queries (e.g., "only external-sourced articles for this answer") becomes possible. Not built now; not precluded.

## Risks

1. **No transcript audit.** Once promoted, the actual conversation that produced the synthesis is not on disk. Six months from now, you cannot reconstruct what was actually said, only what PAL synthesized and you approved. Mitigation: the banner makes this trust level explicit; if audit becomes load-bearing, v2 adds snapshots additively (new articles get `source_type: chat_with_transcript`, old articles remain `chat`).

2. **`source_type` parser drop.** Addressed in "Plumbing risks." Round-trip test required.

3. **Note collision on backfill.** If two orphan notes have similar titles, the slug collision check might not catch semantically-similar duplicates. Out of scope; user can handle manually.

4. **PAL writing notes from memory that the user rubber-stamps.** The whole design rests on the user actually reading the synthesis in the approval CLI. If the user defaults to "approve," articles get promoted with no real review. Mitigation: banner makes the article's provenance honest at retrieval time, even if approval was sloppy.

## Migration

- Existing articles without `source_type` field default to `external`. No migration script required.
- Existing `append_timeline_entry` signature gains optional `source_type` parameter, backward-compatible.
- Existing `raw/notes/` files become candidates for `propose_promote_synthesis` once shipped. Same tool, no separate backfill path.

## Verification

- Unit tests for `propose_promote_synthesis`: parameter validation, approval flow, error shapes, summary writing.
- Unit tests for `Compiler.compile_chat_synthesis`: synthesis-as-compiled-truth (no LLM call), categorization, topic-match-then-merge branch, write + commit + reindex.
- Round-trip test for `_format_timeline_entry` / `_parse_timeline_entries` / `TimelineEntry`: assert `source_type` survives serialize → parse → serialize.
- Integration test: end-to-end forward promotion (PAL writes note, propose_promote_synthesis is called, user approves, article exists with `source_type: chat` in `meta.sources`).
- Integration test: end-to-end backfill promotion of an existing orphan note, asserting same.
- Integration test: chat-derived summary that topic-matches an existing article, asserting merge path runs and merged article retains `source_type` on the new timeline entry.
- Regression test: existing compile path still works for `source_type: external` (default behavior unchanged).
- Manual smoke: nudge prompt addendum, real conversation, real promotion, retrieval over the resulting article, verify banner renders in chat answer.

## Out of scope (deferred)

- `raw/conversations/` snapshot machinery and surface-specific source types (deferred to v2 if transcript audit becomes load-bearing).
- Dereferenceable Discord message IDs and `[msg:id]` context tagging.
- Bridge protocol changes (no daemon→bridge RPC needed for this design).
- `source_type` filter on retrieval queries.
- Migration script to backfill `source_type: external` on existing article frontmatter (default handles this).
- `search_vault` returning exact paths as a primary field (separate workstream, see `project_pal_path_determinism` memory).
- Batch-tool "did you mean" error suggestions (separate workstream, same memory).
- Render-time banner from `meta.sources[].source_type` (deferred to the prompt + tool audit pass, where `search_vault` and adjacent surfaces are touched anyway).

## Panel review notes (2026-05-10)

This design is the product of a four-reviewer critique panel (architect, epistemology critic, YAGNI skeptic, implementation realist) on an earlier draft. Key revisions from the panel:

- **Double-LLM removal.** Earlier draft fed approved synthesis through `compile_one`'s extraction prompt, throwing away the user-approved text. New chat-aware compile entrypoint treats synthesis as compiled truth directly. (Architect, epistemology, realist all flagged.)
- **Snapshot deferral.** Earlier draft created `raw/conversations/` and required bridge changes to resolve Discord message IDs. Realist confirmed the daemon→bridge RPC channel does not exist; YAGNI argued snapshots aren't load-bearing for v1. Deferred to v2.
- **Source-type collapsed.** Earlier draft had four types (`external`, `chat_discord`, `chat_cli`, `user_attested`); now two (`external`, `chat`). Backfill and forward use the same path.
- **Tools collapsed.** Earlier draft had four tools (propose/execute pairs for both chat and orphan-note); now one tool that handles both cases via a `note_path` parameter.
- **Plumbing risk surfaced.** Realist found that `_parse_timeline_entries` would silently drop `source_type` without explicit parser changes. Now a required spec item with a round-trip test.

Then, during implementation planning, one further revision after grounding in the actual code:

- **Banner location: in-body for v1, render-time deferred.** Architect and epistemology argued for render-time. Implementation discovery showed render-time requires modifying `search_vault` (in agent_core, cross-package). For v1, banner is in-body with explicit merge-preservation. Render-time migration is parked for the prompt + tool audit pass, which already needs to touch `search_vault` for the path-determinism fix. A companion system prompt rule makes the banner functional rather than decorative.
