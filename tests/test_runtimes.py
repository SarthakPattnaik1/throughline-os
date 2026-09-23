"""The bootstrap fetches two executables and then runs them.

That is the most dangerous thing this codebase does, so the tests that matter
here are not "does it download" — they are the refusals. Every one of these
drives a case where `runtimes.py` must decline: wrong bytes, an archive that
writes outside its destination, a half-unpacked directory that a laxer check
would call installed.

**Nothing here touches the network.** Downloads are driven through `file://`
URLs, which `urllib` serves from disk, so the verification path is exercised
in full — the same `urlopen`, the same streaming hash, the same rename — with
no dependency on a release still existing. A test that reaches GitHub to prove
a checksum is a test that fails on a train.
"""

from __future__ import annotations

import hashlib
import io
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import manage  # noqa: E402
import runtimes  # noqa: E402


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tar(tmp_path: Path, members: dict[str, bytes], name: str = "a.tar.gz") -> Path:
    """A .tar.gz built in memory, so a test can post an arbitrary member name."""
    path = tmp_path / name
    with tarfile.open(path, "w:gz") as bundle:
        for member_name, payload in members.items():
            info = tarfile.TarInfo(member_name)
            info.size = len(payload)
            bundle.addfile(info, io.BytesIO(payload))
    return path


# --- the pinned table ------------------------------------------------------


def test_the_pinned_python_is_the_version_the_rest_of_the_repo_requires():
    """The drift that would be silent, and the reason this file pins a patch.

    `manage.REQUIRED_PYTHON` is the constraint pgserver imposes; `runtimes`
    names an actual build. Bumping one and not the other gives a bootstrap that
    installs an interpreter its own next step then refuses, and the error would
    name the interpreter rather than the mismatch.
    """
    expected = tuple(int(part) for part in runtimes.CPYTHON_VERSION.split("."))
    assert expected == manage.REQUIRED_PYTHON


def test_every_pin_carries_a_real_digest():
    """A placeholder digest is worse than none: it looks verified."""
    for kind, table in runtimes.RUNTIMES.items():
        for target, (url, digest, _) in table.items():
            assert len(digest) == 64, (kind, target, digest)
            assert set(digest) <= set("0123456789abcdef"), (kind, target)
            assert url.startswith("https://"), (kind, target, url)


def test_both_runtimes_cover_the_same_machines():
    """Shipping Python to a machine that then cannot run the interface is not
    an install. If one table gains a platform, the other has to gain it too."""
    assert set(runtimes.PYTHON) == set(runtimes.NODE)


def test_the_pinned_version_appears_in_the_url_it_names():
    """Stops a digest being updated while the URL still points at the old build."""
    for target, (url, _, _) in runtimes.PYTHON.items():
        assert runtimes.CPYTHON_VERSION in url, target
    for target, (url, _, _) in runtimes.NODE.items():
        assert runtimes.NODE_VERSION in url, target


# --- naming this machine ---------------------------------------------------


@pytest.mark.parametrize("system,machine,expected", [
    ("Linux", "x86_64", "linux-x86_64"),
    ("Linux", "aarch64", "linux-aarch64"),
    # The three names one architecture answers to, depending on who is asking.
    ("Darwin", "arm64", "macos-aarch64"),
    ("Windows", "AMD64", "windows-x86_64"),
    ("Darwin", "x86_64", "macos-x86_64"),
])
def test_one_architecture_is_named_the_same_way_whoever_asks(
        monkeypatch, system, machine, expected):
    monkeypatch.setattr(runtimes.platform, "system", lambda: system)
    monkeypatch.setattr(runtimes.platform, "machine", lambda: machine)
    assert runtimes.host() == expected


def test_an_unsupported_platform_says_what_is_supported(monkeypatch):
    monkeypatch.setattr(runtimes.platform, "system", lambda: "FreeBSD")
    monkeypatch.setattr(runtimes.platform, "machine", lambda: "amd64")
    with pytest.raises(runtimes.UnsupportedPlatform) as raised:
        runtimes.host()
    assert "FreeBSD" in str(raised.value)
    assert "Linux" in str(raised.value)


