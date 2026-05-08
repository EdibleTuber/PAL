"""Tests for PAL's update_scratch and add_learning Tool subclasses.

Phase F PR7: _legacy_tools.ToolExecutor is deleted. These tools are now
implemented as Tool subclasses in pal.tools.scratch and shadow the
framework builtins of the same names.

Tests use a lightweight HandlerContext stub to avoid the full agent setup.
"""
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pal.tools.scratch import UpdateScratch, AddLearning


# ---------------------------------------------------------------------------
# HandlerContext stub
# ---------------------------------------------------------------------------

class _FakeAgent:
    """Minimal agent stub for HandlerContext."""

    name = "pal"

    def __init__(self, vault: Path, learning=None, wiki=None, config=None):
        self.learning = learning
        self.wiki = wiki
        self.config = config
        self._vault = vault

    def _build_scratchpad(self, channel_id: str):
        from agent_core.scratchpad import Scratchpad
        def _commit(path, msg):
            if self.wiki is not None:
                self.wiki.git_commit(msg)
        return Scratchpad(
            vault_path=self._vault,
            agent_name=self.name,
            channel_id=channel_id,
            max_bytes=getattr(self.config, "scratchpad_max_bytes", 1024) if self.config else 1024,
            commit_callback=_commit,
        )


class _FakeConfig:
    def __init__(self, vault: Path, max_bytes: int = 1024):
        self.vault_path = vault
        self.scratchpad_max_bytes = max_bytes


class _FakeCtx:
    def __init__(self, agent, channel_id="C1"):
        self.agent = agent
        self.channel_id = channel_id


# ---------------------------------------------------------------------------
# UpdateScratch tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_scratch_writes_content(tmp_path: Path):
    from agent_core.scratchpad import Scratchpad

    wiki = MagicMock()
    wiki.git_commit = MagicMock()
    cfg = _FakeConfig(tmp_path)
    agent = _FakeAgent(tmp_path, wiki=wiki, config=cfg)
    ctx = _FakeCtx(agent)

    tool = UpdateScratch()
    result = await tool.run({"content": "new notes"}, ctx)
    assert "updated" in result.lower()

    sp = Scratchpad(vault_path=tmp_path, agent_name="pal", channel_id="C1", max_bytes=1024)
    assert sp.read() == "new notes"


@pytest.mark.asyncio
async def test_update_scratch_returns_error_on_oversize(tmp_path: Path):
    wiki = MagicMock()
    cfg = _FakeConfig(tmp_path, max_bytes=10)
    agent = _FakeAgent(tmp_path, wiki=wiki, config=cfg)
    ctx = _FakeCtx(agent)

    tool = UpdateScratch()
    result = await tool.run({"content": "x" * 20}, ctx)
    assert "error" in result.lower() or "too large" in result.lower()


@pytest.mark.asyncio
async def test_update_scratch_uses_content_parameter(tmp_path: Path):
    """Confirms the PAL override uses 'content' not 'text'."""
    assert "content" in UpdateScratch.parameters["properties"]
    assert "text" not in UpdateScratch.parameters["properties"]


# ---------------------------------------------------------------------------
# AddLearning tests
# ---------------------------------------------------------------------------

def _init_vault_git(vault: Path) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test",
           "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test"}
    subprocess.run(["git", "init"], cwd=vault, capture_output=True, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"],
                   cwd=vault, capture_output=True, check=True, env=env)


@pytest.mark.asyncio
async def test_add_learning_writes_file(tmp_path: Path):
    from agent_core.learning import LearningManager

    _init_vault_git(tmp_path)
    wiki = MagicMock()
    wiki.git_commit = MagicMock()
    lm = LearningManager(tmp_path, "pal")
    agent = _FakeAgent(tmp_path, learning=lm, wiki=wiki)
    ctx = _FakeCtx(agent)

    tool = AddLearning()
    result = await tool.run({
        "title": "Granularity Over Consolidation",
        "body": "Keep articles focused, not merged into master guides.",
    }, ctx)
    parsed = json.loads(result)
    slug = parsed["slug"]
    assert parsed["title"] == "Granularity Over Consolidation"
    lm2 = LearningManager(tmp_path, "pal")
    assert lm2.exists(slug)
    assert "focused" in lm2.get(slug)
    wiki.git_commit.assert_called_once()


@pytest.mark.asyncio
async def test_add_learning_rejects_empty_title(tmp_path: Path):
    from agent_core.learning import LearningManager

    lm = LearningManager(tmp_path, "pal")
    wiki = MagicMock()
    agent = _FakeAgent(tmp_path, learning=lm, wiki=wiki)
    ctx = _FakeCtx(agent)

    tool = AddLearning()
    result = await tool.run({"title": "", "body": "x"}, ctx)
    parsed = json.loads(result)
    assert "error" in parsed
    assert "title" in parsed["error"].lower()


@pytest.mark.asyncio
async def test_add_learning_rejects_empty_body(tmp_path: Path):
    from agent_core.learning import LearningManager

    lm = LearningManager(tmp_path, "pal")
    wiki = MagicMock()
    agent = _FakeAgent(tmp_path, learning=lm, wiki=wiki)
    ctx = _FakeCtx(agent)

    tool = AddLearning()
    result = await tool.run({"title": "x", "body": ""}, ctx)
    parsed = json.loads(result)
    assert "error" in parsed
    assert "body" in parsed["error"].lower()
