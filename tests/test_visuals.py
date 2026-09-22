"""Visual intelligence (§72), the critic (§76) and multi-renderer output (§74)."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from throughline_domain import analysis, objects, storage, visuals, workflow
from throughline_domain.db import connection
from throughline_domain.ids import new_id
from throughline_schemas.enums import SourceType
from throughline_visual import critic as visual_critic
from throughline_visual import prepare as visual_prepare
from throughline_visual import recommend as visual_recommend
from throughline_visual.renderers import publication, web
from throughline_visual.spec import (
    Encoding,
    ResearchVisualSpec,
    UncertaintyDisplay,
    VisualData,
    VisualType,
)
from throughline_workers.runner import Worker


def _csv(n: int = 120) -> bytes:
    rng = np.random.default_rng(2024)
    consumption = rng.normal(25, 6, n)
    resistance = 0.85 * consumption + rng.normal(0, 2.5, n)
    gdp = rng.normal(40000, 12000, n)
    rows = ["country,consumption_ddd,resistance_pct,gdp_per_capita"]
    codes = ["IND", "USA", "GBR", "FRA"]
    for i in range(n):
        rows.append(f"{codes[i % 4]},{consumption[i]:.3f},{resistance[i]:.3f},{gdp[i]:.1f}")
    return ("\n".join(rows) + "\n").encode()


def _drain() -> None:
    while Worker(worker_id="visual-test").run_once():
        pass


@pytest.fixture()
def analysed():
    """A project with a completed correlation and a completed regression."""
    user_id, project_id = new_id("usr"), new_id("prj")
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users(id, email, display_name, password_hash, password_salt) "
            "VALUES (%s, %s, %s, 'x', 'y')",
            (user_id, f"{user_id}@test.local", "Visual Test"),
        )
        cur.execute("INSERT INTO projects(id, owner_user_id, name) VALUES (%s, %s, 'Visual')",
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
        for name, spec in {
            "correlation": {"method": "pearson_correlation",
                            "variables": {"x": "consumption_ddd", "y": "resistance_pct"}},
            "regression": {"method": "linear_regression",
                           "variables": {"outcome": "resistance_pct",
                                         "predictors": ["consumption_ddd",
                                                        "gdp_per_capita"]}},
            "groups": {"method": "anova",
                       "variables": {"value": "resistance_pct", "group": "country"}},
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


# ---------------------------------------------------------------------------
# §72 — recommendation
# ---------------------------------------------------------------------------


def test_correlation_is_recommended_as_a_scatter(analysed):
    project_id, _, runs = analysed
    with connection() as conn, conn.cursor() as cur:
        recommendation = visuals.recommend_for_run(cur, analysis_run_id=runs["correlation"])
    assert recommendation["visual_type"] is VisualType.SCATTER
    assert "every observation" in recommendation["reason"]
    spec = recommendation["spec"]
    # A correlation coefficient CI is not a y-axis confidence band, and a
    # correlation run did not fit a regression model.
    assert spec.uncertainty is UncertaintyDisplay.NONE
    assert not any(a.kind == "regression_line" for a in spec.annotations)
    # §52 — the caption must not upgrade the association.
    assert "does not establish causation" in spec.caption
    assert "95% CI" in spec.caption
    assert "r =" in spec.caption
    assert "pearson_r" not in spec.caption
    assert recommendation["alternatives"]


def test_multiple_regression_is_recommended_as_a_coefficient_plot(analysed):
    project_id, _, runs = analysed
    with connection() as conn, conn.cursor() as cur:
        recommendation = visuals.recommend_for_run(cur, analysis_run_id=runs["regression"])
    assert recommendation["visual_type"] is VisualType.FOREST
    assert recommendation["spec"].uncertainty is UncertaintyDisplay.CONFIDENCE_INTERVAL


def test_group_comparison_prefers_a_box_over_a_bar_of_means(analysed):
    """A bar of means hides the spread that decides whether groups differ."""
    project_id, _, runs = analysed
    with connection() as conn, conn.cursor() as cur:
        recommendation = visuals.recommend_for_run(cur, analysis_run_id=runs["groups"])
    assert recommendation["visual_type"] is VisualType.BOX
    assert "hide" in recommendation["reason"]
    assert any(a["visual_type"] is VisualType.BAR
               for a in recommendation["alternatives"])


def test_incomplete_analysis_cannot_be_visualised(analysed):
    project_id, version_id, _ = analysed
    with connection() as conn, conn.cursor() as cur:
        created = analysis.create_spec(cur, project_id=project_id, spec={
            "method": "pearson_correlation", "dataset_version_ids": [version_id],
            "variables": {"x": "consumption_ddd", "y": "resistance_pct"},
        }, actor="test")
        pending = analysis.create_run(cur, project_id=project_id,
                                      spec_id=created["spec_id"])
        with pytest.raises(visuals.VisualError) as exc:
            visuals.recommend_for_run(cur, analysis_run_id=pending)
    assert "queued" in str(exc.value)


def test_simple_regression_figure_uses_recorded_fit_not_a_refit(analysed, tmp_path):
    project_id, _, runs = analysed
    with connection() as conn, conn.cursor() as cur:
        run = analysis.get_run(cur, runs["regression"])
        # The fixture's regression run has two predictors, so create one simple
        # regression whose line is meaningful on a 2D chart.
        spec_created = analysis.create_spec(
            cur,
            project_id=project_id,
            spec={
                "method": "linear_regression",
                "dataset_version_ids": [run["spec"]["dataset_version_ids"][0]],
                "variables": {"outcome": "resistance_pct",
                              "predictors": ["consumption_ddd"]},
            },
            actor="test",
        )
        simple = analysis.create_run(
            cur, project_id=project_id, spec_id=spec_created["spec_id"]
        )
        workflow.enqueue(
            cur, workflow_name="analysis.run", project_id=project_id,
            payload={"analysis_run_id": simple}, idempotency_key=f"analysis:{simple}",
        )
    _drain()

    with connection() as conn, conn.cursor() as cur:
        simple_run = analysis.get_run(cur, simple)
        rec = visuals.recommend_for_run(cur, analysis_run_id=simple)
        sample = _sample_for(
            cur, simple_run, ["consumption_ddd", "resistance_pct"]
        )
        data = visual_prepare.prepare(
            rec["spec"], analysis_result=simple_run["result"], sample=sample
        )

    coefficients = simple_run["result"]["extra"]["coefficients"]
    assert data.statistics["fit_slope"] == pytest.approx(
        coefficients["consumption_ddd"]["estimate"]
    )
    assert data.statistics["fit_intercept"] == pytest.approx(
        coefficients["const"]["estimate"]
    )
    assert rec["spec"].uncertainty is UncertaintyDisplay.NONE

    chart = web.render(rec["spec"], data)
    line_layer = next(
        layer for layer in chart["layer"]
        if isinstance(layer, dict) and "data" in layer
    )
    line_values = line_layer["data"]["values"]
    x0, x1 = line_values[0]["x"], line_values[1]["x"]
    slope = (line_values[1]["y"] - line_values[0]["y"]) / (x1 - x0)
    assert slope == pytest.approx(coefficients["consumption_ddd"]["estimate"])


# ---------------------------------------------------------------------------
# §76 — the critic
# ---------------------------------------------------------------------------


def _bar_spec(**kwargs) -> ResearchVisualSpec:
    defaults = dict(
        visual_type=VisualType.BAR, analysis_run_id="arun_test",
        x=Encoding(field="group", label="group"),
        y=Encoding(field="value", label="value", include_zero=False),
        title="Group means", caption="Mean value by group.",
    )
    return ResearchVisualSpec(**(defaults | kwargs))


def test_truncated_bar_axis_is_detected_and_fixed():
    """§76 — bar length encodes magnitude, so a cropped baseline misleads."""
    data = VisualData(categories=["a", "b"], y_values=[10.0, 10.4], sample_size=80)
    report = visual_critic.critique(_bar_spec(), data, autofix=True)
    axis = next(c for c in report.critiques if c.check == "axis_integrity")
    assert axis.outcome == "fixed" and axis.severity == "blocking"
    assert report.spec.y.include_zero is True
    assert report.publishable is True


def test_truncated_axis_blocks_publication_when_not_autofixed():
    data = VisualData(categories=["a", "b"], y_values=[10.0, 10.4], sample_size=80)
    report = visual_critic.critique(_bar_spec(), data, autofix=False)
    assert report.publishable is False
    assert [c.check for c in report.blocking] == ["axis_integrity"]


def test_a_caption_claiming_causation_blocks_publication():
    """§52 — the caption is where association quietly becomes cause."""
    spec = _bar_spec(caption="Antibiotic consumption causes resistance.",
                     y=Encoding(field="value", include_zero=True))
    data = VisualData(categories=["a", "b"], y_values=[1.0, 2.0], sample_size=80)
    report = visual_critic.critique(spec, data,
                                    analysis={"causal_status": "association_only"})
    overstatement = next(c for c in report.critiques if c.check == "overstatement")
    assert overstatement.outcome == "violated"
    assert report.publishable is False
    assert "association_only" in overstatement.detail


def test_causal_language_is_allowed_when_causality_was_assessed():
    spec = _bar_spec(caption="The intervention causes a reduction in resistance.",
                     y=Encoding(field="value", include_zero=True))
    data = VisualData(categories=["a", "b"], y_values=[1.0, 2.0], sample_size=80)
    report = visual_critic.critique(spec, data,
                                    analysis={"causal_status": "causal_supported"})
    assert report.publishable is True


def test_a_missing_confidence_interval_is_added():
    spec = _bar_spec(y=Encoding(field="value", include_zero=True))
    data = VisualData(categories=["a", "b"], y_values=[1.0, 2.0],
                      ci_low=[0.8, 1.7], ci_high=[1.2, 2.3], sample_size=80)
    report = visual_critic.critique(spec, data, analysis={"ci_low": 0.8, "ci_high": 1.2})
    uncertainty = next(c for c in report.critiques
                       if c.check == "uncertainty_representation")
    assert uncertainty.outcome == "fixed"
    assert report.spec.uncertainty is not UncertaintyDisplay.NONE


def test_sample_size_is_added_to_the_caption():
    spec = _bar_spec(y=Encoding(field="value", include_zero=True))
    data = VisualData(categories=["a"], y_values=[1.0], sample_size=137)
    report = visual_critic.critique(spec, data)
    assert "n = 137" in report.spec.caption


def test_category_overload_is_flagged():
    spec = _bar_spec(y=Encoding(field="value", include_zero=True))
    data = VisualData(categories=[f"c{i}" for i in range(20)],
                      y_values=[float(i) for i in range(20)], sample_size=200)
    report = visual_critic.critique(spec, data)
    overload = next(c for c in report.critiques if c.check == "category_overload")
    assert overload.outcome == "warned"


def test_a_line_over_categories_is_called_misleading():
    spec = ResearchVisualSpec(
        visual_type=VisualType.LINE, analysis_run_id="arun_test",
        x=Encoding(field="country"), y=Encoding(field="value"),
        title="Trend", caption="Values by country.",
    )
    data = VisualData(x_values=["IND", "USA", "GBR"], y_values=[1.0, 2.0, 3.0],
                      sample_size=30)
    report = visual_critic.critique(spec, data)
    encoding = next(c for c in report.critiques if c.check == "misleading_encoding")
    assert encoding.outcome == "violated"


# ---------------------------------------------------------------------------
# §74 — one spec, many renderers
# ---------------------------------------------------------------------------


def test_one_spec_renders_to_publication_and_web_without_recomputing(analysed, tmp_path):
    """§74 — analysis logic is not recreated per output."""
    project_id, _, runs = analysed
    with connection() as conn, conn.cursor() as cur:
        run = analysis.get_run(cur, runs["regression"])
        recommendation = visuals.recommend_for_run(cur, analysis_run_id=runs["regression"])
    spec = recommendation["spec"]
    data = visual_prepare.prepare(spec, analysis_result=run["result"])

    svg = publication.render(spec, data, path=tmp_path / "figure.svg", fmt="svg")
    chart = web.render(spec, data)

    assert svg.exists() and svg.stat().st_size > 1000
    assert svg.read_text(encoding="utf-8").lstrip().startswith("<?xml")

    # Both outputs carry the same estimates, because both were given the same
    # prepared data and neither recomputed anything.
    plotted = [layer for layer in chart["layer"] if layer["mark"].get("type") == "point"]
    assert plotted
    web_values = {row["estimate"] for row in chart["data"]["values"]}
    assert web_values == set(data.y_values)
    # LAW 5 — the web output still names the computation behind it.
    assert chart["usermeta"]["analysis_run_id"] == runs["regression"]
    assert chart["usermeta"]["statistics"]["method"] == "linear_regression"


# Every format the renderer declares, not three of them. Publish offers seven,
# and eps, tiff, jpeg and webp were offered and declared supported and never
# once rendered here — the same shape as the vega-lite path that failed on
# every call for a migration's lifetime without a test noticing.
@pytest.mark.parametrize("fmt", publication.SUPPORTED_FORMATS)
def test_publication_formats_all_render(analysed, tmp_path, fmt):
    """§84 — SVG, PDF and high-DPI PNG."""
    project_id, _, runs = analysed
    with connection() as conn, conn.cursor() as cur:
        run = analysis.get_run(cur, runs["regression"])
        recommendation = visuals.recommend_for_run(cur, analysis_run_id=runs["regression"])
    spec = recommendation["spec"]
    data = visual_prepare.prepare(spec, analysis_result=run["result"])
    path = publication.render(spec, data, path=tmp_path / f"f.{fmt}", fmt=fmt)
    assert path.exists() and path.stat().st_size > 500


def test_unsupported_format_is_refused(analysed, tmp_path):
    project_id, _, runs = analysed
    with connection() as conn, conn.cursor() as cur:
        run = analysis.get_run(cur, runs["regression"])
        spec = visuals.recommend_for_run(cur, analysis_run_id=runs["regression"])["spec"]
    data = visual_prepare.prepare(spec, analysis_result=run["result"])
    # `tiff` was the example here until it became a supported format — several
    # journals ask for it by name. The property under test is that an
    # unsupported format is refused, not that this particular one is, so the
    # example moved rather than the test being deleted.
    with pytest.raises(publication.RenderError) as exc:
        publication.render(spec, data, path=tmp_path / "f.bmp", fmt="bmp")
    assert "not a supported publication format" in str(exc.value)


# ---------------------------------------------------------------------------
# LAW 5 and §78
# ---------------------------------------------------------------------------


def test_a_visual_is_linked_to_the_analysis_it_draws(analysed):
    """LAW 5 — a figure may not escape its source graph."""
    project_id, _, runs = analysed
    with connection() as conn, conn.cursor() as cur:
        run = analysis.get_run(cur, runs["correlation"])
        recommendation = visuals.recommend_for_run(cur, analysis_run_id=runs["correlation"])
        sample = _sample_for(cur, run, ["consumption_ddd", "resistance_pct"])
        created = visuals.create_visual(cur, project_id=project_id,
                                        spec=recommendation["spec"], actor="test",
                                        sample=sample, recommendation=recommendation)

        from throughline_domain import lineage

        ancestors = lineage.ancestors(cur, created["object_id"])
    types = {a["object_type"] for a in ancestors}
    assert "analysis" in types and "dataset" in types, ancestors


def test_presentation_edits_are_allowed(analysed):
    project_id, _, runs = analysed
    with connection() as conn, conn.cursor() as cur:
        run = analysis.get_run(cur, runs["correlation"])
        recommendation = visuals.recommend_for_run(cur, analysis_run_id=runs["correlation"])
        sample = _sample_for(cur, run, ["consumption_ddd", "resistance_pct"])
        created = visuals.create_visual(cur, project_id=project_id,
                                        spec=recommendation["spec"], actor="test",
                                        sample=sample)
        edited = visuals.apply_edit(cur, visual_id=created["visual_id"], actor="test",
                                    changes={"title": "Consumption and resistance"})
    assert edited["spec"].title == "Consumption and resistance"


def test_an_edit_that_changes_the_data_requires_a_new_analysis(analysed):
    """§78 — "Never visually fake a different answer."."""
    project_id, _, runs = analysed
    with connection() as conn, conn.cursor() as cur:
        run = analysis.get_run(cur, runs["correlation"])
        recommendation = visuals.recommend_for_run(cur, analysis_run_id=runs["correlation"])
        sample = _sample_for(cur, run, ["consumption_ddd", "resistance_pct"])
        created = visuals.create_visual(cur, project_id=project_id,
                                        spec=recommendation["spec"], actor="test",
                                        sample=sample)
        with pytest.raises(visuals.EditRequiresRecomputation) as exc:
            visuals.apply_edit(cur, visual_id=created["visual_id"], actor="test",
                               changes={"filters": [{"column": "consumption_ddd",
                                                     "operator": "lt", "value": 30}]})
    assert "new AnalysisSpec" in str(exc.value)


def test_a_figure_failing_the_critic_cannot_be_rendered(analysed):
    """§76 — an unfixed blocking problem must stop publication."""
    project_id, _, runs = analysed
    with connection() as conn, conn.cursor() as cur:
        recommendation = visuals.recommend_for_run(cur, analysis_run_id=runs["correlation"])
        spec = recommendation["spec"].model_copy(
            update={"caption": "Consumption causes resistance."})
        run = analysis.get_run(cur, runs["correlation"])
        sample = _sample_for(cur, run, ["consumption_ddd", "resistance_pct"])
        created = visuals.create_visual(cur, project_id=project_id, spec=spec,
                                        actor="test", sample=sample)
        assert created["publishable"] is False
        with pytest.raises(visuals.VisualError) as exc:
            visuals.render_visual(cur, visual_id=created["visual_id"], fmt="svg")
    assert "did not pass the visualization critic" in str(exc.value)


def test_renders_go_stale_when_the_spec_changes(analysed):
    """§102 — a figure whose spec moved on must not be silently reused."""
    project_id, _, runs = analysed
    with connection() as conn, conn.cursor() as cur:
        run = analysis.get_run(cur, runs["correlation"])
        recommendation = visuals.recommend_for_run(cur, analysis_run_id=runs["correlation"])
        sample = _sample_for(cur, run, ["consumption_ddd", "resistance_pct"])
        created = visuals.create_visual(cur, project_id=project_id,
                                        spec=recommendation["spec"], actor="test",
                                        sample=sample)
        visuals.render_visual(cur, visual_id=created["visual_id"], fmt="svg")
        assert all(not r["stale"] for r in visuals.stale_renders(cur, created["visual_id"]))

        visuals.apply_edit(cur, visual_id=created["visual_id"], actor="test",
                           changes={"title": "A different title"})
        assert all(r["stale"] for r in visuals.stale_renders(cur, created["visual_id"]))


def _sample_for(cur, run, columns: list[str]) -> dict[str, list[float]]:
    """A bounded sample from the stored dataset, as the API would provide."""
    import pandas as pd
    from throughline_domain import storage as store

    cur.execute(
        """
        SELECT f.storage_key, f.filename FROM dataset_versions dv
        JOIN datasets d ON d.id = dv.dataset_id
        JOIN sources s ON s.id = d.source_id
        JOIN files f ON f.id = s.file_id
        WHERE dv.id = %s
        """,
        (run["dataset_version_ids"][0],),
    )
    row = cur.fetchone()
    frame = pd.read_csv(store.path_for(row["storage_key"]))
    return {c: frame[c].tolist()[:500] for c in columns if c in frame.columns}


def test_coefficients_on_incomparable_scales_are_flagged():
    """§76 — a predictor measured in tens of thousands gets a coefficient near
    zero, and its interval collapses to an invisible dot beside one measured in
    units. The reader sees "no effect" when the truth may be "different units".
    """
    spec = ResearchVisualSpec(
        visual_type=VisualType.FOREST, analysis_run_id="arun_test",
        x=Encoding(field="estimate", label="coefficient (95% CI)"),
        uncertainty=UncertaintyDisplay.CONFIDENCE_INTERVAL,
        title="Adjusted associations", caption="Coefficients. n = 120.",
    )
    data = VisualData(categories=["consumption_ddd", "gdp_per_capita"],
                      y_values=[0.80, 0.0000012],
                      ci_low=[0.72, -0.0000004], ci_high=[0.88, 0.0000028],
                      sample_size=120)
    report = visual_critic.critique(spec, data)
    scales = next(c for c in report.critiques if c.check == "comparable_scales")
    assert scales.outcome == "violated"
    assert "standardized coefficients" in scales.detail


def test_comparable_coefficients_pass_the_scale_check():
    spec = ResearchVisualSpec(
        visual_type=VisualType.FOREST, analysis_run_id="arun_test",
        x=Encoding(field="estimate"), uncertainty=UncertaintyDisplay.CONFIDENCE_INTERVAL,
        title="Adjusted", caption="Coefficients. n = 120.",
    )
    data = VisualData(categories=["a", "b"], y_values=[0.8, 0.35],
                      ci_low=[0.7, 0.2], ci_high=[0.9, 0.5], sample_size=120)
    report = visual_critic.critique(spec, data)
    scales = next(c for c in report.critiques if c.check == "comparable_scales")
    assert scales.outcome == "passed"


# ---------------------------------------------------------------------------
# Exporting a figure at a stated size (§84)
# ---------------------------------------------------------------------------

@pytest.fixture()
def figure(analysed):
    """A real recommended spec and its prepared data."""
    _, _, runs = analysed
    with connection() as conn, conn.cursor() as cur:
        run = analysis.get_run(cur, runs["regression"])
        recommendation = visuals.recommend_for_run(
            cur, analysis_run_id=runs["regression"])
    spec = recommendation["spec"]
    return spec, visual_prepare.prepare(spec, analysis_result=run["result"])


@pytest.mark.parametrize("height", [720, 1080])
def test_a_raster_export_is_exactly_the_height_asked_for(figure, tmp_path, height):
    """
    The whole point of naming a size. The publication style trims to content,
    which makes the delivered dimensions unpredictable — so a caller asking for
    1080 gets "about 1000, depending on how long the axis labels are". A
    constrained layout fits the labels inside the figure instead of growing it.
    """
    from PIL import Image

    spec, data = figure
    path = publication.render(spec, data, path=tmp_path / f"f{height}.png",
                              fmt="png", height_px=height)

    with Image.open(path) as image:
        assert image.height == height


def test_the_width_follows_the_figure_rather_than_a_video_frame(figure, tmp_path):
    """
    "1080p" names a 16:9 video frame. A figure's aspect ratio is set by its
    content — forcing 16:9 would letterback or distort it, and nobody asking for
    a bigger image wants their axes stretched.
    """
    from PIL import Image

    spec, data = figure
    path = publication.render(spec, data, path=tmp_path / "f.png", fmt="png",
                              height_px=1080)

    with Image.open(path) as image:
        assert image.height == 1080
        # The publication figure is 6.5x4.2in — about 1.55:1, not 1.78:1.
        assert 1.4 < image.width / image.height < 1.7
        assert image.width != 1920


def test_a_pixel_height_is_refused_for_a_vector_format(figure, tmp_path):
    """
    Silently ignoring it would leave the caller believing the file is 1080
    tall. An SVG has no height in pixels — that is the point of it.
    """
    spec, data = figure
    with pytest.raises(publication.RenderError, match="vector"):
        publication.render(spec, data, path=tmp_path / "f.svg", fmt="svg",
                           height_px=1080)


def test_an_unreadably_small_export_is_refused(figure, tmp_path):
    """A figure nobody can read is not a smaller figure."""
    spec, data = figure
    with pytest.raises(publication.RenderError, match="legibly"):
        publication.render(spec, data, path=tmp_path / "f.png", fmt="png",
                           height_px=64)


@pytest.mark.parametrize("fmt", ["png", "tiff", "jpeg", "webp", "svg", "pdf"])
def test_every_offered_format_writes_a_real_file(figure, tmp_path, fmt):
    spec, data = figure
    height = None if fmt in publication.VECTOR_FORMATS else 720
    path = publication.render(spec, data, path=tmp_path / f"f.{fmt}", fmt=fmt,
                              height_px=height)

    assert path.exists() and path.stat().st_size > 500


def test_jpeg_is_offered_with_the_reason_not_to_use_it(figure, tmp_path):
    """
    It is asked for, so it is provided. But these figures are line art and
    text: JPEG rings around glyph edges, has no transparency, and is usually
    *larger* than PNG for this content. Saying so before the download beats
    letting somebody discover it in review.
    """
    warning = publication.warn_about_format("jpeg")
    assert warning is not None
    assert "lossy" in warning.lower()
    assert "png" in warning.lower()

    # And nothing is said about the formats that are simply correct.
    assert publication.warn_about_format("svg") is None
    assert publication.warn_about_format("png") is None


def test_a_photograph_is_the_case_where_jpeg_is_reasonable(figure):
    """A blanket warning that is wrong sometimes is one people learn to skip."""
    assert publication.warn_about_format("jpeg", has_photograph=True) is None


def test_an_exported_png_carries_its_provenance(figure, tmp_path):
    """
    LAW 5 — no output detached from the source graph. A figure that leaves the
    building is the one case where the link cannot be a foreign key, so it
    travels inside the file.
    """
    from PIL import Image

    spec, data = figure
    path = publication.render(
        spec, data, path=tmp_path / "f.png", fmt="png", height_px=720,
        metadata={"Title": "vis_123", "Description": "spec_hash=abc123"})

    with Image.open(path) as image:
        embedded = {k: str(v) for k, v in (image.text or {}).items()}
    assert "vis_123" in " ".join(embedded.values())
    assert "abc123" in " ".join(embedded.values())


def test_two_sizes_of_one_figure_are_two_files(analysed, tmp_path):
    """
    Without the size in the key and the filename, asking for a 1080px export
    after a 720px one silently destroys the first — same row, same file. The
    artifact renderer had this exact bug (D010); this is the second place.
    """
    from throughline_domain.storage import storage_root

    project_id, _, runs = analysed
    with connection() as conn, conn.cursor() as cur:
        recommendation = visuals.recommend_for_run(
            cur, analysis_run_id=runs["regression"])
        visual_id = visuals.create_visual(
            cur, project_id=project_id, spec=recommendation["spec"],
            recommendation=recommendation, actor="test")["visual_id"]
        small = visuals.render_visual(cur, visual_id=visual_id, fmt="png",
                                      height_px=720)
        large = visuals.render_visual(cur, visual_id=visual_id, fmt="png",
                                      height_px=1080)
        conn.commit()

    assert small["storage_key"] != large["storage_key"]
    assert (storage_root() / small["storage_key"]).exists()
    assert (storage_root() / large["storage_key"]).exists()

    from PIL import Image
    with Image.open(storage_root() / small["storage_key"]) as image:
        assert image.height == 720
    with Image.open(storage_root() / large["storage_key"]) as image:
        assert image.height == 1080


def test_every_render_row_describes_bytes_that_are_there(analysed):
    """
    The row records a content hash and a size. Both were describing a file that
    a later render had already overwritten.
    """
    import hashlib

    from throughline_domain.storage import storage_root

    project_id, _, runs = analysed
    with connection() as conn, conn.cursor() as cur:
        recommendation = visuals.recommend_for_run(
            cur, analysis_run_id=runs["regression"])
        visual_id = visuals.create_visual(
            cur, project_id=project_id, spec=recommendation["spec"],
            recommendation=recommendation, actor="test")["visual_id"]
        visuals.render_visual(cur, visual_id=visual_id, fmt="png", height_px=720)
        visuals.render_visual(cur, visual_id=visual_id, fmt="png", height_px=1080)
        cur.execute(
            "SELECT storage_key, content_hash, bytes FROM visual_renders "
            "WHERE visual_id = %s AND storage_key IS NOT NULL", (visual_id,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.commit()

    assert len(rows) == 2
    for row in rows:
        path = storage_root() / row["storage_key"]
        assert path.exists(), row["storage_key"]
        assert path.stat().st_size == row["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["content_hash"]


def test_a_lossy_export_comes_back_with_its_warning(analysed):
    """
    The researcher is told before they put it in a manuscript, not after a
    reviewer notices the ringing around the axis labels.
    """
    project_id, _, runs = analysed
    with connection() as conn, conn.cursor() as cur:
        recommendation = visuals.recommend_for_run(
            cur, analysis_run_id=runs["regression"])
        visual_id = visuals.create_visual(
            cur, project_id=project_id, spec=recommendation["spec"],
            recommendation=recommendation, actor="test")["visual_id"]
        jpeg = visuals.render_visual(cur, visual_id=visual_id, fmt="jpeg",
                                     height_px=720)
        png = visuals.render_visual(cur, visual_id=visual_id, fmt="png",
                                    height_px=720)
        conn.commit()

    assert jpeg["warning"] and "lossy" in jpeg["warning"].lower()
    assert png["warning"] is None


# ---------------------------------------------------------------------------
# The route a researcher actually takes (LAW 5)
#
# Everything above tests the domain, and the domain was never the problem: the
# four HTTP routes had no caller in the interface at all, and the Figures
# screen exported by cloning the live `<svg>` out of the page. That produced a
# file, which is why nobody noticed it skipped the critic, the lineage edge,
# the publication formats and the traceable filename.
#
# So this walks the sequence the export button performs, over HTTP, because
# that seam is the one nothing exercised.
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from throughline_api.app import app

    with TestClient(app) as test_client:
        yield test_client
    # The API owns its own connections, so these tests commit for real.
    with connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE email = %s", ("figures@lab.local",))


def _http_project_with_analysis(client) -> tuple[str, str]:
    """A project owned by the signed-in account, with one completed analysis."""
    status = client.get("/api/auth/status").json()
    endpoint = "/api/auth/setup" if status["needs_setup"] else "/api/auth/login"
    assert client.post(endpoint, json={
        "email": "figures@lab.local", "display_name": "Figures",
        "password": "correct-horse-battery"}).status_code == 200

    project_id = client.post("/api/projects", json={"name": "Figures"}).json()["id"]
    assert client.post(
        f"/api/projects/{project_id}/sources",
        files={"file": ("amr.csv", _csv(), "text/csv")}).status_code == 202
    _drain()

    sources = client.get(f"/api/projects/{project_id}/sources").json()
    version_id = sources[0]["dataset"]["dataset_version_id"]

    queued = client.post(f"/api/projects/{project_id}/analyses", json={
        "method": "pearson_correlation",
        "dataset_version_ids": [version_id],
        "variables": {"x": "consumption_ddd", "y": "resistance_pct"},
    })
    assert queued.status_code == 202, queued.text
    _drain()
    return project_id, queued.json()["analysis_run_id"]


def test_a_figure_exported_over_http_is_recorded_against_its_analysis(client):
    """
    LAW 5 — a figure resolves back to the computation and the dataset under it.
    The DOM export produced a file related to nothing; this produces an object
    with a `VISUALIZES` edge, which is what makes a figure on a slide traceable.
    """
    project_id, run_id = _http_project_with_analysis(client)

    created = client.post(f"/api/projects/{project_id}/visuals",
                          json={"analysis_run_id": run_id})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["visual_id"].startswith("vis_")
    # The critic ran and said something, rather than the figure being stored blind.
    assert "critiques" in body["critique"]

    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT lineage_type FROM artifact_lineage_edges WHERE target_artifact_id = %s",
            (body["object_id"],))
        assert [r["lineage_type"] for r in cur.fetchall()] == ["visualizes"]


def test_the_download_a_researcher_asks_for_arrives_as_that_format(client):
    """
    The formats journals ask for, which the browser export could not produce:
    it could only save what the page happened to be holding.
    """
    project_id, run_id = _http_project_with_analysis(client)
    visual_id = client.post(f"/api/projects/{project_id}/visuals",
                            json={"analysis_run_id": run_id}).json()["visual_id"]

    pdf = client.get(f"/api/visuals/{visual_id}/download?format=pdf")
    assert pdf.status_code == 200, pdf.text
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content[:4] == b"%PDF"
    # Named for the figure, not for the variables: a folder of downloads stays
    # legible, and every file can be traced back.
    assert visual_id in pdf.headers["content-disposition"]

    png = client.get(f"/api/visuals/{visual_id}/download?format=png&height=600")
    assert png.status_code == 200, png.text
    assert png.headers["content-type"] == "image/png"
    assert png.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert "600px" in png.headers["content-disposition"]


def test_a_figure_comes_on_the_ground_it_is_asked_for_and_keeps_the_other(client):
    """T191: a dark copy is its own file, and does not replace the light one a
    manuscript already links to. Every export used to be opaque white."""
    from matplotlib import image as mpimg
    import io

    project_id, run_id = _http_project_with_analysis(client)
    visual_id = client.post(f"/api/projects/{project_id}/visuals",
                            json={"analysis_run_id": run_id}).json()["visual_id"]

    light = client.post(f"/api/visuals/{visual_id}/render?format=png")
    dark = client.post(f"/api/visuals/{visual_id}/render?format=png&ground=dark")
    clear = client.post(
        f"/api/visuals/{visual_id}/render?format=png&ground=dark&transparent=true")
    for response in (light, dark, clear):
        assert response.status_code == 200, response.text
    keys = {light.json()["storage_key"], dark.json()["storage_key"],
            clear.json()["storage_key"]}
    assert len(keys) == 3, "each ground is its own file"

    again = client.post(f"/api/visuals/{visual_id}/render?format=png")
    assert again.json()["render_id"] == light.json()["render_id"]

    download = client.get(
        f"/api/visuals/{visual_id}/download?format=png&ground=dark&transparent=true")
    assert download.status_code == 200, download.text
    assert "-dark-transparent.png" in download.headers["content-disposition"]
    corner = mpimg.imread(io.BytesIO(download.content))[2, 2]
    assert corner.shape[0] == 4 and corner[3] == 0


def test_a_ground_that_does_not_exist_is_refused_with_the_ones_that_do(client):
    project_id, run_id = _http_project_with_analysis(client)
    visual_id = client.post(f"/api/projects/{project_id}/visuals",
                            json={"analysis_run_id": run_id}).json()["visual_id"]

    sepia = client.post(f"/api/visuals/{visual_id}/render?format=png&ground=sepia")
    assert sepia.status_code == 400, sepia.text
    assert "light, dark" in sepia.json()["detail"]
    eps = client.post(f"/api/visuals/{visual_id}/render?format=eps&transparent=true")
    assert eps.status_code == 400 and "no transparency" in eps.json()["detail"]


def _second_figure(client, project_id: str) -> str:
    """A second recorded figure in the same project, from a Spearman run."""
    sources = client.get(f"/api/projects/{project_id}/sources").json()
    version_id = sources[0]["dataset"]["dataset_version_id"]
    queued = client.post(f"/api/projects/{project_id}/analyses", json={
        "method": "spearman_correlation",
        "dataset_version_ids": [version_id],
        "variables": {"x": "consumption_ddd", "y": "resistance_pct"},
    })
    assert queued.status_code == 202, queued.text
    _drain()
    return client.post(f"/api/projects/{project_id}/visuals", json={
        "analysis_run_id": queued.json()["analysis_run_id"]}).json()["visual_id"]


def test_recorded_figures_compose_into_one_lettered_figure(client):
    """T192: panels A and B, each with its recorded numbers, in one file."""
    project_id, run_id = _http_project_with_analysis(client)
    first = client.post(f"/api/projects/{project_id}/visuals",
                        json={"analysis_run_id": run_id}).json()["visual_id"]
    second = _second_figure(client, project_id)

    checked = client.post(f"/api/projects/{project_id}/figures/compose/check",
                          json={"visual_ids": [first, second]})
    assert checked.status_code == 200, checked.text
    panels = checked.json()["panels"]
    assert [p["letter"] for p in panels] == ["A", "B"]
    assert [p["visual_id"] for p in panels] == [first, second]
    # The numbers are the runs' own, not recomputed: n and a p-value each.
    assert all("n = 120" in p["metrics"] and "p " in p["metrics"] for p in panels)

    drawn = client.post(f"/api/projects/{project_id}/figures/compose",
                        json={"visual_ids": [first, second], "format": "svg"})
    assert drawn.status_code == 200, drawn.text
    assert "figure-2-panels.svg" in drawn.headers["content-disposition"]
    text = drawn.text
    # Text stays text in the SVG, so the letters and numbers are searchable.
    assert ">A<" in text and ">B<" in text
    # And the file carries both panels' ids for tracing back.
    assert first in text and second in text


def test_a_composition_cannot_reach_a_figure_in_another_project(client):
    project_id, run_id = _http_project_with_analysis(client)
    mine = client.post(f"/api/projects/{project_id}/visuals",
                       json={"analysis_run_id": run_id}).json()["visual_id"]
    other_id, other_run = _http_project_with_analysis(client)
    theirs = client.post(f"/api/projects/{other_id}/visuals",
                         json={"analysis_run_id": other_run}).json()["visual_id"]

    for route in ("figures/compose", "figures/compose/check"):
        refused = client.post(f"/api/projects/{project_id}/{route}",
                              json={"visual_ids": [mine, theirs], "format": "pdf"})
        assert refused.status_code == 404, (route, refused.text)
        assert theirs in refused.json()["detail"]


def test_a_composition_refuses_what_it_cannot_draw_with_a_reason(client):
    project_id, run_id = _http_project_with_analysis(client)
    visual_id = client.post(f"/api/projects/{project_id}/visuals",
                            json={"analysis_run_id": run_id}).json()["visual_id"]
    base = f"/api/projects/{project_id}/figures/compose"

    twice = client.post(base, json={"visual_ids": [visual_id, visual_id]})
    assert twice.status_code == 409 and "twice" in twice.json()["detail"]
    unknown = client.post(base, json={"visual_ids": [visual_id], "format": "gif"})
    assert unknown.status_code == 400 and "Supported" in unknown.json()["detail"]
    clear_eps = client.post(base, json={"visual_ids": [visual_id], "format": "eps",
                                        "transparent": True})
    assert clear_eps.status_code == 400
    empty = client.post(base, json={"visual_ids": []})
    assert empty.status_code == 422


def test_the_format_warning_is_available_before_the_file_is(client):
    """
    The render step exists so the interface can warn *before* the download. A
    warning that arrives with the file has already lost.
    """
    project_id, run_id = _http_project_with_analysis(client)
    visual_id = client.post(f"/api/projects/{project_id}/visuals",
                            json={"analysis_run_id": run_id}).json()["visual_id"]

    lossy = client.post(f"/api/visuals/{visual_id}/render?format=jpeg&height=600")
    assert lossy.status_code == 200, lossy.text
    assert "lossy" in (lossy.json()["warning"] or "").lower()

    vector = client.post(f"/api/visuals/{visual_id}/render?format=pdf")
    assert vector.status_code == 200, vector.text
    assert vector.json()["warning"] is None


def test_a_pixel_height_is_refused_for_a_vector_rather_than_ignored(client):
    """
    An SVG has no pixel size. Honouring the request in name only leaves the
    caller believing the file is 1080 tall.
    """
    project_id, run_id = _http_project_with_analysis(client)
    visual_id = client.post(f"/api/projects/{project_id}/visuals",
                            json={"analysis_run_id": run_id}).json()["visual_id"]

    refused = client.get(f"/api/visuals/{visual_id}/download?format=svg&height=1080")
    assert refused.status_code == 400, refused.text


def test_a_figure_for_an_unfinished_analysis_is_refused_with_a_reason(client):
    """The export button's first call, on a run that has nothing to draw yet."""
    project_id, _ = _http_project_with_analysis(client)
    sources = client.get(f"/api/projects/{project_id}/sources").json()
    version_id = sources[0]["dataset"]["dataset_version_id"]
    queued = client.post(f"/api/projects/{project_id}/analyses", json={
        "method": "pearson_correlation",
        "dataset_version_ids": [version_id],
        "variables": {"x": "consumption_ddd", "y": "resistance_pct"},
    }).json()["analysis_run_id"]
    # Deliberately not drained: the run is still queued.

    refused = client.post(f"/api/projects/{project_id}/visuals",
                          json={"analysis_run_id": queued})
    assert refused.status_code == 409, refused.text
    assert "completed" in refused.json()["detail"]


