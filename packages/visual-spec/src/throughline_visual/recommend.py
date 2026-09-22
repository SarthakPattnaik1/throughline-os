"""Visual intelligence — the researcher should not need to know chart names.

Input: an analysis result, the variables it used, and what the figure is for.
Output: a recommended visual, the reason, a ResearchVisualSpec, a caption, an
interpretation and the alternatives that were considered.

The recommendation is deterministic and rule-based. That is a deliberate choice:
chart selection follows from the analysis method and the variable types, both of
which the system already knows exactly. Asking a language model to guess would
add nondeterminism and a fabrication risk to a decision that has a correct
answer.
"""

from __future__ import annotations

from typing import Any, Mapping

from .labels import LabelBook
from .spec import (
    Annotation,
    Encoding,
    ResearchVisualSpec,
    Scale,
    UncertaintyDisplay,
    VisualType,
)


class RecommendationError(ValueError):
    pass


#: The book a caller gets when it supplies none: every label falls back to the
#: humanised column name, which is exactly the behaviour that existed before
#: labels were plumbed through. Shared because `LabelBook` has no mutators.
_NO_LABELS = LabelBook()


def recommend(
    *,
    analysis_run_id: str,
    method: str,
    variables: dict[str, Any],
    result: dict[str, Any],
    dataset_version_id: str | None = None,
    goal: str = "show the relationship",
    audience: str = "researcher",
    labels: LabelBook | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Choose a figure for an analysis result.

    `labels` maps each raw column name to what a reader should see instead —
    normally built from the canonical variable layer by the caller that holds a
    database cursor. Omitting it is supported and produces the humanised raw
    name, which is the only label available when nothing else is known.
    """
    book = LabelBook.coerce(labels)
    builders = {
        "pearson_correlation": _correlation,
        "spearman_correlation": _correlation,
        "bootstrap_correlation": _correlation,
        "linear_regression": _regression,
        "logistic_regression": _logistic_regression,
        "mixed_model": _mixed_model,
        "t_test": _group_comparison,
        "mann_whitney": _group_comparison,
        "anova": _group_comparison,
        "kruskal_wallis": _group_comparison,
        "chi_square": _contingency,
        "descriptive": _descriptive,
    }
    builder = builders.get(method)
    if builder is None:
        raise RecommendationError(
            f"No visualization is defined for {method!r}. "
            f"Supported: {', '.join(sorted(builders))}"
        )
    recommendation = builder(analysis_run_id, dataset_version_id, variables, result,
                             audience, book)
    recommendation["goal"] = goal
    recommendation["audience"] = audience
    return recommendation


def _statistic_label(name: str | None) -> str:
    """Reader-facing notation for stored statistic identifiers."""
    key = (name or "").strip().lower()
    if key == "pearson_r":
        return "r"
    if key in {"spearman_rho", "spearman_r"}:
        return "ρ"
    if key.startswith("kendall"):
        return "τ"
    return key.replace("_", " ") or "effect"


def _significance_note(result: dict[str, Any]) -> str:
    """A caption fragment that keeps stored numbers but not internal identifiers."""
    parts: list[str] = []
    if result.get("p_value") is not None:
        parts.append(f"p = {result['p_value']:.3g}")
    effect = result.get("effect_size") or {}
    if effect.get("value") is not None:
        parts.append(
            f"{_statistic_label(effect.get('name'))} = {effect['value']:.3g}"
        )
    if result.get("sample_size"):
        parts.append(f"n = {result['sample_size']}")
    if result.get("evidence_quality"):
        parts.append(f"evidence: {result['evidence_quality']}")
    return "; ".join(parts)


def _correlation_interval_note(result: dict[str, Any]) -> str:
    """The run-level interval for r/ρ, stated in text rather than drawn as a y-band."""
    low, high = result.get("ci_low"), result.get("ci_high")
    if low is None or high is None:
        return ""
    level = float(result.get("confidence_level") or 0.95)
    percentage = round(level * 100)
    return f"{percentage}% CI [{float(low):.3g}, {float(high):.3g}]"


#: Sample size beyond which one mark per observation stops being readable.
#:
#: Overplotting has no threshold of its own — a scatter degrades continuously and
#: never announces it. This is the point where, at publication figure sizes, the
#: dense region of a typical correlation goes solid and the reader can no longer
#: tell fifty points from five thousand. Below it a scatter is strictly better,
#: because it shows every observation; above it the scatter is showing ink.
OVERPLOTTING_THRESHOLD = 5_000

def _estimate_interval_note(result: dict[str, Any]) -> str:
    """The recorded headline estimate and its interval, in reader-facing notation."""
    estimate = result.get("estimate")
    if estimate is None:
        return "estimate not recorded"
    name = _statistic_label(result.get("estimate_name"))
    text = f"{name} = {float(estimate):.3g}"
    low, high = result.get("ci_low"), result.get("ci_high")
    if low is not None and high is not None:
        level = round(float(result.get("confidence_level") or 0.95) * 100)
        text += f"; {level}% CI [{float(low):.3g}, {float(high):.3g}]"
    return text



def _correlation(run_id, version_id, variables, result, audience,
                 book=_NO_LABELS) -> dict[str, Any]:
    x_name, y_name = variables["x"], variables["y"]
    has_ci = result.get("ci_low") is not None
    sample_size = int(result.get("sample_size") or 0)

    if sample_size >= OVERPLOTTING_THRESHOLD:
        return _binned_correlation(run_id, version_id, x_name, y_name,
                                   result, sample_size, book)

    spec = ResearchVisualSpec(
        visual_type=VisualType.SCATTER,
        analysis_run_id=run_id, dataset_version_id=version_id,
        x=book.encoding(x_name),
        y=book.encoding(y_name),
        # A confidence interval for r/ρ is an interval on the statistic, not a
        # y-axis band around a fitted line. Correlation does not fit a regression
        # model, so drawing a line or residual band would add an analysis that
        # was never recorded. The interval is stated in the caption instead.
        uncertainty=UncertaintyDisplay.NONE,
        annotations=[],
        title=f"{book.label(y_name)} against {book.label(x_name)}",
        #  — the caption must not imply causation from a correlation.
        caption=(f"Association between {book.described(x_name)} and "
                 f"{book.described(y_name)}. {_significance_note(result)}"
                 f"{'; ' + _correlation_interval_note(result) if has_ci else ''}. "
                 "Association does not establish causation."),
        interaction=["hover", "brush", "underlying_table"],
    )
    return {
        "visual_type": VisualType.SCATTER,
        "reason": ("A scatter plot shows every observation, so the reader can judge the "
                   "shape of the relationship and see outliers rather than trusting a "
                   "single coefficient."),
        "spec": spec,
        "interpretation": result.get("interpretation", ""),
        "alternatives": [
            {"visual_type": VisualType.HEATMAP,
             "when": "more than two variables are being compared at once"},
            {"visual_type": VisualType.BOX,
             "when": "one variable is better treated as categorical"},
        ],
    }


def _binned_correlation(run_id, version_id, x_name, y_name, result,
                        sample_size, book=_NO_LABELS) -> dict[str, Any]:
    """The same relationship, at a sample size where marks would overplot.

    Not a downgrade of the scatter. At this many rows a scatter answers "is
    there ink here" rather than "how much data is here", and the two questions
    have visibly different answers in the middle of a dense cloud.
    """
    bins = 30
    spec = ResearchVisualSpec(
        visual_type=VisualType.HEXBIN,
        analysis_run_id=run_id, dataset_version_id=version_id,
        x=book.encoding(x_name),
        y=book.encoding(y_name),
        bin_count=bins,
        annotations=[],
        title=f"{book.label(y_name)} against {book.label(x_name)}",
        caption=(f"Association between {book.described(x_name)} and "
                 f"{book.described(y_name)} across an analysis of "
                 f"{sample_size:,} observations. A bounded uniform sample is "
                 f"binned into {bins} hexagonal cells per axis; shade shows how "
                 f"many sampled observations fall in each cell, on a logarithmic "
                 f"scale — binned counts are heavy-tailed, and a linear ramp "
                 f"would collapse everything outside the densest cells into one shade. "
                 f"{_significance_note(result)}"
                 f"{'; ' + _correlation_interval_note(result) if result.get('ci_low') is not None else ''}. "
                 f"Association does not establish causation."),
        interaction=["hover", "brush", "underlying_table"],
    )
    return {
        "visual_type": VisualType.HEXBIN,
        "reason": (f"{sample_size:,} observations would overplot as a scatter — "
                   f"the dense region fills in and a reader cannot tell where "
                   f"most of the data lies. Binning shades each cell by how many "
                   f"observations it holds, so density stays visible."),
        "spec": spec,
        "interpretation": result.get("interpretation", ""),
        "alternatives": [
            {"visual_type": VisualType.SCATTER,
             "when": "the individual observations matter more than their density, "
                     "such as when hunting outliers"},
            {"visual_type": VisualType.BOX,
             "when": "one variable is better treated as categorical"},
        ],
    }


def _regression(run_id, version_id, variables, result, audience,
                book=_NO_LABELS) -> dict[str, Any]:
    predictors = list(variables.get("predictors") or [])
    outcome = variables["outcome"]
    multiple = len(predictors) > 1
    if multiple:
        # A coefficient plot is the honest view of a multivariable model: it
        # shows every adjusted estimate with its interval side by side.
        spec = ResearchVisualSpec(
            visual_type=VisualType.FOREST,
            analysis_run_id=run_id, dataset_version_id=version_id,
            x=Encoding(field="estimate", label="coefficient (95% CI)", include_zero=True),
            y=Encoding(field="predictor", label="predictor"),
            # The y axis of a forest plot is a list of column names. Without
            # these, that axis is the one place a reader still meets the raw
            # schema — the encodings above describe the estimate, not the rows.
            category_labels=book.category_labels(predictors),
            uncertainty=UncertaintyDisplay.CONFIDENCE_INTERVAL,
            annotations=[Annotation(kind="reference_line", value=0.0,
                                    orientation="vertical", text="no effect")],
            title=f"Adjusted associations with {book.label(outcome)}",
            caption=(f"Coefficients from a multiple regression of "
                     f"{book.described(outcome)} on {book.joined(predictors)}. "
                     f"{_significance_note(result)}. "
                     "Intervals crossing zero are compatible with no effect."),
        )
        reason = ("With several predictors, a coefficient plot shows each adjusted "
                  "estimate and its interval together, which a scatter plot cannot.")
        alternatives = [{"visual_type": VisualType.SCATTER,
                         "when": "you want to show one predictor's raw relationship"}]
        # §10 admits three dimensions where the data has three, and a fitted
        # response over exactly two predictors is that case: z = f(x, y) is a
        # surface, not a chart made to look like one. Offered rather than
        # chosen — a coefficient plot answers "which predictors matter" more
        # legibly than any surface, and this answers a different question,
        # what the model's shape is. With three predictors a surface is a
        # slice, and a slice presented whole is the misreading §10 exists to
        # prevent.
        if len(predictors) == 2:
            alternatives.append({
                "visual_type": VisualType.SURFACE,
                "when": "you want the shape of the fitted response across both "
                        "predictors, rather than each coefficient on its own",
            })
    else:
        spec = ResearchVisualSpec(
            visual_type=VisualType.SCATTER,
            analysis_run_id=run_id, dataset_version_id=version_id,
            x=book.encoding(predictors[0]),
            y=book.encoding(outcome),
            # The coefficient interval is an interval on the slope, not a
            # vertical band around the fitted line. Draw the recorded fit and
            # state its interval in text rather than inventing a prediction band.
            uncertainty=UncertaintyDisplay.NONE,
            annotations=[Annotation(kind="regression_line",
                                    text="fitted line from recorded coefficients")],
            title=f"{book.label(outcome)} against {book.label(predictors[0])}",
            caption=(f"Simple linear regression of {book.described(outcome)} on "
                     f"{book.described(predictors[0])}. "
                     f"{_estimate_interval_note(result)}; {_significance_note(result)}. "
                     "Association does not establish causation."),
        )
        reason = ("A single predictor is best shown as a scatter plot with the fitted "
                  "line, so the reader sees the data behind the coefficient.")
        alternatives = [{"visual_type": VisualType.FOREST,
                         "when": "predictors are added and the model becomes multivariable"}]

    return {"visual_type": spec.visual_type, "reason": reason, "spec": spec,
            "interpretation": result.get("interpretation", ""), "alternatives": alternatives}


def _logistic_regression(run_id, version_id, variables, result, audience,
                         book=_NO_LABELS) -> dict[str, Any]:
    predictors = list(variables.get("predictors") or [])
    outcome = variables["outcome"]
    if not predictors:
        raise RecommendationError("logistic regression named no predictors to plot")

    spec = ResearchVisualSpec(
        visual_type=VisualType.FOREST,
        analysis_run_id=run_id,
        dataset_version_id=version_id,
        x=Encoding(field="odds_ratio", label="odds ratio (95% CI)"),
        y=Encoding(field="predictor", label="predictor"),
        category_labels=book.category_labels(predictors),
        uncertainty=UncertaintyDisplay.CONFIDENCE_INTERVAL,
        annotations=[Annotation(
            kind="reference_line", value=1.0,
            orientation="vertical", text="no association"
        )],
        title=f"Adjusted odds ratios for {book.label(outcome)}",
        caption=(
            f"Odds ratios from a logistic regression of {book.described(outcome)} "
            f"on {book.joined(predictors)}. {_significance_note(result)}. "
            "An odds ratio of 1 is compatible with no association. "
            "Association does not establish causation."
        ),
    )
    return {
        "visual_type": VisualType.FOREST,
        "reason": (
            "A coefficient plot shows every adjusted odds ratio with its interval "
            "against the no-association value of 1."
        ),
        "spec": spec,
        "interpretation": result.get("interpretation", ""),
        "alternatives": [],
    }


def _mixed_model(run_id, version_id, variables, result, audience,
                 book=_NO_LABELS) -> dict[str, Any]:
    predictors = list(variables.get("predictors") or [])
    outcome = variables["outcome"]
    if not predictors:
        raise RecommendationError("mixed model named no predictors to plot")

    spec = ResearchVisualSpec(
        visual_type=VisualType.FOREST,
        analysis_run_id=run_id,
        dataset_version_id=version_id,
        x=Encoding(field="estimate", label="coefficient (95% CI)", include_zero=True),
        y=Encoding(field="predictor", label="predictor"),
        category_labels=book.category_labels(predictors),
        uncertainty=UncertaintyDisplay.CONFIDENCE_INTERVAL,
        annotations=[Annotation(
            kind="reference_line", value=0.0,
            orientation="vertical", text="no association"
        )],
        title=f"Mixed-model associations with {book.label(outcome)}",
        caption=(
            f"Mixed-model coefficients for {book.described(outcome)} across "
            f"{book.joined(predictors)}. {_significance_note(result)}. "
            "Intervals crossing zero are compatible with no association. "
            "Association does not establish causation."
        ),
    )
    return {
        "visual_type": VisualType.FOREST,
        "reason": (
            "A coefficient plot keeps the adjusted mixed-model estimates and "
            "their uncertainty visible without flattening the grouping structure "
            "into a raw scatter."
        ),
        "spec": spec,
        "interpretation": result.get("interpretation", ""),
        "alternatives": [],
    }


def _group_comparison(run_id, version_id, variables, result, audience,
                      book=_NO_LABELS) -> dict[str, Any]:
    value, group = variables["value"], variables["group"]
    groups = (result.get("extra") or {}).get("groups") or {}
    many = len(groups) > 2

    # A box plot shows the distributions being compared. A bar of means hides
    # spread and overlap, which is the single most common way a group difference
    # is overstated — so it is the alternative, not the default.
    spec = ResearchVisualSpec(
        visual_type=VisualType.BOX,
        analysis_run_id=run_id, dataset_version_id=version_id,
        x=book.encoding(group),
        y=book.encoding(value),
        group=book.encoding(group),
        uncertainty=UncertaintyDisplay.NONE,
        title=f"{book.label(value)} by {book.label(group)}",
        caption=(f"Distribution of {book.described(value)} across "
                 f"{book.described(group)}. {_significance_note(result)}. "
                 "Boxes show the median and interquartile range."),
    )
    return {
        "visual_type": VisualType.BOX,
        "reason": ("A box plot shows the spread and overlap of each group. A bar of "
                   "means would hide exactly the information needed to judge whether "
                   "the groups really differ."),
        "spec": spec,
        "interpretation": result.get("interpretation", ""),
        "alternatives": [
            {"visual_type": VisualType.BAR,
             "when": "the audience needs group means and the spread is reported elsewhere"},
            {"visual_type": VisualType.HISTOGRAM,
             "when": "the shape of a single distribution matters more than the comparison"},
        ] + ([{"visual_type": VisualType.FOREST,
               "when": "pairwise differences with intervals are the point"}] if many else []),
    }


def _contingency(run_id, version_id, variables, result, audience,
                 book=_NO_LABELS) -> dict[str, Any]:
    x_name, y_name = variables["x"], variables["y"]
    spec = ResearchVisualSpec(
        visual_type=VisualType.HEATMAP,
        analysis_run_id=run_id, dataset_version_id=version_id,
        x=book.encoding(x_name),
        y=book.encoding(y_name),
        title=f"{book.label(x_name)} by {book.label(y_name)}",
        caption=(f"Contingency table of {book.described(x_name)} against "
                 f"{book.described(y_name)}. {_significance_note(result)}."),
    )
    return {
        "visual_type": VisualType.HEATMAP,
        "reason": ("A heatmap of the contingency table shows where the association "
                   "actually sits, which a single chi-square statistic cannot."),
        "spec": spec,
        "interpretation": result.get("interpretation", ""),
        "alternatives": [{"visual_type": VisualType.BAR,
                          "when": "one variable has only two levels"}],
    }


def _descriptive(run_id, version_id, variables, result, audience,
                 book=_NO_LABELS) -> dict[str, Any]:
    columns = list(variables.get("columns") or [])
    if not columns:
        raise RecommendationError("descriptive analysis named no columns to plot")
    spec = ResearchVisualSpec(
        visual_type=VisualType.HISTOGRAM,
        analysis_run_id=run_id, dataset_version_id=version_id,
        x=book.encoding(columns[0]),
        y=Encoding(field="count", label="count", include_zero=True),
        title=f"Distribution of {book.label(columns[0])}",
        caption=(f"Distribution of {book.described(columns[0])}. "
                 f"{_significance_note(result)}."),
    )
    return {
        "visual_type": VisualType.HISTOGRAM,
        "reason": ("A histogram shows the shape of the distribution — skew, spread and "
                   "gaps — before any test is applied to it."),
        "spec": spec,
        "interpretation": result.get("interpretation", ""),
        "alternatives": [{"visual_type": VisualType.BOX,
                          "when": "several distributions are being compared"}],
    }
