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

from agent_core.utils.frontmatter import parse_frontmatter, serialize_frontmatter

TIMELINE_MARKER = "<!-- TIMELINE -->"


@dataclass
class TimelineEntry:
    date: str            # YYYY-MM-DD
    source_label: str    # hostname or short label
    source_url: str      # full URL
    source_hash: str     # content hash from raw file
    added: str           # ISO timestamp
    summary: str         # thorough summary text
    source_type: str = "external"  # provenance class: "external" or "chat"


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
    ]
    if entry.source_type != "external":
        lines.append(f"**Source type:** {entry.source_type}")
    lines.extend(["", entry.summary.strip()])
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
        source_type = "external"

        # Order matters: "**Source type:**" must be checked before "**Source:**"
        # because the latter is a prefix substring of the former.
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("**Source type:**"):
                source_type = stripped.replace("**Source type:**", "").strip() or "external"
            elif stripped.startswith("**Source:**"):
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
            source_type=source_type,
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
    source_file: str = "",
    source_type: str = "external",
) -> Article:
    """Append a new timeline entry and update frontmatter sources list.

    Returns a new Article with the entry appended (does not mutate input).
    If source_file is provided (non-empty), it is stored in the sources entry
    alongside url and hash so local/PDF sources can be identified later.
    source_type defaults to "external"; non-default values are stored on both
    the TimelineEntry and the meta.sources entry so chat-derived and other
    provenance can be distinguished later.
    """
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    added_str = now.isoformat(timespec="seconds")

    parsed_url = urlparse(source_url)
    label = parsed_url.hostname or source_url
    if not label:
        # Non-URL provenance (e.g. source_type == "chat"). Use the type
        # as the label so the timeline header is non-empty and survives
        # parse round-trip.
        label = source_type if source_type != "external" else "unknown"

    entry = TimelineEntry(
        date=date_str,
        source_label=label,
        source_url=source_url,
        source_hash=source_hash,
        added=added_str,
        summary=summary.strip(),
        source_type=source_type,
    )

    new_timeline = list(article.timeline) + [entry]

    new_sources = list(article.meta.get("sources", []))
    source_entry = {
        "url": source_url,
        "hash": source_hash,
        "added": added_str,
    }
    if source_file:
        source_entry["source_file"] = source_file
    if source_type != "external":
        source_entry["source_type"] = source_type
    new_sources.append(source_entry)

    new_meta = dict(article.meta)
    new_meta["sources"] = new_sources

    return Article(
        meta=new_meta,
        compiled_truth=article.compiled_truth,
        timeline=new_timeline,
    )


TOPIC_MATCH_PROMPT = (
    "You are checking if a new source covers the same topic as an existing "
    "wiki article. Below is the new source title and preview, followed by a "
    "list of existing articles in this category.\n\n"
    "If an existing article covers the same topic (even with different "
    "phrasing), respond with ONLY the filename (e.g., 'sqlite-vec-search.md').\n"
    "If no existing article matches, respond with exactly: NONE"
)


async def find_existing_article(
    summary_title: str,
    summary_preview: str,
    category: str,
    articles: list[dict],
    inference,
) -> dict | None:
    """Check if an existing article covers the same topic as the new source.

    Args:
        summary_title: title of the summary being compiled
        summary_preview: first ~400 chars of the summary
        category: target category directory
        articles: list of dicts with 'path' and 'title' keys
        inference: InferenceClient

    Returns:
        The matching article dict, or None if no match.
    """
    category_articles = [a for a in articles if a["path"].startswith(f"{category}/")]
    if not category_articles:
        return None

    article_list = "\n".join(
        f"- {a['path'].split('/')[-1]}: {a['title']}" for a in category_articles
    )

    user_prompt = (
        f"New source title: {summary_title}\n"
        f"New source preview: {summary_preview[:400]}\n\n"
        f"Existing articles in {category}/:\n{article_list}"
    )

    messages = [
        {"role": "system", "content": TOPIC_MATCH_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    try:
        result = await inference.complete(messages, reasoning="off")
        response = (result.content or "").strip()
    except Exception:
        return None

    if not response or response.upper() == "NONE":
        return None

    response_clean = response.strip().strip("'\"")
    for a in category_articles:
        filename = a["path"].split("/")[-1]
        if filename == response_clean or filename.replace(".md", "") == response_clean.replace(".md", ""):
            return a

    return None


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
