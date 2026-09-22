"""
Rendering a figure through Blender, from a click to a picture on disk.

`blender.render` was written, documented and tested — and called by nothing
but its own tests. Settings found Blender, named its version, and promised "a
physically-based render for publication" that no route could produce.

These tests hold the path that now exists — request, job, record — and the two
claims it must not let slip: a render is never recorded as the export, and a
render that failed is never reported with an earlier picture.

The render itself runs only where Blender is installed, and is skipped rather
than faked elsewhere. Everything up to the invocation is tested everywhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from throughline_domain import analysis, visuals, workflow
from throughline_domain.db import connection
from throughline_domain.storage import storage_root
from throughline_schemas.enums import WorkflowState
from throughline_visual.renderers import blender
from throughline_visual.spec import Encoding, ResearchVisualSpec, VisualType
from throughline_workers.runner import Worker
import throughline_workers.handlers  # noqa: F401 — registers the job

from conftest import sign_in
from test_visuals import _sample_for, analysed  # noqa: F401

needs_blender = pytest.mark.skipif(
    blender.find_blender() is None,
    reason="Blender is not installed here. The render is exercised only where "
           "it exists; a mocked subprocess would prove only the mock.")

AVAILABLE = {"available": True, "path": "/Applications/Blender.app",
             "version": "5.2.1", "withheld": "", "install": ""}


def _surface(fixture) -> tuple[str, str]:
    """A stored, publishable surface figure from the fixture's own regression."""
    project_id, version_id, runs = fixture
    with connection() as conn, conn.cursor() as cur:
        run = analysis.get_run(cur, runs["regression"])
        sample = _sample_for(cur, run, ["consumption_ddd", "gdp_per_capita",
                                        "resistance_pct"])
        created = visuals.create_visual(
            cur, project_id=project_id, actor="test", sample=sample,
            spec=ResearchVisualSpec(
                visual_type=VisualType.SURFACE,
                analysis_run_id=runs["regression"],
                dataset_version_id=version_id,
                x=Encoding(field="consumption_ddd", label="Antibiotic consumption"),
                y=Encoding(field="gdp_per_capita", label="GDP per capita"),
            ))
    assert created["publishable"], created["critique"]
    # Publishable and exportable are different questions: a surface passes
    # every check and still has no flat publication format.
    assert created["exportable"] is False
    return project_id, created["visual_id"]


def _flat(fixture) -> str:
    project_id, _, runs = fixture
    with connection() as conn, conn.cursor() as cur:
        recommendation = visuals.recommend_for_run(
            cur, analysis_run_id=runs["correlation"])
        run = analysis.get_run(cur, runs["correlation"])
        sample = _sample_for(cur, run, ["consumption_ddd", "resistance_pct"])
        created = visuals.create_visual(cur, project_id=project_id,
                                        spec=recommendation["spec"],
                                        actor="test", sample=sample)
    assert created["exportable"] is True
    return created["visual_id"]


def _queued_for(visual_id: str) -> list[dict]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, state FROM workflow_runs WHERE workflow_name = %s "
                    "AND input->>'visual_id' = %s",
                    (visuals.BLENDER_RENDER_WORKFLOW, visual_id))
        return list(cur.fetchall())


# ---------------------------------------------------------------------------
# Refused before anything is queued
# ---------------------------------------------------------------------------

def test_a_flat_figure_is_refused_on_the_click(analysed, monkeypatch):
    monkeypatch.setattr(visuals, "_blender_availability", lambda: AVAILABLE)
    visual_id = _flat(analysed)

    with connection() as conn, conn.cursor() as cur:
        with pytest.raises(visuals.NotASurface, match="third axis"):
            visuals.request_blender_render(cur, visual_id=visual_id)
    assert _queued_for(visual_id) == []


def test_a_machine_without_blender_hears_why_and_how_to_fix_it(analysed,
                                                               monkeypatch):
    monkeypatch.setattr(blender, "find_blender", lambda: None)
    visuals._availability_cache.clear()
    _, visual_id = _surface(analysed)

    with connection() as conn, conn.cursor() as cur:
        with pytest.raises(visuals.BlenderUnavailable) as caught:
            visuals.request_blender_render(cur, visual_id=visual_id)
    assert "brew install --cask blender" in str(caught.value)
    # Refused, not queued to fail: nothing sits in the queue saying "failed".
    assert _queued_for(visual_id) == []


# ---------------------------------------------------------------------------
# One render in flight, and no failure kept for ever
# ---------------------------------------------------------------------------

