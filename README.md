# Throughline OS

An AI-native research operating system.

**Deployment target: local-first desktop.** One researcher, one machine. The
database, the job queue and the scientific sandbox all run locally. Nothing
leaves the machine unless the researcher connects an external service — and
where one can be connected, the interface says so before it is used.

## Status

The scientific spine works end to end: a paper goes in, claims come out, a
dataset tests them, and the result renders as a publication figure with its
provenance intact. Five of the six comparison verbs are wired with real refusal
taxonomies — a comparison the system declines to make is a first-class answer,
not an error.

On top of that spine sits an interpretation layer, whose job is to make the
system's own record legible to the person using it:

- **The exploration ledger** counts every look at the data in a session and
  applies Benjamini–Hochberg across the family. A pre-registered prediction with
  a stated direction is exempt; a comparison the platform refused still counts,
  because it was a look even though it produced no statistic.
- **Withdrawn sources** — harvesting marks a source the repository stopped
  publishing, and the report says what in the project still rests on it. It
  never calls a withdrawal a retraction: an embargo, a correction and a
  retraction arrive identically, and the feed does not say which.
- **Fork lineage** reads `forked_from_run_id` and `fork_reason` back, so a
  sensitivity branch is visible as a branch. Nothing counts forks against the
  researcher — forking is how sensitivity analysis is done.
- **Export staleness** compares the hash recorded when a document was exported
  against what the analyses say now, and keeps "you edited this" apart from "the
  numbers moved underneath it". Only the second is alarming.
- **Pre-registration that is actually checked.** A registration can state the
  analysis it intends — method, design, covariates, exclusions — and the system
  compares that with what was really run. This closes a loophole in the ledger
  above: the exemption from multiple-comparison correction used to ask only
  whether a registration existed, was unedited and came first, all of which can
  be true of an analysis with nothing to do with the plan. It is now earned by
  matching, and a deviating test rejoins the family it belongs to.

  Three rules keep it usable. A plan that says nothing about covariates cannot
  be deviated from on covariates — unstated is reported as unregistered, not as
  a violation. A harmonised rename is not a change. And nothing calls a
  deviation misconduct: deviating is usually right, and the point is to state it
  deliberately rather than have a reviewer find it. The *Deviations from the
  registered plan* section is generated from the record with every reason left
  blank, because the system knows what changed and only the researcher knows
  why.

What is missing is the spatial canvas, most of the integration surface, and the
video engine. `ROADMAP.md` is the live document: it records what exists, what
does not, and the order the rest is being built in. Where an earlier audit was
wrong, the correction is kept rather than quietly edited out.

**Nothing here is a placeholder presented as working functionality.** That is
the standard the project sells itself on, so it is also the thing most worth
checking: `tests/test_packaging.py` and the primitive registry exist to make
drift between what is claimed and what runs visible in CI rather than in a demo.

The current suite is **1302 backend tests and 1269 web tests**, with 13 backend
skips. Nine carry a reason CI's allowlist recognises — a skip with an
unrecognised reason fails the build, so the suite cannot quietly shrink. The
other four are the speech tests, whose reason (`openai-whisper is not
installed`) was never added to that allowlist, so a dispatched run would fail on
it; recorded as D030 in `TASKS.md` rather than fixed in passing, because editing
`ci.yml` is itself a reason to dispatch.

Those numbers are checked by `tests/test_readme_claims.py`, which collects the
suite and compares. They were wrong before it existed — the file said 848 and
161 long after both had moved — and a document that claims a number nothing
verifies is the same defect this project spends its time hunting elsewhere.

A recurring class of defect here is worth naming, because most of the last
wave's work was it: **a column written by one part of the system and read by
none.** A withdrawn source that no screen mentions, a fork reason recorded and
never displayed, a render hash stored so staleness would be "provable" and
compared by nothing. Each one passed every test, because nothing was broken —
the feature simply had no reader. `tests/test_sql_references.py` catches the
narrower version (a query naming a column that does not exist); the wider
version is only found by reading the schema against the code, which is why
`ROADMAP.md` and `TASKS.md` record where it has been found before.

## Layout

Every directory below exists. A layout that lists directories which are not
there is the same defect as a feature claim that is not true, so what is planned
but unbuilt is named separately, underneath.

```
apps/
  api/                  FastAPI application — HTTP surface only, no research logic
  web/                  Next.js researcher interface
packages/
  schemas/              Pydantic domain schemas, versioned
  model/                Model providers, prompts, and the provider interface
  research-domain/      The research model: objects, lineage, claims, evidence,
                        findings, the notebook, and the durable workflow contracts
  connector-sdk/        Connector interface and capability model
  ingestion/            PDF, spreadsheet and document parsing
  visual-spec/          ResearchVisualSpec, recommendation, critic, renderers
services/
  workers/              Durable background workers
  scientific-runtime/   Isolated analysis execution
docs/                   Architecture decisions and phase records
tests/                  Cross-package tests
evals/                  Self-evaluation harness
scripts/                manage.py, and the shell wrappers around it
```

