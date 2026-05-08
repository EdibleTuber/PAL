"""Tests for commands section in PALAgent.system_prompt() after PR6 migration.

PALAgent.system_prompt() uses framework render_commands_catalog() to build the
Available Commands section from the live command_registry.
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_core.agent import HandlerContext
from agent_core.runtime import _attach_registries
from pal.agent import PALAgent
from pal.config import PALConfig


def make_minimal_agent(tmp_path: Path) -> PALAgent:
    """Build a fully-wired PALAgent for prompt assembly testing."""
    from agent_core.allowlist import AllowlistManager
    from agent_core.approval_registry import ApprovalRegistry
    from agent_core.channels import ChannelStore
    from agent_core.inference import InferenceClient
    from agent_core.learning import LearningManager
    from agent_core.profile import ProfileManager
    from agent_core.retrieval import RetrievalClient
    from agent_core.utils.fetcher import URLFetcher
    from agent_core.websearch import WebSearchClient
    from agent_core.wisdom import WisdomManager

    cfg = PALConfig(
        inference_url="http://127.0.0.1:9999",
        model="test-model",
        socket_path=tmp_path / "pal.sock",
        vault_path=tmp_path / "vault",
    )
    agent = PALAgent()
    agent.config = cfg
    agent.profile = ProfileManager(cfg.vault_path, agent_name="pal", username="testuser")
    agent.wisdom = WisdomManager(cfg.vault_path, agent_name="pal")
    agent.learning = LearningManager(cfg.vault_path, agent_name="pal")
    agent.allowlist = AllowlistManager(cfg.vault_path, agent_name="pal")
    agent.approval_registry = ApprovalRegistry()
    agent.channels = ChannelStore(
        vault_path=cfg.vault_path, agent_name="pal", history_depth=cfg.history_depth,
    )
    agent.inference = InferenceClient(base_url=cfg.inference_url, model=cfg.model)
    agent.retrieval = RetrievalClient(
        base_url=cfg.inference_url, collection_id=cfg.collection_id,
    )
    agent.websearch = WebSearchClient(base_url=cfg.searxng_url)
    agent.fetcher = URLFetcher(max_bytes=cfg.fetch_max_bytes, timeout=cfg.fetch_timeout)
    agent.setup()
    _attach_registries(agent)
    return agent


def make_ctx(agent: PALAgent, channel_id: str = "test-channel") -> HandlerContext:
    ctx = MagicMock(spec=HandlerContext)
    ctx.channel_id = channel_id
    return ctx


def test_system_prompt_contains_commands_section(tmp_path: Path):
    agent = make_minimal_agent(tmp_path)
    ctx = make_ctx(agent)
    prompt = agent.system_prompt(ctx)

    assert "## Available Commands" in prompt
    # All PAL-specific commands should appear
    for cls in PALAgent.commands:
        assert f"/{cls.name}" in prompt, f"command /{cls.name} not in prompt"


def test_system_prompt_contains_base_prompt(tmp_path: Path):
    from pal.prompts.system import PAL_BASE_PROMPT
    agent = make_minimal_agent(tmp_path)
    ctx = make_ctx(agent)
    prompt = agent.system_prompt(ctx)

    assert "You are PAL," in prompt
    assert PAL_BASE_PROMPT in prompt
