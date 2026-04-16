---
title: PAL Learning Capture + move_file Design
date: 2026-04-16
status: draft
---

# PAL Learning Capture + move_file Design

## Context

PAL has a learning pipeline: `/learn` extracts candidate lessons from conversation into `_learning/`, `/rate` scores them, `/promote` elevates them to `_wisdom/`, and wisdom entries are injected into every system prompt via `SystemPromptBuilder.build()`. Storage, promotion, and injection all work. Verified 2026-04-16: three real learnings were extracted from the prior night's Discord session, including one (`granularity-over-consolidation`) that captures a direct user correction about PAL's tendency to over-merge articles.

The pipeline's weakness is capture, not storage. Three gaps:

1. **PAL cannot capture in-band.** The LLM has no `add_learning` or `add_wisdom` tool. When the user says "make a learning out of that" in Discord, PAL acknowledges but the learning is only saved if the user subsequently invokes `!learn` themselves. Silent failure.
2. **Capture requires the user to remember the commands.** The Discord `!learn` / `!wisdom` / `!promote` surface exists but is undocumented in the splash, `/help`, and system prompt. PAL cannot reliably tell the user how to invoke it.
3. **No lightweight move primitive.** `propose_reorg` exists but is batch- and approval-oriented. Moving a single mis-categorized article (e.g. an IoT methodology file placed under `Security/`) is disproportionately heavy.

This spec closes all three gaps.

## Non-goals

- Rewriting the learning/wisdom storage format.
- Changing the `/learn` extraction logic itself.
- Replacing `propose_reorg` or `propose_consolidate`.
- Auto-promoting learnings to wisdom without user approval.
- Building a rejected-candidate memory (scanner is stateless by design).

## Architecture

Three bundles, one spec, all inside the `pal` package.

### Bundle A - LLM-facing capture tools

Three new tools registered in `pal/tools.py`:

- **`add_learning(title, body)`** - direct write to `_learning/`. No approval. Reuses `LearningManager.add(title, body, source="conversation")`. Rationale: learnings are a holding pen, not active guidance; the cost of a noisy learning is low because promotion remains user-gated.
- **`propose_promote(slug, rationale)`** - approval-gated. Reuses existing proposal/approval infrastructure. On approve: `LearningManager.mark_promoted(slug)` + `WisdomManager.add(title, body)` + git commit. Rationale: wisdom is injected into every system prompt and shapes future behavior; keep it gated.
- **`move_file(src, dst)`** - direct, single-op. Reuses the move primitive currently embedded in the `reorg` handler but without the batch/approval wrapper. Triggers reindex. Rationale: single moves are reversible, and the existing `edit_file` and `create_file` tools already mutate the vault without approval.

### Bundle B - Proactive learning scanner

New module `pal/learning_scanner.py`. Fires after each LLM turn completes.

Two stages:

1. **Pre-filter (sync, cheap):** regex test on the latest user message against a signal pattern set: `actually`, `no(,| )`, `stop`, `you (always|never|should|shouldn't|tend to)`, `exactly`, `perfect`, `thank you`, `you're right`, `that's wrong`, etc. Pattern list lives in a config constant for easy tuning.
2. **Extraction call (async, on match):** single inference request with a tight prompt:

   > Recent conversation: `{last N turns, default N=6}`. User signal message: `{msg}`. Is there a durable lesson worth saving as a learning? Return JSON `{"title": "...", "body": "..."}` or `null`.

   No tools. Small context. 15s timeout.

If extraction returns a candidate, dedupe against `_learning/` by slug similarity. If not a near-duplicate, emit a `LearningCandidateProposal` over the active connection.

User sees an approval prompt (Discord buttons or CLI prompt): *"Save as learning? **{title}** - {body preview}"* with Approve / Edit / Skip. On Approve, `LearningManager.add()` fires. On Edit, modal. On Skip, discard (no rejected-candidate state kept).

### Bundle C - Command registry + discoverability

Single source of truth for every user-facing command.

- **`pal/commands.py`** - `COMMANDS` constant: list of `(name, args_hint, description)` tuples.
- **CLI splash** (`pal/cli.py` around line 208) renders a two-line compact form from `COMMANDS`.
- **`/help` handler** (`pal/daemon.py` around line 407) renders the full form from `COMMANDS`.
- **System prompt** (`pal/prompt_builder.py`) appends an `## Available Commands` section from `COMMANDS` so PAL can accurately answer "what can I type?".
- **Discord adapter** (`pal/discord_adapter.py`) rewrites `/cmd` → `!cmd` in outbound text for any name in `COMMANDS`. Regex matches standalone tokens at line start or after punctuation; does not rewrite inside fenced code blocks.
- **Drift check test** - asserts every `msg.name == "foo"` branch in `pal/daemon.py` has a matching `COMMANDS` entry and vice versa. AST-based to resist false hits in comments.

## Data flow

### Proactive-scan turn

