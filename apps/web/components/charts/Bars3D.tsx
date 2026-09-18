"use client";

/**
 * Bars drawn in a room (§9 bars, §10).
 *
 * The renderer for the four catalogue entries that share the `bars` primitive —
 * a 3D bar, a 3D column, a waterfall and a 3D histogram — every one of them
 * classified `framed` rather than inherently spatial.
 *
 * **This chart is built to argue against itself.** §10 is explicit that turning
 * an ordinary chart into 3D to look futuristic costs occlusion, perspective
 * distortion and ambiguity, and a bar chart is the clearest case: the third
 * axis is the room, not the data. So the two costs are measured every frame and
 * put in the caption — how many bars are hidden behind others, and how much
 * taller the near row reads for the same value. A reader told both can decide
 * to turn the chart, or to read the flat version instead.
 *
 * **The floor is numbered only where a number would be true.** The height is a
 * measured value and always carries a scale. The other two directions are
 * placed by *rank* — the third distinct row goes in the third slot, whatever
 * its value — so numbers along them are honest for bins and for categories
 * numbered from zero, and a lie for rows of 1, 2 and 10, where a tick reading
 * 2 would sit where no bar stands. Those directions keep their names and lose
 * their numbers instead.
 *
 * **Each bar is four faces, not one quad.** A single filled rectangle would
 * read as a flat sticker; the two visible sides plus the top are what make it a
 * solid, and the top face is what lets the eye find the height. Faces are
 * shaded by orientation rather than lit, so the tone difference between them is
 * constant and a reader is not comparing heights across a lighting gradient.
 */

import { selectionColour } from "@/lib/charts/theme";
import {
  useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState,
} from "react";
import { useMounted } from "@/lib/charts/useMounted";
import { ScreenPoint, TargetRef, VisualizationController } from "@/lib/spatial/commands";
import {
  Axes3D, Camera, DEFAULT_CAMERA, insidePolygon, resetCamera, rotateCamera, toCanvas, zoomCamera,
} from "@/lib/charts/scene3d";
import { AxisNaming, framing, named } from "@/lib/charts/frame";
import { canvasPoint, isClick } from "@/lib/charts/pointer";
import { useSpatialKeys } from "@/lib/charts/spatialKeys";
import { ChartExport } from "@/components/charts/ChartExport";
import { isZoomWheel, wheelZoomFactor } from "@/lib/charts/wheel";
import {
  Bar, Bars, BarSettings, DEFAULT_BARS, PlacedBar, describeBars, evenlySpaced,
  hiddenCount, perspectiveStretch, prepareBars,
} from "@/lib/charts3d/bars";

export type Bars3DProps = {
  bars: Bar[];
  settings?: BarSettings;
  width?: number;
  height?: number;
  controllerRef?: React.RefObject<VisualizationController | null>;
  onSelect?: (target: TargetRef | null) => void;
  caption?: string;
  /**
   * What the floor and the height measure.
   *
   * `value` is the one with a scale under it in every case. `row` and `column`
   * are numbered only when their distinct values step evenly, because that is
   * the only arrangement in which rank and value describe the same positions.
   */
  axes?: { row?: AxisNaming; column?: AxisNaming; value?: AxisNaming };
};

/** How near a pointer must be, in pixels, to count as on a bar. */
const PICK_RADIUS = 18;

/** Tone per face, so the sides read as sides rather than as different values. */
const FACE = { top: 1.0, left: 0.78, right: 0.6 };

