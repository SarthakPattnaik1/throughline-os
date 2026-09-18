"use client";

/**
 * A shell drawn in space (§9 isosurface).
 *
 * The renderer for the eleven catalogue entries that share the `isosurface`
 * primitive — sphere, ellipsoid, torus, hyperboloid, implicit and constraint
 * surfaces, molecular orbitals, tumour margins, and the decision boundary,
 * which in three inputs is a shell rather than a height field.
 *
 * **Triangles are sorted by depth and drawn back to front.** A closed shell is
 * mostly hidden by itself, so the ordering is what makes it a solid rather than
 * a tangle of overlapping facets. Sorted by centroid, which is exact for a
 * convex shell and can mis-order a pair of near-coplanar triangles that
 * interpenetrate — a real limitation of painter's-algorithm rendering and the
 * reason this file does not claim to be a depth buffer.
 *
 * **Shading is flat and comes from the extracted normal, not from a light
 * model.** Every facet takes one tone from the angle between its normal and a
 * fixed lamp. That is enough to read a curved shell — the eye recovers form
 * from the gradient of shading across facets — and it avoids inventing
 * specular highlights, which on a scientific surface read as features of the
 * data rather than of the renderer.
 *
 * **The three directions are the grid's own, so they are framed and
 * numbered.** A shell is a set of positions in a sampled box, and where it
 * reaches is most of what a reader wants off it. The grid knows how many
 * samples it holds in each direction and cannot know how far apart they are,
 * so the default scale is the sample index and the caller supplies millimetres
 * if it has them. The *level* is not one of these axes: it is one value across
 * the whole shell, it is already on the control below the picture, and a
 * colour key beside a surface shaded by a lamp would offer a scale the
 * shading does not carry.
 *
 * **Back faces are kept, not culled.** Culling assumes a closed shell, and this
 * one is deliberately open wherever the data had holes: cull the back faces and
 * an open shell shows the inside of nothing, so the hole stops being visible as
 * a hole. The count of what is open is in the caption, and the picture agrees
 * with it.
 */

import {
  useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState,
} from "react";
import { useMounted } from "@/lib/charts/useMounted";
import { ScreenPoint, TargetRef, VisualizationController } from "@/lib/spatial/commands";
import { canvasPoint, isClick } from "@/lib/charts/pointer";
import { AXES_SCALED_SEPARATELY, Axes3D, Camera, DEFAULT_CAMERA, insidePolygon, resetCamera, rotateCamera, toCanvas, zoomCamera } from "@/lib/charts/scene3d";
import { AxisNaming, framing, namedIndex } from "@/lib/charts/frame";
import { useSpatialKeys } from "@/lib/charts/spatialKeys";
import { ChartExport } from "@/components/charts/ChartExport";
import { isZoomWheel, wheelZoomFactor } from "@/lib/charts/wheel";
import { Grid } from "@/lib/charts3d/voxels";
import {
  DEFAULT_SURFACE, Surface, SurfaceSettings, describeSurface, extractSurface,
} from "@/lib/charts3d/isosurface";

export type Isosurface3DProps = {
  grid: Grid;
  /** The value the shell is drawn at. Absent means the middle of the range. */
  level?: number;
  settings?: SurfaceSettings;
  width?: number;
  height?: number;
  controllerRef?: React.RefObject<VisualizationController | null>;
  /** Told when the reader moves the level, so a caller can keep it. */
  onLevelChange?: (level: number) => void;
  caption?: string;
  /**
   * What the three grid directions are, and how far across they reach.
   *
   * A `Grid` is a count of samples and nothing else — it carries the units of
   * its *values* and no geometry at all — so unnamed directions are numbered
   * in samples: `i (voxel)`, 0 to nx−1. A caller that knows the physical
   * extent gives both ends of it and the axis reads in those units instead,
   * which is exact here because a regular grid's coordinate is linear in its
   * index.
   */
  axes?: { i?: AxisNaming; j?: AxisNaming; k?: AxisNaming };
};

/** Where the lamp sits. Over the viewer's shoulder, which is what a reader expects. */
const LAMP = { x: -0.4, y: 0.6, z: 0.7 };

