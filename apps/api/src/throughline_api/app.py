"""HTTP surface.

Route handlers are deliberately thin: they authenticate, scope to a project, and
delegate. No research logic lives here. Every endpoint that mutates state
does so inside one transaction so that an object, its lineage and its audit
entry commit together.
"""

from __future__ import annotations

import io
import logging
import os
import pathlib
import tempfile

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import (
    Cookie, Depends, FastAPI, File, Form, HTTPException, Query, Request,
    Response, UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from throughline_visual import spec as visual_spec
from pydantic import ValidationError as PayloadInvalid
from throughline_domain import (
    snapshot,
    digitise as digitise_module,
    code_export,
    cohorts,
    provenance_log,
    research_context,
    tables,
    bibliography,
    analysis, auth, claim_test, compare, consistency, critic, discovery,
    embeddings, enquiry, events, example, exploration, extraction, findings,
    fragility,
    graph_projection, graphs,
    harmonize, images, interpret, journal, lineage, notebook, objects, observability,
    arrange, authoring, board, citations, communication, embedding_space,
    dataset_import,
    excerpts, extras,
    haptics,
    marks,
    regions,
    launchers as domain_launchers,
    updates as domain_updates,
    patterns, reconcile, render_artifact, retrieval, selection, speech,
    specification, storage, study_context, synthesis, validation, visuals,
    vocabulary,
    workflow,
)
from throughline_visual.prepare import prepare as visual_prepare
from throughline_visual.renderers import publication as publication_render
from throughline_schemas.words import counted
from throughline_visual.spec import ResearchVisualSpec, VisualType
from throughline_domain import secrets as domain_secrets
from throughline_domain import settings as domain_settings
from throughline_domain.db import connection, jsonb, transaction
from throughline_domain.ids import new_id
from throughline_domain.migrate import migrate
from throughline_runtime.executor import policy_report as sandbox_policy_report
from .security import SecurityMiddleware, deployment_is_local, session_cookie_kwargs
from throughline_schemas.enums import (
    FindingLifecycle,
    FindingType,
    ObjectType,
    SourceType,
    WorkflowState,
)

API_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(_: FastAPI):
    observability.configure()
    migrate()
    # Re-apply the researcher's model choice. Without this it would silently
    # revert to the environment default on every restart, and the system would
    # quietly use a model they had replaced — drift that stays invisible until
    # two runs disagree.
    try:
        with transaction() as cur:
            domain_settings.apply_model_choice(cur)
    except Exception:  # noqa: BLE001 — never let a preference block startup
        logging.getLogger("throughline.api").warning(
            "Could not apply the saved model choice; using the environment "
            "default.", exc_info=True)
    yield
    from throughline_domain.db import shutdown

    shutdown()


app = FastAPI(title="Throughline OS", version=API_VERSION, lifespan=lifespan)

# Rate limiting and security headers (§99). Added as middleware so no endpoint
# can be written that forgets them.
#
# Once. This line — and its import — appeared three times, so every request was
# counted three times: a hosted sign-in allowed about three attempts rather than
# ten, and every limit was a third of what it said. Invisible while limits were
# looked up by concrete path, since no bucket came near one (T167).
app.add_middleware(SecurityMiddleware)


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


#: The longest password any request accepts — one number for every model that
#: takes one. Setting a password allowed 1024 characters while signing in (and
#: first-run setup) allowed 400, so a long generated passphrase could be set and
#: then never used: sign-in refused it as too long before checking it, and the
#: account was locked out for good (T169).
MAX_PASSWORD = 1024


class SetupRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=MAX_PASSWORD)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    research_question: str = Field(default="", max_length=20_000)
    description: str = Field(default="", max_length=20_000)


class ObjectCreate(BaseModel):
    object_type: ObjectType
    title: str = Field(min_length=1, max_length=2000)
    description: str = Field(default="", max_length=50_000)
    derived_from: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FindingCreate(BaseModel):
    title: str = Field(min_length=1, max_length=1000)
    finding_type: FindingType
    statement: str = Field(default="", max_length=100_000)
    summary: str = Field(default="", max_length=100_000)
    #: The connections this finding was drawn from.
    #:
    #: Optional, because a researcher may record a finding before anything has
    #: computed one. Supplying it is what lets "why do we believe this?" answer
    #: with the analysis and the dataset rather than with the claims alone —
    #: `findings.object_id` was never set by any caller, and the whole
    #: evidence-graph branch that reads it therefore never ran.
    from_connections: list[str] = Field(default_factory=list)


class FindingTransition(BaseModel):
    to_status: FindingLifecycle
    reason: str = Field(min_length=1, max_length=4000)
    checks: dict[str, bool] = Field(default_factory=dict)


class FindingLimitations(BaseModel):
    """What a finding does not establish, in the researcher's own words."""

    limitations: list[str] = Field(default_factory=list, max_length=40)


# ---------------------------------------------------------------------------
# Auth plumbing
# ---------------------------------------------------------------------------


