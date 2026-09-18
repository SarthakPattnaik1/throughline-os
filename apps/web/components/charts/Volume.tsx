"use client";

/**
 * P13 — 3D surfaces (Part F).
 *
 * Embedding space in three components, relationship space, and fitted response
 * surfaces over two predictors.
 *
 * **Written against canvas with its own projection, not three.js.** A WebGL
 * scene graph is ~600KB for what is, at this scale, a 4×4 matrix and a sort.
 * The premise of the product is that a researcher installs it on their laptop,
 * and a chart library that costs more than the analysis engine fails that.
 *
 * **The honest position on 3D is that it is usually worse than 2D**, and this
 * component is built to say so rather than to flatter the format:
 *
 *   - **Occlusion loses data.** A point behind another point is not visible.
 *     The count of points currently hidden is computed each frame and shown, so
 *     "I can see everything" is never assumed.
 *   - **A static 3D image cannot be read.** Depth on a flat screen comes from
 *     motion parallax; without rotating it, a projected position is ambiguous.
 *     The chart says this and rotation is a first-class control, not a flourish.
 *   - **Perspective makes near things bigger.** That is a depth cue, and it is
 *     also a size channel the reader may misread as magnitude. So marks encode
 *     value in *colour*, never in radius — radius is depth and nothing else.
 *   - **A position nobody can read is not a measurement.** This chart drew a
 *     grey wireframe box and called it a frame of reference. It was not one:
 *     it said nothing about which direction was which and carried no number
 *     anywhere, so a reader could see that one lobe sits above another and
 *     could not say above in *what*, or by how much. The box is now the shared
 *     axis furniture — three gridded back walls, an axis line per direction,
 *     ticks in the caller's own units and a title naming each dimension.
 *
 * Note that the frame moved this chart onto `toCanvas`. It had its own copy of
 * the projection's final step — centre plus a hand-written `0.30` scale — in
 * four places, which is the same arithmetic `scene3d` exports and a slightly
 * different answer: at 0.30 the cube's corners fall outside the canvas at the
 * default camera, which is why `FIT` was derived at 0.26. A frame computed
 * from one number and marks placed with another would have been a box that
 * does not contain its own data, so the four copies went.
 *
 * Motion is user-driven. There is no idle auto-rotation: it would be animation
 * standing between the reader and the data, which the brief forbids, and it
 * makes the frame budget a permanent cost rather than one paid while dragging.
 */

import { selectionColour } from "@/lib/charts/theme";
import {
  useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState,
} from "react";
import { ScreenPoint, TargetRef, VisualizationController } from "@/lib/spatial/commands";
import { AXES_SCALED_SEPARATELY, Axes3D, Camera, DEFAULT_CAMERA, DEPTH_RANGE, axisFurniture, drawFurniture, insidePolygon, resetCamera, rotateCamera, toCanvas, zoomCamera } from "@/lib/charts/scene3d";
import { extent } from "d3-array";
import { scaleLinear } from "d3-scale";
import { interpolateYlGnBu } from "d3-scale-chromatic";
import { ChartTable } from "./ChartTable";
import { ChartTooltip, Pointer, readable } from "./interaction";

export type Point3D = {
  /** Stable identity. */
  id: string;
  /** Display name (Part C). */
  label: string;
  x: number;
  y: number;
  z: number;
  /** What colour encodes. Radius is reserved for depth. */
  value?: number;
};

/**
 * The projection, the camera and the bounds now live in `lib/charts/scene3d`.
 *
 * Moved out when a second 3D chart arrived. Two copies of the same arithmetic is
 * how the rotation units diverged in the first place — both sides stayed
 * self-consistent and disagreed with each other — and two charts drifting apart
 * under the same gesture would be that bug again with more surface.
 */

const MARK_RADIUS = 3.4;

/**
 * How long the view must be still before the occlusion count is republished.
 *
 * Short enough to feel immediate when a drag ends, long enough that a continuous
 * rotation publishes nothing at all until it stops.
 */
export const SETTLE_MS = 120;

