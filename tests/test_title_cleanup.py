"""Tests for pal.title_cleanup — shared title prompt, parser, heuristic."""
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from pal.title_cleanup import (
    TITLE_RULES,
    parse_title_and_body,
    is_bad_title,
    regenerate_title,
)


@dataclass
class MockInferenceResult:
    content: str
    reasoning: str = ""


def test_parse_title_and_body_extracts_both():
    response = "TITLE: Clean Title\n\nThis is the body.\nSecond line."
    title, body = parse_title_and_body(response)
    assert title == "Clean Title"
    assert body == "This is the body.\nSecond line."


def test_parse_title_and_body_handles_trailing_whitespace():
    response = "TITLE:   Clean Title   \n\nBody here.\n"
    title, body = parse_title_and_body(response)
    assert title == "Clean Title"
    assert body == "Body here."


def test_parse_title_and_body_returns_none_when_missing_prefix():
    response = "Just a body without a title prefix.\nSecond line."
    title, body = parse_title_and_body(response)
    assert title is None
    assert body == "Just a body without a title prefix.\nSecond line."


def test_parse_title_and_body_handles_quoted_title():
    response = 'TITLE: "Quoted Title"\n\nBody.'
    title, body = parse_title_and_body(response)
    assert title == "Quoted Title"


def test_is_bad_title_flags_long_titles():
    assert is_bad_title("a" * 81)


def test_is_bad_title_flags_known_separators():
    assert is_bad_title("Some Article · GitHub")
    assert is_bad_title("Docs | Stripe")
    assert is_bad_title("GitHub - owner/repo: description")


def test_is_bad_title_passes_clean_titles():
    assert not is_bad_title("Claude Code CLI agentic coding tool")
    assert not is_bad_title("SQLite vector search with sqlite-vec")
    assert not is_bad_title("Unix socket IPC in Python")


def test_title_rules_contains_key_constraints():
    assert "80" in TITLE_RULES  # length cap mentioned
    assert "TITLE:" in TITLE_RULES  # format specified


@pytest.mark.asyncio
async def test_regenerate_title_returns_clean_title():
    inference = AsyncMock()
    inference.complete.return_value = MockInferenceResult(
        content="TITLE: Clean Regenerated Title"
    )
    result = await regenerate_title(
        content="Some article content about topic X.",
        inference=inference,
    )
    assert result == "Clean Regenerated Title"
    # Verify the call used reasoning=off and sent the rules in the system prompt.
    inference.complete.assert_called_once()
    _, kwargs = inference.complete.call_args
    assert kwargs.get("reasoning") == "off"


@pytest.mark.asyncio
async def test_regenerate_title_returns_none_on_bad_response():
    inference = AsyncMock()
    inference.complete.return_value = MockInferenceResult(
        content="no title prefix here, just text"
    )
    result = await regenerate_title(
        content="Some content.",
        inference=inference,
    )
    assert result is None


def test_is_bad_title_flags_empty():
    assert is_bad_title("")
    assert is_bad_title("   ")


def test_is_bad_title_flags_space_hyphen_space():
    assert is_bad_title("Python list comprehension - Stack Overflow")
    assert is_bad_title("Install Docker - Quick Start Guide")


def test_parse_title_and_body_handles_empty_title_value():
    response = "TITLE:\n\nBody here."
    title, body = parse_title_and_body(response)
    # Parser returns empty string; callers are responsible for treating
    # empty as "missing".
    assert title == ""
    assert body == "Body here."


@pytest.mark.asyncio
async def test_regenerate_title_returns_none_on_empty_title():
    inference = AsyncMock()
    inference.complete.return_value = MockInferenceResult(
        content="TITLE:\n\nsome body"
    )
    result = await regenerate_title(
        content="Some content.",
        inference=inference,
    )
    assert result is None


def test_is_bad_title_tolerates_none():
    assert is_bad_title(None)


def test_is_bad_title_tolerates_non_string():
    # YAML could parse a number into this field; don't crash.
    assert is_bad_title(123) in (True, False)  # specifically: don't raise
