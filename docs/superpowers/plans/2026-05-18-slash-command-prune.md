# Slash command prune -- Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete 10 redundant slash commands (`/fetch`, `/note`, `/compile`, `/summarize`, `/search`, `/search-web`, `/get`, `/read`, `/research`, `/compile-batch`) and restructure PAL's docs to chat-first. Each duplicates functionality the chat model already has via tools (`search_vault`, `cat`, `propose_research`, `compile_summary`, `propose_compile_batch`, etc.).

**Architecture:** PAL-only change. Delete Command classes (7 in `domain.py`, 2 in `compile.py`, 1 in `research.py`); the two single-class files get deleted entirely. Update the `commands` ClassVar in `PALAgent` + the `__init__.py` exports. Test cleanup spans 10 files (5 deleted, 5 trimmed). README restructured to chat-first; 5 other docs updated.

**Tech Stack:** Python 3.12, pytest, PAL.

**Spec:** `docs/superpowers/specs/2026-05-16-slash-command-prune-design.md`

**No agent_core changes. No pin bump.** Standard `git pull` + `pal-daemon` restart on deploy.

---

## File Structure

**PAL repo (`/home/edible/Projects/PAL/`):**

### Code (Task 1)
- Modify: `pal/commands/domain.py` -- delete classes `Read`, `Search`, `Get`, `Note`, `SearchWeb`, `Fetch`, `Summarize` (plus any helpers used only by them).
- Delete: `pal/commands/compile.py` -- becomes empty after `Compile` + `CompileBatch` deletion.
- Delete: `pal/commands/research.py` -- becomes empty after `Research` deletion.
- Modify: `pal/commands/__init__.py` -- trim imports + `__all__`.
- Modify: `pal/agent.py` -- trim imports (lines 31-35), update `commands` ClassVar (lines 151-152), fix `disabled_builtins` docstring (lines 155-166).
- Modify: `pal/summarizer.py` -- rewrite line 3 docstring (`/summarize` reference is stale).
- Modify: `pal/compiler.py` -- rewrite line 3 docstring (`/compile` reference is stale).

### Tests (Task 2)
- Delete entirely: `tests/test_compile.py`, `tests/test_research_commands.py`, `tests/test_summarize.py`, `tests/test_strict_note.py`, `tests/test_web_commands.py`.
- Trim: `tests/test_commands.py`, `tests/test_commands_drift.py`, `tests/test_wiki_commands.py`, `tests/test_retrieval_commands.py`, `tests/test_daemon_help.py`.
- Touch (comment): `tests/test_prompt_builder.py:104` -- stale `/search-web` mention.

### Docs (Tasks 3-4)
- Modify: `README.md` -- prune Slash Commands table, delete Web Research Pipeline section, flesh out Chat section, update line 86 quick-start.
- Modify: `docs/architecture-flows.md` -- rewrite Direct Creation, Web Fetch Pipeline, Batch Research entrypoints to chat-driven.
- Modify: `docs/agentic_librarian_summary.md` -- lines 84, 86-88, 122-137.
- Modify: `docs/security.md` line 28 -- allowlist wording.
- Modify: `docs/searxng-setup.md` line 3 -- first sentence.
- Modify: `docs/agent_ecosystem_direction.md` lines 17, 37 -- example commands.
- Modify: `docs/article-format.md` line 49 -- `/read` reference.

---

## Task 1: Delete Command classes + update agent.py + fix stale docstrings

Single atomic commit. The daemon must import cleanly after this lands -- so all related files change together.

**Files:**
- Modify: `/home/edible/Projects/PAL/pal/commands/domain.py`
- Delete: `/home/edible/Projects/PAL/pal/commands/compile.py`
- Delete: `/home/edible/Projects/PAL/pal/commands/research.py`
- Modify: `/home/edible/Projects/PAL/pal/commands/__init__.py`
- Modify: `/home/edible/Projects/PAL/pal/agent.py`
- Modify: `/home/edible/Projects/PAL/pal/summarizer.py`
- Modify: `/home/edible/Projects/PAL/pal/compiler.py`

**Branch check before commit:** `git branch --show-current` should report `main`.

- [ ] **Step 1: Delete classes from `pal/commands/domain.py`**

