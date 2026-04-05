# PAL Phase 6: Learning & Ratings — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let PAL extract lessons from conversations, store them as markdown in `_learning/`, and allow the user to promote valuable learnings to wisdom. Plus a simple feedback log for session ratings.

**Architecture:** A `LearningManager` class owns `_learning/` in the vault — add/list/get/remove/promote operations on markdown files with YAML frontmatter. `/learn` feeds the current conversation history to the local model and asks it to extract actionable lessons. `/promote` copies a learning to `_wisdom/` and marks it promoted. `/rate` writes a timestamped entry to `_learning/ratings.md` (append-only log). All storage is markdown, visible in Obsidian, git-tracked.

**Tech Stack:** Python 3.12, existing PAL modules (daemon, conversation, inference, wisdom, frontmatter, prompt_builder, wiki)

---

## File Structure

```
pal/
├── learning.py          # LearningManager — add/list/get/remove/promote learnings
├── daemon.py            # Modified — /learn, /learnings, /promote, /rate commands
├── cli.py               # Modified — help text
tests/
├── test_learning.py     # LearningManager unit tests
├── test_learning_commands.py  # Integration: /learn, /learnings, /promote, /rate
```

**Vault additions:**
- `_learning/*.md` — extracted lessons (one per file, YAML frontmatter)
- `_learning/ratings.md` — append-only session feedback log

---

### Task 1: LearningManager — Add/List/Get/Remove

**Files:**
- Create: `pal/learning.py`
- Create: `tests/test_learning.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_learning.py`:
```python
"""Tests for LearningManager — learning extraction storage."""
from pathlib import Path

import pytest

from pal.learning import LearningManager


@pytest.fixture()
def vault(tmp_path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    return v


@pytest.fixture()
def learning(vault) -> LearningManager:
    return LearningManager(vault)


def test_list_empty(learning):
    assert learning.list() == []


def test_add_creates_file(learning, vault):
    slug = learning.add(
        title="Always test edge cases",
        body="Edge cases reveal assumptions. Test boundaries, empty inputs, and error paths.",
        source="conversation",
    )
    assert slug == "always-test-edge-cases"
    path = vault / "_learning" / "always-test-edge-cases.md"
    assert path.exists()
    content = path.read_text()
    assert "title: Always test edge cases" in content
    assert "Edge cases reveal assumptions" in content
    assert "source: conversation" in content


def test_list_returns_entries(learning):
    learning.add(title="First", body="Body one.", source="conversation")
    learning.add(title="Second", body="Body two.", source="conversation")
    entries = learning.list()
    assert len(entries) == 2
    slugs = [e["slug"] for e in entries]
    assert "first" in slugs
    assert "second" in slugs


def test_get_returns_body(learning):
    learning.add(title="Rule", body="Always check.", source="conversation")
    body = learning.get("rule")
    assert body == "Always check."


def test_get_nonexistent_raises(learning):
    with pytest.raises(FileNotFoundError):
        learning.get("nonexistent")


def test_remove_deletes_file(learning, vault):
    learning.add(title="Temp", body="Will be removed.", source="conversation")
    assert (vault / "_learning" / "temp.md").exists()
    learning.remove("temp")
    assert not (vault / "_learning" / "temp.md").exists()


def test_remove_nonexistent_raises(learning):
    with pytest.raises(FileNotFoundError):
        learning.remove("nope")


def test_add_sanitizes_slug(learning, vault):
    slug = learning.add(title="Hello, World!", body="Test.", source="conversation")
    assert slug == "hello-world"
    assert (vault / "_learning" / "hello-world.md").exists()


def test_add_stores_metadata(learning, vault):
    import yaml
    learning.add(title="Test", body="Body.", source="conversation")
    content = (vault / "_learning" / "test.md").read_text()
    meta = yaml.safe_load(content.split("---")[1])
    assert meta["title"] == "Test"
    assert meta["source"] == "conversation"
    assert "created" in meta
    assert meta["status"] == "active"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_learning.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pal.learning'`

- [ ] **Step 3: Implement learning.py**

