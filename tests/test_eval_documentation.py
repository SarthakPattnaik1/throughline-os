"""Public evaluation counts must stay aligned with the executable harness."""

from pathlib import Path

from evals.harness import BEYOND_SPEC, CATEGORIES, UNIMPLEMENTED

ROOT = Path(__file__).resolve().parents[1]


def test_phase_5_evaluation_counts_match_harness():
    implemented = len(CATEGORIES) - len(BEYOND_SPEC)
    total = implemented + len(UNIMPLEMENTED)
    text = (ROOT / "docs" / "PHASE_5_COMMUNICATION.md").read_text(encoding="utf-8")
    assert (
        f"{implemented} of the {total} §58 categories are implemented. "
        f"{len(UNIMPLEMENTED)} are declared"
    ) in text


def test_roadmap_names_the_current_unimplemented_eval_count():
    text = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
    assert f"the {len(UNIMPLEMENTED)} eval\ncategories currently declared" in text
