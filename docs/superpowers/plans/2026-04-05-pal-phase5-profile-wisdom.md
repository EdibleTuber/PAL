# PAL Phase 5: Profile & Wisdom Injection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give PAL persistent memory about who the user is (profile) and curated guidance (wisdom) that automatically gets injected into the system prompt on every chat, so responses are grounded in user context.

**Architecture:** Two new manager classes (`ProfileManager`, `WisdomManager`) that own markdown files in `_profile/` and `_wisdom/` within the vault. A `SystemPromptBuilder` composes the base prompt plus profile sections plus wisdom entries at chat time. The daemon switches from a static SYSTEM_PROMPT constant to calling the builder per chat. New slash commands let the user view/edit profile and list/add/remove wisdom entries.

**Tech Stack:** Python 3.12, existing PAL modules (wiki, frontmatter, daemon), markdown files with YAML frontmatter (existing pattern)

---

## File Structure

```
pal/
├── profile.py              # ProfileManager — read/write _profile/edible.md with sections
├── wisdom.py               # WisdomManager — list/add/remove/get _wisdom/*.md entries
├── prompt_builder.py       # SystemPromptBuilder — composes base prompt + profile + wisdom
├── daemon.py               # Modified — use prompt_builder, add /profile and /wisdom commands
tests/
├── test_profile.py         # ProfileManager tests
├── test_wisdom.py          # WisdomManager tests
├── test_prompt_builder.py  # SystemPromptBuilder tests
├── test_profile_commands.py # Integration: /profile via daemon
├── test_wisdom_commands.py  # Integration: /wisdom via daemon
```

---

## Data Format

**Profile file** (`_profile/edible.md`):
```markdown
---
title: User Profile
updated: 2026-04-05T12:00:00+00:00
---

## World
Facts about the user's environment, tools, systems.

## Bio
Biographical information.

## Opinions
User's stated preferences and opinions.
```

**Wisdom entry** (`_wisdom/example.md`):
```markdown
---
title: Keep responses concise
confidence: high
created: 2026-04-05T12:00:00+00:00
---

When responding to questions, lead with the answer before any explanation.
```

**Injected prompt structure:**
```
<base system prompt>

## About the User
<profile body>

## Active Wisdom
- <wisdom 1 body>
- <wisdom 2 body>
```

---

### Task 1: ProfileManager — Read/Write User Profile

**Files:**
- Create: `pal/profile.py`
- Create: `tests/test_profile.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_profile.py`:
```python
"""Tests for ProfileManager — user profile read/write."""
from pathlib import Path

import pytest

from pal.profile import ProfileManager


@pytest.fixture()
def vault(tmp_path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    return v


@pytest.fixture()
def profile(vault) -> ProfileManager:
    return ProfileManager(vault, username="edible")


def test_profile_starts_empty(profile, vault):
    body = profile.read()
    assert body == ""


def test_profile_write_creates_file(profile, vault):
    profile.write("## World\n\nI run an inference server.\n")
    path = vault / "_profile" / "edible.md"
    assert path.exists()
    content = path.read_text()
    assert "I run an inference server." in content
    assert "title: User Profile" in content


def test_profile_read_after_write(profile, vault):
    profile.write("## Bio\n\nSoftware engineer.\n")
    body = profile.read()
    assert "Software engineer." in body


def test_profile_write_updates_timestamp(profile, vault):
    profile.write("## World\n\nFirst version.\n")
    first = (vault / "_profile" / "edible.md").read_text()
    profile.write("## World\n\nSecond version.\n")
    second = (vault / "_profile" / "edible.md").read_text()
    assert first != second
    assert "Second version." in second
    assert "First version." not in second


def test_profile_directory_created_automatically(profile, vault):
    assert not (vault / "_profile").exists()
    profile.write("## Bio\n\nHi.\n")
    assert (vault / "_profile").is_dir()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_profile.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pal.profile'`

- [ ] **Step 3: Implement profile.py**

