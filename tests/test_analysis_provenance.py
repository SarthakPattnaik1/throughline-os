"""LAW 2 and §44 — every number is the output of a recorded, reproducible run."""

from __future__ import annotations

import io

import pytest
from throughline_domain import analysis, lineage, objects, storage, workflow
from throughline_domain.db import connection
from throughline_domain.ids import new_id
from throughline_schemas.enums import SourceType
from throughline_workers.runner import Worker

# A deliberately clean association so the statistics are unambiguous and the test
# is about provenance rather than about borderline inference.
CSV = b"""country,year,consumption_ddd,resistance_pct,region
IND,2019,32.1,41.2,asia
USA,2019,24.5,30.1,americas
GBR,2019,18.2,22.4,europe
FRA,2019,26.7,33.8,europe
DEU,2019,15.4,19.1,europe
BRA,2019,29.3,37.5,americas
JPN,2019,14.1,17.6,asia
ZAF,2019,27.8,35.2,africa
NGA,2019,31.5,40.1,africa
AUS,2019,16.8,21.0,oceania
CAN,2019,19.9,24.3,americas
ITA,2019,28.4,36.0,europe
"""


@pytest.fixture()
def analysed_project():
    """A project with an ingested dataset, ready to analyse."""
    user_id, project_id = new_id("usr"), new_id("prj")
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users(id, email, display_name, password_hash, password_salt) "
            "VALUES (%s, %s, %s, 'x', 'y')",
            (user_id, f"{user_id}@test.local", "Analysis Test"),
        )
        cur.execute("INSERT INTO projects(id, owner_user_id, name) VALUES (%s, %s, 'Analysis')",
                    (project_id, user_id))
        record = storage.register_file(cur, project_id=project_id, filename="amr.csv",
                                       stream=io.BytesIO(CSV), media_type="text/csv")
        source_id = objects.create_source(
            cur, project_id=project_id, source_type=SourceType.UPLOAD, title="amr.csv",
            actor="test", file_id=str(record["id"]),
            content_hash=str(record["content_hash"]),
        )
        workflow.enqueue(cur, workflow_name="ingest.source", project_id=project_id,
                         payload={"source_id": source_id})
    _drain()

    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT dv.id FROM dataset_versions dv JOIN datasets d ON d.id = dv.dataset_id "
            "WHERE d.source_id = %s", (source_id,),
        )
        version_id = cur.fetchone()["id"]
    yield project_id, version_id
    with connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))


def _drain() -> None:
    while Worker(worker_id="analysis-test").run_once():
        pass


def _analyse(project_id: str, version_id: str, **spec) -> str:
    with connection() as conn, conn.cursor() as cur:
        created = analysis.create_spec(cur, project_id=project_id, spec={
            "dataset_version_ids": [version_id], **spec,
        }, actor="test")
        run_id = analysis.create_run(cur, project_id=project_id, spec_id=created["spec_id"])
        workflow.enqueue(cur, workflow_name="analysis.run", project_id=project_id,
                         payload={"analysis_run_id": run_id},
                         idempotency_key=f"analysis:{run_id}")
    _drain()
    return run_id


def _run(run_id: str) -> dict:
    with connection() as conn, conn.cursor() as cur:
        return analysis.get_run(cur, run_id)


# ---------------------------------------------------------------------------
# Specification validation (§45)
# ---------------------------------------------------------------------------


def test_spec_naming_an_unknown_column_is_refused_before_execution(analysed_project):
    project_id, version_id = analysed_project
    with connection() as conn, conn.cursor() as cur:
        with pytest.raises(analysis.SpecInvalid) as exc:
            analysis.create_spec(cur, project_id=project_id, spec={
                "method": "pearson_correlation",
                "dataset_version_ids": [version_id],
                "variables": {"x": "consumption_ddd", "y": "not_a_column"},
            }, actor="test")
    assert "not_a_column" in str(exc.value)
    assert "Available:" in str(exc.value)  # §104 — say what could work instead


def test_spec_with_an_unknown_method_is_refused(analysed_project):
    project_id, version_id = analysed_project
    with connection() as conn, conn.cursor() as cur:
        with pytest.raises(analysis.SpecInvalid) as exc:
            analysis.create_spec(cur, project_id=project_id, spec={
                "method": "os.system", "dataset_version_ids": [version_id],
                "variables": {"x": "consumption_ddd", "y": "resistance_pct"},
            }, actor="test")
    assert "Unknown method" in str(exc.value)


