"""Tests for BatchFallbackProposal protocol message."""
import pytest

from pal.protocol import (
    BatchFallbackProposal,
    BatchFallbackApprovalMessage,
    encode_message,
    decode_message,
)


def test_batch_fallback_proposal_round_trips():
    proposal = BatchFallbackProposal(
        proposal_id="abc123",
        caller="categorizer",
        context="categorizing compile for raw/summaries/X.md",
        original_request={"messages": [{"role": "user", "content": "hi"}], "reasoning": "off"},
    )
    wire = encode_message(proposal)
    restored = decode_message(wire)
    assert isinstance(restored, BatchFallbackProposal)
    assert restored.proposal_id == "abc123"
    assert restored.caller == "categorizer"
    assert restored.context == "categorizing compile for raw/summaries/X.md"
    assert restored.original_request["reasoning"] == "off"


def test_batch_fallback_proposal_accepts_llm_toc_caller():
    proposal = BatchFallbackProposal(
        proposal_id="p1",
        caller="llm_toc",
        context="detecting chapters for foo.pdf",
        original_request={},
    )
    wire = encode_message(proposal)
    restored = decode_message(wire)
    assert restored.caller == "llm_toc"


def test_approval_registry_accepts_batch_fallback_kind():
    from pal.approval_registry import ApprovalRegistry
    reg = ApprovalRegistry()
    pid = reg.create_proposal(
        kind="batch_fallback",
        rationale="batch backend unavailable",
        caller="categorizer",
        context="categorizing raw/summaries/X.md",
    )
    reg.approve(pid, state="main")
    proposal = reg.get(pid)
    assert proposal.status == "approved"
    assert proposal.approval_choice == "main"
    assert proposal.caller == "categorizer"
    assert proposal.context == "categorizing raw/summaries/X.md"


def test_approval_registry_batch_fallback_retry_state():
    from pal.approval_registry import ApprovalRegistry
    reg = ApprovalRegistry()
    pid = reg.create_proposal(
        kind="batch_fallback",
        rationale="batch backend unavailable",
        caller="llm_toc",
        context="detecting chapters for book.pdf",
    )
    reg.approve(pid, state="retry")
    proposal = reg.get(pid)
    assert proposal.status == "approved"
    assert proposal.approval_choice == "retry"


def test_approval_registry_batch_fallback_skip_declines():
    from pal.approval_registry import ApprovalRegistry
    reg = ApprovalRegistry()
    pid = reg.create_proposal(
        kind="batch_fallback",
        rationale="batch backend unavailable",
        caller="categorizer",
        context="x",
    )
    reg.decline(pid)
    proposal = reg.get(pid)
    assert proposal.status == "declined"
    assert proposal.approval_choice is None


def test_batch_fallback_approval_round_trips():
    msg = BatchFallbackApprovalMessage(proposal_id="p1", choice="main")
    wire = encode_message(msg)
    restored = decode_message(wire)
    assert isinstance(restored, BatchFallbackApprovalMessage)
    assert restored.proposal_id == "p1"
    assert restored.choice == "main"


def test_batch_fallback_approval_retry_round_trips():
    msg = BatchFallbackApprovalMessage(proposal_id="p2", choice="retry")
    restored = decode_message(encode_message(msg))
    assert restored.choice == "retry"


def test_batch_fallback_approval_skip_round_trips():
    msg = BatchFallbackApprovalMessage(proposal_id="p3", choice="skip")
    restored = decode_message(encode_message(msg))
    assert restored.choice == "skip"


def test_approval_registry_approve_without_state_is_backward_compatible():
    """Existing callers (research, compile, etc.) call approve(proposal_id)
    with no state argument. This must continue to work unchanged."""
    from pal.approval_registry import ApprovalRegistry
    reg = ApprovalRegistry()
    pid = reg.create_proposal(
        kind="research",
        topic="quantum",
        rationale="investigate",
    )
    reg.approve(pid)  # no state argument
    proposal = reg.get(pid)
    assert proposal.status == "approved"
    assert proposal.approval_choice is None
