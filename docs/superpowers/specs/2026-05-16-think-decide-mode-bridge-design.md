# /think → decide_mode wiring fix

**Date:** 2026-05-16
**Status:** Design
**Author:** Brainstormed with Claude
**Related memory:** `project_reasoning_not_shown_in_discord`, `feedback_agent_core_version_bump`, `feedback_restart_both_processes`

## Problem

`/think on` in PAL (Discord or CLI) appears to toggle reasoning mode, but reasoning is never actually enabled at inference time. Symptom (confirmed 2026-05-16 via `/status`):

```
Reasoning: on (effective: off)
```

The label says "on" because PAL's `Status` command reads `conv.overrides["reasoning"]` (`pal/commands/domain.py:764`). The effective mode says "off" because `agent_core.reasoning.decide_mode` reads `conversation.reasoning_override`, an attribute that `Conversation` (`agent_core/conversation.py:17-21`) does not define.

The Think command (`agent_core/commands/_builtin_impls.py:286-323`) writes to `conv.overrides["reasoning"]`. `decide_mode` reads from `conversation.reasoning_override`. The two never meet. Result: `decide_mode` always returns `"off"`, the streaming path in `pal/agent.py:502` always runs, `enable_thinking=False` is forwarded to gemma (which suppresses thinking at the model level), and `ResponseMessage.reasoning` is always `""`. The spoiler-tag rendering shipped earlier today (commit `dbc930f`) is downstream-correct but always silent because the upstream signal is dead.

## History

`reasoning_override` (the attribute name `decide_mode` still reads) was introduced in agent_core 0.4.0 (commit `7d0cea6: feat: add reasoning module`). The generic `Conversation.overrides` dict pattern came later and the `Think` command was built against it. `decide_mode` was never updated to match. The Status command, the Think command, and `decide_mode` should all agree on one source of truth; today they do not.

## Goals

1. Make `/think on` actually toggle reasoning mode on the next inference call.
2. Make `/think off` actually toggle it off.
3. Make `/status` show consistent label and effective values.
4. Preserve the existing `Conversation.overrides` dict as the source of truth (it is already what `/think` writes and what `/status` reads; making `decide_mode` join the consensus is the minimum surface change).

## Non-goals

1. **Adding a Conversation field for reasoning state.** Could be done as `Conversation.reasoning_override: str | None = None` plus property bridging to `overrides["reasoning"]`. Rejected as larger surface for no behavioral benefit; `overrides` is already the consensus location.
2. **Refactoring `overrides` into typed fields.** `overrides` is intentionally a free-form dict for per-channel state; tightening it is its own design conversation.
3. **Changing the `auto` mode semantics.** Today `auto` falls through to `"off"` in `decide_mode`. Future enhancement: a heuristic that flips to `"on"` based on the conversation. Out of scope for this fix.
4. **Reasoning preservation in the streaming path.** `pal/agent.py:502-533` (the `else mode != "on"` branch) does not capture reasoning even when streaming returns it. This is a separate gap; the current fix only ensures the non-streaming (`mode == "on"`) path is reachable. Address in a follow-up if needed.
5. **Server deploy.** User handles server pull and `pal-daemon` restart themselves.

## Design

### Change 1: `agent_core/reasoning.py:55-59` reads from `overrides["reasoning"]`

Replace the current `decide_mode` body. The function continues to return `Literal["on", "off"]`; only the source of the override changes.

```python
def decide_mode(conversation: _ConversationLike) -> Literal["on", "off"]:
    overrides = getattr(conversation, "overrides", None)
    if isinstance(overrides, dict):
        override = overrides.get("reasoning")
        if override in ("on", "off"):
            return override
    return "off"
```

`getattr(... , None)` + `isinstance` guards keep `decide_mode` working with any conversation-like object that lacks `overrides` (defensive parity with the original `getattr(..., "reasoning_override", None)` shape). Tests with bare mocks still pass; the production path with `Conversation` works.

### Change 2: `agent_core/reasoning.py:12-19` updates the `_ConversationLike` Protocol

The Protocol docstring/typing should reflect the new contract:

```python
class _ConversationLike(Protocol):
    """Duck-typed contract for what `decide_mode` reads from its argument.

    Any object with an `overrides` dict containing a "reasoning" key set to
    "on" or "off" will satisfy this. Concrete agents (e.g. agent_core's
    Conversation class) match without explicit subclassing.
    """
    overrides: dict
```

### Change 3: agent_core version bump

`pyproject.toml` `1.2.0` → `1.2.1` (patch release; behavior fix, no API surface change visible to PAL beyond decide_mode now working).

### Change 4: PAL pin bump

