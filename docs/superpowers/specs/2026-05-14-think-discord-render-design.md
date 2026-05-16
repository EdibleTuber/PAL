# Discord reasoning rendering for /think

**Date:** 2026-05-14
**Status:** Design (revised after panel review)
**Author:** Brainstormed with Claude
**Audit:** `docs/superpowers/audits/2026-05-11-tool-audit-report.md` (slash-commands category, the `/think` Discord reasoning rendering needs_spec item)
**Related memory:** `project_reasoning_not_shown_in_discord`

## Revision history

- **v1 (initial):** blockquote format, 20-line cap, module-level helper, 9 tests.
- **v2 (this version, after 4-reviewer panel):** spoiler-tag format, no cap, inline at call site, 4-5 tests. Drops factually-wrong claim about Discord 400 (the adapter paginates via `split_message`). Drops dishonest "in debug log" truncation marker. Documents real risks (spoiler tag breakage on message split, `||` collision in reasoning content). Explicitly cites the slash-command path that remains uncovered.

## Problem

PAL emits reasoning content on `ResponseMessage.reasoning` when reasoning mode is active. The CLI renders this content as a dim-italic block prepended to each answer (`pal/cli.py:327-333`). Discord does not. The Discord stream processor at `pal/discord_interactions.py:645-647` reads `msg.text` only and discards `msg.reasoning` entirely.

Reported during Phase D close-out (2026-04-30) and tracked in the `project_reasoning_not_shown_in_discord` memory. The 3-week gap before fixing is itself signal: this is a quality-of-life fix, not a critical path. So v1 should be the smallest viable shape with the lightest visual footprint.

This spec closes the rendering side of that gap. PAL will render `reasoning` whenever it appears, wrapped in a Discord spoiler tag so the answer stays visually primary and the user opts in to read the reasoning by clicking to expand.

## Goals

1. When `ResponseMessage.reasoning` is non-empty, prepend it to the Discord-rendered final text wrapped in a spoiler tag.
2. Use a format that keeps the answer visually primary (spoiler defaults to collapsed) and signals what is hidden (header above the spoiler).
3. No agent_core changes. No protocol changes. No new dependencies.
4. Inline the change at the call site (no module-level helper); the formatting is a handful of lines and has exactly one caller.

## Non-goals

1. **Per-channel show/hide preference** in Discord. The CLI has a process-global `_reasoning_display` (`pal/cli.py:39`) and `/think show` / `/think hide` mutate it. Translating that to Discord requires per-channel state via `conv.overrides["reasoning_display"]`, which is a separate audit needs_spec item that touches agent_core. The spoiler default partly mitigates the need; defer the full preference to its own spec.
2. **Streaming reasoning live as it generates.** The Discord adapter posts each message after the full stream completes (`pal/discord_adapter.py:151-175`); reasoning lands as one block at the end, same as the chat answer.
3. **Reasoning for slash-command responses.** Slash commands flow through `pal/discord_adapter.py:142-148` (`if parsed[0] == "command":`), which calls `client.command(...)` and reads `resp.text` directly. That path completely bypasses `DiscordStreamProcessor` and is not touched by this spec. Defer to a follow-up if needed.
4. **Changing `/think on` / `/think off` behavior.** The toggle already works.
5. **Length cap on reasoning content.** No truncation. Reasoning content from PAL is naturally bounded by the model; if a pathological case shows up where the combined message exceeds 2000 chars, the existing `split_message` adapter paginates. The spoiler tag may break across messages in that case (see Risks below); add a cap then, informed by real data.

## Render format

When `ResponseMessage.reasoning` is non-empty (after stripping), prepend this block to the chat answer:

```
_Reasoning (click to expand):_
||<reasoning content>||

<final answer>
```

Format details:
- Header line: `_Reasoning (click to expand):_` (markdown italic via underscores). Sits outside the spoiler so the user knows what they are about to reveal.
- Spoiler body: the reasoning content verbatim, surrounded by `||` on each side. Newlines within the reasoning are preserved as-is; Discord renders them inside the spoiler.
- One blank line (`\n\n`) separates the spoiler block from the chat answer.
- Default Discord rendering: the header is visible italic text; the `||...||` block renders as a clickable dark bar showing "SPOILER" (desktop) or "Tap to reveal" (mobile).

## Wire-in

The change is inline in the `ResponseMessage` branch of `DiscordStreamProcessor.run` at `pal/discord_interactions.py:645-647`:

```python
elif isinstance(msg, ResponseMessage):
    final_text = "".join(text_buffer) if text_buffer else msg.text
    reasoning = (msg.reasoning or "").strip()
    if reasoning:
        final_text = f"_Reasoning (click to expand):_\n||{reasoning}||\n\n{final_text}"
    break
```

