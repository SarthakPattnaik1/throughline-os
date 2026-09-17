"""
The accumulating vocabulary — the only part of the system that learns.

These tests exist to hold a line that is easy to cross by accident. A system
that "improves the more you use it" is a good thing to build and a dangerous
thing to build carelessly: the improvement must be confined to *what terms mean*
and must never touch what counts as evidence.

So the suite asserts two things with equal force — that confirmed vocabulary is
reused, and that unconfirmed similarity resolves nothing at all.
"""

from __future__ import annotations

import pytest
from conftest import sign_in
from throughline_domain import claim_test, harmonize, vocabulary
from throughline_domain.ids import new_id


def _canonical(cur, project, name, label=None):
    cur.execute(
        "INSERT INTO canonical_variables(id, project_id, name, definition, "
        "semantic_type, display_label) VALUES (%s, %s, %s, '', 'continuous', %s) "
        "RETURNING id",
        (new_id("cvar"), project, name,
         label or name.replace("_", " ").title()))
    return cur.fetchone()["id"]


# ---------------------------------------------------------------------------
# Resolution happens through approved routes only
# ---------------------------------------------------------------------------

def test_a_canonical_name_resolves(cur, project):
    _canonical(cur, project, "antibiotic_consumption")
    found = vocabulary.resolve(cur, project_id=project,
                               phrase="antibiotic consumption")
    assert found["name"] == "antibiotic_consumption"
    assert found["via"] == "canonical"


def test_a_display_label_resolves(cur, project):
    _canonical(cur, project, "ddd_per_1000", label="Defined Daily Doses")
    found = vocabulary.resolve(cur, project_id=project,
                               phrase="defined daily doses")
    assert found["name"] == "ddd_per_1000"


def test_a_suggested_alias_resolves_nothing(cur, project):
    """
    The gate. A suggestion is a question, and until someone answers it the
    system must behave exactly as though the phrase were unknown — otherwise it
    has silently decided what a paper meant (LAW 4).
    """
    canonical = _canonical(cur, project, "antibiotic_consumption")
    vocabulary.suggest(cur, project_id=project, phrase="antibiotic exposure",
                       canonical_variable_id=canonical)

    assert vocabulary.resolve(cur, project_id=project,
                              phrase="antibiotic exposure") is None


def test_an_approved_alias_resolves(cur, project):
    canonical = _canonical(cur, project, "antibiotic_consumption")
    alias = vocabulary.suggest(cur, project_id=project,
                               phrase="antibiotic exposure",
                               canonical_variable_id=canonical)
    vocabulary.decide(cur, alias_id=alias["id"], status=vocabulary.APPROVED,
                      decided_by="usr_1")

    found = vocabulary.resolve(cur, project_id=project,
                               phrase="antibiotic exposure")
    assert found["name"] == "antibiotic_consumption"
    assert found["via"] == "alias"


def test_a_rejected_alias_stays_rejected(cur, project):
    """
    A researcher who rules that two terms are different must not have that
    ruling quietly reversed by the next paper that uses the word.
    """
    canonical = _canonical(cur, project, "antibiotic_consumption")
    alias = vocabulary.suggest(cur, project_id=project, phrase="antibiotic use",
                               canonical_variable_id=canonical)
    vocabulary.decide(cur, alias_id=alias["id"], status=vocabulary.REJECTED,
                      decided_by="usr_1")

    assert vocabulary.resolve(cur, project_id=project,
                              phrase="antibiotic use") is None
    assert vocabulary.suggest(cur, project_id=project, phrase="antibiotic use",
                              canonical_variable_id=canonical) is None


def test_an_unknown_phrase_resolves_to_nothing(cur, project):
    _canonical(cur, project, "antibiotic_consumption")
    assert vocabulary.resolve(cur, project_id=project,
                              phrase="rainfall") is None


