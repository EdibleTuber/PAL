# Slash command prune: chat-first cleanup

**Date:** 2026-05-16
**Status:** Held -- blocked on propose_research expansion (separate spec TBD). When that lands, this spec ships as written (with panel-noted additions below). Do not execute until then.
**Author:** Brainstormed with Claude
**Related memory:** `project_chat_first_lens`
**Related work:**
- `docs/superpowers/audits/2026-05-16-prompt-audit-chat-slash.md` (this spec implements a deletion-shaped version of the slash command "rewrite descriptions" items C5, C8, I10, I14, I15, I16).
- propose_research expansion spec (TBD) -- adds `topic_file` parameter + per-URL progress emission so the chat path can replace `/research path/to/topics.md` with full functionality before this spec deletes `/research`.

## Why held

Panel review (2026-05-16) surfaced that `/research` has three concrete features the chat path does NOT have:
1. `--verbose` per-URL progress emission (relevant to memory `feedback_terse_progress`).
2. `deep` flag mapping to depth=10 (technically reachable from chat, but one-keystroke vs telling the model "use depth 10").
3. Topic-file batch mode (`/research path/to/topics.md`) with cross-topic URL dedup. The `Researcher` class supports it internally; `propose_research` does not expose it.

User chose path A: ship the propose_research expansion FIRST so the chat path matches `/research`'s functionality, THEN ship this slash prune. No regression window for the topic-file batch workflow.

## Why

PAL is used primarily conversationally. Slash commands that just wrap a chat-callable tool are dead weight: they appear in the commands catalog every chat turn, add cognitive surface for the model, and offer the user nothing they can't get via chat. The 2026-05-16 prompt audit surfaced this as a structural issue; the user has confirmed the chat-first lens (memory: `project_chat_first_lens`).

This spec deletes 10 redundant slash commands and restructures the README's chat documentation to match.

## Scope

### Delete (10 commands)

| Slash | Class | File | Chat equivalent |
|---|---|---|---|
| `/fetch` | `Fetch` | `pal/commands/domain.py` | `propose_research` (only web entry; no direct URL fetch in chat) |
| `/note` | `Note` | `pal/commands/domain.py` | `propose_promote_synthesis` after chat |
| `/compile` | `Compile` | `pal/commands/compile.py` | `compile_summary` tool |
| `/summarize` | `Summarize` | `pal/commands/domain.py` | Folded into `propose_research` -> `research_topic` |
| `/search` | `Search` | `pal/commands/domain.py` | `search_vault` tool |
| `/search-web` | `SearchWeb` | `pal/commands/domain.py` | `propose_research` |
| `/get` | `Get` | `pal/commands/domain.py` | `cat` (with path) |
| `/read` | `Read` | `pal/commands/domain.py` | `cat` (with path) |
| `/research` | `Research` | `pal/commands/research.py` | `propose_research` |
| `/compile-batch` | `CompileBatch` | `pal/commands/compile.py` | `propose_compile_batch` + `compile_batch` tools |

### Keep (PAL-specific, no chat equivalent)

- `Import` (PDF chapter detection, batch fallback proposal flow)
- `Lint` (vault maintenance)
- `Learn` (extract learnings from conversation)

### Keep (PAL-specific overrides of framework builtins)

- `Status`, `Profile`, `Wisdom`, `Scratch`, `PALModel` in `PALAgent.commands`

### Untouched (framework builtins, auto-merged by `_attach_registries`)

`/think`, `/help`, `/clear`, `/quit`, `/learnings`, `/promote`, `/rate`, `/context`

## Approach

Hard delete, no deprecation phase, no redirect stubs. User confirmed they'd rather take the muscle-memory hit once than maintain deprecation scaffolding.

## Code change surface

### `pal/commands/domain.py`
Delete classes: `Read`, `Search`, `Get`, `Note`, `SearchWeb`, `Fetch`, `Summarize`. Read each class first to find helper functions or imports used only by them and delete those too.

### `pal/commands/compile.py`
Delete classes `Compile`, `CompileBatch`. File becomes empty -> delete the file itself.

### `pal/commands/research.py`
Delete class `Research`. File becomes empty -> delete the file itself.

### `pal/commands/__init__.py`
Remove deleted exports. New `__all__`:
```python
["Import", "Learn", "Lint", "PALModel", "Profile", "Scratch", "Status", "Wisdom"]
```

### `pal/agent.py`
Lines 31-35: trim imports to only the kept classes.
Lines 151-152: update `commands` ClassVar to:
```python
commands = [
    Lint, Import, Learn,
    Status, Profile, Wisdom, Scratch, PALModel,
]
```

`disabled_builtins` docstring (lines 155-166) stays untouched -- the note that agent_core's SearchWeb/FetchUrl classes stay intact remains true.

## Doc updates

### `README.md` -- chat-first restructure

