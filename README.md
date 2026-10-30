# InterfaceLayer

The marketplace for my Claude Code plugins.

```text
/plugin marketplace add jagp/interface-layer
/plugin install <plugin>@interface-layer
```

## Plugins

| Plugin | Version | Repo |
|---|---|---|
| omnilog | 1.0.1 | [jagp/omnilog](https://github.com/jagp/omnilog) |
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

Plugins whose own repo has no release yet aren't listed.
