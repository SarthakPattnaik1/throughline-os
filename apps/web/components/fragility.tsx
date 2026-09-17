"use client";

/**
 * How much would have to be unaccounted for, for this to go away.
 *
 * Everything else on a connection's screen reports what was done: the test,
 * the corrected q-value, what the validation suite tried. None of it answers
 * the question a reviewer asks first, and the one a researcher should ask
 * before writing anything down — *what would have to be true for this to be
 * nothing?*
 *
 * The E-value answers it in one number. It pairs with the causal-language
 * validator: that stops a sentence claiming more than the design licenses,
 * and this says how far from a causal claim the evidence sits.
 *
 * **The number is never shown on its own.** An E-value quoted bare reads as a
 * quality score, and researchers would start comparing them across
 * unrelated results. So the sentence that says what it means, the assumptions
 * the conversion rests on, and the refusal to be read as causal evidence all
 * travel with it — and a fragile result is called fragile in words rather
 * than left to be inferred from a small number.
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { Failure, Fold, Loading } from "./primitives";
import { Term } from "./term";

type Report = {
  variables: [string, string];
  /** The statistical method behind `estimate` — the conversion assumes it. */
  method: string;
  estimate: number;
  /**
   * The number the E-value is computed from. The assumptions below name the
   * conversion — d = 2r / sqrt(1 - r^2), RR = exp(0.91 d) — and this is its
   * result. It was sent and not shown, so a reader could see the formula and
   * the answer but not the step between them, and could not check either.
   */
  risk_ratio: number;
  e_value: number;
  e_value_limit: number | null;
  headline: number;
  interval_note: string;
  assumptions: string[];
  sentence: string;
};

/**
 * The server's number, in the form a sentence can carry.
 *
 * Formatting, not arithmetic: `headline` is what the API sent and nothing
 * here recomputes it (principle 10). Three significant figures is what the
 * figure below the sentence shows; a sentence saying "about 29.4 times
 * stronger" claims a precision the reader is not meant to take, so from ten
 * upwards it is read to the nearest whole number — the same number, said the
 * way a person says it.
 */
function readable(headline: number): string {
  if (headline >= 10) return String(Math.round(headline));
  return String(Number(headline.toPrecision(3)));
}

