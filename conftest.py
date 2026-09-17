"""Narrow pytest support for the route-derived ownership sweep.

The T185 sweep exercises every id-bearing API route and requires the caller-owned
control request to succeed before it can prove a foreign id is rejected. A few
of those routes deliberately need a language model. Clean CI deliberately has
no Ollama process or paid provider, so this module supplies a deterministic
in-process model only while that one sweep module is running.

Important: this fixture intentionally does *not* depend on pytest's ``monkeypatch``
fixture. The suite already has an autouse provider-reset fixture in
``tests/conftest.py``. Making a second root autouse fixture depend on monkeypatch
changes teardown ordering for every test and can leave temporary provider doubles
installed while the reset fixture calls ``provider(refresh=True)``.
"""

from __future__ import annotations

from typing import Any

import pytest


class _OwnershipSweepModel:
    """Deterministic, network-free model used only by the T185 ownership sweep."""

    def _completion(self, prompt_name: str, prompt_version: int):
        from throughline_model.provider import Completion, Usage

        return Completion(
            text="Ownership sweep response.",
            model="ownership-sweep-local",
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            usage=Usage(),
            operational_summary="Deterministic ownership-test response.",
        )

    def generate_text(self, *, prompt_name: str = "ownership_sweep",
                      prompt_version: int = 1, **_: Any):
        return self._completion(prompt_name, prompt_version)

    def generate_structured(
        self, *, schema: type, prompt_name: str, prompt_version: int, **_: Any,
    ):
        from throughline_model.schemas import (
            PaperExtraction,
            PlainSummary,
            TestableClaims,
            VariableProposal,
            VariableProposals,
        )

        if schema is TestableClaims:
            value = TestableClaims(
                claims=[],
                note="No substantive claim is needed for the ownership sweep.",
            )
        elif schema is PaperExtraction:
            value = PaperExtraction(
                fields=[],
                note="No substantive extraction is needed for the ownership sweep.",
            )
        elif schema is PlainSummary:
            value = PlainSummary(
                headline="The recorded result is available for review.",
                what_it_means="The recorded variables move together in this analysis.",
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
def _model_for_cross_account_id_sweep(request):
    """Provide a model only to the route-derived cross-account ownership sweep."""
    if request.module.__name__.split(".")[-1] != "test_every_id_is_checked_against_the_caller":
        yield
        return

    import throughline_model
    from throughline_domain import extraction, harmonize, interpret

    fake = _OwnershipSweepModel()
    real_global_provider = throughline_model.provider

    # Some domain modules import ``provider`` at module import time, while
    # others (notably claim_test) import it inside the function that needs it.
    # Patch only bindings that actually exist; function-local imports will see
    # the global throughline_model.provider replacement below.
    candidates = (extraction, harmonize, interpret)
    patchable = tuple(module for module in candidates if hasattr(module, "provider"))
    originals = {module: module.provider for module in patchable}

    def sweep_global_provider(*, refresh: bool = False):
        # Preserve the normal provider reset contract during teardown.
        if refresh:
            return real_global_provider(refresh=True)
        return fake

    try:
        throughline_model.provider = sweep_global_provider
        for module in patchable:
            module.provider = lambda fake=fake: fake
        yield
    finally:
        for module, original in originals.items():
            module.provider = original
        throughline_model.provider = real_global_provider
