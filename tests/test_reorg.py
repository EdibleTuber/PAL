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


from unittest.mock import MagicMock


def test_execute_move_renames_file_and_rewrites_links(tmp_path):
    vault = _seed_vault(tmp_path, {
        "AI-Agents/old-name.md": "---\ntitle: Old\n---\n\nBody.\n",
        "Other.md": "---\ntitle: Other\n---\n\nRefers to [thing](AI-Agents/old-name.md) here.\n",
    })
    wiki = MagicMock()
    wiki.git_commit = MagicMock()
    wiki.rebuild_index = MagicMock()
    reorg = Reorganizer(vault_path=vault, wiki=wiki, compiler=None)

    ops = [{"type": "move", "src": "AI-Agents/old-name.md", "dst": "AI-Agents/new-name.md"}]
    results = reorg.execute_operations(ops)

    assert not (vault / "AI-Agents/old-name.md").exists()
    assert (vault / "AI-Agents/new-name.md").exists()

    other_content = (vault / "Other.md").read_text()
    assert "(AI-Agents/new-name.md)" in other_content
    assert "(AI-Agents/old-name.md)" not in other_content

    assert len(results) == 1
    assert results[0]["status"] == "ok"
    assert results[0]["op"] == "move"
    assert results[0]["references_rewritten"] == 1

    wiki.git_commit.assert_called()


def test_execute_move_handles_zero_references(tmp_path):
    vault = _seed_vault(tmp_path, {
        "Lonely.md": "---\ntitle: Lonely\n---\n\nNo one links to me.\n",
    })
    wiki = MagicMock()
    reorg = Reorganizer(vault_path=vault, wiki=wiki, compiler=None)

    results = reorg.execute_operations([
        {"type": "move", "src": "Lonely.md", "dst": "Renamed.md"},
    ])
    assert results[0]["status"] == "ok"
    assert results[0]["references_rewritten"] == 0
    assert (vault / "Renamed.md").exists()


def test_execute_move_partial_failure_isolation(tmp_path):
    """Per-op error isolation: a failing op does not abort the batch."""
    vault = _seed_vault(tmp_path, {
        "A.md": "---\n---\n",
        "B.md": "---\n---\n",
    })
    wiki = MagicMock()
    reorg = Reorganizer(vault_path=vault, wiki=wiki, compiler=None)

    # The second op's src doesn't exist. execute_operations bypasses
    # pre-validation (which would catch this); we're testing that the
    # per-op failure reporting works.
    ops = [
        {"type": "move", "src": "A.md", "dst": "A-new.md"},
        {"type": "move", "src": "ghost.md", "dst": "ghost-new.md"},
    ]
    results = reorg.execute_operations(ops)
    assert results[0]["status"] == "ok"
    assert results[1]["status"] == "failed"
    assert (vault / "A-new.md").exists()


@pytest.mark.asyncio
async def test_execute_merge_folds_src_into_dst(tmp_path):
    """Merge delegates to compiler.merge_into_existing, then archives
    src and rewrites references."""
    vault = _seed_vault(tmp_path, {
        "AI-Security/src.md": "---\ntitle: Src\n---\n\nSrc body.\n",
        "AI-Security/dst.md": "---\ntitle: Dst\n---\n\nDst body.\n",
        "Other.md": "---\n---\n\nLinks to [x](AI-Security/src.md) here.\n",
    })
    (vault / "raw" / "archived").mkdir(parents=True)

    wiki = MagicMock()
    wiki.git_commit = MagicMock()
    wiki.rebuild_index = MagicMock()

    compiler = MagicMock()
    async def fake_merge(new_content, new_title, existing_article_path):
        return {
            "status": "merged",
            "title": "Dst",
            "article_path_rel": existing_article_path,
        }
    compiler.merge_into_existing = fake_merge

    reorg = Reorganizer(vault_path=vault, wiki=wiki, compiler=compiler)
    ops = [{"type": "merge", "src": "AI-Security/src.md", "dst": "AI-Security/dst.md"}]
    results = await reorg.execute_operations_async(ops)

    assert not (vault / "AI-Security/src.md").exists()
    assert (vault / "AI-Security/dst.md").exists()
    other = (vault / "Other.md").read_text()
    assert "(AI-Security/dst.md)" in other
    assert "(AI-Security/src.md)" not in other
    assert results[0]["status"] == "ok"
    assert results[0]["op"] == "merge"
    assert results[0]["references_rewritten"] == 1


@pytest.mark.asyncio
async def test_execute_merge_leaves_files_on_insufficient(tmp_path):
    """If merge_into_existing returns insufficient, src and dst stay,
    references are NOT rewritten."""
    vault = _seed_vault(tmp_path, {
        "src.md": "---\n---\n",
        "dst.md": "---\n---\n",
        "Other.md": "---\n---\n\n[x](src.md)\n",
    })
    (vault / "raw" / "archived").mkdir(parents=True)

    wiki = MagicMock()
    compiler = MagicMock()
    async def fake_merge(new_content, new_title, existing_article_path):
        return {
            "status": "insufficient",
            "title": "t",
            "article_path_rel": existing_article_path,
            "reason": "LLM refused",
        }
    compiler.merge_into_existing = fake_merge

    reorg = Reorganizer(vault_path=vault, wiki=wiki, compiler=compiler)
    ops = [{"type": "merge", "src": "src.md", "dst": "dst.md"}]
    results = await reorg.execute_operations_async(ops)

    assert (vault / "src.md").exists()
    assert (vault / "dst.md").exists()
    assert "(src.md)" in (vault / "Other.md").read_text()
    assert results[0]["status"] == "insufficient"
    assert results[0]["references_rewritten"] == 0
