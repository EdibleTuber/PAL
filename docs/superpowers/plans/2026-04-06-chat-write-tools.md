# Chat Write Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `edit_file` and `create_file` write tools to PAL's chat mode so the LLM can modify and create vault files during conversation.

**Architecture:** Extend the existing `ToolExecutor` in `pal/tools.py` with two new tools that use `WikiManager` for writing and git commits. Add `WikiManager` as a dependency to `ToolExecutor`. Update daemon to pass it through.

**Tech Stack:** Python 3.12, pytest, existing WikiManager/frontmatter modules

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `pal/tools.py` | Modify | Add edit_file/create_file schemas + handlers, add WikiManager dependency |
| `tests/test_tools.py` | Modify | Add write tool tests |
| `pal/daemon.py` | Modify | Pass `wiki=self.wiki` to ToolExecutor |

---

### Task 1: Add WikiManager dependency to ToolExecutor and update daemon

**Files:**
- Modify: `pal/tools.py:85-90`
- Modify: `pal/daemon.py:61-65`
- Modify: `tests/test_tools.py:5-6,34`

- [ ] **Step 1: Update ToolExecutor.__init__ to accept wiki parameter**

In `pal/tools.py`, change the import and class:

```python
# Add import at top (line 6, after Path import)
from pal.wiki import WikiManager

# Update __init__ (line 88)
    def __init__(self, vault_path: Path, retrieval: RetrievalClient | None, wiki: WikiManager | None = None) -> None:
        self.vault_path = vault_path.resolve()
        self.retrieval = retrieval
        self.wiki = wiki
```

Update the class docstring (line 86):

```python
    """Executes tool calls against the vault."""
```

Update the module docstring (line 1):

```python
"""Vault tools for chat — read and write access to wiki content.
```

- [ ] **Step 2: Update daemon.py to pass wiki**

In `pal/daemon.py`, change the ToolExecutor construction (lines 61-65):

```python
        from pal.tools import ToolExecutor
        self.tool_executor = ToolExecutor(
            vault_path=config.vault_path,
            retrieval=self.retrieval,
            wiki=self.wiki,
        )
```

- [ ] **Step 3: Run tests to verify no regressions**

Run: `pytest -v`
Expected: All 206 tests pass. The `wiki` parameter defaults to `None` so existing test fixtures don't need changes.

- [ ] **Step 4: Commit**

```bash
git add pal/tools.py pal/daemon.py
git commit -m "refactor(tools): add WikiManager dependency to ToolExecutor"
```

---

### Task 2: Add edit_file tool

**Files:**
- Modify: `pal/tools.py`
- Modify: `tests/test_tools.py`

- [ ] **Step 1: Write failing tests for edit_file**

Add to `tests/test_tools.py`. First, update the vault fixture to initialize git (needed for commit verification) and create a helper to build an executor with a WikiManager:

```python
import subprocess

from pal.tools import ToolExecutor
from pal.wiki import WikiManager


@pytest.fixture()
def vault(tmp_path) -> Path:
    """Create a minimal vault structure for tool tests."""
    # Public articles
    research = tmp_path / "Research"
    research.mkdir()
    (research / "quantum.md").write_text(
        "---\ntitle: Quantum Computing\ntags:\n- physics\n---\n\n# Quantum Computing\n\nQubits are neat.\n"
    )
    (research / "ml.md").write_text(
        "---\ntitle: Machine Learning\n---\n\n# Machine Learning\n\nNeural nets.\n"
    )
    # Raw directory
    raw = tmp_path / "raw" / "web"
    raw.mkdir(parents=True)
    (raw / "page-abc.md").write_text(
        "---\ntitle: Fetched Page\n---\n\nRaw fetched content.\n"
    )
    # System directory (should be hidden from list_directory)
    wisdom = tmp_path / "_wisdom"
    wisdom.mkdir()
    (wisdom / "be-kind.md").write_text("---\ntitle: Be Kind\n---\n\nBe kind.\n")
    # Init git repo for commit tests
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial vault"],
        cwd=tmp_path, capture_output=True, check=True,
        env={**__import__("os").environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test"},
    )
    return tmp_path


@pytest.fixture()
def wiki_executor(vault) -> ToolExecutor:
    """ToolExecutor with a WikiManager for write tests."""
    wiki = WikiManager(vault)
    return ToolExecutor(vault_path=vault, retrieval=None, wiki=wiki)
```

Now the tests:

