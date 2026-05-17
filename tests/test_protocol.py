"""Tests for PAL-specific protocol message types.

Generic message-type tests (Chat, Command, StreamChunk, Response, Error,
ToolProgress, LearningCandidateProposal) live in agent_core's own
``tests/test_protocol.py``. The tests below verify that PAL-specific
message types round-trip through agent_core.protocol's registry — i.e.
that importing ``pal.protocol`` registers these classes correctly with
``encode_message``/``decode_message``.

Note: PromoteProposalMessage, BatchFallbackProposal, and
BatchFallbackApprovalMessage have dedicated test modules
(test_protocol_promote_proposal.py, test_batch_fallback_proposal.py).
"""
from agent_core.protocol import encode_message, decode_message
from pal.protocol import (
    CompileProposalMessage,
    ConsolidateProposalMessage,
    ReorgProposalMessage,
    ResearchApprovalResponseMessage,
    ResearchProposalMessage,
)


def test_research_proposal_message_roundtrip():
    msg = ResearchProposalMessage(
        proposal_id="abc-123",
        topic="prompt injection",
        depth=3,
        rationale="vault has no sources",
    )
    line = encode_message(msg)
    decoded = decode_message(line.strip())
    assert isinstance(decoded, ResearchProposalMessage)
    assert decoded.proposal_id == "abc-123"
    assert decoded.topic == "prompt injection"
    assert decoded.depth == 3
    assert decoded.rationale == "vault has no sources"
    assert decoded.type == "research_proposal"


def test_research_approval_response_approve():
    msg = ResearchApprovalResponseMessage(
        proposal_id="abc-123",
        decision="approve",
    )
    decoded = decode_message(encode_message(msg).strip())
    assert isinstance(decoded, ResearchApprovalResponseMessage)
    assert decoded.proposal_id == "abc-123"
    assert decoded.decision == "approve"
    assert decoded.new_topic is None
    assert decoded.new_depth is None


def test_research_approval_response_edit_carries_new_values():
    msg = ResearchApprovalResponseMessage(
        proposal_id="abc-123",
        decision="edit",
        new_topic="refined topic",
        new_depth=5,
    )
    decoded = decode_message(encode_message(msg).strip())
    assert decoded.decision == "edit"
    assert decoded.new_topic == "refined topic"
    assert decoded.new_depth == 5


def test_compile_proposal_message_roundtrip():
    msg = CompileProposalMessage(
        proposal_id="abc",
        summary_paths=["raw/summaries/a.md", "raw/summaries/b.md"],
        rationale="promote findings",
    )
    line = encode_message(msg)
    decoded = decode_message(line.strip())
    assert isinstance(decoded, CompileProposalMessage)
    assert decoded.proposal_id == "abc"
    assert decoded.summary_paths == ["raw/summaries/a.md", "raw/summaries/b.md"]
    assert decoded.rationale == "promote findings"
    assert decoded.type == "compile_proposal"


def test_research_approval_response_carries_summary_paths():
    msg = ResearchApprovalResponseMessage(
        proposal_id="abc",
        decision="edit",
        summary_paths=["raw/summaries/a.md", "raw/summaries/b.md"],
    )
    decoded = decode_message(encode_message(msg).strip())
    assert isinstance(decoded, ResearchApprovalResponseMessage)
    assert decoded.summary_paths == ["raw/summaries/a.md", "raw/summaries/b.md"]
    assert decoded.new_topic is None
    assert decoded.new_depth is None


def test_research_approval_response_summary_paths_defaults_to_none():
    msg = ResearchApprovalResponseMessage(
        proposal_id="abc",
        decision="approve",
    )
    decoded = decode_message(encode_message(msg).strip())
    assert decoded.summary_paths is None


def test_reorg_proposal_message_roundtrip():
    msg = ReorgProposalMessage(
        proposal_id="abc",
        operations=[
            {"type": "move", "src": "A.md", "dst": "B.md"},
            {"type": "merge", "src": "C.md", "dst": "D.md"},
        ],
        rationale="consolidate and rename",
        references_preview=7,
    )
    decoded = decode_message(encode_message(msg).strip())
    assert isinstance(decoded, ReorgProposalMessage)
    assert decoded.proposal_id == "abc"
    assert decoded.operations == [
        {"type": "move", "src": "A.md", "dst": "B.md"},
        {"type": "merge", "src": "C.md", "dst": "D.md"},
    ]
    assert decoded.rationale == "consolidate and rename"
    assert decoded.references_preview == 7
    assert decoded.type == "reorg_proposal"


def test_consolidate_proposal_roundtrip():
    msg = ConsolidateProposalMessage(
        proposal_id="abc-123",
        source_paths=["Security/a.md", "Security/b.md"],
        target_path="Security/Combined.md",
        target_title="Combined",
        rationale="Merge overlapping notes",
    )
    encoded = encode_message(msg)
    decoded = decode_message(encoded.strip())
    assert isinstance(decoded, ConsolidateProposalMessage)
    assert decoded.proposal_id == "abc-123"
    assert decoded.source_paths == ["Security/a.md", "Security/b.md"]
    assert decoded.target_path == "Security/Combined.md"
    assert decoded.target_title == "Combined"
    assert decoded.rationale == "Merge overlapping notes"
    assert decoded.type == "consolidate_proposal"


def test_research_proposal_message_topics_field_default_none():
    """ResearchProposalMessage.topics defaults to None for single-topic mode."""
    from pal.protocol import ResearchProposalMessage
    msg = ResearchProposalMessage(
        proposal_id="abc",
        topic="docker networking",
        depth=3,
        rationale="test",
    )
    assert msg.topics is None


def test_research_proposal_message_topics_field_accepts_list():
    """ResearchProposalMessage.topics carries the list in multi-topic mode."""
    from pal.protocol import ResearchProposalMessage
    msg = ResearchProposalMessage(
        proposal_id="abc",
        topic="3 topics: a, b, c",
        depth=3,
        rationale="test",
        topics=["a", "b", "c"],
    )
    assert msg.topics == ["a", "b", "c"]
