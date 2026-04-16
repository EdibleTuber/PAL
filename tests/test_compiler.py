from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pal.compiler import Compiler


@pytest.mark.asyncio
async def test_compile_one_returns_not_found_for_missing_summary(tmp_path: Path):
    wiki = MagicMock()
    inference = MagicMock()
    categorizer = MagicMock()
    prompt_builder = MagicMock()
    prompt_builder.build = MagicMock(return_value="")

    compiler = Compiler(
        vault_path=tmp_path,
        wiki=wiki,
        inference=inference,
        categorizer=categorizer,
        prompt_builder=prompt_builder,
    )
    result = await compiler.compile_one("raw/summaries/does-not-exist.md")
    assert result["status"] == "not_found"


@pytest.mark.asyncio
async def test_compile_one_rejects_path_traversal(tmp_path: Path):
    compiler = Compiler(
        vault_path=tmp_path,
        wiki=MagicMock(),
        inference=MagicMock(),
        categorizer=MagicMock(),
        prompt_builder=MagicMock(),
    )
    result = await compiler.compile_one("../escape.md")
    assert result["status"] == "invalid_path"


@pytest.mark.asyncio
async def test_compile_one_rejects_absolute_path(tmp_path: Path):
    compiler = Compiler(
        vault_path=tmp_path,
        wiki=MagicMock(),
        inference=MagicMock(),
        categorizer=MagicMock(),
        prompt_builder=MagicMock(),
    )
    result = await compiler.compile_one("/etc/passwd")
    assert result["status"] == "invalid_path"


def test_clip_title_for_slug_word_boundary():
    """Direct unit test on the title-clipping helper: long titles clip at
    a word boundary around 80 chars or ~8 words, whichever comes first."""
    from pal.compiler import _clip_title_for_slug

    short = "AI Agents"
    assert _clip_title_for_slug(short) == "AI Agents"

    medium = "Model Context Protocol Security"
    assert _clip_title_for_slug(medium) == "Model Context Protocol Security"

    long = (
        "GitHub - codeaashu/claude-code: Claude Code is an agentic coding "
        "tool that lives in your terminal, understands your codebase"
    )
    clipped = _clip_title_for_slug(long)
    assert len(clipped) <= 80
    # Clipped at a word boundary (no trailing partial word)
    assert not clipped.endswith(" ")
    assert clipped in long  # it's a prefix


def test_clip_title_for_slug_very_long_single_word():
    """If the title has one enormous word, clip it to 80 chars even mid-word."""
    from pal.compiler import _clip_title_for_slug

    huge_word = "a" * 200
    clipped = _clip_title_for_slug(huge_word)
    assert len(clipped) <= 80


@pytest.mark.asyncio
async def test_compile_one_archives_summary_after_merge(tmp_path: Path, monkeypatch):
    """When compile_one takes the merge-into-existing branch, the source
    summary must be archived to raw/archived/, matching the new-article path.
    """
    from pal.compiler import Compiler

    # Seed vault with an existing article in the target category.
    (tmp_path / "AI-Security").mkdir()
    article_rel = "AI-Security/mcp-notes.md"
    (tmp_path / article_rel).write_text(
        "---\ntitle: MCP Notes\n---\n\n## Overview\n\nExisting.\n"
    )

    # Seed summary in raw/summaries/ with a source_raw pointer.
    (tmp_path / "raw" / "summaries").mkdir(parents=True)
    summary_rel = "raw/summaries/mcp-extra.md"
    (tmp_path / summary_rel).write_text(
        "---\n"
        "title: MCP Extra\n"
        "source_raw: raw/mcp-extra.md\n"
        "source_url: https://example.com/mcp\n"
        "source_hash: abc123\n"
        "---\n"
        "Additional MCP material worth folding in.\n"
    )
    (tmp_path / "raw").mkdir(exist_ok=True)
    (tmp_path / "raw" / "mcp-extra.md").write_text("raw content")

    wiki = MagicMock()
    wiki.list_articles = MagicMock(return_value=[
        {"path": article_rel, "title": "MCP Notes"},
    ])
    wiki.rebuild_index = MagicMock()
    wiki.git_init = MagicMock()
    wiki.git_commit = MagicMock()

    inference = MagicMock()
    inference.complete = AsyncMock(return_value=(
        "## Overview\n\n## Key Concepts\n\nMerged body.\n"
    ))

    categorizer = MagicMock()
    categorizer.categorize = AsyncMock(return_value="AI-Security")

    prompt_builder = MagicMock()
    prompt_builder.build = MagicMock(return_value="system prompt")

    async def _fake_find(**kwargs):
        return {"path": article_rel, "title": "MCP Notes"}

    monkeypatch.setattr("pal.compiler.find_existing_article", _fake_find)

    compiler = Compiler(
        vault_path=tmp_path,
        wiki=wiki,
        inference=inference,
        categorizer=categorizer,
        prompt_builder=prompt_builder,
    )

    result = await compiler.compile_one(summary_rel)

    assert result["status"] == "merged"
    # Summary must have been moved to raw/archived/
    assert not (tmp_path / summary_rel).exists()
    assert (tmp_path / "raw" / "archived" / "mcp-extra.summary.md").exists()
    # Raw source file was also archived
    assert not (tmp_path / "raw" / "mcp-extra.md").exists()
    assert (tmp_path / "raw" / "archived" / "mcp-extra.md").exists()


