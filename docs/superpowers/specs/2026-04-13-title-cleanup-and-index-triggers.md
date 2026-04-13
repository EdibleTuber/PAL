# Title Cleanup and Index Trigger Fix

## Context

Two related problems surfaced during the 2026-04-13 batch compile run:

1. **Ugly titles in compiled articles.** The summarizer at `pal/summarizer.py:74` passes the `title` field from raw frontmatter through to the summary frontmatter unchanged. For web sources, that title is the raw HTML `<title>` tag, which for pages like GitHub repos is effectively a paragraph. Example from `claude-code-mcp-integration-...-6999ca7b.md`:

   > *"GitHub - codeaashu/claude-code: Claude Code is an agentic coding tool that lives in your terminal, understands your codebase, and helps you code faster by executing routine tasks, explaining complex code, and handling git workflows - all through natural language commands. · GitHub"*

   These titles flow into compiled article frontmatter, the wiki index, the categorizer, and topic matching. They actively degrade categorization quality and dedup, not just display.

2. **`_index.md` triggering gaps.** The original framing in `docs/index_problem_and_future_direction_talk.md` claimed the index was LLM-maintained. That is wrong: `WikiManager.rebuild_index()` already walks the vault deterministically, and the LLM cannot write system paths (blocked at `tools.py:253`). The actual problem is that `rebuild_index()` is only called after `/save` (`daemon.py:531`), `/compile` (1051), and `/import` (1296). It is not called after the `edit_file` or `create_file` chat tools, or after manual vault modifications between daemon sessions.

## Goals

- Summarizer emits a clean, human-readable `title:` for every new summary.
- Existing compiled articles get their titles regenerated so the wiki is uniformly clean after this work lands.
- `_index.md` stays accurate across every write path, every daemon startup, and every bulk operation.

## Non-goals

- Fixing the raw file titles (upstream of summarization). Not worth the churn, since the summarizer is what should own title quality.
- Adding a filesystem watcher on the vault to catch external modifications mid-session. Flagged as a known gap but out of scope.
- Fixing the vector index staleness (Problem 1 from `index_problem_and_future_direction_talk.md`). That lives in `inference-server` and is a separate track.
- Adding a `delete_file` tool. Not needed to fix what is broken today.

## Design

### Part 1: Summarizer emits clean title

Modify `pal/summarizer.py` so the summarizer's LLM call produces both a title and a summary body in a single response. No additional inference call.

**Prompt format.** Instruct the model to emit its response as:

```
TITLE: <clean title>

<summary body>
```

**Title rules in the prompt:**

- Max ~80 characters.
- Strip trailing site suffixes like `| Docs`, `· GitHub`, `- Stack Overflow`.
- Sentence case.
- No surrounding quotes.
- Describe what the content is, not where it lives (prefer "Claude Code CLI agentic coding tool" over "GitHub - codeaashu/claude-code").

**Parsing.** Split the model output on the first blank line after `TITLE:`. If the response does not start with `TITLE:`, log a warning and fall back to `raw_stem` (current behavior). The summary body is everything after the title block.

**Write path.** `summary_meta["title"]` receives the parsed clean title instead of `raw_meta.get("title", raw_stem)`.

### Part 2: Article title backfill

Add a one-off CLI command `pal backfill-titles` that walks every compiled article in the vault, detects heuristically-bad titles, regenerates them from `compiled_truth`, and writes the update.

**Bad-title heuristic.** Flag a title for regeneration if any of:

- Length > 80 characters.
- Contains ` · ` (common site suffix separator).
- Contains ` | ` (common site suffix separator).
- Starts with `GitHub -`.
- Ends with ` · GitHub` or similar trailing site names.

Articles with acceptable titles are not touched.

**Regeneration.** For each flagged article, run an inference call via `inference.complete(..., reasoning="off")` with the article's `compiled_truth` as user content and the title rules from Part 1 as the system prompt. The response is parsed for `TITLE:` using the same parser as Part 1. This uses `compiled_truth` rather than the summary body because the compiled article is the current, possibly-merged state; its truth is what the title should describe.

**Scope of changes.** Only the `title` field in article frontmatter is modified. `compiled_truth`, timeline, sources, created/updated timestamps, and all other fields are preserved. The `updated` timestamp is refreshed on write.