Four added lines; zero removed. No helper, no separate module, no new imports, no input sanitization. The `or ""` guards against any sender producing `None` instead of `""` (the protocol default is `""`, but cheap insurance).

## Tests

Add to the existing `tests/test_discord_interactions.py` (follows the established `_stream_processor_*` pattern at line 137+):

1. **`test_stream_processor_prepends_reasoning_spoiler_to_streamed_answer`** -- streamed chunks (`text_buffer` non-empty) + `ResponseMessage(text="", reasoning="step 1\nstep 2")` produces `final_text` that starts with the header line, contains the spoiler body, and ends with the streamed answer.
2. **`test_stream_processor_prepends_reasoning_to_msg_text_when_no_chunks`** -- no `StreamChunkMessage`s + `ResponseMessage(text="answer", reasoning="r")` produces `final_text == "_Reasoning (click to expand):_\n||r||\n\nanswer"`.
3. **`test_stream_processor_no_reasoning_block_when_empty`** -- `ResponseMessage(text="answer", reasoning="")` produces `final_text == "answer"` (no change from current behavior). Pins the regression-free path.
4. **`test_stream_processor_no_reasoning_block_when_whitespace_only`** -- `reasoning="\n\n   \n"` is treated as empty. Pins the `.strip()` guard.

Four tests. All integration tests against `DiscordStreamProcessor.run`. No unit tests for an extracted helper because there is no helper.

## Behavior preservation

- `ResponseMessage.text` rendering is unchanged when `reasoning == ""` (the default).
- `StreamChunkMessage` accumulation is unchanged.
- `ErrorMessage` handling is unchanged.
- `ProgressMessage` and proposal-message routing are unchanged.

## Migration / back-compat

- No protocol changes; existing callers and stored conversation history are unaffected.
- No agent_core version bump.
- No Discord adapter restart required for any other reason (this lands as PAL daemon code only).
- After deploy: every existing chat in `/think on` mode will now show a spoiler block; chats with `/think off` (default for most channels) are unaffected since `reasoning` is empty there.

## Risks

1. **Spoiler tag may break when message is split.** `pal/discord_adapter.py:174` calls `agent_core.adapters.discord_gateway.split_message` if the rendered message exceeds 2000 chars. If a long reasoning + long answer combination triggers a split mid-spoiler, the closing `||` ends up in the second chunk and the spoiler tag becomes malformed. Mitigation: do nothing in v1. Reasoning content is naturally bounded; the case is rare. If it shows up, add a char cap or split the message manually (reasoning as a separate Discord message before the answer).
2. **Italic header rendering varies on mobile.** Discord desktop and web render `_text_` as italic reliably. Older iOS Discord clients sometimes render the underscores literally. The header still reads correctly either way ("`_Reasoning (click to expand):_`" is still a complete instruction). Acceptable.
3. **Reasoning content containing `||`.** A `||` sequence inside the reasoning body would prematurely close the spoiler tag and render the rest as plain text. Accepted as a known limitation in v1: LLM-generated reasoning essentially never contains `||`. If real usage shows the pattern, sanitize on a later iteration (the obvious fix has a trailing-pipe edge case that requires more care than this spec wants to spend).
4. **Spoiler-tag opt-in vs always-visible blockquote.** A spoiler defaults to collapsed; users who do want to see reasoning every time have to click. This is the trade-off for keeping the chat surface clean. If real use shows the click is annoying, the easy follow-up is the per-channel show/hide preference (next audit needs_spec item).
5. **Slash-command reasoning still hidden.** The `pal/discord_adapter.py:142-148` slash-command path bypasses `DiscordStreamProcessor` and remains unchanged. Documented as non-goal; deferred.

## Verification

- PAL test suite passes (5 new integration tests + no regressions).
- Manual smoke after deploy: in a Discord channel with `/think on`, send a chat message that triggers reasoning. Confirm the spoiler block appears before the answer; clicking expands the reasoning content.
- Manual smoke: same message with `/think off`. Confirm no spoiler block appears (the field is empty so the guard short-circuits).
- Manual smoke (mobile if available): verify spoiler tap-to-reveal works and the italic header renders or at least reads correctly.

## Out of scope

- Per-channel show/hide preference (next audit needs_spec item).
- `/think show` / `/think hide` working in Discord (depends on per-channel state).
- Streaming reasoning live as it generates.
- Reasoning for slash-command responses (`pal/discord_adapter.py:142-148` path).
- Length cap on reasoning content.
- Sending reasoning as a separate Discord message before the answer (fallback option if the spoiler-split risk materializes).
- Changing `/think on` / `/think off` behavior.
