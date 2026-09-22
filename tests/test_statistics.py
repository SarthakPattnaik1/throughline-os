"""Statistical correctness (§46) and the result contract (§47).

These run the methods directly rather than through the sandbox: the sandbox is
tested separately, and correctness deserves fast, focused assertions against
values that can be checked by hand or against scipy.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from scipy import stats
from throughline_runtime.contract import grade_evidence, AssumptionCheck, EffectSize
from throughline_runtime.methods import REGISTRY, AnalysisError


def run(method: str, frame: pd.DataFrame, **variables):
    return REGISTRY[method](frame, {
        "variables": variables, "confidence_level": 0.95,
        "method_rationale": "", "random_seed": 0, "filters": [],
    })


# ---------------------------------------------------------------------------
# Correctness against known answers
# ---------------------------------------------------------------------------


def test_pearson_matches_scipy_exactly():
    rng = np.random.default_rng(11)
    x = rng.normal(size=60)
    y = 2.5 * x + rng.normal(scale=0.5, size=60)
    frame = pd.DataFrame({"x": x, "y": y})

    result = run("pearson_correlation", frame, x="x", y="y")
    expected_r, expected_p = stats.pearsonr(x, y)
    assert result.estimate == pytest.approx(expected_r, rel=1e-12)
    assert result.p_value == pytest.approx(expected_p, rel=1e-12)
    # The Fisher-z interval must actually contain the estimate.
    assert result.ci_low < result.estimate < result.ci_high


def test_perfect_correlation_is_exactly_one():
    frame = pd.DataFrame({"x": [1, 2, 3, 4, 5], "y": [2, 4, 6, 8, 10]})
    result = run("pearson_correlation", frame, x="x", y="y")
    assert result.estimate == pytest.approx(1.0)
    # |r| = 1 makes the Fisher transform infinite; the CI must be omitted, not faked.
    assert result.ci_low is None and result.ci_high is None


def test_linear_regression_recovers_a_known_slope():
    rng = np.random.default_rng(7)
    x = rng.normal(size=200)
    y = 3.0 + 1.75 * x + rng.normal(scale=0.2, size=200)
    frame = pd.DataFrame({"x": x, "y": y})

    result = run("linear_regression", frame, outcome="y", predictors=["x"])
    beta = result.extra["coefficients"]["x"]["estimate"]
    assert beta == pytest.approx(1.75, abs=0.05)
    assert beta > result.extra["coefficients"]["x"]["ci_low"]
    assert beta < result.extra["coefficients"]["x"]["ci_high"]
    assert result.extra["r_squared"] > 0.95


def test_t_test_matches_scipy_and_reports_cohens_d():
    rng = np.random.default_rng(3)
    a, b = rng.normal(10, 2, 40), rng.normal(12, 2, 40)
    frame = pd.DataFrame({"value": np.concatenate([a, b]),
                          "group": ["a"] * 40 + ["b"] * 40})

    result = run("t_test", frame, value="value", group="group")
    expected = stats.ttest_ind(a, b, equal_var=True)
    assert result.p_value == pytest.approx(expected.pvalue, rel=1e-9)
    assert result.effect_size.name == "cohens_d"
    assert abs(result.effect_size.value) > 0.8  # a real 1-SD separation


def test_unequal_variance_triggers_welch_automatically():
    """Using Student's t when variances differ understates the standard error."""
    rng = np.random.default_rng(5)
    frame = pd.DataFrame({
        "value": np.concatenate([rng.normal(10, 1, 30), rng.normal(10, 8, 30)]),
        "group": ["a"] * 30 + ["b"] * 30,
    })
    result = run("t_test", frame, value="value", group="group")
    assert result.method == "welch_t_test"
    assert "Welch's correction for unequal variances" in result.adjustments


def test_missing_group_labels_are_dropped_before_group_tests():
    """A missing category must not become a literal group called "nan"."""
    frame = pd.DataFrame({
        "value": [1.0, 2.0, 3.0, 10.0, 11.0, 12.0, 999.0],
        "group": ["a", "a", "a", "b", "b", "b", None],
    })

    t = run("t_test", frame, value="value", group="group")
    mw = run("mann_whitney", frame, value="value", group="group")

    assert t.sample_size == 6
    assert mw.sample_size == 6
    assert set(t.extra["groups"]) == {"a", "b"}
    assert set(mw.extra["groups"]) == {"a", "b"}
    assert "nan" not in t.extra["groups"]


