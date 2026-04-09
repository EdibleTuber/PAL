"""Markdown chunker -- split documents at top-level headings.

Used by /import to break large documents into separate articles.
Detects the highest heading level (H1 or H2) and splits there.
"""
import re
from dataclasses import dataclass

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


@dataclass
class Chunk:
    title: str
    body: str


def chunk_markdown(text: str, fallback_title: str) -> list[Chunk]:
    """Split markdown text at the highest heading level found.

    Args:
        text: markdown content to split
        fallback_title: title to use for content before the first heading,
                        or for the whole document if no headings exist

    Returns:
        list of Chunk(title, body). Empty list if text is blank.
    """
    if not text or not text.strip():
        return []

    # Find all headings and their levels
    headings = [(m.start(), len(m.group(1)), m.group(2).strip()) for m in _HEADING_RE.finditer(text)]

    if not headings:
        return [Chunk(title=fallback_title, body=text.strip())]

    # Detect highest (smallest number) heading level present
    split_level = min(level for _, level, _ in headings)

    # Filter to only headings at the split level
    split_points = [(pos, title) for pos, level, title in headings if level == split_level]

    if len(split_points) <= 1 and split_points[0][0] == 0:
        # Single heading at the start -- no splitting needed
        return [Chunk(title=split_points[0][1], body=text[split_points[0][0]:].strip())]

    chunks: list[Chunk] = []

    # Content before first heading
    first_pos = split_points[0][0]
    if first_pos > 0:
        pre_content = text[:first_pos].strip()
        if pre_content:
            chunks.append(Chunk(title=fallback_title, body=pre_content))

    # Each heading starts a chunk that runs until the next heading
    for i, (pos, title) in enumerate(split_points):
        if i + 1 < len(split_points):
            end = split_points[i + 1][0]
        else:
            end = len(text)

        body = text[pos:end].strip()
        if not body or body == f"{'#' * split_level} {title}":
            continue  # Skip empty chunks

        chunks.append(Chunk(title=title, body=body))

    return chunks
