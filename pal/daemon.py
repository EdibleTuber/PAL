"""PAL agent daemon — unix socket server.

Accepts connections from CLI clients, manages conversation state per connection,
dispatches chat messages to the inference server, and streams responses back.
"""
import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path

import httpx

from pal.config import Config
from pal.wiki import WikiManager
from pal.conversation import Conversation
from pal.inference import InferenceClient
from pal.retrieval import RetrievalClient
from pal.profile import ProfileManager
from pal.wisdom import WisdomManager
from pal.learning import LearningManager
from pal.prompt_builder import SystemPromptBuilder
from pal.allowlist import AllowlistManager
from pal.websearch import WebSearchClient
from pal.fetcher import URLFetcher, FetchError
from pal.converter import DocumentConverter, ConversionError
from pal.categorizer import Categorizer
from pal.compiler import Compiler
from pal.archive import archive_raw_files, cleanup_archived
from pal.summarizer import summarize_raw_file
from pal.chunker import chunk_markdown
import fitz  # pymupdf
from pal.pdf_structure import (
    detect_chapters,
    extract_chapters,
    slugify,
)
from pal.reasoning import decide_mode
from pal.researcher import Researcher, parse_topic_file
from pal.tools import ToolExecutor
from pal.approval_registry import ApprovalRegistry
from pal.article import (
    parse_article, serialize_article, append_timeline_entry,
    validate_compiled_truth, find_existing_article, Article,
)
from pal.protocol import (
    ChatMessage,
    CommandMessage,
    StreamChunkMessage,
    ResponseMessage,
    ErrorMessage,
    ToolProgressMessage,
    ResearchApprovalResponseMessage,
    Message,
    STREAM_BUFFER_LIMIT,
    encode_message,
    decode_message,
)
from pal.commands import COMMANDS

logger = logging.getLogger(__name__)


def render_help_text() -> str:
    """Render /help output from the COMMANDS registry."""
    lines = ["Available commands:"]
    max_name = max(len(f"/{c.name} {c.args}".rstrip()) for c in COMMANDS)
    for cmd in COMMANDS:
        prefix = f"/{cmd.name} {cmd.args}".rstrip()
        padded = prefix.ljust(max_name)
        lines.append(f"  {padded}  - {cmd.description}")
    return "\n".join(lines)


