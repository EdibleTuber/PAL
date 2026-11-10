"""Unit tests for auto-categorization."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from pal.categorizer import Categorizer, build_categorization_prompt, parse_category_response
from agent_core.inference import BatchUnavailableError, CompletionResult, InferenceClient


class TestParseCategoryResponse:
    def test_simple_directory(self):
        assert parse_category_response("Research") == "Research"

    def test_nested_directory(self):
        assert parse_category_response("Projects/infrastructure") == "Projects/infrastructure"

    def test_strips_whitespace(self):
        assert parse_category_response("  Research  \n") == "Research"

    def test_strips_leading_slash(self):
        assert parse_category_response("/Research") == "Research"

    def test_strips_trailing_slash(self):
        assert parse_category_response("Research/") == "Research"

    def test_rejects_system_directory(self):
        assert parse_category_response("_wisdom") == "Research"

    def test_rejects_system_nested(self):
        assert parse_category_response("_config/allowlist") == "Research"

    def test_rejects_path_traversal(self):
        assert parse_category_response("../etc") == "Research"

    def test_rejects_empty(self):
        assert parse_category_response("") == "Research"

    def test_rejects_raw(self):
        assert parse_category_response("raw") == "Research"

    def test_rejects_raw_nested(self):
        assert parse_category_response("raw/web") == "Research"


class TestBuildCategorizationPrompt:
    def test_includes_title(self):
        prompt = build_categorization_prompt("Quantum Computing", "Qubits are...", ["Research", "Projects"])
        assert "Quantum Computing" in prompt

    def test_includes_preview(self):
        prompt = build_categorization_prompt("Title", "Some preview text", ["Research"])
        assert "Some preview text" in prompt

    def test_includes_directories(self):
        prompt = build_categorization_prompt("Title", "Preview", ["Research", "Projects", "Notes"])
        assert "Research" in prompt
        assert "Projects" in prompt
        assert "Notes" in prompt

    def test_truncates_preview(self):
        long_text = "word " * 500
        prompt = build_categorization_prompt("Title", long_text, ["Research"])
        # Preview should be truncated, not the full 500 words
        assert len(prompt) < len(long_text)


class TestCategorizer:
    @pytest.mark.asyncio
    async def test_categorize_returns_model_choice(self):
        async def fake_complete(messages, **kwargs):
            return CompletionResult(type="text", content="Projects/infrastructure")

        inference = InferenceClient(base_url="http://unused", model="unused")
        inference.complete = fake_complete

        categorizer = Categorizer(inference)
        result = await categorizer.categorize(
            title="Server Setup Guide",
            body="This guide covers setting up the inference server...",
            vault_path=Path("/tmp/vault"),
        )
        assert result == "Projects/infrastructure"

    @pytest.mark.asyncio
    async def test_categorize_falls_back_on_error(self):
        async def broken_complete(messages, **kwargs):
            raise RuntimeError("LLM down")

        inference = InferenceClient(base_url="http://unused", model="unused")
        inference.complete = broken_complete

        categorizer = Categorizer(inference)
        result = await categorizer.categorize(
            title="Anything",
            body="Anything",
            vault_path=Path("/tmp/vault"),
        )
        assert result == "Research"

    @pytest.mark.asyncio
    async def test_categorize_lists_vault_directories(self, tmp_path):
        vault = tmp_path / "vault"
        (vault / "Research").mkdir(parents=True)
        (vault / "Projects").mkdir()
        (vault / "_wisdom").mkdir()  # system dir, should be excluded
        (vault / "raw").mkdir()       # raw dir, should be excluded

        prompts_seen = []

        async def spy_complete(messages, **kwargs):
            prompts_seen.append(messages[-1]["content"])
            return CompletionResult(type="text", content="Research")

        inference = InferenceClient(base_url="http://unused", model="unused")
        inference.complete = spy_complete

        categorizer = Categorizer(inference)
        await categorizer.categorize("Title", "Body", vault)

        prompt = prompts_seen[0]
        assert "Research" in prompt
        assert "Projects" in prompt
        assert "_wisdom" not in prompt
        assert "raw" not in prompt


@dataclass
class _FakeProposal:
    status: str
    approval_choice: str | None
    event: asyncio.Event
    expires_at: datetime | None = None  # mirrors agent_core Proposal


class _FakeRegistry:
    """Minimal stand-in for ApprovalRegistry that immediately resolves
    proposals according to preconfigured ``next_choice``."""

    def __init__(self):
        self.next_choice = "main"  # "retry" / "main" / "skip"
        self.proposals: dict[str, _FakeProposal] = {}
        self.create_calls: list[dict] = []

    def create_proposal(self, **kwargs):
        self.create_calls.append(kwargs)
        pid = f"p{len(self.proposals) + 1}"
        ev = asyncio.Event()
        if self.next_choice == "skip":
            status = "declined"
            choice = None
        else:
            status = "approved"
            choice = self.next_choice
        self.proposals[pid] = _FakeProposal(
            status=status,
            approval_choice=choice,
            event=ev,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        ev.set()  # Auto-resolve so the caller's wait returns immediately.
        return pid

    def get(self, pid: str) -> _FakeProposal:
        return self.proposals[pid]

    def expire_stale(self):
        pass


@dataclass
class _ExpiringFakeProposal:
    status: str
    approval_choice: str | None
    event: asyncio.Event
    expires_at: datetime


class _ExpiringFakeRegistry:
    """Stand-in whose batch_fallback proposals are NEVER answered; they only
    reach a terminal state when the caller's bounded wait elapses and
    ``expire_stale()`` flips them to ``expired`` (A2)."""

    def __init__(self, expires_in: float = 0.3):
        self.expires_in = expires_in
        self.expire_stale_calls = 0
        self.proposals: dict[str, _ExpiringFakeProposal] = {}

    def create_proposal(self, **kwargs):
        pid = f"p{len(self.proposals) + 1}"
        self.proposals[pid] = _ExpiringFakeProposal(
            status="pending",
            approval_choice=None,
            event=asyncio.Event(),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=self.expires_in),
        )
        return pid

    def get(self, pid: str) -> _ExpiringFakeProposal:
        return self.proposals[pid]

    def expire_stale(self):
        self.expire_stale_calls += 1
        now = datetime.now(timezone.utc)
        for p in self.proposals.values():
            if p.status == "pending" and now >= p.expires_at:
                p.status = "expired"
                p.event.set()


def _make_inference(side_effect):
    m = AsyncMock()
    m.complete.side_effect = side_effect
    return m


@pytest.mark.asyncio
async def test_categorizer_uses_main_on_fallback_approval(tmp_path):
    async def batch_fail(messages, **kwargs):
        raise BatchUnavailableError("down")

    async def main_ok(messages, **kwargs):
        return CompletionResult(type="text", content="Research")

    batch = _make_inference(batch_fail)
    main = _make_inference(main_ok)
    registry = _FakeRegistry()
    registry.next_choice = "main"
    emitted: list = []

    cat = Categorizer(inference=batch)
    category = await cat.categorize(
        title="X",
        body="y",
        vault_path=tmp_path,
        approval_registry=registry,
        proposal_emitter=emitted.append,
        main_inference=main,
    )
    assert category == "Research"
    main.complete.assert_called_once()
    assert len(emitted) == 1
    assert registry.create_calls[0]["kind"] == "batch_fallback"
    assert registry.create_calls[0]["caller"] == "categorizer"


@pytest.mark.asyncio
async def test_categorizer_retries_on_batch_when_user_picks_retry(tmp_path):
    call_count = 0

    async def batch(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise BatchUnavailableError("down")
        return CompletionResult(type="text", content="Technology")

    registry = _FakeRegistry()
    registry.next_choice = "retry"

    cat = Categorizer(inference=_make_inference(batch))
    category = await cat.categorize(
        title="X",
        body="y",
        vault_path=tmp_path,
        approval_registry=registry,
        proposal_emitter=lambda m: None,
    )
    assert category == "Technology"
    assert call_count == 2  # first raised, retry succeeded


@pytest.mark.asyncio
async def test_categorizer_returns_default_when_user_skips(tmp_path):
    async def batch_fail(messages, **kwargs):
        raise BatchUnavailableError("down")

    registry = _FakeRegistry()
    registry.next_choice = "skip"

    cat = Categorizer(inference=_make_inference(batch_fail))
    category = await cat.categorize(
        title="X",
        body="y",
        vault_path=tmp_path,
        approval_registry=registry,
        proposal_emitter=lambda m: None,
    )
    # Current Categorizer default is "Research" (FALLBACK_DIRECTORY).
    assert category == "Research"


@pytest.mark.asyncio
async def test_categorizer_returns_default_when_no_approval_wired(tmp_path):
    """When approval_registry is None, BatchUnavailableError falls
    straight through to the default without prompting."""
    async def batch_fail(messages, **kwargs):
        raise BatchUnavailableError("down")

    cat = Categorizer(inference=_make_inference(batch_fail))
    category = await cat.categorize(title="X", body="y", vault_path=tmp_path)
    assert category == "Research"


@pytest.mark.asyncio
async def test_categorizer_unanswered_proposal_expires_to_default(tmp_path):
    """A2: a batch_fallback proposal that is never answered must not hang
    categorize() forever. The wait must be bounded by the proposal's own
    expires_at; on expiry the call returns the default category and the
    registry's expire_stale() is consulted."""
    async def batch_fail(messages, **kwargs):
        raise BatchUnavailableError("down")

    registry = _ExpiringFakeRegistry(expires_in=0.3)
    cat = Categorizer(inference=_make_inference(batch_fail))

    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        category = await asyncio.wait_for(
            cat.categorize(
                title="X",
                body="y",
                vault_path=tmp_path,
                approval_registry=registry,
                proposal_emitter=lambda m: None,
            ),
            timeout=5,
        )
    except asyncio.TimeoutError:
        pytest.fail(
            "categorize() hung on an unanswered batch_fallback proposal (A2)"
        )
    elapsed = loop.time() - started

    assert category == "Research"
    assert elapsed < 2.0, (
        f"categorize() took {elapsed:.1f}s; wait not bounded by expires_at"
    )
    assert registry.expire_stale_calls >= 1
    assert registry.get("p1").status == "expired"
