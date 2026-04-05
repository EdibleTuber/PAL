"""Tests for WisdomManager — list/add/remove wisdom entries."""
from pathlib import Path

import pytest

from pal.wisdom import WisdomManager


@pytest.fixture()
def vault(tmp_path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    return v


@pytest.fixture()
def wisdom(vault) -> WisdomManager:
    return WisdomManager(vault)


def test_list_empty(wisdom):
    assert wisdom.list() == []


def test_add_entry_creates_file(wisdom, vault):
    slug = wisdom.add(title="Be concise", body="Lead with the answer.")
    assert slug == "be-concise"
    path = vault / "_wisdom" / "be-concise.md"
    assert path.exists()
    content = path.read_text()
    assert "title: Be concise" in content
    assert "Lead with the answer." in content


def test_list_returns_entries(wisdom):
    wisdom.add(title="First", body="Body one.")
    wisdom.add(title="Second", body="Body two.")
    entries = wisdom.list()
    assert len(entries) == 2
    slugs = [e["slug"] for e in entries]
    assert "first" in slugs
    assert "second" in slugs
    titles = [e["title"] for e in entries]
    assert "First" in titles
    assert "Second" in titles


def test_get_returns_body(wisdom):
    wisdom.add(title="Rule", body="Always measure twice.")
    body = wisdom.get("rule")
    assert body == "Always measure twice."


def test_get_nonexistent_raises(wisdom):
    with pytest.raises(FileNotFoundError):
        wisdom.get("nonexistent")


def test_remove_deletes_file(wisdom, vault):
    wisdom.add(title="Temp", body="Will be removed.")
    assert (vault / "_wisdom" / "temp.md").exists()
    wisdom.remove("temp")
    assert not (vault / "_wisdom" / "temp.md").exists()


def test_remove_nonexistent_raises(wisdom):
    with pytest.raises(FileNotFoundError):
        wisdom.remove("nope")


def test_bodies_returns_all(wisdom):
    wisdom.add(title="One", body="First lesson.")
    wisdom.add(title="Two", body="Second lesson.")
    bodies = wisdom.bodies()
    assert len(bodies) == 2
    assert "First lesson." in bodies
    assert "Second lesson." in bodies


def test_add_sanitizes_slug(wisdom, vault):
    slug = wisdom.add(title="Hello, World!", body="Test.")
    assert slug == "hello-world"
    assert (vault / "_wisdom" / "hello-world.md").exists()
