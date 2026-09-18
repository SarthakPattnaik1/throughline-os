"""The figure design tokens — one palette, one type scale, two grounds.

Every renderer in this package reads its colours and sizes from here: the
publication export (matplotlib), the web spec (Vega-Lite) and the Blender
render. The web charts hold the same palette in `apps/web/lib/tokens.ts`, and
`tests/test_one_palette_everywhere.py` fails when the two disagree.

It used to be four palettes. The web charts drew Okabe-Ito in one order, the
publication export a hand-copied six of them in another — so the second group
of a comparison was orange on screen and vermilion in the manuscript — the
web spec left colour to Vega-Lite's default scheme, and Blender painted every
surface one fixed blue. A reader moving between the screen and the paper met
the same data in different colours, which is a claim that they differ.
"""

from __future__ import annotations

from typing import Literal

#: Okabe-Ito, in the web charts' order: eight hues distinguishable under every
#: common form of colour blindness. Past eight, aggregate or facet.
#:
#: The eighth is black, which is the ink of a light ground and invisible on a
#: dark one, so `categorical(ground)` swaps it for that ground's ink rather
#: than every drawer remembering to.
CATEGORICAL: tuple[str, ...] = (
    "#0072B2", "#E69F00", "#009E73", "#CC79A7",
    "#56B4E9", "#D55E00", "#F0E442", "#000000",
)

#: Shape backs up colour, so a figure printed in greyscale still separates its
#: groups. One per categorical hue.
MARKERS: tuple[str, ...] = ("o", "s", "^", "D", "v", "P", "X", "*")

#: The sequential ramp, for any quantity with an order and a meaningful low
#: end — a count, a density, a fitted value. Perceptually uniform, readable in
#: greyscale and under colour blindness.
SEQUENTIAL = "viridis"

#: Viridis sampled at nine even stops, for renderers that cannot name a
#: colour map (Blender builds its ramp from these).
SEQUENTIAL_STOPS: tuple[str, ...] = (
    "#440154", "#472D7B", "#3B528B", "#2C728E", "#21918C",
    "#28AE80", "#5EC962", "#ADDC30", "#FDE725",
)

#: The app's typeface first, then the nearest neo-grotesques. The web ships
#: Inter as WOFF2, which matplotlib cannot open without a Brotli decoder, so an
#: export uses Inter where it is installed and Helvetica or Arial otherwise —
#: the same letterforms family, never matplotlib's DejaVu unless nothing else
#: exists.
FONT_STACK: tuple[str, ...] = (
    "Inter", "Helvetica Neue", "Helvetica", "Arial", "Liberation Sans",
    "DejaVu Sans",
)

#: Type scale, in points, by role. One scale for every panel of every figure:
#: a composed figure whose panels were set at different sizes reads as pasted
#: together, which is what it was.
TYPE: dict[str, float] = {
    "panel": 12.0,     # the A, B, C of a composed figure
    "title": 10.0,
    "label": 9.0,      # axis titles
    "tick": 8.0,
    "legend": 8.0,
    "metric": 8.0,     # the numbers printed under a panel
    "caption": 7.5,
    "note": 7.0,       # sources, provenance
}

Ground = Literal["light", "dark"]
GROUNDS: tuple[str, ...] = ("light", "dark")

#: The neutrals a figure is drawn in, per ground. Taken from the web's neutral
#: ramp (`neutral.light` / `neutral.dark` in tokens.ts), so an exported figure
#: sits on a Throughline page — or a slide in the same theme — without a seam.
INK: dict[str, dict[str, str]] = {
    "light": {
        "ground": "#FFFFFF",
        "ink": "#2A2A28",        # text, axes, error bars
        "muted": "#4F4F4B",      # tick labels, caption
        "faint": "#6B6B66",      # sources, reference lines
        "grid": "#E8E8E5",
        "band": "#EFEFEF",       # a fit's spread; solid, because EPS has no alpha
        "edge": "#FFFFFF",       # the halo that separates overlapping marks
        "emphasis": "#B91C1C",   # the one line that must be seen
    },
    "dark": {
        "ground": "#0A0C0F",
        "ink": "#D5DAE2",
        "muted": "#97A0AE",
        "faint": "#6E7787",
        "grid": "#1B2028",
        "band": "#1F252E",
        "edge": "#0A0C0F",
        "emphasis": "#F87171",
    },
}


def ink(ground: str) -> dict[str, str]:
    """The neutrals for a ground, refusing one that does not exist."""
    if ground not in INK:
        raise ValueError(
            f"{ground!r} is not a figure ground. Grounds: {', '.join(GROUNDS)}")
    return INK[ground]


def categorical(ground: str = "light") -> list[str]:
    """The categorical hues, in order, as they are drawn on `ground`."""
    neutrals = ink(ground)
    return [neutrals["ink"] if hue == "#000000" else hue for hue in CATEGORICAL]


def _linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def luminance(hex_colour: str) -> float:
    """WCAG relative luminance of `#RRGGBB`."""
    value = hex_colour.lstrip("#")
    r, g, b = (int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two `#RRGGBB` colours."""
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


__all__ = ["CATEGORICAL", "FONT_STACK", "GROUNDS", "INK", "MARKERS", "SEQUENTIAL",
           "SEQUENTIAL_STOPS", "TYPE", "Ground", "categorical", "contrast", "ink",
           "luminance"]
