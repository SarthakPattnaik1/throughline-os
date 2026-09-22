/**
 * What this product can be asked to do, as a table (T189).
 *
 * The command palette reaches every *noun* — sources, runs, connections,
 * findings, reports, figures, and the twenty-two sections — by name. It
 * reaches no *verbs* at all. So a researcher who knows exactly what they want
 * to do and not where it lives had nothing to type: "find papers about soil"
 * matched nothing, because nothing in the project is called that.
 *
 * That is the whole distance between a finder and a front door, and it is why
 * this product needs a rail of fifteen entries while a chat product needs one
 * box. The box is not simpler because it does less; it is simpler because the
 * verbs are typed rather than navigated to.
 *
 * ## No model is required, and none is pretended
 *
 * §69 wants natural-language intent parsing; it needs a model, and the command
 * index has never pretended to do it. That is still true and this table does
 * not change it. What it does is narrower and
 * completely deterministic: a verb, and the rest of the line as its argument.
 * "find papers about soil fertility" is a prefix match and a string, not a
 * parse. When no model is configured — the default install — every verb here
 * still works, because every one is a route or a screen, not a completion.
 *
 * ## The rule that keeps this honest
 *
 * Every section in `Shell.tsx` must be reachable from this table or by its own
 * name, and every verb must name a destination that exists.
 * `the-bar-reaches-everything.test.ts` enumerates both and fails if a section
 * is added without a way to ask for it. That test is the reason this file can
 * claim to miss nothing.
 */

import type { Section } from "./Shell";

/** Where a verb goes, in the workspace's own terms. */
export type Destination = {
  section: Section;
  /** The sub-tab within the section, where it has them. */
  view?: string;
  /**
   * What to do on arrival, beyond showing the screen.
   *
   * `upload` opens the file chooser and `next` follows the loop's own next
   * step. Anything else is a plain navigation, which is most of them.
   *
   * Deliberately no "run": a sweep is real compute and a validation is a real
   * claim, and a bar that starts either from a typed line — on a guess it made
   * about what the line meant — is the behaviour that makes a single box
   * untrustworthy. The verb takes you to the control; you press it.
   */
  act?: "upload" | "next";
};

export type Verb = {
  id: string;
  /** How the bar names it in a list. */
  label: string;
  /** One line saying what pressing it does. Shown under the label. */
  says: string;
  /**
   * The words that select it, canonical first.
   *
   * Written as a researcher would say the thing, not as the screen is titled:
   * somebody wanting the literature search types "find papers", "search
   * papers", "look for studies" or just "papers", and all four have to land.
   */
  triggers: readonly string[];
  /**
   * What the rest of the line means, when it means anything.
   *
   * Present on verbs that take a subject — "find papers about **soil**" — and
   * absent on the ones that do not, so the bar knows whether to keep typing
   * into an argument or to stop at the verb.
   */
  takes?: string;
  to: Destination;
  /** Which part of the work this belongs to, for the "what can I ask?" list. */
  group: string;
};

/**
 * Every verb, grouped the way the work runs rather than the way the rail is
 * filed. The order inside a group is the order somebody would do them.
 */