**Modes.**

- Default (dry-run): print proposed changes as `path | old title -> new title`. Do not write.
- `--apply`: write updates via `WikiManager.write_article`, suppressing per-write index rebuild (see Part 3), and call `rebuild_index()` once at the end.

**Failure handling.** If title regeneration fails for an article (inference error, empty response), log and skip. Continue with the rest. Print a summary at the end: N processed, M updated, K skipped.

**Commit strategy.** One git commit at the end of the apply run with a summary message, not per-article commits.

### Part 3: Indexer trigger fix

Move the index rebuild into `WikiManager.write_article` so it runs automatically on every article write.

**Signature change.** Add a `rebuild_index: bool = True` kwarg to `write_article`. Default behavior is to rebuild after each write. Callers that do bulk operations pass `rebuild_index=False` and are responsible for calling `rebuild_index()` once at the end.

**Remove redundant callers.** Delete the three existing `self.wiki.rebuild_index()` calls at `daemon.py:531`, `1051`, and `1296`. They become redundant once the rebuild lives inside `write_article`.

**Add startup rebuild.** In the daemon init path, after `self.wiki` is constructed, call `self.wiki.rebuild_index()` once. This closes the gap for manual vault modifications between daemon sessions and for first-ever daemon starts on an existing vault.

**Performance.** Rebuild walks the vault and reads frontmatter from every article. For a vault of ~1000 articles, this is sub-second. Acceptable for interactive chat-tool writes.

**Future delete tool.** If a `delete_file` tool is added to `tools.py` later, it should call `rebuild_index()` at its end. Not in scope for this spec, noted as a follow-up.

## Data flow after this lands

1. User runs `/fetch <url>`: raw file has ugly HTML title. Unchanged.
2. User runs `/summarize`: Part 1 makes summarizer emit a clean title into summary frontmatter.
3. User runs `/compile`: clean title flows into the compiled article's frontmatter. `write_article` rebuilds `_index.md` automatically via Part 3.
4. Model creates or edits an article via chat tools: `write_article` rebuilds `_index.md` automatically via Part 3.
5. Daemon restarts after manual vault edits: startup rebuild via Part 3 catches the drift.
6. One-time, after Parts 1 and 3 are merged: user runs `pal backfill-titles --apply` to clean up the 526+ existing articles with ugly titles. Part 2 handles this.

## Invariants after this lands

- Every new summary has a clean title.
- Every new compiled article has a clean title (flows from the summary).
- `_index.md` is accurate at daemon start and after every article write.
- The only drift window is "external process modifies the vault while the daemon is running without going through `write_article`." This is a known, accepted gap.

## Rollout order

1. Land Part 3 first. It is the smallest change and closes the triggering gap immediately. No user-facing effect, just correctness.
2. Land Part 1. New summaries start producing clean titles. No change to existing vault state.
3. Run Part 2's backfill after Parts 1 and 3 are validated. This is the user-visible cleanup.

Parts 1 and 3 are independent and can land in either order, but 3-before-1 is slightly safer because it guarantees the index tracks whatever titles Part 1 produces from the moment they exist.

## Testing

**Part 1 (summarizer):**

- Unit test: mock inference response starting with `TITLE:`, verify parsed title matches and body is stripped of title block.
- Unit test: mock inference response with no `TITLE:` prefix, verify fallback to `raw_stem` and warning logged.
- Integration test: end-to-end summarize of a fixture raw file with an ugly HTML title, verify the summary frontmatter has a clean title under 80 chars.

**Part 2 (backfill):**

- Unit test: heuristic flags articles with long titles, ` · ` separators, and `GitHub -` prefixes. Does not flag clean titles.
- Unit test: dry-run produces expected stdout format and writes nothing.
- Integration test: apply mode updates flagged articles in a fixture vault, skips clean ones, produces one git commit, rebuilds the index once at the end.

**Part 3 (indexer triggers):**

- Unit test: `write_article(rebuild_index=True)` rebuilds the index. `write_article(rebuild_index=False)` does not.
- Unit test: daemon startup rebuild produces an accurate `_index.md` when the vault was modified externally between sessions.
- Regression test: existing `/compile` and `/import` flows still produce a correct `_index.md` after the three redundant `rebuild_index()` calls are removed.