export function Bars3D({
  bars, settings = DEFAULT_BARS, width = 720, height = 520, controllerRef,
  onSelect, caption, axes,
}: Bars3DProps) {
  // The description is computed from floats, and the server's engine and the
  // browser's disagree in the last bit; said after mounting, so both passes
  // render the same text (T180).
  const mounted = useMounted();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const cameraRef = useRef<Camera>({ ...DEFAULT_CAMERA });
  const dirtyRef = useRef(true);
  const [selected, setSelected] = useState<number | null>(null);
  /*
   * The two costs of the third dimension, measured from the current view.
   *
   * Recomputed when the camera moves, because both of them change as it does —
   * a caption that measured them once would state the occlusion of a view the
   * reader has since turned away from.
   */
  const [costs, setCosts] = useState({ hidden: 0, stretch: 1 });

  const prepared: Bars = useMemo(
    () => prepareBars(bars, settings), [bars, settings]);

  /*
   * The frame. Scene axes, not data names: a bar's height is drawn up the
   * screen, so the *value* is scene `y`, and the two floor directions are
   * scene `x` and `z`. Writing the value's numbers along a floor edge would
   * look like a working chart and be wrong in the one way nothing reveals.
   *
   * The height's domain is `range`, which is what `prepareBars` scaled by —
   * and `prepareBars` puts zero inside it always, so the scale a reader sees
   * is the scale the bars stand on rather than one starting at the smallest
   * value.
   *
   * A direction whose ranks and values disagree is given a domain of NaN,
   * which `niceTicks` answers with no ticks at all: the name of the direction
   * survives, the numbers do not. That is the honest picture — the reader can
   * see there are five slots and is not told which values they hold.
   */
  const scene: Axes3D = useMemo(() => {
    const floor = (naming: AxisNaming | undefined, fallback: string,
                   positions: number[]) =>
      named(naming, fallback, evenlySpaced(positions) && positions.length > 0
        ? { min: positions[0], max: positions[positions.length - 1] }
        : { min: NaN, max: NaN });
    return {
      x: floor(axes?.row, "Row", prepared.rows),
      y: named(axes?.value, "Value", prepared.range),
      z: floor(axes?.column, "Column", prepared.columns),
    };
  }, [axes, prepared]);

  const at = useCallback((bar: PlacedBar) => {
    // The top of the bar: what a reader points at and what they compare.
    return toCanvas({ x: bar.x, y: bar.top, z: bar.z },
                    cameraRef.current, width, height);
  }, [width, height]);

  const measure = useCallback(() => {
    const camera = cameraRef.current;
    setCosts({
      hidden: hiddenCount(prepared.bars,
        (bar) => toCanvas({ x: bar.x, y: bar.top, z: bar.z },
                          camera, width, height), PICK_RADIUS),
      stretch: perspectiveStretch(prepared.bars,
        (bar) => toCanvas({ x: bar.x, y: bar.top, z: bar.z },
                          camera, width, height).scale),
    });
  }, [prepared, width, height]);

  useEffect(() => { measure(); }, [measure]);

  const nearest = useCallback((point: ScreenPoint): TargetRef | null => {
    let best: TargetRef | null = null;
    let bestDepth = -Infinity;
    prepared.bars.forEach((bar, index) => {
      const q = at(bar);
      if (Math.hypot(q.x - point.x, q.y - point.y) > PICK_RADIUS) return;
      // The nearest under the pointer: in a grid of bars the hidden ones are
      // exactly what the reader cannot see and did not point at.
      if (q.depth <= bestDepth) return;
      bestDepth = q.depth;
      best = { id: String(index),
               label: bar.label ?? `${Number(bar.value.toPrecision(4))}`,
               datum: bar };
    });
    return best;
  }, [prepared, at]);

  const rotate = useCallback((dx: number, dy: number) => {
    rotateCamera(cameraRef.current, dx, dy);
    dirtyRef.current = true;
    measure();
  }, [measure]);

  /*
   * Zoom and reset as their own callbacks so the keyboard reaches the
   * same behaviour the controller and the wheel already do. Written here
   * rather than inlined into the key handler because three copies of a
   * clamp is how the three drift apart.
   */
  const zoom = useCallback((factor: number) => {
    zoomCamera(cameraRef.current, factor);
    dirtyRef.current = true;
    measure();
  }, [measure]);

  const reset = useCallback(() => {
    resetCamera(cameraRef.current);
    dirtyRef.current = true;
    measure();
  }, [measure]);

  /*
   * Rotation is the depth cue, not a convenience: motion parallax is the
   * strongest signal a flat screen has for which mark is in front. A
   * reader who cannot rotate sees one fixed projection of a tangle, which
   * is the picture §10 exists to forbid.
   */
  const spatialKeys = useSpatialKeys({ rotate, zoom, reset },
    "Bars in three dimensions, rotatable.");

  useImperativeHandle(controllerRef, (): VisualizationController => ({
    rotate,
    zoom: (factor) => {
      zoomCamera(cameraRef.current, factor);
      dirtyRef.current = true;
      measure();
    },
    pan: () => {},
    hover: (point) => nearest(point),
    select: (point) => {
      const target = nearest(point);
      setSelected(target ? Number(target.id) : null);
      onSelect?.(target);
      return target;
    },
    selectRegion: (point, radius) => within(
      prepared, at, (p) => Math.hypot(p.x - point.x, p.y - point.y) <= radius),
    withinPolygon: (polygon) => within(prepared, at,
                                       (p) => insidePolygon(polygon, p)),
    focus: (objectId) => {
      const index = Number(objectId);
      if (!Number.isInteger(index) || index < 0
          || index >= prepared.bars.length) return;
      setSelected(index);
    },
    deselect: () => setSelected(null),
    resetView: () => {
      resetCamera(cameraRef.current);
      dirtyRef.current = true;
      measure();
    },
    viewport: () => ({ width, height }),
    bounds: () => {
      const box = canvasRef.current?.getBoundingClientRect();
      if (!box || box.width === 0 || box.height === 0) return null;
      return { x: box.left, y: box.top, width: box.width, height: box.height };
    },
    viewState: () => ({ yaw: cameraRef.current.yaw,
                        pitch: cameraRef.current.pitch,
                        zoom: cameraRef.current.zoom }),
    restoreViewState: (state) => {
      const keys = ["yaw", "pitch", "zoom"] as const;
      if (keys.some((k) => typeof state[k] !== "number")) return;
      cameraRef.current.yaw = state.yaw;
      cameraRef.current.pitch = state.pitch;
      cameraRef.current.zoom = state.zoom;
      dirtyRef.current = true;
      measure();
    },
  }), [rotate, nearest, prepared, at, width, height, onSelect, measure]);

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
        paintBars(canvasRef.current, prepared, cameraRef.current,
                  { width, height }, selected, scene);
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
        data-testid="bars-3d"
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
          onSelect?.(picked);
          dirtyRef.current = true;
        }}
        onWheel={(event) => {
          if (!isZoomWheel(event)) return;
          event.preventDefault();
          zoomCamera(cameraRef.current, wheelZoomFactor(event.deltaY));
          dirtyRef.current = true;
          measure();
        }}
      />
      {/* §75: a spatial chart could not be saved at all. */}
      <ChartExport canvasRef={canvasRef} name="Bars"
                   rotate={(degrees) => rotate(degrees, 0)}
                   redraw={() => { dirtyRef.current = true; }} />
      <figcaption className="chart-caption">
        {caption ? `${caption} ` : ""}
        {mounted && describeBars(prepared, costs.hidden, costs.stretch)}
        {selected !== null && prepared.bars[selected] && (
          <> Selected: {Number(prepared.bars[selected].value.toPrecision(4))}.</>
        )}
      </figcaption>
    </figure>
  );
}

