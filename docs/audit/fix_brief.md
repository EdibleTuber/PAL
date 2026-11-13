# Fix brief — re-verified audit findings

Independent re-verification of the 18 findings in `docs/audit/findings.md`,
against repo `main` HEAD `6531f32` and agent_core **1.13.0** (deployed venv),
on 2026-11-10. All 18 are real. Six additional defects the audit missed are
filed here as `NEW-1`..`NEW-4` plus two docstring items.

Grouped into fix rounds, smallest blast radius first. Work one round at a time.

---

## Before you start

### Read agent_core from the deployed venv, not the sibling checkout

Three copies exist on this machine and they do not agree on line numbers:

```
/mnt/secondary/PAL/.venv/lib/python3.12/site-packages/agent_core/   # 1.13.0 — authoritative
/mnt/secondary/projects/agent_core/agent_core/                      # divergent checkout, DO NOT cite
/mnt/secondary/agent_core-wt-phase6/agent_core/                     # worktree, matches 1.13.0
```

Five files differ between the first two: `config.py`, `inference.py`,
`runtime.py`, `retrieval.py`, `_builtin_impls.py`. Every `agent_core/…` line
number in this brief and in `findings.md` refers to the venv copy.
(`brief_template.md` already states this; it is repeated here because reading
the wrong copy makes A3's correct citations look wrong.)

### Running the suite

The repo has no `.venv`. Use the deployed interpreter with the repo on `PYTHONPATH`:

```sh
cd /mnt/secondary/projects/PAL
PYTHONPATH=/mnt/secondary/projects/PAL \
  /mnt/secondary/PAL/.venv/bin/python -m pytest -q \
  --ignore=tests/test_client.py --ignore=tests/test_daemon.py \
  --ignore=tests/test_integration.py --ignore=tests/test_chat_research_integration.py \
  --ignore=tests/test_prompt_injection.py
```

Expect **625 passed, 3 failed** (reproduced 2026-11-10). The 3 failures are
`tests/test_config.py` asserting agent_core 1.4-era defaults; the 5 ignored
modules import `pal.client`/`pal.daemon`, deleted in `986bd3c`. Both predate
this work — do not "fix" them inside a round, and do not let them mask a new
failure.

### Rules for these rounds

- One round at a time. Do not merge rounds — a large fix diff is where the next
  regression hides.
- Every regression test must be **observed failing against the pre-fix code**
  before the fix lands. A test that passes before the fix proves nothing.
- Code shown below is a **sketch to verify against the real file**, not text to
  transcribe. Rounds 1 and 2 deliberately contain no code: they cover a security
  invariant and wait/cancellation paths, where plan-supplied code gets
  transcribed along with its author's misconception, and a test written from the
  same plan shares it. Write those against the actual source.
- Re-run the command above after each round and compare against 625/3.

---

## Round 1 — Vault containment

One defect with three documentation surfaces asserting the opposite.
Security invariant: interfaces and failure modes specified, code not.

### X1 — `move_file` escapes the vault — **Medium→High**

`pal/tools/vault.py:296–306`, `pal/reorg.py:32–52`

Confirmed by execution against a temp vault, not by reading. All four succeeded:

```
move_single("notes/secret.md", "../escaped.md")   → file left the vault
move_single("../outside.md", "notes/stolen.md")   → host file pulled in
move_single("notes/a.md", "notes/_wisdom/a.md")   → wrote into a system dir
move_single("notes/b.md", "/tmp/abs_escape.md")   → absolute dst, left the vault
```

The **absolute-path vector is not in the audit's write-up**; it names only `..`.
The deployed `pal/reorg.py` and `pal/tools/vault.py` are byte-identical to the
repo's, so this is live. No test covers move containment at all.

Why it gets through:

- `vault.py:298` — `if resolved_src is not None and not resolved_src.exists()`.
  A `None` from `_resolve_safe` means *escape*, and it falls through to the move
  instead of erroring.
- `vault.py:304` — `dst` is never passed through `_resolve_safe` at all, and
  `:312` reindexes the raw `dst`.
- `reorg.py:43–44` — `vault_path / src|dst` with no `.resolve()`, no
  `is_relative_to`.
- `reorg.py:36–38` — `_is_top_level_system` inspects only `parts[0]`, so
  `notes/_wisdom/x.md` is admitted.

Invariant the fix must establish:

- For both `src` and `dst`: the resolved path is inside the vault root, or the
  call fails with a structured error naming the offending side. Rejection must
  cover `..` components, absolute paths, and symlinked escapes — which is why
  the check resolves rather than string-matches.
- `_resolve_safe` returning `None` is an *error condition*, never a skipped
  check. Match the siblings that already get this right: `EditFile
  vault.py:112–118`, `CreateFile :208–214`, `DeleteFile :365–371`.
- System-directory blocking is any-component, not top-level.
  `pal/tools/vault.py:54–56` already has `_is_system_path`; `MoveFile` never
  calls it.
- Decide deliberately where the guard lives. The batch path is already contained
  by `validate_operations` → `_path_inside_vault` (`reorg.py:81–82, 124–129`);
  putting containment in `move_single` itself closes both entry points,
  including any future caller.

Tests must discriminate:

- All four vectors above need a case, each observed failing (i.e. the move
  *succeeding*) before the fix.
- Assert on **where the file ended up**, not on the returned string. A test that
  only checks for an error message passes against a fix that errors *after*
  moving.
- Add a case proving the legitimate in-vault move still works, or the fix can
  silently become "reject everything".
- Cross-mount `dst` raises `OSError`, which `vault.py:305` does not catch.
  Either widen the catch or let containment make it unreachable — decide, don't
  leave it.

### NEW-1 — `docs/security.md` asserts the guarantee X1 breaks — **High** *(missed by audit)*

`docs/security.md:16–24`

The audit never opened this file — zero references to `security.md`,
`architecture-flows.md` or `agent_ecosystem_direction.md` anywhere in
`findings.md`. It states:

```
## Path Traversal

All file operations resolve paths through a safe-path check that rejects:

- `..` components
- Leading `/` (absolute paths)
- Any resolved path outside the vault root

System directories (underscore-prefixed like `_wisdom/`, `_profile/`, `_channels/`)
are additionally blocked from write operations.
```

All five claims are false for `move_file`. This is the project's trust
document, which makes it a stronger surface than W1's module docstring.
**Fix the code to match the document** (X1 above) rather than weakening the
document. Re-read it once the invariant holds and confirm every line is true.

### W1 — `vault.py` module docstring, same defect — **Medium**

`pal/tools/vault.py:8–9` — "All path operations use _resolve_safe to prevent
vault escapes. System directories (_-prefixed) are blocked on write
operations." Resolved by the X1 code fix; verify rather than edit.

### NEW-2 — same docstring undercounts its own classes — **Cosmetic** *(missed by audit)*

`pal/tools/vault.py:3`, `pal/tools/__init__.py:6`

`vault.py:3` says "Three write Tool subclasses (EditFile, CreateFile,
MoveFile)". There are five — `DeleteFile:326` and `ReplaceInFile:407`.
`pal/tools/__init__.py:6` repeats the omission ("vault.py (edit/create/move…)").
The audit quoted line 8 of the first docstring (W1) and lines 2–3 of the second
(W4) without noticing either.

