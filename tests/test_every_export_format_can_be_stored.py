"""
Every format a writer produces can be stored, and read back.

The export paths are built from literals rather than request data (CodeQL #1-7),
which is right, and the literal list was typed by hand — without `html`, which
reports render to. Every HTML report export raised "Unsupported export extension"
and answered 500. So the list is checked here against the writers' own format
lists, which is where a new format gets added.
"""

from __future__ import annotations

import re
import inspect

import pytest
from throughline_domain import render_artifact, storage
from throughline_visual.renderers import publication


def _artifact_suffixes() -> set[str]:
    return set(re.findall(r'suffix = .*?, "(\w+)"', inspect.getsource(render_artifact.render)))


def test_the_artifact_writer_is_read_for_its_suffixes():
    # The check below is only as good as this list; it must not come back empty.
    assert {"md", "html", "docx", "pptx"} <= _artifact_suffixes()


@pytest.mark.parametrize("extension", sorted(_artifact_suffixes() | set(publication.SUPPORTED_FORMATS)))
def test_each_written_format_has_a_storage_path(extension, tmp_path, monkeypatch):
    monkeypatch.setenv("THROUGHLINE_STORAGE", str(tmp_path))
    table = "communication_artifacts" if extension in _artifact_suffixes() else "visuals"
    object_id = "art_0123456789abcdef0123" if table == "communication_artifacts" else "vis_0123456789abcdef0123"
    render_id = "ren_0123456789abcdef0123" if table == "communication_artifacts" else "vren_0123456789abcdef0123"
    path = storage.export_path(table, object_id, render_id, extension)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")

    key = storage.storage_key_for(path)
    assert storage.path_for(key) == path.resolve()


def test_a_key_is_taken_against_the_resolved_root(tmp_path, monkeypatch):
    """The root behind a symlink — macOS's /var — was the other half of the 500."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    monkeypatch.setenv("THROUGHLINE_STORAGE", str(link))
    path = storage.export_path("visuals", "vis_0123456789abcdef0123",
                               "vren_0123456789abcdef0123", "svg")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")

    assert storage.storage_key_for(path) == "figures/vis_0123456789abcdef0123/vren_0123456789abcdef0123.svg"


def test_a_blender_render_key_reads_back_and_a_forged_one_does_not(tmp_path, monkeypatch):
    monkeypatch.setenv("THROUGHLINE_STORAGE", str(tmp_path))
    directory = storage.blender_directory("vis_0123456789abcdef0123", "ab" * 32)
    directory.mkdir(parents=True)
    png = directory / "vis_0123456789abcdef0123-abababababab-blender.png"
    png.write_bytes(b"x")

    assert storage.path_for(storage.storage_key_for(png)) == png.resolve()
    with pytest.raises(storage.StorageError):
        storage.path_for("figures/vis_0123456789abcdef0123/blender-abababababab/other.png")
    with pytest.raises(storage.StorageError):
        storage.blender_directory("vis_0123456789abcdef0123", "../../etc")
