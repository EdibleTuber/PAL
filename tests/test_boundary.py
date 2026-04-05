"""Tests for GUID boundary wrapping and system prompt framing."""
import re

from pal.boundary import generate_guid, wrap_untrusted, SANITIZATION_SYSTEM_PROMPT


def test_generate_guid_is_unique():
    a = generate_guid()
    b = generate_guid()
    assert a != b


def test_generate_guid_looks_like_uuid():
    g = generate_guid()
    assert re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", g)


def test_wrap_untrusted_includes_guid():
    g = "test-guid-123"
    result = wrap_untrusted("hello world", g)
    assert g in result
    assert "hello world" in result
    assert "<untrusted-content" in result
    assert "</untrusted-content>" in result


def test_wrap_untrusted_uses_opening_and_closing_tags():
    g = "abc"
    result = wrap_untrusted("body", g)
    assert result.count(f'id="{g}"') == 1
    assert result.endswith("</untrusted-content>")


def test_system_prompt_mentions_untrusted_content():
    assert "untrusted-content" in SANITIZATION_SYSTEM_PROMPT
    assert "data" in SANITIZATION_SYSTEM_PROMPT.lower()
    assert "instruction" in SANITIZATION_SYSTEM_PROMPT.lower()


def test_system_prompt_forbids_following_instructions():
    text = SANITIZATION_SYSTEM_PROMPT.lower()
    assert "never follow" in text or "do not follow" in text or "must not follow" in text