Stop the clock: don't write "Five write Tool subclasses" — that breaks on the
next tool added. Name the category and drop the count.

---

## Round 2 — Hangs and lost work

Wait, cancellation and cross-component ordering. No code sketches by design.

### A2 — unbounded batch-fallback waits, two sites — **Medium**

`pal/commands/domain.py:166`, `pal/categorizer.py:188`

`await proposal.event.wait()` with no timeout and no `expires_at` check. The
event is set only by `approve`/`decline`
(`agent_core/approval_registry.py:143–157`) or `expire_stale()` (`:167–173`),
and **nothing sweeps** — `expire_stale` runs lazily from seven call sites in six
tools, and `create_proposal` never sweeps. If the user never clicks,
`domain.py` stays suspended at `:166` holding an open PyMuPDF `doc`; the
`finally: doc.close()` at `:247–248` never runs and the import never reports.

Two corrections to the audit's reasoning here:

- It says `_schedule_button_expiry` (`discord_interactions.py:581–592`) fails to
  send a decision. True but beside the point: the batch-fallback post path at
  `:754–761` **never calls it**. That hook only runs from `_post_proposal:634`.
- It says the 300 s view timeout "only disables buttons". There is no
  `on_timeout` override in the file; the view just stops accepting clicks.
  Nothing is disabled, nothing is signalled.

Both errors point the same way: *nothing at all* on the Discord side resolves
this proposal. Fix the backend wait; do not rely on a Discord-side timeout that
does not exist.

Fix requirement:

- Bound both waits. The codebase already has the pattern —
  `pal/tools/research.py:131–136` computes `remaining` from
  `proposal.expires_at`, uses `asyncio.wait_for`, and calls `ar.expire_stale()`
  on `TimeoutError`. Reuse it; don't invent a second shape.