`pal/profile.py`:
```python
"""ProfileManager — read and write the user's profile.

The profile lives in _profile/<username>.md within the vault. It contains
world facts, biographical notes, and opinions that get injected into
PAL's system prompt on every chat.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

from pal.frontmatter import parse_frontmatter, serialize_frontmatter

logger = logging.getLogger(__name__)


class ProfileManager:
    def __init__(self, vault_path: Path, username: str) -> None:
        self.vault_path = vault_path
        self.username = username

    @property
    def profile_path(self) -> Path:
        return self.vault_path / "_profile" / f"{self.username}.md"

    def read(self) -> str:
        """Return the profile body, or empty string if not yet written."""
        if not self.profile_path.exists():
            return ""
        _, body = parse_frontmatter(self.profile_path.read_text())
        return body.strip()

    def write(self, body: str) -> None:
        """Overwrite the profile with the given body."""
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        meta = {"title": "User Profile", "updated": now}
        content = serialize_frontmatter(meta, body if body.endswith("\n") else body + "\n")
        self.profile_path.write_text(content)
        logger.info("Wrote profile for %s", self.username)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_profile.py -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pal/profile.py tests/test_profile.py
git commit -m "feat: ProfileManager — read/write user profile"
```

---

### Task 2: WisdomManager — List/Add/Remove Wisdom Entries

**Files:**
- Create: `pal/wisdom.py`
- Create: `tests/test_wisdom.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_wisdom.py`:
```python
"""Tests for WisdomManager — list/add/remove wisdom entries."""
from pathlib import Path

import pytest

from pal.wisdom import WisdomManager


@pytest.fixture()
def vault(tmp_path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    return v


@pytest.fixture()
def wisdom(vault) -> WisdomManager:
    return WisdomManager(vault)


def test_list_empty(wisdom):
    assert wisdom.list() == []


def test_add_entry_creates_file(wisdom, vault):
    slug = wisdom.add(title="Be concise", body="Lead with the answer.")
    assert slug == "be-concise"
    path = vault / "_wisdom" / "be-concise.md"
    assert path.exists()
    content = path.read_text()
    assert "title: Be concise" in content
    assert "Lead with the answer." in content


def test_list_returns_entries(wisdom):
    wisdom.add(title="First", body="Body one.")
    wisdom.add(title="Second", body="Body two.")
    entries = wisdom.list()
    assert len(entries) == 2
    slugs = [e["slug"] for e in entries]
    assert "first" in slugs
    assert "second" in slugs
    titles = [e["title"] for e in entries]
    assert "First" in titles
    assert "Second" in titles


def test_get_returns_body(wisdom):
    wisdom.add(title="Rule", body="Always measure twice.")
    body = wisdom.get("rule")
    assert body == "Always measure twice."


def test_get_nonexistent_raises(wisdom):
    with pytest.raises(FileNotFoundError):
        wisdom.get("nonexistent")


def test_remove_deletes_file(wisdom, vault):
    wisdom.add(title="Temp", body="Will be removed.")
    assert (vault / "_wisdom" / "temp.md").exists()
    wisdom.remove("temp")
    assert not (vault / "_wisdom" / "temp.md").exists()


def test_remove_nonexistent_raises(wisdom):
    with pytest.raises(FileNotFoundError):
        wisdom.remove("nope")


def test_bodies_returns_all(wisdom):
    wisdom.add(title="One", body="First lesson.")
    wisdom.add(title="Two", body="Second lesson.")
    bodies = wisdom.bodies()
    assert len(bodies) == 2
    assert "First lesson." in bodies
    assert "Second lesson." in bodies


def test_add_sanitizes_slug(wisdom, vault):
    slug = wisdom.add(title="Hello, World!", body="Test.")
    assert slug == "hello-world"
    assert (vault / "_wisdom" / "hello-world.md").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_wisdom.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pal.wisdom'`

- [ ] **Step 3: Implement wisdom.py**

