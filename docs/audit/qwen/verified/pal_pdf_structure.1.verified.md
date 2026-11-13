# Verified: pal_pdf_structure p2+p4+p5 (task 1/1)

## Ruled out

- p2:A:283 — BatchUnavailableError re-raise is intentional; propagates to daemon for fallback proposal (domain.py:147–150).
- p2:A:285 — Exception catch logs warning and returns None; best-effort handling for a fallback detection tier.
- p2:A:302 — JSONDecodeError catch logs and returns None; appropriate for parsing unstructured LLM output.
- p2:A:333 — TypeError/ValueError catch continues loop; skips invalid page values from LLM per-entry, does not lose work.
- p4:282 — LLM HTTP call is guarded by try/except (lines 281–287); failure returns None (single-file fallback), not unguarded.
- p5:47 — Caller (domain.py:213–246) calls extract_chapters and writes each Chapter to disk via article_full.write_text().

## Notes

- p2 part-B leads: 0 (no create_task / fire-and-forget patterns found in this file).
- p2 part-C leads: 0 (no task results to check).
- p5 verdict counts observed: consistent=48, contradicted=0, cannot_tell_from_this_file=1.
- p5:47 was "cannot_tell_from_this_file" — resolved as consistent by following chain to pal/commands/domain.py:213–246, which calls extract_chapters and writes each returned Chapter to disk.
- All 49 p5 claims verified consistent against code; no contradictions found.
- Quote/schema mismatches: none — all p2 quotes match source; p4 kind="http" is correct for inference.complete HTTP call.
- Unverifiable: none.

## Synthesis adjudication (2026-11-10)

- Report persisted by synthesis (subagent's write tool was denied for this task).
- Zero findings re-adjudicated: CONFIRMED. Synthesis ranged reads:
  - p2-A:283/:285 — `inference.complete` in try; `BatchUnavailableError` re-raised with explicit intent comment (fallback proposal flow, cf. A2/Q1 in commands/domain.py); other exceptions logged + `return None` (single-file fallback). As reported.
  - p2-A:302 — JSONDecodeError → warn + return None. p2-A:333 — TypeError/ValueError → per-entry `continue` (bool rejected explicitly at :329-330, page bounds checked at :335). p4:282 guarded at :281–287. All as reported.
  - p5:47 (source line 422, extract_chapters docstring) — resolved TRUE by synthesis: commands/domain.py:213–219 calls `extract_chapters` in executor; :243–245 `article_full.write_text(...)` per chapter. Claim true.
  - p5 consistent spot checks (synthesis): :37→:86–95 (descent replaces entry via `result.extend(children); continue` — TRUE), :102→:117 (`doc.get_toc()` — TRUE), :166→:181–182 (spans `"".join`, lines `" ".join` — TRUE), :197→:231–232 (`len(candidates) < TYPOGRAPHY_MIN_CANDIDATES → None` — TRUE), :398→:404–409 (end = next.start−1 or total_pages−1, inclusive 0-indexed — TRUE).
- Verdict: 54/54 leads ruled out, 0 findings stands.
