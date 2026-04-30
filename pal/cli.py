"""PAL CLI: thin wrapper around agent_core.adapters.cli.run_repl.

Provides PALRenderer (PAL splash + agent-specific message formatters) and the
main() entry point. Loop logic, prompt-toolkit setup, history, and socket
connection live in agent_core.adapters.cli.run_repl.
"""
from __future__ import annotations

import asyncio

from agent_core.adapters.cli import run_repl
from agent_core.protocol import ToolProgressMessage

from pal.commands import COMMANDS
from pal.config import load_config
from pal.protocol import (
    BatchFallbackProposal,
    CompileProposalMessage,
    ConsolidateProposalMessage,
    ReorgProposalMessage,
    ResearchProposalMessage,
)


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


def format_compile_proposal(msg: CompileProposalMessage) -> str:
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


def format_reorg_proposal(msg: ReorgProposalMessage) -> str:
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


def format_batch_fallback_proposal(msg: BatchFallbackProposal) -> str:
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


def format_consolidate_proposal(msg: ConsolidateProposalMessage) -> str:
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


class PALRenderer:
    """Renderer plug-in for PAL's CLI: splash + agent-specific formatters."""

    def splash(self) -> str:
        return (
            "PAL - Personal Agentic Librarian\n"
            f"Commands: {render_splash_commands()}"
        )

    def format_message(self, msg: object) -> str | None:
        if isinstance(msg, ResearchProposalMessage):
            return format_research_proposal(msg)
        if isinstance(msg, CompileProposalMessage):
            return format_compile_proposal(msg)
        if isinstance(msg, ReorgProposalMessage):
            return format_reorg_proposal(msg)
        if isinstance(msg, ConsolidateProposalMessage):
            return format_consolidate_proposal(msg)
        if isinstance(msg, BatchFallbackProposal):
            return format_batch_fallback_proposal(msg)
        if isinstance(msg, ToolProgressMessage):
            return _tool_progress_label(msg.tool, msg.arguments)
        return None  # fall through to agent_core's default rendering


def main() -> None:
    """Entry point for the `pal` command."""
    config = load_config()
    asyncio.run(run_repl(config.socket_path, PALRenderer()))


if __name__ == "__main__":
    main()
