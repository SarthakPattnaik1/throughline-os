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
import os
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
REQUIRED_PYTHON = (3, 12)

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
    version = sys.version_info[:2]
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
    if existing is not None and existing != REQUIRED_PYTHON:
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
            print("\nCould not create a virtualenv with pip in it.", file=sys.stderr)
            print("  If the output above mentioned ensurepip, the venv module is"
                  "\n  packaged separately on this distribution:"
                  "\n    Debian/Ubuntu:  sudo apt install python3.12-venv"
                  "\n    Fedora/RHEL:    sudo dnf install python3-virtualenv"
                  "\n\n  Then run this again.", file=sys.stderr)
            return 1

    python = str(venv_python())
    subprocess.run([python, "-m", "pip", "install", "-q", "--upgrade", "pip"], check=True)
    for package in PACKAGES:
        print(f"  installing {package}")
        result = subprocess.run(
            [python, "-m", "pip", "install", "-q", "-e", str(ROOT / package)])
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
                    "pytest", "httpx", "xlwt"], check=True)

    print("Applying migrations (this boots the bundled PostgreSQL on first run)…")
    subprocess.run([python, "-c",
                    "from throughline_domain.migrate import migrate;"
                    " print('applied:', migrate() or 'nothing new')"], check=True)

    _ensure_node_for_the_interface()
    print("\nReady. Start the stack with:  python scripts/manage.py dev")
    return 0


def _ensure_node_for_the_interface(log=print) -> str | None:
    """Make sure something on this machine can run the interface.

    Node is a *runtime* dependency here, not just a build one: `serve.sh` starts
    `next start`, and without it the API and the worker come up and the researcher
    gets a warning line instead of a product. Shipping Python and not Node would
    have produced an install that starts and cannot be used, which is a worse
    outcome than the one this task set out to fix.

    Three deliberate choices. **Skipped when an adequate node is already here** —
    a 200 MB download to duplicate a working tool is not a kindness. **Not
    fatal**: the API is genuinely useful headless, `serve.sh` already degrades
    honestly, and a failed optional download should not throw away a
    just-completed database migration. **Opt-out via THROUGHLINE_SKIP_NODE**,
    for the container, which installs Node its own way, and for anyone
    deliberately running headless.
    """
    if os.environ.get("THROUGHLINE_SKIP_NODE"):
        log("  skipping Node (THROUGHLINE_SKIP_NODE is set) — API only.")
        return None

    existing = _node_on_path()
    if existing is not None:
        major = _node_major(existing)
        if major is not None and major >= NODE_MINIMUM:
            log(f"  Node {major} already here ({existing}) — not fetching one.")
            return existing

    try:
        node = runtimes.ensure("node", log=log)
    except runtimes.RuntimeError_ as error:
        log(f"\n  Could not fetch Node: {error}")
        log("  The API and worker will run; the web interface will not.")
        return None
    log(f"  Node ready at {node}")
    return str(node)


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


