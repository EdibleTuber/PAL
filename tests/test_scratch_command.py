"""Tests for the /scratch slash command (append-to-scratchpad)."""
import asyncio
import pytest
from pathlib import Path
from unittest.mock import MagicMock


@pytest.mark.asyncio
async def test_scratch_appends_to_scratchpad(tmp_path):
    """`/scratch some text` appends a timestamped line to the channel's scratchpad."""
    from pal.scratchpad import Scratchpad
    from pal.daemon import handle_scratch

    wiki = MagicMock()
    wiki.git_commit = MagicMock()
    sp = Scratchpad(vault_path=tmp_path, channel_id="C1",
                    wiki=wiki, max_bytes=1024)
    sp.write("existing content\n")

    result = await handle_scratch(scratchpad=sp, text="new observation")
    assert "added" in result.lower() or "appended" in result.lower()

    content = sp.read()
    assert "existing content" in content
    assert "new observation" in content


@pytest.mark.asyncio
async def test_scratch_returns_error_on_oversize(tmp_path):
    from pal.scratchpad import Scratchpad
    from pal.daemon import handle_scratch

    wiki = MagicMock()
    wiki.git_commit = MagicMock()
    sp = Scratchpad(vault_path=tmp_path, channel_id="C1",
                    wiki=wiki, max_bytes=20)
    sp.write("1234567890\n")  # 11 bytes

    result = await handle_scratch(scratchpad=sp, text="this is way too long to fit")
    assert "error" in result.lower() or "too large" in result.lower()

    # Content unchanged
    assert sp.read() == "1234567890\n"


@pytest.mark.asyncio
async def test_scratch_empty_text_returns_usage(tmp_path):
    from pal.scratchpad import Scratchpad
    from pal.daemon import handle_scratch
    wiki = MagicMock()
    sp = Scratchpad(vault_path=tmp_path, channel_id="C1",
                    wiki=wiki, max_bytes=1024)
    result = await handle_scratch(scratchpad=sp, text="")
    assert "usage" in result.lower() or "empty" in result.lower()
