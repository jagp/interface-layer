# InterfaceLayer

The marketplace for my Claude Code plugins.

```text
/plugin marketplace add jagp/interface-layer
/plugin install <plugin>@interface-layer
```

## Plugins

| Plugin | Version | Repo |
|---|---|---|
| cc-gitflow-regulator | 1.0.0 | [jagp/cc-gitflow-regulator](https://github.com/jagp/cc-gitflow-regulator) |
| omnilog | 1.1.0 | [jagp/omnilog](https://github.com/jagp/omnilog) |
| truename | 0.0.2 | [jagp/truename](https://github.com/jagp/truename) |

## How the catalog works

This repo holds no plugin code. `.claude-plugin/marketplace.json` lists each plugin
and where to fetch it. Each plugin is developed and released in its own repo.

Every entry is pinned to a **release commit**: `ref` names the release tag, and `sha`
is the commit that tag pointed to when the entry was written. Claude Code installs
that exact commit, so later work on a plugin's `main` doesn't reach anyone until
the entry here is updated. Publishing a release takes two steps:

1. Release the plugin in its own repo (version bumped in `plugin.json`, merged to
   `main`, tagged `vX.Y.Z`).
2. In this repo, update that plugin's `version`, `ref` and `sha`, then merge to `main`.

Step 2 is automated. `.github/workflows/bump-pins.yml` runs daily, and on demand from
the Actions tab. It opens one PR against `main` per plugin with a newer release, and
you review and merge it. It only proposes a bump when the tag is a strict `vX.Y.Z`, the
tagged commit is on the plugin's `main`, and that commit's `plugin.json` has the same
version. Anything else is reported in the run log and left alone. To check by hand:

```text
python .github/scripts/bump_pins.py --dry-run
```

Plugins whose own repo has no release yet aren't listed.
