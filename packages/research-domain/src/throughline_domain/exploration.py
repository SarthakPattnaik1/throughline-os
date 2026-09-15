"""
How many times the data has been looked at, and what that costs.

Multiple-comparison correction was already here, and it was correct — but it ran
over one discovery sweep at a time. That is the wrong family. A researcher who
runs a sweep, then tests a claim from a paper, then checks a finding for
consistency, then reconciles two studies has interrogated the same dataset four
times. Correcting each of those against only its own siblings reports the fourth
look with the confidence of the first, which is precisely the arithmetic that
makes a garden of forking paths look like a result.

So the family is the line of enquiry, across every verb. Three consequences
follow, and all three are deliberate rather than incidental:

**A finding's q-value moves as you keep looking.** The same p-value is worth less
after twenty tests than after one, and this reports it that way. Researchers find
this uncomfortable, which is the point: the discomfort is the information. A
system that quietly held the first number would be flattering them.

**A refusal still counts.** A comparison the platform declined, or one that
produced no test statistic, has no p-value and cannot join the correction — but
it was still a look at the data, and it is counted and shown. Hiding the looks
that produced nothing is how a count of twenty becomes a reported four.

**Pre-registration is an exemption, and that is the whole reason it exists here.**
A hypothesis registered before the data was examined is confirmatory: it was not
selected by looking, so it does not inflate the family. This is the mechanism
that gives `preRegistered` something to be true about. It is also why the ordering
below is enforced on insertion order rather than on clocks — `now()` is
transaction-stable in PostgreSQL, so a registration and a test written together
share a timestamp exactly, and "was this registered before it was tested" is the
only question that makes any of this mean anything.
"""

from __future__ import annotations

import hashlib
from typing import Any

from .db import jsonb
from .discovery import benjamini_hochberg
from .ids import new_id

VERBS = ("discovery", "claim_test", "compatibility", "finding_consistency",
         "paper_reconciliation", "image_similarity", "analysis")

DIRECTIONS = ("increase", "decrease", "difference", "no_effect")


def _hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Registering a hypothesis before looking
# ---------------------------------------------------------------------------

def preregister(cur, *, project_id: str, hypothesis: str,
                predicted_direction: str, outcome: str | None = None,
                exposure: str | None = None, method: str | None = None,
                design: str | None = None,
                covariates: list[str] | None = None,
                filters: list[Any] | None = None,
                falsified_if: str | None = None,
                author: str | None = None) -> dict[str, Any]:
    """
    Record a hypothesis, and the direction it predicts, before testing it.

    The direction is required rather than optional. "Antibiotic use is associated
    with resistance" is compatible with any outcome; "antibiotic use *increases*
    resistance" can be wrong. A pre-registration that cannot fail is a
    description, and exempting a description from multiple-comparison correction
    would turn this whole mechanism into a way of laundering exploratory work.
    """
    hypothesis = (hypothesis or "").strip()
    if not hypothesis:
        raise ValueError("A pre-registration needs a hypothesis to register.")
    if predicted_direction not in DIRECTIONS:
        raise ValueError(
            f"predicted_direction must be one of {DIRECTIONS}; got "
            f"{predicted_direction!r}. A prediction with no direction cannot be "
            "wrong, and only a prediction that can be wrong earns the exemption.")

    # The plan is optional, and its absence is recorded rather than assumed.
    #
    # A registration with a hypothesis and no plan still earns the exemption on
    # its text — that is how every row already in this table works, and breaking
    # them would be rewriting history. But `plan_hash` stays null, and every
    # report says the analysis could not be checked against it, which is the
    # difference between "matched" and "not comparable".
    from . import deviations

    stated = any(x is not None for x in (method, design, covariates, filters))
    computed_plan_hash = deviations.plan_hash(
        method=method, design=design, covariates=covariates, filters=filters
    ) if stated else None

    registration_id = new_id("prereg")
    cur.execute(
        "INSERT INTO preregistrations(id, project_id, hypothesis, "
        "predicted_direction, outcome, exposure, locked_hash, created_by, "
        "planned_method, planned_design, planned_covariates, planned_filters, "
        "falsified_if, plan_hash) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING sequence, created_at",
        (registration_id, project_id, hypothesis, predicted_direction,
         outcome, exposure, _hash(hypothesis), author,
         method, design,
         jsonb(covariates) if covariates is not None else None,
         jsonb(filters) if filters is not None else None,
         falsified_if, computed_plan_hash))
    row = cur.fetchone()

    return {
        "id": registration_id,
        "hypothesis": hypothesis,
        "predicted_direction": predicted_direction,
        "plan_recorded": computed_plan_hash is not None,
        "sequence": row["sequence"],
        "created_at": row["created_at"],
        "note": ("Registered. A result testing this is confirmatory and is left "
                 "out of the exploratory family — provided the test comes after "
                 "this registration, and the analysis that runs is the one "
                 "registered. Both are checked rather than trusted."
                 if computed_plan_hash is not None else
                 "Registered. No analysis plan was recorded, so a later result "
                 "can be checked against this hypothesis but not against the "
                 "analysis it intended — naming the method and any adjustment "
                 "is what makes the exemption checkable."),
    }


