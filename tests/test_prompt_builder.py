"""Tests for SystemPromptBuilder — compose system prompt from base + profile + wisdom."""
from pathlib import Path

import pytest

from agent_core.profile import ProfileManager
from pal.prompt_builder import SystemPromptBuilder, BASE_PROMPT
from agent_core.wisdom import WisdomManager


@pytest.fixture()
def vault(tmp_path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    return v


@pytest.fixture()
def builder(vault) -> SystemPromptBuilder:
    profile = ProfileManager(vault, "test-agent", username="edible")
    wisdom = WisdomManager(vault, "test-agent")
    return SystemPromptBuilder(profile=profile, wisdom=wisdom)


def test_build_with_no_profile_or_wisdom(builder):
    prompt = builder.build()
    assert BASE_PROMPT in prompt
    assert "## Available Commands" in prompt


def test_build_includes_profile(builder, vault):
    profile = ProfileManager(vault, "test-agent", username="edible")
    profile.write("## World\n\nLinux user.\n")
    result = builder.build()
    assert BASE_PROMPT in result
    assert "## About the User" in result
    assert "Linux user." in result


def test_build_includes_wisdom(builder, vault):
    wisdom = WisdomManager(vault, "test-agent")
    wisdom.add(title="Concise", body="Lead with the answer.")
    wisdom.add(title="Accurate", body="Verify claims.")
    result = builder.build()
    assert BASE_PROMPT in result
    assert "## Active Wisdom" in result
    assert "Lead with the answer." in result
    assert "Verify claims." in result


def test_build_includes_both(builder, vault):
    ProfileManager(vault, "test-agent", username="edible").write("## Bio\n\nEngineer.\n")
    WisdomManager(vault, "test-agent").add(title="Rule", body="Measure twice.")
    result = builder.build()
    assert "## About the User" in result
    assert "Engineer." in result
    assert "## Active Wisdom" in result
    assert "Measure twice." in result


def test_build_sections_ordered(builder, vault):
    ProfileManager(vault, "test-agent", username="edible").write("## Bio\n\nEngineer.\n")
    WisdomManager(vault, "test-agent").add(title="Rule", body="Measure twice.")
    result = builder.build()
    base_idx = result.find(BASE_PROMPT)
    profile_idx = result.find("## About the User")
    wisdom_idx = result.find("## Active Wisdom")
    assert base_idx < profile_idx < wisdom_idx


def test_base_prompt_lists_real_tools():
    assert "search_vault" in BASE_PROMPT
    assert "search_web" in BASE_PROMPT
    assert "propose_research" in BASE_PROMPT
    assert "research_topic" in BASE_PROMPT
    assert "edit_file" in BASE_PROMPT
    assert "create_file" in BASE_PROMPT


def test_base_prompt_forbids_hallucinated_capability():
    lower = BASE_PROMPT.lower()
    assert "never claim you performed a tool action" in lower
    assert "never describe a capability" in lower


def test_base_prompt_instructs_injection_handling():
    lower = BASE_PROMPT.lower()
    assert "ignore previous instructions" in lower  # used as an example
    assert "data" in lower and "not commands" in lower


def test_base_prompt_specifies_research_flow():
    lower = BASE_PROMPT.lower()
    assert "propose_research" in lower
    assert "research_topic" in lower
    assert "blocks until the user" in lower or "blocks until" in lower


def test_base_prompt_routes_wiki_promotion_through_compile():
    # The prompt should list compile tools for wiki promotion
    assert "compile_summary" in BASE_PROMPT or "compile_batch" in BASE_PROMPT
    assert "create_file" in BASE_PROMPT  # still mentioned as a tool
    # The prompt should tell the model NOT to use create_file for wiki promotion.
    lower = BASE_PROMPT.lower()
    assert "do not use create_file" in lower or "do not use create_file or edit_file" in lower


def test_base_prompt_requires_inline_citations():
    lower = BASE_PROMPT.lower()
    assert "inline" in lower
    assert "each claim" in lower or "for each claim" in lower


def test_base_prompt_constrains_default_depth():
    lower = BASE_PROMPT.lower()
    assert "default depth is 3" in lower
    assert "do not inflate depth" in lower or "only propose higher depth" in lower


def test_base_prompt_lists_compile_tools():
    assert "compile_summary" in BASE_PROMPT
    assert "propose_compile_batch" in BASE_PROMPT
    assert "compile_batch" in BASE_PROMPT


def test_base_prompt_routes_wiki_promotion_through_compile_tools():
    lower = BASE_PROMPT.lower()
    assert "compile_summary" in BASE_PROMPT
    assert "propose_compile_batch" in BASE_PROMPT
    assert "do not use create_file" in lower


def test_base_prompt_mentions_consolidate_tool():
    from pal.prompt_builder import BASE_PROMPT
    assert "consolidate" in BASE_PROMPT.lower()
    assert "propose_consolidate" in BASE_PROMPT


def test_base_prompt_mentions_wait_for_reindex():
    from pal.prompt_builder import BASE_PROMPT
    assert "wait_for_reindex" in BASE_PROMPT
    assert "reindex" in BASE_PROMPT.lower()


def test_build_with_scratchpad_renders_section(builder):
    prompt = builder.build(channel_scratchpad="Phase 2 in progress")
    assert "Phase 2 in progress" in prompt
    assert "Channel Scratchpad" in prompt


def test_build_empty_scratchpad_omits_section(builder):
    prompt = builder.build(channel_scratchpad="")
    assert "Channel Scratchpad" not in prompt


def test_build_none_scratchpad_omits_section(builder):
    prompt = builder.build()  # no arg
    assert "Channel Scratchpad" not in prompt


def test_build_scratchpad_appears_between_wisdom_and_commands(builder, vault):
    WisdomManager(vault, "test-agent").add(title="Rule", body="some wisdom")
    prompt = builder.build(channel_scratchpad="scratch content")
    wisdom_idx = prompt.find("Active Wisdom")
    scratch_idx = prompt.find("Channel Scratchpad")
    commands_idx = prompt.find("Available Commands")

    assert wisdom_idx < scratch_idx < commands_idx
