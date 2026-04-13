"""Integration tests for wiki slash commands via the daemon."""
import asyncio

import pytest

from pal.client import PalClient
from pal.config import Config
from pal.daemon import Daemon
from pal.protocol import ResponseMessage
from pal.wiki import WikiManager


@pytest.fixture()
def vault_path(tmp_path):
    return tmp_path / "vault"


@pytest.fixture()
async def wiki_daemon(socket_path, mock_inference_server, vault_path):
    """Start a daemon with wiki support pointing at a temp vault."""
    cfg = Config(
        inference_url=mock_inference_server,
        model="test-model",
        socket_path=socket_path,
        history_depth=50,
        vault_path=vault_path,
    )
    daemon = Daemon(cfg)
    task = asyncio.create_task(daemon.serve())
    for _ in range(100):
        if socket_path.exists():
            break
        await asyncio.sleep(0.01)
    yield daemon
    daemon.shutdown()
    await task


@pytest.mark.asyncio
async def test_note_command_creates_article(wiki_daemon, socket_path, vault_path):
    """The /note command creates a wiki article via inference."""
    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("note", "My Test Topic")
    assert "Created article:" in resp.text

    await client.close()


@pytest.mark.asyncio
async def test_read_command(wiki_daemon, socket_path, vault_path):
    """/read returns an article's content."""
    client = PalClient(socket_path)
    await client.connect()

    # Create an article first via the wiki manager directly
    wm = WikiManager(vault_path)
    wm.init_vault()
    wm.write_article(path="test.md", title="Test", body="# Test\n\nHello world.\n")

    resp = await client.command("read", "test.md")
    assert "Hello world." in resp.text

    await client.close()


@pytest.mark.asyncio
async def test_lint_command(wiki_daemon, socket_path, vault_path):
    """/lint reports vault health."""
    client = PalClient(socket_path)
    await client.connect()

    # Create a clean article
    wm = WikiManager(vault_path)
    wm.init_vault()
    wm.write_article(path="good.md", title="Good", body="Content.\n")

    resp = await client.command("lint")
    assert "issue" in resp.text.lower() or "clean" in resp.text.lower()

    await client.close()


@pytest.mark.asyncio
async def test_status_command_includes_vault(wiki_daemon, socket_path, vault_path):
    """/status now includes vault info."""
    client = PalClient(socket_path)
    await client.connect()

    wm = WikiManager(vault_path)
    wm.init_vault()
    wm.write_article(path="a.md", title="A", body="Content.\n")

    resp = await client.command("status")
    assert "Vault:" in resp.text or "vault" in resp.text.lower()

    await client.close()


@pytest.mark.asyncio
async def test_full_wiki_workflow(wiki_daemon, socket_path, vault_path):
    """Full workflow: create article, read it back, lint, check status."""
    client = PalClient(socket_path)
    await client.connect()

    # 1. Create an article via /note
    resp = await client.command("note", "Test Topic")
    assert "Created article:" in resp.text

    # 2. Check status shows the vault
    resp = await client.command("status")
    assert "vault" in resp.text.lower()

    # 3. Lint should pass on a well-formed vault
    resp = await client.command("lint")
    assert resp.text

    await client.close()


@pytest.mark.asyncio
async def test_daemon_rebuilds_index_on_startup(tmp_path, mock_inference_server):
    """Daemon startup should reconcile _index.md with actual vault state."""
    vault = tmp_path / "vault"
    vault.mkdir()
    # Seed a stale _index.md that doesn't reflect the article below.
    (vault / "_index.md").write_text("---\ntitle: Vault Index\n---\n\n# Vault Index\n\n_stale_\n")
    # Write an article directly to disk (bypassing WikiManager), simulating
    # external modification while the daemon was down.
    (vault / "Projects").mkdir()
    (vault / "Projects" / "external.md").write_text(
        "---\ntitle: External Article\n---\n\nBody.\n"
    )

    socket_path = tmp_path / "pal-test.sock"
    cfg = Config(
        inference_url=mock_inference_server,
        model="test-model",
        socket_path=socket_path,
        history_depth=50,
        vault_path=vault,
    )
    daemon = Daemon(cfg)
    task = asyncio.create_task(daemon.serve())
    for _ in range(100):
        if socket_path.exists():
            break
        await asyncio.sleep(0.01)

    try:
        index_text = (vault / "_index.md").read_text()
        assert "External Article" in index_text
        assert "Projects/external.md" in index_text
        assert "_stale_" not in index_text
    finally:
        daemon.shutdown()
        await task