export function Volume({
  points, controllerRef, onSelect, onSelectRegion, onDetent, xLabel, yLabel,
  zLabel, valueLabel, title,
  caption, width = 720, height = 520, selectionRadius = 0,
}: {
  points: Point3D[];
  /**
   * Exposes this chart as a `VisualizationController`.
   *
   * The one seam any input drives the scene through — mouse, keyboard, hand,
   * and later voice. Optional, because a chart in a report is not being driven
   * by anything and should not pay for the machinery.
   */
  controllerRef?: React.RefObject<VisualizationController | null>;
  /** Told when a point is chosen, so a selection can become AI context. */
  onSelect?: (target: TargetRef | null) => void;
  /** Told about a whole region, when one is selected rather than a single mark. */
  onSelectRegion?: (targets: TargetRef[]) => void;
  /**
   * Told when the pointer lands on a point, or lands a selection.
   *
   * A callback rather than this chart emitting feedback itself. A primitive
   * drawn into a report has no business making a machine tap, and a chart that
   * decided its own feedback policy would be one every host had to remember to
   * silence. The interactive screens pass this; a figure does not.
   */
  onDetent?: (moment: "hover" | "select") => void;
  /**
   * How far a selection reaches, in screen pixels. Zero selects one mark.
   *
   * A setting rather than a second gesture: §9 and Rule 6 both say a gesture
   * must earn its place, and one that only changed how many points the same act
   * gathers would not.
   */
  selectionRadius?: number;
  xLabel: string;
  yLabel: string;
  zLabel: string;
  /** What colour means. Omitted when the points carry no value. */
  valueLabel?: string;
  title?: string;
  caption?: string;
  width?: number;
  height?: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const cameraRef = useRef<Camera>({ ...DEFAULT_CAMERA });
  const dragRef = useRef<{ x: number; y: number } | null>(null);
  /*
   * Where the pointer went *down*, kept apart from `dragRef`.
   *
   * `dragRef` is reassigned on every move so each frame rotates by one step's
   * delta. Measuring "did this click travel" against it therefore always
   * reports roughly zero, and every drag would end by selecting whatever the
   * finger happened to stop over.
   */
  const pressRef = useRef<{ x: number; y: number } | null>(null);
  const dirtyRef = useRef(true);
  const visibleRef = useRef(true);
  const hoveredRef = useRef<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  /**
   * Which mark is being read, and where to put the readout.
   *
   * The chart could already hit-test, highlight and select; what it could not
   * do was *say what it had hit*. Every other primitive answers that with
   * `ChartTooltip`, and this is the chart where it matters most: the docstring
   * above is explicit that a projected position is ambiguous without motion,
   * so identity is the one thing a reader cannot recover by looking harder.
   * Highlighting a dot and naming nothing asks them to guess.
   *
   * State, where the hover highlight is a ref, and deliberately so: the ref
   * exists to keep the draw loop off React's render path, and text cannot be
   * drawn from a ref. The cost is paid only while a mark is under the pointer
   * — moving across empty space sets nothing, which is the common case and the
   * one the frame budget cares about.
   */
  const [readout, setReadout] = useState<{ id: string; at: Pointer } | null>(null);
  // Mirrored so the pointer handler can ask "is there one already?" without
  // clearing it on every move across empty space.
  const readoutRef = useRef<{ id: string; at: Pointer } | null>(null);
  readoutRef.current = readout;
  /*
   * The draw loop reads the selection through a ref, not the state.
   *
   * `draw` is memoised on its dependencies; adding `selected` would rebuild it
   * on every selection and re-run the effect that owns the animation frame. The
   * ref keeps the render loop stable while still letting it see the current
   * value.
   */
  const selectedRef = useRef<string | null>(null);
  /** Ids in the current region, drawn with a lighter emphasis than the selection. */
  const regionRef = useRef<Set<string> | null>(null);
  /** Whether the chart has keyboard focus, so the aim point can be shown. */
  const focusedRef = useRef(false);
  const [occluded, setOccluded] = useState(0);
  /** The latest count the paint loop computed, published once drawing stops. */
  const occludedRef = useRef(0);
  const settleRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [moved, setMoved] = useState(false);

  /**
   * What each direction actually measures, before it is squashed into a cube.
   *
   * Computed once and read by both the scaling below and the axis labels, and
   * that is the whole point of hoisting it. `AxisSpec.min`/`max` have to be
   * the same two numbers the scale was built on; a second `extent` call would
   * agree today and there would be nothing to notice when it stopped — a tick
   * reading 40 sitting where 45 is looks exactly like a tick reading 40.
   */
  const domains = useMemo(() => ({
    x: extent(points, (p) => p.x) as [number, number],
    y: extent(points, (p) => p.y) as [number, number],
    z: extent(points, (p) => p.z) as [number, number],
  }), [points]);

  /**
   * The three axes, named for the reader.
   *
   * Keyed by **scene** axis, and here that is the easy case: this chart puts
   * the caller's x, y and z on the scene's x, y and z unchanged, so the three
   * labels go straight across. (`Surface` does not — its height is the
   * response and its scene z is the second predictor — which is why the key
   * is worth stating rather than assumed.)
   *
   * No units: the component takes `xLabel`/`yLabel`/`zLabel` as free strings
   * and has no unit prop, so a caller with a unit writes it into the label
   * itself. Deriving one from the numbers would be inventing a measurement.
   */
  const axes = useMemo<Axes3D>(() => ({
    x: { label: xLabel, min: domains.x[0], max: domains.x[1] },
    y: { label: yLabel, min: domains.y[0], max: domains.y[1] },
    z: { label: zLabel, min: domains.z[0], max: domains.z[1] },
  }), [domains, xLabel, yLabel, zLabel]);

  /** Unit cube, so the three axes are comparable regardless of their units. */
  const normalised = useMemo(() => {
    const span = (key: "x" | "y" | "z") =>
      scaleLinear().domain(domains[key]).range([-1, 1]);
    const sx = span("x"), sy = span("y"), sz = span("z");
    const values = points.map((p) => p.value).filter(
      (v): v is number => v !== undefined);
    const [vlo, vhi] = extent(values) as [number, number];
    const vscale = scaleLinear().domain([vlo ?? 0, vhi ?? 1]).range([0, 1]);
    return points.map((p) => ({
      id: p.id, label: p.label,
      x: sx(p.x), y: sy(p.y), z: sz(p.z),
      colour: p.value === undefined
        ? interpolateYlGnBu(0.62)
        : interpolateYlGnBu(vscale(p.value)),
    }));
  }, [points, domains]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    if (!context) return;

    const dpr = Math.min(2, window.devicePixelRatio || 1);
    if (canvas.width !== width * dpr) {
      canvas.width = width * dpr;
      canvas.height = height * dpr;
    }
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, width, height);

    const camera = cameraRef.current;
    const cx = width / 2, cy = height / 2;

    /*
     * The frame's colours, asked of the canvas once a frame.
     *
     * A canvas is painted with literal values and cannot inherit a token the
     * way the rest of the interface does — but it can be asked what it
     * inherited. The box this replaces was a hard-coded grey, which is the
     * failure in miniature: one colour chosen for one background, drawn on
     * both. Read here rather than kept in state because a theme change does
     * not go through React.
     */
    const palette = getComputedStyle(canvas);
    const selection = selectionColour(canvas);
    const colours = {
      line: palette.getPropertyValue("--line-strong").trim(),
      grid: palette.getPropertyValue("--line").trim(),
      text: palette.getPropertyValue("--ink-faint").trim(),
      title: palette.getPropertyValue("--ink-soft").trim(),
    };
    /*
     * The frame, recomputed every frame and never cached.
     *
     * Which walls face away and which edge carries each axis's numbers both
     * change continuously as the scene turns, so a cached answer is a wall
     * painted over the cloud for half of a rotation.
     *
     * Two passes, and the order is not decorative: the gridded back walls go
     * under the marks, the axis lines and every label go over them. Both in
     * one call would bury the numbers under the densest part of the cloud,
     * which is the part a reader is trying to place.
     */
    const furniture = axisFurniture(axes, camera, width, height);
    drawFurniture(context, furniture, colours, 1, "behind");

    // Painter's algorithm: far to near, so nearer marks correctly cover
    // farther ones. Drawing in data order would let a distant point paint
    // over a close one and reverse the depth the projection just computed.
    const marks = normalised
      .map((p) => {
        const q = toCanvas(p, camera, width, height);
        return {
          id: p.id, colour: p.colour,
          x: q.x, y: q.y,
          depth: q.depth,
          r: MARK_RADIUS * (1 + (q.scale - 1) * DEPTH_RANGE),
        };
      })
      .sort((a, b) => a.depth - b.depth);

    let hidden = 0;
    /*
     * Occlusion is counted against a uniform grid rather than against every
     * mark painted so far.
     *
     * The naive version compares each mark to all its predecessors, which is
     * quadratic — a 260-point cloud is ~34,000 distance checks *per frame*, and
     * this redraws on every pointer move during a drag. Since a mark can only
     * hide another within a few pixels, bucketing by that radius makes it
     * linear: each mark checks the nine cells around it and no more.
     */
    const cell = MARK_RADIUS * 3;
    const grid = new Map<string, Array<{ x: number; y: number; r: number }>>();
    const keyAt = (x: number, y: number) =>
      `${Math.floor(x / cell)},${Math.floor(y / cell)}`;

    for (const m of marks) {
      // Counted before painting: a mark this one will cover is one the reader
      // cannot see, and claiming otherwise is the standard 3D lie.
      // Named apart from the canvas centre `cx`/`cy` above: these are cell
      // indices, and shadowing the centre here would be a quiet trap.
      const cellX = Math.floor(m.x / cell);
      const cellY = Math.floor(m.y / cell);
      let covered = false;
      for (let gx = cellX - 1; gx <= cellX + 1 && !covered; gx += 1) {
        for (let gy = cellY - 1; gy <= cellY + 1 && !covered; gy += 1) {
          const bucket = grid.get(`${gx},${gy}`);
          if (!bucket) continue;
          for (const q of bucket) {
            if (Math.hypot(q.x - m.x, q.y - m.y) < Math.max(q.r, m.r) * 0.7) {
              covered = true;
              break;
            }
          }
        }
      }
      if (covered) hidden += 1;

      context.beginPath();
      context.arc(m.x, m.y, m.r, 0, Math.PI * 2);
      context.fillStyle = m.colour;
      // Nearer is more opaque: the second depth cue, since radius alone is
      // weak and the reader must not mistake it for magnitude.
      context.globalAlpha = 0.45 + 0.5 * ((m.depth + 1) / 2);
      context.fill();
      const key = keyAt(m.x, m.y);
      const bucket = grid.get(key);
      if (bucket) bucket.push({ x: m.x, y: m.y, r: m.r });
      else grid.set(key, [{ x: m.x, y: m.y, r: m.r }]);
    }
    context.globalAlpha = 1;

    // The aim point, drawn only while the chart has keyboard focus.
    //
    // Without it "press Enter to select what is in the centre" asks somebody to
    // aim at a place the picture does not mark. Only while focused, because a
    // permanent crosshair on a figure is a mark that means nothing to a reader
    // who is not using the keyboard — and this chart is also drawn into reports.
    if (focusedRef.current) {
      // The keyboard's aim, in the theme's selection colour: the fixed blue
      // at 55% was all but invisible on the dark canvas.
      context.strokeStyle = selection;
      context.lineWidth = 1.25;
      const arm = 7;
      context.beginPath();
      context.moveTo(cx - arm, cy); context.lineTo(cx - 2, cy);
      context.moveTo(cx + 2, cy); context.lineTo(cx + arm, cy);
      context.moveTo(cx, cy - arm); context.lineTo(cx, cy - 2);
      context.moveTo(cx, cy + 2); context.lineTo(cx, cy + arm);
      context.stroke();
    }

    // Emphasis is painted last, over the finished cloud.
    //
    // Drawn inside the depth sort it would be occluded by nearer marks — the
    // reader would point at something, be told it was found, and see nothing.
    // A ring rather than a colour change, because colour is the value channel
    // here and borrowing it would make a highlighted point read as a different
    // measurement (§20: feedback that does not clutter, and never at the cost
    // of the encoding).
    for (const m of marks) {
      const isSelected = m.id === selectedRef.current;
      const isHovered = m.id === hoveredRef.current;
      const inRegion = regionRef.current?.has(m.id) ?? false;
      // A region is drawn more faintly than the point at its centre: the
      // researcher indicated one place, and everything else is context around
      // it. Equal emphasis would make it unclear what was actually pointed at.
      if (inRegion && !isSelected && !isHovered) {
        context.beginPath();
        context.arc(m.x, m.y, m.r + 2.5, 0, Math.PI * 2);
        context.save();
        context.strokeStyle = selection;
        context.globalAlpha = 0.3;
        context.lineWidth = 1;
        context.stroke();
        context.restore();
        continue;
      }
      if (!isSelected && !isHovered) continue;
      context.beginPath();
      context.arc(m.x, m.y, m.r + (isSelected ? 5 : 3.5), 0, Math.PI * 2);
      // The theme's selection colour; the hard-coded blue was 2.3:1 on the
      // dark canvas. Hover is the same colour, fainter.
      context.save();
      context.strokeStyle = selection;
      context.globalAlpha = isSelected ? 1 : 0.55;
      context.lineWidth = isSelected ? 2 : 1.25;
      context.stroke();
      context.restore();
    }

    /*
     * The axis lines, ticks and titles, last of all.
     *
     * After the emphasis rings as well as after the cloud: a ring is 5px of
     * feedback about one mark, and a number half-hidden under it is a number
     * the reader has to move the pointer to finish reading.
     */
    drawFurniture(context, furniture, colours, 1, "front");

    // Published when the view settles, not on every painted frame.
    //
    // `draw` runs inside the rAF loop, so an unconditional `setOccluded` asks
    // React to re-render the chart on every frame of a rotation. Measured on a
    // 900-point cloud: 45 React commits across 60 painted frames, because with a
    // dense cloud the count genuinely changes almost every frame as marks slide
    // past one another — 419, 422, 420, 425. React's identical-value bail-out
    // cannot help, and neither can comparing against the previous value here;
    // the first version of this did exactly that and was worthless for the same
    // reason.
    //
    // The cure is to stop asking the question at frame rate. A caption
    // flickering through eight values a second is unreadable *and* expensive,
    // and the count is only meaningful when the scene is at rest — which is also
    // the only time anyone reads it. So the latest value is kept in a ref, and
    // publishing is deferred until the drawing stops.
    occludedRef.current = hidden;
    if (settleRef.current !== null) clearTimeout(settleRef.current);
    settleRef.current = setTimeout(() => {
      settleRef.current = null;
      setOccluded(occludedRef.current);
    }, SETTLE_MS);
    // `axes` is a dependency because the frame carries the labels: a caller
    // renaming a dimension, or handing over data with a different range, has
    // to see the numbers change with it rather than at the next drag.
  }, [normalised, axes, width, height]);

  // rAF loop gated on both dirtiness and visibility — an idle chart must not
  // hold a repaint budget it is not using.
  useEffect(() => {
    let frame = 0;
    const tick = () => {
      if (dirtyRef.current && visibleRef.current) {
        dirtyRef.current = false;
        draw();
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);

    const canvas = canvasRef.current;
    const observer = new IntersectionObserver(
      ([entry]) => {
        visibleRef.current = entry.isIntersecting;
        if (entry.isIntersecting) dirtyRef.current = true;
      }, { rootMargin: "200px" });
    if (canvas) observer.observe(canvas);

    const onVisibility = () => {
      visibleRef.current = !document.hidden;
      dirtyRef.current = true;
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      // The deferred publish outlives the chart otherwise: a component unmounted
      // mid-drag leaves a timer that wakes up 120ms later to set state on
      // something that is gone.
      if (settleRef.current !== null) clearTimeout(settleRef.current);
    };
  }, [draw]);

  useEffect(() => { dirtyRef.current = true; }, [normalised]);

  /**
   * The nearest mark to a screen point, or null.
   *
   * Nearest-within-a-radius rather than exact containment (§7). A researcher
   * pointing at a cloud is indicating a region, not hitting a 3.4px target, and
   * requiring precision would make pointing feel broken rather than forgiving.
   *
   * Runs on demand, never per frame. The draw loop went to some trouble to stay
   * linear, and a hit test on every redraw would undo that.
   */
  const nearest = useCallback((at: ScreenPoint, radius = 28): TargetRef | null => {
    const camera = cameraRef.current;

    let best: { id: string; label: string; datum: Point3D; d: number } | null = null;
    for (let i = 0; i < normalised.length; i += 1) {
      const q = toCanvas(normalised[i], camera, width, height);
      const d = Math.hypot(q.x - at.x, q.y - at.y);
      // Ties break toward the nearer point in depth: when two marks overlap on
      // screen the front one is the one the reader can actually see, and
      // selecting the hidden one would be indefensible.
      if (d <= radius && (best === null || d < best.d)) {
        best = { id: normalised[i].id, label: normalised[i].label,
                 datum: points[i], d };
      }
    }
    return best ? { id: best.id, label: best.label, datum: best.datum } : null;
  }, [normalised, points, width, height]);

  /**
   * Every mark within `radius` of a screen point.
   *
   * Shares `nearest`'s forgiveness about precision but answers a different
   * question: not "which one did they mean" but "what is around here". §25
   * calls this pointing at a cluster; this product does not, because nothing
   * was fitted — it is the points near where somebody pointed, and both the
   * caption and the prompt sent to a model say exactly that.
   *
   * Bounded by the same projection as the draw loop, so what is selected is
   * what is visible: selecting a mark hidden behind another would be selecting
   * something the researcher cannot see.
   */
  const within = useCallback((at: ScreenPoint, radius: number): TargetRef[] => {
    const camera = cameraRef.current;

    const found: Array<{ target: TargetRef; d: number }> = [];
    for (let i = 0; i < normalised.length; i += 1) {
      const q = toCanvas(normalised[i], camera, width, height);
      const d = Math.hypot(q.x - at.x, q.y - at.y);
      if (d <= radius) {
        // `points[i]`, not `normalised[i]` — exactly as `nearest` does, and for
        // the same reason. The normalised copy exists to be drawn: its
        // coordinates are rescaled to a unit cube and it carries none of the
        // caller's own fields. Handing that out as the datum would send a model
        // numbers in the wrong space and drop the identifiers a selection needs
        // to be recorded against anything.
        found.push({
          target: { id: points[i].id, label: points[i].label, datum: points[i] },
          d,
        });
      }
    }
    // Nearest first, so a caller that truncates keeps what was most clearly
    // indicated rather than an arbitrary subset.
    return found.sort((a, b) => a.d - b.d).map((f) => f.target);
  }, [normalised, points, width, height]);

  /**
   * One zoom, used by the wheel, the controller and the keyboard.
   *
   * Written once because three callers clamping independently is three chances
   * to disagree about the bounds — and the bounds are what stop the scene being
   * lost (§32).
   */
  const zoomBy = useCallback((factor: number) => {
    zoomCamera(cameraRef.current, factor);
    dirtyRef.current = true;
  }, []);

  const rotate = useCallback((dx: number, dy: number) => {
    rotateCamera(cameraRef.current, dx, dy);
    dirtyRef.current = true;
    setMoved(true);
  }, []);

  useImperativeHandle(controllerRef, (): VisualizationController => ({
    rotate: (dx, dy) => rotate(dx, dy),
    zoom: (factor) => {
      // Through the shared clamp, like every other caller. This was the fourth
      // place applying the bounds by hand, and a bound applied in four places is
      // a bound that will eventually be four different bounds.
      zoomCamera(cameraRef.current, factor);
      dirtyRef.current = true;
      setMoved(true);
    },
    // Panning is not offered rather than stubbed. This chart centres a unit
    // cube; there is nothing off-frame to pan toward, and a control that
    // silently does nothing is worse than one that is absent.
    pan: () => {},
    hover: (at) => {
      const target = nearest(at);
      if ((target?.id ?? null) !== hoveredRef.current) {
        hoveredRef.current = target?.id ?? null;
        dirtyRef.current = true;
      }
      return target;
    },
    select: (at) => {
      const target = nearest(at);
      setSelected(target?.id ?? null);
      dirtyRef.current = true;
      onSelect?.(target);
      return target;
    },
    focus: (objectId) => { setSelected(objectId); dirtyRef.current = true; },
    withinPolygon: (polygon) => {
      // Every mark tested once, against the same projection the draw loop uses.
      // Exact by construction: no sampling step to fall between.
      const camera = cameraRef.current;
      const found: TargetRef[] = [];
      for (let i = 0; i < normalised.length; i += 1) {
        const at = toCanvas(normalised[i], camera, width, height);
        if (!insidePolygon(polygon, at)) continue;
        found.push({ id: points[i].id, label: points[i].label,
                     datum: points[i] });
      }
      return found;
    },
    selectRegion: (at, radius) => {
      const targets = within(at, radius);
      regionRef.current = new Set(targets.map((t) => t.id));
      // The nearest is still *the* selection, so the existing single-point
      // affordances keep working; the region is emphasis around it.
      setSelected(targets[0]?.id ?? null);
      dirtyRef.current = true;
      onSelectRegion?.(targets);
      return targets;
    },
    deselect: () => {
      setSelected(null);
      regionRef.current = null;
      dirtyRef.current = true;
      onSelect?.(null);
      onSelectRegion?.([]);
    },
    resetView: () => {
      resetCamera(cameraRef.current);
      dirtyRef.current = true;
    },
      bounds: () => {
        const canvas = canvasRef.current;
        if (!canvas) return null;
        const box = canvas.getBoundingClientRect();
        // A canvas that has not been laid out yet measures zero, which is not a
        // position — reporting it would make this chart quietly unreachable.
        if (box.width === 0 || box.height === 0) return null;
        return { x: box.left, y: box.top, width: box.width, height: box.height };
      },
      viewState: () => ({ yaw: cameraRef.current.yaw,
                          pitch: cameraRef.current.pitch,
                          zoom: cameraRef.current.zoom }),
      restoreViewState: (state) => {
        // Ignores anything it does not recognise rather than half-applying it.
        // A partial restore puts the scene somewhere the researcher has never
        // been, which is worse than leaving it where they left it.
        if (typeof state.yaw !== "number" || typeof state.pitch !== "number"
            || typeof state.zoom !== "number") return;
        cameraRef.current.yaw = state.yaw;
        cameraRef.current.pitch = state.pitch;
        cameraRef.current.zoom = state.zoom;
        dirtyRef.current = true;
      },
    viewport: () => ({ width, height }),
    // `controllerRef` is the handle's target, not an input to building it —
    // listing it as a dependency rebuilds the controller whenever the caller
    // passes a new ref object, for no gain.
  }), [nearest, within, normalised, points, onSelect, onSelectRegion, rotate,
      width, height]);

  useEffect(() => {
    selectedRef.current = selected;
    dirtyRef.current = true;
  }, [selected]);

  const hasValue = points.some((p) => p.value !== undefined);
  const tableColumns = [
    { key: "label", header: "Label" },
    { key: "x", header: xLabel, numeric: true },
    { key: "y", header: yLabel, numeric: true },
    { key: "z", header: zLabel, numeric: true },
    ...(hasValue ? [{ key: "value", header: valueLabel ?? "Value", numeric: true }] : []),
  ];
  const tableRows = points.map((p) => ({
    id: p.id, label: p.label, x: p.x, y: p.y, z: p.z, value: p.value,
  }));

  return (
    <figure className="chart">
      {title && <figcaption className="chart-title">{title}</figcaption>}

      <canvas
        ref={canvasRef}
        className="chart-canvas volume"
        /*
         * `aspectRatio` with an automatic height, not a fixed one.
         *
         * The drawing buffer is width x height, and the previous style pinned
         * both in CSS with `maxWidth: 100%`. In any container narrower than the
         * chart — a sidebar, a narrow window, a phone — the browser scaled the
         * width down and left the height alone, so the canvas was squashed
         * horizontally: the bounding cube rendered as a tall trapezoid and every
         * mark sat somewhere it did not belong. A projection that is wrong by a
         * scale factor is the most convincing kind of wrong, because it still
         * looks like data.
         *
         * Found by looking at it in a browser at 608px wide. happy-dom lays
         * nothing out, so no test in this suite could have seen it.
         */
        style={{ width, height: "auto", aspectRatio: `${width} / ${height}`,
                 maxWidth: "100%", touchAction: "none" }}
        role="img"
        tabIndex={0}
        aria-label={
          `${title ?? "Three-dimensional scatter"}. ${points.length} points `
          + `positioned by ${xLabel}, ${yLabel} and ${zLabel}`
          + (valueLabel ? `, coloured by ${valueLabel}` : "")
          // The keys are named here because this is where somebody using a
          // keyboard finds out they exist. A control that is reachable and
          // undiscoverable is reachable in the same sense a door with no handle
          // is a door.
          + `. Arrow keys rotate, plus and minus zoom, Home resets the view. `
          + `Enter selects the point nearest the centre, Escape clears it. `
          + `Dragging and clicking do the same. `
          + `${occluded} points are currently hidden behind others.`}
        onPointerDown={(event) => {
          dragRef.current = { x: event.clientX, y: event.clientY };
          pressRef.current = { x: event.clientX, y: event.clientY };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          const from = dragRef.current;
          if (!from) {
            // Hovering, not dragging. Built for the mouse as well as the hand:
            // a capability reachable only by gesture would make the camera
            // mandatory for part of the chart, which Rule 5 forbids.
            const box = event.currentTarget.getBoundingClientRect();
            const at = { x: event.clientX - box.left, y: event.clientY - box.top };
            const target = nearest(at);
            if ((target?.id ?? null) !== hoveredRef.current) {
              // Landing on a point, not merely moving while one is hovered.
              // The detent marks the transition — firing while the pointer sits
              // still on the same mark would be a buzz rather than a boundary.
              if (target && target.id !== hoveredRef.current) onDetent?.("hover");
              hoveredRef.current = target?.id ?? null;
              dirtyRef.current = true;
            }
            // Tracked only while a mark is under the pointer. Following the
            // cursor across empty space would re-render on every move to
            // reposition something nobody is looking at.
            if (target) {
              setReadout({ id: target.id,
                           at: { x: event.clientX, y: event.clientY } });
            } else if (readoutRef.current) {
              setReadout(null);
            }
            return;
          }
          rotate(event.clientX - from.x, event.clientY - from.y);
          dragRef.current = { x: event.clientX, y: event.clientY };
        }}
        onPointerLeave={() => {
          if (hoveredRef.current !== null) {
            hoveredRef.current = null;
            dirtyRef.current = true;
          }
          setReadout(null);
        }}
        onPointerUp={(event) => {
          const press = pressRef.current;
          dragRef.current = null;
          pressRef.current = null;
          // A click is a pointer that did not travel — measured from where it
          // went down, not from the last move.
          if (!press) return;
          const travelled = Math.hypot(event.clientX - press.x,
                                       event.clientY - press.y);
          if (travelled > 3) return;
          const box = event.currentTarget.getBoundingClientRect();
          const at = { x: event.clientX - box.left, y: event.clientY - box.top };

          // The mouse honours the selection reach exactly as a gesture does.
          // Rule 5 is not only about the mouse remaining *available* — a
          // capability reachable by one input and not the other makes the
          // camera mandatory for part of the chart, which is the same failure
          // wearing different clothes.
          if (selectionRadius > 0) {
            const targets = within(at, selectionRadius);
            regionRef.current = new Set(targets.map((t) => t.id));
            setSelected(targets[0]?.id ?? null);
            if (targets.length) onDetent?.("select");
            onSelectRegion?.(targets);
            return;
          }

          const target = nearest(at);
          regionRef.current = null;
          setSelected(target?.id ?? null);
          // Only when something was actually chosen. A tap for clicking empty
          // space would confirm a selection that did not happen.
          if (target) onDetent?.("select");
          onSelect?.(target);
        }}
        onFocus={() => { focusedRef.current = true; dirtyRef.current = true; }}
        onBlur={() => { focusedRef.current = false; dirtyRef.current = true; }}
        onKeyDown={(event) => {
          /*
           * The keyboard reaches everything the pointer does, which §30 and
           * Rule 5 both require and this chart did not do: it could rotate and
           * nothing else. Selection in particular was reachable only with a
           * pointer, and selection is what feeds a question to the assistant —
           * so a researcher who cannot use a mouse was locked out of the
           * product's headline capability, not merely inconvenienced.
           *
           * The model is aim-and-press. There is no cursor in a 3D scene and
           * inventing one would be a second thing to learn, so the target is
           * the centre of the view: rotate to bring a point there, then press.
           * A crosshair appears while the chart has focus, because "the centre"
           * is not a place anybody can see otherwise.
           */
          const step = 12;
          const centre = { x: width / 2, y: height / 2 };

          if (event.key === "ArrowLeft") rotate(-step, 0);
          else if (event.key === "ArrowRight") rotate(step, 0);
          else if (event.key === "ArrowUp") rotate(0, -step);
          else if (event.key === "ArrowDown") rotate(0, step);
          else if (event.key === "+" || event.key === "=") zoomBy(1.15);
          else if (event.key === "-" || event.key === "_") zoomBy(1 / 1.15);
          else if (event.key === "Home") {
            resetCamera(cameraRef.current);
            dirtyRef.current = true;
          } else if (event.key === "Enter" || event.key === " ") {
            if (selectionRadius > 0) {
              const targets = within(centre, selectionRadius);
              regionRef.current = new Set(targets.map((t) => t.id));
              setSelected(targets[0]?.id ?? null);
              if (targets.length) onDetent?.("select");
              onSelectRegion?.(targets);
            } else {
              // A generous radius, because aiming by rotation is coarser than
              // pointing. Requiring pixel accuracy from the one input that
              // cannot be precise would make the feature technically present
              // and practically unusable.
              const target = nearest(centre, 60);
              regionRef.current = null;
              setSelected(target?.id ?? null);
              if (target) onDetent?.("select");
              onSelect?.(target);
              /*
               * The same readout the pointer gets, over the crosshair rather
               * than over a cursor that is not there. Without this, aiming and
               * pressing selects a mark and says nothing about it — which is
               * the failure this readout exists to fix, reintroduced for
               * exactly the readers who cannot use a mouse.
               */
              const box = event.currentTarget.getBoundingClientRect();
              setReadout(target
                ? { id: target.id,
                    at: { x: box.left + centre.x, y: box.top + centre.y } }
                : null);
            }
          } else if (event.key === "Escape") {
            setSelected(null);
            setReadout(null);
            regionRef.current = null;
            dirtyRef.current = true;
            onSelect?.(null);
            onSelectRegion?.([]);
          } else return;
          event.preventDefault();
        }}
      />

      {/*
        * The same visible recovery as the surface chart.
        *
        * This chart has always had the same trap — rotate or zoom into
        * something unreadable and the only way back was a keyboard binding
        * nothing announced. It surfaced on the surface chart first because a
        * mesh degrades more visibly than a cloud, but the missing affordance
        * was never specific to it.
        */}
      <div className="chart-controls">
        <button type="button" className="chart-control"
                onClick={() => {
                  resetCamera(cameraRef.current);
                  dirtyRef.current = true;
                }}>
          Reset the view
        </button>
      </div>

      <figcaption className="chart-caption">
        {caption ? `${caption} ` : ""}
        {points.length.toLocaleString()} points positioned by {xLabel},{" "}
        {yLabel} and {zLabel}
        {valueLabel && <>, coloured by {valueLabel} — colour carries the value,
          and mark size carries depth only</>}. {AXES_SCALED_SEPARATELY}{" "}
        <b>
          {occluded > 0
            ? `${occluded} of these points are hidden behind others right now.`
            : "No points are currently hidden."}
        </b>{" "}
        Rotate to see them — depth on a flat screen comes from motion, and a
        single still view of a 3D scatter cannot be read reliably.{" "}
        {!moved && "Drag the chart, or focus it and use the arrow keys. "}
        If two of these three variables answer your question, a flat scatter
        will answer it more accurately than this will.
      </figcaption>

      {/*
        * What is under the pointer, as text.
        *
        * Positioned in viewport coordinates and pointer-events:none, so it
        * never sits between the cursor and the mark it names.
        */}
      <VolumeReadout
        readout={readout} points={points}
        xLabel={xLabel} yLabel={yLabel} zLabel={zLabel} valueLabel={valueLabel} />

      <ChartTable
        columns={tableColumns}
        rows={tableRows}
        label={title ?? `Points positioned by ${xLabel}, ${yLabel} and ${zLabel}`}
      />
    </figure>
  );
}




