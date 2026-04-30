"""PAL CLI — interactive REPL.

Thin client that connects to the daemon over unix socket, sends user input,
and renders streaming markdown responses in the terminal.
"""
import asyncio
import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn
from rich.text import Text

from pal.client import PalClient
from pal.commands import COMMANDS
from pal.config import load_config
from agent_core.protocol import (
    StreamChunkMessage,
    ResponseMessage,
    ErrorMessage,
    ToolProgressMessage,
)
from pal.protocol import (
    ResearchProposalMessage,
    ResearchApprovalResponseMessage,
    CompileProposalMessage,
    ConsolidateProposalMessage,
    ReorgProposalMessage,
    BatchFallbackProposal,
    BatchFallbackApprovalMessage,
    Message,
)

CLI_CHANNEL_ID = "cli-default"
_reasoning_display: str = "show"


def render_splash_commands() -> str:
    """Render the compact command list shown on CLI startup."""
    names = [f"/{c.name}" for c in COMMANDS]
    # Pack names into lines under ~90 chars.
    lines: list[list[str]] = [[]]
    current_len = 0
    for name in names:
        if current_len + len(name) + 1 > 88 and lines[-1]:
            lines.append([])
            current_len = 0
        lines[-1].append(name)
        current_len += len(name) + 1
    return "\n          ".join(" ".join(line) for line in lines)


def format_research_proposal(msg: ResearchProposalMessage) -> str:
    """Render a proposal approval prompt. Pure formatter for testability."""
    return (
        "\n"
        "────────── PAL proposes research ──────────\n"
        f"  Topic:     {msg.topic}\n"
        f"  Depth:     {msg.depth}\n"
        f"  Rationale: {msg.rationale}\n"
        "  [a]pprove  [d]ecline  [e]dit\n"
        "> "
    )


def format_compile_proposal(msg: "CompileProposalMessage") -> str:
    """Render a compile proposal approval prompt. Pure formatter."""
    lines = [
        "",
        "────────── PAL proposes compile ──────────",
        f"  Summaries ({len(msg.summary_paths)}):",
    ]
    for path in msg.summary_paths:
        lines.append(f"    {path}")
    lines.extend([
        f"  Rationale: {msg.rationale}",
        "  [a]pprove  [d]ecline  [e]dit",
        "> ",
    ])
    return "\n".join(lines)


def format_reorg_proposal(msg: "ReorgProposalMessage") -> str:
    """Render a reorg proposal approval prompt. Pure formatter."""
    lines = [
        "",
        "────────── PAL proposes reorg ──────────",
        f"  Operations ({len(msg.operations)}):",
    ]
    for op in msg.operations:
        op_type = op.get("type", "?")
        src = op.get("src", "?")
        dst = op.get("dst", "?")
        tag = f"[{op_type}]"
        lines.append(f"    {tag:<8} {src}")
        lines.append(f"             -> {dst}")
    lines.extend([
        f"  Rationale: {msg.rationale}",
        f"  Would rewrite {msg.references_preview} link references.",
        "  [a]pprove  [d]ecline  [e]dit",
        "> ",
    ])
    return "\n".join(lines)


def format_batch_fallback_proposal(msg: "BatchFallbackProposal") -> str:
    """Render a batch-fallback approval prompt. Pure formatter."""
    lines = [
        "",
        "────────── Batch model unavailable ──────────",
        f"  Caller:    {msg.caller}",
        f"  Context:   {msg.context}",
        "  [r]etry on batch  [m]ain (one-off)  [s]kip",
        "> ",
    ]
    return "\n".join(lines)


