"""Integration tests for /compile command."""
import asyncio

import pytest

from pal.client import PalClient
from pal.config import Config
from pal.daemon import Daemon
from pal.inference import CompletionResult


@pytest.fixture()
async def compile_daemon(socket_path, mock_inference_server, tmp_path):
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


def _write_summary_file(vault, path: str, body: str) -> None:
    """Helper: write a raw/summaries/ file with frontmatter."""
    from pal.frontmatter import serialize_frontmatter
    full_path = vault / path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "title": "Quantum Computing Basics",
        "source_url": "https://example.com/quantum",
        "source_raw": "raw/web/quantum-abc.md",
        "source_hash": "abc123",
        "summarized_at": "2026-04-05T12:00:00+00:00",
        "sanitization_issues": [],
        "status": "summary",
    }
    full_path.write_text(serialize_frontmatter(meta, body + "\n"))


@pytest.mark.asyncio
async def test_compile_creates_research_article(compile_daemon, socket_path, monkeypatch):
    daemon, vault = compile_daemon

    call_count = 0

    async def fake_complete(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: compilation
            return CompletionResult(type="text", content="# Quantum Computing Basics\n\nQuantum computers use qubits...")
        else:
            # Second call: categorization
            return CompletionResult(type="text", content="Research")

    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    _write_summary_file(
        vault,
        "raw/summaries/quantum-abc.md",
        "Quantum computers use qubits instead of bits. They leverage superposition.",
    )

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("compile", "raw/summaries/quantum-abc.md")
    assert "Research/" in resp.text
    await client.close()

    research_files = list((vault / "Research").glob("*.md"))
    assert len(research_files) == 1
    content = research_files[0].read_text()
    assert "Quantum computers use qubits" in content
    assert "source_url:" in content
    assert "source_summary:" in content


@pytest.mark.asyncio
async def test_compile_preserves_provenance_chain(compile_daemon, socket_path, monkeypatch):
    daemon, vault = compile_daemon

    call_count = 0

    async def fake_complete(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return CompletionResult(type="text", content="# Topic\n\nContent based on summary.")
        else:
            return CompletionResult(type="text", content="Research")

    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    _write_summary_file(vault, "raw/summaries/foo.md", "Summary body text.")

    client = PalClient(socket_path)
    await client.connect()
    await client.command("compile", "raw/summaries/foo.md")
    await client.close()

    from pal.frontmatter import parse_frontmatter
    research_file = list((vault / "Research").glob("*.md"))[0]
    meta, _ = parse_frontmatter(research_file.read_text())
    assert meta["source_url"] == "https://example.com/quantum"
    assert meta["source_summary"] == "raw/summaries/foo.md"
    assert meta["source_raw"] == "raw/web/quantum-abc.md"
    assert meta["source_hash"] == "abc123"
    assert "compiled_at" in meta


@pytest.mark.asyncio
async def test_compile_refuses_when_model_says_insufficient(compile_daemon, socket_path, monkeypatch):
    """If the model returns INSUFFICIENT:, nothing is saved."""
    daemon, vault = compile_daemon

    async def fake_complete(messages, **kwargs):
        return CompletionResult(type="text", content="INSUFFICIENT: The summary does not contain enough detail.")
    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    _write_summary_file(vault, "raw/summaries/thin.md", "Too brief.")

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("compile", "raw/summaries/thin.md")
    assert "INSUFFICIENT" in resp.text or "insufficient" in resp.text.lower()
    await client.close()

    assert not (vault / "Research").exists() or not list((vault / "Research").glob("*.md"))


@pytest.mark.asyncio
async def test_compile_missing_file(compile_daemon, socket_path):
    daemon, vault = compile_daemon
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="not found"):
        await client.command("compile", "raw/summaries/nonexistent.md")

    await client.close()


@pytest.mark.asyncio
async def test_compile_empty_args(compile_daemon, socket_path):
    daemon, vault = compile_daemon
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="Usage"):
        await client.command("compile", "")

    await client.close()


@pytest.mark.asyncio
async def test_compile_rejects_path_traversal(compile_daemon, socket_path):
    daemon, vault = compile_daemon
    client = PalClient(socket_path)
    await client.connect()

    with pytest.raises(RuntimeError, match="Invalid"):
        await client.command("compile", "../../etc/passwd")

    await client.close()


@pytest.mark.asyncio
async def test_compile_uses_auto_categorization(compile_daemon, socket_path, monkeypatch):
    """Compiled articles should be placed in the LLM-chosen directory."""
    daemon, vault = compile_daemon

    call_count = 0

    async def fake_complete(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: compilation
            return CompletionResult(type="text", content="# Quantum Computing Basics\n\nQuantum computers use qubits...")
        else:
            # Second call: categorization
            return CompletionResult(type="text", content="Science")

    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    _write_summary_file(
        vault,
        "raw/summaries/quantum-abc.md",
        "Quantum computers use qubits instead of bits.",
    )

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("compile", "raw/summaries/quantum-abc.md")
    assert "Science/" in resp.text
    await client.close()

    assert (vault / "Science").exists()
    articles = list((vault / "Science").glob("*.md"))
    assert len(articles) == 1


@pytest.mark.asyncio
async def test_compile_archives_raw_files(compile_daemon, socket_path, monkeypatch):
    """After successful compile, raw and summary files should be archived."""
    daemon, vault = compile_daemon

    call_count = 0

    async def fake_complete(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return CompletionResult(type="text", content="# Topic\n\nArticle content.")
        else:
            return CompletionResult(type="text", content="Research")

    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    # Create both the raw file and the summary file
    from pal.frontmatter import serialize_frontmatter
    raw_file = vault / "raw" / "web" / "quantum-abc.md"
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    raw_file.write_text(serialize_frontmatter({"title": "Raw"}, "raw body\n"))

    _write_summary_file(vault, "raw/summaries/quantum-abc.md", "Summary body text.")

    client = PalClient(socket_path)
    await client.connect()
    await client.command("compile", "raw/summaries/quantum-abc.md")
    await client.close()

    # Raw and summary should be archived
    assert not (vault / "raw" / "web" / "quantum-abc.md").exists()
    assert not (vault / "raw" / "summaries" / "quantum-abc.md").exists()
    assert (vault / "raw" / "archived" / "quantum-abc.md").exists()
    assert (vault / "raw" / "archived" / "quantum-abc.summary.md").exists()
