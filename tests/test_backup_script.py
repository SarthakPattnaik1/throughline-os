"""The safety net, which had no test and did not work.

`backup.sh` and `restore.sh` are what make an update reversible, and T073 leans
on them entirely: an update backs up before it touches anything, and puts the
code back if it fails. Nothing exercised either script, and both carried the
same two bugs.

They pointed `pg_dump`/`pg_restore` at the **data directory** — `-h pgdata` —
but `pgserver` puts its unix socket in a per-user runtime directory, so that
host has never been right. And they only ever worked against an
already-running server, while an update backs up *before* restarting. A backup
that works only when the app happens to be up makes the guarantee conditional on
the exact thing an update is about to change.

Both were invisible to every other test in this suite, because everything else
talks to the database through `db.py`, which asks `pgserver` where the server is
instead of guessing. Only the shell scripts guessed.

**This test runs the real script against a real database.** It is slow — it
boots PostgreSQL — and that is the cost of covering the one path whose whole
job is to work on the worst day somebody has.
"""

from __future__ import annotations

import os
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BACKUP = ROOT / "scripts" / "backup.sh"

#: `pg_dump -Fc` writes a custom-format archive, which begins with this.
#: Checked rather than "the file is non-empty", because the failure being
#: guarded against produced a *plausible* empty file for a while.
PGDMP_MAGIC = b"PGDMP"


@pytest.fixture()
def installation(tmp_path):
    """A throwaway THROUGHLINE_HOME with a real database in it.

    Built through `db.py`, so the schema exists and the server is left in
    whatever state that module leaves it — which is the state a real
    installation is in when somebody runs an update.
    """
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ, THROUGHLINE_HOME=str(home))

    create = subprocess.run(
        [str(ROOT / ".venv" / "bin" / "python"), "-c",
         "from throughline_domain.migrate import migrate; migrate()"],
        env=env, capture_output=True, text=True, cwd=str(ROOT))
    if create.returncode != 0:
        pytest.skip(f"could not create a scratch database: {create.stderr[-300:]}")
    return home


def _run_backup(home: Path, destination: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(BACKUP), str(destination)],
        env=dict(os.environ, THROUGHLINE_HOME=str(home)),
        capture_output=True, text=True, cwd=str(ROOT))


def test_a_backup_contains_a_real_database_dump(installation, tmp_path):
    """The whole point. An archive that restores nothing is worse than none,
    because it is trusted."""
    destination = tmp_path / "backups"
    result = _run_backup(installation, destination)
    assert result.returncode == 0, result.stderr[-600:]

    archives = list(destination.glob("throughline-*.tar"))
    assert len(archives) == 1, [p.name for p in archives]

    with tarfile.open(archives[0]) as bundle:
        names = bundle.getnames()
        assert "database.dump" in names, names
        dump = bundle.extractfile("database.dump")
        assert dump is not None
        head = dump.read(5)

    assert head == PGDMP_MAGIC, (
        "the dump is not a PostgreSQL custom-format archive; pg_dump was "
        "pointed somewhere wrong and the failure was not noticed")


def test_the_objects_travel_with_the_database(installation, tmp_path):
    """Either without the other is useless: a database restored without its
    objects has findings citing files that cannot be opened."""
    destination = tmp_path / "backups"
    assert _run_backup(installation, destination).returncode == 0

    archive = next(destination.glob("throughline-*.tar"))
    with tarfile.open(archive) as bundle:
        assert "objects.tar.gz" in bundle.getnames()
        assert "manifest.txt" in bundle.getnames()


def test_a_stopped_installation_can_still_be_backed_up(installation, tmp_path):
    """The regression that made T073's guarantee conditional.

    An update backs up *before* it restarts, so if a backup needs a running
    server then an update on a stopped installation can never take one — and
    the update correctly refuses, which reads as the updater being broken
    rather than the backup being.

    `pgserver` ties a server's lifetime to the process that started it, so the
    fixture's server is already gone by the time this runs: this is the stopped
    case by construction rather than by simulation.
    """
    destination = tmp_path / "backups"
    result = _run_backup(installation, destination)

    assert result.returncode == 0, (
        "backing up a stopped installation failed, which is exactly when an "
        f"update needs it: {result.stderr[-600:]}")
    archive = next(destination.glob("throughline-*.tar"))
    with tarfile.open(archive) as bundle:
        assert bundle.extractfile("database.dump").read(5) == PGDMP_MAGIC


def test_it_refuses_when_there_is_nothing_to_back_up(tmp_path):
    """Naming the variable that would fix it, rather than writing an empty
    archive somebody would later trust."""
    result = subprocess.run(
        [str(BACKUP), str(tmp_path / "backups")],
        env=dict(os.environ, THROUGHLINE_HOME=str(tmp_path / "nothing-here")),
        capture_output=True, text=True, cwd=str(ROOT))

    assert result.returncode != 0
    assert "THROUGHLINE_HOME" in result.stderr



def test_database_and_objects_are_captured_under_one_write_freeze():
    """The DB dump and object archive must describe one committed state.

    A normal backup/restore test cannot reliably reproduce the tiny window where
    a project delete commits after pg_dump but before object capture. Pin the
    structural invariant as well: the SHARE locks are acquired before either
    capture operation, and both happen inside the same Python/transaction block.
    """
    source = BACKUP.read_text()

    lock = source.index("LOCK TABLE {} IN SHARE MODE")
    dump = source.index("'pg_dump'", lock)
    objects = source.index("tarfile.open(objects_archive", dump)
    release = source.index("releases all SHARE locks", objects)

    assert lock < dump < objects < release
    assert "database + objects (one consistent snapshot)" in source
