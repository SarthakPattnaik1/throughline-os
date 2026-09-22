"""
Every route that takes an id checks it against the caller (T185).

T161, T162 and T164 each found another account's objects reachable through a
route that scoped its *project* and then read an object by id alone — in the
path, then in the body, then in a note. Each was found one class at a time, by
reading. This sweeps instead, from the route table:

1. Two accounts get identical projects: one of every object a route can name.
2. For each route that takes an object id, a **control** call is made with the
   caller's own ids. It has to succeed, or the sweep proves nothing about that
   route — a request refused for a bad body is refused for everyone.
3. Then each id, one at a time, is swapped for the other account's. That call
   must not succeed.

A route the generator cannot exercise is not silently passed: it has to be
written down in `COVERED_ELSEWHERE` with the test that covers it, or in
`NOT_AN_OBJECT` if the parameter names no object.
"""

from __future__ import annotations

import importlib
import json
import re
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from throughline_domain.db import connection
from throughline_domain.ids import new_id

# --------------------------------------------------------------------------
# What each id parameter names
# --------------------------------------------------------------------------

#: Parameter name → the kind of object it names. `run_id` is resolved by path.
KIND = {
    "object_id": "object", "version_id": "version", "dataset_version_id": "version",
    "dataset_version_ids": "version", "left_dataset_version_id": "version",
    "right_dataset_version_id": "version2", "source_id": "source", "source_ids": "source",
    "left_source_id": "source", "right_source_id": "source2", "target_id": "object",
    "event_id": "retrieval", "artifact_id": "artifact", "report_id": "validation",
    "finding_id": "finding", "region_id": "region", "mark_id": "mark", "note_id": "note",
    "canonical_variable_id": "canonical", "alias_id": "alias", "enquiry_id": "enquiry",
    "preregistration_id": "preregistration", "connection_id": "connection",
    "left_connection_id": "connection", "right_connection_id": "connection2",
    "parent_id": "cohort", "analysis_run_id": "run", "visual_id": "visual",
    "visual_ids": "visual",
    "mapping_id": "mapping", "project_id": "project",
}

#: Parameters that name no object of a project, and what they name instead.
NOT_AN_OBJECT = {
    "name": "a feature pack of this installation",
    "table": "a table inside the caller's own source file",
    "node_name": "a step of a workflow, scoped by its run",
    "arxiv_id": "an identifier in an external catalogue",
}


def _run_kind(path: str) -> str:
    if path.startswith("/api/discoveries/"):
        return "discovery"
    if path.startswith("/api/workflows/"):
        return "workflow"
    return "run"


# --------------------------------------------------------------------------
# One of everything
# --------------------------------------------------------------------------

CSV = ("country,x,y\n" + "".join(f"{c},{i},{i * 1.5 + (i % 3)}\n" for i, c in enumerate(
    ["IND", "USA", "GBR", "FRA", "DEU", "BRA", "JPN", "ZAF", "KEN", "MEX", "CAN", "AUS"], 1))
       ).encode()


