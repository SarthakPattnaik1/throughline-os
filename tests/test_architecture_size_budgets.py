"""Keep the consolidation hotspots from growing while they are decomposed.

These are ratchets, not ideal target sizes.  Each budget is the line count at the
start of the consolidation pass.  A follow-up may split a file and lower its
budget; no feature should make one of these coordination hubs larger again.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LINE_BUDGETS = {
    "apps/api/src/throughline_api/app.py": 6447,
    "apps/web/components/views.tsx": 4301,
    "apps/web/app/globals.css": 8268,
    "scripts/manage.py": 2213,
}


def test_known_architecture_hotspots_only_shrink():
    oversized = []
    for relative, budget in LINE_BUDGETS.items():
        lines = len((ROOT / relative).read_text(encoding="utf-8").splitlines())
        if lines > budget:
            oversized.append(f"{relative}: {lines} > {budget}")
    assert not oversized, (
        "Consolidation hotspots grew instead of shrinking:\n" + "\n".join(oversized)
    )
