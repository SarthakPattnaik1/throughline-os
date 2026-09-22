"""Statistical methods with assumption checks.

Every method is a pure function of a DataFrame and a validated specification. No
database, no network, no model calls — this module is imported *inside the
sandbox subprocess*, so it must not be able to reach anything.

Methods are registered by name. The registry is a whitelist: an AnalysisSpec can
only ask for a method that exists here, which is what makes "run an analysis"
safe without executing arbitrary code.
"""

from __future__ import annotations

import math
from typing import Any, Callable

import numpy as np
import pandas as pd
from scipy import stats

from throughline_schemas.words import counted
from .contract import (
    AssumptionCheck,
    EffectSize,
    StatisticalResult,
    compose_interpretation,
    describe_practical_significance,
    grade_evidence,
    significance_level,
)

MethodFn = Callable[[pd.DataFrame, dict[str, Any]], StatisticalResult]
REGISTRY: dict[str, MethodFn] = {}

#: Below this, normality tests have almost no power and their "pass" is
#: uninformative rather than reassuring.
MIN_NORMALITY_N = 8
#: Above this, they have so much power that they flag departures too small to
#: affect inference. scipy itself warns that the p-value is unreliable past 5000.
MAX_NORMALITY_N = 5000


def method(name: str) -> Callable[[MethodFn], MethodFn]:
    def register(fn: MethodFn) -> MethodFn:
        REGISTRY[name] = fn
        return fn

    return register


class AnalysisError(ValueError):
    """The data cannot support the requested analysis."""


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        raise AnalysisError(f"Column {column!r} is not present in the dataset.")
    series = pd.to_numeric(frame[column], errors="coerce")
    return series


def _paired_numeric(frame: pd.DataFrame, x: str, y: str) -> tuple[pd.Series, pd.Series, int]:
    """Listwise deletion, with the number of dropped rows reported to the caller."""
    left, right = _numeric(frame, x), _numeric(frame, y)
    mask = left.notna() & right.notna()
    dropped = int((~mask).sum())
    if int(mask.sum()) < 3:
        raise AnalysisError(
            f"Only {int(mask.sum())} complete pairs for {x!r} and {y!r}; at least 3 are needed."
        )
    return left[mask], right[mask], dropped


def _normality(series: pd.Series, label: str) -> AssumptionCheck:
    n = len(series)
    if n < MIN_NORMALITY_N:
        return AssumptionCheck(
            name=f"normality[{label}]", outcome="not_testable",
            description="Shapiro-Wilk test of normality",
            detail=f"n = {n} is too small for a meaningful normality test.",
            severity="serious",
        )
    if n > MAX_NORMALITY_N:
        # At large n, Shapiro-Wilk rejects departures too small to matter, while
        # the central limit theorem makes the mean's sampling distribution
        # approximately normal anyway. Reporting "violated" here would be
        # technically true and scientifically misleading.
        return AssumptionCheck(
            name=f"normality[{label}]", outcome="not_applicable",
            description="Shapiro-Wilk test of normality",
            detail=(f"n = {n}. Formal normality testing is uninformative at this size; "
                    "the central limit theorem covers inference about the mean. "
                    f"Sample skew = {float(series.skew()):.3f}."),
            severity="informational",
        )
    statistic, p = stats.shapiro(series.to_numpy())
    return AssumptionCheck(
        name=f"normality[{label}]",
        outcome="passed" if p >= 0.05 else "violated",
        description="Shapiro-Wilk test of normality",
        statistic=float(statistic), p_value=float(p),
        detail=("Consistent with a normal distribution." if p >= 0.05
                else "Departs from normality; consider a rank-based method."),
        severity="serious",
    )


def _outliers(series: pd.Series, label: str) -> AssumptionCheck:
    """Report influential points rather than removing them."""
    q1, q3 = np.percentile(series, [25, 75])
    iqr = q3 - q1
    if iqr == 0:
        return AssumptionCheck(name=f"outliers[{label}]", outcome="not_applicable",
                               description="Interquartile-range outlier scan",
                               detail="No spread in this variable.")
    count = int(((series < q1 - 1.5 * iqr) | (series > q3 + 1.5 * iqr)).sum())
    return AssumptionCheck(
        name=f"outliers[{label}]",
        outcome="passed" if count == 0 else "violated",
        description="Interquartile-range outlier scan",
        statistic=float(count),
        detail=(f"{counted(count, 'point')} beyond 1.5×IQR. They are reported, not removed — "
                "excluding them is a transformation and must be explicit."
                if count else "No points beyond 1.5×IQR."),
        severity="informational",
    )


def _finalise(result: StatisticalResult) -> StatisticalResult:
    """Apply the  judgements that every method shares."""
    if result.p_value is not None:
        result.statistically_significant = result.p_value < significance_level(
            result.confidence_level)
    result.practical_significance = describe_practical_significance(result.effect_size)
    result.evidence_quality = grade_evidence(
        sample_size=result.sample_size, assumptions=result.assumptions,
        p_value=result.p_value, effect=result.effect_size,
    )
    result.interpretation = compose_interpretation(result)
    return result


# ---------------------------------------------------------------------------
# Descriptive
# ---------------------------------------------------------------------------


