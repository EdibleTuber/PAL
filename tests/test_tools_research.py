"""Tests for PAL research tools (Phase F PR3)."""
from datetime import datetime, timedelta, timezone
import asyncio
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_core.approval_registry import ApprovalRegistry
from pal.researcher import ResearchReport
from pal.tools.research import ProposeResearch, ResearchTopic


def _empty_report() -> ResearchReport:
    return ResearchReport()


def _build_ctx_with_registry():
    """Build a minimal HandlerContext with an ApprovalRegistry on the agent."""
    ctx = MagicMock()
    ctx.agent.approval_registry = ApprovalRegistry(expiry_minutes=15)
    return ctx


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
    proposal.topic = "X"

    final = MagicMock()
    final.status = "approved"
    final.topic = "X"
    final.depth = 3
    final.topics = None

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
    # Missing rationale -> rationale-required error.
    result = await ProposeResearch().run({"topic": "X"}, _ctx(agent))
    assert "rationale" in result.lower() and "required" in result.lower()
    # Missing both topic and topics -> exactly-one-of error.
    result = await ProposeResearch().run({"rationale": "Y"}, _ctx(agent))
    assert "topic" in result.lower() and "topics" in result.lower()


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
    proposal.topics = None
    ar = MagicMock()
    ar.get = MagicMock(return_value=proposal)
    ar.consume = MagicMock()

    report = MagicMock()
    report.total_summarized = 2
    report.total_fetched = 3
    report.total_failed = 1
    report.results = []

    researcher = MagicMock()
    researcher.research_topics = AsyncMock(return_value=report)

    agent = _Agent(tmp_path, approval_registry=ar, researcher=researcher)
    result = await ResearchTopic().run(
        {"proposal_id": "p-1"}, _ctx(agent),
    )
    ar.consume.assert_called_once_with("p-1")
    researcher.research_topics.assert_awaited_once_with(["X"], depth=3)
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


# --- ProposeResearch topic/topics validation (Task 4) ---

@pytest.mark.asyncio
async def test_propose_research_rejects_neither_topic_nor_topics():
    """Validation: at least one of topic/topics required."""
    tool = ProposeResearch()
    ctx = _build_ctx_with_registry()
    result = await tool.run({"rationale": "test"}, ctx)
    assert "exactly one of 'topic' or 'topics'" in result.lower() or "Error" in result


@pytest.mark.asyncio
async def test_propose_research_rejects_both_topic_and_topics():
    """Validation: mutually exclusive."""
    tool = ProposeResearch()
    ctx = _build_ctx_with_registry()
    result = await tool.run(
        {"topic": "docker", "topics": ["a", "b"], "rationale": "test"},
        ctx,
    )
    assert "exactly one of" in result.lower() or "Error" in result


@pytest.mark.asyncio
async def test_propose_research_rejects_empty_topics_list():
    tool = ProposeResearch()
    ctx = _build_ctx_with_registry()
    result = await tool.run({"topics": [], "rationale": "test"}, ctx)
    assert "Error" in result


@pytest.mark.asyncio
async def test_propose_research_rejects_topics_all_whitespace():
    tool = ProposeResearch()
    ctx = _build_ctx_with_registry()
    result = await tool.run(
        {"topics": ["", "  ", "\n"], "rationale": "test"},
        ctx,
    )
    assert "Error" in result