def test_every_runtime_model_has_a_visual_recommendation():
    """The scientific runtime and figure recommender must not drift apart."""
    logistic = visual_recommend.recommend(
        analysis_run_id="arun_logit",
        method="logistic_regression",
        dataset_version_id="dsv_1",
        variables={"outcome": "event", "predictors": ["dose", "age"]},
        result={
            "sample_size": 120,
            "p_value": 0.01,
            "effect_size": {"name": "pseudo_r_squared", "value": 0.2},
            "extra": {
                "coefficients": {
                    "const": {"odds_ratio": 0.5, "ci_low": 0.2, "ci_high": 1.1},
                    "dose": {"odds_ratio": 2.0, "ci_low": 1.2, "ci_high": 3.3},
                    "age": {"odds_ratio": 1.1, "ci_low": 1.0, "ci_high": 1.2},
                }
            },
        },
    )
    mixed = visual_recommend.recommend(
        analysis_run_id="arun_mixed",
        method="mixed_model",
        dataset_version_id="dsv_1",
        variables={"outcome": "score", "predictors": ["dose", "age"], "group": "site"},
        result={
            "sample_size": 120,
            "p_value": 0.01,
            "effect_size": {"name": "intraclass_correlation", "value": 0.2},
            "extra": {
                "coefficients": {
                    "const": {"estimate": 3.0, "ci_low": 2.0, "ci_high": 4.0},
                    "dose": {"estimate": 0.8, "ci_low": 0.4, "ci_high": 1.2},
                    "age": {"estimate": -0.1, "ci_low": -0.2, "ci_high": 0.0},
                }
            },
        },
    )

    assert logistic["visual_type"] is VisualType.FOREST
    assert mixed["visual_type"] is VisualType.FOREST
    logistic_null = next(
        a.value for a in logistic["spec"].annotations if a.kind == "reference_line")
    mixed_null = next(
        a.value for a in mixed["spec"].annotations if a.kind == "reference_line")
    assert logistic_null == 1.0
    assert mixed_null == 0.0

    logistic_data = visual_prepare.prepare(
        logistic["spec"], analysis_result={
            "sample_size": 120,
            "extra": {
                "coefficients": {
                    "const": {"odds_ratio": 0.5, "ci_low": 0.2, "ci_high": 1.1},
                    "dose": {"odds_ratio": 2.0, "ci_low": 1.2, "ci_high": 3.3},
                    "age": {"odds_ratio": 1.1, "ci_low": 1.0, "ci_high": 1.2},
                }
            },
        })
    assert logistic_data.y_values == [2.0, 1.1]

    web_chart = web.render(logistic["spec"], logistic_data)
    reference = web_chart["layer"][0]["encoding"]["x"]["datum"]
    assert reference == 1.0


