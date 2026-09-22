"""Visuals in the research model — recommendation, critique, render, provenance.

this rule is the whole point of this module: a figure is created *from* an analysis
run and linked to it by lineage, so a chart on a slide can always be resolved
back to the computation and the dataset underneath.

 is enforced here too. A natural-language edit that changes what the figure
*means* — different rows, different variables — is not a re-plot; it requires a
new analysis. `apply_edit` refuses those rather than redrawing and letting the
figure quietly disagree with its own statistics.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from throughline_schemas.enums import LineageType, ObjectType
from throughline_visual import critic as visual_critic
from throughline_visual import labels as labels_module
from throughline_visual import prepare as visual_prepare
from throughline_visual import recommend as visual_recommend
from throughline_visual.labels import LabelBook
from throughline_visual.renderers import publication, web
from throughline_visual.spec import ResearchVisualSpec, VisualData, VisualType

from throughline_schemas.words import plural
from .analysis import get_run
from .db import jsonb
from .events import audit, emit
from .ids import new_id
from .lineage import add_edge
from .objects import create_object
from .storage import (
    StorageError, blender_directory, export_directory, export_path, path_for,
    storage_key_for,
)


class VisualError(RuntimeError):
    pass


class EditRequiresRecomputation(VisualError):
    """ — "If a request changes the underlying analysis: rerun computation."."""


#: Spec fields whose change alters *what is being shown*, not how it looks.
#: Editing one of these means a different analysis, not a different drawing.
_DATA_BEARING_FIELDS = {"x", "y", "group", "facet", "filters", "aggregation",
                        "analysis_run_id", "dataset_version_id", "visual_type"}

#: Fields that only affect presentation and may be edited freely.
_PRESENTATION_FIELDS = {"title", "subtitle", "caption", "citations", "theme",
                        "uncertainty", "annotations", "interaction",
                        "animation_semantics", "category_labels"}


def spec_hash(spec: ResearchVisualSpec) -> str:
    payload = spec.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def variable_labels(cur, *, project_id: str, dataset_version_id: str) -> LabelBook:
    """What a reader should see instead of each raw column name.

    The recommender is deliberately database-free, so this is where the
    canonical variable layer is consulted: it already holds a `display_label`
    for exactly this purpose, and a figure that prints `resistance_pct` is
    ignoring data the project has already curated.

    The unit is the part worth being careful about. `canonical_unit` describes
    the harmonised quantity, but a figure plots the *column's* values, and an
    approved mapping may still record `transformation_required` — the numbers
    have not been converted just because the mapping exists. Labelling an axis
    with a unit the values are not in would be a worse failure than printing a
    raw name, so the column's own unit wins, and the canonical one is used only
    where the column declares none and no transformation stands between them.
    """
    cur.execute(
        """
        SELECT DISTINCT ON (dc.id)
               dc.name, dc.original_name, dc.unit AS column_unit,
               cv.display_label, cv.canonical_unit, vm.transformation_required
        FROM dataset_columns dc
        LEFT JOIN variable_mappings vm
               ON vm.dataset_column_id = dc.id
              AND vm.status = 'approved'
              AND vm.project_id = %s
        LEFT JOIN canonical_variables cv
               ON cv.id = vm.canonical_variable_id
        WHERE dc.dataset_version_id = %s
        -- One column can carry more than one approved mapping; take the most
        -- confident, and break ties by id so the label never depends on scan
        -- order. A figure that renamed itself between two runs of the same
        -- analysis would be its own kind of dishonesty.
        ORDER BY dc.id, vm.confidence DESC NULLS LAST, cv.id
        """,
        (project_id, dataset_version_id),
    )

    entries: dict[str, dict[str, Any]] = {}
    for row in cur.fetchall():
        canonical_label = (row["display_label"] or "").strip()
        header = (row["original_name"] or "").strip()
        column_unit = (row["column_unit"] or "").strip()
        canonical_unit = (row["canonical_unit"] or "").strip()
        transformed = bool((row["transformation_required"] or "").strip())

        unit = column_unit or (canonical_unit if not transformed else "")

        if canonical_label:
            # A project label may itself include the unit. The encoding carries
            # that unit separately, so keep it once rather than trusting every
            # renderer to notice and de-duplicate it.
            label = labels_module.strip_trailing_unit(canonical_label, unit)
            source = labels_module.CANONICAL
        elif header and header != row["name"]:
            # The header the researcher typed. Strip only a unit the *column*
            # profiler read from that header. A canonical unit supplied later
            # was not declared by the header and must not rewrite its wording.
            label = labels_module.humanise(
                labels_module.strip_trailing_unit(header, column_unit))
            source = labels_module.DATASET_HEADER
        else:
            # Here the header and storage key are the same machine-style name.
            # If the profiler found a unit, its own closed vocabulary has
            # already proved the last token is a unit declaration. Remove that
            # token rather than comparing spellings: aliases such as _pct -> %
            # and _mins -> min deliberately store a different display unit.
            bare_name = row["name"]
            if column_unit:
                exact = labels_module.strip_trailing_unit(bare_name, column_unit)
                bare_name = (
                    exact if exact != bare_name
                    else labels_module.strip_profiled_unit_suffix(bare_name)
                )
            label, source = labels_module.humanise(bare_name), labels_module.COLUMN_NAME

        entry = {"label": label, "unit": unit or None, "source": source}
        entries[row["name"]] = entry
        if header:
            # A spec may name either spelling; both must resolve.
            entries.setdefault(header, entry)

    return LabelBook(entries)


def recommend_for_run(
    cur, *, analysis_run_id: str, goal: str = "show the relationship",
    audience: str = "researcher",
) -> dict[str, Any]:
    """ — recommend a figure for a completed analysis."""
    run = get_run(cur, analysis_run_id)
    if not run:
        raise VisualError(f"Unknown analysis run: {analysis_run_id}")
    if run["status"] != "completed":
        raise VisualError(
            f"Analysis run {analysis_run_id} is {run['status']}; only a completed "
            "run has results to visualise."
        )
    version_ids = run["dataset_version_ids"] or []
    version_id = version_ids[0] if version_ids else None
    book = (
        variable_labels(cur, project_id=run["project_id"], dataset_version_id=version_id)
        if version_id else LabelBook()
    )
    recommendation = visual_recommend.recommend(
        analysis_run_id=analysis_run_id, method=run["method"],
        variables=run["variables"], result=run["result"] or {},
        dataset_version_id=version_id,
        goal=goal, audience=audience, labels=book,
    )
    # The figure is of the recorded run, not merely the same method/variables.
    # Filters are data-bearing provenance: omitting them lets a filtered
    # statistic sit over unfiltered points and makes the visual spec unable to
    # state which population it represents.
    recommendation["spec"] = recommendation["spec"].model_copy(
        update={"filters": list(run.get("filters") or [])}
    )
    return recommendation


def _validate_visual_binding(run: dict[str, Any], spec: ResearchVisualSpec) -> None:
    """A visual may restyle a run, never change which analysis it represents."""
    method = str(run.get("method") or "")
    variables = run.get("variables") or {}
    kind = spec.visual_type

    allowed_types = {
        "pearson_correlation": {VisualType.SCATTER, VisualType.HEXBIN},
        "spearman_correlation": {VisualType.SCATTER, VisualType.HEXBIN},
        "bootstrap_correlation": {VisualType.SCATTER, VisualType.HEXBIN},
        "linear_regression": {VisualType.SCATTER, VisualType.FOREST, VisualType.SURFACE},
        "logistic_regression": {VisualType.FOREST},
        "mixed_model": {VisualType.FOREST},
        "t_test": {VisualType.BOX},
        "mann_whitney": {VisualType.BOX},
        "anova": {VisualType.BOX},
        "kruskal_wallis": {VisualType.BOX},
        "chi_square": {VisualType.HEATMAP},
        "descriptive": {VisualType.HISTOGRAM},
    }
    if kind not in allowed_types.get(method, set()):
        raise VisualError(
            f"{kind.value} is not a faithful figure type for the recorded "
            f"{method} run. Rerun or use one of: "
            + ", ".join(sorted(v.value for v in allowed_types.get(method, set())))
        )

    def field(encoding):
        return encoding.field if encoding is not None else None

    if method in {"pearson_correlation", "spearman_correlation",
                  "bootstrap_correlation"}:
        if field(spec.x) != variables.get("x") or field(spec.y) != variables.get("y"):
            raise VisualError(
                "The figure axes do not match the variables recorded by the "
                "correlation run."
            )
    elif method == "linear_regression":
        predictors = list(variables.get("predictors") or [])
        outcome = variables.get("outcome")
        if kind is VisualType.SCATTER:
            if len(predictors) != 1 or field(spec.x) != predictors[0] or field(spec.y) != outcome:
                raise VisualError(
                    "A simple-regression scatter must use the recorded predictor "
                    "on x and recorded outcome on y."
                )
        elif kind is VisualType.SURFACE:
            if len(predictors) != 2 or [field(spec.x), field(spec.y)] != predictors:
                raise VisualError(
                    "A regression surface must use the two recorded predictors "
                    "in their recorded order."
                )
    elif method in {"t_test", "mann_whitney", "anova", "kruskal_wallis"}:
        group, value = variables.get("group"), variables.get("value")
        if field(spec.x) != group or field(spec.y) != value:
            raise VisualError(
                "The group-comparison figure does not use the recorded group "
                "and value variables."
            )
        if kind is VisualType.BOX and field(spec.group) != group:
            raise VisualError(
                "The box plot grouping does not match the recorded group variable."
            )
    elif method == "chi_square":
        if kind is VisualType.HEATMAP:
            if field(spec.x) != variables.get("x") or field(spec.y) != variables.get("y"):
                raise VisualError(
                    "The contingency heatmap axes do not match the recorded "
                    "categorical variables."
                )
    elif method == "descriptive":
        columns = list(variables.get("columns") or [])
        if field(spec.x) not in columns:
            raise VisualError(
                "The histogram column was not part of the recorded descriptive run."
            )


def create_visual(
    cur, *, project_id: str, spec: ResearchVisualSpec, actor: str,
    sample: dict[str, Sequence[Any]] | None = None,
    recommendation: dict[str, Any] | None = None,
    finding_id: str | None = None, autofix: bool = True,
) -> dict[str, Any]:
    """Prepare, critique and store a figure, with its lineage."""
    run = get_run(cur, spec.analysis_run_id)
    if not run:
        raise VisualError(f"Unknown analysis run: {spec.analysis_run_id}")
    if run["project_id"] != project_id:
        raise VisualError("The analysis run belongs to a different project.")

    _validate_visual_binding(run, spec)

    run_versions = list(run.get("dataset_version_ids") or [])
    if run_versions:
        if not spec.dataset_version_id:
            raise VisualError(
                "The figure omits the dataset version used by its analysis run."
            )
        if spec.dataset_version_id not in run_versions:
            raise VisualError(
                "The figure names a dataset version that was not used by its "
                "analysis run."
            )

    recorded_filters = list(run.get("filters") or [])
    if list(spec.filters or []) != recorded_filters:
        raise VisualError(
            "The figure filters do not match the filters recorded on its "
            "analysis run. Change the analysis and rerun it instead."
        )

    if finding_id:
        # The run was checked and the finding was not, so a figure in your
        # project could be filed against another account's finding (T185).
        cur.execute("SELECT 1 FROM findings WHERE id = %s AND project_id = %s",
                    (finding_id, project_id))
        if cur.fetchone() is None:
            raise VisualError("The finding belongs to a different project.")

    result = run["result"] or {}
    data = visual_prepare.prepare(spec, analysis_result=result, sample=sample)
    report = visual_critic.critique(spec, data, analysis=result, autofix=autofix)
    spec = report.spec or spec

    visual_id = new_id("vis")
    object_id = create_object(
        cur, project_id=project_id, object_type=ObjectType.VISUALIZATION,
        title=spec.title or f"{spec.visual_type} figure", actor=actor,
        metadata={"visual_id": visual_id, "visual_type": str(spec.visual_type)},
    )
    # The figure visualises the analysis; the edge is what this rule rests on.
    if run["object_id"]:
        add_edge(cur, project_id=project_id, source_artifact_id=run["object_id"],
                 target_artifact_id=object_id, lineage_type=LineageType.VISUALIZES,
                 metadata={"visual_id": visual_id})

    cur.execute(
        """
        INSERT INTO visuals
            (id, project_id, object_id, analysis_run_id, finding_id, spec_version,
             visual_type, spec, spec_hash, data, recommendation, critique, publishable,
             created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (visual_id, project_id, object_id, spec.analysis_run_id, finding_id,
         spec.spec_version, str(spec.visual_type), jsonb(spec.model_dump(mode="json")),
         spec_hash(spec), jsonb(data.model_dump(mode="json")),
         jsonb(_serialisable(recommendation or {})), jsonb(report.to_dict()),
         report.publishable, actor),
    )
    emit(cur, project_id=project_id, event_type="VisualizationCreated",
         payload={"visual_id": visual_id, "analysis_run_id": spec.analysis_run_id,
                  "publishable": report.publishable})
    audit(cur, project_id=project_id, actor=actor, action="create",
          object_type="visual", object_id=visual_id)

    return {"visual_id": visual_id, "object_id": object_id, "spec": spec,
            "data": data, "critique": report.to_dict(),
            "publishable": report.publishable,
            # Whether any publication format exists for this kind of figure.
            # Publishable and exportable are different questions: a fitted
            # surface can pass every check and still have no flat export.
            "exportable": publication.can_render(spec.visual_type)}