`pal/learning.py`:
```python
"""LearningManager — extracted lessons from conversations.

Learnings live in _learning/ within the vault. Each is a short markdown
file with a title, body (the lesson), source, and status. Users can
review learnings and promote valuable ones to wisdom via /promote.
"""
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from pal.frontmatter import parse_frontmatter, serialize_frontmatter

logger = logging.getLogger(__name__)


def _slugify(title: str) -> str:
    slug = title.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "untitled"


class LearningManager:
    def __init__(self, vault_path: Path) -> None:
        self.vault_path = vault_path

    @property
    def learning_dir(self) -> Path:
        return self.vault_path / "_learning"

    def list(self) -> list[dict]:
        """List all learnings, returning dicts with 'slug', 'title', 'status'."""
        if not self.learning_dir.exists():
            return []
        entries = []
        for md_file in sorted(self.learning_dir.glob("*.md")):
            if md_file.stem == "ratings":
                continue
            meta, _ = parse_frontmatter(md_file.read_text())
            entries.append({
                "slug": md_file.stem,
                "title": meta.get("title", md_file.stem),
                "status": meta.get("status", "active"),
            })
        return entries

    def add(self, title: str, body: str, source: str) -> str:
        """Add a new learning. Returns the slug."""
        self.learning_dir.mkdir(parents=True, exist_ok=True)
        slug = _slugify(title)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        meta = {
            "title": title,
            "source": source,
            "created": now,
            "status": "active",
        }
        content = serialize_frontmatter(meta, body if body.endswith("\n") else body + "\n")
        (self.learning_dir / f"{slug}.md").write_text(content)
        logger.info("Added learning: %s", slug)
        return slug

    def get(self, slug: str) -> str:
        """Return the body of a learning by slug."""
        path = self.learning_dir / f"{slug}.md"
        if not path.exists():
            raise FileNotFoundError(f"Learning not found: {slug}")
        _, body = parse_frontmatter(path.read_text())
        return body.strip()

    def remove(self, slug: str) -> None:
        """Delete a learning."""
        path = self.learning_dir / f"{slug}.md"
        if not path.exists():
            raise FileNotFoundError(f"Learning not found: {slug}")
        path.unlink()
        logger.info("Removed learning: %s", slug)

    def mark_promoted(self, slug: str) -> None:
        """Mark a learning as promoted (status → promoted)."""
        path = self.learning_dir / f"{slug}.md"
        if not path.exists():
            raise FileNotFoundError(f"Learning not found: {slug}")
        meta, body = parse_frontmatter(path.read_text())
        meta["status"] = "promoted"
        meta["promoted_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        path.write_text(serialize_frontmatter(meta, body if body.endswith("\n") else body + "\n"))
        logger.info("Marked learning as promoted: %s", slug)

    def add_rating(self, rating: str, comment: str = "") -> None:
        """Append a rating entry to the ratings log."""
        self.learning_dir.mkdir(parents=True, exist_ok=True)
        ratings_path = self.learning_dir / "ratings.md"
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        entry = f"- [{now}] **{rating}**"
        if comment:
            entry += f" — {comment}"
        entry += "\n"
        if not ratings_path.exists():
            ratings_path.write_text(
                "---\ntitle: Session Ratings\n---\n\n# Session Ratings\n\n"
            )
        with open(ratings_path, "a") as f:
            f.write(entry)
        logger.info("Added rating: %s", rating)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_learning.py -v`
Expected: all 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pal/learning.py tests/test_learning.py
git commit -m "feat: LearningManager — add/list/get/remove learnings + ratings log"
```

---

### Task 2: LearningManager — Promote + Rate Tests

**Files:**
- Modify: `tests/test_learning.py` (append tests)

- [ ] **Step 1: Write the failing tests**

APPEND to `tests/test_learning.py`:
```python
from pal.wisdom import WisdomManager


def test_mark_promoted_updates_status(learning, vault):
    learning.add(title="Good Idea", body="This works.", source="conversation")
    learning.mark_promoted("good-idea")
    import yaml
    content = (vault / "_learning" / "good-idea.md").read_text()
    meta = yaml.safe_load(content.split("---")[1])
    assert meta["status"] == "promoted"
    assert "promoted_at" in meta


def test_mark_promoted_nonexistent_raises(learning):
    with pytest.raises(FileNotFoundError):
        learning.mark_promoted("nope")


