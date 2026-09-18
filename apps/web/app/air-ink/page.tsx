"use client";

/**
 * A page for finding out whether drawing in the air is usable by a person.
 *
 * Separate from `/gesture-check` on purpose. That page asks whether the tracking
 * sees your hand; this one assumes it does and asks the next question, which is
 * whether a line drawn in mid-air lands where you meant it to and encloses what
 * you think it encloses. Those fail differently and are fixed differently, and a
 * page that mixed them would produce "the spatial stuff doesn't work" — a report
 * nobody can act on.
 *
 * Everything the suite cannot reach is on screen here as a number, because the
 * things left unverified in Air Ink are all *physical*: whether two frames of
 * contact is the right threshold for a real pinch, whether a predicted line
 * feels attached to a fingertip or ahead of it, and whether a hand-drawn loop
 * closes reliably enough to be read as a region. None of those can be settled by
 * a test. They can only be settled by somebody drawing.
 *
 * No account, no project, no data leaving the machine — the cloud is synthetic
 * and fixed, exactly as on `/gesture-check`, so this can be opened on any laptop
 * without arranging anything first.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Fold } from "@/components/primitives";
import { Volume } from "@/components/charts/Volume";
import { Surface } from "@/components/charts/Surface";
import { InkLayer, InkSurface } from "@/components/spatial/InkLayer";
import { SpatialControl } from "@/components/spatial/SpatialControl";
import { VisualizationController } from "@/lib/spatial/commands";
import { HandFrame } from "@/lib/spatial/types";
import { InkState } from "@/lib/ink/machine";
import { SpatialStroke, isClosed, observedPoints, strokeLength } from "@/lib/ink/stroke";
import { describeSelection, selectWithinStroke } from "@/lib/ink/select";
import { describeContext, selectionContext } from "@/lib/ink/context";
import { ReferenceTimeline } from "@/lib/voice/timeline";
import {
  ScreenPoint, TargetRef, ViewState, sameView,
} from "@/lib/spatial/commands";
import { describeMeasurement, measureBetween } from "@/lib/ink/measure";
import { Shape } from "@/lib/ink/shapes";
import { InkTool } from "@/lib/ink/stroke";
import { ERASER_RADIUS } from "@/lib/ink/erase";
import {
  STRAIGHTEDGE_HELP, STRAIGHTEDGE_LABEL, Straightedge,
} from "@/lib/ink/straightedge";
import {
  INK_COLOURS, InkLayer as AnnotationLayer, describeLayer,
} from "@/lib/ink/layers";
import { now } from "@/lib/spatial/clock";
import { resolveUtterance } from "@/lib/voice/deixis";
import { describeIntent, readIntent } from "@/lib/voice/intent";
import { ScriptedSpeechSource, typedTiming } from "@/lib/voice/source";
import {
  DEFAULT_STABILISATION_LEVEL, StabilisationLevel,
} from "@/lib/ink/stabilise";

/** The same synthetic cloud shape as the gesture page, so nothing is loaded. */
const CLOUD = Array.from({ length: 180 }, (_, i) => {
  const lobe = i % 3;
  const t = (i / 180) * Math.PI * 2;
  const jitter = (n: number) => (Math.sin(n * 12.9898) * 43758.5453 % 1) - 0.5;
  return {
    id: `p${i}`,
    label: `Point ${i + 1}`,
    x: Math.cos(t) * (2 + lobe) + jitter(i) * 0.8,
    y: Math.sin(t) * (2 + lobe) + jitter(i + 99) * 0.8,
    z: (lobe - 1) * 2 + jitter(i + 7) * 0.9,
    value: lobe,
  };
});

/**
 * A saddle, so the second figure is a different *kind* of thing.
 *
 * Not a second cloud: the point of having two is to check that a loop drawn on
 * one resolves against that one, and two identical figures would make a
 * mis-resolution invisible.
 */
const SADDLE = (() => {
  const axis = Array.from({ length: 15 }, (_, i) => i / 1.75);
  return {
    x: axis,
    y: axis,
    z: axis.map((y) => axis.map((x) =>
      Math.pow(x - 4, 2) / 3 - Math.pow(y - 4, 2) / 3)),
  };
})();

const SADDLE_POINTS = Array.from({ length: 12 }, (_, i) => {
  const x = 1 + (i % 6) * 1.2;
  const y = 1.5 + Math.floor(i / 6) * 3;
  return {
    id: `run${i}`, label: `Run ${i + 1}`, x, y,
    z: Math.pow(x - 4, 2) / 3 - Math.pow(y - 4, 2) / 3 + Math.sin(i * 2.1) * 0.3,
  };
});

/**
 * The chart's size, and therefore the ink's.
 *
 * One constant because the two must not be allowed to drift apart: see the note
 * on the container below.
 */
const CHART = { width: 720, height: 520 };

const STABILISATION_LABEL: Record<StabilisationLevel, string> = {
  natural: "Natural",
  steady: "Steady",
  handwriting: "Handwriting",
};

