/**
 * One bar, and nothing behind it that the bar cannot reach (T189).
 *
 * The point of a single box is that a researcher never has to know the shape
 * of the navigation. That claim is only worth making if it is true of *every*
 * section, and it stops being true the moment somebody adds a sixteenth one
 * and wires it to the rail alone. So this walks `Shell.tsx`'s own tables —
 * the rail is the source of truth, not a list copied into the test — and
 * fails if anything on it cannot be asked for.
 *
 * This is the test that lets `verbs.ts` say it misses nothing.
 */

import { describe, expect, it } from "vitest";
import { PAGES, SECTIONS } from "@/components/Shell";
import { CARRIES_ARGUMENT, VERBS, matchVerbs, verbsByGroup } from "@/components/verbs";
import { VIEWS_OF } from "@/lib/section-url";

/** Every section a verb sends you to. */
const REACHED = new Set(VERBS.map((v) => v.to.section));

describe("every section on the rail can be asked for", () => {
  it("has a verb that goes there", () => {
    const stranded = SECTIONS.filter((s) => !REACHED.has(s.id));
    expect(stranded.map((s) => `${s.group} → ${s.label} (${s.id})`),
      "a rail section no verb reaches: add one to VERBS in components/verbs.ts").toEqual([]);
  });

  it("answers to its own name as it is written on the rail", () => {
    // Somebody who has seen the rail should be able to type what they saw.
    const unmatched: string[] = [];
    for (const section of SECTIONS) {
      const hits = matchVerbs(section.label);
      if (!hits.some((h) => h.verb.to.section === section.id)) {
        unmatched.push(`${section.label} (${section.id})`);
      }
    }
    expect(unmatched, "typing a rail label does not offer that section").toEqual([]);
  });
});

describe("the verbs are well formed", () => {
  it("names a destination the workspace has", () => {
    const known = new Set<string>([...SECTIONS.map((s) => s.id),
      // Sections that exist as views rather than rail rows.
      "search", "patterns", "embedding", "gallery", "activity",
      "literature", "datasearch", "readfigure"]);
    for (const verb of VERBS) {
      expect(known.has(verb.to.section), `${verb.id} → ${verb.to.section}`).toBe(true);
    }
  });

  it("names a view the destination section actually has", () => {
    /**
     * The hole this closes, found by walking all 22 destinations in the real
     * app rather than by reading the table (T198). Two verbs named views that
     * do not exist: "register a hypothesis" asked for `discover?view=register`
     * when Discovery has no views at all, and "what has happened here" asked
     * for `journal?view=activity` when journal's are `written` and `done`.
     * Both were silently dropped by the router, so each verb promised a place
     * and delivered its parent — the exact failure §123 is about, and one this
     * file could not see while it only checked `to.section`.
     */
    for (const verb of VERBS) {
      if (!verb.to.view) continue;
      const allowed: readonly string[] = VIEWS_OF[verb.to.section] ?? [];
      expect(allowed, `${verb.id} → ${verb.to.section} has no views`).not.toEqual([]);
      expect(allowed, `${verb.id} → ${verb.to.section}?view=${verb.to.view}`)
        .toContain(verb.to.view);
    }
  });

  it("only asks for a subject where the destination reads one", () => {
    /**
     * Found by reading the wiring rather than the table (T198): five verbs
     * declared `takes` and two destinations read it. "search my library <a
     * phrase>", "validate <which one>" and "write a note <the note>" all
     * advertised a subject that was dropped on arrival — the bar showing it
     * back to you in the offer, which made the drop look deliberate.
     */
    for (const verb of VERBS) {
      if (!verb.takes) continue;
      const carried = CARRIES_ARGUMENT.some((d) =>
        d.section === verb.to.section && d.view === verb.to.view);
      expect(carried,
        `${verb.id} asks for "${verb.takes}" but ${verb.to.section}`
        + `${verb.to.view ? "?view=" + verb.to.view : ""} never reads one`).toBe(true);
    }
  });

  it("gives every verb a distinct id and at least one trigger", () => {
    const ids = VERBS.map((v) => v.id);
    expect(new Set(ids).size, `duplicate verb id in ${ids.join(", ")}`).toBe(ids.length);
    for (const verb of VERBS) {
      expect(verb.triggers.length, verb.id).toBeGreaterThan(0);
      expect(verb.says.length, `${verb.id} must say what it does`).toBeGreaterThan(10);
    }
  });

  it("never lets one trigger belong to two verbs", () => {
    // A trigger owned twice is a coin toss for whoever types it.
    const owner = new Map<string, string>();
    const clashes: string[] = [];
    for (const verb of VERBS) {
      for (const trigger of verb.triggers) {
        const held = owner.get(trigger);
        if (held && held !== verb.id) clashes.push(`"${trigger}": ${held} and ${verb.id}`);
        owner.set(trigger, verb.id);
      }
    }
    expect(clashes).toEqual([]);
  });

  it("puts every verb in a group the help list shows", () => {
    const listed = verbsByGroup().flatMap((g) => g.verbs.map((v) => v.id));
    expect(listed.sort()).toEqual(VERBS.map((v) => v.id).sort());
  });
});

