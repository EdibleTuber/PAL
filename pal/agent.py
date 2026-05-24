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
from pal.commands import (
    Import, Learn, Lint, PALModel,
    Profile, Scratch, Status, Wisdom,
)
from pal.tools import (
    AddLearning,
    CompileBatch as ToolCompileBatch,
    CompileSummary,
    Consolidate,
    CreateFile,
    DeleteFile,
    EditFile,
    MoveFile,
    ProposeCompileBatch,
    ProposeConsolidate,
    ProposePromote,
    ProposeReorg,
    ProposeResearch,
    ProposeUrlFix,
    PromoteSynthesisProposal,
    Reorg,
    ReplaceInFile,
    ResearchTopic,
    UpdateScratch,
    UrlFix,
    WaitForReindex,
)
from agent_core.scratchpad import ScratchpadTooLarge
from pal.prompts.system import PAL_BASE_PROMPT

logger = logging.getLogger(__name__)


async def handle_scratch(scratchpad, text: str) -> str:
    """Append a timestamped note to the given scratchpad. Returns user-facing message.

    Kept as a module-level utility; tests import it directly.
    """
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


class _BasePALPromptAdapter:
    """Thin adapter passed to Compiler and Consolidator.

    Those classes call `prompt_builder.build()` (no channel context) to get a
    base system prompt for their own inference calls. After PR6 there is no
    longer a PAL-owned SystemPromptBuilder; this adapter assembles the same
    content from PAL_BASE_PROMPT + profile + wisdom, mirroring what the old
    PAL SystemPromptBuilder.build() produced when called with no args.

    Constructed during setup() — before _attach_registries — so it holds
    profile/wisdom directly (both are populated before setup() runs) rather
    than depending on the framework prompt_builder attr.
    """

    def __init__(self, profile, wisdom) -> None:
        self._profile = profile
        self._wisdom = wisdom

    def build(self) -> str:
        sections = [PAL_BASE_PROMPT]
        profile_body = self._profile.read()
        if profile_body:
            sections.append(f"## About the User\n\n{profile_body}")
        wisdom_bodies = self._wisdom.bodies()
        if wisdom_bodies:
            wisdom_text = "\n".join(f"- {b}" for b in wisdom_bodies)
            sections.append(f"## Active Wisdom\n\n{wisdom_text}")
        return "\n\n".join(sections)


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

    # Phase F: declarative registration. PR2 populates vault tools;
    # PR3-PR4 add research/compile/consolidate/reorg/wait; PR7 adds
    # PAL-specific overrides for update_scratch and add_learning (shadow
    # the framework builtins to preserve PAL's parameter names and
    # git-commit behaviour). The framework's BUILTIN_TOOLS and
    # BUILTIN_COMMANDS are unioned in automatically by run_daemon._attach_registries.
    tools = [EditFile, CreateFile, MoveFile, DeleteFile, ReplaceInFile,
             ProposeResearch, ResearchTopic,
             CompileSummary, ProposeCompileBatch, ToolCompileBatch,
             ProposeConsolidate, Consolidate,
             ProposeUrlFix, UrlFix,
             ProposeReorg, ProposePromote, Reorg,
             PromoteSynthesisProposal,
             WaitForReindex,
             UpdateScratch, AddLearning]
    # Phase F PR5: PAL-specific command subclasses. The framework's
    # BUILTIN_COMMANDS are unioned in automatically by _attach_registries;
    # PAL-specific commands registered here override any builtin of the same
    # name (status, profile, wisdom, scratch, model).
    commands = [
        Lint, Import, Learn,
        Status, Profile, Wisdom, Scratch, PALModel,
    ]
    # Disabled LLM-facing builtin tools in PAL.
    #   fetch_url: all web fetching goes through the consent-gated research
    #     pipeline (propose_research / research_topic); direct URL fetch
    #     would bypass the approval flow.
    #   search_web: output URLs are mostly unfetchable due to FetchUrl's
    #     allowlist filter; the user prefers propose_research for web work.
    #     See docs/superpowers/specs/2026-05-12-search-vault-json-result-format-design.md.
    # agent_core's SearchWeb/FetchUrl classes stay intact (future agents may
    # want them); PAL just stops registering them.
    disabled_builtins: frozenset[str] = frozenset({"fetch_url", "search_web"})

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
        from pal.reorg import Reorganizer
        from pal.researcher import Researcher
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
            inference=effective_batch,
            vault_path=config.vault_path,
            on_progress=None,
            max_body_chars=config.max_inference_body_chars,
        )

        # Adapter used by Compiler and Consolidator for their own inference
        # calls (no channel context needed — just base prose + profile/wisdom).
        _prompt_adapter = _BasePALPromptAdapter(
            profile=self.profile,
            wisdom=self.wisdom,
        )

        # Compiler: promotes raw summaries into vault articles.
        self.compiler = Compiler(
            vault_path=config.vault_path,
            wiki=self.wiki,
            inference=effective_batch,
            categorizer=self.categorizer,
            prompt_builder=_prompt_adapter,
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
            inference=effective_batch,
            prompt_builder=_prompt_adapter,
            retrieval=self.retrieval,
            max_body_chars=config.max_inference_body_chars,
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

    async def handle_other(self, msg, ctx: HandlerContext) -> None:
        """Route PAL-specific approval / batch-fallback messages.

        Lifted from the legacy Daemon._route_approval_response plus the inline
        BatchFallbackApprovalMessage handling in _handle_connection. The
        agent_core daemon dispatches non-Chat/non-Command messages here.
        """
        from pal.protocol import (
            ResearchApprovalResponseMessage,
            BatchFallbackApprovalMessage,
        )

        if isinstance(msg, ResearchApprovalResponseMessage):
            # Dual-purpose: scanner candidates AND registry-backed proposals
            # share the same response message type (the CLI uses the same
            # interactive prompt for both).
            candidate = self.scanner.take_pending(msg.proposal_id)
            if candidate is not None:
                if msg.decision == "approve":
                    self.learning.add(
                        title=candidate.title,
                        body=candidate.body,
                        source="scanner",
                    )
                    self.wiki.git_commit(
                        f"learn: scanner-captured {candidate.title}",
                    )
                # decline/skip: candidate already cleared by take_pending
                return

            # Registry-backed routing for research/compile/reorg/consolidate/promote.
            if msg.decision == "approve":
                self.approval_registry.approve(msg.proposal_id)
            elif msg.decision == "decline":
                self.approval_registry.decline(msg.proposal_id)
            elif msg.decision == "edit":
                if msg.summary_paths is not None:
                    self.approval_registry.edit(
                        msg.proposal_id,
                        summary_paths=msg.summary_paths,
                    )
                else:
                    self.approval_registry.edit(
                        msg.proposal_id,
                        new_topic=msg.new_topic or None,
                        new_depth=msg.new_depth or None,
                    )
            return

        if isinstance(msg, BatchFallbackApprovalMessage):
            if msg.choice in ("retry", "main"):
                self.approval_registry.approve(
                    msg.proposal_id, state=msg.choice,
                )
            else:
                self.approval_registry.decline(msg.proposal_id)
            return

        logger.warning(
            "PALAgent.handle_other received unrecognized message: %s",
            type(msg).__name__,
        )

    def system_prompt(self, ctx: HandlerContext) -> str:
        """Return PAL's system prompt for this turn.

        Assembles: PAL identity prose + profile + wisdom + channel scratchpad
        + commands catalog. Framework render helpers (attached by
        _attach_registries as self.prompt_builder) supply every section except
        the hand-curated PAL_BASE_PROMPT, which PAL keeps inline because its
        by-purpose tool grouping performs better than alphabetical rendering.
        """
        pb = self.prompt_builder
        return "\n\n".join(filter(None, [
            PAL_BASE_PROMPT,
            pb.render_profile(),
            pb.render_wisdom(),
            pb.render_scratchpad(ctx.channel_id),
            pb.render_commands_catalog(),
        ]))

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

        self.researcher.on_progress = _emit_progress
        self.scanner.emit = _emit_proposal

        conv.add_user(msg.text)
        mode = self.decide_mode(conv)

        messages = conv.get_messages_for_api(
            system_prompt=self.system_prompt(ctx),
        )
        max_tool_rounds = 50

        try:
            full_response: list[str] = []
            tool_calls: list[ToolCall] | None = None

            if mode == "on":
                completion = await self.inference.complete(
                    messages, tools=self.tool_executor.schemas(), reasoning=mode,
                    max_tokens=4096,  # stopgap: bound runaway loops; proper fix tracked in inference safety plan
                )
                self.record_usage(ctx.channel_id, completion.usage)
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
                    messages, tools=self.tool_executor.schemas(), reasoning=mode,
                    max_tokens=4096,  # stopgap: bound runaway loops; proper fix tracked in inference safety plan
                ):
                    if isinstance(item, list):
                        tool_calls = item
                        break
                    if isinstance(item, StreamEnd):
                        # End-of-stream sentinel from agent_core 0.3.1+. Full
                        # finish_reason handling lands in the deferred safety
                        # fix; for now, treat as normal end-of-text.
                        self.record_usage(ctx.channel_id, item.usage)
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

                    result = await self.tool_executor.run(tc.name, tc.arguments, ctx)
                    conv.add_tool_result(tc.id, result)

                # Re-read in case an update_scratch tool call modified the file.
                # system_prompt(ctx) calls render_scratchpad(ctx.channel_id) which
                # reads fresh from disk, so no need to pass scratchpad_content explicitly.
                messages = conv.get_messages_for_api(
                    system_prompt=self.system_prompt(ctx),
                )
                completion = await self.inference.complete(
                    messages, tools=self.tool_executor.schemas(), reasoning=mode,
                    max_tokens=4096,  # stopgap: bound runaway loops; proper fix tracked in inference safety plan
                )
                self.record_usage(ctx.channel_id, completion.usage)

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
        """Phase F PR5: framework registry handles all commands."""
        async for out in self.command_registry.dispatch(msg.name, msg.args, ctx):
            yield out


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

