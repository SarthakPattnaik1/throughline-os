"""Pilot 01 dry run: one replay-supported analysis and one honest refusal.

Dataset:
  Palmer Penguins simplified penguins.csv, 344 rows.
  Source: https://github.com/allisonhorst/palmerpenguins
  Data license: CC0, per the upstream project.

Frozen acceptance criteria for this internal dry run:
1. The unfiltered Pearson correlation between flipper_length_mm and body_mass_g
   completes through the normal ingestion -> analysis worker path.
2. Its replay receipt binds dataset/spec hashes and expected headline values.
3. Its generated reproduction script executes against the same public dataset
   and reproduces estimate, p-value, and n inside the receipt tolerances.
4. The recorded value agrees with a direct independent calculation over the
   same complete-case rows.
5. The nearby Pearson analysis filtered to species == Adelie also completes as
   a scientific run, but both replay artifacts refuse it specifically because
   declarative filters are outside replay receipt v1.
6. A refusal is a pass only when it happens for the predeclared contract reason.

Audit gates:
- Gate A: this internal executable dry run. It may be run by the author or CI.
- Gate B: a separate second-person audit performed from a clean checkout under
  tests/fixtures/pilot_01/AUDIT_PROTOCOL.md.

Passing Gate A never satisfies Gate B. Pilot 01 is frozen only after both gates
pass against the same commit and the exact source bytes pinned below.
"""

from __future__ import annotations

import hashlib
import io
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
from scipy import stats
from throughline_domain import (
    analysis,
    code_export,
    objects,
    replay_receipt,
    storage,
    visuals,
    workflow,
)
from throughline_domain.db import connection
from throughline_domain.ids import new_id
from throughline_schemas.enums import SourceType
from throughline_visual.spec import VisualType
from throughline_workers.runner import Worker


DATA = Path(__file__).parent / "fixtures" / "pilot_01" / "penguins.csv"

# Audit gate: these identify the exact source bytes, not merely a filename or
# an upstream repository that could change. If the fixture changes for any
# reason, Pilot 01 is no longer the same frozen evaluation and both audit gates
# must be rerun deliberately.
DATA_SHA256 = "f204db2c753b0937caac3cb35258562c14f073e4bbc76be24b4c51ce22767a93"
DATA_BYTES = 15_241

# Frozen independently from Throughline using the public CSV itself.
# These are not generated from a Throughline run, so agreement cannot pass
# merely because two Throughline surfaces share the same wrong value.
EXPECTED_N = 342
EXPECTED_R = 0.8712017673060112
EXPECTED_P = 4.370680963000641e-107
EXPECTED_CI_LOW = 0.8430410511899511
EXPECTED_CI_HIGH = 0.8945989840406315


def _frozen_source_bytes() -> bytes:
    payload = DATA.read_bytes()
    actual = hashlib.sha256(payload).hexdigest()
    assert len(payload) == DATA_BYTES, (
        f"Pilot 01 source CSV byte length changed: {len(payload)} != {DATA_BYTES}. "
        "This is a different evaluation input; do not update the pin silently."
    )
    assert actual == DATA_SHA256, (
        f"Pilot 01 source CSV SHA-256 changed: {actual} != {DATA_SHA256}. "
        "This is a different evaluation input; do not update the pin silently."
    )
    return payload


def test_source_csv_is_exactly_the_frozen_input():
    """Gate A fails immediately if the Pilot 01 source bytes drift."""
    _frozen_source_bytes()


def _drain() -> None:
    while Worker(worker_id="pilot-01").run_once():
        pass


@pytest.fixture()
def penguins_project():
    payload = _frozen_source_bytes()
    user_id, project_id = new_id("usr"), new_id("prj")
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users(id, email, display_name, password_hash, password_salt) "
            "VALUES (%s, %s, %s, 'x', 'y')",
            (user_id, f"{user_id}@test.local", "Pilot 01"),
        )
        cur.execute(
            "INSERT INTO projects(id, owner_user_id, name, research_question) "
            "VALUES (%s, %s, 'Pilot 01 — Palmer Penguins', %s)",
            (
                project_id,
                user_id,
                "How strongly are penguin flipper length and body mass associated?",
            ),
        )
        record = storage.register_file(
            cur,
            project_id=project_id,
            filename="penguins.csv",
            stream=io.BytesIO(payload),
            media_type="text/csv",
        )
        assert str(record["content_hash"]) == DATA_SHA256
        source_id = objects.create_source(
            cur,
            project_id=project_id,
            source_type=SourceType.UPLOAD,
            title="Palmer Penguins",
            actor="pilot-01",
            file_id=str(record["id"]),
            content_hash=str(record["content_hash"]),
        )
        workflow.enqueue(
            cur,
            workflow_name="ingest.source",
            project_id=project_id,
            payload={"source_id": source_id},
        )

    _drain()

    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT dv.id FROM dataset_versions dv "
            "JOIN datasets d ON d.id = dv.dataset_id "
            "WHERE d.source_id = %s",
            (source_id,),
        )
        row = cur.fetchone()
        assert row, "Palmer Penguins fixture did not ingest as a dataset"
        version_id = row["id"]

    yield project_id, version_id

    with connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))


