"""
The paper-first journey, walked through the API with a real worker.

On 2026-09-15 the route from a topic to a finding was walked on a fresh stack
and broke in five places (D409–D413). Each was fixed with its own tests, and
the README said so honestly: fixed, not re-walked — because a set of repaired
steps is not yet a journey. This is the walk: every step a researcher takes,
through the same routes the interface calls, with a worker ingesting what is
added. Only the network and the model are stood in for, because those are the
two things a test must not reach; everything between them runs for real,
including the host allowlist a found file has to pass.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from throughline_domain.db import connection
from throughline_workers.runner import Worker

from conftest import sign_in

PDF_URL = "https://arxiv.org/pdf/2401.00001"
FILE_URL = "https://zenodo.org/api/records/42/files/amr.csv/content"
CSV = (b"country,antibiotic_consumption,resistance_prevalence\n"
       + b"".join(f"C{i},{10 + i},{20 + 2 * i + (i % 3)}\n".encode() for i in range(60)))


@pytest.fixture()
def client():
    from throughline_api.app import app

    with TestClient(app) as test_client:
        yield test_client
    with connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM users")


@pytest.fixture()
def world(monkeypatch, paper_pdf):
    """The network and the model, answered; nothing else is replaced."""
    import throughline_model
    from throughline_connectors import datasets, papers, registry
    from throughline_connectors.base import SourceRecord
    from throughline_domain import dataset_import
    from throughline_model.schemas import (
        TestableClaim, TestableClaims, VariableProposal, VariableProposals)

    class Arxiv:
        def __init__(self, **_):
            pass

        def search(self, query, *, limit=20):
            return [SourceRecord(title="Consumption and resistance", doi="10.1/found",
                                 authors=["Chen"], year=2024, source="arxiv",
                                 url="https://arxiv.org/abs/2401.00001",
                                 pdf_url=PDF_URL, open_access=True)]

    def zenodo_search(self, query, *, limit=20):
        # The real Zenodo connector, with only its network call answered: the
        # import allowlist is derived from the connector classes themselves.
        return [self._record({
            "metadata": {"title": "AMR panel", "access_right": "open",
                         "license": {"id": "cc-by-4.0"}},
            "links": {"self_html": "https://zenodo.org/records/42"},
            "files": [{"key": "amr.csv", "size": len(CSV), "links": {"self": FILE_URL}}],
        })]

    monkeypatch.setattr(registry, "CONNECTORS", {"arxiv": Arxiv})
    monkeypatch.setattr(datasets.Zenodo, "search_datasets", zenodo_search)
    monkeypatch.setattr(datasets, "DATASET_CONNECTORS", {"zenodo": datasets.Zenodo})
    monkeypatch.setattr(papers, "fetch_pdf", lambda url, **_: paper_pdf.read_bytes())
    fetched: list[str] = []

    def fetch(url):
        fetched.append(url)
        return dataset_import.Fetched(
            status=200, body=CSV,
            headers={"content-type": "text/csv",
                     "content-disposition": 'attachment; filename="amr.csv"'})

    monkeypatch.setattr(dataset_import, "DEFAULT_FETCHER", fetch)

    class Completion:
        model, prompt_name, prompt_version = "stand-in", "stand-in", 1

    class Template:
        name, version = "stand-in", 1

        def render(self, **_):
            return "instructions"

    class Model:
        def generate_structured(self, *, schema, **_):
            if schema is TestableClaims:
                return TestableClaims(claims=[TestableClaim(
                    statement="Antibiotic consumption is associated with resistance.",
                    exposure="antibiotic_consumption", outcome="resistance_prevalence",
                    direction="positive", claimed_design="cross_sectional",
                    claimed_effect="r = 0.9", claimed_interval="", estimand="unknown",
                    outcome_definition="", population="", period="", locator="p. 1",
                    choice_confidence=0.8)], note=""), Completion()
            if schema is VariableProposals:
                return VariableProposals(proposals=[
                    VariableProposal(column=name, label=name.replace("_", " ").title(),
                                     canonical_name=name, definition="", unit="")
                    for name in ("antibiotic_consumption", "resistance_prevalence")]), \
                    Completion()
            raise AssertionError(f"the journey asked the model for {schema}")

    monkeypatch.setattr(throughline_model, "prompt", lambda _name: Template())
    monkeypatch.setattr(throughline_model, "provider", lambda *a, **k: Model())
    return {"fetched": fetched}


def _drain():
    while Worker(worker_id="journey").run_once():
        pass


def test_a_topic_reaches_a_verdict_on_found_data(client, world):
    sign_in(client, email="journey@lab.local")
    project_id = client.post("/api/projects", json={"name": "From a topic"}).json()["id"]

    # 1. Find papers, and add one with its text.
    found = client.post("/api/literature/search", json={"query": "antibiotic resistance"})
    assert found.status_code == 200, found.text
    paper = found.json()["results"][0]
    imported = client.post(f"/api/projects/{project_id}/literature/import", json=paper)
    assert imported.status_code == 201, imported.text
    source_id = imported.json()["source_id"]
    read = client.post(f"/api/projects/{project_id}/sources/{source_id}/full-text")
    assert read.status_code == 202, read.text
    _drain()

    # 2. With papers and no data, the overview names the paper route (D413).
    overview = client.get(f"/api/projects/{project_id}/discovery-map").json()
    assert "Read one for its claims" in overview["recommended_next_action"], overview

    # 3. Read the paper for its claims.
    claims = client.post(f"/api/sources/{source_id}/claims", params={"project_id": project_id})
    assert claims.status_code == 201, claims.text
    claim = claims.json()["claims"][0]

    # 4. Find data for the claim, and import the file (D411).
    query = f"{claim['exposure']} {claim['outcome']}".replace("_", " ")
    records = client.post("/api/datasets/search", json={"query": query})
    assert records.status_code == 200, records.text
    record = records.json()["results"][0]
    file = next(f for f in record["files"] if f["readable"])
    added = client.post(f"/api/projects/{project_id}/datasets/import", json={
        "url": file["url"], "title": record["title"], "repository": record["repository"],
        "licence": record["licence"] or None})
    assert added.status_code == 202, added.text
    assert world["fetched"] == [FILE_URL]
    _drain()

    # 5. Name the columns, as the Variables screen does, and approve them.
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT dv.id FROM dataset_versions dv JOIN datasets d ON d.id = dv.dataset_id "
                    "WHERE d.project_id = %s", (project_id,))
        version_id = cur.fetchone()["id"]
    proposed = client.post(f"/api/dataset-versions/{version_id}/propose-labels")
    assert proposed.status_code == 202, proposed.text
    for mapping in client.get(f"/api/projects/{project_id}/variables").json()["pending"]:
        decided = client.post(f"/api/variable-mappings/{mapping['mapping_id']}/decide",
                              json={"approve": True})
        assert decided.status_code == 200, decided.text

    # 6. Test the paper's claim against the data it led to.
    verdict = client.post(f"/api/projects/{project_id}/claim-test",
                          json={"claim": claim, "dataset_version_id": version_id})
    assert verdict.status_code == 201, verdict.text
    first = verdict.json()["verdict"]
    # Honest, not a failure: the found data does not say what kind of study it
    # is, and a cross-sectional claim cannot be judged against an unknown design.
    assert (first["family"], first["reason_code"]) == ("undetermined", "design_unstated"), first

    # 7. Say what the data is, as the claim test's own form does.
    stated = client.put(f"/api/dataset-versions/{version_id}/study-context",
                        json={"study_design": "cross_sectional"})
    assert stated.status_code == 200, stated.text

    # 8. Test the relationship in the data, corrected for how much was looked at.
    discovered = client.post(f"/api/projects/{project_id}/discoveries",
                             json={"dataset_version_id": version_id})
    assert discovered.status_code == 202, discovered.text
    _drain()

    # 9. The paper's claim, against the data it led to — now with an answer.
    verdict = client.post(f"/api/projects/{project_id}/claim-test",
                          json={"claim": claim, "dataset_version_id": version_id})
    assert verdict.status_code == 201, verdict.text
    final = verdict.json()["verdict"]
    assert final["family"] == "supported", final
