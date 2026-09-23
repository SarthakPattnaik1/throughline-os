"""Preflight ZIP-backed research formats before parser libraries expand them."""

from __future__ import annotations

from pathlib import Path
import zipfile

# Office XML is compressed text. These ceilings are intentionally on expanded
# bytes, not the upload bytes: a tiny ZIP can otherwise allocate enormous XML
# trees inside openpyxl/python-docx.
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_MEMBER_BYTES = 256 * 1024 * 1024
MAX_MEMBERS = 20_000
MAX_COMPRESSION_RATIO = 200


class UnsafeArchive(RuntimeError):
    """A ZIP-backed research file is unreasonable to expand in-process."""


def check_zip_container(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
    except (OSError, zipfile.BadZipFile) as exc:
        raise UnsafeArchive(f"This file is not a readable ZIP container ({exc}).") from exc

    if len(members) > MAX_MEMBERS:
        raise UnsafeArchive(
            f"This archive contains {len(members):,} members; "
            f"Throughline accepts at most {MAX_MEMBERS:,} in one Office file."
        )

    total = 0
    for member in members:
        if member.is_dir():
            continue
        size = int(member.file_size)
        compressed = int(member.compress_size)
        total += size

        if size > MAX_MEMBER_BYTES:
            raise UnsafeArchive(
                f"{member.filename!r} expands to {size:,} bytes. "
                f"No single Office archive member may exceed "
                f"{MAX_MEMBER_BYTES:,} expanded bytes."
            )

        # Zero-byte compressed representation is legitimate only for an empty
        # member. Otherwise it is an impossible ratio and is refused.
        if size:
            ratio = size / max(compressed, 1)
            if ratio > MAX_COMPRESSION_RATIO:
                raise UnsafeArchive(
                    f"{member.filename!r} expands at {ratio:.0f}:1. "
                    "That compression ratio is characteristic of an archive "
                    "bomb rather than an ordinary research document."
                )

        if total > MAX_EXPANDED_BYTES:
            raise UnsafeArchive(
                f"This Office file expands beyond {MAX_EXPANDED_BYTES:,} bytes. "
                "Convert very large tables to CSV/TSV, or split the document, "
                "before importing it."
            )
