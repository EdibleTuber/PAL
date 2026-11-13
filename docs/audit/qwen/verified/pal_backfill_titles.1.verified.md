# Verified: pal_backfill_titles p2+p3+p4+p5 (task 1/1)

## Ruled out

p2:66 — Exception handler catches UnicodeDecodeError/OSError, logs with rel path, increments skipped_error, continues; correct per-article best-effort.
p2:82 — Exception handler catches parse errors, logs with rel path, increments skipped_error, continues; correct per-article best-effort.
p2:92 — Exception handler catches inference errors, logs with rel path, increments skipped_error, continues; correct per-article best-effort.
p3:68,74,77,84,94,98,101,102 — All 6 report counter/list updates on a local BackfillReport dataclass in a single synchronous/async walk; no shared state, no lock needed.
p3:115,118,119 — Duplicate of p4 leads; no separate state-write substance.
p4:65 — File read with exception handler at :66; a query, not a mutation. Correctly classified.
p4:115 — md_file.write_text has no handler, but this is a one-shot re-runnable script; mid-batch failure leaves partial state that git preserves and the heuristic will re-find on retry. Caller (backfill_main.py:61) lets exception propagate to asyncio.run which prints traceback; acceptable for a maintenance script.
p4:118 — Mislabeled kind "fs_query"; it is wiki.rebuild_index() which writes vault-internal _index.md (pal/wiki.py:143-144). Established vault-internal effect; no separate finding.
p4:119 — wiki.git_commit uses subprocess check=True (pal/wiki.py:187,200) and can raise after writes land; caller (backfill_main.py:61) does not catch, propagates to asyncio.run. Given one-shot nature and that writes already landed on disk, this is acceptable.
p5:6 (frontmatter), :6 (index rebuild), :20 (skip dirs), :42 (walk), :42 (regenerate), :42 (write), :48 (apply=True), :48 (apply=False), :51 (counts), :51 (change list), :57 (skip _ dirs), :60 (skip top-level), :107 (update title), :107 (rewrite article), :107 (preserve timeline), :108 (preserve all Article fields) — All 16 claims consistent with code.
p5:47 — "Inference client with async .complete() method" verified: agent_core/inference.py line 227 is `async def complete` (confirmed via grep).
p5:108 — "WikiManager.write_article would drop other Article fields" verified: pal/wiki.py:68-74 shows write_article builds a fresh meta dict with only title/created/updated/tags, confirming the claim; this is the justification for writing serialize_article directly.

## Notes

- p5 verdicts observed: consistent=18 (16 directly verifiable from this file, 2 resolved by chain-following to agent_core/inference.py:227 and pal/wiki.py:68-74), contradicted=0, cannot_tell=0.
- p4:118 kind is mislabeled as "fs_query" but should be "file_write" (writes _index.md). Not a finding — it's a metadata labeling issue for an already-established vault-internal effect.
- p4:115 and p4:119 share the same substance: mid-to-late batch failures in a one-shot script. The writes are not atomic (write_text is a single syscall but not crash-safe), and git_commit can raise after writes land. However, the script is designed to be re-runnable (the bad-title heuristic will re-find remaining articles), the state is pre-versioned in git, and the caller is a standalone CLI that prints a report then exits. A partial run is acceptable for this maintenance tool.
- Quote/schema mismatches: none. All p2/p3/p4/p5 lead quotes match the source code.
- The BackfillReport dataclass (pal/backfill_titles.py:28-33) surfaces only aggregate counts (processed, updated, skipped_clean, skipped_error) plus a changes list of (path, old_title, new_title) tuples. There is no per-article error detail beyond the count — this is appropriate for a one-shot best-effort walk.
