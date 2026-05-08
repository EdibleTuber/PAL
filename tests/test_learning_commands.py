"""Integration tests for /learn, /learnings, /promote, /rate commands."""
import asyncio

import pytest

from agent_core.client import DaemonConnection as PalClient
from pal.config import Config

from tests.conftest import make_pal_agent, start_pal_daemon
from agent_core.inference import CompletionResult
from agent_core.learning import LearningManager
from agent_core.wisdom import WisdomManager


@pytest.fixture()
async def learn_daemon(socket_path, mock_inference_server, tmp_path):
    cfg = Config(
        inference_url=mock_inference_server,
        model="test-model",
        socket_path=socket_path,
        history_depth=50,
        vault_path=tmp_path / "vault",
        collection_id="vault",
        username="testuser",
        searxng_url=mock_inference_server,
        fetch_max_bytes=2_000_000,
        fetch_timeout=10,
    )
    daemon = make_pal_agent(cfg)
    task = await start_pal_daemon(daemon)
    for _ in range(100):
        if socket_path.exists():
            break
        await asyncio.sleep(0.01)
    yield daemon, tmp_path / "vault"
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_learn_extracts_from_conversation(learn_daemon, socket_path, monkeypatch):
    """After chatting, /learn extracts lessons from conversation history."""
    daemon, vault = learn_daemon

    from agent_core.protocol import StreamChunkMessage, ResponseMessage
    client = PalClient(socket_path)
    await client.connect()

    # Send a chat message so conversation has history
    async for msg in client.chat("How do I handle errors in Python?"):
        if isinstance(msg, ResponseMessage):
            break

    # Now mock inference for the /learn extraction
    async def fake_complete(messages, **kwargs):
        return CompletionResult(type="text", content=(
            "## Always handle specific exceptions\n"
            "Catch specific exception types rather than bare except.\n\n"
            "## Use try/finally for cleanup\n"
            "Ensure resources are released even when exceptions occur."
        ))
    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    resp = await client.command("learn")
    assert "learning" in resp.text.lower() or "extracted" in resp.text.lower()

    # Learnings should exist in the vault
    learning_files = list((vault / "_learning" / "pal").glob("*.md"))
    learning_files = [f for f in learning_files if f.stem != "ratings"]
    assert len(learning_files) >= 1

    await client.close()


@pytest.mark.asyncio
async def test_learn_with_empty_conversation(learn_daemon, socket_path):
    """Trying /learn with no conversation history returns an error."""
    daemon, vault = learn_daemon
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="No conversation"):
        await client.command("learn")

    await client.close()


@pytest.mark.asyncio
async def test_learnings_list(learn_daemon, socket_path):
    daemon, vault = learn_daemon
    daemon.learning.add(title="Lesson A", body="Body A.", source="conversation")
    daemon.learning.add(title="Lesson B", body="Body B.", source="conversation")

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("learnings")
    assert "Lesson A" in resp.text
    assert "Lesson B" in resp.text
    await client.close()


@pytest.mark.asyncio
async def test_learnings_list_empty(learn_daemon, socket_path):
    daemon, vault = learn_daemon
    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("learnings")
    assert "no learning" in resp.text.lower() or "empty" in resp.text.lower()
    await client.close()


@pytest.mark.asyncio
async def test_promote_moves_to_wisdom(learn_daemon, socket_path):
    daemon, vault = learn_daemon
    daemon.learning.add(title="Good Rule", body="Always validate input.", source="conversation")

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("promote", "good-rule")
    assert "promoted" in resp.text.lower() or "wisdom" in resp.text.lower()
    await client.close()

    wm = WisdomManager(vault, "pal")
    entries = wm.list()
    assert any("Good Rule" in e["title"] for e in entries)

    lm = LearningManager(vault, "pal")
    entries = lm.list()
    promoted = [e for e in entries if e["slug"] == "good-rule"]
    assert promoted[0]["status"] == "promoted"


@pytest.mark.asyncio
async def test_promote_nonexistent(learn_daemon, socket_path):
    """Promoting a non-existent learning returns a 'not found' text response.

    Phase F PR5: framework Promote yields ResponseMessage (not ErrorMessage)
    for not-found; the response text still communicates the error.
    """
    daemon, vault = learn_daemon
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("promote", "nonexistent")
    assert "not found" in resp.text.lower()

    await client.close()


@pytest.mark.asyncio
async def test_rate_good(learn_daemon, socket_path):
    daemon, vault = learn_daemon
    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("rate", "good Great conversation")
    assert "good" in resp.text.lower()
    await client.close()

    ratings_path = vault / "_learning" / "pal" / "ratings.md"
    assert ratings_path.exists()
    content = ratings_path.read_text()
    assert "**good**" in content
    assert "Great conversation" in content


@pytest.mark.asyncio
async def test_rate_empty(learn_daemon, socket_path):
    """Calling /rate with no args returns a usage message.

    Phase F PR5: framework Rate yields ResponseMessage for empty args.
    """
    daemon, vault = learn_daemon
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("rate", "")
    assert "usage" in resp.text.lower()

    await client.close()
