"""
The project's accumulating vocabulary — the part of the system that learns.

A paper says "antibiotic exposure". Another says "antibiotic consumption". The
column is `consumption_ddd`. All three name one quantity, and nothing in the
data can establish that — only a researcher can. This module records that
judgement once so it is reused, and that reuse is the honest version of a system
that "gets better the more you use it".

What improves is the **vocabulary**, not the statistics. Every approved alias
makes the next paper resolve without asking; the next claim test refuses one
fewer time for a reason that was never about the science. What does *not*
improve is any estimate, threshold or verdict rule — those are fixed, because a
system that quietly tuned its own inferential standards against its own history
would be fitting itself to the researcher's expectations, and would get more
confident precisely as it got less trustworthy.

So the gate is strict. A suggestion resolves nothing. Similarity proposes; a
human decides; the decision is dated and attributed. That is the only kind of
learning that survives an audit.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from .ids import new_id

SUGGESTED = "suggested"
APPROVED = "approved"
REJECTED = "rejected"


def normalise(phrase: str) -> str:
    """A phrase reduced to comparable form. Never used to decide, only to look."""
    return re.sub(r"[^a-z0-9]+", " ", (phrase or "").lower()).strip()


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def resolve(cur, *, project_id: str, phrase: str) -> dict[str, Any] | None:
    """
    Find the canonical variable a phrase names, through approved routes only.

    Three routes, all of them human-confirmed: the canonical name itself, its
    display label, and an approved alias. There is deliberately no fourth route
    — no fuzzy fallback, no "close enough" — because the cost of being wrong
    here is a claim test that answers a question the paper never asked.
    """
    key = normalise(phrase)
    if not key:
        return None

    cur.execute(
        "SELECT cv.id, cv.name, cv.display_label, 'canonical' AS via "
        "FROM canonical_variables cv "
        "WHERE cv.project_id = %s "
        "  AND (lower(replace(cv.name, '_', ' ')) = %s "
        "       OR lower(cv.display_label) = %s) "
        "UNION ALL "
        "SELECT cv.id, cv.name, cv.display_label, 'alias' AS via "
        "FROM variable_aliases va "
        "JOIN canonical_variables cv ON cv.id = va.canonical_variable_id "
        "WHERE va.project_id = %s AND va.status = %s "
        "  AND lower(replace(va.alias, '_', ' ')) = %s ",
        (project_id, key, key, project_id, APPROVED, key))
    rows = list(cur.fetchall())
    if not rows:
        return None

    # Every match, not the first. This was `LIMIT 1` with no order, so a phrase
    # naming two variables resolved to whichever row the planner produced —
    # and nothing prevents that: a display label comes from a file's own
    # description while a name comes from the proposal key, and `suggest`
    # never checks a phrase against canonical names. A phrase with two meanings
    # has none here. Both callers already treat "unresolved" conservatively:
    # the claim test stays not testable and asks, reconciliation reports
    # different constructs (T156).
    if len({r["id"] for r in rows}) > 1:
        return None

    # One variable, possibly by two routes. The canonical route wins, so an
    # alias is only counted as having saved a step when it was the only way in.
    row = next((r for r in rows if r["via"] == "canonical"), rows[0])

    if row["via"] == "alias":
        # Counted, because "how often has this vocabulary saved a step" is the
        # only honest measure of whether it is learning anything.
        cur.execute(
            "UPDATE variable_aliases SET times_used = times_used + 1 "
            "WHERE project_id = %s AND lower(replace(alias, '_', ' ')) = %s",
            (project_id, key))
    return dict(row)


# ---------------------------------------------------------------------------
# Suggestion — proposes, never decides
# ---------------------------------------------------------------------------

#: Below this, the phrases are not close enough to be worth a researcher's
#: attention. Above it they are *candidates*, never matches.
_SIMILARITY_FLOOR = 0.6


def candidates(cur, *, project_id: str, phrase: str, limit: int = 3
               ) -> list[dict[str, Any]]:
    """
    Canonical variables a phrase might name, ranked, for a human to choose from.

    This is the one place similarity is used, and it is used to *ask*, not to
    answer. The output is a question put to the researcher — "did the paper mean
    this?" — and the system proceeds as though the answer were no until they say
    otherwise.
    """
    key = normalise(phrase)
    if not key:
        return []

    cur.execute(
        "SELECT id, name, display_label, definition FROM canonical_variables "
        "WHERE project_id = %s", (project_id,))
    scored: list[dict[str, Any]] = []
    for row in cur.fetchall():
        for form in (row["name"], row["display_label"]):
            if not form:
                continue
            score = SequenceMatcher(None, key, normalise(form)).ratio()
            # A shared word is worth more than a shared spelling: "antibiotic
            # exposure" and "antibiotic consumption" overlap on the noun that
            # matters, and character similarity alone undersells that.
            shared = set(key.split()) & set(normalise(form).split())
            if shared:
                score = max(score, 0.5 + 0.5 * len(shared) / max(
                    len(set(key.split())), 1))
            if score >= _SIMILARITY_FLOOR:
                scored.append({**dict(row), "similarity": round(score, 3)})
                break

    scored.sort(key=lambda r: r["similarity"], reverse=True)
    return scored[:limit]


class AliasRefused(ValueError):
    """A proposal that would give a phrase two meanings."""


def suggest(cur, *, project_id: str, phrase: str, canonical_variable_id: str,
            origin: str = "paper", origin_ref: str | None = None,
            created_by: str = "system") -> dict[str, Any] | None:
    """
    Record that a phrase might name a canonical variable. Resolves nothing yet.

    Returns None if the phrase already has a ruling — an approved alias is not
    re-proposed, and a rejected one is not quietly resurrected by the next paper
    that happens to use the word.
    """
    key = normalise(phrase)

    # The ID is supplied by a caller and a foreign key alone proves only that
    # the variable exists, not that it belongs to this project. Without this
    # check an account could attach another project's canonical variable to its
    # own vocabulary and create a cross-project relationship (T185).
    cur.execute(
        "SELECT id FROM canonical_variables WHERE id = %s AND project_id = %s",
        (canonical_variable_id, project_id))
    if not cur.fetchone():
        raise AliasRefused("No such canonical variable in this project.")

    # A phrase that is already the name or label of a *different* variable is
    # refused, not queued. `resolve` refuses a phrase that names two variables
    # (T156), so approving this would silently break a lookup that works today
    # — and the proposal is the only moment a person is there to be told why.
    # Its own exception rather than `None`: the route turns `None` into "already
    # has a ruling ... a rejected term is not re-proposed", which would be false
    # here (T158).
    cur.execute(
        "SELECT id, name, display_label FROM canonical_variables "
        "WHERE project_id = %s AND id <> %s "
        "  AND (lower(replace(name, '_', ' ')) = %s OR lower(display_label) = %s) "
        "ORDER BY name LIMIT 1",
        (project_id, canonical_variable_id, key, key))
    taken = cur.fetchone()
    if taken:
        shown = taken["display_label"] or taken["name"]
        raise AliasRefused(
            f"{phrase.strip()!r} already names the variable {shown!r} "
            f"({taken['name']}) in this project, so it cannot also mean another "
            "one — a phrase with two meanings resolves to neither. Choose a more "
            "specific phrase, or map the column to that variable instead.")

    cur.execute(
        "SELECT id, status FROM variable_aliases "
        "WHERE project_id = %s AND lower(replace(alias, '_', ' ')) = %s",
        (project_id, key))
    if cur.fetchone():
        return None

    alias_id = new_id("valias")
    cur.execute(
        "INSERT INTO variable_aliases(id, project_id, canonical_variable_id, "
        "alias, origin, origin_ref, status, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (alias_id, project_id, canonical_variable_id, phrase.strip(), origin,
         origin_ref, SUGGESTED, created_by))
    return {"id": alias_id, "alias": phrase.strip(), "status": SUGGESTED}


def decide(cur, *, alias_id: str, status: str, decided_by: str) -> dict[str, Any]:
    """Approve or reject an alias. The moment the vocabulary actually changes."""
    if status not in (APPROVED, REJECTED):
        raise ValueError(f"An alias is approved or rejected, not {status!r}.")
    cur.execute(
        "UPDATE variable_aliases SET status = %s, decided_by = %s, "
        "decided_at = now() WHERE id = %s "
        "RETURNING id, alias, status, canonical_variable_id",
        (status, decided_by, alias_id))
    row = cur.fetchone()
    if not row:
        raise KeyError(f"No such alias: {alias_id}")
    return dict(row)


def pending(cur, project_id: str) -> list[dict[str, Any]]:
    """Every alias waiting on a person."""
    cur.execute(
        "SELECT va.id, va.alias, va.origin, va.origin_ref, va.created_at, "
        "       cv.name AS canonical_name, "
        "       COALESCE(NULLIF(cv.display_label, ''), cv.name) AS canonical_label "
        "FROM variable_aliases va "
        "JOIN canonical_variables cv ON cv.id = va.canonical_variable_id "
        "WHERE va.project_id = %s AND va.status = %s "
        "ORDER BY va.created_at", (project_id, SUGGESTED))
    return [dict(row) for row in cur.fetchall()]


def variables(cur, project_id: str) -> list[dict[str, Any]]:
    """
    The canonical variables an alias can point at.

    Nothing returned these. `labels()` and `equivalent_columns()` both project
    the *name* and drop the id, and `learned()` counts them — so a phrase could
    be decided but never proposed by hand, because a proposal has to name what
    the phrase means and no id ever left the server.
    """
    cur.execute(
        "SELECT id, name, COALESCE(NULLIF(display_label, ''), name) AS label, "
        "       definition, canonical_unit "
        "FROM canonical_variables WHERE project_id = %s ORDER BY label",
        (project_id,))
    return [dict(row) for row in cur.fetchall()]


def learned(cur, project_id: str) -> dict[str, Any]:
    """
    What this project's vocabulary has actually accumulated.

    Reported honestly, including the count of times it has saved a step, so the
    claim "it improves as you use it" can be checked rather than believed.
    """
    cur.execute(
        "SELECT status, count(*) AS n, COALESCE(sum(times_used), 0) AS uses "
        "FROM variable_aliases WHERE project_id = %s GROUP BY status",
        (project_id,))
    by_status = {row["status"]: dict(row) for row in cur.fetchall()}
    cur.execute(
        "SELECT count(*) AS n FROM canonical_variables WHERE project_id = %s",
        (project_id,))
    variables = cur.fetchone()["n"]

    approved = by_status.get(APPROVED, {"n": 0, "uses": 0})
    return {
        "canonical_variables": variables,
        "approved_aliases": approved["n"],
        "rejected_aliases": by_status.get(REJECTED, {"n": 0})["n"],
        "pending_aliases": by_status.get(SUGGESTED, {"n": 0})["n"],
        "times_an_alias_resolved_a_term": int(approved["uses"]),
        # Said plainly, because the alternative claim is the one this system
        # must never make.
        "note": ("This project's vocabulary grows as you confirm what terms mean. "
                 "Nothing else about the system learns: thresholds, corrections "
                 "and verdict rules are fixed, so a result today means exactly "
                 "what the same result meant on the first day."),
    }


__all__ = [
    "APPROVED", "REJECTED", "SUGGESTED", "candidates", "decide", "learned",
    "normalise", "pending", "resolve", "suggest", "variables",
]
