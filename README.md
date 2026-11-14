# InterfaceLayer

The marketplace for my Claude Code plugins.

```text
/plugin marketplace add jagp/interface-layer
/plugin install <plugin>@interface-layer
```

## Plugins

| Plugin | Version | Repo |
|---|---|---|
| cc-gitflow-regulator | 1.0.1 | [jagp/cc-gitflow-regulator](https://github.com/jagp/cc-gitflow-regulator) |
| omnilog | 1.1.0 | [jagp/omnilog](https://github.com/jagp/omnilog) |
| truename | 0.0.2 | [jagp/truename](https://github.com/jagp/truename) |

## How the catalog works

This repo holds no plugin code. `.claude-plugin/marketplace.json` lists each plugin
and where to fetch it. Each plugin is developed and released in its own repo.

Every entry is pinned to a **release commit**: `ref` names the release tag, and `sha`
is the commit that tag pointed to when the entry was written. Claude Code installs
that exact commit, so later work on a plugin's `main` doesn't reach anyone until
the entry here is updated. This repo has only `main`: it is a catalog, not code.

Plugins whose own repo has no release yet aren't listed.

## The release pipeline

Once a release is decided, nothing after it needs a human.
`.github/workflows/pipeline.yml` runs every 15 minutes, and on demand from the
Actions tab (optionally for one plugin, or as a dry run). For each plugin listed
here, it does four things:

1. **Merges "move" PRs once their checks pass.** These are `release/X.Y.Z` or
   `hotfix/X.Y.Z` into `main`, and `main` back into `develop` or an open
   `release/*`. Every check on the PR must have passed, and at least one must
   exist. The merge is always a merge commit.
2. **Finishes a landed release.** After a release PR merges into `main`, it runs
   the gitflow plugin's own `finish-release.sh`: tag `vX.Y.Z`, merge back, push,
   and publish the GitHub Release. The gitflow plugin used is the version this
   catalog pins. Tests aren't run again, because CI already passed on that
   exact commit.
3. **Bumps the pin.** It moves the entry to the newest release, but only when
   the tag is a strict `vX.Y.Z`, the tagged commit is on the plugin's `main`,
   and that commit's `plugin.json` says the same version. Anything else is
   reported and left alone.
4. **Merges its own bump PR.** Those checks are stricter than a click.

What stays human: feature PRs (they add content), starting a release
(`/start-release`, `/finish-release` in the plugin's repo), new listings here,
and anything the pipeline reports it couldn't do. Each run's summary lists
every action and refusal. **For a listed plugin, don't run `/finish-release` again once its release PR is
open.** The PR's own description says to, but that text comes from the gitflow
plugin and predates this pipeline. Finishing by hand as well would build a
second merge back on a stale `develop`. To see what it would do now:

```text
GH_TOKEN=... python .github/scripts/pipeline.py --dry-run
python .github/scripts/bump_pins.py --dry-run
```

### Setup

- **Plugin CI.** Each listed plugin repo needs a CI workflow that runs its tests
  on pull requests. Without one, the pipeline never merges into that repo.
- **Token.** Create a **fine-grained token** (GitHub → Settings → Developer
  settings → Fine-grained tokens) with:
  - repository access: only the listed plugin repos
  - permissions: Contents read/write, Pull requests read/write, Workflows
    read/write, Checks read, Commit statuses read

  Store it in this repo as the Actions secret **`PIPELINE_TOKEN`**. Without it,
  steps 1-2 are skipped and pin bumps still run.
- **Actions settings.** Settings → Actions → General → "Allow GitHub Actions to
  create and approve pull requests".
