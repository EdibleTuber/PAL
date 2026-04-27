"""Vault tools for chat — read and write access to wiki content.

Defines tool schemas (OpenAI function-calling format) and a ToolExecutor
that runs tool calls against the vault.
"""
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

from agent_core.retrieval import RetrievalClient
from pal.wiki import WikiManager
from pal.learning import LearningManager
from pal.wisdom import WisdomManager
from pal.scratchpad import ScratchpadTooLarge

if TYPE_CHECKING:
    from pal.approval_registry import ApprovalRegistry
    from pal.websearch import WebSearchClient
    from pal.researcher import Researcher
    from pal.compiler import Compiler
    from pal.reorg import Reorganizer
    from pal.consolidator import Consolidator

# Maximum characters to return from a file read (~8000 tokens ≈ 32000 chars).
_READ_LIMIT = 32_000

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the vault. Returns frontmatter and body.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to vault root (e.g. 'Research/quantum.md')",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": (
                "List files and subdirectories in a vault directory. Paginated: by default "
                "returns up to 50 entries with a footer indicating the total and how to "
                "continue. Use prefix to filter when reorganizing large directories."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path relative to vault root (e.g. 'Research'). Empty or omitted for root.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max entries to return (default 50, cap 500).",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Skip this many entries before returning (for paging).",
                    },
                    "prefix": {
                        "type": "string",
                        "description": "Only return entries whose filename starts with this string (e.g. 'agent-').",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_content",
            "description": "Keyword search across vault files. Returns matching filenames with line snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term or phrase to find in vault files.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_vault",
            "description": "Semantic search across the vault using natural language. Returns ranked results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language query (e.g. 'articles about machine learning').",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Rewrite the body of an existing vault file. Preserves frontmatter (title, tags). Use for restructuring, reformatting, or updating content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to vault root (e.g. 'Research/quantum.md'). Must already exist.",
                    },
                    "content": {
                        "type": "string",
                        "description": "New body content for the file (markdown, without frontmatter).",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": (
                "Create a new scratch note under raw/notes/. Scoped to raw/ to "
                "preserve the promotion discipline: wiki articles are produced by "
                "compile_summary, compile_batch, or consolidate, never by create_file. "
                "If the user asks you to write a new wiki article in a promoted "
                "category (e.g. Security/, Reverse-Engineering/, Research/), say you "
                "cannot do that directly and propose the right tool (research + "
                "compile, or consolidate if merging existing articles)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path relative to vault root. Must begin with 'raw/' (e.g. 'raw/notes/my-note.md'). Must not already exist.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Article title for frontmatter.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Body content for the file (markdown, without frontmatter).",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional tags for frontmatter.",
                    },
                },
                "required": ["path", "title", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "Query the public web via SearxNG and return titles, URLs, "
                "and snippets. Read-only. No fetching, no file writes. Use "
                "to triage whether a topic has material online before "
                "proposing a full research run."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search terms.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum results to return (1-10, default 5).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_research",
            "description": (
                "Propose a web research run. Emits a proposal to the user "
                "and blocks until they approve, decline, or edit it in the "
                "CLI. Returns a JSON object with the final status and "
                "proposal_id. Use research_topic to execute an approved "
                "proposal."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Topic string to research.",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Number of sources to fetch (1-10, default 3).",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "One-line reason shown to the user.",
                    },
                },
                "required": ["topic", "rationale"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research_topic",
            "description": (
                "Execute a research run previously approved via "
                "propose_research. Fetches URLs from SearxNG, summarizes "
                "them, and saves summaries under raw/summaries/. Requires "
                "a proposal_id from an approved (unused, unexpired) "
                "proposal. Returns a structured report."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "proposal_id": {
                        "type": "string",
                        "description": "proposal_id returned by propose_research.",
                    },
                },
                "required": ["proposal_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compile_summary",
            "description": (
                "Promote a single raw summary into a grounded wiki "
                "article. Categorizes, merges with any existing article "
                "on the same topic, and archives the raw+summary on "
                "success. Use when the user wants one specific summary "
                "ingested. For batches, use propose_compile_batch."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary_path": {
                        "type": "string",
                        "description": "Relative path under raw/summaries/ (e.g. 'raw/summaries/foo.md').",
                    },
                },
                "required": ["summary_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_compile_batch",
            "description": (
                "Propose compiling multiple raw summaries into wiki "
                "articles. Blocks until the user approves, declines, "
                "or edits in the CLI. Use for multi-summary promotion. "
                "After approval, immediately call compile_batch with "
                "the returned proposal_id."
            ),
            "parameters": {
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
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compile_batch",
            "description": (
                "Execute a compile batch previously approved via "
                "propose_compile_batch. Iterates the approved summary "
                "paths and compiles each. Partial failures do not "
                "abort the batch. Returns a structured report."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "proposal_id": {
                        "type": "string",
                        "description": "proposal_id returned by propose_compile_batch.",
                    },
                },
                "required": ["proposal_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reorg",
            "description": (
                "Execute a reorg batch previously approved via "
                "propose_reorg. Pre-validates the operations against "
                "current vault state before any mutation. Partial "
                "failures don't abort the batch. Returns a structured "
                "report."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "proposal_id": {
                        "type": "string",
                        "description": "proposal_id returned by propose_reorg.",
                    },
                },
                "required": ["proposal_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_reorg",
            "description": (
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
            ),
            "parameters": {
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
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_consolidate",
            "description": (
                "Propose synthesizing 2+ existing wiki articles into a new article. "
                "Use when the user wants to merge or combine already-promoted articles "
                "(not raw summaries — use compile_batch for raw/summaries/). Blocks until "
                "the user approves, declines, or edits. After approval, immediately call "
                "consolidate(proposal_id). Source articles are NOT deleted by this tool; "
                "after consolidation completes, propose_reorg can archive them."
            ),
            "parameters": {
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
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consolidate",
            "description": (
                "Execute a consolidate previously approved via propose_consolidate. "
                "Takes a proposal_id. Fails if not approved, already used, or expired. "
                "Returns a structured report including vault_exists for the target."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "proposal_id": {
                        "type": "string",
                        "description": "proposal_id returned by propose_consolidate.",
                    },
                },
                "required": ["proposal_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait_for_reindex",
            "description": (
                "Poll a reindex job until it finishes or times out. Use after "
                "compile/consolidate/reorg/edit/create when you need to be sure "
                "the new content is searchable via search_vault before answering. "
                "Most of the time you do NOT need this -- the reindex runs "
                "automatically and finishes within a second or two. Call this "
                "only when latency to-searchable matters for the next answer."
            ),
            "parameters": {
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
            },
        },
    },
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
            "name": "move_file",
            "description": (
                "Move a single vault article from src to dst. Use for quick "
                "re-categorization (for example, moving a mis-categorized "
                "article from Security/ to IoT/). For batch moves or merges, "
                "use propose_reorg instead. Triggers reindex. Rejects paths "
                "inside raw/ or underscore-prefixed system directories."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "src": {
                        "type": "string",
                        "description": "Current path (relative to vault root).",
                    },
                    "dst": {
                        "type": "string",
                        "description": "Destination path (relative to vault root). Must not exist.",
                    },
                },
                "required": ["src", "dst"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_promote",
            "description": (
                "Propose promoting an existing learning (in _learning/) to "
                "wisdom (_wisdom/). Wisdom is injected into every future system "
                "prompt and should be treated as durable guidance. Requires "
                "user approval. Call with the learning slug (from list_learnings "
                "or the return of add_learning) and a brief rationale."
            ),
            "parameters": {
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
    """Executes tool calls against the vault."""

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
        """
        handler = {
            "read_file": self._read_file,
            "list_directory": self._list_directory,
            "search_content": self._search_content,
            "edit_file": self._edit_file,
            "create_file": self._create_file,
            "add_learning": self._add_learning,
        }.get(name)
        if handler is not None:
            return handler(arguments)
        if name == "search_vault":
            return "Error: search_vault must be called via run_async()"
        return f"Unknown tool: {name}"

    async def run_async(self, name: str, arguments: dict) -> str:
        """Dispatch a tool call, supporting async tools like search_vault."""
        if name == "search_vault":
            return await self._search_vault(arguments)
        if name == "search_web":
            return await self._search_web(arguments)
        if name == "propose_research":
            return await self._propose_research(arguments)
        if name == "research_topic":
            return await self._research_topic(arguments)
        if name == "compile_summary":
            return await self._compile_summary(arguments)
        if name == "propose_compile_batch":
            return await self._propose_compile_batch(arguments)
        if name == "compile_batch":
            return await self._compile_batch(arguments)
        if name == "propose_reorg":
            return await self._propose_reorg(arguments)
        if name == "propose_promote":
            return await self._propose_promote(arguments)
        if name == "reorg":
            return await self._reorg(arguments)
        if name == "propose_consolidate":
            return await self._propose_consolidate(arguments)
        if name == "consolidate":
            return await self._consolidate(arguments)
        if name == "wait_for_reindex":
            return await self._wait_for_reindex(arguments)
        if name == "move_file":
            return await self._move_file(arguments)
        if name == "update_scratch":
            return await self._update_scratch(arguments)
        # Sync tools — fall through to self.run, then trigger reindex
        # for tools that write files.
        if name in ("edit_file", "create_file"):
            result = self.run(name, arguments)
            if self.retrieval is not None and "error" not in result.lower()[:30]:
                path = (arguments.get("path") or "").strip()
                if path:
                    absolute = str((self.vault_path / path).resolve())
                    await self.retrieval.trigger_reindex(paths=[absolute])
            return result
        return self.run(name, arguments)

    def _resolve_safe(self, path: str) -> Path | None:
        """Resolve a path within the vault. Returns None if it escapes."""
        full = (self.vault_path / path).resolve()
        if not full.is_relative_to(self.vault_path):
            return None
        return full

    def _is_system_path(self, path: str) -> bool:
        """Check if a path targets a system directory (_-prefixed)."""
        return any(part.startswith("_") for part in Path(path).parts)

    def _read_file(self, arguments: dict) -> str:
        path = arguments.get("path", "")
        if not path:
            return "Error: 'path' parameter is required."
        resolved = self._resolve_safe(path)
        if resolved is None:
            return f"Error: path escapes outside vault: {path}"
        if not resolved.exists():
            return f"File not found: {path}"
        if not resolved.is_file():
            return f"Not a file: {path} (use list_directory for directories)"
        content = resolved.read_text(errors="replace")
        if len(content) > _READ_LIMIT:
            content = content[:_READ_LIMIT] + f"\n\n[Truncated — file exceeds {_READ_LIMIT} characters]"
        return content

    def _list_directory(self, arguments: dict) -> str:
        path = arguments.get("path", "")
        target = self._resolve_safe(path) if path else self.vault_path
        if target is None:
            return f"Error: path escapes outside vault: {path}"
        if not target.exists():
            return f"Directory not found: {path}"
        if not target.is_dir():
            return f"Not a directory: {path} (use read_file for files)"

        prefix = (arguments.get("prefix") or "").strip()
        try:
            offset = max(0, int(arguments.get("offset") or 0))
        except (TypeError, ValueError):
            offset = 0
        try:
            limit = int(arguments.get("limit") or 50)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 500))

        all_entries: list[str] = []
        for child in sorted(target.iterdir()):
            name = child.name
            if name.startswith("_") or name.startswith("."):
                continue
            if prefix and not name.startswith(prefix):
                continue
            all_entries.append(f"  {name}/" if child.is_dir() else f"  {name}")

        label = path or "(vault root)"
        if not all_entries:
            if prefix:
                return f"No entries in {label} with prefix '{prefix}'."
            return f"Directory is empty: {label}"

        total = len(all_entries)
        page = all_entries[offset : offset + limit]
        if not page:
            return (
                f"offset {offset} is past the end ({total} entries"
                f"{' matching prefix ' + repr(prefix) if prefix else ''}). "
                f"Use offset < {total}."
            )

        shown_start = offset + 1
        shown_end = offset + len(page)
        filter_note = f" matching prefix '{prefix}'" if prefix else ""
        header = f"Contents of {label}{filter_note}:"
        body = "\n".join(page)

        if total > shown_end or offset > 0:
            footer_parts = [f"Showing {shown_start}-{shown_end} of {total}{filter_note}."]
            if total > shown_end:
                footer_parts.append(f"Call again with offset={shown_end} to continue.")
            if not prefix and total > limit:
                footer_parts.append("Narrow with prefix='<start-of-filename>'.")
            return f"{header}\n{body}\n{' '.join(footer_parts)}"
        return f"{header}\n{body}"

    def _search_content(self, arguments: dict) -> str:
        query = arguments.get("query", "")
        if not query:
            return "Error: 'query' parameter is required."
        query_lower = query.lower()
        matches = []
        for md_file in sorted(self.vault_path.rglob("*.md")):
            rel = md_file.relative_to(self.vault_path)
            if any(part.startswith("_") or part.startswith(".") for part in rel.parts):
                continue
            try:
                content = md_file.read_text(errors="replace")
            except OSError:
                continue
            for i, line in enumerate(content.splitlines(), 1):
                if query_lower in line.lower():
                    snippet = line.strip()[:120]
                    matches.append(f"  {rel}:{i}  {snippet}")
                    if len(matches) >= 20:
                        break
            if len(matches) >= 20:
                break
        if not matches:
            return f"No results for: {query}"
        return f"Found {len(matches)} match(es) for '{query}':\n" + "\n".join(matches)

    def _edit_file(self, arguments: dict) -> str:
        path = arguments.get("path", "")
        content = arguments.get("content", "")
        if not path:
            return "Error: 'path' parameter is required."
        if not content:
            return "Error: 'content' parameter is required."
        if self._is_system_path(path):
            return f"Error: writing to system directories is not allowed: {path}"
        resolved = self._resolve_safe(path)
        if resolved is None:
            return f"Error: path escapes outside vault: {path}"
        if not resolved.exists():
            return f"Error: file does not exist: {path} (use create_file for new files)"
        if self.wiki is None:
            return "Error: write operations are not available (no wiki manager)."
        meta, _ = self.wiki.read_article(path)
        title = meta.get("title", Path(path).stem)
        tags = meta.get("tags")
        self.wiki.write_article(path, title, content, tags=tags)
        self.wiki.git_commit(f"Edit {path} via chat")
        return f"Updated: {path}"

    def _create_file(self, arguments: dict) -> str:
        path = arguments.get("path", "")
        title = arguments.get("title", "")
        content = arguments.get("content", "")
        tags = arguments.get("tags")
        if not path:
            return "Error: 'path' parameter is required."
        if not title:
            return "Error: 'title' parameter is required."
        if not content:
            return "Error: 'content' parameter is required."
        if self._is_system_path(path):
            return f"Error: writing to system directories is not allowed: {path}"
        resolved = self._resolve_safe(path)
        if resolved is None:
            return f"Error: path escapes outside vault: {path}"
        if resolved.exists():
            return f"Error: file already exists: {path} (use edit_file to modify)"
        if self.wiki is None:
            return "Error: write operations are not available (no wiki manager)."
        if not path.startswith("raw/"):
            return (
                f"Error: create_file is scoped to raw/ (got: {path}). "
                "Wiki articles are produced by compile_summary, compile_batch, "
                "or consolidate. If you are trying to write a new article in "
                "a promoted category, tell the user that create_file cannot do "
                "this and propose the correct workflow."
            )
        self.wiki.write_article(path, title, content, tags=tags)
        self.wiki.git_commit(f"Create {path} via chat")
        return f"Created: {path}"

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

    async def _move_file(self, arguments: dict) -> str:
        import json
        if self.reorganizer is None:
            return json.dumps({"error": "reorganizer not available"})
        src = (arguments.get("src") or "").strip()
        dst = (arguments.get("dst") or "").strip()
        if not src or not dst:
            return json.dumps({"error": "src and dst are required"})
        try:
            self.reorganizer.move_single(src, dst)
        except (FileNotFoundError, FileExistsError, ValueError) as exc:
            return json.dumps({"error": str(exc)})
        if self.wiki is not None:
            self.wiki.git_commit(f"move: {src} -> {dst}")
        if self.retrieval is not None:
            try:
                absolute_dst = str((self.vault_path / dst).resolve())
                await self.retrieval.trigger_reindex(paths=[absolute_dst])
            except Exception as exc:
                logger.warning("reindex trigger failed after move: %s", exc)
        return json.dumps({"moved": f"{src} -> {dst}", "reindex_queued": True})

    async def _search_vault(self, arguments: dict) -> str:
        query = arguments.get("query", "")
        if not query:
            return "Error: 'query' parameter is required."
        if self.retrieval is None:
            return "Error: semantic search is not available (no retrieval client)."
        try:
            results = await self.retrieval.search(query)
        except Exception as exc:
            return f"Search error: {exc}"
        if not results:
            return f"No results for: {query}"
        lines = [f"Found {len(results)} result(s) for '{query}':"]
        for r in results:
            doc_id = r.get("id")
            path = f"{doc_id}.md" if doc_id else "?"
            name = r.get("name", "")
            summary = (r.get("summary") or "")[:100]
            lines.append(f"  [{r.get('score', 0):.2f}] {path} — {name} — {summary}")
        return "\n".join(lines)

    async def _search_web(self, arguments: dict) -> str:
        query = arguments.get("query", "")
        if not query:
            return "Error: 'query' parameter is required."
        if self.websearch is None:
            return "Error: web search is not available (no websearch client)."
        max_results = int(arguments.get("max_results", 5))
        max_results = max(1, min(max_results, 10))
        try:
            results = await self.websearch.search(query)
        except Exception as exc:
            return f"Search error: {exc}"
        results = results[:max_results]
        if not results:
            return f"No results for: {query}"
        lines = [f"Found {len(results)} result(s) for '{query}':"]
        for r in results:
            lines.append(f"  {r.title}")
            lines.append(f"    {r.url}")
            snippet = (r.snippet or "").strip().replace("\n", " ")[:200]
            if snippet:
                lines.append(f"    {snippet}")
        return "\n".join(lines)

    async def _propose_research(self, arguments: dict) -> str:
        import json as _json
        from pal.protocol import ResearchProposalMessage

        if self.approval_registry is None or self.proposal_emitter is None:
            return "Error: research proposals are not available in this session."
        topic = arguments.get("topic", "").strip()
        rationale = arguments.get("rationale", "").strip()
        if not topic:
            return "Error: 'topic' parameter is required."
        if not rationale:
            return "Error: 'rationale' parameter is required."
        depth = int(arguments.get("depth", 3))
        depth = max(1, min(depth, 10))

        proposal_id = self.approval_registry.create_proposal(
            topic=topic, depth=depth, rationale=rationale
        )
        proposal = self.approval_registry.get(proposal_id)
        self.proposal_emitter(
            ResearchProposalMessage(
                proposal_id=proposal_id,
                topic=topic,
                depth=depth,
                rationale=rationale,
            )
        )
        # Block until the CLI signals a terminal status (or expiry).
        # Bound the wait by the proposal's own expiry so a disconnected
        # CLI can't hang this tool coroutine forever.
        remaining = (proposal.expires_at - datetime.now(timezone.utc)).total_seconds()
        try:
            await asyncio.wait_for(proposal.event.wait(), timeout=max(0.1, remaining))
        except asyncio.TimeoutError:
            self.approval_registry.expire_stale()
        final = self.approval_registry.get(proposal_id)
        result = {"proposal_id": proposal_id, "status": final.status}
        if final.status == "declined":
            # If this proposal was edited, the registry links old -> new.
            edited = self.approval_registry.get_successor(proposal_id)
            if edited is not None:
                result = {
                    "proposal_id": edited.proposal_id,
                    "status": "approved",
                    "topic": edited.topic,
                    "depth": edited.depth,
                }
        elif final.status == "approved":
            result["topic"] = final.topic
            result["depth"] = final.depth
        return _json.dumps(result)

    async def _research_topic(self, arguments: dict) -> str:
        proposal_id = arguments.get("proposal_id", "").strip()
        if not proposal_id:
            return "Error: 'proposal_id' parameter is required."
        if self.approval_registry is None or self.researcher is None:
            return "Error: research execution is not available in this session."

        proposal = self.approval_registry.get(proposal_id)
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
        self.approval_registry.consume(proposal_id)

        try:
            report = await self.researcher.research_topic(
                topic=proposal.topic,
                depth=proposal.depth,
            )
        except Exception as exc:
            return f"Research error: {exc}"

        return self._format_research_report(report)

    def _format_research_report(self, report) -> str:
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
                        rel = source.summary_path.relative_to(self.vault_path)
                        lines.append(f"    summary: {rel}")
                    except ValueError:
                        lines.append(f"    summary: {source.summary_path}")
                if source.error:
                    lines.append(f"    error: {source.error}")
        return "\n".join(lines)

    async def _compile_summary(self, arguments: dict) -> str:
        import json as _json
        summary_path = arguments.get("summary_path", "").strip()
        if not summary_path:
            return "Error: 'summary_path' parameter is required."
        if self.compiler is None:
            return "Error: compile is not available in this session."
        result = await self.compiler.compile_one(summary_path)
        return _json.dumps(result)

    async def _propose_compile_batch(self, arguments: dict) -> str:
        import json as _json
        from pal.protocol import CompileProposalMessage

        if self.approval_registry is None or self.proposal_emitter is None:
            return "Error: compile proposals are not available in this session."
        paths = arguments.get("summary_paths")
        if not isinstance(paths, list) or not paths:
            return "Error: 'summary_paths' must be a non-empty list."
        rationale = (arguments.get("rationale") or "").strip()
        if not rationale:
            return "Error: 'rationale' parameter is required."

        proposal_id = self.approval_registry.create_proposal(
            kind="compile",
            summary_paths=paths,
            rationale=rationale,
        )
        proposal = self.approval_registry.get(proposal_id)
        self.proposal_emitter(
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
            self.approval_registry.expire_stale()

        final = self.approval_registry.get(proposal_id)
        result = {"proposal_id": proposal_id, "status": final.status}
        if final.status == "declined":
            edited = self.approval_registry.get_successor(proposal_id)
            if edited is not None:
                result = {
                    "proposal_id": edited.proposal_id,
                    "status": "approved",
                    "summary_paths": list(edited.summary_paths or []),
                }
        elif final.status == "approved":
            result["summary_paths"] = list(final.summary_paths or [])
        return _json.dumps(result)

    async def _compile_batch(self, arguments: dict) -> str:
        import json as _json
        proposal_id = (arguments.get("proposal_id") or "").strip()
        if not proposal_id:
            return "Error: 'proposal_id' parameter is required."
        if self.approval_registry is None or self.compiler is None:
            return "Error: compile execution is not available in this session."

        proposal = self.approval_registry.get(proposal_id)
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
        self.approval_registry.consume(proposal_id)

        per_file = []
        ok = merged = insufficient = error_count = 0
        for path in (proposal.summary_paths or []):
            try:
                outcome = await self.compiler.compile_one(path)
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
                entry["vault_exists"] = (self.vault_path / article_rel).exists()
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
        return _json.dumps(report)

    async def _propose_reorg(self, arguments: dict) -> str:
        import json as _json
        from pal.protocol import ReorgProposalMessage

        if (self.approval_registry is None or self.proposal_emitter is None
                or self.reorganizer is None):
            return "Error: reorg proposals are not available in this session."
        operations = arguments.get("operations")
        if not isinstance(operations, list) or not operations:
            return "Error: 'operations' must be a non-empty list."
        rationale = (arguments.get("rationale") or "").strip()
        if not rationale:
            return "Error: 'rationale' parameter is required."

        # Pre-validate to surface errors before prompting the user
        try:
            validation_errors = self.reorganizer.validate_operations(operations)
        except Exception as exc:
            return f"Error: operation validation failed: {exc}"
        if validation_errors:
            return "Error: invalid operations:\n" + "\n".join(validation_errors)

        # Reference-count preview
        src_paths = [op["src"] for op in operations if "src" in op]
        try:
            references_preview = self.reorganizer.count_references(src_paths)
        except Exception:
            references_preview = 0

        try:
            proposal_id = self.approval_registry.create_proposal(
                kind="reorg",
                operations=operations,
                rationale=rationale,
            )
        except ValueError as exc:
            return f"Error: {exc}"

        proposal = self.approval_registry.get(proposal_id)
        self.proposal_emitter(
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
            self.approval_registry.expire_stale()

        final = self.approval_registry.get(proposal_id)
        result = {"proposal_id": proposal_id, "status": final.status}
        if final.status == "declined":
            edited = self.approval_registry.get_successor(proposal_id)
            if edited is not None:
                result = {
                    "proposal_id": edited.proposal_id,
                    "status": "approved",
                    "operations": list(edited.operations or []),
                }
        elif final.status == "approved":
            result["operations"] = list(final.operations or [])
        return _json.dumps(result)

    async def _propose_promote(self, arguments: dict) -> str:
        import json as _json
        from pal.protocol import PromoteProposalMessage

        if (self.approval_registry is None or self.proposal_emitter is None
                or self.learning is None or self.wisdom is None):
            return "Error: promote proposals are not available in this session."

        slug = (arguments.get("slug") or "").strip()
        rationale = (arguments.get("rationale") or "").strip()
        if not slug:
            return "Error: 'slug' parameter is required."
        if not rationale:
            return "Error: 'rationale' parameter is required."

        if not self.learning.exists(slug):
            return _json.dumps({"error": f"no such learning: {slug}"})
        meta = self.learning.get_meta(slug)
        if meta.get("status") == "promoted":
            return _json.dumps({
                "error": f"already promoted at {meta.get('promoted_at', 'unknown')}"
            })

        title = meta.get("title", slug)
        body = self.learning.get(slug)

        try:
            proposal_id = self.approval_registry.create_proposal(
                kind="promote",
                rationale=rationale,
                slug=slug,
                target_title=title,
                body=body,
            )
        except ValueError as exc:
            return f"Error: {exc}"

        proposal = self.approval_registry.get(proposal_id)
        self.proposal_emitter(
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
            self.approval_registry.expire_stale()

        final = self.approval_registry.get(proposal_id)

        if final.status != "approved":
            return _json.dumps({"status": final.status, "slug": slug})

        # Execute promotion.
        self.approval_registry.consume(proposal_id)
        self.learning.mark_promoted(slug)
        self.wisdom.add(title=title, body=body)
        if self.wiki is not None:
            self.wiki.git_commit(f"promote: {slug} -> wisdom")
        return _json.dumps({"status": "promoted", "slug": slug, "title": title})

    async def _reorg(self, arguments: dict) -> str:
        import json as _json
        proposal_id = (arguments.get("proposal_id") or "").strip()
        if not proposal_id:
            return "Error: 'proposal_id' parameter is required."
        if self.approval_registry is None or self.reorganizer is None:
            return "Error: reorg execution is not available in this session."

        proposal = self.approval_registry.get(proposal_id)
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
        self.approval_registry.consume(proposal_id)

        ops = list(proposal.operations or [])

        # Re-validate against current vault state. State may have changed
        # between proposal and execute.
        validation_errors = self.reorganizer.validate_operations(ops)
        if validation_errors:
            return "Error: invalid operations:\n" + "\n".join(validation_errors)

        try:
            per_op = await self.reorganizer.execute_operations_async(ops)
        except Exception as exc:
            return f"Error: reorg execution failed: {exc}"

        # Ground-truth echo: check on-disk state for every op.
        for r in per_op:
            src = r.get("src", "")
            dst = r.get("dst", "")
            if src:
                r["src_exists_after"] = (self.vault_path / src).exists()
            if dst:
                r["dst_exists_after"] = (self.vault_path / dst).exists()

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
        return _json.dumps(report)

    async def _propose_consolidate(self, arguments: dict) -> str:
        import json as _json
        from pal.protocol import ConsolidateProposalMessage

        if self.approval_registry is None or self.proposal_emitter is None:
            return "Error: consolidate proposals are not available in this session."
        source_paths = arguments.get("source_paths")
        if not isinstance(source_paths, list) or len(source_paths) < 2:
            return "Error: 'source_paths' must be a list with at least two entries."
        target_path = (arguments.get("target_path") or "").strip()
        if not target_path:
            return "Error: 'target_path' parameter is required."
        target_title = (arguments.get("target_title") or "").strip()
        if not target_title:
            return "Error: 'target_title' parameter is required."
        rationale = (arguments.get("rationale") or "").strip()
        if not rationale:
            return "Error: 'rationale' parameter is required."

        try:
            proposal_id = self.approval_registry.create_proposal(
                kind="consolidate",
                summary_paths=source_paths,
                target_path=target_path,
                target_title=target_title,
                rationale=rationale,
            )
        except ValueError as exc:
            return f"Error: {exc}"

        proposal = self.approval_registry.get(proposal_id)
        self.proposal_emitter(
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
            self.approval_registry.expire_stale()

        final = self.approval_registry.get(proposal_id)
        result = {"proposal_id": proposal_id, "status": final.status}
        if final.status == "declined":
            edited = self.approval_registry.get_successor(proposal_id)
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
        return _json.dumps(result)

    async def _consolidate(self, arguments: dict) -> str:
        import json as _json
        proposal_id = (arguments.get("proposal_id") or "").strip()
        if not proposal_id:
            return "Error: 'proposal_id' parameter is required."
        if self.approval_registry is None or self.consolidator is None:
            return "Error: consolidate execution is not available in this session."

        proposal = self.approval_registry.get(proposal_id)
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
        self.approval_registry.consume(proposal_id)

        try:
            outcome = await self.consolidator.consolidate(
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
        return _json.dumps(outcome)

    async def _wait_for_reindex(self, arguments: dict) -> str:
        import asyncio
        import json as _json

        job_id = (arguments.get("job_id") or "").strip()
        if not job_id:
            return "Error: 'job_id' parameter is required."
        if self.retrieval is None:
            return "Error: retrieval client is not configured."

        timeout_seconds = int(arguments.get("timeout_seconds") or 30)
        timeout_seconds = max(1, min(timeout_seconds, 120))

        deadline = asyncio.get_event_loop().time() + timeout_seconds
        last_status = "unknown"
        while True:
            job = await self.retrieval.get_reindex_job(job_id)
            if job is None:
                return f"Error: unknown job_id (not found): {job_id}"
            last_status = job.get("status", "unknown")
            if last_status in ("done", "error"):
                return _json.dumps(job)
            if asyncio.get_event_loop().time() >= deadline:
                return _json.dumps({
                    "job_id": job_id,
                    "status": "timeout",
                    "last_seen_status": last_status,
                    "_note": (
                        "Job did not finish within timeout. The job may still complete; "
                        "poll again with a longer timeout if needed."
                    ),
                })
            await asyncio.sleep(0.25)

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
