# agent_core Extraction and Agent_Template Design

Status: design approved, ready for implementation planning
Date: 2026-04-25
Origin: PAL (this repo). Move this spec to the agent_core repo once that repo is bootstrapped.

## Context

PAL today is one agent with no shared infrastructure to lean on. The ecosystem direction (see `docs/agent_ecosystem_direction.md`, 2026-04-13) commits to N independent agents (PAL, RE Lab, Coding, General, future specialties) each with their own daemon, socket, commands, and wisdom pool, sharing a small amount of plumbing. The doc records the decision that shared infrastructure gets extracted *before* the second agent ships, not after.

This design covers two artifacts that together unblock building a second agent:

1. **`agent_core`**: a private pip-installable library containing the generic plumbing PAL has built (daemon, socket protocol, inference client, retrieval, wisdom, learning, channels, scratchpad, fetcher/chunker/converter, CLI REPL, optional Discord gateway).
2. **`Agent_Template`**: a starter-style git repo that, after a one-shot init script, produces a working minimal agent depending on `agent_core`.

Both repos already exist as empty placeholders at:
- https://github.com/EdibleTuber/agent_core
- https://github.com/EdibleTuber/Agent_Template

PAL becomes the first `agent_core` consumer as part of this work, validating the API by real usage rather than by skeleton testing alone.

## Goals

1. Extract the agent-agnostic parts of PAL into `agent_core` without disrupting PAL's day-to-day function.
2. Produce a starter template such that a new agent can be scaffolded with `git clone Agent_Template my-agent && ./scripts/init-agent.sh my-agent` and immediately run end-to-end against the inference server.
3. Define a small, explicit extension-point surface for new agents (system prompt, commands, tools, optional categorizer) so writing a new agent feels like filling in blanks, not assembling plumbing.
4. Migrate PAL incrementally so that PAL keeps shipping throughout. No big-bang rewrites, no broken-trunk periods.

## Non-Goals

- Building RE Lab or any second agent. The template unblocks that work; the second agent itself is a separate project.
- Inter-agent messaging, launcher UX, multi-agent autonomous polling. Open questions in the ecosystem doc; deferred until a second agent exists to motivate concrete answers.
- Wiki / vault index generation. That logic moves server-side as part of the deterministic indexer rebuild project.
- Publishing `agent_core` to PyPI. It stays private, installed via git tag.
- Plugin discovery / entry-point auto-registration. Agents list their commands and tools explicitly.

## Architecture

Three repositories with a clear dependency direction:

```
inference_server  (existing, on 192.168.1.14)
        ^
        | HTTP
        |
   +----+-----+              +-----------+
   | agent_   | <- pip dep - | PAL       |
   | core     | <- pip dep - | (other    |
   | (library)|              |  agents)  |
   +----+-----+              +-----------+
        |
        | starter scaffold
        v
   +----------+
   | Agent_   |
   | Template |
   +----------+
```

### Dependency rules

- `agent_core` depends on: `httpx`, `prompt-toolkit`, `rich`, `pyyaml` for runtime; optionally `discord.py` behind an extras_require.
- `agent_core` never imports from any specific agent. The dependency arrow is one-way.
- Agents depend on: `agent_core @ git+https://github.com/EdibleTuber/agent_core.git@<tag>`, plus their own agent-specific libraries (PAL adds `trafilatura`, `pymupdf4llm`, `markitdown`).
- `Agent_Template` depends on: `agent_core` at a pinned tag. The template is a working minimal agent, not a meta-template.

### Layer responsibilities

**`inference_server`** (unchanged by this work): model serving, embeddings, vector collections API, vault indexing.

**`agent_core`**: daemon runtime and socket protocol; inference client; retrieval client; wisdom, learning, profile, allowlist, approval registry, channels, scratchpad, conversation; reasoning mode helpers; tool executor and built-in tools; command registry and built-in commands; system prompt builder interface; utility modules (frontmatter, chunker, sanitizer, fetcher, converter); CLI REPL adapter; Discord gateway adapter (opt-in).

