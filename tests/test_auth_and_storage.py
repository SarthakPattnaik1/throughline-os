"""Identity, project scoping (§97) and immutable content-addressed storage (§12)."""

from __future__ import annotations

import io

import pytest
from throughline_domain import auth, storage
from throughline_domain.db import connection
from throughline_domain.ids import new_id


def _user(cur, email: str | None = None) -> dict:
    return auth.create_user(
        cur,
        email=email or f"{new_id('u')}@lab.local",
        display_name="Researcher",
        password="correct-horse-battery",
    )


def test_password_is_never_stored_in_the_clear(cur):
    user = _user(cur)
    cur.execute("SELECT password_hash FROM users WHERE id = %s", (user["id"],))
    assert "correct-horse-battery" not in cur.fetchone()["password_hash"]


def test_short_password_is_refused(cur):
    with pytest.raises(auth.AuthError):
        auth.create_user(cur, email="a@lab.local", display_name="A", password="short")


def test_authenticate_round_trip(cur):
    user = _user(cur, "known@lab.local")
    assert auth.authenticate(cur, email="known@lab.local",
                             password="correct-horse-battery")["id"] == user["id"]
    assert auth.authenticate(cur, email="known@lab.local", password="wrong") is None
    assert auth.authenticate(cur, email="nobody@lab.local", password="x") is None


def test_session_token_is_stored_only_as_a_hash(cur):
    user = _user(cur)
    token = auth.create_session(cur, user_id=user["id"])
    cur.execute("SELECT token_hash FROM sessions WHERE user_id = %s", (user["id"],))
    assert cur.fetchone()["token_hash"] != token
    assert auth.resolve_session(cur, token)["id"] == user["id"]


def test_expired_session_does_not_resolve(cur):
    user = _user(cur)
    token = auth.create_session(cur, user_id=user["id"])
    cur.execute("UPDATE sessions SET expires_at = now() - interval '1 day' "
                "WHERE user_id = %s", (user["id"],))
    assert auth.resolve_session(cur, token) is None


def test_project_scoping_is_enforced_server_side(cur):
    """§97 — one researcher's project is not readable through another's account."""
    owner, other = _user(cur), _user(cur)
    project_id = new_id("prj")
    cur.execute("INSERT INTO projects(id, owner_user_id, name) VALUES (%s, %s, 'Mine')",
                (project_id, owner["id"]))
    assert auth.owns_project(cur, user_id=owner["id"], project_id=project_id) is True
    assert auth.owns_project(cur, user_id=other["id"], project_id=project_id) is False


def test_identical_content_is_stored_once(cur):
    """§12 — re-uploading a document cannot create a divergent second copy."""
    user = _user(cur)
    project_id = new_id("prj")
    cur.execute("INSERT INTO projects(id, owner_user_id, name) VALUES (%s, %s, 'P')",
                (project_id, user["id"]))

    payload = b"%PDF-1.7 fake but stable bytes"
    first = storage.register_file(cur, project_id=project_id, filename="a.pdf",
                                  stream=io.BytesIO(payload), media_type="application/pdf")
    second = storage.register_file(cur, project_id=project_id, filename="copy.pdf",
                                   stream=io.BytesIO(payload), media_type="application/pdf")

    assert first["content_hash"] == second["content_hash"]
    assert second["deduplicated"] is True
    assert first["id"] == second["id"]


def test_reupload_repairs_a_corrupted_content_addressed_blob(cur):
    user = _user(cur)
    project_id = new_id("prj")
    cur.execute(
        "INSERT INTO projects(id, owner_user_id, name) VALUES (%s, %s, 'Repair')",
        (project_id, user["id"]),
    )
    payload = b"country,value\nIN,1\n"
    first = storage.register_file(
        cur,
        project_id=project_id,
        filename="data.csv",
        stream=io.BytesIO(payload),
        media_type="text/csv",
    )
    path = storage.path_for(str(first["storage_key"]))
    path.write_bytes(b"corrupted on disk")
    assert storage.verify(str(first["storage_key"]), str(first["content_hash"])) is False

    second = storage.register_file(
        cur,
        project_id=project_id,
        filename="same-data.csv",
        stream=io.BytesIO(payload),
        media_type="text/csv",
    )

    assert second["id"] == first["id"]
    assert second["deduplicated"] is True
    assert storage.verify(str(first["storage_key"]), str(first["content_hash"])) is True
    assert path.read_bytes() == payload


def test_stored_bytes_can_be_reverified_against_the_cited_hash(cur):
    user = _user(cur)
    project_id = new_id("prj")
    cur.execute("INSERT INTO projects(id, owner_user_id, name) VALUES (%s, %s, 'P')",
                (project_id, user["id"]))
    record = storage.register_file(cur, project_id=project_id, filename="d.csv",
                                   stream=io.BytesIO(b"country,value\nIN,1\n"),
                                   media_type="text/csv")
    assert storage.verify(record["storage_key"], record["content_hash"]) is True
    assert storage.verify(record["storage_key"], "0" * 64) is False


