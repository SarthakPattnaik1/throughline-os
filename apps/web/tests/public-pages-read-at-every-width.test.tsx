/**
 * The public pages and first-run setup, as a live review of `main` found them (T180).
 *
 * happy-dom does no layout, so the landing page's width and the rendered
 * contrast were measured in a browser — the numbers are in the comments beside
 * each rule — and what is pinned here is the rule that produced them. Each
 * assertion fails when its rule is removed.
 */

import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { Gate } from "@/components/Gate";

vi.mock("@/lib/spatial/mediapipe", () => ({
  HandTracker: class { static supported() { return false; } },
}));

const entrance = readFileSync("app/entrance.css", "utf8");
const globals = readFileSync("app/globals.css", "utf8");

/** The declarations of every rule whose selector is exactly `selector`, joined. */
function rule(css: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\>]/g, "\\$&");
  const pattern = new RegExp(`(?:^|\\n)\\s*${escaped}\\s*\\{([^}]*)\\}`, "g");
  return [...css.matchAll(pattern)].map((m) => m[1]).join("\n");
}

describe("the landing page never widens a phone", () => {
  it("clips each panel's overflow, so a scrim past the screen edge cannot widen the page", () => {
    // Measured at 375px: the quote scrims ran 74px past their boxes, the layout
    // viewport grew to 425px, and the fixed header put *Open workspace* at 409.
    expect(rule(entrance, ".panel")).toMatch(/overflow-x:\s*clip/);
  });

  it("gives touch screens 44px targets without changing the desktop composition", () => {
    const coarse = entrance.match(/@media \(pointer: coarse\) \{([\s\S]*?)\n\}/);
    expect(coarse, "a coarse-pointer block").not.toBeNull();
    expect(coarse![1]).toMatch(/\.marks-form, \.motion-toggle, \.enter \{ min-height: 44px; \}/);
  });

  it("fades the stacked word list on every side it does not share with the screen", () => {
    // A linear scrim left a hard top and bottom edge across the light band.
    const narrow = entrance.match(/@media \(max-width: 900px\), \(max-height: 620px\) \{([\s\S]*?)\n\}/);
    expect(narrow).not.toBeNull();
    const words = narrow![1].match(/\.words \{([\s\S]*?)\}/);
    expect(words![1]).toMatch(/margin-right:\s*calc\(-1 \* var\(--e-gutter\)\)/);
    expect(words![1]).toMatch(/radial-gradient\([\s\S]*rgb\(6 7 10 \/ 0\) \d+%\)/);
  });
});

describe("first-run setup", () => {
  const setup = () => renderToStaticMarkup(
    <Gate status={{ needs_setup: true, authenticated: false, user: null } as never}
          onDone={() => {}} />);

  it("says the first account is the administrator", () => {
    // Since T166 it controls the machine; the screen that creates it said only
    // that it "scopes your projects".
    expect(setup()).toMatch(/is its administrator/);
  });

  it("lets the browser fill the name", () => {
    expect(setup()).toMatch(/<input type="text" autoComplete="name"|autocomplete="name"/i);
  });

  it("styles the hint as a sentence, not as a field label", () => {
    // `.gate-field > span` outranked a bare `.gate-hint`: 10px, uppercase, bold.
    const hint = rule(globals, ".gate-field > .gate-hint");
    expect(hint).toMatch(/text-transform:\s*none/);
    expect(hint).toMatch(/letter-spacing:\s*normal/);
    expect(hint).toMatch(/font-weight:\s*400/);
  });

  it("keeps each sign-in choice on one line, stacking the pair instead (T183)", () => {
    // At a 1024px window both labels wrapped inside their buttons.
    expect(rule(globals, ".gate-modes")).toMatch(/flex-wrap:\s*wrap/);
    const buttons = rule(globals, ".gate-modes > .btn");
    expect(buttons).toMatch(/white-space:\s*nowrap/);
    expect(buttons).toMatch(/flex:\s*1 1 auto/);
    expect(globals).toMatch(/@media \(pointer: coarse\) \{ \.gate-modes > \.btn \{ min-height: 44px; \} \}/);
  });

  it("puts the whole form on a phone's first screen", () => {
    // At 375x812 the 42vh sky band put the password and the button below the
    // fold. Measured after the change: the button ends at 774px of 812.
    const sky = readFileSync("app/sky.css", "utf8");
    expect(sky).toMatch(/--sky-band:\s*max\(26vh, 170px\)/);
    expect(rule(sky, ".gate.gate-sky")).toMatch(/grid-template-rows:\s*var\(--sky-band\) auto/);
    expect(rule(sky, ".gate.gate-sky > .sky > .sky-stage")).toMatch(/height:\s*var\(--sky-band\)/);
    expect(sky.replace(/\/\*[\s\S]*?\*\//g, "")).not.toMatch(/42vh/);
  });

  it("gives Back a touch-sized target", () => {
    const coarse = globals.match(/@media \(pointer: coarse\) \{\s*\.gate-back \{([^}]*)\}/);
    expect(coarse, "a coarse-pointer rule for .gate-back").not.toBeNull();
    expect(coarse![1]).toMatch(/min-height:\s*44px/);
  });

  it("sets its small text at an ink that measured 4.5:1 or better", () => {
    // 42% measured 3.79:1 and 25% measured 2.15:1 on this ground; 56% is 5.8:1.
    for (const selector of [".gate-field > span", ".gate-field input::placeholder",
                            ".gate-field > .gate-hint", ".gate-back"]) {
      const declarations = rule(globals, selector);
      expect(declarations, selector).toMatch(/color:\s*var\(--gate-ink-56\)/);
    }
  });
});

describe("the spatial charts page names the chart a hand is on", () => {
  const page = readFileSync("app/charts-3d/page.tsx", "utf8");

  it("names every chart hand control drives", () => {
    // The globe was driven and unnamed, so addressing it read "—".
    const driven = page.match(/alsoControls=\{\[([^\]]*)\]\}/)![1]
      .split(",").map((name) => name.trim()).filter(Boolean);
    for (const ref of ["network", ...driven]) {
      expect(page, ref).toMatch(new RegExp(`active === ${ref}\\.current \\? "the `));
    }
  });

  it("never prints a bare dash as the chart's name", () => {
    expect(page).not.toMatch(/setAddressing\(\s*"—"|\? "—"|: "—"\)/);
    expect(page).toMatch(/No chart is being addressed yet/);
  });
});
