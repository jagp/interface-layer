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



class ReleasesToFinish(unittest.TestCase):
    """Merged release PRs whose tag is not on origin yet."""

    def found(self, tags, heads):
        lines = []
        def run(*args, **kwargs):
            if args[:3] == ("gh", "pr", "list"):
                return json.dumps([{"headRefName": "release/1.1.0"}, {"headRefName": "feature/x"},
                                   {"headRefName": "release/1.1.0"}])
            if args[:3] == ("git", "ls-remote", "--tags"):
                return "".join(f"abc\trefs/tags/{tag}\n" for tag in tags)
            if args[:3] == ("git", "ls-remote", "--heads"):
                return "".join(f"abc\trefs/heads/{head}\n" for head in heads)
            return ""
        with mock.patch.object(catalog, "run", run), mock.patch.object(catalog, "report", lines.append):
            return pipeline.releases_to_finish("jagp/x", "x"), lines

    def test_untagged_with_its_branch(self):
        self.assertEqual(self.found(tags=["v1.0.0"], heads=["release/1.1.0"])[0], [("release", "1.1.0")])

    def test_already_tagged_is_done(self):
        self.assertEqual(self.found(tags=["v1.1.0"], heads=["release/1.1.0"])[0], [])

    def test_branch_gone_is_reported_not_finished(self):
        found, lines = self.found(tags=[], heads=[])
        self.assertEqual(found, [])
        self.assertTrue(any("origin/release/1.1.0 is gone" in line for line in lines))


class FinishRelease(unittest.TestCase):
    """finish-release.sh is run, then publish, then the origin branch delete."""

    def finish(self, finish_code, finish_output):
        statuses, lines = [], []
        def run_status(*args, **kwargs):
            statuses.append(args)
            if args[2:4] == ("release", "finish"):
                return finish_code, finish_output
            return 0, "ok: pushed v1.1.0 to origin\n"
        with mock.patch.object(catalog, "run", lambda *a, **k: ""), \
             mock.patch.object(catalog, "run_status", run_status), \
             mock.patch.object(catalog, "report", lines.append), \
             mock.patch.dict("os.environ", {"GH_TOKEN": "t"}):
            done = pipeline.finish_release("jagp/x", "x", "release", "1.1.0", "/gitflow", dry_run=False)
        return done, statuses, lines

    def test_finish_then_publish_then_delete_remote(self):
        output = ("ok: tagged main as v1.1.0 (local)\n"
                  "ask: delete-remote release/1.1.0 " + "f" * 40 + " — delete it too?\n"
                  "ask: push-tag v1.1.0 — push the tag and publish the release?\n")
        done, statuses, _ = self.finish(0, output)
        self.assertTrue(done)
        self.assertEqual([call[2:4] for call in statuses],
                         [("release", "finish"), ("release", "publish"), ("delete-remote", "release/1.1.0")])

    def test_a_refusal_stops_before_publish(self):
        done, statuses, lines = self.finish(2, "error: main does not contain release/1.1.0 — squashed\n")
        self.assertFalse(done)
        self.assertEqual(len(statuses), 1)
        self.assertTrue(any("squashed" in line for line in lines))


class Main(unittest.TestCase):
    def test_no_token_skips_the_plugin_repos(self):
        lines = []
        with mock.patch.object(catalog, "report", lines.append), mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(pipeline.main([]), 0)
        self.assertTrue(any("PIPELINE_TOKEN not set" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
