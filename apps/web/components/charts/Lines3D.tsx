"use client";

/**
 * Paths drawn in space (§9 lines).
 *
 * The renderer for the seventeen catalogue entries that share the `lines`
 * primitive — planetary, satellite and asteroid orbits, flight paths,
 * spacecraft trajectories, robot arm motion, neural pathways, blood vessel
 * networks, a training trajectory through parameter space, streamlines through
 * a field, and the research timeline tunnel.
 *
 * **Every segment is sorted separately, not every path.** A trajectory is not
 * at one depth: an orbit passes in front of the body it circles and then
 * behind it, and a path sorted as a unit is drawn entirely in front or
 * entirely behind — so the orbit either floats over the thing it orbits or
 * hides beneath it, and in both cases the reader is looking at a picture of
 * the sort rather than of the motion. Splitting into segments costs an
 * allocation per frame and buys the one cue that makes a closed path legible.
 *
 * **Direction is drawn, because a path has an order and a line does not.** The
 * stroke ramps from faint at the start to solid at the end. An arrowhead per
 * path would put one mark at one place; a ramp is readable everywhere along
 * it, survives the path leaving the frame, and adds no clutter to a bundle of
 * two hundred trajectories.
 *
 * **A gap is never joined.** `preparePaths` has already split a path wherever
 * its data was missing, and this file draws the runs separately. That is the
 * whole reason the runs exist, and the temptation it removes — flattening them
 * into one polyline — would draw a straight chord across ground nothing was
 * measured on.
 */

import { selectionColour } from "@/lib/charts/theme";
import {
  useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState,
} from "react";
import { useMounted } from "@/lib/charts/useMounted";
import { ScreenPoint, TargetRef, VisualizationController } from "@/lib/spatial/commands";
import { canvasPoint, isClick } from "@/lib/charts/pointer";
import { AXES_SCALED_SEPARATELY, Axes3D, Camera, DEFAULT_CAMERA, insidePolygon, resetCamera, rotateCamera, toCanvas, zoomCamera } from "@/lib/charts/scene3d";
import { AxisNaming, framing, named } from "@/lib/charts/frame";
import { useSpatialKeys } from "@/lib/charts/spatialKeys";
import { ChartExport } from "@/components/charts/ChartExport";
import { isZoomWheel, wheelZoomFactor } from "@/lib/charts/wheel";
import {
  DEFAULT_PATHS, Path, PathSettings, Paths, Polyline, describePaths,
  preparePaths,
} from "@/lib/charts3d/paths";
import { categorical, seriesStroke } from "@/lib/tokens";

export type Lines3DProps = {
  paths: Path[];
  settings?: PathSettings;
  width?: number;
  height?: number;
  controllerRef?: React.RefObject<VisualizationController | null>;
  onSelect?: (target: TargetRef | null) => void;
  caption?: string;
  /**
   * What the three coordinates of a path point are.
   *
   * Names only: the numbers are the extent every path was scaled against, and
   * one extent covers all of them, because two orbits scaled separately would
   * each fill the cube and look the same size. Unnamed, a direction carries
   * the name of the field it was read from.
   */
  axes?: { x?: AxisNaming; y?: AxisNaming; z?: AxisNaming };
};

/** How near a pointer must be, in pixels, to count as on a path. */
const PICK_RADIUS = 10;

