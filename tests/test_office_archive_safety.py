from __future__ import annotations

import zipfile

import pytest

from throughline_ingestion import archive_safety, datasets, documents


def _zip(path, name: str, payload: bytes) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, payload)


def test_docx_refuses_oversized_expanded_member_before_python_docx(
        tmp_path, monkeypatch):
    path = tmp_path / "bomb.docx"
    _zip(path, "word/document.xml", b"A" * 4096)
    assert path.stat().st_size < 1000

    monkeypatch.setattr(archive_safety, "MAX_MEMBER_BYTES", 1024)

    with pytest.raises(documents.UnsupportedFormat, match="unsafe to expand"):
        documents.parse_docx(path)


def test_xlsx_refuses_oversized_expanded_member_before_openpyxl(
        tmp_path, monkeypatch):
    path = tmp_path / "bomb.xlsx"
    _zip(path, "xl/worksheets/sheet1.xml", b"A" * 4096)
    assert path.stat().st_size < 1000

    monkeypatch.setattr(archive_safety, "MAX_MEMBER_BYTES", 1024)

    with pytest.raises(datasets.UnsupportedDataset, match="unsafe to expand"):
        datasets.read_dataset(path, suffix=".xlsx")


def test_total_expanded_size_is_bounded_across_many_members(tmp_path, monkeypatch):
    path = tmp_path / "many.docx"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/a.xml", b"A" * 800)
        archive.writestr("word/b.xml", b"B" * 800)

    monkeypatch.setattr(archive_safety, "MAX_MEMBER_BYTES", 10_000)
    monkeypatch.setattr(archive_safety, "MAX_EXPANDED_BYTES", 1_000)

    with pytest.raises(archive_safety.UnsafeArchive, match="expands beyond"):
        archive_safety.check_zip_container(path)


def test_normal_zip_container_passes_preflight(tmp_path):
    path = tmp_path / "ordinary.docx"
    _zip(path, "word/document.xml", b"<document><p>Hello</p></document>")

    archive_safety.check_zip_container(path)
