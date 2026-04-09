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
from pal.config import load_config
from pal.protocol import StreamChunkMessage, ResponseMessage, ErrorMessage, ToolProgressMessage, Message


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

    async for msg in client.command_stream(name, args):
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

    console.print("[dim]PAL — Personal Agentic Librarian[/dim]")
    console.print("[dim]Commands: /note /read /search /get /search-web /fetch /import /summarize /compile[/dim]")
    console.print("[dim]          /learn /learnings /promote /rate /profile /wisdom /lint /status /quit[/dim]\n")

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

                try:
                    resp = await _run_command(client, cmd_name, cmd_args, console)
                    console.print(f"\n{resp.text}\n")
                except RuntimeError as exc:
                    console.print(f"\n[red]{exc}[/red]\n")
                continue

            # Stream chat response with live markdown rendering
            accumulated = ""
            console.print()
            live = None
            try:
                async for msg in client.chat(text):
                    if isinstance(msg, ToolProgressMessage):
                        if live is not None:
                            live.stop()
                            live = None
                        label = _tool_progress_label(msg.tool, msg.arguments)
                        console.print(Text(f"  {label}", style="dim"))
                    elif isinstance(msg, StreamChunkMessage):
                        if live is None:
                            live = Live(Markdown(""), console=console, refresh_per_second=10)
                            live.start()
                        accumulated += msg.token
                        live.update(Markdown(accumulated))
                    elif isinstance(msg, ResponseMessage):
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