@method("descriptive")
def descriptive(frame: pd.DataFrame, spec: dict[str, Any]) -> StatisticalResult:
    columns = spec["variables"].get("columns") or []
    if not columns:
        raise AnalysisError("descriptive requires at least one column in variables.columns")

    summary: dict[str, Any] = {}
    total_n = 0
    for column in columns:
        series = _numeric(frame, column).dropna()
        total_n = max(total_n, len(series))
        if series.empty:
            summary[column] = {"n": 0, "note": "no numeric values"}
            continue
        summary[column] = {
            "n": int(len(series)),
            "missing": int(len(frame) - len(series)),
            "mean": float(series.mean()),
            "std": float(series.std(ddof=1)) if len(series) > 1 else 0.0,
            "min": float(series.min()),
            "p25": float(series.quantile(0.25)),
            "median": float(series.median()),
            "p75": float(series.quantile(0.75)),
            "max": float(series.max()),
            "skew": float(series.skew()) if len(series) > 2 else None,
        }

    return _finalise(StatisticalResult(
        method="descriptive",
        method_rationale=spec.get("method_rationale") or "Summarise distributions before testing.",
        sample_size=total_n,
        confidence_level=spec.get("confidence_level", 0.95),
        extra={"columns": summary},
        limitations=["Descriptive only. No hypothesis was tested."],
    ))


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------


def _correlation(frame: pd.DataFrame, spec: dict[str, Any], kind: str) -> StatisticalResult:
    x_name, y_name = spec["variables"]["x"], spec["variables"]["y"]
    x, y, dropped = _paired_numeric(frame, x_name, y_name)
    n = len(x)
    confidence = spec.get("confidence_level", 0.95)

    if kind == "pearson":
        r, p = stats.pearsonr(x, y)
        effect_name = "pearson_r"
    else:
        r, p = stats.spearmanr(x, y)
        effect_name = "spearman_rho"
    r, p = float(r), float(p)

    # Fisher z confidence interval; undefined at |r| = 1 or n <= 3.
    ci_low = ci_high = None
    if n > 3 and abs(r) < 1:
        z = math.atanh(r)
        se = 1 / math.sqrt(n - 3)
        crit = stats.norm.ppf(1 - (1 - confidence) / 2)
        ci_low, ci_high = math.tanh(z - crit * se), math.tanh(z + crit * se)

    checks = [_outliers(x, x_name), _outliers(y, y_name)]
    if kind == "pearson":
        checks += [_normality(x, x_name), _normality(y, y_name)]
    else:
        checks.append(AssumptionCheck(
            name="monotonicity", outcome="not_testable",
            description="Spearman assumes a monotonic relationship",
            detail="Inspect the scatter plot; monotonicity is not formally tested.",
        ))

    limitations = ["Correlation is association, not causation."]
    if dropped:
        limitations.append(f"{counted(dropped, 'row')} dropped for missing values in either variable.")

    result = StatisticalResult(
        method=f"{kind}_correlation",
        method_rationale=spec.get("method_rationale")
            or ("Pearson assumes linear association between two continuous variables."
                if kind == "pearson"
                else "Spearman ranks are robust to non-normality and monotone nonlinearity."),
        sample_size=n, estimate=r, estimate_name=effect_name,
        ci_low=ci_low, ci_high=ci_high, confidence_level=confidence, p_value=p,
        effect_size=EffectSize(name=effect_name, value=r, ci_low=ci_low, ci_high=ci_high),
        assumptions=checks, limitations=limitations,
        extra={"x": x_name, "y": y_name, "dropped_rows": dropped},
    )

    # / — if Pearson's normality assumption fails, say what to run instead.
    if kind == "pearson" and any(
        c.outcome == "violated" and c.name.startswith("normality") for c in checks
    ):
        result.warnings.append(
            "Normality is violated. Spearman correlation is the appropriate alternative."
        )
    return _finalise(result)


@method("pearson_correlation")
def pearson_correlation(frame: pd.DataFrame, spec: dict[str, Any]) -> StatisticalResult:
    return _correlation(frame, spec, "pearson")


@method("spearman_correlation")
def spearman_correlation(frame: pd.DataFrame, spec: dict[str, Any]) -> StatisticalResult:
    return _correlation(frame, spec, "spearman")


# ---------------------------------------------------------------------------
# Regression
# ---------------------------------------------------------------------------


