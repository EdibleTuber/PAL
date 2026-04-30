"""Tests for vault tool execution."""
import subprocess
import os
import pytest
from pathlib import Path

from pal.tools import ToolExecutor
from pal.wiki import WikiManager


@pytest.fixture()
def vault(tmp_path) -> Path:
    """Create a minimal vault structure for tool tests."""
    # Public articles
    research = tmp_path / "Research"
    research.mkdir()
    (research / "quantum.md").write_text(
        "---\ntitle: Quantum Computing\ntags:\n- physics\n---\n\n# Quantum Computing\n\nQubits are neat.\n"
    )
    (research / "ml.md").write_text(
        "---\ntitle: Machine Learning\n---\n\n# Machine Learning\n\nNeural nets.\n"
    )
    # Raw directory
    raw = tmp_path / "raw" / "web"
    raw.mkdir(parents=True)
    (raw / "page-abc.md").write_text(
        "---\ntitle: Fetched Page\n---\n\nRaw fetched content.\n"
    )
    # System directory (should be hidden from list_directory)
    wisdom = tmp_path / "_wisdom"
    wisdom.mkdir()
    (wisdom / "be-kind.md").write_text("---\ntitle: Be Kind\n---\n\nBe kind.\n")
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial vault"],
        cwd=tmp_path, capture_output=True, check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test"},
    )
    return tmp_path


@pytest.fixture()
def wiki_executor(vault) -> ToolExecutor:
    """ToolExecutor with a WikiManager for write tests."""
    wiki = WikiManager(vault)
    return ToolExecutor(vault_path=vault, retrieval=None, wiki=wiki)


