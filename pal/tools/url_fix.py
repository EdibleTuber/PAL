"""URL-fix tool pair for backfilling empty-source articles."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from agent_core.tools.base import Tool

if TYPE_CHECKING:
    from agent_core.agent import HandlerContext


class ProposeUrlFix(Tool):
    """Propose a URL or source_file fix for an article missing both."""

    name = "propose_url_fix"
    description = (
        "Propose filling a missing source URL or source_file path for a vault article "
        "whose sources entries are all empty. Blocks until the user approves, edits, or declines. "
        "Returns a proposal_id for use with url_fix."
    )
    parameters = {
        "type": "object",
        "properties": {
            "article_path": {
                "type": "string",
                "description": "Vault-relative path to the article with empty sources (e.g. Hardware/arm-architecture.md).",
            },
            "proposed_url": {
                "type": "string",
                "description": "A URL to fill in as the source. Leave empty if providing proposed_source_file instead.",
            },
            "proposed_source_file": {
                "type": "string",
                "description": "A vault-relative path to a raw source file. Leave empty if providing proposed_url instead.",
            },
            "rationale": {
                "type": "string",
                "description": "One-line reason shown to the user in the approval prompt.",
            },
        },
        "required": ["article_path", "rationale"],
    }
    requires = ("approval_registry",)

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        from pal.protocol import UrlFixProposalMessage

        if ctx.agent.approval_registry is None:
            return json.dumps({"status": "error", "message": "url_fix proposals are not available in this session."})

        article_path = (args.get("article_path") or "").strip()
        if not article_path:
            return json.dumps({"status": "error", "message": "article_path parameter is required."})

        proposed_url = (args.get("proposed_url") or "").strip()
        proposed_source_file = (args.get("proposed_source_file") or "").strip()

        if not proposed_url and not proposed_source_file:
            return json.dumps({
                "status": "error",
                "message": "Must provide at least one of proposed_url or proposed_source_file.",
            })

        rationale = (args.get("rationale") or "").strip()

        vault_path = ctx.agent.config.vault_path
        full_path = vault_path / article_path
        if not full_path.exists():
            return json.dumps({
                "status": "error",
                "message": f"Article not found: {article_path}",
            })

        ar = ctx.agent.approval_registry
        proposal_id = ar.create_proposal(
            kind="url_fix",
            rationale=rationale,
        )

        proposal = ar.get(proposal_id)
        await ctx.emit(
            UrlFixProposalMessage(
                proposal_id=proposal_id,
                article_path=article_path,
                proposed_url=proposed_url,
                proposed_source_file=proposed_source_file,
                rationale=rationale,
            )
        )

        remaining = (proposal.expires_at - datetime.now(timezone.utc)).total_seconds()
        try:
            await asyncio.wait_for(proposal.event.wait(), timeout=max(0.1, remaining))
        except asyncio.TimeoutError:
            ar.expire_stale()

        final = ar.get(proposal_id)
        result: dict = {"proposal_id": proposal_id, "status": final.status}
        if final.status == "approved":
            result["article_path"] = article_path
            result["proposed_url"] = proposed_url
            result["proposed_source_file"] = proposed_source_file
        return json.dumps(result)
