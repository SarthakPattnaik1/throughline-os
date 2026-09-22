"""Web renderer — emits a Vega-Lite specification.

The second backend consuming the *same* `(ResearchVisualSpec, VisualData)` pair.
It exists to prove the  claim concretely: two very different outputs, one
semantic description, and no analysis logic duplicated between them.

The emitted spec carries its data inline because `VisualData` is already a
bounded, chart-ready sample — the browser never receives the
dataset.
"""

from __future__ import annotations

from typing import Any

from .. import tokens
from ..spec import ResearchVisualSpec, UncertaintyDisplay, VisualData, VisualType

VEGA_LITE_SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json"


class WebRenderError(ValueError):
    pass


def render(spec: ResearchVisualSpec, data: VisualData) -> dict[str, Any]:
    builders = {
        VisualType.SCATTER: _scatter,
        VisualType.FOREST: _forest,
        VisualType.BOX: _box,
        VisualType.BAR: _bar,
        VisualType.HISTOGRAM: _histogram,
        VisualType.HEATMAP: _heatmap,
        VisualType.HEXBIN: _hexbin,
    }
    builder = builders.get(spec.visual_type)
    if builder is None:
        raise WebRenderError(f"No web renderer for {spec.visual_type}")

    chart = builder(spec, data)
    chart = _make_interactive(chart, spec, data)
    chart["$schema"] = VEGA_LITE_SCHEMA
    chart["config"] = config()
    chart["title"] = {"text": spec.title, "subtitle": spec.subtitle or None,
                      "anchor": "start"}
    # the rendered figure keeps its link to the computation behind it.
    chart["usermeta"] = {
        "analysis_run_id": spec.analysis_run_id,
        "dataset_version_id": spec.dataset_version_id,
        "caption": spec.caption,
        "citations": spec.citations,
        "statistics": data.statistics,
        "sample_size": data.sample_size,
        "note": data.note,
        "interaction": spec.interaction,
    }
    return chart


def _px(points: float) -> float:
    """A type-scale size in CSS pixels: 1pt is 4/3px."""
    return round(points * 4 / 3, 1)


def config(ground: str = "light") -> dict[str, Any]:
    """The Vega-Lite config that draws a chart in the shared tokens.

    Without it, Vega-Lite coloured groups from its own `tableau10` scheme — a
    third palette, beside the web charts' and the export's — and set every label
    in its default font and size.
    """
    neutrals = tokens.ink(ground)
    font = ", ".join(f'"{name}"' if " " in name else name for name in tokens.FONT_STACK)
    return {
        "font": font + ", sans-serif",
        "background": neutrals["ground"],
        "range": {"category": tokens.categorical(ground),
                  "ramp": {"scheme": tokens.SEQUENTIAL},
                  "heatmap": {"scheme": tokens.SEQUENTIAL}},
        "title": {"fontSize": _px(tokens.TYPE["title"]), "fontWeight": "bold",
                  "color": neutrals["ink"], "subtitleColor": neutrals["muted"],
                  "subtitleFontSize": _px(tokens.TYPE["tick"]), "anchor": "start"},
        "axis": {"labelFontSize": _px(tokens.TYPE["tick"]),
                 "titleFontSize": _px(tokens.TYPE["label"]),
                 "titleFontWeight": "normal",
                 "labelColor": neutrals["muted"], "titleColor": neutrals["ink"],
                 "domainColor": neutrals["muted"], "tickColor": neutrals["muted"],
                 "gridColor": neutrals["grid"]},
        "legend": {"labelFontSize": _px(tokens.TYPE["legend"]),
                   "titleFontSize": _px(tokens.TYPE["legend"]),
                   "labelColor": neutrals["ink"], "titleColor": neutrals["ink"]},
        "view": {"stroke": None},
    }