class Daemon:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.inference = InferenceClient(
            base_url=config.inference_url,
            model=config.model,
        )
        self._server: asyncio.AbstractServer | None = None
        self._should_exit = False
        self.wiki = WikiManager(config.vault_path)
        self.wiki.init_vault()
        self.retrieval = RetrievalClient(
            base_url=config.inference_url,
            collection_id=config.collection_id,
        )
        self.profile = ProfileManager(config.vault_path, username=config.username)
        self.wisdom = WisdomManager(config.vault_path)
        self.prompt_builder = SystemPromptBuilder(
            profile=self.profile,
            wisdom=self.wisdom,
        )
        self.learning = LearningManager(config.vault_path)
        self.allowlist = AllowlistManager(config.vault_path)
        self.allowlist.seed()
        self.websearch = WebSearchClient(
            base_url=config.searxng_url,
            timeout=config.fetch_timeout,
        )
        self.fetcher = URLFetcher(
            max_bytes=config.fetch_max_bytes,
            timeout=config.fetch_timeout,
        )
        self.converter = DocumentConverter()
        self.categorizer = Categorizer(self.inference)
        self.compiler = Compiler(
            vault_path=config.vault_path,
            wiki=self.wiki,
            inference=self.inference,
            categorizer=self.categorizer,
            prompt_builder=self.prompt_builder,
            retrieval=self.retrieval,
            max_body_chars=config.max_inference_body_chars,
        )
        from pal.reorg import Reorganizer
        self.reorganizer = Reorganizer(
            vault_path=config.vault_path,
            wiki=self.wiki,
            compiler=self.compiler,
            retrieval=self.retrieval,
        )
        from pal.consolidator import Consolidator
        self.consolidator = Consolidator(
            vault_path=config.vault_path,
            wiki=self.wiki,
            inference=self.inference,
            prompt_builder=self.prompt_builder,
            retrieval=self.retrieval,
            max_body_chars=config.max_inference_body_chars,
        )
        cleanup_archived(config.vault_path)

    async def serve(self) -> None:
        """Start listening on the unix socket."""
        sock_path = self.config.socket_path
        # Clean up stale socket
        if sock_path.exists():
            sock_path.unlink()
        sock_path.parent.mkdir(parents=True, exist_ok=True)

        # Reconcile _index.md with vault state on startup so external
        # modifications made while the daemon was down are reflected.
        self.wiki.rebuild_index()

        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=str(sock_path),
            limit=STREAM_BUFFER_LIMIT,
        )
        logger.info("Daemon listening on %s", sock_path)

        async with self._server:
            while not self._should_exit:
                await asyncio.sleep(0.1)

    def shutdown(self) -> None:
        """Signal the daemon to stop."""
        self._should_exit = True
        if self._server:
            self._server.close()

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single client connection."""
        conv = Conversation(history_depth=self.config.history_depth)
        logger.info("Client connected")

        approval_registry = ApprovalRegistry()

        def emit_proposal(msg):
            writer.write(encode_message(msg))
            drain_task = asyncio.create_task(writer.drain())
            def _log_drain_failure(task: asyncio.Task) -> None:
                exc = task.exception()
                if exc is not None:
                    logger.warning("proposal drain failed: %s", exc)
            drain_task.add_done_callback(_log_drain_failure)

        def emit_progress(status: str) -> None:
            progress = ToolProgressMessage(
                tool="research_topic",
                arguments={"status": status},
            )
            writer.write(encode_message(progress))
            drain_task = asyncio.create_task(writer.drain())
            def _log_drain_failure(task: asyncio.Task) -> None:
                exc = task.exception()
                if exc is not None:
                    logger.warning("progress drain failed: %s", exc)
            drain_task.add_done_callback(_log_drain_failure)

        researcher = Researcher(
            websearch=self.websearch,
            fetcher=self.fetcher,
            inference=self.inference,
            vault_path=self.config.vault_path,
            on_progress=emit_progress,
            max_body_chars=self.config.max_inference_body_chars,
        )

        from pal.learning_scanner import LearningScanner, extract_candidate

        async def _scanner_extractor(recent_turns, trigger_message):
            async def call(prompt: str) -> str:
                result = await self.inference.complete(
                    messages=[{"role": "user", "content": prompt}],
                    tools=None,
                )
                if result.type != "text":
                    return ""
                return result.content or ""
            return await extract_candidate(
                recent_turns=recent_turns,
                trigger_message=trigger_message,
                inference_call=call,
                timeout=15.0,
            )

        scanner = LearningScanner(
            learning_manager=self.learning,
            extractor=_scanner_extractor,
            emit=emit_proposal,
        )

        tool_executor = ToolExecutor(
            vault_path=self.config.vault_path,
            retrieval=self.retrieval,
            wiki=self.wiki,
            approval_registry=approval_registry,
            websearch=self.websearch,
            researcher=researcher,
            proposal_emitter=emit_proposal,
            compiler=self.compiler,
            reorganizer=self.reorganizer,
            consolidator=self.consolidator,
            learning=self.learning,
            wisdom=self.wisdom,
        )

        current_chat_task: asyncio.Task | None = None

        try:
            while True:
                line = await reader.readline()
                if not line:
                    break

                try:
                    msg = decode_message(line.strip())
                except ValueError as exc:
                    error = ErrorMessage(error=str(exc))
                    writer.write(encode_message(error))
                    await writer.drain()
                    continue

                if isinstance(msg, ResearchApprovalResponseMessage):
                    # Fast, synchronous registry update. Processed immediately
                    # so the tool coroutine awaiting the proposal event can
                    # proceed even while a chat turn is in flight.
                    self._route_approval_response(msg, approval_registry, scanner)
                elif isinstance(msg, ChatMessage):
                    if current_chat_task is not None and not current_chat_task.done():
                        error = ErrorMessage(
                            error="A previous turn is still being processed. Wait for it to complete."
                        )
                        writer.write(encode_message(error))
                        await writer.drain()
                        continue
                    current_chat_task = asyncio.create_task(
                        self._handle_chat(msg, conv, writer, tool_executor, scanner)
                    )
                elif isinstance(msg, CommandMessage):
                    if current_chat_task is not None and not current_chat_task.done():
                        error = ErrorMessage(
                            error="A previous turn is still being processed. Wait for it to complete."
                        )
                        writer.write(encode_message(error))
                        await writer.drain()
                        continue
                    await self._handle_command(msg, conv, writer)
                else:
                    error = ErrorMessage(error=f"Unexpected message type: {msg.type}")
                    writer.write(encode_message(error))
                    await writer.drain()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.exception("Connection error: %s", exc)
        finally:
            # Cancel any in-flight chat task on disconnect and await its cleanup.
            if current_chat_task is not None and not current_chat_task.done():
                current_chat_task.cancel()
                try:
                    await current_chat_task
                except (asyncio.CancelledError, Exception):
                    pass
            writer.close()
            await writer.wait_closed()
            logger.info("Client disconnected")

    def _route_approval_response(
        self,
        msg: ResearchApprovalResponseMessage,
        registry: ApprovalRegistry,
        scanner=None,  # LearningScanner | None
    ) -> None:
        # If the proposal_id matches a scanner pending candidate, handle
        # it as a learning candidate rather than a registry proposal.
        candidate = scanner.take_pending(msg.proposal_id) if scanner is not None else None
        if candidate is not None:
            if msg.decision == "approve":
                self.learning.add(
                    title=candidate.title,
                    body=candidate.body,
                    source="scanner",
                )
                self.wiki.git_commit(f"learn: scanner-captured {candidate.title}")
            # decline/skip: do nothing, candidate is already cleared
            return

        # Existing registry-backed routing.
        if msg.decision == "approve":
            registry.approve(msg.proposal_id)
        elif msg.decision == "decline":
            registry.decline(msg.proposal_id)
        elif msg.decision == "edit":
            if msg.summary_paths is not None:
                registry.edit(msg.proposal_id, summary_paths=msg.summary_paths)
            else:
                registry.edit(
                    msg.proposal_id,
                    new_topic=msg.new_topic or None,
                    new_depth=msg.new_depth or None,
                )

    async def _handle_chat(
        self,
        msg: ChatMessage,
        conv: Conversation,
        writer: asyncio.StreamWriter,
        tool_executor,
        scanner,  # NEW
    ) -> None:
        """Process a chat message with optional tool use.

        First call uses streaming. If the model returns tool calls instead of
        text, enters a non-streaming loop: execute tools, show progress, feed
        results back, repeat until the model returns text or the loop cap is hit.
        """
        from pal.inference import ToolCall
        from pal.tools import TOOL_DEFINITIONS

        conv.add_user(msg.text)
        mode = decide_mode(conv)
        messages = conv.get_messages_for_api(system_prompt=self.prompt_builder.build())
        max_tool_rounds = 50

        try:
            full_response = []
            tool_calls: list[ToolCall] | None = None

            if mode == "on":
                completion = await self.inference.complete(
                    messages, tools=TOOL_DEFINITIONS, reasoning=mode,
                )
                if completion.type == "text":
                    response_text = completion.content or ""
                    if completion.reasoning:
                        logger.debug("reasoning_content: %.500s", completion.reasoning)
                    conv.add_assistant(response_text)
                    done = ResponseMessage(
                        text=response_text,
                        reasoning=completion.reasoning or "",
                    )
                    writer.write(encode_message(done))
                    await writer.drain()
                    # Proactive learning scan (fire-and-forget).
                    recent_turns = conv.get_messages_for_api(system_prompt="")[-6:]
                    asyncio.create_task(scanner.maybe_scan(
                        recent_turns=recent_turns,
                        latest_user_message=msg.text,
                    ))
                    return
                tool_calls = completion.tool_calls
            else:
                async for item in self.inference.stream(
                    messages, tools=TOOL_DEFINITIONS, reasoning=mode,
                ):
                    if isinstance(item, list):
                        tool_calls = item
                        break
                    else:
                        chunk = StreamChunkMessage(token=item)
                        writer.write(encode_message(chunk))
                        await writer.drain()
                        full_response.append(item)

                if tool_calls is None:
                    response_text = "".join(full_response)
                    conv.add_assistant(response_text)
                    done = ResponseMessage(text=response_text)
                    writer.write(encode_message(done))
                    await writer.drain()
                    # Proactive learning scan (fire-and-forget).
                    recent_turns = conv.get_messages_for_api(system_prompt="")[-6:]
                    asyncio.create_task(scanner.maybe_scan(
                        recent_turns=recent_turns,
                        latest_user_message=msg.text,
                    ))
                    return

            # Tool-use loop
            for _round in range(max_tool_rounds):
                raw_calls = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in tool_calls
                ]
                conv.add_assistant_tool_calls(raw_calls)

                for tc in tool_calls:
                    progress = ToolProgressMessage(tool=tc.name, arguments=tc.arguments)
                    writer.write(encode_message(progress))
                    await writer.drain()

                    result = await tool_executor.run_async(tc.name, tc.arguments)
                    conv.add_tool_result(tc.id, result)

                messages = conv.get_messages_for_api(
                    system_prompt=self.prompt_builder.build()
                )
                completion = await self.inference.complete(messages, tools=TOOL_DEFINITIONS, reasoning=mode)

                if completion.type == "text":
                    response_text = completion.content or ""
                    if completion.reasoning:
                        logger.debug("reasoning_content: %.500s", completion.reasoning)
                    conv.add_assistant(response_text)
                    done = ResponseMessage(
                        text=response_text,
                        reasoning=completion.reasoning or "",
                    )
                    writer.write(encode_message(done))
                    await writer.drain()
                    # Proactive learning scan (fire-and-forget).
                    recent_turns = conv.get_messages_for_api(system_prompt="")[-6:]
                    asyncio.create_task(scanner.maybe_scan(
                        recent_turns=recent_turns,
                        latest_user_message=msg.text,
                    ))
                    return

                tool_calls = completion.tool_calls

            # Hit the loop cap
            cap_msg = "I've reached the limit of tool calls for this turn. Here's what I found so far."
            conv.add_assistant(cap_msg)
            done = ResponseMessage(text=cap_msg)
            writer.write(encode_message(done))
            await writer.drain()
            # Proactive learning scan (fire-and-forget).
            recent_turns = conv.get_messages_for_api(system_prompt="")[-6:]
            asyncio.create_task(scanner.maybe_scan(
                recent_turns=recent_turns,
                latest_user_message=msg.text,
            ))

        except Exception as exc:
            logger.exception("Chat error: %s", exc)
            error = ErrorMessage(error=f"Chat error: {exc}")
            writer.write(encode_message(error))
            await writer.drain()

    async def _handle_command(
        self,
        msg: CommandMessage,
        conv: Conversation,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a slash command."""
        if msg.name == "help":
            resp = ResponseMessage(
                text=render_help_text(),
                command="help",
            )
            writer.write(encode_message(resp))
            await writer.drain()
        elif msg.name in ("quit", "exit"):
            resp = ResponseMessage(text="Goodbye.", command="quit")
            writer.write(encode_message(resp))
            await writer.drain()
        elif msg.name == "status":
            articles = self.wiki.list_articles()
            reasoning_mode = decide_mode(conv)
            reasoning_label = conv.reasoning_override or "auto"
            resp = ResponseMessage(
                text=(
                    f"Model: {self.inference.default_model}\n"
                    f"Config default: {self.config.model}\n"
                    f"Reasoning: {reasoning_label} (effective: {reasoning_mode})\n"
                    f"Server: {self.inference.base_url}\n"
                    f"Vault: {self.wiki.vault_path} ({len(articles)} articles)\n"
                    f"Collection: {self.retrieval.collection_id}"
                ),
                command="status",
            )
            writer.write(encode_message(resp))
            await writer.drain()
        elif msg.name == "read":
            await self._handle_read(msg.args, writer)
        elif msg.name == "lint":
            await self._handle_lint(writer)
        elif msg.name == "note":
            await self._handle_note(msg.args, writer)
        elif msg.name == "search":
            await self._handle_search(msg.args, writer)
        elif msg.name == "get":
            await self._handle_get(msg.args, writer)
        elif msg.name == "profile":
            await self._handle_profile(msg.args, writer)
        elif msg.name == "wisdom":
            await self._handle_wisdom(msg.args, writer)
        elif msg.name == "search-web":
            await self._handle_search_web(msg.args, writer)
        elif msg.name == "fetch":
            await self._handle_fetch(msg.args, writer)
        elif msg.name == "summarize":
            await self._handle_summarize(msg.args, writer)
        elif msg.name == "compile":
            await self._handle_compile(msg.args, writer)
        elif msg.name == "compile-batch":
            await self._handle_compile_batch(writer)
        elif msg.name == "import":
            await self._handle_import(msg.args, writer)
        elif msg.name == "learn":
            await self._handle_learn(conv, writer)
        elif msg.name == "learnings":
            await self._handle_learnings(writer)
        elif msg.name == "promote":
            await self._handle_promote(msg.args, writer)
        elif msg.name == "rate":
            await self._handle_rate(msg.args, writer)
        elif msg.name == "model":
            await self._handle_model(msg.args, writer)
        elif msg.name == "think":
            await self._handle_think(msg.args, conv, writer)
        elif msg.name == "research":
            await self._handle_research(msg.args, writer)
        else:
            error = ErrorMessage(error=f"Unknown command: /{msg.name}")
            writer.write(encode_message(error))
            await writer.drain()

    async def _handle_read(self, path: str, writer: asyncio.StreamWriter) -> None:
        """Handle /read <path> — return article content."""
        path = path.strip()
        if not path:
            error = ErrorMessage(error="Usage: /read <path>")
            writer.write(encode_message(error))
            await writer.drain()
            return
        try:
            meta, body = self.wiki.read_article(path)
            title = meta.get("title", path)
            tags = meta.get("tags", [])
            header = f"**{title}**"
            if tags:
                header += f"  tags: {', '.join(tags)}"
            resp = ResponseMessage(text=f"{header}\n\n{body}", command="read")
            writer.write(encode_message(resp))
            await writer.drain()
        except FileNotFoundError:
            error = ErrorMessage(error=f"Article not found: {path}")
            writer.write(encode_message(error))
            await writer.drain()

    async def _handle_lint(self, writer: asyncio.StreamWriter) -> None:
        """Handle /lint — run vault health check."""
        issues = self.wiki.lint()
        if not issues:
            resp = ResponseMessage(text="Vault is clean — no issues found.", command="lint")
        else:
            lines = [f"Found {len(issues)} issue(s):\n"]
            for issue in issues:
                lines.append(f"- **{issue['path']}**: {issue['issue']}")
            resp = ResponseMessage(text="\n".join(lines), command="lint")
        writer.write(encode_message(resp))
        await writer.drain()

    async def _trigger_reindex_for_paths(self, paths: list[str]) -> None:
        """Best-effort reindex trigger for direct daemon writes (slash commands).
        Logs warnings on failure; never raises."""
        if not paths:
            return
        try:
            await self.retrieval.trigger_reindex(paths=paths)
        except Exception as exc:
            logger.warning("daemon reindex trigger failed: %s", exc)

    async def _handle_note(
        self,
        topic: str,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle /note <topic> — create or update a wiki article."""
        topic = topic.strip()
        if not topic:
            error = ErrorMessage(error="Usage: /note <topic>")
            writer.write(encode_message(error))
            await writer.drain()
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

        messages = [
            {"role": "system", "content": self.prompt_builder.build()},
            {"role": "user", "content": prompt},
        ]

        try:
            result = await self.inference.complete(messages, reasoning="off")
            body = result.content or ""
        except Exception as exc:
            logger.exception("Inference error during /note: %s", exc)
            error = ErrorMessage(error=f"Inference error: {exc}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        if body.strip().startswith("UNKNOWN:"):
            resp = ResponseMessage(
                text=(
                    f"{body.strip()}\n\n"
                    "No article saved. Try `/search-web <topic>` to find sources, "
                    "then `/fetch` and `/compile` to build from them."
                ),
                command="note",
            )
            writer.write(encode_message(resp))
            await writer.drain()
            return

        slug = topic.lower().replace("_", "-").replace(" ", "-")
        slug = "".join(c for c in slug if c.isalnum() or c == "-")
        slug = slug.strip("-")
        if not slug:
            slug = "untitled"

        # Auto-categorize
        category = await self.categorizer.categorize(
            title=topic,
            body=body,
            vault_path=self.config.vault_path,
        )
        path = f"{category}/{slug}.md"

        self.wiki.write_article(path=path, title=topic, body=body + "\n")
        self.wiki.git_init()
        self.wiki.git_commit(f"note: {topic}")

        absolute = str((self.config.vault_path / path).resolve())
        await self._trigger_reindex_for_paths([absolute])

        resp = ResponseMessage(
            text=f"Created article: {path}\n\n{body}",
            command="note",
        )
        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_search(self, query: str, writer: asyncio.StreamWriter) -> None:
        """Handle /search <query> — semantic search over the vault collection."""
        query = query.strip()
        if not query:
            error = ErrorMessage(error="Usage: /search <query>")
            writer.write(encode_message(error))
            await writer.drain()
            return
        try:
            results = await self.retrieval.search(query, limit=5)
        except Exception as exc:
            logger.exception("Search failed: %s", exc)
            error = ErrorMessage(error=f"Search failed: {exc}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        if not results:
            resp = ResponseMessage(text="No results found.", command="search")
        else:
            lines = [f"Found {len(results)} result(s):\n"]
            for r in results:
                score = r.get("score", 0.0)
                summary = r.get("summary", "")
                lines.append(f"- **{r['id']}** (score: {score:.2f})")
                if summary:
                    lines.append(f"  {summary}")
            resp = ResponseMessage(text="\n".join(lines), command="search")
        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_get(self, doc_id: str, writer: asyncio.StreamWriter) -> None:
        """Handle /get <doc_id> — fetch full document content."""
        doc_id = doc_id.strip()
        if not doc_id:
            error = ErrorMessage(error="Usage: /get <doc_id>")
            writer.write(encode_message(error))
            await writer.drain()
            return
        try:
            doc = await self.retrieval.get_document(doc_id)
        except FileNotFoundError:
            error = ErrorMessage(error=f"Document not found: {doc_id}")
            writer.write(encode_message(error))
            await writer.drain()
            return
        except Exception as exc:
            logger.exception("Get document failed: %s", exc)
            error = ErrorMessage(error=f"Get failed: {exc}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        content = doc.get("content", "")
        name = doc.get("name", doc_id)
        resp = ResponseMessage(
            text=f"**{name}** ({doc_id})\n\n{content}",
            command="get",
        )
        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_wisdom(self, args: str, writer: asyncio.StreamWriter) -> None:
        """Handle /wisdom [add <title> | <body>] [remove <slug>] — manage wisdom.

        Usage:
          /wisdom                          — list all wisdom entries
          /wisdom add <title> | <body>     — add a new entry
          /wisdom remove <slug>            — remove an entry by slug
        """
        args = args.strip()

        if args.startswith("add "):
            rest = args[4:].strip()
            if "|" not in rest:
                error = ErrorMessage(error="Usage: /wisdom add <title> | <body>")
                writer.write(encode_message(error))
                await writer.drain()
                return
            title, body = rest.split("|", 1)
            title = title.strip()
            body = body.strip()
            if not title or not body:
                error = ErrorMessage(error="Usage: /wisdom add <title> | <body>")
                writer.write(encode_message(error))
                await writer.drain()
                return
            slug = self.wisdom.add(title=title, body=body)
            resp = ResponseMessage(
                text=f"Added wisdom: {slug}",
                command="wisdom",
            )
            writer.write(encode_message(resp))
            await writer.drain()
            return

        if args.startswith("remove "):
            slug = args[7:].strip()
            if not slug:
                error = ErrorMessage(error="Usage: /wisdom remove <slug>")
                writer.write(encode_message(error))
                await writer.drain()
                return
            try:
                self.wisdom.remove(slug)
            except FileNotFoundError:
                error = ErrorMessage(error=f"Wisdom not found: {slug}")
                writer.write(encode_message(error))
                await writer.drain()
                return
            resp = ResponseMessage(text=f"Removed wisdom: {slug}", command="wisdom")
            writer.write(encode_message(resp))
            await writer.drain()
            return

        # Default: list entries
        entries = self.wisdom.list()
        if not entries:
            resp = ResponseMessage(
                text="No wisdom entries. Use `/wisdom add <title> | <body>` to add one.",
                command="wisdom",
            )
        else:
            lines = [f"{len(entries)} wisdom entries:\n"]
            for e in entries:
                lines.append(f"- **{e['title']}** ({e['slug']})")
            resp = ResponseMessage(text="\n".join(lines), command="wisdom")
        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_search_web(self, query: str, writer: asyncio.StreamWriter) -> None:
        """Handle /search-web <query> — SearxNG query, return allowlisted results."""
        query = query.strip()
        if not query:
            error = ErrorMessage(error="Usage: /search-web <query>")
            writer.write(encode_message(error))
            await writer.drain()
            return
        try:
            results = await self.websearch.search(query)
        except Exception as exc:
            logger.exception("Web search failed: %s", exc)
            error = ErrorMessage(error=f"Web search failed: {exc}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        # Filter through allowlist
        allowed = [r for r in results if self.allowlist.is_allowed(r.url)]

        if not allowed:
            resp = ResponseMessage(
                text=(
                    "No allowlisted results. "
                    "Edit `_config/allowlist.md` in the vault to add domains."
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
            resp = ResponseMessage(text="\n".join(lines), command="search-web")

        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_fetch(self, url: str, writer: asyncio.StreamWriter) -> None:
        """Handle /fetch <url> — download URL content into raw/web/ (quarantine)."""
        url = url.strip()
        if not url:
            error = ErrorMessage(error="Usage: /fetch <url>")
            writer.write(encode_message(error))
            await writer.drain()
            return

        if not self.allowlist.is_allowed(url):
            error = ErrorMessage(
                error=(
                    f"URL not on allowlist: {url}\n"
                    "Add its domain to _config/allowlist.md in the vault, then retry."
                )
            )
            writer.write(encode_message(error))
            await writer.drain()
            return

        try:
            result = await self.fetcher.fetch(url)
        except FetchError as exc:
            error = ErrorMessage(error=f"Fetch failed: {exc}")
            writer.write(encode_message(error))
            await writer.drain()
            return
        except Exception as exc:
            logger.exception("Fetch failed: %s", exc)
            error = ErrorMessage(error=f"Fetch failed: {exc}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        # Build a slug from the URL path + hash suffix for uniqueness
        from urllib.parse import urlparse
        parsed = urlparse(url)
        path_part = (parsed.path or "/").strip("/").replace("/", "-") or parsed.hostname or "page"
        path_part = "".join(c for c in path_part if c.isalnum() or c in "-_")[:40]
        slug = f"{path_part}-{result.content_hash[:8]}"
        filename = f"{slug}.md"

        # Write to raw/web/ with frontmatter containing provenance
        raw_dir = self.config.vault_path / "raw" / "web"
        raw_dir.mkdir(parents=True, exist_ok=True)

        from datetime import datetime, timezone
        from pal.frontmatter import serialize_frontmatter
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

        resp = ResponseMessage(
            text=(
                f"Saved to raw/web/{filename}\n"
                f"Title: {result.title or '(no title)'}\n"
                f"Size: {result.byte_size} bytes\n\n"
                "Review it in Obsidian before running /summarize (Phase 4b)."
            ),
            command="fetch",
        )
        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_summarize(self, raw_path: str, writer: asyncio.StreamWriter) -> None:
        """Handle /summarize <raw-path> — sanitize + boundary-wrap + summarize."""
        raw_path = raw_path.strip()
        if not raw_path:
            error = ErrorMessage(error="Usage: /summarize <raw-path>")
            writer.write(encode_message(error))
            await writer.drain()
            return

        # Path traversal guard
        if ".." in raw_path.split("/") or raw_path.startswith("/"):
            error = ErrorMessage(error=f"Invalid path: {raw_path}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        full_path = self.config.vault_path / raw_path
        if not full_path.exists():
            error = ErrorMessage(error=f"File not found: {raw_path}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        # Confirm it's actually under the vault (resolves symlinks / .. defense)
        try:
            resolved = full_path.resolve()
            vault_resolved = self.config.vault_path.resolve()
            if not str(resolved).startswith(str(vault_resolved) + "/"):
                error = ErrorMessage(error=f"Invalid path: {raw_path}")
                writer.write(encode_message(error))
                await writer.drain()
                return
        except Exception:
            error = ErrorMessage(error=f"Invalid path: {raw_path}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        try:
            result = await summarize_raw_file(
                raw_path=full_path,
                vault_path=self.config.vault_path,
                inference=self.inference,
                max_body_chars=self.config.max_inference_body_chars,
            )
        except Exception as exc:
            logger.exception("Summarize failed: %s", exc)
            error = ErrorMessage(error=f"Summarize failed: {exc}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        summary_path_rel = str(result.summary_path.relative_to(self.config.vault_path))
        issue_text = ""
        if result.sanitization_issues:
            issue_text = "\n\nSanitization: " + "; ".join(result.sanitization_issues)

        resp = ResponseMessage(
            text=(
                f"Saved to {summary_path_rel}\n\n"
                f"{result.summary_text}"
                f"{issue_text}"
            ),
            command="summarize",
        )
        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_compile(self, summary_path: str, writer: asyncio.StreamWriter) -> None:
        """Handle /compile <summary-path> — build a grounded wiki article from a summary."""
        summary_path = summary_path.strip()
        if not summary_path:
            error = ErrorMessage(error="Usage: /compile <summary-path>")
            writer.write(encode_message(error))
            await writer.drain()
            return

        outcome = await self.compiler.compile_one(summary_path)

        if outcome["status"] in ("invalid_path", "not_found", "error"):
            error = ErrorMessage(error=outcome["reason"])
            writer.write(encode_message(error))
            await writer.drain()
            return

        if outcome["status"] == "insufficient":
            resp = ResponseMessage(
                text=(
                    f"{outcome['reason']}\n\n"
                    "No article saved. The source summary may need more detail."
                ),
                command="compile",
            )
            writer.write(encode_message(resp))
            await writer.drain()
            return

        verb = "Updated" if outcome["status"] == "merged" else "Saved to"
        resp = ResponseMessage(
            text=(
                f"{verb} {outcome['article_path_rel']}\n\n"
                f"{outcome['compiled_truth']}"
            ),
            command="compile",
        )
        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_compile_batch(self, writer: asyncio.StreamWriter) -> None:
        """Handle /compile-batch — compile all summaries in raw/summaries/."""
        summaries_dir = self.config.vault_path / "raw" / "summaries"
        if not summaries_dir.exists():
            error = ErrorMessage(error=f"No summaries directory at {summaries_dir}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        summary_files = sorted(summaries_dir.glob("*.md"))
        # Filter out .dirty backups
        summary_files = [
            p for p in summary_files
            if not p.name.endswith(".dirty.md") and not p.name.endswith(".md.dirty")
        ]

        if not summary_files:
            resp = ResponseMessage(
                text="No summaries to compile in raw/summaries/",
                command="compile-batch",
            )
            writer.write(encode_message(resp))
            await writer.drain()
            return

        total = len(summary_files)
        compiled_new = 0
        merged = 0
        insufficient = 0
        errors = []

        for i, path in enumerate(summary_files, 1):
            rel = str(path.relative_to(self.config.vault_path))
            title_preview = path.stem[:50]

            progress = ToolProgressMessage(
                tool="compile-batch",
                arguments={"status": f"Compiling {i}/{total}: {title_preview}"},
            )
            writer.write(encode_message(progress))
            await writer.drain()

            try:
                outcome = await self.compiler.compile_one(rel)
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

        resp = ResponseMessage(text="\n".join(lines), command="compile-batch")
        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_import(self, file_path: str, writer: asyncio.StreamWriter) -> None:
        """Handle /import <path> - raw-first ingestion.

        Converts the source to markdown, splits into sections using
        format-appropriate detection, writes each section to
        raw/sources/<doc-slug>/NN-slug.md, archives the source, and
        triggers reindex. No categorization, no wiki-article writes.
        Promotion to wiki articles is a separate user/agent-driven step.
        """
        file_path = file_path.strip()
        if not file_path:
            error = ErrorMessage(error="Usage: /import <path-in-raw/>")
            writer.write(encode_message(error))
            await writer.drain()
            return

        if not file_path.startswith("raw/"):
            error = ErrorMessage(error=f"Files must be in raw/ directory: {file_path}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        if ".." in file_path.split("/") or file_path.startswith("/"):
            error = ErrorMessage(error=f"Invalid path: {file_path}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        full_path = self.config.vault_path / file_path
        if not full_path.exists():
            error = ErrorMessage(error=f"File not found: {file_path}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        try:
            resolved = full_path.resolve()
            vault_resolved = self.config.vault_path.resolve()
            if not str(resolved).startswith(str(vault_resolved) + "/"):
                error = ErrorMessage(error=f"Invalid path: {file_path}")
                writer.write(encode_message(error))
                await writer.drain()
                return
        except Exception:
            error = ErrorMessage(error=f"Invalid path: {file_path}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        ext = full_path.suffix.lower()
        is_pdf = ext == ".pdf"
        doc_slug = slugify(full_path.stem)

        target_dir = self.config.vault_path / "raw" / "sources" / doc_slug
        if target_dir.exists() and any(target_dir.iterdir()):
            error = ErrorMessage(
                error=(
                    f"raw/sources/{doc_slug}/ already exists and is not empty; "
                    f"remove it to re-import {full_path.name}."
                ),
            )
            writer.write(encode_message(error))
            await writer.drain()
            return
        target_dir.mkdir(parents=True, exist_ok=True)

        from datetime import datetime, timezone
        from pal.frontmatter import serialize_frontmatter
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
                error = ErrorMessage(error=f"PDF open failed: {exc}")
                writer.write(encode_message(error))
                await writer.drain()
                return

            try:
                total_pages = len(doc)

                progress = ToolProgressMessage(
                    tool="import",
                    arguments={"status": "Detecting chapters..."},
                )
                writer.write(encode_message(progress))
                await writer.drain()

                detection = await detect_chapters(doc, inference=self.inference)
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
                    None, self.converter.convert, full_path,
                )
            except ConversionError as exc:
                error = ErrorMessage(error=f"Conversion failed: {exc}")
                writer.write(encode_message(error))
                await writer.drain()
                return

            chunks = chunk_markdown(convert_result.text, fallback_title=convert_result.title)
            if not chunks:
                error = ErrorMessage(error="Conversion produced no content.")
                writer.write(encode_message(error))
                await writer.drain()
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
        self.wiki.git_init()
        self.wiki.git_commit(f"import: {full_path.stem} ({len(saved_articles)} sections)")

        absolute_paths = [
            str((self.config.vault_path / rel).resolve())
            for rel in saved_articles
        ]
        await self._trigger_reindex_for_paths(absolute_paths)

        # Archive source.
        progress = ToolProgressMessage(
            tool="import",
            arguments={"status": "Archiving source..."},
        )
        writer.write(encode_message(progress))
        await writer.drain()
        archive_raw_files(self.config.vault_path, raw_path=file_path)
        self.wiki.git_commit(f"archive: {full_path.stem}")

        # Build detection report.
        lines = [
            f"Imported {len(saved_articles)} section(s) from {full_path.name} "
            f"(detection: {detection_method}):"
        ]
        for rel in saved_articles:
            lines.append(f"- {rel}")

        resp = ResponseMessage(text="\n".join(lines), command="import")
        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_learn(
        self,
        conv: Conversation,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle /learn — extract lessons from the current conversation."""
        messages = conv.messages
        if not messages:
            error = ErrorMessage(error="No conversation history to learn from.")
            writer.write(encode_message(error))
            await writer.drain()
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

        api_messages = [
            {"role": "system", "content": self.prompt_builder.build()},
            {"role": "user", "content": prompt},
        ]

        try:
            completion = await self.inference.complete(api_messages, reasoning="off")
            result = completion.content or ""
        except Exception as exc:
            logger.exception("Learn inference failed: %s", exc)
            error = ErrorMessage(error=f"Learn failed: {exc}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        if result.strip() == "NONE":
            resp = ResponseMessage(
                text="No actionable lessons found in this conversation.",
                command="learn",
            )
            writer.write(encode_message(resp))
            await writer.drain()
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
            slug = self.learning.add(title=title, body=body, source="conversation")
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

        self.wiki.git_init()
        self.wiki.git_commit(f"learn: extracted {len(added)} lesson(s)")

        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_learnings(self, writer: asyncio.StreamWriter) -> None:
        """Handle /learnings — list all extracted learnings."""
        entries = self.learning.list()
        if not entries:
            resp = ResponseMessage(
                text="No learnings yet. Use `/learn` after a conversation to extract lessons.",
                command="learnings",
            )
        else:
            lines = [f"{len(entries)} learning(s):\n"]
            for e in entries:
                status_marker = " (promoted)" if e["status"] == "promoted" else ""
                lines.append(f"- **{e['title']}** ({e['slug']}){status_marker}")
            resp = ResponseMessage(text="\n".join(lines), command="learnings")
        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_promote(self, slug: str, writer: asyncio.StreamWriter) -> None:
        """Handle /promote <slug> — promote a learning to wisdom."""
        slug = slug.strip()
        if not slug:
            error = ErrorMessage(error="Usage: /promote <slug>")
            writer.write(encode_message(error))
            await writer.drain()
            return
        try:
            body = self.learning.get(slug)
            meta_path = self.learning.learning_dir / f"{slug}.md"
            from pal.frontmatter import parse_frontmatter
            meta, _ = parse_frontmatter(meta_path.read_text())
            title = meta.get("title", slug)
        except FileNotFoundError:
            error = ErrorMessage(error=f"Learning not found: {slug}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        self.wisdom.add(title=title, body=body)
        self.learning.mark_promoted(slug)

        self.wiki.git_init()
        self.wiki.git_commit(f"promote: {slug} → wisdom")

        resp = ResponseMessage(
            text=f"Promoted **{title}** to wisdom.",
            command="promote",
        )
        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_rate(self, args: str, writer: asyncio.StreamWriter) -> None:
        """Handle /rate <good|bad> [comment] — record session feedback."""
        args = args.strip()
        if not args:
            error = ErrorMessage(error="Usage: /rate <good|bad> [comment]")
            writer.write(encode_message(error))
            await writer.drain()
            return
        parts = args.split(None, 1)
        rating = parts[0].lower()
        comment = parts[1] if len(parts) > 1 else ""

        self.learning.add_rating(rating, comment)

        resp = ResponseMessage(
            text=f"Rated: **{rating}**" + (f" — {comment}" if comment else ""),
            command="rate",
        )
        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_profile(self, args: str, writer: asyncio.StreamWriter) -> None:
        """Handle /profile [set <text>] — show or update user profile.

        Usage:
          /profile             — show current profile
          /profile set <text>  — replace profile with <text>
        """
        args = args.strip()
        if args.startswith("set "):
            body = args[4:].strip()
            if not body:
                error = ErrorMessage(error="Usage: /profile set <text>")
                writer.write(encode_message(error))
                await writer.drain()
                return
            self.profile.write(body)
            resp = ResponseMessage(text="Profile updated.", command="profile")
            writer.write(encode_message(resp))
            await writer.drain()
            return
        # Default: show current profile
        body = self.profile.read()
        if not body:
            resp = ResponseMessage(
                text="Profile is empty. Use `/profile set <text>` to set it.",
                command="profile",
            )
        else:
            resp = ResponseMessage(text=body, command="profile")
        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_model(
        self,
        args: str,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle /model -- show or switch the active model.

        The active model is a single global setting. Changing it affects
        every inference call: chat, research, summarize, compile, etc.
        """
        arg = args.strip()

        if arg == "":
            resp = ResponseMessage(
                text=f"Model: {self.inference.default_model}",
                command="model",
            )
        elif arg == "list":
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.get(f"{self.inference.base_url}/v1/models")
                    r.raise_for_status()
                data = r.json()
                names = [m["id"] for m in data.get("data", [])]
                if names:
                    lines = ["Available models:"]
                    for i, name in enumerate(names, 1):
                        marker = " (active)" if name == self.inference.default_model else ""
                        lines.append(f"  {i}. {name}{marker}")
                    resp = ResponseMessage(text="\n".join(lines), command="model")
                else:
                    resp = ResponseMessage(text="No models available.", command="model")
            except Exception as exc:
                logger.warning("Failed to list models: %s", exc)
                error = ErrorMessage(error=f"Could not reach inference server: {exc}")
                writer.write(encode_message(error))
                await writer.drain()
                return
        elif arg == "default":
            self.inference.default_model = self.config.model
            resp = ResponseMessage(
                text=f"Model reset to config default: {self.inference.default_model}",
                command="model",
            )
        else:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.get(f"{self.inference.base_url}/v1/models")
                    r.raise_for_status()
                data = r.json()
                names = [m["id"] for m in data.get("data", [])]
            except Exception as exc:
                logger.warning("Failed to validate model: %s", exc)
                error = ErrorMessage(error=f"Could not reach inference server: {exc}")
                writer.write(encode_message(error))
                await writer.drain()
                return

            if arg not in names:
                error = ErrorMessage(
                    error=f"Model not found: {arg}. Use /model list to see available models.",
                )
                writer.write(encode_message(error))
                await writer.drain()
                return

            self.inference.default_model = arg
            resp = ResponseMessage(
                text=f"Model set to: {arg}",
                command="model",
            )

        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_research(
        self, args: str, writer: asyncio.StreamWriter
    ) -> None:
        """Handle /research - search, fetch, and summarize topics."""
        args = args.strip()
        if not args:
            error = ErrorMessage(error="Usage: /research [--verbose] [deep] <topic or path>")
            writer.write(encode_message(error))
            await writer.drain()
            return

        # Parse flags
        verbose = False
        deep = False
        parts = args.split()
        remaining = []
        for part in parts:
            if part == "--verbose":
                verbose = True
            elif part == "deep" and not remaining:
                deep = True
            else:
                remaining.append(part)
        topic_or_path = " ".join(remaining)

        if not topic_or_path:
            error = ErrorMessage(error="Usage: /research [--verbose] [deep] <topic or path>")
            writer.write(encode_message(error))
            await writer.drain()
            return

        depth = 10 if deep else 3

        # Progress callback - send ToolProgressMessage to client
        async def send_progress(msg: str) -> None:
            progress = ToolProgressMessage(tool="research", arguments={"status": msg})
            writer.write(encode_message(progress))
            await writer.drain()

        def on_progress(msg: str) -> None:
            asyncio.get_running_loop().create_task(send_progress(msg))

        researcher = Researcher(
            websearch=self.websearch,
            fetcher=self.fetcher,
            inference=self.inference,
            vault_path=self.config.vault_path,
            on_progress=on_progress,
            max_body_chars=self.config.max_inference_body_chars,
        )

        # Detect file vs topic
        candidate_path = self.config.vault_path / topic_or_path
        if candidate_path.is_file():
            topics = parse_topic_file(candidate_path)
            if not topics:
                error = ErrorMessage(error=f"No topics found in {topic_or_path}")
                writer.write(encode_message(error))
                await writer.drain()
                return
        else:
            topics = [topic_or_path]

        # Run research
        try:
            report = await researcher.research_topics(topics, depth=depth, verbose=verbose)
        except Exception as exc:
            logger.exception("Research failed: %s", exc)
            error = ErrorMessage(error=f"Research failed: {exc}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        # Format report
        from urllib.parse import urlparse
        lines = [f"Research complete: {len(report.results)} topic(s), "
                 f"{report.total_fetched} fetched, {report.total_summarized} summarized"]
        lines.append("")
        for res in report.results:
            source_count = len([s for s in res.sources if s.status == "ok"])
            lines.append(f"  {res.topic} ({source_count} source(s))")
            for src in res.sources:
                host = urlparse(src.url).hostname or src.url
                if src.status == "ok":
                    lines.append(f"    + {host} - {src.title}")
                else:
                    lines.append(f"    x {host} - {src.error or src.status}")
            lines.append("")

        if report.flagged_topics:
            for ft in report.flagged_topics:
                lines.append(f"  ! No usable results for: {ft}")
            lines.append("")

        lines.append("Summaries ready in raw/summaries/. Review and run /compile to add to wiki.")

        resp = ResponseMessage(text="\n".join(lines), command="research")
        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_think(
        self,
        args: str,
        conv: Conversation,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle /think -- control reasoning mode for this conversation."""
        arg = args.strip().lower()
        if arg == "on":
            conv.reasoning_override = "on"
            logger.info(
                "reasoning_toggle conversation_id=%s turn_idx=%d action=on last_user_message=%.200s",
                id(conv),
                len(conv.messages),
                conv.messages[-1]["content"] if conv.messages else "",
            )
            resp = ResponseMessage(text="Reasoning: on", command="think")
        elif arg == "off":
            conv.reasoning_override = "off"
            logger.info(
                "reasoning_toggle conversation_id=%s turn_idx=%d action=off last_user_message=%.200s",
                id(conv),
                len(conv.messages),
                conv.messages[-1]["content"] if conv.messages else "",
            )
            resp = ResponseMessage(text="Reasoning: off", command="think")
        elif arg == "auto":
            conv.reasoning_override = None
            logger.info(
                "reasoning_toggle conversation_id=%s turn_idx=%d action=auto last_user_message=%.200s",
                id(conv),
                len(conv.messages),
                conv.messages[-1]["content"] if conv.messages else "",
            )
            resp = ResponseMessage(text="Reasoning: auto (off by default)", command="think")
        elif arg in ("show", "hide"):
            resp = ResponseMessage(
                text=f"Reasoning display: {arg} (CLI only -- Discord reasoning display is not yet available)",
                command="think",
            )
        elif arg == "":
            mode = decide_mode(conv)
            resp = ResponseMessage(
                text=f"Reasoning mode: {conv.reasoning_override or 'auto'} (effective: {mode})",
                command="think",
            )
        else:
            resp = ResponseMessage(
                text="Usage: /think [on|off|auto|show|hide]",
                command="think",
            )
        writer.write(encode_message(resp))
        await writer.drain()
