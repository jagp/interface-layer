# Release Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** interface-layer merges "move" PRs in its listed plugin repos once their checks pass, finishes merged releases with the gitflow plugin's own scripts, and merges its own validated pin bumps, with `develop` dropped from interface-layer.

**Architecture:** One workflow (`.github/workflows/pipeline.yml`) runs on a 15-minute schedule and on demand. It runs `pipeline.py` (merge move-PRs, finish merged releases; uses `PIPELINE_TOKEN`), then `bump_pins.py --open-prs --merge` (uses `GITHUB_TOKEN`). Both import a shared `catalog.py`. External effects go through one injectable `run()` so unit tests use a fake. The gitflow scripts run from cc-gitflow-regulator checked out at the sha the catalog pins.

**Tech Stack:** Python 3 standard library (`unittest`), GitHub Actions, `gh`, git, bash (gitflow scripts), Windows PowerShell 5.1 (omnilog CI).

**Spec:** `docs/superpowers/specs/2026-11-12-release-pipeline-design.md`

## Global Constraints

- Merges are always `gh pr merge --merge`. Never squash, rebase, or force push.
- A PR merges only if every check run AND every commit status on its head is successful, with at least one present.
- Only repos listed in `.claude-plugin/marketplace.json` with a `github.com` source are touched.
- Move PRs: head `release/X.Y.Z` or `hotfix/X.Y.Z` into `main`; head `main` into `develop` or into a `release/*` branch. Same-repo PRs only, never forks. Feature PRs are never merged.
- Finishing: `CC_GITFLOW_REGULATOR_RELEASE_MERGE=pr`, `CC_GITFLOW_REGULATOR_BACK_MERGE=push`. Skip any version whose `vX.Y.Z` tag is already on origin.
- Secret name `PIPELINE_TOKEN`. When it's missing, the plugin-repo steps are skipped with a log line, and pin bumps still run.
- Every action and refusal is printed as one line, `<plugin>: <what> — <why>`, and appended to `$GITHUB_STEP_SUMMARY` when that is set. `--dry-run` prints the same lines and changes nothing.
- Work happens on branches of each repo's own flow, committed in small Conventional Commits with the session attribution lines.

## Review Focus

1. **A release PR merged by squash** — finish refuses (`refuse_squashed_pr`). The pipeline must report it and move on, without retrying every 15 minutes in a loop that spams the log: one line per run is fine. Test in Task 3.
2. **The release branch was already deleted on origin** before the pipeline got to it — report "can't finish: origin/<branch> is gone, finish locally", never crash. Test in Task 3.
3. **Two move PRs in one repo in one run** (a hotfix into main and its back-merge) — each judged on its own checks; merging one never merges the other early. Test in Task 2.
4. **A check still pending** — not merged this run, picked up next run. Test in Task 2.
5. **A catalog entry that isn't on github.com** (or a subdir source) — skipped with a line, the others proceed. Test in Task 1.

---

### Task 1: interface-layer — drop develop, shared catalog module, self-merging pin bumps

Work on `feature/release-pipeline` from `origin/main` in a worktree at `interface-layer/.claude/worktrees/release-pipeline`. First stash the main checkout's uncommitted `bump-pins.yml` edit: `git stash push -m "unexplained bump-pins.yml edit (2026-11-12)" -- .github/workflows/bump-pins.yml`. Then bring over `feature/auto-bump-pins`'s `2a7a971` (the `bump` job and `bump_pins.py`), not `9240e69` (`sync-develop`).

**Files:**
- Create: `.github/scripts/catalog.py`, `.github/scripts/test_catalog.py`, `.github/scripts/test_bump_pins.py`
- Modify: `.github/scripts/bump_pins.py` (import from `catalog`; add `--merge`)
- Delete: `.github/workflows/bump-pins.yml` (replaced in Task 3)

**Interfaces:**
- Produces, in `catalog.py`:
  - `ROOT: Path`, `MANIFEST: Path`
  - `run(*args, capture=True, check=True, cwd=ROOT) -> str`: the single subprocess entry point. Tests replace it.
  - `load_catalog() -> list[dict]`: the `plugins` list.
  - `github_repo(entry: dict) -> str | None`: `"owner/name"` for a `url` source on github.com, else `None`.
  - `report(line: str) -> None`: print, and append to `$GITHUB_STEP_SUMMARY` if set.
