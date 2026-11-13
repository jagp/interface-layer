"""Find plugins whose own repo has a newer release tag than the catalog pins, and
open one pull request per plugin that moves its pin.

How it works, for each plugin entry in .claude-plugin/marketplace.json:
  1. Work out the plugin's GitHub repo (and subfolder, for a git-subdir source).
     Entries that are not on github.com are skipped.
  2. List the repo's tags with `git ls-remote` (public repos need no token) and keep
     only strict release tags, vX.Y.Z. Messy tags such as `vv.0.0.1` or
     `name--v0.3.0` are ignored.
  3. Take the highest release tag. If it is not newer than the pinned `ref`, stop.
  4. Use the COMMIT the tag points at (annotated tags are peeled with `^{}`), not
     the tag object - the catalog pins a commit.
  5. Refuse the bump if that commit is not on the plugin's `main` branch, or if the
     plugin.json at that commit names a different version than the tag.
  6. Update `version`, `ref` and `sha` in the entry plus the plugin's row in
     README.md, then (with --open-prs) commit that one change on its own branch
     off main and open a PR, unless one is already open for that branch.

Usage:
  python bump_pins.py --dry-run              # report what would change
  python bump_pins.py --open-prs [--plugin X] # what the workflow runs
Exit code 1 means at least one plugin was refused; the others still proceed.
"""
import argparse
import json
import re
import subprocess
import sys

import catalog                  # shared: catalog.run() (faked in tests), github_repo(), MANIFEST
from catalog import MANIFEST, ROOT, github_repo

README = ROOT / "README.md"
BASE_BRANCH = "main"            # bump PRs go straight to main (users install from it)
RELEASE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def release_tags(repo):
    """Map each vX.Y.Z tag to the commit it points at (peeled), via git ls-remote."""
    out = catalog.run("git", "ls-remote", "--tags", f"https://github.com/{repo}.git")
    plain, peeled = {}, {}
    for line in out.splitlines():
        sha, ref = line.split("\t")
        name = ref.removeprefix("refs/tags/")
        if name.endswith("^{}"):
            peeled[name[:-3]] = sha          # annotated tag -> its commit
        else:
            plain[name] = sha                # lightweight tag commit, or a tag object
    tags = {}
    for name, sha in plain.items():
        if RELEASE_TAG.match(name):
            tags[name] = peeled.get(name, sha)
    return tags


def version_key(tag):
    return tuple(int(part) for part in RELEASE_TAG.match(tag).groups())


def gh_api(path, raw=False):
    args = ["gh", "api", path]
    if raw:
        args += ["-H", "Accept: application/vnd.github.raw"]
    return catalog.run(*args)


def on_main(repo, sha):
    """True when the commit is reachable from the repo's main branch."""
    status = json.loads(gh_api(f"repos/{repo}/compare/{BASE_BRANCH}...{sha}"))["status"]
    return status in ("behind", "identical")


def manifest_version(repo, subfolder, sha):
    path = f"{subfolder}/.claude-plugin/plugin.json" if subfolder else ".claude-plugin/plugin.json"
    return json.loads(gh_api(f"repos/{repo}/contents/{path}?ref={sha}", raw=True)).get("version")


def find_bumps(manifest, only=None):
    """Return (bumps, refusals). A bump is (entry_name, repo, new_tag, new_sha, version)."""
    bumps, refusals = [], []
    for entry in manifest["plugins"]:
        name, source = entry["name"], entry.get("source")
        if only and name != only:
            continue
        located = github_repo(source) if isinstance(source, dict) else None
        if not located:
            continue                                         # relative path or non-GitHub host
        repo, subfolder = located
        tags = release_tags(repo)
        if not tags:
            continue
        latest = max(tags, key=version_key)
        pinned = source.get("ref", "")
        if RELEASE_TAG.match(pinned) and version_key(latest) <= version_key(pinned):
            continue                                         # already current (never downgrade)
        sha = tags[latest]
        if not on_main(repo, sha):
            refusals.append(f"{name}: {latest} ({sha[:7]}) is not on {repo}@{BASE_BRANCH}")
            continue
        version = manifest_version(repo, subfolder, sha)
        if version != latest[1:]:
            refusals.append(f"{name}: tag {latest} but plugin.json at {sha[:7]} says {version!r}")
            continue
        bumps.append((name, repo, latest, sha, version))
    return bumps, refusals


def apply_bump(bump):
    """Rewrite marketplace.json and the README row for one plugin, on disk."""
    name, _repo, tag, sha, version = bump
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for entry in manifest["plugins"]:
        if entry["name"] == name:
            if "version" in entry:
                entry["version"] = version
            entry["source"]["ref"] = tag
            entry["source"]["sha"] = sha
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    readme = re.sub(rf"^(\| {re.escape(name)} \| )[^|]+( \|)", rf"\g<1>{version}\g<2>", readme, flags=re.M)
    README.write_text(readme, encoding="utf-8")


def open_pr(bump, merge=False):
    """Commit one bump on its own branch off main and open a PR for it.

    With merge: merge that PR straight away (merge commit). find_bumps already
    refused anything it could not verify (strict vX.Y.Z tag, tagged commit on
    the plugin's main, matching plugin.json version), so a bump that got this
    far needs no human: those checks are stricter than a click.
    """
    name, repo, tag, sha, version = bump
    branch = f"feature/bump-{name}-{version}"
    catalog.run("git", "fetch", "-q", "origin", BASE_BRANCH)
    catalog.run("git", "checkout", "-q", "-B", branch, f"origin/{BASE_BRANCH}")
    apply_bump(bump)
    catalog.run("git", "add", str(MANIFEST), str(README))
    catalog.run("git", "commit", "-q", "-m", f"feat: pin {name} to {version}\n\n{repo}@{tag} ({sha}).")
    catalog.run("git", "push", "-q", "--force-with-lease", "origin", branch)
    already_open = catalog.run("gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number", "--jq", "length").strip()
    if already_open == "0":
        body = (f"Moves `{name}` to `{tag}`.\n\n- version: `{version}`\n- ref: `{tag}`\n- sha: `{sha}`\n\n"
                f"The commit is on `{repo}@{BASE_BRANCH}` and its `plugin.json` says `{version}`.\n\n"
                "Opened by `.github/workflows/pipeline.yml`.")
        catalog.run("gh", "pr", "create", "--base", BASE_BRANCH, "--head", branch,
            "--title", f"feat: pin {name} to {version}", "--body", body)
    if merge:
        catalog.run("gh", "pr", "merge", branch, "--merge", "--delete-branch")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--open-prs", action="store_true")
    parser.add_argument("--merge", action="store_true", help="merge each bump PR once it is open")
    parser.add_argument("--plugin", default="")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    bumps, refusals = find_bumps(manifest, args.plugin or None)
    # catalog.report: each line also lands in the GitHub Actions run summary.
    for name, repo, tag, sha, version in bumps:
        catalog.report(f"bump  {name}: -> {version} ({repo}@{tag}, {sha})")
    for reason in refusals:
        catalog.report(f"REFUSED  {reason}")
    if not bumps and not refusals:
        catalog.report("all pins current")
    failed = False
    if args.open_prs:
        for bump in bumps:
            try:                                   # one stuck bump never blocks the others
                open_pr(bump, merge=args.merge)
            except subprocess.CalledProcessError as error:
                catalog.report(f"{bump[0]}: bump PR failed — {(error.stderr or str(error)).strip()[-300:]}")
                failed = True
    return 1 if refusals or failed else 0


if __name__ == "__main__":
    sys.exit(main())
