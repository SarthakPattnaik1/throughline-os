"""
The session exploration ledger.

Correction over one discovery sweep was already correct arithmetic on the wrong
family. What is tested here is the family: every look at the data in a session,
across all the verbs, corrected together — and the one exemption that is
legitimate, which is a hypothesis registered before anybody looked.

The property most of these turn on is uncomfortable on purpose. A p-value is
worth less after twenty tests than after one, so the *same* result gets a worse
q-value as a session goes on. A system that quietly reported the first number
would be flattering the researcher, and that is the failure this exists to
prevent.
"""

from __future__ import annotations

import pytest
from throughline_domain import exploration
from conftest import make_enquiry
from throughline_domain import enquiry as enquiries
from throughline_domain.ids import new_id


@pytest.fixture()
def project(cur):
    user_id, project_id = new_id("usr"), new_id("prj")
    cur.execute(
        "INSERT INTO users(id, email, display_name, password_hash, password_salt) "
        "VALUES (%s, %s, 'Ledger', 'x', 'y')", (user_id, f"{user_id}@test.local"))
    cur.execute(
        "INSERT INTO projects(id, owner_user_id, name, research_question) "
        "VALUES (%s, %s, 'Ledger', 'q')", (project_id, user_id))
    return {"id": project_id, "user": user_id,
            "enquiry": make_enquiry(cur, project_id)}


def dataset_version(cur, project) -> str:
    """The chain a discovery run needs: source -> dataset -> version."""
    source_id, dataset_id, version_id = new_id("src"), new_id("dst"), new_id("dsv")
    cur.execute(
        "INSERT INTO sources(id, project_id, title, source_type) "
        "VALUES (%s, %s, 'A table', 'upload')", (source_id, project["id"]))
    cur.execute(
        "INSERT INTO datasets(id, project_id, source_id, name, format) "
        "VALUES (%s, %s, %s, 'panel', 'csv')",
        (dataset_id, project["id"], source_id))
    cur.execute(
        "INSERT INTO dataset_versions(id, dataset_id, version, content_hash) "
        "VALUES (%s, %s, 1, 'hash-1')", (version_id, dataset_id))
    return version_id


def look(cur, project, p=None, verb="discovery", prereg=None, what="a test"):
    return exploration.record(
        cur, enquiry_id=project["enquiry"], project_id=project["id"],
        verb=verb, description=what, p_value=p, preregistration_id=prereg)


# ---------------------------------------------------------------------------
# The family is the session, not the sweep
# ---------------------------------------------------------------------------

def test_looks_across_different_verbs_are_one_family(cur, project):
    """
    The whole point. A sweep, a claim test and a consistency check are three
    interrogations of the same data, and correcting each against only its own
    siblings reports the third with the confidence of the first.
    """
    look(cur, project, p=0.01, verb="discovery")
    look(cur, project, p=0.02, verb="claim_test")
    report = look(cur, project, p=0.03, verb="finding_consistency")

    assert report["looks"] == 3
    assert report["family_size"] == 3


def test_the_same_p_value_is_worth_less_later_in_a_session(cur, project):
    """The cost of having looked, made arithmetic."""
    first = look(cur, project, p=0.04)
    early_q = first["tests"][0]["q_value"]

    for _ in range(19):
        look(cur, project, p=0.5)

    later = exploration.ledger(cur, project["enquiry"])
    same_test = next(t for t in later["tests"] if t["id"] == first["recorded"]["id"])
    assert same_test["q_value"] > early_q


