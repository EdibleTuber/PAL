from pal.cli import render_splash_commands
from pal.commands import COMMANDS


def test_splash_contains_every_command_name():
    text = render_splash_commands()
    for cmd in COMMANDS:
        assert f"/{cmd.name}" in text
