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


@pytest.mark.asyncio
async def test_execute_operations_async_triggers_reindex(tmp_path):
    """After move/merge ops succeed, Reorganizer fires one reindex covering
    all dst paths and returns a dict stashed on per_op[0]."""
    from unittest.mock import AsyncMock
    from pal.reorg import Reorganizer
    from pal.wiki import WikiManager

    wiki = WikiManager(tmp_path)
    wiki.init_vault()

    src = tmp_path / "Research" / "old.md"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("---\ntitle: Old\n---\nBody")

    retrieval = AsyncMock()
    retrieval.trigger_reindex = AsyncMock(return_value={
        "job_id": "rj1", "status": "queued",
    })

    reorg = Reorganizer(vault_path=tmp_path, wiki=wiki, compiler=None, retrieval=retrieval)
    per_op = await reorg.execute_operations_async([
        {"type": "move", "src": "Research/old.md", "dst": "Research/new.md"},
    ])

    assert per_op[0]["status"] == "ok"
    retrieval.trigger_reindex.assert_awaited_once()
    call_args = retrieval.trigger_reindex.await_args
    paths = call_args.kwargs.get("paths") if call_args.kwargs else (call_args.args[0] if call_args.args else None)
    assert paths is not None
    assert any("Research/new.md" in p for p in paths)
    assert any("Research/old.md" in p for p in paths)
    assert "_reindex" in per_op[0]
    assert per_op[0]["_reindex"]["job_id"] == "rj1"


def test_reorganizer_constructor_default_retrieval_is_none():
    from pathlib import Path
    from unittest.mock import MagicMock
    from pal.reorg import Reorganizer
    r = Reorganizer(vault_path=Path("/tmp"), wiki=MagicMock(), compiler=None)
    assert r.retrieval is None


# ---- vault containment: batch path (R6b) ----
#
# The batch gate (validate_operations) and the single-op primitive
# (move_single) must agree on containment: for every op that reaches
# execute_operations_async, both src and dst, resolved against the vault
# root, are inside vault_path.resolve(). Containment is decided on the
# resolved path, so an in-vault symlink pointing outside is rejected.


def _make_vault_with_outside(tmp_path: Path) -> tuple[Path, Path]:
    """Vault and a sibling 'outside' directory in a fresh tmp_path."""
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (vault / "notes").mkdir()
    (vault / "notes" / "secret.md").write_text("secret")
    return vault, outside


@pytest.mark.asyncio
async def test_batch_symlink_dst_escape_file_stays_in_vault(tmp_path: Path):
    """dst traversing an in-vault symlink to an outside dir must not move
    the file out of the vault. Asserts on where the file landed, not on
    the error string."""
    vault, outside = _make_vault_with_outside(tmp_path)
    (vault / "link").symlink_to(outside)
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    ops = [{"type": "move", "src": "notes/secret.md", "dst": "link/escaped.md"}]
    errors = reorg.validate_operations(ops)
    if not errors:
        await reorg.execute_operations_async(ops)
    assert not (outside / "escaped.md").exists()
    assert (vault / "notes" / "secret.md").exists()


@pytest.mark.asyncio
async def test_batch_symlink_src_escape_host_file_untouched(tmp_path: Path):
    """src that is an in-vault symlink to a host file must not be moved;
    nothing is pulled into the vault through the link."""
    vault, outside = _make_vault_with_outside(tmp_path)
    (outside / "host_secret.md").write_text("host secret")
    (vault / "sneaky.md").symlink_to(outside / "host_secret.md")
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    ops = [{"type": "move", "src": "sneaky.md", "dst": "notes/stolen.md"}]
    errors = reorg.validate_operations(ops)
    if not errors:
        await reorg.execute_operations_async(ops)
    assert (outside / "host_secret.md").exists()
    assert not (vault / "notes" / "stolen.md").exists()