export function Isosurface3D({
  grid, level, settings = DEFAULT_SURFACE, width = 720, height = 520,
  controllerRef, onLevelChange, caption, axes,
}: Isosurface3DProps) {
  // The description is computed from floats, and the server's engine and the
  // browser's disagree in the last bit; said after mounting, so both passes
  // render the same text (T180).
  const mounted = useMounted();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const cameraRef = useRef<Camera>({ ...DEFAULT_CAMERA });
  const dirtyRef = useRef(true);
  const [chosen, setChosen] = useState<number | null>(level ?? null);

  /*
   * The range is measured once per grid, so moving the level does not re-scan
   * the volume — and so the slider's ends stay put while the reader drags it.
   * A control whose own bounds move as you use it is unusable.
   */
  const range = useMemo(() => {
    let min = Infinity, max = -Infinity;
    const total = grid.nx * grid.ny * grid.nz;
    for (let n = 0; n < total && n < grid.values.length; n += 1) {
      const v = grid.values[n];
      if (!Number.isFinite(v)) continue;
      if (v < min) min = v;
      if (v > max) max = v;
    }
    return Number.isFinite(min) ? { min, max } : { min: 0, max: 1 };
  }, [grid]);

  const at = chosen ?? (range.min + range.max) / 2;

  /*
   * Extracted once per grid and level. Marching on every frame would recompute
   * a mesh the camera did not change, and a shell that flickered as the reader
   * turned it would be indistinguishable from a shell that was moving.
   */
  const surface: Surface = useMemo(
    () => extractSurface(grid, at, settings), [grid, at, settings]);

  /*
   * The frame. `extractSurface` places a corner at `i / (nx - 1)` mapped onto
   * −1..1, so the domain of the scene's x is 0 to nx−1 exactly — the same two
   * numbers, not a second reading of them. Marching with a stride does not
   * change it: the extractor normalises against the grid's full extent so a
   * coarser march puts the shell in the same place rather than a scaled one.
   *
   * i runs across the screen, j up it and k into it, which is the order
   * `extractSurface` writes into x, y and z.
   */
  const scene: Axes3D = useMemo(() => ({
    x: namedIndex(axes?.i, "i", grid.nx),
    y: namedIndex(axes?.j, "j", grid.ny),
    z: namedIndex(axes?.k, "k", grid.nz),
  }), [axes, grid.nx, grid.ny, grid.nz]);

  const rotate = useCallback((dx: number, dy: number) => {
    rotateCamera(cameraRef.current, dx, dy);
    dirtyRef.current = true;
  }, []);

  /*
   * Zoom and reset as their own callbacks so the keyboard reaches the
   * same behaviour the controller and the wheel already do. Written here
   * rather than inlined into the key handler because three copies of a
   * clamp is how the three drift apart.
   */
  const zoom = useCallback((factor: number) => {
    zoomCamera(cameraRef.current, factor);
    dirtyRef.current = true;
  }, []);

  const reset = useCallback(() => {
    resetCamera(cameraRef.current);
    dirtyRef.current = true;
  }, []);

  /*
   * Rotation is the depth cue, not a convenience: motion parallax is the
   * strongest signal a flat screen has for which mark is in front. A
   * reader who cannot rotate sees one fixed projection of a tangle, which
   * is the picture §10 exists to forbid.
   */
  const spatialKeys = useSpatialKeys({ rotate, zoom, reset },
    "An isosurface through a volume, rotatable.");

  const moveLevel = useCallback((next: number) => {
    setChosen(next);
    onLevelChange?.(next);
  }, [onLevelChange]);

  /*
   * Which facet is selected, if any.
   *
   * There was no selection at all: `select` returned a target and recorded
   * nothing, `focus` and `deselect` were empty functions, and `selectRegion`
   * and `withinPolygon` returned `[]` whatever they were given. So a hand that
   * could rotate this chart could not pick anything on it, while every other
   * chart in the same gallery answered the same commands — which is exactly
   * the single command architecture failing quietly on one implementation.
   */
  const [selected, setSelected] = useState<number | null>(null);
  /*
   * Mirrored into a ref because the paint loop is started once and closes over
   * what it can see. Reading `selected` from the loop's closure would paint
   * whatever was selected when the effect ran, which is `null` for ever.
   */
  const selectedRef = useRef<number | null>(null);
  useEffect(() => {
    selectedRef.current = selected;
    dirtyRef.current = true;
  }, [selected]);

  /** Which facet a pointer is over. The nearest one, not the first found. */
  const nearest = useCallback((point: ScreenPoint): TargetRef | null => {
    const camera = cameraRef.current;
    let best: TargetRef | null = null;
    let bestDepth = -Infinity;
    surface.triangles.forEach((t, index) => {
      const a = toCanvas(t.a, camera, width, height);
      const b = toCanvas(t.b, camera, width, height);
      const c = toCanvas(t.c, camera, width, height);
      if (!insideTriangle(point, a, b, c)) return;
      const depth = (a.depth + b.depth + c.depth) / 3;
      // In a closed shell every pixel has a front and a back facet under it.
      // Reporting the back one names a place the reader cannot see.
      if (depth <= bestDepth) return;
      bestDepth = depth;
      best = { id: String(index), label: `facet ${index + 1}`, datum: t };
    });
    return best;
  }, [surface, width, height]);

  /**
   * Every facet whose centre falls inside a region.
   *
   * Unlike `nearest`, this does *not* keep only the facet in front. A point
   * names one place, so reporting the far wall of a shell under the cursor
   * would name somewhere the reader cannot see. A region is an area of
   * interest rather than a click, and dropping the far side of a shell from it
   * would under-report what was enclosed — the two rules differ because the
   * gestures mean different things.
   */
  const within = useCallback((inside: (p: ScreenPoint) => boolean): TargetRef[] => {
    const camera = cameraRef.current;
    const found: TargetRef[] = [];
    surface.triangles.forEach((t, index) => {
      const a = toCanvas(t.a, camera, width, height);
      const b = toCanvas(t.b, camera, width, height);
      const c = toCanvas(t.c, camera, width, height);
      const centre = { x: (a.x + b.x + c.x) / 3, y: (a.y + b.y + c.y) / 3 };
      if (inside(centre)) {
        found.push({ id: String(index), label: `facet ${index + 1}`, datum: t });
      }
    });
    return found;
  }, [surface, width, height]);

  useImperativeHandle(controllerRef, (): VisualizationController => ({
    rotate,
    zoom: (factor) => { zoomCamera(cameraRef.current, factor); dirtyRef.current = true; },
    pan: () => {},
    hover: (point) => nearest(point),
    select: (point) => {
      const target = nearest(point);
      setSelected(target ? Number(target.id) : null);
      return target;
    },
    selectRegion: (point, radius) => within(
      (p) => Math.hypot(p.x - point.x, p.y - point.y) <= radius),
    withinPolygon: (polygon) => within((p) => insidePolygon(polygon, p)),
    focus: (objectId) => {
      const index = Number(objectId);
      if (!Number.isInteger(index) || index < 0
          || index >= surface.triangles.length) return;
      setSelected(index);
    },
    deselect: () => setSelected(null),
    resetView: () => {
      resetCamera(cameraRef.current);
      // The level is part of the view: a reader who has narrowed to one shell
      // and asks to reset expects the whole field back, not the camera moved
      // inside a level they had already chosen.
      setChosen(level ?? null);
      dirtyRef.current = true;
    },
    viewport: () => ({ width, height }),
    bounds: () => {
      const box = canvasRef.current?.getBoundingClientRect();
      if (!box || box.width === 0 || box.height === 0) return null;
      return { x: box.left, y: box.top, width: box.width, height: box.height };
    },
    /*
     * The level travels with the view, like the volume's window. A mark placed
     * on one shell means nothing over a different one, and the same grid at
     * two levels is two different pictures.
     */
    viewState: () => ({ yaw: cameraRef.current.yaw,
                        pitch: cameraRef.current.pitch,
                        zoom: cameraRef.current.zoom, level: at }),
    restoreViewState: (state) => {
      const keys = ["yaw", "pitch", "zoom", "level"] as const;
      if (keys.some((k) => typeof state[k] !== "number")) return;
      cameraRef.current.yaw = state.yaw;
      cameraRef.current.pitch = state.pitch;
      cameraRef.current.zoom = state.zoom;
      moveLevel(state.level);
      dirtyRef.current = true;
    },
    /*
     * `within` and the surface belong here. Without them the handle keeps the
     * closure it was built with, so after the level moves — which rebuilds the
     * surface entirely — a lasso would test against the shell that is no
     * longer on screen, and `focus` would bound-check against its facet count.
     */
  }), [rotate, nearest, within, surface, width, height, at, level, moveLevel]);

  useEffect(() => { dirtyRef.current = true; }, [surface]);

  useEffect(() => {
    if (typeof requestAnimationFrame === "undefined") return;
    /*
     * Anything that restarts this loop needs a frame drawn.
     *
     * The loop paints only when something has marked the scene dirty, which
     * is right for a camera that has not moved and wrong for every other
     * dependency this effect lists — a new selection, a new layout, a new
     * size. Those change what belongs on the canvas while leaving the flag
     * false, so the figure kept whatever it had until the reader happened to
     * drag it. Found in the volume, where a theme change repainted nothing;
     * the same shape was in six charts.
     */
    dirtyRef.current = true;
    let running = true;
    let handle = 0;
    const tick = () => {
      if (!running) return;
      if (dirtyRef.current) {
        dirtyRef.current = false;
        paintSurface(canvasRef.current, surface, cameraRef.current,
                     { width, height }, selectedRef.current, scene);
      }
      handle = requestAnimationFrame(tick);
    };
    handle = requestAnimationFrame(tick);
    return () => { running = false; cancelAnimationFrame(handle); };
  }, [surface, width, height, scene]);

  const dragging = useRef<{ x: number; y: number } | null>(null);
  /** Where a press began, so a click can be told from a rotation. */
  const pressedAt = useRef<{ x: number; y: number } | null>(null);
  const step = (range.max - range.min) / 200 || 1;

  return (
    <figure className="chart">
      <canvas
        {...spatialKeys}
        ref={canvasRef}
        width={width}
        height={height}
        data-testid="isosurface-3d"
        style={{ width: "100%", maxWidth: width, touchAction: "none" }}
        onPointerDown={(event) => {
          pressedAt.current = { x: event.clientX, y: event.clientY };
          dragging.current = { x: event.clientX, y: event.clientY };
          (event.target as Element).setPointerCapture?.(event.pointerId);
        }}
        onPointerMove={(event) => {
          const from = dragging.current;
          if (!from) return;
          rotate(event.clientX - from.x, event.clientY - from.y);
          dragging.current = { x: event.clientX, y: event.clientY };
        }}
        onPointerUp={(event) => {
          /*
           * A press that did not travel is a click, and a click selects.
           *
           * This chart has painted a selected mark and exposed `select` on
           * its controller since it was written, and no pointer ever reached
           * either — selection was available to the gesture layer and to
           * nothing a mouse could do. The canvas rotates on drag, so the
           * distance travelled is what separates a click from a camera move.
           */
          const start = pressedAt.current;
          pressedAt.current = null;
          dragging.current = null;
          if (!isClick(start, { x: event.clientX, y: event.clientY })) return;
          const picked = nearest(
            canvasPoint(event, event.currentTarget, width, height));
          setSelected(picked ? Number(picked.id) : null);
          dirtyRef.current = true;
        }}
        onWheel={(event) => {
          if (!isZoomWheel(event)) return;
          event.preventDefault();
          zoomCamera(cameraRef.current, wheelZoomFactor(event.deltaY));
          dirtyRef.current = true;
        }}
      />
      <div className="chart-controls">
        {/*
          * The level is a control because an isosurface is a *choice* of
          * threshold, not a property of the field. One shell out of a family
          * shown without the means to move it implies the field has a
          * boundary, which is the one thing it does not have.
          */}
        <label>
          Level
          <input
            type="range"
            aria-label="Isosurface level"
            min={range.min}
            max={range.max}
            step={step}
            value={at}
            onChange={(event) => moveLevel(Number(event.target.value))}
          />
        </label>
      </div>
      {/* §75: a spatial chart could not be saved at all. */}
      <ChartExport canvasRef={canvasRef} name="Isosurface"
                   rotate={(degrees) => rotate(degrees, 0)}
                   redraw={() => { dirtyRef.current = true; }} />
      <figcaption className="chart-caption">
        {caption ? `${caption} ` : ""}
        {mounted && describeSurface(surface)}
        {" "}{AXES_SCALED_SEPARATELY}
      </figcaption>
    </figure>
  );
}