Database migrations are not a top-level directory: they live with the code that
owns the schema, at
`packages/research-domain/src/throughline_domain/migrations/`, applied in
filename order by `migrate.py`.

The Markdown files at the root are not interchangeable, and reading the wrong
one is the usual way two people end up doing the same work twice:

| File | Answers |
|---|---|
| `README.md` | How do I run this, and what is it? |
| `ROADMAP.md` | What is the project trying to become, and what is honestly missing? Corrections to earlier audits are kept, not edited out. |
| `TASKS.md` | Who is doing what **right now**. Where it disagrees with the roadmap, this one is current. |
| `CONTRIBUTING.md` | The workflow two people share without colliding. |
| `PLAN.md` | The original specification the section numbers (§55, §102) refer to. |
| `docs/MASTER_BUILD_PROMPT.md` | The immersive-spatial specification, verbatim, with its hash pinned. Committed rather than remembered: 236 sections do not survive being carried in anybody's head. |
| `docs/REQUIREMENTS.md` | What exists against each of those 236 sections. `unreviewed` means no claim has been made yet, and the count may only fall. |
| `CLAUDE.md` | Instructions for Claude sessions working in this repository. |

Planned and not present: the Scientific Motion Grammar and the deterministic 4K
scene renderer. Institutional sign-on and the licensed bibliographic databases
are likewise unbuilt.

The durable workflow contracts live inside `research-domain` as `workflow.py`
rather than in a package of their own.

Business logic does not live in React components. Research logic does not live
in API route handlers.

## Models

The platform runs without a model at all. Every deterministic path — ingestion,
statistics, validation, the refusal taxonomies, provenance, export — works with
no provider configured, and says so plainly rather than degrading into a version
of the product that quietly makes things up.

Two backends exist, and the choice between them is a privacy decision:

| | |
|---|---|
| `ollama` *(default)* | Runs on this machine. Nothing sent to it leaves the device, which is what makes it usable on unpublished data. |
| `anthropic` | A frontier model, for statistical interpretation. **Sends text to a hosted API.** |

Nothing selects the hosted provider implicitly. There is no fallback that
reaches for it when the local model is missing: that would move unpublished
research off the machine to fix an availability problem, which is not a trade
the software gets to make on a researcher's behalf. It is configured
deliberately, or not at all.

```bash
export THROUGHLINE_MODEL_PROVIDER=anthropic
export ANTHROPIC_API_KEY=...
```

The hosted client is an optional extra (`pip install 'throughline-model[anthropic]'`);
a workspace without it reports the provider unavailable rather than failing to
import.

## Requirements

**Any Python 3.8 or newer, and git.** That is the whole list, and it is short
because the bootstrap now fetches what it actually runs on rather than asking
you to.

What it fetches, and why it cannot just use yours:

- **Python 3.12 exactly** — not merely 3.12 or newer. `pgserver`, which provides
  the embedded PostgreSQL, publishes no wheel past cp312, so 3.13 and 3.14
  cannot install the database. A relocatable build is downloaded from
  `python-build-standalone`, verified against a checksum committed to this
  repository, and the bootstrap re-executes itself under it. If your machine
  already has 3.12, that one is used and nothing is downloaded.
- **Node 20+**, because `serve.sh` runs `next start` — the interface is a Node
  process at runtime, not only at build time. Fetched the same way, and skipped
  when the machine already has one new enough. The API and workers run without
  it; `serve.sh` says so plainly rather than appearing to start.

Both land in `~/.throughline-os/runtimes`, versioned, so an update can be walked
back. `THROUGHLINE_RUNTIME_DIR` moves them; `THROUGHLINE_SKIP_NODE=1` declines
the Node download for a deliberately headless install.

PostgreSQL is **not** a prerequisite — `pgserver` bundles a real PostgreSQL with
pgvector as a Python wheel and runs it against a local data directory.

Linux, macOS and Windows. The analysis sandbox was POSIX-only until it grew a
Windows backend built on Job Objects; see `services/scientific-runtime`.

## Quick start

```bash
curl -fsSL https://raw.githubusercontent.com/SarthakPattnaik1/throughline-os/main/scripts/install.sh | sh
```

That clones the repository to `~/throughline-os` (override with
`THROUGHLINE_INSTALL_DIR`) and runs the bootstrap. A terminal line rather than a
download on purpose: the quarantine flag that triggers Gatekeeper and SmartScreen
is set by the downloading browser, not by the operating system, so this path
carries no security warning at all.

From an existing clone, the bootstrap directly:

```bash
./scripts/bootstrap.sh
```

Then:

```bash
./scripts/dev.sh
```

On Windows, or wherever bash is not the shell, call the launcher directly — the
shell scripts are wrappers around it and there is no separate implementation to
fall out of step:

```
python scripts\manage.py bootstrap
python scripts\manage.py dev
```

## Working with someone else

