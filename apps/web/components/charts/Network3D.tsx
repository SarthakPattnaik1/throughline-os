"use client";

/**
 * A graph drawn in space (§9 networks, §16 native).
 *
 * The renderer for the thirty-five catalogue entries that share the `network`
 * primitive — citation networks, protein interaction, dependency graphs, and
 * the provenance tree and evidence galaxy that draw this system's own semantic
 * layer.
 *
 * **Canvas with the shared projection, not a scene graph.** `scene3d` already
 * owns the camera, the perspective divide and the unit cube, and every other
 * spatial chart here reads through it. A second projection would drift from the
 * first in exactly the way the ink canvas and the page canvas would have — and
 * the drift shows up as selections landing next to marks rather than on them.
 *
 * **Edges are drawn before nodes, back to front.** A painter's ordering, because
 * without it an edge passing behind a node is drawn over it and the graph reads
 * as flat. Depth is most of what a spatial network buys over a flat one, so
 * losing it loses the reason to be here.
 *
 * **Layout is not recomputed while the camera moves.** Rotating asks a different
 * question of the same arrangement; relaxing the graph again would make the
 * nodes swim, and a researcher would be unable to tell a rotation from a change
 * in the data.
 */

import { selectionColour } from "@/lib/charts/theme";
import {
  useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState,
} from "react";
import { useMounted } from "@/lib/charts/useMounted";
import { ScreenPoint, TargetRef, VisualizationController } from "@/lib/spatial/commands";
import { canvasPoint, isClick } from "@/lib/charts/pointer";
import { drawLitSphere } from "@/lib/charts3d/shading";
import { AXES_SCALED_SEPARATELY, Camera, DEFAULT_CAMERA, insidePolygon, resetCamera, rotateCamera, toCanvas, zoomCamera } from "@/lib/charts/scene3d";
import { useSpatialKeys } from "@/lib/charts/spatialKeys";
import { ChartExport } from "@/components/charts/ChartExport";
import { depthRange, hazeFor } from "@/lib/charts/depth";
import { isZoomWheel, wheelZoomFactor } from "@/lib/charts/wheel";
import { useLayout } from "@/lib/charts3d/useLayout";
import {
  Graph, Layout, Placed, describeLayout,
  neighboursOf,
} from "@/lib/charts3d/network";
import { categorical, seriesEdge } from "@/lib/tokens";

export type Network3DProps = {
  graph: Graph;
  width?: number;
  height?: number;
  /**
   * Depth per node, for graphs where depth carries meaning.
   *
   * Supplying it swaps the force-directed arrangement for a layered one. The
   * provenance tree is why: a result derives from runs which derive from
   * datasets, and that ordering is the information rather than an artefact of
   * how many edges each node happens to have.
   */
  depthOf?: (nodeId: string) => number;
  controllerRef?: React.RefObject<VisualizationController | null>;
  onSelect?: (target: TargetRef | null) => void;
  /** What the graph is, for a reader who did not build it. */
  caption?: string;
};

const NODE_RADIUS = 4.5;
/** How near a pointer must be, in pixels, to count as on a node. */
const PICK_RADIUS = 14;

