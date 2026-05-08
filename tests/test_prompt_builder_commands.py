from pathlib import Path

from agent_core.profile import ProfileManager
from agent_core.wisdom import WisdomManager
from pal.agent import PALAgent
from pal.prompt_builder import SystemPromptBuilder


def test_system_prompt_contains_commands_section(tmp_path: Path):
    (tmp_path / "_wisdom").mkdir()
    (tmp_path / "_profile").mkdir()
    builder = SystemPromptBuilder(
        profile=ProfileManager(tmp_path, "test-agent", username="testuser"),
        wisdom=WisdomManager(tmp_path, "test-agent"),
    )
    prompt = builder.build()

    assert "## Available Commands" in prompt
    # All PAL-specific commands should appear
    for cls in PALAgent.commands:
        assert f"/{cls.name}" in prompt, f"command /{cls.name} not in prompt"


def test_system_prompt_with_explicit_metadata(tmp_path: Path):
    """When command_metadata is passed explicitly, it is used verbatim."""
    (tmp_path / "_wisdom").mkdir()
    (tmp_path / "_profile").mkdir()
    builder = SystemPromptBuilder(
        profile=ProfileManager(tmp_path, "test-agent", username="testuser"),
        wisdom=WisdomManager(tmp_path, "test-agent"),
    )
    metadata = [
        ("read", "<title>", "Read a wiki article"),
        ("search", "<q>", "Search wiki articles"),
    ]
    prompt = builder.build(command_metadata=metadata)

    assert "## Available Commands" in prompt
    assert "/read" in prompt
    assert "/search" in prompt