export const VERBS: readonly Verb[] = [
  // ---- getting things in ------------------------------------------------
  {
    id: "add",
    label: "Add a file",
    says: "Choose a spreadsheet or a PDF from this machine",
    triggers: ["add data", "add a file", "add file", "upload", "import a file",
               "add a dataset", "add dataset", "add a paper", "add paper", "add"],
    to: { section: "sources", view: "library", act: "upload" },
    group: "Get evidence in",
  },
  {
    id: "library",
    label: "See what I have",
    says: "The papers and datasets already in this project",
    triggers: ["sources", "my sources", "library", "my library", "see what i have",
               "what have i got", "my data", "my papers"],
    to: { section: "sources", view: "library" },
    group: "Get evidence in",
  },
  {
    id: "find-papers",
    label: "Find papers",
    says: "Search the open literature and add what you keep",
    triggers: ["find papers", "search papers", "find studies", "search literature",
               "look for papers", "literature", "papers about", "papers on", "papers"],
    takes: "a topic",
    to: { section: "sources", view: "papers" },
    group: "Get evidence in",
  },
  {
    id: "find-data",
    label: "Find data",
    says: "Search open data repositories and import a file",
    triggers: ["find data", "search data", "find a dataset", "find datasets",
               "open data", "datasets about", "datasets on", "datasets"],
    takes: "a topic",
    to: { section: "sources", view: "data" },
    group: "Get evidence in",
  },
  {
    id: "read-figure",
    label: "Read a figure",
    says: "Pull the numbers out of a chart in a paper",
    triggers: ["read a figure", "read figure", "digitise", "digitize",
               "extract a chart", "chart from a paper"],
    to: { section: "sources", view: "figure" },
    group: "Get evidence in",
  },
  {
    id: "search-library",
    label: "Search what I have",
    says: "Search inside the papers and datasets already in this project",
    triggers: ["search my library", "search these", "search the library",
               "search my papers", "find in my papers"],
    takes: "a phrase",
    to: { section: "sources", view: "search" },
    group: "Get evidence in",
  },

  // ---- looking at the data ----------------------------------------------
  {
    id: "variables",
    label: "See the columns",
    says: "What each column holds, and what it has been agreed to mean",
    triggers: ["columns", "variables", "schema", "fields", "see the columns"],
    to: { section: "variables" },
    group: "Look at the data",
  },
  {
    id: "compare",
    label: "Compare a paper with my data",
    says: "Put a paper's reported numbers beside your own",
    triggers: ["compare", "compare a paper", "paper vs data", "check against a paper"],
    to: { section: "compare" },
    group: "Look at the data",
  },

  // ---- finding and testing ----------------------------------------------
  {
    id: "discover",
    label: "Test every pair of columns",
    says: "Opens the sweep that tries each pair and corrects for how many ran",
    triggers: ["discover", "discovery", "find connections", "test everything",
               "test every pair", "what is related", "what's related",
               "find relationships", "sweep"],
    to: { section: "discover" },
    group: "Find and test",
  },
  {
    id: "analyses",
    label: "See the analysis runs",
    says: "Every run, with its seed, its assumptions and its result",
    triggers: ["analyses", "runs", "analysis runs", "see the runs", "my analyses"],
    to: { section: "analyses" },
    group: "Find and test",
  },
  {
    id: "register",
    label: "Register a hypothesis",
    says: "Write down the prediction before the test, so it cannot move",
    triggers: ["register a hypothesis", "preregister", "pre-register",
               "register", "hypothesis"],
    to: { section: "discover", view: "register" },
    group: "Find and test",
  },
  {
    id: "patterns",
    label: "See the pattern sweep",
    says: "Which pairs the sweep proposed, and how the runs sit together",
    triggers: ["patterns", "pattern sweep", "specification curve", "sweep results"],
    to: { section: "analyses", view: "patterns" },
    group: "Find and test",
  },

  // ---- standing it up ---------------------------------------------------
  {
    id: "connections",
    label: "See the connections",
    says: "Every tested relationship, corrected, with how far it has got",
    triggers: ["connections", "relationships", "what did we find",
               "see the connections", "results"],
    to: { section: "connections" },
    group: "Stand it up",
  },
  {
    id: "validate",
    label: "Try to destroy a result",
    says: "Bootstrap, outliers, missingness and confounders — promotion is earned",
    triggers: ["validate", "try to destroy", "robustness", "stress test",
               "check it holds", "test it harder"],
    takes: "which one",
    to: { section: "connections" },
    group: "Stand it up",
  },
  {
    id: "findings",
    label: "See the findings",
    says: "What has been recorded as a finding, and what stands behind it",
    triggers: ["findings", "record a finding", "my findings", "what stands"],
    to: { section: "findings" },
    group: "Stand it up",
  },
  {
    id: "graph",
    label: "Show where it came from",
    says: "What was derived from what, including the branches set aside",
    triggers: ["lineage", "provenance", "research graph", "where did this come from",
               "how did we get here", "graph", "river"],
    to: { section: "graph", view: "river" },
    group: "Stand it up",
  },

  // ---- writing it up ----------------------------------------------------
  {
    id: "report",
    label: "Draft a report",
    says: "A write-up that references its findings rather than copying them",
    triggers: ["draft a report", "write a report", "reports", "report",
               "write it up", "write up", "paper draft"],
    to: { section: "reports" },
    group: "Write it up",
  },
  {
    id: "figures",
    label: "Make a figure",
    says: "Draw a result, and keep the numbers behind the picture",
    triggers: ["figures", "figure", "chart", "plot", "draw", "graph it",
               "make a figure", "visualise", "visualize"],
    to: { section: "figures" },
    group: "Write it up",
  },
  {
    id: "notebook",
    label: "Open the notebook",
    says: "Your pages, and what each one links to",
    triggers: ["notebook", "my notes", "pages"],
    to: { section: "notebook" },
    group: "Write it up",
  },
  {
    id: "note",
    label: "Write a note",
    says: "A permanent entry — notes are never edited, only added to",
    triggers: ["write a note", "note", "journal", "record a note"],
    takes: "the note",
    to: { section: "journal" },
    group: "Write it up",
  },
  {
    id: "export",
    label: "Take it away",
    says: "The results table, the bibliography and a snapshot, as files",
    triggers: ["export", "download", "take it away", "csv", "bibliography",
               "bib", "snapshot", "save to disk"],
    to: { section: "reports" },
    group: "Write it up",
  },
  {
    id: "activity",
    label: "What has happened here",
    says: "Everything written and done in this project, in order",
    triggers: ["activity", "history", "what happened", "log", "record"],
    to: { section: "journal", view: "activity" },
    group: "Write it up",
  },

  // ---- the project and the machine --------------------------------------
  {
    id: "overview",
    label: "Where this project stands",
    says: "The loop, what has been found, and what is waiting",
    triggers: ["overview", "where am i", "status", "summary", "the project",
               "how is it going", "home"],
    to: { section: "overview" },
    group: "This project",
  },
  {
    id: "next",
    label: "What should I do next?",
    says: "Follow the one step the project is actually waiting on",
    triggers: ["what next", "what's next", "whats next", "next step", "next",
               "what should i do", "what do i do"],
    to: { section: "overview", act: "next" },
    group: "This project",
  },
  {
    id: "board",
    label: "Open the workboard",
    says: "Arrange this project's objects by hand",
    triggers: ["workboard", "board", "canvas", "arrange"],
    to: { section: "board" },
    group: "This project",
  },
  {
    id: "settings",
    label: "Settings",
    says: "Feature packs, the model provider, and this installation",
    triggers: ["settings", "preferences", "configure", "connect a model",
               "add a model", "model", "install a pack", "packs"],
    to: { section: "settings" },
    group: "This machine",
  },
];

