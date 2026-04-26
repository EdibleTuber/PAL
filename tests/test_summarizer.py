"""Tests for extracted summarize logic."""
from pathlib import Path
from unittest.mock import AsyncMock
from dataclasses import dataclass

import pytest

from pal.summarizer import summarize_raw_file, SummarizeResult, SourceTooLargeError


@dataclass
class MockInferenceResult:
    content: str
    reasoning: str = ""


@pytest.fixture
def mock_inference():
    inference = AsyncMock()
    inference.complete.return_value = MockInferenceResult(
        content="This is a summary of the article about testing."
    )
    return inference


@pytest.fixture
def raw_file(tmp_path):
    """Create a raw file with frontmatter in a vault-like structure."""
    vault = tmp_path / "vault"
    raw_dir = vault / "raw" / "web"
    raw_dir.mkdir(parents=True)
    raw_file = raw_dir / "test-article-abc12345.md"
    raw_file.write_text(
        "---\n"
        "title: Test Article\n"
        "source_url: https://example.com/test\n"
        "content_hash: abc12345\n"
        "status: raw\n"
        "---\n"
        "# Test Article\n\n"
        "This is some content about testing that should be summarized.\n"
    )
    return vault, raw_file


@pytest.mark.asyncio
async def test_summarize_returns_result(mock_inference, raw_file):
    vault, path = raw_file
    result = await summarize_raw_file(
        raw_path=path,
        vault_path=vault,
        inference=mock_inference,
    )
    assert isinstance(result, SummarizeResult)
    assert result.summary_path.exists()
    assert "summary" in result.summary_path.read_text().lower() or "testing" in result.summary_path.read_text().lower()


@pytest.mark.asyncio
async def test_summarize_preserves_source_metadata(mock_inference, raw_file):
    vault, path = raw_file
    result = await summarize_raw_file(
        raw_path=path,
        vault_path=vault,
        inference=mock_inference,
    )
    from agent_core.utils.frontmatter import parse_frontmatter
    meta, body = parse_frontmatter(result.summary_path.read_text())
    assert meta["source_url"] == "https://example.com/test"
    assert meta["source_hash"] == "abc12345"
    assert meta["status"] == "summary"


@pytest.mark.asyncio
async def test_summarize_calls_inference_with_sanitized_content(mock_inference, raw_file):
    vault, path = raw_file
    await summarize_raw_file(
        raw_path=path,
        vault_path=vault,
        inference=mock_inference,
    )
    mock_inference.complete.assert_called_once()
    call_args = mock_inference.complete.call_args
    messages = call_args[0][0]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "BEGIN UNTRUSTED" in messages[1]["content"] or "UNTRUSTED" in messages[1]["content"].upper()


@pytest.mark.asyncio
async def test_summarize_refuses_oversized_body(mock_inference, raw_file):
    """Raw bodies larger than max_body_chars raise SourceTooLargeError
    before any inference call is made."""
    vault, path = raw_file
    big_body = "x" * 100
    path.write_text(
        "---\n"
        "title: Big File\n"
        "status: raw\n"
        "---\n"
        + big_body
    )
    with pytest.raises(SourceTooLargeError, match="exceeds summarize limit"):
        await summarize_raw_file(
            raw_path=path,
            vault_path=vault,
            inference=mock_inference,
            max_body_chars=50,
        )
    mock_inference.complete.assert_not_called()


@pytest.mark.asyncio
async def test_summarize_handles_inference_error(raw_file):
    vault, path = raw_file
    inference = AsyncMock()
    inference.complete.side_effect = RuntimeError("model offline")
    with pytest.raises(RuntimeError, match="model offline"):
        await summarize_raw_file(
            raw_path=path,
            vault_path=vault,
            inference=inference,
        )


@pytest.mark.asyncio
async def test_summarize_uses_clean_title_from_response(raw_file):
    """When the model emits TITLE: ..., the summary frontmatter gets that title."""
    vault, path = raw_file
    inference = AsyncMock()
    inference.complete.return_value = MockInferenceResult(
        content="TITLE: Clean Short Title\n\nThis is the summary body."
    )
    result = await summarize_raw_file(
        raw_path=path,
        vault_path=vault,
        inference=inference,
    )
    from agent_core.utils.frontmatter import parse_frontmatter
    meta, body = parse_frontmatter(result.summary_path.read_text())
    assert meta["title"] == "Clean Short Title"
    assert "This is the summary body." in body
    assert "TITLE:" not in body  # title line is stripped from body


@pytest.mark.asyncio
async def test_summarize_falls_back_to_raw_stem_when_no_title_prefix(raw_file):
    """When the model skips the TITLE: prefix, fall back to raw_stem."""
    vault, path = raw_file
    inference = AsyncMock()
    inference.complete.return_value = MockInferenceResult(
        content="No title prefix here, just a body."
    )
    result = await summarize_raw_file(
        raw_path=path,
        vault_path=vault,
        inference=inference,
    )
    from agent_core.utils.frontmatter import parse_frontmatter
    meta, body = parse_frontmatter(result.summary_path.read_text())
    # Expect fallback to the raw file's stem.
    assert meta["title"] == path.stem
    assert "No title prefix here" in body
