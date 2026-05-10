# Chat-Derived Knowledge Promotion

**Date:** 2026-05-10
**Status:** Design
**Author:** Brainstormed with Claude

## Problem

PAL's promotion pipeline (`compile_summary`, `propose_compile_batch` → `compile_one`) gates every article on a non-empty `source_url` or `source_file` in the summary frontmatter. The check lives at `pal/compiler.py:124` and `:301` and exists to guarantee that every wiki article has verifiable provenance back to a fetched URL or an on-disk document.

Knowledge that emerges purely from conversation has neither. PAL's only currently-available reaction is to write a note in `raw/notes/` from memory and refuse to promote it, leaving the knowledge stuck in raw staging where it pollutes retrieval but never reaches the wiki. The accumulation observed on 2026-05-10 (the "vibe-coding-comprehension-strategies" example) is the predictable outcome of this gap.

The "articles are substrate, chat is the product" framing makes this gap load-bearing: chat is the surface the user actually consumes, so chat-derived knowledge is exactly what should be reaching the substrate, not what should be silently dropped.

## Goals

1. Allow chat conversations to be promoted into wiki articles through a first-class tool path.
2. Preserve the trust invariant that every article has typed, recorded provenance.
3. Provide a one-time backfill path for orphan notes already sitting in `raw/notes/`.
4. Make weaker provenance types visible in the article body so trust level is obvious at read time.
5. Preserve the existing `compile_one` pipeline; new tools should feed it, not replace it.

## Non-goals

1. Removing the `source_url`/`source_file` gate. The gate remains and continues to enforce provenance; this design adds new ways to satisfy it, not ways around it.
2. Re-snapshotting historical conversations whose buffers are gone. CLI sessions and Discord history beyond retention cannot be reconstructed; orphan notes get the `user_attested` path instead.
3. Auto-promotion. PAL never calls a promotion tool unprompted; it may nudge once per conversation, but execution always requires user approval through the existing approval registry.
4. Fixing the unrelated `raw/` indexing leak (already tracked separately in memory). The new `raw/conversations/` directory inherits that bug and will be cleaned up as part of that workstream, not this one.

## Source semantics

Chat-derived content uses a hybrid provenance model: a dereferenceable range pointer plus an on-disk snapshot.

- **Range pointer**: identifies the conversation slice (Discord channel + start/end message IDs, or CLI session + turn range) that produced the article. Allows a future reader to jump back into the source surface.
- **Snapshot**: a verbatim transcript of the slice, written to `raw/conversations/...` at promotion time and committed to the vault git history. Survives Discord deletions, edits, and CLI session loss.

Both pieces are recorded in the snapshot frontmatter. The snapshot file is what the summary frontmatter's `source_file` points at, satisfying the existing gate.

For CLI surfaces, the dereferenceable pointer becomes non-resolvable once the session ends. The snapshot is then the sole provenance. This is acknowledged as a property of the surface, not a defect.

## Pipeline shape

```
Discord channel  or  CLI session
   │  start_marker..end_marker (resolved by bridge)
   ▼
raw/conversations/<surface>/<channel-or-session-slug>/<YYYYMMDD-HHMMSS>-<topic-slug>.md
   │  speaker-labeled transcript, frontmatter with channel + ID range + source_type
   ▼
raw/summaries/<topic-slug>.md
   │  body = transcript + "---" + "## Synthesis" (PAL's distilled understanding)
   │  frontmatter source_file → snapshot, source_type = chat_discord | chat_cli
   ▼
existing compile_one()  (unchanged gate, new banner)
   │
   ▼
<Category>/<topic-slug>.md   (article with source_type-aware banner)
   │
   ▼
archive_raw_files moves snapshot + summary into raw/_archive/
```

For orphan-note backfill, the pipeline is shorter (no snapshot fetch):

```
existing raw/notes/<orphan>.md
   │
   ▼
raw/summaries/<topic-slug>.md
   │  source_file → orphan note, source_type = user_attested
   ▼
existing compile_one()  →  <Category>/<topic-slug>.md
```

## File model

