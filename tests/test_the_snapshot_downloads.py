"""
The snapshot is a zip somebody can open, containing the work and the files.

The records are only half of it. A snapshot describing analyses of a CSV
nobody has is a description of work rather than the work, so the files travel
beside the records — and a file that was recorded and is no longer on the disk
is named rather than silently absent.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from throughline_domain import storage
from throughline_domain.db import connection, transaction
from throughline_domain.ids import new_id
from conftest import sign_in


@pytest.fixture()
def client():
    from throughline_api.app import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean_users():
    yield
    with connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM users")
        conn.commit()


def _account(client) -> None:
    sign_in(client, email="snap@lab.local", display_name="Lead")


def _connection_in(project_id: str, marker: str) -> None:
    with transaction() as cur:
        cur.execute(
            "INSERT INTO connections (id, project_id, left_variable, "
            "right_variable, method, lifecycle_status) VALUES (%s, %s, %s, "
            "'other', 'pearson_correlation', 'candidate')",
            (new_id("con"), project_id, marker))


def _open(response) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(response.content))


def test_it_is_a_zip_with_the_records_in_it(client):
    _account(client)
    project_id = client.post("/api/projects",
                             json={"name": "Snap"}).json()["id"]
    _connection_in(project_id, "mine_variable")

    response = client.get(f"/api/projects/{project_id}/snapshot.zip")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]

    archive = _open(response)
    assert "project.json" in archive.namelist()
    records = json.loads(archive.read("project.json"))
    assert records["project"]["id"] == project_id
    assert records["tables"]["connections"][0]["left_variable"] \
        == "mine_variable"


def test_a_file_recorded_but_missing_is_named_rather_than_dropped(client):
    """
    A reader counting files against the records deserves to know which one
    this installation had lost, rather than finding a number that does not add
    up.
    """
    _account(client)
    project_id = client.post("/api/projects",
                             json={"name": "Snap"}).json()["id"]
    with transaction() as cur:
        cur.execute(
            "INSERT INTO files(id, project_id, storage_key, filename, "
            "size_bytes, content_hash) VALUES (%s, %s, 'gone/forever', "
            "'lost.csv', 10, %s)",
            (new_id("fil"), project_id, new_id("hash")))

    archive = _open(client.get(f"/api/projects/{project_id}/snapshot.zip"))
    assert "files/MISSING.txt" in archive.namelist()
    assert "lost.csv" in archive.read("files/MISSING.txt").decode()


def test_a_corrupted_recorded_file_is_not_packaged_under_its_old_hash(client):
    """A portable snapshot must not carry bytes that contradict project.json."""
    _account(client)
    project_id = client.post("/api/projects", json={"name": "Snap"}).json()["id"]

    payload = b"country,value\nIN,1\n"
    with transaction() as cur:
        record = storage.register_file(
            cur,
            project_id=project_id,
            filename="evidence.csv",
            stream=io.BytesIO(payload),
            media_type="text/csv",
        )

    path = storage.path_for(str(record["storage_key"]))
    original = path.read_bytes()
    try:
        path.write_bytes(b"corrupted")
        response = client.get(f"/api/projects/{project_id}/snapshot.zip")
        assert response.status_code == 200, response.text
        archive = _open(response)

        trusted_name = f"files/{record['storage_key']}"
        assert trusted_name not in archive.namelist()
        assert "files/MISSING.txt" in archive.namelist()
        note = archive.read("files/MISSING.txt").decode()
        assert "evidence.csv" in note
        assert "SHA-256" in note
    finally:
        path.write_bytes(original)


def test_another_project_is_not_in_it(client):
    _account(client)
    mine = client.post("/api/projects", json={"name": "Mine"}).json()["id"]
    theirs = client.post("/api/projects", json={"name": "Theirs"}).json()["id"]
    _connection_in(theirs, "their_variable")

    archive = _open(client.get(f"/api/projects/{mine}/snapshot.zip"))
    assert b"their_variable" not in archive.read("project.json")


def test_a_project_that_does_not_exist_is_a_404(client):
    _account(client)
    assert client.get(
        "/api/projects/prj_nope/snapshot.zip").status_code in (403, 404)