`pal/wisdom.py`:
```python
"""WisdomManager — curated guidance entries injected into the system prompt.

Wisdom entries live in _wisdom/ within the vault. Each entry is a short
markdown file with a title and a body (the actual guidance). Entries are
small, focused, and human-editable.
"""
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from pal.frontmatter import parse_frontmatter, serialize_frontmatter

logger = logging.getLogger(__name__)


def _slugify(title: str) -> str:
    """Convert a title into a filesystem-safe slug."""
    slug = title.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "untitled"


class WisdomManager:
    def __init__(self, vault_path: Path) -> None:
        self.vault_path = vault_path

    @property
    def wisdom_dir(self) -> Path:
        return self.vault_path / "_wisdom"

    def list(self) -> list[dict]:
        """List all wisdom entries, returning dicts with 'slug' and 'title'."""
        if not self.wisdom_dir.exists():
            return []
        entries = []
        for md_file in sorted(self.wisdom_dir.glob("*.md")):
            meta, _ = parse_frontmatter(md_file.read_text())
            entries.append({
                "slug": md_file.stem,
                "title": meta.get("title", md_file.stem),
            })
        return entries

    def add(self, title: str, body: str) -> str:
        """Add a new wisdom entry. Returns the slug."""
        self.wisdom_dir.mkdir(parents=True, exist_ok=True)
        slug = _slugify(title)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        meta = {"title": title, "created": now}
        content = serialize_frontmatter(meta, body if body.endswith("\n") else body + "\n")
        (self.wisdom_dir / f"{slug}.md").write_text(content)
        logger.info("Added wisdom: %s", slug)
        return slug

    def get(self, slug: str) -> str:
        """Return the body of a wisdom entry by slug."""
        path = self.wisdom_dir / f"{slug}.md"
        if not path.exists():
            raise FileNotFoundError(f"Wisdom not found: {slug}")
        _, body = parse_frontmatter(path.read_text())
        return body.strip()

    def remove(self, slug: str) -> None:
        """Delete a wisdom entry."""
        path = self.wisdom_dir / f"{slug}.md"
        if not path.exists():
            raise FileNotFoundError(f"Wisdom not found: {slug}")
        path.unlink()
        logger.info("Removed wisdom: %s", slug)

    def bodies(self) -> list[str]:
        """Return all wisdom entry bodies, for injection into prompts."""
        if not self.wisdom_dir.exists():
            return []
        bodies = []
        for md_file in sorted(self.wisdom_dir.glob("*.md")):
            _, body = parse_frontmatter(md_file.read_text())
            bodies.append(body.strip())
        return bodies
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_wisdom.py -v`
Expected: all 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pal/wisdom.py tests/test_wisdom.py
git commit -m "feat: WisdomManager — add/list/get/remove wisdom entries"
```

---

### Task 3: SystemPromptBuilder

**Files:**
- Create: `pal/prompt_builder.py`
- Create: `tests/test_prompt_builder.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_prompt_builder.py`:
```python
"""Tests for SystemPromptBuilder — compose system prompt from base + profile + wisdom."""
from pathlib import Path

import pytest

from pal.profile import ProfileManager
from pal.prompt_builder import SystemPromptBuilder, BASE_PROMPT
from pal.wisdom import WisdomManager