def test_similarity_alone_never_resolves(cur, project):
    """
    There is deliberately no fuzzy fallback. "Antibiotic consumption rate" is
    very nearly "antibiotic consumption", and *very nearly* is exactly the
    standard this system refuses to work to.
    """
    _canonical(cur, project, "antibiotic_consumption")
    assert vocabulary.resolve(cur, project_id=project,
                              phrase="antibiotic consumption rate") is None


# ---------------------------------------------------------------------------
# Similarity asks, it does not answer
# ---------------------------------------------------------------------------

def test_candidates_surface_a_near_miss(cur, project):
    _canonical(cur, project, "antibiotic_consumption")
    near = vocabulary.candidates(cur, project_id=project,
                                 phrase="antibiotic exposure")
    assert near and near[0]["name"] == "antibiotic_consumption"


def test_candidates_ignore_unrelated_variables(cur, project):
    _canonical(cur, project, "rainfall_mm")
    assert vocabulary.candidates(cur, project_id=project,
                                 phrase="antibiotic exposure") == []


def test_the_claim_test_offers_candidates_instead_of_a_bare_refusal(cur, project):
    """
    Part H1 — the refusal has to teach. "Does 'antibiotic exposure' mean
    'Antibiotic Consumption'?" turns a dead end into one click.
    """
    from tests.test_claim_test import CLAIM, _dataset, _map

    data = _dataset(cur, project, name="panel", rows=180,
                    design="cross_sectional", columns=["ddd", "res_pct"])
    _map(cur, project, data["columns"]["ddd"], "antibiotic_consumption")
    _map(cur, project, data["columns"]["res_pct"], "resistance_prevalence")

    result = claim_test.assess_testability(
        cur, project_id=project,
        claim={**CLAIM, "exposure": "antibiotic exposure"},
        dataset_version_id=data["version_id"])

    assert result["testable"] is False
    assert any("Does 'antibiotic exposure' mean" in r
               for r in result["verdict"].remedies)


def test_an_approved_alias_makes_the_claim_testable(cur, project):
    """
    The payoff, and the whole of what "it learns" means here: one confirmation,
    and every later paper using that word resolves without asking again.
    """
    from tests.test_claim_test import CLAIM, _dataset, _map

    data = _dataset(cur, project, name="panel", rows=180,
                    design="cross_sectional", columns=["ddd", "res_pct"])
    _map(cur, project, data["columns"]["ddd"], "antibiotic_consumption")
    _map(cur, project, data["columns"]["res_pct"], "resistance_prevalence")

    cur.execute("SELECT id FROM canonical_variables WHERE project_id = %s "
                "AND name = 'antibiotic_consumption'", (project,))
    canonical = cur.fetchone()["id"]
    alias = vocabulary.suggest(cur, project_id=project,
                               phrase="antibiotic exposure",
                               canonical_variable_id=canonical)
    vocabulary.decide(cur, alias_id=alias["id"], status=vocabulary.APPROVED,
                      decided_by="usr_1")

    result = claim_test.assess_testability(
        cur, project_id=project,
        claim={**CLAIM, "exposure": "antibiotic exposure"},
        dataset_version_id=data["version_id"])

    assert result["testable"] is True
    assert result["exposure_column"] == "ddd"


# ---------------------------------------------------------------------------
# What the system claims to have learned must be checkable
# ---------------------------------------------------------------------------

def test_use_is_counted_so_the_claim_can_be_checked(cur, project):
    canonical = _canonical(cur, project, "antibiotic_consumption")
    alias = vocabulary.suggest(cur, project_id=project,
                               phrase="antibiotic exposure",
                               canonical_variable_id=canonical)
    vocabulary.decide(cur, alias_id=alias["id"], status=vocabulary.APPROVED,
                      decided_by="usr_1")

    for _ in range(3):
        vocabulary.resolve(cur, project_id=project, phrase="antibiotic exposure")

    assert vocabulary.learned(cur, project)["times_an_alias_resolved_a_term"] == 3


