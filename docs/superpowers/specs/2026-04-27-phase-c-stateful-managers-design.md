# Phase C: Stateful Managers Migration Design

Status: design approved, ready for implementation planning
Date: 2026-04-27
Origin: PAL repo. Move to agent_core when that repo takes over docs ownership.
Parent: `docs/superpowers/specs/2026-04-25-agent-core-extraction-design.md` (umbrella extraction design)
Predecessors:
- `docs/superpowers/plans/2026-04-25-agent-core-extraction-phase-a.md` (Phase A, merged 2026-04-26 in PR #1)
- `docs/superpowers/plans/2026-04-27-agent-core-extraction-phase-b.md` (Phase B, merged 2026-04-27 in PR #2)

## Context

Phase A extracted the leaf utilities. Phase B extracted the stateless HTTP clients (`reasoning`, `inference`, `retrieval`, `websearch`). PAL now consumes `agent_core@v0.2.0` with 9 modules in active use.

Phase C extracts six PAL modules that hold or read agent-specific state on disk: `approval_registry` (in-memory only, included for completeness), `profile`, `allowlist`, `wisdom`, `learning`, and `learning_scanner`. After this phase, agent_core will expose 15 modules total.

The wrinkle that did not exist in Phases A or B: these managers are vault-rooted. PAL writes wisdom to `<vault>/_wisdom/`, learnings to `<vault>/_learning/`, profile to `<vault>/_profile/<username>.md`, and allowlist to `<vault>/_config/allowlist.md`. The umbrella spec aspirationally described a separate XDG-style state tree, but the actual code stores everything inside the vault. Phase C reconciles the umbrella's per-agent intent with the vault-rooted reality by adding a per-agent subdirectory under each existing storage prefix. PAL's data lands at `_wisdom/pal/`, RE Lab at `_wisdom/re-lab/`, etc.

## Goals

1. Move six modules from PAL into agent_core, byte-identically except for the `agent_name` parameterization required to namespace storage and the corresponding path properties.
2. Migrate PAL's existing on-disk state into the new namespaced subdirectories with a one-time `mv` runbook executed at deploy.
3. Tag `agent_core@v0.3.0` and migrate PAL to consume it. PAL keeps shipping throughout; per-module commits preserve bisect.
4. Add two new tests in agent_core (`test_two_agents_have_isolated_dirs` shape) to lock the multi-agent namespacing invariant for `wisdom` and `learning`.

## Non-Goals

1. Adopting the umbrella spec's full XDG layout. Defer until multi-agent operation makes the vault-rooted layout uncomfortable.
2. Changing the on-disk markdown format of any wisdom, learning, profile, or allowlist file. Frontmatter shape and body structure stay identical.
3. Adding persistence to `ApprovalRegistry`. Stays in-memory.
4. Generalizing `learning_scanner`'s English-language signal regex (`_SIGNAL_PATTERNS`). The patterns ship as-is; agents that want a different signal set override the prompt template entirely.
5. Refactoring `learning_scanner` beyond the agent_name parameterization. The 252-LOC orchestration logic stays.
6. Designing `agent_core.BaseConfig`, the `Agent` base class, or any other Phase E machinery.
7. Moving the per-channel state managers (`channels`, `scratchpad`, `conversation`). Those are Phase D.
8. Touching anything in PAL outside the construction sites and import lines for these six modules.

## Architecture

The dependency direction stays the same: PAL depends on agent_core via a git-pinned tag.

```
inference_server (HTTP, on 192.168.1.14, localhost-only)
        ^
        | httpx
        |
   +----+--------+
   |  agent_core | <- pip dep -- PAL
   |  v0.3.0     |
   +-------------+
```

After Phase C, `agent_core` exposes 15 modules:

```
agent_core/
    utils/
        frontmatter.py     (Phase A)
        chunker.py         (Phase A)
        sanitizer.py       (Phase A)
        converter.py       (Phase A)
        fetcher.py         (Phase A)
    reasoning.py           (Phase B)
    inference.py           (Phase B)
    retrieval.py           (Phase B)
    websearch.py           (Phase B)
    approval_registry.py   (Phase C)
    profile.py             (Phase C)
    allowlist.py           (Phase C)
    wisdom.py              (Phase C)
    learning.py            (Phase C)
    learning_scanner.py    (Phase C)
```

The six Phase C modules live at the top level of the package, alongside the Phase B clients, matching the umbrella spec's package layout. They are not in `utils/` because they are stateful managers tied to specific agent state, not generic utilities.

## Storage Layout

Per-agent state lives inside the shared vault, namespaced by agent name. The convention is "existing PAL prefix, plus an agent_name subdir."

| Module | Today | After Phase C |
|---|---|---|
| `ApprovalRegistry` | in-memory | in-memory (no change) |
| `ProfileManager` | `<vault>/_profile/<username>.md` | `<vault>/_profile/<agent_name>/<username>.md` |
| `AllowlistManager` | `<vault>/_config/allowlist.md` | `<vault>/_config/<agent_name>/allowlist.md` |
| `WisdomManager` | `<vault>/_wisdom/*.md` | `<vault>/_wisdom/<agent_name>/*.md` |
| `LearningManager` | `<vault>/_learning/*.md` | `<vault>/_learning/<agent_name>/*.md` |

`agent_name` for PAL is `"pal"` (lowercase, matching directory naming convention).

Profile keeps the username axis under the agent dir (`_profile/<agent_name>/<username>.md`) rather than collapsing to `_profile/<agent_name>.md`. This costs nothing today and leaves room for multi-user-per-agent later if it ever becomes useful. In the homelab there is one user, so PAL writes to `_profile/pal/<username>.md` and reads only that file.

Cross-agent reads are possible but not the default. An agent's manager only lists/reads its own subdirectory; the per-agent isolation is filesystem-namespaced rather than enforced by the code.

### Server-side data migration

One-time, runs at deploy after the PR merges. Lives in the PR description as a runbook step:

```bash
cd /mnt/secondary/agent-workspace/vault
mkdir -p _wisdom/pal _learning/pal _profile/pal _config/pal
shopt -s nullglob
mv _wisdom/*.md _wisdom/pal/    2>/dev/null || true
mv _learning/*.md _learning/pal/ 2>/dev/null || true
mv _profile/*.md _profile/pal/   2>/dev/null || true
mv _config/allowlist.md _config/pal/allowlist.md  2>/dev/null || true
```

The migration runs between `git pull` and `systemctl --user start pal-daemon`. Starting the daemon before the data migration is not destructive; it just sees empty namespaced directories until the `mv` completes.

The `_wisdom/ratings.md` file (used by `LearningManager.add_rating`) is in `_learning/`, not `_wisdom/`, so the wildcard `mv _learning/*.md _learning/pal/` catches it.

## Constructor Signatures

All vault-rooted managers gain `agent_name` as a required positional argument after `vault_path`. ApprovalRegistry's signature does not change. LearningScanner's signature gains `agent_name` for prompt interpolation plus an optional `prompt_template` override.

| Module | Today | After Phase C |
|---|---|---|
| `ApprovalRegistry` | `(expiry_minutes: int = 15)` | unchanged |
| `ProfileManager` | `(vault_path: Path, username: str)` | `(vault_path: Path, agent_name: str, username: str)` |
| `AllowlistManager` | `(vault_path: Path)` | `(vault_path: Path, agent_name: str)` |
| `WisdomManager` | `(vault_path: Path)` | `(vault_path: Path, agent_name: str)` |
| `LearningManager` | `(vault_path: Path)` | `(vault_path: Path, agent_name: str)` |
| `LearningScanner` | `(learning_manager, inference_client, ...)` | adds `agent_name: str` and optional `prompt_template: str | None = None` |

The `_<thing>_dir` properties update to include the agent_name subdir:

```python
# WisdomManager today:
@property
def wisdom_dir(self) -> Path:
    return self.vault_path / "_wisdom"

# After Phase C:
@property
def wisdom_dir(self) -> Path:
    return self.vault_path / "_wisdom" / self.agent_name
```

Same shape for `learning_dir`, `allowlist_path`, `profile_path`. ApprovalRegistry has no path properties.

### LearningScanner prompt template

The current PAL prompt contains the literal string `"shape PAL's future behavior across sessions"`. After Phase C, agent_core ships a default template that interpolates `{agent_name}`:

```
A durable lesson is a behavioral preference, a correction, or a confirmed
approach that should shape {agent_name}'s future behavior across sessions...
```

PAL constructs the scanner with `agent_name="PAL"` (mixed case for the human-readable interpolation; the directory namespacing uses the lowercase `"pal"` slug). Different agents pass their own readable name. Agents that want a substantively different prompt pass `prompt_template=...` to override.

## PAL-Side Construction Sites

PAL's `daemon.py` constructs each manager today; Phase C updates these construction sites to pass `agent_name`. The agent name is hardcoded `"pal"` in PAL (matching the directory namespacing); for the LearningScanner's prompt interpolation, PAL uses `"PAL"` mixed case.

```python
# Before
self.profile = ProfileManager(config.vault_path, config.username)
self.allowlist = AllowlistManager(config.vault_path)
self.wisdom = WisdomManager(config.vault_path)
self.learning = LearningManager(config.vault_path)
self.learning_scanner = LearningScanner(self.learning, self.inference, ...)

# After
self.profile = ProfileManager(config.vault_path, "pal", config.username)
self.allowlist = AllowlistManager(config.vault_path, "pal")
self.wisdom = WisdomManager(config.vault_path, "pal")
self.learning = LearningManager(config.vault_path, "pal")
self.learning_scanner = LearningScanner(
    self.learning, self.inference, "PAL", ...
)
```

When `agent_core.BaseConfig` lands in Phase E, these strings move into the config; for now they are inline literals at the daemon's construction call sites.

## Migration Order

agent_core gets six feature commits plus a version bump, in dependency-respecting order:

1. `approval_registry` (no storage; pure dataclass + dict; nothing on it depends on other agent_core code)
2. `profile` (single file, simplest persistent module)
3. `allowlist` (single file)
4. `wisdom` (directory of files)
5. `learning` (directory of files; required by LearningScanner)
6. `learning_scanner` (depends on LearningManager + InferenceClient; both already in agent_core after step 5)
7. Version bump to `0.3.0` and tag `v0.3.0`

PAL's feature branch gets seven commits:

1. Bump dep pin to `agent_core@v0.3.0`
2. Migrate `approval_registry` callers
3. Migrate `profile` callers
4. Migrate `allowlist` callers
5. Migrate `wisdom` callers
6. Migrate `learning` callers
7. Migrate `learning_scanner` callers (final per-module migration; the final smoke + clean install + PR open happens in this commit's task or a separate Task 14-style commit, decided in the implementation plan)

## Test Strategy

### agent_core

Each module's PAL test file moves alongside its source. Tests for stateful managers use pytest's `tmp_path` for the vault directory (PAL's existing pattern). The `mock_inference_server` fixture from Phase B handles `learning_scanner`'s LLM dispatch; no conftest extension is needed for Phase C.

The agent_name parameterization in tests: each test that constructs a manager passes a fixed agent name (e.g., `"test-agent"`). The path-namespacing is exercised implicitly because the tests instantiate the manager and call methods that all go through the agent_name-aware path properties.

Two new tests get added in the same commits that move the modules, locking the multi-agent invariant:

1. `tests/test_wisdom.py::test_two_agents_have_isolated_dirs` instantiates two `WisdomManager`s with the same `vault_path` but different `agent_name`s, adds an entry to each, and verifies each manager's `list()` returns only its own.
2. `tests/test_learning.py::test_two_agents_have_isolated_dirs` does the same shape for LearningManager.

Profile and allowlist are single-file rather than directory-based; the agent_name appears in the filesystem path either way, so a multi-agent test would verify nothing the existing tests don't already cover. No new test added there.

ApprovalRegistry has no storage; no new test needed.

### PAL

Each PAL migration commit runs the targeted PAL tests for that module's callers (same pattern as Phase B). The final smoke commit runs the broad PAL pytest excluding the 5 known-flaky integration files identified during Phase A:

```
--ignore=tests/test_daemon.py
--ignore=tests/test_integration.py
--ignore=tests/test_chat_research_integration.py
--ignore=tests/test_consolidate_integration.py
--ignore=tests/test_learning_e2e.py
```

The Phase B baseline was 714 tests passing in this scope. Phase C deletes four module-specific test files (test_wisdom, test_learning, test_profile, test_allowlist; possibly also test_learning_scanner and test_approval_registry depending on what exists). Expect a small reduction in PAL's count; agent_core picks up the moved tests.

### Manual smoke on the server post-merge

In addition to the standard daemon-startup + `/help` + chat checks from Phases A and B, exercise the migrated managers:

- `/wisdom list` and `/wisdom add <text>` (verifies WisdomManager reads and writes the namespaced path)
- `/profile` (verifies ProfileManager finds the migrated file)
- `/learnings` (verifies LearningManager finds migrated entries)
- A short conversation with a "thank you" or "actually" signal phrase (exercises learning_scanner end-to-end via the mock inference server during chat)
- A research approval flow if convenient (exercises ApprovalRegistry through real traffic)

The migration script must run between `git pull` and `systemctl --user start pal-daemon`. If the daemon starts before the migration, it sees empty `_wisdom/pal/` and `_learning/pal/` directories until the `mv` completes; not destructive but visibly empty in the meantime.

## Migration Risk and Mitigation

Phase C is mostly mechanical, but three patterns from Phases A and B continue to apply:

1. **String-form references.** `monkeypatch.setattr("pal.<module>.X", ...)` and similar string paths must be rewritten alongside `from pal.<module>` imports. Use the broad grep pattern `grep -rnE "pal\.<module>|\"pal\.<module>|'pal\.<module>" pal/ tests/` for each module's pre-flight.
2. **Test fixtures.** PAL's tests use `tmp_path` heavily for these managers, which is hermetic; no fixture migration is expected. If something turns up that depends on PAL's full conftest, take the same trim-and-bring approach Phase A used for Task 6's fetcher conftest.
3. **The `test_daemon.py` hang.** Pre-existing infrastructure issue (verified during Phases A and B). Skip in test runs; do not let it block migration commits.

Phase C's new risk:

4. **Server data migration timing.** If the daemon starts between `git pull` and the `mv` script, it sees empty namespaced dirs. Documented in the PR runbook to avoid this. The `mv` itself is idempotent (`|| true` on each line; `mkdir -p` for the destinations), so a partial run can be safely resumed.

## Out of Scope (parked for later phases)

- The umbrella spec's XDG-style layout. Revisit if the vault-rooted approach becomes uncomfortable.
- `agent_core.BaseConfig`, env var prefix machinery, configurable per-agent config dataclass. Phase E.
- `Agent` base class with extension points (system prompt, commands, tools). Phase E.
- Per-channel state managers (`channels`, `scratchpad`, `conversation`). Phase D.
- Building or scaffolding a second agent (RE Lab). Out of scope for the extraction; that is its own project.
- PAL's `pyyaml` direct-dep cleanup (test-only usage; transitively provided by agent_core). Track as a follow-up across the entire extraction.

## Decisions Summary

| Decision | Choice |
|---|---|
| Modules in scope | approval_registry, profile, allowlist, wisdom, learning, learning_scanner |
| Storage layout | Vault-rooted with per-agent subdir (`<vault>/_wisdom/<agent_name>/...`) |
| Constructor API | All vault-rooted managers gain `agent_name` positional after `vault_path` |
| Profile path shape | `_profile/<agent_name>/<username>.md` (keep username axis) |
| LearningScanner prompt | Default template with `{agent_name}` interpolation; optional override |
| ApprovalRegistry | unchanged signature, no persistence |
| Migration sequencing | approval_registry, profile, allowlist, wisdom, learning, learning_scanner |
| agent_core release | v0.3.0 with matching pyproject version (Phase B's lesson) |
| PAL-side commits | 7 (1 dep bump, 6 migrations) plus final smoke verification |
| Server data migration | One-time `mv` runbook in PR description; runs between `git pull` and `systemctl start` |
| New tests | 2 multi-agent isolation tests for wisdom and learning |
| Test scope | Same flaky-integration ignore list as Phase B |