export function Fragility({ connectionId }: { connectionId: string }) {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    api.get<Report>(`/api/connections/${connectionId}/fragility`)
      .then((body) => { setReport(body); setError(null); })
      .catch(setError)
      .finally(() => setLoading(false));
  }, [connectionId]);

  useEffect(load, [load]);

  if (loading) return <Loading rows={2} label="Working out how fragile this is" />;
  /*
   * A method this cannot convert honestly is not an error on this screen — it
   * is a fact about the analysis. Saying so beats a red banner, and beats
   * silence, which would leave a researcher wondering whether the number was
   * withheld or never existed.
   */
  /*
   * A body without a usable number is treated exactly like a refusal, rather
   * than trusted because the request succeeded. Found by the existing
   * connection tests, whose fixtures answer every `api.get` with connection
   * data: this panel read `headline` off one of them and crashed the whole
   * screen. In the product the same shape arrives from an older client or a
   * response that changed underneath one, and blanking the screen is a much
   * worse answer than saying no number is available.
   */
  const usable = report != null && Number.isFinite(report.headline);

  /*
   * Which refusal, and whether it is a refusal at all.
   *
   * The endpoint answers with a different status for each of these and writes
   * a sentence for every one: 422 when the method cannot be converted to a
   * risk ratio honestly, 409 when no analysis has completed or the run
   * recorded no estimate, 404 when the connection is gone. Every one of them
   * arrived here as `error` and produced the single sentence below — so a
   * connection whose analysis had simply not finished was told its *method*
   * was the problem, which is a specific claim about the researcher's own
   * work that this screen had never established. A server that was down said
   * the same thing.
   *
   * So: 422 keeps the sentence written for it. The other refusals carry the
   * server's own words, which §104 requires reach the researcher and which
   * are already written as sentences for one. Anything else — a 5xx, a
   * dropped network, something that is not an `ApiError` at all — is a
   * failure, and a failure is recoverable and offers the retry. A refusal is
   * not and must not, because a retry that cannot succeed reads as a system
   * that is merely broken.
   */
  const refused = error instanceof ApiError ? error : null;

  if (error && !refused) {
    return (
      <section className="fragility">
        <h2>How fragile is this?</h2>
        <Failure error={error} retry={load} />
      </section>
    );
  }

  if (refused && refused.status !== 422) {
    if (refused.status >= 500) {
      return (
        <section className="fragility">
          <h2>How fragile is this?</h2>
          <Failure error={error} retry={load} />
        </section>
      );
    }
    return (
      <section className="fragility">
        <h2>How fragile is this?</h2>
        <p className="note">
          {refused.message} No number is shown rather than a confident one
          with no meaning.
        </p>
      </section>
    );
  }

  /*
   * A successful response with no usable fragility number is defensive UI,
   * not a result the reader needs expanded on arrival. It can happen when an
   * older or malformed response reaches the client. Keeping the explanation
   * open on every connection made this secondary state consume roughly forty
   * words of the connection-detail screen's readability budget. The summary
   * says exactly what is behind it; opening it preserves the full explanation.
   *
   * A real 422 refusal stays open below. That is an intentional answer from
   * the current server, not merely a defensive fallback.
   */
  if (!refused && !usable) {
    return (
      <section className="fragility">
        <Fold summary="How fragile is this?" count={1}>
          <p className="lede" style={{ marginTop: 0 }}>
            This connection&rsquo;s method is not one it can convert to a risk
            ratio honestly, so no number is shown rather than a confident one
            with no meaning. The number this panel reports for a correlation is
            the <Term id="E-value" />.
          </p>
        </Fold>
      </section>
    );
  }

  if (refused) {
    return (
      <section className="fragility">
        <h2>How fragile is this?</h2>
        <p className="lede">
          This connection&rsquo;s method is not one it can convert to a risk
          ratio honestly, so no number is shown rather than a confident one
          with no meaning. The number this panel reports for a correlation is
          the <Term id="E-value" />.
        </p>
      </section>
    );
  }

  const fragile = report!.headline < 1.25;
  const both = report!.variables?.[0] && report!.variables?.[1]
    ? `both ${report!.variables[0]} and ${report!.variables[1]}`
    : "both variables";
  const assumptions = report!.assumptions ?? [];

  return (
    <section className="fragility">
      <h2>How fragile is this?</h2>
      {/*
        * ResultCard's law, which §6 keeps and item 2.11 applies here: the
        * sentence comes first, and the number is a labelled figure under it.
        * A 2.4rem numeral directly beneath the heading answered "how fragile
        * is this?" with "1.42", which is a quantity and not an answer — and
        * the sentence that made it mean something was below it, where a
        * reader who had already formed an impression of the number was.
        */}
      <p className="lede">
        {fragile
          ? `An unmeasured confounder only ${readable(report!.headline)} times stronger `
            + `than anything measured here — on ${both} — would be enough to explain `
            + `this away entirely.`
          : `An unmeasured confounder would have to be about ${readable(report!.headline)} `
            + `times stronger than anything measured here — on ${both} — to explain `
            + `this away entirely.`}
      </p>
      <p style={{ margin: "0.6rem 0 0" }}>
        <span className="eyebrow"><Term id="E-value" /></span>
      </p>
      <p className="big" data-fragile={fragile ? "yes" : "no"}>
        {report!.headline.toPrecision(3)}
      </p>
      {(report!.sentence ?? "").split("\n").filter(Boolean).map((line) => (
        <p key={line} className="note">{line}</p>
      ))}
      {/*
        §4/principle 4 — a closed summary states what is inside it, so nothing
        is hidden by being one press away. It says it in the workspace's own
        disclosure now (T139): the count moved out of the label and onto
        `data-count`, which is where every other fold on this screen carries
        it, so the reader meets one control rather than two idioms.
      */}
      <Fold summary="What this number rests on" count={assumptions.length}>
        {/* Guarded the way the rest of this screen is: a report missing a
            field shows less rather than taking the whole panel down with it. */}
        {typeof report!.risk_ratio === "number"
          && typeof report!.estimate === "number" && (
          <p className="note">
            {report!.method
              ? `A ${report!.method.replace(/_/g, " ")} estimate of `
              : "An estimate of "}
            {report!.estimate.toPrecision(3)}, which is a risk ratio of{" "}
            {report!.risk_ratio.toPrecision(3)} — the figure the E-value above
            is computed from.
          </p>
        )}
        <ul>
          {assumptions.map((assumption) => (
            <li key={assumption}>{assumption}</li>
          ))}
        </ul>
      </Fold>
    </section>
  );
}
