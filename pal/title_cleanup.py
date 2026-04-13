"""Shared title generation and cleanup logic.

Used by the summarizer (to generate clean titles for new summaries)
and by the backfill CLI (to regenerate titles on existing articles).
"""
import logging

logger = logging.getLogger(__name__)


TITLE_RULES = """You generate a clean title for the content below.

Rules:
- Max 80 characters.
- Strip trailing site names (e.g. " - Stack Overflow", " · GitHub", " | Docs").
- Sentence case. No surrounding quotes.
- Describe what the content IS, not where it lives. Prefer
  "Claude Code CLI agentic coding tool" over "GitHub - codeaashu/claude-code".

Respond with exactly one line in this format:

TITLE: <your title>
"""


def parse_title_and_body(response: str) -> tuple[str | None, str]:
    """Parse a model response that should start with `TITLE: <title>`.

    Returns (title, body). If the response does not start with `TITLE:`,
    title is None and body is the full response unchanged.

    The body is everything after the title line's trailing newline(s),
    stripped of leading/trailing whitespace.
    """
    stripped = response.lstrip()
    if not stripped.startswith("TITLE:"):
        return None, response.strip()

    # Split on first newline after the TITLE line.
    first_newline = stripped.find("\n")
    if first_newline == -1:
        title_line = stripped
        body = ""
    else:
        title_line = stripped[:first_newline]
        body = stripped[first_newline + 1 :].strip()

    title = title_line[len("TITLE:") :].strip()
    # Strip surrounding quotes if present.
    if len(title) >= 2 and title[0] == title[-1] and title[0] in ("'", '"'):
        title = title[1:-1].strip()

    return title, body


_BAD_TITLE_SUBSTRINGS = (
    " · ",
    " | ",
    " - ",
    "GitHub -",
)


def is_bad_title(title: str) -> bool:
    """Return True if the title should be regenerated during backfill."""
    if not title.strip():
        return True
    if len(title) > 80:
        return True
    for marker in _BAD_TITLE_SUBSTRINGS:
        if marker in title:
            return True
    return False


async def regenerate_title(content: str, inference) -> str | None:
    """Ask the inference client to generate a clean title for the given content.

    Returns the cleaned title, or None if the model response did not conform
    to the expected `TITLE:` format.
    """
    messages = [
        {"role": "system", "content": TITLE_RULES},
        {"role": "user", "content": content},
    ]
    result = await inference.complete(messages, reasoning="off")
    raw = result.content or ""
    title, _ = parse_title_and_body(raw)
    if not title or not title.strip():
        logger.warning("regenerate_title: model response missing or empty TITLE: %r", raw[:200])
        return None
    return title