- On expiry both sites must reach the same terminal state they reach on decline.
  `domain.py:182` and the categorizer's trailing branch already fall through to
  the safe default — confirm that holds for `status == "expired"` with
  `approval_choice is None`, rather than assuming it.
- `domain.py` must still run its `finally: doc.close()` on the expiry path.

Tests must discriminate: drive a proposal that is never answered and assert the
call *returns* within the bound with the single-file/default outcome. Verify the
test hangs (or times out) against pre-fix code. Assert the PyMuPDF handle was
closed — a test that only checks the return value passes against a fix that
leaks the handle.

### Q1 — unguarded "main" branch in the same flow — **Medium**

`pal/commands/domain.py:178–181`

The retry branch catches `BatchUnavailableError` and degrades to single-file
(`:170–177`); the main branch does not. Any main-inference error (600 s read
timeout, 5xx, reset) propagates to `agent_core/daemon.py:167–172` and surfaces
as a raw `Type: msg`, abandoning the import with `target_dir` already created.
Same function as A2 — fix together, test separately.

### A5 — daemon disconnect kills the REPL — **Medium**

`pal/cli.py:287, 347`, `agent_core/client.py:100–101, 148–149`

`cli.py:287` raises `ConnectionError`; `:347` catches only `RuntimeError`. They
are siblings under `Exception`, not parent and child, and `main()` at
`:563–565` is a bare `asyncio.run`. A daemon restart mid-command prints a
traceback and exits, losing the in-flight result.

The author's intent is clear from `:283`, where the `ErrorMessage` path raises
`RuntimeError` specifically so `:347` can render it in-REPL. `ConnectionError`
is simply missing from the tuple.

Also decide on the quiet cousin: `client.py:100–101` breaks silently on EOF in
`chat`, so a mid-chat disconnect shows the user nothing at all. The audit noted
it and declined to file it. It is the same defect on the more-used path.

### A1 — unguarded send in the batch-fallback button path — **Low**

`pal/discord_interactions.py:463–479, 749–752`

`send_choice` at `:749–752` has no try/except, and neither do its three callers.
If the send raises, the decision never reaches the backend, the flow at A2's
site waits forever, and the `edit_message` confirmation never runs. The same
function wraps its own best-effort skip send at `:766–773` — the button path is
the outlier. Pairs with A2: this is the front half of that flow.

---

## Round 3 — Error boundaries

Local, loud-on-failure, cheap to reverse. Lighter review than Rounds 1–2.
Sketches below are shapes to verify, not text to paste.

### Q3 — reorg batch discards all per-op state on an unexpected error — **Medium**

`pal/tools/reorg.py:290–293`, `pal/reorg.py:268–293`

The op loop does not wrap `_execute_move`/`_execute_merge`, so expected failures
return `{"status":"failed"}` and the batch continues, but an unexpected
exception escapes to the broad catch at `tools/reorg.py:292`, which returns only
`Error: reorg execution failed: {exc}`.

By then earlier moves are applied *and individually git-committed*
(`reorg.py:252–256`), and the ground-truth echo (`tools/reorg.py:295–302`), the
ok/failed report, and the post-loop `rebuild_index`/`trigger_reindex`
(`reorg.py:283–293`) are all skipped — leaving `_index.md` stale against the
applied moves. The tool's own description at `:236–241` promises "Partial
failures don't abort the batch. Returns a structured report." The exception path
violates both.

Realistic trigger: a mid-batch merge whose `compiler.merge_into_existing` LLM
call raises, or the unguarded `src_full.read_text()` at `reorg.py:319` (the
audit cites `:317`; that line is blank).

Fix: catch per op inside the loop so an unexpected exception becomes a `failed`
row, and the index rebuild and ground-truth echo always run. Test: inject a
raising merge mid-batch, assert earlier ops report `ok` with
`src_exists_after`/`dst_exists_after` populated and that `rebuild_index` was
called.

### Q5 — vault writes sit outside the fetch guard — **Low**

`pal/researcher.py:132–149, 167, 179`

The try/except wraps only `fetcher.fetch`. `raw_dir.mkdir` (`:167`) and
`raw_path.write_text` (`:179`) are outside it, and neither the `asyncio.gather`
at `:227` nor the topic loop at `:255–257` (the audit cites `:263`, which is the
tally loop) has a try. An `OSError` on the vault filesystem aborts every
remaining topic and discards the in-progress `ResearchReport`.

Worth stating in the fix: `asyncio.gather` does **not** cancel sibling fetch
tasks on first exception — they keep running orphaned.

Fix: move mkdir/write inside the per-source try and return
`SourceResult(status="save_failed")`, so one bad write costs one source instead
of the run.

