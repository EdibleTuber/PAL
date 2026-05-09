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


# ---------------------------------------------------------------------------
# ProposeUrlFix
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_propose_url_fix_creates_proposal_for_each_empty_article(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry
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

    registry = ApprovalRegistry()
    emit = AsyncMock()

    async def _emit_and_approve(msg):
        registry.approve(msg.proposal_id)

    emit.side_effect = _emit_and_approve

    agent = _Agent(vault, registry)
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

    assert result["status"] == "approved"
    assert "proposal_id" in result
    assert result["proposed_source_file"] == "raw/archived/arm-arm.pdf"

    emit.assert_awaited_once()
    msg = emit.call_args[0][0]
    assert isinstance(msg, UrlFixProposalMessage)
    assert msg.proposed_source_file == "raw/archived/arm-arm.pdf"
    assert msg.article_path == "Hardware/arm-architecture.md"


@pytest.mark.asyncio
async def test_propose_url_fix_rejects_when_both_proposed_fields_empty(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry
    from pal.tools.url_fix import ProposeUrlFix

    vault = tmp_path / "vault"
    (vault / "Hardware").mkdir(parents=True)
    (vault / "Hardware" / "arm-architecture.md").write_text(
        "---\ntitle: ARM\nsources:\n  - url: ''\n    hash: ''\n---\n\nbody\n"
    )

    agent = _Agent(vault, ApprovalRegistry())
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


@pytest.mark.asyncio
async def test_propose_url_fix_rejects_when_article_does_not_exist(tmp_path):
    from agent_core.approval_registry import ApprovalRegistry
    from pal.tools.url_fix import ProposeUrlFix

    vault = tmp_path / "vault"
    vault.mkdir()

    agent = _Agent(vault, ApprovalRegistry())
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
