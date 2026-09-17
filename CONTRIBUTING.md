# Working on Throughline together

Throughline is open source, but the canonical repository is maintainer-controlled.
You do not need collaborator access to contribute: fork the repository, work on a
branch, and open a pull request.

## Contribution model

- **External contributors:** fork + pull request. No direct write access is needed.
- **Trusted collaborators:** may receive repository write access, but should still
  work on branches and use pull requests.
- **`main`:** should never be used as a working branch. Changes belong in pull
  requests and should be reviewed before merge.
- **Code ownership:** `.github/CODEOWNERS` assigns the repository to
  `@SarthakPattnaik1`. When branch protection is configured to require code-owner
  review, changes cannot be merged without that approval.
- **Admin access:** keep this limited to the repository owner and only people who
  genuinely need repository-administration privileges.

## The loop

```bash
./scripts/sync.sh                  # before you start. every time.
git checkout -b feat/<what-you-are-doing>
# … build …
./scripts/preflight.sh --full      # before you push
git push -u origin feat/<what-you-are-doing>
```

Then open a pull request. `main` is what the container is built from; nothing
should reach it without passing through review.

## Why `sync` first, every time

This repository has already lost time to duplicated work: two people independently
wrote a Dockerfile, fixed the same package list, and edited the same route file.
Nothing was broken — the work was simply done twice, and one copy was discarded.

No test catches that. CI cannot catch it. It is invisible until the merge.

`sync` fetches and prints what every other unmerged branch has touched, and warns
when a file appears on your branch *and* somebody else's — including files you
have only edited locally and not yet committed, because those are the ones you
can still cheaply decide not to work on.

```text
  feat/wave-1-definition-of-done
      Dockerfile
      apps/api/src/throughline_api/app.py
      …
    ⚠ You are both editing:
      Dockerfile
```

Overlap is not an error. Two people editing `app.py` is normal and Git can merge
it. It is a prompt to coordinate before spending a day on something that already
exists.

`sync` changes nothing. It fetches, reports, and exits.

## Why `preflight` before pushing

It runs the same checks CI runs, in the same order, so a failure is yours to see
before it is anyone else's to wait on.

- `./scripts/preflight.sh` — compile, package-list drift guards, and web tests.
- `./scripts/preflight.sh --full` — the above plus the whole backend suite.
  **Use this one before pushing.**

## Claim a wave before building it

`ROADMAP.md` is the live plan and the waves are deliberately independent. Say
which one you are taking before you start — a branch named after it is enough,
since `sync` shows branch names to everyone else.

## Tests

Write the test against the property, not against your implementation.

A better implementation should be free to change internal structure without
breaking a test whose real purpose is unchanged.

## Pull-request expectations

A pull request should:

- explain what changed and why;
- include concrete evidence of testing;
- call out caveats and intentionally untested areas;
- avoid mixing unrelated changes;
- avoid committing secrets, credentials, private datasets, or generated local
  environment files;
- receive maintainer review before merge.

## CI does not run by itself

The full GitHub Actions workflow is currently dispatch-only. Run it before merging
into `main` and before publishing a release:

```bash
gh workflow run ci.yml --ref <branch>
```

or use **Run workflow** in the GitHub Actions tab.

The full run covers the backend suite on Linux and macOS, the Windows sandbox,
the web build, and a Docker build that verifies `/api/health` from the built
container.
