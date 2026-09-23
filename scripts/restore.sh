#!/usr/bin/env bash
# Restore a Throughline backup without touching live research until the entire
# backup has been validated and restored successfully in staging.
set -euo pipefail

ARCHIVE="${1:?usage: restore.sh <archive.tar> [--force]}"
FORCE="${2:-}"
HOME_DIR="${THROUGHLINE_HOME:-$HOME/.throughline-os}"
WORK="$(mktemp -d)"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY_BIN="${REPO}/.venv/bin/python"
[ -x "$PY_BIN" ] || PY_BIN="$(command -v python3)"

PARENT="$(dirname "$HOME_DIR")"
mkdir -p "$PARENT"
STAGE_HOME="$(mktemp -d "$PARENT/.throughline-restore-stage.XXXXXX")"

cleanup() {
  rm -rf "$WORK"
  [ ! -d "$STAGE_HOME" ] || rm -rf "$STAGE_HOME"
}
trap cleanup EXIT

# Validate the outer archive before trusting tar member names or touching the
# installation. Only three regular files are accepted; links/devices/traversal
# and duplicate members are refused.
mkdir -p "$WORK/extracted-parent"
"$PY_BIN" "$REPO/scripts/backup_archive.py" outer "$ARCHIVE" "$WORK/extracted-parent/backup"
EXTRACTED="$WORK/extracted-parent/backup"

cat "$EXTRACTED/manifest.txt"
echo

if [ -d "$HOME_DIR/pgdata" ] && [ "$FORCE" != "--force" ]; then
  echo "$HOME_DIR already contains an installation." >&2
  echo "Restoring would overwrite it. Re-run with --force if that is what you want." >&2
  exit 1
fi

# Never move a running PostgreSQL data directory out from under the process.
# A stale postmaster.pid is harmless: only a live pid blocks the restore.
if [ -f "$HOME_DIR/pgdata/postmaster.pid" ]; then
  PID="$(head -n 1 "$HOME_DIR/pgdata/postmaster.pid" 2>/dev/null || true)"
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    echo "Throughline's database is still running (pid $PID)." >&2
    echo "Stop Throughline before restoring a backup." >&2
    exit 1
  fi
fi

# Validate and extract the nested object archive into staging only. It may
# contain regular files/directories below objects/ and nothing else.
"$PY_BIN" "$REPO/scripts/backup_archive.py" objects   "$EXTRACTED/objects.tar.gz" "$STAGE_HOME"

# Restore the database into staging too. A malformed/incompatible dump therefore
# fails before either live pgdata or live objects is changed.
echo "Restoring database into staging…"
THROUGHLINE_TARGET="$STAGE_HOME" DUMP="$EXTRACTED/database.dump" "$PY_BIN" -c "
import os, pathlib, subprocess, sys
import pgserver

home = pathlib.Path(os.environ['THROUGHLINE_TARGET'])
pgdata = home / 'pgdata'
pgdata.mkdir(parents=True, exist_ok=True)
server = pgserver.get_server(str(pgdata))
binaries = pathlib.Path(pgserver.__file__).parent / 'pginstall' / 'bin'
result = subprocess.run(
    [str(binaries / 'pg_restore'), '--clean', '--if-exists', '--no-owner',
     '-d', server.get_uri(), os.environ['DUMP']],
    capture_output=True, text=True,
)
noise = ('does not exist', 'already exists')
real = [line for line in result.stderr.splitlines()
        if line.strip() and not any(n in line for n in noise)]
if result.returncode != 0 and real:
    print('\n'.join(real[:10]), file=sys.stderr)
    sys.exit(1)
print('  staged database restored')
" || { echo "Restore validation failed. The live installation was not changed." >&2; exit 1; }

mkdir -p "$HOME_DIR"
OLD_SUFFIX=".pre-restore.$$"
OLD_PG="$HOME_DIR/pgdata$OLD_SUFFIX"
OLD_OBJECTS="$HOME_DIR/objects$OLD_SUFFIX"
HAD_PG=0
HAD_OBJECTS=0

rollback() {
  rm -rf "$HOME_DIR/pgdata" "$HOME_DIR/objects"
  if [ "$HAD_PG" -eq 1 ] && [ -e "$OLD_PG" ]; then mv "$OLD_PG" "$HOME_DIR/pgdata"; fi
  if [ "$HAD_OBJECTS" -eq 1 ] && [ -e "$OLD_OBJECTS" ]; then mv "$OLD_OBJECTS" "$HOME_DIR/objects"; fi
}

if [ -e "$HOME_DIR/pgdata" ]; then
  mv "$HOME_DIR/pgdata" "$OLD_PG"
  HAD_PG=1
fi
if [ -e "$HOME_DIR/objects" ]; then
  mv "$HOME_DIR/objects" "$OLD_OBJECTS"
  HAD_OBJECTS=1
fi

if ! mv "$STAGE_HOME/pgdata" "$HOME_DIR/pgdata"; then
  rollback
  echo "Could not install the restored database; the previous installation was put back." >&2
  exit 1
fi
if ! mv "$STAGE_HOME/objects" "$HOME_DIR/objects"; then
  rollback
  echo "Could not install the restored object store; the previous installation was put back." >&2
  exit 1
fi

rm -rf "$OLD_PG" "$OLD_OBJECTS"
echo "Done. Start with ./scripts/dev.sh"
