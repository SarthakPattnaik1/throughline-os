#!/usr/bin/env python3
"""Set up and run the stack, on whichever platform this is.

`bootstrap.sh` and `dev.sh` delegate here. The logic lives in one place rather
than in a bash script plus a PowerShell twin, because two implementations of the
same startup sequence drift, and the one nobody runs is the one that breaks.

It also removes the assumption that broke Windows support in the small: every
script hardcoded `.venv/bin/python`, which on Windows is `.venv\\Scripts\\python.exe`.
That path is computed here once.

Standard library only, deliberately. `bootstrap` has to run before there is a
virtualenv to install anything into.

    python scripts/manage.py bootstrap
    python scripts/manage.py dev
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import urllib.request
import webbrowser
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import runtimes

ROOT = Path(__file__).resolve().parent.parent
WINDOWS = sys.platform == "win32"

# Set on the child when this script re-executes itself under a fetched
# interpreter. Without it a build whose fetched Python somehow reports the wrong
# version would fetch and re-exec forever, which is a worse failure than the
# refusal it replaced because it never returns.
_REEXEC = "THROUGHLINE_BOOTSTRAP_REEXEC"

# Below this, `next` will not start. Enforced rather than merely printed: a
# bootstrap that ships Node is pointless if an older one on PATH shadows it.
NODE_MINIMUM = 20

# The floor every pyproject declares, and the ceiling pgserver imposes: it ships
# the embedded PostgreSQL as a binary wheel and publishes none past cp312. Windows
# is included in that — the win_amd64 wheels exist through cp312 — so this is a
# version constraint, not a platform one.
REQUIRED_PYTHON = tuple(int(part) for part in runtimes.CPYTHON_VERSION.split("."))

# Order satisfies the dependency graph. None of these is published, so pip can
# only resolve `throughline-visual` and friends if the directory providing them is
# installed first. Installing a subset sends pip to the index after a package that
# is not there.
PACKAGES = (
    "packages/schemas",
    "packages/model",
    "packages/connector-sdk",
    "packages/visual-spec",
    "packages/ingestion",
    "services/scientific-runtime",
    "packages/research-domain",
    "services/workers",
    "apps/api",
)


def venv_python(root: Path = ROOT) -> Path:
    """The interpreter inside .venv, wherever this platform keeps it."""
    return root / ".venv" / ("Scripts" if WINDOWS else "bin") / (
        "python.exe" if WINDOWS else "python")


def _venv_has_pip(root: Path = ROOT) -> bool:
    """A virtualenv is only usable if it has pip, and a directory is not proof.

    Where ensurepip is unbundled — Ubuntu 24.04 among them — `python -m venv`
    exits non-zero *and leaves the directory behind*, so a later run that checks
    only for the directory skips creation and fails further down with "No module
    named pip", blaming the wrong step.
    """
    python = venv_python(root)
    if not python.exists():
        return False
    return subprocess.run([str(python), "-m", "pip", "--version"],
                          capture_output=True).returncode == 0


def _explain_the_version(want: str, have: str) -> None:
    """Why 3.12 and not whatever this machine has. Printed only when we cannot
    fix it ourselves — it is an explanation, not an instruction, now that the
    normal path is to go and get the right one."""
    print(f"Python {want} is required; this is {have} ({sys.executable}).",
          file=sys.stderr)
    print(f"\n  Every package here declares requires-python >= {want}, and"
          f"\n  pgserver — which provides the embedded PostgreSQL — publishes"
          f"\n  no wheel past cp{''.join(str(p) for p in REQUIRED_PYTHON)}."
          f" Anything newer cannot install\n  the database."
          f"\n\n  Run this with a {want} interpreter instead.", file=sys.stderr)


def _explain_no_venv() -> None:
    """The fallback advice, for when fetching an interpreter also failed.

    Still worth printing: offline, on a platform with no pinned build, or behind
    a proxy, installing the distribution's venv package really is the way out.
    It is the second answer now rather than the first.
    """
    print("\nCould not create a virtualenv with pip in it, and could not "
          "fetch an\ninterpreter that can.", file=sys.stderr)
    print("  If the output above mentioned ensurepip, the venv module is"
          "\n  packaged separately on this distribution:"
          "\n    Debian/Ubuntu:  sudo apt install python3.12-venv"
          "\n    Fedora/RHEL:    sudo dnf install python3-virtualenv"
          "\n\n  Then run this again.", file=sys.stderr)


def _venv_version(root: Path = ROOT) -> tuple[int, int] | None:
    """The Python the existing virtualenv was built from, if it has one.

    This became a question worth asking the moment the bootstrap could supply
    its own interpreter. Before, the venv was always built by whatever ran this
    script and the version could not drift; now a venv left by an earlier run
    may have been built from a different Python entirely, and `_venv_has_pip`
    would happily call it usable right up until an extension module fails to
    import with a message about a symbol.
    """
    python = venv_python(root)
    if not python.exists():
        return None
    result = subprocess.run(
        [str(python), "-c",
         "import sys; print(sys.version_info[0], sys.version_info[1])"],
        capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        major, minor = result.stdout.split()
        return int(major), int(minor)
    except ValueError:
        return None


def bootstrap() -> int:
    version = sys.version_info[:3]
    want = ".".join(str(part) for part in REQUIRED_PYTHON)
    have = ".".join(str(part) for part in version)

    if version != REQUIRED_PYTHON:
        if os.environ.get(_REEXEC):
            # Fetched an interpreter, ran this under it, and it still is not the
            # version it claims to be. Stopping is the only safe answer: the
            # alternative is fetching the same thing again forever.
            print(f"The fetched interpreter reports {have}, not {want}.",
                  file=sys.stderr)
            _explain_the_version(want, have)
            return 1

        print(f"This is Python {have}, and {want} is required.")
        print("Fetching one rather than asking you to install it.\n")
        try:
            interpreter = runtimes.ensure("python")
        except runtimes.RuntimeError_ as error:
            print(f"\n{error}\n", file=sys.stderr)
            _explain_the_version(want, have)
            return 1

        return subprocess.run(
            [str(interpreter), str(Path(__file__).resolve()), "bootstrap"],
            env={**os.environ, _REEXEC: "1"}).returncode

    # A virtualenv built by a different interpreter is not reusable, and the
    # symptom if it is reused is an import error naming a C symbol.
    existing = _venv_version()
    if existing is not None and existing != REQUIRED_PYTHON[:2]:
        stale = ".".join(str(part) for part in existing)
        print(f"Replacing the existing virtualenv: it was built from Python "
              f"{stale}, and this is {have}.")
        shutil.rmtree(ROOT / ".venv", ignore_errors=True)

    if not _venv_has_pip():
        shutil.rmtree(ROOT / ".venv", ignore_errors=True)
        subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")])
        if not _venv_has_pip():
            # Leave nothing half-built for the next run to trip over.
            shutil.rmtree(ROOT / ".venv", ignore_errors=True)

            # The right version, and still unable to build a virtualenv. Debian
            # and Ubuntu package `venv` separately, so `python3.12` without
            # `python3.12-venv` lands exactly here — a mainstream configuration,
            # not an edge case.
            #
            # This used to stop and ask for `sudo apt install python3.12-venv`.
            # That advice works and it is the wrong advice: it demands root on a
            # machine where T070 already has everything needed to avoid the
            # problem. The fetched python-build-standalone build ships pip and
            # creates virtualenvs fine. The fetch was gated on a *version*
            # mismatch alone, so the one case it could not rescue was the one
            # where the version was already correct — which is D046, and which
            # cost a real repair on this machine.
            #
            # "Cannot build a virtualenv" is now a fetch trigger in its own
            # right. The sentinel still guards it: an interpreter we fetched
            # that also cannot build one is a dead end, not a loop.
            if not os.environ.get(_REEXEC):
                print("\nThis Python cannot create a virtualenv — on Debian "
                      "and Ubuntu the venv\nmodule is packaged separately. "
                      "Fetching one that can, rather than\nasking you for root."
                      "\n")
                try:
                    interpreter = runtimes.ensure("python")
                except runtimes.RuntimeError_ as error:
                    print(f"\n{error}\n", file=sys.stderr)
                    _explain_no_venv()
                    return 1
                return subprocess.run(
                    [str(interpreter), str(Path(__file__).resolve()),
                     "bootstrap"],
                    env={**os.environ, _REEXEC: "1"}).returncode

            _explain_no_venv()
            return 1

    python = str(venv_python())
    subprocess.run([python, "-m", "pip", "install", "-q", "--upgrade", "pip"], check=True)
    for package in PACKAGES:
        print(f"  installing {package}")
        result = subprocess.run(
            [python, "-m", "pip", "install", "-q",
             "-c", str(ROOT / "requirements" / "scientific-runtime.lock"),
             "-e", str(ROOT / package)])
        if result.returncode != 0:
            print(f"\nFailed installing {package}.", file=sys.stderr)
            return result.returncode
    # Test-only dependencies, installed here rather than declared on a package
    # that does not need them at runtime.
    #
    # `xlwt` is the awkward one and is here on purpose. The .xls test writes its
    # own fixture, and it is the only way to: pandas 2.x dropped its xlwt
    # writer, and xlrd — which reads .xls — cannot write. Without it that test
    # skips, and CI's skip allowlist accepts three reasons, none of them this
    # one. So a fresh checkout would fail the build on a library nothing
    # declared, which is precisely the class of defect this project's CI exists
    # to catch. It ran here only because Wave 0 installed it by hand.
    subprocess.run([python, "-m", "pip", "install", "-q",
                    "-c", str(ROOT / "requirements" / "scientific-runtime.lock"),
                    "pytest", "httpx", "xlwt"], check=True)

    print("Applying migrations (this boots the bundled PostgreSQL on first run)…")
    subprocess.run([python, "-c",
                    "from throughline_domain.migrate import migrate;"
                    " print('applied:', migrate() or 'nothing new')"], check=True)

    _ensure_interface()
    print("\nReady. Start the stack with:  python scripts/manage.py dev")
    return 0


def _can_run_dev_server(root: Path | None = None) -> bool:
    """Whether there is an npm project here to run `next dev` from.

    **This is not the same question as "is Node installed", and conflating them
    made every release unusable on any machine that happened to have Node.**
    `start` branched on `if node:` and ran `npm run dev` inside `apps/web`. A
    release ships the *exported* interface and no npm project at all, so npm
    exited immediately with `ENOENT ... apps/web/package.json` — and because the
    supervisor below deliberately exits as soon as any child does, that killed
    the API and worker that had just started correctly. The researcher saw the
    stack come up and shut itself down.

    Invisible on a source checkout, where `package.json` is present and the dev
    server is the right thing to run: the defect only exists on the installation
    nobody develops on. Found by a release install on a Mac that had Node.
    """
    root = root or ROOT
    return (root / "apps" / "web" / "package.json").is_file()


def _ensure_interface(log=print) -> bool:
    """Make sure there is an interface for the API to serve.

    Node used to be a *runtime* dependency: `serve.sh` started `next start`, and
    without it the researcher got a warning line instead of a product — so
    bootstrap fetched a 204 MB runtime onto every machine. The interface is now
    an exported folder of files the API serves itself, which changes what this
    function is for. Node is needed to **build** that folder and for nothing
    else.

    Hence the order. **An already-built interface needs no Node at all**, which
    is the case a release should arrive in: ship the exported files and the
    install is a Python install. Only a source checkout has to build one, and
    only then is a runtime fetched.

    Not fatal when it cannot be done. The API and worker are genuinely useful
    headless, `serve.sh` says so, and the interface itself answers 503 naming
    the command that fixes it rather than showing a blank page.
    """
    if (ROOT / "apps" / "web" / "out" / "index.html").is_file():
        log("  Interface already built — nothing to do.")
        return True

    if os.environ.get("THROUGHLINE_SKIP_NODE"):
        log("  Skipping the interface (THROUGHLINE_SKIP_NODE is set) — API only.")
        return False

    node = _node_on_path()
    major = _node_major(node) if node else None
    if node is None or major is None or major < NODE_MINIMUM:
        try:
            node = str(runtimes.ensure("node", log=log))
        except runtimes.RuntimeError_ as error:
            log(f"\n  No interface built — Node {NODE_MINIMUM}+ is needed to "
                f"build one and could not be fetched: {error}")
            log("  The API and worker will run. Build it later with:")
            log("    python scripts/manage.py build-interface")
            return False

    web = ROOT / "apps" / "web"
    environment = dict(os.environ)
    environment["PATH"] = str(Path(node).parent) + os.pathsep + environment.get("PATH", "")

    if not (web / "node_modules").is_dir():
        # Said out loud because it is the largest single download in the whole
        # install and it is *temporary*: these are build dependencies, and a
        # release that ships the exported files needs none of them.
        log("  Installing the interface's build dependencies (a few hundred MB,")
        log("  needed only to build it — a release ships it already built)…")
        result = subprocess.run([node_exe(node, "npm"), "ci", "--no-audit",
                                 "--no-fund"], cwd=str(web), env=environment)
        if result.returncode != 0:
            log("  Could not install them; the interface will not be available.")
            return False

    log("  Building the interface…")
    if build_interface() != 0:
        log("  The interface did not build. The API and worker still run.")
        return False
    return True


def start(api_port: int, web_port: int) -> int:
    """Install if this machine has not got it yet, then run. One command.

    The launchers exist to be double-clicked by somebody who has never opened a
    terminal, and what they need is not `bootstrap` or `dev` but "make it work".
    Splitting that into two commands and a decision is exactly the step that
    loses people, so the decision is made here — the same sequence `ROADMAP.md`
    describes: detect an existing install, otherwise build one, then launch.

    **The window stays visible while it installs**, and the message says how long
    it will take. A first run downloads a relocatable Python, possibly Node, and
    several hundred megabytes of wheels; behind a hidden window that is
    indistinguishable from a freeze, and the person kills it at four minutes and
    reports that it does not start.

    The venv is checked for its *version*, not merely its existence, so an
    install left behind by a different interpreter is rebuilt rather than used —
    `bootstrap` knows how to do that, this only has to ask the question.
    """
    if _venv_has_pip() and _venv_version() == REQUIRED_PYTHON:
        return dev(api_port, web_port, open_browser=True)

    print("First run — setting this up before starting it.")
    print("It downloads a few hundred megabytes and takes a few minutes.")
    print("Leave this window open; it will start on its own when it is done.\n",
          flush=True)
    code = bootstrap()
    if code != 0:
        print("\nSetup did not finish, so there is nothing to start yet.",
              file=sys.stderr)
        return code
    print("\nSetup finished. Starting…\n", flush=True)
    return dev(api_port, web_port, open_browser=True)


def _venv_json(python: Path, expression: str) -> dict | None:
    """Ask the installed packages a question and get structured data back.

    Through the virtualenv's interpreter, because that is where the product is
    installed — `manage.py` itself is standard-library only and cannot import
    any of it.
    """
    code = f"import json; {expression}"
    result = subprocess.run([str(python), "-c", code], capture_output=True,
                            text=True, cwd=str(ROOT))
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError):
        return None


def _update_from_release(python: Path, state: dict, log=print) -> int:
    """Apply an update to an installation that has no git checkout.

    **The virtualenv is not moved, and that is the whole shape of this.** It
    lives at `<root>/.venv` and the workspace packages are installed *editable*
    into it, pointing back at `<root>/packages/...`. A virtualenv is not
    relocatable — its scripts carry absolute paths — so an update that renamed
    the installation directory would leave a venv addressing somewhere that no
    longer exists. Instead the **source is replaced in place** and the venv
    stays exactly where it is, which keeps every editable install valid and
    every path in it true.

    Rollback is therefore a move rather than a re-download: the replaced tree
    goes to `.rollback/` first and comes back if anything after it fails. The
    previous version is literally still on disk, which is what T073 asked for
    and `git reset --hard` only approximated by reconstructing it.

    The tarball URL is **derived from the manifest's own location** rather than
    read out of the manifest. A signed manifest naming an arbitrary host would
    be a redirect the signature endorses; deriving it means the archive comes
    from the same place the verified manifest did, and there is no field to
    abuse.
    """
    import shutil
    import tempfile
    from urllib.parse import urljoin

    import runtimes
    from throughline_domain import updates as domain_updates

    manifest = state["manifest"]
    base = os.environ.get("THROUGHLINE_RELEASE_URL") or domain_updates.RELEASE_URL
    archive_url = urljoin(base, manifest["file"])

    log(f"\nBacking up before anything changes "
        f"(current: {state['current'].get('version')})…")
    if subprocess.run([str(ROOT / "scripts" / "backup.sh")],
                      cwd=str(ROOT)).returncode != 0:
        print("\nThe backup failed, so nothing was updated. The database is "
              "the only copy of your research.", file=sys.stderr)
        return 1

    staging = Path(tempfile.mkdtemp(prefix="throughline-update-"))
    rollback = ROOT / ".rollback"
    try:
        log(f"\nDownloading {manifest['version']}…")
        try:
            archive = runtimes.download(archive_url, manifest["sha256"],
                                        staging / manifest["file"], log=log)
        except runtimes.RuntimeError_ as error:
            print(f"\n{error}\n\nNothing was changed.", file=sys.stderr)
            return 1

        unpacked = runtimes.unpack(archive, staging / "new", "release")

        # Checked before anything is moved: an archive that unpacked into
        # something unrecognisable must not be discovered halfway through a swap.
        if not (unpacked / "scripts" / "manage.py").is_file():
            print("\nThe downloaded release has no scripts/manage.py. "
                  "Nothing was changed.", file=sys.stderr)
            return 1

        incoming = sorted(p.name for p in unpacked.iterdir())
        shutil.rmtree(rollback, ignore_errors=True)
        rollback.mkdir()

        log("\nReplacing the source…")
        moved: list[str] = []
        try:
            for name in incoming:
                existing = ROOT / name
                if existing.exists():
                    shutil.move(str(existing), str(rollback / name))
                    moved.append(name)
                shutil.move(str(unpacked / name), str(ROOT / name))
        except OSError as error:
            for name in moved:
                shutil.rmtree(ROOT / name, ignore_errors=True)
                shutil.move(str(rollback / name), str(ROOT / name))
            print(f"\nThe swap failed and was undone: {error}", file=sys.stderr)
            return 1

        def put_it_back(why: str) -> int:
            print(f"\n{why}", file=sys.stderr)
            print("Putting the previous version back…", file=sys.stderr)
            for name in moved:
                shutil.rmtree(ROOT / name, ignore_errors=True)
                shutil.move(str(rollback / name), str(ROOT / name))
            subprocess.run([str(python), "-m", "pip", "install", "-q", "-e",
                            str(ROOT / "apps" / "api")], capture_output=True)
            print("The previous version is in place again.", file=sys.stderr)
            print("\nYour backup is in ~/throughline-backups. If the database "
                  "needs restoring too:\n  ./scripts/restore.sh <archive.tar> "
                  "--force", file=sys.stderr)
            return 1

        log("\nReinstalling packages…")
        for package in PACKAGES:
            if subprocess.run([str(python), "-m", "pip", "install", "-q", "-e",
                               str(ROOT / package)]).returncode != 0:
                return put_it_back(f"Installing {package} failed.")

        log("\nApplying migrations…")
        if subprocess.run(
                [str(python), "-c",
                 "from throughline_domain.migrate import migrate;"
                 " print('applied:', migrate() or 'nothing new')"],
                cwd=str(ROOT)).returncode != 0:
            return put_it_back("A migration failed.")

        shutil.rmtree(rollback, ignore_errors=True)
        log(f"\nUpdated to {manifest['version']}.")
        log("Restart Throughline for it to take effect.")
        return 0
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def update(check_only: bool) -> int:
    """Update this installation, backing up first and reverting on failure.

    **Never automatic, and this is the only thing that applies one.** An update
    replaces the code the API process is executing, so it cannot be done from
    inside that process — a running server cannot swap itself out from
    underneath a request. The button in Settings checks and then names this
    command; it does not run it.

    The order is the whole design, and it is chosen around one fact: **the
    database is the researcher's only copy of their research.**

    1. Refuse on a dirty checkout. Updating fast-forwards the working tree, and
       uncommitted work is exactly the thing this must not silently discard.
    2. Back up before anything. `backup.sh` writes the database and the object
       store into one archive, because either without the other is useless.
    3. Fast-forward only. A merge or a rebase here could conflict, and a
       half-updated checkout is a worse place to be than an old one.
    4. Reinstall, rebuild, migrate — in that order, because a migration may
       depend on code that arrived in this update.
    5. On any failure after the code moved, put the code back and reinstall from
       it, so the previous version runs again.

    What it deliberately does **not** do is restore the database automatically.
    Migrations are forward-only by construction and each runs in its own
    transaction, so a failure leaves the schema at the last one that succeeded
    rather than half-applied — and old code generally tolerates a newer schema.
    Restoring is destructive and would throw away anything done since the backup,
    which is a decision that belongs to the researcher. The command is printed
    instead.
    """
    python = venv_python()
    if not python.exists():
        print("No virtualenv. Run bootstrap first.", file=sys.stderr)
        return 1

    state = _venv_json(
        python, "from throughline_domain import updates;"
                " print(json.dumps(updates.check()))")
    if state is None:
        print("Could not check for updates.", file=sys.stderr)
        return 1

    here = state["current"]
    print(f"This installation: {here['version'] or 'unknown'} "
          f"({here['source']})")

    if not state.get("checked"):
        print(f"\n{state['reason']}", file=sys.stderr)
        # Not an error when only checking: "I could not ask" is a legitimate
        # answer to a question, and exiting non-zero would make a button red.
        return 0 if check_only else 1

    print(f"Following: {state['following']} ({state['channel']})")
    if state.get("ahead"):
        print(f"  {state['ahead']} local commit(s) not on {state['channel']} — "
              f"this checkout is ahead as well as behind.")

    if not state["update_available"]:
        print("\nUp to date.")
        return 0

    print(f"\n{state['behind']} update(s) available on {state['channel']}.")
    if check_only:
        print("Apply with:  python scripts/manage.py update")
        return 0

    # A released copy has no checkout to be dirty and no branch to fast-forward;
    # it swaps a verified tarball in instead.
    if state.get("channel") == "releases":
        return _update_from_release(python, state)

    dirty = _git("status", "--porcelain", check=False).strip()
    if dirty:
        print("\nThis checkout has uncommitted changes:", file=sys.stderr)
        for line in dirty.splitlines()[:10]:
            print(f"  {line}", file=sys.stderr)
        print("\nUpdating fast-forwards the working tree, which would discard "
              "them.\nCommit or stash them first.", file=sys.stderr)
        return 1

    before = _git("rev-parse", "HEAD").strip()
    print(f"\nBacking up before anything changes (current: {before[:9]})…")
    backup = subprocess.run([str(ROOT / "scripts" / "backup.sh")], cwd=str(ROOT))
    if backup.returncode != 0:
        print("\nThe backup failed, so nothing was updated. The database is "
              "the only copy of your research and an update that cannot be "
              "walked back is not one worth applying.", file=sys.stderr)
        return backup.returncode

    target = (f"refs/tags/{state['channel']}"
              if state["following"] == "a release tag" else "origin/main")
    print(f"\nUpdating to {state['channel']}…")
    moved = subprocess.run(["git", "-C", str(ROOT), "merge", "--ff-only", target])
    if moved.returncode != 0:
        print("\nCould not fast-forward. Nothing was changed.", file=sys.stderr)
        return moved.returncode

    def put_it_back(why: str) -> int:
        print(f"\n{why}", file=sys.stderr)
        print(f"Putting the code back to {before[:9]} …", file=sys.stderr)
        subprocess.run(["git", "-C", str(ROOT), "reset", "--hard", before],
                       capture_output=True)
        subprocess.run([str(python), "-m", "pip", "install", "-q", "-e",
                        str(ROOT / "apps" / "api")], capture_output=True)
        print("The previous version is in place again.", file=sys.stderr)
        print("\nYour backup is in ~/throughline-backups. If the database "
              "needs restoring too:", file=sys.stderr)
        print("  ./scripts/restore.sh <archive.tar> --force", file=sys.stderr)
        return 1

    print("\nReinstalling packages…")
    for package in PACKAGES:
        result = subprocess.run([str(python), "-m", "pip", "install", "-q", "-e",
                                 str(ROOT / package)])
        if result.returncode != 0:
            return put_it_back(f"Installing {package} failed.")

    if (ROOT / "apps" / "web" / "out").exists() or _node_on_path():
        print("\nRebuilding the interface…")
        if build_interface() != 0:
            return put_it_back("The interface did not rebuild.")

    print("\nApplying migrations…")
    migrated = subprocess.run(
        [str(python), "-c",
         "from throughline_domain.migrate import migrate;"
         " print('applied:', migrate() or 'nothing new')"], cwd=str(ROOT))
    if migrated.returncode != 0:
        return put_it_back("A migration failed.")

    after = _venv_json(python, "from throughline_domain import version;"
                               " print(json.dumps(version.current()))")
    print(f"\nUpdated to {after['version'] if after else 'a new version'}.")
    print("Restart Throughline for it to take effect.")
    return 0


def release_key() -> int:
    """Generate a release signing keypair, once, for a person to store.

    Prints the private half rather than writing it anywhere. A signing key
    written to disk by a script is a signing key that gets committed eventually,
    and the only thing standing between a private repository and a published
    private key is nobody having run `git add -A` at the wrong moment — which
    happens.

    The public half is written into `keys/release.pub`, committed, and therefore
    travels inside every release tarball. That placement is the point: it
    arrives with the software rather than from the server being verified.

    **If the private key is lost, installed copies stop accepting updates** —
    a new public key can only reach them in a release they would have to verify
    with the key they no longer have. Worth understanding before generating one.
    """
    from throughline_domain import signing

    target = ROOT / signing.PUBLIC_KEY_FILE
    if target.is_file() and target.read_text().strip():
        print(f"A public key already exists at {target}.", file=sys.stderr)
        print("Replacing it would stop every installed copy from accepting "
              "updates,\nbecause they verify against the key they shipped "
              "with. Delete it deliberately\nif that is what you mean.",
              file=sys.stderr)
        return 1

    private, public = signing.generate()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(public + "\n")

    print(f"Public key written to {target} — commit it.")
    print("\nPrivate key below. It is not written anywhere. Put it in a "
          "password manager\nand a CI secret, then point "
          "THROUGHLINE_RELEASE_KEY at a file containing it.\n")
    print(private)
    print("Losing it means installed copies can never accept another update.")
    return 0


def release(destination: str | None, version: str | None) -> int:
    """Build the artifact a stranger downloads: tarball, checksum, manifest.

    Here rather than in a workflow file, and the workflow will call this. YAML
    is not testable and not runnable locally, and a release pipeline that only
    exists inside CI cannot be exercised on the day CI is down — which is today
    (D034). `bootstrap.sh` is a wrapper around this file for the same reason.

    The interface is built first if it is not already there. A release without
    it is a Python package that serves 503s, and the check costs one `stat`.
    """
    import release as release_build

    root = ROOT
    out = Path(destination) if destination else root / "dist"

    if not (root / "apps" / "web" / "out" / "index.html").is_file():
        print("No built interface; building one first…")
        if build_interface() != 0:
            return 1

    print(f"\nBuilding a release into {out}")
    try:
        manifest = release_build.build(root, PACKAGES, out, version=version)
    except release_build.ReleaseError as error:
        print(f"\n{error}", file=sys.stderr)
        return 1

    print(f"\n  version   {manifest['version']}")
    print(f"  file      {manifest['file']}")
    print(f"  size      {manifest['size'] / 1_000_000:.0f} MB")
    print(f"  sha256    {manifest['sha256']}")
    print(f"\nOne artifact serves every platform: the interface is already "
          f"built,\nand the runtimes are fetched per machine at install time.")
    if manifest.get("signature"):
        print("\nThe manifest is signed. An installed copy verifies it before "
              "applying\nan update, against the public key it shipped with.")
    else:
        print("\nThe checksum beside the tarball is not the security story — "
              "anyone who\ncan replace one can replace the other. See "
              "keys/README.md.")
    return 0


#: Where `dev.sh` puts the web dev server, and therefore the port to look at
#: when deciding whether one is running. Overridable the same way `dev.sh`
#: overrides it, so a person who moved it is still warned.
WEB_DEV_PORT = int(os.environ.get("WEB_PORT", "3000"))


def _dev_server_running(port: int = WEB_DEV_PORT) -> bool:
    """Whether something is serving the web dev port.

    Asked by `build_interface`, because a production build and `next dev` cannot
    share a checkout. `NEXT_DIST_DIR=out` sends the *export* to `out/`, and that
    part works — but Next still writes its build manifests through `.next` on
    the way, which is where `next dev` keeps its own. The dev server then reads
    files the build has already replaced and every route answers 500 with
    `ENOENT: .next/static/development/_buildManifest.js.tmp.*`.

    Reproduced deliberately rather than inferred: with a healthy dev server up,
    `build-interface` took `/workspace` and `/gesture-check` from 200 to 500 and
    put twenty errors in its log. Recovery is `rm -rf apps/web/.next` and a
    restart, which is easy once you know and baffling when you do not — the
    build reports success, and the thing that breaks is a different process.

    A socket rather than a process list: `pgrep` is not everywhere, and what
    matters is whether the port is served, not what is serving it.
    """
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.35)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def build_interface(force: bool = False) -> int:
    """Export the interface to `apps/web/out`, where the API serves it from.

    One command, because the destination matters and is not the default. A
    static export is written to whatever `distDir` says, and `distDir` defaults
    to `.next` — which is also where `next dev` keeps its working files. Building
    into `.next` would leave the API serving a dev server's scratch space rather
    than an exported site, and the symptom is a blank page rather than an error.

    **Node is needed here and nowhere else.** This is the one step that requires
    it, and it is a step somebody runs before shipping rather than something a
    researcher's machine has to do — which is the whole reason a second language
    runtime no longer has to be fetched, checksummed and updated on every
    install.
    """
    if not force and _dev_server_running():
        print(
            f"A dev server is answering on port {WEB_DEV_PORT}, and a "
            f"production build\n"
            "  cannot share a checkout with it: this build writes through "
            ".next, which\n"
            "  is where `next dev` keeps its own files, and every route it "
            "serves will\n"
            "  start answering 500 until .next is deleted and it is restarted."
            "\n\n"
            "  Stop the dev server first, or pass --force and then run:\n"
            "      rm -rf apps/web/.next\n",
            file=sys.stderr)
        return 1

    node = _node_on_path()
    if node is None:
        print("Building the interface needs Node 20+, which is not installed.\n"
              "  This is a build step, not something an installed copy does —\n"
              "  a release ships the exported files and needs no Node at all.",
              file=sys.stderr)
        return 1
    major = _node_major(node)
    if major is not None and major < NODE_MINIMUM:
        print(f"Node {major} is too old to build the interface; "
              f"{NODE_MINIMUM}+ is required.", file=sys.stderr)
        return 1

    web = ROOT / "apps" / "web"
    environment = dict(os.environ)
    environment["PATH"] = str(Path(node).parent) + os.pathsep + environment.get("PATH", "")
    environment["NEXT_DIST_DIR"] = "out"
    environment["NEXT_TELEMETRY_DISABLED"] = "1"

    print("Exporting the interface to apps/web/out …", flush=True)
    result = subprocess.run([node_exe(node, "npm"), "run", "build"],
                            cwd=str(web), env=environment)
    if result.returncode != 0:
        return result.returncode

    # Checked rather than trusted. `npm run build` exits 0 having compiled and
    # then failed to export more than once in this codebase's short history, and
    # an empty `out/` is served as a 503 much later — by which point the person
    # reading it is debugging the API rather than the build.
    index = web / "out" / "index.html"
    if not index.is_file():
        print(f"\nThe build reported success but {index} is not there.",
              file=sys.stderr)
        return 1
    size = sum(f.stat().st_size for f in (web / "out").rglob("*") if f.is_file())
    print(f"\nInterface exported: {size / 1_000_000:.0f} MB in apps/web/out")
    print("The API serves it; nothing needs Node to run it.")
    return 0


def desktop_entry() -> int:
    """Put Throughline in the Linux applications menu.

    The file is written by `throughline_domain.launchers`, not here. The
    Settings screen needs the same thing, and two implementations of one
    `.desktop` file would drift — which is the defect this repository has
    shipped more than once. This is the terminal way to ask for it; the button
    is the other way, and they write the same bytes.

    The Linux half of T071 asked for an `.AppImage`, and that is the one door
    on its list that cannot be a script here: an AppImage is a squashfs image
    built by `appimagetool` around a bundled runtime. What the request wants is
    *a thing you double-click*, and on Linux that is a `.desktop` entry.
    Recorded as D033 so the substitution is visible.
    """
    python = venv_python()
    if not python.exists():
        print("No virtualenv. Run bootstrap first.", file=sys.stderr)
        return 1

    result = _venv_json(
        python, "from throughline_domain import launchers;"
                " print(json.dumps(launchers.install_desktop_entry()))")
    if result is None:
        print("Could not write the desktop entry.", file=sys.stderr)
        return 1
    if not result.get("installed"):
        print(result.get("note", "Not installed."), file=sys.stderr)
        return 1
    print(f"Written {result['path']}")
    print(result["note"])
    return 0


def _node_major(binary: str) -> int | None:
    """The major version of a node binary, or None if it will not answer."""
    try:
        result = subprocess.run([binary, "--version"], capture_output=True,
                                text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.match(r"v(\d+)\.", result.stdout.strip())
    return int(match.group(1)) if match else None


def _node_candidates() -> list[str]:
    """Every node this machine might use, best-known location first."""
    candidates = []
    found = shutil.which("node")
    if found:
        candidates.append(found)
    # Where Phase 5 installed it by hand, before anything fetched it.
    legacy = Path.home() / ".local" / "opt" / "node" / "bin" / "node"
    if legacy.exists():
        candidates.append(str(legacy))
    try:
        managed = runtimes.executable("node")
    except runtimes.RuntimeError_:
        managed = None
    if managed is not None and managed.exists():
        candidates.append(str(managed))
    return candidates


def _node_on_path() -> str | None:
    """The first node new enough to run the interface, else the first we found.

    Version, not just presence, because this is now a real fork: a machine may
    carry an old node on PATH *and* the one the bootstrap fetched, and picking
    by position rather than by capability would prefer the one that cannot
    start `next`. Falling back to the first candidate keeps `doctor` honest —
    "Node v18.4.0, too old" is a better report than "not found" about a node
    that is plainly there.
    """
    candidates = _node_candidates()
    for candidate in candidates:
        major = _node_major(candidate)
        if major is not None and major >= NODE_MINIMUM:
            return candidate
    return candidates[0] if candidates else None


def _spawn(command: list[str], **kwargs: object) -> subprocess.Popen:
    """Start a child in its own group, so it can be stopped with its children."""
    if WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)  # type: ignore[arg-type]


def _stop(process: subprocess.Popen) -> None:
    """Stop a child and anything it spawned, politely then not.

    npm starts node, uvicorn --reload starts a worker process: signalling only the
    process we know about leaves those running and holding the port, so the next
    `dev` fails with an address already in use.
    """
    if process.poll() is not None:
        return
    try:
        if WINDOWS:
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (OSError, ValueError):
        pass
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        try:
            if WINDOWS:
                # No process-group kill without a job object; taskkill walks the
                # tree, which is what is needed here.
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                               capture_output=True)
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (OSError, ValueError):
            pass


def running_stacks() -> list[tuple[int, str]]:
    """The `manage.py dev` processes running out of *this* checkout.

    Matched on the script's own path, not on the words "manage.py": a second
    clone, or somebody else's project, must not be stopped by a command run
    here.
    """
    if WINDOWS or not shutil.which("ps"):
        return []
    found = subprocess.run(["ps", "-eo", "pid=,command="],
                           capture_output=True, text=True)
    stacks = []
    for line in found.stdout.splitlines():
        line = line.strip()
        pid, _, command = line.partition(" ")
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        # `dev.sh` execs `scripts/manage.py dev` with a *relative* path, which
        # is how everybody starts this — so matching an absolute path found
        # nothing and the command refused to stop the stack it had just been
        # asked about. The path is matched by its tail and the checkout is
        # confirmed from the process's working directory instead.
        if "scripts/manage.py" not in command.replace("\\", "/"):
            continue
        if not re.search(r"\bdev\b", command):
            continue
        if _working_directory(int(pid)) == ROOT:
            stacks.append((int(pid), command.strip()))
    return stacks


def _working_directory(pid: int) -> Path | None:
    """Where a process is running, so another clone is never signalled."""
    if WINDOWS or not shutil.which("lsof"):
        return None
    found = subprocess.run(["lsof", "-a", "-d", "cwd", "-Fn", "-p", str(pid)],
                           capture_output=True, text=True)
    for line in found.stdout.splitlines():
        if line.startswith("n"):
            try:
                return Path(line[1:]).resolve()
            except OSError:
                return None
    return None


def stop(api_port: int, web_port: int) -> int:
    """Stop a running stack, the way Ctrl-C would.

    `dev` already unwinds on SIGTERM and stops its three children — that is
    what `_stop_on_termination` is for — and there was no way to ask it to.
    TRY_IT tells a reader "something is already running … Stop it", and left
    them to find the process themselves; a session left running for hours
    serves whatever it compiled hours ago, which looks like the product being
    broken rather than stale.

    Only this checkout's own `dev` processes are signalled. A port held by
    something that is not ours is reported and left alone, for the reason
    `_throughline_at` gives: deciding it is safe to kill somebody else's
    server is not a decision this command gets to make.
    """
    stacks = running_stacks()
    if stacks:
        for pid, _ in stacks:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
            except PermissionError:
                print(f"  Cannot signal pid {pid} — it belongs to "
                      f"another user.", file=sys.stderr)
                return 1
        # `dev` stops its children on the way out, so wait for the ports rather
        # than for the parent: the ports are what the next start needs.
        for _ in range(60):
            time.sleep(0.25)
            if port_owner(api_port) is None and port_owner(web_port) is None:
                print(f"  Stopped ({len(stacks)} running).")
                return 0
        print("  Signalled, but a port is still held. "
              "Run doctor to see what.", file=sys.stderr)
        return 1

    held = [(label, port, port_owner(port))
            for label, port in (("API", api_port), ("Web interface", web_port))]
    holders = [(label, port, found) for label, port, found in held if found]
    if not holders:
        print("  Nothing to stop.")
        return 0

    print("  No `manage.py dev` from this directory is running, but "
          "something holds the ports:", file=sys.stderr)
    for label, port, (owner, pid) in holders:
        print(f"    Port {port} ({label}): {owner}", file=sys.stderr)
    print("  Left alone, because stopping a process this command did not "
          "start is not its decision. Stop it yourself, or start on other "
          "ports with --api-port / --web-port.", file=sys.stderr)
    return 1


def _stop_on_termination() -> None:
    """Make SIGTERM unwind like Ctrl-C does, so the children get stopped.

    Found by running it: `kill` on this process left the worker, the API and the
    web server alive and still holding both ports, because Python's default
    SIGTERM ends the interpreter without unwinding — the `finally` that stops them
    never ran. Ctrl-C worked, which is exactly why this went unnoticed. The bash
    script it replaced trapped INT *and* TERM; this is that second half.
    """
    def handler(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handler)
    if WINDOWS and hasattr(signal, "SIGBREAK"):
        # What CTRL_BREAK_EVENT arrives as, which is how a console asks a process
        # group to stop on Windows.
        signal.signal(signal.SIGBREAK, handler)


def port_owner(port: int) -> tuple[str, int | None] | None:
    """Who is holding this port, or None if it is free.

    Checked *before* anything starts, because the alternative is what actually
    happens today: uvicorn or Next fails several seconds in with an address-in-use
    traceback, the other two children are already running, and the person reading
    it has to work out which of three processes died and what is holding the
    port. Naming the process up front turns that into one line.

    Returns a description *and* the PID: the description is what a person
    reads, and the number is what the caller needs to offer `kill 35651`
    instead of leaving them to work out how to stop it. Naming a process
    without saying how to stop it is half an answer.
    """
    import socket

    # Asked by *connecting*, not by binding, and that distinction is the whole
    # check working.
    #
    # The first version bound 127.0.0.1 with SO_REUSEADDR and reported a free
    # port while a server was plainly running on it — servers commonly listen on
    # `*` (the IPv6 wildcard), and a bind to the IPv4 loopback with address reuse
    # is allowed alongside it. So the guard passed in exactly the case it exists
    # for: a previous session still holding the port. Found by running `dev`
    # against a port I knew was busy and watching it start anyway.
    #
    # A successful connection means something is accepting there, whichever
    # family and interface it bound.
    for family, address in ((socket.AF_INET, ("127.0.0.1", port)),
                            (socket.AF_INET6, ("::1", port))):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.4)
                if probe.connect_ex(address) == 0:
                    break
        except OSError:
            # No IPv6 on this machine, or the address is unreachable. Not an
            # answer about the port, so try the next one.
            continue
    else:
        return None

    # Free ports are the common case, so identifying the holder is only done on
    # the branch where somebody is waiting to be told something useful.
    if not WINDOWS and shutil.which("lsof"):
        found = subprocess.run(["lsof", "-nP", f"-i:{port}", "-sTCP:LISTEN"],
                               capture_output=True, text=True)
        for line in found.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                pid = int(parts[1]) if parts[1].isdigit() else None
                return f"{parts[0]} (pid {parts[1]})", pid
    return "another process", None


def _throughline_at(port: int) -> bool:
    """Whether the thing on this port is *our* API, rather than merely a server.

    The distinction is the whole reason this exists. `_answers` returns true for
    any 200, and something else on 8080 — a Java service, another dev server,
    someone's Jenkins — would pass it. Acting on "a server is here" would mean
    sending a researcher to somebody else's application, or worse, deciding it
    was safe to kill.

    So the payload is checked, not just the status. 503 counts: `/api/health`
    deliberately answers 503 while degraded, and a Throughline with no model is
    still a Throughline that is already running.
    """
    import json
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/health",
        headers={"User-Agent": "Throughline"})
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            body = json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        if error.code != 503:
            return False
        try:
            body = json.loads(error.read().decode())
        except Exception:
            return False
    except Exception:
        return False
    return isinstance(body, dict) and "checks" in body and "database" in (
        body.get("checks") or {})


def _first_free_port(start_at: int, tries: int = 20) -> int | None:
    """The next port nothing is listening on, or None if the range is full."""
    for candidate in range(start_at, start_at + tries):
        if port_owner(candidate) is None:
            return candidate
    return None


def _hand_tracking_ready() -> bool:
    """Whether the gesture model has been vendored.

    Reported at startup rather than discovered in the browser. The failure
    otherwise arrives as a 404 on a `.task` file at the moment somebody enables
    a camera feature, which reads as the feature being broken rather than
    un-installed.

    **Two places, because a release keeps it somewhere else.** `public/` is
    where the vendor step puts it in a checkout; `out/` is where the export
    carries it, and `out/` is what a release actually ships — `public/` is not
    in the archive at all. Looking only in `public/` therefore reported hand
    tracking as missing on every release install *while it was working*, since
    the API serves the model straight out of `out/`. A false negative rather
    than a false positive, but the same defect: a claim that does not match what
    the machine can do.
    """
    web = ROOT / "apps" / "web"
    return any((web / where / "mediapipe" / "hand_landmarker.task").exists()
               for where in ("public", "out"))


def _exported_interface() -> bool:
    """Whether this installation has an interface the API can serve itself."""
    return (ROOT / "apps" / "web" / "out" / "index.html").is_file()


def _open_when_ready(url: str, *, timeout: int = 90) -> None:
    """Open a browser once the address actually answers, in the background.

    **Waiting is the whole feature.** The port is listening well before the app
    responds — `next dev` compiles on the first request, and the API boots
    PostgreSQL and applies migrations before it serves anything. A browser
    opened the instant a socket accepts shows a connection error or a blank
    page, and the researcher concludes it is broken. So this polls for a real
    answer and only then opens.

    In a thread, because the caller has children to supervise and must not stop
    doing that to wait for a web server.

    **Not from `dev`.** A developer restarts that twenty times an hour and does
    not want twenty tabs. It is `start` — what the double-click launchers call —
    that has a person in front of it who has never opened a terminal, and for
    whom a running server they cannot see is indistinguishable from nothing
    happening at all.
    """
    if os.environ.get("THROUGHLINE_NO_BROWSER"):
        return

    def wait_then_open() -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=3) as response:
                    if response.status < 500:
                        break
            except Exception:
                pass
            time.sleep(1)
        else:
            # Say so rather than opening something that is not there. The
            # address is already on screen; a person can still use it.
            print(f"\n  (Not opening a browser: {url} did not answer within "
                  f"{timeout}s.)", flush=True)
            return
        try:
            webbrowser.open(url)
        except Exception:
            # A machine with no browser is a normal way to run this, not a
            # failure worth a traceback over.
            pass

    threading.Thread(target=wait_then_open, daemon=True,
                     name="open-browser").start()


def dev(api_port: int, web_port: int, *,
        open_browser: bool = False) -> int:
    _stop_on_termination()
    python = venv_python()
    if not python.exists():
        print(f"No virtualenv at {ROOT / '.venv'}. Run:"
              f"\n  python scripts/manage.py bootstrap", file=sys.stderr)
        return 1

    # Ports first, before a database is touched or a child is spawned. A stack
    # that half-starts and then fails on an address already in use leaves two
    # processes running and one confusing traceback.
    # **Already running is not an error.** Somebody re-running the advertised
    # one-liner wants to get to Throughline, and if it is already up the right
    # answer is to open it — not to refuse, and certainly not to kill it. The
    # check is for *our* API specifically, so a stranger's service on 8080 is
    # never mistaken for ours and never interfered with.
    if open_browser and _throughline_at(api_port):
        address = f"http://localhost:{api_port}"
        print(f"\n  Throughline is already running at {address}", flush=True)
        print("  Opening it. Nothing was started or stopped.\n", flush=True)
        # `/workspace`, not `/`: `/` is the marketing landing page, meant for
        # somebody who has not installed Throughline yet. An installed copy
        # is opened at the door of the product — sign-in, then the workspace
        # — every time, not just the first. The exported interface serves
        # this same route as `workspace.html` (see interface.py: resolve()).
        _open_when_ready(f"{address}/workspace")
        # Give the browser thread its moment; there are no children to supervise.
        time.sleep(3)
        return 0

    # Only then, ports. Something else holding 8080 is not a reason to fail on
    # the `start` path: move aside and say so. A developer running `dev` still
    # gets the refusal, because they asked for those ports on purpose.
    if open_browser:
        for name in ("api_port", "web_port"):
            wanted = api_port if name == "api_port" else web_port
            if port_owner(wanted) is None:
                continue
            moved = _first_free_port(wanted + 1)
            if moved is None:
                break
            print(f"  Port {wanted} is in use by something else; "
                  f"using {moved} instead.", flush=True)
            if name == "api_port":
                api_port = moved
            else:
                web_port = moved

    blocked, holders = False, []
    for label, port in (("API", api_port), ("web interface", web_port)):
        found = port_owner(port)
        if found:
            description, pid = found
            print(f"Port {port} ({label}) is already in use by {description}.",
                  file=sys.stderr)
            if pid:
                holders.append(pid)
            blocked = True
    if blocked:
        # **Both commands absolute, and both runnable from where the reader is
        # standing.** This used to say `./scripts/dev.sh`, which is a relative
        # path to a developer script: somebody who installed with the one-liner
        # is sitting in their home directory and has never heard of dev.sh, so
        # the advice named a file that was not there under a name they did not
        # know. Advice that cannot be followed is worse than none, because it
        # reads as the product being broken rather than the port being busy.
        if holders:
            print("\n  Most often that is an earlier Throughline that did not "
                  "shut down.\n  Stop it with:", file=sys.stderr)
            print("    kill " + " ".join(str(pid) for pid in holders),
                  file=sys.stderr)
        print("\n  Or start on ports that are free:", file=sys.stderr)
        print(f"    {venv_python()} {ROOT / 'scripts' / 'manage.py'} start"
              f" --api-port {api_port + 1} --web-port {web_port + 1}",
              file=sys.stderr)
        return 1

    # Before anything starts. Both processes would otherwise race a fresh
    # database, and the worker would find no tables to poll.
    migrated = subprocess.run(
        [str(python), "-c",
         "from throughline_domain.migrate import migrate;"
         " a=migrate(); print('migrations:', ', '.join(a) if a else 'up to date')"])
    if migrated.returncode != 0:
        return migrated.returncode

    children: list[subprocess.Popen] = []
    named: dict[int, str] = {}

    def watch(process: subprocess.Popen, what: str) -> subprocess.Popen:
        """Remember what a child was, so its death can be explained rather than
        reported as a bare exit code from something unnamed."""
        named[id(process)] = what
        return process

    try:
        children.append(watch(_spawn([str(python), "-m", "throughline_workers"]),
                              "the background worker"))
        # --reload so the API tracks edits the way the web dev server already
        # does. Without it the two halves disagree about which code is running,
        # which is a confusing way to lose an afternoon.
        children.append(watch(_spawn([
            str(python), "-m", "uvicorn", "throughline_api.app:app",
            "--host", "127.0.0.1", "--port", str(api_port), "--reload",
            "--reload-dir", str(ROOT / "apps" / "api" / "src"),
            "--reload-dir", str(ROOT / "packages")]), "the API"))

        node = _node_on_path()
        if node and _can_run_dev_server():
            print(f"\n  Throughline      http://localhost:{web_port}", flush=True)
            print(f"  API docs         http://127.0.0.1:{api_port}/docs", flush=True)
            # Said here because the alternative is finding out in the browser,
            # with a camera already switched on.
            if _hand_tracking_ready():
                print("  Hand tracking    ready (model served from this machine)", flush=True)
            else:
                print("  Hand tracking    model not installed — run:", flush=True)
                print("                     npm --prefix apps/web run vendor:hand-model", flush=True)
            print("\n  Open the address above in a browser. Use localhost, not", flush=True)
            print("  a LAN address: cameras are blocked on insecure origins.\n", flush=True)
            environment = dict(os.environ,
                               THROUGHLINE_API=f"http://127.0.0.1:{api_port}",
                               PATH=os.pathsep.join(
                                   [str(Path(node).parent), os.environ.get("PATH", "")]))
            # `node_exe`, not `shutil.which`: which() searches *this* process's
            # PATH, which is not where the chosen node necessarily lives. On a
            # machine carrying an old node on PATH and a fetched one under
            # ~/.throughline-os/runtimes, that pairs the new node with the old
            # npm — the same pick-by-position mistake `_node_on_path` fixes one
            # level up, and it surfaces as an npm error about an engine
            # constraint rather than as a version mismatch.
            children.append(watch(_spawn(
                [node_exe(node, "npm"), "run", "dev", "--",
                 "--port", str(web_port)],
                cwd=str(ROOT / "apps" / "web"), env=environment),
                "the web dev server"))
            if open_browser:
                # `next dev`'s port, not the API's: in development the browser
                # loads pages from the Next server and its rewrites proxy /api
                # back, which is what keeps the session cookie same-origin.
                # Open the product, not the landing page — see the comment
                # on the `_open_when_ready` call above.
                _open_when_ready(f"http://localhost:{web_port}/workspace")
        elif _exported_interface():
            # Since T072 the API serves the exported interface itself, so "no
            # Node" stopped meaning "no interface". This branch used to say the
            # interface was unavailable on exactly the installation where it is
            # available — §123 in reverse, claiming an absence that is not
            # there, which is as wrong as claiming a capability that is not.
            address = f"http://localhost:{api_port}"
            print(f"\n  Throughline      {address}", flush=True)
            print("  Served by the API itself — no Node process is involved.\n",
                  flush=True)
            if open_browser:
                # Open the product, not the landing page — see the comment
                # on the first `_open_when_ready` call in this function.
                _open_when_ready(f"{address}/workspace")
        else:
            # §123 — say plainly that the interface is unavailable rather than
            # pretending.
            print(f"\n  API              http://127.0.0.1:{api_port}", flush=True)
            print("  Web interface    unavailable — no interface is built here,",
                  flush=True)
            print("  and Node is not installed to build one. Either:", flush=True)
            print("    python scripts/manage.py build-interface", flush=True)

        # Exit as soon as any child does: a dead worker with a live API looks like
        # a working stack that silently never finishes anything.
        #
        # **Say which one, and what to do.** This used to return in silence, so a
        # child that failed on startup produced a wall of its own error output
        # followed by the stack tearing itself down with no explanation — the
        # researcher saw something start and stop and had nothing to act on
        # (D063, D064). A supervisor that knows why it is exiting and does not
        # say so is throwing away the one piece of information nobody else has.
        started = time.monotonic()
        while True:
            for child in children:
                if child.poll() is None:
                    continue
                what = named.get(id(child), "a component")
                code = child.returncode or 0
                print(f"\n  Stopping: {what} exited"
                      + (f" with status {code}" if code else "")
                      + ".", flush=True, file=sys.stderr)
                if time.monotonic() - started < 20:
                    # Dying within seconds is a startup failure, not a crash
                    # under load, and it is almost always the environment
                    # rather than the code.
                    print("  It stopped almost immediately, which usually means "
                          "this installation\n  is missing something rather than "
                          "that it went wrong while running.\n", flush=True,
                          file=sys.stderr)
                    print("  Check this installation:", flush=True, file=sys.stderr)
                    print(f"    {python} {ROOT / 'scripts' / 'manage.py'} doctor",
                          flush=True, file=sys.stderr)
                    print("\n  Or bring it up to date, in case this is already "
                          "fixed:", flush=True, file=sys.stderr)
                    print(f"    {python} {ROOT / 'scripts' / 'manage.py'} update\n",
                          flush=True, file=sys.stderr)
                return code
            time.sleep(0.4)
    except KeyboardInterrupt:
        return 0
    finally:
        for child in reversed(children):
            _stop(child)


# ---------------------------------------------------------------------------
# Working alongside somebody else
# ---------------------------------------------------------------------------
#
# Two people building the same repository hit two failure modes, and only one of
# them is a bug CI can see.
#
# The expensive one is invisible: both of us independently built a Dockerfile,
# both fixed the same broken package list, both edited the same route file.
# Nothing was broken — the work was simply done twice, and one copy had to be
# thrown away. No test catches that, because at no point was anything wrong.
# `sync` exists for exactly this: it looks at what everyone else's branches
# already touch before you spend a day on it.
#
# The cheap one is a push that does not build. CI catches it, but only after it
# is public and only after somebody waits. `preflight` runs the same checks
# locally first.


def _git(*args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, text=True,
                            capture_output=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout.strip()


def _changed_against_main(ref: str) -> set[str]:
    """Files a ref has touched since it left main — not since main last moved."""
    try:
        base = _git("merge-base", "origin/main", ref)
    except RuntimeError:
        return set()
    listing = _git("diff", "--name-only", f"{base}..{ref}", check=False)
    return {line for line in listing.splitlines() if line}


def sync() -> int:
    """
    Find out what everyone else is doing before writing code.

    Read-only on purpose. It fetches, reports, and changes nothing: a command
    that rebases your branch as a side effect of asking a question is one you
    stop running.
    """
    print("Fetching…")
    _git("fetch", "origin", "--prune")

    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    print(f"\nYou are on {branch}.")

    if branch != "main":
        behind = _git("rev-list", "--count", f"{branch}..origin/main")
        if behind != "0":
            print(f"  origin/main has moved {behind} commit(s) ahead of you.")
            print(f"  Catch up before you push:  git rebase origin/main")
        else:
            print("  Up to date with origin/main.")

    # Committed work *and* what is still in the working tree. Uncommitted edits
    # are exactly the ones worth warning about — they are the work you can still
    # cheaply decide not to do.
    mine = _changed_against_main("HEAD")
    mine |= {line[3:].strip().strip('"')
             for line in _git("status", "--porcelain").splitlines() if line}

    others = [line.strip() for line in
              _git("branch", "-r", "--no-merged", "origin/main").splitlines()
              if line.strip() and "origin/HEAD" not in line]

    if not others:
        print("\nNo other unmerged branches. Nobody else has work in flight.")
        return 0

    print("\nOther branches with work in flight:")
    overlapping = False
    for ref in others:
        if ref.endswith(f"/{branch}"):
            continue
        # The address, not just the name: one person committing from two
        # machines under two `user.name` values reads as two collaborators
        # otherwise, which is exactly the wrong conclusion to draw from a tool
        # whose whole job is telling you who is working on what.
        when = _git("log", "-1", "--format=%ar by %an <%ae>", ref, check=False)
        theirs = _changed_against_main(ref)
        shared = sorted(mine & theirs)
        name = ref.replace("origin/", "")
        print(f"\n  {name}  ({when})")
        for path in sorted(theirs)[:8]:
            print(f"      {path}")
        if len(theirs) > 8:
            print(f"      … and {len(theirs) - 8} more")
        if shared:
            overlapping = True
            print(f"    ⚠ You are both editing:")
            for path in shared:
                print(f"      {path}")

    if overlapping:
        print("\nOverlap is not an error — but it is where duplicated work and")
        print("merge conflicts both come from. Worth a message before continuing.")
    return 0


def _marker_applies(marker: str, python: Path) -> bool:
    """Whether a PEP 508 environment marker is true for the target interpreter.

    Evaluated by `packaging`, in the virtualenv, rather than pattern-matched
    here. `manage.py` is standard-library only because `bootstrap` runs before
    there is anything to import from — but this function only ever runs when a
    virtualenv already exists, so it can ask something that understands the
    grammar instead of guessing at `sys_platform ==` and being wrong about the
    rest.

    **Unreadable markers count as applying.** A dependency wrongly reported as
    missing costs one confusing message; one wrongly skipped is a package that
    is genuinely absent and never mentioned, which is the failure this whole
    check exists to prevent.
    """
    probe = ("import sys;"
             "from packaging.markers import Marker;"
             "print('1' if Marker(sys.argv[1]).evaluate() else '0')")
    result = subprocess.run([str(python), "-c", probe, marker],
                            capture_output=True, text=True)
    if result.returncode != 0:
        return True
    return result.stdout.strip() != "0"


def _undeclared_but_needed(python: Path) -> list[str]:
    """
    Dependencies the packages declare that the virtualenv does not have.

    Merging somebody's branch can add a dependency, and an editable install does
    not notice: the metadata was written when the package was installed, so pip
    sees nothing wrong. What the developer sees instead is a test failing on an
    unrelated-looking assertion — a `.sav` file reported as needing a runtime
    that is not installed reads like a broken merge, and the search starts in
    entirely the wrong place. Naming it costs one subprocess.

    Distribution names are normalised because `pyreadstat>=1.2`,
    `python-docx` and `Pillow` all arrive spelled differently from the module
    they install.
    """
    import tomllib

    wanted: set[str] = set()
    for package in PACKAGES:
        pyproject = ROOT / package / "pyproject.toml"
        if not pyproject.exists():
            continue
        # Parsed rather than pattern-matched. The first version read the file
        # line by line and broke on `services/workers`, which declares its
        # dependencies on a single line — every entry after the first became one
        # mangled string, and preflight reported a package missing that was
        # installed. tomllib has been in the standard library since 3.11, so
        # this costs nothing and cannot misread a valid file.
        declared = tomllib.loads(pyproject.read_text())
        project = declared.get("project", {})
        for entry in project.get("dependencies", []):
            # The marker is the half that used to be thrown away, and it was not
            # cosmetic: `pyobjc-framework-Cocoa; sys_platform == "darwin"` was
            # therefore demanded on Linux and Windows, so preflight exited 1
            # before running a single check on any machine that is not a Mac.
            # With CI unable to run since D034, that left no working pre-push
            # verification anywhere. Recorded as D039.
            requirement, _, marker = entry.partition(";")
            name = re.split(r"[<>=!~\[ ]", requirement.strip(), 1)[0].strip()
            if not name:
                continue
            if marker.strip() and not _marker_applies(marker.strip(), python):
                continue
            wanted.add(name.lower().replace("_", "-"))

    check = (
        "import importlib.metadata as m, sys;"
        "want = sys.argv[1:];"
        "have = {d.metadata['Name'].lower().replace('_','-') "
        "        for d in m.distributions() if d.metadata['Name']};"
        "print('\\n'.join(sorted(set(want) - have)))"
    )
    result = subprocess.run([str(python), "-c", check, *sorted(wanted)],
                            capture_output=True, text=True)
    return [line for line in result.stdout.split() if line]


def _other_suites() -> list[str]:
    """
    Test runs already in flight, excluding this process and its children.

    Matched on the command line rather than a lock file, because a lock file
    outlives a run that was killed and then blocks every later one until
    somebody works out what the stale file is — the fix becoming the next
    problem. `pgrep -f` needs the self-exclusion below: this process's own
    command line contains the pattern, so an unfiltered match always finds
    itself and reports a conflict that is not there. That exact mistake once
    left five waiter shells spinning for over an hour.
    """
    result = subprocess.run(
        ["pgrep", "-f", "pytest tests"], text=True, capture_output=True)
    mine = {str(os.getpid()), str(os.getppid())}
    return [pid for pid in result.stdout.split() if pid not in mine]


def _container_check() -> int:
    """Build the image and prove the API answers inside it.

    The same two commands as CI's `container` job. This is the check least
    likely to be run by hand and the one with the best catch record: both
    Dockerfiles in this repository's history were confidently wrong in ways only
    an actual build revealed. A Dockerfile that is written but never built is
    not evidence of anything.

    Skipped, loudly, when the daemon is not up. A silent skip would let this
    report success while doing nothing, which is the failure mode the whole
    preflight exists to prevent.
    """
    if not shutil.which("docker"):
        print("! No docker on PATH — skipping the image build. CI still runs it.")
        return 0
    probe = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if probe.returncode != 0:
        # Two different faults with one symptom, and they were reported as the
        # same thing. A daemon that is not running and a socket this user may
        # not open both make `docker info` exit non-zero — but "start the
        # daemon" is useless advice for the second, and following it changes
        # nothing, which reads as the instruction not working rather than as the
        # wrong instruction. Observed: `sudo service docker start` on a machine
        # whose daemon had been up for a day. Recorded as D043.
        denied = "permission denied" in (probe.stderr + probe.stdout).lower()
        print("! Skipping the image build.")
        if denied:
            print("  The daemon is running; this user may not talk to it —")
            print("  /var/run/docker.sock is owned by the `docker` group.")
            print("    sudo usermod -aG docker $USER")
            print("  then open a new shell, or run `newgrp docker` in this one.")
        else:
            print("  The docker daemon is not running.")
            print("    sudo service docker start")
        print("  CI still runs this, and it is the check that catches Dockerfile")
        print("  errors nothing else can.")
        return 0

    print("\n── the image builds")
    if subprocess.run(["docker", "build", "-t", "throughline-os:preflight", "."],
                      cwd=ROOT).returncode != 0:
        print("\nFAILED: the image did not build.", file=sys.stderr)
        return 1

    print("\n── the API answers inside the image")
    subprocess.run(["docker", "rm", "-f", "throughline-preflight"],
                   capture_output=True)
    started = subprocess.run(
        ["docker", "run", "-d", "--name", "throughline-preflight",
         "-p", "8080:8080", "throughline-os:preflight"], capture_output=True)
    if started.returncode != 0:
        print("\nFAILED: the container did not start.", file=sys.stderr)
        print(started.stderr.decode()[:400], file=sys.stderr)
        return 1

    import urllib.request
    try:
        for _ in range(60):
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:8080/api/health", timeout=3) as answer:
                    if answer.status == 200:
                        print("healthy")
                        return 0
            except Exception:
                pass
            time.sleep(5)
        print("\nFAILED: the API never became healthy.", file=sys.stderr)
        logs = subprocess.run(["docker", "logs", "throughline-preflight"],
                              capture_output=True)
        print(logs.stdout.decode()[-2000:], file=sys.stderr)
        print(logs.stderr.decode()[-2000:], file=sys.stderr)
        return 1
    finally:
        subprocess.run(["docker", "rm", "-f", "throughline-preflight"],
                       capture_output=True)


def preflight(full: bool) -> int:
    """
    Everything CI will check, before anyone else can see it fail.

    Deliberately the same checks rather than a cheaper subset: a preflight that
    passes while CI fails teaches you to ignore the preflight.

    It refuses to start while another run is in flight, and that refusal is the
    most load-bearing line in this function. The suite drives one embedded
    PostgreSQL, and several fixtures clear whole tables between tests — two runs
    at once delete each other's rows and produce failures that belong to
    neither. Observed directly: two concurrent runs of identical code reported
    11 failures and 15 failures, on different tests, while the code was in fact
    clean. Either number would have sent somebody hunting a bug that was not
    there, and a *passing* overlap would have been worse still.
    """
    python = venv_python()
    if not python.exists():
        print("No virtualenv. Run bootstrap first.", file=sys.stderr)
        return 1

    missing = _undeclared_but_needed(python)
    if missing:
        print("The virtualenv is behind what the packages declare. Missing:",
              file=sys.stderr)
        for name in missing:
            print(f"  {name}", file=sys.stderr)
        print("\nRun bootstrap. This usually means somebody added a dependency "
              "and your environment predates it — a stale venv fails as a "
              "puzzling test failure rather than as a missing package, which "
              "reads like a broken merge and sends you looking in the wrong "
              "place.", file=sys.stderr)
        return 1

    others = _other_suites()
    if others:
        print("Another test run is already using the database "
              f"(pid {', '.join(others)}).", file=sys.stderr)
        print("Two runs share one PostgreSQL and clear each other's tables, so "
              "the result would describe neither. Wait for it, or stop it.",
              file=sys.stderr)
        return 1

    Step = tuple[str, list[str], Path, dict[str, str]]
    steps: list[Step] = [
        ("everything imports",
         [str(python), "-m", "compileall", "-q", "packages", "services", "apps/api"],
         ROOT, {}),
        ("the three package lists still agree",
         [str(python), "-m", "pytest", "tests/test_packaging.py", "-q"], ROOT, {}),
    ]

    node = _node_on_path()
    if node:
        # npx is a node script: finding it is not enough, it has to be able to
        # find node itself. On a machine where node lives under ~/.local/opt and
        # is not on PATH, resolving npx and then running it fails with
        # "env: node: No such file or directory" — which reads like a missing
        # dependency rather than a missing PATH entry.
        env = dict(os.environ)
        env["PATH"] = str(Path(node).parent) + os.pathsep + env.get("PATH", "")
        steps.append(("the interface's tests pass",
                      [node_exe(node, "npx"), "vitest", "run"],
                      ROOT / "apps" / "web", env))
        # This label used to say "builds and its tests pass" while running only
        # vitest, and the missing half was not academic: `main` carried a broken
        # production build — an unclosed CSS block, and a component exported
        # from a page file, which Next forbids — and neither `next dev` nor a
        # single one of 1269 unit tests noticed. CI runs `npm run build` for
        # exactly this reason ("the build catches what unit tests cannot"), and
        # CI has been unable to run since D034. Recorded as D038.
        #
        # NEXT_DIST_DIR keeps it off `.next`: building into the directory a dev
        # server is serving from corrupts it, every page starts returning 500,
        # and the fix — `rm -rf .next` — is not one anybody guesses from the
        # error. That incident is why the setting exists at all.
        steps.append(("the interface builds for production",
                      [node_exe(node, "npm"), "run", "build"],
                      ROOT / "apps" / "web",
                      {**env, "NEXT_DIST_DIR": ".next-check",
                       "NEXT_TELEMETRY_DISABLED": "1"}))
    else:
        print("! No node found — skipping the web tests. CI will still run them.")

    if full:
        steps.append(("the full backend suite",
                      [str(python), "-m", "pytest", "tests", "-q"], ROOT, {}))

    # `next build` writes the chosen dist directory into tsconfig.json's
    # `include`, so a check build leaves a repo change nobody asked for and a
    # reference to a directory that is about to be deleted. Snapshotted rather
    # than fixed with `git checkout`, because preflight has no business running
    # git against a tree somebody may be mid-rebase on.
    tsconfig = ROOT / "apps" / "web" / "tsconfig.json"
    tsconfig_before = tsconfig.read_text() if tsconfig.exists() else None

    def tidy() -> None:
        if tsconfig_before is not None and tsconfig.read_text() != tsconfig_before:
            tsconfig.write_text(tsconfig_before)
        shutil.rmtree(ROOT / "apps" / "web" / ".next-check", ignore_errors=True)

    # D116: `main()` grants this program the installed-home allowance, because
    # `dev`, `doctor` and `backup` genuinely are the product acting on its own
    # data. A *check* is not, and it inherited the grant anyway: preflight
    # handed it to pytest, pytest handed it to the subprocess in
    # `test_a_stray_script_cannot_open_real_data.py`, and the guard that exists
    # to prove an unnamed home is refused ran with the refusal switched off. It
    # therefore failed only under `preflight` and passed under a bare `pytest`,
    # which reads as flakiness — and a security guard believed to be flaky is
    # one that gets deleted.
    def without_the_allowance(overrides: dict[str, str]) -> dict[str, str]:
        merged = {**os.environ, **overrides}
        merged.pop(ALLOW_INSTALLED_HOME, None)
        return merged

    for label, command, cwd, env in steps:
        print(f"\n── {label}")
        result = subprocess.run(command, cwd=cwd,
                                env=without_the_allowance(env or {}))
        if result.returncode != 0:
            tidy()
            print(f"\nFAILED: {label}.", file=sys.stderr)
            print("Nothing was pushed. Fix this first — it is the same check CI "
                  "runs, so pushing would only move the failure somewhere more "
                  "public.", file=sys.stderr)
            return result.returncode

    tidy()

    if full:
        # Last, because it is the slowest and everything above is a faster way
        # to learn the same bad news.
        code = _container_check()
        if code != 0:
            return code

    print("\nAll checks passed." if full else
          "\nFast checks passed. Run with --full before pushing.")
    if full:
        print("Not covered here: macOS and Windows. Those need a dispatched run —")
        print("  gh workflow run ci.yml --ref <branch>")
    return 0


def node_exe(node_binary: str, name: str) -> str:
    """
    `npx` from beside the node that was actually found.

    `_node_on_path` returns the node executable, so the sibling tool lives in its
    parent directory. Falling back to the bare name matters on a machine where
    node is on PATH but npx is installed elsewhere — the alternative is an
    absolute path to a file that is not there, which fails less clearly.
    """
    beside = Path(node_binary).parent / (f"{name}.cmd" if WINDOWS else name)
    return str(beside) if beside.exists() else name



# ---------------------------------------------------------------------------
# Checking the installation
# ---------------------------------------------------------------------------

#: The model the interface fetches, and the hash it was built against. Kept here
#: as well as in the vendoring script because this check has to be able to say
#: "present but not the file we expect", which a bare existence test cannot.
MODEL_SHA256 = "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"


def _check(name: str, ok: bool, detail: str, fix: str = "") -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail, "fix": fix}


def _check_python() -> dict[str, Any]:
    """3.12 exactly, and this is not fussiness.

    `pgserver` — the embedded PostgreSQL the whole product is built on —
    publishes no wheel past cp312. On 3.13 the install fails with a message about
    a package nobody has heard of, at the end of a long download.
    """
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    return _check(
        "Python", version == "3.12", f"{version} ({sys.executable})",
        "" if version == "3.12"
        else "The embedded PostgreSQL publishes no wheel past 3.12. Create the "
             "virtualenv with python3.12.")


def _check_venv() -> dict[str, Any]:
    python = venv_python()
    return _check("Virtualenv", python.exists(), str(python),
                  "" if python.exists() else "python scripts/manage.py bootstrap")


def _check_node() -> dict[str, Any]:
    node = _node_on_path()
    if not node:
        return _check("Node", False, "not found",
                      "Install Node 20 or newer. The API works without it; the "
                      "interface does not.")
    try:
        version = subprocess.run([node, "--version"], capture_output=True,
                                 text=True).stdout.strip()
    except OSError:
        version = "unknown"
    return _check("Node", True, f"{version} ({node})")


def _check_model() -> dict[str, Any]:
    """Present, and the file we expect.

    A truncated or half-downloaded model exists on disk and fails in the browser
    with a message about WASM, which points nowhere near the cause.
    """
    # Same two locations as `_hand_tracking_ready`, and for the same reason: a
    # release ships the model under `out/`, never `public/`.
    web = ROOT / "apps" / "web"
    path = next(
        (candidate for candidate in
         (web / "public" / "mediapipe" / "hand_landmarker.task",
          web / "out" / "mediapipe" / "hand_landmarker.task")
         if candidate.exists()), None)
    if path is None:
        # The vendor step needs an npm project, which a release does not have,
        # so the advice is only followable in a checkout. Say the right thing
        # for the installation actually in front of us.
        fix = ("npm --prefix apps/web run vendor:hand-model"
               if (web / "package.json").is_file() else
               "reinstall: this release should have shipped the model")
        return _check("Hand-tracking model", False, "not installed", fix)

    import hashlib

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != MODEL_SHA256:
        return _check(
            "Hand-tracking model", False,
            f"present but the contents do not match ({digest[:12]}…)",
            "npm --prefix apps/web run vendor:hand-model"
            if (web / "package.json").is_file() else
            "reinstall: the model that shipped is not the one expected")
    return _check("Hand-tracking model", True,
                  f"{path.stat().st_size // (1024 * 1024)}MB, hash matches")


def _answers(url: str) -> bool:
    """Whether something at this address is already serving Throughline."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return 200 <= response.status < 400
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _check_ports(api_port: int, web_port: int) -> list[dict[str, Any]]:
    """A held port is only a problem if it is somebody else holding it.

    The first version reported two failures whenever the stack was running,
    which is the normal state and the one somebody is most likely to be in when
    they run this. A check that calls the working case a failure teaches people
    to ignore it, and then it is worth nothing on the day it matters.

    So each port is asked whether *this* product is answering there. If it is,
    that is not a fault to fix, it is the thing already working.
    """
    results = []
    for label, port, probe in (
            ("API", api_port, f"http://127.0.0.1:{api_port}/api/health"),
            ("Web interface", web_port, f"http://127.0.0.1:{web_port}/")):
        found = port_owner(port)
        if found is None:
            results.append(_check(f"Port {port} ({label})", True, "free"))
            continue
        owner, holder = found
        if _answers(probe):
            results.append(_check(
                f"Port {port} ({label})", True,
                f"already serving ({owner}) — nothing to do"))
            continue
        results.append(_check(
            f"Port {port} ({label})", False, f"in use by {owner}, and not "
            f"answering as Throughline",
            (f"kill {holder}" if holder else "Stop that process")
            + f", or start with --api-port {api_port + 1} --web-port {web_port + 1}."))
    return results


