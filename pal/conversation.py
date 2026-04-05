"""In-memory conversation history management.

Maintains a rolling window of messages, truncated to history_depth.
No persistence — memorable content goes into the wiki or learning system,
not a chat log.
"""
from dataclasses import dataclass, field


@dataclass
class Conversation:
    history_depth: int
    _messages: list[dict[str, str]] = field(default_factory=list)

    @property
    def messages(self) -> list[dict[str, str]]:
        return list(self._messages)

    def add_user(self, text: str) -> None:
        self._messages.append({"role": "user", "content": text})
        self._truncate()

    def add_assistant(self, text: str) -> None:
        self._messages.append({"role": "assistant", "content": text})
        self._truncate()

    def get_messages_for_api(self, system_prompt: str) -> list[dict[str, str]]:
        """Return message list for the inference API: system + history."""
        return [{"role": "system", "content": system_prompt}] + self.messages

    def clear(self) -> None:
        self._messages.clear()

    def _truncate(self) -> None:
        if len(self._messages) > self.history_depth:
            self._messages = self._messages[-self.history_depth:]
