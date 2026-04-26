"""Integration tests for /compile command."""
import asyncio

import pytest

from pal.client import PalClient
from pal.config import Config
from pal.daemon import Daemon
from pal.inference import CompletionResult
from pal.article import parse_article, TIMELINE_MARKER


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


def _write_summary_file(vault, path: str, body: str, title="Quantum Computing Basics",
                        source_url="https://example.com/quantum",
                        source_raw="raw/web/quantum-abc.md",
                        source_hash="abc123") -> None:
    """Helper: write a raw/summaries/ file with frontmatter."""
    from agent_core.utils.frontmatter import serialize_frontmatter
    full_path = vault / path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "title": title,
        "source_url": source_url,
        "source_raw": source_raw,
        "source_hash": source_hash,
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
            return CompletionResult(type="text", content="Research")  # categorize
        # No topic match call -- no articles exist in category yet
        else:
            return CompletionResult(  # compile
                type="text",
                content="## Overview\n\nQuantum computers use qubits.\n\n## Key Concepts\n\n- Superposition\n",
            )

    monkeypatch.setattr(daemon.inference, "complete", fake_complete)
    _write_summary_file(vault, "raw/summaries/quantum-abc.md",
                        "Quantum computers use qubits instead of bits.")

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("compile", "raw/summaries/quantum-abc.md")
    assert "Research/" in resp.text
    await client.close()

    research_files = list((vault / "Research").glob("*.md"))
    assert len(research_files) == 1
    content = research_files[0].read_text()
    assert "Quantum computers use qubits" in content
    assert TIMELINE_MARKER in content