- `bump_pins.py`: `open_pr(bump, merge: bool)`. With `merge`, after the PR exists it runs `gh pr merge <branch> --merge --delete-branch`. Validation already happened in `find_bumps`.

- [ ] **Step 1: Write the failing tests.** `test_catalog.py`:
  - `github_repo` returns `"jagp/omnilog"` for `https://github.com/jagp/omnilog.git`
  - it returns `None` for `https://gitlab.com/x/y.git` and for a `git-subdir` source (Review Focus 5)
  - `load_catalog()` names include `cc-gitflow-regulator`

  `test_bump_pins.py`, with `catalog.run` replaced by a recorder: `open_pr(bump, merge=True)` issues `gh pr create ... --base main`, then `gh pr merge bump/<plugin>-<version> --merge --delete-branch`; `merge=False` issues no merge.
- [ ] **Step 2: Run** `python -m unittest discover -s .github/scripts -p "test_*.py"`. Expected: FAIL (no `catalog` module).
- [ ] **Step 3: Implement** `catalog.py` (move `run` / `github_repo` out of `bump_pins.py`), and `--merge` in `bump_pins.py` (`main()` passes `args.merge`).
- [ ] **Step 4: Run** the same command. Expected: OK. Then `python .github/scripts/bump_pins.py --dry-run` prints `all pins current`.
- [ ] **Step 5: Commit:** `refactor: share catalog parsing between the catalog scripts`, then `feat: merge validated pin bumps automatically`, and `chore: drop the develop sync job` (the `bump-pins.yml` deletion).

### Task 2: interface-layer — merge move-PRs whose checks pass

**Files:**
- Create: `.github/scripts/pipeline.py`, `.github/scripts/test_pipeline.py`

**Interfaces:**
- Consumes: `catalog.run`, `catalog.load_catalog`, `catalog.github_repo`, `catalog.report`.
- Produces, in `pipeline.py`:
  - `VERSION_BRANCH = re.compile(r"^(release|hotfix)/\d+\.\d+\.\d+$")`
  - `is_move_pr(pr: dict) -> bool`. `pr` has `headRefName`, `baseRefName`, `isCrossRepository`. True only for the Global Constraints move shapes.
  - `checks_pass(rollup: list[dict]) -> bool`, over `gh pr view --json statusCheckRollup`. A CheckRun passes when `status == "COMPLETED"` and its `conclusion` is `SUCCESS`, `NEUTRAL` or `SKIPPED`. A StatusContext passes when `state == "SUCCESS"`. Empty means False.
  - `merge_move_prs(repo: str, plugin: str, dry_run: bool) -> None`
- [ ] **Step 1: Write the failing tests** (fake `run` returning canned JSON):
  - `is_move_pr`: `release/1.1.0` → `main` is True; `hotfix/1.0.1` → `main` True; `main` → `develop` True; `main` → `release/1.1.0` True; `feature/x` → `develop` False; `release/1.1` → `main` False; a cross-repo `release/1.1.0` → `main` False.
  - `checks_pass`: `[]` False; one SUCCESS CheckRun True; one IN_PROGRESS False (Review Focus 4); SUCCESS run plus FAILURE status False; one SKIPPED plus one SUCCESS True.
  - `merge_move_prs`, two open move PRs, one passing and one pending: exactly one `gh pr merge <n> --repo <r> --merge`, plus a report line for the pending one (Review Focus 3). With `dry_run=True`, no merge call.
- [ ] **Step 2: Run** the unittest command. Expected: FAIL (no `pipeline`).
- [ ] **Step 3: Implement.** List PRs with `gh pr list --repo R --state open --json number,headRefName,baseRefName,isCrossRepository`, then `gh pr view N --repo R --json statusCheckRollup` per move PR.
- [ ] **Step 4: Run.** Expected: OK.
- [ ] **Step 5: Commit** `feat(pipeline): merge plugin move-PRs once their checks pass`.

### Task 3: interface-layer — finish merged releases; the workflow; docs

