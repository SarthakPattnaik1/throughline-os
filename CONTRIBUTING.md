# Contributing to Throughline

Thank you for considering a contribution. Throughline is an early research release, not medical or clinical decision software. The canonical repository is maintainer-controlled, but contribution does not require collaborator access: fork the repository, work on a branch, and open a pull request.

## Before you start

- Check existing issues and pull requests to avoid duplicating work.
- For a substantial feature or architectural change, open an issue first so scope and interfaces can be discussed before implementation.
- Do not include confidential research, credentials, access tokens, private datasets, database exports, session cookies, signing material, or personal information in issues, logs, screenshots, tests, or commits.
- Security vulnerabilities belong in the private reporting route described in `SECURITY.md`, not in public issues.

## Development setup

From a source checkout:

```bash
python scripts/manage.py bootstrap
python scripts/manage.py dev
```

The bootstrap installs the workspace packages and builds the web interface. Run diagnostics with:

```bash
python scripts/manage.py doctor
```

## Branches and pull requests

- Never use `main` as a working branch.
- External contributors should fork the repository and submit a pull request.
- Collaborators with write access should still use branches and pull requests.
- Keep each pull request focused on one coherent change.
- Maintainer/code-owner review is required before merge.
- Do not bypass failing or missing required verification to merge a change.

A typical local loop is:

```bash
git checkout -b feat/<short-description>
# make the change
./scripts/preflight.sh --full
git push -u origin feat/<short-description>
```

`scripts/sync.sh` can be used by collaborators working in the canonical repository to inspect overlapping branches. Fork-based contributors do not need it.

## Maintainer areas

Throughline may assign maintainers to specific technical areas. Area maintainers have
primary review and decision responsibility within their documented scope rather than
serving only as informal reviewers.

Current roles and responsibilities are listed in `MAINTAINERS.md`. Automatic review
routing is defined in `.github/CODEOWNERS`.

For a change that falls inside a maintainer-owned area:

- request review from the relevant area maintainer;
- treat that maintainer as the primary reviewer for area-specific standards and acceptance criteria;
- involve additional maintainers or the project owner when the change crosses architecture,
  security/privacy, persistence, release, or other ownership boundaries.

Repository permissions and branch-protection rules remain the authority for merge access.

## Tests

Write tests against behavior and invariants, not incidental implementation shape. A better implementation should be free to change internal structure without breaking a test whose actual guarantee still holds.

For bug fixes, include a regression test when practical. For security-sensitive or identifier-bearing API changes, include ownership/isolation tests that prove an object from another project or account cannot be read or mutated through the changed path.

For interface changes, exercise:

- the primary user journey;
- keyboard navigation and focus behavior;
- narrow screens;
- loading, empty, error, and disabled states;
- reduced-motion or accessibility behavior when relevant.

For integrations, test missing credentials, denied access, malformed responses, timeouts, rate limits, and safe retry behavior where applicable.

## Local verification

Before pushing, run:

```bash
./scripts/preflight.sh --full
```

The individual suites are also available:

```bash
.venv/bin/python -m pytest tests -q
cd apps/web && npm test
```

Local verification does not establish that another operating system or a clean installation works.

## CI

CI runs automatically for pull requests targeting `main` and for pushes to `main`; it can also be dispatched manually. The workflow verifies:

- the backend suite on Ubuntu;
- the backend suite on macOS;
- the Windows sandbox implementation;
- web tests, lint, and production build;
- Docker build plus an in-container `/api/health` check.

A GitHub Actions billing or infrastructure refusal before job steps execute is missing evidence, not a passing run and not evidence of a code defect. A changed pull-request head requires verification of the new commit.

## Pull-request expectations

A pull request should:

- explain the problem and why the change belongs in Throughline;
- describe the implementation at the level needed for review;
- include the exact verification performed;
- call out caveats and intentionally untested areas;
- avoid unrelated generated files or drive-by refactors;
- update documentation when behavior, setup, security boundaries, or public claims change;
- update `TASKS.md` when the work corresponds to a tracked internal task.

## Code and architecture expectations

- Research logic belongs in the domain packages, not React components or HTTP handlers.
- New API surface should go into a focused router/module rather than further growing `apps/api/src/throughline_api/app.py`.
- Keep network access explicit. Do not silently send research content to hosted services.
- Prefer fail-closed behavior at privacy, authorization, signature-verification, and release-evidence boundaries.
- Preserve provenance: a change that makes an output impossible to trace back to its inputs, method, version, or evidence needs redesign before merge.

## Security and privacy

Read `SECURITY.md` before changing authentication, authorization, network fetches, model-provider behavior, update/release verification, sandboxing, or secrets handling.

Never weaken a security control merely to make a test or release checklist pass. If verification is unavailable, state that evidence is unavailable.

## Licensing

By submitting a contribution, you agree that your contribution may be distributed under the repository's Apache-2.0 license. Submit only material you have the right to contribute.

## Community expectations

Participation in this project is governed by `CODE_OF_CONDUCT.md`. Technical disagreement is welcome; harassment, personal attacks, and disclosure of another person's private information are not.
