"""ApprovalRegistry — per-session store for research proposal approvals.

Tracks ResearchProposal entries through their lifecycle:
    pending -> approved -> consumed
    pending -> declined
    pending -> expired

Proposals are keyed by proposal_id (uuid4). The registry holds state in
memory for the lifetime of one chat session; it is not persisted.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

ProposalStatus = Literal["pending", "approved", "declined", "consumed", "expired"]

DEFAULT_EXPIRY_MINUTES = 30


@dataclass
class ResearchProposal:
    proposal_id: str
    topic: str
    depth: int
    rationale: str
    status: ProposalStatus
    created_at: datetime
    expires_at: datetime
    # asyncio.Event is set when the proposal reaches a terminal state
    # (approved, declined, or expired). Not part of the public dataclass
    # fields — carried separately for awaiting.
    event: asyncio.Event = field(default_factory=asyncio.Event, repr=False, compare=False)


class ApprovalRegistry:
    def __init__(self, expiry_minutes: int = DEFAULT_EXPIRY_MINUTES) -> None:
        self._proposals: dict[str, ResearchProposal] = {}
        self._expiry_minutes = expiry_minutes

    def create_proposal(self, topic: str, depth: int, rationale: str) -> str:
        proposal_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        self._proposals[proposal_id] = ResearchProposal(
            proposal_id=proposal_id,
            topic=topic,
            depth=depth,
            rationale=rationale,
            status="pending",
            created_at=now,
            expires_at=now + timedelta(minutes=self._expiry_minutes),
        )
        return proposal_id

    def get(self, proposal_id: str) -> Optional[ResearchProposal]:
        return self._proposals.get(proposal_id)

    def approve(self, proposal_id: str) -> None:
        proposal = self._proposals.get(proposal_id)
        if proposal is None or proposal.status != "pending":
            return
        proposal.status = "approved"
        proposal.event.set()

    def decline(self, proposal_id: str) -> None:
        proposal = self._proposals.get(proposal_id)
        if proposal is None or proposal.status != "pending":
            return
        proposal.status = "declined"
        proposal.event.set()

    def consume(self, proposal_id: str) -> bool:
        """Mark an approved proposal as consumed. Returns True on success."""
        proposal = self._proposals.get(proposal_id)
        if proposal is None or proposal.status != "approved":
            return False
        proposal.status = "consumed"
        return True

    def expire_stale(self) -> None:
        """Mark pending proposals past their expiry as expired and signal waiters."""
        now = datetime.now(timezone.utc)
        for proposal in self._proposals.values():
            if proposal.status == "pending" and now >= proposal.expires_at:
                proposal.status = "expired"
                proposal.event.set()

    def edit(
        self,
        proposal_id: str,
        new_topic: str,
        new_depth: int,
    ) -> Optional[str]:
        """Replace a pending proposal with a new approved one.

        Returns the new proposal_id, or None if the original is missing
        or not pending.
        """
        old = self._proposals.get(proposal_id)
        if old is None or old.status != "pending":
            return None
        # Decline the old proposal so any waiter gets a terminal state.
        old.status = "declined"
        old.event.set()
        # The user has committed to the edited values via the CLI, so the
        # new proposal is created already-approved.
        new_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        new_proposal = ResearchProposal(
            proposal_id=new_id,
            topic=new_topic,
            depth=new_depth,
            rationale=old.rationale,
            status="approved",
            created_at=now,
            expires_at=now + timedelta(minutes=self._expiry_minutes),
        )
        new_proposal.event.set()
        self._proposals[new_id] = new_proposal
        return new_id
