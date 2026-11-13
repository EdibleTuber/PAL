# Verified: pal_wiki p2+p4+p5 (task 1/1)

## Ruled out

p2-A:92 -- ValueError→FileNotFoundError re-raise is proper error handling per docstring intent.
p2-A:272 -- OSError swallowed with continue in a filesystem scan; best-effort skip is reasonable, no work lost.
p4:31,34,77,144 -- Vault-internal file writes; the user-path-driven one (:77 write_article) is guarded by `_resolve_safe` (:57); :31/:34/:144 are the manager's own vault_path (startup/index), no user input. Failure (check=True / plain IO error) propagates visibly to caller.
p4:33,64,94,149,179 -- `.exists()` queries, not external effects.
p4:65,96,109,238,271 -- `read_text()` calls; file reads, not mutations.
p4:104,226,232,264 -- `rglob()` calls; filesystem queries, not mutations.
p4:150,157,163,183,190,198 -- `subprocess.run` git ops in `git_init`/`git_commit`; all guarded by `.git` existence check on lines 149/179 (git_commit additionally no-ops when clean via `git status --porcelain` at :190–194). All `check=True` — failures raise visibly, never silent.
p4:208 -- `git_rm` subprocess has no `.git` guard, but the sole real caller (`pal/tools/vault.py:386–388`) wraps it in `try/except Exception`, returning structured error JSON to the LLM.
p5:22,30,50,88,99,101,117,148,172,207,217(x2),219,220,222,258,260(x2),261 -- all 19 consistent claims verified against code; docstrings/comments match implementation.
p5:207 (cannot_tell) -- "git_rm stages the deletion atomically" resolved TRUE on git semantics: `git rm` updates index + working tree in a single porcelain command; no PAL-side multi-step to interleave.

## Notes

- p2 Part B / C: zero `asyncio.create_task` calls in `pal/wiki.py`; all methods synchronous. No fire-and-forget tasks.
- p5 verdict counts: consistent=19, contradicted=0, cannot_tell=1 (line 207, resolved TRUE — see Ruled out).
- p4 over-labeling: 14 of 25 leads are reads/exists/rglob queries; only 11 are mutations (4 file writes, 7 git subprocesses).
- Established context respected: `git_commit` guards (.git check + clean no-op at :179–197) and `git_init` guard (:149) confirmed in source.

## Synthesis adjudication (2026-11-10)

- Report persisted by synthesis (subagent's write tool was denied; the returned report was truncated at the end of Notes — final Notes bullet completed above matches what synthesis verified).
- Zero findings re-adjudicated: CONFIRMED. Synthesis ranged reads (pal/wiki.py + lead files):
  - p2-A:92 (:85–96) — `_resolve_safe` ValueError re-raised as `FileNotFoundError` with `from exc`; matches the read_article docstring contract. As reported.
  - p2-A:272 (:274–276) — `parse_frontmatter(path.read_text())` under `except OSError: continue` inside `find_articles_missing_source`; a listing helper, skip = unreadable file absent from the report, scan continues. As reported.
  - p4:77 — `write_article` resolves via `self._resolve_safe(path)` at :57 before mkdir+write; vault-internal. p4:31/34/144 — `init_vault`/`rebuild_index` write `_index.md` from `self.vault_path` directly (no user input, no `_resolve_safe` needed); subagent's group label "guarded by `_resolve_safe`" is imprecise for these three — ruling unchanged.
  - p4:150/157/163 — `git_init`: guard at :149, `git init` / `add .` / `commit --allow-empty`, all `check=True`. p4:183/190/198 — `git_commit`: guard at :179, `add .`, clean-no-op at :190–194, commit `check=True`. As reported and as in established context.
  - p4:208 — `git_rm` body (:202–210): `["git", "rm", "--", path]` `check=True`, no `.git` guard; caller vault.py:385–388 `try: wiki.git_rm(path) except Exception → error JSON` (verified in the pal_tools_vault pass). Ruling holds.
  - p5 consistent spot checks: :22→:25–27 (escape check raises ValueError — TRUE), :101→:110–113 (`{"path": str(rel), "title": ...}` — TRUE), :148→:149 (git_init guard — TRUE), :207→:209 (`git rm -- path` — TRUE), :258→:279–283 (`all_empty` = every entry has empty url AND empty source_file — TRUE, matches claim verbatim).
  - p5:207 cannot_tell — resolved TRUE (git semantics, as above).
- Verdict: 47/47 leads ruled out, 0 findings stands.