@method("linear_regression")
def linear_regression(frame: pd.DataFrame, spec: dict[str, Any]) -> StatisticalResult:
    import statsmodels.api as sm
    from statsmodels.stats.diagnostic import het_breuschpagan
    from statsmodels.stats.stattools import durbin_watson

    outcome_name = spec["variables"]["outcome"]
    predictor_names = list(spec["variables"].get("predictors") or [])
    if not predictor_names:
        raise AnalysisError("linear_regression requires variables.predictors")

    columns = [outcome_name, *predictor_names]
    numeric = pd.DataFrame({c: _numeric(frame, c) for c in columns}).dropna()
    dropped = len(frame) - len(numeric)
    if len(numeric) <= len(predictor_names) + 1:
        raise AnalysisError(
            f"{len(numeric)} complete rows cannot fit "
            f"{counted(len(predictor_names), 'predictor')}."
        )

    y = numeric[outcome_name]
    X = sm.add_constant(numeric[predictor_names], has_constant="add")
    model = sm.OLS(y, X).fit()
    confidence = spec.get("confidence_level", 0.95)
    intervals = model.conf_int(alpha=1 - confidence)

    coefficients = {
        name: {
            "estimate": float(model.params[name]),
            "std_error": float(model.bse[name]),
            "t": float(model.tvalues[name]),
            "p_value": float(model.pvalues[name]),
            "ci_low": float(intervals.loc[name, 0]),
            "ci_high": float(intervals.loc[name, 1]),
        }
        for name in X.columns
    }

    residuals = model.resid
    checks = [_normality(pd.Series(residuals), "residuals")]
    try:
        _, bp_p, _, _ = het_breuschpagan(residuals, X)
        checks.append(AssumptionCheck(
            name="homoscedasticity", outcome="passed" if bp_p >= 0.05 else "violated",
            description="Breusch-Pagan test for constant error variance",
            p_value=float(bp_p), severity="serious",
            detail=("Error variance looks constant." if bp_p >= 0.05
                    else "Error variance changes with the fitted values; "
                         "standard errors may be wrong."),
        ))
    except Exception as exc:  # noqa: BLE001
        checks.append(AssumptionCheck(name="homoscedasticity", outcome="not_testable",
                                      description="Breusch-Pagan test", detail=str(exc)))

    dw = float(durbin_watson(residuals))
    checks.append(AssumptionCheck(
        name="independence", outcome="passed" if 1.5 <= dw <= 2.5 else "violated",
        description="Durbin-Watson test for autocorrelated residuals",
        statistic=dw, severity="serious",
        detail=f"Durbin-Watson = {dw:.3f} (≈2 indicates independence).",
    ))

    # Multicollinearity matters only with two or more predictors.
    if len(predictor_names) > 1:
        from statsmodels.stats.outliers_influence import variance_inflation_factor

        vifs = {}
        for index, name in enumerate(X.columns):
            if name == "const":
                continue
            try:
                vifs[name] = float(variance_inflation_factor(X.to_numpy(), index))
            except Exception:  # noqa: BLE001
                vifs[name] = float("nan")
        worst = max((v for v in vifs.values() if not math.isnan(v)), default=0.0)
        checks.append(AssumptionCheck(
            name="multicollinearity", outcome="passed" if worst < 5 else "violated",
            description="Variance inflation factors", statistic=worst, severity="serious",
            detail=f"Highest VIF = {worst:.2f} (>5 indicates collinear predictors). {vifs}",
        ))

    r_squared = float(model.rsquared)
    limitations = ["Regression coefficients are associations, not causal effects."]
    if dropped:
        limitations.append(f"{counted(dropped, 'row')} dropped by listwise deletion.")

    return _finalise(StatisticalResult(
        method="linear_regression",
        method_rationale=spec.get("method_rationale")
            or "Ordinary least squares for a continuous outcome.",
        sample_size=int(model.nobs),
        estimate=float(model.params[predictor_names[0]]),
        estimate_name=f"beta[{predictor_names[0]}]",
        ci_low=float(intervals.loc[predictor_names[0], 0]),
        ci_high=float(intervals.loc[predictor_names[0], 1]),
        confidence_level=confidence,
        # The predictor's own test, like the estimate and interval beside it. The
        # model's F-test answers whether anything in it explains the outcome — a
        # strong covariate makes that overwhelming for a predictor with no effect.
        p_value=coefficients[predictor_names[0]]["p_value"],
        test_statistic=coefficients[predictor_names[0]]["t"],
        degrees_of_freedom=float(model.df_resid),
        effect_size=EffectSize(name="r_squared", value=r_squared,
                               interpretation="proportion of variance explained"),
        assumptions=checks,
        adjustments=[f"Adjusted for: {', '.join(predictor_names[1:])}"]
                    if len(predictor_names) > 1 else [],
        limitations=limitations,
        extra={
            "outcome": outcome_name, "predictors": predictor_names,
            "coefficients": coefficients, "r_squared": r_squared,
            "adjusted_r_squared": float(model.rsquared_adj), "dropped_rows": dropped,
            "model_f": float(model.fvalue) if not math.isnan(model.fvalue) else None,
            "model_p_value": (float(model.f_pvalue)
                              if not math.isnan(model.f_pvalue) else None),
        },
    ))


# ---------------------------------------------------------------------------
# Group comparison
# ---------------------------------------------------------------------------


