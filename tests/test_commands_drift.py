"""Registry completeness check: PALAgent.commands contains every expected name.

Phase F PR5: the old AST-based drift check (which scanned for _handle_X
methods in agent.py) is superseded by this test, which verifies that
PALAgent.commands lists all PAL-specific Command subclasses and that the
pal.commands package exports them all.
"""
from pal.agent import PALAgent
from pal.commands import (
    Import, Learn, Lint, PALModel, Profile, Scratch, Status, Wisdom,
)

EXPECTED_PAL_COMMANDS = {
    Import, Learn, Lint, PALModel, Profile, Scratch, Status, Wisdom,
}

EXPECTED_NAMES = {
    "import", "learn", "lint", "model", "profile", "scratch", "status", "wisdom",
}


def test_pal_agent_commands_contains_all_expected():
    registered = set(PALAgent.commands)
    missing = EXPECTED_PAL_COMMANDS - registered
    assert not missing, (
        f"PALAgent.commands is missing: {[cls.__name__ for cls in missing]}"
    )


def test_pal_agent_commands_names():
    names = {cls.name for cls in PALAgent.commands}
    missing = EXPECTED_NAMES - names
    assert not missing, f"PALAgent.commands missing names: {missing}"


def test_no_duplicate_names_in_pal_commands():
    names = [cls.name for cls in PALAgent.commands]
    assert len(names) == len(set(names)), f"Duplicate command names: {names}"
