import pytest
from pathlib import Path

from pal.consolidator import Consolidator


class _FakeInference:
    def __init__(self, response: str):
        self.response = response
        self.calls = []

    async def generate(self, *, system: str, user: str):
        self.calls.append({"system": system, "user": user})
        class R:
            content = self.response
        return R()


class _FakeWiki:
    def __init__(self, vault_path: Path):
        self.vault_path = vault_path
        self.written = []

    def write_article(self, path, title, content, tags=None):
        full = self.vault_path / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        self.written.append({"path": path, "title": title})

    def git_commit(self, message: str) -> None:
        pass


class _StubPromptBuilder:
    def build(self) -> str:
        return "BASE"


def _make(tmp_path, inference_response="## Overview\n\ncontent"):
    inference = _FakeInference(inference_response)
    wiki = _FakeWiki(tmp_path)
    return Consolidator(
        vault_path=tmp_path,
        wiki=wiki,
        inference=inference,
        prompt_builder=_StubPromptBuilder(),
    ), inference, wiki


@pytest.mark.asyncio
async def test_rejects_target_outside_vault(tmp_path):
    c, _, _ = _make(tmp_path)
    out = await c.consolidate(
        source_paths=["a.md", "b.md"],
        target_path="../evil.md",
        target_title="Evil",
    )
    assert out["status"] == "invalid_path"


@pytest.mark.asyncio
async def test_rejects_target_in_raw(tmp_path):
    c, _, _ = _make(tmp_path)
    out = await c.consolidate(
        source_paths=["a.md", "b.md"],
        target_path="raw/notes/out.md",
        target_title="Out",
    )
    assert out["status"] == "invalid_path"
    assert "raw/" in out["reason"]


@pytest.mark.asyncio
async def test_rejects_system_target(tmp_path):
    c, _, _ = _make(tmp_path)
    out = await c.consolidate(
        source_paths=["a.md", "b.md"],
        target_path="_internal/out.md",
        target_title="Out",
    )
    assert out["status"] == "invalid_path"


@pytest.mark.asyncio
async def test_target_must_not_exist(tmp_path):
    (tmp_path / "Security").mkdir()
    (tmp_path / "Security" / "out.md").write_text("exists")
    (tmp_path / "Security" / "a.md").write_text("---\ntitle: A\n---\nA body")
    (tmp_path / "Security" / "b.md").write_text("---\ntitle: B\n---\nB body")
    c, _, _ = _make(tmp_path)
    out = await c.consolidate(
        source_paths=["Security/a.md", "Security/b.md"],
        target_path="Security/out.md",
        target_title="Out",
    )
    assert out["status"] == "invalid_path"
    assert "exists" in out["reason"]


@pytest.mark.asyncio
async def test_source_missing(tmp_path):
    (tmp_path / "Security").mkdir()
    (tmp_path / "Security" / "a.md").write_text("---\ntitle: A\n---\nA body")
    c, _, _ = _make(tmp_path)
    out = await c.consolidate(
        source_paths=["Security/a.md", "Security/missing.md"],
        target_path="Security/out.md",
        target_title="Out",
    )
    assert out["status"] == "not_found"
    assert "missing.md" in out["reason"]