Read the file first to find the exact line ranges for each class. Delete these class definitions in full (including their docstrings, any imports used ONLY by them):
- `Read` (the `/read` command class)
- `Search` (the `/search` command class)
- `Get` (the `/get` command class)
- `Note` (the `/note` command class)
- `SearchWeb` (the `/search-web` command class)
- `Fetch` (the `/fetch` command class)
- `Summarize` (the `/summarize` command class)

After deletion, scan the imports at the top of the file. Remove any import lines whose symbols were used ONLY by the deleted classes. Use the Grep tool to verify each removed import is no longer referenced in the remaining file.

The file will retain its other classes (Import, Lint, Learn, Status, Profile, Wisdom, Scratch, PALModel) and shared helpers.

- [ ] **Step 2: Delete `pal/commands/compile.py` entirely**

The file contains only `Compile` and `CompileBatch`. Remove the file:

```bash
rm /home/edible/Projects/PAL/pal/commands/compile.py
```

- [ ] **Step 3: Delete `pal/commands/research.py` entirely**

The file contains only `Research`. Remove the file:

```bash
rm /home/edible/Projects/PAL/pal/commands/research.py
```

- [ ] **Step 4: Update `pal/commands/__init__.py`**

Read the current content. Replace with:

```python
"""PAL command implementations (Command subclasses)."""
from pal.commands.domain import (
    Import, Learn, Lint, PALModel, Profile, Scratch, Status, Wisdom,
)

__all__ = [
    "Import", "Learn", "Lint", "PALModel",
    "Profile", "Scratch", "Status", "Wisdom",
]
```

The `from pal.commands.compile import ...` and `from pal.commands.research import ...` lines MUST be removed (those modules are gone).

- [ ] **Step 5: Update `pal/agent.py` imports**

Locate the import block at lines 31-35 (currently importing 17 symbols including the deleted ones). After trim, the block should import only the 8 remaining: `Import, Learn, Lint, PALModel, Profile, Scratch, Status, Wisdom`. Remove the `Compile as CmdCompile` and `CompileBatch as CmdCompileBatch` aliases entirely.

Updated block (preserve the surrounding context):

```python
from pal.commands import (
    Import, Learn, Lint, PALModel,
    Profile, Scratch, Status, Wisdom,
)
```

- [ ] **Step 6: Update `pal/agent.py` `commands` ClassVar**

Locate lines 151-152 in `pal/agent.py`. Replace with:

```python
    commands = [
        Lint, Import, Learn,
        Status, Profile, Wisdom, Scratch, PALModel,
    ]
```

- [ ] **Step 7: Update `pal/agent.py` `disabled_builtins` docstring**

Locate lines 155-166 (the comment block above `disabled_builtins = frozenset({"fetch_url", "search_web"})`). Currently the last two sentences say:

> agent_core's SearchWeb/FetchUrl classes stay intact (future agents may
> want them); PAL just stops registering them. The /search-web slash
> command (separate user-facing surface in pal/commands/domain.py) is
> unaffected.

That last sentence is now FALSE (the `/search-web` slash command is being deleted). Drop the last two sentences and replace the docstring tail with:

```python
    # Disabled LLM-facing builtin tools in PAL.
    #   fetch_url: all web fetching goes through the consent-gated research
    #     pipeline (propose_research / research_topic); direct URL fetch
    #     would bypass the approval flow.
    #   search_web: output URLs are mostly unfetchable due to FetchUrl's
    #     allowlist filter; the user prefers propose_research for web work.
    #     See docs/superpowers/specs/2026-05-12-search-vault-json-result-format-design.md.
    # agent_core's SearchWeb/FetchUrl classes stay intact (future agents may
    # want them); PAL just stops registering them.
    disabled_builtins: frozenset[str] = frozenset({"fetch_url", "search_web"})
```

(Read the actual current docstring first to ensure the exact text being replaced.)

- [ ] **Step 8: Update `pal/summarizer.py` docstring**

Read line 1-5 of the file. The docstring currently says something like "Extracted from daemon._handle_summarize so both /summarize and /research...". Both consumers are gone except `Researcher` (which still uses it internally for `summarize_raw_file`).

