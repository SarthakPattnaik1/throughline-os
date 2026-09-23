"use client";

/**
 * The eight marks of chapter D.
 *
 * The point of this graphic is that it is ONE set of marks reconfigured, not
 * four charts cross-faded: the same eight groups persist through Bar, Dot plot,
 * Violin and Interval, and a reader watching one value can follow it across
 * every form. So each mark keeps its own <g> for the life of the page and only
 * its geometry moves.
 *
 * It takes no scroll listener of its own. §03 of the handoff allows exactly one
 * progress owner on this page, and that is the entrance controller; this
 * component exposes `setStage` and the controller drives it. Attributes are
 * written straight to the DOM rather than through state, because a React
 * re-render per frame is the thing that would make this stutter.
 *
 * What it is not: a statistical figure. The violin is not a density and the
 * intervals are not confidence intervals — they are shapes. The qualifier
 * saying so is rendered beside it and is not decoration.
 */

import { forwardRef, useCallback, useImperativeHandle, useRef, useState } from "react";

/** The handoff's own eight values, so the shape matches the approved frame. */
const VALUES = [0.82, 0.61, 0.44, 0.73, 0.35, 0.56, 0.68, 0.29] as const;

export const FORMS = ["Bar", "Dot plot", "Violin", "Interval"] as const;
export type Form = (typeof FORMS)[number];

export type MarksHandle = {
  /** 0 = Bar, 1 = Dot plot, 2 = Violin, 3 = Interval. Fractional interpolates. */
  setStage(stage: number): void;
};

const W = 780;
const H = 300;
const BASE = H - 52;
const TOP = 26;

const clamp = (v: number, lo: number, hi: number) => (v < lo ? lo : v > hi ? hi : v);
/** Triangular weight: 1 at its own form, 0 at the neighbouring ones. */
const weight = (stage: number, at: number) => Math.max(0, 1 - Math.abs(stage - at));

export const Marks = forwardRef<MarksHandle, { onFormChange?: (form: Form) => void }>(
  function Marks({ onFormChange }, ref) {
    const groups = useRef<(SVGGElement | null)[]>([]);
    const [settled, setSettled] = useState<Form>("Bar");
    const settledRef = useRef<Form>("Bar");

    const paint = useCallback((stage: number) => {
      const s = clamp(stage, 0, 3);
      const bar = weight(s, 0);
      const dot = weight(s, 1);
      const violin = weight(s, 2);
      const interval = weight(s, 3);

      const gap = (W - 120) / (VALUES.length - 1);

      VALUES.forEach((value, i) => {
        const g = groups.current[i];
        if (!g) return;
        const x = 70 + i * gap;
        const y = TOP + (BASE - TOP) * (1 - value);

        const rect = g.querySelector("rect");
        const spine = g.querySelector("line");
        const blob = g.querySelector("path");
        const dotEl = g.querySelector("circle");

        // Bar: a column from the baseline to the value.
        if (rect) {
          const h = (BASE - y) * bar;
          rect.setAttribute("x", String(x - 13));
          rect.setAttribute("y", String(BASE - h));
          rect.setAttribute("height", String(Math.max(0, h)));
          rect.setAttribute("opacity", String(bar));
        }

        // Spine: nothing under Bar, the whisker under Interval, the axis the
        // violin is built around.
        const spread = (BASE - TOP) * 0.13 * (violin * 0.85 + interval);
        if (spine) {
          spine.setAttribute("x1", String(x));
          spine.setAttribute("x2", String(x));
          spine.setAttribute("y1", String(y - spread));
          spine.setAttribute("y2", String(y + spread));
          spine.setAttribute("opacity", String(clamp(violin * 0.4 + interval, 0, 1)));
        }

        // Violin: a symmetric lobe about the value. A shape, not a density.
        if (blob) {
          const half = 24 * violin;
          const top = y - spread;
          const bottom = y + spread;
          blob.setAttribute(
            "d",
            `M ${x} ${top} C ${x + half} ${top + spread * 0.5}, ${x + half} ${bottom - spread * 0.5}, ${x} ${bottom}` +
              ` C ${x - half} ${bottom - spread * 0.5}, ${x - half} ${top + spread * 0.5}, ${x} ${top} Z`,
          );
          blob.setAttribute("opacity", String(violin));
        }

        // The dot is the one mark that never leaves: it is the value itself,
        // and keeping it visible is what lets a reader track one observation
        // through all four forms.
        if (dotEl) {
          dotEl.setAttribute("cx", String(x));
          dotEl.setAttribute("cy", String(y));
          dotEl.setAttribute("r", String(3.2 + 1.9 * (dot + interval) - 0.6 * bar));
          dotEl.setAttribute("opacity", String(clamp(0.28 + dot + interval * 0.9 + violin * 0.3, 0, 1)));
        }
      });

      const nearest = FORMS[clamp(Math.round(s), 0, 3)];
      if (nearest !== settledRef.current) {
        settledRef.current = nearest;
        setSettled(nearest);
        onFormChange?.(nearest);
      }
    }, [onFormChange]);

    useImperativeHandle(ref, () => ({ setStage: paint }), [paint]);

    return (
      <div className="marks">
        <svg
          className="marks-chart"
          viewBox={`0 0 ${W} ${H}`}
          // Capped in height, so without this the letterboxing centres the
          // marks and they drift away from the copy they sit under.
          preserveAspectRatio="xMinYMid meet"
          role="img"
          aria-label={
            "The same eight values drawn four ways: as bars, as dots, as violin " +
            "shapes and as intervals. The marks are reconfigured rather than replaced. " +
            "Illustrative shapes, not statistical estimates."
          }
        >
          <line className="marks-axis" x1={44} x2={W - 30} y1={BASE} y2={BASE} />
          {VALUES.map((value, i) => (
            <g
              key={i}
              ref={(el) => {
                groups.current[i] = el;
              }}
              className="mark"
            >
              <rect className="mark-bar" width={26} rx={1.5} x={0} y={BASE} height={0} opacity={1} />
              <path className="mark-violin" d="" opacity={0} />
              <line className="mark-spine" x1={0} x2={0} y1={0} y2={0} opacity={0} />
              <circle className="mark-dot" cx={0} cy={0} r={4} opacity={0.3} />
            </g>
          ))}
        </svg>

        <div className="marks-foot">
          <div className="marks-forms" role="group" aria-label="Graph form">
            {FORMS.map((form, i) => (
              <button
                key={form}
                type="button"
                className="marks-form"
                aria-pressed={settled === form}
                onClick={() => paint(i)}
              >
                {form}
              </button>
            ))}
          </div>
          <p className="marks-qualifier">
            Illustrative animation · shapes only, not statistical estimates
          </p>
        </div>
      </div>
    );
  },
);
