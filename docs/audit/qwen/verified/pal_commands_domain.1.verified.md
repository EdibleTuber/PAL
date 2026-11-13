# Verified: pal_commands_domain p1+p2+p3 (task 1/2)

Source: /mnt/secondary/projects/PAL/pal/commands/domain.py (626 lines, HEAD 6531f32).
Leads: p1=18, p2=20, p3=24 (62 total).

Note: the reviewer-egpu subagent's write was denied; it returned its draft (4 confirmed,
29 ruled out). The synthesis session re-verified every confirmed item against source
(ranged reads only) and re-adjudicated. Net result: 2 of the subagent's "High" findings
reclassified/ruled out (wrong mechanism), 1 kept as Medium, 1 kept as Low, and 1
additional finding added from a p3 spot-check the subagent had not flagged.

## F1 [A][Medium] Unbounded `await proposal.event.wait()` on batch-fallback proposal — domain.py:166

`Import.run` catches `BatchUnavailableError` from the first `detect_chapters` attempt
(domain.py:147), creates a `batch_fallback` proposal via the approval registry and then
does `await proposal.event.wait()` (domain.py:166). The event is set only by
`ApprovalRegistry.approve/decline` (agent_core/approval_registry.py:143-157) or by
`expire_stale()` (approval_registry.py:167-173). `expire_stale` has NO background
sweeper: it is called only lazily from six proposal tools (pal/tools/research.py:136,
compile.py:106, reorg.py:124,218, consolidate.py:95, url_fix.py:102,
promote_synthesis.py:129), and `create_proposal` does not sweep. The Discord side does
not resolve it either: `BatchFallbackView` has a 300 s discord timeout
(discord_interactions.py:435-437) which only disables buttons, and the
`_schedule_button_expiry` task (discord_interactions.py:581-592) merely pops
`active_proposals` and edits the message to "Proposal expired." — no decision is sent
and the event is never set. If the user never clicks, the import generator stays
suspended at line 166 indefinitely (holding the open PyMuPDF `doc`; the
`finally: doc.close()` at domain.py:247-248 never runs). Mitigation found: the daemon
spawns a task per message (agent_core/daemon.py:119-126), so a hung import does not
deadlock the session — other commands still run, and any of the six tools above will
eventually unblock it. User-visible symptom: the proposal message says "Proposal
expired." after 15 min but the import never completes and never reports.

Leads: p1:166, p2-B:166

## F2 [Q][Low] Unguarded `detect_chapters` in the "main" fallback branch — domain.py:179-181

After the user answers the batch-fallback proposal, the "retry" branch guards
`detect_chapters(doc, inference=ctx.agent.batch_inference)` with
`except BatchUnavailableError` (domain.py:170-177) and degrades to single-file. The
"main" branch (domain.py:178-181) calls
`detect_chapters(doc, inference=ctx.agent.inference)` with NO guard. Any exception
from the main inference path (the client has a 600 s read timeout —
agent_core/inference.py:121; HTTP 5xx, connection reset) propagates out of
`Import.run` → `handle_command` (pal/agent.py:603-608 has no local try) → the daemon's
`_run_handler` (agent_core/daemon.py:167-172), which logs and returns a raw
`f"{type(exc).__name__}: {exc}"` ErrorMessage. Not a crash (daemon-level catch
confirmed), but inconsistent: the identical call one branch up degrades gracefully.
Import is abandoned with `target_dir` already created (domain.py:104); an empty dir
passes the guard at line 96, so retry is clean.

Leads: p1:179 (p1:171 is the guarded retry branch — ruled out)

## F3 [A][Low] `/model default` mutates shared `inference.default_model` with no synchronization — domain.py:604

`PALModel.run`'s "default" branch assigns
`ctx.agent.inference.default_model = ctx.agent.config.model` (domain.py:604).
`default_model` is a plain attribute on the shared agent-core inference client, read
on every completion (`resolved_model = model or self.default_model`,
agent_core/inference.py:240,304). The daemon runs each message in its own task
(agent_core/daemon.py:119-126), so a `/model default` (or `/model <name>`, which routes
through `_dispatch_model_command`, domain.py:611) issued while a chat turn is in flight
changes the default under that turn; any subsequent completion in the in-flight turn
picks up the new model. No lock, no per-turn snapshot. Benign single-user system,
impact is a mid-turn model switch — hence Low, but it is the only true cross-task
state write in this file.

Leads: p3:604

## Ruled out

