# PAL audit — working ledger

Mirrors the inference-server audit (`/mnt/secondary/inference-server/docs/audit/`).
This file is the handoff state for a fresh session.

## Scope and inputs

- Audited code: this repo, branch `main` @ `e938761` (record the exact HEAD at
  synthesis time; also record `git status` — the two untracked
  `systemd/searxng-restart.{service,timer}` files are part of the p6 surface).
- Deployed tree: `/mnt/secondary/PAL` @ `1be5516` (2 commits ahead: agent_core
  1.13 bump, inference-server phase 6 Task 7 / Q18). Repo-vs-deployed drift is
  an input to the findings, not noise.
- Inventory: Qwen passes p1–p5 over the 33 substantive `pal/**/*.py` files
  (37 .py files, stubs <10 lines skipped) + p6 config surface (pal/config.py,
  agent_core config from the deployed venv, repo systemd units, /etc/pal/discord.env,
  host pal-*/searxng-* units). 166 passes total.
- Inventory model: `Qwen3.6-35B-A3B-UD-IQ4_NL_XL` on the egpu slot (chosen over
  Qwen3.8-27B on main to keep the orchestrating session responsive; probes on
  2026-11-09 showed 6s/34s runtimes and 0 rejected quotes).

## Run log

- 2026-11-09: tooling landed (docs/audit/qwen/, commit a680d7e), run started
  in background at 00:04 (166 passes, est. 40–90 min).
  Log: `/tmp/opencode/pal-audit-run.log` (kept/rejected lines are the
  `name: N kept, M rejected` lines). Output: `docs/audit/qwen/out/`.
- Status: IN PROGRESS (update on completion: kept/rejected totals, any
  `.FAILED` files to re-run with `--passes <p> --files <name>`).

## Synthesis steps (for the fresh session)

1. Confirm run finished: no `.FAILED` files in `docs/audit/qwen/out/`;
   re-run any failed pass; record kept/rejected totals here.
2. Read every lead in the jsonl in source and verify it (this is where
   inventory quality is enforced). Group into the findings.md categories:
   A async/concurrency, O ordering, Q QoL/stability, D config+docs drift,
   S startup/deploy/host, W docs-vs-code, X security, plus I (observed) if
   anything surfaces live.
3. Ordering walks: `pal/agent.py`, `pal/discord_interactions.py`
   (DiscordStreamProcessor), and the batch-fallback proposal flow are the
   candidates for a state-writer/reader walk, as the slot-state walk was in
   the other audit.
4. Tests section: run the suite in the deployed venv
   (`/mnt/secondary/PAL/.venv/bin/python -m pytest`) — note PAL's repo has no
   .venv; coverage with branches; hermeticity (real ports, 127.0.0.1:11434
   probes from tests?).
5. Drift checks: repo `main` vs deployed `1be5516` (diff the two trees);
   repo systemd units vs `/etc/systemd/system` copies (byte-compare);
   `/etc/pal/discord.env` vs what the code expects (PAL_* keys, PAL_MODEL —
   the repo unit says `Qwen3.5-35B-A3B-Q4_K_M`, check what the host has and
   what is actually loaded per `/v1/models`); agent_core 1.4.0 pin (repo) vs
   1.13 (deployed venv) — config-surface implications.
6. Docs-vs-code: README claims, docs/*.md (security.md, architecture-flows.md,
   agent_ecosystem_direction.md), systemd unit comments.
7. Write `docs/audit/findings.md` in the same format as
   `/mnt/secondary/inference-server/docs/audit/findings.md` (Status header,
   verified tables by category, Ruled out, Inventory quality, Not yet covered,
   Tests). Commit.
8. Out of scope here (later sessions): design review + phased fix plans.