@pytest.fixture()
def vault(tmp_path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    return v


@pytest.fixture()
def builder(vault) -> SystemPromptBuilder:
    profile = ProfileManager(vault, username="edible")
    wisdom = WisdomManager(vault)
    return SystemPromptBuilder(profile=profile, wisdom=wisdom)


def test_build_with_no_profile_or_wisdom(builder):
    prompt = builder.build()
    assert prompt == BASE_PROMPT


def test_build_includes_profile(builder, vault):
    profile = ProfileManager(vault, username="edible")
    profile.write("## World\n\nLinux user.\n")
    result = builder.build()
    assert BASE_PROMPT in result
    assert "## About the User" in result
    assert "Linux user." in result


def test_build_includes_wisdom(builder, vault):
    wisdom = WisdomManager(vault)
    wisdom.add(title="Concise", body="Lead with the answer.")
    wisdom.add(title="Accurate", body="Verify claims.")
    result = builder.build()
    assert BASE_PROMPT in result
    assert "## Active Wisdom" in result
    assert "Lead with the answer." in result
    assert "Verify claims." in result


def test_build_includes_both(builder, vault):
    ProfileManager(vault, username="edible").write("## Bio\n\nEngineer.\n")
    WisdomManager(vault).add(title="Rule", body="Measure twice.")
    result = builder.build()
    assert "## About the User" in result
    assert "Engineer." in result
    assert "## Active Wisdom" in result
    assert "Measure twice." in result


def test_build_sections_ordered(builder, vault):
    ProfileManager(vault, username="edible").write("## Bio\n\nEngineer.\n")
    WisdomManager(vault).add(title="Rule", body="Measure twice.")
    result = builder.build()
    base_idx = result.find(BASE_PROMPT)
    profile_idx = result.find("## About the User")
    wisdom_idx = result.find("## Active Wisdom")
    assert base_idx < profile_idx < wisdom_idx
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_prompt_builder.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pal.prompt_builder'`

- [ ] **Step 3: Implement prompt_builder.py**

`pal/prompt_builder.py`:
```python
"""SystemPromptBuilder — compose system prompt from base + profile + wisdom.

The base prompt establishes PAL's identity. Profile and wisdom are appended
dynamically so PAL has fresh user context on every chat turn.
"""
from pal.profile import ProfileManager
from pal.wisdom import WisdomManager


BASE_PROMPT = (
    "You are PAL, a personal AI librarian and conversational companion. "
    "You help the user think, answer questions, and manage knowledge. "
    "Be concise, direct, and helpful."
)


class SystemPromptBuilder:
    def __init__(self, profile: ProfileManager, wisdom: WisdomManager) -> None:
        self.profile = profile
        self.wisdom = wisdom

    def build(self) -> str:
        """Compose the current system prompt from base + profile + wisdom."""
        sections = [BASE_PROMPT]

        profile_body = self.profile.read()
        if profile_body:
            sections.append(f"## About the User\n\n{profile_body}")

        wisdom_bodies = self.wisdom.bodies()
        if wisdom_bodies:
            wisdom_text = "\n".join(f"- {body}" for body in wisdom_bodies)
            sections.append(f"## Active Wisdom\n\n{wisdom_text}")

        return "\n\n".join(sections)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_prompt_builder.py -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pal/prompt_builder.py tests/test_prompt_builder.py
git commit -m "feat: SystemPromptBuilder — compose prompt with profile and wisdom"
```

---

### Task 4: Add username to Config

**Files:**
- Modify: `pal/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

APPEND to `tests/test_config.py`:
```python
def test_default_config_has_username():
    cfg = Config()
    assert cfg.username == "user"


def test_load_config_username_from_env(monkeypatch):
    monkeypatch.setenv("PAL_USERNAME", "edible")
    for key in ["PAL_INFERENCE_URL", "PAL_MODEL", "PAL_SOCKET_PATH", "PAL_HISTORY_DEPTH", "PAL_VAULT_PATH", "PAL_COLLECTION_ID"]:
        monkeypatch.delenv(key, raising=False)
    cfg = load_config()
    assert cfg.username == "edible"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py::test_default_config_has_username -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'username'`

- [ ] **Step 3: Add username to Config**

In `pal/config.py`, add to the `Config` dataclass:
```python
    username: str = "user"
```

And in `load_config()`, add at the end (before `return`):
```python
    if user := os.environ.get("PAL_USERNAME"):
        kwargs["username"] = user
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: all tests in test_config.py PASS

- [ ] **Step 5: Commit**

```bash
git add pal/config.py tests/test_config.py
git commit -m "feat: add username config setting for profile"
```

---

### Task 5: Wire Profile/Wisdom/Builder into Daemon

**Files:**
- Modify: `pal/daemon.py`

- [ ] **Step 1: Update imports**

Add to `pal/daemon.py` imports (with other `from pal.*` imports):
```python
from pal.profile import ProfileManager
from pal.wisdom import WisdomManager
from pal.prompt_builder import SystemPromptBuilder
```

- [ ] **Step 2: Remove the SYSTEM_PROMPT constant**

In `pal/daemon.py`, remove the module-level:
```python
SYSTEM_PROMPT = (
    "You are PAL, a personal AI librarian and conversational companion. "
    "You help the user think, answer questions, and manage knowledge. "
    "Be concise, direct, and helpful."
)
```

- [ ] **Step 3: Instantiate managers in `Daemon.__init__`**

In `Daemon.__init__`, after the existing `self.retrieval = RetrievalClient(...)` block, add:
```python
        self.profile = ProfileManager(config.vault_path, username=config.username)
        self.wisdom = WisdomManager(config.vault_path)
        self.prompt_builder = SystemPromptBuilder(
            profile=self.profile,
            wisdom=self.wisdom,
        )
```

- [ ] **Step 4: Use the builder in `_handle_chat`**

In `pal/daemon.py`, find `_handle_chat`. Change:
```python
        conv.add_user(msg.text)
        messages = conv.get_messages_for_api(system_prompt=SYSTEM_PROMPT)
```
To:
```python
        conv.add_user(msg.text)
        messages = conv.get_messages_for_api(system_prompt=self.prompt_builder.build())
```

- [ ] **Step 5: Update `_handle_note` to use the builder**

In `_handle_note`, change:
```python
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
```
To:
```python
        messages = [
            {"role": "system", "content": self.prompt_builder.build()},
            {"role": "user", "content": prompt},
        ]
```

- [ ] **Step 6: Run the full test suite to verify nothing broke**

Run: `python -m pytest tests/ -v`
Expected: all existing tests pass (prompt injection is transparent to existing tests)

- [ ] **Step 7: Commit**

```bash
git add pal/daemon.py
git commit -m "feat: inject profile and wisdom into system prompt on every chat"
```

---

### Task 6: Add /profile Command

**Files:**
- Modify: `pal/daemon.py`
- Create: `tests/test_profile_commands.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_profile_commands.py`:
```python
"""Integration tests for /profile slash command via the daemon."""
import asyncio

import pytest

from pal.client import PalClient
from pal.config import Config
from pal.daemon import Daemon
from pal.profile import ProfileManager


@pytest.fixture()
async def profile_daemon(socket_path, mock_inference_server, tmp_path):
    cfg = Config(
        inference_url=mock_inference_server,
        model="test-model",
        socket_path=socket_path,
        history_depth=50,
        vault_path=tmp_path / "vault",
        collection_id="vault",
        username="testuser",
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
async def test_profile_show_empty(profile_daemon, socket_path):
    """/profile with no args shows the current profile (empty by default)."""
    daemon, vault = profile_daemon
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("profile", "")
    assert "empty" in resp.text.lower() or "no profile" in resp.text.lower()

    await client.close()


@pytest.mark.asyncio
async def test_profile_set_and_show(profile_daemon, socket_path):
    """/profile set <text> writes the profile, then /profile shows it."""
    daemon, vault = profile_daemon
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("profile", "set ## Bio\n\nSoftware engineer.")
    assert "updated" in resp.text.lower() or "saved" in resp.text.lower()

    resp = await client.command("profile", "")
    assert "Software engineer." in resp.text

    await client.close()


@pytest.mark.asyncio
async def test_profile_persists_on_disk(profile_daemon, socket_path):
    """Profile writes reach the vault filesystem."""
    daemon, vault = profile_daemon
    client = PalClient(socket_path)
    await client.connect()

    await client.command("profile", "set ## World\n\nLinux user.")

    profile_path = vault / "_profile" / "testuser.md"
    assert profile_path.exists()
    content = profile_path.read_text()
    assert "Linux user." in content

    await client.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_profile_commands.py -v`
Expected: FAIL — daemon doesn't handle /profile yet

- [ ] **Step 3: Add /profile handler to daemon**

In `_handle_command`, add this elif branch before the final `else:`:
```python
        elif msg.name == "profile":
            await self._handle_profile(msg.args, writer)
```

Add this new method to the `Daemon` class:
```python
    async def _handle_profile(self, args: str, writer: asyncio.StreamWriter) -> None:
        """Handle /profile [set <text>] — show or update user profile.

        Usage:
          /profile             — show current profile
          /profile set <text>  — replace profile with <text>
        """
        args = args.strip()
        if args.startswith("set "):
            body = args[4:].strip()
            if not body:
                error = ErrorMessage(error="Usage: /profile set <text>")
                writer.write(encode_message(error))
                await writer.drain()
                return
            self.profile.write(body)
            resp = ResponseMessage(text="Profile updated.", command="profile")
            writer.write(encode_message(resp))
            await writer.drain()
            return
        # Default: show current profile
        body = self.profile.read()
        if not body:
            resp = ResponseMessage(
                text="Profile is empty. Use `/profile set <text>` to set it.",
                command="profile",
            )
        else:
            resp = ResponseMessage(text=body, command="profile")
        writer.write(encode_message(resp))
        await writer.drain()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_profile_commands.py -v`
Expected: all 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add pal/daemon.py tests/test_profile_commands.py
git commit -m "feat: /profile command — show and update user profile"
```

---

### Task 7: Add /wisdom Command

**Files:**
- Modify: `pal/daemon.py`
- Create: `tests/test_wisdom_commands.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_wisdom_commands.py`:
```python
"""Integration tests for /wisdom slash command via the daemon."""
import asyncio

import pytest

from pal.client import PalClient
from pal.config import Config
from pal.daemon import Daemon


@pytest.fixture()
async def wisdom_daemon(socket_path, mock_inference_server, tmp_path):
    cfg = Config(
        inference_url=mock_inference_server,
        model="test-model",
        socket_path=socket_path,
        history_depth=50,
        vault_path=tmp_path / "vault",
        collection_id="vault",
        username="testuser",
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
async def test_wisdom_list_empty(wisdom_daemon, socket_path):
    daemon, vault = wisdom_daemon
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("wisdom", "")
    assert "no wisdom" in resp.text.lower() or "empty" in resp.text.lower()

    await client.close()


@pytest.mark.asyncio
async def test_wisdom_add_and_list(wisdom_daemon, socket_path):
    daemon, vault = wisdom_daemon
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("wisdom", "add Be Concise | Lead with the answer.")
    assert "added" in resp.text.lower()
    assert "be-concise" in resp.text.lower() or "Be Concise" in resp.text

    resp = await client.command("wisdom", "")
    assert "Be Concise" in resp.text

    await client.close()


@pytest.mark.asyncio
async def test_wisdom_remove(wisdom_daemon, socket_path):
    daemon, vault = wisdom_daemon
    client = PalClient(socket_path)
    await client.connect()

    await client.command("wisdom", "add Temp Rule | This will be removed.")
    resp = await client.command("wisdom", "remove temp-rule")
    assert "removed" in resp.text.lower()

    resp = await client.command("wisdom", "")
    assert "Temp Rule" not in resp.text

    await client.close()


@pytest.mark.asyncio
async def test_wisdom_add_invalid_format(wisdom_daemon, socket_path):
    daemon, vault = wisdom_daemon
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="Usage"):
        await client.command("wisdom", "add no-separator-here")

    await client.close()


@pytest.mark.asyncio
async def test_wisdom_remove_nonexistent(wisdom_daemon, socket_path):
    daemon, vault = wisdom_daemon
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="not found"):
        await client.command("wisdom", "remove nonexistent")

    await client.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_wisdom_commands.py -v`
Expected: FAIL — daemon doesn't handle /wisdom yet

- [ ] **Step 3: Add /wisdom handler to daemon**

In `_handle_command`, add this elif branch before the final `else:`:
```python
        elif msg.name == "wisdom":
            await self._handle_wisdom(msg.args, writer)
