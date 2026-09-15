"""
The claim test — paper ↔ dataset (Part I, taxonomy pair 2).

The wedge, and the pair with the largest outcome space. Doing it well means
refusing well, and the checks run in the order that protects the researcher
soonest:

1. **Circularity (P7) first.** If the paper was written from this dataset, every
   other check is moot: re-running a paper's analysis on the paper's own data is
   arithmetic, not evidence, and announcing it as a replication would be a
   tautology dressed as a finding. Cheap to detect, so it goes before anything
   that costs.
2. **Constructs (P8)** — through the approved canonical variable layer only,
   never string similarity.
3. **Design (P9)** — a cohort claim is not testable on a cross-sectional panel
   however many columns match.
4. **Scope (P10, P11)** — checked only when both sides record it, and reported
   as *unchecked* rather than passed when they do not.
5. **Adjudication** — and here **power (P5) decides whether a null means
   anything at all**. Failure to detect is not absence; a system that reports an
   underpowered null as "not supported" makes the most common inferential error
   in replication tooling, with the full authority of an interface.

Only step 0 — locating a claim in prose — needs a model. Everything from
circularity onward is deterministic, so the verdict is reproducible and a
local-only installation produces exactly what any other would.
"""

from __future__ import annotations

import math
import re
from typing import Any

from . import causal, discovery, harmonize, vocabulary
from .db import jsonb
from .ids import new_id
from .verdicts import Family, RunState, Verdict


class ClaimTestError(RuntimeError):
    """A claim could not be located or adjudicated."""


# ---------------------------------------------------------------------------
# P7 — circularity, checked before anything else
# ---------------------------------------------------------------------------

#: Phrases that introduce a data availability statement. A paper naming this
#: dataset inside one of these is describing the data it was written from.
_AVAILABILITY = re.compile(
    r"(?i)\b(data\s+availability|data\s+and\s+materials|we\s+(?:used|analy[sz]ed)|"
    r"data\s+were\s+obtained|derived\s+from|drawn\s+from|accession|"
    r"deposited\s+in|available\s+(?:at|from))\b")

_ACCESSION = re.compile(r"\b(?:GSE|PRJNA|SRR|E-MTAB|PXD)\d{3,}\b")

#: How many passages after a heading still count as inside its section. Three is
#: enough for a heading, a sentence and its continuation, and short enough that
#: a data statement cannot reach across into an unrelated section.
_SECTION_WINDOW = 3


def _distinctive(text: str) -> set[str]:
    """
    Tokens distinctive enough that a shared one means something.

    Short and common words are dropped: a paper and a dataset both containing
    "data" or "study" is not evidence of anything, and a circularity check that
    fired on those would cry wolf until nobody read it.
    """
    stop = {"data", "dataset", "study", "final", "clean", "cleaned", "raw",
            "analysis", "results", "table", "file", "csv", "xlsx", "sheet",
            "version", "copy", "full", "the", "and", "for", "with", "from"}
    tokens = re.split(r"[^A-Za-z0-9]+", text.lower())
    return {t for t in tokens if len(t) >= 4 and t not in stop and not t.isdigit()}


def check_circularity(cur, *, source_id: str, dataset_version_id: str
                      ) -> dict[str, Any] | None:
    """
    Was this paper written from this dataset?

    Returns the evidence, or None. Deliberately evidence-first, and this outcome
    routes to *needs review* rather than an automatic refusal: the system is
    asserting something about provenance, and a human should confirm it.
    """
    cur.execute(
        "SELECT dv.storage_key, s.title AS dataset_title, s.metadata AS meta, "
        "       s.id AS dataset_source_id "
        "FROM dataset_versions dv "
        "JOIN datasets d ON d.id = dv.dataset_id "
        "JOIN sources s ON s.id = d.source_id WHERE dv.id = %s",
        (dataset_version_id,))
    dataset = cur.fetchone()
    if not dataset:
        return None

    metadata = dataset["meta"] or {}
    dataset_doi = str(metadata.get("doi") or "").lower()
    dataset_accessions = {a.upper() for a in _ACCESSION.findall(
        f"{dataset['dataset_title']} {metadata.get('description', '')}")}
    dataset_tokens = _distinctive(dataset["dataset_title"])

    cur.execute(
        "SELECT id, content, locator FROM passages WHERE source_id = %s "
        "ORDER BY ordinal", (source_id,))
    passages = list(cur.fetchall())

    for index, passage in enumerate(passages):
        # A heading scopes what follows it. Chunkers split "## Data
        # availability" from the sentence underneath, so a check that required
        # both in one passage would miss every real paper — which is exactly
        # what happened the first time this ran against one.
        window = passages[index:index + _SECTION_WINDOW]
        content = "\n".join(p["content"] for p in window)

        # Strongest signal: the same DOI or accession number in both.
        if dataset_doi and dataset_doi in content.lower():
            return {"kind": "doi", "confidence": 0.95,
                    "detail": f"The paper cites DOI {dataset_doi}, which is this "
                              "dataset's own identifier.",
                    "evidence_refs": [passage["id"]]}
        shared = dataset_accessions & set(_ACCESSION.findall(content))
        if shared:
            return {"kind": "accession", "confidence": 0.95,
                    "detail": f"The paper names accession {', '.join(sorted(shared))}"
                              ", which is this dataset.",
                    "evidence_refs": [passage["id"]]}

        # Weaker: the dataset is named inside a data availability statement.
        if _AVAILABILITY.search(content):
            overlap = dataset_tokens & _distinctive(content)
            # Two shared tokens, or one rare enough to stand alone. Real dataset
            # names are short — `amr_surveillance_2019` yields one usable token
            # once the year and the three-letter acronym are dropped — so a flat
            # two-token rule would miss the ordinary case this check exists for.
            if len(overlap) >= 2 or any(len(t) >= 10 for t in overlap):
                return {
                    "kind": "availability_statement",
                    # Lower, and said so: prose overlap is suggestive, not proof.
                    "confidence": 0.55,
                    "detail": (f"the paper's data statement at {passage['locator']} "
                               f"names {' and '.join(sorted(overlap))}, which also "
                               "identify this dataset"),
                    "evidence_refs": [passage["id"]],
                }

    # Provenance this system recorded itself is not a guess.
    cur.execute(
        # `artifact_lineage_edges` keys on *_artifact_id. The object-relationship
        # table is the one with *_object_id, and mixing the two here made the
        # circularity check raise UndefinedColumn — which the caller swallowed
        # as "no recorded lineage", quietly turning the strongest signal this
        # check has into a silent no.
        "SELECT 1 FROM artifact_lineage_edges "
        "WHERE (source_artifact_id = %s AND target_artifact_id = %s) "
        "   OR (source_artifact_id = %s AND target_artifact_id = %s) LIMIT 1",
        (source_id, dataset["dataset_source_id"],
         dataset["dataset_source_id"], source_id))
    if cur.fetchone():
        return {"kind": "recorded_lineage", "confidence": 0.9,
                "detail": "this system has a recorded lineage edge between the "
                          "paper and the dataset",
                "evidence_refs": []}
    return None