def format_consolidate_proposal(msg: "ConsolidateProposalMessage") -> str:
    """Render a consolidate proposal approval prompt. Pure formatter."""
    lines = [
        "",
        "────────── PAL proposes consolidate ──────────",
        f"  Sources ({len(msg.source_paths)}):",
    ]
    for path in msg.source_paths:
        lines.append(f"    {path}")
    lines.extend([
        f"  Target:    {msg.target_path}",
        f"  Title:     {msg.target_title}",
        f"  Rationale: {msg.rationale}",
        "  [a]pprove  [d]ecline  [e]dit",
        "> ",
    ])
    return "\n".join(lines)


def _tool_progress_label(tool: str, arguments: dict) -> str:
    """Format a brief progress label for a tool call."""
    if tool == "read_file":
        return f"[reading {arguments.get('path', '?')}...]"
    if tool == "list_directory":
        path = arguments.get("path", "")
        return f"[listing {path or 'vault'}...]"
    if tool == "search_content":
        return f"[searching for \"{arguments.get('query', '?')}\"...]"
    if tool == "search_vault":
        return f"[searching vault for \"{arguments.get('query', '?')}\"...]"
    if tool == "edit_file":
        return f"[editing {arguments.get('path', '?')}...]"
    if tool == "create_file":
        return f"[creating {arguments.get('path', '?')}...]"
    if tool == "research_topic":
        status = arguments.get("status")
        label = status if status else "running research..."
        return f"[{label}]"
    if tool == "propose_research":
        topic = arguments.get("topic", "")
        label = f"proposing research on \"{topic}\"..." if topic else "proposing research..."
        return f"[{label}]"
    if tool == "search_web":
        query = arguments.get("query", "")
        label = f"searching web for \"{query}\"..." if query else "searching web..."
        return f"[{label}]"
    return f"[{tool}...]"


async def _run_command(
    client: PalClient,
    name: str,
    args: str,
    console: Console,
) -> ResponseMessage:
    """Run a command with progress display."""
    progress_bar = None
    task_id = None
    last_status = ""

    async for msg in client.command_stream(name, args, channel_id=CLI_CHANNEL_ID):
        if isinstance(msg, ToolProgressMessage):
            current = msg.arguments.get("current")
            total = msg.arguments.get("total")
            status = msg.arguments.get("status", "")
            title = msg.arguments.get("title", "")
            step = msg.arguments.get("step", "")

            if current and total and int(total) > 1:
                # Multi-chunk: show progress bar
                if progress_bar is None:
                    progress_bar = Progress(
                        SpinnerColumn(),
                        TextColumn("[dim]{task.fields[step]}[/dim]"),
                        BarColumn(),
                        MofNCompleteColumn(),
                        TextColumn("[dim]{task.fields[title]}[/dim]"),
                        console=console,
                    )
                    progress_bar.start()
                    task_id = progress_bar.add_task("import", total=int(total), step=step, title=title)
                progress_bar.update(task_id, completed=int(current) - 1, step=step, title=title)
            else:
                # Single chunk or no structured progress: dim text
                if status and status != last_status:
                    console.print(Text(f"  {status}", style="dim"))
                    last_status = status

        elif isinstance(msg, ResponseMessage):
            if progress_bar is not None:
                progress_bar.update(task_id, completed=progress_bar.tasks[task_id].total)
                progress_bar.stop()
            return msg

        elif isinstance(msg, ErrorMessage):
            if progress_bar is not None:
                progress_bar.stop()
            raise RuntimeError(msg.error)

    if progress_bar is not None:
        progress_bar.stop()
    raise ConnectionError("Connection closed")


