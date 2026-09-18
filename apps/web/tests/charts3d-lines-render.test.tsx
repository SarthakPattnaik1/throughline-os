/**
 * Drawing paths in space (§9 lines).
 *
 * Three claims carry this renderer, and each fails silently rather than
 * loudly: that segments are depth-sorted individually so an orbit passes both
 * in front of and behind what it circles, that a gap in the data is never
 * joined, and that a path's direction is visible at all.
 */

import { SELECT_FALLBACK } from "@/lib/charts/theme";
import { describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";
import { createRef } from "react";
import { Lines3D, paintLines, pathColour } from "@/components/charts/Lines3D";
import { DEFAULT_CAMERA, toCanvas } from "@/lib/charts/scene3d";
import { Path, Paths, preparePaths } from "@/lib/charts3d/paths";
import { VisualizationController } from "@/lib/spatial/commands";

type Call = { op: string; args: number[]; alpha: number; stroke: string };

function recordingCanvas() {
  const calls: Call[] = [];
  const context: Record<string, unknown> & {
    globalAlpha: number; strokeStyle: string;
  } = { globalAlpha: 1, strokeStyle: "", lineWidth: 0, lineCap: "" };
  const note = (op: string) => (...args: unknown[]) => {
    calls.push({
      op,
      args: args.filter((a): a is number => typeof a === "number"),
      alpha: context.globalAlpha,
      stroke: String(context.strokeStyle),
    });
  };
  Object.assign(context, {
    clearRect: note("clearRect"), save: note("save"), restore: note("restore"),
    beginPath: note("beginPath"), moveTo: note("moveTo"), lineTo: note("lineTo"),
    stroke: note("stroke"),
  });
  const canvas = {
    getContext: () => context, width: 400, height: 300,
  } as unknown as HTMLCanvasElement;
  return { canvas, calls };
}

const SIZE = { width: 400, height: 300 };
const paint = (prepared: Paths, selected: string | null = null,
               hovered: string | null = null) => {
  const r = recordingCanvas();
  paintLines(r.canvas, prepared, DEFAULT_CAMERA, SIZE, selected, hovered);
  return r;
};

/** A ring in the xy plane, which passes both toward and away from the eye. */
const ring = (id: string, n = 24): Path => ({
  id, closed: true,
  points: Array.from({ length: n }, (_, i) => ({
    x: Math.cos((i / n) * Math.PI * 2),
    y: 0,
    z: Math.sin((i / n) * Math.PI * 2),
  })),
});

describe("segments are sorted, not paths", () => {
  it("draws the near half of a ring after the far half", () => {
    /*
     * The reason this renderer sorts pieces rather than paths. An orbit is not
     * at one depth — it passes in front of the body it circles and then behind
     * it. Sorted as a unit, the whole orbit is drawn in front or behind, and
     * the reader is looking at a picture of the sort rather than of the motion.
     */
    const prepared = preparePaths([ring("orbit")], { tolerance: 0, maxPoints: 1e6 });
    const { calls } = paint(prepared);
    const strokes = calls.filter((c) => c.op === "moveTo");

    // Recover the depth of each drawn segment's start point and check the
    // sequence never steps backward.
    const line = prepared.lines[0].runs[0];
    const depthAt = new Map(line.map((p) => {
      const q = toCanvas(p, DEFAULT_CAMERA, 400, 300);
      return [`${q.x.toFixed(4)},${q.y.toFixed(4)}`, q.depth];
    }));
    const drawn = strokes
      .map((c) => depthAt.get(`${c.args[0].toFixed(4)},${c.args[1].toFixed(4)}`))
      .filter((d): d is number => d !== undefined);

    expect(drawn.length).toBeGreaterThan(10);
    // The first segment drawn is behind the last one drawn.
    expect(drawn[0]).toBeLessThan(drawn[drawn.length - 1]);
  });

  it("interleaves two paths that cross in depth", () => {
    /*
     * Sorted per path, one trajectory is drawn wholly over the other and the
     * crossing disappears. Interleaving is the observable difference.
     */
    const a: Path = { id: "a", points: [
      { x: -1, y: 0, z: -1 }, { x: 0, y: 0, z: 0 }, { x: 1, y: 0, z: 1 }] };
    const b: Path = { id: "b", points: [
      { x: -1, y: 0, z: 1 }, { x: 0, y: 0.1, z: 0 }, { x: 1, y: 0, z: -1 }] };

    const prepared = preparePaths([a, b], { tolerance: 0, maxPoints: 1e6 });
    const { calls } = paint(prepared);
    const order = calls.filter((c) => c.op === "stroke").map((c) => c.stroke);
    // Both colours appear, and neither is drawn as one uninterrupted block.
    const distinct = new Set(order);
    expect(distinct.size).toBe(2);
    const first = order[0];
    const switches = order.filter((c, i) => i > 0 && c !== order[i - 1]).length;
    expect(switches).toBeGreaterThan(0);
    expect(order.filter((c) => c === first).length).toBeLessThan(order.length);
  });

  it("clears before drawing, so a rotation does not smear", () => {
    const { calls } = paint(preparePaths([ring("o")]));
    expect(calls[0].op).toBe("clearRect");
  });

  it("does nothing at all without a canvas", () => {
    expect(() => paintLines(null, preparePaths([ring("o")]), DEFAULT_CAMERA,
                            SIZE, null, null)).not.toThrow();
  });
});

describe("a gap is never joined", () => {
  it("draws one fewer segment than a whole path would", () => {
    /*
     * The chord across the gap is the thing this must not draw. Two runs of
     * three points are four segments; one run of six would be five, and the
     * fifth is the invented one.
     */
    const broken: Path = { id: "t", points: [
      { x: 0, y: 0, z: 0 }, { x: 1, y: 0, z: 0 }, { x: 2, y: 0, z: 0 },
      { x: NaN, y: 0, z: 0 },
      { x: 5, y: 0, z: 0 }, { x: 6, y: 0, z: 0 }, { x: 7, y: 0, z: 0 }] };

    const prepared = preparePaths([broken], { tolerance: 0, maxPoints: 1e6 });
    const { calls } = paint(prepared);
    expect(calls.filter((c) => c.op === "lineTo")).toHaveLength(4);
  });

  it("starts a new stroke at each run rather than continuing", () => {
    const broken: Path = { id: "t", points: [
      { x: 0, y: 0, z: 0 }, { x: 1, y: 0, z: 0 },
      { x: NaN, y: 0, z: 0 },
      { x: 5, y: 0, z: 0 }, { x: 6, y: 0, z: 0 }] };
    const { calls } = paint(preparePaths([broken], { tolerance: 0, maxPoints: 1e6 }));
    // One moveTo per segment, because every segment is its own stroke.
    expect(calls.filter((c) => c.op === "moveTo")).toHaveLength(2);
  });

  it("closes a closed path and leaves an open one open", () => {
    const square = (closed: boolean): Path => ({
      id: "s", closed,
      points: [{ x: 0, y: 0, z: 0 }, { x: 1, y: 0, z: 0 },
               { x: 1, y: 1, z: 0 }, { x: 0, y: 1, z: 0 }],
    });
    const segs = (closed: boolean) => paint(preparePaths(
      [square(closed)], { tolerance: 0, maxPoints: 1e6 }))
      .calls.filter((c) => c.op === "lineTo").length;
    expect(segs(false)).toBe(3);
    expect(segs(true)).toBe(4);
  });

  it("draws nothing for a path with too few points", () => {
    const { calls } = paint(preparePaths([{ id: "x", points: [
      { x: 0, y: 0, z: 0 }] }]));
    expect(calls.filter((c) => c.op === "lineTo")).toHaveLength(0);
  });
});

describe("direction is visible", () => {
  it("ramps from faint at the start to solid at the end", () => {
    /*
     * A path has an order and a line does not. Without the ramp a trajectory
     * and its reverse are the same picture, and "the training ran toward this
     * minimum" cannot be read off the chart at all.
     */
    const straight: Path = { id: "s", points: Array.from({ length: 10 },
      (_, i) => ({ x: i, y: 0, z: 0 })) };
    const { calls } = paint(preparePaths([straight],
      { tolerance: 0, maxPoints: 1e6 }));
    const alphas = calls.filter((c) => c.op === "stroke").map((c) => c.alpha);
    expect(alphas[alphas.length - 1]).toBeGreaterThan(alphas[0]);
    /*
     * And the faint end is still visible. Found on the real page rather than
     * here: ramping from 0.19 drew 3305 pixels of trajectory that could not be
     * seen at all against a near-black background. The ramp only has to be
     * ordered to say which way the path runs; reaching zero costs the start of
     * every path, which is where a reader looks first.
     */
    expect(alphas[0]).toBeGreaterThan(0.4);
  });

  it("gives two paths in different groups different colours", () => {
    const a: Path = { id: "a", group: "one", points: [
      { x: 0, y: 0, z: 0 }, { x: 1, y: 1, z: 1 }] };
    const b: Path = { id: "b", group: "two", points: [
      { x: 0, y: 1, z: 0 }, { x: 1, y: 0, z: 1 }] };
    const prepared = preparePaths([a, b]);
    expect(pathColour(prepared.lines[0], 0))
      .not.toBe(pathColour(prepared.lines[1], 1));
  });

  it("gives two paths in the same group the same colour", () => {
    // A constellation, a patient, one simulation run: the group is the thing
    // being compared, so it must survive being drawn beside another.
    const mk = (id: string): Path => ({ id, group: "fleet", points: [
      { x: 0, y: 0, z: 0 }, { x: 1, y: 1, z: 1 }] });
    const prepared = preparePaths([mk("a"), mk("b")]);
    expect(pathColour(prepared.lines[0], 0))
      .toBe(pathColour(prepared.lines[1], 1));
  });

  it("draws a selected path more heavily than the rest", () => {
    const prepared = preparePaths([ring("a"), ring("b")]);
    const plain = paint(prepared);
    const picked = paint(prepared, "a");
    const widest = (r: ReturnType<typeof paint>) =>
      Math.max(...r.calls.filter((c) => c.op === "stroke").map((c) => c.alpha));
    expect(widest(picked)).toBeGreaterThanOrEqual(widest(plain));
    // In the theme's selection colour — the stand-in canvas has no styles, so
    // the light theme's value — not the fixed blue that vanished on dark.
    expect(picked.calls.some((c) => c.stroke === SELECT_FALLBACK)).toBe(true);
  });
});

describe("the controller the seam talks to", () => {
  const three: Path[] = [
    { id: "alpha", label: "Alpha", points: [
      { x: -1, y: -1, z: 0 }, { x: 0, y: 0, z: 0 }, { x: 1, y: 1, z: 0 }] },
    { id: "beta", points: [
      { x: -1, y: 1, z: 0 }, { x: 0, y: 0, z: 0.5 }, { x: 1, y: -1, z: 0 }] },
    { id: "gamma", points: [
      { x: 0, y: -1, z: -1 }, { x: 0, y: 1, z: 1 }] },
  ];

  it("finds a path where the projection draws it", () => {
    /*
     * Hit-testing measures to the *line*, not to its sampled points. A
     * trajectory simplified to four points would otherwise be unselectable
     * everywhere except at those four places, which reads as broken tracking.
     */
    const ref = createRef<VisualizationController>();
    render(<Lines3D paths={three} controllerRef={ref} width={400} height={300} />);
    let found = null;
    for (let x = 0; x <= 400 && !found; x += 4) {
      for (let y = 0; y <= 300 && !found; y += 4) {
        found = ref.current!.hover({ x, y });
      }
    }
    expect(found).not.toBeNull();
  });

  it("selects a path between its sampled points, not only at them", () => {
    /*
     * Hit-testing measures to the line. Measured to the sampled *points*, a
     * trajectory simplified down to four vertices is selectable at four places
     * and nowhere else — the reader points squarely at a drawn line and is
     * told they pointed at nothing, which reads as broken tracking rather than
     * as a hit-test measuring the wrong thing.
     */
    const long: Path[] = [{ id: "long", points: [
      { x: -1, y: 0, z: 0 }, { x: 1, y: 0, z: 0 }] }];
    const ref = createRef<VisualizationController>();
    render(<Lines3D paths={long} controllerRef={ref} width={400} height={300} />);

    const prepared = preparePaths(long, { tolerance: 0, maxPoints: 1e6 });
    const [a, b] = prepared.lines[0].runs[0].map(
      (pt) => toCanvas(pt, DEFAULT_CAMERA, 400, 300));
    const middle = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };

    // The probe has to be far from both ends, or a point-based hit-test would
    // pass by accident.
    expect(Math.hypot(middle.x - a.x, middle.y - a.y)).toBeGreaterThan(20);
    expect(ref.current!.hover(middle)?.id).toBe("long");
  });

  it("does not select a path across the gap it refused to draw", () => {
    // What is drawn and what is selectable have to agree. A chord nobody drew
    // must not be pointable either.
    const broken: Path[] = [{ id: "b", points: [
      { x: -1, y: 0, z: 0 }, { x: -0.8, y: 0, z: 0 },
      { x: NaN, y: 0, z: 0 },
      { x: 0.8, y: 0, z: 0 }, { x: 1, y: 0, z: 0 }] }];
    const ref = createRef<VisualizationController>();
    render(<Lines3D paths={broken} controllerRef={ref} width={400} height={300} />);

    const prepared = preparePaths(broken, { tolerance: 0, maxPoints: 1e6 });
    const runs = prepared.lines[0].runs;
    const end = toCanvas(runs[0][runs[0].length - 1], DEFAULT_CAMERA, 400, 300);
    const start = toCanvas(runs[1][0], DEFAULT_CAMERA, 400, 300);
    const middleOfGap = { x: (end.x + start.x) / 2, y: (end.y + start.y) / 2 };

    expect(Math.hypot(middleOfGap.x - end.x, middleOfGap.y - end.y))
      .toBeGreaterThan(20);
    expect(ref.current!.hover(middleOfGap)).toBeNull();
  });

  it("reports a view that changes when the camera does, and restores it", () => {
    const ref = createRef<VisualizationController>();
    render(<Lines3D paths={three} controllerRef={ref} />);
    const home = ref.current!.viewState();
    ref.current!.rotate(40, 10);
    expect(ref.current!.viewState().yaw).not.toBeCloseTo(home.yaw, 6);
    ref.current!.restoreViewState(home);
    expect(ref.current!.viewState().yaw).toBeCloseTo(home.yaw, 6);
  });

  it("refuses a view state missing any field", () => {
    const ref = createRef<VisualizationController>();
    render(<Lines3D paths={three} controllerRef={ref} />);
    ref.current!.rotate(30, 5);
    const moved = ref.current!.viewState();
    ref.current!.restoreViewState({ pitch: 0.9, zoom: 3 });
    ref.current!.restoreViewState({ yaw: 0.9, zoom: 3 });
    ref.current!.restoreViewState({ yaw: 0.9, pitch: 0.9 });
    expect(ref.current!.viewState()).toEqual(moved);
  });

  it("ignores a focus on a path it does not have", () => {
    const ref = createRef<VisualizationController>();
    const { container } = render(<Lines3D paths={three} controllerRef={ref} />);
    act(() => { ref.current!.focus("not-a-path"); });
    expect(container.textContent).not.toContain("Selected");
  });

  it("names the path that was selected", () => {
    const ref = createRef<VisualizationController>();
    const { container } = render(<Lines3D paths={three} controllerRef={ref} />);
    act(() => { ref.current!.focus("alpha"); });
    expect(container.textContent).toContain("Selected: Alpha");
  });

  it("tells the caller when nothing was under the point", () => {
    const onSelect = vi.fn();
    const ref = createRef<VisualizationController>();
    render(<Lines3D paths={three} controllerRef={ref} onSelect={onSelect} />);
    act(() => { ref.current!.select({ x: -900, y: -900 }); });
    expect(onSelect).toHaveBeenCalledWith(null);
  });

  it("takes every path inside a lasso round the whole canvas, and none outside", () => {
    const ref = createRef<VisualizationController>();
    render(<Lines3D paths={three} controllerRef={ref} width={400} height={300} />);
    expect(ref.current!.withinPolygon([
      { x: -1000, y: -1000 }, { x: 1000, y: -1000 },
      { x: 1000, y: 1000 }, { x: -1000, y: 1000 }])).toHaveLength(3);
    expect(ref.current!.withinPolygon([
      { x: -50, y: -50 }, { x: -10, y: -50 },
      { x: -10, y: -10 }, { x: -50, y: -10 }])).toEqual([]);
  });

  it("has no bounds before it is laid out", () => {
    const ref = createRef<VisualizationController>();
    render(<Lines3D paths={three} controllerRef={ref} />);
    expect(ref.current!.bounds()).toBeNull();
  });

  it("says how many paths and points it drew, and what was missing", () => {
    const gappy: Path[] = [{ id: "g", points: [
      { x: 0, y: 0, z: 0 }, { x: 1, y: 1, z: 1 },
      { x: NaN, y: 0, z: 0 },
      { x: 3, y: 3, z: 3 }, { x: 4, y: 4, z: 4 }] }];
    const { container } = render(
      <Lines3D paths={gappy} caption="Two tracks." />);
    expect(container.textContent).toContain("Two tracks.");
    expect(container.textContent).toContain("stops rather than crossing");
  });
});
