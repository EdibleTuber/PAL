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

logger = logging.getLogger(__name__)


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
        # directory and _index.md exist.
        self.wiki = WikiManager(config.vault_path)
        self.wiki.init_vault()

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
        """Handle a slash command. Filled in by Task 17."""
        raise NotImplementedError
        yield  # pragma: no cover
