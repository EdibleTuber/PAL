# Reviewer-egpu dispatch template (PAL audit synthesis)

Use per dispatch: fill `{placeholders}`, send the whole block as the subagent
prompt to `reviewer-egpu`. ONE task at a time, foreground, never while any other
model request is in flight. On transport error: wait (original likely still
running), re-issue only if nothing arrives.

KNOWN: the subagent's write tool is SOMETIMES denied (denied every task up to
`pal_compiler`, then granted on `pal_tools_vault`). Either way: check whether
the report file actually landed on disk after the subagent returns; if it is
missing, persist the returned report content yourself. In both cases,
spot-check before accepting. Do not treat "Report written: no" as failure.

---

You are verifying audit-inventory leads for the PAL codebase. READ-ONLY on the
repo: do not edit/create/delete any repo file EXCEPT the single report file named
in OUTPUT (if the write tool is denied, return the full report content in your
final message instead). Repo: /mnt/secondary/projects/PAL at HEAD 6531f32 (main).

INPUT LEADS (jsonl, one JSON object per line; skip lines that are {"none": true}):
{numbered list: /mnt/secondary/projects/PAL/docs/audit/qwen/out/<pass>__<name>.jsonl (N leads)}

Lead schemas (only the passes present in this task; all leads carry "file" = {source path}):
- p1: {"kind" (timeout|event_wait|executor|sleep|gather), "line", "quote", "value", "on_expiry", "enclosing_function"}
- p2 A (except clause): {"part":"A","line","quote","catches","try_line","handler_does","enclosing_function"}
- p2 B (task creation): {"part":"B","line","quote","enclosing_function"}
- p2 C (task result handling): {"part":"C","line","quote","result_kept","result_awaited_or_checked","enclosing_function"}
- p3 (state write): {"target","new_value","locks_held","awaits_between_read_and_write","line","quote","enclosing_function"}
- p4 (external effect): {"kind" (http|file|shell|git|...), "target", "line", "quote", ...}
- p5 (doc-vs-code claim): {"claim","quote","line","verdict" (consistent|contradicted|cannot_tell_from_this_file), ...}

SOURCE: {path} ({N} lines). {1-2 sentences: what this file does, key functions + line ranges.}

