# agent_core Extraction Phase A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bootstrap the `agent_core` repo, migrate the five leaf utilities (`frontmatter`, `chunker`, `sanitizer`, `converter`, `fetcher`) out of PAL into `agent_core`, and update PAL to consume `agent_core@v0.1.0`. At the end, PAL ships on the new dependency with all tests passing.

**Architecture:** Two repositories. `agent_core` is a brand-new private library at `https://github.com/EdibleTuber/agent_core` (currently empty). PAL adds `agent_core` as a git-pinned pip dependency. The five migrated utilities are pure leaves with no internal PAL imports, so no refactoring is required to move them; only mechanical relocation and import updates.

**Tech Stack:** Python 3.12+, hatchling build backend, pytest, GitHub Actions CI, git tags for versioning. Runtime deps in `agent_core` for Phase A: `httpx`, `pyyaml`, `prompt-toolkit`, `rich`, `trafilatura`, `markitdown[pdf,docx,pptx,xlsx]`.

**Repos involved:**
- `agent_core` working directory: `/home/edible/Projects/agent_core` (cloned in Task 1)
- `PAL` working directory: `/home/edible/Projects/PAL` (existing)

**Reference:** spec at `docs/superpowers/specs/2026-04-25-agent-core-extraction-design.md`. This plan covers Step 0 (pre-flight) and Step 1 (leaf utilities) of the spec's nine-step migration sequence. Subsequent phases get their own plans.

---

## Task 1: Bootstrap agent_core repository

**Files:**
- Create: `/home/edible/Projects/agent_core/pyproject.toml`
- Create: `/home/edible/Projects/agent_core/agent_core/__init__.py`
- Create: `/home/edible/Projects/agent_core/agent_core/utils/__init__.py`
- Create: `/home/edible/Projects/agent_core/tests/__init__.py`
- Create: `/home/edible/Projects/agent_core/tests/test_smoke.py`
- Create: `/home/edible/Projects/agent_core/.github/workflows/test.yml`
- Create: `/home/edible/Projects/agent_core/.gitignore`
- Create: `/home/edible/Projects/agent_core/README.md`

- [ ] **Step 1: Clone the empty agent_core repo**

```bash
cd /home/edible/Projects && git clone https://github.com/EdibleTuber/agent_core.git
cd /home/edible/Projects/agent_core
```

Expected: clone succeeds, directory contains only `.git/`. If the clone reports "warning: You appear to have cloned an empty repository," that is fine.

- [ ] **Step 2: Create `pyproject.toml`**

Write `/home/edible/Projects/agent_core/pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "agent_core"
version = "0.0.0"
description = "Shared library for the agent ecosystem"
requires-python = ">=3.12"
dependencies = [
    "httpx>=0.27.0",
    "pyyaml>=6.0",
    "prompt-toolkit>=3.0.0",
    "rich>=13.0.0",
    "trafilatura>=1.12.0",
    "markitdown[pdf,docx,pptx,xlsx]>=0.1.0",
]

[project.optional-dependencies]
discord = [
    "discord.py>=2.0",
]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 3: Create empty package layout**

Write `/home/edible/Projects/agent_core/agent_core/__init__.py` (empty file, single blank line).

Write `/home/edible/Projects/agent_core/agent_core/utils/__init__.py` (empty file, single blank line).

Write `/home/edible/Projects/agent_core/tests/__init__.py` (empty file, single blank line).

- [ ] **Step 4: Write the smoke test**

Write `/home/edible/Projects/agent_core/tests/test_smoke.py`:

```python
"""Smoke test: agent_core package imports cleanly."""


def test_package_imports():
    import agent_core
    assert agent_core is not None


def test_utils_subpackage_imports():
    from agent_core import utils
    assert utils is not None
```

- [ ] **Step 5: Write the CI workflow**

Write `/home/edible/Projects/agent_core/.github/workflows/test.yml`:

```yaml
name: tests

on:
  push:
    branches: [main]
  pull_request:

jobs:
  pytest:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"
      - name: Run tests
        run: pytest -v
```

- [ ] **Step 6: Write `.gitignore`**

Write `/home/edible/Projects/agent_core/.gitignore`:

```
__pycache__/
*.py[cod]
*$py.class
*.egg-info/
.eggs/
.pytest_cache/
.venv/
venv/
build/
dist/
.coverage
.tox/
.mypy_cache/
.ruff_cache/
```

- [ ] **Step 7: Write `README.md`**

Write `/home/edible/Projects/agent_core/README.md`:

```markdown
# agent_core