# ---------------------------------------------------------------------------
# P9 — design
# ---------------------------------------------------------------------------

#: Which dataset designs can carry a claim made under a given design.
#:
#: The asymmetry is the point. A cross-sectional claim is testable on cohort
#: data — a weaker question can always be asked of stronger data. The reverse is
#: not true: a cohort claim asserts temporal ordering, and a cross-sectional
#: panel has none to offer.
_DESIGN_SUPPORTS: dict[str, set[str]] = {
    "cross_sectional": {"cross_sectional", "cohort", "longitudinal", "panel",
                        "case_control", "randomised_controlled_trial",
                        "randomized_controlled_trial", "experiment"},
    "observational": {"cross_sectional", "cohort", "longitudinal", "panel",
                      "observational", "ecological", "survey"},
    "ecological": {"ecological", "cross_sectional", "panel"},
    "cohort": {"cohort", "longitudinal", "panel"},
    "longitudinal": {"cohort", "longitudinal", "panel"},
    "case_control": {"case_control", "cohort"},
    "randomised_controlled_trial": {"randomised_controlled_trial",
                                    "randomized_controlled_trial", "experiment"},
    "randomized_controlled_trial": {"randomised_controlled_trial",
                                    "randomized_controlled_trial", "experiment"},
    "experiment": {"experiment", "randomised_controlled_trial",
                   "randomized_controlled_trial"},
}

#: Designs a *dataset* may be recorded under: exactly the values the support
#: table is willing to see on the data side, taken as the union of what it
#: allows rather than written out beside it. A second hand-kept list would
#: drift, and the drift would show up as a claim refused for a design the
#: table would in fact have accepted.
DATASET_DESIGNS: frozenset[str] = frozenset().union(*_DESIGN_SUPPORTS.values())


#: Words a paper wraps its design in. Stripped before matching, because a model
#: reading prose returns what the prose says — "a cross-sectional analysis",
#: "prospective cohort study" — and rejecting those as unrecognised would refuse
#: on a formatting difference while calling it a fact about the science.
_DESIGN_NOISE = re.compile(
    r"(?i)\b(a|an|the|analysis|analyses|study|studies|design|survey|data|"
    r"prospective|retrospective|based|of|was|were)\b")

#: Phrases that mean a recognised design under another name.
_DESIGN_ALIASES = {
    "crosssectional": "cross_sectional",
    "rct": "randomised_controlled_trial",
    "randomised_trial": "randomised_controlled_trial",
    "randomized_trial": "randomised_controlled_trial",
    "clinical_trial": "randomised_controlled_trial",
    "casecontrol": "case_control",
    "follow_up": "cohort",
    "repeated_measures": "longitudinal",
    "time_series": "longitudinal",
}


def normalise_design(text: str | None) -> str:
    """
    Reduce a paper's own words to a design this system recognises.

    Deterministic and lossy on purpose: it drops filler, never guesses. Anything
    that does not reduce to a known design comes back unchanged so the caller
    reports it as unrecognised rather than silently choosing a neighbour — a
    wrong design here would license a causal reading the data cannot support.
    """
    if not text:
        return "unknown"
    cleaned = _DESIGN_NOISE.sub(" ", text.strip().lower())
    cleaned = re.sub(r"[^a-z]+", "_", cleaned).strip("_")
    if not cleaned:
        return "unknown"
    if cleaned in _DESIGN_SUPPORTS:
        return cleaned
    if cleaned in _DESIGN_ALIASES:
        return _DESIGN_ALIASES[cleaned]
    # "cohort_panel", "cross_sectional_ecological" — take the first recognised
    # design named, since a paper listing two is describing the stronger claim
    # under the weaker structure.
    for known in ("randomised_controlled_trial", "randomized_controlled_trial",
                  "case_control", "cross_sectional", "longitudinal", "cohort",
                  "ecological", "experiment", "observational"):
        if known in cleaned:
            return known
    return cleaned


#: Words a paper wraps its design in. Stripped before matching, because a model
#: reading prose returns what the prose says — "a cross-sectional analysis",
#: "prospective cohort study" — and rejecting those as unrecognised would refuse
#: on a formatting difference while calling it a fact about the science.
_DESIGN_NOISE = re.compile(
    r"(?i)\b(a|an|the|analysis|analyses|study|studies|design|survey|data|"
    r"prospective|retrospective|based|of|was|were)\b")

#: Phrases that mean a recognised design under another name.
_DESIGN_ALIASES = {
    "crosssectional": "cross_sectional",
    "rct": "randomised_controlled_trial",
    "randomised_trial": "randomised_controlled_trial",
    "randomized_trial": "randomised_controlled_trial",
    "clinical_trial": "randomised_controlled_trial",
    "casecontrol": "case_control",
    "follow_up": "cohort",
    "repeated_measures": "longitudinal",
    "time_series": "longitudinal",
}


