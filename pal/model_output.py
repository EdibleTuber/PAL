"""Clean model output of special tokens and reasoning leaks.

Some models (e.g. Gemma 4) leak internal special tokens and reasoning
content into their output even when reasoning is disabled. This module
strips those artifacts so downstream consumers get clean text.

Artifacts handled:
- Special tokens: <|end|>, <|think|>, <|response|>, <|system|>, etc.
- Reasoning blocks: <think>...</think>
- Duplicated responses when the model re-processes its own prompt
"""
import re


# Matches well-formed <|anything|> tokens (e.g. <|end|>, <|/think|>, <|endoftext|>)
_SPECIAL_TOKEN_RE = re.compile(r"<\|[^|>\n]*\|>")

# Matches malformed leftovers like <|end| (missing >) or lone <| at start of a line
_MALFORMED_TOKEN_RE = re.compile(r"<\|[^\n]*?(?:\|>|\||$)", re.MULTILINE)

# Strips any leftover standalone <| or |> fragments
_TOKEN_FRAGMENT_RE = re.compile(r"<\||\|>")

# Matches <think>...</think> blocks, including multiline content
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# Matches token-style think blocks: <|think|>...<|/think|> (some models use these)
_TOKEN_THINK_BLOCK_RE = re.compile(r"<\|think\|>.*?<\|/think\|>", re.DOTALL | re.IGNORECASE)

# Matches token-style response wrappers: <|response|>...<|/response|>
_TOKEN_RESPONSE_BLOCK_RE = re.compile(r"<\|response\|>(.*?)<\|/response\|>", re.DOTALL | re.IGNORECASE)

# Matches standalone <think> or </think> when unpaired
_ORPHAN_THINK_RE = re.compile(r"</?think>", re.IGNORECASE)

# Collapses 3+ consecutive newlines to 2
_MULTIPLE_BLANK_LINES_RE = re.compile(r"\n{3,}")

# Fragment of the summarization prompt that sometimes leaks back into output
_PROMPT_LEAK_MARKER = "Summarize the following content concisely and factually"


def clean_model_output(text: str) -> str:
    """Remove special tokens, reasoning blocks, and duplicate responses.

    Returns cleaned text. Idempotent - cleaning already-clean text returns
    the same output.
    """
    if not text:
        return ""

    # Handle duplicate response case: if the prompt text appears in the body,
    # the model likely responded twice. Split on the prompt marker and keep
    # the last substantial chunk (usually the second, more complete response).
    if _PROMPT_LEAK_MARKER in text:
        parts = text.split(_PROMPT_LEAK_MARKER)
        # Keep the longest part (most content)
        text = max(parts, key=len)

    # Remove token-style think blocks (<|think|>...<|/think|>) - content included
    text = _TOKEN_THINK_BLOCK_RE.sub("", text)

    # Unwrap token-style response blocks (keep content, drop wrapper)
    text = _TOKEN_RESPONSE_BLOCK_RE.sub(r"\1", text)

    # Remove XML-style think blocks entirely (paired tags)
    text = _THINK_BLOCK_RE.sub("", text)

    # Remove orphan <think> or </think> tags that didn't pair up
    text = _ORPHAN_THINK_RE.sub("", text)

    # Strip well-formed <|...|> special tokens
    text = _SPECIAL_TOKEN_RE.sub("", text)

    # Strip malformed variants (e.g. "<|end|" without closing >)
    text = _MALFORMED_TOKEN_RE.sub("", text)

    # Clean up any lingering <| or |> fragments
    text = _TOKEN_FRAGMENT_RE.sub("", text)

    # Collapse multiple blank lines
    text = _MULTIPLE_BLANK_LINES_RE.sub("\n\n", text)

    return text.strip()
