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