def test_missing_group_labels_are_dropped_before_multi_group_tests():
    frame = pd.DataFrame({
        "value": [1, 2, 3, 10, 11, 12, 20, 21, 22, 999],
        "group": ["a"] * 3 + ["b"] * 3 + ["c"] * 3 + [None],
    })

    anova_result = run("anova", frame, value="value", group="group")
    kruskal_result = run("kruskal_wallis", frame, value="value", group="group")

    assert anova_result.sample_size == 9
    assert kruskal_result.sample_size == 9
    assert anova_result.extra["group_count"] == 3
    assert kruskal_result.extra["group_count"] == 3


def test_chi_square_drops_rows_missing_either_category():
    frame = pd.DataFrame({
        "exposure": ["yes", "yes", "no", "no", None, "yes"],
        "outcome": ["case", "control", "case", "control", "case", None],
    })
    result = run("chi_square", frame, x="exposure", y="outcome")

    assert result.sample_size == 4
    outer = result.extra["table"]
    assert "nan" not in outer
    assert all("nan" not in inner for inner in outer.values())


def test_chi_square_matches_scipy():
    frame = pd.DataFrame({
        "exposure": ["yes"] * 60 + ["no"] * 60,
        "outcome": ["case"] * 40 + ["control"] * 20 + ["case"] * 15 + ["control"] * 45,
    })
    result = run("chi_square", frame, x="exposure", y="outcome")
    table = pd.crosstab(frame["exposure"], frame["outcome"])
    expected_stat, expected_p, _, _ = stats.chi2_contingency(table)
    assert result.test_statistic == pytest.approx(expected_stat, rel=1e-12)
    assert result.p_value == pytest.approx(expected_p, rel=1e-12)
    assert 0 <= result.effect_size.value <= 1  # Cramér's V is bounded


def test_anova_and_kruskal_agree_on_a_clear_difference():
    rng = np.random.default_rng(13)
    frame = pd.DataFrame({
        "value": np.concatenate([rng.normal(5, 1, 30), rng.normal(8, 1, 30),
                                 rng.normal(11, 1, 30)]),
        "group": ["a"] * 30 + ["b"] * 30 + ["c"] * 30,
    })
    parametric = run("anova", frame, value="value", group="group")
    rank_based = run("kruskal_wallis", frame, value="value", group="group")
    assert parametric.p_value < 0.001 and rank_based.p_value < 0.001
    assert parametric.effect_size.value > 0.5  # large eta-squared


# ---------------------------------------------------------------------------
# §47 — significance, magnitude and evidence quality stay separate
# ---------------------------------------------------------------------------


def test_a_significant_result_can_still_be_practically_negligible():
    """The heart of §47: never equate p < 0.05 with important."""
    rng = np.random.default_rng(17)
    n = 20000
    x = rng.normal(size=n)
    y = 0.03 * x + rng.normal(size=n)  # real but tiny association
    frame = pd.DataFrame({"x": x, "y": y})

    result = run("pearson_correlation", frame, x="x", y="y")
    assert result.statistically_significant is True
    assert result.practical_significance == "negligible"
    assert "p =" in result.interpretation and "effect size" in result.interpretation


def test_tiny_sample_yields_insufficient_evidence_however_small_the_p_value():
    frame = pd.DataFrame({"x": [1, 2, 3, 4], "y": [2.0, 4.1, 5.9, 8.2]})
    result = run("pearson_correlation", frame, x="x", y="y")
    assert result.p_value < 0.05
    assert result.evidence_quality == "insufficient"  # n = 4


def test_blocking_assumption_violation_makes_evidence_insufficient():
    quality = grade_evidence(
        sample_size=500,
        assumptions=[AssumptionCheck(name="x", outcome="violated", severity="blocking")],
        p_value=1e-12,
        effect=EffectSize(name="pearson_r", value=0.9),
    )
    assert quality == "insufficient"


def test_non_normal_data_is_told_which_test_to_use_instead():
    """§49/§51 — the system suggests the correct alternative, it does not switch silently."""
    rng = np.random.default_rng(23)
    skewed = rng.exponential(scale=2.0, size=80)
    frame = pd.DataFrame({"x": skewed, "y": skewed * 2 + rng.exponential(size=80)})
    result = run("pearson_correlation", frame, x="x", y="y")
    assert any("Spearman" in w for w in result.warnings)
    assert result.method == "pearson_correlation"  # nothing was swapped behind the researcher


def test_outliers_are_reported_not_removed():
    """LAW 4 — removing points is a transformation and must be explicit."""
    frame = pd.DataFrame({"x": list(range(30)) + [500], "y": list(range(30)) + [500]})
    result = run("pearson_correlation", frame, x="x", y="y")
    outlier_checks = [c for c in result.assumptions if c.name.startswith("outliers")]
    assert outlier_checks and any(c.outcome == "violated" for c in outlier_checks)
    assert result.sample_size == 31  # nothing was dropped


