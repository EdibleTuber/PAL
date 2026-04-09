"""Auto-categorization -- LLM-based directory selection for vault articles.

After an article is compiled, the categorizer asks the model which vault
directory best fits the content. Falls back to Research/ on any failure.
"""
import logging
from pathlib import Path

from pal.inference import InferenceClient

logger = logging.getLogger(__name__)

FALLBACK_DIRECTORY = "Research"

CATEGORIZATION_SYSTEM_PROMPT = (
    "You are choosing where to file an article in a wiki vault. "
    "Given the article details and existing directories, respond with "
    "ONLY the directory path (e.g., \"Research\" or \"Projects/tools\"). "
    "If no existing directory fits, suggest a short, descriptive new one. "
    "Never use underscore-prefixed directories (those are system directories). "
    "Never use the raw/ directory. Use hyphens instead of spaces in directory names. "
    "Respond with nothing but the directory path."
)

PREVIEW_WORD_LIMIT = 200


def build_categorization_prompt(title: str, body: str, directories: list[str]) -> str:
    """Build the user prompt for categorization."""
    words = body.split()
    preview = " ".join(words[:PREVIEW_WORD_LIMIT])

    dir_list = "\n".join(f"- {d}" for d in directories) if directories else "- (none yet)"

    return (
        f"Article title: {title}\n"
        f"Content preview: {preview}\n\n"
        f"Existing directories:\n{dir_list}\n\n"
        f"Which directory should this article go in?"
    )


def parse_category_response(response: str) -> str:
    """Parse and validate the model's category response.

    Returns the directory path, or FALLBACK_DIRECTORY if invalid.
    """
    # Only consider the first line -- a valid directory has no newlines
    first_line = response.strip().splitlines()[0] if response.strip() else ""
    category = first_line.strip().strip("/")

    if not category:
        return FALLBACK_DIRECTORY

    # Reject anything suspiciously long (directory names should be short)
    if len(category) > 64:
        return FALLBACK_DIRECTORY

    # Reject paths containing spaces (not a valid directory path)
    if " " in category:
        return FALLBACK_DIRECTORY

    if category.startswith("_"):
        return FALLBACK_DIRECTORY

    if category == "raw" or category.startswith("raw/"):
        return FALLBACK_DIRECTORY

    if ".." in category.split("/"):
        return FALLBACK_DIRECTORY

    return category


class Categorizer:
    def __init__(self, inference: InferenceClient) -> None:
        self.inference = inference

    async def categorize(
        self,
        title: str,
        body: str,
        vault_path: Path,
    ) -> str:
        """Choose the best vault directory for an article.

        Args:
            title: article title
            body: full article body
            vault_path: path to the vault root

        Returns:
            directory path relative to vault root (e.g., "Research")
        """
        directories = self._list_directories(vault_path)
        user_prompt = build_categorization_prompt(title, body, directories)

        messages = [
            {"role": "system", "content": CATEGORIZATION_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        try:
            result = await self.inference.complete(messages)
            return parse_category_response(result.content or "")
        except Exception:
            logger.exception("Categorization failed, falling back to %s", FALLBACK_DIRECTORY)
            return FALLBACK_DIRECTORY

    def _list_directories(self, vault_path: Path) -> list[str]:
        """List non-system, non-raw top-level directories in the vault."""
        dirs = []
        if not vault_path.exists():
            return dirs
        for entry in sorted(vault_path.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("_") or entry.name.startswith("."):
                continue
            if entry.name == "raw":
                continue
            dirs.append(entry.name)
        return dirs
