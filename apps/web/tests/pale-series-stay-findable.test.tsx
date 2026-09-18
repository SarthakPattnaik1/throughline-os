/**
 * A pale series stays findable, and an annotation survives a theme change.
 *
 * D416: Okabe–Ito yellow is 1.2–1.3:1 on the light grounds, so a seventh group
 * all but disappeared. The fill is kept — a group has one colour on screen and
 * on paper — and the mark is outlined; a line is drawn in the outline colour.
 *
 * The four ink colours were chosen for a light page; the default blue was
 * 2.3:1 on the dark canvas. The recorded colour is data and is never rewritten;
 * only its display takes a lighter step of the same hue.
 */

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Cartesian } from "@/components/charts/Cartesian";
import { INK_COLOURS, displayInk } from "@/lib/ink/layers";
import { PALE_SERIES_EDGE, categorical, seriesEdge, seriesStroke } from "@/lib/tokens";

function contrast(a: string, b: string): number {
  const lum = (hex: string) => [1, 3, 5]
    .map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
    .reduce((sum, c, i) => sum + c * [0.2126, 0.7152, 0.0722][i], 0);
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe("a pale series (D416)", () => {
  it("is outlined in a colour that shows on every light ground and against the fill", () => {
    const yellow = categorical[6];
    expect(seriesEdge(yellow)).toBe(PALE_SERIES_EDGE);
    for (const ground of ["#FFFFFF", "#FAFAF9", "#F4F4F2"]) {
      expect(contrast(PALE_SERIES_EDGE, ground)).toBeGreaterThanOrEqual(4);
    }
    expect(contrast(PALE_SERIES_EDGE, yellow)).toBeGreaterThanOrEqual(3);
    expect(seriesStroke(yellow)).toBe(PALE_SERIES_EDGE);
  });

  it("leaves every other series exactly as it was", () => {
    for (const hue of categorical.filter((_, i) => i !== 6)) {
      expect(seriesEdge(hue)).toBeNull();
      expect(seriesStroke(hue)).toBe(hue);
    }
  });

  it("outlines the seventh group's points in a real chart, and only those", () => {
    const groups = ["a", "b", "c", "d", "e", "f", "g"];
    const data = groups.map((g, i) => ({ id: g, x: i, y: i, group: g }));
    const { container } = render(
      <Cartesian mark="point" xLabel="x" yLabel="y" data={data} />);
    const points = [...container.querySelectorAll<SVGCircleElement>("circle.chart-point")];
    const outlined = points.filter((c) => c.style.stroke !== "");
    expect(outlined).toHaveLength(1);
    expect(outlined[0].style.fill.toUpperCase().replace(/\s/g, ""))
      .toMatch(/F0E442|RGB\(240,228,66\)/);
  });
});

describe("an annotation after a theme change", () => {
  it("shows each ink in a dark step that holds 7:1 on the dark canvas", () => {
    for (const ink of INK_COLOURS) {
      const shown = displayInk(ink.value, true);
      expect(shown, ink.name).not.toBe(ink.value);
      expect(contrast(shown, "#0E1116"), ink.name).toBeGreaterThanOrEqual(7);
    }
  });

  it("shows the recorded colour on a light page, and anything unlisted as recorded", () => {
    for (const ink of INK_COLOURS) expect(displayInk(ink.value, false)).toBe(ink.value);
    expect(displayInk("#123456", true)).toBe("#123456");
  });
});
