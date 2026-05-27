"""Auto-categorization -- LLM-based directory selection for vault articles.

After an article is compiled, the categorizer asks the model which vault
directory best fits the content. Falls back to Research/ on any failure.
"""
import logging
from pathlib import Path

from agent_core.inference import BatchUnavailableError, InferenceClient
from pal.protocol import BatchFallbackProposal

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
    snippet = response.strip()[:200] if response else "<empty>"

    # Only consider the first line -- a valid directory has no newlines
    first_line = response.strip().splitlines()[0] if response.strip() else ""
    category = first_line.strip().strip("/")

    if not category:
        logger.warning("categorizer fallback (empty response): %r", snippet)
        return FALLBACK_DIRECTORY

    # Reject anything suspiciously long (directory names should be short)
    if len(category) > 64:
        logger.warning("categorizer fallback (response too long): %r", snippet)
        return FALLBACK_DIRECTORY

    # Reject paths containing spaces (not a valid directory path)
    if " " in category:
        logger.warning("categorizer fallback (contains space): %r", snippet)
        return FALLBACK_DIRECTORY

    if category.startswith("_"):
        logger.warning("categorizer fallback (system dir): %r", snippet)
        return FALLBACK_DIRECTORY

    if category == "raw" or category.startswith("raw/"):
        logger.warning("categorizer fallback (raw/ dir): %r", snippet)
        return FALLBACK_DIRECTORY

    if ".." in category.split("/"):
        logger.warning("categorizer fallback (path traversal): %r", snippet)
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
        *,
        approval_registry=None,
        proposal_emitter=None,
        main_inference=None,
    ) -> str:
        """Choose the best vault directory for an article.

        Args:
            title: article title
            body: full article body
            vault_path: path to the vault root
            approval_registry: optional ApprovalRegistry for batch-fallback
                user prompting. When None, batch unavailability silently
                falls back to the default category.
            proposal_emitter: optional callable (message) -> None used to
                send BatchFallbackProposal to the active client.
            main_inference: optional InferenceClient used when the user
                chooses "main" on a batch-fallback prompt.

        Returns:
            directory path relative to vault root (e.g., "Research")
        """
        messages = self._build_messages(title, body, vault_path)

        try:
            result = await self.inference.complete(messages)
            category = self._parse_category(result)
            # Surface when the model mints a directory not in the existing list.
            # This is the dominant vault-entropy source: small models invent near-
            # duplicate siblings (Research vs research vs LLMs vs llm-research).
            existing = self._list_directories(vault_path)
            if category != FALLBACK_DIRECTORY and category not in existing:
                logger.warning(
                    "categorizer minted new directory %r (existing: %r)",
                    category, existing,
                )
            return category
        except BatchUnavailableError:
            return await self._handle_batch_unavailable(
                messages,
                title,
                approval_registry,
                proposal_emitter,
                main_inference,
            )
        except Exception:
            logger.exception("Categorization failed, falling back to %s", FALLBACK_DIRECTORY)
            return self._default_category()

    def _build_messages(self, title: str, body: str, vault_path: Path) -> list[dict]:
        directories = self._list_directories(vault_path)
        user_prompt = build_categorization_prompt(title, body, directories)
        return [
            {"role": "system", "content": CATEGORIZATION_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    def _parse_category(self, result) -> str:
        return parse_category_response(result.content or "")

    def _default_category(self) -> str:
        return FALLBACK_DIRECTORY

    async def _handle_batch_unavailable(
        self,
        messages: list[dict],
        title: str,
        approval_registry,
        proposal_emitter,
        main_inference,
    ) -> str:
        if approval_registry is None or proposal_emitter is None:
            logger.warning(
                "Batch backend unavailable and no approval pathway wired; "
                "falling back to %s",
                FALLBACK_DIRECTORY,
            )
            return self._default_category()

        context = f"categorizing {title!r}"
        proposal_id = approval_registry.create_proposal(
            kind="batch_fallback",
            rationale="batch backend unavailable",
            caller="categorizer",
            context=context,
        )
        proposal_msg = BatchFallbackProposal(
            proposal_id=proposal_id,
            caller="categorizer",
            context=context,
            original_request={"messages": messages, "reasoning": "off"},
        )
        proposal_emitter(proposal_msg)

        proposal = approval_registry.get(proposal_id)
        await proposal.event.wait()

        if proposal.status == "declined":
            return self._default_category()

        choice = proposal.approval_choice
        try:
            if choice == "retry":
                result = await self.inference.complete(messages)
                return self._parse_category(result)
            if choice == "main" and main_inference is not None:
                result = await main_inference.complete(messages)
                return self._parse_category(result)
        except Exception:
            logger.exception(
                "Batch-fallback follow-up call failed, falling back to %s",
                FALLBACK_DIRECTORY,
            )
        return self._default_category()

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
