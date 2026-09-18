"use client";

/**
 * Drawing the ink, on two canvases rather than one.
 *
 * The obvious implementation keeps every stroke in one canvas and repaints the
 * lot each frame. It is correct, and it degrades in exactly the way that makes
 * air drawing unusable: the cost per frame grows with how much the researcher
 * has already drawn, so the line lags more the longer the session goes on. A
 * subsystem whose whole purpose is hiding tens of milliseconds of latency cannot
 * afford to add its own, and it must not add *increasing* latency, because that
 * is the kind people blame on their machine rather than report (§146–147).
 *
 * So the layers are split by how often they change:
 *
 *   - **committed** — every finished stroke. Repainted only when that set
 *     changes: a stroke ends, an undo, a clear. Usually not for seconds at a
 *     time, however much is on it.
 *   - **live** — the one stroke being drawn. Repainted every frame, and holds at
 *     most a few hundred points, so the per-frame cost is flat no matter how
 *     many strokes are underneath.
 *
 * Neither repaint goes through React. Frames arrive around thirty times a
 * second; re-rendering a tree at that rate is the cost the spatial session was
 * rewritten to remove, and re-introducing it here would slow down the chart the
 * ink is drawn over as well as the ink.
 */

import { pageIsDark } from "@/lib/charts/theme";
import {
  forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState,
} from "react";
import { HandFrame } from "@/lib/spatial/types";
import { InkRecorder, RecorderOptions } from "@/lib/ink/recorder";
import { InkState } from "@/lib/ink/machine";
import { SpatialStroke, StrokePoint, StrokeStyle } from "@/lib/ink/stroke";
import { StabilisationLevel } from "@/lib/ink/stabilise";
import { Shape } from "@/lib/ink/shapes";
import { InkTool } from "@/lib/ink/stroke";
import { Straightedge } from "@/lib/ink/straightedge";
// Aliased: this file's component is also called `InkLayer`, and the canvas
// overlay and the annotation group are different things that happen to share a
// word.
import {
  InkLayer as AnnotationLayer, dashFor, isShowing, displayInk,
} from "@/lib/ink/layers";
import { ReferenceTimeline } from "@/lib/voice/timeline";
import { deviceFeedback } from "@/lib/spatial/feedback";

export type InkSurface = {
  /** Feed a tracked frame. Safe to call at tracker rate. */
  step: (frame: HandFrame) => void;
  arm: () => void;
  disarm: () => void;
  clear: () => void;
  /** What a finished stroke looks like, if it looks like anything (§181). */
  shapeOf: (strokeId: string) => Shape | null;
  /** Accept an offered shape. Keeps what was drawn (§174). */
  tidy: (strokeId: string, shape: Shape) => void;
  /** The layers on this canvas, and their visibility (§201). */
  layers: () => AnnotationLayer[];
  setLayerVisible: (id: string, visible: boolean) => void;
  putLayer: (layer: AnnotationLayer) => void;
  setActiveLayer: (id: string) => void;
  /** Which layer a mark would land on, so it can be shown. */
  activeLayer: () => string;
  /** Change the style new strokes are drawn with (§202). */
  setStyle: (style: Partial<StrokeStyle>) => void;
  /** Constrain the line while it is drawn (§182). */
  setStraightedge: (mode: Straightedge) => void;
  straightedge: () => Straightedge;
  /** Switch between the pen, the eraser (§176) and the lasso (§180). */
  setTool: (tool: InkTool) => void;
  tool: () => InkTool;
  undo: () => void;
  redo: () => void;
  /** What undo and redo would do, so a control can say so before it is pressed. */
  pending: () => { undo: string | null; redo: string | null };
  strokes: () => SpatialStroke[];
  state: () => InkState;
};

