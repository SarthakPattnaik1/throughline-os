#!/usr/bin/env python3
"""Build one Pilot 02 handoff package from an existing completed run.

This is the producer-side command surface.  The consumer verifier remains a
separate stdlib-only program and never imports Throughline.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from throughline_domain import handoff
from throughline_domain.db import connection


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()

    with connection() as conn, conn.cursor() as cur:
        built = handoff.build(cur, args.run_id, args.destination)

    print(json.dumps({
        "path": str(built["path"]),
        "manifest_sha256": built["manifest_sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
