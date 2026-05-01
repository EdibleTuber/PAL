"""PALAgent: PAL's agent_core Agent subclass.

Owns the PAL-specific infrastructure (wiki, categorizer, researcher, compiler,
tool executor, prompt builder, learning scanner) and implements the
chat/command/system-prompt extension points for the agent_core daemon.

Framework managers (profile, wisdom, learning, allowlist, approval_registry,
channels, inference, retrieval, websearch) are populated on `self` by
`agent_core.runtime.run_daemon` BEFORE `setup()` runs, so this class can
freely use them when constructing PAL-specific objects.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

from agent_core.agent import Agent, HandlerContext
from agent_core.protocol import (
    ChatMessage,
    CommandMessage,
    ErrorMessage,
    ResponseMessage,
    StreamChunkMessage,
    ToolProgressMessage,
    encode_message,
)

from pal.config import PALConfig
from pal.commands import COMMANDS
from agent_core.scratchpad import ScratchpadTooLarge

logger = logging.getLogger(__name__)


async def handle_scratch(scratchpad, text: str) -> str:
    """Append a timestamped note to the given scratchpad. Returns user-facing message."""
    from datetime import datetime, timezone
    text = text.strip()
    if not text:
        return "Usage: /scratch <text>. Appends a timestamped line to this channel's scratchpad."
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    appended = f"- {ts}: {text}\n"
    try:
        scratchpad.append(appended)
    except ScratchpadTooLarge as exc:
        return (
            f"Error: note would push scratchpad over {exc.max_bytes} bytes. "
            "Prune the scratchpad (edit in Obsidian or call update_scratch) and retry."
        )
    return f"Note added ({len(appended)} bytes)."


def render_help_text() -> str:
    """Render /help output from the COMMANDS registry."""
    lines = ["Available commands:"]
    max_name = max(len(f"/{c.name} {c.args}".rstrip()) for c in COMMANDS)
    for cmd in COMMANDS:
        prefix = f"/{cmd.name} {cmd.args}".rstrip()
        padded = prefix.ljust(max_name)
        lines.append(f"  {padded}  - {cmd.description}")
    return "\n".join(lines)


class PALAgent(Agent):
    """The PAL agent. Subclass of agent_core.agent.Agent.

    `setup()` constructs PAL-specific infrastructure (wiki, prompt builder,
    categorizer, fetcher, converter, researcher, compiler, tool executor,
    learning scanner). Per-turn objects whose construction requires the
    client `writer` (e.g. progress/proposal callbacks) are wired with no-op
    placeholders here and rebuilt or overridden per-turn in handle_chat
    (Task 16).
    """

    name = "pal"
    config: PALConfig  # type-narrows the framework attr

    def setup(self) -> None:
        """Construct PAL-specific infrastructure.

        Framework managers (profile, wisdom, learning, allowlist,
        approval_registry, channels, inference, retrieval, websearch) are
        already populated by run_daemon when this runs.
        """
        # Imports inside setup() to defer until after agent_core has
        # populated framework attrs and to avoid circular imports at
        # module load time.
        from agent_core.inference import InferenceClient
        from agent_core.learning_scanner import LearningScanner, extract_candidate
        from agent_core.utils.converter import DocumentConverter
        from agent_core.utils.fetcher import URLFetcher

        # Seed the allowlist with PAL's default trusted domains on first run.
        # Idempotent; AllowlistManager.seed() only writes if the file is missing.
        # The legacy Daemon.__init__ called this; run_daemon doesn't, so PAL
        # owns the call.
        self.allowlist.seed()

        from pal.categorizer import Categorizer
        from pal.compiler import Compiler
        from pal.consolidator import Consolidator
        from pal.prompt_builder import SystemPromptBuilder
        from pal.reorg import Reorganizer
        from pal.researcher import Researcher
        from pal.tools import ToolExecutor
        from pal.wiki import WikiManager

        config = self.config

        # Wiki: vault read/write surface. init_vault() ensures the vault
        # directory and _index.md exist; rebuild_index() reconciles
        # _index.md with on-disk articles, so external modifications made
        # while the agent was offline are reflected on startup.
        self.wiki = WikiManager(config.vault_path)
        self.wiki.init_vault()
        self.wiki.rebuild_index()

        # Optional batch inference client. The framework run_daemon only
        # constructs the primary inference client; the batch client is
        # PAL-specific and gated by config.batch_enabled.
        if config.batch_enabled:
            self.batch_inference: InferenceClient | None = InferenceClient(
                base_url=config.batch_inference_url,
                model=config.batch_model,
                is_batch=True,
            )
        else:
            self.batch_inference = None

        # Inference client used for cheap "batch" tasks (categorization,
        # learning extraction). Falls back to the primary client when no
        # batch endpoint is configured.
        effective_batch = (
            self.batch_inference if self.batch_inference is not None else self.inference
        )

        # System prompt builder: composes base prompt + profile + wisdom.
        self.prompt_builder = SystemPromptBuilder(
            profile=self.profile,
            wisdom=self.wisdom,
        )

        # Categorizer: routes raw summaries to vault categories. Uses
        # the batch client when available.
        self.categorizer = Categorizer(effective_batch)

        # URL fetch + document conversion utilities (used by researcher
        # and by some tool handlers).
        self.fetcher = URLFetcher(
            max_bytes=config.fetch_max_bytes,
            timeout=config.fetch_timeout,
        )
        self.converter = DocumentConverter()

        # Researcher: search -> fetch -> summarize pipeline. The progress
        # callback is wired per-turn in handle_chat (Task 16) because it
        # writes to the connected client. Default is None (no progress
        # emission); handle_chat rebuilds with a writer-aware callback.
        self.researcher = Researcher(
            websearch=self.websearch,
            fetcher=self.fetcher,
            inference=self.inference,
            vault_path=config.vault_path,
            on_progress=None,
            max_body_chars=config.max_inference_body_chars,
        )

        # Compiler: promotes raw summaries into vault articles.
        self.compiler = Compiler(
            vault_path=config.vault_path,
            wiki=self.wiki,
            inference=self.inference,
            categorizer=self.categorizer,
            prompt_builder=self.prompt_builder,
            retrieval=self.retrieval,
            max_body_chars=config.max_inference_body_chars,
        )

        # Reorganizer: vault file moves and merges.
        self.reorganizer = Reorganizer(
            vault_path=config.vault_path,
            wiki=self.wiki,
            compiler=self.compiler,
            retrieval=self.retrieval,
        )

        # Consolidator: fuses 2+ existing articles into a new article.
        self.consolidator = Consolidator(
            vault_path=config.vault_path,
            wiki=self.wiki,
            inference=self.inference,
            prompt_builder=self.prompt_builder,
            retrieval=self.retrieval,
            max_body_chars=config.max_inference_body_chars,
        )

        # Tool executor: dispatches LLM tool calls. The proposal emitter
        # is wired per-turn in handle_chat (Task 16); a no-op placeholder
        # is installed here so the attribute exists for code paths that
        # introspect it.
        def _noop_proposal_emitter(_msg) -> None:  # pragma: no cover
            return None

        self.tool_executor = ToolExecutor(
            vault_path=config.vault_path,
            retrieval=self.retrieval,
            wiki=self.wiki,
            approval_registry=self.approval_registry,
            websearch=self.websearch,
            researcher=self.researcher,
            proposal_emitter=_noop_proposal_emitter,
            compiler=self.compiler,
            reorganizer=self.reorganizer,
            consolidator=self.consolidator,
            learning=self.learning,
            wisdom=self.wisdom,
        )

        # Learning scanner: surfaces durable-lesson candidates after each
        # turn. The extractor calls back into inference; the emit callback
        # is a no-op placeholder and is replaced per-turn in handle_chat
        # (Task 16) so proposals stream to the connected client.
        async def _scanner_extractor(recent_turns, trigger_message):
            async def _call(prompt: str) -> str:
                result = await effective_batch.complete(
                    messages=[{"role": "user", "content": prompt}],
                    tools=None,
                )
                if result.type != "text":
                    return ""
                return result.content or ""
            return await extract_candidate(
                recent_turns=recent_turns,
                trigger_message=trigger_message,
                inference_call=_call,
                timeout=15.0,
            )

        def _noop_emit(_proposal_msg) -> None:  # pragma: no cover
            return None

        self.scanner = LearningScanner(
            learning_manager=self.learning,
            extractor=_scanner_extractor,
            emit=_noop_emit,
        )

    def _build_scratchpad(self, channel_id: str):
        """Construct the per-channel Scratchpad bound to the wiki commit hook."""
        from agent_core.scratchpad import Scratchpad

        def _commit_scratchpad(path, message):
            self.wiki.git_commit(message)

        return Scratchpad(
            vault_path=self.config.vault_path,
            agent_name="pal",
            channel_id=channel_id,
            max_bytes=self.config.scratchpad_max_bytes,
            commit_callback=_commit_scratchpad,
        )

    def system_prompt(self, ctx: HandlerContext) -> str:
        """Return PAL's system prompt for this turn, including channel scratchpad."""
        scratchpad = self._build_scratchpad(ctx.channel_id)
        scratchpad_content = scratchpad.read()
        return self.prompt_builder.build(channel_scratchpad=scratchpad_content)

    async def handle_chat(
        self, msg: ChatMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Process a chat message with optional tool use.

        First call uses streaming (when reasoning != "on"). If the model
        returns tool calls instead of text, enters a non-streaming loop:
        execute tools, show progress, feed results back, repeat until the
        model returns text or the loop cap is hit.

        Lifted from pal.daemon.Daemon._handle_chat. Phase E preserves the
        writer-passing pattern: streaming chunks and tool-progress messages
        are written directly to ctx.writer as side effects, while terminal
        messages (Response, Error) are yielded so the framework's
        _run_handler emission loop can also flush them.
        """
        from agent_core.inference import StreamEnd, ToolCall

        from pal.tools import TOOL_DEFINITIONS

        conv = ctx.conversation
        channel_id = ctx.channel_id
        writer = ctx.writer

        # Per-turn callback wiring. PALAgent.setup() created tool_executor,
        # researcher, and scanner with no-op placeholders because the writer
        # is only known per-turn. Mutate the long-lived instances now so any
        # tool/proposal/progress emission during this turn streams to the
        # connected client.
        def _emit_proposal(proposal_msg) -> None:
            writer.write(encode_message(proposal_msg))
            drain_task = asyncio.create_task(writer.drain())

            def _log_drain_failure(task: asyncio.Task) -> None:
                exc = task.exception()
                if exc is not None:
                    logger.warning("proposal drain failed: %s", exc)
            drain_task.add_done_callback(_log_drain_failure)

        def _emit_progress(status: str) -> None:
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

        self.tool_executor.proposal_emitter = _emit_proposal
        self.researcher.on_progress = _emit_progress
        self.scanner.emit = _emit_proposal

        conv.add_user(msg.text)
        mode = self.decide_mode(conv)

        scratchpad = self._build_scratchpad(channel_id)
        scratchpad_content = scratchpad.read()
        self.tool_executor.scratchpad = scratchpad

        messages = conv.get_messages_for_api(
            system_prompt=self.prompt_builder.build(channel_scratchpad=scratchpad_content),
        )
        max_tool_rounds = 50

        try:
            full_response: list[str] = []
            tool_calls: list[ToolCall] | None = None

            if mode == "on":
                completion = await self.inference.complete(
                    messages, tools=TOOL_DEFINITIONS, reasoning=mode,
                    max_tokens=4096,  # stopgap: bound runaway loops; proper fix tracked in inference safety plan
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
                    asyncio.create_task(self.scanner.maybe_scan(
                        recent_turns=recent_turns,
                        latest_user_message=msg.text,
                    ))
                    return
                tool_calls = completion.tool_calls
            else:
                async for item in self.inference.stream(
                    messages, tools=TOOL_DEFINITIONS, reasoning=mode,
                    max_tokens=4096,  # stopgap: bound runaway loops; proper fix tracked in inference safety plan
                ):
                    if isinstance(item, list):
                        tool_calls = item
                        break
                    if isinstance(item, StreamEnd):
                        # End-of-stream sentinel from agent_core 0.3.1+. Full
                        # finish_reason handling lands in the deferred safety
                        # fix; for now, treat as normal end-of-text.
                        break
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
                    asyncio.create_task(self.scanner.maybe_scan(
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

                    result = await self.tool_executor.run_async(tc.name, tc.arguments)
                    conv.add_tool_result(tc.id, result)

                # Re-read in case an update_scratch tool call modified the file.
                scratchpad_content = scratchpad.read()
                messages = conv.get_messages_for_api(
                    system_prompt=self.prompt_builder.build(channel_scratchpad=scratchpad_content),
                )
                completion = await self.inference.complete(
                    messages, tools=TOOL_DEFINITIONS, reasoning=mode,
                    max_tokens=4096,  # stopgap: bound runaway loops; proper fix tracked in inference safety plan
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
                    asyncio.create_task(self.scanner.maybe_scan(
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
            asyncio.create_task(self.scanner.maybe_scan(
                recent_turns=recent_turns,
                latest_user_message=msg.text,
            ))

        except Exception as exc:
            logger.exception("Chat error: %s", exc)
            yield ErrorMessage(error=f"Chat error: {exc}")

    async def handle_command(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Dispatch a slash command to its handler.

        Lifted from pal.daemon.Daemon._handle_command. The dispatch table
        maps command names to per-handler methods on this class. Each
        handler is an async generator and uses ctx.writer to emit progress
        and ctx.conversation / ctx.channel_id for per-turn state.
        """
        handler_map = {
            "help": self._handle_help,
            "quit": self._handle_quit,
            "exit": self._handle_quit,
            "status": self._handle_status,
            "read": self._handle_read,
            "lint": self._handle_lint,
            "note": self._handle_note,
            "search": self._handle_search,
            "get": self._handle_get,
            "profile": self._handle_profile,
            "wisdom": self._handle_wisdom,
            "search-web": self._handle_search_web,
            "fetch": self._handle_fetch,
            "summarize": self._handle_summarize,
            "compile": self._handle_compile,
            "compile-batch": self._handle_compile_batch,
            "import": self._handle_import,
            "learn": self._handle_learn,
            "learnings": self._handle_learnings,
            "promote": self._handle_promote,
            "rate": self._handle_rate,
            "model": self._handle_model,
            "think": self._handle_think,
            "research": self._handle_research,
            "scratch": self._handle_scratch,
        }
        handler = handler_map.get(msg.name)
        if handler is None:
            yield ErrorMessage(error=f"Unknown command: /{msg.name}")
            return
        async for response in handler(msg, ctx):
            yield response

    # ----- Command handlers (lifted from pal.daemon.Daemon) -----
    #
    # All command handlers share this signature:
    #   async def _handle_X(self, msg: CommandMessage, ctx: HandlerContext)
    #     -> AsyncIterator[object]
    #
    # Most handlers write directly to ctx.writer (preserving the daemon's
    # explicit-write pattern) and yield nothing. A few yield terminal
    # messages so the framework's emission loop can also flush them. Both
    # approaches are valid; see Task 16 notes.

    async def _handle_help(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle /help — show command list."""
        resp = ResponseMessage(text=render_help_text(), command="help")
        ctx.writer.write(encode_message(resp))
        await ctx.writer.drain()
        return
        yield  # pragma: no cover

    async def _handle_quit(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle /quit and /exit — say goodbye."""
        resp = ResponseMessage(text="Goodbye.", command="quit")
        ctx.writer.write(encode_message(resp))
        await ctx.writer.drain()
        return
        yield  # pragma: no cover

    async def _handle_status(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle /status — show daemon status."""
        articles = self.wiki.list_articles()
        reasoning_mode = self.decide_mode(ctx.conversation)
        reasoning_label = ctx.conversation.overrides.get("reasoning") or "auto"
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
        ctx.writer.write(encode_message(resp))
        await ctx.writer.drain()
        return
        yield  # pragma: no cover

    async def _handle_read(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle /read <path> — return article content."""
        writer = ctx.writer
        path = msg.args.strip()
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
        return
        yield  # pragma: no cover

    async def _handle_lint(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle /lint — run vault health check."""
        writer = ctx.writer
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
        return
        yield  # pragma: no cover

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
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle /note <topic> — create or update a wiki article."""
        writer = ctx.writer
        topic = msg.args.strip()
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
        return
        yield  # pragma: no cover

    async def _handle_search(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle /search <query> — semantic search over the vault collection."""
        writer = ctx.writer
        query = msg.args.strip()
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
        return
        yield  # pragma: no cover

    async def _handle_get(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle /get <doc_id> — fetch full document content."""
        writer = ctx.writer
        doc_id = msg.args.strip()
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
        return
        yield  # pragma: no cover

    async def _handle_wisdom(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle /wisdom [add <title> | <body>] [remove <slug>] — manage wisdom."""
        writer = ctx.writer
        args = msg.args.strip()

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
        return
        yield  # pragma: no cover

    async def _handle_search_web(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle /search-web <query> — SearxNG query, return allowlisted results."""
        writer = ctx.writer
        query = msg.args.strip()
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
                    f"Edit `{self.allowlist.allowlist_path.relative_to(self.allowlist.vault_path)}` in the vault to add domains."
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
        return
        yield  # pragma: no cover

    async def _handle_fetch(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle /fetch <url> — download URL content into raw/web/ (quarantine)."""
        from agent_core.utils.fetcher import FetchError

        writer = ctx.writer
        url = msg.args.strip()
        if not url:
            error = ErrorMessage(error="Usage: /fetch <url>")
            writer.write(encode_message(error))
            await writer.drain()
            return

        if not self.allowlist.is_allowed(url):
            error = ErrorMessage(
                error=(
                    f"URL not on allowlist: {url}\n"
                    f"Add its domain to {self.allowlist.allowlist_path.relative_to(self.allowlist.vault_path)} in the vault, then retry."
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
        return
        yield  # pragma: no cover

    async def _handle_summarize(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle /summarize <raw-path> — sanitize + boundary-wrap + summarize."""
        from pal.summarizer import summarize_raw_file

        writer = ctx.writer
        raw_path = msg.args.strip()
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
        return
        yield  # pragma: no cover

    async def _handle_compile(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle /compile <summary-path> — build a grounded wiki article from a summary."""
        writer = ctx.writer
        summary_path = msg.args.strip()
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
        return
        yield  # pragma: no cover

    async def _handle_compile_batch(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle /compile-batch — compile all summaries in raw/summaries/."""
        writer = ctx.writer
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
        return
        yield  # pragma: no cover

    async def _handle_import(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle /import <path> - raw-first ingestion."""
        import fitz  # pymupdf
        from agent_core.utils.chunker import chunk_markdown
        from agent_core.utils.converter import ConversionError
        from agent_core.utils.frontmatter import serialize_frontmatter
        from datetime import datetime, timezone

        from pal.archive import archive_raw_files
        from pal.pdf_structure import detect_chapters, extract_chapters, slugify
        from pal.protocol import BatchFallbackProposal

        writer = ctx.writer
        approval_registry = self.approval_registry

        # Per-turn proposal emitter wired to ctx.writer.
        def proposal_emitter(proposal_msg) -> None:
            writer.write(encode_message(proposal_msg))
            drain_task = asyncio.create_task(writer.drain())

            def _log_drain_failure(task: asyncio.Task) -> None:
                exc = task.exception()
                if exc is not None:
                    logger.warning("proposal drain failed: %s", exc)
            drain_task.add_done_callback(_log_drain_failure)

        file_path = msg.args.strip()
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

                from agent_core.inference import BatchUnavailableError
                from pal.pdf_structure import DetectionResult

                effective_inference = (
                    self.batch_inference
                    if self.batch_inference is not None
                    else self.inference
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
                                    doc, inference=self.batch_inference,
                                )
                            except BatchUnavailableError:
                                detection = DetectionResult(
                                    method="single-file", boundaries=[],
                                )
                        elif proposal.approval_choice == "main":
                            detection = await detect_chapters(
                                doc, inference=self.inference,
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
        return
        yield  # pragma: no cover

    async def _handle_learn(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle /learn — extract lessons from the current conversation."""
        writer = ctx.writer
        conv = ctx.conversation
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
        return
        yield  # pragma: no cover

    async def _handle_learnings(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle /learnings — list all extracted learnings."""
        writer = ctx.writer
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
        return
        yield  # pragma: no cover

    async def _handle_promote(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle /promote <slug> — promote a learning to wisdom."""
        from agent_core.utils.frontmatter import parse_frontmatter

        writer = ctx.writer
        slug = msg.args.strip()
        if not slug:
            error = ErrorMessage(error="Usage: /promote <slug>")
            writer.write(encode_message(error))
            await writer.drain()
            return
        try:
            body = self.learning.get(slug)
            meta_path = self.learning.learning_dir / f"{slug}.md"
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
        return
        yield  # pragma: no cover

    async def _handle_rate(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle /rate <good|bad> [comment] — record session feedback."""
        writer = ctx.writer
        args = msg.args.strip()
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
        return
        yield  # pragma: no cover

    async def _handle_profile(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle /profile [set <text>] — show or update user profile."""
        writer = ctx.writer
        args = msg.args.strip()
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
        return
        yield  # pragma: no cover

    async def _get_manager_status(self) -> dict:
        """Fetch /status from the manager. Returns empty dict on error."""
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                resp = await c.get(f"{self.config.inference_url}/status")
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning("manager /status fetch failed: %s", exc)
            return {}

    async def _model_status_text(self) -> str:
        """Render the /model status text showing main (and batch when
        enabled) slots with their loaded model and health."""
        status = await self._get_manager_status()
        slots = status.get("slots", {})
        lines = ["Loaded models:"]
        # Always show main
        main = slots.get("main")
        if main is not None:
            loaded = main.get("loaded_model", "?")
            healthy = main.get("healthy", False)
            marker = "healthy" if healthy else "UNHEALTHY"
            lines.append(f"  main: {loaded} ({marker})")
        else:
            # Fallback: manager doesn't yet expose slots (pre-Phase B server)
            lines.append(f"  main: {self.config.model}")
        # Show batch only when enabled
        if self.config.batch_enabled:
            batch = slots.get("batch")
            if batch is not None:
                loaded = batch.get("loaded_model", "?")
                healthy = batch.get("healthy", False)
                marker = "healthy" if healthy else "UNHEALTHY"
                lines.append(f"  batch: {loaded} ({marker})")
            else:
                lines.append(f"  batch: {self.config.batch_model} (slot info unavailable)")
        return "\n".join(lines)

    async def _dispatch_model_command(self, args: str) -> str:
        """Parse /model args and dispatch.

        Supports:
          - empty -> show status (PB-14)
          - <name> -> swap main
          - --target <slot> <name> -> swap that slot

        Returns a text response to include in the ResponseMessage.
        """
        parts = args.strip().split()
        if not parts:
            return await self._model_status_text()
        target = "main"
        if parts[0] == "--target":
            if len(parts) < 3:
                return "Usage: /model [--target main|batch] <model-name>"
            target = parts[1]
            model_name = " ".join(parts[2:])
            if target not in ("main", "batch"):
                return f"Unknown target: {target}. Use main or batch."
        else:
            model_name = " ".join(parts)
        try:
            await self._request_model_swap(model_name, target=target)
        except Exception as exc:
            return f"Swap failed: {exc}"
        return f"Requested swap: {target} -> {model_name}"

    async def _request_model_swap(self, model: str, target: str = "main") -> dict:
        """POST to the manager's swap endpoint with the target slot."""
        import httpx
        async with httpx.AsyncClient(timeout=120.0) as c:
            resp = await c.post(
                f"{self.config.inference_url}/swap",
                json={"model": model, "target": target},
            )
            resp.raise_for_status()
            return resp.json()

    async def _handle_model(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle /model -- show or switch the active model."""
        import httpx

        writer = ctx.writer
        arg = msg.args.strip()

        if arg == "list":
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
            writer.write(encode_message(resp))
            await writer.drain()
            return

        if arg == "default":
            self.inference.default_model = self.config.model
            resp = ResponseMessage(
                text=f"Model reset to config default: {self.inference.default_model}",
                command="model",
            )
            writer.write(encode_message(resp))
            await writer.drain()
            return

        text = await self._dispatch_model_command(arg)
        resp = ResponseMessage(text=text, command="model")
        writer.write(encode_message(resp))
        await writer.drain()
        return
        yield  # pragma: no cover

    async def _handle_research(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle /research - search, fetch, and summarize topics."""
        from pal.researcher import Researcher, parse_topic_file

        writer = ctx.writer
        args = msg.args.strip()
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
        async def send_progress(text: str) -> None:
            progress = ToolProgressMessage(tool="research", arguments={"status": text})
            writer.write(encode_message(progress))
            await writer.drain()

        def on_progress(text: str) -> None:
            asyncio.get_running_loop().create_task(send_progress(text))

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
        return
        yield  # pragma: no cover

    async def _handle_think(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle /think -- control reasoning mode for this conversation."""
        writer = ctx.writer
        conv = ctx.conversation
        arg = msg.args.strip().lower()
        if arg == "on":
            conv.overrides["reasoning"] = "on"
            logger.info(
                "reasoning_toggle conversation_id=%s turn_idx=%d action=on last_user_message=%.200s",
                id(conv),
                len(conv.messages),
                conv.messages[-1]["content"] if conv.messages else "",
            )
            resp = ResponseMessage(text="Reasoning: on", command="think")
        elif arg == "off":
            conv.overrides["reasoning"] = "off"
            logger.info(
                "reasoning_toggle conversation_id=%s turn_idx=%d action=off last_user_message=%.200s",
                id(conv),
                len(conv.messages),
                conv.messages[-1]["content"] if conv.messages else "",
            )
            resp = ResponseMessage(text="Reasoning: off", command="think")
        elif arg == "auto":
            conv.overrides.pop("reasoning", None)
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
            mode = self.decide_mode(conv)
            resp = ResponseMessage(
                text=f"Reasoning mode: {conv.overrides.get('reasoning') or 'auto'} (effective: {mode})",
                command="think",
            )
        else:
            resp = ResponseMessage(
                text="Usage: /think [on|off|auto|show|hide]",
                command="think",
            )
        writer.write(encode_message(resp))
        await writer.drain()
        return
        yield  # pragma: no cover

    async def _handle_scratch(
        self, msg: CommandMessage, ctx: HandlerContext,
    ) -> AsyncIterator[object]:
        """Handle /scratch <text> — append a timestamped note to the channel scratchpad."""
        writer = ctx.writer
        scratchpad = self._build_scratchpad(ctx.channel_id)
        text = await handle_scratch(scratchpad=scratchpad, text=msg.args)
        resp = ResponseMessage(text=text, command="scratch")
        writer.write(encode_message(resp))
        await writer.drain()
        return
        yield  # pragma: no cover
