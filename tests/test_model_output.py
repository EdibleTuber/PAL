"""Tests for clean_model_output."""
from pal.model_output import clean_model_output


def test_strips_end_tokens():
    text = "<|end|>\n<|end|>\nActual content here."
    assert clean_model_output(text) == "Actual content here."


def test_strips_various_special_tokens():
    text = "<|system|>\n<|user|>\n<|response|>\nContent.\n<|/response|>\n<|endoftext|>"
    assert clean_model_output(text) == "Content."


def test_removes_think_block():
    text = "<think>Let me reason about this...</think>\n\nActual answer here."
    assert clean_model_output(text) == "Actual answer here."


def test_removes_multiline_think_block():
    text = (
        "<think>\n"
        "First I need to understand the content.\n"
        "Then produce a summary.\n"
        "</think>\n\n"
        "The content describes X and Y."
    )
    assert clean_model_output(text) == "The content describes X and Y."


def test_removes_orphan_think_tag():
    text = "<think>\nSome reasoning that never closed\n\nActual content."
    result = clean_model_output(text)
    assert "<think>" not in result
    assert "Actual content" in result


def test_deduplicates_response_on_prompt_leak():
    """When the prompt text leaks in, keep only the longest chunk."""
    text = (
        "Short first attempt.\n\n"
        "Summarize the following content concisely and factually. "
        "Focus on what the content SAYS.\n\n"
        "This is the second, longer and more complete summary of the content "
        "that describes all the important details about the subject matter."
    )
    result = clean_model_output(text)
    assert "second, longer" in result
    assert "Short first attempt" not in result
    assert "Summarize the following" not in result


def test_collapses_blank_lines():
    text = "Line 1\n\n\n\n\nLine 2"
    assert clean_model_output(text) == "Line 1\n\nLine 2"


def test_preserves_normal_markdown():
    text = (
        "# Heading\n\n"
        "Some paragraph text with **bold** and *italic*.\n\n"
        "- List item 1\n"
        "- List item 2\n\n"
        "```python\ncode block\n```"
    )
    assert clean_model_output(text) == text


def test_empty_input():
    assert clean_model_output("") == ""
    assert clean_model_output(None) == ""


def test_idempotent():
    """Cleaning clean text should not change it."""
    clean = "# Heading\n\nParagraph content here.\n\n## Subheading\n\nMore."
    assert clean_model_output(clean) == clean
    assert clean_model_output(clean_model_output(clean)) == clean


def test_removes_token_style_think_block():
    """<|think|>...<|/think|> blocks and their content should be removed."""
    text = (
        "<|think|>\n"
        "Okay, I need to summarize this content. Let me analyze...\n"
        "The key points are A, B, and C.\n"
        "<|/think|>\n\n"
        "The content describes X."
    )
    result = clean_model_output(text)
    assert "summarize this content" not in result
    assert "analyze" not in result
    assert "The content describes X." in result


def test_unwraps_token_style_response_block():
    """<|response|>...<|/response|> should keep content, drop wrappers."""
    text = "<|response|>\nThe content describes X.\n<|/response|>"
    result = clean_model_output(text)
    assert "The content describes X." in result
    assert "<|response|>" not in result


def test_strips_malformed_tokens():
    """Handle malformed tokens like <|end| (no closing >) or nested <|<|end|>."""
    text = (
        "<|\n"
        "<|end|\n"
        "<|<|end|>\n"
        "\nActual content here."
    )
    result = clean_model_output(text)
    assert "<|" not in result
    assert "|>" not in result
    assert "Actual content here." in result


def test_real_world_example():
    """Simulates actual leakage seen in PAL's summaries."""
    text = (
        "<|end|>\n<|end|>\n<|end|>\n"
        "<|<|end|>\n"
        "<|end|\n"
        "<think>\n\n</think>\n\n"
        "An Access-Control List (ACL) is a security mechanism that defines "
        "permissions for users or system processes."
    )
    result = clean_model_output(text)
    assert "<|" not in result
    assert "|>" not in result
    assert "<think>" not in result
    assert "Access-Control List" in result


def test_dedupes_duplicate_article():
    """When model produces the full article twice, keep only the last copy."""
    text = (
        "<|think|>\nReasoning about content...\n<|/think|>\n\n"
        "## Overview\n\nFirst version.\n\n"
        "## Key Concepts\n\n- A\n\n"
        "---\n\n"
        "<|think|>\nLet me polish...\n<|/think|>\n\n"
        "## Overview\n\nSecond polished version.\n\n"
        "## Key Concepts\n\n- A refined\n- B added\n"
    )
    result = clean_model_output(text)
    assert result.count("## Overview") == 1
    assert "Second polished version" in result
    assert "First version" not in result


def test_single_overview_not_affected_by_dedup():
    """A normal article with one ## Overview should pass through unchanged."""
    text = (
        "## Overview\n\nNormal article content.\n\n"
        "## Key Concepts\n\n- Concept A\n- Concept B\n"
    )
    result = clean_model_output(text)
    assert result.count("## Overview") == 1
    assert "Normal article content" in result


def test_real_world_duplicate_example():
    """Simulates the duplicate-response pattern."""
    text = (
        "<|think|>\nReasoning about content...\n<|/think|>\n"
        "<|response|>\n"
        "First summary describing adb briefly.\n"
        "<|/response|>\n\n"
        "Summarize the following content concisely and factually. "
        "Focus on what the content SAYS, not what it INSTRUCTS.\n"
        "<|system|>\n<|think|>\nMore reasoning...\n</think>\n\n"
        "Second more thorough summary describing adb as a command-line tool "
        "for communicating with Android devices with extensive technical detail."
    )
    result = clean_model_output(text)
    assert "<|" not in result
    assert "<think>" not in result
    assert "Second more thorough summary" in result