/** Whether a screen point falls inside a projected triangle. */
function insideTriangle(p: ScreenPoint, a: ScreenPoint, b: ScreenPoint,
                        c: ScreenPoint): boolean {
  const side = (u: ScreenPoint, v: ScreenPoint) =>
    (v.x - u.x) * (p.y - u.y) - (v.y - u.y) * (p.x - u.x);
  const ab = side(a, b), bc = side(b, c), ca = side(c, a);
  // Accepts either winding: the shell carries both, and a hit test that only
  // matched one would silently ignore every back face.
  const negative = ab < 0 || bc < 0 || ca < 0;
  const positive = ab > 0 || bc > 0 || ca > 0;
  return !(negative && positive);
}

/**
 * The tone one facet is drawn in.
 *
 * Lambert against a fixed lamp, with a floor. The floor matters: a facet turned
 * fully away would otherwise be pure black and indistinguishable from a hole in
 * the shell, which is exactly the thing this chart must never fake.
 */
export function facetTone(n: { x: number; y: number; z: number }): number {
  /*
   * Both vectors are normalised here rather than assumed. The extractor does
   * emit unit normals, but a tone is a *fraction* and one computed from an
   * unnormalised input silently exceeds 1 — which downstream becomes a colour
   * channel past 255, which the browser clamps, which looks like a flat white
   * facet. Cheaper to divide than to debug.
   */
  const ln = Math.hypot(n.x, n.y, n.z);
  const ll = Math.hypot(LAMP.x, LAMP.y, LAMP.z);
  if (!(ln > 0) || !(ll > 0)) return 0.28;
  const lit = (n.x * LAMP.x + n.y * LAMP.y + n.z * LAMP.z) / (ln * ll);
  return 0.28 + 0.72 * Math.abs(lit);
}