ESTABLISHED CONTEXT (already adjudicated in earlier tasks — build on it, do not re-litigate):
- Daemon (agent_core/daemon.py) spawns ONE TASK PER MESSAGE (:119–126); handler exceptions are caught in `_run_handler` (:167–172) → logged + ErrorMessage to user. Tasks the handler SPAWNS are NOT covered by that catch.
- `writer` is the per-connection asyncio StreamWriter; `await writer.drain()` suspends the coroutine, never blocks the loop (daemon `_emit` :105–107 is the sanctioned pattern).
- ApprovalRegistry (agent_core/approval_registry.py): proposal.event is set only by approve/decline/expire_stale (:143–173); `expire_stale` is LAZY — called only from six proposal tools (tools/research.py:136, compile.py:106, reorg.py:124,218, consolidate.py:95, url_fix.py:102, promote_synthesis.py:129), no background sweeper, `create_proposal` does not sweep. The unbounded batch-fallback wait is already recorded as A2 (commands/domain.py:166); judge any other await-proposal-event site on its own consequences.
- `wiki.git_commit` guarded: bails without `.git`, no-op when clean (pal/wiki.py:176–200); `git_init` guarded (:149). `allowlist.seed` idempotent (agent_core/allowlist.py:60–66).
- Inference client (agent_core/inference.py): `_post_with_retry` (5 attempts, backoff), 600 s read timeout (:121), `default_model` read per completion (:240, :304).
- `record_usage` is a plain per-key assignment (agent_core/agent.py:114) — not a lost update.
- `maybe_scan` (agent_core/learning_scanner.py:215–250) is NOT internally guarded; its 4 fire-and-forget sites in agent.py are already recorded as A4.
- Findings confirmed so far: A1 (discord_interactions.py:463–479,749–752 unguarded send), A2 (commands/domain.py:166 unbounded batch-fallback proposal wait; SECOND SITE categorizer.py:188 — the categorizer's own batch-fallback path has the identical unbounded wait; do not re-file either site), A3 (commands/domain.py:604 model switch race), A4 (agent.py:491,524,578,594 unguarded scanner tasks), Q1 (commands/domain.py:178–181 unguarded detect_chapters), W1 (tools/vault.py:8 docstring vs MoveFile — dst never contained, src escape falls through), X1 (tools/vault.py:297–304 + reorg.py:32–52 vault escape via single-file move_file; the BATCH reorg path IS contained — `validate_operations` enforces `_path_inside_vault`, reorg.py:81–82,124–129 — do not re-file X1 for the batch path), A5 (cli.py:287,347 ConnectionError escapes the command handler — note agent_core/client.py streams break SILENTLY on EOF: chat :100–101, command_stream :148–149; only one-shot `command()` raises at :126), Q2 (discord_adapter.py:175 unguarded reply send — the only unwrapped Discord send in that file), Q3 (tools/reorg.py:290–292 Reorg exception path discards per-op state and skips rebuild_index/trigger_reindex after partial application — Medium), Q4 (tools/reorg.py:95-98 silent references_preview=0 fallback, no log — Low), Q5 (researcher.py:167,179 unguarded vault writes outside the fetch try — aborts the multi-topic run with no partial report; executor contains the error — Low), Q6 (tools/scratch.py:124 AddLearning git_commit outside the try — learning file written, commit escape loses the success JSON; whole-tree git add . self-heals — Low), W2 (prompts/system.py:37,44 propose_research approval wording is CLI-specific in the shared, adapter-agnostic prompt — Low; note PAL disables the builtin fetch_url/search_web via disabled_builtins at agent.py:161, so the prompt's "cannot browse URLs" claims ARE true), W3 (protocol.py:147-149 stale consumer comment on the Message union — Cosmetic), W4 (tools/__init__.py:2-3 docstring claims all tool subclasses were added across Phase F PRs 2-7 — but url_fix.py and promote_synthesis.py came from post-PR7 standalone untagged commits, 05-09/05-11; the per-PR list omits both files; no behavior impact — Cosmetic), W5 (README.md:167,195,196 env-var table lists wrong code defaults — 192.168.1.14:11434 vs 127.0.0.1:11434 for PAL_INFERENCE_URL/PAL_BATCH_INFERENCE_URL, gemma-4-E4B-it vs Qwen3.5-35B-A3B for PAL_BATCH_MODEL; the :179 searxng LAN-IP row IS the real code default — do not re-file any README table row), W6 (README.md:187,408 PAL_CHANNELS_DIR is a documented but silent no-op env var — no channels_dir field in BaseConfig/PALConfig; actual history at <vault>/_channels/pal/<channel_id>/history.jsonl — do not re-file). {add more as they land; list any relevant to this file}
- agent_core lives at /mnt/secondary/PAL/.venv/lib/python3.12/site-packages/agent_core/ — follow helper chains into it when judging cross-file behavior (grep + ranged reads only).

CRITICAL inventory artifacts (verified in earlier files):
- p2 part-B leads can be plain `await` statements mislabeled as task creation. Judge the `quote`, not the label.
- p4 over-labels `kind` (e.g. all 71 leads in pal_discord_interactions were "http"). Judge the quote, not the kind.
- p5 within-file boundary produces FALSE contradictions for cross-file claims: a claim may be correct because a helper elsewhere (incl. agent_core in the venv path above) makes it true. Before confirming ANY p5 contradiction, follow the call chain through helpers into agent_core. BOTH "contradicted" p5 verdicts in the inventory are now processed (discord_interactions ruled out as false W; pal_tools_vault p5:1 confirmed as true W → W1). Do not manufacture a contradiction; if this file's leads carry "contradicted", follow the full chain before reporting.

