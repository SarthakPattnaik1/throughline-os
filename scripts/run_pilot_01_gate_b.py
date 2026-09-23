#!/usr/bin/env python3
"""Run Pilot 01 Gate B as one ordered, cross-platform audit operation.

This does not make the run independent; the operator still must be a second
person. It removes procedural ambiguity: exact clean checkout verification,
fresh database home, bootstrap, frozen-environment verification, and the Pilot
test always happen in the same order and every command/result is recorded.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import shutil
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _venv_python() -> Path:
    return ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _fresh_home() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("USERPROFILE", str(Path.home())))
        return Path(tempfile.mkdtemp(prefix=".throughline-pilot01-gateb-", dir=base))
    return Path(tempfile.mkdtemp(prefix="throughline-pilot01-gateb-"))


def _run(command: list[str], env: dict[str, str], record: dict) -> None:
    shown = subprocess.list2cmdline(command) if os.name == "nt" else " ".join(command)
    step = {"argv": command, "command": shown}
    record["steps"].append(step)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    step["returncode"] = completed.returncode
    step["output"] = completed.stdout
    print(f"\n$ {shown}")
    print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.returncode != 0:
        record["status"] = "failed"
        raise subprocess.CalledProcessError(completed.returncode, command)


def _write_record(path: Path | None, record: dict) -> None:
    payload = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if path is None:
        print("\n--- Gate B audit record ---")
        print(payload, end="")
        return
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    print(f"\nGate B audit record: {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "expected_commit",
        help="exact commit SHA supplied for the independent Gate B run",
    )
    parser.add_argument(
        "--operator",
        required=True,
        help="name or handle of the independent second-person auditor",
    )
    parser.add_argument(
        "--record",
        type=Path,
        help="optional JSON record path; keep it outside the repository",
    )
    args = parser.parse_args()

    if args.record is not None:
        record_path = args.record.expanduser().resolve()
        try:
            record_path.relative_to(ROOT)
        except ValueError:
            pass
        else:
            parser.error("--record must be outside the repository so audit output cannot dirty the checkout")

    record = {
        "schema": "throughline.pilot-01-gate-b.v1",
        "operator": args.operator,
        "operator_role_required": "independent-second-person",
        "expected_commit": args.expected_commit,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "launcher_python": platform.python_version(),
        "sanitized_environment": [
            "THROUGHLINE_DATABASE_URL",
            "THROUGHLINE_ALLOW_INSTALLED_HOME",
            "PYTHONPATH",
            "PYTHONHOME",
        ],
        "reused_virtualenv": False,
        "status": "running",
        "steps": [],
    }

    env = os.environ.copy()

    # Gate B must not inherit machine-local execution overrides. An exported
    # database URL outranks the fresh test home, and PYTHONPATH can shadow the
    # audited checkout/venv with arbitrary packages from elsewhere.
    for key in (
        "THROUGHLINE_DATABASE_URL",
        "THROUGHLINE_ALLOW_INSTALLED_HOME",
        "PYTHONPATH",
        "PYTHONHOME",
    ):
        env.pop(key, None)

    # .venv is intentionally gitignored, so a clean Git tree does not prove the
    # audit is using a fresh environment. Remove any pre-existing virtualenv
    # before bootstrap; Gate B must create its own from the pinned runtime.
    existing_venv = ROOT / ".venv"
    if existing_venv.exists():
        shutil.rmtree(existing_venv)
    if existing_venv.exists():
        raise RuntimeError(f"could not remove pre-existing audit virtualenv: {existing_venv}")

    audit_home = _fresh_home()
    env["THROUGHLINE_TEST_HOME"] = str(audit_home)
    env["THROUGHLINE_HOME"] = str(audit_home)
    record["test_home"] = str(audit_home)

    try:
        _run(
            [
                sys.executable,
                "scripts/verify_pilot_01.py",
                "--expected-commit",
                args.expected_commit,
                "--clean-tree",
            ],
            env,
            record,
        )
        _run([sys.executable, "scripts/manage.py", "bootstrap"], env, record)

        venv_python = _venv_python()
        if not venv_python.exists():
            raise RuntimeError(f"bootstrap did not create {venv_python}")

        _run(
            [
                str(venv_python),
                "scripts/verify_pilot_01.py",
                "--expected-commit",
                args.expected_commit,
                "--clean-tree",
                "--environment",
            ],
            env,
            record,
        )
        _run(
            [
                str(venv_python),
                "-m",
                "pytest",
                "tests/test_pilot_01_palmer_penguins.py",
                "-q",
            ],
            env,
            record,
        )
        record["status"] = "passed"
        return_code = 0
    except (subprocess.CalledProcessError, RuntimeError) as exc:
        record["error"] = str(exc)
        record["status"] = "failed"
        return_code = 1
    finally:
        record["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        _write_record(args.record, record)

    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
