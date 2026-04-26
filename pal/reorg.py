"""Reorganizer -- consent-gated vault reorg operations.

Owns validation, link-reference scanning, and execution of move/merge
operations. Pure of protocol concerns; a separate layer (tools.py)
handles proposal/approval lifecycle.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class Reorganizer:
    def __init__(
        self,
        vault_path: Path,
        wiki,              # WikiManager or None (tests may pass None)
        compiler,          # Compiler or None (only needed for merge ops)
        retrieval=None,    # RetrievalClient | None
    ) -> None:
        self.vault_path = vault_path
        self.wiki = wiki
        self.compiler = compiler
        self.retrieval = retrieval

    # ---- single-op primitive ----

    def move_single(self, src: str, dst: str) -> None:
        """Rename a single vault file. Raises on missing src, existing dst,
        or paths inside system directories (raw/ or underscore-prefixed).
        """
        def _is_top_level_system(p: str) -> bool:
            parts = p.split("/")
            return parts[0] == "raw" or (parts[0].startswith("_") if parts[0] else False)

        if _is_top_level_system(src) or _is_top_level_system(dst):
            raise ValueError(f"system directory: {src} or {dst}")

        src_path = self.vault_path / src
        dst_path = self.vault_path / dst

        if not src_path.exists():
            raise FileNotFoundError(f"source not found: {src}")
        if dst_path.exists():
            raise FileExistsError(f"destination exists: {dst}")

        dst_path.parent.mkdir(parents=True, exist_ok=True)
        src_path.rename(dst_path)
        logger.info("move_single: %s -> %s", src, dst)

    # ---- validation ----

    def validate_operations(self, operations: list[dict]) -> list[str]:
        """Return list of validation errors. Empty list means valid."""
        errors: list[str] = []
        if not operations:
            errors.append("operations list is empty")
            return errors

        # Simulate execution state: track which srcs are "consumed"
        # (moved/merged away) and which dsts are "produced" as we walk.
        consumed: set[str] = set()
        produced: set[str] = set()
        seen_srcs: set[str] = set()
        seen_dsts: set[str] = set()

        for idx, op in enumerate(operations):
            op_type = op.get("type")
            src = op.get("src", "")
            dst = op.get("dst", "")
            prefix = f"op {idx+1} ({op_type})"

            if op_type not in ("move", "merge"):
                errors.append(f"{prefix}: unknown type {op_type!r}")
                continue

            if not self._path_inside_vault(src) or not self._path_inside_vault(dst):
                errors.append(f"{prefix}: path outside vault (src={src!r} dst={dst!r})")
                continue
            if self._is_system_path(src) or self._is_system_path(dst):
                errors.append(f"{prefix}: system/underscore path not allowed")
                continue
            if src == dst:
                errors.append(f"{prefix}: src and dst are identical")
                continue

            if src in seen_srcs:
                errors.append(f"{prefix}: duplicate src in batch: {src}")
                continue
            seen_srcs.add(src)
            if dst in seen_dsts:
                errors.append(f"{prefix}: duplicate dst in batch: {dst}")
                continue
            seen_dsts.add(dst)

            src_exists_now = (src in produced) or (
                (self.vault_path / src).exists() and src not in consumed
            )
            if not src_exists_now:
                errors.append(f"{prefix}: src does not exist: {src}")
                continue

            dst_exists_now = (dst in produced) or (
                (self.vault_path / dst).exists() and dst not in consumed
            )
            if op_type == "move":
                if dst_exists_now:
                    errors.append(f"{prefix}: dst already exists (collision): {dst}")
                    continue
                consumed.add(src)
                produced.add(dst)
            else:  # merge
                if not dst_exists_now:
                    errors.append(f"{prefix}: dst does not exist for merge: {dst}")
                    continue
                consumed.add(src)

        return errors

    def _path_inside_vault(self, rel: str) -> bool:
        if rel.startswith("/"):
            return False
        if ".." in rel.split("/"):
            return False
        return True

    def _is_system_path(self, rel: str) -> bool:
        parts = Path(rel).parts
        return any(p.startswith("_") for p in parts)

    def _collect_touched_paths(self, per_op: list[dict]) -> list[str]:
        """Absolute paths of every src/dst from successful ops."""
        touched: list[str] = []
        seen: set[str] = set()
        for r in per_op:
            if r.get("status") != "ok":
                continue
            for key in ("src", "dst"):
                rel = r.get(key, "")
                if not rel:
                    continue
                full = str((self.vault_path / rel).resolve())
                if full not in seen:
                    seen.add(full)
                    touched.append(full)
        return touched

    # ---- reference scanning ----

    _LINK_PATTERN_TEMPLATE = r"\]\(\s*{}\s*\)"

    def count_references(self, paths: list[str]) -> int:
        """Count markdown-link references across the vault to any of the
        given paths. Excludes raw/archived/."""
        total = 0
        for md_file in self.vault_path.rglob("*.md"):
            rel = md_file.relative_to(self.vault_path)
            if len(rel.parts) >= 2 and rel.parts[0] == "raw" and rel.parts[1] == "archived":
                continue
            try:
                content = md_file.read_text(errors="replace")
            except OSError:
                continue
            for path in paths:
                pattern = self._LINK_PATTERN_TEMPLATE.format(re.escape(path))
                total += len(re.findall(pattern, content))
        return total

    # ---- execution ----

    def execute_operations(self, operations: list[dict]) -> list[dict]:
        """Execute a batch of operations sequentially. Returns per-op results.

        Sync variant -- only supports move ops. Merge ops must go through
        execute_operations_async (added in R6)."""
        results: list[dict] = []
        for op in operations:
            op_type = op.get("type")
            if op_type == "move":
                results.append(self._execute_move(op))
            elif op_type == "merge":
                results.append({
                    "op": "merge",
                    "src": op.get("src", ""),
                    "dst": op.get("dst", ""),
                    "status": "failed",
                    "reason": "merge requires execute_operations_async",
                    "references_rewritten": 0,
                })
            else:
                results.append({
                    "op": str(op_type),
                    "src": op.get("src", ""),
                    "dst": op.get("dst", ""),
                    "status": "failed",
                    "reason": f"unknown op type: {op_type!r}",
                    "references_rewritten": 0,
                })
        if self.wiki is not None:
            try:
                self.wiki.rebuild_index()
            except Exception as exc:
                logger.warning("rebuild_index failed after reorg: %s", exc)
        return results

    def _execute_move(self, op: dict) -> dict:
        src = op.get("src", "")
        dst = op.get("dst", "")
        src_full = self.vault_path / src
        dst_full = self.vault_path / dst

        if not src_full.exists():
            return {
                "op": "move", "src": src, "dst": dst,
                "status": "failed",
                "reason": f"src does not exist: {src}",
                "references_rewritten": 0,
            }
        if dst_full.exists():
            return {
                "op": "move", "src": src, "dst": dst,
                "status": "failed",
                "reason": f"dst already exists: {dst}",
                "references_rewritten": 0,
            }

        try:
            refs = self._rewrite_references(src, dst)
        except Exception as exc:
            return {
                "op": "move", "src": src, "dst": dst,
                "status": "failed",
                "reason": f"link rewrite failed: {exc}",
                "references_rewritten": 0,
            }

        try:
            dst_full.parent.mkdir(parents=True, exist_ok=True)
            src_full.rename(dst_full)
        except Exception as exc:
            return {
                "op": "move", "src": src, "dst": dst,
                "status": "failed",
                "reason": f"rename failed: {exc}",
                "references_rewritten": refs,
            }

        if self.wiki is not None:
            try:
                self.wiki.git_commit(f"reorg: move {src} -> {dst}")
            except Exception as exc:
                logger.warning("git commit failed after move: %s", exc)

        return {
            "op": "move", "src": src, "dst": dst,
            "status": "ok",
            "references_rewritten": refs,
        }

    async def execute_operations_async(self, operations: list[dict]) -> list[dict]:
        """Execute a batch of operations sequentially. Returns per-op
        results. Async because merge ops call the LLM via Compiler."""
        results: list[dict] = []
        for op in operations:
            op_type = op.get("type")
            if op_type == "move":
                results.append(self._execute_move(op))
            elif op_type == "merge":
                results.append(await self._execute_merge(op))
            else:
                results.append({
                    "op": str(op_type),
                    "src": op.get("src", ""),
                    "dst": op.get("dst", ""),
                    "status": "failed",
                    "reason": f"unknown op type: {op_type!r}",
                    "references_rewritten": 0,
                })
        if self.wiki is not None:
            try:
                self.wiki.rebuild_index()
            except Exception as exc:
                logger.warning("rebuild_index failed after reorg: %s", exc)
        if self.retrieval is not None:
            touched = self._collect_touched_paths(results)
            if touched:
                reindex_result = await self.retrieval.trigger_reindex(paths=touched)
                if results and reindex_result is not None:
                    results[0]["_reindex"] = reindex_result
        return results

    async def _execute_merge(self, op: dict) -> dict:
        src = op.get("src", "")
        dst = op.get("dst", "")
        src_full = self.vault_path / src
        dst_full = self.vault_path / dst

        if not src_full.exists():
            return {"op": "merge", "src": src, "dst": dst,
                    "status": "failed",
                    "reason": f"src does not exist: {src}",
                    "references_rewritten": 0}
        if not dst_full.exists():
            return {"op": "merge", "src": src, "dst": dst,
                    "status": "failed",
                    "reason": f"dst does not exist: {dst}",
                    "references_rewritten": 0}
        if self.compiler is None:
            return {"op": "merge", "src": src, "dst": dst,
                    "status": "failed",
                    "reason": "compiler not available",
                    "references_rewritten": 0}

        from agent_core.utils.frontmatter import parse_frontmatter
        src_meta, src_body = parse_frontmatter(src_full.read_text())
        src_title = src_meta.get("title", src_full.stem)

        merge_result = await self.compiler.merge_into_existing(
            new_content=src_body,
            new_title=src_title,
            existing_article_path=dst,
        )
        if merge_result.get("status") != "merged":
            return {
                "op": "merge", "src": src, "dst": dst,
                "status": merge_result.get("status", "failed"),
                "reason": merge_result.get("reason", "merge failed"),
                "references_rewritten": 0,
            }

        try:
            refs = self._rewrite_references(src, dst)
        except Exception as exc:
            return {"op": "merge", "src": src, "dst": dst,
                    "status": "failed",
                    "reason": f"link rewrite failed after merge: {exc}",
                    "references_rewritten": 0}

        archived_dir = self.vault_path / "raw" / "archived"
        archived_dir.mkdir(parents=True, exist_ok=True)
        archive_dest = archived_dir / f"{src_full.stem}.archived.md"
        if archive_dest.exists():
            import hashlib
            h = hashlib.sha1(src.encode("utf-8")).hexdigest()[:8]
            archive_dest = archived_dir / f"{src_full.stem}.{h}.archived.md"
        src_full.rename(archive_dest)

        if self.wiki is not None:
            try:
                self.wiki.git_commit(f"reorg: merge {src} into {dst}")
            except Exception as exc:
                logger.warning("git commit failed after merge: %s", exc)

        return {"op": "merge", "src": src, "dst": dst,
                "status": "ok",
                "references_rewritten": refs}

    def _rewrite_references(self, old_path: str, new_path: str) -> int:
        """Rewrite `](old_path)` occurrences to `](new_path)` across the
        vault (excluding raw/archived/). Returns the number of rewrites."""
        pattern = re.compile(self._LINK_PATTERN_TEMPLATE.format(re.escape(old_path)))
        replacement = f"]({new_path})"
        total = 0
        for md_file in self.vault_path.rglob("*.md"):
            rel = md_file.relative_to(self.vault_path)
            if len(rel.parts) >= 2 and rel.parts[0] == "raw" and rel.parts[1] == "archived":
                continue
            try:
                content = md_file.read_text(errors="replace")
            except OSError:
                continue
            new_content, n = pattern.subn(replacement, content)
            if n > 0:
                md_file.write_text(new_content)
                total += n
        return total
