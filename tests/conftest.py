from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest

# Each test session gets its own embedded PostgreSQL data directory so a test
# run can never touch a researcher's real project database.
#
# **This suite must run serially, and nothing else says so.** One database is
# shared by every test, and twenty test files tear down with an unscoped
# `DELETE FROM users`; `sessions.user_id` is `ON DELETE CASCADE`, so that
# statement signs out every session in the database, not just the file's own.
# Serially that is correct — each test creates the account it needs. Under
# `pytest -n auto` the files would delete each other's users mid-request, and
# the symptom is a 401 arriving where a list was expected, in whichever test
# happened to be running.
#
# Written here because the obvious response to a seven-minute backend run is to
# reach for `xdist`, and the cost of learning this by doing it is an afternoon.
# `xdist` is not installed, which is the only thing currently preventing it.
_TEST_HOME = Path(
    os.environ.get(
        "THROUGHLINE_TEST_HOME",
        str(Path(tempfile.gettempdir()) / "throughline-os-tests"),
    )
)
os.environ.setdefault("THROUGHLINE_HOME", str(_TEST_HOME))


def _discard_a_half_initialised_cluster() -> None:
    """Recover from a `pgdata` that macOS emptied out from under us.

    The test database lives in the OS temp directory, which macOS purges
    periodically — and it does not purge it atomically. What survives is the data
    *subdirectories* (`base`, `global`, `pg_wal`) with every top-level file gone,
    including `PG_VERSION` and the configuration. No server can start from that,
    and `initdb` refuses to touch it because the directory is not empty.

    Left alone this presents as a thousand identical errors whose traceback ends
    inside `initdb`, which says nothing about the actual cause and cost an
    afternoon the first time. A directory with no `PG_VERSION` is not a database
    by PostgreSQL's own definition, so it is moved aside rather than deleted —
    reversible, and it keeps the evidence if the cause was ever something else.
    """
    pgdata = _TEST_HOME / "pgdata"
    if not pgdata.exists() or (pgdata / "PG_VERSION").exists():
        return
    ruined = pgdata.with_name(f"pgdata.unusable-{int(time.time())}")
    pgdata.rename(ruined)
    print(
        f"\nThe test database at {pgdata} had no PG_VERSION — the temp directory "
        f"was purged mid-flight. Moved it to {ruined.name} and starting fresh; "
        f"nothing of yours was in it.",
    )


def _empty_every_table() -> None:
    """
    Start each session from an empty database, whatever the last one left.

    Most tests run inside a transaction that is rolled back, but the ones that
    exercise HTTP go through `TestClient` and commit real rows — and they tidy
    up in fixtures that only run when a session ends gracefully. A run killed
    part-way therefore leaves accounts, projects and analyses behind, and the
    next run inherits them.

    That is not hypothetical. A run interrupted mid-suite left rows that failed
    six tests in the file that happened to sign in as the same address, and the
    failures pointed at the tests rather than at the wreckage — the usual shape
    of contaminated state.

    **It refuses to run anywhere but the test database.** This truncates every
    table there is, and the one mistake it must never make is doing that to a
    researcher's project. `THROUGHLINE_HOME` is set to the temp directory at
    import, above; if anything has changed it since, that is exactly the case
    where deleting nothing and saying so is the only safe answer.
    """
    home = os.environ.get("THROUGHLINE_HOME", "")
    if Path(home) != _TEST_HOME:
        raise RuntimeError(
            f"The tests are pointed at {home!r}, not the test database at "
            f"{_TEST_HOME}. Refusing to empty it: this clears every table, and "
            f"a real project directory is not something to guess about.")

    from throughline_domain.db import transaction

    with transaction() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename <> 'schema_migrations'")
        tables = [row["tablename"] for row in cur.fetchall()]
        if not tables:
            return
        # One statement, so foreign keys never see a half-emptied database.
        # `schema_migrations` is kept: emptying it would re-run every migration
        # against a schema that already has them.
        joined = ", ".join(f'"{name}"' for name in tables)
        cur.execute(f"TRUNCATE {joined} RESTART IDENTITY CASCADE")


