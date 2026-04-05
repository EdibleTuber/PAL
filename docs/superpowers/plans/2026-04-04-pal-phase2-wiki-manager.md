# PAL Phase 2: Wiki Manager — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give PAL the ability to read, write, and manage markdown articles in an Obsidian vault with YAML frontmatter, auto-maintained indexes, git commits, and slash commands (`/note`, `/read`, `/lint`, `/exit`).

**Architecture:** A `WikiManager` class owns all vault I/O — reading/writing markdown with frontmatter, maintaining `_index.md`, and committing changes via git. The daemon delegates wiki commands to this manager. Articles use YAML frontmatter for metadata (title, tags, created, updated, status). The master index is rebuilt from vault contents on demand. `/exit` is handled client-side in the CLI (same as `/quit`).

**Tech Stack:** Python 3.12, PyYAML (frontmatter parsing), subprocess (git operations), existing PAL modules (daemon, protocol, cli, config)

---

## File Structure

```
pal/
├── wiki.py              # WikiManager — read/write/index/lint/git operations on ~/vault
├── frontmatter.py       # Parse and serialize YAML frontmatter in markdown files
├── daemon.py            # Modified — wire wiki commands into _handle_command
├── cli.py               # Modified — add /exit as alias for /quit
tests/
├── test_frontmatter.py  # Frontmatter parse/serialize round-trips
├── test_wiki.py         # WikiManager operations against a temp vault
├── test_wiki_commands.py # Integration: daemon wiki commands via client
```

---

### Task 1: YAML Frontmatter Parser

**Files:**
- Create: `pal/frontmatter.py`
- Create: `tests/test_frontmatter.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_frontmatter.py`:
```python
"""Tests for YAML frontmatter parsing and serialization."""
from pal.frontmatter import parse_frontmatter, serialize_frontmatter


def test_parse_frontmatter_basic():
    content = """---
title: Test Article
tags: [python, testing]
---

# Test Article

Body text here.
"""
    meta, body = parse_frontmatter(content)
    assert meta["title"] == "Test Article"
    assert meta["tags"] == ["python", "testing"]
    assert body.strip() == "# Test Article\n\nBody text here."


def test_parse_frontmatter_empty():
    content = "# No frontmatter\n\nJust body."
    meta, body = parse_frontmatter(content)
    assert meta == {}
    assert body == content


def test_parse_frontmatter_empty_yaml():
    content = "---\n---\n\nBody only."
    meta, body = parse_frontmatter(content)
    assert meta == {}
    assert body.strip() == "Body only."


def test_parse_frontmatter_preserves_body_whitespace():
    content = "---\ntitle: X\n---\n\nLine one.\n\nLine two.\n"
    meta, body = parse_frontmatter(content)
    assert meta["title"] == "X"
    assert "\n\nLine one.\n\nLine two.\n" == body


def test_serialize_frontmatter():
    meta = {"title": "My Article", "tags": ["ai", "wiki"]}
    body = "# My Article\n\nContent here.\n"
    result = serialize_frontmatter(meta, body)
    assert result.startswith("---\n")
    assert "title: My Article" in result
    assert result.endswith("\n# My Article\n\nContent here.\n")
    # Round-trip
    parsed_meta, parsed_body = parse_frontmatter(result)
    assert parsed_meta["title"] == "My Article"
    assert parsed_meta["tags"] == ["ai", "wiki"]
    assert parsed_body.strip() == "# My Article\n\nContent here."


def test_serialize_frontmatter_empty_meta():
    body = "# Just body\n"
    result = serialize_frontmatter({}, body)
    assert result == "# Just body\n"


def test_parse_frontmatter_with_dashes_in_body():
    content = "---\ntitle: Test\n---\n\nSome text with --- dashes in it.\n"
    meta, body = parse_frontmatter(content)
    assert meta["title"] == "Test"
    assert "--- dashes" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_frontmatter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pal.frontmatter'`

- [ ] **Step 3: Implement frontmatter.py**

