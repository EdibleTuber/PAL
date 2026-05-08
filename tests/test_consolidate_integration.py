"""End-to-end: propose_consolidate -> approval -> consolidate -> article exists.

Migrated from ToolExecutor.run_async calls to direct Tool subclass calls
(Phase F PR4). The integration tests use real Consolidator and WikiManager
to verify the full pipeline; the new Tool subclasses accept a HandlerContext
with agent attrs rather than a ToolExecutor instance.
"""
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock

from agent_core.approval_registry import ApprovalRegistry
from pal.consolidator import Consolidator
from pal.tools.consolidate import Consolidate, ProposeConsolidate
from pal.wiki import WikiManager


class _FakeInference:
    async def complete(self, messages, reasoning=None, tools=None, model=None):
        class R:
            type = "text"
            content = "## Overview\n\nFused content (from Security/a.md)(from Security/b.md)\n\n## Key Concepts\n\nPoint A (from Security/a.md). Point B (from Security/b.md)."
            reasoning = ""
        return R()


class _StubPromptBuilder:
    def build(self) -> str:
        return "BASE"


class _Config:
    def __init__(self, vault_path):
        self.vault_path = vault_path


class _Agent:
    def __init__(self, vault_path, approval_registry, consolidator):
        self.config = _Config(vault_path)
        self.approval_registry = approval_registry
        self.consolidator = consolidator


def _ctx(agent, emit=None):
    class _C:
        pass
    c = _C()
    c.agent = agent
    c.emit = emit or AsyncMock()
    return c


@pytest.mark.asyncio
async def test_consolidate_end_to_end(tmp_path):
    (tmp_path / "Security").mkdir()
    (tmp_path / "Security" / "a.md").write_text("---\ntitle: A\n---\nBody A")
    (tmp_path / "Security" / "b.md").write_text("---\ntitle: B\n---\nBody B")

    wiki = WikiManager(tmp_path)
    wiki.init_vault()
    registry = ApprovalRegistry()
    consolidator = Consolidator(
        vault_path=tmp_path,
        wiki=wiki,
        inference=_FakeInference(),
        prompt_builder=_StubPromptBuilder(),
    )

    agent = _Agent(tmp_path, approval_registry=registry, consolidator=consolidator)

    emit = AsyncMock()

    async def _emit_and_approve(msg):
        registry.approve(msg.proposal_id)

    emit.side_effect = _emit_and_approve

    ctx = _ctx(agent, emit=emit)

    propose_result = await ProposeConsolidate().run({
        "source_paths": ["Security/a.md", "Security/b.md"],
        "target_path": "Security/Combined.md",
        "target_title": "Combined",
        "rationale": "merge overlapping notes",
    }, ctx)
    propose_payload = json.loads(propose_result)
    assert propose_payload["status"] == "approved"
    pid = propose_payload["proposal_id"]

    exec_result = await Consolidate().run({"proposal_id": pid}, ctx)
    exec_payload = json.loads(exec_result)
    assert exec_payload["status"] == "ok"
    assert exec_payload["vault_exists"] is True
    assert (tmp_path / "Security" / "Combined.md").exists()


@pytest.mark.asyncio
async def test_consolidate_refuses_oversized_combined_sources(tmp_path):
    """Consolidate refuses when combined source bodies exceed max_body_chars."""
    (tmp_path / "Security").mkdir()
    big_body = "y" * 3000
    (tmp_path / "Security" / "a.md").write_text(f"---\ntitle: A\n---\n{big_body}")
    (tmp_path / "Security" / "b.md").write_text(f"---\ntitle: B\n---\n{big_body}")

    wiki = WikiManager(tmp_path)
    wiki.init_vault()

    class _NeverCalledInference:
        async def complete(self, messages, reasoning=None, tools=None, model=None):
            raise AssertionError("inference must not be called on oversized input")

    consolidator = Consolidator(
        vault_path=tmp_path,
        wiki=wiki,
        inference=_NeverCalledInference(),
        prompt_builder=_StubPromptBuilder(),
        max_body_chars=5000,  # smaller than combined 6000
    )
    result = await consolidator.consolidate(
        source_paths=["Security/a.md", "Security/b.md"],
        target_path="Security/Merged.md",
        target_title="Merged",
    )
    assert result["status"] == "too_large"
    assert "exceeds consolidate limit" in result["reason"]
    assert not (tmp_path / "Security" / "Merged.md").exists()