```

Add this new method to the `Daemon` class:
```python
    async def _handle_wisdom(self, args: str, writer: asyncio.StreamWriter) -> None:
        """Handle /wisdom [add <title> | <body>] [remove <slug>] — manage wisdom.

        Usage:
          /wisdom                          — list all wisdom entries
          /wisdom add <title> | <body>     — add a new entry
          /wisdom remove <slug>            — remove an entry by slug
        """
        args = args.strip()

        if args.startswith("add "):
            rest = args[4:].strip()
            if "|" not in rest:
                error = ErrorMessage(error="Usage: /wisdom add <title> | <body>")
                writer.write(encode_message(error))
                await writer.drain()
                return
            title, body = rest.split("|", 1)
            title = title.strip()
            body = body.strip()
            if not title or not body:
                error = ErrorMessage(error="Usage: /wisdom add <title> | <body>")
                writer.write(encode_message(error))
                await writer.drain()
                return
            slug = self.wisdom.add(title=title, body=body)
            resp = ResponseMessage(
                text=f"Added wisdom: {slug}",
                command="wisdom",
            )
            writer.write(encode_message(resp))
            await writer.drain()
            return

        if args.startswith("remove "):
            slug = args[7:].strip()
            if not slug:
                error = ErrorMessage(error="Usage: /wisdom remove <slug>")
                writer.write(encode_message(error))
                await writer.drain()
                return
            try:
                self.wisdom.remove(slug)
            except FileNotFoundError:
                error = ErrorMessage(error=f"Wisdom not found: {slug}")
                writer.write(encode_message(error))
                await writer.drain()
                return
            resp = ResponseMessage(text=f"Removed wisdom: {slug}", command="wisdom")
            writer.write(encode_message(resp))
            await writer.drain()
            return

        # Default: list entries
        entries = self.wisdom.list()
        if not entries:
            resp = ResponseMessage(
                text="No wisdom entries. Use `/wisdom add <title> | <body>` to add one.",
                command="wisdom",
            )
        else:
            lines = [f"{len(entries)} wisdom entries:\n"]
            for e in entries:
                lines.append(f"- **{e['title']}** ({e['slug']})")
            resp = ResponseMessage(text="\n".join(lines), command="wisdom")
        writer.write(encode_message(resp))
        await writer.drain()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_wisdom_commands.py -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest tests/ -v`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add pal/daemon.py tests/test_wisdom_commands.py
git commit -m "feat: /wisdom command — list/add/remove wisdom entries"
```