**PAL** (after migration): Oracle/librarian system prompt content; categorizer prompts and category set; `/research`, `/compile`, `/summarize`, `/think`, `/import` command implementations; compiler, article, consolidator, reorg, summarizer, pdf_structure modules; Discord Interactions HTTP webhook (`discord_interactions.py`); PAL-specific tool implementations.

**`Agent_Template`**: minimal stub agent with placeholder system prompt, one example command, systemd service file template, smoke test, README walking through the init flow.

## agent_core API

The extension surface for an agent is a single class. Everything else is sensible defaults.

### Package layout

```
agent_core/
    pyproject.toml
    agent_core/
        __init__.py            re-exports: Agent, run_daemon, Command, Tool
        agent.py               Agent base class
        runtime.py             run_daemon() entry point
        protocol.py            NDJSON message types
        client.py              socket client
        daemon.py              daemon server core
        config.py              BaseConfig dataclass + env loader

        inference.py
        retrieval.py
        websearch.py

        wisdom.py
        learning.py
        learning_scanner.py
        profile.py
        allowlist.py
        approval_registry.py
        channels.py
        scratchpad.py
        conversation.py
        reasoning.py

        tools/
            __init__.py
            executor.py
            builtin.py

        commands/
            __init__.py
            registry.py
            builtin.py

        prompts/
            __init__.py
            builder.py

        utils/
            frontmatter.py
            chunker.py
            sanitizer.py
            fetcher.py
            converter.py

        adapters/
            __init__.py
            cli.py
            discord_gateway.py    behind agent_core[discord] extra

    tests/
        ... contract tests + module tests
```

### The `Agent` class

```python
from agent_core import Agent, Command, run_daemon

class MyAgent(Agent):
    name = "my-agent"
    env_prefix = "MYAGENT_"

    def system_prompt(self, ctx) -> str:
        return "You are my-agent, a..."

    commands = [MyResearchCommand, MyCompileCommand]
    tools = [MyDomainTool]

    categorizer = None     # optional Categorizer subclass

    def decide_mode(self, message, ctx):
        ...                # optional override of agent_core's heuristic

if __name__ == "__main__":
    run_daemon(MyAgent())
```

### Design choices for the extension surface

1. **Class-based, not config-based.** Commands and tools have code, not just data. A class is honest about that.
2. **Built-ins are opt-out, not opt-in.** Every agent gets `/help`, `/clear`, `/profile`, `/scratch`, basic tools (e.g., fetch_url, search_vault) by default. An agent can remove built-ins explicitly. This avoids every new agent re-wiring the same eight commands.
3. **No plugin discovery.** Commands and tools are listed explicitly on the class. No entry points, no decorators that auto-register on import. Explicit is debuggable.
4. **Storage paths parameterized, not hardcoded.** Managers receive paths via constructor (`WisdomManager(path, agent_name)`); they do not read env vars internally. The agent's daemon loads config at the top and injects.
5. **Adapters opt-in.** `run_daemon(agent)` starts the socket daemon. CLI is a separate process (`python -m agent_core.adapters.cli`). Discord gateway is its own entry point. An agent can run headless (socket only) if nothing else is needed.

### Config and environment variables

`agent_core` provides a `BaseConfig` dataclass with the same fields PAL uses today (inference_url, model, socket_path, history_depth, vault_path, collection_id, username, searxng_url, fetch_max_bytes, fetch_timeout, max_inference_body_chars, batch_enabled, batch_inference_url, batch_model, channels_dir, scratchpad_max_bytes). The env var prefix is read from `Agent.env_prefix`, so the same field `inference_url` reads from `PAL_INFERENCE_URL` for PAL and `RELAB_INFERENCE_URL` for RE Lab. Agents can subclass `BaseConfig` to add fields; the prefix machinery applies uniformly.

## Storage Conventions

The vault is shared across all agents. Per-agent state lives outside the vault.

### Shared (one copy, all agents read)

