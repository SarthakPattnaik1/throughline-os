"""
Every route that reaches a project checks who owns it.

Eleven did not, and the failures were not subtle once anybody looked: a second
account could read another researcher's board, their reports, their excerpts
and the marks on their papers, render somebody else's report to a file, re-cut
it as a talk inside their project, and approve a term into their vocabulary.

**The one that is worth remembering guarded itself against itself.** `POST
/artifacts/{id}/presentation` called a domain function that refuses a report
belonging to a different project — and handed it the report's own project_id,
so the comparison was against itself and could never fail. Reading the code, it
looked checked.

So this file does not assert that a route *calls* anything. It asserts that
every route either scopes by one of the known means, or is written down here as
not needing to. The behavioural proof lives in
`tests/test_project_isolation.py`, which asks from a second account and reads
the status code — because `DELETE /api/projects/{id}` scopes correctly without
calling any of these, by filtering on `owner_user_id` in its own query, and a
scan alone would call it a hole.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

API = pathlib.Path(__file__).resolve().parents[1] / "apps" / "api" / "src" / "throughline_api"

#: The ways a handler can establish that the caller owns what they are touching.
SCOPERS = {"scoped_project", "_scoped", "_owning_project"}

#: Routes that reach no project, and why.
#:
#: Each of these is either about the machine rather than a project, or is the
#: act of becoming someone who has projects at all. A route that appears here
#: wrongly is how the next leak gets in, so the reason has to say what it
#: touches instead.
NO_PROJECT = {
    "GET /health": "Liveness. Answers before anything is signed in.",
    "GET /api/health": "The same, for the container's healthcheck.",
    "GET /api/auth/status": "Whether this installation has any account yet.",
    "POST /api/auth/setup": "Creates the first account; nobody exists to own anything.",
    "POST /api/auth/register": "Creates an account on this machine.",
    "POST /api/auth/login": "Becomes an account; touches sessions only.",
    "POST /api/auth/logout": "Ends the caller's own session.",
    "GET /api/auth/accounts": "The accounts on this machine, not a project.",
    "POST /api/auth/accounts": "Adds an account to this machine.",
    "POST /api/auth/password": "Changes the caller's own password.",
    "GET /api/projects": "The caller's own projects, filtered by owner.",
    "POST /api/projects": "Creates a project; there is none to own yet.",
    "POST /api/projects/example": "Seeds the worked example into a new project.",
    "DELETE /api/projects/{project_id}":
        "Scoped without calling these: it selects on `owner_user_id` and finds "
        "nothing for anybody else. Proved from a second account in "
        "`test_project_isolation.py`.",
    "GET /api/haptics": "Whether this machine has haptics at all.",
    "POST /api/speech/transcribe":
        "Audio in, words out. It reads no project and stores nothing, and is "
        "the one route with no session at all — a documented choice on the "
        "grounds that the API binds to localhost. Worth re-reading if this "
        "ever binds to anything else, because the product does support more "
        "than one account.",
    "POST /api/haptics/tap": "Drives this machine's haptics, nothing stored.",
    "GET /api/literature/sources": "Which external catalogues can be searched.",
    "POST /api/literature/search": "Searches external catalogues; no project data leaves.",
    "POST /api/literature/pdf": "Fetches a PDF from a URL the caller supplies.",
    "GET /api/datasets/repositories": "Which external data repositories exist.",
    "POST /api/datasets/search": "Searches external data repositories.",
    "GET /api/system/models": "The model settings of this installation.",
    "PUT /api/system/models": "Changes this installation's model settings.",
    "PUT /api/system/model-key": "Stores a model key for this installation.",
    "DELETE /api/system/model-key": "Removes this installation's model key.",
    "GET /api/system/capabilities": "What this machine can do.",
    "GET /api/system/launchers": "Desktop launchers installed on this machine.",
    "POST /api/system/launchers/desktop-entry": "Writes a desktop launcher file.",
    "GET /api/system/version": "The version this installation is running.",
    "POST /api/system/version/check": "Asks whether a newer one exists.",
    "GET /api/system/packs/{name}": "Whether an optional feature pack is present.",
    "POST /api/system/packs/{name}/install": "Installs an optional feature pack.",
    "GET /api/system/registration": "Whether this installation accepts sign-ups from the network.",
    "PUT /api/system/registration": "Opens or closes sign-up from the network (administrator only).",
}


def _handlers():
    """Every route handler, with the routes it serves."""
    for path in (API / "app.py", API / "interpretation.py"):
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            routes = [
                f"{d.func.attr.upper()} {d.args[0].value}"
                for d in node.decorator_list
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                and d.func.attr in ("get", "post", "put", "patch", "delete")
                and d.args and isinstance(d.args[0], ast.Constant)
            ]
            if routes:
                yield node, routes


def scoped_routes() -> tuple[set[str], set[str]]:
    """Which routes scope, and which do not."""
    scoped: set[str] = set()
    unscoped: set[str] = set()
    for node, routes in _handlers():
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        (scoped if names & SCOPERS else unscoped).update(routes)
    return scoped, unscoped


def audit(scoped: set[str], unscoped: set[str],
          excused: dict[str, str]) -> dict[str, list[str]]:
    """
    Compare the three. Pure, so it can be run against a broken API rather than
    only against this one.

    Against the real app all three lists are empty, which is the point and also
    the problem: deleting any of the checks below would change no result.
    Mutation testing found exactly that, twice — the reachability guard had the
    same hole and was fixed the same way.
    """
    return {
        "unexplained": sorted(unscoped - set(excused)),
        "stale": sorted(set(excused) & scoped),
        "gone": sorted(set(excused) - scoped - unscoped),
    }


def test_audit_names_a_route_that_scopes_nothing():
    found = audit({"GET /a"}, {"POST /b"}, {})
    assert found["unexplained"] == ["POST /b"]


def test_audit_accepts_a_route_that_is_written_down():
    found = audit(set(), {"POST /b"}, {"POST /b": "reaches no project"})
    assert found["unexplained"] == []


def test_audit_names_an_excuse_for_a_route_that_now_scopes():
    found = audit({"POST /b"}, set(), {"POST /b": "reaches no project"})
    assert found["stale"] == ["POST /b"]


def test_audit_names_an_excuse_for_a_route_that_is_gone():
    found = audit(set(), set(), {"POST /gone": "reaches no project"})
    assert found["gone"] == ["POST /gone"]


def test_the_scan_finds_the_routes():
    """
    A parser that matches nothing calls every route scoped and passes for ever.
    """
    scoped, unscoped = scoped_routes()
    assert len(scoped) > 90, f"only {len(scoped)} scoped routes found"
    assert "GET /api/projects/{project_id}/board" in scoped
    assert "POST /api/artifacts/{artifact_id}/presentation" in scoped


def test_every_route_scopes_or_says_why_not():
    scoped, unscoped = scoped_routes()
    unexplained = audit(scoped, unscoped, NO_PROJECT)["unexplained"]
    assert not unexplained, (
        "these routes never establish who owns what they touch:\n  "
        + "\n  ".join(unexplained)
        + "\n\nEither scope them, or record here what they reach instead of a "
          "project."
    )


def test_nothing_is_excused_that_now_scopes():
    """
    The direction that rots. A route that gains a check leaves an entry saying
    it reaches no project, and the next reader believes it.
    """
    scoped, unscoped = scoped_routes()
    stale = audit(scoped, unscoped, NO_PROJECT)["stale"]
    assert not stale, (
        "these scope now, so remove their entries:\n  " + "\n  ".join(stale))


def test_nothing_is_excused_that_no_longer_exists():
    scoped, unscoped = scoped_routes()
    gone = audit(scoped, unscoped, NO_PROJECT)["gone"]
    assert not gone, (
        "these entries name routes that are gone:\n  " + "\n  ".join(gone))


@pytest.mark.parametrize("route", sorted(NO_PROJECT))
def test_every_excuse_says_what_the_route_reaches(route):
    assert len(NO_PROJECT[route].strip()) > 20
