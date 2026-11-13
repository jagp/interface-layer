"""Tests for pipeline.py. Every command goes through catalog.run / run_status, faked here."""
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

    def test_skipped_beside_a_success_passes(self):
        self.assertTrue(pipeline.checks_pass([check_run(conclusion="SKIPPED"), check_run()]))

    def test_only_skipped_is_not_a_pass(self):
        self.assertFalse(pipeline.checks_pass([check_run(conclusion="SKIPPED")]))   # nothing actually ran


def view(rollup, oid="aaa", mergeable="MERGEABLE", draft=False):
    return {"statusCheckRollup": rollup, "headRefOid": oid, "mergeable": mergeable, "isDraft": draft}


class MergeMovePrs(unittest.TestCase):
    """Several move PRs in one repo, each judged on its own."""

    def merged(self, listed, views, dry_run=False, merge_result=(0, "")):
        calls, lines = [], []

        def run(*args, **kwargs):
            calls.append(args)
            if args[:3] == ("gh", "pr", "list"):
                return json.dumps(listed)
            if args[:3] == ("gh", "pr", "view"):
                return json.dumps(views[args[3]])
            return ""

        def run_status(*args, **kwargs):
            calls.append(args)
            return merge_result
        with mock.patch.object(catalog, "run", run), mock.patch.object(catalog, "run_status", run_status), \
             mock.patch.object(catalog, "report", lines.append):
            pipeline.merge_move_prs("jagp/x", "x", dry_run=dry_run)
        return [call for call in calls if call[:3] == ("gh", "pr", "merge")], lines

    def test_only_the_green_one_merges_and_only_the_checked_head(self):
        merges, lines = self.merged(
            [pr("release/1.1.0", "main", number=7), pr("main", "develop", number=8), pr("feature/x", "develop", number=9)],
            {"7": view([check_run()], oid="abc123"), "8": view([check_run(status="IN_PROGRESS", conclusion="")])})
        self.assertEqual(merges, [("gh", "pr", "merge", "7", "--repo", "jagp/x", "--merge", "--match-head-commit", "abc123")])
        self.assertTrue(any("#8" in line and "waiting" in line for line in lines))
        self.assertFalse(any("#9" in line for line in lines))        # features: not even mentioned

    def test_dry_run_merges_nothing(self):
        merges, lines = self.merged([pr("release/1.1.0", "main", number=7)], {"7": view([check_run()])}, dry_run=True)
        self.assertEqual(merges, [])
        self.assertTrue(any("#7" in line and "would merge" in line for line in lines))

    def test_drafts_and_conflicts_are_skipped_with_a_reason(self):
        merges, lines = self.merged(
            [pr("main", "develop", number=8), pr("main", "release/1.2.0", number=9)],
            {"8": view([check_run()], draft=True), "9": view([check_run()], mergeable="CONFLICTING")})
        self.assertEqual(merges, [])
        self.assertTrue(any("#8" in line and "draft" in line for line in lines))
        self.assertTrue(any("#9" in line and "CONFLICTING" in line for line in lines))

    def test_a_failed_merge_is_reported_and_the_next_pr_still_runs(self):
        merges, lines = self.merged(
            [pr("main", "develop", number=8), pr("main", "release/1.2.0", number=9)],
            {"8": view([check_run()]), "9": view([check_run()])},
            merge_result=(1, "GraphQL: Head branch was modified\n"))
        self.assertEqual(len(merges), 2)
        self.assertTrue(any("#8" in line and "Head branch was modified" in line for line in lines))

    def test_one_release_into_main_per_run(self):
        merges, lines = self.merged(
            [pr("hotfix/1.1.1", "main", number=7), pr("release/1.2.0", "main", number=8)],
            {"7": view([check_run()]), "8": view([check_run()])})
        self.assertEqual([call[3] for call in merges], ["7"])
        self.assertTrue(any("#8" in line and "one release into main per run" in line for line in lines))


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


class PrepareClone(unittest.TestCase):
    """finish-release.sh only sees LOCAL branches; a fresh clone has one."""

    def created(self, kind, branch):
        calls = []
        def run(*args, **kwargs):
            calls.append(args)
            if "refs/remotes/origin" in args:
                return "origin/HEAD\norigin/main\norigin/develop\norigin/release/1.2.0\norigin/hotfix/1.1.1\n"
            if "refs/heads" in args:
                return "main\n"                                    # the default branch, checked out
            return ""
        with mock.patch.object(catalog, "run", run):
            pipeline.prepare_clone("/clone", kind, branch)
        return [call[-2] for call in calls if call[3:4] == ("branch",)]

    def test_hotfix_gets_develop_its_branch_and_open_releases(self):
        self.assertEqual(sorted(self.created("hotfix", "hotfix/1.1.1")), ["develop", "hotfix/1.1.1", "release/1.2.0"])

    def test_release_gets_develop_and_its_branch_only(self):
        self.assertEqual(sorted(self.created("release", "release/1.2.0")), ["develop", "release/1.2.0"])