# ---------------------------------------------------------------------------
# P5 — power. The check that decides whether a null means anything.
# ---------------------------------------------------------------------------

#: The effect size this system powers against when the paper reports no
#: magnitude of its own — Cohen's conventional "medium" correlation. Stated
#: rather than hidden, because it is a choice: with no number from the paper,
#: something has to stand in, and a reader is entitled to know what and to
#: disagree with it.
DEFAULT_BENCHMARK = 0.3


def minimum_detectable_r(n: int | None, *, alpha: float = 0.05,
                         power: float = 0.8) -> float | None:
    """
    The smallest correlation this sample could have detected.

    Fisher's z transformation, two-sided. Returns None below n=4, where the
    quantity is undefined rather than merely large — returning a number there
    would invite exactly the false precision this check exists to prevent.
    """
    if n is None or n < 4:
        return None
    from scipy import stats

    z_alpha = float(stats.norm.ppf(1 - alpha / 2))
    z_beta = float(stats.norm.ppf(power))
    return float(math.tanh((z_alpha + z_beta) / math.sqrt(n - 3)))


_EFFECT_NUMBER = re.compile(
    r"(?i)(?:\br\b|rho|beta|β|\bd\b|\bOR\b|\bRR\b|\bHR\b)\s*[=:]\s*(-?\d*\.?\d+)"
    r"|(-?\d*\.?\d+)")


def parse_claimed_effect(text: str | None) -> float | None:
    """
    Read a magnitude out of the paper's own words, deterministically.

    A regex rather than a model, because this number decides whether a null is
    reported as evidence of absence. A parse that can be checked by eye against
    the quoted string is worth more here than one that is usually cleverer.
    """
    if not text:
        return None
    match = _EFFECT_NUMBER.search(text)
    if not match:
        return None
    try:
        return abs(float(match.group(1) or match.group(2)))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Testability — steps 1 through 4
# ---------------------------------------------------------------------------

