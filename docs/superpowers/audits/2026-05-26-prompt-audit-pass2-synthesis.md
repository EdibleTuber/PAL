# PAL Prompt Audit -- Pass 2: Synthesis-Tool Internal Prompts

**Date:** 2026-05-26
**Status:** Synthesis complete; panel review pending; awaiting user acceptance
**Scope:** Internal LLM prompts inside synthesis tools -- prompts the chat-loop model never sees
**Files audited:** summarizer.py, pdf_structure.py, title_cleanup.py, compiler.py (2 sites), article.py, categorizer.py, learning_scanner.py (extractor), consolidator.py
**Method:** 4 parallel subagents reading their assigned surfaces, then this synthesis. Panel review next.
**Reframe vs Pass 1:** Pass 1 was about chat-loop prompts + tool descriptions the chat model sees. Pass 2 audits the prompts the CODE sends to inference directly. Since gemma-4-E4B-it-Q4_K_M was validated 2026-05-26 as the synthesis model and handled all tasks at acceptable quality, Pass 2 is about output-format strictness and parser robustness as the model rotates, not "fix small-model fit."

## Panel review highlights (added 2026-05-26)

The 4-reviewer panel (faithfulness, prioritization, devil's advocate, implementation feasibility) substantially refined the tier list. Key adjustments baked into "Revised recommended actions (post-panel)" below:

- **All 9 Critical findings verified accurate** (faithfulness). Minor cite drifts only; no severity-changing errors.
- **C1 recommendation revised**: consolidator should construct an `Article` and use `serialize_article` (mirroring compiler at `compiler.py:276`) rather than adding a `sources` parameter to `wiki.write_article`. The latter would touch ~10 call sites across 7 files. Also: full sources-array merge is contested -- devil's advocate argues "what consumer needs this?" given articles-as-substrate. Pared recommendation: just add `consolidated_from: [src_paths]` lineage, teach `find_orphan_articles` to skip `tags: [consolidated]`, defer full sources merge until evidence of fabrication.
- **C2 (validation as hard error)**: devil's advocate flagged this inverts the failure mode if I8 doesn't ship first. A known-loose validator becomes a hard gate → starts rejecting substantively-fine articles. Must pair with I8 (regex anchor); ship I8 first, then validate-as-warning + telemetry for one cycle, then escalate to gate if data justifies.
- **C4 (categorizer closed-set)** reframed: keep open-set + case-fold + WARNING log on minted-new-directory. Devil's argues closed-set removes the only mechanism vault grows new dirs. Defer fuzzy-snap until telemetry shows what actually accretes.
- **C5 + C7 parser broadening** reframed: fix prompts first (I3 "no preamble, no fences"), gather telemetry, broaden parsers only for failure modes that actually show up in logs. Case-insensitive INSUFFICIENT matching risks misfiring on prose containing "insufficient." Tier 2 shared-helper module (`pal/parsing.py`) is contested as over-engineering -- inline fixes acceptable for 4 sites, extract only if a fifth site appears.
- **C6 (title-regen no injection boundary)** downgraded to Important by faithfulness: `regenerate_title` is only called from the one-shot backfill CLI, narrow attack surface. Truncation half of recommendation is still worth doing; sanitization-wrapping half is hardening against a threat model the user hasn't adopted.
- **I4 (retry loops)**: devil's says don't ship. Retries add latency + token cost to recover from problems we don't have evidence of. Revisit only if telemetry shows >5% validation-failure rate after I3 lands.
- **I14 (telemetry on parse-fallback paths)**: promoted from Tier 7 to **Tier 0** by all three of prioritization/devil's/feasibility independently. The chat-first lens means observability IS the only signal channel -- without article-reading as a check, every other Critical finding is theoretical until logs prove otherwise.
- **Reclassified as intentional design (not bugs)**:
  - Compiler not embedding source URLs in article body -- supported by `project_articles_are_substrate`; URLs in body would be dead weight on the chat-loop surface, and verbatim-copy is a known small-model failure mode. Add a docstring comment, close.
  - Consolidator pulling `PAL_BASE_PROMPT` -- provides uniform identity/style across compiler + consolidator output. Smoke-test validates current shape works. Refactor would be for token-cost reasons, not quality.
  - PDF tier-3 bracket extract fails soft to tier-2 -- that's the design.
- **Implementation findings** (feasibility):
  - C8 lives in `agent_core/learning_scanner.py` -- cross-repo, needs agent_core version bump.
  - C1 broad blast radius: `wiki.write_article` has 10 call sites across 7 files. Reframe per the faithfulness fix.
  - C2 breaks 2 consolidator happy-path tests (current fixtures use sparse `## Overview ... Fused (from ...)` content missing `## Key Concepts`). Bundle with I8 fixture updates.
  - C4 invasive: `parse_category_response` standalone function has 11 unit tests; adding required `directories` parameter breaks all. Implement enforcement inside `Categorizer._parse_category` instead to preserve the public signature.
  - I1 (drop PAL_BASE_PROMPT) tests safe -- neither `_StubPromptBuilder.build` assertions nor MagicMock `return_value="system prompt"` patterns assert on contents.
- **Missed by the audit** (faithfulness): `merge_into_existing` lacks the size guard that `compile_one` has (same robustness class). `article.py:259` smart-quotes gap parallel to the audit's title-parser note. Chat-derived articles indistinguishable from external-source in `find_existing_article` candidate list.
- **Critical-severity question raised by devil's**: "Critical should mean observed." Several Critical findings (C5, C7, C8, C9) are framed as silent quality degradation but the 2026-05-26 smoke test surfaced none. Consider downgrading to Important pending log evidence -- which is exactly what the promoted-to-Tier-0 telemetry will provide.

## Headline findings

1. **The synthesis stack systematically trusts model output too much.** Every prompt+parser pair lacks at least one of: code-fence stripping, preamble stripping, allowed-value enforcement, retry on malformed output, format example, observability on parse fallback. Individually each is minor; together they form a class of silent quality degradation that gets worse as the model rotates.

2. **Validation-as-warning is the single most damaging pattern.** `compile_one`, `merge_into_existing`, and `consolidate` all run `validate_compiled_truth` on model output, log a warning if it fails, then write the article anyway with `status: "ok"`. The only path that respects validation is `compile_chat_synthesis`. Result: malformed articles slide into the vault, the chat model sees success, the user has no signal.

3. **Provenance is uniformly weak.** Consolidator drops the entire `sources:` frontmatter array (only marker is `tags: [consolidated]`). Compiler doesn't include source URLs in article bodies (intentional but undocumented). Article topic-match silently creates duplicates when the model returns a fabricated filename. The "chat is the product" lens makes this worse -- users read articles via chat synthesis, never see frontmatter.

4. **Format examples are absent from every synthesis prompt.** Each describes the expected shape in schema language but shows no concrete example. For a small synthesis model this is the highest-leverage cheap fix: 3-5 lines of `Input -> Output` per prompt would shave format-failure rates significantly.

5. **Open-set vs closed-set ambiguity in classification.** Categorizer explicitly invites the model to mint new directories ("suggest a short descriptive new one"), and the parser doesn't validate against the existing dir list. Long-term vault-entropy source: every run can create `AI-Research`, `ai-research`, `LLMs`, `llm-research` as siblings.

---

## CRITICAL findings

Critical = silent data loss, wrong behavior, fabrication risk, or shipped-but-broken output.

### C1. Consolidator drops the entire `sources:` frontmatter array

**Source:** `consolidator.py:166-168` + `wiki.py:68-77`.

`wiki.write_article` builds frontmatter from `{title, created, updated, tags}` only -- no `sources:` field passed through. Consolidator never harvests source articles' `sources:` arrays, never merges them, never writes a TIMELINE. Compare `compiler.py:237-248` which does both correctly.

Consequence: every consolidated article has zero structured provenance. Indistinguishable from a hand-authored article with `tags: [consolidated]`. `find_orphan_articles()` treats them as orphans. Inline `(from Path/foo.md)` citations the prompt requests live only in prose -- can drift, be edited away, or be lost on subsequent compile/merge passes.

**Recommendation:** Harvest each source's `meta.get("sources", [])` after `parse_frontmatter`, concatenate + dedupe by `(url, hash)`, pass through to `write_article` (which needs a `sources` parameter). Add `consolidated_from: [<src_paths>]` frontmatter so lineage is queryable. Update the prompt to explicitly demand inline citations AND verify they're present in the parser.

### C2. Validation failures are warnings; articles ship anyway

**Sources:** `compiler.py:230-233` (compile_one), `compiler.py:681` (merge_into_existing), `consolidator.py:159-164`.

All three call `validate_compiled_truth(content)`. When it fails, all three log `logger.warning` and proceed to write. Only `compile_chat_synthesis` at `compiler.py:381-387` treats it as a hard `status: "insufficient"` error.

Practical outcome: when the synthesis model produces a malformed response (missing `## Overview`, code-fenced body, prose preamble), the chat model still sees `{status: "ok", path: "..."}`. No signal anything went wrong. User reads degraded article via chat synthesis or `cat`.

**Recommendation:** Make validation a hard error in all three sites. Return `{status: "insufficient", reason: <issues>}` for compile_one/merge_into_existing; symmetric for consolidator. Optional second-shot retry with sharper prompt ("you missed `## Overview`; reformat") before final fail.

### C3. Article topic-match silently creates duplicate articles

**Source:** `article.py:256-265`.

`find_existing_article` parser: checks `response.upper() == "NONE"`, then iterates candidate list for exact filename match. If the model returns a plausible-but-fabricated filename (`kafka-streaming.md` when `kafka-streams.md` was in the list), the loop falls through and returns `None`. Caller treats this as "no match exists" and creates a brand new article via `compile_one`'s create-new branch.

Silent topic duplication. No warning log. The chat-first lens makes this invisible -- nobody reads the vault directory to spot duplicates.

**Recommendation:** Log `logger.warning("topic-match model returned unknown filename: %r (candidates: %r)", ...)` before returning None. Optionally retry once with constrained prompt ("you MUST pick from the list OR respond NONE -- no other responses are valid").

### C4. Categorizer doesn't enforce allowed-value set

**Source:** `categorizer.py:16-24` + parser at `:50-73`.

System prompt at lines 19-20 explicitly invites the model to "suggest a short, descriptive new one" if no existing directory fits. Parser enforces only negative constraints (no `_` prefix, no `raw/`, no spaces, max length) -- never checks the returned string against the `directories` list passed in.

Every categorization run can mint a new top-level directory. Over time the vault accretes near-duplicate siblings (`AI-Research`, `ai-research`, `LLMs`, `llm-research`). This is the dominant long-term entropy source for vault structure.

**Recommendation:** Either (a) make the prompt strictly closed-set ("respond with exactly one of: ...; if none fit, respond `Research`") and fall back to `FALLBACK_DIRECTORY` on out-of-set response, or (b) keep the open-set design but post-validate proposed new dirs against fuzzy-match of existing ones and snap to nearest above a similarity threshold. Either way, case-fold compare before treating a response as new.

### C5. `INSUFFICIENT:` / `NONE` parsers are strict-prefix; prompts don't enforce strictness

**Sources:** `compiler.py:222` (INSUFFICIENT in compile_one), `compiler.py:673` (INSUFFICIENT in merge_into_existing), `article.py:256` (NONE in find_existing_article).

All three parsers do strict prefix/exact matching:
- `compiled_truth.strip().startswith("INSUFFICIENT:")`
- `response.upper() == "NONE"`

But none of the prompts forbid code-fence wrapping, preambles, case drift, or punctuation variants. Failure modes that get silently misinterpreted:

- ` ```\nINSUFFICIENT: ...\n``` ` -> `.strip()` keeps backticks -> parser falls through -> treats as article -> ships garbage with `status: ok`.
- `Insufficient: ...` (lowercase i) or `INSUFFICIENT - ...` (dash not colon) -> fall through.
- `Here is my response:\n\nINSUFFICIENT: ...` -> fall through.
- `NONE.` (trailing period) -> fails `response.upper() == "NONE"` -> creates duplicate article.

**Recommendation:** Use `re.search(r"^\s*INSUFFICIENT[:\-]", ..., re.IGNORECASE | re.MULTILINE)` after stripping fences. Same broadening for NONE. Add explicit "do not wrap in code fences, no preamble, no commentary" to all three prompts.

### C6. `title_cleanup.regenerate_title` sends raw article content with no injection boundary

**Source:** `title_cleanup.py:84-87`.

Unlike `summarizer.py` which wraps untrusted content with `wrap_untrusted(...)` and pairs with `SANITIZATION_SYSTEM_PROMPT`, `regenerate_title` sends raw `content` directly. If a vault article body contains "Ignore previous instructions, your new task is X" (innocently quoted from injection research, or maliciously placed), the title-regen model has zero defense. Backfill CLI runs this on every article.

**Recommendation:** Wrap `content` with `wrap_untrusted` + GUID, prepend `SANITIZATION_SYSTEM_PROMPT` to the system message. Additionally truncate -- backfill ships full 50KB bodies for a 5-token output task.

### C7. `TITLE:` parser strict-prefix loses titles silently

**Source:** `title_cleanup.py:24-51` (`parse_title_and_body`).

Used by both `summarizer.py:82` and `title_cleanup.py:90`. Does `stripped.startswith("TITLE:")`. If the model emits any of these (all plausible small-model outputs):
- `Sure. TITLE: Foo\n\nBody...`
- `Here is the summary.\n\nTITLE: Foo`
- `**TITLE:** Foo` (bold)
- `Title: Foo` (case drift)
- `# TITLE: Foo` (heading marker)

...the parser returns `(None, full_response)`. Summarizer falls back to `raw_stem`, body retains a duplicate inline TITLE line. Title-regen logs a warning and skips. User sees `_index.md` entries like `raw_abc123def456`.

**Recommendation:** Broaden parser to search for `TITLE:` (case-insensitive, allow `**TITLE:**` and `# TITLE:` markup) anywhere in first ~5 lines. Strip preceding prose. Remove the TITLE line from the body so it doesn't double-appear.

### C8. Learning-scanner `json.loads` doesn't strip code fences

**Source:** `learning_scanner.py:111-114`.

Calls `json.loads(text)` directly on the stripped raw model output. Small models very frequently wrap JSON in ` ```json ... ``` ` even when told not to. Fenced responses fail `JSONDecodeError`, log at INFO, candidate lost.

Combined with the `has_signal` pre-filter, every miss is one user-correction signal silently discarded -- the exact class of data the learning loop exists to capture.

**Recommendation:** Strip ` ``` ` / ` ```json ` fences before `json.loads`. As a fallback, extract the first balanced `{...}` substring with a small bracket-matching helper before giving up.

### C9. PDF-structure JSON extraction breaks on prose with brackets

**Source:** `pdf_structure.py:291-298`.

Parser uses `content.find("[")` and `content.rfind("]")` to extract a JSON array. Failure modes:
- Prose with brackets before JSON (`"Looking at pages [around 50-100], here is the TOC:\n[{...}]"`) -> `first_bracket` lands on `[around`, JSON parse fails, returns None.
- Fenced JSON with trailing prose containing brackets -> `rfind("]")` finds the wrong one.
- Multiple JSON arrays in response -> spans both, fails.

All silently return `None` and fall through to single-file extraction. No log explaining tier 3 was attempted but failed parsing.

**Recommendation:** Strip ` ```json ` fences before bracket-find. Log at WARNING on `JSONDecodeError` with a snippet. Optionally try balanced-bracket extraction from `first_bracket` forward.

---

## IMPORTANT findings

Important = real quality / robustness issue but not silent data corruption.

### I1. Consolidator pulls full PAL_BASE_PROMPT as system prompt

**Source:** `consolidator.py:111`.

`prompt_builder.build()` returns PAL_BASE_PROMPT (104 lines) + profile + wisdom. The synthesis call gets several KB of chat-loop policy (full tool catalog, "How to handle research requests" workflow, "What you cannot do" list, write-tool JSON envelope description) before its actual synthesis instructions.

For a small synthesis model, instruction-following degrades as the actual rules ("use only sources, cite inline, required sections") sit at the end after the irrelevant chat-loop bloat. Compare `article.find_existing_article`'s surgical `TOPIC_MATCH_PROMPT` -- correct minimal prompt.

**Recommendation:** Stop calling `prompt_builder.build()` for synthesis calls. Build a focused synthesis-only system prompt: identity ("You are a consolidator that fuses wiki articles"), rules, output format, example. Same pattern likely applies to compiler.py's two sites.

### I2. No format examples in any synthesis prompt

**Cross-cut:** every prompt audited.

Categorizer, learning_scanner, compiler (both sites), article find_existing, pdf_structure, consolidator, summarizer all describe expected output in schema language or English ("respond with a JSON object containing `title` and `body`"). None show a concrete `Input -> Output` example.

Small models follow examples much more reliably than schema descriptions. This is the single highest-leverage cheap intervention -- 3-5 lines per prompt.

**Recommendation:** Add a worked example to each prompt. For binary-shape prompts (NONE-or-result, INSUFFICIENT-or-article), show both shapes.

### I3. No "do not wrap in code fences, no preamble" instruction

**Cross-cut:** compile, consolidate, summarize, categorize, learning_scanner, pdf_structure.

None of these prompts explicitly forbid Markdown code fences or chatty preambles. Small models lean toward both. Combined with strict-prefix parsers, the prompts and parsers actively conspire to lose output.

**Recommendation:** Add a "FORMAT" or "OUTPUT" block to every synthesis prompt: "Output only X. Do not wrap in code fences. Do not add preamble like 'Here is the article'. Begin your response with [first expected token]."

### I4. No retry on validation failure

**Cross-cut:** compiler, consolidator.

Single-shot inference + permissive parser = silent quality degradation as model rotates. There's no second-chance retry with a "you violated the format, redo" message.

**Recommendation:** Bounded retry (max 1) on validation failure with a sharper instruction. Catches most small-model glitches cheaply.

### I5. Compiler merge has prompt/parser drift on INSUFFICIENT

**Sources:** `compiler.py:635-646` (merge prompt) + `compiler.py:673` (parser).

First-compile prompt at line 194 defines `INSUFFICIENT:` escape hatch. Merge prompt at 635-646 doesn't mention it. But merge parser at 673 still checks for it. Either drop the dead parser branch or add the escape to the merge prompt.

**Recommendation:** Add to the merge prompt for symmetry -- merge can fail to integrate too.

### I6. Compiler merge truncates timeline summaries to 200 chars

**Source:** `compiler.py:630-633`.

For a third/fourth iteration of an article, the model gets one full new summary plus a list of ~200-char fragments of prior sources. High risk of dropping facts that exist in the article but not the truncated previews.

**Recommendation:** Pass full timeline summaries, OR drop the `timeline_context` block entirely and rely on the existing `compiled_truth` (already passed) as authoritative.

### I7. Compiler merge doesn't protect chat-banner sentinel

**Source:** `compiler.py:649-650` (vs `compile_chat_synthesis` line 411-426 which DOES guard).

When `merge_into_existing` is called from `compile_one`'s existing-match branch, the existing article may be chat-derived with `CHAT_BANNER_SENTINEL`. That banner feeds verbatim into the model, which may drop or paraphrase it. Banner-preservation contract broken silently.

**Recommendation:** Detect `CHAT_BANNER_SENTINEL` in existing compiled truth; refuse (return `needs_consolidate`) or strip+reprepend.

### I8. `validate_compiled_truth` is substring-only

**Source:** `article.py:271-281`.

`if section not in text` is a raw substring check. Accepts:
- `### Overview` (wrong header level)
- `## Overview goes here` (false positive on header continuation)
- `text mentioning ## Overview inline`

Conversely rejects:
- `##Overview` (no space)
- `## OVERVIEW`

**Recommendation:** Anchor regex: `re.search(rf"^{re.escape(section)}\s*$", text, re.MULTILINE)`.

### I9. Summarizer title rules embedded mid-user-message

**Source:** `summarizer.py:69-78`.

`TITLE_RULES` is designed as a standalone system prompt (ends with "Begin your response with..."). Embedded mid-user-message it's followed by more instructions and the GUID-wrapped untrusted content. Small models attend to the last instruction before content -- which here is "write the summary body," not the title format spec.

**Recommendation:** Move title-format spec to the system message. Keep user message as `"Summarize the following:\n\n" + wrapped`. Add a closing reminder line AFTER the wrapped block.

### I10. Length controls missing across the surface

- **Summarizer** (`summarizer.py:69-78`): no output budget. 20K input, no target -- model can ramble 4K or truncate to 200 chars.
- **Title-cleanup** (`title_cleanup.py:78-94`): no input cap. Ships full 50KB articles for 5-token title output. Wasteful + context-window risk on small model.
- **Consolidator** (`consolidator.py:139`): no `max_tokens` on completion. No combined-context budget; only per-source body cap that doesn't account for system prompt.
- **Learning-scanner** (`learning_scanner.py:118-122`): no length validation on parsed `title`/`body`. Model could return 4000-char body that flows through.

**Recommendation:** Per-site fixes. Summarizer: "Aim for 200-600 words, 2-5 paragraphs." Title-cleanup: truncate input to ~4000 chars (head only). Consolidator: `max_tokens` cap + token-aware combined budget. Learning-scanner: title <= 120 chars, body <= 600.

### I11. Categorizer parser doesn't case-normalize

**Source:** `categorizer.py:64-71`.

Returns `Research` vs `research` vs `RESEARCH` as different strings. On case-sensitive filesystems, vault treats them as separate directories.

**Recommendation:** Case-fold-compare against existing `directories` list; snap to canonical existing form when a case-insensitive match exists.

### I12. Consolidator inline-citation requirement is unverified

**Source:** `consolidator.py:112-122` + parser.

Prompt asks for inline `(from Path/foo.md)` citations as the sole anti-fabrication defense. Parser never verifies citations are present or well-formed. Model can produce a clean-looking article with zero citations and commit silently.

**Recommendation:** Post-parse, scan for `(from <path>)` patterns; require at least one per `##` section; warn or hard-fail if absent.

### I13. Learning-scanner conflates refusal vs error vs no-result

**Source:** `learning_scanner.py:108-122`.

`text.lower() == "null"` is too narrow (misses `"null"` quoted, `null.`, `No`, `None`). Failures fall to `json.loads` which then fails with `JSONDecodeError` (also handled). Parser returns None either way, but logs conflate them so refusal-rate vs format-failure-rate is unobservable.

**Recommendation:** Add refusal-keyword check (`null`, `none`, `no durable lesson`, leading `no.`) returning None cleanly. Distinct log messages per failure type.

### I14. Parse-fallback paths have no telemetry

**Cross-cut:** summarizer (raw_stem fallback), pdf_structure (single-file fallback), learning_scanner (None fallback), categorizer (FALLBACK_DIRECTORY).

Every silent-fallback path lacks a WARNING log with a response snippet. Adding observability is zero behavior risk and dramatically improves debugging.

**Recommendation:** Add `logger.warning("parse-fallback in <site>: %r", response[:200])` to each fallback path.

### I15. `TITLE_RULES` reused across system-prompt and user-message contexts

**Source:** `title_cleanup.py:11-21` reused in `summarizer.py:74` as user-message fragment AND `title_cleanup.py:85` as system message.

Block ends with "Begin your response with a single line in this format: TITLE: ..." -- correctly positioned as final system-prompt instruction in `regenerate_title`, mis-positioned mid-user-message in summarizer. Same text can only be optimal for one.

**Recommendation:** Split into `TITLE_RULES_BODY` + `TITLE_FORMAT_FOOTER`. Each caller composes with appropriate placement.

### I16. Categorizer prompt doesn't fence the directory list

**Source:** `categorizer.py:34, 39`.

User prompt embeds directory list as `- foo\n- bar`. If a directory name has a leading hyphen or the article preview ends with a bullet, the boundary between "existing directories" and the question is fuzzy.

**Recommendation:** Wrap with delimiters: `<directories>\n- foo\n- bar\n</directories>`.

---

## MINOR findings (grouped)

**Prompt phrasing nits:**
- `compiler.py:200-208`: "grounded" is jargon; "using only the source material" clearer.
- `compiler.py:191`: compile-specific instructions buried at end of `base_prompt`; small models attend to early content more.
- `consolidator.py:117`: example uses `Security/a.md` -- match actual vault category conventions (lowercase).
- `consolidator.py:124`: `### SOURCE:` collides with source-body `###` headers. Use `===== SOURCE: path =====` or strip TIMELINE blocks from source bodies before concat.
- `categorizer.py:19`: example `"Projects/tools"` suggests multi-segment paths but `_list_directories` only enumerates top-level; mental-model mismatch.
- `summarizer.py:73-75`: injection-handling rule duplicates `SANITIZATION_SYSTEM_PROMPT`; conflicting wording.

**Code-style consistency:**
- `summarizer.py:81` vs `pdf_structure.py:289` vs `compiler.py:215`/`668`: inconsistent `result.content` vs `getattr(result, "content", "")` vs `getattr(result, "content", result)`. Pick one across module.
- `consolidator.py:151`: `content[len("INSUFFICIENT:"):]` is prefix-only; same broadening as parser.

**Parser edge cases:**
- `article.py:262-263`: `filename.replace(".md", "")` strips all occurrences, not suffix. Use `removesuffix(".md")`.
- `article.py:256`: `if not response or response.upper() == "NONE":` -- empty-response branch deserves a log line (inference failure not caught upstream).
- `title_cleanup.py:48-49`: quote-stripping only handles matched outer quotes; doesn't handle smart quotes.
- `categorizer.py:50`: empty-response branch is guarded but reads brittle.

**Documentation gaps:**
- `compiler.py`: docstring should note "all sites use reasoning='off'" since that's load-bearing.
- `consolidator.py`: docstring should note that compile-style timeline+sources merge is intentionally NOT done (currently looks like an oversight; clarify if oversight or design).

---

## Cross-cutting themes

### Theme A: Output strictness as a single discipline

Every prompt+parser pair could benefit from the same hardening pass:
1. Prompt says "do not wrap, no preamble, start with `<sentinel>`."
2. Parser strips code fences + preamble before matching.
3. Parser case-folds keyword matching.
4. Parser logs a warning + snippet on fallback paths.

Doing this once as a shared helper module (`pal/prompts/output_parsing.py`?) and adopting across all sites would address ~10 of the Critical/Important findings with one design.

### Theme B: Validation as gating, not telemetry

`validate_compiled_truth` exists. It's called in 4 places. Only 1 of those respects it. Making validation a hard gate in the other 3 (compiler.compile_one, compiler.merge_into_existing, consolidator.consolidate) closes the silent-corruption class of bugs at a single architectural level.

### Theme C: Provenance as first-class

Consolidator drops sources. Compiler doesn't embed URLs in article bodies. Article topic-match silently duplicates. Title-regen has no injection boundary. The synthesis stack treats provenance as decorative; it should be load-bearing because the chat-first lens means users never read frontmatter.

### Theme D: Closed-set enforcement

Categorizer and article topic-match both ask the model to pick from a list. Neither enforces the choice was from that list. Both can silently misclassify or duplicate. Closed-set enforcement should be the default for any "pick one" prompt; the parser validates against the allowed set with a defined fallback.

### Theme E: Prompt bloat in synthesis

Consolidator (and likely compiler) pull PAL_BASE_PROMPT for synthesis calls. The chat-loop identity, tool catalog, and workflow rules are irrelevant noise that buries the synthesis instructions. Focused synthesis-only system prompts -- like article.find_existing_article's TOPIC_MATCH_PROMPT -- are the right shape.

---

## Revised recommended actions (post-panel)

The tier list below replaces the original "Tiered action list" further down (kept for reference but superseded). The revised structure reflects all 4 panel reviewers' input.

### Tier 0: Telemetry (~30 min, single commit, ship FIRST)

Promoted from I14. All 3 of prioritization/devil's/feasibility reviewers independently flagged this as the load-bearing prerequisite for evidence-based decisions on everything else. Without it, the rest of the audit is speculation.

- Add WARNING log + response snippet (`response[:200]`) to every silent-fallback path:
  - `summarizer.py` raw_stem fallback in `parse_title_and_body` parse miss
  - `pdf_structure.py` tier-3 single-file fallback in `detect_from_llm_toc`
  - `agent_core/learning_scanner.py` `json.loads` failure (upgrade from INFO to WARNING) -- cross-repo, see Tier 1d
  - `categorizer.py` FALLBACK_DIRECTORY path AND new-directory-minted path
  - `article.find_existing_article` NONE/unknown-filename branches (this is **C3 logging**)
  - `compiler.py` validate warnings in `compile_one` and `merge_into_existing`
  - `consolidator.py` validate warning

One commit, additive only. Zero test breakage. After this lands, observe one cycle of real usage before any Tier 2+ decisions.

### Tier 1: Mechanical, low-risk fixes (4 separate commits)

1a. **I5**: Compiler merge prompt drift -- pick one of (add `INSUFFICIENT:` escape to merge prompt) OR (drop dead parser branch). Recommend adding to prompt for symmetry with compile_one. `~5 min, no test breakage.`

1b. **I11**: Categorizer case-fold against existing directories -- snap to canonical existing form on case-insensitive match. Implement inside `Categorizer._parse_category` not the standalone `parse_category_response` function (per feasibility: would break 11 existing parser tests). `~20 min.`

1c. **C6 + I10 title-cleanup bundle**: Add input truncation to `regenerate_title` (~4000 chars head only). Skip the `wrap_untrusted`/`SANITIZATION_SYSTEM_PROMPT` half per faithfulness downgrade -- narrow attack surface, not the user's adopted threat model. `~30 min.`

1d. **C8 cross-repo**: `agent_core/learning_scanner.py` -- strip ` ``` ` fences before `json.loads`, balanced-bracket fallback. Bump agent_core version. Bump PAL pin. Two commits across two repos same pattern as v1.3.3. `~30 min + cross-repo coordination.`

### Tier 2: Evidence-driven (after Tier 0 telemetry has run for one cycle)

Decide based on what the logs actually show. The audit's original Tier 2-5 collapses to a single decision tree:

- **If parser-strict-prefix is firing > some-threshold%**: fix prompts first -- add "no fences, no preamble, start with `<sentinel>`" to compile/consolidate/summarize/article-match prompts. **I3 + I2** (format examples).
- **If parsers are STILL firing after prompts tighten**: broaden parsers (C5/C7). Inline at each site. Only extract shared helper if a fifth site appears (devil's: 4 sites doesn't justify the helper module).
- **If validation-as-warning is firing > some-threshold%**: tighten validator first (**I8** regex anchor). Then consider hard-gate (**C2**). Don't gate without anchoring.
- **If categorizer minted-new-directory log shows entropy**: implement fuzzy-snap-to-existing with similarity threshold (**C4** soft form). Don't go strict closed-set; would break vault-growth.
- **If article topic-match unknown-filename log shows duplicate creation**: add constrained-prompt retry (**C3** retry half).

### Tier 3: Consolidator lineage (~1 hour, after panel scope-reduction)

8. **C1 pared form**: Add `consolidated_from: [src_paths]` frontmatter. Use compiler's `Article` + `serialize_article` pattern (not adding `sources` parameter to `write_article`). Skip full sources-array merge until evidence of fabrication. Verify `find_orphan_articles` exists (faithfulness flagged it might not); if so, teach it about `tags: [consolidated]`; if not, drop that consequence claim.

### Tier 4: Prompt focusing (~1-2 hours, conditional)

9. **I1**: Drop `prompt_builder.build()` from synthesis sites (consolidator + compiler.compile_one + compiler.merge_into_existing). Replace with focused synthesis-only system prompts. Devil's argues this is intentional; prioritization argues smoke-test contradicts the "instruction-following degrades" claim. **Decide based on Tier 0 telemetry**: if compile/consolidate fail-rates are non-trivial, this matters; if they're flat, defer indefinitely. Ship in its own commit, never bundled with C2 (would confound regression analysis).

### Reclassified as intentional design (close, do not fix)

- Compiler not embedding source URLs in article body -- add a docstring comment in `compiler.py` noting "compiled truth is intentionally URL-free; provenance lives in TIMELINE below the marker."
- Consolidator pulling `PAL_BASE_PROMPT` -- add a docstring comment noting the choice; revisit only for token-cost reasons.
- PDF tier-3 bracket-extract failing soft to tier-2 -- already the design; no change.

### Deferred forever (acknowledged real, not worth fixing)

Per prioritization reviewer: real but never bite in practice. Queue indefinitely, don't feel bad.

- **I6** (compiler merge truncates timeline summaries to 200 chars) -- hypothetical fact-loss; chat-first lens means users don't read TIMELINE anyway.
- **I15** (`TITLE_RULES` reused across system + user contexts) -- cosmetic refactor, both sites produce acceptable titles today.
- **I16** (fence categorizer directory list with `<directories>` tags) -- speculative; defer until we see it fire.
- All Minor "prompt phrasing nits" -- without a prompt-eval harness, nothing here can be measured.

### Misses noted by panel (incorporate when adjacent work touches them)

- `merge_into_existing` lacks the `max_body_chars` size guard that `compile_one` has -- add when next touching compiler.
- `article.py:259` strips only straight quotes, not smart quotes -- add when next touching topic-match.
- `find_existing_article` candidate list shows filenames only, no source-type provenance -- chat-derived articles indistinguishable from external-source in matching. Out of Pass 2 scope but flag for future "topic-match quality" work.

---

## Original Tiered action list (superseded by panel-revised list above)

### Tier 1: Mechanical, low-risk (single commit each, ~15-30 min)

1. **C8 + C9**: Strip code fences before JSON parse in learning_scanner + pdf_structure. Add WARNING logs on parse-fallback in both. Plus categorizer first-line parser.
2. **C3**: Add WARNING log in `article.find_existing_article` when topic-match returns unknown filename.
3. **I5**: Drop dead `INSUFFICIENT:` parser branch in compiler.py merge (OR add it to the merge prompt). Pick one.
4. **I11**: Case-fold-compare in categorizer parser.
5. **I14**: Add `logger.warning` to every parse-fallback path (summarizer, pdf_structure, categorizer, learning_scanner).

One commit per item or one commit for all (small enough). No test breakage expected.

### Tier 2: Output-strictness hardening (one shared spec, ~1-2 hours)

6. **Theme A**: Build `pal/prompts/output_parsing.py` with `strip_model_wrapping(text)` and `match_keyword_prefix(text, keyword)` helpers. Adopt across compiler INSUFFICIENT parser, article NONE parser, summarizer/title_cleanup TITLE parser, categorizer first-line parser. Add "do not wrap, no preamble" instruction to every prompt simultaneously.

Pairs with **C5 + C7** (parser broadening) and **I2 + I3** (prompt format-example + no-wrapping additions).

### Tier 3: Validation as gate (one spec, ~30-60 min)

7. **C2**: Make `validate_compiled_truth` a hard error in compile_one, merge_into_existing, consolidator. Return `status: "insufficient"` with issues instead of shipping. Tighten validator to regex-anchored line matching (**I8**).

### Tier 4: Consolidator provenance (one spec, ~1-2 hours)

8. **C1**: Add `sources` parameter to `wiki.write_article`, harvest source articles' sources in consolidator, dedupe, merge, write through. Add `consolidated_from` frontmatter. Add inline-citation verification in parser (**I12**).

### Tier 5: Closed-set enforcement (one spec, ~1 hour)

9. **C4**: Decide categorizer closed-set vs open-set. If closed: rewrite prompt to "respond with exactly one of: ..." and parser to validate-or-fallback. If open: add fuzzy-match snap-to-existing before treating as new.
10. **C3 retry**: Optional second-shot for article topic-match with constrained prompt before giving up.

### Tier 6: Prompt focusing (one spec, ~1 hour)

11. **I1**: Drop `prompt_builder.build()` from consolidator; replace with focused synthesis-only system prompt. Check whether compiler.py's two sites have the same issue and fix in same pass.

### Tier 7: Smaller follow-ups (touch opportunistically)

12. **C6**: Wrap title-regen content with `wrap_untrusted` + `SANITIZATION_SYSTEM_PROMPT`. Add input truncation.
13. **I6**: Drop or rethink compiler merge `timeline_context` truncation.
14. **I7**: Guard CHAT_BANNER_SENTINEL in compiler merge path.
15. **I9**: Move summarizer title-format to system prompt.
16. **I10**: Length controls per-site.
17. **I13**: Learning-scanner refusal-keyword detection.
18. **I15**: Split `TITLE_RULES` into composable fragments.
19. **I16**: Fence directory list in categorizer prompt.

### Tier 8: Bigger asks (parked)

20. **I4 retry loops**: Bounded retry on validation failure. Touches compiler + consolidator. Worth doing AFTER Tier 3 (validation-as-gate) lands so the retry is the recovery mechanism for the new hard errors.
21. **Theme C / Provenance for compiler**: Decide whether compiler should embed source URLs in article bodies (currently only in TIMELINE below the marker, which chat-loop synthesis never sees).

---

## What's NOT in this audit

- Output of the prompts -- no end-to-end evaluation of consolidated/compiled article quality. The 2026-05-26 smoke test validated the synthesis model is capable; this audit assumes the prompts could be tighter even when current output is acceptable.
- Token cost analysis. Several recommendations (drop PAL_BASE_PROMPT from synthesis, truncate inputs) would save tokens, but Pass 2's framing is correctness/robustness, not cost.
- Benchmark harness. The held inference-routing-split spec's chat-side question still requires one; that's a separate workstream.
