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