def test_correlation_always_carries_the_causation_limitation():
    frame = pd.DataFrame({"x": range(40), "y": [i * 2 for i in range(40)]})
    result = run("pearson_correlation", frame, x="x", y="y")
    assert any("not causation" in limitation for limitation in result.limitations)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_missing_column_is_named_in_the_error():
    frame = pd.DataFrame({"x": [1, 2, 3]})
    with pytest.raises(AnalysisError) as exc:
        run("pearson_correlation", frame, x="x", y="nope")
    assert "nope" in str(exc.value)


def test_too_few_complete_pairs_is_refused():
    frame = pd.DataFrame({"x": [1, None, None], "y": [1, 2, None]})
    with pytest.raises(AnalysisError) as exc:
        run("pearson_correlation", frame, x="x", y="y")
    assert "complete pairs" in str(exc.value)


def test_t_test_refuses_three_groups():
    frame = pd.DataFrame({"value": range(9), "group": ["a", "b", "c"] * 3})
    with pytest.raises(AnalysisError) as exc:
        run("t_test", frame, value="value", group="group")
    assert "exactly 2 groups" in str(exc.value)


def test_dropped_rows_are_reported_as_a_limitation():
    frame = pd.DataFrame({"x": [1, 2, 3, 4, None, 6], "y": [2, 4, 6, 8, 10, None]})
    result = run("pearson_correlation", frame, x="x", y="y")
    assert result.extra["dropped_rows"] == 2
    assert any("dropped" in limitation for limitation in result.limitations)


# ---------------------------------------------------------------------------
# §62: logistic regression and mixed models
# ---------------------------------------------------------------------------

def test_logistic_regression_reports_an_odds_ratio_not_a_coefficient():
    """
    `exp(beta)` is what a reader can act on. A raw log-odds invites being read
    as a probability difference, which it is not.
    """
    import numpy as np
    rng = np.random.default_rng(7)
    x = rng.normal(size=400)
    frame = pd.DataFrame({
        "x": x,
        "happened": (1.2 * x + rng.normal(scale=0.6, size=400) > 0).astype(float),
    })

    result = run("logistic_regression", frame, outcome="happened", predictors=["x"])

    assert result.estimate_name == "odds_ratio[x]"
    # A positive coefficient means odds above one, never a negative estimate.
    assert result.estimate > 1
    assert result.ci_low > 0, "an odds ratio cannot be negative"
    assert result.ci_low < result.estimate < result.ci_high


def test_logistic_regression_refuses_an_outcome_that_is_not_binary():
    frame = pd.DataFrame({"x": range(30), "outcome": [0, 1, 2] * 10})
    with pytest.raises(AnalysisError, match="exactly two things"):
        run("logistic_regression", frame, outcome="outcome", predictors=["x"])


def test_logistic_regression_refuses_a_perfectly_separated_outcome():
    """
    The estimate does not exist here — the coefficient runs to infinity and a
    fitter stops wherever its iteration limit is. Reported rather than refused,
    it reads as an enormous effect with a wide interval instead of as a model
    with no answer.
    """
    frame = pd.DataFrame({
        "x": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        "happened": [0, 0, 0, 0, 0, 1, 1, 1, 1, 1],
    })
    with pytest.raises(AnalysisError):
        run("logistic_regression", frame, outcome="happened", predictors=["x"])


def test_logistic_regression_says_how_thin_the_evidence_is():
    """Ten of the rarer outcome per predictor, or the interval is optimistic."""
    # Thin but *not* separated: the four events are scattered through the
    # range, so an estimate exists and is merely fragile. A block of ones at
    # one end would be perfect separation, which is refused outright — the
    # first version of this test made that mistake and was testing the wrong
    # refusal.
    import numpy as np
    rng = np.random.default_rng(3)
    happened = np.zeros(40)
    happened[[5, 14, 23, 31]] = 1
    frame = pd.DataFrame({"x": rng.normal(size=40), "happened": happened})
    result = run("logistic_regression", frame, outcome="happened", predictors=["x"])
    check = next(c for c in result.assumptions if c.name == "events_per_predictor")
    assert check.outcome == "violated"