class FinishRelease(unittest.TestCase):
    """finish-release.sh is run, then publish, then the origin branch delete."""

    def finish(self, finish_code, finish_output, publish_output="ok: pushed v1.1.0 to origin\n", dry_run=False):
        statuses, lines = [], []
        def run_status(*args, **kwargs):
            statuses.append(args)
            if args[3] in ("finish", "plan"):
                return finish_code, finish_output
            if args[3] == "publish":
                return 0, publish_output
            return 0, "ok: deleted\n"
        with mock.patch.object(catalog, "run", lambda *a, **k: ""), \
             mock.patch.object(pipeline, "prepare_clone", lambda *a: None), \
             mock.patch.object(catalog, "run_status", run_status), \
             mock.patch.object(catalog, "report", lines.append), \
             mock.patch.dict("os.environ", {"GH_TOKEN": "t"}):
            outcome = pipeline.finish_release("jagp/x", "x", "release", "1.1.0", "/gitflow", dry_run=dry_run)
        return outcome, statuses, lines

    def test_finish_then_publish_then_delete_remote(self):
        output = ("ok: tagged main as v1.1.0 (local)\n"
                  "ask: delete-remote release/1.1.0 " + "f" * 40 + " — delete it too?\n"
                  "ask: push-tag v1.1.0 — push the tag and publish the release?\n")
        outcome, statuses, _ = self.finish(0, output)
        self.assertEqual(outcome, "finished")
        self.assertEqual([call[2:4] for call in statuses],
                         [("release", "finish"), ("release", "publish"), ("delete-remote", "release/1.1.0")])

    def test_a_named_refusal_is_refused_not_failed(self):
        outcome, statuses, lines = self.finish(2, "error: main does not contain release/1.1.0 — squashed\n")
        self.assertEqual(outcome, "refused")
        self.assertEqual(len(statuses), 1)
        self.assertTrue(any("squashed" in line for line in lines))

    def test_an_unexpected_exit_is_a_failure(self):
        outcome, _, _ = self.finish(1, "bash: line 3: oops\n")
        self.assertEqual(outcome, "failed")

    def test_a_missing_release_page_is_reported(self):
        output = "ask: push-tag v1.1.0 — push the tag and publish the release?\n"
        _, _, lines = self.finish(0, output, publish_output="ok: pushed v1.1.0\nwarn: GitHub Release not created — run: gh release create v1.1.0\n")
        self.assertTrue(any("GitHub Release not created" in line for line in lines))

    def test_dry_run_rehearses_with_plan_in_a_real_clone(self):
        outcome, statuses, lines = self.finish(0, "plan: merge into main (pr): done\n", dry_run=True)
        self.assertEqual([call[2:4] for call in statuses], [("release", "plan")])
        self.assertEqual(outcome, "finished")
        self.assertTrue(any("would finish" in line for line in lines))


class Main(unittest.TestCase):
    def run_main(self, entries, outcome="finished"):
        lines = []
        with mock.patch.object(catalog, "report", lines.append), \
             mock.patch.object(catalog, "load_catalog", lambda: entries), \
             mock.patch.object(pipeline, "merge_move_prs", lambda *a: 0), \
             mock.patch.object(pipeline, "releases_to_finish", lambda *a: [("release", "1.1.0")]), \
             mock.patch.object(pipeline, "finish_release", lambda *a: outcome), \
             mock.patch.dict("os.environ", {"GH_TOKEN": "t"}):
            code = pipeline.main([])
        return code, lines

    GITHUB = {"name": "x", "source": {"source": "url", "url": "https://github.com/jagp/x.git"}}

    def test_no_token_skips_the_plugin_repos(self):
        lines = []
        with mock.patch.object(catalog, "report", lines.append), mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(pipeline.main([]), 0)
        self.assertTrue(any("PIPELINE_TOKEN not set" in line for line in lines))

    def test_a_path_source_is_skipped_and_the_rest_proceed(self):
        code, lines = self.run_main([{"name": "local", "source": "./plugins/local"}, self.GITHUB])
        self.assertEqual(code, 0)
        self.assertTrue(any(line.startswith("local: skipped") for line in lines))

    def test_a_refusal_keeps_the_run_green(self):
        self.assertEqual(self.run_main([self.GITHUB], outcome="refused")[0], 0)

    def test_a_failure_turns_it_red(self):
        self.assertEqual(self.run_main([self.GITHUB], outcome="failed")[0], 1)


if __name__ == "__main__":
    unittest.main()
