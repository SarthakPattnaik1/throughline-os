"""First-run authority must not be decided by network timing."""

from __future__ import annotations

import threading
import uuid

import pytest
from fastapi.testclient import TestClient

from throughline_domain import auth
from throughline_domain.db import connection, transaction


DOMAIN = "first-run-security.invalid"
PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def clean_accounts():
    with transaction() as cur:
        cur.execute("DELETE FROM users WHERE email LIKE %s", (f"%@{DOMAIN}",))
    yield
    with transaction() as cur:
        cur.execute("DELETE FROM users WHERE email LIKE %s", (f"%@{DOMAIN}",))


def test_hosted_first_admin_requires_operator_bootstrap_token(monkeypatch):
    from throughline_api.app import app

    # Make this an actually fresh installation for the route under test.
    with transaction() as cur:
        cur.execute("DELETE FROM users")

    monkeypatch.setenv("THROUGHLINE_DEPLOYMENT", "hosted")
    monkeypatch.setenv("THROUGHLINE_REMOTE_SETUP_TOKEN", "operator-only-secret")

    payload = {
        "email": f"admin-{uuid.uuid4().hex}@{DOMAIN}",
        "display_name": "Admin",
        "password": PASSWORD,
    }

    with TestClient(app) as client:
        refused = client.post("/api/auth/setup", json=payload)
        assert refused.status_code == 403

        accepted = client.post(
            "/api/auth/setup",
            json=payload,
            headers={"X-Throughline-Setup-Token": "operator-only-secret"},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["user"]["is_admin"] is True


def test_first_account_decision_is_serialized_across_connections():
    """Two simultaneous first-user transactions cannot both become admin."""
    from throughline_api.app import _lock_first_account

    # Isolate the premise from whatever account another test created.
    with transaction() as cur:
        cur.execute("DELETE FROM users")

    barrier = threading.Barrier(2)
    results: list[tuple[bool, str]] = []
    errors: list[BaseException] = []

    def contender(label: str) -> None:
        email = f"{label}-{uuid.uuid4().hex}@{DOMAIN}"
        try:
            with connection() as conn, conn.cursor() as cur:
                barrier.wait(timeout=10)
                _lock_first_account(cur)
                cur.execute("SELECT COUNT(*) AS n FROM users")
                first = cur.fetchone()["n"] == 0
                if first:
                    auth.create_user(
                        cur,
                        email=email,
                        display_name=label,
                        password=PASSWORD,
                        is_admin=True,
                    )
                conn.commit()
                results.append((first, email))
        except BaseException as exc:  # surfaced in the parent assertion
            errors.append(exc)

    threads = [
        threading.Thread(target=contender, args=("one",)),
        threading.Thread(target=contender, args=("two",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not errors, errors
    assert not any(thread.is_alive() for thread in threads)
    assert sum(1 for first, _ in results if first) == 1

    with transaction() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM users WHERE is_admin AND email LIKE %s",
            (f"%@{DOMAIN}",),
        )
        assert cur.fetchone()["n"] == 1
