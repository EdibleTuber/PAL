# Verified: pal_backfill_main p3+p4+p5 (task 1/1)

## Ruled out

- p3:51 — `config.model = _BACKFILL_DEFAULT_MODEL` — local mutation on a function-local object (`config` loaded at :46, used only below in `main()`); no shared state, no awaits, no locks.
- p4:8 — `import os` — import statement; no runtime effect.
- p4:12 — `from pal.config import load_config` — import statement; no runtime effect.
- p4:14 — `from pal.wiki import WikiManager` — import statement; no runtime effect.
- p4:50 — `if "PAL_MODEL" not in os.environ:` — simple environment-variable presence check; no side effects, no writes.
- p4:54 — `wiki = WikiManager(vault_path)` — constructor (pal/wiki.py:17–19) performs only `self.vault_path = vault_path`; no filesystem I/O, no `.git` check, no network call.
- p4:56 — `base_url=config.inference_url,` — `InferenceClient.__init__` stores config fields only; HTTP I/O occurs later in `complete()`, not at construction.
- p4:62 — `vault=vault_path,` — argument pass-through into `backfill_titles` (already adjudicated: 0 findings; the `apply` flag gates all writes inside that function).
- p4:65 — `apply=args.apply,` — argument pass-through into `backfill_titles` (already adjudicated).

## Notes

- p5 verdicts observed: consistent=5, contradicted=0, cannot_tell=1 in the input (line 42); after chain-following, line 42 resolves to consistent — net consistent=5, cannot_tell=0.
- p5:42 (vault path default) — "Defaults to PAL_VAULT_PATH env var or config default" is CONSISTENT: the agent_core config loader (agent_core/config.py:89 builds `env_name = f"{'PAL_'}{f.name.upper()}"`, :95–97 reads `os.environ[env_name]`, :35 sets the `vault_path` default to `Path.home() / "vault"`) implements exactly this two-tier default, and backfill_main.py:53 is `args.vault or config.vault_path`. The p5 tool marked it cannot_tell only because the chain crosses into agent_core; the chain is fully traceable from the entry point's `load_config()` call (pal/config.py:21–23 delegates to the base loader).
- p5:3/:36 (dry-run default) — `--apply` is `store_true` (default False) and is passed through as `apply`; the writes are gated inside the already-adjudicated backfill_titles.
- p5:48/:49 (forces gemma4 unless PAL_MODEL set) — :50–51 does exactly this.
- p4 mislabelling: three of eight p4 leads (lines 8, 12, 14) cite plain `import`/`from` statements labeled `env_read`/`fs_query` — import lines perform no external effect at runtime.

## Synthesis adjudication (2026-11-10)

- 0 findings; 14/14 leads ruled out (9 p3/p4 + 5 p5). All subagent rulings confirmed by synthesis.
- Subagent's WikiManager.__init__ claim spot-checked by synthesis: pal/wiki.py:17–19 — class + `__init__` with a single assignment, no I/O. Correct.
- p5:42 chain verified by synthesis directly (pal/config.py:21–23 → agent_core/config.py:35, :85–110 generic `PAL_`-prefixed loader). The claim is TRUE; the help text at :42 accurately describes the behavior.
- The file is a thin CLI entry point: argparse → config load → two client constructions (both store-only) → one delegated call to the already-adjudicated backfill_titles → report printing. No exceptions paths of interest (a top-level traceback on bad operator input is normal CLI behavior, and no lead points at it).
- Subagent's write tool was denied; report persisted by synthesis (verbatim from the subagent return, which was complete).
