# PAL Reasoning Control and Model Switching

**Date:** 2026-04-11
**Scope:** PAL daemon, CLI client
**Repos:** PAL (primary), inference-server (noted prerequisite only)

---

## Summary

Per-conversation control over (1) which model a conversation uses and (2) whether that model reasons before answering. Ships the Gemma 4 reasoning toggle, a `/model` command for runtime model switching, and structured logging of toggle events to enable a future learning-driven auto heuristic.

The inference server manager is not modified. One server-side config change (systemd unit edit for interleaved chat template) is noted as a prerequisite but executed separately.

---

## Explicit non-goals

- No persistence of model or reasoning choice across daemon restarts
- No adapter framework for reasoning models; single module with in-code branches and a family lookup dict
- No Qwen3 reasoning control branch; today Qwen3 always reasons and PAL does nothing to change that. A branch can be added to `pal/reasoning.py` later in ~8 lines
- No learning-driven auto heuristic. Ships "always off unless explicit override" and emits log events for future learning-driven v2
- No vision/mmproj support
- No Discord reasoning display (deferred to follow-up)
- No multi-query expansion or vault coherence (compiled truth + links); those are separate specs

---

## Architecture

### New file: `pal/reasoning.py`

Three functions, no classes. A prefix-to-family dict maps model names to family identifiers, and a `match` statement dispatches per family.

```python
from typing import Literal

_MODEL_FAMILIES: dict[str, str] = {
    "gemma-4": "gemma",
    "gemma-3": "gemma",
    "qwen3":   "qwen3",
}


def _identify_family(model: str) -> str | None:
    for prefix, family in _MODEL_FAMILIES.items():
        if model.startswith(prefix):
            return family
    return None


def shape_request(body: dict, model: str, mode: Literal["on", "off"]) -> dict:
    match _identify_family(model):
        case "gemma":
            body.setdefault("chat_template_kwargs", {})["enable_thinking"] = (mode == "on")
        case "qwen3":
            pass  # future: /no_think suffix
        case None:
            pass
    return body


def extract_reasoning(response: dict) -> str | None:
    msg = response["choices"][0]["message"]
    return msg.get("reasoning_content") or None


def decide_mode(conversation) -> Literal["on", "off"]:
    if conversation.reasoning_override in ("on", "off"):
        return conversation.reasoning_override
    return "off"
```

**Design rationale:**

- Family lookup separates configuration ("what do we know about") from logic ("what do we do"). Adding a variant (e.g., `"gemma-5": "gemma"`) is a one-line dict entry. Adding a new family is a dict entry plus a `case` arm.
- `match` is used over `if/elif` because each arm is self-documenting and there is no dangling `else` to reason about. `None` is the explicit "unknown model" path.
- `decide_mode` takes the full `Conversation` (not just the override field) so that when the learning-driven v2 lands, it has access to message history without a signature change.
- `extract_reasoning` is model-agnostic today because llama.cpp's parser surfaces `reasoning_content` for all reasoning models that go through its dedicated parsers.

### Modified: `pal/inference.py`

- `InferenceClient.model` renamed to `default_model` to make the semantics obvious now that "the model" is no longer a single value.
- `complete()` and `stream()` grow two optional params: `model: str | None = None` and `reasoning: Literal["on", "off"] | None = None`. When `model` is None, uses `default_model`. When `reasoning` is None, no reasoning control is injected (backwards-compatible behavior).
- Request-body construction passes through `reasoning.shape_request()` so the model-specific control lands in the right place.
- Response path extracts reasoning via `reasoning.extract_reasoning()`.
- `CompletionResult` grows a new field: `reasoning: str | None = None`.

### Modified: `pal/conversation.py`

- `Conversation` dataclass grows two fields:
  - `model_override: str | None = None`
  - `reasoning_override: Literal["on", "off"] | None = None`
- Both default to None, meaning "use daemon defaults."
- Reasoning content is never stored in conversation history. It is delivered to the client for display but does not survive into the next turn's message replay.
- If the history-stripping verification (see Testing section) reveals that llama.cpp does not auto-strip historical reasoning, a defensive guard in `add_assistant` strips `<think>...</think>` blocks before storing. ~5 lines, only added if the verification proves it necessary.

### Modified: `pal/daemon.py`

- Main chat path resolves model and reasoning mode at the top of each turn:
  - `model = conv.model_override or self.inference.default_model`
  - `mode = decide_mode(conv)`
- Passes both through to `self.inference.complete()` / `.stream()`.
- Two new slash commands added to `_handle_command`:
  - `/model` (show, list, set, default)
  - `/think` (on, off, auto, show, hide, status)
- Internal daemon operations (summarization, learning extraction, `/rate`, `/promote`, background tasks) always call `complete()` with `reasoning="off"` explicitly. They do not inherit conversation overrides.
- `/status` output grows to show the active model and reasoning mode for the current conversation alongside the daemon defaults.
- Toggle event logging: every `/think on|off|auto` invocation writes a structured log line.

### Modified: `pal/cli.py`

