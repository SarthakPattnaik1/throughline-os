"""Repository-wide pytest hooks that must apply before ``tests/conftest.py``.

The T185 ownership sweep exercises every id-bearing API route. A handful of
those routes intentionally require a model, while clean CI intentionally has no
Ollama process, API key, or paid provider. The sweep's contract is stronger than
"the foreign request failed": its caller-owned control request must first
succeed, otherwise a model/configuration refusal could masquerade as an
ownership check.

For that one sweep only, provide a deterministic in-process model. It performs
no network I/O and produces no research numbers. This keeps CI free and local
while preserving the sweep's control-request requirement. Product tests of the
real no-model/refusal paths continue to use the real provider because this
fixture is scoped by test module.
"""

from __future__ import annotations

from typing import Any

import pytest


class _OwnershipSweepModel:
    """Small deterministic stand-in used only by the cross-account id sweep."""

    def _completion(self, prompt_name: str, prompt_version: int):
        from throughline_model.provider import Completion, Usage

        return Completion(
            text="Ownership sweep response.",
            model="ownership-sweep-local",
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            usage=Usage(),
            operational_summary="Deterministic test response.",
        )

    def generate_text(self, *, prompt_name: str, prompt_version: int, **_: Any):
        return self._completion(prompt_name, prompt_version)

    def generate_structured(
        self, *, schema: type, prompt_name: str, prompt_version: int, **_: Any,
    ):
        from throughline_model.schemas import (
            PlainSummary,
            TestableClaims,
            VariableProposal,
            VariableProposals,
        )

        if schema is TestableClaims:
            value = TestableClaims(
                claims=[],
                note="The ownership sweep does not need a substantive claim.",
            )
        elif schema is PlainSummary:
            value = PlainSummary(
                headline="The recorded result is available for review.",
                what_it_means="The variables move together in the recorded analysis.",
                how_confident="The recorded checks determine how much weight it deserves.",
                what_would_change_it="Different data or failed robustness checks could change it.",
                causal_reading="association_only",
            )
        elif schema is VariableProposals:
            value = VariableProposals(proposals=[
                VariableProposal(
                    column=name,
                    label=label,
                    canonical_name=canonical,
                    definition=definition,
                    choice_confidence=1.0,
                )
                for name, label, canonical, definition in (
                    ("country", "Country", "country", "Country recorded by the dataset."),
                    ("x", "X measure", "x_measure", "First recorded continuous measure."),
                    ("y", "Y measure", "y_measure", "Second recorded continuous measure."),
                )
            ])
        else:
            raise AssertionError(
                f"The ownership sweep reached an unexpected model schema: {schema.__name__}")

        return value, self._completion(prompt_name, prompt_version)


@pytest.fixture(autouse=True)
def _model_for_cross_account_id_sweep(request, monkeypatch):
    """Make model-backed T185 controls executable without an external service."""
    if request.module.__name__.split(".")[-1] != "test_every_id_is_checked_against_the_caller":
        yield
        return

    from throughline_domain import claim_test, harmonize, interpret, journal

    fake = _OwnershipSweepModel()
    for module in (claim_test, harmonize, interpret, journal):
        # These modules import ``provider`` directly, so patch the live name at
        # the point of use rather than the registry it was copied from.
        monkeypatch.setattr(module, "provider", lambda fake=fake: fake)

    yield