/** The fill for a facet, from its tone. */
export function facetColour(n: { x: number; y: number; z: number }): string {
  const t = facetTone(n);
  return `rgb(${Math.round(60 + 150 * t)},${Math.round(90 + 140 * t)},`
       + `${Math.round(140 + 110 * t)})`;
}

/**
 * One frame of the shell.
 *
 * Exported for the same reason the other painters are: a draw loop reachable
 * only through an animation frame is one no test ever runs, and depth ordering
 * is where this either works or silently does not.
 */
export function paintSurface(
  canvas: HTMLCanvasElement | null,
  surface: Surface,
  camera: Camera,
  size: { width: number; height: number },
  /** Index of the facet a person picked, drawn so they can see which. */
  selected: number | null = null,
  /**
   * What the three directions measure, or nothing.
   *
   * Optional so the paint tests that predate the frame still describe what
   * they meant to: they read facets off the call list in order, and a wall
   * drawn before them would be a true statement about a different chart.
   */
  axes: Axes3D | null = null,
): void {
  if (!canvas) return;
  const context = canvas.getContext("2d");
  if (!context) return;

  const { width, height } = size;
  context.clearRect(0, 0, width, height);

  // Recomputed here, inside the frame, because which walls face away changes
  // continuously as the scene turns.
  const frame = framing(context, canvas, axes, camera, size);
  frame.behind();

  /*
   * Back to front by centroid depth. `depth` is larger when nearer, so this
   * ascends. Without it a closed shell draws its far side over its near side
   * and reads as a wireframe tangle rather than as a solid.
   */
  const facets = surface.triangles
    .map((t, index) => {
      const a = toCanvas(t.a, camera, width, height);
      const b = toCanvas(t.b, camera, width, height);
      const c = toCanvas(t.c, camera, width, height);
      return { t, index, a, b, c, depth: (a.depth + b.depth + c.depth) / 3 };
    })
    .sort((p, q) => p.depth - q.depth);

  for (const { t, index, a, b, c } of facets) {
    context.save();
    context.fillStyle = facetColour(t.n);
    /*
     * The facet is stroked in its own fill colour as well as filled. Canvas
     * antialiases polygon edges, so abutting triangles leave a faint seam of
     * background between them — a mesh drawn only with fills is visibly
     * cracked along every shared edge, which on this chart reads as the holes
     * the caption says are not there.
     */
    context.strokeStyle = context.fillStyle;
    context.lineWidth = 0.6;
    context.beginPath();
    context.moveTo(a.x, a.y);
    context.lineTo(b.x, b.y);
    context.lineTo(c.x, c.y);
    context.closePath();
    context.fill();
    context.stroke();
    /*
     * The picked facet outlined on top of its own fill.
     *
     * One facet of a fine mesh is a few pixels across, so a different fill
     * colour is not findable — the outline is what a person can actually see.
     * Drawn after the fill and in the painter's own back-to-front order, so a
     * selected facet on the far side stays behind the near ones rather than
     * appearing to float in front of the shell.
     */
    if (index === selected) {
      // Orange rather than the theme's selection blue: the shell itself is
      // drawn in blues, and the outline has to stand off it. 4.3:1 on both
      // canvases. (A `var(--accent, …)` line above this did nothing — a canvas
      // cannot resolve a CSS variable — and has gone.)
      context.strokeStyle = "#d84315";
      context.lineWidth = 2;
      context.stroke();
    }
    context.restore();
  }

  // Over the shell: a closed surface is opaque, and a tick behind it is gone.
  frame.front();
}