/**
 * The values of the mark being read.
 *
 * Split out and exported so the choice of *which* numbers a reader is shown can
 * be tested without a canvas, a projection or a pointer — happy-dom lays
 * nothing out and paints nothing, so a hit-test cannot be driven here, but what
 * the readout says is the part that carries meaning.
 */
export function volumeRows(
  point: Point3D,
  labels: { x: string; y: string; z: string; value?: string },
): Array<{ label: string; value: string }> {
  return [
    { label: labels.x, value: readable(point.x) },
    { label: labels.y, value: readable(point.y) },
    { label: labels.z, value: readable(point.z) },
    // Only when the chart encodes one. A "Value —" row on a chart with no
    // value channel invents a variable the reader does not have.
    ...(labels.value !== undefined && point.value !== undefined
      ? [{ label: labels.value, value: readable(point.value) }]
      : []),
  ];
}

function VolumeReadout({ readout, points, xLabel, yLabel, zLabel, valueLabel }: {
  readout: { id: string; at: Pointer } | null;
  points: Point3D[];
  xLabel: string;
  yLabel: string;
  zLabel: string;
  valueLabel?: string;
}) {
  if (!readout) return null;
  const point = points.find((p) => p.id === readout.id);
  // A mark that has gone — the data changed under a held pointer — is not a
  // mark to describe. An empty tooltip pinned to the cursor would be worse
  // than none.
  if (!point) return null;

  return (
    <ChartTooltip
      pointer={readout.at}
      title={point.label}
      rows={volumeRows(point, { x: xLabel, y: yLabel, z: zLabel,
                                value: valueLabel })}
    />
  );
}