def test_a_strong_result_still_survives_a_large_family(cur, project):
    """Correction has to discriminate, not merely refuse.

    Its complement below already catches the crudest version of the opposite
    failure: it asserts a result survives before the noise arrives, so a
    correction that rejected *everything* would fail it. Checked by mutation
    rather than assumed — stubbing `benjamini_hochberg` to return all-False
    fails that test too.

    What nothing covered is survival in a *large* family. A correction that is
    right at a family of one and too aggressive at two hundred passes every
    other test in this file, and that is the interesting way to get this wrong:
    scepticism that scales faster than the evidence does. A machine that always
    says no is as useless as one that always says yes, and the value is
    entirely in telling them apart at the sizes people actually run.

    Two hundred looks with five genuine effects among them — the shape of a
    checkpoint sweep or a hyperparameter search rather than anything about
    papers. All five come back; none of the 195 nulls does.
    """
    real = {1e-7, 4e-7, 2e-6, 8e-6, 3e-5}
    for p_value in real:
        look(cur, project, p=p_value)
    # Enough noise to bury them, if the correction could not tell the
    # difference. Spread across the range a true null actually produces.
    for i in range(195):
        look(cur, project, p=0.05 + 0.95 * (i / 195))

    report = exploration.ledger(cur, project["enquiry"])
    survivors = [t for t in report["tests"] if t["survives"]]

    assert report["looks"] == 200
    assert len(survivors) == 5, (
        f"expected the five real effects to survive, got {len(survivors)}"
    )
    assert {t["p_value"] for t in survivors} == real


def test_a_borderline_result_stops_surviving_once_enough_looks_pile_up(cur, project):
    report = look(cur, project, p=0.04)
    assert report["tests"][0]["survives"] is True

    for _ in range(30):
        look(cur, project, p=0.6)

    final = exploration.ledger(cur, project["enquiry"])
    assert final["surviving"] == 0


def test_two_enquiries_do_not_pollute_each_other(cur, project):
    """A researcher returning tomorrow starts a new family, not a worse one."""
    look(cur, project, p=0.01)
    other = dict(project, enquiry=make_enquiry(cur, project["id"]))
    report = look(cur, other, p=0.01)
    assert report["looks"] == 1


# ---------------------------------------------------------------------------
# Looks that produced nothing still count
# ---------------------------------------------------------------------------

def test_a_refusal_is_counted_even_though_it_cannot_be_corrected(cur, project):
    """
    Leaving out the looks that found nothing is how a family of twenty gets
    reported as four.
    """
    look(cur, project, p=0.01)
    report = look(cur, project, p=None, verb="compatibility",
                  what="refused: no shared measurement")

    assert report["looks"] == 2
    assert report["family_size"] == 1
    assert report["uncorrectable"] == 1


def test_the_count_is_stated_in_words_not_only_as_a_number(cur, project):
    """`family_size: 23` means nothing to a reader who does not already know."""
    look(cur, project, p=0.01)
    report = look(cur, project, p=0.02)
    assert "2 looks at the data" in report["note"]
    assert "cost of having looked" in report["note"]


# ---------------------------------------------------------------------------
# Pre-registration: the one legitimate exemption
# ---------------------------------------------------------------------------

def test_a_registered_hypothesis_is_left_out_of_the_family(cur, project):
    registration = exploration.preregister(
        cur, project_id=project["id"], hypothesis="Use increases resistance.",
        predicted_direction="increase", author=project["user"])

    look(cur, project, p=0.20)
    report = look(cur, project, p=0.04, verb="claim_test",
                  prereg=registration["id"])

    assert report["recorded"]["confirmatory"] is True
    assert report["confirmatory"] == 1
    assert report["family_size"] == 1  # only the exploratory one


def test_a_prediction_with_no_direction_is_refused(cur, project):
    """
    A pre-registration that cannot be wrong is a description, and exempting a
    description would make this a way of laundering exploratory work.
    """
    with pytest.raises(ValueError, match="direction"):
        exploration.preregister(cur, project_id=project["id"],
                                hypothesis="Something happens.",
                                predicted_direction="unspecified")


def test_editing_the_hypothesis_afterwards_forfeits_the_exemption(cur, project):
    registration = exploration.preregister(
        cur, project_id=project["id"], hypothesis="Use increases resistance.",
        predicted_direction="increase")

    cur.execute("UPDATE preregistrations SET hypothesis = %s WHERE id = %s",
                ("Use decreases resistance.", registration["id"]))

    report = look(cur, project, p=0.04, verb="claim_test",
                  prereg=registration["id"])
    assert report["recorded"]["confirmatory"] is False
    assert "edited" in report["recorded"]["why"]