p1 (16 of 18):
- p1:118, p1:135, p1:192, p1:228, p1:256, p1:309 — `StreamWriter.drain()` is an
  awaitable; it suspends the command's coroutine, it never blocks the event loop. It
  only awaits while the socket send buffer is full (peer stopped reading — the
  connection is then already broken), and the daemon's own `_emit` uses the identical
  pattern (agent_core/daemon.py:105-107). Subagent's "blocks the event loop
  indefinitely" was a mechanism error.
- p1:122 — `fitz.open` in executor is wrapped in try/except (domain.py:120-125) → clean
  ErrorMessage.
- p1:146 — first `detect_chapters` attempt; wrapped in `except BatchUnavailableError`
  (domain.py:147) → proposal fallback, by design.
- p1:171 — retry branch; guarded by `except BatchUnavailableError` (domain.py:174-177).
- p1:194, p1:213 — executor calls for `pymupdf4llm.to_markdown` / `extract_chapters`:
  CPU-bound file operations that complete or raise; a raise reaches the daemon-level
  catch (agent_core/daemon.py:167-172) → user-visible error. No realistic hang
  scenario; "no timeout" adds no protection.
- p1:260 — `converter.convert` wrapped in `except ConversionError` (domain.py:258-264).
- p1:301, p1:624 — `_trigger_reindex_for_paths` docstring: "never raises"; body is a
  blanket try/except (domain.py:619-625).
- p1:365 — `inference.complete` wrapped in try/except with log + ErrorMessage
  (domain.py:364-370); client has 600 s read timeout (inference.py:121).
- p1:585 — `httpx.AsyncClient(timeout=10)` explicitly bounded.

p2 (19 of 20; the 20th, B:166, folded into F1):
- p2-A:87 — bare `except Exception:` around the `resolve()` containment check → yields
  clean "Invalid path" ErrorMessage; deliberate input validation (domain.py:81-89).
- p2-A:123 — `except Exception` around `fitz.open` → "PDF open failed" ErrorMessage.
- p2-A:147, p2-A:174 — `except BatchUnavailableError` is the designed fallback trigger.
- p2-A:247 — `finally: doc.close()` cleanup; correct.
- p2-A:263 — `except ConversionError` → clean ErrorMessage.
- p2-A:367 — `except Exception` in Learn → logged + "Learn failed" ErrorMessage.
- p2-A:501 — `except FileNotFoundError` on wisdom remove; expected, handled.
- p2-A:555 — `except ScratchpadTooLarge`; domain error, handled.
- p2-A:598 — `except Exception` around `/model list` fetch → warning + clean
  ErrorMessage (domain.py:584-600).
- p2-A:625 — reindex helper's blanket except (never-raises contract).
- p2-B:118, p2-B:135, p2-B:192, p2-B:228, p2-B:256, p2-B:309, p2-B:301 — known
  inventory artifact: plain `await` statements (drain ×6, reindex trigger) mislabeled
  as task creation. 7 mislabels.
- p2-C:55 — `drain_task = asyncio.create_task(writer.drain())` in `proposal_emitter`:
  intentional fire-and-forget (must not block the sync emitter) WITH a
  done-callback that logs failures (domain.py:57-61). No lost-exception problem.

p3 (23 of 24; the 24th, st:604, folded into F3):
- p3:27, p3:390, p3:400, p3:554 — purely local list appends (lint output, learn slugs,
  scratchpad). No shared state.
- p3:104 — `target_dir.mkdir(parents=True, exist_ok=True)` idempotent; guarded by the
  non-empty check at line 96; single command instance.
- p3:117, p3:134, p3:191, p3:227, p3:255, p3:308 — `writer.write(progress)` to this
  command's own stream only.
- p3:211, p3:246, p3:291 — `saved_articles.append` local list.
- p3:294, p3:295, p3:311, p3:404, p3:405 — `wiki.git_init/git_commit` sequential within
  one command; git provides its own locking.
- p3:389 — `learning.add` sequential command flow, no await between read and write.
- p3:452 — `profile.write(body)`; single logical writer, no interleaved await.
- p3:487, p3:500 — `wisdom.add/remove`; same.

## Notes

- Subagent draft returned 4 confirmed: (a) drains "High" — RULED OUT (mechanism
  error, see p1 group above); (b) event.wait "High" — kept, downgraded to Medium
  (per-message daemon tasks rule out session deadlock); (c) detect_chapters "High" —
  kept, downgraded to Low (daemon-level catch confirmed); (d) executors "Medium" —
  RULED OUT (no realistic hang; raises are caught upstream).
- Subagent had not flagged p3:604 (default_model) — added as F3 during synthesis
  spot-check.
- All 62 leads accounted for: 3 folded into findings, 59 ruled out (incl. 7 p2-B
  mislabels).
