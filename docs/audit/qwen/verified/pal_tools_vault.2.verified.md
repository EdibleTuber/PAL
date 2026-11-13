# Verified: pal_tools_vault p4+p5 (task 2/2)

## F1 [W][Medium] Docstring line 8 contradicted: MoveFile.dst not checked with _resolve_safe

The module docstring at line 8 states "All path operations use _resolve_safe to prevent vault escapes." However, MoveFile.run() validates `src` via `_resolve_safe` (line 297: `resolved_src = _resolve_safe(vault, src)`) but does NOT validate `dst`. At line 312, `absolute_dst = str((ctx.agent.config.vault_path / dst).resolve())` directly resolves `dst` without the `_resolve_safe` safety check. The `reorganizer.move_single(src, dst)` call at line 304 also lacks vault-escape validation for `dst` — it only checks top-level system prefixes (line 40 of pal/reorg.py: `_is_top_level_system(dst)`), which does not catch path traversal like `../../foo.md`. A crafted dst could escape the vault via `dst_path.parent.mkdir(parents=True)` followed by `src_path.rename(dst_path)` (pal/reorg.py:51–52).

Leads: p5:1 (line 8, verdict "contradicted" is correct; evidence_line 297 is misleading — line 297 does use _resolve_safe; the real contradiction is at line 312 where dst is not checked)

## Ruled out

p4:40 — guarded by try/except in _maybe_reindex (lines 39–43), returns None on failure
p4:48,49,56,111,112,119,207,208,215,296,297,298,364,365,372,479,480,487 — local Path operations with zero I/O (resolve, is_relative_to, exists, parts)
p4:132,502 — vault-internal file reads (wiki.read_article / resolved.read_text), failure returns error to user
p4:135,243,539,545 — vault-internal file writes, path validated by wiki._resolve_safe or direct Path; 545 is rollback on git failure
p4:136,244,310,391,542 — wiki.git_commit guarded: bails without .git (wiki.py:179–181), no-op when clean (wiki.py:196–197)
p4:138,246,313,399,552 — _maybe_reindex is best-effort (try/except wrapper at vault.py:39–43)
p4:304 — vault-internal file move, wrapped in try/except (lines 303–306), returns error on failure
p4:386 — wrapped in try/except (lines 385–388), returns error on failure
p4:542 — wrapped in try/except (lines 541–550), restores original on failure

p5:2,3,4,5,6,7,8,9,10,11,12,13 — all consistent with code

## Notes

- p5:1 verdict "contradicted" is correct but evidence_line 297 is misleading — line 297 does use _resolve_safe. The actual contradiction is at line 312 where dst is resolved via direct `vault_path / dst` without _resolve_safe.
- p5 verdicts observed: consistent=12, contradicted=1, cannot_tell=0
- All p4 leads are either local zero-I/O Path operations, vault-internal operations, or already-guarded operations
- The p4 over-labeling of `kind` noted in the dispatch applies: many leads labeled "fs_query" are local Path operations with zero I/O; leads labeled "http" for _maybe_reindex calls are best-effort wrappers
- No findings from the p4 leads — all external effects are either vault-internal, guarded, or best-effort
