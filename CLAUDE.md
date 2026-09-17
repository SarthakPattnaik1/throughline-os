## Start here: the shared ledger

Several people and several Claude sessions share this repository and cannot see
each other's terminals. `TASKS.md` at the root is the only thing they share.

**Before proposing any plan:**

```bash
git fetch --all --prune && git pull --rebase
```

then read `TASKS.md` and say what is claimed, what is open, and what is `done`
but not yet `verified`. Do not start anything another session has claimed.

`ROADMAP.md` says what the project is trying to become and why. `TASKS.md` says
who is doing what right now. When they disagree, the ledger is current.

The protocol for claiming, closing, verifying and recording discoveries — and
the rules that keep two sessions from clobbering each other's rows — is in
`.claude/skills/ledger/SKILL.md`. Invoke it with `/ledger`, or just follow it.

Two rules carry most of the value: **a claim you have not pushed does not
exist**, and **`done` is not `verified`** — the author says `done`, someone
else says `verified` and names what they ran.

## CI and security checks run automatically

This repository has automatic GitHub Actions coverage. Do not assume that an
absence of red means a pass; inspect the checks attached to the exact commit or
pull request.

`CI` runs automatically on every pull request targeting `main` and every push
to `main`. It also supports manual dispatch. Its current matrix verifies:

- Ubuntu + Python 3.12 using the documented bootstrap path and full backend suite
- macOS + Python 3.12 using the documented bootstrap path and full backend suite
- the Windows sandbox tests
- web install, tests, lint and production build
- a Docker image build plus loopback health check

Every backend skip must be accounted for by `scripts/check_test_skips.py`.

`CodeQL` also runs automatically on pull requests and pushes to `main`, plus a
weekly scheduled scan. It analyzes Python and JavaScript/TypeScript with the
security-extended query suite.

Do not manually dispatch duplicate CI runs while an automatic run for the same
head is already active. If a check fails, inspect the failing job and logs;
distinguish a product failure from a transient runner/network failure before
rerunning anything.

When reporting verification, name the exact evidence: local tests, CI jobs,
CodeQL, or all three. A change is not verified merely because it was pushed.