export function Lines3D({
  paths, settings = DEFAULT_PATHS, width = 720, height = 520, controllerRef,
  onSelect, caption, axes,
}: Lines3DProps) {
  // The description is computed from floats, and the server's engine and the
  // browser's disagree in the last bit; said after mounting, so both passes
  // render the same text (T180).
  const mounted = useMounted();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const cameraRef = useRef<Camera>({ ...DEFAULT_CAMERA });
  const dirtyRef = useRef(true);
  const hoveredRef = useRef<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  /*
   * Prepared once per set of paths. Simplification and normalisation are
   * decisions about the data rather than the camera, and re-simplifying on
   * every rotation would let points appear and vanish as the reader turned the
   * scene — indistinguishable from the trajectory changing.
   */
  const prepared: Paths = useMemo(
    () => preparePaths(paths, settings), [paths, settings]);

  /*
   * The frame, from the extent the paths were placed against.
   *
   * `prepared.domain` rather than a sweep of `paths`: the preparation ignores
   * points that are not finite, and an axis labelled from a domain the
   * drawing did not use puts every tick a little off its own value with
   * nothing in the picture to show it.
   *
   * A trajectory's `t` is not one of these. Time is drawn as the ramp along
   * the stroke — faint at the start, solid at the end — and there is no
   * spatial direction for it to be an axis of.
   */
  const scene: Axes3D = useMemo(() => ({
    x: named(axes?.x, "x", prepared.domain.x),
    y: named(axes?.y, "y", prepared.domain.y),
    z: named(axes?.z, "z", prepared.domain.z),
  }), [axes, prepared]);

  /** Which path a pointer is on, measured to the line rather than to a point. */
  const nearest = useCallback((point: ScreenPoint): TargetRef | null => {
    const camera = cameraRef.current;
    let best: TargetRef | null = null;
    let bestGap = PICK_RADIUS;

    for (const line of prepared.lines) {
      for (const run of line.runs) {
        const screen = run.map((p) => toCanvas(p, camera, width, height));
        const ends = line.closed && screen.length > 2
          ? [...screen, screen[0]] : screen;
        for (let i = 1; i < ends.length; i += 1) {
          const gap = distanceToSegment(point, ends[i - 1], ends[i]);
          // `<=` so a later path wins a tie: it is the one drawn on top, and
          // selecting the one behind picks something the reader cannot see.
          if (gap > bestGap) continue;
          bestGap = gap;
          best = { id: line.id, label: line.label ?? line.id, datum: line };
        }
      }
    }
    return best;
  }, [prepared, width, height]);

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
    "Paths through a volume, drawn in three dimensions and rotatable.");

  useImperativeHandle(controllerRef, (): VisualizationController => ({
    rotate,
    zoom: (factor) => {
      zoomCamera(cameraRef.current, factor);
      dirtyRef.current = true;
    },
    // Not offered rather than stubbed: this centres a unit cube and there is
    // nothing off-frame to pan toward.
    pan: () => {},
    hover: (point) => {
      const target = nearest(point);
      if ((target?.id ?? null) !== hoveredRef.current) {
        hoveredRef.current = target?.id ?? null;
        dirtyRef.current = true;
      }
      return target;
    },
    select: (point) => {
      const target = nearest(point);
      setSelected(target?.id ?? null);
      onSelect?.(target);
      return target;
    },
    selectRegion: (point, radius) => within(
      prepared, cameraRef.current, width, height,
      (p) => Math.hypot(p.x - point.x, p.y - point.y) <= radius),
    withinPolygon: (polygon) => within(
      prepared, cameraRef.current, width, height,
      (p) => insidePolygon(polygon, p)),
    focus: (objectId) => {
      // Ignored rather than stored when it names no path here, so the caption
      // cannot claim a selection nothing can be highlighted for.
      if (!prepared.lines.some((l) => l.id === objectId)) return;
      setSelected(objectId);
    },
    deselect: () => setSelected(null),
    resetView: () => { resetCamera(cameraRef.current); dirtyRef.current = true; },
    viewport: () => ({ width, height }),
    bounds: () => {
      const box = canvasRef.current?.getBoundingClientRect();
      // A canvas not yet laid out measures zero, which is not a position.
      if (!box || box.width === 0 || box.height === 0) return null;
      return { x: box.left, y: box.top, width: box.width, height: box.height };
    },
    viewState: () => ({ yaw: cameraRef.current.yaw,
                        pitch: cameraRef.current.pitch,
                        zoom: cameraRef.current.zoom }),
    restoreViewState: (state) => {
      // All three or none. A partial restore puts the scene somewhere the
      // researcher has never been, which is worse than not moving at all.
      if (typeof state.yaw !== "number" || typeof state.pitch !== "number"
          || typeof state.zoom !== "number") return;
      cameraRef.current.yaw = state.yaw;
      cameraRef.current.pitch = state.pitch;
      cameraRef.current.zoom = state.zoom;
      dirtyRef.current = true;
    },
  }), [rotate, nearest, prepared, width, height, onSelect]);

  useEffect(() => { dirtyRef.current = true; }, [prepared, selected]);

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
        paintLines(canvasRef.current, prepared, cameraRef.current,
                   { width, height }, selected, hoveredRef.current, scene);
      }
      handle = requestAnimationFrame(tick);
    };
    handle = requestAnimationFrame(tick);
    return () => { running = false; cancelAnimationFrame(handle); };
  }, [prepared, width, height, selected, scene]);

  const dragging = useRef<{ x: number; y: number } | null>(null);
  /** Where a press began, so a click can be told from a rotation. */
  const pressedAt = useRef<{ x: number; y: number } | null>(null);

  return (
    <figure className="chart">
      <canvas
        {...spatialKeys}
        ref={canvasRef}
        width={width}
        height={height}
        data-testid="lines-3d"
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
          setSelected(picked?.id ?? null);
          onSelect?.(picked);
          dirtyRef.current = true;
        }}
        onWheel={(event) => {
          if (!isZoomWheel(event)) return;
          event.preventDefault();
          zoomCamera(cameraRef.current, wheelZoomFactor(event.deltaY));
          dirtyRef.current = true;
        }}
      />
      {/* §75: a spatial chart could not be saved at all. */}
      <ChartExport canvasRef={canvasRef} name="Paths"
                   rotate={(degrees) => rotate(degrees, 0)}
                   redraw={() => { dirtyRef.current = true; }} />
      <figcaption className="chart-caption">
        {caption ? `${caption} ` : ""}
        {mounted && describePaths(prepared)}
        {selected && (
          <> Selected: {prepared.lines.find((l) => l.id === selected)?.label
                        ?? selected}.</>
        )}
        {" "}{AXES_SCALED_SEPARATELY}
      </figcaption>
    </figure>
  );
}