def assess_testability(cur, *, project_id: str, claim: dict[str, Any],
                       dataset_version_id: str, source_id: str | None = None
                       ) -> dict[str, Any]:
    """
    Can this claim be tested on this data at all?

    Returns either a `Verdict` refusing (P7–P11, D14) or the resolved columns
    needed to adjudicate. Deterministic throughout — no model is consulted, so
    this boundary does not evaporate on an installation without one.
    """
    cur.execute(
        "SELECT dv.study_design, dv.row_count, dv.population, dv.period_start, "
        "       dv.period_end, d.project_id, s.title "
        "FROM dataset_versions dv JOIN datasets d ON d.id = dv.dataset_id "
        "JOIN sources s ON s.id = d.source_id WHERE dv.id = %s",
        (dataset_version_id,))
    dataset = cur.fetchone()
    if not dataset:
        raise ClaimTestError(f"No such dataset version: {dataset_version_id}")
    if dataset["project_id"] != project_id:
        raise ClaimTestError("That dataset belongs to a different project.")

    context: dict[str, Any] = {
        "project_id": project_id,
        "dataset": {"id": dataset_version_id, "name": dataset["title"],
                    "design": dataset["study_design"],
                    "rows": dataset["row_count"],
                    "population": dataset["population"]},
        "unchecked": [],
    }

    def refuse(verdict: Verdict) -> dict[str, Any]:
        return {**context, "testable": False, "verdict": verdict}

    # --- P7: circularity, before anything else ------------------------------
    if source_id:
        # A caller-supplied identifier is not authorisation. Without this check,
        # the circularity scan could read passages and lineage from another
        # project before the claim was adjudicated.
        cur.execute(
            "SELECT 1 FROM sources WHERE id = %s AND project_id = %s",
            (source_id, project_id))
        if not cur.fetchone():
            raise ClaimTestError("No such source in this project.")
        circular = check_circularity(
            cur, source_id=source_id, dataset_version_id=dataset_version_id)
        if circular:
            return refuse(Verdict(
                outcome_code="P7", reason_code=f"circular_{circular['kind']}",
                confidence=circular["confidence"],
                evidence_refs=circular["evidence_refs"],
                facts={"evidence": circular["detail"]},
                state=RunState.REFUSED_BY_POLICY,
                remedies=["Test this claim against data the paper did not use.",
                          "If the overlap is coincidental, record that and re-run."],
                still_possible=[
                    "Read the paper's analysis alongside this data as a "
                    "description, without calling the agreement a replication."],
                caveats=["Detected from the paper's text and this system's "
                         "recorded provenance; confirm before relying on it."]))

    # --- P8: constructs, through the approved layer only --------------------
    #
    # String similarity is deliberately not used. A column named
    # `resistance_prevalence` is the claim's outcome only once a human says so;
    # matching automatically would let the system silently decide what a paper
    # meant, which is the silent alteration this rule forbids.
    cur.execute(
        "SELECT dc.id AS column_id, dc.name AS column_name, cv.name AS canonical, "
        "       cv.id AS canonical_id, "
        "       COALESCE(NULLIF(cv.display_label, ''), cv.name) AS label "
        "FROM variable_mappings vm "
        "JOIN dataset_columns dc ON dc.id = vm.dataset_column_id "
        "JOIN canonical_variables cv ON cv.id = vm.canonical_variable_id "
        "WHERE vm.project_id = %s AND vm.status = %s AND dc.dataset_version_id = %s",
        (project_id, harmonize.APPROVED, dataset_version_id))
    available = {row["canonical"]: dict(row) for row in cur.fetchall()}
    by_label = {row["label"].lower(): row for row in available.values()}

    by_canonical_id = {row["canonical_id"]: row for row in available.values()}

    def resolve(construct: str) -> dict[str, Any] | None:
        key = (construct or "").strip().lower()
        direct = available.get(key.replace(" ", "_")) or by_label.get(key)
        if direct:
            return direct
        # Then the project's approved vocabulary: a phrase a researcher has
        # already confirmed names this quantity. Approved routes only — there is
        # no fuzzy fallback, because being wrong here means answering a question
        # the paper never asked.
        named = vocabulary.resolve(cur, project_id=project_id, phrase=construct)
        return by_canonical_id.get(named["id"]) if named else None

    exposure = resolve(claim.get("exposure", ""))
    outcome_column = resolve(claim.get("outcome", ""))
    missing = [name for name, resolved in
               ((claim.get("exposure", ""), exposure),
                (claim.get("outcome", ""), outcome_column)) if resolved is None]

    if missing:
        # Offer the near misses. This is the one place similarity is used, and
        # it asks rather than answers: the phrasing is a question to the
        # researcher, and the verdict stands as *not testable* until they say
        # otherwise.
        remedies = []
        for term in missing:
            near = vocabulary.candidates(cur, project_id=project_id, phrase=term)
            if near:
                remedies.append(
                    f"Does {term!r} mean "
                    + " or ".join(repr(c["display_label"] or c["name"])
                                  for c in near)
                    + "? Confirm it and this claim becomes testable.")
            else:
                remedies.append(
                    f"If a column here measures {term!r}, map it to that "
                    "canonical variable and approve the mapping.")

        return refuse(Verdict(
            outcome_code="P8", reason_code="construct_not_mapped", confidence=0.95,
            facts={"missing": " and ".join(repr(m) for m in missing)},
            remedies=remedies,
            still_possible=[
                "Harmonise this dataset's columns — the claim becomes testable the "
                "moment the constructs are confirmed present.",
                "Look for a dataset that already measures what the claim is about."]))

    # --- P9: design ---------------------------------------------------------
    claimed_design = normalise_design(claim.get("claimed_design"))
    dataset_design = normalise_design(dataset["study_design"])

    if claimed_design == "unknown" or dataset_design == "unknown":
        # Undetermined, not "design mismatch": nothing was established either
        # way, and refusing on a mismatch nobody found would be a lie about the
        # data as well as about the paper.
        unstated = ("The paper does not state its study design"
                    if claimed_design == "unknown"
                    else f"{dataset['title']}'s study design is not recorded")
        return refuse(Verdict(
            outcome_code="D14", reason_code="design_unstated", confidence=0.9,
            facts={"missing": "the study design", "left": "the claim",
                   "right": dataset["title"]},
            caveats=[f"{unstated}, so whether this data could carry the claim was "
                     "not established either way."],
            remedies=["Record the study design and this check will run."],
            still_possible=["Record the design on whichever side is missing it; "
                            "nothing else about this pair is blocking."]))

    allowed = _DESIGN_SUPPORTS.get(claimed_design)
    if allowed is None:
        return refuse(Verdict(
            outcome_code="D14", reason_code="design_unrecognised", confidence=0.8,
            facts={"missing": f"a recognised design (the paper says "
                              f"{claimed_design!r})",
                   "left": "the claim", "right": dataset["title"]},
            remedies=["Record the paper's design as one of: "
                      + ", ".join(sorted(_DESIGN_SUPPORTS))]))

    if dataset_design not in allowed:
        return refuse(Verdict(
            outcome_code="P9", reason_code="design_cannot_carry_claim",
            confidence=0.95,
            facts={"claimed_design": claimed_design.replace("_", " "),
                   "dataset_design": dataset_design.replace("_", " ")},
            remedies=["Test this claim on data collected under a design that can "
                      "carry it.",
                      "Or restate the claim at the level this data supports — the "
                      "association is testable here even though the ordering is "
                      "not."],
            still_possible=[
                "Test the weaker, associational form of the same claim on this "
                "data, reported as an association and not as the paper's claim."]))

    # --- P10 / P11: scope, only where both sides record it ------------------
    #
    # An unrecorded scope is reported as *unchecked*, never as passed. "We did
    # not look" and "we looked and it was fine" are different statements, and an
    # interface that merges them is lying by omission about its own diligence.
    paper_population = (claim.get("population") or "").strip()
    dataset_population = (dataset["population"] or "").strip()
    if paper_population and dataset_population:
        if not _scopes_overlap(paper_population, dataset_population):
            return refuse(Verdict(
                outcome_code="P10", reason_code="population_out_of_scope",
                confidence=0.7,
                facts={"paper_population": paper_population,
                       "dataset_population": dataset_population},
                remedies=["Find data observing the population the paper studied.",
                          "Or restate the claim as being about "
                          f"{dataset_population} — which is a different claim."],
                still_possible=["Test the same relationship in "
                                f"{dataset_population} as a separate question."],
                caveats=["Populations were compared as recorded text; confirm the "
                         "descriptions really do denote different groups."]))
    else:
        # Every side that lacks it, and both when both do. The message used to
        # be `"the paper." if not paper_population else "this dataset."`, so
        # with neither recorded it named the paper alone — and a researcher who
        # went and added a population to the paper would find the check still
        # unmade. Naming one side of two is a true-sounding sentence that sends
        # somebody to fix half a problem.
        if not paper_population and not dataset_population:
            where = "neither the paper nor this dataset."
        elif not paper_population:
            where = "the paper."
        else:
            where = "this dataset."
        context["unchecked"].append(
            f"Population scope was not checked — it is not recorded on {where}")

    if not (dataset["period_start"] and dataset["period_end"]):
        context["unchecked"].append(
            "Temporal scope was not checked — this dataset's collection period is "
            "not recorded.")

    return {
        **context, "testable": True, "verdict": None,
        "exposure_column": exposure["column_name"],
        "outcome_column": outcome_column["column_name"],
        "evidence_refs": [exposure["column_id"], outcome_column["column_id"]],
    }


