"""PAL-specific overrides for update_scratch and add_learning.

These shadow the framework builtins of the same names because PAL's
behaviour differs in two ways:

  UpdateScratch: accepts "content" (not "text") to preserve the LLM
    prompt contract already established with users; also uses a
    Scratchpad instance constructed with PAL's commit_callback so every
    write triggers a git commit via WikiManager.

  AddLearning: calls wiki.git_commit() after writing the learning file,
    keeping vault history consistent with PAL's existing behaviour.

Both classes are registered on PALAgent.tools, which causes the
framework executor to shadow the builtin instances of the same name
(BUILTIN_TOOLS + agent_tool_classes; later name wins - see
agent_core.tools.executor.ToolExecutor.build).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agent_core.scratchpad import ScratchpadTooLarge
from agent_core.tools.base import Tool

if TYPE_CHECKING:
    from agent_core.agent import HandlerContext

logger = logging.getLogger(__name__)


class UpdateScratch(Tool):
    """Replace the scratchpad for the current channel (PAL override).

    Uses 'content' parameter (matching the schema already sent to the LLM)
    and constructs the Scratchpad with PAL's git-commit callback so every
    write is recorded in vault history.
    """

    name = "update_scratch"
    description = (
        "Replace the scratchpad contents for the current channel. "
        "Use this to record short-term project state, current decisions, "
        "or context you want to remember on the next turn. The scratchpad "
        "is automatically included in your system prompt on every turn in "
        "this channel. Content must be 2048 bytes or less. Calling this "
        "REPLACES the scratchpad wholesale -- prior content is discarded "
        "unless you include it in the new content."
    )
    parameters = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": (
                    "New scratchpad content. Markdown is fine. Keep it "
                    "terse -- it's working state, not a wiki article."
                ),
            },
        },
        "required": ["content"],
    }
    requires = ("config", "wiki")

    async def run(self, args, ctx: "HandlerContext") -> str:
        content = args.get("content", "")
        sp = ctx.agent._build_scratchpad(ctx.channel_id)
        try:
            sp.write(content)
        except ScratchpadTooLarge as exc:
            return (
                f"Error: scratchpad too large. Proposed {exc.proposed_bytes} bytes, "
                f"cap is {exc.max_bytes}. Prune or summarize and retry."
            )
        except Exception as exc:
            return f"Scratchpad error: {exc}"
        return f"Scratchpad updated ({len(content)} bytes)."


class AddLearning(Tool):
    """Capture a durable lesson as a learning candidate (PAL override).

    After writing, calls wiki.git_commit() to record the new learning
    file in vault history — matching PAL's pre-migration behaviour.
    """

    name = "add_learning"
    description = (
        "Save a durable lesson extracted from conversation into the "
        "learning pool. Learnings stay as candidates until the user "
        "promotes them to wisdom via /promote. Use when the user says "
        "'make a learning out of that' or when you detect a correction "
        "you want to remember across sessions."
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short, specific title of the lesson.",
            },
            "body": {
                "type": "string",
                "description": "The lesson itself, in 1-4 sentences.",
            },
        },
        "required": ["title", "body"],
    }
    requires = ("learning", "wiki")

    async def run(self, args, ctx: "HandlerContext") -> str:
        import json
        title = (args.get("title") or "").strip()
        body = (args.get("body") or "").strip()
        if not title:
            return json.dumps({"error": "title is required"})
        if not body:
            return json.dumps({"error": "body is required"})
        try:
            slug = ctx.agent.learning.add(title=title, body=body, source="conversation")
        except Exception as exc:
            return json.dumps({"error": str(exc)})
        ctx.agent.wiki.git_commit(f"learn: add {slug}")
        return json.dumps({"slug": slug, "title": title})
