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


def test_approve_sets_event_and_status():
    registry = ApprovalRegistry()
    pid = registry.create_proposal(topic="t", depth=3, rationale="r")
    registry.approve(pid)
    proposal = registry.get(pid)
    assert proposal.status == "approved"
    assert proposal.event.is_set()


def test_decline_sets_event_and_status():
    registry = ApprovalRegistry()
    pid = registry.create_proposal(topic="t", depth=3, rationale="r")
    registry.decline(pid)
    proposal = registry.get(pid)
    assert proposal.status == "declined"
    assert proposal.event.is_set()


def test_consume_only_valid_from_approved():
    registry = ApprovalRegistry()
    pid = registry.create_proposal(topic="t", depth=3, rationale="r")
    # cannot consume a pending proposal
    assert registry.consume(pid) is False
    assert registry.get(pid).status == "pending"
    registry.approve(pid)
    assert registry.consume(pid) is True
    assert registry.get(pid).status == "consumed"
    # cannot consume twice
    assert registry.consume(pid) is False


def test_approve_unknown_id_is_noop():
    registry = ApprovalRegistry()
    registry.approve("nonexistent")  # should not raise


def test_approve_declined_proposal_is_noop():
    registry = ApprovalRegistry()
    pid = registry.create_proposal(topic="t", depth=3, rationale="r")
    registry.decline(pid)
    registry.approve(pid)
    assert registry.get(pid).status == "declined"