def _scopes_overlap(left: str, right: str) -> bool:
    """
    Do two recorded population descriptions plausibly denote the same group?

    Deliberately permissive. This check *refuses* a comparison, and a false
    refusal costs a researcher a real analysis, so it fires only on recognised,
    mutually exclusive groupings — the children-versus-adults case the taxonomy
    names — and abstains on everything else.
    """
    exclusive = [
        ({"child", "children", "paediatric", "pediatric", "infant", "infants",
          "neonate", "neonates"}, {"adult", "adults"}),
        ({"male", "males", "men"}, {"female", "females", "women"}),
        ({"human", "humans", "patients"},
         {"mice", "mouse", "rat", "rats", "murine"}),
    ]
    left_tokens = set(re.split(r"[^a-z]+", left.lower()))
    right_tokens = set(re.split(r"[^a-z]+", right.lower()))
    for a, b in exclusive:
        if ((left_tokens & a and right_tokens & b and not left_tokens & b)
                or (left_tokens & b and right_tokens & a and not left_tokens & a)):
            return False
    return True


# ---------------------------------------------------------------------------
# Adjudication — step 5
# ---------------------------------------------------------------------------

def test_claim(cur, *, project_id: str, claim: dict[str, Any],
               dataset_version_id: str, source_id: str | None = None
               ) -> dict[str, Any]:
    """
    Adjudicate one claim against one dataset, returning a typed verdict.

    Refuses before it computes, and never runs its own hypothesis test: one that
    did would sit outside the discovery run's correction family and quietly
    inflate the false-discovery rate. It reads a result already computed
    under correction, or reports that the pair has not been tested.
    """
    claim = {**claim, "source_id": claim.get("source_id") or source_id}
    testability = assess_testability(
        cur, project_id=project_id, claim=claim,
        dataset_version_id=dataset_version_id, source_id=source_id)
    if not testability["testable"]:
        return _result(cur, testability, claim, testability["verdict"])

    exposure = testability["exposure_column"]
    outcome_name = testability["outcome_column"]
    cur.execute(
        "SELECT c.id, c.estimate, c.q_value, c.lifecycle_status, c.sample_size, "
        "       c.method, dr.false_discovery_rate "
        "FROM connections c "
        "LEFT JOIN discovery_runs dr ON dr.id = c.discovery_run_id "
        "WHERE c.project_id = %s "
        "AND ((c.left_variable = %s AND c.right_variable = %s) "
        "  OR (c.left_variable = %s AND c.right_variable = %s)) "
        "ORDER BY c.created_at DESC LIMIT 1",
        (project_id, exposure, outcome_name, outcome_name, exposure))
    connection = cur.fetchone()

    if not connection:
        return _result(cur, testability, claim, Verdict(
            outcome_code="D14", reason_code="not_yet_analysed", confidence=0.99,
            facts={"missing": f"a computed result for {exposure} × {outcome_name}",
                   "left": "the claim", "right": testability["dataset"]["name"]},
            evidence_refs=testability["evidence_refs"],
            state=RunState.NEEDS_INPUT,
            remedies=["Run discovery on this dataset. The claim is testable here; "
                      "the pair simply has not been tested yet."],
            still_possible=["Run discovery — every blocker to testing this claim "
                            "is already cleared."]))

    estimate = connection["estimate"] or 0.0
    q_value = connection["q_value"]
    n = connection["sample_size"] or testability["dataset"]["rows"] or 0
    observed = "positive" if estimate > 0 else "negative" if estimate < 0 else "none"
    claimed = (claim.get("direction") or "unclear").strip().lower()
    # The quoted statement is the fallback, and a good one: it is the paper's
    # own words, recorded verbatim, so a number read out of it can be checked by
    # eye against the quotation. A model that forgets to fill the effect field
    # must not silently cost the researcher the power check.
    claimed_effect_text = claim.get("claimed_effect") or ""
    claimed_effect = parse_claimed_effect(claimed_effect_text)
    if claimed_effect is None:
        claimed_effect = parse_claimed_effect(claim.get("statement"))
        if claimed_effect is not None:
            claimed_effect_text = f"{claimed_effect} (read from the quoted claim)"
    significant = discovery.survived_correction(q_value, connection["false_discovery_rate"])

    refs = [*testability["evidence_refs"], connection["id"]]
    caveats = list(testability["unchecked"])

    # --- Null results: P5 is asked before P4 --------------------------------
    if not significant:
        mde = minimum_detectable_r(n)
        benchmark = claimed_effect if claimed_effect is not None else DEFAULT_BENCHMARK

        if mde is None or mde > benchmark:
            return _result(cur, testability, claim, Verdict(
                outcome_code="P5", reason_code="insufficient_power", confidence=0.9,
                evidence_refs=refs,
                facts={"n": f"{n:,}",
                       "mde": (f"{mde:.2f}" if mde is not None
                               else "not computable at this sample size"),
                       "claimed": (claimed_effect_text
                                   or f"an effect of at least {benchmark}")},
                caveats=caveats + ([
                    "The paper reports no magnitude, so this was powered against a "
                    f"correlation of {DEFAULT_BENCHMARK} — a conventional "
                    "benchmark, not the paper's number."]
                    if claimed_effect is None else []),
                remedies=["Test this claim on a larger sample.",
                          "Or report the interval this data does support, which is "
                          "a weaker but honest statement."],
                still_possible=["Report what this data can rule out, rather than "
                                "what it failed to find."]))

        return _result(cur, testability, claim, Verdict(
            outcome_code="P4", reason_code="null_and_adequately_powered",
            confidence=0.85, evidence_refs=refs, facts={},
            caveats=caveats + [
                f"This sample could have detected a correlation of {mde:.2f} or "
                "larger, which is what makes the null informative here."],
            still_possible=["Report the null as a result — it is one."]))

    # --- Significant results ------------------------------------------------
    if claimed in ("unclear", "none"):
        return _result(cur, testability, claim, Verdict(
            outcome_code="P17", reason_code="direction_unstated_by_paper",
            confidence=0.75, evidence_refs=refs, facts={},
            caveats=caveats + ["The paper states no direction, so only the "
                               "existence of a relationship is corroborated."]))

    if claimed != observed:
        return _result(cur, testability, claim, Verdict(
            outcome_code="P6", reason_code="opposite_sign", confidence=0.9,
            evidence_refs=refs, facts={"observed": observed, "claimed": claimed},
            caveats=caveats,
            still_possible=["Look for a confounder that could reverse the sign — an "
                            "opposite result is often Simpson's paradox rather than "
                            "a contradiction."]))

    # Direction agrees. Magnitude separates P1 from P2, and can only be compared
    # when the paper reported one.
    if claimed_effect is None:
        return _result(cur, testability, claim, Verdict(
            outcome_code="P17", reason_code="paper_reports_no_variance",
            confidence=0.8, evidence_refs=refs, facts={},
            caveats=caveats + ["The paper reports no effect size, so the two "
                               "magnitudes cannot be placed on the same scale."]))

    # A 20% relative difference is the line between "the same size" and "the same
    # direction, a different size". Stated, because a threshold nobody can see is
    # a hidden analytical choice.
    ratio = abs(abs(estimate) - claimed_effect) / max(claimed_effect, 1e-9)
    code, reason = (("P1", "direction_and_magnitude_agree") if ratio <= 0.2
                    else ("P2", "direction_agrees_magnitude_differs"))

    return _result(cur, testability, claim, Verdict(
        outcome_code=code, reason_code=reason, confidence=0.85, evidence_refs=refs,
        facts={},
        caveats=caveats + [
            "Agreement with one dataset is corroboration, not replication, "
            "under "
            + causal.describe(testability["dataset"]["design"])["description"] + ".",
            f"Magnitudes were called {'the same' if code == 'P1' else 'different'} "
            f"at a 20% relative difference: this data gives {abs(estimate):.3f} and "
            f"the paper reports {claimed_effect_text or claimed_effect}."]))