export function Network3D({
  graph, width = 720, height = 520, depthOf, controllerRef, onSelect, caption,
}: Network3DProps) {
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
   * Computed once per graph, and deliberately not per frame. Relaxing again on
   * every rotation would make the nodes swim, and a researcher could not tell a
   * camera move from a change in the data.
   */
  /*
   * Off the main thread once the graph is big enough to freeze it. See
   * `useLayout` — small graphs and layered ones stay synchronous, because the
   * round trip costs more than the work and would flash a "settling" state in
   * front of a picture that is otherwise instant.
   */
  const state = useLayout(graph, depthOf);
  const EMPTY: Layout = useMemo(
    () => ({ nodes: [], edges: [], dangling: [] }), []);
  /*
   * An empty layout while the worker settles, rather than a partial one.
   *
   * Everything below — hit-testing, the controller, the caption — reads this,
   * and half a layout would let a reader click a node that is about to move.
   * The figure says it is working; it does not show a graph that is not
   * finished.
   */
  const layout: Layout = state.kind === "ready" ? state.layout : EMPTY;

  /** Where a node lands on the canvas, at the current camera. */
  const at = useCallback((node: Placed): ScreenPoint => {
    // Through the shared `toCanvas`, which is also what the draw loop uses.
    // A private copy of the fit factor would put a network at a different
    // scale from a scatter, and — worse — let hit-testing drift from painting.
    const q = toCanvas(node, cameraRef.current, width, height);
    return { x: q.x, y: q.y };
  }, [width, height]);

  const nearest = useCallback((point: ScreenPoint): TargetRef | null => {
    let best: TargetRef | null = null;
    let bestGap = PICK_RADIUS;
    for (const node of layout.nodes) {
      const p = at(node);
      const gap = Math.hypot(p.x - point.x, p.y - point.y);
      // `<=` so a later node wins a tie: it is the one drawn on top, and taking
      // the one behind removes something the researcher cannot see.
      if (gap <= bestGap) {
        bestGap = gap;
        best = { id: node.id, label: node.label ?? node.id, datum: node };
      }
    }
    return best;
  }, [layout, at]);

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
    "Citation network, drawn in three dimensions and rotatable.");

  useImperativeHandle(controllerRef, (): VisualizationController => ({
    rotate,
    zoom: (factor) => {
      // Through the shared clamp, like every other caller.
      zoomCamera(cameraRef.current, factor);
      dirtyRef.current = true;
    },
    // Not offered rather than stubbed: this centres a unit cube and there is
    // nothing off-frame to pan toward. A control that silently does nothing is
    // worse than one that is absent.
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
      dirtyRef.current = true;
      onSelect?.(target);
      return target;
    },
    selectRegion: (point, radius) => {
      const found: TargetRef[] = [];
      for (const node of layout.nodes) {
        const p = at(node);
        if (Math.hypot(p.x - point.x, p.y - point.y) > radius) continue;
        found.push({ id: node.id, label: node.label ?? node.id, datum: node });
      }
      return found;
    },
    withinPolygon: (polygon) => {
      // Every node tested once against the same projection the draw loop uses,
      // so the count a researcher reads is exact rather than sampled.
      const found: TargetRef[] = [];
      for (const node of layout.nodes) {
        if (!insidePolygon(polygon, at(node))) continue;
        found.push({ id: node.id, label: node.label ?? node.id, datum: node });
      }
      return found;
    },
    focus: (objectId) => { setSelected(objectId); dirtyRef.current = true; },
    deselect: () => { setSelected(null); dirtyRef.current = true; },
    resetView: () => { resetCamera(cameraRef.current); dirtyRef.current = true; },
    viewport: () => ({ width, height }),
    /*
     * Null rather than a zero rectangle when unmeasurable — a zero rectangle
     * is a claim about where this chart is, and an unmounted canvas has no
     * position to claim.
     */
    bounds: () => {
      const box = canvasRef.current?.getBoundingClientRect();
      // A canvas not yet laid out measures zero, which is not a position.
      if (!box || box.width === 0 || box.height === 0) return null;
      return { x: box.left, y: box.top, width: box.width, height: box.height };
    },
    /*
     * Yaw, pitch and zoom — the same three every camera-driven chart here
     * reports, so an annotation drawn on a network and one drawn on a surface
     * can both answer "is this still the view I was drawn in".
     */
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
  }), [rotate, nearest, layout, at, width, height, onSelect]);

  /* ---- drawing ---- */

  useEffect(() => { dirtyRef.current = true; }, [layout, selected]);

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
        paintNetwork(canvasRef.current, layout, cameraRef.current,
                     { width, height }, selected, hoveredRef.current);
      }
      handle = requestAnimationFrame(tick);
    };
    handle = requestAnimationFrame(tick);
    return () => { running = false; cancelAnimationFrame(handle); };
  }, [layout, width, height, selected]);

  /* ---- pointer, so the chart works without a camera (Rule 4) ---- */

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
        data-testid="network-3d"
        style={{ width: "100%", maxWidth: width, touchAction: "none" }}
        onPointerDown={(event) => {
          dragging.current = { x: event.clientX, y: event.clientY };
          pressedAt.current = { x: event.clientX, y: event.clientY };
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
           * Selection existed on this chart all along — it paints a selected
           * node, reports one through `onSelect`, and exposes `select` on its
           * controller — and no pointer ever reached it. The capability was
           * reachable from the gesture layer and from nowhere a mouse could
           * go, so clicking a node in a node-link graph did nothing at all.
           *
           * The canvas rotates on drag, so every selection starts as a
           * gesture that might become a rotation; the distance travelled is
           * what separates them.
           */
          const start = pressedAt.current;
          pressedAt.current = null;
          dragging.current = null;
          if (!isClick(start, { x: event.clientX, y: event.clientY })) return;
          const target = event.currentTarget;
          const point = canvasPoint(event, target, width, height);
          const picked = nearest(point);
          setSelected(picked?.id ?? null);
          dirtyRef.current = true;
          onSelect?.(picked);
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
      {/* §75: a spatial chart could not be saved at all. */}
      <ChartExport canvasRef={canvasRef} name="Citation network"
                   rotate={(degrees) => rotate(degrees, 0)}
                   redraw={() => { dirtyRef.current = true; }} />
      <figcaption className="chart-caption">
        {caption ? `${caption} ` : ""}
        {mounted && describeLayout(layout)}
        {selected && (
          <> Selected: {layout.nodes.find((n) => n.id === selected)?.label
                        ?? selected}
            {" "}({neighboursOf(layout, selected).length} connected).</>
        )}
        {" "}{AXES_SCALED_SEPARATELY}
      </figcaption>
    </figure>
  );
}


