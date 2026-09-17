"""Read-only CI evidence check. Requires an authenticated GitHub CLI, no paid service.

Usage: python scripts/check_ci_evidence.py --pr 17
       python scripts/check_ci_evidence.py --sha <full commit SHA>
This checks recorded execution, not application correctness or release readiness.
"""
import argparse
import json
import re
import subprocess
import sys

REQUIRED = {
    "ubuntu-latest · python 3.12": {"Run the suite", "Every skip must be accounted for"},
    "macos-latest · python 3.12": {"Run the suite", "Every skip must be accounted for"},
    "windows · sandbox": {"The Windows sandbox actually confines"},
    "web interface": {"Run npm test", "Run npm run build"},
    "docker build": {"Build the image", "The API answers in the built image"},
}


def api(path, collection=None):
    command = ["gh", "api", "--method", "GET", path]
    if collection:
        command += ["--paginate", "--slurp"]
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
    data = json.loads(result.stdout)
    if collection:
        return [item for page in data for item in page[collection]]
    return data


def latest_run(runs, sha):
    eligible = [r for r in runs if r.get("head_sha") == sha
                and r.get("path", "").split("@")[0] == ".github/workflows/ci.yml"
                and r.get("event") in {"push", "pull_request", "workflow_dispatch"}]
    return max(eligible, key=lambda r: r["id"], default=None)


def assess(run, jobs, sha):
    problems = []
    if not run or run.get("head_sha") != sha:
        return ["No CI run for the requested exact commit."]
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        problems.append("Latest CI run is not completed successfully.")
    for name, required_steps in REQUIRED.items():
        matches = [j for j in jobs if j.get("name") == name]
        if len(matches) != 1:
            problems.append(f"{name}: expected one job, found {len(matches)}.")
            continue
        job = matches[0]
        steps = job.get("steps") or []
        if not steps:
            problems.append(f"{name}: no recorded steps; execution is unverified.")
            continue
        if job.get("head_sha") != sha or job.get("run_id") != run["id"]:
            problems.append(f"{name}: job belongs to a different commit or run.")
        if job.get("run_attempt") != run.get("run_attempt"):
            problems.append(f"{name}: job belongs to a different attempt.")
        if job.get("status") != "completed" or job.get("conclusion") != "success":
            problems.append(f"{name}: job did not succeed.")
        for step_name in required_steps:
            found = [s for s in steps if s.get("name") == step_name]
            if len(found) != 1 or found[0].get("status") != "completed" or found[0].get("conclusion") != "success":
                problems.append(f"{name}: required step {step_name!r} did not execute successfully.")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="SarthakPattnaik1/throughline-os")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--pr", type=int)
    target.add_argument("--sha")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repo):
        parser.error("--repo must be owner/name")
    root = f"repos/{args.repo}"
    try:
        sha = api(f"{root}/pulls/{args.pr}")["head"]["sha"] if args.pr else args.sha
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            parser.error("--sha must be a full 40-character commit SHA")
        # Do not filter by event: PR-only queries miss push and dispatch runs.
        runs = api(f"{root}/actions/runs?head_sha={sha}&per_page=100", "workflow_runs")
        run = latest_run(runs, sha)
        jobs = api(f"{root}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100", "jobs") if run else []
        problems = assess(run, jobs, sha)
        if run:
            current = api(f"{root}/actions/runs/{run['id']}")
            if current.get("run_attempt") != run["run_attempt"] or current.get("status") != run["status"]:
                problems.append("Run changed while checking; check again.")
            newest = latest_run(api(f"{root}/actions/runs?head_sha={sha}&per_page=100", "workflow_runs"), sha)
            if not newest or newest["id"] != run["id"]:
                problems.append("A newer run appeared while checking; check again.")
        if args.pr and api(f"{root}/pulls/{args.pr}")["head"]["sha"] != sha:
            problems.append("PR head changed while checking; check again.")
        print(json.dumps({"sha": sha, "run_url": run.get("html_url") if run else None,
                          "verified": not problems, "problems": problems}, indent=2))
        return 1 if problems else 0
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        # Do not dump stderr: authentication helpers may include sensitive data.
        print(f"CI evidence unavailable ({type(exc).__name__}); no pass recorded.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
