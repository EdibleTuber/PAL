# Verified: pal_daemon_main p4+p5 (task 1/1)

## Ruled out

- **p4:14** (`run_daemon(PALAgent(), config_cls=PALConfig)` — kind "subprocess"): mislabeled. This is a synchronous in-process function call to a vendored library. No `subprocess`, `Popen`, `os.fork`, or any OS-level process spawning exists anywhere in `pal/daemon_main.py`. The vendored `run_daemon` (agent_core/runtime.py:74) runs the event loop in-process via `asyncio.run()` (line 123), which is not a subprocess.

## Notes

- **p4 "subprocess" mislabel**: The p4 tool classified the call to `run_daemon()` as a subprocess external effect. It is not — `run_daemon` is a synchronous function call within the same process. The only async activity is `asyncio.run(daemon.serve())` which runs the event loop on the calling thread, not in a child process.
- **p5 rejects-fragment artifact**: The rejects file (`p5_claims__pal_daemon_main.rejects.jsonl`) contains 27 rows of `{"reason":"not_json","raw":...}` fragments. These are fragments of the 3 JSON claims split by the line-based parser because the p5 pass emitted pretty-printed (multi-line) JSON. Synthesis verified these fragments reconstruct to exactly the same 3 claims (lines 3, 4, 5). No extra claims exist there.
- **SIGTERM nuance**: The docstring says "Signal handling (SIGINT/SIGTERM) is handled by asyncio's default behavior inside run_daemon." This is substance-true but imprecise. `asyncio.run()` provides default KeyboardInterrupt handling for SIGINT (main task cancelled, loop shut down). SIGTERM has no asyncio involvement — it gets Python's runtime default (process terminates). Neither SIGINT nor SIGTERM has custom handlers: `grep -n "signal\|SIGINT\|SIGTERM\|add_signal_handler\|signal.signal" agent_core/runtime.py` matches only `asyncio.run(daemon.serve())` (:123), and `agent_core/daemon.py` has zero signal-related references. The docstring's imprecision does not rise to W-class (docs vs code): the substance — no custom handling, defaults apply — is true and would not misdirect a developer.
- **Verdicts**: p5:3 = consistent, p5:4 = consistent, p5:5 = consistent (with SIGTERM nuance noted above).

## Synthesis adjudication (2026-11-10)

- 0 findings; all 4 leads resolved (1 mislabeled p4 + 3 p5 rows forming 2 claims, both verified/resolved).
- p5:3 verified by synthesis directly: `main()` at :13–14 does exactly `run_daemon(PALAgent(), config_cls=PALConfig)`; `run_daemon` (agent_core/runtime.py:74) is synchronous and ends at :123 with `asyncio.run(daemon.serve())`, which blocks the calling thread until the serve loop completes. "Blocks on the daemon's serve loop" — TRUE.
- p5:4/:5 cannot_tell resolved CONSISTENT by synthesis directly: `grep "signal|SIGINT|SIGTERM|asyncio.run|run_until_complete|KeyboardInterrupt"` over agent_core/runtime.py matches only the `asyncio.run` line (:123); agent_core/daemon.py (Daemon at :40) has zero signal references. No custom handler exists anywhere in the daemon path — SIGINT → asyncio.run's default KeyboardInterrupt behavior; SIGTERM → runtime default (terminate). The docstring is imprecise on SIGTERM ("asyncio's default" vs the runtime default) but substance-true (nothing custom is installed); below the W bar — W3's precedent was a factually-wrong stale comment, this is vague-but-true.
- p4:14 "subprocess" is a mislabel: thin entry point, in-process call, framework runs the loop (same class as the backfill_main/cli entry-point rulings).
- The 27 rejects-fragment rows were independently confirmed by synthesis as not_json fragments of the same 3 claims (fragments carry `"line": 3/4/5` and the same quotes) — a pretty-printed-JSON parser artifact, no extra claims.
- Subagent's write tool was denied; report persisted by synthesis (verbatim from the subagent return, which was complete).
