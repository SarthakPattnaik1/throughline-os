#!/usr/bin/env python3
"""Validate and safely extract Throughline backup archives."""

from __future__ import annotations

import argparse
import hashlib
import tarfile
from pathlib import Path, PurePosixPath


OUTER = {"manifest.txt", "database.dump", "objects.tar.gz"}


class BackupError(RuntimeError):
    pass


def _safe_name(raw: str) -> PurePosixPath:
    if "\\" in raw:
        raise BackupError(f"unsafe archive path: {raw}")
    path = PurePosixPath(raw)
    if not raw or raw.startswith("/") or path.is_absolute():
        raise BackupError(f"unsafe archive path: {raw}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise BackupError(f"unsafe archive path: {raw}")
    return path


def _copy_member(bundle: tarfile.TarFile, member: tarfile.TarInfo, destination: Path) -> None:
    source = bundle.extractfile(member)
    if source is None:
        raise BackupError(f"could not read archive member: {member.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source, destination.open("xb") as out:
            while chunk := source.read(1024 * 1024):
                out.write(chunk)
    except FileExistsError as exc:
        raise BackupError(f"archive names {member.name} more than once") from exc


def extract_outer(archive: Path, into: Path) -> None:
    into.mkdir(parents=True, exist_ok=False)
    seen: set[str] = set()
    with tarfile.open(archive, "r:*") as bundle:
        for member in bundle.getmembers():
            name = _safe_name(member.name).as_posix()
            if name not in OUTER:
                raise BackupError(f"unexpected backup member: {name}")
            if name in seen:
                raise BackupError(f"duplicate backup member: {name}")
            seen.add(name)
            if not member.isfile():
                raise BackupError(f"backup member is not a regular file: {name}")
            _copy_member(bundle, member, into / name)
    if seen != OUTER:
        raise BackupError(f"backup is incomplete: missing {sorted(OUTER - seen)}")


def _manifest(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "throughline-backup":
        raise BackupError("Not a Throughline backup.")
    values: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    return values


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_manifest(extracted: Path) -> None:
    values = _manifest(extracted / "manifest.txt")
    for filename, prefix in (("database.dump", "database"), ("objects.tar.gz", "objects")):
        path = extracted / filename
        expected_size = values.get(f"{prefix}_bytes")
        if expected_size is None:
            raise BackupError(f"manifest names no {prefix}_bytes")
        if path.stat().st_size != int(expected_size):
            raise BackupError(f"{filename} size does not match manifest")

        expected_hash = values.get(f"{prefix}_sha256")
        if expected_hash is not None and _sha256(path) != expected_hash:
            raise BackupError(f"{filename} SHA-256 does not match manifest")


def extract_objects(archive: Path, into: Path) -> None:
    into.mkdir(parents=True, exist_ok=False)
    seen: set[str] = set()
    with tarfile.open(archive, "r:*") as bundle:
        for member in bundle.getmembers():
            name = _safe_name(member.name)
            if not name.parts or name.parts[0] != "objects":
                raise BackupError(f"object archive escapes objects/: {member.name}")
            key = name.as_posix()
            if key in seen:
                raise BackupError(f"duplicate object archive member: {key}")
            seen.add(key)
            destination = into.joinpath(*name.parts)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise BackupError(f"object archive contains unsupported entry: {key}")
            _copy_member(bundle, member, destination)
    (into / "objects").mkdir(parents=True, exist_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    outer = sub.add_parser("outer")
    outer.add_argument("archive", type=Path)
    outer.add_argument("into", type=Path)

    objects = sub.add_parser("objects")
    objects.add_argument("archive", type=Path)
    objects.add_argument("into", type=Path)

    args = parser.parse_args()
    try:
        if args.command == "outer":
            extract_outer(args.archive, args.into)
            verify_manifest(args.into)
        else:
            extract_objects(args.archive, args.into)
    except (BackupError, OSError, tarfile.TarError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
