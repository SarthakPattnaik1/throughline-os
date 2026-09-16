# Throughline OS

An AI-native research operating system.

**Deployment target: local-first desktop.** One researcher, one machine. The
database, the job queue and the scientific sandbox all run locally. Nothing
leaves the machine unless the researcher connects an external service — and
where one can be connected, the interface says so before it is used.

## Start here

**Early research release — not medical or clinical decision software.**
Do not use this release for diagnosis, treatment, or clinical decisions.
Cross-platform release verification is incomplete; a live demo is not proof
that a clean installation or every integration works.

1. Choose one installation route under [Quick start](#quick-start).
2. Use a disposable project and synthetic or non-sensitive data first.
3. Follow [the guided walkthrough](docs/TRY_IT.md) before enabling optional features.
4. If setup fails, run `python scripts/manage.py doctor` and review the named
   corrective action. Remove secrets and personal data before sharing diagnostics.

External integrations are optional. Check what data a provider receives before
connecting it; do not upload confidential research just to test connectivity.

## Status

The scientific spine works end to end: a paper goes in, claims come out, a
dataset tests them, and the result renders as a publication figure with its
provenance intact. All six of Part I's comparison verbs are wired with real refusal
taxonomies — a comparison the system declines to make is a first-class answer,
not an error.

That vocabulary now covers images as well. **Scan ↔ scan** compares a received
case against the scans a researcher already holds, and sorts them by whether
they *may* be compared rather than by how much they resemble it: appearance in
medical imaging is dominated by acquisition rather than by pathology, so a list
ordered by resemblance is mostly a list of scans taken on the same machine. It
partitions and never ranks — an ordered list of similar cases is a differential
diagnosis whatever it is labelled. NIfTI files are read in the browser and never
persisted, identifiers are never written into the project, and a region marked
on the case is echoed only onto scans the verdict permits, because drawing it
elsewhere would assert a correspondence that does not exist.

DICOM headers are read whatever the file, and pixels only where they are stored
uncompressed — so a compressed series is still *judged* for comparability, which
takes only the header, and declined for display with its transfer syntax named.
That split matters because DICOM is the only format here that records modality,
sequence weighting and contrast phase; a NIfTI carries none of them, which is
why two NIfTIs honestly come back as *cannot be judged*.

A library scales by being **queried on acquisition rather than ranked on
resemblance**: "every portal-venous CT at a millimetre or under" is the same
facts the verdict rests on, asked as a question. A query built from the case
returns exactly the scans the verdict calls directly comparable — a test holds
the two together, because two answers to one question that disagreed would make
both untrustworthy. Scans whose headers cannot answer are neither matched nor
excluded but counted separately, since a filter reporting twelve matches while
silently dropping forty unreadable headers is lying by omission.

Marks survive between sessions; scans do not. A mark attaches to a *salted
hash* of the series UID, computed with a salt that never leaves the machine —
so reopening the same series brings the marks back, while nothing stored points
at a patient or a study, and the same file opened elsewhere would not find them.
They are kept in browser storage rather than the project record, because a mark
carries free text and free text is the most reliable way an identifier escapes a
research system.

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

The spatial layer renders rather than being catalogued.
`apps/web/lib/charts3d/registry.ts` names all 253 visualizations the brief lists
and records which of them anything can actually draw — **216 today, against 86
at the start of the wave, and no primitive left unwritten**. Eight primitives
account for all of them, because a named visualization is a configuration of a
primitive rather than a chart of its own: `surface` 55, `points` 36, `network`
35, `glyphs` 28 (vector and tensor fields), `volume` 26 (voxel grids), `lines`
20 (orbits, flight paths, trajectories and streamlines), `isosurface` 12
(spheres, tori, molecular orbitals, tumour margins — and a decision boundary,
which in three inputs is a shell rather than a height field) and `bars` 4. The
remaining 37 need somebody else's reader — DICOM, NIfTI, Mol\*, a CAD kernel —
which the registry keeps apart from a missing renderer because the costs differ
in kind.

These figures are checked against the registry by
`apps/web/tests/the-readme-counts-what-is-there.test.ts`. They were wrong here
for a long time — 238 and 199, when the catalogue held 253 and drew 216 — which
is the same defect the catalogue *page* had already been repaired for: a
headline number typed into a file, going quietly out of date while the code it
described grew. The page derives its number now; this paragraph cannot, so a
test derives it instead.

`bars` is the one §10 warns against, and it is built to say so: all four of its
entries are *framed* rather than inherently spatial, so the chart measures what
depth costs it — how many bars are hidden behind others, and how much taller the
near row reads for the same value — and puts both in the caption. At the default
view of a 5×5 grid that is three hidden and 56% of stretch.

Each of those charts states what it is hiding, which for a spatial chart is not
a courtesy. Depth buys occlusion, so a volume reports how many voxels fall below
the window, how many sit inside it but too faint for a pixel to show, and what
the sampling stride was; a field says how many arrows were shortened to fit and
to read those by colour instead; a set of paths says where the measurements had
holes in them, because a line drawn across a gap is a confident claim about
ground nothing was recorded on. `/charts-3d` draws all six from synthetic data,
with one hand driving whichever chart it is over — which is where `bounds()` is
actually used rather than merely implemented.

The flat charts became interactive in the same wave: hover emphasis, a tooltip
carrying the real value in the reader's units rather than in pixels, and
highlighting linked between a chart and its data table, across 12 of the 13
primitives.

What is missing is most of the integration surface, the Scientific Motion
Grammar and the video engine. `ROADMAP.md` is the live document: it records what exists, what
does not, and the order the rest is being built in. Where an earlier audit was
wrong, the correction is kept rather than quietly edited out.

**Every statistical method is checked against somebody else's implementation.**
`evals/conformance.py` walks the method registry rather than a list somebody
maintains: each method either has a scipy reference or a written reason why one
cannot exist, and a method with neither fails the build. It runs over sixty
random frames chosen to be awkward — unequal groups, single-member groups, tied
ranks — because a hand-picked example can pass while the edge cases diverge.
Agreement is required to floating-point noise rather than to a few decimal
places, since two implementations of one closed form should differ only in
rounding.

Agreeing with scipy is necessary and not sufficient: a number can be computed
correctly and then be labelled, paired or judged wrongly. The latest wave found
several of those:
- A rank-biserial correlation had its sign reversed.
- Kruskal–Wallis reported eta-squared-H under the name epsilon-squared.
- A proportion of variance explained was judged on the correlation scale.
- A regression's headline p-value belonged to the whole model while the
  estimate beside it belonged to one predictor.
- Cramér's V was taken from the continuity-corrected statistic.

Each result's headline numbers now describe the same thing, and each effect
size is judged on its own scale.

**A corrected result gets one verdict everywhere.** A discovery run's
false-discovery rate is the researcher's to set, and whether a q-value survived
is decided by one rule, `discovery.survived_correction`, at the rate its own run
was corrected at. Before that, the correction promoted a result at 0.10 and
eight later readers each re-derived the verdict with `q < 0.05`, including:
- the patterns screen;
- the claim test, which called the promoted discovery a null;
- validation;
- the figures and the result card.

The interface now shows the server's verdict rather than computing its own, and
a source scan fails any new comparison of a q-value with a fixed number.

**Nothing here is a placeholder presented as working functionality.** That is
the standard the project sells itself on, so it is also the thing most worth
checking: `tests/test_packaging.py` and the primitive registry exist to make
drift between what is claimed and what runs visible in CI rather than in a demo.

The current suite is **2840 backend tests and 3571 web tests**.

How many of those skip depends on which optional extras a machine has
installed, so the number is not fixed and is not claimed as one: on a checkout
with every extra present it is six, all of them `no Neo4j configured`, and on
one with none of them it is more. What *is* fixed is that every skip must carry
a reason CI's allowlist recognises — Neo4j, `cv2`, the local embedding model, or
`openai-whisper` — because a skip with an unrecognised reason fails the build.
That is the property worth stating: the suite cannot quietly shrink by skipping
its way out of a failure.

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

## Accounts and the administrator

The first account created on an installation is its **administrator**, and
nobody after it is. The role is enforced on the server, not only hidden in the
interface. Only the administrator can:
- add people;
- install feature packs;
- choose the model and save or clear its key;
- install the desktop entry;
- open sign-up to the network.

Those act on the machine for everyone who uses it rather than on one
researcher's projects, so anyone else gets a 403 and sees the setting and who
can change it, not a button that fails.

**Sign-up from the network is off by default.** Accounts can be created at the
machine itself; opening registration to other machines is a switch in Settings,
behind a confirmation. It is off by default because a laptop joins café wifi.

Every account's work stays its own. An id is checked against the project it
arrives with, whether it comes in the path or in the request body, and an id
from another project answers 404, the same as one that does not exist. Fetches
the server makes on a caller's behalf — a paper, a dataset, a repository
harvest — refuse addresses on this machine or its network, and check again
after DNS resolves and on every redirect. Rate limits count once per request,
against the route that was actually matched.

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
- **Node 20+ — but only to *build* the interface, never to run it.** The
  interface is exported to a folder of HTML, CSS and JavaScript that the API
  serves itself, so a copy that already has it built needs no Node at all. A
  source checkout builds one during bootstrap and fetches Node to do it; that
  is the only reason it is ever downloaded, and it is not needed again.

Both land in `~/.throughline-os/runtimes`, versioned, so an update can be walked
back. `THROUGHLINE_RUNTIME_DIR` moves them; `THROUGHLINE_SKIP_NODE=1` declines
the Node download for a deliberately headless install.

### One process, one port

The API serves the interface as well as answering it, on **port 8080**. There is
no second server.

That is not tidiness. The session cookie is `httpOnly` and `SameSite=strict`, so
a cross-origin request drops it *silently* — no error, just a researcher who
appears logged out. Serving both from one process makes same-origin true by
construction rather than by a proxy rule somebody has to keep correct.

`next dev` still runs on port 3000 during development, with its own proxy, so
hot reload is unaffected. Only the shipped product changed.

```bash
python scripts/manage.py build-interface
```

Exports the interface to `apps/web/out`, which is what the API serves. If it is
missing, every page answers 503 naming that command rather than showing a blank
screen.

PostgreSQL is **not** a prerequisite — `pgserver` bundles a real PostgreSQL with
pgvector as a Python wheel and runs it against a local data directory.

### Feature packs

The base install carries what every researcher needs. Nine further capabilities
are optional, reported by `/api/system/capabilities`, and installable from
Settings — or by hand, since the screen shows the command next to the button:

```bash
pip install 'throughline-domain[speech]'
```

Each one states what is **withheld** without it rather than only what it adds,
and its approximate size. That matters most for `speech`, which pulls in torch
and is gigabytes where everything else on the list is tens of megabytes.

None of them is needed to open your work, run an analysis, or read a paper. A
capability that is off is reported as *"needs the X extra"* and never as
unavailable-and-unexplained — the same rule `datasets.py` has always applied to
file formats, now applied to all of them.

Linux, macOS and Windows. The analysis sandbox was POSIX-only until it grew a
Windows backend built on Job Objects; see `services/scientific-runtime`.

## Quick start

```bash
curl -fsSL https://throughline-research.pages.dev/install.sh | sh
```

On **Windows**, in PowerShell — there is no `sh` on a stock Windows, so the line
above cannot work there and the equivalent is:

```powershell
irm https://throughline-research.pages.dev/install.ps1 | iex
```

That downloads the current release to `~/throughline-os` (override with
`THROUGHLINE_INSTALL_DIR`), checks it against the checksum the release
publishes, and runs the bootstrap. It does **not** clone the repository — this
one is private, so the clone that used to happen here failed for everybody
without credentials (D050). Set `THROUGHLINE_REPO` to take the git path on
purpose if you have access.

The commands above download and immediately execute code. For a safer reviewable
installation, download the script first, inspect it, and execute it only if you
trust its source. HTTPS and a published checksum do not independently establish
publisher identity. Do not disable operating-system security protections to install.

From an existing clone, the bootstrap directly:

```bash
./scripts/bootstrap.sh
```

Then:

```bash
./scripts/dev.sh
```

### Without a terminal

`launchers/` holds one door per platform, and **each is a file you can hand
somebody on its own**. Run it and it opens Throughline — installing it first if
that machine has not got it yet. There is one install sequence rather than three:
each launcher does two cheap local checks and then delegates.

It looks in three places, cheapest first, with the network only in the last:

1. **Inside a checkout** — running from a clone keeps working, and uses *that*
   clone rather than some other copy in the home directory.
2. **`~/throughline-os`**, or wherever `THROUGHLINE_INSTALL_DIR` points. This is
   the common case for a downloaded launcher on its second run, and it costs two
   `stat` calls.
3. **Nothing yet** — hand over to `install.sh`, which clones and sets up, and
   ends by starting it.

They previously assumed they were already inside a checkout, so a launcher saved
on its own to a downloads folder failed looking for `scripts/manage.py` one
directory up. That made them a convenience for somebody who already had the code
rather than a way to get it, which is the opposite of what a download is for.

| Platform | Double-click | First-run warning |
|---|---|---|
| macOS | `launchers/Throughline.command` | Gatekeeper — System Settings > Privacy & Security > Open Anyway, or use the Terminal one-liner |
| Windows | `launchers/Throughline.bat` | SmartScreen — More info, Run anyway, once |
| Linux | `python scripts/manage.py desktop-entry`, then Throughline in the menu | none |

These launchers are unsigned. Review their source and publisher before running
them. A missing security warning is not evidence that a download is safe.

**The window stays open while it installs.** A first run pulls several hundred
megabytes, and behind a hidden window that is indistinguishable from a freeze.

**And it opens a browser when the app answers** — not when the port starts
listening, which is several seconds earlier and shows a connection error. A
launcher that starts a server the researcher cannot see has not started
anything, as far as they can tell. `manage.py dev` deliberately does *not* do
this: a developer restarts it twenty times an hour. `THROUGHLINE_NO_BROWSER=1`
declines it.

**Settings shows the door for the machine it is running on**, under *Starting
Throughline* — including the security warning to expect, and on Linux a button
that adds the menu entry. This section is a reference; the app is where somebody
who has never opened a terminal will actually find it, which is the whole point
of the launchers existing.

Linux gets a `.desktop` entry rather than an `.AppImage`: an AppImage is a
squashfs image built by `appimagetool` around a bundled runtime, which is a
build pipeline rather than a script in this repository. The entry is written
rather than committed because it has to carry an absolute path.

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

## Building a release

```bash
python scripts/manage.py release
```

Writes a tarball, its SHA-256 and a `latest.json` manifest into `dist/`. **One
artifact serves every platform** — the interface is a static export with no
native binaries, and the runtimes are fetched per machine at install time — so a
release is one build rather than three, and 19 MB rather than the 850 MB the
build dependencies weigh.

It refuses on a dirty checkout. A release built from uncommitted changes looks
exactly like one built from a commit, and the difference only surfaces when
somebody tries to reproduce it and cannot.

The archive is an allowlist of declared paths rather than a directory walk with
exclusions, so nothing ships because it happened to be lying in the folder.

**The manifest is signed**, and the public key travels inside every tarball —
it has to arrive with the software rather than from the server being verified,
which is the whole reason a signature beats a checksum published beside its own
file. Make a key once with `python scripts/manage.py release-key`; see
`keys/README.md`.

**What a first install cannot check.** Verifying a download happens before
anything is installed, and the library that checks a signature arrives *in* that
download. So a first install trusts HTTPS and the digest in the manifest; every
update afterwards verifies the signature properly, because the virtualenv exists
by then. Said here rather than implied, because a verification that silently
does nothing is worse than none — it is believed.

## Updating

```bash
python scripts/manage.py update --check
python scripts/manage.py update
```

Never automatic, and never from inside the running app — applying an update
replaces the code the API process is executing, so it cannot swap itself out
from underneath a request. Settings has a **Check for updates** button; it
checks and then names this command.

The order matters more than the mechanism, because the database is your only
copy of your research:

1. **Refuses on a dirty checkout.** Updating fast-forwards the working tree, and
   uncommitted work is what this must not silently discard.
2. **Backs up before anything changes** — database and object store in one
   archive, since either without the other is useless.
3. **Fast-forward only.** A merge could conflict, and a half-updated checkout is
   worse than an old one.
4. Reinstalls, rebuilds the interface, migrates — in that order, because a
   migration may need code that arrived in the update.
5. **Puts the previous version back if any of that fails**, and tells you where
   the backup is. It does *not* restore the database for you: that is
   destructive and would discard anything done since the backup, which is your
   decision rather than the updater's.

An installation follows `main` until somebody tags a release, and follows tags
after that — one mechanism, not two.

**A version this installation cannot state is a hole in its own provenance.**
`main` on Tuesday and `main` on Thursday are different software wearing one
name, so the version is reported along with *where that answer came from*: a
stamped release, a git checkout, or nothing. A modified checkout says so, since
it names something nobody else can obtain.

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
docker run -p 127.0.0.1:8080:8080 -v throughline:/data throughline-os
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

CI is configured for pushes to `main` and `hardening/open-source-readiness`,
pull requests targeting `main`, and manual dispatch. GitHub may refuse jobs
before any step executes when the private account's Actions allowance is exhausted.
Such a refusal is not a test failure or a passing verification.

Before merging or tagging a release, verify all five jobs on the current candidate:
Ubuntu suite, macOS suite, Windows sandbox, web tests/build, and Docker build/health.
Record the exact commit and run links. Local tests do not verify other operating systems.
Public-source publication and a verified release are separate decisions; neither
should be represented as complete while security or installation checks remain open.

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
