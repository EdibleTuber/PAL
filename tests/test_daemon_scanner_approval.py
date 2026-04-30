"""Verify _route_approval_response handles scanner-issued candidates."""
from pathlib import Path
from unittest.mock import MagicMock

from agent_core.approval_registry import ApprovalRegistry
from agent_core.learning import LearningManager
from pal.learning_scanner import LearningScanner
from agent_core.protocol import LearningCandidateProposalMessage
from pal.protocol import ResearchApprovalResponseMessage


def _make_daemon(tmp_path: Path):
    """Minimal daemon-like shim with only the fields _route_approval_response touches."""
    class Shim:
        def __init__(self):
            self.learning = LearningManager(tmp_path, "pal")
            self.wiki = MagicMock()
            self.wiki.git_commit = MagicMock()

        _route_approval_response = None  # filled in below
    shim = Shim()
    # Import and bind the real method
    from pal.daemon import Daemon
    shim._route_approval_response = Daemon._route_approval_response.__get__(shim)
    return shim


def test_approve_scanner_candidate_saves_learning(tmp_path: Path):
    shim = _make_daemon(tmp_path)
    registry = ApprovalRegistry()
    scanner = LearningScanner(
        learning_manager=shim.learning,
        extractor=MagicMock(),
        emit=lambda msg: None,
    )
    # Seed the scanner with a pending candidate (as if maybe_scan had emitted)
    msg = LearningCandidateProposalMessage(
        proposal_id="cand-1",
        title="Granularity",
        body="keep focused",
        trigger_excerpt="you always merge",
    )
    scanner._pending_id = msg.proposal_id
    scanner._pending_candidate = msg

    response = ResearchApprovalResponseMessage(
        proposal_id="cand-1", decision="approve",
    )
    shim._route_approval_response(response, registry, scanner)

    # Learning written
    entries = shim.learning.list()
    titles = [e["title"] for e in entries]
    assert "Granularity" in titles
    # Scanner pending cleared
    assert scanner._pending_id is None
    # Git commit called
    shim.wiki.git_commit.assert_called_once()


def test_decline_scanner_candidate_clears_without_saving(tmp_path: Path):
    shim = _make_daemon(tmp_path)
    registry = ApprovalRegistry()
    scanner = LearningScanner(
        learning_manager=shim.learning,
        extractor=MagicMock(),
        emit=lambda msg: None,
    )
    msg = LearningCandidateProposalMessage(
        proposal_id="cand-2",
        title="Granularity",
        body="x",
        trigger_excerpt="y",
    )
    scanner._pending_id = msg.proposal_id
    scanner._pending_candidate = msg

    response = ResearchApprovalResponseMessage(
        proposal_id="cand-2", decision="decline",
    )
    shim._route_approval_response(response, registry, scanner)

    # No learning saved
    assert shim.learning.list() == []
    assert scanner._pending_id is None
    shim.wiki.git_commit.assert_not_called()


def test_non_scanner_response_still_routes_to_registry(tmp_path: Path):
    shim = _make_daemon(tmp_path)
    registry = MagicMock()
    scanner = LearningScanner(
        learning_manager=shim.learning,
        extractor=MagicMock(),
        emit=lambda msg: None,
    )
    # Scanner has no pending candidate
    response = ResearchApprovalResponseMessage(
        proposal_id="other-id", decision="approve",
    )
    shim._route_approval_response(response, registry, scanner)

    registry.approve.assert_called_once_with("other-id")
