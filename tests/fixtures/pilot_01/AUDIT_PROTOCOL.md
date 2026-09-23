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

## Frozen analysis contract

The same machine-readable contract also fixes the supported Pilot 01 analysis:

- method: `pearson_correlation`
- x: `flipper_length_mm`
- y: `body_mass_g`
- sample size: `342`
- Pearson r: `0.8712017673060112`
- p-value: `4.370680963000641e-107`
- 95% CI low: `0.8430410326303456`
- 95% CI high: `0.8945989968524182`

Changing any of these values creates a different Pilot 01 acceptance contract
and requires both audit gates to be rerun deliberately.

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
   audit. The verifier must be given that exact SHA and must fail if HEAD differs
   or if the working tree contains tracked or untracked changes.
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

### Gate B command

Use the one-command runner from a fresh clone or clean checkout of the exact
audited commit.

Windows PowerShell:

```powershell
python scripts/run_pilot_01_gate_b.py <exact-commit-supplied-for-Gate-B> --operator "<auditor-name-or-handle>" --record "$env:USERPROFILE\pilot-01-gate-b.json"
```

macOS/Linux:

```bash
python scripts/run_pilot_01_gate_b.py <exact-commit-supplied-for-Gate-B> --operator "<auditor-name-or-handle>" --record "$HOME/pilot-01-gate-b.json"
```

The runner enforces this order:

1. remove any ignored pre-existing `.venv` so Git cleanliness cannot hide a
   reused author environment;
2. strip inherited `THROUGHLINE_*`, `PIP_*`, `PYTHONPATH`, and
   `PYTHONHOME` overrides that could redirect data or package resolution;
3. require HEAD to equal the supplied commit and require a clean working tree;
4. verify the frozen CSV bytes, SHA-256, and canonical Git blob;
5. create a unique fresh test/database/runtime home;
6. download the repository-pinned CPython archive into that private runtime
   home and verify its committed SHA-256 before use;
7. bootstrap Throughline through the documented installer with that pinned
   interpreter;
8. reverify the exact clean checkout after bootstrap;
9. verify the frozen Python/core scientific environment;
10. record `pip freeze --all` so the complete resolved Python environment is
    part of the evidence;
11. run `tests/test_pilot_01_palmer_penguins.py` unmodified;
12. write a structured JSON record containing the auditor identity, exact argv,
    command outputs, platform, exact commit, environment, and pass/fail result.

The audit record must be written outside the repository. The runner refuses a
`--record` path inside the checkout so recording the audit cannot itself make
the audited working tree dirty.

Gate B passes only when the second person reproduces the frozen contract from a
clean checkout.

### Frozen environment

Pilot 01 now recreates and verifies an exact core scientific environment:

- Python `3.12.14`
- NumPy `2.3.2`
- pandas `2.3.2`
- SciPy `1.16.1`
- statsmodels `0.14.5`

The Python runtime is pinned in `scripts/runtimes.py`. The core scientific
packages are pinned in `requirements/scientific-runtime.lock`. Bootstrap, CI,
Docker, and Gate B use those same pins.

`scripts/verify_pilot_01.py --environment` is a hard verification step, not
just an informational printout. Gate B must stop and record a failure if any
installed version differs from the frozen environment.

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

The scientific analysis itself must complete. The capability refusal is:

```text
Replay receipt v1 does not yet reproduce declarative row filters; filtered runs are refused rather than replayed against different rows.
```

The two public artifacts intentionally expose these exact messages:

Replay receipt:

```text
Replay receipt v1 does not yet reproduce declarative row filters; filtered runs are refused rather than replayed against different rows.
```

Code export:

```text
Replay receipt v1 does not yet reproduce declarative row filters; filtered runs are refused rather than replayed against different rows. Replay-supported methods: pearson_correlation, spearman_correlation.
```

Changing the species, operator, value, capability reason, or either public
artifact message creates a different audit contract.
