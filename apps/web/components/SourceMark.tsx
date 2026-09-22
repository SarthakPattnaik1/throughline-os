"use client";

/**
 * Marks for the connectors.
 *
 * **These are not the sources' real logos, and that is deliberate.** arXiv,
 * PubMed, Crossref and the rest own their marks; reproducing them inside a
 * product implies an endorsement or an affiliation that does not exist. The
 * workspace also has to work with no network, so fetching a favicon is not an
 * option even where it would be allowed. So each source gets a monogram drawn
 * here — recognisable at 18px, honest about being ours.
 *
 * **The colour is deliberately weak.** The one rule the design system will not
 * bend on is that saturated colour belongs to data and chrome stays
 * near-neutral. Fourteen bright badges in a result list would compete with
 * every chart on the screen, so the hues below are low-chroma and do most of
 * their work at ~12% alpha behind a letterform. Recognition is carried by the
 * *letters*, which is why each monogram is one or two characters chosen to be
 * distinct at a glance: OA, CR, aX, PM, S2, EP, bR, DJ, AI, Zo, Ze, Dy, DV, Fs.
 *
 * The third field, `kind`, matters more than the colour. A result from a
 * dataset repository, a preprint server and a peer-reviewed index are three
 * different sorts of evidence, and the mark carries that distinction so a list
 * never flattens them into "sources".
 */

export type SourceKind = "index" | "preprint" | "dataset" | "personal";

type Mark = { label: string; hue: number; kind: SourceKind; title: string };

const MARKS: Record<string, Mark> = {
  openalex: { label: "OA", hue: 212, kind: "index",
              title: "OpenAlex — open index of scholarly works" },
  crossref: { label: "CR", hue: 28, kind: "index",
              title: "Crossref — the DOI registration agency" },
  arxiv: { label: "aX", hue: 356, kind: "preprint",
           title: "arXiv — preprints in physics, maths and computing" },
  pubmed: { label: "PM", hue: 202, kind: "index",
            title: "PubMed — biomedical literature index" },
  semanticscholar: { label: "S2", hue: 268, kind: "index",
                     title: "Semantic Scholar — citations and references" },
  europepmc: { label: "EP", hue: 168, kind: "index",
               title: "Europe PMC — biomedical records with open-access full text" },
  biorxiv: { label: "bR", hue: 14, kind: "preprint",
             title: "bioRxiv — preprints, not peer reviewed" },
  medrxiv: { label: "mR", hue: 4, kind: "preprint",
             title: "medRxiv — preprints, not peer reviewed" },
  doaj: { label: "DJ", hue: 130, kind: "index",
          title: "DOAJ — vetted open-access journals" },
  openaire: { label: "AI", hue: 190, kind: "index",
              title: "OpenAIRE — European research graph" },
  zotero: { label: "Zo", hue: 350, kind: "personal",
            title: "Your own Zotero library, read-only" },
  zenodo: { label: "Ze", hue: 220, kind: "dataset",
            title: "Zenodo — open deposit, anyone may upload" },
  dryad: { label: "Dy", hue: 96, kind: "dataset",
           title: "Dryad — curated data behind published papers" },
  dataverse: { label: "DV", hue: 42, kind: "dataset",
               title: "Dataverse — curated, with variable-level metadata" },
  figshare: { label: "Fs", hue: 288, kind: "dataset",
              title: "Figshare — open deposit" },
};

/** Unknown sources still get a mark rather than a hole in the row. */
function markFor(name: string): Mark {
  const key = name.toLowerCase().replace(/[^a-z0-9]/g, "");
  return MARKS[key] ?? {
    label: name.slice(0, 2).replace(/^./, (c) => c.toUpperCase()),
    hue: 220, kind: "index", title: name,
  };
}

