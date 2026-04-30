"""Full round-trip: user message -> scanner -> candidate -> approve -> file.

This test exercises the real LearningScanner + LearningManager, stubbing
only the inference extractor. It verifies the file lands in _learning/
with the right content.
"""
import asyncio
from pathlib import Path

from agent_core.learning import LearningManager
from agent_core.learning_scanner import LearningScanner


def test_full_flow_to_disk(tmp_path: Path):
    lm = LearningManager(tmp_path, "pal")
    emitted: list = []

    async def fake_extract(recent, trigger):
        return {"title": "Granularity", "body": "keep focused"}

    scanner = LearningScanner(
        learning_manager=lm,
        extractor=fake_extract,
        emit=lambda msg: emitted.append(msg),
    )
    asyncio.run(scanner.maybe_scan(
        recent_turns=[{"role": "user", "content": "before"}],
        latest_user_message="you always merge into one article",
    ))
    assert len(emitted) == 1
    candidate = emitted[0]

    # Simulate user approve: the daemon's _route_approval_response would
    # call take_pending then lm.add. Reproduce that.
    popped = scanner.take_pending(candidate.proposal_id)
    assert popped is not None

    slug = lm.add(popped.title, popped.body, source="scanner")

    # File on disk
    path = tmp_path / "_learning" / "pal" / f"{slug}.md"
    assert path.exists()
    text = path.read_text()
    assert "Granularity" in text
    assert "keep focused" in text
    assert "source: scanner" in text  # frontmatter carries source label

    # Scanner pending is cleared
    assert scanner._pending_id is None
    assert scanner._pending_candidate is None


def test_full_flow_declined_keeps_nothing_on_disk(tmp_path: Path):
    lm = LearningManager(tmp_path, "pal")
    emitted: list = []

    async def fake_extract(recent, trigger):
        return {"title": "Whatever", "body": "maybe"}

    scanner = LearningScanner(
        learning_manager=lm,
        extractor=fake_extract,
        emit=lambda msg: emitted.append(msg),
    )
    asyncio.run(scanner.maybe_scan(
        recent_turns=[],
        latest_user_message="you always",
    ))
    assert len(emitted) == 1
    candidate = emitted[0]

    # Simulate decline: take_pending (clears state), do nothing.
    popped = scanner.take_pending(candidate.proposal_id)
    assert popped is not None

    # No file written
    learning_dir = tmp_path / "_learning" / "pal"
    if learning_dir.exists():
        assert list(learning_dir.glob("*.md")) == []
    # Scanner pending cleared
    assert scanner._pending_id is None


def test_full_flow_with_queued_second_candidate(tmp_path: Path):
    lm = LearningManager(tmp_path, "pal")
    emitted: list = []
    extractor_returns = [
        {"title": "First", "body": "body-1"},
        {"title": "Second", "body": "body-2"},
    ]
    call_count = {"n": 0}

    async def fake_extract(recent, trigger):
        idx = call_count["n"]
        call_count["n"] += 1
        return extractor_returns[idx]

    scanner = LearningScanner(
        learning_manager=lm,
        extractor=fake_extract,
        emit=lambda msg: emitted.append(msg),
    )

    # First scan emits.
    asyncio.run(scanner.maybe_scan(
        recent_turns=[],
        latest_user_message="you always 1",
    ))
    assert len(emitted) == 1

    # Second scan queues (first still pending).
    asyncio.run(scanner.maybe_scan(
        recent_turns=[],
        latest_user_message="you always 2",
    ))
    assert len(emitted) == 1
    assert len(scanner.queued) == 1

    # Approve first -> save + drain -> second gets emitted.
    first = emitted[0]
    popped = scanner.take_pending(first.proposal_id)
    assert popped.title == "First"
    slug1 = lm.add(popped.title, popped.body, source="scanner")
    assert (tmp_path / "_learning" / "pal" / f"{slug1}.md").exists()

    # After take_pending drained the queue, second is now emitted and pending.
    assert len(emitted) == 2
    assert emitted[1].title == "Second"
    assert scanner._pending_id == emitted[1].proposal_id

    # Approve second.
    popped2 = scanner.take_pending(emitted[1].proposal_id)
    slug2 = lm.add(popped2.title, popped2.body, source="scanner")
    assert (tmp_path / "_learning" / "pal" / f"{slug2}.md").exists()
    assert len(list((tmp_path / "_learning" / "pal").glob("*.md"))) == 2
