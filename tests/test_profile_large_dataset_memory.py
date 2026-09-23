from __future__ import annotations

from throughline_ingestion import datasets


def test_delimited_profiling_streams_before_applying_row_limit(tmp_path, monkeypatch):
    path = tmp_path / "large.csv"
    rows = ["value,group"]
    rows.extend(f"{i},A" for i in range(25))
    rows.extend(f"{i},B" for i in range(25, 120))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    real_read_csv = datasets.pd.read_csv
    calls: list[dict] = []

    def observed_read_csv(*args, **kwargs):
        calls.append(dict(kwargs))
        return real_read_csv(*args, **kwargs)

    monkeypatch.setattr(datasets.pd, "read_csv", observed_read_csv)

    profile = datasets.profile_dataset(path, suffix=".csv", row_limit=25)

    assert profile.row_count == 120
    assert profile.quality_report["sampled"] is True
    assert profile.quality_report["rows_profiled"] == 25
    # Historical semantics are preserved: profiling sees the first N rows,
    # rather than changing to a random sample as part of the memory fix.
    assert "group" in profile.quality_report["constant_columns"]

    assert calls
    assert all(call.get("chunksize") == datasets.PROFILE_CHUNK_ROWS for call in calls), (
        "profile_dataset materialised a delimited file before applying row_limit"
    )
