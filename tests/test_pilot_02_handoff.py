"""Pilot 02 Gate A: producer-to-consumer handoff identity and replay."""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from throughline_domain import analysis, code_export, handoff, objects, replay_receipt, storage, workflow
from throughline_domain.db import connection
from throughline_domain.ids import new_id
from throughline_schemas.enums import SourceType
from throughline_workers.runner import Worker

ROOT = Path(__file__).parent.parent
DATA = ROOT / "tests/fixtures/pilot_02/heart_failure_clinical_records_dataset.csv"
CONTRACT_SHA256 = "cd4a8d9ac6af9aa82c38a89d595d8e9446127c94e546a148e686b0abb90151f5"
DATA_SHA256 = "9c73cea7468ff5d517801ec050fe9993da5912fce4b56f296f8df3b38dd75912"
DATA_BYTES = 12239
EXPECTED_N = 299
EXPECTED_R = 0.2942775609841492
EXPECTED_P = 2.1901978548979836e-07
QUESTION = "Is serum creatinine associated with the recorded death-event indicator in the frozen UCI Heart Failure Clinical Records dataset?"
RATIONALE = "Pearson correlation between a continuous variable and a numeric 0/1 indicator is the point-biserial special case. Pilot 02 tests replay and provenance fidelity, not clinical causality or predictive validity."
RECEIPT_REFUSAL = "Recorded y variable 'death_event' requires translation to file header 'DEATH_EVENT'; replay receipt v1 does not perform that translation yet."
EXPORT_REFUSAL = RECEIPT_REFUSAL + " Replay-supported methods: pearson_correlation, spearman_correlation."


def _contract(path: Path) -> Path:
    value = {
        "contract_version": "1.0.0",
        "source": {"fixture_bytes": DATA_BYTES, "fixture_sha256": DATA_SHA256},
        "semantic_spec": {
            "analysis_type": "statistical",
            "research_question": QUESTION,
            "method": "pearson_correlation",
            "variables": {"x": "serum_creatinine", "y": "DEATH_EVENT"},
            "filters": [], "transformations": [], "parameters": {},
            "confidence_level": 0.95, "assumptions": [], "outputs_requested": [],
            "visualization_intent": "", "random_seed": 0,
            "method_rationale": RATIONALE,
        },
        "oracle": {
            "sample_size": EXPECTED_N,
            "estimate": EXPECTED_R,
            "p_value_two_sided": EXPECTED_P,
            "stored_vs_oracle": {
                "estimate": {"abs_tol": 1e-12},
                "p_value": {"rel_tol": 1e-10, "abs_tol": 0.0},
                "sample_size": {"exact": True},
            },
        },
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _drain():
    while Worker(worker_id="pilot-02").run_once():
        pass


@pytest.fixture()
def heart_project():
    payload = DATA.read_bytes()
    assert len(payload) == DATA_BYTES
    assert hashlib.sha256(payload).hexdigest() == DATA_SHA256
    user_id, project_id = new_id("usr"), new_id("prj")
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users(id,email,display_name,password_hash,password_salt) VALUES (%s,%s,%s,'x','y')",
            (user_id, f"{user_id}@test.local", "Pilot 02"),
        )
        cur.execute(
            "INSERT INTO projects(id,owner_user_id,name,research_question) VALUES (%s,%s,%s,%s)",
            (project_id, user_id, "Pilot 02", QUESTION),
        )
        record = storage.register_file(
            cur, project_id=project_id, filename=DATA.name,
            stream=io.BytesIO(payload), media_type="text/csv",
        )
        source_id = objects.create_source(
            cur, project_id=project_id, source_type=SourceType.UPLOAD,
            title="UCI Heart Failure Clinical Records", actor="pilot-02",
            file_id=str(record["id"]), content_hash=str(record["content_hash"]),
        )
        workflow.enqueue(cur, workflow_name="ingest.source", project_id=project_id,
                         payload={"source_id": source_id})
    _drain()
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT dv.id FROM dataset_versions dv JOIN datasets d ON d.id=dv.dataset_id WHERE d.source_id=%s",
            (source_id,),
        )
        version_id = cur.fetchone()["id"]
    yield project_id, version_id
    with connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE id=%s", (user_id,))


def _analyse(project_id: str, version_id: str, y: str) -> str:
    with connection() as conn, conn.cursor() as cur:
        created = analysis.create_spec(cur, project_id=project_id, actor="pilot-02", spec={
            "analysis_type": "statistical", "research_question": QUESTION,
            "dataset_version_ids": [version_id],
            "variables": {"x": "serum_creatinine", "y": y},
            "filters": [], "transformations": [], "method": "pearson_correlation",
            "method_rationale": RATIONALE, "parameters": {}, "confidence_level": 0.95,
            "assumptions": [], "outputs_requested": [], "visualization_intent": "",
            "random_seed": 0,
        })
        run_id = analysis.create_run(cur, project_id=project_id, spec_id=created["spec_id"])
        workflow.enqueue(cur, workflow_name="analysis.run", project_id=project_id,
                         payload={"analysis_run_id": run_id}, idempotency_key=f"analysis:{run_id}")
    _drain()
    return run_id


