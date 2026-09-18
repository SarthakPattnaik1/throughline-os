/**
 * Every class a stylesheet defines is one the interface can put on an element.
 *
 * `css-classes.test.ts` checks one direction: a class in the markup has a
 * rule. Nothing checked the other, so rules outlived their markup — the rail's
 * 24 (D356), then 35 more across eleven features, and the comments above them
 * describing things that no longer existed. A class counts as used when the
 * source names it, or builds it from a prefix at runtime (`status-${…}`,
 * `"rc-" + tone`), which is how the verdict, status and tone classes are made.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

function files(dir: string, pattern: RegExp): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path, pattern)
      : pattern.test(name) && !name.includes(".test.") ? [path] : [];
  });
}

const css = files("app", /\.css$/).map((f) => readFileSync(f, "utf8")).join("\n")
  .replace(/\/\*[\s\S]*?\*\//g, "");
const source = ["components", "app", "lib"]
  .flatMap((d) => files(d, /\.(tsx?|js)$/)).map((f) => readFileSync(f, "utf8")).join("\n");

function defined(): Set<string> {
  const names = new Set<string>();
  for (const [, selector] of css.matchAll(/([^{}]+)\{/g)) {
    if (selector.trim().startsWith("@")) continue;
    for (const [, name] of selector.matchAll(/\.([a-zA-Z_][\w-]*)/g)) names.add(name);
  }
  return names;
}

const prefixes = new Set([
  ...[...source.matchAll(/[`"' ]([a-z][\w-]*-)\$\{/g)].map((m) => m[1]),
  ...[...source.matchAll(/["']([a-z][\w-]*-)["']\s*\+/g)].map((m) => m[1]),
]);

const named = (name: string) =>
  new RegExp(`(?<![\\w-])${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![\\w-])`).test(source);

describe("the stylesheets", () => {
  it("define no class the interface cannot use", () => {
    const unused = [...defined()].filter((name) =>
      !named(name) && ![...prefixes].some((prefix) => name.startsWith(prefix)));
    expect(unused).toEqual([]);
  });

  it("can see a class that is only built at runtime", () => {
    // The guard is only right if it does not call these dead.
    expect([...prefixes]).toEqual(expect.arrayContaining(["status-", "vd-"]));
  });
});