def _registration(cur, registration_id: str) -> dict[str, Any] | None:
    cur.execute(
        "SELECT id, project_id, hypothesis, predicted_direction, locked_hash, sequence "
        "FROM preregistrations WHERE id = %s", (registration_id,))
    return cur.fetchone()


# ---------------------------------------------------------------------------
# Recording a look
# ---------------------------------------------------------------------------

def record(cur, *, enquiry_id: str, project_id: str, verb: str,
           description: str, p_value: float | None = None,
           preregistration_id: str | None = None,
           spec_id: str | None = None,
           analysis_run_id: str | None = None) -> dict[str, Any]:
    """
    Record one look at the data.

    Returns what the ledger says *after* this test, not before, because the
    number a researcher needs is the one that accounts for the look they just
    took.

    A claimed pre-registration is verified here rather than believed. Ways it
    fails, all reported rather than silently downgraded: the registration was
    written after the test it supposedly predicted, its text has been edited
    since, or — when the analysis is named — the analysis that ran is not the
    analysis that was registered. Any of them turns the test back into an
    exploratory one, which is the honest outcome and the one a researcher needs
    before they write it up.

    That last check is the one that makes the others mean anything. Until it
    existed, the exemption asked only whether a registration *existed*, was
    unedited and came first — all three of which are true of an analysis with
    nothing to do with the plan. Register one comparison, run forty-seven
    variants, claim the winner as confirmatory: every check passed, because
    nothing looked at the content. `spec_id` is optional so that callers with no
    recorded spec still work, but a test that names one has its plan checked.
    """
    if verb not in VERBS:
        raise ValueError(f"verb must be one of {VERBS}; got {verb!r}")

    # The family is the enquiry's, and so is its correction. An enquiry from
    # another project would take this look into someone else's ledger and
    # change which of *their* results survive. The route checks this too; it
    # is here so that no other caller has to remember to (T161).
    cur.execute("SELECT project_id FROM enquiries WHERE id = %s", (enquiry_id,))
    owner = cur.fetchone()
    if owner is None or owner["project_id"] != project_id:
        raise ValueError("That line of enquiry does not belong to this project.")

    # Optional references are tenant boundaries too. A foreign key proves
    # that a row exists; it does not prove that it belongs to this project.
    # Validate them here so every caller is protected, including callers that
    # do not pass a pre-registration and therefore never call deviations.compare.
    for table, reference, label in (
        ("analysis_specs", spec_id, "analysis specification"),
        ("analysis_runs", analysis_run_id, "analysis run"),
    ):
        if reference:
            cur.execute(
                f"SELECT id FROM {table} WHERE id = %s AND project_id = %s",
                (reference, project_id))
            if not cur.fetchone():
                raise ValueError(f"No such {label} in this project.")

    confirmatory, why = False, None
    # The claim is only storable when the thing claimed exists — a foreign key
    # cannot point at a registration nobody wrote, and a caller quoting an id
    # that was never registered has already been told so in `why`.
    claimed = None
    if preregistration_id:
        registration = _registration(cur, preregistration_id)
        if registration is not None and registration["project_id"] != project_id:
            raise ValueError("No such pre-registration in this project.")
        if registration is None:
            why = "No such pre-registration; counted as exploratory."
        elif registration["locked_hash"] != _hash(registration["hypothesis"]):
            claimed = preregistration_id
            why = ("The registered hypothesis has been edited since it was "
                   "registered, so it no longer predicts anything it did not "
                   "already know. Counted as exploratory.")
        else:
            claimed = preregistration_id
            confirmatory, why = True, "Registered before this test."
            if spec_id:
                from . import deviations

                comparison = deviations.compare(
                    cur, registration_id=preregistration_id, spec_id=spec_id)
                if not comparison["plan_recorded"]:
                    why = ("Registered before this test. The registration "
                           "records no analysis plan, so what ran could not be "
                           "checked against it.")
                elif not comparison["matches_plan"]:
                    fields = ", ".join(
                        sorted({d["field"] for d in comparison["deviations"]}))
                    confirmatory = False
                    why = (
                        f"The analysis that ran differs from the one registered "
                        f"({fields}), so this result was not predicted by the "
                        "registration. Counted as exploratory and corrected with "
                        "the rest of the family — which is what it is. The "
                        "deviation is not misconduct; keeping the exemption "
                        "would be.")

    test_id = new_id("xtest")
    cur.execute(
        # `preregistration_id` is the exemption and is stored only when earned,
        # because the ledger reads it to decide what joins the family.
        # `claimed_registration_id` is the claim, kept either way — a deviation
        # whose claim was discarded is one nobody can state deliberately later.
        "INSERT INTO exploration_tests(id, enquiry_id, project_id, verb, "
        "description, p_value, preregistration_id, claimed_registration_id, "
        "spec_id, deviation_note, analysis_run_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING sequence",
        (test_id, enquiry_id, project_id, verb, description, p_value,
         preregistration_id if confirmatory else None, claimed,
         spec_id, None if confirmatory else why, analysis_run_id))
    sequence = cur.fetchone()["sequence"]

    # Ordering, not clocks. A registration written in the same transaction as the
    # test shares its timestamp exactly, so a timestamp comparison would pass for
    # a hypothesis registered after the answer was known.
    if confirmatory and registration["sequence"] > sequence:
        confirmatory = False
        why = ("Registered after this test ran, so it did not predict anything. "
               "Counted as exploratory.")
        cur.execute("UPDATE exploration_tests SET preregistration_id = NULL "
                    "WHERE id = %s", (test_id,))

    report = ledger(cur, enquiry_id)
    report["recorded"] = {
        "id": test_id, "verb": verb, "description": description,
        "p_value": p_value, "confirmatory": confirmatory, "why": why,
    }
    return report