def test_batch_validate_rejects_symlink_dst_before_approval(tmp_path: Path):
    """validate_operations returns a non-empty error list naming the op and
    the offending side, so the proposal is rejected at propose time and
    never reaches the approval prompt."""
    vault, outside = _make_vault_with_outside(tmp_path)
    (vault / "link").symlink_to(outside)
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    errors = reorg.validate_operations(
        [{"type": "move", "src": "notes/secret.md", "dst": "link/escaped.md"}]
    )
    assert errors, f"expected validation errors, got {errors!r}"
    # Per-side error contract (R6b, mutation-tested): each error names the op
    # prefix and the offending side. Only dst is offending here, so exactly
    # one error naming dst — a combined `(src=... dst=...)` line would fail.
    assert len(errors) == 1
    assert "op 1" in errors[0]
    assert "path outside vault: dst=" in errors[0]
    assert "src=" not in errors[0]


def test_batch_validate_agrees_with_move_single_rejections(tmp_path: Path, tmp_path_factory):
    """Every op that move_single rejects with ValueError must also be
    rejected by validate_operations — the two entry points must agree."""
    vault, outside = _make_vault_with_outside(tmp_path)
    (vault / "link").symlink_to(outside)
    (outside / "host_secret.md").write_text("host secret")
    (vault / "sneaky.md").symlink_to(outside / "host_secret.md")
    abs_target = tmp_path_factory.mktemp("abs") / "abs_escape.md"
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    cases = [
        ("notes/secret.md", "../escaped.md"),
        ("notes/secret.md", str(abs_target)),
        ("notes/secret.md", "link/escaped.md"),
        ("sneaky.md", "notes/stolen.md"),
    ]
    for src, dst in cases:
        with pytest.raises(ValueError):
            reorg.move_single(src, dst)
        errors = reorg.validate_operations(
            [{"type": "move", "src": src, "dst": dst}]
        )
        assert errors, (
            f"move_single rejects {src!r} -> {dst!r} "
            f"but validate_operations accepted it"
        )


def test_batch_validate_rejects_raw_component(tmp_path: Path):
    """raw/ is a system directory: move_single blocks it, and the batch
    gate must agree (the raw decision of R6b)."""
    vault = _seed_vault(tmp_path, {"A.md": "---\n---\n"})
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    assert reorg.validate_operations(
        [{"type": "move", "src": "A.md", "dst": "raw/A.md"}]
    )
    assert reorg.validate_operations(
        [{"type": "move", "src": "A.md", "dst": "notes/raw/A.md"}]
    )


def test_batch_validate_allows_nested_new_parent_dirs(tmp_path: Path):
    """A dst whose parent directories do not exist yet must still validate:
    resolution is non-strict and the move creates the parents afterwards."""
    vault, _ = _make_vault_with_outside(tmp_path)
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    errors = reorg.validate_operations(
        [{"type": "move", "src": "notes/secret.md", "dst": "Deep/Nested/Dir/secret.md"}]
    )
    assert errors == []


def test_batch_validate_allows_inner_dotdot_staying_inside(tmp_path: Path):
    """An inner .. that stays inside the vault resolves inside and is
    allowed: containment is about the resolved location, not the string.
    (sub/ is a real directory, as it would be in a live vault — the
    kernel can only walk `..` through directories that exist.)"""
    vault, _ = _make_vault_with_outside(tmp_path)
    (vault / "notes" / "sub").mkdir()
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    errors = reorg.validate_operations(
        [{"type": "move", "src": "notes/sub/../secret.md", "dst": "notes/moved.md"}]
    )
    assert errors == []


def test_batch_validate_allows_directory_named_drawer(tmp_path: Path):
    """A directory merely containing 'raw' as a substring (drawer) is not
    a raw/ component and must validate."""
    vault = _seed_vault(tmp_path, {"drawer/note.md": "---\n---\n"})
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    errors = reorg.validate_operations(
        [{"type": "move", "src": "drawer/note.md", "dst": "drawer/renamed.md"}]
    )
    assert errors == []


def test_batch_validate_allows_multi_op_legit_batch(tmp_path: Path):
    vault = _seed_vault(tmp_path, {
        "A.md": "---\n---\n",
        "B.md": "---\n---\n",
        "C.md": "---\n---\n",
    })
    reorg = Reorganizer(vault_path=vault, wiki=None, compiler=None)
    errors = reorg.validate_operations([
        {"type": "move", "src": "A.md", "dst": "Topics/A.md"},
        {"type": "move", "src": "B.md", "dst": "Topics/sub/B.md"},
        {"type": "move", "src": "C.md", "dst": "drawer/C.md"},
    ])
    assert errors == []
