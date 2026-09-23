"""
Figure → dataset — reading numbers back out of a published chart (pair 6).

**Status: the engine is here, the surface is not.** Nothing imports this module
yet — no route, no component — so a researcher cannot reach it. That is
deliberate sequencing rather than abandonment: the refusal taxonomy below is the
hard part and is finished, and wiring it up is a later wave. Recorded here
because unreached code is otherwise indistinguishable from dead code, and the
next person to audit this repository will reasonably wonder.

Not a comparison: an extraction that then feeds the claim test. Which is exactly
why it is dangerous. Digitised values *look* like measured values by the time
they reach a correlation, and a P1 "supported" built on points read off a
low-resolution PNG is a materially weaker claim than one built on a CSV — but
the number that comes out the other end looks identical.

Three commitments follow, and each is load-bearing.

**Axis calibration comes from the researcher, never from OCR.** Every real
digitiser works this way (WebPlotDigitizer, Engauge) and it is not a limitation,
it is the correct division of labour. An axis label misread as 100 instead of
10 corrupts every extracted value by an order of magnitude *silently* — the
points still land on the curve, the correlation still computes, and nothing
anywhere says it is wrong. A person clicking two known points on each axis
cannot make that mistake without noticing.

**Pixel uncertainty is propagated and never discarded.** A point located to ±2
pixels on an axis spanning 400 pixels for 100 units carries ±0.5 units, and that
travels into every statistic computed from it. G2 exists because most real
figures are G2.

**A log axis must be declared, not guessed.** Reading a log plot as linear
produces values that are wrong by orders of magnitude at one end and nearly
right at the other, which is the most convincing kind of wrong. If the caller
does not say, the system refuses (G6) rather than assuming.
"""

from __future__ import annotations

import math
from typing import Any

from throughline_schemas.words import counted
from .image_safety import UnsafeImage, checked_dimensions
from .verdicts import Verdict

#: Below this a published figure has been through too much compression for the
#: marks to be located reliably.
MIN_DIMENSION = 200

#: Assumed error in locating a point centre, in pixels. Deliberately not zero:
#: an anti-aliased marker on a compressed figure genuinely is a fuzzy blob, and
#: a digitiser reporting exact values from one is lying by omission.
POINT_ERROR_PX = 2.0

#: Above this proportion of the axis span, the uncertainty is large enough that
#: only the ordering survives — the magnitudes are not usable (G4).
ORDINAL_ONLY = 0.05


class DigitiseError(RuntimeError):
    """A figure could not be read."""


class Calibration:
    """
    Two known points on each axis, supplied by the researcher.

    `x1_px`/`x1_value` and `x2_px`/`x2_value` are two positions on the x axis
    whose data values the researcher has read off the printed labels; likewise
    for y. That is the whole calibration, and it is the only part a machine
    should not do.
    """

    def __init__(self, *, x1_px: float, x1_value: float, x2_px: float,
                 x2_value: float, y1_px: float, y1_value: float,
                 y2_px: float, y2_value: float,
                 x_log: bool = False, y_log: bool = False) -> None:
        if x1_px == x2_px or y1_px == y2_px:
            raise DigitiseError(
                "The two reference points on an axis must be at different "
                "pixel positions.")
        if x1_value == x2_value or y1_value == y2_value:
            raise DigitiseError(
                "The two reference points on an axis must have different "
                "values.")
        if x_log and (x1_value <= 0 or x2_value <= 0):
            raise DigitiseError("A log axis cannot pass through zero.")
        if y_log and (y1_value <= 0 or y2_value <= 0):
            raise DigitiseError("A log axis cannot pass through zero.")

        self.x1_px, self.x2_px = x1_px, x2_px
        self.y1_px, self.y2_px = y1_px, y2_px
        self.x1_value, self.x2_value = x1_value, x2_value
        self.y1_value, self.y2_value = y1_value, y2_value
        self.x_log, self.y_log = x_log, y_log

    def _map(self, pixel: float, *, axis: str) -> float:
        if axis == "x":
            p1, p2, v1, v2, log = (self.x1_px, self.x2_px, self.x1_value,
                                   self.x2_value, self.x_log)
        else:
            p1, p2, v1, v2, log = (self.y1_px, self.y2_px, self.y1_value,
                                   self.y2_value, self.y_log)

        fraction = (pixel - p1) / (p2 - p1)
        if log:
            # Interpolate in log space, then come back. Doing this linearly is
            # the error that makes a log plot wrong by orders of magnitude at
            # one end while looking right at the other.
            return 10 ** (math.log10(v1) + fraction * (math.log10(v2)
                                                       - math.log10(v1)))
        return v1 + fraction * (v2 - v1)

    def to_data(self, x_px: float, y_px: float) -> tuple[float, float]:
        return self._map(x_px, axis="x"), self._map(y_px, axis="y")

    def uncertainty(self, x_px: float, y_px: float) -> tuple[float, float]:
        """
        Data-space error corresponding to POINT_ERROR_PX in each direction.

        Computed locally rather than as a constant, because on a log axis the
        same pixel error means a very different data error at each end.
        """
        x_error = abs(self._map(x_px + POINT_ERROR_PX, axis="x")
                      - self._map(x_px - POINT_ERROR_PX, axis="x")) / 2
        y_error = abs(self._map(y_px + POINT_ERROR_PX, axis="y")
                      - self._map(y_px - POINT_ERROR_PX, axis="y")) / 2
        return x_error, y_error