@pytest.mark.asyncio
async def test_compile_preserves_provenance_chain(compile_daemon, socket_path, monkeypatch):
    daemon, vault = compile_daemon

    call_count = 0

    async def fake_complete(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return CompletionResult(type="text", content="Research")  # categorize
        else:
            return CompletionResult(  # compile
                type="text",
                content="## Overview\n\nContent based on summary.\n\n## Key Concepts\n\n- Key point\n",
            )

    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    _write_summary_file(vault, "raw/summaries/foo.md", "Summary body text.")

    client = PalClient(socket_path)
    await client.connect()
    await client.command("compile", "raw/summaries/foo.md")
    await client.close()

    research_file = list((vault / "Research").glob("*.md"))[0]
    article = parse_article(research_file.read_text())
    assert article.meta["sources"][0]["url"] == "https://example.com/quantum"
    assert article.meta["sources"][0]["hash"] == "abc123"
    assert "compiled_at" in article.meta


@pytest.mark.asyncio
async def test_compile_refuses_when_model_says_insufficient(compile_daemon, socket_path, monkeypatch):
    """If the model returns INSUFFICIENT:, nothing is saved."""
    daemon, vault = compile_daemon

    call_count = 0

    async def fake_complete(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return CompletionResult(type="text", content="Research")  # categorize
        else:
            return CompletionResult(  # compile
                type="text",
                content="INSUFFICIENT: The summary does not contain enough detail.",
            )

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
async def test_compile_refuses_oversized_body(compile_daemon, socket_path, monkeypatch):
    """compile_one must refuse bodies larger than max_body_chars without
    making an inference call, returning status 'too_large'."""
    daemon, vault = compile_daemon

    inference_called = False

    async def fail_if_called(messages, **kwargs):
        nonlocal inference_called
        inference_called = True
        raise AssertionError("inference must not be called on oversized body")

    monkeypatch.setattr(daemon.inference, "complete", fail_if_called)
    monkeypatch.setattr(daemon.compiler, "max_body_chars", 500)

    big_body = "z" * 2000
    _write_summary_file(vault, "raw/summaries/huge.md", big_body)

    result = await daemon.compiler.compile_one("raw/summaries/huge.md")
    assert result["status"] == "too_large"
    assert "exceeds compile limit" in result["reason"]
    assert inference_called is False


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
            return CompletionResult(type="text", content="Science")  # categorize
        else:
            return CompletionResult(  # compile
                type="text",
                content="## Overview\n\nQuantum computers use qubits.\n\n## Key Concepts\n\n- Superposition\n",
            )

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
            return CompletionResult(type="text", content="Research")  # categorize
        else:
            return CompletionResult(  # compile
                type="text",
                content="## Overview\n\nArticle content.\n\n## Key Concepts\n\n- Topic point\n",
            )

    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    # Create both the raw file and the summary file
    from agent_core.utils.frontmatter import serialize_frontmatter
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


@pytest.mark.asyncio
async def test_compile_produces_timeline_format(compile_daemon, socket_path, monkeypatch):
    """Compiled articles should have compiled truth + timeline sections."""
    daemon, vault = compile_daemon
    call_count = 0

    async def fake_complete(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return CompletionResult(type="text", content="Research")  # categorize
        else:
            return CompletionResult(  # compile
                type="text",
                content=(
                    "## Overview\n\n"
                    "Quantum computers use qubits instead of classical bits.\n\n"
                    "## Key Concepts\n\n"
                    "- **Superposition** - qubits can be in multiple states\n"
                    "- **Entanglement** - qubits can be correlated\n"
                ),
            )

    monkeypatch.setattr(daemon.inference, "complete", fake_complete)
    _write_summary_file(vault, "raw/summaries/quantum-abc.md",
                        "Quantum computers use qubits instead of bits. They leverage superposition.")

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("compile", "raw/summaries/quantum-abc.md")
    await client.close()

    research_files = list((vault / "Research").glob("*.md"))
    assert len(research_files) == 1
    content = research_files[0].read_text()
    assert TIMELINE_MARKER in content
    assert "## Overview" in content
    assert "## Key Concepts" in content
    assert "**Source:** https://example.com/quantum" in content
    assert "**Source hash:** abc123" in content


@pytest.mark.asyncio
async def test_compile_merge_updates_existing_article(compile_daemon, socket_path, monkeypatch):
    """Compiling a source that matches an existing article should merge."""
    daemon, vault = compile_daemon

    # Create an existing article
    from pal.article import Article, TimelineEntry, serialize_article as sa
    existing = Article(
        meta={
            "title": "Quantum Computing Basics",
            "created": "2026-04-10T10:00:00+00:00",
            "updated": "2026-04-10T10:00:00+00:00",
            "compiled_at": "2026-04-10T10:00:00+00:00",
            "status": "compiled",
            "sources": [{"url": "https://old.com/quantum", "hash": "old123", "added": "2026-04-10T10:00:00+00:00"}],
        },
        compiled_truth="## Overview\n\nOld quantum overview.\n\n## Key Concepts\n\n- Old concepts\n",
        timeline=[TimelineEntry(
            date="2026-04-10", source_label="old.com",
            source_url="https://old.com/quantum", source_hash="old123",
            added="2026-04-10T10:00:00+00:00", summary="Old source findings.",
        )],
    )
    research_dir = vault / "Research"
    research_dir.mkdir(parents=True, exist_ok=True)
    (research_dir / "quantum-computing-basics.md").write_text(sa(existing))

    daemon.wiki.rebuild_index()

    call_count = 0

    async def fake_complete(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return CompletionResult(type="text", content="Research")  # categorize
        elif call_count == 2:
            return CompletionResult(type="text", content="quantum-computing-basics.md")  # topic match
        else:
            return CompletionResult(  # merge compile
                type="text",
                content=(
                    "## Overview\n\n"
                    "Merged quantum overview with new info.\n\n"
                    "## Key Concepts\n\n"
                    "- Old concepts\n- New concepts from new source\n"
                ),
            )

    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    _write_summary_file(vault, "raw/summaries/quantum-new.md",
                        "New quantum findings about error correction.",
                        title="Quantum Computing Basics",
                        source_url="https://new.com/quantum",
                        source_hash="new456")

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("compile", "raw/summaries/quantum-new.md")
    await client.close()

    research_files = list(research_dir.glob("*.md"))
    assert len(research_files) == 1

    article = parse_article(research_files[0].read_text())
    assert "Merged quantum overview" in article.compiled_truth
    assert len(article.timeline) == 2
    assert article.timeline[0].source_url == "https://old.com/quantum"
    assert article.timeline[1].source_url == "https://new.com/quantum"
    assert article.meta["created"] == "2026-04-10T10:00:00+00:00"
    assert len(article.meta["sources"]) == 2


@pytest.mark.asyncio
async def test_compile_batch_empty_directory(compile_daemon, socket_path):
    """/compile-batch with no summaries should report no work to do."""
    daemon, vault = compile_daemon
    # Create the directory so the "no directory" error doesn't fire
    (vault / "raw" / "summaries").mkdir(parents=True, exist_ok=True)

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("compile-batch", "")
    await client.close()

    assert "No summaries" in resp.text


@pytest.mark.asyncio
async def test_compile_batch_processes_multiple_summaries(compile_daemon, socket_path, monkeypatch):
    """/compile-batch should compile all summaries and report counts."""
    daemon, vault = compile_daemon

    _write_summary_file(
        vault, "raw/summaries/topic-a.md",
        "Content about topic A.",
        title="Topic A", source_url="https://a.com/page", source_hash="aaa111",
    )
    _write_summary_file(
        vault, "raw/summaries/topic-b.md",
        "Content about topic B.",
        title="Topic B", source_url="https://b.com/page", source_hash="bbb222",
    )

    call_count = 0

    async def fake_complete(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        # Pattern: categorize -> compile, repeated for each summary
        # First summary: no articles exist, so topic match is skipped
        # Second summary: Research/ now has 1 article, so topic match IS called
        system = messages[0].get("content", "") if messages else ""
        if "filing an article" in system or "choosing where to file" in system.lower():
            return CompletionResult(type="text", content="Research")
        if "same topic as" in system or "existing wiki article" in system.lower():
            return CompletionResult(type="text", content="NONE")
        # Compile
        return CompletionResult(
            type="text",
            content=f"## Overview\n\nArticle {call_count}.\n\n## Key Concepts\n\n- Concept\n",
        )

    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("compile-batch", "")
    await client.close()

    assert "Batch compile complete" in resp.text
    assert "2 summaries processed" in resp.text

    # Both articles should be saved
    research_files = list((vault / "Research").glob("*.md"))
    assert len(research_files) == 2


@pytest.mark.asyncio
async def test_compile_batch_skips_dirty_backups(compile_daemon, socket_path, monkeypatch):
    """/compile-batch should ignore .dirty and .md.dirty backup files."""
    daemon, vault = compile_daemon

    _write_summary_file(
        vault, "raw/summaries/topic-x.md",
        "Content about topic X.",
        title="Topic X", source_url="https://x.com/page", source_hash="xxx111",
    )
    # Create a .md.dirty backup that should be ignored
    (vault / "raw" / "summaries" / "old-topic.md.dirty").write_text(
        "---\ntitle: Old\n---\nOld content.\n"
    )

    async def fake_complete(messages, **kwargs):
        system = messages[0].get("content", "") if messages else ""
        if "choosing where to file" in system.lower():
            return CompletionResult(type="text", content="Research")
        if "existing wiki article" in system.lower():
            return CompletionResult(type="text", content="NONE")
        return CompletionResult(
            type="text",
            content="## Overview\n\nContent.\n\n## Key Concepts\n\n- Concept\n",
        )

    monkeypatch.setattr(daemon.inference, "complete", fake_complete)

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("compile-batch", "")
    await client.close()

    assert "1 summaries processed" in resp.text
