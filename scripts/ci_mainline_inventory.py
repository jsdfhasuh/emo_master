"""Check a product commit against the reviewed original CI files."""
import argparse
import json
from pathlib import Path
import re
import subprocess


def verify(repo, sha, expected):
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("expected full product SHA")
    out = subprocess.check_output(
        ["git", "-C", str(repo), "ls-tree", "-z", sha, "--", ".github/workflows/"],
    )
    found = {}
    for row in out.decode("utf-8").split("\0"):
        if row:
            meta, path = row.split("\t", 1)
            _, kind, blob = meta.split()
            if kind == "blob" and path.endswith((".yml", ".yaml")):
                found[path] = blob
    if found != expected:
        drift = sorted(p for p in found.keys() | expected.keys() if found.get(p) != expected.get(p))
        raise ValueError("review or re-pin changed workflow contracts: " + ", ".join(drift))
    return len(found)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sha", required=True)
    parser.add_argument("--repo", default=".", type=Path)
    args = parser.parse_args()
    manifest = Path(__file__).with_name("ci_migration") / "mainline_workflows.json"
    expected = json.loads(manifest.read_text(encoding="utf-8"))["active_workflows"]
    try:
        print(f"Verified original workflow contracts: {verify(args.repo, args.sha, expected)}")
    except (ValueError, subprocess.CalledProcessError) as ex:
        parser.exit(1, f"{ex}\n")


if __name__ == "__main__":
    main()
