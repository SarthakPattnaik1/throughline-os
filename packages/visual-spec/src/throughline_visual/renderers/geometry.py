"""
A fitted surface as geometry, for Blender and anything else that reads a mesh.

Figures already leave here as SVG, PDF, PNG and TIFF — pictures of a surface
from one chosen angle. A surface figure is genuinely three-dimensional, and a
researcher who wants to light it, turn it, or put it in a poster at a different
angle currently has to rebuild it by hand from the numbers. This writes the
geometry instead.

**Two objects, and the difference between them is the whole point.** The mesh
is the *fitted model* evaluated from the recorded coefficients; the points are
the *observations* it was fitted to. In a rendered image the surface and the
scatter are visibly different things. In a mesh file they would both just be
geometry, so they are separate files with names that say which is which, and
the README repeats it. A poster showing a fitted plane captioned as
measurements is exactly the failure this product exists to prevent.

**Each axis is normalised independently, and that distorts shape.** Data axes
have unrelated units — years against dollars against a percentage — so nothing
here can produce a geometrically faithful object; and exported in data units, a
surface spanning millions arrives in Blender as an object kilometres wide,
past the default clipping plane. So each axis is mapped to -1..1 and **the
exact ranges are written into every file**, which is the only way the mapping
back to data survives the trip. This is the same declaration the interactive
3D charts make about themselves; a slope read off the exported mesh is not the
slope in the data, and the files say so where a reader will see it.

Formats are chosen to be opened, not to be clever: Wavefront OBJ for the mesh
and PLY for the points, both of which Blender imports with no add-on.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

from ..spec import ResearchVisualSpec, VisualData, VisualType


class GeometryError(ValueError):
    """This figure has no three-dimensional form to export."""


#: What the normalised cube spans on each axis.
CUBE = 1.0


def _range(values: list[float]) -> tuple[float, float]:
    low, high = min(values), max(values)
    if high == low:
        # A constant axis has no extent. Giving it one keeps the mesh from
        # collapsing to a line while leaving the recorded range honest.
        high = low + 1.0
    return low, high


def _norm(value: float, low: float, high: float) -> float:
    return (2.0 * (value - low) / (high - low) - 1.0) * CUBE


def _axis_names(spec: ResearchVisualSpec) -> tuple[str, str, str]:
    x = (spec.x.label or spec.x.field) if spec.x else "x"
    y = (spec.y.label or spec.y.field) if spec.y else "y"
    # The z axis of a surface is the outcome, which the spec carries as `y`;
    # `x` and the second predictor are the ground plane. Named from the data
    # rather than invented, so the README can be read against the analysis.
    return x, "second predictor", y


def _ranges(data: VisualData) -> dict[str, tuple[float, float]]:
    grid = [value for row in data.matrix for value in row]
    if not data.x_values or not data.y_values or not grid:
        raise GeometryError(
            "This figure carries no surface grid, so there is no mesh to write.")
    zs = list(grid) + [float(p["z"]) for p in data.series if "z" in p]
    return {
        "x": _range([float(v) for v in data.x_values]),
        "y": _range([float(v) for v in data.y_values]),
        "z": _range([float(v) for v in zs]),
    }


def _header(spec: ResearchVisualSpec, ranges: dict[str, tuple[float, float]],
            comment: str, what: str) -> list[str]:
    x_name, y_name, z_name = _axis_names(spec)
    lines = [
        f"{comment} {what}",
        f"{comment} Figure: {spec.title or 'untitled'}",
        f"{comment}",
        f"{comment} Each axis is normalised to -1..1 SEPARATELY, so the shape",
        f"{comment} is not geometrically faithful: an angle or a slope measured",
        f"{comment} on this mesh is not the angle or slope in the data. The",
        f"{comment} ranges below are what the cube maps back onto.",
        f"{comment}",
        f"{comment}   x  {x_name}: {ranges['x'][0]:.10g} to {ranges['x'][1]:.10g}",
        f"{comment}   y  {y_name}: {ranges['y'][0]:.10g} to {ranges['y'][1]:.10g}",
        f"{comment}   z  {z_name}: {ranges['z'][0]:.10g} to {ranges['z'][1]:.10g}",
        f"{comment}",
        f"{comment} To read a value back: data = low + (coord + 1) / 2 * (high - low)",
    ]
    return lines


def surface_obj(spec: ResearchVisualSpec, data: VisualData) -> str:
    """The fitted surface as a quad mesh.

    OBJ indexes vertices from 1, and a face winding that disagrees with itself
    leaves Blender shading the surface inside out — so the quads are emitted in
    a consistent order rather than whichever way the loop happened to run.
    """
    ranges = _ranges(data)
    xs = [float(v) for v in data.x_values]
    ys = [float(v) for v in data.y_values]

    lines = _header(spec, ranges, "#", "The FITTED SURFACE — a model, not measurements.")
    lines += [
        "#",
        "# This mesh is the model evaluated over the observed range of both",
        "# predictors. It is not data. The observations it was fitted to are",
        "# in observations.ply beside this file.",
        "",
        "o fitted_surface",
    ]

    for row_index, y in enumerate(ys):
        for column_index, x in enumerate(xs):
            z = float(data.matrix[row_index][column_index])
            lines.append(
                f"v {_norm(x, *ranges['x']):.6f} "
                f"{_norm(z, *ranges['z']):.6f} "
                f"{_norm(y, *ranges['y']):.6f}")

    # Y is up in Blender, so the fitted value takes the vertical axis above and
    # the two predictors lie in the ground plane. A surface exported with the
    # outcome on a horizontal axis arrives on its side.
    #
    # Counter-clockwise seen from above, so each face's normal points up the
    # value axis. It was wound the other way: every normal pointed down, and
    # Blender lit the surface as its underside — the slope facing the key
    # light rendered in shadow, and the peak came out darker than the valleys.
    width = len(xs)
    for row_index in range(len(ys) - 1):
        for column_index in range(width - 1):
            base = row_index * width + column_index + 1
            lines.append(
                f"f {base} {base + width} {base + width + 1} {base + 1}")

    return "\n".join(lines) + "\n"


def observations_ply(spec: ResearchVisualSpec, data: VisualData) -> str:
    """The observations as a point cloud.

    PLY rather than more OBJ vertices, because a point cloud in an OBJ file is
    a set of vertices no face refers to — which several importers silently drop
    as unused. A file the researcher opens to find nothing in it is worse than
    no file.
    """
    ranges = _ranges(data)
    points = [p for p in data.series
              if all(k in p for k in ("x", "y", "z"))]

    header = _header(spec, ranges, "comment",
                     "The OBSERVATIONS — the measurements the surface was fitted to.")
    lines = ["ply", "format ascii 1.0"] + header + [
        f"element vertex {len(points)}",
        "property float x", "property float y", "property float z",
        "end_header",
    ]
    for point in points:
        lines.append(
            f"{_norm(float(point['x']), *ranges['x']):.6f} "
            f"{_norm(float(point['z']), *ranges['z']):.6f} "
            f"{_norm(float(point['y']), *ranges['y']):.6f}")
    return "\n".join(lines) + "\n"


def readme(spec: ResearchVisualSpec, data: VisualData) -> str:
    ranges = _ranges(data)
    x_name, y_name, z_name = _axis_names(spec)
    points = sum(1 for p in data.series if all(k in p for k in ("x", "y", "z")))
    return f"""# {spec.title or 'Figure'} — as 3D geometry

