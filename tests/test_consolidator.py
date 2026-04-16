import pytest
from pathlib import Path

from pal.consolidator import Consolidator


class _FakeInference:
    def __init__(self, response: str):
        self.response = response
        self.calls = []

    async def complete(self, messages, reasoning=None, tools=None, model=None):
        self.calls.append({"messages": list(messages), "reasoning": reasoning})
        class R:
            type = "text"
            content = self.response
            reasoning = ""
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


@pytest.mark.asyncio
async def test_happy_path_writes_article(tmp_path):
    (tmp_path / "Security").mkdir()
    (tmp_path / "Security" / "a.md").write_text("---\ntitle: A\n---\nBody A")
    (tmp_path / "Security" / "b.md").write_text("---\ntitle: B\n---\nBody B")
    c, inference, wiki = _make(tmp_path, inference_response="## Overview\n\nFused (from Security/a.md)")

    out = await c.consolidate(
        source_paths=["Security/a.md", "Security/b.md"],
        target_path="Security/Combined.md",
        target_title="Combined",
    )

    assert out["status"] == "ok", out
    assert out["target_path"] == "Security/Combined.md"
    assert out["article_path_rel"] == "Security/Combined.md"
    assert out["vault_exists"] is True
    assert (tmp_path / "Security" / "Combined.md").exists()
    assert wiki.written and wiki.written[0]["title"] == "Combined"

    # Inference saw both source bodies and the path labels in the user message
    assert inference.calls, "inference was not invoked"
    messages = inference.calls[0]["messages"]
    user_content = next(m["content"] for m in messages if m["role"] == "user")
    assert "Security/a.md" in user_content
    assert "Security/b.md" in user_content
    assert "Body A" in user_content
    assert "Body B" in user_content


@pytest.mark.asyncio
async def test_insufficient_response(tmp_path):
    (tmp_path / "Security").mkdir()
    (tmp_path / "Security" / "a.md").write_text("---\ntitle: A\n---\nA")
    (tmp_path / "Security" / "b.md").write_text("---\ntitle: B\n---\nB")
    c, _, wiki = _make(tmp_path, inference_response="INSUFFICIENT: sources too thin")

    out = await c.consolidate(
        source_paths=["Security/a.md", "Security/b.md"],
        target_path="Security/Combined.md",
        target_title="Combined",
    )

    assert out["status"] == "insufficient"
    assert "thin" in out["reason"].lower()
    assert not wiki.written
    assert not (tmp_path / "Security" / "Combined.md").exists()


@pytest.mark.asyncio
async def test_prompt_demands_inline_citations(tmp_path):
    (tmp_path / "Security").mkdir()
    (tmp_path / "Security" / "a.md").write_text("---\ntitle: A\n---\nA")
    (tmp_path / "Security" / "b.md").write_text("---\ntitle: B\n---\nB")
    c, inference, _ = _make(tmp_path, inference_response="## Overview\n\nFused")

    await c.consolidate(
        source_paths=["Security/a.md", "Security/b.md"],
        target_path="Security/Combined.md",
        target_title="Combined",
    )

    messages = inference.calls[0]["messages"]
    system_content = next(m["content"] for m in messages if m["role"] == "system")
    assert "ONLY information" in system_content
    assert "INSUFFICIENT" in system_content
    assert "cite" in system_content.lower() or "citation" in system_content.lower()


@pytest.mark.asyncio
async def test_consolidate_triggers_reindex_with_target_path(tmp_path):
    from unittest.mock import AsyncMock

    (tmp_path / "Security").mkdir()
    (tmp_path / "Security" / "a.md").write_text("---\ntitle: A\n---\nBody A")
    (tmp_path / "Security" / "b.md").write_text("---\ntitle: B\n---\nBody B")

    inference = _FakeInference("## Overview\n\nFused.\n\n## Key Concepts\n\nA point.")
    wiki = _FakeWiki(tmp_path)
    retrieval = AsyncMock()
    retrieval.trigger_reindex = AsyncMock(return_value={
        "job_id": "j2", "status": "queued",
    })

    c = Consolidator(
        vault_path=tmp_path,
        wiki=wiki,
        inference=inference,
        prompt_builder=_StubPromptBuilder(),
        retrieval=retrieval,
    )

    out = await c.consolidate(
        source_paths=["Security/a.md", "Security/b.md"],
        target_path="Security/Combined.md",
        target_title="Combined",
    )

    assert out["status"] == "ok"
    assert out.get("reindex", {}).get("job_id") == "j2"
    retrieval.trigger_reindex.assert_awaited_once()
    call_args = retrieval.trigger_reindex.await_args
    paths = call_args.kwargs.get("paths") if call_args.kwargs else (call_args.args[0] if call_args.args else None)
    assert paths is not None
    assert any("Security/Combined.md" in p for p in paths)


@pytest.mark.asyncio
async def test_consolidate_no_retrieval_omits_reindex_key(tmp_path):
    (tmp_path / "Security").mkdir()
    (tmp_path / "Security" / "a.md").write_text("---\ntitle: A\n---\nBody A")
    (tmp_path / "Security" / "b.md").write_text("---\ntitle: B\n---\nBody B")

    c, _, _ = _make(tmp_path, inference_response="## Overview\n\nx\n\n## Key Concepts\n\ny")
    out = await c.consolidate(
        source_paths=["Security/a.md", "Security/b.md"],
        target_path="Security/Combined.md",
        target_title="Combined",
    )
    assert out["status"] == "ok"
    assert "reindex" not in out or out.get("reindex") is None