```python
def test_edit_file(wiki_executor, vault):
    result = wiki_executor.run("edit_file", {
        "path": "Research/quantum.md",
        "content": "# Quantum Computing\n\n## Overview\n\nQubits are the building blocks.\n",
    })
    assert "edited" in result.lower() or "updated" in result.lower()
    # Verify content changed
    text = (vault / "Research" / "quantum.md").read_text()
    assert "building blocks" in text
    # Verify frontmatter preserved
    assert "title: Quantum Computing" in text
    assert "physics" in text  # tags preserved


def test_edit_file_not_found(wiki_executor):
    result = wiki_executor.run("edit_file", {
        "path": "Research/nonexistent.md",
        "content": "new content",
    })
    assert "not found" in result.lower() or "does not exist" in result.lower()


def test_edit_file_system_dir(wiki_executor):
    result = wiki_executor.run("edit_file", {
        "path": "_wisdom/be-kind.md",
        "content": "new content",
    })
    assert "system" in result.lower() or "not allowed" in result.lower()


def test_edit_file_path_traversal(wiki_executor):
    result = wiki_executor.run("edit_file", {
        "path": "../../etc/passwd",
        "content": "hacked",
    })
    assert "outside vault" in result.lower() or "escapes" in result.lower()


def test_edit_file_git_commits(wiki_executor, vault):
    wiki_executor.run("edit_file", {
        "path": "Research/quantum.md",
        "content": "# Quantum\n\nRewritten.\n",
    })
    result = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=vault, capture_output=True, text=True,
    )
    assert "quantum" in result.stdout.lower() or "edit" in result.stdout.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools.py::test_edit_file -v`
Expected: Fails — `edit_file` not in dispatch dict.

- [ ] **Step 3: Add edit_file tool schema to TOOL_DEFINITIONS**

Add to `TOOL_DEFINITIONS` list in `pal/tools.py` (after the `search_vault` entry, before the closing `]`):

```python
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Rewrite the body of an existing vault file. Preserves frontmatter (title, tags). Use for restructuring, reformatting, or updating content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to vault root (e.g. 'Research/quantum.md'). Must already exist.",
                    },
                    "content": {
                        "type": "string",
                        "description": "New body content for the file (markdown, without frontmatter).",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
```

- [ ] **Step 4: Add _is_system_path helper and _edit_file handler**

Add helper method to `ToolExecutor` (after `_resolve_safe`):

```python
    def _is_system_path(self, path: str) -> bool:
        """Check if a path targets a system directory (_-prefixed)."""
        return any(part.startswith("_") for part in Path(path).parts)
```

Add `_edit_file` handler (after `_search_vault`):

```python
    def _edit_file(self, arguments: dict) -> str:
        path = arguments.get("path", "")
        content = arguments.get("content", "")
        if not path:
            return "Error: 'path' parameter is required."
        if not content:
            return "Error: 'content' parameter is required."
        if self._is_system_path(path):
            return f"Error: writing to system directories is not allowed: {path}"
        resolved = self._resolve_safe(path)
        if resolved is None:
            return f"Error: path escapes outside vault: {path}"
        if not resolved.exists():
            return f"Error: file does not exist: {path} (use create_file for new files)"
        if self.wiki is None:
            return "Error: write operations are not available (no wiki manager)."
        # Read existing frontmatter to preserve title and tags
        meta, _ = self.wiki.read_article(path)
        title = meta.get("title", Path(path).stem)
        tags = meta.get("tags")
        self.wiki.write_article(path, title, content, tags=tags)
        self.wiki.git_commit(f"Edit {path} via chat")
        return f"Updated: {path}"
```

Update `run()` dispatch dict to include `edit_file`:

```python
        handler = {
            "read_file": self._read_file,
            "list_directory": self._list_directory,
            "search_content": self._search_content,
            "edit_file": self._edit_file,
        }.get(name)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_tools.py -v`
Expected: All tests pass (old and new).

- [ ] **Step 6: Run full test suite**

Run: `pytest -v`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add pal/tools.py tests/test_tools.py
git commit -m "feat(tools): add edit_file tool for rewriting vault files via chat"
```

---

### Task 3: Add create_file tool

**Files:**
- Modify: `pal/tools.py`
- Modify: `tests/test_tools.py`

- [ ] **Step 1: Write failing tests for create_file**

Add to `tests/test_tools.py`:

```python
def test_create_file(wiki_executor, vault):
    result = wiki_executor.run("create_file", {
        "path": "Research/newtons-laws.md",
        "title": "Newton's Laws",
        "content": "# Newton's Laws\n\nThree laws of motion.\n",
        "tags": ["physics"],
    })
    assert "created" in result.lower()
    text = (vault / "Research" / "newtons-laws.md").read_text()
    assert "Newton's Laws" in text
    assert "Three laws" in text
    assert "physics" in text


def test_create_file_already_exists(wiki_executor):
    result = wiki_executor.run("create_file", {
        "path": "Research/quantum.md",
        "title": "Quantum",
        "content": "duplicate",
    })
    assert "already exists" in result.lower()