def _make_interactive(chart: dict[str, Any], spec: ResearchVisualSpec,
                      data: VisualData) -> dict[str, Any]:
    """
    Add  interaction, centrally rather than per chart type.

    Applied here so a new chart builder inherits hover, zoom, brush and the
    underlying-data view without remembering to. A capability that each builder
    must opt into is a capability half the builders will lack.

    On motion,  and  point the same way and it is worth being explicit:
    the transitions configured here are *semantic*. They exist so that when data
    changes under a chart — a filter applied, a confounder adjusted — marks move
    to their new positions instead of being replaced, and the reader can follow
    an individual point through the change. That is object constancy, and it is
    the one kind of chart animation that carries information.

    What is deliberately absent is entrance animation. A chart that draws itself
    on load delays reading by half a second, communicates nothing, and is the
    "gratuitous animation"  names. Motion that argues belongs in the
    communication surface , not in
    the instrument a researcher is reading.
    """
    layers = chart.get("layer")
    target = layers[0] if layers else chart

    # Hover, and a tooltip carrying every encoded field rather than one value.
    if isinstance(target, dict) and "mark" in target:
        mark = target["mark"]
        if isinstance(mark, str):
            mark = {"type": mark}
            target["mark"] = mark
        mark.setdefault("tooltip", {"content": "data"})
        # Object constancy: marks move rather than being torn down and rebuilt.
        mark.setdefault("cursor", "pointer")

    params: list[dict[str, Any]] = []

    # Zoom and pan on continuous axes only. On an ordinal axis it does nothing
    # useful and makes the chart feel broken when it refuses to move.
    encoding = target.get("encoding", {}) if isinstance(target, dict) else {}
    continuous = {"quantitative", "temporal"}
    if (encoding.get("x", {}).get("type") in continuous
            and encoding.get("y", {}).get("type") in continuous):
        params.append({"name": "view", "select": "interval",
                       "bind": "scales"})

    # Brush selection: the reader marks a region and reads what is in it.
    params.append({
        "name": "brush",
        "select": {"type": "interval", "encodings": ["x", "y"]},
    })

    # Legend as a filter, when there are groups to filter by.
    if data.group_values:
        params.append({
            "name": "group_filter",
            "select": {"type": "point", "fields": ["group"]},
            "bind": "legend",
        })
        if isinstance(target, dict):
            target.setdefault("encoding", {}).setdefault("opacity", {
                "condition": {"param": "group_filter", "value": 1},
                "value": 0.15,
            })

    if isinstance(target, dict):
        existing = target.get("params", [])
        target["params"] = existing + params

    #  — an interactive chart still needs a described, tabular fallback.
    description = f"{spec.title}. {spec.caption}" if spec.caption else spec.title
    if data.note:
        description = (description + " " + data.note).strip()
    chart["description"] = description

    return chart


def _label(encoding) -> str:
    if encoding is None:
        return ""
    label = encoding.label or encoding.field.replace("_", " ")
    return f"{label} ({encoding.unit})" if encoding.unit else label


def _category_label(spec, category) -> str:
    """Text for one category on an axis that lists them.

    A forest plot's categories are column names, so the spec carries their
    labels. Humanising is the fallback for a spec written before those labels
    existed — not the intended path.
    """
    text = str(category)
    return spec.category_labels.get(text) or text.replace("_", " ")


def _hexbin(spec, data: VisualData) -> dict[str, Any]:
    cells = list(data.series)
    if not cells:
        raise WebRenderError("A binned figure needs prepared cells.")
    shape = "square" if str(spec.bin_shape) == "square" else "hexagon"
    scale = str(spec.count_scale)
    color_scale: dict[str, Any] = {"scheme": tokens.SEQUENTIAL}
    if scale == "log":
        color_scale["type"] = "log"
    elif scale == "sqrt":
        color_scale["type"] = "sqrt"
    return {
        "data": {"values": cells},
        "mark": {"type": "point", "filled": True, "shape": shape, "size": 90},
        "encoding": {
            "x": {"field": "x", "type": "quantitative", "title": _label(spec.x)},
            "y": {"field": "y", "type": "quantitative", "title": _label(spec.y)},
            "color": {
                "field": "count", "type": "quantitative",
                "title": (
                    "sampled observations per cell"
                    if data.note else "observations per cell"
                ),
                "scale": color_scale,
            },
            "tooltip": [
                {"field": "count", "type": "quantitative", "title": "observations"},
            ],
        },
    }


def _scatter(spec, data: VisualData) -> dict[str, Any]:
    rows = [{"x": x, "y": y} for x, y in zip(data.x_values, data.y_values)]
    if data.group_values:
        for row, group in zip(rows, data.group_values):
            row["group"] = group

    encoding: dict[str, Any] = {
        "x": {"field": "x", "type": "quantitative", "title": _label(spec.x),
              "scale": {"zero": bool(spec.x and spec.x.include_zero)}},
        "y": {"field": "y", "type": "quantitative", "title": _label(spec.y),
              "scale": {"zero": bool(spec.y and spec.y.include_zero)}},
    }
    if data.group_values:
        #  — shape as well as colour, so the figure survives greyscale.
        encoding["color"] = {"field": "group", "type": "nominal"}
        encoding["shape"] = {"field": "group", "type": "nominal"}

    layers: list[dict[str, Any]] = [
        {"mark": {"type": "point", "filled": True, "opacity": 0.75},
         "encoding": encoding}
    ]
    if any(a.kind == "regression_line" for a in spec.annotations):
        slope = data.statistics.get("fit_slope")
        intercept = data.statistics.get("fit_intercept")
        if slope is None or intercept is None:
            raise WebRenderError(
                "This figure asks for a fitted regression line, but the recorded "
                "analysis did not supply the coefficients needed to draw it."
            )
        xs = [float(v) for v in data.x_values]
        low, high = min(xs), max(xs)
        line = [
            {"x": low, "y": float(intercept) + float(slope) * low},
            {"x": high, "y": float(intercept) + float(slope) * high},
        ]
        layers.append({
            "data": {"values": line},
            "mark": {"type": "line", "color": tokens.INK["light"]["ink"], "strokeDash": [4, 3]},
            "encoding": {"x": {"field": "x", "type": "quantitative"},
                         "y": {"field": "y", "type": "quantitative"}},
        })
    return {"data": {"values": rows}, "layer": layers}