```
$AGENT_VAULT_PATH/
    Knowledge/
    Health/
    articles/
    raw/
    ...
```

The vault is one flat namespace of categorized content. Retrieval queries hit the same collection regardless of which agent is asking. No per-agent subtree bias parameter on retrieval. The category structure inside the vault already does the organizing job; an agent's query naturally surfaces relevant content.

### Per-agent (isolated state)

```
~/.local/share/<agent_name>/
    wisdom/
    learning/
    profile.md
    allowlist.yaml
    approval_registry.json
    channels/
        <channel_id>/
            conversation.json
            scratchpad.md
```

Wisdom, learning, profile, allowlist, approval registry, channels, and scratchpads are all per-agent. Profile is per-agent for now (revisit if duplication becomes annoying after the second agent ships). Allowlist is per-agent because different agents may have different fetch policies. Approval registry is per-agent because approval flows are stateful.

### Sockets and runtime files

```
$XDG_RUNTIME_DIR/<agent_name>.sock
```

### PAL-specific storage migration

PAL's existing paths (`/mnt/secondary/PAL/...`, `~/.local/share/pal/...`) line up with the new convention because PAL's name slug is "pal". The conventions matter for *new* agents; PAL is a no-op rename. Any reorganization of the vault contents on the server is independent of this work.

## Agent_Template Repo Structure

Starter-style scaffold. After cloning and running the init script, the result is a runnable agent with one stub command.

### File layout

```
Agent_Template/
    README.md
    pyproject.toml                       name = "{{AGENT_NAME}}", deps include agent_core@<tag>
    scripts/
        init-agent.sh
    {{agent_pkg}}/
        __init__.py
        __main__.py                      python -m <agent_name> -> run_daemon
        agent.py                         Agent subclass with stubs
        prompts/
            system.md                    stub system prompt
        commands/
            __init__.py
            hello.py                     one example command
        tools/
            __init__.py                  empty; example commented out
    systemd/
        {{agent_name}}-daemon.service
        {{agent_name}}-cli.service
    tests/
        __init__.py
        test_smoke.py
    .env.example
```

### Placeholders

| Placeholder | Example for "re-lab" | Where it appears |
|---|---|---|
| `{{AGENT_NAME}}` | `re-lab` | README, prompts, service files, pyproject |
| `{{agent_pkg}}` | `re_lab` | Python package directory (hyphens to underscores) |
| `{{AGENT_CLASS}}` | `RELabAgent` | Class name in agent.py |
| `{{AGENT_PREFIX}}` | `RELAB` | env var prefix |
| `{{AGENT_DESCRIPTION}}` | (prompted at init) | README, pyproject description |

### `init-agent.sh` behavior

1. Validates the name (lowercase slug, hyphens, no spaces).
2. Derives `agent_pkg` (underscores), `AGENT_CLASS` (PascalCase), `AGENT_PREFIX` (uppercase, no hyphens).
3. Renames the `{{agent_pkg}}/` directory and the systemd service files.
4. Runs `find . -type f \( -name '*.py' -o -name '*.md' -o -name '*.toml' -o -name '*.service' -o -name '*.example' \) -exec sed -i ...` for each placeholder.
5. Removes itself and the README's "before init" section.
6. Optionally runs `git init` (default yes; flag to skip if cloning into an existing repo).
7. Prints next steps: install agent_core, set env vars, run the smoke test, install the systemd unit.

### What the template explicitly omits

- No Discord adapter wired by default. The README documents how to opt in: edit `pyproject.toml` to add `agent_core[discord]`, instantiate the gateway in `__main__.py`.
- No vault content or seed wisdom.
- No example research, compile, or categorizer logic. Those are PAL-specific patterns, not template patterns.
- No CI workflow files.

The template is intentionally minimal. Three goals: (1) prove `agent_core`'s API works end-to-end, (2) compile and run on first attempt, (3) leave a clear empty space where the agent author writes their actual logic.

## Migration Sequence

