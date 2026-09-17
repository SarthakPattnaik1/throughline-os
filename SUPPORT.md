# Support

Throughline is an early research release maintained on a best-effort basis. There is no guaranteed support, uptime, compatibility, or response SLA.

## Before opening an issue

1. Check the README and `docs/TRY_IT.md`.
2. Run:

   ```bash
   python scripts/manage.py doctor
   ```

3. Search existing issues for the same failure.
4. Reproduce with synthetic or non-sensitive data when possible.

## Bug reports

Use the bug-report issue template and include:

- Throughline version or full commit SHA;
- operating system and architecture;
- installation route used;
- exact steps to reproduce;
- expected and observed behavior;
- the smallest safe log excerpt that demonstrates the problem.

Do **not** attach credentials, session cookies, database dumps, unpublished research, confidential datasets, private documents, or personal information. Redact filenames and paths when they reveal sensitive project information.

## Feature requests

Use the feature-request template. Describe the research workflow or user problem first, then the proposed feature. A feature request is easier to evaluate when it explains what evidence or task cannot currently be represented, validated, or completed.

## Security vulnerabilities

Do not open a public issue. Follow `SECURITY.md`.

## Scientific or statistical concerns

If you believe an analysis method, statistical interpretation, or research claim is incorrect, open a bug report using a synthetic or public reproducible example. Include the reference method or expected result where possible. Treat scientific correctness issues as bugs, not as feature requests.

## Installation and environment problems

Include `doctor` output only after reviewing and redacting it. If the problem occurs during installation, state whether the machine is behind a proxy, whether installation was online or offline, and whether the failure occurred before or after the virtual environment was created.

## Scope

Maintainers may close issues that cannot be reproduced, are outside the project's scope, contain no actionable information after follow-up, or request unsafe behavior. Closing an issue does not mean the reported experience was invalid; it means the repository cannot act on it with the available evidence.
