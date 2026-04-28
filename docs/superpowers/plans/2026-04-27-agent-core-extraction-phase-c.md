# agent_core Extraction Phase C Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move five stateful-manager modules (`approval_registry`, `profile`, `allowlist`, `wisdom`, `learning`) from PAL into `agent_core`, tag `agent_core@v0.3.0`, migrate PAL to consume the new tag, and run a one-time server-side data migration to put existing wisdom/learning/profile/allowlist files under per-agent subdirectories. After merge, PAL ships with all 14 of agent_core's modules in active use.

**Architecture:** Same two-repo split as Phases A and B. agent_core is the library; PAL is the consumer. Phase C's modules are file-storage managers (except `ApprovalRegistry`, in-memory only). Each vault-rooted manager gains an `agent_name` constructor argument and namespaces its files under `<vault>/_<thing>/<agent_name>/`. PAL's existing data on the server moves into `_<thing>/pal/` via a one-time `mv` runbook executed at deploy. `learning_scanner` stays in PAL with its import of `LearningManager` rewritten to point at agent_core; the scanner's full migration is deferred to Phase D or E.

**Tech Stack:** Python 3.12+, hatchling, pytest, GitHub Actions CI, git tags. No new agent_core runtime or dev deps; everything needed (httpx, pyyaml, etc.) is already declared from Phases A and B. PAL adds nothing new beyond the version bump.

**Repos involved:**
- agent_core: `/home/edible/Projects/agent_core` (existing; currently at `v0.2.0`)
- PAL: `/home/edible/Projects/PAL` (main checkout). PAL-side migration work happens in a feature-branch worktree at `/home/edible/Projects/PAL/.worktrees/agent-core-phase-c`.

