# Discord reasoning rendering for /think

**Date:** 2026-05-14
**Status:** Design
**Author:** Brainstormed with Claude
**Audit:** `docs/superpowers/audits/2026-05-11-tool-audit-report.md` (slash-commands category, the `/think` Discord reasoning rendering needs_spec item)
**Related memory:** `project_reasoning_not_shown_in_discord`

## Problem

PAL emits reasoning content on `ResponseMessage.reasoning` when reasoning mode is active. The CLI renders this content as a dim-italic block prepended to each answer (`pal/cli.py:327-333`). Discord does not. The Discord stream processor at `pal/discord_interactions.py:645-647` reads `msg.text` only and discards `msg.reasoning` entirely.

This was first reported during Phase D close-out (2026-04-30) and tracked in the `project_reasoning_not_shown_in_discord` memory. The user has been living with the gap for several weeks: `/think on` in Discord toggles reasoning mode correctly, the daemon emits the reasoning, but Discord users only see the final answer.

This spec closes the rendering side of that gap. PAL will render `reasoning` whenever it appears, in a Discord-friendly blockquote format, with a length cap to stay within Discord's 2000-character message limit.

## Goals

1. When `ResponseMessage.reasoning` is non-empty, prepend it to the Discord-rendered final text.
2. Use a format that distinguishes reasoning from the answer (blockquote + italic header).
3. Cap the rendered length so a long reasoning block plus a long answer cannot exceed Discord's 2000-character message limit on its own (the rest of the budget is for the answer).
4. Extract the formatting into a module-level helper that is unit-testable in isolation.
5. No agent_core changes. No protocol changes. No new dependencies.

## Non-goals

1. **Per-channel show/hide preference** in Discord. The CLI has a process-global `_reasoning_display` (`pal/cli.py:39`) and `/think show` / `/think hide` mutate it. Translating that to Discord requires per-channel state via `conv.overrides["reasoning_display"]`, which is a separate audit needs_spec item that touches agent_core. This spec always renders reasoning when present.
2. **Streaming reasoning live as it generates.** Discord does not support cheap live edits to messages; reasoning lands as one block at the end. The chat answer itself is also delivered as a single message in PAL's current Discord adapter, so this matches the existing UX.
3. **Reasoning for slash-command responses.** Slash commands flow through `_run_command` rather than the chat path. The slash-command render path is separate and not currently broken (the CLI handles them; Discord slash-commands are a smaller surface). Defer to a follow-up if needed.
4. **Changing `/think on` / `/think off` behavior.** The toggle already works; this spec is purely about rendering what the toggle produces.
5. **Truncation policy beyond a hard line cap.** No word-boundary cleverness or smart summarization. If reasoning exceeds the cap, the tail is dropped and a marker appended.

## Render format

When `ResponseMessage.reasoning` is non-empty, prepend this block (before the chat answer, separated by a blank line):

```
> _Reasoning:_
> First step of PAL's reasoning...
> Second step...
> Third step...

<final answer goes here>
```

Format details:
- First line is `> _Reasoning:_` (markdown italic via underscores). Self-identifying header.
- Each subsequent line is the original reasoning line prefixed with `> ` (two characters: greater-than, space).
- Empty lines within the reasoning become `> ` (greater-than then space, no content). Preserves intentional paragraph breaks inside the blockquote.
- One blank line (`\n\n`) separates the reasoning block from the chat answer.
- The Discord blockquote convention matches PAL's existing chat-derived article banner, so PAL has one visual style for "context distinct from the main content."

## Length cap

Cap the rendered reasoning at **20 lines** (after the `> _Reasoning:_` header). If the input has more lines:
- Take the first 20 reasoning lines.
- Append one final blockquote line: `> _... (truncated; full reasoning in debug log)_`
- Do not also truncate the answer; the answer takes its own share of the 2000-char budget.

Why 20 lines: matches the CLI's cap for slash-command reasoning (`pal/cli.py:329-331`). Average reasoning line is short (15-40 chars), so 20 lines plus header plus truncation marker is roughly 600-1000 chars, leaving 1000+ chars for the answer within Discord's 2000-char limit. If both reasoning and answer are unusually long, Discord will refuse the message with a 400 from the Discord API, which the existing stream processor already handles (no change needed here).

Line count is computed on `reasoning.splitlines()` so trailing newlines do not inflate the count.

## Helper API

New module-level helper in `pal/discord_interactions.py`:

```python
def _format_reasoning_block(reasoning: str, max_lines: int = 20) -> str:
    """Render reasoning content as a Discord blockquote.

    Returns a multi-line string starting with `> _Reasoning:_` followed by
    `> `-prefixed lines. Truncates at `max_lines` lines, appending a
    truncation marker when cut. Returns an empty string when reasoning
    is empty or whitespace-only.
    """
```

Called from the `ResponseMessage` branch in `DiscordStreamProcessor.run`:

