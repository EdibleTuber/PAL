"""Message protocol for PAL — newline-delimited JSON over unix socket.

Message types:
    chat            — user text message
    command         — parsed slash command (name + args)
    stream_chunk    — single streaming token from daemon
    response        — complete response (non-streaming commands)
    error           — error message
    tool_progress   — tool execution progress indicator

All messages are serialized as a single JSON line terminated by newline.
"""
import json
from dataclasses import dataclass, asdict


@dataclass
class ChatMessage:
    text: str
    type: str = "chat"


@dataclass
class CommandMessage:
    name: str
    args: str
    type: str = "command"


@dataclass
class StreamChunkMessage:
    token: str
    type: str = "stream_chunk"


@dataclass
class ResponseMessage:
    text: str
    command: str = ""
    type: str = "response"


@dataclass
class ErrorMessage:
    error: str
    type: str = "error"


@dataclass
class ToolProgressMessage:
    tool: str
    arguments: dict
    type: str = "tool_progress"


_MESSAGE_TYPES: dict[str, type] = {
    "chat": ChatMessage,
    "command": CommandMessage,
    "stream_chunk": StreamChunkMessage,
    "response": ResponseMessage,
    "error": ErrorMessage,
    "tool_progress": ToolProgressMessage,
}

Message = ChatMessage | CommandMessage | StreamChunkMessage | ResponseMessage | ErrorMessage | ToolProgressMessage


def encode_message(msg: Message) -> bytes:
    """Serialize a message to a newline-terminated JSON bytes line."""
    return json.dumps(asdict(msg), ensure_ascii=False).encode("utf-8") + b"\n"


def decode_message(data: bytes) -> Message:
    """Deserialize a JSON bytes line into a message object.

    Raises ValueError for unknown message types.
    """
    obj = json.loads(data)
    msg_type = obj.get("type")
    cls = _MESSAGE_TYPES.get(msg_type)
    if cls is None:
        raise ValueError(f"Unknown message type: {msg_type!r}")
    obj.pop("type", None)
    return cls(**obj)
