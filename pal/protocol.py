"""Message protocol for PAL — newline-delimited JSON over unix socket.

Message types:
    chat                        — user text message
    command                     — parsed slash command (name + args)
    stream_chunk                — single streaming token from daemon
    response                    — complete response (non-streaming commands)
    error                       — error message
    tool_progress               — tool execution progress indicator
    research_proposal           — daemon-to-CLI research approval request
    research_approval_response  — CLI-to-daemon approval decision
    compile_proposal            — daemon-to-CLI compile approval request
    reorg_proposal              — daemon-to-CLI reorganization approval request
    consolidate_proposal        — daemon-to-CLI consolidation approval request
    batch_fallback_proposal     — daemon-to-CLI batch inference fallback request
    batch_fallback_approval     — CLI-to-daemon batch fallback choice

All messages are serialized as a single JSON line terminated by newline.
"""
import json
from dataclasses import dataclass, asdict
from typing import Literal

# asyncio StreamReader default is 64 KiB, which /research and similar
# commands can exceed in a single NDJSON line after aggregating sources.
STREAM_BUFFER_LIMIT = 16 * 1024 * 1024


@dataclass
class ChatMessage:
    text: str
    channel_id: str | None = None
    type: str = "chat"


@dataclass
class CommandMessage:
    name: str
    args: str
    channel_id: str | None = None
    type: str = "command"


@dataclass
class StreamChunkMessage:
    token: str
    type: str = "stream_chunk"


@dataclass
class ResponseMessage:
    text: str
    command: str = ""
    reasoning: str = ""
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


@dataclass
class ResearchProposalMessage:
    proposal_id: str
    topic: str
    depth: int
    rationale: str
    type: str = "research_proposal"


@dataclass
class ResearchApprovalResponseMessage:
    proposal_id: str
    decision: str  # "approve" | "decline" | "edit"
    new_topic: str | None = None
    new_depth: int | None = None
    summary_paths: list[str] | None = None
    type: str = "research_approval_response"


@dataclass
class CompileProposalMessage:
    proposal_id: str
    summary_paths: list[str]
    rationale: str
    type: str = "compile_proposal"


@dataclass
class ReorgProposalMessage:
    proposal_id: str
    operations: list[dict]
    rationale: str
    references_preview: int
    type: str = "reorg_proposal"


@dataclass
class ConsolidateProposalMessage:
    proposal_id: str
    source_paths: list[str]
    target_path: str
    target_title: str
    rationale: str
    type: str = "consolidate_proposal"


@dataclass
class PromoteProposalMessage:
    proposal_id: str
    slug: str
    title: str
    body: str
    rationale: str
    type: str = "promote_proposal"


@dataclass
class LearningCandidateProposalMessage:
    proposal_id: str
    title: str
    body: str
    trigger_excerpt: str  # user-message fragment that triggered the scan
    type: str = "learning_candidate_proposal"


@dataclass
class BatchFallbackProposal:
    """Emitted when a user-facing call to the batch inference backend
    fails and the user should choose: retry on batch, run on main, or
    skip this step.

    Approval states carried via approval_choice in the approval registry:
      - approved with state "retry": retry on batch
      - approved with state "main": run on main for this one call
      - declined: caller uses its default fallback
    """
    proposal_id: str
    caller: Literal["categorizer", "llm_toc"]
    context: str
    original_request: dict
    type: str = "batch_fallback_proposal"


@dataclass
class BatchFallbackApprovalMessage:
    """Client to daemon: the user's choice for a BatchFallbackProposal.

    choice values:
      - "retry": approve with state "retry" (retry on batch)
      - "main":  approve with state "main"  (run on main for this one call)
      - "skip":  decline (caller uses its default fallback)
    """
    proposal_id: str
    choice: Literal["retry", "main", "skip"]
    type: str = "batch_fallback_approval"


_MESSAGE_TYPES: dict[str, type] = {
    "chat": ChatMessage,
    "command": CommandMessage,
    "stream_chunk": StreamChunkMessage,
    "response": ResponseMessage,
    "error": ErrorMessage,
    "tool_progress": ToolProgressMessage,
    "research_proposal": ResearchProposalMessage,
    "research_approval_response": ResearchApprovalResponseMessage,
    "compile_proposal": CompileProposalMessage,
    "reorg_proposal": ReorgProposalMessage,
    "consolidate_proposal": ConsolidateProposalMessage,
    "promote_proposal": PromoteProposalMessage,
    "learning_candidate_proposal": LearningCandidateProposalMessage,
    "batch_fallback_proposal": BatchFallbackProposal,
    "batch_fallback_approval": BatchFallbackApprovalMessage,
}

Message = (
    ChatMessage
    | CommandMessage
    | StreamChunkMessage
    | ResponseMessage
    | ErrorMessage
    | ToolProgressMessage
    | ResearchProposalMessage
    | ResearchApprovalResponseMessage
    | CompileProposalMessage
    | ReorgProposalMessage
    | ConsolidateProposalMessage
    | PromoteProposalMessage
    | LearningCandidateProposalMessage
    | BatchFallbackProposal
    | BatchFallbackApprovalMessage
)


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