**Reference:** spec at `docs/superpowers/specs/2026-04-27-phase-c-stateful-managers-design.md`. Builds on Phase A (`docs/superpowers/plans/2026-04-25-agent-core-extraction-phase-a.md`, merged 2026-04-26 in PR #1) and Phase B (`docs/superpowers/plans/2026-04-27-agent-core-extraction-phase-b.md`, merged 2026-04-27 in PR #2).

**Pre-flight: PAL caller graph (mapped during planning):**

| Module | PAL source callers | PAL test callers |
|---|---|---|
| `approval_registry` | `pal/daemon.py`, `pal/tools.py` (local import) | `tests/test_approval_registry.py` (deleted), `tests/test_chat_research_tools.py`, `tests/test_chat_compile_tools.py`, `tests/test_chat_consolidate_tools.py`, `tests/test_chat_reorg_tools.py`, `tests/test_tools_propose_promote.py`, `tests/test_daemon_scanner_approval.py`, `tests/test_batch_fallback_proposal.py` (4 sites), `tests/test_import.py` (3 sites including string-form `pal.approval_registry.ApprovalRegistry`), `tests/test_chat_research_integration.py` (2 sites; flaky-skipped), `tests/test_consolidate_integration.py` (flaky-skipped) |
| `profile` | `pal/daemon.py`, `pal/prompt_builder.py` | `tests/test_profile.py` (deleted), `tests/test_prompt_builder.py`, `tests/test_prompt_builder_commands.py` |
| `allowlist` | `pal/daemon.py` | `tests/test_allowlist.py` (deleted) |
| `wisdom` | `pal/daemon.py`, `pal/prompt_builder.py`, `pal/tools.py` | `tests/test_wisdom.py` (deleted), `tests/test_learning.py`, `tests/test_learning_commands.py`, `tests/test_prompt_builder.py`, `tests/test_prompt_builder_commands.py`, `tests/test_tools_propose_promote.py` |
| `learning` | `pal/daemon.py`, `pal/tools.py`, `pal/learning_scanner.py` | `tests/test_learning.py` (deleted, 5 sites), `tests/test_learning_commands.py`, `tests/test_learning_scanner.py`, `tests/test_learning_scanner_orchestrator.py`, `tests/test_learning_scanner_extract.py`, `tests/test_daemon_scanner_hook.py`, `tests/test_daemon_scanner_approval.py`, `tests/test_scanner_take_pending.py`, `tests/test_tools_add_learning.py`, `tests/test_learning_e2e.py` (flaky-skipped) |

PAL construction sites for the manager classes are all in `pal/daemon.py`:
- Line 141: `self.profile = ProfileManager(config.vault_path, username=config.username)`
- Line 142: `self.wisdom = WisdomManager(config.vault_path)`
- Line 147: `self.learning = LearningManager(config.vault_path)`
- Line 148: `self.allowlist = AllowlistManager(config.vault_path)`
- Line 231: `approval_registry = ApprovalRegistry()` (no change)

`pal/tools.py` and `pal/prompt_builder.py` accept manager instances via constructor; they do not construct the managers themselves.

---

## Task 1: Move `approval_registry` into agent_core

**Files:**
- Create: `/home/edible/Projects/agent_core/agent_core/approval_registry.py`
- Create: `/home/edible/Projects/agent_core/tests/test_approval_registry.py`

`ApprovalRegistry` has no on-disk state and no signature change. The migration is byte-identical except for the import path in the test file.

**Working directory:** `/home/edible/Projects/agent_core`. Use `.venv/bin/pytest`.

- [ ] **Step 1: Pre-flight, confirm fresh main**

```bash
cd /home/edible/Projects/agent_core
git fetch origin
git checkout main
git pull
git status
```

Expected: clean working tree on `main`, HEAD matches origin (most recent commit is whatever was last pushed during Phase B, e.g., `cdd8568` v0.2.0 bump or later).

- [ ] **Step 2: Copy the source byte-identically**

```bash
cp /home/edible/Projects/PAL/pal/approval_registry.py /home/edible/Projects/agent_core/agent_core/approval_registry.py
diff /home/edible/Projects/PAL/pal/approval_registry.py /home/edible/Projects/agent_core/agent_core/approval_registry.py
```

Expected: zero output. The module has no internal `pal` imports.

- [ ] **Step 3: Copy the test file**

```bash
cp /home/edible/Projects/PAL/tests/test_approval_registry.py /home/edible/Projects/agent_core/tests/test_approval_registry.py
```

- [ ] **Step 4: Update test imports**

Pre-flight grep:

```bash
grep -nE "pal\.approval_registry|\"pal\.approval_registry|'pal\.approval_registry" /home/edible/Projects/PAL/tests/test_approval_registry.py
```

Expected matches:
- Line 3: `from pal.approval_registry import ApprovalRegistry, ResearchProposal`
- Line 164: `from pal.approval_registry import Proposal`
- Lines 375 and 390: `from pal.approval_registry import ApprovalRegistry`

Use the Edit tool on `/home/edible/Projects/agent_core/tests/test_approval_registry.py`. The same module path appears 4 times. Use `replace_all=true`:

```
old: from pal.approval_registry
new: from agent_core.approval_registry
```

Then verify zero residual:

```bash
grep -nE "pal\.approval_registry|\"pal\.approval_registry|'pal\.approval_registry" /home/edible/Projects/agent_core/tests/test_approval_registry.py
```

Expected: zero matches.

- [ ] **Step 5: Run the tests**

```bash
cd /home/edible/Projects/agent_core
.venv/bin/pytest tests/test_approval_registry.py -v 2>&1 | tail -3
.venv/bin/pytest -v 2>&1 | tail -5
```

Expected: all approval_registry tests pass. Full suite: 106 (Phase B baseline) + N approval_registry tests.

- [ ] **Step 6: Commit**

```bash
cd /home/edible/Projects/agent_core
git add agent_core/approval_registry.py tests/test_approval_registry.py
git status
```

Verify only those two files staged. Then:

```bash
git commit -m "$(cat <<'EOF'
feat: add approval_registry module

In-memory store for proposal lifecycle (research, compile, reorg,
consolidate, promote, batch_fallback). Migrated from PAL byte-identically;
no signature change. Approvals do not persist across sessions.
EOF
)"
```

Do NOT push. Tasks 2 through 5 add the other modules; Task 6 tags v0.3.0 and pushes everything.

---

## Task 2: Move `profile` into agent_core

**Files:**
- Create: `/home/edible/Projects/agent_core/agent_core/profile.py`
- Create: `/home/edible/Projects/agent_core/tests/test_profile.py`

`ProfileManager` gains an `agent_name` constructor argument inserted between `vault_path` and `username`. The `profile_path` property nests under `<vault>/_profile/<agent_name>/<username>.md`.

- [ ] **Step 1: Copy the source as a starting point**

```bash
cp /home/edible/Projects/PAL/pal/profile.py /home/edible/Projects/agent_core/agent_core/profile.py
```

- [ ] **Step 2: Update the constructor signature and path property**

Use the Edit tool on `/home/edible/Projects/agent_core/agent_core/profile.py`:

Old:
```python
class ProfileManager:
    def __init__(self, vault_path: Path, username: str) -> None:
        self.vault_path = vault_path
        self.username = _sanitize_username(username)

    @property
    def profile_path(self) -> Path:
        return self.vault_path / "_profile" / f"{self.username}.md"
```

New:
```python
class ProfileManager:
    def __init__(self, vault_path: Path, agent_name: str, username: str) -> None:
        self.vault_path = vault_path
        self.agent_name = agent_name
        self.username = _sanitize_username(username)

    @property
    def profile_path(self) -> Path:
        return self.vault_path / "_profile" / self.agent_name / f"{self.username}.md"
```

- [ ] **Step 3: Copy and adapt the test file**

```bash
cp /home/edible/Projects/PAL/tests/test_profile.py /home/edible/Projects/agent_core/tests/test_profile.py
```

Pre-flight: count `pal.profile` references and ProfileManager construction sites:

```bash
grep -nE "pal\.profile|ProfileManager\(" /home/edible/Projects/agent_core/tests/test_profile.py
```

The import line(s) need rewriting to `agent_core.profile`. Each `ProfileManager(...)` construction needs an `agent_name` argument inserted; pass `"test-agent"` everywhere unless context calls for a different name.

Use the Edit tool to rewrite imports:

```
old: from pal.profile
new: from agent_core.profile
```

Then for each `ProfileManager(...)` construction, edit the call to insert `"test-agent"` as the second positional argument:

Example:
```python
# Old
manager = ProfileManager(tmp_path, "alice")

# New
manager = ProfileManager(tmp_path, "test-agent", "alice")
```

Apply this transformation to every construction site. If the original test passes `username=...` as a kwarg, the agent_name still goes positionally as the second argument:

```python
# Old
manager = ProfileManager(tmp_path, username="alice")

# New
manager = ProfileManager(tmp_path, "test-agent", username="alice")
```

After all edits, verify:

```bash
grep -nE "pal\.profile|\"pal\.profile|'pal\.profile" /home/edible/Projects/agent_core/tests/test_profile.py
```

Expected: zero matches.

```bash
grep -nE "ProfileManager\(" /home/edible/Projects/agent_core/tests/test_profile.py | wc -l
```

Note the construction count; each one should now have three positional/kwarg arguments (vault_path, agent_name, username) rather than two.

- [ ] **Step 4: Run tests**

```bash
cd /home/edible/Projects/agent_core
.venv/bin/pytest tests/test_profile.py -v 2>&1 | tail -3
.venv/bin/pytest -v 2>&1 | tail -5
```

Expected: all profile tests pass. Full suite cumulative count grows by the profile test count.

If a test fails because it constructed ProfileManager with the old signature and the rewrite missed it, locate and fix.

- [ ] **Step 5: Commit**

```bash
cd /home/edible/Projects/agent_core
git add agent_core/profile.py tests/test_profile.py
git status
```

Verify two files staged. Then:

```bash
git commit -m "$(cat <<'EOF'
feat: add profile module

User profile manager with per-agent namespacing. Adds agent_name to the
constructor signature; profile_path now nests under
<vault>/_profile/<agent_name>/<username>.md. Migrated from PAL with
the path property updated and all test construction sites passing
"test-agent" as the agent_name.
EOF
)"
```

---

## Task 3: Move `allowlist` into agent_core

**Files:**
- Create: `/home/edible/Projects/agent_core/agent_core/allowlist.py`
- Create: `/home/edible/Projects/agent_core/tests/test_allowlist.py`

`AllowlistManager` gains `agent_name` after `vault_path`; `allowlist_path` nests under `<vault>/_config/<agent_name>/allowlist.md`.

- [ ] **Step 1: Copy the source**

```bash
cp /home/edible/Projects/PAL/pal/allowlist.py /home/edible/Projects/agent_core/agent_core/allowlist.py
```

- [ ] **Step 2: Update the constructor signature and path property**

Use the Edit tool on `/home/edible/Projects/agent_core/agent_core/allowlist.py`:

Old:
```python
class AllowlistManager:
    def __init__(self, vault_path: Path) -> None:
        self.vault_path = vault_path

    @property
    def allowlist_path(self) -> Path:
        return self.vault_path / "_config" / "allowlist.md"
```

New:
```python
class AllowlistManager:
    def __init__(self, vault_path: Path, agent_name: str) -> None:
        self.vault_path = vault_path
        self.agent_name = agent_name

    @property
    def allowlist_path(self) -> Path:
        return self.vault_path / "_config" / self.agent_name / "allowlist.md"
```

- [ ] **Step 3: Copy and adapt the test file**

```bash
cp /home/edible/Projects/PAL/tests/test_allowlist.py /home/edible/Projects/agent_core/tests/test_allowlist.py
```

Use the Edit tool. Two transformations:

(a) Update the top-of-file import:
```
old: from pal.allowlist
new: from agent_core.allowlist
```

(b) Every `AllowlistManager(...)` construction gains `"test-agent"` as the second positional arg:

```python
# Old
manager = AllowlistManager(tmp_path)

# New
manager = AllowlistManager(tmp_path, "test-agent")
```

Verify:

```bash
grep -nE "pal\.allowlist" /home/edible/Projects/agent_core/tests/test_allowlist.py
```

Expected: zero matches.

- [ ] **Step 4: Run tests**

```bash
cd /home/edible/Projects/agent_core
.venv/bin/pytest tests/test_allowlist.py -v 2>&1 | tail -3
.venv/bin/pytest -v 2>&1 | tail -5
```

Expected: all allowlist tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/edible/Projects/agent_core
git add agent_core/allowlist.py tests/test_allowlist.py
git commit -m "$(cat <<'EOF'
feat: add allowlist module

Domain allowlist manager with per-agent namespacing. Adds agent_name to
the constructor; allowlist_path now nests under
<vault>/_config/<agent_name>/allowlist.md.
EOF
)"
```

---

## Task 4: Move `wisdom` into agent_core (with multi-agent isolation test)

**Files:**
- Create: `/home/edible/Projects/agent_core/agent_core/wisdom.py`
- Create: `/home/edible/Projects/agent_core/tests/test_wisdom.py`

`WisdomManager` gains `agent_name`; `wisdom_dir` nests under `<vault>/_wisdom/<agent_name>/`. This task also adds a new test (`test_two_agents_have_isolated_dirs`) that locks the multi-agent invariant.

- [ ] **Step 1: Copy the source**

```bash
cp /home/edible/Projects/PAL/pal/wisdom.py /home/edible/Projects/agent_core/agent_core/wisdom.py
```

- [ ] **Step 2: Update the constructor signature and path property**

Use the Edit tool on `/home/edible/Projects/agent_core/agent_core/wisdom.py`:

Old:
```python
class WisdomManager:
    def __init__(self, vault_path: Path) -> None:
        self.vault_path = vault_path

    @property
    def wisdom_dir(self) -> Path:
        return self.vault_path / "_wisdom"