def test_an_unsupported_architecture_is_refused_by_name(monkeypatch):
    """Linux on a machine with no pinned build: the platform is known, the
    architecture is not, and the message must not be a KeyError."""
    monkeypatch.setattr(runtimes.platform, "system", lambda: "Linux")
    monkeypatch.setattr(runtimes.platform, "machine", lambda: "riscv64")
    with pytest.raises(runtimes.UnsupportedPlatform) as raised:
        runtimes.executable("python", Path("/nowhere"))
    assert "linux-riscv64" in str(raised.value)
    assert "linux-x86_64" in str(raised.value)


# --- the checksum, which is the whole point --------------------------------


def test_a_verified_download_lands_at_its_destination(tmp_path):
    payload = b"the right bytes"
    source = tmp_path / "src.bin"
    source.write_bytes(payload)
    dest = tmp_path / "out" / "dest.bin"

    runtimes.download(source.as_uri(), _sha(payload), dest, log=lambda *_: None)

    assert dest.read_bytes() == payload


def test_wrong_bytes_are_refused_and_not_left_on_disk(tmp_path):
    """The failure that must not be recoverable by running it again.

    A rejected download kept "for inspection" is an unverified executable
    sitting at a path the next step may well accept.
    """
    source = tmp_path / "src.bin"
    source.write_bytes(b"substituted")
    dest = tmp_path / "dest.bin"

    with pytest.raises(runtimes.ChecksumMismatch) as raised:
        runtimes.download(source.as_uri(), _sha(b"expected"), dest,
                          log=lambda *_: None)

    assert not dest.exists()
    assert not dest.with_suffix(dest.suffix + ".part").exists()
    # Both digests in the message: the person reading it is comparing them.
    assert _sha(b"expected") in str(raised.value)
    assert _sha(b"substituted") in str(raised.value)


def test_a_failed_download_leaves_no_partial_file(tmp_path):
    dest = tmp_path / "dest.bin"
    missing = (tmp_path / "does-not-exist.bin").as_uri()

    with pytest.raises(runtimes.RuntimeError_):
        runtimes.download(missing, "0" * 64, dest, log=lambda *_: None)

    assert not dest.exists()
    assert list(tmp_path.glob("*.part")) == []


# --- archives, which name their own destinations ---------------------------


def test_an_archive_that_climbs_out_of_its_destination_is_refused(tmp_path):
    """`../` in a member name is the entire attack, and the unpacker is the
    only thing standing between an archive and an arbitrary write."""
    archive = _tar(tmp_path, {"python/bin/python3": b"ok",
                              "../../escaped": b"owned"})
    with pytest.raises(runtimes.RuntimeError_) as raised:
        runtimes.unpack(archive, tmp_path / "dest", "python")
    assert "escapes its destination" in str(raised.value)
    assert not (tmp_path.parent / "escaped").exists()


def test_an_absolute_member_name_is_refused(tmp_path):
    """Carries a valid top-level directory too, and asserts the *reason*.

    Without both, this passes on the layout check — "expected one top-level
    directory" — and would keep passing with the traversal guard deleted.
    """
    archive = _tar(tmp_path, {"python/bin/python3": b"ok",
                              "/etc/passwd": b"owned"})
    with pytest.raises(runtimes.RuntimeError_) as raised:
        runtimes.unpack(archive, tmp_path / "dest", "python")
    assert "escapes its destination" in str(raised.value)


def test_a_zip_that_climbs_out_is_refused_too(tmp_path):
    """Windows' Node is a .zip, and `zipfile` has no `filter='data'` to lean on
    — so the check has to be ours, and it has to cover both branches."""
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("node/node.exe", "ok")
        bundle.writestr("../escaped.exe", "owned")
    with pytest.raises(runtimes.RuntimeError_) as raised:
        runtimes.unpack(archive, tmp_path / "dest", "node")
    assert "escapes its destination" in str(raised.value)


def test_a_backslash_separator_does_not_slip_past_the_check(tmp_path):
    """A zip written on Windows may use backslashes, and a check that splits on
    '/' alone reads `..\\..\\x` as one harmless filename."""
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("node/node.exe", "ok")
        bundle.writestr("..\\..\\escaped.exe", "owned")
    with pytest.raises(runtimes.RuntimeError_) as raised:
        runtimes.unpack(archive, tmp_path / "dest", "node")
    assert "escapes its destination" in str(raised.value)


