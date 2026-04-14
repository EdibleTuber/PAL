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