def test_spec_cannot_reach_another_projects_dataset(analysed_project):
    project_id, version_id = analysed_project
    other = new_id("prj")
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT owner_user_id FROM projects WHERE id = %s", (project_id,))
        owner = cur.fetchone()["owner_user_id"]
        cur.execute("INSERT INTO projects(id, owner_user_id, name) VALUES (%s, %s, 'Other')",
                    (other, owner))
        with pytest.raises(analysis.SpecInvalid) as exc:
            analysis.create_spec(cur, project_id=other, spec={
                "method": "pearson_correlation", "dataset_version_ids": [version_id],
                "variables": {"x": "consumption_ddd", "y": "resistance_pct"},
            }, actor="test")
    assert "different project" in str(exc.value)


# ---------------------------------------------------------------------------
# LAW 2 and §44
# ---------------------------------------------------------------------------


def test_analysis_refuses_dataset_bytes_that_do_not_match_the_version_hash(
        analysed_project):
    project_id, version_id = analysed_project
    with connection() as conn, conn.cursor() as cur:
        created = analysis.create_spec(
            cur,
            project_id=project_id,
            spec={
                "method": "pearson_correlation",
                "dataset_version_ids": [version_id],
                "variables": {"x": "consumption_ddd", "y": "resistance_pct"},
            },
            actor="test",
        )
        cur.execute(
            "SELECT storage_key FROM dataset_versions WHERE id = %s",
            (version_id,),
        )
        key = cur.fetchone()["storage_key"]

    # The content-addressed filename alone is not proof that the bytes inside it
    # still have that identity.
    storage.path_for(key).write_bytes(
        b"country,consumption_ddd,resistance_pct\nX,1,999\n"
    )

    from throughline_workers.handlers import _prepare_analysis

    with connection() as conn, conn.cursor() as cur:
        with pytest.raises(ValueError, match="recorded SHA-256"):
            _prepare_analysis(cur, created["spec_id"])


def test_analysis_produces_a_real_computed_result_with_provenance(analysed_project):
    project_id, version_id = analysed_project
    run_id = _analyse(project_id, version_id, method="pearson_correlation",
                      variables={"x": "consumption_ddd", "y": "resistance_pct"},
                      research_question="Does consumption track resistance?",
                      method_rationale="Both variables are continuous and roughly linear.")

    run = _run(run_id)
    assert run["status"] == "completed", run["error"]
    result = run["result"]

    # LAW 2 — the number came from computation, and it is the right number.
    assert result["estimate"] > 0.95  # the fixture is a near-perfect association
    assert result["p_value"] < 0.001
    assert result["sample_size"] == 12

    # §44 — everything needed to reproduce it.
    assert run["random_seed"] == 0
    assert run["dependency_versions"]["pandas"]
    assert run["dependency_versions"]["scipy"]
    assert run["input_hashes"]["dataset_content_hash"]
    assert run["input_hashes"]["spec_content_hash"]
    assert run["sandbox_policy"]["enforced"]["separate_process"] is True
    assert run["duration_ms"] >= 0 and run["started_at"] and run["finished_at"]

    # §47 — the four judgements are all present and separate.
    assert result["statistically_significant"] is True
    assert result["practical_significance"] == "large"
    assert result["evidence_quality"] in {"weak", "moderate", "strong"}
    assert "not causation" in " ".join(result["limitations"])

    # §45 — assumption checks are queryable rows, not prose.
    names = {c["name"] for c in run["assumption_checks"]}
    assert any(n.startswith("normality") for n in names)
    assert any(n.startswith("outliers") for n in names)



def test_a_run_records_the_throughline_build_that_executed_it(
        analysed_project, monkeypatch):
    """A later export must never attribute today's checkout to an older run."""
    build = {
        "version": "test-build",
        "source": "checkout",
        "commit": "a" * 40,
        "modified": False,
        "note": "Test build.",
    }
    monkeypatch.setattr(analysis.installation_version, "current", lambda: build)
    project_id, version_id = analysed_project

    run_id = _analyse(project_id, version_id, method="pearson_correlation",
                      variables={"x": "consumption_ddd", "y": "resistance_pct"})

    assert _run(run_id)["environment"]["throughline"] == build


def test_analysis_is_linked_to_the_dataset_it_was_calculated_from(analysed_project):
    """LAW 1 — the number traces back to the rows behind it."""
    project_id, version_id = analysed_project
    run_id = _analyse(project_id, version_id, method="linear_regression",
                      variables={"outcome": "resistance_pct",
                                 "predictors": ["consumption_ddd"]})
    run = _run(run_id)
    assert run["status"] == "completed", run["error"]

    with connection() as conn, conn.cursor() as cur:
        ancestors = lineage.ancestors(cur, run["object_id"])
    assert any(a["object_type"] == "dataset" for a in ancestors), ancestors