Approach 1 from the brainstorm: leaves-inward, each numbered step is its own PR (or short PR series) against PAL. Tag `agent_core` after each step. PAL keeps shipping throughout. PAL's tests pass at the end of every step.

### Step 0 (pre-flight)

Bootstrap `agent_core` repo: `pyproject.toml`, package skeleton, empty modules, CI scaffold, README. Tag `v0.0.0`. PAL does not depend on it yet.

### Phase A: leaf utilities

**Step 1.** Move `frontmatter`, `chunker`, `sanitizer`, `converter`, `fetcher` to `agent_core.utils.*`. Tests come along. Tag `v0.1.0`. PAL adds the `agent_core` dep at `v0.1.0`, deletes its copies, updates imports.

### Phase B: stateless clients

**Step 2.** Move `inference`, `retrieval`, `websearch` to `agent_core`. Parameterize PAL_-specific config reads. Tag `v0.2.0`. PAL updates.

### Phase C: stateful managers

**Step 3.** Move `wisdom`, `learning`, `learning_scanner`, `profile`, `allowlist`, `approval_registry`, `reasoning` to `agent_core`. Tag `v0.3.0`. PAL updates.

**Step 4.** Move `channels`, `scratchpad`, `conversation` to `agent_core`. Tag `v0.4.0`.

### Phase D: tool, command, prompt scaffolding

**Step 5.** Build `agent_core.tools.executor`, `agent_core.tools.builtin`, `agent_core.commands.registry`, `agent_core.commands.builtin`, `agent_core.prompts.builder`. Tag `v0.5.0`. PAL's `tools.py` and `commands.py` get split: generic dispatch goes to `agent_core`, PAL-specific tool implementations and slash command handlers stay in `pal/`. This is the largest design lift; defining the registration API is the bulk of the work.

### Phase E: daemon, protocol, CLI

**Step 6.** Move `protocol`, `client` to `agent_core`. Move daemon core (connection loop, dispatch, lifecycle) to `agent_core.daemon` plus `agent_core.runtime.run_daemon`. PAL's `daemon.py` becomes a thin module: subclasses `Agent`, registers PAL's commands/tools, calls `run_daemon`. Move CLI REPL to `agent_core.adapters.cli`. Tag `v0.6.0`.

Expected diff in PAL: `daemon.py` shrinks from 1998 LOC to ~150-300 LOC; `tools.py` shrinks from 1544 LOC to PAL-specific tool impls only; `cli.py` shrinks dramatically or is replaced by `agent_core.adapters.cli` invocation.

### Phase F: Discord gateway adapter

**Step 7.** Move `discord_adapter.py` to `agent_core.adapters.discord_gateway`, behind the `agent_core[discord]` extra. PAL keeps `discord_interactions.py` (the webhook) entirely; that stays PAL-specific. Tag `v0.7.0`.

### Phase G: template

**Step 8.** Build `Agent_Template` repo: file scaffolding, init script, stub agent, stub command, systemd templates. Pin `agent_core@v0.7.0`. Smoke-test by running `init-agent.sh test-agent` and verifying the resulting agent boots, accepts a chat message, and gets a response from inference. Tag `v1.0.0` on `agent_core`.

### Phase H: stabilize

**Step 9.** Burn-in period: PAL runs on `agent_core@v1.0.0` for a week or two of normal use. Any rough edges in the API get fixed (tag patch versions). No new agent built yet; the goal is "PAL is happy on `agent_core` for real workloads" before another consumer arrives.

## Testing Strategy

### agent_core's own tests

**Unit tests (per module):** every module imported into `agent_core` brings its existing PAL tests with it. PAL's test files for moved modules move alongside the code. PAL does not keep tests for code it no longer owns.

