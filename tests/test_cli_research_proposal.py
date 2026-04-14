from pal.cli import format_research_proposal
from pal.protocol import ResearchProposalMessage


def test_format_research_proposal_includes_topic_depth_rationale():
    msg = ResearchProposalMessage(
        proposal_id="abc",
        topic="prompt injection in MCP",
        depth=3,
        rationale="vault is empty on this",
    )
    text = format_research_proposal(msg)
    assert "prompt injection in MCP" in text
    assert "3" in text
    assert "vault is empty on this" in text
    assert "[a]" in text.lower() or "approve" in text.lower()
