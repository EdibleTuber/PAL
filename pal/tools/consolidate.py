"""PAL consolidate tools — fuse existing wiki articles into a new article."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from agent_core.tools.base import Tool

if TYPE_CHECKING:
    from agent_core.agent import HandlerContext


class ProposeConsolidate(Tool):
    name = "propose_consolidate"
    description = (
        "Propose synthesizing 2+ existing wiki articles into a new article. "
        "Use when the user wants to merge or combine already-promoted articles "
        "(not raw summaries — use compile_batch for raw/summaries/). Blocks until "
        "the user approves, declines, or edits. After approval, immediately call "
        "consolidate(proposal_id). Source articles are NOT deleted by this tool; "
        "after consolidation completes, propose_reorg can archive them."
    )
    parameters = {
        "type": "object",
        "properties": {
            "source_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Existing article paths to fuse (at least two). Must not be in raw/ or system dirs.",
            },
            "target_path": {
                "type": "string",
                "description": "New article path (must not exist, must not start with raw/ or _).",
            },
            "target_title": {
                "type": "string",
                "description": "Frontmatter title for the new article.",
            },
            "rationale": {
                "type": "string",
                "description": "One-line reason shown to the user in the approval prompt.",
            },
        },
        "required": ["source_paths", "target_path", "target_title", "rationale"],
    }
    requires = ("approval_registry",)

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        from pal.protocol import ConsolidateProposalMessage

        if ctx.agent.approval_registry is None:
            return "Error: consolidate proposals are not available in this session."
        source_paths = args.get("source_paths")
        if not isinstance(source_paths, list) or len(source_paths) < 2:
            return "Error: 'source_paths' must be a list with at least two entries."
        target_path = (args.get("target_path") or "").strip()
        if not target_path:
            return "Error: 'target_path' parameter is required."
        target_title = (args.get("target_title") or "").strip()
        if not target_title:
            return "Error: 'target_title' parameter is required."
        rationale = (args.get("rationale") or "").strip()
        if not rationale:
            return "Error: 'rationale' parameter is required."

        ar = ctx.agent.approval_registry
        try:
            proposal_id = ar.create_proposal(
                kind="consolidate",
                summary_paths=source_paths,
                target_path=target_path,
                target_title=target_title,
                rationale=rationale,
            )
        except ValueError as exc:
            return f"Error: {exc}"

        proposal = ar.get(proposal_id)
        await ctx.emit(
            ConsolidateProposalMessage(
                proposal_id=proposal_id,
                source_paths=list(source_paths),
                target_path=target_path,
                target_title=target_title,
                rationale=rationale,
            )
        )

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
                    "source_paths": list(edited.summary_paths or []),
                    "target_path": edited.target_path or "",
                    "target_title": edited.target_title or "",
                }
        elif final.status == "approved":
            result["source_paths"] = list(final.summary_paths or [])
            result["target_path"] = final.target_path or ""
            result["target_title"] = final.target_title or ""
        return json.dumps(result)


class Consolidate(Tool):
    name = "consolidate"
    description = (
        "Execute a consolidate previously approved via propose_consolidate. "
        "Takes a proposal_id. Fails if not approved, already used, or expired. "
        "Returns a structured report including vault_exists for the target."
    )
    parameters = {
        "type": "object",
        "properties": {
            "proposal_id": {
                "type": "string",
                "description": "proposal_id returned by propose_consolidate.",
            },
        },
        "required": ["proposal_id"],
    }
    requires = ("approval_registry", "consolidator")

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        proposal_id = (args.get("proposal_id") or "").strip()
        if not proposal_id:
            return "Error: 'proposal_id' parameter is required."
        if ctx.agent.approval_registry is None or ctx.agent.consolidator is None:
            return "Error: consolidate execution is not available in this session."

        ar = ctx.agent.approval_registry
        proposal = ar.get(proposal_id)
        if proposal is None:
            return f"Error: unknown proposal_id: {proposal_id}"
        if proposal.kind != "consolidate":
            return f"Error: proposal_id {proposal_id} is not a consolidate proposal."
        if proposal.status == "pending":
            return "Error: proposal is not approved yet."
        if proposal.status == "declined":
            return "Error: proposal was declined."
        if proposal.status == "expired":
            return "Error: proposal expired; propose again."
        if proposal.status == "consumed":
            return "Error: proposal was already used. Each proposal is single-use."
        if proposal.status != "approved":
            return f"Error: proposal in unexpected state: {proposal.status}"

        # Consume first — single-use even on failure.
        ar.consume(proposal_id)

        try:
            outcome = await ctx.agent.consolidator.consolidate(
                source_paths=list(proposal.summary_paths or []),
                target_path=proposal.target_path or "",
                target_title=proposal.target_title or "",
            )
        except Exception as exc:
            outcome = {
                "status": "error",
                "target_path": proposal.target_path or "",
                "reason": f"{type(exc).__name__}: {exc}",
                "vault_exists": False,
            }

        outcome["_note"] = (
            "Trust vault_exists: if false, the target file was not written to disk."
        )
        return json.dumps(outcome)
