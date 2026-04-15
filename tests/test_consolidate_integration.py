"""End-to-end: propose_consolidate -> approval -> consolidate -> article exists."""
import json
import pytest
from pathlib import Path

from pal.approval_registry import ApprovalRegistry
from pal.consolidator import Consolidator
from pal.tools import ToolExecutor
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

    emitted = []

    def emit(msg):
        emitted.append(msg)
        # Simulate immediate user approval.
        registry.approve(msg.proposal_id)

    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        wiki=wiki,
        approval_registry=registry,
        proposal_emitter=emit,
        consolidator=consolidator,
    )

    propose_result = await executor.run_async("propose_consolidate", {
        "source_paths": ["Security/a.md", "Security/b.md"],
        "target_path": "Security/Combined.md",
        "target_title": "Combined",
        "rationale": "merge overlapping notes",
    })
    propose_payload = json.loads(propose_result)
    assert propose_payload["status"] == "approved"
    pid = propose_payload["proposal_id"]

    exec_result = await executor.run_async("consolidate", {"proposal_id": pid})
    exec_payload = json.loads(exec_result)
    assert exec_payload["status"] == "ok"
    assert exec_payload["vault_exists"] is True
    assert (tmp_path / "Security" / "Combined.md").exists()
