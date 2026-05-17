"""PAL protocol: PAL-specific message types registered with agent_core's
protocol registry, plus a local Message union over both generic and
PAL-specific message types for type hints.

Generic primitives (Chat, Command, StreamChunk, Response, Error, ToolProgress,
LearningCandidateProposal) live in agent_core.protocol. The transport
machinery (encode_message, decode_message, STREAM_BUFFER_LIMIT) does too.

Message types defined here are PAL-specific approval/proposal messages tied to
PAL's domain workflows (research, compile, reorg, consolidate, promote,
batch_fallback). They register with agent_core.protocol's registry at import
time so encode_message/decode_message round-trip them correctly.
"""
from dataclasses import dataclass
from typing import Literal

from agent_core.protocol import (
    ChatMessage,
    CommandMessage,
    ErrorMessage,
    LearningCandidateProposalMessage,
    ResponseMessage,
    StreamChunkMessage,
    ToolProgressMessage,
    register_message,
)


@register_message
@dataclass
class ResearchProposalMessage:
    proposal_id: str
    topic: str
    depth: int
    rationale: str
    topics: list[str] | None = None
    type: str = "research_proposal"


@register_message
@dataclass
class ResearchApprovalResponseMessage:
    proposal_id: str
    decision: str  # "approve" | "decline" | "edit"
    new_topic: str | None = None
    new_depth: int | None = None
    summary_paths: list[str] | None = None
    type: str = "research_approval_response"


@register_message
@dataclass
class CompileProposalMessage:
    proposal_id: str
    summary_paths: list[str]
    rationale: str
    type: str = "compile_proposal"


@register_message
@dataclass
class ReorgProposalMessage:
    proposal_id: str
    operations: list[dict]
    rationale: str
    references_preview: int
    type: str = "reorg_proposal"


@register_message
@dataclass
class ConsolidateProposalMessage:
    proposal_id: str
    source_paths: list[str]
    target_path: str
    target_title: str
    rationale: str
    type: str = "consolidate_proposal"


@register_message
@dataclass
class UrlFixProposalMessage:
    proposal_id: str
    article_path: str
    proposed_url: str
    proposed_source_file: str
    rationale: str
    type: str = "url_fix_proposal"


@register_message
@dataclass
class PromoteProposalMessage:
    proposal_id: str
    slug: str
    title: str
    body: str
    rationale: str
    type: str = "promote_proposal"


@register_message
@dataclass
class PromoteSynthesisProposalMessage:
    """Daemon to client: a chat-derived synthesis is proposed for promotion to a wiki article."""
    proposal_id: str
    title: str
    rationale: str
    note_path: str
    note_body_preview: str
    type: str = "promote_synthesis_proposal"


@register_message
@dataclass
class BatchFallbackProposal:
    """Emitted when a user-facing call to the batch inference backend fails
    and the user should choose: retry on batch, run on main, or skip this step.

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


@register_message
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


# Local Message union over BOTH generic and PAL-specific types for type hints.
# Consumers like pal/client.py and pal/cli.py import this for their isinstance
# branches and type annotations.
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
    | UrlFixProposalMessage
    | PromoteProposalMessage
    | PromoteSynthesisProposalMessage
    | LearningCandidateProposalMessage
    | BatchFallbackProposal
    | BatchFallbackApprovalMessage
)