### `raw/conversations/<surface>/<channel-or-session-slug>/<timestamp>-<slug>.md`

New directory under the vault. Holds verbatim transcript snapshots produced at promotion time.

Discord variant frontmatter:
```yaml
title: "Vibe-coding comprehension strategies"
source_type: chat_discord
channel_id: "1234567890"
channel_name: "general"
start_message_id: "..."
end_message_id: "..."
captured_at: "2026-05-10T15:23:00+00:00"
participants: ["edibletuber", "pal-app"]
message_count: 47
```

CLI variant frontmatter:
```yaml
title: "Vibe-coding comprehension strategies"
source_type: chat_cli
session_id: "<uuid>"
start_turn: 12
end_turn: 47
captured_at: "2026-05-10T15:23:00+00:00"
```

Body: speaker-labeled transcript, format:
```
**edibletuber** (15:21:03):
How do I think about vibe-coding comprehension?

**PAL** (15:21:18):
A few angles worth pulling apart...
```

### `raw/summaries/<topic-slug>.md` (chat-derived path)

Reuses the existing summary location. Frontmatter:
```yaml
title: "Vibe-coding comprehension strategies"
source_file: "raw/conversations/discord/general/20260510-152300-vibe-coding.md"
source_url: ""
source_type: chat_discord
source_hash: "<sha1 of snapshot body>"
source_raw: "raw/conversations/discord/general/20260510-152300-vibe-coding.md"
```

Body: full transcript verbatim, then `---`, then a `## Synthesis` section containing PAL's distilled understanding of the conversation.

### `raw/summaries/<topic-slug>.md` (user-attested path)

For orphan notes:
```yaml
title: "Vibe-coding comprehension strategies"
source_file: "raw/notes/vibe-coding-comprehension-strategies.md"
source_url: ""
source_type: user_attested
source_hash: "<sha1 of note body>"
source_raw: "raw/notes/vibe-coding-comprehension-strategies.md"
```

Body: the orphan note's body verbatim. No synthesis section (the note already is the synthesis).

## Tool contracts

### `propose_promote_chat`

Blocking. Surfaces an approval prompt. Mirrors `propose_consolidate`.

```python
parameters = {
    "start_marker": str,   # Discord msg ID, CLI turn number, or "first"
    "end_marker":   str,   # Discord msg ID, CLI turn number, or "last"
    "title":        str,   # proposed article title
    "rationale":    str,   # one-line for approval prompt
    "synthesis":    str,   # PAL's distilled understanding (~200-1000 words)
}
```

PAL composes `synthesis` from its current context before calling. User sees title, range summary, rationale, and full synthesis in the approval CLI. Edit-on-decline supported.

Returns standard approval-flow result: `{proposal_id, status}` with `status in {approved, declined, expired}`.

### `promote_chat`

Execution. Takes an approved `proposal_id`.

Flow:
1. Bridge resolves `start_marker..end_marker` to actual messages or turns.
2. PAL daemon writes snapshot to `raw/conversations/<surface>/<chan>/<ts>-<slug>.md`.
3. PAL daemon writes summary to `raw/summaries/<slug>.md` with `source_file` pointing at snapshot, `source_type` set, body containing transcript + `---` + `## Synthesis: <approved synthesis>`.
4. Calls existing `compile_one(summary_path)` unchanged.
5. Returns the standard compile result shape: `{status, title, article_path_rel, vault_exists, reindex, _note}`.

### `propose_promote_orphan_note`

Blocking. For backfill of existing `raw/notes/` files.

```python
parameters = {
    "note_path": str,    # path under raw/notes/
    "title":     str,    # proposed article title
    "rationale": str,    # one-line for approval prompt
}
```

User sees `note_path`, body preview, title, rationale in approval CLI.

### `promote_orphan_note`

Execution. Takes approved `proposal_id`. Writes summary with `source_type: user_attested`, `source_file` pointing at the orphan note, then calls `compile_one`.

### Error shapes (returned as structured JSON, not exceptions)

