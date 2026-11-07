"""PAL on agent_core 1.13 (inference-server phase 6, Task 7; Q18).

agent_core 1.13's builtin /model replaces PAL's own (it shows every slot,
lists, resets the session pointer and swaps any slot the manager allows,
with the manager token on every call), and the manager now refuses any
request without its token, so every client PAL builds for the manager must
carry config.manager_token.
"""
import ast
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import pal.commands
from agent_core.commands.builtin import BUILTIN_COMMANDS
from agent_core.inference import InferenceClient
from pal.agent import PALAgent
from pal.config import PALConfig
from tests.conftest import make_pal_agent

REPO = Path(__file__).resolve().parent.parent
AGENT_CORE_MODEL = next(c for c in BUILTIN_COMMANDS if c.name == "model")


def _config(tmp_path, **overrides) -> PALConfig:
    return PALConfig(
        socket_path=tmp_path / "pal.sock",
        vault_path=tmp_path / "vault",
        **overrides,
    )


# --- /model is agent_core's ---------------------------------------------------

def test_pal_registers_agent_cores_model_command(tmp_path):
    assert "model" not in {c.name for c in PALAgent.commands}, \
        "a PAL command named model would shadow agent_core's"
    agent = make_pal_agent(_config(tmp_path))
    assert type(agent.command_registry._commands["model"]) is AGENT_CORE_MODEL


@pytest.mark.parametrize("name", [
    "_get_manager_status", "_model_status_text",
    "_dispatch_model_command", "_request_model_swap",
])
def test_pals_own_model_helpers_are_gone(name):
    assert not hasattr(PALAgent, name)


def test_pal_commands_no_longer_exports_its_model_command():
    assert not hasattr(pal.commands, "PALModel")
    assert "PALModel" not in pal.commands.__all__


# --- the manager token --------------------------------------------------------

@pytest.mark.parametrize("batch_url", [None, "http://127.0.0.1:9999"],
                         ids=["same-manager", "own-url"])
def test_the_batch_client_gets_the_token(tmp_path, batch_url):
    overrides = {"batch_inference_url": batch_url} if batch_url else {}
    agent = make_pal_agent(_config(
        tmp_path, batch_enabled=True, manager_token="tok-batch", **overrides))
    assert agent.batch_inference is not None
    assert agent.batch_inference.token == "tok-batch"


def test_the_backfill_client_gets_the_token(tmp_path, monkeypatch):
    import pal.backfill_main as backfill_main

    made = []

    class Recorder:
        def __init__(self, *args, **kwargs):
            bound = inspect.signature(InferenceClient).bind(*args, **kwargs)
            made.append(bound.arguments)

    async def fake_backfill(**kwargs):
        return SimpleNamespace(processed=0, updated=0, skipped_clean=0,
                               skipped_error=0, changes=[])

    monkeypatch.setattr(backfill_main, "InferenceClient", Recorder)
    monkeypatch.setattr(backfill_main, "backfill_titles", fake_backfill)
    monkeypatch.setattr(backfill_main, "WikiManager", lambda path: object())
    monkeypatch.setenv("PAL_MANAGER_TOKEN", "tok-backfill")
    monkeypatch.setattr(sys, "argv",
                        ["pal-backfill-titles", "--vault", str(tmp_path)])
    backfill_main.main()
    assert [m.get("token") for m in made] == ["tok-backfill"]


def _manager_client_calls():
    """Every InferenceClient(...) or RetrievalClient(...) call in PAL's code
    and scripts, as (path:line, keyword names)."""
    for root in ("pal", "scripts"):
        for path in sorted((REPO / root).rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = (func.id if isinstance(func, ast.Name)
                        else func.attr if isinstance(func, ast.Attribute)
                        else None)
                if name in ("InferenceClient", "RetrievalClient"):
                    yield (f"{path.relative_to(REPO)}:{node.lineno}",
                           {k.arg for k in node.keywords})


def test_every_pal_manager_client_is_given_the_token():
    """Stops the clock: a client added later without token= fails here."""
    calls = list(_manager_client_calls())
    assert calls, "found no client constructions; the scan is broken"
    missing = [where for where, keywords in calls if "token" not in keywords]
    assert missing == [], f"built without token=: {missing}"
