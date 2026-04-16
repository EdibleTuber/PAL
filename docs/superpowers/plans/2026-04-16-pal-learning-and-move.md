# PAL Learning Capture + move_file Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the capture gap in PAL's learning pipeline by giving the LLM in-band tools, adding a proactive signal-triggered scanner, and fixing command discoverability. Also add a single-op `move_file` tool.

**Architecture:** Three bundles, one spec. Bundle A adds three LLM tools (`add_learning`, `propose_promote`, `move_file`). Bundle B adds a `learning_scanner` module that fires after each LLM turn, pre-filters on signal regex, and on match fires a single structured inference call to extract a candidate learning, which is surfaced as an approval proposal. Bundle C centralizes command definitions in `pal/commands.py` with a drift-check test. All storage primitives (`LearningManager`, `WisdomManager`, `Reorganizer`) are already in place; this plan wires them to the LLM and adds the scanner.

**Tech Stack:** Python 3.12, pytest, asyncio, discord.py. No new dependencies. Uses existing project `.venv`.

**Spec:** `docs/superpowers/specs/2026-04-16-pal-learning-and-move-design.md`

**Assumed patterns from the codebase (engineer should skim these before starting):**
- Proposal lifecycle: `pal/tools.py:959` (`_propose_reorg`) and `pal/tools.py:1027` (`_reorg`). Proposals produce a `proposal_id`, wait for approval via `asyncio.Event`, then the executor tool consumes the id.
- Proposal message class: `pal/protocol.py:94` (`ReorgProposalMessage`).
- Discord proposal rendering: `pal/discord_interactions.py:138` (`build_reorg_proposal_embed`) and `pal/discord_interactions.py:448` (`_handle_reorg_proposal`).
- CLI proposal rendering: `pal/cli.py:65` (`format_reorg_proposal`).

---

## Task 1: Create COMMANDS registry

**Files:**
- Create: `pal/commands.py`
- Test: `tests/test_commands.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_commands.py
from pal.commands import COMMANDS, Command


def test_commands_registry_is_non_empty():
    assert len(COMMANDS) > 0


def test_every_command_has_shape():
    for cmd in COMMANDS:
        assert isinstance(cmd, Command)
        assert cmd.name and isinstance(cmd.name, str)
        assert isinstance(cmd.args, str)
        assert cmd.description and isinstance(cmd.description, str)


def test_expected_commands_present():
    names = {c.name for c in COMMANDS}
    expected = {
        "help", "status", "read", "search", "get", "note", "lint",
        "profile", "wisdom", "search-web", "fetch", "summarize",
        "compile", "compile-batch", "import", "learn", "learnings",
        "promote", "rate", "model", "think", "research", "quit",
    }
    missing = expected - names
    assert not missing, f"missing commands in registry: {missing}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_commands.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pal.commands'`

- [ ] **Step 3: Create the module**

```python
# pal/commands.py
"""Central registry of user-facing daemon commands.

Every command the daemon accepts via CommandMessage must have an entry
here. The CLI splash, /help output, system prompt, and the Discord
prefix-rewrite adapter all read from this list. A drift-check test
enforces that every daemon handler branch has a registry entry.
"""
from typing import NamedTuple


class Command(NamedTuple):
    name: str
    args: str
    description: str


COMMANDS: list[Command] = [
    Command("help", "", "Show this message"),
    Command("status", "", "Show daemon status (model, vault, etc.)"),
    Command("read", "<title>", "Read a wiki article"),
    Command("search", "<q>", "Search wiki articles"),
    Command("get", "<title>", "Get article by exact title"),
    Command("note", "<text>", "Save a quick note"),
    Command("lint", "", "Lint wiki articles"),
    Command("profile", "<q>", "Query your profile"),
    Command("wisdom", "[add/remove]", "Manage wisdom entries"),
    Command("search-web", "<q>", "Web search via SearxNG"),
    Command("fetch", "<url>", "Fetch and summarize a URL"),
    Command("summarize", "<t>", "Summarize a wiki article"),
    Command("compile", "<t>", "Compile a wiki article"),
    Command("compile-batch", "", "Compile all summaries in raw/summaries/"),
    Command("import", "<path>", "Import a local document into the vault"),
    Command("learn", "", "Extract learnings from conversation"),
    Command("learnings", "", "List saved learnings"),
    Command("promote", "<id>", "Promote a learning to wisdom"),
    Command("rate", "<id> <n>", "Rate a learning (1-5)"),
    Command("model", "[name]", "Show or switch the active model"),
    Command("think", "[mode]", "Control reasoning (on/off/auto/show/hide)"),
    Command("research", "<t>", "Research a topic or file of topics"),
    Command("quit", "", "End the session"),
]


def command_names() -> set[str]:
    """Return the set of registered command names."""
    return {c.name for c in COMMANDS}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_commands.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pal/commands.py tests/test_commands.py
git commit -m "feat: COMMANDS registry as single source of truth"
```

---

## Task 2: Drift-check test for daemon command handlers

