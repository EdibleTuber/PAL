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
    ) -> None:
        self.vault_path = vault_path
        self.wiki = wiki
        self.compiler = compiler

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
