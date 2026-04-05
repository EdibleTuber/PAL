"""WikiManager — read, write, and manage markdown articles in the vault.

All articles are markdown files with YAML frontmatter. The vault is
organized into topic directories. System directories (prefixed with _)
are managed by PAL and should not appear in user-facing article lists.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

from pal.frontmatter import parse_frontmatter, serialize_frontmatter

logger = logging.getLogger(__name__)

SYSTEM_DIRS = {"_index.md", "_wisdom", "_learning", "_profile"}


class WikiManager:
    def __init__(self, vault_path: Path) -> None:
        self.vault_path = vault_path

    def init_vault(self) -> None:
        """Create vault directory structure if it doesn't exist."""
        self.vault_path.mkdir(parents=True, exist_ok=True)
        index_path = self.vault_path / "_index.md"
        if not index_path.exists():
            index_path.write_text(
                "---\ntitle: Vault Index\n---\n\n"
                "# Vault Index\n\n"
                "_Auto-maintained by PAL. Lists all articles in the vault._\n"
            )

    def write_article(
        self,
        path: str,
        title: str,
        body: str,
        tags: list[str] | None = None,
    ) -> Path:
        """Write or update a markdown article in the vault.

        Creates parent directories as needed. Preserves the original
        'created' timestamp on updates and sets 'updated'.
        """
        full_path = self.vault_path / path
        full_path.parent.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        # Preserve created timestamp on update
        created = now
        if full_path.exists():
            existing_meta, _ = parse_frontmatter(full_path.read_text())
            created = existing_meta.get("created", now)

        meta: dict = {
            "title": title,
            "created": created,
            "updated": now,
        }
        if tags:
            meta["tags"] = tags

        content = serialize_frontmatter(meta, body)
        full_path.write_text(content)
        logger.info("Wrote article: %s", path)
        return full_path

    def read_article(self, path: str) -> tuple[dict, str]:
        """Read an article, returning (frontmatter_dict, body).

        Raises FileNotFoundError if the article doesn't exist.
        """
        full_path = self.vault_path / path
        if not full_path.exists():
            raise FileNotFoundError(f"Article not found: {path}")
        return parse_frontmatter(full_path.read_text())

    def list_articles(self) -> list[dict]:
        """List all non-system articles in the vault.

        Returns a list of dicts with 'path' and 'title' keys.
        """
        articles = []
        for md_file in sorted(self.vault_path.rglob("*.md")):
            rel = md_file.relative_to(self.vault_path)
            # Skip system files/dirs
            if any(part.startswith("_") for part in rel.parts):
                continue
            meta, _ = parse_frontmatter(md_file.read_text())
            articles.append({
                "path": str(rel),
                "title": meta.get("title", rel.stem),
            })
        return articles