describe("asking in the words a researcher would use", () => {
  const asked: Array<[string, string]> = [
    ["find papers about soil fertility", "find-papers"],
    ["papers on rainfall", "find-papers"],
    ["find data", "find-data"],
    ["datasets about yield", "find-data"],
    ["upload", "add"],
    ["add a paper", "add"],
    ["what's next", "next"],
    ["what should I do", "next"],
    ["test everything", "discover"],
    ["what's related", "discover"],
    ["validate", "validate"],
    ["try to destroy", "validate"],
    ["write it up", "report"],
    ["draft a report", "report"],
    ["where did this come from", "graph"],
    ["connect a model", "settings"],
    ["download", "export"],
    ["register a hypothesis", "register"],
    ["columns", "variables"],
    ["make a figure", "figures"],
  ];

  for (const [line, verb] of asked) {
    it(`"${line}" offers ${verb} first`, () => {
      const hits = matchVerbs(line);
      expect(hits.length, "nothing matched").toBeGreaterThan(0);
      expect(hits[0].verb.id).toBe(verb);
    });
  }

  it("keeps the subject of the sentence as the argument", () => {
    expect(matchVerbs("find papers about soil fertility")[0].argument)
      .toBe("soil fertility");
    expect(matchVerbs("papers on rainfall")[0].argument).toBe("rainfall");
    // A connector is filler, not part of the topic.
    expect(matchVerbs("find data for maize yields")[0].argument).toBe("maize yields");
  });

  it("does not invent an argument for a verb that takes none", () => {
    const hit = matchVerbs("open the workboard please")[0];
    expect(hit.verb.id).toBe("board");
    expect(hit.argument).toBe("");
  });

  it("tolerates the politeness people type at a box", () => {
    expect(matchVerbs("can you find papers about maize")[0].verb.id).toBe("find-papers");
    expect(matchVerbs("how do I add a file")[0].verb.id).toBe("add");
    expect(matchVerbs("please validate")[0].verb.id).toBe("validate");
  });

  it("offers something while the word is still being typed", () => {
    expect(matchVerbs("val")[0].verb.id).toBe("validate");
    expect(matchVerbs("repo")[0].verb.id).toBe("report");
  });

  it("says nothing rather than guessing at an empty line", () => {
    expect(matchVerbs("")).toEqual([]);
    expect(matchVerbs("   ")).toEqual([]);
  });

  it("matches nothing on a line that is not a verb, leaving it to the name search", () => {
    // `yield_t_ha` is an object, and the palette's fuzzy name match owns it.
    expect(matchVerbs("yield_t_ha")).toEqual([]);
    expect(matchVerbs("conn_b5aa1ca011094feb")).toEqual([]);
  });
});

describe("the machine pages are not stranded either", () => {
  it("still has all three, for the bar to list beside the sections", () => {
    expect(PAGES.map((p) => p.href).sort())
      .toEqual(["/air-ink", "/charts-3d", "/gesture-check"]);
  });
});
