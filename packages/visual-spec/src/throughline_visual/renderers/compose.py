"""Several figures made into one — lettered panels, with their numbers under them.

A multi-panel figure was assembled by hand: each chart exported on its own,
then placed side by side in a drawing program, where the type sizes drifted,
the palettes disagreed and the numbers lived only in the caption. The strongest
figures in structural biology do the opposite — each panel carries its own
metrics beneath it, so a reader sees at once when two measures of the same
result tell different stories (a prediction that scores well on one metric and
badly on another).

This composes figures that already exist, drawn by the same drawers as a
single export, on one ground, in one type scale. It computes nothing: every
number under a panel is the recorded statistic the figure already carries, and
every disagreement it names is between recorded numbers.
"""

from __future__ import annotations

import math
import string
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402

from .. import tokens  # noqa: E402
from ..spec import ResearchVisualSpec, VisualData  # noqa: E402
from . import publication  # noqa: E402

#: How many panels one figure may hold. Past this a reader stops comparing and
#: starts searching; split it into two figures.
MAX_PANELS = 9

#: The size of one panel, in inches, before its line of numbers.
PANEL_SIZE = (3.4, 2.6)

Panel = tuple[ResearchVisualSpec, VisualData]


class ComposeError(publication.RenderError):
    """These figures cannot be made into one."""


# --- the numbers under a panel ------------------------------------------------


def _number(value: Any) -> str:
    """A recorded number for print: rounded for reading, never to another sign.

    Two decimals between 0.01 and 1000; two significant figures below that, so
    a small bound keeps its sign and its first digits (-0.004 is not "-0.00");
    whole numbers with separators above, rather than 1.2e+03 for 1234.
    """
    if value is None:
        return ""
    value = float(value)
    if value == 0:
        return "0"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value) < 0.01:
        return f"{value:.2g}"
    return f"{value:.2f}"


def _p(value: Any, alpha: float | None = None) -> str:
    """A p-value for print, never rounded across the threshold it is judged at.

    p = 0.0496 printed to three places is "0.050", which reads as not below
    0.05 while the result counts it as significant — the rounding reverses the
    finding (the class T176 fixed in the interpretation). Digits are added
    until the printed number sits on the same side of alpha as the recorded one.
    """
    value = float(value)
    if value < 0.001:
        return "p < 0.001"
    text = f"{value:.3f}"
    if alpha is not None:
        digits = 3
        while (float(text) < alpha) != (value < alpha) and digits < 12:
            digits += 1
            text = f"{value:.{digits}f}"
    return f"p = {text}"


def _name(raw: Any) -> str:
    text = str(raw or "estimate")
    return text.replace("_", " ")


def metrics_line(statistics: dict[str, Any]) -> str:
    """The recorded numbers of one panel, in one line: estimate, interval, p, effect, n.

    Only what was recorded. A number the run did not produce is left out, not
    shown as a dash — a dash under one panel and a value under the next reads
    as a comparison.
    """
    parts: list[str] = []
    estimate = statistics.get("estimate")
    if estimate is not None:
        text = f"{_name(statistics.get('estimate_name'))} = {_number(estimate)}"
        low, high = statistics.get("ci_low"), statistics.get("ci_high")
        if low is not None and high is not None:
            level = statistics.get("confidence_level")
            # The level is printed only when the run recorded one. An interval
            # labelled 95% because 95% is usual is a claim nobody made.
            label = f"{float(level) * 100:g}% CI" if level else "CI, level not recorded"
            text += f" [{_number(low)}, {_number(high)}] ({label})"
        parts.append(text)
    if statistics.get("p_value") is not None:
        parts.append(_p(statistics["p_value"], _alpha(statistics)))
    if statistics.get("effect_size") is not None:
        effect = f"{_name(statistics.get('effect_size_name') or 'effect size')} = "
        effect += _number(statistics["effect_size"])
        practical = statistics.get("practical_significance")
        if practical and practical != "not_assessed":
            effect += f" ({practical})"
        parts.append(effect)
    if statistics.get("sample_size"):
        parts.append(f"n = {int(statistics['sample_size'])}")
    return " · ".join(parts)


#: Characters of metric text that fit across one panel at the metric size.
#: Measured, not derived: Inter and Helvetica at 8pt set about 17 characters to
#: the inch, and 3.4in panels overflowed into their neighbour at one line.
CHARACTERS_PER_INCH = 16


def _fit(line: str, width: int) -> str:
    """Break a metrics line between its parts, never inside a number."""
    rows: list[str] = []
    for part in line.split(" · "):
        if rows and len(rows[-1]) + 3 + len(part) <= width:
            rows[-1] += " · " + part
        else:
            rows.append(part)
    return "\n".join(rows)


# --- where the numbers disagree -----------------------------------------------


def _null(statistics: dict[str, Any]) -> float:
    """No effect is 1 for a ratio and 0 for a difference, slope or correlation."""
    return 1.0 if "ratio" in str(statistics.get("estimate_name") or "") else 0.0


