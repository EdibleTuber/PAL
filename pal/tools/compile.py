"""PAL compile tools — promote raw summaries into wiki articles."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from agent_core.tools.base import Tool

if TYPE_CHECKING:
    from agent_core.agent import HandlerContext


class CompileSummary(Tool):
    name = "compile_summary"
    description = (
        "Promote a single raw summary into a grounded wiki "
        "article. Categorizes, merges with any existing article "
        "on the same topic, and archives the raw+summary on "
        "success. Use when the user wants one specific summary "
        "ingested. For batches, use propose_compile_batch."
    )
    parameters = {
        "type": "object",
        "properties": {
            "summary_path": {
                "type": "string",
                "description": (
                    "Vault-relative path under raw/summaries/ (e.g. 'raw/summaries/foo.md'). "
                    "Use ls raw/summaries/ to find the exact path; do not guess slug formats."
                ),
            },
        },
        "required": ["summary_path"],
    }
    requires = ("compiler",)

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        summary_path = args.get("summary_path", "").strip()
        if not summary_path:
            return "Error: 'summary_path' parameter is required."
        if ctx.agent.compiler is None:
            return "Error: compile is not available in this session."
        result = await ctx.agent.compiler.compile_one(summary_path)
        return json.dumps(result)


class ProposeCompileBatch(Tool):
    name = "propose_compile_batch"
    description = (
        "Propose compiling multiple raw summaries into wiki "
        "articles. Blocks until the user approves, declines, "
        "or edits via the approval prompt. Use for multi-summary promotion. "
        "After approval, immediately call compile_batch with "
        "the returned proposal_id."
    )
    parameters = {
        "type": "object",
        "properties": {
            "summary_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Relative paths under raw/summaries/ (non-empty).",
            },
            "rationale": {
                "type": "string",
                "description": "One-line reason shown to the user in the approval prompt.",
            },
        },
        "required": ["summary_paths", "rationale"],
    }
    requires = ("approval_registry",)

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        from pal.protocol import CompileProposalMessage

        if ctx.agent.approval_registry is None:
            return "Error: compile proposals are not available in this session."
        paths = args.get("summary_paths")
        if not isinstance(paths, list) or not paths:
            return "Error: 'summary_paths' must be a non-empty list."
        rationale = (args.get("rationale") or "").strip()
        if not rationale:
            return "Error: 'rationale' parameter is required."

        ar = ctx.agent.approval_registry
        proposal_id = ar.create_proposal(
            kind="compile",
            summary_paths=paths,
            rationale=rationale,
        )
        proposal = ar.get(proposal_id)
        await ctx.emit(
            CompileProposalMessage(
                proposal_id=proposal_id,
                summary_paths=list(paths),
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
                    "summary_paths": list(edited.summary_paths or []),
                }
        elif final.status == "approved":
            result["summary_paths"] = list(final.summary_paths or [])
        return json.dumps(result)


class CompileBatch(Tool):
    name = "compile_batch"
    description = (
        "Execute a compile batch previously approved via "
        "propose_compile_batch. Iterates the approved summary "
        "paths and compiles each. Partial failures do not "
        "abort the batch. Returns a structured report."
    )
    parameters = {
        "type": "object",
        "properties": {
            "proposal_id": {
                "type": "string",
                "description": "proposal_id returned by propose_compile_batch.",
            },
        },
        "required": ["proposal_id"],
    }
    requires = ("approval_registry", "compiler")

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        proposal_id = (args.get("proposal_id") or "").strip()
        if not proposal_id:
            return "Error: 'proposal_id' parameter is required."
        if ctx.agent.approval_registry is None or ctx.agent.compiler is None:
            return "Error: compile execution is not available in this session."

        ar = ctx.agent.approval_registry
        proposal = ar.get(proposal_id)
        if proposal is None:
            return f"Error: unknown proposal_id: {proposal_id}"
        if proposal.kind != "compile":
            return f"Error: proposal_id {proposal_id} is not a compile proposal."
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

        per_file = []
        ok = merged = insufficient = error_count = 0
        for path in (proposal.summary_paths or []):
            try:
                outcome = await ctx.agent.compiler.compile_one(path)
            except Exception as exc:
                outcome = {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
            entry = {"path": path, "status": outcome.get("status")}
            if "title" in outcome:
                entry["title"] = outcome["title"]
            if "article_path_rel" in outcome:
                entry["article_path"] = outcome["article_path_rel"]
            if "reason" in outcome:
                entry["reason"] = outcome["reason"]
            # Ground-truth echo: confirm the file actually exists on disk.
            article_rel = outcome.get("article_path_rel")
            if article_rel:
                entry["vault_exists"] = (ctx.agent.config.vault_path / article_rel).exists()
            per_file.append(entry)
            s = outcome.get("status")
            if s == "ok":
                ok += 1
            elif s == "merged":
                merged += 1
            elif s == "insufficient":
                insufficient += 1
            else:
                error_count += 1

        report = {
            "total": len(per_file),
            "ok": ok,
            "merged": merged,
            "insufficient": insufficient,
            "error_count": error_count,
            "per_file": per_file,
            "_note": (
                "Trust vault_exists for each entry. If vault_exists is false, "
                "the file is not on disk regardless of status."
            ),
        }
        return json.dumps(report)
