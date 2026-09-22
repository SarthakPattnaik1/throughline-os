"use client";

/**
 * One bar, and everything behind it (T189).
 *
 * ## Why
 *
 * This product grew a rail of fifteen sections in six groups, each with its
 * own sub-tabs, and every one of them is there for a reason. The trouble is
 * that a researcher arriving for the first time has to learn that shape before
 * they can do the first thing — and the first thing is usually "I have a
 * spreadsheet" or "find me papers about X", neither of which names a section.
 *
 * A chat product solves this with one box. It is not simpler because it can do
 * less; it is simpler because the verbs are typed instead of navigated to. So:
 * one box, and a table of verbs behind it (`verbs.ts`).
 *
 * ## What it is not
 *
 * It is not a chatbot, and it does not pretend to understand a sentence. The
 * matching is a verb and an argument — deterministic, and working in the
 * default install where no model provider is configured. Everything it offers,
 * it offers by name, before you press it. Nothing is guessed and then done.
 *
 * ## What keeps it from being a guessing game
 *
 * A box with no visible vocabulary is worse than a rail, because a rail at
 * least shows you what exists. Three things stop that here:
 *
 *  - every keystroke lists what it would do, with a sentence saying what that
 *    means, so the vocabulary is learned by using it;
 *  - the chips under an empty box are the things worth doing *now*, taken from
 *    the project's own loop rather than from a fixed list;
 *  - "What can I ask?" opens the whole verb table, grouped.
 *
 * And the rail is still there. This is a front door, not a replacement: T139's
 * rule that nothing is hidden applies to navigation too.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Command, rank } from "./CommandPalette";
import { Verb, VERBS, matchVerbs, verbsByGroup } from "./verbs";
import type { Destination } from "./verbs";
import { AskEntry, readHistory, remember } from "./askhistory";

/** One thing the bar is offering to do. */
export type Offer =
  | { kind: "verb"; verb: Verb; argument: string }
  | { kind: "object"; command: Command };

/**
 * What the bar would do with this line, best first.
 *
 * Verbs come before names because a line that reads as an instruction is an
 * instruction: somebody typing "find papers" wants the search, not a source
 * that happens to have "papers" in its title. Names still appear underneath,
 * and a line that is not a verb at all is pure name search — which is what
 * makes the bar a superset of the palette rather than a different thing.
 */
export function offersFor(query: string, commands: Command[], limit = 8): Offer[] {
  const verbs = matchVerbs(query, 5);
  const trimmed = query.trim();
  if (!trimmed) return [];
  const named = rank(commands, trimmed).slice(0, limit);
  const offers: Offer[] = [
    ...verbs.map((m) => ({ kind: "verb" as const, verb: m.verb, argument: m.argument })),
    ...named.map((c) => ({ kind: "object" as const, command: c })),
  ];
  return offers.slice(0, limit);
}

/** A stable key for an offer, for React and for the tests. */
export function offerKey(offer: Offer): string {
  return offer.kind === "verb" ? `v:${offer.verb.id}` : `o:${offer.command.id}`;
}

export type BarProps = {
  /** Everything in the project, by name — the palette's own list. */
  commands: Command[];
  /** Run a verb. The workspace knows how to reach a destination. */
  onVerb: (destination: Destination, argument: string) => void;
  /**
   * The chips under an empty box.
   *
   * The first is the project's own next step where it has one, so the bar
   * answers "what do I do?" before being asked.
   */
  suggestions?: Array<{ label: string; run: () => void; primary?: boolean }>;
  /**
   * `dock` is the always-on bar at the foot of every screen; `home` the big
   * centred one; `compact` the small one a header can hold.
   */
  size?: "home" | "compact" | "dock";
  /**
   * Which project's trail to keep, and whether to keep one at all.
   *
   * Absent means no memory — the compact and home bars do not want a panel of
   * recents opening over the screen they sit on.
   */
  historyKey?: string;
  placeholder?: string;
  /** Called after anything runs, so the landing can clear itself. */
  onRan?: (what: string) => void;
};

