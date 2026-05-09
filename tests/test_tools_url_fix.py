"""Tests for the url_fix propose tool."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pal.protocol import UrlFixProposalMessage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _Config:
    def __init__(self, vault_path):
        self.vault_path = vault_path


class _Agent:
    def __init__(self, vault_path, approval_registry=None):
        self.config = _Config(vault_path)
        self.approval_registry = approval_registry


def _ctx(agent, emit=None):
    class _C:
        pass
    c = _C()
    c.agent = agent
    c.emit = emit or AsyncMock()
    return c


def _approval_registry_with_proposal(proposal_id="test-proposal-1"):
    """Returns a mock approval registry that auto-approves any proposal."""
    proposal = MagicMock()
    proposal.id = proposal_id
    proposal.status = "approved"
    proposal.consumed = False
    proposal.event = MagicMock()
    proposal.event.wait = AsyncMock(return_value=None)
    proposal.fields = {}
    proposal.expires_at = None

    ar = MagicMock()
    ar.create_proposal = MagicMock(return_value=proposal_id)
    ar.get = MagicMock(return_value=proposal)
    ar.consume = MagicMock()
    return ar, proposal


# ---------------------------------------------------------------------------
# ProposeUrlFix
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_propose_url_fix_creates_proposal_for_each_empty_article(tmp_path):
    from pal.tools.url_fix import ProposeUrlFix

    vault = tmp_path / "vault"
    (vault / "Hardware").mkdir(parents=True)
    (vault / "Hardware" / "arm-architecture.md").write_text(
        "---\n"
        "title: ARM Architecture\n"
        "sources:\n"
        "  - url: ''\n"
        "    hash: ''\n"
        "---\n\n## Overview\n\nbody\n"
    )

    ar, proposal = _approval_registry_with_proposal()
    emit = AsyncMock()

    async def _emit_and_approve(msg):
        pass  # proposal.status is already "approved"

    emit.side_effect = _emit_and_approve

    agent = _Agent(vault, ar)
    tool = ProposeUrlFix()

    result = json.loads(await tool.run(
        {
            "article_path": "Hardware/arm-architecture.md",
            "proposed_url": "",
            "proposed_source_file": "raw/archived/arm-arm.pdf",
            "rationale": "Found in archived sources",
        },
        _ctx(agent, emit=emit),
    ))

    assert result["status"] == "proposed"
    assert "proposal_id" in result
    ar.create_proposal.assert_called_once()
    call_kwargs = ar.create_proposal.call_args.kwargs
    assert call_kwargs["kind"] == "url_fix"
    assert call_kwargs["article_path"] == "Hardware/arm-architecture.md"
    assert call_kwargs["proposed_source_file"] == "raw/archived/arm-arm.pdf"


@pytest.mark.asyncio
async def test_propose_url_fix_rejects_when_both_proposed_fields_empty(tmp_path):
    from pal.tools.url_fix import ProposeUrlFix

    vault = tmp_path / "vault"
    (vault / "Hardware").mkdir(parents=True)
    (vault / "Hardware" / "arm-architecture.md").write_text(
        "---\ntitle: ARM\nsources:\n  - url: ''\n    hash: ''\n---\n\nbody\n"
    )

    ar, _ = _approval_registry_with_proposal()
    agent = _Agent(vault, ar)
    tool = ProposeUrlFix()

    result = json.loads(await tool.run(
        {
            "article_path": "Hardware/arm-architecture.md",
            "proposed_url": "",
            "proposed_source_file": "",
            "rationale": "nothing",
        },
        _ctx(agent),
    ))

    assert result["status"] == "error"
    assert "must provide" in result["message"].lower()
    ar.create_proposal.assert_not_called()


@pytest.mark.asyncio
async def test_propose_url_fix_rejects_when_article_does_not_exist(tmp_path):
    from pal.tools.url_fix import ProposeUrlFix

    vault = tmp_path / "vault"
    vault.mkdir()

    ar, _ = _approval_registry_with_proposal()
    agent = _Agent(vault, ar)
    tool = ProposeUrlFix()

    result = json.loads(await tool.run(
        {
            "article_path": "Hardware/nonexistent.md",
            "proposed_url": "https://example.com",
            "proposed_source_file": "",
            "rationale": "x",
        },
        _ctx(agent),
    ))

    assert result["status"] == "error"
    assert "not found" in result["message"].lower()
