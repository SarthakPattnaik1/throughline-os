# Throughline

**A local-first research workspace for connecting papers, datasets, analyses, findings, and evidence.**

Throughline is open source under the Apache-2.0 license. It is an early research release, **not medical or clinical decision software**. Review statistical conclusions independently and use synthetic or non-sensitive data when evaluating a new installation.

**Current packaged release:** `v0.3.0`  
**Download / installer host:** https://throughline-research.pages.dev

## What Throughline does

The core workflow is designed around a research chain rather than a chat transcript:

1. **Add sources** — papers, documents, and datasets.
2. **Profile data** — types, units, distributions, and missingness.
3. **Generate and test candidates** — including multiple-comparison correction for discovery sweeps.
4. **Challenge the result** — resampling, outlier sensitivity, missingness checks, and selected confounder adjustment.
5. **Record a finding** — linked to the analysis and evidence that produced it.
6. **Communicate it** — reports and figures that preserve provenance and specification.

The deterministic research path does not require an AI model. Model-assisted reading and interpretation are optional and are separated from statistical computation.

## Current status

**Dataset-first workflows work end to end today.** A dataset can be imported, profiled, explored, analyzed, validated, recorded as a finding, and communicated through the product.

**Paper/topic-first workflows are still being re-verified end to end.** Individual defects have tests, but a fixed component is not treated as proof that the entire user journey has been re-walked on a clean installation.

For detailed capability status, see `docs/CAPABILITIES.md`. For what remains or is intentionally incomplete, see `ROADMAP.md` and `docs/REQUIREMENTS.md`.

## Quick start

### macOS / Linux

```bash
curl -fsSL https://throughline-research.pages.dev/install.sh | sh
```

This command downloads and executes an installer. If you prefer to inspect code before executing it, download `install.sh` from the same host, review it, then run it locally.

### Windows

There is no `sh` on a stock Windows installation, so use PowerShell:

```powershell
irm https://throughline-research.pages.dev/install.ps1 | iex
```

### From a source checkout

```bash
python scripts/manage.py bootstrap
python scripts/manage.py dev
```

Run diagnostics with:

```bash
python scripts/manage.py doctor
```

The bootstrap obtains the exact Python runtime Throughline needs when the host machine does not already provide it, creates the environment, installs workspace packages, applies migrations, and builds the interface.

## Requirements and runtime model

Throughline's application runtime is built around **Python 3.12** because the embedded PostgreSQL dependency currently publishes compatible wheels through CPython 3.12. Node is used to build the web interface from a source checkout; the shipped static interface is served by the Python API and does not require a separate Node server at runtime.

The API and web interface are served from one origin on port `8080`. This is intentional: authentication uses an `httpOnly`, `SameSite=strict` session cookie and same-origin deployment removes an unnecessary cross-origin failure mode.

PostgreSQL is bundled through the project's database runtime rather than required as a separately managed prerequisite.

## Privacy and models

Throughline can run with no model provider configured.

| Provider | Behavior |
|---|---|
| `none` | No model calls. Deterministic research paths remain available. |
| `ollama` | Local model execution. Research text stays on the machine when Ollama is local. |
| `anthropic` | Hosted provider. Text sent to this provider leaves the machine. |

Nothing silently falls back from a local/offline mode to a hosted provider. A hosted provider must be selected deliberately.

Example:

```bash
export THROUGHLINE_MODEL_PROVIDER=anthropic
export ANTHROPIC_API_KEY=...
```

The hosted client is optional. If it is not installed/configured, the product reports the capability as unavailable rather than failing unrelated workflows.

## Accounts and project isolation

The first account on an installation is the administrator. Administrative operations that affect the whole installation are enforced on the server.

Network sign-up is off by default. Project ownership checks are server-side rather than UI-only, and object identifiers are tested against project/account boundaries. Security-sensitive changes should preserve the rule that another account's object is not distinguishable from a nonexistent object unless a route explicitly needs a different behavior.

See `SECURITY.md` for reporting and threat-boundary details.

## Feature packs

Optional capabilities are exposed through `/api/system/capabilities` and Settings. A missing optional dependency is represented as a missing capability with an explanation rather than as a generic runtime failure.

Some optional packs are large. For example, local speech recognition can pull in multi-gigabyte ML dependencies. They are intentionally not required for the base research workflow.

## Backups

Local-first means the researcher may hold the only copy of a project. Backup therefore belongs to the product, not just operations.

```bash
./scripts/backup.sh
./scripts/restore.sh <archive.tar> [--force]
```

