"""Proactive scanner for learning candidates.

Fires after each LLM turn completes. A two-stage pipeline: a cheap regex
pre-filter gates an LLM extraction call. The extraction call decides
whether a durable lesson exists in the recent conversation and returns
{title, body} or null. Novel candidates are surfaced as approval
proposals via LearningCandidateProposalMessage.
"""
from __future__ import annotations

import logging
import re

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