**Contract tests (new, in Step 6):** verify the extension-point surface. Concretely:
- `test_minimal_agent_boots`: define a one-line `Agent` subclass, call `run_daemon`, assert the socket comes up.
- `test_agent_receives_chat`: send a chat message, mock inference, verify response flows back.
- `test_command_registration`: agent registers a command, slash dispatch works.
- `test_tool_registration`: agent registers a tool, executor finds it.
- `test_builtin_commands_present`: `/help` works without the agent doing anything.
- `test_builtin_can_be_disabled`: agent opts out of `/help`, `/help` returns "unknown command".

These fail when the API is accidentally broken. They live in `agent_core` forever.

### PAL's tests during migration

PAL's test suite runs on every migration PR. Most of PAL's current tests are module-level and move with the code. Integration tests for PAL-specific flows (categorizer, compiler, researcher, article pipeline) stay in PAL. After Step 6, PAL's test count drops 60-70%, which is the right outcome: most of what PAL was testing was not really PAL-specific.

### "PAL still works" gate

Every migration PR satisfies:
- `agent_core` tests pass on its own.
- PAL's tests pass with the new `agent_core` dep.
- A manual smoke-test checklist runs: `pal-daemon` starts, CLI connects, `/help` works, a chat message returns a response, `/research` (post-Step 5) works, Discord bridge (post-Step 7) works.

The smoke checklist lives in `docs/migration-smoke-test.md` and gets updated as new functionality reaches `agent_core`.

### Template tests

Smoke only: `from <agent_pkg> import <AgentClass>; assert <AgentClass>().name == "<agent-name>"`. Verifies package layout works and `Agent` instantiates. New-agent authors write their own tests as they add commands.

### What is not tested at this layer

- System prompt content quality (vibes, not regression-tested).
- Inference response quality (model concern).
- Cross-agent interactions (no second agent yet).
- Storage path migrations (manual server-side ops).

### CI

`agent_core` gets a GitHub Actions workflow: pytest on push and PR, Python 3.12+. No deploy steps. PAL's existing CI continues unchanged; it just now installs `agent_core@<pinned tag>`. Template CI is one job: install, run smoke test.

## Parked Open Questions

Recorded for revisit, not blockers for shipping:

1. **Inter-agent communication.** Direct vault writes vs socket messages between agents. Defer until two agents exist and the pattern's frequency is observable.
2. **Launcher UX.** CLI startup screen, per-session command, separate process vs exec wrapper. Defer until friction with multiple agents is real.
3. **Multi-agent autonomous polling.** Each agent owns its own wake-up schedule (probably) vs dispatcher layer. Defer until autonomous polling actually ships.
4. **Wisdom cross-pollination.** Per-agent dirs are filesystem-readable; if cross-reads become useful, design then.
5. **Profile sharing.** Per-agent for now. Revisit if duplication becomes annoying.
6. **Discord Interactions webhook generalization.** Stays PAL-only. Lift to `agent_core` only if a second agent wants the same surface.
7. **`agent_core` release cadence.** Tag-on-demand after v1.0.0, not on a fixed schedule.

## Hard Non-Goals

Not "parked"; explicitly excluded.

- `agent_core` published to PyPI. Stays private, git-installed.
- Plugin discovery / entry-point auto-registration.
- A monorepo for all agents.
- File-based config (YAML/TOML) instead of env vars.

## Decisions Summary

| Decision | Choice |
|---|---|
| Scope | Extraction + starter-style template (not a full second agent) |
| Repos | `agent_core` (library) + `Agent_Template` (scaffold), separate |
| PAL's role | Migrates now, becomes first `agent_core` consumer |
| Surfaces in `agent_core` | CLI REPL + Discord gateway (opt-in) |
| Surfaces staying PAL | Discord Interactions HTTP webhook |
| Template style | Starter (clone + init script), not generator |
| `agent_core` API | Class-based `Agent` subclass, opt-out built-ins, no plugin magic |
| Storage | Shared vault; per-agent wisdom, learning, scratchpad, profile, allowlist, approval registry |
| Migration | Leaves-inward in 9 numbered steps (plus pre-flight), daemon last, burn-in before v1.0.0 |
| Tests | Module tests move with code, contract tests for the API, template ships smoke test only |
