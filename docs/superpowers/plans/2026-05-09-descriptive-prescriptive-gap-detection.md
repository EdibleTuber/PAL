# Descriptive Prescriptive Gap Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land one new wisdom rule in the vault at `_wisdom/pal/descriptive-prescriptive-gap-detection.md` that teaches PAL to flag descriptive responses to action-oriented questions.

**Architecture:** No code changes. The rule is a markdown file that injects into PAL's system prompt via the existing wisdom-injection mechanism. Lives in the vault git repo (separate from the PAL code repo). Validation is qualitative via three smoke-test prompts.

**Tech Stack:** Markdown with YAML frontmatter. Vault is a git repo with auto-commit on PAL writes; manual commit for direct-write files. PAL reads wisdom files into the system prompt per session.

---

## Context

The design spec is at `docs/superpowers/specs/2026-05-09-descriptive-prescriptive-gap-detection-design.md`. Read it first if context is missing.

The vault is at `~/pal-vault-prod` on the dev machine (per the audit) and on the server at the path PAL's daemon reads (typically configured via env or PAL's config; the user knows the server-side path). The wisdom file must land on the server because that is where PAL actually runs.

The user prefers `scp` for transfers (per the prior empty-URL backfill plan) and explicit-path git operations (no `git add -A`).

## File Structure

**Created:** one file in the vault repo (NOT the PAL code repo):
- `_wisdom/pal/descriptive-prescriptive-gap-detection.md`

**No PAL repo changes.** No tests. No code.

---

## Task 1: Write the wisdom file locally

**Files:**
- Create (locally on dev machine for transfer): `~/pal-vault-prod/_wisdom/pal/descriptive-prescriptive-gap-detection.md`

The dev-machine vault is the audit's snapshot. Writing here gives a reviewable artifact and a predictable scp source. The user can also choose to write directly on the server and skip the dev-side step; this plan assumes the dev-then-transfer path because it leaves a reviewable local copy.

- [ ] **Step 1: Confirm vault path exists and the wisdom directory is in place**

Run:
```bash
ls -la ~/pal-vault-prod/_wisdom/pal/ 2>/dev/null | head -5
```

Expected: directory exists with other rule files (e.g., `granularity-over-consolidation.md`, `grounded-ai-protocol.md`). If the directory is missing, run `mkdir -p ~/pal-vault-prod/_wisdom/pal/`.

- [ ] **Step 2: Write the wisdom file**

Create `~/pal-vault-prod/_wisdom/pal/descriptive-prescriptive-gap-detection.md` with this exact content:

```markdown
---
title: Descriptive Prescriptive Gap Detection
created: '2026-05-09T00:00:00+00:00'
---
When the user asks an action-oriented question, evaluate your own answer
before sending. Action signals are explicit phrasings only: "how do I", "how
to", "how can I", "walk me through", "step by step", "what's the command for",
"best way to", "show me how", "tell me how to". Do not infer action intent
from noun-phrase questions like "Frida JNI hooking?" or "GDB remote attach?";
treat those as ambiguous and stay silent.

If the trigger fires and your answer is mostly descriptive (definitions,
concepts, what-is) rather than prescriptive (specific commands, executable
steps, copy-pasteable examples), append a compact end-of-response note:

> (Note: this is descriptive coverage. The vault doesn't have prescriptive
> examples for [name the specific action the user asked about]. Research that?)

Name the specific gap, not a generic one. Fire at most once per response. Do
not fire on follow-up turns where the user has already chosen to proceed with
the descriptive material.
```

- [ ] **Step 3: Verify content matches the spec**

Run:
```bash
diff <(awk '/^```markdown$/,/^```$/' /home/edible/Projects/PAL/docs/superpowers/specs/2026-05-09-descriptive-prescriptive-gap-detection-design.md | head -n -1 | tail -n +2) ~/pal-vault-prod/_wisdom/pal/descriptive-prescriptive-gap-detection.md
```

