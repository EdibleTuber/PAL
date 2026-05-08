"""Tests for vault tool execution (legacy ToolExecutor, non-migrated tools).

Note: read_file, list_directory, search_content, edit_file, create_file,
move_file, and search_vault have been migrated to pal.tools.vault Tool
subclasses (Phase F PR2). Their tests now live in tests/test_tools_vault.py.
wait_for_reindex has been migrated to pal.tools.wait (Phase F PR4); its tests
now live in tests/test_tools_wait.py. This file retains tests for tools still
in the legacy executor: add_learning, update_scratch, and the executor's
unknown-tool fallback.
"""
import subprocess
import os
import pytest
from pathlib import Path

from pal._legacy_tools import ToolExecutor
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


def test_unknown_tool(vault):
    executor = ToolExecutor(vault_path=vault, retrieval=None)
    result = executor.run("delete_everything", {})
    assert "unknown tool" in result.lower()


@pytest.mark.asyncio
async def test_update_scratch_writes_content(tmp_path):
    from unittest.mock import MagicMock
    from agent_core.scratchpad import Scratchpad
    from pal._legacy_tools import ToolExecutor

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
    from pal._legacy_tools import ToolExecutor

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
    from pal._legacy_tools import ToolExecutor
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        wiki=None,
        scratchpad=None,
    )
    result = await executor.run_async("update_scratch", {"content": "x"})
    assert "scratchpad" in result.lower() and "not" in result.lower()
