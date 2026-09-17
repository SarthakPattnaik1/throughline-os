"""Whether something newer exists. Asked, never assumed, and never automatic.

`version.py` answers "what am I?" without touching the network. This answers
"is there anything newer?", which cannot be answered offline — so it is a
separate module with a separate failure mode, and being unable to reach the
remote is reported as *not knowing* rather than as being up to date.

That distinction is the whole reason this file is not three lines. "No update
available" and "I could not ask" look identical to a researcher on a train, and
only one of them means what the screen says.

**Checking mutates nothing the product runs from.** `git fetch` writes
remote-tracking refs inside `.git` and touches neither the working tree nor the
installed packages, so a check is safe to run from a button. Applying an update
is a different operation entirely and lives in `manage.py`, because it replaces
the code the API process is executing and therefore requires a restart — a
running server cannot swap itself out from underneath a request.

**The channel is tags when tags exist, and `main` until they do.** That is the
progression `T073` describes rather than two separate mechanisms: during the
build period an installation follows the branch, and the day somebody runs
`git tag beta-4` it starts following releases without anything being rewritten.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from . import signing, version

#: Where a released copy asks what the newest release is.
#:
#: Overridable so a fork, a staging bucket or a test can point elsewhere. The
#: landing page names the same host in `frontend/assemble.mjs`, and the two are
#: held together by `test_the_page_and_the_updater_name_the_same_host` rather
#: than by anybody remembering: if they drift, downloads keep working while
#: every installed copy checks a host that publishes nothing (D050).
RELEASE_URL = "https://throughline-research.pages.dev/latest.json"

#: Cloudflare answers Python's default `Python-urllib/3.x` agent with **403**,
#: so a release host behind it refuses this while `curl` of the same URL
#: succeeds. Measured against the live host (D056). Naming ourselves is also
#: the honest thing: the server's log should say who is asking.
USER_AGENT = "Throughline (+https://throughline-research.pages.dev)"

#: How long a check may take before it is abandoned. A button that hangs is
#: worse than one that says it could not reach the network: the researcher is
#: left unable to tell a slow answer from no answer.
TIMEOUT = 30


def _git(root: Path, *args: str, timeout: int = TIMEOUT) -> tuple[int, str, str]:
    try:
        result = subprocess.run(("git", "-C", str(root)) + args,
                                capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "", f"git {' '.join(args)} took longer than {timeout}s"
    except OSError:
        return 127, "", "git could not be started"
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _newest_tag(root: Path) -> str | None:
    """The newest tag the remote has, by version order rather than by date.

    `--sort=-v:refname` so `beta-10` sorts above `beta-9`, which lexical order
    gets wrong and which is exactly the kind of thing nobody notices until the
    tenth release.
    """
    code, out, _ = _git(root, "ls-remote", "--tags", "--refs",
                        "--sort=-v:refname", "origin")
    if code != 0 or not out:
        return None
    first = out.splitlines()[0]
    match = re.search(r"refs/tags/(.+)$", first)
    return match.group(1) if match else None


def _fetch_manifest(url: str) -> dict[str, Any]:
    import json
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode())


def check_releases(root: Path, here: dict[str, Any], *,
                   url: str | None = None) -> dict[str, Any]:
    """Ask the release server what is newest, and refuse to believe it lightly.

    This is the path a downloaded copy takes: no `.git`, so no `git fetch`. The
    manifest is fetched over HTTPS and then **verified against the public key
    this installation shipped with** — which is the whole reason T083 signs it.
    HTTPS alone authenticates the server; the signature authenticates whoever
    made the release, and those become different facts the moment the server is
    compromised.

    **A manifest that does not verify is not an update, and not "up to date"
    either.** It is reported as a refusal with its reason, because the three
    states — newer available, current, could not establish — are genuinely
    different and collapsing any two of them tells the researcher something
    false.
    """
    import urllib.error

    where = url or os.environ.get("THROUGHLINE_RELEASE_URL") or RELEASE_URL
    key = signing.public_key(root)
    if not key:
        return {"checked": False, "current": here,
                "reason": "This installation ships no release public key, so a "
                          "manifest could not be verified even if one arrived. "
                          "Refusing to check rather than trusting the server."}

    try:
        manifest = _fetch_manifest(where)
    except (urllib.error.URLError, OSError, ValueError):
        return {"checked": False, "current": here,
                "reason": "Could not reach or read the release server."}

    try:
        signing.verify(manifest, key)
    except signing.VerificationError:
        return {"checked": False, "current": here,
                "reason": "The release manifest did not verify."}

    newest = manifest.get("version")
    if not newest:
        return {"checked": False, "current": here,
                "reason": "The manifest names no version."}

    return {
        "checked": True,
        "current": here,
        "channel": "releases",
        "following": "signed releases",
        # A string comparison, deliberately: a released copy knows the version
        # it is and the version on offer, and "different" is the honest answer.
        # Ordering release names is a guess this has no business making — a
        # rollback published deliberately is still an update to apply.
        "update_available": newest != here.get("version"),
        "available": newest,
        "manifest": manifest,
        "behind": 1 if newest != here.get("version") else 0,
        "ahead": 0,
        "how": ("python scripts/manage.py update"
                if newest != here.get("version") else None),
    }


def check(root: Path | None = None) -> dict[str, Any]:
    """Ask the remote what it has, and say plainly when the question failed."""
    root = root or version._repository_root()
    here = version.current()

    if not (root / ".git").exists():
        # A released copy: no git, so ask the release server instead. This used
        # to be a flat refusal pointing at a mechanism that did not exist.
        return check_releases(root, here)

    code, _, error = _git(root, "fetch", "--quiet", "origin")
    if code != 0:
        # Not knowing is its own answer, and it is not "up to date".
        return {"checked": False, "current": here,
                "reason": "Could not reach the configured update remote."}

    tag = _newest_tag(root)
    channel = tag or "main"
    target = f"refs/tags/{tag}" if tag else "origin/main"

    code, behind, error = _git(root, "rev-list", "--count", f"HEAD..{target}")
    if code != 0:
        return {"checked": False, "current": here,
                "reason": f"Could not compare this installation against {channel}."}

    code, ahead, _ = _git(root, "rev-list", "--count", f"{target}..HEAD")
    count = int(behind or 0)

    return {
        "checked": True,
        "current": here,
        "channel": channel,
        "following": "a release tag" if tag else "the main branch",
        "behind": count,
        # Reported because it explains an otherwise baffling "no update" on a
        # machine somebody has been committing on.
        "ahead": int(ahead or 0) if code == 0 else 0,
        "update_available": count > 0,
        # The command rather than a button that does it: applying replaces the
        # code this process is running from, so it cannot be done from inside
        # the process. See `manage.py update`.
        "how": ("python scripts/manage.py update" if count > 0 else None),
    }
