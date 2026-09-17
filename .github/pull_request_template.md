## What this changes

<!-- Explain the problem, the change, and why it belongs in Throughline. -->

## Evidence

<!-- Name exactly what you ran and what passed. Commands, test files, counts,
     screenshots, or measured behavior are useful. "Tested" is not evidence. -->

## Research / security impact

<!-- Note effects on provenance, statistical behavior, privacy, authorization,
     network access, model providers, migrations, or release/update behavior. -->

- [ ] No secrets, credentials, tokens, private datasets, unpublished research, personal data, or local environment files are included
- [ ] I reviewed the diff for accidental generated files and unrelated changes
- [ ] New or changed identifier-bearing API inputs have explicit ownership/isolation coverage where applicable

## Before merging

- [ ] `./scripts/preflight.sh --full` passed locally, or the reason it could not run is documented below
- [ ] Required GitHub Actions checks passed on the current PR head commit
- [ ] Documentation/public claims were updated where behavior changed
- [ ] `TASKS.md` was updated when this work corresponds to a tracked internal task
- [ ] Caveats and intentionally untested areas are stated below
- [ ] Maintainer / code-owner review completed

## UI changes

<!-- Remove this section if not applicable. -->

- [ ] Primary journey exercised
- [ ] Keyboard/focus behavior checked
- [ ] Narrow-screen behavior checked
- [ ] Loading, empty, error, and disabled states considered
- [ ] Screenshots contain only synthetic/public data and no sensitive information

## Caveats

<!-- What is NOT covered, what is unverified, or what you deliberately left for later.
     Missing evidence should be stated, not converted into a passing claim. -->
