"""Tests for UrlFixProposalMessage."""

from pal.protocol import UrlFixProposalMessage


def test_url_fix_proposal_message_fields():
    msg = UrlFixProposalMessage(
        proposal_id="test-id",
        article_path="Hardware/arm-architecture.md",
        proposed_url="",
        proposed_source_file="raw/archived/arm-arm.pdf",
        rationale="ARM ARM PDF found in archived sources",
    )
    assert msg.proposal_id == "test-id"
    assert msg.article_path == "Hardware/arm-architecture.md"
    assert msg.proposed_source_file == "raw/archived/arm-arm.pdf"
    assert msg.type == "url_fix_proposal"


def test_url_fix_proposal_message_serializes_to_dict():
    msg = UrlFixProposalMessage(
        proposal_id="abc",
        article_path="AI/x.md",
        proposed_url="https://example.com",
        proposed_source_file="",
        rationale="found via search",
    )
    d = msg.to_dict() if hasattr(msg, "to_dict") else msg.__dict__
    assert d.get("proposal_id") == "abc"
    assert d.get("article_path") == "AI/x.md"
    assert d.get("proposed_url") == "https://example.com"