/**
 * What each setting trades, in the terms somebody choosing between them needs.
 *
 * Every claim here is one `ink-stabilise.test.ts` checks: the order of the
 * levels, that Steady removes most of a still hand's tremor, the gain behind
 * "about 1.5x", and that Handwriting never predicts. It used to quote a still
 * hand's drift in pixels — "about 9px", "under 4px" — and nothing measured
 * either (D326): drift in pixels depends on the hand and the camera, not on
 * this code, so no test here could back it. `air-ink-claims.test.ts` keeps a
 * pixel figure from coming back unmeasured.
 */
const STABILISATION_HELP: Record<StabilisationLevel, string> = {
  natural: "One-to-one with your hand. Best for big marks and arrows; it "
         + "smooths the least, so a hand held still shows the most drift.",
  // "your hand moves *the pen* slightly further than the pen travels" — the
  // stray object made the sentence say the hand moves the pen further than the
  // pen moves, which cannot happen. The `handwriting` entry below has the
  // shape this one meant: hand against ink, not hand against itself.
  steady: "The default. Smooths away most of a still hand's tremor — more "
        + "than Natural — and your hand moves slightly further than the pen "
        + "travels.",
  handwriting: "Most precise. Your hand moves about 1.5x further than the ink "
             + "does, which is what makes small letters controllable, and the "
             + "line never runs ahead of where the camera last saw you.",
};

/** What each pen state means, in the researcher's terms. */
const EXPLAIN: Record<InkState, string> = {
  DISABLED: "The pen is away. Nothing you do will draw.",
  ARMED: "Pen ready. Pinch to start a line.",
  HOVER: "Pen ready, hand seen. Pinch to start a line.",
  PEN_DOWN: "Contact — hold the pinch a moment longer to begin.",
  DRAWING: "Drawing.",
  TRACKING_LOST: "Your hand is not in the picture.",
};

/**
 * What a finished stroke turned out to be.
 *
 * Reported rather than acted on. §197: an interpretation that changes what a
 * researcher is analysing is a question, not a side effect — so the page says
 * what the loop caught and leaves selecting it to them.
 */
type Reading = {
  /** Which stroke this describes, so the table can follow undo and redo. */
  strokeId: string;
  points: number;
  closed: boolean;
  /** Path length in pixels, which is the honest unit for a screen-space stroke. */
  length: number;
  verdict: string;
  /** What could be asked about it, or why it could not be. */
  context: string | null;
  /** Where the scene was when this was drawn, for noticing it has moved. */
  viewState?: ViewState;
  /** What it looks like, if it looks like anything (§181). */
  shape: Shape | null;
  /** Whether the offer has been accepted, so it is not offered twice. */
  tidied: boolean;
};

