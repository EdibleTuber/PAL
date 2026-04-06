"""Tests for LearningManager — learning extraction storage."""
from pathlib import Path

import pytest

from pal.learning import LearningManager


@pytest.fixture()
def vault(tmp_path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    return v


@pytest.fixture()
def learning(vault) -> LearningManager:
    return LearningManager(vault)


def test_list_empty(learning):
    assert learning.list() == []


def test_add_creates_file(learning, vault):
    slug = learning.add(
        title="Always test edge cases",
        body="Edge cases reveal assumptions. Test boundaries, empty inputs, and error paths.",
        source="conversation",
    )
    assert slug == "always-test-edge-cases"
    path = vault / "_learning" / "always-test-edge-cases.md"
    assert path.exists()
    content = path.read_text()
    assert "title: Always test edge cases" in content
    assert "Edge cases reveal assumptions" in content
    assert "source: conversation" in content


def test_list_returns_entries(learning):
    learning.add(title="First", body="Body one.", source="conversation")
    learning.add(title="Second", body="Body two.", source="conversation")
    entries = learning.list()
    assert len(entries) == 2
    slugs = [e["slug"] for e in entries]
    assert "first" in slugs
    assert "second" in slugs


def test_get_returns_body(learning):
    learning.add(title="Rule", body="Always check.", source="conversation")
    body = learning.get("rule")
    assert body == "Always check."


def test_get_nonexistent_raises(learning):
    with pytest.raises(FileNotFoundError):
        learning.get("nonexistent")


def test_remove_deletes_file(learning, vault):
    learning.add(title="Temp", body="Will be removed.", source="conversation")
    assert (vault / "_learning" / "temp.md").exists()
    learning.remove("temp")
    assert not (vault / "_learning" / "temp.md").exists()


def test_remove_nonexistent_raises(learning):
    with pytest.raises(FileNotFoundError):
        learning.remove("nope")


def test_add_sanitizes_slug(learning, vault):
    slug = learning.add(title="Hello, World!", body="Test.", source="conversation")
    assert slug == "hello-world"
    assert (vault / "_learning" / "hello-world.md").exists()


def test_add_stores_metadata(learning, vault):
    import yaml
    learning.add(title="Test", body="Body.", source="conversation")
    content = (vault / "_learning" / "test.md").read_text()
    meta = yaml.safe_load(content.split("---")[1])
    assert meta["title"] == "Test"
    assert meta["source"] == "conversation"
    assert "created" in meta
    assert meta["status"] == "active"


from pal.wisdom import WisdomManager


def test_mark_promoted_updates_status(learning, vault):
    learning.add(title="Good Idea", body="This works.", source="conversation")
    learning.mark_promoted("good-idea")
    import yaml
    content = (vault / "_learning" / "good-idea.md").read_text()
    meta = yaml.safe_load(content.split("---")[1])
    assert meta["status"] == "promoted"
    assert "promoted_at" in meta


def test_mark_promoted_nonexistent_raises(learning):
    with pytest.raises(FileNotFoundError):
        learning.mark_promoted("nope")


def test_add_rating(learning, vault):
    learning.add_rating("good", "Great session")
    ratings_path = vault / "_learning" / "ratings.md"
    assert ratings_path.exists()
    content = ratings_path.read_text()
    assert "**good**" in content
    assert "Great session" in content


def test_add_rating_appends(learning, vault):
    learning.add_rating("good", "First")
    learning.add_rating("bad", "Second")
    content = (vault / "_learning" / "ratings.md").read_text()
    assert "**good**" in content
    assert "**bad**" in content
    assert "First" in content
    assert "Second" in content


def test_list_excludes_ratings_file(learning):
    learning.add(title="Real Learning", body="Body.", source="conversation")
    learning.add_rating("good")
    entries = learning.list()
    slugs = [e["slug"] for e in entries]
    assert "ratings" not in slugs
    assert "real-learning" in slugs
