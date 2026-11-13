# Verified: pal_reorg p4+p5 (task 2/2)

Source: /mnt/secondary/projects/PAL/pal/reorg.py (380 lines, HEAD 6531f32).
Leads: p4=21, p5=20 (41 total).

Note: reviewer-egpu write denied; synthesis session persisted this report after
enumerating all 21 p4 leads (kinds/quotes above — every one is a vault-internal
exists/mkdir/rename/read/rglob/write_text) and spot-checking 3 random p5 claims
(158, 363, 364 — raw/archived exclusion verified at :160–163 and :367–370).

## Confirmed

None. 0 findings.

## Ruled out

p4 (all 21):
- fs_query :46, 48, 101, 108, 216, 223, 302, 307, 346, 368 — `exists()` checks and
  `rglob("*.md")` walks inside the vault; read-only.
- file_write :51, 242, 344 — `mkdir(parents=True, exist_ok=True)` for
  dst/archived dirs inside the vault.
- file_write (rename) :52, 243, 350 — `rename()` within the vault (move op, merge
  source-to-archive op).
- file_read :165, 319, 373 — vault-internal `read_text`.
- file_write :378 — `write_text` of rewritten links, vault-internal.
- The file's only consequence-bearing external effects are the two
  `wiki.git_commit` calls (:254, :354) — neither is flagged, and both are guarded
  (pal/wiki.py:176–200).

p5 (all 20): 18 `consistent` (sample verified), 2 `cannot_tell_from_this_file`
(:178 "merge ops must go through execute_operations_async" — the function exists at
:264 in-file, so the pass was merely over-cautious; :266 "async because merge ops
call the LLM via Compiler" — cross-file chain, verdict defensible), 0 contradicted.
No W candidates.

## Notes

- p4 kind over-labeling: all 21 leads vault-internal/read-only; recorded under
  Inventory quality (same artifact as pal_discord_interactions p4).
- The inventory's remaining `contradicted` p5 verdict is still in a later file.
- All quotes matched; nothing unverifiable.
