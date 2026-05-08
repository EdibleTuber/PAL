"""Vault tools for chat — read and write access to wiki content.

Defines tool schemas (OpenAI function-calling format) and a ToolExecutor
that runs tool calls against the vault.

Phase F migration status:
  PR2: read_file, list_directory, search_content, edit_file, create_file,
       move_file — migrated to pal.tools.vault Tool subclasses.
  PR3: search_web, propose_research, research_topic — migrated to
       pal.tools.research Tool subclasses.
  PR4: compile_summary, propose_compile_batch, compile_batch,
       propose_reorg, propose_promote, reorg, propose_consolidate,
       consolidate, wait_for_reindex — migrated to pal.tools.compile,
       pal.tools.consolidate, pal.tools.reorg, pal.tools.wait.
  Remaining: add_learning, update_scratch (PR5/PR7).
"""
import logging
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

from agent_core.retrieval import RetrievalClient
from pal.wiki import WikiManager
from agent_core.learning import LearningManager
from agent_core.wisdom import WisdomManager
from agent_core.scratchpad import ScratchpadTooLarge

if TYPE_CHECKING:
    from agent_core.approval_registry import ApprovalRegistry
    from agent_core.websearch import WebSearchClient
    from pal.researcher import Researcher
    from pal.compiler import Compiler
    from pal.reorg import Reorganizer
    from pal.consolidator import Consolidator

TOOL_DEFINITIONS = [
    # Note: read_file, list_directory, search_content, search_vault,
    # edit_file, create_file, move_file removed — migrated to
    # pal.tools.vault Tool subclasses (Phase F PR2).
    # search_web, propose_research, research_topic removed — migrated to
    # pal.tools.research Tool subclasses (Phase F PR3). search_web is also
    # served by agent_core.tools.SearchWeb builtin.
    # compile_summary, propose_compile_batch, compile_batch, propose_reorg,
    # propose_promote, reorg, propose_consolidate, consolidate,
    # wait_for_reindex removed — migrated to pal.tools.compile,
    # pal.tools.consolidate, pal.tools.reorg, pal.tools.wait (Phase F PR4).
    {
        "type": "function",
        "function": {
            "name": "add_learning",
            "description": (
                "Save a durable lesson extracted from conversation into the "
                "learning pool. Learnings stay as candidates until the user "
                "promotes them to wisdom via /promote. Use when the user says "
                "'make a learning out of that' or when you detect a correction "
                "you want to remember across sessions."
            ),
            "parameters": {
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
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_scratch",
            "description": (
                "Replace the scratchpad contents for the current channel. "
                "Use this to record short-term project state, current decisions, "
                "or context you want to remember on the next turn. The scratchpad "
                "is automatically included in your system prompt on every turn in "
                "this channel. Content must be 2048 bytes or less. Calling this "
                "REPLACES the scratchpad wholesale -- prior content is discarded "
                "unless you include it in the new content."
            ),
            "parameters": {
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
            },
        },
    },
]


class ToolExecutor:
    """Executes tool calls against the vault.

    After Phase F PR4, this class only handles add_learning and update_scratch.
    The 9 wiki-shaping tools (compile, consolidate, reorg, promote, wait) have
    been migrated to Tool subclasses in pal.tools. The vault tools (PR2) and
    research tools (PR3) were migrated in prior PRs.

    PR5/PR7 will migrate add_learning and update_scratch, after which this
    class can be deleted.
    """

    def __init__(
        self,
        vault_path: Path,
        retrieval: RetrievalClient | None,
        wiki: WikiManager | None = None,
        approval_registry: "ApprovalRegistry | None" = None,
        websearch: "WebSearchClient | None" = None,
        researcher: "Researcher | None" = None,
        proposal_emitter=None,
        compiler: "Compiler | None" = None,
        reorganizer: "Reorganizer | None" = None,
        consolidator: "Consolidator | None" = None,
        learning: "LearningManager | None" = None,
        wisdom: "WisdomManager | None" = None,
        scratchpad=None,
    ) -> None:
        self.vault_path = vault_path.resolve()
        self.retrieval = retrieval
        self.wiki = wiki
        self.approval_registry = approval_registry
        self.websearch = websearch
        self.researcher = researcher
        # proposal_emitter kept in constructor signature for backward compat
        # during transition; no longer used now that all propose_* tools are
        # migrated to Tool subclasses.
        self.proposal_emitter = proposal_emitter
        self.compiler = compiler
        self.reorganizer = reorganizer
        self.consolidator = consolidator
        self.learning = learning
        self.wisdom = wisdom
        self.scratchpad = scratchpad

    def run(self, name: str, arguments: dict) -> str:
        """Dispatch a tool call and return the result as a string.

        Always returns a string — errors are returned as descriptive messages,
        never raised, so the LLM can see what went wrong and adjust.

        Note: all vault, research, compile, consolidate, reorg, and wait tools
        have been migrated to pal.tools Tool subclasses (Phase F PR2-PR4) and
        are no longer dispatched here.
        """
        handler = {
            "add_learning": self._add_learning,
        }.get(name)
        if handler is not None:
            return handler(arguments)
        return f"Unknown tool: {name}"

    async def run_async(self, name: str, arguments: dict) -> str:
        """Dispatch a tool call, supporting async tools.

        Note: all vault, research, compile, consolidate, reorg, and wait tools
        have been migrated to pal.tools Tool subclasses (Phase F PR2-PR4) and
        are no longer dispatched here.
        """
        if name == "update_scratch":
            return await self._update_scratch(arguments)
        return self.run(name, arguments)

    def _add_learning(self, arguments: dict) -> str:
        import json
        if self.learning is None:
            return json.dumps({"error": "learning manager not available"})
        title = (arguments.get("title") or "").strip()
        body = (arguments.get("body") or "").strip()
        if not title:
            return json.dumps({"error": "title is required"})
        if not body:
            return json.dumps({"error": "body is required"})
        slug = self.learning.add(title=title, body=body, source="conversation")
        if self.wiki is not None:
            self.wiki.git_commit(f"learn: add {slug}")
        return json.dumps({"slug": slug, "title": title})

    async def _update_scratch(self, arguments: dict) -> str:
        content = arguments.get("content", "")
        if self.scratchpad is None:
            return "Error: scratchpad not configured for this session."
        try:
            self.scratchpad.write(content)
        except ScratchpadTooLarge as exc:
            return (
                f"Error: scratchpad too large. Proposed {exc.proposed_bytes} bytes, "
                f"cap is {exc.max_bytes}. Prune or summarize and retry."
            )
        return f"Scratchpad updated ({len(content)} bytes)."
