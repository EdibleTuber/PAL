import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

from pal.approval_registry import ApprovalRegistry
from pal.learning import LearningManager
from pal.tools import ToolExecutor
from pal.wisdom import WisdomManager


def _make_executor(
    vault: Path,
    emitter,
    wiki=None,
    learning=None,
    wisdom=None,
    registry=None,
):
    return ToolExecutor(
        vault_path=vault,
        retrieval=None,
        wiki=wiki,
        approval_registry=registry or ApprovalRegistry(),
        proposal_emitter=emitter,
        learning=learning or LearningManager(vault),
        wisdom=wisdom or WisdomManager(vault),
    )


def _auto_approve_emitter(registry: ApprovalRegistry):
    def emit(msg):
        registry.approve(msg.proposal_id)
    return emit


def _auto_decline_emitter(registry: ApprovalRegistry):
    def emit(msg):
        registry.decline(msg.proposal_id)
    return emit


def test_propose_promote_emits_and_promotes_on_approve(tmp_path: Path):
    lm = LearningManager(tmp_path)
    slug = lm.add("Granularity", "keep it focused", source="conversation")

    registry = ApprovalRegistry()
    wm = WisdomManager(tmp_path)
    executor = _make_executor(
        tmp_path,
        emitter=_auto_approve_emitter(registry),
        learning=lm,
        wisdom=wm,
        registry=registry,
    )

    result = asyncio.run(executor.run_async("propose_promote", {
        "slug": slug,
        "rationale": "User reiterated.",
    }))
    parsed = json.loads(result)

    assert parsed["status"] == "promoted"
    assert parsed["slug"] == slug
    assert lm.get_meta(slug)["status"] == "promoted"
    titles = {e["title"] for e in wm.list()}
    assert "Granularity" in titles


def test_propose_promote_returns_declined_on_decline(tmp_path: Path):
    lm = LearningManager(tmp_path)
    slug = lm.add("Temp", "body", source="conversation")
    registry = ApprovalRegistry()
    wm = WisdomManager(tmp_path)

    executor = _make_executor(
        tmp_path,
        emitter=_auto_decline_emitter(registry),
        learning=lm,
        wisdom=wm,
        registry=registry,
    )

    result = asyncio.run(executor.run_async("propose_promote", {
        "slug": slug,
        "rationale": "no.",
    }))
    parsed = json.loads(result)

    assert parsed["status"] == "declined"
    assert lm.get_meta(slug)["status"] == "active"


def test_propose_promote_errors_on_missing_slug(tmp_path: Path):
    registry = ApprovalRegistry()
    executor = _make_executor(
        tmp_path,
        emitter=_auto_approve_emitter(registry),
        registry=registry,
    )
    result = asyncio.run(executor.run_async("propose_promote", {
        "slug": "no-such",
        "rationale": "r",
    }))
    parsed = json.loads(result)
    assert "error" in parsed
    assert "no such" in parsed["error"].lower()


def test_propose_promote_errors_on_already_promoted(tmp_path: Path):
    lm = LearningManager(tmp_path)
    slug = lm.add("X", "body", source="conversation")
    lm.mark_promoted(slug)

    registry = ApprovalRegistry()
    executor = _make_executor(
        tmp_path,
        emitter=_auto_approve_emitter(registry),
        learning=lm,
        registry=registry,
    )
    result = asyncio.run(executor.run_async("propose_promote", {
        "slug": slug,
        "rationale": "r",
    }))
    parsed = json.loads(result)
    assert "error" in parsed
    assert "already promoted" in parsed["error"].lower()
