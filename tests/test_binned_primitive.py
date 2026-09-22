"""P5 — binned aggregation.

The registry claimed this primitive rendered when nothing implemented it
anywhere: no component, no VisualType member, no data path. These tests cover
the build, and in particular the guard the registry itself states — that a
binned figure must say how it was binned.
"""

from __future__ import annotations

import numpy as np
import pytest
from throughline_visual.critic import DEFAULT_BIN_COUNT, critique
from throughline_visual.prepare import prepare
from throughline_visual.recommend import OVERPLOTTING_THRESHOLD
from throughline_visual.renderers.publication import render
from throughline_visual.spec import (
    BinShape, CountScale, Encoding, ResearchVisualSpec, VisualData, VisualType,
)


def _cloud(n: int) -> VisualData:
    rng = np.random.default_rng(11)
    xs = rng.normal(25, 6, n)
    ys = 0.85 * xs + rng.normal(0, 2.5, n)
    return VisualData(x_values=list(xs), y_values=list(ys))


def _spec(**overrides) -> ResearchVisualSpec:
    base = dict(
        visual_type=VisualType.HEXBIN,
        analysis_run_id="arun_test",
        x=Encoding(field="consumption_ddd", label="consumption"),
        y=Encoding(field="resistance_pct", label="resistance"),
        bin_count=30,
    )
    base.update(overrides)
    return ResearchVisualSpec(**base)


def _prepared(spec: ResearchVisualSpec, n: int) -> VisualData:
    raw = _cloud(n)
    return prepare(
        spec,
        analysis_result={"sample_size": n},
        sample={
            spec.x.field: raw.x_values,
            spec.y.field: raw.y_values,
        },
    )


# ---------------------------------------------------------------------------
# The guard: bin width is a decision, so it is published
# ---------------------------------------------------------------------------


def test_a_binned_figure_without_a_bin_count_is_refused():
    """Bin width decides how many modes a distribution appears to have.

    Widen it and two humps merge; narrow it and noise becomes structure. Both
    pictures are defensible, they disagree, and the image does not say which
    was chosen — so an unstated bin count is a blocking fault, not a default to
    fill in silently.
    """
    report = critique(_spec(bin_count=None), _cloud(20_000), autofix=False)
    finding = next(c for c in report.critiques if c.check == "bin_transparency")
    assert finding.outcome == "violated"
    assert finding.severity == "blocking"
    assert "modes" in finding.detail


def test_autofix_states_the_bin_count_rather_than_hiding_it():
    report = critique(_spec(bin_count=None), _cloud(20_000), autofix=True)
    finding = next(c for c in report.critiques if c.check == "bin_transparency")
    assert finding.outcome == "fixed"
    assert report.spec.bin_count == DEFAULT_BIN_COUNT
    # The fix has to be legible in the record, not merely applied.
    assert str(DEFAULT_BIN_COUNT) in finding.fix_applied


def test_a_stated_bin_count_passes_and_is_reported():
    report = critique(_spec(bin_count=24), _cloud(20_000), autofix=False)
    finding = next(c for c in report.critiques if c.check == "bin_transparency")
    assert finding.outcome == "passed"
    assert "24" in finding.detail


def test_charts_that_do_not_bin_are_not_asked_to_state_a_bin_count():
    report = critique(_spec(visual_type=VisualType.SCATTER, bin_count=None),
                      _cloud(100), autofix=False)
    finding = next(c for c in report.critiques if c.check == "bin_transparency")
    assert finding.outcome == "passed"


def test_the_bin_count_cannot_be_absurd():
    """Four cells is not a distribution; two hundred is one point per cell."""
    with pytest.raises(ValueError):
        _spec(bin_count=2)
    with pytest.raises(ValueError):
        _spec(bin_count=5_000)


# ---------------------------------------------------------------------------
# The recommendation: when a scatter has stopped working
# ---------------------------------------------------------------------------


def test_a_small_sample_still_gets_a_scatter():
    """Below the threshold a scatter is strictly better — it shows every row."""
    from throughline_visual.recommend import _correlation

    picked = _correlation("arun_1", "dsv_1",
                          {"x": "consumption_ddd", "y": "resistance_pct"},
                          {"sample_size": 120, "p_value": 1e-38}, "researcher")
    assert picked["visual_type"] is VisualType.SCATTER


def test_an_overplotting_sample_gets_binned_and_says_why():
    from throughline_visual.recommend import _correlation

    picked = _correlation("arun_1", "dsv_1",
                          {"x": "consumption_ddd", "y": "resistance_pct"},
                          {"sample_size": OVERPLOTTING_THRESHOLD + 1,
                           "p_value": 1e-38}, "researcher")
    assert picked["visual_type"] is VisualType.HEXBIN
    assert picked["spec"].bin_count
    # The reason names the failure being avoided, not the chart being chosen.
    assert "overplot" in picked["reason"]
    # A scatter stays available: density is not always the question.
    assert any(a["visual_type"] is VisualType.SCATTER
               for a in picked["alternatives"])


def test_the_binned_caption_states_the_binning():
    from throughline_visual.recommend import _correlation

    picked = _correlation("arun_1", "dsv_1",
                          {"x": "consumption_ddd", "y": "resistance_pct"},
                          {"sample_size": 40_000, "p_value": 1e-38}, "researcher")
    caption = picked["spec"].caption
    assert "40,000" in caption and "cells per" in caption
    #  — no causal language from a correlation.
    assert "does not establish causation" in caption


