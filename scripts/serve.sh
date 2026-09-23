#!/usr/bin/env bash
# Run the stack in one container: migrations, a worker, the API, the interface.
#
# Migrations run to completion *before* anything serves. Two processes racing a
# fresh database is how a worker ends up polling tables that do not exist yet,
# and the failure looks like a hung queue rather than a startup order bug.
set -euo pipefail
# Absolute, and without a subshell: see scripts/dev.sh for why.
cd -P -- "${BASH_SOURCE[0]%/*}/.."

PORT="${PORT:-8080}"

# This entrypoint binds on all container interfaces. Docker may publish that
# port only to host loopback (the documented default), but the API cannot infer
# that from inside the container: the browser normally appears as the Docker
# bridge peer, which is non-loopback. Protect first-admin creation with an
# operator-held token instead of trusting the deployment label or a proxy/Host
# header. A supplied token is respected; otherwise generate an ephemeral one for
# this server start and print it once for the person running the container.
if [ -z "${THROUGHLINE_REMOTE_SETUP_TOKEN:-}" ]; then
  THROUGHLINE_REMOTE_SETUP_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
  export THROUGHLINE_REMOTE_SETUP_TOKEN
  echo "Throughline first-run setup token: ${THROUGHLINE_REMOTE_SETUP_TOKEN}"
  echo "Enter this token only if the setup screen says this connection is remote."
fi

# Checks then migrations, in one process — see throughline_domain/preflight.py.
# The check runs first because migration 0001 is where an ARM host fails, and
# its traceback names nothing that would lead anyone to the cause. One process
# because each one starts and stops its own embedded PostgreSQL.
python -m throughline_domain.preflight

python -m throughline_workers &
WORKER=$!

python -m uvicorn throughline_api.app:app --host 0.0.0.0 --port "${PORT}" &
API=$!

# Any process exiting takes the container down. A half-running stack that keeps
# answering health checks is the worst of both: the orchestrator sees green
# while ingestion silently stops.
trap 'kill "$WORKER" "$API" 2>/dev/null || true' EXIT INT TERM

# The interface is served by the API, from one port, and there is no third
# process. It used to be `next start` on port 3000 with the Next server proxying
# /api back here, because the session cookie is SameSite=strict and a
# cross-origin call drops it silently. Serving the exported files from the API
# makes that same-origin by construction instead of by configuration — and it
# removes Node from the running product entirely.
if [ ! -f apps/web/out/index.html ]; then
  # Not fatal: the API and worker are genuinely useful headless, and saying so
  # beats a blank page. The image builds this in, so seeing it means something
  # went wrong in the build rather than on this machine.
  echo '{"level":"warn","message":"no interface built — API only. Run: python scripts/manage.py build-interface"}'
fi

wait -n "$WORKER" "$API"