def test_the_system_states_what_does_not_learn(cur, project):
    """
    The claim "it improves the more you use it" is true of the vocabulary and
    must never become true of the statistics. A system that tuned its own
    thresholds against its own history would grow more confident exactly as it
    grew less trustworthy, so it says plainly that it does not.
    """
    report = vocabulary.learned(cur, project)
    assert "thresholds" in report["note"]
    assert "fixed" in report["note"]


def test_pending_aliases_are_listed_for_a_person(cur, project):
    canonical = _canonical(cur, project, "antibiotic_consumption")
    vocabulary.suggest(cur, project_id=project, phrase="antibiotic exposure",
                       canonical_variable_id=canonical, origin="paper",
                       origin_ref="src_1")

    waiting = vocabulary.pending(cur, project)
    assert len(waiting) == 1
    assert waiting[0]["alias"] == "antibiotic exposure"
    assert waiting[0]["canonical_label"] == "Antibiotic Consumption"


def test_an_alias_is_approved_or_rejected_and_nothing_else(cur, project):
    canonical = _canonical(cur, project, "x")
    alias = vocabulary.suggest(cur, project_id=project, phrase="y",
                               canonical_variable_id=canonical)
    with pytest.raises(ValueError):
        vocabulary.decide(cur, alias_id=alias["id"], status="maybe",
                          decided_by="usr_1")


# ---------------------------------------------------------------------------
# A phrase that names two variables names neither (T156)
# ---------------------------------------------------------------------------

def test_a_label_that_is_another_variables_name_resolves_to_neither(cur, project):
    """
    `resolve` was a `UNION ALL ... LIMIT 1` with no order, so a phrase matching
    two variables returned whichever row the planner produced first. Its own
    docstring names what that costs: a claim test answering a question the
    paper never asked. Refusing is the conservative answer both callers already
    handle — the claim test asks, reconciliation reports different constructs.
    """
    _canonical(cur, project, "consumption", label="Resistance")
    _canonical(cur, project, "resistance", label="Resistance rate")

    assert vocabulary.resolve(cur, project_id=project, phrase="resistance") is None


def test_a_variable_that_arrives_named_like_an_approved_alias_leaves_it_resolving_to_neither(
        cur, project):
    """
    The order a proposal cannot catch. `suggest` now refuses a phrase that is
    already another variable's name (T158), but an alias approved first and a
    dataset that later brings a variable of that name still meet — so the
    ambiguity reaches `resolve`, which has to refuse it on its own.

    (This test used to build the state by proposing the conflicting alias
    directly; that route is closed now, and a fixture the product cannot
    produce proves nothing.)
    """
    consumption = _canonical(cur, project, "antibiotic_consumption")
    alias = vocabulary.suggest(cur, project_id=project, phrase="resistance",
                               canonical_variable_id=consumption)
    vocabulary.decide(cur, alias_id=alias["id"], status=vocabulary.APPROVED,
                      decided_by="usr_1")
    # A later dataset proposes a variable of that name.
    harmonize._upsert_canonical(cur, project_id=project, name="resistance",
                                label="Resistance", definition="",
                                semantic_type="continuous", unit=None)

    assert vocabulary.resolve(cur, project_id=project, phrase="resistance") is None


def test_two_routes_to_the_same_variable_are_not_ambiguous(cur, project):
    """
    The refusal is about two *variables*, not two rows. A name and an approved
    alias that agree still resolve, and through the name — a confirmed
    canonical match needs no alias, so none is counted as having saved a step.
    """
    canonical = _canonical(cur, project, "antibiotic_consumption",
                           label="Antibiotic use")
    alias = vocabulary.suggest(cur, project_id=project, phrase="antibiotic use",
                               canonical_variable_id=canonical)
    vocabulary.decide(cur, alias_id=alias["id"], status=vocabulary.APPROVED,
                      decided_by="usr_1")

    found = vocabulary.resolve(cur, project_id=project, phrase="antibiotic use")
    assert found["name"] == "antibiotic_consumption"
    assert found["via"] == "canonical"
    assert vocabulary.learned(cur, project)["times_an_alias_resolved_a_term"] == 0