Shared library for the agent ecosystem. Provides daemon runtime, socket protocol, inference client, retrieval, wisdom, learning, channels, scratchpad, fetcher/chunker/converter, CLI REPL, and an opt-in Discord gateway adapter.

## Status

Under active extraction from PAL. See `docs/agent-core-extraction-design.md` (in PAL repo) for the design.

## Installation

Private repo, install via git:

```bash
pip install "agent_core @ git+https://github.com/EdibleTuber/agent_core.git@v0.1.0"
```

For development against a local checkout:

```bash
pip install -e /path/to/agent_core
```

## Tests

```bash
pip install -e ".[dev]"
pytest
```
```

- [ ] **Step 8: Set up a venv and install editable**

```bash
cd /home/edible/Projects/agent_core
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
```

Expected: install succeeds, `pip show agent_core` reports `Version: 0.0.0`.

- [ ] **Step 9: Run the smoke test**

```bash
cd /home/edible/Projects/agent_core
source .venv/bin/activate
pytest -v
```

Expected: 2 tests pass (`test_package_imports`, `test_utils_subpackage_imports`).

- [ ] **Step 10: Initial commit, tag v0.0.0, push**

```bash
cd /home/edible/Projects/agent_core
git add pyproject.toml agent_core/ tests/ .github/ .gitignore README.md
git status
```

Verify the staged file list matches the files created in Steps 2-7. Then commit and tag:

```bash
git commit -m "$(cat <<'EOF'
chore: bootstrap agent_core package

Empty package skeleton with pyproject, smoke test, and CI workflow.
First tagged version. No code migrated yet.
EOF
)"
git tag v0.0.0
git push origin main
git push origin v0.0.0
```

Expected: push succeeds, `v0.0.0` tag visible at https://github.com/EdibleTuber/agent_core/tags.

---

## Task 2: Move `frontmatter` into agent_core

**Files:**
- Create: `/home/edible/Projects/agent_core/agent_core/utils/frontmatter.py`
- Create: `/home/edible/Projects/agent_core/tests/test_frontmatter.py`

- [ ] **Step 1: Copy the source module**

```bash
cp /home/edible/Projects/PAL/pal/frontmatter.py /home/edible/Projects/agent_core/agent_core/utils/frontmatter.py
```

Expected: file copied, no modifications needed (the module has no internal `pal` imports, verified during planning).

- [ ] **Step 2: Copy the test file**

```bash
cp /home/edible/Projects/PAL/tests/test_frontmatter.py /home/edible/Projects/agent_core/tests/test_frontmatter.py
```

- [ ] **Step 3: Update test imports**

Use the Edit tool on `/home/edible/Projects/agent_core/tests/test_frontmatter.py`:

Old:
```python
from pal.frontmatter import parse_frontmatter, serialize_frontmatter
```

New:
```python
from agent_core.utils.frontmatter import parse_frontmatter, serialize_frontmatter
```

If the test file has additional `from pal.frontmatter` imports, replace each one similarly.

- [ ] **Step 4: Run the test**

```bash
cd /home/edible/Projects/agent_core
source .venv/bin/activate
pytest tests/test_frontmatter.py -v
```

Expected: all `test_frontmatter.py` tests pass. Smoke tests from Task 1 still pass when running full `pytest`.

- [ ] **Step 5: Commit on agent_core**

```bash
cd /home/edible/Projects/agent_core
git add agent_core/utils/frontmatter.py tests/test_frontmatter.py
git commit -m "$(cat <<'EOF'
feat: add frontmatter utility

Migrated from PAL. Pure leaf module, no behavior change.
EOF
)"
```

---

## Task 3: Move `chunker` into agent_core

**Files:**
- Create: `/home/edible/Projects/agent_core/agent_core/utils/chunker.py`
- Create: `/home/edible/Projects/agent_core/tests/test_chunker.py`

- [ ] **Step 1: Copy the source module**

```bash
cp /home/edible/Projects/PAL/pal/chunker.py /home/edible/Projects/agent_core/agent_core/utils/chunker.py
```

- [ ] **Step 2: Copy the test file**

```bash
cp /home/edible/Projects/PAL/tests/test_chunker.py /home/edible/Projects/agent_core/tests/test_chunker.py
```

- [ ] **Step 3: Update test imports**

Use the Edit tool on `/home/edible/Projects/agent_core/tests/test_chunker.py`:

Old:
```python
from pal.chunker import chunk_markdown, Chunk
```

New:
```python
from agent_core.utils.chunker import chunk_markdown, Chunk
```

- [ ] **Step 4: Run the test**

```bash
cd /home/edible/Projects/agent_core
source .venv/bin/activate
pytest tests/test_chunker.py -v
```

Expected: all `test_chunker.py` tests pass.

- [ ] **Step 5: Commit on agent_core**

```bash
cd /home/edible/Projects/agent_core
git add agent_core/utils/chunker.py tests/test_chunker.py
git commit -m "$(cat <<'EOF'
feat: add chunker utility