export const InkLayer = forwardRef<InkSurface, {
  /** Whether the pen is available at all. The first of the two locks (§139). */
  armed: boolean;
  /**
   * Cover the whole viewport rather than one figure.
   *
   * A layer sized to a single chart can only be drawn on inside that chart, and
   * strokes are recorded in its pixels — so a page with two figures needs two
   * layers, two recorders and two undo histories, and a mark cannot cross from
   * one to the other. Covering the viewport makes the pen one pen: strokes are
   * recorded in viewport coordinates, and the figure a stroke belongs to is
   * whichever the hand was addressing when it was drawn (§189), converted
   * through that chart's `bounds()`.
   *
   * The host must be positioned for this to mean anything — `position: fixed`
   * with the layer inside it, or the overlay scrolls away from the hand.
   */
  fullViewport?: boolean;
  /**
   * How hard to fight the hand's tremor.
   *
   * Changing it rebuilds the recorder, which is why any open stroke is committed
   * first: the settings belong to a stroke, and half a line drawn at one level
   * and half at another is neither.
   */
  stabilisation?: StabilisationLevel;
  options?: RecorderOptions;
  /**
   * Where gestures are recorded so speech can refer to them (§199).
   *
   * The layer opens an entry at pen-down rather than at pen-up, and that timing
   * is the whole reason the timeline takes intervals: "why are these different"
   * is usually said *while* the circle is still being drawn, so a referent that
   * only existed once the stroke finished would never be there when the word
   * arrived.
   */
  timeline?: ReferenceTimeline;
  /**
   * Told when a stroke is finished, so a host can offer to act on it.
   *
   * The second argument is the timeline entry this stroke opened, if any. The
   * host resolves what was inside the loop — only it has the chart — and closes
   * the entry with those targets.
   */
  onStroke?: (stroke: SpatialStroke, referenceId: number | null) => void;
  /**
   * A finished lasso boundary (§180), handed over once and never kept.
   *
   * Separate from `onStroke` because it is not a mark: the host resolves it into
   * a selection and it disappears. Reporting it as a stroke would put it in the
   * annotation list and invite somebody to tidy or erase a thing that no longer
   * exists.
   */
  onLasso?: (boundary: SpatialStroke, referenceId: number | null) => void;
  /** Told when the pen state changes, for a status line. Never per frame. */
  onState?: (state: InkState) => void;
}>(function InkLayer({ armed, stabilisation, options, timeline, fullViewport,
                       onStroke, onLasso, onState }, ref) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const committedRef = useRef<HTMLCanvasElement | null>(null);
  const liveRef = useRef<HTMLCanvasElement | null>(null);
  const recorderRef = useRef<InkRecorder | null>(null);
  /**
   * Repaint flags, as refs rather than state.
   *
   * Setting state per frame would re-render this component and everything it
   * sits over thirty times a second to move a line by three pixels.
   */
  const liveDirty = useRef(false);
  const committedDirty = useRef(false);
  const [size, setSize] = useState({ width: 0, height: 0 });

  // Callbacks are read through a ref so a host passing an inline arrow does not
  // have to memoise it to avoid rebuilding the recorder — and, more importantly,
  // so the recorder never ends up holding a closure over stale props.
  const handlers = useRef({ onStroke, onLasso, onState, timeline });
  handlers.current = { onStroke, onLasso, onState, timeline };
  /** The timeline entry the open stroke belongs to. */
  const referenceRef = useRef<number | null>(null);

  if (!recorderRef.current) {
    recorderRef.current = new InkRecorder({ stabilisation, ...options });
  }

  // Rebuilding on a level change, carrying the finished strokes across. The
  // alternative — mutating the live recorder — would leave the open stroke's
  // filter, dead zone and gain anchor half-configured.
  const level = useRef(stabilisation);
  if (level.current !== stabilisation) {
    level.current = stabilisation;
    const previous = recorderRef.current;
    previous.disarm();
    const next = new InkRecorder({ stabilisation, ...options });
    // Everything, in one call: strokes and the undo history together. Moving
    // only the strokes left the researcher unable to undo because they had
    // adjusted a slider.
    next.adoptFrom(previous);
    if (armed) next.arm();
    recorderRef.current = next;
    committedDirty.current = true;
    liveDirty.current = true;
  }

  /** Match the backing store to the display, or every line is soft. */
  const resize = useCallback(() => {
    const host = hostRef.current;
    if (!host) return;
    // The viewport when the pen spans the page, so a stroke's coordinates and a
    // chart's `bounds()` are in the same frame and converting between them is a
    // subtraction rather than a guess.
    const width = fullViewport ? window.innerWidth : host.clientWidth;
    const height = fullViewport ? window.innerHeight : host.clientHeight;
    if (!width || !height) return;
    setSize({ width, height });
    recorderRef.current?.setViewport({ width, height });
    // A repaint is required, not optional: resizing a canvas clears it, so the
    // committed layer is blank until it is redrawn — and it is the layer that
    // may not be repainted again for a minute.
    committedDirty.current = true;
    liveDirty.current = true;
  }, [fullViewport]);

  useEffect(() => {
    resize();
    if (typeof window !== "undefined" && fullViewport) {
      window.addEventListener("resize", resize);
    }
    if (typeof ResizeObserver === "undefined") {
      return () => window.removeEventListener?.("resize", resize);
    }
    const observer = new ResizeObserver(resize);
    if (hostRef.current) observer.observe(hostRef.current);
    return () => {
      observer.disconnect();
      window.removeEventListener?.("resize", resize);
    };
  }, [resize, fullViewport]);

  useEffect(() => {
    const recorder = recorderRef.current;
    if (!recorder) return;
    if (armed) recorder.arm();
    else recorder.disarm();
    handlers.current.onState?.(recorder.state());
    committedDirty.current = true;
    liveDirty.current = true;
  }, [armed]);

  useImperativeHandle(ref, (): InkSurface => ({
    step(frame: HandFrame) {
      const recorder = recorderRef.current;
      if (!recorder) return;
      const result = recorder.step(frame);
      if (result.open || result.events.length) liveDirty.current = true;

      const timeline = handlers.current.timeline;
      if (result.events.includes("penDown")) {
        // Opened here, not on commit. A word spoken mid-stroke has to find
        // something to bind to, and by the time the stroke commits the word has
        // already been said.
        referenceRef.current = timeline?.begin(frame.timestamp, "region") ?? null;
      }

      if (result.lasso) {
        liveDirty.current = true;
        committedDirty.current = true;
        /*
         * A selection lands with a detent (§94's "selected").
         *
         * The lasso's boundary vanishes the instant it closes, which is right —
         * it is a question, not a mark — but it meant the most consequential
         * gesture in the subsystem was also the only one that ended in silence.
         * The researcher let go and the loop simply was not there any more.
         */
        deviceFeedback.emit("select");
        handlers.current.onLasso?.(result.lasso, referenceRef.current);
        referenceRef.current = null;
      }

      // An erase changes the canvas without adding a stroke, so it needs the
      // repaint that `committed` used to provide by accident.
      if (result.erased) committedDirty.current = true;

      if (result.committed) {
        committedDirty.current = true;
        const strokes = recorder.strokes();
        handlers.current.onStroke?.(strokes[strokes.length - 1],
                                    referenceRef.current);
        referenceRef.current = null;
      } else if (referenceRef.current !== null
                 && (result.events.includes("penUp")
                     || result.events.includes("strokeCancelled"))) {
        /*
         * The pen lifted and no stroke was kept.
         *
         * This happens for real: a stroke that begins drawing and then records
         * fewer than two points — tracking lost immediately, or every frame
         * after the first rejected as a glitch — is not a mark, so `commit`
         * discards it and `onStroke` never fires. The timeline entry opened at
         * pen-down was then never closed, and an open entry is deliberately
         * never forgotten, because a hand may rest mid-stroke. So one such
         * stroke would sit open for the rest of the session and capture *every
         * word spoken afterwards* under the "during" rule, binding each to an
         * empty referent while reporting success.
         *
         * Found by mutation: deleting the original cleanup changed nothing,
         * because that branch handled `strokeCancelled`, which is emitted from
         * PEN_DOWN — a state reached *before* `penDown` is emitted, so no entry
         * existed yet and the code was unreachable. The reachable leak was the
         * committed-nothing case, which had no handling at all.
         */
        timeline?.abandon(referenceRef.current);
        referenceRef.current = null;
      }
      // §177: a tick at the moment the eraser meets ink, which is what makes a
      // virtual mark feel like something that was there. On the transition
      // only — a tick per frame along a long line is a buzz, not a boundary.
      if (result.events.includes("erasedInk")) deviceFeedback.emit("hover");

      // State is published on change only. Pushing it per frame would re-render
      // the host's status line thirty times a second to write the same word.
      if (result.events.length) handlers.current.onState?.(recorder.state());
    },
    arm() { recorderRef.current?.arm(); },
    disarm() {
      recorderRef.current?.disarm();
      committedDirty.current = true;
      liveDirty.current = true;
    },
    clear() {
      recorderRef.current?.clear();
      committedDirty.current = true;
      liveDirty.current = true;
    },
    shapeOf(strokeId) { return recorderRef.current?.shapeOf(strokeId) ?? null; },
    tidy(strokeId, shape) {
      recorderRef.current?.tidy(strokeId, shape);
      committedDirty.current = true;
    },
    layers() { return recorderRef.current?.allLayers() ?? []; },
    setLayerVisible(id, visible) {
      recorderRef.current?.setLayerVisible(id, visible);
      committedDirty.current = true;
      liveDirty.current = true;
    },
    putLayer(layer) {
      recorderRef.current?.putLayer(layer);
      committedDirty.current = true;
    },
    setActiveLayer(id) { recorderRef.current?.setActiveLayer(id); },
    activeLayer() {
      return recorderRef.current?.activeLayerId() ?? "researcher";
    },
    setStyle(style) { recorderRef.current?.setStyle(style); },
    setStraightedge(mode) { recorderRef.current?.setStraightedge(mode); },
    straightedge() {
      return recorderRef.current?.currentStraightedge() ?? "off";
    },
    setTool(tool) { recorderRef.current?.setTool(tool); },
    tool() { return recorderRef.current?.currentTool() ?? "pen"; },
    undo() {
      recorderRef.current?.undo();
      committedDirty.current = true;
    },
    redo() {
      recorderRef.current?.redo();
      committedDirty.current = true;
    },
    pending() {
      const recorder = recorderRef.current;
      return { undo: recorder?.describeUndo() ?? null,
               redo: recorder?.describeRedo() ?? null };
    },
    strokes() { return recorderRef.current?.strokes() ?? []; },
    state() { return recorderRef.current?.state() ?? "DISABLED"; },
  }), []);

  // The paint loop. One animation frame, repainting only what is dirty.
  useEffect(() => {
    if (typeof requestAnimationFrame === "undefined") return;
    let running = true;
    let handle = 0;

    const tick = () => {
      if (!running) return;
      const recorder = recorderRef.current;
      if (recorder) {
        if (committedDirty.current) {
          committedDirty.current = false;
          paint(committedRef.current, recorder.strokes(), size,
                recorder.allLayers());
        }
        if (liveDirty.current) {
          liveDirty.current = false;
          const open = recorder.openStroke();
          paint(liveRef.current, open ? [open] : [], size,
                recorder.allLayers());
        }
      }
      handle = requestAnimationFrame(tick);
    };

    handle = requestAnimationFrame(tick);
    return () => { running = false; cancelAnimationFrame(handle); };
  }, [size]);

  const dpr = typeof window === "undefined" ? 1 : window.devicePixelRatio || 1;
  const canvasStyle = {
    position: "absolute" as const, inset: 0,
    width: "100%", height: "100%",
    // The ink is an overlay: it must never eat the clicks meant for what it is
    // drawn over. Nothing here is interactive — the hand is the input.
    pointerEvents: "none" as const,
  };

  return (
    <div ref={hostRef} data-testid="ink-layer"
         style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
      <canvas ref={committedRef} data-testid="ink-committed" aria-hidden="true"
              width={Math.round(size.width * dpr)}
              height={Math.round(size.height * dpr)} style={canvasStyle} />
      <canvas ref={liveRef} data-testid="ink-live" aria-hidden="true"
              width={Math.round(size.width * dpr)}
              height={Math.round(size.height * dpr)} style={canvasStyle} />
    </div>
  );
});

