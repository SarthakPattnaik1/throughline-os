"""
An EPS export draws the same figure as every other format.

The recommender's default figure for a correlation is a scatter with a fitted
line and a band about it. The band was `#333333` at 8% alpha, drawn after the
points at the same z-order, so it sat on top of them — faintly, in PNG. EPS has
no transparency at all, and matplotlib's PostScript backend draws a partially
transparent shape opaque: exported as EPS, the band became a solid dark slab
over nearly every point and the fitted line. EPS is a format journals ask for,
and choosing it produced no warning.

The band is now solid and beneath the points; the grid is solid too; and
choosing EPS says what it still cannot draw. These tests look at the figure
`render` actually builds — captured as it is saved — because what matters is
the band's colour and z-order, and PostScript text is a poor way to read that.
"""

from __future__ import annotations

import re

import numpy as np
import pytest
from matplotlib.collections import PathCollection, PolyCollection
from matplotlib.colors import to_rgba
from matplotlib.figure import Figure
from throughline_visual.prepare import prepare
from throughline_visual.recommend import recommend
from throughline_visual.renderers import publication


def _default_correlation_figure():
    rng = np.random.default_rng(7)
    x = rng.normal(25, 6, 160)
    y = 0.85 * x + rng.normal(0, 2.5, 160)
    result = {"method": "pearson_correlation", "sample_size": 160,
              "estimate": 0.9, "estimate_name": "pearson_r", "p_value": 1e-9,
              "ci_low": 0.86, "ci_high": 0.93, "extra": {}}
    spec = recommend(analysis_run_id="arun_1", dataset_version_id="dsv_1",
                     method="pearson_correlation",
                     variables={"x": "consumption", "y": "resistance"},
                     result=result)["spec"]
    data = prepare(spec, analysis_result=result,
                   sample={"consumption": x.tolist(), "resistance": y.tolist()})
    return spec, data


@pytest.fixture()
def drawn(monkeypatch, tmp_path):
    """The figure `render` builds for EPS, captured as it is saved."""
    captured: dict = {}
    original = Figure.savefig

    def keep(self, *args, **kwargs):
        captured["figure"] = self
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Figure, "savefig", keep)
    spec, data = _default_correlation_figure()
    publication.render(spec, data, path=tmp_path / "figure.eps", fmt="eps")
    axes = captured["figure"].axes[0]
    bands = [c for c in axes.collections if isinstance(c, PolyCollection)]
    points = [c for c in axes.collections if isinstance(c, PathCollection)]
    return spec, bands, points


def test_the_default_correlation_figure_carries_a_band(drawn):
    """The case is the ordinary one, not a corner: every correlation gets it."""
    spec, bands, _ = drawn
    assert any(a.kind == "regression_line" for a in spec.annotations)
    assert len(bands) == 1


def test_the_band_does_not_depend_on_transparency(drawn):
    _, bands, _ = drawn
    band = bands[0]
    assert band.get_alpha() in (None, 1, 1.0)
    red, green, blue, alpha = band.get_facecolor()[0]
    assert alpha == 1.0
    # The colour the 8% wash of #333333 made over white, within rounding — so
    # the band looks as it did in every format that had transparency.
    expected = to_rgba("#efefef")
    assert max(abs(a - b) for a, b in zip((red, green, blue), expected[:3])) < 2 / 255


def test_the_band_sits_beneath_the_points(drawn):
    """Solid is not enough. A solid band on top would hide the points too."""
    _, bands, points = drawn
    assert points, "the scatter should be there"
    assert bands[0].get_zorder() < min(p.get_zorder() for p in points)


def test_the_grid_does_not_depend_on_transparency():
    """On either ground the grid is a solid colour, never a translucent grey
    that EPS would draw at full strength (T191 moved the colour into the
    per-ground tokens)."""
    from throughline_visual import tokens

    assert publication.PUBLICATION_STYLE.get("grid.alpha", 1.0) == 1.0
    for ground in tokens.GROUNDS:
        style = publication.style_for(publication.Look(ground=ground))
        assert style["grid.alpha"] == 1.0
        assert re.fullmatch(r"#[0-9a-f]{6}", style["grid.color"].lower())
        assert style["grid.color"] == tokens.ink(ground)["grid"]


def test_choosing_eps_says_what_it_still_cannot_draw():
    warning = publication.warn_about_format("eps")
    assert warning and "transparency" in warning
    assert "PDF and SVG keep transparency" in warning
    # And nothing is said where nothing is lost.
    assert publication.warn_about_format("pdf") is None
    assert publication.warn_about_format("svg") is None
