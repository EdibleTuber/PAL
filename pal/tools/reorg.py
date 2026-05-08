"""PAL reorg and promote tools — vault reorganization and learning promotion."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from agent_core.tools.base import Tool

if TYPE_CHECKING:
    from agent_core.agent import HandlerContext


class ProposeReorg(Tool):
    name = "propose_reorg"
    description = (
        "Propose a batch of vault reorganization operations. "
        "Supported op types: 'move' renames src to dst (dst must "
        "not exist); 'merge' combines src into an existing dst and "
        "removes src after success. Use 'merge' to consolidate "
        "duplicate articles and for cleanup after content has been "
        "folded into a canonical article. There is no separate "
        "delete op; merge into the target article is the only way "
        "to remove a source file. Prefer batches of 3-5 operations "
        "so the approval prompt stays scannable. Use exact filenames "
        "from list_directory output, including any unicode characters. "
        "Do not paraphrase or approximate filename text. Blocks until "
        "the user approves, declines, or edits. After approval, call "
        "reorg(proposal_id) to execute."
    )
    parameters = {
        "type": "object",
        "properties": {
            "operations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["move", "merge"],
                        },
                        "src": {"type": "string"},
                        "dst": {"type": "string"},
                    },
                    "required": ["type", "src", "dst"],
                },
                "description": "List of reorg operations (non-empty).",
            },
            "rationale": {
                "type": "string",
                "description": "One-line reason shown to the user.",
            },
        },
        "required": ["operations", "rationale"],
    }
    requires = ("approval_registry", "reorganizer")

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        from pal.protocol import ReorgProposalMessage

        if (ctx.agent.approval_registry is None
                or ctx.agent.reorganizer is None):
            return "Error: reorg proposals are not available in this session."
        operations = args.get("operations")
        if not isinstance(operations, list) or not operations:
            return "Error: 'operations' must be a non-empty list."
        rationale = (args.get("rationale") or "").strip()
        if not rationale:
            return "Error: 'rationale' parameter is required."

        # Pre-validate to surface errors before prompting the user
        try:
            validation_errors = ctx.agent.reorganizer.validate_operations(operations)
        except Exception as exc:
            return f"Error: operation validation failed: {exc}"
        if validation_errors:
            return "Error: invalid operations:\n" + "\n".join(validation_errors)

        # Reference-count preview
        src_paths = [op["src"] for op in operations if "src" in op]
        try:
            references_preview = ctx.agent.reorganizer.count_references(src_paths)
        except Exception:
            references_preview = 0

        ar = ctx.agent.approval_registry
        try:
            proposal_id = ar.create_proposal(
                kind="reorg",
                operations=operations,
                rationale=rationale,
            )
        except ValueError as exc:
            return f"Error: {exc}"

        proposal = ar.get(proposal_id)
        await ctx.emit(
            ReorgProposalMessage(
                proposal_id=proposal_id,
                operations=[dict(op) for op in operations],
                rationale=rationale,
                references_preview=references_preview,
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
                    "operations": list(edited.operations or []),
                }
        elif final.status == "approved":
            result["operations"] = list(final.operations or [])
        return json.dumps(result)


class ProposePromote(Tool):
    name = "propose_promote"
    description = (
        "Propose promoting an existing learning (in _learning/) to "
        "wisdom (_wisdom/). Wisdom is injected into every future system "
        "prompt and should be treated as durable guidance. Requires "
        "user approval. Call with the learning slug (from list_learnings "
        "or the return of add_learning) and a brief rationale."
    )
    parameters = {
        "type": "object",
        "properties": {
            "slug": {
                "type": "string",
                "description": "Slug of the learning to promote.",
            },
            "rationale": {
                "type": "string",
                "description": "One-line reason shown in the approval prompt.",
            },
        },
        "required": ["slug", "rationale"],
    }
    requires = ("approval_registry", "learning", "wisdom")

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        from pal.protocol import PromoteProposalMessage

        if (ctx.agent.approval_registry is None
                or ctx.agent.learning is None or ctx.agent.wisdom is None):
            return "Error: promote proposals are not available in this session."

        slug = (args.get("slug") or "").strip()
        rationale = (args.get("rationale") or "").strip()
        if not slug:
            return "Error: 'slug' parameter is required."
        if not rationale:
            return "Error: 'rationale' parameter is required."

        if not ctx.agent.learning.exists(slug):
            return json.dumps({"error": f"no such learning: {slug}"})
        meta = ctx.agent.learning.get_meta(slug)
        if meta.get("status") == "promoted":
            return json.dumps({
                "error": f"already promoted at {meta.get('promoted_at', 'unknown')}"
            })

        title = meta.get("title", slug)
        body = ctx.agent.learning.get(slug)

        ar = ctx.agent.approval_registry
        try:
            proposal_id = ar.create_proposal(
                kind="promote",
                rationale=rationale,
                slug=slug,
                target_title=title,
                body=body,
            )
        except ValueError as exc:
            return f"Error: {exc}"

        proposal = ar.get(proposal_id)
        await ctx.emit(
            PromoteProposalMessage(
                proposal_id=proposal_id,
                slug=slug,
                title=title,
                body=body,
                rationale=rationale,
            )
        )

        remaining = (proposal.expires_at - datetime.now(timezone.utc)).total_seconds()
        try:
            await asyncio.wait_for(proposal.event.wait(), timeout=max(0.1, remaining))
        except asyncio.TimeoutError:
            ar.expire_stale()

        final = ar.get(proposal_id)

        if final.status != "approved":
            return json.dumps({"status": final.status, "slug": slug})

        # Execute promotion.
        ar.consume(proposal_id)
        ctx.agent.learning.mark_promoted(slug)
        ctx.agent.wisdom.add(title=title, body=body)
        if ctx.agent.wiki is not None:
            ctx.agent.wiki.git_commit(f"promote: {slug} -> wisdom")
        return json.dumps({"status": "promoted", "slug": slug, "title": title})


class Reorg(Tool):
    name = "reorg"
    description = (
        "Execute a reorg batch previously approved via "
        "propose_reorg. Pre-validates the operations against "
        "current vault state before any mutation. Partial "
        "failures don't abort the batch. Returns a structured "
        "report."
    )
    parameters = {
        "type": "object",
        "properties": {
            "proposal_id": {
                "type": "string",
                "description": "proposal_id returned by propose_reorg.",
            },
        },
        "required": ["proposal_id"],
    }
    requires = ("approval_registry", "reorganizer")

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        proposal_id = (args.get("proposal_id") or "").strip()
        if not proposal_id:
            return "Error: 'proposal_id' parameter is required."
        if ctx.agent.approval_registry is None or ctx.agent.reorganizer is None:
            return "Error: reorg execution is not available in this session."

        ar = ctx.agent.approval_registry
        proposal = ar.get(proposal_id)
        if proposal is None:
            return f"Error: unknown proposal_id: {proposal_id}"
        if proposal.kind != "reorg":
            return f"Error: proposal_id {proposal_id} is not a reorg proposal."
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

        # Consume first - single-use invariant
        ar.consume(proposal_id)

        ops = list(proposal.operations or [])

        # Re-validate against current vault state. State may have changed
        # between proposal and execute.
        validation_errors = ctx.agent.reorganizer.validate_operations(ops)
        if validation_errors:
            return "Error: invalid operations:\n" + "\n".join(validation_errors)

        try:
            per_op = await ctx.agent.reorganizer.execute_operations_async(ops)
        except Exception as exc:
            return f"Error: reorg execution failed: {exc}"

        # Ground-truth echo: check on-disk state for every op.
        for r in per_op:
            src = r.get("src", "")
            dst = r.get("dst", "")
            if src:
                r["src_exists_after"] = (ctx.agent.config.vault_path / src).exists()
            if dst:
                r["dst_exists_after"] = (ctx.agent.config.vault_path / dst).exists()

        ok = sum(1 for r in per_op if r.get("status") == "ok")
        failed = sum(1 for r in per_op if r.get("status") not in ("ok",))
        refs = sum(int(r.get("references_rewritten", 0)) for r in per_op)

        report = {
            "total": len(per_op),
            "ok": ok,
            "failed": failed,
            "references_rewritten": refs,
            "per_op": per_op,
            "_note": (
                "Trust src_exists_after and dst_exists_after for each op. "
                "A successful move or merge must show src_exists_after=false "
                "and dst_exists_after=true."
            ),
        }
        # Promote per-op _reindex (if any) to top-level
        for r in per_op:
            if "_reindex" in r:
                report["reindex"] = r.pop("_reindex")
                break
        return json.dumps(report)
