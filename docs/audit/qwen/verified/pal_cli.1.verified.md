# Verified: pal_cli p1+p2+p3+p4+p5 (task 1/1)

## F1 [A][High] ConnectionError from daemon drops out of REPL command handler — unhandled crash

pal/cli.py:287 raises ConnectionError("Connection closed") when the daemon socket closes during client.command_stream(...). The handler at pal/cli.py:347 only catches RuntimeError:

    # line 347
    except RuntimeError as exc:
        console.print(f"\n[red]{exc}[/red]\n")

ConnectionError is a sibling of RuntimeError (both subclasses of Exception), not a subclass of RuntimeError, so it propagates uncaught and crashes the REPL. A dropped connection during a long-running command would terminate the session instead of printing a user-friendly error. The daemon-side command_stream raises ConnectionError at agent_core/client.py:126 (raise ConnectionError("Connection closed")), confirmed by grep of the installed package.

Leads: p2-A:347 (mislabeled — RuntimeError IS caught; ConnectionError is what is not), p1:338 (on_expiry says "RuntimeError caught at line 347" which is true for RuntimeError but misses ConnectionError)

## Ruled out

p1:298 — await client.connect() errors (ConnectionRefusedError, FileNotFoundError) caught by except at line 299; on_expiry label is incorrect.
p1:313 — await session.prompt_async() is normal interactive design; EOFError/KeyboardInterrupt handled at line 314.
p1:338 — RuntimeError from _run_command IS caught at line 347 (see F1 for the real gap: ConnectionError).
p1:369,375,383,412,435,456,477,494,517 — loop.run_in_executor(None, input) is intentional blocking I/O in a thread pool; no event-loop hazard.
p1:560 — await client.close() in outer finally block is acceptable cleanup; unhandled close is harmless at process exit.
p2-A:299 — proper error handling: prints message and calls sys.exit(1).
p2-A:314 — proper graceful exit on EOFError/KeyboardInterrupt during prompt.
p2-A:347 — RuntimeError IS caught and printed (the lead misidentified the problem; ConnectionError is what escapes).
p2-A:390 — ValueError defaults new_depth to msg.depth; proper fallback.
p2-A:554 — finally block stops Rich Live display; cleanup only.
p2-A:559 — finally block closes client; cleanup only.
p3:333 — synchronous global assignment _reasoning_display = cmd_args.strip() with no await between read and write; no concurrency issue.
p4:293 — PalClient(config.socket_path) is a local object constructor (zero I/O).
p4:298 — socket connection is guarded by try/except at lines 297-304.
p4:313 — stdin read via prompt_toolkit with EOFError/KeyboardInterrupt handling at line 314.
p4:369,375,412,435,456,477,494,517 — stdin reads via run_in_executor(None, input); blocking I/O in executor, not a file_read.
p4:560 — socket close in finally block; guarded by context.
p5:1-33 — all 33 claims are consistent with code; no contradictions found.

## Notes

- p1 schema mismatches: p1:298 on_expiry says "not caught in this function" but ConnectionRefusedError and FileNotFoundError ARE caught at line 299. p1:338 on_expiry says "RuntimeError caught at line 347" which is true for RuntimeError but misses ConnectionError (the actual gap).
- p2 schema mismatch: p2-A:347 identifies except RuntimeError as exc at line 347 as the handler — it IS the handler for RuntimeError. The real issue is that ConnectionError from the same code path is not caught (see F1).
- p4 schema mismatches: All 12 p4 leads are labeled kind: "file_read" but none are file reads. Lines 293/298/560 are socket operations (PalClient), lines 313/369/375/412/435/456/477/494/517 are stdin reads. The kind field is over-labeled.
- p5 verdicts seen: consistent=33 contradicted=0 cannot_tell=0
- No p2-B (task creation) or p2-C (task result handling) leads were present in the inventory for this file.

## Synthesis adjudication (2026-11-10)

- Report persisted by synthesis (subagent's write tool was denied for this task).
- F1 re-adjudicated with ranged reads: CONFIRMED, severity revised **High → Medium**. Verified: `_run_command` uses `client.command_stream` (cli.py:246); `command_stream` breaks SILENTLY on EOF (agent_core/client.py:148–149, not 126 — :126 is the one-shot `command()` path, which this file does not call), so control reaches `raise ConnectionError("Connection closed")` at cli.py:287; cli.py:347's `except RuntimeError` cannot catch it (sibling classes); `main()` (:563–565) is a bare `asyncio.run(run_repl())` with no catch. The ErrorMessage path (:283 `raise RuntimeError`) IS handled at :347, so the author's intent was a friendly in-REPL error — the ConnectionError sibling is simply missing from the tuple. Medium rather than High: single-user interactive client, no corruption, relaunch recovers; blast radius is one lost in-flight command result.
- Quiet cousin (not filed — not lead-pointed, noted for completeness): `client.chat` ALSO breaks silently on EOF (agent_core/client.py:100–101), so a daemon death mid-chat shows nothing at all (no response, no error) and the REPL continues to the next prompt.
- Ruled-out spot checks (synthesis): p1:313 (:312–315), p1:369/375/383 (:368–385), p2-A:390 (:388–391), p3:333 (:331–334), p4:293 (:293) — all rulings confirmed as written. p5 spot checks: claims at :43→:48 (union of command classes), :94→:95 (pure formatter), :255→:257 (multi-chunk progress bar), :418→:421 (v1 edit→decline), :543→:544 (reasoning >20-line truncation) — all true.