@method("t_test")
def t_test(frame: pd.DataFrame, spec: dict[str, Any]) -> StatisticalResult:
    value_name = spec["variables"]["value"]
    group_name = spec["variables"]["group"]
    if group_name not in frame.columns:
        raise AnalysisError(f"Column {group_name!r} is not present in the dataset.")

    values = _numeric(frame, value_name)
    working = pd.DataFrame({"value": values, "group": frame[group_name]}).dropna()
    working["group"] = working["group"].astype(str)
    groups = sorted(working["group"].unique())
    if len(groups) != 2:
        raise AnalysisError(
            f"t_test needs exactly 2 groups in {group_name!r}; found {len(groups)}."
        )

    a = working.loc[working["group"] == groups[0], "value"]
    b = working.loc[working["group"] == groups[1], "value"]
    if len(a) < 2 or len(b) < 2:
        raise AnalysisError("Each group needs at least 2 observations.")

    levene_stat, levene_p = stats.levene(a, b)
    equal_variance = bool(levene_p >= 0.05)
    # Welch's correction is the default when variances differ — using Student's
    # anyway would understate the standard error.
    statistic, p = stats.ttest_ind(a, b, equal_var=equal_variance)

    pooled = math.sqrt(
        ((len(a) - 1) * a.var(ddof=1) + (len(b) - 1) * b.var(ddof=1))
        / max(1, len(a) + len(b) - 2)
    )
    d = float((a.mean() - b.mean()) / pooled) if pooled > 0 else 0.0

    checks = [
        AssumptionCheck(
            name="equal_variance", outcome="passed" if equal_variance else "violated",
            description="Levene's test for equality of variances",
            statistic=float(levene_stat), p_value=float(levene_p), severity="serious",
            detail=("Variances are comparable; Student's t-test used."
                    if equal_variance else
                    "Variances differ; Welch's correction applied automatically."),
        ),
        _normality(a, str(groups[0])),
        _normality(b, str(groups[1])),
    ]

    result = StatisticalResult(
        method="welch_t_test" if not equal_variance else "student_t_test",
        method_rationale=spec.get("method_rationale")
            or "Compare the means of two independent groups.",
        sample_size=int(len(a) + len(b)),
        estimate=float(a.mean() - b.mean()), estimate_name="mean_difference",
        confidence_level=spec.get("confidence_level", 0.95),
        p_value=float(p), test_statistic=float(statistic),
        effect_size=EffectSize(name="cohens_d", value=d),
        assumptions=checks,
        adjustments=["Welch's correction for unequal variances"] if not equal_variance else [],
        extra={
            "groups": {str(groups[0]): {"n": int(len(a)), "mean": float(a.mean()),
                                        "std": float(a.std(ddof=1))},
                       str(groups[1]): {"n": int(len(b)), "mean": float(b.mean()),
                                        "std": float(b.std(ddof=1))}},
        },
    )
    if any(c.outcome == "violated" and c.name.startswith("normality") for c in checks):
        result.warnings.append(
            "Normality is violated. Mann-Whitney U is the appropriate alternative."
        )
    return _finalise(result)


@method("mann_whitney")
def mann_whitney(frame: pd.DataFrame, spec: dict[str, Any]) -> StatisticalResult:
    value_name, group_name = spec["variables"]["value"], spec["variables"]["group"]
    values = _numeric(frame, value_name)
    working = pd.DataFrame({"value": values, "group": frame[group_name]}).dropna()
    working["group"] = working["group"].astype(str)
    groups = sorted(working["group"].unique())
    if len(groups) != 2:
        raise AnalysisError(f"mann_whitney needs exactly 2 groups; found {len(groups)}.")

    a = working.loc[working["group"] == groups[0], "value"]
    b = working.loc[working["group"] == groups[1], "value"]
    statistic, p = stats.mannwhitneyu(a, b, alternative="two-sided")
    # Rank-biserial correlation, the effect size natural to this test.
    # scipy's U counts the pairs the first group wins (ties as half), so
    # 2U/(n1 n2) - 1 is P(a > b) - P(b > a): positive when a is larger, the
    # same direction as the median difference reported beside it.
    rank_biserial = float((2 * statistic) / (len(a) * len(b)) - 1)

    return _finalise(StatisticalResult(
        method="mann_whitney_u",
        method_rationale=spec.get("method_rationale")
            or "Rank-based comparison of two groups; makes no normality assumption.",
        sample_size=int(len(a) + len(b)),
        estimate=float(a.median() - b.median()), estimate_name="median_difference",
        confidence_level=spec.get("confidence_level", 0.95),
        p_value=float(p), test_statistic=float(statistic),
        effect_size=EffectSize(name="rank_biserial", value=rank_biserial),
        assumptions=[AssumptionCheck(
            name="independent_observations", outcome="not_testable",
            description="Observations must be independent",
            detail="Independence follows from the study design, not from the data.",
        )],
        extra={"groups": {str(groups[0]): int(len(a)), str(groups[1]): int(len(b))}},
    ))


