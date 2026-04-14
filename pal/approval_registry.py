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