- When `CompletionResult.reasoning` is non-None and display preference is `show`, renders a dim text block above the answer, visually distinct from the main content.
- CLI tracks a display preference (`show` or `hide`) toggled by `/think show` / `/think hide`. Default is `show`. Dim text is capped at 20 lines in the terminal with a note that full reasoning is in the debug log; this cap is a hardcoded default that can become configurable if needed.
- Debug log always receives the full reasoning regardless of display preference or line cap.

### Not modified: `pal/discord_adapter.py`

Discord reasoning display is deferred. Discord users see `content` only. `/model` and `/think on|off|auto` work on Discord because they are daemon-side commands. `/think show` / `/think hide` issued over Discord responds with "Discord reasoning display is not yet available."

### Not modified: `pal/config.py`

`PAL_MODEL` continues to set the daemon default. No new environment variables. No config format changes.

---

## Command surfaces

### `/model`

```
/model                 Show current model (notes whether override vs default)
/model list            Query manager /v1/models, render a numbered list
/model <name>          Validate against /v1/models, set conversation override
/model default         Clear override, revert to daemon default
```

Validation happens at set-time via a GET to `{inference_url}/v1/models`. Once set, requests use the override unconditionally. If the manager becomes unreachable on a subsequent chat turn, the request itself fails and the user sees the real error; no pre-flight check on every turn.

### `/think`

```
/think                 Show current mode (on/off/auto) and display state (show/hide)
/think on              Set conversation reasoning_override = "on"
/think off             Set conversation reasoning_override = "off"
/think auto            Set reasoning_override = None, falling back to decide_mode ("off" today)
/think show            CLI renders reasoning blocks (default)
/think hide            CLI hides reasoning blocks; reasoning still flows and logs
```

`show` / `hide` are client-side display state only. They do not change what the server is asked to do, only what the CLI renders.

---

## Data flow (one turn, Gemma 4, reasoning on)

1. User types a message. CLI sends it to daemon via unix socket.
2. Daemon resolves model and mode: `model = conv.model_override or default_model`, `mode = decide_mode(conv)`.
3. Daemon builds system prompt + message history via existing path (unchanged).
4. Daemon calls `self.inference.complete(messages, tools, model=model, reasoning=mode)`.
5. `InferenceClient` builds the request body, passes it through `reasoning.shape_request(body, model, mode)` which inserts `chat_template_kwargs: {"enable_thinking": true}`.
6. Request goes to the manager at `192.168.1.14:11434`, passes through unchanged, hits llama-server.
7. Response comes back with `reasoning_content` and `content` populated.
8. `InferenceClient` pulls reasoning via `reasoning.extract_reasoning()`, returns `CompletionResult(type="text", content=..., reasoning=...)`.
9. Daemon writes reasoning to debug log, sends response to the client with reasoning and content in separate fields.
10. CLI renders reasoning as a dim text block above the answer (if display pref is `show`).
11. Conversation history stores only `content`. Reasoning is never replayed.

---

## Toggle event logging

Every `/think on|off|auto` invocation writes a structured log line to `pal-daemon.log`:

```
INFO reasoning_toggle conversation_id=<uuid> turn_idx=<N> action=<on|off|auto> last_user_message=<first 200 chars>
```

No new file, no new subsystem, no new config. Grep-able when the v2 learning-driven heuristic wants a training signal.

---

## Error handling

| Scenario | Behavior |
|---|---|
| Manager unreachable during `/model <name>` validation | Error message, conversation override unchanged |
| Unknown model in `/model <name>` | "Model X not found. Use `/model list`." Override unchanged |
| Manager unreachable during a real chat turn | Existing `_post_with_retry` logic handles it, user sees the real error |
| `/model` switched mid-tool-loop | Override applies starting with the next user turn; current tool loop finishes on the original model |
| `/think on` with a non-reasoning model loaded | `shape_request` is a no-op for unknown model families. Request goes out unmodified. Debug log notes "reasoning control requested but no-op for model X" |
| Server returns `reasoning_content` unexpectedly | `extract_reasoning` returns the string. Client renders if display is on. Never stored in history |
| Reasoning block exceeds reasonable length | Debug log always gets the full content. CLI rendering is capped at a configurable line limit with a "full reasoning in debug log" note |

---

## Server-side prerequisite (noted, not executed by PAL)

For Gemma 4 tool-calling to work correctly with reasoning on, llama-server needs to launch with the interleaved chat template. One-line addition to `/etc/systemd/system/llama-server.service` ExecStart:

```
--chat-template-file /mnt/secondary/llama.cpp/models/templates/google-gemma-4-31B-it-interleaved.jinja
```

Without it, Gemma 4 works for chat, but multi-tool-call turns may behave incorrectly because thoughts get dropped between tool calls instead of preserved. This is a systemd edit, not PAL code.

---

## Testing

### Pre-flight checklist

```
Server required:
  [ ] Inference server reachable at 192.168.1.14:11434
  [ ] Gemma 4 model loaded (curl /status to confirm)
  [ ] llama-server launched with interleaved template (systemd prereq done)
  [ ] PAL daemon running with new code

No server required:
  [ ] Unit tests (pal/reasoning.py, pal/conversation.py)
  [ ] /model and /think command parsing logic
```