def _verify(package: Path, contract: Path, anchor: str):
    contract_sha256 = hashlib.sha256(contract.read_bytes()).hexdigest()
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/verify_pilot_02_handoff.py"),
         str(package), str(contract), anchor, contract_sha256],
        capture_output=True, text=True, check=False,
    )


def _rehash_manifest(package: Path) -> str:
    path = package / "HANDOFF_MANIFEST.json"
    manifest = json.loads(path.read_text())
    for rel in list(manifest["files"]):
        payload = (package / rel).read_bytes()
        manifest["files"][rel] = {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    raw = (json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def test_pilot_02_gate_a(heart_project, tmp_path):
    project_id, version_id = heart_project
    run_id = _analyse(project_id, version_id, "DEATH_EVENT")
    with connection() as conn, conn.cursor() as cur:
        run = analysis.get_run(cur, run_id)
        assert run["status"] == "completed", run["error"]
        assert run["result"]["sample_size"] == EXPECTED_N
        assert run["result"]["estimate"] == pytest.approx(EXPECTED_R, abs=1e-12)
        assert run["result"]["p_value"] == pytest.approx(EXPECTED_P, rel=1e-10)
        built = handoff.build(cur, run_id, tmp_path / "handoff")

    contract = _contract(tmp_path / "contract.json")
    good = _verify(built["path"], contract, built["manifest_sha256"])
    assert good.returncode == 0, good.stderr
    assert json.loads(good.stdout)["status"] == "passed"

    # N1: same Pearson values, different row order, self-consistent internal manifest.
    n1 = tmp_path / "n1"; shutil.copytree(built["path"], n1)
    data = n1 / "dataset" / DATA.name
    lines = data.read_text().splitlines()
    data.write_text("\n".join([lines[0], *reversed(lines[1:])]) + "\n")
    forged_anchor = _rehash_manifest(n1)
    assert forged_anchor != built["manifest_sha256"]
    bad = _verify(n1, contract, built["manifest_sha256"])
    assert bad.returncode != 0 and "trusted manifest mismatch" in bad.stderr

    # N2: same-number symmetric spec, with a self-consistent internal manifest.
    n2 = tmp_path / "n2"; shutil.copytree(built["path"], n2)
    spec_path = n2 / "analysis/recorded-spec.json"
    spec = json.loads(spec_path.read_text())
    spec["variables"] = {"x": "DEATH_EVENT", "y": "serum_creatinine"}
    spec_path.write_text(json.dumps(spec, sort_keys=True, separators=(",", ":")) + "\n")
    _rehash_manifest(n2)
    bad = _verify(n2, contract, built["manifest_sha256"])
    assert bad.returncode != 0 and "trusted manifest mismatch" in bad.stderr

    # N3/N4: mutation without manifest update is rejected before execution.
    n3 = tmp_path / "n3"; shutil.copytree(built["path"], n3)
    with (n3 / "replay/reproduce.py").open("a") as handle: handle.write("\n# changed\n")
    bad = _verify(n3, contract, built["manifest_sha256"])
    assert bad.returncode != 0 and "replay/reproduce.py: sha256 mismatch" in bad.stderr

    n4 = tmp_path / "n4"; shutil.copytree(built["path"], n4)
    receipt_path = n4 / "replay/replay-receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["replay"]["comparison"]["estimate"]["abs_tol"] = 0.1
    receipt_path.write_text(json.dumps(receipt) + "\n")
    bad = _verify(n4, contract, built["manifest_sha256"])
    assert bad.returncode != 0 and "replay/replay-receipt.json: sha256 mismatch" in bad.stderr

    # U1: the frozen comparator rejects an estimate displaced by 0.001 and p=0.
    assert EXPECTED_R + 0.001 != pytest.approx(EXPECTED_R, abs=1e-12)
    assert 0.0 != pytest.approx(EXPECTED_P, rel=1e-10, abs=0.0)


def test_pilot_02_alias_refusal_is_exact(heart_project):
    project_id, version_id = heart_project
    run_id = _analyse(project_id, version_id, "death_event")
    with connection() as conn, conn.cursor() as cur:
        run = analysis.get_run(cur, run_id)
        assert run["status"] == "completed", run["error"]
        with pytest.raises(replay_receipt.CannotReceipt) as exc:
            replay_receipt.for_run(cur, run_id)
        assert str(exc.value) == RECEIPT_REFUSAL
        with pytest.raises(code_export.CannotEmit) as exc:
            code_export.for_run(cur, run_id)
        assert str(exc.value) == EXPORT_REFUSAL
