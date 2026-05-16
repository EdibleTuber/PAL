# /think Discord reasoning rendering -- Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render `ResponseMessage.reasoning` in Discord as a blockquote prepended to the chat answer when /think is on, so reasoning content is visible to Discord users (CLI already does this).

**Architecture:** Add a module-level `_format_reasoning_block(reasoning, max_lines=20)` helper to `pal/discord_interactions.py`. Helper returns a markdown blockquote (`> _Reasoning:_` header + `> `-prefixed lines, truncated at 20 lines). Extend the `ResponseMessage` branch in `DiscordStreamProcessor.run` to call the helper and prepend the block to `final_text` when non-empty.

**Tech Stack:** Python 3.12, pytest, PAL daemon (no agent_core changes, no protocol changes).

**Spec:** `docs/superpowers/specs/2026-05-14-think-discord-render-design.md`

**No agent_core, no Discord adapter restart beyond the daemon's normal reload, no version bump.** PAL git pull + daemon restart only.

---

## File Structure

**PAL repo (`/home/edible/Projects/PAL/`):**
- Modify: `pal/discord_interactions.py` -- add `_format_reasoning_block` module-level helper; extend `DiscordStreamProcessor.run` `ResponseMessage` branch (around line 645-647) to call it.
- Create: `tests/test_discord_reasoning_render.py` -- new file for the 7 helper unit tests + 2 integration tests. Existing `tests/test_discord_interactions.py` is already 513 lines / 28 tests; keep new tests in their own file for focus.

**No other files.**

---

## Task 1: Add `_format_reasoning_block` helper

**Files:**
- Modify: `/home/edible/Projects/PAL/pal/discord_interactions.py` (add module-level helper)
- Create: `/home/edible/Projects/PAL/tests/test_discord_reasoning_render.py` (7 unit tests)

- [ ] **Step 1: Write failing tests**

Create `/home/edible/Projects/PAL/tests/test_discord_reasoning_render.py`:

```python
"""Tests for Discord reasoning render helper and stream-processor integration.

Covers _format_reasoning_block (unit) and DiscordStreamProcessor.run's
ResponseMessage branch behavior when msg.reasoning is non-empty (integration).
"""
from __future__ import annotations

from pal.discord_interactions import _format_reasoning_block


def test_format_reasoning_block_short_text():
    """Single-line input produces header + one quoted line."""
    result = _format_reasoning_block("step 1")
    assert result == "> _Reasoning:_\n> step 1"


def test_format_reasoning_block_multi_line():
    """Three-line input produces header + three quoted lines, no truncation marker."""
    result = _format_reasoning_block("step 1\nstep 2\nstep 3")
    expected = "> _Reasoning:_\n> step 1\n> step 2\n> step 3"
    assert result == expected


def test_format_reasoning_block_truncates_at_20_lines():
    """30-line input returns header + 20 lines + truncation marker."""
    input_lines = [f"line {i}" for i in range(30)]
    result = _format_reasoning_block("\n".join(input_lines))
    lines = result.splitlines()
    # 1 header + 20 reasoning lines + 1 truncation marker = 22 lines total
    assert len(lines) == 22
    assert lines[0] == "> _Reasoning:_"
    assert lines[1] == "> line 0"
    assert lines[20] == "> line 19"
    assert lines[21] == "> _... (truncated; full reasoning in debug log)_"


def test_format_reasoning_block_handles_empty_lines():
    """Blank lines within input become '> ' (greater-than space) lines, not omitted."""
    result = _format_reasoning_block("para 1\n\npara 2")
    expected = "> _Reasoning:_\n> para 1\n> \n> para 2"
    assert result == expected


def test_format_reasoning_block_empty_string_returns_empty():
    """Empty input returns empty string so caller can skip the prepend."""
    assert _format_reasoning_block("") == ""


def test_format_reasoning_block_whitespace_only_returns_empty():
    """Whitespace-only input returns empty string."""
    assert _format_reasoning_block("\n\n   \n") == ""


def test_format_reasoning_block_preserves_special_chars():
    """Lines with markdown-like chars (*, _, #, backticks) render inside blockquote."""
    input_text = "*not a list*\n# not a heading\n`not code`"
    result = _format_reasoning_block(input_text)
    expected = "> _Reasoning:_\n> *not a list*\n> # not a heading\n> `not code`"
    assert result == expected
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_discord_reasoning_render.py -v
```

Expected: FAIL with `ImportError: cannot import name '_format_reasoning_block' from 'pal.discord_interactions'`.

- [ ] **Step 3: Add the helper to `pal/discord_interactions.py`**

Find a location near the top of `pal/discord_interactions.py` (after the imports and module-level constants, before the `DiscordStreamProcessor` class definition around line 525). Add:

