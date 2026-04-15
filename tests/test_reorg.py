from pathlib import Path

import pytest

from pal.reorg import Reorganizer


def _seed_vault(tmp_path: Path, files: dict[str, str]) -> Path:
    """Write a set of markdown files under tmp_path. Returns tmp_path."""
    for rel, content in files.items():
        full = tmp_path / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    return tmp_path


def test_validate_rejects_missing_src(tmp_path):
    vault = _seed_vault(tmp_path, {})
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    errors = reorg.validate_operations(
        [{"type": "move", "src": "does-not-exist.md", "dst": "new.md"}]
    )
    assert errors
    assert any("does-not-exist" in e for e in errors)


def test_validate_rejects_dst_collision(tmp_path):
    vault = _seed_vault(tmp_path, {"A.md": "---\ntitle: A\n---\n", "B.md": "---\ntitle: B\n---\n"})
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    errors = reorg.validate_operations(
        [{"type": "move", "src": "A.md", "dst": "B.md"}]
    )
    assert errors
    assert any("exists" in e.lower() or "collision" in e.lower() for e in errors)


def test_validate_rejects_path_traversal(tmp_path):
    vault = _seed_vault(tmp_path, {"A.md": "---\ntitle: A\n---\n"})
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    errors = reorg.validate_operations(
        [{"type": "move", "src": "A.md", "dst": "../escape.md"}]
    )
    assert errors
    assert any("escape" in e.lower() or "invalid" in e.lower() or "outside" in e.lower() for e in errors)


def test_validate_rejects_system_path(tmp_path):
    vault = _seed_vault(tmp_path, {"A.md": "---\ntitle: A\n---\n"})
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    errors = reorg.validate_operations(
        [{"type": "move", "src": "A.md", "dst": "_config/settings.md"}]
    )
    assert errors
    assert any("system" in e.lower() or "underscore" in e.lower() for e in errors)


def test_validate_rejects_self_move(tmp_path):
    vault = _seed_vault(tmp_path, {"A.md": "---\ntitle: A\n---\n"})
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    errors = reorg.validate_operations(
        [{"type": "move", "src": "A.md", "dst": "A.md"}]
    )
    assert errors


def test_validate_rejects_duplicate_src_in_batch(tmp_path):
    vault = _seed_vault(tmp_path, {"A.md": "---\ntitle: A\n---\n"})
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    errors = reorg.validate_operations([
        {"type": "move", "src": "A.md", "dst": "B.md"},
        {"type": "move", "src": "A.md", "dst": "C.md"},
    ])
    assert errors
    assert any("duplicate" in e.lower() for e in errors)


def test_validate_simulates_execution_state(tmp_path):
    """If op 1 moves A to B, op 2 can reference B (it's produced by op 1)."""
    vault = _seed_vault(tmp_path, {"A.md": "---\ntitle: A\n---\n"})
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    errors = reorg.validate_operations([
        {"type": "move", "src": "A.md", "dst": "B.md"},
        {"type": "move", "src": "B.md", "dst": "C.md"},
    ])
    assert errors == []


def test_validate_passes_valid_batch(tmp_path):
    vault = _seed_vault(tmp_path, {
        "A.md": "---\ntitle: A\n---\n",
        "C.md": "---\ntitle: C\n---\n",
    })
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    errors = reorg.validate_operations([
        {"type": "move", "src": "A.md", "dst": "AI-Agents/renamed-a.md"},
    ])
    assert errors == []


def test_count_references_finds_markdown_links(tmp_path):
    vault = _seed_vault(tmp_path, {
        "A.md": "---\ntitle: A\n---\n\nLink: [x](target.md)\n",
        "B.md": "---\ntitle: B\n---\n\nAnother [y](target.md)\n",
        "C.md": "---\ntitle: C\n---\n\nNo link here\n",
        "target.md": "---\ntitle: Target\n---\n\n",
    })
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    count = reorg.count_references(["target.md"])
    assert count == 2


def test_count_references_ignores_literal_prose(tmp_path):
    vault = _seed_vault(tmp_path, {
        "A.md": "---\ntitle: A\n---\n\nSomewhere I mention target.md in prose but not as a link.\n",
    })
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    count = reorg.count_references(["target.md"])
    assert count == 0


def test_count_references_skips_raw_archived(tmp_path):
    vault = _seed_vault(tmp_path, {
        "A.md": "---\n---\n\n[x](target.md)\n",
        "raw/archived/B.md": "---\n---\n\n[y](target.md)\n",
    })
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    count = reorg.count_references(["target.md"])
    assert count == 1