**Files:**
- Create: `tests/test_commands_drift.py`
- (no code change required if daemon already aligned; Task 1's registry already lists all 23 known handlers)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_commands_drift.py
"""Drift check: every `msg.name == "foo"` branch in pal/daemon.py
must have a matching COMMANDS entry, and vice versa.
"""
import ast
from pathlib import Path

from pal.commands import command_names


DAEMON_PATH = Path(__file__).parent.parent / "pal" / "daemon.py"


def _collect_daemon_command_names() -> set[str]:
    """Parse daemon.py and collect every string compared against msg.name."""
    tree = ast.parse(DAEMON_PATH.read_text())
    found: set[str] = set()

    for node in ast.walk(tree):
        # msg.name == "foo"
        if isinstance(node, ast.Compare):
            if (
                isinstance(node.left, ast.Attribute)
                and node.left.attr == "name"
                and len(node.ops) == 1
                and isinstance(node.ops[0], ast.Eq)
                and len(node.comparators) == 1
                and isinstance(node.comparators[0], ast.Constant)
                and isinstance(node.comparators[0].value, str)
            ):
                found.add(node.comparators[0].value)
            # msg.name in ("foo", "bar")
            if (
                isinstance(node.left, ast.Attribute)
                and node.left.attr == "name"
                and len(node.ops) == 1
                and isinstance(node.ops[0], ast.In)
                and len(node.comparators) == 1
                and isinstance(node.comparators[0], (ast.Tuple, ast.List))
            ):
                for elt in node.comparators[0].elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        found.add(elt.value)
    return found


def test_no_command_drift():
    daemon_cmds = _collect_daemon_command_names()
    registry_cmds = command_names()

    # "exit" is a quit alias handled in a tuple branch; exempt it.
    daemon_cmds.discard("exit")

    missing_from_registry = daemon_cmds - registry_cmds
    missing_from_daemon = registry_cmds - daemon_cmds

    assert not missing_from_registry, (
        f"daemon handles these commands but COMMANDS registry does not list them: "
        f"{sorted(missing_from_registry)}"
    )
    assert not missing_from_daemon, (
        f"COMMANDS registry lists these but no daemon handler exists: "
        f"{sorted(missing_from_daemon)}"
    )
```

- [ ] **Step 2: Run test to verify it passes (drift should be zero after Task 1)**

Run: `.venv/bin/pytest tests/test_commands_drift.py -v`
Expected: PASS (Task 1's registry covers all 23 handlers; "exit" alias is excluded).

If it fails because the registry missed a command the daemon actually handles, add the missing `Command(...)` entry to `pal/commands.py` with an appropriate description and re-run.

- [ ] **Step 3: Commit**

```bash
git add tests/test_commands_drift.py
git commit -m "test: drift check between daemon handlers and COMMANDS registry"
```

---

## Task 3: Render /help from COMMANDS

**Files:**
- Modify: `pal/daemon.py:407-438` (`_handle_command` help branch)
- Test: `tests/test_daemon_help.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_daemon_help.py
"""The /help handler must render from the COMMANDS registry."""
from pal.commands import COMMANDS
from pal.daemon import render_help_text


def test_render_help_contains_every_command():
    text = render_help_text()
    for cmd in COMMANDS:
        assert f"/{cmd.name}" in text, f"command /{cmd.name} missing from help"


def test_render_help_includes_descriptions():
    text = render_help_text()
    for cmd in COMMANDS:
        assert cmd.description in text, f"description for /{cmd.name} missing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_daemon_help.py -v`
Expected: FAIL with `ImportError: cannot import name 'render_help_text'`

- [ ] **Step 3: Add the helper and rewire /help**

Add near the top of `pal/daemon.py` after the imports:

```python
from pal.commands import COMMANDS


def render_help_text() -> str:
    """Render /help output from the COMMANDS registry."""
    lines = ["Available commands:"]
    max_name = max(len(f"/{c.name} {c.args}".rstrip()) for c in COMMANDS)
    for cmd in COMMANDS:
        prefix = f"/{cmd.name} {cmd.args}".rstrip()
        padded = prefix.ljust(max_name)
        lines.append(f"  {padded}  - {cmd.description}")
    return "\n".join(lines)
```

Replace the body of the `if msg.name == "help":` branch at `pal/daemon.py:407` so only the text source changes (keep the ResponseMessage wiring):

```python
        if msg.name == "help":
            resp = ResponseMessage(
                text=render_help_text(),
                command="help",
            )
            writer.write(encode_message(resp))
            await writer.drain()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_daemon_help.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pal/daemon.py tests/test_daemon_help.py
git commit -m "feat: /help renders from COMMANDS registry"
```

---

## Task 4: Render CLI splash from COMMANDS

**Files:**
- Modify: `pal/cli.py:208-210` (splash print block)
- Test: `tests/test_cli_splash.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_splash.py
from pal.cli import render_splash_commands
from pal.commands import COMMANDS


def test_splash_contains_every_command_name():
    text = render_splash_commands()
    for cmd in COMMANDS:
        assert f"/{cmd.name}" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cli_splash.py -v`
Expected: FAIL with `ImportError: cannot import name 'render_splash_commands'`

- [ ] **Step 3: Add the helper and rewire the splash**

Add near the top of `pal/cli.py` (after imports):

```python
from pal.commands import COMMANDS


def render_splash_commands() -> str:
    """Render the compact command list shown on CLI startup."""
    names = [f"/{c.name}" for c in COMMANDS]
    # Pack names into lines under ~90 chars.
    lines: list[list[str]] = [[]]
    current_len = 0
    for name in names:
        if current_len + len(name) + 1 > 88 and lines[-1]:
            lines.append([])
            current_len = 0
        lines[-1].append(name)
        current_len += len(name) + 1
    return "\n          ".join(" ".join(line) for line in lines)
```

Replace the splash print at `pal/cli.py:208-210`:

```python
    console.print("[dim]PAL - Personal Agentic Librarian[/dim]")
    console.print(f"[dim]Commands: {render_splash_commands()}[/dim]\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_cli_splash.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pal/cli.py tests/test_cli_splash.py
git commit -m "feat: CLI splash renders from COMMANDS registry"
```

---

## Task 5: Inject command list into system prompt

**Files:**
- Modify: `pal/prompt_builder.py` (the `build()` method)
- Test: `tests/test_prompt_builder_commands.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prompt_builder_commands.py
from pathlib import Path

from pal.commands import COMMANDS
from pal.profile import ProfileManager
from pal.wisdom import WisdomManager
from pal.prompt_builder import SystemPromptBuilder


def test_system_prompt_contains_commands_section(tmp_path: Path):
    (tmp_path / "_wisdom").mkdir()
    (tmp_path / "_profile").mkdir()
    builder = SystemPromptBuilder(
        profile=ProfileManager(tmp_path),
        wisdom=WisdomManager(tmp_path),
    )
    prompt = builder.build()

    assert "## Available Commands" in prompt
    for cmd in COMMANDS:
        assert f"/{cmd.name}" in prompt, f"command /{cmd.name} not in prompt"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_prompt_builder_commands.py -v`
Expected: FAIL with `AssertionError: ... not in prompt` or `## Available Commands` missing.

- [ ] **Step 3: Add commands section to the prompt**

In `pal/prompt_builder.py`, add after the `wisdom_bodies` block near the existing "## Active Wisdom" section (around line 116):

```python
        from pal.commands import COMMANDS
        cmd_lines = [f"- `/{c.name} {c.args}`".rstrip() + f" - {c.description}"
                     for c in COMMANDS]
        sections.append(
            "## Available Commands\n\n"
            "The user can invoke these slash commands (they appear as `!cmd` "
            "in Discord). When the user asks what commands exist, cite from "
            "this list verbatim.\n\n"
            + "\n".join(cmd_lines)
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_prompt_builder_commands.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pal/prompt_builder.py tests/test_prompt_builder_commands.py
git commit -m "feat: system prompt includes Available Commands section"
```

---

## Task 6: Discord adapter rewrites `/cmd` to `!cmd` in outbound text

**Files:**
- Modify: `pal/discord_adapter.py` (add `rewrite_slash_prefixes` helper, use in send path)
- Test: `tests/test_discord_prefix_rewrite.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_discord_prefix_rewrite.py
from pal.discord_adapter import rewrite_slash_prefixes


def test_rewrites_known_command_at_line_start():
    out = rewrite_slash_prefixes("Use /learn to extract learnings.")
    assert out == "Use !learn to extract learnings."


def test_rewrites_multiple_known_commands():
    out = rewrite_slash_prefixes("Try /learnings or /promote <slug>.")
    assert "!learnings" in out and "!promote" in out
    assert "/learnings" not in out and "/promote" not in out


def test_does_not_rewrite_unknown_tokens():
    # /not-a-command stays as-is.
    out = rewrite_slash_prefixes("See /not-a-command for details.")
    assert "/not-a-command" in out


def test_does_not_rewrite_inside_code_fence():
    src = "```\n/learn should stay as /learn\n```"
    out = rewrite_slash_prefixes(src)
    assert out == src


def test_does_not_rewrite_inside_inline_code():
    src = "Use `/learn` inline."
    out = rewrite_slash_prefixes(src)
    assert out == src


def test_rewrites_after_punctuation():
    out = rewrite_slash_prefixes("First: /learn, then /promote <slug>.")
    assert "!learn" in out and "!promote" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_discord_prefix_rewrite.py -v`
Expected: FAIL with `ImportError: cannot import name 'rewrite_slash_prefixes'`

- [ ] **Step 3: Add the rewriter and wire it into the outbound path**

Add to `pal/discord_adapter.py`:

```python
import re

from pal.commands import command_names


_FENCED_CODE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE = re.compile(r"`[^`\n]+`")


def rewrite_slash_prefixes(text: str) -> str:
    """Translate `/cmd` to `!cmd` for commands registered in COMMANDS.

    Skips content inside fenced and inline code. Only rewrites tokens at
    line start or immediately following whitespace/punctuation.
    """
    names = command_names()
    if not names:
        return text
    # Build an alternation regex for known command names, longest first
    # so `compile-batch` wins over `compile`.
    sorted_names = sorted(names, key=len, reverse=True)
    pattern = re.compile(
        r"(?P<lead>^|[\s,.;:!?\(])/(?P<name>" + "|".join(re.escape(n) for n in sorted_names) + r")\b"
    )

    # Protect fenced code blocks and inline code by temporarily substituting.
    placeholders: dict[str, str] = {}

    def _stash(m: re.Match) -> str:
        key = f"\x00PLACEHOLDER{len(placeholders)}\x00"
        placeholders[key] = m.group(0)
        return key

    safe = _FENCED_CODE.sub(_stash, text)
    safe = _INLINE_CODE.sub(_stash, safe)

    rewritten = pattern.sub(lambda m: f"{m.group('lead')}!{m.group('name')}", safe)

    for key, original in placeholders.items():
        rewritten = rewritten.replace(key, original)
    return rewritten
```

Then in the outbound send path (where `split_message` is called before `channel.send(...)` - search for `split_message(` in `pal/discord_adapter.py` and wrap the text), apply the rewrite before splitting:

```python
# Before every call of the form:
#   chunks = split_message(text)
# Change to:
#   chunks = split_message(rewrite_slash_prefixes(text))
```

Apply this to every outbound text send site in the file.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_discord_prefix_rewrite.py -v`
Expected: PASS (all six tests)

- [ ] **Step 5: Commit**

```bash
git add pal/discord_adapter.py tests/test_discord_prefix_rewrite.py
git commit -m "feat: Discord adapter rewrites /cmd to !cmd in outbound text"
```

---

## Task 7: LearningManager helpers (exists, get_meta)

**Files:**
- Modify: `pal/learning.py`
- Test: `tests/test_learning.py` (extend existing)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_learning.py`:

```python
def test_exists_returns_true_for_existing(tmp_path):
    from pal.learning import LearningManager
    lm = LearningManager(tmp_path)
    slug = lm.add("My Lesson", "body text", source="conversation")
    assert lm.exists(slug) is True


def test_exists_returns_false_for_missing(tmp_path):
    from pal.learning import LearningManager
    lm = LearningManager(tmp_path)
    assert lm.exists("no-such-slug") is False


def test_get_meta_returns_frontmatter(tmp_path):
    from pal.learning import LearningManager
    lm = LearningManager(tmp_path)
    slug = lm.add("My Lesson", "body text", source="conversation")
    meta = lm.get_meta(slug)
    assert meta["title"] == "My Lesson"
    assert meta["status"] == "active"


def test_get_meta_raises_for_missing(tmp_path):
    from pal.learning import LearningManager
    import pytest
    lm = LearningManager(tmp_path)
    with pytest.raises(FileNotFoundError):
        lm.get_meta("no-such-slug")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_learning.py -v -k "exists or get_meta"`
Expected: FAIL with `AttributeError: 'LearningManager' object has no attribute 'exists'`

- [ ] **Step 3: Add helpers**

Add to `pal/learning.py` inside `LearningManager`:

```python
    def exists(self, slug: str) -> bool:
        """Return True if a learning with this slug exists."""
        return (self.learning_dir / f"{slug}.md").exists()

    def get_meta(self, slug: str) -> dict:
        """Return the frontmatter dict of a learning by slug."""
        path = self.learning_dir / f"{slug}.md"
        if not path.exists():
            raise FileNotFoundError(f"Learning not found: {slug}")
        meta, _ = parse_frontmatter(path.read_text())
        return meta
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_learning.py -v -k "exists or get_meta"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pal/learning.py tests/test_learning.py
git commit -m "feat: LearningManager.exists and get_meta helpers"
```

---

## Task 8: Reorganizer.move_single primitive

**Files:**
- Modify: `pal/reorg.py` (add `move_single` method)
- Test: `tests/test_reorg.py` (extend existing; if missing, create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reorg_move_single.py
from pathlib import Path

import pytest

from pal.reorg import Reorganizer


def _make_vault(tmp_path: Path) -> Path:
    (tmp_path / "Security").mkdir()
    (tmp_path / "IoT").mkdir()
    (tmp_path / "Security" / "methodology.md").write_text("---\ntitle: M\n---\nbody\n")
    return tmp_path


def test_move_single_renames_file(tmp_path: Path):
    vault = _make_vault(tmp_path)
    r = Reorganizer(vault_path=vault, wiki=None, compiler=None, retrieval=None)
    r.move_single("Security/methodology.md", "IoT/methodology.md")
    assert not (vault / "Security" / "methodology.md").exists()
    assert (vault / "IoT" / "methodology.md").exists()


def test_move_single_rejects_missing_src(tmp_path: Path):
    vault = _make_vault(tmp_path)
    r = Reorganizer(vault_path=vault, wiki=None, compiler=None, retrieval=None)
    with pytest.raises(FileNotFoundError):
        r.move_single("Security/missing.md", "IoT/missing.md")


def test_move_single_rejects_existing_dst(tmp_path: Path):
    vault = _make_vault(tmp_path)
    (vault / "IoT" / "methodology.md").write_text("existing")
    r = Reorganizer(vault_path=vault, wiki=None, compiler=None, retrieval=None)
    with pytest.raises(FileExistsError):
        r.move_single("Security/methodology.md", "IoT/methodology.md")


def test_move_single_rejects_system_dirs(tmp_path: Path):
    vault = _make_vault(tmp_path)
    (vault / "_wisdom").mkdir()
    (vault / "_wisdom" / "x.md").write_text("x")
    r = Reorganizer(vault_path=vault, wiki=None, compiler=None, retrieval=None)
    with pytest.raises(ValueError, match="system directory"):
        r.move_single("_wisdom/x.md", "IoT/x.md")
    with pytest.raises(ValueError, match="system directory"):
        r.move_single("Security/methodology.md", "raw/methodology.md")


def test_move_single_creates_parent_dirs(tmp_path: Path):
    vault = _make_vault(tmp_path)
    r = Reorganizer(vault_path=vault, wiki=None, compiler=None, retrieval=None)
    r.move_single("Security/methodology.md", "Networking/Protocols/methodology.md")
    assert (vault / "Networking" / "Protocols" / "methodology.md").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_reorg_move_single.py -v`
Expected: FAIL with `AttributeError: 'Reorganizer' object has no attribute 'move_single'`

- [ ] **Step 3: Add `move_single`**

Add to `pal/reorg.py` inside `Reorganizer`:

```python
    def move_single(self, src: str, dst: str) -> None:
        """Rename a single vault file. Raises on missing src, existing dst,
        or paths inside system directories (raw/ or underscore-prefixed).
        """
        def _is_system_path(p: str) -> bool:
            parts = p.split("/")
            return parts[0] == "raw" or (parts[0].startswith("_") if parts[0] else False)

        if _is_system_path(src) or _is_system_path(dst):
            raise ValueError(f"system directory: {src} or {dst}")

        src_path = self.vault_path / src
        dst_path = self.vault_path / dst

        if not src_path.exists():
            raise FileNotFoundError(f"source not found: {src}")
        if dst_path.exists():
            raise FileExistsError(f"destination exists: {dst}")

        dst_path.parent.mkdir(parents=True, exist_ok=True)
        src_path.rename(dst_path)
        logger.info("move_single: %s -> %s", src, dst)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_reorg_move_single.py -v`
Expected: PASS (all five tests)

- [ ] **Step 5: Commit**

```bash
git add pal/reorg.py tests/test_reorg_move_single.py
git commit -m "feat: Reorganizer.move_single for single-op vault rename"
```

---

## Task 9: add_learning LLM tool

**Files:**
- Modify: `pal/tools.py` (add tool schema + handler)
- Test: `tests/test_tools_add_learning.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools_add_learning.py
import asyncio
from pathlib import Path

import pytest

from pal.learning import LearningManager
from pal.tools import ToolHandler  # adjust import if class name differs


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    return tmp_path


def test_add_learning_writes_file(vault: Path):
    # Minimal ToolHandler: we only need it to hold a LearningManager.
    # If ToolHandler requires more deps, use real instances with tmp vault.
    handler = _make_handler(vault)
    result = asyncio.run(handler._add_learning({
        "title": "Granularity Over Consolidation",
        "body": "Keep articles focused, not merged into master guides.",
    }))
    import json
    parsed = json.loads(result)
    slug = parsed["slug"]

    lm = LearningManager(vault)
    assert lm.exists(slug)
    assert "focused" in lm.get(slug)


def test_add_learning_rejects_empty_title(vault: Path):
    handler = _make_handler(vault)
    result = asyncio.run(handler._add_learning({"title": "", "body": "x"}))
    import json
    parsed = json.loads(result)
    assert "error" in parsed
    assert "title" in parsed["error"].lower()


def test_add_learning_rejects_empty_body(vault: Path):
    handler = _make_handler(vault)
    result = asyncio.run(handler._add_learning({"title": "x", "body": ""}))
    import json
    parsed = json.loads(result)
    assert "error" in parsed
    assert "body" in parsed["error"].lower()


def _make_handler(vault: Path):
    """Construct a minimal ToolHandler for unit testing the _add_learning method.
    The engineer should use the project's existing fixture if available; otherwise
    instantiate with only the fields _add_learning reads (learning manager + git commit).
    """
    # Check how existing tests build ToolHandler (look at tests/test_tools.py).
    # Fill in with a real constructor call matching the current signature.
    raise NotImplementedError(
        "replace with real ToolHandler construction matching tests/test_tools.py"
    )
```

Before running: **open `tests/test_tools.py`** (if present) and copy the `ToolHandler` fixture/constructor pattern into `_make_handler` above. If the codebase uses dependency injection via `daemon.py`, consider building the ToolHandler by following the construction in `pal/daemon.py:__init__`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_tools_add_learning.py -v`
Expected: FAIL on `_add_learning` not existing or on `NotImplementedError` (fix `_make_handler` first, then FAIL should be on the method).

- [ ] **Step 3: Add tool schema and handler**

In `pal/tools.py`, add to the `TOOLS` / `TOOL_SCHEMAS` list (wherever the other tools like `create_file` are defined):

```python
    {
        "type": "function",
        "function": {
            "name": "add_learning",
            "description": (
                "Save a durable lesson extracted from conversation into the "
                "learning pool. Learnings stay as candidates until the user "
                "promotes them to wisdom via /promote. Use when the user says "
                "'make a learning out of that' or when you detect a correction "
                "you want to remember across sessions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short, specific title of the lesson.",
                    },
                    "body": {
                        "type": "string",
                        "description": "The lesson itself, in 1-4 sentences.",
                    },
                },
                "required": ["title", "body"],
            },
        },
    },
```

And add the handler method on the `ToolHandler` class alongside the others:

```python
    async def _add_learning(self, arguments: dict) -> str:
        import json
        title = (arguments.get("title") or "").strip()
        body = (arguments.get("body") or "").strip()
        if not title:
            return json.dumps({"error": "title is required"})
        if not body:
            return json.dumps({"error": "body is required"})
        slug = self.learning.add(title=title, body=body, source="conversation")
        # Git commit so the learning is durable and indexed next reindex cycle.
        if self.wiki is not None:
            self.wiki.git_commit(f"learn: add {slug}")
        return json.dumps({"slug": slug, "title": title})
```

Then register the dispatch in the tool execution switch (search `pal/tools.py` for where `create_file` is dispatched and add `add_learning` alongside it):

```python
        elif name == "add_learning":
            return await self._add_learning(arguments)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_tools_add_learning.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pal/tools.py tests/test_tools_add_learning.py
git commit -m "feat: add_learning tool for in-band learning capture"
```

---

## Task 10: move_file LLM tool

**Files:**
- Modify: `pal/tools.py`
- Test: `tests/test_tools_move_file.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools_move_file.py
import asyncio
import json
from pathlib import Path

import pytest


def test_move_file_moves_and_triggers_reindex(tmp_path: Path):
    vault = tmp_path
    (vault / "Security").mkdir()
    (vault / "IoT").mkdir()
    (vault / "Security" / "methodology.md").write_text("---\ntitle: M\n---\nbody\n")

    reindex_calls: list[str] = []
    handler = _make_handler(vault, reindex_hook=lambda: reindex_calls.append("r"))
    result = asyncio.run(handler._move_file({
        "src": "Security/methodology.md",
        "dst": "IoT/methodology.md",
    }))
    parsed = json.loads(result)

    assert parsed["moved"] == "Security/methodology.md -> IoT/methodology.md"
    assert (vault / "IoT" / "methodology.md").exists()
    assert not (vault / "Security" / "methodology.md").exists()
    assert reindex_calls == ["r"]


def test_move_file_rejects_system_dirs(tmp_path: Path):
    vault = tmp_path
    (vault / "_wisdom").mkdir()
    (vault / "_wisdom" / "x.md").write_text("x")
    (vault / "IoT").mkdir()
    handler = _make_handler(vault)
    result = asyncio.run(handler._move_file({"src": "_wisdom/x.md", "dst": "IoT/x.md"}))
    assert "error" in json.loads(result)


def test_move_file_rejects_missing_src(tmp_path: Path):
    vault = tmp_path
    (vault / "Security").mkdir()
    (vault / "IoT").mkdir()
    handler = _make_handler(vault)
    result = asyncio.run(handler._move_file({"src": "Security/ghost.md", "dst": "IoT/ghost.md"}))
    assert "error" in json.loads(result)


def _make_handler(vault: Path, reindex_hook=None):
    """Construct a ToolHandler with a Reorganizer pointing at the tmp vault
    and a stubbable reindex callback. Match the project's existing fixture pattern.
    """
    raise NotImplementedError("replace with real ToolHandler construction")
```

Before running: fill `_make_handler` the same way as in Task 9.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_tools_move_file.py -v`
Expected: FAIL with `_move_file` method missing.

- [ ] **Step 3: Add tool schema and handler**

Add the tool schema to `pal/tools.py`:

```python
    {
        "type": "function",
        "function": {
            "name": "move_file",
            "description": (
                "Move a single vault article from src to dst. Use for quick "
                "re-categorization (for example, moving a mis-categorized "
                "article from Security/ to IoT/). For batch moves or merges, "
                "use propose_reorg instead. Triggers reindex. Rejects paths "
                "inside raw/ or underscore-prefixed system directories."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "src": {
                        "type": "string",
                        "description": "Current path (relative to vault root).",
                    },
                    "dst": {
                        "type": "string",
                        "description": "Destination path (relative to vault root). Must not exist.",
                    },
                },
                "required": ["src", "dst"],
            },
        },
    },
```

Add the handler method on `ToolHandler`:

```python
    async def _move_file(self, arguments: dict) -> str:
        import json
        src = (arguments.get("src") or "").strip()
        dst = (arguments.get("dst") or "").strip()
        if not src or not dst:
            return json.dumps({"error": "src and dst are required"})
        try:
            self.reorganizer.move_single(src, dst)
        except (FileNotFoundError, FileExistsError, ValueError) as exc:
            return json.dumps({"error": str(exc)})
        if self.wiki is not None:
            self.wiki.git_commit(f"move: {src} -> {dst}")
        if self.retrieval is not None:
            try:
                await self.retrieval.trigger_reindex()
            except Exception as exc:
                logger.warning("reindex trigger failed after move: %s", exc)
        return json.dumps({"moved": f"{src} -> {dst}", "reindex_queued": True})
```

Register the dispatch next to other tool dispatches:

```python
        elif name == "move_file":
            return await self._move_file(arguments)
```

Verify that `ToolHandler.__init__` receives a `Reorganizer` reference; if not, thread one through from `pal/daemon.py` where `ToolHandler` is constructed. (Search `pal/daemon.py` for `ToolHandler(` to find the construction site.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_tools_move_file.py -v`
Expected: PASS (all three tests)

- [ ] **Step 5: Commit**

```bash
git add pal/tools.py tests/test_tools_move_file.py
git commit -m "feat: move_file tool for single-op article relocation"
```

---

## Task 11: PromoteProposalMessage protocol

**Files:**
- Modify: `pal/protocol.py`
- Test: `tests/test_protocol_promote_proposal.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_protocol_promote_proposal.py
from pal.protocol import PromoteProposalMessage, encode_message, decode_message


def test_promote_proposal_roundtrip():
    msg = PromoteProposalMessage(
        proposal_id="p-123",
        slug="granularity-over-consolidation",
        title="Granularity Over Consolidation",
        body="Keep articles focused.",
        rationale="User reiterated the preference.",
    )
    wire = encode_message(msg)
    decoded = decode_message(wire)
    assert isinstance(decoded, PromoteProposalMessage)
    assert decoded.proposal_id == "p-123"
    assert decoded.slug == "granularity-over-consolidation"
    assert decoded.rationale == "User reiterated the preference."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_protocol_promote_proposal.py -v`
Expected: FAIL with `ImportError: cannot import name 'PromoteProposalMessage'`

- [ ] **Step 3: Add the message class**

In `pal/protocol.py`, add near `ReorgProposalMessage` (around line 94):

```python
@dataclass
class PromoteProposalMessage:
    proposal_id: str
    slug: str
    title: str
    body: str
    rationale: str
```

And register it in the wire-type map and the union (search `pal/protocol.py` for `"reorg_proposal":` and add below):

```python
    "promote_proposal": PromoteProposalMessage,
```

and add `| PromoteProposalMessage` to the union type alias.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_protocol_promote_proposal.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pal/protocol.py tests/test_protocol_promote_proposal.py
git commit -m "feat: PromoteProposalMessage protocol class"
```

---

## Task 12: propose_promote LLM tool (emits proposal, awaits approval, executes)

**Files:**
- Modify: `pal/tools.py`
- Test: `tests/test_tools_propose_promote.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools_propose_promote.py
import asyncio
import json
from pathlib import Path

import pytest

from pal.learning import LearningManager


def test_propose_promote_emits_proposal_and_promotes_on_approve(tmp_path: Path):
    vault = tmp_path
    lm = LearningManager(vault)
    slug = lm.add("Granularity", "keep it focused", source="conversation")

    handler, approver = _make_handler_with_approver(vault, auto_approve=True)
    result = asyncio.run(handler._propose_promote({
        "slug": slug,
        "rationale": "User reiterated.",
    }))
    parsed = json.loads(result)
    assert parsed["status"] == "promoted"
    # Learning marked promoted
    assert lm.get_meta(slug)["status"] == "promoted"
    # Wisdom entry created
    from pal.wisdom import WisdomManager
    wm = WisdomManager(vault)
    titles = {e["title"] for e in wm.list()}
    assert "Granularity" in titles


def test_propose_promote_aborts_on_decline(tmp_path: Path):
    vault = tmp_path
    lm = LearningManager(vault)
    slug = lm.add("Temp", "body", source="conversation")

    handler, _ = _make_handler_with_approver(vault, auto_approve=False)
    result = asyncio.run(handler._propose_promote({
        "slug": slug,
        "rationale": "r",
    }))
    parsed = json.loads(result)
    assert parsed["status"] == "declined"
    assert lm.get_meta(slug)["status"] == "active"


def test_propose_promote_errors_on_missing_slug(tmp_path: Path):
    handler, _ = _make_handler_with_approver(tmp_path, auto_approve=True)
    result = asyncio.run(handler._propose_promote({"slug": "no-such", "rationale": "r"}))
    assert "error" in json.loads(result)


def _make_handler_with_approver(vault: Path, auto_approve: bool):
    """Construct a ToolHandler whose proposal-approval mechanism is stubbed to
    auto-approve or auto-decline. Mirror the existing test fixture for
    _propose_reorg (see tests/test_tools.py or tests/test_reorg.py).
    """
    raise NotImplementedError("replace following existing propose_reorg test pattern")
```

Before running: mirror the auto-approve pattern used in any existing `test_tools*` file that exercises `_propose_reorg` or `_propose_research`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_tools_propose_promote.py -v`
Expected: FAIL with `_propose_promote` missing.

- [ ] **Step 3: Add tool schema and handler**

Add schema to `pal/tools.py`:

```python
    {
        "type": "function",
        "function": {
            "name": "propose_promote",
            "description": (
                "Propose promoting an existing learning (in _learning/) to "
                "wisdom (_wisdom/). Wisdom is injected into every future system "
                "prompt and should be treated as durable guidance. Requires "
                "user approval. Call with the learning slug (from list_learnings "
                "or the return of add_learning) and a brief rationale."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "Slug of the learning to promote.",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "One-line reason shown in the approval prompt.",
                    },
                },
                "required": ["slug", "rationale"],
            },
        },
    },
