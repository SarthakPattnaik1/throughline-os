"""Several figures made into one, with their numbers under each panel (T192).

A multi-panel figure was made by exporting charts one at a time and pasting
them together elsewhere, where sizes drifted and the numbers lived only in the
caption — so a reader could not see when two measures of one result disagree.
These pin what the composition says: every number is the recorded one, and
every disagreement it names is real and only real.
"""

from __future__ import annotations

import pytest

from throughline_visual.renderers import compose, publication
from throughline_visual.spec import Encoding, ResearchVisualSpec, VisualData, VisualType


def _panel(estimate=0.5, low=0.2, high=0.8, p=0.001, name="mean_difference",
           y="recall", x="arm", visual_type=VisualType.BAR, **stats):
    spec = ResearchVisualSpec(
        visual_type=visual_type, analysis_run_id="run", title="Panel",
        x=Encoding(field=x, label=x.title()), y=Encoding(field=y, label=y.title()))
    data = VisualData(
        categories=["a", "b"], y_values=[1.0, 2.0],
        statistics={"estimate": estimate, "estimate_name": name, "ci_low": low,
                    "ci_high": high, "p_value": p, "sample_size": 80,
                    "confidence_level": 0.95, **stats})
    return spec, data


# --- the numbers under a panel -------------------------------------------------


def test_a_panels_line_carries_its_recorded_numbers_and_nothing_invented():
    line = compose.metrics_line({
        "estimate": 0.614, "estimate_name": "pearson_r", "ci_low": 0.48,
        "ci_high": 0.71, "p_value": 2e-9, "effect_size": 0.614,
        "effect_size_name": "r", "practical_significance": "large",
        "sample_size": 120, "confidence_level": 0.99})
    assert line == ("pearson r = 0.61 [0.48, 0.71] (99% CI) · p < 0.001 · "
                    "r = 0.61 (large) · n = 120")


def test_a_number_the_run_did_not_record_is_left_out_not_dashed():
    assert compose.metrics_line({"estimate": None, "p_value": 0.2}) == "p = 0.200"
    assert compose.metrics_line({}) == ""


# --- where the numbers disagree ------------------------------------------------


def test_significant_with_an_interval_through_no_effect_is_named():
    notes = compose.disagreements([_panel(low=-0.1, high=0.9, p=0.03)])
    assert notes == ["A: p is below 0.05, but the interval [-0.10, 0.90] "
                     "includes 0, no effect."]


def test_an_interval_that_excludes_no_effect_with_p_above_alpha_is_named():
    notes = compose.disagreements([_panel(low=0.1, high=0.9, p=0.2)])
    assert notes and "excludes 0, but p is not below 0.05" in notes[0]


def test_a_ratio_is_judged_against_one_not_zero():
    """An odds ratio of 1.8 [1.2, 2.6] with p = 0.004 agrees with itself."""
    agreeing = _panel(estimate=1.8, low=1.2, high=2.6, p=0.004, name="odds_ratio[dose]")
    assert compose.disagreements([agreeing]) == []
    crossing = _panel(estimate=1.8, low=0.9, high=2.6, p=0.004, name="odds_ratio[dose]")
    assert "includes 1" in compose.disagreements([crossing])[0]


def test_alpha_is_the_runs_own_not_assumed():
    """At a 99% level, p = 0.03 is not significant, and its 99% interval crossing
    zero is agreement, not a contradiction."""
    panel = _panel(low=-0.1, high=0.9, p=0.03, confidence_level=0.99)
    assert compose.disagreements([panel]) == []


def test_significant_and_negligible_is_named():
    panel = _panel(practical_significance="negligible")
    assert compose.disagreements([panel]) == [
        "A: statistically significant, but the effect size is negligible."]
    assert compose.disagreements([_panel(practical_significance="moderate")]) == []


def test_the_same_question_answered_both_ways_is_named():
    up = _panel(estimate=0.5, low=0.2, high=0.8, name="beta[sleep]", x="sleep")
    down = _panel(estimate=-0.4, low=-0.7, high=-0.1, name="beta[sleep]", x="sleep")
    assert compose.disagreements([up, down]) == [
        "A, B: the same estimate for Recall points in opposite directions."]


def test_a_difference_between_groups_is_never_compared_by_sign():
    """Its sign is which group was taken from which: +6 and -6 may be the same
    finding written the other way round. Saying they disagree would be false."""
    up = _panel(estimate=6.0, low=2.0, high=10.0)
    down = _panel(estimate=-6.0, low=-10.0, high=-2.0)
    assert compose.disagreements([up, down]) == []
    medians = [_panel(estimate=3.0, low=1.0, high=5.0, name="median_difference"),
               _panel(estimate=-3.0, low=-5.0, high=-1.0, name="median_difference")]
    assert compose.disagreements(medians) == []


