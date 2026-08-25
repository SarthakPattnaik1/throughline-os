"""The doors a researcher double-clicks.

Three platforms, and only one of them can be run here. So the tests split in
two: what can be *driven* (the Linux and macOS scripts are both bash, and both
can be made to take their no-Python branch on this machine) and what can only be
*asserted about the text* — the batch file, which no amount of care on WSL can
execute.

That division is deliberate and is the honest limit of this file. A `.bat` is
verified by `gh workflow run ci.yml` on a Windows runner or by a person with
Windows, and until one of those happens the batch file is unverified no matter
how many assertions sit here. What these checks *can* catch is the class of
defect that has bitten this repository before: a file that is subtly the wrong
shape — wrong line endings, a character the console cannot render, a working
directory resolved with a subshell that cannot fork.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LAUNCHERS = ROOT / "launchers"

sys.path.insert(0, str(ROOT / "scripts"))
import manage  # noqa: E402

SHELL_LAUNCHERS = ["Throughline.command", "throughline.sh"]


def test_there_is_a_door_for_each_platform():
    for name in ["Throughline.command", "Throughline.bat", "throughline.sh"]:
        assert (LAUNCHERS / name).exists(), name


@pytest.mark.parametrize("name", SHELL_LAUNCHERS)
def test_the_shell_launchers_are_executable(name):
    """A launcher without the bit is a text file that opens in an editor."""
    assert os.access(LAUNCHERS / name, os.X_OK)


@pytest.mark.parametrize("name", SHELL_LAUNCHERS)
def test_the_shell_launchers_resolve_without_a_subshell(name):
    """The same rule dev.sh and serve.sh follow, and for a sharper reason here.

    A launcher is precisely the thing that hands a script a working directory
    that no longer exists, and bash cannot fork from one — so `$(...)` dies
    before the first real command, with an error about getcwd.
    """
    text = (LAUNCHERS / name).read_text()
    cd_lines = [l for l in text.splitlines()
                if l.strip().startswith("cd ") and "BASH_SOURCE" in l]
    assert cd_lines, f"{name} does not anchor itself to its own location"
    assert not any("$(" in l for l in cd_lines), f"{name} forks to find the repo"


@pytest.mark.parametrize("name", SHELL_LAUNCHERS)
def test_a_launcher_does_not_exit_on_the_first_error(name):
    """`set -e` would close the window on the error the person needed to read."""
    text = (LAUNCHERS / name).read_text()
    assert "set -euo" not in text
    assert "\nset -e\n" not in text


@pytest.mark.parametrize("name", SHELL_LAUNCHERS)
def test_a_failure_pauses_so_the_message_can_be_read(name):
    text = (LAUNCHERS / name).read_text()
    assert "read -r -p" in text


def test_the_batch_file_keeps_crlf():
    """cmd.exe mis-parses LF-only batch files, and the failure is a wrong branch
    rather than a clean error — the worst kind to debug remotely."""
    raw = (LAUNCHERS / "Throughline.bat").read_bytes()
    assert b"\r\n" in raw
    assert raw.count(b"\n") == raw.count(b"\r\n"), "a bare LF slipped in"


def test_the_batch_file_is_ascii_only():
    """The console runs in an OEM codepage, not UTF-8. A stray em dash in a
    comment is harmless; one in a message is mojibake in front of the user."""
    raw = (LAUNCHERS / "Throughline.bat").read_bytes()
    raw.decode("ascii")  # raises if anything is not


def test_git_is_told_to_preserve_those_endings():
    """Without this, a clone on another platform silently rewrites them and the
    guarantee above lasts exactly until somebody else checks the repo out."""
    attributes = (ROOT / ".gitattributes").read_text()
    assert "*.bat text eol=crlf" in attributes
    assert "*.sh text eol=lf" in attributes
    assert "*.command text eol=lf" in attributes


def test_the_batch_file_prefers_the_py_launcher():
    """`python` on a machine with no Python is the Microsoft Store stub, which
    prints an advert and exits 9009. Trying `py` first is what stops the stub
    being mistaken for an interpreter."""
    text = (LAUNCHERS / "Throughline.bat").read_text()
    assert text.index("py -3") < text.index("python -c")


@pytest.mark.parametrize("name", ["Throughline.command", "throughline.sh",
                                  "Throughline.bat"])
def test_every_door_calls_the_same_command(name):
    """One sequence, not three. A launcher that inlined its own steps would be a
    fourth copy of the install order to drift out of step."""
    text = (LAUNCHERS / name).read_text()
    assert "manage.py start" in text.replace("\\", "/")


@pytest.mark.parametrize("name", SHELL_LAUNCHERS)
def test_no_python_at_all_is_reported_rather_than_crashed(tmp_path, name):
    """Driven for real, on the one branch this machine can reach without
    starting the whole stack: an empty PATH means no interpreter is found."""
    result = subprocess.run(
        ["/bin/bash", str(LAUNCHERS / name)],
        capture_output=True, text=True, timeout=60,
        stdin=subprocess.DEVNULL,
        env={"HOME": str(tmp_path), "PATH": str(tmp_path / "empty")})
    assert result.returncode == 1
    assert "Python 3.8 or newer" in result.stdout
    # Names the command that fixes it, per this repo's standard for failures.
    assert "install" in result.stdout


# --- the command they all call ---------------------------------------------


def test_start_installs_first_when_there_is_no_virtualenv(monkeypatch):
    monkeypatch.setattr(manage, "_venv_has_pip", lambda *a, **k: False)
    monkeypatch.setattr(manage, "_venv_version", lambda *a, **k: None)
    order = []
    monkeypatch.setattr(manage, "bootstrap", lambda: order.append("bootstrap") or 0)
    monkeypatch.setattr(manage, "dev", lambda *a: order.append("dev") or 0)
    assert manage.start(8080, 3000) == 0
    assert order == ["bootstrap", "dev"]


def test_start_does_not_reinstall_a_working_one(monkeypatch):
    """Every launch calls this, so the common path must cost nothing."""
    monkeypatch.setattr(manage, "_venv_has_pip", lambda *a, **k: True)
    monkeypatch.setattr(manage, "_venv_version", lambda *a, **k: manage.REQUIRED_PYTHON)
    order = []
    monkeypatch.setattr(manage, "bootstrap",
                        lambda: order.append("bootstrap") or 0)
    monkeypatch.setattr(manage, "dev", lambda *a: order.append("dev") or 0)
    assert manage.start(8080, 3000) == 0
    assert order == ["dev"]


def test_start_rebuilds_a_virtualenv_from_the_wrong_interpreter(monkeypatch):
    """Newly possible now that the bootstrap supplies its own Python — and the
    symptom if it is reused is an import error naming a C symbol."""
    monkeypatch.setattr(manage, "_venv_has_pip", lambda *a, **k: True)
    monkeypatch.setattr(manage, "_venv_version", lambda *a, **k: (3, 11))
    order = []
    monkeypatch.setattr(manage, "bootstrap", lambda: order.append("bootstrap") or 0)
    monkeypatch.setattr(manage, "dev", lambda *a: order.append("dev") or 0)
    assert manage.start(8080, 3000) == 0
    assert order == ["bootstrap", "dev"]


def test_start_does_not_launch_after_a_failed_install(monkeypatch, capsys):
    """Starting anyway would replace a setup error with a confusing runtime one."""
    monkeypatch.setattr(manage, "_venv_has_pip", lambda *a, **k: False)
    monkeypatch.setattr(manage, "_venv_version", lambda *a, **k: None)
    monkeypatch.setattr(manage, "bootstrap", lambda: 1)

    def refuse(*_a):
        raise AssertionError("started the stack after setup failed")

    monkeypatch.setattr(manage, "dev", refuse)
    assert manage.start(8080, 3000) == 1
    assert "nothing to start" in capsys.readouterr().err


# --- the Linux menu entry ---------------------------------------------------


def test_the_desktop_entry_keeps_the_terminal_visible(monkeypatch, tmp_path):
    """The whole reason it exists. A first run installs several hundred
    megabytes; behind a hidden window that is indistinguishable from a freeze."""
    monkeypatch.setattr(manage.sys, "platform", "linux")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))

    assert manage.desktop_entry() == 0
    written = tmp_path / ".local/share/applications/throughline.desktop"
    body = written.read_text()
    assert "Terminal=true" in body
    assert body.startswith("[Desktop Entry]")
    assert "Type=Application" in body


def test_the_desktop_entry_points_at_an_absolute_path(monkeypatch, tmp_path):
    """A menu entry is launched from nowhere in particular; a relative Exec
    silently resolves against whatever the session's cwd happens to be."""
    monkeypatch.setattr(manage.sys, "platform", "linux")
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    manage.desktop_entry()
    body = (tmp_path / ".local/share/applications/throughline.desktop").read_text()
    exec_line = [l for l in body.splitlines() if l.startswith("Exec=")][0]
    target = pathlib.Path(exec_line[len("Exec="):].strip('"'))
    assert target.is_absolute()
    assert target.exists()


def test_the_desktop_entry_is_refused_off_linux(monkeypatch, capsys):
    """And names the door that platform actually has."""
    monkeypatch.setattr(manage.sys, "platform", "darwin")
    assert manage.desktop_entry() == 1
    assert "Throughline.command" in capsys.readouterr().err


def test_the_desktop_entry_survives_a_path_with_a_space(monkeypatch, tmp_path):
    """An unquoted Exec is split on spaces, so a clone under "~/My Research/"
    becomes two arguments and the menu entry launches nothing — silently, which
    is the part that makes it expensive to diagnose."""
    monkeypatch.setattr(manage.sys, "platform", "linux")
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    manage.desktop_entry()
    body = (tmp_path / ".local/share/applications/throughline.desktop").read_text()
    exec_line = [l for l in body.splitlines() if l.startswith("Exec=")][0]
    assert exec_line.startswith('Exec="') and exec_line.endswith('"'), exec_line