- `unresolvable_range`: bridge couldn't resolve markers (deleted Discord messages, bad IDs, CLI turn out of range).
- `range_empty`: markers resolved to zero content.
- `snapshot_too_large`: parallels the existing `compile_one` size guard at `compiler.py:108`.
- `title_collision`: article already exists at the proposed slug; PAL should retry with a different title or use `propose_consolidate` to merge into the existing article.
- `note_not_found`: orphan note path does not exist or escapes the vault.

## Compiler changes

The gate at `compiler.py:124` and `:301` is preserved. `source_file` non-empty satisfies it; chat-derived and user-attested summaries both pass.

Two adjustments downstream:

1. **`source_type` propagation.** `compile_one` and `merge_into_existing` read `source_type` from the summary frontmatter (default `"external"` when absent for back-compat), and propagate it into the `meta.sources` entry on the article via `append_timeline_entry`. Requires extending `append_timeline_entry` (in `pal/article.py`) to accept and store `source_type`.

2. **In-body banner for non-external types.** When `source_type` is one of `chat_discord`, `chat_cli`, or `user_attested`, `compile_one` prepends a banner to the compiled body before writing:

   - `chat_discord`: `> _Compiled from chat in #<channel_name> (discord) on <date>. Transcript: <snapshot path>._`
   - `chat_cli`: `> _Compiled from CLI chat session on <date>. Transcript: <snapshot path>._`
   - `user_attested`: `> _User-attested chat synthesis. No transcript available; this article reflects the user's recollection of an earlier conversation._`

   Banner is part of the compiled body (not frontmatter only), so it survives merges and shows up in retrieval excerpts.

3. **Source label fallback.** `append_timeline_entry` currently builds `source_label` via `urlparse(source_url).hostname`. With `source_url=""` this yields empty. Fallback: when `source_type` is non-external, derive label from snapshot frontmatter (`"chat: #general (discord, 2026-05-10)"` for chat, `"user-attested note"` for user-attested).

## Bridge integration

### Message ID visibility

PAL's LLM has to be able to reference specific messages to populate `start_marker` and `end_marker`. The per-channel context renderer in the daemon prepends each message with an opaque marker:

```
[msg:1234567890] edibletuber: How do I think about vibe-coding comprehension?
[msg:1234567891] PAL: A few angles worth pulling apart...
```

For CLI: `[turn:12]`. PAL learns by example that these tags are valid `start_marker`/`end_marker` values.

### Marker resolution

The bridge owns the mapping from marker → actual message content. On `promote_chat` execution:

- Discord surface: bridge calls `channel.history(after=start_id, before=end_id)` (discord.py), returns ordered list of `{author, timestamp, content, message_id}`.
- CLI surface: bridge reads the session buffer that already backs `/scratch` (per the per-channel-context infrastructure), slices by turn range.

The PAL daemon process calls into the bridge for resolution, then writes the snapshot file itself. Single filesystem writer; bridge supplies data only.

### Nudge mechanism

Soft, prompt-driven, no state machine. System-prompt addendum (in the per-channel system prompt builder):

> When a conversation has produced durable factual knowledge worth keeping, especially on a topic that doesn't already have a wiki article, you may suggest *once per conversation context*: *"Want me to promote this thread about &lt;topic&gt; into the wiki?"* Do not call `propose_promote_chat` unprompted; wait for the user to say yes.

The "once per conversation" cap is not enforced in code. If PAL nudges twice, that becomes a feedback-memory tuning moment, not a bug.

## Approval registry

Two new proposal kinds: `promote_chat` and `promote_orphan_note`.

`promote_chat` proposal fields:
- `start_marker`, `end_marker`, `title`, `rationale`, `synthesis`, `surface`, `channel_label`

`promote_orphan_note` proposal fields:
- `note_path`, `title`, `rationale`

Both support edit-on-decline (user can adjust title, trim synthesis, etc.).

Both are single-use, follow the existing `pending → approved → consumed` lifecycle, expire on the same timeout as other proposals.

## Source-type taxonomy