```

New:
```python
class WisdomManager:
    def __init__(self, vault_path: Path, agent_name: str) -> None:
        self.vault_path = vault_path
        self.agent_name = agent_name

    @property
    def wisdom_dir(self) -> Path:
        return self.vault_path / "_wisdom" / self.agent_name
```

- [ ] **Step 3: Copy and adapt the test file**

```bash
cp /home/edible/Projects/PAL/tests/test_wisdom.py /home/edible/Projects/agent_core/tests/test_wisdom.py
```

Use the Edit tool:

(a) Top-of-file import: `from pal.wisdom` → `from agent_core.wisdom`

(b) Every `WisdomManager(...)` construction gains `"test-agent"` as the second positional arg.

Verify:

```bash
grep -nE "pal\.wisdom" /home/edible/Projects/agent_core/tests/test_wisdom.py
```

Expected: zero matches.

- [ ] **Step 4: Add the multi-agent isolation test**

Append the following test to `/home/edible/Projects/agent_core/tests/test_wisdom.py` (use the Edit tool to insert it at the bottom of the file, after the last existing test):

```python


def test_two_agents_have_isolated_dirs(tmp_path):
    """Two managers with the same vault_path but different agent_names see only their own entries."""
    pal = WisdomManager(tmp_path, "pal")
    relab = WisdomManager(tmp_path, "re-lab")

    pal.add("PAL idea", "Library should organize by topic.")
    relab.add("RE Lab idea", "Always grep before assuming.")

    pal_slugs = [e["slug"] for e in pal.list()]
    relab_slugs = [e["slug"] for e in relab.list()]

    assert pal_slugs == ["pal-idea"]
    assert relab_slugs == ["re-lab-idea"]
    assert (tmp_path / "_wisdom" / "pal" / "pal-idea.md").exists()
    assert (tmp_path / "_wisdom" / "re-lab" / "re-lab-idea.md").exists()
```

- [ ] **Step 5: Run tests**

```bash
cd /home/edible/Projects/agent_core
.venv/bin/pytest tests/test_wisdom.py -v 2>&1 | tail -5
.venv/bin/pytest -v 2>&1 | tail -5
```

Expected: all existing wisdom tests pass, plus `test_two_agents_have_isolated_dirs` passes.

- [ ] **Step 6: Commit**

```bash
cd /home/edible/Projects/agent_core
git add agent_core/wisdom.py tests/test_wisdom.py
git commit -m "$(cat <<'EOF'
feat: add wisdom module

Curated guidance entries with per-agent namespacing. Adds agent_name
to the constructor; wisdom_dir nests under
<vault>/_wisdom/<agent_name>/. Includes a new test asserting two
managers on the same vault with different agent_names see only their
own entries.
EOF
)"
```

---

## Task 5: Move `learning` into agent_core (with multi-agent isolation test)

**Files:**
- Create: `/home/edible/Projects/agent_core/agent_core/learning.py`
- Create: `/home/edible/Projects/agent_core/tests/test_learning.py`

`LearningManager` gains `agent_name`; `learning_dir` nests under `<vault>/_learning/<agent_name>/`. This task also adds a multi-agent isolation test.

- [ ] **Step 1: Copy the source**

```bash
cp /home/edible/Projects/PAL/pal/learning.py /home/edible/Projects/agent_core/agent_core/learning.py
```

- [ ] **Step 2: Update the constructor signature and path property**

Use the Edit tool on `/home/edible/Projects/agent_core/agent_core/learning.py`:

Old:
```python
class LearningManager:
    def __init__(self, vault_path: Path) -> None:
        self.vault_path = vault_path

    @property
    def learning_dir(self) -> Path:
        return self.vault_path / "_learning"
```

New:
```python
class LearningManager:
    def __init__(self, vault_path: Path, agent_name: str) -> None:
        self.vault_path = vault_path
        self.agent_name = agent_name

    @property
    def learning_dir(self) -> Path:
        return self.vault_path / "_learning" / self.agent_name