Rewrite the module docstring (lines 1-5 area) to:

```python
"""Summarize a raw fetched source file into a wiki-ready summary.

Used by Researcher during research_topic execution. The model is asked
to produce a TITLE: line plus a body; on parse failure the path stem is
used as the title and the raw text is preserved.
"""
```

- [ ] **Step 9: Update `pal/compiler.py` docstring**

Read line 1-5 of the file. The docstring currently says something like "Extracted from pal.daemon so both the /compile slash command and the...". `/compile` is gone; only the `compile_summary` tool path remains.

Rewrite the module docstring to:

```python
"""Compile raw summary files into wiki articles.

Used by the compile_summary tool. find_existing_article identifies merge
targets via retrieval-index lookup + model confirmation; merges append to
the article's timeline while rewriting the compiled-truth section.
"""
```

- [ ] **Step 10: Daemon import smoke**

```bash
cd /home/edible/Projects/PAL && .venv/bin/python -c "from pal.agent import PALAgent; print('ok')"
```

Expected: prints `ok`. If `ImportError` or `NameError`, something in steps 1-9 was missed. Read the traceback, locate the missing import/reference, fix.

- [ ] **Step 11: Em-dash sweep on staged changes**

```bash
cd /home/edible/Projects/PAL && git diff | grep '^+' | grep -v '^+++' | grep -cP '[\x{2014}\x{2013}]'
```

Expected: `0`. (Pre-existing em dashes in unchanged context lines are out of scope.)

- [ ] **Step 12: Commit**

`git branch --show-current` must return `main`. Then:

```bash
cd /home/edible/Projects/PAL && git add pal/commands/domain.py pal/commands/__init__.py pal/agent.py pal/summarizer.py pal/compiler.py && git rm pal/commands/compile.py pal/commands/research.py && git commit -m "$(cat <<'EOF'
refactor(commands): remove 10 slash commands redundant with chat-tool path

Removes /fetch, /note, /compile, /summarize, /search, /search-web,
/get, /read, /research, /compile-batch. Each duplicated functionality
the chat model already has via tools (search_vault, cat, propose_research,
compile_summary, propose_compile_batch, etc.).

PAL is used primarily conversationally; slash commands that wrap
chat-callable tools were dead weight in the commands catalog every
turn. See memory: project_chat_first_lens.

Kept: /import, /lint, /learn (no chat equivalent); all PAL-specific
overrides of framework builtins (Status, Profile, Wisdom, Scratch,
PALModel); all framework builtins.

Docstrings in summarizer.py and compiler.py updated to reflect their
new single-consumer reality (Researcher and compile_summary tool).
disabled_builtins docstring tail dropped its now-false claim about
/search-web slash being unaffected.

Test cleanup, README restructure, and other doc updates land in
follow-up commits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Test cleanup

**Files:**
- Delete: `/home/edible/Projects/PAL/tests/test_compile.py`
- Delete: `/home/edible/Projects/PAL/tests/test_research_commands.py`
- Delete: `/home/edible/Projects/PAL/tests/test_summarize.py`
- Delete: `/home/edible/Projects/PAL/tests/test_strict_note.py`
- Delete: `/home/edible/Projects/PAL/tests/test_web_commands.py`
- Modify: `/home/edible/Projects/PAL/tests/test_commands.py`
- Modify: `/home/edible/Projects/PAL/tests/test_commands_drift.py`
- Modify: `/home/edible/Projects/PAL/tests/test_wiki_commands.py`
- Modify: `/home/edible/Projects/PAL/tests/test_retrieval_commands.py`
- Modify: `/home/edible/Projects/PAL/tests/test_daemon_help.py`
- Modify: `/home/edible/Projects/PAL/tests/test_prompt_builder.py` (comment-only fix)

**Branch check:** `git branch --show-current` should report `main`.

- [ ] **Step 1: Delete 5 entirely-stale test files**

```bash
cd /home/edible/Projects/PAL && rm \
  tests/test_compile.py \
  tests/test_research_commands.py \
  tests/test_summarize.py \
  tests/test_strict_note.py \
  tests/test_web_commands.py
