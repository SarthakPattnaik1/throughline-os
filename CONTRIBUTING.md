# Contributing

Throughline OS is an early research release, not medical or clinical decision software.

## Development setup

Use a branch for your changes. Follow the README installation instructions;
the bootstrap installs the Python packages and builds the web interface.

```text
python scripts/manage.py bootstrap
python scripts/manage.py dev
```

Before submitting, run:

```text
python scripts/manage.py preflight
```

Local verification does not certify other operating systems.

## Pull requests

Describe the problem, the change, how you tested it, and any remaining limitations.
Keep changes focused. Include regression tests for bug fixes and ownership-boundary
tests for identifier-bearing API inputs. Test behavior rather than implementation shape.

For interface changes, check the primary journey, keyboard navigation, narrow
screens, loading, empty, and error states. Include screenshots with synthetic data.
For integrations, test missing credentials, denied access, timeouts, rate limits,
and safe retries. Document unsupported cases.

Never submit API keys, session cookies, database dumps, private research, or personal
information. Review logs and screenshots before attaching them.

## CI and release verification

CI is configured for pull requests targeting main and configured branch pushes;
manual dispatch is also available. A billing refusal before step 1 means no tests
executed. It is neither a passing check nor evidence of a code defect.

Before merging or tagging, record the exact candidate commit and successful results
for the Ubuntu suite, macOS suite, Windows sandbox, web tests/build, and Docker
build/health. A changed head needs new verification. Do not bypass required checks.

## Security and conduct

Do not post vulnerabilities publicly. Obtain a maintainer-confirmed private
reporting channel before sharing exploit details or sensitive evidence.
Respect contributors, provide actionable feedback, and avoid harassment or
disclosing another person's private information.

By submitting a contribution, you agree to license it under the repository's
Apache-2.0 license. Submit only material you have the right to contribute.