export default function AirInkPage() {
  const chartRef = useRef<VisualizationController | null>(null);
  const surfaceRef = useRef<VisualizationController | null>(null);
  /**
   * Which figure the hand was addressing, read at the moment a stroke lands.
   *
   * §189 makes this the right question to ask then rather than continuously:
   * the pen is a pinch, so the target is locked for the whole stroke, and
   * whatever it was at pen-down is still what it is at pen-up.
   */
  const readTarget = useRef<(() => VisualizationController | null) | null>(null);
  const inkRef = useRef<InkSurface>(null);
  const [armed, setArmed] = useState(false);
  const [state, setState] = useState<InkState>("DISABLED");
  const [readings, setReadings] = useState<Reading[]>([]);
  const [level, setLevel] = useState<StabilisationLevel>(DEFAULT_STABILISATION_LEVEL);
  const [tool, setTool] = useState<InkTool>("pen");
  /** What the last lasso caught, reported and not applied (§197). */
  const [lassoed, setLassoed] = useState<string | null>(null);
  const [edge, setEdge] = useState<Straightedge>("off");
  const [colour, setColour] = useState<string>(INK_COLOURS[0].value);
  /**
   * The two observations a measurement is between (§183).
   *
   * Two marks rather than two screen points, because a point on screen over a
   * rotatable scene is a ray: depth is ambiguous from one projection, so "the
   * place I indicated" only has data coordinates when it is a mark that exists.
   */
  const [measuring, setMeasuring] = useState<TargetRef[]>([]);
  const [layers, setLayers] = useState<AnnotationLayer[]>([]);
  const [activeLayerId, setActiveLayerId] = useState("researcher");
  const refreshLayers = useCallback(() => {
    setLayers(inkRef.current?.layers() ?? []);
    setActiveLayerId(inkRef.current?.activeLayer() ?? "researcher");
  }, []);
  /**
   * The name of the layer a mark would land on.
   *
   * Shown because a researcher who switched layers and forgot is drawing into a
   * group they may then hide — and would conclude the ink had vanished rather
   * than that it was somewhere they were not looking.
   */
  const activeLayer = layers.find((l) => l.id === activeLayerId)?.name ?? null;
  const [said, setSaid] = useState("");
  const [proposal, setProposal] = useState<string | null>(null);
  /** When the current sentence began being written. See `typedTiming`. */
  const typingStartedAt = useRef<number | null>(null);
  /** What undo and redo would do right now, read after anything changes. */
  /**
   * The current view, polled so the table can say when an annotation no longer
   * corresponds to what is on screen.
   *
   * Polled rather than pushed: rotating fires per frame and this is read by eye.
   */
  const [view, setView] = useState<ViewState | null>(null);
  const [pending, setPendingState] =
    useState<{ undo: string | null; redo: string | null }>({ undo: null, redo: null });
  const setPending = useCallback(() => {
    setPendingState(inkRef.current?.pending() ?? { undo: null, redo: null });
  }, []);

  /**
   * Which strokes are on the canvas right now.
   *
   * Kept apart from the readings themselves, and that separation is the fix:
   * the first version *filtered* the readings to the strokes that existed,
   * which follows an undo correctly and then loses on redo — the stroke comes
   * back and its row does not, because the row was discarded rather than
   * hidden. Readings are never thrown away; the table renders the ones whose
   * stroke is currently present.
   *
   * The underlying disagreement was worse: strokes returned from an undone
   * clear while the table stayed empty, so the canvas showed three marks and
   * the reading showed none.
   */
  const [present, setPresent] = useState<Set<string>>(new Set());
  const syncPresent = useCallback(() => {
    setPresent(new Set((inkRef.current?.strokes() ?? []).map((s) => s.id)));
    setPending();
  }, [setPending]);
  /**
   * What the hand has indicated, on the same clock the words arrive on.
   *
   * A ref rather than state: entries are written thirty times a second in the
   * worst case, and none of them should re-render the page.
   */
  const timelineRef = useRef(new ReferenceTimeline());

  // Frames go straight through. Anything stateful here would run thirty times a
  // second; the recorder is the thing that holds state, and it is not React.
  const handleFrame = useCallback((frame: HandFrame) => {
    inkRef.current?.step(frame);
  }, []);

  /**
   * A finished lasso (§180).
   *
   * Resolved exactly as a drawn region is — the same `selectWithinStroke`, the
   * same conversion into the addressed figure's frame — because it *is* one.
   * The only difference is that nothing is kept afterwards.
   */
  const handleLasso = useCallback((boundary: SpatialStroke,
                                   referenceId: number | null) => {
    const chart = readTarget.current?.() ?? chartRef.current;
    const box = chart?.bounds() ?? null;
    const selection = chart && box
      ? selectWithinStroke(boundary, {
          withinPolygon: (polygon: ScreenPoint[]) => chart.withinPolygon(
            polygon.map((p) => ({ x: p.x - box.x, y: p.y - box.y }))),
        })
      : null;

    setLassoed(selection ? describeSelection(selection)
                         : "No figure was under that loop.");

    // The timeline entry the layer opened at pen-down still has to be closed, or
    // it stays open for ever and captures every word spoken afterwards.
    if (referenceId !== null) {
      if (selection?.ok) {
        timelineRef.current.complete(referenceId, now(),
          { targets: selection.targets.map((t) => t.id) });
      } else {
        timelineRef.current.abandon(referenceId);
      }
    }
  }, []);

  const handleStroke = useCallback((stroke: SpatialStroke,
                                    referenceId: number | null) => {
    const observed = observedPoints(stroke);
    // The figure the hand was on, not a fixed one. With the pen spanning the
    // page, a stroke over the surface must resolve against the surface.
    const chart = readTarget.current?.() ?? chartRef.current;
    const box = chart?.bounds() ?? null;

    /*
     * Strokes are in viewport coordinates and a chart hit-tests in its own, so
     * the polygon is moved into the chart's frame before it is asked anything.
     *
     * A subtraction rather than a projection, and only because `bounds()` and
     * the stroke are expressed in the same frame — which is the entire reason
     * the layer measures the viewport rather than a container.
     */
    const inChartFrame = chart && box
      ? {
          withinPolygon: (polygon: ScreenPoint[]) => chart.withinPolygon(
            polygon.map((p) => ({ x: p.x - box.x, y: p.y - box.y }))),
        }
      : null;
    const selection = inChartFrame
      ? selectWithinStroke(stroke, inChartFrame)
      : null;

    // The view this was drawn in, kept with the stroke (§143). A screen loop
    // over a rotatable scene has no data-space equivalent, so the honest record
    // is where the researcher was standing when they drew it.
    if (chart) stroke.viewState = chart.viewState();

    // Close the timeline entry the layer opened at pen-down. Only this side
    // knows what was inside the loop, because only this side has the chart.
    if (referenceId !== null) {
      const last = observed[observed.length - 1];
      if (selection?.ok) {
        timelineRef.current.complete(referenceId, last?.timestamp ?? now(),
          { targets: selection.targets.map((t) => t.id) });
      } else {
        // A loop that caught nothing is not a referent. Leaving it open would
        // let "these" bind to an empty set and read as though it had worked.
        timelineRef.current.abandon(referenceId);
      }
    }
    setReadings((previous) => [{
      strokeId: stroke.id,
      points: observed.length,
      closed: isClosed(observed),
      length: Math.round(strokeLength(observed)),
      verdict: selection ? describeSelection(selection)
                         : "No chart was mounted to resolve that against.",
      // §198: what the region would hand the assistant, shown rather than sent.
      // §197 is the reason it is only shown — an interpretation that changes
      // what a researcher is analysing gets confirmed, not applied.
      viewState: stroke.viewState,
      // Read once, after the stroke is finished — never while it is being
      // drawn (§181) — and offered rather than applied (§197).
      shape: inkRef.current?.shapeOf(stroke.id) ?? null,
      tidied: false,
      context: selection ? describeContext(selectionContext(selection, {
        visualization: "a synthetic cloud in three lobes",
        xLabel: "x", yLabel: "y", zLabel: "z",
      })) : null,
      // Newest first, and only the last few: this is a live reading, not a log.
    }, ...previous].slice(0, 6));
    syncPresent();
    refreshLayers();
  }, [syncPresent, refreshLayers]);

  /** The rows to draw: readings whose stroke is still on the canvas. */
  const visible = readings.filter((r) => present.has(r.strokeId));

  useEffect(() => {
    const timer = setInterval(
      () => setView(chartRef.current?.viewState() ?? null), 400);
    return () => clearInterval(timer);
  }, []);

  return (
    <main style={{ maxWidth: 1080, margin: "0 auto", padding: "32px 24px 64px" }}>
      <h1 style={{ fontSize: 26, marginBottom: 4 }}>Air Ink</h1>
      <p style={{ color: "var(--ink-soft)", marginTop: 0, maxWidth: 640 }}>
        Drawing in mid-air, over a chart. Nothing here leaves your machine: the
        cloud is synthetic, no project is loaded, and the camera feed is
        processed in the browser and never uploaded.
      </p>
      <p style={{ color: "var(--ink-soft)", marginTop: 0 }}>
        {/* Arrived at from the rail, and reachable no other way from inside
            the page: without this link, leaving means the browser's Back
            button, which a reload has already thrown away. */}
        <Link href="/workspace">Back to the workspace</Link>
      </p>

      <SpatialControl controllerRef={chartRef}
                      alsoControls={[surfaceRef]}
                      label="the figures on this page"
                      onFrame={handleFrame}
                      onActiveTarget={(read) => { readTarget.current = read; }}
                      reachOf={() => (armed && tool === "eraser"
                        ? ERASER_RADIUS : null)}
                      intentOf={() => (!armed ? "grab"
                                     : tool === "eraser" ? "erase"
                                     : tool === "lasso" ? "lasso" : "draw")} />

      <div style={{ display: "flex", gap: 8, alignItems: "center",
                    margin: "16px 0" }}>
        <button onClick={() => setArmed((on) => !on)}
                style={{ padding: "8px 14px", borderRadius: 6,
                         border: "1px solid var(--accent)",
                         background: armed ? "var(--accent)" : "transparent",
                         color: armed ? "var(--on-accent)" : "var(--accent)", cursor: "pointer" }}>
          {armed ? "Put the pen away" : "Take out the pen"}
        </button>
        {/*
          * Undo says what it would undo (§42, §96).
          *
          * "Undo" on its own is not a decision anybody can make after a few
          * minutes of drawing; "Undo clearing 12 strokes" is. That matters most
          * for the one action here that destroys work.
          */}
        <button onClick={() => { inkRef.current?.undo(); syncPresent(); }}
                disabled={!pending.undo}
                style={{ padding: "8px 14px", borderRadius: 6,
                         border: "1px solid var(--line-strong)", background: "transparent",
                         cursor: pending.undo ? "pointer" : "default",
                         opacity: pending.undo ? 1 : 0.45 }}>
          {pending.undo ?? "Undo"}
        </button>
        <button onClick={() => { inkRef.current?.redo(); syncPresent(); }}
                disabled={!pending.redo}
                style={{ padding: "8px 14px", borderRadius: 6,
                         border: "1px solid var(--line-strong)", background: "transparent",
                         cursor: pending.redo ? "pointer" : "default",
                         opacity: pending.redo ? 1 : 0.45 }}>
          {pending.redo ?? "Redo"}
        </button>
        <button onClick={() => { inkRef.current?.clear(); syncPresent(); }}
                style={{ padding: "8px 14px", borderRadius: 6,
                         border: "1px solid var(--line-strong)", background: "transparent",
                         cursor: "pointer" }}>
          Clear
        </button>
        <span style={{ color: "var(--ink-soft)", fontSize: 14 }}>{EXPLAIN[state]}</span>
      </div>

      {/*
        * The eraser is a mode, and that is §176 rather than a design
        * preference: people wave their hands while they talk, so a wiping
        * motion may only erase once the researcher has said they are erasing.
        */}
      <div style={{ display: "flex", gap: 8, alignItems: "center",
                    margin: "0 0 12px" }}>
        <span style={{ fontSize: 14, color: "var(--ink)" }}>Tool</span>
        {(["pen", "eraser", "lasso"] as const).map((option) => (
          <button key={option}
                  onClick={() => { inkRef.current?.setTool(option); setTool(option); }}
                  aria-pressed={tool === option}
                  style={{ padding: "5px 11px", borderRadius: 6, fontSize: 13,
                           border: "1px solid " + (tool === option ? "var(--accent)" : "var(--line-strong)"),
                           background: tool === option ? "var(--accent-soft)" : "transparent",
                           color: tool === option ? "var(--on-accent-soft)" : "var(--ink-soft)",
                           cursor: "pointer" }}>
            {option === "pen" ? "Pen" : option === "eraser" ? "Eraser" : "Lasso"}
          </button>
        ))}
        <span style={{ color: "var(--ink-soft)", fontSize: 13 }}>
          {tool === "pen" && activeLayer
            ? `Drawing into "${activeLayer}". `
            : ""}
          {tool === "pen"
            ? "Pinch and move to draw."
            : tool === "eraser"
              ? "Pinch and move across a mark to rub it out. Erasing the middle "
                + "of a line leaves two lines."
              : "Pinch and draw a loop around some points. The boundary selects "
                + "them and then disappears — it is a question, not a mark."}
        </span>
      </div>

      {/*
        * How the pen behaves, folded away behind its current values (T194).
        *
        * The page opened on fifteen controls and two paragraphs before the
        * first chart. The pen, its tool and undo are what a session needs every
        * few seconds, so they stay out; the straightedge and the stabilisation
        * are set once, so they fold — and the summary names both current values,
        * so a folded setting is never a hidden one.
        */}
      <Fold summary={`Pen settings: straightedge ${STRAIGHTEDGE_LABEL[edge]}, `
                     + `stabilisation ${STABILISATION_LABEL[level]}`}
            count={2} className="ink-settings">
        {/*
          * The straightedge (§182). A hand in mid-air cannot draw a straight line
          * — an arm rotates about a shoulder while the researcher believes they
          * are moving it sideways, so the line bows — and no amount of smoothing
          * fixes that, because the problem is not tremor.
          */}
        <div style={{ display: "flex", gap: 8, alignItems: "center",
                      flexWrap: "wrap", margin: "0 0 6px" }}>
          <span style={{ fontSize: 14, color: "var(--ink)" }}>Straightedge</span>
          {(["off", "line", "horizontal", "vertical", "diagonal",
             "magnetic"] as const).map((option) => (
            <button key={option}
                    onClick={() => { inkRef.current?.setStraightedge(option);
                                     setEdge(option); }}
                    aria-pressed={edge === option}
                    style={{ padding: "5px 11px", borderRadius: 6, fontSize: 13,
                             border: "1px solid " + (edge === option ? "var(--accent)" : "var(--line-strong)"),
                             background: edge === option ? "var(--accent-soft)" : "transparent",
                             color: edge === option ? "var(--on-accent-soft)" : "var(--ink-soft)",
                             cursor: "pointer" }}>
              {STRAIGHTEDGE_LABEL[option]}
            </button>
          ))}
        </div>
        <p style={{ color: "var(--ink-soft)", fontSize: 13, maxWidth: 640, marginTop: 0 }}>
          {STRAIGHTEDGE_HELP[edge]}
        </p>

        {/*
          * Stabilisation is a control rather than a constant because the right
          * amount depends on the hand, the camera and the room — none of which
          * this code can see. The numbers behind each setting were measured
          * against a simulated tremor; only a person can say which one lets them
          * write.
          */}
        <div style={{ display: "flex", gap: 8, alignItems: "center", margin: "0 0 16px" }}>
          <span style={{ fontSize: 14, color: "var(--ink)" }}>Stabilisation</span>
          {(["natural", "steady", "handwriting"] as const).map((option) => (
            <button key={option} onClick={() => setLevel(option)}
                    aria-pressed={level === option}
                    style={{ padding: "5px 11px", borderRadius: 6, fontSize: 13,
                             border: "1px solid " + (level === option ? "var(--accent)" : "var(--line-strong)"),
                             background: level === option ? "var(--accent-soft)" : "transparent",
                             color: level === option ? "var(--on-accent-soft)" : "var(--ink-soft)",
                             cursor: "pointer" }}>
              {STABILISATION_LABEL[option]}
            </button>
          ))}
        </div>
        <p style={{ color: "var(--ink-soft)", fontSize: 13, maxWidth: 640, margin: 0 }}>
          {STABILISATION_HELP[level]}
        </p>
      </Fold>


      {/*
        * The ink host is sized to the chart, exactly, and that is load-bearing.
        *
        * `withinPolygon` resolves a region in the chart's own logical pixels —
        * CHART.width by CHART.height — while `InkLayer` records strokes in the
        * pixels of the element it measures. A host stretched to the page width
        * would record a loop in one coordinate system and hand it to a chart
        * reading another: the ink would draw perfectly, the selection would come
        * back wrong, and nothing on screen would say so. That is the rotation
        * units failure exactly, and the only defence is to make the two boxes
        * the same box rather than to hope they match.
        */}
      {/*
        * The border lives on the outer element, and that is not cosmetic.
        *
        * With `box-sizing: border-box` — which this app sets globally — a 1px
        * border on the positioned box makes its content area 718x518 while the
        * chart still reasons in 720x520. Measured in a browser: the ink host came
        * back two pixels short in each direction. It is a 0.3% error, which is
        * both too small to see and exactly the kind that grows the moment
        * somebody adds padding. Keeping the measured box free of any box-model
        * decoration removes the class of mistake rather than the instance.
        */}
      <div style={{ width: "fit-content", margin: "0 auto",
                    border: "1px solid var(--line)", borderRadius: 8, overflow: "hidden" }}>
      <div style={{ position: "relative", width: CHART.width, height: CHART.height }}>
        <Volume controllerRef={chartRef} points={CLOUD}
                onSelect={(target) => {
                  if (!target) return;
                  // The two most recent, so a third replaces the first rather
                  // than being ignored — a control that stops responding is
                  // harder to understand than one that moves on.
                  setMeasuring((previous) =>
                    [...previous, target].slice(-2));
                }}
                width={CHART.width} height={CHART.height}
                caption="A synthetic cloud in three lobes."
                xLabel="x" yLabel="y" zLabel="z" />
      </div>
      </div>

      <p style={{ color: "var(--ink-soft)", fontSize: 14, maxWidth: 640, marginTop: 12 }}>
        Two locks, deliberately. The pen has to be out <em>and</em> you have to
        pinch — pointing draws nothing at any time, because pointing is what
        people do while they talk.
      </p>

      <h2 style={{ fontSize: 18, marginTop: 32 }}>A second figure</h2>
      <p style={{ color: "var(--ink-soft)", fontSize: 14, maxWidth: 640 }}>
        The pen spans the page rather than one chart, so a loop drawn here
        resolves against <em>this</em> figure. Whichever one your hand is over is
        the one being drawn on, and once you pinch it is held until you let go.
      </p>
      <div style={{ width: "fit-content", margin: "0 auto",
                    border: "1px solid var(--line)", borderRadius: 8, overflow: "hidden" }}>
      <div style={{ position: "relative", width: CHART.width, height: CHART.height }}>
        <Surface controllerRef={surfaceRef} grid={SADDLE}
                 observations={SADDLE_POINTS}
                 width={CHART.width} height={CHART.height}
                 caption="A fitted saddle over two predictors."
                 xLabel="dose" yLabel="duration" zLabel="response" />
      </div>
      </div>

      {/*
        * One pen for the whole page.
        *
        * Fixed to the viewport rather than sized to a figure: a layer inside a
        * chart can only be drawn on inside that chart, and would need its own
        * recorder and its own undo history per figure. Strokes are recorded in
        * viewport coordinates and converted into a chart's frame through its
        * `bounds()` when they are resolved.
        */}
      <div style={{ position: "fixed", inset: 0, pointerEvents: "none",
                    zIndex: 5 }}>
        <InkLayer ref={inkRef} armed={armed} stabilisation={level} fullViewport
                  timeline={timelineRef.current}
                  onState={setState} onStroke={handleStroke}
                  onLasso={handleLasso} />
      </div>

      <h2 style={{ fontSize: 18, marginTop: 32 }}>What each stroke turned out to be</h2>
      <p style={{ color: "var(--ink-soft)", fontSize: 14, maxWidth: 640 }}>
        Reported, not applied. A loop that selects 43 observations is a question
        worth answering before anything acts on it — a selection that silently
        happened is one you have to notice.
      </p>
      {visible.length === 0
        ? <p style={{ color: "var(--ink-faint)" }}>Nothing drawn yet.</p>
        : (
          <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 14 }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--line)" }}>
                <th style={{ padding: "6px 8px" }}>Points</th>
                <th style={{ padding: "6px 8px" }}>Length</th>
                <th style={{ padding: "6px 8px" }}>Closed?</th>
                <th style={{ padding: "6px 8px" }}>Reading</th>
                <th style={{ padding: "6px 8px" }}>As a question</th>
                <th style={{ padding: "6px 8px" }}>Still the same view?</th>
                <th style={{ padding: "6px 8px" }}>Shape</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((reading, i) => (
                <tr key={i} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td style={{ padding: "6px 8px" }}>{reading.points}</td>
                  <td style={{ padding: "6px 8px" }}>{reading.length}px</td>
                  <td style={{ padding: "6px 8px" }}>
                    {reading.closed ? "yes" : "no"}
                  </td>
                  <td style={{ padding: "6px 8px" }}>{reading.verdict}</td>
                  <td style={{ padding: "6px 8px", color: "var(--ink-soft)" }}>
                    {reading.context ?? "—"}
                  </td>
                  <td style={{ padding: "6px 8px" }}>
                    {!reading.viewState ? "—"
                      : sameView(reading.viewState, view)
                        ? <span style={{ color: "var(--positive)" }}>yes</span>
                        : (
                          <button
                            onClick={() => {
                              chartRef.current?.restoreViewState(reading.viewState!);
                              setView(chartRef.current?.viewState() ?? null);
                            }}
                            style={{ font: "inherit", fontSize: 13,
                                     color: "var(--accent)", background: "none",
                                     border: "none", padding: 0,
                                     textDecoration: "underline",
                                     cursor: "pointer" }}>
                            no — go back to it
                          </button>
                        )}
                  </td>
                  <td style={{ padding: "6px 8px" }}>
                    {reading.tidied
                      ? <span style={{ color: "var(--ink-faint)" }}>tidied</span>
                      : !reading.shape
                        ? <span style={{ color: "var(--ink-faint)" }}>as drawn</span>
                        : (
                          <button
                            onClick={() => {
                              inkRef.current?.tidy(reading.strokeId, reading.shape!);
                              setReadings((rows) => rows.map((r) =>
                                r.strokeId === reading.strokeId
                                  ? { ...r, tidied: true } : r));
                              setPending();
                            }}
                            style={{ font: "inherit", fontSize: 13,
                                     color: "var(--accent)", background: "none",
                                     border: "none", padding: 0,
                                     textDecoration: "underline",
                                     cursor: "pointer" }}>
                            tidy into {reading.shape.kind}
                          </button>
                        )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

      {/*
        * Colour and layers (§202, §201), kept to one row each.
        *
        * §202 warns against a floating palette for every change, so there are
        * four colours rather than a picker, and layers are a list of switches
        * rather than a panel.
        */}
      <div style={{ display: "flex", gap: 8, alignItems: "center",
                    margin: "0 0 16px" }}>
        <span style={{ fontSize: 14, color: "var(--ink)" }}>Colour</span>
        {INK_COLOURS.map((option) => (
          <button key={option.id}
                  onClick={() => { inkRef.current?.setStyle({ colour: option.value });
                                   setColour(option.value); }}
                  aria-pressed={colour === option.value}
                  aria-label={option.name}
                  style={{ width: 26, height: 26, borderRadius: "50%",
                           background: option.value, cursor: "pointer",
                           border: colour === option.value
                             ? "3px solid var(--ink)" : "1px solid var(--line-strong)" }} />
        ))}
      </div>

      <h2 style={{ fontSize: 18, marginTop: 32 }}>Measuring between two points</h2>
      <p style={{ color: "var(--ink-soft)", fontSize: 14, maxWidth: 640 }}>
        Click two observations on the cloud above — or pinch them, with the pen
        away. What comes back is the difference along each axis, in that axis&rsquo;s
        own units.
      </p>
      <p style={{ color: "var(--ink-soft)", fontSize: 13, maxWidth: 640 }}>
        <strong>It will not give you a single distance, and that is deliberate.</strong>{" "}
        The three axes are scaled independently so the shape of the cloud is
        legible, which means a centimetre along one is a different amount of a
        different quantity from a centimetre along another. There is no length of
        the line between two observations — not one that is hard to compute, one
        that does not exist. A number here would be an invented unit, and it
        would get quoted.
      </p>
      <p style={{ maxWidth: 640, fontSize: 14, padding: "10px 12px",
                  background: "var(--panel)", border: "1px solid var(--line)",
                  borderRadius: 6 }}>
        {measuring.length < 2
          ? `Select ${2 - measuring.length} more observation${
              measuring.length === 1 ? "" : "s"}.`
          : describeMeasurement(measureBetween(
              (measuring[0].datum ?? {}) as Record<string, unknown>,
              (measuring[1].datum ?? {}) as Record<string, unknown>,
              // The chart's own labels, so a difference reads in the terms the
              // figure is drawn in rather than as an anonymous axis letter.
              { x: "x", y: "y", z: "z" }))}
      </p>
      {measuring.length > 0 && (
        <button onClick={() => setMeasuring([])}
                style={{ font: "inherit", fontSize: 13, color: "var(--accent)",
                         background: "none", border: "none", padding: 0,
                         textDecoration: "underline", cursor: "pointer" }}>
          Start again
        </button>
      )}

      <h2 style={{ fontSize: 18, marginTop: 32 }}>Layers</h2>
      <p style={{ color: "var(--ink-soft)", fontSize: 14, maxWidth: 640 }}>
        Turn a group of annotations off to see the figure underneath. Hiding is
        not erasing — everything comes back.
      </p>
      {layers.length === 0
        ? <p style={{ color: "var(--ink-faint)", fontSize: 14 }}>
            Nothing drawn yet. <button
              onClick={refreshLayers}
              style={{ font: "inherit", color: "var(--accent)", background: "none",
                       border: "none", padding: 0, textDecoration: "underline",
                       cursor: "pointer" }}>Show layers</button>
          </p>
        : (
          <ul style={{ listStyle: "none", padding: 0, maxWidth: 640 }}>
            {layers.map((layer) => (
              <li key={layer.id} style={{ marginBottom: 8, fontSize: 14 }}>
                <label style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                  <input type="checkbox" checked={layer.visible}
                         onChange={(e) => {
                           inkRef.current?.setLayerVisible(layer.id,
                                                           e.target.checked);
                           refreshLayers();
                         }} />
                  <span style={{ color: "var(--ink)" }}>{describeLayer(layer)}</span>
                </label>
              </li>
            ))}
          </ul>
        )}

      <h2 style={{ fontSize: 18, marginTop: 32 }}>Saying what you mean</h2>
      <p style={{ color: "var(--ink-soft)", fontSize: 14, maxWidth: 640 }}>
        Draw a loop around some points, then say what you want — &ldquo;why are
        these different&rdquo;, &ldquo;compare this with this&rdquo;. The word
        &ldquo;these&rdquo; is resolved against <em>what your hand was doing when
        you said it</em>, so it works even when you speak while still drawing.
      </p>
      <p style={{ color: "var(--ink-soft)", fontSize: 13, maxWidth: 640 }}>
        <strong>Typed, not spoken, and that is deliberate.</strong> The
        browser&rsquo;s built-in speech recognition sends your microphone audio
        to Google, which would break the promise that nothing here leaves your
        machine. Typing runs the identical path — the words are stamped with the
        time you enter them — so this is the real feature rather than a stand-in.
      </p>
      <form onSubmit={(event) => {
              event.preventDefault();
              const source = new ScriptedSpeechSource();
              const words: Array<{ text: string; at: number }> = [];
              source.start((e) => words.push({ text: e.text, at: e.at }));
              /*
               * Spread across the interval the sentence was *composed* in, on
               * the same clock the hand frames use.
               *
               * Stamping relative to submit meant drawing a loop and taking ten
               * seconds to type put every word outside the backward window, so
               * somebody referring to the circle they had just drawn was told
               * nothing was indicated. Speech does not have that problem because
               * people speak while they gesture; the equivalent for typing is
               * when they started writing.
               */
              const { startAt, durationMs } =
                typedTiming(typingStartedAt.current ?? now(), now());
              source.utter(said, startAt, durationMs);
              typingStartedAt.current = null;
              const resolved = resolveUtterance({ words, final: true },
                                                timelineRef.current);
              setProposal(describeIntent(readIntent(resolved)));
            }}
            style={{ display: "flex", gap: 8, maxWidth: 640, margin: "12px 0" }}>
        <input value={said}
               onChange={(e) => {
                 // The first keystroke of a sentence is when the researcher was
                 // still looking at what they had just done.
                 if (typingStartedAt.current === null) {
                   typingStartedAt.current = now();
                 }
                 if (e.target.value === "") typingStartedAt.current = null;
                 setSaid(e.target.value);
               }}
               placeholder="why are these different"
               aria-label="Say something about what you indicated"
               style={{ flex: 1, padding: "7px 10px", fontSize: 14,
                        border: "1px solid var(--line-strong)", borderRadius: 6 }} />
        <button type="submit"
                style={{ padding: "7px 14px", borderRadius: 6, fontSize: 14,
                         border: "1px solid var(--accent)", background: "transparent",
                         color: "var(--accent)", cursor: "pointer" }}>
          Read it
        </button>
      </form>
      {lassoed && (
        <p style={{ maxWidth: 640, fontSize: 14, padding: "10px 12px",
                    background: "var(--panel)", border: "1px solid var(--line)",
                    borderRadius: 6 }}>
          {lassoed}
        </p>
      )}

      {proposal && (
        <p style={{ maxWidth: 640, fontSize: 14, padding: "10px 12px",
                    background: "var(--panel)", border: "1px solid var(--line)",
                    borderRadius: 6 }}>
          {proposal}
        </p>
      )}
      <p style={{ color: "var(--ink-faint)", fontSize: 13, maxWidth: 640 }}>
        Nothing is run. A spoken sentence is ambiguous and has no natural moment
        to confirm it, so what comes back is a proposal you would accept or
        decline — a misheard word should cost you a decline, not an analysis.
      </p>

      <h2 style={{ fontSize: 18, marginTop: 32 }}>What is actually unknown</h2>
      <p style={{ color: "var(--ink-soft)", fontSize: 14, maxWidth: 640 }}>
        Every number in this subsystem came from reasoning rather than from a
        hand. These are the specific things a test cannot settle, phrased so that
        an answer is useful.
      </p>
      <ol style={{ color: "var(--ink)", fontSize: 14, maxWidth: 640, lineHeight: 1.7 }}>
        <li>
          <strong>Does the line feel attached to your fingertip?</strong> It is
          drawn about one frame ahead of where the camera last saw you, to cover
          latency that cannot be removed. Ahead is wrong too — if it overshoots
          when you stop or corners, the prediction horizon is too long.
        </li>
        <li>
          <strong>Do you get dots you did not mean?</strong> A pinch has to hold
          for two frames before it counts as a mark. If stray dots appear anyway,
          that number is too low; if lines start late, it is too high.
        </li>
        <li>
          <strong>Do your loops close?</strong> The <em>Closed?</em> column above
          says whether each stroke was read as a region. A loop that looks closed
          to you and reads as open is a tolerance problem, and it is the
          difference between selecting a cluster and being told to draw again.
        </li>
        <li>
          <strong>Is the count right?</strong> Draw round a group you can count
          by eye and compare. This is the number that would be quoted, so a
          disagreement here matters more than anything else on the page.
        </li>
        <li>
          <strong>Does it stay fast?</strong> Draw thirty or forty strokes and
          see whether the line lags more than it did at the start. It should not:
          finished strokes are painted on a separate layer that is not touched
          while you draw.
        </li>
      </ol>
    </main>
  );
}