def port_owner(port: int) -> str | None:
    """Who is holding this port, or None if it is free.

    Checked *before* anything starts, because the alternative is what actually
    happens today: uvicorn or Next fails several seconds in with an address-in-use
    traceback, the other two children are already running, and the person reading
    it has to work out which of three processes died and what is holding the
    port. Naming the process up front turns that into one line.

    Returns a description rather than a PID alone — "node (pid 35651)" is
    something you can act on; a number is something you have to look up.
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
                return f"{parts[0]} (pid {parts[1]})"
    return "another process"


def _hand_tracking_ready() -> bool:
    """Whether the gesture model has been vendored.

    Reported at startup rather than discovered in the browser. The failure
    otherwise arrives as a 404 on a `.task` file at the moment somebody enables
    a camera feature, which reads as the feature being broken rather than
    un-installed.
    """
    return (ROOT / "apps" / "web" / "public" / "mediapipe"
            / "hand_landmarker.task").exists()


def dev(api_port: int, web_port: int) -> int:
    _stop_on_termination()
    python = venv_python()
    if not python.exists():
        print(f"No virtualenv at {ROOT / '.venv'}. Run:"
              f"\n  python scripts/manage.py bootstrap", file=sys.stderr)
        return 1

    # Ports first, before a database is touched or a child is spawned. A stack
    # that half-starts and then fails on an address already in use leaves two
    # processes running and one confusing traceback.
    blocked = False
    for label, port in (("API", api_port), ("web interface", web_port)):
        owner = port_owner(port)
        if owner:
            print(f"Port {port} ({label}) is already in use by {owner}.",
                  file=sys.stderr)
            blocked = True
    if blocked:
        print("\nEither stop that process, or choose other ports:"
              "\n  PORT=8081 WEB_PORT=3001 ./scripts/dev.sh", file=sys.stderr)
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
    try:
        children.append(_spawn([str(python), "-m", "throughline_workers"]))
        # --reload so the API tracks edits the way the web dev server already
        # does. Without it the two halves disagree about which code is running,
        # which is a confusing way to lose an afternoon.
        children.append(_spawn([
            str(python), "-m", "uvicorn", "throughline_api.app:app",
            "--host", "127.0.0.1", "--port", str(api_port), "--reload",
            "--reload-dir", str(ROOT / "apps" / "api" / "src"),
            "--reload-dir", str(ROOT / "packages")]))

        node = _node_on_path()
        if node:
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
            children.append(_spawn(
                [shutil.which("npm") or "npm", "run", "dev", "--",
                 "--port", str(web_port)],
                cwd=str(ROOT / "apps" / "web"), env=environment))
        else:
            # §123 — say plainly that the interface is unavailable rather than
            # pretending.
            print(f"\n  API              http://127.0.0.1:{api_port}", flush=True)
            print("  Web interface    unavailable — Node 20+ is not installed.\n", flush=True)

        # Exit as soon as any child does: a dead worker with a live API looks like
        # a working stack that silently never finishes anything.
        while True:
            for child in children:
                if child.poll() is not None:
                    return child.returncode or 0
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
            name = re.split(r"[<>=!~\[; ]", entry, 1)[0].strip()
            if name:
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
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        print("! The docker daemon is not running — skipping the image build.")
        print("  Start it with:  sudo service docker start")
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
        steps.append(("the interface builds and its tests pass",
                      [node_exe(node, "npx"), "vitest", "run"],
                      ROOT / "apps" / "web", env))
    else:
        print("! No node found — skipping the web tests. CI will still run them.")

    if full:
        steps.append(("the full backend suite",
                      [str(python), "-m", "pytest", "tests", "-q"], ROOT, {}))

    for label, command, cwd, env in steps:
        print(f"\n── {label}")
        result = subprocess.run(command, cwd=cwd, env=env or None)
        if result.returncode != 0:
            print(f"\nFAILED: {label}.", file=sys.stderr)
            print("Nothing was pushed. Fix this first — it is the same check CI "
                  "runs, so pushing would only move the failure somewhere more "
                  "public.", file=sys.stderr)
            return result.returncode

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
    path = ROOT / "apps" / "web" / "public" / "mediapipe" / "hand_landmarker.task"
    if not path.exists():
        return _check("Hand-tracking model", False, "not installed",
                      "npm --prefix apps/web run vendor:hand-model")

    import hashlib

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != MODEL_SHA256:
        return _check(
            "Hand-tracking model", False,
            f"present but the contents do not match ({digest[:12]}…)",
            "npm --prefix apps/web run vendor:hand-model")
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
        owner = port_owner(port)
        if owner is None:
            results.append(_check(f"Port {port} ({label})", True, "free"))
            continue
        if _answers(probe):
            results.append(_check(
                f"Port {port} ({label})", True,
                f"already serving ({owner}) — nothing to do"))
            continue
        results.append(_check(
            f"Port {port} ({label})", False, f"in use by {owner}, and not "
            f"answering as Throughline",
            "Stop that process, or run with PORT= and WEB_PORT= set to other "
            "ports."))
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("bootstrap", help="create the virtualenv and install everything")
    run = sub.add_parser("dev", help="run the API, a worker and the web interface")
    run.add_argument("--api-port", type=int, default=int(os.environ.get("PORT", 8080)))
    run.add_argument("--web-port", type=int, default=int(os.environ.get("WEB_PORT", 3000)))
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
    if args.command == "doctor":
        return doctor(args.api_port, args.web_port)
    if args.command == "preflight":
        return preflight(args.full)
    return dev(args.api_port, args.web_port)


if __name__ == "__main__":
    raise SystemExit(main())
