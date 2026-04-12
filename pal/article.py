"""Article format -- compiled truth + timeline.

Every compiled wiki article has two zones separated by a marker:
- Compiled truth: current best understanding, rewritten on new evidence
- Timeline: append-only evidence trail, one entry per source

The model writes compiled truth prose. Code builds timeline entries.
"""
import re
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


_ENTRY_HEADER_RE = re.compile(r"^### (\d{4}-\d{2}-\d{2}) - (.+)$", re.MULTILINE)


def _parse_timeline_entries(timeline_text: str) -> list[TimelineEntry]:
    """Parse the timeline section into a list of TimelineEntry objects."""
    entries = []
    parts = _ENTRY_HEADER_RE.split(timeline_text)
    # parts[0] is text before first header (usually empty/whitespace)
    # then triples: (date, label, body)
    i = 1
    while i + 2 <= len(parts) - 1:
        date = parts[i]
        label = parts[i + 1]
        body = parts[i + 2].strip()

        source_url = ""
        source_hash = ""
        added = ""
        summary_lines = []

        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("**Source:**"):
                source_url = stripped.replace("**Source:**", "").strip()
            elif stripped.startswith("**Added:**"):
                added = stripped.replace("**Added:**", "").strip()
            elif stripped.startswith("**Source hash:**"):
                source_hash = stripped.replace("**Source hash:**", "").strip()
            elif stripped:
                summary_lines.append(stripped)

        entries.append(TimelineEntry(
            date=date,
            source_label=label,
            source_url=source_url,
            source_hash=source_hash,
            added=added,
            summary="\n".join(summary_lines),
        ))
        i += 3

    return entries


def parse_article(text: str) -> Article:
    """Parse a markdown article into an Article with compiled truth and timeline.

    If no TIMELINE marker exists (legacy article), the entire body is
    compiled truth and timeline is empty.
    """
    meta, body = parse_frontmatter(text)

    if TIMELINE_MARKER in body:
        parts = body.split(TIMELINE_MARKER, 1)
        compiled_truth = parts[0].strip() + "\n"
        timeline_text = parts[1]
        timeline = _parse_timeline_entries(timeline_text)
    else:
        compiled_truth = body
        timeline = []

    return Article(meta=meta, compiled_truth=compiled_truth, timeline=timeline)


def append_timeline_entry(
    article: Article,
    source_url: str,
    source_hash: str,
    summary: str,
) -> Article:
    """Append a new timeline entry and update frontmatter sources list.

    Returns a new Article with the entry appended (does not mutate input).
    """
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    added_str = now.isoformat(timespec="seconds")

    parsed_url = urlparse(source_url)
    label = parsed_url.hostname or source_url

    entry = TimelineEntry(
        date=date_str,
        source_label=label,
        source_url=source_url,
        source_hash=source_hash,
        added=added_str,
        summary=summary.strip(),
    )

    new_timeline = list(article.timeline) + [entry]

    new_sources = list(article.meta.get("sources", []))
    new_sources.append({
        "url": source_url,
        "hash": source_hash,
        "added": added_str,
    })

    new_meta = dict(article.meta)
    new_meta["sources"] = new_sources

    return Article(
        meta=new_meta,
        compiled_truth=article.compiled_truth,
        timeline=new_timeline,
    )


_REQUIRED_SECTIONS = ["## Overview", "## Key Concepts"]


def validate_compiled_truth(text: str) -> list[str]:
    """Check compiled truth text for required sections.

    Returns a list of issues. Empty list means valid.
    """
    issues = []
    for section in _REQUIRED_SECTIONS:
        if section not in text:
            section_name = section.replace("## ", "")
            issues.append(f"Missing required section: {section_name}")
    return issues