```

- [ ] **Step 3: Copy and adapt the test file**

```bash
cp /home/edible/Projects/PAL/tests/test_learning.py /home/edible/Projects/agent_core/tests/test_learning.py
```

Pre-flight: PAL's `tests/test_learning.py` imports `WisdomManager` from `pal.wisdom`; that needs rewriting too.

Use the Edit tool:

(a) Imports:
- `from pal.learning` → `from agent_core.learning`
- `from pal.wisdom` → `from agent_core.wisdom` (PAL's test_learning imports WisdomManager for promotion testing)

(b) Every `LearningManager(...)` construction gains `"test-agent"` as the second positional arg.

(c) Every `WisdomManager(...)` construction (used in the promotion tests in this file) also gains `"test-agent"` as the second positional arg.

Verify:

```bash
grep -nE "pal\.learning|pal\.wisdom" /home/edible/Projects/agent_core/tests/test_learning.py
```

Expected: zero matches.

- [ ] **Step 4: Add the multi-agent isolation test**

Append to `/home/edible/Projects/agent_core/tests/test_learning.py`:

```python


def test_two_agents_have_isolated_dirs(tmp_path):
    """Two managers with the same vault_path but different agent_names see only their own entries."""
    pal = LearningManager(tmp_path, "pal")
    relab = LearningManager(tmp_path, "re-lab")

    pal.add("PAL lesson", "Avoid the fire emoji.", source="chat")
    relab.add("RE Lab lesson", "Always pin clang versions.", source="chat")

    pal_slugs = [e["slug"] for e in pal.list()]
    relab_slugs = [e["slug"] for e in relab.list()]

    assert pal_slugs == ["pal-lesson"]
    assert relab_slugs == ["re-lab-lesson"]
    assert (tmp_path / "_learning" / "pal" / "pal-lesson.md").exists()
    assert (tmp_path / "_learning" / "re-lab" / "re-lab-lesson.md").exists()
```

- [ ] **Step 5: Run tests**

```bash
cd /home/edible/Projects/agent_core
.venv/bin/pytest tests/test_learning.py -v 2>&1 | tail -5
.venv/bin/pytest -v 2>&1 | tail -5
```

Expected: all existing learning tests pass, plus `test_two_agents_have_isolated_dirs` passes.

- [ ] **Step 6: Commit**

```bash
cd /home/edible/Projects/agent_core
git add agent_core/learning.py tests/test_learning.py
git commit -m "$(cat <<'EOF'
feat: add learning module

Extracted-lesson manager with per-agent namespacing. Adds agent_name
to the constructor; learning_dir nests under
<vault>/_learning/<agent_name>/. Includes a multi-agent isolation
test mirroring the wisdom module's.
EOF
)"
```

---

## Task 6: Bump version to 0.3.0 + tag + push

**Files:**
- Modify: `/home/edible/Projects/agent_core/pyproject.toml`

- [ ] **Step 1: Final full-suite run**

```bash
cd /home/edible/Projects/agent_core
.venv/bin/pytest -v 2>&1 | tail -10
```

Expected: full suite green. Cumulative count = 106 (Phase B baseline) + Phase C tests (the count depends on how many tests existed in PAL's test files for these 5 modules; expect roughly 50-80 additional tests, putting the total around 160-190).

- [ ] **Step 2: Bump pyproject version**

Use the Edit tool on `/home/edible/Projects/agent_core/pyproject.toml`:

Old:
```
version = "0.2.0"
```

New:
```
version = "0.3.0"
```

- [ ] **Step 3: Reinstall editable**

```bash
cd /home/edible/Projects/agent_core
.venv/bin/pip install -e ".[dev]" 2>&1 | tail -3
.venv/bin/pip show agent_core | grep -E "Name|Version"
```

Expected: `Version: 0.3.0`.

- [ ] **Step 4: Commit, tag, push**

```bash
cd /home/edible/Projects/agent_core
git add pyproject.toml
git commit -m "$(cat <<'EOF'
chore: bump version to 0.3.0

Phase C release: adds approval_registry, profile, allowlist, wisdom,
learning. PAL pins this tag in its Phase C migration. Vault-rooted
managers gain agent_name parameterization for per-agent storage
namespacing under <vault>/_<thing>/<agent_name>/.
EOF
)"

git tag v0.3.0
git push origin main
git push origin v0.3.0
```

- [ ] **Step 5: Verify CI on the tagged commit**

```bash
sleep 15
gh run list --repo EdibleTuber/agent_core --limit 3
```

Note the run id and status. Wait for completion if needed. Required: the run for the v0.3.0 commit reports `success`.

- [ ] **Step 6: Verify v0.3.0 install works in a fresh venv**

```bash
mkdir -p /tmp/agent_core_v030_test && cd /tmp/agent_core_v030_test
python3 -m venv .venv && source .venv/bin/activate
pip install --quiet "agent_core @ git+https://github.com/EdibleTuber/agent_core.git@v0.3.0"
pip show agent_core | grep -E "Name|Version"
python -c "
from agent_core.utils.frontmatter import parse_frontmatter
from agent_core.utils.chunker import chunk_markdown
from agent_core.utils.sanitizer import sanitize
from agent_core.utils.converter import DocumentConverter
from agent_core.utils.fetcher import URLFetcher
from agent_core.reasoning import shape_request
from agent_core.inference import InferenceClient
from agent_core.retrieval import RetrievalClient
from agent_core.websearch import WebSearchClient
from agent_core.approval_registry import ApprovalRegistry
from agent_core.profile import ProfileManager
from agent_core.allowlist import AllowlistManager
from agent_core.wisdom import WisdomManager
from agent_core.learning import LearningManager
print('OK')
"
deactivate
rm -rf /tmp/agent_core_v030_test
```

Expected: `Name: agent_core`, `Version: 0.3.0`, `OK`. All 14 modules import cleanly.

---

## Task 7: PAL worktree + bump dep to v0.3.0

**Files:**
- Create worktree: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-c` on new branch `feature/agent-core-extraction-phase-c`
- Modify: `/home/edible/Projects/PAL/.worktrees/agent-core-phase-c/pyproject.toml`

- [ ] **Step 1: Verify PAL main is up to date and clean**

```bash
cd /home/edible/Projects/PAL
git fetch origin
git checkout main
git pull
git status
```

Expected: clean working tree, HEAD matches origin. The Phase C spec commits (`c85d29b`, `5bd4835`) and the Phase B merge commit (`c3d5b08`) should be present.

- [ ] **Step 2: Create the PAL worktree**

```bash
cd /home/edible/Projects/PAL
git worktree add .worktrees/agent-core-phase-c -b feature/agent-core-extraction-phase-c
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
git status
git branch --show-current
```

Expected: clean worktree on `feature/agent-core-extraction-phase-c`.

- [ ] **Step 3: Set up venv**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
python3 -m venv .venv
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install -e ".[dev]" 2>&1 | tail -5
```

Expected: install succeeds with agent_core@v0.2.0 (current pin).

- [ ] **Step 4: Run baseline targeted tests**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
.venv/bin/pytest tests/test_approval_registry.py tests/test_profile.py tests/test_allowlist.py tests/test_wisdom.py tests/test_learning.py -v 2>&1 | tail -5
```

