"""Move releases of the catalog's plugins down their pipeline, with no human.

For each plugin listed in .claude-plugin/marketplace.json whose repo is on
GitHub (and is the whole repo, not a subfolder):

  1. merge_move_prs: merge open PRs that only MOVE work along gitflow, once
     every check on them has passed:
       release/X.Y.Z or hotfix/X.Y.Z  -> main   (at most one per run)
       main                           -> develop or release/*   (back-merges)
     Feature PRs are never touched: they add content, so merging one is a
     human decision. Merges are always merge commits, of exactly the commit
     whose checks were read.

  2. releases_to_finish + finish_release: once a release PR has merged into
     main, run the gitflow plugin's own finish-release.sh to tag, merge back
     and publish.

Every action and every refusal is one catalog.report() line. --dry-run
reports the same and changes nothing; for a landed release it rehearses
finish with finish-release.sh's read-only `plan`, in a real clone.

Outcomes and the run's colour: a refusal finish-release.sh reports by name
(exit 2, 3) is expected — a warning, the run stays green. Anything else
unexpected fails the run (exit 1), so it is noticed.
"""
import argparse
import json
import os
import re
import sys
import tempfile

import catalog

VERSION_BRANCH = re.compile(r"^(release|hotfix)/(\d+\.\d+\.\d+)$")
PRODUCTION, INTEGRATION = "main", "develop"
BACK_MERGE_TARGETS = re.compile(r"^(develop|release/\d+\.\d+\.\d+)$")
PASSING_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
NAMED_REFUSALS = {2, 3}           # finish-release.sh: 2 refused, 3 merge conflict


def warn(line):
    """A report line that also shows as a warning on the run (not a failure)."""
    catalog.report(line)
    print(f"::warning::{line}", flush=True)


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
    """True when every check on the PR head passed, and at least one SUCCEEDED.

    gh's statusCheckRollup mixes two kinds: GitHub Actions check runs
    (status + conclusion) and older commit statuses (state). Both must pass.
    An empty list (no CI) or only skipped checks (nothing actually ran) is
    never a pass.
    """
    succeeded = False
    for item in rollup:
        if item.get("__typename") == "StatusContext":
            if item.get("state") != "SUCCESS":
                return False
            succeeded = True
        else:
            if item.get("status") != "COMPLETED" or item.get("conclusion") not in PASSING_CONCLUSIONS:
                return False
            succeeded = succeeded or item.get("conclusion") == "SUCCESS"
    return succeeded


def merge_move_prs(repo, plugin, dry_run):
    """Merge each open move PR in <repo> that is ready. Returns how many it saw.

    For each move PR (features are skipped silently), in order:
      1. read its checks, head commit, mergeability and draft state
      2. a draft, a conflict, or checks not all green -> report why, next PR
      3. a second release/hotfix into main this run -> wait: two landing in
         one run would put both tags on the same commit
      4. merge with a merge commit, pinned to the head commit whose checks
         were read (--match-head-commit): a push in between makes it refuse
         instead of merging unchecked code
      5. a failed merge is reported with gh's own words; the next PR still runs
    """
    listed = json.loads(catalog.run("gh", "pr", "list", "--repo", repo, "--state", "open",
                                    "--json", "number,headRefName,baseRefName,isCrossRepository"))
    moves = [pr for pr in listed if is_move_pr(pr)]
    landed_on_production = False
    for pr in moves:
        number, move = str(pr["number"]), f"{pr['headRefName']} -> {pr['baseRefName']}"
        state = json.loads(catalog.run("gh", "pr", "view", number, "--repo", repo,
                                       "--json", "statusCheckRollup,headRefOid,mergeable,isDraft"))
        into_production = pr["baseRefName"] == PRODUCTION
        if state.get("isDraft"):
            catalog.report(f"{plugin}: #{number} {move} — skipped: it is a draft")
            continue
        if state.get("mergeable") != "MERGEABLE":
            catalog.report(f"{plugin}: #{number} {move} — waiting: not mergeable ({state.get('mergeable')})")
            continue
        if not checks_pass(state.get("statusCheckRollup") or []):
            catalog.report(f"{plugin}: #{number} {move} — waiting: checks missing, pending or failing")
            continue
        if into_production and landed_on_production:
            catalog.report(f"{plugin}: #{number} {move} — waiting: one release into main per run")
            continue
        if dry_run:
            catalog.report(f"{plugin}: #{number} {move} — would merge (checks passed)")
        else:
            code, output = catalog.run_status("gh", "pr", "merge", number, "--repo", repo, "--merge",
                                              "--match-head-commit", state["headRefOid"])
            if code != 0:
                catalog.report(f"{plugin}: #{number} {move} — merge failed: {output.strip()[-300:]}")
                continue
            catalog.report(f"{plugin}: #{number} {move} — merged (checks passed)")
        landed_on_production = landed_on_production or into_production
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
            warn(f"{plugin}: {head} merged but untagged, and origin/{head} is gone — finish it locally")
            continue
        found.append((match.group(1), match.group(2)))
    return found


