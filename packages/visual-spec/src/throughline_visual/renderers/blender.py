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

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .. import tokens

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
import json
import sys

import bpy
import mathutils
from bpy_extras.object_utils import world_to_camera_view

argv = sys.argv[sys.argv.index("--") + 1:]
obj_path, ply_path, out_path, samples = argv[0], argv[1], argv[2], int(argv[3])
# The look: colours, size and style, written by `scene_arguments` from the
# design tokens. Numbers and colour triples only; no researcher text.
with open(argv[4]) as handle:
    look = json.load(handle)

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
direction = mathutils.Vector((2.5, -2.9, 2.0)).normalized()
distance = 4.4 * radius
camera.location = centre + direction * distance
constraint = camera.constraints.new(type="TRACK_TO")
constraint.target = target
constraint.track_axis = "TRACK_NEGATIVE_Z"
constraint.up_axis = "UP_Y"
bpy.context.scene.camera = camera
bpy.context.scene.render.resolution_x = look["width"]
bpy.context.scene.render.resolution_y = look["height"]
if look["style"] == "hero":
    camera_data.lens = 60

# Then fit it: project the surface's own vertices into the frame and move the
# camera until the widest reaches 85% of it. A distance from the bounds left
# the first renders using 40% of the frame — a surface fills its cube
# unevenly, and the cube's empty corners still set the distance.
corners = []
for obj in surface:
    if obj.type == "MESH":
        vertices = obj.data.vertices
        step = max(1, len(vertices) // 4000)
        corners += [obj.matrix_world @ vertices[i].co
                    for i in range(0, len(vertices), step)]
if not corners:
    corners = [mathutils.Vector((x, y, z)) for x in (lo[0], hi[0])
               for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
for _ in range(4):
    bpy.context.view_layer.update()
    spread = 0.0
    for corner in corners:
        seen = world_to_camera_view(bpy.context.scene, camera, corner)
        spread = max(spread, abs(seen.x - 0.5), abs(seen.y - 0.5))
    distance *= (spread * 2) / 0.85
    camera.location = centre + direction * distance

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
# On a dark ground there is no bounce light from the backdrop, so the side
# the key misses needs more fill to stay readable as the same ramp.
fill.energy = look["fill"]
fill.size = 8
fill_obj = bpy.data.objects.new("fill", fill)
fill_obj.location = centre + mathutils.Vector((-5, -3, 2)) * radius
fill_obj.rotation_euler = (1.2, 0.0, -0.8)
bpy.context.scene.collection.objects.link(fill_obj)

# A rim from behind, so the far edge of the surface separates from the ground
# instead of sinking into it — on a dark ground the unlit side was lost.
rim = bpy.data.lights.new("rim", type="AREA")
rim.energy = 600
rim.size = 5
rim_obj = bpy.data.objects.new("rim", rim)
rim_obj.location = centre + mathutils.Vector((-2, 5, 4)) * radius
rim_constraint = rim_obj.constraints.new(type="TRACK_TO")
rim_constraint.target = target
rim_constraint.track_axis = "TRACK_NEGATIVE_Z"
rim_constraint.up_axis = "UP_Y"
bpy.context.scene.collection.objects.link(rim_obj)

# The surface is coloured by its height, on the shared sequential ramp — the
# same viridis the heatmaps and density figures use. It was one flat blue, so
# the only cue to the fitted value was shading, which a light moves; colour by
# value is a second, lighting-independent reading of the same axis.
material = bpy.data.materials.new("surface")
material.use_nodes = True
nodes = material.node_tree.nodes
links = material.node_tree.links
principled = nodes.get("Principled BSDF")
position = nodes.new("ShaderNodeNewGeometry")
separate = nodes.new("ShaderNodeSeparateXYZ")
height = nodes.new("ShaderNodeMapRange")
ramp = nodes.new("ShaderNodeValToRGB")
# World-space height, mapped from the measured bounds: the bottom of the
# fitted range is the ramp's first stop and the top its last. Object-space
# coordinates are not the value axis — the importer turns the object to put
# the file's Y up, so they ran along a predictor instead.
links.new(position.outputs["Position"], separate.inputs[0])
links.new(separate.outputs["Z"], height.inputs["Value"])
height.inputs["From Min"].default_value = lo[2]
height.inputs["From Max"].default_value = hi[2]
links.new(height.outputs["Result"], ramp.inputs["Fac"])
stops = look["ramp_linear"]
elements = ramp.color_ramp.elements
while len(elements) < len(stops):
    elements.new(0.5)
for index, stop in enumerate(stops):
    elements[index].position = index / (len(stops) - 1)
    elements[index].color = (stop[0], stop[1], stop[2], 1.0)
if principled:
    links.new(ramp.outputs["Color"], principled.inputs["Base Color"])
    if "Roughness" in principled.inputs:
        principled.inputs["Roughness"].default_value = 0.55
    # A soft sheen, not a mirror: a bright highlight is white, and white is
    # not on the ramp — it reads as a value the surface does not have.
    if "Specular IOR Level" in principled.inputs:
        principled.inputs["Specular IOR Level"].default_value = 0.25
for obj in surface:
    if obj.type == "MESH":
        obj.data.materials.clear()
        obj.data.materials.append(material)

scene = bpy.context.scene
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGBA"
scene.render.filepath = out_path

# Colours as the tokens name them. Blender's default view transform (AgX,
# Filmic before it) compresses and desaturates for photographic highlights,
# which turned the ramp's yellow end beige — the figure's colours stopped
# matching the colour bar every other figure uses.
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"

world = bpy.data.worlds.new("backdrop")
scene.world = world
world.use_nodes = True
background = world.node_tree.nodes.get("Background")
if look["style"] == "hero":
    # A soft vertical gradient behind the figure, in the ground's tones, seen
    # only by the camera: `Window` coordinates run top to bottom of the frame.
    scene.render.film_transparent = False
    window = world.node_tree.nodes.new("ShaderNodeTexCoord")
    split = world.node_tree.nodes.new("ShaderNodeSeparateXYZ")
    wash = world.node_tree.nodes.new("ShaderNodeValToRGB")
    world.node_tree.links.new(window.outputs["Window"], split.inputs[0])
    world.node_tree.links.new(split.outputs["Y"], wash.inputs["Fac"])
    low, high = look["backdrop_linear"]
    wash.color_ramp.elements[0].color = (low[0], low[1], low[2], 1.0)
    wash.color_ramp.elements[1].color = (high[0], high[1], high[2], 1.0)
    world.node_tree.links.new(wash.outputs["Color"], background.inputs["Color"])
    background.inputs["Strength"].default_value = 1.0
    # Shallow depth of field, focused on the centre of the surface: the eye
    # goes where the figure is sharp. Never in the plain figure, where every
    # part of the surface must be equally legible.
    camera_data.dof.use_dof = True
    camera_data.dof.focus_object = target
    camera_data.dof.aperture_fstop = look["fstop"]
else:
    # A figure is placed on a page or a slide, so it carries no ground of its
    # own; the world only lights it, dimly, in the ground's tone.
    scene.render.film_transparent = True
    ambient = look["backdrop_linear"][1]
    background.inputs["Color"].default_value = (ambient[0], ambient[1], ambient[2], 1.0)
    background.inputs["Strength"].default_value = 0.35

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


#: The two looks a render can take. `figure` is for a page: transparent, every
#: part in focus. `hero` is for a cover or a slide: a backdrop and a shallow
#: focus, and not for reading values off.
STYLES = ("figure", "hero")


def _linear(hex_colour: str) -> list[float]:
    """sRGB `#RRGGBB` to the linear triple Blender's colour inputs expect."""
    value = hex_colour.lstrip("#")
    channels = (int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return [round(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4, 6)
            for c in channels]


def _mix(a: str, b: str, amount: float) -> str:
    """`amount` of `a` over `b`, in sRGB."""
    pa = [int(a.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    pb = [int(b.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x * amount + y * (1 - amount)):02X}" for x, y in zip(pa, pb))


def scene_arguments(*, ground: str = "light", style: str = "figure") -> dict[str, Any]:
    """Everything the render script needs to know about the look, from the tokens.

    Written to a JSON file the script reads, so no colour — and nothing else —
    is ever interpolated into the script's source.
    """
    if style not in STYLES:
        raise BlenderError(f"{style!r} is not a render style. Styles: {', '.join(STYLES)}")
    neutrals = tokens.ink(ground)
    # The backdrop runs from the ground to a faint wash of the palette's first
    # hue: the page's own tone, lifted just enough to separate the figure.
    wash = _mix(tokens.CATEGORICAL[0], neutrals["ground"], 0.10 if ground == "light" else 0.18)
    hero = style == "hero"
    return {
        "style": style,
        "ground": ground,
        "ramp": list(tokens.SEQUENTIAL_STOPS),
        "ramp_linear": [_linear(stop) for stop in tokens.SEQUENTIAL_STOPS],
        "backdrop": [neutrals["ground"], wash],
        "backdrop_linear": [_linear(neutrals["ground"]), _linear(wash)],
        "width": 2560 if hero else 2400,
        "height": 1440 if hero else 1800,
        "fstop": 0.35,
        "fill": 220 if ground == "light" else 520,
    }


def render(*, obj_path: Path, ply_path: Path, out_path: Path,
           samples: int = 64, ground: str = "light",
           style: str = "figure") -> dict[str, Any]:
    """Render the geometry, returning what made the picture.

    Refuses rather than falling back to a lesser picture: a researcher who
    asked for this render and silently received the ordinary export would have
    no way to tell, and the figure would carry a claim about how it was made
    that is not true.
    """
    executable = find_blender()
    if not executable:
        raise BlenderError(INSTALL_HINT)

    arguments = scene_arguments(ground=ground, style=style)
    found_version = version_of(executable)
    script = out_path.parent / "render.py"
    script.write_text(RENDER_SCRIPT)
    look_path = out_path.parent / "look.json"
    look_path.write_text(json.dumps(arguments))

    command = [
        executable, "--background", "--factory-startup",
        "--python", str(script), "--",
        str(obj_path), str(ply_path), str(out_path), str(int(samples)),
        str(look_path),
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
        "style": style,
        "ground": ground,
        "note": ("Rendered with Blender "
                 f"{found_version}. A render is not reproducible byte for "
                 "byte; the geometry it was made from is, and exports "
                 "separately."),
    }


__all__ = ["BlenderError", "ENV_VAR", "INSTALL_HINT", "KNOWN_LOCATIONS",
           "RENDER_SCRIPT", "STYLES", "TIMEOUT_SECONDS", "availability",
           "find_blender", "render", "scene_arguments", "version_of"]
