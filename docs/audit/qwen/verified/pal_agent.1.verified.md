# Verified: pal_agent p1+p2+p3 (task 1/2)

Source: /mnt/secondary/projects/PAL/pal/agent.py (690 lines, HEAD 6531f32).
Leads: p1=4, p2=16, p3=35 (55 total).

Note: reviewer-egpu write denied; synthesis session persisted this report after
re-adjudicating the scanner-task ruling (subagent's stated reason was mechanically
wrong — see F1) and spot-checking `record_usage` (agent_core/agent.py:102–114:
plain `self.last_usage[channel_id] = usage` assignment, not an increment — ruled
out correctly despite loose GIL phrasing).

## F1 [A][Low] Four unguarded fire-and-forget `maybe_scan` tasks — agent.py:491, 524, 578, 594

`handle_chat` spawns `asyncio.create_task(self.scanner.maybe_scan(...))` at four
sites with no assigned reference and no done-callback (comment at :489: "Proactive
learning scan (fire-and-forget)" — intentional as fire-and-forget, but unguarded).
`maybe_scan` (agent_core/learning_scanner.py:215–250) is NOT internally guarded: its
first real step `await self.extractor(...)` (:229) is `extract_candidate`, whose
docstring says "Other exceptions propagate to the caller" (learning_scanner.py:91 —
it only catches `BatchUnavailableError` and `asyncio.TimeoutError`, :99–106), and
`self.emit(msg)` (:250) writes to the socket writer and can raise. Any such
exception becomes an un-retrieved task exception: the daemon's `_run_handler`
(agent_core/daemon.py:167–172) catches only the handler coroutine it drives, not
tasks the handler spawns. Best case is a bare "Task exception was never retrieved"
loop warning at GC, with no channel/turn context; the learning-scan feature fails
silently for that turn with no log a human can tie to the user. Impact is small
(background feature, no data corruption — hence Low), but the subagent's ruling of
"exceptions caught by the daemon's _run_handler wrapper" was wrong on the mechanics.
Leads: p2-B:491, p2-B:524, p2-B:578, p2-B:594 (with their p2-C pairings)

## Ruled out

p1 (all 4):
- p1:228 — `timeout=config.fetch_timeout`; default 30 s (agent_core/config.py) is a
  reasonable HTTP bound for URLFetcher.
- p1:298 — `timeout=15.0` passed to `extract_candidate`, which wraps the call in its
  own `asyncio.wait_for` with TimeoutError handling (learning_scanner.py:99–106);
  verified in source.
- p1:615 — `_get_manager_status` httpx `timeout=10.0`; caught at :619, logged, safe
  default `{}`.
- p1:683 — `_request_model_swap` `timeout=120.0` for a possibly slow model load on
  the inference server; caller :674–677 wraps in try/except → user error message.

p2 A (all 4):
- p2-A:77 — `except ScratchpadTooLarge`; specific domain error → informative message.
- p2-A:599 — `except Exception` in handle_chat: `logger.exception` + ErrorMessage to
  user; deliberate handler-level safety net.
- p2-A:619 — `_get_manager_status`: logs warning, returns `{}`; non-critical health
  check.
- p2-A:676 — `_dispatch_model_command`: error message to user.

p2 B/C (12 of 12; the four maybe_scan sites moved to F1):
- p2-B/C:435, 449 — `drain_task = asyncio.create_task(writer.drain())` WITH logging
  done-callbacks (:437–441, :451–455); same sanctioned pattern as
  commands/domain.py:53–61. Intentional.

p3 (all 35):
- p3 setup()-range leads — constructor-level assignments at startup, before any
  task exists.
- p3 handle_other leads — synchronous registry/writer calls (daemon dispatches
  handle_other synchronously, agent_core/daemon.py:136); no await between read and
  write.
- p3 callback-assignment leads — per-turn local wiring.
- p3 `conv.add_*` leads — `conv` is the per-connection conversation object
  (agent_core/daemon.py:103), local to this handler task.
- p3 `self.record_usage` leads — plain per-key assignment
  `self.last_usage[channel_id] = usage` (agent_core/agent.py:114); no
  read-modify-write, last-write-wins per key is the semantics.

## Notes

- 55 leads: 4 (+ their p2-C pairings) into F1, remainder ruled out.
- All quotes matched source; no schema mismatches; nothing unverifiable.
- Subagent draft misruled the maybe_scan sites citing the daemon `_run_handler`
  catch — corrected in synthesis (see F1).
