"""Tests for the system prompt content and assembly after PR6 migration.

PAL_BASE_PROMPT lives in pal/prompts/system.py.
PALAgent.system_prompt() assembles via framework render helpers.
_BasePALPromptAdapter is used by Compiler/Consolidator for no-channel-context builds.
"""
from pathlib import Path

import pytest

from agent_core.profile import ProfileManager
from agent_core.wisdom import WisdomManager
from pal.prompts.system import PAL_BASE_PROMPT
from pal.agent import _BasePALPromptAdapter


@pytest.fixture()
def vault(tmp_path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    return v


@pytest.fixture()
def adapter(vault) -> _BasePALPromptAdapter:
    profile = ProfileManager(vault, "test-agent", username="edible")
    wisdom = WisdomManager(vault, "test-agent")
    return _BasePALPromptAdapter(profile=profile, wisdom=wisdom)


def test_build_with_no_profile_or_wisdom(adapter):
    prompt = adapter.build()
    assert PAL_BASE_PROMPT in prompt


def test_build_includes_profile(adapter, vault):
    profile = ProfileManager(vault, "test-agent", username="edible")
    profile.write("## World\n\nLinux user.\n")
    # Rebuild adapter with updated profile
    wisdom = WisdomManager(vault, "test-agent")
    result = _BasePALPromptAdapter(profile=profile, wisdom=wisdom).build()
    assert PAL_BASE_PROMPT in result
    assert "## About the User" in result
    assert "Linux user." in result


def test_build_includes_wisdom(adapter, vault):
    wisdom = WisdomManager(vault, "test-agent")
    wisdom.add(title="Concise", body="Lead with the answer.")
    wisdom.add(title="Accurate", body="Verify claims.")
    profile = ProfileManager(vault, "test-agent", username="edible")
    result = _BasePALPromptAdapter(profile=profile, wisdom=wisdom).build()
    assert PAL_BASE_PROMPT in result
    assert "## Active Wisdom" in result
    assert "Lead with the answer." in result
    assert "Verify claims." in result


def test_build_includes_both(adapter, vault):
    ProfileManager(vault, "test-agent", username="edible").write("## Bio\n\nEngineer.\n")
    WisdomManager(vault, "test-agent").add(title="Rule", body="Measure twice.")
    profile = ProfileManager(vault, "test-agent", username="edible")
    wisdom = WisdomManager(vault, "test-agent")
    result = _BasePALPromptAdapter(profile=profile, wisdom=wisdom).build()
    assert "## About the User" in result
    assert "Engineer." in result
    assert "## Active Wisdom" in result
    assert "Measure twice." in result


def test_build_sections_ordered(adapter, vault):
    ProfileManager(vault, "test-agent", username="edible").write("## Bio\n\nEngineer.\n")
    WisdomManager(vault, "test-agent").add(title="Rule", body="Measure twice.")
    profile = ProfileManager(vault, "test-agent", username="edible")
    wisdom = WisdomManager(vault, "test-agent")
    result = _BasePALPromptAdapter(profile=profile, wisdom=wisdom).build()
    base_idx = result.find(PAL_BASE_PROMPT[:40])
    profile_idx = result.find("## About the User")
    wisdom_idx = result.find("## Active Wisdom")
    assert base_idx < profile_idx < wisdom_idx


def test_base_prompt_lists_real_tools():
    assert "search_vault" in PAL_BASE_PROMPT
    assert "search_web" in PAL_BASE_PROMPT
    assert "propose_research" in PAL_BASE_PROMPT
    assert "research_topic" in PAL_BASE_PROMPT
    assert "edit_file" in PAL_BASE_PROMPT
    assert "create_file" in PAL_BASE_PROMPT


def test_base_prompt_forbids_hallucinated_capability():
    lower = PAL_BASE_PROMPT.lower()
    assert "never claim you performed a tool action" in lower
    assert "never describe a capability" in lower


def test_base_prompt_instructs_injection_handling():
    lower = PAL_BASE_PROMPT.lower()
    assert "ignore previous instructions" in lower  # used as an example
    assert "data" in lower and "not commands" in lower


def test_base_prompt_specifies_research_flow():
    lower = PAL_BASE_PROMPT.lower()
    assert "propose_research" in lower
    assert "research_topic" in lower
    assert "blocks until the user" in lower or "blocks until" in lower


def test_base_prompt_routes_wiki_promotion_through_compile():
    # The prompt should list compile tools for wiki promotion
    assert "compile_summary" in PAL_BASE_PROMPT or "compile_batch" in PAL_BASE_PROMPT
    assert "create_file" in PAL_BASE_PROMPT  # still mentioned as a tool
    # The prompt should tell the model NOT to use create_file for wiki promotion.
    lower = PAL_BASE_PROMPT.lower()
    assert "do not use create_file" in lower or "do not use create_file or edit_file" in lower


def test_base_prompt_requires_inline_citations():
    lower = PAL_BASE_PROMPT.lower()
    assert "inline" in lower
    assert "each claim" in lower or "for each claim" in lower


def test_base_prompt_constrains_default_depth():
    lower = PAL_BASE_PROMPT.lower()
    assert "default depth is 3" in lower
    assert "do not inflate depth" in lower or "only propose higher depth" in lower


def test_base_prompt_lists_compile_tools():
    assert "compile_summary" in PAL_BASE_PROMPT
    assert "propose_compile_batch" in PAL_BASE_PROMPT
    assert "compile_batch" in PAL_BASE_PROMPT


def test_base_prompt_routes_wiki_promotion_through_compile_tools():
    lower = PAL_BASE_PROMPT.lower()
    assert "compile_summary" in PAL_BASE_PROMPT
    assert "propose_compile_batch" in PAL_BASE_PROMPT
    assert "do not use create_file" in lower


def test_base_prompt_mentions_consolidate_tool():
    assert "consolidate" in PAL_BASE_PROMPT.lower()
    assert "propose_consolidate" in PAL_BASE_PROMPT


def test_base_prompt_mentions_wait_for_reindex():
    assert "wait_for_reindex" in PAL_BASE_PROMPT
    assert "reindex" in PAL_BASE_PROMPT.lower()


def test_base_prompt_mentions_promote_synthesis_tool():
    assert "propose_promote_synthesis" in PAL_BASE_PROMPT


def test_base_prompt_includes_chat_promotion_nudge():
    lower = PAL_BASE_PROMPT.lower()
    assert "promote this thread" in lower or "promote this chat" in lower
    assert "once per conversation" in lower


def test_base_prompt_includes_banner_reaction_rule():
    assert "chat-derived synthesis" in PAL_BASE_PROMPT
    lower = PAL_BASE_PROMPT.lower()
    assert "previous chat" in lower or "prior conversation" in lower


def test_adapter_omits_scratchpad_section(adapter):
    # The base adapter (used by Compiler/Consolidator) has no channel context;
    # it must not include a Channel Scratchpad section.
    prompt = adapter.build()
    assert "Channel Scratchpad" not in prompt


def test_adapter_omits_commands_catalog(adapter):
    # The base adapter does not include a commands catalog — that's only in the
    # full PALAgent.system_prompt() assembly.
    prompt = adapter.build()
    assert "Available Commands" not in prompt