def _serialisable(recommendation: dict[str, Any]) -> dict[str, Any]:
    payload = dict(recommendation)
    if isinstance(payload.get("spec"), ResearchVisualSpec):
        payload["spec"] = payload["spec"].model_dump(mode="json")
    payload["visual_type"] = str(payload.get("visual_type", ""))
    payload["alternatives"] = [
        {**a, "visual_type": str(a.get("visual_type", ""))}
        for a in payload.get("alternatives", [])
    ]
    return payload


def list_visuals(cur, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """
    Every figure this project has made, newest first.

    Nothing listed them. A figure is created with its critique and a lineage
    edge back to the analysis it draws, and then could only be reached by
    somebody who had kept its id — so the record existed and the researcher
    could not see it. `idx_visuals_project` has been indexed on exactly this
    order since the table was written, for a query nobody had made.

    The spec is not returned: it is large, and a list wants a title and whether
    the figure passed the critic. Opening one loads the rest.
    """
    cur.execute(
        "SELECT id, visual_type, publishable, created_at, analysis_run_id, "
        "       finding_id, spec ->> 'title' AS title, "
        "       spec ->> 'caption' AS caption "
        "FROM visuals WHERE project_id = %s "
        "ORDER BY created_at DESC LIMIT %s",
        (project_id, limit))
    return [dict(row) for row in cur.fetchall()]


def load_visual(cur, visual_id: str) -> dict[str, Any]:
    cur.execute("SELECT * FROM visuals WHERE id = %s", (visual_id,))
    row = cur.fetchone()
    if not row:
        raise VisualError(f"Unknown visual: {visual_id}")
    return row


def _refuse_if_unpublishable(row: dict[str, Any]) -> None:
    """A figure the critic blocked is refused by every renderer, not by one."""
    if not row["publishable"]:
        blocking = [c["check"] for c in (row["critique"].get("critiques") or [])
                    if c["severity"] == "blocking" and c["outcome"] == "violated"]
        raise VisualError(
            "This figure did not pass the visualization critic and must not be "
            f"published. Unresolved: {', '.join(blocking) or 'unknown'}."
        )


def render_visual(cur, *, visual_id: str, fmt: str,
                  height_px: int | None = None, ground: str = "light",
                  transparent: bool = False) -> dict[str, Any]:
    """
    Render a stored figure. Publication formats plus the web spec, one source.

    `height_px` gives an exact pixel height for a raster export; the width
    follows from the figure's own proportions rather than from a video frame.
    It is refused for a vector format by the renderer, because an SVG has no
    pixel height and a silent no-op would leave the caller believing otherwise.

    `ground` (light or dark) and `transparent` choose what the figure is drawn
    on. Each combination is its own file and its own row: asking for the dark
    version must not replace the light one a manuscript already links to.
    """
    row = load_visual(cur, visual_id)
    _refuse_if_unpublishable(row)

    spec = ResearchVisualSpec.model_validate(row["spec"])
    data = VisualData.model_validate(row["data"])
    current_hash = row["spec_hash"]

    if fmt == "vega-lite":
        payload = web.render(spec, data)
        cur.execute(
            "INSERT INTO visual_renders(id, visual_id, format, spec_hash, payload) "
            "VALUES (%s, %s, %s, %s, %s) "
            # This target named the constraint migration 0030 dropped, so every
            # vega-lite render since then failed with "no unique or exclusion
            # constraint matching the ON CONFLICT specification" — the file path
            # was moved to the new index and this one was not. Nothing in the
            # interface asks for vega-lite, which is why nobody saw it.
            "ON CONFLICT (visual_id, format, spec_hash, COALESCE(height_px, -1), "
            "renderer, ground, transparent) DO UPDATE SET payload = EXCLUDED.payload "
            "RETURNING id",
            (new_id("vren"), visual_id, fmt, current_hash, jsonb(payload)),
        )
        return {"visual_id": visual_id, "format": fmt, "payload": payload,
                "render_id": cur.fetchone()["id"]}

    # Every render gets a server-generated filename. The spec hash and size
    # remain in the database uniqueness key and metadata; they do not need to be
    # copied into a filesystem path.
    directory = export_directory("visuals", visual_id)
    directory.mkdir(parents=True, exist_ok=True)
    render_id = new_id("vren")
    path = export_path("visuals", visual_id, render_id, fmt)
    publication.render(
        spec, data, path=path, fmt=fmt, height_px=height_px,
        ground=ground, transparent=transparent,
        # Provenance travels inside the file, because a figure that leaves the
        # building is the one output whose link back cannot be a foreign key.
        metadata={
            "Title": visual_id,
            "Description": f"spec_hash={current_hash}",
            "Creator": "Throughline",
        })
    byte_size = path.stat().st_size
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    storage_key = storage_key_for(path)

    cur.execute(
        "INSERT INTO visual_renders(id, visual_id, format, storage_key, content_hash, "
        "spec_hash, bytes, height_px, ground, transparent) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (visual_id, format, spec_hash, COALESCE(height_px, -1), renderer, "
        "ground, transparent) DO UPDATE "
        "SET storage_key = EXCLUDED.storage_key, content_hash = EXCLUDED.content_hash, "
        "bytes = EXCLUDED.bytes RETURNING id",
        (render_id, visual_id, fmt, storage_key, digest, current_hash,
         byte_size, height_px, ground, transparent),
    )
    return {"visual_id": visual_id, "format": fmt, "storage_key": storage_key,
            "content_hash": digest, "bytes": byte_size, "height_px": height_px,
            "ground": ground, "transparent": transparent,
            "warning": publication.warn_about_format(fmt),
            "render_id": cur.fetchone()["id"]}


def _panels(cur, *, project_id: str, visual_ids: Sequence[str]):
    """Load the figures to compose, each checked against the project.

    An id from another project is reported as not found rather than drawn: a
    composed figure is a download, and a download that carried another
    account's figure is the leak T185 closed route by route.
    """
    if not visual_ids:
        raise VisualError("Choose at least one figure to compose.")
    if len(set(visual_ids)) != len(visual_ids):
        raise VisualError("A figure appears twice. Each panel is a different figure.")
    panels = []
    for visual_id in visual_ids:
        try:
            row = load_visual(cur, visual_id)
        except VisualError:
            raise VisualNotFound(f"Figure {visual_id} was not found.") from None
        if row["project_id"] != project_id:
            raise VisualNotFound(f"Figure {visual_id} was not found.")
        _refuse_if_unpublishable(row)
        panels.append((ResearchVisualSpec.model_validate(row["spec"]),
                       VisualData.model_validate(row["data"]), row))
    return panels


def check_composition(cur, *, project_id: str,
                      visual_ids: Sequence[str]) -> dict[str, Any]:
    """What a composed figure would say, before it is drawn.

    The letters, each panel's recorded numbers and every disagreement between
    them — so the interface can show "B and D point opposite ways" while the
    researcher is still choosing panels, not after the file has downloaded.
    """
    from throughline_visual.renderers import compose

    panels = _panels(cur, project_id=project_id, visual_ids=visual_ids)
    pairs = [(spec, data) for spec, data, _ in panels]
    return {
        "panels": [{"letter": chr(ord("A") + i), "visual_id": row["id"],
                    "title": spec.title, "visual_type": spec.visual_type.value,
                    "metrics": compose.metrics_line(data.statistics or {}),
                    "drawable": publication.can_render(spec.visual_type)}
                   for i, (spec, data, row) in enumerate(panels)],
        "disagreements": compose.disagreements(pairs),
    }


def compose_figure(cur, *, project_id: str, visual_ids: Sequence[str], fmt: str,
                   directory: Path, ground: str = "light", transparent: bool = False,
                   height_px: int | None = None, columns: int | None = None,
                   actor: str = "system") -> dict[str, Any]:
    """Draw several figures as one lettered figure, into `directory`.

    Not stored: a composition is an arrangement of figures that are each
    recorded, with their own lineage to the analyses they draw, and the file
    carries the ids and spec hashes of every panel so it can be traced back
    the same way. The composition is audited so the project's history says
    it left.
    """
    from throughline_visual.renderers import compose

    fmt = fmt.lower()
    if fmt not in publication.SUPPORTED_FORMATS:
        # Checked before any path is built from it: the format names the file.
        raise compose.ComposeError(
            f"{fmt!r} is not a supported publication format. "
            f"Supported: {', '.join(publication.SUPPORTED_FORMATS)}")
    panels = _panels(cur, project_id=project_id, visual_ids=visual_ids)
    provenance = "; ".join(f"{row['id']} spec_hash={row['spec_hash']}"
                           for _, _, row in panels)
    # The extension is the library's own literal, found by position, never the
    # caller's string: a membership check does not stop request text reaching
    # a filesystem path, and CodeQL (rightly) does not treat it as though it did.
    extension = publication.SUPPORTED_FORMATS[publication.SUPPORTED_FORMATS.index(fmt)]
    path = directory / f"figure.{extension}"
    drawn = compose.compose(
        [(spec, data) for spec, data, _ in panels], path=path, fmt=extension,
        ground=ground, transparent=transparent, height_px=height_px,
        columns=columns,
        metadata={"Title": "Composed figure: " + ", ".join(visual_ids),
                  "Description": provenance, "Creator": "Throughline"})
    audit(cur, project_id=project_id, actor=actor, action="compose",
          object_type="visual", object_id=visual_ids[0],
          detail={"visual_ids": list(visual_ids), "format": fmt,
                  "ground": ground, "transparent": transparent,
                  "disagreements": drawn["disagreements"]})
    return drawn


def apply_edit(
    cur, *, visual_id: str, changes: dict[str, Any], actor: str,
) -> dict[str, Any]:
    """Edit a figure's presentation. Refuses edits that change its meaning.

    "Never visually fake a different answer": changing which rows or variables a
    figure draws is a new analysis, and this raises rather than silently
    redrawing with the old statistics attached.
    """
    row = load_visual(cur, visual_id)
    data_changes = sorted(set(changes) & _DATA_BEARING_FIELDS)
    if data_changes:
        raise EditRequiresRecomputation(
            f"Changing {', '.join(data_changes)} changes what the figure shows, not "
            "how it looks. Create a new AnalysisSpec and run it, then visualise "
            "that result — a redraw would leave the old statistics on new data."
        )
    unknown = sorted(set(changes) - _PRESENTATION_FIELDS)
    if unknown:
        raise VisualError(
            f"Unknown or non-editable {plural(len(unknown), 'field')}: {', '.join(unknown)}. "
            f"Editable: {', '.join(sorted(_PRESENTATION_FIELDS))}"
        )

    spec = ResearchVisualSpec.model_validate(row["spec"] | changes)
    data = VisualData.model_validate(row["data"])
    run = get_run(cur, row["analysis_run_id"])
    report = visual_critic.critique(spec, data, analysis=(run["result"] or {}),
                                    autofix=True)
    spec = report.spec or spec

    cur.execute(
        "UPDATE visuals SET spec = %s, spec_hash = %s, visual_type = %s, "
        "critique = %s, publishable = %s, version = version + 1 WHERE id = %s",
        (jsonb(spec.model_dump(mode="json")), spec_hash(spec), str(spec.visual_type),
         jsonb(report.to_dict()), report.publishable, visual_id),
    )
    audit(cur, project_id=row["project_id"], actor=actor, action="edit",
          object_type="visual", object_id=visual_id, detail={"fields": sorted(changes)})
    return {"visual_id": visual_id, "spec": spec, "critique": report.to_dict(),
            "publishable": report.publishable}


def stale_renders(cur, visual_id: str) -> list[dict[str, Any]]:
    """ — renders whose spec has moved on are marked, not silently served."""
    row = load_visual(cur, visual_id)
    cur.execute(
        # `renderer` and `deterministic` as well: this is where a figure's files
        # are listed, and without them a Blender render read as one more PNG
        # export of the same figure — the conflation migration 0045 exists to
        # prevent, repeated one layer up.
        "SELECT id, format, renderer, deterministic, spec_hash, ground, transparent, created_at "
        "FROM visual_renders WHERE visual_id = %s",
        (visual_id,),
    )
    return [dict(r, stale=r["spec_hash"] != row["spec_hash"]) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Rendering through Blender
# ---------------------------------------------------------------------------
#
# `throughline_visual.renderers.blender.render` was written, documented and
# tested, and called by nothing but its own tests. Settings found Blender,
# named its version, and promised "a physically-based render for
# publication"; nothing anywhere could ask for one. What follows is the path
# from a figure to that render: requested from a route, run by a worker — a
# render can take minutes, and a request held open that long is a timeout
# reported as a failure — and recorded beside the export rather than as it.

#: The worker job that runs Blender.
BLENDER_RENDER_WORKFLOW = "visual.render_blender"


class VisualNotFound(VisualError):
    """A figure id that does not exist in this project. Answered 404."""


class NotALook(VisualError):
    """A render style or ground that does not exist. Answered 400."""


class NotASurface(VisualError):
    """Only a fitted surface has a third axis for Blender to render."""


class BlenderUnavailable(VisualError):
    """This machine cannot render through Blender; the message says how to fix it."""


class BlenderRenderFailed(VisualError):
    """Blender ran and did not produce a picture."""


def _refuse_if_not_a_surface(row: dict[str, Any]) -> None:
    if row["visual_type"] != "surface":
        raise NotASurface(
            "Only a fitted surface has a third axis for Blender to render. This "
            f"figure is a {str(row['visual_type']).replace('_', ' ')}, and its "
            "publication formats are the export.")


_availability_cache: dict[tuple[str, float], dict[str, Any]] = {}


def _blender_availability() -> dict[str, Any]:
    """
    `blender.availability()`, asked once per installation rather than per poll.

    It runs `blender --version`, which takes most of a second, and the screen
    asks for a render's state every few seconds while one runs. Keyed on the
    executable's path and modification time, so installing, upgrading or
    removing Blender is noticed on the next request rather than at restart.
    """
    from throughline_visual.renderers import blender

    executable = blender.find_blender()
    if not executable:
        return blender.availability()
    try:
        key = (executable, Path(executable).stat().st_mtime)
    except OSError:
        return blender.availability()
    if key not in _availability_cache:
        _availability_cache.clear()
        _availability_cache[key] = blender.availability()
    return _availability_cache[key]


def _plain_error(error: str | None) -> str | None:
    """The runner stores `Type: message`; a researcher needs the message."""
    if not error:
        return None
    for prefix in ("BlenderUnavailable: ", "BlenderRenderFailed: ",
                   "BlenderError: ", "NotASurface: ", "GeometryError: ",
                   "VisualError: "):
        if error.startswith(prefix):
            return error[len(prefix):]
    return error


def request_blender_render(cur, *, visual_id: str, style: str = "figure",
                           ground: str = "light") -> dict[str, Any]:
    """
    Queue a Blender render of this figure, or return the one already running.

    Refused up front rather than queued to fail: a figure with no third axis,
    a figure the critic blocked, and a machine without Blender are all known
    before any job exists, and a researcher should hear the reason on the
    click rather than after watching a spinner.

    **Deduplicated against runs in flight, never against finished ones.** The
    workflow table's `idempotency_key` is unique for all time, so keying a
    render on the figure would hand back the same run for ever — including a
    failed one, so a researcher who installed Blender after a failure could
    never render again. A finished render is no reason to refuse another:
    Blender's output varies, and asking twice is a reasonable thing to do.

    One attempt only. A missing or broken Blender does not appear on the
    third try, and retrying would only delay the sentence that says so.
    """
    from throughline_schemas.enums import TERMINAL_WORKFLOW_STATES

    from . import workflow

    from throughline_visual import tokens
    from throughline_visual.renderers import blender

    # Refused on the click, like every other known-in-advance failure.
    if style not in blender.STYLES:
        raise NotALook(f"{style!r} is not a render style. "
                          f"Styles: {', '.join(blender.STYLES)}")
    if ground not in tokens.GROUNDS:
        raise NotALook(f"{ground!r} is not a figure ground. "
                          f"Grounds: {', '.join(tokens.GROUNDS)}")

    row = load_visual(cur, visual_id)
    _refuse_if_not_a_surface(row)
    _refuse_if_unpublishable(row)

    available = _blender_availability()
    if not available["available"]:
        raise BlenderUnavailable(
            " ".join(part for part in (available.get("withheld"),
                                       available.get("install")) if part)
            or "Blender could not be found on this machine.")

    terminal = [str(state) for state in TERMINAL_WORKFLOW_STATES]
    cur.execute(
        "SELECT id, state FROM workflow_runs WHERE workflow_name = %s "
        "AND input->>'visual_id' = %s AND NOT (state = ANY(%s)) "
        "ORDER BY created_at DESC LIMIT 1",
        (BLENDER_RENDER_WORKFLOW, visual_id, terminal))
    running = cur.fetchone()
    if running:
        return {"run_id": running["id"], "state": running["state"],
                "reused": True}

    run_id = workflow.enqueue(
        cur, workflow_name=BLENDER_RENDER_WORKFLOW,
        project_id=row["project_id"],
        payload={"visual_id": visual_id, "style": style, "ground": ground},
        max_attempts=1)
    return {"run_id": run_id, "state": "queued", "reused": False}


def render_through_blender(cur, *, visual_id: str, samples: int = 64,
                           style: str = "figure",
                           ground: str = "light") -> dict[str, Any]:
    """
    Render this figure through Blender, and record it as a render.

    Called by the worker. The geometry is the same the `scene.zip` export
    carries, written into a directory this system made; Blender reads it as
    data and never sees a string a researcher typed.

    **The previous file is removed before Blender runs.** `blender.render`
    believes the output file rather than the exit status, because Blender
    exits 0 on a great many failures. A re-render writes to the same path, so
    a render that failed the second time would have found the first render's
    file waiting there and reported success — the old picture, presented as
    the new one.
    """
    from throughline_visual.renderers import blender, geometry

    row = load_visual(cur, visual_id)
    _refuse_if_not_a_surface(row)
    _refuse_if_unpublishable(row)

    spec = ResearchVisualSpec.model_validate(row["spec"])
    data = VisualData.model_validate(row["data"])
    current_hash = row["spec_hash"]

    # Built from the canonical id and a hex-checked hash, never the raw strings,
    # so the key recorded below is one `path_for` reads back (CodeQL #6, #7).
    directory = blender_directory(visual_id, current_hash)
    directory.mkdir(parents=True, exist_ok=True)
    obj_path = directory / "fitted_surface.obj"
    ply_path = directory / "observations.ply"
    obj_path.write_text(geometry.surface_obj(spec, data))
    ply_path.write_text(geometry.observations_ply(spec, data))
    out_path = directory / f"{directory.parent.name}-{directory.name[len('blender-'):]}-blender.png"
    out_path.unlink(missing_ok=True)

    try:
        made = blender.render(obj_path=obj_path, ply_path=ply_path,
                              out_path=out_path, samples=samples,
                              style=style, ground=ground)
    except blender.BlenderError as exc:
        if blender.find_blender() is None:
            raise BlenderUnavailable(str(exc)) from exc
        raise BlenderRenderFailed(str(exc)) from exc

    byte_size = out_path.stat().st_size
    digest = hashlib.sha256(out_path.read_bytes()).hexdigest()
    storage_key = storage_key_for(out_path)

    cur.execute(
        "INSERT INTO visual_renders(id, visual_id, format, storage_key, "
        "content_hash, spec_hash, bytes, renderer, renderer_version, "
        "deterministic) VALUES (%s, %s, 'png', %s, %s, %s, %s, 'blender', %s, "
        "FALSE) "
        "ON CONFLICT (visual_id, format, spec_hash, COALESCE(height_px, -1), "
        "renderer, ground, transparent) DO UPDATE SET storage_key = EXCLUDED.storage_key, "
        "content_hash = EXCLUDED.content_hash, bytes = EXCLUDED.bytes, "
        "renderer_version = EXCLUDED.renderer_version, created_at = now() "
        "RETURNING id",
        (new_id("vren"), visual_id, storage_key, digest, current_hash,
         byte_size, made["renderer_version"]))
    render_id = cur.fetchone()["id"]
    audit(cur, project_id=row["project_id"], actor="system:blender",
          action="render", object_type="visual", object_id=visual_id,
          detail={"renderer": "blender",
                  "renderer_version": made["renderer_version"],
                  "style": style, "ground": ground,
                  "render_id": render_id})

    return {"visual_id": visual_id, "render_id": render_id,
            "renderer": "blender", "renderer_version": made["renderer_version"],
            "deterministic": False, "bytes": byte_size, "note": made["note"],
            "style": style, "ground": ground}


def _colour_scale(row: dict[str, Any]) -> dict[str, Any] | None:
    """What the render's colours stand for, in the figure's own numbers.

    The surface is coloured from its lowest to its highest fitted value — the
    mesh's own bounds, not the observations', which Blender never sees as
    colour. A ramp with no numbers is decoration that looks like data, so the
    two ends are stated. Only for a render of the current figure: a stale one
    was coloured from different numbers.
    """
    from throughline_visual.renderers import compose, geometry

    matrix = (row.get("data") or {}).get("matrix") or []
    fitted = [float(v) for line in matrix for v in line if v is not None]
    if not fitted:
        return None
    spec = ResearchVisualSpec.model_validate(row["spec"])
    label = geometry._axis_names(spec)[2]
    low, high = min(fitted), max(fitted)
    return {"low": low, "high": high, "label": label,
            "text": (f"Colour is the fitted {label}: dark purple is "
                     f"{compose._number(low)}, the lowest fitted value, and "
                     f"yellow {compose._number(high)}, the highest. The surface "
                     "is the model, not the measurements.")}


def blender_render_state(cur, *, visual_id: str) -> dict[str, Any]:
    """Whether this figure can be rendered through Blender, and how far it got."""
    row = load_visual(cur, visual_id)
    available = _blender_availability()

    cur.execute(
        "SELECT id, renderer_version, deterministic, bytes, spec_hash, "
        "created_at FROM visual_renders WHERE visual_id = %s "
        "AND renderer = 'blender' ORDER BY created_at DESC LIMIT 1",
        (visual_id,))
    render = cur.fetchone()
    cur.execute(
        "SELECT id, state, error FROM workflow_runs WHERE workflow_name = %s "
        "AND input->>'visual_id' = %s ORDER BY created_at DESC LIMIT 1",
        (BLENDER_RENDER_WORKFLOW, visual_id))
    run = cur.fetchone()

    return {
        "visual_id": visual_id,
        "is_surface": row["visual_type"] == "surface",
        "colour_scale": (_colour_scale(row) if render
                         and render["spec_hash"] == row["spec_hash"] else None),
        "available": bool(available["available"]),
        "version": available.get("version"),
        "withheld": available.get("withheld") or "",
        "install": available.get("install") or "",
        "render": None if not render else {
            "render_id": render["id"],
            "renderer_version": render["renderer_version"],
            "deterministic": render["deterministic"],
            "bytes": render["bytes"],
            "created_at": render["created_at"].isoformat(),
            # A render of an earlier spec is a picture of a figure that no
            # longer exists in that form. Said, rather than shown as current.
            "stale": render["spec_hash"] != row["spec_hash"],
            "note": ("Rendered with Blender "
                     f"{render['renderer_version'] or '(version unrecorded)'}. "
                     "A render varies with the version, the build and the "
                     "machine, so it is kept beside the reproducible export, "
                     "never in place of it."),
        },
        "run": None if not run else {
            "run_id": run["id"], "state": run["state"],
            "error": _plain_error(run["error"]),
        },
    }


def blender_render_file(cur, *, visual_id: str) -> Path | None:
    """The newest Blender render of this figure, if one exists on disk."""
    cur.execute(
        "SELECT storage_key FROM visual_renders WHERE visual_id = %s "
        "AND renderer = 'blender' ORDER BY created_at DESC LIMIT 1",
        (visual_id,))
    found = cur.fetchone()
    if not found or not found["storage_key"]:
        return None
    # Through the validated reader: the key came from a row, and a row is not
    # a reason to build a path from a string.
    try:
        return path_for(found["storage_key"])
    except StorageError:
        return None