def _check_database() -> dict[str, Any]:
    """Reachable, and migrated. Two different failures with two different fixes."""
    python = venv_python()
    if not python.exists():
        return _check("Database", False, "no virtualenv yet",
                      "python scripts/manage.py bootstrap")
    probe = subprocess.run(
        [str(python), "-c",
         "from throughline_domain.migrate import migrate;"
         " a = migrate();"
         " print('applied' if a else 'up to date')"],
        capture_output=True, text=True)
    if probe.returncode != 0:
        last = (probe.stderr.strip().splitlines() or ["failed"])[-1]
        return _check("Database", False, last[:160],
                      "If this mentions a vector extension, see the note in "
                      "throughline_domain/preflight.py — the container is "
                      "x86-64 only.")
    return _check("Database", True, f"migrations {probe.stdout.strip()}")


def _check_haptics() -> dict[str, Any]:
    """Reported rather than required. Most machines have nothing, and that is
    a fine answer — the interface falls back to visual confirmation."""
    python = venv_python()
    if not python.exists():
        return _check("Haptics", True, "unknown until the virtualenv exists")
    probe = subprocess.run(
        [str(python), "-c",
         "from throughline_domain import haptics;"
         " c = haptics.capability();"
         " print('available' if c['available'] else 'none on this machine')"],
        capture_output=True, text=True)
    detail = probe.stdout.strip() or "none on this machine"
    # Never a failure: no actuator is the common case, not a broken install.
    return _check("Haptics", True, detail)


