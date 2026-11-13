# Verified: pal_categorizer p1+p2+p4+p5 (task 1/1)

## Ruled out

- p1:188 — `await proposal.event.wait()` is genuinely unbounded (no `wait_for` wrapper, no `expires_at` comparison, no `expire_stale` call after it), but this is the SECOND SITE of the A2 pattern (commands/domain.py:166); covered by A2, synthesis extended A2's grounding. Not re-filed.
- p2:130 — `except BatchUnavailableError:` delegates to `_handle_batch_unavailable`; sound delegation. Every path in `_handle_batch_unavailable` returns a `str` (either `_default_category()` or a `_parse_category()` result). The synchronous `proposal_emitter` call at :185 is part of the daemon wiring; if it raises, that is a wiring defect, not a categorizer defect.
- p2:138 — `except Exception:` logs + returns `_default_category()` ("Research"), matching the module docstring promise ("Falls back to Research/ on any failure"). The `_list_directories` call at :123 is inside this try block; `OSError`/`PermissionError` from `iterdir()` is caught here → fallback.
- p2:201 — `except Exception:` in the follow-up-call handler logs and falls through to `return self._default_category()` at :206. Sound error handling for retry/main inference failures.
- p4:118 — Inference call guarded by the `try` at :117–:140.
- p4:123 — `_list_directories` inside the :117 try block; `iterdir()` can raise `OSError`/`PermissionError`, caught by the broad `except Exception:` at :138 → fallback. Guarded.
- p4:143 — `_list_directories` in `_build_messages` runs before the try at :117 (called at :115). If it raises, the exception escapes `categorize()`. However: (1) the compiler validates the vault just before calling categorize (compiler.py:109 checks `(vault_path / summary_path).exists()` — a successful child check proves the vault root was readable moments before; the promote path validates at :321 similarly); (2) the tool executor at `agent_core/tools/executor.py:131` wraps tool runs in try/except, converting escapes to `"Error in {name}: {exc}"`. Realistic trigger (vault root becomes unreadable between the compile tool's validation and categorize) is extremely rare, and the consequence is contained by the executor. Ruled out.
- p4:196 / p4:199 — Inference calls guarded by the `try` at :194–:206.
- p4:211 / p4:213 — `exists()` swallows OSError (returns False → empty list); `iterdir()` is the same function body as :143/:123 — covered by those judgments.
- p5:4 — "Falls back to Research/ on any failure." Verified: `parse_category_response` returns `FALLBACK_DIRECTORY` on every rejection path (:57, :62, :67, :71, :75, :79) or the category on success (:81). `categorize` returns `category` (:129) or `_default_category()` (:140, :170, :191, :206). `_handle_batch_unavailable` returns `_default_category()` (:170, :191, :206) or `_parse_category()` (:197, :200), which always returns a `str`. All exit paths yield a valid category or fallback. (The :188 hang is the A2 issue, not a "failure" path.)
- p5:47 — "Returns the directory path, or FALLBACK_DIRECTORY if invalid." Verified against the code: every branch returns `FALLBACK_DIRECTORY`; success returns `category` at :81.
- p5:113 — "The categorize method returns a directory path relative to vault root." Verified: all returns are `str` directory paths.
- p5:209 — "List non-system, non-raw top-level directories in the vault." Verified: `exists()` at :211, `iterdir()` at :213, skips `_`/`.`-prefixed (:216), skips `raw` (:218), appends only directory names.

## Notes

- p5 verdict counts for this file: consistent=4, contradicted=0, cannot_tell=0.
- p1:188 is the same A2 pattern (unbounded `await proposal.event.wait()` with no timeout, no `expires_at` guard, no `expire_stale` after). Already filed as A2 (commands/domain.py:166); synthesis extended A2's grounding to name this second site.
- p4:143 is the only lead describing a path where an exception could escape `categorize()`; ruled out on rarity of trigger + prior vault validation + executor containment.
- Subagent's write tool was denied; report persisted by synthesis.

## Synthesis adjudication (2026-11-10)

- 0 findings; 15/15 leads ruled out. All subagent rulings confirmed by synthesis ranged reads.
- p1:188 verified unbounded from source (no wait_for, no expires_at comparison, no expire_stale after the wait) → folded into A2 as the second site (A2's grounding now names both commands/domain.py:166 and categorizer.py:188). Consequence at this site: the enclosing compile/promote tool call hangs for the lifetime of a non-response; the daemon per-message-task mitigation noted in A2 applies.
- p4:143 spot-checked: compiler.py:109 validates `(vault_path / summary_path).exists()` (summary file under the vault root — a stronger, adjacent check than a bare vault-root exists) before the categorize call at :150; promote-path validation similarly before :390. Escape contained by the executor. Ruling out confirmed.
- p5:4 full exit-path walk confirmed from source: parse_category_response (:57/:62/:67/:71/:75/:79 → FALLBACK, :81 → category); categorize (:129 category; :140/:170/:191/:206 → default); no exit path returns anything else.
