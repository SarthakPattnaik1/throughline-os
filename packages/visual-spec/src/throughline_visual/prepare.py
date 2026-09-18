"""Turn an analysis result into chart-ready data — exactly once.

This is the seam that makes the multi-renderer architecture honest.  says the
analysis logic must not be recreated per output, and  evaluates whether a
figure faithfully represents the underlying values. Both are satisfied by having
one function build `VisualData` from the recorded run, which every renderer then
draws without recomputing anything.

A renderer that could re-aggregate could disagree with the analysis. None of them
can, because none of them ever sees the dataset.
"""

from __future__ import annotations

from typing import Any, Sequence

from .spec import ResearchVisualSpec, VisualData, VisualType


class PreparationError(ValueError):
    pass


def prepare(
    spec: ResearchVisualSpec, *, analysis_result: dict[str, Any],
    sample: dict[str, Sequence[Any]] | None = None,
) -> VisualData:
    """Build the figure's data from the recorded result, plus a bounded sample.

    ``sample`` carries the raw points a scatter or box plot needs. It is a
    *bounded* sample drawn server-side — never the whole dataset —
    and every statistic on the figure comes from ``analysis_result``, not from
    recomputing over the sample.
    """
    result = analysis_result or {}
    sample = sample or {}
    statistics = _statistics(result)

    if spec.visual_type is VisualType.SCATTER:
        return _scatter(spec, result, sample, statistics)
    if spec.visual_type is VisualType.FOREST:
        return _forest(spec, result, statistics)
    if spec.visual_type is VisualType.BOX:
        return _box(spec, result, sample, statistics)
    if spec.visual_type is VisualType.BAR:
        return _bar(spec, result, statistics)
    if spec.visual_type is VisualType.HISTOGRAM:
        return _histogram(spec, result, sample, statistics)
    if spec.visual_type is VisualType.HEATMAP:
        return _heatmap(spec, result, statistics)
    if spec.visual_type is VisualType.SURFACE:
        return _surface(spec, result, sample, statistics)
    raise PreparationError(f"No data preparation defined for {spec.visual_type}")


#: How finely the fitted surface is evaluated.
#:
#: Twenty-four squares is about the point where a plane stops looking faceted
#: and well below where the cell count starts to cost a frame. It is a drawing
#: decision, not a statistical one — the surface is a continuous function and
#: this only chooses how often it is sampled.
SURFACE_STEPS = 24


def _surface(spec: ResearchVisualSpec, result: dict[str, Any],
             sample: dict[str, Sequence[Any]],
             statistics: dict[str, Any]) -> VisualData:
    """
    The fitted response over two predictors, evaluated from the recorded
    coefficients.

    **Nothing is refitted.** The coefficients come from the run, and the
    surface is arithmetic on them — the same rule every other figure here
    follows, and the reason a picture cannot disagree with the analysis that
    produced it. A renderer that re-estimated from the sample would be drawing
    a second, quieter model beside the one in the record.

    **The grid stops where the data does.** It spans the observed range of each
    predictor and no further: a plane is defined everywhere, and drawing it
    past the rows that informed it states a prediction nobody measured. The
    renderer fades cells with no observation near them for the same reason.
    """
    extra = result.get("extra") or {}
    coefficients = extra.get("coefficients") or {}
    predictors = list(extra.get("predictors") or [])

    if len(predictors) != 2:
        raise PreparationError(
            "A surface needs exactly two predictors: with one it is a line, "
            f"and with {len(predictors)} it is a slice through a model this "
            "cannot draw whole.")

    missing = [name for name in predictors if name not in coefficients]
    if missing or "const" not in coefficients:
        raise PreparationError(
            "The recorded result carries no coefficient for "
            f"{', '.join(missing) or 'the intercept'}, so the fitted surface "
            "cannot be evaluated from it. Nothing here refits a model.")

    first, second = predictors
    xs_raw = [float(v) for v in sample.get(first, []) if _is_number(v)]
    ys_raw = [float(v) for v in sample.get(second, []) if _is_number(v)]
    if len(xs_raw) < 2 or len(ys_raw) < 2:
        raise PreparationError(
            "The sample carries too few values of "
            f"{first} and {second} to know where the surface should stop.")

    intercept = float(coefficients["const"]["estimate"])
    slope_x = float(coefficients[first]["estimate"])
    slope_y = float(coefficients[second]["estimate"])

    def axis(values: list[float]) -> list[float]:
        low, high = min(values), max(values)
        if high == low:           # a constant predictor spans nothing
            high = low + 1.0
        step = (high - low) / (SURFACE_STEPS - 1)
        return [low + step * i for i in range(SURFACE_STEPS)]

    x_axis, y_axis = axis(xs_raw), axis(ys_raw)
    grid = [[intercept + slope_x * x + slope_y * y for x in x_axis]
            for y in y_axis]

    # The observations, so the reader sees what the fit was fitted to. Paired
    # by position, and only where all three are present — a point with a
    # missing coordinate is not somewhere.
    outcome = extra.get("outcome")
    zs_raw = [v for v in sample.get(outcome or "", [])]
    observations = [
        {"x": float(x), "y": float(y), "z": float(z)}
        for x, y, z in zip(sample.get(first, []), sample.get(second, []),
                           zs_raw)
        if _is_number(x) and _is_number(y) and _is_number(z)
    ]

    return VisualData(
        x_values=list(x_axis),
        y_values=list(y_axis),
        matrix=grid,
        series=observations,
        sample_size=int(result.get("sample_size") or len(observations)),
        statistics=statistics,
        note=("The surface is the fitted model evaluated from the recorded "
              f"coefficients, over the observed range of {first} and "
              f"{second}. The points are the observations it was fitted to."),
    )


