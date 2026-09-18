/**
 * What Air Ink tells a researcher about its settings is only what is measured (D326).
 *
 * The help quoted a still hand's drift as "about 9px" and "under 4px", and no
 * test in the repository could produce either number: drift in pixels depends
 * on the hand and the camera. The claims that remain are each checked — the
 * ordering and the tremor removed in `ink-stabilise.test.ts`, the gains here.
 */

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { STABILISATION } from "@/lib/ink/stabilise";

const page = readFileSync("app/air-ink/page.tsx", "utf8");
const help = page.match(/STABILISATION_HELP[^=]*=\s*\{([\s\S]*?)\n\};/)![1];

describe("the stabilisation help", () => {
  it("quotes no drift in pixels that nothing measures", () => {
    expect(help).not.toMatch(/\d+\s*px/);
  });

  it("says 'about 1.5x' for handwriting because its gain is 0.65", () => {
    const quoted = Number(help.match(/about ([\d.]+)x further/)![1]);
    expect(1 / STABILISATION.handwriting.gain).toBeCloseTo(quoted, 1);
  });

  it("calls Natural one-to-one because its gain is exactly 1", () => {
    expect(help).toMatch(/natural: "One-to-one/);
    expect(STABILISATION.natural.gain).toBe(1);
  });

  it("says the hand moves slightly further under Steady, which a gain under 1 means", () => {
    expect(help).toMatch(/steady:[\s\S]*slightly further than the pen/);
    expect(STABILISATION.steady.gain).toBeLessThan(1);
    expect(STABILISATION.steady.gain).toBeGreaterThan(STABILISATION.handwriting.gain);
  });
});
