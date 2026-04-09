"""Tests for strict /note mode — model must refuse to guess."""
import asyncio

import pytest

from pal.client import PalClient
from pal.config import Config
from pal.daemon import Daemon
from pal.inference import CompletionResult


@pytest.fixture()
async def strict_daemon(socket_path, mock_inference_server, tmp_path):
    cfg = Config(
        inference_url=mock_inference_server,
        model="test-model",
        socket_path=socket_path,
        history_depth=50,
        vault_path=tmp_path / "vault",
        collection_id="vault",
        username="testuser",
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
async def test_note_refuses_when_model_returns_unknown(strict_daemon, socket_path, monkeypatch):
    """If the model returns 'UNKNOWN: ...', /note does not save anything."""
    daemon, vault = strict_daemon

    async def fake_complete(messages, **kwargs):
        return CompletionResult(type="text", content="UNKNOWN: No reliable information on this topic.")
    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("note", "obscure topic")
    assert "UNKNOWN" in resp.text or "unknown" in resp.text.lower()
    assert not (vault / "obscure-topic.md").exists()

    await client.close()


@pytest.mark.asyncio
async def test_note_saves_when_model_responds_normally(strict_daemon, socket_path, monkeypatch):
    """If the model returns actual content, /note saves normally."""
    daemon, vault = strict_daemon

    call_count = 0

    async def fake_complete(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return CompletionResult(type="text", content="# Known Topic\n\nThis is confident content about a known topic.")
        else:
            return CompletionResult(type="text", content="Research")

    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("note", "known topic")
    assert "Created article:" in resp.text
    assert "Research/" in resp.text
    assert (vault / "Research" / "known-topic.md").exists()

    await client.close()


@pytest.mark.asyncio
async def test_note_uses_auto_categorization(strict_daemon, socket_path, monkeypatch):
    daemon, vault = strict_daemon

    call_count = 0

    async def fake_complete(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return CompletionResult(type="text", content="# Quantum Computing\n\nQuantum computers use qubits.")
        else:
            return CompletionResult(type="text", content="Science")

    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("note", "quantum computing")
    assert "Science/" in resp.text
    await client.close()

    assert (vault / "Science").exists()
