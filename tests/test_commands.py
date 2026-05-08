"""Tests for the pal.commands package (Command subclasses)."""
from agent_core.commands.base import Command

from pal.commands import (
    Compile, CompileBatch, Fetch, Get, Import, Learn, Lint, Note, PALModel,
    Profile, Read, Research, Scratch, Search, SearchWeb, Status, Summarize, Wisdom,
)


ALL_COMMANDS = [
    Read, Search, Get, Note, Lint, SearchWeb, Fetch, Summarize,
    Compile, CompileBatch, Import, Learn, Research,
    Status, Profile, Wisdom, Scratch, PALModel,
]

EXPECTED_NAMES = {
    "read", "search", "get", "note", "lint", "search-web", "fetch", "summarize",
    "compile", "compile-batch", "import", "learn", "research",
    "status", "profile", "wisdom", "scratch", "model",
}


def test_all_commands_are_command_subclasses():
    for cls in ALL_COMMANDS:
        assert issubclass(cls, Command), f"{cls} is not a Command subclass"


def test_every_command_has_required_attrs():
    for cls in ALL_COMMANDS:
        assert isinstance(cls.name, str) and cls.name, f"{cls} has no name"
        assert isinstance(cls.args, str), f"{cls} args is not a str"
        assert isinstance(cls.description, str) and cls.description, f"{cls} has no description"


def test_expected_command_names_present():
    names = {cls.name for cls in ALL_COMMANDS}
    missing = EXPECTED_NAMES - names
    assert not missing, f"missing commands in pal.commands: {missing}"


def test_read_metadata():
    assert Read.name == "read"
    assert Read.args == "<title>"
    assert "wiki" in Read.description.lower() or "read" in Read.description.lower()


def test_compile_metadata():
    assert Compile.name == "compile"
    assert Compile.args == "<t>"


def test_research_metadata():
    assert Research.name == "research"
    assert Research.args == "<t>"


def test_requires_are_tuples_of_strings():
    for cls in ALL_COMMANDS:
        assert isinstance(cls.requires, tuple), f"{cls}.requires is not a tuple"
        for attr in cls.requires:
            assert isinstance(attr, str), f"{cls}.requires contains non-str: {attr}"
