"""PAL vault Tool subclasses (Phase F PR2).

Six Tool subclasses migrated from pal._legacy_tools.ToolExecutor:
  ReadFile, ListDirectory, SearchContent — read-only, require only config.
  EditFile, CreateFile, MoveFile — write tools, require config + retrieval
    (retrieval may be None; reindex is skipped when absent).

EditFile uses old_str/new_str in-place string replacement (more surgical
than the legacy full-body rewrite). CreateFile writes raw file content
directly, scoped to raw/. MoveFile delegates to ctx.agent.reorganizer.

All path operations use _resolve_safe to prevent vault escapes. System
directories (_-prefixed) are blocked on write operations.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from agent_core.tools.base import Tool

if TYPE_CHECKING:
    from agent_core.agent import HandlerContext

logger = logging.getLogger(__name__)

# Maximum characters to return from a file read (~8 000 tokens ≈ 32 000 chars).
_READ_LIMIT = 32_000


def _resolve_safe(vault: Path, path: str) -> Path | None:
    """Resolve a vault-relative path; return None if it escapes the vault."""
    full = (vault / path).resolve()
    if not full.is_relative_to(vault):
        return None
    return full


def _is_system_path(path: str) -> bool:
    """Return True if any component of path is _-prefixed (system dir)."""
    return any(part.startswith("_") for part in Path(path).parts)


# ---------------------------------------------------------------------------
# ReadFile
# ---------------------------------------------------------------------------

class ReadFile(Tool):
    """Read a file from the vault, returning frontmatter and body."""

    name = "read_file"
    description = "Read a file from the vault. Returns frontmatter and body."
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "File path relative to vault root (e.g. 'Research/quantum.md')"
                ),
            },
        },
        "required": ["path"],
    }
    requires = ("config",)

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        path = (args.get("path") or "").strip()
        if not path:
            return "Error: 'path' parameter is required."

        vault = ctx.agent.config.vault_path.resolve()
        resolved = _resolve_safe(vault, path)
        if resolved is None:
            return f"Error: path escapes outside vault: {path}"
        if not resolved.exists():
            return f"File not found: {path}"
        if not resolved.is_file():
            return f"Not a file: {path} (use list_directory for directories)"

        content = resolved.read_text(errors="replace")
        if len(content) > _READ_LIMIT:
            content = (
                content[:_READ_LIMIT]
                + f"\n\n[Truncated — file exceeds {_READ_LIMIT} characters]"
            )
        return content


# ---------------------------------------------------------------------------
# ListDirectory
# ---------------------------------------------------------------------------

class ListDirectory(Tool):
    """List files and subdirectories in a vault directory. Paginated."""

    name = "list_directory"
    description = (
        "List files and subdirectories in a vault directory. Paginated: by default "
        "returns up to 50 entries with a footer indicating the total and how to "
        "continue. Use prefix to filter when reorganizing large directories."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Directory path relative to vault root (e.g. 'Research'). "
                    "Empty or omitted for root."
                ),
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
                "description": (
                    "Only return entries whose filename starts with this string "
                    "(e.g. 'agent-')."
                ),
            },
        },
        "required": [],
    }
    requires = ("config",)

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        vault = ctx.agent.config.vault_path.resolve()
        path = (args.get("path") or "").strip()

        target = _resolve_safe(vault, path) if path else vault
        if target is None:
            return f"Error: path escapes outside vault: {path}"
        if not target.exists():
            return f"Directory not found: {path}"
        if not target.is_dir():
            return f"Not a directory: {path} (use read_file for files)"

        prefix = (args.get("prefix") or "").strip()
        try:
            offset = max(0, int(args.get("offset") or 0))
        except (TypeError, ValueError):
            offset = 0
        try:
            limit = int(args.get("limit") or 50)
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


# ---------------------------------------------------------------------------
# SearchContent
# ---------------------------------------------------------------------------

class SearchContent(Tool):
    """Keyword search across vault files. Returns matching filenames with line snippets."""

    name = "search_content"
    description = (
        "Keyword search across vault files. Returns matching filenames with line snippets."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search term or phrase to find in vault files.",
            },
        },
        "required": ["query"],
    }
    requires = ("config",)

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        query = (args.get("query") or "").strip()
        if not query:
            return "Error: 'query' parameter is required."

        vault = ctx.agent.config.vault_path.resolve()
        query_lower = query.lower()
        matches: list[str] = []

        for md_file in sorted(vault.rglob("*.md")):
            rel = md_file.relative_to(vault)
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


# ---------------------------------------------------------------------------
# EditFile
# ---------------------------------------------------------------------------

class EditFile(Tool):
    """Edit a vault file by replacing old_str with new_str. Triggers reindex on success."""

    name = "edit_file"
    description = (
        "Edit an existing vault file by replacing a specific string. "
        "Preserves the rest of the file content. Triggers reindex."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "File path relative to vault root (e.g. 'Research/quantum.md'). "
                    "Must already exist."
                ),
            },
            "old_str": {
                "type": "string",
                "description": "Exact string to replace (must appear in the file).",
            },
            "new_str": {
                "type": "string",
                "description": "Replacement string.",
            },
        },
        "required": ["path", "old_str", "new_str"],
    }
    requires = ("config",)

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        path = (args.get("path") or "").strip()
        old_str = args.get("old_str", "")
        new_str = args.get("new_str", "")

        if not path:
            return "Error: 'path' parameter is required."
        if not old_str and old_str is not None:
            # Allow empty new_str (deletion), but old_str must be non-empty
            return "Error: 'old_str' parameter is required."

        if _is_system_path(path):
            return f"Error: writing to system directories is not allowed: {path}"

        vault = ctx.agent.config.vault_path.resolve()
        resolved = _resolve_safe(vault, path)
        if resolved is None:
            return f"Error: path escapes outside vault: {path}"
        if not resolved.exists():
            return f"File not found: {path}"
        if not resolved.is_file():
            return f"Not a file: {path}"

        content = resolved.read_text(errors="replace")
        if old_str not in content:
            return f"Error: old_str not found in {path}"

        new_content = content.replace(old_str, new_str, 1)
        resolved.write_text(new_content)

        # Trigger reindex if retrieval client is available.
        retrieval = getattr(ctx.agent, "retrieval", None)
        if retrieval is not None:
            try:
                await retrieval.trigger_reindex(paths=[str(resolved)])
            except Exception as exc:
                logger.warning("reindex trigger failed after edit_file: %s", exc)

        return f"Updated: {path}"


# ---------------------------------------------------------------------------
# CreateFile
# ---------------------------------------------------------------------------

class CreateFile(Tool):
    """Create a new file under raw/. Refuses to overwrite. Triggers reindex on success."""

    name = "create_file"
    description = (
        "Create a new scratch note under raw/notes/. Scoped to raw/ to "
        "preserve the promotion discipline: wiki articles are produced by "
        "compile_summary, compile_batch, or consolidate, never by create_file. "
        "If the user asks you to write a new wiki article in a promoted "
        "category (e.g. Security/, Reverse-Engineering/, Research/), say you "
        "cannot do that directly and propose the right tool (research + "
        "compile, or consolidate if merging existing articles)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Path relative to vault root. Must begin with 'raw/' "
                    "(e.g. 'raw/notes/my-note.md'). Must not already exist."
                ),
            },
            "content": {
                "type": "string",
                "description": "File content (markdown).",
            },
        },
        "required": ["path", "content"],
    }
    requires = ("config",)

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        path = (args.get("path") or "").strip()
        content = args.get("content", "")

        if not path:
            return "Error: 'path' parameter is required."
        if not content and content is not None:
            return "Error: 'content' parameter is required."

        if _is_system_path(path):
            return f"Error: writing to system directories is not allowed: {path}"

        vault = ctx.agent.config.vault_path.resolve()
        resolved = _resolve_safe(vault, path)
        if resolved is None:
            return f"Error: path escapes outside vault: {path}"
        if resolved.exists():
            return f"Error: file already exists: {path} (use edit_file to modify)"

        if not path.startswith("raw/"):
            return (
                f"Error: create_file is scoped to raw/ (got: {path}). "
                "Wiki articles are produced by compile_summary, compile_batch, "
                "or consolidate. If you are trying to write a new article in "
                "a promoted category, tell the user that create_file cannot do "
                "this and propose the correct workflow."
            )

        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content)

        # Trigger reindex if retrieval client is available.
        retrieval = getattr(ctx.agent, "retrieval", None)
        if retrieval is not None:
            try:
                await retrieval.trigger_reindex(paths=[str(resolved)])
            except Exception as exc:
                logger.warning("reindex trigger failed after create_file: %s", exc)

        return f"Created: {path}"


# ---------------------------------------------------------------------------
# MoveFile
# ---------------------------------------------------------------------------

class MoveFile(Tool):
    """Move a single vault article from src to dst. Triggers reindex on success."""

    name = "move_file"
    description = (
        "Move a single vault article from src to dst. Use for quick "
        "re-categorization (for example, moving a mis-categorized "
        "article from Security/ to IoT/). For batch moves or merges, "
        "use propose_reorg instead. Triggers reindex. Rejects paths "
        "inside raw/ or underscore-prefixed system directories."
    )
    parameters = {
        "type": "object",
        "properties": {
            "src": {
                "type": "string",
                "description": "Current path (relative to vault root).",
            },
            "dst": {
                "type": "string",
                "description": (
                    "Destination path (relative to vault root). Must not exist."
                ),
            },
        },
        "required": ["src", "dst"],
    }
    requires = ("config",)

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        src = (args.get("src") or "").strip()
        dst = (args.get("dst") or "").strip()

        if not src or not dst:
            return json.dumps({"error": "src and dst are required"})

        if _is_system_path(src) or _is_system_path(dst):
            return json.dumps({
                "error": f"move_file rejects system directories (_-prefixed): {src} -> {dst}"
            })

        vault = ctx.agent.config.vault_path.resolve()

        src_resolved = _resolve_safe(vault, src)
        if src_resolved is None:
            return json.dumps({"error": f"path escapes outside vault: {src}"})

        dst_resolved = _resolve_safe(vault, dst)
        if dst_resolved is None:
            return json.dumps({"error": f"path escapes outside vault: {dst}"})

        if not src_resolved.exists():
            return json.dumps({"error": f"source not found: {src}"})
        if dst_resolved.exists():
            return json.dumps({"error": f"destination already exists: {dst}"})

        dst_resolved.parent.mkdir(parents=True, exist_ok=True)
        src_resolved.rename(dst_resolved)

        # Trigger reindex if retrieval client is available.
        retrieval = getattr(ctx.agent, "retrieval", None)
        if retrieval is not None:
            try:
                await retrieval.trigger_reindex(paths=[str(dst_resolved)])
            except Exception as exc:
                logger.warning("reindex trigger failed after move_file: %s", exc)

        return json.dumps({"moved": f"{src} -> {dst}", "reindex_queued": True})