Migrated from PAL. Pure leaf module, no behavior change.
EOF
)"
```

---

## Task 4: Move `sanitizer` into agent_core

**Files:**
- Create: `/home/edible/Projects/agent_core/agent_core/utils/sanitizer.py`
- Create: `/home/edible/Projects/agent_core/tests/test_sanitizer.py`

- [ ] **Step 1: Copy the source module**

```bash
cp /home/edible/Projects/PAL/pal/sanitizer.py /home/edible/Projects/agent_core/agent_core/utils/sanitizer.py
```

- [ ] **Step 2: Copy the test file**

```bash
cp /home/edible/Projects/PAL/tests/test_sanitizer.py /home/edible/Projects/agent_core/tests/test_sanitizer.py
```

- [ ] **Step 3: Update test imports**

Use the Edit tool on `/home/edible/Projects/agent_core/tests/test_sanitizer.py`:

Old:
```python
from pal.sanitizer import sanitize, SanitizationResult
```

New:
```python
from agent_core.utils.sanitizer import sanitize, SanitizationResult
```

- [ ] **Step 4: Run the test**

```bash
cd /home/edible/Projects/agent_core
source .venv/bin/activate
pytest tests/test_sanitizer.py -v
```

Expected: all `test_sanitizer.py` tests pass.

- [ ] **Step 5: Commit on agent_core**

```bash
cd /home/edible/Projects/agent_core
git add agent_core/utils/sanitizer.py tests/test_sanitizer.py
git commit -m "$(cat <<'EOF'
feat: add sanitizer utility

Migrated from PAL. Pure leaf module, no behavior change.
EOF
)"
```

---

## Task 5: Move `converter` into agent_core

**Files:**
- Create: `/home/edible/Projects/agent_core/agent_core/utils/converter.py`
- Create: `/home/edible/Projects/agent_core/tests/test_converter.py`

- [ ] **Step 1: Copy the source module**

```bash
cp /home/edible/Projects/PAL/pal/converter.py /home/edible/Projects/agent_core/agent_core/utils/converter.py
```

- [ ] **Step 2: Copy the test file**

```bash
cp /home/edible/Projects/PAL/tests/test_converter.py /home/edible/Projects/agent_core/tests/test_converter.py
```

- [ ] **Step 3: Update test imports**

Use the Edit tool on `/home/edible/Projects/agent_core/tests/test_converter.py`:

Replace top-of-file import:

Old:
```python
from pal.converter import DocumentConverter, ConvertResult, ConversionError
```

New:
```python
from agent_core.utils.converter import DocumentConverter, ConvertResult, ConversionError
```

The test file also has a local import inside a function (line ~56 in PAL):

Old:
```python
        from pal.converter import SUPPORTED_EXTENSIONS
```

New:
```python
        from agent_core.utils.converter import SUPPORTED_EXTENSIONS
```

- [ ] **Step 4: Run the test**

```bash
cd /home/edible/Projects/agent_core
source .venv/bin/activate
pytest tests/test_converter.py -v
```

Expected: all `test_converter.py` tests pass. (The `markitdown` dep was already declared in Task 1's pyproject.)

- [ ] **Step 5: Commit on agent_core**

```bash
cd /home/edible/Projects/agent_core
git add agent_core/utils/converter.py tests/test_converter.py
git commit -m "$(cat <<'EOF'
feat: add converter utility