def test_a_registration_that_does_not_exist_is_not_believed(cur, project):
    report = look(cur, project, p=0.04, prereg="prereg_nonexistent")
    assert report["recorded"]["confirmatory"] is False
    assert report["confirmatory"] == 0


def test_ordering_comes_from_one_shared_counter(cur, project):
    """
    Why the two tables draw on a single sequence.

    `now()` is transaction-stable, so a registration and a test written together
    share a timestamp exactly and no clock can order them. Insertion order can —
    but only if both tables are counted by the same counter. Two independent
    BIGSERIALs would give two unrelated number lines, and comparing across them
    would be arithmetic that looks like a check and is not one.
    """
    registration = exploration.preregister(
        cur, project_id=project["id"], hypothesis="Registered first.",
        predicted_direction="increase")
    report = look(cur, project, p=0.04, prereg=registration["id"])

    cur.execute("SELECT sequence FROM exploration_tests WHERE id = %s",
                (report["recorded"]["id"],))
    test_sequence = cur.fetchone()["sequence"]
    assert registration["sequence"] < test_sequence
    assert report["recorded"]["confirmatory"] is True


def test_the_two_tables_share_one_number_line(cur, project):
    """
    The check above passes even with two independent counters, by luck of which
    number each happens to be on. This is the one that does not: interleaved
    writes across both tables have to come out strictly increasing, which is only
    true if a single sequence issues every value.
    """
    issued: list[int] = []
    for index in range(3):
        registration = exploration.preregister(
            cur, project_id=project["id"], hypothesis=f"Hypothesis {index}.",
            predicted_direction="increase")
        issued.append(registration["sequence"])

        report = look(cur, project, p=0.1)
        cur.execute("SELECT sequence FROM exploration_tests WHERE id = %s",
                    (report["recorded"]["id"],))
        issued.append(cur.fetchone()["sequence"])

    assert issued == sorted(issued)
    assert len(set(issued)) == len(issued)


# ---------------------------------------------------------------------------
# Restraint
# ---------------------------------------------------------------------------

def test_an_empty_session_says_so_without_inventing_a_correction(cur, project):
    report = exploration.ledger(cur, project["enquiry"])
    assert report["looks"] == 0
    assert report["tests"] == []
    assert "first result needs no correction" in report["note"]


def test_the_ledger_is_derived_and_stores_nothing(cur, project):
    look(cur, project, p=0.01)
    cur.execute("SELECT count(*) AS n FROM exploration_tests WHERE enquiry_id = %s",
                (project["enquiry"],))
    before = cur.fetchone()["n"]

    exploration.ledger(cur, project["enquiry"])
    exploration.ledger(cur, project["enquiry"])

    cur.execute("SELECT count(*) AS n FROM exploration_tests WHERE enquiry_id = %s",
                (project["enquiry"],))
    assert cur.fetchone()["n"] == before


def test_an_unknown_verb_is_refused_rather_than_recorded(cur, project):
    with pytest.raises(ValueError, match="verb"):
        exploration.record(cur, enquiry_id=project["enquiry"],
                           project_id=project["id"], verb="vibes",
                           description="a look")


# ---------------------------------------------------------------------------
# The verbs feed it
# ---------------------------------------------------------------------------
#
# Until these existed the ledger had no source but a manual API call, so in
# practice it read zero forever — and a library note would say the count was
# "not recorded" on every finding, which reads as *this does not apply* rather
# than *nobody counted*. A ledger nothing populates is not a safeguard.

def test_a_discovery_sweep_counts_every_pair_it_tested(cur, project):
    """
    The case that produces the most tests by far. A sweep of twelve pairs is a
    family of twelve, and it forms without anybody remembering to say so.
    """
    from throughline_domain import discovery

    run_id = discovery.create_run(cur, project_id=project["id"],
                                  dataset_version_id=dataset_version(cur, project))
    for index in range(12):
        discovery.record_connection(
            cur, project_id=project["id"], discovery_run_id=run_id,
            candidate={"left_variable": f"x{index}", "right_variable": "y",
                       "method": "spearman", "rationale": "both numeric"},
            analysis_run_id=None,
            result={"p_value": 0.01 * (index + 1), "sample_size": 40,
                    "evidence_quality": "moderate"},
            q_value=None)

    report = exploration.ledger(cur, enquiries.standalone_id(run_id))
    assert report["looks"] == 12
    assert report["family_size"] == 12


