#!/usr/bin/env python3
"""Capture PostgreSQL + object storage as one consistency window.

The database dump and object archive are one recovery unit. Holding SHARE locks
on every public application table prevents INSERT/UPDATE/DELETE from committing
between those two captures while remaining compatible with pg_dump's reads.
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import tarfile

import pgserver
import psycopg
from psycopg import sql


def capture(home: pathlib.Path, dump: pathlib.Path, objects_archive: pathlib.Path) -> None:
    server = pgserver.get_server(str(home / "pgdata"))
    binaries = pathlib.Path(pgserver.__file__).parent / "pginstall" / "bin"

    with psycopg.connect(server.get_uri()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' ORDER BY tablename"
            )
            tables = [row[0] for row in cur.fetchall()]
            if tables:
                statement = sql.SQL("LOCK TABLE {} IN SHARE MODE").format(
                    sql.SQL(", ").join(
                        sql.Identifier("public", name) for name in tables
                    )
                )
                cur.execute(statement)

            result = subprocess.run(
                [
                    str(binaries / "pg_dump"),
                    "-Fc",
                    "-f",
                    str(dump),
                    "--exclude-table-data=installation_secrets",
                    server.get_uri(),
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                detail = result.stderr.strip()[:500]
                raise RuntimeError(
                    "PostgreSQL dump failed"
                    + (f": {detail}" if detail else "")
                )

            objects = home / "objects"
            if objects.exists():
                for entry in objects.rglob("*"):
                    if entry.is_symlink() or (
                        not entry.is_file() and not entry.is_dir()
                    ):
                        raise RuntimeError(
                            f"unsupported object-store entry: {entry}"
                        )

            with tarfile.open(objects_archive, "w:gz") as archive:
                if objects.is_dir():
                    archive.add(objects, arcname="objects", recursive=True)

        # The transaction exits only after both files are complete, releasing
        # the SHARE locks after the DB and object snapshots describe one state.


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("home", type=pathlib.Path)
    parser.add_argument("dump", type=pathlib.Path)
    parser.add_argument("objects", type=pathlib.Path)
    args = parser.parse_args()
    try:
        capture(args.home, args.dump, args.objects)
    except Exception as exc:  # CLI boundary: one actionable line for backup.sh
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
