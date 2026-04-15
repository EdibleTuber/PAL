"""Consolidator -- synthesize one new grounded article from N existing ones.

Parallel to pal.compiler.Compiler, but with different topology:
  - Compiler: 1 raw summary -> 1 article (create or merge into existing).
  - Consolidator: N existing articles -> 1 new article at a caller-specified path.

Never merges into existing articles (target must not exist). Does not use
the categorizer (the caller names the target path). Source cleanup is
explicitly out of scope; callers use propose_reorg afterwards.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pal.article import validate_compiled_truth
from pal.frontmatter import parse_frontmatter

logger = logging.getLogger(__name__)


class Consolidator:
    def __init__(
        self,
        vault_path: Path,
        wiki,              # WikiManager
        inference,         # InferenceClient
        prompt_builder,    # SystemPromptBuilder
    ) -> None:
        self.vault_path = vault_path
        self.wiki = wiki
        self.inference = inference
        self.prompt_builder = prompt_builder

    async def consolidate(
        self,
        source_paths: list[str],
        target_path: str,
        target_title: str,
    ) -> dict[str, Any]:
        """Synthesize `target_path` from the content of `source_paths`.

        Validation-phase return shape (Task 3):
          status: invalid_path | not_found | error
          target_path: str (echo of input)
          reason: str
          vault_exists: bool (True iff target already exists on disk)

        Task 4 extends this with:
          status: ok | insufficient
          article_path_rel: str (ok branch only)
        """
        # Validate target
        bad = self._validate_target(target_path)
        if bad is not None:
            return {"status": "invalid_path", "target_path": target_path, "reason": bad, "vault_exists": False}

        target_full = self.vault_path / target_path
        if target_full.exists():
            return {
                "status": "invalid_path",
                "target_path": target_path,
                "reason": f"target already exists: {target_path}",
                "vault_exists": True,
            }

        # Validate each source exists
        source_bodies: list[tuple[str, str]] = []  # populated here for Task 4 inference block
        for src in source_paths:
            if ".." in src.split("/") or src.startswith("/"):
                return {
                    "status": "invalid_path",
                    "target_path": target_path,
                    "reason": f"invalid source path: {src}",
                    "vault_exists": False,
                }
            src_full = self.vault_path / src
            if not src_full.exists():
                return {
                    "status": "not_found",
                    "target_path": target_path,
                    "reason": f"source not found: {src}",
                    "vault_exists": False,
                }
            text = src_full.read_text()
            _meta, body = parse_frontmatter(text)
            source_bodies.append((src, body))

        # Build the grounded system + user prompt
        base = self.prompt_builder.build()
        system_prompt = (
            f"{base}\n\n"
            "You are consolidating multiple existing wiki articles into ONE new article. RULES:\n"
            "- Use ONLY information from the SOURCES below. Do NOT add facts that aren't in sources.\n"
            "- Deduplicate overlap across sources; do not repeat the same fact twice.\n"
            "- For every substantive claim, cite the specific source path inline, e.g. (from Security/a.md).\n"
            "- If the combined source material lacks sufficient detail for a grounded article, respond "
            "with exactly: INSUFFICIENT: <one-sentence reason>\n\n"
            "Required sections: ## Overview, ## Key Concepts\n"
            "Optional sections (include if relevant): ## Usage, ## Configuration, ## Gotchas, ## Related"
        )

        labeled_sources = "\n\n".join(
            f"### SOURCE: {path}\n{body.strip()}" for path, body in source_bodies
        )
        user_prompt = (
            f"Target title: {target_title}\n"
            f"Target path: {target_path}\n\n"
            f"SOURCES ({len(source_bodies)}):\n\n{labeled_sources}"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            result = await self.inference.complete(messages, reasoning="off")
        except Exception as exc:
            logger.exception("Consolidate inference failed: %s", exc)
            return {
                "status": "error",
                "target_path": target_path,
                "reason": f"inference failed: {type(exc).__name__}: {exc}",
                "vault_exists": False,
            }

        content = (getattr(result, "content", "") or "").strip()
        if content.startswith("INSUFFICIENT:"):
            reason = content[len("INSUFFICIENT:"):].strip() or "model reported insufficient material"
            return {
                "status": "insufficient",
                "target_path": target_path,
                "reason": reason,
                "vault_exists": False,
            }

        missing = validate_compiled_truth(content)
        if missing:
            logger.warning(
                "Consolidated article missing sections (target=%s): %s",
                target_path, ", ".join(missing),
            )

        tags = ["consolidated"]
        try:
            self.wiki.write_article(target_path, target_title, content, tags=tags)
            self.wiki.git_commit(
                f"consolidate {len(source_bodies)} sources -> {target_path}"
            )
        except Exception as exc:
            logger.exception("Consolidate write failed: %s", exc)
            return {
                "status": "error",
                "target_path": target_path,
                "reason": f"write failed: {type(exc).__name__}: {exc}",
                "vault_exists": (self.vault_path / target_path).exists(),
            }

        return {
            "status": "ok",
            "target_path": target_path,
            "article_path_rel": target_path,
            "vault_exists": (self.vault_path / target_path).exists(),
        }

    def _validate_target(self, target_path: str) -> str | None:
        if not target_path:
            return "target_path is required"
        if target_path.startswith("/"):
            return f"absolute paths not allowed: {target_path}"
        parts = Path(target_path).parts
        if ".." in parts:
            return f"path traversal not allowed: {target_path}"
        if parts and parts[0].startswith("_"):
            return f"system directory not allowed: {target_path}"
        if target_path.startswith("raw/"):
            return f"raw/ is for unpromoted material; target must be a promoted category (got {target_path})"
        if not target_path.endswith(".md"):
            return f"target must be a .md file: {target_path}"
        return None
