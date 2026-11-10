from pathlib import Path

import pytest

from pal.reorg import Reorganizer


def _make_vault(tmp_path: Path) -> Path:
    (tmp_path / "Security").mkdir()
    (tmp_path / "IoT").mkdir()
    (tmp_path / "Security" / "methodology.md").write_text("---\ntitle: M\n---\nbody\n")
    return tmp_path


def test_move_single_renames_file(tmp_path: Path):
    vault = _make_vault(tmp_path)
    r = Reorganizer(vault_path=vault, wiki=None, compiler=None, retrieval=None)
    r.move_single("Security/methodology.md", "IoT/methodology.md")
    assert not (vault / "Security" / "methodology.md").exists()
    assert (vault / "IoT" / "methodology.md").exists()


def test_move_single_rejects_missing_src(tmp_path: Path):
    vault = _make_vault(tmp_path)
    r = Reorganizer(vault_path=vault, wiki=None, compiler=None, retrieval=None)
    with pytest.raises(FileNotFoundError):
        r.move_single("Security/missing.md", "IoT/missing.md")


def test_move_single_rejects_existing_dst(tmp_path: Path):
    vault = _make_vault(tmp_path)
    (vault / "IoT" / "methodology.md").write_text("existing")
    r = Reorganizer(vault_path=vault, wiki=None, compiler=None, retrieval=None)
    with pytest.raises(FileExistsError):
        r.move_single("Security/methodology.md", "IoT/methodology.md")


def test_move_single_rejects_system_dirs(tmp_path: Path):
    vault = _make_vault(tmp_path)
    (vault / "_wisdom").mkdir()
    (vault / "_wisdom" / "x.md").write_text("x")
    r = Reorganizer(vault_path=vault, wiki=None, compiler=None, retrieval=None)
    with pytest.raises(ValueError, match="system directory"):
        r.move_single("_wisdom/x.md", "IoT/x.md")
    with pytest.raises(ValueError, match="system directory"):
        r.move_single("Security/methodology.md", "raw/methodology.md")


def test_move_single_creates_parent_dirs(tmp_path: Path):
    vault = _make_vault(tmp_path)
    r = Reorganizer(vault_path=vault, wiki=None, compiler=None, retrieval=None)
    r.move_single("Security/methodology.md", "Networking/Protocols/methodology.md")
    assert (vault / "Networking" / "Protocols" / "methodology.md").exists()


# ---- vault containment (X1) ----

def _make_vault_with_outside(tmp_path: Path) -> tuple[Path, Path]:
    """Vault and a sibling 'outside' directory in a fresh tmp_path."""
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (vault / "notes").mkdir()
    (vault / "notes" / "secret.md").write_text("secret")
    return vault, outside


def test_move_single_rejects_dst_dotdot_escape(tmp_path: Path):
    """dst='../escaped.md' must not move the file above the vault."""
    vault, _ = _make_vault_with_outside(tmp_path)
    r = Reorganizer(vault_path=vault, wiki=None, compiler=None, retrieval=None)
    with pytest.raises(ValueError, match="outside vault"):
        r.move_single("notes/secret.md", "../escaped.md")
    assert not (tmp_path / "escaped.md").exists()
    assert (vault / "notes" / "secret.md").exists()


def test_move_single_rejects_src_dotdot_escape(tmp_path: Path):
    """src='../host_file.md' must not pull a host file into the vault."""
    vault, outside = _make_vault_with_outside(tmp_path)
    (outside / "host_file.md").write_text("host secret")
    r = Reorganizer(vault_path=vault, wiki=None, compiler=None, retrieval=None)
    with pytest.raises(ValueError, match="outside vault"):
        r.move_single("../host_file.md", "notes/stolen.md")
    assert (outside / "host_file.md").exists()
    assert not (vault / "notes" / "stolen.md").exists()


def test_move_single_rejects_absolute_dst(tmp_path: Path, tmp_path_factory):
    """Absolute dst must be rejected, not re-rooted outside the vault."""
    vault, _ = _make_vault_with_outside(tmp_path)
    abs_target = tmp_path_factory.mktemp("abs") / "abs_escape.md"
    r = Reorganizer(vault_path=vault, wiki=None, compiler=None, retrieval=None)
    with pytest.raises(ValueError, match="outside vault"):
        r.move_single("notes/secret.md", str(abs_target))
    assert not abs_target.exists()
    assert (vault / "notes" / "secret.md").exists()


def test_move_single_rejects_nested_system_dir(tmp_path: Path):
    """dst with an _-prefixed component anywhere (not just top level) is blocked."""
    vault, _ = _make_vault_with_outside(tmp_path)
    r = Reorganizer(vault_path=vault, wiki=None, compiler=None, retrieval=None)
    with pytest.raises(ValueError, match="system directory"):
        r.move_single("notes/secret.md", "notes/_wisdom/secret.md")
    assert not (vault / "notes" / "_wisdom" / "secret.md").exists()
    assert (vault / "notes" / "secret.md").exists()


def test_move_single_rejects_symlink_dst_escape(tmp_path: Path):
    """dst through an in-vault symlink to an outside dir must be rejected."""
    vault, outside = _make_vault_with_outside(tmp_path)
    (vault / "link").symlink_to(outside)
    r = Reorganizer(vault_path=vault, wiki=None, compiler=None, retrieval=None)
    with pytest.raises(ValueError, match="outside vault"):
        r.move_single("notes/secret.md", "link/secret.md")
    assert not (outside / "secret.md").exists()
    assert (vault / "notes" / "secret.md").exists()


def test_move_single_rejects_symlink_src_escape(tmp_path: Path):
    """src that is an in-vault symlink to a host file must be rejected."""
    vault, outside = _make_vault_with_outside(tmp_path)
    (outside / "host_secret.md").write_text("host secret")
    (vault / "sneaky.md").symlink_to(outside / "host_secret.md")
    r = Reorganizer(vault_path=vault, wiki=None, compiler=None, retrieval=None)
    with pytest.raises(ValueError, match="outside vault"):
        r.move_single("sneaky.md", "notes/stolen.md")
    assert (outside / "host_secret.md").exists()
    assert not (vault / "notes" / "stolen.md").exists()