def build(cur, *, user_id: str, project_id: str) -> dict[str, str]:
    """One of every object a route can name, in `project_id` — two where a route takes a pair."""
    import io

    from throughline_domain import storage, visuals

    ids: dict[str, str] = {"project": project_id}

    def insert(kind: str, table: str, columns: dict[str, Any], prefix: str) -> str:
        ids[kind] = new_id(prefix)
        values = {"id": ids[kind], **columns}
        cur.execute(
            f"INSERT INTO {table}({', '.join(values)}) "
            f"VALUES ({', '.join(['%s'] * len(values))})", list(values.values()))
        return ids[kind]

    insert("object", "research_objects", {"project_id": project_id, "object_type": "dataset",
           "title": "Panel", "created_by": user_id}, "obj")
    for suffix in ("", "2"):
        insert("source" + suffix, "sources", {"project_id": project_id, "source_type": "paper",
               "title": "Paper" + suffix, "ingestion_status": "ready",
               # An open-access copy on record, so the full-text route can act.
               "metadata": json.dumps({"pdf_url": "https://arxiv.org/pdf/2401.00001"})},
               "src")
        for ordinal in range(3):
            cur.execute(
                "INSERT INTO passages(id, project_id, source_id, ordinal, kind, locator, "
                "section, content, metadata) VALUES (%s, %s, %s, %s, 'paragraph', %s, "
                "'Results', %s, '{}')",
                (new_id("psg"), project_id, ids["source" + suffix], ordinal, f"p. {ordinal + 1}",
                 "Consumption rose with resistance across the eight countries studied."))
        cur.execute(
            "INSERT INTO paper_extractions(id, project_id, source_id, fields, model, "
            "prompt_name, prompt_version) VALUES (%s, %s, %s, %s, 'test', 'extract', 1)",
            (new_id("pex"), project_id, ids["source" + suffix],
             json.dumps({"results": {"quote": "Consumption rose.", "locator": "p. 1"}})))
        # The second differs by a row: one project stores a given file once.
        data = CSV if not suffix else CSV + b"NZL,13,20.5\n"
        content_hash, storage_key, size = storage.put(io.BytesIO(data))
        # A dataset arrives as its own uploaded source, with the file behind it.
        insert("file" + suffix, "files", {"project_id": project_id,
               "content_hash": content_hash, "filename": "panel.csv",
               "media_type": "text/csv", "size_bytes": size,
               "storage_key": storage_key}, "file")
        insert("dataset_source" + suffix, "sources", {"project_id": project_id,
               "source_type": "upload", "title": "panel.csv", "ingestion_status": "ready",
               "file_id": ids["file" + suffix], "content_hash": content_hash}, "src")
        insert("dataset" + suffix, "datasets", {"project_id": project_id,
               "source_id": ids["dataset_source" + suffix], "object_id": ids["object"],
               "name": "panel" + suffix, "format": "csv"}, "dst")
        insert("version" + suffix, "dataset_versions", {"dataset_id": ids["dataset" + suffix],
               "version": 1, "content_hash": content_hash, "storage_key": storage_key,
               "row_count": 12, "column_count": 3}, "dsv")
        for ordinal, (column, semantic) in enumerate(
                (("country", "geography"), ("x", "continuous"), ("y", "continuous"))):
            insert(f"column_{column}{suffix}", "dataset_columns", {
                   "dataset_version_id": ids["version" + suffix], "ordinal": ordinal,
                   "name": column, "original_name": column,
                   "physical_type": "string" if column == "country" else "double",
                   "semantic_type": semantic}, "dcol")
        insert("spec" + suffix, "analysis_specs", {"project_id": project_id,
               "analysis_type": "correlation", "method": "pearson_correlation",
               "dataset_version_ids": json.dumps([ids["version" + suffix]]),
               "variables": json.dumps({"x": "x", "y": "y"}),
               "content_hash": "0" * 64, "created_by": user_id}, "aspec")
        insert("run" + suffix, "analysis_runs", {"project_id": project_id,
               "spec_id": ids["spec" + suffix], "status": "completed", "object_id": ids["object"],
               "input_hashes": json.dumps({
                   "dataset_content_hash": content_hash,
                   "spec_content_hash": "0" * 64,
               }),
               "result": json.dumps({"method": "pearson_correlation", "estimate": 0.5,
                                     "estimate_name": "pearson_r", "p_value": 0.01,
                                     "sample_size": 8, "evidence_quality": "weak",
                                     "extra": {"x": "x", "y": "y"}})}, "arun")
    ids["column"] = ids["column_x"]
    cur.execute("INSERT INTO board_placements(id, project_id, object_id, x, y, width, height, "
                "updated_by) VALUES (%s, %s, %s, 0, 0, 200, 200, %s)",
                (new_id("bpl"), project_id, ids["object"], user_id))
    recommendation = visuals.recommend_for_run(cur, analysis_run_id=ids["run"])
    ids["visual"] = visuals.create_visual(
        cur, project_id=project_id, spec=recommendation["spec"], actor=user_id,
        recommendation=recommendation)["visual_id"]
    from throughline_domain import objects
    ids["object_latest"] = objects.new_version(cur, object_id=ids["object"], actor=user_id,
                                               title="Panel, revised", reason="sweep")
    insert("finding", "findings", {"project_id": project_id, "title": "Finding",
           "finding_type": "statistical"}, "fnd")
    for suffix in ("", "2"):
        insert("connection" + suffix, "connections", {"project_id": project_id,
               "left_variable": "x", "right_variable": "y" + suffix,
               "method": "pearson_correlation", "analysis_run_id": ids["run"],
               "estimate": 0.5, "q_value": 0.01}, "conn")
    insert("artifact", "communication_artifacts", {"project_id": project_id,
           "artifact_type": "report", "title": "Report"}, "art")
    insert("enquiry", "enquiries", {"project_id": project_id, "name": "Enquiry"}, "enq")
    insert("note", "notes", {"project_id": project_id, "object_type": "dataset",
           "object_id": ids["object"], "body": "A note.", "author": user_id,
           "note_kind": "note", "title": "Page"}, "note")
    insert("region", "board_regions", {"project_id": project_id, "name": "Region", "x": 0,
           "y": 0, "width": 200, "height": 200, "updated_by": user_id}, "breg")
    insert("mark", "paper_marks", {"project_id": project_id, "source_id": ids["source"],
           "page": 1, "kind": "underline", "points": json.dumps([[0, 0], [1, 1]]),
           "created_by": user_id}, "mark")
    insert("canonical", "canonical_variables", {"project_id": project_id,
           "name": f"var_{ids['project'][-6:]}", "semantic_type": "continuous"}, "cvar")
    insert("alias", "variable_aliases", {"project_id": project_id,
           "canonical_variable_id": ids["canonical"], "alias": "x alias",
           "created_by": user_id}, "valias")
    insert("mapping", "variable_mappings", {"project_id": project_id,
           "dataset_column_id": ids["column"], "canonical_variable_id": ids["canonical"]},
           "vmap")
    insert("preregistration", "preregistrations", {"project_id": project_id,
           "hypothesis": "x relates to y", "predicted_direction": "increase",
           "locked_hash": "0" * 64}, "prereg")
    insert("cohort", "cohorts", {"project_id": project_id, "dataset_version_id": ids["version"],
           "name": "Cohort", "created_by": user_id}, "coh")
    insert("retrieval", "retrieval_events", {"project_id": project_id, "query": "q",
           "strategy": "lexical"}, "ret")
    insert("validation", "validation_reports", {"project_id": project_id,
           "connection_id": ids["connection"]}, "vrep")
    insert("discovery", "discovery_runs", {"project_id": project_id,
           "dataset_version_id": ids["version"]}, "drun")
    insert("workflow", "workflow_runs", {"project_id": project_id,
           "workflow_name": "system.echo", "state": "awaiting_approval"}, "wrun")
    cur.execute("INSERT INTO workflow_nodes(id, run_id, node_name, sequence, state, input, "
                "output, attempts, requires_approval) VALUES (%s, %s, 'echo', 0, "
                "'awaiting_approval', '{}', '{}', 0, true)", (new_id("wnode"), ids["workflow"]))
    return ids