# ---------------------------------------------------------------------------
# What the line of enquiry has spent
# ---------------------------------------------------------------------------

def attach_result(cur, *, analysis_run_id: str,
                  p_value: float | None) -> bool:
    """Give a recorded look the p-value its run eventually produced.

    An analysis is counted when it is *specified*, which is the only honest
    moment: a look recorded after the number exists is one a researcher could
    decline to record having seen it, and the family would then hold exactly
    the tests that worked. The cost of that ordering is that the look starts
    with no p-value, and the ledger corrects only tests that have one — so
    every specified analysis counted as `uncorrectable` and never joined
    `family_size`. The tests a researcher deliberately chose to run were the
    only ones escaping correction, and the error ran in the flattering
    direction.

    **Written once.** `WHERE p_value IS NULL` is the whole guarantee: a look
    cannot acquire a second, more convenient number by being re-recorded. A run
    that failed passes `None` and stays uncorrectable, which is right — it
    questioned the data and produced no statistic, the same as a comparison the
    system refused.

    Returns whether a look was updated, so a caller can tell "there was no such
    look" from "it already had one" only by asking; both are ordinary.
    """
    if p_value is None:
        return False
    cur.execute(
        "UPDATE exploration_tests SET p_value = %s "
        "WHERE analysis_run_id = %s AND p_value IS NULL",
        (float(p_value), analysis_run_id),
    )
    return cur.rowcount > 0