def _analyse(project_id: str, version_id: str, *, filters=None) -> str:
    with connection() as conn, conn.cursor() as cur:
        created = analysis.create_spec(
            cur,
            project_id=project_id,
            spec={
                "method": "pearson_correlation",
                "dataset_version_ids": [version_id],
                "variables": {
                    "x": "flipper_length_mm",
                    "y": "body_mass_g",
                },
                "filters": filters or [],
                "research_question": (
                    "Is flipper length associated with body mass in Palmer penguins?"
                ),
                "method_rationale": (
                    "Both measurements are continuous; Pilot 01 is testing the "
                    "current Pearson replay contract rather than making a causal claim."
                ),
            },
            actor="pilot-01",
        )
        run_id = analysis.create_run(
            cur, project_id=project_id, spec_id=created["spec_id"]
        )
        workflow.enqueue(
            cur,
            workflow_name="analysis.run",
            project_id=project_id,
            payload={"analysis_run_id": run_id},
            idempotency_key=f"analysis:{run_id}",
        )
    _drain()
    return run_id


def _run(run_id: str) -> dict:
    with connection() as conn, conn.cursor() as cur:
        return analysis.get_run(cur, run_id)


def _execute_exported_script(script: str, tmp_path: Path) -> dict[str, float]:
    match = re.search(r"pd\.read_csv\((.*?)\)", script)
    assert match, "Generated replay script did not contain its dataset read"
    script = script.replace(match.group(1), repr(str(DATA)))
    path = tmp_path / "pilot_01_reproduce.py"
    path.write_text(script, encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr

    values: dict[str, float] = {}
    for line in done.stdout.splitlines():
        key, _, value = line.partition(" = ")
        values[key.strip()] = float(value)
    return values


def test_supported_run_replays_under_the_frozen_contract(
    penguins_project, tmp_path
):
    project_id, version_id = penguins_project
    run_id = _analyse(project_id, version_id)
    run = _run(run_id)

    assert run["status"] == "completed", run["error"]
    assert run["input_hashes"]["dataset_content_hash"] == DATA_SHA256
    assert run["input_hashes"]["spec_content_hash"]
    assert run["dependency_versions"]["pandas"]
    assert run["dependency_versions"]["scipy"]
    assert run["sandbox_policy"]["enforced"]["separate_process"] is True

    with connection() as conn, conn.cursor() as cur:
        receipt = replay_receipt.for_run(cur, run_id)
        script = code_export.for_run(cur, run_id)

    assert receipt["format"] == "throughline.replay-receipt.v1"
    assert receipt["analysis"]["method"] == "pearson_correlation"
    assert receipt["analysis"]["spec_hash"] == run["input_hashes"]["spec_content_hash"]
    assert (
        receipt["inputs"]["dataset_content_hash"]
        == run["input_hashes"]["dataset_content_hash"]
        == DATA_SHA256
    )

    printed = _execute_exported_script(script, tmp_path)
    comparison = receipt["replay"]["comparison"]
    expected = receipt["replay"]["expected"]

    assert printed["estimate"] == pytest.approx(
        expected["estimate"], abs=comparison["estimate"]["abs_tol"]
    )
    assert printed["p"] == pytest.approx(
        expected["p_value"],
        rel=comparison["p_value"]["rel_tol"],
        abs=comparison["p_value"]["abs_tol"],
    )
    assert printed["n"] == expected["sample_size"] == run["result"]["sample_size"]

    frame = pd.read_csv(DATA)
    paired = frame[["flipper_length_mm", "body_mass_g"]].dropna()
    independent = stats.pearsonr(
        paired["flipper_length_mm"], paired["body_mass_g"]
    )

    assert run["result"]["sample_size"] == len(paired) == EXPECTED_N
    assert run["result"]["estimate"] == pytest.approx(EXPECTED_R, abs=1e-12)
    assert run["result"]["p_value"] == pytest.approx(EXPECTED_P, rel=1e-10)
    assert run["result"]["ci_low"] == pytest.approx(EXPECTED_CI_LOW, abs=1e-10)
    assert run["result"]["ci_high"] == pytest.approx(EXPECTED_CI_HIGH, abs=1e-10)

    # A second implementation check against scipy on the raw public rows.
    assert run["result"]["estimate"] == pytest.approx(float(independent.statistic))
    assert run["result"]["p_value"] == pytest.approx(float(independent.pvalue))




def test_supported_run_produces_a_faithful_publishable_figure(penguins_project):
    """The figure must carry the same numbers as the run, not recompute them."""
    project_id, version_id = penguins_project
    run_id = _analyse(project_id, version_id)
    run = _run(run_id)
    assert run["status"] == "completed", run["error"]

    frame = pd.read_csv(DATA)
    paired = frame[["flipper_length_mm", "body_mass_g"]].dropna()
    sample = {
        "flipper_length_mm": paired["flipper_length_mm"].tolist(),
        "body_mass_g": paired["body_mass_g"].tolist(),
    }

    with connection() as conn, conn.cursor() as cur:
        recommendation = visuals.recommend_for_run(
            cur, analysis_run_id=run_id
        )
        assert recommendation["visual_type"] is VisualType.SCATTER
        spec = recommendation["spec"]

        # Human-facing labels/titles must not expose raw schema underscores.
        assert spec.x.label == "flipper length mm"
        assert spec.y.label == "body mass g"
        assert spec.title == "body mass g against flipper length mm"
        assert "Association does not establish causation." in spec.caption
        assert "pearson_r = 0.871" in spec.caption
        assert "n = 342" in spec.caption

        made = visuals.create_visual(
            cur,
            project_id=project_id,
            spec=spec,
            actor="pilot-01",
            sample=sample,
            recommendation=recommendation,
        )
        assert made["publishable"] is True
        assert made["exportable"] is True

        # The figure's statistics are copied from the immutable run.
        figure_stats = made["data"].statistics
        assert figure_stats["sample_size"] == EXPECTED_N
        assert figure_stats["estimate"] == pytest.approx(EXPECTED_R, abs=1e-12)
        assert figure_stats["p_value"] == pytest.approx(EXPECTED_P, rel=1e-10)
        assert figure_stats["ci_low"] == pytest.approx(EXPECTED_CI_LOW, abs=1e-10)
        assert figure_stats["ci_high"] == pytest.approx(EXPECTED_CI_HIGH, abs=1e-10)

        # At 342 complete cases the plot is small enough to show every point.
        assert len(made["data"].x_values) == EXPECTED_N
        assert len(made["data"].y_values) == EXPECTED_N

        rendered = visuals.render_visual(
            cur, visual_id=made["visual_id"], fmt="svg"
        )

    svg_path = storage.path_for(rendered["storage_key"])
    assert svg_path.exists() and svg_path.stat().st_size > 1_000
    svg = svg_path.read_text(encoding="utf-8")

    # Matplotlib keeps SVG text as text, so the exported figure can be audited.
    assert "body mass g against flipper length mm" in svg
    assert "flipper length mm" in svg
    assert "body mass g" in svg
    assert "pearson_r = 0.871" in svg
    assert "n = 342" in svg
    assert "Association does not establish causation." in svg

def test_species_filtered_neighbor_is_refused_for_the_declared_reason(
    penguins_project,
):
    project_id, version_id = penguins_project
    run_id = _analyse(
        project_id,
        version_id,
        filters=[{"column": "species", "operator": "eq", "value": "Adelie"}],
    )
    run = _run(run_id)

    # The analysis itself is valid and really ran. Only the replay claim is refused.
    assert run["status"] == "completed", run["error"]
    assert 0 < run["result"]["sample_size"] < 342

    with connection() as conn, conn.cursor() as cur:
        with pytest.raises(code_export.CannotEmit, match="filters"):
            code_export.for_run(cur, run_id)
        with pytest.raises(replay_receipt.CannotReceipt, match="filters"):
            replay_receipt.for_run(cur, run_id)