def test_contingency_preparation_keeps_x_horizontal_and_y_vertical():
    recommendation = visual_recommend.recommend(
        analysis_run_id="arun_chi",
        method="chi_square",
        dataset_version_id="dsv_1",
        variables={"x": "exposure", "y": "outcome"},
        result={
            "sample_size": 10,
            "p_value": 0.2,
            "effect_size": {"name": "cramers_v", "value": 0.1},
            # pandas crosstab(x, y).to_dict(): outer keys are y.
            "extra": {
                "table": {
                    "case": {"no": 1, "yes": 4},
                    "control": {"no": 3, "yes": 2},
                }
            },
        },
    )
    data = visual_prepare.prepare(
        recommendation["spec"],
        analysis_result={
            "sample_size": 10,
            "extra": {
                "table": {
                    "case": {"no": 1, "yes": 4},
                    "control": {"no": 3, "yes": 2},
                }
            },
        },
    )

    assert data.categories == ["no", "yes"]          # x / exposure
    assert data.group_values == ["case", "control"]  # y / outcome
    assert data.matrix == [[1.0, 4.0], [3.0, 2.0]]


def test_publication_export_keeps_the_sampling_caveat(tmp_path):
    spec = ResearchVisualSpec(
        visual_type=VisualType.SCATTER,
        analysis_run_id="arun_sampled",
        x=Encoding(field="x", label="x"),
        y=Encoding(field="y", label="y"),
        title="Sampled figure",
        caption="Recorded relationship.",
    )
    data = VisualData(
        x_values=[1.0, 2.0],
        y_values=[2.0, 4.0],
        sample_size=1000,
        note="Points shown are a bounded complete-case sample; all statistics come from the full analysis run.",
    )
    path = publication.render(spec, data, path=tmp_path / "sampled.svg", fmt="svg")
    body = path.read_text(encoding="utf-8")
    assert "bounded complete-case sample" in body
    assert "full analysis run" in body


