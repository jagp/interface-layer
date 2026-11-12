"""What the catalog scripts (bump_pins.py, pipeline.py) share.

  ROOT, MANIFEST      this repo and its marketplace.json
  run(*args)          the ONE way these scripts run a command (git, gh, bash).
                      Tests replace it with a fake, so nothing real happens.
  run_status(*args)   the same, for commands whose exit code is the answer
  load_catalog()      the plugin entries of marketplace.json
  github_repo(source) where a GitHub-hosted plugin lives: (owner/repo, subfolder)
  report(line)        print one result line, and add it to the GitHub Actions
                      run summary when running there
"""
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / ".claude-plugin" / "marketplace.json"


def run(*args, capture=True, check=True, cwd=ROOT, env=None):
    """Run a command in <cwd>; return its stdout as text (empty if not captured)."""
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=capture, check=check, env=env)
    return result.stdout if capture else ""


def run_status(*args, cwd=ROOT, env=None):
    """Run a command that may fail on purpose; return (exit code, stdout+stderr).

    For scripts whose exit code is the answer (finish-release.sh exits 2 on a
    refusal, 3 on a conflict), where run()'s raise-on-failure would hide it.
    """
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, env=env)
    return result.returncode, result.stdout + result.stderr


def load_catalog():
    """The catalog's plugin entries, as listed in marketplace.json."""
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["plugins"]


def github_repo(source):
    """Return (owner/repo, subfolder) for a GitHub-hosted source, else None.

    `github` sources name the repo directly; `url` and `git-subdir` sources
    are recognised by their github.com URL. Anything else (another host, a
    relative path) is not ours to act on.
    """
    kind = source.get("source")
    if kind == "github":
        return source["repo"], ""
    if kind in ("url", "git-subdir"):
        url = source.get("url", "")
        match = re.match(r"^(?:https://github\.com/|git@github\.com:)?([\w.-]+/[\w.-]+?)(?:\.git)?/?$", url)
        if match and (url.startswith(("https://github.com/", "git@github.com:")) or kind == "git-subdir"):
            return match.group(1), source.get("path", "").strip("/")
    return None


def report(line):
    """One line of what happened: to the log, and to the run summary on GitHub."""
    print(line, flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"- {line}\n")