**Files:**
- Modify: `.github/scripts/pipeline.py`, `.github/scripts/test_pipeline.py`, `README.md`
- Create: `.github/workflows/pipeline.yml`, `.github/workflows/ci.yml`

**Interfaces:**
- Produces, in `pipeline.py`:
  - `releases_to_finish(repo: str) -> list[tuple[str, str]]`: `(type, version)` pairs for merged PRs (`gh pr list --repo R --state merged --base main --json headRefName --limit 30`) whose head matches `VERSION_BRANCH`, whose `v<version>` is NOT in `git ls-remote --tags https://github.com/R.git`, and whose `origin/<type>/<version>` still exists (`git ls-remote --heads`). A missing branch gets a report line instead (Review Focus 2).
  - `finish_release(repo: str, kind: str, version: str, gitflow_dir: Path, dry_run: bool) -> bool`. In a temp dir:
    - clone `https://x-access-token:$GH_TOKEN@github.com/R.git`
    - set git identity `github-actions[bot]`
    - `git branch <kind>/<version> origin/<kind>/<version>`
    - `git config gitflow.testCommand true` (CI already gated this exact commit; the comment says so)
    - run `bash <gitflow_dir>/scripts/finish-release.sh <kind> finish` with the Global Constraints env
    - on exit 0 and an `ask: push-tag` line: run `... publish`
    - if the output has `ask: delete-remote <b> <sha>`: run `bash <gitflow_dir>/scripts/finish-feature.sh delete-remote <b> <sha>`

    Any non-zero exit reports the script's last 5 lines and returns False (Review Focus 1). One failing repo never stops the others.
  - `main()`: args `--dry-run`, `--plugin`, `--gitflow-dir` (default `gitflow`). With no `GH_TOKEN`, it reports `PIPELINE_TOKEN not set — skipping plugin repos` and exits 0.
- `pipeline.yml`:
  - `on`: `schedule` `*/15 * * * *`; `workflow_dispatch` with inputs `plugin` and `dry_run` (boolean)
  - `permissions: contents: write, pull-requests: write`; `concurrency: pipeline`
  - steps:
    1. checkout
    2. git identity
    3. read cc-gitflow-regulator's pinned `sha` from the catalog into an output
    4. `actions/checkout` of `jagp/cc-gitflow-regulator` at that sha into `gitflow/`
    5. `python3 .github/scripts/pipeline.py` with `GH_TOKEN: ${{ secrets.PIPELINE_TOKEN }}`
    6. `python3 .github/scripts/bump_pins.py --open-prs --merge` with `GH_TOKEN: ${{ github.token }}`

    `--dry-run` is passed to both when `dry_run`.
- `ci.yml`: on `pull_request` and `push`, runs the unittest command.
- [ ] **Step 1: Write the failing tests:**
  - `releases_to_finish` returns `[("release","1.1.0")]` when the tag is absent and the branch present
  - it returns `[]` when `v1.1.0` is on origin
  - it reports and returns `[]` when the branch is gone
  - `finish_release` with a fake `run`: finish exit 0 with `ask: push-tag` then calls publish; finish exit 2 reports and returns False without calling publish
  - `main()` with no `GH_TOKEN` exits 0 and reports the skip
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement**, plus both workflows.
- [ ] **Step 4: Run** the unittest command. Expected: OK. Then `python .github/scripts/pipeline.py --dry-run` with no token prints the skip line.
- [ ] **Step 5: README.** Rewrite "How the catalog works":
  - no `develop`
  - the pipeline's four jobs, as the spec's Goals
  - what stays human (feature PRs, new listings)
  - the `PIPELINE_TOKEN` setup: fine-grained token; repository access = the listed plugin repos; permissions Contents RW, Pull requests RW, Workflows RW, Checks R, Commit statuses R; stored as an Actions secret named `PIPELINE_TOKEN`
- [ ] **Step 6: Commit** `feat(pipeline): finish merged plugin releases`, `ci: run the pipeline every 15 minutes`, `ci: test the catalog scripts`, and `docs: describe the release pipeline`. Include the spec and this plan: `docs: add the release pipeline design and plan`.
- [ ] **Step 7: Land.** Push the branch and open a PR into `main` for Jared to merge: this one adds content, so it is a human merge. After it merges:
  - set `gitflow.branch.develop main` in this clone
  - delete the local `develop` and the stale `feature/add-*` / `feature/omnilog-*` locals
  - ask before deleting `origin/develop`

