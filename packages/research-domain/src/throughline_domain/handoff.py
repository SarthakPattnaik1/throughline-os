"""Build the exact portable evidence package used by Pilot 02.

The package is intentionally small and byte-addressed.  The manifest binds the
five evidence files; its own digest must be captured outside the package before
consumer execution.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from . import analysis, code_export, replay_receipt, storage

FORMAT = "throughline.pilot02.handoff-manifest.v1"
CONTRACT_VERSION = "1.0.0"
DATASET_NAME = "heart_failure_clinical_records_dataset.csv"


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _identity(payload: bytes) -> dict[str, Any]:
    return {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def build(cur, run_id: str, destination: Path) -> dict[str, Any]:
    """Write one complete Pilot 02 handoff package for an eligible run."""
    run = analysis.get_run(cur, run_id)
    if not run or run.get("status") != "completed":
        raise ValueError(f"Pilot 02 handoff requires a completed run: {run_id}")

    cur.execute(
        """
        SELECT r.spec_id, s.*, dv.id AS dataset_version_id,
               dv.storage_key AS dataset_storage_key
          FROM analysis_runs r
          JOIN analysis_specs s ON s.id = r.spec_id
          JOIN dataset_versions dv
            ON dv.id = (s.dataset_version_ids ->> 0)
         WHERE r.id = %s
        """,
        (run_id,),
    )
    row = cur.fetchone()
    if not row:
        raise LookupError(f"Unknown analysis run: {run_id}")
    spec = dict(row)

    destination = Path(destination)
    if destination.exists():
        shutil.rmtree(destination)
    (destination / "dataset").mkdir(parents=True)
    (destination / "analysis").mkdir()
    (destination / "replay").mkdir()

    dataset_path = storage.path_for(str(spec["dataset_storage_key"]))
    dataset_bytes = dataset_path.read_bytes()

    recorded_spec = {
        "id": spec["id"],
        "content_hash": spec["content_hash"],
        "schema_version": spec["schema_version"],
        "analysis_type": spec["analysis_type"],
        "research_question": spec["research_question"],
        "dataset_version_ids": list(spec["dataset_version_ids"] or []),
        "variables": dict(spec["variables"] or {}),
        "filters": list(spec["filters"] or []),
        "transformations": list(spec["transformations"] or []),
        "method": spec["method"],
        "method_rationale": spec["method_rationale"],
        "parameters": dict(spec["parameters"] or {}),
        "confidence_level": float(spec["confidence_level"]),
        "assumptions": list(spec["assumptions"] or []),
        "outputs_requested": list(spec["outputs_requested"] or []),
        "visualization_intent": spec["visualization_intent"] or "",
        "random_seed": int(spec["random_seed"]),
    }
    result = dict(run["result"] or {})
    receipt = replay_receipt.for_run(cur, run_id)
    script = code_export.for_run(
        cur,
        run_id,
        dataset_relative_path=f"dataset/{DATASET_NAME}",
    )

    files: dict[str, bytes] = {
        f"dataset/{DATASET_NAME}": dataset_bytes,
        "analysis/recorded-spec.json": _json_bytes(recorded_spec),
        "analysis/recorded-result.json": _json_bytes(result),
        "replay/reproduce.py": script.encode("utf-8"),
        "replay/replay-receipt.json": _json_bytes(receipt),
    }
    for relative, payload in files.items():
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    build_identity = dict(receipt["execution"]["throughline"])
    implementation_sha = build_identity.get("commit")
    if not implementation_sha:
        raise ValueError("Pilot 02 requires a recorded Throughline implementation commit.")

    manifest = {
        "schema": FORMAT,
        "contract_version": CONTRACT_VERSION,
        "implementation_sha": implementation_sha,
        "run_id": run_id,
        "dataset_version_id": str(spec["dataset_version_id"]),
        "analysis_spec_id": str(spec["id"]),
        "files": {name: _identity(payload) for name, payload in files.items()},
    }
    manifest_bytes = _json_bytes(manifest)
    (destination / "HANDOFF_MANIFEST.json").write_bytes(manifest_bytes)
    return {
        "path": destination,
        "manifest": manifest,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }
