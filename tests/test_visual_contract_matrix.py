"""The supported analysis methods and their figure paths must stay in lockstep.

This is intentionally broader than renderer unit tests. A method is not visually
supported because recommend.py names a chart; it is supported only if the same
recommendation can be prepared once and consumed by both the web and
publication renderers without another statistical fit or aggregation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from throughline_visual import prepare, recommend
from throughline_visual.renderers import publication, web
from throughline_visual.spec import VisualType


def _coefficient(estimate: float, low: float, high: float, p: float = 0.01):
    return {
        "estimate": estimate,
        "std_error": 0.1,
        "t": 2.0,
        "z": 2.0,
        "p_value": p,
        "ci_low": low,
        "ci_high": high,
    }


def _result(method: str, *, n: int = 30) -> dict:
    base = {
        "method": method,
        "sample_size": n,
        "estimate": 0.4,
        "estimate_name": "estimate",
        "p_value": 0.01,
        "ci_low": 0.2,
        "ci_high": 0.6,
        "confidence_level": 0.95,
        "effect_size": {"name": "effect", "value": 0.4},
        "evidence_quality": "moderate",
        "extra": {},
    }
    if "correlation" in method:
        base["estimate_name"] = "pearson_r" if "pearson" in method else "spearman_rho"
        base["effect_size"] = {
            "name": base["estimate_name"], "value": 0.4,
        }
    return base


def _case(name: str):
    if name == "descriptive":
        return {"columns": ["x"]}, _result(name, n=6), {"x": [1, 2, 2, 3, 4, 8]}
    if name in {"pearson_correlation", "spearman_correlation", "bootstrap_correlation"}:
        return {"x": "x", "y": "y"}, _result(name, n=6), {
            "x": [1, 2, 3, 4, 5, 6], "y": [2, 3, 5, 6, 8, 9],
        }
    if name == "large_pearson":
        result = _result("pearson_correlation", n=6000)
        return {"x": "x", "y": "y"}, result, {
            "x": list(range(500)), "y": [float(i % 37) for i in range(500)],
        }
    if name == "linear_regression_simple":
        result = _result("linear_regression", n=6)
        result["estimate_name"] = "beta[x]"
        result["extra"] = {
            "outcome": "y", "predictors": ["x"],
            "coefficients": {
                "const": _coefficient(1.0, 0.8, 1.2),
                "x": _coefficient(2.0, 1.7, 2.3),
            },
        }
        return {"outcome": "y", "predictors": ["x"]}, result, {
            "x": [1, 2, 3, 4, 5, 6], "y": [3, 5, 7, 9, 11, 13],
        }
    if name == "linear_regression_multiple":
        result = _result("linear_regression", n=30)
        result["extra"] = {
            "outcome": "y", "predictors": ["x", "z"],
            "coefficients": {
                "const": _coefficient(1.0, 0.8, 1.2),
                "x": _coefficient(0.4, 0.2, 0.6),
                "z": _coefficient(-0.2, -0.4, -0.05),
            },
        }
        return {"outcome": "y", "predictors": ["x", "z"]}, result, {}
    if name in {"t_test", "mann_whitney", "anova", "kruskal_wallis"}:
        result = _result(name, n=8)
        result["extra"] = {
            "groups": {
                "A": {"n": 4, "mean": 2.5, "std": 1.1},
                "B": {"n": 4, "mean": 5.5, "std": 1.0},
            }
        }
        return {"value": "value", "group": "group"}, result, {
            "value": [1, 2, 3, 4, 4, 5, 6, 7],
            "group": ["A", "A", "A", "A", "B", "B", "B", "B"],
        }
    if name == "chi_square":
        result = _result(name, n=14)
        result["extra"] = {
            "table": {
                "control": {"yes": 4, "no": 3},
                "treated": {"yes": 6, "no": 1},
            }
        }
        return {"x": "arm", "y": "outcome"}, result, {}
    if name == "logistic_regression":
        result = _result(name, n=40)
        result["estimate_name"] = "odds_ratio[x]"
        result["estimate"] = 1.5
        result["ci_low"], result["ci_high"] = 1.1, 2.1
        result["effect_size"] = {"name": "pseudo_r_squared", "value": 0.18}
        result["extra"] = {
            "outcome": "case", "predictors": ["x", "z"],
            "coefficients": {
                "const": _coefficient(-0.4, -0.8, 0.0),
                "x": _coefficient(0.405465, 0.09531, 0.74194),
                "z": _coefficient(-0.25, -0.5, -0.05),
            },
        }
        return {"outcome": "case", "predictors": ["x", "z"]}, result, {}
    if name == "mixed_model":
        result = _result(name, n=50)
        result["estimate_name"] = "beta[x]"
        result["extra"] = {
            "outcome": "y", "predictors": ["x", "z"], "group": "site",
            "coefficients": {
                "const": _coefficient(1.0, 0.7, 1.3),
                "x": _coefficient(0.5, 0.2, 0.8),
                "z": _coefficient(-0.3, -0.55, -0.05),
            },
        }
        return {"outcome": "y", "predictors": ["x", "z"], "group": "site"}, result, {}
    raise AssertionError(name)


CASES = [
    "descriptive",
    "pearson_correlation",
    "spearman_correlation",
    "bootstrap_correlation",
    "large_pearson",
    "linear_regression_simple",
    "linear_regression_multiple",
    "t_test",
    "mann_whitney",
    "anova",
    "kruskal_wallis",
    "chi_square",
    "logistic_regression",
    "mixed_model",
]


@pytest.mark.parametrize("name", CASES)
def test_every_supported_method_has_one_prepared_web_and_publication_path(
    name: str, tmp_path: Path,
):
    variables, result, sample = _case(name)
    method = (
        "pearson_correlation" if name == "large_pearson"
        else "linear_regression" if name.startswith("linear_regression_")
        else name
    )
    recommendation = recommend.recommend(
        analysis_run_id=f"run_{name}",
        method=method,
        variables=variables,
        result=result,
        dataset_version_id="dsv_1",
    )
    spec = recommendation["spec"]
    data = prepare.prepare(spec, analysis_result=result, sample=sample)

    chart = web.render(spec, data)
    assert chart["usermeta"]["analysis_run_id"] == f"run_{name}"
    assert chart["usermeta"]["statistics"] == data.statistics

    target = tmp_path / f"{name}.svg"
    publication.render(spec, data, path=target, fmt="svg")
    assert target.exists() and target.stat().st_size > 500


def test_large_correlation_is_prepared_as_binned_cells_once():
    variables, result, sample = _case("large_pearson")
    recommendation = recommend.recommend(
        analysis_run_id="run_large",
        method="pearson_correlation",
        variables=variables,
        result=result,
        dataset_version_id="dsv_1",
    )
    assert recommendation["visual_type"] is VisualType.HEXBIN
    data = prepare.prepare(
        recommendation["spec"], analysis_result=result, sample=sample
    )
    assert data.series
    assert sum(int(cell["count"]) for cell in data.series) == len(sample["x"])
    assert "bounded uniform sample" in data.note


def test_logistic_forest_uses_odds_ratios_and_null_one():
    variables, result, sample = _case("logistic_regression")
    recommendation = recommend.recommend(
        analysis_run_id="run_logit",
        method="logistic_regression",
        variables=variables,
        result=result,
        dataset_version_id="dsv_1",
    )
    data = prepare.prepare(
        recommendation["spec"], analysis_result=result, sample=sample
    )
    assert recommendation["visual_type"] is VisualType.FOREST
    assert recommendation["spec"].annotations[0].value == 1.0
    # exp(log(1.5)) from the recorded coefficient, not the headline copied twice.
    assert data.y_values[0] == pytest.approx(1.5, rel=1e-5)