# ---------------------------------------------------------------------------
# The renderer
# ---------------------------------------------------------------------------


def test_a_binned_figure_renders_to_vector(tmp_path):
    spec = _spec(title="Resistance against consumption")
    written = render(spec, _prepared(spec, 20_000),
                     path=tmp_path / "figure.svg", fmt="svg")
    body = written.read_text(encoding="utf-8", errors="replace")
    assert "<svg" in body[:400]
    # The colour bar has to be there: without it the shading encodes nothing a
    # reader can name.
    assert "observations per cell" in body
    assert len(body) > 5_000


def test_the_colour_bar_names_a_non_linear_scale(tmp_path):
    """A log ramp that does not say so is a misread waiting to happen.

    Two cells three shades apart differ by a factor of two on a linear scale and
    by orders of magnitude on a logarithmic one. The picture is identical; only
    the label distinguishes them.
    """
    spec = _spec(count_scale=CountScale.LOG)
    written = render(spec, _prepared(spec, 20_000),
                     path=tmp_path / "log.svg", fmt="svg")
    assert "log scale" in written.read_text(encoding="utf-8", errors="replace")


def test_a_linear_scale_is_not_labelled_as_a_transform(tmp_path):
    """Only a departure from the obvious needs announcing.

    A linear ramp is what a reader already assumes, so labelling it adds noise;
    a log ramp is not, so labelling it is the whole point. Rendered and read
    back, because an earlier version of this test asserted that an enum's string
    value was "linear" — which is true, tells you nothing about the figure, and
    was named as though it verified the label.
    """
    spec = _spec(count_scale=CountScale.LINEAR)
    written = render(spec, _prepared(spec, 8_000),
                     path=tmp_path / "linear.svg", fmt="svg")
    body = written.read_text(encoding="utf-8", errors="replace")
    assert "observations per cell" in body
    assert "log scale" not in body
    assert "sqrt" not in body


# ---------------------------------------------------------------------------
# Reaching the researcher
# ---------------------------------------------------------------------------


def test_the_cells_are_counted_server_side():
    """The browser is given counts, never asked to compute them.

    Binning is aggregation, and a client that re-aggregated could disagree with
    the analysis that produced the figure (LAW 2). It is also what made the
    primitive unreachable: with points and no counts, the workspace fell back to
    a scatter — the overplotted blob this chart replaces.
    """
    from throughline_api.app import _binned_cells

    spec = _spec(bin_count=10)
    data = _prepared(spec, 5_000)
    cells = _binned_cells(spec, data)

    assert cells, "a binned recommendation must produce cells"
    # Every observation lands in exactly one cell.
    assert sum(c["count"] for c in cells) == len(data.x_values)
    assert all(c["count"] > 0 for c in cells), "empty cells are absent, not zero"
    assert all({"x", "y", "count"} == set(c) for c in cells)


def test_no_cells_are_produced_for_charts_that_do_not_bin():
    from throughline_api.app import _binned_cells

    assert _binned_cells(_spec(visual_type=VisualType.SCATTER), _cloud(100)) is None


def test_the_two_cell_shapes_bin_differently():
    """A hexagonal lattice offsets alternate rows; a square one does not."""
    from throughline_api.app import _binned_cells

    hex_spec = _spec(bin_shape=BinShape.HEX, bin_count=12)
    square_spec = _spec(bin_shape=BinShape.SQUARE, bin_count=12)
    hexes = _binned_cells(hex_spec, _prepared(hex_spec, 5_000))
    squares = _binned_cells(square_spec, _prepared(square_spec, 5_000))

    assert sum(c["count"] for c in hexes) == sum(c["count"] for c in squares)
    # The offset means the two lattices cannot land on identical centres.
    assert {(c["x"], c["y"]) for c in hexes} != {(c["x"], c["y"]) for c in squares}


def test_both_cell_shapes_render(tmp_path):
    """One primitive, two arguments — the catalogue says so, so both must work."""
    for shape in (BinShape.HEX, BinShape.SQUARE):
        spec = _spec(bin_shape=shape)
        written = render(spec, _prepared(spec, 8_000),
                         path=tmp_path / f"{shape}.svg", fmt="svg")
        body = written.read_text(encoding="utf-8", errors="replace")
        assert "<svg" in body[:400], shape
        assert len(body) > 5_000, shape


def test_the_critic_records_the_shape_and_the_scale_not_just_the_count():
    """Both decisions shape what the reader concludes, so both are on record."""
    report = critique(_spec(bin_count=24, bin_shape=BinShape.SQUARE,
                            count_scale=CountScale.LOG),
                      _cloud(20_000), autofix=False)
    detail = next(c for c in report.critiques
                  if c.check == "bin_transparency").detail
    assert "24" in detail and "square" in detail and "log" in detail


def test_the_defaults_are_the_ones_that_survive_heavy_tails():
    """Defaults matter more than options: most figures take them unexamined."""
    spec = _spec()
    assert spec.bin_shape is BinShape.HEX
    assert spec.count_scale is CountScale.LOG


def test_the_renderer_refuses_an_empty_figure(tmp_path):
    from throughline_visual.renderers.publication import RenderError

    with pytest.raises(RenderError):
        render(_spec(), VisualData(x_values=[], y_values=[]),
               path=tmp_path / "empty.svg", fmt="svg")