export function SourceMark({ name, size = 18 }: { name: string; size?: number }) {
  const mark = markFor(name);
  return (
    <span
      className="smark"
      data-kind={mark.kind}
      title={mark.title}
      style={{
        width: size, height: size,
        fontSize: Math.round(size * 0.46),
        // oklch keeps every mark at the same perceived lightness, so no badge
        // shouts louder than its neighbour purely because of its hue.
        ["--smark-hue" as string]: String(mark.hue),
      }}
      aria-hidden
    >
      {mark.label}
    </span>
  );
}

/**
 * A source with its mark, its name and its state.
 *
 * `ok=false` is drawn differently from a zero count on purpose: "PubMed
 * returned nothing" and "PubMed did not answer" are different facts about the
 * world, and a researcher who reads the first when the second is true will
 * conclude the literature is empty.
 */
export function SourceChip({
  name, ok, count, note, polite, waiting,
}: {
  name: string;
  ok?: boolean;
  count?: number;
  note?: string | null;
  /** False when the source works but is throttled or unconfigured. */
  polite?: boolean;
  /** Asked, and has not answered yet (T196). */
  waiting?: boolean;
}) {
  const state = waiting ? "waiting"
    : ok === false ? "down" : polite === false ? "limited" : "ok";
  return (
    <span className="schip" data-state={state} title={note ?? markFor(name).title}>
      <SourceMark name={name} size={16} />
      <span className="schip-name">{name}</span>
      {count !== undefined && ok !== false && !waiting && (
        <span className="schip-count numeric">{count}</span>
      )}
      {waiting && <span className="schip-state">asking…</span>}
      {!waiting && ok === false && <span className="schip-state">did not answer</span>}
      {!waiting && ok !== false && polite === false && (
        <span className="schip-state">limited</span>
      )}
    </span>
  );
}

export { markFor };

/**
 * What a fan-out search is waiting on, while it waits (T196).
 *
 * Both searches ask ten or so databases at once and wait up to 25 seconds.
 * That is long enough that a four-row skeleton reads as a hang — and the one
 * on Find papers was captioned "Asking four databases" while asking ten, a
 * literal written when four was true. The count is taken from the list of
 * sources here so it cannot rot again, which is the repair `datasearch.tsx`
 * already made for itself and this is the other half of.
 *
 * Every source is named rather than counted, because the useful thing to know
 * during the wait is *who* — a researcher who sees PubMed in the list knows
 * the search covers what they care about before any result arrives. They are
 * all marked `asking…` rather than resolved one by one: the server fans out
 * and answers once, so per-source progress would be an animation of something
 * this screen cannot actually observe.
 */
export function SearchingSources({ names, deadline = 25, kind = "databases",
                                   whenUnknown }: {
  /** Every source being asked, in the order the screen lists them. */
  names: string[];
  /** Seconds before the search goes ahead without whatever is still silent. */
  deadline?: number;
  /**
   * What to call them, as a plural: "databases", "dataset repositories".
   *
   * Used verbatim in both sentences rather than singularised — stripping a
   * trailing "s" turned "repositories" into "repositorie", which is the kind
   * of thing that only shows up in the branch nobody looks at (the list's own
   * request failing).
   */
  kind?: string;
  /**
   * How to name them when the list could not be read, if "the {kind}" is not
   * what this screen calls them. Find data says "the dataset repositories",
   * which is more than "the repositories" and less than repeating it in the
   * counted sentence too.
   */
  whenUnknown?: string;
}) {
  return (
    <div className="searching" role="status" aria-live="polite">
      <p className="searching-line">
        <span className="searching-spinner" aria-hidden />
        {/* No count when the list could not be read: an invented number is the
            same false precision the caption's old literal had. */}
        {names.length > 0
          ? `Asking ${names.length} ${kind} at once…`
          : `Asking ${whenUnknown ?? `the ${kind}`}…`}
      </p>
      {names.length > 0 && (
        <p className="searching-chips">
          {names.map((name) => <SourceChip key={name} name={name} waiting />)}
        </p>
      )}
      <p className="searching-note">
        Whatever has answered within {deadline} seconds is shown. Any that are
        slow are named beside the results rather than quietly dropped, so a
        short list is never mistaken for a thin literature.
      </p>
    </div>
  );
}
