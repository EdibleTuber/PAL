"""One-off backfill that regenerates bad titles on compiled articles.

Walks the vault, finds articles whose titles match the bad-title heuristic,
asks the inference client to generate a clean title from the article's
compiled_truth, and writes the update preserving the Article's timeline
and other frontmatter fields. Rebuilds the index once at the end.
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path

from pal.article import parse_article, serialize_article, Article
from pal.frontmatter import parse_frontmatter
from pal.title_cleanup import is_bad_title, regenerate_title
from pal.wiki import WikiManager

logger = logging.getLogger(__name__)


@dataclass
class BackfillReport:
    processed: int = 0
    updated: int = 0
    skipped_clean: int = 0
    skipped_error: int = 0
    changes: list[tuple[str, str, str]] = field(default_factory=list)  # (path, old_title, new_title)


async def backfill_titles(
    vault: Path,
    wiki: WikiManager,
    inference,
    apply: bool,
) -> BackfillReport:
    """Walk compiled articles, regenerate bad titles, write updates.

    Args:
        vault: Vault root.
        wiki: WikiManager for this vault.
        inference: Inference client with an async .complete() method.
        apply: If True, write changes. If False, dry-run (count only).

    Returns:
        BackfillReport with counts and per-article change list.
    """
    report = BackfillReport()

    for md_file in sorted(vault.rglob("*.md")):
        rel = md_file.relative_to(vault)
        # Skip system directories (anything starting with _).
        if any(part.startswith("_") for part in rel.parts):
            continue

        text = md_file.read_text()
        meta, _ = parse_frontmatter(text)
        old_title = meta.get("title", "")

        if not is_bad_title(old_title):
            report.skipped_clean += 1
            continue

        report.processed += 1

        # Re-parse as an Article so we can feed compiled_truth to the model.
        try:
            article = parse_article(text)
        except Exception as exc:
            logger.warning("backfill: cannot parse %s as Article: %s", rel, exc)
            report.skipped_error += 1
            continue

        try:
            new_title = await regenerate_title(
                content=article.compiled_truth,
                inference=inference,
            )
        except Exception as exc:
            logger.warning("backfill: inference error for %s: %s", rel, exc)
            report.skipped_error += 1
            continue

        if not new_title:
            report.skipped_error += 1
            continue

        report.changes.append((str(rel), old_title, new_title))
        report.updated += 1

        if not apply:
            continue

        # Update the article's title and rewrite, preserving timeline and
        # all other Article fields (which WikiManager.write_article would drop).
        updated_article = Article(
            meta={**article.meta, "title": new_title},
            compiled_truth=article.compiled_truth,
            timeline=article.timeline,
        )
        md_file.write_text(serialize_article(updated_article))

    if apply and report.updated > 0:
        wiki.rebuild_index()

    return report
