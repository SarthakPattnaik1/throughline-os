"""Content-addressed object storage.

Files are stored under the SHA-256 of their bytes and never modified. Two
consequences matter:

* Re-uploading the same document is free and cannot produce a second, divergent
  copy that evidence might point at.
* A stored file can always be re-verified against the hash a finding cited.

The local backend writes to a directory. The interface is deliberately the
subset that maps onto S3 (`put`/`open`/`exists`), so a hosted deployment swaps
the backend without touching callers.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator

from .db import data_root, transaction
from .ids import new_id

CHUNK = 1024 * 1024



class StorageError(RuntimeError):
    pass


def storage_root() -> Path:
    root = Path(os.environ.get("THROUGHLINE_STORAGE", data_root() / "objects"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _key_for(content_hash: str) -> str:
    # Two levels of fan-out keeps directory listings usable at scale.
    return f"{content_hash[:2]}/{content_hash[2:4]}/{content_hash}"


def hash_stream(stream: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    for block in iter(lambda: stream.read(CHUNK), b""):
        digest.update(block)
        size += len(block)
    return digest.hexdigest(), size


def _write_hashed_blob(
    stream: BinaryIO, *, content_hash: str, size: int,
) -> tuple[str, str, int]:
    """Write bytes already hashed by the caller, atomically."""
    key = _key_for(content_hash)
    destination = storage_root() / key
    if destination.exists():
        with destination.open("rb") as existing:
            actual_hash, actual_size = hash_stream(existing)
        if hmac_equal(actual_hash, content_hash) and actual_size == size:
            return content_hash, key, size
        # A path named after this hash contains different bytes. Treat the
        # caller's freshly-hashed stream as the repair source rather than
        # perpetuating corruption merely because the filename already exists.

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_suffix(f".{new_id('tmp')}.part")
    stream.seek(0)
    with staging.open("wb") as handle:
        shutil.copyfileobj(stream, handle, CHUNK)
    try:
        staging.replace(destination)
    except OSError:
        # Another writer of the same content may have won the race. If the
        # canonical destination now exists, the bytes are content-addressed and
        # therefore equivalent; otherwise propagate the real filesystem error.
        if not destination.exists():
            raise
        staging.unlink(missing_ok=True)
    return content_hash, key, size


def put(stream: BinaryIO) -> tuple[str, str, int]:
    """Store bytes. Returns (content_hash, storage_key, size_bytes).

    Low-level storage only. Database-backed uploads should use register_file(),
    which serializes same-content registration with garbage collection.
    """
    stream.seek(0)
    content_hash, size = hash_stream(stream)
    return _write_hashed_blob(stream, content_hash=content_hash, size=size)


def _lock_content(cur, content_hash: str) -> None:
    """Serialize registration and garbage collection for one content hash."""
    cur.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (content_hash,),
    )


def _hex_token(value: str, width: int, *, label: str) -> str:
    if len(value) != width:
        raise StorageError(f"{label} has an invalid length")
    try:
        number = int(value, 16)
    except ValueError as exc:
        raise StorageError(f"{label} is not hexadecimal") from exc
    return f"{number:0{width}x}"


def _validated_key_parts(storage_key: str) -> tuple[str, ...]:
    """Reconstruct only storage-key shapes this server itself creates."""
    if "\\" in storage_key:
        raise StorageError("Storage key contains a platform path separator")
    pure = PurePosixPath(storage_key)
    if pure.is_absolute() or not pure.parts:
        raise StorageError("Storage key is not relative to the object store")
    parts = tuple(pure.parts)
    if any(part in {"", ".", ".."} for part in parts):
        raise StorageError("Storage key contains an unsafe path component")

    # Content-addressed blobs: aa/bb/<64-char sha256>. Rebuild every path
    # component from parsed hexadecimal values so the caller's original string
    # never reaches pathlib.
    if len(parts) == 3 and len(parts[0]) == 2 and len(parts[1]) == 2:
        first = _hex_token(parts[0], 2, label="Hash prefix")
        second = _hex_token(parts[1], 2, label="Hash prefix")
        digest = _hex_token(parts[2], 64, label="Content hash")
        if first != digest[:2] or second != digest[2:4]:
            raise StorageError("Storage hash prefixes do not match the content hash")
        return first, second, digest

    # Render exports: figures/vis_<id>/vren_<id>.<ext> or
    # artifacts/art_<id>/ren_<id>.<ext>. Again, the returned components are
    # canonical values rebuilt from typed IDs and literal extensions.
    if len(parts) == 3 and parts[0] in {"figures", "artifacts"}:
        table = "visuals" if parts[0] == "figures" else "communication_artifacts"
        folder = "figures" if table == "visuals" else "artifacts"
        object_id = _canonical_export_id(table, parts[1])
        stem, dot, extension = parts[2].rpartition(".")
        if not dot:
            raise StorageError("Export storage key has no extension")
        render_id = _canonical_render_id(table, stem)
        safe_extension = _canonical_extension(extension)
        return folder, object_id, f"{render_id}.{safe_extension}"

    # Blender renders: figures/vis_<id>/blender-<12 hex>/vis_<id>-<12 hex>-blender.png.
    # The one four-part shape, rebuilt from the canonical id and the parsed
    # spec-hash prefix, so a stored Blender render reads back through this
    # function like every other export rather than around it.
    if len(parts) == 4 and parts[0] == "figures" and parts[2].startswith("blender-"):
        object_id = _canonical_export_id("visuals", parts[1])
        tag = _hex_token(parts[2][len("blender-"):], 12, label="Spec hash")
        filename = f"{object_id}-{tag}-blender.png"
        if parts[3] != filename:
            raise StorageError("Blender render key does not name its own figure")
        return "figures", object_id, f"blender-{tag}", filename

    raise StorageError("Storage key has an unsupported shape")


def path_for(storage_key: str) -> Path:
    root = storage_root().resolve()
    path = root.joinpath(*_validated_key_parts(storage_key)).resolve()
    if not path.is_relative_to(root):
        raise StorageError("Storage key escapes the object store")
    if not path.exists():
        raise StorageError("Stored object is missing")
    return path


_EXPORT_ID_PREFIX = {
    "communication_artifacts": "art",
    "visuals": "vis",
}


def _canonical_export_id(table: str, value: str) -> str:
    """Rebuild an export id from typed data, never from a path-shaped string."""
    try:
        prefix = _EXPORT_ID_PREFIX[table]
    except KeyError as exc:
        raise StorageError("Unknown export directory") from exc
    marker = prefix + "_"
    if not value.startswith(marker):
        raise StorageError("Object id has the wrong type for this export")
    raw = value[len(marker):]
    if len(raw) != 20:
        raise StorageError("Object id has an invalid length")
    try:
        number = int(raw, 16)
    except ValueError as exc:
        raise StorageError("Object id is not hexadecimal") from exc
    # Formatting an integer into a fixed-width hexadecimal token guarantees the
    # result contains no separator, dot or platform-specific path syntax.
    return f"{prefix}_{number:020x}"


def export_directory(table: str, object_id: str) -> Path:
    try:
        folder = EXPORT_DIRECTORIES[table]
    except KeyError as exc:
        raise StorageError("Unknown export directory") from exc
    safe_id = _canonical_export_id(table, object_id)
    root = storage_root().resolve()
    parent = (root / folder).resolve()
    directory = (parent / safe_id).resolve()
    if directory.parent != parent:
        raise StorageError("Export directory escapes storage root")
    return directory


def _canonical_render_id(table: str, value: str) -> str:
    prefix = "ren" if table == "communication_artifacts" else "vren"
    marker = prefix + "_"
    if not value.startswith(marker):
        raise StorageError("Render id has the wrong type")
    raw = value[len(marker):]
    if len(raw) != 20:
        raise StorageError("Render id has an invalid length")
    try:
        number = int(raw, 16)
    except ValueError as exc:
        raise StorageError("Render id is not hexadecimal") from exc
    return f"{prefix}_{number:020x}"


def _canonical_extension(value: str) -> str:
    # Return literals, never the caller's string. This keeps the filesystem
    # component independent from request data even when a format originated in
    # a query parameter.
    if value == "md":
        return "md"
    if value == "html":
        # Reports render to HTML too; missing here, every HTML export raised
        # and answered 500 (tests/test_every_export_format_can_be_stored.py).
        return "html"
    if value == "docx":
        return "docx"
    if value == "pdf":
        return "pdf"
    if value == "tex":
        return "tex"
    if value == "pptx":
        return "pptx"
    if value == "svg":
        return "svg"
    if value == "eps":
        return "eps"
    if value == "png":
        return "png"
    if value == "tiff":
        return "tiff"
    if value == "jpeg":
        return "jpeg"
    if value == "jpg":
        return "jpg"
    if value == "webp":
        return "webp"
    raise StorageError("Unsupported export extension")


def export_path(
    table: str, object_id: str, render_id: str, extension: str,
) -> Path:
    directory = export_directory(table, object_id)
    safe_render = _canonical_render_id(table, render_id)
    safe_extension = _canonical_extension(extension)
    filename = f"{safe_render}.{safe_extension}"
    path = (directory / filename).resolve()
    if path.parent != directory:
        raise StorageError("Export path escapes its object directory")
    return path


def blender_directory(visual_id: str, spec_hash: str) -> Path:
    """Where a figure's Blender working files and render go, built from checked parts."""
    tag = _hex_token(spec_hash[:12], 12, label="Spec hash")
    return export_directory("visuals", visual_id) / f"blender-{tag}"