# ---------------------------------------------------------------------------
# A phrase that already names a variable cannot be proposed for another (T158)
# ---------------------------------------------------------------------------

def test_a_phrase_that_names_another_variable_cannot_be_proposed(cur, project):
    """
    `suggest` looked only at existing aliases, so a phrase could be proposed —
    and approved — for one variable while it was the name of another. Since
    T156 that approval makes the phrase resolve to nothing, so a single click
    would silently break a lookup that worked. Refused where it is proposed,
    which is the only point a person is there to be told.
    """
    consumption = _canonical(cur, project, "antibiotic_consumption")
    _canonical(cur, project, "resistance")

    with pytest.raises(vocabulary.AliasRefused) as refused:
        vocabulary.suggest(cur, project_id=project, phrase="resistance",
                           canonical_variable_id=consumption)
    # Names the variable it collides with, so the refusal can be acted on.
    assert "resistance" in str(refused.value)
    assert vocabulary.resolve(cur, project_id=project,
                              phrase="resistance")["name"] == "resistance"


def test_a_phrase_that_is_another_variables_label_cannot_be_proposed(cur, project):
    consumption = _canonical(cur, project, "antibiotic_consumption")
    _canonical(cur, project, "amr_pct", label="Resistance rate")

    with pytest.raises(vocabulary.AliasRefused):
        vocabulary.suggest(cur, project_id=project, phrase="resistance rate",
                           canonical_variable_id=consumption)


def test_a_phrase_that_names_the_same_variable_is_still_proposable(cur, project):
    """
    Not ambiguous — both routes lead to one variable — so not refused. Whether
    such an alias is worth recording is the reviewer's decision, not this one.
    """
    canonical = _canonical(cur, project, "antibiotic_consumption")
    assert vocabulary.suggest(cur, project_id=project, phrase="antibiotic consumption",
                              canonical_variable_id=canonical) is not None


def test_the_route_says_why_it_refused_rather_than_calling_it_a_ruling():
    """
    The route turned every `None` from `suggest` into "already has a ruling ...
    a rejected term is not re-proposed". Reusing `None` for this refusal would
    have told the researcher something false about their own vocabulary.
    """
    from fastapi.testclient import TestClient
    from throughline_api.app import app
    from throughline_domain.db import connection

    with TestClient(app) as client:
        try:
            sign_in(client, email="lead@lab.local", display_name="Lead")
            project = client.post("/api/projects", json={"name": "Vocabulary"}).json()["id"]

            with connection() as conn, conn.cursor() as cur:
                consumption = _canonical(cur, project, "antibiotic_consumption")
                _canonical(cur, project, "resistance")

            response = client.post(f"/api/projects/{project}/vocabulary", json={
                "phrase": "resistance", "canonical_variable_id": consumption})
            assert response.status_code == 409, response.text
            detail = response.json()["detail"]
            assert "ruling" not in detail and "rejected" not in detail
            assert "resistance" in detail
        finally:
            with connection() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM users")


# ---------------------------------------------------------------------------
# Project isolation (T185)
# ---------------------------------------------------------------------------

def test_a_foreign_canonical_variable_cannot_enter_this_projects_vocabulary(
        cur, project):
    other_user, other_project = new_id("usr"), new_id("prj")
    cur.execute(
        "INSERT INTO users(id, email, display_name, password_hash, password_salt) "
        "VALUES (%s, %s, 'Other', 'x', 'y')",
        (other_user, f"{other_user}@test.local"))
    cur.execute(
        "INSERT INTO projects(id, owner_user_id, name) VALUES (%s, %s, 'Other')",
        (other_project, other_user))
    foreign = _canonical(cur, other_project, "private_measure")

    with pytest.raises(vocabulary.AliasRefused, match="in this project"):
        vocabulary.suggest(
            cur, project_id=project, phrase="borrowed",
            canonical_variable_id=foreign)

    cur.execute(
        "SELECT id FROM variable_aliases WHERE project_id = %s",
        (project,))
    assert cur.fetchall() == []