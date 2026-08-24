"""Fetching the two runtimes this product refuses to make a researcher install.

The documented install has always asked for **Python 3.12 exactly** — pgserver
ships the embedded PostgreSQL as a binary wheel and publishes none past cp312 —
and, separately, for **Node 20+**, because `serve.sh` runs `next start` and
without it the API comes up and the interface does not. Two prerequisites, one
of them a version nobody has by accident, is the reason the only people who have
ever run this are the two who wrote it.

So the bootstrap brings its own. Both runtimes are relocatable redistributions
that unpack anywhere and need no installer, no admin rights and no PATH change:
CPython from `python-build-standalone` (what `uv` and Rye ship) and the official
Node tarball.

**Why this file is not twenty lines.** It downloads executables over the network
and then runs them. That is the single most dangerous thing this codebase does,
and it deserves the same treatment `connector-sdk/papers.py` gives an
attacker-supplied URL:

- **Every download is pinned by version and verified by SHA-256** against a
  digest committed to this repository. Not the digest the server sends alongside
  it — a checksum fetched over the same connection as the file authenticates
  nothing, because whoever can replace one can replace the other. The digests
  below were read from the publishers' signed release manifests and are part of
  the source tree, so changing what a bootstrap installs means changing a
  reviewed line of code.
- **A mismatch is fatal and leaves nothing behind.** There is no "continue
  anyway" path and no flag to skip it. A corrupted download and a substituted
  one are indistinguishable from here, and the safe reading of the ambiguous
  case is the hostile one.
- **Archives are extracted with member-name checks**, because a tar or zip entry
  may name `../../../.ssh/authorized_keys`, and an unpacker that trusts the
  archive writes it. Python 3.12 ships `tarfile`'s data filter for exactly this;
  zip has no equivalent, so the check here is explicit.
- **Nothing is written to its final path until it is complete and verified.** A
  half-downloaded interpreter left at the name of a good one turns a network
  blip into a permanently broken install that no re-run repairs, which is the
  same trap `manage.py._venv_has_pip()` exists to escape one level up.

**Where they land, and why it is not THROUGHLINE_HOME.** `embeddings.model_dir()`
already settled this question for the downloaded embedding model: a fetched
asset is a shared machine-level thing, while `THROUGHLINE_HOME` is per-instance
state that a test run overrides to isolate its database. Tying the two together
would make every isolated instance re-download a runtime. Runtimes follow the
model's precedent exactly, including the override.

**Known gap, stated rather than discovered later:** the Linux builds selected
here are `gnu`, so they do not run on a musl distribution such as Alpine. The
failure is at exec, and its message names an interpreter rather than a libc.
Detecting musl and selecting the musl build is a real piece of work and is not
done here.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

# Read from https://github.com/astral-sh/python-build-standalone/releases and
# https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt on 2026-08-24.
#
# The patch version is pinned, not floated. A bootstrap that silently installs a
# different interpreter than the one a result was produced on undermines the
# provenance claim this product is built on: "3.12" is not a version, it is a
# family. Bumping these is a reviewed commit, which is the point.
# `install_only_stripped`, not `install_only`, and the difference is measured
# rather than assumed: 104 MB unpacked against 350 MB, a 246 MB saving on an
# install T072 is trying to keep under a gigabyte. What "stripped" removes is
# debug symbols. Everything this product needs was checked on the stripped build
# before pinning it — pip, venv creation, ssl, sqlite3, ctypes, lzma, bz2, zlib,
# a real install from PyPI, and pgserver, which is the package whose cp312-only
# wheels are the reason this version is pinned at all.
CPYTHON_VERSION = "3.12.14"
CPYTHON_RELEASE = "20260814"
NODE_VERSION = "22.23.2"

_PBS = ("https://github.com/astral-sh/python-build-standalone/releases/download"
        f"/{CPYTHON_RELEASE}")
_NODE = f"https://nodejs.org/dist/v{NODE_VERSION}"


class RuntimeError_(RuntimeError):
    """Raised when a runtime cannot be provided, for a reason worth reading."""


class ChecksumMismatch(RuntimeError_):
    """The bytes that arrived are not the bytes this repository pinned.

    Its own class because it is the one failure here that must never be caught
    and retried into submission: a second attempt against the same substituted
    file fails identically, and a loop around it is how a guard becomes a delay.
    """


class UnsupportedPlatform(RuntimeError_):
    """No pinned build for this machine. Names what is supported."""


def host() -> str:
    """This machine, as one of the keys the tables below use.

    `platform.machine()` disagrees with itself across operating systems for the
    same silicon — `arm64` on macOS, `aarch64` on Linux, `AMD64` on Windows
    where everything else says `x86_64` — so the normalisation is here rather
    than repeated at each call site.
    """
    system = platform.system()
    machine = platform.machine().lower()
    arch = {"amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64"}.get(
        machine, machine)
    name = {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}.get(system)
    if name is None:
        raise UnsupportedPlatform(
            f"{system} is not a platform this bootstrap has builds for. "
            f"Supported: Linux, macOS, Windows.")
    return f"{name}-{arch}"


# (url, sha256, path to the executable inside the unpacked directory).
#
# The archives unpack to a single top-level directory, which is stripped during
# extraction so that the layout here does not depend on a name carrying a
# version in it — otherwise every version bump would edit two places and the
# second one would be forgotten.
PYTHON: dict[str, tuple[str, str, str]] = {
    "linux-x86_64": (
        f"{_PBS}/cpython-{CPYTHON_VERSION}+{CPYTHON_RELEASE}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz",
        "5acfa3e9ba26b51ae161c83aff278da915b590d22373a424b2ba55b8afe91fcc",
        "bin/python3"),
    "linux-aarch64": (
        f"{_PBS}/cpython-{CPYTHON_VERSION}+{CPYTHON_RELEASE}-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz",
        "2d8e17dfd732102cfeb18e0e1fa6769b24caa034e159981129590fe409c7157a",
        "bin/python3"),
    "macos-x86_64": (
        f"{_PBS}/cpython-{CPYTHON_VERSION}+{CPYTHON_RELEASE}-x86_64-apple-darwin-install_only_stripped.tar.gz",
        "aec265e3cddaccdb2a3d783331596351b24d4a63c97af0a38f75f643c9451de9",
        "bin/python3"),
    "macos-aarch64": (
        f"{_PBS}/cpython-{CPYTHON_VERSION}+{CPYTHON_RELEASE}-aarch64-apple-darwin-install_only_stripped.tar.gz",
        "dd5b76ab11451a4a4367c17c61d944dded56b425396b07f102922a7ebef7d55f",
        "bin/python3"),
    "windows-x86_64": (
        f"{_PBS}/cpython-{CPYTHON_VERSION}+{CPYTHON_RELEASE}-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
        "89f18f6932917163b74339ebcec2645c8e47ae7f1c5f2ac37f2b4f4cf3beb647",
        "python.exe"),
}

NODE: dict[str, tuple[str, str, str]] = {
    "linux-x86_64": (
        f"{_NODE}/node-v{NODE_VERSION}-linux-x64.tar.xz",
        "d60acfe00a2932254bb0ad20e01b0d74397a0875595de719654b214f4b03f307",
        "bin/node"),
    "linux-aarch64": (
        f"{_NODE}/node-v{NODE_VERSION}-linux-arm64.tar.xz",
        "fff4078c5def658577f92c88db7db3bc0072924bfb93fe52c1e744a54e94abb8",
        "bin/node"),
    "macos-x86_64": (
        f"{_NODE}/node-v{NODE_VERSION}-darwin-x64.tar.xz",
        "96dff79f4e19a78715da559ec7cac2028f4985a175ea0c3454625a269c21deb7",
        "bin/node"),
    "macos-aarch64": (
        f"{_NODE}/node-v{NODE_VERSION}-darwin-arm64.tar.xz",
        "5eff7a9011895aae3f29d06f167b84a62b028a591370c7cafb59103559fd26e1",
        "bin/node"),
    "windows-x86_64": (
        f"{_NODE}/node-v{NODE_VERSION}-win-x64.zip",
        "1177b4137ba5adaa56354ae40f1080c7450e8ae09cecb47da459d1c52ac99f97",
        "node.exe"),
}

RUNTIMES = {"python": PYTHON, "node": NODE}
VERSIONS = {"python": CPYTHON_VERSION, "node": NODE_VERSION}


def runtime_root() -> Path:
    """Where fetched runtimes live. See the module docstring on why not HOME."""
    override = os.environ.get("THROUGHLINE_RUNTIME_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".throughline-os" / "runtimes"


def install_dir(kind: str, root: Path | None = None) -> Path:
    """The versioned directory one runtime unpacks into.

    Versioned because an update that replaces a runtime in place has no way back
    when the new one is broken, and `T073` requires that the previous version
    still works after a failed update.
    """
    return (root or runtime_root()) / f"{kind}-{VERSIONS[kind]}"


def executable(kind: str, root: Path | None = None) -> Path:
    """Where this runtime's binary would be, whether or not it is there yet."""
    try:
        target = RUNTIMES[kind][host()]
    except KeyError:
        raise UnsupportedPlatform(
            f"No pinned {kind} build for {host()}. "
            f"Supported: {', '.join(sorted(RUNTIMES[kind]))}.") from None
    return install_dir(kind, root) / target[2]


