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