def test_a_completed_run_cannot_be_edited(analysed_project):
    """§12/§44 — a result is a historical record. Fork it, do not rewrite it."""
    project_id, version_id = analysed_project
    run_id = _analyse(project_id, version_id, method="pearson_correlation",
                      variables={"x": "consumption_ddd", "y": "resistance_pct"})
    with connection() as conn, conn.cursor() as cur:
        with pytest.raises(Exception) as exc:
            cur.execute("UPDATE analysis_runs SET result = %s WHERE id = %s",
                        ({"estimate": 0.0}, run_id))
    assert "terminal" in str(exc.value).lower()


def test_rerunning_the_same_spec_reproduces_the_same_number(analysed_project):
    """§44 — rerun must reproduce, or reproducibility is a slogan."""
    project_id, version_id = analysed_project
    variables = {"outcome": "resistance_pct", "predictors": ["consumption_ddd"]}
    first = _run(_analyse(project_id, version_id, method="linear_regression",
                          variables=variables))
    second = _run(_analyse(project_id, version_id, method="linear_regression",
                           variables=variables))
    assert first["result"]["estimate"] == second["result"]["estimate"]
    assert first["result"]["p_value"] == second["result"]["p_value"]
    # Same spec, same inputs — so the recorded hashes must match too.
    assert first["input_hashes"] == second["input_hashes"]


def test_a_failed_analysis_records_why(analysed_project):
    """§104 — a failure explains itself instead of vanishing."""
    project_id, version_id = analysed_project
    # Valid at specification time; the data cannot support it (one group only).
    run_id = _analyse(project_id, version_id, method="t_test",
                      variables={"value": "resistance_pct", "group": "year"})
    run = _run(run_id)
    assert run["status"] == "failed"
    assert "exactly 2 groups" in run["error"]
    assert run["object_id"] is None  # a failed run creates no artifact


def test_retrying_a_terminal_run_does_not_recompute_it(analysed_project):
    """§38 — idempotency, checked against the real handler."""
    project_id, version_id = analysed_project
    run_id = _analyse(project_id, version_id, method="descriptive",
                      variables={"columns": ["consumption_ddd", "resistance_pct"]})
    before = _run(run_id)

    with connection() as conn, conn.cursor() as cur:
        from throughline_workers.handlers import analysis_run

        outcome = analysis_run({"input": {"analysis_run_id": run_id}}, cur)
    assert outcome["skipped"] is True
    assert _run(run_id)["finished_at"] == before["finished_at"]


# ---------------------------------------------------------------------------
# §95 — fork and compare
# ---------------------------------------------------------------------------


def test_forking_isolates_an_analytical_choice(analysed_project):
    """A sensitivity branch must be legible: same question, one changed decision."""
    project_id, version_id = analysed_project
    base = _analyse(project_id, version_id, method="pearson_correlation",
                    variables={"x": "consumption_ddd", "y": "resistance_pct"})

    with connection() as conn, conn.cursor() as cur:
        spec_id = _run(base)["spec_id"]
        spec_row = analysis.load_spec(cur, spec_id)
        created = analysis.create_spec(cur, project_id=project_id, spec={
            "method": "spearman_correlation",
            "dataset_version_ids": spec_row["dataset_version_ids"],
            "variables": spec_row["variables"],
            "method_rationale": "Rank-based check on the same association.",
        }, actor="test")
        fork_id = analysis.create_run(cur, project_id=project_id, spec_id=created["spec_id"],
                                      forked_from_run_id=base,
                                      fork_reason="Rank-based sensitivity check")
        workflow.enqueue(cur, workflow_name="analysis.run", project_id=project_id,
                         payload={"analysis_run_id": fork_id},
                         idempotency_key=f"analysis:{fork_id}")
    _drain()

    forked = _run(fork_id)
    assert forked["status"] == "completed", forked["error"]
    assert forked["forked_from_run_id"] == base

    with connection() as conn, conn.cursor() as cur:
        comparison = analysis.compare_runs(cur, [base, fork_id])
    assert len(comparison["runs"]) == 2
    # Both methods should agree on this deliberately clean association.
    assert comparison["conclusion_stable"] is True
    assert "agree" in comparison["note"]


