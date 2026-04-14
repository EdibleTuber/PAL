"""Compiler -- promote raw summaries into grounded wiki articles.

Extracted from pal.daemon so both the /compile slash command and the
chat compile tools call the same implementation.
"""
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pal.article import (
    Article,
    append_timeline_entry,
    find_existing_article,
    parse_article,
    serialize_article,
    validate_compiled_truth,
)
from pal.frontmatter import parse_frontmatter
from pal.archive import archive_raw_files, MAX_SLUG_BYTES

logger = logging.getLogger(__name__)


class Compiler:
    def __init__(
        self,
        vault_path: Path,
        wiki,           # WikiManager
        inference,      # InferenceClient
        categorizer,    # Categorizer
        prompt_builder, # SystemPromptBuilder
    ) -> None:
        self.vault_path = vault_path
        self.wiki = wiki
        self.inference = inference
        self.categorizer = categorizer
        self.prompt_builder = prompt_builder

    async def compile_one(self, summary_path: str) -> dict[str, Any]:
        """Compile a single summary into a wiki article.

        Returns a dict with status, title, article_path_rel, compiled_truth,
        reason. Status values: ok, merged, insufficient, not_found,
        invalid_path, error.
        """
        # Path traversal guard
        if ".." in summary_path.split("/") or summary_path.startswith("/"):
            return {"status": "invalid_path", "reason": f"Invalid path: {summary_path}"}

        full_path = self.vault_path / summary_path
        if not full_path.exists():
            return {"status": "not_found", "reason": f"File not found: {summary_path}"}

        # Resolve + boundary check
        try:
            resolved = full_path.resolve()
            vault_resolved = self.vault_path.resolve()
            if not str(resolved).startswith(str(vault_resolved) + "/"):
                return {"status": "invalid_path", "reason": f"Invalid path: {summary_path}"}
        except Exception:
            return {"status": "invalid_path", "reason": f"Invalid path: {summary_path}"}

        summary_meta, summary_body = parse_frontmatter(full_path.read_text())

        title = summary_meta.get("title", full_path.stem)
        source_url = summary_meta.get("source_url", "")
        source_hash = summary_meta.get("source_hash", "")

        # Step 1: Categorize
        category = await self.categorizer.categorize(
            title=title,
            body=summary_body,
            vault_path=self.vault_path,
        )

        # Step 2: Topic matching
        all_articles = self.wiki.list_articles()
        existing_match = await find_existing_article(
            summary_title=title,
            summary_preview=summary_body[:400],
            category=category,
            articles=all_articles,
            inference=self.inference,
        )

        # Step 3: Build prompts
        base_prompt = self.prompt_builder.build()

        if existing_match:
            # Merge compile
            existing_text = (self.vault_path / existing_match["path"]).read_text()
            existing_article = parse_article(existing_text)

            timeline_context = "\n".join(
                f"- {e.date} {e.source_label}: {e.summary[:200]}"
                for e in existing_article.timeline
            )

            system_prompt = (
                f"{base_prompt}\n\n"
                "You are updating a wiki article with new information. "
                "Rewrite the compiled truth sections to incorporate the new source material. "
                "Keep the same section structure. Do not drop existing knowledge unless "
                "the new source directly contradicts it.\n\n"
                "Required sections: ## Overview, ## Key Concepts\n"
                "Optional sections (include if relevant): ## Usage, ## Configuration, "
                "## Gotchas, ## Related\n\n"
                "Use ONLY information from the existing article and the new source material. "
                "Do NOT add facts not present in either."
            )

            user_prompt = (
                f"CURRENT COMPILED TRUTH:\n\n{existing_article.compiled_truth.strip()}\n\n"
                f"PREVIOUS SOURCES:\n{timeline_context}\n\n"
                f"NEW SOURCE MATERIAL:\n"
                f"Title: {title}\n"
                f"Source URL: {source_url}\n\n"
                f"{summary_body.strip()}\n\n"
                "---\n\n"
                "Rewrite the compiled truth incorporating the new information."
            )
        else:
            # First compile
            existing_article = None

            system_prompt = (
                f"{base_prompt}\n\n"
                "You are compiling a wiki article from source material. RULES:\n"
                "- Use ONLY information from the SOURCE MATERIAL below.\n"
                "- Do NOT add facts that aren't in the source.\n"
                "- If the source lacks sufficient detail, respond with exactly: "
                "INSUFFICIENT: <one-sentence reason>\n\n"
                "Required sections: ## Overview, ## Key Concepts\n"
                "Optional sections (include if relevant): ## Usage, ## Configuration, "
                "## Gotchas, ## Related"
            )

            user_prompt = (
                f"SOURCE MATERIAL (reviewed summary):\n\n"
                f"Title: {title}\n"
                f"Source URL: {source_url}\n\n"
                f"{summary_body.strip()}\n\n"
                f"---\n\n"
                f"Write a grounded wiki article based on this source material."
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            result = await self.inference.complete(messages, reasoning="off")
            compiled_truth = result.content or ""
        except Exception as exc:
            logger.exception("Compile inference failed: %s", exc)
            return {"status": "error", "title": title, "reason": f"Compile failed: {exc}"}

        if compiled_truth.strip().startswith("INSUFFICIENT:"):
            return {
                "status": "insufficient",
                "title": title,
                "reason": compiled_truth.strip(),
            }

        # Validate required sections
        issues = validate_compiled_truth(compiled_truth)
        if issues:
            logger.warning("Compiled truth validation issues: %s", issues)

        # Build article
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        if existing_article:
            article = Article(
                meta=dict(existing_article.meta),
                compiled_truth=compiled_truth.strip() + "\n",
                timeline=list(existing_article.timeline),
            )
            article.meta["updated"] = now
            article.meta["compiled_at"] = now
        else:
            article = Article(
                meta={
                    "title": title,
                    "created": now,
                    "updated": now,
                    "compiled_at": now,
                    "status": "compiled",
                    "sources": [],
                },
                compiled_truth=compiled_truth.strip() + "\n",
                timeline=[],
            )

        # Append timeline entry
        article = append_timeline_entry(
            article=article,
            source_url=source_url,
            source_hash=source_hash,
            summary=summary_body.strip(),
        )

        # Determine save path
        if existing_match:
            article_path_rel = existing_match["path"]
            article_full_path = self.vault_path / article_path_rel
        else:
            slug = title.lower().replace("_", "-").replace(" ", "-")
            slug = "".join(c for c in slug if c.isalnum() or c == "-").strip("-") or "untitled"
            if len(slug.encode("utf-8")) > MAX_SLUG_BYTES:
                h = hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
                truncated = (
                    slug.encode("utf-8")[: MAX_SLUG_BYTES - 9]
                    .decode("utf-8", errors="ignore")
                    .rstrip("-")
                )
                slug = f"{truncated}-{h}"
            target_dir = self.vault_path / category
            target_dir.mkdir(parents=True, exist_ok=True)
            article_path_rel = f"{category}/{slug}.md"
            article_full_path = target_dir / f"{slug}.md"

        article_full_path.write_text(serialize_article(article))
        logger.info("Compiled %s -> %s", summary_path, article_path_rel)

        # Rebuild index and commit
        self.wiki.rebuild_index()
        self.wiki.git_init()
        self.wiki.git_commit(f"compile: {title}")

        # Archive raw intermediates
        source_raw = summary_meta.get("source_raw", "")
        archive_raw_files(self.vault_path, raw_path=source_raw, summary_path=summary_path)
        self.wiki.git_commit(f"archive: {title}")

        return {
            "status": "merged" if existing_match else "ok",
            "title": title,
            "article_path_rel": article_path_rel,
            "compiled_truth": compiled_truth.strip(),
        }