@pytest.fixture(scope="session", autouse=True)
def database() -> None:
    from throughline_domain.migrate import migrate

    _discard_a_half_initialised_cluster()
    migrate()
    _empty_every_table()
    yield
    from throughline_domain.db import shutdown

    shutdown()


@pytest.fixture()
def cur():
    """A cursor in a transaction that is always rolled back.

    Tests therefore share one migrated database without sharing state, and a
    failing test cannot leave rows behind for the next one.
    """
    from throughline_domain.db import connection

    with connection() as conn:
        with conn.cursor() as cursor:
            yield cursor
        conn.rollback()


@pytest.fixture()
def empty_queue():
    """A workflow queue holding nothing but what the test puts in it.

    `claim_next` and `Worker.run_once` take the next claimable run in the whole
    table, not the next one belonging to the caller. Several tests assert that
    what came back is the run they just enqueued — which is only true while
    nothing else is queued, and nothing made that true. It held by accident of
    declaration order: run the same files in a different order and a run
    committed by an earlier file is claimed instead, so
    `claimed["id"] == run_id` fails on a worker that is behaving correctly.

    Found by running the suite with the file order shuffled: one seed failed
    `test_worker_completes_a_queued_run`, another failed four tests in
    `test_workflow.py`, and the default order passes every time.

    Committed on its own connection, because the tests that need it most run a
    real `Worker` in a separate connection that cannot see an open
    transaction's deletes. This is the test database — `_empty_every_table`
    above refuses to run anywhere else — and emptying a queue between tests is
    what a session-scoped clean start already does once.
    """
    from throughline_domain.db import connection

    with connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM workflow_runs")
    yield


@pytest.fixture()
def project(cur) -> str:
    from throughline_domain.ids import new_id

    user_id = new_id("usr")
    cur.execute(
        "INSERT INTO users(id, email, display_name, password_hash, password_salt) "
        "VALUES (%s, %s, %s, %s, %s)",
        (user_id, f"{user_id}@test.local", "Test Researcher", "x", "y"),
    )
    project_id = new_id("prj")
    cur.execute(
        "INSERT INTO projects(id, owner_user_id, name, research_question) "
        "VALUES (%s, %s, %s, %s)",
        (project_id, user_id, "Test project", "Does X associate with Y?"),
    )
    return project_id

# ---------------------------------------------------------------------------
# A real paper, built rather than found
# ---------------------------------------------------------------------------
#
# The three tests that need a PDF used to glob one out of a directory under
# /Users/sarthakpattnaik/Downloads. On any other machine that glob is empty, the
# tests skipped themselves, and a skip reads as green — so the one test covering
# the whole §137 workflow had never run anywhere but its author's laptop, and the
# gaps it would have caught shipped.
#
# Generated, not committed: a checked-in binary cannot be reviewed in a diff, and
# the properties these tests actually depend on — headed sections, a known
# searchable phrase, several pages, resolvable anchors — are clearer as code than
# as bytes. PyMuPDF is already a dependency of throughline-ingestion, so this
# costs nothing new.