**Slash Commands table (lines 206-233):** prune to the kept commands plus framework builtins worth listing. Drop the 10 deleted rows. Keep: `/import`, `/lint`, `/learn`, `/learnings`, `/promote`, `/rate`, `/scratch`, `/profile`, `/wisdom`, `/status`, `/model`, `/think`, `/help`, `/quit`.

**Web Research Pipeline section (lines 362-379):** DELETE the entire section. The workflow walkthrough moves into the Chat section.

**Chat section (lines 200-204):** flesh out from 2 sentences to a half-page showing 2-3 worked chat examples (research with propose_research approval, file edit with edit_file, wiki promotion with propose_promote_synthesis). Becomes the canonical workflow walkthrough.

**Chat Tools section (lines 235-297):** LEAVE AS-IS in this spec. It has staleness (lists `read_file`, `list_directory`, `search_content` which don't exist, and `search_web` which is disabled) but those are tracked under prompt audit Theme D. Touching them here is scope creep.

### Smaller doc fixes

- `docs/agent_ecosystem_direction.md` lines 17 and 37: replace `/research, /compile, /summarize` examples with `/import, /lint, /learn`.
- `docs/article-format.md` line 49: `"used by /read and the search index"` -> `"used by the chat path and the search index"`.
- `docs/agentic_librarian_summary.md` line 84: `"when /compile runs"` -> `"when compile_summary runs"`.

## Test surface

### Direct cleanup
- `tests/test_commands.py` -- remove imports + test functions for deleted commands; keep tests for `Import`, `Lint`, `Learn`, `Status`, `Profile`, `Wisdom`, `Scratch`, `PALModel`.
- `tests/test_commands_drift.py` -- same pattern, plus update `EXPECTED_PAL_COMMANDS` / `EXPECTED_NAMES` sets.
- `tests/test_research_commands.py` -- file is for `/research` specifically; delete the file.
- `tests/test_summarize.py` -- file is for `/summarize`; delete the file.
- `tests/test_compile.py` -- panel-verified: entire file tests the `/compile` slash command; delete the file. (Compiler module tests live in `test_compiler.py` and `test_tools_compile.py`, untouched.)
- `tests/test_strict_note.py` -- for `/note`'s UNKNOWN-refusal behavior; delete the file.
- **`tests/test_wiki_commands.py`** -- panel caught: tests `/note` (3 places) and `/read`. Trim 3 of 6 tests (`test_note_command_creates_article`, `test_read_command`, `test_full_wiki_workflow`); keep `test_lint_command`, `test_status_command_includes_vault`, `test_daemon_rebuilds_index_on_startup`.
- **`tests/test_web_commands.py`** -- panel caught: entirely `/search-web` + `/fetch`; delete the file.
- **`tests/test_retrieval_commands.py`** -- panel caught: 4 of 5 tests use `/search` or `/get`; trim those, keep `test_status_includes_collection`.
- **`tests/test_daemon_help.py`** -- panel caught: hardcoded assertion list at line 20-21 includes deleted command names; trim the tuple to `("lint", "import", "learn")` plus framework builtins.

### Leave alone (substring matches but not the deleted commands)
- `tests/test_summarizer.py` -- tests `pal/summarizer.py` module (used by Researcher); stays.
- `tests/test_compiler.py`, `tests/test_tools_compile.py` -- test compiler module + `compile_summary` tool, not the slash command; stay.
- `tests/test_researcher.py` -- tests `pal/researcher.py` module (still used internally); stays.
- `tests/test_daemon.py` (already in standard --ignore list per memory) has two tests that drive `/summarize` and `/compile` for `/model` propagation. They'd become dead code asserting against deleted commands; rewrite to use a surviving command or delete the assertions.
- `tests/test_client.py` (already --ignored) line 142 uses `client.command("note", ...)` as a wire-format test vector; command name is arbitrary, doesn't route. Optionally rename to a surviving command for clarity.
- `tests/test_prompt_builder.py:104` -- comment string mentions `/search-web`; update comment.

## Doc surface (panel additions)

In addition to the README + 3 small doc fixes already listed:

- **`docs/architecture-flows.md`** -- panel caught: ASCII diagrams describe `/note`, `/fetch -> /summarize -> /compile`, `/research -> /compile-batch` pipelines (lines 8-9, 27-47, 87-132). Rewrite the diagrams to the chat-tool flow or mark stale. Cannot reasonably be deferred -- it's architecture documentation that becomes wrong on commit.
- **`docs/security.md` line 28** -- "Domain allowlist gates /search-web results and /fetch targets." Update to "Domain allowlist gates web fetches in the research pipeline."
- **`docs/searxng-setup.md` line 3** -- opens with "PAL's /search-web command requires..." Rewrite to reference `propose_research`/SearxNG-via-research-pipeline.
- **`docs/agentic_librarian_summary.md`** -- panel caught: in addition to line 84, lines 88 and 127-137 reference `/compile`, `/research`, `/search-web`, `/fetch`, `/summarize`, `/compile-batch`. Update the whole web-research-pipeline subsection (lines 122-137) and the research-mode subsection (lines 86-88).
- **`README.md` line 86** -- quick-start mentions `/research`; rewrite to chat-first.
- **`README.md` Chat section flesh-out** -- panel said "half a page" is too much for the README voice. Tighten to one terminal snippet showing the propose/approve pattern + prose under 150 words. Match the existing Quickstart's concise, terminal-flavored style.

## Source-file docstring updates (panel additions)

- **`pal/agent.py:155-166`** -- `disabled_builtins` docstring claims "The /search-web slash command (separate user-facing surface in pal/commands/domain.py) is unaffected." After this spec, that's FALSE -- PAL's `/search-web` is gone. Drop the last two sentences or rewrite.
- **`pal/summarizer.py:3`** -- docstring says "Extracted from daemon._handle_summarize so both /summarize and /research..." Both consumers gone; only Researcher remains. Rewrite.
- **`pal/compiler.py:3`** -- docstring says "Extracted from pal.daemon so both the /compile slash command and the..." /compile gone. Rewrite.

## Other panel-noted additions

- `/context` was missing from the keep-list of slash commands worth listing in the README. Add `/context` to the README table.
- `pal/commands/__init__.py` -- spec must explicitly say "trim the `from pal.commands.compile import ...` and `from pal.commands.research import ...` lines" in addition to `__all__`. Implementer-blocking ambiguity caught by feasibility reviewer.

## Verification

1. Daemon import smoke:
   ```bash
   cd /home/edible/Projects/PAL && .venv/bin/python -c "from pal.agent import PALAgent; print('ok')"
   ```
2. Full PAL test suite (with standard ignores per memory):
   ```bash
   cd /home/edible/Projects/PAL && .venv/bin/pytest tests/ \
       --ignore=tests/test_chat_research_integration.py \
       --ignore=tests/test_client.py \
       --ignore=tests/test_daemon.py \
       --ignore=tests/test_integration.py \
       --ignore=tests/test_prompt_injection.py \
       -q 2>&1 | tail -5
   ```
3. Em-dash sweep on the diff -- no new em dashes in any added content.

## Commit shape

One commit. Message:

```
refactor(commands): remove 10 slash commands redundant with chat-tool path

Removes /fetch, /note, /compile, /summarize, /search, /search-web,
/get, /read, /research, /compile-batch. Each duplicated functionality
the chat model already has via tools (search_vault, cat, propose_research,
compile_summary, etc.).

PAL is used primarily conversationally; slash commands that wrap
chat-callable tools were dead weight in the commands catalog every
turn. See memory: project_chat_first_lens.

Kept: /import, /lint, /learn (no chat equivalent); all PAL-specific
overrides of framework builtins; all framework builtins.

README restructured to chat-first: deletes "Web Research Pipeline"
section, prunes Slash Commands table, fleshes out Chat section with
worked examples. Chat Tools table staleness deferred to prompt audit.
```

## Migration / back-compat

- Anyone with muscle memory typing one of the removed commands gets `Unknown command` error. Acceptable per user's choice (option 1 hard delete).
- Stored conversation history with user messages like "/research X" are just text; no replay issue.
- `ResponseMessage.command="research"` (etc.) in historical messages: Discord adapter doesn't route on this field (verified by grep); no behavior change.
- No agent_core changes; no PAL pin bump; server deploy is standard `git pull` + `pal-daemon` restart.

## Risks

1. **Muscle memory hit.** User accepts this per the option-1 hard-delete choice.
2. **`/research` UX change.** The deleted `/research` was immediate-execution (no approval prompt); `propose_research` requires approval. User now waits one extra round for the approval prompt. Marginal cost; explicit consent is arguably better.
3. **`/fetch` has no direct replacement.** Direct URL fetch via slash command goes away. All web entry goes through `propose_research`. User previously used /fetch for ad-hoc URL ingestion; now must phrase as research request. Acceptable per chat-first lens.
4. **README rewrite is taste-dependent.** The "Chat" section flesh-out is the largest doc change. Implementer needs to write the worked examples; should match the existing README voice (concise, terminal-snippet-flavored).

## Out of scope (intentionally)

- Fixing stale Chat Tools table entries (`read_file`, `list_directory`, `search_content`, `search_web`) -- tracked under prompt audit Theme D.
- Tier 1 / Tier 2 audit fixes from the prompt audit -- separate work.
- Any tool-side changes -- only slash commands and docs in this spec.
- Adding new chat-driven equivalents for anything currently missing -- the existing tool surface is the assumed replacement.
- Server deployment -- user handles.
