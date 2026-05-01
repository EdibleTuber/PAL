"""Verify PALAgent.handle_other routes scanner-issued candidates correctly.

Phase E migration note: the legacy ``Daemon._route_approval_response`` lifted
into ``PALAgent.handle_other`` (in agent_core 0.5.1's daemon dispatch). These
tests target the new method directly via a minimal PALAgent shim that wires
only the attributes ``handle_other`` reads.
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_core.agent import HandlerContext
from agent_core.approval_registry import ApprovalRegistry
from agent_core.learning import LearningManager
from agent_core.learning_scanner import LearningScanner
from agent_core.protocol import LearningCandidateProposalMessage
from pal.agent import PALAgent
from pal.protocol import (
    BatchFallbackApprovalMessage,
    ResearchApprovalResponseMessage,
)


def _make_agent(tmp_path: Path) -> PALAgent:
    """Build a PALAgent with only the attrs handle_other reads."""
    agent = PALAgent.__new__(PALAgent)
    agent.learning = LearningManager(tmp_path, "pal")
    agent.approval_registry = ApprovalRegistry()
    agent.wiki = MagicMock()
    agent.wiki.git_commit = MagicMock()
    agent.scanner = LearningScanner(
        learning_manager=agent.learning,
        extractor=MagicMock(),
        emit=lambda msg: None,
    )
    return agent


def _ctx() -> HandlerContext:
    """A throwaway HandlerContext; handle_other does not read its fields."""
    return HandlerContext(conversation=None, channel_id="cli-default", writer=None)


@pytest.mark.asyncio
async def test_approve_scanner_candidate_saves_learning(tmp_path: Path):
    agent = _make_agent(tmp_path)
    msg = LearningCandidateProposalMessage(
        proposal_id="cand-1",
        title="Granularity",
        body="keep focused",
        trigger_excerpt="you always merge",
    )
    agent.scanner._pending_id = msg.proposal_id
    agent.scanner._pending_candidate = msg

    response = ResearchApprovalResponseMessage(
        proposal_id="cand-1", decision="approve",
    )
    await agent.handle_other(response, _ctx())

    titles = [e["title"] for e in agent.learning.list()]
    assert "Granularity" in titles
    assert agent.scanner._pending_id is None
    agent.wiki.git_commit.assert_called_once()


@pytest.mark.asyncio
async def test_decline_scanner_candidate_clears_without_saving(tmp_path: Path):
    agent = _make_agent(tmp_path)
    msg = LearningCandidateProposalMessage(
        proposal_id="cand-2",
        title="Granularity",
        body="x",
        trigger_excerpt="y",
    )
    agent.scanner._pending_id = msg.proposal_id
    agent.scanner._pending_candidate = msg

    response = ResearchApprovalResponseMessage(
        proposal_id="cand-2", decision="decline",
    )
    await agent.handle_other(response, _ctx())

    assert agent.learning.list() == []
    assert agent.scanner._pending_id is None
    agent.wiki.git_commit.assert_not_called()


@pytest.mark.asyncio
async def test_non_scanner_response_routes_to_registry(tmp_path: Path):
    agent = _make_agent(tmp_path)
    # Replace the registry with a mock so we can assert the call shape.
    agent.approval_registry = MagicMock()

    response = ResearchApprovalResponseMessage(
        proposal_id="other-id", decision="approve",
    )
    await agent.handle_other(response, _ctx())

    agent.approval_registry.approve.assert_called_once_with("other-id")


@pytest.mark.asyncio
async def test_batch_fallback_retry_routes_with_state(tmp_path: Path):
    agent = _make_agent(tmp_path)
    agent.approval_registry = MagicMock()
    msg = BatchFallbackApprovalMessage(proposal_id="bf-1", choice="retry")
    await agent.handle_other(msg, _ctx())
    agent.approval_registry.approve.assert_called_once_with(
        "bf-1", state="retry",
    )


@pytest.mark.asyncio
async def test_batch_fallback_skip_declines(tmp_path: Path):
    agent = _make_agent(tmp_path)
    agent.approval_registry = MagicMock()
    msg = BatchFallbackApprovalMessage(proposal_id="bf-2", choice="skip")
    await agent.handle_other(msg, _ctx())
    agent.approval_registry.decline.assert_called_once_with("bf-2")