`pal/frontmatter.py`:
```python
"""YAML frontmatter parsing and serialization for markdown files.

Frontmatter is delimited by --- on its own line at the start of the file.
The opening --- must be the very first line. The closing --- ends the
frontmatter block. Everything after is the body.
"""
import yaml


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown content.

    Returns (metadata_dict, body_without_frontmatter).
    If no frontmatter is present, returns ({}, original_content).
    """
    if not content.startswith("---"):
        return {}, content

    # Find closing --- (must be on its own line after the opening)
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content

    yaml_str = content[4:end]  # skip opening "---\n"
    body = content[end + 4:]   # skip "\n---"

    try:
        meta = yaml.safe_load(yaml_str) or {}
    except yaml.YAMLError:
        return {}, content

    return meta, body


def serialize_frontmatter(meta: dict, body: str) -> str:
    """Serialize metadata and body into a markdown string with YAML frontmatter.

    If meta is empty, returns just the body (no frontmatter block).
    """
    if not meta:
        return body

    yaml_str = yaml.dump(meta, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return f"---\n{yaml_str}---\n{body}"
```

- [ ] **Step 4: Add pyyaml dependency**

Add `"pyyaml>=6.0"` to `dependencies` in `pyproject.toml`:

```toml
dependencies = [
    "httpx>=0.27.0",
    "prompt-toolkit>=3.0.0",
    "rich>=13.0.0",
    "pyyaml>=6.0",
]
```

Run: `pip install -e ".[dev]"`

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_frontmatter.py -v`
Expected: all 7 tests PASS

- [ ] **Step 6: Commit**

```bash
git add pal/frontmatter.py tests/test_frontmatter.py pyproject.toml
git commit -m "feat: YAML frontmatter parser and serializer"
```

---

### Task 2: WikiManager — Core Read/Write Operations

**Files:**
- Create: `pal/wiki.py`
- Create: `tests/test_wiki.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_wiki.py`:
```python
"""Tests for WikiManager — vault read/write operations."""
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from pal.wiki import WikiManager


@pytest.fixture()
def vault(tmp_path) -> Path:
    """Create a temporary vault directory."""
    return tmp_path / "vault"


@pytest.fixture()
def wiki(vault) -> WikiManager:
    """Create a WikiManager pointing at the temp vault."""
    return WikiManager(vault)


def test_init_creates_vault_structure(wiki, vault):
    """First access creates vault dir and system directories."""
    wiki.init_vault()
    assert vault.exists()
    assert (vault / "_index.md").exists()


def test_write_article(wiki, vault):
    wiki.init_vault()
    wiki.write_article(
        path="Projects/my-project.md",
        title="My Project",
        body="# My Project\n\nA cool project.\n",
        tags=["project", "python"],
    )
    full_path = vault / "Projects" / "my-project.md"
    assert full_path.exists()
    content = full_path.read_text()
    assert "title: My Project" in content
    assert "# My Project" in content
    assert "A cool project." in content


def test_write_article_creates_parent_dirs(wiki, vault):
    wiki.init_vault()
    wiki.write_article(
        path="Deep/Nested/Dir/article.md",
        title="Nested",
        body="Content.\n",
    )
    assert (vault / "Deep" / "Nested" / "Dir" / "article.md").exists()


def test_write_article_updates_existing(wiki, vault):
    wiki.init_vault()
    wiki.write_article(path="test.md", title="V1", body="Version 1.\n")
    wiki.write_article(path="test.md", title="V2", body="Version 2.\n")
    content = (vault / "test.md").read_text()
    assert "title: V2" in content
    assert "Version 2." in content
    # Should have both created and updated timestamps
    meta = yaml.safe_load(content.split("---")[1])
    assert "created" in meta
    assert "updated" in meta


def test_read_article(wiki, vault):
    wiki.init_vault()
    wiki.write_article(path="test.md", title="Test", body="Body here.\n")
    meta, body = wiki.read_article("test.md")
    assert meta["title"] == "Test"
    assert "Body here." in body


def test_read_article_not_found(wiki, vault):
    wiki.init_vault()
    with pytest.raises(FileNotFoundError):
        wiki.read_article("nonexistent.md")


def test_list_articles(wiki, vault):
    wiki.init_vault()
    wiki.write_article(path="a.md", title="A", body="A content.\n")
    wiki.write_article(path="Projects/b.md", title="B", body="B content.\n")
    articles = wiki.list_articles()
    paths = [a["path"] for a in articles]
    assert "a.md" in paths
    assert "Projects/b.md" in paths
    # System dirs should not appear
    for a in articles:
        assert not a["path"].startswith("_")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_wiki.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pal.wiki'`

- [ ] **Step 3: Implement wiki.py**

