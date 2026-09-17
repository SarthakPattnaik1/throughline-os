import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / "scripts" / (name + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


ci = module("check_ci_evidence")
skips = module("check_test_skips")
SHA = "a" * 40


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.run = dict(id=7, head_sha=SHA, run_attempt=2, path=".github/workflows/ci.yml",
                        event="push", status="completed", conclusion="success")
        self.jobs = [dict(name=name, head_sha=SHA, run_id=7, run_attempt=2,
                          status="completed", conclusion="success",
                          steps=[dict(name=s, status="completed", conclusion="success") for s in steps])
                     for name, steps in ci.REQUIRED.items()]

    def test_all_required_executed(self):
        self.assertEqual(ci.assess(self.run, self.jobs, SHA), [])

    def test_refused_jobs_cannot_pass(self):
        for job in self.jobs:
            job["steps"] = None
        self.assertEqual(len(ci.assess(self.run, self.jobs, SHA)), 5)

    def test_missing_jobs_cannot_pass(self):
        self.assertTrue(ci.assess(self.run, self.jobs[:-1], SHA))

    def test_missing_build_step_cannot_pass(self):
        self.jobs[3]["steps"] = [s for s in self.jobs[3]["steps"] if s["name"] != "Run npm run build"]
        self.assertTrue(ci.assess(self.run, self.jobs, SHA))

    def test_skipped_required_step_cannot_pass(self):
        self.jobs[0]["steps"][0]["conclusion"] = "skipped"
        self.assertTrue(ci.assess(self.run, self.jobs, SHA))

    def test_old_sha_or_attempt_cannot_pass(self):
        for field, value in [("head_sha", "b" * 40), ("run_attempt", 1), ("run_id", 6)]:
            with self.subTest(field=field):
                jobs = copy.deepcopy(self.jobs)
                jobs[0][field] = value
                self.assertTrue(ci.assess(self.run, jobs, SHA))

    def test_every_supported_trigger_is_seen(self):
        for event in ["push", "pull_request", "workflow_dispatch"]:
            with self.subTest(event=event):
                self.run["event"] = event
                self.assertEqual(ci.latest_run([self.run], SHA), self.run)

    def test_new_failure_supersedes_old_success(self):
        newer = dict(self.run, id=8, conclusion="failure")
        self.assertEqual(ci.latest_run([newer, self.run], SHA), newer)
        self.assertTrue(ci.assess(newer, self.jobs, SHA))

    def test_unrelated_workflow_and_sha_are_excluded(self):
        self.assertIsNone(ci.latest_run([dict(self.run, path="other.yml"), dict(self.run, head_sha="b"*40)], SHA))

    def test_api_failure_does_not_pass(self):
        with patch.object(ci, "api", side_effect=OSError("unavailable")):
            self.assertEqual(ci.main(["--sha", SHA]), 2)

    def test_missing_skip_report_fails(self):
        self.assertEqual(skips.main(["/this-path-does-not-exist/pytest.out"]), 1)

    def test_incomplete_report_fails(self):
        for text in ["", "collecting ...", "1 skipped in 1s"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                skips.check(text)

    def test_expected_optional_skip_passes(self):
        skips.check("SKIPPED [1] tests/test_graph.py:1: no Neo4j configured\n10 passed, 1 skipped in 2s")

    def test_unexpected_skip_fails(self):
        with self.assertRaises(ValueError):
            skips.check("SKIPPED [1] tests/test_security.py:1: database broken\n10 passed, 1 skipped in 2s")


if __name__ == "__main__":
    unittest.main()
