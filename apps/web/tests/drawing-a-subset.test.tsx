/**
 * Turning a region drawn on a figure into a recorded subset.
 *
 * This is the gesture the subset tree exists for. Without it a researcher
 * draws a region, reads two numbers off the readout, and retypes them into a
 * form — the same subset described twice, and the second description is the
 * one that will be wrong.
 *
 * Two things must not slip. The subset is defined on the **column**, never on
 * the axis label, which is what a reader should call it and frequently not
 * what the data calls it. And the count comes back from the server, because a
 * subset counted against the points on screen would be counting a sample and
 * calling it the dataset.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Cartesian } from "@/components/charts/Cartesian";

const DATA = Array.from({ length: 40 }, (_, i) => ({
  id: `p${i}`, x: i, y: i % 5,
}));

function brush(container: HTMLElement) {
  const surface = container.querySelector("rect.chart-brush-surface")!;
  fireEvent.mouseDown(surface, { clientX: 0 });
  fireEvent.mouseMove(surface, { clientX: 200 });
  fireEvent.mouseUp(surface);
}

describe("drawing a subset", () => {
  it("offers to record a region once one is drawn", () => {
    const { container } = render(
      <Cartesian data={DATA} mark="point" xLabel="Age" yLabel="y"
                 onRecordRegion={() => {}} />);

    expect(screen.queryByRole("button", { name: /record as a subset/i }))
      .toBeNull();

    brush(container);

    expect(screen.getByRole("button", { name: /record as a subset/i }))
      .toBeTruthy();
  });

  it("offers nothing when the caller cannot record one", () => {
    /**
     * A control that cannot work teaches a researcher the feature is broken
     * rather than inapplicable.
     */
    const { container } = render(
      <Cartesian data={DATA} mark="point" xLabel="Age" yLabel="y" />);

    brush(container);

    expect(screen.queryByRole("button", { name: /record as a subset/i }))
      .toBeNull();
  });

  it("hands back the range in data units, and nothing else", () => {
    /**
     * The chart does not know which column it draws — `xLabel` is a display
     * name — so it reports the range and lets the caller, which knows the
     * field, define the subset.
     */
    const recorded: Array<{ from: number; to: number }> = [];
    const { container } = render(
      <Cartesian data={DATA} mark="point" xLabel="Age" yLabel="y"
                 onRecordRegion={(range) => recorded.push(range)} />);

    brush(container);
    fireEvent.click(screen.getByRole("button", { name: /record as a subset/i }));

    expect(recorded).toHaveLength(1);
    expect(recorded[0].from).toBeLessThan(recorded[0].to);
    expect(Object.keys(recorded[0]).sort()).toEqual(["from", "to"]);
  });

  it("reports the same range it printed in the readout", async () => {
    // The number the researcher read and the number that defines the subset
    // have to be one number, or the subset is not the region they drew.
    const recorded: Array<{ from: number; to: number }> = [];
    const { container } = render(
      <Cartesian data={DATA} mark="point" xLabel="Age" yLabel="y"
                 onRecordRegion={(range) => recorded.push(range)} />);

    brush(container);
    const said = screen.getByRole("status").textContent ?? "";
    fireEvent.click(screen.getByRole("button", { name: /record as a subset/i }));

    const shown = /Age ([-\d.]+) to ([-\d.]+)/.exec(said);
    expect(shown, `readout did not name the range: ${said}`).not.toBeNull();
    expect(recorded[0].from).toBeCloseTo(Number(shown![1]), 2);
    expect(recorded[0].to).toBeCloseTo(Number(shown![2]), 2);
  });

  it("stops offering once the region is cleared", () => {
    const { container } = render(
      <Cartesian data={DATA} mark="point" xLabel="Age" yLabel="y"
                 onRecordRegion={() => {}} />);
    brush(container);

    fireEvent.click(screen.getByRole("button", { name: /^clear$/i }));

    expect(screen.queryByRole("button", { name: /record as a subset/i }))
      .toBeNull();
  });
});