`pal/wiki.py`:
```python
"""WikiManager — read, write, and manage markdown articles in the vault.

All articles are markdown files with YAML frontmatter. The vault is
organized into topic directories. System directories (prefixed with _)
are managed by PAL and should not appear in user-facing article lists.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

from pal.frontmatter import parse_frontmatter, serialize_frontmatter

logger = logging.getLogger(__name__)

SYSTEM_DIRS = {"_index.md", "_wisdom", "_learning", "_profile"}


class WikiManager:
    def __init__(self, vault_path: Path) -> None:
        self.vault_path = vault_path

    def init_vault(self) -> None:
        """Create vault directory structure if it doesn't exist."""
        self.vault_path.mkdir(parents=True, exist_ok=True)
        index_path = self.vault_path / "_index.md"
        if not index_path.exists():
            index_path.write_text(
                "---\ntitle: Vault Index\n---\n\n"
                "# Vault Index\n\n"
                "_Auto-maintained by PAL. Lists all articles in the vault._\n"
            )

    def write_article(
        self,
        path: str,
        title: str,
        body: str,
        tags: list[str] | None = None,
    ) -> Path:
        """Write or update a markdown article in the vault.

        Creates parent directories as needed. Preserves the original
        'created' timestamp on updates and sets 'updated'.
        """
        full_path = self.vault_path / path
        full_path.parent.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        # Preserve created timestamp on update
        created = now
        if full_path.exists():
            existing_meta, _ = parse_frontmatter(full_path.read_text())
            created = existing_meta.get("created", now)

        meta: dict = {
            "title": title,
            "created": created,
            "updated": now,
        }
        if tags:
            meta["tags"] = tags

        content = serialize_frontmatter(meta, body)
        full_path.write_text(content)
        logger.info("Wrote article: %s", path)
        return full_path

    def read_article(self, path: str) -> tuple[dict, str]:
        """Read an article, returning (frontmatter_dict, body).

        Raises FileNotFoundError if the article doesn't exist.
        """
        full_path = self.vault_path / path
        if not full_path.exists():
            raise FileNotFoundError(f"Article not found: {path}")
        return parse_frontmatter(full_path.read_text())

    def list_articles(self) -> list[dict]:
        """List all non-system articles in the vault.

        Returns a list of dicts with 'path' and 'title' keys.
        """
        articles = []
        for md_file in sorted(self.vault_path.rglob("*.md")):
            rel = md_file.relative_to(self.vault_path)
            # Skip system files/dirs
            if any(part.startswith("_") for part in rel.parts):
                continue
            meta, _ = parse_frontmatter(md_file.read_text())
            articles.append({
                "path": str(rel),
                "title": meta.get("title", rel.stem),
            })
        return articles
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_wiki.py -v`
Expected: all 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pal/wiki.py tests/test_wiki.py
git commit -m "feat: WikiManager — core read/write/list operations"
```

---

### Task 3: WikiManager — Index Maintenance

**Files:**
- Modify: `pal/wiki.py`
- Modify: `tests/test_wiki.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_wiki.py`:
```python
def test_rebuild_index(wiki, vault):
    wiki.init_vault()
    wiki.write_article(path="Projects/alpha.md", title="Alpha", body="Alpha content.\n")
    wiki.write_article(path="Disciplines/beta.md", title="Beta", body="Beta content.\n", tags=["science"])
    wiki.rebuild_index()
    index_content = (vault / "_index.md").read_text()
    assert "Alpha" in index_content
    assert "Beta" in index_content
    assert "Projects/alpha.md" in index_content
    assert "Disciplines/beta.md" in index_content


def test_rebuild_index_empty_vault(wiki, vault):
    wiki.init_vault()
    wiki.rebuild_index()
    index_content = (vault / "_index.md").read_text()
    assert "Vault Index" in index_content
    # Should not crash with no articles
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_wiki.py::test_rebuild_index -v`
Expected: FAIL — `AttributeError: 'WikiManager' object has no attribute 'rebuild_index'`

- [ ] **Step 3: Implement rebuild_index**

Add to `pal/wiki.py` inside the `WikiManager` class:
```python
    def rebuild_index(self) -> None:
        """Rebuild _index.md from all articles in the vault.

        Groups articles by their top-level directory, with a summary line
        for each article showing path and title.
        """
        articles = self.list_articles()
        groups: dict[str, list[dict]] = {}
        for article in articles:
            parts = article["path"].split("/")
            group = parts[0] if len(parts) > 1 else "Ungrouped"
            groups.setdefault(group, []).append(article)

        lines = ["# Vault Index\n"]
        lines.append("_Auto-maintained by PAL. Lists all articles in the vault._\n")

        if not groups:
            lines.append("_No articles yet._\n")
        else:
            for group_name in sorted(groups):
                lines.append(f"\n## {group_name}\n")
                for article in sorted(groups[group_name], key=lambda a: a["path"]):
                    lines.append(f"- [[{article['path']}|{article['title']}]]")
            lines.append("")

        body = "\n".join(lines)
        meta = {"title": "Vault Index"}
        index_path = self.vault_path / "_index.md"
        index_path.write_text(serialize_frontmatter(meta, body))
        logger.info("Rebuilt vault index (%d articles)", len(articles))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_wiki.py -v`
Expected: all 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pal/wiki.py tests/test_wiki.py
git commit -m "feat: WikiManager — auto-maintained vault index"
```