Expected: all five test files pass with PAL's current local manager modules. Note the count.

- [ ] **Step 5: Bump the agent_core pin**

Use the Edit tool on `/home/edible/Projects/PAL/.worktrees/agent-core-phase-c/pyproject.toml`:

Old:
```
"agent_core @ git+https://github.com/EdibleTuber/agent_core.git@v0.2.0",
```

New:
```
"agent_core @ git+https://github.com/EdibleTuber/agent_core.git@v0.3.0",
```

- [ ] **Step 6: Reinstall**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
.venv/bin/pip install -e ".[dev]" 2>&1 | tail -10
.venv/bin/pip show agent_core | grep -E "Name|Version"
```

Expected: `Version: 0.3.0`.

- [ ] **Step 7: Verify the new modules are importable**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
.venv/bin/python -c "
from agent_core.approval_registry import ApprovalRegistry
from agent_core.profile import ProfileManager
from agent_core.allowlist import AllowlistManager
from agent_core.wisdom import WisdomManager
from agent_core.learning import LearningManager
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 8: Run baseline targeted tests again with the new pin**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
.venv/bin/pytest tests/test_approval_registry.py tests/test_profile.py tests/test_allowlist.py tests/test_wisdom.py tests/test_learning.py -v 2>&1 | tail -5
```

Expected: same count as Step 4. PAL still uses its local `pal.X` modules; the new dep does not break anything.

- [ ] **Step 9: Commit**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
git add pyproject.toml
git status
```

Verify only `pyproject.toml`. Then:

```bash
git commit -m "$(cat <<'EOF'
chore: bump agent_core dependency to v0.3.0

Adds the five Phase C modules (approval_registry, profile, allowlist,
wisdom, learning) to PAL's transitive surface. PAL still uses its
own local copies; subsequent commits in this branch switch each one
over.
EOF
)"
```

Do NOT push.

---

## Task 8: Migrate PAL's `approval_registry` usage to agent_core

**Files modified:** `pal/daemon.py`, `pal/tools.py` (local import)
**Files deleted:** `pal/approval_registry.py`, `tests/test_approval_registry.py`
**Test files with imports rewritten (stay in PAL):** `tests/test_chat_research_tools.py`, `tests/test_chat_compile_tools.py`, `tests/test_chat_consolidate_tools.py`, `tests/test_chat_reorg_tools.py`, `tests/test_tools_propose_promote.py`, `tests/test_daemon_scanner_approval.py`, `tests/test_batch_fallback_proposal.py`, `tests/test_import.py`, `tests/test_chat_research_integration.py`, `tests/test_consolidate_integration.py`

- [ ] **Step 1: Pre-flight broad grep**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
grep -rnE "pal\.approval_registry|\"pal\.approval_registry|'pal\.approval_registry" pal/ tests/
```

Expected: many matches across the source files (`pal/daemon.py`, `pal/tools.py`) and test files listed above. Note the count.

- [ ] **Step 2: Bulk rewrite imports + string-form references**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
find pal tests -type f -name '*.py' -exec sed -i 's|from pal\.approval_registry|from agent_core.approval_registry|g' {} +
find pal tests -type f -name '*.py' -exec sed -i 's|import pal\.approval_registry|import agent_core.approval_registry|g' {} +
find pal tests -type f -name '*.py' -exec sed -i 's|"pal\.approval_registry|"agent_core.approval_registry|g' {} +
find pal tests -type f -name '*.py' -exec sed -i "s|'pal\.approval_registry|'agent_core.approval_registry|g" {} +
```

- [ ] **Step 3: Verify zero remaining `pal.approval_registry` references**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
grep -rnE "pal\.approval_registry" pal/ tests/
```

Expected: zero matches.

- [ ] **Step 4: Delete PAL's local module and its dedicated test**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
rm pal/approval_registry.py tests/test_approval_registry.py
```

- [ ] **Step 5: Verify imports**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
.venv/bin/python -c "
from pal.daemon import Daemon
from pal.tools import ToolExecutor
from agent_core.approval_registry import ApprovalRegistry, Proposal, ResearchProposal
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 6: Run targeted tests**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
.venv/bin/pytest tests/test_chat_research_tools.py tests/test_chat_compile_tools.py tests/test_chat_consolidate_tools.py tests/test_chat_reorg_tools.py tests/test_tools_propose_promote.py tests/test_daemon_scanner_approval.py tests/test_batch_fallback_proposal.py tests/test_import.py 2>&1 | tail -5
```

Expected: all listed test files pass.

- [ ] **Step 7: Stage and commit**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
git add -u pal tests
git status
```

Expected staged: 2 modified pal/ files (daemon, tools), 1 deleted (approval_registry.py), modifications across multiple test files, 1 deleted (test_approval_registry.py).

```bash
git commit -m "$(cat <<'EOF'
refactor: migrate approval_registry usage to agent_core

All callers (daemon, tools, plus the chat-tools and batch-fallback
test files) now import from agent_core.approval_registry. Deletes
PAL's copy of the module and its dedicated test. The string-form
references in test_import.py are also rewritten.
EOF
)"
```

---

## Task 9: Migrate PAL's `profile` usage to agent_core

**Files modified:** `pal/daemon.py` (import + construction at line 141), `pal/prompt_builder.py` (import only; receives ProfileManager via constructor)
**Files deleted:** `pal/profile.py`, `tests/test_profile.py`
**Test files with imports rewritten (stay in PAL):** `tests/test_prompt_builder.py`, `tests/test_prompt_builder_commands.py`

- [ ] **Step 1: Pre-flight broad grep**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
grep -rnE "pal\.profile|\"pal\.profile|'pal\.profile" pal/ tests/
```

Note the count.

- [ ] **Step 2: Bulk rewrite imports**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
find pal tests -type f -name '*.py' -exec sed -i 's|from pal\.profile|from agent_core.profile|g' {} +
find pal tests -type f -name '*.py' -exec sed -i 's|import pal\.profile|import agent_core.profile|g' {} +
find pal tests -type f -name '*.py' -exec sed -i 's|"pal\.profile|"agent_core.profile|g' {} +
find pal tests -type f -name '*.py' -exec sed -i "s|'pal\.profile|'agent_core.profile|g" {} +
```

- [ ] **Step 3: Update the `daemon.py` construction site**

Use the Edit tool on `/home/edible/Projects/PAL/.worktrees/agent-core-phase-c/pal/daemon.py`:

Old:
```python
self.profile = ProfileManager(config.vault_path, username=config.username)
```

New:
```python
self.profile = ProfileManager(config.vault_path, "pal", username=config.username)
```

- [ ] **Step 4: Update test construction sites**

PAL's `tests/test_prompt_builder.py` and `tests/test_prompt_builder_commands.py` may construct ProfileManager directly. Find sites:

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
grep -nE "ProfileManager\(" pal/ tests/
```

