# Public release and distribution evidence

Throughline's source repository is public. That does **not** mean a tagged binary/release candidate has been verified for broad distribution. Throughline is an early research release, not medical or clinical decision software. A public repository or live landing page is not evidence that installation, isolation, analysis, update, backup, or recovery works on every supported platform.

This document is the gate for claiming that a specific commit is release-ready.

## Check the candidate's actual CI

With Python and an authenticated GitHub CLI:

```sh
python scripts/check_ci_evidence.py --sha FULL_40_CHARACTER_RELEASE_COMMIT
```

For a pull request candidate, use its current PR number:

```sh
python scripts/check_ci_evidence.py --pr PR_NUMBER
```

The command only reads GitHub. Exit 0 means the latest matching CI run completed successfully and all required jobs have successful required steps. Exit 1 means evidence is missing or unsuccessful; exit 2 means evidence could not be read.

A changed PR head invalidates evidence from the old head. Missing, skipped, cancelled, refused, stale, or infrastructure-blocked checks do not satisfy the release gate.

CI now runs automatically for pull requests targeting `main` and for pushes to `main`; manual dispatch remains available. Repository rules should require the relevant successful checks before merge. This file is evidence policy, not a substitute for GitHub ruleset configuration.

## Release candidate checklist

A commit may be called release-ready only when all applicable items below have recorded evidence for that exact commit.

### Security and isolation

- [ ] Route-derived ownership/isolation tests pass on the candidate commit.
- [ ] Any open security-hardening branch whose fixes are not on the candidate has been reconciled or explicitly shown to be superseded.
- [ ] Two independent accounts have been exercised against the same installation and cannot read or mutate each other's projects or project objects.
- [ ] Server-side fetch protections are exercised against loopback/private-network targets and redirects.
- [ ] Authentication, authorization, rate limiting, CSP/cookie settings, update verification, and sandbox tests pass.
- [ ] Private vulnerability reporting is enabled and verified from a non-maintainer account.

### Clean-environment verification

- [ ] Ubuntu backend suite passed.
- [ ] macOS backend suite passed.
- [ ] Windows sandbox job passed.
- [ ] Web tests, lint, and production build passed.
- [ ] Docker image built and `/api/health` answered from the built container.
- [ ] No required job was merely skipped, cancelled, or blocked before execution.

### Installation and recovery rehearsal

Using clean machines or clean virtual machines, without repository credentials:

- [ ] Linux installation completed using a documented route.
- [ ] macOS installation completed using a documented route.
- [ ] Windows installation completed using a documented route.
- [ ] First account/setup flow completed.
- [ ] A source was imported.
- [ ] A dataset analysis completed.
- [ ] Validation completed.
- [ ] A report/figure export completed.
- [ ] Backup completed.
- [ ] Restore into a clean installation completed and provenance links remained usable.

Record the exact version/commit, platform, commands, results, and limitations. Local testing on one machine does not establish another platform.

### Repository and supply-chain hygiene

- [ ] Scan all reachable Git history and refs for secrets, signing material, credentials, private data, and accidentally committed environment files.
- [ ] Review findings privately and rotate any credential that may have been exposed; deletion alone is not rotation.
- [ ] Review the release archive contents against the allowlist and confirm no developer-only/private files are shipped.
- [ ] Review dependency advisories for the resolved release dependency graph.
- [ ] Confirm the release manifest/checksum/signature behavior on an actual generated release archive.
- [ ] Confirm the version reported by the running application matches the candidate being tested.

### Documentation and user expectations

- [ ] README installation instructions match the current public repository and release mechanism.
- [ ] `SECURITY.md`, `SUPPORT.md`, `CONTRIBUTING.md`, and `CODE_OF_CONDUCT.md` are present and current.
- [ ] Known limitations are stated without presenting planned or unverified behavior as complete.
- [ ] External-service/model privacy behavior is stated before use.
- [ ] The release notes identify breaking changes, migrations, known issues, and rollback/backup requirements.

## Open-source readiness versus release readiness

The repository can accept public contributions while a release candidate is still unverified. These are separate claims:

- **Open-source ready** means licensing, contribution guidance, conduct rules, security reporting, issue intake, review controls, and CI are present and usable by outsiders.
- **Release ready** means a particular commit has passed the security, cross-platform, installation, recovery, and supply-chain evidence above.

Do not represent one as proof of the other.

## Evidence retention

For every tagged release, retain or link to:

- full commit SHA;
- CI run URL(s);
- clean-install rehearsal notes;
- secret/dependency scan summary;
- release archive hash/signature evidence;
- known limitations and unresolved issues.

If evidence is unavailable, state that it is unavailable. Do not convert missing verification into a passing claim.

The Apache-2.0 license permits use and contribution; it does not certify scientific correctness, security, fitness for a particular purpose, or release readiness.
