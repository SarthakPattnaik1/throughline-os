# Throughline OS — one image, three processes.
#
# The image ships the embedded PostgreSQL rather than expecting an external one,
# because that is what the product is: a research workspace a person installs,
# not a service someone operates. `THROUGHLINE_DATABASE_URL` points it at a real
# cluster when a lab wants one.
#
# Two things are deliberate and would look like mistakes otherwise.
#
# The data root is a VOLUME. A research corpus that vanishes with the container
# is the same unrecoverable loss the ephemeral-path warning exists to prevent,
# and a default that loses data quietly is worse than one that refuses to start.
#
# It runs as a non-root user with a writable home. The analysis sandbox spawns
# subprocesses, and a container running everything as root would mean an
# analysis escape is a host escape.
#
# The runtime stage is pinned to linux/amd64, and that is not an oversight.
# `pgserver` — the embedded PostgreSQL this image is built around — publishes
# wheels for macOS arm64 but, on Linux, for x86_64 only. There is no aarch64
# wheel in any release and no sdist to fall back on, so on an ARM host the build
# does not degrade, it fails outright: "No matching distribution found for
# pgserver".
#
# CI cannot see this. It runs on ubuntu-latest, which is amd64, so the job is
# green and would stay green forever. The people it breaks are the ones most
# likely to try `docker compose up`: Docker Desktop on an Apple Silicon Mac
# defaults to linux/arm64, so a researcher on an M-series laptop following the
# documented container route hits a wall the test suite says nothing about.
#
# Pinning trades speed for existing at all. On an ARM host this runs under
# emulation and is slower — noticeably so during install — but a slow container
# that works beats a fast one that cannot be built. The native path for Apple
# Silicon is `scripts/bootstrap.sh`, which uses the macOS arm64 wheel and is
# what the README recommends first.
#
# Remove the pin when pgserver ships linux-aarch64 wheels, not before;
# `tests/test_packaging.py` fails if it disappears without that.

# Native on purpose: this stage emits JavaScript and CSS, which are the same
# bytes on any architecture, so there is nothing to gain from emulating it.
FROM node:22-slim AS web
WORKDIR /build
COPY apps/web/package*.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web ./
ENV NEXT_TELEMETRY_DISABLED=1

# `public/` is optional in Next.js, and this project had none until the fonts
# arrived — so the runtime stage below had nothing to copy and the build failed
# there instead of here. The directory is now real and tracked, but the line
# stays: it costs nothing, and it is what keeps the failure from moving back to
# the runtime stage if the last file in `public/` is ever removed.
RUN mkdir -p public

# Exported to `out/`, not `.next`: the runtime stage serves these files
# directly and never runs a Node process.
RUN NEXT_DIST_DIR=out npm run build


FROM --platform=linux/amd64 python:3.12.14-slim AS runtime

# Build tools for the scientific stack, removed in the same layer so they do not
# ship. Nothing here is needed at runtime.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential curl \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash throughline
WORKDIR /app

COPY packages ./packages
COPY services ./services
COPY apps/api ./apps/api
COPY requirements ./requirements

# There was a `COPY pyproject.toml* ./` here. This workspace has no root
# pyproject.toml — it is nine independent packages — so the glob matches nothing
# and the line copies nothing in the best case and fails the build outright in
# the worst, since that is what Docker does with a wildcard that matches no file.
# Removed rather than kept: it cannot be doing anything useful either way.

# No ./packages/workflow-sdk: it has never existed in this repository. The
# durable-workflow code lives in research-domain as workflow.py, and pip cannot
# install a path that is not there — so this layer failed, and with it every
# `docker build` and `docker compose up`.
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir \
      -c ./requirements/scientific-runtime.lock \
      ./packages/schemas ./packages/ingestion ./packages/model \
      ./packages/visual-spec ./packages/connector-sdk \
      ./packages/research-domain ./services/scientific-runtime \
      ./services/workers ./apps/api \
 && apt-get purge -y build-essential && apt-get autoremove -y

# Node for the web interface. Without it the API still runs and §123 makes the
# interface report itself unavailable rather than pretending.
# The exported interface, and nothing else. This used to copy node_modules and
# the node binary as well, because the interface was a `next start` process; it
# is now a folder of files the API serves, so the image carries neither a second
# language runtime nor its dependency tree.
COPY --from=web /build/out ./apps/web/out

COPY scripts ./scripts
RUN chmod +x scripts/*.sh && chown -R throughline:throughline /app

# /data has to exist, and be owned by the user that runs, *before* the VOLUME
# below. Docker creates a declared volume's mount point as root when the path is
# absent from the image, and this container deliberately does not run as root — so
# the first thing the embedded PostgreSQL would do is fail to write its data
# directory. /app was already chowned; the directory the research actually lives in
# was not.
RUN mkdir -p /data && chown throughline:throughline /data

USER throughline
ENV THROUGHLINE_HOME=/data \
    PYTHONUNBUFFERED=1 \
    THROUGHLINE_LOG_LEVEL=info

# Research lives here. Losing it with the container is not an acceptable default.
VOLUME ["/data"]

# One port. The interface and the API share an origin by construction.
EXPOSE 8080

# Readiness, not liveness: the endpoint returns 200 while degraded on purpose,
# because a workspace with no model still does everything deterministic and
# restarting it would lose in-flight work to fix nothing.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/api/health || exit 1

CMD ["./scripts/serve.sh"]
