"""Fetch the published release and put it on disk. The one implementation.

**Why this is a file rather than three copies.** Throughline has three front
doors — `install.sh` for the `curl | sh` line and the macOS/Linux launchers,
`Throughline.bat` for Windows — and every one of them needs the same five
steps: read the signed manifest, derive the archive URL, download it, check the
digest, unpack it. Writing that in POSIX shell *and* in batch would be a third
and fourth copy of an install sequence, which is the drift this repository has
already paid for twice (`bootstrap.sh` installing four of nine packages, the
Dockerfile carrying a third copy of the same list). The doors now find a Python
and hand over here.

It cannot import `scripts/runtimes.py`, which does the same download-and-verify
for CPython builds: nothing is on disk yet. That is the one duplication this
file accepts, and it is the reason the whole thing is stdlib-only and written
for the oldest Python the doors accept (3.8), not for the pinned 3.12.

**What the digest does and does not buy.** It proves the archive is the one the
manifest names. It does not prove the manifest is honest — anyone who can
replace the tarball can replace the checksum beside it. HTTPS to the release
host is the trust anchor for a *first* install, which is the same position
rustup and Homebrew take. The Ed25519 signature matters for *updates*, where an
installed copy already has the public key it shipped with; that path is
`throughline_domain.updates`, not this one.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin

MANIFEST_URL = "https://throughline-research.pages.dev/latest.json"
USER_AGENT = "Throughline-Installer (+https://throughline-research.pages.dev)"
TIMEOUT = 60
DOWNLOAD_TIMEOUT = 600


class InstallError(Exception):
    """Something went wrong that the person running this can act on."""


def _open(url: str, timeout: int):
    """Every request this file makes, with the agent set. See USER_AGENT."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=timeout)


def fetch_manifest(url: str) -> dict:
    try:
        with _open(url, TIMEOUT) as response:
            manifest = json.load(response)
    except Exception as error:
        raise InstallError(
            "Could not reach the release server.\n"
            "    {}\n    {}".format(url, error))
    if not isinstance(manifest, dict):
        raise InstallError("The release manifest is not a JSON object.")
    for field in ("version", "file", "sha256"):
        if not manifest.get(field):
            raise InstallError(
                "The release manifest names no {}.".format(field))
    return manifest


def download(url: str, expected: str, dest: Path, log=print) -> Path:
    """Fetch `url` to `dest` and delete it instead if it is the wrong bytes."""
    try:
        with _open(url, DOWNLOAD_TIMEOUT) as response:
            with dest.open("wb") as out:
                shutil.copyfileobj(response, out)
    except Exception as error:
        dest.unlink(missing_ok=True)
        raise InstallError(
            "Could not download {}\n    {}".format(url, error))

    actual = hashlib.sha256(dest.read_bytes()).hexdigest()
    if actual != expected:
        dest.unlink()
        raise InstallError(
            "The download does not match the checksum the release publishes.\n"
            "    expected sha256 {}\n    received sha256 {}\n"
            "  Refusing to install it. This is either a corrupted download or "
            "a substituted file, and from here they look the same."
            .format(expected, actual))
    log("  checksum verified")
    return dest


def _archive_name(member: tarfile.TarInfo) -> PurePosixPath:
    """Validate one tar member name before any filesystem operation."""
    # Tar paths are POSIX paths even on Windows. Backslashes are treated as
    # separators as well so an archive cannot smuggle a Windows traversal past
    # a POSIX-shaped check and have it become meaningful only on extraction.
    raw = member.name.replace("\\", "/")
    name = PurePosixPath(raw)
    if (not raw or raw.startswith("/") or name.is_absolute()
            or any(part in ("", ".", "..") for part in name.parts)):
        raise InstallError(
            "The release archive would write outside the install directory "
            "or uses an unsafe path: {}".format(member.name))
    return name