### Task 4: cc-gitflow-regulator — honour `gitflow.branch.develop`; CI

On a `feature/develop-branch-config` from `develop`.

**Files:**
- Modify:
  - `scripts/common.sh`: `integration_branch` checks `gitflow.branch.develop` before `develop`/`dev`
  - `scripts/start-feature.sh`: the base loop, same order
  - `scripts/finish-feature.sh`: the base fallback, same order
  - `scripts/cc-gitflow-regulator.sh`: the base auto-detect, same order
  - `README.md`: Configuration
  - `CHANGELOG.md`: a new `## Unreleased` entry
- Create: `.github/workflows/ci.yml` (`ubuntu-latest`, `bash scripts/run-tests.sh`, on `push` and `pull_request`)
- Test: `tests/common.sh`, `tests/start-feature.sh`, `tests/hooks.sh`

**Interfaces:**
- Base order everywhere: `CC_GITFLOW_REGULATOR_BASE`, then `git config gitflow.branch.develop` (if that branch exists), then `develop`, then `dev`.
- [ ] **Step 1: Failing tests:**
  - `tests/common.sh` "gitflow.branch.develop names the integration branch": set to `trunk` with `trunk` existing → `integration_branch` = `trunk`
  - `tests/start-feature.sh` "a repo with only main and gitflow.branch.develop=main branches off main": `start start x` exits 0, and `merge-base HEAD main` = `main`
  - `tests/hooks.sh` "rename forks a fresh worktree from gitflow.branch.develop"
- [ ] **Step 2: Run** each file. Expected: the new cases FAIL.
- [ ] **Step 3: Implement.** One shared lookup if the scripts can source it; else the same three-line loop, commented as matching `common.sh` `integration_branch`.
- [ ] **Step 4: Run** `bash scripts/run-tests.sh`. Expected: `tests: passed`.
- [ ] **Step 5: Commit**, one per concern: `feat(common): honour gitflow.branch.develop`, `ci: run the test suites on GitHub`, `docs: ...`. Land by `/finish-feature` as a PR into `develop` (a feature: human merge).

### Task 5: omnilog — public, with CI

- [ ] **Step 1:** `gh repo edit jagp/omnilog --visibility public --accept-visibility-change-consequences` (Jared approved). Verify with `gh repo view jagp/omnilog --json visibility` → `PUBLIC`.
- [ ] **Step 2:** In a worktree on `feature/ci` from `origin/develop`, create `.github/workflows/ci.yml`: `windows-latest`, `powershell -NoProfile -ExecutionPolicy Bypass -File tests/run-all.ps1`, on `push` and `pull_request`. Commit `ci: run the test suite on GitHub`. Push, and open a PR into `develop` (human merge). Verify its CI run passes: `gh pr checks <n> --repo jagp/omnilog --watch` exits 0.

### Task 6: end to end

- [ ] **Step 1:** Jared creates `PIPELINE_TOKEN` (README steps).
- [ ] **Step 2:** Run the pipeline by hand: `gh workflow run pipeline.yml --repo jagp/interface-layer -f dry_run=true`. Expected: every listed plugin reported, nothing changed.
- [ ] **Step 3:** In cc-gitflow-regulator, `/start-release` (the suggestion should be 1.1.0) and `/finish-release` (opens the PR into main). Then wait. Expected, with no further action: the PR merges once CI passes, `v1.1.0` is tagged and published, `develop` gets the merge back, `release/1.1.0` is deleted on origin, and the catalog pin moves to 1.1.0.

## Rulings in this plan

- truename doesn't use gitflow (no `develop`, no release branches, no tests). Goals 3-4 and CI don't apply to it; only pin bumps do.
- omnilog's tests need Windows PowerShell 5.1, so its CI runs on `windows-latest`. On the pipeline runner, finish sets `gitflow.testCommand true`: the merged commit already passed CI.
- One workflow (`pipeline.yml`) replaces `bump-pins.yml`, so a release reaches the catalog in the same run that finishes it.
- Branch protection isn't required: the pipeline enforces "checks pass" itself.
