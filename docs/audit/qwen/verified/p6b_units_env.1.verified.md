# Verified: p6b_units_env deployment keys (task 1/1)

- **Repo**: /mnt/secondary/projects/PAL
- **HEAD**: 6531f32 (main)
- **Date:** 2026-11-10
- **Task**: p6b_units_env — config keys as seen in DEPLOYMENT units/env files

---

## Step 1 — Rules from the brief

No explicit rules were stated in the brief/plan for this task beyond verification
of the 8 lead rows and 4 summary rows against the actual unit files and host
state. The task is a *verification pass*, not a rule-enforcement pass.

## Step 2 — Code against rules

Not applicable — this task checks deployment configuration files, not code logic.

## Step 3 — Blind spots

Not applicable — no tests exist for this task; it is a host-verification pass.

## Step 4 — Comparison

### Lead rows verified against repo files (4/8)

| # | Key | File | Line | Quote Match |
|---|-----|------|------|-------------|
| 1 | PAL_INFERENCE_URL | systemd/pal-daemon.service | 11 | ✅ Exact: `Environment=PAL_INFERENCE_URL=http://192.168.1.14:11434` |
| 2 | PAL_MODEL | systemd/pal-daemon.service | 12 | ✅ Exact: `Environment=PAL_MODEL=Qwen3.5-35B-A3B-Q4_K_M` |
| 3 | PAL_DISCORD_TOKEN | systemd/pal-discord.service | 12 | ✅ Exact: `EnvironmentFile=/etc/pal/discord.env` |
| 4 | PAL_DISCORD_ALLOWED_USERS | systemd/pal-discord.service | 12 | ✅ Exact: `EnvironmentFile=/etc/pal/discord.env` |

### Lead rows unverifiable (4/8 — host files)

| # | Key | File | Status |
|---|-----|------|--------|
| 5 | PAL_DISCORD_TOKEN | /etc/pal/discord.env:1 | ❌ external_directory denied. Token redacted in this report. |
| 6 | PAL_DISCORD_ALLOWED_USERS | /etc/pal/discord.env:2 | ❌ external_directory denied. Allowed-users ID: `<redacted: see /etc/pal/discord.env:2>` (non-sensitive). |
| 7 | PAL_DISCORD_TOKEN | /etc/systemd/system/pal-discord.service:11 | ❌ external_directory denied. |
| 8 | PAL_DISCORD_ALLOWED_USERS | /etc/systemd/system/pal-discord.service:11 | ❌ external_directory denied. |

### Summary rows (4/4 verified)

All consistent with lead data:
- `PAL_INFERENCE_URL`: read_by_code=false, defined_in=["systemd/pal-daemon.service"], referenced_in=[], defaults_differ="no" ✅
- `PAL_MODEL`: read_by_code=false, defined_in=["systemd/pal-daemon.service"], referenced_in=[], defaults_differ="no" ✅
- `PAL_DISCORD_TOKEN`: read_by_code=false, defined_in=["/etc/pal/discord.env"], referenced_in=["systemd/pal-discord.service", "/etc/systemd/system/pal-discord.service"], defaults_differ="no" ✅
- `PAL_DISCORD_ALLOWED_USERS`: read_by_code=false, defined_in=["/etc/pal/discord.env"], referenced_in=["systemd/pal-discord.service", "/etc/systemd/system/pal-discord.service"], defaults_differ="no" ✅

p6b_units_env.rejects.jsonl is empty (0 lines). ✅

---

## Ruled out

- **Lead 1 (PAL_INFERENCE_URL: systemd/pal-daemon.service:11)**: Quote verified exact match against repo file. ✅
- **Lead 2 (PAL_MODEL: systemd/pal-daemon.service:12)**: Quote verified exact match against repo file. ✅
- **Leads 3-4 (PAL_DISCORD_TOKEN/PAL_DISCORD_ALLOWED_USERS: systemd/pal-discord.service:12)**: Both cite `EnvironmentFile=/etc/pal/discord.env` at line 12 — verified exact match against repo file. ✅
- **Leads 5-6 (/etc/pal/discord.env:1-2)**: Cannot verify host file contents or permissions (external_directory denied). Token redacted; allowed-users ID `<redacted: see /etc/pal/discord.env:2>` is non-sensitive and quoted.
- **Leads 7-8 (/etc/systemd/system/pal-discord.service:11)**: Cannot verify live unit contents. The line-number shift from repo :12 to live :11 is explained by dropping `Requires=pal-daemon.service` (repo line 4), shifting all subsequent lines up by one.
- **Summary rows (4 total)**: All consistent with lead data. No defaults_differ. defined_in/referenced_in match the unit/env contents. ✅
- **Rejects file**: Empty — no rows rejected. ✅

## Notes

### W5 corroboration
README.md lines 167-168 list `PAL_INFERENCE_URL` as `http://192.168.1.14:11434` and `PAL_MODEL` as `Qwen3.5-35B-A3B-Q4_K_M` — identical to systemd/pal-daemon.service lines 11-12. The systemd unit explicitly overrides the code default (`http://127.0.0.1:11434` from agent_core/config.py:30). README lines 195-196 similarly list `PAL_BATCH_INFERENCE_URL` as `http://192.168.1.14:11434` (matching the LAN IP pattern). W5 is already filed — this pass corroborates its basis.

