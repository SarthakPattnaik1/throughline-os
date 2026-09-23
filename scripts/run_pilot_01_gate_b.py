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
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        print(f"\nCould not write Gate B audit record to {path}: {exc}", file=sys.stderr)
        print("--- Gate B audit record fallback ---", file=sys.stderr)
        print(payload, end="", file=sys.stderr)
        raise
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
        "sanitized_environment": "all inherited THROUGHLINE_*/PIP_* plus PYTHONPATH/PYTHONHOME",
        "reused_virtualenv": False,
        "preexisting_virtualenv_removed": False,
        "status": "running",
        "steps": [],
    }

    env = os.environ.copy()
    return_code = 1

    try:
        # Gate B must not inherit machine-local execution overrides. An exported
        # database URL outranks the fresh test home, and PYTHONPATH can shadow the
        # audited checkout/venv with arbitrary packages from elsewhere.
        for key in list(env):
            if (
                key.startswith("THROUGHLINE_")
                or key.startswith("PIP_")
                or key in {"PYTHONPATH", "PYTHONHOME"}
            ):
                env.pop(key, None)

        # .venv is intentionally gitignored, so a clean Git tree does not prove
        # the audit is using a fresh environment. Remove it before bootstrap.
        existing_venv = ROOT / ".venv"
        if existing_venv.exists():
            record["preexisting_virtualenv_removed"] = True
            shutil.rmtree(existing_venv)
        if existing_venv.exists():
            raise RuntimeError(
                f"could not remove pre-existing audit virtualenv: {existing_venv}"
            )

        audit_home = _fresh_home()
        env["THROUGHLINE_TEST_HOME"] = str(audit_home)
        env["THROUGHLINE_HOME"] = str(audit_home)
        env["THROUGHLINE_RUNTIME_DIR"] = str(audit_home / "runtimes")
        record["test_home"] = str(audit_home)
        record["runtime_home"] = env["THROUGHLINE_RUNTIME_DIR"]

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
        # Fetch the reviewed CPython archive into an audit-private runtime
        # directory first. This avoids trusting a machine-global cached binary,
        # which runtimes.ensure() otherwise accepts based on existence alone.
        runtime_step = [sys.executable, "scripts/runtimes.py", "python"]
        _run(runtime_step, env, record)

        runtime = __import__("runpy").run_path(str(ROOT / "scripts" / "runtimes.py"))
        pinned_python = runtime["executable"]("python", Path(env["THROUGHLINE_RUNTIME_DIR"]))
        if not Path(pinned_python).exists():
            raise RuntimeError(f"pinned audit Python was not created: {pinned_python}")

        _run([str(pinned_python), "scripts/manage.py", "bootstrap"], env, record)

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
        # The core scientific stack is pinned, but the broader workspace still
        # contains transitive/floor-bounded packages. Record the complete
        # resolved environment so the audit evidence says exactly what ran.
        _run(
            [str(venv_python), "-m", "pip", "freeze", "--all"],
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
    except KeyboardInterrupt:
        record["status"] = "interrupted"
        record["error"] = "KeyboardInterrupt"
        return_code = 130
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["status"] = "failed"
        return_code = 1
    finally:
        record["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        _write_record(args.record, record)

    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