1. User sends a message; Discord adapter forwards to daemon.
2. Daemon appends to conversation, dispatches to LLM.
3. LLM reply streams to user normally. Scanner does not block.
4. After turn completes, daemon calls `learning_scanner.maybe_scan(conversation, latest_user_msg)`.
5. Pre-filter evaluates. No match → silent return.
6. Match → extraction inference call with 6-turn window. Timeout 15s.
7. Null return → silent. Non-null candidate → dedupe.
8. Novel candidate → emit `LearningCandidateProposal` over the active connection.
9. User clicks Approve → `LearningManager.add()` → git commit → reindex queued → confirmation back to user.
10. If another signal fires while a proposal is pending, the new candidate is queued, not dispatched, to avoid stacking approval prompts.

### In-band tool calls

- **`add_learning(title, body)`**: PAL tool call during reply. Handler validates non-empty fields, calls `LearningManager.add()`, returns `{slug}`. Progress line `*[saving learning: {slug}]*` renders in Discord.
- **`propose_promote(slug, rationale)`**: PAL tool call. Handler emits a `PromoteProposal`. User approves → promoted. User declines → learning stays unpromoted in `_learning/`.
- **`move_file(src, dst)`**: PAL tool call. Handler validates (src exists, dst does not, neither in `raw/` or `_`), invokes the shared move primitive, triggers reindex. Returns `{moved: "src→dst", reindex_queued: true}`.

## Components

### New files

- `pal/commands.py`
- `pal/learning_scanner.py`
- `tests/test_tools_learning_wisdom.py`
- `tests/test_learning_scanner.py`
- `tests/test_commands_drift.py`
- `tests/test_learning_e2e.py`

### Modified files

- `pal/tools.py` - three new tool schemas.
- `pal/daemon.py` - handler wiring for the three tools; scanner hook in the post-turn path; `/help` renders from `COMMANDS`.
- `pal/cli.py` - splash renders from `COMMANDS`.
- `pal/discord_adapter.py` - outbound `/cmd` → `!cmd` rewrite.
- `pal/prompt_builder.py` - appends `## Available Commands` section.
- `pal/protocol.py` - `LearningCandidateProposal` message; `PromoteProposal` message (may reuse a generic proposal envelope if present).
- `pal/discord_interactions.py` - approval button handlers for the two new proposal kinds.
- `pal/learning.py` - minor: `get_meta(slug)` and `exists(slug)` helpers.

### Unchanged but referenced

- `pal/wisdom.py` - `add()` covers the promote target.
- `pal/retrieval.py` and the reindex trigger - `move_file` piggybacks on the existing post-mutation hook used by `edit_file` and `create_file`.

## Error handling

### add_learning

- Empty title or body → tool error; PAL retries or abandons.
- Slug collision → append `-2` suffix, consistent with `create_file`.
- Vault unwritable → error bubbles.

### propose_promote

- Slug not found → error `"no such learning: {slug}"`.
- Already promoted → error `"already promoted at {timestamp}"`.
- Proposal times out → match existing research/compile proposal timeout. No new timeout constant.

### move_file

- src missing → error `"source not found"`.
- dst exists → error `"destination exists; use propose_reorg with op=merge"`.
- src or dst begins with `raw/` or `_` → error `"system directory; use ingestion flow"`.
- dst parent missing → auto-create parents.
- Reindex fails post-move → log, continue. The move is already committed; reindex backfills.
- Git commit failure mid-move → restore via git, return error.

### Scanner

- Pre-filter false positive → extraction returns null → silent. Expected.
- Extraction returns malformed JSON → log, treat as null. No retry.
- Duplicate candidate → silent skip.
- Scan fires mid-tool-burst → wait until the turn's tool calls have all drained before emitting proposal.
- Extraction timeout (15s) → silent. Never blocks user.

### Discord rewrite

- Rewrite applies only to standalone command-shaped tokens (line start or after punctuation). Skips fenced code blocks.
- Unknown command in outbound text → not rewritten (safer than false positives).

## Testing

### Unit

- **`test_tools_learning_wisdom.py`** - `add_learning` writes correct frontmatter, dedupes slug collisions, rejects empty fields. `propose_promote` emits proposal, approve-path calls the right methods, errors on missing/already-promoted. `move_file` moves, triggers reindex, rejects bad paths.
- **`test_learning_scanner.py`** - signal pre-filter matches known phrases, rejects neutral prose. Mocked extraction returns candidate → proposal emitted. Null → silent. Dedupe against fixture `_learning/` contents. Timeout → silent. Backpressure: second signal while pending → queued, not dropped.
- **`test_commands_drift.py`** - AST parse of `pal/daemon.py`, symmetric-diff check against `COMMANDS`. Fails with offending name.

### Integration

- **`test_learning_e2e.py`** - user chat → LLM reply → scanner → candidate → approval → file on disk → git committed → reindex queued. Splash output contains every `COMMANDS` name. `/help` output contains every `COMMANDS` name. Discord adapter translates `/learn` → `!learn` in response text.

### Regression

- Existing `/learn`, `/learnings`, `/promote`, `/wisdom add` tests pass unchanged.
- Existing `propose_reorg` op=move path still works.

## Out of scope

- Iterative tuning of signal patterns and extraction prompt. Ship v1, observe, tune.
- A rejected-candidate corpus to bias future scans.
- Promoting learnings without explicit user approval.
- A CLI-side `/move` symmetrical with the tool (user can move on the filesystem directly if needed).
