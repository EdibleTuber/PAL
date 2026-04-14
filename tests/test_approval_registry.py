from datetime import datetime, timedelta, timezone

from pal.approval_registry import ApprovalRegistry, ResearchProposal


def test_create_proposal_returns_pending():
    registry = ApprovalRegistry()
    proposal_id = registry.create_proposal(
        topic="indirect prompt injection",
        depth=3,
        rationale="vault has no sources on this",
    )
    assert proposal_id
    proposal = registry.get(proposal_id)
    assert isinstance(proposal, ResearchProposal)
    assert proposal.topic == "indirect prompt injection"
    assert proposal.depth == 3
    assert proposal.rationale == "vault has no sources on this"
    assert proposal.status == "pending"
    assert proposal.proposal_id == proposal_id


def test_get_unknown_returns_none():
    registry = ApprovalRegistry()
    assert registry.get("nonexistent") is None


def test_create_proposal_generates_unique_ids():
    registry = ApprovalRegistry()
    ids = {
        registry.create_proposal(topic=f"t{i}", depth=3, rationale="r")
        for i in range(10)
    }
    assert len(ids) == 10