def storage_key_for(path: Path) -> str:
    """The storage key of a file this store wrote, as `path_for` will read it back.

    Computed against the *resolved* root, because `export_path` returns a
    resolved path. Taking `path.relative_to(storage_root())` mixed the two, and
    wherever the root sits behind a symlink — macOS's temporary directory is
    `/var` → `/private/var` — every figure and report render raised and answered
    500. One helper, so the three writers cannot disagree about it again.
    """
    root = storage_root().resolve()
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(root):
        raise StorageError("That file is not inside the object store")
    return resolved.relative_to(root).as_posix()


def collect(candidates: list[tuple[str, str]]) -> dict[str, int]:
    """Remove unreferenced content blobs without racing a concurrent upload.

    A project delete and an upload of the same bytes can overlap. Deletion used
    to decide a blob was orphaned, commit, and unlink it later; an upload could
    create a new database reference in that gap and end with a live row pointing
    at a missing file.

    Both registration and collection now take the same transaction-scoped
    advisory lock derived from the content hash. Collection rechecks the live
    files table while holding that lock and unlinks before releasing it.
    """
    removed = failed = kept = 0
    root = storage_root().resolve()

    for content_hash, key in candidates:
        try:
            digest = _hex_token(content_hash, 64, label="Content hash")
            canonical = _key_for(digest)
            if key != canonical:
                failed += 1
                continue

            parts = _validated_key_parts(key)
            # Project-file garbage collection is allowed to touch only the
            # aa/bb/<sha256> namespace, never rendered reports/figures.
            if len(parts) != 3 or parts[2] != digest:
                failed += 1
                continue

            with transaction() as cur:
                _lock_content(cur, digest)
                cur.execute(
                    "SELECT 1 FROM files WHERE content_hash = %s LIMIT 1",
                    (digest,),
                )
                if cur.fetchone():
                    kept += 1
                    continue

                path = root.joinpath(*parts).resolve()
                if not path.is_relative_to(root):
                    failed += 1
                    continue
                if path.exists():
                    path.unlink()
                    removed += 1
        except (OSError, StorageError):
            failed += 1

    return {"removed": removed, "failed": failed, "kept": kept}