For each construction in `tests/`, insert `"test-agent"` (or `"pal"` if the test specifically expects PAL paths) as the second positional arg. Use the Edit tool per-site.

- [ ] **Step 5: Verify zero remaining `pal.profile` references**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
grep -rnE "pal\.profile" pal/ tests/
```

Expected: zero matches.

- [ ] **Step 6: Delete PAL's local module and test**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
rm pal/profile.py tests/test_profile.py
```

- [ ] **Step 7: Verify imports**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
.venv/bin/python -c "
from pal.daemon import Daemon
from pal.prompt_builder import SystemPromptBuilder
from agent_core.profile import ProfileManager
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 8: Run targeted tests**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
.venv/bin/pytest tests/test_prompt_builder.py tests/test_prompt_builder_commands.py 2>&1 | tail -5
```

Expected: all pass. If any test fails because a ProfileManager construction was missed, locate it via the grep in Step 4 and fix.

- [ ] **Step 9: Commit**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
git add -u pal tests
git status
git commit -m "$(cat <<'EOF'
refactor: migrate profile usage to agent_core

All callers now import from agent_core.profile. daemon.py construction
site updated to pass agent_name="pal". PAL's copy of the module and
its dedicated test deleted. Test construction sites in
test_prompt_builder*.py updated to pass "test-agent".
EOF
)"
```

---

## Task 10: Migrate PAL's `allowlist` usage to agent_core

**Files modified:** `pal/daemon.py` (import + construction at line 148)
**Files deleted:** `pal/allowlist.py`, `tests/test_allowlist.py`

- [ ] **Step 1: Pre-flight broad grep**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
grep -rnE "pal\.allowlist|\"pal\.allowlist|'pal\.allowlist" pal/ tests/
```

Expected: 2 matches (`pal/daemon.py:27`, `tests/test_allowlist.py:6`).

- [ ] **Step 2: Bulk rewrite**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
find pal tests -type f -name '*.py' -exec sed -i 's|from pal\.allowlist|from agent_core.allowlist|g' {} +
find pal tests -type f -name '*.py' -exec sed -i 's|import pal\.allowlist|import agent_core.allowlist|g' {} +
find pal tests -type f -name '*.py' -exec sed -i 's|"pal\.allowlist|"agent_core.allowlist|g' {} +
find pal tests -type f -name '*.py' -exec sed -i "s|'pal\.allowlist|'agent_core.allowlist|g" {} +
```

- [ ] **Step 3: Update the `daemon.py` construction site**

Use the Edit tool on `/home/edible/Projects/PAL/.worktrees/agent-core-phase-c/pal/daemon.py`:

Old:
```python
self.allowlist = AllowlistManager(config.vault_path)
```

New:
```python
self.allowlist = AllowlistManager(config.vault_path, "pal")
```

- [ ] **Step 4: Verify zero remaining refs**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
grep -rnE "pal\.allowlist" pal/ tests/
```

Expected: zero matches.

- [ ] **Step 5: Delete PAL's local module and test**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
rm pal/allowlist.py tests/test_allowlist.py
```

- [ ] **Step 6: Verify imports**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
.venv/bin/python -c "
from pal.daemon import Daemon
from agent_core.allowlist import AllowlistManager
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 7: Commit**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
git add -u pal tests
git status
git commit -m "$(cat <<'EOF'
refactor: migrate allowlist usage to agent_core

Single caller (daemon.py) now imports from agent_core.allowlist;
construction site passes agent_name="pal". PAL's copy of the module
and its test deleted.
EOF
)"
```

---

## Task 11: Migrate PAL's `wisdom` usage to agent_core

**Files modified:** `pal/daemon.py` (import + construction at line 142), `pal/prompt_builder.py` (import only), `pal/tools.py` (import only)
**Files deleted:** `pal/wisdom.py`, `tests/test_wisdom.py`
**Test files with imports rewritten (stay in PAL):** `tests/test_learning.py` (note: this file moves to agent_core in Task 5; the PAL copy gets deleted in Task 12), `tests/test_learning_commands.py`, `tests/test_prompt_builder.py`, `tests/test_prompt_builder_commands.py`, `tests/test_tools_propose_promote.py`

- [ ] **Step 1: Pre-flight broad grep**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
grep -rnE "pal\.wisdom|\"pal\.wisdom|'pal\.wisdom" pal/ tests/
```

Note the count.

- [ ] **Step 2: Bulk rewrite**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
find pal tests -type f -name '*.py' -exec sed -i 's|from pal\.wisdom|from agent_core.wisdom|g' {} +
find pal tests -type f -name '*.py' -exec sed -i 's|import pal\.wisdom|import agent_core.wisdom|g' {} +
find pal tests -type f -name '*.py' -exec sed -i 's|"pal\.wisdom|"agent_core.wisdom|g' {} +
find pal tests -type f -name '*.py' -exec sed -i "s|'pal\.wisdom|'agent_core.wisdom|g" {} +
```

- [ ] **Step 3: Update the `daemon.py` construction site**

Use the Edit tool:

Old:
```python
self.wisdom = WisdomManager(config.vault_path)
```

New:
```python
self.wisdom = WisdomManager(config.vault_path, "pal")
```

- [ ] **Step 4: Update test construction sites**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
grep -nE "WisdomManager\(" pal/ tests/
```

For each construction in `tests/` that takes only `vault_path`, insert `"test-agent"` (or `"pal"` if the test asserts PAL paths) as the second positional arg.

- [ ] **Step 5: Verify zero remaining refs**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
grep -rnE "pal\.wisdom" pal/ tests/
```

Expected: zero matches.

- [ ] **Step 6: Delete PAL's local module and test**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
rm pal/wisdom.py tests/test_wisdom.py
```

- [ ] **Step 7: Verify imports**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
.venv/bin/python -c "
from pal.daemon import Daemon
from pal.prompt_builder import SystemPromptBuilder
from pal.tools import ToolExecutor
from agent_core.wisdom import WisdomManager
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 8: Run targeted tests**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
.venv/bin/pytest tests/test_learning_commands.py tests/test_prompt_builder.py tests/test_prompt_builder_commands.py tests/test_tools_propose_promote.py 2>&1 | tail -5
```

Expected: all pass. If a test failed due to a missed WisdomManager construction site, locate via the grep in Step 4 and fix.

- [ ] **Step 9: Commit**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
git add -u pal tests
git status
git commit -m "$(cat <<'EOF'
refactor: migrate wisdom usage to agent_core

Three callers (daemon, prompt_builder, tools) now import from
agent_core.wisdom; daemon's construction site passes agent_name="pal".
PAL's copy of the module and its test deleted. Test construction
sites in the related test files updated to pass "test-agent" or
"pal" as appropriate.
EOF
)"
```