---

### Task 4: WikiManager — Git Operations

**Files:**
- Modify: `pal/wiki.py`
- Modify: `tests/test_wiki.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_wiki.py`:
```python
import subprocess


def test_git_init(wiki, vault):
    """init_vault creates a git repo if one doesn't exist."""
    wiki.init_vault()
    wiki.git_init()
    assert (vault / ".git").exists()


def test_git_commit(wiki, vault):
    wiki.init_vault()
    wiki.git_init()
    wiki.write_article(path="test.md", title="Test", body="Content.\n")
    wiki.git_commit("Add test article")
    result = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=vault,
        capture_output=True,
        text=True,
    )
    assert "Add test article" in result.stdout


def test_git_commit_no_changes(wiki, vault):
    """Committing with no changes does not error."""
    wiki.init_vault()
    wiki.git_init()
    wiki.git_commit("Nothing to commit")
    # Should not raise — just a no-op
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_wiki.py::test_git_init -v`
Expected: FAIL — `AttributeError: 'WikiManager' object has no attribute 'git_init'`

- [ ] **Step 3: Implement git operations**

Add to `pal/wiki.py` inside the `WikiManager` class:
```python
    def git_init(self) -> None:
        """Initialize a git repo in the vault if one doesn't exist."""
        if not (self.vault_path / ".git").exists():
            subprocess.run(
                ["git", "init"],
                cwd=self.vault_path,
                capture_output=True,
                check=True,
            )
            # Initial commit so we have a HEAD
            subprocess.run(
                ["git", "add", "."],
                cwd=self.vault_path,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "Initial vault", "--allow-empty"],
                cwd=self.vault_path,
                capture_output=True,
                check=True,
            )
            logger.info("Initialized git repo in vault")

    def git_commit(self, message: str) -> None:
        """Stage all changes in the vault and commit.

        No-op if there are no changes to commit.
        """
        subprocess.run(
            ["git", "add", "."],
            cwd=self.vault_path,
            capture_output=True,
            check=True,
        )
        # Check if there's anything to commit
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.vault_path,
            capture_output=True,
            text=True,
        )
        if not result.stdout.strip():
            return
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=self.vault_path,
            capture_output=True,
            check=True,
        )
        logger.info("Committed: %s", message)
```

Also add `import subprocess` at the top of `pal/wiki.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_wiki.py -v`
Expected: all 12 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pal/wiki.py tests/test_wiki.py
git commit -m "feat: WikiManager — git init and commit operations"
```

---

### Task 5: WikiManager — Lint

**Files:**
- Modify: `pal/wiki.py`
- Modify: `tests/test_wiki.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_wiki.py`:
```python
def test_lint_finds_missing_title(wiki, vault):
    wiki.init_vault()
    # Write a file with no frontmatter
    (vault / "bad.md").write_text("# No frontmatter\n\nJust body.\n")
    issues = wiki.lint()
    paths = [i["path"] for i in issues]
    assert "bad.md" in paths
    assert any("title" in i["issue"].lower() for i in issues)


def test_lint_clean_vault(wiki, vault):
    wiki.init_vault()
    wiki.write_article(path="good.md", title="Good", body="All good.\n")
    issues = wiki.lint()
    article_issues = [i for i in issues if i["path"] == "good.md"]
    assert article_issues == []


