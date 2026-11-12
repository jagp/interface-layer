# Release pipeline for InterfaceLayer plugins

Status: design, 2026-11-12.

## Intent

Releasing a plugin listed in this catalog takes a string of steps that add
nothing: merge the release PR, run finish again, merge the PR back into
`develop`, bump the catalog pin, merge that, publish the catalog. Each one waits
on Jared to click merge, though there is nothing left to decide. This repo takes
those steps over, deterministically, on GitHub, so that after Jared opens a
release the rest happens without him.

Rule of thumb: a human gates **decisions** (what goes into a feature, what
version ships); machines gate **moves** (a branch flowing down a pipeline, a pin
following a tag), using checks that are stricter than a click.

## Where it lives

Entirely in this repo: GitHub workflows and Python scripts beside the existing
`bump-pins` ones. The gitflow plugin (cc-gitflow-regulator) stays generic and
does not know this catalog exists; this repo only *runs* its released scripts.

## Goals

1. **Drop `develop` here.** The catalog is a list of pins, not code. Changes
   land on `main` directly; the `sync-develop` job goes away.
2. **Pin bumps merge themselves.** `bump_pins.py` already refuses anything it
   can't verify (strict `vX.Y.Z` tag, tagged commit on the plugin's `main`,
   matching `plugin.json` version). A bump that passes that is merged with no
   human.
3. **Move-PRs in plugin repos merge themselves.** For every plugin listed in
   the catalog, open PRs that only move work down the pipeline are merged once
   their checks pass:
   - `release/X.Y.Z` or `hotfix/X.Y.Z` into the production branch (`main`)
   - `main` into `develop` or into an open `release/*` (back-merges)
4. **Releases finish themselves.** Once a release or hotfix PR has merged into
   `main` and the version is not tagged yet, the pipeline runs the gitflow
   plugin's own `finish-release.sh` on a runner: tag, merge back, push,
   `publish` (push the tag, create the GitHub Release). Opening the release
   was the go-ahead for the tag push.
5. **Then the pin follows** (goal 2), so a release reaches the catalog in the
   same run or the next one.

## Non-goals

- Feature PRs (`feature/*` into `develop`) stay human: features are where
  content is added, so merging one is a decision.
- Plugins not listed in the catalog, or not hosted on github.com.
- New listings (adding a plugin to the catalog) stay a human PR.
- Anything in the gitflow plugin itself.

## Anti-goals

- Never a squash or rebase merge, never a force push.
- Never merge a PR whose checks are failing, pending, or absent (a plugin repo
  with no CI is reported, not merged into).
- Never act on a repo the catalog doesn't list.

## Constraints

- One fine-grained GitHub token, created by Jared, stored as the secret
  `PIPELINE_TOKEN` in this repo. Repository access: the listed plugin repos
  only. Permissions: Contents read/write, Pull requests read/write, Workflows
  read/write (a release that adds or changes `.github/workflows/*` can't be
  merged or pushed without it), Checks read, Commit statuses read, Metadata
  read (implied). The built-in `GITHUB_TOKEN` covers this repo's own PRs.
- Every listed plugin repo is public (omnilog is made public; Jared's call).
- Each listed plugin repo gets a CI workflow that runs its tests on pushes and
  on PRs into `main` and `develop`, landed through that repo's own normal flow.
  This is part of this build. A plugin repo without CI is skipped with a
  message, never merged into.
- Runs on a schedule (every 15 minutes) and on demand
  (`workflow_dispatch`, optional `plugin` input), one run at a time.
- Public repos, so Actions minutes are free.

## The gitflow scripts on the runner

The pipeline runs cc-gitflow-regulator's scripts at the version **this catalog
pins** (checked out at its pinned `sha`), so the code doing the work is a
released, tested version, and upgrading it is an ordinary pin bump.

For a merged release PR, in a fresh clone of the plugin repo:
1. Create the local `release/X.Y.Z` (or `hotfix/X.Y.Z`) branch from
   `origin/...`, since `finish-release.sh` acts on local branches.
2. Run `finish-release.sh <type> finish` with
   `CC_GITFLOW_REGULATOR_RELEASE_MERGE=pr` (the PR already merged: finish
   fast-forwards `main` and carries on) and
   `CC_GITFLOW_REGULATOR_BACK_MERGE=push` (the token can push `develop`).
3. Run `finish-release.sh <type> publish`.
4. On exit 2/3/4, stop for that plugin and report the script's own output in
   the run log. A human resolves it locally with the same commands.

## Reporting

Every action and every refusal is a line in the workflow run's summary,
naming the plugin, the PR or tag, and why. `--dry-run` prints the same without
acting.

## Decided by Jared

- Automation lives in this repo, deterministic; the gitflow plugin stays generic.
- Releases finish on GitHub (tag, merge back, publish) once the release PR merges.
- One `PIPELINE_TOKEN` secret, created by Jared.
- `develop` is dropped here. omnilog goes public. CI on each listed plugin repo.

## Designer's calls (not ruled on by Jared)

- Once a listed repo's release PR is open, the pipeline finishes it; finishing
  locally as well is documented as unsupported. The pipeline skips a release
  whose `vX.Y.Z` tag is already on origin, so the two can't both tag.
- "Checks pass" means every check run AND every commit status on the PR head
  is successful, with at least one present (the combined-status API alone
  misses GitHub Actions check runs).
- The gitflow plugin's `integration_branch` also honours git-flow's own
  per-repo key `gitflow.branch.develop` (as `production_branch` already honours
  `gitflow.branch.master`). A generic gitflow fix, shipped as a normal plugin
  release; this repo then sets `gitflow.branch.develop=main` so `/start-feature`
  still works with no `develop`.
- The unexplained uncommitted edit on `feature/auto-bump-pins`
  (`bump-pins.yml`) is stashed, not discarded; the build uses the committed
  workflow and folds that branch's `bump` job in (its `sync-develop` job is
  dropped with `develop`).
- Deleting `origin/develop` here is asked for at that step, not assumed.

- Scripts: `.github/scripts/pipeline.py` for goals 3-4; `bump_pins.py` gains a
  "merge my own PR" step for goal 2. Catalog parsing moves into a shared module
  both import.
- After finish, the plugin repo's `release/*` branch on origin is deleted (it
  is fully merged; finish's own `delete-remote` is lease-guarded).
- "Checks pass" means GitHub's combined status for the PR head is success and
  at least one check ran.

## Left to the builder

- Python structure and the exact `gh` calls.
- Test approach (unit tests with a faked `gh`, plus `--dry-run` against the
  live catalog).