def ledger(cur, enquiry_id: str) -> dict[str, Any]:
    """
    Every look this line of enquiry has taken, and what the family costs.

    Derived on read and stored nowhere. A written ledger is a second copy of the
    truth, and a second copy is a thing that can disagree with the first — the
    same reason the notebook index is computed rather than saved.
    """
    cur.execute(
        "SELECT id, verb, description, p_value, preregistration_id, sequence "
        "FROM exploration_tests WHERE enquiry_id = %s ORDER BY sequence",
        (enquiry_id,))
    tests = cur.fetchall()

    if not tests:
        return {
            "enquiry_id": enquiry_id, "looks": 0, "family_size": 0,
            "confirmatory": 0, "uncorrectable": 0, "tests": [],
            "note": ("Nothing has been tested in this line of enquiry yet. The first "
                     "result needs no correction; the twentieth does."),
        }

    exploratory = [t for t in tests
                   if t["preregistration_id"] is None and t["p_value"] is not None]
    corrected = benjamini_hochberg([float(t["p_value"]) for t in exploratory])
    by_id = {t["id"]: c for t, c in zip(exploratory, corrected)}

    rows = []
    for test in tests:
        outcome = by_id.get(test["id"])
        rows.append({
            "id": test["id"],
            "verb": test["verb"],
            "description": test["description"],
            "p_value": float(test["p_value"]) if test["p_value"] is not None else None,
            "confirmatory": test["preregistration_id"] is not None,
            "q_value": outcome["q_value"] if outcome else None,
            "survives": outcome["survives"] if outcome else None,
        })

    confirmatory = sum(1 for t in tests if t["preregistration_id"] is not None)
    uncorrectable = sum(1 for t in tests
                        if t["preregistration_id"] is None and t["p_value"] is None)
    survivors = sum(1 for r in rows if r["survives"])

    return {
        "enquiry_id": enquiry_id,
        "looks": len(tests),
        "family_size": len(exploratory),
        "confirmatory": confirmatory,
        "uncorrectable": uncorrectable,
        "surviving": survivors,
        "tests": rows,
        "note": _summary(len(tests), len(exploratory), confirmatory,
                         uncorrectable, survivors),
    }


def _summary(looks: int, family: int, confirmatory: int, uncorrectable: int,
             survivors: int) -> str:
    """
    The count in a sentence, because the number alone does not carry the point.

    A researcher reading "family_size: 23" has to already know what it implies.
    The sentence says the thing the number means: you have looked this many
    times, and that is why these p-values are worth less than they appear.
    """
    parts = [f"{looks} look{'' if looks == 1 else 's'} at the data in this "
             f"line of enquiry."]
    if family:
        parts.append(
            f"{family} carried a p-value and were corrected together as one "
            f"family; {survivors} survive at a 5% false-discovery rate. A test "
            "run later in an enquiry is held to a stricter bar than the same test "
            "run first, which is the cost of having looked.")
    if confirmatory:
        parts.append(
            f"{confirmatory} tested a hypothesis registered beforehand and are "
            "excluded from that family — they were not selected by looking.")
    if uncorrectable:
        parts.append(
            f"{uncorrectable} produced no test statistic — a refusal, or a "
            "comparison with nothing to test — so they cannot be corrected. They "
            "are counted anyway: leaving out the looks that found nothing is how "
            "a family of twenty gets reported as four.")
    return " ".join(parts)


__all__ = ["DIRECTIONS", "VERBS", "ledger", "preregister", "record"]
