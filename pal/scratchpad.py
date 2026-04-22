"""Per-channel scratchpad — a free-form markdown file in the vault.

Lives at <vault>/_channels/<channel_id>/scratch.md. Committed via
WikiManager on every write so history is inspectable in git. Size-capped
to prevent drift into a second wiki.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ScratchpadTooLarge(Exception):
    """Raised when a write would exceed the scratchpad size cap."""

    def __init__(self, current_bytes: int, proposed_bytes: int, max_bytes: int) -> None:
        super().__init__(
            f"scratchpad would be {proposed_bytes} bytes (cap {max_bytes}, "
            f"current {current_bytes})"
        )
        self.current_bytes = current_bytes
        self.proposed_bytes = proposed_bytes
        self.max_bytes = max_bytes


class Scratchpad:
    """File-backed free-form markdown owned by one channel."""

    def __init__(
        self,
        vault_path: Path,
        channel_id: str,
        wiki,                 # WikiManager
        max_bytes: int,
    ) -> None:
        self._vault_path = vault_path
        self._channel_id = channel_id
        self._wiki = wiki
        self._max_bytes = max_bytes

    @property
    def _path(self) -> Path:
        return self._vault_path / "_channels" / self._channel_id / "scratch.md"

    def read(self) -> str:
        """Return the scratchpad content, or empty string if missing/unreadable."""
        path = self._path
        if not path.exists():
            return ""
        try:
            with path.open("r", encoding="utf-8") as f:
                return f.read()
        except OSError as exc:
            logger.warning(
                "scratchpad %s unreadable (%s) — treating as empty",
                path, exc,
            )
            return ""

    def write(self, content: str) -> None:
        """Replace scratchpad content. Raises ScratchpadTooLarge if over cap."""
        size = len(content.encode("utf-8"))
        if size > self._max_bytes:
            raise ScratchpadTooLarge(
                current_bytes=len(self.read().encode("utf-8")),
                proposed_bytes=size,
                max_bytes=self._max_bytes,
            )
        path = self._path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        try:
            self._wiki.git_commit(f"scratch: update {self._channel_id}")
        except Exception as exc:
            logger.warning(
                "scratchpad git commit failed for %s: %s",
                self._channel_id, exc,
            )

    def append(self, text: str) -> None:
        """Append text to the scratchpad. Raises ScratchpadTooLarge if resulting size over cap."""
        combined = self.read() + text
        self.write(combined)
