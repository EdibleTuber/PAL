"""Tests for Compiler.compile_chat_synthesis (chat-derived promotion path)."""
from __future__ import annotations

from pathlib import Path

import pytest

from pal.compiler import Compiler, CHAT_BANNER_SENTINEL, make_chat_banner
from pal.article import parse_article


class FakeWiki:
    def __init__(self):
        self.commits = []
        self.indexed = False
    def list_articles(self):
        return []
    def rebuild_index(self):
        self.indexed = True
    def git_init(self):
        pass
    def git_commit(self, msg):
        self.commits.append(msg)


class FakeInference:
    """Should never be called by the chat-aware path."""
    async def complete(self, *args, **kwargs):
        raise AssertionError("compile_chat_synthesis must not call inference")


class FakeCategorizer:
    async def categorize(self, **kwargs):
        return "Software-Development"


class FakePromptBuilder:
    def build(self):
        return "system prompt"


def _write_summary(vault: Path, slug: str, body: str) -> str:
    summaries = vault / "raw" / "summaries"
    summaries.mkdir(parents=True, exist_ok=True)
    (vault / "raw" / "notes").mkdir(parents=True, exist_ok=True)
    note_path = vault / "raw" / "notes" / f"{slug}.md"
    note_path.write_text(body)
    summary_path = summaries / f"{slug}.md"
    summary_path.write_text(
        "---\n"
        f"title: \"{slug}\"\n"
        f"source_file: \"raw/notes/{slug}.md\"\n"
        "source_url: \"\"\n"
        "source_type: chat\n"
        "source_hash: \"abc123\"\n"
        f"source_raw: \"raw/notes/{slug}.md\"\n"
        "---\n"
        f"{body}"
    )
    return f"raw/summaries/{slug}.md"


@pytest.mark.asyncio
async def test_compile_chat_synthesis_writes_article_with_banner(tmp_path):
    body = "## Overview\nVibe-coding comprehension is X.\n\n## Key Concepts\n- A\n- B\n"
    summary_rel = _write_summary(tmp_path, "vibe-coding", body)

    compiler = Compiler(
        vault_path=tmp_path,
        wiki=FakeWiki(),
        inference=FakeInference(),
        categorizer=FakeCategorizer(),
        prompt_builder=FakePromptBuilder(),
    )

    result = await compiler.compile_chat_synthesis(summary_rel)

    assert result["status"] == "ok"
    article_rel = result["article_path_rel"]
    assert article_rel.startswith("Software-Development/")
    article_full = tmp_path / article_rel
    assert article_full.exists()

    article = parse_article(article_full.read_text())
    assert article.compiled_truth.lstrip().startswith(CHAT_BANNER_SENTINEL)
    assert "## Overview" in article.compiled_truth
    assert "## Key Concepts" in article.compiled_truth
    assert article.meta["sources"][-1]["source_type"] == "chat"


@pytest.mark.asyncio
async def test_compile_chat_synthesis_returns_insufficient_when_sections_missing(tmp_path):
    body = "Just a paragraph with no required sections.\n"
    summary_rel = _write_summary(tmp_path, "no-sections", body)

    compiler = Compiler(
        vault_path=tmp_path,
        wiki=FakeWiki(),
        inference=FakeInference(),
        categorizer=FakeCategorizer(),
        prompt_builder=FakePromptBuilder(),
    )
    result = await compiler.compile_chat_synthesis(summary_rel)
    assert result["status"] == "insufficient"


@pytest.mark.asyncio
async def test_compile_chat_synthesis_rejects_empty_source_file(tmp_path):
    """Chat path requires source_file pointing at the synthesis note."""
    summaries = tmp_path / "raw" / "summaries"
    summaries.mkdir(parents=True)
    (summaries / "no-source.md").write_text(
        "---\n"
        "title: \"No source\"\n"
        "source_file: \"\"\n"
        "source_url: \"\"\n"
        "source_type: chat\n"
        "source_hash: \"abc\"\n"
        "---\n"
        "## Overview\nfoo\n## Key Concepts\nbar\n"
    )
    compiler = Compiler(
        vault_path=tmp_path,
        wiki=FakeWiki(),
        inference=FakeInference(),
        categorizer=FakeCategorizer(),
        prompt_builder=FakePromptBuilder(),
    )
    result = await compiler.compile_chat_synthesis("raw/summaries/no-source.md")
    assert result["status"] == "missing_source_file"


def test_make_chat_banner_format():
    banner = make_chat_banner("2026-05-10")
    assert banner.startswith(CHAT_BANNER_SENTINEL)
    assert "2026-05-10" in banner


@pytest.mark.asyncio
async def test_merge_chat_synthesis_preserves_banner(tmp_path, monkeypatch):
    """When chat synthesis topic-matches an existing chat-derived article,
    the merged article must still begin with the chat banner sentinel."""
    # Pre-seed an existing chat-derived article.
    cat_dir = tmp_path / "Software-Development"
    cat_dir.mkdir(parents=True)
    existing_article_text = (
        "---\n"
        "title: \"Vibe-coding comprehension strategies\"\n"
        "sources: []\n"
        "---\n"
        f"{make_chat_banner('2026-05-09')}\n\n"
        "## Overview\nOriginal overview.\n\n"
        "## Key Concepts\n- existing point\n\n"
        "<!-- TIMELINE -->\n"
    )
    existing_path = cat_dir / "vibe-coding-comprehension-strategies.md"
    existing_path.write_text(existing_article_text)

    # New synthesis on the same topic.
    body = "## Overview\nUpdated overview.\n\n## Key Concepts\n- new point\n"
    summary_rel = _write_summary(tmp_path, "vibe-coding-comprehension-strategies", body)

    class MatchingWiki(FakeWiki):
        def list_articles(self):
            return [{"path": "Software-Development/vibe-coding-comprehension-strategies.md",
                     "title": "Vibe-coding comprehension strategies"}]

    compiler = Compiler(
        vault_path=tmp_path,
        wiki=MatchingWiki(),
        inference=FakeInference(),
        categorizer=FakeCategorizer(),
        prompt_builder=FakePromptBuilder(),
    )

    # Force topic match: patch find_existing_article in BOTH modules that import it.
    async def fake_find(**kwargs):
        return {"path": "Software-Development/vibe-coding-comprehension-strategies.md"}
    monkeypatch.setattr("pal.compiler.find_existing_article", fake_find)

    result = await compiler.compile_chat_synthesis(summary_rel)

    assert result["status"] == "merged"
    merged_text = existing_path.read_text()
    article = parse_article(merged_text)
    assert article.compiled_truth.lstrip().startswith(CHAT_BANNER_SENTINEL)
    # Banner date must be preserved from the seeded article, not regenerated.
    original_banner = make_chat_banner("2026-05-09")
    assert article.compiled_truth.lstrip().startswith(original_banner), (
        f"banner date should be preserved; expected {original_banner!r} prefix, "
        f"got {article.compiled_truth.lstrip()[:200]!r}"
    )
    # The new synthesis content must have landed in the article.
    assert "Updated overview." in article.compiled_truth or "new point" in article.compiled_truth