METHOD — for each lead:
1. Locate the cited line: grep -n a distinctive fragment of the quote, then read ONLY the cited line plus ~15 lines around it. NEVER read the whole file (same for agent_core files). Build context with several ranged reads/greps.
2. Judge the SUBSTANCE (only the passes present in this task):
   - p1: waits/timeouts with no bound or an absurd bound; blocking event-loop work; a timeout shorter than the operation it guards; on_expiry behavior that loses work silently.
   - p2 A: except clauses that swallow without logging, or convert errors into misleading successes/returns; bare/broad Exception hiding a real bug class.
   - p2 B/C: fire-and-forget create_task whose result is never awaited (silent exception loss, possible GC). Distinguish genuine problems from intentional fire-and-forget (a logging done-callback makes it intentional-and-safe; state why either way).
   - p3: does the state write matter for concurrency or for atomicity of a multi-step operation? Real-issue signals: read-modify-write with an await between read and write (lost update — each message is its own task); write to state read by another path with no synchronization; observable half-applied state. Constructor/__init__ assignments and purely local values are ruled out (say why).
   - p4: is the cited line a real external effect (network, file mutation outside the vault, shell exec, real git op, Discord API)? Is it guarded (try/except), best-effort, or does failure lose work / crash? Only a real, unguarded, consequence-bearing effect is a finding. Local object construction (zero-I/O), writes to the command's own output stream, vault-internal file writes, and guarded in-vault git ops are ruled out (say which).
   - p5: is the claim (docstring/comment) actually true of the code? Verdict "consistent" = no evidence either way — spot-check a sample to confirm the pass was not lazy; confirm any "contradicted" by full chain-following. Report contradicted-with-chain-followed as a W finding; contradicted-but-explained-by-cross-file-helper as ruled out (cite the helper).
   - Do NOT invent issues the lead does not point at; no style findings.
3. Classify each confirmed issue: A (async/concurrency), O (ordering/interleaving), Q (QoL/stability — not a bug), D (config/docs drift), S (startup/deploy/host), W (docs vs code), X (security). Severity: High / Medium / Low / Cosmetic.
4. If NOT a real issue, rule it out with a 5–15 word reason grounded in what the code actually does.

OUTPUT — write exactly one report file:
/mnt/secondary/projects/PAL/docs/audit/qwen/verified/{name}.{k}.verified.md
Format:
- Line 1: `# Verified: {name} {passes} (task {k}/{n})`
- One `## F<n> [cat][sev] <one-line title>` block per CONFIRMED finding (dedup: same line in multiple passes = one finding). 2–6 sentences of evidence with exact file:line refs and a short code quote, plus a `Leads:` line (e.g. `p1:120, p2-C:120`).
- `## Ruled out` — one line per lead or coherent group: `p1:<line> — <5-15 word reason>` (group when the reason is shared).
- `## Notes` — mislabeled leads, anything unverifiable, p5 verdict counts observed in this file.

RETURN (final message, under ~70 lines — the synthesis session works from this alone; if the report could not be written, include the FULL report here):
- `Report written: yes|no`
- `Confirmed:` one line per finding: `[cat][sev] <title> — <file:line>`
- `Ruled out: <N> leads (report has reasons)`
- `p5 verdicts seen: consistent=<n> contradicted=<n> cannot_tell=<n>` (p5 tasks only)
- `Quote/schema mismatches: <list or none>`
- `Unverifiable: <list or none>`

---

## Synthesis-side protocol (the dispatching session, not the subagent)

1. Dispatch foreground, one at a time. No parallel subagents, no other model request in flight (background CPU-only shell is fine — it doesn't touch the GPU).
2. Write is always denied on the subagent side → persist the returned report to the OUTPUT path yourself (add a note line that synthesis persisted it after spot-checks).
3. RE-ADJUDICATE before accepting:
   - Every CONFIRMED finding: verify with your own ranged reads. Two known subagent mis-rulings to watch for: (a) `writer.drain()` claimed to "block the event loop" (it's an awaitable — it suspends); (b) "exceptions from spawned tasks are caught by the daemon `_run_handler`" (they are not — `_run_handler` covers only the handler coroutine).
   - Ruled-out sample: spot-check ~3–6 leads (random p5 claims + any ruling that surprised you) against source.
4. Append confirmed findings to findings.md category tables (next free number per letter; numbers are provisional until the final dedup pass), add a Ruled-out line naming the report path, update the checklist line (progress note → `[x]` when all of the file's tasks are done).
5. Split rule: dispatch unit = one source file (all its pass files), split when >~70 leads.
6. p6a/p6b are analytical, not lead-verification: key-by-key drift tables, merge bare + PAL_-prefixed keys, confirm `PAL_INFERENCE_URL` in agent_core source; their summaries merge by hand.
7. `pal_tools_promote_synthesis` carries 8 p5 claims in the REJECTS file (`p5_claims__pal_tools_promote_synthesis.rejects.jsonl`, objects with `reason`/`raw`) — quote-gate artifact; verify them by hand in that file's task and note it in findings.md.