```

Handler (model after `_propose_reorg` at `pal/tools.py:959` for the approval-waiting pattern):

```python
    async def _propose_promote(self, arguments: dict) -> str:
        import json
        import uuid
        from pal.protocol import PromoteProposalMessage

        slug = (arguments.get("slug") or "").strip()
        rationale = (arguments.get("rationale") or "").strip()
        if not slug:
            return json.dumps({"error": "slug is required"})
        if not self.learning.exists(slug):
            return json.dumps({"error": f"no such learning: {slug}"})
        meta = self.learning.get_meta(slug)
        if meta.get("status") == "promoted":
            return json.dumps({
                "error": f"already promoted at {meta.get('promoted_at', 'unknown')}"
            })
        body = self.learning.get(slug)
        title = meta.get("title", slug)

        proposal_id = uuid.uuid4().hex
        # Emit proposal using the same registry/wait-event pattern as _propose_reorg.
        # Look at _propose_reorg in this file for the exact calls (proposal registry,
        # asyncio.Event, timeout). Adapt them for PromoteProposalMessage.
        approved = await self._emit_and_wait_promote(
            PromoteProposalMessage(
                proposal_id=proposal_id,
                slug=slug,
                title=title,
                body=body,
                rationale=rationale,
            )
        )
        if not approved:
            return json.dumps({"status": "declined", "slug": slug})

        self.learning.mark_promoted(slug)
        self.wisdom.add(title=title, body=body)
        if self.wiki is not None:
            self.wiki.git_commit(f"promote: {slug} -> wisdom")
        return json.dumps({"status": "promoted", "slug": slug})
