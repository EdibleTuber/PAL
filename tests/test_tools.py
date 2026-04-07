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
