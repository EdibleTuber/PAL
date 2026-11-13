# Verified: pal_tools_promote_synthesis p1+p2+p4+p5 (task 1/1)

## Ruled out

**p1:127 (both rows: timeout + event_wait).** Bounded wait `wait_for(proposal.event.wait(), timeout=max(0.1, remaining))` with `except asyncio.TimeoutError: ar.expire_stale()` at line 128, followed by status re-check at lines 131–133. This is the established good pattern (url_fix/consolidate/reorg/research). — *2 leads ruled out.*

**p2:95.** `except Exception:` around `full_note.resolve()` (lines 91–96). `Path.resolve()` can raise `OSError` (broken symlink loop, permissions); the handler returns structured `{"status":"invalid_path","reason":"note_path resolution failed"}`. The path already passed the prefix and no-".." checks. — *Ruled out.*

**p2:111.** `except ValueError as exc:` around `ar.create_proposal(...)` (lines 103–112). Returns `{"status":"error","reason":str(exc)}`. Same pattern as `consolidate.py:77`. — *Ruled out.*

**p2:128.** `except asyncio.TimeoutError:` at line 128 calls `ar.expire_stale()` — the bounded-wait tail of the established pattern. — *Ruled out.*

**p2:161.** `except Exception as exc:` around `compile_chat_synthesis(summary_rel)` (lines 159–166). Sets `outcome = {"status":"error","title":title,"reason":f"{type(exc).__name__}: {exc}"}`. On this error path, `outcome` has no `article_path_rel` and no `vault_exists`. This cannot mislead: `status` is `"error"` with a human-readable reason, there is no `article_path_rel` to "trust", and the `_note` at lines 168–171 says "if vault_exists is false (when set)" — which anticipates exactly this absence. Matches the F2 ruling class. — *Ruled out.*

**p4:87 / p4:92.** `full_note.exists() or not full_note.is_file()` (line 87) and `full_note.resolve()` (line 92) in a try/except (lines 91–96) — both feed structured `invalid_path` / `note_not_found` errors. Sound. — *2 leads ruled out.*

**p4:98.** `note_body = full_note.read_text()` (line 98). Reads from a validated in-vault file (exists + is_file + resolved boundary checks ran moments before). If it raises (e.g., permissions), executor containment turns it into an honest one-line error. — *Ruled out.*

**p4:141.** `if summary_full.exists():` (line 141) — the collision guard. Prevents the real failure mode (LLM reusing a slug) by returning actionable `"summary_collision"` error. Sound. — *Ruled out.*

**p4:146.** `summary_full.parent.mkdir(parents=True, exist_ok=True)` (line 146) — idempotent directory creation. — *Ruled out.*

**p4:157.** `summary_full.write_text(summary_text)` (line 157) — the one substantive write lead. Judged against the url_fix:207 ruling-out class: (a) executor containment wraps the entire `run()` — LLM sees honest error, never false success; (b) nothing non-derivable is lost — source note untouched, summary is pure function of (note body, title, slug, sha1); (c) clean retry via re-propose + re-approve; (d) single-shot write with collision guard immediately before (line 141); (e) consumed-proposal cost aligns with codebase's consume-first design (line 135). — *Ruled out.*

**p4:174.** `outcome["vault_exists"] = (vault_path / article_rel).exists()` (line 174) — computes actual filesystem ground truth. Verified: `compile_chat_synthesis` success path returns `{"status":"ok",..., "article_path_rel":..., ...}` (no `vault_exists`); error paths return no `article_path_rel`. The tool's line 174 is the sole source of `vault_exists`; the `_note` at lines 168–171 anticipates its absence on error paths. — *Ruled out.*

## Notes

- **p1 duplicate:** Two rows in p1 cite the exact same line 127 and quote. Both judged as one.
- **p5 rejects provenance:** All 8 rows rejected for `quote_mismatch` (description spans multiple lines). All 8 claims independently verified as consistent.
- **p4:92 overlap:** Line 92 is the try-body of p2:95's except block. Both judged together.
- **No confirmed findings.** All 20 leads (p1×2 + p2×4 + p4×7 + p5×8) are ruled out or consistent.

## Synthesis adjudication (2026-11-10)

- 0 findings; all 21 inventory rows (20 unique — p1:127 duplicated) ruled out / verified consistent. All subagent rulings confirmed by synthesis ranged reads.
- p4:157 (the one substantive lead) re-verified by synthesis against the unguarded-write ladder: honest executor error (never a false success); source note at raw/notes/<slug>.md is intact and untouched (the summary is a pure re-derivable function of note body + title); clean retry via re-propose + re-approve (the consume-first at :133 is the codebase's consistent pattern, adjudicated in research :202–203 and consolidate :159–160); single-shot write immediately after the collision guard; vault is git-recoverable. Lands in the url_fix:207 ruling-out class, below the Q5/Q6 filing bar.
- p2:161 error-path absence of vault_exists confirmed not-misleading: no `article_path_rel` to trust, `status: "error"` unambiguous, and the `_note`'s "(when set)" wording anticipates the absence exactly.
- Compiler outcome shape re-verified by synthesis: `compile_chat_synthesis` (pal/compiler.py:300) carries `article_path_rel` in its success/existing-article return dicts and no `vault_exists` anywhere — the tool's :174 is the sole source of the field.
- p5 rejects (8 rows) — all `quote_mismatch` artifacts from multi-line descriptions, per the checklist's "quote-gate artifact" note. Each claim verified: :32 TRUE (note body passed verbatim to `serialize_frontmatter`, no LLM); :35×3 TRUE (wait → approved-gate → write → compile order holds); :36×2 TRUE (`is_file` check is real; "directly under raw/notes/" is an intended-use description — the enforced boundary is the sound superset `startswith("raw/notes/")` + no-".." + resolved-boundary, url_fix:16 ruling class); :37 TRUE (all escape routes return structured errors; symlinks escaping the vault are caught by `resolve()`); :169 TRUE (vault_exists = actual `exists()` when `article_path_rel` present; absent on error paths per the _note).
- Subagent's write tool was denied; report persisted by synthesis (verbatim from the subagent return, which was complete).
