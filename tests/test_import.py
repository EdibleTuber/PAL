"""Integration tests for /import command."""
import asyncio
from pathlib import Path

import pytest

from pal.client import PalClient
from pal.config import Config
from pal.daemon import Daemon
from pal.inference import CompletionResult


@pytest.fixture()
async def import_daemon(socket_path, mock_inference_server, tmp_path):
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
    daemon = Daemon(cfg)
    task = asyncio.create_task(daemon.serve())
    for _ in range(100):
        if socket_path.exists():
            break
        await asyncio.sleep(0.01)
    yield daemon, tmp_path / "vault"
    daemon.shutdown()
    await task


def _place_csv_in_raw(vault: Path, name: str, content: str) -> str:
    """Helper: place a CSV file in raw/ and return its relative path."""
    raw_dir = vault / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    csv_path = raw_dir / name
    csv_path.write_text(content)
    return f"raw/{name}"


@pytest.mark.asyncio
async def test_import_csv_creates_article(import_daemon, socket_path, monkeypatch):
    daemon, vault = import_daemon

    call_count = 0

    async def fake_complete(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Summarization
            return CompletionResult(type="text", content="A table of employees with names, roles, and departments.")
        elif call_count == 2:
            # Compilation
            return CompletionResult(type="text", content="# Employee Directory\n\nThe team consists of three members...")
        else:
            # Categorization
            return CompletionResult(type="text", content="Research")

    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    rel_path = _place_csv_in_raw(
        vault, "employees.csv",
        "Name,Role,Department\nAlice,Engineer,Platform\nBob,Designer,Product\n"
    )

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("import", rel_path)
    assert "Research/" in resp.text
    assert "Employee Directory" in resp.text or "employees" in resp.text.lower()
    await client.close()

    articles = list((vault / "Research").glob("*.md"))
    assert len(articles) == 1


@pytest.mark.asyncio
async def test_import_archives_source(import_daemon, socket_path, monkeypatch):
    daemon, vault = import_daemon

    call_count = 0

    async def fake_complete(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return CompletionResult(type="text", content="Summary of data.")
        elif call_count == 2:
            return CompletionResult(type="text", content="# Data Report\n\nContent.")
        else:
            return CompletionResult(type="text", content="Research")

    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    rel_path = _place_csv_in_raw(vault, "data.csv", "A,B\n1,2\n")

    client = PalClient(socket_path)
    await client.connect()
    await client.command("import", rel_path)
    await client.close()

    # Source file should be archived
    assert not (vault / "raw" / "data.csv").exists()
    assert (vault / "raw" / "archived" / "data.csv").exists()


@pytest.mark.asyncio
async def test_import_rejects_non_raw_path(import_daemon, socket_path):
    daemon, vault = import_daemon

    # Create a file outside raw/
    (vault / "Research").mkdir(parents=True, exist_ok=True)
    (vault / "Research" / "article.csv").write_text("A,B\n1,2\n")

    client = PalClient(socket_path)
    await client.connect()
    with pytest.raises(RuntimeError, match="raw/"):
        await client.command("import", "Research/article.csv")
    await client.close()


@pytest.mark.asyncio
async def test_import_rejects_unsupported_format(import_daemon, socket_path):
    daemon, vault = import_daemon
    raw_dir = vault / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "data.json").write_text('{"key": "value"}')

    client = PalClient(socket_path)
    await client.connect()
    with pytest.raises(RuntimeError, match="Unsupported"):
        await client.command("import", "raw/data.json")
    await client.close()


@pytest.mark.asyncio
async def test_import_empty_args(import_daemon, socket_path):
    daemon, vault = import_daemon
    client = PalClient(socket_path)
    await client.connect()
    with pytest.raises(RuntimeError, match="Usage"):
        await client.command("import", "")
    await client.close()


@pytest.mark.asyncio
async def test_import_path_traversal(import_daemon, socket_path):
    daemon, vault = import_daemon
    client = PalClient(socket_path)
    await client.connect()
    with pytest.raises(RuntimeError, match="Invalid"):
        await client.command("import", "raw/../../etc/passwd")
    await client.close()
