"use client";

/**
 * P15 — a fitted surface over two predictors.
 *
 * The second chart in this product where three dimensions are the honest choice
 * rather than a decoration, and it is worth saying why, because the argument is
 * the same one that rules 3D *out* almost everywhere else.
 *
 * A bar chart in 3D is worse than a bar chart: perspective makes near bars
 * larger, occlusion hides the ones behind, and reading a value off a rotated
 * axis is measurably less accurate than reading it off a flat one. Nothing is
 * gained, because the data has one dimension and the picture has three.
 *
 * A response surface is different in kind. `z = f(x, y)` *is* a two-dimensional
 * manifold in three-space — the third dimension is in the data, not added to it.
 * Flattened to a contour plot it stays readable but loses the shape a researcher
 * is usually looking for: whether the surface has a ridge, a saddle, a plateau,
 * or a single optimum. That shape is what rotating reveals, and it is the reason
 * §3 lists mathematical surfaces and fitted responses among the visualizations
 * where spatial manipulation genuinely helps.
 *
 * **What this chart owes the reader, and says.**
 *
 *   - **A fitted surface is a model, not data.** It shows what the fit predicts
 *     at points nobody measured. The observations are drawn on top so the two
 *     are never confused, and the caption says how many there were.
 *   - **A surface hides what is behind it**, far more completely than a cloud of
 *     points does. The count of observations currently occluded is reported for
 *     the same reason P13 reports hidden points.
 *   - **Extrapolation is marked.** Cells of the grid with no observation nearby
 *     are drawn faintly, because the smoothest part of a fitted surface is
 *     usually the part with no data under it.
 *   - **The three directions are named and numbered.** For a long time they
 *     were not: this chart took `xLabel`, `yLabel` and `zLabel`, put all three
 *     in the table and the screen-reader label, and drew none of them on the
 *     picture. A response surface whose axes carry no scale shows that there
 *     *is* a ridge and refuses to say where — the reader can see the shape and
 *     cannot report a single number off it, which is the difference between a
 *     figure and an ornament. The frame comes from `axisFurniture`, shared with
 *     every other spatial chart so the three of them cannot drift into three
 *     conventions for reading the same kind of picture.
 */

import { selectionColour } from "@/lib/charts/theme";
import {
  useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState,
} from "react";
import { AXES_SCALED_SEPARATELY, Axes3D, Camera, DEFAULT_CAMERA, DEPTH_RANGE, axisFurniture, drawFurniture, resetCamera, rotateCamera, toCanvas, unitScale, zoomCamera } from "@/lib/charts/scene3d";
import { ScreenPoint, TargetRef, VisualizationController } from "@/lib/spatial/commands";
import { isZoomWheel } from "@/lib/charts/wheel";
import { interpolateYlGnBu } from "d3-scale-chromatic";
import { ChartTable } from "./ChartTable";
import { Colourbar } from "./Colourbar";
import { contourSegments, levelsFor } from "@/lib/charts3d/contour";

export type SurfaceObservation = {
  id: string;
  label: string;
  x: number;
  y: number;
  z: number;
};

export type SurfaceGrid = {
  /** Ascending predictor values along each axis. */
  x: number[];
  y: number[];
  /** `z[yIndex][xIndex]` — the fitted response. `null` where the fit declines. */
  z: Array<Array<number | null>>;
};

/**
 * How far from an observation a cell may sit before it is drawn as extrapolation,
 * as a fraction of the grid's extent.
 *
 * A judgement, stated so it can be argued with. Too small and a sparse but
 * perfectly reasonable design is greyed out everywhere; too large and the
 * warning stops meaning anything.
 */
const SUPPORT_RADIUS = 0.18;

/**
 * The lowest and highest of a list, swept rather than spread.
 *
 * `Math.min(...values)` passes one argument per element and overflows the
 * stack somewhere past a hundred thousand of them — `unitScale` says so in its
 * own body and sweeps for exactly that reason. The colour range here was
 * spread, and a fitted grid is the easy way to reach that size: a 400×400
 * response is 160,000 cells from one call to a model.
 *
 * It is also the one place the axis domains come from, and that is not a
 * convenience. `AxisSpec.min`/`max` must be the same two numbers `unitScale`
 * was given, and a mismatch is undetectable from inside the furniture — a tick
 * reading 40 would simply sit where 45 is, and the reader would read the chart
 * off it. One computation, used by the scaling and by the labels, is what
 * makes that impossible rather than merely unlikely.
 */