def _forest(spec, data: VisualData) -> dict[str, Any]:
    # The axis prints the field's values, so the label has to be substituted
    # into the data rather than declared on the encoding.
    rows = [
        {"predictor": _category_label(spec, name), "estimate": estimate,
         "low": low, "high": high}
        for name, estimate, low, high in zip(
            data.categories, data.y_values, data.ci_low, data.ci_high)
    ]
    reference = next(
        (float(a.value) for a in spec.annotations
         if a.kind == "reference_line" and a.value is not None),
        0.0,
    )
    return {
        "data": {"values": rows},
        "layer": [
            {"mark": {"type": "rule", "color": tokens.INK["light"]["faint"], "strokeDash": [2, 2]},
             "encoding": {"x": {"datum": reference}}},
            {"mark": {"type": "rule", "size": 1.5},
             "encoding": {"y": {"field": "predictor", "type": "nominal", "title": None},
                          "x": {"field": "low", "type": "quantitative",
                                "title": _label(spec.x)},
                          "x2": {"field": "high"}}},
            {"mark": {"type": "point", "filled": True, "size": 60},
             "encoding": {"y": {"field": "predictor", "type": "nominal"},
                          "x": {"field": "estimate", "type": "quantitative"}}},
        ],
    }


def _box(spec, data: VisualData) -> dict[str, Any]:
    summaries = list(data.series)
    if not summaries:
        raise WebRenderError("A box figure needs prepared group summaries.")
    rows = [
        {
            "group": item["group"],
            "q1": item["q1"], "median": item["median"], "q3": item["q3"],
            "whisker_low": item["whisker_low"],
            "whisker_high": item["whisker_high"],
            "n": item["n"],
        }
        for item in summaries
    ]
    return {
        "data": {"values": rows},
        "layer": [
            {"mark": {"type": "rule"},
             "encoding": {
                 "x": {"field": "group", "type": "nominal", "title": _label(spec.x)},
                 "y": {"field": "whisker_low", "type": "quantitative",
                       "title": _label(spec.y)},
                 "y2": {"field": "whisker_high"},
             }},
            {"mark": {"type": "bar", "size": 24},
             "encoding": {
                 "x": {"field": "group", "type": "nominal"},
                 "y": {"field": "q1", "type": "quantitative"},
                 "y2": {"field": "q3"},
             }},
            {"mark": {"type": "tick", "size": 24, "color": tokens.INK["light"]["ink"]},
             "encoding": {
                 "x": {"field": "group", "type": "nominal"},
                 "y": {"field": "median", "type": "quantitative"},
             }},
        ],
    }


def _bar(spec, data: VisualData) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for index, category in enumerate(data.categories):
        row = {"category": category, "value": data.y_values[index]}
        if data.ci_low and data.ci_high:
            row["low"], row["high"] = data.ci_low[index], data.ci_high[index]
        rows.append(row)

    layers: list[dict[str, Any]] = [{
        "mark": "bar",
        "encoding": {
            "x": {"field": "category", "type": "nominal", "title": _label(spec.x)},
            #  — bar length encodes magnitude, so the scale includes zero.
            "y": {"field": "value", "type": "quantitative", "title": _label(spec.y),
                  "scale": {"zero": True}},
        },
    }]
    if data.ci_low and spec.uncertainty is not UncertaintyDisplay.NONE:
        layers.append({
            "mark": {"type": "rule", "color": tokens.INK["light"]["ink"]},
            "encoding": {"x": {"field": "category", "type": "nominal"},
                         "y": {"field": "low", "type": "quantitative"},
                         "y2": {"field": "high"}},
        })
    return {"data": {"values": rows}, "layer": layers}


def _histogram(spec, data: VisualData) -> dict[str, Any]:
    bins = list(data.series)
    if not bins:
        raise WebRenderError("A histogram needs prepared bins.")
    return {
        "data": {"values": bins},
        "mark": "bar",
        "encoding": {
            "x": {"field": "left", "type": "quantitative",
                  "title": _label(spec.x)},
            "x2": {"field": "right"},
            "y": {"field": "count", "type": "quantitative", "title": "count",
                  "scale": {"zero": True}},
        },
    }


def _heatmap(spec, data: VisualData) -> dict[str, Any]:
    rows = [
        {"x": column, "y": row, "value": data.matrix[row_index][column_index]}
        for row_index, row in enumerate(data.group_values)
        for column_index, column in enumerate(data.categories)
    ]
    return {
        "data": {"values": rows},
        "layer": [
            {"mark": "rect",
             "encoding": {"x": {"field": "x", "type": "nominal", "title": _label(spec.x)},
                          "y": {"field": "y", "type": "nominal", "title": _label(spec.y)},
                          "color": {"field": "value", "type": "quantitative"}}},
            # The number is printed, so meaning does not rest on colour alone.
            {"mark": {"type": "text", "fontSize": 10},
             "encoding": {"x": {"field": "x", "type": "nominal"},
                          "y": {"field": "y", "type": "nominal"},
                          "text": {"field": "value", "type": "quantitative"}}},
        ],
    }
