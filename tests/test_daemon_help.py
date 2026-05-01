"""The /help handler must render from the COMMANDS registry."""
from pal.commands import COMMANDS
from pal.agent import render_help_text


def test_render_help_contains_every_command():
    text = render_help_text()
    for cmd in COMMANDS:
        assert f"/{cmd.name}" in text, f"command /{cmd.name} missing from help"


def test_render_help_includes_descriptions():
    text = render_help_text()
    for cmd in COMMANDS:
        assert cmd.description in text, f"description for /{cmd.name} missing"