function extentOf(values: readonly number[]): [number, number] {
  let lo = Infinity, hi = -Infinity;
  for (const value of values) {
    if (value < lo) lo = value;
    if (value > hi) hi = value;
  }
  return [lo, hi];
}

/**
 * One shared empty list, rather than a fresh `[]` in the parameter default.
 *
 * A default written inline is evaluated on every render, so a caller that
 * draws a surface without observations handed the projection memo a new array
 * each time and the memo — which flattens the grid, spans every cell and
 * builds the colour ramp — recomputed on every render for a picture that had
 * not changed. It also hid a missing `colourBy` dependency further down: the
 * memo could not go stale while it never cached.
 */
const NO_OBSERVATIONS: readonly SurfaceObservation[] = [];

export function Surface({
  grid, colourBy, observations = NO_OBSERVATIONS, controllerRef, onSelect, onDetent,
  xLabel, yLabel, zLabel, title, caption, width = 720, height = 520,
  style = "filled",
}: {
  grid: SurfaceGrid;
  /**
   * How the same numbers are drawn.
   *
   * `"filled"` shades each cell — the shape. `"wireframe"` strokes the cells
   * and fills nothing — the structure, and what a fitted surface's resolution
   * actually is. `"contour"` draws level curves on the surface — where the
   * field crosses a value, which is what you read when the question is "how
   * steep" or "where is the boundary".
   *
   * The catalogue names all three and this component drew one, so a reader
   * asking for a contour plot got a filled surface and no indication of it.
   */
  style?: "filled" | "wireframe" | "contour";
  /** What was actually measured. Drawn over the fit, never merged into it. */
  /**
   * A fourth variable, painted onto the surface instead of its height.
   *
   * Height already carries `z`, so colouring by it says the same thing twice
   * and the picture has three dimensions of data in it, not four. A separate
   * grid — dispersion across a strike/maturity surface, uncertainty across a
   * fitted response, residual across a model — is the case where a colour
   * earns its place, and it must be the same shape as `grid.z` or it is
   * describing a different surface.
   *
   * The legend is not optional when this is given. A colour scale with no key
   * is a picture that looks quantitative and cannot be read.
   */
  colourBy?: {
    values: Array<Array<number | null>>;
    label: string;
    unit?: string;
  };
  observations?: readonly SurfaceObservation[];
  controllerRef?: React.RefObject<VisualizationController | null>;
  onSelect?: (target: TargetRef | null) => void;
  onDetent?: (moment: "hover" | "select") => void;
  xLabel: string;
  yLabel: string;
  zLabel: string;
  title?: string;
  caption?: string;
  width?: number;
  height?: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const cameraRef = useRef<Camera>({ ...DEFAULT_CAMERA });
  const dragRef = useRef<{ x: number; y: number } | null>(null);
  const pressRef = useRef<{ x: number; y: number } | null>(null);
  const dirtyRef = useRef(true);
  const focusedRef = useRef(false);
  const hoveredRef = useRef<string | null>(null);
  const selectedRef = useRef<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [hiddenCount, setHiddenCount] = useState(0);
  const hiddenRef = useRef(0);
  const settleRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  /** The fit and the observations placed in one shared unit cube. */
  const scene = useMemo(() => {
    const zValues = grid.z.flat().filter((v): v is number => v !== null);
    const allZ = [...zValues, ...observations.map((o) => o.z)];
    /*
     * The height is scaled over the fit *and* the observations together, and
     * the axis has to be labelled over the same two numbers.
     *
     * Easy to get wrong and impossible to see: labelling the height axis with
     * the grid's own range would be right whenever every observation happens
     * to fall inside the fitted surface and silently wrong the moment one does
     * not — which is the case a reader most wants to look at, a measurement
     * the model did not reach.
     */
    const heights = allZ.length ? allZ : [0, 1];
    const sx = unitScale(grid.x);
    const sy = unitScale(grid.y);
    const sz = unitScale(heights);

    /*
     * What the colour means, and it is not always the height.
     *
     * A fourth variable is used when one is supplied *and* it describes this
     * surface — same number of rows and columns. A mismatched grid is refused
     * rather than stretched: painting one surface with another's values
     * produces a picture that is confidently wrong everywhere, and no reader
     * could see it.
     */
    const shapeMatches = !!colourBy
      && colourBy.values.length === grid.z.length
      && colourBy.values.every((row, i) => row.length === grid.z[i].length);

    const fourth = shapeMatches
      ? colourBy!.values.flat().filter((v): v is number => v !== null)
      : [];
    const paintedBy: number[] = fourth.length ? fourth : allZ;

    const [lo, hi] = extentOf(paintedBy.length ? paintedBy : [0, 1]);
    const ramp = (value: number) =>
      interpolateYlGnBu(hi === lo ? 0.5 : (value - lo) / (hi - lo));

    // Indexed by cell when a fourth variable is painted, by height otherwise.
    const valueAt = (row: number, column: number, z: number): number =>
      (fourth.length ? (colourBy!.values[row]?.[column] ?? z) : z);
    const colourOf = (z: number) => ramp(z);

    // Support is measured in the normalised square, so it does not depend on
    // whichever units the two predictors happen to be in.
    const support = observations.map((o) => ({ x: sx(o.x), y: sy(o.y) }));
    const supported = (nx: number, ny: number) =>
      support.length === 0
      || support.some((s) => Math.hypot(s.x - nx, s.y - ny) <= SUPPORT_RADIUS * 2);

    return {
      sx, sy, sz, colourOf, supported, valueAt,
      /*
       * The three data domains, kept beside the three scalings that consumed
       * them. `x` and `y` are the predictors; `height` is what `sz` was built
       * over, which is the fit and the observations together.
       */
      domains: {
        x: extentOf(grid.x), y: extentOf(grid.y), height: extentOf(heights),
      },
      /*
       * What the colour stands for, or nothing when it stands for the height
       * — in which case the z axis is already the key and a second one would
       * repeat it.
       */
      legend: fourth.length && colourBy
        ? { label: colourBy.label, unit: colourBy.unit,
            low: lo, high: hi,
            /*
             * The ramp itself, handed over rather than sampled into stops.
             *
             * The key and the surface have to be the same scale or the key is
             * a picture of a different chart, and five hard-coded stops was
             * one copy of it: change the interpolator here and the bar keeps
             * painting the old one until somebody notices. `Colourbar` asks
             * for the function and samples it, so there is nothing to keep in
             * step.
             */
            ramp: interpolateYlGnBu }
        : null,
      /*
       * A colour grid that does not describe this surface. Reported so the
       * caption can say so: silently falling back to height would draw a
       * different picture from the one asked for, and look correct.
       */
      colourMismatch: !!colourBy && !shapeMatches,
      points: observations.map((o) => ({
        id: o.id, label: o.label, datum: o,
        x: sx(o.x), y: sz(o.z), z: sy(o.y),
      })),
    };
  /*
   * `colourBy` belongs here. It was omitted, and everything above that reads
   * it — the fourth variable, the legend, the shape-mismatch flag — then went
   * stale whenever a caller changed the colour grid while keeping the same
   * `grid` and `observations` objects: the surface kept the previous
   * colouring, which is the "confidently wrong everywhere" picture the
   * mismatch check a few lines up exists to refuse.
   */
  }, [grid, observations, colourBy]);

  /**
   * The three directions, named and given their real numbers.
   *
   * **Keyed by scene axis, not by the caller's variable names**, and that is
   * the one thing worth checking twice here. Scene `y` runs up the screen and
   * scene `z` runs into it, while this chart's own `z` is the response and its
   * `y` is the second predictor — so the height goes on scene `y` and
   * `yLabel` goes on scene `z`. The projection above already does exactly this
   * (`y: sz(o.z), z: sy(o.y)`), and the two have to agree: transposing them
   * writes the right numbers along the wrong directions, and nothing about the
   * picture looks broken afterwards.
   *
   * Units are not written here because the component has no prop carrying one.
   * `xLabel`/`yLabel`/`zLabel` are strings a caller composes, so a caller with
   * a unit already puts it in the label; inventing one from the numbers would
   * be a measurement this chart made up.
   */
  const axes = useMemo<Axes3D>(() => ({
    x: { label: xLabel,
         min: scene.domains.x[0], max: scene.domains.x[1] },
    y: { label: zLabel,
         min: scene.domains.height[0], max: scene.domains.height[1] },
    z: { label: yLabel,
         min: scene.domains.y[0], max: scene.domains.y[1] },
  }), [scene, xLabel, yLabel, zLabel]);

  /**
   * The mesh as quads, each with its own depth.
   *
   * Quads rather than a single path: a surface has to be drawn back to front or
   * the far side paints over the near side, and that sort has to happen per
   * cell. A wireframe would avoid the sort and also avoid conveying the shape,
   * which is the only reason this chart exists.
   */
  const cells = useMemo(() => {
    const out: Array<{
      corners: Array<{ x: number; y: number; z: number }>;
      /** The value this cell's colour stands for — its height, or a fourth
       *  variable when one was supplied. */
      paint: number;
      z: number; supported: boolean;
    }> = [];
    for (let j = 0; j + 1 < grid.y.length; j += 1) {
      for (let i = 0; i + 1 < grid.x.length; i += 1) {
        const quad = [[j, i], [j, i + 1], [j + 1, i + 1], [j + 1, i]] as const;
        const heights = quad.map(([qj, qi]) => grid.z[qj]?.[qi]);
        if (heights.some((h) => h === null || h === undefined)) continue;

        const corners = quad.map(([qj, qi], k) => ({
          x: scene.sx(grid.x[qi]),
          y: scene.sz(heights[k] as number),
          z: scene.sy(grid.y[qj]),
        }));
        const mean = (heights as number[]).reduce((a, b) => a + b, 0) / 4;
        const nx = (scene.sx(grid.x[i]) + scene.sx(grid.x[i + 1])) / 2;
        const ny = (scene.sy(grid.y[j]) + scene.sy(grid.y[j + 1])) / 2;
        // The cell carries what its colour means, which is its height only
        // when no fourth variable was given. Read here rather than at paint
        // time so the sort and the fill cannot disagree about a cell.
        out.push({ corners, z: mean, paint: scene.valueAt(j, i, mean),
                   supported: scene.supported(nx, ny) });
      }
    }
    return out;
  }, [grid, scene]);

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

    /*
     * The frame's colours, asked of the canvas once a frame.
     *
     * A canvas is painted with literal values and cannot inherit a token the
     * way the rest of the interface does — but it can be asked what it
     * inherited, which is the only way a figure follows a reader who switches
     * theme with it on screen. Read here rather than hoisted into state
     * because the answer changes without React being told.
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
     * Recomputed every frame, never cached.
     *
     * Which walls face away and which edge each axis is labelled along both
     * change continuously as the scene turns; a cached answer is a wall
     * painted over the surface for half of a rotation.
     */
    const furniture = axisFurniture(axes, camera, width, height);
    // The far walls and their gridlines go under the mesh. A pane painted
    // after it would hide the thing the pane exists to measure.
    drawFurniture(context, furniture, colours, 1, "behind");

    // Painter's algorithm over the cells: far to near, so a near cell covers a
    // far one and the surface reads as solid rather than as a tangle.
    const painted = cells
      .map((cell) => {
        const screen = cell.corners.map((c) => toCanvas(c, camera, width, height));
        const depth = screen.reduce((sum, s) => sum + s.depth, 0) / screen.length;
        return { ...cell, screen, depth };
      })
      .sort((a, b) => a.depth - b.depth);

    for (const cell of painted) {
      context.beginPath();
      context.moveTo(cell.screen[0].x, cell.screen[0].y);
      for (const corner of cell.screen.slice(1)) context.lineTo(corner.x, corner.y);
      context.closePath();

      if (style === "filled") {
        context.fillStyle = scene.colourOf(cell.paint);
        // Unsupported cells are drawn faintly. The smoothest part of a fitted
        // surface is usually the part with no data under it, and a reader has
        // no way to tell that from the shape alone.
        context.globalAlpha = cell.supported ? 0.92 : 0.28;
        context.fill();
      } else if (style === "contour") {
        // A wash under the curves, or the levels float with nothing to read
        // them against — but faint, because the curves are the chart.
        context.fillStyle = scene.colourOf(cell.paint);
        context.globalAlpha = cell.supported ? 0.16 : 0.06;
        context.fill();
      }

      if (style === "wireframe") {
        // The cell edges carry the value, since nothing is filled.
        context.globalAlpha = cell.supported ? 0.95 : 0.35;
        context.strokeStyle = scene.colourOf(cell.paint);
        context.lineWidth = 0.9;
      } else {
        context.globalAlpha = cell.supported ? 0.5 : 0.2;
        context.strokeStyle = "rgba(20,30,50,0.55)";
        context.lineWidth = 0.5;
      }
      if (style !== "contour") context.stroke();
    }
    context.globalAlpha = 1;

    if (style === "contour") {
      /*
       * The level curves, on the surface rather than flattened beneath it: a
       * contour sits at the height it represents, and drawing it flat would
       * put the line somewhere the field never is.
       */
      const heights = grid.z.flat()
        .filter((v): v is number => v !== null && Number.isFinite(v));
      const levels = levelsFor(Math.min(...heights), Math.max(...heights));
      context.lineWidth = 1.4;
      for (const segment of contourSegments(grid, levels)) {
        // The same axis order the observations use: screen y carries the
        // height, screen z the second predictor.
        const place = (p: { x: number; y: number; z: number }) => ({
          x: scene.sx(p.x), y: scene.sz(p.z), z: scene.sy(p.y),
        });
        const a = toCanvas(place(segment.a), camera, width, height);
        const b = toCanvas(place(segment.b), camera, width, height);
        context.beginPath();
        context.moveTo(a.x, a.y);
        context.lineTo(b.x, b.y);
        context.strokeStyle = scene.colourOf(segment.level);
        context.stroke();
      }
    }

    // Observations last, over the fit, and never merged into it: the surface is
    // a model and these are the measurements it was fitted to.
    let hidden = 0;
    for (const point of scene.points) {
      const at = toCanvas(point, camera, width, height);
      const radius = 3.2 * (1 + (at.scale - 1) * DEPTH_RANGE);

      // Behind the surface if a cell nearer the viewer covers this position.
      const covered = painted.some((cell) =>
        cell.depth > at.depth + 0.02 && insideQuad(at, cell.screen));
      if (covered) { hidden += 1; continue; }

      context.beginPath();
      context.arc(at.x, at.y, radius, 0, Math.PI * 2);
      context.fillStyle = "#12203a";
      context.fill();
      context.lineWidth = 1;
      context.strokeStyle = "rgba(255,255,255,0.85)";
      context.stroke();

      if (point.id === selectedRef.current || point.id === hoveredRef.current) {
        context.beginPath();
        context.arc(at.x, at.y, radius + 4, 0, Math.PI * 2);
        // The theme's selection colour: the hard-coded blue was 2.3:1 on the
        // dark canvas. Hover is the same colour, fainter.
        context.save();
        context.strokeStyle = selection;
        context.globalAlpha = point.id === selectedRef.current ? 1 : 0.55;
        context.lineWidth = point.id === selectedRef.current ? 2 : 1.25;
        context.stroke();
        context.restore();
      }
    }

    /*
     * The axis lines, the ticks and the titles, over everything.
     *
     * A filled surface is opaque, so a label drawn before it is a label the
     * reader never sees — the second pass is what makes the numbers legible on
     * the exact chart that most needs them.
     */
    drawFurniture(context, furniture, colours, 1, "front");

    if (focusedRef.current) {
      const cx = width / 2, cy = height / 2;
      // The keyboard's aim, in the theme's selection colour: the fixed blue
      // at 55% was all but invisible on the dark canvas.
      context.strokeStyle = selection;
      context.lineWidth = 1.25;
      context.beginPath();
      context.moveTo(cx - 7, cy); context.lineTo(cx - 2, cy);
      context.moveTo(cx + 2, cy); context.lineTo(cx + 7, cy);
      context.moveTo(cx, cy - 7); context.lineTo(cx, cy - 2);
      context.moveTo(cx, cy + 2); context.lineTo(cx, cy + 7);
      context.stroke();
    }

    hiddenRef.current = hidden;
    if (settleRef.current !== null) clearTimeout(settleRef.current);
    // Published once the view settles, for the same reason P13 does it: a count
    // that changes eight times a second while the scene turns is unreadable and
    // costs a React render per painted frame.
    settleRef.current = setTimeout(() => {
      settleRef.current = null;
      setHiddenCount(hiddenRef.current);
    }, 120);
    // `grid` and `style` are read by the contour and wireframe branches, so a
    // change to either has to repaint — without them a style switch left the
    // previous drawing on screen. `axes` is here for the same reason: the
    // frame is what carries the labels, and a renamed axis that does not
    // repaint is a chart labelled with the previous variable.
  }, [cells, scene, width, height, grid, style, axes]);

  useEffect(() => {
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
    let frame = 0;
    const tick = () => {
      if (dirtyRef.current) { dirtyRef.current = false; draw(); }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      if (settleRef.current !== null) clearTimeout(settleRef.current);
    };
  }, [draw]);

  useEffect(() => { dirtyRef.current = true; }, [cells]);
  useEffect(() => { selectedRef.current = selected; dirtyRef.current = true; },
            [selected]);

  const nearest = useCallback((at: ScreenPoint, radius = 28): TargetRef | null => {
    const camera = cameraRef.current;
    let best: { target: TargetRef; d: number } | null = null;
    for (const point of scene.points) {
      const p = toCanvas(point, camera, width, height);
      const d = Math.hypot(p.x - at.x, p.y - at.y);
      if (d <= radius && (best === null || d < best.d)) {
        best = { target: { id: point.id, label: point.label, datum: point.datum }, d };
      }
    }
    return best?.target ?? null;
  }, [scene, width, height]);

  useImperativeHandle(controllerRef, (): VisualizationController => ({
    rotate: (dx, dy) => { rotateCamera(cameraRef.current, dx, dy); dirtyRef.current = true; },
    zoom: (factor) => { zoomCamera(cameraRef.current, factor); dirtyRef.current = true; },
    pan: () => {},
    hover: (at) => {
      const target = nearest(at);
      hoveredRef.current = target?.id ?? null;
      dirtyRef.current = true;
      return target;
    },
    select: (at) => {
      const target = nearest(at);
      setSelected(target?.id ?? null);
      onSelect?.(target);
      return target;
    },
    withinPolygon: (polygon) => {
      // Exact, like the scatter's: every observation tested against the region
      // rather than the region sampled for observations.
      const camera = cameraRef.current;
      return scene.points
        .map((point) => ({ point, at: toCanvas(point, camera, width, height) }))
        .filter(({ at }) => insideQuad(at, polygon))
        .map(({ point }) =>
          ({ id: point.id, label: point.label, datum: point.datum }));
    },
    selectRegion: (at, radius) => {
      const camera = cameraRef.current;
      const found = scene.points
        .map((point) => ({ point, at: toCanvas(point, camera, width, height) }))
        .map(({ point, at: p }) => ({ point, d: Math.hypot(p.x - at.x, p.y - at.y) }))
        .filter(({ d }) => d <= radius)
        .sort((a, b) => a.d - b.d)
        .map(({ point }) =>
          ({ id: point.id, label: point.label, datum: point.datum }));
      setSelected(found[0]?.id ?? null);
      return found;
    },
    focus: (objectId) => { setSelected(objectId); dirtyRef.current = true; },
    deselect: () => { setSelected(null); dirtyRef.current = true; onSelect?.(null); },
    resetView: () => { resetCamera(cameraRef.current); dirtyRef.current = true; },
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
  }), [nearest, onSelect, scene, width, height]);

  const rows = observations.map((o) => ({
    label: o.label, x: o.x, y: o.y, z: o.z,
  }));
  const columns = [
    { key: "label", header: "Observation" },
    { key: "x", header: xLabel, numeric: true },
    { key: "y", header: yLabel, numeric: true },
    { key: "z", header: zLabel, numeric: true },
  ];

  return (
    <figure className="chart">
      {title && <figcaption className="chart-title">{title}</figcaption>}
      {/*
        * The canvas and its key, side by side.
        *
        * Beside rather than below because a colour scale is read by carrying
        * a patch of the picture to the bar and back; a bar under the caption
        * makes that a scroll. Inline styles rather than a class, for the
        * reason `Colourbar` itself is styled inline: the two are one piece of
        * furniture, and splitting it across a stylesheet the component does
        * not own is how a legend ends up 300px wide beside a 720px canvas.
        */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 14 }}>
        <canvas
          ref={canvasRef}
          className="chart-canvas volume"
          style={{ width, height: "auto", aspectRatio: `${width} / ${height}`,
                   maxWidth: "100%", touchAction: "none" }}
          role="img"
          tabIndex={0}
          aria-label={
            `${title ?? "Fitted surface"}. ${zLabel} predicted from ${xLabel} and `
            + `${yLabel}, over ${observations.length} observations. `
            + `Arrow keys rotate, plus and minus zoom, Home resets the view. `
            + `Enter selects the observation nearest the centre, Escape clears it.`}
          onFocus={() => { focusedRef.current = true; dirtyRef.current = true; }}
          onBlur={() => { focusedRef.current = false; dirtyRef.current = true; }}
          onPointerDown={(event) => {
            dragRef.current = { x: event.clientX, y: event.clientY };
            pressRef.current = { x: event.clientX, y: event.clientY };
            event.currentTarget.setPointerCapture(event.pointerId);
          }}
          onPointerMove={(event) => {
            const from = dragRef.current;
            const box = event.currentTarget.getBoundingClientRect();
            const at = { x: event.clientX - box.left, y: event.clientY - box.top };
            if (!from) {
              const target = nearest(at);
              if (target && target.id !== hoveredRef.current) onDetent?.("hover");
              if ((target?.id ?? null) !== hoveredRef.current) {
                hoveredRef.current = target?.id ?? null;
                dirtyRef.current = true;
              }
              return;
            }
            rotateCamera(cameraRef.current, event.clientX - from.x,
                         event.clientY - from.y);
            dirtyRef.current = true;
            dragRef.current = { x: event.clientX, y: event.clientY };
          }}
          onPointerUp={(event) => {
            const press = pressRef.current;
            dragRef.current = null;
            pressRef.current = null;
            if (!press) return;
            if (Math.hypot(event.clientX - press.x, event.clientY - press.y) > 3) return;
            const box = event.currentTarget.getBoundingClientRect();
            const target = nearest({ x: event.clientX - box.left,
                                     y: event.clientY - box.top });
            setSelected(target?.id ?? null);
            if (target) onDetent?.("select");
            onSelect?.(target);
          }}
          onWheel={(event) => {
            // The same gate as every other chart: a plain wheel belongs to the
            // page. One page can stack several of these, and two different wheel
            // behaviours among them would be worse than either.
            if (!isZoomWheel(event)) return;
            event.preventDefault();
            zoomCamera(cameraRef.current, event.deltaY < 0 ? 1.08 : 1 / 1.08);
            dirtyRef.current = true;
          }}
          onKeyDown={(event) => {
            const step = 12;
            if (event.key === "ArrowLeft") rotateCamera(cameraRef.current, -step, 0);
            else if (event.key === "ArrowRight") rotateCamera(cameraRef.current, step, 0);
            else if (event.key === "ArrowUp") rotateCamera(cameraRef.current, 0, -step);
            else if (event.key === "ArrowDown") rotateCamera(cameraRef.current, 0, step);
            else if (event.key === "+" || event.key === "=") zoomCamera(cameraRef.current, 1.15);
            else if (event.key === "-" || event.key === "_") zoomCamera(cameraRef.current, 1 / 1.15);
            else if (event.key === "Home") resetCamera(cameraRef.current);
            else if (event.key === "Enter" || event.key === " ") {
              const target = nearest({ x: width / 2, y: height / 2 }, 60);
              setSelected(target?.id ?? null);
              if (target) onDetent?.("select");
              onSelect?.(target);
            } else if (event.key === "Escape") {
              setSelected(null);
              onSelect?.(null);
            } else return;
            dirtyRef.current = true;
            event.preventDefault();
          }}
        />

        {/*
          * The key to the colour, and it appears only when the colour carries
          * something the axes do not. Painting by height and then drawing a
          * scale for it would key the picture to itself — the z axis is already
          * that key, and now that it carries ticks it is a better one.
          *
          * In the DOM rather than on the canvas: a bar painted into the scene
          * cannot be read by anything that cannot see, does not follow the
          * theme, and does not grow with the reader's text size.
          *
          * `Colourbar` rather than the three spans that used to live here. Those
          * gave a reader the two ends and nothing between them, so a patch of
          * colour from the middle of the surface could only be guessed at —
          * YlGnBu is not linear in hue, and guessing is exactly what a key is
          * for avoiding. The shared component labels the scale the whole way up
          * and formats its numbers with the same formatter the axis ticks use,
          * so one figure does not carry two conventions for a number.
          */}
        {scene.legend && (
          <Colourbar
            ramp={scene.legend.ramp}
            min={scene.legend.low} max={scene.legend.high}
            label={scene.legend.label} unit={scene.legend.unit} />
        )}
      </div>

      {/*
        * A visible way back.
        *
        * `Home` already reset the view and nothing on screen said so, which is
        * half of what "I can't do anything with this graph" meant: once a reader
        * has rotated or zoomed into something unreadable, an undiscoverable
        * keyboard binding is the same as no recovery at all. A button is also
        * the only route for somebody using a pointer and never a keyboard.
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
        {caption}{" "}
        {/*
          * "A fit, not data" is the right warning for a fitted response
          * surface, and it was printed whether or not anything had been
          * fitted. A surface drawn from a function — or from a grid a caller
          * simply had — was announced as a model of measurements that do not
          * exist, alongside "the 0 observations it was fitted to are drawn on
          * top". The warning is the important half, so it stays exactly as it
          * was wherever there is a fit to warn about.
          */}
        {observations.length > 0 ? (
          <>
            <strong>This surface is a fit, not data.</strong> It shows what the
            model predicts at points nobody measured; the {observations.length}{" "}
            observations it was fitted to are drawn on top.{" "}
            {hiddenCount > 0
              ? <><strong>{hiddenCount} of them are behind the surface right now.</strong>{" "}
                  Rotate to see them.</>
              : "No observations are hidden behind the surface right now."}{" "}
          </>
        ) : (
          <>
            No observations are drawn on this surface, so it is the height field
            it was given rather than a model fitted to measurements.{" "}
          </>
        )}
        {/*
          * Only where there are observations to be near.
          *
          * This sentence used to sit outside the branch above, so a surface
          * with no observations at all said "faint cells have no observation
          * near them" — vacuously true of every cell, and read as though some
          * cells did have data under them. A caption that describes a fit is
          * misleading on a height field that was never fitted to anything.
          */}
        {observations.length > 0 && (
          <>
            Faint cells have no observation near them — the smoothest part of a
            fitted surface is usually the part with no data under it.{" "}
          </>
        )}
        {AXES_SCALED_SEPARATELY}
      </figcaption>

      <ChartTable
        rows={rows}
        columns={columns}
        label="the observations this surface was fitted to"
        note="The surface itself is a fit and has no rows: it is defined
              everywhere, including where nothing was measured." />
    </figure>
  );
}

/** Whether a point falls inside a projected quad, by the winding test. */
function insideQuad(point: { x: number; y: number },
                    quad: Array<{ x: number; y: number }>): boolean {
  let inside = false;
  for (let i = 0, j = quad.length - 1; i < quad.length; j = i, i += 1) {
    const a = quad[i];
    const b = quad[j];
    if ((a.y > point.y) !== (b.y > point.y)
        && point.x < ((b.x - a.x) * (point.y - a.y)) / (b.y - a.y) + a.x) {
      inside = !inside;
    }
  }
  return inside;
}
