## What this changes

<!-- Explain the change and why it belongs in Throughline. -->

## Evidence

<!-- Name exactly what you ran and what passed. Commands, test files, counts,
     screenshots, or measured behavior are useful. "Tested" is not evidence. -->

## Security / data check

- [ ] No secrets, credentials, tokens, private datasets, or local environment files are included
- [ ] I reviewed the diff for accidental generated files or unrelated changes

## Before merging

- [ ] `./scripts/preflight.sh --full` passed locally
- [ ] **Dispatched CI on this branch** and it passed:
      `gh workflow run ci.yml --ref <this-branch>`
- [ ] `TASKS.md` updated when this work corresponds to a tracked task
- [ ] Caveats and intentionally untested areas are stated below
- [ ] Maintainer / code-owner review completed before merge

## Caveats

<!-- What is NOT covered, what is untested, or what you deliberately left for
     later. A precise caveat is better than an overbroad completeness claim. -->
