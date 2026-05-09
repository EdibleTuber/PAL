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


class UrlFix(Tool):
    """Execute an approved url_fix proposal: rewrite the article's first sources entry."""

    name = "url_fix"
    description = (
        "Execute an approved propose_url_fix proposal. Pass the proposal_id (from "
        "propose_url_fix's return) plus the approved article_path and proposed url/source_file. "
        "Rewrites the first sources entry in the target article to include the approved values."
    )
    requires = ("approval_registry",)

    parameters = {
        "type": "object",
        "properties": {
            "proposal_id": {
                "type": "string",
                "description": "The proposal_id returned by propose_url_fix when the proposal was approved.",
            },
            "article_path": {
                "type": "string",
                "description": "Path of the article to fix, relative to the vault.",
            },
            "proposed_url": {
                "type": "string",
                "description": "Approved URL to write into the first sources entry. Empty if not setting a URL.",
            },
            "proposed_source_file": {
                "type": "string",
                "description": "Approved source_file path to write into the first sources entry. Empty if not setting a path.",
            },
        },
        "required": ["proposal_id", "article_path"],
    }

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        proposal_id = args["proposal_id"]
        article_path_rel = args["article_path"]
        proposed_url = (args.get("proposed_url") or "").strip()
        proposed_source_file = (args.get("proposed_source_file") or "").strip()

        if not proposed_url and not proposed_source_file:
            return json.dumps({
                "status": "error",
                "message": "Must provide at least one of proposed_url or proposed_source_file.",
            })

        ar = ctx.agent.approval_registry
        proposal = ar.get(proposal_id)

        if proposal is None:
            return json.dumps({"status": "error", "message": f"Proposal not found: {proposal_id}"})

        if proposal.status == "consumed":
            return json.dumps({
                "status": "error",
                "message": f"Proposal {proposal_id} has already been consumed.",
            })

        if proposal.status != "approved":
            return json.dumps({
                "status": "error",
                "message": f"Proposal {proposal_id} is not approved (status={proposal.status}).",
            })

        vault_path = ctx.agent.config.vault_path
        full_path = vault_path / article_path_rel
        if not full_path.exists():
            return json.dumps({
                "status": "error",
                "message": f"Article not found at execute time: {article_path_rel}",
            })

        from agent_core.utils.frontmatter import parse_frontmatter, serialize_frontmatter

        meta, body = parse_frontmatter(full_path.read_text())
        sources = meta.get("sources", [])
        if not sources:
            return json.dumps({
                "status": "error",
                "message": f"Article {article_path_rel} has no sources array to fix.",
            })

        first = dict(sources[0])
        if proposed_url:
            first["url"] = proposed_url
        if proposed_source_file:
            first["source_file"] = proposed_source_file
        sources[0] = first
        meta["sources"] = sources

        full_path.write_text(serialize_frontmatter(meta, body))
        ar.consume(proposal_id)

        return json.dumps({
            "status": "fixed",
            "article_path": article_path_rel,
            "wrote_url": bool(proposed_url),
            "wrote_source_file": bool(proposed_source_file),
        })
