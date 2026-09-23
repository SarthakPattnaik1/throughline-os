from __future__ import annotations

import importlib.util

import pandas as pd
import pytest

from throughline_ingestion import datasets


needs_tables = pytest.mark.skipif(
    importlib.util.find_spec("tables") is None,
    reason="optional HDF5 reader is not installed",
)


@needs_tables
def test_table_format_hdf5_remains_readable(tmp_path):
    path = tmp_path / "safe.h5"
    pd.DataFrame({"x": ["1", "2"], "y": ["a", "b"]}).to_hdf(
        path, key="data", format="table", mode="w"
    )

    frame, fmt = datasets.read_dataset(path, suffix=".h5")

    assert fmt == "hdf5"
    assert frame.to_dict(orient="records") == [
        {"x": "1", "y": "a"},
        {"x": "2", "y": "b"},
    ]


@needs_tables
def test_fixed_format_hdf5_is_refused_before_payload_read(tmp_path):
    path = tmp_path / "unsafe.h5"
    pd.DataFrame({"x": ["1", "2"]}).to_hdf(
        path, key="data", format="fixed", mode="w"
    )

    with pytest.raises(datasets.UnsupportedDataset) as raised:
        datasets.read_dataset(path, suffix=".h5")

    message = str(raised.value)
    assert "fixed format" in message
    assert "pickled Python objects" in message
    assert "format='table'" in message