def test_a_second_click_joins_the_render_already_running(analysed, monkeypatch):
    monkeypatch.setattr(visuals, "_blender_availability", lambda: AVAILABLE)
    _, visual_id = _surface(analysed)

    with connection() as conn, conn.cursor() as cur:
        first = visuals.request_blender_render(cur, visual_id=visual_id)
    with connection() as conn, conn.cursor() as cur:
        second = visuals.request_blender_render(cur, visual_id=visual_id)

    assert second["run_id"] == first["run_id"]
    assert (first["reused"], second["reused"]) == (False, True)
    assert len(_queued_for(visual_id)) == 1


def test_a_failed_render_does_not_block_the_next_one(analysed, monkeypatch):
    """
    The workflow table's idempotency key is unique for all time, so keying a
    render on the figure would hand back a failed run for ever — and a
    researcher who installed Blender after the failure could never render.
    """
    monkeypatch.setattr(visuals, "_blender_availability", lambda: AVAILABLE)
    _, visual_id = _surface(analysed)

    with connection() as conn, conn.cursor() as cur:
        first = visuals.request_blender_render(cur, visual_id=visual_id)
        workflow.finish(cur, run_id=first["run_id"], state=WorkflowState.FAILED,
                        error="BlenderUnavailable: Install Blender first.")
    with connection() as conn, conn.cursor() as cur:
        again = visuals.request_blender_render(cur, visual_id=visual_id)

    assert again["run_id"] != first["run_id"]
    assert again["reused"] is False


def test_a_failure_reaches_the_screen_as_a_sentence(analysed, monkeypatch):
    monkeypatch.setattr(visuals, "_blender_availability", lambda: AVAILABLE)
    _, visual_id = _surface(analysed)
    with connection() as conn, conn.cursor() as cur:
        run = visuals.request_blender_render(cur, visual_id=visual_id)
        workflow.finish(cur, run_id=run["run_id"], state=WorkflowState.FAILED,
                        error="BlenderRenderFailed: Blender ran and produced no image.")
    with connection() as conn, conn.cursor() as cur:
        state = visuals.blender_render_state(cur, visual_id=visual_id)

    assert state["run"]["state"] == "failed"
    # The runner stores `Type: message`; the researcher is given the message.
    assert state["run"]["error"] == "Blender ran and produced no image."


# ---------------------------------------------------------------------------
# A failed re-render is not the previous picture
# ---------------------------------------------------------------------------

def test_a_failed_re_render_is_not_reported_with_the_last_picture(analysed,
                                                                  monkeypatch):
    """
    `blender.render` believes the output file rather than Blender's exit
    status, because Blender exits 0 on a great many failures. A re-render
    writes to the same path, so a second render that failed would have found
    the first render's file there and reported success.

    The stand-in keeps exactly that contract — success if and only if the file
    exists afterwards — so this fails the moment the old file is left in place.
    """
    _, visual_id = _surface(analysed)
    with connection() as conn, conn.cursor() as cur:
        spec_hash = visuals.load_visual(cur, visual_id)["spec_hash"]
    old = (storage_root() / "figures" / visual_id / f"blender-{spec_hash[:12]}"
           / f"{visual_id}-{spec_hash[:12]}-blender.png")
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(b"\x89PNG the previous render")

    def produces_nothing(*, obj_path: Path, ply_path: Path, out_path: Path,
                         samples: int = 64, **_look):
        if out_path.exists():
            return {"path": str(out_path), "renderer": "blender",
                    "renderer_version": "5.2.1", "deterministic": False,
                    "note": "stale file taken for a render"}
        raise blender.BlenderError("Blender ran and produced no image.")

    monkeypatch.setattr(blender, "render", produces_nothing)
    monkeypatch.setattr(blender, "find_blender", lambda: "/bin/sh")

    with connection() as conn, conn.cursor() as cur:
        with pytest.raises(visuals.BlenderRenderFailed, match="produced no image"):
            visuals.render_through_blender(cur, visual_id=visual_id)


# ---------------------------------------------------------------------------
# The render itself, where Blender exists
# ---------------------------------------------------------------------------