```

Per the panel: `test_compile.py` is purely the `/compile` slash command; `test_research_commands.py` is `/research`; `test_summarize.py` is `/summarize`; `test_strict_note.py` is `/note`'s UNKNOWN refusal behavior; `test_web_commands.py` is `/search-web` + `/fetch`. All five files have nothing to keep after deletion.

- [ ] **Step 2: Trim `tests/test_commands.py`**

Read the file. Find any test functions and import lines that reference the deleted classes (`Read`, `Search`, `Get`, `Note`, `SearchWeb`, `Fetch`, `Summarize`, `Compile`, `CompileBatch`, `Research`). Remove those tests + their imports.

KEEP tests for `Import`, `Lint`, `Learn`, `Status`, `Profile`, `Wisdom`, `Scratch`, `PALModel`.

Run the file to confirm it imports + passes after the trim:

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_commands.py -v 2>&1 | tail -10
```

- [ ] **Step 3: Trim `tests/test_commands_drift.py`**

Read the file. Same pattern as Step 2. Plus: update the `EXPECTED_PAL_COMMANDS` / `EXPECTED_NAMES` sets/lists to reflect the new commands ClassVar (just the 8 kept). Remove the deleted command names from any expected-set assertions.

Run:

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_commands_drift.py -v 2>&1 | tail -10
```

- [ ] **Step 4: Trim `tests/test_wiki_commands.py`**

Read the file. Remove the 3 tests that exercise deleted commands:
- `test_note_command_creates_article` (uses `/note`)
- `test_read_command` (uses `/read`)
- `test_full_wiki_workflow` (uses `/note`)

KEEP: `test_lint_command`, `test_status_command_includes_vault`, `test_daemon_rebuilds_index_on_startup`.

Run:

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_wiki_commands.py -v 2>&1 | tail -10
```

- [ ] **Step 5: Trim `tests/test_retrieval_commands.py`**