def test_lint_finds_broken_wiki_links(wiki, vault):
    wiki.init_vault()
    wiki.write_article(
        path="linker.md",
        title="Linker",
        body="See [[nonexistent.md]] for more.\n",
    )
    issues = wiki.lint()
    assert any("nonexistent.md" in i["issue"] for i in issues)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_wiki.py::test_lint_finds_missing_title -v`
Expected: FAIL — `AttributeError: 'WikiManager' object has no attribute 'lint'`

- [ ] **Step 3: Implement lint**

Add to the top of `pal/wiki.py` (imports section):
```python
import re
```

Add to `pal/wiki.py` inside the `WikiManager` class:
```python
    def lint(self) -> list[dict]:
        """Check vault health. Returns a list of issue dicts.

        Each issue has 'path' and 'issue' keys.
        Checks:
        - Missing title in frontmatter
        - Broken wiki-style links ([[target]])
        """
        issues = []
        all_paths = set()
        for md_file in self.vault_path.rglob("*.md"):
            rel = md_file.relative_to(self.vault_path)
            if any(part.startswith("_") for part in rel.parts):
                continue
            all_paths.add(str(rel))

        for md_file in self.vault_path.rglob("*.md"):
            rel = md_file.relative_to(self.vault_path)
            if any(part.startswith("_") for part in rel.parts):
                continue

            path_str = str(rel)
            content = md_file.read_text()
            meta, body = parse_frontmatter(content)

            # Check: missing title
            if not meta.get("title"):
                issues.append({"path": path_str, "issue": "Missing title in frontmatter"})

            # Check: broken wiki links
            for match in re.finditer(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]", body):
                target = match.group(1)
                if target not in all_paths:
                    issues.append({
                        "path": path_str,
                        "issue": f"Broken link: [[{target}]] — target not found",
                    })

        return issues
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_wiki.py -v`
Expected: all 15 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pal/wiki.py tests/test_wiki.py
git commit -m "feat: WikiManager — vault linting (missing titles, broken links)"
```

---

### Task 6: Wire Wiki Commands into Daemon

**Files:**
- Modify: `pal/daemon.py`
- Create: `tests/test_wiki_commands.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_wiki_commands.py`:
```python
"""Integration tests for wiki slash commands via the daemon."""
import pytest

from pal.client import PalClient
from pal.protocol import ResponseMessage, StreamChunkMessage


@pytest.fixture()
def vault_path(tmp_path):
    return tmp_path / "vault"


@pytest.fixture()
async def wiki_daemon(socket_path, mock_inference_server, vault_path):
    """Start a daemon with wiki support pointing at a temp vault."""
    import asyncio
    from pal.config import Config
    from pal.daemon import Daemon

    cfg = Config(
        inference_url=mock_inference_server,
        model="test-model",
        socket_path=socket_path,
        history_depth=50,
        vault_path=vault_path,
    )
    daemon = Daemon(cfg)
    task = asyncio.create_task(daemon.serve())
    for _ in range(100):
        if socket_path.exists():
            break
        await asyncio.sleep(0.01)
    yield daemon
    daemon.shutdown()
    await task


@pytest.mark.asyncio
async def test_note_command_creates_article(wiki_daemon, socket_path, vault_path):
    """The /note command triggers article creation via streaming chat."""
    client = PalClient(socket_path)
    await client.connect()

    # /note sends a command; the daemon streams back a response via inference
    # (the mock echoes user text, so we verify the command reaches the daemon)
    tokens = []
    async for msg in client.chat("/note My Test Topic"):
        if isinstance(msg, StreamChunkMessage):
            tokens.append(msg.token)
        elif isinstance(msg, ResponseMessage):
            break

    # The daemon should have processed this as a wiki note request
    # The response comes from inference (mock echoes it)
    full = "".join(tokens)
    assert len(full) > 0

    await client.close()


@pytest.mark.asyncio
async def test_read_command(wiki_daemon, socket_path, vault_path):
    """/read returns an article's content."""
    client = PalClient(socket_path)
    await client.connect()

    # Create an article first via the wiki manager directly
    from pal.wiki import WikiManager
    wm = WikiManager(vault_path)
    wm.init_vault()
    wm.write_article(path="test.md", title="Test", body="# Test\n\nHello world.\n")

    resp = await client.command("read", "test.md")
    assert "Hello world." in resp.text

    await client.close()


@pytest.mark.asyncio
async def test_lint_command(wiki_daemon, socket_path, vault_path):
    """/lint reports vault health."""
    client = PalClient(socket_path)
    await client.connect()

    # Create a clean article
    from pal.wiki import WikiManager
    wm = WikiManager(vault_path)
    wm.init_vault()
    wm.write_article(path="good.md", title="Good", body="Content.\n")

    resp = await client.command("lint")
    assert "issue" in resp.text.lower() or "clean" in resp.text.lower()

    await client.close()


@pytest.mark.asyncio
async def test_status_command_includes_vault(wiki_daemon, socket_path, vault_path):
    """/status now includes vault info."""
    client = PalClient(socket_path)
    await client.connect()

    from pal.wiki import WikiManager
    wm = WikiManager(vault_path)
    wm.init_vault()
    wm.write_article(path="a.md", title="A", body="Content.\n")

    resp = await client.command("status")
    assert "Vault:" in resp.text or "vault" in resp.text.lower()

    await client.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_wiki_commands.py -v`
