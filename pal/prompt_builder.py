"""SystemPromptBuilder — compose system prompt from base + profile + wisdom.

The base prompt establishes PAL's identity. Profile and wisdom are appended
dynamically so PAL has fresh user context on every chat turn.
"""
from pal.profile import ProfileManager
from pal.wisdom import WisdomManager


BASE_PROMPT = (
    "You are PAL, a personal AI librarian and conversational companion. "
    "You help the user think, answer questions, and manage knowledge. "
    "Be concise, direct, and helpful."
)


class SystemPromptBuilder:
    def __init__(self, profile: ProfileManager, wisdom: WisdomManager) -> None:
        self.profile = profile
        self.wisdom = wisdom

    def build(self) -> str:
        """Compose the current system prompt from base + profile + wisdom."""
        sections = [BASE_PROMPT]

        profile_body = self.profile.read()
        if profile_body:
            sections.append(f"## About the User\n\n{profile_body}")

        wisdom_bodies = self.wisdom.bodies()
        if wisdom_bodies:
            wisdom_text = "\n".join(f"- {body}" for body in wisdom_bodies)
            sections.append(f"## Active Wisdom\n\n{wisdom_text}")

        return "\n\n".join(sections)
