"""Drift check: every `msg.name == "foo"` branch in pal/agent.py
must have a matching COMMANDS entry, and vice versa.

Phase E: command dispatch was lifted from pal/daemon.py to PALAgent in
pal/agent.py; the AST scan target moved with it.
"""
import ast
from pathlib import Path

from pal.commands import command_names


DAEMON_PATH = Path(__file__).parent.parent / "pal" / "agent.py"


def _collect_daemon_command_names() -> set[str]:
    """Parse agent.py and collect every command name in the dispatch map.

    Phase E: dispatch is a `handler_map = { "name": self._handle_X, ... }`
    inside ``PALAgent.handle_command``. We walk for any ``ast.Dict`` whose
    string keys all map to attribute accesses starting with ``_handle_``;
    those are command-handler dispatch dicts.
    """
    tree = ast.parse(DAEMON_PATH.read_text())
    found: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        if not node.keys or not node.values:
            continue
        # Heuristic: all values are attribute accesses to ``_handle_*`` methods.
        all_handler_values = all(
            isinstance(v, ast.Attribute) and v.attr.startswith("_handle_")
            for v in node.values
        )
        if not all_handler_values:
            continue
        for k in node.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                found.add(k.value)
    return found


def test_no_command_drift():
    daemon_cmds = _collect_daemon_command_names()
    assert daemon_cmds, (
        "drift check collected zero command names from pal/agent.py - "
        "the AST dispatch pattern is likely stale, review the matcher"
    )
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