Migrated from PAL. Wraps markitdown for document-to-markdown conversion.
No behavior change.
EOF
)"
```

---

## Task 6: Move `fetcher` into agent_core

**Files:**
- Create: `/home/edible/Projects/agent_core/agent_core/utils/fetcher.py`
- Create: `/home/edible/Projects/agent_core/tests/test_fetcher.py`

- [ ] **Step 1: Copy the source module**

```bash
cp /home/edible/Projects/PAL/pal/fetcher.py /home/edible/Projects/agent_core/agent_core/utils/fetcher.py
```

- [ ] **Step 2: Copy the test file**

```bash
cp /home/edible/Projects/PAL/tests/test_fetcher.py /home/edible/Projects/agent_core/tests/test_fetcher.py
```

- [ ] **Step 3: Update test imports**

Use the Edit tool on `/home/edible/Projects/agent_core/tests/test_fetcher.py`:

Old:
```python
from pal.fetcher import URLFetcher, FetchResult, FetchError
```

New:
```python
from agent_core.utils.fetcher import URLFetcher, FetchResult, FetchError
```

If any other `from pal.fetcher` imports exist in the file, replace them similarly.

- [ ] **Step 4: Run the test**

```bash
cd /home/edible/Projects/agent_core
source .venv/bin/activate
pytest tests/test_fetcher.py -v
```

Expected: all `test_fetcher.py` tests pass. (The `trafilatura` dep was already declared in Task 1's pyproject.)

- [ ] **Step 5: Commit on agent_core**

```bash
cd /home/edible/Projects/agent_core
git add agent_core/utils/fetcher.py tests/test_fetcher.py
git commit -m "$(cat <<'EOF'
feat: add fetcher utility

Migrated from PAL. URL fetcher with safety checks (private-IP refusal, size
caps, content-type filtering). No behavior change.
EOF
)"
```

---

## Task 7: Tag agent_core v0.1.0

**Files:**
- No file changes; tag and push only.

- [ ] **Step 1: Run the full agent_core test suite**

```bash
cd /home/edible/Projects/agent_core
source .venv/bin/activate
pytest -v
```

Expected: smoke tests + frontmatter + chunker + sanitizer + converter + fetcher tests all pass. No failures, no errors.

- [ ] **Step 2: Tag and push**

```bash
cd /home/edible/Projects/agent_core
git tag v0.1.0
git push origin main
git push origin v0.1.0
```

Expected: tag visible at https://github.com/EdibleTuber/agent_core/tags. Commits from Tasks 2-6 visible on `main`.

- [ ] **Step 3: Verify the tag is installable**

In a scratch directory, in a fresh venv, install agent_core from the tag and run an import check:

```bash
mkdir -p /tmp/agent_core_install_test && cd /tmp/agent_core_install_test
python3 -m venv .venv && source .venv/bin/activate
pip install "agent_core @ git+https://github.com/EdibleTuber/agent_core.git@v0.1.0"
python -c "from agent_core.utils.frontmatter import parse_frontmatter; from agent_core.utils.chunker import chunk_markdown; from agent_core.utils.sanitizer import sanitize; from agent_core.utils.converter import DocumentConverter; from agent_core.utils.fetcher import URLFetcher; print('OK')"
```

Expected: `OK` printed, no ImportError.

Clean up:
```bash
deactivate
rm -rf /tmp/agent_core_install_test
```

---

## Task 8: Add agent_core dependency to PAL

**Files:**
- Modify: `/home/edible/Projects/PAL/pyproject.toml`

- [ ] **Step 1: Add agent_core to PAL's dependencies**

Use the Edit tool on `/home/edible/Projects/PAL/pyproject.toml`:

Old:
```toml
dependencies = [
    "httpx>=0.27.0",
    "prompt-toolkit>=3.0.0",
    "rich>=13.0.0",
    "pyyaml>=6.0",
    "trafilatura>=1.12.0",
    "markitdown[pdf,docx,pptx,xlsx]>=0.1.0",
    "pymupdf4llm>=0.0.17",
]
```

New:
```toml
dependencies = [
    "agent_core @ git+https://github.com/EdibleTuber/agent_core.git@v0.1.0",
    "httpx>=0.27.0",
    "prompt-toolkit>=3.0.0",
    "rich>=13.0.0",
    "pyyaml>=6.0",
    "trafilatura>=1.12.0",
    "markitdown[pdf,docx,pptx,xlsx]>=0.1.0",
    "pymupdf4llm>=0.0.17",
]
```

(The redundant deps `httpx`, `pyyaml`, `trafilatura`, `markitdown` are kept for now; they will be removed in Task 14 once all imports have been migrated. PAL's other modules still need them directly until later phases.)

- [ ] **Step 2: Reinstall PAL's editable env**

```bash
cd /home/edible/Projects/PAL
source .venv/bin/activate
pip install -e ".[dev]"
```

Expected: pip resolves and installs `agent_core` from the v0.1.0 git tag. `pip show agent_core` reports `Version: 0.1.0`.

- [ ] **Step 3: Verify agent_core is importable from PAL's env**

```bash
cd /home/edible/Projects/PAL
source .venv/bin/activate
python -c "from agent_core.utils.frontmatter import parse_frontmatter; print('OK')"
```

Expected: `OK` printed.

- [ ] **Step 4: Run PAL's full test suite as a baseline**

```bash
cd /home/edible/Projects/PAL
source .venv/bin/activate
pytest -v
```

Expected: all PAL tests pass. PAL has not yet migrated any of its imports, so it is still using `pal.frontmatter` etc.; this run confirms the new dep did not break anything.

- [ ] **Step 5: Commit on PAL**

```bash
cd /home/edible/Projects/PAL
git add pyproject.toml
git commit -m "$(cat <<'EOF'
chore: add agent_core@v0.1.0 dependency

