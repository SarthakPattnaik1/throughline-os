"use client";

/**
 * A vector field drawn in space (§9 fields).
 *
 * The renderer for the twenty-six catalogue entries that share the `glyphs`
 * primitive — gradient and Jacobian fields, fluid flow, wind, velocity, force,
 * magnetic and electric fields, and the tensor fields that draw an ellipsoid
 * per sample instead of an arrow.
 *
 * **The hard part is not drawing an arrow, it is deciding which arrows to
 * draw**, and that decision lives in `lib/charts3d/field.ts` where it can be
 * tested without a canvas. This file is the part that has to be a canvas:
 * projection, depth ordering, and the head that tells a reader which end of a
 * line is the front.
 *
 * **An arrowhead is drawn in screen space, not in the scene.** A cone modelled
 * in three dimensions and projected is nearly invisible when it points at the
 * camera — which is exactly the direction a reader most needs to identify, and
 * the one where a projected shaft has almost no length to read either. Drawn
 * flat against the screen the head keeps a constant size, so a vector pointing
 * away is a short line with a clear head rather than a dot.
 *
 * **Magnitude is carried by colour as well as by length.** Length runs out —
 * an arrow may not reach into its neighbour's cell — so the arrows at the top
 * of the range are all the same length, and without colour they would be
 * indistinguishable from one another.
 *
 * **The three directions are measurements, so they are framed and numbered.**
 * A sample sits where it was taken; the axes are the coordinates the caller
 * measured in, and a reader can read a position off the picture. The colour
 * key beside it is not decoration either: the caption already tells a reader
 * to read a clamped arrow "by colour", and until there was a key that was an
 * instruction to consult a scale nothing on the page carried.
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
import { Colourbar } from "@/components/charts/Colourbar";
import { isZoomWheel, wheelZoomFactor } from "@/lib/charts/wheel";
import {
  DEFAULT_FIELD, Field, FieldSettings, Glyph, Sample, describeField,
  prepareField,
} from "@/lib/charts3d/field";

export type Field3DProps = {
  samples: Sample[];
  settings?: FieldSettings;
  width?: number;
  height?: number;
  controllerRef?: React.RefObject<VisualizationController | null>;
  onSelect?: (target: TargetRef | null) => void;
  /** What the field is, and in what units, for a reader who did not build it. */
  caption?: string;
  /**
   * What the three sample coordinates are.
   *
   * Names only: the numbers on the axes are the extent of the samples this
   * chart was given, and nothing a caller says can move them. Unnamed, a
   * direction carries the name of the field it was read from — `x`, `y`, `z` —
   * which tells a reader which way is which without pretending to know what
   * the field measured.
   */
  axes?: { x?: AxisNaming; y?: AxisNaming; z?: AxisNaming };
  /**
   * What the arrows' length and colour measure.
   *
   * A vector's magnitude has whatever unit its components had — m/s, tesla,
   * newtons — and only the caller knows which.
   */
  magnitude?: AxisNaming;
};

/** How near a pointer must be, in pixels, to count as on an arrow. */
const PICK_RADIUS = 12;
/** The arrowhead, in pixels. Constant, because it is drawn against the screen. */
const HEAD = 5;