Expected: FAIL — tests fail because daemon doesn't handle wiki commands yet

- [ ] **Step 3: Wire wiki into the daemon**

Modify `pal/daemon.py`:

1. Add import at the top:
```python
from pal.wiki import WikiManager
```

2. In `Daemon.__init__`, add after the inference client setup:
```python
        self.wiki = WikiManager(config.vault_path)
        self.wiki.init_vault()
```

3. Replace the `_handle_command` method:
```python
    async def _handle_command(
        self,
        msg: CommandMessage,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a slash command."""
        if msg.name in ("quit", "exit"):
            resp = ResponseMessage(text="Goodbye.", command="quit")
            writer.write(encode_message(resp))
            await writer.drain()
        elif msg.name == "status":
            articles = self.wiki.list_articles()
            resp = ResponseMessage(
                text=(
                    f"Model: {self.inference.model}\n"
                    f"Server: {self.inference.base_url}\n"
                    f"Vault: {self.wiki.vault_path} ({len(articles)} articles)"
                ),
                command="status",
            )
            writer.write(encode_message(resp))
            await writer.drain()
        elif msg.name == "read":
            await self._handle_read(msg.args, writer)
        elif msg.name == "lint":
            await self._handle_lint(writer)
        elif msg.name == "note":
            # /note is handled as a chat message with wiki context
            await self._handle_note(msg.args, writer)
        else:
            error = ErrorMessage(error=f"Unknown command: /{msg.name}")
            writer.write(encode_message(error))
            await writer.drain()
```

4. Add the new handler methods to the `Daemon` class:
```python
    async def _handle_read(self, path: str, writer: asyncio.StreamWriter) -> None:
        """Handle /read <path> — return article content."""
        path = path.strip()
        if not path:
            error = ErrorMessage(error="Usage: /read <path>")
            writer.write(encode_message(error))
            await writer.drain()
            return
        try:
            meta, body = self.wiki.read_article(path)
            title = meta.get("title", path)
            tags = meta.get("tags", [])
            header = f"**{title}**"
            if tags:
                header += f"  tags: {', '.join(tags)}"
            resp = ResponseMessage(text=f"{header}\n\n{body}", command="read")
            writer.write(encode_message(resp))
            await writer.drain()
        except FileNotFoundError:
            error = ErrorMessage(error=f"Article not found: {path}")
            writer.write(encode_message(error))
            await writer.drain()

    async def _handle_lint(self, writer: asyncio.StreamWriter) -> None:
        """Handle /lint — run vault health check."""
        issues = self.wiki.lint()
        if not issues:
            resp = ResponseMessage(text="Vault is clean — no issues found.", command="lint")
        else:
            lines = [f"Found {len(issues)} issue(s):\n"]
            for issue in issues:
                lines.append(f"- **{issue['path']}**: {issue['issue']}")
            resp = ResponseMessage(text="\n".join(lines), command="lint")
        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_note(
        self,
        topic: str,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle /note <topic> — create or update a wiki article.

        Sends the topic to the inference model with instructions to write
        a wiki article, then saves the response to the vault.
        """
        topic = topic.strip()
        if not topic:
            error = ErrorMessage(error="Usage: /note <topic>")
            writer.write(encode_message(error))
            await writer.drain()
            return

        # Build a prompt for article generation
        prompt = (
            f"Write a concise wiki article about: {topic}\n\n"
            "Format: Start with a markdown heading, then clear explanatory paragraphs. "
            "Be informative and concise."
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        full_response = []
        try:
            async for token in self.inference.stream(messages):
                chunk = StreamChunkMessage(token=token)
                writer.write(encode_message(chunk))
                await writer.drain()
                full_response.append(token)
        except Exception as exc:
            logger.exception("Inference error during /note: %s", exc)
            error = ErrorMessage(error=f"Inference error: {exc}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        body = "".join(full_response)

        # Derive a filename from the topic
        slug = topic.lower().replace(" ", "-")
        slug = "".join(c for c in slug if c.isalnum() or c == "-")
        path = f"{slug}.md"

        self.wiki.write_article(path=path, title=topic, body=body + "\n")
        self.wiki.rebuild_index()
        self.wiki.git_init()
        self.wiki.git_commit(f"note: {topic}")

        done = ResponseMessage(text=body, command="note")
        writer.write(encode_message(done))
        await writer.drain()
```