Adds agent_core as a git-pinned dependency. PAL's own copies of the migrated
modules are still in use; subsequent commits switch each one over.
EOF
)"
```

---

## Task 9: Migrate PAL's `frontmatter` usage to agent_core

**Files modified (12 source files + 5 test files):**
- Modify: `pal/compiler.py`, `pal/article.py`, `pal/wisdom.py`, `pal/profile.py`, `pal/summarizer.py`, `pal/wiki.py`, `pal/learning.py`, `pal/daemon.py`, `pal/consolidator.py`, `pal/reorg.py`, `pal/backfill_titles.py`, `pal/researcher.py`
- Modify: `tests/test_daemon.py`, `tests/test_compile.py`, `tests/test_summarize.py`, `tests/test_summarizer.py`, `tests/test_backfill_titles.py`
- Delete: `pal/frontmatter.py`
- Delete: `tests/test_frontmatter.py`

- [ ] **Step 1: Confirm the full caller list**

Use the Grep tool with pattern `from pal\.frontmatter` across `/home/edible/Projects/PAL/pal/` and `/home/edible/Projects/PAL/tests/`.

Expected matches (verified during planning):
- `pal/compiler.py:20`
- `pal/article.py:15`
- `pal/wisdom.py:14`
- `pal/profile.py:12`
- `pal/summarizer.py:12`
- `pal/wiki.py:13`
- `pal/learning.py:12`
- `pal/daemon.py:29`, `daemon.py:1017`, `daemon.py:1303`, `daemon.py:1641`
- `pal/consolidator.py:18`
- `pal/reorg.py:318`
- `pal/backfill_titles.py:14`
- `pal/researcher.py:17`
- `tests/test_daemon.py:224`, `test_daemon.py:272`
- `tests/test_compile.py:44`, `test_compile.py:276`
- `tests/test_summarize.py:40`
- `tests/test_summarizer.py:67`, `test_summarizer.py:139`, `test_summarizer.py:159`
- `tests/test_backfill_titles.py:64`, `test_backfill_titles.py:91`, `test_backfill_titles.py:274`
- `tests/test_frontmatter.py` (this whole file is deleted, not migrated)

If the actual matches differ from the list above, adjust scope accordingly before proceeding.

- [ ] **Step 2: Bulk-rewrite imports**

Run a single sed across PAL's source and tests:

```bash
cd /home/edible/Projects/PAL
find pal tests -type f -name '*.py' -exec sed -i 's|from pal\.frontmatter|from agent_core.utils.frontmatter|g' {} +
find pal tests -type f -name '*.py' -exec sed -i 's|import pal\.frontmatter|import agent_core.utils.frontmatter|g' {} +
```

Verify with the Grep tool: searching `from pal\.frontmatter` should now return zero matches across `pal/` and `tests/`.

- [ ] **Step 3: Delete the old module and its old test file**

```bash
cd /home/edible/Projects/PAL
rm pal/frontmatter.py tests/test_frontmatter.py
```

- [ ] **Step 4: Run PAL's tests**

```bash
cd /home/edible/Projects/PAL
source .venv/bin/activate
pytest -v
```

Expected: all PAL tests pass. Notably the tests that previously imported `from pal.frontmatter` now succeed via `from agent_core.utils.frontmatter`.

If any test fails: check whether the failure is an import error (sed missed something; re-run Step 2 with adjusted patterns) or a behavior change (should not happen; investigate).

- [ ] **Step 5: Commit on PAL**

```bash
cd /home/edible/Projects/PAL
git add -u pal tests pyproject.toml
git status
```

Verify the staged diff: `pal/frontmatter.py` and `tests/test_frontmatter.py` are deleted; 12 source files and 5 test files have import updates. Then:

```bash
git commit -m "$(cat <<'EOF'
refactor: migrate frontmatter usage to agent_core