def _destination(root: Path, name: PurePosixPath) -> Path:
    """Return a member destination proven to remain below `root`."""
    base = root.resolve()
    target = (base.joinpath(*name.parts)).resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise InstallError(
            "The release archive would write outside the install directory: {}"
            .format(name)) from exc
    return target


def _extract_safely(bundle: tarfile.TarFile, into: Path) -> None:
    """Extract regular files/directories only, without `extractall`.

    Rejecting links is deliberate. Even a link whose own name is inside the
    staging directory can redirect a *later* regular member outside it. Manual
    extraction into a freshly-created private staging directory removes that
    entire class, and also avoids device/FIFO members that have no legitimate
    place in an application release.
    """
    into.mkdir(parents=True, exist_ok=False)
    for member in bundle.getmembers():
        name = _archive_name(member)
        target = _destination(into, name)

        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            # Never preserve setuid/setgid/sticky bits from a downloaded archive.
            os.chmod(target, member.mode & 0o777)
            continue

        if not member.isfile():
            raise InstallError(
                "The release archive contains a link, device, or other unsupported "
                "entry: {}".format(member.name))

        target.parent.mkdir(parents=True, exist_ok=True)
        source = bundle.extractfile(member)
        if source is None:
            raise InstallError(
                "The release archive could not read {}.".format(member.name))
        # The staging tree starts empty. `xb` makes an archive with duplicate
        # member names fail rather than letting a later entry overwrite an
        # earlier one after validation.
        try:
            with source, target.open("xb") as out:
                shutil.copyfileobj(source, out)
        except FileExistsError as exc:
            raise InstallError(
                "The release archive names {} more than once.".format(member.name)
            ) from exc
        os.chmod(target, member.mode & 0o777)


def unpack(archive: Path, into: Path) -> Path:
    """Extract `archive` safely, returning its single top-level directory."""
    with tarfile.open(archive) as bundle:
        _extract_safely(bundle, into)

    entries = list(into.iterdir())
    if len(entries) != 1 or not entries[0].is_dir():
        raise InstallError(
            "Expected the release to hold one top-level directory, found {}."
            .format(sorted(entry.name for entry in entries)))
    return entries[0]


def install(dest: Path, url: str = MANIFEST_URL, log=print) -> Path:
    """Put the current release at `dest` and return it."""
    if dest.exists():
        raise InstallError(
            "{} already exists. Refusing to write into it.\n"
            "  Set THROUGHLINE_INSTALL_DIR to somewhere else.".format(dest))

    manifest = fetch_manifest(url)
    archive_url = urljoin(url, manifest["file"])
    log("  downloading {}".format(manifest["version"]))

    staging = Path(tempfile.mkdtemp(prefix="throughline-install-"))
    try:
        archive = download(archive_url, manifest["sha256"],
                           staging / "release.tar.gz", log=log)
        tree = unpack(archive, staging / "tree")
        if not (tree / "scripts" / "manage.py").is_file():
            raise InstallError(
                "The downloaded release has no scripts/manage.py, so it is not "
                "a Throughline release. Nothing was installed.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tree), str(dest))
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    log("  installed {} into {}".format(manifest["version"], dest))
    return dest


def main(argv: list) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Install the current Throughline release.")
    parser.add_argument(
        "--into", default=os.environ.get("THROUGHLINE_INSTALL_DIR"),
        help="where to install (default: ~/throughline-os)")
    parser.add_argument(
        "--url", default=os.environ.get("THROUGHLINE_RELEASE_URL")
        or MANIFEST_URL,
        help="the release manifest to install from")
    parser.add_argument(
        "--start", action="store_true",
        help="run `manage.py start` once it is installed")
    args = parser.parse_args(argv)

    dest = Path(args.into).expanduser() if args.into \
        else Path.home() / "throughline-os"

    try:
        installed = install(dest, args.url)
    except InstallError as error:
        print("\n{}".format(error), file=sys.stderr)
        return 1

    if args.start:
        import subprocess
        return subprocess.call(
            [sys.executable, str(installed / "scripts" / "manage.py"), "start"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
