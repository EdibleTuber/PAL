"""In-memory conversation history management.

Maintains a rolling window of messages, truncated to history_depth.
No persistence — memorable content goes into the wiki or learning system,
not a chat log.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Conversation:
    history_depth: int
    _messages: list[dict] = field(default_factory=list)
    reasoning_override: Literal["on", "off"] | None = None

    @property
    def messages(self) -> list[dict]:
        return list(self._messages)

    def add_user(self, text: str) -> None:
        self._messages.append({"role": "user", "content": text})
        self._truncate()

    def add_assistant(self, text: str) -> None:
        self._messages.append({"role": "assistant", "content": text})
        self._truncate()

    def add_assistant_tool_calls(self, tool_calls: list[dict]) -> None:
        """Record an assistant message that contains tool calls (no text content)."""
        self._messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": tool_calls,
        })
        self._truncate()

    def add_tool_result(self, tool_call_id: str, content: str) -> None:
        """Record a tool result message."""
        self._messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        })
        self._truncate()

    def get_messages_for_api(self, system_prompt: str) -> list[dict]:
        """Return message list for the inference API: system + history."""
        return [{"role": "system", "content": system_prompt}] + self.messages

    def clear(self) -> None:
        self._messages.clear()

    def _truncate(self) -> None:
        if len(self._messages) > self.history_depth:
            self._messages = self._messages[-self.history_depth:]
            # Don't start with orphaned tool messages that lost their
            # matching counterpart during truncation. Drop leading
            # assistant(tool_calls) and tool result messages.
            changed = True
            while changed:
                changed = False
                if self._messages and self._messages[0].get("role") == "tool":
                    self._messages.pop(0)
                    changed = True
                elif (
                    self._messages
                    and self._messages[0].get("role") == "assistant"
                    and self._messages[0].get("tool_calls")
                ):
                    self._messages.pop(0)
                    changed = True
