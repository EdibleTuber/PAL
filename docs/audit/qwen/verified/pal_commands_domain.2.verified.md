# Verified: pal_commands_domain p4+p5 (task 2/2)

Source: /mnt/secondary/projects/PAL/pal/commands/domain.py (626 lines, HEAD 6531f32).
Leads: p4=23, p5=51 (74 total).

Note: reviewer-egpu write denied again; synthesis session persisted this report from
the subagent's returned content after spot-checking a sample (6 random p5 claims +
p4:77/96/288 raw lines) against source — all match.

## Confirmed

None. 0 findings.

## Ruled out

p4 (all 23):
- p4:77, p4:96 — local `Path.exists()` / `iterdir()` checks; not external effects.
- p4:104, p4:208, p4:243, p4:288 — `mkdir()` / `write_text()` on paths inside
  `ctx.agent.config.vault_path` (vault-internal writes; :288 is the chapter
  `article_full.write_text`).
- p4:122, p4:196, p4:213, p4:261 — `run_in_executor` PDF/converter ops; already
  adjudicated in task 1/2 (complete-or-raise, daemon-level catch upstream).
- p4:294, p4:295, p4:311, p4:404, p4:405 — `wiki.git_init()/git_commit()` subprocess
  git inside the vault; `git_init` guarded by `.git` existence check (pal/wiki.py:149),
  `git_commit` no-op if no changes (pal/wiki.py:179–197).
- p4:310 — `archive_raw_files()` moves files within `vault_path/raw/archived`
  (pal/archive.py:17–31); vault-internal.
- p4:452, p4:456 — `profile.write()/read()` on the vault-internal profile file
  (agent_core ProfileManager).
- p4:487, p4:500, p4:508 — `wisdom.add/remove/list` on vault-internal wisdom files
  (agent_core WisdomManager).
- p4:554 — scratchpad append to the channel's scratchpad file in the vault.
- p4:586 — `client.get(...)` to the inference `/v1/models` IS a real external call but
  is guarded by try/except (domain.py:584–600) → warning + ErrorMessage.

p5 (all 51): every kept lead has `verdict: consistent`, contradicted=0,
cannot_tell=0. Sample verified by synthesis (random seed 7, 6 of 51): line 239
(1-based section_range — code at :239 does `start_page + 1`), 112 (pymupdf4llm comment),
293 (commit+reindex after import — :294–295, :301), 522 (scratch docstring), 52
(proposal emitter comment), 69 (raw/ guard). All true. No cross-file helper chains
needed.

## Notes

- The inventory's ONE remaining "contradicted" p5 verdict (of 2 total, the other being
  the ruled-out discord_interactions italic-line claim) is therefore in a later, not
  yet processed, file.
- p4 kind labels were mixed (fs_query/file_read/file_write/subprocess/http) and all
  were judged from the quote; none is an unguarded consequence-bearing external effect.
