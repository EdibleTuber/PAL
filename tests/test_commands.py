from pal.commands import COMMANDS, Command


def test_commands_registry_is_non_empty():
    assert len(COMMANDS) > 0


def test_every_command_has_shape():
    for cmd in COMMANDS:
        assert isinstance(cmd, Command)
        assert cmd.name and isinstance(cmd.name, str)
        assert isinstance(cmd.args, str)
        assert cmd.description and isinstance(cmd.description, str)


def test_expected_commands_present():
    names = {c.name for c in COMMANDS}
    expected = {
        "help", "status", "read", "search", "get", "scratch", "lint",
        "profile", "wisdom", "search-web", "fetch", "summarize",
        "compile", "compile-batch", "import", "learn", "learnings",
        "promote", "rate", "model", "think", "research", "quit",
    }
    missing = expected - names
    assert not missing, f"missing commands in registry: {missing}"