### Q6 — `add_learning`'s commit is outside its guard — **Low**

`pal/tools/scratch.py:120–124`

```python
try:
    slug = ctx.agent.learning.add(title=title, body=body, source="conversation")
except Exception as exc:
    return json.dumps({"error": str(exc)})
ctx.agent.wiki.git_commit(f"learn: add {slug}")   # :124 — outside
return json.dumps({"slug": slug, "title": title})
```

`git_commit` runs `subprocess.run(..., check=True)` (`pal/wiki.py:183–203`). If
it raises, the learning file is already on disk but the LLM sees a bare executor
error instead of the success JSON. The file self-heals — every later
`git_commit` does a whole-tree `git add .` — so move the commit inside the try,
log, and still return the success JSON.

### Q4 — silent zero in the reorg reference preview — **Low**

`pal/tools/reorg.py:95–98`

`except Exception: references_preview = 0` with no log. The approval UI
(`:111–118`) then tells the user zero other files reference these paths, which
may be false, and they approve against wrong information. `count_references`
already absorbs per-file `OSError` at `reorg.py:164–167`, so this backstop is
rare — and invisible in both the UI and the logs when it fires. Log a warning
and surface an unknown marker rather than a confident `0`.

---

## Round 4 — Documentation drift

Mechanical and fully specified. Low risk, loud failures — this round can be
larger than the others.

### W5 — README env table lists wrong code defaults — **Low**

| README | Documented | Actual (`agent_core/config.py`) |
|---|---|---|
| `:167 PAL_INFERENCE_URL` | `192.168.1.14:11434` | `127.0.0.1:11434` (`:30`) |
| `:195 PAL_BATCH_INFERENCE_URL` | `192.168.1.14:11434` | `127.0.0.1:11434` (`:43`) |
| `:196 PAL_BATCH_MODEL` | `gemma-4-E4B-it-Q4_K_M` | `Qwen3.5-35B-A3B-Q4_K_M` (`:44`) |

**Do not touch `README.md:179`** — `PAL_SEARXNG_URL` really does default to
`192.168.1.14:8080` (`config.py:38`). That one LAN IP being correct is what
makes the other three look plausible.

Stop the clock: three hand-copied defaults drifted once and will drift again on
the next agent_core bump. Prefer pointing the column at `agent_core/config.py`
(or generating the table) over pasting today's values back in.

### W6 — `PAL_CHANNELS_DIR` is a silent no-op — **Low**

`README.md:187, 408`

No code reads it. Neither `BaseConfig` (`agent_core/config.py:25–47`) nor
`PALConfig` has a `channels_dir` field, so the generic loader never derives that
env name. History actually lives at
`<vault>/_channels/<agent_name>/<channel_id>/history.jsonl`
(`agent_core/channels.py:39, 52–54`). Remove the row and correct the path.

### NEW-3 — scratchpad path drops the agent_name segment — **Low** *(missed by audit)*

`README.md:410`, `README.md:373`

`:410` says the scratchpad is at `<vault>/_channels/<channel_id>/scratch.md`. It
is at `<vault>/_channels/<agent_name>/<channel_id>/scratch.md` — see
`agent_core/scratchpad.py:3` and `channels.py:39`. This is the line directly
after the one W6 flags, in the same paragraph block. Check `:373`'s vault-layout
line in the same pass.

### NEW-4 — agent_core pin disagrees with the deployed runtime — **Medium** *(missed by audit)*

`pyproject.toml`

```
"agent_core @ git+https://github.com/EdibleTuber/agent_core.git@v1.4.0"   # declared
agent_core 1.13.0                                                        # deployed venv
```

Root cause of the 3 `test_config.py` failures: the repo suite is green only
against the 1.4.0 it declares. The audit's own Tests section says "the pin and
the deployed runtime disagree **(see D)**" — and section **D is empty**. It
diagnosed this and never filed it.

Decide which is authoritative and make them agree. Until then the test suite is
measuring a configuration nobody runs.

### W2 — shared prompt describes a CLI-only approval UX — **Low**

`pal/prompts/system.py:37, 44` — "emits a CLI approval prompt", "requires
explicit user approval via the CLI prompt", "blocks until the user approves or
declines in the CLI". The code is adapter-agnostic: `tools/research.py:124–134`
emits via `ctx.emit`; the CLI renders a prompt, Discord renders buttons. One
`PAL_BASE_PROMPT` serves both. Every behavioural claim around it (blocks until
response, explicit approval not text agreement, depth 3 / max 10) is accurate —
change only the mechanism wording.

