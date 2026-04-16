"""Central registry of user-facing daemon commands.

Every command the daemon accepts via CommandMessage must have an entry
here. The CLI splash, /help output, system prompt, and the Discord
prefix-rewrite adapter all read from this list. A drift-check test
enforces that every daemon handler branch has a registry entry.
"""
from typing import NamedTuple


class Command(NamedTuple):
    name: str
    args: str
    description: str


COMMANDS: list[Command] = [
    Command("help", "", "Show this message"),
    Command("status", "", "Show daemon status (model, vault, etc.)"),
    Command("read", "<title>", "Read a wiki article"),
    Command("search", "<q>", "Search wiki articles"),
    Command("get", "<title>", "Get article by exact title"),
    Command("note", "<text>", "Save a quick note"),
    Command("lint", "", "Lint wiki articles"),
    Command("profile", "<q>", "Query your profile"),
    Command("wisdom", "[add/remove]", "Manage wisdom entries"),
    Command("search-web", "<q>", "Web search via SearxNG"),
    Command("fetch", "<url>", "Fetch and summarize a URL"),
    Command("summarize", "<t>", "Summarize a wiki article"),
    Command("compile", "<t>", "Compile a wiki article"),
    Command("compile-batch", "", "Compile all summaries in raw/summaries/"),
    Command("import", "<path>", "Import a local document into the vault"),
    Command("learn", "", "Extract learnings from conversation"),
    Command("learnings", "", "List saved learnings"),
    Command("promote", "<id>", "Promote a learning to wisdom"),
    Command("rate", "<id> <n>", "Rate a learning (1-5)"),
    Command("model", "[name]", "Show or switch the active model"),
    Command("think", "[mode]", "Control reasoning (on/off/auto/show/hide)"),
    Command("research", "<t>", "Research a topic or file of topics"),
    Command("quit", "", "End the session"),
]


def command_names() -> set[str]:
    """Return the set of registered command names."""
    return {c.name for c in COMMANDS}