#: Where renderers write outside the content-addressed store: one directory per
#: object, named by its id, so nothing in one is shared with another project.
EXPORT_DIRECTORIES = {"communication_artifacts": "artifacts", "visuals": "figures"}


def collect_exports(ids_by_table: dict[str, list[str]]) -> dict[str, int]:
    """
    Remove the rendered documents and figures of objects that no longer exist.

    `collect` only ever saw blobs listed in `files`. Reports are rendered into
    `artifacts/{artifact_id}/` and figures — with their Blender scene files and
    renders — into `figures/{visual_id}/`, and nothing removed either, so a
    deleted project's unpublished exports stayed on disk after the deletion
    that was supposed to take them (T170). Each directory is named by one
    object's id and holds only that object's files, so this cannot reach
    another project's. Failures are counted, not raised, for the reason
    `collect` gives.
    """
    removed = failed = 0
    root = storage_root().resolve()
    for table, ids in ids_by_table.items():
        folder = EXPORT_DIRECTORIES[table]
        for object_id in ids:
            try:
                directory = export_directory(table, object_id)
                if directory.is_dir():
                    shutil.rmtree(directory)
                    removed += 1
            except (OSError, StorageError):
                failed += 1
    return {"removed": removed, "failed": failed}


def verify(storage_key: str, expected_hash: str) -> bool:
    """Re-check stored bytes against the hash a finding cited."""
    with path_for(storage_key).open("rb") as handle:
        actual, _ = hash_stream(handle)
    return hmac_equal(actual, expected_hash)


def hmac_equal(left: str, right: str) -> bool:
    import hmac as _hmac

    return _hmac.compare_digest(left, right)


def register_file(
    cur, *, project_id: str, filename: str, stream: BinaryIO, media_type: str
) -> dict[str, str | int]:
    """Store bytes and record the immutable file row.

    Deduplicated per project: the same content uploaded twice returns the
    original row rather than creating a divergent second artifact.
    """
    stream.seek(0)
    content_hash, size = hash_stream(stream)
    _lock_content(cur, content_hash)
    content_hash, key, size = _write_hashed_blob(
        stream, content_hash=content_hash, size=size,
    )
    cur.execute(
        "SELECT id, content_hash, storage_key, size_bytes FROM files "
        "WHERE project_id = %s AND content_hash = %s",
        (project_id, content_hash),
    )
    existing = cur.fetchone()
    if existing:
        return dict(existing) | {"deduplicated": True}

    file_id = new_id("fil")
    cur.execute(
        "INSERT INTO files(id, project_id, content_hash, filename, media_type, "
        "size_bytes, storage_key) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (file_id, project_id, content_hash, filename, media_type, size, key),
    )
    return {
        "id": file_id,
        "content_hash": content_hash,
        "storage_key": key,
        "size_bytes": size,
        "deduplicated": False,
    }