```

Also add the helper `_emit_and_wait_promote` alongside the existing `_emit_and_wait_reorg` (or equivalent). If the file uses a generic `_emit_and_wait(msg)` already, use that. Look at existing code to pick the right integration point.

Register dispatch:

```python
        elif name == "propose_promote":
            return await self._propose_promote(arguments)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_tools_propose_promote.py -v`
Expected: PASS (all three tests)

- [ ] **Step 5: Commit**

```bash
git add pal/tools.py tests/test_tools_propose_promote.py
git commit -m "feat: propose_promote tool for approval-gated wisdom promotion"
```

---

## Task 13: Discord UI for promote proposal

**Files:**
- Modify: `pal/discord_interactions.py`
- Test: `tests/test_discord_promote_proposal.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_discord_promote_proposal.py
from pal.discord_interactions import build_promote_proposal_embed
from pal.protocol import PromoteProposalMessage


def test_promote_embed_contains_title_body_rationale():
    msg = PromoteProposalMessage(
        proposal_id="p1",
        slug="granularity",
        title="Granularity Over Consolidation",
        body="Keep articles focused.",
        rationale="User reiterated.",
    )
    embed = build_promote_proposal_embed(msg)
    rendered = (embed.title or "") + "\n" + (embed.description or "")
    for field in embed.fields:
        rendered += f"\n{field.name}: {field.value}"
    assert "Granularity Over Consolidation" in rendered
    assert "Keep articles focused." in rendered
    assert "User reiterated." in rendered
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_discord_promote_proposal.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Add embed builder + button handler**