@needs_blender
def test_the_job_renders_it_and_records_it_as_a_render(analysed):
    visuals._availability_cache.clear()
    _, visual_id = _surface(analysed)
    with connection() as conn, conn.cursor() as cur:
        queued = visuals.request_blender_render(cur, visual_id=visual_id)
    while Worker(worker_id="blender-test").run_once():
        pass

    with connection() as conn, conn.cursor() as cur:
        state = visuals.blender_render_state(cur, visual_id=visual_id)
        path = visuals.blender_render_file(cur, visual_id=visual_id)
        listed = visuals.stale_renders(cur, visual_id)

    assert state["run"]["run_id"] == queued["run_id"]
    assert state["run"]["state"] == "completed", state["run"]
    assert state["render"]["deterministic"] is False
    assert state["render"]["renderer_version"] == blender.version_of(
        blender.find_blender())
    assert path is not None and path.read_bytes()[:4] == b"\x89PNG"

    # Listed as what it is. A figure's files are enumerated here, and a render
    # must not read as one more PNG export.
    blender_rows = [r for r in listed if r["renderer"] == "blender"]
    assert len(blender_rows) == 1 and blender_rows[0]["deterministic"] is False

    # The colours are stated in the figure's own numbers: the ends of the ramp
    # are the lowest and highest fitted values, exactly (T193).
    with connection() as conn, conn.cursor() as cur:
        row = visuals.load_visual(cur, visual_id)
    fitted = [v for line in row["data"]["matrix"] for v in line]
    scale = state["colour_scale"]
    assert (scale["low"], scale["high"]) == (min(fitted), max(fitted))
    assert "lowest fitted value" in scale["text"]

    # A render of an earlier figure was coloured from other numbers: no scale.
    with connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE visuals SET spec_hash = 'changed' WHERE id = %s",
                    (visual_id,))
        stale = visuals.blender_render_state(cur, visual_id=visual_id)
        conn.rollback()
    assert stale["render"]["stale"] is True and stale["colour_scale"] is None


def test_a_surface_has_no_publication_export_to_mistake_it_for(analysed):
    """
    The fact the first version of this file got wrong, pinned so it stays
    known. It assumed a surface figure had an ordinary PNG export to sit
    beside, and there is none: the publication renderer draws flat figures
    only. Before the Blender route a surface left the system as a zip of
    geometry and nothing else; the render is now its only picture.

    That is also why the renderer is part of a render's identity rather than
    only a label on it — for the day a publication surface renderer exists and
    the two would otherwise be one row.
    """
    from throughline_visual.renderers.publication import RenderError

    _, visual_id = _surface(analysed)
    with connection() as conn, conn.cursor() as cur:
        with pytest.raises(RenderError, match="No publication renderer for surface"):
            visuals.render_visual(cur, visual_id=visual_id, fmt="png")


# ---------------------------------------------------------------------------
# The web spec, which a changed index had broken
# ---------------------------------------------------------------------------

def test_the_web_spec_renders_again(analysed):
    """
    Migration 0030 replaced the constraint this path's ON CONFLICT named, and
    every vega-lite render since failed with "no unique or exclusion
    constraint matching the ON CONFLICT specification". Nothing in the
    interface asks for it, which is why it went unseen.
    """
    visual_id = _flat(analysed)
    with connection() as conn, conn.cursor() as cur:
        first = visuals.render_visual(cur, visual_id=visual_id, fmt="vega-lite")
        again = visuals.render_visual(cur, visual_id=visual_id, fmt="vega-lite")
    assert first["payload"] and again["render_id"] == first["render_id"]


# ---------------------------------------------------------------------------
# Over HTTP
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    from throughline_api.app import app

    with TestClient(app) as test_client:
        yield test_client
    with connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE email = 'blender@lab.local'")


def _own(client, project_id: str) -> None:
    """Hand the fixture's project to the signed-in account."""
    email = sign_in(client, email="blender@lab.local", display_name="Blender")
    with connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE projects SET owner_user_id = "
                    "(SELECT id FROM users WHERE email = %s) WHERE id = %s",
                    (email, project_id))


def test_the_routes_refuse_a_signed_out_caller(client):
    for method, path in (("post", "/api/visuals/vis_x/blender-render"),
                         ("get", "/api/visuals/vis_x/blender-render"),
                         ("get", "/api/visuals/vis_x/blender-render.png")):
        assert getattr(client, method)(path).status_code == 401, path


def test_the_routes_over_http(client, analysed, monkeypatch):
    monkeypatch.setattr(visuals, "_blender_availability", lambda: AVAILABLE)
    project_id, visual_id = _surface(analysed)
    flat_id = _flat(analysed)
    _own(client, project_id)

    assert client.get(f"/api/visuals/{visual_id}/blender-render.png").status_code == 404
    refused = client.post(f"/api/visuals/{flat_id}/blender-render")
    assert refused.status_code == 400 and "third axis" in refused.json()["detail"]

    started = client.post(f"/api/visuals/{visual_id}/blender-render")
    assert started.status_code == 202, started.text
    state = client.get(f"/api/visuals/{visual_id}/blender-render").json()
    assert state["run"]["run_id"] == started.json()["run_id"]
    assert state["available"] is True and state["is_surface"] is True

    # The create route assembles its own reply, so a field reaches Publish only
    # if it is named there. This one decides whether a Download is offered.
    _, _, runs = analysed
    made = client.post(f"/api/projects/{project_id}/visuals",
                       json={"analysis_run_id": runs["correlation"]})
    assert made.status_code in (200, 201), made.text
    assert made.json()["exportable"] is True
