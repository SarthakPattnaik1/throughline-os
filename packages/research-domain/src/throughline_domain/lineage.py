"""Artifact lineage — the system that answers "How was this made?".

this rule says no result without provenance. In practice that means: an artifact is
created *together with* the edges that explain it, in one transaction. A helper
that writes the object and lets the caller remember the edge afterwards would
make provenance optional, so ``record_derivation`` is the only creation path for
derived objects.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from throughline_schemas.enums import LineageType, ObjectType

from .ids import new_id


class LineageError(RuntimeError):
    pass


#: Object types that enter a project from outside rather than being made inside
#: it. Everything else is derived from something, so an empty chain under any
#: other type is a gap in the record and not a fact about the artifact.
#:
#: Declared here rather than in the interface. The screen that reads a chain
#: used to assert "this is a source artifact — nothing was derived to make it"
#: for *any* empty ancestor list, which is a provenance claim it had no basis
#: for: an analysis whose lineage edges were never written looks identical, and
#: the sentence reports the record as complete rather than missing. That is the
#: flattering direction, in the one screen whose whole job is not to flatter.
ROOT_TYPES = frozenset({ObjectType.PAPER, ObjectType.DATASET})


def origin_of(object_type: str, ancestor_count: int) -> str:
    """Whether a chain is derived, a root, or simply not recorded.

    Three answers, because there are three cases and the last two look the same
    from the ancestor list alone:

    * ``derived`` — something was recorded as making this.
    * ``uploaded`` — it is a paper or a dataset, so the chain legitimately
      starts here.
    * ``unrecorded`` — it is neither, and nothing explains it. The honest
      reading is that the derivation was never written down, which a reader has
      to be told rather than left to infer from an empty list.
    """
    if ancestor_count > 0:
        return "derived"
    return "uploaded" if object_type in ROOT_TYPES else "unrecorded"


def add_edge(
    cur,
    *,
    project_id: str,
    source_artifact_id: str,
    target_artifact_id: str,
    lineage_type: LineageType,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Record that ``target`` was produced from ``source``.

    Idempotent on (source, target, type) so a workflow retry cannot duplicate
    lineage.
    """
    if source_artifact_id == target_artifact_id:
        raise LineageError("An artifact cannot derive from itself")

    cur.execute(
        "SELECT id, project_id FROM research_objects WHERE id = ANY(%s)",
        ([source_artifact_id, target_artifact_id],),
    )
    projects = {row["id"]: row["project_id"] for row in cur.fetchall()}
    missing = [object_id for object_id in (source_artifact_id, target_artifact_id)
               if object_id not in projects]
    if missing:
        raise LineageError(
            "A lineage edge can only name recorded research objects: "
            + ", ".join(missing)
        )
    if (projects[source_artifact_id] != project_id
            or projects[target_artifact_id] != project_id):
        raise LineageError(
            "Both ends of a lineage edge must belong to the edge's project."
        )

    edge_id = new_id("lin")
    cur.execute(
        """
        INSERT INTO artifact_lineage_edges
            (id, project_id, source_artifact_id, target_artifact_id, lineage_type, metadata)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (source_artifact_id, target_artifact_id, lineage_type)
        DO UPDATE SET metadata = artifact_lineage_edges.metadata || EXCLUDED.metadata
        RETURNING id
        """,
        (
            edge_id,
            project_id,
            source_artifact_id,
            target_artifact_id,
            str(lineage_type),
            metadata or {},
        ),
    )
    return cur.fetchone()["id"]


def record_derivation(
    cur,
    *,
    project_id: str,
    target_artifact_id: str,
    inputs: Sequence[str],
    lineage_type: LineageType = LineageType.DERIVED_FROM,
    metadata: dict[str, Any] | None = None,
) -> list[str]:
    """Attach every input that contributed to a derived artifact."""
    if not inputs:
        raise LineageError(
            "A derived artifact must record at least one input. "
            "If it genuinely has no antecedent it is a source, not a derivation."
        )
    return [
        add_edge(
            cur,
            project_id=project_id,
            source_artifact_id=source_id,
            target_artifact_id=target_artifact_id,
            lineage_type=lineage_type,
            metadata=metadata,
        )
        for source_id in dict.fromkeys(inputs)
    ]


def ancestors(cur, artifact_id: str, *, max_depth: int = 32) -> list[dict[str, Any]]:
    """Everything this artifact was made from, breadth-first, with depth.

    Uses a recursive CTE with a visited path so a cycle introduced by a future
    bug degrades to a truncated answer rather than an infinite loop.
    """
    cur.execute(
        """
        WITH RECURSIVE walk(artifact_id, depth, path) AS (
            SELECT %s::text, 0, ARRAY[%s::text]
          UNION ALL
            SELECT e.source_artifact_id, w.depth + 1, w.path || e.source_artifact_id
            FROM artifact_lineage_edges e
            JOIN walk w ON e.target_artifact_id = w.artifact_id
            WHERE w.depth < %s
              AND NOT e.source_artifact_id = ANY(w.path)
        )
        SELECT w.artifact_id, MIN(w.depth) AS depth,
               o.object_type, o.title, o.created_at
        FROM walk w
        JOIN research_objects o ON o.id = w.artifact_id
        WHERE w.depth > 0
        GROUP BY w.artifact_id, o.object_type, o.title, o.created_at
        ORDER BY depth, o.created_at
        """,
        (artifact_id, artifact_id, max_depth),
    )
    return list(cur.fetchall())


def descendants(cur, artifact_id: str, *, max_depth: int = 32) -> list[dict[str, Any]]:
    """Everything made from this artifact — the blast radius for  and ."""
    cur.execute(
        """
        WITH RECURSIVE walk(artifact_id, depth, path) AS (
            SELECT %s::text, 0, ARRAY[%s::text]
          UNION ALL
            SELECT e.target_artifact_id, w.depth + 1, w.path || e.target_artifact_id
            FROM artifact_lineage_edges e
            JOIN walk w ON e.source_artifact_id = w.artifact_id
            WHERE w.depth < %s
              AND NOT e.target_artifact_id = ANY(w.path)
        )
        SELECT w.artifact_id, MIN(w.depth) AS depth,
               o.object_type, o.title, o.created_at
        FROM walk w
        JOIN research_objects o ON o.id = w.artifact_id
        WHERE w.depth > 0
        GROUP BY w.artifact_id, o.object_type, o.title, o.created_at
        ORDER BY depth, o.created_at
        """,
        (artifact_id, artifact_id, max_depth),
    )
    return list(cur.fetchall())


def provenance_chain(cur, artifact_id: str) -> dict[str, Any]:
    """The  traceability payload for one artifact."""
    cur.execute(
        "SELECT id, project_id, object_type, title, created_at, version "
        "FROM research_objects WHERE id = %s",
        (artifact_id,),
    )
    node = cur.fetchone()
    if not node:
        raise LineageError(f"Unknown artifact: {artifact_id}")
    cur.execute(
        """
        SELECT e.source_artifact_id, e.target_artifact_id, e.lineage_type, e.metadata
        FROM artifact_lineage_edges e
        WHERE e.target_artifact_id = %s
        ORDER BY e.created_at
        """,
        (artifact_id,),
    )
    direct_inputs = list(cur.fetchall())
    chain = ancestors(cur, artifact_id)
    return {
        "artifact": node,
        "direct_inputs": direct_inputs,
        "ancestors": chain,
        # Said by the side that knows the vocabulary. An interface deciding
        # this for itself needs its own copy of which types are roots, and the
        # copy that drifts is the one that starts calling a finding a source.
        "origin": origin_of(str(node["object_type"]), len(chain)),
    }
