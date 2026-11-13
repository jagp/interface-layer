"""Tests for bump_pins.open_prs: a validated bump is merged when asked to."""
import unittest
from unittest import mock

import bump_pins
import catalog

BUMP = ("omnilog", "jagp/omnilog", "v1.2.0", "a" * 40, "1.2.0")


class OpenPr(unittest.TestCase):
    def run_open_pr(self, merge):
        calls = []
        def fake_run(*args, **kwargs):
            calls.append(args)
            return "0" if args[:3] == ("gh", "pr", "list") else ""
        with mock.patch.object(catalog, "run", fake_run), mock.patch.object(bump_pins, "apply_bump"):
            bump_pins.open_pr(BUMP, merge=merge)
        return calls

    def test_merge_merges_the_pr_with_a_merge_commit(self):
        calls = self.run_open_pr(merge=True)
        self.assertIn(("gh", "pr", "merge", "feature/bump-omnilog-1.2.0", "--merge", "--delete-branch"), calls)

    def test_without_merge_the_pr_is_left_open(self):
        calls = self.run_open_pr(merge=False)
        self.assertFalse([call for call in calls if call[:3] == ("gh", "pr", "merge")])
        self.assertTrue([call for call in calls if call[:3] == ("gh", "pr", "create")])


if __name__ == "__main__":
    unittest.main()