All callers now import from agent_core.utils.frontmatter. Deletes PAL's
copy of the module and its dedicated test file (test_frontmatter.py lives
in agent_core now).
EOF
)"
```

---

## Task 10: Migrate PAL's `chunker` usage to agent_core

**Files modified:**
- Modify: `pal/daemon.py` (one import)
- Delete: `pal/chunker.py`
- Delete: `tests/test_chunker.py`

- [ ] **Step 1: Confirm the caller list**

Use the Grep tool with pattern `from pal\.chunker` across `/home/edible/Projects/PAL/pal/` and `/home/edible/Projects/PAL/tests/`.

Expected matches:
- `pal/daemon.py:35`
- `tests/test_chunker.py` (this whole file is deleted)

If the actual matches differ, adjust scope.

- [ ] **Step 2: Bulk-rewrite imports**

```bash
cd /home/edible/Projects/PAL
find pal tests -type f -name '*.py' -exec sed -i 's|from pal\.chunker|from agent_core.utils.chunker|g' {} +
find pal tests -type f -name '*.py' -exec sed -i 's|import pal\.chunker|import agent_core.utils.chunker|g' {} +
```

Verify with Grep: `from pal\.chunker` should return zero matches across `pal/` and `tests/`.

- [ ] **Step 3: Delete the old module and test**

```bash
cd /home/edible/Projects/PAL
rm pal/chunker.py tests/test_chunker.py
```

- [ ] **Step 4: Run PAL's tests**

```bash
cd /home/edible/Projects/PAL
source .venv/bin/activate
pytest -v
```

Expected: all PAL tests pass.

- [ ] **Step 5: Commit on PAL**

```bash
cd /home/edible/Projects/PAL
git add -u pal tests
git commit -m "$(cat <<'EOF'
refactor: migrate chunker usage to agent_core

Single caller (daemon.py) now imports from agent_core.utils.chunker.
PAL's copy of the module and its test deleted.
EOF
)"
```

---

## Task 11: Migrate PAL's `sanitizer` usage to agent_core

**Files modified:**
- Modify: `pal/summarizer.py` (one import)
- Delete: `pal/sanitizer.py`
- Delete: `tests/test_sanitizer.py`

- [ ] **Step 1: Confirm the caller list**

Use the Grep tool with pattern `from pal\.sanitizer` across `/home/edible/Projects/PAL/pal/` and `/home/edible/Projects/PAL/tests/`.

Expected matches:
- `pal/summarizer.py:13`
- `tests/test_sanitizer.py` (deleted)

If actual matches differ, adjust.

- [ ] **Step 2: Bulk-rewrite imports**

```bash
cd /home/edible/Projects/PAL
find pal tests -type f -name '*.py' -exec sed -i 's|from pal\.sanitizer|from agent_core.utils.sanitizer|g' {} +
find pal tests -type f -name '*.py' -exec sed -i 's|import pal\.sanitizer|import agent_core.utils.sanitizer|g' {} +
```

Verify with Grep: `from pal\.sanitizer` should return zero matches.

- [ ] **Step 3: Delete the old module and test**

```bash
cd /home/edible/Projects/PAL
rm pal/sanitizer.py tests/test_sanitizer.py
```

- [ ] **Step 4: Run PAL's tests**

```bash
cd /home/edible/Projects/PAL
source .venv/bin/activate
pytest -v
```

Expected: all PAL tests pass.

- [ ] **Step 5: Commit on PAL**

```bash
cd /home/edible/Projects/PAL
git add -u pal tests
git commit -m "$(cat <<'EOF'
refactor: migrate sanitizer usage to agent_core