5. Pass conversation to `_handle_command` so `/note` can use history context. Update the call in `_handle_connection`:

Change:
```python
                elif isinstance(msg, CommandMessage):
                    await self._handle_command(msg, writer)
```
To:
```python
                elif isinstance(msg, CommandMessage):
                    await self._handle_command(msg, conv, writer)
```

And update the `_handle_command` signature:
```python
    async def _handle_command(
        self,
        msg: CommandMessage,
        conv: Conversation,
        writer: asyncio.StreamWriter,
    ) -> None:
```

(The `conv` parameter is available for future use — `/note` uses its own prompt for now.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_wiki_commands.py -v`
Expected: all 4 tests PASS

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest tests/ -v`
Expected: all tests pass. Note: the existing `test_daemon.py` tests use `running_daemon` which creates a `Daemon` — this should still work because `WikiManager.init_vault()` creates the vault dir at the configured path.

- [ ] **Step 6: Commit**

```bash
git add pal/daemon.py tests/test_wiki_commands.py
git commit -m "feat: wire wiki commands into daemon — /note, /read, /lint, /status with vault info"
```

---

### Task 7: CLI — Add /exit Command

**Files:**
- Modify: `pal/cli.py`

- [ ] **Step 1: Add /exit as a quit alias in the CLI**

In `pal/cli.py`, change:
```python
                if cmd_name == "quit":
                    break
```
To:
```python
                if cmd_name in ("quit", "exit"):
                    break
```

- [ ] **Step 2: Update the help text**

In `pal/cli.py`, change:
```python
    console.print("[dim]Type /quit to exit, /status for daemon info[/dim]\n")
```
To:
```python
    console.print("[dim]Type /quit or /exit to exit, /status for daemon info[/dim]\n")
```

- [ ] **Step 3: Commit**

```bash
git add pal/cli.py
git commit -m "feat: add /exit as alias for /quit in CLI"
```

---

### Task 8: Integration Test — Full Wiki Workflow

**Files:**
- Modify: `tests/test_wiki_commands.py`

- [ ] **Step 1: Add a full workflow test**

Append to `tests/test_wiki_commands.py`:
```python
@pytest.mark.asyncio
async def test_full_wiki_workflow(wiki_daemon, socket_path, vault_path):
    """Full workflow: create article, read it back, lint, check status."""
    client = PalClient(socket_path)
    await client.connect()

    # 1. Create an article via /note
    tokens = []
    async for msg in client.chat("/note Test Topic"):
        if isinstance(msg, StreamChunkMessage):
            tokens.append(msg.token)
        elif isinstance(msg, ResponseMessage):
            break
    assert len("".join(tokens)) > 0

    # 2. Check status shows the vault
    resp = await client.command("status")
    assert "vault" in resp.text.lower()

    # 3. Lint should pass on a well-formed vault
    resp = await client.command("lint")
    # May have issues or be clean — just verify it doesn't error
    assert resp.text

    await client.close()
```

- [ ] **Step 2: Run the full test suite**

Run: `python -m pytest tests/ -v`
Expected: all tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_wiki_commands.py
git commit -m "test: full wiki workflow integration test"
```

---

### Task 9: Final Verification

- [ ] **Step 1: Run the complete test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: all tests pass

- [ ] **Step 2: Verify /note works end-to-end conceptually**

Review that `/note` flow is:
1. User types `/note <topic>` in CLI
2. CLI parses as slash command, but since it's not `quit`/`exit`, sends as `CommandMessage(name="note", args="<topic>")`
3. Wait — actually `/note` in the current CLI sends it as a command to the daemon. But in `_handle_note` it streams back `StreamChunkMessage` tokens. The `client.command()` method only reads `ResponseMessage`/`ErrorMessage`. So `/note` needs to go through `client.chat()` or the CLI needs to handle streaming for commands.

This is a design issue: `/note` streams tokens (like chat) but is triggered as a command. The fix: in the CLI, route `/note` through `client.chat()` instead of `client.command()`. Update `pal/cli.py`:

Change the slash command block to:
```python
            # Parse slash commands
            if text.startswith("/"):
                parts = text[1:].split(None, 1)
                cmd_name = parts[0]
                cmd_args = parts[1] if len(parts) > 1 else ""

                if cmd_name in ("quit", "exit"):
                    break

                # Commands that stream responses (like chat)
                if cmd_name == "note":
                    accumulated = ""
                    console.print()
                    with Live(Markdown(""), console=console, refresh_per_second=10) as live:
                        async for msg in client.chat(text):
                            if isinstance(msg, StreamChunkMessage):
                                accumulated += msg.token
                                live.update(Markdown(accumulated))
                            elif isinstance(msg, ErrorMessage):
                                console.print(f"[red]{msg.error}[/red]")
                                break
                            elif isinstance(msg, ResponseMessage):
                                break
                    console.print()
                    continue

                try:
                    resp = await client.command(cmd_name, cmd_args)
                    console.print(f"\n{resp.text}\n")
                except RuntimeError as exc:
                    console.print(f"\n[red]{exc}[/red]\n")
                continue
```

Wait — but `client.chat()` sends a `ChatMessage`, not a `CommandMessage`. For `/note` to work, the daemon needs to receive it as a `CommandMessage`. So the CLI should use `client.command()` for commands, and the daemon's `/note` handler should NOT stream — it should handle the inference internally and return a `ResponseMessage` with the result.

Actually, the simplest fix: change `_handle_note` to NOT stream to the client. It calls inference, collects the full response, writes to vault, then sends one `ResponseMessage` back. This means `/note` behaves like other commands — client sends command, gets response.

Replace `_handle_note` in `pal/daemon.py`:
```python
    async def _handle_note(
        self,
        topic: str,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle /note <topic> — create or update a wiki article."""
        topic = topic.strip()
        if not topic:
            error = ErrorMessage(error="Usage: /note <topic>")
            writer.write(encode_message(error))
            await writer.drain()
            return

        prompt = (
            f"Write a concise wiki article about: {topic}\n\n"
            "Format: Start with a markdown heading, then clear explanatory paragraphs. "
            "Be informative and concise."
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        try:
            body = await self.inference.complete(messages)
        except Exception as exc:
            logger.exception("Inference error during /note: %s", exc)
            error = ErrorMessage(error=f"Inference error: {exc}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        slug = topic.lower().replace(" ", "-")
        slug = "".join(c for c in slug if c.isalnum() or c == "-")
        path = f"{slug}.md"

        self.wiki.write_article(path=path, title=topic, body=body + "\n")
        self.wiki.rebuild_index()
        self.wiki.git_init()
        self.wiki.git_commit(f"note: {topic}")

        resp = ResponseMessage(
            text=f"Created article: {path}\n\n{body}",
            command="note",
        )
        writer.write(encode_message(resp))
        await writer.drain()
```

- [ ] **Step 3: Update the /note test to use client.command()**

In `tests/test_wiki_commands.py`, change `test_note_command_creates_article`:
```python
@pytest.mark.asyncio
async def test_note_command_creates_article(wiki_daemon, socket_path, vault_path):
    """The /note command creates a wiki article via inference."""
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("note", "My Test Topic")
    assert "Created article:" in resp.text

    await client.close()
```

And update `test_full_wiki_workflow`:
```python
@pytest.mark.asyncio
async def test_full_wiki_workflow(wiki_daemon, socket_path, vault_path):
    """Full workflow: create article, read it back, lint, check status."""
    client = PalClient(socket_path)
    await client.connect()

    # 1. Create an article via /note
    resp = await client.command("note", "Test Topic")
    assert "Created article:" in resp.text

    # 2. Check status shows the vault
    resp = await client.command("status")
    assert "vault" in resp.text.lower()

    # 3. Lint should pass on a well-formed vault
    resp = await client.command("lint")
    assert resp.text

    await client.close()
```

- [ ] **Step 4: Run the full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: all tests pass

- [ ] **Step 5: Commit if any fixes were made**

```bash
git add pal/daemon.py pal/cli.py tests/test_wiki_commands.py
git commit -m "fix: /note uses non-streaming response, /exit alias in CLI"
```
