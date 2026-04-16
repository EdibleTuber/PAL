"""Integration test for the daemon's scanner hook.

Strategy: build the scanner as the daemon would (extractor closure around
inference.complete), stub inference to return a canned candidate, and
verify the scanner emits a proposal when fed a signal message.

This does NOT spin up the full daemon (chat loop, sockets). Task 21 will
cover full end-to-end.
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from pal.learning import LearningManager
from pal.learning_scanner import LearningScanner, extract_candidate


def test_scanner_extractor_closure_calls_inference_and_emits(tmp_path: Path):
    lm = LearningManager(tmp_path)

    # Stub inference: simulate InferenceClient.complete returning text.
    inference = MagicMock()
    text_result = MagicMock()
    text_result.type = "text"
    text_result.content = '{"title": "Granularity", "body": "keep focused"}'
    inference.complete = AsyncMock(return_value=text_result)

    async def _scanner_extractor(recent_turns, trigger_message):
        async def call(prompt: str) -> str:
            r = await inference.complete(messages=[{"role": "user", "content": prompt}], tools=None)
            if r.type != "text":
                return ""
            return r.content or ""
        return await extract_candidate(
            recent_turns=recent_turns,
            trigger_message=trigger_message,
            inference_call=call,
            timeout=5.0,
        )

    emitted = []
    scanner = LearningScanner(
        learning_manager=lm,
        extractor=_scanner_extractor,
        emit=lambda msg: emitted.append(msg),
    )

    asyncio.run(scanner.maybe_scan(
        recent_turns=[{"role": "user", "content": "earlier"}],
        latest_user_message="you always merge these",
    ))

    # Inference was called exactly once (scanner invoked it through the closure).
    assert inference.complete.call_count == 1
    assert len(emitted) == 1
    assert emitted[0].title == "Granularity"
    assert emitted[0].body == "keep focused"


def test_scanner_extractor_returns_none_when_inference_is_not_text(tmp_path: Path):
    lm = LearningManager(tmp_path)

    inference = MagicMock()
    tool_result = MagicMock()
    tool_result.type = "tool_calls"
    tool_result.content = None
    inference.complete = AsyncMock(return_value=tool_result)

    async def _scanner_extractor(recent_turns, trigger_message):
        async def call(prompt: str) -> str:
            r = await inference.complete(messages=[{"role": "user", "content": prompt}], tools=None)
            if r.type != "text":
                return ""
            return r.content or ""
        return await extract_candidate(
            recent_turns=recent_turns,
            trigger_message=trigger_message,
            inference_call=call,
            timeout=5.0,
        )

    emitted = []
    scanner = LearningScanner(
        learning_manager=lm,
        extractor=_scanner_extractor,
        emit=lambda msg: emitted.append(msg),
    )

    asyncio.run(scanner.maybe_scan(
        recent_turns=[],
        latest_user_message="you always merge",
    ))

    assert inference.complete.await_count == 1
    assert emitted == []  # extractor returned "" which is empty, so extract_candidate returns None


def test_scanner_does_not_fire_on_neutral_message(tmp_path: Path):
    lm = LearningManager(tmp_path)

    inference = MagicMock()
    inference.complete = AsyncMock()

    async def _scanner_extractor(recent_turns, trigger_message):
        return None  # not called anyway

    scanner = LearningScanner(
        learning_manager=lm,
        extractor=_scanner_extractor,
        emit=lambda msg: None,
    )

    asyncio.run(scanner.maybe_scan(
        recent_turns=[],
        latest_user_message="what does OpenOCD do?",
    ))

    # No inference call, no emission.
    inference.complete.assert_not_called()
