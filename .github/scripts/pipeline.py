"""Move releases of the catalog's plugins down their pipeline, with no human.

For each plugin listed in .claude-plugin/marketplace.json whose repo is on
GitHub (and is the whole repo, not a subfolder):

  1. merge_move_prs: merge open PRs that only MOVE work along gitflow, once
     every check on them has passed:
       release/X.Y.Z or hotfix/X.Y.Z  -> main
       main                           -> develop or release/*   (back-merges)
     Feature PRs are never touched: they add content, so merging one is a
     human decision. Merges are always merge commits.

  2. releases_to_finish + finish_release (below): once a release PR has merged into main, run the
     gitflow plugin's own finish-release.sh to tag, merge back and publish.

Every action and every refusal is one catalog.report() line. --dry-run
reports the same lines and changes nothing.
"""
import argparse
import json
import os
import re
import sys
import tempfile

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
    Returns how many move PRs it saw (merged or waiting).
    """
    listed = json.loads(catalog.run("gh", "pr", "list", "--repo", repo, "--state", "open",
                                    "--json", "number,headRefName,baseRefName,isCrossRepository"))
    moves = [pr for pr in listed if is_move_pr(pr)]
    for pr in moves:
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
    return len(moves)


def remote_refs(repo, kind):
    """Names of <repo>'s "tags" or "heads" on GitHub, as a set (v1.0.0, main, ...).
    An annotated tag is listed twice (the tag, and its ^{} commit); both give one name.
    """
    listing = catalog.run("git", "ls-remote", f"--{kind}", f"https://github.com/{repo}.git")
    return {line.split("\t")[1].removeprefix(f"refs/{kind}/").removesuffix("^{}")
            for line in listing.splitlines() if "\t" in line}


def releases_to_finish(repo, plugin):
    """(kind, version) for each release/hotfix PR merged into main whose tag is
    not on origin yet — landed, but not finished.

    1. merged PRs into main whose head is release/X.Y.Z or hotfix/X.Y.Z
    2. drop versions already tagged on origin (finished: nothing to do)
    3. the branch must still be on origin (finish works from it); if it is
       gone, say so — a human finishes that one locally
    """
    merged = json.loads(catalog.run("gh", "pr", "list", "--repo", repo, "--state", "merged",
                                    "--base", PRODUCTION, "--json", "headRefName", "--limit", "30"))
    tags, heads = remote_refs(repo, "tags"), remote_refs(repo, "heads")
    found = []
    for head in dict.fromkeys(pr["headRefName"] for pr in merged):   # unique, in order
        match = VERSION_BRANCH.match(head)
        if not match or f"v{match.group(2)}" in tags:
            continue                                  # not a release, or already finished
        if head not in heads:
            catalog.report(f"{plugin}: {head} merged but untagged, and origin/{head} is gone — finish it locally")
            continue
        found.append((match.group(1), match.group(2)))
    return found


def finish_release(repo, plugin, kind, version, gitflow_dir, dry_run):
    """Finish one landed release with the gitflow plugin's own scripts.

    In a fresh clone (git authenticates through gh's GH_TOKEN, so it can push):
      1. create the local <kind>/<version> branch (finish works on local ones)
      2. gitflow.testCommand=true — CI already passed on this exact commit;
         the merge into main only happened because it did
      3. finish-release.sh <kind> finish — the merge into main is a merged PR
         (RELEASE_MERGE=pr: it fast-forwards main and carries on); the merge
         back is pushed (BACK_MERGE=push)
      4. publish — push the tag, create the GitHub Release (opening the
         release was the go-ahead for this)
      5. delete the branch on origin when finish offers to (lease-guarded)
    Any non-zero exit stops this release and reports the script's last lines.
    Returns True when the release is finished.
    """
    branch = f"{kind}/{version}"
    if dry_run:
        catalog.report(f"{plugin}: {branch} — would finish (tag v{version}, merge back, publish)")
        return True
    clone = tempfile.mkdtemp(prefix=f"pipeline-{plugin}-")
    # gh lends git its GH_TOKEN, so plain URLs can push and the token never
    # appears on a command line (or in an error message that quotes one).
    catalog.run("gh", "auth", "setup-git")
    catalog.run("git", "clone", "-q", f"https://github.com/{repo}.git", clone)
    for key, value in (("user.name", "github-actions[bot]"),
                       ("user.email", "41898282+github-actions[bot]@users.noreply.github.com"),
                       ("gitflow.testCommand", "true")):
        catalog.run("git", "-C", clone, "config", key, value)
    catalog.run("git", "-C", clone, "branch", branch, f"origin/{branch}")
    env = dict(os.environ, CC_GITFLOW_REGULATOR_RELEASE_MERGE="pr", CC_GITFLOW_REGULATOR_BACK_MERGE="push")
    finish_script = f"{gitflow_dir}/scripts/finish-release.sh"

    def step(*args):
        """Run one gitflow step in the clone; on failure report why and stop."""
        code, output = catalog.run_status("bash", *args, cwd=clone, env=env)
        if code != 0:
            tail = " | ".join(output.strip().splitlines()[-5:])
            catalog.report(f"{plugin}: {branch} — {args[1]} {args[2]} stopped (exit {code}): {tail}")
        return code == 0, output

    finished, output = step(finish_script, kind, "finish")
    if not finished:
        return False
    if "ask: push-tag" not in output:
        catalog.report(f"{plugin}: {branch} — finish ended without offering the tag push; finish it locally")
        return False
    published, _ = step(finish_script, kind, "publish")
    if not published:
        return False
    remote = re.search(r"^ask: delete-remote (\S+) (\S+)", output, re.M)
    if remote:
        step(f"{gitflow_dir}/scripts/finish-feature.sh", "delete-remote", remote.group(1), remote.group(2))
    catalog.report(f"{plugin}: {branch} — finished: v{version} tagged, merged back and published")
    return True


def main(argv=None):
    """Run the pipeline over the catalog (or one --plugin).

    Exits 1 when any release could not be finished, so the run shows red;
    everything else (waiting PRs, skipped entries) is normal and exits 0.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--plugin", default="")
    parser.add_argument("--gitflow-dir", default="gitflow", help="cc-gitflow-regulator checkout")
    args = parser.parse_args(argv)
    if not os.environ.get("GH_TOKEN"):
        catalog.report("PIPELINE_TOKEN not set — skipping plugin repos")
        return 0
    failed = False
    for entry in catalog.load_catalog():
        plugin = entry["name"]
        if args.plugin and plugin != args.plugin:
            continue
        located = catalog.github_repo(entry.get("source") or {})
        if not located or located[1]:
            catalog.report(f"{plugin}: skipped — not a whole GitHub repo")
            continue
        repo = located[0]
        try:
            moved = merge_move_prs(repo, plugin, args.dry_run)
            landed = releases_to_finish(repo, plugin)
            for kind, version in landed:
                failed |= not finish_release(repo, plugin, kind, version, args.gitflow_dir, args.dry_run)
            if not moved and not landed:
                catalog.report(f"{plugin}: nothing to move")      # a quiet run still says it ran
        except Exception as error:                     # one repo's trouble never stops the others
            catalog.report(f"{plugin}: error — {error}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
