from pathlib import Path

from pal.commands import COMMANDS
from pal.profile import ProfileManager
from pal.wisdom import WisdomManager
from pal.prompt_builder import SystemPromptBuilder


def test_system_prompt_contains_commands_section(tmp_path: Path):
    (tmp_path / "_wisdom").mkdir()
    (tmp_path / "_profile").mkdir()
    builder = SystemPromptBuilder(
        profile=ProfileManager(tmp_path, username="testuser"),
        wisdom=WisdomManager(tmp_path),
    )
    prompt = builder.build()

    assert "## Available Commands" in prompt
    for cmd in COMMANDS:
        assert f"/{cmd.name}" in prompt, f"command /{cmd.name} not in prompt"
