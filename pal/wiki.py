"""WikiManager — read, write, and manage markdown articles in the vault.

All articles are markdown files with YAML frontmatter. The vault is
organized into topic directories. System directories (prefixed with _)
are managed by PAL and should not appear in user-facing article lists.
"""
import logging
import subprocess
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

    def rebuild_index(self) -> None:
        """Rebuild _index.md from all articles in the vault.

        Groups articles by their top-level directory, with a summary line
        for each article showing path and title.
        """
        articles = self.list_articles()
        groups: dict[str, list[dict]] = {}
        for article in articles:
            parts = article["path"].split("/")
            group = parts[0] if len(parts) > 1 else "Ungrouped"
            groups.setdefault(group, []).append(article)

        lines = ["# Vault Index\n"]
        lines.append("_Auto-maintained by PAL. Lists all articles in the vault._\n")

        if not groups:
            lines.append("_No articles yet._\n")
        else:
            for group_name in sorted(groups):
                lines.append(f"\n## {group_name}\n")
                for article in sorted(groups[group_name], key=lambda a: a["path"]):
                    lines.append(f"- [[{article['path']}|{article['title']}]]")
            lines.append("")

        body = "\n".join(lines)
        meta = {"title": "Vault Index"}
        index_path = self.vault_path / "_index.md"
        index_path.write_text(serialize_frontmatter(meta, body))
        logger.info("Rebuilt vault index (%d articles)", len(articles))

    def git_init(self) -> None:
        """Initialize a git repo in the vault if one doesn't exist."""
        if not (self.vault_path / ".git").exists():
            subprocess.run(
                ["git", "init"],
                cwd=self.vault_path,
                capture_output=True,
                check=True,
            )
            # Initial commit so we have a HEAD
            subprocess.run(
                ["git", "add", "."],
                cwd=self.vault_path,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "Initial vault", "--allow-empty"],
                cwd=self.vault_path,
                capture_output=True,
                check=True,
            )
            logger.info("Initialized git repo in vault")

    def git_commit(self, message: str) -> None:
        """Stage all changes in the vault and commit.

        No-op if there are no changes to commit.
        """
        subprocess.run(
            ["git", "add", "."],
            cwd=self.vault_path,
            capture_output=True,
            check=True,
        )
        # Check if there's anything to commit
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.vault_path,
            capture_output=True,
            text=True,
        )
        if not result.stdout.strip():
            return
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=self.vault_path,
            capture_output=True,
            check=True,
        )
        logger.info("Committed: %s", message)