Model after `build_reorg_proposal_embed` at `pal/discord_interactions.py:138` and `_handle_reorg_proposal` at `pal/discord_interactions.py:448`. Add:

```python
def build_promote_proposal_embed(msg: PromoteProposalMessage) -> discord.Embed:
    embed = discord.Embed(
        title=f"PAL proposes promoting to wisdom",
        color=discord.Color.gold(),
    )
    embed.add_field(name="Title", value=msg.title, inline=False)
    embed.add_field(name="Body", value=msg.body[:1000], inline=False)
    embed.add_field(name="Rationale", value=msg.rationale, inline=False)
    embed.add_field(name="Slug", value=f"`{msg.slug}`", inline=True)
    return embed
```

Wire into the stream handler's `isinstance(msg, PromoteProposalMessage)` branch (alongside the reorg branch). Reuse the existing Approve / Decline button components; custom_id format should be `promote:{proposal_id}:approve` / `promote:{proposal_id}:decline`, matching the existing `reorg:` scheme. Extend `parse_button_custom_id` if needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_discord_promote_proposal.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pal/discord_interactions.py tests/test_discord_promote_proposal.py
git commit -m "feat: Discord embed + buttons for promote proposal"
```

---

## Task 14: LearningCandidateProposalMessage protocol

**Files:**
- Modify: `pal/protocol.py`
- Test: `tests/test_protocol_learning_candidate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_protocol_learning_candidate.py
from pal.protocol import LearningCandidateProposalMessage, encode_message, decode_message


def test_learning_candidate_roundtrip():
    msg = LearningCandidateProposalMessage(
        proposal_id="lc-1",
        title="Granularity Over Consolidation",
        body="Keep articles focused.",
        trigger_excerpt="you always try to merge into one article",
    )
    decoded = decode_message(encode_message(msg))
    assert isinstance(decoded, LearningCandidateProposalMessage)
    assert decoded.proposal_id == "lc-1"
    assert decoded.trigger_excerpt == "you always try to merge into one article"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_protocol_learning_candidate.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Add the message class**

In `pal/protocol.py`, near the other proposal classes:

```python
@dataclass
class LearningCandidateProposalMessage:
    proposal_id: str
    title: str
    body: str
    trigger_excerpt: str  # the user-message fragment that triggered the scan
```

Register in the wire-type map and union the same way as `PromoteProposalMessage` in Task 11.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_protocol_learning_candidate.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pal/protocol.py tests/test_protocol_learning_candidate.py
git commit -m "feat: LearningCandidateProposalMessage protocol class"
```

---

## Task 15: Learning scanner - signal pre-filter

**Files:**
- Create: `pal/learning_scanner.py`
- Test: `tests/test_learning_scanner_prefilter.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_learning_scanner_prefilter.py
import pytest

from pal.learning_scanner import has_signal


@pytest.mark.parametrize("msg", [
    "actually, you got that wrong",
    "No, don't do that",
    "stop, I meant the other one",
    "you always try to merge these",
    "you never cite sources",
    "you should use DOMPurify",
    "you shouldn't rely on auto-escape",
    "exactly, that's what I meant",
    "perfect, keep doing that",
    "thank you for the correction",
    "you're right about that",
    "that's wrong",
    "you tend to over-consolidate",
])
def test_has_signal_matches(msg: str):
    assert has_signal(msg) is True


@pytest.mark.parametrize("msg", [
    "tell me about IoT security",
    "what does OpenOCD do?",
    "Can we research compilers next?",
    "cool",
    "ok",
])
def test_has_signal_ignores_neutral(msg: str):
    assert has_signal(msg) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_learning_scanner_prefilter.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create the module**

```python
# pal/learning_scanner.py
"""Proactive scanner for learning candidates.

Fires after each LLM turn completes. A two-stage pipeline: a cheap regex
pre-filter gates an LLM extraction call. The extraction call decides
whether a durable lesson exists in the recent conversation and returns
{title, body} or null. Novel candidates are surfaced as approval
proposals via LearningCandidateProposalMessage.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


# Signal patterns: phrases that plausibly indicate a correction,
# confirmation, or durable preference worth turning into a learning.
# Applied case-insensitively to the latest user message.
_SIGNAL_PATTERNS = [
    r"\bactually\b",
    r"\bno[,.\s]",
    r"\bstop\b",
    r"\byou\s+(always|never|should|shouldn[''`]?t|tend\s+to)\b",
    r"\bexactly\b",
    r"\bperfect\b",
    r"\bthank\s+you\b",
    r"\byou[''`]re\s+right\b",
    r"\bthat[''`]?s\s+wrong\b",
]

_SIGNAL_RE = re.compile("|".join(_SIGNAL_PATTERNS), re.IGNORECASE)


def has_signal(message: str) -> bool:
    """Return True if the message contains a learning-candidate signal."""
    if not message:
        return False
    return _SIGNAL_RE.search(message) is not None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_learning_scanner_prefilter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pal/learning_scanner.py tests/test_learning_scanner_prefilter.py
git commit -m "feat: learning_scanner signal pre-filter"
```

---

## Task 16: Learning scanner - extraction call

**Files:**
- Modify: `pal/learning_scanner.py`
- Test: `tests/test_learning_scanner_extract.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_learning_scanner_extract.py
import asyncio
from unittest.mock import AsyncMock

import pytest

from pal.learning_scanner import extract_candidate


@pytest.fixture
def inference_returning(monkeypatch):
    """Inference stub returning a controlled response."""
    def _make(response_text: str):
        return AsyncMock(return_value=response_text)
    return _make


def test_extract_candidate_parses_json(inference_returning):
    stub = inference_returning('{"title": "T", "body": "B"}')
    result = asyncio.run(extract_candidate(
        recent_turns=[{"role": "user", "content": "you always merge"}],
        trigger_message="you always merge",
        inference_call=stub,
    ))
    assert result == {"title": "T", "body": "B"}


def test_extract_candidate_returns_none_on_null(inference_returning):
    stub = inference_returning("null")
    result = asyncio.run(extract_candidate(
        recent_turns=[],
        trigger_message="actually never mind",
        inference_call=stub,
    ))
    assert result is None


def test_extract_candidate_returns_none_on_malformed_json(inference_returning):
    stub = inference_returning("this is not json at all")
    result = asyncio.run(extract_candidate(
        recent_turns=[],
        trigger_message="you always",
        inference_call=stub,
    ))
    assert result is None


def test_extract_candidate_returns_none_on_timeout():
    async def slow(*args, **kwargs):
        await asyncio.sleep(30)
        return "{}"
    result = asyncio.run(extract_candidate(
        recent_turns=[],
        trigger_message="x",
        inference_call=slow,
        timeout=0.1,
    ))
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_learning_scanner_extract.py -v`
Expected: FAIL with `ImportError: cannot import name 'extract_candidate'`

- [ ] **Step 3: Add extraction**

Append to `pal/learning_scanner.py`:

```python
import asyncio
import json
from typing import Callable, Optional


_EXTRACTION_PROMPT = """You review a short conversation excerpt and decide whether a durable lesson is present.

A durable lesson is a behavioral preference, a correction, or a confirmed approach that should shape PAL's future behavior across sessions. It is NOT a one-off factual answer, a research topic, or a fleeting emotion.

Recent conversation (most recent last):
{conversation}

User signal message:
{trigger}

If a durable lesson is present, respond with JSON:
{{"title": "<short specific title>", "body": "<1-3 sentence lesson>"}}

If no durable lesson is present, respond with the bare word:
null

Respond with ONLY the JSON object or the word null. No prose."""


