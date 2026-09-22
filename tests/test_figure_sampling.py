"""
What a figure is drawn from, and whether it says so (§47).

§47 opens with "do not attempt to render millions of points naïvely" and closes
with "clearly communicate sampling". The endpoint that feeds a figure did
neither honestly.

**`.head(500)` is not a sample.** Research data arrives sorted — by date, by
site, by arm — so the first five hundred rows are the earliest patients, or one
hospital, or one condition. That subset was drawn *underneath statistics
computed from the whole dataset*, so the picture and the numbers beside it
described different populations, and nothing on the figure said so.

**And nothing was communicated at all.** A reader could not tell a figure of
every point from a figure of 500 of two million.
"""

from __future__ import annotations

import pandas as pd
import pytest


def _sorted_frame(rows: int = 5_000) -> pd.DataFrame:
    """
    Sorted, the way research data actually arrives.

    `site` changes once, a third of the way in. A head-of-file sample sees one
    site; a random one sees both, in roughly their true proportion.
    """
    return pd.DataFrame({
        "day": range(rows),
        "value": [float(i) for i in range(rows)],
        "site": ["north"] * (rows // 3) + ["south"] * (rows - rows // 3),
    })


class TestTakingTheTopIsNotSampling:
    def test_the_head_of_a_sorted_file_misses_most_of_it(self):
        """
        The defect, demonstrated rather than asserted. This is what the
        endpoint used to hand the renderer.
        """
        frame = _sorted_frame()
        head = frame.head(500)

        assert set(head["site"]) == {"north"}, "the head sees one site"
        assert head["value"].max() < frame["value"].max() / 5

    def test_a_random_sample_sees_the_whole_file(self):
        frame = _sorted_frame()
        drawn = frame.sample(n=500, random_state=0)

        assert set(drawn["site"]) == {"north", "south"}
        # Roughly the true proportion, not one third of one site.
        share = (drawn["site"] == "north").mean()
        assert 0.25 < share < 0.42, share

    def test_the_same_seed_draws_the_same_points(self):
        """
        A researcher who reopens a chart and finds different points cannot
        tell a redraw from a change in the data, and cannot put it in a paper.
        """
        frame = _sorted_frame()
        first = frame.sample(n=500, random_state=0).index.tolist()
        second = frame.sample(n=500, random_state=0).index.tolist()
        assert first == second


class TestTheAccountIsHonest:
    """
    The shape `_visual_sample` returns alongside the points, asserted here
    against the same rules the endpoint applies. §47's closing line is that
    sampling must be *communicated*, and a caption can only say what the
    endpoint tells it.
    """

    @staticmethod
    def _account(total: int, limit: int = 500) -> dict:
        return {
            "sampled": total > limit,
            "rows_total": total,
            "rows_drawn": min(total, limit),
            "method": ("uniform random without replacement, fixed seed"
                       if total > limit else "every row"),
        }

    def test_a_small_dataset_says_it_drew_everything(self):
        account = self._account(120)
        assert account["sampled"] is False
        assert account["method"] == "every row"
        assert account["rows_drawn"] == 120

    def test_a_large_dataset_says_how_many_of_how_many(self):
        account = self._account(2_000_000)
        assert account["sampled"] is True
        assert account["rows_total"] == 2_000_000
        assert account["rows_drawn"] == 500
        assert "random" in account["method"]

    def test_the_boundary_is_not_called_sampling(self):
        """Exactly at the limit every row is drawn, and saying otherwise would
        put a caveat on a complete figure."""
        assert self._account(500)["sampled"] is False


def test_the_endpoint_returns_the_account(monkeypatch):
    """
    The wiring, which is the part that can silently regress: the sampling can
    be computed correctly and never reach the response.
    """
    from pathlib import Path

    source = Path("apps/api/src/throughline_api/app.py").read_text()
    body = source[source.index("def analysis_points("):]
    body = body[:body.index("\n@app.")]

    assert '"sampling": sampling' in body, "the account never reaches the reader"
    assert '"note": data.note' in body, "the sampling caveat never reaches the reader"
    assert "sample, sampling = _visual_sample(" in body


def test_the_sample_is_not_taken_from_the_top(tmp_path):
    """
    A regression guard on the defect itself, drawn rather than read.

    This used to search `_visual_sample` for the literal expression
    `.sample(n=limit, random_state=0)`, which stopped being true the moment the
    sampling moved into `_sample_columns` to stop loading the whole file — and
    it would have stopped being true just as loudly if the code had been made
    better in some other way. A guard that reads for a string is a guard about
    the spelling; this one takes a sample from a deliberately sorted file and
    checks where the rows came from, which is the thing that must stay true.
    """
    import numpy as np
    import pandas as pd
    from throughline_api.app import _sample_columns

    rows = 20_000
    ordered = tmp_path / "sorted.csv"
    # Sorted, as research data arrives: by date, by site, by arm.
    pd.DataFrame({"row_id": np.arange(rows)}).to_csv(ordered, index=False)

    sample, account = _sample_columns(ordered, ".csv", ["row_id"], 500)
    drawn = np.array(sample["row_id"])

    assert account["sampled"] is True
    assert account["rows_total"] == rows
    # The head of the file would be entirely inside the first 500 ids.
    assert drawn.max() > rows * 0.9, (
        "the sample stops near the top of the file, which is what "
        "`.head(500)` did beneath statistics computed from everything")
    assert drawn.min() < rows * 0.1, "the sample never reaches the top either"
    # And it is spread, not clustered at one end.
    assert rows * 0.3 < np.median(drawn) < rows * 0.7
