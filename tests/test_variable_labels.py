"""Reader-facing labels on figures — "zero raw column names outside mapping".

The canonical variable layer already stores a `display_label` and a unit for
exactly this purpose. These tests walk the real path — ingest a file, approve a
mapping, run an analysis, ask for a figure, render it — and assert that what
comes out the far end is what the project decided the variable is called, not
the normalised key the matcher uses.

The dataset here deliberately has one column of each kind: one with an approved
canonical mapping, one with a human header and no mapping, one with a mapping
that requires a transformation, and one with nothing better than its own name.
A single well-labelled column proves very little; the fallbacks are where a
label layer usually goes wrong.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from throughline_domain import analysis, objects, storage, visuals, workflow
from throughline_domain.db import connection
from throughline_domain.ids import new_id
from throughline_schemas.enums import SourceType
from throughline_visual import labels as labels_module
from throughline_visual.renderers import web
from throughline_visual.spec import VisualData, VisualType
from throughline_workers.runner import Worker

#: The headers as they appear in the uploaded file. Two are already
#: machine-shaped, which is the common case; one is written for a person and
#: carries its unit in parentheses, which is the other common case.
CONSUMPTION_HEADER = "Antibiotic consumption (DDD)"
RESISTANCE_HEADER = "resistance_pct"
GDP_HEADER = "gdp_per_capita"
COUNTRY_HEADER = "country"

#: What the profiler will store as `dataset_columns.name` for the human header
#: (whitespace to underscores, lowercased). Written out rather than computed so
#: that a change to the normalisation rule fails here loudly.
CONSUMPTION_KEY = "antibiotic_consumption_(ddd)"


def _csv(n: int = 120) -> bytes:
    rng = np.random.default_rng(2024)
    consumption = rng.normal(25, 6, n)
    resistance = 0.85 * consumption + rng.normal(0, 2.5, n)
    gdp = rng.normal(40000, 12000, n)
    rows = [f"{COUNTRY_HEADER},{CONSUMPTION_HEADER},{RESISTANCE_HEADER},{GDP_HEADER}"]
    codes = ["IND", "USA", "GBR", "FRA"]
    for i in range(n):
        rows.append(f"{codes[i % 4]},{consumption[i]:.3f},{resistance[i]:.3f},{gdp[i]:.1f}")
    return ("\n".join(rows) + "\n").encode()


def _drain() -> None:
    while Worker(worker_id="label-test").run_once():
        pass


def _map(cur, *, project_id, version_id, column, name, display_label,
         canonical_unit=None, transformation=None, status="approved"):
    """
    Approve a canonical variable for one column, as the mapping screen does.

    That sentence was untrue of `transformation` for as long as it existed.
    Nothing in the product wrote `transformation_required`, so this helper was
    the only thing that ever set it, and the guard it protects below — keeping
    a canonical unit off the axis of a column whose values are not in it —
    could not fire for a researcher. The reviewer can now answer that question
    when approving a label, which is what makes the fixture honest; the path
    itself is tested in
    `test_a_mapping_says_whether_the_numbers_still_need_converting.py`.
    """
    cur.execute("SELECT id FROM dataset_columns WHERE dataset_version_id = %s "
                "AND (name = %s OR original_name = %s)",
                (version_id, column, column))
    column_id = cur.fetchone()["id"]
    variable_id = new_id("cvar")
    cur.execute(
        "INSERT INTO canonical_variables(id, project_id, name, display_label, "
        "semantic_type, canonical_unit) VALUES (%s, %s, %s, %s, 'continuous', %s)",
        (variable_id, project_id, name, display_label, canonical_unit),
    )
    cur.execute(
        "INSERT INTO variable_mappings(id, project_id, dataset_column_id, "
        "canonical_variable_id, confidence, status, transformation_required) "
        "VALUES (%s, %s, %s, %s, 0.9, %s, %s)",
        (new_id("vmap"), project_id, column_id, variable_id, status, transformation),
    )
    return variable_id


@pytest.fixture()
def labelled():
    """A project whose columns span every label source the resolver knows."""
    user_id, project_id = new_id("usr"), new_id("prj")
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users(id, email, display_name, password_hash, password_salt) "
            "VALUES (%s, %s, %s, 'x', 'y')",
            (user_id, f"{user_id}@test.local", "Label Test"),
        )
        cur.execute("INSERT INTO projects(id, owner_user_id, name) VALUES (%s, %s, 'Labels')",
                    (project_id, user_id))
        record = storage.register_file(cur, project_id=project_id, filename="amr.csv",
                                       stream=io.BytesIO(_csv()), media_type="text/csv")
        source_id = objects.create_source(
            cur, project_id=project_id, source_type=SourceType.UPLOAD, title="amr.csv",
            actor="test", file_id=str(record["id"]),
            content_hash=str(record["content_hash"]),
        )
        workflow.enqueue(cur, workflow_name="ingest.source", project_id=project_id,
                         payload={"source_id": source_id})
    _drain()

    runs: dict[str, str] = {}
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT dv.id FROM dataset_versions dv JOIN datasets d "
                    "ON d.id = dv.dataset_id WHERE d.source_id = %s", (source_id,))
        version_id = cur.fetchone()["id"]

        _map(cur, project_id=project_id, version_id=version_id, column=RESISTANCE_HEADER,
             name="resistance_prevalence", display_label="Resistance prevalence",
             canonical_unit="%")
        # An approved mapping whose values still need converting. The label is
        # safe to use; the canonical unit is not, because nothing has converted
        # the column yet.
        _map(cur, project_id=project_id, version_id=version_id, column=GDP_HEADER,
             name="gross_domestic_product", display_label="GDP per capita",
             canonical_unit="constant 2015 USD",
             transformation="rebase from current to constant 2015 USD")

        # Specs name the header as the file wrote it, which is what the sandbox
        # will look for when it reads the file back.
        for name, spec in {
            "correlation": {"method": "pearson_correlation",
                            "variables": {"x": CONSUMPTION_HEADER,
                                          "y": RESISTANCE_HEADER}},
            "regression": {"method": "linear_regression",
                           "variables": {"outcome": RESISTANCE_HEADER,
                                         "predictors": [CONSUMPTION_HEADER, GDP_HEADER]}},
            "groups": {"method": "anova",
                       "variables": {"value": RESISTANCE_HEADER,
                                     "group": COUNTRY_HEADER}},
        }.items():
            created = analysis.create_spec(cur, project_id=project_id,
                                           spec={"dataset_version_ids": [version_id],
                                                 **spec}, actor="test")
            run_id = analysis.create_run(cur, project_id=project_id,
                                         spec_id=created["spec_id"])
            workflow.enqueue(cur, workflow_name="analysis.run", project_id=project_id,
                             payload={"analysis_run_id": run_id},
                             idempotency_key=f"analysis:{run_id}")
            runs[name] = run_id
    _drain()
    yield project_id, version_id, runs
    with connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))


def _recommend(run_id):
    with connection() as conn, conn.cursor() as cur:
        return visuals.recommend_for_run(cur, analysis_run_id=run_id)


# ---------------------------------------------------------------------------
# Where each label comes from
# ---------------------------------------------------------------------------


def test_the_profiler_stores_the_names_these_tests_assume(labelled):
    """Everything below rests on this; assert it rather than assume it."""
    _, version_id, _ = labelled
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT name, original_name, unit FROM dataset_columns "
                    "WHERE dataset_version_id = %s ORDER BY ordinal", (version_id,))
        columns = {r["name"]: r for r in cur.fetchall()}
    assert set(columns) == {COUNTRY_HEADER, CONSUMPTION_KEY,
                            RESISTANCE_HEADER, GDP_HEADER}
    assert columns[CONSUMPTION_KEY]["original_name"] == CONSUMPTION_HEADER
    # The unit was lifted out of the header into its own column, which is why
    # the label must not keep repeating it.
    assert columns[CONSUMPTION_KEY]["unit"] == "DDD"


def test_an_approved_canonical_variable_supplies_the_label_and_the_unit(labelled):
    project_id, version_id, _ = labelled
    with connection() as conn, conn.cursor() as cur:
        book = visuals.variable_labels(cur, project_id=project_id,
                                       dataset_version_id=version_id)
    entry = book.get(RESISTANCE_HEADER)
    assert entry.label == "Resistance prevalence"
    assert entry.unit == "%"
    assert entry.source == labels_module.CANONICAL


def test_an_unmapped_column_falls_back_to_the_header_a_person_wrote(labelled):
    project_id, version_id, _ = labelled
    with connection() as conn, conn.cursor() as cur:
        book = visuals.variable_labels(cur, project_id=project_id,
                                       dataset_version_id=version_id)
    entry = book.get(CONSUMPTION_KEY)
    assert entry.source == labels_module.DATASET_HEADER
    # The unit lives on the encoding, so the label must not carry it as well —
    # otherwise the renderer prints "Antibiotic consumption (DDD) (DDD)".
    assert entry.label == "Antibiotic consumption"
    assert entry.unit == "DDD"
    assert entry.described() == "Antibiotic consumption (DDD)"
    # Both spellings resolve, because a spec may use either.
    assert book.get(CONSUMPTION_HEADER).label == entry.label


def test_a_column_with_nothing_better_uses_its_own_name(labelled):
    """Not a failure. There is no better label anywhere in the system."""
    project_id, version_id, _ = labelled
    with connection() as conn, conn.cursor() as cur:
        book = visuals.variable_labels(cur, project_id=project_id,
                                       dataset_version_id=version_id)
    entry = book.get(COUNTRY_HEADER)
    assert entry.label == "country"
    assert entry.source == labels_module.COLUMN_NAME




def test_machine_style_unit_suffix_is_removed_before_rendering(labelled):
    """A known unit must appear once, not once in the label and again in parentheses."""
    project_id, version_id, _ = labelled
    with connection() as conn, conn.cursor() as cur:
        # Model the Palmer Penguins shape directly in the profiled schema:
        # the profiler recognizes _mm and stores mm separately.
        cur.execute(
            "UPDATE dataset_columns SET name = %s, original_name = %s, unit = %s "
            "WHERE dataset_version_id = %s AND name = %s",
            ("flipper_length_mm", "flipper_length_mm", "mm",
             version_id, COUNTRY_HEADER),
        )
        book = visuals.variable_labels(
            cur, project_id=project_id, dataset_version_id=version_id
        )

    entry = book.get("flipper_length_mm")
    assert entry.label == "flipper length"
    assert entry.unit == "mm"
    assert entry.described() == "flipper length (mm)"


def test_a_suggested_mapping_is_not_a_mapping(labelled):
    """§21 — a suggestion must not relabel a figure before anyone approves it."""
    project_id, version_id, _ = labelled
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE variable_mappings vm SET status = 'suggested' "
            "FROM dataset_columns dc "
            "WHERE dc.id = vm.dataset_column_id AND dc.name = %s",
            (RESISTANCE_HEADER,),
        )
        book = visuals.variable_labels(cur, project_id=project_id,
                                       dataset_version_id=version_id)
    entry = book.get(RESISTANCE_HEADER)
    assert entry.label == "resistance pct"
    assert entry.source == labels_module.COLUMN_NAME


def test_a_canonical_unit_is_not_borrowed_across_a_pending_transformation(labelled):
    """The figure plots the column's values, not the harmonised quantity.

    Printing "constant 2015 USD" on an axis of current-dollar values would be a
    worse lie than printing the raw column name, which is the failure this
    whole change exists to fix.
    """
    project_id, version_id, _ = labelled
    with connection() as conn, conn.cursor() as cur:
        book = visuals.variable_labels(cur, project_id=project_id,
                                       dataset_version_id=version_id)
    entry = book.get(GDP_HEADER)
    assert entry.label == "GDP per capita"
    assert entry.source == labels_module.CANONICAL
    assert entry.unit is None


# ---------------------------------------------------------------------------
# What a reader actually sees
# ---------------------------------------------------------------------------


def test_a_scatter_is_titled_and_captioned_in_the_project_s_own_words(labelled):
    recommendation = _recommend(labelled[2]["correlation"])
    spec = recommendation["spec"]
    assert spec.visual_type is VisualType.SCATTER
    assert spec.x.label == "Antibiotic consumption" and spec.x.unit == "DDD"
    assert spec.y.label == "Resistance prevalence" and spec.y.unit == "%"
    assert spec.title == "Resistance prevalence against Antibiotic consumption"
    assert "Antibiotic consumption (DDD)" in spec.caption
    assert "Resistance prevalence (%)" in spec.caption
    # The field is the data key and must stay exactly as the file wrote it.
    assert spec.x.field == CONSUMPTION_HEADER
    assert spec.y.field == RESISTANCE_HEADER


def test_a_group_comparison_is_labelled_too(labelled):
    spec = _recommend(labelled[2]["groups"])["spec"]
    assert spec.visual_type is VisualType.BOX
    assert spec.y.label == "Resistance prevalence"
    assert spec.title == "Resistance prevalence by country"


def test_a_forest_plot_labels_the_axis_that_lists_columns(labelled):
    """The y axis of a coefficient plot is the one axis no encoding describes."""
    spec = _recommend(labelled[2]["regression"])["spec"]
    assert spec.visual_type is VisualType.FOREST
    assert spec.category_labels == {
        CONSUMPTION_HEADER: "Antibiotic consumption",
        GDP_HEADER: "GDP per capita",
    }
    assert "Resistance prevalence" in spec.title


def test_no_raw_column_name_reaches_anything_a_reader_sees(labelled):
    """The Definition-of-Done item, stated as an assertion over every figure.

    Checking for the underscored spelling alone is not enough: the old code
    humanised the raw name, so `resistance_pct` reached the screen as
    "resistance pct" and a naive substring check would call that a pass. Both
    forms are banned wherever the project has decided on something better.
    """
    _, _, runs = labelled
    # `CONSUMPTION_HEADER` is deliberately absent: the researcher's own header
    # was already the right words, so "Antibiotic consumption (DDD)" appearing
    # in a caption is the label plus its unit, not a leaked column name.
    banned = [
        form
        for raw in (CONSUMPTION_KEY, RESISTANCE_HEADER, GDP_HEADER)
        for form in (raw, labels_module.humanise(raw))
    ]
    expected = {"correlation": ["Antibiotic consumption", "Resistance prevalence"],
                "regression": ["Resistance prevalence", "GDP per capita"],
                "groups": ["Resistance prevalence"]}

    for name, run_id in runs.items():
        spec = _recommend(run_id)["spec"]
        visible = " | ".join([
            spec.title, spec.subtitle, spec.caption,
            *(e.label for e in (spec.x, spec.y, spec.group, spec.facet) if e),
            *spec.category_labels.values(),
        ])
        for raw in banned:
            assert raw not in visible, f"{raw!r} reached the reader in {name}: {visible}"
        # And the curated label is actually there — absence of the raw name
        # would also be satisfied by a figure that named nothing at all.
        for label in expected[name]:
            assert label in visible, f"{label!r} missing from {name}: {visible}"


def test_the_rendered_axis_carries_the_label_and_the_unit(labelled):
    """A label that stops at the spec is not a label the researcher can read."""
    spec = _recommend(labelled[2]["correlation"])["spec"]
    data = VisualData(x_values=[1.0, 2.0], y_values=[3.0, 4.0], sample_size=120)
    chart = web.render(spec, data)
    titles = str(chart)
    assert "Antibiotic consumption (DDD)" in titles
    assert "Resistance prevalence (%)" in titles
    assert CONSUMPTION_KEY not in titles


def test_the_rendered_forest_axis_prints_labels_not_columns(labelled):
    spec = _recommend(labelled[2]["regression"])["spec"]
    data = VisualData(categories=[CONSUMPTION_HEADER, GDP_HEADER],
                      y_values=[0.85, 0.01], ci_low=[0.7, -0.02],
                      ci_high=[1.0, 0.04], sample_size=120)
    printed = str(web.render(spec, data))
    assert "Antibiotic consumption" in printed and "GDP per capita" in printed
    # The axis prints the field's values, so the column name must not survive
    # into the rows either — a label on the encoding would not have been enough.
    assert GDP_HEADER not in printed


# ---------------------------------------------------------------------------
# The recommender on its own
# ---------------------------------------------------------------------------


def test_the_recommender_still_works_with_no_labels_at_all():
    """A caller without a database gets the old behaviour, not a crash."""
    from throughline_visual.recommend import recommend

    picked = recommend(analysis_run_id="arun_1", method="pearson_correlation",
                       variables={"x": "consumption_ddd", "y": "resistance_pct"},
                       result={"sample_size": 40, "p_value": 0.01})
    assert picked["spec"].x.label == "consumption ddd"
    assert picked["spec"].x.unit is None


def test_labels_may_be_handed_in_as_plain_dictionaries():
    """The book is a contract, not a class the caller must import."""
    from throughline_visual.recommend import recommend

    picked = recommend(
        analysis_run_id="arun_1", method="pearson_correlation",
        variables={"x": "consumption_ddd", "y": "resistance_pct"},
        result={"sample_size": 40},
        labels={"consumption_ddd": {"label": "Antibiotic consumption", "unit": "DDD"},
                "resistance_pct": "Resistance"},
    )
    assert picked["spec"].x.label == "Antibiotic consumption"
    assert picked["spec"].x.unit == "DDD"
    assert picked["spec"].y.label == "Resistance"




def test_strip_trailing_unit_handles_machine_style_suffixes_without_guessing():
    assert labels_module.strip_trailing_unit("flipper_length_mm", "mm") == "flipper_length"
    assert labels_module.strip_trailing_unit("height-cm", "cm") == "height"
    assert labels_module.strip_trailing_unit("duration mins", "mins") == "duration"

    # No unit means no stripping. The label layer never invents semantics.
    assert labels_module.strip_trailing_unit("count_c", None) == "count_c"


def test_an_entry_with_no_label_does_not_claim_to_be_canonical():
    """Otherwise a test asserting the canonical layer was used would pass."""
    book = labels_module.LabelBook({"resistance_pct": {"label": "",
                                                       "source": labels_module.CANONICAL}})
    entry = book.get("resistance_pct")
    assert entry.label == "resistance pct"
    assert entry.source == labels_module.COLUMN_NAME