# ---------------------------------------------------------------------------
# Finding the marks
# ---------------------------------------------------------------------------

def detect_points(path: str, *, marker_colour: tuple[int, int, int] | None = None,
                  tolerance: int = 60) -> list[dict[str, float]]:
    """
    Find candidate data markers in a scatter plot.

    Colour-keyed when a marker colour is given, otherwise the darkest connected
    blobs. Returns pixel centroids with their apparent radius — a blob much
    larger than its neighbours is usually two overlapping points, and the caller
    reports that rather than silently treating it as one.
    """
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise DigitiseError(
            "Reading points off a figure needs OpenCV, which is not installed "
            "in this environment.") from exc

    try:
        checked_dimensions(path)
    except UnsafeImage as exc:
        raise DigitiseError(str(exc)) from exc

    image = cv2.imread(path, cv2.IMREAD_COLOR)
    if image is None:
        raise DigitiseError("That file could not be read as an image.")

    if marker_colour is not None:
        target = np.array(marker_colour[::-1], dtype=np.int16)  # RGB → BGR
        distance = np.abs(image.astype(np.int16) - target).sum(axis=2)
        mask = (distance < tolerance).astype("uint8") * 255
    else:
        grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        # Adaptive rather than fixed: published figures vary enormously in
        # background tone, and a fixed threshold finds nothing on half of them.
        mask = cv2.adaptiveThreshold(
            grey, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
            31, 10)

    count, _, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)

    found = []
    for index in range(1, count):
        area = stats[index, cv2.CC_STAT_AREA]
        width = stats[index, cv2.CC_STAT_WIDTH]
        height = stats[index, cv2.CC_STAT_HEIGHT]
        if area < 4 or area > 4000:
            continue
        # Axes, gridlines and tick labels are long and thin; markers are not.
        if max(width, height) > 6 * max(1, min(width, height)):
            continue
        found.append({
            "x_px": float(centroids[index][0]),
            "y_px": float(centroids[index][1]),
            "radius_px": float(math.sqrt(area / math.pi)),
            "area": float(area),
        })
    return found


# ---------------------------------------------------------------------------
# Digitising
# ---------------------------------------------------------------------------

