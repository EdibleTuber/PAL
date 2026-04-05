"""Tests for WikiManager — vault read/write operations."""
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from pal.wiki import WikiManager


@pytest.fixture()
def vault(tmp_path) -> Path:
    """Create a temporary vault directory."""
    return tmp_path / "vault"


@pytest.fixture()
def wiki(vault) -> WikiManager:
    """Create a WikiManager pointing at the temp vault."""
    return WikiManager(vault)


def test_init_creates_vault_structure(wiki, vault):
    """First access creates vault dir and system directories."""
    wiki.init_vault()
    assert vault.exists()
    assert (vault / "_index.md").exists()


def test_write_article(wiki, vault):
    wiki.init_vault()
    wiki.write_article(
        path="Projects/my-project.md",
        title="My Project",
        body="# My Project\n\nA cool project.\n",
        tags=["project", "python"],
    )
    full_path = vault / "Projects" / "my-project.md"
    assert full_path.exists()
    content = full_path.read_text()
    assert "title: My Project" in content
    assert "# My Project" in content
    assert "A cool project." in content


def test_write_article_creates_parent_dirs(wiki, vault):
    wiki.init_vault()
    wiki.write_article(
        path="Deep/Nested/Dir/article.md",
        title="Nested",
        body="Content.\n",
    )
    assert (vault / "Deep" / "Nested" / "Dir" / "article.md").exists()


def test_write_article_updates_existing(wiki, vault):
    wiki.init_vault()
    wiki.write_article(path="test.md", title="V1", body="Version 1.\n")
    wiki.write_article(path="test.md", title="V2", body="Version 2.\n")
    content = (vault / "test.md").read_text()
    assert "title: V2" in content
    assert "Version 2." in content
    # Should have both created and updated timestamps
    meta = yaml.safe_load(content.split("---")[1])
    assert "created" in meta
    assert "updated" in meta


def test_read_article(wiki, vault):
    wiki.init_vault()
    wiki.write_article(path="test.md", title="Test", body="Body here.\n")
    meta, body = wiki.read_article("test.md")
    assert meta["title"] == "Test"
    assert "Body here." in body


def test_read_article_not_found(wiki, vault):
    wiki.init_vault()
    with pytest.raises(FileNotFoundError):
        wiki.read_article("nonexistent.md")


def test_list_articles(wiki, vault):
    wiki.init_vault()
    wiki.write_article(path="a.md", title="A", body="A content.\n")
    wiki.write_article(path="Projects/b.md", title="B", body="B content.\n")
    articles = wiki.list_articles()
    paths = [a["path"] for a in articles]
    assert "a.md" in paths
    assert "Projects/b.md" in paths
    # System dirs should not appear
    for a in articles:
        assert not a["path"].startswith("_")


def test_rebuild_index(wiki, vault):
    wiki.init_vault()
    wiki.write_article(path="Projects/alpha.md", title="Alpha", body="Alpha content.\n")
    wiki.write_article(path="Disciplines/beta.md", title="Beta", body="Beta content.\n", tags=["science"])
    wiki.rebuild_index()
    index_content = (vault / "_index.md").read_text()
    assert "Alpha" in index_content
    assert "Beta" in index_content
    assert "Projects/alpha.md" in index_content
    assert "Disciplines/beta.md" in index_content


def test_rebuild_index_empty_vault(wiki, vault):
    wiki.init_vault()
    wiki.rebuild_index()
    index_content = (vault / "_index.md").read_text()
    assert "Vault Index" in index_content
    # Should not crash with no articles


import subprocess


def test_git_init(wiki, vault):
    """init_vault creates a git repo if one doesn't exist."""
    wiki.init_vault()
    wiki.git_init()
    assert (vault / ".git").exists()


def test_git_commit(wiki, vault):
    wiki.init_vault()
    wiki.git_init()
    wiki.write_article(path="test.md", title="Test", body="Content.\n")
    wiki.git_commit("Add test article")
    result = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=vault,
        capture_output=True,
        text=True,
    )
    assert "Add test article" in result.stdout


def test_git_commit_no_changes(wiki, vault):
    """Committing with no changes does not error."""
    wiki.init_vault()
    wiki.git_init()
    wiki.git_commit("Nothing to commit")
    # Should not raise — just a no-op
