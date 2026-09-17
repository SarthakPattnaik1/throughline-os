# Public release evidence

Throughline is an early research release, not medical or clinical decision
software. A live landing page is not evidence that installation, isolation,
analysis, or recovery works.

## Check the candidate's actual CI

With Python and an authenticated GitHub CLI:

```sh
python scripts/check_ci_evidence.py --pr 17
python scripts/check_ci_evidence.py --sha FULL_40_CHARACTER_RELEASE_COMMIT
```

The command only reads GitHub; it does not dispatch jobs or change billing.
Exit 0 means the latest matching CI run completed successfully and all five
required jobs have successful required steps. Exit 1 means missing or unsuccessful
evidence; exit 2 means the evidence could not be read. Push, PR and manual runs
are included, all pages are read, and jobs must belong to the same commit and
attempt. A PR head change during the check invalidates the result.

No recorded steps means execution is unverified. It does not identify billing
as the cause by itself; inspect GitHub's job annotation before attributing it.
Missing, skipped, cancelled, refused, and stale checks do not satisfy this gate.

This is a review helper, not a protected-branch rule or an automatic release
block. It verifies job evidence, not whether a workflow's test commands are
sufficient; workflow edits need review. Main currently uses manual dispatch.

## Evidence still required before public exposure

- Bring the security changes and current main together and resolve conflicts.
  Re-run the route-derived T185 ownership tests on that combined commit.
- Record Ubuntu and macOS suites, Windows sandbox, web tests and production
  build, Docker build and health check on the exact release commit.
- Scan all history and refs for secrets, signing material and private data.
  A current-files scan or release archive cannot establish history safety.
  Review findings privately and rotate any exposed credentials.
- Review internal documentation and release contents for private information.
- Enable and verify the private reporting route described in SECURITY.md.
- Rehearse installation without repository credentials on clean Linux, macOS
  and Windows machines; exercise first account, source import, analysis,
  validation, report export, backup and restore. Also test two independent
  accounts for isolation. Record the version, commands, results and limitations.
- Configure main's required reviews and successful checks before merge; verify
  the repository rules in GitHub. This document does not configure them.

Keep hosted Actions spending capped at zero. If the private repository's free
allowance is exhausted, paid capacity is not a prerequisite to developing fixes:
use existing local machines for testing and wait for free capacity to reset for
hosted verification. Local results must be labelled local and do not substitute
for missing platform evidence. Do not expose unscanned history to obtain CI.

The Apache-2.0 license is already present. It does not certify release readiness.