Read the file. Remove the 4 tests that use `/search` or `/get`. KEEP `test_status_includes_collection` (per panel reviewer's identification).

Run:

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_retrieval_commands.py -v 2>&1 | tail -10
```

- [ ] **Step 6: Trim `tests/test_daemon_help.py`**

Read the file. Find `test_help_would_include_all_registered_names` (around line 20-21). The hardcoded tuple currently includes deleted command names like `"read"`, `"search"`, `"note"`, `"compile"`, `"research"`, `"fetch"`, `"summarize"`. Trim the tuple to only the surviving PAL command names PLUS framework builtins still expected: `("lint", "import", "learn")` for PAL's set, plus whatever framework builtins the test already includes.

Read the actual test first; the assertion may be on a different structure than a tuple. Adjust accordingly.

Run:

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_daemon_help.py -v 2>&1 | tail -10
```

- [ ] **Step 7: Fix comment in `tests/test_prompt_builder.py:104`**

Read line 104 of `tests/test_prompt_builder.py`. There's a comment mentioning `/search-web` that becomes stale. Update the comment text to remove the slash-command reference (the test itself probably still passes since the comment is documentation).

- [ ] **Step 8: Full PAL suite regression sweep**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/ \
    --ignore=tests/test_chat_research_integration.py \
    --ignore=tests/test_client.py \
    --ignore=tests/test_daemon.py \
    --ignore=tests/test_integration.py \
    --ignore=tests/test_prompt_injection.py \
    -q 2>&1 | tail -5
```

Expected: all pass. The test count drops significantly (5 files deleted + multiple trims) but no failures.

- [ ] **Step 9: Em-dash sweep**

```bash
cd /home/edible/Projects/PAL && git diff | grep '^+' | grep -v '^+++' | grep -cP '[\x{2014}\x{2013}]'
```

Expected: `0`.

- [ ] **Step 10: Commit**

`git branch --show-current` must return `main`. Then stage explicit paths (NEVER `git add -A`):

```bash
cd /home/edible/Projects/PAL && git add tests/test_commands.py tests/test_commands_drift.py tests/test_wiki_commands.py tests/test_retrieval_commands.py tests/test_daemon_help.py tests/test_prompt_builder.py && git rm tests/test_compile.py tests/test_research_commands.py tests/test_summarize.py tests/test_strict_note.py tests/test_web_commands.py && git commit -m "$(cat <<'EOF'
test(commands): drop tests for the 10 removed slash commands

Deletes 5 test files that exercised only deleted commands
(test_compile, test_research_commands, test_summarize, test_strict_note,
test_web_commands). Trims 4 mixed files (test_commands,
test_commands_drift, test_wiki_commands, test_retrieval_commands) to
keep tests for surviving commands. Updates test_daemon_help's
hardcoded expected-names tuple and a stale /search-web comment in
test_prompt_builder.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: README chat-first restructure

**Files:**
- Modify: `/home/edible/Projects/PAL/README.md`

**Branch check:** `git branch --show-current` should report `main`.

- [ ] **Step 1: Prune the Slash Commands table (lines 206-233)**

Read the current table. Remove these 10 rows: `/note`, `/read`, `/search`, `/get`, `/search-web`, `/fetch`, `/research`, `/summarize`, `/compile`, `/compile-batch`.

KEEP these rows (already in the table): `/scratch`, `/learn`, `/learnings`, `/promote`, `/rate`, `/profile`, `/wisdom`, `/lint`, `/model`, `/think`, `/status`, `/help`, `/quit`.

ADD a new row for `/context` (framework builtin, panel-noted as missing from the README table). Insert it near `/status`:

```
| `/context` | Show context budget: last-turn tokens + component byte sizes |
```

Verify the final table has ~14 rows (13 kept + 1 added).

- [ ] **Step 2: Update line 86 quick-start mention**

Locate the quick-start narrative around line 86. Current text mentions `/research <topic>` as an example next-step:

> That's it. From here, explore `/help` for commands, `/research <topic>` for web research, or just keep chatting and let PAL use tools as it sees fit.

Replace with:

> That's it. From here, explore `/help` for commands, or just chat with PAL. Ask it to research a topic, edit a note, or consolidate articles -- it'll use tools as needed and ask for approval before web fetches or wiki writes.

- [ ] **Step 3: Delete "Web Research Pipeline" section (lines 362-379)**

Find the section header `## Web Research Pipeline` and delete the entire section through the end of its last paragraph (the line ending with "...without losing provenance."). The chat-first walkthrough in Step 4 (next) covers this content's purpose.

- [ ] **Step 4: Flesh out the Chat section (lines 200-204)**

The current Chat section is 2 sentences. Replace it with a worked example showing the propose/approve pattern. Keep the prose under 150 words per panel guidance. The replacement:

```markdown
### Chat

Type naturally. PAL streams responses with live markdown rendering and uses tools to read, search, edit, and write your vault.

Web fetches and most vault writes go through a consent-gated proposal flow. When PAL wants to research a topic or merge articles, it sends an approval prompt; you review and click approve, decline, or edit.

```
you> research the topics in raw/notes/queue.md
  [reading raw/notes/queue.md ...]
  [proposes research with 5 topics]

[Approve] [Decline] [Edit]

you> approve
  [Fetched: example.com/page-1 ...]
  [Summarized: example.com/page-1 ...]
  ...
  [5 summaries staged in raw/summaries/]

you> compile those into the wiki
  [proposes compile-batch with 5 summaries]

[Approve] [Decline] [Edit]
```

Single-source operations (a single research, a single compile) follow the same propose/approve pattern. The full set of tools PAL has during a chat turn is in [Chat Tools](#chat-tools) below.
```

(Length: ~140 words including code block content. Trim if it grows during edit.)

- [ ] **Step 5: Em-dash sweep**

```bash
cd /home/edible/Projects/PAL && git diff | grep '^+' | grep -v '^+++' | grep -cP '[\x{2014}\x{2013}]'
```

Expected: `0`.

- [ ] **Step 6: Commit**

`git branch --show-current` must return `main`. Then:

```bash
cd /home/edible/Projects/PAL && git add README.md && git commit -m "$(cat <<'EOF'
docs(README): restructure to chat-first; drop deleted-slash refs

Slash Commands table pruned to the 14 surviving commands (adds
/context which was missing). Web Research Pipeline section deleted
entirely -- the workflow walkthrough moves into the Chat section,
which now shows the propose/approve pattern with a worked
research-then-compile example.

Quick-start narrative at line 86 updated to point at chat ("just
chat with PAL") instead of /research.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Other doc updates

**Files:**
- Modify: `/home/edible/Projects/PAL/docs/architecture-flows.md`
- Modify: `/home/edible/Projects/PAL/docs/agentic_librarian_summary.md`
- Modify: `/home/edible/Projects/PAL/docs/security.md`
- Modify: `/home/edible/Projects/PAL/docs/searxng-setup.md`
- Modify: `/home/edible/Projects/PAL/docs/agent_ecosystem_direction.md`
- Modify: `/home/edible/Projects/PAL/docs/article-format.md`

**Branch check:** `git branch --show-current` should report `main`.

- [ ] **Step 1: Rewrite `docs/architecture-flows.md` entrypoint sections**

Read the file. The "Ingestion Pipeline" section has three flow diagrams keyed on deleted commands:
- "Direct Creation (/note)" -- lines 8-24 area
- "Web Fetch Pipeline (/fetch --> /summarize --> /compile)" -- lines 27-47+ area
- "Batch Research and Compilation (/research --> /compile-batch)" -- around line 87+

Rewrite the entrypoint headers + first few lines of each flow to chat-driven. The downstream pipeline diagrams (Categorizer, WikiManager.write_article, git commit, etc.) stay UNCHANGED -- those parts of the flows are still accurate.

For each flow, replace the entrypoint as follows:

**Direct Creation:** the `/note` flow becomes chat-driven via `propose_promote_synthesis` after conversation. Replace the header + first 3 lines:

```
Direct Creation (chat-derived synthesis)
  User chats with PAL about a topic
    |
    v
  PAL suggests: "Want me to promote this thread about <topic> into the wiki?"
    |
    v
  User: yes
    |
    v
  Chat model calls propose_promote_synthesis(title, note_path, rationale)
    |
    v
  [Approve / Decline / Edit prompt]
    |
    v (on approve)
  Daemon reads raw note --> inference server (compile prompt)
    |
    v
  Categorizer scans vault dirs --> picks category
    |
    [... rest of original flow stays the same ...]
```

**Web Fetch Pipeline:** the manual three-step `/fetch -> /summarize -> /compile` pipeline is gone. Replace with the chat-driven research path:

```
Web Research Pipeline (chat-driven, consent-gated)

  User chats: "research <topic>" or "research these topics: a, b, c"
    |
    v
  Chat model calls propose_research(topic=... or topics=[...], rationale=...)
    |
    v
  [Approve / Decline / Edit prompt]
    |
    v (on approve)
  Chat model calls research_topic(proposal_id)
    |
    v
  Researcher: SearxNG search (with allowlist filter)
    |
    v
  URLFetcher.fetch() -- HTTP GET with prompt injection defenses
    |                    (GUID boundaries, sanitization, size cap)
    v
  raw/web/{slug}.md -- quarantine zone, untrusted content
    |
    v
  summarize_raw_file --> inference server (summarize prompt)
    |
    v
  raw/summaries/{slug}.md -- sanitized summary
    |
    v
  User: "compile those into the wiki"
    |
    v
  Chat model calls propose_compile_batch(summary_paths)
    |
    v
  [Approve / Decline / Edit prompt]
    |
    v (on approve)
  compile_batch tool runs Compiler for each summary
    |
    [... downstream: Categorizer, find_existing_article, etc.]
```

**Batch Research and Compilation:** if there's a separate section for this, merge it into the Web Research Pipeline above (the chat-driven path handles both single and batch with the `topics: list[str]` parameter).

Read the actual file to identify exact line ranges; the goal is "after this edit, no flow diagram references a deleted command."

- [ ] **Step 2: Update `docs/agentic_librarian_summary.md` (multi-line)**

Read lines 80-90 and 122-137.

**Line 84:** change `"when /compile runs"` (or similar) to `"when compile_summary runs"`.

**Lines 86-88 ("Research Mode" subsection):** replace the paragraph that starts with `/research <topic>` with:

```markdown
## Research Mode

The chat model invokes `propose_research` for topic-level research. Single-topic mode accepts a string; batch mode accepts a `topics: list[str]` with cross-topic URL deduplication. Approval is consent-gated; after approve, `research_topic` runs the SearxNG search + per-URL fetch + per-source summarize pipeline. Summaries land in `raw/summaries/` for review. Chat then proposes compilation via `propose_compile_batch` for batches or `compile_summary` for single articles.
```

**Lines 122-137 ("Web Research Pipeline" section):** replace the whole subsection with:

```markdown
## Web Research Pipeline

Controlled ingestion for untrusted external content. The chat model drives it through proposal-gated tools:

1. `propose_research(topic=...)` for single topic OR `propose_research(topics=[...])` for batch with cross-topic URL dedup. Blocks until user approves.
2. After approve, `research_topic(proposal_id)` runs the SearxNG-filtered fetch + summarize pipeline. Per-URL progress events stream live.
3. Summaries land in `raw/summaries/` for review.
4. Chat then proposes compilation via `propose_compile_batch(summary_paths=...)` for batches or `compile_summary(summary_path=...)` for single articles. Both go through the propose/approve cycle.

Untrusted content stays quarantined until it has passed through the full sanitization and compilation pipeline. The review gate sits between `raw/summaries/` and any compile proposal, so no wiki writes happen without explicit user action.
```

- [ ] **Step 3: Update `docs/security.md` line 28**

Read line 28 in context (a few lines around it for context).

Current text mentions `/search-web` and `/fetch` allowlist gating. Change to:

```markdown
- Domain allowlist gates web fetches in the chat-driven research pipeline (propose_research / research_topic) -- _config/allowlist.md is the source of truth.
```

(Adapt the exact wording to flow with the surrounding bullet style.)

- [ ] **Step 4: Update `docs/searxng-setup.md` line 3**

Read line 1-5.

The first sentence currently says something like "PAL's `/search-web` command requires...". Rewrite to:

```markdown
PAL's chat-driven research path (`propose_research` / `research_topic`) requires a SearxNG instance for web search. This doc covers the local SearxNG setup PAL was tested against.
```

- [ ] **Step 5: Update `docs/agent_ecosystem_direction.md` lines 17 and 37**

Read both line ranges.

Line 17 currently has something like:

> Commands: per-agent. /research, /compile, /summarize stay with PAL. Other agents define their own command sets appropriate to their work.

Replace with:

> Commands: per-agent. /import, /lint, /learn stay with PAL. Other agents define their own command sets appropriate to their work.

Line 37 currently lists `/research, /compile, /summarize, /think, /import` as PAL examples. Replace the list with `/import, /lint, /learn, /think, /scratch` (the PAL-specific kept commands).

- [ ] **Step 6: Update `docs/article-format.md` line 49**

Read line 49 in context. Current text mentions `/read`. Change `"used by /read and the search index"` to `"used by the chat read path (cat tool) and the search index"`.

- [ ] **Step 7: Em-dash sweep**

```bash
cd /home/edible/Projects/PAL && git diff | grep '^+' | grep -v '^+++' | grep -cP '[\x{2014}\x{2013}]'
```

Expected: `0`.

- [ ] **Step 8: Commit**

`git branch --show-current` must return `main`. Then:

```bash
cd /home/edible/Projects/PAL && git add docs/architecture-flows.md docs/agentic_librarian_summary.md docs/security.md docs/searxng-setup.md docs/agent_ecosystem_direction.md docs/article-format.md && git commit -m "$(cat <<'EOF'
docs: update references to deleted slash commands across 6 files

architecture-flows.md: rewrite Direct Creation, Web Fetch Pipeline,
and Batch Research entrypoints to chat-driven (the downstream
ingestion diagrams stay accurate).
agentic_librarian_summary.md: rewrite Research Mode subsection and
the Web Research Pipeline subsection to chat-driven; fix /compile ->
compile_summary tool reference.
security.md: domain-allowlist line now references the chat-driven
research pipeline instead of /search-web and /fetch.
searxng-setup.md: first sentence updated to reference propose_research
/ research_topic instead of /search-web.
agent_ecosystem_direction.md: PAL command examples updated to
/import, /lint, /learn (the kept commands).
article-format.md: /read reference updated to "chat read path".

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Full verification + push

**Files:** none modified; verification only.

- [ ] **Step 1: Full PAL test suite regression sweep**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/ \
    --ignore=tests/test_chat_research_integration.py \
    --ignore=tests/test_client.py \
    --ignore=tests/test_daemon.py \
    --ignore=tests/test_integration.py \
    --ignore=tests/test_prompt_injection.py \
    -q 2>&1 | tail -5
```

Expected: all pass. Test count is lower than pre-prune (~30+ tests removed from deleted files and trims).

- [ ] **Step 2: Daemon import smoke (defense in depth)**

```bash
cd /home/edible/Projects/PAL && .venv/bin/python -c "from pal.agent import PALAgent; print('ok')"
```

Expected: prints `ok`.

- [ ] **Step 3: Em-dash sweep on the entire diff vs origin/main**

```bash
cd /home/edible/Projects/PAL && git diff origin/main..HEAD | grep '^+' | grep -v '^+++' | grep -cP '[\x{2014}\x{2013}]'
```

Expected: `0` on added lines.

- [ ] **Step 4: Verify branch**

```bash
cd /home/edible/Projects/PAL && git branch --show-current
```

Expected: `main`. STOP if not.

- [ ] **Step 5: Push PAL main**

```bash
cd /home/edible/Projects/PAL && git push origin main 2>&1 | tail -5
```

Expected: the 4 new commits (Task 1 code, Task 2 tests, Task 3 README, Task 4 docs) push to origin.

- [ ] **Step 6: Smoke test plan (user-driven, after server deploy)**

After the user pulls on the server and restarts `pal-daemon` (Discord adapter does NOT need a restart -- no protocol change), they should:

1. **CLI**: `/help` should list only the 14 surviving commands. Typing `/research X` or `/note X` or `/fetch X` etc. should return "Unknown command".
2. **Discord**: `/help` (slash-command surface) similarly shows only surviving commands. Old commands return unknown-command errors.
3. **Chat regression**: "research docker networking" → propose_research → approve → execution runs with per-URL progress. (Verifies the propose_research expansion path is the canonical research surface.)
4. **Compile flow**: "compile the raw summary about <X>" → propose_compile_batch → approve → article created. (Verifies the slash-command chat-equivalent works.)
5. **Promotion flow**: discuss a topic, then "promote that to the wiki" → propose_promote_synthesis → approve → article created.

---

## Self-review checklist (whole plan)

- [ ] Every task has exact file paths and exact commands.
- [ ] Every code edit step shows the actual code (no "do similar thing" handwaves).
- [ ] No "TBD" / "TODO" / "implement later" anywhere.
- [ ] Cross-repo: NO agent_core change. PAL only.
- [ ] No pin bump.
- [ ] No protocol change. No Discord adapter restart.
- [ ] Branch-check reminder in every commit step (memory `feedback_check_branch_before_commit`).
- [ ] `git rm` used for deleted files; `git add` with explicit paths for modified files (NEVER `git add -A` in PAL).
- [ ] All commit messages end with the Co-Authored-By line.
- [ ] No em dashes (U+2014) or en dashes (U+2013) in any commit message or added prompt/comment text.

## Out of scope (intentionally)

- Audit Tier 3 items (slash-command description rewrites: C5, C8, I10, I14, I15, I16) -- most of these are superseded by the deletion. The few that remain (`/learn` description, `/status` description) can be addressed in a follow-up Tier 3 pass or as part of normal command-file touches.
- Audit Theme D process action (phase-transition prompt-touch checklist) -- separate workstream.
- Cleanup of `pal/researcher.py:parse_topic_file` (now unused since `/research` is gone; the chat model handles file parsing via `cat`).
- Stale Chat Tools table entries in README (lists `read_file`, `list_directory`, `search_content`, `search_web` which don't exist or are disabled). Separate fix; pre-existing staleness independent of this prune.
- Tier 1 audit fixes (delete_file contradiction in PAL_BASE_PROMPT, "in the CLI" wording, etc.). Separate work after this lands.
- Tier 2 audit fix (path-parameter standardization). Separate work after this lands.
- Pass 2 prompt audit (synthesis-tool internal prompts). Separate work.
- Server-side deploy (user handles).