export function Field3D({
  samples, settings = DEFAULT_FIELD, width = 720, height = 520, controllerRef,
  onSelect, caption, axes, magnitude,
}: Field3DProps) {
  // The description is computed from floats, and the server's engine and the
  // browser's disagree in the last bit; said after mounting, so both passes
  // render the same text (T180).
  const mounted = useMounted();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const cameraRef = useRef<Camera>({ ...DEFAULT_CAMERA });
  const dirtyRef = useRef(true);
  const hoveredRef = useRef<number | null>(null);
  const [selected, setSelected] = useState<number | null>(null);

  // Prepared once per set of samples. Thinning and scaling are decisions about
  // the data, not about the camera, so rotating must not change them — a field
  // that re-decimated on every drag would flicker as different arrows survived.
  const field: Field = useMemo(
    () => prepareField(samples, settings), [samples, settings]);

  /*
   * The frame, from the domain the arrows were actually placed against.
   *
   * `field.domain` rather than a sweep of `samples`: `prepareField` normalises
   * against the *usable* samples, so a field carrying one NaN would be drawn
   * on one domain and labelled from a wider one — and a tick reading 40 would
   * sit where 45 is, with nothing in the picture to show it.
   *
   * Scene axes, not data names, and here they coincide: a sample's x is placed
   * on the axis that runs across the screen and its y on the one that runs up
   * it, which is what `toCanvas` is handed below.
   */
  const scene: Axes3D = useMemo(() => ({
    x: named(axes?.x, "x", field.domain.x),
    y: named(axes?.y, "y", field.domain.y),
    z: named(axes?.z, "z", field.domain.z),
  }), [axes, field]);

  const at = useCallback((glyph: Glyph): ScreenPoint => {
    const q = toCanvas(glyph, cameraRef.current, width, height);
    return { x: q.x, y: q.y };
  }, [width, height]);

  /** Which arrow a pointer is on, by its tail. */
  const nearest = useCallback((point: ScreenPoint): TargetRef | null => {
    let best: TargetRef | null = null;
    let bestGap = PICK_RADIUS;
    field.glyphs.forEach((glyph, index) => {
      const p = at(glyph);
      const gap = Math.hypot(p.x - point.x, p.y - point.y);
      // `<=` so a later arrow wins a tie: it is the one drawn on top, and
      // taking the one behind selects something the reader cannot see.
      if (gap > bestGap) return;
      bestGap = gap;
      best = {
        id: String(index),
        label: `magnitude ${glyph.magnitude.toPrecision(3)}`,
        datum: glyph,
      };
    });
    return best;
  }, [field, at]);

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
    "A vector field, drawn in three dimensions and rotatable.");

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
      const id = target ? Number(target.id) : null;
      if (id !== hoveredRef.current) { hoveredRef.current = id; dirtyRef.current = true; }
      return target;
    },
    select: (point) => {
      const target = nearest(point);
      setSelected(target ? Number(target.id) : null);
      onSelect?.(target);
      return target;
    },
    selectRegion: (point, radius) => within(
      field, at, (p) => Math.hypot(p.x - point.x, p.y - point.y) <= radius),
    withinPolygon: (polygon) => within(
      field, at, (p) => insidePolygon(polygon, p)),
    focus: (objectId) => {
      const index = Number(objectId);
      /*
       * Defensive rather than load-bearing, and worth saying so.
       *
       * `Number("citation-4")` is NaN, and a NaN selection already reads as
       * nothing everywhere downstream — the caption checks the glyph exists
       * and the draw loop compares by index, which NaN never matches. So a
       * mutation removing this guard changes no observable behaviour, which
       * is how its status was established rather than assumed.
       *
       * It stays because it makes the invalid state unrepresentable at the
       * seam rather than merely harmless three layers down, and the state it
       * would otherwise store is one no reader could explain.
       */
      if (!Number.isInteger(index) || index < 0
          || index >= field.glyphs.length) return;
      setSelected(index);
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
  }), [rotate, nearest, field, at, width, height, onSelect]);

  useEffect(() => { dirtyRef.current = true; }, [field, selected]);

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
        paintField(canvasRef.current, field, cameraRef.current,
                   { width, height }, selected, hoveredRef.current, scene);
      }
      handle = requestAnimationFrame(tick);
    };
    handle = requestAnimationFrame(tick);
    return () => { running = false; cancelAnimationFrame(handle); };
  }, [field, width, height, selected, scene]);

  const dragging = useRef<{ x: number; y: number } | null>(null);
  /** Where a press began, so a click can be told from a rotation. */
  const pressedAt = useRef<{ x: number; y: number } | null>(null);

  return (
    <figure className="chart">
      {/*
        * The canvas and the colour key, side by side.
        *
        * The key is markup rather than pixels — see `Colourbar` — so it sits
        * beside the canvas instead of on it, and stays selectable, searchable
        * and readable by a screen reader. `minWidth: 0` because a flex item
        * defaults to its content's minimum size, which for a canvas is its
        * attribute width: without it the figure stops shrinking with the
        * column and spills out of it.
        */}
      <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
      <canvas
        {...spatialKeys}
        ref={canvasRef}
        width={width}
        height={height}
        data-testid="field-3d"
        style={{ width: "100%", maxWidth: width, minWidth: 0, flex: "1 1 auto",
                 touchAction: "none" }}
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
          // A plain wheel scrolls the page; ctrl or ⌘ zooms. Without the gate
          // a reader scrolling past three stacked charts never reaches the
          // bottom of the page. Zoom is also on the controller, so the gesture
          // layer and the keyboard reach it without a wheel at all.
          if (!isZoomWheel(event)) return;
          event.preventDefault();
          zoomCamera(cameraRef.current, wheelZoomFactor(event.deltaY));
          dirtyRef.current = true;
        }}
      />
      {/*
        * Colour carries magnitude, and nothing else on the figure does.
        *
        * `Colourbar`'s own rule is that a key is drawn only where colour is a
        * variable the geometry does not already carry. Here it is exactly
        * that: length is clamped to the lattice spacing, so every arrow at
        * the top of the range is the same length and the caption tells the
        * reader to "read it by colour" — which was an instruction to consult
        * a scale that was not on the page.
        */}
      {field.glyphs.length > 0 && (
        <Colourbar
          ramp={(t) => magnitudeColour(t)}
          min={field.range.min}
          max={field.range.max}
          label={magnitude?.label ?? "Magnitude"}
          unit={magnitude?.unit}
        />
      )}
      </div>
      {/* §75: a spatial chart could not be saved at all. */}
      <ChartExport canvasRef={canvasRef} name="Vector field"
                   rotate={(degrees) => rotate(degrees, 0)}
                   redraw={() => { dirtyRef.current = true; }} />
      <figcaption className="chart-caption">
        {caption ? `${caption} ` : ""}
        {mounted && describeField(field)}
        {selected !== null && field.glyphs[selected] && (
          <> Selected: magnitude{" "}
            {field.glyphs[selected].magnitude.toPrecision(3)}
            {field.glyphs[selected].clamped
              ? " (drawn shortened; read it by colour)." : "."}</>
        )}
        {" "}{AXES_SCALED_SEPARATELY}
      </figcaption>
    </figure>
  );
}

