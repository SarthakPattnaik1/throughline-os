# Security policy

Throughline is an early research release. It is not medical or clinical decision
software. Back up research before upgrading and review statistical conclusions
independently. There is no security certification or guaranteed response SLA.

## Supported versions

Security fixes target the latest tagged release and current main. Older releases
do not receive guaranteed backports. Include the full commit SHA or release
version in every report; an unreleased branch is not a verified release.

## Private reports

Use GitHub's **Report a vulnerability** on this repository's Security tab:
https://github.com/SarthakPattnaik1/throughline-os/security/advisories/new

If private reporting is unavailable, open an issue titled **Private security
contact requested** with no vulnerability details and ask a maintainer for a
private reporting channel. Do not attach an exploit to that issue.

Include privately: affected version and platform, a minimal reproduction using
synthetic data, expected and observed behavior, impact, and redacted logs.
Cross-account or cross-project access, sandbox escapes, unsafe network fetches,
credential exposure, and release-verification bypasses are security reports.

Do not post credentials, signing keys, session cookies, database exports,
unpublished research, personal information, or an unpatched exploit in public
issues, discussions, or pull requests. Test only installations and data you own
or have explicit permission to test.

## Response expectations

Reports are handled on a best-effort basis by the maintainers. There is no
guaranteed acknowledgement or fix deadline and no paid incident-response
service. Use the private report thread for follow-ups. Maintainers and reporters
should agree on disclosure timing after assessing impact and mitigation.

Before public release, maintainers must enable private vulnerability reporting
and verify that the reporting link works from a non-maintainer account.