def digitise(*, path: str, calibration: Calibration,
             marker_colour: tuple[int, int, int] | None = None,
             axes_declared: bool = True) -> dict[str, Any]:
    """
    Read a series off a chart, with its uncertainty.

    `axes_declared` records that the researcher has confirmed whether each axis
    is linear or logarithmic. Without that confirmation the result is G6 rather
    than a guess: a log plot read as linear is wrong by orders of magnitude at
    one end and nearly right at the other, which is the most convincing kind of
    wrong there is.
    """
    try:
        width, height = checked_dimensions(path)
    except UnsafeImage as exc:
        raise DigitiseError(str(exc)) from exc

    if min(width, height) < MIN_DIMENSION:
        return _refusal(
            "G8", "insufficient_resolution",
            f"This figure is {width}×{height}. Below {MIN_DIMENSION}px the "
            "marks cannot be located reliably enough for the numbers to mean "
            "anything.",
            remedies=["Find the figure at publication resolution, or ask the "
                      "authors for the underlying data."])

    if not axes_declared:
        return _refusal(
            "G6", "axis_scale_undeclared",
            "Whether these axes are linear or logarithmic has not been "
            "confirmed.",
            caveats=["Reading a log axis as linear produces values that are "
                     "wrong by orders of magnitude at one end and nearly right "
                     "at the other — which is the most convincing kind of "
                     "wrong."],
            remedies=["Confirm each axis scale and run it again."])

    points = detect_points(path, marker_colour=marker_colour)
    if not points:
        return _refusal(
            "G5", "no_marks_found",
            "No data markers could be separated from the rest of the figure.",
            remedies=["If the series is a line rather than points, or the "
                      "markers share a colour with the gridlines, this cannot "
                      "read it.",
                      "Ask the authors for the underlying data."])

    # Overlapping markers: a blob much larger than its neighbours is usually
    # two points, and reporting it as one would invent a value between them.
    radii = sorted(p["radius_px"] for p in points)
    median_radius = radii[len(radii) // 2]
    occluded = [p for p in points if p["radius_px"] > 1.8 * median_radius]

    series = []
    x_errors, y_errors = [], []
    for point in points:
        if point in occluded:
            continue
        x_value, y_value = calibration.to_data(point["x_px"], point["y_px"])
        x_error, y_error = calibration.uncertainty(point["x_px"], point["y_px"])
        series.append({
            "x": x_value, "y": y_value,
            "x_error": x_error, "y_error": y_error,
            "x_px": point["x_px"], "y_px": point["y_px"],
        })
        x_errors.append(x_error)
        y_errors.append(y_error)

    if not series:
        return _refusal(
            "G3", "all_points_occluded",
            "Every candidate marker overlaps another, so no individual point "
            "could be located.",
            remedies=["Read the values from a higher-resolution version."])

    x_span = abs(calibration.x2_value - calibration.x1_value)
    y_span = abs(calibration.y2_value - calibration.y1_value)
    x_relative = (sum(x_errors) / len(x_errors)) / x_span if x_span else 1.0
    y_relative = (sum(y_errors) / len(y_errors)) / y_span if y_span else 1.0
    worst = max(x_relative, y_relative)

    if worst > ORDINAL_ONLY:
        outcome, reason = "G4", "ordinal_only"
    elif occluded:
        outcome, reason = "G3", "partially_extracted"
    elif worst > 0.005:
        outcome, reason = "G2", "extracted_with_uncertainty"
    else:
        outcome, reason = "G1", "extracted_high_confidence"

    verdict = Verdict(
        outcome_code=outcome, reason_code=reason,
        confidence=0.85 if outcome in ("G1", "G2") else 0.6,
        facts={"error": f"±{sum(y_errors) / len(y_errors):.3g} on y",
               "extracted": str(len(series)),
               "total": str(len(series) + len(occluded))},
        method="deterministic",
        caveats=[
            # The line the taxonomy insists on: this uncertainty must survive
            # into anything computed from these values.
            "These values were read from pixels, not measured. The error "
            "propagates into every statistic computed from them and is carried "
            "with the data rather than dropped.",
        ] + ([f"{counted(len(occluded), 'marker')} overlap another and were left out "
              "rather than guessed at."] if occluded else [])
          + (["The uncertainty is large relative to the axis range, so the "
              "ordering of these points is usable and their magnitudes are "
              "not."] if outcome == "G4" else []),
        remedies=["Ask the authors for the underlying data — a digitised "
                  "series is always weaker evidence than the numbers behind "
                  "the figure."],
    )

    return {
        "verdict": verdict.to_dict(),
        "series": series,
        "occluded": len(occluded),
        "extracted": len(series),
        "figure_size": [width, height],
        "mean_error": {"x": sum(x_errors) / len(x_errors),
                       "y": sum(y_errors) / len(y_errors)},
        "relative_error": {"x": x_relative, "y": y_relative},
        "axes": {"x_log": calibration.x_log, "y_log": calibration.y_log},
        "provenance": (
            "Digitised from a figure. Axis calibration was supplied by a "
            "person; point positions were detected and converted with the "
            "stated pixel error. This is not measured data and must not be "
            "reported as if it were."),
    }


def _refusal(code: str, reason: str, sentence: str, **kwargs: Any
             ) -> dict[str, Any]:
    verdict = Verdict(outcome_code=code, reason_code=reason, confidence=0.9,
                      facts={}, method="deterministic", **kwargs)
    body = verdict.to_dict()
    # The taxonomy's templates for G5–G8 take no fields; the specific reason is
    # more useful than the generic sentence, so it leads.
    body["sentence"] = sentence
    return {"verdict": body, "series": [], "extracted": 0,
            "provenance": "Nothing was extracted."}


__all__ = ["Calibration", "DigitiseError", "MIN_DIMENSION", "ORDINAL_ONLY",
           "POINT_ERROR_PX", "detect_points", "digitise"]