export function OneBar({ commands, onVerb, suggestions = [], size = "compact",
                        placeholder, onRan, historyKey }: BarProps) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [open, setOpen] = useState(false);
  const [helping, setHelping] = useState(false);
  const [trail, setTrail] = useState<AskEntry[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  /* Read after mount, never during render: `localStorage` does not exist on
     the server, and reading it in the render body makes the first client paint
     disagree with the server's HTML. */
  useEffect(() => {
    if (historyKey) setTrail(readHistory(historyKey));
  }, [historyKey]);

  const offers = useMemo(() => offersFor(query, commands), [query, commands]);

  useEffect(() => { setActive(0); }, [query]);

  const run = useCallback((offer: Offer) => {
    if (offer.kind === "verb") onVerb(offer.verb.to, offer.argument);
    else offer.command.run();
    const said = offer.kind === "verb" ? offer.verb.label : offer.command.label;
    if (historyKey) {
      setTrail(remember(historyKey, offer.kind === "verb"
        ? { kind: "verb", verbId: offer.verb.id, argument: offer.argument,
            label: offer.argument ? `${offer.verb.label} — ${offer.argument}` : offer.verb.label }
        : { kind: "object", commandId: offer.command.id, label: offer.command.label }));
    }
    setQuery("");
    setOpen(false);
    onRan?.(said);
  }, [onVerb, onRan, historyKey]);

  /** Go where a trail entry went, by looking its target up again now. */
  const replay = useCallback((entry: AskEntry) => {
    if (entry.kind === "verb") {
      const verb = VERBS.find((v) => v.id === entry.verbId);
      if (verb) onVerb(verb.to, entry.argument);
      return;
    }
    // The object may have been deleted since. Saying so beats a dead press.
    const command = commands.find((c) => c.id === entry.commandId);
    if (command) command.run();
    else setQuery(entry.label);
  }, [commands, onVerb]);

  /*
   * Keys are handled on the input rather than the document, unlike the palette.
   *
   * The palette is modal and owns every key while it is up; this bar sits on a
   * page with other controls, so binding arrows to the document would steal
   * them from a table the researcher is walking with the keyboard.
   */
  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (!offers.length) {
      if (event.key === "Escape") { setQuery(""); setOpen(false); }
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((i) => (i + 1) % offers.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((i) => (i - 1 + offers.length) % offers.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      run(offers[active] ?? offers[0]);
    } else if (event.key === "Escape") {
      event.preventDefault();
      setQuery("");
      setOpen(false);
    }
  }

  // A click outside closes the list without clearing what was typed.
  useEffect(() => {
    if (!open) return;
    const away = (event: MouseEvent) => {
      if (!boxRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  const listId = size === "home" ? "onebar-home-offers" : "onebar-offers";
  const showing = open && offers.length > 0;

  return (
    <div className={`onebar onebar-${size}`} ref={boxRef}>
      <div className="onebar-field">
        <span className="onebar-mark" aria-hidden>›</span>
        <input
          ref={inputRef}
          className="onebar-input"
          type="text"
          value={query}
          role="combobox"
          aria-expanded={showing}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-label="Ask for anything in this project"
          placeholder={placeholder
            ?? "Ask for anything — “find papers about soil”, “what’s next”, “add data”"}
          onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          /* Click as well as focus. After running something the input keeps
             focus while the panel is closed, so a second click fired no focus
             event and the bar appeared dead to anyone reaching for it twice. */
          onClick={() => setOpen(true)}
          onKeyDown={onKeyDown}
        />
        {query && (
          <button type="button" className="onebar-clear" aria-label="Clear"
                  onClick={() => { setQuery(""); inputRef.current?.focus(); }}>
            ×
          </button>
        )}
      </div>

      {showing && (
        <ul className="onebar-offers" id={listId} role="listbox">
          {offers.map((offer, i) => (
            <li key={offerKey(offer)} role="option" aria-selected={i === active}>
              <button
                type="button"
                className={`onebar-offer${i === active ? " is-active" : ""}`}
                onMouseEnter={() => setActive(i)}
                onClick={() => run(offer)}
              >
                {offer.kind === "verb" ? (
                  <>
                    <span className="onebar-offer-label">
                      {offer.verb.label}
                      {offer.argument && (
                        <span className="onebar-offer-arg"> — {offer.argument}</span>
                      )}
                    </span>
                    {/* What it will do, before it does it. A bar that acts on a
                        guess without saying what the guess was is the thing
                        people rightly distrust about a single box. */}
                    <span className="onebar-offer-says">{offer.verb.says}</span>
                  </>
                ) : (
                  <>
                    <span className="onebar-offer-label">{offer.command.label}</span>
                    <span className="onebar-offer-says">
                      {offer.command.group}
                      {offer.command.hint ? ` · ${offer.command.hint}` : ""}
                    </span>
                  </>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* Nothing matched: say so, and say what would. */}
      {open && query.trim() && offers.length === 0 && (
        <div className="onebar-offers onebar-empty" id={listId}>
          <p>
            Nothing in this project is called <b>{query.trim()}</b>, and that is
            not one of the things this can be asked to do.
          </p>
          <button type="button" className="btn-text" onClick={() => setHelping(true)}>
            See everything you can ask for →
          </button>
        </div>
      )}

      {/*
        * The dock's empty state: what is worth doing now, and where you have
        * been (T195). Shown only while the box is focused and empty, so the
        * bar is a thin line the rest of the time and never covers the screen
        * it sits on. This is the panel that replaces what a rail gave away for
        * free — the sense that these places exist.
        */}
      {size === "dock" && open && !query.trim() && (suggestions.length > 0 || trail.length > 0) && (
        <div className="onebar-start">
          {suggestions.length > 0 && (
            <div className="onebar-start-block">
              <h4>Worth doing now</h4>
              <div className="onebar-chips">
                {suggestions.map((chip) => (
                  <button
                    key={chip.label}
                    type="button"
                    className={`onebar-chip${chip.primary ? " is-primary" : ""}`}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => { chip.run(); setOpen(false); onRan?.(chip.label); }}
                  >
                    {chip.label}
                  </button>
                ))}
              </div>
            </div>
          )}
          {trail.length > 0 && (
            <div className="onebar-start-block">
              <h4>Where you have been</h4>
              <ul className="onebar-trail">
                {trail.map((entry) => (
                  <li key={`${entry.at}`}>
                    <button type="button" className="onebar-trail-item"
                            onMouseDown={(e) => e.preventDefault()}
                            onClick={() => { replay(entry); setOpen(false); }}>
                      {entry.label}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <button type="button" className="onebar-help-open"
                  onMouseDown={(e) => e.preventDefault()}
                  aria-expanded={helping}
                  onClick={() => setHelping((v) => !v)}>
            {helping ? "Hide what you can ask" : "What can I ask?"}
          </button>
        </div>
      )}

      {size === "home" && (
        <>
          {suggestions.length > 0 && (
            <div className="onebar-chips">
              {suggestions.map((chip) => (
                <button
                  key={chip.label}
                  type="button"
                  className={`onebar-chip${chip.primary ? " is-primary" : ""}`}
                  onClick={() => { chip.run(); onRan?.(chip.label); }}
                >
                  {chip.label}
                </button>
              ))}
            </div>
          )}
          <button type="button" className="onebar-help-open"
                  aria-expanded={helping}
                  onClick={() => setHelping((v) => !v)}>
            {helping ? "Hide what you can ask" : "What can I ask?"}
          </button>
        </>
      )}

      {helping && <VerbSheet onPick={(verb) => {
        onVerb(verb.to, "");
        setHelping(false);
        onRan?.(verb.label);
      }} />}
    </div>
  );
}

/**
 * The whole vocabulary, grouped and visible.
 *
 * Not a tooltip and not a separate help page: the same argument this codebase
 * makes about glosses (`term.tsx`) applies to a command vocabulary. If the only
 * way to learn what the box accepts is to guess at it, the box is worse than
 * the rail it replaced.
 */
export function VerbSheet({ onPick }: { onPick: (verb: Verb) => void }) {
  return (
    <div className="verbsheet">
      <p className="verbsheet-lede">
        Type any of these, or press one. The ones followed by something{" "}
        <i>in italics</i> take a subject — “find papers{" "}
        <i>about soil fertility</i>”; the rest are complete as they stand.
      </p>
      {verbsByGroup().map(({ group, verbs }) => (
        <section key={group} className="verbsheet-group">
          <h3>{group}</h3>
          <ul>
            {verbs.map((verb) => (
              <li key={verb.id}>
                <button type="button" className="verbsheet-verb"
                        onClick={() => onPick(verb)}>
                  <span className={verb.takes ? "verbsheet-takes" : undefined}>
                    {verb.triggers[0]}
                  </span>
                  {verb.takes && <span className="verbsheet-arg"> {verb.takes}</span>}
                </button>
                <span className="verbsheet-says">{verb.says}</span>
              </li>
            ))}
          </ul>
        </section>
      ))}
      <p className="verbsheet-foot">
        {VERBS.length} things to ask for, and everything in the project by name —
        a column, a run, a connection, a finding, a report, or an id somebody
        sent you.
      </p>
    </div>
  );
}