def test_a_mixed_model_reduces_to_regression_when_the_grouping_carries_nothing():
    """
    The property `evals/conformance.py` promises in place of a reference.

    A random-intercept model with no variance between groups *is* ordinary
    least squares, and its coefficient must agree with one. If it did not, the
    grouping would be changing an answer it has no information about.
    """
    import numpy as np
    rng = np.random.default_rng(11)
    n = 300
    x = rng.normal(size=n)
    # Groups assigned at random, so they carry no information about y.
    frame = pd.DataFrame({
        "x": x,
        "y": 2.0 * x + rng.normal(scale=1.0, size=n),
        "site": rng.choice([f"s{i}" for i in range(8)], size=n),
    })

    mixed = run("mixed_model", frame, outcome="y", predictors=["x"], group="site")
    plain = run("linear_regression", frame, outcome="y", predictors=["x"])

    assert mixed.estimate == pytest.approx(plain.estimate, abs=0.02)
    assert mixed.extra["intraclass_correlation"] < 0.05


def test_a_mixed_model_parts_from_regression_when_the_grouping_carries_something():
    """
    The other half, and the reason the model exists.

    With a real shift per group, the between-group variance is not zero and the
    intraclass correlation says so. Fitting this with OLS would report an
    interval narrower than the data supports.
    """
    import numpy as np
    rng = np.random.default_rng(13)
    sites = [f"s{i}" for i in range(10)]
    offsets = {site: rng.normal(scale=4.0) for site in sites}
    rows = []
    for site in sites:
        for _ in range(30):
            x = rng.normal()
            rows.append({"x": x, "site": site,
                         "y": 2.0 * x + offsets[site] + rng.normal(scale=0.5)})
    frame = pd.DataFrame(rows)

    result = run("mixed_model", frame, outcome="y", predictors=["x"], group="site")

    assert result.extra["intraclass_correlation"] > 0.5
    check = next(c for c in result.assumptions if c.name == "grouping_is_needed")
    assert check.outcome == "passed"


def test_a_mixed_model_will_not_guess_the_grouping():
    """Which rows belong together is a fact about the design, not the data."""
    frame = pd.DataFrame({"x": range(20), "y": range(20)})
    with pytest.raises(AnalysisError, match="variables.group"):
        run("mixed_model", frame, outcome="y", predictors=["x"])


def test_a_mixed_model_refuses_a_single_group():
    frame = pd.DataFrame({"x": range(20), "y": range(20), "site": ["only"] * 20})
    with pytest.raises(AnalysisError, match="group"):
        run("mixed_model", frame, outcome="y", predictors=["x"], group="site")


# ---------------------------------------------------------------------------
# Effect sizes report what they are (T174)
# ---------------------------------------------------------------------------

def _two_groups(larger: str = "a"):
    rng = np.random.default_rng(3)
    high, low = rng.normal(10, 1, 40), rng.normal(8, 1, 40)
    a, b = (high, low) if larger == "a" else (low, high)
    return pd.DataFrame({"value": list(a) + list(b), "group": ["a"] * 40 + ["b"] * 40}), a, b


def test_rank_biserial_points_the_same_way_as_the_difference_it_describes():
    """
    It was `1 - 2U/(n1 n2)` with scipy's U for the first group — the number of
    pairs that group wins — which reverses the sign: a clearly larger group came
    out at about -0.92 beside a median difference and a Cohen's d that are both
    positive. One result contradicting itself on the page.
    """
    frame, a, b = _two_groups("a")
    result = run("mann_whitney", frame, value="value", group="group")

    wins = sum((x > y) + 0.5 * (x == y) for x in a for y in b)
    reference = (2 * wins) / (len(a) * len(b)) - 1  # Kerby: P(a > b) - P(b > a)

    assert result.estimate > 0
    assert result.effect_size.value > 0
    assert math.isclose(result.effect_size.value, reference, rel_tol=1e-12)


def test_rank_biserial_is_negative_when_the_first_group_is_smaller():
    frame, _, _ = _two_groups("b")
    result = run("mann_whitney", frame, value="value", group="group")
    assert result.estimate < 0 and result.effect_size.value < 0


def test_the_kruskal_wallis_effect_is_the_epsilon_squared_it_is_called():
    """`(H - k + 1)/(n - k)` is eta-squared-H. Epsilon-squared is H / (n - 1)."""
    rng = np.random.default_rng(5)
    groups = [rng.normal(m, 1, 25) for m in (0.0, 0.4, 0.9)]
    frame = pd.DataFrame({"value": np.concatenate(groups),
                          "group": ["g1"] * 25 + ["g2"] * 25 + ["g3"] * 25})
    result = run("kruskal_wallis", frame, value="value", group="group")

    h, _ = stats.kruskal(*groups)
    assert result.effect_size.name == "epsilon_squared"
    assert math.isclose(result.effect_size.value, h / (75 - 1), rel_tol=1e-12)