def installed(kind: str, root: Path | None = None) -> bool:
    """Whether this runtime is already here — a file, not just a directory.

    A directory is not proof, for the reason `_venv_has_pip` documents one layer
    up: an interrupted unpack leaves one behind, and a check that accepts it
    reports success and then fails at exec, blaming the wrong step.
    """
    return executable(kind, root).exists()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, expected: str, dest: Path,
             log=print) -> Path:
    """Fetch `url` to `dest`, and delete it instead if it is the wrong bytes.

    The verified file is the return value; there is no path through this that
    returns an unverified one.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".part")
    log(f"  downloading {url.rsplit('/', 1)[-1]}")
    try:
        with urllib.request.urlopen(url) as response, partial.open("wb") as out:
            shutil.copyfileobj(response, out)
    except OSError as error:
        partial.unlink(missing_ok=True)
        raise RuntimeError_(f"Could not download {url}: {error}") from error

    actual = _sha256(partial)
    if actual != expected:
        # Removed rather than kept for inspection: a rejected executable sitting
        # on disk beside a good one is a loaded gun, and the digest is in the
        # message for anyone who genuinely needs to compare.
        partial.unlink(missing_ok=True)
        raise ChecksumMismatch(
            f"{url}\n  expected sha256 {expected}\n  received sha256 {actual}\n"
            f"  Refusing to install it. This is either a corrupted download or "
            f"a substituted file, and from here they look the same.")
    partial.replace(dest)
    return dest


def _safe_members(names, kind: str):
    """Reject any archive entry that would write outside the destination.

    Absolute paths and `..` segments are the whole attack: an archive is a list
    of filenames chosen by whoever built it, and an unpacker that joins them to
    a destination without looking will write wherever they say.
    """
    for name in names:
        pure = name.replace("\\", "/")
        if pure.startswith("/") or ".." in pure.split("/"):
            raise RuntimeError_(
                f"Refusing to unpack {kind}: the archive contains an entry that "
                f"escapes its destination ({name!r}).")


def unpack(archive: Path, dest: Path, kind: str) -> Path:
    """Extract `archive` into `dest`, stripping its single top-level directory.

    Unpacked beside the destination and moved into place, so an interrupted
    extraction never leaves something at `dest` that `installed()` would accept.
    """
    staging = dest.with_name(dest.name + ".unpacking")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)

    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            _safe_members(bundle.namelist(), kind)
            bundle.extractall(staging)
    else:
        with tarfile.open(archive) as bundle:
            _safe_members(bundle.getnames(), kind)
            # `data` refuses device nodes, symlinks pointing outside the tree and
            # absolute names. The explicit check above is not redundant: it also
            # covers the zip branch, which has no filter of its own.
            bundle.extractall(staging, filter="data")

    entries = list(staging.iterdir())
    if len(entries) != 1 or not entries[0].is_dir():
        shutil.rmtree(staging, ignore_errors=True)
        raise RuntimeError_(
            f"Expected {kind}'s archive to hold one top-level directory, "
            f"found {[entry.name for entry in entries]}.")

    shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    entries[0].replace(dest)
    shutil.rmtree(staging, ignore_errors=True)
    return dest


def ensure(kind: str, root: Path | None = None, log=print) -> Path:
    """Return the path to `kind`'s executable, fetching it if it is not here.

    Idempotent: the common case is one `exists()` call and no network at all,
    which is what makes it safe to call on every launch rather than only on
    first install.
    """
    if kind not in RUNTIMES:
        raise RuntimeError_(f"Unknown runtime {kind!r}.")
    binary = executable(kind, root)
    if binary.exists():
        return binary

    url, expected, _ = RUNTIMES[kind][host()]
    target = install_dir(kind, root)
    log(f"Fetching {kind} {VERSIONS[kind]} for {host()} — this happens once.")
    archive = download(url, expected, target.parent / url.rsplit("/", 1)[-1], log)
    try:
        unpack(archive, target, kind)
    finally:
        archive.unlink(missing_ok=True)

    if not binary.exists():
        raise RuntimeError_(
            f"Unpacked {kind} but found no executable at {binary}. "
            f"The archive layout has changed and the table in this file is stale.")
    if os.name != "nt":
        binary.chmod(binary.stat().st_mode | 0o111)
    return binary


def main(argv: list[str]) -> int:
    """`python scripts/runtimes.py python|node` — fetch one, print its path.

    Exists so the front-door installer can call this without importing anything,
    and so a person debugging an install can run the download on its own.
    """
    if len(argv) != 1 or argv[0] not in RUNTIMES:
        print(f"usage: {Path(__file__).name} {'|'.join(RUNTIMES)}",
              file=sys.stderr)
        return 2
    try:
        print(ensure(argv[0]))
    except RuntimeError_ as error:
        print(f"\n{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