@method("chi_square")
def chi_square(frame: pd.DataFrame, spec: dict[str, Any]) -> StatisticalResult:
    x_name, y_name = spec["variables"]["x"], spec["variables"]["y"]
    for name in (x_name, y_name):
        if name not in frame.columns:
            raise AnalysisError(f"Column {name!r} is not present in the dataset.")

    working = frame[[x_name, y_name]].dropna()
    table = pd.crosstab(
        working[x_name].astype(str),
        working[y_name].astype(str),
    )
    if table.size == 0 or table.shape[0] < 2 or table.shape[1] < 2:
        raise AnalysisError("chi_square needs at least a 2×2 contingency table.")

    statistic, p, dof, expected = stats.chi2_contingency(table)
    n = int(table.to_numpy().sum())
    min_expected = float(expected.min())
    # Cramér's V is defined on the uncorrected statistic. The test keeps scipy's
    # Yates correction for a 2×2 table; V taken from it was understated.
    uncorrected = float(stats.chi2_contingency(table, correction=False)[0])
    cramers_v = float(math.sqrt((uncorrected / n) / (min(table.shape) - 1)))

    return _finalise(StatisticalResult(
        method="chi_square_independence",
        method_rationale=spec.get("method_rationale")
            or "Test association between two categorical variables.",
        sample_size=n, p_value=float(p), test_statistic=float(statistic),
        degrees_of_freedom=float(dof),
        confidence_level=spec.get("confidence_level", 0.95),
        effect_size=EffectSize(name="cramers_v", value=cramers_v),
        assumptions=[AssumptionCheck(
            name="expected_cell_counts",
            outcome="passed" if min_expected >= 5 else "violated",
            description="Every expected cell count should be at least 5",
            statistic=min_expected, severity="serious",
            detail=(f"Smallest expected count is {min_expected:.2f}."
                    + ("" if min_expected >= 5 else " Fisher's exact test is more appropriate.")),
        )],
        extra={"table": table.to_dict(), "x": x_name, "y": y_name},
    ))


@method("anova")
def anova(frame: pd.DataFrame, spec: dict[str, Any]) -> StatisticalResult:
    value_name, group_name = spec["variables"]["value"], spec["variables"]["group"]
    values = _numeric(frame, value_name)
    working = pd.DataFrame({"value": values, "group": frame[group_name]}).dropna()
    working["group"] = working["group"].astype(str)
    groups = [g["value"] for _, g in working.groupby("group")]
    if len(groups) < 3:
        raise AnalysisError(
            f"anova needs at least 3 groups; found {len(groups)}. Use t_test for two."
        )

    statistic, p = stats.f_oneway(*groups)
    grand_mean = working["value"].mean()
    ss_between = sum(len(g) * (g.mean() - grand_mean) ** 2 for g in groups)
    ss_total = float(((working["value"] - grand_mean) ** 2).sum())
    eta_squared = float(ss_between / ss_total) if ss_total > 0 else 0.0

    levene_stat, levene_p = stats.levene(*groups)
    return _finalise(StatisticalResult(
        method="one_way_anova",
        method_rationale=spec.get("method_rationale")
            or "Compare means across three or more independent groups.",
        sample_size=int(len(working)),
        confidence_level=spec.get("confidence_level", 0.95),
        p_value=float(p), test_statistic=float(statistic),
        effect_size=EffectSize(name="eta_squared", value=eta_squared),
        assumptions=[
            AssumptionCheck(
                name="homogeneity_of_variance",
                outcome="passed" if levene_p >= 0.05 else "violated",
                description="Levene's test across groups",
                statistic=float(levene_stat), p_value=float(levene_p), severity="serious",
                detail=("Group variances are comparable." if levene_p >= 0.05
                        else "Group variances differ; Kruskal-Wallis is more appropriate."),
            ),
            *[_normality(g, f"group{i}") for i, g in enumerate(groups)],
        ],
        limitations=["A significant ANOVA says the groups differ, not which ones."],
        extra={"group_count": len(groups),
               "group_sizes": [int(len(g)) for g in groups]},
    ))


@method("kruskal_wallis")
def kruskal_wallis(frame: pd.DataFrame, spec: dict[str, Any]) -> StatisticalResult:
    value_name, group_name = spec["variables"]["value"], spec["variables"]["group"]
    values = _numeric(frame, value_name)
    working = pd.DataFrame({"value": values, "group": frame[group_name]}).dropna()
    working["group"] = working["group"].astype(str)
    groups = [g["value"] for _, g in working.groupby("group")]
    if len(groups) < 3:
        raise AnalysisError(f"kruskal_wallis needs at least 3 groups; found {len(groups)}.")

    statistic, p = stats.kruskal(*groups)
    n = int(len(working))
    # Epsilon-squared is H / (n - 1). (H - k + 1)/(n - k) is eta-squared-H, a
    # different quantity that was reported under this name.
    epsilon_squared = float(statistic / (n - 1)) if n > 1 else 0.0

    return _finalise(StatisticalResult(
        method="kruskal_wallis",
        method_rationale=spec.get("method_rationale")
            or "Rank-based comparison across three or more groups; no normality assumption.",
        sample_size=n, confidence_level=spec.get("confidence_level", 0.95),
        p_value=float(p), test_statistic=float(statistic),
        degrees_of_freedom=float(len(groups) - 1),
        effect_size=EffectSize(name="epsilon_squared", value=epsilon_squared),
        assumptions=[AssumptionCheck(
            name="independent_observations", outcome="not_testable",
            description="Observations must be independent",
            detail="Independence follows from the study design, not from the data.",
        )],
        extra={"group_count": len(groups)},
    ))