@pytest.mark.parametrize("name,value,expected", [
    # Proportions of variance explained: Cohen's (1988) small .01, medium .06, large .14.
    ("eta_squared", 0.005, "negligible"), ("eta_squared", 0.03, "small"),
    ("eta_squared", 0.12, "moderate"), ("eta_squared", 0.20, "large"),
    ("epsilon_squared", 0.12, "moderate"),
    # r-squared as the correlation thresholds squared: .01, .09, .25.
    ("r_squared", 0.20, "moderate"), ("r_squared", 0.30, "large"),
    # Correlation-scale measures keep .1, .3, .5.
    ("pearson_r", 0.20, "small"), ("rank_biserial", -0.35, "moderate"),
    ("cohens_d", 0.6, "moderate"),
])
def test_each_effect_size_is_judged_on_its_own_scale(name, value, expected):
    """
    eta-squared and r-squared are proportions of variance, and they were judged
    with the thresholds for a correlation coefficient — so eta-squared of 0.12,
    medium-to-large by the convention everyone quotes, was called "small", and a
    regression explaining a fifth of the variance with it. Rank-biserial and
    epsilon-squared have scales of their own and were never judged at all.
    """
    from throughline_runtime.contract import describe_practical_significance

    assert describe_practical_significance(EffectSize(name=name, value=value)) == expected


# ---------------------------------------------------------------------------
# A result's headline numbers describe the same thing (T175)
# ---------------------------------------------------------------------------

def _adjusted_frame():
    """y is driven by z; x, the predictor asked about, has no effect."""
    rng = np.random.default_rng(11)
    n = 200
    z, x = rng.normal(size=n), rng.normal(size=n)
    return pd.DataFrame({"y": 3 * z + rng.normal(size=n), "x": x, "z": z})


def test_a_regressions_p_value_is_the_predictors_it_sits_beside():
    """
    The headline estimate and interval are the first predictor's, adjusted for
    the rest; the p-value beside them was the whole model's F-test. With a
    strong covariate that read as overwhelming evidence about a predictor whose
    own interval spans zero — and the evidence grade was built on it.
    """
    result = run("linear_regression", _adjusted_frame(), outcome="y", predictors=["x", "z"])
    own = result.extra["coefficients"]["x"]

    assert result.ci_low < 0 < result.ci_high
    assert result.p_value == own["p_value"] and result.p_value > 0.05
    assert result.test_statistic == own["t"]
    # The model's test is still there, named as what it is.
    assert result.extra["model_p_value"] < 1e-10
    assert result.extra["model_f"] > 100


def test_with_one_predictor_the_two_tests_agree():
    rng = np.random.default_rng(12)
    x = rng.normal(size=80)
    frame = pd.DataFrame({"x": x, "y": 0.5 * x + rng.normal(size=80)})
    result = run("linear_regression", frame, outcome="y", predictors=["x"])
    assert math.isclose(result.p_value, result.extra["model_p_value"], rel_tol=1e-9)


def test_cramers_v_is_computed_without_the_continuity_correction():
    """
    `chi2_contingency` applies Yates' correction to a 2×2 table. That is a
    choice for the test; Cramér's V is defined on the uncorrected statistic, and
    taking the corrected one understated it (0.234 against 0.267 here).
    """
    from scipy.stats.contingency import association

    frame = pd.DataFrame({"a": ["u"] * 30 + ["v"] * 30,
                          "b": ["p"] * 20 + ["q"] * 10 + ["p"] * 12 + ["q"] * 18})
    result = run("chi_square", frame, x="a", y="b")
    table = pd.crosstab(frame["a"], frame["b"]).to_numpy()

    assert math.isclose(result.effect_size.value,
                        association(table, method="cramer"), rel_tol=1e-12)
    # The test itself keeps scipy's default.
    assert math.isclose(result.p_value, stats.chi2_contingency(table)[1], rel_tol=1e-12)


@pytest.mark.parametrize("confidence", [0.95, 0.99, 0.90])
def test_a_p_value_on_the_threshold_gets_one_verdict(confidence):
    """
    `1 - 0.95` is 0.050000000000000044. The flag was set from it while the
    sentence beside it rounded, so p = 0.05 was flagged significant and
    described as "not below the 0.05 threshold" in the same result (T176).
    """
    from throughline_runtime.contract import StatisticalResult
    from throughline_runtime.methods import _finalise

    alpha = round(1 - confidence, 10)
    result = _finalise(StatisticalResult(method="m", method_rationale="", sample_size=50,
                                         p_value=alpha, confidence_level=confidence))

    assert result.statistically_significant is False
    assert "not below" in result.interpretation
