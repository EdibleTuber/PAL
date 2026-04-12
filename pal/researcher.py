"""Researcher -- search, fetch, summarize orchestration for topic research.

Searches SearxNG for topics, fetches top results, summarizes each.
Handles query refinement on thin results, URL dedup across topics,
and progress reporting.
"""
import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from pal.fetcher import FetchError
from pal.frontmatter import serialize_frontmatter
from pal.summarizer import summarize_raw_file

logger = logging.getLogger(__name__)

_REFINEMENT_SUFFIXES = ["tutorial", "documentation", "guide"]


def parse_topic_file(path: Path) -> list[str]:
    """Parse a markdown file, return top-level bullet items as topic strings."""
    topics = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            topic = stripped[2:].strip()
            if topic:
                topics.append(topic)
    return topics


def _slugify(text: str, max_len: int = 30) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len]


def _url_slug(url: str, max_len: int = 30) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "unknown").replace(".", "-")
    path = parsed.path.strip("/").replace("/", "-")
    combined = f"{host}-{path}" if path else host
    cleaned = re.sub(r"[^a-z0-9-]+", "", combined.lower())
    return cleaned[:max_len]


@dataclass
class SourceResult:
    url: str
    title: str
    raw_path: Optional[Path] = None
    summary_path: Optional[Path] = None
    status: str = "ok"
    error: Optional[str] = None


@dataclass
class ResearchResult:
    topic: str
    sources: list[SourceResult] = field(default_factory=list)
    refined_query: Optional[str] = None
    flagged: bool = False


@dataclass
class ResearchReport:
    results: list[ResearchResult] = field(default_factory=list)
    total_fetched: int = 0
    total_summarized: int = 0
    total_failed: int = 0
    flagged_topics: list[str] = field(default_factory=list)


class Researcher:
    """Orchestrates search -> fetch -> summarize for research topics."""

    def __init__(
        self,
        websearch,
        fetcher,
        inference,
        vault_path: Path,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.websearch = websearch
        self.fetcher = fetcher
        self.inference = inference
        self.vault_path = vault_path
        self.on_progress = on_progress
        self._fetched_urls: set[str] = set()

    def _progress(self, msg: str) -> None:
        if self.on_progress:
            self.on_progress(msg)
        logger.info(msg)

    async def _search_with_refinement(self, topic: str, depth: int):
        """Search SearxNG, refine query if fewer than depth unique results."""
        results = await self.websearch.search(topic)
        # Filter out already-fetched URLs
        unique = []
        seen_urls = set()
        for r in results:
            if r.url not in self._fetched_urls and r.url not in seen_urls:
                seen_urls.add(r.url)
                unique.append(r)

        refined_query = None

        if len(unique) < depth:
            for suffix in _REFINEMENT_SUFFIXES:
                if len(unique) >= depth:
                    break
                refined = f"{topic} {suffix}"
                refined_query = refined
                self._progress(f"Refining search: {refined}")
                extra = await self.websearch.search(refined)
                for r in extra:
                    if r.url not in self._fetched_urls and r.url not in seen_urls:
                        seen_urls.add(r.url)
                        unique.append(r)

        return unique[:depth], refined_query

    async def _fetch_and_save(self, url: str, topic_slug: str) -> SourceResult:
        """Fetch a URL, save raw content to vault, return SourceResult."""
        try:
            fetch_result = await self.fetcher.fetch(url)
        except FetchError as exc:
            return SourceResult(
                url=url,
                title="",
                status="fetch_failed",
                error=str(exc),
            )
        except Exception as exc:
            return SourceResult(
                url=url,
                title="",
                status="fetch_failed",
                error=str(exc),
            )

        if not fetch_result.text.strip():
            return SourceResult(
                url=url,
                title=fetch_result.title or "",
                status="extract_empty",
                error="trafilatura returned empty content",
            )

        url_slug = _url_slug(url)
        hash8 = fetch_result.content_hash[:8]
        filename = f"{topic_slug}-{url_slug}-{hash8}.md"

        raw_dir = self.vault_path / "raw" / "web"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / filename

        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        meta = {
            "source_url": url,
            "title": fetch_result.title or url_slug,
            "fetched_at": fetched_at,
            "content_hash": fetch_result.content_hash,
            "byte_size": fetch_result.byte_size,
            "status": "raw",
        }
        raw_path.write_text(serialize_frontmatter(meta, fetch_result.text))

        return SourceResult(
            url=url,
            title=fetch_result.title,
            raw_path=raw_path,
            status="ok",
        )

    async def _summarize(self, source: SourceResult) -> SourceResult:
        """Summarize a successfully fetched source."""
        if source.status != "ok" or source.raw_path is None:
            return source
        try:
            result = await summarize_raw_file(
                raw_path=source.raw_path,
                vault_path=self.vault_path,
                inference=self.inference,
            )
            source.summary_path = result.summary_path
        except Exception as exc:
            logger.warning("Summarize failed for %s: %s", source.url, exc)
            source.status = "summarize_failed"
            source.error = str(exc)
        return source

    async def _research_one(self, topic: str, depth: int, verbose: bool) -> ResearchResult:
        """Search + fetch + summarize for one topic."""
        self._progress(f"Researching: {topic}")
        topic_slug = _slugify(topic)

        search_results, refined_query = await self._search_with_refinement(topic, depth)

        if not search_results:
            self._progress(f"No results found for: {topic}")
            return ResearchResult(topic=topic, flagged=True)

        # Mark URLs as fetched before launching concurrent fetches
        urls_to_fetch = [r.url for r in search_results]
        for url in urls_to_fetch:
            self._fetched_urls.add(url)

        # Concurrent fetches within the topic
        self._progress(f"Fetching {len(urls_to_fetch)} sources for: {topic}")
        fetch_tasks = [self._fetch_and_save(url, topic_slug) for url in urls_to_fetch]
        sources = await asyncio.gather(*fetch_tasks)
        sources = list(sources)

        # Summarize each fetched source
        self._progress(f"Summarizing sources for: {topic}")
        summarize_tasks = [self._summarize(s) for s in sources]
        sources = list(await asyncio.gather(*summarize_tasks))

        return ResearchResult(
            topic=topic,
            sources=sources,
            refined_query=refined_query,
        )

    async def research_topic(self, topic: str, depth: int = 3, verbose: bool = False) -> ResearchReport:
        """Convenience wrapper for researching a single topic."""
        return await self.research_topics([topic], depth=depth, verbose=verbose)

    async def research_topics(
        self,
        topics: list[str],
        depth: int = 3,
        verbose: bool = False,
    ) -> ResearchReport:
        """Main entry point -- process topics sequentially, return report."""
        self._fetched_urls = set()
        report = ResearchReport()

        for topic in topics:
            result = await self._research_one(topic, depth, verbose)
            report.results.append(result)

            if result.flagged:
                report.flagged_topics.append(result.topic)

        # Tally
        for result in report.results:
            for source in result.sources:
                if source.status == "ok":
                    report.total_fetched += 1
                    if source.summary_path is not None:
                        report.total_summarized += 1
                else:
                    report.total_failed += 1

        self._progress(
            f"Research complete: {report.total_fetched} fetched, "
            f"{report.total_summarized} summarized, {report.total_failed} failed"
        )
        return report