/**
 * One frame of the graph.
 *
 * Exported for the same reason `paintCursor` and `InkLayer`'s `paint` are: a
 * draw loop reachable only through an animation frame is one no test ever runs,
 * and this is where depth ordering either happens or does not.
 */
/**
 * The page colour behind the marks, for haloing.
 *
 * Read from the cascade so the chart follows the theme instead of assuming a
 * light page. Guarded because `paintNetwork` is deliberately callable with a
 * recording stand-in for a canvas — that is how the paint order is tested
 * without a renderer — and a plain object is not an `Element`, so asking the
 * window for its computed style throws. The fallback is only ever reached from
 * those tests, where no colour is drawn to a screen at all.
 */
/**
 * How far the halo stands out past a node.
 *
 * Exported so the paint tests can tell a halo from the node it belongs to
 * without counting arcs positionally — every node contributes exactly two, and
 * a test that assumed one silently measured the wrong circle.
 */
export const HALO_PAD = 2.5;

function groundColour(canvas: HTMLCanvasElement): string {
  if (typeof getComputedStyle !== "function"
      || typeof Element === "undefined" || !(canvas instanceof Element)) {
    return "#ffffff";
  }
  return getComputedStyle(canvas).getPropertyValue("--panel").trim() || "#ffffff";
}