def _record_in_graph(cur, *, project_id: str, claim: dict[str, Any],
                     dataset_version_id: str, verdict: Verdict) -> None:
    """
    Put the claim test into the research graph.

    Without this the graph has papers on one side and analyses on the other and
    nothing between them — a path query from a paper to the finding that tested
    its claim returns "no connection", which is true of the record and false of
    the research. The whole point of the product is the throughline, and the
    throughline has to be an edge.

    Recorded for refusals too. "This paper's claim could not be tested on this
    data, and here is why" is a result about both objects, and losing it would
    mean the same dead end gets rediscovered every time someone tries.
    """
    from throughline_schemas.enums import LineageType

    from .lineage import add_edge

    source_id = claim.get("source_id")
    if not source_id:
        return

    cur.execute(
        "SELECT o.id FROM research_objects o WHERE o.project_id = %s "
        "  AND o.source_id = %s AND o.object_type = 'paper' LIMIT 1",
        (project_id, source_id))
    paper = cur.fetchone()

    cur.execute(
        "SELECT d.object_id FROM dataset_versions dv "
        "JOIN datasets d ON d.id = dv.dataset_id WHERE dv.id = %s",
        (dataset_version_id,))
    dataset = cur.fetchone()

    if not (paper and dataset and dataset["object_id"]):
        return

    add_edge(
        cur, project_id=project_id,
        source_artifact_id=dataset["object_id"],
        target_artifact_id=paper["id"],
        # `references` rather than `supports`: the edge records that the two
        # were tested against each other, and a P9 refusal is as much a part of
        # the record as a P1 agreement.
        lineage_type=LineageType.REFERENCES,
        metadata={"claim_test": verdict.outcome_code,
                  "family": verdict.family.value,
                  "reason": verdict.reason_code,
                  "statement": (claim.get("statement") or "")[:500]},
    )

    _record_evidence(cur, project_id=project_id, claim=claim,
                     paper_object_id=paper["id"],
                     dataset_object_id=dataset["object_id"], verdict=verdict)


#: What a verdict family says about the claim, in the vocabulary `evidence` and
#: `finding_claims` already use. Taken from the taxonomy rather than invented,
#: so a family added later fails loudly here instead of being silently scored.
_DIRECTION = {
    Family.SUPPORTED: "supports",
    Family.CONTRADICTED: "contradicts",
    # Ran, with named threats. Not clean support, and calling it support would
    # let a qualified result be counted as a confirmation.
    Family.QUALIFIED: "mixed",
    # Ran, but the answer is unstable. Neutral is the honest score.
    Family.UNDETERMINED: "neutral",
}

#: The relationship the outcome asserts between the dataset and the paper.
_RELATIONSHIP = {
    Family.SUPPORTED: "supports",
    Family.CONTRADICTED: "contradicts",
    Family.QUALIFIED: "qualifies",
    Family.UNDETERMINED: "inconclusive",
}


