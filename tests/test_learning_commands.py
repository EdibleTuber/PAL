"""Integration tests for /learn, /learnings, /promote, /rate commands."""
import asyncio

import pytest

from pal.client import PalClient
from pal.config import Config
from pal.daemon import Daemon
from pal.inference import CompletionResult
from pal.learning import LearningManager
from pal.wisdom import WisdomManager


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
        channels_dir=tmp_path / "channels",
    )
    daemon = Daemon(cfg)
    task = asyncio.create_task(daemon.serve())
    for _ in range(100):
        if socket_path.exists():
            break
        await asyncio.sleep(0.01)
    yield daemon, tmp_path / "vault"
    daemon.shutdown()
    await task


@pytest.mark.asyncio
async def test_learn_extracts_from_conversation(learn_daemon, socket_path, monkeypatch):
    """After chatting, /learn extracts lessons from conversation history."""
    daemon, vault = learn_daemon

    from pal.protocol import StreamChunkMessage, ResponseMessage
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
    learning_files = list((vault / "_learning").glob("*.md"))
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

    wm = WisdomManager(vault)
    entries = wm.list()
    assert any("Good Rule" in e["title"] for e in entries)

    lm = LearningManager(vault)
    entries = lm.list()
    promoted = [e for e in entries if e["slug"] == "good-rule"]
    assert promoted[0]["status"] == "promoted"


@pytest.mark.asyncio
async def test_promote_nonexistent(learn_daemon, socket_path):
    daemon, vault = learn_daemon
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="not found"):
        await client.command("promote", "nonexistent")

    await client.close()


@pytest.mark.asyncio
async def test_rate_good(learn_daemon, socket_path):
    daemon, vault = learn_daemon
    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("rate", "good Great conversation")
    assert "good" in resp.text.lower()
    await client.close()

    ratings_path = vault / "_learning" / "ratings.md"
    assert ratings_path.exists()
    content = ratings_path.read_text()
    assert "**good**" in content
    assert "Great conversation" in content


@pytest.mark.asyncio
async def test_rate_empty(learn_daemon, socket_path):
    daemon, vault = learn_daemon
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="Usage"):
        await client.command("rate", "")

    await client.close()