def _format_conversation(turns: list[dict]) -> str:
    if not turns:
        return "(no prior turns)"
    lines = []
    for t in turns:
        role = t.get("role", "user")
        content = (t.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else "(empty)"


async def extract_candidate(
    recent_turns: list[dict],
    trigger_message: str,
    inference_call: Callable,
    timeout: float = 15.0,
) -> Optional[dict]:
    """Ask the inference server whether a durable lesson is present.

    Returns {"title": str, "body": str} or None. Never raises.
    inference_call(prompt: str) -> str (async).
    """
    prompt = _EXTRACTION_PROMPT.format(
        conversation=_format_conversation(recent_turns),
        trigger=trigger_message,
    )
    try:
        raw = await asyncio.wait_for(inference_call(prompt), timeout=timeout)
    except (asyncio.TimeoutError, Exception) as exc:
        logger.warning("learning extraction failed: %s", exc)
        return None

    text = (raw or "").strip()
    if text.lower() == "null" or not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.info("learning extraction returned non-JSON: %s", text[:100])
        return None
    if not isinstance(parsed, dict):
        return None
    title = (parsed.get("title") or "").strip()
    body = (parsed.get("body") or "").strip()
    if not title or not body:
        return None
    return {"title": title, "body": body}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_learning_scanner_extract.py -v`
Expected: PASS (all four tests)

- [ ] **Step 5: Commit**

```bash
git add pal/learning_scanner.py tests/test_learning_scanner_extract.py
git commit -m "feat: learning_scanner extraction call with timeout and JSON parse"
```

---

## Task 17: Learning scanner - dedupe

**Files:**
- Modify: `pal/learning_scanner.py`
- Test: `tests/test_learning_scanner_dedupe.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_learning_scanner_dedupe.py
from pal.learning_scanner import is_duplicate_candidate


def test_exact_title_match_is_duplicate():
    existing = ["granularity-over-consolidation", "strategic-research-sprints"]
    assert is_duplicate_candidate("Granularity Over Consolidation", existing) is True


def test_near_title_match_is_duplicate():
    existing = ["granularity-over-consolidation"]
    # Different wording, same idea -> slug token overlap is high.
    assert is_duplicate_candidate("Granularity vs Consolidation", existing) is True


def test_distinct_title_is_not_duplicate():
    existing = ["granularity-over-consolidation"]
    assert is_duplicate_candidate("Prefer Typed Protobuf Schemas", existing) is False


def test_empty_existing_returns_false():
    assert is_duplicate_candidate("Anything", []) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_learning_scanner_dedupe.py -v`
Expected: FAIL with `ImportError: cannot import name 'is_duplicate_candidate'`

- [ ] **Step 3: Add the helper**

Append to `pal/learning_scanner.py`:

```python
def _slugify_title(title: str) -> str:
    slug = title.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _slug_tokens(slug: str) -> set[str]:
    return {t for t in slug.split("-") if len(t) > 2}


def is_duplicate_candidate(title: str, existing_slugs: list[str]) -> bool:
    """True if the candidate title matches an existing learning by exact slug
    or by high token overlap (Jaccard >= 0.6).
    """
    cand_slug = _slugify_title(title)
    if not cand_slug:
        return False
    if cand_slug in existing_slugs:
        return True
    cand_tokens = _slug_tokens(cand_slug)
    if not cand_tokens:
        return False
    for existing in existing_slugs:
        ex_tokens = _slug_tokens(existing)
        if not ex_tokens:
            continue
        overlap = len(cand_tokens & ex_tokens)
        union = len(cand_tokens | ex_tokens)
        if union and (overlap / union) >= 0.6:
            return True
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_learning_scanner_dedupe.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pal/learning_scanner.py tests/test_learning_scanner_dedupe.py
git commit -m "feat: learning_scanner dedupe via slug token Jaccard"
```

---

## Task 18: Learning scanner - orchestrator with backpressure

**Files:**
- Modify: `pal/learning_scanner.py`
- Test: `tests/test_learning_scanner_orchestrator.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_learning_scanner_orchestrator.py
import asyncio
from unittest.mock import AsyncMock
from pathlib import Path

from pal.learning import LearningManager
from pal.learning_scanner import LearningScanner


def test_scanner_emits_candidate_on_signal(tmp_path: Path):
    lm = LearningManager(tmp_path)
    emitted: list = []
    extractor = AsyncMock(return_value={"title": "Granularity", "body": "focused"})
    scanner = LearningScanner(
        learning_manager=lm,
        extractor=extractor,
        emit=lambda msg: emitted.append(msg),
    )
    asyncio.run(scanner.maybe_scan(
        recent_turns=[],
        latest_user_message="you always merge",
    ))
    assert len(emitted) == 1
    assert emitted[0].title == "Granularity"


def test_scanner_silent_on_no_signal(tmp_path: Path):
    lm = LearningManager(tmp_path)
    emitted: list = []
    extractor = AsyncMock(return_value={"title": "x", "body": "y"})
    scanner = LearningScanner(
        learning_manager=lm,
        extractor=extractor,
        emit=lambda msg: emitted.append(msg),
    )
    asyncio.run(scanner.maybe_scan(
        recent_turns=[],
        latest_user_message="tell me about IoT security",
    ))
    assert emitted == []
    extractor.assert_not_called()


def test_scanner_silent_on_duplicate(tmp_path: Path):
    lm = LearningManager(tmp_path)
    lm.add("Granularity Over Consolidation", "keep focused", source="conversation")
    emitted: list = []
    extractor = AsyncMock(return_value={"title": "Granularity Over Consolidation", "body": "x"})
    scanner = LearningScanner(
        learning_manager=lm,
        extractor=extractor,
        emit=lambda msg: emitted.append(msg),
    )
    asyncio.run(scanner.maybe_scan(
        recent_turns=[],
        latest_user_message="you always merge",
    ))
    assert emitted == []


def test_scanner_queues_while_proposal_pending(tmp_path: Path):
    lm = LearningManager(tmp_path)
    emitted: list = []
    extractor = AsyncMock(return_value={"title": "Another", "body": "x"})
    scanner = LearningScanner(
        learning_manager=lm,
        extractor=extractor,
        emit=lambda msg: emitted.append(msg),
    )
    scanner.mark_pending("prior-proposal-id")
    asyncio.run(scanner.maybe_scan(
        recent_turns=[],
        latest_user_message="you always merge",
    ))
    # Candidate is queued, not emitted.
    assert emitted == []
    assert len(scanner.queued) == 1


def test_scanner_drains_queue_when_cleared(tmp_path: Path):
    lm = LearningManager(tmp_path)
    emitted: list = []
    extractor = AsyncMock(return_value={"title": "q", "body": "x"})
    scanner = LearningScanner(
        learning_manager=lm,
        extractor=extractor,
        emit=lambda msg: emitted.append(msg),
    )
    scanner.mark_pending("p1")
    asyncio.run(scanner.maybe_scan(
        recent_turns=[],
        latest_user_message="you always do that",
    ))
    assert emitted == []
    scanner.clear_pending()
    assert len(emitted) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_learning_scanner_orchestrator.py -v`
Expected: FAIL with `ImportError: cannot import name 'LearningScanner'`

- [ ] **Step 3: Add the orchestrator**

Append to `pal/learning_scanner.py`:

```python
import uuid
from typing import Awaitable, Callable
from collections import deque

from pal.protocol import LearningCandidateProposalMessage


class LearningScanner:
    """Orchestrates signal detection, extraction, dedupe, and proposal emission.

    At most one proposal is active at a time. Additional candidates are
    queued and drained when `clear_pending` is called.
    """

    def __init__(
        self,
        learning_manager,
        extractor: Callable[..., Awaitable],
        emit: Callable[[LearningCandidateProposalMessage], None],
    ) -> None:
        self.lm = learning_manager
        self.extractor = extractor  # async: (recent_turns, trigger) -> dict | None
        self.emit = emit
        self._pending_id: str | None = None
        self.queued: deque[LearningCandidateProposalMessage] = deque()

    def mark_pending(self, proposal_id: str) -> None:
        self._pending_id = proposal_id

    def clear_pending(self) -> None:
        self._pending_id = None
        self._drain_queue()

    def _drain_queue(self) -> None:
        if self._pending_id is None and self.queued:
            msg = self.queued.popleft()
            self._pending_id = msg.proposal_id
            self.emit(msg)

    async def maybe_scan(
        self,
        recent_turns: list[dict],
        latest_user_message: str,
    ) -> None:
        if not has_signal(latest_user_message):
            return

        candidate = await self.extractor(recent_turns, latest_user_message)
        if candidate is None:
            return

        existing = [e["slug"] for e in self.lm.list()]
        if is_duplicate_candidate(candidate["title"], existing):
            return

        msg = LearningCandidateProposalMessage(
            proposal_id=uuid.uuid4().hex,
            title=candidate["title"],
            body=candidate["body"],
            trigger_excerpt=latest_user_message[:200],
        )

        if self._pending_id is not None:
            self.queued.append(msg)
            return

        self._pending_id = msg.proposal_id
        self.emit(msg)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_learning_scanner_orchestrator.py -v`
Expected: PASS (all five tests)

- [ ] **Step 5: Commit**

```bash
git add pal/learning_scanner.py tests/test_learning_scanner_orchestrator.py
git commit -m "feat: LearningScanner orchestrator with backpressure queue"
```

---

## Task 19: Daemon wires LearningScanner into the turn loop

**Files:**
- Modify: `pal/daemon.py`
- Test: `tests/test_daemon_scanner_hook.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_daemon_scanner_hook.py
"""End-to-end-ish: feed a signal user message through the daemon's
conversation path and assert the scanner's extractor is called.
"""
import asyncio
from unittest.mock import AsyncMock

# Use whichever daemon harness the project's existing tests use.
# If tests/test_daemon.py has a fixture, mirror it. Pseudocode below:

def test_scanner_runs_after_turn_on_signal(monkeypatch, tmp_path):
    # 1. Build a daemon with a stubbed inference client so the LLM turn is mockable.
    # 2. Replace the learning scanner's extractor with an AsyncMock that returns
    #    {"title": "T", "body": "B"}.
    # 3. Send a user message with a signal phrase.
    # 4. Assert the extractor was awaited exactly once and a
    #    LearningCandidateProposalMessage was emitted to the client.
    pass
```

This test is a stub; fill it using whatever fixture pattern the project already has for driving a daemon end-to-end. Look at any existing `tests/test_daemon*.py` file for the pattern, and mirror it. If no such test exists in the project, replace this task's test with a lighter integration test that invokes the scanner-wiring function directly.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_daemon_scanner_hook.py -v`
Expected: FAIL (scanner not yet wired).

- [ ] **Step 3: Wire the scanner in the daemon**

In `pal/daemon.py`:

1. Add to imports: `from pal.learning_scanner import LearningScanner, extract_candidate`
2. In `__init__` (around line 75 where wisdom/learning are constructed), add:

```python
        self.scanner = LearningScanner(
            learning_manager=self.learning,
            extractor=self._scanner_extractor,
            emit=lambda msg: self._emit_scanner_proposal(msg),
        )
```

3. Add helper methods:

```python
    async def _scanner_extractor(self, recent_turns, trigger_message):
        async def call(prompt: str) -> str:
            # Use the same inference client as the main LLM path, but a small
            # single-shot completion with no tools and a short max_tokens.
            return await self.inference.simple_complete(
                prompt=prompt,
                max_tokens=256,
                temperature=0.0,
            )
        return await extract_candidate(
            recent_turns=recent_turns,
            trigger_message=trigger_message,
            inference_call=call,
            timeout=15.0,
        )

    def _emit_scanner_proposal(self, msg) -> None:
        """Forward a scanner-generated proposal to the active connection.
        The active writer is tracked per-conversation; reuse the same
        mechanism other proposal emitters use.
        """
        # Follow whatever pattern the existing proposal emitters use in
        # this file (search for 'ReorgProposalMessage(' or
        # 'ResearchProposalMessage(' emission sites).
        pass  # implement
```

4. In the turn-completion path (where the daemon finishes streaming a response back to the user and before returning to the await-next-message loop), add:

```python
        # Proactive learning scan (fire-and-forget; never blocks the next turn).
        recent_turns = conv.recent_turns(n=6)
        asyncio.create_task(self.scanner.maybe_scan(
            recent_turns=recent_turns,
            latest_user_message=latest_user_msg,
        ))
```

Search `pal/daemon.py` for where a conversation turn completes (after the LLM streaming loop finishes) to find the right insertion point. If `Conversation` does not already have a `recent_turns(n)` method, add a simple one: return `self.messages[-n:]`.

5. In the approval-handler path for `LearningCandidateProposalMessage` (user clicks Approve), call `self.scanner.clear_pending()` after saving.

Note: `self.inference.simple_complete` is assumed to exist. If the inference client lacks a simple-completion method, add one or adapt to whatever lightweight call the codebase already exposes (look at how `compile_summary` or `summarize` calls inference).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_daemon_scanner_hook.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pal/daemon.py tests/test_daemon_scanner_hook.py
git commit -m "feat: daemon invokes LearningScanner after each turn"
```

---

## Task 20: Discord UI for learning candidate proposal

**Files:**
- Modify: `pal/discord_interactions.py`
- Test: `tests/test_discord_learning_candidate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_discord_learning_candidate.py
from pal.discord_interactions import build_learning_candidate_embed
from pal.protocol import LearningCandidateProposalMessage


def test_learning_candidate_embed_has_expected_fields():
    msg = LearningCandidateProposalMessage(
        proposal_id="lc-1",
        title="Granularity Over Consolidation",
        body="Keep articles focused.",
        trigger_excerpt="you always try to merge into one article",
    )
    embed = build_learning_candidate_embed(msg)
    combined = (embed.title or "") + "\n" + (embed.description or "")
    for field in embed.fields:
        combined += f"\n{field.name}: {field.value}"
    assert "Granularity Over Consolidation" in combined
    assert "Keep articles focused." in combined
    assert "you always try to merge" in combined
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_discord_learning_candidate.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Add embed builder and button handling**

In `pal/discord_interactions.py`:

```python
def build_learning_candidate_embed(msg: LearningCandidateProposalMessage) -> discord.Embed:
    embed = discord.Embed(
        title="PAL spotted a possible learning",
        color=discord.Color.green(),
    )
    embed.add_field(name="Title", value=msg.title, inline=False)
    embed.add_field(name="Body", value=msg.body[:1000], inline=False)
    embed.add_field(
        name="Triggered by",
        value=f"> {msg.trigger_excerpt[:300]}",
        inline=False,
    )
    return embed
```

Wire into the stream handler's isinstance branch. Buttons: Approve / Edit / Skip. Use custom_id scheme `learning:{proposal_id}:approve|edit|skip`. Extend `parse_button_custom_id` if needed.

On Approve: adapter sends back an approval message; daemon calls `learning.add(title, body)`, commits, clears scanner pending, drains any queued proposals.

On Edit: open a modal prefilled with title + body; submit becomes Approve with edited values.

On Skip: daemon discards, clears pending, drains queue.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_discord_learning_candidate.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pal/discord_interactions.py tests/test_discord_learning_candidate.py
git commit -m "feat: Discord embed + buttons for learning candidate proposal"
```

---

## Task 21: End-to-end integration test

**Files:**
- Test: `tests/test_learning_e2e.py`

- [ ] **Step 1: Write the integration test**

```python
# tests/test_learning_e2e.py
"""Full round-trip: user message -> scanner -> candidate -> approve -> file.

This test exercises the real LearningScanner + LearningManager, stubbing
only the inference extractor. It verifies the file lands in _learning/
with the right content.
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from pal.learning import LearningManager
from pal.learning_scanner import LearningScanner


def test_full_flow_to_disk(tmp_path: Path):
    lm = LearningManager(tmp_path)
    emitted: list = []

    async def fake_extract(recent, trigger):
        return {"title": "Granularity", "body": "keep focused"}

    scanner = LearningScanner(
        learning_manager=lm,
        extractor=fake_extract,
        emit=lambda msg: emitted.append(msg),
    )
    asyncio.run(scanner.maybe_scan(
        recent_turns=[{"role": "user", "content": "before"}],
        latest_user_message="you always merge into one article",
    ))
    assert len(emitted) == 1
    candidate = emitted[0]

    # Simulate user approve: daemon would do this.
    slug = lm.add(candidate.title, candidate.body, source="conversation")
    scanner.clear_pending()

    # File on disk
    path = tmp_path / "_learning" / f"{slug}.md"
    assert path.exists()
    text = path.read_text()
    assert "Granularity" in text
    assert "keep focused" in text
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_learning_e2e.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_learning_e2e.py
git commit -m "test: learning capture end-to-end from scanner to disk"
```

---

## Task 22: Final smoke - full test suite and regression check

- [ ] **Step 1: Run the entire test suite**

Run: `.venv/bin/pytest tests/ -v`
Expected: all tests pass including pre-existing ones.

- [ ] **Step 2: If any pre-existing test fails, investigate**

If `test_learning.py` or `test_reorg.py` or `test_tools.py` pre-existing tests fail, diff against `git log -p` on those files for changes introduced by this plan. Fix forward; do not revert unless the plan itself is wrong.

- [ ] **Step 3: Manual verification notes**

Manual tests (not automated; perform on the real server):

1. In Discord, send: `!wisdom add test em dash | PAL should not use em dashes`. Verify file appears in `/mnt/secondary/agent-workspace/vault/_wisdom/`.
2. Ask PAL in chat: "What commands can I use?" Verify the reply lists every command with `!` prefix (not `/`).
3. Send a message with a correction phrase such as "actually, you always do X." Wait for the LLM to finish replying. Within ~15s, expect a Discord embed "PAL spotted a possible learning" with Approve/Edit/Skip buttons.
4. Approve it. Verify a new file in `_learning/`. Run `!promote <slug>`. Verify a new file in `_wisdom/`.
5. Ask PAL to move an article with a direct request: "Move `Security/overview-of-embedded-penetration-testing-methodologies-and-challenges.md` to `IoT/overview-of-embedded-penetration-testing-methodologies-and-challenges.md`." Verify the file appears in the new location.

- [ ] **Step 4: Commit any final polish**

If the manual runs expose small issues (signal pattern too narrow/wide, embed text awkward, log verbosity), fix in a follow-up commit. Scope of this plan is complete once all automated tests pass and the manual steps above work.

---

## Summary of deliverables

- **New modules:** `pal/commands.py`, `pal/learning_scanner.py`.
- **New tools:** `add_learning`, `move_file`, `propose_promote`.
- **New protocol messages:** `PromoteProposalMessage`, `LearningCandidateProposalMessage`.
- **Registry-driven rendering:** `/help`, CLI splash, system prompt, Discord prefix rewrite.
- **Drift-check test:** daemon handlers vs. COMMANDS registry.
- **Full automated test coverage** for every new unit plus one end-to-end integration test.
- **Manual verification checklist** for the parts that touch real Discord / real inference server.
