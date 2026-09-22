"""The visualization critic.

Runs before a figure is published and either fixes the problem or warns. Its
subject is honesty of encoding, not aesthetics: a truncated bar axis, a hidden
sample size, an unshown confidence interval and a caption claiming causation are
all ways a technically-correct number becomes a misleading picture.

The overstatement check is the one that matters most. the system forbids converting
association into causation, and a caption is exactly where that conversion tends
to happen quietly.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from .spec import ResearchVisualSpec, Scale, UncertaintyDisplay, VisualData, VisualType

Severity = Literal["blocking", "serious", "advisory"]

#: Beyond this many categories a bar or box chart stops being readable.
MAX_CATEGORIES = 12

#: Language that asserts a causal relationship.
_CAUSAL_LANGUAGE = re.compile(
    r"\b(causes?|caused|causing|causal|leads? to|results? in|due to|because of|"
    r"drives?|driven by|produces?|induces?|effect of)\b",
    re.I,
)

#: Phrases that correctly hedge, so a caption that already says "association"
#: is not flagged for using the word "effect size".
_HEDGED = re.compile(r"\b(association|associated|correlat\w+|not (?:establish|imply))\b", re.I)


@dataclass(slots=True)
class Critique:
    check: str
    outcome: Literal["passed", "fixed", "warned", "violated"]
    severity: Severity
    detail: str
    fix_applied: str = ""


@dataclass(slots=True)
class CritiqueReport:
    critiques: list[Critique] = field(default_factory=list)
    spec: ResearchVisualSpec | None = None

    @property
    def blocking(self) -> list[Critique]:
        return [c for c in self.critiques if c.severity == "blocking"
                and c.outcome == "violated"]

    @property
    def publishable(self) -> bool:
        """ — a figure with an unfixed blocking problem must not be published."""
        return not self.blocking

    def to_dict(self) -> dict[str, Any]:
        return {
            "publishable": self.publishable,
            # asdict, not __dict__: these are slots dataclasses.
            "critiques": [asdict(c) for c in self.critiques],
        }


def critique(
    spec: ResearchVisualSpec, data: VisualData, *, analysis: dict[str, Any] | None = None,
    autofix: bool = True,
) -> CritiqueReport:
    """Check a figure and, where possible, correct it rather than only complain."""
    analysis = analysis or {}
    spec = spec.model_copy(deep=True)
    report = CritiqueReport(spec=spec)

    _axis_integrity(spec, report, autofix)
    _bin_transparency(spec, report, autofix)
    _uncertainty(spec, data, analysis, report, autofix)
    _sample_visibility(spec, data, report, autofix)
    _category_overload(spec, data, report)
    _scale_choice(spec, data, report)
    _accessibility(spec, data, report)
    _comparable_scales(spec, data, report)
    _misleading_encoding(spec, data, report)
    _overstatement(spec, analysis, report)

    report.spec = spec
    return report


def _comparable_scales(spec, data, report: CritiqueReport) -> None:
    """A coefficient plot puts every predictor on one axis.

    Raw regression coefficients are in the units of their predictor, so a
    variable measured in tens of thousands gets a coefficient near zero and its
    interval collapses to an invisible dot beside a predictor measured in units.
    The reader sees "no effect" when the truth may be "different units".
    """
    if spec.visual_type is not VisualType.FOREST or len(data.y_values) < 2:
        report.critiques.append(Critique(
            check="comparable_scales", outcome="passed", severity="serious",
            detail="Not a multi-estimate plot; no shared-axis comparison is implied.",
        ))
        return

    magnitudes = [abs(float(v)) for v in data.y_values if v]
    if not magnitudes:
        return
    spread = max(magnitudes) / min(magnitudes)
    if spread > 100:
        report.critiques.append(Critique(
            check="comparable_scales", outcome="violated", severity="serious",
            detail=(f"Coefficients span {spread:.0f}× on one axis, so the smallest "
                    "intervals are invisible and read as 'no effect' when they may "
                    "simply be in different units. Report standardized coefficients, "
                    "or facet the predictors by scale."),
        ))
    else:
        report.critiques.append(Critique(
            check="comparable_scales", outcome="passed", severity="serious",
            detail=f"Coefficients span {spread:.1f}×, comparable on a shared axis.",
        ))


def _axis_integrity(spec: ResearchVisualSpec, report: CritiqueReport, autofix: bool) -> None:
    """Bar length encodes magnitude; a truncated baseline exaggerates differences."""
    if spec.visual_type not in {VisualType.BAR, VisualType.HISTOGRAM}:
        report.critiques.append(Critique(
            check="axis_integrity", outcome="passed", severity="serious",
            detail=f"{spec.visual_type} does not encode magnitude by length; "
                   "a non-zero baseline is legitimate here.",
        ))
        return
    if spec.y is None:
        return
    if spec.y.include_zero:
        report.critiques.append(Critique(
            check="axis_integrity", outcome="passed", severity="blocking",
            detail="The value axis includes zero.",
        ))
        return
    if autofix:
        spec.y.include_zero = True
        report.critiques.append(Critique(
            check="axis_integrity", outcome="fixed", severity="blocking",
            detail="A bar chart's value axis was not anchored at zero, which "
                   "exaggerates differences between bars.",
            fix_applied="Set the value axis to include zero.",
        ))
    else:
        report.critiques.append(Critique(
            check="axis_integrity", outcome="violated", severity="blocking",
            detail="A bar chart's value axis must include zero.",
        ))


#: The default used when a binned figure arrives without a bin count.
#:
#: Chosen rather than computed so it is one reviewable number: around 30 cells
#: across the range holds up from a few thousand rows to a few hundred thousand,
#: which is the span where binning is the right answer at all.
DEFAULT_BIN_COUNT = 30


def _bin_transparency(spec: ResearchVisualSpec, report: CritiqueReport,
                      autofix: bool) -> None:
    """A binned figure must say how it was binned.

    Bin width is not a rendering detail. Widen it and two modes merge into one;
    narrow it and sampling noise reads as structure. Both are defensible
    figures, they disagree, and a reader cannot tell them apart from the picture
    — so the number that produced the shape belongs on the figure, exactly as a
    density plot states its bandwidth.
    """
    if spec.visual_type is not VisualType.HEXBIN:
        report.critiques.append(Critique(
            check="bin_transparency", outcome="passed", severity="serious",
            detail=f"{spec.visual_type} does not bin, so no bin width shapes it.",
        ))
        return

    if spec.bin_count:
        # Both decisions are recorded, not only the bin count. A logarithmic
        # colour scale changes the apparent ratio between two cells by an order
        # of magnitude, so a reader who assumes linear misreads the figure as
        # surely as one who assumes a different bin width.
        report.critiques.append(Critique(
            check="bin_transparency", outcome="passed", severity="blocking",
            detail=f"Binned into {spec.bin_count} {spec.bin_shape} cells across "
                   f"the range, with counts on a {spec.count_scale} colour "
                   f"scale. Both are stated on the figure.",
        ))
        return

    if autofix:
        spec.bin_count = DEFAULT_BIN_COUNT
        report.critiques.append(Critique(
            check="bin_transparency", outcome="fixed", severity="blocking",
            detail="A binned figure did not state its bin count, so its shape "
                   "could not be interpreted or reproduced.",
            fix_applied=f"Set the bin count to {DEFAULT_BIN_COUNT} and stated it "
                        f"in the caption.",
        ))
    else:
        report.critiques.append(Critique(
            check="bin_transparency", outcome="violated", severity="blocking",
            detail="A binned figure must state its bin count: bin width decides "
                   "how many modes the distribution appears to have.",
        ))


def _uncertainty(spec, data, analysis, report: CritiqueReport, autofix: bool) -> None:
    """Require uncertainty in the form the recorded analysis actually supports.

    A scalar interval on r, a regression slope, or an odds ratio is not a
    vertical interval around every observation. Those intervals belong in text
    unless the prepared data carries per-mark intervals (for example a forest
    plot). Turning a scalar CI into a band/error bar invents uncertainty the
    analysis never computed.
    """
    mark_interval = bool(data.ci_low) and bool(data.ci_high)
    scalar_interval = (
        analysis.get("ci_low") is not None and analysis.get("ci_high") is not None
    ) or (
        (analysis.get("effect_size") or {}).get("ci_low") is not None
        and (analysis.get("effect_size") or {}).get("ci_high") is not None
    )

    if mark_interval:
        if spec.uncertainty is not UncertaintyDisplay.NONE:
            report.critiques.append(Critique(
                check="uncertainty_representation", outcome="passed", severity="serious",
                detail=f"Per-mark uncertainty is shown as {spec.uncertainty}.",
            ))
            return
        if autofix:
            spec.uncertainty = UncertaintyDisplay.CONFIDENCE_INTERVAL
            report.critiques.append(Critique(
                check="uncertainty_representation", outcome="fixed", severity="serious",
                detail="Prepared mark-level confidence intervals were not displayed.",
                fix_applied="Enabled confidence-interval marks.",
            ))
        else:
            report.critiques.append(Critique(
                check="uncertainty_representation", outcome="violated",
                severity="serious",
                detail="Prepared mark-level confidence intervals are hidden.",
            ))
        return

    if scalar_interval:
        # Reader-facing recommenders state this as e.g. "95% CI [0.2, 0.6]".
        # That is the honest representation when the interval is on one scalar
        # statistic rather than on the observations.
        if re.search(r"\b(?:\d+(?:\.\d+)?%\s*)?CI\s*\[", spec.caption or "", re.I):
            report.critiques.append(Critique(
                check="uncertainty_representation", outcome="passed",
                severity="serious",
                detail="The run-level parameter interval is stated in the caption.",
            ))
            return
        if autofix and analysis.get("ci_low") is not None and analysis.get("ci_high") is not None:
            level = float(analysis.get("confidence_level") or 0.95) * 100
            interval = (
                f"{level:g}% CI [{float(analysis['ci_low']):.3g}, "
                f"{float(analysis['ci_high']):.3g}]"
            )
            spec.caption = (spec.caption.rstrip(". ") + f". {interval}.").strip()
            report.critiques.append(Critique(
                check="uncertainty_representation", outcome="fixed",
                severity="serious",
                detail="The run-level parameter interval was omitted from the figure.",
                fix_applied=f"Added {interval} to the caption.",
            ))
            return
        report.critiques.append(Critique(
            check="uncertainty_representation", outcome="violated",
            severity="serious",
            detail="A run-level parameter interval exists but is not stated.",
        ))
        return

    report.critiques.append(Critique(
        check="uncertainty_representation", outcome="passed", severity="serious",
        detail="The analysis produced no interval to display.",
    ))

def _sample_visibility(spec, data, report: CritiqueReport, autofix: bool) -> None:
    """A figure without n invites the reader to assume it is large."""
    n = data.sample_size or 0
    if not n:
        report.critiques.append(Critique(
            check="sample_visibility", outcome="warned", severity="serious",
            detail="The sample size is unknown, so it cannot be stated on the figure.",
        ))
        return
    if re.search(r"\bn\s*=\s*\d", spec.caption or ""):
        report.critiques.append(Critique(
            check="sample_visibility", outcome="passed", severity="serious",
            detail=f"The caption states n = {n}.",
        ))
        return
    if autofix:
        spec.caption = (spec.caption + f" n = {n}.").strip()
        report.critiques.append(Critique(
            check="sample_visibility", outcome="fixed", severity="serious",
            detail="The sample size was not stated on the figure.",
            fix_applied=f"Added n = {n} to the caption.",
        ))
    else:
        report.critiques.append(Critique(
            check="sample_visibility", outcome="violated", severity="serious",
            detail="The figure does not state its sample size.",
        ))


def _category_overload(spec, data, report: CritiqueReport) -> None:
    count = len(data.categories or data.group_values or [])
    if count > MAX_CATEGORIES:
        report.critiques.append(Critique(
            check="category_overload", outcome="warned", severity="advisory",
            detail=(f"{count} categories exceeds the {MAX_CATEGORIES} that stay readable. "
                    "Consider grouping the smallest, or faceting."),
        ))
    else:
        report.critiques.append(Critique(
            check="category_overload", outcome="passed", severity="advisory",
            detail=f"{count} categories." if count else "No categorical axis.",
        ))


def _scale_choice(spec, data, report: CritiqueReport) -> None:
    values = [v for v in (data.y_values or []) if v is not None and v > 0]
    if len(values) < 3:
        report.critiques.append(Critique(
            check="scale_choice", outcome="passed", severity="advisory",
            detail="Too few positive values to assess the scale.",
        ))
        return
    spread = max(values) / min(values)
    linear = spec.y is not None and spec.y.scale is Scale.LINEAR
    if spread > 1000 and linear:
        report.critiques.append(Critique(
            check="scale_choice", outcome="warned", severity="advisory",
            detail=(f"Values span {spread:.0f}× on a linear scale; the smallest are "
                    "invisible. A logarithmic scale may be more honest — but label it "
                    "clearly, because readers routinely misread log axes."),
        ))
    else:
        report.critiques.append(Critique(
            check="scale_choice", outcome="passed", severity="advisory",
            detail=f"Values span {spread:.1f}×, appropriate for the chosen scale.",
        ))


def _accessibility(spec, data, report: CritiqueReport) -> None:
    """ — colour must not be the only carrier of meaning."""
    groups = len(set(data.group_values or []))
    if groups > 1 and spec.visual_type in {VisualType.SCATTER, VisualType.LINE}:
        report.critiques.append(Critique(
            check="accessibility", outcome="warned", severity="advisory",
            detail=(f"{groups} groups are distinguished. Marker shape or line style must "
                    "vary as well as colour, and the underlying table must be available."),
        ))
    else:
        report.critiques.append(Critique(
            check="accessibility", outcome="passed", severity="advisory",
            detail="No colour-only encoding of groups.",
        ))


def _misleading_encoding(spec, data, report: CritiqueReport) -> None:
    problems: list[str] = []
    if spec.visual_type is VisualType.LINE and data.x_values:
        # A line between categories implies an ordering and interpolation that
        # categorical data does not have.
        if any(isinstance(v, str) for v in data.x_values):
            problems.append("A line chart over categorical x implies an ordering and "
                            "interpolation that the data does not support.")
    if spec.visual_type is VisualType.BAR and len(data.y_values) == 1:
        problems.append("A single bar communicates nothing a number does not; "
                        "it invites comparison with an absent baseline.")
    if problems:
        report.critiques.append(Critique(
            check="misleading_encoding", outcome="violated", severity="serious",
            detail=" ".join(problems),
        ))
    else:
        report.critiques.append(Critique(
            check="misleading_encoding", outcome="passed", severity="serious",
            detail="No misleading encoding detected.",
        ))


def _overstatement(spec, analysis: dict[str, Any], report: CritiqueReport) -> None:
    """/ — a caption may not upgrade an association into a cause."""
    text = f"{spec.title} {spec.subtitle} {spec.caption}"
    causal = _CAUSAL_LANGUAGE.search(text)
    if not causal:
        report.critiques.append(Critique(
            check="overstatement", outcome="passed", severity="blocking",
            detail="No causal language in the title or caption.",
        ))
        return

    causal_status = (analysis.get("causal_status") or "not_assessed")
    if causal_status in {"causal_supported", "possible_causal"}:
        report.critiques.append(Critique(
            check="overstatement", outcome="passed", severity="blocking",
            detail=f"Causal language is supported by causal_status = {causal_status}.",
        ))
        return
    if _HEDGED.search(text):
        report.critiques.append(Critique(
            check="overstatement", outcome="warned", severity="blocking",
            detail=(f"The caption uses causal wording ({causal.group(0)!r}) but also "
                    "hedges. Check that the causal phrase describes the literature "
                    "rather than this analysis."),
        ))
        return
    report.critiques.append(Critique(
        check="overstatement", outcome="violated", severity="blocking",
        detail=(f"The caption claims causation ({causal.group(0)!r}) but the analysis "
                f"has causal_status = {causal_status}. Rewrite it as an association, "
                "or assess causality explicitly."),
    ))
