# /think → decide_mode bridge -- Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/think on|off` actually toggle reasoning mode at inference time. Today the Think command writes to `conv.overrides["reasoning"]` and `decide_mode` reads from a never-set `reasoning_override` attribute, so the toggle is a no-op. Bridge them in `decide_mode`.

**Architecture:** One-file change in agent_core. `agent_core/reasoning.py` `decide_mode` reads from `conversation.overrides["reasoning"]` (matching where `/think` writes and where PAL's `/status` reads). Update the `_ConversationLike` Protocol docstring to reflect the new contract. Bump agent_core 1.2.0 -> 1.2.1; bump PAL's pin to match.

**Tech Stack:** Python 3.12, pytest, agent_core, PAL.

**Spec:** `docs/superpowers/specs/2026-05-16-think-decide-mode-bridge-design.md`

**Repos touched:** `agent_core` (fix + tests + version bump) AND `PAL` (pin bump only). Server deploy: user pulls both and restarts `pal-daemon`; no Discord adapter restart (behavior change only, no protocol change).

---

## File Structure

**agent_core repo (`/home/edible/Projects/agent_core/`):**
- Modify: `agent_core/reasoning.py:12-19` (Protocol docstring + type) and `agent_core/reasoning.py:55-59` (`decide_mode` body).
- Modify: `agent_core/tests/test_reasoning.py:9-15` (`_StubConversation` uses `overrides` dict) and `:102-114` (3 existing decide_mode tests use the new shape). Append 2 new tests.
- Modify: `pyproject.toml` version `1.2.0` -> `1.2.1`.

**PAL repo (`/home/edible/Projects/PAL/`):**
- Modify: `pyproject.toml` agent_core pin `==1.2.0` -> `==1.2.1`.

No other files. No PAL source changes (the wire-in path is correct already; only the upstream `decide_mode` behavior changes).

---

## Task 1: agent_core fix + tests + version bump

**Files (all in `/home/edible/Projects/agent_core/`):**
- Modify: `agent_core/reasoning.py`
- Modify: `agent_core/tests/test_reasoning.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Update existing tests to the new contract (will then fail against unmodified code)**

In `/home/edible/Projects/agent_core/tests/test_reasoning.py`, replace the `_StubConversation` dataclass:

```python
@dataclass
class _StubConversation:
    """Minimal stand-in for an agent's Conversation type.

    Matches the duck-typed _ConversationLike Protocol that decide_mode reads.
    The override is stored under overrides["reasoning"] so the stub matches
    where /think writes and where PAL's /status reads.
    """
    overrides: dict = field(default_factory=dict)
```

Add `from dataclasses import dataclass, field` at the top if not already there.

Replace the three existing decide_mode tests (lines 102-114) with these (same names, new bodies):

```python
def test_decide_mode_override_on():
    conv = _StubConversation(overrides={"reasoning": "on"})
    assert decide_mode(conv) == "on"


def test_decide_mode_override_off():
    conv = _StubConversation(overrides={"reasoning": "off"})
    assert decide_mode(conv) == "off"


def test_decide_mode_no_override():
    conv = _StubConversation()
    assert decide_mode(conv) == "off"
```

Append two new tests at the end of the file:

```python
def test_decide_mode_ignores_unknown_override_values():
    """auto, garbage, None, etc. fall through to the default of off."""
    conv = _StubConversation(overrides={"reasoning": "auto"})
    assert decide_mode(conv) == "off"
    conv = _StubConversation(overrides={"reasoning": "garbage"})
    assert decide_mode(conv) == "off"
    conv = _StubConversation(overrides={"reasoning": None})
    assert decide_mode(conv) == "off"


def test_decide_mode_works_with_objects_without_overrides():
    """Defensive: an object without an overrides attribute returns off, no raise."""
    class _NoOverrides:
        pass
    assert decide_mode(_NoOverrides()) == "off"
```

- [ ] **Step 2: Run tests, verify failures**

```bash
cd /home/edible/Projects/agent_core && .venv/bin/pytest tests/test_reasoning.py -k "decide_mode" -v
```

Expected: `test_decide_mode_override_on` and `test_decide_mode_override_off` fail (current `decide_mode` reads `reasoning_override`, sees `_StubConversation` no longer has it, returns `"off"`). The two `no_override`-style tests still pass by coincidence (return `"off"` either way). The new `_ignores_unknown_override_values` and `_works_with_objects_without_overrides` also pass under current behavior.

- [ ] **Step 3: Update `decide_mode` and the Protocol**

In `/home/edible/Projects/agent_core/agent_core/reasoning.py`, replace lines 12-19 (the `_ConversationLike` Protocol):

```python
class _ConversationLike(Protocol):
    """Duck-typed contract for what `decide_mode` reads from its argument.

    Any object with an `overrides` dict containing a "reasoning" key set to
    "on" or "off" will satisfy this. Concrete agents (e.g. agent_core's
    Conversation class) match without explicit subclassing.
    """
    overrides: dict
```

Replace lines 55-59 (the `decide_mode` body):

```python
def decide_mode(conversation: _ConversationLike) -> Literal["on", "off"]:
    overrides = getattr(conversation, "overrides", None)
    if isinstance(overrides, dict):
        override = overrides.get("reasoning")
        if override in ("on", "off"):
            return override
    return "off"
```

- [ ] **Step 4: Run the decide_mode tests, verify they pass**

```bash
cd /home/edible/Projects/agent_core && .venv/bin/pytest tests/test_reasoning.py -k "decide_mode" -v
```

Expected: all 5 decide_mode tests pass.

- [ ] **Step 5: Full agent_core test suite regression sweep**

```bash
cd /home/edible/Projects/agent_core && .venv/bin/pytest tests/ -q 2>&1 | tail -5
```

Expected: all pass. If `test_agent.py:test_agent_decide_mode_delegates_to_reasoning` (line 57) fails, the test currently asserts `result in ("on", "off", "auto")`. After this change, the assertion is still satisfied (`decide_mode` returns `"off"` for a Conversation with no overrides). No update needed unless the assertion was stricter.

If any unrelated test fails because it relies on `reasoning_override`, update it to use `overrides["reasoning"]` (use the Grep tool to search: `reasoning_override` in `agent_core/tests/`). Today no such tests exist.

- [ ] **Step 6: Bump agent_core version**

In `/home/edible/Projects/agent_core/pyproject.toml`, change:

```
version = "1.2.0"
```

to:

```
version = "1.2.1"
```

Verify there is exactly one `version = ` line in `pyproject.toml` and you changed the right one.

- [ ] **Step 7: Em-dash sweep on the diff**

```bash
cd /home/edible/Projects/agent_core && git diff main..HEAD 2>/dev/null | grep -cP '[\x{2014}\x{2013}]'
```

Expected: `0`. (Note: `main..HEAD` may show nothing if no commit yet; if so, run `git diff` against the working tree: `cd /home/edible/Projects/agent_core && git diff | grep -cP '[\x{2014}\x{2013}]'`.)

- [ ] **Step 8: Commit**

```bash
cd /home/edible/Projects/agent_core && git add agent_core/reasoning.py tests/test_reasoning.py pyproject.toml && git commit -m "$(cat <<'EOF'
fix(reasoning): decide_mode reads from overrides["reasoning"]

The Think command writes conv.overrides["reasoning"] but decide_mode
read conv.reasoning_override (an attribute Conversation never defined).
The two never met, so /think on was a silent no-op: decide_mode always
returned "off" and enable_thinking=False was forwarded to gemma,
suppressing reasoning at the model level.

Bridge by reading from overrides["reasoning"] in decide_mode. This is
where /think writes and where consumer agents (PAL's /status) already
read. _ConversationLike Protocol updated to reflect the new contract;
the old reasoning_override attribute had no producers anywhere.

Bumps to 1.2.1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Critical correctness notes

- `decide_mode` keeps the same return type (`Literal["on", "off"]`) and the same default (`"off"`). Only the source of the override changes.
- Defensive `getattr(..., None)` + `isinstance` guards preserve compatibility with bare mocks or objects that do not have `overrides`. Do not remove them.
- The version bump is required even though the change is small. Per the `feedback_agent_core_version_bump` memory: same-version-with-changed-code is silently stale on the wheel-installed server.
- No em dashes in any commit message or test docstring.

## Self-review checklist

- [ ] `_StubConversation` field is `overrides: dict = field(default_factory=dict)` (not the old `reasoning_override` field).
- [ ] `field` is imported alongside `dataclass` in the test file.
- [ ] All 5 decide_mode tests pass after the fix.
- [ ] Full `agent_core` suite still passes.
- [ ] `pyproject.toml` version is `1.2.1` exactly.
- [ ] No em dashes.

---

## Task 2: PAL pin bump + verification

**Files (all in `/home/edible/Projects/PAL/`):**
- Modify: `pyproject.toml`

- [ ] **Step 1: Bump the agent_core pin**

In `/home/edible/Projects/PAL/pyproject.toml`, find the agent_core dependency line. It currently pins `==1.2.0` (verify with the Read tool first). Change to `==1.2.1`.

- [ ] **Step 2: Reinstall agent_core in PAL's venv**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pip install -e /home/edible/Projects/agent_core --force-reinstall --no-deps 2>&1 | tail -5
```

(Using editable install from the local agent_core checkout so the dev box gets the same fix the server will get from the pinned wheel after deploy. `--force-reinstall --no-deps` per `feedback_agent_core_version_bump`.)

Verify:

```bash
cd /home/edible/Projects/PAL && .venv/bin/python -c "from agent_core.reasoning import decide_mode; class X: pass; x=X(); x.overrides={'reasoning':'on'}; print(decide_mode(x))"
```

Expected: prints `on`.

- [ ] **Step 3: Full PAL test suite regression sweep**

```bash
cd /home/edible/Projects/PAL && .venv/bin/pytest tests/ \
    --ignore=tests/test_chat_research_integration.py \
    --ignore=tests/test_client.py \
    --ignore=tests/test_daemon.py \
    --ignore=tests/test_integration.py \
    --ignore=tests/test_prompt_injection.py \
    -q 2>&1 | tail -5
```

Expected: all pass (647 or close to it).

- [ ] **Step 4: Em-dash sweep**

```bash
cd /home/edible/Projects/PAL && git diff main..HEAD -- pyproject.toml | grep -cP '[\x{2014}\x{2013}]'
```

Expected: `0`.

- [ ] **Step 5: Commit**

```bash
cd /home/edible/Projects/PAL && git add pyproject.toml && git commit -m "$(cat <<'EOF'
chore: bump agent_core pin to v1.2.1

Picks up the decide_mode fix that bridges /think to actual reasoning
mode at inference time. Without this bump the server installs would
silently stay on the broken 1.2.0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

## Critical correctness notes

- This task does NOT modify any PAL source code. The PAL-side wire-in (`pal/agent.py` mode == "on" branch + `pal/discord_interactions.py` spoiler) is correct already; only the upstream signal was broken.
- The reinstall in Step 2 uses the local editable agent_core checkout. On the server, the user's deploy runs `pip install` from the pin, which pulls the published wheel; that wheel needs to have the bumped version (Task 1 Step 6 covers it).
- No em dashes.

## Self-review checklist

- [ ] PAL `pyproject.toml` pins `agent_core==1.2.1` exactly.
- [ ] Editable reinstall succeeded and the one-liner check prints `on`.
- [ ] Full PAL suite still passes.
- [ ] No em dashes.

---

## Verification after deploy (user-driven)

These are the smoke tests the user runs after pulling on the server and restarting `pal-daemon`:

1. CLI: `/status` shows `Reasoning: auto (effective: off)` initially.
2. CLI: `/think on` then `/status` shows `Reasoning: on (effective: on)`.
3. CLI: send a chat message; reasoning appears as dim-italic text before the answer.
4. Discord: `/think on` in a channel, send a chat message; spoiler block appears above the answer.
5. CLI: `/think off` then `/status` shows `Reasoning: off (effective: off)`; chat answers have no reasoning.

No Discord adapter restart needed (behavior change only, no protocol change).

## Self-review checklist (whole plan)

- [ ] Two tasks: agent_core fix (Task 1, 8 steps) and PAL pin bump (Task 2, 5 steps).
- [ ] Each task has exact file paths.
- [ ] Each test step shows the assertion code.
- [ ] Each implementation step shows the actual code.
- [ ] No "TBD", "TODO", "implement later" anywhere.
- [ ] Version bump in agent_core AND pin bump in PAL.
- [ ] No PAL source code changes.
- [ ] All commit messages end with the Co-Authored-By line.
- [ ] No em dashes anywhere.

## Out of scope

- Reasoning capture in the PAL streaming path (`pal/agent.py:502-533`).
- `/think auto` heuristic to decide on/off based on conversation content.
- Per-channel `/think show` / `/think hide` for Discord.
- Slash-command reasoning rendering in Discord (`pal/discord_adapter.py:142-148`).
- Server-side deploy (user handles).