# --------------------------------------------------------------------------
# The route table
# --------------------------------------------------------------------------

def _model_fields(route: APIRoute) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for param in route.dependant.body_params:
        model = getattr(param, "type_", None) \
            or getattr(getattr(param, "field_info", None), "annotation", None)
        fields.update(getattr(model, "model_fields", {}) or {})
    return fields


def id_routes() -> list[tuple[str, APIRoute]]:
    app = importlib.import_module("throughline_api.app").app
    found = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        names = ([p for p in re.findall(r"\{(\w+)\}", route.path) if p != "project_id"]
                 + [p.name for p in route.dependant.query_params
                    if p.name.endswith(("_id", "_ids"))]
                 + [n for n in _model_fields(route) if n.endswith(("_id", "_ids"))])
        if names:
            for method in sorted(route.methods):
                found.append((method, route))
    return found


def id_parameters(route: APIRoute) -> list[tuple[str, str]]:
    """(where, name) for every id-looking parameter of a route."""
    out = [("path", p) for p in re.findall(r"\{(\w+)\}", route.path)]
    out += [("query", p.name) for p in route.dependant.query_params
            if p.name.endswith(("_id", "_ids"))]
    out += [("body", n) for n in _model_fields(route) if n.endswith(("_id", "_ids"))]
    return out