_PAPER_PAGES = (
    "Antibiotic Consumption and Antimicrobial Resistance:\n"
    "A Cross-National Analysis\n\n"
    "A. Researcher, B. Colleague\n"
    "Department of Global Health\n\n"
    "ABSTRACT\n\n"
    "Antimicrobial resistance is among the most consequential threats to modern\n"
    "medicine. We examine whether national antibiotic consumption, measured in\n"
    "defined daily doses, is associated with the prevalence of resistant isolates\n"
    "across 120 country-years. We find a strong positive association between\n"
    "consumption and resistance. Gross domestic product per capita is frequently\n"
    "proposed as a confounder of this relationship; we test that proposal\n"
    "directly and do not find that it explains the association.\n",

    "1. INTRODUCTION\n\n"
    "Resistance to antibiotics arises through selection pressure. Where more\n"
    "antibiotic is consumed, resistant organisms enjoy a larger advantage. The\n"
    "mechanism is not in doubt at the level of a single organism, but its\n"
    "magnitude at the level of a national health system is contested.\n\n"
    "Three objections recur in the literature. The first is surveillance bias:\n"
    "wealthier countries detect more resistance because they look harder. The\n"
    "second is that consumption and resistance are both downstream of economic\n"
    "development, so the association is spurious. The third is that resistance\n"
    "prevalence is measured inconsistently between reporting systems.\n\n"
    "2. DATA\n\n"
    "We assembled a panel of 120 country-year observations recording antibiotic\n"
    "consumption in defined daily doses per 1000 inhabitants per day, the\n"
    "percentage of tested isolates resistant to a first-line agent, and gross\n"
    "domestic product per capita in constant dollars.\n",

    "3. METHODS\n\n"
    "We regress resistance on consumption and report the ordinary least squares\n"
    "slope with heteroskedasticity-consistent standard errors. To test the\n"
    "economic-development objection we refit the model with gross domestic\n"
    "product per capita entered as a covariate and compare the consumption\n"
    "coefficient before and after adjustment. A confounder that explained the\n"
    "association would move that coefficient substantially toward zero.\n\n"
    "4. RESULTS\n\n"
    "Consumption is positively associated with resistance. The association\n"
    "survives adjustment for gross domestic product per capita: the consumption\n"
    "coefficient is essentially unchanged, and the coefficient on gross domestic\n"
    "product is not distinguishable from zero.\n\n"
    "5. LIMITATIONS\n\n"
    "The panel is observational. Surveillance intensity is not measured and may\n"
    "still bias the resistance estimate upward in high-income countries.\n",
)

# A phrase that appears in the body exactly once, for search assertions. Kept
# beside the text so a later edit to the paper cannot silently orphan it.
PAPER_SEARCH_TERM = "surveillance bias"


def build_paper_pdf(path: Path) -> Path:
    """Write a small multi-page paper with extractable, sectioned text."""
    import fitz  # PyMuPDF, via throughline-ingestion

    document = fitz.open()
    for text in _PAPER_PAGES:
        page = document.new_page()
        page.insert_textbox(fitz.Rect(60, 60, 535, 780), text,
                            fontsize=10.5, fontname="helv")
    document.save(path)
    document.close()
    return path


@pytest.fixture(scope="session")
def paper_pdf(tmp_path_factory) -> Path:
    """A generated paper, shared across the session — building it twice is waste."""
    directory = tmp_path_factory.mktemp("papers")
    return build_paper_pdf(directory / "consumption_and_resistance.pdf")


@pytest.fixture(scope="session")
def paper_search_term() -> str:
    """A phrase known to be in :func:`build_paper_pdf` output.

    A fixture rather than a literal in the test, so the phrase and the text it
    has to appear in cannot drift apart unnoticed.
    """
    return PAPER_SEARCH_TERM


@pytest.fixture(autouse=True)
def _fresh_model_provider():
    """
    Re-probe the model provider around every test.

    The provider is cached deliberately (probing costs a round trip), but that
    cache is process-global: one test pinning the provider to "none" would
    otherwise silently disable inference for every test that ran after it, in
    file order. Cheap to reset, and the alternative is an order-dependent suite.
    """
    import throughline_model
    from throughline_model import registry

    # The cache was only half the shared state. `registry.configure()` — which
    # `PUT /api/system/models` calls — writes a module-level `_override` that
    # takes precedence over the environment and nothing ever cleared, so one
    # test choosing a model in the interface pinned the selection for every test
    # that ran after it in file order.
    #
    # It stayed invisible while `/api/system/capabilities` reported `configured`
    # as a hardcoded `False`: the endpoint could not disagree with anything.
    # Wiring it to the registry surfaced the leak immediately, as an
    # order-dependent failure in a test that passed on its own. Recorded as D037.
    before = dict(registry._override)
    throughline_model.provider(refresh=True)
    yield
    registry._override.clear()
    registry._override.update(before)
    throughline_model.provider(refresh=True)


