"""Integration tests for /search-web and /fetch commands."""
import asyncio

import pytest

from pal.client import PalClient
from pal.config import Config
from pal.daemon import Daemon


@pytest.fixture(autouse=True)
def _disable_blocklist(monkeypatch):
    """Tests use 127.0.0.1 mock server -- disable blocklist for test suite."""
    monkeypatch.setattr("agent_core.utils.fetcher.check_url_safety", lambda url: None)


@pytest.fixture()
async def web_daemon(socket_path, mock_inference_server, tmp_path):
    """Daemon using mock_inference_server as SearxNG endpoint too."""
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
async def test_search_web_returns_allowed_results(web_daemon, socket_path):
    """/search-web returns only results from allowlisted domains."""
    daemon, vault = web_daemon
    # Add the mock server host to allowlist so search results pass through
    import re
    host = re.sub(r"^https?://", "", daemon.websearch.base_url).split(":")[0]
    (vault / "_config" / "pal").mkdir(parents=True, exist_ok=True)
    (vault / "_config" / "pal" / "allowlist.md").write_text(
        f"# Allowlist\n\n- {host}\n"
    )

    client = PalClient(socket_path)
    await client.connect()

    resp = await client.command("search-web", "python")
    # Mock returns self-referencing URLs + evil.example.com
    # evil.example.com should be filtered out
    assert "evil.example.com" not in resp.text
    # Should have some results
    assert "Overview" in resp.text or "Tutorial" in resp.text or "python" in resp.text.lower()

    await client.close()


@pytest.mark.asyncio
async def test_search_web_empty_query(web_daemon, socket_path):
    daemon, vault = web_daemon
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="Usage"):
        await client.command("search-web", "")

    await client.close()


@pytest.mark.asyncio
async def test_search_web_seeds_allowlist_on_first_use(web_daemon, socket_path):
    daemon, vault = web_daemon
    # Seeding happens in Daemon __init__, so it should already exist
    assert (vault / "_config" / "pal" / "allowlist.md").exists()

    client = PalClient(socket_path)
    await client.connect()
    await client.command("search-web", "test")
    await client.close()

    assert (vault / "_config" / "pal" / "allowlist.md").exists()


@pytest.mark.asyncio
async def test_fetch_saves_to_raw_web(web_daemon, socket_path):
    """/fetch <url> pulls content, validates against allowlist, saves to raw/web/."""
    daemon, vault = web_daemon
    client = PalClient(socket_path)
    await client.connect()

    # Add the mock server host to allowlist so /fetch will accept it
    import re
    host = re.sub(r"^https?://", "", daemon.websearch.base_url).split(":")[0]
    (vault / "_config" / "pal").mkdir(parents=True, exist_ok=True)
    (vault / "_config" / "pal" / "allowlist.md").write_text(
        f"# Allowlist\n\n- {host}\n"
    )

    resp = await client.command("fetch", f"{daemon.websearch.base_url}/page.html")
    assert "Saved" in resp.text or "saved" in resp.text.lower()

    # File should exist in raw/web/
    raw_web = vault / "raw" / "web"
    assert raw_web.exists()
    files = list(raw_web.glob("*.md"))
    assert len(files) >= 1
    content = files[0].read_text()
    assert "main content" in content.lower() or "extract me" in content.lower()

    await client.close()


@pytest.mark.asyncio
async def test_fetch_rejects_non_allowlisted_url(web_daemon, socket_path):
    """/fetch refuses to fetch URLs not on the allowlist."""
    daemon, vault = web_daemon
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="not on allowlist"):
        await client.command("fetch", "https://evil.example.com/page")

    await client.close()


@pytest.mark.asyncio
async def test_fetch_empty_url(web_daemon, socket_path):
    daemon, vault = web_daemon
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="Usage"):
        await client.command("fetch", "")

    await client.close()
