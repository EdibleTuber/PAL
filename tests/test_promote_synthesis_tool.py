"""Tests for PromoteSynthesisProposal tool."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_core.approval_registry import ApprovalRegistry
from pal.tools.promote_synthesis import PromoteSynthesisProposal


def _make_ctx(tmp_path: Path, ar: ApprovalRegistry, compiler):
    ctx = MagicMock()
    ctx.agent.approval_registry = ar
    ctx.agent.compiler = compiler
    ctx.agent.config.vault_path = tmp_path
    ctx.emit = AsyncMock()
    return ctx


@pytest.mark.asyncio
async def test_promote_synthesis_rejects_missing_note(tmp_path):
    ctx = _make_ctx(tmp_path, ApprovalRegistry(), MagicMock())
    tool = PromoteSynthesisProposal()
    result_str = await tool.run(
        {"title": "x", "rationale": "y", "note_path": "raw/notes/missing.md"},
        ctx,
    )
    result = json.loads(result_str) if result_str.startswith("{") else result_str
    assert "note_not_found" in result_str or "not found" in result_str.lower()


@pytest.mark.asyncio
async def test_promote_synthesis_rejects_path_traversal(tmp_path):
    (tmp_path / "raw" / "notes").mkdir(parents=True)
    ctx = _make_ctx(tmp_path, ApprovalRegistry(), MagicMock())
    tool = PromoteSynthesisProposal()
    result_str = await tool.run(
        {"title": "x", "rationale": "y", "note_path": "../../etc/passwd"},
        ctx,
    )
    assert "invalid" in result_str.lower() or "note_not_found" in result_str


@pytest.mark.asyncio
async def test_promote_synthesis_creates_proposal_and_emits_message(tmp_path):
    notes = tmp_path / "raw" / "notes"
    notes.mkdir(parents=True)
    (notes / "vibe.md").write_text("## Overview\nfoo\n## Key Concepts\nbar\n")

    compiler = MagicMock()
    compiler.compile_chat_synthesis = AsyncMock(return_value={
        "status": "ok",
        "title": "Vibe",
        "article_path_rel": "Software-Development/vibe.md",
    })

    ar = ApprovalRegistry()
    ctx = _make_ctx(tmp_path, ar, compiler)

    tool = PromoteSynthesisProposal()

    async def auto_approve():
        await asyncio.sleep(0.05)
        proposals = list(ar._proposals.values())
        if proposals:
            ar.approve(proposals[0].proposal_id)

    asyncio.create_task(auto_approve())
    result_str = await tool.run(
        {"title": "Vibe", "rationale": "user asked", "note_path": "raw/notes/vibe.md"},
        ctx,
    )
    result = json.loads(result_str)
    assert result["status"] == "ok"
    assert result["article_path_rel"] == "Software-Development/vibe.md"

    ctx.emit.assert_awaited()
    compiler.compile_chat_synthesis.assert_awaited_once()


@pytest.mark.asyncio
async def test_promote_synthesis_declined_returns_status(tmp_path):
    notes = tmp_path / "raw" / "notes"
    notes.mkdir(parents=True)
    (notes / "vibe.md").write_text("## Overview\nfoo\n## Key Concepts\nbar\n")

    compiler = MagicMock()
    compiler.compile_chat_synthesis = AsyncMock()

    ar = ApprovalRegistry()
    ctx = _make_ctx(tmp_path, ar, compiler)

    tool = PromoteSynthesisProposal()

    async def auto_decline():
        await asyncio.sleep(0.05)
        proposals = list(ar._proposals.values())
        if proposals:
            ar.decline(proposals[0].proposal_id)

    asyncio.create_task(auto_decline())
    result_str = await tool.run(
        {"title": "Vibe", "rationale": "user asked", "note_path": "raw/notes/vibe.md"},
        ctx,
    )
    result = json.loads(result_str)
    assert result["status"] == "declined"
    compiler.compile_chat_synthesis.assert_not_awaited()
