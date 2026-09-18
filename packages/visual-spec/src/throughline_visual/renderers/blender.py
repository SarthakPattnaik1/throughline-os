"""
Rendering a three-dimensional figure through Blender, when it is installed.

Everything else in this package draws deterministically: the same figure
exports the same bytes, and tests hold that down. This does not, and cannot —
a Blender render varies with the version, the build, the device it ran on and
the sampler's seed. That is not a defect to be engineered away; it is what a
physically-based renderer is.

So this sits **beside** the deterministic export rather than replacing it, and
everything it produces carries three things: that it is a render, which Blender
made it, and that the geometry came from the recorded figure. A picture that
cannot be reproduced byte for byte is still perfectly good evidence of shape —
what it must never do is pass as the export that can be.

**Blender is an executable, not a package.** It cannot be a `Pack` in
`extras.py`, because that registry finds Python distributions with `find_spec`
and would report an absent Blender as present the moment anything shipped a
module with the same name. It is found on the filesystem instead, and its
version is asked of the binary rather than assumed.

**Nothing a researcher typed reaches the script.** The generated script names
files this system wrote, in a directory this system made. Blender executes
Python with full privileges, so a title or a column name interpolated into it
would be a remote code execution with extra steps — the geometry files carry
all the researcher's content, and Blender only ever reads them as data.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

#: Where Blender puts itself, per platform. Checked in order, after `PATH`.
KNOWN_LOCATIONS = (
    "/Applications/Blender.app/Contents/MacOS/Blender",
    "/usr/bin/blender",
    "/usr/local/bin/blender",
    "/snap/bin/blender",
    "C:\\Program Files\\Blender Foundation\\Blender\\blender.exe",
)

#: An override, for an install somewhere unusual.
ENV_VAR = "THROUGHLINE_BLENDER"

#: How to get one. Named per platform rather than as a link, because a command
#: somebody can run beats a page they have to read.
INSTALL_HINT = (
    "Install Blender to render figures through it: `brew install --cask "
    "blender` on macOS, your package manager or blender.org elsewhere. It is "
    "optional — every other export works without it."
)

#: A render that has not finished by now is one nobody is waiting for any more.
TIMEOUT_SECONDS = 600


class BlenderError(RuntimeError):
    """Blender could not render this."""


def find_blender() -> str | None:
    """The Blender executable, or None.

    The environment variable wins, then `PATH`, then the places installers put
    it — an application bundle on macOS is not on `PATH` and never will be, so
    looking only there would report Blender missing on the platform most
    researchers here are using.
    """
    override = os.environ.get(ENV_VAR)
    if override:
        return override if Path(override).exists() else None

    found = shutil.which("blender")
    if found:
        return found

    for candidate in KNOWN_LOCATIONS:
        if Path(candidate).exists():
            return candidate
    return None


def version_of(executable: str) -> str:
    """Blender's own version string, asked of the binary.

    Asked rather than inferred from the path: an installation can be any
    version, and a figure that records the wrong renderer version is worse than
    one that records none.
    """
    try:
        done = subprocess.run([executable, "--version"], capture_output=True,
                              text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BlenderError("Blender could not be run on this machine.") from exc

    match = re.search(r"Blender\s+(\S+)", done.stdout or "")
    if not match:
        raise BlenderError(
            "That executable did not identify itself as Blender.")
    return match.group(1)


def availability() -> dict[str, Any]:
    """Whether this machine can render through Blender, and what it costs.

    The same shape the dataset formats report, for the same reason: a
    capability surface that says only what works leaves a researcher guessing
    why something is missing.
    """
    executable = find_blender()
    if not executable:
        return {
            "available": False,
            "path": None,
            "version": None,
            "withheld": ("Figures cannot be rendered through Blender. The 3D "
                         "geometry still exports, and every other figure "
                         "format is unaffected."),
            "install": INSTALL_HINT,
        }
    try:
        found_version = version_of(executable)
    except BlenderError:
        return {
            "available": False,
            "path": executable,
            "version": None,
            "withheld": "Blender is present but could not be started safely.",
            "install": INSTALL_HINT,
        }

    return {
        "available": True,
        "path": executable,
        "version": found_version,
        "withheld": "",
        "install": "",
        "note": ("A Blender render is not reproducible byte for byte — the "
                 "version, the build and the device all change it. It is "
                 "recorded as a render, with this version, beside the "
                 "deterministic export rather than in place of it."),
    }


#: The script Blender runs. It takes its paths from argv after `--`, so no
#: researcher-supplied text is ever interpolated into executable code.
RENDER_SCRIPT = '''
"""Render a Throughline scene. Written by throughline_visual, not by hand."""
import sys

import bpy
import mathutils

argv = sys.argv[sys.argv.index("--") + 1:]
obj_path, ply_path, out_path, samples = argv[0], argv[1], argv[2], int(argv[3])

# Start from nothing: the default cube would appear in the figure.
bpy.ops.wm.read_factory_settings(use_empty=True)


def _import(kind, path):
    """Import across the operator renames between Blender generations."""
    if kind == "obj":
        if hasattr(bpy.ops.wm, "obj_import"):
            bpy.ops.wm.obj_import(filepath=path)
        else:
            bpy.ops.import_scene.obj(filepath=path)
    else:
        if hasattr(bpy.ops.wm, "ply_import"):
            bpy.ops.wm.ply_import(filepath=path)
        else:
            bpy.ops.import_mesh.ply(filepath=path)
    return [o for o in bpy.context.scene.objects if o.select_get()] or \\
        list(bpy.context.scene.objects)


surface = _import("obj", obj_path)
for obj in surface:
    obj.name = "fitted model (not measurements)"

# Smooth shading. The mesh is a sampled grid, and flat-shading it draws every
# quad boundary as a crease — facets that are an artefact of how finely the
# surface was evaluated, not features of the fitted response. Rendered
# unsmoothed, the first figure out of this looked quilted.
for obj in surface:
    if obj.type == "MESH":
        for polygon in obj.data.polygons:
            polygon.use_smooth = True

# Frame whatever was imported, rather than trusting a fixed camera.
#
# The geometry is normalised to a unit cube, so a fixed camera *nearly* works
# — and "nearly" put the first render in the top-left corner with a third of
# the frame empty, because a surface fills that cube unevenly. The bounds are
# measured and the camera is aimed at their centre from a distance set by
# their size, so a tall surface and a flat one are both filled to the frame.
lo = [1e9, 1e9, 1e9]
hi = [-1e9, -1e9, -1e9]
for obj in surface:
    for corner in obj.bound_box:
        world = obj.matrix_world @ mathutils.Vector(corner)
        for axis in range(3):
            lo[axis] = min(lo[axis], world[axis])
            hi[axis] = max(hi[axis], world[axis])

centre = mathutils.Vector([(lo[a] + hi[a]) / 2 for a in range(3)])
radius = max(max(hi[a] - lo[a] for a in range(3)) / 2, 0.001)

target = bpy.data.objects.new("target", None)
target.location = centre
bpy.context.scene.collection.objects.link(target)

camera_data = bpy.data.cameras.new("camera")
camera = bpy.data.objects.new("camera", camera_data)
bpy.context.scene.collection.objects.link(camera)
# Three-quarter view: high enough to read the surface as a surface, low enough
# that a fold does not hide behind the one in front of it.
camera.location = centre + mathutils.Vector((2.5, -2.9, 2.0)) * radius * 1.5
constraint = camera.constraints.new(type="TRACK_TO")
constraint.target = target
constraint.track_axis = "TRACK_NEGATIVE_Z"
constraint.up_axis = "UP_Y"
bpy.context.scene.camera = camera

# A key light and a fill. Soft, because hard shadows on a data surface read as
# features of the data.
key = bpy.data.lights.new("key", type="AREA")
key.energy = 900
key.size = 6
key_obj = bpy.data.objects.new("key", key)
key_obj.location = centre + mathutils.Vector((4, -4, 6)) * radius
key_obj.rotation_euler = (0.6, 0.2, 0.8)
bpy.context.scene.collection.objects.link(key_obj)

fill = bpy.data.lights.new("fill", type="AREA")
fill.energy = 220
fill.size = 8
fill_obj = bpy.data.objects.new("fill", fill)
fill_obj.location = centre + mathutils.Vector((-5, -3, 2)) * radius
fill_obj.rotation_euler = (1.2, 0.0, -0.8)
bpy.context.scene.collection.objects.link(fill_obj)

material = bpy.data.materials.new("surface")
material.use_nodes = True
principled = material.node_tree.nodes.get("Principled BSDF")
if principled:
    principled.inputs["Base Color"].default_value = (0.25, 0.45, 0.75, 1.0)
    if "Roughness" in principled.inputs:
        principled.inputs["Roughness"].default_value = 0.45
for obj in surface:
    if obj.type == "MESH":
        obj.data.materials.clear()
        obj.data.materials.append(material)

scene = bpy.context.scene
scene.render.resolution_x = 1600
scene.render.resolution_y = 1200
scene.render.film_transparent = True
scene.render.image_settings.file_format = "PNG"
scene.render.filepath = out_path

# Prefer the fast rasteriser; fall back to whatever this build has. The engine
# identifiers have changed between releases, so this asks rather than assumes.
for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES"):
    try:
        scene.render.engine = engine
        break
    except TypeError:
        continue

if scene.render.engine == "CYCLES":
    scene.cycles.samples = samples

bpy.ops.render.render(write_still=True)
print("throughline: rendered to", out_path)
'''


def render(*, obj_path: Path, ply_path: Path, out_path: Path,
           samples: int = 64) -> dict[str, Any]:
    """Render the geometry, returning what made the picture.

    Refuses rather than falling back to a lesser picture: a researcher who
    asked for this render and silently received the ordinary export would have
    no way to tell, and the figure would carry a claim about how it was made
    that is not true.
    """
    executable = find_blender()
    if not executable:
        raise BlenderError(INSTALL_HINT)

    found_version = version_of(executable)
    script = out_path.parent / "render.py"
    script.write_text(RENDER_SCRIPT)

    command = [
        executable, "--background", "--factory-startup",
        "--python", str(script), "--",
        str(obj_path), str(ply_path), str(out_path), str(int(samples)),
    ]
    try:
        done = subprocess.run(command, capture_output=True, text=True,
                              timeout=TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired as exc:
        raise BlenderError(
            f"Blender did not finish within {TIMEOUT_SECONDS} seconds."
        ) from exc

    # Blender exits 0 on a great many failures, so the file is what is
    # believed rather than the status code.
    produced = out_path if out_path.exists() else None
    if produced is None:
        tail = (done.stderr or done.stdout or "").strip().splitlines()[-4:]
        raise BlenderError(
            "Blender ran and produced no image. " + " ".join(tail))

    return {
        "path": str(produced),
        "renderer": "blender",
        "renderer_version": found_version,
        "deterministic": False,
        "note": ("Rendered with Blender "
                 f"{found_version}. A render is not reproducible byte for "
                 "byte; the geometry it was made from is, and exports "
                 "separately."),
    }


__all__ = ["BlenderError", "ENV_VAR", "INSTALL_HINT", "KNOWN_LOCATIONS",
           "RENDER_SCRIPT", "TIMEOUT_SECONDS", "availability", "find_blender",
           "render", "version_of"]