/** Every destination a verb can send you to, for the coverage test. */
export const VERB_SECTIONS: readonly Section[] =
  [...new Set(VERBS.map((v) => v.to.section))];

/** One match: a verb, and whatever the line said after it. */
export type VerbMatch = {
  verb: Verb;
  /** The rest of the line, trimmed, or "" when the verb took nothing. */
  argument: string;
  /** Lower is better. */
  score: number;
};

/** Strip the filler a person types around a verb without meaning anything by it. */
function normalise(query: string): string {
  let line = query.toLowerCase().replace(/[?!.]+$/g, "").trim();
  // Politeness, front and back. People type it at a box and mean nothing by it.
  line = line.replace(
    /^(please|can you|could you|i want to|i'd like to|id like to|help me|how do i|how can i|let's|lets)\s+/i, "");
  line = line.replace(/[\s,]+(please|thanks|thank you)$/i, "");
  // "open the workboard" is the workboard. An opener carries no meaning of its
  // own here — every verb in this table opens something.
  line = line.replace(/^(open|show me|show|go to|take me to|jump to|bring up)\s+/i, "");
  line = line.replace(/^(the|my|a|an)\s+/i, "");
  return line.trim();
}

/** The words that introduce a verb's subject, removed from the argument. */
const CONNECTORS = /^(about|on|for|of|with|to|in|into|re)\s+/i;

/**
 * Rank the verbs against a typed line.
 *
 * Three ways to match, in strength order: the line begins with a trigger (so
 * the rest is the argument); the line is a prefix of a trigger (somebody
 * halfway through typing it); or the trigger's words all appear in the line.
 * No fuzzy subsequence here — the palette does that for names, and a verb
 * matched by scattered letters would offer to run something nobody asked for.
 */
export function matchVerbs(query: string, limit = 6): VerbMatch[] {
  const line = normalise(query);
  if (!line) return [];
  const out: VerbMatch[] = [];

  for (const verb of VERBS) {
    let best: VerbMatch | null = null;
    for (let i = 0; i < verb.triggers.length; i++) {
      const trigger = verb.triggers[i];
      // Later triggers are looser synonyms, so they score slightly worse and a
      // verb's canonical wording wins a tie.
      const rank = i * 0.5;
      let match: VerbMatch | null = null;

      if (line === trigger) {
        match = { verb, argument: "", score: rank };
      } else if (line.startsWith(trigger + " ")) {
        const rest = line.slice(trigger.length + 1).replace(CONNECTORS, "").trim();
        // A verb that takes nothing still matches, but the trailing words are
        // not silently thrown away — they are offered to the name search too.
        match = { verb, argument: verb.takes ? rest : "", score: 1 + rank };
      } else if (trigger.startsWith(line)) {
        match = { verb, argument: "", score: 3 + rank + (trigger.length - line.length) / 100 };
      } else {
        const words = line.split(/\s+/);
        const triggerWords = trigger.split(/\s+/);
        if (triggerWords.length > 1 && triggerWords.every((w) => words.includes(w))) {
          match = { verb, argument: "", score: 6 + rank };
        }
      }
      if (match && (!best || match.score < best.score)) best = match;
    }
    if (best) out.push(best);
  }

  return out.sort((a, b) => a.score - b.score).slice(0, limit);
}

/**
 * The verbs as a teachable list, grouped, for "what can I ask?".
 *
 * A bar with no visible vocabulary is a guessing game — which is the one way a
 * single box is genuinely worse than a rail. This is what makes it not one.
 */
export function verbsByGroup(): Array<{ group: string; verbs: Verb[] }> {
  const groups: string[] = [];
  for (const verb of VERBS) if (!groups.includes(verb.group)) groups.push(verb.group);
  return groups.map((group) => ({
    group, verbs: VERBS.filter((v) => v.group === group),
  }));
}