@method("logistic_regression")
def logistic_regression(frame: pd.DataFrame, spec: dict[str, Any]) -> StatisticalResult:
    """A binary outcome against one or more predictors (§62).

    §62 names logistic regression and it was absent, which mattered more than
    the gap in a list: a researcher whose outcome is *did this happen* had no
    method at all, and the nearest available one — linear regression — fits a
    line through zeroes and ones and predicts probabilities above one.

    **The estimate is an odds ratio, not a coefficient.** `exp(beta)` is what a
    reader can act on: "the odds are 2.3 times higher per unit". Reporting the
    raw log-odds as the headline invites it to be read as a probability
    difference, which it is not, and the confidence interval is exponentiated
    with it so the two agree.

    **A perfectly separated outcome is refused rather than reported.** When a
    predictor divides the outcome exactly, the maximum-likelihood estimate does
    not exist: the coefficient runs to infinity and the fitter stops wherever
    its iteration limit is. statsmodels returns numbers for this, and they look
    like an enormous effect with a wide interval rather than like a model that
    has no answer.
    """
    import numpy as np
    import statsmodels.api as sm

    outcome_name = spec["variables"]["outcome"]
    predictor_names = list(spec["variables"].get("predictors") or [])
    if not predictor_names:
        raise AnalysisError("logistic_regression requires variables.predictors")

    columns = [outcome_name, *predictor_names]
    numeric = pd.DataFrame({c: _numeric(frame, c) for c in columns}).dropna()
    dropped = len(frame) - len(numeric)

    outcome = numeric[outcome_name]
    levels = sorted(outcome.unique())
    if len(levels) != 2:
        raise AnalysisError(
            f"{outcome_name} takes {counted(len(levels), 'value')}; logistic "
            "regression needs an outcome that is one of exactly two things.")
    # Whichever pair of values was recorded, the higher is "it happened".
    y = (outcome == levels[1]).astype(float)

    if len(numeric) <= len(predictor_names) + 1:
        raise AnalysisError(
            f"{len(numeric)} complete rows cannot fit "
            f"{counted(len(predictor_names), 'predictor')}.")

    X = sm.add_constant(numeric[predictor_names], has_constant="add")
    try:
        model = sm.Logit(y, X).fit(disp=False)
    except Exception as exc:  # noqa: BLE001
        raise AnalysisError(f"The model would not fit: {exc}") from exc

    fitted = model.predict()
    converged = bool(getattr(model, "mle_retvals", {}).get("converged", True))
    complete_separation = bool(np.all((fitted < 1e-6) | (fitted > 1 - 1e-6)))
    if not np.all(np.isfinite(model.params.to_numpy())) or complete_separation \
            or not converged:
        # Checked against the fit rather than the coefficients, because
        # statsmodels returns *finite* numbers here: a perfectly separated
        # ten-row frame yields an odds ratio of 1.2e31, which passes an
        # is-finite test and reads as an enormous effect. The optimiser having
        # given up, and every fitted probability sitting at 0 or 1, are what
        # actually say the estimate does not exist.
        raise AnalysisError(
            "A predictor separates the outcome perfectly, so there is no "
            "maximum-likelihood estimate to report — the coefficient runs to "
            "infinity and a fitter stops wherever its iteration limit is. "
            "Reported as a number it would read as an enormous effect.")

    confidence = spec.get("confidence_level", 0.95)
    intervals = model.conf_int(alpha=1 - confidence)
    first = predictor_names[0]

    coefficients = {
        name: {
            "log_odds": float(model.params[name]),
            "odds_ratio": float(np.exp(model.params[name])),
            "std_error": float(model.bse[name]),
            "z": float(model.tvalues[name]),
            "p_value": float(model.pvalues[name]),
            "ci_low": float(np.exp(intervals.loc[name, 0])),
            "ci_high": float(np.exp(intervals.loc[name, 1])),
        }
        for name in X.columns
    }

    # Ten events per predictor is the usual rule of thumb; below it the
    # coefficients are biased away from zero and the intervals are optimistic.
    events = int(min(y.sum(), len(y) - y.sum()))
    per_predictor = events / max(len(predictor_names), 1)
    checks = [AssumptionCheck(
        name="events_per_predictor",
        outcome="passed" if per_predictor >= 10 else "violated",
        description="At least ten of the rarer outcome per predictor",
        statistic=float(per_predictor), severity="serious",
        detail=(f"{counted(events, 'observation')} of the rarer outcome for "
                f"{counted(len(predictor_names), 'predictor')}. "
                + ("Enough to estimate stably."
                   if per_predictor >= 10
                   else "Coefficients are biased away from zero at this size, "
                        "and the intervals are narrower than they should be.")),
    ), AssumptionCheck(
        name="independent_observations", outcome="not_testable",
        description="Observations must be independent",
        detail="Independence follows from the study design, not from the data.",
    )]

    # Partial separation: extreme somewhere but not everywhere, so an estimate
    # exists and is merely unstable. Reported rather than refused.
    separated = bool((fitted > 0.999).any() or (fitted < 0.001).any())
    checks.append(AssumptionCheck(
        name="separation", outcome="violated" if separated else "passed",
        description="No predictor divides the outcome almost perfectly",
        severity="serious",
        detail=("Some rows are predicted almost exactly, which makes the "
                "coefficients unstable even where the fit converged."
                if separated else
                "No fitted probability sits at the boundary."),
    ))

    limitations = ["An odds ratio is an association, not a causal effect.",
                   "Odds ratios overstate risk ratios when the outcome is common."]
    if dropped:
        limitations.append(f"{counted(dropped, 'row')} dropped by listwise deletion.")

    return _finalise(StatisticalResult(
        method="logistic_regression",
        method_rationale=spec.get("method_rationale")
            or "Logistic regression for an outcome that is one of two things.",
        sample_size=int(model.nobs),
        estimate=float(np.exp(model.params[first])),
        estimate_name=f"odds_ratio[{first}]",
        ci_low=float(np.exp(intervals.loc[first, 0])),
        ci_high=float(np.exp(intervals.loc[first, 1])),
        confidence_level=confidence,
        p_value=float(model.pvalues[first]),
        test_statistic=float(model.tvalues[first]),
        degrees_of_freedom=float(model.df_resid),
        effect_size=EffectSize(
            name="pseudo_r_squared", value=float(model.prsquared),
            interpretation="McFadden's pseudo R², not comparable to an R²"),
        assumptions=checks,
        adjustments=[f"Adjusted for: {', '.join(predictor_names[1:])}"]
                    if len(predictor_names) > 1 else [],
        limitations=limitations,
        extra={
            "outcome": outcome_name, "outcome_levels": [str(v) for v in levels],
            "modelled_as_event": str(levels[1]),
            "predictors": predictor_names, "coefficients": coefficients,
            "events": events, "dropped_rows": dropped,
            "log_likelihood": float(model.llf),
        },
    ))