### Unit tests: `pal/reasoning.py`

- `shape_request` injects `enable_thinking=True` when mode is `"on"` and model is in the `gemma` family
- `shape_request` injects `enable_thinking=False` when mode is `"off"` and model is in the `gemma` family
- `shape_request` is a pure no-op (body unchanged) when model family is unknown
- `shape_request` preserves any pre-existing `chat_template_kwargs` (merges, not overwrites)
- `extract_reasoning` returns the string when `reasoning_content` is present
- `extract_reasoning` returns `None` when `reasoning_content` is missing or empty
- `decide_mode` returns the override verbatim when the conversation has one set
- `decide_mode` returns `"off"` when no override is set

### Unit tests: `pal/conversation.py`

- New fields default to `None`
- If history-strip guard is needed: `add_assistant` strips `<think>...</think>` blocks before storing

### Integration verification (runs first, before writing code)

- `curl` against the live server with Gemma 4 loaded
- Send a multi-turn message array with a prior assistant turn containing a fake `reasoning_content` field
- Observe whether llama.cpp stripped the historical reasoning before feeding it to the template
- Document the result; if not stripped, implement the guard in `conversation.py`

### Manual smoke tests

Items tagged `[server]` require the full stack. Items tagged `[local]` run against the daemon only.

- `[local]` `/model list` returns expected model IDs
- `[local]` `/model <name>` accepts a known model, rejects unknown names with a clear error
- `[local]` `/model default` clears an override; `/model` shows the daemon default again
- `[local]` `/status` shows override vs default correctly
- `[server]` `/think on` followed by a chat message produces reasoning rendered dim above the answer in the CLI
- `[local]` `/think hide` suppresses rendering; `/think show` restores it
- `[server]` `/think off` followed by a chat message produces no `reasoning_content` in logs, latency drops noticeably
- `[server]` Model switch mid-conversation takes effect on the next turn, confirmed by checking the model name in the request log
- `[server]` Internal `/learn`, `/rate`, `/promote` do not emit `reasoning_content` in the debug log
- `[local]` `/think on` event produces a toggle log line grep-able in `pal-daemon.log`
- `[local]` `/think show` / `/think hide` issued over Discord responds with "Discord reasoning display is not yet available"

---

## File-level diff preview

| File | Change | Rough LOC |
|---|---|---|
| `pal/reasoning.py` | new | ~80 |
| `pal/inference.py` | rename attr, extend method signatures, wire reasoning module | ~30 |
| `pal/conversation.py` | two new fields, optional strip guard | ~5-15 |
| `pal/daemon.py` | two new command handlers, turn path changes, status update, toggle logging | ~80 |
| `pal/cli.py` | reasoning block rendering, display pref state, command response formatting | ~40 |
| `pal/discord_adapter.py` | no changes | 0 |
| `pal/config.py` | no changes | 0 |
| Tests | new | ~120 |

Total: ~240 lines production code + ~120 lines tests.

---

## Migration

- No database migrations. No config file format changes. No new environment variables.
- First daemon restart after merge: all conversations start with `model_override=None` and `reasoning_override=None`. Default behavior is identical to today for Qwen3 users because `shape_request` has no active Qwen3 branch.
- If the user switches `PAL_MODEL` to Gemma 4, out-of-box behavior is reasoning off by default (`decide_mode` returns `"off"`, `shape_request` translates to `enable_thinking: False`). User runs `/think on` to opt in. This is correct per the "conservative about tokens" design decision.

---

## Deferred follow-ups

### Vision / mmproj support

Gemma 4 is natively multimodal and Unsloth ships `mmproj-F16.gguf` alongside the model. Wiring this in would unlock scanned PDFs, diagrams, screenshots of code, and other visual sources for the ingestion pipeline. Needs manager changes (model-to-mmproj mapping in config or by naming convention) plus PAL request-building changes. High value for the RE Lab direction where document ingestion at scale matters. Own spec.

### Discord reasoning display

Render reasoning as a blockquote in Discord messages with length truncation. Same display toggle semantics as the CLI. Not in this cycle; Discord users see `content` only.

### Learning-driven auto heuristic

V2 of `decide_mode` that reads the `reasoning_toggle` log events emitted by this spec, extracts patterns via `/learn`, promotes useful ones to wisdom, and uses wisdom entries tagged `reasoning-policy` as classification rules. Becomes worth building once there is enough toggle data to see patterns.

### Qwen3 reasoning control

Add a branch to `shape_request` that appends `/no_think` to the last user message when mode is `"off"` and model is Qwen3. Roughly 8 lines when needed.

### Persistence of model / reasoning choice

Storing preferences across daemon restarts. Requires deciding where to persist (state file, `_profile/`, JSON sidecar). Build when "I have to re-set this every morning" becomes an actual complaint.

### Per-client default preferences

e.g., "always use Gemma 4 on Discord, Qwen3 on CLI." Speculative. Only if a real use case emerges.