function within(prepared: Bars, at: (b: PlacedBar) => { x: number; y: number },
                inside: (p: { x: number; y: number }) => boolean): TargetRef[] {
  const found: TargetRef[] = [];
  prepared.bars.forEach((bar, index) => {
    if (!inside(at(bar))) return;
    found.push({ id: String(index),
                 label: bar.label ?? `${Number(bar.value.toPrecision(4))}`,
                 datum: bar });
  });
  return found;
}


/** The fill for one face of a bar at a given level. */
export function barColour(level: number, face: keyof typeof FACE): string {
  const t = Math.max(0, Math.min(1, level));
  const shade = FACE[face];
  return `rgb(${Math.round((70 + 150 * t) * shade)},`
       + `${Math.round((110 + 90 * t) * shade)},`
       + `${Math.round((190 - 40 * t) * shade)})`;
}

/**
 * One frame of the bars.
 *
 * Exported for the same reason the other painters are: a draw loop reachable
 * only through an animation frame is one no test ever runs, and here that is
 * where the occlusion the caption reports either happens or does not.
 */
export function paintBars(
  canvas: HTMLCanvasElement | null,
  prepared: Bars,
  camera: Camera,
  size: { width: number; height: number },
  selected: number | null,
  /**
   * What the three directions measure, or nothing.
   *
   * Optional so the paint tests that predate the frame still describe what
   * they meant to: they read a bar's faces off the call list in order, and a
   * wall drawn before them would be a true statement about a different chart.
   */
  axes: Axes3D | null = null,
): void {
  if (!canvas) return;
  const context = canvas.getContext("2d");
  // The theme's selection colour: the fixed blue was 2.3:1 on the dark canvas.
  const selection = selectionColour(canvas);
  if (!context) return;

  const { width, height } = size;
  context.clearRect(0, 0, width, height);

  // Recomputed here, inside the frame, because which walls face away changes
  // continuously as the scene turns.
  const frame = framing(context, canvas, axes, camera, size);
  frame.behind();

  const project = (x: number, y: number, z: number) =>
    toCanvas({ x, y, z }, camera, width, height);

  const order = drawOrder(prepared, camera, size);

  for (const { bar, index } of order) {
    const h = bar.half;
    // The four base corners and the four top corners.
    const corners = [
      [-h, -h], [h, -h], [h, h], [-h, h],
    ].map(([dx, dz]) => ({
      base: project(bar.x + dx, bar.base, bar.z + dz),
      top: project(bar.x + dx, bar.top, bar.z + dz),
    }));

    /*
     * All four sides, drawn back to front, rather than the two that face the
     * viewer.
     *
     * Culling by projected winding was the first attempt and it was wrong on
     * screen: it selected two *opposite* faces rather than two adjacent ones,
     * so each bar came out as a floating top with a spike under it. The winding
     * of a side quad depends on the corner order it was built with as well as
     * on the camera, and getting that consistent for all four is fiddly in a
     * way that produces a plausible-looking wrong picture.
     *
     * Four quads sorted by their own depth is provably right for a convex box:
     * the near faces are drawn last and cover the far ones exactly. The cost is
     * two extra fills per bar, which is nothing, and the result cannot be
     * subtly wrong.
     */
    const faces: Array<[number, number, keyof typeof FACE]> = [
      [0, 1, "left"], [1, 2, "right"], [2, 3, "left"], [3, 0, "right"],
    ];

    context.save();
    const sides = faces
      .map(([i, j, tone]) => ({
        quad: [corners[i].base, corners[j].base, corners[j].top, corners[i].top],
        tone,
        depth: (corners[i].base.depth + corners[j].base.depth) / 2,
      }))
      .sort((a, b) => a.depth - b.depth);

    for (const side of sides) {
      fillPath(context, side.quad, barColour(bar.level, side.tone));
    }

    // The top last, so it sits over the sides it shares an edge with.
    fillPath(context, corners.map((c) => c.top), barColour(bar.level, "top"));

    if (index === selected) {
      context.strokeStyle = selection;
      context.lineWidth = 2;
      context.beginPath();
      corners.forEach((c, i) => {
        if (i === 0) context.moveTo(c.top.x, c.top.y);
        else context.lineTo(c.top.x, c.top.y);
      });
      context.closePath();
      context.stroke();
    }
    context.restore();
  }

  // Over the bars: a solid is opaque, and a tick behind one is not a tick.
  frame.front();
}