def test_a_pair_that_could_not_be_tested_still_counts_as_a_look(cur, project):
    from throughline_domain import discovery

    run_id = discovery.create_run(cur, project_id=project["id"],
                                  dataset_version_id=dataset_version(cur, project))
    discovery.record_connection(
        cur, project_id=project["id"], discovery_run_id=run_id,
        candidate={"left_variable": "a", "right_variable": "b",
                   "method": "spearman", "rationale": "both numeric"},
        analysis_run_id=None, result={"sample_size": 3}, q_value=None)

    report = exploration.ledger(cur, enquiries.standalone_id(run_id))
    assert report["looks"] == 1
    assert report["family_size"] == 0
    assert report["uncorrectable"] == 1


def test_a_sweep_joins_a_wider_session_when_one_is_given(cur, project):
    """
    The default makes a sweep its own family, which is right on its own. Given a
    session, the sweep joins everything else the researcher has looked at — and
    that wider family is the one the correction should really run over.
    """
    from throughline_domain import discovery

    enquiry_id = make_enquiry(cur, project["id"])
    exploration.record(cur, enquiry_id=enquiry_id, project_id=project["id"],
                       verb="claim_test", description="a claim", p_value=0.02)

    run_id = discovery.create_run(cur, project_id=project["id"],
                                  dataset_version_id=dataset_version(cur, project))
    discovery.record_connection(
        cur, project_id=project["id"], discovery_run_id=run_id,
        candidate={"left_variable": "a", "right_variable": "b",
                   "method": "spearman", "rationale": "both numeric"},
        analysis_run_id=None, result={"p_value": 0.03}, q_value=None,
        enquiry_id=enquiry_id)

    assert exploration.ledger(cur, enquiry_id)["looks"] == 2
    assert exploration.ledger(cur, enquiries.standalone_id(run_id))["looks"] == 0


# ---------------------------------------------------------------------------
# A sweep joins the researcher's session
# ---------------------------------------------------------------------------
#
# The sweep does not happen during the request that starts it: the API queues a
# run and returns, and a worker records the tested pairs minutes later. The run
# row is the only place the session can survive that gap, which is why it is
# stored rather than passed.

def test_a_run_remembers_the_session_that_started_it(cur, project):
    from throughline_domain import discovery

    enquiry_id = make_enquiry(cur, project["id"])
    run_id = discovery.create_run(
        cur, project_id=project["id"],
        dataset_version_id=dataset_version(cur, project),
        enquiry_id=enquiry_id)

    cur.execute("SELECT enquiry_id FROM discovery_runs WHERE id = %s", (run_id,))
    assert cur.fetchone()["enquiry_id"] == enquiry_id


def test_a_sweep_started_in_a_session_joins_that_family(cur, project):
    """
    The point of the whole column. Without it a sweep is its own family and a
    claim tested in the same sitting is corrected as though nobody had looked.
    """
    from throughline_domain import discovery

    enquiry_id = make_enquiry(cur, project["id"])
    exploration.record(cur, enquiry_id=enquiry_id, project_id=project["id"],
                       verb="claim_test", description="a claim", p_value=0.02)

    run_id = discovery.create_run(
        cur, project_id=project["id"],
        dataset_version_id=dataset_version(cur, project), enquiry_id=enquiry_id)

    cur.execute("SELECT enquiry_id FROM discovery_runs WHERE id = %s", (run_id,))
    carried = cur.fetchone()["enquiry_id"]
    for index in range(3):
        discovery.record_connection(
            cur, project_id=project["id"], discovery_run_id=run_id,
            candidate={"left_variable": f"x{index}", "right_variable": "y",
                       "method": "spearman", "rationale": "both numeric"},
            analysis_run_id=None, result={"p_value": 0.04}, q_value=None,
            enquiry_id=carried)

    assert exploration.ledger(cur, enquiry_id)["looks"] == 4
    assert exploration.ledger(cur, enquiries.standalone_id(run_id))["looks"] == 0