# ---------------------------------------------------------------------------
# The route a researcher takes to a sensitivity analysis
#
# The test above builds a fork by calling `create_spec` and `create_run`
# directly, which is how the domain was proved correct and is not how anybody
# uses it. `POST /analyses/{id}/fork` and `GET /projects/{id}/analyses/compare`
# had no caller in the interface and no test over HTTP either — so the seam
# between the screen and the domain was the one nothing exercised.
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from throughline_api.app import app

    with TestClient(app) as test_client:
        yield test_client
    with connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE email = %s", ("branches@lab.local",))


def _signed_in_project(client) -> tuple[str, str]:
    """A project owned by the signed-in account, with one completed analysis."""
    status = client.get("/api/auth/status").json()
    endpoint = "/api/auth/setup" if status["needs_setup"] else "/api/auth/login"
    assert client.post(endpoint, json={
        "email": "branches@lab.local", "display_name": "Branches",
        "password": "correct-horse-battery"}).status_code == 200

    project_id = client.post("/api/projects", json={"name": "Branches"}).json()["id"]
    assert client.post(f"/api/projects/{project_id}/sources",
                       files={"file": ("amr.csv", CSV, "text/csv")}
                       ).status_code == 202
    _drain()
    sources = client.get(f"/api/projects/{project_id}/sources").json()
    version_id = sources[0]["dataset"]["dataset_version_id"]

    queued = client.post(f"/api/projects/{project_id}/analyses", json={
        "method": "pearson_correlation",
        "dataset_version_ids": [version_id],
        "variables": {"x": "consumption_ddd", "y": "resistance_pct"},
    })
    assert queued.status_code == 202, queued.text
    _drain()
    return project_id, queued.json()["analysis_run_id"]


def test_a_specified_analysis_runs_and_then_appears_in_the_project(client):
    """
    The loop the interface could not close.

    Discovery produced analyses and forks branched them; a researcher could
    register a hypothesis and had no way to run it. The form now posts exactly
    this payload, so the test is over the whole path: a spec the interface can
    build is accepted, executed, and listed — the last step being the one that
    was missing, since the screen listed connections and a specified run belongs
    to none.
    """
    project_id, _ = _signed_in_project(client)

    queued = client.post(f"/api/projects/{project_id}/analyses", json={
        "method": "linear_regression",
        "dataset_version_ids": [_version_of(client, project_id)],
        # A many-column role sent as a list, which is the shape the form builds
        # and the shape the executor reads. A bare string validates and then
        # fails in the sandbox as a list of letters.
        "variables": {"outcome": "resistance_pct",
                      "predictors": ["consumption_ddd"]},
        "research_question": "Does consumption explain resistance?",
        "method_rationale": "Both are continuous and the relationship is linear.",
    })
    assert queued.status_code == 202, queued.text
    run_id = queued.json()["analysis_run_id"]
    _drain()

    listed = client.get(f"/api/projects/{project_id}/analyses")
    assert listed.status_code == 200, listed.text
    rows = {row["id"]: row for row in listed.json()}
    assert run_id in rows, "a specified run was executed and then not listed"

    row = rows[run_id]
    assert row["status"] == "completed", row
    assert row["origin"] == "specified"
    assert row["estimate"] is not None
    # The reason is recorded with the run rather than asked for afterwards
    # (§47), so the run's own screen has something to show.
    detail = client.get(f"/api/analyses/{run_id}").json()
    assert detail["method_rationale"].startswith("Both are continuous")


def test_a_discovery_run_is_listed_as_one(client):
    """The distinction the list has to carry: a swept run was corrected inside
    a family of tests, and a specified one stands alone."""
    project_id, swept = _signed_in_project(client)

    rows = {r["id"]: r for r in
            client.get(f"/api/projects/{project_id}/analyses").json()}

    assert rows[swept]["origin"] == "specified"


def _version_of(client, project_id: str) -> str:
    sources = client.get(f"/api/projects/{project_id}/sources").json()
    return sources[0]["dataset"]["dataset_version_id"]


def test_a_branch_taken_over_http_records_what_it_changed_and_why(client):
    project_id, base = _signed_in_project(client)

    forked = client.post(f"/api/analyses/{base}/fork", json={
        "reason": "The outcome is skewed, so a rank-based test is fairer.",
        "method": "spearman_correlation",
    })
    assert forked.status_code == 202, forked.text
    branch = forked.json()["analysis_run_id"]
    assert forked.json()["forked_from"] == base
    _drain()

    # The branch is a real run of the method that was asked for, and it knows
    # where it came from — which is what makes the pair legible afterwards.
    run = client.get(f"/api/analyses/{branch}").json()
    assert run["status"] == "completed", run.get("error")
    assert run["method"] == "spearman_correlation"
    assert run["forked_from_run_id"] == base
    assert "skewed" in run["fork_reason"]