async def run_repl() -> None:
    """Main REPL loop."""
    config = load_config()
    client = PalClient(config.socket_path)
    console = Console()
    session: PromptSession = PromptSession()

    try:
        await client.connect()
    except (ConnectionRefusedError, FileNotFoundError):
        console.print(
            "[red]Cannot connect to PAL daemon.[/red] "
            f"Is it running? (socket: {config.socket_path})"
        )
        sys.exit(1)

    console.print("[dim]PAL - Personal Agentic Librarian[/dim]")
    console.print(f"[dim]Commands: {render_splash_commands()}[/dim]\n")

    try:
        while True:
            with patch_stdout():
                try:
                    user_input = await session.prompt_async("you> ")
                except (EOFError, KeyboardInterrupt):
                    break

            text = user_input.strip()
            if not text:
                continue

            # Parse slash commands
            if text.startswith("/"):
                parts = text[1:].split(None, 1)
                cmd_name = parts[0]
                cmd_args = parts[1] if len(parts) > 1 else ""

                if cmd_name in ("quit", "exit"):
                    break

                # Handle display prefs client-side
                if cmd_name == "think" and cmd_args.strip() in ("show", "hide"):
                    global _reasoning_display
                    _reasoning_display = cmd_args.strip()
                    console.print(f"\nReasoning display: {_reasoning_display}\n")
                    continue

                try:
                    resp = await _run_command(client, cmd_name, cmd_args, console)
                    if resp.reasoning and _reasoning_display == "show":
                        reasoning_lines = resp.reasoning.splitlines()
                        if len(reasoning_lines) > 20:
                            reasoning_lines = reasoning_lines[:20]
                            reasoning_lines.append("... (full reasoning in debug log)")
                        console.print(Text("\n".join(reasoning_lines), style="dim italic"))
                        console.print()
                    console.print(f"\n{resp.text}\n")
                except RuntimeError as exc:
                    console.print(f"\n[red]{exc}[/red]\n")
                continue

            # Stream chat response with live markdown rendering
            accumulated = ""
            console.print()
            live = None
            try:
                async for msg in client.chat(text, channel_id=CLI_CHANNEL_ID):
                    if isinstance(msg, ToolProgressMessage):
                        if live is not None:
                            live.stop()
                            live = None
                        label = _tool_progress_label(msg.tool, msg.arguments)
                        console.print(Text(f"  {label}", style="dim"))
                    elif isinstance(msg, ResearchProposalMessage):
                        if live is not None:
                            live.stop()
                            live = None
                        print(format_research_proposal(msg), end="", flush=True)
                        loop = asyncio.get_running_loop()
                        choice = (await loop.run_in_executor(None, input)).strip().lower()
                        if choice in ("a", "approve"):
                            response = ResearchApprovalResponseMessage(
                                proposal_id=msg.proposal_id, decision="approve"
                            )
                        elif choice in ("e", "edit"):
                            new_topic = (await loop.run_in_executor(None, input, "  New topic: ")).strip()
                            if not new_topic:
                                # Empty topic -> treat as decline, don't send a bad edit.
                                response = ResearchApprovalResponseMessage(
                                    proposal_id=msg.proposal_id, decision="decline"
                                )
                            else:
                                new_depth_raw = (
                                    await loop.run_in_executor(
                                        None, input, f"  New depth [{msg.depth}]: "
                                    )
                                ).strip()
                                if new_depth_raw:
                                    try:
                                        new_depth = int(new_depth_raw)
                                    except ValueError:
                                        new_depth = msg.depth
                                else:
                                    new_depth = msg.depth
                                response = ResearchApprovalResponseMessage(
                                    proposal_id=msg.proposal_id,
                                    decision="edit",
                                    new_topic=new_topic,
                                    new_depth=new_depth,
                                )
                        else:
                            response = ResearchApprovalResponseMessage(
                                proposal_id=msg.proposal_id, decision="decline"
                            )
                        await client.send(response)
                        continue
                    elif isinstance(msg, CompileProposalMessage):
                        if live is not None:
                            live.stop()
                            live = None
                        print(format_compile_proposal(msg), end="", flush=True)
                        loop = asyncio.get_running_loop()
                        choice = (await loop.run_in_executor(None, input)).strip().lower()
                        if choice in ("a", "approve"):
                            response = ResearchApprovalResponseMessage(
                                proposal_id=msg.proposal_id, decision="approve"
                            )
                        elif choice in ("e", "edit"):
                            # v1: edit maps to decline; model reproposes
                            # based on the user's next message.
                            response = ResearchApprovalResponseMessage(
                                proposal_id=msg.proposal_id, decision="decline"
                            )
                        else:
                            response = ResearchApprovalResponseMessage(
                                proposal_id=msg.proposal_id, decision="decline"
                            )
                        await client.send(response)
                        continue
                    elif isinstance(msg, ReorgProposalMessage):
                        if live is not None:
                            live.stop()
                            live = None
                        print(format_reorg_proposal(msg), end="", flush=True)
                        loop = asyncio.get_running_loop()
                        choice = (await loop.run_in_executor(None, input)).strip().lower()
                        if choice in ("a", "approve"):
                            response = ResearchApprovalResponseMessage(
                                proposal_id=msg.proposal_id, decision="approve"
                            )
                        elif choice in ("e", "edit"):
                            response = ResearchApprovalResponseMessage(
                                proposal_id=msg.proposal_id, decision="decline"
                            )
                        else:
                            response = ResearchApprovalResponseMessage(
                                proposal_id=msg.proposal_id, decision="decline"
                            )
                        await client.send(response)
                        continue
                    elif isinstance(msg, ConsolidateProposalMessage):
                        if live is not None:
                            live.stop()
                            live = None
                        print(format_consolidate_proposal(msg), end="", flush=True)
                        loop = asyncio.get_running_loop()
                        choice = (await loop.run_in_executor(None, input)).strip().lower()
                        if choice in ("a", "approve"):
                            response = ResearchApprovalResponseMessage(
                                proposal_id=msg.proposal_id, decision="approve"
                            )
                        elif choice in ("e", "edit"):
                            response = ResearchApprovalResponseMessage(
                                proposal_id=msg.proposal_id, decision="decline"
                            )
                        else:
                            response = ResearchApprovalResponseMessage(
                                proposal_id=msg.proposal_id, decision="decline"
                            )
                        await client.send(response)
                        continue
                    elif isinstance(msg, BatchFallbackProposal):
                        if live is not None:
                            live.stop()
                            live = None
                        print(format_batch_fallback_proposal(msg), end="", flush=True)
                        loop = asyncio.get_running_loop()
                        while True:
                            raw = (await loop.run_in_executor(None, input)).strip().lower()
                            if raw in ("r", "retry"):
                                fallback_choice = "retry"
                                break
                            if raw in ("m", "main"):
                                fallback_choice = "main"
                                break
                            if raw in ("s", "skip", ""):
                                fallback_choice = "skip"
                                break
                            print(f"Invalid choice {raw!r}; please enter r, m, or s.")
                        response = BatchFallbackApprovalMessage(
                            proposal_id=msg.proposal_id,
                            choice=fallback_choice,
                        )
                        await client.send(response)
                        continue
                    elif isinstance(msg, StreamChunkMessage):
                        if live is None:
                            live = Live(Markdown(""), console=console, refresh_per_second=10)
                            live.start()
                        accumulated += msg.token
                        live.update(Markdown(accumulated))
                    elif isinstance(msg, ResponseMessage):
                        if msg.reasoning and _reasoning_display == "show":
                            reasoning_lines = msg.reasoning.splitlines()
                            if len(reasoning_lines) > 20:
                                reasoning_lines = reasoning_lines[:20]
                                reasoning_lines.append("... (full reasoning in debug log)")
                            console.print(Text("\n".join(reasoning_lines), style="dim italic"))
                            console.print()
                        if not accumulated and msg.text:
                            console.print(Markdown(msg.text))
                        break
                    elif isinstance(msg, ErrorMessage):
                        console.print(f"[red]{msg.error}[/red]")
                        break
            finally:
                if live is not None:
                    live.stop()
            console.print()

    finally:
        await client.close()


def main() -> None:
    """Entry point for the `pal` command."""
    asyncio.run(run_repl())
