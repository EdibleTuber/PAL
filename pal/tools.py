"""Vault tools for chat — read and write access to wiki content.

Defines tool schemas (OpenAI function-calling format) and a ToolExecutor
that runs tool calls against the vault.
"""
from pathlib import Path
from typing import TYPE_CHECKING

from pal.retrieval import RetrievalClient
from pal.wiki import WikiManager

if TYPE_CHECKING:
    from pal.approval_registry import ApprovalRegistry
    from pal.websearch import WebSearchClient
    from pal.researcher import Researcher

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
            "description": "List files and subdirectories in a vault directory. Omit path to list the vault root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path relative to vault root (e.g. 'Research'). Empty or omitted for root.",
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
            "description": "Create a new file in the vault with proper frontmatter. Use for writing new notes or articles.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to vault root (e.g. 'Research/new-topic.md'). Must not already exist.",
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
    ) -> None:
        self.vault_path = vault_path.resolve()
        self.retrieval = retrieval
        self.wiki = wiki
        self.approval_registry = approval_registry
        self.websearch = websearch
        self.researcher = researcher
        self.proposal_emitter = proposal_emitter

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
        entries = []
        for child in sorted(target.iterdir()):
            name = child.name
            if name.startswith("_") or name.startswith("."):
                continue
            if child.is_dir():
                entries.append(f"  {name}/")
            else:
                entries.append(f"  {name}")
        if not entries:
            return f"Directory is empty: {path or '(vault root)'}"
        header = f"Contents of {path or '(vault root)'}:"
        return header + "\n" + "\n".join(entries)

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
        self.wiki.write_article(path, title, content, tags=tags)
        self.wiki.git_commit(f"Create {path} via chat")
        return f"Created: {path}"

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
            lines.append(f"  [{r.get('score', 0):.2f}] {r.get('name', '?')} — {r.get('summary', '')[:100]}")
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
