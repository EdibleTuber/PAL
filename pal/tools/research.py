"""PAL research tools — propose-then-execute with user approval gating."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from agent_core.tools.base import Tool

if TYPE_CHECKING:
    from agent_core.agent import HandlerContext


def _format_research_report(report, vault_path) -> str:
    """Format a ResearchReport for the LLM. Preserves the exact shape from
    pal._legacy_tools.ToolExecutor._format_research_report."""
    lines = [
        f"Research complete: {report.total_summarized} summarized, "
        f"{report.total_fetched} fetched, {report.total_failed} failed."
    ]
    for result in report.results:
        lines.append(f"\nTopic: {result.topic}")
        if result.refined_query:
            lines.append(f"  (refined query: {result.refined_query})")
        if result.flagged:
            lines.append("  ! no usable results")
        for source in result.sources:
            marker = "+" if source.status == "ok" else "x"
            lines.append(f"  {marker} {source.title}")
            lines.append(f"    {source.url}")
            if source.summary_path:
                try:
                    rel = source.summary_path.relative_to(vault_path)
                    lines.append(f"    summary: {rel}")
                except ValueError:
                    lines.append(f"    summary: {source.summary_path}")
            if source.error:
                lines.append(f"    error: {source.error}")
    return "\n".join(lines)


class ProposeResearch(Tool):
    name = "propose_research"
    description = (
        "Propose a web research run. Provide either `topic` (single string) "
        "for one topic, or `topics` (array of strings) for a batch with "
        "cross-topic URL deduplication. Exactly one is required. Emits a "
        "proposal to the user and blocks until they approve, decline, or "
        "edit it. Returns a JSON object with the final status and proposal_id. "
        "Use research_topic to execute an approved proposal."
    )
    parameters = {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "Single topic string. Use this OR `topics`, not both.",
            },
            "topics": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of topics for batch research with cross-topic URL dedup. Use this OR `topic`, not both.",
            },
            "depth": {
                "type": "integer",
                "description": "Number of sources to fetch per topic (1-10, default 3).",
            },
            "rationale": {
                "type": "string",
                "description": "One-line reason shown to the user.",
            },
        },
        "required": ["rationale"],
    }
    requires = ("approval_registry",)

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        from pal.protocol import ResearchProposalMessage

        if ctx.agent.approval_registry is None:
            return "Error: research proposals are not available in this session."

        rationale = args.get("rationale", "").strip()
        if not rationale:
            return "Error: 'rationale' parameter is required."

        raw_topic = args.get("topic", "")
        topic = raw_topic.strip() if isinstance(raw_topic, str) else ""
        raw_topics = args.get("topics")
        topics: list[str] = []
        if isinstance(raw_topics, list):
            topics = [t.strip() for t in raw_topics if isinstance(t, str) and t.strip()]

        if not topic and not topics:
            return "Error: provide exactly one of 'topic' or 'topics'."
        if topic and topics:
            return "Error: provide exactly one of 'topic' or 'topics'."
        if raw_topics is not None and isinstance(raw_topics, list) and not topics:
            return "Error: 'topics' must be a non-empty list of non-empty strings."

        depth = int(args.get("depth", 3))
        depth = max(1, min(depth, 10))

        ar = ctx.agent.approval_registry

        if topics:
            # Multi-topic mode: build human-readable summary and store list.
            if len(topics) <= 3:
                summary = f"{len(topics)} topics: " + ", ".join(topics)
            else:
                first_three = ", ".join(topics[:3])
                summary = f"{len(topics)} topics: {first_three}, ..."
            proposal_id = ar.create_proposal(
                topic=summary, depth=depth, rationale=rationale, topics=topics,
            )
        else:
            # Single-topic mode (unchanged).
            proposal_id = ar.create_proposal(
                topic=topic, depth=depth, rationale=rationale,
            )

        proposal = ar.get(proposal_id)
        await ctx.emit(ResearchProposalMessage(
            proposal_id=proposal_id,
            topic=proposal.topic,
            depth=depth,
            rationale=rationale,
            topics=topics if topics else None,
        ))
        # Block until the user signals a terminal status (or expiry).
        remaining = (proposal.expires_at - datetime.now(timezone.utc)).total_seconds()
        try:
            await asyncio.wait_for(proposal.event.wait(), timeout=max(0.1, remaining))
        except asyncio.TimeoutError:
            ar.expire_stale()

        final = ar.get(proposal_id)
        result = {"proposal_id": proposal_id, "status": final.status}
        if final.status == "declined":
            edited = ar.get_successor(proposal_id)
            if edited is not None:
                result = {
                    "proposal_id": edited.proposal_id,
                    "status": "approved",
                    "topic": edited.topic,
                    "depth": edited.depth,
                }
                if edited.topics:
                    result["topics"] = list(edited.topics)
        elif final.status == "approved":
            result["topic"] = final.topic
            result["depth"] = final.depth
            if final.topics:
                result["topics"] = list(final.topics)
        return json.dumps(result)


class ResearchTopic(Tool):
    name = "research_topic"
    description = (
        "Execute a research run previously approved via "
        "propose_research. Fetches URLs from SearxNG, summarizes "
        "them, and saves summaries under raw/summaries/. Requires "
        "a proposal_id from an approved (unused, unexpired) "
        "proposal. Returns a structured report."
    )
    parameters = {
        "type": "object",
        "properties": {
            "proposal_id": {
                "type": "string",
                "description": "proposal_id returned by propose_research.",
            },
        },
        "required": ["proposal_id"],
    }
    requires = ("approval_registry", "researcher", "config")

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        proposal_id = args.get("proposal_id", "").strip()
        if not proposal_id:
            return "Error: 'proposal_id' parameter is required."
        if ctx.agent.approval_registry is None or ctx.agent.researcher is None:
            return "Error: research execution is not available in this session."

        ar = ctx.agent.approval_registry
        proposal = ar.get(proposal_id)
        if proposal is None:
            return f"Error: unknown proposal_id: {proposal_id}"
        if proposal.status == "pending":
            return "Error: proposal is not approved yet."
        if proposal.status == "declined":
            return "Error: proposal was declined."
        if proposal.status == "expired":
            return "Error: proposal expired; ask the user to propose again."
        if proposal.status == "consumed":
            return "Error: proposal was already used. Each proposal is single-use."
        if proposal.status != "approved":
            return f"Error: proposal in unexpected state: {proposal.status}"

        # Consume first so even an exception during run() prevents reuse.
        ar.consume(proposal_id)

        try:
            report = await ctx.agent.researcher.research_topic(
                topic=proposal.topic,
                depth=proposal.depth,
            )
        except Exception as exc:
            return f"Research error: {exc}"

        return _format_research_report(report, ctx.agent.config.vault_path)
