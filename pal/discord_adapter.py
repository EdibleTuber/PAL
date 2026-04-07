"""Discord adapter for PAL.

Bridges Discord messages to the PAL daemon via unix socket.
Each allowed Discord user gets their own daemon connection.
"""
import logging

logger = logging.getLogger(__name__)

_DISCORD_MSG_LIMIT = 2000


def parse_discord_message(text: str) -> tuple | None:
    """Parse a Discord message into a PAL intent.

    Returns:
        ("chat", text) for regular messages
        ("command", name, args) for ! commands
        None for empty/invalid messages
    """
    text = text.strip()
    if not text:
        return None
    if text.startswith("!"):
        rest = text[1:].strip()
        if not rest:
            return None
        parts = rest.split(None, 1)
        name = parts[0]
        args = parts[1] if len(parts) > 1 else ""
        return ("command", name, args)
    return ("chat", text)


def format_tool_progress(tool: str, arguments: dict) -> str:
    """Format a tool progress message for Discord (italic text)."""
    if tool == "read_file":
        label = f"reading {arguments.get('path', '?')}..."
    elif tool == "list_directory":
        path = arguments.get("path", "")
        label = f"listing {path or 'vault'}..."
    elif tool == "search_content":
        label = f"searching for \"{arguments.get('query', '?')}\"..."
    elif tool == "search_vault":
        label = f"searching vault for \"{arguments.get('query', '?')}\"..."
    elif tool == "edit_file":
        label = f"editing {arguments.get('path', '?')}..."
    elif tool == "create_file":
        label = f"creating {arguments.get('path', '?')}..."
    else:
        label = f"{tool}..."
    return f"*[{label}]*"


def split_message(text: str, limit: int = _DISCORD_MSG_LIMIT) -> list[str]:
    """Split a message into chunks that fit within Discord's character limit.

    Prefers splitting at paragraph boundaries (double newline).
    Falls back to splitting at the last space before the limit.
    """
    if len(text) <= limit:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at > 0:
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at + 2:]
            continue

        split_at = remaining.rfind(" ", 0, limit)
        if split_at > 0:
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at + 1:]
            continue

        chunks.append(remaining[:limit])
        remaining = remaining[limit:]

    return chunks
