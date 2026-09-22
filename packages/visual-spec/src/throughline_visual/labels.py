"""What a reader should see instead of a raw column name.

A dataset column has three names and they are not interchangeable.
`dataset_columns.name` is the normalised matching key. `original_name` is the
header the researcher actually typed. `canonical_variables.display_label` is
what the project decided this quantity is called once it had been mapped. Only
the last two were ever meant for a human, and a figure that prints the first is
showing its own plumbing.

This module is the one place that decision is made, so every axis, title and
caption resolves a name the same way. It is deliberately free of any database
import: `recommend.py` stays pure and testable, and the caller that *has* a
cursor is the one that fills the book (`throughline_domain.visuals`).

An empty book is not an error. When nothing better is known the raw name is
humanised and used, because inventing a label would be worse than showing the
one the file came with.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .spec import Encoding

#: Where a label came from, most trustworthy first. Recorded rather than
#: inferred, so a caller can tell a mapped variable from a bare column header
#: without re-deriving the decision — and so a test can assert that the
#: canonical layer was actually consulted rather than coincidentally agreeing.
CANONICAL = "canonical_variable"
DATASET_HEADER = "dataset_header"
COLUMN_NAME = "column_name"

LABEL_SOURCES = (CANONICAL, DATASET_HEADER, COLUMN_NAME)


def humanise(name: str) -> str:
    """The last resort: a raw column name made as readable as it can be.

    This is what the whole module exists to avoid reaching, but it has to
    exist — a column with no header worth reading and no approved mapping has
    no better label anywhere in the system, and a placeholder would be a lie.
    """
    return str(name).replace("_", " ").strip()


def strip_trailing_unit(header: str, unit: str | None) -> str:
    """Remove a unit the header already states, when it is carried separately.

    The profiler reads the unit out of a header like ``Resistance (%)`` and
    stores it in its own column. Both then reach the renderer, which appends
    the unit to the label — so a header used verbatim would print
    ``Resistance (%) (%)``. Stripping is safe only because the unit came from
    that exact suffix in the first place.
    """
    text = str(header).strip()
    if not unit:
        return text
    suffix = str(unit).strip().lower()
    lowered = text.lower()
    for opener, closer in (("(", ")"), ("[", "]")):
        candidate = f"{opener}{suffix}{closer}"
        if lowered.endswith(candidate):
            return text[: -len(candidate)].strip() or text

    # A profiler-inferred unit can come from a machine-style suffix such as
    # `flipper_length_mm`, `height-cm`, or `duration mins`. Once the unit
    # is stored separately on the encoding, leaving that suffix in the label
    # makes the renderer print it twice: "flipper length mm (mm)".
    #
    # Only strip an exact, already-known unit after a separator. We do not infer
    # a unit here, and we deliberately do not strip arbitrary trailing letters.
    # That keeps ambiguous names such as `count_c` untouched unless the
    # profiler has independently established that "c" is a unit.
    for separator in ("_", "-", " "):
        candidate = f"{separator}{suffix}"
        if lowered.endswith(candidate):
            cleaned = text[: -len(candidate)].rstrip("_- ").strip()
            return cleaned or text
    return text


@dataclass(frozen=True)
class VariableLabel:
    """One column's reader-facing identity."""

    label: str
    unit: str | None = None
    source: str = COLUMN_NAME

    def described(self) -> str:
        """Label with its unit, for prose that has no axis to carry it."""
        return f"{self.label} ({self.unit})" if self.unit else self.label


def _coerce(value: Any, *, field: str) -> VariableLabel:
    if isinstance(value, VariableLabel):
        return value

    if isinstance(value, str):
        label, unit, source = value.strip(), None, DATASET_HEADER
    elif isinstance(value, Mapping):
        label = str(value.get("label") or "").strip()
        raw_unit = value.get("unit")
        unit = str(raw_unit).strip() or None if raw_unit is not None else None
        source = str(value.get("source") or COLUMN_NAME)
        if source not in LABEL_SOURCES:
            source = COLUMN_NAME
    else:
        raise TypeError(
            f"A variable label must be a VariableLabel, a mapping or a string; "
            f"got {type(value).__name__} for {field!r}."
        )

    if not label:
        # An entry that carries no label is not a label. Say so in `source`
        # rather than letting an empty string inherit a provenance it does not
        # have — a test asserting "the canonical layer was used" would pass.
        return VariableLabel(label=humanise(field), unit=unit, source=COLUMN_NAME)
    return VariableLabel(label=label, unit=unit, source=source)


class LabelBook:
    """Raw column name in, reader-facing label out.

    Keyed by whatever name the analysis spec used. A spec may name either the
    normalised or the original column name (`analysis.validate_spec` accepts
    both), so the builder is expected to register both spellings.
    """

    __slots__ = ("_entries",)

    def __init__(
        self,
        entries: Mapping[str, VariableLabel | Mapping[str, Any] | str] | None = None,
    ) -> None:
        self._entries = {
            str(key): _coerce(value, field=str(key))
            for key, value in (entries or {}).items()
        }

    @classmethod
    def coerce(cls, value: "LabelBook | Mapping[str, Any] | None") -> "LabelBook":
        if isinstance(value, cls):
            return value
        return cls(value)

    def __bool__(self) -> bool:
        return bool(self._entries)

    def __contains__(self, name: object) -> bool:
        return str(name) in self._entries

    def get(self, name: str) -> VariableLabel:
        entry = self._entries.get(str(name))
        if entry is not None:
            return entry
        return VariableLabel(label=humanise(name), source=COLUMN_NAME)

    def label(self, name: str) -> str:
        """For an axis or a title. The unit is added by the renderer."""
        return self.get(name).label

    def unit(self, name: str) -> str | None:
        return self.get(name).unit

    def described(self, name: str) -> str:
        """For a caption, which has no axis to hang the unit on."""
        return self.get(name).described()

    def source(self, name: str) -> str:
        return self.get(name).source

    def encoding(self, name: str, **kwargs: Any) -> Encoding:
        """An `Encoding` whose field stays raw and whose label never is.

        `field` is a data key — renderers and `prepare.py` look values up by
        it, so it must remain exactly the column name. Everything a reader
        sees comes from the book.
        """
        entry = self.get(name)
        return Encoding(field=name, label=entry.label, unit=entry.unit, **kwargs)

    def joined(self, names: Iterable[str], *, separator: str = ", ") -> str:
        """A prose list of labels — for captions naming several predictors."""
        return separator.join(self.described(name) for name in names)

    def category_labels(self, names: Iterable[str]) -> dict[str, str]:
        """A category-value → label map for figures whose axis lists columns.

        A forest plot's y axis is a list of predictor *column names*, which no
        encoding covers: the encoding describes the estimate, not the rows. The
        map travels on the spec so the renderers do not have to guess which
        categories are columns and which are data values.
        """
        return {str(name): self.label(name) for name in names}
