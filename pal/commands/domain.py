"""PAL domain commands — wiki, search, notes, web, import, learning."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from agent_core.commands.base import Command
from agent_core.protocol.messages import ErrorMessage, ResponseMessage, ToolProgressMessage

logger = logging.getLogger(__name__)


class Read(Command):
    name = "read"
    args = "<title>"
    description = "Read a wiki article"
    requires = ("wiki",)

    async def run(self, raw_args: str, ctx) -> AsyncIterator:
        from agent_core.protocol import encode_message

        writer = ctx.writer
        path = raw_args.strip()
        if not path:
            yield ErrorMessage(error="Usage: /read <path>")
            return
        try:
            meta, body = ctx.agent.wiki.read_article(path)
            title = meta.get("title", path)
            tags = meta.get("tags", [])
            header = f"**{title}**"
            if tags:
                header += f"  tags: {', '.join(tags)}"
            yield ResponseMessage(text=f"{header}\n\n{body}", command="read")
        except FileNotFoundError:
            yield ErrorMessage(error=f"Article not found: {path}")


class Lint(Command):
    name = "lint"
    args = ""
    description = "Lint wiki articles"
    requires = ("wiki",)

    async def run(self, raw_args: str, ctx) -> AsyncIterator:
        issues = ctx.agent.wiki.lint()
        if not issues:
            yield ResponseMessage(text="Vault is clean — no issues found.", command="lint")
        else:
            lines = [f"Found {len(issues)} issue(s):\n"]
            for issue in issues:
                lines.append(f"- **{issue['path']}**: {issue['issue']}")
            yield ResponseMessage(text="\n".join(lines), command="lint")


class Note(Command):
    name = "note"
    args = "<text>"
    description = "Save a quick note"
    requires = ("wiki", "inference", "categorizer", "config", "retrieval")

    async def run(self, raw_args: str, ctx) -> AsyncIterator:
        topic = raw_args.strip()
        if not topic:
            yield ErrorMessage(error="Usage: /note <topic>")
            return

        prompt = (
            f"Write a concise wiki article about: {topic}\n\n"
            "RULES:\n"
            "- If you do not have confident, factual knowledge of this topic, "
            "respond with exactly: UNKNOWN: <one-sentence reason>\n"
            "- Do NOT guess, speculate, or fabricate facts.\n"
            "- Do NOT use placeholder text like [insert details here].\n"
            "- Only write the article if you can ground every claim in what you actually know.\n\n"
            "Format: Start with a markdown heading, then clear explanatory paragraphs. "
            "Be informative and concise."
        )

        pb = ctx.agent.prompt_builder
        from pal.prompts.system import PAL_BASE_PROMPT
        base_prompt = "\n\n".join(filter(None, [
            PAL_BASE_PROMPT,
            pb.render_profile(),
            pb.render_wisdom(),
        ]))
        messages = [
            {"role": "system", "content": base_prompt},
            {"role": "user", "content": prompt},
        ]

        try:
            result = await ctx.agent.inference.complete(messages, reasoning="off")
            body = result.content or ""
        except Exception as exc:
            logger.exception("Inference error during /note: %s", exc)
            yield ErrorMessage(error=f"Inference error: {exc}")
            return

        if body.strip().startswith("UNKNOWN:"):
            yield ResponseMessage(
                text=(
                    f"{body.strip()}\n\n"
                    "No article saved. Try `/search-web <topic>` to find sources, "
                    "then `/fetch` and `/compile` to build from them."
                ),
                command="note",
            )
            return

        slug = topic.lower().replace("_", "-").replace(" ", "-")
        slug = "".join(c for c in slug if c.isalnum() or c == "-")
        slug = slug.strip("-")
        if not slug:
            slug = "untitled"

        # Auto-categorize
        category = await ctx.agent.categorizer.categorize(
            title=topic,
            body=body,
            vault_path=ctx.agent.config.vault_path,
        )
        path = f"{category}/{slug}.md"

        ctx.agent.wiki.write_article(path=path, title=topic, body=body + "\n")
        ctx.agent.wiki.git_init()
        ctx.agent.wiki.git_commit(f"note: {topic}")

        absolute = str((ctx.agent.config.vault_path / path).resolve())
        await _trigger_reindex_for_paths(ctx.agent, [absolute])

        yield ResponseMessage(
            text=f"Created article: {path}\n\n{body}",
            command="note",
        )


class Search(Command):
    name = "search"
    args = "<q>"
    description = "Search wiki articles"
    requires = ("retrieval",)

    async def run(self, raw_args: str, ctx) -> AsyncIterator:
        query = raw_args.strip()
        if not query:
            yield ErrorMessage(error="Usage: /search <query>")
            return
        try:
            results = await ctx.agent.retrieval.search(query, limit=5)
        except Exception as exc:
            logger.exception("Search failed: %s", exc)
            yield ErrorMessage(error=f"Search failed: {exc}")
            return

        if not results:
            yield ResponseMessage(text="No results found.", command="search")
        else:
            lines = [f"Found {len(results)} result(s):\n"]
            for r in results:
                score = r.get("score", 0.0)
                summary = r.get("summary", "")
                lines.append(f"- **{r['id']}** (score: {score:.2f})")
                if summary:
                    lines.append(f"  {summary}")
            yield ResponseMessage(text="\n".join(lines), command="search")


class Get(Command):
    name = "get"
    args = "<title>"
    description = "Get article by exact title"
    requires = ("retrieval",)

    async def run(self, raw_args: str, ctx) -> AsyncIterator:
        doc_id = raw_args.strip()
        if not doc_id:
            yield ErrorMessage(error="Usage: /get <doc_id>")
            return
        try:
            doc = await ctx.agent.retrieval.get_document(doc_id)
        except FileNotFoundError:
            yield ErrorMessage(error=f"Document not found: {doc_id}")
            return
        except Exception as exc:
            logger.exception("Get document failed: %s", exc)
            yield ErrorMessage(error=f"Get failed: {exc}")
            return

        content = doc.get("content", "")
        name = doc.get("name", doc_id)
        yield ResponseMessage(
            text=f"**{name}** ({doc_id})\n\n{content}",
            command="get",
        )


class SearchWeb(Command):
    name = "search-web"
    args = "<q>"
    description = "Web search via SearxNG"
    requires = ("websearch", "allowlist")

    async def run(self, raw_args: str, ctx) -> AsyncIterator:
        query = raw_args.strip()
        if not query:
            yield ErrorMessage(error="Usage: /search-web <query>")
            return
        try:
            results = await ctx.agent.websearch.search(query)
        except Exception as exc:
            logger.exception("Web search failed: %s", exc)
            yield ErrorMessage(error=f"Web search failed: {exc}")
            return

        # Filter through allowlist
        allowed = [r for r in results if ctx.agent.allowlist.is_allowed(r.url)]

        if not allowed:
            yield ResponseMessage(
                text=(
                    "No allowlisted results. "
                    f"Edit `{ctx.agent.allowlist.allowlist_path.relative_to(ctx.agent.allowlist.vault_path)}` in the vault to add domains."
                ),
                command="search-web",
            )
        else:
            lines = [f"Found {len(allowed)} allowed result(s) (of {len(results)} total):\n"]
            for i, r in enumerate(allowed, 1):
                lines.append(f"{i}. **{r.title}**")
                lines.append(f"   {r.url}")
                if r.snippet:
                    lines.append(f"   {r.snippet}")
            lines.append("\nUse `/fetch <url>` to save a page to the vault.")
            yield ResponseMessage(text="\n".join(lines), command="search-web")


class Fetch(Command):
    name = "fetch"
    args = "<url>"
    description = "Fetch and summarize a URL"
    requires = ("fetcher", "allowlist", "config")

    async def run(self, raw_args: str, ctx) -> AsyncIterator:
        from agent_core.utils.fetcher import FetchError

        url = raw_args.strip()
        if not url:
            yield ErrorMessage(error="Usage: /fetch <url>")
            return

        if not ctx.agent.allowlist.is_allowed(url):
            yield ErrorMessage(
                error=(
                    f"URL not on allowlist: {url}\n"
                    f"Add its domain to {ctx.agent.allowlist.allowlist_path.relative_to(ctx.agent.allowlist.vault_path)} in the vault, then retry."
                )
            )
            return

        try:
            result = await ctx.agent.fetcher.fetch(url)
        except FetchError as exc:
            yield ErrorMessage(error=f"Fetch failed: {exc}")
            return
        except Exception as exc:
            logger.exception("Fetch failed: %s", exc)
            yield ErrorMessage(error=f"Fetch failed: {exc}")
            return

        # Build a slug from the URL path + hash suffix for uniqueness
        from urllib.parse import urlparse
        parsed = urlparse(url)
        path_part = (parsed.path or "/").strip("/").replace("/", "-") or parsed.hostname or "page"
        path_part = "".join(c for c in path_part if c.isalnum() or c in "-_")[:40]
        slug = f"{path_part}-{result.content_hash[:8]}"
        filename = f"{slug}.md"

        # Write to raw/web/ with frontmatter containing provenance
        raw_dir = ctx.agent.config.vault_path / "raw" / "web"
        raw_dir.mkdir(parents=True, exist_ok=True)

        from datetime import datetime, timezone
        from agent_core.utils.frontmatter import serialize_frontmatter
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        meta = {
            "source_url": url,
            "title": result.title or slug,
            "fetched_at": fetched_at,
            "content_hash": result.content_hash,
            "byte_size": result.byte_size,
            "status": "raw",
        }
        content = serialize_frontmatter(meta, result.text + "\n")
        (raw_dir / filename).write_text(content)
        logger.info("Fetched %s to %s", url, filename)

        yield ResponseMessage(
            text=(
                f"Saved to raw/web/{filename}\n"
                f"Title: {result.title or '(no title)'}\n"
                f"Size: {result.byte_size} bytes\n\n"
                "Review it in Obsidian before running /summarize (Phase 4b)."
            ),
            command="fetch",
        )


class Summarize(Command):
    name = "summarize"
    args = "<t>"
    description = "Summarize a wiki article"
    requires = ("inference", "config")

    async def run(self, raw_args: str, ctx) -> AsyncIterator:
        from pal.summarizer import summarize_raw_file

        raw_path = raw_args.strip()
        if not raw_path:
            yield ErrorMessage(error="Usage: /summarize <raw-path>")
            return

        # Path traversal guard
        if ".." in raw_path.split("/") or raw_path.startswith("/"):
            yield ErrorMessage(error=f"Invalid path: {raw_path}")
            return

        full_path = ctx.agent.config.vault_path / raw_path
        if not full_path.exists():
            yield ErrorMessage(error=f"File not found: {raw_path}")
            return

        # Confirm it's actually under the vault (resolves symlinks / .. defense)
        try:
            resolved = full_path.resolve()
            vault_resolved = ctx.agent.config.vault_path.resolve()
            if not str(resolved).startswith(str(vault_resolved) + "/"):
                yield ErrorMessage(error=f"Invalid path: {raw_path}")
                return
        except Exception:
            yield ErrorMessage(error=f"Invalid path: {raw_path}")
            return

        try:
            result = await summarize_raw_file(
                raw_path=full_path,
                vault_path=ctx.agent.config.vault_path,
                inference=ctx.agent.inference,
                max_body_chars=ctx.agent.config.max_inference_body_chars,
            )
        except Exception as exc:
            logger.exception("Summarize failed: %s", exc)
            yield ErrorMessage(error=f"Summarize failed: {exc}")
            return

        summary_path_rel = str(result.summary_path.relative_to(ctx.agent.config.vault_path))
        issue_text = ""
        if result.sanitization_issues:
            issue_text = "\n\nSanitization: " + "; ".join(result.sanitization_issues)

        yield ResponseMessage(
            text=(
                f"Saved to {summary_path_rel}\n\n"
                f"{result.summary_text}"
                f"{issue_text}"
            ),
            command="summarize",
        )


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