def test_every_id_parameter_is_classified():
    """A new id parameter is either mapped to what it names, or said not to name one."""
    unknown = sorted({name for _, route in id_routes() for _, name in id_parameters(route)
                      if name not in KIND and name not in NOT_AN_OBJECT
                      and name != "run_id"})
    assert unknown == [], f"classify these parameters: {unknown}"


# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------

#: Where one route's parameter names a different kind than the name suggests.
KIND_BY_ROUTE = {
    # A research object's version, not a dataset's: its history is by object.
    ("POST /api/projects/{project_id}/objects/{object_id}/restore", "version_id"): "object",
    ("POST /api/projects/{project_id}/objects/{object_id}/restore", "object_id"): "object_latest",
}

#: Routes that compare, and so take at least two of a kind.
PAIRS = {
    "POST /api/projects/{project_id}/synthesis", "POST /api/projects/{project_id}/synthesis/key-points",
    "POST /api/projects/{project_id}/dataset-synthesis", "POST /api/projects/{project_id}/image-comparison",
    "GET /api/projects/{project_id}/analyses/compare",
}


def _kind(key: str, route: APIRoute, name: str) -> str:
    if (key, name) in KIND_BY_ROUTE:
        return KIND_BY_ROUTE[(key, name)]
    return _run_kind(route.path) if name == "run_id" else KIND[name]


def _id_for(ids: dict[str, str], mine: dict[str, str], key: str, route: APIRoute,
            name: str, swapped: bool) -> Any:
    kind = _kind(key, route, name)
    if key in PAIRS:
        # Two of them, because these routes compare; the swap is the first.
        return [ids[kind], mine.get(kind + "2", mine[kind])]
    if name.endswith("_ids"):
        return [ids[kind]]
    return ids[kind]


def _fill(schema: dict[str, Any], defs: dict[str, Any]) -> Any:
    """The smallest value a JSON schema accepts, for fields nobody templated."""
    if "$ref" in schema:
        return _fill(defs[schema["$ref"].split("/")[-1]], defs)
    if "default" in schema:
        return schema["default"]
    if "anyOf" in schema:
        options = [s for s in schema["anyOf"] if s.get("type") != "null"]
        return _fill(options[0], defs) if options else None
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object":
        required = schema.get("required", [])
        return {k: _fill(v, defs) for k, v in schema.get("properties", {}).items()
                if k in required}
    if kind == "array":
        return []
    if kind == "integer":
        return max(1, schema.get("minimum", 1))
    if kind == "number":
        return float(schema.get("minimum", 1))
    if kind == "boolean":
        return False
    return "x" * max(1, schema.get("minLength", 1))