---

## Task 12: Migrate PAL's `learning` usage to agent_core (also rewrites `learning_scanner.py` import)

**Files modified:** `pal/daemon.py` (import + construction at line 147), `pal/tools.py` (import only), `pal/learning_scanner.py` (import only; the scanner stays in PAL)
**Files deleted:** `pal/learning.py`, `tests/test_learning.py`
**Test files with imports rewritten (stay in PAL):** `tests/test_learning_commands.py`, `tests/test_learning_scanner.py`, `tests/test_learning_scanner_orchestrator.py`, `tests/test_learning_scanner_extract.py`, `tests/test_daemon_scanner_hook.py`, `tests/test_daemon_scanner_approval.py`, `tests/test_scanner_take_pending.py`, `tests/test_tools_add_learning.py`, `tests/test_learning_e2e.py` (flaky-skipped)

- [ ] **Step 1: Pre-flight broad grep**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
grep -rnE "pal\.learning[^_]|\"pal\.learning[^_]|'pal\.learning[^_]" pal/ tests/
```

The `[^_]` excludes `pal.learning_scanner` matches. Note the count for `pal.learning` (the module being migrated).

Then separately confirm the scanner's local references:

```bash
grep -rnE "pal\.learning_scanner" pal/ tests/ | head
```

These do NOT get rewritten in this task; learning_scanner stays in PAL.

- [ ] **Step 2: Bulk rewrite `pal.learning` imports**

The `[^_]` boundary in sed is awkward; use word-boundary matching to avoid hitting `pal.learning_scanner`. The cleanest path is to enumerate the patterns explicitly:

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
find pal tests -type f -name '*.py' -exec sed -i 's|from pal\.learning import|from agent_core.learning import|g' {} +
find pal tests -type f -name '*.py' -exec sed -i 's|import pal\.learning$|import agent_core.learning|g' {} +
```

The first sed rewrites `from pal.learning import LearningManager` to `from agent_core.learning import LearningManager` without touching `from pal.learning_scanner import ...`. The second is anchored to end-of-line for bare `import pal.learning` statements.

If string-form references exist (verify with grep), add patterns:

```bash
find pal tests -type f -name '*.py' -exec sed -i 's|"pal\.learning\b|"agent_core.learning|g' {} +
find pal tests -type f -name '*.py' -exec sed -i "s|'pal\.learning\b|'agent_core.learning|g" {} +
```

The `\b` word boundary ensures `pal.learning_scanner` is not matched.

- [ ] **Step 3: Verify the rewrite was clean**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
grep -rnE "pal\.learning[^_]" pal/ tests/
```

Expected: zero matches. Then verify learning_scanner refs are still intact:

```bash
grep -rnE "pal\.learning_scanner" pal/ tests/ | wc -l
```

Expected: matches still present (these stay).

- [ ] **Step 4: Update the `daemon.py` construction site**

Use the Edit tool:

Old:
```python
self.learning = LearningManager(config.vault_path)
```

New:
```python
self.learning = LearningManager(config.vault_path, "pal")
```

- [ ] **Step 5: Update test construction sites**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
grep -nE "LearningManager\(" pal/ tests/
```

For each construction in `tests/` that takes only `vault_path`, insert `"test-agent"` (or `"pal"` if the test asserts PAL paths) as the second positional arg.

- [ ] **Step 6: Delete PAL's local module and test**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
rm pal/learning.py tests/test_learning.py
```

- [ ] **Step 7: Verify imports including the scanner**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
.venv/bin/python -c "
from pal.daemon import Daemon
from pal.tools import ToolExecutor
from pal.learning_scanner import LearningScanner, extract_candidate, has_signal
from agent_core.learning import LearningManager
print('OK')
"
```

Expected: `OK`. The scanner still works because `LearningManager`'s public API is unchanged.

- [ ] **Step 8: Run targeted tests**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
.venv/bin/pytest tests/test_learning_commands.py tests/test_learning_scanner.py tests/test_learning_scanner_orchestrator.py tests/test_learning_scanner_extract.py tests/test_daemon_scanner_hook.py tests/test_daemon_scanner_approval.py tests/test_scanner_take_pending.py tests/test_tools_add_learning.py 2>&1 | tail -5
```

Expected: all pass. The scanner-related tests in particular verify that PAL's scanner integrates correctly with the agent_core LearningManager.

- [ ] **Step 9: Commit**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
git add -u pal tests
git status
git commit -m "$(cat <<'EOF'
refactor: migrate learning usage to agent_core

Callers (daemon, tools, learning_scanner) now import LearningManager
from agent_core.learning. daemon.py's construction site passes
agent_name="pal". PAL's copy of the module and its dedicated test
deleted. Scanner stays in PAL with only its import line rewritten;
its full migration is deferred to Phase D or E together with the
protocol module.
EOF
)"
```

---

## Task 13: Final smoke + clean install + open PR

**Files:** No file modifications. Verification, server-migration runbook, branch push, PR creation.

- [ ] **Step 1: Final import probe from PAL's worktree env**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
.venv/bin/python -c "
from agent_core.utils.frontmatter import parse_frontmatter
from agent_core.utils.fetcher import URLFetcher
from agent_core.reasoning import shape_request
from agent_core.inference import InferenceClient
from agent_core.retrieval import RetrievalClient
from agent_core.websearch import WebSearchClient
from agent_core.approval_registry import ApprovalRegistry
from agent_core.profile import ProfileManager
from agent_core.allowlist import AllowlistManager
from agent_core.wisdom import WisdomManager
from agent_core.learning import LearningManager
from pal.daemon import Daemon
from pal.learning_scanner import LearningScanner
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 2: Confirm zero residual `pal.<migrated_module>` references**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
grep -rnE "pal\.approval_registry|pal\.profile|pal\.allowlist|pal\.wisdom|pal\.learning[^_]" pal/ tests/
```

Expected: zero matches.

- [ ] **Step 3: Run the broad PAL test suite**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
timeout 240 .venv/bin/pytest \
    --ignore=tests/test_daemon.py \
    --ignore=tests/test_integration.py \
    --ignore=tests/test_chat_research_integration.py \
    --ignore=tests/test_consolidate_integration.py \
    --ignore=tests/test_learning_e2e.py \
    -q 2>&1 | tail -20
```