### W3 — `Message` union cites a file that doesn't exist — **Cosmetic**

`pal/protocol.py:148–150` — "Consumers like pal/client.py and pal/cli.py import
this for their isinstance branches and type annotations." `pal/client.py` was
deleted in `986bd3c`; `pal/cli.py:35` imports `Message` and never uses it; no
isinstance branch anywhere uses the union. Its only real use is the annotation
at `discord_interactions.py:638`. Correct the comment and drop the dead import
at `cli.py:35`.

### W4 — stale PR provenance in `tools/__init__.py` — **Cosmetic**

`pal/tools/__init__.py:2–9` — "Phase F migration complete through PR7. Tool
subclasses are added per-category across PRs 2-7." Three of the 21 re-exported
classes are post-PR7 standalone commits: `url_fix.py` (5 commits, 2026-05-09)
and `promote_synthesis.py` (2 commits, 2026-05-11), against PR7 `4a3c7b5` on
2026-05-08. Confirmed with `git merge-base --is-ancestor` — negative against all
three PR ranges. The per-PR list has no line for either file. Fix alongside
NEW-2, same docstring.

---

## Deferred

Both confirmed, both genuinely low. Don't bundle them into an earlier round.

### A3 — `/model default` mutates shared state unsynchronised — **Low**

`pal/commands/domain.py:604, 611`, `agent_core/inference.py:240, 304`

`default_model` is a plain attribute on the shared client, read per completion.
The daemon runs each message in its own task (`daemon.py:119–126`), so
`/model default` during an in-flight turn switches the model under it.
Single-user system; impact is a mid-turn model switch.

### A4 — four unguarded fire-and-forget learning scans — **Low**

`pal/agent.py:491, 524, 578, 594`

No retained reference, no done-callback. `maybe_scan`
(`agent_core/learning_scanner.py:215–250`) has no internal try, and
`extract_candidate` explicitly lets non-timeout exceptions propagate (`:91`).
The daemon's catch covers the handler coroutine, not tasks it spawns, so
failures become un-retrieved task exceptions and the feature fails silently per
turn. A done-callback that logs is the whole fix.

---

## Citation corrections

Where `findings.md` points at the wrong line. Substance holds in every case;
these will waste your time if followed literally.

| Finding | Cited | Actual |
|---|---|---|
| Q3 | `reorg.py:317` | `reorg.py:319` (`:317` is blank) |
| Q5 | `researcher.py:263` | `:255–257` (`:263` is the tally loop) |
| Q4 | `tools/reorg.py:109–114`, `reorg.py:163–166` | `:111–118`, `:164–167` |
| W3 | `protocol.py:147–149` | `:148–150` |
| Q2 | "the only unwrapped Discord send in that file" | `:195`, `:200`, `:208`, `:212` are also unwrapped |
| A2 | `_schedule_button_expiry` applies | never called on the batch-fallback path |

A3's `agent_core/inference.py:240, 304` looks wrong against the sibling checkout
and is **correct** against the deployed 1.13.0. See the baseline warning above.

Q2 itself (`pal/discord_adapter.py:175`, the unwrapped main reply send, outside
the `:138–171` try) is real — only its "sole outlier" argument is wrong.

---

## What the audit did not cover

The inventory pass is complete and its quality is high: 35 of 35 checklist items
checked, 41 referenced evidence files all present, test numbers reproduce
exactly. But the checklist covers only **step 2** of the eight-step plan in
`docs/audit/notes.md`. Treat the rest as open, not clean:

- **Four of nine categories are empty headers** — O (ordering), D (config/docs
  drift), S (startup/deploy/host), I (observed live).
- **Ordering walks never ran** (plan step 3): `pal/agent.py`,
  `DiscordStreamProcessor`, the batch-fallback flow.
- **Drift checks never filed** (step 5). Repo and deployed `pal/` differ in four
  files: `agent.py`, `backfill_main.py`, `commands/domain.py`,
  `commands/__init__.py`. The systemd byte-compare and the version pin (NEW-4)
  are in the same step.
- **Docs beyond README never read** (step 6) — which is how NEW-1 was missed.
  `security.md`, `architecture-flows.md`, `agent_ecosystem_direction.md`,
  `document-import.md`, `article-format.md`, `searxng-setup.md` all still
  unexamined.
- `findings.md` says so itself: the title reads "working file — synthesis in
  progress" and `## Not yet covered` reads "(pending final pass)".
- `pal_discord_interactions.3.verified.md` exists on disk but is referenced
  nowhere in `findings.md`.

The reported claim that the audit is "fully complete" refers to the inventory
checklist. It is not true of the audit.