def test_a_branch_is_refused_without_a_reason(client):
    """
    Reporting only the branch that worked is what this whole path exists to
    make visible, and a branch with no recorded reason cannot be read later.
    """
    _, base = _signed_in_project(client)
    assert client.post(f"/api/analyses/{base}/fork", json={"reason": ""}
                       ).status_code == 422


def test_the_comparison_answers_whether_the_conclusion_held(client):
    project_id, base = _signed_in_project(client)
    branch = client.post(f"/api/analyses/{base}/fork", json={
        "reason": "Rank-based check.", "method": "spearman_correlation",
    }).json()["analysis_run_id"]
    _drain()

    compared = client.get(f"/api/projects/{project_id}/analyses/compare",
                          params={"run_id": [base, branch]})
    assert compared.status_code == 200, compared.text
    body = compared.json()
    assert [r["run_id"] for r in body["runs"]] == [base, branch]
    # The verdict, which is the only question a set of branches raises.
    assert isinstance(body["conclusion_stable"], bool)
    assert body["note"]
    assert body["runs"][1]["fork_reason"] == "Rank-based check."


def test_comparing_a_run_from_another_project_is_refused(client):
    """Otherwise a comparison could quietly reach outside the project."""
    project_id, base = _signed_in_project(client)
    other = client.post("/api/projects", json={"name": "Elsewhere"}).json()["id"]
    assert client.get(f"/api/projects/{other}/analyses/compare",
                      params={"run_id": [base, base]}).status_code == 404


def test_one_run_is_not_a_comparison(client):
    project_id, base = _signed_in_project(client)
    assert client.get(f"/api/projects/{project_id}/analyses/compare",
                      params={"run_id": [base]}).status_code == 422


#: One outlier, and nothing else. Pearson reads a near-perfect linear
#: association (p ≈ 8e-15) because the outlier dominates the covariance;
#: Spearman, on ranks, sees the outlier as merely the largest value and finds
#: nothing (p ≈ 0.55). The disagreement is real rather than arranged, and it is
#: exactly the situation a sensitivity analysis exists to expose.
OUTLIER_CSV = b"""country,consumption_ddd,resistance_pct
a,1,8
b,2,3
c,3,9
d,4,2
e,5,7
f,6,4
g,7,10
h,8,1
i,9,6
j,10,5
k,11,11
l,12,2
m,13,9
n,14,3
o,200,300
"""


def test_branches_that_disagree_are_reported_as_a_dependency_on_a_choice(client):
    """
    The sentence this whole path exists to deliver, and the case the fork test
    above cannot reach: it uses a deliberately clean association where both
    methods agree.

    An earlier version of this test set the second run's result directly. The
    database refused it — a trigger holds a completed run immutable and says
    *"fork it instead of editing it"* — so the disagreement here is produced by
    data rather than asserted into existence, which is the better test anyway.
    """
    status = client.get("/api/auth/status").json()
    endpoint = "/api/auth/setup" if status["needs_setup"] else "/api/auth/login"
    client.post(endpoint, json={
        "email": "branches@lab.local", "display_name": "Branches",
        "password": "correct-horse-battery"})

    project_id = client.post("/api/projects", json={"name": "Outlier"}).json()["id"]
    client.post(f"/api/projects/{project_id}/sources",
                files={"file": ("outlier.csv", OUTLIER_CSV, "text/csv")})
    _drain()
    version_id = client.get(f"/api/projects/{project_id}/sources"
                            ).json()[0]["dataset"]["dataset_version_id"]

    base = client.post(f"/api/projects/{project_id}/analyses", json={
        "method": "pearson_correlation",
        "dataset_version_ids": [version_id],
        "variables": {"x": "consumption_ddd", "y": "resistance_pct"},
    }).json()["analysis_run_id"]
    _drain()

    branch = client.post(f"/api/analyses/{base}/fork", json={
        "reason": "One country is far from the rest; check it on ranks.",
        "method": "spearman_correlation",
    }).json()["analysis_run_id"]
    _drain()

    compared = client.get(f"/api/projects/{project_id}/analyses/compare",
                          params={"run_id": [base, branch]}).json()

    assert [r["statistically_significant"] for r in compared["runs"]] == [True, False]
    assert compared["conclusion_stable"] is False
    assert "depends on an analytical choice" in compared["note"]
