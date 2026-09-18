/**
 * A canvas chart marks what was picked in the theme's own selection colour.
 *
 * The surface and volume charts hard-coded `#1443B8` for their selection and
 * hover rings: 8:1 on the light canvas and 2.3:1 on the dark one, under the
 * 3:1 a meaningful graphic needs — in the dark theme, which point was picked
 * was hard to see. They now read `--select` at draw time.
 */

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { SELECT_FALLBACK, selectionColour } from "@/lib/charts/theme";

const css = readFileSync("app/globals.css", "utf8");

function contrast(a: string, b: string): number {
  const lum = (hex: string) => {
    const [r, g, bl] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
      .map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl;
  };
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** Every value a custom property takes across the stylesheet, in order. */
const values = (name: string) =>
  [...css.matchAll(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`, "g"))].map((m) => m[1]);

describe("selection on a canvas", () => {
  it("reads the theme's --select", () => {
    const canvas = document.createElement("canvas");
    canvas.style.setProperty("--select", "#a8c6e6");
    document.body.appendChild(canvas);
    expect(selectionColour(canvas)).toBe("#a8c6e6");
    expect(selectionColour(null)).toBe(SELECT_FALLBACK);
  });

  it("is visible on the canvas in both themes", () => {
    const [lightSelect, ...darkSelects] = values("--select");
    const [lightCanvas, ...darkCanvases] = values("--n-25");
    expect(lightSelect).toBe(SELECT_FALLBACK);
    expect(contrast(lightSelect, lightCanvas)).toBeGreaterThanOrEqual(3);
    for (const dark of darkSelects) {
      for (const ground of darkCanvases) {
        expect(contrast(dark, ground)).toBeGreaterThanOrEqual(3);
      }
    }
  });

  it("is never the old hard-coded blue in a chart", () => {
    for (const file of ["Surface", "Volume"]) {
      const source = readFileSync(`components/charts/${file}.tsx`, "utf8");
      expect(source, file).not.toMatch(/#1443B8|20,\s*67,\s*184/);
    }
  });
});
