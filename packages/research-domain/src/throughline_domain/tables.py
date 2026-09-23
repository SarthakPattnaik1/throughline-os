"""
The project's results as a table a researcher can open (§75).

Every other export here is a *document*: Markdown, HTML, .docx, .pptx, a
figure. Those are for reading. A researcher also needs the numbers as data —
to put a results table in a paper, to re-plot in R, to hand a collaborator
something they can sort. §75 recorded that as unbuilt, and it is the last
export a working scientist reaches for that this product did not have.

**Rows are what was tested, not what survived.** A results table containing
only the significant connections is the shape of publication bias, and this
system spends most of its effort refusing exactly that: the family that was
tested is what makes a q-value mean anything, so every candidate is a row and
`lifecycle_status` says which ones stood.

**Nothing is recomputed.** Every number comes from the recorded connection,
which came from a recorded run — the same rule the figures and reports follow,
for the same reason. A CSV that quietly rounded, or re-derived a q-value from
the p-values in front of it, would be a second opinion wearing the first one's
name.

**Absent stays absent.** A missing p-value is an empty cell, never a zero and
never "N/A" — a reader can sort on an empty cell and cannot sort on a word, and
zero is a claim.
"""

from __future__ import annotations

import csv
import io
from typing import Any

#: The columns, in the order a reader wants them: what was compared, how, what
#: came out, and how much it was corrected for.
CONNECTION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("left_variable", "Variable A"),
    ("right_variable", "Variable B"),
    ("method", "Method"),
    ("estimate", "Estimate"),
    ("effect_size_name", "Effect size name"),
    ("effect_size", "Effect size"),
    ("sample_size", "n"),
    ("p_value", "p"),
    ("q_value", "q (corrected)"),
    # Which data this answer came from.
    #
    # Added on a false premise and kept on a true one. I read two rows for
    # `resistance_pct ~ gdp_per_capita` with opposite signs and reported it as
    # a contradiction the screen was hiding; the query behind that reading had
    # no project filter, and the two rows are in two different projects, each
    # with one dataset. A researcher would never see them together.
    #
    # The column stays because a project may hold several datasets — the
    # schema allows it and discovery runs per dataset version — and because a
    # results table that travels into a paper should name the data it came
    # from whether or not there is a second one to confuse it with. That is a
    # smaller claim than the one it was built on, and it is the true one.
    ("dataset_name", "Dataset"),
    ("evidence_quality", "Evidence quality"),
    ("lifecycle_status", "State"),
    ("id", "Connection id"),
    ("analysis_run_id", "Analysis run id"),
)


def _cell(value: Any) -> str:
    """One value, written the way a spreadsheet should receive it.

    Numeric database values stay numeric. Text is made literal when a spreadsheet
    would otherwise interpret it as a formula. Dataset names and variable labels
    come from uploaded files, so a dangerous formula prefix must never become
    executable merely because a researcher opens the exported CSV.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)

    text = str(value)
    first = text.lstrip(" \t\r\n")[:1]
    if first in {"=", "+", "-", "@"}:
        return "'" + text
    return text


def connections_csv(cur, project_id: str) -> str:
    """
    Every connection this project tested, as CSV.

    Ordered by q-value with the uncorrected last, so the strongest evidence is
    at the top and a reader scanning down meets the results in the order they
    would argue about them. Ties break on id, so two exports of an unchanged
    project are byte-identical — a table that reshuffles itself makes a diff
    unreadable and a reviewer suspicious, which is the same reason the
    bibliography is ordered.

    `NULLS LAST` is written out although PostgreSQL already does this for an
    ascending sort — checked, rather than assumed: NULLs sort last in ASC and
    first in DESC. It stays because the clause says what the order is *for*.
    A later `DESC`, or a different engine, would silently head the table with
    the rows that were never corrected — the least conclusive results in the
    most prominent position — and the test below pins that behaviour rather
    than this clause, so removing the words changes nothing and reversing the
    order fails.
    """
    cur.execute(
        """
        SELECT id, left_variable, right_variable, method, estimate,
               effect_size, effect_size_name, sample_size, p_value, q_value,
               evidence_quality, lifecycle_status, analysis_run_id,
               dataset_name
          FROM (
            SELECT c.*, ds.name AS dataset_name
              FROM connections c
              LEFT JOIN discovery_runs dr ON dr.id = c.discovery_run_id
              LEFT JOIN dataset_versions dv ON dv.id = dr.dataset_version_id
              LEFT JOIN datasets ds ON ds.id = dv.dataset_id
          ) AS c
         WHERE project_id = %s
         ORDER BY q_value NULLS LAST, p_value NULLS LAST, id
        """,
        (project_id,),
    )
    rows = cur.fetchall()

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([heading for _, heading in CONNECTION_COLUMNS])
    for row in rows:
        writer.writerow([_cell(row[field]) for field, _ in CONNECTION_COLUMNS])
    return out.getvalue()


def connections_count(cur, project_id: str) -> int:
    cur.execute("SELECT count(*) AS n FROM connections WHERE project_id = %s",
                (project_id,))
    return int(cur.fetchone()["n"])