def test_filtered_run_carries_its_population_into_the_visual_spec(analysed):
    project_id, version_id, _ = analysed
    with connection() as conn, conn.cursor() as cur:
        created = analysis.create_spec(
            cur,
            project_id=project_id,
            spec={
                "method": "pearson_correlation",
                "dataset_version_ids": [version_id],
                "variables": {"x": "consumption_ddd", "y": "resistance_pct"},
                "filters": [{
                    "column": "country", "operator": "eq", "value": "IND",
                }],
            },
            actor="test",
        )
        run_id = analysis.create_run(
            cur, project_id=project_id, spec_id=created["spec_id"])
        workflow.enqueue(
            cur, workflow_name="analysis.run", project_id=project_id,
            payload={"analysis_run_id": run_id},
            idempotency_key=f"analysis:{run_id}",
        )
    _drain()

    with connection() as conn, conn.cursor() as cur:
        recommendation = visuals.recommend_for_run(
            cur, analysis_run_id=run_id)

    assert recommendation["spec"].filters == [
        {"column": "country", "operator": "eq", "value": "IND"}
    ]


def test_hexbin_preparation_produces_cells_instead_of_refusing():
    spec = ResearchVisualSpec(
        visual_type=VisualType.HEXBIN,
        analysis_run_id="arun_hex",
        x=Encoding(field="x", label="x"),
        y=Encoding(field="y", label="y"),
        bin_count=12,
    )
    sample = {
        "x": list(range(500)),
        "y": [float(v * 2) for v in range(500)],
    }
    data = visual_prepare.prepare(
        spec,
        analysis_result={"sample_size": 10_000},
        sample=sample,
    )

    assert data.series
    assert sum(int(cell["count"]) for cell in data.series) == 500
    assert all({"x", "y", "count", "x_step", "y_step"} <= set(cell)
               for cell in data.series)


