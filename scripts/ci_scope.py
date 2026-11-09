"""Select full source checks without skipping the workflow's status checks."""
from pathlib import Path, PurePosixPath
import json
import os
import subprocess


ROOT_DOCS = {"AGENTS.md", "README.md", "LICENSE", "LICENSE.md", "LICENSE.txt"}
DOC_SUFFIXES = {".md", ".rst", ".txt", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf"}
# These documents are executable test input or part of a delivered SDK.
SOURCE_DOCS = {"docs/operator-development-guide.md", "docs/development/panel-contract-v1.md"}


def needsFullChecks(paths, webCovered=True):
    for path in paths:
        if path in SOURCE_DOCS:
            return True
        if path in ROOT_DOCS or (webCovered and path.startswith("web_panel/")):
            continue
        if path.startswith("docs/") and PurePosixPath(path).suffix.lower() in DOC_SUFFIXES:
            continue
        # Unknown paths, fixtures, dependencies and CI changes keep full coverage.
        return True
    return False


def changedPaths(base, repo):
    try:
        subprocess.run(["git", "cat-file", "-e", f"{base}^{{commit}}"], cwd=repo,
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError:
        subprocess.run(["git", "fetch", "--no-tags", "--depth=1", "--filter=blob:none", "origin", base],
                       cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=60)
    # No rename detection: both a removed source path and its new name must be classified.
    output = subprocess.check_output(
        ["git", "diff", "--name-only", "--no-renames", "-z", base, "HEAD", "--"], cwd=repo)
    return [os.fsdecode(path) for path in output.split(b"\0") if path]


def selectChecks(eventName, event, repo):
    if eventName == "pull_request":
        # checkout's PR merge commit contains the complete change against this base tip.
        base = event.get("pull_request", {}).get("base", {}).get("sha")
    elif eventName == "push":
        base = event.get("before")
    else:
        return True, "Manual or unsupported event: full source checks.", []
    if not base or set(base) == {"0"}:
        return True, "No previous revision: full source checks.", []
    try:
        paths = changedPaths(base, repo)
    except (OSError, subprocess.SubprocessError):
        return True, "Changed paths unavailable: full source checks.", []
    # The legacy architecture push branch is not a Web CI push trigger.
    webCovered = eventName == "pull_request" or event.get("ref") == "refs/heads/main"
    full = needsFullChecks(paths, webCovered=webCovered)
    reason = ("Source, shared input or CI changes: full source checks." if full else
              "Only passive documentation and/or independent Web files changed; "
              "full source checks are not required. Web files remain covered by Web CI.")
    return full, reason, paths


def main():
    eventPath = os.environ.get("GITHUB_EVENT_PATH")
    event = json.loads(Path(eventPath).read_text(encoding="utf-8")) if eventPath else {}
    full, reason, paths = selectChecks(os.environ.get("GITHUB_EVENT_NAME", ""), event, Path.cwd())
    decision = str(full).lower()
    print(json.dumps({"run_full": full, "reason": reason, "changed_files": len(paths)}, ensure_ascii=False))
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"run_full={decision}\n")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(f"### Source check selection\n\n{reason}\n\n"
                         f"Changed paths: {len(paths)}. Full source checks: `{decision}`.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