def test_add_rating(learning, vault):
    learning.add_rating("good", "Great session")
    ratings_path = vault / "_learning" / "ratings.md"
    assert ratings_path.exists()
    content = ratings_path.read_text()
    assert "**good**" in content
    assert "Great session" in content


def test_add_rating_appends(learning, vault):
    learning.add_rating("good", "First")
    learning.add_rating("bad", "Second")
    content = (vault / "_learning" / "ratings.md").read_text()
    assert "**good**" in content
    assert "**bad**" in content
    assert "First" in content
    assert "Second" in content


def test_list_excludes_ratings_file(learning):
    learning.add(title="Real Learning", body="Body.", source="conversation")
    learning.add_rating("good")
    entries = learning.list()
    slugs = [e["slug"] for e in entries]
    assert "ratings" not in slugs
    assert "real-learning" in slugs
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `python -m pytest tests/test_learning.py -v`
Expected: all 14 tests PASS (the implementation from Task 1 already has `mark_promoted`, `add_rating`, and the `ratings` exclusion)

- [ ] **Step 3: Commit**

```bash
git add tests/test_learning.py
git commit -m "test: learning promotion and ratings log tests"
```

---

### Task 3: Wire /learn, /learnings, /promote, /rate into Daemon

**Files:**
- Modify: `pal/daemon.py`
- Create: `tests/test_learning_commands.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_learning_commands.py`:
```python
"""Integration tests for /learn, /learnings, /promote, /rate commands."""
import asyncio

import pytest

from pal.client import PalClient
from pal.config import Config
from pal.daemon import Daemon
from pal.learning import LearningManager
from pal.wisdom import WisdomManager


@pytest.fixture()
async def learn_daemon(socket_path, mock_inference_server, tmp_path):
    cfg = Config(
        inference_url=mock_inference_server,
        model="test-model",
        socket_path=socket_path,
        history_depth=50,
        vault_path=tmp_path / "vault",
        collection_id="vault",
        username="testuser",
        searxng_url=mock_inference_server,
        fetch_max_bytes=2_000_000,
        fetch_timeout=10,
    )
    daemon = Daemon(cfg)
    task = asyncio.create_task(daemon.serve())
    for _ in range(100):
        if socket_path.exists():
            break
        await asyncio.sleep(0.01)
    yield daemon, tmp_path / "vault"
    daemon.shutdown()
    await task


@pytest.mark.asyncio
async def test_learn_extracts_from_conversation(learn_daemon, socket_path, monkeypatch):
    """After chatting, /learn extracts lessons from conversation history."""
    daemon, vault = learn_daemon

    # First have a conversation to give /learn something to work with
    from pal.protocol import StreamChunkMessage, ResponseMessage
    client = PalClient(socket_path)
    await client.connect()

    # Send a chat message so conversation has history
    async for msg in client.chat("How do I handle errors in Python?"):
        if isinstance(msg, ResponseMessage):
            break

    # Now mock inference for the /learn extraction
    async def fake_complete(messages):
        return (
            "## Always handle specific exceptions\n"
            "Catch specific exception types rather than bare except.\n\n"
            "## Use try/finally for cleanup\n"
            "Ensure resources are released even when exceptions occur."
        )
    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    resp = await client.command("learn")
    assert "learning" in resp.text.lower() or "extracted" in resp.text.lower()

    # Learnings should exist in the vault
    learning_files = list((vault / "_learning").glob("*.md"))
    # Exclude ratings.md
    learning_files = [f for f in learning_files if f.stem != "ratings"]
    assert len(learning_files) >= 1

    await client.close()


@pytest.mark.asyncio
async def test_learn_with_empty_conversation(learn_daemon, socket_path):
    """Trying /learn with no conversation history returns an error."""
    daemon, vault = learn_daemon
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="No conversation"):
        await client.command("learn")

    await client.close()


@pytest.mark.asyncio
async def test_learnings_list(learn_daemon, socket_path):
    daemon, vault = learn_daemon
    # Add some learnings directly
    daemon.learning.add(title="Lesson A", body="Body A.", source="conversation")
    daemon.learning.add(title="Lesson B", body="Body B.", source="conversation")

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("learnings")
    assert "Lesson A" in resp.text
    assert "Lesson B" in resp.text
    await client.close()


@pytest.mark.asyncio
async def test_learnings_list_empty(learn_daemon, socket_path):
    daemon, vault = learn_daemon
    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("learnings")
    assert "no learning" in resp.text.lower() or "empty" in resp.text.lower()
    await client.close()


@pytest.mark.asyncio
async def test_promote_moves_to_wisdom(learn_daemon, socket_path):
    daemon, vault = learn_daemon
    daemon.learning.add(title="Good Rule", body="Always validate input.", source="conversation")

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("promote", "good-rule")
    assert "promoted" in resp.text.lower() or "wisdom" in resp.text.lower()
    await client.close()

    # Wisdom should now have this entry
    wm = WisdomManager(vault)
    entries = wm.list()
    assert any("Good Rule" in e["title"] for e in entries)

    # Learning should be marked promoted
    lm = LearningManager(vault)
    entries = lm.list()
    promoted = [e for e in entries if e["slug"] == "good-rule"]
    assert promoted[0]["status"] == "promoted"


@pytest.mark.asyncio
async def test_promote_nonexistent(learn_daemon, socket_path):
    daemon, vault = learn_daemon
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="not found"):
        await client.command("promote", "nonexistent")

    await client.close()


@pytest.mark.asyncio
async def test_rate_good(learn_daemon, socket_path):
    daemon, vault = learn_daemon
    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("rate", "good Great conversation")
    assert "recorded" in resp.text.lower() or "rated" in resp.text.lower()
    await client.close()

    ratings_path = vault / "_learning" / "ratings.md"
    assert ratings_path.exists()
    content = ratings_path.read_text()
    assert "**good**" in content
    assert "Great conversation" in content


@pytest.mark.asyncio
async def test_rate_empty(learn_daemon, socket_path):
    daemon, vault = learn_daemon
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="Usage"):
        await client.command("rate", "")

    await client.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_learning_commands.py -v`