def _is_number(value: Any) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _statistics(result: dict[str, Any]) -> dict[str, Any]:
    """The numbers a figure is permitted to state, taken verbatim from the run."""
    effect = result.get("effect_size") or {}
    return {
        "estimate": result.get("estimate"),
        "estimate_name": result.get("estimate_name"),
        "p_value": result.get("p_value"),
        "ci_low": result.get("ci_low"),
        "ci_high": result.get("ci_high"),
        "effect_size": effect.get("value"),
        "effect_size_name": effect.get("name"),
        "sample_size": result.get("sample_size"),
        "evidence_quality": result.get("evidence_quality"),
        "practical_significance": result.get("practical_significance"),
        "method": result.get("method"),
        # The level the interval was computed at, so a figure that states the
        # interval states its level, and alpha is the run's rather than assumed.
        "confidence_level": result.get("confidence_level"),
    }


def _scatter(spec, result, sample, statistics) -> VisualData:
    x_field = spec.x.field if spec.x else None
    y_field = spec.y.field if spec.y else None
    xs = list(sample.get(x_field, []) or [])
    ys = list(sample.get(y_field, []) or [])
    if len(xs) != len(ys):
        raise PreparationError("Scatter sample has mismatched x and y lengths.")
    return VisualData(
        x_values=xs, y_values=[float(v) for v in ys],
        group_values=list(sample.get(spec.group.field, []) or []) if spec.group else [],
        sample_size=int(result.get("sample_size") or len(xs)),
        statistics=statistics,
        note=("Points shown are a bounded sample of the dataset; all statistics come "
              "from the full analysis run." if len(xs) < (result.get("sample_size") or 0)
              else ""),
    )


def _forest(spec, result, statistics) -> VisualData:
    coefficients = (result.get("extra") or {}).get("coefficients") or {}
    names, estimates, lows, highs = [], [], [], []
    for name, values in coefficients.items():
        if name == "const":
            continue  # the intercept is not a comparable effect
        names.append(name)
        estimates.append(float(values["estimate"]))
        lows.append(float(values["ci_low"]))
        highs.append(float(values["ci_high"]))
    if not names:
        raise PreparationError("The regression result carried no coefficients to plot.")
    return VisualData(
        categories=names, y_values=estimates, ci_low=lows, ci_high=highs,
        sample_size=int(result.get("sample_size") or 0), statistics=statistics,
    )


def _box(spec, result, sample, statistics) -> VisualData:
    group_field = spec.group.field if spec.group else None
    value_field = spec.y.field if spec.y else None
    groups = list(sample.get(group_field, []) or [])
    values = [float(v) for v in (sample.get(value_field, []) or [])]
    if len(groups) != len(values):
        raise PreparationError("Box sample has mismatched group and value lengths.")
    return VisualData(
        group_values=groups, y_values=values,
        categories=sorted(set(groups)),
        sample_size=int(result.get("sample_size") or len(values)),
        statistics=statistics,
    )


def _bar(spec, result, statistics) -> VisualData:
    groups = (result.get("extra") or {}).get("groups") or {}
    categories = sorted(groups)
    means, lows, highs = [], [], []
    for name in categories:
        entry = groups[name]
        mean = float(entry["mean"]) if isinstance(entry, dict) else float(entry)
        means.append(mean)
        if isinstance(entry, dict) and entry.get("std") is not None and entry.get("n"):
            # Standard error of the mean, so the bar carries its own uncertainty.
            se = float(entry["std"]) / max(1.0, float(entry["n"]) ** 0.5)
            lows.append(mean - 1.96 * se)
            highs.append(mean + 1.96 * se)
    if not categories:
        raise PreparationError("The result carried no group means to plot.")
    return VisualData(
        categories=categories, y_values=means, ci_low=lows, ci_high=highs,
        sample_size=int(result.get("sample_size") or 0), statistics=statistics,
    )


def _histogram(spec, result, sample, statistics) -> VisualData:
    field = spec.x.field if spec.x else None
    values = [float(v) for v in (sample.get(field, []) or [])]
    if not values:
        raise PreparationError(f"No sample values supplied for {field!r}.")
    return VisualData(
        y_values=values, sample_size=int(result.get("sample_size") or len(values)),
        statistics=statistics,
    )


def _heatmap(spec, result, statistics) -> VisualData:
    table = (result.get("extra") or {}).get("table") or {}
    if not table:
        raise PreparationError("The result carried no contingency table to plot.")
    columns = sorted(table)
    rows = sorted({row for column in table.values() for row in column})
    matrix = [[float(table[column].get(row, 0)) for column in columns] for row in rows]
    return VisualData(
        categories=columns, group_values=rows, matrix=matrix,
        sample_size=int(result.get("sample_size") or 0), statistics=statistics,
    )
