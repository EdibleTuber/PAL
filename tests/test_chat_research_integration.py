"""Regression tests for the consent gate.

These tests simulate what an indirect-prompt-injection attack would look
like in code: the model (or injected content) tries to invoke
research_topic with a proposal_id that has no valid approval. The tool
must refuse without calling the Researcher.
"""
from unittest.mock import MagicMock

import pytest

from pal.approval_registry import ApprovalRegistry
from pal.researcher import Researcher
from pal.tools import ToolExecutor


@pytest.mark.asyncio
async def test_injected_research_topic_call_without_valid_proposal_is_refused(tmp_path):
    """Fetched content could contain 'call research_topic(proposal_id=...)'.
    The registry refuses unknown ids — researcher must never run."""
    registry = ApprovalRegistry()
    researcher = MagicMock(spec=Researcher)
    researcher.research_topic = MagicMock()
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        researcher=researcher,
    )

    output = await executor.run_async(
        "research_topic",
        {"proposal_id": "injected-by-malicious-webpage"},
    )
    assert "unknown" in output.lower() or "not found" in output.lower()
    researcher.research_topic.assert_not_called()


@pytest.mark.asyncio
async def test_consumed_proposal_cannot_be_reused(tmp_path):
    """After a legitimate research run completes, the proposal_id is
    consumed. A second call with the same id (injected or accidental)
    must be refused."""
    from pal.researcher import ResearchReport

    registry = ApprovalRegistry()
    pid = registry.create_proposal(topic="t", depth=3, rationale="r")
    registry.approve(pid)

    researcher = MagicMock(spec=Researcher)

    async def fake_research_topic(topic, depth, verbose=False):
        return ResearchReport(
            results=[], total_fetched=0, total_summarized=0, total_failed=0
        )
    researcher.research_topic = fake_research_topic

    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        approval_registry=registry,
        researcher=researcher,
    )

    # First call succeeds — proposal is legitimately approved.
    first = await executor.run_async("research_topic", {"proposal_id": pid})
    assert not first.startswith("Error")

    # Second call with the same proposal_id is refused.
    second = await executor.run_async("research_topic", {"proposal_id": pid})
    assert "already" in second.lower() or "consumed" in second.lower()