def _record_evidence(cur, *, project_id: str, claim: dict[str, Any],
                     paper_object_id: str, dataset_object_id: str,
                     verdict: Verdict) -> None:
    """
    Record the outcome as evidence about the claim, and as an asserted edge.

    Two tables, both of which the system read and never wrote.

    `evidence` (D014) is what `findings.evidence_summary` counts by direction.
    It returned zeros for every finding in every project, which reads as "no
    evidence bears on this" — where the truth was that nothing was ever
    recorded. `research_edges` (D013) is the asserted half of the knowledge
    graph, the half `graphs.neighbourhood` documents as one of "two kinds of
    edge, one graph"; nothing had ever written a row, so every graph ever drawn
    showed derivation only.

    **A refusal is not evidence.** A claim that could not be tested taught
    nothing about whether it is true — no statistic was read. Scoring that as
    neutral evidence would put a number in `evidence_summary` for a test that
    never happened, which is the same defect as the Contradictions meter one
    table over: a count that reads as knowledge and is not. The lineage edge
    above *is* still written for those, deliberately — "this pairing was tried
    and could not be tested" is worth keeping so the dead end is not
    rediscovered — but it is a fact about the pairing, not about the claim.

    **The family alone cannot decide that, and assuming it could was a bug
    here.** `D14 not_yet_analysed` is `Family.UNDETERMINED`, the same family as
    a genuinely unstable result, so scoring on family recorded neutral evidence
    for a pair that had never been analysed at all. `RunState` is what separates
    them, and the taxonomy says so in as many words: it is "deliberately *not* a
    `Family`", because one describes whether the job ran and the other what the
    science said. Both have to agree before anything is written.
    """
    direction = _DIRECTION.get(verdict.family)
    if direction is None:
        return
    if verdict.state is not RunState.COMPLETE:
        return

    claim_id = claim.get("claim_id")
    if not claim_id:
        # `evidence.claim_id` is NOT NULL and pointing it at a Claim that does
        # not exist is not an option. Inventing one here would also mean a
        # hand-typed claim silently created a permanent record the caller never
        # asked for.
        return

    cur.execute("SELECT id FROM claims WHERE id = %s AND project_id = %s",
                (claim_id, project_id))
    if not cur.fetchone():
        return

    evidence_id = new_id("evd")
    cur.execute(
        "INSERT INTO evidence(id, project_id, claim_id, source_object_id, "
        "evidence_type, location, direction, strength, confidence, metadata) "
        "VALUES (%s, %s, %s, %s, 'analysis_result', %s, %s, %s, %s, %s)",
        (evidence_id, project_id, claim_id, dataset_object_id,
         jsonb({"locator": claim.get("locator") or ""}),
         direction, None, verdict.confidence,
         jsonb({"outcome": verdict.outcome_code, "reason": verdict.reason_code,
                "family": verdict.family.value})))

    # `strength` is left null on purpose. The column means how strongly the
    # evidence bears on the claim, and this system has no principled scale for
    # that — `verdict.confidence` is confidence in the *adjudication*, which is
    # a different quantity. Writing one in place of the other would put an
    # invented number where a scientific one is expected.

    cur.execute(
        "INSERT INTO research_edges(id, project_id, source_object_id, "
        "target_object_id, relationship_type, confidence, status, evidence_id, "
        "metadata) VALUES (%s, %s, %s, %s, %s, %s, 'asserted', %s, %s) "
        # The unique key is (source, target, type), so re-testing the same claim
        # updates the edge rather than accumulating one per run. The evidence
        # row is not deduplicated with it: each test is a separate observation,
        # and the edge points at the most recent one.
        "ON CONFLICT (source_object_id, target_object_id, relationship_type) "
        "DO UPDATE SET confidence = EXCLUDED.confidence, "
        "              evidence_id = EXCLUDED.evidence_id, "
        "              metadata = EXCLUDED.metadata, status = 'asserted'",
        (new_id("redg"), project_id, dataset_object_id, paper_object_id,
         _RELATIONSHIP[verdict.family], verdict.confidence, evidence_id,
         jsonb({"outcome": verdict.outcome_code,
                "statement": (claim.get("statement") or "")[:500]})))


def _result(cur, testability: dict[str, Any], claim: dict[str, Any],
            verdict: Verdict) -> dict[str, Any]:
    """
    Assemble the response, with the verdict as the single source of state.

    Also the one place the graph edge is written, so no return path above can
    forget it — and there are eleven of them.
    """
    _record_in_graph(cur, project_id=testability.get("project_id", ""),
                     claim=claim,
                     dataset_version_id=testability["dataset"]["id"],
                     verdict=verdict)
    unchecked = testability.get("unchecked", [])
    body = verdict.to_dict()
    for note in unchecked:
        if note not in body["caveats"]:
            body["caveats"].append(note)
    return {
        "verdict": body,
        "claim": claim,
        "dataset": testability["dataset"],
        "testable": testability["testable"],
        "exposure_column": testability.get("exposure_column"),
        "outcome_column": testability.get("outcome_column"),
        # Never checked is distinct from checked and passed.
        "unchecked": unchecked,
    }


# ---------------------------------------------------------------------------
# Step 0 — locating claims, the only part that needs a model
# ---------------------------------------------------------------------------

def claims_for(cur, *, project_id: str, source_id: str,
               limit: int = 40) -> dict[str, Any]:
    """
    The claims recorded for a paper, reading it only if none are.

    **Reconciling two papers used to re-read both of them, every time.** That
    is two model calls per comparison, and — the part that matters — two
    readings of one paper can disagree, so asking the same question twice could
    return different verdicts with nothing on screen to say the inputs had
    changed. `stored_claims` says as much itself: when readings disagree, the
    disagreement has to be attributable rather than argued about.

    Re-reading stays available and stays deliberate: it is what
    `locate_claims` is, and the claim-test screen offers it as its own act.
    Comparing two papers is not a request to re-read them.
    """
    cur.execute("SELECT project_id, title FROM sources WHERE id = %s", (source_id,))
    source = cur.fetchone()
    if not source:
        raise ClaimTestError(f"No such source: {source_id}")
    if source["project_id"] != project_id:
        raise ClaimTestError("That source belongs to a different project.")

    recorded = stored_claims(cur, source_id)
    if not recorded:
        return {**locate_claims(cur, project_id=project_id, source_id=source_id,
                                limit=limit),
                "read_now": True}

    first = recorded[0]
    return {
        "source_id": source_id,
        "source_title": source["title"],
        "claims": recorded,
        "verdict": None,
        "note": "",
        # Whose reading this is. Carried so a caller can say which model
        # produced the claims it is comparing, rather than implying they came
        # from whatever model is configured today.
        "model": first.get("model") or "",
        "prompt": (f"{first.get('prompt_name')} v{first.get('prompt_version')}"
                   if first.get("prompt_name") else ""),
        "read_now": False,
    }


