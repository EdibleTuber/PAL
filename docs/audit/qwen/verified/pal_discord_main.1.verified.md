# Verified: pal_discord_main p4 (task 1/1)

## Ruled out

- Lead p4:15 (env_read PAL_DISCORD_TOKEN at discord_main.py:15): Standard required-secret read for a Discord bot entry point. Missing value is guarded at lines 16–18 with a clear operator-facing error message and sys.exit(1). No finding.
- Lead p4:20 (env_read PAL_DISCORD_ALLOWED_USERS at discord_main.py:20): Env read with default "". Empty set triggers a loud warning at line 23 and the bot continues running with a zero-member allowlist (fail-closed: bot responds to nobody). This is intentional design, not a defect. No finding.

## Notes

- Both leads are benign env reads whose failure cases are handled: for line 15, the missing-token case is handled with sys.exit(1); for line 20, the empty-allowlist case is handled with a warning and fail-closed behavior.
- The lead file (p4_external_effects__pal_discord_main.jsonl) contains only these 2 lines; no other external effects exist in this 37-line file.
- The p5 pass had zero non-none leads for this source, which is consistent with the p4 assessment.
- load_config() (line 25) and PalDiscordBot (lines 27–33) are already adjudicated in prior tasks (pal_config: 0 findings; discord_adapter: Q2 filed at discord_adapter.py:175 for unguarded reply send, unrelated to this entry point).

## Synthesis adjudication (2026-11-10)

- 0 findings; both p4 leads ruled out, consistent with synthesis's full read of the 37-line file.
- p4:15 verified: `token = os.environ.get("PAL_DISCORD_TOKEN")` at :15 is a read; :16–18 guard the missing case with an operator-facing error + `sys.exit(1)`. Standard required-secret read.
- p4:20 verified: `os.environ.get("PAL_DISCORD_ALLOWED_USERS", "")` at :20 with default; :21 parses to a set; :22–23 warn loudly when empty and startup continues — the allowlist is FAIL-CLOSED (empty set → bot responds to nobody), the safe direction.
- Subagent's line cites (:16–18 guard, :23 warning) match synthesis's read (:16 if / :17 print / :18 sys.exit; :22 if / :23 print).
- Not leads (not filed): the logging setup, the function-local PalDiscordBot import (import-line mislabel class already ruled out in pal_backfill_main), and `bot.run(token)` (the run loop is the already-adjudicated discord_adapter.py territory — Q2).
- Subagent's write tool was denied; report persisted by synthesis (verbatim from the subagent return, which was complete).
