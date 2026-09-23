"""Critical provenance invariants that must fail closed."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from throughline_domain import analysis, corpus, lineage, objects, storage
from throughline_domain.ids import new_id
from throughline_schemas.enums import LineageType, ObjectType, SourceType


def _second_project(cur, project_id: str) -> str:
    cur.execute("SELECT owner_user_id FROM projects WHERE id = %s", (project_id,))
    owner = cur.fetchone()["owner_user_id"]
    other = new_id("prj")
    cur.execute(
        "INSERT INTO projects(id, owner_user_id, name) VALUES (%s, %s, 'Other')",
        (other, owner),
    )
    return other


def _source(cur, project_id: str, title: str = "source.csv") -> str:
    source_id = new_id("src")
    cur.execute(
        "INSERT INTO sources(id, project_id, source_type, title, ingestion_status) "
        "VALUES (%s, %s, 'upload', %s, 'ready')",
        (source_id, project_id, title),
    )
    return source_id


def test_reingesting_a_dataset_keeps_one_dataset_object(cur, project):
    source_id = _source(cur, project)
    profile = SimpleNamespace(
        format="csv",
        row_count=2,
        column_count=0,
        quality_report={},
        columns=[],
    )

    first = corpus.store_dataset(
        cur,
        project_id=project,
        source_id=source_id,
        name="panel.csv",
        profile=profile,
        content_hash="a" * 64,
        storage_key="aa/aa/" + "a" * 64,
        actor="test",
    )
    second = corpus.store_dataset(
        cur,
        project_id=project,
        source_id=source_id,
        name="panel.csv",
        profile=profile,
        content_hash="a" * 64,
        storage_key="aa/aa/" + "a" * 64,
        actor="test",
    )

    assert first["object_id"] == second["object_id"]
    assert (first["version"], second["version"]) == (1, 2)

    cur.execute(
        "SELECT count(*) AS n FROM research_objects "
        "WHERE source_id = %s AND object_type = 'dataset'",
        (source_id,),
    )
    assert cur.fetchone()["n"] == 1

    cur.execute(
        "SELECT version FROM dataset_versions WHERE dataset_id = %s ORDER BY version",
        (first["dataset_id"],),
    )
    assert [row["version"] for row in cur.fetchall()] == [1, 2]


def test_reingesting_a_paper_keeps_one_paper_object(cur, project):
    source_id = _source(cur, project, "paper.pdf")
    parsed = SimpleNamespace(
        title="A study",
        page_count=3,
        metadata={"parser": "test"},
    )

    first = corpus.store_paper(
        cur, project_id=project, source_id=source_id, parsed=parsed, actor="test"
    )
    second = corpus.store_paper(
        cur, project_id=project, source_id=source_id, parsed=parsed, actor="test"
    )

    assert first["object_id"] == second["object_id"]
    cur.execute(
        "SELECT count(*) AS n FROM research_objects "
        "WHERE source_id = %s AND object_type = 'paper'",
        (source_id,),
    )
    assert cur.fetchone()["n"] == 1


def test_lineage_cannot_cross_project_boundary(cur, project):
    other = _second_project(cur, project)
    left = objects.create_object(
        cur, project_id=project, object_type=ObjectType.DATASET,
        title="Mine", actor="test",
    )
    right = objects.create_object(
        cur, project_id=other, object_type=ObjectType.ANALYSIS,
        title="Theirs", actor="test",
    )

    with pytest.raises(lineage.LineageError, match="project"):
        lineage.add_edge(
            cur,
            project_id=project,
            source_artifact_id=left,
            target_artifact_id=right,
            lineage_type=LineageType.CALCULATED_FROM,
        )


def test_database_itself_refuses_cross_project_lineage(cur, project):
    other = _second_project(cur, project)
    left = objects.create_object(
        cur, project_id=project, object_type=ObjectType.DATASET,
        title="Mine", actor="test",
    )
    right = objects.create_object(
        cur, project_id=other, object_type=ObjectType.ANALYSIS,
        title="Theirs", actor="test",
    )

    with pytest.raises(Exception, match="lineage endpoints must belong"):
        cur.execute(
            "INSERT INTO artifact_lineage_edges "
            "(id, project_id, source_artifact_id, target_artifact_id, lineage_type) "
            "VALUES (%s, %s, %s, %s, 'calculated_from')",
            (new_id("lin"), project, left, right),
        )


def test_analysis_run_cannot_use_another_projects_spec(cur, project):
    other = _second_project(cur, project)
    spec_id = new_id("asp")
    cur.execute(
        "INSERT INTO analysis_specs "
        "(id, project_id, analysis_type, method, research_question, "
        "dataset_version_ids, content_hash, created_by) "
        "VALUES (%s, %s, 'statistical', 'pearson_correlation', 'q', "
        "'[]'::jsonb, %s, 'test')",
        (spec_id, other, "b" * 64),
    )

    with pytest.raises(analysis.AnalysisError, match="different project"):
        analysis.create_run(cur, project_id=project, spec_id=spec_id)


def test_analysis_fork_cannot_name_another_projects_run(cur, project):
    other = _second_project(cur, project)

    mine_spec = new_id("asp")
    other_spec = new_id("asp")
    for spec_id, owner in ((mine_spec, project), (other_spec, other)):
        cur.execute(
            "INSERT INTO analysis_specs "
            "(id, project_id, analysis_type, method, research_question, "
            "dataset_version_ids, content_hash, created_by) "
            "VALUES (%s, %s, 'statistical', 'pearson_correlation', 'q', "
            "'[]'::jsonb, %s, 'test')",
            (spec_id, owner, new_id("hash")),
        )

    foreign_run = analysis.create_run(cur, project_id=other, spec_id=other_spec)

    with pytest.raises(analysis.AnalysisError, match="different project"):
        analysis.create_run(
            cur,
            project_id=project,
            spec_id=mine_spec,
            forked_from_run_id=foreign_run,
            fork_reason="must refuse",
        )