/**
 * Paint a set of strokes onto one canvas, replacing whatever was there.
 *
 * Exported for testing: the draw loop is the part whose cost is paid per frame,
 * and leaving it reachable only through an animation frame in happy-dom means it
 * is the part no test ever runs. That was true of the volume chart's painter for
 * most of its life.
 */
export function paint(canvas: HTMLCanvasElement | null,
                      strokes: SpatialStroke[],
                      size: { width: number; height: number },
                      layers: readonly AnnotationLayer[] = []): void {
  if (!canvas) return;
  const context = canvas.getContext("2d");
  const dark = pageIsDark();
  if (!context) return;

  const dpr = canvas.width && size.width ? canvas.width / size.width : 1;
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  context.clearRect(0, 0, size.width, size.height);

  const layerOf = (id: string) => layers.find((l) => l.id === id);

  for (const stroke of strokes) {
    /*
     * Whose mark it is decides how it is drawn, and the style does not get a
     * vote (§201).
     *
     * An assistant's annotation is always dashed, whatever style it carries.
     * That override is the whole mechanism: distinguishability cannot be lost
     * by a caller setting a style, by a style being copied between strokes, or
     * by a future control offering "dash" as an option. A figure showing a
     * suggested trend line is making a different claim from one showing a trend
     * line somebody committed to, and the difference has to survive being
     * screenshotted and looked at a year later.
     */
    const layer = layerOf(stroke.layerId);
    if (layer && !isShowing(layer)) continue;

    const points = stroke.points.length ? stroke.points : stroke.originalPoints;
    if (points.length < 2) {
      // A single point is not drawn as a line, and drawing it as a dot would
      // reintroduce exactly the stray mark the two-frame contact rule removes.
      continue;
    }
    context.lineWidth = stroke.style.width;
    // Shown in the dark theme's step of the same ink; recorded as drawn.
    context.strokeStyle = displayInk(stroke.style.colour, dark);
    context.globalAlpha = stroke.style.opacity;
    context.lineCap = "round";
    context.lineJoin = "round";
    const dash = layer ? dashFor(layer)
                       : (stroke.style.dashed ? [6, 5] : null);
    context.setLineDash(dash ? [...dash] : []);

    context.beginPath();
    drawPath(context, points);
    context.stroke();
  }
  context.globalAlpha = 1;
}

/**
 * A smooth path through the points, without inventing a shape.
 *
 * Quadratic segments between midpoints: the curve passes near every observation
 * and through none of them, which removes the polygonal look of a 30 Hz sample
 * without moving the line anywhere the hand did not go. Fitting a spline
 * *through* the points would overshoot at corners — the same failure prediction
 * is clamped to avoid, arriving from the other direction.
 *
 * The rendered curve is never what a selection is resolved against. That uses
 * `originalPoints`, which this does not touch.
 */
function drawPath(context: CanvasRenderingContext2D, points: StrokePoint[]): void {
  context.moveTo(points[0].x, points[0].y);
  for (let i = 1; i < points.length - 1; i += 1) {
    const midX = (points[i].x + points[i + 1].x) / 2;
    const midY = (points[i].y + points[i + 1].y) / 2;
    context.quadraticCurveTo(points[i].x, points[i].y, midX, midY);
  }
  const last = points[points.length - 1];
  context.lineTo(last.x, last.y);
}
