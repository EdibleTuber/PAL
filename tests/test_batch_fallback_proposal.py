"""Tests for BatchFallbackProposal protocol message."""
import pytest

from pal.protocol import BatchFallbackProposal, encode_message, decode_message


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