export function paintNetwork(
  canvas: HTMLCanvasElement | null,
  layout: Layout,
  camera: Camera,
  size: { width: number; height: number },
  selected: string | null,
  hovered: string | null,
): void {
  if (!canvas) return;
  const context = canvas.getContext("2d");
  if (!context) return;

  const { width, height } = size;
  context.clearRect(0, 0, width, height);

  const place = (node: Placed) => toCanvas(node, camera, width, height);

  const placed = new Map(layout.nodes.map((n) => [n.id, place(n)]));

  /*
   * Aerial perspective, over the scene actually on screen.
   *
   * Back-to-front order was the only depth cue here, and it says something
   * only where two marks overlap; everywhere else a near node and a far one
   * were drawn identically and the graph read flat until it was moved. Haze
   * is free — opacity carries nothing else in this chart — and it needs no
   * explaining to a reader. See `lib/charts/depth.ts` for why it is not size.
   */
  const { near, far } = depthRange([...placed.values()].map((p) => p.depth));

  /*
   * The colour behind the marks, for haloing below. Read from the cascade so
   * the chart follows the theme rather than assuming a light one.
   */
  const ground = groundColour(canvas);
  const selection = selectionColour(canvas);

  /*
   * Back to front. Without it an edge passing behind a node is drawn over it
   * and the graph reads as flat — and depth is most of what a spatial network
   * buys over a flat one.
   */
  const edges = layout.edges
    .map((edge) => ({
      edge,
      from: placed.get(edge.from.id)!,
      to: placed.get(edge.to.id)!,
    }))
    .sort((a, b) => (a.from.depth + a.to.depth) - (b.from.depth + b.to.depth));

  for (const { edge, from, to } of edges) {
    const touchesSelection = selected !== null
      && (edge.from.id === selected || edge.to.id === selected);
    context.save();
    // An edge takes the haze of its midpoint: it spans two depths, and the
    // point halfway along is where it actually is.
    context.globalAlpha = touchesSelection
      ? 1 : hazeFor((from.depth + to.depth) / 2, near, far);
    // The theme's selection colour (the fixed blue was 2.3:1 on the dark canvas).
    context.strokeStyle = touchesSelection ? selection : "rgba(120,130,150,0.30)";
    context.lineWidth = touchesSelection ? 1.8 : 1;
    context.beginPath();
    context.moveTo(from.x, from.y);
    context.lineTo(to.x, to.y);
    context.stroke();
    context.restore();
  }

  const nodes = layout.nodes
    .map((node) => ({ node, at: placed.get(node.id)! }))
    .sort((a, b) => a.at.depth - b.at.depth);

  for (const { node, at } of nodes) {
    const isSelected = node.id === selected;
    const isHovered = node.id === hovered;
    // Size carries weight, and nothing else. Depth is already carried by the
    // projection, so binding it to size as well would double-count it.
    const radius = NODE_RADIUS * (1 + Math.min(1, node.weight ?? 0));

    context.save();

    /*
     * A halo in the page's own colour, drawn before the node.
     *
     * This is the occlusion cue. Two circles of the same colour overlapping
     * read as a figure-of-eight, not as one in front of the other, so sorting
     * them back to front bought nothing a reader could see. A ring of the
     * background punched around each node means a nearer one visibly cuts
     * into whatever it covers — the single largest difference between a
     * legible node-link graph and a tangle.
     */
    context.beginPath();
    context.arc(at.x, at.y, radius + HALO_PAD, 0, Math.PI * 2);
    context.fillStyle = ground;
    context.fill();

    // Haze on the node itself; a selected node stays at full strength, since
    // fading the thing the reader just picked would answer the wrong question.
    context.globalAlpha = isSelected || isHovered
      ? 1 : hazeFor(at.depth, near, far);
    // Shaded from a constant light, so a node reads as a bead rather than a
    // sticker. The colour still carries the group and nothing else: the light
    // does not move with position or value, so lightness says nothing.
    const fill = node.group
      ? categorical[hashOf(node.group) % categorical.length]
      : "rgba(90,105,135,0.9)";
    drawLitSphere(context, at.x, at.y, isSelected ? radius + 2 : radius, fill);
    // A pale group is ringed so its beads stay findable on a light page (D416).
    const pale = node.group ? seriesEdge(fill) : null;
    if (pale && !isSelected && !isHovered) {
      context.beginPath();
      context.arc(at.x, at.y, radius, 0, Math.PI * 2);
      context.strokeStyle = pale;
      context.lineWidth = 1;
      context.stroke();
    }

    if (isSelected || isHovered) {
      // The path has to be laid down here: `drawLitSphere` stamps an image and
      // leaves no current path to stroke. Built only for the one node that
      // needs it rather than for every node — an outline nothing strokes is
      // two canvas calls per node, on every frame of a drag.
      context.beginPath();
      context.arc(at.x, at.y, isSelected ? radius + 2 : radius, 0, Math.PI * 2);
      // An outline as well as a colour, because §82 forbids state carried by
      // colour alone.
      context.strokeStyle = isSelected ? selection : "rgba(60,70,90,0.8)";
      context.lineWidth = isSelected ? 2.5 : 1.5;
      context.stroke();
    }
    context.restore();
  }
}

/** A stable index into the categorical palette, from a group name. */
function hashOf(group: string): number {
  let h = 0;
  for (let i = 0; i < group.length; i += 1) {
    h = (h * 31 + group.charCodeAt(i)) >>> 0;
  }
  return h;
}
