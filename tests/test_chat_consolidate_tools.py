import asyncio
import json
import pytest
from pathlib import Path

from pal.approval_registry import ApprovalRegistry
from pal.tools import ToolExecutor


class _StubConsolidator:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def consolidate(self, *, source_paths, target_path, target_title):
        self.calls.append({"source_paths": list(source_paths), "target_path": target_path, "target_title": target_title})
        return dict(self.outcome)


def _executor(tmp_path, *, stub=None, auto_approve=True):
    registry = ApprovalRegistry()
    emitted = []

    def emit(msg):
        emitted.append(msg)
        if auto_approve:
            registry.approve(msg.proposal_id)

    if stub is None:
        stub = _StubConsolidator({
            "status": "ok",
            "target_path": "Security/Combined.md",
            "article_path_rel": "Security/Combined.md",
            "vault_exists": True,
        })
    executor = ToolExecutor(
        vault_path=tmp_path,
        retrieval=None,
        wiki=None,
        approval_registry=registry,
        proposal_emitter=emit,
        consolidator=stub,
    )
    return executor, registry, emitted, stub


@pytest.mark.asyncio
async def test_propose_consolidate_requires_two_sources(tmp_path):
    executor, _, _, _ = _executor(tmp_path, auto_approve=False)
    result = await executor.run_async("propose_consolidate", {
        "source_paths": ["Security/a.md"],
        "target_path": "Security/Combined.md",
        "target_title": "Combined",
        "rationale": "r",
    })
    assert "at least two" in result.lower()


@pytest.mark.asyncio
async def test_propose_consolidate_requires_target(tmp_path):
    executor, _, _, _ = _executor(tmp_path, auto_approve=False)
    result = await executor.run_async("propose_consolidate", {
        "source_paths": ["Security/a.md", "Security/b.md"],
        "target_path": "",
        "target_title": "Combined",
        "rationale": "r",
    })
    assert "target_path" in result.lower() or "'target_path'" in result


@pytest.mark.asyncio
async def test_propose_then_execute_happy_path(tmp_path):
    executor, registry, emitted, stub = _executor(tmp_path)

    propose_result = await executor.run_async("propose_consolidate", {
        "source_paths": ["Security/a.md", "Security/b.md"],
        "target_path": "Security/Combined.md",
        "target_title": "Combined",
        "rationale": "merge overlapping notes",
    })
    payload = json.loads(propose_result)
    assert payload["status"] == "approved"
    assert payload["source_paths"] == ["Security/a.md", "Security/b.md"]
    assert payload["target_path"] == "Security/Combined.md"
    assert payload["target_title"] == "Combined"

    # One proposal message was emitted to the (fake) CLI/Discord layer.
    assert len(emitted) == 1
    assert emitted[0].target_path == "Security/Combined.md"

    exec_result = await executor.run_async("consolidate", {"proposal_id": payload["proposal_id"]})
    exec_payload = json.loads(exec_result)
    assert exec_payload["status"] == "ok"
    assert exec_payload["vault_exists"] is True
    assert exec_payload["target_path"] == "Security/Combined.md"
    assert "_note" in exec_payload  # ground-truth echo footer

    # Registry proposal is consumed.
    final = registry.get(payload["proposal_id"])
    assert final.status == "consumed"
    assert stub.calls == [{
        "source_paths": ["Security/a.md", "Security/b.md"],
        "target_path": "Security/Combined.md",
        "target_title": "Combined",
    }]


@pytest.mark.asyncio
async def test_execute_rejects_unknown_proposal(tmp_path):
    executor, _, _, _ = _executor(tmp_path, auto_approve=False)
    result = await executor.run_async("consolidate", {"proposal_id": "does-not-exist"})
    assert "unknown proposal_id" in result.lower()


@pytest.mark.asyncio
async def test_execute_rejects_reused_proposal(tmp_path):
    executor, _, _, _ = _executor(tmp_path)
    propose = await executor.run_async("propose_consolidate", {
        "source_paths": ["Security/a.md", "Security/b.md"],
        "target_path": "Security/Combined.md",
        "target_title": "Combined",
        "rationale": "r",
    })
    pid = json.loads(propose)["proposal_id"]
    first = await executor.run_async("consolidate", {"proposal_id": pid})
    assert json.loads(first)["status"] == "ok"
    second = await executor.run_async("consolidate", {"proposal_id": pid})
    assert "already used" in second.lower() or "consumed" in second.lower()