def test_the_single_top_level_directory_is_stripped(tmp_path):
    archive = _tar(tmp_path, {"python-3.12.14/bin/python3": b"#!/bin/sh\n"})
    dest = runtimes.unpack(archive, tmp_path / "dest", "python")
    # Not dest/python-3.12.14/bin/python3 — the version must not appear in the
    # path the tables describe, or a bump would have to edit two places.
    assert (dest / "bin" / "python3").read_bytes() == b"#!/bin/sh\n"


def test_an_archive_with_no_single_root_is_refused(tmp_path):
    """The layout assumption is stated and checked rather than assumed, because
    when a publisher changes it the failure otherwise appears much later."""
    archive = _tar(tmp_path, {"one/x": b"a", "two/y": b"b"})
    with pytest.raises(runtimes.RuntimeError_) as raised:
        runtimes.unpack(archive, tmp_path / "dest", "python")
    assert "one top-level directory" in str(raised.value)


def test_unpacking_leaves_no_staging_directory_behind(tmp_path):
    archive = _tar(tmp_path, {"python/bin/python3": b"x"})
    dest = tmp_path / "dest"
    runtimes.unpack(archive, dest, "python")
    assert list(tmp_path.glob("*.unpacking")) == []


# --- "is it installed?" is a file question, not a directory question -------


def test_a_directory_without_the_executable_is_not_installed(tmp_path):
    """`_venv_has_pip`'s lesson, one layer down: an interrupted unpack leaves a
    directory, and a check that accepts it reports success then fails at exec."""
    runtimes.install_dir("python", tmp_path).mkdir(parents=True)
    assert runtimes.installed("python", tmp_path) is False


def test_the_executable_makes_it_installed(tmp_path):
    binary = runtimes.executable("python", tmp_path)
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#!/bin/sh\n")
    assert runtimes.installed("python", tmp_path) is True


def test_ensure_does_not_reach_the_network_when_it_is_already_here(
        tmp_path, monkeypatch):
    """The common case is every launch, so it must cost one stat and no bytes."""
    binary = runtimes.executable("node", tmp_path)
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#!/bin/sh\n")

    def refuse(*_args, **_kwargs):
        raise AssertionError("ensure() downloaded something it already had")

    monkeypatch.setattr(runtimes, "download", refuse)
    assert runtimes.ensure("node", tmp_path, log=lambda *_: None) == binary


def test_installs_are_versioned_so_an_update_can_be_walked_back(tmp_path):
    """T073 requires the previous version still works after a failed update,
    which is only possible if two versions can coexist."""
    assert runtimes.CPYTHON_VERSION in str(runtimes.install_dir("python", tmp_path))
    assert runtimes.NODE_VERSION in str(runtimes.install_dir("node", tmp_path))


def test_an_unknown_runtime_is_refused_by_name(tmp_path):
    with pytest.raises(runtimes.RuntimeError_):
        runtimes.ensure("perl", tmp_path, log=lambda *_: None)


def test_the_runtime_directory_is_overridable(monkeypatch, tmp_path):
    """Follows `embeddings.model_dir()`: a fetched asset is machine-level, and
    the override is what lets a test — or a second checkout — isolate it."""
    monkeypatch.setenv("THROUGHLINE_RUNTIME_DIR", str(tmp_path / "elsewhere"))
    assert runtimes.runtime_root() == tmp_path / "elsewhere"


