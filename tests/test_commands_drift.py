"""Registry completeness check: PALAgent.commands contains every expected name.

Phase F PR5: the old AST-based drift check (which scanned for _handle_X
methods in agent.py) is superseded by this test, which verifies that
PALAgent.commands lists all PAL-specific Command subclasses and that the
pal.commands package exports them all.
"""
from pal.agent import PALAgent
from pal.commands import (
    Compile, CompileBatch, Fetch, Get, Import, Learn, Lint, Note, PALModel,
    Profile, Read, Research, Scratch, Search, SearchWeb, Status, Summarize, Wisdom,
)

EXPECTED_PAL_COMMANDS = {
    Compile, CompileBatch, Fetch, Get, Import, Learn, Lint, Note, PALModel,
    Profile, Read, Research, Scratch, Search, SearchWeb, Status, Summarize, Wisdom,
}

EXPECTED_NAMES = {
    "compile", "compile-batch", "fetch", "get", "import", "learn", "lint",
    "note", "model", "profile", "read", "research", "scratch", "search",
    "search-web", "status", "summarize", "wisdom",
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