def doctor(api_port: int, web_port: int) -> int:
    """Say what is wrong with this installation, in one pass.

    Written for the person testing this alone. The alternative — and what
    happened before it existed — is discovering each problem one at a time, in
    the middle of doing something else, from an error that names a symptom
    several layers away from its cause.

    Every check that fails carries the command that fixes it. A diagnosis
    without a next step is only a better-worded complaint.
    """
    checks: list[dict[str, Any]] = [
        _check_python(), _check_venv(), _check_node(), _check_model(),
        *_check_ports(api_port, web_port), _check_database(), _check_haptics(),
    ]

    print()
    for check in checks:
        mark = "ok  " if check["ok"] else "FAIL"
        print(f"  [{mark}] {check['name']:<24} {check['detail']}", flush=True)
        if check["fix"]:
            print(f"         {check['fix']}", flush=True)

    failed = [c for c in checks if not c["ok"]]
    print()
    if failed:
        print(f"  {len(failed)} of {len(checks)} checks failed.", flush=True)
        return 1

    # Say the next step, and make it the *right* next step. Telling somebody to
    # start something that is plainly already running is the kind of small
    # wrongness that makes a tool feel like it is not paying attention.
    running = any("already serving" in c["detail"] for c in checks)
    if running:
        print(f"  Everything checks out, and it is already running:", flush=True)
        print(f"    Throughline    http://localhost:{web_port}", flush=True)
        print(f"    Gesture check  http://localhost:{web_port}/gesture-check",
              flush=True)
        print(f"    Air Ink        http://localhost:{web_port}/air-ink",
              flush=True)
    else:
        print("  Everything checks out. Start it with ./scripts/dev.sh", flush=True)
    return 0


