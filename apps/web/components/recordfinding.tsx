"use client";

/**
 * Record a validated connection as a finding.
 *
 * The step the workflow was missing. `PLAN.md` §137 item 5 recorded it plainly:
 * after validation succeeds the overview says "Record a finding — NEXT", the
 * Findings section renders zero buttons, and no `api.post` to `/findings`
 * existed anywhere in `apps/web`. The capability was in the API and exercised
 * by the suite; it was unreachable by click. The Findings empty state even told
 * the researcher to "record what it shows as a finding" — instructing an action
 * the interface did not offer.
 *
 * **It lives on the connection, not on the Findings list.** A finding is
 * recorded *from* something, and the moment a researcher wants to record one is
 * the moment they are looking at a result that survived. Putting a bare "new
 * finding" button on the list would invite one written from memory, detached
 * from the analysis — which is the state every finding was in until the edge
 * behind this was added, and the reason nothing could be walked back.
 *
 * **The connection travels with it.** `from_connections` is what attaches the
 * finding to the analysis that produced it, and through that to the dataset and
 * the paper. Without it the finding is recorded and immediately unverifiable.
 *
 * **Causality is not offered as a choice here.** The API records a new finding
 * as `not_assessed`, and this does not present a control to say otherwise. A
 * dropdown offering "causal" beside a correlation, at the exact moment somebody
 * is pleased their result survived, is the single easiest place in the product
 * to overclaim. Changing it is a separate, deliberate act.
 */

import { useState } from "react";
import { api } from "@/lib/api";
import { Failure, Fold } from "./primitives";

type Created = { finding_id: string; lifecycle_status: string };

export function RecordFinding({ projectId, connectionId, defaultTitle, validated,
                               onRecorded }: {
  projectId: string;
  connectionId: string;
  defaultTitle: string;
  /** Whether this connection has survived a validation run. */
  validated: boolean;
  onRecorded?: (findingId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState(defaultTitle);
  const [statement, setStatement] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState<Created | null>(null);

  async function record() {
    setBusy(true);
    setError(null);
    try {
      const created = await api.post<Created>(
        `/api/projects/${projectId}/findings`,
        {
          title: title.trim(),
          finding_type: "statistical",
          statement: statement.trim(),
          // The link that makes the finding checkable afterwards.
          from_connections: [connectionId],
        });
      setDone(created);
      onRecorded?.(created.finding_id);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="card">
        <h2>Recorded as a finding</h2>
{/*
          What this used to say: "Promoting it needs evidence attached". True
          while nothing in the product could attach any — `attach_claim` had a
          single caller, the worked example — so every finding a researcher
          made was stuck at candidate and the sentence was the only warning
          they got. It is the wrong sentence now: `from_connections` travels
          with this request, and the analysis behind the connection is
          recorded as the finding's evidence. Telling a researcher to go and
          attach evidence sends them looking for a step that has happened.

          What has not happened is the judgement, so that is what it says.
        */}
        <p style={{ margin: "0 0 8px" }}>
          It starts as a <b>candidate</b>. The analysis behind this connection
          travels with it as its evidence, so what is left is the judgement —
          whether it holds, and whether the robustness checks were run and
          passed. The lifecycle refuses an unearned promotion rather than
          warning about one.
        </p>
        <p className="mono" style={{ color: "var(--ink-faint)", margin: 0 }}>
          {done.finding_id}
        </p>
      </div>
    );
  }

  return (
    <div className="card">
      <h2>Record this as a finding</h2>

      {/*
        Offered before validation too, and said plainly rather than disabled. A
        researcher may have reason to record something that has not been through
        the checks, and the lifecycle already refuses to promote it past
        candidate — so blocking the button here would be a second, weaker
        enforcement of a rule that is already structural, in a place where it
        only removes the researcher's judgement.

        The caveat matters, but it is secondary to the act. Keeping the full
        sentence open on every connection made this screen exceed its own
        readability budget after the action band and provenance controls were
        added. The closed summary states the consequence before the researcher
        chooses to read the explanation, preserving the warning without making
        every visitor process it up front.
      */}
      {!validated && (
        <Fold summary="Why this stays a candidate" count={1}>
          <p className="note" style={{ margin: 0 }}>
            This connection has not survived a validation run yet — recorded now,
            it sits as a candidate, which is what an untested result is.
          </p>
        </Fold>
      )}

      {error != null && <Failure error={error} retry={record} />}

      {!open ? (
        <button type="button" className="btn" onClick={() => setOpen(true)}>
          Record a finding
        </button>
      ) : (
        <>
          <label style={{ display: "block", fontSize: 12, marginBottom: 10 }}>
            What was found
            <input value={title} onChange={(event) => setTitle(event.target.value)}
                   style={{ display: "block", width: "100%", marginTop: 4 }} />
          </label>

          <label style={{ display: "block", fontSize: 12, marginBottom: 10 }}>
            What it does and does not show
            <textarea value={statement} rows={3}
                      onChange={(event) => setStatement(event.target.value)}
                      style={{ display: "block", width: "100%", marginTop: 4 }} />
          </label>

          {/*
            Stated rather than selectable. The finding is recorded as
            not-assessed for causality, and saying so here is what stops a
            reader assuming the system judged it either way.
          */}
          <p className="note">
            Recorded as an association. Nothing here asserts that one variable
            causes the other, and the causal status stays <b>not assessed</b>
            until somebody assesses it.
          </p>

          <button type="button" className="btn" onClick={record}
                  disabled={busy || !title.trim()}>
            {busy ? "Recording…" : "Record it"}
          </button>
        </>
      )}
    </div>
  );
}