def prepare_clone(clone, kind, branch):
    """Give a fresh clone the LOCAL branches finish-release.sh looks for.

    A clone has only its default branch locally; the gitflow scripts resolve
    main, develop and open release/* from local refs. So create, from origin:
      - main and develop (whichever is not already local)
      - <branch>, the release/hotfix being finished
      - for a hotfix: every open release/*, so it merges back into the release
        that would otherwise ship without it
    """
    remote = catalog.run("git", "-C", clone, "for-each-ref", "--format=%(refname:short)",
                         "refs/remotes/origin").split()
    local = set(catalog.run("git", "-C", clone, "for-each-ref", "--format=%(refname:short)",
                            "refs/heads").split())
    on_origin = {name.removeprefix("origin/") for name in remote if name != "origin/HEAD"}
    wanted = [PRODUCTION, INTEGRATION, branch]
    if kind == "hotfix":
        wanted += sorted(name for name in on_origin if name.startswith("release/"))
    for name in dict.fromkeys(wanted):
        if name in on_origin and name not in local:
            catalog.run("git", "-C", clone, "branch", name, f"origin/{name}")


def finish_release(repo, plugin, kind, version, gitflow_dir, dry_run):
    """Finish one landed release with the gitflow plugin's own scripts.

    Returns "finished", "refused" (finish-release.sh refused by name: exit 2
    or 3 — a human looks) or "failed" (anything else unexpected).

    In a fresh clone (git authenticates through gh's GH_TOKEN, so it can push):
      1. prepare_clone: the local branches finish works from
      2. gitflow.testCommand=true — CI already passed on this exact commit (the
         merge into main only happened because it did), and it keeps the
         plugin's own code from ever running with the token
      3. finish-release.sh <kind> finish — the merge into main is a merged PR
         (RELEASE_MERGE=pr: it fast-forwards main and carries on); the merge
         back is pushed (BACK_MERGE=push). A dry run runs `plan` instead: the
         same checks, read-only.
      4. publish — push the tag, create the GitHub Release (opening the
         release was the go-ahead for this); its warn: lines are reported
      5. delete the branch on origin when finish offers to (lease-guarded)
    """
    branch = f"{kind}/{version}"
    clone = tempfile.mkdtemp(prefix=f"pipeline-{plugin}-")
    # gh lends git its GH_TOKEN, so plain URLs can push and the token never
    # appears on a command line (or in an error message that quotes one).
    catalog.run("gh", "auth", "setup-git")
    catalog.run("git", "clone", "-q", f"https://github.com/{repo}.git", clone)
    for key, value in (("user.name", "github-actions[bot]"),
                       ("user.email", "41898282+github-actions[bot]@users.noreply.github.com"),
                       ("gitflow.testCommand", "true")):
        catalog.run("git", "-C", clone, "config", key, value)
    prepare_clone(clone, kind, branch)
    env = dict(os.environ, CC_GITFLOW_REGULATOR_RELEASE_MERGE="pr", CC_GITFLOW_REGULATOR_BACK_MERGE="push")
    finish_script = f"{gitflow_dir}/scripts/finish-release.sh"

    def step(*args):
        """Run one gitflow step in the clone; report a non-zero exit with its last lines."""
        code, output = catalog.run_status("bash", *args, cwd=clone, env=env)
        if code != 0:
            tail = " | ".join(output.strip().splitlines()[-5:])
            line = f"{plugin}: {branch} — {args[1]} {args[2]} stopped (exit {code}): {tail}"
            warn(line) if code in NAMED_REFUSALS else catalog.report(line)
        return code, output

    def outcome_of(code):
        return "refused" if code in NAMED_REFUSALS else "failed"

    if dry_run:
        code, _ = step(finish_script, kind, "plan")
        if code != 0:
            return outcome_of(code)
        catalog.report(f"{plugin}: {branch} — would finish (plan passed: tag v{version}, merge back, publish)")
        return "finished"
    code, output = step(finish_script, kind, "finish")
    if code != 0:
        return outcome_of(code)
    if "ask: push-tag" not in output:
        warn(f"{plugin}: {branch} — finish ended without offering the tag push; finish it locally")
        return "refused"
    code, published = step(finish_script, kind, "publish")
    if code != 0:
        return outcome_of(code)
    for line in published.splitlines():
        if line.startswith("warn:"):                  # e.g. the GitHub Release page wasn't created
            warn(f"{plugin}: {branch} — {line}")
    remote = re.search(r"^ask: delete-remote (\S+) (\S+)", output, re.M)
    if remote:
        code, _ = step(f"{gitflow_dir}/scripts/finish-feature.sh", "delete-remote", remote.group(1), remote.group(2))
        if code != 0:
            catalog.report(f"{plugin}: {branch} — origin/{branch} was left in place")
    catalog.report(f"{plugin}: {branch} — finished: v{version} tagged, merged back and published")
    return "finished"


def main(argv=None):
    """Run the pipeline over the catalog (or one --plugin).

    Exits 1 only for something unexpected (a "failed" finish, an error);
    waiting PRs, skipped entries and named refusals keep the run green.
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
        source = entry.get("source")
        located = catalog.github_repo(source) if isinstance(source, dict) else None   # a path is a str
        if not located or located[1]:
            catalog.report(f"{plugin}: skipped — not a whole GitHub repo")
            continue
        repo = located[0]
        try:
            moved = merge_move_prs(repo, plugin, args.dry_run)
            landed = releases_to_finish(repo, plugin)
            for kind, version in landed:
                failed |= finish_release(repo, plugin, kind, version, args.gitflow_dir, args.dry_run) == "failed"
            if not moved and not landed:
                catalog.report(f"{plugin}: nothing to move")      # a quiet run still says it ran
        except Exception as error:                     # one repo's trouble never stops the others
            catalog.report(f"{plugin}: error — {error}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
