"""PAL wait tool — poll reindex jobs until complete."""
from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from agent_core.tools.base import Tool

if TYPE_CHECKING:
    from agent_core.agent import HandlerContext


class WaitForReindex(Tool):
    name = "wait_for_reindex"
    description = (
        "Poll a reindex job until it finishes or times out. Use after "
        "compile/consolidate/reorg/edit/create when you need to be sure "
        "the new content is searchable via search_vault before answering. "
        "Most of the time you do NOT need this -- the reindex runs "
        "automatically and finishes within a second or two. Call this "
        "only when latency to-searchable matters for the next answer."
    )
    parameters = {
        "type": "object",
        "properties": {
            "job_id": {
                "type": "string",
                "description": "job_id from a prior tool result's reindex field.",
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Max seconds to wait. Default 30.",
            },
        },
        "required": ["job_id"],
    }
    requires = ("retrieval",)

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        job_id = (args.get("job_id") or "").strip()
        if not job_id:
            return "Error: 'job_id' parameter is required."
        if ctx.agent.retrieval is None:
            return "Error: retrieval client is not configured."

        timeout_seconds = int(args.get("timeout_seconds") or 30)
        timeout_seconds = max(1, min(timeout_seconds, 120))

        deadline = asyncio.get_event_loop().time() + timeout_seconds
        last_status = "unknown"
        while True:
            job = await ctx.agent.retrieval.get_reindex_job(job_id)
            if job is None:
                return f"Error: unknown job_id (not found): {job_id}"
            last_status = job.get("status", "unknown")
            if last_status in ("done", "error"):
                return json.dumps(job)
            if asyncio.get_event_loop().time() >= deadline:
                return json.dumps({
                    "job_id": job_id,
                    "status": "timeout",
                    "last_seen_status": last_status,
                    "_note": (
                        "Job did not finish within timeout. The job may still complete; "
                        "poll again with a longer timeout if needed."
                    ),
                })
            await asyncio.sleep(0.25)