def current_user(throughline_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    with transaction() as cur:
        user = auth.resolve_session(cur, throughline_session)
    if not user:
        raise HTTPException(401, "Sign in to continue.")
    return user


def admin_user(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """
    The signed-in account, if it is this installation's administrator.

    `users.is_admin` has been set since accounts existed — the first account is
    the administrator, `docs/TRY_IT.md` says so, and the interface shows the
    badge — and nothing checked it. Any account could install packs, save or
    clear the model key, change the model, create accounts and install the
    desktop entry. Those act on the machine rather than on a researcher's own
    projects, and they are this role's now (T166). 403 rather than 404: unlike
    another account's project, what is being refused here is no secret.
    """
    if not user.get("is_admin"):
        raise HTTPException(
            403, "Only the administrator of this installation can do this. It "
                 "changes the machine for everyone who uses it, not one project.")
    return user


def scoped_project(project_id: str, user: dict[str, Any]) -> str:
    """Project isolation is checked here, never in the client."""
    with transaction() as cur:
        if not auth.owns_project(cur, user_id=user["id"], project_id=project_id):
            # Not 403: an account should not learn that someone else's project id exists.
            raise HTTPException(404, "Project not found.")
    return project_id


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------


@app.post("/api/speech/transcribe")
async def transcribe_speech(request: Request,
                            user: dict = Depends(current_user)) -> dict[str, Any]:
    """Turn recorded audio into timed words, without it leaving the machine.

    The body is raw 16 kHz mono float32 — not a container — because the browser
    already has a complete audio decoder and Whisper's usual path would otherwise
    shell out to ffmpeg, which is one more thing a researcher has to install
    before speech works at all.

    **Word times are relative to the clip and never absolute.** This process has
    no idea what the browser's monotonic clock reads, and inventing an absolute
    time would put speech and gesture on different clocks — which this codebase
    shipped once, silently, and will not again. The caller knows when it started
    recording and does the addition.

    **Signed in**, on the same reasoning as the feature packs: binding to
    localhost keeps out the network but not the researcher's own browser, and
    this one loads a speech model and runs it over whatever body arrives. The
    cookie is `SameSite=strict`, so a cross-origin POST carries no session.
    """
    raw = await request.body()
    try:
        return speech.transcribe(raw)
    except speech.SpeechError as exc:
        # 400 rather than 500: audio this system will not transcribe is a
        # request problem with a sentence a person can act on, not a fault.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        # A model that fails to load or infer must not take the API down; the
        # researcher's hand is still drawing and everything else still works.
        raise HTTPException(
            status_code=503,
            detail=f"Local transcription is unavailable: {exc}") from exc


@app.get("/health")
def liveness() -> dict[str, Any]:
    """
    The bare liveness probe, kept distinct from `/api/health`.

    Both used to be called `health`. The decorators had already registered each
    function object by the time the second definition rebound the name, so both
    routes worked — but the module-level name pointed at only one of them, and a
    reader checking "what does health() do" saw the wrong body for this route.
    """
    with transaction() as cur:
        cur.execute("SELECT 1 AS ok")
        db_ok = cur.fetchone()["ok"] == 1
    return {"status": "ok" if db_ok else "degraded", "version": API_VERSION}


@app.get("/api/auth/status")
def auth_status(throughline_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    with transaction() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM users")
        needs_setup = cur.fetchone()["n"] == 0
        user = auth.resolve_session(cur, throughline_session)
    return {"needs_setup": needs_setup, "authenticated": bool(user), "user": user}


@app.post("/api/auth/setup")
def auth_setup(payload: SetupRequest, response: Response) -> dict[str, Any]:
    with transaction() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM users")
        if cur.fetchone()["n"]:
            raise HTTPException(409, "This installation is already set up.")
        try:
            user = auth.create_user(
                cur, email=payload.email, display_name=payload.display_name,
                password=payload.password,
            )
        except auth.AuthError as exc:
            raise HTTPException(400, str(exc)) from exc
        token = auth.create_session(cur, user_id=user["id"])
    _set_session_cookie(response, token)
    return {"user": user}


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(default="", max_length=200)
    password: str = Field(min_length=12, max_length=MAX_PASSWORD)


def _registration_is_open(request: Request) -> bool:
    """
    Whether a stranger may create an account on this installation.

    Open sign-up and a local-first workspace are in genuine tension: anyone who
    can reach the port could otherwise help themselves to a corpus that lives on
    someone's laptop. So it is allowed from the machine itself, and off-machine
    only when the operator has explicitly turned it on.

    That keeps the ordinary case — a researcher installs this and signs up —
    working exactly as expected, without turning a laptop on café wifi into an
    open registration server.
    """
    with transaction() as cur:
        if (domain_settings.get(cur, "open_registration") or "").lower() in _OPEN:
            return True
    host = (request.client.host if request.client else "") or ""
    return host in ("127.0.0.1", "::1", "localhost")


class RegistrationSetting(BaseModel):
    open: bool


_OPEN = ("1", "true", "yes", "on")


@app.get("/api/system/registration")
def registration_setting(user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Whether strangers on the network may create accounts, and whether you can
    change that. Readable by any account, so the screen can say what is true
    and who can alter it rather than offering a switch that answers 403.
    """
    with transaction() as cur:
        value = (domain_settings.get(cur, "open_registration") or "").lower()
    return {"open": value in _OPEN, "can_change": bool(user.get("is_admin"))}


@app.put("/api/system/registration")
def set_registration(payload: RegistrationSetting,
                     user: dict = Depends(admin_user)) -> dict[str, Any]:
    """
    Turn open registration on or off.

    `_registration_is_open` has always read this, and the sign-up refusal and
    `docs/TRY_IT.md` both told people to turn it on in Settings — where there
    was no such switch, and no route that wrote it (T166). Administrator only:
    turned on, anyone who can reach the port can make themselves an account
    beside unpublished data. Written through `settings.set_value`, so who
    opened it and when is on the record.
    """
    with transaction() as cur:
        domain_settings.set_value(cur, "open_registration",
                                  "true" if payload.open else "false",
                                  changed_by=user["id"])
    return {"open": payload.open, "can_change": True}


@app.post("/api/auth/register", status_code=201)
def auth_register(payload: RegisterRequest, request: Request,
                  response: Response) -> dict[str, Any]:
    """
    Create an account and sign in, in one step.

    A brand-new user could not previously get in at all: `setup` runs once and
    `accounts` needs an existing session, so the second person to open this
    installation had no way to create an account.

    The new account owns nothing. Projects are scoped by owner, so a fresh
    account sees an empty workspace rather than anyone else's corpus — that is
    a property of the query, not of the interface hiding rows.
    """
    if not _registration_is_open(request):
        raise HTTPException(
            403,
            "Sign-up is limited to this machine. This workspace holds a "
            "researcher's corpus, so accounts can only be created locally "
            "unless the operator turns on open registration in Settings.")

    with transaction() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM users")
        first = cur.fetchone()["n"] == 0
        try:
            user = auth.create_user(
                cur, email=payload.email, display_name=payload.display_name,
                password=payload.password,
                # The person who installs it administers it. Nobody after that.
                is_admin=first)
        except auth.AuthError as exc:
            raise HTTPException(400, str(exc)) from exc
        token = auth.create_session(cur, user_id=user["id"])

    _set_session_cookie(response, token)
    return {"user": user, "first_account": first}


@app.post("/api/auth/login")
def auth_login(payload: LoginRequest, response: Response) -> dict[str, Any]:
    with transaction() as cur:
        user = auth.authenticate(cur, email=payload.email, password=payload.password)
        if not user:
            raise HTTPException(401, "Email or password is incorrect.")
        token = auth.create_session(cur, user_id=user["id"])
    _set_session_cookie(response, token)
    return {"user": user}


class NewAccount(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(default="", max_length=200)
    password: str = Field(min_length=12, max_length=MAX_PASSWORD)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD)
    new_password: str = Field(min_length=12, max_length=MAX_PASSWORD)


@app.post("/api/auth/accounts", status_code=201)
def create_account(payload: NewAccount,
                   user: dict = Depends(admin_user)) -> dict[str, Any]:
    """
    Add another researcher to this installation.

    Requires an existing session on purpose. This is a local-first workspace,
    not a service: anyone who can reach the port is on the machine or the
    network the researcher chose, and an open registration endpoint would let
    them help themselves to the corpus.
    """
    with transaction() as cur:
        try:
            created = auth.create_user(
                cur, email=payload.email, display_name=payload.display_name,
                password=payload.password, is_admin=False)
        except auth.AuthError as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"user": created}


@app.post("/api/auth/password")
def change_password(payload: PasswordChange, response: Response,
                    user: dict = Depends(current_user),
                    throughline_session: str | None = Cookie(default=None)
                    ) -> dict[str, Any]:
    """
    Change your own password, proving you know the current one.

    Every other session is destroyed. A password change is usually a response to
    the suspicion that someone else has it, and leaving their session alive is
    the one thing that would make the change pointless.
    """
    with transaction() as cur:
        confirmed = auth.authenticate(cur, email=user["email"],
                                      password=payload.current_password)
        if not confirmed:
            raise HTTPException(403, "That is not your current password.")
        try:
            auth.set_password(cur, user_id=user["id"],
                              password=payload.new_password)
        except auth.AuthError as exc:
            raise HTTPException(400, str(exc)) from exc
        auth.destroy_other_sessions(cur, user_id=user["id"],
                                    keep_token=throughline_session)
    return {"ok": True,
            "note": "Signed out everywhere else. This session stays open."}


@app.get("/api/auth/accounts")
def list_accounts(user: dict = Depends(current_user)) -> list[dict[str, Any]]:
    with transaction() as cur:
        cur.execute(
            "SELECT id, email, display_name, is_admin, created_at FROM users "
            "ORDER BY created_at")
        return [dict(row) for row in cur.fetchall()]


@app.post("/api/auth/logout")
def auth_logout(response: Response,
                throughline_session: str | None = Cookie(default=None)) -> dict[str, bool]:
    with transaction() as cur:
        auth.destroy_session(cur, throughline_session)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"ok": True}


def _set_session_cookie(response: Response, token: str) -> None:
    """
    Set the session cookie with flags matched to the deployment.

    `secure` was hardcoded False here, which is right on http://localhost and
    silently wrong the moment the API is reachable over a network — the token
    would travel in clear text with nothing in the interface saying so. It now
    defaults to on and is relaxed only when the deployment declares itself local.
    """
    response.set_cookie(
        auth.SESSION_COOKIE, token, max_age=auth.SESSION_DAYS * 86400,
        **session_cookie_kwargs(),
    )


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@app.get("/api/projects")
def list_projects(user: dict = Depends(current_user)) -> list[dict[str, Any]]:
    with transaction() as cur:
        cur.execute(
            # No `status`: the column exists with a DEFAULT and nothing ever
            # writes it, so every project was sent the word "active" whatever
            # its real state. Archiving is `archived_at`, which the filter
            # below already reads, and the client never displayed the constant
            # it was being sent (T154).
            "SELECT id, name, research_question, description, created_at, updated_at "
            "FROM projects WHERE owner_user_id = %s AND archived_at IS NULL "
            "ORDER BY created_at DESC",
            (user["id"],),
        )
        return list(cur.fetchall())


@app.post("/api/projects", status_code=201)
def create_project(payload: ProjectCreate, user: dict = Depends(current_user)) -> dict[str, Any]:
    project_id = new_id("prj")
    with transaction() as cur:
        cur.execute(
            "INSERT INTO projects(id, owner_user_id, name, research_question, description) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING *",
            (project_id, user["id"], payload.name, payload.research_question,
             payload.description),
        )
        return cur.fetchone()



@app.post("/api/projects/{project_id}/advance", status_code=202)
def advance_project(project_id: str,
                    user: dict = Depends(current_user)) -> dict[str, Any]:
    """Take this project's data as far as the machine honestly can, in one act.

    Discovery over the profiled columns, every pair tested and corrected for
    how many tests ran, and the strongest survivor recorded as a finding that
    keeps the line back to the analysis behind it.

    **Why this route exists.** The loop was six screens, each with its own
    button, each able to fail on its own, and nobody walks all six to find out
    whether their data says anything — the seeded example was the only project
    in this product that ever arrived with work in it. The work is the
    machine's; the judging is the researcher's.

    **Why it is a press and not automatic.** Queuing it when ingestion finishes
    was tried. The project then holds a discovery run nobody asked for, so the
    researcher's own *Discover connections* either doubles every connection or
    is refused as a repeat of something they never started — and spending
    compute on somebody's data unasked is its own objection.

    202 with the run id: this returns as soon as the work is queued. The
    workspace already knows how to show a project assembling itself, and
    watching it is a better introduction to the pipeline than a spinner.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        run_id = workflow.enqueue(
            cur, workflow_name="project.advance", project_id=project_id,
            payload={"project_id": project_id},
            # One walk in flight per project. Pressing twice while it runs is
            # the same request, not a second discovery.
            idempotency_key=f"advance:{project_id}",
            max_attempts=3)
    return {"workflow_run_id": run_id, "project_id": project_id}


@app.post("/api/projects/example", status_code=201)
def create_example_project(user: dict = Depends(current_user)) -> dict[str, Any]:
    """Seed the worked example (Part B6).

    Returns as soon as the sources are queued rather than waiting for them.
    Ingesting a PDF takes seconds, the workspace already knows how to show a
    source that is still being read, and watching the example assemble itself
    is a better introduction to the pipeline than a spinner followed by a
    finished screen.

    Idempotent per user: asking twice returns the project that already exists,
    because two identical examples would leave nobody able to tell which one
    they had been reading.
    """
    with transaction() as cur:
        result = example.create(cur, user_id=user["id"], actor=user["id"])
        cur.execute("SELECT * FROM projects WHERE id = %s", (result["project_id"],))
        project = cur.fetchone()
    return {**project, "created": result["created"]}


@app.delete("/api/projects/{project_id}", status_code=200)
def delete_project(project_id: str,
                   user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Delete a project and everything in it, for real.

    Not archived. Every table referencing a project cascades, so the sources,
    analyses, findings, figures and notes go with it — a project that vanishes
    from the list while its rows survive is the kind of half-deletion that
    later reappears as a foreign-key error nobody can explain.

    **Stored files are collected only when nothing else references them.** The
    object store is content-addressed: two projects that uploaded the same PDF
    share one blob. Deleting blobs by project would destroy the other project's
    evidence while its rows still claimed to have it, so the orphans are
    computed after the cascade rather than before it.
    """
    from throughline_domain import storage

    with transaction() as cur:
        cur.execute(
            "SELECT id, name FROM projects WHERE id = %s AND owner_user_id = %s",
            (project_id, user["id"]))
        project = cur.fetchone()
        if not project:
            # Same answer whether it never existed or belongs to someone else:
            # distinguishing them would confirm another user's project ids.
            raise HTTPException(404, "No such project.")

        cur.execute(
            "SELECT DISTINCT content_hash, storage_key FROM files "
            "WHERE project_id = %s", (project_id,))
        candidates = [(r["content_hash"], r["storage_key"])
                      for r in cur.fetchall()]

        # Counted before the cascade, because afterwards there is nothing left
        # to count. The audit log recorded creation and not destruction, which
        # for a research record is the wrong way round: a corpus can be erased —
        # sources, analyses, findings, figures, notes — and the log that exists
        # to make the work legible said nothing at all. "Deleted a project" is
        # not a record either; what was in it is.
        destroyed: dict[str, int] = {}
        for table in ("sources", "datasets", "analysis_runs", "connections",
                      "findings", "visuals", "communication_artifacts",
                      "notes", "preregistrations", "exploration_tests"):
            cur.execute(f"SELECT count(*) AS n FROM {table} WHERE project_id = %s",
                        (project_id,))
            count = int(cur.fetchone()["n"])
            if count:
                destroyed[table] = count

        # Their ids, before the cascade takes the rows: rendered reports and
        # figures live in per-object directories outside `files`, and afterwards
        # there is nothing left to say which ones were this project's (T170).
        exports: dict[str, list[str]] = {}
        for table in storage.EXPORT_DIRECTORIES:
            cur.execute(f"SELECT id FROM {table} WHERE project_id = %s",  # noqa: S608
                        (project_id,))
            exports[table] = [row["id"] for row in cur.fetchall()]

        cur.execute("DELETE FROM projects WHERE id = %s", (project_id,))

        events.audit(
            cur, project_id=None, actor=user["id"], action="delete",
            object_type="project", object_id=project_id,
            # `project_id` is null on purpose: the row it would reference no
            # longer exists, and an audit entry that cascades away with the
            # thing it records is not an audit entry.
            detail={"name": project["name"], "destroyed": destroyed})

        # Which of those blobs are now referenced by nothing at all.
        orphans: list[str] = []
        for content_hash, key in candidates:
            cur.execute(
                "SELECT 1 FROM files WHERE content_hash = %s LIMIT 1",
                (content_hash,))
            if not cur.fetchone():
                orphans.append(key)

    collected = storage.collect(orphans)
    # After the commit, like `collect`: files are removed only once the rows
    # that described them are gone for good.
    storage.collect_exports(exports)
    return {
        "deleted": project_id,
        "name": project["name"],
        "files_removed": collected["removed"],
        "files_kept_shared": len(candidates) - len(orphans),
        "note": (
            f"{project['name']} and everything in it is gone. "
            + (f"{collected['removed']} stored files were removed; "
               f"{len(candidates) - len(orphans)} were kept because another "
               "project uses the same bytes."
               if candidates else "No stored files were attached.")),
    }


# ---------------------------------------------------------------------------
# Sources and research objects
# ---------------------------------------------------------------------------


@app.post("/api/projects/{project_id}/sources", status_code=202)
async def upload_source(
    project_id: str, file: UploadFile = File(...), user: dict = Depends(current_user)
) -> dict[str, Any]:
    """Store the bytes immutably, create the source, and queue ingestion.

    The response is 202: the file is safely on disk and the work is durably
    queued, but nothing has been parsed yet. Reporting 200 here would claim a
    completeness the system does not have.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        record = storage.register_file(
            cur, project_id=project_id, filename=file.filename or "upload",
            stream=file.file, media_type=file.content_type or "application/octet-stream",
        )

        # The same bytes in the same project ingest once. That was already the
        # intent, and keying the ingestion workflow on the content hash was how it
        # was attempted — but it deduplicated the wrong half of the operation. The
        # second upload still created a source row, and then enqueue() handed back
        # the *already completed* run belonging to the first one, so nothing would
        # ever move the new row out of 'uploaded'. The interface showed it waiting
        # for a worker for as long as the project existed.
        #
        # Deduplicating here answers a repeat upload with the source that already
        # holds those bytes, which is what the researcher meant, and leaves no row
        # behind that no worker will ever look at.
        existing = objects.find_source_by_content_hash(
            cur, project_id=project_id, content_hash=str(record["content_hash"]),
            source_type=SourceType.UPLOAD,
        )
        if existing is not None:
            # The run that actually ingested these bytes, not a new one and not
            # null: a caller comparing run ids across two uploads of the same file
            # is asking "did this ingest twice?", and the honest answer is one run
            # id, the same both times.
            return {
                "source_id": existing["id"],
                "file": record,
                "workflow_run_id": existing["ingest_run_id"],
                "ingestion_status": existing["ingestion_status"],
                # Distinct from file.deduplicated, which reports that the *bytes*
                # were already stored. This reports that the *source* already
                # existed, so no second one was made.
                "source_reused": True,
                "note": (
                    f"These bytes are already in this project as "
                    f"{existing['title']!r}, currently "
                    f"{existing['ingestion_status']}. No second source was created."
                ),
            }

        source_id = objects.create_source(
            cur, project_id=project_id, source_type=SourceType.UPLOAD,
            title=file.filename or "upload", actor=user["id"],
            file_id=str(record["id"]), content_hash=str(record["content_hash"]),
        )
        run_id = workflow.enqueue(
            cur, workflow_name="ingest.source", project_id=project_id,
            payload={"source_id": source_id},
            # Keyed per source rather than per content hash. A retried or
            # duplicated POST for one source is still collapsed into a single run,
            # but a source that does get created can no longer end up without a run
            # to finish it — which is the only way the orphan above was reachable,
            # including under two near-simultaneous uploads that cannot see each
            # other's row yet.
            idempotency_key=f"ingest:{source_id}",
        )
    return {
        "source_id": source_id, "file": record, "workflow_run_id": run_id,
        "ingestion_status": "uploaded",
        "source_reused": False,
    }


@app.get("/api/projects/{project_id}/sources")
def list_sources(project_id: str, user: dict = Depends(current_user)) -> list[dict[str, Any]]:
    """Every source in the project, with what ingestion made of it — and where
    it came from.

    **The provenance is selected because the product already stores it and no
    screen could say it (D211).** `POST /api/projects/{id}/datasets/import`
    writes the repository, the address and the licence onto the source — the
    route's own note promises the researcher that Sources will show them — and
    this list selected none of the three, so an imported dataset was
    indistinguishable from a file somebody dragged in.

    `repository` and `licence` are lifted out of `metadata` to the top level
    rather than handing the whole blob over: the rest of that object is the
    importer's bookkeeping (the redirect it followed, the byte count), and a
    list that ships it invites a screen to render whatever happens to be in
    there. Both are null when absent, and *null is not a licence* — "not
    stated" and "openly licensed" are different facts and the interface has to
    be able to tell them apart, which it cannot if a missing licence arrives as
    an empty string.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        cur.execute(
            "SELECT id, title, source_type, ingestion_status, ingestion_detail, "
            "trust_level, content_hash, created_at, connector_id, original_uri, "
            # `->>` yields SQL NULL for a key that is absent and for a JSON
            # null alike, which is the answer in both cases: nothing was stated.
            "metadata->>'repository' AS repository, "
            "metadata->>'licence' AS licence "
            "FROM sources "
            "WHERE project_id = %s ORDER BY created_at DESC",
            (project_id,),
        )
        sources = [dict(row) for row in cur.fetchall()]

        # What ingestion actually produced, attached to the source that
        # produced it. Without this the list can say a source is "ready"
        # while giving the interface nothing to open — and "ready" with
        # nothing behind it is the least useful true statement available.
        #
        # Both are always present as keys, null when absent, so a caller
        # never has to distinguish "no dataset" from "this endpoint does
        # not report datasets".
        # The research object each source has in the graph, in two queries
        # rather than two per source.
        #
        # This is the handle every provenance route actually takes. The journal,
        # the note and the version history are all addressed by research-object
        # id, and a screen holding only a source id cannot reach any of them —
        # D213 records exactly that dead end for the finding, source and
        # analysis details. Sending it with the source ends the class of
        # problem for every screen that lists sources.
        #
        # Two queries because a source reaches its node by two different roads,
        # and taking only the first road is what made this look finished while
        # returning null for every dataset. A paper's object points back at the
        # source (`research_objects.source_id`), and a dataset's is named by
        # the dataset row (`datasets.object_id`) — the same split `claim_test`
        # navigates when it looks up the two halves of a pair.
        #
        # Null when the source has no object yet, which is an ordinary state
        # while ingestion is still running. A caller must be able to tell "not
        # yet" from "this endpoint does not report it", so the key is always
        # present.
        source_ids = [s["id"] for s in sources]
        cur.execute(
            "SELECT source_id, id FROM research_objects "
            "WHERE project_id = %s AND source_id = ANY(%s) "
            # Oldest first, so a source that has grown several objects resolves
            # to the same one on every request rather than to whichever row the
            # planner happened to return.
            "ORDER BY created_at",
            (project_id, source_ids))
        object_of = {row["source_id"]: row["id"] for row in cur.fetchall()}

        cur.execute(
            "SELECT source_id, object_id FROM datasets "
            "WHERE project_id = %s AND source_id = ANY(%s) "
            "  AND object_id IS NOT NULL",
            (project_id, source_ids))
        for row in cur.fetchall():
            object_of.setdefault(row["source_id"], row["object_id"])

        for source in sources:
            source["object_id"] = object_of.get(source["id"])
            source["paper"] = None
            source["dataset"] = None

            cur.execute(
                "SELECT id, title, page_count FROM papers WHERE source_id = %s",
                (source["id"],))
            paper = cur.fetchone()
            if paper:
                source["paper"] = dict(paper)
                cur.execute(
                    "SELECT COUNT(*) AS n FROM passages WHERE source_id = %s",
                    (source["id"],))
                source["passage_count"] = cur.fetchone()["n"]

            # The latest version, not the first: a re-upload supersedes, and a
            # list showing the original would point at data nobody is using.
            cur.execute(
                """
                SELECT d.id AS dataset_id, dv.id AS dataset_version_id,
                       dv.version, dv.row_count, dv.column_count,
                       dv.quality_report
                FROM datasets d
                JOIN dataset_versions dv ON dv.dataset_id = d.id
                WHERE d.source_id = %s
                ORDER BY dv.version DESC
                LIMIT 1
                """,
                (source["id"],))
            dataset = cur.fetchone()
            if dataset:
                source["dataset"] = dict(dataset)

        return sources


@app.post("/api/projects/{project_id}/objects", status_code=201)
def create_object(project_id: str, payload: ObjectCreate,
                  user: dict = Depends(current_user)) -> dict[str, Any]:
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            object_id = objects.create_object(
                cur, project_id=project_id, object_type=payload.object_type,
                title=payload.title, description=payload.description,
                metadata=payload.metadata, derived_from=payload.derived_from,
                actor=user["id"],
            )
        except lineage.LineageError as exc:
            raise HTTPException(422, str(exc)) from exc
    return {"object_id": object_id}


class ObjectEdit(BaseModel):
    """A correction to an object's own description of itself."""

    title: str | None = None
    description: str | None = None
    metadata: dict[str, Any] | None = None


class RestoreRequest(BaseModel):
    version_id: str
    reason: str = ""


def _object_in_project(cur, *, project_id: str, object_id: str) -> dict[str, Any]:
    cur.execute(
        "SELECT id, title, project_id FROM research_objects "
        "WHERE id = %s AND project_id = %s",
        (object_id, project_id))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "No such object in this project.")
    return dict(row)


def _sources_in_project(cur, *, project_id: str, source_ids: list[str]) -> None:
    """
    Every source named in a request body belongs to the project in its path.

    Scoping the project is not scoping the ids a body carries: synthesis, marks
    and excerpts handed `source_id` straight to a domain function that looked
    it up alone, so another project's papers could be read into a comparison
    or marked from your own (T162). 404 for any stranger, the same as an id
    that does not exist, and before anything is read or written.
    """
    wanted = set(source_ids)
    cur.execute("SELECT id FROM sources WHERE project_id = %s AND id = ANY(%s)",
                (project_id, list(wanted)))
    if {row["id"] for row in cur.fetchall()} != wanted:
        raise HTTPException(404, "No such paper in this project.")


#: Why a lookup came back empty, said in the caller's terms.
#:
#: Each names the ordinary state that produces it, because every one of them is
#: reachable without anything being broken and a bare "not found" would read as
#: a fault. An id that belongs to another project, or to nothing at all, gets
#: the same sentence on purpose — the same reason `scoped_project` answers 404
#: rather than 403, so that an account cannot learn what exists elsewhere.
_NOTHING_TO_SHOW = {
    "finding": ("No research object stands for this finding, so there is no "
                "history to show. A finding recorded before findings had "
                "objects, or one recorded with no connection behind it, has "
                "none."),
    "analysis_run": ("No research object stands for this analysis run, so "
                     "there is no history to show. A run becomes an object "
                     "when it completes; one that failed or has not finished "
                     "has none."),
    "source": ("No research object stands for this source, so there is no "
               "history to show. Ingestion has not produced a paper or a "
               "dataset from it."),
}


@app.get("/api/projects/{project_id}/objects/lookup")
def lookup_object(project_id: str,
                  kind: Literal["finding", "analysis_run", "source"],
                  id: str,
                  user: dict = Depends(current_user)) -> dict[str, Any]:
    """The research object standing for a finding, an analysis run or a source.

    **This is the join that lets object history be mounted anywhere but the
    board (D213).** The journal and the version chain are addressed by research
    object id, and the finding, source and analysis screens each hold a domain
    id instead — so the history sat on the card detail alone, because mounting
    it on the other three would have 404ed on every request.

    A GET with the domain id in the query string rather than in the path: the
    id being resolved is not a sub-resource of `objects`, and putting it in the
    path would claim `/objects/fnd_…` names an object, which is the confusion
    this route exists to remove.

    `kind` is a closed set, so a value outside it is refused by validation as a
    422 before any query runs — an unknown kind is a caller's mistake, and
    answering it with 404 would let a screen asking for the wrong thing look
    like a screen asking about something that does not exist.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        found = objects.object_for(cur, project_id=project_id, kind=kind,
                                   ref_id=id)
    if found is None:
        raise HTTPException(404, _NOTHING_TO_SHOW[kind])
    return found


@app.patch("/api/projects/{project_id}/objects/{object_id}")
def edit_object(project_id: str, object_id: str, payload: ObjectEdit,
                user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Correct an object's title, description or metadata.

    **This is what made the versioning reachable.** `update_object` and
    `new_version` have been correct since they were written, and nothing
    outside the tests called either — so a researcher could not edit a
    research object at all, and the mechanism that keeps an edit safe when
    other work depends on it was written by nothing in production.

    The domain decides which kind of edit this is, and the answer is returned
    rather than assumed: an object nothing was derived from is edited in
    place; one that other work cites is superseded by a new version, so the
    evidence pointing at the old state still resolves to what it described.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        _object_in_project(cur, project_id=project_id, object_id=object_id)
        try:
            now_at = objects.update_object(
                cur, object_id=object_id, actor=user["id"],
                title=payload.title, description=payload.description,
                metadata=payload.metadata)
        except objects.ObjectError as exc:
            raise HTTPException(422, str(exc)) from exc
        events.audit(cur, project_id=project_id, actor=user["id"],
                     action="edited", object_type="research_object",
                     object_id=now_at)

    versioned = now_at != object_id
    return {
        "object_id": now_at,
        "versioned": versioned,
        "note": ("Other work is derived from this, so the edit was recorded as "
                 "a new version and the old one still stands where it was "
                 "cited." if versioned else
                 "Nothing is derived from this yet, so it was edited in place."),
    }


@app.get("/api/projects/{project_id}/objects/{object_id}/versions")
def object_versions(project_id: str, object_id: str,
                    user: dict = Depends(current_user)) -> dict[str, Any]:
    """This object's chain, oldest first."""
    scoped_project(project_id, user)
    with transaction() as cur:
        _object_in_project(cur, project_id=project_id, object_id=object_id)
        chain = objects.versions_of(cur, object_id=object_id)
    return {
        "object_id": object_id,
        "versions": chain,
        "current": chain[-1]["id"] if chain else object_id,
    }


@app.post("/api/projects/{project_id}/objects/{object_id}/restore",
          status_code=201)
def restore_object_version(project_id: str, object_id: str,
                           payload: RestoreRequest,
                           user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Bring an earlier version's content back, as a new version.

    §43 recorded this as missing, and it was missing the operation rather than
    the data: the chain has been kept in full all along. Nothing is rewritten
    — a restore is a forward step whose content happens to be an old one's, so
    the history shows that somebody went back rather than pretending the
    intervening versions never happened.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        _object_in_project(cur, project_id=project_id, object_id=object_id)
        try:
            restored = objects.restore_version(
                cur, object_id=object_id, version_id=payload.version_id,
                actor=user["id"], reason=payload.reason)
        except objects.ObjectError as exc:
            # 422: a version from another object's history, or one that is
            # already current, is the caller's to fix and the message says
            # which.
            raise HTTPException(422, str(exc)) from exc
        events.audit(cur, project_id=project_id, actor=user["id"],
                     action="restored", object_type="research_object",
                     object_id=restored,
                     detail={"from_version": payload.version_id})
    return {
        "object_id": restored,
        "note": ("The earlier content is current again, recorded as a new "
                 "version. Nothing was deleted — the versions in between are "
                 "still in the history."),
    }


@app.get("/api/objects/{object_id}/provenance")
def object_provenance(object_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    """"How was this made?" resolved through the lineage graph."""
    with transaction() as cur:
        cur.execute("SELECT project_id FROM research_objects WHERE id = %s", (object_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Artifact not found.")
        scoped_project(row["project_id"], user)
        return lineage.provenance_chain(cur, object_id)


@app.get("/api/objects/{object_id}/impact")
def object_impact(object_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    """what a deletion would destroy, before it is destroyed."""
    with transaction() as cur:
        cur.execute("SELECT project_id FROM research_objects WHERE id = %s", (object_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Artifact not found.")
        scoped_project(row["project_id"], user)
        return objects.deletion_impact(cur, object_id)


@app.get("/api/projects/{project_id}/sources/{source_id}")
def get_source(project_id: str, source_id: str,
               user: dict = Depends(current_user)) -> dict[str, Any]:
    """A source with whatever ingestion produced from it."""
    scoped_project(project_id, user)
    with transaction() as cur:
        cur.execute("SELECT * FROM sources WHERE id = %s AND project_id = %s",
                    (source_id, project_id))
        source = cur.fetchone()
        if not source:
            raise HTTPException(404, "Source not found.")
        cur.execute("SELECT COUNT(*) AS n FROM passages WHERE source_id = %s", (source_id,))
        source["passage_count"] = cur.fetchone()["n"]
        # `metadata` carries which parser read the paper and, when the column
        # split could not be detected on a page, which pages were read
        # whole-width — where a two-column layout splices unrelated sentences
        # together. The parser has recorded that for as long as it has existed
        # and this route selected every other column, so the one fact that
        # bears on whether a quotation is verbatim stopped at the database.
        cur.execute(
            "SELECT id, title, page_count, object_id, metadata "
            "FROM papers WHERE source_id = %s",
            (source_id,))
        source["paper"] = cur.fetchone()
        cur.execute(
            """
            SELECT d.id AS dataset_id, dv.id AS dataset_version_id, dv.version,
                   dv.row_count, dv.column_count, dv.quality_report
            FROM datasets d JOIN dataset_versions dv ON dv.dataset_id = d.id
            WHERE d.source_id = %s ORDER BY dv.version DESC LIMIT 1
            """,
            (source_id,),
        )
        source["dataset"] = cur.fetchone()
        return source


@app.get("/api/dataset-versions/{version_id}/columns")
def dataset_columns(version_id: str, user: dict = Depends(current_user)) -> list[dict[str, Any]]:
    """The profiled schema."""
    with transaction() as cur:
        cur.execute(
            "SELECT d.project_id FROM dataset_versions dv "
            "JOIN datasets d ON d.id = dv.dataset_id WHERE dv.id = %s",
            (version_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Dataset version not found.")
        scoped_project(row["project_id"], user)
        cur.execute(
            "SELECT ordinal, name, original_name, physical_type, semantic_type, unit, "
            "missing_count, unique_count, statistics, sensitivity FROM dataset_columns "
            "WHERE dataset_version_id = %s ORDER BY ordinal",
            (version_id,),
        )
        return list(cur.fetchall())


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@app.get("/api/projects/{project_id}/search")
def search(
    project_id: str,
    q: str = Query(min_length=1, max_length=2000),
    limit: int = Query(10, ge=1, le=100),
    source_id: list[str] | None = Query(default=None),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    """Hybrid retrieval. The response names the strategy actually used."""
    scoped_project(project_id, user)
    with transaction() as cur:
        return retrieval.hybrid_search(cur, project_id=project_id, query=q,
                                       limit=limit, source_ids=source_id)


@app.get("/api/retrievals/{event_id}")
def retrieval_provenance(event_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    """audit exactly which passages an answer was built from."""
    with transaction() as cur:
        cur.execute("SELECT project_id FROM retrieval_events WHERE id = %s", (event_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Retrieval event not found.")
        scoped_project(row["project_id"], user)
        return retrieval.retrieval_provenance(cur, event_id)


class NoteBody(BaseModel):
    body: str
    object_type: str = "unknown"
    replies_to: str | None = None


class Question(BaseModel):
    question: str
    #: What the researcher pointed at, when the question is about a selection
    #: in a visualization (§26). Validated in `throughline_domain.selection`
    #: rather than here: the rules are about scientific honesty — statistics
    #: recomputed rather than trusted, no wording that implies a grouping was
    #: fitted — and they belong beside the code that renders it for a model.
    selection: dict[str, Any] | None = None
    #: What the researcher is looking at — screen, filters, chart configuration
    #: and where attention is (§36). Validated in
    #: `throughline_domain.research_context` for the same reason the selection
    #: is: the rules are about what a model may believe, chiefly that a
    #: filtered count on a screen is not a total in the project.
    view: dict[str, Any] | None = None


@app.get("/api/projects/{project_id}/objects/{object_id}/journal")
def object_journal(project_id: str, object_id: str,
                   user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Everything recorded about one node in the graph, plus its notes.

    Provenance only — there is deliberately no retrieval step here. A model or a
    reader handed semantically similar prose will treat it as though it were
    about this object, and a note written on that basis is wrong in a way that
    looks researched.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            return journal.context(cur, project_id=project_id,
                                   object_id=object_id)
        except journal.JournalError as exc:
            raise HTTPException(404, str(exc)) from exc


@app.post("/api/projects/{project_id}/objects/{object_id}/journal",
          status_code=201)
def add_note(project_id: str, object_id: str, payload: NoteBody,
             user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Write a note on a node. Append-only.

    There is no edit endpoint, and that is deliberate: what a researcher
    believed at the time is evidence about how they reached a conclusion, and
    editing it away would rewrite the reasoning while leaving the conclusion
    standing. Corrections are made by writing another note.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            return journal.write(
                cur, project_id=project_id, object_id=object_id,
                object_type=payload.object_type, body=payload.body,
                author=user["id"], replies_to=payload.replies_to)
        except journal.NoSuchObject as exc:
            # 404, as for any id outside the project, so the refusal cannot be
            # used to tell another project's object ids from made-up ones (T164).
            raise HTTPException(404, str(exc)) from exc
        except journal.JournalError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.post("/api/projects/{project_id}/objects/{object_id}/ask", status_code=201)
def ask_about_object(project_id: str, object_id: str, payload: Question,
                     user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Ask the configured model about this node; the answer is recorded as a note.

    Stored as a *model* note, never as the researcher's, and rendered as one
    forever. The moment those blur, the journal stops being a record of what the
    researcher thought.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            return journal.ask(cur, project_id=project_id, object_id=object_id,
                               question=payload.question, author=user["id"],
                               selection=payload.selection,
                               view=payload.view)
        except journal.NoSuchObject as exc:
            # 404, not 503. Reporting a missing object as a service outage sent
            # researchers to check a model configuration that was working.
            raise HTTPException(404, str(exc)) from exc
        except research_context.ContextError as exc:
            # 400 for the same reason as a bad selection below: a view this
            # system cannot describe honestly is the caller's to fix, and
            # calling it a model outage sends the researcher to the wrong
            # screen entirely.
            raise HTTPException(400, str(exc)) from exc
        except selection.SelectionError as exc:
            # 400, not 503: a selection this system cannot describe honestly is
            # the caller's to fix, and reporting it as a model outage would send
            # the researcher looking in entirely the wrong place.
            raise HTTPException(400, str(exc)) from exc
        except journal.JournalError as exc:
            raise HTTPException(503, str(exc)) from exc


class HapticTap(BaseModel):
    pattern: str = "generic"


@app.get("/api/haptics")
def haptic_capability() -> dict[str, Any]:
    """What haptic feedback this machine can produce, and where it is felt.

    Unauthenticated, deliberately. It is a property of the hardware rather than
    of anybody's research: it reads no project, returns no data about anyone,
    and the gesture-check page has to work before a researcher has an account —
    testing tracking on a colleague's laptop must not require making them one.
    """
    return haptics.capability()


@app.post("/api/haptics/tap")
def haptic_tap(payload: HapticTap) -> dict[str, Any]:
    """Perform one tap on the trackpad.

    A JSON body rather than an empty POST, and that is a security decision
    rather than a style one: a request carrying `application/json` is not a
    "simple" request, so a browser must preflight it, and no cross-origin
    preflight is permitted here. Without that, any page in any tab could POST to
    this port and buzz somebody's trackpad.

    Returns whether it fired. A machine with no actuator answers 200 with
    `performed: false` — the caller asked a reasonable question and the answer is
    no, which is not a server error.
    """
    return {"performed": haptics.tap(payload.pattern)}


@app.get("/api/projects/{project_id}/embedding-space")
def embedding_space_view(project_id: str, limit: int = Query(
                             embedding_space.MAX_POINTS, ge=4, le=5000),
                         user: dict = Depends(current_user)) -> dict[str, Any]:
    """This project's passages projected into three dimensions.

    503 rather than 200-with-an-empty-list when it cannot be done. A chart drawn
    from nothing is indistinguishable from a chart of a corpus with no
    structure, and the researcher would read the second when the truth is the
    first. The reason travels with the status so the interface can say which of
    the several quite different causes it was.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            return embedding_space.project(cur, project_id, limit=limit)
        except embedding_space.EmbeddingSpaceUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc


@app.get("/api/projects/{project_id}/journal")
def project_journal(project_id: str, limit: int = Query(50, ge=1, le=200),
                    user: dict = Depends(current_user)) -> list[dict[str, Any]]:
    """The journal as a stream — what has been thought about lately."""
    scoped_project(project_id, user)
    with transaction() as cur:
        return journal.recent(cur, project_id, limit=limit)


@app.post("/api/projects/{project_id}/graph-projection", status_code=201)
def rebuild_projection(project_id: str,
                       user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Rebuild this project's Neo4j projection from PostgreSQL (ADR 0002).

    Whole-project rather than incremental on purpose: a projection that is
    *nearly* right invites exactly the trust a derived store must never be
    given.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            return graph_projection.rebuild(cur, project_id)
        except graph_projection.ProjectionUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc


@app.get("/api/projects/{project_id}/graph/path")
def graph_path(project_id: str, source_id: str = Query(...),
               target_id: str = Query(...), max_depth: int = Query(8, ge=1, le=15),
               user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    How are these two objects connected at all?

    Routed to Neo4j because the traversal is variable-length and open-ended,
    which is where a recursive CTE explores exponentially and Cypher prunes.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            return graph_projection.shortest_path(
                cur, project_id=project_id, source_id=source_id,
                target_id=target_id, max_depth=max_depth)
        except graph_projection.ProjectionUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc


@app.get("/api/projects/{project_id}/graph/centrality")
def graph_centrality(project_id: str, limit: int = Query(20, ge=1, le=100),
                     user: dict = Depends(current_user)) -> dict[str, Any]:
    """Which objects are most connected — a structural fact, not a finding."""
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            return graph_projection.centrality(cur, project_id=project_id,
                                               limit=limit)
        except graph_projection.ProjectionUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc


@app.get("/api/projects/{project_id}/graph/communities")
def graph_communities(project_id: str, max_depth: int = Query(4, ge=1, le=8),
                      user: dict = Depends(current_user)) -> dict[str, Any]:
    """Which objects cluster together through recorded relationships."""
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            return graph_projection.communities(cur, project_id=project_id,
                                                max_depth=max_depth)
        except graph_projection.ProjectionUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc


@app.get("/api/projects/{project_id}/graph/reachable")
def graph_reachable(project_id: str, source_id: str = Query(...),
                    max_depth: int = Query(5, ge=1, le=10),
                    user: dict = Depends(current_user)) -> dict[str, Any]:
    """Everything derived from, or contributing to, one object at any depth."""
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            return graph_projection.reachable(
                cur, project_id=project_id, source_id=source_id,
                max_depth=max_depth)
        except graph_projection.ProjectionUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc


class SpecificationCurveRequest(BaseModel):
    dataset_version_id: str
    outcome: str
    exposure: str
    #: The covariates the *researcher* thinks might belong in the model. The
    #: system never chooses these: deciding what to adjust for is a causal
    #: judgement, and making it from the data is exactly what this rule forbids.
    candidates: list[str] = Field(default_factory=list, max_length=8)


@app.post("/api/projects/{project_id}/specification-curve", status_code=201)
def specification_curve(project_id: str, payload: SpecificationCurveRequest,
                        user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    P15 — run the relationship across every combination of these covariates.

    Returns the distribution, never a preferred specification. A result that
    flips sign or significance across reasonable covariate sets is reported as
    undetermined, because reporting any one of them would be reporting a choice
    of covariates rather than a finding.

    Every specification goes through the same sandbox, with the same validation
    and the same policy, as any other analysis — this is not a second path into
    the compute layer.
    """
    scoped_project(project_id, user)

    from throughline_runtime.executor import SandboxPolicy
    from throughline_runtime.executor import run_analysis as sandbox_run

    with transaction() as cur:
        cur.execute(
            "SELECT dv.storage_key, d.project_id, d.format, s.title "
            "FROM dataset_versions dv JOIN datasets d ON d.id = dv.dataset_id "
            "JOIN sources s ON s.id = d.source_id WHERE dv.id = %s",
            (payload.dataset_version_id,))
        version = cur.fetchone()
        if not version:
            raise HTTPException(404, "That dataset version does not exist.")
        if version["project_id"] != project_id:
            raise HTTPException(403, "That dataset belongs to a different project.")
        path = storage.path_for(version["storage_key"])
        # Stored objects are content-addressed and therefore have no extension.
        # The sandbox reads the format from the suffix, so it comes from the
        # recorded format — not from the path, which has none.
        suffix = (Path(version["title"] or "").suffix.lower()
                  or f".{(version['format'] or 'csv').lower()}")

    def run_one(spec: dict[str, Any]) -> dict[str, Any]:
        result = sandbox_run(spec=spec, input_path=path, input_suffix=suffix,
                             policy=SandboxPolicy())
        if not result.ok:
            # The sandbox reports failures in its payload, not on stderr.
            # Reading stderr produced "the fit failed" for every specification,
            # which told a researcher nothing about which covariate set broke or
            # why — and the reason a specification cannot be fitted is exactly
            # what they need to know.
            body = result.payload or {}
            raise RuntimeError(
                str(body.get("error") or result.stderr or "the fit failed")[:300])
        return (result.payload or {}).get("result") or {}

    with transaction() as cur:
        try:
            return specification.curve(
                cur, project_id=project_id,
                dataset_version_id=payload.dataset_version_id,
                outcome=payload.outcome, exposure=payload.exposure,
                candidates=payload.candidates, run_analysis=run_one)
        except specification.SpecificationError as exc:
            raise HTTPException(400, str(exc)) from exc


class SynthesisRequest(BaseModel):
    source_ids: list[str] = Field(min_length=2, max_length=12)


@app.post("/api/sources/{source_id}/extract", status_code=201)
def extract_paper(source_id: str, project_id: str = Query(...),
                  force: bool = Query(False),
                  user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Read what a paper says about its own methods, results and limits.

    Every field is a verbatim quotation, verified against the paper's text after
    generation. A sentence that cannot be found is discarded rather than shown —
    so a wrong extraction becomes an empty cell with a stated reason, never a
    plausible fabrication in a comparison table.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            return extraction.extract(cur, project_id=project_id,
                                      source_id=source_id, force=force)
        except extraction.ExtractionError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.get("/api/sources/{source_id}/extract")
def stored_extraction(source_id: str, project_id: str = Query(...),
                      user: dict = Depends(current_user)) -> dict[str, Any]:
    """What was already read out of this paper. Never runs a model."""
    scoped_project(project_id, user)
    with transaction() as cur:
        record = extraction.stored(cur, source_id)
        if record is None:
            raise HTTPException(404, "This paper has not been read yet.")
        return record


@app.post("/api/projects/{project_id}/synthesis", status_code=201)
def compare_papers(project_id: str, payload: SynthesisRequest,
                   user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Compare several papers side by side.

    Built only from verified readings — never from a fresh extraction — so the
    table is reproducible and cannot change under the researcher between two
    glances. Every pair is adjudicated, and what cannot be compared is reported
    before anything that agrees.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        _sources_in_project(cur, project_id=project_id, source_ids=payload.source_ids)
        try:
            return synthesis.matrix(cur, project_id=project_id,
                                    source_ids=payload.source_ids)
        except synthesis.SynthesisError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.post("/api/projects/{project_id}/synthesis/key-points", status_code=201)
def synthesis_key_points(project_id: str, payload: SynthesisRequest,
                         user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    What a set of papers says taken together — counted, never written.

    A generated synthesis of several papers is precisely the artifact a reader
    cannot check, so these are counts over verified quotations.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        _sources_in_project(cur, project_id=project_id, source_ids=payload.source_ids)
        try:
            return synthesis.key_points(cur, project_id=project_id,
                                        source_ids=payload.source_ids)
        except synthesis.SynthesisError as exc:
            raise HTTPException(400, str(exc)) from exc


class DraftRequest(BaseModel):
    """Which tested connection to assemble a report from (§74)."""

    connection_id: str
    artifact_type: str = "report"
    audience: str = "researcher"


class RegionRequest(BaseModel):
    """A named part of the board — a frame, a zone, a group (§54).

    One shape for all three, because they are one idea under three names. See
    `regions.py`.
    """

    name: str
    x: float
    y: float
    width: float
    height: float


class RegionMoveRequest(BaseModel):
    """Where a region is now. Its contents come with it."""

    x: float
    y: float


class RegionRenameRequest(BaseModel):
    name: str


class ArrangeRequest(BaseModel):
    """How to organise the board, said the way a researcher would say it."""

    phrase: str


class ArrangeApplyRequest(BaseModel):
    """The arrangement that was previewed, sent back to be written.

    The moves rather than the phrase, so what is applied is what was shown —
    recomputing here would let a card added since the preview be swept into an
    arrangement nobody looked at.
    """

    moves: list[dict[str, Any]]


class PlacementRequest(BaseModel):
    """Where an object sits on the workboard (§4).

    Coordinates are world units, not pixels — a board arranged on a laptop
    opens on a monitor with everything in the same relation.
    """

    object_id: str
    x: float
    y: float
    width: float
    height: float
    z: int | None = None


class MarkRequest(BaseModel):
    """A mark drawn on a paper (§204).

    `points` are in PDF user space, not screen pixels — see
    `throughline_domain.marks`, which refuses anything else, because a path in
    pixels is meaningful only at the zoom it was drawn at.
    """

    source_id: str
    page: int
    kind: str
    points: list[dict[str, float]]
    body: str | None = None


class ExcerptRequest(BaseModel):
    """A piece of a paper to keep (§205).

    `region` is in PDF user space, not screen pixels — see
    `throughline_domain.excerpts`, which refuses anything else that would make
    the record unpointable at a different zoom.
    """

    source_id: str
    page: int
    region: dict[str, float]
    citation: str
    context: str | None = None
    title: str | None = None


class PaperPdfRequest(BaseModel):
    """A paper to download. Validated further in the connector — see there."""

    url: str


class LiteratureSearch(BaseModel):
    query: str = Field(min_length=2, max_length=400)
    sources: list[str] = Field(default_factory=list, max_length=8)
    limit: int = Field(default=20, ge=1, le=50)


class ImportRequest(BaseModel):
    """One record chosen from a search, imported as a source."""
    title: str
    doi: str | None = None
    arxiv_id: str | None = None
    pmid: str | None = None
    url: str = ""
    pdf_url: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str = ""
    abstract: str = ""
    source: str = ""
    provenance: dict[str, str] = Field(default_factory=dict)


@app.get("/api/literature/sources")
def literature_sources(user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Which literature databases this installation can search, and how politely.

    Reported rather than assumed: a source that wants a contact address
    still works without one, it simply gets a worse rate limit, and saying so is
    more useful than either hiding it or refusing.
    """
    import throughline_connectors

    with transaction() as cur:
        contact = domain_settings.get(cur, "contact_email") or ""

    return {
        "sources": throughline_connectors.capabilities(mailto=contact),
        "contact_email": contact,
        "note": ("These are public APIs run on someone else's budget. "
                 "Throughline rate-limits itself to their published limits and "
                 "identifies itself when you give it an address to use."),
    }


@app.post("/api/literature/search", status_code=200)
def search_literature(payload: LiteratureSearch,
                      user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Search several literature databases at once.

    One source failing never empties the page: results arrive per source with a
    status, and a failure renders beside the results that did arrive. Records
    appearing in more than one database are merged on identifier, and where the
    sources disagree every value is kept rather than silently resolved.
    """
    import throughline_connectors

    with transaction() as cur:
        contact = domain_settings.get(cur, "contact_email") or ""

    try:
        return throughline_connectors.search(
            payload.query, sources=payload.sources or None,
            limit=payload.limit, mailto=contact)
    except Exception as exc:  # noqa: BLE001 — a search failure is not a crash
        raise HTTPException(502, f"The search could not be completed ({exc}).")


@app.get("/api/datasets/repositories")
def dataset_repositories(user: dict = Depends(current_user)) -> dict[str, Any]:
    """Which dataset repositories can be searched, and whether each curates."""
    from throughline_connectors.datasets import DATASET_CONNECTORS

    return {
        "repositories": [
            {"name": name, "curated": cls.curated,
             "note": ("Submissions are reviewed before publication."
                      if cls.curated else
                      "Anyone may deposit anything here, so a record typed as a "
                      "dataset is often a PDF or an archive.")}
            for name, cls in sorted(DATASET_CONNECTORS.items())
        ],
        "note": ("A dataset is not a paper. These results carry licence, file "
                 "formats and embargo status, because those decide whether the "
                 "data can answer anything — a title and a DOI do not."),
    }


@app.post("/api/datasets/search", status_code=200)
def search_dataset_repositories(
        payload: LiteratureSearch,
        user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Search dataset repositories.

    Deliberately a separate endpoint from the literature search rather than
    another source inside it. A dataset and a paper are different objects: one
    is cited, the other is computed on, and merging them into one result list
    would drop the licence, the file formats and the embargo status — which are
    exactly the fields that decide whether a dataset is usable at all.

    Results are not deduplicated across repositories. The same data deposited in
    two places is two records with different licences, files and versions.
    """
    from throughline_connectors.datasets import search_datasets

    with transaction() as cur:
        contact = domain_settings.get(cur, "contact_email") or ""

    try:
        return search_datasets(payload.query, sources=payload.sources or None,
                               limit=payload.limit, mailto=contact)
    except Exception as exc:  # noqa: BLE001 — a search failure is not a crash
        raise HTTPException(502, f"The search could not be completed ({exc}).")


class DatasetImportRequest(BaseModel):
    """One dataset record chosen from the repository search.

    The three fields beside the URL are the record's own, carried from the
    search result rather than re-fetched: what it is called, which repository
    it was found in, and the licence it states. `licence` is nullable and
    means "the repository did not say", which the dataset search is careful to
    distinguish from "open" — flattening it to an empty string here would
    undo that distinction one layer down.
    """

    url: str
    title: str = Field(min_length=1, max_length=300)
    repository: str = Field(min_length=1, max_length=60)
    licence: str | None = None


@app.post("/api/projects/{project_id}/datasets/import", status_code=202)
def import_dataset(project_id: str, payload: DatasetImportRequest,
                   user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Bring a dataset found on Find data into the project (D202).

    **The same door an upload uses**, deliberately: the bytes are registered
    with `storage.register_file`, the source is created by `objects.create_source`
    and ingestion is queued as `ingest.source`, so a dataset that arrived from
    Zenodo deduplicates, profiles, audits and appears in the Sources list
    exactly like one dragged off a desktop. A second path would be a second set
    of bugs — the reasoning `import_database_table` gives for writing a CSV out
    and handing it to the ordinary ingestion rather than teaching the pipeline
    a new kind of source.

    202 rather than 200 for the reason `upload_source` gives: the file is on
    disk and the work is durably queued, and nothing has been parsed yet.

    Which addresses may be fetched at all is decided in
    `throughline_domain.dataset_import`, where the SSRF reasoning lives — the
    host must belong to a repository this installation actually searches, and
    every redirect hop is checked again.
    """
    scoped_project(project_id, user)

    try:
        fetched = dataset_import.fetch_dataset(payload.url)
    except dataset_import.DatasetImportRefused as exc:
        # 422 with the sentence intact: every refusal names the host, the
        # format or the cap, and the screen shows it in place of the control
        # rather than translating it into "something went wrong" (§104).
        raise HTTPException(422, str(exc)) from exc
    except dataset_import.DatasetImportUnreachable as exc:
        # 502, as the dataset search itself answers when a repository is down:
        # nothing about this import was refused, the repository did not answer.
        raise HTTPException(502, str(exc)) from exc

    with transaction() as cur:
        record = storage.register_file(
            cur, project_id=project_id, filename=fetched.filename,
            stream=io.BytesIO(fetched.content), media_type=fetched.media_type)

        # Scoped to CONNECTOR, which is what `find_source_by_content_hash`
        # documents: an upload and a repository import of the same bytes are
        # two sources with different provenance, but importing the same record
        # twice is one act done twice — and answering it with the source that
        # already holds those bytes is the only way that does not leave a row
        # behind whose ingestion run belongs to something else.
        existing = objects.find_source_by_content_hash(
            cur, project_id=project_id,
            content_hash=str(record["content_hash"]),
            source_type=SourceType.CONNECTOR)
        if existing is not None:
            return {
                "source_id": existing["id"],
                "file": record,
                "workflow_run_id": existing["ingest_run_id"],
                "ingestion_status": existing["ingestion_status"],
                "source_reused": True,
                "note": (f"These bytes are already in this project as "
                         f"{existing['title']!r}, currently "
                         f"{existing['ingestion_status']}. No second source "
                         f"was created."),
            }

        source_id = objects.create_source(
            cur, project_id=project_id, source_type=SourceType.CONNECTOR,
            title=payload.title, actor=user["id"],
            original_uri=payload.url, connector_id=payload.repository,
            file_id=str(record["id"]),
            content_hash=str(record["content_hash"]),
            # Where it came from, kept on the source itself so the Sources list
            # can say so. `licence` is carried as the repository stated it,
            # null included: "not stated" is not "open".
            metadata={"repository": payload.repository,
                      "url": payload.url,
                      "downloaded_from": fetched.url,
                      "licence": payload.licence,
                      "filename": fetched.filename,
                      "size_bytes": fetched.size_bytes})
        run_id = workflow.enqueue(
            cur, workflow_name="ingest.source", project_id=project_id,
            payload={"source_id": source_id},
            # Keyed per source, for the reason the upload route gives at
            # length: a retried POST collapses into one run, and a source that
            # is created can never end up with no run to finish it.
            idempotency_key=f"ingest:{source_id}")
        events.audit(cur, project_id=project_id, actor=user["id"],
                     action="imported", object_type="source",
                     object_id=source_id,
                     detail={"repository": payload.repository,
                             "url": payload.url})

    return {
        "source_id": source_id, "file": record, "workflow_run_id": run_id,
        "ingestion_status": "uploaded",
        "source_reused": False,
        "note": (f"{fetched.filename} is being profiled. It will appear in "
                 f"Sources, with {payload.repository} and its licence "
                 f"recorded as where it came from."),
    }


@app.post("/api/literature/pdf")
def fetch_paper_pdf(payload: PaperPdfRequest,
                    user: dict = Depends(current_user)) -> Response:
    """The PDF behind a search result, fetched by this server.

    Server-side rather than from the browser because arXiv, Crossref and the
    rest send no CORS headers, so the page cannot read a response it is
    otherwise allowed to request.

    That makes this a URL supplied by a client and fetched from the server, so
    the destination is resolved and checked before every hop — see
    `throughline_connectors.papers`, where the reasoning and the redirect
    handling live. Behind authentication for the same reason: an unauthenticated
    fetcher is a fetcher for anybody who can reach the port.
    """
    from throughline_connectors.papers import PaperFetchError, fetch_pdf

    try:
        data = fetch_pdf(payload.url)
    except PaperFetchError as exc:
        # 400 rather than 502: a refused address and a paywalled paper are both
        # things the researcher can act on, and neither is this server failing.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return Response(
        content=data,
        media_type="application/pdf",
        # Never inline: the bytes go to PDF.js with scripting off, and letting
        # the browser render an untrusted PDF in this origin would undo that.
        headers={"Content-Disposition": "attachment"},
    )


# ---------------------------------------------------------------------------
# Reports and presentations (§74, §75)
#
# These five routes were the whole gap between a finished analysis and a
# document. Everything below this line already existed and was tested —
# `authoring` assembles a report from a tested connection, `communication`
# resolves every displayed value back to the run it came from, and
# `render_artifact` produces real .docx and .pptx bytes — and none of it was
# reachable, because the API never imported any of it. The Reports screen
# called these paths and received 404s.
# ---------------------------------------------------------------------------

def _owning_project(cur, table: str, row_id: str, missing: str) -> str:
    """
    Which project a row belongs to, or 404.

    Routes keyed by an artifact, a source or an alias cannot check ownership
    from the path alone: the id names a row, and the project has to be looked
    up before anybody can be asked whether it is theirs. Five such routes never
    did, and answered 200 to a second account — one of them rendered another
    researcher's report to a file.

    404 rather than 403, matching `scoped_project`: whether a row exists is
    itself something only its owner is entitled to know.
    """
    cur.execute(f"SELECT project_id FROM {table} WHERE id = %s", (row_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, missing)
    return str(row["project_id"])


@app.get("/api/projects/{project_id}/artifacts")
def list_artifacts(project_id: str,
                   user: dict = Depends(current_user)) -> list[dict[str, Any]]:
    """Every report and presentation in this project, newest first."""
    scoped_project(project_id, user)
    with transaction() as cur:
        cur.execute(
            """
            SELECT a.id, a.artifact_type, a.title, a.status, a.version,
                   a.created_at,
                   (SELECT COUNT(*) FROM artifact_blocks b
                     WHERE b.artifact_id = a.id) AS block_count,
                   (SELECT COUNT(*) FROM artifact_renders r
                     WHERE r.artifact_id = a.id) AS render_count
              FROM communication_artifacts a
             WHERE a.project_id = %s
          ORDER BY a.created_at DESC
            """,
            (project_id,))
        return [dict(row) for row in cur.fetchall()]


@app.post("/api/projects/{project_id}/artifacts/draft", status_code=201)
def draft_artifact(project_id: str, payload: DraftRequest,
                   user: dict = Depends(current_user)) -> dict[str, Any]:
    """Assemble a report from a tested connection (§74).

    Nothing here is written by a model. The narrative is built from the
    connection, its validation report and the runs behind them, so every
    sentence in the result is traceable to something that was computed.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            artifact_id = authoring.draft_from_connection(
                cur, project_id=project_id, connection_id=payload.connection_id,
                artifact_type=payload.artifact_type, audience=payload.audience)
        except (authoring.AuthoringError, communication.CommunicationError) as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"artifact_id": artifact_id}


@app.post("/api/artifacts/{artifact_id}/presentation", status_code=201)
def draft_presentation(artifact_id: str,
                       user: dict = Depends(current_user)) -> dict[str, Any]:
    """Re-cut an existing report as a talk (§75).

    A presentation is the same evidence at a different length, so it is derived
    from the report rather than assembled again — which is what keeps the slides
    and the paper referencing the same runs.
    """
    with transaction() as cur:
        cur.execute("SELECT project_id FROM communication_artifacts WHERE id = %s",
                    (artifact_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "There is no such report.")
        # Every other artifact route checks this and this one did not, so a
        # second account could re-cut somebody else's report and leave the
        # slides inside their project. `draft_presentation_from_report` looks
        # like it guards the boundary — it refuses a report from a different
        # project — but it was handed the report's *own* project_id, so the
        # comparison was against itself and could never fail.
        scoped_project(row["project_id"], user)
        try:
            new_id = authoring.draft_presentation_from_report(
                cur, project_id=row["project_id"], report_id=artifact_id)
        except (authoring.AuthoringError, communication.CommunicationError) as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"artifact_id": new_id}


@app.get("/api/artifacts/{artifact_id}")
def get_artifact(artifact_id: str,
                 user: dict = Depends(current_user)) -> dict[str, Any]:
    """An artifact, its resolved blocks, its integrity and its renders.

    Integrity travels with the document rather than behind a separate call: a
    reader deciding whether to export needs to know what will be refused, and a
    screen that has to ask twice tends to show one of the two.
    """
    with transaction() as cur:
        scoped_project(_owning_project(cur, "communication_artifacts", artifact_id,
                                       "There is no such document."), user)
        try:
            artifact = communication.load_artifact(cur, artifact_id, resolve=True)
        except communication.UnresolvedReference:
            """A reference that no longer resolves is the case a researcher
            most needs to *see*.

            Answering 404 — which this did first — says the report does not
            exist, when in fact it exists and one of its numbers has lost the
            run behind it. That is unfixable from the interface: the document
            cannot be opened to find out which block is at fault.

            So the blocks are returned unresolved and `integrity` below names
            the problem. Rendering still refuses; only reading is allowed."""
            artifact = communication.load_artifact(cur, artifact_id, resolve=False)
        except communication.CommunicationError as exc:
            raise HTTPException(404, str(exc)) from exc
        artifact["integrity"] = communication.check_integrity(cur, artifact_id)
        cur.execute(
            "SELECT id, fmt, storage_key, byte_size, resolved_hash, "
            "artifact_version, created_at FROM artifact_renders "
            "WHERE artifact_id = %s ORDER BY created_at DESC", (artifact_id,))
        artifact["renders"] = [dict(row) for row in cur.fetchall()]
        return artifact


@app.post("/api/artifacts/{artifact_id}/render")
def render_artifact_to_file(artifact_id: str, fmt: str = Query("markdown"),
                            user: dict = Depends(current_user)) -> dict[str, Any]:
    """Produce the document (§75).

    Refused, with the specific problems, when a value no longer traces to the
    run it came from — "3 blocks reference an analysis run that no longer
    exists" is actionable in a way that "export failed" is not, and a document
    that quietly published a stale number is the failure this product exists to
    prevent.
    """
    with transaction() as cur:
        scoped_project(_owning_project(cur, "communication_artifacts", artifact_id,
                                       "There is no such document."), user)
        try:
            return render_artifact.render(cur, artifact_id=artifact_id, fmt=fmt)
        except render_artifact.RenderError as exc:
            raise HTTPException(400, str(exc)) from exc
        except communication.CommunicationError as exc:
            raise HTTPException(404, str(exc)) from exc


@app.post("/api/artifacts/{artifact_id}/check-citations")
def check_artifact_citations(artifact_id: str,
                             user: dict = Depends(current_user)) -> dict[str, Any]:
    """Check every citation in this artifact still says what it is quoted for."""
    with transaction() as cur:
        scoped_project(_owning_project(cur, "communication_artifacts", artifact_id,
                                       "There is no such document."), user)
        # Through the link table: a citation belongs to the project and is
        # attached to blocks, so it can support more than one sentence.
        cur.execute(
            "SELECT c.id, b.template FROM artifact_blocks b "
            "JOIN block_citations bc ON bc.block_id = b.id "
            "JOIN citations c ON c.id = bc.citation_id "
            "WHERE b.artifact_id = %s",
            (artifact_id,))
        rows = list(cur.fetchall())
        checked = []
        for row in rows:
            try:
                checked.append(citations.check_entailment(
                    cur, row["id"], row["template"] or ""))
            except Exception as exc:  # noqa: BLE001 - reported, never fatal
                # One unresolvable citation must not stop the others being
                # checked; the researcher needs the whole picture.
                checked.append({"citation_id": row["id"], "error": str(exc)})
    return {"checked": len(checked), "citations": checked}


@app.get("/api/projects/{project_id}/citations/verify")
def verify_citations(project_id: str,
                     user: dict = Depends(current_user)) -> dict[str, Any]:
    """Which citations in this project still resolve (§73)."""
    scoped_project(project_id, user)
    with transaction() as cur:
        return citations.verify_project(cur, project_id)


@app.get("/api/analyses/{run_id}/reproduce.py")
def analysis_reproduction_script(run_id: str,
                                 user: dict = Depends(current_user)) -> Response:
    """
    The script that produces this analysis's number again (§75).

    Generated from the recorded spec rather than written by hand, so the script
    and the number have one source instead of two that can disagree — and
    refused by name for a method whose computation cannot be written out
    honestly in a few lines. A script that looked like the analysis and quietly
    did something else would be believed, which is worse than having none.
    """
    with transaction() as cur:
        cur.execute("SELECT project_id FROM analysis_runs WHERE id = %s",
                    (run_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Analysis run not found.")
        scoped_project(row["project_id"], user)
        try:
            text = code_export.for_run(cur, run_id)
        except code_export.CannotEmit as exc:
            # 409, not 404: the run exists and this is a considered refusal
            # that names the methods it can write, so the caller can act on it.
            raise HTTPException(409, str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
    return Response(
        content=text,
        media_type="text/x-python; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="{run_id}-reproduce.py"'},
    )


@app.get("/api/findings/{finding_id}/provenance.md")
def finding_provenance_log(finding_id: str,
                           user: dict = Depends(current_user)) -> Response:
    """
    The reproducibility record for one finding, as a file (§75).

    `/objects/{id}/provenance` answers "what did this come from" for a screen.
    This answers the question a methods section asks — what somebody would
    have to do to get the number again — and answers it as something they can
    attach: the analyses behind the finding, each with its method, the reason
    that method was chosen, the seed, the library versions and the input
    hashes §44 requires a run to record.

    A download rather than JSON. The audience is a person pasting this into a
    supplementary file; the structured form for machines already exists on the
    provenance route.
    """
    with transaction() as cur:
        cur.execute("SELECT project_id FROM findings WHERE id = %s",
                    (finding_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Finding not found.")
        scoped_project(row["project_id"], user)
        try:
            text = provenance_log.for_finding(cur, finding_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
    return Response(
        content=text,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="{finding_id}-provenance.md"'},
    )


@app.get("/api/projects/{project_id}/snapshot.zip")
def project_snapshot(project_id: str,
                     user: dict = Depends(current_user)) -> Response:
    """
    One project as one file, to keep or to carry to another machine (§75).

    `backup.sh` copies the whole installation — every project, including other
    researchers'. This is the one somebody actually asks for: *give me
    everything about this project*.

    A zip rather than JSON, because the records are only half of it. The files
    the project ingested travel beside them under `files/`, named by the
    storage key the records refer to, so the archive is self-contained: a
    snapshot describing analyses of a CSV nobody has is a description of work
    rather than the work.

    Built in memory. A project's records are small and its files are already
    on this disk; streaming would add a temporary file to clean up for no gain
    at the sizes involved, and the download is local.
    """
    import io
    import zipfile

    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            records = snapshot.as_json(cur, project_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        files = snapshot.files_in(cur, project_id)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project.json", records)
        missing: list[str] = []
        for entry in files:
            # `path_for` raises for an object that is not there rather than
            # returning a path to test, so the absence arrives as an exception
            # and is caught here — checked, not assumed.
            try:
                path = storage.path_for(entry["storage_key"])
                present = path.exists()
            except storage.StorageError:
                present = False
            if not present:
                # Recorded and gone. Named in the archive rather than silently
                # absent, because a reader counting files against the records
                # deserves to know which one this installation had lost.
                missing.append(f"{entry['storage_key']}  {entry['filename']}")
                continue
            archive.write(path, f"files/{entry['storage_key']}")
        if missing:
            archive.writestr(
                "files/MISSING.txt",
                "These files are recorded in project.json and were not on "
                "this machine when the snapshot was taken:\n\n"
                + "\n".join(missing) + "\n")

    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition":
                 f'attachment; filename="{project_id}-snapshot.zip"'},
    )


@app.get("/api/projects/{project_id}/results.csv")
def project_results_csv(project_id: str,
                        user: dict = Depends(current_user)) -> Response:
    """
    Every connection this project tested, as a spreadsheet (§75).

    Returned as a real file download rather than as text in JSON, unlike the
    bibliography beside it. A `.bib` is short and worth reading on screen
    before it is saved; a results table is opened in Excel, R or a paper's
    supplementary material, and asking somebody to copy a textarea into a file
    is asking them to introduce a transcription error into their own results.

    `text/csv` with a filename carrying the project id, so a researcher with
    three of these in a downloads folder can tell them apart.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        text = tables.connections_csv(cur, project_id)
    return Response(
        content=text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="{project_id}-results.csv"'},
    )


@app.get("/api/projects/{project_id}/bibliography")
def project_bibliography(project_id: str,
                         user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Every paper this project cited, as a `.bib` file.

    `bibliography.py` has produced this for some time — stable keys, missing
    fields omitted rather than guessed, and braces escaped so one malformed
    scraped title cannot swallow the rest of the file. Nothing called it. There
    was no route and no control, so a researcher who had done the work could
    not get their citations out to the thing they write in.

    Returned as text in JSON rather than as a file download: the interface
    shows it, and a researcher can read it before saving it. A .bib that turns
    out to be a comment saying nothing was cited is better discovered on screen
    than in a submission.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        text = bibliography.as_bibtex(cur, project_id)
        found = bibliography.entries(cur, project_id)
    # `entries()` returns {entries, citations, papers}; the count is `papers`,
    # not the length of that mapping — which is what the first version of this
    # returned, giving three for every project on earth.
    return {
        "bibtex": text,
        "entries": found["papers"],
        "citations": found["citations"],
        # Which references are short a year, a journal or an author. The module
        # works this out per entry and nothing was showing it: a .bib that
        # renders as "Lovelace, ?" is found in the proofs otherwise.
        "incomplete": [
            {"key": e["key"], "title": e["title"], "missing": e["missing_fields"]}
            for e in found["entries"] if e["missing_fields"]
        ],
    }


# ---------------------------------------------------------------------------
# The workboard (§4, §109)
#
# §109 puts this at Phase 0, before gesture and before Air Ink, and it was never
# built — so the objects a project accumulates have existed in a list and never
# in a place. A placement is a view over an object rather than an object: taking
# something off the board removes its position and nothing else.
# ---------------------------------------------------------------------------

@app.get("/api/projects/{project_id}/board")
def read_board(project_id: str,
               user: dict = Depends(current_user)) -> dict[str, Any]:
    """Everything on this project's board, bottom to top."""
    scoped_project(project_id, user)
    with transaction() as cur:
        return {"placements": board.for_project(cur, project_id=project_id)}


@app.get("/api/projects/{project_id}/board/available")
def board_available(project_id: str,
                    user: dict = Depends(current_user)) -> dict[str, Any]:
    """What this project has that is not on the board yet."""
    scoped_project(project_id, user)
    with transaction() as cur:
        return {"objects": board.available(cur, project_id=project_id)}


@app.put("/api/projects/{project_id}/board")
def place_on_board(project_id: str, payload: PlacementRequest,
                   user: dict = Depends(current_user)) -> dict[str, Any]:
    """Put an object on the board, or move one already there.

    PUT rather than POST because it is idempotent by design: a drag emits a
    position repeatedly, and the same object at the same place is the same
    board however many times it is said.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            return board.place(
                cur, project_id=project_id, object_id=payload.object_id,
                x=payload.x, y=payload.y, width=payload.width,
                height=payload.height, z=payload.z, actor=user["id"])
        except board.BoardError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.post("/api/projects/{project_id}/board/{object_id}/front")
def raise_on_board(project_id: str, object_id: str,
                   user: dict = Depends(current_user)) -> dict[str, Any]:
    """Bring a card above everything else."""
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            return {"z": board.bring_to_front(
                cur, project_id=project_id, object_id=object_id)}
        except board.BoardError as exc:
            raise HTTPException(404, str(exc)) from exc


@app.delete("/api/projects/{project_id}/board/{object_id}")
def take_off_board(project_id: str, object_id: str,
                   user: dict = Depends(current_user)) -> dict[str, Any]:
    """Take something off the board.

    The object itself is untouched — this removes a position, not a piece of
    research. Answering 404 for something that was never placed matters: "it is
    off the board now" and "it was never on it" are different answers to
    somebody who believes they just removed something.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        removed = board.remove(cur, project_id=project_id, object_id=object_id)
    if not removed:
        raise HTTPException(404, "That object is not on this board.")
    return {"removed": object_id}


# ---------------------------------------------------------------------------
# Named regions, and tidying (§54)
# ---------------------------------------------------------------------------

@app.post("/api/projects/{project_id}/board/{object_id}/back")
def lower_on_board(project_id: str, object_id: str,
                   user: dict = Depends(current_user)) -> dict[str, Any]:
    """Send a card behind everything else.

    The counterpart of `/front`, which had none: a card dropped on top of a
    frame could be raised for ever and never put back underneath.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            return {"z": board.send_to_back(
                cur, project_id=project_id, object_id=object_id)}
        except board.BoardError as exc:
            raise HTTPException(404, str(exc)) from exc


@app.get("/api/projects/{project_id}/board/regions")
def read_regions(project_id: str,
                 user: dict = Depends(current_user)) -> dict[str, Any]:
    """Every named region, in draw order, with what each one holds."""
    scoped_project(project_id, user)
    with transaction() as cur:
        found = regions.for_project(cur, project_id=project_id)
        for region in found:
            region["members"] = regions.members(
                cur, project_id=project_id, region_id=region["id"])
        return {"regions": found}


@app.post("/api/projects/{project_id}/board/regions")
def draw_region(project_id: str, payload: RegionRequest,
                user: dict = Depends(current_user)) -> dict[str, Any]:
    """Draw a named region: a frame, a zone, or a group of what is inside it."""
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            return regions.create(
                cur, project_id=project_id, name=payload.name, x=payload.x,
                y=payload.y, width=payload.width, height=payload.height,
                actor=user["id"])
        except regions.RegionError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.put("/api/projects/{project_id}/board/regions/{region_id}")
def move_region(project_id: str, region_id: str, payload: RegionMoveRequest,
                user: dict = Depends(current_user)) -> dict[str, Any]:
    """Move a region, carrying everything inside it."""
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            return regions.move(cur, project_id=project_id,
                                region_id=region_id, x=payload.x, y=payload.y,
                                actor=user["id"])
        except regions.RegionError as exc:
            raise HTTPException(404, str(exc)) from exc


@app.patch("/api/projects/{project_id}/board/regions/{region_id}")
def rename_region(project_id: str, region_id: str,
                  payload: RegionRenameRequest,
                  user: dict = Depends(current_user)) -> dict[str, Any]:
    """Say what this part of the board is for now."""
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            return regions.rename(cur, project_id=project_id,
                                  region_id=region_id, name=payload.name,
                                  actor=user["id"])
        except regions.RegionError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.delete("/api/projects/{project_id}/board/regions/{region_id}")
def erase_region(project_id: str, region_id: str,
                 user: dict = Depends(current_user)) -> dict[str, Any]:
    """Remove a region and leave its cards where they are."""
    scoped_project(project_id, user)
    with transaction() as cur:
        removed = regions.remove(cur, project_id=project_id,
                                 region_id=region_id)
    if not removed:
        raise HTTPException(404, "That region is not on this board.")
    return {"removed": region_id}


@app.post("/api/projects/{project_id}/board/arrangement")
def preview_arrangement(project_id: str, payload: ArrangeRequest,
                        user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Where a tidy would put everything, without moving anything.

    A preview, because §54 asks for one and because a rearrangement nobody can
    look at first is one nobody runs twice. A phrase this does not understand
    is refused by name rather than answered with an arrangement that means
    nothing.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            return arrange.plan(cur, project_id=project_id,
                                phrase=payload.phrase)
        except arrange.ArrangeError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.put("/api/projects/{project_id}/board/arrangement")
def confirm_arrangement(project_id: str, payload: ArrangeApplyRequest,
                        user: dict = Depends(current_user)) -> dict[str, Any]:
    """Write down the arrangement that was previewed."""
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            moved = arrange.apply(cur, project_id=project_id,
                                  moves=payload.moves, actor=user["id"])
        except (arrange.ArrangeError, board.BoardError) as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"moved": moved}


@app.post("/api/projects/{project_id}/marks", status_code=201)
def keep_mark(project_id: str, payload: MarkRequest,
              user: dict = Depends(current_user)) -> dict[str, Any]:
    """Keep something drawn on a paper (§204).

    Marks were previously held only in the browser, so closing a paper erased
    everything written on it — an annotation that does not survive being closed
    is a demonstration of one.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        _sources_in_project(cur, project_id=project_id, source_ids=[payload.source_id])
        try:
            return marks.record(
                cur, project_id=project_id, source_id=payload.source_id,
                page=payload.page, kind=payload.kind, points=payload.points,
                body=payload.body, actor=user["id"])
        except marks.MarkError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/sources/{source_id}/marks")
def list_marks(source_id: str,
               user: dict = Depends(current_user)) -> dict[str, Any]:
    """Everything drawn on this paper, oldest first, so it redraws as written."""
    with transaction() as cur:
        scoped_project(_owning_project(cur, "sources", source_id,
                                       "No such paper."), user)
        return {"marks": marks.for_source(cur, source_id=source_id)}


@app.delete("/api/projects/{project_id}/marks/{mark_id}")
def rub_out_mark(project_id: str, mark_id: str,
                 user: dict = Depends(current_user)) -> dict[str, Any]:
    """Rub out a mark.

    Scoped by project as well as id: an identifier is not an authorisation, and
    a mark id kept from another workspace should delete nothing.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        removed = marks.remove(cur, mark_id=mark_id, project_id=project_id)
    if not removed:
        raise HTTPException(404, "There is no such mark on this project.")
    return {"removed": mark_id}


@app.post("/api/projects/{project_id}/excerpts", status_code=201)
def keep_excerpt(project_id: str, payload: ExcerptRequest,
                 user: dict = Depends(current_user)) -> dict[str, Any]:
    """Put a circled piece of a paper on the board (§205).

    The five things §205 names — source paper, page, bounding region, citation,
    original context — are all required here rather than filled in with
    defaults. An excerpt that could not say where it came from would sit on the
    board looking exactly like one that could.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        _sources_in_project(cur, project_id=project_id, source_ids=[payload.source_id])
        try:
            return excerpts.record(
                cur,
                project_id=project_id,
                source_id=payload.source_id,
                page=payload.page,
                region=payload.region,
                citation=payload.citation,
                context=payload.context,
                title=payload.title,
                actor=user["id"],
            )
        except excerpts.ExcerptError as exc:
            # 400: an incomplete excerpt is something the researcher can fix,
            # and every refusal names the missing thing.
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/projects/{project_id}/excerpts")
def list_excerpts(project_id: str,
                  user: dict = Depends(current_user)) -> dict[str, Any]:
    """Everything taken from papers in this project, newest first."""
    scoped_project(project_id, user)
    with transaction() as cur:
        return {"excerpts": excerpts.for_project(cur, project_id=project_id)}


@app.post("/api/projects/{project_id}/literature/import", status_code=201)
def import_record(project_id: str, payload: ImportRequest,
                  user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Bring a search result into the project as a source.

    The record's metadata is stored with its field-level provenance intact, so a
    disagreement between databases about a year or an author list survives into
    the workspace rather than being flattened on the way in.

    Only metadata is imported. Fetching a PDF is a separate, explicit act: this
    never routes around a paywall, and an open-access link is offered rather
    than followed automatically.
    """
    scoped_project(project_id, user)

    identifier = payload.doi or payload.arxiv_id or payload.pmid
    with transaction() as cur:
        if identifier:
            cur.execute(
                "SELECT id, title FROM sources WHERE project_id = %s "
                "AND external_identifier = %s", (project_id, identifier))
            existing = cur.fetchone()
            if existing:
                # Idempotent: importing the same paper twice from two searches
                # would double-count it in every synthesis downstream.
                return {"source_id": existing["id"], "title": existing["title"],
                        "already_present": True}

        source_id = new_id("src")
        cur.execute(
            "INSERT INTO sources(id, project_id, source_type, title, "
            "original_uri, external_identifier, ingestion_status, metadata) "
            "VALUES (%s, %s, 'connector', %s, %s, %s, 'ready', %s)",
            (source_id, project_id, payload.title,
             payload.url or payload.pdf_url, identifier,
             jsonb({"authors": payload.authors, "year": payload.year,
                    "venue": payload.venue, "abstract": payload.abstract,
                    "doi": payload.doi, "arxiv_id": payload.arxiv_id,
                    "pmid": payload.pmid, "pdf_url": payload.pdf_url,
                    "found_via": payload.source,
                    "field_provenance": payload.provenance})))
        return {"source_id": source_id, "title": payload.title,
                "already_present": False}


class DatasetSetRequest(BaseModel):
    dataset_version_ids: list[str] = Field(min_length=2, max_length=8)


class ImageSetRequest(BaseModel):
    source_ids: list[str] = Field(min_length=2, max_length=20)


@app.post("/api/projects/{project_id}/dataset-synthesis", status_code=201)
def compare_datasets(project_id: str, payload: DatasetSetRequest,
                     user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Compare several datasets side by side.

    Reports the **ceiling** — the weakest pair — rather than an average.
    Someone planning to pool five datasets needs to know that two of them
    cannot be compared at all, and a mean across ten pairs hides precisely that.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            return synthesis.dataset_matrix(
                cur, project_id=project_id,
                version_ids=payload.dataset_version_ids)
        except synthesis.SynthesisError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.post("/api/projects/{project_id}/image-comparison", status_code=201)
def compare_images(project_id: str, payload: ImageSetRequest,
                   user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Compare figures for reuse, rotation and shared regions.

    Every positive outcome is phrased as similarity and routed to *needs
    review*. This system never asserts that an image was manipulated: the
    exposure from a false accusation is asymmetric and severe, and only a person
    can say what a pixel relationship means.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        loaded = []
        for source_id in payload.source_ids:
            # The bytes live in `files`, joined through `sources.file_id` —
            # sources record what a thing is, files record where it is.
            cur.execute(
                "SELECT s.id, s.title, s.content_hash, f.storage_key, "
                "       f.media_type "
                "FROM sources s LEFT JOIN files f ON f.id = s.file_id "
                "WHERE s.id = %s AND s.project_id = %s",
                (source_id, project_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(
                    404, f"{source_id} is not a source in this project.")
            if not row["storage_key"]:
                raise HTTPException(
                    400, f"{row['title']} has no stored file to compare.")
            loaded.append({
                "id": row["id"], "title": row["title"],
                "content_hash": row["content_hash"],
                "path": str(storage.path_for(row["storage_key"])),
            })

    try:
        return images.compare_many(loaded)
    except images.ImageError as exc:
        raise HTTPException(400, str(exc)) from exc


class ReconcileRequest(BaseModel):
    """Two located claims, as they travel back for reconciliation."""
    left: ClaimPayload
    right: ClaimPayload


class ReconcilePapersRequest(BaseModel):
    left_source_id: str
    right_source_id: str


@app.post("/api/projects/{project_id}/reconcile", status_code=201)
def reconcile_claims(project_id: str, payload: ReconcileRequest,
                     user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Can these two papers' claims be compared, and if so do they agree? (Pair 3)

    Every citation tool can show that two papers relate. This says why they
    cannot be compared — different constructs, populations, outcome definitions
    or estimands — which is the answer a reviewer actually needs and the one
    nobody offers. Deterministic: a model located the claims; it does not
    adjudicate them.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        return reconcile.reconcile(
            cur, project_id=project_id,
            left=payload.left.model_dump(), right=payload.right.model_dump())


@app.post("/api/projects/{project_id}/reconcile-papers", status_code=201)
def reconcile_papers(project_id: str, payload: ReconcilePapersRequest,
                     user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Locate the claims in two papers and reconcile every comparable pair.

    The location step needs a model; everything after it does not. A paper with
    no locatable claim is reported as such (P13) rather than silently producing
    an empty comparison.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        located = {}
        for side, source_id in (("left", payload.left_source_id),
                                ("right", payload.right_source_id)):
            try:
                # The record, and a reading only if there is none. Comparing
                # two papers is not a request to re-read them, and re-reading
                # can change the claims the comparison is about.
                located[side] = claim_test.claims_for(
                    cur, project_id=project_id, source_id=source_id)
            except claim_test.ClaimTestError as exc:
                raise HTTPException(400, str(exc)) from exc

        pairs = []
        for left in located["left"]["claims"]:
            for right in located["right"]["claims"]:
                pairs.append(reconcile.reconcile(
                    cur, project_id=project_id,
                    left={**left, "source_title": located["left"]["source_title"]},
                    right={**right,
                           "source_title": located["right"]["source_title"]}))

        return {
            "left": {"source_id": payload.left_source_id,
                     "title": located["left"]["source_title"],
                     "claims": located["left"]["claims"],
                     "verdict": located["left"].get("verdict")},
            "right": {"source_id": payload.right_source_id,
                      "title": located["right"]["source_title"],
                      "claims": located["right"]["claims"],
                      "verdict": located["right"].get("verdict")},
            "reconciliations": pairs,
            "model": located["left"]["model"],
            # Which sides had to be read to answer this, so the interface can
            # say whether it is comparing a fresh reading or a recorded one.
            "read_now": [side for side in ("left", "right")
                         if located[side].get("read_now")],
        }


class ConsistencyRequest(BaseModel):
    left_connection_id: str
    right_connection_id: str


@app.post("/api/projects/{project_id}/consistency", status_code=201)
def compare_two_results(project_id: str, payload: ConsistencyRequest,
                        user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Are these two results consistent, and does that mean anything? (Pair 4)

    Every check reads state only this system holds — which version of a file
    each used, how a column was harmonised that day, how many comparisons had
    been made by then. That is why a divergence can arrive already carrying its
    most likely explanation instead of as a mystery.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            return consistency.compare_results(
                cur, project_id=project_id,
                left_id=payload.left_connection_id,
                right_id=payload.right_connection_id)
        except consistency.ConsistencyError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.get("/api/projects/{project_id}/consistency")
def project_consistency(project_id: str, limit: int = Query(20, ge=1, le=100),
                        user: dict = Depends(current_user)) -> dict[str, Any]:
    """Every pair of results in this project worth a second look."""
    scoped_project(project_id, user)
    with transaction() as cur:
        return consistency.inconsistencies(cur, project_id, limit=limit)


class NewNote(BaseModel):
    title: str
    body: str = ""


class NoteEdit(BaseModel):
    body: str


@app.get("/api/projects/{project_id}/notebook")
def list_notes(project_id: str,
               user: dict = Depends(current_user)) -> dict[str, Any]:
    """Every note, plus the links that point at nothing yet."""
    scoped_project(project_id, user)
    with transaction() as cur:
        return {"notes": notebook.listing(cur, project_id),
                "unresolved": notebook.unresolved(cur, project_id)}


@app.post("/api/projects/{project_id}/notebook", status_code=201)
def create_note(project_id: str, payload: NewNote,
                user: dict = Depends(current_user)) -> dict[str, Any]:
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            return notebook.create(cur, project_id=project_id,
                                   title=payload.title, body=payload.body,
                                   author=user["id"])
        except notebook.NotebookError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.get("/api/projects/{project_id}/notebook/today")
def todays_note(project_id: str,
                user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Today's page, created if it does not exist.

    A GET that creates is unusual and correct here: the whole point of a daily
    note is that it is already there, and anything that asks a question before
    you can type has lost.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        page = notebook.daily(cur, project_id=project_id, author=user["id"])
        return notebook.get(cur, page["id"])


@app.get("/api/notes/{note_id}")
def read_note(note_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    with transaction() as cur:
        try:
            note = notebook.get(cur, note_id)
        except notebook.NotebookError as exc:
            raise HTTPException(404, str(exc)) from exc
        scoped_project(note["project_id"], user)
        return note


@app.patch("/api/notes/{note_id}")
def edit_note(note_id: str, payload: NoteEdit,
              user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Revise a notebook page.

    Annotations attached to an object are refused here: those are part of the
    record and are never edited. A notebook page is a working document.
    """
    with transaction() as cur:
        cur.execute("SELECT project_id FROM notes WHERE id = %s", (note_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "No such note.")
        scoped_project(row["project_id"], user)
        try:
            notebook.update(cur, note_id=note_id, body=payload.body,
                            author=user["id"])
        except notebook.NotebookError as exc:
            raise HTTPException(409, str(exc)) from exc
        return notebook.get(cur, note_id)


@app.get("/api/projects/{project_id}/notebook/index")
def notebook_index(project_id: str,
                   user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Where to start in the notebook, computed rather than kept.

    A written index is a second copy of the truth and can therefore be wrong —
    it goes stale on the first rename and nothing about reading it reveals
    that. Deriving it means it never needs maintaining and never misleads.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        return notebook.index(cur, project_id)


@app.get("/api/projects/{project_id}/notebook/lint")
def notebook_lint(project_id: str,
                  user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    What is wrong with the notebook, without changing any of it.

    A read-only report on purpose. A stale note may be exactly right and the
    new evidence wrong; an isolated note may be a deliberate scratch page. A
    researcher who finds their own notes rewritten stops trusting the notebook,
    which costs more than every problem this reports.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        return notebook.lint(cur, project_id)


@app.get("/api/projects/{project_id}/notebook/graph")
def notebook_graph(project_id: str,
                   user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    The notebook as a graph, with its edges labelled as asserted.

    Kept separate from the provenance graph so an assertion can never be
    mistaken for a derivation.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        return notebook.graph(cur, project_id)


@app.get("/api/objects/{object_id}/mentions")
def object_mentions(object_id: str,
                    user: dict = Depends(current_user)) -> list[dict[str, Any]]:
    """Notes that mention this object by name."""
    with transaction() as cur:
        cur.execute("SELECT project_id FROM research_objects WHERE id = %s",
                    (object_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "No such object.")
        scoped_project(row["project_id"], user)
        return notebook.object_backlinks(cur, object_id)


@app.get("/api/projects/{project_id}/patterns")
def project_patterns(project_id: str,
                     user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Structural patterns across everything computed in this project.

    Deterministic and read-only: every number comes from a result already
    computed inside a correction family. A pattern search that ran its own tests
    would be the purest form of the multiplicity problem it exists to warn
    about.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        return patterns.detect(cur, project_id)


@app.get("/api/projects/{project_id}/key-findings")
def project_key_findings(project_id: str, limit: int = Query(8, ge=1, le=50),
                         user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    The results most worth attention, each with the patterns that argue against
    it. Ranked by evidence rather than effect size.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        return patterns.key_findings(cur, project_id, limit=limit)


class AliasSuggestion(BaseModel):
    phrase: str
    canonical_variable_id: str
    origin: str = "paper"
    origin_ref: str | None = None


class AliasDecision(BaseModel):
    status: str


@app.get("/api/projects/{project_id}/vocabulary")
def project_vocabulary(project_id: str,
                       user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    What this project has learned to call things — the one part that improves.

    Reported with its usage count so the claim can be checked rather than
    believed, and with an explicit statement of what does *not* learn.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        return {"pending": vocabulary.pending(cur, project_id),
                # The variables an alias can point at. Without these a term can
                # be decided and never proposed: a proposal names what the
                # phrase means, and no id had ever left the server.
                "variables": vocabulary.variables(cur, project_id),
                **vocabulary.learned(cur, project_id)}


@app.post("/api/projects/{project_id}/vocabulary", status_code=201)
def suggest_alias(project_id: str, payload: AliasSuggestion,
                  user: dict = Depends(current_user)) -> dict[str, Any]:
    """Propose that a phrase names a canonical variable. Resolves nothing yet."""
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            suggested = vocabulary.suggest(
                cur, project_id=project_id, phrase=payload.phrase,
                canonical_variable_id=payload.canonical_variable_id,
                origin=payload.origin, origin_ref=payload.origin_ref,
                created_by=user["id"])
        except vocabulary.AliasRefused as exc:
            # The domain's own sentence: it names the variable the phrase
            # already belongs to, which the generic refusal below cannot.
            raise HTTPException(409, str(exc)) from exc
        if suggested is None:
            raise HTTPException(409, (
                f"{payload.phrase!r} already has a ruling in this project. A "
                "rejected term is not re-proposed by the next paper that uses "
                "it."))
        return suggested


@app.post("/api/vocabulary/{alias_id}/decide")
def decide_alias(alias_id: str, payload: AliasDecision,
                 user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Approve or reject an alias — the moment the vocabulary actually changes.

    Attributed and dated, because from here on it silently resolves terms in
    every future paper, and a reader is entitled to know who decided that.
    """
    with transaction() as cur:
        scoped_project(_owning_project(cur, "variable_aliases", alias_id,
                                       "No such term."), user)
        try:
            decided = vocabulary.decide(cur, alias_id=alias_id,
                                        status=payload.status,
                                        decided_by=user["id"])
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return decided


class ModelChoice(BaseModel):
    provider: str = "ollama"
    model: str | None = None


@app.get("/api/system/models")
def available_models(user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Every model this machine can actually run, and which one is selected.

    The whole point of a local-first deployment is that the researcher chooses.
    A PhD student on a laptop and a lab with a workstation are running the same
    software against very different hardware, and the system reports what is
    installed rather than assuming.
    """
    import throughline_model
    from throughline_model.ollama import OllamaProvider
    from throughline_model.provider import ModelUnavailable

    installed: list[dict[str, Any]] = []
    note = None
    try:
        installed = OllamaProvider().installed()
    except ModelUnavailable as exc:
        note = str(exc)

    capability = throughline_model.capability()
    with transaction() as cur:
        saved = domain_settings.get(cur, domain_settings.MODEL)
        changes = domain_settings.history(cur, domain_settings.MODEL, limit=10)
        key_hint = domain_secrets.hint(cur, domain_secrets.ANTHROPIC_API_KEY)

    from throughline_model.anthropic_provider import DEFAULT_MODEL as HOSTED_MODEL

    return {
        "installed": installed,
        "selection": throughline_model.selection(),
        "saved": saved,
        # The hosted option, described rather than hidden. `key_hint` is the
        # last four characters and never the key: enough for a researcher to
        # tell which credential is saved, useless to anyone reading the screen.
        #
        # `billed` is here because the single most likely misunderstanding is
        # that this uses a Claude subscription. It does not — there is no way
        # for a third-party application to spend one — and a researcher who
        # learns that from a bill rather than from this line has been misled by
        # the interface that was supposed to be the trustworthy part.
        "hosted": {
            "provider": "anthropic",
            "model": HOSTED_MODEL,
            "key_saved": key_hint is not None,
            "key_hint": key_hint,
            "local": False,
            "billed": "Billed per token to an Anthropic API account. This is "
                      "not a Claude subscription; a subscription cannot be "
                      "used here.",
            "warning": "Text from your papers and datasets is sent to "
                       "Anthropic. Do not select this for data you are not "
                       "permitted to send off this machine.",
        },
        "active": {"name": capability.name, "model": capability.model,
                   "usable": capability.text, "local": capability.local,
                   "structured": capability.structured, "note": capability.note},
        # Swapping the model changes what the system produces, so the swaps are
        # part of the audit trail rather than a hidden preference.
        "history": changes,
        "note": note,
        "how_to_install": "ollama pull <model>",
    }


@app.put("/api/system/models")
def choose_model(payload: ModelChoice,
                 user: dict = Depends(admin_user)) -> dict[str, Any]:
    """
    Point the system at a different model, effective immediately and after a
    restart.

    Refuses a model that is not installed. Accepting one would produce a system
    that looks configured and fails at the moment of use — exactly the fake
    capability  forbids.
    """
    import throughline_model
    from throughline_model.ollama import OllamaProvider
    from throughline_model.provider import ModelUnavailable

    if payload.provider == "ollama" and payload.model:
        try:
            names = {m["name"] for m in OllamaProvider().installed()}
        except ModelUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        if payload.model not in names and not any(
                n.split(":")[0] == payload.model.split(":")[0] for n in names):
            raise HTTPException(400, (
                f"{payload.model} is not installed on this machine. Install it "
                f"with `ollama pull {payload.model}`, or choose one of: "
                + ", ".join(sorted(names))))

    if payload.provider == "anthropic":
        import os

        with transaction() as cur:
            key = domain_secrets.get_secret(cur, domain_secrets.ANTHROPIC_API_KEY)
        if not key and not os.environ.get("ANTHROPIC_API_KEY"):
            raise HTTPException(400, (
                "No API key is saved for the hosted model. Add one first. It "
                "is billed per token to an Anthropic API account — a Claude "
                "subscription cannot be used here."))
        if key:
            throughline_model.configure(api_key=key)

    previous = throughline_model.selection()
    throughline_model.configure(provider=payload.provider, model=payload.model)
    capability = throughline_model.provider(refresh=True).capability()

    # The same rule the Ollama branch above applies, and for the same reason:
    # a provider that cannot answer turns "this feature needs a model" into a
    # runtime error at the moment of use. A key that is present but rejected,
    # or an account with no credit, both land here — so the check is whether
    # the thing actually works, not whether it was configured.
    if not capability.text:
        throughline_model.configure(provider=previous["provider"] or "ollama",
                                    model=previous["model"])
        throughline_model.provider(refresh=True)
        raise HTTPException(400, (
            capability.note
            or f"{payload.provider} is configured but did not answer, so the "
               "selection was left where it was."))

    with transaction() as cur:
        domain_settings.set_value(
            cur, domain_settings.MODEL,
            {"provider": payload.provider, "model": payload.model},
            changed_by=user["id"])

    return {"selection": throughline_model.selection(),
            "active": {"name": capability.name, "model": capability.model,
                       "usable": capability.text, "note": capability.note}}


class ModelKey(BaseModel):
    """
    A credential for the hosted model.

    Write-only by construction: there is no response model that carries it back
    and no endpoint that returns it. The interface confirms a key is saved by
    showing its last four characters, which the researcher can match against
    their console without the value being recoverable from the screen.
    """
    api_key: str


@app.put("/api/system/model-key")
def save_model_key(payload: ModelKey,
                   user: dict = Depends(admin_user)) -> dict[str, Any]:
    """
    Save the hosted model's API key. Does not select the hosted model.

    Saving a credential and deciding to send unpublished research off the
    machine are two different decisions, and collapsing them would make the
    second one happen as a side effect of the first. The key sits unused until
    the researcher selects the hosted provider deliberately.

    Not written to `setting_history`: that table keeps every previous value
    forever, so a key stored through it would outlive its own revocation.
    """
    import throughline_model

    key = payload.api_key.strip()
    if not key:
        raise HTTPException(400, "That is empty. Remove the key instead.")

    with transaction() as cur:
        domain_secrets.set_secret(cur, domain_secrets.ANTHROPIC_API_KEY, key,
                                  changed_by=user["id"])
    throughline_model.configure(api_key=key)

    # Deliberately returns the hint rather than the key, and says plainly that
    # nothing has changed about where data goes.
    with transaction() as cur:
        hint = domain_secrets.hint(cur, domain_secrets.ANTHROPIC_API_KEY)
    return {"key_saved": True, "key_hint": hint,
            "note": "Saved. Nothing is sent anywhere until you select the "
                    "hosted model."}


@app.delete("/api/system/model-key")
def clear_model_key(user: dict = Depends(admin_user)) -> dict[str, Any]:
    """
    Remove the key, and stop using the hosted model if it was selected.

    Leaving the selection pointing at a provider whose credential has just been
    removed would produce exactly the fake capability the rest of this endpoint
    refuses: configured, displayed as active, failing at the moment of use.
    """
    import throughline_model

    with transaction() as cur:
        removed = domain_secrets.clear_secret(cur, domain_secrets.ANTHROPIC_API_KEY)
    throughline_model.configure(api_key="")

    reverted = False
    if (throughline_model.selection()["provider"] or "") == "anthropic":
        throughline_model.configure(provider="ollama", model=None)
        throughline_model.provider(refresh=True)
        with transaction() as cur:
            domain_settings.set_value(
                cur, domain_settings.MODEL,
                {"provider": "ollama", "model": None}, changed_by=user["id"])
        reverted = True

    return {"key_saved": False, "removed": removed, "reverted_to_local": reverted,
            "note": "Removed." + (" The system is back on the local model."
                                  if reverted else "")}


@app.get("/api/health")
def health() -> dict[str, Any]:
    """
    Liveness and readiness, per dependency.

    Unauthenticated on purpose — an orchestrator has no session — and it
    deliberately returns 200 while degraded. A workspace with no model can still
    do every deterministic thing in the system, and restarting it would fix
    nothing while losing in-flight work. Only an unreachable record is a reason
    to take the process out of rotation.
    """
    report = observability.health()
    if report["status"] == "unhealthy":
        return JSONResponse(status_code=503, content=report)
    return report


@app.get("/api/system/capabilities")
def capabilities() -> dict[str, Any]:
    """What this installation can actually do right now.

    The interface reads this instead of assuming: a missing embedding model
    means search is lexical, and the researcher is told so rather than being
    quietly given worse results.
    """
    embedder = embeddings.provider()
    from throughline_visual.renderers import blender as blender_renderer

    return {
        # An external application rather than a Python package, so it is found
        # on the filesystem and asked its version — `extras` would report it
        # present the moment anything shipped a module of the same name.
        "blender": blender_renderer.availability(),
        "retrieval": {
            "lexical": True,
            "semantic": embedder is not None,
            "model": embedder.name if embedder else None,
            "note": None if embedder else
                    "No local embedding model installed — search is lexical only.",
        },
        # Stated explicitly so nothing downstream mistakes absence for silence.
        "analysis": {
            "sandbox": True,
            "methods": sorted(analysis.SUPPORTED_METHODS),
            # Which variables each method needs, so an interface can ask for
            # them without keeping its own copy of this map. "variables" means
            # something different for a correlation and a regression, and a
            # second copy in the client is a second thing to keep in step —
            # the one place that does copy such a map needed a test to stop
            # the two drifting.
            "method_variables": analysis.method_variables(),
            # the honest limits of a desktop process sandbox, stored with
            # every run and surfaced here rather than glossed over.
            "isolation": sandbox_policy_report(),
        },
        # Asked of the registry rather than asserted. This said `False`
        # unconditionally, so a researcher who had configured a provider was
        # told none was configured — a displayed claim contradicting the
        # system's own state, which is the defect class this product exists to
        # catch. Recorded as D035.
        #
        # `selection()` and not `provider()`: the first reads the override and
        # the environment and costs nothing, the second probes the backend over
        # the network on its first call. The interface polls this endpoint, so
        # what it reports is what is *configured*; whether that backend answers
        # is a different question and a slower one.
        "llm": _model_selection(),
        # What this installation can open, asked of the layer that reads them
        # rather than from a list kept here. Optional formats report the extra
        # that turns them on, so "we cannot read Parquet" and "Parquet needs one
        # pip install" are distinguishable — they need different responses.
        "formats": _dataset_formats(),
        # Every optional capability, not only the ones that happen to be file
        # formats. Before this, four of them — the graph driver, local
        # transcription, figure digitising and the hosted model client — were
        # undiscoverable except by trying them and reading an error. D032.
        "packs": extras.availability(),
        # `settings.tsx` has read this key since the panel was written, and
        # nothing ever returned it — so `projection` was always undefined and
        # the whole Neo4j status panel behind `{projection && …}` has never
        # rendered once. Both halves existed and complete: `capability()`
        # already returns exactly the shape the `Projection` type declares.
        # A read with no writer, which is D011 and D013's defect class again.
        # Recorded as D036.
        #
        # Cheap in the common case: `configured()` is false without the Neo4j
        # environment, and returns before any connection is attempted.
        "graph_projection": graph_projection.capability(),
    }


def _model_selection() -> dict[str, Any]:
    """What model provider is selected, and where the choice came from."""
    from throughline_model.registry import selection

    chosen = selection()
    configured = (chosen["provider"] or "").lower() not in ("", "none", "off",
                                                            "disabled")
    return {
        # "A provider is selected", which is not the same as "it answers".
        # `ollama` is the default, so this is true on a machine that has never
        # installed it — and reporting `reachable` would mean a network probe on
        # an endpoint the interface polls. So reachability is reported as
        # explicitly unknown rather than implied by `configured`, because an
        # unchecked claim rendered as a fact is the thing this product exists to
        # prevent. `registry.provider()` is what answers it, once, when a
        # feature actually needs a model.
        "configured": configured,
        "provider": chosen["provider"] if configured else None,
        "model": chosen["model"],
        "source": chosen["source"],
        "reachable": None,
        "note": (None if configured
                 else "No model provider is configured yet."),
        "reachability_note": ("Selected, but not contacted from here — this "
                              "endpoint is polled and probing would cost a "
                              "round trip." if configured else None),
    }


@app.get("/api/projects/{project_id}/activity")
def project_activity(project_id: str, limit: int = Query(100, ge=1, le=500),
                     before: str | None = Query(None),
                     user: dict = Depends(current_user)) -> dict[str, Any]:
    """What was done in this project, by whom, and when.

    The first reader `audit_log` has ever had. Nine call sites wrote to it and
    nothing asked it a question, so the record existed and was unreachable —
    finished, tested, and no way for a person to see it (D070's shape, one
    table down).

    Scoped like every other project route: an audit trail that leaked across
    projects would be a worse defect than not having one.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        return events.activity(cur, project_id=project_id, limit=limit,
                               before=before)


@app.get("/api/system/launchers")
def system_launchers() -> dict[str, Any]:
    """The double-click door for *this* machine, and whether it is really there.

    T071 built one launcher per platform for somebody who has never opened a
    terminal — and then said so only in the README, which that person will never
    read. A capability nothing links to is the same defect as a button that does
    nothing: present in the repository, absent from the product.

    Only this platform's door is reported. A macOS `.command` offered on Windows
    is noise, and making the reader work out which of three applies is work the
    software has already done.
    """
    return domain_launchers.available()


@app.post("/api/system/launchers/desktop-entry", status_code=200)
def install_desktop_entry(user: dict = Depends(admin_user)) -> dict[str, Any]:
    """Add Throughline to the Linux applications menu.

    A POST because it writes a file into the researcher's home directory —
    a small thing, but a thing they should ask for rather than have happen.

    **Signed in, for the same reason.** This took no session and no body, so it
    was reachable by any page in any tab: a cross-origin form POST needs no
    preflight, and the side effect lands whether or not the response can be
    read. `haptics/tap` states that argument in full and defends itself by
    requiring JSON; this route made the same promise in its first paragraph and
    kept none of it. The session cookie is `httpOnly` and `SameSite=strict`, so
    requiring it is what turns "they should ask for it" into something the
    server actually checks. Every caller is inside the workspace already.

    The bytes are written by `throughline_domain.launchers`, which
    `manage.py desktop-entry` also calls, so the button and the command cannot
    disagree about what a `.desktop` file should contain.
    """
    return domain_launchers.install_desktop_entry()


@app.get("/api/system/version")
def system_version() -> dict[str, Any]:
    """What this installation is. No network, so the page costs nothing to open.

    Separate from the check on purpose. "What am I?" must be answerable offline,
    instantly, and identically on a train; "is there anything newer?" cannot be
    any of those, and folding them together would make opening Settings depend
    on reaching GitHub.
    """
    from throughline_domain import version

    return version.current()


@app.post("/api/system/version/check", status_code=200)
def check_for_updates(user: dict = Depends(current_user)) -> dict[str, Any]:
    """Ask the remote whether anything newer exists — only when asked.

    Signed in, because "only when asked" has to mean asked *by the researcher*.
    Without a session any page in any tab could make this installation reach
    out, which is both an outbound request nobody chose and a signal that this
    machine is running Throughline.

    A POST rather than a GET because it reaches the network and writes
    remote-tracking refs; it is an action a person takes, not a fact to be
    polled. **Never automatic** is T073's first line, and an endpoint the
    interface could poll on a timer is how that quietly stops being true.

    Failing to reach the remote is reported as `checked: false` with a reason,
    never as being up to date — those look identical on a screen and only one
    of them is what it says.
    """
    return domain_updates.check()


@app.get("/api/system/packs/{name}")
def pack_state(name: str) -> dict[str, Any]:
    """One pack: whether it is here, and how an install of it is going."""
    if name not in extras.BY_NAME:
        raise HTTPException(status_code=404, detail=f"No feature pack {name!r}.")
    state = dict(extras.availability()[name])
    progress = extras.install_status(name)
    state["install_state"] = progress.state if progress else None
    state["install_detail"] = progress.detail if progress else None
    return state


@app.post("/api/system/packs/{name}/install", status_code=202)
def install_pack(name: str, user: dict = Depends(admin_user)) -> dict[str, Any]:
    """Turn a capability on, from the screen that reported it missing.

    **202 and not 200.** Some of these are gigabytes — `speech` pulls in torch —
    and a request that waits for pip to finish times out long before it does,
    telling the researcher the install failed while it is still running. So this
    starts the work and hands back somewhere to watch it.

    **The name is a lookup, never a command.** `extras.BY_NAME` is an allowlist
    of nine literals; anything else is a 404 that never reaches pip. This is the
    one endpoint in the product that could otherwise turn a path parameter into
    package installation, which is the whole machine rather than one record —
    the same reasoning `connector-sdk/papers.py` applies to a URL it is asked to
    fetch.

    **Signed in.** This used to say that binding to localhost made
    authentication unnecessary — and then, in the same breath, that "localhost
    only" is exactly what a page in the researcher's own browser already
    satisfies. The second sentence refutes the first: any tab could POST here
    and start a multi-gigabyte download (`speech` pulls in torch). The
    allowlist bounds what can be installed, which is why this was survivable,
    but it does not stop it being started. The session cookie is `httpOnly` and
    `SameSite=strict` and so is never sent cross-origin, and the only caller is
    the settings screen inside the workspace.

    The allowlist stays exactly as it was. It defends against a different thing
    — a path parameter reaching pip — and that argument did not depend on this
    one.
    """
    if name not in extras.BY_NAME:
        raise HTTPException(status_code=404, detail=f"No feature pack {name!r}.")
    if extras.installed(extras.BY_NAME[name]):
        return {"pack": name, "install_state": "installed",
                "install_detail": "Already here; nothing to do."}
    record = extras.install_in_background(name)
    return {"pack": name, "install_state": record.state,
            "install_detail": record.detail,
            "watch": f"/api/system/packs/{name}"}


def _dataset_formats() -> dict[str, Any]:
    from throughline_ingestion.datasets import format_availability

    availability = format_availability()
    readable = sorted(s for s, state in availability.items() if state["readable"])
    optional = {s: state for s, state in availability.items()
                if not state["readable"]}
    return {
        "readable": readable,
        "available_with_an_extra": {
            suffix: {"describes": state["describes"], "install": state["install"]}
            for suffix, state in optional.items()
        },
        "note": (f"{len(readable)} formats readable here."
                 + (f" {len(optional)} more become readable by installing an "
                    f"extra." if optional else "")),
    }


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


class AnalysisSpecRequest(BaseModel):
    method: str = Field(min_length=1, max_length=80)
    dataset_version_ids: list[str] = Field(min_length=1, max_length=1)
    variables: dict[str, Any] = Field(default_factory=dict)
    research_question: str = Field(default="", max_length=20_000)
    method_rationale: str = Field(default="", max_length=8000)
    filters: list[dict[str, Any]] = Field(default_factory=list)
    confidence_level: float = Field(default=0.95, gt=0, lt=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    random_seed: int = 0
    #: The look this analysis constitutes belongs to a line of enquiry, so that
    #: a researcher who sweeps, then specifies two analyses, is corrected across
    #: all of it rather than across each verb separately.
    #:
    #: Absent means "the project's open line of enquiry", which is what an
    #: interactive caller wants and no longer has to compute for itself.
    enquiry_id: str | None = None
    #: Deliberately keep this run out of the researcher's family.
    #:
    #: This used to be what *omitting* the field meant, and the distinction
    #: worked only because the interface always sent one and a script never did.
    #: Now that the server resolves the family, absent no longer tells the two
    #: apart, and guessing wrong is not a harmless default in either direction:
    #: a script silently joining an open enquiry inflates a researcher's family
    #: with work they did not do, and standing alone under-counts the looks. So
    #: it is asked for rather than inferred, and the caller that wants isolation
    #: says so.
    stands_alone: bool = False
    #: Which registered hypothesis this analysis tests, if any. Verified rather
    #: than believed: `exploration.record` checks that the registration came
    #: first, is unedited, and — because a spec is named here — that the
    #: analysis about to run is the analysis that was registered.
    preregistration_id: str | None = None


class ForkRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
    filters: list[dict[str, Any]] | None = None
    method: str | None = None
    variables: dict[str, Any] | None = None


@app.post("/api/projects/{project_id}/analyses", status_code=202)
def create_analysis(project_id: str, payload: AnalysisSpecRequest,
                    user: dict = Depends(current_user)) -> dict[str, Any]:
    """Validate a spec and queue it for sandboxed execution."""
    scoped_project(project_id, user)
    with transaction() as cur:
        # Before anything is created. `enquiry_id` comes from the request, and
        # this route recorded the look into whatever enquiry it named — so an
        # analysis in your own project could count as a look in someone else's,
        # changing their correction (T161). `exploration.record` refuses that
        # too, but by then the spec and run exist and the refusal is a 500.
        if payload.enquiry_id:
            cur.execute("SELECT id FROM enquiries WHERE id = %s AND project_id = %s",
                        (payload.enquiry_id, project_id))
            if not cur.fetchone():
                raise HTTPException(404, "no such line of enquiry")
        try:
            created = analysis.create_spec(cur, project_id=project_id,
                                           spec=payload.model_dump(), actor=user["id"])
        except analysis.SpecInvalid as exc:
            raise HTTPException(422, str(exc)) from exc
        run_id = analysis.create_run(cur, project_id=project_id,
                                     spec_id=created["spec_id"])
        workflow.enqueue(
            cur, workflow_name="analysis.run", project_id=project_id,
            payload={"analysis_run_id": run_id},
            # the same spec queued twice runs once.
            idempotency_key=f"analysis:{run_id}",
        )
        """
        Counted now, before the number exists.

        This is the only honest moment. A look recorded after the result is a
        look the researcher could decline to record once they had seen it, and
        the family would then contain exactly the tests that worked. The
        specification is complete here and the sandbox has not run, so nothing
        about the outcome can influence whether it counts.

        The same ordering is what makes a claimed registration checkable. The
        exemption asks whether the registration came first and whether the
        analysis about to run is the one registered — both are questions about
        the spec, and both are answerable now.
        """
        # The old fallback wrote the run's own id into this column. That is now
        # a foreign key, and even before it was one the "family" it named was a
        # row nothing could ever look up.
        if payload.enquiry_id:
            family = payload.enquiry_id
        elif payload.stands_alone:
            family = enquiry.standalone(
                cur, project_id=project_id, run_id=run_id,
                name="Analysis run on its own")
        else:
            family = enquiry.current(cur, project_id=project_id)["id"]
        look = exploration.record(
            cur, enquiry_id=family,
            project_id=project_id, verb="analysis",
            description=f"{payload.method}: "
                        + ", ".join(f"{role}={value}"
                                    for role, value in sorted(payload.variables.items())),
            # There is no p-value yet. A look with nothing to correct is still a
            # look, and the ledger already handles that for a refused comparison.
            p_value=None,
            preregistration_id=payload.preregistration_id,
            spec_id=created["spec_id"],
            # So the look can learn its p-value when the sandbox finishes. The
            # ledger corrects only tests that have one, and without this a
            # specified analysis would count as a look and never join the
            # family it should be corrected against.
            analysis_run_id=run_id,
        )
    recorded = look.get("recorded", {})
    return {"analysis_run_id": run_id, "spec_id": created["spec_id"],
            "spec_content_hash": created["content_hash"], "status": "queued",
            # §104: the server's own words about what this run counts as.
            # Restating the rule in the interface would let the two disagree,
            # and the interesting cases are the ones where the exemption was
            # claimed and refused.
            "confirmatory": recorded.get("confirmatory", False),
            "standing": recorded.get("why"),
            # How many looks this session now carries. The reason a researcher
            # wants it beside the run they just queued is that it is the number
            # this result will be corrected against.
            "looks_this_session": look.get("looks"),
            "session_note": look.get("note")}


@app.get("/api/projects/{project_id}/analyses")
def list_analyses(project_id: str, limit: int = 200,
                  user: dict = Depends(current_user)) -> list[dict[str, Any]]:
    """Every analysis run in the project, whatever produced it."""
    scoped_project(project_id, user)
    with transaction() as cur:
        return analysis.list_runs(cur, project_id, limit=min(max(limit, 1), 500))


@app.get("/api/analyses/{run_id}")
def get_analysis(run_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    """A run with its result, assumptions and everything  needs to reproduce it."""
    with transaction() as cur:
        run = analysis.get_run(cur, run_id)
        if not run:
            raise HTTPException(404, "Analysis run not found.")
        scoped_project(run["project_id"], user)
        return run


@app.post("/api/analyses/{run_id}/fork", status_code=202)
def fork_analysis(run_id: str, payload: ForkRequest,
                  user: dict = Depends(current_user)) -> dict[str, Any]:
    """branch an analysis to test whether a choice changes the conclusion."""
    with transaction() as cur:
        run = analysis.get_run(cur, run_id)
        if not run:
            raise HTTPException(404, "Analysis run not found.")
        project_id = run["project_id"]
        scoped_project(project_id, user)

        spec_row = analysis.load_spec(cur, run["spec_id"])
        forked = {
            "method": payload.method or spec_row["method"],
            "dataset_version_ids": spec_row["dataset_version_ids"],
            "variables": payload.variables or spec_row["variables"],
            "research_question": spec_row["research_question"],
            "method_rationale": payload.reason,
            "filters": spec_row["filters"] if payload.filters is None else payload.filters,
            "confidence_level": spec_row["confidence_level"],
            "parameters": spec_row["parameters"],
            "random_seed": spec_row["random_seed"],
        }
        try:
            created = analysis.create_spec(cur, project_id=project_id, spec=forked,
                                           actor=user["id"])
        except analysis.SpecInvalid as exc:
            raise HTTPException(422, str(exc)) from exc
        new_run_id = analysis.create_run(cur, project_id=project_id,
                                         spec_id=created["spec_id"],
                                         forked_from_run_id=run_id,
                                         fork_reason=payload.reason)
        workflow.enqueue(cur, workflow_name="analysis.run", project_id=project_id,
                         payload={"analysis_run_id": new_run_id},
                         idempotency_key=f"analysis:{new_run_id}")
    return {"analysis_run_id": new_run_id, "forked_from": run_id, "status": "queued"}


@app.get("/api/projects/{project_id}/analyses/compare")
def compare_analyses(project_id: str, run_id: list[str] = Query(min_length=2),
                     user: dict = Depends(current_user)) -> dict[str, Any]:
    """put branches side by side and say whether the conclusion held."""
    scoped_project(project_id, user)
    with transaction() as cur:
        for candidate in run_id:
            run = analysis.get_run(cur, candidate)
            if not run or run["project_id"] != project_id:
                raise HTTPException(404, f"Analysis run {candidate} not found in this project.")
        return analysis.compare_runs(cur, run_id)


# ---------------------------------------------------------------------------
# Discovery (, , , –)
# ---------------------------------------------------------------------------


class DiscoveryRequest(BaseModel):
    dataset_version_id: str
    false_discovery_rate: float = Field(default=0.05, gt=0, lt=1)
    #: Run again over data that has not changed. A decision, never a default.
    force: bool = False
    #: The researcher's working session, so this sweep joins the same
    #: multiple-comparison family as everything else they have looked at today.
    #: Optional: without it the run is its own family, which is what happened
    #: before this existed.
    enquiry_id: str | None = None
    #: Stop the sweep before it records anything into the project, and wait for
    #: a person — Rule 10. The tests still run; what waits is the part that
    #: writes connections and promotes the survivors, so the results can be read
    #: before they become part of the project's record rather than after.
    hold_before_recording: bool = False



class ValidateRequest(BaseModel):
    confounders: list[str] = Field(default_factory=list)


@app.post("/api/projects/{project_id}/discoveries", status_code=202)
def start_discovery(project_id: str, payload: DiscoveryRequest,
                    user: dict = Depends(current_user)) -> dict[str, Any]:
    """generate, test, correct and rank candidate relationships."""
    scoped_project(project_id, user)
    with transaction() as cur:
        # The enquiry as well as the dataset. Accepted from another project,
        # the sweep ran in full and failed only when the worker came to record
        # its looks, which `exploration.record` refuses since T161 (T162).
        if payload.enquiry_id:
            cur.execute("SELECT id FROM enquiries WHERE id = %s AND project_id = %s",
                        (payload.enquiry_id, project_id))
            if not cur.fetchone():
                raise HTTPException(404, "no such line of enquiry")
        cur.execute(
            "SELECT d.project_id FROM dataset_versions dv "
            "JOIN datasets d ON d.id = dv.dataset_id WHERE dv.id = %s",
            (payload.dataset_version_id,),
        )
        row = cur.fetchone()
        if not row or row["project_id"] != project_id:
            raise HTTPException(404, "Dataset version not found in this project.")

        # A second run over unchanged data is refused, and the existing run is
        # handed back instead.
        #
        # Each run is its own multiple-testing family. Running again re-tests
        # the same pairs and corrects them within a separate family of the same
        # size, so the connections table ends up showing every pair twice at the
        # same q-value. That reads as replication, and it is the opposite: it is
        # the same evidence counted twice. Refusing by default is what keeps the
        # correction meaning what it says.
        if not payload.force:
            cur.execute(
                "SELECT id FROM discovery_runs "
                "WHERE project_id = %s AND dataset_version_id = %s "
                "  AND status <> 'failed' "
                "ORDER BY created_at DESC LIMIT 1",
                (project_id, payload.dataset_version_id))
            existing = cur.fetchone()
            if existing:
                return {
                    "discovery_run_id": existing["id"],
                    "status": "queued",
                    "reused": True,
                    "note": ("This dataset has already been searched. Running "
                             "again would test the same pairs a second time and "
                             "correct them in a separate family, which shows "
                             "every pair twice at the same q-value and reads as "
                             "replication. Pass force to run it anyway."),
                }

        run_id = discovery.create_run(cur, project_id=project_id,
                                      dataset_version_id=payload.dataset_version_id,
                                      fdr=payload.false_discovery_rate,
                                      enquiry_id=payload.enquiry_id)
        workflow.enqueue(cur, workflow_name="discovery.run", project_id=project_id,
                         payload={"discovery_run_id": run_id,
                                  "hold_before_recording":
                                      payload.hold_before_recording},
                         idempotency_key=f"discovery:{run_id}")
    return {"discovery_run_id": run_id, "status": "queued", "reused": False,
            "will_wait_for_approval": payload.hold_before_recording}


@app.get("/api/discoveries/{run_id}")
def get_discovery(run_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    with transaction() as cur:
        cur.execute("SELECT * FROM discovery_runs WHERE id = %s", (run_id,))
        run = cur.fetchone()
        if not run:
            raise HTTPException(404, "Discovery run not found.")
        scoped_project(run["project_id"], user)
        run["connections"] = discovery.list_connections(cur, project_id=run["project_id"])
        return run


@app.get("/api/projects/{project_id}/connections")
def list_connections(project_id: str, status: str | None = None,
                     limit: int = Query(50, ge=1, le=200),
                     user: dict = Depends(current_user)) -> list[dict[str, Any]]:
    scoped_project(project_id, user)
    with transaction() as cur:
        return discovery.list_connections(cur, project_id=project_id, status=status,
                                          limit=limit)


@app.post("/api/connections/{connection_id}/validate", status_code=202)
def validate_connection(connection_id: str, payload: ValidateRequest,
                        user: dict = Depends(current_user)) -> dict[str, Any]:
    """try to destroy the connection; promote only if it survives."""
    with transaction() as cur:
        cur.execute("SELECT project_id FROM connections WHERE id = %s", (connection_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Connection not found.")
        project_id = scoped_project(row["project_id"], user)
        workflow.enqueue(
            cur, workflow_name="connection.validate", project_id=project_id,
            payload={"connection_id": connection_id, "confounders": payload.confounders},
            idempotency_key=f"validate:{connection_id}:{','.join(sorted(payload.confounders))}",
        )
    return {"connection_id": connection_id, "status": "queued"}


class CohortRequest(BaseModel):
    """A named subset somebody drew, and where it sits in the chain."""

    dataset_version_id: str
    name: str
    definition: list[dict[str, Any]]
    parent_id: str | None = None


def _dataset_version(cur, version_id: str) -> dict[str, Any]:
    cur.execute(
        "SELECT dv.storage_key, dv.content_hash, d.project_id, d.format, "
        "s.title FROM dataset_versions dv "
        "JOIN datasets d ON d.id = dv.dataset_id "
        "JOIN sources s ON s.id = d.source_id WHERE dv.id = %s",
        (version_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "That dataset version does not exist.")
    return dict(row)


@app.post("/api/projects/{project_id}/cohorts", status_code=201)
def define_cohort(project_id: str, payload: CohortRequest,
                  user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Record a named subset, counted against the dataset's own rows.

    The counts are computed here rather than accepted from the caller. A
    client that could send its own would be able to make a subset claim
    anything, and a share whose numerator nobody checked is the kind of number
    this product exists not to produce.
    """
    from throughline_ingestion.datasets import read_dataset

    scoped_project(project_id, user)
    with transaction() as cur:
        version = _dataset_version(cur, payload.dataset_version_id)
        if version["project_id"] != project_id:
            raise HTTPException(404, "That dataset version does not exist.")

    path = storage.path_for(version["storage_key"])
    # Content-addressed storage has no extension, so the format comes from the
    # recorded one rather than from the path.
    suffix = (Path(version["title"] or "").suffix.lower()
              or f".{(version['format'] or 'csv').lower()}")
    try:
        frame, _ = read_dataset(path, suffix=suffix)
    except Exception as exc:  # noqa: BLE001 — any read failure is unusable here
        raise HTTPException(
            422, f"This dataset could not be read, so a subset of it cannot "
                 f"be counted: {exc}") from exc

    with transaction() as cur:
        try:
            return cohorts.define(
                cur, project_id=project_id,
                dataset_version_id=payload.dataset_version_id,
                name=payload.name, definition=payload.definition,
                rows=frame.to_dict("records"), actor=user["id"],
                parent_id=payload.parent_id,
                content_hash=str(version["content_hash"] or ""))
        except cohorts.CohortError as exc:
            raise HTTPException(422, str(exc)) from exc


@app.get("/api/dataset-versions/{version_id}/cohorts")
def list_cohorts(version_id: str,
                 user: dict = Depends(current_user)) -> dict[str, Any]:
    """The subsets of one dataset version, parents before children."""
    with transaction() as cur:
        version = _dataset_version(cur, version_id)
        scoped_project(version["project_id"], user)
        found = cohorts.tree(cur, dataset_version_id=version_id)

    stale = [row["id"] for row in found
             if row["content_hash"]
             and row["content_hash"] != str(version["content_hash"] or "")]
    return {
        "dataset_version_id": version_id,
        "cohorts": found,
        # A count shown against data it was not computed on is the failure the
        # stored hash exists to prevent, so the answer names them rather than
        # quietly serving numbers from other bytes.
        "counted_on_other_data": stale,
    }


@app.get("/api/connections/{connection_id}/fragility")
def connection_fragility(connection_id: str,
                         user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    How much unmeasured confounding would explain this association away.

    The validation suite asks what happens when the analysis is perturbed;
    this asks the question a reviewer asks instead — what would have to be
    true, and unaccounted for, for the result to vanish. It is the natural
    complement to the causal-language validator: that stops a sentence
    claiming more than the design licenses, and this says in one number how
    far from a causal claim the evidence sits.

    Nothing is refitted. The estimate, its interval and its sample size come
    from the recorded run.
    """
    with transaction() as cur:
        cur.execute(
            "SELECT c.project_id, c.p_value, c.q_value, c.method, "
            "c.left_variable, c.right_variable, c.analysis_run_id "
            "FROM connections c WHERE c.id = %s", (connection_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Connection not found.")
        scoped_project(row["project_id"], user)
        run = (analysis.get_run(cur, row["analysis_run_id"])
               if row["analysis_run_id"] else None)

    if not run or run.get("status") != analysis.RUN_COMPLETED:
        raise HTTPException(
            409, "This connection has no completed analysis, so there is no "
                 "estimate to test the fragility of.")

    result = run.get("result") or {}
    estimate = result.get("estimate")
    if estimate is None:
        raise HTTPException(
            409, "The recorded run has no estimate, so nothing can be said "
                 "about how much would explain it away.")

    try:
        report = fragility.for_correlation(
            r=float(estimate), method=str(row["method"]),
            ci_low=result.get("ci_low"), ci_high=result.get("ci_high"))
    except fragility.FragilityError as exc:
        # 422 and not 500: an estimate this cannot convert honestly is a fact
        # about the method, and the message names which methods it does.
        raise HTTPException(422, str(exc)) from exc

    return {
        "connection_id": connection_id,
        "variables": [row["left_variable"], row["right_variable"]],
        **report,
        "sentence": fragility.describe(report),
    }


@app.get("/api/connections/{connection_id}/validations")
def list_validations(connection_id: str,
                     user: dict = Depends(current_user)) -> list[dict[str, Any]]:
    """
    Every validation report for a connection, newest first.

    Reachable from the connection rather than only by report id, because that is
    how a reader arrives: they are looking at an association and want to know
    what was done to try to break it. A report that can only be found if you
    already know its id is a report nobody reads.

    An empty list is a real answer — the connection has not been challenged yet
    — and is returned as such rather than as a 404, which would be
    indistinguishable from the connection not existing.
    """
    with transaction() as cur:
        cur.execute("SELECT project_id FROM connections WHERE id = %s",
                    (connection_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Connection not found.")
        scoped_project(row["project_id"], user)

        cur.execute(
            "SELECT id FROM validation_reports WHERE connection_id = %s "
            "ORDER BY created_at DESC",
            (connection_id,))
        ids = [r["id"] for r in cur.fetchall()]
        return [validation.report(cur, report_id) for report_id in ids]


@app.get("/api/validations/{report_id}")
def get_validation(report_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    with transaction() as cur:
        try:
            report = validation.report(cur, report_id)
        except validation.ValidationError as exc:
            raise HTTPException(404, str(exc)) from exc
        scoped_project(report["project_id"], user)
        return report


@app.post("/api/findings/{finding_id}/challenge", status_code=202)
def challenge_finding(finding_id: str, payload: ValidateRequest,
                      user: dict = Depends(current_user)) -> dict[str, Any]:
    """"Challenge This Finding"."""
    with transaction() as cur:
        cur.execute("SELECT project_id FROM findings WHERE id = %s", (finding_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Finding not found.")
        project_id = scoped_project(row["project_id"], user)
        workflow.enqueue(
            cur, workflow_name="finding.challenge", project_id=project_id,
            payload={"finding_id": finding_id, "actor": user["id"],
                     "confounders": payload.confounders},
            idempotency_key=f"challenge:{finding_id}:{new_id('c')}",
        )
    return {"finding_id": finding_id, "status": "queued"}


@app.get("/api/findings/{finding_id}/evidence-graph")
def finding_evidence_graph(finding_id: str,
                           user: dict = Depends(current_user)) -> dict[str, Any]:
    """why do we believe this?"""
    with transaction() as cur:
        cur.execute("SELECT project_id FROM findings WHERE id = %s", (finding_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Finding not found.")
        scoped_project(row["project_id"], user)
        return graphs.evidence_graph(cur, finding_id=finding_id)


@app.get("/api/projects/{project_id}/knowledge-graph")
def knowledge_graph(project_id: str, focus: str | None = None,
                    depth: int = Query(1, ge=1, le=4),
                    limit: int = Query(200, ge=1, le=300),
                    user: dict = Depends(current_user)) -> dict[str, Any]:
    """what is connected. Bounded and expandable, never a full dump."""
    scoped_project(project_id, user)
    with transaction() as cur:
        return graphs.knowledge_graph(cur, project_id=project_id, focus_object_id=focus,
                                      depth=depth, limit=limit)


@app.get("/api/projects/{project_id}/discovery-map")
def discovery_map(project_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    """the project overview and one concrete next action."""
    scoped_project(project_id, user)
    with transaction() as cur:
        return graphs.discovery_map(cur, project_id=project_id)


# ---------------------------------------------------------------------------
# Visuals
# ---------------------------------------------------------------------------


class VisualCreate(BaseModel):
    analysis_run_id: str
    goal: str = Field(default="show the relationship", max_length=500)
    audience: str = Field(default="researcher", max_length=200)
    finding_id: str | None = None
    #: Override the recommendation. Omit to accept what  proposes.
    spec: dict[str, Any] | None = None


class VisualEdit(BaseModel):
    changes: dict[str, Any]


@app.get("/api/analyses/{run_id}/visual-recommendation")
def visual_recommendation(run_id: str, goal: str = "show the relationship",
                          audience: str = "researcher",
                          user: dict = Depends(current_user)) -> dict[str, Any]:
    """what figure suits this result, and why."""
    with transaction() as cur:
        run = analysis.get_run(cur, run_id)
        if not run:
            raise HTTPException(404, "Analysis run not found.")
        scoped_project(run["project_id"], user)
        try:
            recommendation = visuals.recommend_for_run(cur, analysis_run_id=run_id,
                                                       goal=goal, audience=audience)
        except visuals.VisualError as exc:
            raise HTTPException(409, str(exc)) from exc
    return {
        "visual_type": str(recommendation["visual_type"]),
        "reason": recommendation["reason"],
        "spec": recommendation["spec"].model_dump(mode="json"),
        "caption": recommendation["spec"].caption,
        "interpretation": recommendation["interpretation"],
        "alternatives": [{**a, "visual_type": str(a["visual_type"])}
                         for a in recommendation["alternatives"]],
    }


class CompareRequest(BaseModel):
    left_dataset_version_id: str
    right_dataset_version_id: str


@app.post("/api/projects/{project_id}/compatibility", status_code=201)
def assess_compatibility(project_id: str, payload: CompareRequest,
                         user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Can these two datasets honestly be compared? (Part H1, Part I)

    A refusal is a legitimate and often correct answer, and it is the one this
    endpoint exists to give well. The checks are deterministic, so the verdict
    is reproducible and does not require a model provider.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            return compare.assess_datasets(
                cur, project_id=project_id,
                left_version_id=payload.left_dataset_version_id,
                right_version_id=payload.right_dataset_version_id,
            )
        except compare.ComparisonError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.get("/api/projects/{project_id}/compatibility")
def list_compatibility(project_id: str,
                       user: dict = Depends(current_user)) -> list[dict[str, Any]]:
    scoped_project(project_id, user)
    with transaction() as cur:
        return compare.list_assessments(cur, project_id)


class ClaimPayload(BaseModel):
    """A located claim, as it travels back for adjudication."""
    statement: str
    exposure: str
    outcome: str
    direction: str = "unclear"
    claimed_design: str = "unknown"
    claimed_effect: str = ""
    population: str = ""
    locator: str = ""
    #: The paper the claim came from. Optional, but without it the circularity
    #: check (P7) cannot run — and that check has to run before any other, since
    #: a paper tested against its own data produces agreement that means nothing.
    source_id: str | None = None
    #: The recorded Claim this came from, as `locate_claims` returned it.
    #:
    #: The field was missing while the interface was already sending it, so
    #: Pydantic dropped it on the way in and the adjudication had no way to
    #: attach its outcome to a claim. That is why nothing ever wrote `evidence`
    #: (D014): the row needs a `claim_id` and the id was being discarded one
    #: layer above.
    claim_id: str | None = None


class ClaimTestRequest(BaseModel):
    claim: ClaimPayload
    dataset_version_id: str


@app.get("/api/sources/{source_id}/claims")
def stored_claims(source_id: str, project_id: str = Query(...),
                  user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    What was already read out of this paper. Never runs a model.

    Separate from the POST on purpose: reading the record and re-reading the
    paper are different acts, and only one of them can change what every
    downstream comparison rests on.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        return {"source_id": source_id,
                "claims": claim_test.stored_claims(cur, source_id)}


@app.post("/api/sources/{source_id}/claims", status_code=201)
def locate_claims(source_id: str, project_id: str = Query(...),
                  user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Read a paper and record the empirical claims a dataset could test.

    This is the one step of the claim test that needs inference, and it does
    only location — quoting what the paper asserts and naming its constructs.
    Whether those constructs exist in any dataset, and whether the design can
    carry them, are decided afterwards without a model.

    **There is no `force`.** This route took one and handed it to
    `claim_test.locate_claims`, which has never accepted it — so every call
    raised `TypeError` and the researcher got a 500 where the paper's claims
    should have been. It was also redundant by construction: reading the record
    and re-reading the paper are different acts on different routes, and a POST
    here *is* the re-read. Nothing in the interface or the tests ever passed it.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            return claim_test.locate_claims(
                cur, project_id=project_id, source_id=source_id)
        except claim_test.ClaimTestError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.post("/api/projects/{project_id}/claim-test", status_code=201)
def test_claim(project_id: str, payload: ClaimTestRequest,
               user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Adjudicate one claim from a paper against one dataset.

    Deterministic end to end, so the verdict is reproducible and available on an
    installation with inference switched off. It refuses before it reports:
    a claim whose constructs are not confirmed present, or whose design this
    data cannot carry, gets an explanation rather than a number that would look
    like an answer.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            return claim_test.test_claim(
                cur, project_id=project_id,
                claim=payload.claim.model_dump(),
                dataset_version_id=payload.dataset_version_id,
                source_id=payload.claim.source_id)
        except claim_test.ClaimTestError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.get("/api/dataset-versions/{version_id}/by-place")
def values_by_place(version_id: str, place: str = Query(...),
                    value: str = Query(...),
                    user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    One measurement summarised per place, ready to be drawn on a map.

    The profiler has always recognised a geography column — `country`, `iso3`,
    `region` and the rest get `semantic_type: "geography"` at ingestion — and
    nothing in the product has ever used it for anything but choosing a
    statistical test. A dataset that knows where its rows are was drawn only as
    scatter and bars.

    **The mean, and the count it rests on.** A single number per country hides
    how much is behind it: eight rows and eight hundred shade identically. So
    `n` travels with every place and the table under the map shows it.

    **Places that could not be recognised are returned, not dropped.** A
    choropleth is believed, and a country missing from it reads as *no data
    there* rather than *we did not understand the name*. `places.resolve_all`
    reports both halves and this hands the second one back so the interface can
    say it.
    """
    import numpy as np
    import pandas as pd

    from throughline_domain import places as place_codes
    from throughline_ingestion.datasets import read_dataset

    with transaction() as cur:
        cur.execute(
            "SELECT d.project_id FROM dataset_versions dv "
            "JOIN datasets d ON d.id = dv.dataset_id WHERE dv.id = %s", (version_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Dataset version not found.")
        project_id = scoped_project(row["project_id"], user)

        cur.execute(
            "SELECT name, semantic_type FROM dataset_columns "
            "WHERE dataset_version_id = %s AND name = ANY(%s)",
            (version_id, [place, value]))
        columns = {c["name"]: c["semantic_type"] for c in cur.fetchall()}
        for name in (place, value):
            if name not in columns:
                raise HTTPException(
                    404, f"No column {name!r} in this dataset version.")
        if columns[place] != "geography":
            # Refused rather than attempted. Any column of strings can be put
            # through a country lookup, and most of them will match nothing —
            # producing an empty map that looks like missing data.
            raise HTTPException(
                409,
                f"{place!r} is profiled as {columns[place]!r}, not geography, so "
                "it is not a column of places.")

        cur.execute(
            """
            SELECT f.storage_key, f.filename FROM dataset_versions dv
            JOIN datasets d ON d.id = dv.dataset_id
            JOIN sources s ON s.id = d.source_id
            JOIN files f ON f.id = s.file_id
            WHERE dv.id = %s
            """,
            (version_id,),
        )
        located = cur.fetchone()
        labels = harmonize.labels(cur, project_id)

    if not located:
        raise HTTPException(409, "The file behind this dataset version is gone.")

    frame, _ = read_dataset(storage.path_for(located["storage_key"]),
                            suffix=Path(located["filename"] or "").suffix.lower())
    for name in (place, value):
        if name not in frame.columns:
            raise HTTPException(409, f"{name!r} is not in the file any more.")

    numbers = pd.to_numeric(frame[value], errors="coerce")
    usable = pd.DataFrame({"place": frame[place].astype(str), "value": numbers})
    usable = usable[usable["value"].notna()]
    if usable.empty:
        raise HTTPException(
            409, f"No usable numbers in {value!r}, so there is nothing to map.")

    resolution = place_codes.resolve_all(list(usable["place"].unique()))
    matched = resolution["matched"]

    drawn = []
    for raw, group in usable.groupby("place"):
        found = matched.get(str(raw))
        if not found:
            continue
        code, canonical = found
        values = group["value"].to_numpy(dtype=float)
        drawn.append({
            "id": code,
            "label": canonical,
            "as_written": str(raw),
            "value": float(np.mean(values)),
            "n": int(values.size),
        })
    drawn.sort(key=lambda p: p["value"], reverse=True)

    unmatched = list(resolution["unmatched"])
    return {
        "place_column": place,
        "value_column": value,
        "value_label": labels.get(value, value),
        "places": drawn,
        # Said rather than implied by an absence on the map.
        "unmatched": unmatched,
        "note": (
            f"The mean {labels.get(value, value)} of each place, over "
            f"{counted(int(usable.shape[0]), 'row')}. A mean says nothing about "
            "how much sits behind it, so each place carries its own count."
            + (f" {counted(len(unmatched), 'place')} could not be recognised and "
               f"{'is' if len(unmatched) == 1 else 'are'} not drawn: "
               + ", ".join(unmatched[:8]) + "."
               if unmatched else "")
        ),
    }


@app.get("/api/dataset-versions/{version_id}/density")
def column_density(version_id: str, column: str = Query(...),
                   user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    A kernel density estimate for one column (P3).

    Computed here by executed code, never smoothed in the browser. A density
    curve is not a picture of the data — it is an *estimate* with a bandwidth
    parameter, and changing that parameter changes the shape. A browser-side
    smoother would be a second, undocumented analytical choice sitting under a
    figure that claims to show a distribution, so the bandwidth rule is
    computed server-side and stated with the result.
    """
    import numpy as np
    from scipy import stats

    with transaction() as cur:
        cur.execute(
            "SELECT d.project_id FROM dataset_versions dv "
            "JOIN datasets d ON d.id = dv.dataset_id WHERE dv.id = %s", (version_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Dataset version not found.")
        project_id = scoped_project(row["project_id"], user)

        cur.execute(
            "SELECT name, semantic_type FROM dataset_columns "
            "WHERE dataset_version_id = %s AND name = %s", (version_id, column))
        meta = cur.fetchone()
        if not meta:
            raise HTTPException(404, f"No column {column!r} in this dataset version.")
        labels = harmonize.labels(cur, project_id)

    values = _column_values(version_id, column)
    finite = np.asarray([v for v in values if v is not None and np.isfinite(v)],
                        dtype=float)
    if finite.size < 10:
        raise HTTPException(
            409,
            f"Only {finite.size} usable values in {column!r}. A density estimate over "
            "fewer than ten points describes the smoother more than the data.")
    if float(np.std(finite)) == 0.0:
        raise HTTPException(
            409, f"Every value in {column!r} is identical, so it has no distribution "
                 "to estimate.")

    # Scott's rule: the default, stated rather than hidden, so a reader can
    # judge how much of the shape is the data and how much is the smoother.
    kernel = stats.gaussian_kde(finite, bw_method="scott")
    lo, hi = float(finite.min()), float(finite.max())
    pad = (hi - lo) * 0.08
    grid = np.linspace(lo - pad, hi + pad, 160)

    return {
        "column": column,
        "label": labels.get(column, column),
        "x": [float(v) for v in grid],
        "density": [float(v) for v in kernel(grid)],
        # The actual observations, for the rug: a density with no rug hides how
        # much data is behind each bump.
        "observations": [float(v) for v in finite[:400]],
        "n": int(finite.size),
        "bandwidth_rule": "Scott",
        "bandwidth": float(kernel.factor * float(np.std(finite, ddof=1))),
        "quartiles": [float(np.percentile(finite, q)) for q in (25, 50, 75)],
        "note": ("A density curve is a smoothed estimate, not the data. The "
                 "bandwidth above controls how much smoothing was applied; the "
                 "rug beneath the curve shows the observations themselves."),
    }


@app.get("/api/projects/{project_id}/correlation-matrix")
def correlation_matrix(project_id: str,
                       user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Every tested pair as a matrix (P5).

    Assembled from recorded connections rather than recomputed. That is the
    whole point: the matrix and the forest plot and the report are three views
    of one set of runs, so they cannot disagree. A matrix computed fresh
    here would be a second, uncorrected family of tests wearing the same colours.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        cur.execute(
            "SELECT c.left_variable, c.right_variable, c.estimate, "
            f"       {discovery.SURVIVED_SQL} AS survived "
            "FROM connections c "
            "LEFT JOIN discovery_runs dr ON dr.id = c.discovery_run_id "
            "WHERE c.project_id = %s AND c.estimate IS NOT NULL",
            (project_id,),
        )
        rows = list(cur.fetchall())
        labels = harmonize.labels(cur, project_id)

    if not rows:
        return {"cells": [], "variables": [], "note": "Nothing has been tested yet."}

    # Variable order: most-connected first, so structure reads down the diagonal
    # rather than being scattered by alphabetical accident.
    degree: dict[str, int] = {}
    for row in rows:
        for name in (row["left_variable"], row["right_variable"]):
            degree[name] = degree.get(name, 0) + 1
    variables = sorted(degree, key=lambda v: (-degree[v], v))

    cells: list[dict[str, Any]] = []
    for row in rows:
        left = labels.get(row["left_variable"], row["left_variable"])
        right = labels.get(row["right_variable"], row["right_variable"])
        # A correlation matrix is symmetric, and the stored connection is one
        # direction only — so both cells are emitted from the one measurement
        # rather than leaving half the grid blank.
        cells.append({"row": left, "column": right, "value": row["estimate"],
                      "significant": row["survived"]})
        cells.append({"row": right, "column": left, "value": row["estimate"],
                      "significant": row["survived"]})

    return {
        "cells": cells,
        "variables": [labels.get(v, v) for v in variables],
        "note": ("Every pair the discovery run tested, from the recorded results — "
                 "not recomputed here. Blank cells were never tested."),
    }


@app.get("/api/projects/{project_id}/estimates")
def project_estimates(project_id: str, limit: int = Query(30, ge=1, le=200),
                      user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Every tested estimate with its interval (P2, forest plot).

    This is the honest picture of a discovery run. Showing only the row that
    survived correction implies the run found one thing; showing all of them,
    most with intervals crossing zero, shows what actually happened — which is
    the same argument  makes for keeping rejected candidates visible.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        cur.execute(
            """
            SELECT c.id, c.left_variable, c.right_variable, c.estimate, c.q_value,
                   {survived} AS survived,
                   (r.result->>'ci_low')::float  AS ci_low,
                   (r.result->>'ci_high')::float AS ci_high,
                   r.result->>'estimate_name'    AS estimate_name,
                   c.sample_size, c.lifecycle_status
            FROM connections c
            JOIN analysis_runs r ON r.id = c.analysis_run_id
            LEFT JOIN discovery_runs dr ON dr.id = c.discovery_run_id
            WHERE c.project_id = %s
              AND r.result ? 'ci_low' AND r.result ? 'ci_high'
              -- An estimate with no value is not an estimate. A categorical
              -- pair tested by association has no coefficient and no interval,
              -- so it has no place on a forest plot — and one null here took
              -- the whole Figures screen down with it.
              AND c.estimate IS NOT NULL
              AND (r.result->>'ci_low') IS NOT NULL
              AND (r.result->>'ci_high') IS NOT NULL
            ORDER BY abs(c.estimate) DESC
            LIMIT %s
            """.replace("{survived}", discovery.SURVIVED_SQL),
            (project_id, limit),
        )
        rows = list(cur.fetchall())
        # How many were left out, so the omission is stated rather than silent.
        # No join to analysis_runs: a pair tested for association may have no
        # analysis run recorded at all, and joining would drop exactly the rows
        # this count exists to report.
        cur.execute(
            "SELECT count(*) AS n FROM connections "
            "WHERE project_id = %s AND estimate IS NULL", (project_id,))
        without_estimate = cur.fetchone()["n"]
        labels = harmonize.labels(cur, project_id)

    return {
        "excluded_without_estimate": without_estimate,
        "estimates": [
            {
                "id": row["id"],
                # Display names, so a forest plot never lists raw columns.
                "label": f"{labels.get(row['left_variable'], row['left_variable'])}"
                         f" × {labels.get(row['right_variable'], row['right_variable'])}",
                "estimate": row["estimate"],
                "lo": row["ci_low"],
                "hi": row["ci_high"],
                "n": row["sample_size"],
                # Marks which survived correction. Never used to reorder: ranking
                # by significance is how a reader learns to read p-values as
                # importance.
                "significant": row["survived"],
                "lifecycle_status": row["lifecycle_status"],
            }
            for row in rows
        ],
        "estimate_name": rows[0]["estimate_name"] if rows else "estimate",
        "note": ("Intervals crossing zero are consistent with no relationship. "
                 "They are shown rather than hidden: most of what a discovery run "
                 "tests does not survive, and that is the run's most important "
                 "result."
                 + (f" {without_estimate} tested pair"
                    f"{'s are' if without_estimate != 1 else ' is'} not shown here"
                    " — they were tested for association and have no coefficient "
                    "or interval to plot." if without_estimate else "")),
    }


@app.get("/api/analyses/{run_id}/plain-summary")
def plain_summary(run_id: str, refresh: bool = Query(False),
                  user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    A plain-language reading of a result, beside the exact figures.

    **This route did not exist, and the interface has been calling it.**
    `ResultCard` asks for it whenever a connection is opened, so every request
    fell through to the catch-all that serves the interface: 503 in
    development, and an HTML page in a release, which the client then tried to
    parse as JSON. `throughline_domain.interpret` — the module that answers it,
    including the check that the model wrote no figures into prose that must
    not carry them — was imported by nothing at all.

    A 409 rather than a 404 when a run is not finished: the run exists and has
    no result yet, which is a different thing from a run nobody has heard of,
    and the interface says so differently.
    """
    with transaction() as cur:
        cur.execute("SELECT project_id FROM analysis_runs WHERE id = %s", (run_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Analysis run not found.")
        scoped_project(row["project_id"], user)
        try:
            return interpret.summarise_run(cur, run_id=run_id, refresh=refresh)
        except interpret.SummaryContainedNumbers as exc:
            # The summary is refused rather than shown with the figures in it:
            # a paraphrase that restates a number can drift from the recorded
            # value, which is the whole reason it is forbidden one.
            raise HTTPException(502, str(exc)) from exc
        except interpret.InterpretationError as exc:
            # Includes "no model is configured", which is a fact about this
            # machine the researcher can act on, and "the run is not
            # completed", which is a fact about the run.
            raise HTTPException(409, str(exc)) from exc


@app.get("/api/analyses/{run_id}/points")
def analysis_points(run_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    The chart-ready values behind a figure.

    A bounded sample prepared server-side, never the dataset. The browser gets
    what it needs to draw and nothing more — and because every statistic on the
    figure comes from the recorded result rather than from re-aggregating these
    points, the picture cannot disagree with the analysis that produced it.
    """
    with transaction() as cur:
        cur.execute("SELECT project_id, result FROM analysis_runs WHERE id = %s",
                    (run_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Analysis run not found.")
        scoped_project(row["project_id"], user)

        try:
            recommendation = visuals.recommend_for_run(cur, analysis_run_id=run_id)
        except visuals.VisualError as exc:
            raise HTTPException(409, str(exc)) from exc

        spec = recommendation["spec"]
        sample, sampling = _visual_sample(cur, spec)
        try:
            data = visual_prepare(spec, analysis_result=row["result"] or {},
                                  sample=sample)
        except Exception as exc:  # noqa: BLE001 — reported, never guessed at
            raise HTTPException(409, f"Could not prepare figure data: {exc}") from exc

        return {
            "x": data.x_values,
            "y": data.y_values,
            "group": data.group_values,
            "ci_low": data.ci_low,
            "ci_high": data.ci_high,
            # §47: sampling is communicated, never inferred from a point count.
            "sampling": sampling,
            # Statistics come from the recorded run, so the figure and the
            # analysis cannot state different numbers.
            "statistics": data.statistics,
            "sample_size": data.sample_size,
            # Present only when the recommendation is a binned figure. Computed
            # here rather than in the browser: binning is aggregation, and a
            # client that re-aggregated could disagree with the analysis (LAW 2).
            "cells": _binned_cells(spec, data),
            "bin_count": spec.bin_count,
            "bin_shape": str(spec.bin_shape),
            "count_scale": str(spec.count_scale),
            # Present only for a surface: the fitted response evaluated over a
            # grid, and the observations it was fitted to. Sent here rather
            # than computed in the browser for the same reason as `cells` — a
            # client that evaluated the model itself could disagree with the
            # analysis that recorded the coefficients.
            "grid": data.matrix,
            "grid_x": data.x_values if spec.visual_type is VisualType.SURFACE else [],
            "grid_y": data.y_values if spec.visual_type is VisualType.SURFACE else [],
            "observations": data.series if spec.visual_type is VisualType.SURFACE else [],
        }


def _binned_cells(spec, data) -> list[dict[str, Any]] | None:
    """Counts per cell for a binned figure, or None for every other chart.

    Without this the browser has points and no counts, so the workspace falls
    back to drawing a scatter — which at the sample sizes that trigger this
    recommendation is precisely the overplotted blob the binned primitive
    exists to replace. The catalogue said P5 rendered; the figure a researcher
    actually saw was a scatter.
    """
    from throughline_visual.spec import BinShape, VisualType

    if spec.visual_type is not VisualType.HEXBIN:
        return None
    xs, ys = data.x_values, data.y_values
    if not xs or not ys:
        return None

    bins = spec.bin_count or 30
    x_low, x_high = min(xs), max(xs)
    y_low, y_high = min(ys), max(ys)
    x_step = (x_high - x_low) / bins or 1.0
    y_step = (y_high - y_low) / bins or 1.0

    counts: dict[tuple[int, int], int] = {}
    for x, y in zip(xs, ys):
        column = min(int((x - x_low) / x_step), bins - 1)
        row = min(int((y - y_low) / y_step), bins - 1)
        if spec.bin_shape is BinShape.HEX:
            # Offset alternate rows by half a cell, which is what makes the
            # lattice hexagonal rather than square.
            column = min(int((x - x_low) / x_step - (0.5 if row % 2 else 0)),
                         bins - 1)
        counts[(column, row)] = counts.get((column, row), 0) + 1

    offset = 0.5 if spec.bin_shape is BinShape.HEX else 0.0
    return [
        {
            "x": x_low + (column + 0.5 + (offset if row % 2 else 0)) * x_step,
            "y": y_low + (row + 0.5) * y_step,
            "count": count,
        }
        for (column, row), count in sorted(counts.items())
    ]


@app.post("/api/projects/{project_id}/visuals", status_code=201)
def create_visual(project_id: str, payload: VisualCreate,
                  user: dict = Depends(current_user)) -> dict[str, Any]:
    """Create a figure from an analysis run, critiqued before it is stored."""
    scoped_project(project_id, user)
    with transaction() as cur:
        try:
            recommendation = visuals.recommend_for_run(
                cur, analysis_run_id=payload.analysis_run_id,
                goal=payload.goal, audience=payload.audience,
                project_id=project_id,
            )
        except visuals.VisualError as exc:
            raise HTTPException(409, str(exc)) from exc

        spec = (ResearchVisualSpec.model_validate(payload.spec) if payload.spec
                else recommendation["spec"])
        # Validate every data-bearing identifier before reading a sample. A
        # user-supplied spec may otherwise name a dataset from another project,
        # and sampling happens before the visual is stored (T185).
        visuals.validate_spec_scope(cur, project_id=project_id, spec=spec)
        sample, _sampling = _visual_sample(cur, spec)
        try:
            created = visuals.create_visual(
                cur, project_id=project_id, spec=spec, actor=user["id"], sample=sample,
                recommendation=recommendation, finding_id=payload.finding_id,
            )
        except visuals.VisualError as exc:
            raise HTTPException(422, str(exc)) from exc

    return {"visual_id": created["visual_id"], "object_id": created["object_id"],
            "publishable": created["publishable"], "critique": created["critique"],
            "spec": created["spec"].model_dump(mode="json"),
            # Assembled here rather than passed through, so a field the domain
            # adds reaches the interface only if it is named — which is why
            # this one is. Without it Publish offered PDF, SVG and PNG for a
            # surface and a Download that could only fail.
            "exportable": created["exportable"]}


@app.get("/api/projects/{project_id}/visuals")
def list_project_visuals(project_id: str, limit: int = Query(100, ge=1, le=500),
                         user: dict = Depends(current_user)) -> list[dict[str, Any]]:
    """
    Every figure in this project.

    **This route did not exist, and nothing else could reach a saved figure.**
    Creating one answers with its id, the interface downloaded the file and
    moved on, and the stored record — its critique, and the lineage edge back
    to the analysis it draws — was from then on reachable only by somebody who
    had written the id down.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        return visuals.list_visuals(cur, project_id, limit=limit)


@app.get("/api/visuals/{visual_id}")
def get_visual(visual_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    with transaction() as cur:
        try:
            row = visuals.load_visual(cur, visual_id)
        except visuals.VisualError as exc:
            raise HTTPException(404, str(exc)) from exc
        scoped_project(row["project_id"], user)
        row["renders"] = visuals.stale_renders(cur, visual_id)
        return row


@app.post("/api/visuals/{visual_id}/render")
def render_visual(visual_id: str, format: str = Query("svg"),
                  height: int | None = Query(None, ge=120, le=8000),
                  user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    One spec, rendered by whichever backend was asked for.

    `height` is an exact pixel height for a raster export; the width follows
    from the figure's own proportions rather than from a 16:9 video frame. It is
    refused for a vector format rather than ignored — an SVG has no pixel size,
    and pretending to honour one leaves the caller believing something false.

    The response carries a `warning` when the chosen format will damage the
    figure, so the interface can say so *before* the download rather than after.
    """
    with transaction() as cur:
        try:
            row = visuals.load_visual(cur, visual_id)
        except visuals.VisualError as exc:
            raise HTTPException(404, str(exc)) from exc
        scoped_project(row["project_id"], user)
        try:
            return visuals.render_visual(cur, visual_id=visual_id, fmt=format,
                                         height_px=height)
        except visuals.VisualError as exc:
            # A figure that failed the critic is refused, not quietly drawn.
            raise HTTPException(409, str(exc)) from exc
        except publication_render.RenderError as exc:
            # An unsupported format, or a pixel height asked of a vector one.
            raise HTTPException(400, str(exc)) from exc


#: Content types for the formats a figure may be downloaded in. Kept beside the
#: route rather than guessed from the extension, because a wrong type makes a
#: browser download a file it could have displayed — or worse, display one it
#: should have downloaded.
FIGURE_MEDIA_TYPES = {
    "svg": "image/svg+xml", "pdf": "application/pdf", "eps": "application/postscript",
    "png": "image/png", "tiff": "image/tiff", "jpeg": "image/jpeg",
    "jpg": "image/jpeg", "webp": "image/webp",
}


@app.get("/api/visuals/{visual_id}/download")
def download_visual(visual_id: str, format: str = Query("png"),
                    height: int | None = Query(None, ge=120, le=8000),
                    user: dict = Depends(current_user)) -> FileResponse:
    """
    The rendered file itself, as a download.

    Renders on demand when that size has not been made yet, rather than
    returning 404 and asking the caller to POST first — a download link that
    works only after a separate request is a link that fails the first time
    somebody clicks it.

    The filename carries the figure id and the size, so a folder of downloads is
    still legible a month later. That matters more here than it sounds: this is
    the point where a figure leaves the system, and a file called `chart.png` is
    one nobody can trace back.
    """
    with transaction() as cur:
        try:
            row = visuals.load_visual(cur, visual_id)
        except visuals.VisualError as exc:
            raise HTTPException(404, str(exc)) from exc
        scoped_project(row["project_id"], user)
        try:
            rendered = visuals.render_visual(cur, visual_id=visual_id,
                                             fmt=format, height_px=height)
        except visuals.VisualError as exc:
            raise HTTPException(409, str(exc)) from exc
        except publication_render.RenderError as exc:
            raise HTTPException(400, str(exc)) from exc

    key = rendered.get("storage_key")
    if not key:
        raise HTTPException(
            400, f"{format} is a web specification rather than a file. Ask for "
                 "svg, pdf, png, tiff, jpeg or webp to download one.")

    path = storage.storage_root() / key
    if not path.exists():
        raise HTTPException(500, "The figure was recorded but its file is missing.")

    size = "" if height is None else f"-{height}px"
    return FileResponse(
        path,
        media_type=FIGURE_MEDIA_TYPES.get(format.lower(), "application/octet-stream"),
        filename=f"{visual_id}{size}.{format.lower()}")


@app.get("/api/visuals/{visual_id}/scene.zip")
def download_visual_geometry(visual_id: str,
                             user: dict = Depends(current_user)) -> Response:
    """
    A surface figure as geometry, for Blender or any other 3D tool.

    Figures leave here as SVG, PDF, PNG and TIFF — pictures from one chosen
    angle. A fitted surface is genuinely three-dimensional, and a researcher
    who wants to light it, turn it, or place it in a poster at another angle
    has until now had to rebuild it by hand from the numbers.

    The archive keeps the model and the measurements in separate, named files,
    because in a rendered image they are visibly different things and in a mesh
    file they would both be geometry. A poster showing a fitted plane captioned
    as measurements is the failure this product exists to prevent.
    """
    from throughline_visual.renderers import geometry

    with transaction() as cur:
        try:
            row = visuals.load_visual(cur, visual_id)
        except visuals.VisualError as exc:
            raise HTTPException(404, str(exc)) from exc
        scoped_project(row["project_id"], user)

    spec = visual_spec.ResearchVisualSpec.model_validate(row["spec"])
    data = visual_spec.VisualData.model_validate(row["data"])
    try:
        payload = geometry.bundle(spec, data)
    except geometry.GeometryError as exc:
        # 400 and not 404: the figure exists, and the message says why this
        # particular one has no third axis rather than implying it is missing.
        raise HTTPException(400, str(exc)) from exc

    return Response(
        content=payload, media_type="application/zip",
        headers={"Content-Disposition":
                 f'attachment; filename="{visual_id}-scene.zip"'})


@app.post("/api/visuals/{visual_id}/blender-render", status_code=202)
def start_blender_render(visual_id: str,
                         user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Render this figure through Blender, on this machine, in the background.

    Blender's renderer was written and tested and called by nothing: Settings
    found Blender and promised a render for publication that no route could
    produce. This is the route. It queues the render and answers at once —
    a render can take minutes — and returns the run already in flight rather
    than starting a second.

    400 for a figure with no third axis, 409 for one the critic blocked, 503
    when this machine has no usable Blender — each with a sentence saying why.
    """
    with transaction() as cur:
        try:
            row = visuals.load_visual(cur, visual_id)
        except visuals.VisualError as exc:
            raise HTTPException(404, str(exc)) from exc
        scoped_project(row["project_id"], user)
        try:
            return visuals.request_blender_render(cur, visual_id=visual_id)
        except visuals.NotASurface as exc:
            raise HTTPException(400, str(exc)) from exc
        except visuals.BlenderUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except visuals.VisualError as exc:
            raise HTTPException(409, str(exc)) from exc


@app.get("/api/visuals/{visual_id}/blender-render")
def blender_render_state(visual_id: str,
                         user: dict = Depends(current_user)) -> dict[str, Any]:
    """Whether this figure can be rendered through Blender, and how far it got."""
    with transaction() as cur:
        try:
            row = visuals.load_visual(cur, visual_id)
        except visuals.VisualError as exc:
            raise HTTPException(404, str(exc)) from exc
        scoped_project(row["project_id"], user)
        return visuals.blender_render_state(cur, visual_id=visual_id)


@app.get("/api/visuals/{visual_id}/blender-render.png")
def blender_render_image(visual_id: str,
                         user: dict = Depends(current_user)) -> FileResponse:
    """
    The newest Blender render of this figure.

    Named as a render in its filename as well as on screen, because a file
    that leaves the building loses the label the page gave it.
    """
    with transaction() as cur:
        try:
            row = visuals.load_visual(cur, visual_id)
        except visuals.VisualError as exc:
            raise HTTPException(404, str(exc)) from exc
        scoped_project(row["project_id"], user)
        path = visuals.blender_render_file(cur, visual_id=visual_id)
    if path is None:
        raise HTTPException(
            404, "Nothing has been rendered through Blender for this figure yet.")
    return FileResponse(path, media_type="image/png",
                        filename=f"{visual_id}-blender-render.png")


@app.patch("/api/visuals/{visual_id}")
def edit_visual(visual_id: str, payload: VisualEdit,
                user: dict = Depends(current_user)) -> dict[str, Any]:
    """presentation edits only; anything data-bearing needs a new analysis."""
    with transaction() as cur:
        try:
            row = visuals.load_visual(cur, visual_id)
        except visuals.VisualError as exc:
            raise HTTPException(404, str(exc)) from exc
        scoped_project(row["project_id"], user)
        try:
            edited = visuals.apply_edit(cur, visual_id=visual_id, actor=user["id"],
                                        changes=payload.changes)
        except visuals.EditRequiresRecomputation as exc:
            raise HTTPException(409, str(exc)) from exc
        except visuals.VisualError as exc:
            raise HTTPException(422, str(exc)) from exc
    return {"visual_id": visual_id, "spec": edited["spec"].model_dump(mode="json"),
            "publishable": edited["publishable"], "critique": edited["critique"]}


def _column_values(version_id: str, column: str) -> list[float | None]:
    """
    Every numeric value of one column, read server-side.

    Unlike `_visual_sample` this is not capped: a density estimate over a
    truncated head of the file would describe the first rows rather than the
    distribution, and the shape would change silently with row order. The values
    never leave the server — only the fitted curve does.
    """
    import pandas as pd

    from throughline_ingestion.datasets import read_dataset

    with transaction() as cur:
        cur.execute(
            """
            SELECT f.storage_key, f.filename FROM dataset_versions dv
            JOIN datasets d ON d.id = dv.dataset_id
            JOIN sources s ON s.id = d.source_id
            JOIN files f ON f.id = s.file_id
            WHERE dv.id = %s
            """,
            (version_id,),
        )
        row = cur.fetchone()
    if not row:
        return []

    frame, _ = read_dataset(storage.path_for(row["storage_key"]),
                            suffix=Path(row["filename"] or "").suffix.lower())
    if column not in frame.columns:
        return []
    numeric = pd.to_numeric(frame[column], errors="coerce")
    return [None if pd.isna(v) else float(v) for v in numeric]


#: Rows read at a time when sampling a delimited file. Large enough that the
#: per-chunk overhead is irrelevant, small enough that the peak is bounded no
#: matter how big the file is.
SAMPLE_CHUNK_ROWS = 50_000

#: Formats that can be read a piece at a time. Everything else — Excel, SPSS,
#: Stata, Parquet through the columnar reader — is materialised whole by its
#: library, and is also the shape nobody has two million rows of.
STREAMABLE = {".csv", ".tsv"}


def _sample_columns(
    path: Path, suffix: str, fields: list[str], limit: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    A bounded sample of `fields`, without loading the file to get it (§47).

    The sampling was already uniform, seeded and declared. What was wrong was
    the cost of taking it: `read_dataset` materialised the entire file and then
    kept five hundred rows. Measured, a 2,000,000-row CSV cost 0.40s and 323MB
    resident to draw those five hundred — and every cell is read as a Python
    string, which is where the megabytes go. §47 opens by forbidding exactly
    this, and the time was never the alarming part: the memory is transient
    peak in the API process, per request, paid again by the next researcher
    opening the next figure.

    A delimited file is read in chunks, keeping only the columns the figure
    draws, and the sample is a reservoir — Algorithm R, seeded. That gives a
    uniform draw without replacement in one pass with no idea of the row count
    in advance, and hands back the true total as a by-product, which is what
    the account needs to say "500 of 2,000,000".

    **The points move once.** The old sample was pandas' `.sample(n,
    random_state=0)` over a fully materialised index, and no streaming
    algorithm reproduces that choice. Figures already saved keep their stored
    points; a live redraw of an unsaved figure over a sampled dataset will show
    a different five hundred than it did before this change, once. Seeded, so
    it is stable from here.
    """
    import numpy as np
    import pandas as pd

    if suffix not in STREAMABLE:
        # Read whole, as before: these libraries offer nothing else, and the
        # formats are not where the millions are.
        from throughline_ingestion.datasets import read_dataset

        frame, _ = read_dataset(path, suffix=suffix)
        total = len(frame)
        chosen = (frame.sample(n=limit, random_state=0).sort_index()
                  if total > limit else frame)
        return _columns_from(chosen, fields), _account(total, len(chosen), limit)

    separator = "\t" if suffix == ".tsv" else _sniff(path)

    rng = np.random.default_rng(0)
    # The reservoir is a list of rows, not a DataFrame. Replacing a row in a
    # frame of string dtype fights the block manager for no benefit, and only
    # the winners are ever touched — a handful, not a chunk.
    kept: list[Any] = []
    columns: list[str] = []
    seen = 0

    reader = pd.read_csv(
        path, sep=separator, usecols=lambda name: name in set(fields),
        dtype=str, keep_default_na=False, encoding="utf-8",
        encoding_errors="replace", chunksize=SAMPLE_CHUNK_ROWS,
        low_memory=False,
    )
    for chunk in reader:
        if not columns:
            columns = list(chunk.columns)
        rows = chunk.to_numpy(dtype=object)

        take = min(limit - len(kept), len(rows))
        if take > 0:
            kept.extend(rows[:take])
        rest = rows[take:]
        offset = seen + take

        # Algorithm R over the remainder: the row at overall position i
        # replaces a uniformly chosen slot with probability limit/(i+1).
        # Vectorised per chunk; the loop below runs only for the winners.
        if len(rest):
            positions = np.arange(offset, offset + len(rest))
            draws = rng.integers(0, positions + 1)
            for index in np.nonzero(draws < limit)[0]:
                kept[int(draws[index])] = rest[index]
        seen += len(rows)

    frame = pd.DataFrame(kept, columns=columns or fields)
    return _columns_from(frame, fields), _account(seen, len(frame), limit)


def _sniff(path: Path) -> str:
    from throughline_ingestion.datasets import sniff_delimiter

    return sniff_delimiter(path)


def _columns_from(frame: Any, fields: list[str]) -> dict[str, Any]:
    """Numbers as numbers, anything else as text — as the whole-file path did."""
    import pandas as pd

    sample: dict[str, Any] = {}
    for field_name in fields:
        if field_name not in frame.columns:
            continue
        column = frame[field_name]
        numeric = pd.to_numeric(column, errors="coerce")
        sample[field_name] = (numeric.tolist() if numeric.notna().all()
                              else column.astype(str).tolist())
    return sample


def _account(total: int, drawn: int, limit: int) -> dict[str, Any]:
    return {
        "sampled": total > limit,
        "rows_total": int(total),
        "rows_drawn": int(drawn),
        "method": "uniform random without replacement, fixed seed"
                  if total > limit else "every row",
    }


def _visual_sample(cur, spec: ResearchVisualSpec,
                   limit: int = 500) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    A bounded sample of the fields a figure draws, and an account of it.

    The browser never receives the dataset; scatter and box plots need points,
    so a capped sample is read server-side and passed to the renderer.

    **It is drawn at random, not taken from the top.** This used to be
    `.head(500)`, which is not a sample of anything: research data arrives
    sorted — by date, by site, by arm — so the first five hundred rows are the
    earliest patients, or one hospital, or one condition. That subset was then
    drawn *underneath statistics computed from the whole dataset*, so the
    picture and the numbers beside it described different populations, and
    nothing on the figure said so.

    **Seeded, so the same figure is the same picture.** A researcher who
    reopens a chart and finds different points cannot tell a redraw from a
    change in the data, and cannot put it in a paper.

    **The account is returned rather than logged**, because §47's closing line
    is that sampling must be clearly communicated, and a caption can only say
    what the endpoint tells it.
    """
    fields = spec.data_fields()
    if not fields or not spec.dataset_version_id:
        return {}, {"sampled": False}
    cur.execute(
        """
        SELECT f.storage_key, f.filename FROM dataset_versions dv
        JOIN datasets d ON d.id = dv.dataset_id
        JOIN sources s ON s.id = d.source_id
        JOIN files f ON f.id = s.file_id
        WHERE dv.id = %s
        """,
        (spec.dataset_version_id,),
    )
    row = cur.fetchone()
    if not row:
        return {}, {"sampled": False}

    return _sample_columns(
        storage.path_for(row["storage_key"]),
        Path(row["filename"] or "").suffix.lower(),
        list(fields), limit)


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


@app.post("/api/projects/{project_id}/findings", status_code=201)
def create_finding(project_id: str, payload: FindingCreate,
                   user: dict = Depends(current_user)) -> dict[str, Any]:
    scoped_project(project_id, user)
    with transaction() as cur:
        finding_id = findings.create_finding(
            cur, project_id=project_id, title=payload.title,
            finding_type=payload.finding_type, statement=payload.statement,
            summary=payload.summary, from_connections=payload.from_connections,
            actor=user["id"],
        )
    return {"finding_id": finding_id, "lifecycle_status": str(FindingLifecycle.CANDIDATE)}


@app.post("/api/findings/{finding_id}/transition")
def transition_finding(finding_id: str, payload: FindingTransition,
                       user: dict = Depends(current_user)) -> dict[str, Any]:
    """the lifecycle refuses illegal or unearned promotions."""
    with transaction() as cur:
        cur.execute("SELECT project_id FROM findings WHERE id = %s", (finding_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Finding not found.")
        scoped_project(row["project_id"], user)
        try:
            return findings.transition(
                cur, finding_id=finding_id, to_status=payload.to_status,
                reason=payload.reason, actor=user["id"], checks=payload.checks,
            )
        except findings.EvidenceRequired as exc:
            raise HTTPException(409, str(exc)) from exc
        except findings.ChecksContradicted as exc:
            # 409, not 422: the request is well formed and the researcher's
            # answers are legible. It conflicts with what the system recorded,
            # which is a different thing from being incomplete, and the
            # researcher needs to be told which of the two happened.
            raise HTTPException(409, str(exc)) from exc
        except (findings.IllegalTransition, findings.ValidationIncomplete) as exc:
            raise HTTPException(422, str(exc)) from exc


@app.put("/api/findings/{finding_id}/limitations")
def record_finding_limitations(
    finding_id: str, payload: FindingLimitations,
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    """
    Record the caveats on a finding.

    The column was read on the finding page, in the library note and in the
    evidence graph, and written by nothing — so every finding displayed no
    limitations whether or not it had any, which is the same screen a
    researcher sees for a finding with nothing left to caveat. PUT rather than
    POST: the list is the whole statement of what this finding does not
    establish, and sending it entire is what lets a caveat be withdrawn (T154).
    """
    with transaction() as cur:
        cur.execute("SELECT project_id FROM findings WHERE id = %s", (finding_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Finding not found.")
        scoped_project(row["project_id"], user)
        try:
            return findings.record_limitations(
                cur, finding_id=finding_id, limitations=payload.limitations,
                actor=user["id"])
        except findings.LimitationsRefused as exc:
            # 422: the request is well formed and its content is not usable.
            raise HTTPException(422, str(exc)) from exc


@app.get("/api/projects/{project_id}/findings")
def list_findings(project_id: str,
                  user: dict = Depends(current_user)) -> list[dict[str, Any]]:
    """
    Every finding in this project.

    This route did not exist. The interface had been calling it since the
    Findings screen was built, receiving 405 on every load and rendering the
    empty state — so the workspace reported "0 findings" to a researcher who
    might have had a dozen. An error that renders as absence is the worst
    failure this system has: it is indistinguishable from the truth.

    Each finding carries its evidence counts, because  requires a finding to
    be legible as supported *and* contradicted at a glance.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        cur.execute(
            "SELECT * FROM findings WHERE project_id = %s "
            "ORDER BY updated_at DESC", (project_id,))
        rows = [dict(row) for row in cur.fetchall()]
        for finding in rows:
            finding["evidence"] = findings.evidence_summary(cur, finding["id"])
        return rows


@app.get("/api/findings/{finding_id}")
def get_finding(finding_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    with transaction() as cur:
        cur.execute("SELECT * FROM findings WHERE id = %s", (finding_id,))
        finding = cur.fetchone()
        if not finding:
            raise HTTPException(404, "Finding not found.")
        scoped_project(finding["project_id"], user)
        finding["evidence"] = findings.evidence_summary(cur, finding_id)
        finding["history"] = findings.lifecycle_history(cur, finding_id)
        # What validation already observed about the robustness checks, so the
        # promotion form shows the record instead of asking the researcher to
        # certify six things from memory.
        finding["recorded_checks"] = findings.recorded_checks(cur, finding_id)
        return finding


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------


@app.get("/api/workflows/{run_id}")
def get_workflow(run_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    with transaction() as cur:
        run = workflow.get_run(cur, run_id)
        if not run:
            raise HTTPException(404, "Workflow run not found.")
        if run["project_id"]:
            scoped_project(run["project_id"], user)
        return run


@app.get("/api/projects/{project_id}/workflows/awaiting-approval")
def workflows_awaiting_approval(project_id: str,
                                user: dict = Depends(current_user)) -> list[dict[str, Any]]:
    """
    The work in this project that has stopped and is waiting for a person.

    Without this the approval that releases a gated step could only be given by
    someone who already knew the id of the run they were looking for — and
    nothing hands that id out. An approval nobody can find is not a control.
    """
    scoped_project(project_id, user)
    with transaction() as cur:
        return workflow.awaiting_approval(cur, project_id=project_id)


@app.post("/api/workflows/{run_id}/nodes/{node_name}/approve")
def approve_workflow_node(run_id: str, node_name: str,
                          user: dict = Depends(current_user)) -> dict[str, Any]:
    """an irreversible step is released by a visible human decision."""
    with transaction() as cur:
        run = workflow.get_run(cur, run_id)
        if not run:
            raise HTTPException(404, "Workflow run not found.")
        if run["project_id"]:
            scoped_project(run["project_id"], user)
        released = workflow.approve_node(cur, run_id=run_id, node_name=node_name,
                                         actor=user["id"])
        if not released:
            # Answering 200 here would report an approval that did not happen —
            # for a step that had already run, for a name no node has, or for a
            # second click on a button someone pressed twice.
            raise HTTPException(
                409, f"There is no step named {node_name!r} waiting for "
                     f"approval on this run.")
        return workflow.get_run(cur, run_id)


class LabelDecision(BaseModel):
    approve: bool
    #: What still has to happen to the numbers before they are in the canonical
    #: unit. Optional, and only a person can supply it when the column declares
    #: no unit of its own — where both units are known the domain derives it.
    #:
    #: `visuals.variable_labels` reads this to keep a canonical unit off the
    #: axis of a column whose values are not in it. Nothing wrote the column
    #: until this field existed, so that guard never engaged.
    transformation: str | None = None


class StudyContext(BaseModel):
    """
    What a dataset observes, as the researcher states it.

    Every field is optional and the record is replaced wholesale: the form
    shows all four together, so an omitted field means "not recorded" rather
    than "leave what was there". "Not recorded" has to stay sayable — the claim
    test reports an unrecorded scope as *unchecked*, never as passed, and a
    merge would make clearing a field impossible.
    """
    study_design: str | None = None
    population: str | None = None
    period_start: str | None = None
    period_end: str | None = None


@app.get("/api/dataset-versions/{version_id}/study-context")
def read_study_context(version_id: str,
                       user: dict = Depends(current_user)) -> dict[str, Any]:
    """What this dataset is on record as observing, and the designs on offer."""
    with transaction() as cur:
        scoped_project(_dataset_version_project(cur, version_id), user)
        return study_context.of(cur, version_id)


@app.put("/api/dataset-versions/{version_id}/study-context")
def write_study_context(version_id: str, body: StudyContext,
                        user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Record the design, population and collection period of a dataset.

    These four columns were read everywhere and written nowhere, so on every
    real dataset the claim test refused at its design step with "the study
    design is not recorded" and a remedy — "Record the study design and this
    check will run" — that named no control. This is that control.

    Ingestion cannot supply them: a CSV's bytes say how many rows there are,
    not that the rows are a prospective cohort followed from 2011 to 2019.
    """
    with transaction() as cur:
        project_id = scoped_project(_dataset_version_project(cur, version_id), user)
        try:
            return study_context.record(
                cur, dataset_version_id=version_id, project_id=project_id,
                study_design=body.study_design, population=body.population,
                period_start=body.period_start, period_end=body.period_end)
        except study_context.StudyContextError as exc:
            # The researcher typed these. A refusal has to be a sentence they
            # can act on, not a validation shape or a Postgres constraint name.
            raise HTTPException(400, str(exc)) from exc


def _dataset_version_project(cur, version_id: str) -> str:
    cur.execute(
        "SELECT d.project_id FROM dataset_versions dv "
        "JOIN datasets d ON d.id = dv.dataset_id WHERE dv.id = %s", (version_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Dataset version not found.")
    return str(row["project_id"])


@app.post("/api/dataset-versions/{version_id}/propose-labels", status_code=202)
def propose_labels(version_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    """read each column and propose a human label. Nothing is applied."""
    with transaction() as cur:
        cur.execute(
            "SELECT d.project_id FROM dataset_versions dv "
            "JOIN datasets d ON d.id = dv.dataset_id WHERE dv.id = %s", (version_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Dataset version not found.")
        project_id = scoped_project(row["project_id"], user)
        try:
            return harmonize.propose_labels(cur, project_id=project_id,
                                            dataset_version_id=version_id)
        except harmonize.HarmonizationError as exc:
            raise HTTPException(503, str(exc)) from exc


@app.get("/api/projects/{project_id}/variables")
def project_variables(project_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    """Approved labels, what is awaiting review, and what has been harmonized."""
    scoped_project(project_id, user)
    with transaction() as cur:
        return {
            "labels": harmonize.labels(cur, project_id),
            "pending": harmonize.pending(cur, project_id),
            "equivalent": harmonize.equivalent_columns(cur, project_id),
            "note": ("Only approved labels are used anywhere. An unreviewed "
                     "suggestion changes nothing on screen ( this rule)."),
        }


@app.post("/api/variable-mappings/{mapping_id}/decide")
def decide_label(mapping_id: str, payload: LabelDecision,
                 user: dict = Depends(current_user)) -> dict[str, Any]:
    with transaction() as cur:
        cur.execute("SELECT project_id FROM variable_mappings WHERE id = %s", (mapping_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Mapping not found.")
        scoped_project(row["project_id"], user)
        try:
            return harmonize.decide(cur, mapping_id=mapping_id,
                                    approve=payload.approve, user_id=user["id"],
                                    transformation=payload.transformation)
        except harmonize.HarmonizationError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    """never a bare "something went wrong"."""
    return JSONResponse(
        status_code=500,
        content={
            "error": type(exc).__name__,
            "message": str(exc),
            "path": request.url.path,
            "hint": "The request was rolled back; no partial state was written.",
        },
    )


# ---------------------------------------------------------------------------
# Databases a researcher uploaded
# ---------------------------------------------------------------------------


def _database_file(cur, *, project_id: str, source_id: str) -> dict[str, Any]:
    """The stored file behind a source, once it is known to be a database."""
    cur.execute(
        "SELECT f.storage_key, f.filename FROM sources s "
        "JOIN files f ON f.id = s.file_id "
        "WHERE s.id = %s AND s.project_id = %s",
        (source_id, project_id))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "No such source in this project.")
    return dict(row)


@app.get("/api/projects/{project_id}/sources/{source_id}/tables")
def database_tables(project_id: str, source_id: str,
                    user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    What is in an uploaded SQLite database.

    A dataset is one table and a database is several, so ingesting one is a
    choice somebody has to make. Refusing without saying what the choices are
    would leave the researcher with a file the system can read and no way to
    say which part of it they meant.
    """
    from throughline_ingestion import datasets as dataset_reader

    scoped_project(project_id, user)
    with transaction() as cur:
        located = _database_file(cur, project_id=project_id, source_id=source_id)

    path = storage.path_for(located["storage_key"])
    try:
        tables = dataset_reader.sqlite_tables(path)
    except dataset_reader.UnsupportedDataset as exc:
        raise HTTPException(400, str(exc)) from exc

    return {
        "source_id": source_id,
        "filename": located["filename"],
        "tables": tables,
        "note": ("Importing a table copies its rows into this project as a "
                 "dataset of its own. The database file is left as it is."),
    }


@app.post("/api/projects/{project_id}/sources/{source_id}/tables/{table}",
          status_code=202)
def import_database_table(project_id: str, source_id: str, table: str,
                          user: dict = Depends(current_user)) -> dict[str, Any]:
    """
    Import one table out of a database as a dataset in its own right.

    **It becomes a CSV source rather than a special kind of database source.**
    Everything downstream — profiling, semantic types, missingness, the column
    passages a researcher searches — already works on a table of text, and a
    second path through all of that would be a second set of bugs. The rows are
    written out once, registered as an ordinary file, and handed to the
    ingestion the rest of the product already uses.
    """
    from throughline_ingestion import datasets as dataset_reader

    scoped_project(project_id, user)
    with transaction() as cur:
        located = _database_file(cur, project_id=project_id, source_id=source_id)

    path = storage.path_for(located["storage_key"])
    try:
        frame, _ = dataset_reader.read_dataset(path, suffix=".sqlite", table=table)
    except dataset_reader.UnsupportedDataset as exc:
        # 400 rather than 404 even for an unknown table: the message names
        # every table there is, which is more use than a bare "not found".
        raise HTTPException(400, str(exc)) from exc

    if frame.empty:
        raise HTTPException(
            400, f"{table!r} has no rows, so there is nothing to import.")

    buffer = io.BytesIO(frame.to_csv(index=False).encode("utf-8"))
    stem = pathlib.Path(located["filename"]).stem
    name = f"{stem} — {table}.csv"

    with transaction() as cur:
        record = storage.register_file(
            cur, project_id=project_id, filename=name, stream=buffer,
            media_type="text/csv")
        new_source = objects.create_source(
            cur, project_id=project_id, source_type=SourceType.UPLOAD,
            title=name, actor=user["id"], file_id=str(record["id"]),
            content_hash=str(record["content_hash"]))
        run_id = workflow.enqueue(
            cur, workflow_name="ingest.source", project_id=project_id,
            payload={"source_id": new_source},
            # Keyed per source, for the reason the upload route gives at
            # length: a retried POST collapses into one run, and a source that
            # is created can never end up with no run to finish it.
            idempotency_key=f"ingest:{new_source}")
        events.audit(cur, project_id=project_id, actor=user["id"],
                     action="imported", object_type="dataset",
                     object_id=new_source,
                     detail={"from_database": located["filename"], "table": table})

    return {
        "source_id": new_source,
        "workflow_run_id": run_id,
        "rows": int(len(frame)),
        "columns": int(len(frame.columns)),
        "note": (f"{len(frame)} rows from {table!r} are being profiled as a "
                 "dataset of their own. The database is unchanged."),
    }


# ---------------------------------------------------------------------------
# Reading numbers back off a published figure
# ---------------------------------------------------------------------------


#: A published figure is a picture of a chart, not a photograph of a landscape.
#: The cap is generous for a 600-dpi journal plate and small enough that the
#: request cannot be used to spend the machine's memory. It is enforced by
#: reading a bounded number of bytes rather than by trusting `Content-Length`,
#: which is a claim the client makes about itself.
MAX_FIGURE_BYTES = 32 * 1024 * 1024


class DigitiseRequest(BaseModel):
    """The calibration, which is the part a machine must not do for itself.

    Two reference points per axis, their data values read off the printed
    labels by a person, and an explicit statement of whether each axis is
    linear or logarithmic. `axes_declared` is not a formality: reading a log
    axis as linear is wrong by orders of magnitude at one end and nearly right
    at the other, and the domain refuses rather than guessing.
    """

    x1_px: float
    x1_value: float
    x2_px: float
    x2_value: float
    y1_px: float
    y1_value: float
    y2_px: float
    y2_value: float
    x_log: bool = False
    y_log: bool = False
    axes_declared: bool = False
    marker_colour: list[int] | None = None


@app.post("/api/projects/{project_id}/figures/digitise")
async def digitise_figure(
    project_id: str,
    calibration: str = Form(...),
    file: UploadFile = File(...),
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    """
    Read a data series off an uploaded chart image.

    **Nothing is stored.** This is a computation, not an ingestion: the bytes
    live in a temporary file for the length of the call and are deleted in a
    `finally`, and no `files` row is written. A researcher digitising a figure
    from a paper is asking a question about a picture, and answering it does
    not require the platform to keep the picture. If they want the numbers in
    the project, they upload the CSV — which is a visible, deliberate act that
    records where the data came from, rather than a silent side effect.

    The calibration arrives as a JSON form field beside the file because a
    multipart request cannot carry a JSON body as well.
    """
    scoped_project(project_id, user)

    try:
        parsed = DigitiseRequest.model_validate_json(calibration)
    except PayloadInvalid as exc:
        raise HTTPException(422, f"The calibration is not usable: {exc}") from exc

    # Bounded read. `await file.read(MAX_FIGURE_BYTES + 1)` — one byte past the
    # limit — is how the difference between "exactly at the cap" and "over it"
    # is known without holding the oversized body.
    payload = await file.read(MAX_FIGURE_BYTES + 1)
    if len(payload) > MAX_FIGURE_BYTES:
        raise HTTPException(
            413, f"That figure is larger than {MAX_FIGURE_BYTES // (1024 * 1024)} MB. "
                 "A published figure is a chart image; something much larger is "
                 "probably not one.")
    if not payload:
        raise HTTPException(422, "That file is empty.")

    try:
        calib = digitise_module.Calibration(
            x1_px=parsed.x1_px, x1_value=parsed.x1_value,
            x2_px=parsed.x2_px, x2_value=parsed.x2_value,
            y1_px=parsed.y1_px, y1_value=parsed.y1_value,
            y2_px=parsed.y2_px, y2_value=parsed.y2_value,
            x_log=parsed.x_log, y_log=parsed.y_log,
        )
    except digitise_module.DigitiseError as exc:
        # 422 rather than 500: a calibration with two identical reference
        # points is a thing the researcher can fix, and the domain's message
        # already says how.
        raise HTTPException(422, str(exc)) from exc

    marker = tuple(parsed.marker_colour) if parsed.marker_colour else None
    if marker is not None and len(marker) != 3:
        raise HTTPException(422, "A marker colour is three numbers: red, green, blue.")

    suffix = pathlib.Path(file.filename or "figure.png").suffix or ".png"
    handle, temp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(handle, "wb") as scratch:
            scratch.write(payload)
        try:
            result = digitise_module.digitise(
                path=temp_path, calibration=calib, marker_colour=marker,
                axes_declared=parsed.axes_declared,
            )
        except digitise_module.DigitiseError as exc:
            # The one that reaches here on a normal installation is the missing
            # OpenCV pack. It is a 503 rather than a 500 because the request was
            # well formed and the machine simply cannot answer it yet, and the
            # message names the pack that fixes it.
            raise HTTPException(503, str(exc)) from exc
    finally:
        # Deleted whether the read succeeded, refused or raised. A figure left
        # in the system's temporary directory is a copy of somebody's
        # unpublished data that nothing is tracking.
        pathlib.Path(temp_path).unlink(missing_ok=True)

    return result


# ---------------------------------------------------------------------------
# The interpretation layer
# ---------------------------------------------------------------------------
#
# Mounted from its own module rather than written here. Two other branches are
# open against this file, and several hundred more lines in it would produce a
# three-way merge that gets resolved wrongly in places. One line does not.
#
# Imported at the bottom on purpose: that module reaches back for `current_user`
# and `scoped_project`, so both have to exist before it loads.
from .interpretation import router as interpretation_router  # noqa: E402

app.include_router(interpretation_router)


# ---------------------------------------------------------------------------
# The interface
# ---------------------------------------------------------------------------
#
# Last in the file, and that placement is the whole mechanism. FastAPI matches
# routes in registration order, so a catch-all declared here is reached only by
# requests that no real route claimed — `/api/...`, `/health` and the router
# above are all matched by their own handlers first. Declared any earlier and it
# would shadow them, which fails as an API endpoint mysteriously returning HTML.
#
# `interpretation_router` is included above this for the same ordering reason.

from .interface import response_for  # noqa: E402


@app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
def interface_files(path: str) -> Response:
    """Serve the exported interface, so the browser has one origin.

    **HEAD as well as GET.** The Node server this replaced answered it, so
    GET-only would be a quiet regression: health checks, proxies and link
    checkers all use HEAD, and a 405 from the page that loads fine in a browser
    is the kind of difference nobody looks for. Starlette drops the body for a
    HEAD itself, so the handler is the same one.

    `include_in_schema=False` because a catch-all in the OpenAPI document is
    noise: it matches everything and documents nothing, and `/docs` is a surface
    a researcher reads.
    """
    return response_for(path)
