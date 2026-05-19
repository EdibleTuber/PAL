"""PAL domain commands -- wiki lint, import, learning, status, profile, wisdom, scratch, model."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from agent_core.commands.base import Command
from agent_core.protocol.messages import ErrorMessage, ResponseMessage, ToolProgressMessage

logger = logging.getLogger(__name__)


class Lint(Command):
    name = "lint"
    args = ""
    description = "Lint wiki articles"
    requires = ("wiki",)

    async def run(self, raw_args: str, ctx) -> AsyncIterator:
        issues = ctx.agent.wiki.lint()
        if not issues:
            yield ResponseMessage(text="Vault is clean -- no issues found.", command="lint")
        else:
            lines = [f"Found {len(issues)} issue(s):\n"]
            for issue in issues:
                lines.append(f"- **{issue['path']}**: {issue['issue']}")
            yield ResponseMessage(text="\n".join(lines), command="lint")


class Import(Command):
    name = "import"
    args = "<path>"
    description = "Import a local document into the vault"
    requires = ("config", "wiki", "converter", "batch_inference", "approval_registry")

    async def run(self, raw_args: str, ctx) -> AsyncIterator:
        import fitz  # pymupdf
        from agent_core.utils.chunker import chunk_markdown
        from agent_core.utils.converter import ConversionError
        from agent_core.utils.frontmatter import serialize_frontmatter
        from datetime import datetime, timezone

        from pal.archive import archive_raw_files
        from pal.pdf_structure import detect_chapters, extract_chapters, slugify
        from pal.protocol import BatchFallbackProposal
        from agent_core.protocol import encode_message

        writer = ctx.writer
        approval_registry = ctx.agent.approval_registry

        # Per-turn proposal emitter wired to ctx.writer.
        def proposal_emitter(proposal_msg) -> None:
            writer.write(encode_message(proposal_msg))
            drain_task = asyncio.create_task(writer.drain())

            def _log_drain_failure(task: asyncio.Task) -> None:
                exc = task.exception()
                if exc is not None:
                    logger.warning("proposal drain failed: %s", exc)
            drain_task.add_done_callback(_log_drain_failure)

        file_path = raw_args.strip()
        if not file_path:
            yield ErrorMessage(error="Usage: /import <path-in-raw/>")
            return

        if not file_path.startswith("raw/"):
            yield ErrorMessage(error=f"Files must be in raw/ directory: {file_path}")
            return

        if ".." in file_path.split("/") or file_path.startswith("/"):
            yield ErrorMessage(error=f"Invalid path: {file_path}")
            return

        full_path = ctx.agent.config.vault_path / file_path
        if not full_path.exists():
            yield ErrorMessage(error=f"File not found: {file_path}")
            return

        try:
            resolved = full_path.resolve()
            vault_resolved = ctx.agent.config.vault_path.resolve()
            if not str(resolved).startswith(str(vault_resolved) + "/"):
                yield ErrorMessage(error=f"Invalid path: {file_path}")
                return
        except Exception:
            yield ErrorMessage(error=f"Invalid path: {file_path}")
            return

        ext = full_path.suffix.lower()
        is_pdf = ext == ".pdf"
        doc_slug = slugify(full_path.stem)

        target_dir = ctx.agent.config.vault_path / "raw" / "sources" / doc_slug
        if target_dir.exists() and any(target_dir.iterdir()):
            yield ErrorMessage(
                error=(
                    f"raw/sources/{doc_slug}/ already exists and is not empty; "
                    f"remove it to re-import {full_path.name}."
                ),
            )
            return
        target_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        saved_articles: list[str] = []
        detection_method: str

        if is_pdf:
            # PDF path: pymupdf4llm + structural detection.
            progress = ToolProgressMessage(
                tool="import",
                arguments={"status": f"Converting {full_path.name} (pymupdf4llm)..."},
            )
            writer.write(encode_message(progress))
            await writer.drain()

            try:
                loop = asyncio.get_running_loop()
                doc = await loop.run_in_executor(None, fitz.open, str(full_path))
            except Exception as exc:
                yield ErrorMessage(error=f"PDF open failed: {exc}")
                return

            try:
                total_pages = len(doc)

                progress = ToolProgressMessage(
                    tool="import",
                    arguments={"status": "Detecting chapters..."},
                )
                writer.write(encode_message(progress))
                await writer.drain()

                from agent_core.inference import BatchUnavailableError
                from pal.pdf_structure import DetectionResult

                effective_inference = (
                    ctx.agent.batch_inference
                    if ctx.agent.batch_inference is not None
                    else ctx.agent.inference
                )
                try:
                    detection = await detect_chapters(doc, inference=effective_inference)
                except BatchUnavailableError:
                    if approval_registry is None:
                        # No approval deps wired; fall through to single-file.
                        detection = DetectionResult(method="single-file", boundaries=[])
                    else:
                        pid = approval_registry.create_proposal(
                            kind="batch_fallback",
                            rationale="batch backend unavailable for LLM-TOC",
                            caller="llm_toc",
                            context=f"detecting chapters for {full_path.name}",
                        )
                        proposal_msg = BatchFallbackProposal(
                            proposal_id=pid,
                            caller="llm_toc",
                            context=f"detecting chapters for {full_path.name}",
                            original_request={},
                        )
                        proposal_emitter(proposal_msg)
                        proposal = approval_registry.get(pid)
                        await proposal.event.wait()
                        if proposal.status == "declined":
                            detection = DetectionResult(method="single-file", boundaries=[])
                        elif proposal.approval_choice == "retry":
                            try:
                                detection = await detect_chapters(
                                    doc, inference=ctx.agent.batch_inference,
                                )
                            except BatchUnavailableError:
                                detection = DetectionResult(
                                    method="single-file", boundaries=[],
                                )
                        elif proposal.approval_choice == "main":
                            detection = await detect_chapters(
                                doc, inference=ctx.agent.inference,
                            )
                        else:
                            detection = DetectionResult(method="single-file", boundaries=[])
                detection_method = detection.method

                if detection.method == "single-file":
                    progress = ToolProgressMessage(
                        tool="import",
                        arguments={"status": "No chapters detected; writing single file..."},
                    )
                    writer.write(encode_message(progress))
                    await writer.drain()

                    full_markdown = await loop.run_in_executor(
                        None,
                        lambda: __import__("pymupdf4llm").to_markdown(str(full_path)),
                    )
                    article_path_rel = f"raw/sources/{doc_slug}/full.md"
                    article_full = target_dir / "full.md"
                    meta = {
                        "title": full_path.stem,
                        "source_file": file_path,
                        "source_type": "pdf",
                        "section_number": 1,
                        "detection_method": detection_method,
                        "imported": now,
                    }
                    article_full.write_text(
                        serialize_frontmatter(meta, full_markdown.strip() + "\n"),
                    )
                    saved_articles.append(article_path_rel)
                else:
                    chapters = await loop.run_in_executor(
                        None,
                        extract_chapters,
                        str(full_path),
                        detection.boundaries,
                        total_pages,
                    )
                    for i, ch in enumerate(chapters, start=1):
                        progress = ToolProgressMessage(
                            tool="import",
                            arguments={
                                "status": f"Writing chapter {i} of {len(chapters)}: {ch.title}",
                            },
                        )
                        writer.write(encode_message(progress))
                        await writer.drain()

                        section_slug = slugify(ch.title)
                        filename = f"{i:02d}-{section_slug}.md"
                        article_path_rel = f"raw/sources/{doc_slug}/{filename}"
                        article_full = target_dir / filename
                        meta = {
                            "title": ch.title,
                            "source_file": file_path,
                            "source_type": "pdf",
                            "section_number": i,
                            "section_range": f"p.{ch.start_page + 1}-p.{ch.end_page + 1}",
                            "detection_method": detection_method,
                            "imported": now,
                        }
                        article_full.write_text(
                            serialize_frontmatter(meta, ch.markdown.strip() + "\n"),
                        )
                        saved_articles.append(article_path_rel)
            finally:
                doc.close()
        else:
            # Non-PDF path: existing MarkItDown + chunk_markdown flow, re-homed to raw/sources/.
            progress = ToolProgressMessage(
                tool="import",
                arguments={"status": f"Converting {full_path.name}..."},
            )
            writer.write(encode_message(progress))
            await writer.drain()

            try:
                loop = asyncio.get_running_loop()
                convert_result = await loop.run_in_executor(
                    None, ctx.agent.converter.convert, full_path,
                )
            except ConversionError as exc:
                yield ErrorMessage(error=f"Conversion failed: {exc}")
                return

            chunks = chunk_markdown(convert_result.text, fallback_title=convert_result.title)
            if not chunks:
                yield ErrorMessage(error="Conversion produced no content.")
                return

            detection_method = "headings"
            source_type = ext.lstrip(".")

            for i, chunk in enumerate(chunks, start=1):
                section_slug = slugify(chunk.title)
                filename = f"{i:02d}-{section_slug}.md"
                article_path_rel = f"raw/sources/{doc_slug}/{filename}"
                article_full = target_dir / filename
                meta = {
                    "title": chunk.title,
                    "source_file": file_path,
                    "source_type": source_type,
                    "section_number": i,
                    "detection_method": detection_method,
                    "imported": now,
                }
                article_full.write_text(
                    serialize_frontmatter(meta, chunk.body.strip() + "\n"),
                )
                saved_articles.append(article_path_rel)

        # Commit and reindex.
        ctx.agent.wiki.git_init()
        ctx.agent.wiki.git_commit(f"import: {full_path.stem} ({len(saved_articles)} sections)")

        absolute_paths = [
            str((ctx.agent.config.vault_path / rel).resolve())
            for rel in saved_articles
        ]
        await _trigger_reindex_for_paths(ctx.agent, absolute_paths)

        # Archive source.
        progress = ToolProgressMessage(
            tool="import",
            arguments={"status": "Archiving source..."},
        )
        writer.write(encode_message(progress))
        await writer.drain()
        archive_raw_files(ctx.agent.config.vault_path, raw_path=file_path)
        ctx.agent.wiki.git_commit(f"archive: {full_path.stem}")

        # Build detection report.
        lines = [
            f"Imported {len(saved_articles)} section(s) from {full_path.name} "
            f"(detection: {detection_method}):"
        ]
        for rel in saved_articles:
            lines.append(f"- {rel}")

        yield ResponseMessage(text="\n".join(lines), command="import")


class Learn(Command):
    name = "learn"
    args = ""
    description = "Extract learnings from conversation"
    requires = ("inference", "learning", "wiki")

    async def run(self, raw_args: str, ctx) -> AsyncIterator:
        conv = ctx.conversation
        messages = conv.messages
        if not messages:
            yield ErrorMessage(error="No conversation history to learn from.")
            return

        conv_text = "\n".join(
            f"{'User' if m['role'] == 'user' else 'PAL'}: {m['content']}"
            for m in messages
        )

        prompt = (
            "Review this conversation and extract actionable lessons or insights. "
            "Each lesson should be a concise, reusable principle. "
            "Format each lesson as: ## <title>\\n<body>\\n\\n "
            "Extract 1-3 lessons. If the conversation has no useful lessons, "
            "respond with exactly: NONE\n\n"
            f"Conversation:\n{conv_text}"
        )

        pb = ctx.agent.prompt_builder
        from pal.prompts.system import PAL_BASE_PROMPT
        base_prompt = "\n\n".join(filter(None, [
            PAL_BASE_PROMPT,
            pb.render_profile(),
            pb.render_wisdom(),
        ]))
        api_messages = [
            {"role": "system", "content": base_prompt},
            {"role": "user", "content": prompt},
        ]

        try:
            completion = await ctx.agent.inference.complete(api_messages, reasoning="off")
            result = completion.content or ""
        except Exception as exc:
            logger.exception("Learn inference failed: %s", exc)
            yield ErrorMessage(error=f"Learn failed: {exc}")
            return

        if result.strip() == "NONE":
            yield ResponseMessage(
                text="No actionable lessons found in this conversation.",
                command="learn",
            )
            return

        import re
        sections = re.split(r"^## ", result, flags=re.MULTILINE)
        added = []
        for section in sections:
            section = section.strip()
            if not section:
                continue
            lines = section.split("\n", 1)
            title = lines[0].strip()
            body = lines[1].strip() if len(lines) > 1 else title
            slug = ctx.agent.learning.add(title=title, body=body, source="conversation")
            added.append(slug)

        if not added:
            resp = ResponseMessage(
                text="Could not parse lessons from model output.",
                command="learn",
            )
        else:
            lines_out = [f"Extracted {len(added)} learning(s):\n"]
            for slug in added:
                lines_out.append(f"- {slug}")
            lines_out.append("\nUse `/learnings` to list, `/promote <slug>` to promote to wisdom.")
            resp = ResponseMessage(text="\n".join(lines_out), command="learn")

        ctx.agent.wiki.git_init()
        ctx.agent.wiki.git_commit(f"learn: extracted {len(added)} lesson(s)")

        yield resp


# ---------------------------------------------------------------------------
# PAL-specific overrides of framework builtins
# ---------------------------------------------------------------------------

class Status(Command):
    """PAL override: includes vault info, inference server, reasoning mode."""
    name = "status"
    args = ""
    description = "Show daemon status (model, vault, etc.)"
    requires = ("inference", "config", "wiki", "retrieval")

    async def run(self, raw_args: str, ctx) -> AsyncIterator:
        articles = ctx.agent.wiki.list_articles()
        reasoning_mode = ctx.agent.decide_mode(ctx.conversation)
        reasoning_label = ctx.conversation.overrides.get("reasoning") or "auto"
        yield ResponseMessage(
            text=(
                f"Model: {ctx.agent.inference.default_model}\n"
                f"Config default: {ctx.agent.config.model}\n"
                f"Reasoning: {reasoning_label} (effective: {reasoning_mode})\n"
                f"Server: {ctx.agent.inference.base_url}\n"
                f"Vault: {ctx.agent.wiki.vault_path} ({len(articles)} articles)\n"
                f"Collection: {ctx.agent.retrieval.collection_id}"
            ),
            command="status",
        )


class Profile(Command):
    """PAL override: supports /profile set <text> in addition to show."""
    name = "profile"
    args = "<q>"
    description = "Query your profile"
    requires = ("profile",)

    async def run(self, raw_args: str, ctx) -> AsyncIterator:
        args = raw_args.strip()
        if args.startswith("set "):
            body = args[4:].strip()
            if not body:
                yield ErrorMessage(error="Usage: /profile set <text>")
                return
            ctx.agent.profile.write(body)
            yield ResponseMessage(text="Profile updated.", command="profile")
            return
        # Default: show current profile
        body = ctx.agent.profile.read()
        if not body:
            yield ResponseMessage(
                text="Profile is empty. Use `/profile set <text>` to set it.",
                command="profile",
            )
        else:
            yield ResponseMessage(text=body, command="profile")


class Wisdom(Command):
    """PAL override: /wisdom add <title> | <body> (supports body via | separator)."""
    name = "wisdom"
    args = "[add/remove]"
    description = "Manage wisdom entries"
    requires = ("wisdom",)

    async def run(self, raw_args: str, ctx) -> AsyncIterator:
        args = raw_args.strip()

        if args.startswith("add "):
            rest = args[4:].strip()
            if "|" not in rest:
                yield ErrorMessage(error="Usage: /wisdom add <title> | <body>")
                return
            title, body = rest.split("|", 1)
            title = title.strip()
            body = body.strip()
            if not title or not body:
                yield ErrorMessage(error="Usage: /wisdom add <title> | <body>")
                return
            slug = ctx.agent.wisdom.add(title=title, body=body)
            yield ResponseMessage(
                text=f"Added wisdom: {slug}",
                command="wisdom",
            )
            return

        if args.startswith("remove "):
            slug = args[7:].strip()
            if not slug:
                yield ErrorMessage(error="Usage: /wisdom remove <slug>")
                return
            try:
                ctx.agent.wisdom.remove(slug)
            except FileNotFoundError:
                yield ErrorMessage(error=f"Wisdom not found: {slug}")
                return
            yield ResponseMessage(text=f"Removed wisdom: {slug}", command="wisdom")
            return

        # Default: list entries
        entries = ctx.agent.wisdom.list()
        if not entries:
            yield ResponseMessage(
                text="No wisdom entries. Use `/wisdom add <title> | <body>` to add one.",
                command="wisdom",
            )
        else:
            lines = [f"{len(entries)} wisdom entries:\n"]
            for e in entries:
                lines.append(f"- **{e['title']}** ({e['slug']})")
            yield ResponseMessage(text="\n".join(lines), command="wisdom")


class Scratch(Command):
    """PAL override: timestamped append with wiki commit callback."""
    name = "scratch"
    args = "<text>"
    description = "Append a timestamped note to this channel's scratchpad."
    requires = ("config", "wiki")

    async def run(self, raw_args: str, ctx) -> AsyncIterator:
        from agent_core.scratchpad import Scratchpad, ScratchpadTooLarge
        from datetime import datetime, timezone

        def _commit_scratchpad(path, message):
            ctx.agent.wiki.git_commit(message)

        sp = Scratchpad(
            vault_path=ctx.agent.config.vault_path,
            agent_name="pal",
            channel_id=ctx.channel_id,
            max_bytes=ctx.agent.config.scratchpad_max_bytes,
            commit_callback=_commit_scratchpad,
        )

        text = raw_args.strip()
        if not text:
            yield ResponseMessage(
                text="Usage: /scratch <text>. Appends a timestamped line to this channel's scratchpad.",
                command="scratch",
            )
            return

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        appended = f"- {ts}: {text}\n"
        try:
            sp.append(appended)
        except ScratchpadTooLarge as exc:
            yield ResponseMessage(
                text=(
                    f"Error: note would push scratchpad over {exc.max_bytes} bytes. "
                    "Prune the scratchpad (edit in Obsidian or call update_scratch) and retry."
                ),
                command="scratch",
            )
            return
        yield ResponseMessage(
            text=f"Note added ({len(appended)} bytes).",
            command="scratch",
        )


class PALModel(Command):
    """PAL-specific /model: HTTP calls to inference server, dual-slot, list/default."""
    name = "model"
    args = "[name]"
    description = "Show or switch the active model"
    requires = ("inference", "config")

    async def run(self, raw_args: str, ctx) -> AsyncIterator:
        import httpx

        writer = ctx.writer
        arg = raw_args.strip()

        if arg == "list":
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.get(f"{ctx.agent.inference.base_url}/v1/models")
                    r.raise_for_status()
                data = r.json()
                names = [m["id"] for m in data.get("data", [])]
                if names:
                    lines = ["Available models:"]
                    for i, name in enumerate(names, 1):
                        marker = " (active)" if name == ctx.agent.inference.default_model else ""
                        lines.append(f"  {i}. {name}{marker}")
                    yield ResponseMessage(text="\n".join(lines), command="model")
                else:
                    yield ResponseMessage(text="No models available.", command="model")
            except Exception as exc:
                logger.warning("Failed to list models: %s", exc)
                yield ErrorMessage(error=f"Could not reach inference server: {exc}")
            return

        if arg == "default":
            ctx.agent.inference.default_model = ctx.agent.config.model
            yield ResponseMessage(
                text=f"Model reset to config default: {ctx.agent.inference.default_model}",
                command="model",
            )
            return

        text = await ctx.agent._dispatch_model_command(arg)
        yield ResponseMessage(text=text, command="model")


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

async def _trigger_reindex_for_paths(agent, paths: list[str]) -> None:
    """Best-effort reindex trigger. Logs warnings on failure; never raises."""
    if not paths:
        return
    try:
        await agent.retrieval.trigger_reindex(paths=paths)
    except Exception as exc:
        logger.warning("daemon reindex trigger failed: %s", exc)
