"""Publication renderer — SVG, PDF and high-DPI PNG from one spec.

Consumes `(ResearchVisualSpec, VisualData)` and draws. It computes nothing: every
statistic printed on the figure comes from `data.statistics`, which came from the
recorded analysis run. That is what makes 's "visualization fidelity" check
answerable — the figure cannot disagree with the analysis because it never had
its own opinion.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # no display, no interactive backend

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from dataclasses import dataclass  # noqa: E402

from matplotlib.colors import LogNorm, PowerNorm  # noqa: E402

from .. import tokens  # noqa: E402
from ..spec import (  # noqa: E402
    BinShape, ResearchVisualSpec, Scale, UncertaintyDisplay, VisualData, VisualType,
)

#:  — journal-style defaults. Restrained, legible at column width. The
#: colours and sizes are not here: they come from `tokens`, per ground, through
#: `style_for` — this is only what does not change with the ground.
PUBLICATION_STYLE: dict[str, Any] = {
    "figure.figsize": (6.5, 4.2),
    "figure.dpi": 100,
    "savefig.dpi": 300,
    "font.family": "sans-serif",
    "font.sans-serif": list(tokens.FONT_STACK),
    "font.size": tokens.TYPE["label"],
    "axes.titlesize": tokens.TYPE["title"],
    "axes.titleweight": "bold",
    "axes.titlelocation": "left",
    "axes.labelsize": tokens.TYPE["label"],
    "xtick.labelsize": tokens.TYPE["tick"],
    "ytick.labelsize": tokens.TYPE["tick"],
    "legend.fontsize": tokens.TYPE["legend"],
    "legend.title_fontsize": tokens.TYPE["legend"],
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.linewidth": 0.6,
    "axes.axisbelow": True,
    "xtick.major.width": 0.6,
    "ytick.major.width": 0.6,
    "xtick.major.size": 3,
    "ytick.major.size": 3,
    "axes.grid": True,
    # Solid, not transparent: the grid colour is what a 25% grey made over the
    # ground. EPS has no transparency, so an alpha grid came out at full
    # strength there — visibly heavier than the same figure as PNG or PDF.
    "grid.alpha": 1.0,
    "grid.linewidth": 0.5,
    "legend.frameon": False,
    "savefig.bbox": "tight",
    "svg.fonttype": "none",   # text stays text in an SVG: searchable, editable
    "pdf.fonttype": 42,       # TrueType in a PDF, which journals' checkers accept
}

#: Formats with no alpha channel. A transparent ground is refused for these
#: rather than delivered as black or white — the thing the caller asked to avoid.
OPAQUE_FORMATS = ("eps", "jpeg", "jpg")


@dataclass(frozen=True)
class Look:
    """How one figure is drawn: its ground, and the colours that follow from it.

    Passed to every drawer, so no drawer names a colour of its own — which is
    how a dark export came to have white halos round every point.
    """

    ground: str = "light"
    transparent: bool = False

    @property
    def ink(self) -> dict[str, str]:
        return tokens.ink(self.ground)

    @property
    def palette(self) -> list[str]:
        return tokens.categorical(self.ground)

    def hue(self, index: int) -> str:
        return self.palette[index % len(self.palette)]

    def edge(self, hue: str) -> str:
        """The halo round a mark: the ground, unless the mark would vanish into it.

        Okabe-Ito yellow on white is 1.3:1 — a yellow point with a white halo
        is a hole in the figure. Those marks are outlined in ink instead.
        """
        neutrals = self.ink
        if tokens.contrast(hue, neutrals["ground"]) < 1.5:
            return neutrals["ink"]
        return neutrals["edge"]


def style_for(look: Look) -> dict[str, Any]:
    """The full matplotlib style for a look: the fixed defaults plus its colours."""
    neutrals = look.ink
    ground = "none" if look.transparent else neutrals["ground"]
    return {
        **PUBLICATION_STYLE,
        "figure.facecolor": ground,
        "axes.facecolor": ground,
        "savefig.facecolor": ground,
        "savefig.transparent": look.transparent,
        "text.color": neutrals["ink"],
        "axes.labelcolor": neutrals["ink"],
        "axes.titlecolor": neutrals["ink"],
        "axes.edgecolor": neutrals["muted"],
        "xtick.color": neutrals["muted"],
        "ytick.color": neutrals["muted"],
        "xtick.labelcolor": neutrals["muted"],
        "ytick.labelcolor": neutrals["muted"],
        "grid.color": neutrals["grid"],
        "legend.labelcolor": neutrals["ink"],
        "axes.prop_cycle": matplotlib.cycler(color=look.palette),
        "image.cmap": tokens.SEQUENTIAL,
    }

#: Formats a figure may be written in.
#:
#: Split by what they are *for*, because the choice is not cosmetic:
#:
#: **Vector** — `svg`, `pdf`, `eps`. Resolution-independent: the same file is
#: correct on a phone and on a poster. This is what a journal wants, and what a
#: reader can zoom into to check a value. A pixel height means nothing here, and
#: asking for one is reported rather than silently ignored.
#:
#: **Lossless raster** — `png`, `tiff`. For slides, and for journals that demand
#: raster at a stated resolution. `tiff` is the one several still specify.
#:
#: **Lossy raster** — `jpeg`, `webp`. Included because they are asked for, and
#: **wrong for almost every figure here.** These are line art and text on a flat
#: ground: JPEG's DCT rings around glyph edges and thin rules, and it has no
#: alpha, so a transparent background becomes black or white without asking. For
#: this content class PNG is usually *smaller as well as* exact. They are
#: offered for the one case where they help — a figure carrying a photograph or
#: a digitised scan — and `warn_about_format` says so rather than leaving a
#: researcher to discover it in review.
VECTOR_FORMATS = ("svg", "pdf", "eps")
LOSSLESS_RASTER_FORMATS = ("png", "tiff")
LOSSY_RASTER_FORMATS = ("jpeg", "jpg", "webp")
RASTER_FORMATS = LOSSLESS_RASTER_FORMATS + LOSSY_RASTER_FORMATS
SUPPORTED_FORMATS = VECTOR_FORMATS + RASTER_FORMATS

#: Named heights, in pixels. Deliberately heights rather than "1080p"/"720p".
#:
#: Those names mean a 16:9 *video frame*, and a figure's aspect ratio is set by
#: its content — the publication default is 6.5x4.2in, roughly 1.55:1. Forcing
#: 16:9 would either letterbox the figure or distort it, and nobody asking for
#: "1080p" wants their axes stretched. What they want is a predictable, large
#: enough image: so the height is honoured exactly and the width follows from
#: the figure.
HEIGHTS = {"720p": 720, "1080p": 1080, "1440p": 1440, "4k": 2160}

#: Colourblind-safe (Okabe-Ito), in the web charts' order, from `tokens` —
#: colour is never the only encoder, so markers vary too.
PALETTE = list(tokens.CATEGORICAL)
MARKERS = list(tokens.MARKERS)


class RenderError(ValueError):
    pass


def warn_about_format(fmt: str, *, has_photograph: bool = False) -> str | None:
    """
    What a researcher should know about this format before publishing it.

    Returns None when there is nothing to say. Separated from `render` so the
    interface can show it *before* the download rather than after — a warning
    that arrives with the file has already lost.
    """
    fmt = fmt.lower()
    if fmt in LOSSY_RASTER_FORMATS and not has_photograph:
        return (
            f"{fmt.upper()} is lossy. This figure is line art and text, so the "
            "compression will ring around glyph edges and thin rules, and there "
            "is no transparency. PNG is exact and usually smaller for this kind "
            "of image; SVG or PDF is what most journals ask for.")
    if fmt == "eps":
        # PostScript has no transparency at all. The band and the grid are
        # drawn solid so they survive it; overlapping points cannot be,
        # because their transparency is what shows where the data are dense.
        return (
            "EPS has no transparency. The confidence band and grid are drawn "
            "in solid colours so they look the same, but overlapping points are "
            "drawn fully solid, so crowded regions will not look denser than "
            "sparse ones. PDF and SVG keep transparency.")
    if fmt in VECTOR_FORMATS:
        return None
    return None


def render(
    spec: ResearchVisualSpec, data: VisualData, *, path: Path, fmt: str = "svg",
    height_px: int | None = None, metadata: dict[str, str] | None = None,
    ground: str = "light", transparent: bool = False,
) -> Path:
    """
    Render to `path`. Returns the written path.

    `height_px` sets the exact pixel height of a raster export; the width
    follows from the figure's own proportions. It is refused for a vector format
    rather than ignored, because a caller asking for 1080px of SVG has
    misunderstood something and a silent no-op leaves them believing it worked.

    **Exact dimensions need the tight bounding box switched off.** The
    publication style trims to content, which is right for a figure dropped into
    a manuscript and makes the output size unpredictable — the very thing a
    caller asking for a height is trying to pin down. A constrained layout fits
    the labels *inside* the figure instead of growing it, so nothing is clipped
    and the height is the height that was asked for.

    `ground` is `light` or `dark`: the neutrals the figure is drawn in. With
    `transparent`, nothing is painted behind the marks, so the figure takes
    whatever page it is placed on — the pair a README shows by theme.
    """
    fmt = fmt.lower()
    look = _look(ground, transparent, fmt)
    if fmt not in SUPPORTED_FORMATS:
        raise RenderError(
            f"{fmt!r} is not a supported publication format. "
            f"Supported: {', '.join(SUPPORTED_FORMATS)}"
        )
    if height_px is not None and fmt in VECTOR_FORMATS:
        raise RenderError(
            f"A pixel height means nothing for {fmt}, which is vector: the same "
            "file is correct at any size. Ask for a raster format, or drop the "
            "height.")
    if height_px is not None and height_px < 120:
        raise RenderError(
            f"{height_px}px is too small to carry axis labels legibly. A figure "
            "nobody can read is not a smaller figure.")

    path.parent.mkdir(parents=True, exist_ok=True)

    style = style_for(look)
    saving: dict[str, Any] = {"format": fmt if fmt != "jpg" else "jpeg"}

    if height_px is not None:
        inches_high = style["figure.figsize"][1]
        saving["dpi"] = height_px / inches_high
        # Content is fitted inside the figure rather than the figure grown to
        # fit the content, so the requested height is the delivered height.
        style["savefig.bbox"] = None
        saving["bbox_inches"] = None

    if fmt in ("jpeg", "jpg"):
        # Quality high and chroma subsampling off. It is still lossy — this
        # limits the damage rather than undoing it.
        saving["pil_kwargs"] = {"quality": 95, "subsampling": 0}

    if metadata:
        # Provenance travels with the file. A figure that leaves the building
        # and cannot be traced back to the spec that produced it is exactly what
        # this system refuses to do internally.
        if fmt == "png":
            saving["metadata"] = dict(metadata)
        elif fmt in ("pdf", "svg", "eps"):
            saving["metadata"] = {"Creator": metadata.get("Creator", "Throughline"),
                                  "Title": metadata.get("Title", "")}

    with plt.rc_context(style):
        figure, axes = plt.subplots(
            layout="constrained" if height_px is not None else None)
        try:
            draw_panel(spec, data, axes, look)
            _caption(spec, data, figure, look)
            figure.savefig(path, **saving)
        finally:
            plt.close(figure)
    return path


def _look(ground: str, transparent: bool, fmt: str) -> Look:
    """The look asked for, refused when the format cannot carry it."""
    if ground not in tokens.GROUNDS:
        raise RenderError(
            f"{ground!r} is not a figure ground. Grounds: {', '.join(tokens.GROUNDS)}")
    if transparent and fmt in OPAQUE_FORMATS:
        raise RenderError(
            f"{fmt.upper()} has no transparency, so a transparent ground would "
            "come out solid black or white. Use PNG, SVG or PDF for a figure "
            "that takes the colour of the page it is placed on.")
    return Look(ground=ground, transparent=transparent)


def draw_panel(spec: ResearchVisualSpec, data: VisualData, axes,
               look: Look | None = None) -> None:
    """Draw one figure onto `axes`: its marks, axes and title — not its caption.

    The unit a composed figure is built from. The caption belongs to the whole
    figure, so `render` adds it and `compose` gathers them into one legend.
    """
    look = look or Look()
    _draw(spec, data, axes, look)
    _decorate(spec, data, axes, look)


def _drawers() -> dict[VisualType, Any]:
    """
    Every kind of figure this renderer draws. One table, read by `_draw` and by
    `can_render`, so what the renderer does and what the interface is told it
    does cannot drift apart.

    Built when asked rather than at import: the drawers are defined further
    down this module, and a module-level table naming them stopped the whole
    package from importing — which is how it was first written.
    """
    return {
        VisualType.SCATTER: _scatter,
        VisualType.FOREST: _forest,
        VisualType.BOX: _box,
        VisualType.BAR: _bar,
        VisualType.HISTOGRAM: _histogram,
        VisualType.HEATMAP: _heatmap,
        VisualType.HEXBIN: _hexbin,
    }


def can_render(visual_type: VisualType | str) -> bool:
    """
    Whether a publication export exists for this kind of figure at all.

    Asked by the interface before it offers a format picker. The picker used
    to be shown for every figure, so a fitted surface — which this renderer
    has never drawn — offered PDF, SVG and PNG, and a Download that failed
    every time it was pressed.
    """
    try:
        return VisualType(visual_type) in _drawers()
    except ValueError:
        return False


def _draw(spec: ResearchVisualSpec, data: VisualData, axes, look: Look) -> None:
    drawer = _drawers().get(spec.visual_type)
    if drawer is None:
        raise RenderError(f"No publication renderer for {spec.visual_type}")
    drawer(spec, data, axes, look)


def _hexbin(spec, data: VisualData, axes, look: Look) -> None:
    """Draw the prepared cell counts; never bin observations in a renderer."""
    from matplotlib.cm import ScalarMappable
    from matplotlib.colors import Normalize
    from matplotlib.patches import Rectangle, RegularPolygon

    cells = list(data.series)
    if not cells:
        raise RenderError("A binned figure needs prepared cells.")

    counts = np.asarray([float(cell["count"]) for cell in cells], dtype=float)
    maximum = max(1.0, float(counts.max()))
    scale = str(spec.count_scale)
    if scale == "log":
        norm = LogNorm(vmin=max(1.0, float(counts.min())), vmax=maximum)
    elif scale == "sqrt":
        norm = PowerNorm(0.5, vmin=0.0, vmax=maximum)
    else:
        norm = Normalize(vmin=0.0, vmax=maximum)
    cmap = plt.get_cmap(tokens.SEQUENTIAL)

    for cell in cells:
        x, y = float(cell["x"]), float(cell["y"])
        x_step, y_step = float(cell.get("x_step", 1.0)), float(cell.get("y_step", 1.0))
        face = cmap(norm(float(cell["count"])))
        if spec.bin_shape is BinShape.SQUARE:
            patch = Rectangle(
                (x - x_step / 2, y - y_step / 2), x_step, y_step,
                facecolor=face, edgecolor=look.ink["edge"], linewidth=0.25,
            )
        else:
            patch = RegularPolygon(
                (x, y), numVertices=6, radius=min(x_step, y_step) * 0.58,
                orientation=np.radians(30), facecolor=face,
                edgecolor=look.ink["edge"], linewidth=0.25,
            )
        axes.add_patch(patch)

    x_step = float(cells[0].get("x_step", 1.0))
    y_step = float(cells[0].get("y_step", 1.0))
    axes.set_xlim(min(float(c["x"]) for c in cells) - x_step,
                  max(float(c["x"]) for c in cells) + x_step)
    axes.set_ylim(min(float(c["y"]) for c in cells) - y_step,
                  max(float(c["y"]) for c in cells) + y_step)

    mapper = ScalarMappable(norm=norm, cmap=cmap)
    mapper.set_array(counts)
    bar = axes.get_figure().colorbar(mapper, ax=axes, pad=0.02)
    suffix = "" if scale == "linear" else f" ({scale} scale)"
    bar.set_label(f"observations per cell{suffix}", fontsize=tokens.TYPE["tick"])
    bar.ax.tick_params(labelsize=tokens.TYPE["note"])
    bar.outline.set_visible(False)


def _scatter(spec, data: VisualData, axes, look: Look) -> None:
    xs = np.asarray(data.x_values, dtype=float)
    ys = np.asarray(data.y_values, dtype=float)
    groups = data.group_values
    if groups:
        for index, name in enumerate(sorted(set(groups))):
            mask = np.array([g == name for g in groups])
            hue = look.hue(index)
            axes.scatter(xs[mask], ys[mask], s=22, alpha=0.8, color=hue,
                         marker=MARKERS[index % len(MARKERS)], label=str(name),
                         edgecolors=look.edge(hue), linewidths=0.4)
        axes.legend(title=spec.group.label if spec.group else None)
    else:
        axes.scatter(xs, ys, s=22, alpha=0.8, color=look.hue(0),
                     edgecolors=look.edge(look.hue(0)), linewidths=0.4)

    if any(a.kind == "regression_line" for a in spec.annotations) and len(xs) > 1:
        slope = data.statistics.get("fit_slope")
        intercept = data.statistics.get("fit_intercept")
        if slope is None or intercept is None:
            raise RenderError(
                "This figure asks for a fitted regression line, but the recorded "
                "analysis did not supply the coefficients needed to draw it."
            )
        slope, intercept = float(slope), float(intercept)
        line_x = np.linspace(xs.min(), xs.max(), 100)
        axes.plot(line_x, slope * line_x + intercept, color=look.ink["ink"],
                  linewidth=1.2, linestyle="--",
                  label="_nolegend_")
        if spec.uncertainty is UncertaintyDisplay.BAND:
            # A visual guide to scatter about the fit, not a re-derived model
            # interval — the analysis's own interval is printed in the caption.
            residual = ys - (slope * xs + intercept)
            spread = float(np.std(residual, ddof=1)) if len(xs) > 2 else 0.0
            fitted = slope * line_x + intercept
            # Solid, and beneath the points. It was `#333333` at 8% alpha, drawn
            # after the points at the same z-order — so on top of them — and
            # EPS has no transparency: exported as EPS, this band became a
            # solid dark slab over nearly every point and the fitted line, on
            # the recommender's default figure for any correlation. `#efefef`
            # is the colour the 8% wash made over white, so every other format
            # looks as it did, and z-order 0.5 puts it under the points (1)
            # and the grid (1.5) rather than relying on drawing order. The
            # band colour is the ground's, from `tokens`.
            axes.fill_between(line_x, fitted - 1.96 * spread, fitted + 1.96 * spread,
                              color=look.ink["band"], linewidth=0, zorder=0.5)


def _forest(spec, data: VisualData, axes, look: Look) -> None:
    positions = np.arange(len(data.categories))
    estimates = np.asarray(data.y_values, dtype=float)
    lows = np.asarray(data.ci_low, dtype=float)
    highs = np.asarray(data.ci_high, dtype=float)

    axes.errorbar(estimates, positions,
                  xerr=[estimates - lows, highs - estimates],
                  fmt="o", color=look.hue(0), ecolor=look.ink["muted"],
                  capsize=3, markersize=5, linewidth=1.1)
    axes.set_yticks(positions)
    axes.set_yticklabels([_category_label(spec, c) for c in data.categories])
    axes.invert_yaxis()
    for annotation in spec.annotations:
        if annotation.kind == "reference_line" and annotation.value is not None:
            axes.axvline(annotation.value, color=look.ink["faint"], linewidth=1,
                         linestyle=":", zorder=0)


def _box(spec, data: VisualData, axes, look: Look) -> None:
    summaries = list(data.series)
    if not summaries:
        raise RenderError("A box figure needs prepared group summaries.")

    positions = np.arange(1, len(summaries) + 1)
    for index, (position, summary) in enumerate(zip(positions, summaries)):
        q1, median, q3 = (float(summary["q1"]), float(summary["median"]),
                          float(summary["q3"]))
        low, high = float(summary["whisker_low"]), float(summary["whisker_high"])
        width = 0.55
        axes.add_patch(__import__("matplotlib").patches.Rectangle(
            (position - width / 2, q1), width, q3 - q1,
            facecolor=look.hue(index), alpha=0.35,
            edgecolor=look.ink["muted"], linewidth=1,
        ))
        axes.plot([position - width / 2, position + width / 2], [median, median],
                  color=look.ink["ink"], linewidth=1.4)
        axes.plot([position, position], [low, q1], color=look.ink["muted"], linewidth=1)
        axes.plot([position, position], [q3, high], color=look.ink["muted"], linewidth=1)
        axes.plot([position - 0.12, position + 0.12], [low, low],
                  color=look.ink["muted"], linewidth=1)
        axes.plot([position - 0.12, position + 0.12], [high, high],
                  color=look.ink["muted"], linewidth=1)
        outliers = [float(v) for v in summary.get("outliers", [])]
        if outliers:
            axes.scatter(np.full(len(outliers), position), outliers, s=10,
                         facecolors="none", edgecolors=look.ink["muted"], linewidths=0.8)

    axes.set_xticks(positions)
    axes.set_xticklabels([str(summary["group"]) for summary in summaries])


def _bar(spec, data: VisualData, axes, look: Look) -> None:
    positions = np.arange(len(data.categories))
    axes.bar(positions, data.y_values, width=0.6,
             color=[look.hue(i) for i in range(len(positions))],
             alpha=0.85, edgecolor=look.ink["ink"], linewidth=0.5)
    if data.ci_low and data.ci_high and spec.uncertainty is not UncertaintyDisplay.NONE:
        values = np.asarray(data.y_values, dtype=float)
        axes.errorbar(positions, values,
                      yerr=[values - np.asarray(data.ci_low, dtype=float),
                            np.asarray(data.ci_high, dtype=float) - values],
                      fmt="none", ecolor=look.ink["ink"], capsize=3, linewidth=1)
    axes.set_xticks(positions)
    axes.set_xticklabels([str(c) for c in data.categories])
    #  — bar length encodes magnitude, so the baseline is zero. The critic
    # sets include_zero; honouring it here is what makes the fix real.
    if spec.y is not None and spec.y.include_zero:
        axes.set_ylim(bottom=min(0.0, float(np.min(data.y_values))))


def _histogram(spec, data: VisualData, axes, look: Look) -> None:
    bins = list(data.series)
    if not bins:
        raise RenderError("A histogram needs prepared bins.")
    left = np.asarray([float(item["left"]) for item in bins], dtype=float)
    right = np.asarray([float(item["right"]) for item in bins], dtype=float)
    count = np.asarray([float(item["count"]) for item in bins], dtype=float)
    axes.bar(left, count, width=right - left, align="edge",
             color=look.hue(0), alpha=0.8,
             edgecolor=look.ink["edge"], linewidth=0.5)
    axes.set_ylim(bottom=0)


def _heatmap(spec, data: VisualData, axes, look: Look) -> None:
    matrix = np.asarray(data.matrix, dtype=float)
    image = axes.imshow(matrix, cmap=tokens.SEQUENTIAL, aspect="auto")
    axes.set_xticks(np.arange(len(data.categories)))
    axes.set_xticklabels([str(c) for c in data.categories], rotation=30, ha="right")
    axes.set_yticks(np.arange(len(data.group_values)))
    axes.set_yticklabels([str(g) for g in data.group_values])
    #  — the value is printed, so the figure does not rely on colour alone.
    for row in range(matrix.shape[0]):
        for column in range(matrix.shape[1]):
            value = matrix[row, column]
            axes.text(column, row, f"{value:g}", ha="center", va="center",
                      fontsize=tokens.TYPE["tick"],
                      color="white" if value < matrix.max() * 0.6 else "#111111")
    axes.figure.colorbar(image, ax=axes, shrink=0.8, label="count")
    axes.grid(False)


def _decorate(spec: ResearchVisualSpec, data: VisualData, axes, look: Look) -> None:
    if spec.x is not None and spec.visual_type is not VisualType.FOREST:
        axes.set_xlabel(_axis_label(spec.x))
    if spec.y is not None and spec.visual_type is not VisualType.FOREST:
        axes.set_ylabel(_axis_label(spec.y))
        if spec.y.scale is Scale.LOG:
            axes.set_yscale("log")
    if spec.visual_type is VisualType.FOREST and spec.x is not None:
        axes.set_xlabel(_axis_label(spec.x))

    title = spec.title
    if spec.subtitle:
        # The subtitle is a sentence under the title, not a second bold line.
        axes.set_title(title, loc="left", pad=16 if title else 6)
        axes.text(0.0, 1.0, spec.subtitle, transform=axes.transAxes,
                  fontsize=tokens.TYPE["tick"], color=look.ink["muted"],
                  ha="left", va="bottom")
    elif title:
        axes.set_title(title, loc="left")


def _caption(spec: ResearchVisualSpec, data: VisualData, figure, look: Look) -> None:
    """The caption, preparation caveat, and sources under the whole figure."""
    parts = [text for text in (spec.caption, data.note) if text]
    if parts:
        # The preparation note is part of the exported research claim. In
        # particular, a bounded sample disclosure must not disappear when a
        # figure leaves the browser.
        figure.text(0.0, -0.06, _wrap(" ".join(parts)),
                    fontsize=tokens.TYPE["caption"],
                    color=look.ink["muted"], ha="left", va="top", wrap=True)
    if spec.citations:
        figure.text(1.0, -0.06, "Sources: " + "; ".join(spec.citations[:3]),
                    fontsize=tokens.TYPE["note"], color=look.ink["faint"],
                    ha="right", va="top")


def _axis_label(encoding) -> str:
    label = encoding.label or encoding.field.replace("_", " ")
    return f"{label} ({encoding.unit})" if encoding.unit else label


def _category_label(spec, category) -> str:
    """Text for one tick on an axis that lists categories.

    A forest plot's categories are column names, so the spec carries their
    labels. Humanising is the fallback for a spec written before those labels
    existed — not the intended path.
    """
    text = str(category)
    return spec.category_labels.get(text) or text.replace("_", " ")


def _wrap(text: str, width: int = 110) -> str:
    import textwrap

    return "\n".join(textwrap.wrap(text, width=width))