Expected: empty diff (the file matches the spec's wisdom-rule body verbatim). If the diff shows differences, the file content drifted from the spec; rewrite it.

- [ ] **Step 4: Commit in the dev-side vault repo**

Run:
```bash
cd ~/pal-vault-prod && git add _wisdom/pal/descriptive-prescriptive-gap-detection.md && git commit -m "wisdom: add descriptive-prescriptive gap detection rule"
```

Expected: one new commit in the vault repo with the new file.

---

## Task 2: Transfer the wisdom file to the server

**Files (server-side):**
- Create: `<server-vault-path>/_wisdom/pal/descriptive-prescriptive-gap-detection.md`

Substitute `<server-vault-path>` with whatever the actual vault path is on agenthost. The user knows this; common locations are `/mnt/secondary/PAL-vault-prod` or wherever their PAL config points.

- [ ] **Step 1: scp the wisdom file**

Run from `~/pal-vault-prod`:
```bash
scp _wisdom/pal/descriptive-prescriptive-gap-detection.md \
    agenthost:<server-vault-path>/_wisdom/pal/descriptive-prescriptive-gap-detection.md
```

Substitute the actual server-side path. Expected: file transfers (scp output shows the file copied).

If `<server-vault-path>/_wisdom/pal/` does not exist, create it first:
```bash
ssh agenthost 'mkdir -p <server-vault-path>/_wisdom/pal/'
```

- [ ] **Step 2: Commit in the server-side vault repo**

PAL's vault is a git repo with auto-commit on PAL's own writes, but a direct file drop (via scp) bypasses PAL and so bypasses auto-commit. Commit explicitly:

```bash
ssh agenthost 'cd <server-vault-path> && git add _wisdom/pal/descriptive-prescriptive-gap-detection.md && git commit -m "wisdom: add descriptive-prescriptive gap detection rule"'
```

Expected: one new commit in the server-side vault repo.

- [ ] **Step 3: Verify the file is in place server-side**

Run:
```bash
ssh agenthost 'cat <server-vault-path>/_wisdom/pal/descriptive-prescriptive-gap-detection.md | head -5'
```

Expected: prints the frontmatter (title, created date) and the first line of the rule body.

---

## Task 3: Reload PAL so it picks up the new wisdom rule

**No files modified.** Operational step.

PAL injects wisdom into the system prompt at session start (per the architecture spec). Whether existing in-flight sessions also pick up the rule depends on whether the daemon caches wisdom or reads it per-turn. To be safe, restart the daemon. New sessions started after restart will definitely have the rule.

- [ ] **Step 1: Restart the PAL daemon**

PAL runs via systemd on the server. Restart it:

```bash
ssh agenthost 'sudo systemctl restart pal-daemon.service'
```

(Substitute the actual unit name if it differs from `pal-daemon.service`. The user knows the unit name from prior PAL deployment work.)

Expected: command returns successfully, no error output. Daemon comes back online within a few seconds.

- [ ] **Step 2: Verify the daemon is up**

Run:
```bash
ssh agenthost 'sudo systemctl status pal-daemon.service --no-pager | head -10'
```

Expected: `active (running)` status. If status is `failed` or `inactive`, check logs (`sudo journalctl -u pal-daemon.service -n 50 --no-pager`) and resolve before proceeding.

---

## Task 4: Smoke-test the rule with three prompts

**No files modified.** Operational step. Validates the rule works as designed.

Open a new Discord channel (so the rule injects into a fresh session, not one with cached state from before). Run three prompts in order. Watch PAL's behavior. The pass criterion is qualitative.

- [ ] **Step 1: Positive trigger smoke test**

Send PAL: `How do I run Frida against a packed iOS app?`

(Substitute a different action-oriented question if you don't have packed-iOS-Frida coverage; pick a topic where the vault has descriptive material but no prescriptive examples. The 5a baseline data identified this kind of gap on queries 1 and 7; either of those topics would work.)

Expected: PAL answers with whatever descriptive content the vault has, then appends an end-of-response note in the form:

> (Note: this is descriptive coverage. The vault doesn't have prescriptive examples for `<specific gap>`. Research that?)

If PAL answers without flagging, the trigger underfired. Recheck the wisdom rule was loaded (look for it in the daemon's prompt-construction log if available, or restart the daemon and try again).

- [ ] **Step 2: Negative trigger smoke test**

Send PAL: `What is Frida?`

Expected: PAL answers descriptively (because the question is descriptive) and does NOT append the flag. The question is "what is X", which the rule explicitly lists as a negative case.

If PAL flags here, the trigger overfires and the rule needs tightening (probably the action-signal list is being matched too loosely).

- [ ] **Step 3: Mixed-case smoke test**

Send PAL a question on a topic where the vault has prescriptive coverage (e.g., something MCP-bridge-related or GDB-related, both of which the 5a baseline showed had clean prescriptive hits): `How do I set up an MCP bridge for an RE agent?`

Expected: PAL answers with the prescriptive content from the vault (commands, examples, concrete setup steps) and does NOT flag (because the answer is prescriptive, not descriptive, so there is no gap to flag).

If PAL flags here, the self-classification heuristic is too aggressive about calling responses descriptive.

- [ ] **Step 4: Record results**

If all three smoke tests pass, the rule is calibrated and operational. Record the result by appending a one-line entry to `_learning/pal/` if a calibration learning emerges (e.g., "trigger underfired on noun-phrase questions"; intentional per spec but worth tracking) or in a session note. No code change.

If any test fails, return to the spec's iteration plan: edit the wisdom rule (file in the vault), commit, scp, restart daemon, re-test. Iteration cost is low (one file edit + transfer + restart).

---

## Self-review notes

- **Spec coverage:** The single requirement of the spec (write the wisdom file with the body specified) is covered by Task 1 step 2. The validation section of the spec (three smoke checks) is covered by Task 4. Rollback (delete the file, restart) is implicit in the iteration plan referenced from Task 4 step 4.
- **Placeholder scan:** The plan uses `<server-vault-path>` as a substitution parameter where the user has the value, not as a TBD. The exact PAL daemon unit name is left for the user to confirm because PAL's deployment shape is user-specific and outside this plan's scope.
- **Type consistency:** N/A. No types involved.
- **No em dashes:** Verified across the document.