Two people build this repository, so the loop starts and ends with a command
rather than with `git push`:

```bash
./scripts/sync.sh
```

```bash
./scripts/preflight.sh --full
```

`sync` fetches and prints what everyone else is on — branches, and who is
claiming what in `TASKS.md`. Running it first is not politeness: the same
feature has been written twice from two clones before, which cost a day and is
why the ledger exists at all.

`CONTRIBUTING.md` has the rest, including why a single contributor showing up
under two `user.name` values is harmless (`sync` prints the email beside the
name, so a second identity never reads as a second person).

New API surface goes in its **own router module** mounted with one line in
`app.py`, rather than as more routes inside it. This is a merge decision, not an
architectural one: `app.py` is the file two branches always both touch, and the
last wave merged with zero conflicts because nothing new was added to it.

## Is this installation healthy?

```bash
python scripts/manage.py doctor
```

One pass over the Python version, the virtualenv, Node, the hand-tracking model
and its hash, both ports, the database and its migrations, and whether this
machine has haptic hardware. Every failing check names the command that fixes
it, and a port held by an already-running Throughline is reported as *already
serving* rather than as a failure.

## Trying it by hand

`docs/TRY_IT.md` is a walkthrough for testing the product yourself — starting
the stack, what to look at, how to exercise the gesture controls, and what each
startup failure means. It is written to be followed with nobody to ask, and
`tests/test_try_it_guide.py` checks its claims against the code so it cannot
quietly go stale.

## Container

The image ships the embedded PostgreSQL rather than expecting an external one,
because that is what the product is: a workspace someone installs, not a service
someone operates.

**The container is x86-64 only, and that is a hard limit rather than a default.**
The embedded PostgreSQL (`pgserver`) publishes no Linux ARM build — no aarch64
wheel in any release, and no source distribution to fall back on — so the image
is pinned to `linux/amd64`. On an ARM host it therefore runs under emulation,
and `pgvector`, which is compiled C using SIMD instructions, crashes the server
as it loads. PostgreSQL itself is fine under emulation; the extension is not.

So **on an Apple Silicon Mac, use `scripts/bootstrap.sh` rather than the
container.** It is the supported route there and considerably faster besides.
Running the image anyway is not dangerous — it refuses at startup with an
explanation instead of failing halfway through a migration — but it will not
run.

This is invisible to CI, which is why it is written down here: the docker job
runs on `ubuntu-latest`, which is amd64, so it passes and would keep passing.

There is no published image; build it first.

```bash
docker build -t throughline-os .
```

```bash
docker run -p 3000:3000 -p 8080:8080 -v throughline:/data throughline-os
```

**The volume is not optional.** PostgreSQL runs inside the container and writes
to `/data`; without a mount, the entire corpus is destroyed by the second
`docker run`, and it presents as the application having forgotten everything
rather than as a missing volume.

The health check answers 200 while degraded on purpose. A workspace with no
model still does everything deterministic, and restarting it would lose
in-flight work to fix nothing. Only an unreachable database answers 503.

## Backups

Local-first means the researcher owns the only copy, so backup is part of the
product rather than an operational afterthought.

```bash
./scripts/backup.sh
```

```bash
./scripts/restore.sh <archive.tar> [--force]
```

One archive holds a `pg_dump` of the database *and* a tarball of the `objects`
tree, because they reference each other: a figure render is a row pointing at a
file, and restoring either alone produces a corpus whose provenance links
resolve to nothing. The database is dumped rather than file-copied — a
file-level copy of a running PostgreSQL is not a consistent snapshot.

## Tests

Before pushing, run what CI runs, in one command:

```bash
python scripts/manage.py preflight
```

It finds node the same way `dev` does — including a user-local install at
`~/.local/opt/node` that is not on `PATH` — so it works in a shell where a bare
`npx` would not. It also refuses to start while another `pytest tests` is
running: both suites share one embedded PostgreSQL, and two concurrent runs
produce a scatter of unrelated failures that look like real regressions and are
not.

The suites individually:

```bash
.venv/bin/python -m pytest tests -q
```

```bash
cd apps/web && npm test
```

`npm test` is `vitest run` from `package.json`, which resolves the local binary;
`npx vitest run` reaches for the network if the package is missing.

CI runs the suite across Linux and macOS, a Windows job for the sandbox, the web
build, and a Docker job that builds the image and polls `/api/health` until the
API answers inside it — a Dockerfile that is written but never built is not
evidence of anything.

### What counts as a passing test here

A test that passes is not evidence on its own; a test that fails when the
behaviour it names is removed is. Several guards in this repository were written,
seen green, and found to be checking nothing — `tests/test_sql_references.py`
passed with the exact bug it existed to catch reintroduced, because it only
inspected the first string literal in each statement.

So the standard for anything load-bearing is: **break it deliberately and watch
the test fail.** Where that has been done, `TASKS.md` records which mutation
killed which test in the `Evidence` column. "Looks right" is not evidence, and
neither is a green run.