@method("mixed_model")
def mixed_model(frame: pd.DataFrame, spec: dict[str, Any]) -> StatisticalResult:
    """A predictor's effect where the rows are not independent (§62).

    §62 names mixed models, and their absence was the more consequential half
    of that gap. Repeated measures on the same subject, pupils inside schools,
    readings from one instrument — the ordinary shapes of research data — break
    the independence every other method here assumes. Fitting them with OLS
    does not fail; it reports a confidence interval that is too narrow and a
    p-value that is too small, which is worse than refusing.

    **The grouping variable is required and never guessed.** Which rows belong
    together is a fact about the study design, not something inferable from a
    column, and a wrong guess produces a confident answer to a different
    question.

    `intraclass_correlation` reports how much of the variance the grouping
    accounts for. Near zero means the grouping was not needed; high means
    ignoring it would have been the mistake.
    """
    import numpy as np
    import statsmodels.api as sm

    outcome_name = spec["variables"]["outcome"]
    predictor_names = list(spec["variables"].get("predictors") or [])
    group_name = spec["variables"].get("group")
    if not predictor_names:
        raise AnalysisError("mixed_model requires variables.predictors")
    if not group_name:
        raise AnalysisError(
            "mixed_model requires variables.group — which rows belong "
            "together is a fact about the design and cannot be inferred.")

    numeric = pd.DataFrame({
        **{c: _numeric(frame, c) for c in [outcome_name, *predictor_names]},
        group_name: frame[group_name],
    }).dropna()
    dropped = len(frame) - len(numeric)

    groups = numeric[group_name].astype(str)
    distinct = int(groups.nunique())
    if distinct < 2:
        raise AnalysisError(
            f"{group_name} has {counted(distinct, 'group')}; a mixed model "
            "needs several, or there is nothing for it to model.")
    if len(numeric) <= len(predictor_names) + distinct:
        raise AnalysisError(
            f"{len(numeric)} complete rows cannot fit "
            f"{counted(len(predictor_names), 'predictor')} across "
            f"{counted(distinct, 'group')}.")

    y = numeric[outcome_name]
    X = sm.add_constant(numeric[predictor_names], has_constant="add")
    try:
        model = sm.MixedLM(y, X, groups=groups).fit()
    except Exception as exc:  # noqa: BLE001
        raise AnalysisError(f"The model would not fit: {exc}") from exc

    confidence = spec.get("confidence_level", 0.95)
    intervals = model.conf_int(alpha=1 - confidence)
    first = predictor_names[0]

    coefficients = {
        name: {
            "estimate": float(model.params[name]),
            "std_error": float(model.bse[name]),
            "z": float(model.tvalues[name]),
            "p_value": float(model.pvalues[name]),
            "ci_low": float(intervals.loc[name, 0]),
            "ci_high": float(intervals.loc[name, 1]),
        }
        for name in X.columns if name in model.params.index
    }

    between = float(model.cov_re.iloc[0, 0]) if model.cov_re.size else 0.0
    within = float(model.scale)
    icc = between / (between + within) if (between + within) > 0 else 0.0

    checks = [AssumptionCheck(
        name="grouping_is_needed", outcome="passed" if icc >= 0.01 else "violated",
        description="The grouping accounts for some of the variance",
        statistic=float(icc), severity="minor",
        detail=(f"Intraclass correlation {icc:.3f}: the grouping explains "
                f"{icc * 100:.1f}% of the variance. "
                + ("Ignoring it would have narrowed the interval wrongly."
                   if icc >= 0.01 else
                   "Close to zero — an ordinary regression would have said "
                   "much the same thing, and is easier to read.")),
    ), AssumptionCheck(
        name="enough_groups", outcome="passed" if distinct >= 5 else "violated",
        description="Enough groups to estimate a variance between them",
        statistic=float(distinct), severity="serious",
        detail=(f"{counted(distinct, 'group')}. "
                + ("Enough to estimate the between-group variance."
                   if distinct >= 5 else
                   "Below about five, that variance is barely identified and "
                   "the standard errors should not be trusted.")),
    ), _normality(pd.Series(model.resid), "residuals")]

    limitations = ["Coefficients are associations within the model, not causal effects.",
                   f"Rows are treated as independent only within {group_name}."]
    if dropped:
        limitations.append(f"{counted(dropped, 'row')} dropped by listwise deletion.")

    return _finalise(StatisticalResult(
        method="mixed_model",
        method_rationale=spec.get("method_rationale")
            or f"A random intercept for {group_name}, because rows within one "
               "are not independent.",
        sample_size=int(model.nobs),
        estimate=float(model.params[first]),
        estimate_name=f"beta[{first}]",
        ci_low=float(intervals.loc[first, 0]),
        ci_high=float(intervals.loc[first, 1]),
        confidence_level=confidence,
        p_value=float(model.pvalues[first]),
        test_statistic=float(model.tvalues[first]),
        degrees_of_freedom=float(distinct - 1),
        effect_size=EffectSize(
            name="intraclass_correlation", value=float(icc),
            interpretation="share of variance between groups"),
        assumptions=checks,
        adjustments=[f"Adjusted for: {', '.join(predictor_names[1:])}"]
                    if len(predictor_names) > 1 else [],
        limitations=limitations,
        extra={
            "outcome": outcome_name, "predictors": predictor_names,
            "group": group_name, "groups": distinct,
            "coefficients": coefficients,
            "variance_between": between, "variance_within": within,
            "intraclass_correlation": float(icc), "dropped_rows": dropped,
        },
    ))


