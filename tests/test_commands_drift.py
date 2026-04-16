"""Drift check: every `msg.name == "foo"` branch in pal/daemon.py
must have a matching COMMANDS entry, and vice versa.
"""
import ast
from pathlib import Path

from pal.commands import command_names


DAEMON_PATH = Path(__file__).parent.parent / "pal" / "daemon.py"


def _collect_daemon_command_names() -> set[str]:
    """Parse daemon.py and collect every string compared against msg.name."""
    tree = ast.parse(DAEMON_PATH.read_text())
    found: set[str] = set()

    for node in ast.walk(tree):
        # msg.name == "foo"
        if isinstance(node, ast.Compare):
            if (
                isinstance(node.left, ast.Attribute)
                and node.left.attr == "name"
                and len(node.ops) == 1
                and isinstance(node.ops[0], ast.Eq)
                and len(node.comparators) == 1
                and isinstance(node.comparators[0], ast.Constant)
                and isinstance(node.comparators[0].value, str)
            ):
                found.add(node.comparators[0].value)
            # msg.name in ("foo", "bar")
            if (
                isinstance(node.left, ast.Attribute)
                and node.left.attr == "name"
                and len(node.ops) == 1
                and isinstance(node.ops[0], ast.In)
                and len(node.comparators) == 1
                and isinstance(node.comparators[0], (ast.Tuple, ast.List))
            ):
                for elt in node.comparators[0].elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        found.add(elt.value)
    return found


def test_no_command_drift():
    daemon_cmds = _collect_daemon_command_names()
    registry_cmds = command_names()

    # "exit" is a quit alias handled in a tuple branch; exempt it.
    daemon_cmds.discard("exit")

    missing_from_registry = daemon_cmds - registry_cmds
    missing_from_daemon = registry_cmds - daemon_cmds

    assert not missing_from_registry, (
        f"daemon handles these commands but COMMANDS registry does not list them: "
        f"{sorted(missing_from_registry)}"
    )
    assert not missing_from_daemon, (
        f"COMMANDS registry lists these but no daemon handler exists: "
        f"{sorted(missing_from_daemon)}"
    )