@pytest.mark.asyncio
async def test_merge_into_existing_updates_article_body(tmp_path):
    """The extracted merge_into_existing method should run the LLM
    synthesis against an existing article and write the merged body."""
    from pal.compiler import Compiler
    from unittest.mock import MagicMock, AsyncMock

    vault = tmp_path
    article_dir = vault / "AI-Security"
    article_dir.mkdir(parents=True)
    article_path_rel = "AI-Security/mcp-notes.md"
    (vault / article_path_rel).write_text(
        "---\ntitle: MCP Notes\n---\n\n## Overview\n\nExisting content.\n"
    )

    wiki = MagicMock()
    wiki.read_article = MagicMock(return_value=(
        {"title": "MCP Notes"},
        "## Overview\n\nExisting content.\n",
    ))
    wiki.write_article = MagicMock()
    wiki.rebuild_index = MagicMock()
    wiki.git_init = MagicMock()
    wiki.git_commit = MagicMock()
    wiki.list_articles = MagicMock(return_value=[])

    inference = MagicMock()
    inference.complete = AsyncMock(return_value=(
        "## Overview\n\nMerged content combining existing and new.\n"
    ))

    categorizer = MagicMock()
    prompt_builder = MagicMock()
    prompt_builder.build = MagicMock(return_value="system prompt")

    compiler = Compiler(
        vault_path=vault,
        wiki=wiki,
        inference=inference,
        categorizer=categorizer,
        prompt_builder=prompt_builder,
    )

    result = await compiler.merge_into_existing(
        new_content="New content to fold in.",
        new_title="MCP Additional Notes",
        existing_article_path=article_path_rel,
    )
    assert result["status"] == "merged"
    assert result["article_path_rel"] == article_path_rel
    inference.complete.assert_awaited()


@pytest.mark.asyncio
async def test_compile_one_triggers_reindex_with_target_path(tmp_path):
    """After a successful first-compile, Compiler calls retrieval.trigger_reindex
    with the absolute path of the new article and includes the response in outcome."""
    from unittest.mock import AsyncMock
    from pal.compiler import Compiler
    from pal.wiki import WikiManager

    wiki = WikiManager(tmp_path)
    wiki.init_vault()
    raw_dir = tmp_path / "raw" / "summaries"
    raw_dir.mkdir(parents=True)
    raw_path = raw_dir / "test.md"
    raw_path.write_text(
        "---\ntitle: Test\nsource_url: https://example.com\nsource_hash: abc\n---\n\n"
        "Substantive content for grounded compilation that has enough body to be promoted.\n"
    )

    inference = AsyncMock()
    inference.complete = AsyncMock(return_value=type("R", (), {
        "type": "text",
        "content": "## Overview\n\nReal content.\n\n## Key Concepts\n\nA point.",
        "reasoning": "",
    })())

    categorizer = AsyncMock()
    categorizer.categorize = AsyncMock(return_value="Research")

    prompt_builder = type("PB", (), {"build": lambda self: "BASE"})()

    retrieval = AsyncMock()
    retrieval.trigger_reindex = AsyncMock(return_value={
        "job_id": "j1", "status": "queued", "paths": None,
    })

    compiler = Compiler(
        vault_path=tmp_path,
        wiki=wiki,
        inference=inference,
        categorizer=categorizer,
        prompt_builder=prompt_builder,
        retrieval=retrieval,
    )

    outcome = await compiler.compile_one("raw/summaries/test.md")
    assert outcome["status"] == "ok", outcome
    assert "reindex" in outcome
    assert outcome["reindex"]["job_id"] == "j1"

    # Trigger was called once with the absolute path of the new article.
    retrieval.trigger_reindex.assert_awaited_once()
    call_args = retrieval.trigger_reindex.await_args
    paths = call_args.kwargs.get("paths") if call_args.kwargs else (call_args.args[0] if call_args.args else None)
    assert paths is not None
    assert any(str(tmp_path) in p and outcome["article_path_rel"] in p for p in paths)


def test_compiler_constructor_default_retrieval_is_none():
    """Compiler constructed without a retrieval kwarg has self.retrieval is None.
    This protects every existing call site from breaking — they all omit the kwarg."""
    from pathlib import Path
    from pal.compiler import Compiler
    from unittest.mock import MagicMock
    compiler = Compiler(
        vault_path=Path("/tmp"),
        wiki=MagicMock(),
        inference=MagicMock(),
        categorizer=MagicMock(),
        prompt_builder=MagicMock(),
    )
    assert compiler.retrieval is None