def _alpha(statistics: dict[str, Any]) -> float | None:
    """The run's own alpha, or None when it did not record its level.

    Not assumed: a check made at 0.05 against a run computed at 0.01 would
    report a contradiction between numbers that agree.
    """
    level = statistics.get("confidence_level")
    return None if not level else round(1 - float(level), 10)


def _significant(statistics: dict[str, Any]) -> bool | None:
    p, alpha = statistics.get("p_value"), _alpha(statistics)
    return None if p is None or alpha is None else float(p) < alpha


#: Estimates whose sign is fixed by the variables alone, so two panels with the
#: same outcome and predictor can be compared by sign. A mean or median
#: difference is not among them: its sign depends on which group was taken
#: from which, and two panels with opposite signs may agree entirely.
SIGNED_BY_VARIABLES = ("pearson_r", "spearman_rho", "spearman_r", "beta[", "odds_ratio[")


def _comparable_by_sign(statistics: dict[str, Any]) -> bool:
    name = str(statistics.get("estimate_name") or "")
    return any(name == prefix or (prefix.endswith("[") and name.startswith(prefix))
               for prefix in SIGNED_BY_VARIABLES)


def _direction(statistics: dict[str, Any]) -> int:
    estimate = statistics.get("estimate")
    if estimate is None:
        return 0
    return int(math.copysign(1, float(estimate) - _null(statistics))) \
        if float(estimate) != _null(statistics) else 0


def disagreements(panels: list[Panel]) -> list[str]:
    """Every place the recorded numbers point different ways, as sentences.

    Three kinds, each a comparison between numbers the runs recorded:

    - within a panel, the p-value and the interval disagree — significant with
      an interval that crosses no-effect, or the reverse (a one-sided test, an
      interval from a different method, or a boundary case a reader should see);
    - within a panel, significant and negligible — the p-value says "real", the
      effect size says "too small to matter";
    - across panels asking the same question — same outcome, same predictor,
      same kind of estimate, and a kind whose sign the variables fix — estimates
      on opposite sides of no-effect.

    A check that needs alpha is skipped for a run that did not record its
    confidence level, rather than made against an assumed one.
    """
    letters = string.ascii_uppercase
    found: list[str] = []
    for index, (spec, data) in enumerate(panels):
        stats = data.statistics or {}
        letter = letters[index]
        significant = _significant(stats)
        low, high = stats.get("ci_low"), stats.get("ci_high")
        if significant is not None and low is not None and high is not None:
            null = _null(stats)
            crosses = float(low) <= null <= float(high)
            if significant and crosses:
                found.append(
                    f"{letter}: p is below {_alpha(stats):g}, but the interval "
                    f"[{_number(low)}, {_number(high)}] includes {_number(null)}, "
                    "no effect.")
            elif not significant and not crosses:
                found.append(
                    f"{letter}: the interval [{_number(low)}, {_number(high)}] "
                    f"excludes {_number(null)}, but p is not below "
                    f"{_alpha(stats):g}.")
        if significant and stats.get("practical_significance") == "negligible":
            found.append(
                f"{letter}: statistically significant, but the effect size is "
                "negligible.")

    # Only estimates of the same thing are compared: the same outcome, against
    # the same predictor or grouping, by the same kind of estimate. A
    # correlation with sleep and a difference between arms can both be about
    # recall and have opposite signs without disagreeing about anything.
    same_question: dict[tuple[str, str, str], list[int]] = {}
    for index, (spec, data) in enumerate(panels):
        stats = data.statistics or {}
        if (spec.y is None or spec.x is None or not _direction(stats)
                or not _comparable_by_sign(stats)):
            continue
        key = (spec.y.field, spec.x.field, str(stats.get("estimate_name") or ""))
        same_question.setdefault(key, []).append(index)
    for (outcome, _, _), members in same_question.items():
        signs = {_direction(panels[i][1].statistics) for i in members}
        if len(signs) > 1:
            named = ", ".join(letters[i] for i in members)
            label = panels[members[0]][0].y.label or outcome.replace("_", " ")
            found.append(
                f"{named}: the same estimate for {label} points in opposite "
                "directions.")
    return found


# --- the figure ---------------------------------------------------------------


def _grid(count: int, columns: int | None) -> tuple[int, int]:
    if columns is None:
        columns = 1 if count == 1 else 2 if count in (2, 4) else 3
    columns = max(1, min(columns, count))
    return math.ceil(count / columns), columns


