"""Article format -- compiled truth + timeline.

Every compiled wiki article has two zones separated by a marker:
- Compiled truth: current best understanding, rewritten on new evidence
- Timeline: append-only evidence trail, one entry per source

The model writes compiled truth prose. Code builds timeline entries.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from pal.frontmatter import parse_frontmatter, serialize_frontmatter

TIMELINE_MARKER = "<!-- TIMELINE -->"


@dataclass
class TimelineEntry:
    date: str            # YYYY-MM-DD
    source_label: str    # hostname or short label
    source_url: str      # full URL
    source_hash: str     # content hash from raw file
    added: str           # ISO timestamp
    summary: str         # thorough summary text


@dataclass
class Article:
    meta: dict                      # YAML frontmatter
    compiled_truth: str             # everything above TIMELINE marker
    timeline: list[TimelineEntry] = field(default_factory=list)


def _format_timeline_entry(entry: TimelineEntry) -> str:
    """Format a single timeline entry as markdown."""
    lines = [
        f"### {entry.date} - {entry.source_label}",
        f"**Source:** {entry.source_url}",
        f"**Added:** {entry.added}",
        f"**Source hash:** {entry.source_hash}",
        "",
        entry.summary.strip(),
    ]
    return "\n".join(lines)


def serialize_article(article: Article) -> str:
    """Assemble an Article into a complete markdown string with frontmatter."""
    truth = article.compiled_truth.strip() + "\n"

    timeline_parts = []
    for entry in article.timeline:
        timeline_parts.append(_format_timeline_entry(entry))

    timeline_text = "\n\n".join(timeline_parts)

    body = f"{truth}\n{TIMELINE_MARKER}\n"
    if timeline_text:
        body += f"\n{timeline_text}\n"

    return serialize_frontmatter(article.meta, body)