Two files, and they are different kinds of thing:

- **`fitted_surface.obj`** — the model, evaluated from the coefficients the
  analysis recorded. Nothing was refitted to produce it.
- **`observations.ply`** — the {points} measurements the model was fitted to.

Keep them labelled that way in whatever you build. A surface captioned as
measurements is a claim the data does not support.

## The shape is not faithful, and here is the mapping

Each axis is normalised to -1..1 **separately**, because data axes have
unrelated units and a surface exported in data units arrives kilometres wide
and past the viewport's clipping plane. The consequence is that **an angle or
a slope measured on this mesh is not the one in the data**.

| axis | variable | -1 | +1 |
|---|---|---|---|
| x | {x_name} | {ranges['x'][0]:.10g} | {ranges['x'][1]:.10g} |
| y (up) | {z_name} | {ranges['z'][0]:.10g} | {ranges['z'][1]:.10g} |
| z | {y_name} | {ranges['y'][0]:.10g} | {ranges['y'][1]:.10g} |

To read a coordinate back into data units:
`value = low + (coord + 1) / 2 * (high - low)`

Y is the vertical axis, which is Blender's convention, so the fitted value is
up and the two predictors lie in the ground plane.

## Importing

Easiest: run `import_scene.py` from this folder.

    blender --python import_scene.py

It loads both files, names them for what they are rather than for their
filenames, and writes the axis ranges above into the scene as text so a render
carries its own scale. It handles both Blender 3 and 4, whose importers are
named differently. **It has not been run in Blender by the machine that wrote
it** — it is executed against a stand-in during testing, which checks the logic
and not Blender's acceptance of it.

By hand instead: **File → Import → Wavefront (.obj)** and **File → Import →
Stanford PLY (.ply)**, both built in. The point cloud imports as a mesh with
vertices and no faces; give it a geometry-nodes point cloud or a particle
instance to see the points.