```python
def _format_reasoning_block(reasoning: str, max_lines: int = 20) -> str:
    """Render reasoning content as a Discord blockquote.

    Returns a multi-line string starting with `> _Reasoning:_` followed by
    `> `-prefixed lines. Truncates at `max_lines` lines, appending a
    truncation marker when cut. Returns an empty string when reasoning
    is empty or whitespace-only.

    Used by DiscordStreamProcessor.run to render /think reasoning content
    that is otherwise discarded on the Discord side.
    """
    if not reasoning or not reasoning.strip():
        return ""

    lines = reasoning.splitlines()
    truncated = len(lines) > max_lines
    if truncated:
        lines = lines[:max_lines]

    quoted = ["> _Reasoning:_"]
    for line in lines:
        # Preserve blank lines as "> " (greater-than then space) so
        # paragraph breaks survive inside the blockquote.
        quoted.append(f"> {line}" if line else "> ")
    if truncated:
        quoted.append("> _... (truncated; full reasoning in debug log)_")

    return "\n".join(quoted)
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_discord_reasoning_render.py -v
```

Expected: all 7 unit tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/discord_interactions.py tests/test_discord_reasoning_render.py && git commit -m "$(cat <<'EOF'
feat(discord): add _format_reasoning_block helper

Module-level helper in pal/discord_interactions.py. Renders reasoning
text as a Discord blockquote with italic header (`> _Reasoning:_`),
each line prefixed `> `, blank lines preserved as `> ` to keep
paragraph breaks. Truncates at 20 lines (matches the CLI's
slash-command cap) and appends a truncation marker when cut. Returns
empty string on empty/whitespace input so the caller can skip the
prepend cleanly.

Will be wired into DiscordStreamProcessor.run's ResponseMessage branch
in the next task to close the gap that has discarded reasoning content
in Discord since Phase D close-out (memory: project_reasoning_not_shown_in_discord).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Critical correctness notes

- The header uses underscores for italic: `> _Reasoning:_`. Discord renders `_text_` as italic in desktop and web clients; some mobile clients may render literally, but the text is still readable.
- Blank lines preserved as `> ` (greater-than then SPACE), NOT just `>`. Discord renders empty blockquote lines as a small visual gap; without the trailing space the parser sometimes collapses them.
- Truncation marker uses literal `...` (three ASCII dots), NOT U+2026 ellipsis. Discord's mobile fonts sometimes render the Unicode ellipsis at a different size; three dots are universally consistent.
- The helper is at module level so it can be tested without instantiating `DiscordStreamProcessor`.
- No em dashes in commit message or any added comments.

## Self-review checklist

- All 7 unit tests pass
- Helper signature is `_format_reasoning_block(reasoning: str, max_lines: int = 20) -> str`
- Helper at module level in `pal/discord_interactions.py`
- New test file created (not added to existing 513-line test_discord_interactions.py)
- Commit message has no em dashes

---

## Task 2: Wire helper into ResponseMessage branch + integration tests + regression sweep

**Files:**
- Modify: `/home/edible/Projects/PAL/pal/discord_interactions.py` (extend `DiscordStreamProcessor.run` `ResponseMessage` branch around line 645-647)
- Modify: `/home/edible/Projects/PAL/tests/test_discord_reasoning_render.py` (add 2 integration tests)

- [ ] **Step 1: Write failing integration tests**

Append to `/home/edible/Projects/PAL/tests/test_discord_reasoning_render.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

from agent_core.protocol.messages import ResponseMessage
from pal.discord_interactions import DiscordStreamProcessor


def _stream_with_response(response_msg: ResponseMessage):
    """Build an async iterator that yields exactly one ResponseMessage.

    Mirrors the shape DiscordStreamProcessor.run consumes (the chat()
    async generator from the daemon).
    """
    async def gen():
        yield response_msg
    return gen()


@pytest.mark.asyncio
async def test_discord_processor_prepends_reasoning_to_final_text():
    """ResponseMessage with reasoning produces final_text starting with the
    reasoning block, then a blank line, then the answer."""
    client = MagicMock()
    client.chat = MagicMock(return_value=_stream_with_response(
        ResponseMessage(text="answer", reasoning="step 1\nstep 2")
    ))
    processor = DiscordStreamProcessor(client=client, thread=MagicMock())
    # _post_progress_to_thread is awaited inside the loop for ProgressMessages
    # only; not relevant here since we only yield a ResponseMessage.
    processor._post_progress_to_thread = AsyncMock()
    _, final_text = await processor.run(channel_id="123", text="hello")
    assert final_text.startswith("> _Reasoning:_\n> step 1\n> step 2")
    assert final_text.endswith("\n\nanswer")


@pytest.mark.asyncio
async def test_discord_processor_no_reasoning_block_when_field_empty():
    """ResponseMessage with empty reasoning produces final_text equal to the
    answer alone (no leading blockquote, no leading blank line)."""
    client = MagicMock()
    client.chat = MagicMock(return_value=_stream_with_response(
        ResponseMessage(text="answer", reasoning="")
    ))
    processor = DiscordStreamProcessor(client=client, thread=MagicMock())
    processor._post_progress_to_thread = AsyncMock()
    _, final_text = await processor.run(channel_id="123", text="hello")
    assert final_text == "answer"
```

