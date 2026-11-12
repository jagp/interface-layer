"""Move releases of the catalog's plugins down their pipeline, with no human.

For each plugin listed in .claude-plugin/marketplace.json whose repo is on
GitHub (and is the whole repo, not a subfolder):

  1. merge_move_prs: merge open PRs that only MOVE work along gitflow, once
     every check on them has passed:
       release/X.Y.Z or hotfix/X.Y.Z  -> main
       main                           -> develop or release/*   (back-merges)
     Feature PRs are never touched: they add content, so merging one is a
     human decision. Merges are always merge commits.

  2. finish_releases (below): once a release PR has merged into main, run the
     gitflow plugin's own finish-release.sh to tag, merge back and publish.

Every action and every refusal is one catalog.report() line. --dry-run
reports the same lines and changes nothing.
"""
import argparse
import json
import re
import sys

import catalog

VERSION_BRANCH = re.compile(r"^(release|hotfix)/(\d+\.\d+\.\d+)$")
PRODUCTION = "main"
BACK_MERGE_TARGETS = re.compile(r"^(develop|release/\d+\.\d+\.\d+)$")
PASSING_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}


def is_move_pr(pr):
    """Does this PR only move work along the pipeline (see the module header)?

    Same-repo PRs only: a PR from a fork is someone else's content.
    """
    if pr.get("isCrossRepository"):
        return False
    head, base = pr["headRefName"], pr["baseRefName"]
    if VERSION_BRANCH.match(head) and base == PRODUCTION:
        return True                                          # a release/hotfix landing
    return head == PRODUCTION and bool(BACK_MERGE_TARGETS.match(base))   # a back-merge


def checks_pass(rollup):
    """True when every check on the PR head passed, and at least one ran.

    gh's statusCheckRollup mixes two kinds: GitHub Actions check runs
    (status + conclusion) and older commit statuses (state). Both must pass;
    an empty list means no CI at all, which is never a pass.
    """
    if not rollup:
        return False
    for item in rollup:
        if item.get("__typename") == "StatusContext":
            if item.get("state") != "SUCCESS":
                return False
        elif item.get("status") != "COMPLETED" or item.get("conclusion") not in PASSING_CONCLUSIONS:
            return False
    return True


def merge_move_prs(repo, plugin, dry_run):
    """Merge each open move PR in <repo> whose checks all passed.

    1. list the open PRs
    2. keep the move PRs (is_move_pr); features are skipped silently
    3. for each: read its checks; green -> merge (merge commit), else report
       that it waits for the next run
    """
    listed = json.loads(catalog.run("gh", "pr", "list", "--repo", repo, "--state", "open",
                                    "--json", "number,headRefName,baseRefName,isCrossRepository"))
    for pr in filter(is_move_pr, listed):
        number, move = str(pr["number"]), f"{pr['headRefName']} -> {pr['baseRefName']}"
        rollup = json.loads(catalog.run("gh", "pr", "view", number, "--repo", repo,
                                        "--json", "statusCheckRollup"))["statusCheckRollup"]
        if not checks_pass(rollup):
            catalog.report(f"{plugin}: #{number} {move} — waiting: checks missing, pending or failing")
            continue
        if dry_run:
            catalog.report(f"{plugin}: #{number} {move} — would merge (checks passed)")
            continue
        catalog.run("gh", "pr", "merge", number, "--repo", repo, "--merge")
        catalog.report(f"{plugin}: #{number} {move} — merged (checks passed)")