def test_a_run_with_no_session_stays_its_own_family(cur, project):
    """
    A script or an older client has no session. Inventing one would drop
    unrelated work into somebody's family and make their results look worse
    than they are, so the run remains what it already was.
    """
    from throughline_domain import discovery

    run_id = discovery.create_run(
        cur, project_id=project["id"],
        dataset_version_id=dataset_version(cur, project))

    cur.execute("SELECT enquiry_id FROM discovery_runs WHERE id = %s", (run_id,))
    assert cur.fetchone()["enquiry_id"] is None

    discovery.record_connection(
        cur, project_id=project["id"], discovery_run_id=run_id,
        candidate={"left_variable": "a", "right_variable": "b",
                   "method": "spearman", "rationale": "both numeric"},
        analysis_run_id=None, result={"p_value": 0.04}, q_value=None)

    assert exploration.ledger(cur, enquiries.standalone_id(run_id))["looks"] == 1


def test_a_look_cannot_be_recorded_into_another_projects_enquiry(cur, project):
    """
    Held in `record` itself, not only at the routes. Three routes took an
    enquiry id from the request and one of them reached this with nothing in
    between; a check that lives only at the edges is one new caller away from
    changing someone else's correction again (T161).
    """
    other_user, other_project = new_id("usr"), new_id("prj")
    cur.execute(
        "INSERT INTO users(id, email, display_name, password_hash, password_salt) "
        "VALUES (%s, %s, 'Other', 'x', 'y')", (other_user, f"{other_user}@test.local"))
    cur.execute("INSERT INTO projects(id, owner_user_id, name) VALUES (%s, %s, 'Other')",
                (other_project, other_user))
    theirs = make_enquiry(cur, other_project)

    with pytest.raises(ValueError, match="does not belong to this project"):
        exploration.record(cur, enquiry_id=theirs, project_id=project["id"],
                           verb="discovery", description="planted", p_value=0.9)

    assert exploration.ledger(cur, theirs)["looks"] == 0


# ---------------------------------------------------------------------------
# Every optional reference stays inside the project (T185)
# ---------------------------------------------------------------------------

def _other_project(cur):
    user_id, project_id = new_id("usr"), new_id("prj")
    cur.execute(
        "INSERT INTO users(id, email, display_name, password_hash, password_salt) "
        "VALUES (%s, %s, 'Other', 'x', 'y')",
        (user_id, f"{user_id}@test.local"))
    cur.execute(
        "INSERT INTO projects(id, owner_user_id, name) VALUES (%s, %s, 'Other')",
        (project_id, user_id))
    return {"id": project_id, "user": user_id,
            "enquiry": make_enquiry(cur, project_id)}


def test_a_foreign_preregistration_cannot_claim_an_exemption(cur, project):
    other = _other_project(cur)
    foreign = exploration.preregister(
        cur, project_id=other["id"], hypothesis="Private hypothesis.",
        predicted_direction="increase", author=other["user"])

    with pytest.raises(ValueError, match="in this project"):
        look(cur, project, p=0.01, prereg=foreign["id"])

    assert exploration.ledger(cur, project["enquiry"])["looks"] == 0


def test_a_foreign_spec_cannot_be_attached_to_this_projects_ledger(cur, project):
    other = _other_project(cur)
    spec_id = new_id("asp")
    cur.execute(
        "INSERT INTO analysis_specs("
        "id, project_id, schema_version, analysis_type, research_question, "
        "dataset_version_ids, variables, filters, transformations, method, "
        "method_rationale, parameters, confidence_level, assumptions, "
        "outputs_requested, visualization_intent, random_seed, content_hash"
        ") VALUES (%s, %s, 1, 'association', 'q', '[]', '{}', '[]', '[]', "
        "'pearson_correlation', '', '{}', 0.95, '[]', '[]', '', 1, 'foreign')",
        (spec_id, other["id"]))

    with pytest.raises(ValueError, match="analysis specification"):
        exploration.record(
            cur, enquiry_id=project["enquiry"], project_id=project["id"],
            verb="analysis", description="foreign spec", spec_id=spec_id)

    assert exploration.ledger(cur, project["enquiry"])["looks"] == 0
