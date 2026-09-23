from __future__ import annotations

import importlib.util
import io
import tarfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "backup_archive_test", ROOT / "scripts" / "backup_archive.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backup_archive = _load()


def _add_file(bundle: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    bundle.addfile(info, io.BytesIO(payload))


def _outer(path: Path, *, manifest: bytes, database: bytes = b"PGDMPx",
           objects: bytes = b"objects") -> None:
    with tarfile.open(path, "w") as bundle:
        _add_file(bundle, "manifest.txt", manifest)
        _add_file(bundle, "database.dump", database)
        _add_file(bundle, "objects.tar.gz", objects)


def test_outer_archive_refuses_path_traversal(tmp_path):
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as bundle:
        _add_file(bundle, "../manifest.txt", b"throughline-backup\n")

    with pytest.raises(backup_archive.BackupError, match="unsafe archive path"):
        backup_archive.extract_outer(archive, tmp_path / "out")


def test_outer_archive_refuses_links(tmp_path):
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as bundle:
        link = tarfile.TarInfo("manifest.txt")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        bundle.addfile(link)

    with pytest.raises(backup_archive.BackupError, match="not a regular file"):
        backup_archive.extract_outer(archive, tmp_path / "out")


def test_manifest_size_mismatch_is_refused(tmp_path):
    archive = tmp_path / "backup.tar"
    manifest = (
        b"throughline-backup\n"
        b"database_bytes: 999\n"
        b"objects_bytes: 7\n"
    )
    _outer(archive, manifest=manifest)

    out = tmp_path / "out"
    backup_archive.extract_outer(archive, out)
    with pytest.raises(backup_archive.BackupError, match="size does not match"):
        backup_archive.verify_manifest(out)


def test_manifest_hash_mismatch_is_refused(tmp_path):
    archive = tmp_path / "backup.tar"
    manifest = (
        b"throughline-backup\n"
        b"database_bytes: 6\n"
        b"objects_bytes: 7\n"
        b"database_sha256: " + b"0" * 64 + b"\n"
    )
    _outer(archive, manifest=manifest)

    out = tmp_path / "out"
    backup_archive.extract_outer(archive, out)
    with pytest.raises(backup_archive.BackupError, match="SHA-256"):
        backup_archive.verify_manifest(out)


def test_object_archive_refuses_escape_and_links(tmp_path):
    traversal = tmp_path / "traversal.tar.gz"
    with tarfile.open(traversal, "w:gz") as bundle:
        _add_file(bundle, "../outside", b"x")
    with pytest.raises(backup_archive.BackupError):
        backup_archive.extract_objects(traversal, tmp_path / "one")

    linked = tmp_path / "linked.tar.gz"
    with tarfile.open(linked, "w:gz") as bundle:
        root = tarfile.TarInfo("objects")
        root.type = tarfile.DIRTYPE
        bundle.addfile(root)
        link = tarfile.TarInfo("objects/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/tmp/elsewhere"
        bundle.addfile(link)
    with pytest.raises(backup_archive.BackupError, match="unsupported entry"):
        backup_archive.extract_objects(linked, tmp_path / "two")


def test_valid_object_archive_extracts_only_under_objects(tmp_path):
    archive = tmp_path / "objects.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        root = tarfile.TarInfo("objects")
        root.type = tarfile.DIRTYPE
        bundle.addfile(root)
        _add_file(bundle, "objects/aa/bb/blob", b"research")

    out = tmp_path / "out"
    backup_archive.extract_objects(archive, out)

    assert (out / "objects/aa/bb/blob").read_bytes() == b"research"
