# /think Discord reasoning rendering -- Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render `ResponseMessage.reasoning` in Discord as a spoiler-tag block prepended to the chat answer when `/think` is on, so reasoning is one click away in Discord (matching the visibility the CLI already provides).

**Architecture:** Inline 4 lines in the `ResponseMessage` branch of `DiscordStreamProcessor.run` (`pal/discord_interactions.py:645-647`). When `msg.reasoning` is non-empty after strip, prepend `_Reasoning (click to expand):_\n||<reasoning>||\n\n` to the final text. No helper extraction, no separate test file, no input sanitization (`||` collision is documented as a known v1 limitation; see spec Risks #3).

**Tech Stack:** Python 3.12, pytest, PAL daemon (no agent_core changes, no protocol changes).

**Spec:** `docs/superpowers/specs/2026-05-14-think-discord-render-design.md`

**No agent_core, no Discord adapter restart beyond the daemon's normal reload, no version bump.** PAL git pull + daemon restart only.

---

## File Structure

**PAL repo (`/home/edible/Projects/PAL/`):**
- Modify: `pal/discord_interactions.py:645-647` -- extend the `ResponseMessage` branch (4 added lines).
- Modify: `tests/test_discord_interactions.py` -- add 4 integration tests following the existing `_stream_processor_*` pattern at line 137+.

**No new files. No agent_core changes.**

---

## Task 1: Wire reasoning spoiler into `ResponseMessage` branch + 4 integration tests

**Files:**
- Modify: `/home/edible/Projects/PAL/pal/discord_interactions.py` (4 lines added in the `ResponseMessage` branch)
- Modify: `/home/edible/Projects/PAL/tests/test_discord_interactions.py` (4 new test functions appended)

- [ ] **Step 1: Write the failing integration tests**

Append the following block to the end of `/home/edible/Projects/PAL/tests/test_discord_interactions.py`. The existing file already imports `pytest`, `MagicMock`, `AsyncMock`, the message types, and `DiscordStreamProcessor`. Confirm those imports cover what these tests use; if any are missing, add them at the top of the file.

```python
@pytest.mark.asyncio
async def test_stream_processor_prepends_reasoning_spoiler_to_streamed_answer():
    """Streamed chunks + ResponseMessage with reasoning produces final_text
    with the spoiler block prepended and the streamed answer after."""
    channel = MagicMock()
    bot = MagicMock()
    bot.active_proposals = {}
    client = MagicMock()

    processor = DiscordStreamProcessor(
        channel=channel,
        triggerer_id="user-1",
        bot=bot,
        client=client,
    )

    async def stream():
        yield StreamChunkMessage(token="Hello ")
        yield StreamChunkMessage(token="world")
        yield ResponseMessage(text="", reasoning="step 1\nstep 2")

    _, final_text = await processor.run(stream())
    assert final_text.startswith("_Reasoning (click to expand):_\n||step 1\nstep 2||\n\n")
    assert final_text.endswith("Hello world")


@pytest.mark.asyncio
async def test_stream_processor_prepends_reasoning_to_msg_text_when_no_chunks():
    """No streamed chunks: ResponseMessage(text=..., reasoning=...) produces
    spoiler block + msg.text."""
    channel = MagicMock()
    bot = MagicMock()
    bot.active_proposals = {}
    client = MagicMock()

    processor = DiscordStreamProcessor(
        channel=channel,
        triggerer_id="user-1",
        bot=bot,
        client=client,
    )

    async def stream():
        yield ResponseMessage(text="answer", reasoning="r")

    _, final_text = await processor.run(stream())
    assert final_text == "_Reasoning (click to expand):_\n||r||\n\nanswer"


@pytest.mark.asyncio
async def test_stream_processor_no_reasoning_block_when_empty():
    """Default behavior preserved: reasoning='' produces final_text == answer alone."""
    channel = MagicMock()
    bot = MagicMock()
    bot.active_proposals = {}
    client = MagicMock()

    processor = DiscordStreamProcessor(
        channel=channel,
        triggerer_id="user-1",
        bot=bot,
        client=client,
    )

    async def stream():
        yield ResponseMessage(text="answer", reasoning="")

    _, final_text = await processor.run(stream())
    assert final_text == "answer"


@pytest.mark.asyncio
async def test_stream_processor_no_reasoning_block_when_whitespace_only():
    """Whitespace-only reasoning is treated as empty (pins the .strip() guard)."""
    channel = MagicMock()
    bot = MagicMock()
    bot.active_proposals = {}
    client = MagicMock()

    processor = DiscordStreamProcessor(
        channel=channel,
        triggerer_id="user-1",
        bot=bot,
        client=client,
    )

    async def stream():
        yield ResponseMessage(text="answer", reasoning="\n\n   \n")

    _, final_text = await processor.run(stream())
    assert final_text == "answer"


```

- [ ] **Step 2: Run the new tests, verify they fail**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_discord_interactions.py -k "reasoning_spoiler or reasoning_to_msg_text or no_reasoning_block" -v
```

Expected: 2 of 4 fail. The two `no_reasoning_block_*` tests pass by coincidence (today's behavior already returns the answer alone when reasoning is empty or whitespace); the two prepend tests fail because the current code discards `msg.reasoning`.

- [ ] **Step 3: Modify the `ResponseMessage` branch**

In `/home/edible/Projects/PAL/pal/discord_interactions.py`, locate the existing branch (lines 645-647):

```python
            elif isinstance(msg, ResponseMessage):
                final_text = "".join(text_buffer) if text_buffer else msg.text
                break
```

Replace with:

```python
            elif isinstance(msg, ResponseMessage):
                final_text = "".join(text_buffer) if text_buffer else msg.text
                reasoning = (msg.reasoning or "").strip()
                if reasoning:
                    final_text = f"_Reasoning (click to expand):_\n||{reasoning}||\n\n{final_text}"
                break
```

Four added lines, zero removed. No new imports, no input sanitization. The `or ""` guards against any sender producing `None` (the protocol default is `""`, but cheap insurance). `||` collisions in reasoning content are documented as a known v1 limitation (see spec Risks #3).

- [ ] **Step 4: Run the new tests, verify they pass**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_discord_interactions.py -k "reasoning_spoiler or reasoning_to_msg_text or no_reasoning_block" -v
```

Expected: all 4 pass.

- [ ] **Step 5: Full regression sweep on the modified test file**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/test_discord_interactions.py -v 2>&1 | tail -20
```

Expected: all previously-passing tests still pass (28 existing + 4 new = 32 total). If a pre-existing test asserts `final_text == "<exact answer>"` on a `ResponseMessage` whose `reasoning` happens to be non-empty, that test will need updating; this should not occur because today no test populates `msg.reasoning` (use the Grep tool to confirm: search for `reasoning=` in `tests/test_discord_interactions.py` should show only the new occurrences after this step).

- [ ] **Step 6: Full PAL suite regression sweep**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/ \
    --ignore=tests/test_chat_research_integration.py \
    --ignore=tests/test_client.py \
    --ignore=tests/test_daemon.py \
    --ignore=tests/test_integration.py \
    --ignore=tests/test_prompt_injection.py \
    -q 2>&1 | tail -5
```

Expected: all pass. The five `--ignore` flags match documented pre-existing pal.client collection failures (memory: `project_pal_client_test_cleanup`).

- [ ] **Step 7: Em-dash sweep on the diff**

```bash
cd /home/edible/Projects/PAL && git diff main..HEAD -- pal/discord_interactions.py tests/test_discord_interactions.py | grep -cP '[\x{2014}\x{2013}]'
```

Expected: `0`. If non-zero, locate and replace em dashes with `--` before committing.

- [ ] **Step 8: Commit**

```bash
cd /home/edible/Projects/PAL && git add pal/discord_interactions.py tests/test_discord_interactions.py && git commit -m "$(cat <<'EOF'
feat(discord): render /think reasoning as spoiler-tag block

Extend DiscordStreamProcessor's ResponseMessage branch to prepend
msg.reasoning (when non-empty) to the final text wrapped in a Discord
spoiler tag with an italic header.

Closes the gap that has discarded reasoning content in Discord since
Phase D close-out. Spoiler default keeps every answer visually clean
while letting curious users tap to reveal the reasoning trace.
Per-channel show/hide preference is a separate audit follow-up that
touches agent_core's conv.overrides. `||` collision in reasoning is
documented as a known v1 limitation; sanitization deferred.

Memory: project_reasoning_not_shown_in_discord.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Critical correctness notes

- The change is exactly 4 added lines inside the existing `ResponseMessage` branch. Do NOT modify the `text_buffer` selection logic or the trailing `break`.
- The `.strip()` guard handles both `""` and whitespace-only inputs; do not also add a separate `is None` check (already covered by `or ""`).
- Do NOT add sanitization for `||` in reasoning content. v1 accepts the collision risk; see spec Risks #3.
- No em dashes in any commit message, comment, or added test text. Use `--` if needed.
- No drive-by edits to other tools or files.

## Self-review checklist (whole plan)

- [ ] Single task, 8 steps total.
- [ ] Exact file paths in Files section.
- [ ] Test code shown in full; no "similar to above" placeholders.
- [ ] Implementation code shown in full.
- [ ] No agent_core changes.
- [ ] No protocol changes.
- [ ] No Discord adapter restart called for in deploy notes.
- [ ] No em dashes anywhere.

## Out of scope

- Per-channel show/hide preference (separate audit needs_spec item, touches agent_core).
- `/think show` / `/think hide` working in Discord (depends on per-channel state).
- Streaming reasoning live as it generates.
- Reasoning for slash-command responses (`pal/discord_adapter.py:142-148` bypasses DiscordStreamProcessor).
- Length cap on reasoning content (defer until a real pathology shows up).
- Sending reasoning as a separate Discord message before the answer (fallback if the spoiler-split risk materializes; see spec Risks #1).
- Server-side deploy (user handles deploy on their own cadence; PAL git pull + daemon restart only).
