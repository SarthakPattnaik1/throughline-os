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

This is an internal executable dry run, not the external second-person audit.
"""

from __future__ import annotations

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
    workflow,
)
from throughline_domain.db import connection
from throughline_domain.ids import new_id
from throughline_schemas.enums import SourceType
from throughline_workers.runner import Worker


DATA = Path(__file__).parent / "fixtures" / "pilot_01" / "penguins.csv"


def _drain() -> None:
    while Worker(worker_id="pilot-01").run_once():
        pass


@pytest.fixture()
def penguins_project():
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
            stream=io.BytesIO(DATA.read_bytes()),
            media_type="text/csv",
        )
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
    assert run["input_hashes"]["dataset_content_hash"]
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

    assert run["result"]["sample_size"] == len(paired) == 342
    assert run["result"]["estimate"] == pytest.approx(float(independent.statistic))
    assert run["result"]["p_value"] == pytest.approx(float(independent.pvalue))


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
