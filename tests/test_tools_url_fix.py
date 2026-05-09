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


# ---------------------------------------------------------------------------
# UrlFix
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_url_fix_writes_source_file_and_preserves_other_frontmatter(tmp_path):
    """An approved proposal causes the first sources entry to be rewritten with the approved fields."""
    from pal.tools.url_fix import UrlFix
    from agent_core.approval_registry import ApprovalRegistry

    vault = tmp_path / "vault"
    (vault / "Hardware").mkdir(parents=True)
    article_full_path = vault / "Hardware" / "arm-architecture.md"
    article_full_path.write_text(
        "---\n"
        "title: ARM Architecture\n"
        "compiled_at: '2026-04-01T00:00:00+00:00'\n"
        "status: compiled\n"
        "sources:\n"
        "  - url: ''\n"
        "    hash: 'oldhash'\n"
        "---\n"
        "\n"
        "## Overview\n\nbody\n"
    )

    ar = ApprovalRegistry()
    proposal_id = ar.create_proposal(kind="url_fix", rationale="ARM ARM PDF found")
    ar.approve(proposal_id)  # Test simulates user approval. Real flow waits on event.

    agent = _Agent(vault, ar)
    tool = UrlFix()

    result_json = await tool.run(
        {
            "proposal_id": proposal_id,
            "article_path": "Hardware/arm-architecture.md",
            "proposed_url": "",
            "proposed_source_file": "raw/archived/arm-arm.pdf",
        },
        _ctx(agent),
    )
    result = json.loads(result_json)

    assert result["status"] == "fixed"
    text = article_full_path.read_text()
    assert "source_file: raw/archived/arm-arm.pdf" in text
    assert "title: ARM Architecture" in text
    assert "hash: oldhash" in text  # other sources fields preserved
    # Proposal should be consumed after successful execution.
    assert ar.get(proposal_id).status == "consumed"


@pytest.mark.asyncio
async def test_url_fix_refuses_unapproved_proposal(tmp_path):
    from pal.tools.url_fix import UrlFix
    from agent_core.approval_registry import ApprovalRegistry

    vault = tmp_path / "vault"
    (vault / "Hardware").mkdir(parents=True)
    (vault / "Hardware" / "x.md").write_text(
        "---\ntitle: X\nsources:\n  - url: ''\n    hash: ''\n---\n\nbody\n"
    )

    ar = ApprovalRegistry()
    proposal_id = ar.create_proposal(kind="url_fix", rationale="z")
    # Do NOT approve.

    agent = _Agent(vault, ar)
    tool = UrlFix()

    result_json = await tool.run(
        {
            "proposal_id": proposal_id,
            "article_path": "Hardware/x.md",
            "proposed_url": "https://example.com",
            "proposed_source_file": "",
        },
        _ctx(agent),
    )
    result = json.loads(result_json)

    assert result["status"] == "error"
    assert "approved" in result["message"].lower()
    assert ar.get(proposal_id).status != "consumed"


@pytest.mark.asyncio
async def test_url_fix_refuses_consumed_proposal(tmp_path):
    from pal.tools.url_fix import UrlFix
    from agent_core.approval_registry import ApprovalRegistry

    vault = tmp_path / "vault"
    (vault / "Hardware").mkdir(parents=True)
    (vault / "Hardware" / "x.md").write_text(
        "---\ntitle: X\nsources:\n  - url: ''\n    hash: ''\n---\n\nbody\n"
    )

    ar = ApprovalRegistry()
    proposal_id = ar.create_proposal(kind="url_fix", rationale="z")
    ar.approve(proposal_id)
    ar.consume(proposal_id)  # Mark already consumed.

    agent = _Agent(vault, ar)
    tool = UrlFix()

    result_json = await tool.run(
        {
            "proposal_id": proposal_id,
            "article_path": "Hardware/x.md",
            "proposed_url": "https://example.com",
            "proposed_source_file": "",
        },
        _ctx(agent),
    )
    result = json.loads(result_json)

    assert result["status"] == "error"
    assert ("consumed" in result["message"].lower()) or ("already" in result["message"].lower())
