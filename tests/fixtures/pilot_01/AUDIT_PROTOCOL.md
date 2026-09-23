# Pilot 01 audit protocol

Pilot 01 uses the Palmer Penguins fixture in this directory. The evaluation has
two separate audit gates. Passing one does not satisfy the other.

## Frozen input

The machine-readable source of truth is
`tests/fixtures/pilot_01/FROZEN_INPUT.json`.

The frozen input is:

- path: `tests/fixtures/pilot_01/penguins.csv`
- byte length: `15,241`
- SHA-256: `f204db2c753b0937caac3cb35258562c14f073e4bbc76be24b4c51ce22767a93`
- canonical upstream repository: `allisonhorst/palmerpenguins`
- canonical upstream commit: `156daa4301838d9fdcc5b018b29b9149bf975552`
- canonical upstream path: `inst/extdata/penguins.csv`
- canonical upstream Git blob: `25b46d384bf81f8399188500ea54917bb49d8890`

Do not refresh, normalize, re-export, reorder, or otherwise replace this CSV
without explicitly declaring a new Pilot 01 input and rerunning both gates.

Before either gate, from the repository root run:

```text
python scripts/verify_pilot_01.py
```

This verifier uses only the Python standard library, so it must run before
Throughline is bootstrapped. It verifies byte length, SHA-256, and the Git blob
identity against the single frozen-input manifest.

## Gate A — internal dry run

Purpose: prove that the frozen test contract is executable and reproducible
inside the development process.

Who may run it:

- the author
- another maintainer
- CI

After bootstrap, run the Pilot with the virtualenv interpreter.

macOS/Linux:

```text
./.venv/bin/python -m pytest tests/test_pilot_01_palmer_penguins.py -q
```

Windows PowerShell:

```text
.venv\Scripts\python -m pytest tests/test_pilot_01_palmer_penguins.py -q
```

Gate A passes only if the complete Pilot 01 test file passes against the frozen
CSV bytes and the exact commit being reviewed.

A Gate A result is an internal validation result. It must never be described as
the independent audit.

## Gate B — independent second-person audit

Purpose: test the same frozen contract without relying on the author's working
state, interpretation, or local artifacts.

Who runs it:

- a person other than the author of the Pilot 01 implementation/dry run

Required conditions:

1. Start from a fresh clone or clean checkout of the exact commit supplied for
   audit. Record it with `git rev-parse HEAD`.
2. Do not reuse the author's virtual environment, database, generated replay
   artifacts, cached outputs, or copied test results.
3. Run `python scripts/verify_pilot_01.py` before bootstrap. If it fails, stop
   and record Gate B as failed.
4. Bootstrap Throughline using the documented platform path.
5. Use a fresh test home for the audit run so an older local PostgreSQL test
   cluster cannot be reused.
6. Run the same Pilot 01 test file without modifying the test, fixture, filter,
   tolerances, or expected values.
7. Record the commit SHA, CSV SHA-256, command executed, environment/platform,
   installed scientific dependency versions, and pass/fail result.
8. If anything fails, record the failure as observed. Do not patch during the
   audit and continue calling it the same audit run.

### Windows PowerShell Gate B commands

From a fresh checkout of the exact audited commit:

```powershell
git rev-parse HEAD
python scripts/verify_pilot_01.py
python scripts/manage.py bootstrap

$env:THROUGHLINE_TEST_HOME = Join-Path (Get-Location) (".pilot01-gateb-" + [guid]::NewGuid().ToString())
$env:THROUGHLINE_HOME = $env:THROUGHLINE_TEST_HOME

.venv\Scripts\python scripts/verify_pilot_01.py --environment
.venv\Scripts\python -m pytest tests/test_pilot_01_palmer_penguins.py -q
```

### macOS/Linux Gate B commands

From a fresh checkout of the exact audited commit:

```bash
git rev-parse HEAD
python scripts/verify_pilot_01.py
./scripts/bootstrap.sh

export THROUGHLINE_TEST_HOME="$(mktemp -d)/throughline-pilot01-gateb"
export THROUGHLINE_HOME="$THROUGHLINE_TEST_HOME"

./.venv/bin/python scripts/verify_pilot_01.py --environment
./.venv/bin/python -m pytest tests/test_pilot_01_palmer_penguins.py -q
```

Gate B passes only when the second person reproduces the frozen contract from a
clean checkout.

### Environment limitation

Replay receipt v1 records the Python and scientific-library versions that
produced a run, but the normal bootstrap currently installs compatible current
versions rather than reconstructing those recorded versions exactly. Therefore
Gate B must record the installed versions printed by
`scripts/verify_pilot_01.py --environment`. A future replay milestone may add
environment restoration; Pilot 01 does not claim that capability.

## Separation rule

Gate A and Gate B must be recorded as two different events with different
operators when applicable. An internal run may precede the external audit, but
it cannot substitute for it.

The second-person auditor must not be handed a precomputed result and asked only
to confirm it. The point is to independently execute the frozen contract.

## Frozen refusal case

The refusal case is fixed as:

```python
[{"column": "species", "operator": "eq", "value": "Adelie"}]
```

The scientific analysis itself must complete. The replay script and replay
receipt must refuse because declarative filters are outside replay receipt v1.
Changing the species, operator, value, or refusal reason creates a different
audit contract.
