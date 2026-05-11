"""End-to-end integration tests for chat-derived promotion."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_core.approval_registry import ApprovalRegistry
from pal.compiler import Compiler, CHAT_BANNER_SENTINEL
from pal.tools.promote_synthesis import PromoteSynthesisProposal
from pal.article import parse_article


class FakeWiki:
    def __init__(self, existing=None):
        self.existing = existing or []
        self.commits = []
    def list_articles(self):
        return list(self.existing)
    def rebuild_index(self):
        pass
    def git_init(self):
        pass
    def git_commit(self, msg):
        self.commits.append(msg)


class FakeCategorizer:
    async def categorize(self, **kwargs):
        return "Software-Development"


class FakePromptBuilder:
    def build(self):
        return "system prompt"


def _build_compiler(tmp_path, wiki=None):
    return Compiler(
        vault_path=tmp_path,
        wiki=wiki or FakeWiki(),
        inference=MagicMock(),
        categorizer=FakeCategorizer(),
        prompt_builder=FakePromptBuilder(),
    )


@pytest.mark.asyncio
async def test_forward_promotion_end_to_end(tmp_path):
    notes = tmp_path / "raw" / "notes"
    notes.mkdir(parents=True)
    note_body = "## Overview\nVibe-coding is a comprehension strategy.\n\n## Key Concepts\n- Read aloud\n- Rubber duck\n"
    (notes / "vibe-coding.md").write_text(note_body)

    compiler = _build_compiler(tmp_path)
    ar = ApprovalRegistry()

    ctx = MagicMock()
    ctx.agent.approval_registry = ar
    ctx.agent.compiler = compiler
    ctx.agent.config.vault_path = tmp_path
    ctx.emit = AsyncMock()

    tool = PromoteSynthesisProposal()

    async def auto_approve():
        await asyncio.sleep(0.05)
        for p in ar._proposals.values():
            if p.status == "pending":
                ar.approve(p.proposal_id)
                return

    asyncio.create_task(auto_approve())
    result_str = await tool.run(
        {
            "title": "Vibe-coding comprehension strategies",
            "rationale": "user asked",
            "note_path": "raw/notes/vibe-coding.md",
        },
        ctx,
    )
    result = json.loads(result_str)

    assert result["status"] == "ok"
    article_path = tmp_path / result["article_path_rel"]
    assert article_path.exists()
    article = parse_article(article_path.read_text())
    assert article.compiled_truth.lstrip().startswith(CHAT_BANNER_SENTINEL)
    assert article.meta["sources"][-1]["source_type"] == "chat"
    assert article.timeline[-1].source_type == "chat"
