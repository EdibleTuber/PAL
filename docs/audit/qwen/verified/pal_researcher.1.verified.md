# Verified: pal_researcher p1+p2+p3+p4+p5 (task 1/1)

## F1 [Q][Low] Uncaught filesystem errors in `_fetch_and_save` propagate through `asyncio.gather`, aborting the multi-topic run (subagent filed [A][Medium]; synthesis reclassified Q/Low — recorded as Q5)

The `try/except` block in `_fetch_and_save` (researcher.py:132–149) wraps ONLY `await self.fetcher.fetch(url)`. The vault writes — `raw_dir.mkdir(parents=True, exist_ok=True)` at :167 and `raw_path.write_text(...)` at :179 — are outside it. An OSError there (disk full, read-only FS, permission denied) escapes the fetch task into `await asyncio.gather(*fetch_tasks)` at :227 in `_research_one`; neither `_research_one` nor the sequential topic loop in `research_topics` (:263) has a try, so all remaining topics in the call are aborted and the in-progress `ResearchReport` is discarded — no partial report, earlier topics' completed summaries not aggregated. CORRECTION to the subagent's evidence: `asyncio.gather` does NOT cancel the sibling fetch tasks when one child fails — they keep running in the background, orphaned from the report. Containment: the tool executor catches the escape and returns `f"Error in {name}: {exc}"` to the LLM (agent_core/tools/executor.py:127–131), so the session survives with an opaque one-line error. No persistent state is damaged: fetched files and completed summaries remain valid on disk, each summary write reindexes itself, and re-running is clean (URL-dedup set resets at `research_topics` start, :252; filenames embed content_hash so re-fetch overwrites idempotently). Severity Low (not Medium): rare trigger, in-run aggregation loss only, no stale state — contrast Q3, which leaves `_index.md` stale.

Leads: p1:227, p4:167, p4:179

## Ruled out

- p1:227 (consequence of F1; the fetch tasks themselves are guarded by try/except in `_fetch_and_save` — only the file ops after the fetch succeed are unguarded)
- p1:233 (summarize tasks are guarded: `_summarize` has `except Exception` at line 202 that catches all exceptions and returns a SourceResult with `status="summarize_failed"`)
- p2:134 (FetchError handler is fine — specific catch, logs via `_progress`, returns `SourceResult(status="fetch_failed")`)
- p2:142 (broad `except Exception` returns a valid `SourceResult` with error details; `CancelledError` inherits from `BaseException` in Python 3.8+ so it is **not** caught; caller can detect failure via status field)
- p2:202 (handler modifies source in place and returns it — correct; caller receives the same source object with `status="summarize_failed"` and `error` set)
- p3:95 (constructor `__init__` assignment, no concurrency)
- p3:222 (synchronous `set.add()` before any `await`; topics are sequential per `research_topics` line 255)
- p3:252 (sequential reset at start of `research_topics`; topics process one at a time)
- p3:257–270 (writes to local `ResearchReport` in sequential loop; no concurrent mutation)
- p4:167 (same root cause as F1 — both mkdir and write_text are unguarded; not a separate finding)
- p5:1–13 (all 13 claims are consistent with the code; no contradictions)

## Notes

- p4:167 and p4:179 are mislabelled as separate external effects; both are vault-internal writes under `raw/web/` that are intentional by design. The real issue is they are **unguarded** — covered by F1.
- p2:142 lead describes the handler as catching `Exception` broadly; while technically true, it does not mask `CancelledError` (Python 3.8+ inherits from `BaseException`), and the handler does return a usable `SourceResult`. Not a finding.
- p5 verdicts seen: consistent=13 contradicted=0 cannot_tell=0

## Synthesis adjudication (2026-11-10)

- F1 CONFIRMED as a real unguarded external effect, reclassified [A][Medium] → **[Q][Low]**, recorded as Q5. Own ranged reads: `_fetch_and_save` :130-187 (try wraps only `fetcher.fetch` :132-133; mkdir :167 + write_text :179 outside it; the post-fetch `_progress`/meta build is pure except the two I/O calls); `_research_one` :208-239 (no try around either gather); `research_topics` :241-276 (plain sequential `for` loop, no per-topic try); executor containment at agent_core/tools/executor.py:127-131. Reclassification rationale: (1) no concurrency hazard — the set mutation is synchronous pre-gather, topics are sequential, and gather's real semantics (siblings keep running, not cancelled) create no lost-update or cancellation issue; (2) the consequence is in-run aggregation loss with a clean recovery path, not the persistent stale state of Q3.
- Subagent error corrected: "gather ... cancels all remaining sibling tasks" is false for `asyncio.gather` — on a child failure it propagates immediately and the other pending awaitables continue to run in the background.
- Ruled-out sample spot-checked by synthesis: p1:233/p2:202 — `_summarize` :189-207 guards `summarize_raw_file` with `except Exception` → `logger.warning` + `status="summarize_failed"` (the pre-check early-return is pure, so the :233 gather cannot raise); p2:134/142 — both handlers return structured `SourceResult(status="fetch_failed", error=...)` with `_progress` logging (:134-149); p3:222 — the `for url in urls_to_fetch: self._fetched_urls.add(url)` loop (:220-222) is synchronous and precedes the :227 gather; p3:257-270 — local `report` object, sequential loop.
- p5 spot-checks (synthesis): :251 "processes topics sequentially" — TRUE (plain `for` with `await self._research_one`, no per-topic tasks); :230 "each fetched source is summarized" — TRUE (task built for every source; non-ok sources return unchanged from `_summarize`'s pre-check); :131 "saves raw content to the vault" — TRUE (`vault_path/raw/web/`).
- Subagent's write tool was denied; report persisted by synthesis after the spot-checks above.
- Verdict: 1 finding (Q5, Low — 3 leads: p1:227, p4:167, p4:179), 25 leads ruled out as claimed.