@pytest.mark.asyncio
async def test_propose_research_topics_list_populates_proposal_topics():
    """Multi-topic mode stores the list on the proposal record."""
    tool = ProposeResearch()
    ctx = _build_ctx_with_registry()
    # Pre-approve any created proposal so run() returns instead of blocking
    ctx.emit = AsyncMock()
    async def auto_approve_after_emit(msg):
        ctx.agent.approval_registry.approve(msg.proposal_id)
    ctx.emit.side_effect = auto_approve_after_emit

    import json
    result_json = await tool.run(
        {"topics": ["a", "b", "c"], "rationale": "test"},
        ctx,
    )
    result = json.loads(result_json)
    assert result["status"] == "approved"
    assert result["topics"] == ["a", "b", "c"]
    assert "topic" in result  # human-readable summary
    proposal = ctx.agent.approval_registry.get(result["proposal_id"])
    assert proposal.topics == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_propose_research_topics_summary_truncates_after_three():
    """Topic summary string shows first 3 + '...' for longer lists."""
    tool = ProposeResearch()
    ctx = _build_ctx_with_registry()
    ctx.emit = AsyncMock()
    async def auto_approve_after_emit(msg):
        ctx.agent.approval_registry.approve(msg.proposal_id)
    ctx.emit.side_effect = auto_approve_after_emit

    import json
    result_json = await tool.run(
        {"topics": ["a", "b", "c", "d", "e"], "rationale": "test"},
        ctx,
    )
    result = json.loads(result_json)
    assert "5 topics" in result["topic"]
    assert "a" in result["topic"]
    assert "b" in result["topic"]
    assert "c" in result["topic"]
    assert "..." in result["topic"]


@pytest.mark.asyncio
async def test_propose_research_return_shape_single_topic_no_topics_key():
    """Regression: single-topic return shape does NOT include `topics` key."""
    tool = ProposeResearch()
    ctx = _build_ctx_with_registry()
    ctx.emit = AsyncMock()
    async def auto_approve_after_emit(msg):
        ctx.agent.approval_registry.approve(msg.proposal_id)
    ctx.emit.side_effect = auto_approve_after_emit

    import json
    result_json = await tool.run(
        {"topic": "docker networking", "rationale": "test"},
        ctx,
    )
    result = json.loads(result_json)
    assert result["status"] == "approved"
    assert result["topic"] == "docker networking"
    assert "topics" not in result


@pytest.mark.asyncio
async def test_research_topic_batch_calls_research_topics_with_list():
    """research_topic on a multi-topic proposal passes the full list to Researcher."""
    from pal.tools.research import ResearchTopic
    from agent_core.approval_registry import ApprovalRegistry

    reg = ApprovalRegistry(expiry_minutes=15)
    pid = reg.create_proposal(
        topic="3 topics: a, b, c",
        depth=3,
        rationale="test",
        topics=["a", "b", "c"],
    )
    reg.approve(pid)

    ctx = MagicMock()
    ctx.agent.approval_registry = reg
    ctx.agent.researcher = MagicMock()
    ctx.agent.researcher.research_topics = AsyncMock(return_value=_empty_report())
    ctx.agent.config = MagicMock()
    ctx.agent.config.vault_path = Path("/tmp")

    tool = ResearchTopic()
    await tool.run({"proposal_id": pid}, ctx)

    ctx.agent.researcher.research_topics.assert_awaited_once()
    call_args = ctx.agent.researcher.research_topics.call_args
    assert call_args.args[0] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_research_topic_single_calls_research_topics_with_one_element():
    """research_topic on a single-topic proposal wraps the topic in a 1-element list."""
    from pal.tools.research import ResearchTopic
    from agent_core.approval_registry import ApprovalRegistry

    reg = ApprovalRegistry(expiry_minutes=15)
    pid = reg.create_proposal(topic="docker networking", depth=3, rationale="test")
    reg.approve(pid)

    ctx = MagicMock()
    ctx.agent.approval_registry = reg
    ctx.agent.researcher = MagicMock()
    ctx.agent.researcher.research_topics = AsyncMock(return_value=_empty_report())
    ctx.agent.config = MagicMock()
    ctx.agent.config.vault_path = Path("/tmp")

    tool = ResearchTopic()
    await tool.run({"proposal_id": pid}, ctx)

    ctx.agent.researcher.research_topics.assert_awaited_once()
    call_args = ctx.agent.researcher.research_topics.call_args
    assert call_args.args[0] == ["docker networking"]
