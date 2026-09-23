"""Verify the frozen Pilot 01 input using only the Python standard library.

This intentionally works before Throughline is bootstrapped.  Gate B uses it
immediately after checkout so line-ending conversion, wrong fixture bytes, or
metadata drift fail before any analysis or environment setup begins.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "tests" / "fixtures" / "pilot_01" / "FROZEN_INPUT.json"


def _contract() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def verify_input() -> dict:
    contract = _contract()
    path = ROOT / contract["fixture_path"]
    payload = path.read_bytes()

    actual_bytes = len(payload)
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    git_blob = hashlib.sha1(
        f"blob {actual_bytes}\0".encode("ascii") + payload,
        usedforsecurity=False,
    ).hexdigest()

    expected_bytes = int(contract["bytes"])
    expected_sha256 = str(contract["sha256"])
    expected_blob = str(contract["upstream"]["git_blob_sha"])

    if actual_bytes != expected_bytes:
        raise SystemExit(
            f"Pilot 01 frozen CSV byte count changed: "
            f"{actual_bytes} != {expected_bytes}"
        )
    if actual_sha256 != expected_sha256:
        raise SystemExit(
            f"Pilot 01 frozen CSV SHA-256 changed: "
            f"{actual_sha256} != {expected_sha256}"
        )
    if git_blob != expected_blob:
        raise SystemExit(
            f"Pilot 01 frozen CSV Git blob changed: "
            f"{git_blob} != {expected_blob}"
        )

    print(
        "Pilot 01 frozen CSV verified: "
        f"{actual_bytes} bytes, sha256={actual_sha256}, git_blob={git_blob}"
    )
    print(
        "Canonical upstream: "
        f"{contract['upstream']['repository']}@{contract['upstream']['commit']}:"
        f"{contract['upstream']['path']}"
    )
    return contract


def print_environment() -> None:
    """Print the replay environment after bootstrap without claiming to lock it."""
    import numpy
    import pandas
    import scipy
    import statsmodels

    print(f"platform={platform.platform()}")
    print(f"python={platform.python_version()} ({sys.executable})")
    print(f"pandas={pandas.__version__}")
    print(f"numpy={numpy.__version__}")
    print(f"scipy={scipy.__version__}")
    print(f"statsmodels={statsmodels.__version__}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--environment",
        action="store_true",
        help="also print the installed scientific runtime versions",
    )
    args = parser.parse_args()

    verify_input()
    if args.environment:
        print_environment()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
