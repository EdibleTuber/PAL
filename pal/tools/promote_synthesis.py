"""PAL chat-derived promotion tool: propose_promote_synthesis."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from agent_core.tools.base import Tool
from agent_core.utils.frontmatter import serialize_frontmatter

if TYPE_CHECKING:
    from agent_core.agent import HandlerContext


_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def _slugify(title: str) -> str:
    s = title.lower().replace("_", "-").replace(" ", "-")
    s = _SLUG_RE.sub("", s).strip("-")
    return s or "untitled"


class PromoteSynthesisProposal(Tool):
    name = "propose_promote_synthesis"
    description = (
        "Propose promoting a chat-derived synthesis note (or an existing "
        "orphan note in raw/notes/) into a wiki article. The note body "
        "becomes the compiled truth directly (no LLM re-extraction). "
        "Required: a synthesis note already at raw/notes/<slug>.md "
        "containing ## Overview and ## Key Concepts sections. Blocks for "
        "user approval; on approval, writes a chat-derived summary and "
        "invokes the chat-aware compile path. Source path must be a file "
        "directly under raw/notes/."
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Proposed article title.",
            },
            "rationale": {
                "type": "string",
                "description": "One-line reason shown to the user in the approval prompt.",
            },
            "note_path": {
                "type": "string",
                "description": "Path to synthesis note under raw/notes/ (e.g. 'raw/notes/foo.md').",
            },
        },
        "required": ["title", "rationale", "note_path"],
    }
    requires = ("approval_registry", "compiler")

    async def run(self, args: dict, ctx: "HandlerContext") -> str:
        from pal.protocol import PromoteSynthesisProposalMessage

        if ctx.agent.approval_registry is None or ctx.agent.compiler is None:
            return "Error: promote_synthesis is not available in this session."

        title = (args.get("title") or "").strip()
        rationale = (args.get("rationale") or "").strip()
        note_path = (args.get("note_path") or "").strip()

        if not title:
            return "Error: 'title' parameter is required."
        if not rationale:
            return "Error: 'rationale' parameter is required."
        if not note_path:
            return "Error: 'note_path' parameter is required."

        # Path discipline: must be under raw/notes/, no traversal.
        if ".." in note_path.split("/") or note_path.startswith("/"):
            return json.dumps({"status": "invalid_path", "reason": f"Invalid note_path: {note_path}"})
        if not note_path.startswith("raw/notes/"):
            return json.dumps({
                "status": "invalid_path",
                "reason": "note_path must be under raw/notes/",
            })

        vault_path = ctx.agent.config.vault_path
        full_note = vault_path / note_path
        if not full_note.exists() or not full_note.is_file():
            return json.dumps({"status": "note_not_found", "reason": f"No file at {note_path}"})

        # Resolved-path boundary check.
        try:
            resolved = full_note.resolve()
            if not str(resolved).startswith(str(vault_path.resolve()) + "/"):
                return json.dumps({"status": "invalid_path", "reason": "note_path escapes vault"})
        except Exception:
            return json.dumps({"status": "invalid_path", "reason": "note_path resolution failed"})

        note_body = full_note.read_text()
        slug = _slugify(title)
        body_preview = note_body[:600]

        ar = ctx.agent.approval_registry
        try:
            proposal_id = ar.create_proposal(
                kind="promote_synthesis",
                rationale=rationale,
                note_path=note_path,
                target_title=title,
                slug=slug,
            )
        except ValueError as exc:
            return json.dumps({"status": "error", "reason": str(exc)})

        proposal = ar.get(proposal_id)
        await ctx.emit(
            PromoteSynthesisProposalMessage(
                proposal_id=proposal_id,
                title=title,
                rationale=rationale,
                note_path=note_path,
                note_body_preview=body_preview,
            )
        )

        remaining = (proposal.expires_at - datetime.now(timezone.utc)).total_seconds()
        try:
            await asyncio.wait_for(proposal.event.wait(), timeout=max(0.1, remaining))
        except asyncio.TimeoutError:
            ar.expire_stale()

        final = ar.get(proposal_id)
        if final.status != "approved":
            return json.dumps({"status": final.status, "title": title})

        ar.consume(proposal_id)

        # Write the chat-derived summary file.
        note_hash = hashlib.sha1(note_body.encode("utf-8")).hexdigest()
        summary_rel = f"raw/summaries/{slug}.md"
        summary_full = vault_path / summary_rel
        if summary_full.exists():
            return json.dumps({
                "status": "summary_collision",
                "reason": f"raw/summaries/{slug}.md already exists; pick a different title",
            })
        summary_full.parent.mkdir(parents=True, exist_ok=True)

        summary_meta = {
            "title": title,
            "source_file": note_path,
            "source_url": "",
            "source_type": "chat",
            "source_hash": note_hash,
            "source_raw": note_path,
        }
        summary_text = serialize_frontmatter(summary_meta, note_body)
        summary_full.write_text(summary_text)

        try:
            outcome = await ctx.agent.compiler.compile_chat_synthesis(summary_rel)
        except Exception as exc:
            outcome = {
                "status": "error",
                "title": title,
                "reason": f"{type(exc).__name__}: {exc}",
            }

        outcome["_note"] = (
            "Trust article_path_rel; if vault_exists is false (when set), the "
            "file was not written to disk."
        )
        article_rel = outcome.get("article_path_rel")
        if article_rel:
            outcome["vault_exists"] = (vault_path / article_rel).exists()
        return json.dumps(outcome)