/**
 * The bars, back to front.
 *
 * Ordered by the *base* rather than the top: the height is the data, and where
 * a bar stands on the floor is what decides which is in front. Sorting by the
 * top would put a short near bar behind a tall far one and the grid would
 * interleave.
 *
 * Exported because it is the ordering decision, and a decision buried in a draw
 * loop is one no test reaches.
 */
export function drawOrder(prepared: Bars, camera: Camera,
                          size: { width: number; height: number }) {
  return prepared.bars
    .map((bar, index) => ({
      bar, index,
      at: toCanvas({ x: bar.x, y: bar.base, z: bar.z },
                   camera, size.width, size.height),
    }))
    .sort((a, b) => a.at.depth - b.at.depth);
}

function fillPath(context: CanvasRenderingContext2D,
                  points: Array<{ x: number; y: number }>, fill: string): void {
  context.fillStyle = fill;
  // Stroked in its own colour as well: canvas antialiasing leaves a hairline of
  // background between abutting fills, and a bar cracked along every edge reads
  // as a rendering fault.
  context.strokeStyle = fill;
  context.lineWidth = 0.6;
  context.beginPath();
  points.forEach((p, i) => {
    if (i === 0) context.moveTo(p.x, p.y);
    else context.lineTo(p.x, p.y);
  });
  context.closePath();
  context.fill();
  context.stroke();
}
