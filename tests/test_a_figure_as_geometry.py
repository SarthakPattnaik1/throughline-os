"""
A fitted surface exported as geometry, for Blender.

Figures already leave as SVG, PDF, PNG and TIFF — pictures of a surface from
one chosen angle. A surface is genuinely three-dimensional, and a researcher
who wants to light it, turn it, or put it in a poster from another angle had to
rebuild it by hand from the numbers.

Two things are being defended here, and neither is about file formats.

**The model and the measurements stay distinguishable.** In a rendered image a
fitted surface and a scatter of observations are visibly different things; in a
mesh file they are both just geometry. A poster showing a fitted plane captioned
as measurements is precisely the failure this product exists to prevent.

**The distortion is declared.** Data axes have unrelated units, so each is
normalised separately and the exported shape is not geometrically faithful — a
slope measured on the mesh is not the slope in the data. That has to be written
where somebody will read it, along with the ranges that map the cube back onto
the numbers.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from throughline_visual.renderers import geometry
from throughline_visual.spec import (
    Encoding, ResearchVisualSpec, VisualData, VisualType,
)


def _spec(visual_type=VisualType.SURFACE) -> ResearchVisualSpec:
    return ResearchVisualSpec(
        visual_type=visual_type,
        analysis_run_id="run_1",
        title="Yield against rainfall and temperature",
        x=Encoding(field="rainfall", label="Rainfall"),
        y=Encoding(field="yield", label="Yield"),
    )


#: A plane with known values: z = x + 10*y over x in 0..3, y in 0..2.
X_VALUES = [0.0, 1.0, 2.0, 3.0]
Y_VALUES = [0.0, 1.0, 2.0]


def _data() -> VisualData:
    return VisualData(
        x_values=list(X_VALUES),
        y_values=list(Y_VALUES),
        matrix=[[x + 10.0 * y for x in X_VALUES] for y in Y_VALUES],
        series=[{"x": 0.0, "y": 0.0, "z": 0.0},
                {"x": 3.0, "y": 2.0, "z": 23.0},
                {"x": 1.5, "y": 1.0, "z": 11.5}],
        sample_size=3,
        note="The surface is the fitted model.",
    )


def _vertices(obj: str) -> list[tuple[float, float, float]]:
    return [tuple(float(part) for part in line.split()[1:4])
            for line in obj.splitlines() if line.startswith("v ")]


def _faces(obj: str) -> list[list[int]]:
    return [[int(part) for part in line.split()[1:]]
            for line in obj.splitlines() if line.startswith("f ")]


# ---------------------------------------------------------------------------
# The mesh is the surface that was recorded
# ---------------------------------------------------------------------------

def test_every_grid_point_becomes_a_vertex():
    obj = geometry.surface_obj(_spec(), _data())

    assert len(_vertices(obj)) == len(X_VALUES) * len(Y_VALUES)


def test_the_grid_is_closed_into_quads():
    obj = geometry.surface_obj(_spec(), _data())
    faces = _faces(obj)

    assert len(faces) == (len(X_VALUES) - 1) * (len(Y_VALUES) - 1)
    assert all(len(face) == 4 for face in faces)


def test_every_face_faces_up_the_value_axis():
    """The value is the file's Y. A face wound the other way is lit as the
    underside: Blender rendered the slope facing the light in shadow."""
    obj = geometry.surface_obj(_spec(), _data())
    vertices = _vertices(obj)
    for face in _faces(obj):
        a, b, c = (vertices[i - 1] for i in face[:3])
        u = [b[k] - a[k] for k in range(3)]
        v = [c[k] - a[k] for k in range(3)]
        normal_y = u[2] * v[0] - u[0] * v[2]
        assert normal_y > 0, face


def test_no_face_refers_to_a_vertex_that_is_not_there():
    """OBJ indexes from 1, so an off-by-one here is a mesh that will not open."""
    obj = geometry.surface_obj(_spec(), _data())
    count = len(_vertices(obj))

    for face in _faces(obj):
        for index in face:
            assert 1 <= index <= count


def test_a_vertex_reads_back_to_the_value_it_came_from():
    """
    The mapping in the header is the only way the numbers survive the trip, so
    it has to be the mapping actually used.
    """
    obj = geometry.surface_obj(_spec(), _data())
    vertices = _vertices(obj)

    # The corner at x=3, y=2 is z=23, the maximum of both x and z.
    corner = vertices[-1]
    assert corner[0] == pytest.approx(1.0)    # x at its top
    assert corner[1] == pytest.approx(1.0)    # z (up) at its top
    assert corner[2] == pytest.approx(1.0)    # y at its top

    first = vertices[0]
    assert first == pytest.approx((-1.0, -1.0, -1.0))


def test_the_fitted_value_is_the_vertical_axis():
    """
    Blender's up is Y. A surface exported with the outcome on a horizontal
    axis arrives on its side, and every render of it is wrong in a way that
    looks deliberate.
    """
    obj = geometry.surface_obj(_spec(), _data())
    vertices = _vertices(obj)

    # z runs 0..23 across the grid; x runs 0..3. The vertical coordinate must
    # follow the fitted value, so it varies with the row as well as the column.
    verticals = {round(v[1], 6) for v in vertices}
    assert len(verticals) > len(X_VALUES)


# ---------------------------------------------------------------------------
# The model and the measurements stay apart
# ---------------------------------------------------------------------------

def test_the_observations_are_a_separate_file_of_points():
    ply = geometry.observations_ply(_spec(), _data())

    assert ply.startswith("ply")
    assert "element vertex 3" in ply
    body = ply.split("end_header\n")[1].strip().splitlines()
    assert len(body) == 3


def test_an_observation_missing_a_coordinate_is_not_a_point():
    """A point with a missing coordinate is not somewhere."""
    data = _data()
    data.series.append({"x": 1.0, "y": 1.0})

    ply = geometry.observations_ply(_spec(), data)

    assert "element vertex 3" in ply


def test_the_mesh_says_it_is_a_model_and_not_measurements():
    obj = geometry.surface_obj(_spec(), _data())

    assert "FITTED SURFACE" in obj
    assert "not data" in obj.lower() or "not measurements" in obj.lower()


def test_the_points_say_they_are_measurements():
    ply = geometry.observations_ply(_spec(), _data())

    assert "OBSERVATIONS" in ply


# ---------------------------------------------------------------------------
# The distortion is declared, in every file
# ---------------------------------------------------------------------------

def test_every_file_says_the_axes_were_scaled_separately():
    """
    The exported shape is not faithful. Somebody who opens one file and not
    the others must still be told, so it is in all of them rather than only in
    the README.
    """
    obj = geometry.surface_obj(_spec(), _data())
    ply = geometry.observations_ply(_spec(), _data())
    text = geometry.readme(_spec(), _data())

    for content in (obj, ply, text):
        assert "SEPARATELY" in content or "separately" in content
    for content in (obj, ply):
        assert "not the angle or slope in the data" in content


def test_the_ranges_that_map_back_to_data_are_written_down():
    obj = geometry.surface_obj(_spec(), _data())

    assert "0 to 3" in obj      # x
    assert "0 to 23" in obj     # z, including the observations' range


def test_the_readme_names_which_file_is_which():
    text = geometry.readme(_spec(), _data())

    assert "fitted_surface.obj" in text
    assert "observations.ply" in text
    assert "claim the data does not support" in text


# ---------------------------------------------------------------------------
# The bundle, and what has no geometry at all
# ---------------------------------------------------------------------------

def test_the_bundle_holds_both_files_and_the_readme():
    payload = geometry.bundle(_spec(), _data())

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert set(archive.namelist()) == {
            "fitted_surface.obj", "observations.ply", "README.md",
            # Generated from the same spec as the geometry, so the labels it
            # writes into the viewport cannot drift from the numbers in the
            # mesh. Executed against a stand-in for Blender in
            # `test_the_blender_script_runs.py`.
            "import_scene.py"}


def test_a_flat_figure_is_refused_by_name():
    with pytest.raises(geometry.GeometryError, match="flat"):
        geometry.bundle(_spec(VisualType.SCATTER), _data())


def test_a_surface_with_no_grid_is_refused():
    empty = VisualData(x_values=[], y_values=[], matrix=[])

    with pytest.raises(geometry.GeometryError, match="no surface grid"):
        geometry.bundle(_spec(), empty)


def test_a_constant_axis_does_not_collapse_the_mesh():
    """A flat predictor has no extent; the mesh must still be a mesh."""
    data = _data()
    data.x_values = [2.0, 2.0, 2.0, 2.0]

    obj = geometry.surface_obj(_spec(), data)

    assert len(_faces(obj)) == (len(X_VALUES) - 1) * (len(Y_VALUES) - 1)
    assert all(all(abs(c) <= 1.0001 for c in v) for v in _vertices(obj))


def test_the_export_is_identical_between_runs():
    """A figure that has not changed must produce the same bytes."""
    first = geometry.bundle(_spec(), _data())
    second = geometry.bundle(_spec(), _data())

    with zipfile.ZipFile(io.BytesIO(first)) as a, zipfile.ZipFile(io.BytesIO(second)) as b:
        assert [a.read(n) for n in sorted(a.namelist())] == \
               [b.read(n) for n in sorted(b.namelist())]
