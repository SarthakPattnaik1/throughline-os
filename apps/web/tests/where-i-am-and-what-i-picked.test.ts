/**
 * Where I am is gold; what I picked is blue (D355).
 *
 * Navigation and data selection shared one accent, so nothing said *which
 * column did I choose* as distinct from *which screen am I on*. The theme has
 * a selection colour, `--select`; these are the places a researcher picks
 * data, and each must use it rather than the navigation accent.
 */

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync("app/globals.css", "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

function body(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = css.match(new RegExp(`(?:^|\\n)\\s*${escaped}\\s*\\{([^}]*)\\}`));
  expect(match, selector).not.toBeNull();
  return match![1];
}

const PICKED_DATA = [
  '.pick-option[data-on="true"]',   // columns chosen for an analysis
  '.cmp-choice[data-chosen="true"]', // papers chosen to compare
  '.sc-chip[data-on="true"]',       // covariates chosen for the spec curve
  '.rsn-claim[data-chosen="true"]', // the claim chosen to test
];

const WHERE_I_AM = [
  '.sectionbar-item[aria-current="true"]',
  '.tab[aria-selected="true"]',
];

describe("two colours for two different answers", () => {
  it("marks picked data in the selection colour, never the navigation accent", () => {
    for (const selector of PICKED_DATA) {
      const rule = body(selector);
      expect(rule, selector).toMatch(/var\(--select/);
      expect(rule, selector).not.toMatch(/var\(--accent/);
    }
  });

  it("keeps the navigation accent for where the reader is", () => {
    for (const selector of WHERE_I_AM) expect(body(selector), selector).toMatch(/var\(--accent/);
  });
});
