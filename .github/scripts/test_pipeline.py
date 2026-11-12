"""Tests for pipeline.py. Every command goes through catalog.run, faked here."""
import json
import unittest
from unittest import mock

import catalog
import pipeline


def pr(head, base, cross=False, number=1):
    return {"number": number, "headRefName": head, "baseRefName": base, "isCrossRepository": cross}


class IsMovePr(unittest.TestCase):
    def test_release_and_hotfix_into_main(self):
        self.assertTrue(pipeline.is_move_pr(pr("release/1.1.0", "main")))
        self.assertTrue(pipeline.is_move_pr(pr("hotfix/1.0.1", "main")))

    def test_back_merges(self):
        self.assertTrue(pipeline.is_move_pr(pr("main", "develop")))
        self.assertTrue(pipeline.is_move_pr(pr("main", "release/1.1.0")))

    def test_not_a_move(self):
        self.assertFalse(pipeline.is_move_pr(pr("feature/x", "develop")))   # features are decisions
        self.assertFalse(pipeline.is_move_pr(pr("release/1.1", "main")))     # not X.Y.Z
        self.assertFalse(pipeline.is_move_pr(pr("release/1.1.0", "main", cross=True)))  # a fork


def check_run(status="COMPLETED", conclusion="SUCCESS"):
    return {"__typename": "CheckRun", "status": status, "conclusion": conclusion}


def status_context(state="SUCCESS"):
    return {"__typename": "StatusContext", "state": state}


class ChecksPass(unittest.TestCase):
    def test_no_checks_is_not_a_pass(self):
        self.assertFalse(pipeline.checks_pass([]))

    def test_one_success(self):
        self.assertTrue(pipeline.checks_pass([check_run()]))

    def test_pending_is_not_a_pass(self):
        self.assertFalse(pipeline.checks_pass([check_run(status="IN_PROGRESS", conclusion="")]))

    def test_a_failing_status_fails_it(self):
        self.assertFalse(pipeline.checks_pass([check_run(), status_context("FAILURE")]))

    def test_skipped_counts_as_passing(self):
        self.assertTrue(pipeline.checks_pass([check_run(conclusion="SKIPPED"), check_run()]))


class MergeMovePrs(unittest.TestCase):
    """Two move PRs in one repo: one green, one still running."""

    def fake(self, calls):
        listed = [pr("release/1.1.0", "main", number=7), pr("main", "develop", number=8),
                  pr("feature/x", "develop", number=9)]
        rollups = {"7": [check_run()], "8": [check_run(status="IN_PROGRESS", conclusion="")]}

        def run(*args, **kwargs):
            calls.append(args)
            if args[:3] == ("gh", "pr", "list"):
                return json.dumps(listed)
            if args[:3] == ("gh", "pr", "view"):
                return json.dumps({"statusCheckRollup": rollups[args[3]]})
            return ""
        return run

    def merged(self, dry_run):
        calls, lines = [], []
        with mock.patch.object(catalog, "run", self.fake(calls)), \
             mock.patch.object(catalog, "report", lines.append):
            pipeline.merge_move_prs("jagp/x", "x", dry_run=dry_run)
        return [call for call in calls if call[:3] == ("gh", "pr", "merge")], lines

    def test_only_the_green_one_merges(self):
        merges, lines = self.merged(dry_run=False)
        self.assertEqual(merges, [("gh", "pr", "merge", "7", "--repo", "jagp/x", "--merge")])
        self.assertTrue(any("#8" in line and "waiting" in line for line in lines))
        self.assertFalse(any("#9" in line for line in lines))        # features: not even mentioned

    def test_dry_run_merges_nothing(self):
        merges, lines = self.merged(dry_run=True)
        self.assertEqual(merges, [])
        self.assertTrue(any("#7" in line and "would merge" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