`/home/edible/Projects/PAL/pyproject.toml` updates the agent_core pin to `1.2.1`. Required because server installs from the pin; per `feedback_agent_core_version_bump`, same-version-with-changed-code is silently stale on the server.

## Alternatives considered

**A. (chosen) `decide_mode` reads `overrides["reasoning"]`.** One file, ~6 lines changed, no Conversation surface change. Aligns with where `/think` writes and where `/status` reads.

**B. `Conversation` exposes `reasoning_override` as a property over `overrides["reasoning"]`.** Preserves the current `decide_mode` contract, costs a small property on Conversation. Slightly larger surface, no behavioral benefit. Would also leave the codebase with two equivalent ways to read the same state, which is the kind of drift that caused this bug.

**C. `Think` writes `conv.reasoning_override = ...` directly.** Requires adding `reasoning_override` as a Conversation field. Diverges from `/status`'s read path (still reads `overrides["reasoning"]`), so either Status also moves to the new field or we end up with two sources of truth again. Rejected.

## Tests

In `agent_core/tests/`:

1. **`test_decide_mode_returns_on_when_override_set`** -- given a `Conversation` with `overrides={"reasoning": "on"}`, `decide_mode` returns `"on"`.
2. **`test_decide_mode_returns_off_when_override_set`** -- given `overrides={"reasoning": "off"}`, returns `"off"`.
3. **`test_decide_mode_returns_off_when_override_absent`** -- given a fresh `Conversation` with empty overrides, returns `"off"` (default).
4. **`test_decide_mode_ignores_unknown_override_values`** -- given `overrides={"reasoning": "auto"}` or `overrides={"reasoning": "garbage"}`, returns `"off"` (current behavior preserved; `auto` is not a valid mode at decide time).
5. **`test_decide_mode_works_with_objects_without_overrides`** -- given an object that does not have an `overrides` attribute at all (defensive), returns `"off"` without raising.

If any existing test in agent_core asserts the old `reasoning_override` contract, update it to use `overrides["reasoning"]`. Verify with `grep -n "reasoning_override" agent_core/tests/`.

In PAL: no new tests required. The wire-in path is unchanged on PAL's side; only the upstream `decide_mode` behavior changes.

## Verification

After the agent_core release + PAL pin bump + server deploy:

1. Run `/status` on PAL CLI. With `/think auto` (default), expect `Reasoning: auto (effective: off)`.
2. Run `/think on` then `/status`. Expect `Reasoning: on (effective: on)`. (The discrepancy that confirmed the bug should disappear.)
3. Send a chat message in `/think on` mode in Discord. Expect the spoiler block (`_Reasoning (click to expand):_\n||...||`) to appear above the answer.
4. Send a chat message in `/think on` mode in CLI. Expect dim-italic reasoning to appear above the answer (existing CLI render at `pal/cli.py:327-333`).
5. Run `/think off` then `/status`. Expect `Reasoning: off (effective: off)`. Confirm chat answers have no reasoning block.

## Migration / back-compat

- `_ConversationLike` Protocol contract changes from `reasoning_override` to `overrides`. Any caller that satisfied the old contract by setting `reasoning_override` directly (no such caller exists in PAL or agent_core today) would silently get `"off"` after this change. Acceptable; the attribute was never wired up.
- Existing `Conversation` instances need no migration -- the `overrides` dict already exists.
- agent_core wheel needs reinstall on server (PAL ships a pinned wheel install per `feedback_agent_core_version_bump`).

## Risks

1. **Server deploys with stale agent_core.** Per `feedback_agent_core_version_bump`, same-version-with-changed-code is silently stale on the wheel-installed server. Mitigation: bump 1.2.0 → 1.2.1 in agent_core's pyproject AND bump the PAL pin so the server's `pip install` actually re-resolves.
2. **No Discord adapter restart needed.** This is a behavior change, not a protocol change. Per `feedback_restart_both_processes`, protocol-only changes need both processes restarted; this is daemon-only. Only `pal-daemon` needs a restart.
3. **A hypothetical caller setting `reasoning_override` directly will silently break.** None such exist in either repo (verified by grep). Future contributors who follow the old protocol contract would be confused; the Protocol docstring update mitigates.

## Out of scope

- Reasoning capture in the streaming path (`pal/agent.py:502-533`); v1 only fixes the non-streaming `mode == "on"` path which is the path that fires when `/think on` is active.
- `/think auto` heuristic that decides "on" based on conversation content.
- Per-channel `/think show` / `/think hide` for Discord (separate audit needs_spec item; already deferred).
- Slash-command reasoning rendering in Discord (separate audit needs_spec item; already deferred).
- Server-side deploy (user handles).
