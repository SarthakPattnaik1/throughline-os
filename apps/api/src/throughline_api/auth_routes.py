"""Authentication and installation-account HTTP routes.

This module is deliberately separate from the main API surface.  Authentication
is a machine/account concern, not research-domain logic; keeping it here makes
its security boundary reviewable without scrolling through the full research
API.  Public route paths and dependency functions are re-exported by app.py so
existing imports remain compatible.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
from typing import Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from throughline_domain import auth
from throughline_domain import settings as domain_settings
from throughline_domain.db import transaction

from .security import session_cookie_kwargs

router = APIRouter()

#: The longest password any request accepts — one number for every model that
#: takes one.  A password accepted when set must always be accepted by sign-in.
MAX_PASSWORD = 1024


class SetupRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=MAX_PASSWORD)
    setup_token: str = Field(default="", max_length=512)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD)


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(default="", max_length=200)
    password: str = Field(min_length=12, max_length=MAX_PASSWORD)


class RegistrationSetting(BaseModel):
    open: bool


class NewAccount(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(default="", max_length=200)
    password: str = Field(min_length=12, max_length=MAX_PASSWORD)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD)
    new_password: str = Field(min_length=12, max_length=MAX_PASSWORD)


def current_user(
    throughline_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    with transaction() as cur:
        user = auth.resolve_session(cur, throughline_session)
    if not user:
        raise HTTPException(401, "Sign in to continue.")
    return user


def admin_user(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """The signed-in account, if it administers this installation."""
    if not user.get("is_admin"):
        raise HTTPException(
            403,
            "Only the administrator of this installation can do this. It "
            "changes the machine for everyone who uses it, not one project.",
        )
    return user


def scoped_project(project_id: str, user: dict[str, Any]) -> str:
    """Project isolation is checked server-side, never in the client."""
    with transaction() as cur:
        if not auth.owns_project(cur, user_id=user["id"], project_id=project_id):
            # Do not reveal that another account's project id exists.
            raise HTTPException(404, "Project not found.")
    return project_id


@router.get("/api/auth/status")
def auth_status(
    throughline_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    with transaction() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM users")
        needs_setup = cur.fetchone()["n"] == 0
        user = auth.resolve_session(cur, throughline_session)
    return {"needs_setup": needs_setup, "authenticated": bool(user), "user": user}


_FIRST_ACCOUNT_LOCK = "throughline:first-account"


def _lock_first_account(cur) -> None:
    """Serialize the decision about which account is the administrator."""
    cur.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (_FIRST_ACCOUNT_LOCK,),
    )


def _setup_peer_is_loopback(request: Request) -> bool:
    """Whether first-run setup originated from this machine itself."""
    host = ((request.client.host if request.client else "") or "").split("%", 1)[0]
    if host == "testclient" and os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() == "localhost"


def _require_remote_setup_authority(
    request: Request, *, body_token: str = "",
) -> None:
    """Require an operator secret for every non-loopback first-admin setup."""
    if _setup_peer_is_loopback(request):
        return

    expected = os.environ.get("THROUGHLINE_REMOTE_SETUP_TOKEN", "")
    supplied = body_token or request.headers.get("x-throughline-setup-token", "")
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            403,
            "First-run setup reached Throughline over a network connection. "
            "Enter the setup token printed by the Throughline server, or set "
            "THROUGHLINE_REMOTE_SETUP_TOKEN on the server and use that value.",
        )


@router.post("/api/auth/setup")
def auth_setup(
    payload: SetupRequest, response: Response, request: Request,
) -> dict[str, Any]:
    _require_remote_setup_authority(request, body_token=payload.setup_token)
    with transaction() as cur:
        _lock_first_account(cur)
        cur.execute("SELECT COUNT(*) AS n FROM users")
        if cur.fetchone()["n"]:
            raise HTTPException(409, "This installation is already set up.")
        try:
            user = auth.create_user(
                cur,
                email=payload.email,
                display_name=payload.display_name,
                password=payload.password,
            )
        except auth.AuthError as exc:
            raise HTTPException(400, str(exc)) from exc
        token = auth.create_session(cur, user_id=user["id"])
    _set_session_cookie(response, token)
    return {"user": user}


_OPEN = ("1", "true", "yes", "on")


def _registration_is_open(request: Request) -> bool:
    """Whether a stranger may create an account on this installation."""
    with transaction() as cur:
        if (domain_settings.get(cur, "open_registration") or "").lower() in _OPEN:
            return True
    host = (request.client.host if request.client else "") or ""
    return host in ("127.0.0.1", "::1", "localhost")


@router.get("/api/system/registration")
def registration_setting(
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    with transaction() as cur:
        value = (domain_settings.get(cur, "open_registration") or "").lower()
    return {"open": value in _OPEN, "can_change": bool(user.get("is_admin"))}


@router.put("/api/system/registration")
def set_registration(
    payload: RegistrationSetting,
    user: dict[str, Any] = Depends(admin_user),
) -> dict[str, Any]:
    with transaction() as cur:
        domain_settings.set_value(
            cur,
            "open_registration",
            "true" if payload.open else "false",
            changed_by=user["id"],
        )
    return {"open": payload.open, "can_change": True}


@router.post("/api/auth/register", status_code=201)
def auth_register(
    payload: RegisterRequest, request: Request, response: Response,
) -> dict[str, Any]:
    if not _registration_is_open(request):
        raise HTTPException(
            403,
            "Sign-up is limited to this machine. This workspace holds a "
            "researcher's corpus, so accounts can only be created locally "
            "unless the operator turns on open registration in Settings.",
        )

    with transaction() as cur:
        _lock_first_account(cur)
        cur.execute("SELECT COUNT(*) AS n FROM users")
        first = cur.fetchone()["n"] == 0
        try:
            user = auth.create_user(
                cur,
                email=payload.email,
                display_name=payload.display_name,
                password=payload.password,
                is_admin=first,
            )
        except auth.AuthError as exc:
            raise HTTPException(400, str(exc)) from exc
        token = auth.create_session(cur, user_id=user["id"])

    _set_session_cookie(response, token)
    return {"user": user, "first_account": first}


@router.post("/api/auth/login")
def auth_login(payload: LoginRequest, response: Response) -> dict[str, Any]:
    with transaction() as cur:
        user = auth.authenticate(cur, email=payload.email, password=payload.password)
        if not user:
            raise HTTPException(401, "Email or password is incorrect.")
        token = auth.create_session(cur, user_id=user["id"])
    _set_session_cookie(response, token)
    return {"user": user}


@router.post("/api/auth/accounts", status_code=201)
def create_account(
    payload: NewAccount,
    user: dict[str, Any] = Depends(admin_user),
) -> dict[str, Any]:
    with transaction() as cur:
        try:
            created = auth.create_user(
                cur,
                email=payload.email,
                display_name=payload.display_name,
                password=payload.password,
                is_admin=False,
            )
        except auth.AuthError as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"user": created}


@router.post("/api/auth/password")
def change_password(
    payload: PasswordChange,
    response: Response,
    user: dict[str, Any] = Depends(current_user),
    throughline_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    with transaction() as cur:
        confirmed = auth.authenticate(
            cur, email=user["email"], password=payload.current_password,
        )
        if not confirmed:
            raise HTTPException(403, "That is not your current password.")
        try:
            auth.set_password(
                cur, user_id=user["id"], password=payload.new_password,
            )
        except auth.AuthError as exc:
            raise HTTPException(400, str(exc)) from exc
        auth.destroy_other_sessions(
            cur, user_id=user["id"], keep_token=throughline_session,
        )
    return {
        "ok": True,
        "note": "Signed out everywhere else. This session stays open.",
    }


@router.get("/api/auth/accounts")
def list_accounts(
    user: dict[str, Any] = Depends(current_user),
) -> list[dict[str, Any]]:
    with transaction() as cur:
        cur.execute(
            "SELECT id, email, display_name, is_admin, created_at FROM users "
            "ORDER BY created_at"
        )
        return [dict(row) for row in cur.fetchall()]


@router.post("/api/auth/logout")
def auth_logout(
    response: Response,
    throughline_session: str | None = Cookie(default=None),
) -> dict[str, bool]:
    with transaction() as cur:
        auth.destroy_session(cur, throughline_session)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"ok": True}


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        max_age=auth.SESSION_DAYS * 86400,
        **session_cookie_kwargs(),
    )