---

### Task 8: Integration Test — Prompt Injection End-to-End

**Files:**
- Create: `tests/test_prompt_injection.py`

- [ ] **Step 1: Write the integration test**

`tests/test_prompt_injection.py`:
```python
"""Verify profile and wisdom actually reach the inference server in chat."""
import asyncio

import pytest

from pal.client import PalClient
from pal.config import Config
from pal.daemon import Daemon
from pal.protocol import ResponseMessage, StreamChunkMessage


@pytest.fixture()
async def injection_daemon(socket_path, mock_inference_server, tmp_path):
    cfg = Config(
        inference_url=mock_inference_server,
        model="test-model",
        socket_path=socket_path,
        history_depth=50,
        vault_path=tmp_path / "vault",
        collection_id="vault",
        username="testuser",
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
async def test_profile_in_prompt_affects_chat(injection_daemon, socket_path):
    """After setting a profile, subsequent chats include it in the system prompt.

    The mock inference server echoes the user message, so we can't verify
    the system prompt directly — but we CAN verify the daemon still operates
    correctly with profile+wisdom loaded (no crashes, chat still works).
    """
    client = PalClient(socket_path)
    await client.connect()

    # Set a profile
    await client.command("profile", "set ## Bio\n\nTest user.")

    # Add wisdom
    await client.command("wisdom", "add Test Rule | Always test.")

    # Chat should still work
    tokens = []
    async for msg in client.chat("hello"):
        if isinstance(msg, StreamChunkMessage):
            tokens.append(msg.token)
        elif isinstance(msg, ResponseMessage):
            break
    full = "".join(tokens)
    assert "echo: hello" == full

    await client.close()


@pytest.mark.asyncio
async def test_prompt_builder_composed_correctly(injection_daemon, socket_path, tmp_path):
    """The daemon's prompt_builder reflects profile and wisdom state."""
    daemon = injection_daemon

    # Initially empty
    prompt = daemon.prompt_builder.build()
    assert "About the User" not in prompt
    assert "Active Wisdom" not in prompt

    # Set profile
    daemon.profile.write("## Bio\n\nEngineer.")
    prompt = daemon.prompt_builder.build()
    assert "About the User" in prompt
    assert "Engineer." in prompt

    # Add wisdom
    daemon.wisdom.add(title="Rule", body="Measure twice.")
    prompt = daemon.prompt_builder.build()
    assert "Active Wisdom" in prompt
    assert "Measure twice." in prompt
```

- [ ] **Step 2: Run the full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: all tests pass

- [ ] **Step 3: Commit**

```bash
git add tests/test_prompt_injection.py
git commit -m "test: verify profile and wisdom prompt injection end-to-end"
```

---

### Task 9: Final Verification + CLI Help Update

**Files:**
- Modify: `pal/cli.py`

- [ ] **Step 1: Update CLI help text**

In `pal/cli.py`, find:
```python
    console.print("[dim]Commands: /note /read /search /get /lint /status /quit[/dim]\n")
```

Replace with:
```python
    console.print("[dim]Commands: /note /read /search /get /profile /wisdom /lint /status /quit[/dim]\n")
```

- [ ] **Step 2: Run the full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: all tests pass

- [ ] **Step 3: Commit**

```bash
git add pal/cli.py
git commit -m "docs: update CLI help text with /profile and /wisdom commands"
```
