# Inference Safety Guards Design

**Status:** Approved 2026-04-28
**Triggered by:** 2026-04-28 incident where the model fell into an "I'll call the tool" narration loop for ~18 minutes without emitting any tool calls. PAL's daemon stayed unresponsive after the stream ended; the user's `@PAL stop` message was rejected with "A previous turn is still being processed."
**Scope:** Phase 1 only. A deeper investigation into root causes (model behavior, prompt confusion, tool-description ambiguity, status-line UX) is deferred to Phase 2 after all eight `agent_core` extraction phases complete. See memory `project_phase2_inference_investigation.md`.

## Goal

Bound the worst case of model misbehavior in PAL's inference path and make user cancellation work. Specifically:

1. Cap response length via `max_tokens` so a stream cannot run for more than ~7 minutes worst-case.
2. Replace the "reject new messages on busy channel" guard with preemption: a new chat or command on a channel cancels any in-flight turn.
3. Emit one structured log line per turn so future investigation has data to work with.

## Non-goals

- Repetition detection in the streaming path (deferred to Phase 2).
- Wall-clock per-stream timeout (deferred to Phase 2).
- Tool-narration heuristic (deferred to Phase 2).
- Root-cause investigation of why the model loses its way (deferred to Phase 2).
- CLI status-line UX showing model + context fill (deferred to Phase 2).
- Server-side changes (the inference server isn't modified).

## Architecture

Three layers of change:

1. **`agent_core/agent_core/inference.py`**: `complete()` and `stream()` accept a new `max_tokens: int | None = None` parameter that flows into the request payload. `stream()` reads each chunk's `finish_reason` and yields a final `StreamEnd(finish_reason, chunks_yielded)` sentinel before returning. `StreamEnd` is a new public dataclass.
2. **`pal/daemon.py`**: The per-connection `current_chat_task` becomes a per-channel `_chat_tasks: dict[str, asyncio.Task]`. New chat or command messages on a channel preempt any in-flight task on that channel. `_handle_chat` consumes the new `StreamEnd` sentinel, catches `asyncio.CancelledError`, and emits a structured `chat_turn_ended` log at exit. Aborted partials are not appended to the in-memory conversation; the JSONL persistence file gets one forensic line per abort.
3. **`pal/config.py`**: New field `max_response_tokens: int = 4096` on `BaseConfig`, overridable via `PAL_MAX_RESPONSE_TOKENS` env var or config file.

`agent_core.channels.ChannelStore._replay_into` (which is being moved from PAL in Phase D, currently still at `pal/channels.py`) needs a small update to skip JSONL records whose `role` is not `user`, `assistant`, or `tool`. New abort records use `role: "abort"` and must be skipped on replay.

## Decisions log

| # | Question | Decision | Rationale |
|---|---|---|---|
| 1 | How does the user signal stop mid-turn? | New chat or command preempts in-flight turn (replaces "reject on busy" guard). | Discord doesn't have a clean slash-command mechanism mid-stream; plain text is the natural Discord stop signal. The current rejection is what made `@PAL stop` invisible. |
| 2 | What happens to the partial assistant message on abort? | Always drop from in-memory history. Forensic line written to JSONL. | Predictable. Avoids degenerate partial output (e.g., the narration loop) influencing the next turn. JSONL preserves data for post-mortem. |
| 3 | Where does `max_tokens` live and what value? | New parameter on `agent_core.inference.complete/stream`, defaulting to `None`. PAL config carries the actual policy value `max_response_tokens=4096`. | `agent_core` stays opinion-free for other consumers. PAL's policy is tunable without code changes. 4096 chosen as comfortable headroom over typical chat lengths but small enough to bound damage. |
| 4 | How does the daemon detect a new message in the same channel mid-stream? | Per-channel task registry; new message calls `_preempt_existing_turn(channel_id)` which `task.cancel()`s the in-flight task and `wait_for(..., timeout=2.0)` for unwind. | Standard asyncio idiom. `httpx.aclose()` triggered by the async-context-manager unwinds the stream. Doesn't couple cancellation to the Conversation object (which is moving to agent_core in Phase D). |
| 5 | How do we capture the precise reason a stream ended? | `agent_core.inference.stream()` yields a final `StreamEnd(finish_reason, chunks_yielded)` sentinel. Daemon catches it and logs. | Distinguishes "model said all it had to say" from "server cut it off at max_tokens" cleanly. Future Phase 2 work (repetition, narration heuristic) needs this precise signal anyway. |

## `agent_core.inference` changes

`StreamEnd` dataclass (new, exported):

```python
@dataclass
class StreamEnd:
    finish_reason: str   # "stop" | "length" | "tool_calls" | "content_filter" | "unknown"
    chunks_yielded: int
```

`complete()` signature:

```python
async def complete(
    self,
    messages: list[dict],
    tools: list[dict] | None = None,
    model: str | None = None,
    reasoning: Literal["on", "off"] | None = None,
    max_tokens: int | None = None,
) -> CompletionResult: ...
```

When `max_tokens` is not None, the request payload includes `"max_tokens": max_tokens`.

`stream()` signature:

```python
async def stream(
    self,
    messages: list[dict],
    tools: list[dict] | None = None,
    model: str | None = None,
    reasoning: Literal["on", "off"] | None = None,
    max_tokens: int | None = None,
) -> AsyncGenerator[str | list[ToolCall] | StreamEnd, None]: ...
```

Behavior changes inside `stream()`:

- Track `chunks_yielded: int` and `finish_reason: str` (default `"unknown"`).
- On each chunk, if `choice.get("finish_reason")` is non-null, capture it.
- On each yielded text token, increment `chunks_yielded`.
- After the SSE stream ends (`[DONE]` or graceful end of `aiter_lines`):
  - If tool calls were accumulated: yield `list[ToolCall]` (existing behavior, no `StreamEnd`).
  - Otherwise: yield `StreamEnd(finish_reason=finish_reason, chunks_yielded=chunks_yielded)`.

The tool-call branch keeps the existing implicit "end" (consumers break on `isinstance(item, list)`). `StreamEnd` is only emitted on the text-output path. This keeps tool-call consumers (other agents, integration tests) unchanged.

Tests added to `agent_core/tests/test_inference.py`:

1. `test_stream_max_tokens_in_payload`: mock httpx; verify `max_tokens=N` in JSON request body when set, absent when None.
2. `test_complete_max_tokens_in_payload`: same for non-streaming.
3. `test_stream_yields_streamend_with_finish_reason`: mock SSE response with `finish_reason: "stop"`; verify generator's last yielded item is `StreamEnd(finish_reason="stop", chunks_yielded=...)`.
4. `test_stream_yields_streamend_length_when_capped`: mock SSE with `finish_reason: "length"`; verify `StreamEnd.finish_reason == "length"`.
5. `test_stream_chunks_yielded_count`: stream emits N text chunks; verify `chunks_yielded == N`.
6. `test_stream_tool_calls_no_streamend`: when tool calls are emitted, no `StreamEnd` follows.

## PAL daemon changes

State change in `Daemon.__init__`:

```python
self._chat_tasks: dict[str, asyncio.Task] = {}
```

Replaces the existing per-connection `current_chat_task`.

Preemption helper:

```python
async def _preempt_existing_turn(self, channel_id: str) -> None:
    """Cancel an in-flight chat task on this channel and wait briefly for unwind."""
    existing = self._chat_tasks.get(channel_id)
    if existing is None or existing.done():
        return
    existing.cancel()
    try:
        await asyncio.wait_for(existing, timeout=2.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    except Exception as exc:
        logger.warning("preempted task raised on cancel: %s", exc)
```

Connection-handler dispatch (replaces the existing rejection-on-busy guard at `pal/daemon.py:335-341`):

```python
elif isinstance(msg, ChatMessage):
    channel_id = msg.channel_id or "cli-default"
    await self._preempt_existing_turn(channel_id)
    task = asyncio.create_task(
        self._handle_chat(msg, conv, channel_id, writer, tool_executor, scanner)
    )
    self._chat_tasks[channel_id] = task
elif isinstance(msg, CommandMessage):
    channel_id = msg.channel_id or "cli-default"
    await self._preempt_existing_turn(channel_id)
    task = asyncio.create_task(
        self._handle_command(msg, conv, channel_id, writer, tool_executor, scanner)
    )
    self._chat_tasks[channel_id] = task
```

Disconnect cleanup (currently at `pal/daemon.py:370-375`) broadens. Each task carries an identifier for its owning connection; on disconnect, all tasks owned by that connection are cancelled. Implementation: store `(task, writer)` tuples or attach the writer reference to the task via `task.writer = writer` at creation time.

`_handle_chat` instrumentation:

- Capture `start = time.monotonic()` at entry.
- Maintain `chunk_count`, `terminated_reason`, `finish_reason`, `full_response: list[str]`, `tool_calls: list[ToolCall] | None`.
- Pass `max_tokens=self.config.max_response_tokens` to `self.inference.stream(...)`.
- In the stream loop:
  - `isinstance(item, StreamEnd)` → capture finish_reason, set terminated_reason to `"length"` if finish_reason is `"length"` else leave as `"complete"`, break.
  - `isinstance(item, list)` → tool_calls = item, terminated_reason = `"tool_call"`, break.
  - Otherwise → text token, increment chunk_count, write StreamChunkMessage to client, append to full_response.
- After the loop:
  - If `tool_calls is None and terminated_reason in ("complete",)`: append assistant message to conversation, send ResponseMessage.
  - If `terminated_reason == "length"`: do NOT append, write forensic line, send `ResponseMessage(text="[response truncated by max_tokens]", command="chat")`.
  - If `terminated_reason == "tool_call"`: existing tool-call handling, no change.
- `except asyncio.CancelledError`: terminated_reason = `"user_preempt"`, write forensic line, send `ResponseMessage(text="[stopped]", command="chat")` (best-effort), do NOT re-raise.
- `except Exception as exc`: terminated_reason = `"error"`, write forensic line, log exception, send ErrorMessage (best-effort).
- `finally`: emit one `chat_turn_ended` log line with the structured fields.

Forensic helper:

```python
def _record_abort_forensic(self, conv: Conversation, reason: str, partial: str) -> None:
    """Append a non-message forensic line to the JSONL history. Does NOT add to in-memory window."""
    if conv.history_path is None:
        return
    record = {
        "role": "abort",
        "reason": reason,
        "partial_chars": len(partial),
        "partial_preview": partial[:200],
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    try:
        conv.history_path.parent.mkdir(parents=True, exist_ok=True)
        with conv.history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("failed to write abort forensic: %s", exc)
```

Structured log emitted at end of every turn (success or failure):

```python
logger.info(
    "chat_turn_ended",
    extra={
        "channel_id": channel_id,
        "agent_name": "pal",
        "duration_ms": int((time.monotonic() - start) * 1000),
        "chunk_count": chunk_count,
        "terminated_reason": terminated_reason,  # complete | tool_call | length | user_preempt | error
        "finish_reason": finish_reason,
        "max_tokens_cap": self.config.max_response_tokens,
        "model": self.config.inference_model,
        "reasoning_mode": mode,
    },
)
```

Conversation replay update (in `pal/channels.py` now, `agent_core/agent_core/channels.py` after Phase D):

```python
# In _replay_into:
for lineno, line in enumerate(raw.splitlines(), 1):
    line = line.strip()
    if not line:
        continue
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        ...
        continue
    role = message.get("role")
    if role not in ("user", "assistant", "tool"):
        # Skip forensic records (e.g., role="abort") and unknown roles.
        continue
    conv._messages.append(message)
conv._truncate()
```

Tests in new file `tests/test_daemon_cancellation.py`:

1. `test_new_chat_message_preempts_existing_turn`
2. `test_preempted_partial_not_appended_to_history`
3. `test_preempted_writes_abort_forensic_to_jsonl`
4. `test_max_tokens_finish_reason_truncates_cleanly`
5. `test_disconnect_cancels_all_owned_tasks`
6. `test_chat_turn_ended_log_emitted_on_complete`
7. `test_chat_turn_ended_log_emitted_on_preempt`
8. `test_replay_skips_abort_records`

## PAL config additions

`pal/config.py`:

```python
@dataclass
class BaseConfig:
    ...
    max_response_tokens: int = 4096
```

Source priority: env var override (`PAL_MAX_RESPONSE_TOKENS`) → config file → default.

Tests added to `tests/test_config.py`:

1. `test_default_max_response_tokens_is_4096`
2. `test_max_response_tokens_env_override`
3. `test_max_response_tokens_config_file_override`

## Integration test (PAL)

New file `tests/test_inference_safety_integration.py`. Stand up an in-process fake SSE server and exercise:

1. **max_tokens truncation:** server streams 100 small chunks then `finish_reason: "length"` and `[DONE]`. Verify daemon emits `[response truncated by max_tokens]`, writes the forensic line, drops the partial from in-memory history, logs `terminated_reason="length"`.
2. **Preemption:** server streams indefinitely. Send a second chat message in the same channel. Verify the first task is cancelled within ~2.5s, partial is dropped, second message runs to completion.
3. **Server disconnect mid-stream:** server streams 5 chunks then disconnects. Verify daemon catches the exception, emits ErrorMessage, logs `terminated_reason="error"`.

## Manual smoke checklist (after deploy)

The user runs these against the live daemon on the inference server.

- Start a normal-length chat that completes well under 4096 tokens. Verify it finishes cleanly. Check journalctl for one `chat_turn_ended` log line with `terminated_reason="complete"`.
- Trigger a research command. Verify either it completes or, if truncated, the user sees `[response truncated by max_tokens]` and one log line with `terminated_reason="length"`.
- Start a chat. Mid-stream, send `@PAL stop` (or any other text). Verify the original is cancelled, the new message is processed (PAL replies to it), and the log shows the original's `terminated_reason="user_preempt"`.
- Restart the daemon. Verify channel histories load without errors (no `abort`-role records get injected as user/assistant turns).

## Risks

1. **Existing tests assert the old "one turn per connection" rejection.** Likely candidates: `tests/test_daemon.py` (already flaky-skipped per project memory) and possibly `tests/test_integration.py`. Implementation plan must search for the error string `"A previous turn is still being processed"` and remove or update those assertions.
2. **2-second cancellation timeout might be too short** if the inference server is slow to close streaming connections. Mitigation: the helper logs a warning on `wait_for` timeout, and the new task proceeds anyway. If the warning fires often in production, raise the timeout. The timeout is a constant in `_preempt_existing_turn`; making it configurable is Phase 2 work.
3. **Concurrent commands on the same channel are now possible.** Previously the second was rejected; now it preempts the first. Behavior change worth noting in CHANGELOG.
4. **Discord adapter's text_buffer behavior.** The adapter at `pal/discord_adapter.py:274` only posts `reply_text` after the final `ResponseMessage`. On abort, the adapter receives `[stopped]` or `[response truncated...]` as the final ResponseMessage and posts that, NOT the partial chunks accumulated in `text_buffer`. This means Discord users see the abort marker but not the garbage stream. Smoke-check confirms.
5. **Phase D timing.** This work touches `pal/channels.py` (the replay-skip change) which is being moved to `agent_core` in Phase D. Order matters: ship this safety fix BEFORE Phase D, so the Phase D move can carry the replay-skip change with it. Phase D's plan needs to be updated to reflect the new replay logic.

## Out of scope (Phase 2 backlog)

Tracked in memory `project_phase2_inference_investigation.md`:

1. Streaming repetition detection (n-gram window).
2. Wall-clock per-stream timeout (httpx's existing 600s timeout doesn't fire because it resets per-IO-event).
3. Tool-narration heuristic (text emitted in tool-eligible context without firing a tool call).
4. Root-cause investigation of why the model produced narration instead of a tool call (Gemma-4 specific? prompt audit? tool-description audit?).
5. CLI status-line UX (model name, context window size, fill percentage).
6. Broader PAL review (separate effort the user has planned).
