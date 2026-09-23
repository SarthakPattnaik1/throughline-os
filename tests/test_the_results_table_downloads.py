"""
The results table is a file a researcher can actually save.

The domain writes the CSV and `test_the_results_table` covers what it says.
This covers the half that made §74 a defect twice over: a capability with no
route, or a route with no control, is a capability nobody has.
"""

from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient
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
    sign_in(client, email="csv@lab.local", display_name="Lead")


def _tested(project_id: str, **over) -> None:
    values = {"left_variable": "consumption", "right_variable": "resistance",
              "q_value": 0.012, "lifecycle_status": "exploratory"}
    values.update(over)
    with transaction() as cur:
        cur.execute(
            "INSERT INTO connections (id, project_id, left_variable, "
            "right_variable, method, estimate, p_value, q_value, sample_size, "
            "evidence_quality, lifecycle_status) VALUES (%s, %s, %s, %s, "
            "'pearson_correlation', 0.62, 0.001, %s, 120, 'moderate', %s)",
            (new_id("con"), project_id, values["left_variable"],
             values["right_variable"], values["q_value"],
             values["lifecycle_status"]))


def test_it_arrives_as_a_file_and_not_as_a_page(client):
    _account(client)
    project_id = client.post("/api/projects",
                             json={"name": "Results"}).json()["id"]
    _tested(project_id)

    response = client.get(f"/api/projects/{project_id}/results.csv")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    # Saved rather than rendered: a researcher opens this in a spreadsheet, and
    # copying a textarea into a file is how a transcription error enters
    # somebody's own results.
    assert "attachment" in response.headers["content-disposition"]
    assert project_id in response.headers["content-disposition"]


def test_the_rows_are_the_connections(client):
    _account(client)
    project_id = client.post("/api/projects",
                             json={"name": "Results"}).json()["id"]
    _tested(project_id, left_variable="consumption")
    _tested(project_id, left_variable="gdp", q_value=0.6)

    text = client.get(f"/api/projects/{project_id}/results.csv").text
    rows = list(csv.DictReader(io.StringIO(text)))

    assert [r["Variable A"] for r in rows] == ["consumption", "gdp"]
    assert rows[0]["q (corrected)"] == "0.012"


def test_another_project_is_not_in_it(client):
    _account(client)
    mine = client.post("/api/projects", json={"name": "Mine"}).json()["id"]
    theirs = client.post("/api/projects", json={"name": "Theirs"}).json()["id"]
    _tested(theirs, left_variable="not_mine")

    text = client.get(f"/api/projects/{mine}/results.csv").text
    assert "not_mine" not in text


def test_text_cells_cannot_become_spreadsheet_formulas(client):
    _account(client)
    project_id = client.post("/api/projects", json={"name": "Safe CSV"}).json()["id"]

    dangerous = [
        "=HYPERLINK(\"https://example.invalid\",\"click\")",
        " +SUM(1,1)",
        "\t@SUM(1,1)",
        "-CMD|' /C calc'!A0",
    ]
    for value in dangerous:
        _tested(project_id, left_variable=value)

    rows = list(csv.DictReader(io.StringIO(
        client.get(f"/api/projects/{project_id}/results.csv").text
    )))

    exported = [row["Variable A"] for row in rows]
    assert len(exported) == len(dangerous)
    for raw, cell in zip(dangerous, exported):
        assert cell.startswith("'"), (raw, cell)
        assert cell[1:] == raw


def test_negative_numbers_remain_numeric_cells():
    from throughline_domain import tables

    assert tables._cell(-1) == "-1"
    assert tables._cell(-0.5) == "-0.5"


def test_a_project_that_tested_nothing_is_a_header_not_an_error(client):
    """
    An empty file and a failed download look identical once saved, so this
    answers with the columns and no rows.
    """
    _account(client)
    project_id = client.post("/api/projects",
                             json={"name": "Quiet"}).json()["id"]

    response = client.get(f"/api/projects/{project_id}/results.csv")
    assert response.status_code == 200
    assert "Variable A" in response.text
    assert len(list(csv.DictReader(io.StringIO(response.text)))) == 0