def test_storage_key_cannot_escape_the_object_store(cur):
    with pytest.raises(storage.StorageError):
        storage.path_for("../../../../etc/passwd")


def test_blob_collection_refuses_a_noncanonical_key(tmp_path, monkeypatch):
    """A corrupt files.storage_key must never widen deletion inside the store."""
    monkeypatch.setattr(storage, "storage_root", lambda: tmp_path)
    digest = "0" * 64
    neighbour = tmp_path / "unrelated.txt"
    neighbour.write_bytes(b"keep me")

    result = storage.collect([(digest, "unrelated.txt")])

    assert result["removed"] == 0
    assert result["failed"] == 1
    assert neighbour.read_bytes() == b"keep me"


def test_blob_collection_rechecks_live_references_before_unlinking():
    """A stale orphan decision cannot delete bytes a concurrent upload now uses."""
    with connection() as conn, conn.cursor() as cur:
        user = _user(cur)
        project_id = new_id("prj")
        cur.execute(
            "INSERT INTO projects(id, owner_user_id, name) VALUES (%s, %s, 'Live')",
            (project_id, user["id"]),
        )
        record = storage.register_file(
            cur,
            project_id=project_id,
            filename="live.csv",
            stream=io.BytesIO(b"x\n1\n"),
            media_type="text/csv",
        )
        conn.commit()

    path = storage.path_for(str(record["storage_key"]))
    assert path.exists()

    result = storage.collect([
        (str(record["content_hash"]), str(record["storage_key"])),
    ])

    assert result["removed"] == 0
    assert result["kept"] == 1
    assert path.exists()


# ---------------------------------------------------------------------------
# Adding people, and changing a password
# ---------------------------------------------------------------------------

def test_a_second_account_can_be_created(cur):
    from throughline_domain import auth

    first = auth.create_user(cur, email="chen@lab.local", display_name="Chen",
                             password="correct-horse-battery")
    second = auth.create_user(cur, email="okafor@lab.local",
                              display_name="Okafor",
                              password="another-long-passphrase",
                              is_admin=False)

    assert second["id"] != first["id"]
    assert second["is_admin"] is False


def test_changing_a_password_signs_out_every_other_session(cur):
    """
    A password change is usually a response to the suspicion that someone else
    has the old one. Leaving their session alive is the single thing that would
    make the change pointless.
    """
    from throughline_domain import auth

    user = auth.create_user(cur, email="chen@lab.local", display_name="Chen",
                            password="correct-horse-battery")
    mine = auth.create_session(cur, user_id=user["id"])
    theirs = auth.create_session(cur, user_id=user["id"])

    auth.set_password(cur, user_id=user["id"], password="a-brand-new-passphrase")
    auth.destroy_other_sessions(cur, user_id=user["id"], keep_token=mine)

    assert auth.resolve_session(cur, mine) is not None, "my own session survives"
    assert auth.resolve_session(cur, theirs) is None, "theirs is gone"


def test_the_old_password_stops_working(cur):
    from throughline_domain import auth

    user = auth.create_user(cur, email="chen@lab.local", display_name="Chen",
                            password="correct-horse-battery")
    auth.set_password(cur, user_id=user["id"], password="a-brand-new-passphrase")

    assert auth.authenticate(cur, email="chen@lab.local",
                             password="correct-horse-battery") is None
    assert auth.authenticate(cur, email="chen@lab.local",
                             password="a-brand-new-passphrase") is not None


def test_a_short_password_is_refused_on_change(cur):
    """
    Twelve, not eight: this protects an entire research corpus and is typed once
    on a machine the researcher already controls.
    """
    from throughline_domain import auth

    user = auth.create_user(cur, email="chen@lab.local", display_name="Chen",
                            password="correct-horse-battery")

    with pytest.raises(auth.AuthError, match="12 characters"):
        auth.set_password(cur, user_id=user["id"], password="short")


def test_a_new_password_is_salted_afresh(cur):
    """
    Reusing the salt would make two hashes for one account comparable, which
    leaks whether the password actually changed.
    """
    from throughline_domain import auth

    user = auth.create_user(cur, email="chen@lab.local", display_name="Chen",
                            password="correct-horse-battery")
    cur.execute("SELECT password_salt FROM users WHERE id = %s", (user["id"],))
    before = cur.fetchone()["password_salt"]

    auth.set_password(cur, user_id=user["id"], password="correct-horse-battery")
    cur.execute("SELECT password_salt FROM users WHERE id = %s", (user["id"],))

    assert cur.fetchone()["password_salt"] != before