A backup includes both the PostgreSQL dump and the object store because database rows and files reference one another. Restore testing is part of the release gate in `docs/PUBLIC_RELEASE_GATE.md`.

## Docker

The container is currently intended for `linux/amd64`. On Apple Silicon, use the native bootstrap path rather than relying on x86 emulation for the embedded database/pgvector stack.

Build and run:

```bash
docker build -t throughline-os .
docker run -p 127.0.0.1:8080:8080 -v throughline:/data throughline-os
```

Then open `http://localhost:8080`.

The volume is required for durable research data. Running a fresh container without mounting the data volume creates a fresh local corpus.

## Updating

```bash
python scripts/manage.py update --check
python scripts/manage.py update
```

Updates are explicit rather than automatic. The updater backs up before migration, uses fast-forward semantics, and reports rollback/recovery information if an update fails.

A release version that cannot be tied to a specific commit is a provenance defect, so the running installation reports its software version/source.

## Testing

The suite is at least **2828 backend tests and 3712 web tests**. Optional feature packs can add collected cases. CI also audits skip reasons so optional-capability skips do not become a general way to hide failing tests.

Run the full local preflight:

```bash
python scripts/manage.py preflight
```

Or run suites individually:

```bash
.venv/bin/python -m pytest tests -q
cd apps/web && npm test
```

GitHub Actions runs automatically for pull requests targeting `main` and for pushes to `main`. The workflow covers:

- backend suite on Ubuntu;
- backend suite on macOS;
- Windows sandbox tests;
- web tests, lint, and production build;
- Docker build and in-container health check.

A skipped, cancelled, infrastructure-blocked, or billing-refused workflow is missing verification, not a passing result.

## Release readiness

Public source code and a verified release are different claims. Before calling a commit release-ready, follow `docs/PUBLIC_RELEASE_GATE.md`, including cross-platform CI, clean-install rehearsal, account isolation, history/secret review, dependency review, backup/restore rehearsal, and release-signature checks.

Build a release artifact with:

```bash
python scripts/manage.py release
```

The release builder refuses a dirty checkout, builds from an allowlist of paths, emits a SHA-256 digest and manifest, and supports signed update verification. See `keys/README.md` for release-signing details.

## Contributing

Contributions are welcome through forks and pull requests. Start with:

- `CONTRIBUTING.md` — development and review workflow;
- `CODE_OF_CONDUCT.md` — community expectations;
- `SUPPORT.md` — bug/support/scientific-correctness reports;
- `SECURITY.md` — private vulnerability reporting.

For substantial features or architecture changes, open an issue before implementation so the problem, research-integrity implications, and interfaces can be discussed first.

Do not include private datasets, unpublished research, credentials, database exports, session cookies, or personal information in public issues or pull requests.

## Repository layout

```text
apps/
  api/                  FastAPI HTTP surface
  web/                  Next.js researcher interface
packages/
  schemas/              Versioned domain schemas
  model/                Model providers and prompt/version contracts
  research-domain/      Research objects, lineage, analyses, findings, workflows
  connector-sdk/        Connector interfaces and capability model
  ingestion/            Document and dataset ingestion
  visual-spec/          Visualization specification and rendering logic
services/
  workers/              Durable background work
  scientific-runtime/   Isolated analysis execution
docs/                   Architecture, capabilities, requirements, release evidence
tests/                  Cross-package regression and invariant tests
evals/                  Evaluation harness
scripts/                Bootstrap, release, diagnostics, backup, verification
```

New research logic should live in the domain packages rather than React components or HTTP handlers. New API surfaces should use focused modules/routers instead of further concentrating routes in `apps/api/src/throughline_api/app.py`.

## Documentation map

| File | Purpose |
|---|---|
| `README.md` | What Throughline is and how to run it |
| `docs/CAPABILITIES.md` | Detailed implemented capabilities |
| `ROADMAP.md` | What is planned or incomplete |
| `docs/REQUIREMENTS.md` | Status against the full specification |
| `TASKS.md` | Current internal task/evidence ledger |
| `CONTRIBUTING.md` | Contribution workflow |
| `SECURITY.md` | Security reporting and expectations |
| `SUPPORT.md` | Support and issue-reporting guidance |
| `docs/PUBLIC_RELEASE_GATE.md` | Evidence required to call a commit release-ready |

Historical planning documents remain useful context, but current behavior should be taken from code, tests, the capability/requirements ledgers, and the release evidence for the exact commit being evaluated.

## License

Apache License 2.0 — see `LICENSE`.