#: The escape hatch `db.py` reads. Named here so the command that *grants* it
#: and the check that *strips* it cannot drift apart — they are the same string
#: or the guard silently stops guarding.
ALLOW_INSTALLED_HOME = "THROUGHLINE_ALLOW_INSTALLED_HOME"


def main() -> int:
    # D116: this program *is* the installed product acting on purpose — `dev`
    # runs it, `doctor` inspects it, `backup` copies it — so it says which
    # database it means instead of letting the library guess. Running from a
    # checkout, `data_root()` now refuses an unnamed home, because a script
    # that forgets wrote two users and two projects into a researcher's real
    # data once already.
    #
    # `setdefault`, and THROUGHLINE_HOME still wins: `preflight` runs the
    # tests, and the tests name the temp home themselves.
    os.environ.setdefault(ALLOW_INSTALLED_HOME, "1")

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("bootstrap", help="create the virtualenv and install everything")
    go = sub.add_parser("start", help="set up if needed, then run — what the "
                                      "double-click launchers call")
    go.add_argument("--api-port", type=int, default=8080)
    go.add_argument("--web-port", type=int, default=3000)
    run = sub.add_parser("dev", help="run the API, a worker and the web interface")
    run.add_argument("--api-port", type=int, default=int(os.environ.get("PORT", 8080)))
    run.add_argument("--web-port", type=int, default=int(os.environ.get("WEB_PORT", 3000)))
    iface = sub.add_parser("build-interface",
                           help="export the web interface for the API to serve")
    iface.add_argument("--force", action="store_true",
                       help="build even with a dev server running (it will "
                            "break until you delete apps/web/.next)")
    sub.add_parser("release-key",
                   help="generate the release signing keypair, once")
    rel = sub.add_parser("release",
                         help="build the tarball, checksum and manifest a "
                              "stranger downloads")
    rel.add_argument("--into", default=None,
                     help="where to write them (default: dist/)")
    rel.add_argument("--version", default=None,
                     help="override the version name (default: git describe)")
    up = sub.add_parser("update",
                        help="update this installation, backing up first")
    up.add_argument("--check", action="store_true",
                    help="only say whether an update is available")
    halt = sub.add_parser("stop", help="stop a running stack")
    halt.add_argument("--api-port", type=int,
                      default=int(os.environ.get("PORT", 8080)))
    halt.add_argument("--web-port", type=int,
                      default=int(os.environ.get("WEB_PORT", 3000)))

    sub.add_parser("desktop-entry",
                   help="add Throughline to the Linux applications menu")
    sub.add_parser("sync", help="fetch, and show what everyone else is working on")
    doc = sub.add_parser("doctor", help="check this installation and say what is wrong")
    doc.add_argument("--api-port", type=int, default=int(os.environ.get("PORT", 8080)))
    doc.add_argument("--web-port", type=int,
                     default=int(os.environ.get("WEB_PORT", 3000)))
    check = sub.add_parser("preflight", help="run CI's checks before pushing")
    check.add_argument("--full", action="store_true",
                       help="include the whole backend suite (about three minutes)")
    args = parser.parse_args()

    if args.command == "bootstrap":
        return bootstrap()
    if args.command == "sync":
        return sync()
    if args.command == "stop":
        return stop(args.api_port, args.web_port)
    if args.command == "doctor":
        return doctor(args.api_port, args.web_port)
    if args.command == "preflight":
        return preflight(args.full)
    if args.command == "start":
        return start(args.api_port, args.web_port)
    if args.command == "build-interface":
        return build_interface(force=args.force)
    if args.command == "release-key":
        return release_key()
    if args.command == "release":
        return release(args.into, args.version)
    if args.command == "update":
        return update(args.check)
    if args.command == "desktop-entry":
        return desktop_entry()
    return dev(args.api_port, args.web_port)


if __name__ == "__main__":
    raise SystemExit(main())