{data.note}
"""


def import_script(spec: ResearchVisualSpec, data: VisualData) -> str:
    """A Blender script that imports both files and labels what they are.

    Generated from the same spec and data as the geometry, for the reason
    `code_export` gives: a script written by hand beside the files it loads is
    a second source of truth that can disagree with them. Here the axis names
    and the ranges are substituted from the recorded figure, so the labels in
    the viewport cannot drift from the numbers in the mesh.

    Two things it does that a manual import will not.

    **It names the objects for what they are** — "fitted model (not
    measurements)" and "observations (measured)" — because in the outliner they
    are otherwise `fitted_surface` and `observations`, two meshes of equal
    standing, and the whole risk in exporting a model as geometry is that it
    stops being distinguishable from data.

    **It writes the axis ranges into the scene as text**, so a render carries
    the mapping back to data units with it. A figure that leaves the file
    without its scale is a picture of a shape.

    It is deliberately tolerant of Blender's version split: the importers were
    renamed in 4.0, and a script that works only on the version its author
    happened to have is a script most people cannot run. It has not been run
    in Blender here — Blender is not installed on the machine that generated
    it — so it prints what it did rather than assuming.
    """
    ranges = _ranges(data)
    x_name, y_name, z_name = _axis_names(spec)
    points = sum(1 for p in data.series if all(k in p for k in ("x", "y", "z")))

    return f'''"""Import this figure into Blender.

Run it from the directory holding fitted_surface.obj and observations.ply:

    blender --python import_scene.py

or open Blender, go to the Scripting workspace, and run it there.

Generated by Throughline from the recorded figure. The axis names and ranges
below come from the analysis, not from anybody typing them in again.
"""

import os

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))

#: Which variable each axis carries, and what -1 and +1 mean on it. Y is up,
#: which is Blender's convention, so the fitted value is the vertical axis.
AXES = [
    ("X", {x_name!r}, {ranges["x"][0]!r}, {ranges["x"][1]!r}),
    ("Y (up)", {z_name!r}, {ranges["z"][0]!r}, {ranges["z"][1]!r}),
    ("Z", {y_name!r}, {ranges["y"][0]!r}, {ranges["y"][1]!r}),
]


def _import(kind, filename):
    """Import one file, across the operator rename that landed in Blender 4.0.

    A script that works only on the version its author happened to have is a
    script most people cannot run.
    """
    path = os.path.join(HERE, filename)
    if not os.path.exists(path):
        raise SystemExit("Missing {{}} — run this from the folder the zip was "
                         "extracted into.".format(filename))

    before = set(bpy.data.objects)
    if kind == "obj":
        if hasattr(bpy.ops.wm, "obj_import"):
            bpy.ops.wm.obj_import(filepath=path)          # Blender 4.x
        else:
            bpy.ops.import_scene.obj(filepath=path)       # Blender 3.x
    else:
        if hasattr(bpy.ops.wm, "ply_import"):
            bpy.ops.wm.ply_import(filepath=path)          # Blender 4.x
        else:
            bpy.ops.import_mesh.ply(filepath=path)        # Blender 3.x
    return [o for o in bpy.data.objects if o not in before]


def _label(text, location):
    bpy.ops.object.text_add(location=location)
    label = bpy.context.object
    label.data.body = text
    label.name = "label: " + text
    return label


def main():
    surface = _import("obj", "fitted_surface.obj")
    points = _import("ply", "observations.ply")

    # Named for what they are, not for their filenames. In the outliner these
    # are otherwise two meshes of equal standing, and the whole risk in
    # exporting a model as geometry is that it stops being distinguishable
    # from the measurements.
    for obj in surface:
        obj.name = "fitted model (not measurements)"
    for obj in points:
        obj.name = "observations (measured)"

    # The scale, written into the scene, so a render carries the mapping back
    # to data units with it.
    for index, (axis, variable, low, high) in enumerate(AXES):
        _label("{{}}: {{}}  [{{:.4g}} .. {{:.4g}}]".format(axis, variable, low, high),
               (-1.0, -1.4 - index * 0.35, -1.0))
    _label("Axes are scaled separately: a slope here is not the slope in the data.",
           (-1.0, -1.4 - len(AXES) * 0.35, -1.0))

    print("Throughline: imported {{}} surface object(s) and {{}} point object(s), "
          "{points} observations.".format(len(surface), len(points)))
    print("Axes are normalised to -1..1 separately. See README.md for the "
          "mapping back to data units.")


if __name__ == "__main__":
    main()
'''


def bundle(spec: ResearchVisualSpec, data: VisualData) -> bytes:
    """Both files and the README, as one zip."""
    if spec.visual_type is not VisualType.SURFACE:
        raise GeometryError(
            f"A {spec.visual_type.value} figure is flat: it has no third axis "
            "to export as geometry. Only a fitted surface does.")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("fitted_surface.obj", surface_obj(spec, data))
        archive.writestr("observations.ply", observations_ply(spec, data))
        archive.writestr("README.md", readme(spec, data))
        archive.writestr("import_scene.py", import_script(spec, data))
    return buffer.getvalue()


__all__ = ["CUBE", "GeometryError", "bundle", "import_script",
           "observations_ply", "readme", "surface_obj"]
