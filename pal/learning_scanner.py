"""Proactive scanner for learning candidates.

Fires after each LLM turn completes. A two-stage pipeline: a cheap regex
pre-filter gates an LLM extraction call. The extraction call decides
whether a durable lesson exists in the recent conversation and returns
{title, body} or null. Novel candidates are surfaced as approval
proposals via LearningCandidateProposalMessage.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# Signal patterns: phrases that plausibly indicate a correction,
# confirmation, or durable preference worth turning into a learning.
# Applied case-insensitively to the latest user message.
_SIGNAL_PATTERNS = [
    r"\bactually\b",
    r"\bno[,.\s]",
    r"\bstop\b",
    r"\byou\s+(always|never|should|shouldn[''`]?t|tend\s+to)\b",
    r"\bexactly\b",
    r"\bperfect\b",
    r"\bthank\s+you\b",
    r"\byou[''`]re\s+right\b",
    r"\bthat[''`]?s\s+wrong\b",
]

_SIGNAL_RE = re.compile("|".join(_SIGNAL_PATTERNS), re.IGNORECASE)


def has_signal(message: str) -> bool:
    """Return True if the message contains a learning-candidate signal."""
    if not message:
        return False
    return _SIGNAL_RE.search(message) is not None


_EXTRACTION_PROMPT = """You review a short conversation excerpt and decide whether a durable lesson is present.

A durable lesson is a behavioral preference, a correction, or a confirmed approach that should shape PAL's future behavior across sessions. It is NOT a one-off factual answer, a research topic, or a fleeting emotion.

Recent conversation (most recent last):
{conversation}

User signal message:
{trigger}

If a durable lesson is present, respond with JSON:
{{"title": "<short specific title>", "body": "<1-3 sentence lesson>"}}

If no durable lesson is present, respond with the bare word:
null

Respond with ONLY the JSON object or the word null. No prose."""


def _format_conversation(turns: list[dict]) -> str:
    if not turns:
        return "(no prior turns)"
    lines = []
    for t in turns:
        role = t.get("role", "user")
        content = (t.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else "(empty)"


async def extract_candidate(
    recent_turns: list[dict],
    trigger_message: str,
    inference_call: Callable,
    timeout: float = 15.0,
) -> Optional[dict]:
    """Ask the inference server whether a durable lesson is present.

    Returns {"title": str, "body": str} or None. Never raises.
    inference_call is an async callable that takes a single prompt string
    and returns the model's response text.
    """
    prompt = _EXTRACTION_PROMPT.format(
        conversation=_format_conversation(recent_turns),
        trigger=trigger_message,
    )
    try:
        raw = await asyncio.wait_for(inference_call(prompt), timeout=timeout)
    except (asyncio.TimeoutError, Exception) as exc:
        logger.warning("learning extraction failed: %s", exc)
        return None

    text = (raw or "").strip()
    if text.lower() == "null" or not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.info("learning extraction returned non-JSON: %s", text[:100])
        return None
    if not isinstance(parsed, dict):
        return None
    title = (parsed.get("title") or "").strip()
    body = (parsed.get("body") or "").strip()
    if not title or not body:
        return None
    return {"title": title, "body": body}


def _slugify_title(title: str) -> str:
    slug = title.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _slug_tokens(slug: str) -> set[str]:
    return {t for t in slug.split("-") if len(t) > 2}


def is_duplicate_candidate(title: str, existing_slugs: list[str]) -> bool:
    """True if the candidate title matches an existing learning by exact slug
    or by high token overlap (Jaccard >= 0.6).
    """
    cand_slug = _slugify_title(title)
    if not cand_slug:
        return False
    if cand_slug in existing_slugs:
        return True
    cand_tokens = _slug_tokens(cand_slug)
    if not cand_tokens:
        return False
    for existing in existing_slugs:
        ex_tokens = _slug_tokens(existing)
        if not ex_tokens:
            continue
        overlap = len(cand_tokens & ex_tokens)
        union = len(cand_tokens | ex_tokens)
        if union and (overlap / union) >= 0.6:
            return True
    return False