def test_the_runtime_directory_is_not_throughline_home(monkeypatch, tmp_path):
    """THROUGHLINE_HOME is per-instance state a test overrides to isolate its
    database. A runtime under it would be re-downloaded by every such run."""
    monkeypatch.delenv("THROUGHLINE_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("THROUGHLINE_HOME", str(tmp_path / "instance"))
    assert tmp_path / "instance" not in runtimes.runtime_root().parents


# --- what the bootstrap does with them -------------------------------------


def test_the_bootstrap_does_not_fetch_forever(monkeypatch):
    """The failure the re-exec sentinel exists to prevent.

    Fetching an interpreter and running the bootstrap under it is a loop by
    construction. If the fetched Python ever reports the wrong version, the
    honest answer is to stop; the alternative never returns, and a bootstrap
    that hangs is worse than the refusal this replaced.
    """
    monkeypatch.setattr(manage, "REQUIRED_PYTHON", (9, 9))
    monkeypatch.setenv(manage._REEXEC, "1")

    def refuse(*_args, **_kwargs):
        raise AssertionError("fetched a second interpreter after re-executing")

    monkeypatch.setattr(manage.runtimes, "ensure", refuse)
    assert manage.bootstrap() == 1


def test_a_wrong_version_fetches_and_re_executes(monkeypatch, tmp_path):
    """The behaviour that replaced the refusal: go and get the right one."""
    monkeypatch.setattr(manage, "REQUIRED_PYTHON", (9, 9))
    monkeypatch.delenv(manage._REEXEC, raising=False)
    fetched = tmp_path / "python9"
    monkeypatch.setattr(manage.runtimes, "ensure", lambda *_a, **_k: fetched)

    seen = {}

    class Result:
        returncode = 0

    def capture(command, **kwargs):
        seen["command"] = command
        seen["env"] = kwargs.get("env", {})
        return Result()

    monkeypatch.setattr(manage.subprocess, "run", capture)
    assert manage.bootstrap() == 0
    assert seen["command"][0] == str(fetched)
    assert seen["command"][2] == "bootstrap"
    # Without this the child would fetch and re-exec again.
    assert seen["env"][manage._REEXEC] == "1"


def test_a_failed_fetch_still_explains_the_version(monkeypatch, capsys):
    """Offline, the old explanation is still the useful thing to print — it is
    the only remaining way the person can fix it themselves."""
    monkeypatch.setattr(manage, "REQUIRED_PYTHON", (9, 9))
    monkeypatch.delenv(manage._REEXEC, raising=False)

    def fail(*_a, **_k):
        raise manage.runtimes.RuntimeError_("no network")

    monkeypatch.setattr(manage.runtimes, "ensure", fail)
    assert manage.bootstrap() == 1
    assert "pgserver" in capsys.readouterr().err


def test_node_is_chosen_by_version_not_by_position(monkeypatch, tmp_path):
    """A machine may carry an old node on PATH and the fetched one as well.
    Picking by position prefers the one that cannot start `next`."""
    old, new = str(tmp_path / "old"), str(tmp_path / "new")
    monkeypatch.setattr(manage, "_node_candidates", lambda: [old, new])
    monkeypatch.setattr(manage, "_node_major",
                        lambda binary: 18 if binary == old else 22)
    assert manage._node_on_path() == new


def test_a_node_that_is_only_too_old_is_still_reported(monkeypatch, tmp_path):
    """`doctor` saying "not found" about a node that is plainly installed sends
    the reader looking for the wrong problem."""
    old = str(tmp_path / "old")
    monkeypatch.setattr(manage, "_node_candidates", lambda: [old])
    monkeypatch.setattr(manage, "_node_major", lambda _binary: 18)
    assert manage._node_on_path() == old


def test_no_node_anywhere_is_none(monkeypatch):
    monkeypatch.setattr(manage, "_node_candidates", list)
    assert manage._node_on_path() is None


def test_an_already_built_interface_needs_no_node_at_all(monkeypatch, tmp_path):
    """The case a release should arrive in.

    The interface is now exported files rather than a Node process, so an
    install that already has them is a Python install — nothing should fetch a
    204 MB runtime to serve a folder.
    """
    monkeypatch.setattr(manage, "ROOT", tmp_path)
    built = tmp_path / "apps" / "web" / "out"
    built.mkdir(parents=True)
    (built / "index.html").write_text("<html></html>")

    def refuse(*_a, **_k):
        raise AssertionError("fetched Node for an interface that already exists")

    monkeypatch.setattr(manage.runtimes, "ensure", refuse)
    assert manage._ensure_interface(log=lambda *_: None) is True


def test_building_the_interface_can_be_declined(monkeypatch, tmp_path):
    monkeypatch.setattr(manage, "ROOT", tmp_path)
    monkeypatch.setenv("THROUGHLINE_SKIP_NODE", "1")

    def refuse(*_a, **_k):
        raise AssertionError("fetched Node despite THROUGHLINE_SKIP_NODE")

    monkeypatch.setattr(manage.runtimes, "ensure", refuse)
    assert manage._ensure_interface(log=lambda *_: None) is False


def test_a_failed_interface_build_does_not_fail_the_bootstrap(monkeypatch,
                                                              tmp_path):
    """It runs after the database migration. Raising here would throw away a
    completed migration over an interface the API reports the absence of anyway,
    with the command that fixes it.
    """
    monkeypatch.setattr(manage, "ROOT", tmp_path)
    monkeypatch.delenv("THROUGHLINE_SKIP_NODE", raising=False)
    monkeypatch.setattr(manage, "_node_on_path", lambda: None)

    def fail(*_a, **_k):
        raise manage.runtimes.RuntimeError_("no network")

    monkeypatch.setattr(manage.runtimes, "ensure", fail)
    assert manage._ensure_interface(log=lambda *_: None) is False


def test_the_managed_node_is_a_candidate(monkeypatch, tmp_path):
    """The whole point of fetching one is that discovery can then find it."""
    monkeypatch.setenv("THROUGHLINE_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(manage.shutil, "which", lambda _name: None)
    binary = runtimes.executable("node", tmp_path)
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#!/bin/sh\n")
    assert str(binary) in manage._node_candidates()


def test_npm_is_taken_from_beside_the_chosen_node(tmp_path):
    """The pick-by-position mistake, one level down.

    `dev` chooses node by version and then has to run npm. Resolving npm on
    PATH can hand back the one belonging to a *different* node — on a machine
    with an old node on PATH and a fetched one in the runtime directory, that is
    exactly what happens, and it surfaces as an npm error about an engine
    constraint rather than as a version mismatch.
    """
    node = tmp_path / "bin" / "node"
    node.parent.mkdir(parents=True)
    node.write_text("#!/bin/sh\n")
    npm = tmp_path / "bin" / ("npm.cmd" if manage.WINDOWS else "npm")
    npm.write_text("#!/bin/sh\n")

    assert manage.node_exe(str(node), "npm") == str(npm)


def test_a_missing_sibling_falls_back_to_the_bare_name(tmp_path):
    """An absolute path to a file that is not there fails less clearly than a
    name the child's PATH can still resolve."""
    node = tmp_path / "bin" / "node"
    node.parent.mkdir(parents=True)
    node.write_text("#!/bin/sh\n")
    assert manage.node_exe(str(node), "npm") == "npm"


def test_a_python_that_cannot_build_a_venv_triggers_the_fetch(monkeypatch):
    """D046, and the reason it was worth fixing rather than documenting.

    Debian and Ubuntu package `venv` separately, so `python3.12` without
    `python3.12-venv` is a mainstream configuration — not an edge case. The
    version is *correct*, so the version check passes, and bootstrap used to
    stop and ask for `sudo apt install python3.12-venv`.

    That advice works and it is the wrong advice: it demands root on a machine
    where T070 already has everything needed to avoid the problem. The fetch was
    gated on a version mismatch alone, so the one case it could not rescue was
    the one where the version was already right.
    """
    monkeypatch.setattr(manage, "_venv_has_pip", lambda *a, **k: False)
    monkeypatch.setattr(manage, "_venv_version", lambda *a, **k: None)
    monkeypatch.delenv(manage._REEXEC, raising=False)
    monkeypatch.setattr(manage.shutil, "rmtree", lambda *a, **k: None)

    fetched = []
    monkeypatch.setattr(manage.runtimes, "ensure",
                        lambda *a, **k: fetched.append("python") or "/fetched/python")

    class Result:
        returncode = 0

    monkeypatch.setattr(manage.subprocess, "run", lambda *a, **k: Result())

    assert manage.bootstrap() == 0
    assert fetched == ["python"], (
        "bootstrap should fetch an interpreter that can build a virtualenv "
        "rather than asking the researcher for root")


def test_it_does_not_fetch_forever_when_the_fetched_one_also_fails(monkeypatch,
                                                                   capsys):
    """The sentinel still guards it. An interpreter we fetched that also cannot
    build a virtualenv is a dead end, not a loop — and the distribution's own
    package really is the way out then."""
    monkeypatch.setattr(manage, "_venv_has_pip", lambda *a, **k: False)
    monkeypatch.setattr(manage, "_venv_version", lambda *a, **k: None)
    monkeypatch.setenv(manage._REEXEC, "1")
    monkeypatch.setattr(manage.shutil, "rmtree", lambda *a, **k: None)

    def refuse(*_a, **_k):
        raise AssertionError("fetched a second interpreter after re-executing")

    monkeypatch.setattr(manage.runtimes, "ensure", refuse)

    class Result:
        returncode = 0

    monkeypatch.setattr(manage.subprocess, "run", lambda *a, **k: Result())

    assert manage.bootstrap() == 1
    assert "python3.12-venv" in capsys.readouterr().err