Expected: all pass. Phase B baseline was 714; Phase C deletes 5 module-specific test files and adds none, so expect roughly 670-700 passing.

If anything fails, investigate. Most likely causes:
1. A construction site missed in Tasks 9, 11, or 12 (grep `ProfileManager\(`, `WisdomManager\(`, `LearningManager\(` and verify each has `agent_name`).
2. A string-form reference the sed missed (re-grep with broader pattern).

- [ ] **Step 4: Daemon startup smoke (with empty namespaced dirs as expected)**

The daemon will start; the namespaced directories at `~/vault/_wisdom/pal/` etc. don't exist locally (the dev machine's vault may be different from the server's) so the managers will create them on first write.

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c

.venv/bin/pal-daemon > /tmp/pal_daemon_phase_c_smoke.log 2>&1 &
DAEMON_PID=$!
sleep 3

if ! kill -0 $DAEMON_PID 2>/dev/null; then
    echo "FAIL: daemon died on startup"
    cat /tmp/pal_daemon_phase_c_smoke.log
    exit 1
fi

cat /tmp/pal_daemon_phase_c_smoke.log

SOCKET="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/pal.sock"
ls -la "$SOCKET" 2>&1

echo "/help" | timeout 5 .venv/bin/pal 2>&1 | head -30 || true

kill $DAEMON_PID 2>/dev/null
wait $DAEMON_PID 2>/dev/null
echo "Daemon stopped"
```

Required outcome: daemon log shows `Daemon listening on /run/user/<uid>/pal.sock` with no Python tracebacks.

- [ ] **Step 5: Clean install probe**

```bash
mkdir -p /tmp/pal_phase_c_install_test && cd /tmp/pal_phase_c_install_test
python3 -m venv .venv && source .venv/bin/activate
pip install --quiet -e /home/edible/Projects/PAL/.worktrees/agent-core-phase-c 2>&1 | tail -3
python -c "
from agent_core.approval_registry import ApprovalRegistry
from agent_core.profile import ProfileManager
from agent_core.allowlist import AllowlistManager
from agent_core.wisdom import WisdomManager
from agent_core.learning import LearningManager
from pal.daemon import Daemon
from pal.learning_scanner import LearningScanner
print('OK')
"
deactivate
rm -rf /tmp/pal_phase_c_install_test
```

Expected: `OK`.

- [ ] **Step 6: Push the branch**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
git push -u origin feature/agent-core-extraction-phase-c
```

- [ ] **Step 7: Open the PR**

```bash
cd /home/edible/Projects/PAL/.worktrees/agent-core-phase-c
gh pr create --title "Phase C: extract approval_registry, profile, allowlist, wisdom, learning into agent_core" --body "$(cat <<'EOF'
## Summary

Phase C of the agent_core extraction (see `docs/superpowers/specs/2026-04-27-phase-c-stateful-managers-design.md`). This branch:

- Bumps `agent_core` pin from `v0.2.0` to `v0.3.0`
- Migrates five stateful-manager modules out of PAL into `agent_core`: `approval_registry`, `profile`, `allowlist`, `wisdom`, `learning`
- Adds `agent_name` parameterization to the four vault-rooted managers (profile, allowlist, wisdom, learning) so storage namespaces under `<vault>/_<thing>/<agent_name>/`
- `learning_scanner` stays in PAL for now; only its import of `LearningManager` is rewritten. Full scanner migration deferred to Phase D or E (it imports `pal.protocol`, which moves with the daemon).

After merge, PAL ships using all 14 of agent_core's modules.

## Commits

- chore: bump agent_core dependency to v0.3.0
- refactor: migrate approval_registry usage to agent_core
- refactor: migrate profile usage to agent_core
- refactor: migrate allowlist usage to agent_core
- refactor: migrate wisdom usage to agent_core
- refactor: migrate learning usage to agent_core

## Server-side data migration (REQUIRED before restarting pal-daemon)

After merging and pulling on the server, BEFORE starting pal-daemon, run:

```bash
cd /mnt/secondary/agent-workspace/vault
mkdir -p _wisdom/pal _learning/pal _profile/pal _config/pal
shopt -s nullglob
mv _wisdom/*.md _wisdom/pal/    2>/dev/null || true
mv _learning/*.md _learning/pal/ 2>/dev/null || true
mv _profile/*.md _profile/pal/   2>/dev/null || true
mv _config/allowlist.md _config/pal/allowlist.md  2>/dev/null || true
```

Then start the daemon:

```bash
cd /mnt/secondary/PAL
git checkout main
git pull
.venv/bin/pip install -e ".[dev]"
systemctl --user start pal-daemon
```

If the daemon starts before the migration runs, it sees empty `_wisdom/pal/` and `_learning/pal/` directories. Not destructive but visibly empty until the `mv` completes.

## Test plan

- [x] agent_core's own pytest suite passes on the v0.3.0 tag (CI run on agent_core repo)
- [x] PAL targeted tests for the five migrated modules pass before and after the dep bump
- [x] PAL broad suite passes (excluding the 5 known-flaky integration files identified during Phase A)
- [x] PAL daemon starts cleanly with the new agent_core dep and empty namespaced dirs
- [x] Clean install probe in a fresh venv: all 14 agent_core modules + PAL daemon + PAL learning_scanner import
- [x] Two new agent_core tests verify multi-agent isolation for wisdom and learning
- [ ] Manual smoke against the inference server: chat turn, /wisdom list, /wisdom add, /profile, /learnings, signal-phrase chat for learning_scanner (couldn't be automated)

## Notes

- agent_core's pyproject `version` field bumped to `0.3.0` matching the v0.3.0 tag.
- `learning_scanner.py` stays in PAL. It now imports `LearningManager` from `agent_core.learning` but otherwise unchanged. Its full migration is deferred to a later phase together with the protocol module.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL printed. Capture and report it.

---

## Phase C complete

At the end of Phase C:
- agent_core has 14 modules: 5 utilities + 4 stateless clients (Phase B) + 5 stateful managers (Phase C)
- agent_core@v0.3.0 is tagged, CI green, fresh-install verified
- PAL ships on agent_core@v0.3.0; the four vault-rooted managers namespace storage by `agent_name="pal"`
- PAL has 5 fewer files in `pal/` and 5 fewer dedicated test files
- `learning_scanner.py` stays in PAL with one import line rewritten; its full migration is parked for Phase D or E

Next phase plan (Phase D: per-channel state, `channels`, `scratchpad`, `conversation`) gets written when this phase lands. Phase D also gets the parked `learning_scanner` migration plus the introduction of `agent_core.LearningCandidate` if that's the chosen decoupling path, or whatever decision flows from the Phase D protocol scoping.