def available_methods() -> list[str]:
    return sorted(REGISTRY)


@method("bootstrap_correlation")
def bootstrap_correlation(frame: pd.DataFrame, spec: dict[str, Any]) -> StatisticalResult:
    """Resample the association to see whether it is an artefact of a few rows.

    Reports the fraction of resamples keeping the observed sign and a percentile
    interval. Stability is the question here, not significance: a correlation
    that flips sign in a fifth of resamples is not a finding whatever its
    p-value says.
    """
    x_name, y_name = spec["variables"]["x"], spec["variables"]["y"]
    x, y, dropped = _paired_numeric(frame, x_name, y_name)
    n = len(x)
    iterations = int(spec.get("parameters", {}).get("iterations", 2000))
    kind = str(spec.get("parameters", {}).get("correlation", "pearson"))
    confidence = spec.get("confidence_level", 0.95)

    correlate = stats.pearsonr if kind == "pearson" else stats.spearmanr
    observed = float(correlate(x, y)[0])

    # Seeded so a rerun reproduces the interval exactly.
    rng = np.random.default_rng(int(spec.get("random_seed", 0)))
    values = np.empty(iterations, dtype=float)
    xv, yv = x.to_numpy(), y.to_numpy()
    for i in range(iterations):
        idx = rng.integers(0, n, n)
        # A resample can be degenerate (all identical rows); those are undefined,
        # not zero, and are excluded rather than counted as a sign flip.
        with np.errstate(invalid="ignore"):
            r = correlate(xv[idx], yv[idx])[0]
        values[i] = r
    usable = values[~np.isnan(values)]
    if usable.size < iterations * 0.5:
        raise AnalysisError("Too many degenerate resamples to assess stability.")

    lower = float(np.percentile(usable, 100 * (1 - confidence) / 2))
    upper = float(np.percentile(usable, 100 * (1 - (1 - confidence) / 2)))
    sign_agreement = float(np.mean(np.sign(usable) == np.sign(observed))) if observed != 0 else 0.0
    excludes_zero = bool((lower > 0 and upper > 0) or (lower < 0 and upper < 0))

    stable = sign_agreement >= 0.95 and excludes_zero
    return _finalise(StatisticalResult(
        method="bootstrap_correlation",
        method_rationale=spec.get("method_rationale")
            or "Resampling shows whether the association depends on a few observations.",
        sample_size=n, estimate=observed, estimate_name=f"{kind}_r",
        ci_low=lower, ci_high=upper, confidence_level=confidence,
        effect_size=EffectSize(name="pearson_r" if kind == "pearson" else "spearman_rho",
                               value=observed, ci_low=lower, ci_high=upper),
        assumptions=[AssumptionCheck(
            name="bootstrap_stability", outcome="passed" if stable else "violated",
            description="Sign agreement and percentile interval across resamples",
            statistic=sign_agreement, severity="serious",
            detail=(f"{sign_agreement:.1%} of {usable.size} resamples kept the observed sign; "
                    f"{confidence:.0%} interval [{lower:.4f}, {upper:.4f}] "
                    f"{'excludes' if excludes_zero else 'includes'} zero."),
        )],
        limitations=["Bootstrap assesses stability, not correctness of the model."],
        extra={"iterations": int(usable.size), "sign_agreement": sign_agreement,
               "excludes_zero": excludes_zero, "stable": stable,
               "dropped_rows": dropped, "correlation": kind},
    ))