Single caller (summarizer.py) now imports from agent_core.utils.sanitizer.
PAL's copy of the module and its test deleted.
EOF
)"
```

---

## Task 12: Migrate PAL's `converter` usage to agent_core

**Files modified:**
- Modify: `pal/daemon.py` (one import)
- Delete: `pal/converter.py`
- Delete: `tests/test_converter.py`

- [ ] **Step 1: Confirm the caller list**

Use the Grep tool with pattern `from pal\.converter` across `/home/edible/Projects/PAL/pal/` and `/home/edible/Projects/PAL/tests/`.

Expected matches:
- `pal/daemon.py:30`
- `tests/test_converter.py` (deleted)

- [ ] **Step 2: Bulk-rewrite imports**

```bash
cd /home/edible/Projects/PAL
find pal tests -type f -name '*.py' -exec sed -i 's|from pal\.converter|from agent_core.utils.converter|g' {} +
find pal tests -type f -name '*.py' -exec sed -i 's|import pal\.converter|import agent_core.utils.converter|g' {} +
```

Verify with Grep: `from pal\.converter` should return zero matches.

- [ ] **Step 3: Delete the old module and test**

```bash
cd /home/edible/Projects/PAL
rm pal/converter.py tests/test_converter.py
```

- [ ] **Step 4: Run PAL's tests**

```bash
cd /home/edible/Projects/PAL
source .venv/bin/activate
pytest -v
```

Expected: all PAL tests pass.

- [ ] **Step 5: Commit on PAL**

```bash
cd /home/edible/Projects/PAL
git add -u pal tests
git commit -m "$(cat <<'EOF'
refactor: migrate converter usage to agent_core

Single caller (daemon.py) now imports from agent_core.utils.converter.
PAL's copy of the module and its test deleted.
EOF
)"
```

---

## Task 13: Migrate PAL's `fetcher` usage to agent_core

**Files modified:**
- Modify: `pal/daemon.py`, `pal/researcher.py` (two source files)
- Modify: `tests/test_researcher.py` (one test file)
- Delete: `pal/fetcher.py`
- Delete: `tests/test_fetcher.py`

- [ ] **Step 1: Confirm the caller list**

Use the Grep tool with pattern `from pal\.fetcher` across `/home/edible/Projects/PAL/pal/` and `/home/edible/Projects/PAL/tests/`.

Expected matches:
- `pal/daemon.py:29`
- `pal/researcher.py:16`
- `tests/test_researcher.py:11`
- `tests/test_fetcher.py` (deleted)

If actual matches differ, adjust.

- [ ] **Step 2: Bulk-rewrite imports**

```bash
cd /home/edible/Projects/PAL
find pal tests -type f -name '*.py' -exec sed -i 's|from pal\.fetcher|from agent_core.utils.fetcher|g' {} +
find pal tests -type f -name '*.py' -exec sed -i 's|import pal\.fetcher|import agent_core.utils.fetcher|g' {} +
```

Verify with Grep: `from pal\.fetcher` should return zero matches.

- [ ] **Step 3: Delete the old module and test**

```bash
cd /home/edible/Projects/PAL
rm pal/fetcher.py tests/test_fetcher.py
```

- [ ] **Step 4: Run PAL's tests**

```bash
cd /home/edible/Projects/PAL
source .venv/bin/activate
pytest -v
```

Expected: all PAL tests pass, including `test_researcher.py` which transitively uses fetcher.

- [ ] **Step 5: Commit on PAL**

```bash
cd /home/edible/Projects/PAL
git add -u pal tests
git commit -m "$(cat <<'EOF'
refactor: migrate fetcher usage to agent_core