def compose(
    panels: list[Panel], *, path: Path, fmt: str = "svg", ground: str = "light",
    transparent: bool = False, height_px: int | None = None,
    columns: int | None = None, metadata: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Draw `panels` as one lettered figure at `path`.

    Returns what was drawn: the letters, each panel's numbers, and the
    disagreements printed under the figure — the same text the file carries, so
    the interface can show it without reading the image.
    """
    fmt = fmt.lower()
    if not panels:
        raise ComposeError("A composed figure needs at least one panel.")
    if len(panels) > MAX_PANELS:
        raise ComposeError(
            f"{len(panels)} panels is more than one figure can carry legibly; "
            f"the limit is {MAX_PANELS}. Split it into two figures.")
    if fmt not in publication.SUPPORTED_FORMATS:
        raise ComposeError(
            f"{fmt!r} is not a supported publication format. "
            f"Supported: {', '.join(publication.SUPPORTED_FORMATS)}")
    if height_px is not None and fmt in publication.VECTOR_FORMATS:
        raise ComposeError(
            f"A pixel height means nothing for {fmt}, which is vector. Ask for a "
            "raster format, or drop the height.")
    undrawable = [spec.visual_type.value for spec, _ in panels
                  if not publication.can_render(spec.visual_type)]
    if undrawable:
        raise ComposeError(
            "These figures have no flat drawing to place in a panel: "
            f"{', '.join(sorted(set(undrawable)))}. A fitted surface is rendered "
            "on its own, through Blender.")

    look = publication._look(ground, transparent, fmt)
    rows, cols = _grid(len(panels), columns)
    width = PANEL_SIZE[0] * cols
    # Each panel row carries its numbers under it, on up to two lines; the
    # figure's notes sit below the grid.
    height = (PANEL_SIZE[1] + 0.5) * rows
    lines = [metrics_line(data.statistics or {}) for _, data in panels]
    notes = disagreements(panels)

    style = publication.style_for(look)
    style["figure.figsize"] = (width, height)
    style["savefig.bbox"] = "tight" if height_px is None else None

    saving: dict[str, Any] = {"format": fmt if fmt != "jpg" else "jpeg"}
    if height_px is not None:
        saving["dpi"] = height_px / height
        saving["bbox_inches"] = None
    if fmt in ("jpeg", "jpg"):
        saving["pil_kwargs"] = {"quality": 95, "subsampling": 0}
    if metadata:
        saving["metadata"] = (dict(metadata) if fmt == "png" else
                              {"Creator": metadata.get("Creator", "Throughline"),
                               "Title": metadata.get("Title", "")}
                              if fmt in ("pdf", "svg", "eps") else None)
        if saving["metadata"] is None:
            del saving["metadata"]

    path.parent.mkdir(parents=True, exist_ok=True)
    letters = string.ascii_uppercase
    neutrals = look.ink
    with plt.rc_context(style):
        figure = plt.figure(layout="constrained")
        grid = figure.subfigures(rows, cols, squeeze=False)
        try:
            for index, (spec, data) in enumerate(panels):
                cell = grid[index // cols][index % cols]
                cell.set_facecolor("none")
                axes = cell.add_subplot()
                publication.draw_panel(spec, data, axes, look)
                # The letter sits in the corner of the cell, outside the axes,
                # in the one bold size every panel shares.
                cell.text(0.0, 1.0, letters[index], fontsize=tokens.TYPE["panel"],
                          fontweight="bold", color=neutrals["ink"],
                          ha="left", va="top")
                if lines[index]:
                    fitted = _fit(lines[index],
                                  int(PANEL_SIZE[0] * CHARACTERS_PER_INCH))
                    cell.supxlabel(fitted, fontsize=tokens.TYPE["metric"],
                                   color=neutrals["muted"], x=0.02,
                                   ha="left")
            for index in range(len(panels), rows * cols):
                grid[index // cols][index % cols].set_visible(False)

            across = int(width * CHARACTERS_PER_INCH * 8 / tokens.TYPE["caption"])
            below: list[tuple[str, str, str]] = []   # text, colour, weight
            if notes:
                below.append(("Where the numbers disagree", neutrals["ink"], "bold"))
                below += [(publication._wrap(note, width=across), neutrals["ink"],
                           "normal") for note in notes]
            captions = [f"{letters[i]}: {spec.caption}"
                        for i, (spec, _) in enumerate(panels) if spec.caption]
            if captions:
                below.append((publication._wrap(" ".join(captions), width=across),
                              neutrals["muted"], "normal"))
            # Every panel's sources, as a single export prints them: a figure
            # made of several must not cite fewer than its parts did.
            sources: list[str] = []
            for spec, _ in panels:
                sources += [c for c in spec.citations if c not in sources]
            if sources:
                below.append((publication._wrap("Sources: " + "; ".join(sources),
                                                width=across),
                              neutrals["faint"], "normal"))
            # Stacked downward from the bottom edge, one block per note, so a
            # disagreement is its own line rather than a clause in a paragraph.
            offset = 0.0
            step = tokens.TYPE["caption"] * 1.45 / 72 / height
            for text, colour, weight in below:
                figure.text(0.0, -offset, text, fontsize=tokens.TYPE["caption"],
                            color=colour, fontweight=weight, ha="left", va="top",
                            transform=figure.transFigure)
                offset += step * (text.count("\n") + 1) + step * 0.25
            figure.savefig(path, **saving)
        finally:
            plt.close(figure)

    return {"path": str(path), "letters": [letters[i] for i in range(len(panels))],
            "metrics": lines, "disagreements": notes,
            "rows": rows, "columns": cols}


__all__ = ["ComposeError", "MAX_PANELS", "compose", "disagreements", "metrics_line"]
