"""Compile commands — /compile and /compile-batch."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from agent_core.commands.base import Command
from agent_core.protocol.messages import ErrorMessage, ResponseMessage, ToolProgressMessage

logger = logging.getLogger(__name__)


class Compile(Command):
    name = "compile"
    args = "<t>"
    description = "Compile a wiki article"
    requires = ("compiler",)

    async def run(self, raw_args: str, ctx) -> AsyncIterator:
        from agent_core.protocol import encode_message

        writer = ctx.writer
        summary_path = raw_args.strip()
        if not summary_path:
            yield ErrorMessage(error="Usage: /compile <summary-path>")
            return

        outcome = await ctx.agent.compiler.compile_one(summary_path)

        if outcome["status"] in ("invalid_path", "not_found", "error"):
            yield ErrorMessage(error=outcome["reason"])
            return

        if outcome["status"] == "insufficient":
            yield ResponseMessage(
                text=(
                    f"{outcome['reason']}\n\n"
                    "No article saved. The source summary may need more detail."
                ),
                command="compile",
            )
            return

        verb = "Updated" if outcome["status"] == "merged" else "Saved to"
        yield ResponseMessage(
            text=(
                f"{verb} {outcome['article_path_rel']}\n\n"
                f"{outcome['compiled_truth']}"
            ),
            command="compile",
        )


class CompileBatch(Command):
    name = "compile-batch"
    args = ""
    description = "Compile all summaries in raw/summaries/"
    requires = ("compiler", "config")

    async def run(self, raw_args: str, ctx) -> AsyncIterator:
        from agent_core.protocol import encode_message

        writer = ctx.writer
        summaries_dir = ctx.agent.config.vault_path / "raw" / "summaries"
        if not summaries_dir.exists():
            yield ErrorMessage(error=f"No summaries directory at {summaries_dir}")
            return

        summary_files = sorted(summaries_dir.glob("*.md"))
        # Filter out .dirty backups
        summary_files = [
            p for p in summary_files
            if not p.name.endswith(".dirty.md") and not p.name.endswith(".md.dirty")
        ]

        if not summary_files:
            yield ResponseMessage(
                text="No summaries to compile in raw/summaries/",
                command="compile-batch",
            )
            return

        total = len(summary_files)
        compiled_new = 0
        merged = 0
        insufficient = 0
        errors = []

        for i, path in enumerate(summary_files, 1):
            rel = str(path.relative_to(ctx.agent.config.vault_path))
            title_preview = path.stem[:50]

            progress = ToolProgressMessage(
                tool="compile-batch",
                arguments={"status": f"Compiling {i}/{total}: {title_preview}"},
            )
            writer.write(encode_message(progress))
            await writer.drain()

            try:
                outcome = await ctx.agent.compiler.compile_one(rel)
            except Exception as exc:
                logger.exception("Batch compile failed for %s: %s", rel, exc)
                errors.append((rel, str(exc)))
                continue

            if outcome["status"] == "ok":
                compiled_new += 1
            elif outcome["status"] == "merged":
                merged += 1
            elif outcome["status"] == "insufficient":
                insufficient += 1
            else:
                errors.append((rel, outcome.get("reason", outcome["status"])))

        lines = [
            f"Batch compile complete: {total} summaries processed",
            "",
            f"  + New articles: {compiled_new}",
            f"  ~ Merged into existing: {merged}",
            f"  ! Insufficient content: {insufficient}",
            f"  x Errors: {len(errors)}",
        ]

        if errors:
            lines.append("")
            lines.append("Failed summaries:")
            for rel, reason in errors[:20]:
                lines.append(f"  - {rel}: {reason[:80]}")
            if len(errors) > 20:
                lines.append(f"  ... and {len(errors) - 20} more")

        yield ResponseMessage(text="\n".join(lines), command="compile-batch")