daemon.py, researcher.py, and test_researcher.py now import from
agent_core.utils.fetcher. PAL's copy of the module and its test deleted.
EOF
)"
```

---

## Task 14: Drop redundant direct deps + run smoke checklist

**Files modified:**
- Modify: `/home/edible/Projects/PAL/pyproject.toml`

After Tasks 9-13, no PAL source code imports `httpx`, `pyyaml`, `trafilatura`, or `markitdown` directly through code that has been migrated. But other PAL modules (e.g., `pal/inference.py`) still use `httpx` directly, and `pal/wisdom.py`/`pal/profile.py` still use `pyyaml` indirectly through frontmatter (now in agent_core, transitive). Conservatively, only drop deps where every direct PAL caller has been migrated *and* agent_core's transitive coverage matches.

- [ ] **Step 1: Audit each candidate dep against PAL's remaining direct usage**

Use the Grep tool to verify what PAL still uses directly:

- `httpx`: search `import httpx` in `pal/`. Expected: still used by `pal/inference.py`, `pal/retrieval.py`, `pal/websearch.py`, `pal/discord_adapter.py`, `pal/discord_interactions.py`. **Keep as direct dep.**
- `pyyaml`: search `import yaml` in `pal/`. Expected: used by `pal/allowlist.py` and possibly others. **Keep as direct dep.**
- `trafilatura`: search `import trafilatura` in `pal/`. Expected: zero matches after Task 13 (only `fetcher.py` used it, and fetcher is now in agent_core). **Drop from direct deps.**
- `markitdown`: search `from markitdown` and `import markitdown` in `pal/`. Expected: zero matches after Task 12 (only `converter.py` used it). **Drop from direct deps.**

If grep shows unexpected direct usage, do not drop that dep.

- [ ] **Step 2: Update PAL's pyproject.toml**

Use the Edit tool on `/home/edible/Projects/PAL/pyproject.toml`:

Old:
```toml
dependencies = [
    "agent_core @ git+https://github.com/EdibleTuber/agent_core.git@v0.1.0",
    "httpx>=0.27.0",
    "prompt-toolkit>=3.0.0",
    "rich>=13.0.0",
    "pyyaml>=6.0",
    "trafilatura>=1.12.0",
    "markitdown[pdf,docx,pptx,xlsx]>=0.1.0",
    "pymupdf4llm>=0.0.17",
]
```

New:
```toml
dependencies = [
    "agent_core @ git+https://github.com/EdibleTuber/agent_core.git@v0.1.0",
    "httpx>=0.27.0",
    "prompt-toolkit>=3.0.0",
    "rich>=13.0.0",
    "pyyaml>=6.0",
    "pymupdf4llm>=0.0.17",
]
```

(`trafilatura` and `markitdown` removed; `httpx`, `prompt-toolkit`, `rich`, `pyyaml`, `pymupdf4llm` remain because PAL still uses them directly.)

- [ ] **Step 3: Reinstall PAL's editable env**

```bash
cd /home/edible/Projects/PAL
source .venv/bin/activate
pip install -e ".[dev]"
```

Expected: install succeeds. `pip show trafilatura` and `pip show markitdown` should both still find them (now installed transitively via agent_core).

- [ ] **Step 4: Run PAL's full test suite**

```bash
cd /home/edible/Projects/PAL
source .venv/bin/activate
pytest -v
```

Expected: all tests pass.

- [ ] **Step 5: Run the migration smoke checklist**

In one terminal:
```bash
cd /home/edible/Projects/PAL
source .venv/bin/activate
pal-daemon
```

Expected: daemon starts, logs "listening on ..." with the socket path. No import errors.

In a second terminal:
```bash
cd /home/edible/Projects/PAL
source .venv/bin/activate
pal
```

Expected: CLI connects, prompt appears.

In the CLI, run:
1. `/help`: expect command list rendered.
2. Type a short chat (`hello, briefly say hi back`): expect a streamed response from inference. (Requires the inference server at 192.168.1.14 to be reachable.)
3. `/profile`: expect profile content rendered.
4. `/scratch test`: expect "appended to scratchpad" or equivalent confirmation.
5. Quit (`Ctrl+D` or `exit`).

Stop the daemon with `Ctrl+C` in the first terminal.

If any step fails, do not proceed. Investigate and fix before committing.

- [ ] **Step 6: Commit on PAL**

```bash
cd /home/edible/Projects/PAL
git add pyproject.toml
git commit -m "$(cat <<'EOF'
chore: drop trafilatura and markitdown direct deps

After Phase A migration, fetcher and converter live in agent_core, so
trafilatura and markitdown reach PAL transitively. Remove them as direct
deps. httpx, pyyaml, prompt-toolkit, rich, and pymupdf4llm stay direct
because other PAL modules still use them.
EOF
)"
```

- [ ] **Step 7: Final verification, clean install from scratch**

In a scratch directory, simulate a fresh install of PAL:

```bash
mkdir -p /tmp/pal_clean_install_test && cd /tmp/pal_clean_install_test
python3 -m venv .venv && source .venv/bin/activate
pip install -e /home/edible/Projects/PAL
python -c "from agent_core.utils.frontmatter import parse_frontmatter; from agent_core.utils.chunker import chunk_markdown; from agent_core.utils.sanitizer import sanitize; from agent_core.utils.converter import DocumentConverter; from agent_core.utils.fetcher import URLFetcher; from pal.daemon import Daemon; print('OK')"
```

Expected: `OK` printed, no ImportError.

Clean up:
```bash
deactivate
rm -rf /tmp/pal_clean_install_test
```

---

## Phase A complete

At this point:
- `agent_core` repo exists at v0.1.0 with five leaf utilities and tests.
- PAL depends on `agent_core@v0.1.0` and uses it for frontmatter, chunker, sanitizer, converter, fetcher.
- PAL's tests all pass; smoke checklist passed; clean install works.
- PAL is ~600 LOC smaller (the five modules and their tests).

Next phase plan (Phase B: stateless clients `inference`, `retrieval`, `websearch`) gets written when this phase lands.
