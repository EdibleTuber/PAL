# Verified: pal_prompts_system p5 (task 1/1)

## F1 [W][Low] propose_research approval wording is CLI-specific in the shared, adapter-agnostic prompt

The prompt (pal/prompts/system.py:37) says propose_research "emits a CLI approval prompt" and "requires explicit user approval via the CLI prompt", and :44 says "This tool blocks until the user approves or declines in the CLI." The code is adapter-agnostic: research.py:125-134 emits `ResearchProposalMessage` via `ctx.emit(...)` and blocks on the bounded proposal wait; the CLI renders it as an approval prompt (cli.py:68-80 `format_research_proposal`, :363) while Discord renders it as an embed with approve/decline/edit buttons (discord_interactions.py:60 `build_research_proposal_embed`, :645, :682). PAL uses one shared `PAL_BASE_PROMPT` for all adapters, so in a Discord deployment the prompt text misdescribes the approval UX. Only the mechanism wording is wrong — the behavioral claims (blocks until the user responds; explicit approval, not text agreement; depth default 3, up to 10 if asked; "approved" → call research_topic, "declined" → don't) are accurate for both adapters (research.py:126-150). The LLM's own behavior is unaffected (it calls, blocks, and acts on the returned status); the risk is misdescribing the flow to a Discord user. Subagent filed this as [Medium]; synthesis downgrades to **Low** — UX-description drift only, no flow/data consequence.

Leads: p5:37, p5:44 (subagent's return listed "Leads: 7, 9" — misnumbered; 7/8/9 are the module docstring lines, not claims; corrected here.)

## Ruled out

p5:17 — search_vault JSON shape {status, query, count, results:[{path,name,summary,score}]} — claim true (subagent verbatim; its cite _framework.py:162-189 misattributed — search_vault is at _framework.py:87, shape at :88-91 and :148-167; synthesis re-verified).
p5:18 — scores are similarity, higher = more relevant (subagent verbatim; synthesis: `round(float(score), 3)` at _framework.py:166, similarity from retrieval search).
p5:19 — `…`-terminated summaries are truncated; call cat for full article — `_truncate` appends "…" only when cut (_framework.py:27-44) and is applied at :165.
p5:20 — head/tail first/last N lines, read_lines 1-indexed range, find filename glob — _shell.py: head :75-95 ("Read the first N lines", default 20), find :277-300 ("Filename glob search"), tail :102 / read_lines :329 same module.
p5:31 — wait_for_reindex polls until done or timeout; "finishes within a second or two" — wait.py:15-60: poll loop exits on status done/error or deadline (default 30 s, capped 120 s); the timing wording is verbatim the tool's own description — consistent, empirical (in-process reindex job).
p5:34 — update_scratch ≤2 KB, per-channel, auto-included in system prompt — scratch.py:47 "must be 2048 bytes or less"; builder.render_scratchpad(channel_id) (agent_core/prompts/builder.py:48) appends it per channel.
p5:38 — "Fails if the proposal is not approved, already used, or expired" — accurate user-facing paraphrase: research.py:193-203 rejects pending ("not approved yet"), declined, expired, and consumed whose error string literally reads "proposal was already used". Subagent's "F2" finding block is self-contradictory (its own text: "not a behavioral contradiction") — discounted.
p5:46 — "approved" → call research_topic immediately — policy instruction; premise verified (research.py:140-141 returns `{"status": final.status}`; edited-successor case returns "approved" with the edited id, :142-150).
p5:47 — "declined" → do not call, ask user — policy instruction; premise verified (research.py:141, :196-197).
p5:49 — inline citation to the specific summary file — policy instruction; premise verified (researcher.py:3 "fetches top results, summarizes each" into raw/summaries).
p5:73 — must not claim unperformed tool actions — policy instruction, no code to contradict.
p5:79 — cannot browse arbitrary URLs; web content only via research_topic/SearxNG — TRUE for PAL: `fetch_url` is a builtin (agent_core/tools/builtin.py:18-21, defined _framework.py:50) but PAL disables it — `disabled_builtins = frozenset({"fetch_url", "search_web"})` (pal/agent.py:161, rationale comment :153-160); researcher fetches only SearxNG-chosen URLs (researcher.py:3,103).
p5:80 — no direct arXiv/GitHub/StackOverflow access; use propose_research — same disabled_builtins premise + SearxNG (researcher.py:103).
p5:81 — no arbitrary code/shell beyond registered vault read tools — builtin set (builtin.py:18-21) contains no shell/exec tool; PAL tool set (agent.py:135-143) is all vault/research/compile/reorg.
p5:82 — no DB/REST/other services beyond SearxNG + inference — fetch_url/search_web disabled (agent.py:161); only network egress is the researcher (SearxNG) and the InferenceClient.
p5:83 — cannot read outside the vault; read tools scoped to vault root — _shell.py:39-41 `resolve_safe(vault_path, path)` → "Error: path escapes outside vault".
p5:84 — cannot write to system dirs (leading underscore) — underscore convention established across wiki/reorg/backfill (`part.startswith("_")`).
p5:85 — cannot send email/post/contact anyone outside the conversation — no such tools in the registered set (agent.py:135-143 + builtin.py:18-21).
p5:86 — no hidden long-term memory beyond vault + profile + wisdom — prompt sections are exactly profile/wisdom/scratchpad (builder render helpers; builder.py:48 region).
p5:87 — cannot schedule/timers/background tasks — no scheduling tool registered; the daemon's internal scanner tasks (cf. A4) are not agent-invocable tools.
p5:88 — cannot modify its own prompt/tools/config — no tool writes prompt/config; vault writes are scoped to the vault, prompt/config live in the repo.
p5:92 — no announce-then-fake — policy instruction, no code to contradict.
p5:94 — explain failures in plain language, no silent retry >1 — policy instruction, no code to contradict.
p5:96 — report outcomes only from observed results — policy instruction, no code to contradict.
p5:97 — after batch tools, read the structured report first — policy instruction; premise verified (compile_batch/reorg/consolidate are the registered batch tools, agent.py:135-143).
p5:98 — search_vault/grep before answering from memory — policy instruction; both tools exist (builtin.py:18-21).
p5:99 — `> _Source: chat-derived synthesis` marks chat-derived articles — exact match: CHAT_BANNER_SENTINEL = "> _Source: chat-derived synthesis" (pal/compiler.py:29).
p5:100 — write tools return {status, path, reindex}; reindex null or job dict — vault.py:138-139 `{"status": "updated", "path": path, "reindex": reindex}`; `_maybe_reindex` (vault.py:27-42) returns the trigger_reindex job dict (job_id consumed by wait.py) or None on failure.

## Notes

- p5 verdict counts: all 30 leads arrived as cannot_tell_from_this_file (by construction — this file is prompt text; every claim is about another module). Resolution: 28 resolved TRUE (factual claims verified against code, or policy instructions with premises checked), 2 resolved FALSE-ish (p5:37/:44 — CLI-specific mechanism wording in a shared prompt) → F1.
- Subagent return was truncated mid-Ruled-out at the p5:19 line; p5:19 (completed) through p5:100, this Notes section, and the Synthesis adjudication are synthesis work. p5:17/18 lines are subagent-verbatim with the cite correction noted.
- Quote/schema mismatches: (1) F1's "Leads: 7, 9" and F2's "Leads: 8" do not correspond to inventory claim lines (claims start at line 17) — corrected to 37/44 and 38; (2) the _framework.py:162-189 / :176-183 cites for p5:17/18 point at run()'s body and search_web — actual cites :87-91, :148-167, :166.
- Unverifiable: none. The "finishes within a second or two" timing (p5:31) is empirical but is the tool's own documented wording — not a contradiction.
- Subagent's write tool was denied; report persisted by synthesis.

## Synthesis adjudication (2026-11-10)

- F1 CONFIRMED, severity re-adjudicated [Medium]→**[Low]**, recorded as W2. Own ranged reads: prompt :37/:44 (exact wording above); research.py:110-150 (emit + bounded wait + status result incl. edited-successor→"approved"); cli.py:68-80, :363 (CLI approval-prompt rendering); discord_interactions.py:60, :645, :682 (Discord button flow). Behavior identical across adapters; only the "CLI" mechanism wording drifts. Low.
- F2 DISCOUNTED (not a finding): subagent's block concedes "not a behavioral contradiction"; the prompt's "already used" is the code's own error string (research.py:201) and the three stated failure conditions map 1:1 onto the code's rejections (pending/declined ⊂ "not approved", consumed = "already used", expired).
- Critical spot-check the subagent's truncated report cannot show — p5:79/82: BUILTIN_TOOLS (agent_core/tools/builtin.py:18-21) DOES include FetchUrl and SearchWeb; PAL disables both via `disabled_builtins` (pal/agent.py:161) with a design comment ("all web fetching goes through the consent-gated research flow"). Verified by synthesis — claims TRUE for this deployment. Had PAL not disabled them, this would have been the file's only substantive W.
- p5:31 verified at wait.py:15-60 (bounded poll, default 30 s / cap 120 s); p5:34 at scratch.py:47 + builder.py:48; p5:99 at compiler.py:29 (exact string); p5:100 at vault.py:27-42,138-139; p5:83 at _shell.py:39-41; p5:20 at _shell.py:75-95,277-300.
- Verdict: 1 finding (W2, Low), 29/30 ruled out.
