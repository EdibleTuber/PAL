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

from pal.client import PalClient
from pal.config import load_config
from pal.protocol import StreamChunkMessage, ResponseMessage, ErrorMessage, ToolProgressMessage


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
    return f"[{tool}...]"


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
    console.print("[dim]Commands: /note /read /search /get /search-web /fetch /summarize /compile[/dim]")
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
                    resp = await client.command(cmd_name, cmd_args)
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
                        console.print(f"  [dim]{label}[/dim]")
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
