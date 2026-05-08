"""The /help command must list all registered PAL and framework commands."""
from pal.agent import PALAgent
from agent_core.commands.builtin import BUILTIN_COMMANDS


def test_all_pal_commands_have_name_and_description():
    """All PAL-specific commands have non-empty name and description."""
    for cls in PALAgent.commands:
        assert cls.name, f"{cls} has no name"
        assert cls.description, f"{cls} has no description"


def test_help_would_include_all_registered_names():
    """The names that /help would render include all PAL + builtin commands."""
    pal_names = {cls.name for cls in PALAgent.commands}
    builtin_names = {cls.name for cls in BUILTIN_COMMANDS}
    all_names = pal_names | builtin_names

    # Key PAL commands
    for name in ("read", "search", "note", "compile", "research", "lint",
                 "fetch", "summarize", "import", "learn"):
        assert name in all_names, f"/{name} not in combined command set"

    # Key framework commands that should be present
    for name in ("help", "quit", "status", "think"):
        assert name in all_names, f"builtin /{name} not registered"