def locate_claims(cur, *, project_id: str, source_id: str,
                  limit: int = 40) -> dict[str, Any]:
    """
    Read a paper and record the empirical claims a dataset could test (P13).

    Passages are handed to the model as untrusted data: a paper is a
    document, and a sentence inside it addressed to an AI is content to report
    on, never an instruction to follow.
    """
    from throughline_model import ModelUnavailable, prompt, provider
    from throughline_model.schemas import TestableClaims

    cur.execute("SELECT project_id, title FROM sources WHERE id = %s", (source_id,))
    source = cur.fetchone()
    if not source:
        raise ClaimTestError(f"No such source: {source_id}")
    if source["project_id"] != project_id:
        raise ClaimTestError("That source belongs to a different project.")

    cur.execute(
        "SELECT content, locator FROM passages WHERE source_id = %s "
        "ORDER BY ordinal LIMIT %s", (source_id, limit))
    passages = list(cur.fetchall())
    if not passages:
        raise ClaimTestError(
            f"{source['title']!r} has no indexed passages. It may still be "
            "ingesting, or it may not be a document with readable text.")

    template = prompt("locate_claims")
    try:
        located, completion = provider().generate_structured(
            schema=TestableClaims,
            instructions=template.render(),
            untrusted_context="\n\n".join(
                f"[{p['locator']}] {p['content']}" for p in passages),
            prompt_name=template.name, prompt_version=template.version,
        )
    except ModelUnavailable as exc:
        raise ClaimTestError(
            f"{exc} Locating claims is the one step of the claim test that needs a "
            "model; a claim already recorded can still be adjudicated without one."
        ) from exc

    # A re-read replaces the previous reading rather than sitting beside it.
    #
    # Two extractions of one paper kept side by side would silently double every
    # downstream comparison: each claim reconciled against the other paper
    # twice, and a reviewer counting agreements counting each one two times.
    # `test_a_re_read_replaces_the_previous_reading` has asserted this invariant
    # since the table was added, and simulated the delete itself with the
    # comment "the real function deletes first" — which it did not.
    cur.execute("DELETE FROM located_claims WHERE source_id = %s", (source_id,))

    recorded: list[dict[str, Any]] = []
    for ordinal, found in enumerate(located.claims):
        # A Claim in the  sense — a literature interpretation, kept distinct
        # from a calculated result so the two can never be merged.
        claim_id = new_id("clm")
        cur.execute(
            "INSERT INTO claims(id, project_id, statement, claim_type, status, "
            "confidence, created_by) "
            "VALUES (%s, %s, %s, 'literature_interpretation', 'proposed', %s, %s)",
            (claim_id, project_id, found.statement, found.choice_confidence,
             completion.model))

        # The located claim itself, which is not the same record as the Claim.
        #
        # `claims` holds the statement; everything about *how it was read* —
        # the constructs named, the design reported, the locator, and which
        # model at which prompt version produced them — lives here. All of it
        # was computed on every extraction and then dropped, so `stored_claims`
        # returned an empty list for every paper ever read, and its docstring's
        # promise that a disagreement between two extractions is attributable
        # rather than arguable had nothing behind it (D015).
        cur.execute(
            "INSERT INTO located_claims(id, project_id, source_id, claim_id, "
            "statement, exposure, outcome, direction, claimed_design, "
            "claimed_effect, claimed_interval, estimand, outcome_definition, "
            "population, period, locator, choice_confidence, model, "
            "prompt_name, prompt_version, ordinal) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s, %s, %s, %s)",
            (new_id("lclm"), project_id, source_id, claim_id,
             found.statement, found.exposure, found.outcome, found.direction,
             found.claimed_design, found.claimed_effect, found.claimed_interval,
             found.estimand, found.outcome_definition, found.population,
             found.period, found.locator, found.choice_confidence,
             completion.model, completion.prompt_name,
             completion.prompt_version, ordinal))

        recorded.append({
            "claim_id": claim_id, "statement": found.statement,
            "exposure": found.exposure, "outcome": found.outcome,
            "direction": found.direction, "claimed_design": found.claimed_design,
            "claimed_effect": found.claimed_effect, "population": found.population,
            "locator": found.locator, "source_id": source_id,
            "choice_confidence": found.choice_confidence,
        })

    header = {
        "source_id": source_id, "source_title": source["title"],
        "model": completion.model,
        "prompt": f"{completion.prompt_name} v{completion.prompt_version}",
    }
    if not recorded:
        # P13 is an outcome, not an empty response.
        return {**header, "claims": [], "note": located.note or
                "No testable empirical claim was found.",
                "verdict": Verdict(
                    outcome_code="P13", reason_code="no_quantitative_claim",
                    confidence=0.7, facts={"source": source["title"]},
                    method="model-assisted",
                    caveats=[f"Read by {completion.model}; a claim stated in an "
                             "unusual form may have been missed."],
                    remedies=["Record the claim by hand if the paper makes one."],
                ).to_dict()}
    return {**header, "claims": recorded, "verdict": None, "note": located.note}


def stored_claims(cur, source_id: str) -> list[dict[str, Any]]:
    """
    The claims currently recorded for a paper, in the order they were read.

    Returned with the model and prompt version that produced them. Two
    extractions of the same paper can disagree — a different model, or the same
    model at a different prompt version, will locate different claims — and when
    they do, the disagreement has to be attributable rather than argued about.
    Without those fields the only available answer is "the system said so once
    and says otherwise now", which is not an answer.

    Ordered by `ordinal` so the sequence is the one the reader saw, not
    whatever the planner returns.
    """
    cur.execute(
        """
        SELECT id, project_id, source_id, claim_id, statement, exposure, outcome,
               direction, claimed_design, claimed_effect, claimed_interval,
               estimand, outcome_definition, population, period, locator,
               choice_confidence, model, prompt_name, prompt_version, ordinal,
               created_at
        FROM located_claims
        WHERE source_id = %s
        ORDER BY ordinal, created_at
        """,
        (source_id,))
    return [dict(row) for row in cur.fetchall()]


__all__ = [
    "ClaimTestError", "DEFAULT_BENCHMARK", "assess_testability",
    "check_circularity", "locate_claims", "minimum_detectable_r",
    "normalise_design", "parse_claimed_effect", "stored_claims",
    "test_claim",
]
