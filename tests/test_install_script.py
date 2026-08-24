"""The front door, which runs on a machine that has nothing yet.

`install.sh` is the one script here that executes with **no file on disk** — it
arrives through `curl | sh`, so it cannot look at its own location, cannot
source a sibling, and cannot assume bash. Most of what follows is therefore
about what it must *not* contain.

The rest is the drift guard. This repository has twice shipped a defect caused
by the same list existing in two places — `bootstrap.sh` installing four of nine
packages, and the Dockerfile carrying a third copy. The pinned interpreter
version and its checksums are exactly that shape of fact, so the installer must
not carry a second copy of them; it finds any Python and delegates.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALL = ROOT / "scripts" / "install.sh"


def _text() -> str:
    return INSTALL.read_text()


def test_it_is_executable():
    assert os.access(INSTALL, os.X_OK), "curl | sh does not care, but a clone does"


def test_it_is_posix_sh_not_bash():
    """`sh` on Debian is dash. A bashism here fails on the machines least able
    to diagnose it, and the shebang is what a reader checks first."""
    assert _text().splitlines()[0] == "#!/bin/sh"


def test_dash_accepts_it():
    """Parsed by a real POSIX shell rather than eyeballed for bashisms."""
    shell = "/bin/dash" if pathlib.Path("/bin/dash").exists() else "/bin/sh"
    result = subprocess.run([shell, "-n", str(INSTALL)], capture_output=True,
                            text=True)
    assert result.returncode == 0, result.stderr


def test_it_never_looks_for_its_own_location():
    """It has none. `$0` is `sh`, and BASH_SOURCE does not exist — code that
    tries to anchor itself to a directory silently anchors to the wrong one."""
    text = _text()
    assert "BASH_SOURCE" not in text
    assert "dirname \"$0\"" not in text


def test_it_does_not_carry_a_second_copy_of_the_pins():
    """The drift guard, and the reason this script delegates rather than fetches.

    A checksum or a pinned patch version in here would have to be updated in
    lockstep with `runtimes.py`, and the copy nobody remembers is the one that
    installs the wrong interpreter — silently, because it would still be *a*
    Python.
    """
    text = _text()
    assert not re.search(r"\b[0-9a-f]{64}\b", text), "a checksum is pinned here"
    assert "python-build-standalone" not in text
    assert not re.search(r"3\.12\.\d+", text), "a patch version is pinned here"


def test_it_hands_over_to_manage_py():
    """One implementation of the install sequence, in the language that can
    express it — the same reason bootstrap.sh is a wrapper."""
    assert "manage.py" in _text()


def _run(tmp_path, env_extra: dict[str, str], path: str | None = None):
    env = {"HOME": str(tmp_path), "PATH": path if path is not None
           else os.environ.get("PATH", "")}
    env.update(env_extra)
    return subprocess.run(["/bin/sh", str(INSTALL)], capture_output=True,
                          text=True, env=env, timeout=60)


def test_no_python_at_all_is_reported_not_crashed(tmp_path):
    """The chicken-and-egg case, stated plainly: the thing that fetches Python
    is itself Python. It must name the fix rather than fail on a missing binary.
    """
    result = _run(tmp_path, {}, path=str(tmp_path / "empty"))
    assert result.returncode != 0
    assert "No Python 3.8+" in result.stderr
    # Names the command that fixes it, per this repo's standard for failures.
    assert "apt install python3" in result.stderr


def test_it_refuses_to_write_into_something_that_is_not_a_checkout(tmp_path):
    """Refusing beats merging into a directory somebody else owns."""
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "someone-elses-file").write_text("x")

    result = _run(tmp_path, {"THROUGHLINE_INSTALL_DIR": str(occupied)})

    assert result.returncode != 0
    assert "not a git checkout" in result.stderr
    # Nothing touched.
    assert (occupied / "someone-elses-file").read_text() == "x"
    assert not (occupied / ".git").exists()


def test_the_destination_is_overridable(tmp_path):
    """A researcher who does not want it in $HOME must not have to edit a
    script they are piping from the internet."""
    assert "THROUGHLINE_INSTALL_DIR" in _text()


@pytest.mark.parametrize("variable", ["THROUGHLINE_REPO", "THROUGHLINE_BRANCH"])
def test_the_source_is_overridable(variable):
    """So a fork, or a release tag, can be installed without a second script —
    T073 points this at tags once the build period ends."""
    assert variable in _text()
