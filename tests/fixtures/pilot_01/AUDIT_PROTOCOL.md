# Pilot 01 audit protocol

Pilot 01 uses the Palmer Penguins fixture in this directory. The evaluation has
two separate audit gates. Passing one does not satisfy the other.

## Frozen input

The only accepted source file is:

- path: `tests/fixtures/pilot_01/penguins.csv`
- byte length: `15,241`
- SHA-256: `f204db2c753b0937caac3cb35258562c14f073e4bbc76be24b4c51ce22767a93`

Do not refresh, normalize, re-export, reorder, or otherwise replace this CSV
without explicitly declaring a new Pilot 01 input and rerunning both gates.

Before either gate, verify the file from the repository root:

```bash
python - <<'PY'
from pathlib import Path
import hashlib
p = Path("tests/fixtures/pilot_01/penguins.csv")
b = p.read_bytes()
print(len(b))
print(hashlib.sha256(b).hexdigest())
PY
```

The result must be exactly `15241` and
`f204db2c753b0937caac3cb35258562c14f073e4bbc76be24b4c51ce22767a93`.

## Gate A — internal dry run

Purpose: prove that the frozen test contract is executable and reproducible
inside the development process.

Who may run it:
- the author
- another maintainer
- CI

Run:

```bash
python -m pytest tests/test_pilot_01_palmer_penguins.py -q
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
1. Start from a clean checkout of the exact commit under review.
2. Do not reuse the author's virtual environment, database, generated replay
   artifacts, cached outputs, or copied test results.
3. Verify the CSV byte length and SHA-256 before running anything else.
4. Bootstrap Throughline using the documented install path.
5. Run the same Pilot 01 test file without modifying the test, fixture, filter,
   tolerances, or expected values.
6. Record the commit SHA, CSV SHA-256, command executed, environment/platform,
   and pass/fail result.
7. If anything fails, record the failure as observed. Do not patch during the
   audit and continue calling it the same audit run.

Gate B passes only when the second person reproduces the frozen contract from a
clean checkout.

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