def make_enquiry(cur, project_id: str, name: str | None = None) -> str:
    """
    A real line of enquiry to hang looks off, for tests that need one.

    Before `enquiries` existed, a test could pass any string as the family and
    the ledger would accept it. It is a foreign key now, so a family has to be a
    thing that exists — which is the whole point of the change, and it makes
    tests that want two *distinct* families say so by creating two.

    Not a fixture: several tests need more than one per project, and a fixture
    that returns a single value cannot express that.
    """
    from throughline_domain import enquiry

    # `open_new` closes whatever is open, so successive calls in one test give
    # genuinely separate families rather than colliding on the one-open index.
    return enquiry.open_new(cur, project_id=project_id, name=name)["id"]



@pytest.fixture()
def make_enquiry_for(cur):
    """A line of enquiry on a given project, as a callable.

    `make_enquiry` takes a cursor; tests that already have one want to say
    `make_enquiry_for(project)` without threading it through again. A callable
    rather than an id, because a test may want two families to compare.
    """
    def _make(project_id: str, name: str | None = None) -> str:
        return make_enquiry(cur, project_id, name)

    return _make


def sign_in(client, *, email: str = "researcher@lab.local",
            password: str = "correct-horse-battery",
            display_name: str = "Researcher") -> str:
    """
    Sign a test client in, and fail loudly here if it did not work.

    Twenty-two test files opened with the same four lines:

        status = client.get("/api/auth/status").json()
        endpoint = "/api/auth/setup" if status["needs_setup"] else "/api/auth/login"
        client.post(endpoint, json={"email": ..., "password": ...})

    Nine of them generated a fresh random email each run, and only five of the
    twenty-two looked at what the sign-in answered. Those two facts together
    are a flake that cost two preflight investigations before it was caught
    here:

      a user row survives from an earlier file, so `needs_setup` is false;
      the branch therefore picks *login*, with an address that has never
      existed anywhere; the login answers 401 "Email or password is
      incorrect"; nobody looks; and the next request fails with 401 "Sign in
      to continue" — a message that names none of this, three lines from the
      cause, in a file that has nothing to do with authentication.

    That is the failure this whole codebase keeps finding in itself: an
    absence read as evidence. A sign-in that did not happen looked exactly
    like a sign-in that did.

    So this asks for the account it wants rather than inferring one from
    global state. Setup when the installation has no account; log in when this
    account exists; and create it first when some *other* account got there —
    which is the case the old branch could not express, and the whole reason
    it broke. The account is created directly for the same reason
    `test_interpretation_api` does it: setup closes after the first account,
    and a second one has no route.

    Returns the email, so a caller that wants a second, different account can
    say which one it is holding.
    """
    from throughline_domain import auth
    from throughline_domain.db import connection

    email = email.strip().lower()
    status = client.get("/api/auth/status")
    assert status.status_code == 200, status.text

    if status.json()["needs_setup"]:
        answer = client.post("/api/auth/setup", json={
            "email": email, "display_name": display_name, "password": password})
        assert answer.status_code == 200, (
            f"setting up {email} failed: {answer.status_code} {answer.text}")
        return email

    answer = client.post("/api/auth/login",
                         json={"email": email, "password": password})
    if answer.status_code == 200:
        return email

    # Somebody else's account closed setup. Make this one and sign in.
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM users WHERE email = %s", (email,))
        if not cur.fetchone():
            auth.create_user(cur, email=email, display_name=display_name,
                             password=password)
        conn.commit()

    answer = client.post("/api/auth/login",
                         json={"email": email, "password": password})
    assert answer.status_code == 200, (
        f"signing in as {email} failed: {answer.status_code} {answer.text}")
    return email