Note: the exact `DiscordStreamProcessor.__init__` signature may differ from `(client, thread)`. Read the actual constructor first (around line 525-540 in `pal/discord_interactions.py`) and adapt the test fixtures to match. If `run` takes a different argument shape than `(channel_id, text)`, match that too.

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_discord_reasoning_render.py -k "discord_processor" -v
```

Expected: FAIL. The first test fails because `final_text` is currently just `"answer"` (the reasoning is discarded). The second test passes by coincidence today (since today's behavior IS to ignore reasoning).

- [ ] **Step 3: Modify `DiscordStreamProcessor.run`'s ResponseMessage branch**

In `/home/edible/Projects/PAL/pal/discord_interactions.py`, locate the existing branch (around line 645-647):

```python
            elif isinstance(msg, ResponseMessage):
                final_text = "".join(text_buffer) if text_buffer else msg.text
                break
```

Replace with:

```python
            elif isinstance(msg, ResponseMessage):
                final_text = "".join(text_buffer) if text_buffer else msg.text
                block = _format_reasoning_block(msg.reasoning)
                if block:
                    final_text = f"{block}\n\n{final_text}"
                break
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_discord_reasoning_render.py -v
```

Expected: all 9 tests pass (7 unit + 2 integration).

- [ ] **Step 5: Full-suite regression sweep**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/ \
    --ignore=tests/test_chat_research_integration.py \
    --ignore=tests/test_client.py \
    --ignore=tests/test_daemon.py \
    --ignore=tests/test_integration.py \
    --ignore=tests/test_prompt_injection.py \
    -q 2>&1 | tail -5
```

Expected: all pass. The five `--ignore` flags match documented pre-existing pal.client collection failures.

If any non-preexisting test fails, the likely cause is a test in `tests/test_discord_interactions.py` that asserts on the exact text of a `ResponseMessage`-driven `final_text`. Update those tests if the assertion was strict equality (`==`) on text that did NOT include reasoning today, by asserting `result.endswith(<expected_text>)` or parsing the text to confirm answer + optional prepend.

- [ ] **Step 6: Em-dash sweep**

```bash
cd /home/edible/Projects/PAL && git diff main..HEAD | grep -P '[\x{2014}\x{2013}]' || echo "no em dashes in PAL diff"
```

Should print "no em dashes in PAL diff".

- [ ] **Step 7: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/discord_interactions.py tests/test_discord_reasoning_render.py && git commit -m "$(cat <<'EOF'
feat(discord): prepend reasoning block to ResponseMessage final_text

Wires the _format_reasoning_block helper into
DiscordStreamProcessor.run's ResponseMessage branch. When
msg.reasoning is non-empty, the rendered blockquote prepends the
answer with a blank line separator; when empty, behavior is unchanged.

Two integration tests pin: (a) reasoning present produces blockquote
+ blank line + answer, (b) reasoning absent produces answer alone.

Closes the gap that has discarded reasoning content in Discord since
Phase D close-out (memory: project_reasoning_not_shown_in_discord).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Critical correctness notes

- The change is exactly four lines: capture block, check truthiness, conditional prepend, leave `break` in place. Do NOT modify the `text_buffer` logic that picks between streamed chunks and `msg.text`.
- The integration tests construct a fake one-message async iterator. Read the actual `DiscordStreamProcessor.run` signature before naming arguments in the test; adapt if `run` does not take `(channel_id, text)`.
- If the existing `test_discord_interactions.py` has tests asserting `final_text == "<exact answer>"` with a `ResponseMessage` that today happens to have empty `reasoning`, those tests still pass (the helper returns empty string for empty reasoning). They only break if the test explicitly populates `msg.reasoning` and asserts on bare-answer equality, which is unlikely given current Discord behavior.
- No em dashes anywhere.

## Self-review checklist

- All 9 tests pass (7 unit + 2 integration)
- Full PAL suite passes (modulo documented pre-existing failures)
- The `ResponseMessage` branch change is minimal (4 added lines, 0 removed)
- The branch still calls `break` to exit the message loop
- No em dashes
- No drive-by edits to other tools

---

## Self-review checklist (whole plan)

- [ ] Every task has Files section with exact paths.
- [ ] Every test step shows the assertion code.
- [ ] Every implementation step shows the actual code change.
- [ ] No "TBD", "TODO", "implement later" anywhere.
- [ ] Names used in later tasks match earlier tasks (`_format_reasoning_block`, signature, output format).
- [ ] No agent_core changes.
- [ ] No protocol changes.
- [ ] No Discord adapter restart called for in deploy notes.
- [ ] Helper test coverage and integration test coverage are both present.
- [ ] All commit messages end with the Co-Authored-By line.
- [ ] No em dashes in any commit message or added prompt/comment text.

## Out of scope

- Per-channel show/hide preference (separate audit needs_spec item).
- `/think show` / `/think hide` working in Discord (depends on per-channel state).
- Streaming reasoning live as it generates.
- Reasoning for slash-command responses (separate code path).
- Smart truncation (word boundaries, sentence boundaries).
- Character-count cap (currently line-count only).
- Thread-based reasoning render (considered during brainstorm; deferred as follow-up if blockquote proves noisy in real chat scrollback).
- Server-side deploy (the user handles deploy on their own cadence; PAL git pull + daemon restart only).
