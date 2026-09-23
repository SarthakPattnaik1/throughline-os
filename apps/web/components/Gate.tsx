"use client";

/**
 * The sign-in gate: the first account on a fresh install, then sign-in or
 * account creation. Its own file so the entrance can carry the product's
 * identity — the sky and the black hole the public site wears — without the
 * workspace page changing every time the entrance does (T140), and so the
 * form can be tested without the whole workspace behind it.
 */

import Link from "next/link";
import { useState } from "react";

import { Sky } from "@/components/Sky";
import { ApiError, api } from "@/lib/api";

export type AuthStatus = {
  needs_setup: boolean; authenticated: boolean;
  user: { display_name: string } | null;
};

/** First run creates the local account; afterwards it signs in. */
export function Gate({ status, onDone }: { status: AuthStatus; onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [setupToken, setSetupToken] = useState("");
  const [needsSetupToken, setNeedsSetupToken] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  /*
   * Three modes, not two.
   *
   * `needs_setup` is the very first account on a fresh install. After that a
   * visitor may still need to *create* an account — previously they could not:
   * setup runs once and everything else required a session, so the second
   * person to open this installation had no way in at all.
   */
  const setup = status.needs_setup;
  /*
   * Which path to lead with. A brand-new visitor arrives from the landing
   * page's only button and used to meet "Welcome back" with account creation
   * as an underlined link in body text (T135). The browser remembers whether
   * an account has ever signed in here; until it has, creating one leads.
   * Both paths are always visible as equal, labelled choices.
   */
  const [seen] = useState(() => {
    if (typeof window === "undefined") return false;
    try { return window.localStorage.getItem("throughline.account") === "1"; } catch { return false; }
  });
  const [mode, setMode] = useState<"signin" | "signup">(seen ? "signin" : "signup");
  const creating = setup || mode === "signup";

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true); setError(null);
    try {
      if (setup) {
        await api.post("/api/auth/setup", {
          email,
          display_name: name || "Researcher",
          password,
          setup_token: setupToken,
        });
      } else if (mode === "signup") {
        await api.post("/api/auth/register",
                       { email, display_name: name || "Researcher", password });
      } else {
        await api.post("/api/auth/login", { email, password });
      }
      try { window.localStorage.setItem("throughline.account", "1"); } catch { /* a convenience only */ }
      onDone();
    } catch (err) {
      if (setup && err instanceof ApiError && err.status === 403
          && /setup token|first-run setup/i.test(err.message)) {
        setNeedsSetupToken(true);
      }
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const message = error instanceof Error ? error.message : error ? String(error) : null;

  return (
    <div className="gate gate-sky">
      {/* The entrance carries depth; the instrument beyond it does not (§115).
          The depth is the site's own black hole, running the site's own
          shader — behind the panel, never under the words on it. */}
      <Sky variant="gate" />
      <aside className="gate-art">
        {/* Under the hole, where the site sets its headline. */}
        <div className="gate-art-copy">
          <h2>An interesting pattern is not a discovery.</h2>
          <p>
            Everything you load stays on this machine — the database, the
            embeddings and the analysis sandbox all run locally. Nothing is
            uploaded anywhere.
          </p>
        </div>
      </aside>

      <div className="gate-form">
        <div className="gate-form-inner">
          <div className="gate-mark">
            <i aria-hidden />
            <span>Throughline</span>
          </div>

          {/* Not shown during first-run setup: there is nothing to choose
              between until an account exists. Two equal, labelled choices,
              never a link hidden in body text. */}
          {!setup && (
            <div role="group" aria-label="Sign in or create an account" className="gate-modes">
              <button type="button"
                      className={mode === "signup" ? "btn btn-primary" : "btn"}
                      aria-pressed={mode === "signup"}
                      onClick={() => { setMode("signup"); setError(null); }}>
                Create an account
              </button>
              <button type="button"
                      className={mode === "signin" ? "btn btn-primary" : "btn"}
                      aria-pressed={mode === "signin"}
                      onClick={() => { setMode("signin"); setError(null); }}>
                I already have an account
              </button>
            </div>
          )}

          <h1>
            {setup ? "Set up this machine"
                   : mode === "signup" ? "Create your account" : "Welcome back"}
          </h1>
          <p className="gate-sub">
            {setup
              ? "The first account on this machine is its administrator: the only one that can add people, install feature packs and choose the model. It also scopes your projects and signs the audit trail."
              : mode === "signup"
                ? "Your own workspace on this machine. You will not see anyone else's projects, and they will not see yours."
                : "Sign in to your local workspace."}
          </p>

          <form onSubmit={submit}>
            {creating && (
              <label className="gate-field">
                <span>Name</span>
                <input type="text" autoComplete="name" value={name} placeholder="Dr Chen"
                       onChange={(e) => setName(e.target.value)} />
              </label>
            )}
            <label className="gate-field">
              <span>Email</span>
              <input type="email" required autoComplete="username" value={email}
                     placeholder="you@lab.local"
                     onChange={(e) => setEmail(e.target.value)} />
            </label>
            <label className="gate-field">
              <span>Password</span>
              <input type="password" required minLength={creating ? 12 : 1}
                     autoComplete={creating ? "new-password" : "current-password"}
                     value={password} onChange={(e) => setPassword(e.target.value)} />
              {creating && <span className="gate-hint">At least 12 characters. It protects an entire research corpus.</span>}
            </label>

            {setup && needsSetupToken && (
              <label className="gate-field">
                <span>Setup token</span>
                <input type="password" required autoComplete="off"
                       value={setupToken}
                       onChange={(e) => setSetupToken(e.target.value)} />
                <span className="gate-hint">
                  This first-run setup reached Throughline over a network connection.
                  Enter the token printed by the Throughline server/container.
                </span>
              </label>
            )}

            {message ? <div className="gate-error" role="alert">{message}</div> : null}

            <button className="gate-submit" type="submit" disabled={busy}>
              {busy ? "Working…"
                    : creating ? "Create account and continue" : "Sign in"}
            </button>
          </form>


          <Link className="gate-back" href="/">← Back</Link>
        </div>
      </div>
    </div>
  );
}
