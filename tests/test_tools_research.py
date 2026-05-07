"""Tests for PAL research tools (Phase F PR3)."""
from datetime import datetime, timedelta, timezone
import asyncio
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pal.tools.research import ProposeResearch, ResearchTopic


@dataclass
class _Config:
    vault_path: Path


class _Agent:
    def __init__(self, vault_path, approval_registry=None, researcher=None):
        self.config = _Config(vault_path)
        self.approval_registry = approval_registry
        self.researcher = researcher


def _ctx(agent, emit=None):
    class _C:
        pass
    c = _C()
    c.agent = agent
    c.channel_id = "default"
    c.emit = emit or AsyncMock()
    return c


# --- ProposeResearch ---

async def test_propose_research_emits_proposal(tmp_path):
    """The Tool emits a ResearchProposalMessage via ctx.emit and waits on the
    proposal's event. When the event fires (status approved), it returns a
    JSON-encoded result.
    """
    import json
    from pal.protocol import ResearchProposalMessage

    # Mock proposal that gets approved immediately
    proposal_id = "p-123"
    event = asyncio.Event()
    event.set()
    proposal = MagicMock()
    proposal.event = event
    proposal.expires_at = datetime.now(timezone.utc) + timedelta(seconds=300)

    final = MagicMock()
    final.status = "approved"
    final.topic = "X"
    final.depth = 3

    approval_registry = MagicMock()
    approval_registry.create_proposal = MagicMock(return_value=proposal_id)
    approval_registry.get = MagicMock(side_effect=[proposal, final])
    approval_registry.expire_stale = MagicMock()
    approval_registry.get_successor = MagicMock(return_value=None)

    agent = _Agent(tmp_path, approval_registry=approval_registry)
    emit = AsyncMock()
    result = await ProposeResearch().run(
        {"topic": "X", "rationale": "Y", "depth": 3},
        _ctx(agent, emit=emit),
    )

    emit.assert_awaited_once()
    args, _ = emit.call_args
    msg = args[0]
    assert isinstance(msg, ResearchProposalMessage)
    assert msg.topic == "X"
    assert msg.depth == 3

    parsed = json.loads(result)
    assert parsed["proposal_id"] == proposal_id
    assert parsed["status"] == "approved"
    assert parsed["topic"] == "X"
    assert parsed["depth"] == 3


async def test_propose_research_declined_with_edit(tmp_path):
    """If the proposal is declined but has an edited successor, the Tool
    returns the successor's proposal_id with status=approved."""
    import json
    event = asyncio.Event()
    event.set()
    proposal = MagicMock(); proposal.event = event
    proposal.expires_at = datetime.now(timezone.utc) + timedelta(seconds=300)

    final = MagicMock(); final.status = "declined"
    edited = MagicMock(); edited.proposal_id = "p-edit"; edited.topic = "X-edit"; edited.depth = 5

    ar = MagicMock()
    ar.create_proposal = MagicMock(return_value="p-1")
    ar.get = MagicMock(side_effect=[proposal, final])
    ar.get_successor = MagicMock(return_value=edited)
    ar.expire_stale = MagicMock()

    agent = _Agent(tmp_path, approval_registry=ar)
    result = await ProposeResearch().run(
        {"topic": "X", "rationale": "Y"}, _ctx(agent),
    )
    parsed = json.loads(result)
    assert parsed["proposal_id"] == "p-edit"
    assert parsed["status"] == "approved"
    assert parsed["depth"] == 5


async def test_propose_research_validates_required(tmp_path):
    agent = _Agent(tmp_path, approval_registry=MagicMock())
    result = await ProposeResearch().run({}, _ctx(agent))
    assert "topic" in result.lower() and "required" in result.lower()


async def test_propose_research_no_approval_registry(tmp_path):
    """If approval_registry not configured, returns error."""
    agent = _Agent(tmp_path, approval_registry=None)
    result = await ProposeResearch().run(
        {"topic": "X", "rationale": "Y"}, _ctx(agent),
    )
    assert "not available" in result.lower()


# --- ResearchTopic ---

async def test_research_topic_runs_approved_proposal(tmp_path):
    """Looks up an approved proposal, consumes it, runs researcher, formats."""
    proposal = MagicMock()
    proposal.status = "approved"
    proposal.topic = "X"
    proposal.depth = 3
    ar = MagicMock()
    ar.get = MagicMock(return_value=proposal)
    ar.consume = MagicMock()

    report = MagicMock()
    report.total_summarized = 2
    report.total_fetched = 3
    report.total_failed = 1
    report.results = []

    researcher = MagicMock()
    researcher.research_topic = AsyncMock(return_value=report)

    agent = _Agent(tmp_path, approval_registry=ar, researcher=researcher)
    result = await ResearchTopic().run(
        {"proposal_id": "p-1"}, _ctx(agent),
    )
    ar.consume.assert_called_once_with("p-1")
    researcher.research_topic.assert_awaited_once_with(topic="X", depth=3)
    assert "Research complete" in result
    assert "2 summarized" in result and "3 fetched" in result


async def test_research_topic_unknown_proposal(tmp_path):
    ar = MagicMock(); ar.get = MagicMock(return_value=None)
    agent = _Agent(tmp_path, approval_registry=ar, researcher=MagicMock())
    result = await ResearchTopic().run({"proposal_id": "nope"}, _ctx(agent))
    assert "unknown" in result.lower()


@pytest.mark.parametrize("status,fragment", [
    ("pending", "not approved"),
    ("declined", "declined"),
    ("expired", "expired"),
    ("consumed", "already used"),
])
async def test_research_topic_blocks_invalid_status(tmp_path, status, fragment):
    proposal = MagicMock(); proposal.status = status
    ar = MagicMock(); ar.get = MagicMock(return_value=proposal)
    agent = _Agent(tmp_path, approval_registry=ar, researcher=MagicMock())
    result = await ResearchTopic().run({"proposal_id": "p"}, _ctx(agent))
    assert fragment in result.lower()


async def test_research_topic_validates_proposal_id(tmp_path):
    agent = _Agent(tmp_path, approval_registry=MagicMock(), researcher=MagicMock())
    result = await ResearchTopic().run({}, _ctx(agent))
    assert "proposal_id" in result and "required" in result.lower()


async def test_research_topic_requires_managers(tmp_path):
    """If approval_registry or researcher missing, returns error."""
    agent = _Agent(tmp_path, approval_registry=None, researcher=MagicMock())
    result = await ResearchTopic().run({"proposal_id": "p"}, _ctx(agent))
    assert "not available" in result.lower()