```python
elif isinstance(msg, ResponseMessage):
    final_text = "".join(text_buffer) if text_buffer else msg.text
    block = _format_reasoning_block(msg.reasoning)
    if block:
        final_text = f"{block}\n\n{final_text}"
    break
```

The helper handles the "empty reasoning" case so the caller stays one-liner clean.

## Empty / whitespace input handling

- `reasoning == ""` → helper returns `""`, caller skips the prepend.
- `reasoning` consists only of whitespace/newlines → helper returns `""` (treats as empty).
- Single-line reasoning → header + one blockquote line + blank-line separator before answer.
- Lines containing markdown that could confuse Discord (e.g. lines starting with `#` or `*`) → the `> ` prefix is sufficient; Discord renders the whole line as blockquote content, not as a heading or list inside the quote.

## Tests

In `tests/test_discord_interactions.py` (or a new `tests/test_discord_reasoning_render.py` if the existing file is large):

- `test_format_reasoning_block_short_text` -- single-line input produces header + one quoted line. Asserts exact output.
- `test_format_reasoning_block_multi_line` -- three-line input produces header + three quoted lines, no truncation marker.
- `test_format_reasoning_block_truncates_at_20_lines` -- 30-line input returns header + 20 lines + truncation marker. Total line count is 22.
- `test_format_reasoning_block_handles_empty_lines` -- input with blank lines between paragraphs produces `> ` (greater-than space) lines for the blank parts, not omitted.
- `test_format_reasoning_block_empty_string_returns_empty` -- empty input returns empty string.
- `test_format_reasoning_block_whitespace_only_returns_empty` -- input of `\n\n   \n` returns empty string.
- `test_format_reasoning_block_preserves_special_chars` -- input containing `*`, `_`, `#`, backticks renders inside the blockquote without being interpreted as Discord markdown structure.
- `test_discord_processor_prepends_reasoning_to_final_text` -- integration test: a `ResponseMessage` with `reasoning="step 1\nstep 2"` and `text="answer"` produces a `final_text` that starts with the reasoning block and ends with `\n\nanswer`.
- `test_discord_processor_no_reasoning_block_when_field_empty` -- `ResponseMessage` with `reasoning=""` produces `final_text` equal to the answer alone.

## Behavior preservation

- `ResponseMessage.text` rendering is unchanged.
- `StreamChunkMessage` accumulation is unchanged.
- `ErrorMessage` handling is unchanged.
- `ProgressMessage` and proposal-message routing are unchanged.
- The 2000-char Discord message limit is not enforced inside `_format_reasoning_block`; if the combined block exceeds it, existing Discord-API error handling fires (same behavior as today when a long answer alone exceeds the limit).

## Migration / back-compat

- No protocol changes; existing callers and stored conversation history are unaffected.
- No agent_core version bump.
- No Discord adapter restart required for any other reason (this lands as part of the PAL daemon code).
- After deploy: every existing chat in `/think on` mode will now show reasoning; chats with `/think off` (default for most channels) are unaffected since `reasoning` is empty there.

## Risks

1. **Reasoning content exceeds Discord's 2000-char limit even with the 20-line cap.** Cap is on line count, not character count. A pathological reasoning with twenty 200-char lines plus answer could exceed the limit. Mitigation: existing error handling at the Discord API layer fires; user sees the message-failed error. Acceptable for v1; if it becomes a real problem, add a char cap to the helper.
2. **The `> _Reasoning:_` header makes assumptions about Discord's italic rendering.** Discord renders `_text_` as italic in most contexts but inside blockquote some clients may render it literally. The header still reads correctly even if italic does not apply. Acceptable.
3. **Some lines of reasoning may already start with `>` characters** (e.g. PAL quoting something in its own reasoning). The blockquote prefix becomes `> >`, which Discord renders as a nested blockquote. Acceptable visual nesting; no information loss.
4. **No per-channel hide.** Users on busy Discord channels who do not want to see reasoning have to either toggle `/think off` (loses reasoning entirely) or move to a different channel. The follow-up spec for per-channel show/hide closes this gap.

## Verification

- PAL test suite passes (helper unit tests + integration tests for the stream processor branch).
- Manual smoke after deploy: in a Discord channel with `/think on`, send a chat message that triggers reasoning. Confirm the reasoning block appears before the answer, formatted as a blockquote.
- Manual smoke: send the same message with `/think off`. Confirm no reasoning block appears (the field is empty so the helper returns empty).
- Manual smoke: trigger a long reasoning response (e.g. an architectural question). Confirm truncation marker appears when reasoning exceeds 20 lines.

## Out of scope

- Per-channel show/hide preference (next audit needs_spec item).
- `/think show` / `/think hide` working in Discord (depends on per-channel state).
- Streaming reasoning live as it generates.
- Reasoning for slash-command responses (`_run_command` path, separate from chat path).
- Smart truncation (word boundaries, sentence boundaries).
- Character-count cap (currently line-count only).
- Changing `/think on` / `/think off` behavior.
