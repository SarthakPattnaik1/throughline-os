#!/usr/bin/env python3
"""Verify and consume a Pilot 02 handoff package without importing Throughline."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


EXPECTED_FILES = {
    "dataset/heart_failure_clinical_records_dataset.csv",
    "analysis/recorded-spec.json",
    "analysis/recorded-result.json",
    "replay/reproduce.py",
    "replay/replay-receipt.json",
}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _semantic_spec(recorded: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "analysis_type",
        "research_question",
        "variables",
        "filters",
        "transformations",
        "method",
        "method_rationale",
        "parameters",
        "confidence_level",
        "assumptions",
        "outputs_requested",
        "visualization_intent",
        "random_seed",
    )
    return {key: recorded.get(key) for key in keys}


def _compare_number(actual: float, expected: float, rule: dict[str, Any]) -> bool:
    return math.isclose(
        float(actual),
        float(expected),
        rel_tol=float(rule.get("rel_tol", 0.0)),
        abs_tol=float(rule.get("abs_tol", 0.0)),
    )


def verify(package: Path, contract_path: Path, trusted_manifest_sha256: str) -> dict[str, Any]:
    package = package.resolve()
    contract = _load(contract_path)
    manifest_path = package / "HANDOFF_MANIFEST.json"
    manifest_bytes = manifest_path.read_bytes()
    actual_manifest_hash = _sha256(manifest_bytes)
    if actual_manifest_hash != trusted_manifest_sha256:
        raise RuntimeError(
            "trusted manifest mismatch: "
            f"{actual_manifest_hash} != {trusted_manifest_sha256}"
        )

    manifest = json.loads(manifest_bytes)
    if manifest.get("schema") != "throughline.pilot02.handoff-manifest.v1":
        raise RuntimeError("unexpected handoff manifest schema")
    if manifest.get("contract_version") != contract["contract_version"]:
        raise RuntimeError("handoff contract version mismatch")
    if set(manifest.get("files", {})) != EXPECTED_FILES:
        raise RuntimeError("handoff file set does not match the frozen contract")

    for relative, expected in manifest["files"].items():
        payload = (package / relative).read_bytes()
        # Hash is the frozen identity failure reason for N3/N4. Check it first:
        # a mutation may change both size and digest, but the contract explicitly
        # requires those controls to fail on SHA-256 mismatch.
        if _sha256(payload) != expected["sha256"]:
            raise RuntimeError(f"{relative}: sha256 mismatch")
        if len(payload) != int(expected["bytes"]):
            raise RuntimeError(f"{relative}: byte length mismatch")

    source = contract["source"]
    dataset = (package / "dataset/heart_failure_clinical_records_dataset.csv").read_bytes()
    if len(dataset) != int(source["fixture_bytes"]):
        raise RuntimeError("dataset byte length does not match the frozen source")
    if _sha256(dataset) != source["fixture_sha256"]:
        raise RuntimeError("dataset sha256 does not match the frozen source")

    spec = _load(package / "analysis/recorded-spec.json")
    if _semantic_spec(spec) != contract["semantic_spec"]:
        raise RuntimeError("recorded semantic specification does not match the frozen contract")

    receipt = _load(package / "replay/replay-receipt.json")
    result = _load(package / "analysis/recorded-result.json")
    if receipt["analysis"]["spec_hash"] != spec["content_hash"]:
        raise RuntimeError("receipt spec hash does not match recorded specification")
    if receipt["inputs"]["dataset_content_hash"] != source["fixture_sha256"]:
        raise RuntimeError("receipt dataset hash does not match frozen input")
    if receipt["integrity"]["recorded_result_sha256"] != _sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ):
        raise RuntimeError("receipt result hash does not match recorded result")

    oracle = contract["oracle"]
    stored_rule = oracle["stored_vs_oracle"]
    expected = receipt["replay"]["expected"]
    if int(result["sample_size"]) != int(oracle["sample_size"]):
        raise RuntimeError("stored result sample size does not match oracle")
    if not _compare_number(result["estimate"], oracle["estimate"], stored_rule["estimate"]):
        raise RuntimeError("stored result estimate does not match oracle")
    if not _compare_number(result["p_value"], oracle["p_value_two_sided"], stored_rule["p_value"]):
        raise RuntimeError("stored result p-value does not match oracle")
    if int(expected["sample_size"]) != int(result["sample_size"]):
        raise RuntimeError("receipt sample size does not match recorded result")

    env = os.environ.copy()
    for key in list(env):
        if (
            key.startswith("THROUGHLINE_")
            or key.startswith("PIP_")
            or key in {"PYTHONPATH", "PYTHONHOME"}
        ):
            env.pop(key, None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    script = package / "replay/reproduce.py"
    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=package,
        env=env,
        text=True,
        encoding="utf-8",
        errors="strict",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"reproduction script failed ({completed.returncode}): {completed.stderr}"
        )

    printed: dict[str, float] = {}
    for line in completed.stdout.splitlines():
        key, sep, value = line.partition(" = ")
        if sep:
            printed[key.strip()] = float(value)

    comparison = receipt["replay"]["comparison"]
    if int(printed.get("n", -1)) != int(expected["sample_size"]):
        raise RuntimeError("reproduced sample size does not match receipt")
    if not _compare_number(printed["estimate"], expected["estimate"], comparison["estimate"]):
        raise RuntimeError("reproduced estimate does not match receipt")
    if not _compare_number(printed["p"], expected["p_value"], comparison["p_value"]):
        raise RuntimeError("reproduced p-value does not match receipt")

    return {
        "status": "passed",
        "implementation_sha": manifest["implementation_sha"],
        "run_id": manifest["run_id"],
        "handoff_manifest_sha256": actual_manifest_hash,
        "reproduced": {
            "sample_size": int(printed["n"]),
            "estimate": printed["estimate"],
            "p_value": printed["p"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("contract", type=Path)
    parser.add_argument("trusted_manifest_sha256")
    args = parser.parse_args()
    try:
        result = verify(args.package, args.contract, args.trusted_manifest_sha256)
    except Exception as exc:
        print(f"Pilot 02 consumer verification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