#: Bodies a generator cannot guess — a real method name, a real variable.
BODIES: dict[str, dict[str, Any]] = {
    "PUT /api/projects/{project_id}/board": {"x": 0, "y": 0, "width": 200, "height": 200},
    "POST /api/projects/{project_id}/objects/{object_id}/restore": {"reason": "sweep"},
    "POST /api/projects/{project_id}/marks": {"page": 1, "kind": "underline",
                                              "points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}]},
    "POST /api/projects/{project_id}/excerpts": {"page": 1, "citation": "p. 1",
        "region": {"x": 40, "y": 60, "width": 400, "height": 300}},
    "POST /api/vocabulary/{alias_id}/decide": {"status": "approved"},
    "POST /api/findings/{finding_id}/transition": {"to_status": "deprecated", "reason": "sweep"},
    "POST /api/projects/{project_id}/analyses": {"method": "pearson_correlation",
                                                 "variables": {"x": "x", "y": "y"}},
    "POST /api/analyses/{run_id}/fork": {"reason": "sweep"},
    "POST /api/projects/{project_id}/cohorts": {
        "name": "Sweep subset", "definition": [{"column": "x", "min": 1, "max": 6}]},
    "POST /api/projects/{project_id}/specification-curve": {"outcome": "y", "exposure": "x"},
    "PATCH /api/visuals/{visual_id}": {"changes": {"title": "Retitled"}},
    "PATCH /api/notes/{note_id}": {"body": "Edited."},
}

#: Query parameters a route requires besides its ids.
QUERIES: dict[str, dict[str, Any]] = {
    "GET /api/projects/{project_id}/search": {"q": "consumption"},
    "GET /api/dataset-versions/{version_id}/by-place": {"place": "country", "value": "x"},
    "GET /api/dataset-versions/{version_id}/density": {"column": "x"},
}


def request_for(method: str, route: APIRoute, mine: dict[str, str],
                swap: tuple[str, str] | None = None,
                theirs: dict[str, str] | None = None) -> tuple[str, dict, Any]:
    """The route's URL, query and body with the caller's ids — one swapped, if asked."""
    key = f"{method} {route.path}"

    def value(where: str, name: str) -> Any:
        swapped = swap == (where, name)
        return _id_for(theirs if swapped else mine, mine, key, route, name, swapped)

    path = route.path
    for name in re.findall(r"\{(\w+)\}", route.path):
        if name == "project_id":
            replacement = mine["project"]
        elif name in NOT_AN_OBJECT:
            replacement = {"name": "parquet", "table": "Table", "node_name": "echo"}[name]
        else:
            replacement = value("path", name)
        path = path.replace("{" + name + "}", str(replacement))

    query = {**QUERIES.get(key, {}),
             **{p.name: value("query", p.name) for p in route.dependant.query_params
                if p.name.endswith(("_id", "_ids"))}}

    body = None
    fields = _model_fields(route)
    if fields:
        model = next(getattr(p, "type_", None)
                     or getattr(getattr(p, "field_info", None), "annotation", None)
                     for p in route.dependant.body_params)
        schema = model.model_json_schema()
        body = _fill(schema, schema.get("$defs", {}))
        body.update(BODIES.get(key, {}))
        for name in fields:
            if name.endswith(("_id", "_ids")) and name not in NOT_AN_OBJECT:
                body[name] = value("body", name)
    return path, query, body


# --------------------------------------------------------------------------
# The accounts
# --------------------------------------------------------------------------

PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def nothing_touches_the_machine(monkeypatch):
    """
    The sweep calls routes it did not choose, so nothing it calls may install.

    An early version of this sweep called the feature-pack install route, which
    ran a real `pip install` of pyarrow into the development environment; with
    pyarrow present pandas stores text differently, and an unrelated memory
    test began to fail. The pack routes are no longer swept, and this makes
    sure a route that reaches the machine fails the test instead of acting.
    """
    from throughline_domain import extras, launchers

    def refuse(*args, **kwargs):
        raise AssertionError("a machine-level action ran during the id sweep")

    monkeypatch.setattr(extras, "install_in_background", refuse)
    monkeypatch.setattr(launchers, "install_desktop_entry", refuse)

    # Nor the network: a route that fetches a paper gets a stand-in document.
    from throughline_connectors import papers

    monkeypatch.setattr(papers, "fetch_pdf", lambda url, **_: b"%PDF-1.4 sweep stand-in")


@pytest.fixture(scope="module")
def signed_in():
    """Two accounts, and a client signed in as the first."""
    from throughline_api.app import app
    from throughline_domain import auth

    with connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM users")
        me = auth.create_user(cur, email="me@sweep.local", display_name="Me",
                              password=PASSWORD)
        them = auth.create_user(cur, email="them@sweep.local", display_name="Them",
                                password=PASSWORD)
    with TestClient(app, raise_server_exceptions=False) as client:
        signed = client.post("/api/auth/login", json={"email": "me@sweep.local",
                                                      "password": PASSWORD})
        assert signed.status_code == 200, signed.text
        yield client, me["id"], them["id"]
    with connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM users")


@pytest.fixture()
def world(signed_in):
    """Fresh identical projects for each case: a DELETE control consumes its object."""
    client, me, them = signed_in
    with connection() as conn, conn.cursor() as cur:
        projects = {}
        for owner in (me, them):
            projects[owner] = new_id("prj")
            cur.execute("INSERT INTO projects(id, owner_user_id, name) VALUES (%s, %s, 'P')",
                        (projects[owner], owner))
        mine = build(cur, user_id=me, project_id=projects[me])
        theirs = build(cur, user_id=them, project_id=projects[them])
    return client, mine, theirs


def object_parameters(route: APIRoute) -> list[tuple[str, str]]:
    return [(where, name) for where, name in id_parameters(route)
            if name not in NOT_AN_OBJECT and not (where == "path" and name == "project_id")]


CASES = [pytest.param(method, route, where, name,
                      id=f"{method} {route.path} [{where} {name}]")
         for method, route in id_routes()
         for where, name in object_parameters(route)]


#: Parameters a route accepts from anyone and then treats as absent, by design,
#: with the response an id that never existed gets.
#:
#: An analysis that claims a registration nobody wrote still runs and is still
#: counted — a look at the data happened — as exploratory, with the reason said
#: (`test_claiming_a_registration_that_does_not_exist_is_refused_not_believed`).
#: Another project's registration must get exactly that, and not the exemption
#: it used to grant, which is also what keeps its existence unconfirmed.
ABSENT_WHEN_FOREIGN: dict[tuple[str, str], dict[str, Any]] = {
    ("POST /api/projects/{project_id}/analyses", "preregistration_id"):
        {"confirmatory": False, "standing": "No such pre-registration"},
}


#: Routes whose control call cannot succeed here, and why that is not a gap.
#:
#: The foreign call is still made for each of these and still has to be refused:
#: what the entry excuses is only that the refusal cannot be compared with a
#: success, because the caller's own request stops earlier for a reason that
#: is not about whose ids they are.
COVERED_ELSEWHERE: dict[str, str] = {
    "GET /api/projects/{project_id}/graph/path":
        "No graph projection is configured in tests (503). The query matches both "
        "ends on `project_id: $p` in `graph_projection.shortest_path`.",
    "GET /api/projects/{project_id}/graph/reachable":
        "No graph projection in tests (503). The start is matched on `project_id: $p`.",
    "GET /api/projects/{project_id}/sources/{source_id}/tables":
        "Needs an uploaded SQLite file. `_database_file` selects on id AND project_id.",
    "POST /api/projects/{project_id}/sources/{source_id}/tables/{table}":
        "Needs an uploaded SQLite file. `_database_file` selects on id AND project_id.",
    "POST /api/projects/{project_id}/image-comparison":
        "Needs stored image files. Each source is selected on id AND project_id "
        "before its file is checked.",
    "GET /api/visuals/{visual_id}/scene.zip":
        "Only a fitted surface exports geometry (400). The visual's own project is "
        "scoped before the surface check.",
    "POST /api/visuals/{visual_id}/blender-render":
        "Only a fitted surface renders through Blender (400). Scoped before that check.",
    "GET /api/visuals/{visual_id}/blender-render.png":
        "Nothing has been rendered (404). Scoped before the file is looked for.",
}


@pytest.mark.parametrize("method,route,where,name", CASES)
def test_another_accounts_id_is_refused(world, method, route, where, name):
    client, mine, theirs = world
    key = f"{method} {route.path}"

    path, query, body = request_for(method, route, mine)
    control = client.request(method, path, params=query, json=body)
    if not 200 <= control.status_code < 300:
        assert key in COVERED_ELSEWHERE, (
            f"{key}: the control call with the caller's own ids answered "
            f"{control.status_code}, so nothing here proves the {where} {name} is "
            f"checked. Give it a body in BODIES, or say in COVERED_ELSEWHERE why "
            f"it cannot succeed here. {control.text[:200]}")

    path, query, body = request_for(method, route, mine, swap=(where, name), theirs=theirs)
    foreign = client.request(method, path, params=query, json=body)
    if (key, name) in ABSENT_WHEN_FOREIGN:
        # Not refused, by design — but treated exactly as an id nobody wrote.
        expected = ABSENT_WHEN_FOREIGN[(key, name)]
        assert 200 <= foreign.status_code < 300, foreign.text[:300]
        for field, value in expected.items():
            assert value in str(foreign.json()[field]) if isinstance(value, str) \
                else foreign.json()[field] == value, (key, field, foreign.text[:300])
        return
    assert not 200 <= foreign.status_code < 300, (
        f"{key}: another account's {name} in the {where} was accepted "
        f"({foreign.status_code}): {foreign.text[:300]}")
    assert foreign.status_code != 500, f"{key}: {foreign.text[:300]}"


# --------------------------------------------------------------------------
# The two that wrote, checked where they write
# --------------------------------------------------------------------------

def _other_project(cur) -> tuple[str, str]:
    user_id, project_id = new_id("usr"), new_id("prj")
    cur.execute("INSERT INTO users(id, email, display_name, password_hash, password_salt) "
                "VALUES (%s, %s, 'Other', 'x', 'y')", (user_id, f"{user_id}@t.local"))
    cur.execute("INSERT INTO projects(id, owner_user_id, name) VALUES (%s, %s, 'Theirs')",
                (project_id, user_id))
    return user_id, project_id


def test_another_projects_registration_does_not_make_a_look_confirmatory(cur, project):
    """
    `exploration.record` read the claimed registration by id alone, so a look in
    your project could be exempted from correction by a plan another account
    registered. It is now absent unless it is this project's.
    """
    from throughline_domain import enquiry, exploration

    other_user, other_project = _other_project(cur)
    theirs = exploration.preregister(cur, project_id=other_project,
                                     hypothesis="Consumption predicts resistance.",
                                     predicted_direction="increase", author=other_user)
    mine = enquiry.open_new(cur, project_id=project)["id"]

    look = exploration.record(cur, enquiry_id=mine, project_id=project,
                              verb=exploration.VERBS[0], description="A look.",
                              p_value=0.01, preregistration_id=theirs["id"])

    assert look["recorded"]["confirmatory"] is False
    assert "No such pre-registration" in look["recorded"]["why"]


def test_a_figure_cannot_be_filed_against_another_projects_finding(cur, project):
    from throughline_domain import visuals

    cur.execute("SELECT owner_user_id FROM projects WHERE id = %s", (project,))
    me = cur.fetchone()["owner_user_id"]
    other_user, other_project = _other_project(cur)
    mine = build(cur, user_id=me, project_id=project)
    theirs = build(cur, user_id=other_user, project_id=other_project)
    recommendation = visuals.recommend_for_run(cur, analysis_run_id=mine["run"])

    with pytest.raises(visuals.VisualError, match="different project"):
        visuals.create_visual(cur, project_id=project, spec=recommendation["spec"],
                              actor=me, recommendation=recommendation,
                              finding_id=theirs["finding"])