function within(prepared: Paths, camera: Camera, width: number, height: number,
                inside: (p: ScreenPoint) => boolean): TargetRef[] {
  const found: TargetRef[] = [];
  for (const line of prepared.lines) {
    // A path counts if any of its points is inside: a lasso round part of a
    // trajectory is asking for that trajectory, not for the fragment.
    const any = line.runs.some((run) => run.some(
      (p) => inside(toCanvas(p, camera, width, height))));
    if (any) found.push({ id: line.id, label: line.label ?? line.id, datum: line });
  }
  return found;
}


/** Distance from a point to a segment on screen. Clamped to the endpoints. */
function distanceToSegment(p: ScreenPoint, a: ScreenPoint, b: ScreenPoint): number {
  const vx = b.x - a.x, vy = b.y - a.y;
  const wx = p.x - a.x, wy = p.y - a.y;
  const vv = vx * vx + vy * vy;
  const t = vv > 0 ? Math.max(0, Math.min(1, (wx * vx + wy * vy) / vv)) : 0;
  return Math.hypot(wx - t * vx, wy - t * vy);
}

/** The colour a path is drawn in. Grouped paths share one; others cycle. */
export function pathColour(line: Polyline, index: number): string {
  const key = line.group ?? line.id;
  let h = 2166136261;
  for (let i = 0; i < key.length; i += 1) {
    h ^= key.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return line.group
    ? categorical[Math.abs(h) % categorical.length]
    : categorical[index % categorical.length];
}

/**
 * One frame of the paths.
 *
 * Exported for the same reason `paintNetwork` and `paintVolume` are: a draw
 * loop reachable only through an animation frame is one no test ever runs, and
 * the per-segment depth ordering is the thing here that is either right or
 * silently wrong.
 */
export function paintLines(
  canvas: HTMLCanvasElement | null,
  prepared: Paths,
  camera: Camera,
  size: { width: number; height: number },
  selected: string | null,
  hovered: string | null,
  /**
   * What the three directions measure, or nothing.
   *
   * Optional so the paint tests that predate the frame still describe what
   * they meant to: they read the segments off the call list in order, and a
   * wall drawn before them would be a true statement about a different chart.
   */
  axes: Axes3D | null = null,
): void {
  if (!canvas) return;
  const context = canvas.getContext("2d");
  const selection = selectionColour(canvas);
  if (!context) return;

  const { width, height } = size;
  context.clearRect(0, 0, width, height);

  // Recomputed here, inside the frame, because which walls face away changes
  // continuously as the scene turns.
  const frame = framing(context, canvas, axes, camera, size);
  frame.behind();

  /*
   * Every segment of every path, collected and then sorted together.
   *
   * Sorting whole paths would draw an orbit entirely in front of or entirely
   * behind the body it circles. `depth` is larger when nearer, so this
   * ascends: farthest first.
   */
  type Piece = {
    from: { x: number; y: number };
    to: { x: number; y: number };
    depth: number;
    colour: string;
    /** How far along the path this segment sits, 0..1. Drives the ramp. */
    along: number;
    id: string;
  };
  const pieces: Piece[] = [];

  prepared.lines.forEach((line, index) => {
    const colour = pathColour(line, index);
    for (const run of line.runs) {
      const screen = run.map((p) => ({
        ...toCanvas(p, camera, width, height),
      }));
      // A closed path joins its last point to its first. `preparePaths` has
      // already refused to close anything that was broken by missing data.
      const ends = line.closed && screen.length > 2
        ? [...screen, screen[0]] : screen;
      for (let i = 1; i < ends.length; i += 1) {
        pieces.push({
          from: ends[i - 1],
          to: ends[i],
          depth: (ends[i - 1].depth + ends[i].depth) / 2,
          colour,
          along: ends.length > 1 ? i / (ends.length - 1) : 1,
          id: line.id,
        });
      }
    }
  });

  pieces.sort((a, b) => a.depth - b.depth);

  for (const piece of pieces) {
    const isSelected = piece.id === selected;
    const isHovered = piece.id === hovered;
    context.save();
    /*
     * Fainter at the start, solid at the end. A path has an order and a line
     * does not, so without this a trajectory and its reverse are the same
     * picture.
     *
     * **The floor is 0.55, not 0.** Ramping from near-transparent reads
     * beautifully on paper and vanishes on this interface's near-black
     * background: at 0.19 the first half of every trajectory painted 3305
     * pixels that could not be seen at all. The ramp only has to be *ordered*
     * to convey direction — it does not have to reach zero, and reaching zero
     * costs the beginning of the path, which is where a trajectory starts and
     * therefore where a reader looks first.
     */
    const emphasis = isSelected ? 1 : isHovered ? 0.9 : 0.8;
    context.globalAlpha = (0.55 + 0.45 * piece.along) * emphasis;
    // The theme's selection colour; a pale series as a line in its edge (D416).
    context.strokeStyle = isSelected ? selection : seriesStroke(piece.colour);
    context.lineWidth = isSelected ? 2.6 : isHovered ? 2 : 1.6;
    context.lineCap = "round";
    context.beginPath();
    context.moveTo(piece.from.x, piece.from.y);
    context.lineTo(piece.to.x, piece.to.y);
    context.stroke();
    context.restore();
  }

  // Over the paths: a tick under a trajectory is a tick nobody reads.
  frame.front();
}