function within(field: Field, at: (g: Glyph) => ScreenPoint,
                inside: (p: ScreenPoint) => boolean): TargetRef[] {
  const found: TargetRef[] = [];
  field.glyphs.forEach((glyph, index) => {
    if (!inside(at(glyph))) return;
    found.push({ id: String(index),
                 label: `magnitude ${glyph.magnitude.toPrecision(3)}`,
                 datum: glyph });
  });
  return found;
}


/**
 * Colour for a magnitude on 0..1.
 *
 * A single hue darkening with magnitude, rather than a rainbow. A rainbow ramp
 * has no perceptual order — readers disagree about whether green is more or
 * less than yellow — and it invents banding at the hue boundaries that looks
 * like structure in the field.
 */
export function magnitudeColour(level: number, alpha = 1): string {
  const clamped = Math.max(0, Math.min(1, level));
  // Light blue to deep blue, matching the accent this system already uses.
  const r = Math.round(191 - 154 * clamped);
  const g = Math.round(219 - 120 * clamped);
  const b = Math.round(254 - 68 * clamped);
  return `rgba(${r},${g},${b},${alpha})`;
}

/**
 * One frame of the field.
 *
 * Exported for the same reason `paintNetwork` is: a draw loop reachable only
 * through an animation frame is one no test ever runs, and this is where depth
 * ordering either happens or does not.
 */
export function paintField(
  canvas: HTMLCanvasElement | null,
  field: Field,
  camera: Camera,
  size: { width: number; height: number },
  selected: number | null,
  hovered: number | null,
  /**
   * What the three directions measure, or nothing.
   *
   * Optional so the paint tests that predate the frame still describe what
   * they meant to: they assert the *first* line drawn is an arrow's shaft, and
   * a wall painted before it would be a true statement about a different
   * chart.
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

  /*
   * Back to front. `depth` from the shared projection is larger when nearer,
   * so this ascends — descending draws the near arrows first and lets far ones
   * paint over them, and the field then reads as flat.
   */
  const drawn = field.glyphs
    .map((glyph, index) => ({
      glyph, index,
      tail: toCanvas(glyph, camera, width, height),
      head: toCanvas({ x: glyph.hx, y: glyph.hy, z: glyph.hz },
                     camera, width, height),
    }))
    .sort((a, b) => a.tail.depth - b.tail.depth);

  for (const { glyph, index, tail, head } of drawn) {
    const isSelected = index === selected;
    const isHovered = index === hovered;
    context.save();
    context.strokeStyle = isSelected
      ? selection : magnitudeColour(glyph.level, 0.85);
    context.fillStyle = context.strokeStyle;
    context.lineWidth = isSelected ? 2.4 : isHovered ? 1.8 : 1.1;

    const dx = head.x - tail.x, dy = head.y - tail.y;
    const length = Math.hypot(dx, dy);

    /*
     * A still point is a dot, not a zero-length arrow. It is also what an
     * arrow pointing straight at the camera projects to, and the two are
     * genuinely indistinguishable on a flat screen — which is why rotation is
     * a first-class control rather than a flourish.
     */
    if (length < 0.5) {
      context.beginPath();
      context.arc(tail.x, tail.y, isSelected ? 3 : 2, 0, Math.PI * 2);
      context.fill();
      context.restore();
      continue;
    }

    context.beginPath();
    context.moveTo(tail.x, tail.y);
    context.lineTo(head.x, head.y);
    context.stroke();

    /*
     * The head, drawn flat against the screen at a constant size. Two barbs
     * swept back from the tip along the projected direction — so an arrow
     * pointing away from the camera is a short line that still plainly has a
     * front, rather than an ambiguous segment.
     */
    const ux = dx / length, uy = dy / length;
    const barb = Math.min(HEAD, length);
    context.beginPath();
    context.moveTo(head.x, head.y);
    context.lineTo(head.x - ux * barb - uy * barb * 0.5,
                   head.y - uy * barb + ux * barb * 0.5);
    context.lineTo(head.x - ux * barb + uy * barb * 0.5,
                   head.y - uy * barb - ux * barb * 0.5);
    context.closePath();
    context.fill();
    context.restore();
  }

  // Over the arrows: a tick under an opaque arrowhead is a tick nobody reads.
  frame.front();
}