def test_read_file(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("read_file", {"path": "Research/quantum.md"})
    assert "Quantum Computing" in result
    assert "Qubits are neat." in result


def test_read_file_not_found(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("read_file", {"path": "Research/nonexistent.md"})
    assert "not found" in result.lower()


def test_read_file_path_traversal(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("read_file", {"path": "../../etc/passwd"})
    assert "outside vault" in result.lower() or "escapes" in result.lower()


def test_list_directory_root(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("list_directory", {})
    assert "Research/" in result
    assert "raw/" in result
    # System dirs should be excluded
    assert "_wisdom" not in result


def test_list_directory_subdir(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("list_directory", {"path": "Research"})
    assert "quantum.md" in result
    assert "ml.md" in result


def test_list_directory_not_found(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("list_directory", {"path": "nonexistent"})
    assert "not found" in result.lower()


@pytest.fixture()
def big_dir_vault(tmp_path) -> Path:
    """A vault with one directory holding 120 files (bigger than default limit=50)."""
    big = tmp_path / "AI"
    big.mkdir()
    for i in range(120):
        stem = f"{i:03d}-topic"
        (big / f"{stem}.md").write_text(f"---\ntitle: {stem}\n---\n\nbody\n")
    for name in ("alpha-note.md", "beta-note.md"):
        (big / name).write_text(f"---\ntitle: {Path(name).stem}\n---\n\nbody\n")
    return tmp_path


def test_list_directory_truncates_large_dir(big_dir_vault):
    executor = ToolExecutor(vault_path=big_dir_vault, retrieval=None)
    result = executor.run("list_directory", {"path": "AI"})
    assert "000-topic.md" in result
    assert "049-topic.md" in result
    assert "050-topic.md" not in result
    assert "showing 1-50 of 122" in result.lower()
    assert "offset=50" in result


def test_list_directory_offset_paging(big_dir_vault):
    executor = ToolExecutor(vault_path=big_dir_vault, retrieval=None)
    result = executor.run("list_directory", {"path": "AI", "offset": 50})
    assert "050-topic.md" in result
    assert "099-topic.md" in result
    assert "049-topic.md" not in result
    # Still 122 total, 72 remaining at this offset (50-121) - fits under default limit of 50
    assert "showing" in result.lower()


def test_list_directory_prefix_filter(big_dir_vault):
    executor = ToolExecutor(vault_path=big_dir_vault, retrieval=None)
    result = executor.run("list_directory", {"path": "AI", "prefix": "alpha"})
    assert "alpha-note.md" in result
    assert "beta-note.md" not in result
    assert "000-topic.md" not in result


def test_list_directory_custom_limit(big_dir_vault):
    executor = ToolExecutor(vault_path=big_dir_vault, retrieval=None)
    result = executor.run("list_directory", {"path": "AI", "limit": 10})
    assert "000-topic.md" in result
    assert "009-topic.md" in result
    assert "010-topic.md" not in result
    assert "showing 1-10 of 122" in result.lower()


def test_unknown_tool(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("delete_everything", {})
    assert "unknown tool" in result.lower()


def test_search_content_found(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("search_content", {"query": "Qubits"})
    assert "quantum.md" in result
    assert "Qubits" in result


def test_search_content_not_found(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("search_content", {"query": "zzznoexist"})
    assert "no results" in result.lower()


def test_search_content_skips_system_dirs(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("search_content", {"query": "Be kind"})
    assert "no results" in result.lower()


def test_search_content_empty_query(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("search_content", {"query": ""})
    assert "error" in result.lower()


@pytest.mark.asyncio
async def test_search_vault_no_retrieval(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = await executor.run_async("search_vault", {"query": "quantum"})
    assert "not available" in result.lower()


class _FakeRetrieval:
    def __init__(self, results):
        self._results = results

    async def search(self, query, limit=5, tags=None):
        return self._results


@pytest.mark.asyncio
async def test_search_vault_surfaces_vault_relative_path(vault):
    """The output must include a path a follow-up tool (edit_file, consolidate) can use.

    Regression: previously only the `name` field (bare filename stem or title) was
    shown, forcing the model to guess the directory and causing 'source not found'
    when the guess was passed to consolidate.
    """
    retrieval = _FakeRetrieval([
        {
            "id": "Security/comparison-of-container-isolation",
            "name": "Comparison of Container Isolation",
            "collection": "wiki",
            "summary": "gVisor vs Kata vs Firecracker sandboxing tradeoffs",
            "tags": ["security"],
            "score": 0.87,
        },
    ])
    executor = ToolExecutor(vault_path=vault, retrieval=retrieval)
    result = await executor.run_async("search_vault", {"query": "sandboxing"})
    assert "Security/comparison-of-container-isolation.md" in result


def test_edit_file(wiki_executor, vault):
    result = wiki_executor.run("edit_file", {
        "path": "Research/quantum.md",
        "content": "# Quantum Computing\n\n## Overview\n\nQubits are the building blocks.\n",
    })
    assert "updated" in result.lower()
    text = (vault / "Research" / "quantum.md").read_text()
    assert "building blocks" in text
    assert "title: Quantum Computing" in text
    assert "physics" in text


def test_edit_file_not_found(wiki_executor):
    result = wiki_executor.run("edit_file", {
        "path": "Research/nonexistent.md",
        "content": "new content",
    })
    assert "does not exist" in result.lower()


def test_edit_file_system_dir(wiki_executor):
    result = wiki_executor.run("edit_file", {
        "path": "_wisdom/be-kind.md",
        "content": "new content",
    })
    assert "not allowed" in result.lower()


def test_edit_file_path_traversal(wiki_executor):
    result = wiki_executor.run("edit_file", {
        "path": "../../etc/passwd",
        "content": "hacked",
    })
    assert "escapes" in result.lower() or "outside vault" in result.lower()


def test_edit_file_git_commits(wiki_executor, vault):
    wiki_executor.run("edit_file", {
        "path": "Research/quantum.md",
        "content": "# Quantum\n\nRewritten.\n",
    })
    result = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=vault, capture_output=True, text=True,
    )
    assert "edit" in result.stdout.lower()


def test_create_file(wiki_executor, vault):
    result = wiki_executor.run("create_file", {
        "path": "raw/notes/newtons-laws.md",
        "title": "Newton's Laws",
        "content": "# Newton's Laws\n\nThree laws of motion.\n",
        "tags": ["physics"],
    })
    assert "created" in result.lower()
    text = (vault / "raw" / "notes" / "newtons-laws.md").read_text()
    assert "Newton's Laws" in text
    assert "Three laws" in text
    assert "physics" in text


def test_create_file_already_exists(wiki_executor, vault):
    (vault / "raw" / "notes").mkdir(parents=True, exist_ok=True)
    (vault / "raw" / "notes" / "quantum.md").write_text("existing")
    result = wiki_executor.run("create_file", {
        "path": "raw/notes/quantum.md",
        "title": "Quantum",
        "content": "duplicate",
    })
    assert "already exists" in result.lower()


def test_create_file_system_dir(wiki_executor):
    result = wiki_executor.run("create_file", {
        "path": "_wisdom/new-wisdom.md",
        "title": "New Wisdom",
        "content": "some wisdom",
    })
    assert "not allowed" in result.lower()


def test_create_file_rejects_promoted_category(wiki_executor):
    result = wiki_executor.run("create_file", {
        "path": "Research/newtons-laws.md",
        "title": "Newton's Laws",
        "content": "# Newton's Laws\n\nThree laws.\n",
    })
    assert "scoped to raw/" in result


def test_create_file_creates_parent_dirs(wiki_executor, vault):
    result = wiki_executor.run("create_file", {
        "path": "raw/notes/subtopic/article.md",
        "title": "Deep Article",
        "content": "# Deep Article\n\nNested content.\n",
    })
    assert "created" in result.lower()
    assert (vault / "raw" / "notes" / "subtopic" / "article.md").exists()


def test_create_file_git_commits(wiki_executor, vault):
    wiki_executor.run("create_file", {
        "path": "raw/notes/new-article.md",
        "title": "New Article",
        "content": "# New\n\nContent.\n",
    })
    result = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=vault, capture_output=True, text=True,
    )
    assert "create" in result.stdout.lower()


def test_edit_file_no_wiki(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("edit_file", {"path": "Research/quantum.md", "content": "x"})
    assert "not available" in result.lower()


def test_create_file_no_wiki(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("create_file", {"path": "Research/new.md", "title": "T", "content": "x"})
    assert "not available" in result.lower()


def test_create_file_path_traversal(wiki_executor):
    result = wiki_executor.run("create_file", {
        "path": "../../etc/evil.md",
        "title": "Evil",
        "content": "hacked",
    })
    assert "escapes" in result.lower() or "outside vault" in result.lower()


@pytest.mark.asyncio
async def test_create_file_triggers_reindex(vault):
    """After a successful create_file via run_async, reindex is triggered."""
    from unittest.mock import AsyncMock
    from pal.tools import ToolExecutor
    from pal.wiki import WikiManager

    wiki = WikiManager(vault)
    wiki.init_vault()
    retrieval = AsyncMock()
    retrieval.trigger_reindex = AsyncMock(return_value={
        "job_id": "j", "status": "queued",
    })
    executor = ToolExecutor(
        vault_path=vault,
        retrieval=retrieval,
        wiki=wiki,
    )

    result = await executor.run_async("create_file", {
        "path": "raw/notes/scratch.md",
        "title": "Scratch",
        "content": "# Scratch\n\nNote content.\n",
    })
    assert "created" in result.lower()
    retrieval.trigger_reindex.assert_awaited_once()
    call_args = retrieval.trigger_reindex.await_args
    paths = call_args.kwargs.get("paths") if call_args.kwargs else (call_args.args[0] if call_args.args else None)
    assert paths is not None
    assert any("raw/notes/scratch.md" in p for p in paths)


@pytest.mark.asyncio
async def test_edit_file_triggers_reindex(vault):
    """After a successful edit_file via run_async, reindex is triggered."""
    from unittest.mock import AsyncMock
    from pal.tools import ToolExecutor
    from pal.wiki import WikiManager

    wiki = WikiManager(vault)
    wiki.init_vault()

    # Seed a file to edit
    (vault / "raw" / "notes").mkdir(parents=True, exist_ok=True)
    wiki.write_article("raw/notes/n.md", "N", "old body")
    wiki.git_commit("seed")

    retrieval = AsyncMock()
    retrieval.trigger_reindex = AsyncMock(return_value={
        "job_id": "j2", "status": "queued",
    })
    executor = ToolExecutor(
        vault_path=vault,
        retrieval=retrieval,
        wiki=wiki,
    )

    result = await executor.run_async("edit_file", {
        "path": "raw/notes/n.md",
        "content": "new body",
    })
    assert "updated" in result.lower() or "edit" in result.lower()
    retrieval.trigger_reindex.assert_awaited_once()
    call_args = retrieval.trigger_reindex.await_args
    paths = call_args.kwargs.get("paths") if call_args.kwargs else (call_args.args[0] if call_args.args else None)
    assert paths is not None
    assert any("raw/notes/n.md" in p for p in paths)


@pytest.mark.asyncio
async def test_wait_for_reindex_returns_done_when_finished(vault):
    """Polls until the job reports done; returns the final status."""
    from unittest.mock import AsyncMock
    import json as _json

    retrieval = AsyncMock()
    retrieval.get_reindex_job = AsyncMock(side_effect=[
        {"job_id": "j", "status": "running"},
        {"job_id": "j", "status": "running"},
        {"job_id": "j", "status": "done", "stats": {"new": 1}},
    ])
    executor = ToolExecutor(vault_path=vault, retrieval=retrieval)

    result = await executor.run_async("wait_for_reindex", {
        "job_id": "j",
        "timeout_seconds": 5,
    })
    payload = _json.loads(result)
    assert payload["status"] == "done"
    assert payload["job_id"] == "j"
    assert retrieval.get_reindex_job.await_count == 3


@pytest.mark.asyncio
async def test_wait_for_reindex_times_out(vault):
    from unittest.mock import AsyncMock
    import json as _json

    retrieval = AsyncMock()
    retrieval.get_reindex_job = AsyncMock(return_value={"job_id": "j", "status": "running"})
    executor = ToolExecutor(vault_path=vault, retrieval=retrieval)

    result = await executor.run_async("wait_for_reindex", {
        "job_id": "j",
        "timeout_seconds": 1,
    })
    payload = _json.loads(result)
    assert payload["status"] == "timeout"
    assert payload["last_seen_status"] == "running"


@pytest.mark.asyncio
async def test_wait_for_reindex_unknown_job(vault):
    from unittest.mock import AsyncMock

    retrieval = AsyncMock()
    retrieval.get_reindex_job = AsyncMock(return_value=None)
    executor = ToolExecutor(vault_path=vault, retrieval=retrieval)

    result = await executor.run_async("wait_for_reindex", {
        "job_id": "missing",
        "timeout_seconds": 1,
    })
    assert "not found" in result.lower() or "unknown" in result.lower()


@pytest.mark.asyncio
async def test_update_scratch_writes_content(tmp_path):
    from unittest.mock import MagicMock
    from agent_core.scratchpad import Scratchpad
    from pal.tools import ToolExecutor

    wiki = MagicMock()
    wiki.git_commit = MagicMock()
    sp = Scratchpad(
        vault_path=tmp_path,
        agent_name="pal",
        channel_id="C1",
        max_bytes=1024,
        commit_callback=lambda path, msg: wiki.git_commit(msg),
    )
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        wiki=wiki,
        scratchpad=sp,
    )

    result = await executor.run_async("update_scratch", {"content": "new notes"})
    assert "updated" in result.lower() or "ok" in result.lower()
    assert sp.read() == "new notes"


@pytest.mark.asyncio
async def test_update_scratch_returns_error_on_oversize(tmp_path):
    from unittest.mock import MagicMock
    from agent_core.scratchpad import Scratchpad
    from pal.tools import ToolExecutor

    wiki = MagicMock()
    wiki.git_commit = MagicMock()
    sp = Scratchpad(
        vault_path=tmp_path,
        agent_name="pal",
        channel_id="C1",
        max_bytes=10,
        commit_callback=lambda path, msg: wiki.git_commit(msg),
    )
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        wiki=wiki,
        scratchpad=sp,
    )

    result = await executor.run_async(
        "update_scratch", {"content": "x" * 20}
    )
    assert "error" in result.lower() or "too large" in result.lower()
    assert sp.read() == ""


@pytest.mark.asyncio
async def test_update_scratch_without_scratchpad_errors(tmp_path):
    """If executor wasn't given a scratchpad, tool returns a clear error."""
    from pal.tools import ToolExecutor
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        wiki=None,
        scratchpad=None,
    )
    result = await executor.run_async("update_scratch", {"content": "x"})
    assert "scratchpad" in result.lower() and "not" in result.lower()