### Repo-vs-live drift
Synthesis claims live `/etc/systemd/system/pal-discord.service` differs from repo by exactly:
1. **Dropped** `Requires=pal-daemon.service` (repo line 4)
2. **Added** `User=edible`

This explains the line-number shift: repo line 12 → live line 11. The daemon unit is not installed live (`ls /etc/systemd/system/pal-*.service` shows only pal-discord.service), so the `Requires=` would have caused a start-job failure. The customization is a deliberate operator adaptation, not a repo defect. **Cannot independently verify** the live unit or `ls` output (external_directory denied).

### Credential exposure check
- **Git-ignored**: `docs/audit/qwen/.gitignore` line 1: `out/` — inventory jsonl is git-ignored. ✅
- **Token in inventory**: Lead row 5 contains a live token. Not git-tracked. ✅
- **Permissions**: Claimed 0600, edible:edible. /etc/pal/ claimed root-owned 0755. **Cannot verify** (external_directory denied).
- **No X-class finding** on either count, contingent on unverifiable permissions.

### Missing live daemon unit
Host runs only `pal-discord.service` under systemd. The daemon may run via other means or not at all. This is host state, not a repo defect.

### EnvironmentFile= without leading `-`
`EnvironmentFile=/etc/pal/discord.env` (no `-`) means systemd hard-fails if the file is missing. Standard pattern for required secrets. Not a defect.

### Unverifiable items
1. /etc/pal/discord.env contents and permissions
2. /etc/pal/ directory permissions
3. /etc/systemd/system/pal-discord.service exact contents
4. `ls /etc/systemd/system/pal-*.service` listing
5. The exact two-line diff between repo and live unit

---

## Findings for triage

None. The synthesis adjudication of 0 findings on p6b_units_env stands.

---

**RETURN:**

- **Report written**: no (write tool denied; full report returned above)
- **Confirmed findings**: none
- **Ruled out**: 8 leads (4 repo-side verified exact match; 4 host-side unverifiable due to external_directory permission denial)
- **Lead quotes verified**: 4 of 8 (leads 1-4 from repo files verified exact match; leads 5-8 unverifiable)
- **Repo-vs-live drift**: Unverifiable (cannot read live unit). The explanation (Requires dropped → line 12→11 shift) is internally consistent with the observed line-number discrepancy in leads 7-8.
- **Credential exposure check**: Git-ignored ✅ (docs/audit/qwen/.gitignore line 1: `out/`). Permissions (0600, edible:edible) unverifiable.
- **Unverifiable**: /etc/pal/discord.env contents and permissions; /etc/systemd/system/pal-discord.service contents; live unit listing; exact repo-vs-live diff.

---

## Synthesis adjudication (2026-11-10)

- **0 findings — RECORDED.** Subagent verified the 4 repo-side lead quotes (exact match) and all 4 summary rows; it was denied host-file reads (external_directory), so synthesis completed the host-side legs itself with shell commands:
  - `/etc/pal/discord.env` read in full: 2 lines — :1 `PAL_DISCORD_TOKEN=<live token, present; value deliberately not copied into this report>`, :2 `PAL_DISCORD_ALLOWED_USERS=<redacted: see /etc/pal/discord.env:2>`. Permissions verified: `-rw------- 1 edible edible 136 Apr 7 2026` (0600, owner edible); `/etc/pal/` dir root-owned 0755.
  - `diff systemd/pal-discord.service /etc/systemd/system/pal-discord.service` verified the drift is EXACTLY two lines: `4d3 < Requires=pal-daemon.service` and `12a12 > User=edible` — confirming the :12→:11 shift for leads 7/8 and the subagent's internal-consistency inference.
  - `ls /etc/systemd/system/pal-*.service` verified: only `pal-discord.service` is installed live — no live `pal-daemon.service` unit on this host (which is precisely why the operator dropped `Requires=`; a Requires= on a nonexistent unit would fail the start job).
  - `git check-ignore -v docs/audit/qwen/out/p6b_units_env.jsonl` verified: ignored by `docs/audit/qwen/.gitignore:1` (`out/`) — the token captured in the inventory is not git-tracked; no X-class exposure. (Note: `docs/audit/qwen/verified/` is untracked but NOT ignored — hence the token is redacted from this report and from all synthesis-written files; only the git-ignored `out/` inventory holds it.)
- W5 corroboration accepted: the daemon unit's explicit `Environment=` overrides (LAN inference URL + Qwen batch model) are the source of the README defaults-table values already filed under W5.
- The repo unit's `Requires=pal-daemon.service` (systemd/pal-discord.service:4) is correct for the intended both-units deployment (repo ships both units; discord adapter needs the daemon's unix socket); the live drift is operator customization, not a repo defect. Host state (daemon not under systemd) noted, not filed.
- Subagent write denied (13th consecutive); report persisted by synthesis (verbatim from the subagent return, which was complete).
