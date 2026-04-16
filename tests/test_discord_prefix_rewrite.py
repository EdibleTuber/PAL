from pal.discord_adapter import rewrite_slash_prefixes


def test_rewrites_known_command_at_line_start():
    out = rewrite_slash_prefixes("Use /learn to extract learnings.")
    assert out == "Use !learn to extract learnings."


def test_rewrites_multiple_known_commands():
    out = rewrite_slash_prefixes("Try /learnings or /promote <slug>.")
    assert "!learnings" in out and "!promote" in out
    assert "/learnings" not in out and "/promote" not in out


def test_does_not_rewrite_unknown_tokens():
    # /not-a-command stays as-is.
    out = rewrite_slash_prefixes("See /not-a-command for details.")
    assert "/not-a-command" in out


def test_does_not_rewrite_inside_code_fence():
    src = "```\n/learn should stay as /learn\n```"
    out = rewrite_slash_prefixes(src)
    assert out == src


def test_does_not_rewrite_inside_inline_code():
    src = "Use `/learn` inline."
    out = rewrite_slash_prefixes(src)
    assert out == src


def test_rewrites_after_punctuation():
    out = rewrite_slash_prefixes("First: /learn, then /promote <slug>.")
    assert "!learn" in out and "!promote" in out