def test_different_questions_about_one_outcome_are_not_a_disagreement():
    """A correlation with sleep and a difference between arms can have opposite
    signs and contradict nothing."""
    correlation = _panel(estimate=0.5, name="pearson_r", x="sleep",
                         visual_type=VisualType.SCATTER)
    difference = _panel(estimate=-0.4, low=-0.7, high=-0.1)
    other_predictor = _panel(estimate=-0.4, low=-0.7, high=-0.1, x="dose")
    assert compose.disagreements([correlation, difference]) == []
    assert compose.disagreements([_panel(), other_predictor]) == []


def test_a_p_value_is_never_rounded_across_the_threshold():
    """p = 0.0496 to three places is 0.050, which reads as not significant."""
    assert compose.metrics_line({"p_value": 0.0496, "confidence_level": 0.95}) \
        == "p = 0.0496"
    assert compose.metrics_line({"p_value": 0.04996, "confidence_level": 0.95}) \
        == "p = 0.04996"
    # Where rounding keeps the side, three places is enough.
    assert compose.metrics_line({"p_value": 0.0504, "confidence_level": 0.95}) \
        == "p = 0.050"
    assert compose.metrics_line({"p_value": 0.0123, "confidence_level": 0.95}) \
        == "p = 0.012"


def test_an_interval_without_a_recorded_level_says_so_and_is_not_judged():
    stats = {"estimate": 0.5, "estimate_name": "pearson_r", "ci_low": -0.1,
             "ci_high": 0.9, "p_value": 0.03}
    assert "(CI, level not recorded)" in compose.metrics_line(stats)
    assert "95%" not in compose.metrics_line(stats)
    spec, data = _panel()
    data.statistics = dict(stats, practical_significance="negligible")
    # No alpha was recorded, so neither alpha check can be made honestly.
    assert compose.disagreements([(spec, data)]) == []


def test_a_bound_keeps_its_sign_and_a_large_number_its_digits():
    line = compose.metrics_line({"estimate": 1234.6, "ci_low": -0.004,
                                 "ci_high": 2500.0, "confidence_level": 0.95})
    assert "1,235 [-0.004, 2,500]" in line


def test_agreeing_panels_say_nothing():
    assert compose.disagreements([_panel(), _panel(estimate=0.6, low=0.3, high=0.9)]) == []


# --- the figure ----------------------------------------------------------------


def test_panels_are_lettered_in_reading_order_on_one_grid(tmp_path):
    drawn = compose.compose([_panel(), _panel(), _panel()], path=tmp_path / "f.svg")
    assert drawn["letters"] == ["A", "B", "C"]
    assert (drawn["rows"], drawn["columns"]) == (1, 3)
    text = (tmp_path / "f.svg").read_text()
    assert all(f">{letter}<" in text for letter in "ABC")


def test_the_disagreement_is_printed_in_the_file_not_only_returned(tmp_path):
    compose.compose([_panel(low=-0.1, high=0.9, p=0.03)], path=tmp_path / "f.svg")
    text = (tmp_path / "f.svg").read_text()
    assert "Where the numbers disagree" in text
    assert "includes 0, no effect." in text


def test_every_panels_sources_are_printed(tmp_path):
    first, second = _panel(), _panel()
    first[0].citations = ["Smith 2021"]
    second[0].citations = ["Lee 2023", "Smith 2021"]
    compose.compose([first, second], path=tmp_path / "f.svg")
    text = (tmp_path / "f.svg").read_text()
    assert "Sources: Smith 2021; Lee 2023" in text


def test_four_panels_make_two_rows_of_two(tmp_path):
    drawn = compose.compose([_panel()] * 4, path=tmp_path / "f.png", fmt="png")
    assert (drawn["rows"], drawn["columns"]) == (2, 2)


def test_a_surface_has_no_flat_panel_and_is_refused_with_a_reason(tmp_path):
    spec = ResearchVisualSpec(visual_type=VisualType.SURFACE, analysis_run_id="r")
    with pytest.raises(compose.ComposeError, match="through Blender"):
        compose.compose([_panel(), (spec, VisualData())], path=tmp_path / "f.svg")


def test_too_many_panels_is_refused_rather_than_drawn_illegibly(tmp_path):
    with pytest.raises(compose.ComposeError, match="Split it"):
        compose.compose([_panel()] * (compose.MAX_PANELS + 1), path=tmp_path / "f.svg")


def test_a_composition_is_refused_before_any_file_is_written(tmp_path):
    target = tmp_path / "nested" / "f.gif"
    with pytest.raises(publication.RenderError):
        compose.compose([_panel()], path=target, fmt="gif")
    assert not target.parent.exists()


def test_a_composed_figure_takes_the_ground_it_is_asked_for(tmp_path):
    from matplotlib import image as mpimg

    compose.compose([_panel(), _panel()], path=tmp_path / "f.png", fmt="png",
                    ground="dark", transparent=True)
    assert mpimg.imread(tmp_path / "f.png")[2, 2][3] == 0