| `source_type`     | `source_file` points at         | Banner | Verifiability                            |
|-------------------|---------------------------------|--------|------------------------------------------|
| `external`        | original URL or fetched doc     | No     | Re-fetchable from the outside world      |
| `chat_discord`    | `raw/conversations/discord/...` | Yes    | Snapshot in vault + dereferenceable IDs  |
| `chat_cli`        | `raw/conversations/cli/...`     | Yes    | Snapshot in vault                        |
| `user_attested`   | `raw/notes/...` (orphan note)   | Yes    | User-vouched only, no transcript         |

The trust invariant shifts from "every article has external provenance" to "every article has *typed* provenance, and types weaker than external are visibly marked." This is a deliberate weakening; the banner is what keeps it honest.

## Retrieval implications

- Compiled articles with banners get indexed normally. Banner text becomes part of the searchable content. Slightly noisy but worth it for transparency at read time.
- The new `raw/conversations/` directory should be excluded from indexing. Full transcripts are large and noisy; only the compiled article should surface in retrieval. Add `raw/conversations/` to whatever exclusion rule the indexer uses.
- This intersects with the existing "raw staging files leak into index" issue. Until that workstream lands, snapshots will leak the same way other raw/ contents do. Acknowledged, not addressed here.
- Future: `source_type` filter on retrieval queries (e.g., "only external-sourced articles for this answer") becomes trivially possible because metadata is structured. Not built now; just kept possible.

## Out of scope

- Fixing the existing `raw/` indexing leak (separate workstream per memory).
- Auto-promotion (PAL never calls promotion tools unprompted).
- Re-snapshotting historical conversations whose buffers are gone.
- Cross-channel conversation stitching.
- Promotion of conversations spanning multiple sessions.
- A `source_type` filter on retrieval queries (kept possible, not built).
- Migration tooling to add `source_type: external` to existing article frontmatter (back-compat default handles this).

## Migration

- Existing articles with no `source_type` field are treated as `external` by default. No migration script required.
- Existing `append_timeline_entry` signature gets an optional `source_type` parameter with default `"external"`. Existing call sites continue to work.
- Existing `raw/notes/` files become candidates for `propose_promote_orphan_note` once the tool ships. No automated migration; user triages on their own cadence.

## Risks

1. **Trust regression.** Adding `user_attested` lowers the article-trust ceiling. Banner mitigates but does not eliminate. If banner text is dropped during a future merge that rewrites compiled body, trust signal disappears silently. Mitigation: validate that compiled body retains the banner sentinel during merges; log a warning if missing.

2. **Snapshot bloat.** Conversations can be long. The `snapshot_too_large` guard is defensive but doesn't help retrieval quality if many borderline-large snapshots accumulate. Mitigation: monitor `raw/conversations/` size after a few weeks of use; revisit if it grows fast.

3. **Marker drift.** Discord message IDs prefixed in PAL's context add character overhead per message. For long-context Discord scrollback this adds up. Mitigation: ship un-mitigated, measure; if needed, tag only the last N messages or use shorter alias tags with a per-channel ID map.

4. **Nudge fatigue.** Soft prompt-driven nudge has no hard cap. PAL may nudge too often or in the wrong moments. Mitigation: rely on feedback memory loop; tune the prompt addendum if it becomes a problem.

5. **Self-promotion of low-quality synthesis.** Even with approval gating, a user might rubber-stamp PAL's synthesis without reading carefully. Banner provides post-hoc audit trail; user can always delete the article and re-do.

## Verification

- Unit tests for `propose_promote_chat`, `promote_chat`, `propose_promote_orphan_note`, `promote_orphan_note`: parameter validation, approval flow, snapshot writing, summary writing, error shapes.
- Integration test: end-to-end promotion of a fixture chat snippet through the new tool path, asserting the article exists, contains the banner, and has correct `source_type` propagated to `meta.sources`.
- Integration test: end-to-end orphan-note promotion of a fixture note, asserting same.
- Integration test: existing compile path still works (regression guard on `compile_one` for `source_type: external` default).
- Manual smoke: nudge prompt addendum, real Discord conversation, real promotion, retrieval over the resulting article.
