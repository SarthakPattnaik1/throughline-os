#!/usr/bin/env bash
# Back up a Throughline installation (§99, §100).
#
# Two things must travel together or neither is useful: the database, which holds
# every finding, citation and lineage edge, and the object store, which holds the
# bytes those rows are hashes of. A database restored without its objects has
# findings citing files that cannot be opened; objects without the database are
# an unindexed pile. So this writes one archive containing both.
#
#   ./scripts/backup.sh [destination-directory]
set -euo pipefail

# Backups contain private research. Do not let the caller's permissive umask turn
# the archive into a file other local accounts can read.
umask 077

HOME_DIR="${THROUGHLINE_HOME:-$HOME/.throughline-os}"
DEST="${1:-$HOME/throughline-backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [ ! -d "$HOME_DIR" ]; then
  echo "No installation at $HOME_DIR. Set THROUGHLINE_HOME if it lives elsewhere." >&2
  exit 1
fi

mkdir -p "$DEST"
echo "Backing up $HOME_DIR"

# The database is dumped rather than copied: a file-level copy of a running
# PostgreSQL data directory is not a consistent snapshot, and the failure only
# shows up when you try to restore it.
#
# `installation_secrets` is deliberately schema-only in a backup. A saved hosted
# model credential is machine configuration, not research, and backup archives
# are precisely the files researchers copy to external disks or cloud storage.
# Restoring the workspace should therefore require re-entering that credential
# instead of reviving a usable API key from an archive.
#
# The venv's interpreter, not the system one: pgserver ships the PostgreSQL
# binaries and only the venv can import it. Using `python3` here failed on the
# first run, which is the argument for testing a backup script before trusting it.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY_BIN="${REPO}/.venv/bin/python"
[ -x "$PY_BIN" ] || PY_BIN="$(command -v python3)"
PGBIN="$("$PY_BIN" -c 'import pathlib,pgserver;print(pathlib.Path(pgserver.__file__).parent/"pginstall"/"bin")')"

# Start the embedded server if it is not already up.
#
# `pg_dump` connects over a socket inside pgdata, so a stopped installation
# cannot be dumped at all. That was survivable while backups were something a
# person ran on a running system — and stopped being survivable when `update`
# started requiring one, because an update backs up *before* it restarts. A
# backup that only works while the app happens to be running would make T073's
# central guarantee quietly conditional. Found by actually running an update
# against a stopped installation, which is the only way this shows up.
#
# `get_server` is the same call `db.py` makes, and it is idempotent: on a
# running server it connects and returns, on a stopped one it starts it.
# The dump happens inside one Python process that holds the server open.
#
# `pgserver` ties the server's lifetime to the process that started it —
# `restore.sh` documents this and was built around it. So starting the server in
# one step and running pg_dump in the next leaves nothing listening by the time
# pg_dump connects, which is precisely what happened here: the server came up,
# the process exited, and the dump failed against a socket that had just been
# removed.
#
# Two things were wrong before, and both were invisible while the app happened
# to be running. `-h "$HOME_DIR/pgdata"` pointed at the data directory, but
# pgserver puts its socket in a per-user runtime directory, so that host has
# never been right. And connecting only ever worked against an already-running
# server, while `update` backs up *before* restarting — so a stopped
# installation could not be backed up at all, which made T073's central
# guarantee conditional on the very thing an update is about to change.
#
# Found by running an update against a stopped installation.
echo "  database + objects (one consistent snapshot)…"
THROUGHLINE_TARGET="$HOME_DIR" DUMP="$WORK/database.dump" OBJECTS="$WORK/objects.tar.gz" "$PY_BIN" -c "
import os, pathlib, subprocess, sys, tarfile
import pgserver
import psycopg
from psycopg import sql

home = pathlib.Path(os.environ['THROUGHLINE_TARGET'])
dump = pathlib.Path(os.environ['DUMP'])
objects_archive = pathlib.Path(os.environ['OBJECTS'])
server = pgserver.get_server(str(home / 'pgdata'))
binaries = pathlib.Path(pgserver.__file__).parent / 'pginstall' / 'bin'

# Freeze every application table against INSERT/UPDATE/DELETE while both halves
# are captured. ACCESS SHARE (used by pg_dump) remains compatible. A writer that
# has already begun finishes first; later writers wait until this transaction
# releases the locks.
with psycopg.connect(server.get_uri()) as conn:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "ORDER BY tablename")
        tables = [row[0] for row in cur.fetchall()]
        if tables:
            statement = sql.SQL('LOCK TABLE {} IN SHARE MODE').format(
                sql.SQL(', ').join(sql.Identifier('public', name) for name in tables))
            cur.execute(statement)

        result = subprocess.run(
            [str(binaries / 'pg_dump'), '-Fc', '-f', str(dump),
             '--exclude-table-data=installation_secrets', server.get_uri()],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(result.stderr.strip()[:500], file=sys.stderr)
            sys.exit(1)

        objects = home / 'objects'
        if objects.exists():
            # A backup that contains a symlink/device would later be refused by
            # restore's safe extractor. Refuse it here instead of publishing a
            # backup that cannot be restored.
            for entry in objects.rglob('*'):
                if entry.is_symlink() or (not entry.is_file() and not entry.is_dir()):
                    print(f'unsupported object-store entry: {entry}', file=sys.stderr)
                    sys.exit(1)

        with tarfile.open(objects_archive, 'w:gz') as archive:
            if objects.is_dir():
                archive.add(objects, arcname='objects', recursive=True)

# Leaving the transaction releases all SHARE locks only after database.dump and
# objects.tar.gz have both been completed.
" || { echo "  the database/object snapshot could not be captured" >&2; exit 1; }

# A manifest, so a restore can tell whether the archive is intact before it
# starts overwriting anything.
cat > "$WORK/manifest.txt" <<EOF
throughline-backup
created: $(date -u +%Y-%m-%dT%H:%M:%SZ)
source: $HOME_DIR
database_bytes: $(wc -c < "$WORK/database.dump" | tr -d ' ')
objects_bytes: $(wc -c < "$WORK/objects.tar.gz" | tr -d ' ')
database_sha256: $("$PY_BIN" -c 'import hashlib,sys;f=open(sys.argv[1],"rb");print(hashlib.file_digest(f,"sha256").hexdigest())' "$WORK/database.dump")
objects_sha256: $("$PY_BIN" -c 'import hashlib,sys;f=open(sys.argv[1],"rb");print(hashlib.file_digest(f,"sha256").hexdigest())' "$WORK/objects.tar.gz")
EOF

ARCHIVE="$DEST/throughline-$STAMP.tar"
tar -cf "$ARCHIVE" -C "$WORK" manifest.txt database.dump objects.tar.gz
# Belt-and-suspenders with the restrictive umask above: a future refactor that
# creates the archive through another tool should still leave the final file
# private to its owner.
chmod 600 "$ARCHIVE"
echo "Wrote $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"
echo
echo "Restore with: ./scripts/restore.sh $ARCHIVE"
