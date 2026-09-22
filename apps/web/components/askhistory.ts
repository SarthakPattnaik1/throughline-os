/**
 * What this person has asked the bar for, most recent first (T195).
 *
 * The bar answers "what do I want to do"; this answers "where was I". Both
 * are navigation, and the second is the one a rail used to provide by simply
 * staying on screen — you could see Connections was a place because it was
 * always listed. A single box has no such furniture, so it has to remember.
 *
 * ## Why it is stored per viewer and not on the server
 *
 * This is a trail through one person's own screens on one machine. It is not
 * research: nothing here is evidence, nothing is cited, and losing it costs a
 * convenience rather than a record. The project's real history — what was run,
 * what was written, what was decided — is the journal and the activity log,
 * which are server-side, permanent and never edited. Putting a UI breadcrumb
 * in the same store would mix the two, and the ledger's whole value is that
 * everything in it was a decision somebody made about the research.
 *
 * So: `localStorage`, keyed by project, wrapped in try/catch at every access.
 * A private window, blocked site data, or a preview frame all make it throw or
 * come back empty, and the bar has to work exactly as well without it.
 */

/** One thing that was asked for, in a form that can be run again. */
export type AskEntry =
  /* A verb keeps its id and subject, so "find papers about soil" replays as
     that search rather than as the bare screen. */
  | { kind: "verb"; verbId: string; argument: string; label: string; at: number }
  /* An object keeps its command id. The label is stored too, so a trail entry
     still reads as something when the object it named has been deleted. */
  | { kind: "object"; commandId: string; label: string; at: number };

/**
 * An entry before it is stamped.
 *
 * Written as a distributed Omit rather than `Omit<AskEntry, "at">`, because
 * Omit over a union collapses it to the fields the members share — which loses
 * `verbId` and `commandId` and makes every call site an error.
 */
export type NewAsk =
  | Omit<Extract<AskEntry, { kind: "verb" }>, "at">
  | Omit<Extract<AskEntry, { kind: "object" }>, "at">;

/** How many to keep. Enough to retrace a session, short enough to scan. */
export const KEPT = 8;

function key(projectId: string): string {
  return `throughline.ask.${projectId}`;
}

/** Read the trail. Always an array, even when storage is unavailable. */
export function readHistory(projectId: string): AskEntry[] {
  try {
    const raw = window.localStorage.getItem(key(projectId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    // Anything that does not look like an entry is dropped rather than
    // rendered: this is parsed from storage a previous version wrote.
    return parsed.filter((e): e is AskEntry =>
      e && typeof e === "object" && typeof e.label === "string"
      && (e.kind === "verb" || e.kind === "object")).slice(0, KEPT);
  } catch {
    return [];
  }
}

/**
 * Add one, newest first, with the same thing asked twice collapsing to once.
 *
 * Repeats collapse because a trail that reads "Find papers, Find papers, Find
 * papers" is a worse answer to "where was I" than one that reads "Find papers,
 * Connections, harvest.csv" — the point is the path, not the frequency.
 */
export function remember(projectId: string, entry: NewAsk): AskEntry[] {
  const now: AskEntry = { ...entry, at: Date.now() } as AskEntry;
  const same = (a: AskEntry, b: AskEntry) =>
    a.kind === b.kind
    && (a.kind === "verb" && b.kind === "verb"
      ? a.verbId === b.verbId && a.argument === b.argument
      : a.kind === "object" && b.kind === "object"
        ? a.commandId === b.commandId : false);
  const next = [now, ...readHistory(projectId).filter((e) => !same(e, now))].slice(0, KEPT);
  try {
    window.localStorage.setItem(key(projectId), JSON.stringify(next));
  } catch {
    // Storage refused. The list is still right for this page's lifetime.
  }
  return next;
}

/** Forget the trail for one project. */
export function clearHistory(projectId: string): void {
  try {
    window.localStorage.removeItem(key(projectId));
  } catch {
    // Nothing to do: it was never written.
  }
}
