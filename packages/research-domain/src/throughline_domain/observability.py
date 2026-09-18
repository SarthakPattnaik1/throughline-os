"""
Structured logging and health, for a system that must be auditable while running.

Two decisions here are about research integrity rather than operations.

**Logs are structured and redacted by default.** A research workspace's logs
would otherwise become an uncontrolled second copy of the data: a query string
containing a patient identifier, a prompt containing an unpublished result. The
formatter emits JSON with a fixed field set and never interpolates arbitrary
payloads, so what leaves the process is what someone chose to log.

**Health is reported per dependency, with degradation named.** "Healthy" is not
a boolean here. A workspace with no model can still do every deterministic
thing; one with no database can do nothing. Collapsing those into one green tick
means an operator restarts the wrong process.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

#: Never logged, at any level. Not a denylist of what to scrub from a message —
#: these keys are dropped from structured fields outright, because a log line is
#: the one place a secret leaks without anyone reading it.
#:
#: Matched as substrings of the key, not as whole keys. Exact matching dropped
#: `api_key` and kept `zotero_api_key`, dropped `token` and kept `access_token`
#: — and a compound name is the ordinary shape: the Zotero route already takes
#: a field called `api_key`, which becomes `zotero_api_key` the moment anybody
#: logs which service it belonged to.
#:
#: Nothing logs structured context today, so nothing was leaking; this is the
#: first caller's defect rather than a live one, which is exactly when it is
#: cheap to fix.
_REDACT = {"password", "token", "secret", "authorization", "cookie",
           "session", "api_key", "credential", "passphrase", "private_key",
           "content"}


def _is_secret(key: str) -> bool:
    """Whether a field name looks like something that must never be logged.

    Deliberately over-inclusive: `content_type` and `token_count` are dropped
    along with the things worth hiding. Losing a content type from a log line
    costs an operator a detail they can get elsewhere; logging a bearer token
    costs them the credential. The `redacted` field below means the loss is
    visible rather than silent, so an over-match can be seen and argued with.
    """
    low = key.lower()
    return any(term in low for term in _REDACT)


class StructuredFormatter(logging.Formatter):
    """One JSON object per line, with a fixed shape."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        withheld: list[str] = []
        for key, value in getattr(record, "context", {}).items():
            if _is_secret(key):
                # The *name*, never the value. An operator reading this needs
                # to know a field was carried and withheld — a field that
                # simply vanishes is indistinguishable from one nobody sent,
                # and that ambiguity is what makes an over-broad match
                # impossible to notice and correct.
                withheld.append(key)
                continue
            payload[key] = value
        if withheld:
            # Names only. `test_secrets_are_dropped_from_log_context` argues
            # that dropping beats masking because "a masked value still
            # records that one existed and how long it was" — the length is
            # the part that matters, and a key name carries neither the value
            # nor its size. This extends that decision rather than reversing
            # it: the substring match above can over-reach, and an operator
            # who cannot see that a field was withheld can never notice the
            # over-reach or argue with it.
            payload["redacted"] = sorted(withheld)
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)[-2000:]
        return json.dumps(payload, default=str)


#: Libraries that log their own internals at INFO. Left at INFO they bury this
#: system's own lines under postmaster status dumps — and an operator who cannot
#: find the line they need has no observability, however structured the output.
_NOISY = ("pgserver", "urllib3", "httpx", "httpcore", "neo4j",
          "multipart", "asyncio", "watchfiles")


def configure(level: str | None = None) -> None:
    """Install the formatter once, at startup."""
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel((level or os.environ.get("THROUGHLINE_LOG_LEVEL", "info")).upper())
    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)


def log(logger: logging.Logger, level: int, message: str, **context: Any) -> None:
    """Log with structured context, redacted."""
    logger.log(level, message, extra={"context": context})


#: How long a job that is due may sit unclaimed before nobody is draining the
#: queue. Generous on purpose: a worker mid-analysis is not polling, and a
#: threshold tight enough to fire during one sandboxed run would cry wolf on a
#: healthy workspace, after which nobody reads this field.
STALLED_AFTER_SECONDS = 90


