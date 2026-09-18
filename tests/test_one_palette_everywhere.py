"""One palette and one type scale, in every renderer (T190), on either ground (T191).

A figure was drawn in four palettes depending on where it was drawn: the web
charts' Okabe-Ito, a hand-copied six of those in another order in the
publication export, Vega-Lite's own default scheme, and one fixed Blender
blue. The same group was orange on screen and vermilion in the manuscript. The
palette now lives in `throughline_visual.tokens`; these tests fail when any
renderer, or the web's copy, stops agreeing with it.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest
from matplotlib import image as mpimg

from throughline_visual import tokens
from throughline_visual.renderers import blender, publication, web
from throughline_visual.spec import (
    Annotation, Encoding, ResearchVisualSpec, UncertaintyDisplay, VisualData, VisualType,
)

ROOT = Path(__file__).resolve().parents[1]
WEB_TOKENS = (ROOT / "apps/web/lib/tokens.ts").read_text()
RENDERERS = ROOT / "packages/visual-spec/src/throughline_visual/renderers"


def _ts_array(name: str) -> list[str]:
    body = re.search(rf"export const {name} = \[([^\]]*)\]", WEB_TOKENS)
    assert body, f"tokens.ts no longer exports `{name}` as an array"
    return [h.upper() for h in re.findall(r'"(#[0-9A-Fa-f]{6})"', body.group(1))]


def _ts_neutral(theme: str) -> dict[int, str]:
    block = re.search(rf"{theme}: \{{([^}}]*)\}}", WEB_TOKENS)
    assert block, f"tokens.ts has no neutral.{theme} ramp"
    return {int(k): v.upper() for k, v in
            re.findall(r'(\d+): "(#[0-9A-Fa-f]{6})"', block.group(1))}


def test_the_web_charts_and_the_renderers_hold_the_same_palette_in_the_same_order():
    assert _ts_array("categorical") == [h.upper() for h in tokens.CATEGORICAL]
    assert publication.PALETTE == list(tokens.CATEGORICAL)
    assert len(tokens.MARKERS) >= len(tokens.CATEGORICAL)


def test_a_figures_neutrals_are_the_web_themes_neutrals():
    """An exported figure on a Throughline page shows no seam."""
    for theme in ("light", "dark"):
        ramp = _ts_neutral(theme)
        neutrals = tokens.ink(theme)
        assert neutrals["ground"] == ramp[0]
        assert neutrals["ink"] == ramp[800]
        assert neutrals["muted"] == ramp[600]
        assert neutrals["faint"] == ramp[500]
        assert neutrals["grid"] == ramp[100]


def test_no_renderer_names_a_colour_of_its_own():
    """A literal hex in a renderer is a fifth palette waiting to drift.

    The one allowed exception is the heatmap's cell labels, which sit on the
    viridis cell rather than on the ground and switch between white and near
    black by the cell's own value.
    """
    allowed = {("publication.py", '"#111111"')}
    found = []
    for source in RENDERERS.glob("*.py"):
        if source.name == "geometry.py":
            continue
        for literal in re.findall(r'"#[0-9A-Fa-f]{6}"', source.read_text()):
            if (source.name, literal) not in allowed:
                found.append((source.name, literal))
    assert not found, found


@pytest.mark.parametrize("ground", tokens.GROUNDS)
def test_text_on_either_ground_is_legible(ground):
    neutrals = tokens.ink(ground)
    assert tokens.contrast(neutrals["ink"], neutrals["ground"]) >= 7
    assert tokens.contrast(neutrals["muted"], neutrals["ground"]) >= 4.5
    assert tokens.contrast(neutrals["faint"], neutrals["ground"]) >= 3


@pytest.mark.parametrize("ground", tokens.GROUNDS)
def test_no_hue_disappears_into_its_ground(ground):
    """Black is the ink of a light ground and invisible on a dark one."""
    look = publication.Look(ground=ground)
    for hue in look.palette:
        visible = tokens.contrast(hue, look.ink["ground"]) >= 1.5
        # A hue that nearly vanishes is outlined in ink rather than haloed in
        # the ground — Okabe-Ito yellow on white is 1.3:1.
        assert visible or look.edge(hue) == look.ink["ink"], (ground, hue)
    assert "#000000" not in tokens.categorical("dark")


def test_the_vega_lite_spec_draws_groups_in_the_shared_palette():
    spec, data = _scatter()
    chart = web.render(spec, data)
    assert chart["config"]["range"]["category"] == tokens.categorical("light")
    assert chart["config"]["font"].startswith("Inter")
    assert chart["config"]["axis"]["labelFontSize"] == round(tokens.TYPE["tick"] * 4 / 3, 1)


def test_the_export_sets_its_type_in_the_shared_scale_and_family():
    style = publication.style_for(publication.Look())
    assert style["font.sans-serif"][0] == "Inter"
    assert style["axes.titlesize"] == tokens.TYPE["title"]
    assert style["axes.labelsize"] == tokens.TYPE["label"]
    assert style["xtick.labelsize"] == tokens.TYPE["tick"]
    assert list(style["axes.prop_cycle"].by_key()["color"]) == tokens.categorical("light")


def test_the_blender_render_takes_its_colours_from_the_tokens():
    assert "Base Color\"].default_value = (0.25, 0.45, 0.75" not in blender.RENDER_SCRIPT
    stops = blender.scene_arguments(ground="light")
    assert stops["ramp"] == list(tokens.SEQUENTIAL_STOPS)


# --- grounds (T191) ----------------------------------------------------------


def _scatter():
    rng = np.random.default_rng(0)
    xs = list(rng.normal(0, 1, 60))
    spec = ResearchVisualSpec(
        visual_type=VisualType.SCATTER, analysis_run_id="run",
        title="Recall by sleep", x=Encoding(field="sleep", label="Sleep"),
        y=Encoding(field="recall", label="Recall"),
        group=Encoding(field="arm", label="Arm"),
        annotations=[Annotation(kind="regression_line")],
        uncertainty=UncertaintyDisplay.BAND)
    data = VisualData(x_values=xs, y_values=[v * 0.5 for v in xs],
                      group_values=["a", "b", "c"] * 20, sample_size=60)
    return spec, data


def _corner(path: Path) -> np.ndarray:
    return mpimg.imread(path)[2, 2]


def test_a_dark_export_is_drawn_on_the_dark_ground(tmp_path):
    spec, data = _scatter()
    path = publication.render(spec, data, path=tmp_path / "f.png", fmt="png",
                              ground="dark")
    expected = [int(tokens.INK["dark"]["ground"][i:i + 2], 16) / 255 for i in (1, 3, 5)]
    assert np.allclose(_corner(path)[:3], expected, atol=2 / 255)


def test_a_transparent_export_paints_nothing_behind_the_marks(tmp_path):
    spec, data = _scatter()
    path = publication.render(spec, data, path=tmp_path / "f.png", fmt="png",
                              ground="dark", transparent=True)
    pixel = _corner(path)
    assert pixel.shape[0] == 4 and pixel[3] == 0


def test_the_default_export_is_unchanged_white(tmp_path):
    spec, data = _scatter()
    path = publication.render(spec, data, path=tmp_path / "f.png", fmt="png")
    assert np.allclose(_corner(path)[:3], 1.0)


@pytest.mark.parametrize("fmt", ["eps", "jpeg"])
def test_a_transparent_ground_is_refused_where_the_format_has_no_alpha(tmp_path, fmt):
    spec, data = _scatter()
    with pytest.raises(publication.RenderError, match="no transparency"):
        publication.render(spec, data, path=tmp_path / f"f.{fmt}", fmt=fmt,
                           transparent=True)


def test_an_unknown_ground_is_refused_not_guessed(tmp_path):
    spec, data = _scatter()
    with pytest.raises(publication.RenderError, match="not a figure ground"):
        publication.render(spec, data, path=tmp_path / "f.png", fmt="png",
                           ground="sepia")


def test_an_svg_keeps_its_text_as_text(tmp_path):
    """Searchable and editable in a journal's pipeline, not outlined paths."""
    spec, data = _scatter()
    path = publication.render(spec, data, path=tmp_path / "f.svg", fmt="svg")
    assert "Recall by sleep" in path.read_text()