def test_visual_creation_refuses_unrelated_dataset_columns(analysed):
    project_id, _, runs = analysed
    with connection() as conn, conn.cursor() as cur:
        recommendation = visuals.recommend_for_run(
            cur, analysis_run_id=runs["correlation"])
        wrong = recommendation["spec"].model_copy(
            update={"x": Encoding(field="gdp_per_capita", label="GDP")}
        )
        with pytest.raises(visuals.VisualError, match="axes do not match"):
            visuals.create_visual(
                cur, project_id=project_id, spec=wrong, actor="test",
                recommendation=recommendation,
            )


def test_simple_regression_visual_cannot_swap_predictor_and_outcome(analysed):
    project_id, version_id, _ = analysed
    with connection() as conn, conn.cursor() as cur:
        created = analysis.create_spec(
            cur,
            project_id=project_id,
            spec={
                "method": "linear_regression",
                "dataset_version_ids": [version_id],
                "variables": {
                    "outcome": "resistance_pct",
                    "predictors": ["consumption_ddd"],
                },
            },
            actor="test",
        )
        run_id = analysis.create_run(
            cur, project_id=project_id, spec_id=created["spec_id"])
        workflow.enqueue(
            cur, workflow_name="analysis.run", project_id=project_id,
            payload={"analysis_run_id": run_id},
            idempotency_key=f"analysis:{run_id}",
        )
    _drain()

    with connection() as conn, conn.cursor() as cur:
        recommendation = visuals.recommend_for_run(
            cur, analysis_run_id=run_id)
        swapped = recommendation["spec"].model_copy(update={
            "x": Encoding(field="resistance_pct", label="resistance"),
            "y": Encoding(field="consumption_ddd", label="consumption"),
        })
        with pytest.raises(visuals.VisualError, match="recorded predictor"):
            visuals.create_visual(
                cur, project_id=project_id, spec=swapped, actor="test",
                recommendation=recommendation,
            )


def test_saved_visual_cannot_change_chart_type_without_repreparing_data(analysed):
    project_id, _, runs = analysed
    with connection() as conn, conn.cursor() as cur:
        recommendation = visuals.recommend_for_run(
            cur, analysis_run_id=runs["groups"])
        run = analysis.get_run(cur, runs["groups"])
        sample = _sample_for(cur, run, ["country", "resistance_pct"])
        created = visuals.create_visual(
            cur, project_id=project_id, spec=recommendation["spec"],
            actor="test", sample=sample, recommendation=recommendation,
        )
        with pytest.raises(visuals.EditRequiresRecomputation, match="visual_type"):
            visuals.apply_edit(
                cur, visual_id=created["visual_id"], actor="test",
                changes={"visual_type": "histogram"},
            )