Expected: FAIL — daemon doesn't handle these commands yet

- [ ] **Step 3: Wire into daemon**

In `pal/daemon.py`:

1. Add import:
```python
from pal.learning import LearningManager
```

2. In `Daemon.__init__`, after the existing `self.prompt_builder = ...` block, add:
```python
        self.learning = LearningManager(config.vault_path)
```

3. In `_handle_command`, add four new elif branches BEFORE the final `else:`:
```python
        elif msg.name == "learn":
            await self._handle_learn(conv, writer)
        elif msg.name == "learnings":
            await self._handle_learnings(writer)
        elif msg.name == "promote":
            await self._handle_promote(msg.args, writer)
        elif msg.name == "rate":
            await self._handle_rate(msg.args, writer)
```

4. Add these new methods to the `Daemon` class:
```python
    async def _handle_learn(
        self,
        conv: Conversation,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle /learn — extract lessons from the current conversation."""
        messages = conv.messages
        if not messages:
            error = ErrorMessage(error="No conversation history to learn from.")
            writer.write(encode_message(error))
            await writer.drain()
            return

        # Format conversation for the model
        conv_text = "\n".join(
            f"{'User' if m['role'] == 'user' else 'PAL'}: {m['content']}"
            for m in messages
        )

        prompt = (
            "Review this conversation and extract actionable lessons or insights. "
            "Each lesson should be a concise, reusable principle. "
            "Format each lesson as: ## <title>\\n<body>\\n\\n "
            "Extract 1-3 lessons. If the conversation has no useful lessons, "
            "respond with exactly: NONE\n\n"
            f"Conversation:\n{conv_text}"
        )

        api_messages = [
            {"role": "system", "content": self.prompt_builder.build()},
            {"role": "user", "content": prompt},
        ]

        try:
            result = await self.inference.complete(api_messages)
        except Exception as exc:
            logger.exception("Learn inference failed: %s", exc)
            error = ErrorMessage(error=f"Learn failed: {exc}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        if result.strip() == "NONE":
            resp = ResponseMessage(
                text="No actionable lessons found in this conversation.",
                command="learn",
            )
            writer.write(encode_message(resp))
            await writer.drain()
            return

        # Parse ## Title / body pairs
        import re
        sections = re.split(r"^## ", result, flags=re.MULTILINE)
        added = []
        for section in sections:
            section = section.strip()
            if not section:
                continue
            lines = section.split("\n", 1)
            title = lines[0].strip()
            body = lines[1].strip() if len(lines) > 1 else title
            slug = self.learning.add(title=title, body=body, source="conversation")
            added.append(slug)

        if not added:
            resp = ResponseMessage(
                text="Could not parse lessons from model output.",
                command="learn",
            )
        else:
            lines = [f"Extracted {len(added)} learning(s):\n"]
            for slug in added:
                lines.append(f"- {slug}")
            lines.append("\nUse `/learnings` to list, `/promote <slug>` to promote to wisdom.")
            resp = ResponseMessage(text="\n".join(lines), command="learn")

        self.wiki.git_init()
        self.wiki.git_commit(f"learn: extracted {len(added)} lesson(s)")

        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_learnings(self, writer: asyncio.StreamWriter) -> None:
        """Handle /learnings — list all extracted learnings."""
        entries = self.learning.list()
        if not entries:
            resp = ResponseMessage(
                text="No learnings yet. Use `/learn` after a conversation to extract lessons.",
                command="learnings",
            )
        else:
            lines = [f"{len(entries)} learning(s):\n"]
            for e in entries:
                status_marker = " (promoted)" if e["status"] == "promoted" else ""
                lines.append(f"- **{e['title']}** ({e['slug']}){status_marker}")
            resp = ResponseMessage(text="\n".join(lines), command="learnings")
        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_promote(self, slug: str, writer: asyncio.StreamWriter) -> None:
        """Handle /promote <slug> — promote a learning to wisdom."""
        slug = slug.strip()
        if not slug:
            error = ErrorMessage(error="Usage: /promote <slug>")
            writer.write(encode_message(error))
            await writer.drain()
            return
        try:
            body = self.learning.get(slug)
            meta_path = self.learning.learning_dir / f"{slug}.md"
            from pal.frontmatter import parse_frontmatter
            meta, _ = parse_frontmatter(meta_path.read_text())
            title = meta.get("title", slug)
        except FileNotFoundError:
            error = ErrorMessage(error=f"Learning not found: {slug}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        self.wisdom.add(title=title, body=body)
        self.learning.mark_promoted(slug)

        self.wiki.git_init()
        self.wiki.git_commit(f"promote: {slug} → wisdom")

        resp = ResponseMessage(
            text=f"Promoted **{title}** to wisdom.",
            command="promote",
        )
        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_rate(self, args: str, writer: asyncio.StreamWriter) -> None:
        """Handle /rate <good|bad> [comment] — record session feedback."""
        args = args.strip()
        if not args:
            error = ErrorMessage(error="Usage: /rate <good|bad> [comment]")
            writer.write(encode_message(error))
            await writer.drain()
            return
        parts = args.split(None, 1)
        rating = parts[0].lower()
        comment = parts[1] if len(parts) > 1 else ""

        self.learning.add_rating(rating, comment)

        resp = ResponseMessage(
            text=f"Rated: **{rating}**" + (f" — {comment}" if comment else ""),
            command="rate",
        )
        writer.write(encode_message(resp))
        await writer.drain()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_learning_commands.py -v`
Expected: all 8 tests PASS

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add pal/daemon.py tests/test_learning_commands.py
git commit -m "feat: /learn /learnings /promote /rate — learning extraction and promotion"
```

---

### Task 4: CLI Help Update + Final Verification

**Files:**
- Modify: `pal/cli.py`

- [ ] **Step 1: Update CLI help text**

In `pal/cli.py`, find:
```python
    console.print("[dim]Commands: /note /read /search /get /search-web /fetch /summarize /compile /profile /wisdom /lint /status /quit[/dim]\n")
```

Replace with:
```python
    console.print("[dim]Commands: /note /read /search /get /search-web /fetch /summarize /compile /learn /learnings /promote /rate /profile /wisdom /lint /status /quit[/dim]\n")
```

- [ ] **Step 2: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: all tests pass

- [ ] **Step 3: Commit**

```bash
git add pal/cli.py
git commit -m "docs: update CLI help with learning and rating commands"
```