def test_create_file_system_dir(wiki_executor):
    result = wiki_executor.run("create_file", {
        "path": "_wisdom/new-wisdom.md",
        "title": "New Wisdom",
        "content": "some wisdom",
    })
    assert "system" in result.lower() or "not allowed" in result.lower()


def test_create_file_creates_parent_dirs(wiki_executor, vault):
    result = wiki_executor.run("create_file", {
        "path": "NewTopic/subtopic/article.md",
        "title": "Deep Article",
        "content": "# Deep Article\n\nNested content.\n",
    })
    assert "created" in result.lower()
    assert (vault / "NewTopic" / "subtopic" / "article.md").exists()


def test_create_file_git_commits(wiki_executor, vault):
    wiki_executor.run("create_file", {
        "path": "Research/new-article.md",
        "title": "New Article",
        "content": "# New\n\nContent.\n",
    })
    result = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=vault, capture_output=True, text=True,
    )
    assert "new-article" in result.stdout.lower() or "create" in result.stdout.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools.py::test_create_file -v`
Expected: Fails — `create_file` not in dispatch dict.

- [ ] **Step 3: Add create_file tool schema to TOOL_DEFINITIONS**

Add to `TOOL_DEFINITIONS` (after the `edit_file` entry):

```python
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "Create a new file in the vault with proper frontmatter. Use for writing new notes or articles.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to vault root (e.g. 'Research/new-topic.md'). Must not already exist.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Article title for frontmatter.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Body content for the file (markdown, without frontmatter).",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional tags for frontmatter.",
                    },
                },
                "required": ["path", "title", "content"],
            },
        },
    },
```

- [ ] **Step 4: Add _create_file handler**

Add to `ToolExecutor` (after `_edit_file`):

```python
    def _create_file(self, arguments: dict) -> str:
        path = arguments.get("path", "")
        title = arguments.get("title", "")
        content = arguments.get("content", "")
        tags = arguments.get("tags")
        if not path:
            return "Error: 'path' parameter is required."
        if not title:
            return "Error: 'title' parameter is required."
        if not content:
            return "Error: 'content' parameter is required."
        if self._is_system_path(path):
            return f"Error: writing to system directories is not allowed: {path}"
        resolved = self._resolve_safe(path)
        if resolved is None:
            return f"Error: path escapes outside vault: {path}"
        if resolved.exists():
            return f"Error: file already exists: {path} (use edit_file to modify)"
        if self.wiki is None:
            return "Error: write operations are not available (no wiki manager)."
        self.wiki.write_article(path, title, content, tags=tags)
        self.wiki.git_commit(f"Create {path} via chat")
        return f"Created: {path}"
```

Update `run()` dispatch dict to include `create_file`:

```python
        handler = {
            "read_file": self._read_file,
            "list_directory": self._list_directory,
            "search_content": self._search_content,
            "edit_file": self._edit_file,
            "create_file": self._create_file,
        }.get(name)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_tools.py -v`
Expected: All tests pass.

- [ ] **Step 6: Run full test suite**

Run: `pytest -v`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add pal/tools.py tests/test_tools.py
git commit -m "feat(tools): add create_file tool for creating vault files via chat"
```

---

### Task 4: Add progress label for write tools in CLI

**Files:**
- Modify: `pal/cli.py`

- [ ] **Step 1: Read cli.py to find _tool_progress_label**

Read: `pal/cli.py`

- [ ] **Step 2: Add labels for edit_file and create_file**

In `_tool_progress_label()`, add before the final `return f"[{tool}...]"`:

```python
    if tool == "edit_file":
        return f"[editing {arguments.get('path', '?')}...]"
    if tool == "create_file":
        return f"[creating {arguments.get('path', '?')}...]"
```

- [ ] **Step 3: Run full test suite**

Run: `pytest -v`
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add pal/cli.py
git commit -m "feat(cli): add progress labels for edit_file and create_file tools"
```

---

### Task 5: End-to-end manual test

- [ ] **Step 1: Start daemon and test edit_file**

```bash
# Terminal 1
pal-daemon

# Terminal 2
pal
you> Read the agent harness file and restructure it with clear headings
# Should see: [reading agent-harness-like-openclaw-or-pi-mono.md...]
# Then: [editing agent-harness-like-openclaw-or-pi-mono.md...]
# Then a response confirming the changes
```

- [ ] **Step 2: Verify the edit in Obsidian or via git**

```bash
cd ~/vault && git log --oneline -2
git diff HEAD~1
```

- [ ] **Step 3: Test create_file**

```bash
you> Write a new article about the three laws of thermodynamics and save it in Research/
# Should see: [creating Research/thermodynamics.md...]
```

- [ ] **Step 4: Test safety — system dir rejection**

```bash
you> Create a new file in _wisdom/ called test.md
# LLM should either refuse or the tool should return an error about system dirs
```
