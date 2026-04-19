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

    rel_path = _place_csv_in_raw(
        vault, "employees.csv",
        "Name,Role,Department\nAlice,Engineer,Platform\nBob,Designer,Product\n"
    )

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("import", rel_path)
    assert "raw/sources/employees/" in resp.text
    await client.close()

    articles = list((vault / "raw" / "sources" / "employees").glob("*.md"))
    assert len(articles) >= 1
    content = articles[0].read_text()
    assert "Alice" in content
    # Frontmatter should reflect raw-first shape.
    assert "source_file: raw/employees.csv" in content
    assert "source_type: csv" in content
    assert "detection_method: headings" in content


@pytest.mark.asyncio
async def test_import_archives_source(import_daemon, socket_path, monkeypatch):
    daemon, vault = import_daemon

    rel_path = _place_csv_in_raw(
        vault, "data.csv",
        "a,b\n1,2\n"
    )

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("import", rel_path)
    await client.close()

    # Source has been moved to raw/archived/
    assert not (vault / rel_path).exists()
    assert (vault / "raw" / "archived" / "data.csv").exists()
    # Raw-sources output exists.
    assert (vault / "raw" / "sources" / "data").exists()


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


@pytest.mark.asyncio
async def test_import_converts_underscores_to_hyphens_in_slug(import_daemon, socket_path, monkeypatch):
    """Filenames with underscores should produce hyphenated doc slugs."""
    daemon, vault = import_daemon

    rel_path = _place_csv_in_raw(
        vault, "Agentic_Design_Patterns.csv",
        "Pattern,Description\nReAct,Reasoning and Acting\n"
    )

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("import", rel_path)
    await client.close()

    # The doc slug should hyphenate the underscored filename.
    doc_dir = vault / "raw" / "sources" / "agentic-design-patterns"
    assert doc_dir.exists()
    articles = list(doc_dir.glob("*.md"))
    assert len(articles) >= 1


@pytest.mark.asyncio
async def test_import_splits_multi_heading_document(import_daemon, socket_path, monkeypatch):
    """A document with multiple H1 headings should produce multiple articles."""
    daemon, vault = import_daemon

    # Create an HTML file with two H1 headings
    raw_dir = vault / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    md_file = raw_dir / "multi-chapter.html"
    md_file.write_text(
        "<html><body>"
        "<h1>Chapter One</h1><p>First chapter content with enough text to extract.</p>"
        "<h1>Chapter Two</h1><p>Second chapter content with enough text to extract.</p>"
        "</body></html>"
    )

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("import", "raw/multi-chapter.html")
    await client.close()

    # Should have created 2 articles under raw/sources/multi-chapter/
    articles = list((vault / "raw" / "sources" / "multi-chapter").glob("*.md"))
    assert len(articles) == 2
    assert "2 section" in resp.text


@pytest.mark.asyncio
async def test_import_single_chunk_still_works(import_daemon, socket_path, monkeypatch):
    """A document with no headings still produces a single article."""
    daemon, vault = import_daemon

    rel_path = _place_csv_in_raw(vault, "simple.csv", "A,B\n1,2\n3,4\n")

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("import", rel_path)
    await client.close()

    articles = list((vault / "raw" / "sources" / "simple").glob("*.md"))
    assert len(articles) == 1


@pytest.mark.asyncio
async def test_import_pdf_with_toc_produces_chapters(import_daemon, socket_path, tmp_path):
    import fitz

    daemon, vault = import_daemon

    # Build a synthetic PDF with a TOC and three trivial pages.
    pdf_path_on_disk = vault / "raw" / "test-book.pdf"
    pdf_path_on_disk.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for i, title in enumerate(["Introduction page", "Pattern page", "Conclusion page"]):
        page = doc.new_page()
        page.insert_text((72, 72), title, fontsize=18)
        page.insert_text((72, 120), f"Body text for page {i + 1}.", fontsize=11)
    doc.set_toc([
        [1, "Introduction", 1],
        [1, "The Pattern", 2],
        [1, "Conclusion", 3],
    ])
    doc.save(str(pdf_path_on_disk))
    doc.close()

    client = PalClient(socket_path)
    await client.connect()
    resp = await client.command("import", "raw/test-book.pdf")
    await client.close()

    assert "detection: toc" in resp.text
    out_dir = vault / "raw" / "sources" / "test-book"
    assert out_dir.exists()
    files = sorted(f.name for f in out_dir.glob("*.md"))
    assert files == ["01-introduction.md", "02-the-pattern.md", "03-conclusion.md"]

    # Archived source.
    assert not pdf_path_on_disk.exists()
    assert (vault / "raw" / "archived" / "test-book.pdf").exists()

    # Frontmatter sanity on one chapter.
    first = (out_dir / "01-introduction.md").read_text()
    assert "source_type: pdf" in first
    assert "detection_method: toc" in first
    assert "section_range: p.1-p.1" in first


@pytest.mark.asyncio
async def test_import_pdf_llm_toc_fallback_runs_on_main(import_daemon, socket_path, tmp_path):
    """When LLM-TOC triggers, the batch backend raises, and the user
    picks 'main', the retry should succeed on the main inference."""
    import fitz
    from pal.inference import BatchUnavailableError, CompletionResult

    daemon, vault = import_daemon

    # Build a synthetic PDF with no TOC and flat typography so both
    # tier-1 and tier-2 return None, forcing tier-3 LLM-TOC.
    pdf_path = vault / "raw" / "no-structure.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((72, 72), f"uniform body for page {i}", fontsize=11)
    doc.save(str(pdf_path))
    doc.close()

    # Mock: batch raises BatchUnavailable; main returns an empty TOC
    # (which also results in single-file fallback — we just want to
    # confirm main was called, not batch).
    batch_calls = {"n": 0}
    main_calls = {"n": 0}

    async def batch_complete(messages, **kwargs):
        batch_calls["n"] += 1
        raise BatchUnavailableError("batch down")

    async def main_complete(messages, **kwargs):
        main_calls["n"] += 1
        import json
        return CompletionResult(type="text", content=json.dumps([]))

    from unittest.mock import AsyncMock
    daemon.batch_inference = AsyncMock()
    daemon.batch_inference.complete.side_effect = batch_complete
    daemon.batch_inference.is_batch = True
    daemon.inference.complete = main_complete

    # Auto-approve the BatchFallbackProposal with state="main" when it
    # arrives. We do this by patching _handle_connection's local
    # emit_proposal indirectly: intercept the writer.write path.
    # Simplest: patch approval_registry.create_proposal to immediately
    # mark the proposal as approved with state="main".
    import pal.approval_registry
    original_create = pal.approval_registry.ApprovalRegistry.create_proposal

    def auto_approve_create(self, *args, **kwargs):
        pid = original_create(self, *args, **kwargs)
        if kwargs.get("kind") == "batch_fallback":
            self.approve(pid, state="main")
        return pid

    monkeypatch_obj = pytest.MonkeyPatch()
    monkeypatch_obj.setattr(
        pal.approval_registry.ApprovalRegistry,
        "create_proposal",
        auto_approve_create,
    )
    try:
        client = PalClient(socket_path)
        await client.connect()
        resp = await client.command("import", "raw/no-structure.pdf")
        await client.close()
    finally:
        monkeypatch_obj.undo()

    assert batch_calls["n"] >= 1, "batch inference should have been attempted"
    assert main_calls["n"] >= 1, "main inference should have been the fallback"
    # Import succeeded to some form (either chapters or single-file); any
    # response text means we didn't crash on BatchUnavailableError.
    assert resp.text
