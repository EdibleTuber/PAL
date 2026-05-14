"""PAL vault write Tool subclasses.

Three write Tool subclasses (EditFile, CreateFile, MoveFile) for vault
mutation. Read-side tools (ReadFile, ListDirectory, SearchContent) were
dropped in favor of agent_core builtins (cat, ls, grep), which cover the
same functionality with broader capability.

All path operations use _resolve_safe to prevent vault escapes. System
directories (_-prefixed) are blocked on write operations.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from agent_core.tools.base import Tool
from agent_core.tools._shell_helpers import format_not_found_with_suggestions

if TYPE_CHECKING:
    from agent_core.agent import HandlerContext

logger = logging.getLogger(__name__)


async def _maybe_reindex(retrieval, paths: list[str]) -> dict | None:
    """Trigger reindex for the given absolute paths.

    Returns the inference server's response dict on success
    (`{job_id, status, paths}`), or None on any failure (no retrieval
    client, server unreachable, exception). Logs failures at WARN.

    Used by the 5 vault write tools to propagate `wait_for_reindex`-ready
    job_ids into their canonical {status, path, reindex} envelope.
    """
    if retrieval is None:
        return None
    try:
        return await retrieval.trigger_reindex(paths=paths)
    except Exception as exc:
        logger.warning("reindex trigger failed: %s", exc)
        return None


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
# EditFile
# ---------------------------------------------------------------------------

class EditFile(Tool):
    """Rewrite the body of an existing vault file. Preserves frontmatter."""

    name = "edit_file"
    description = (
        "Rewrite the entire body of an existing vault file. Preserves frontmatter "
        "(title, tags). Use ONLY for structural overhauls where most of the body is "
        "being replaced (e.g., reorganizing sections, swapping a draft for a final "
        "version). For targeted changes (typo fix, link update, single-line edit, "
        "adding a paragraph), use replace_in_file instead. The cost difference is "
        "significant: edit_file requires retransmitting the entire body; "
        "replace_in_file only the changed strings."
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
            "content": {
                "type": "string",
                "description": "New body content for the file (markdown, without frontmatter).",
            },
        },
        "required": ["path", "content"],
    }
    requires = ("config",)

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        path = args.get("path", "")
        content = args.get("content", "")

        if not path:
            return json.dumps({"status": "error", "path": "", "reason": "'path' parameter is required."})
        if not content:
            return json.dumps({"status": "error", "path": path, "reason": "'content' parameter is required."})

        if _is_system_path(path):
            return json.dumps({
                "status": "error",
                "path": path,
                "reason": f"writing to system directories is not allowed: {path}",
            })

        vault = ctx.agent.config.vault_path.resolve()
        resolved = _resolve_safe(vault, path)
        if resolved is None:
            return json.dumps({
                "status": "error",
                "path": path,
                "reason": f"path escapes outside vault: {path}",
            })
        if not resolved.exists():
            base = f"file does not exist: {path} (use create_file for new files)"
            reason = format_not_found_with_suggestions(ctx.agent.config.vault_path, path, base)
            return json.dumps({"status": "error", "path": path, "reason": reason})

        wiki = getattr(ctx.agent, "wiki", None)
        if wiki is None:
            return json.dumps({
                "status": "error",
                "path": path,
                "reason": "write operations are not available (no wiki manager).",
            })

        meta, _ = wiki.read_article(path)
        title = meta.get("title", Path(path).stem)
        tags = meta.get("tags")
        wiki.write_article(path, title, content, tags=tags)
        wiki.git_commit(f"Edit {path} via chat")

        reindex = await _maybe_reindex(getattr(ctx.agent, "retrieval", None), [str(resolved)])
        return json.dumps({"status": "updated", "path": path, "reindex": reindex})


# ---------------------------------------------------------------------------
# CreateFile
# ---------------------------------------------------------------------------

class CreateFile(Tool):
    """Create a new scratch note under raw/. Refuses to overwrite. Triggers reindex on success."""

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
    }
    requires = ("config",)

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        path = args.get("path", "")
        title = args.get("title", "")
        content = args.get("content", "")
        tags = args.get("tags")

        if not path:
            return json.dumps({"status": "error", "path": "", "reason": "'path' parameter is required."})
        if not title:
            return json.dumps({"status": "error", "path": path, "reason": "'title' parameter is required."})
        if not content:
            return json.dumps({"status": "error", "path": path, "reason": "'content' parameter is required."})

        if _is_system_path(path):
            return json.dumps({
                "status": "error",
                "path": path,
                "reason": f"writing to system directories is not allowed: {path}",
            })

        vault = ctx.agent.config.vault_path.resolve()
        resolved = _resolve_safe(vault, path)
        if resolved is None:
            return json.dumps({
                "status": "error",
                "path": path,
                "reason": f"path escapes outside vault: {path}",
            })
        if resolved.exists():
            return json.dumps({
                "status": "error",
                "path": path,
                "reason": f"file already exists: {path} (use edit_file to modify)",
            })

        wiki = getattr(ctx.agent, "wiki", None)
        if wiki is None:
            return json.dumps({
                "status": "error",
                "path": path,
                "reason": "write operations are not available (no wiki manager).",
            })

        if not path.startswith("raw/"):
            return json.dumps({
                "status": "error",
                "path": path,
                "reason": (
                    f"create_file is scoped to raw/ (got: {path}). "
                    "Wiki articles are produced by compile_summary, compile_batch, "
                    "or consolidate. If you are trying to write a new article in "
                    "a promoted category, tell the user that create_file cannot do "
                    "this and propose the correct workflow."
                ),
            })

        wiki.write_article(path, title, content, tags=tags)
        wiki.git_commit(f"Create {path} via chat")

        reindex = await _maybe_reindex(getattr(ctx.agent, "retrieval", None), [str(resolved)])
        return json.dumps({"status": "created", "path": path, "reindex": reindex})


# ---------------------------------------------------------------------------
# MoveFile
# ---------------------------------------------------------------------------

class MoveFile(Tool):
    """Move a single vault article from src to dst via Reorganizer. Triggers reindex on success."""

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
        reorganizer = getattr(ctx.agent, "reorganizer", None)
        if reorganizer is None:
            return json.dumps({"error": "reorganizer not available"})

        src = (args.get("src") or "").strip()
        dst = (args.get("dst") or "").strip()
        if not src or not dst:
            return json.dumps({"error": "src and dst are required"})

        vault = ctx.agent.config.vault_path.resolve()
        resolved_src = _resolve_safe(vault, src)
        if resolved_src is not None and not resolved_src.exists():
            return format_not_found_with_suggestions(
                ctx.agent.config.vault_path,
                src,
                f"Error: file does not exist: {src}",
            )

        try:
            reorganizer.move_single(src, dst)
        except (FileNotFoundError, FileExistsError, ValueError) as exc:
            return json.dumps({"error": str(exc)})

        wiki = getattr(ctx.agent, "wiki", None)
        if wiki is not None:
            wiki.git_commit(f"move: {src} -> {dst}")

        retrieval = getattr(ctx.agent, "retrieval", None)
        if retrieval is not None:
            try:
                absolute_dst = str((ctx.agent.config.vault_path / dst).resolve())
                await retrieval.trigger_reindex(paths=[absolute_dst])
            except Exception as exc:
                logger.warning("reindex trigger failed after move_file: %s", exc)

        return json.dumps({"moved": f"{src} -> {dst}", "reindex_queued": True})


# ---------------------------------------------------------------------------
# DeleteFile
# ---------------------------------------------------------------------------

class DeleteFile(Tool):
    """Delete a vault file. Atomic git rm. Reversible via git history."""

    name = "delete_file"
    description = (
        "Delete a vault file. Stages the removal atomically via git rm and commits. "
        "Recoverable from git history with `git revert`. Refuses underscore-prefixed "
        "system directories (_wisdom, _learning, _config, _channels, _profile). "
        "Triggers reindex to remove the file from the embedding store. Reports if "
        "reindex fails so the caller knows the embedding store is temporarily stale."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Path relative to vault root (e.g. 'Hardware/old-article.md'). "
                    "Must already exist. Must not be in a system directory."
                ),
            },
        },
        "required": ["path"],
    }
    requires = ("config",)

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        path = args.get("path", "")
        if not path:
            return json.dumps({"status": "error", "path": "", "reason": "'path' parameter is required."})

        if _is_system_path(path):
            return json.dumps({
                "status": "error",
                "path": path,
                "reason": f"writing to system directories is not allowed: {path}",
            })

        vault = ctx.agent.config.vault_path.resolve()
        resolved = _resolve_safe(vault, path)
        if resolved is None:
            return json.dumps({
                "status": "error",
                "path": path,
                "reason": f"path escapes outside vault: {path}",
            })
        if not resolved.exists():
            base = f"file does not exist: {path}"
            reason = format_not_found_with_suggestions(ctx.agent.config.vault_path, path, base)
            return json.dumps({"status": "error", "path": path, "reason": reason})

        wiki = getattr(ctx.agent, "wiki", None)
        if wiki is None:
            return json.dumps({
                "status": "error",
                "path": path,
                "reason": "write operations are not available (no wiki manager).",
            })

        try:
            wiki.git_rm(path)
        except Exception as exc:
            return json.dumps({"status": "error", "path": path, "reason": f"git rm failed: {exc}"})

        try:
            wiki.git_commit(f"Delete {path} via chat")
        except Exception as exc:
            return json.dumps({
                "status": "deleted_uncommitted",
                "path": path,
                "warning": f"git commit failed; staged removal in index, manual recovery required: {exc}",
            })

        reindex = await _maybe_reindex(getattr(ctx.agent, "retrieval", None), [str(resolved)])
        return json.dumps({"status": "deleted", "path": path, "reindex": reindex})


# ---------------------------------------------------------------------------
# ReplaceInFile
# ---------------------------------------------------------------------------

class ReplaceInFile(Tool):
    """Replace exact string match in body of a vault file. Frontmatter preserved."""

    name = "replace_in_file"
    description = (
        "Replace an exact string match in the body of an existing vault file. "
        "Frontmatter is parsed and reattached unchanged; this tool does not modify "
        "YAML metadata. Whitespace-sensitive. Requires old_string to be unique "
        "in the body unless replace_all is true. Useful for targeted edits without "
        "rewriting the whole body, including appending content (use the trailing "
        "portion of the body as old_string and the same trailing portion plus your "
        "new content as new_string)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Path relative to vault root. Must already exist. Must not be in "
                    "a system directory."
                ),
            },
            "old_string": {
                "type": "string",
                "description": (
                    "Exact string to find in the body. Must appear in the body. Must "
                    "be unique unless replace_all is true. Whitespace-sensitive (preserve "
                    "indentation and newlines exactly). To make a non-unique match unique, "
                    "widen old_string to include surrounding lines."
                ),
            },
            "new_string": {
                "type": "string",
                "description": (
                    "Replacement string. Empty string deletes the matched content."
                ),
            },
            "replace_all": {
                "type": "boolean",
                "description": (
                    "If true, replace every occurrence of old_string in the body. If "
                    "false (default), require old_string to be unique and replace one "
                    "occurrence."
                ),
                "default": False,
            },
        },
        "required": ["path", "old_string", "new_string"],
    }
    requires = ("config",)

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        path = args.get("path", "")
        old_string = args.get("old_string", "")
        new_string = args.get("new_string")
        replace_all = bool(args.get("replace_all", False))

        if not path:
            return "Error: 'path' parameter is required."
        if not old_string:
            return "Error: 'old_string' parameter is required."
        if new_string is None:
            return "Error: 'new_string' parameter is required."

        if _is_system_path(path):
            return f"Error: writing to system directories is not allowed: {path}"

        vault = ctx.agent.config.vault_path.resolve()
        resolved = _resolve_safe(vault, path)
        if resolved is None:
            return f"Error: path escapes outside vault: {path}"
        if not resolved.exists():
            return format_not_found_with_suggestions(
                ctx.agent.config.vault_path,
                path,
                f"Error: file does not exist: {path}",
            )

        wiki = getattr(ctx.agent, "wiki", None)
        if wiki is None:
            return "Error: write operations are not available (no wiki manager)."

        from agent_core.utils.frontmatter import parse_frontmatter, serialize_frontmatter

        original_text = resolved.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(original_text)

        count = body.count(old_string)
        if count == 0:
            return f"Error: old_string not found in body of {path}"
        if count > 1 and not replace_all:
            return (
                f"Error: old_string appears {count} times in body of {path}; "
                f"pass replace_all=true, or widen old_string to include surrounding "
                f"lines until it is unique in the body."
            )

        if old_string == new_string:
            return json.dumps({
                "status": "no_change",
                "path": path,
                "occurrences": 0,
                "reindex": "skipped",
                "note": "no-op (old_string equals new_string)",
            })

        if replace_all:
            new_body = body.replace(old_string, new_string)
            occurrences = count
        else:
            new_body = body.replace(old_string, new_string, 1)
            occurrences = 1

        resolved.write_text(serialize_frontmatter(meta, new_body), encoding="utf-8")

        try:
            wiki.git_commit(f"Edit {path} via chat (replace_in_file)")
        except Exception as exc:
            # Restore original content
            resolved.write_text(original_text, encoding="utf-8")
            return f"Error: git commit failed; original content restored: {exc}"

        reindex_status = "ok"
        retrieval = getattr(ctx.agent, "retrieval", None)
        if retrieval is not None:
            try:
                await retrieval.trigger_reindex(paths=[str(resolved)])
            except Exception as exc:
                logger.warning("reindex trigger failed after replace_in_file: %s", exc)
                reindex_status = "failed"

        return json.dumps({
            "status": "replaced",
            "path": path,
            "occurrences": occurrences,
            "reindex": reindex_status,
        })