def _worker_check() -> dict[str, Any]:
    """
    Is anything actually draining the queue?

    Every dependency check above asks whether a *service* is reachable. None of
    them asks the question an operator of this product actually has, which is
    whether work is getting done — and here those are different questions,
    because ingestion, discovery and analysis all run through the durable queue.
    With the database healthy and no worker running, the API answers every
    request correctly, `/api/health` reports ok, and nothing ever completes. The
    researcher sees "Waiting for a worker to pick it up…" indefinitely.

    That is not hypothetical: it is what an earlier audit recorded as four
    sources and two workflow runs with two rows stuck, and it is what happened
    while measuring the provenance depth on this branch — a worked example sat
    at zero findings until somebody noticed no worker had been started.

    **An idle queue is not evidence of a live worker, and is not reported as
    one.** Most workspaces are idle most of the time, so failing on silence
    would cry wolf constantly; claiming health from silence would be the
    reassuring lie. `confirmed` carries the difference: it is true only when a
    worker has actually heartbeated recently, and `ok` goes false only when work
    is genuinely overdue — which is unambiguous.
    """
    try:
        from .db import connection

        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  count(*) FILTER (WHERE state = 'queued'
                                     AND run_after <= now())            AS due,
                  count(*) FILTER (WHERE state = 'running')             AS running,
                  count(*) FILTER (WHERE state = 'running'
                                     AND lease_expires_at IS NOT NULL
                                     AND lease_expires_at < now())      AS abandoned,
                  EXTRACT(EPOCH FROM now() - min(run_after) FILTER (
                      WHERE state = 'queued' AND run_after <= now()))   AS waiting_s,
                  EXTRACT(EPOCH FROM now() - max(heartbeat_at))         AS since_beat
                FROM workflow_runs
                """)
            row = dict(cur.fetchone())
    except Exception:  # noqa: BLE001
        return {"ok": False, "critical": False,
                "error": "Worker status could not be checked.",
                "impact": "Whether anything is processing work is unknown."}

    due = int(row["due"] or 0)
    waiting = float(row["waiting_s"] or 0)
    since_beat = row["since_beat"]
    confirmed = since_beat is not None and float(since_beat) <= STALLED_AFTER_SECONDS

    check: dict[str, Any] = {
        "ok": True,
        "critical": False,
        "confirmed": confirmed,
        "queued_due": due,
        "running": int(row["running"] or 0),
        # A run whose lease expired while still marked running is a worker that
        # died mid-job. `claim` reclaims these, so it is a symptom rather than a
        # leak — but a rising count is the clearest sign of a crash loop.
        "abandoned_leases": int(row["abandoned"] or 0),
        "seconds_since_heartbeat": None if since_beat is None else round(float(since_beat)),
        "impact": None,
    }

    if due and waiting > STALLED_AFTER_SECONDS:
        check["ok"] = False
        check["impact"] = (
            f"{due} job{'' if due == 1 else 's'} due and unclaimed for "
            f"{round(waiting)}s. Nothing is draining the queue, so ingestion, "
            "discovery and analysis will not finish. Start a worker: "
            "`python -m throughline_workers`.")
    elif not confirmed:
        # Deliberately still `ok`. There is nothing to do, so there is nothing
        # to be failing — but the payload does not pretend a worker was seen.
        check["impact"] = (
            "No worker has reported in recently. Nothing is waiting either, so "
            "this is not evidence of a problem — and it is not evidence that a "
            "worker is running.")

    return check


def health() -> dict[str, Any]:
    """
    What this installation can do right now, dependency by dependency.

    Deliberately not a boolean. A workspace with no model can still ingest,
    analyse, correct, adjudicate and export — everything deterministic. One with
    no database can do nothing at all. An operator who sees a single red tick
    restarts the wrong process.
    """
    checks: dict[str, Any] = {}

    try:
        from .db import connection

        started = time.monotonic()
        with connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        checks["database"] = {
            "ok": True, "critical": True,
            "latency_ms": round((time.monotonic() - started) * 1000, 1)}
    except Exception:  # noqa: BLE001
        checks["database"] = {
            "ok": False, "critical": True,
            "error": "The database health check failed.",
            "impact": "Nothing works without the record.",
        }

    try:
        import throughline_model

        capability = throughline_model.capability()
        checks["model"] = {
            "ok": capability.text, "critical": False, "model": capability.model,
            "impact": None if capability.text else
            "Claim location and plain summaries are unavailable. Every "
            "deterministic verdict, correction and export still works."}
    except Exception:  # noqa: BLE001
        checks["model"] = {
            "ok": False, "critical": False,
            "error": "The model capability check failed.",
        }

    checks["workers"] = _worker_check()

    try:
        from . import graph_projection

        projection = graph_projection.capability()
        checks["graph_projection"] = {
            "ok": projection["reachable"], "critical": False,
            "configured": projection["configured"],
            "impact": None if projection["reachable"] else
            "Path-finding, centrality and clustering are unavailable. "
            "Provenance and evidence graphs are unaffected."}
    except Exception:  # noqa: BLE001
        checks["graph_projection"] = {
            "ok": False, "critical": False,
            "error": "The graph projection health check failed.",
        }

    critical_ok = all(c["ok"] for c in checks.values() if c.get("critical"))
    degraded = [name for name, c in checks.items() if not c["ok"]]

    return {
        "status": "ok" if critical_ok and not degraded
                  else "degraded" if critical_ok else "unhealthy",
        "checks": checks,
        "degraded": degraded,
        # Named, so an operator knows whether to page someone.
        "summary": ("Everything is available." if critical_ok and not degraded
                    else "Running with reduced capability: "
                         + ", ".join(degraded) if critical_ok
                    else "The record is unreachable; nothing can run."),
    }


__all__ = ["StructuredFormatter", "configure", "health", "log"]
