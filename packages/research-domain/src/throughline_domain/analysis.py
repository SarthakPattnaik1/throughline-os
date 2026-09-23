"""Analysis specifications, runs and computational provenance.

this rule in practice: a number reaches the rest of the system only as a field of a
recorded `analysis_run`, produced by the sandbox from a validated spec against a
named dataset version. There is no path by which a number enters the research
model without a run behind it.

Validation happens *before* execution and against the real profiled schema,
so a spec naming a column that does not exist, or a method that does not exist,
fails at specification time rather than halfway through a computation.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Sequence

from throughline_schemas.enums import LineageType, ObjectType

from throughline_schemas.words import plural
from . import version as installation_version
from .events import audit, emit
from .ids import new_id
from .lineage import add_edge
from .objects import create_object

#: Methods this installation can run. Imported from the runtime so the whitelist
#: has exactly one definition.
try:  # pragma: no cover - exercised implicitly by every analysis test
    from throughline_runtime.methods import available_methods

    SUPPORTED_METHODS = frozenset(available_methods())
except Exception:  # pragma: no cover
    SUPPORTED_METHODS = frozenset()

#: Which variable roles each method requires. Validation is per method, because
#: "variables" means something different for a correlation and a regression.
METHOD_VARIABLES: dict[str, tuple[str, ...]] = {
    "descriptive": ("columns",),
    "pearson_correlation": ("x", "y"),
    "spearman_correlation": ("x", "y"),
    "linear_regression": ("outcome", "predictors"),
    "t_test": ("value", "group"),
    "mann_whitney": ("value", "group"),
    "chi_square": ("x", "y"),
    "anova": ("value", "group"),
    "kruskal_wallis": ("value", "group"),
}

# Roles that name several columns rather than one. `methods.py` reads exactly
# these two with `list(...)` and every other role as a single name, so an
# interface offering one box where the executor wants many produces a spec that
# validates and then fails in the sandbox. Declared here so the interface can be
# told rather than have to know.
MULTI_COLUMN_ROLES = frozenset({"columns", "predictors"})


#: The status a run carries once it has produced a result, and the one a
#: failure carries. These are written in exactly one place and read in
#: several — `findings` decides what counts as evidence by this value, and it
#: spent its first version filtering on "succeeded", a word nothing writes.
#: The query matched nothing, in production only: the test that covered it set
#: up its fixture with the same wrong word, so the code and the test agreed
#: with each other and neither agreed with the database.
RUN_COMPLETED = "completed"
RUN_FAILED = "failed"


def method_variables() -> dict[str, list[dict[str, str]]]:
    """Each method's variables, and whether each takes one column or several."""
    return {
        method: [
            {"role": role,
             "takes": "many" if role in MULTI_COLUMN_ROLES else "one"}
            for role in roles
        ]
        for method, roles in sorted(METHOD_VARIABLES.items())
        if method in SUPPORTED_METHODS
    }


FILTER_OPERATORS = frozenset({"gt", "gte", "lt", "lte", "eq", "ne", "in", "not_null"})


class AnalysisError(RuntimeError):
    pass


class SpecInvalid(AnalysisError):
    """ — every AnalysisSpec is validated before execution."""


def _columns_for(cur, dataset_version_id: str) -> dict[str, dict[str, Any]]:
    cur.execute(
        "SELECT name, original_name, physical_type, semantic_type, unit "
        "FROM dataset_columns WHERE dataset_version_id = %s",
        (dataset_version_id,),
    )
    rows = list(cur.fetchall())
    if not rows:
        raise SpecInvalid(f"Dataset version {dataset_version_id} has no profiled columns.")
    # A spec may name either the normalised or the original column name.
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        index[row["name"]] = row
        index[row["original_name"]] = row
    return index


def _referenced_columns(method: str, variables: dict[str, Any]) -> list[str]:
    referenced: list[str] = []
    for role in METHOD_VARIABLES.get(method, ()):
        value = variables.get(role)
        if isinstance(value, str):
            referenced.append(value)
        elif isinstance(value, (list, tuple)):
            referenced.extend(str(v) for v in value)
    return referenced


def file_column_names(cur, dataset_version_id: str) -> dict[str, str]:
    """Every accepted spelling of a column, mapped to the header in the file.

    An exact header always maps to itself, so a dataset holding both `a b` and
    `a_b` cannot have one of them shadowed by the other's normalised form.
    """
    cur.execute(
        "SELECT name, original_name FROM dataset_columns "
        "WHERE dataset_version_id = %s ORDER BY ordinal",
        (dataset_version_id,),
    )
    rows = list(cur.fetchall())
    index: dict[str, str] = {}
    for row in rows:
        index.setdefault(row["name"], row["original_name"])
    for row in rows:
        index[row["original_name"]] = row["original_name"]
    return index


def to_file_columns(cur, *, dataset_version_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Rewrite a spec's column references to the headers the file actually has.

    `validate_spec` accepts either the normalised name or the original header,
    because either identifies the column unambiguously. The sandbox cannot make
    that promise: it reads the stored file with pandas, so the only names it
    ever sees are the headers as written. Without this translation a spec the
    validator approved fails at compute time on a name the validator approved —
    which is precisely the failure mode validation exists to prevent, and it
    stayed invisible while every test dataset happened to be snake_case.

    The stored spec is left alone. Only the payload handed to the sandbox is
    translated, so provenance still records the names the researcher used.
    """
    index = file_column_names(cur, dataset_version_id)
    variables = dict(spec.get("variables") or {})
    for role in METHOD_VARIABLES.get(str(spec.get("method") or ""), ()):
        value = variables.get(role)
        if isinstance(value, str):
            variables[role] = index.get(value, value)
        elif isinstance(value, (list, tuple)):
            variables[role] = [index.get(str(v), str(v)) for v in value]

    filters = [
        {**rule, "column": index.get(rule["column"], rule["column"])}
        if isinstance(rule, dict) and "column" in rule else rule
        for rule in (spec.get("filters") or [])
    ]
    return {**spec, "variables": variables, "filters": filters}


def validate_spec(cur, *, project_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Validate a specification against the real dataset schema.

    Returns the normalised spec. Raises `SpecInvalid` with a specific reason —
    the system forbids a generic failure here as much as anywhere else.
    """
    method = str(spec.get("method") or "")
    if method not in SUPPORTED_METHODS:
        raise SpecInvalid(
            f"Unknown method {method!r}. Available: {', '.join(sorted(SUPPORTED_METHODS))}"
        )

    version_ids = list(spec.get("dataset_version_ids") or [])
    if len(version_ids) != 1:
        # Multi-dataset analysis needs the  harmonization layer, which does
        # not exist yet. Refusing is better than joining on a guess.
        raise SpecInvalid(
            "Exactly one dataset_version_id is required. Analysing across datasets "
            "needs variable harmonization, which is not implemented yet."
        )

    cur.execute(
        "SELECT dv.id, dv.content_hash, dv.row_count, d.project_id, d.source_id "
        "FROM dataset_versions dv JOIN datasets d ON d.id = dv.dataset_id WHERE dv.id = %s",
        (version_ids[0],),
    )
    version = cur.fetchone()
    if not version:
        raise SpecInvalid(f"Dataset version {version_ids[0]} does not exist.")
    if version["project_id"] != project_id:
        raise SpecInvalid("The dataset version belongs to a different project.")

    variables = dict(spec.get("variables") or {})
    required = METHOD_VARIABLES.get(method, ())
    missing = [role for role in required if not variables.get(role)]
    if missing:
        raise SpecInvalid(f"{method} requires variables: {', '.join(missing)}")

    known = _columns_for(cur, version["id"])
    unknown = [c for c in _referenced_columns(method, variables) if c not in known]
    if unknown:
        raise SpecInvalid(
            f"Unknown {plural(len(unknown), 'column')}: {', '.join(unknown)}. "
            f"Available: {', '.join(sorted({v['name'] for v in known.values()}))}"
        )

    for rule in spec.get("filters") or []:
        if not isinstance(rule, dict) or "column" not in rule or "operator" not in rule:
            raise SpecInvalid("Each filter needs a column and an operator.")
        if rule["operator"] not in FILTER_OPERATORS:
            raise SpecInvalid(
                f"Unsupported filter operator {rule['operator']!r}. "
                f"Supported: {', '.join(sorted(FILTER_OPERATORS))}"
            )
        if rule["column"] not in known:
            raise SpecInvalid(f"Filter references unknown column {rule['column']!r}")

    confidence = float(spec.get("confidence_level", 0.95))
    if not 0 < confidence < 1:
        raise SpecInvalid("confidence_level must be between 0 and 1 (exclusive).")

    normalised = {
        "schema_version": 1,
        "analysis_type": spec.get("analysis_type") or "statistical",
        "research_question": spec.get("research_question") or "",
        "dataset_version_ids": version_ids,
        "variables": variables,
        "filters": list(spec.get("filters") or []),
        "transformations": list(spec.get("transformations") or []),
        "method": method,
        "method_rationale": spec.get("method_rationale") or "",
        "parameters": dict(spec.get("parameters") or {}),
        "confidence_level": confidence,
        "assumptions": list(spec.get("assumptions") or []),
        "outputs_requested": list(spec.get("outputs_requested") or []),
        "visualization_intent": spec.get("visualization_intent") or "",
        "random_seed": int(spec.get("random_seed", 0)),
    }
    normalised["_dataset"] = {
        "source_id": version["source_id"],
        "content_hash": version["content_hash"],
        "row_count": version["row_count"],
    }
    return normalised


def spec_hash(spec: dict[str, Any]) -> str:
    """A stable identity for a specification, ignoring resolution metadata."""
    payload = {k: v for k, v in spec.items() if not k.startswith("_")}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def create_spec(cur, *, project_id: str, spec: dict[str, Any], actor: str) -> dict[str, Any]:
    validated = validate_spec(cur, project_id=project_id, spec=spec)
    spec_id = new_id("asp")
    content_hash = spec_hash(validated)

    cur.execute(
        """
        INSERT INTO analysis_specs
            (id, project_id, schema_version, analysis_type, research_question,
             dataset_version_ids, variables, filters, transformations, method,
             method_rationale, parameters, confidence_level, assumptions,
             outputs_requested, visualization_intent, random_seed, content_hash, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            spec_id, project_id, validated["schema_version"], validated["analysis_type"],
            validated["research_question"], json.dumps(validated["dataset_version_ids"]),
            validated["variables"], json.dumps(validated["filters"]),
            json.dumps(validated["transformations"]), validated["method"],
            validated["method_rationale"], validated["parameters"],
            validated["confidence_level"], json.dumps(validated["assumptions"]),
            json.dumps(validated["outputs_requested"]), validated["visualization_intent"],
            validated["random_seed"], content_hash, actor,
        ),
    )
    audit(cur, project_id=project_id, actor=actor, action="create",
          object_type="analysis_spec", object_id=spec_id)
    return {"spec_id": spec_id, "content_hash": content_hash, "spec": validated}


def load_spec(cur, spec_id: str) -> dict[str, Any]:
    cur.execute("SELECT * FROM analysis_specs WHERE id = %s", (spec_id,))
    row = cur.fetchone()
    if not row:
        raise AnalysisError(f"Unknown analysis spec: {spec_id}")
    return row


def create_run(
    cur, *, project_id: str, spec_id: str, forked_from_run_id: str | None = None,
    fork_reason: str = "",
) -> str:
    """Create a run only from analysis state owned by this project.

    The foreign keys prove the ids exist, but not that their project_id matches
    the run being created. Without this check a domain caller could create a run
    in project A from project B's spec, and record cross-project provenance.
    """
    cur.execute(
        "SELECT project_id FROM analysis_specs WHERE id = %s",
        (spec_id,),
    )
    spec = cur.fetchone()
    if not spec:
        raise AnalysisError(f"Unknown analysis spec: {spec_id}")
    if spec["project_id"] != project_id:
        raise AnalysisError("The analysis spec belongs to a different project.")

    if forked_from_run_id:
        cur.execute(
            "SELECT project_id FROM analysis_runs WHERE id = %s",
            (forked_from_run_id,),
        )
        parent = cur.fetchone()
        if not parent:
            raise AnalysisError(f"Unknown analysis run: {forked_from_run_id}")
        if parent["project_id"] != project_id:
            raise AnalysisError("The forked analysis run belongs to a different project.")

    run_id = new_id("arun")
    cur.execute(
        "INSERT INTO analysis_runs (id, project_id, spec_id, forked_from_run_id, "
        "fork_reason, status) VALUES (%s, %s, %s, %s, %s, 'queued')",
        (run_id, project_id, spec_id, forked_from_run_id, fork_reason),
    )
    return run_id


def record_result(
    cur, *, run_id: str, sandbox: Any, spec_row: dict[str, Any], actor: str,
) -> dict[str, Any]:
    """Persist a completed run and commit its provenance.

    The analysis becomes a research object derived from the dataset version, so
    anything later built on this number can be traced back to the rows and the
    code that produced it.
    """
    cur.execute("SELECT project_id, spec_id FROM analysis_runs WHERE id = %s", (run_id,))
    run = cur.fetchone()
    if not run:
        raise AnalysisError(f"Unknown analysis run: {run_id}")
    project_id = run["project_id"]

    payload = sandbox.payload or {}
    ok = bool(sandbox.ok)
    result = payload.get("result") or {}
    runtime = payload.get("runtime") or {}

    if ok:
        object_id = create_object(
            cur, project_id=project_id, object_type=ObjectType.ANALYSIS,
            title=f"{spec_row['method']} — {spec_row.get('research_question') or 'analysis'}"[:2000],
            actor=actor,
            metadata={"method": spec_row["method"], "run_id": run_id},
        )
        # Lineage: the analysis was calculated from the dataset version's object.
        version_ids = spec_row["dataset_version_ids"]
        if isinstance(version_ids, str):
            version_ids = json.loads(version_ids)
        for version_id in version_ids:
            cur.execute(
                "SELECT d.object_id FROM dataset_versions dv "
                "JOIN datasets d ON d.id = dv.dataset_id WHERE dv.id = %s",
                (version_id,),
            )
            row = cur.fetchone()
            if row and row["object_id"]:
                add_edge(cur, project_id=project_id,
                         source_artifact_id=row["object_id"],
                         target_artifact_id=object_id,
                         lineage_type=LineageType.CALCULATED_FROM,
                         metadata={"dataset_version_id": version_id, "run_id": run_id})
    else:
        object_id = None

    cur.execute(
        """
        UPDATE analysis_runs SET
            status = %s, object_id = %s, runtime = %s, dependency_versions = %s,
            environment = %s, sandbox_policy = %s, random_seed = %s, input_hashes = %s,
            result = %s, logs = %s, warnings = %s, error = %s, duration_ms = %s,
            started_at = COALESCE(started_at, now()), finished_at = now()
        WHERE id = %s
        """,
        (
            RUN_COMPLETED if ok else RUN_FAILED, object_id,
            runtime.get("python", ""), runtime,
            {
                "sandbox_notes": payload.get("sandbox_notes") or [],
                "throughline": dict(installation_version.current()),
            },
            sandbox.policy, int(payload.get("random_seed", spec_row.get("random_seed", 0))),
            {"dataset_content_hash": (spec_row.get("_dataset") or {}).get("content_hash"),
             "spec_content_hash": spec_row.get("content_hash")},
            result, (sandbox.stderr or "")[:8000],
            json.dumps(result.get("warnings") or []),
            None if ok else str(payload.get("error") or "Analysis failed"),
            int(payload.get("duration_ms") or sandbox.duration_ms), run_id,
        ),
    )

    # / — assumption checks are first-class rows, queryable on their own.
    for check in result.get("assumptions") or []:
        cur.execute(
            """
            INSERT INTO assumption_checks
                (id, run_id, name, description, outcome, statistic, p_value, detail, severity)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, name) DO NOTHING
            """,
            (new_id("asc"), run_id, check.get("name", "unnamed"),
             check.get("description", ""), check.get("outcome", "not_testable"),
             check.get("statistic"), check.get("p_value"),
             check.get("detail", ""), check.get("severity", "informational")),
        )

    # The look this run was recorded as, now that there is a number to correct.
    # Counted when the analysis was specified — before the sandbox ran, so it
    # could not be un-counted once the result was known — and the p-value
    # arrives here. Imported inside the function because `exploration` imports
    # `benjamini_hochberg` from `discovery`, which imports this module.
    from .exploration import attach_result

    attach_result(cur, analysis_run_id=run_id,
                  p_value=result.get("p_value") if ok else None)

    emit(cur, project_id=project_id,
         event_type="AnalysisCompleted" if ok else "AnalysisFailed",
         payload={"run_id": run_id, "method": spec_row["method"], "object_id": object_id})
    return {"run_id": run_id, "status": "completed" if ok else "failed",
            "object_id": object_id, "result": result}


def get_run(cur, run_id: str) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT r.*, s.method, s.variables, s.research_question, s.method_rationale,
               s.dataset_version_ids, s.filters, s.confidence_level, s.content_hash AS spec_hash
        FROM analysis_runs r JOIN analysis_specs s ON s.id = r.spec_id
        WHERE r.id = %s
        """,
        (run_id,),
    )
    run = cur.fetchone()
    if not run:
        return None
    cur.execute(
        "SELECT name, description, outcome, statistic, p_value, detail, severity "
        "FROM assumption_checks WHERE run_id = %s ORDER BY name",
        (run_id,),
    )
    run["assumption_checks"] = list(cur.fetchall())
    return run


def list_runs(cur, project_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """Every analysis run in a project, newest first.

    The interface listed analyses by reading `connections` and keeping the ones
    that carried an `analysis_run_id`. That is a list of *discovery's* runs, not
    of the project's: a run specified by hand belongs to no connection, so it
    would have been queued, executed, recorded — and never shown. The list has
    to come from `analysis_runs` for a run to be able to appear in it.

    `origin` is carried because the difference matters when reading. A run that
    came out of a sweep was corrected inside a family of tests; one a researcher
    specified stands alone, and one is a fork of another. Reporting all three as
    an undifferentiated list of numbers would flatten exactly the distinction
    §47 turns on.
    """
    cur.execute(
        """
        SELECT r.id, r.status, r.error, r.created_at, r.finished_at,
               r.forked_from_run_id, r.fork_reason, r.result,
               s.method, s.variables, s.research_question,
               c.id AS connection_id, c.left_variable, c.right_variable
        FROM analysis_runs r
        JOIN analysis_specs s ON s.id = r.spec_id
        LEFT JOIN connections c ON c.analysis_run_id = r.id
        WHERE r.project_id = %s
        ORDER BY r.created_at DESC, r.id DESC
        LIMIT %s
        """,
        (project_id, limit),
    )
    runs = []
    for row in cur.fetchall():
        row = dict(row)
        if row["forked_from_run_id"]:
            row["origin"] = "fork"
        elif row["connection_id"]:
            row["origin"] = "discovery"
        else:
            row["origin"] = "specified"
        result = row.pop("result", None) or {}
        # Enough to read the row without a second request per run, and no more:
        # the full result belongs to the detail screen, which shows it beside
        # the assumption checks that qualify it.
        row["estimate"] = result.get("estimate")
        row["estimate_name"] = result.get("estimate_name")
        row["p_value"] = result.get("p_value")
        row["sample_size"] = result.get("sample_size")
        runs.append(row)
    return runs


def compare_runs(cur, run_ids: Sequence[str]) -> dict[str, Any]:
    """ — put forked analyses side by side.

    Sensitivity analysis is exactly this: same question, different defensible
    choices, and an honest look at whether the conclusion survives.
    """
    rows = []
    for run_id in run_ids:
        run = get_run(cur, run_id)
        if not run:
            raise AnalysisError(f"Unknown analysis run: {run_id}")
        result = run["result"] or {}
        rows.append({
            "run_id": run_id,
            "status": run["status"],
            "method": run["method"],
            "fork_reason": run["fork_reason"],
            "estimate": result.get("estimate"),
            "estimate_name": result.get("estimate_name"),
            "ci_low": result.get("ci_low"),
            "ci_high": result.get("ci_high"),
            "p_value": result.get("p_value"),
            "sample_size": result.get("sample_size"),
            "effect_size": result.get("effect_size"),
            "evidence_quality": result.get("evidence_quality"),
            "statistically_significant": result.get("statistically_significant"),
        })

    significance = {r["statistically_significant"] for r in rows if r["status"] == "completed"}
    return {
        "runs": rows,
        # The point of a fork comparison is whether the conclusion held, so say it.
        "conclusion_stable": len(significance) <= 1,
        "note": ("All completed branches agree on statistical significance."
                 if len(significance) <= 1 else
                 "Branches disagree on significance — the conclusion depends on an "
                 "analytical choice and should be reported as such."),
    }
