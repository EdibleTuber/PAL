"""Research command — /research <topic or path>."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from agent_core.commands.base import Command
from agent_core.protocol.messages import ErrorMessage, ResponseMessage, ToolProgressMessage

logger = logging.getLogger(__name__)


class Research(Command):
    name = "research"
    args = "<t>"
    description = "Research a topic or file of topics"
    requires = ("websearch", "fetcher", "inference", "config")

    async def run(self, raw_args: str, ctx) -> AsyncIterator:
        from pal.researcher import Researcher, parse_topic_file

        args = raw_args.strip()
        if not args:
            yield ErrorMessage(error="Usage: /research [--verbose] [deep] <topic or path>")
            return

        # Parse flags
        verbose = False
        deep = False
        parts = args.split()
        remaining = []
        for part in parts:
            if part == "--verbose":
                verbose = True
            elif part == "deep" and not remaining:
                deep = True
            else:
                remaining.append(part)
        topic_or_path = " ".join(remaining)

        if not topic_or_path:
            yield ErrorMessage(error="Usage: /research [--verbose] [deep] <topic or path>")
            return

        depth = 10 if deep else 3

        # Progress callback - send ToolProgressMessage to client
        writer = ctx.writer

        async def send_progress(text: str) -> None:
            from agent_core.protocol import encode_message
            progress = ToolProgressMessage(tool="research", arguments={"status": text})
            writer.write(encode_message(progress))
            await writer.drain()

        def on_progress(text: str) -> None:
            asyncio.get_running_loop().create_task(send_progress(text))

        researcher = Researcher(
            websearch=ctx.agent.websearch,
            fetcher=ctx.agent.fetcher,
            inference=ctx.agent.inference,
            vault_path=ctx.agent.config.vault_path,
            on_progress=on_progress,
            max_body_chars=ctx.agent.config.max_inference_body_chars,
        )

        # Detect file vs topic
        candidate_path = ctx.agent.config.vault_path / topic_or_path
        if candidate_path.is_file():
            topics = parse_topic_file(candidate_path)
            if not topics:
                yield ErrorMessage(error=f"No topics found in {topic_or_path}")
                return
        else:
            topics = [topic_or_path]

        # Run research
        try:
            report = await researcher.research_topics(topics, depth=depth, verbose=verbose)
        except Exception as exc:
            logger.exception("Research failed: %s", exc)
            yield ErrorMessage(error=f"Research failed: {exc}")
            return

        # Format report
        from urllib.parse import urlparse
        lines = [f"Research complete: {len(report.results)} topic(s), "
                 f"{report.total_fetched} fetched, {report.total_summarized} summarized"]
        lines.append("")
        for res in report.results:
            source_count = len([s for s in res.sources if s.status == "ok"])
            lines.append(f"  {res.topic} ({source_count} source(s))")
            for src in res.sources:
                host = urlparse(src.url).hostname or src.url
                if src.status == "ok":
                    lines.append(f"    + {host} - {src.title}")
                else:
                    lines.append(f"    x {host} - {src.error or src.status}")
            lines.append("")

        if report.flagged_topics:
            for ft in report.flagged_topics:
                lines.append(f"  ! No usable results for: {ft}")
            lines.append("")

        lines.append("Summaries ready in raw/summaries/. Review and run /compile to add to wiki.")

        yield ResponseMessage(text="\n".join(lines), command="research")
