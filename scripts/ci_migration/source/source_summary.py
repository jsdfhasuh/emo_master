"""Run only on authenticated, decrypted manifests in a credential-free context."""
import argparse
from pathlib import Path
import os
import subprocess
import sys


def main():
    p = argparse.ArgumentParser()
    p.add_argument("verified_source", type=Path)
    p.add_argument("runtime_manifest", type=Path)
    p.add_argument("other_manifest", type=Path)
    args = p.parse_args()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("ACTIONS_", "RUNNER_", "GITHUB_"))}
    try:
        cmd = [sys.executable, "-I", str(args.verified_source.resolve(strict=True)
               / "scripts" / "ci_shard_summary.py"),
               str(args.runtime_manifest.resolve(strict=True)),
               str(args.other_manifest.resolve(strict=True))]
        return int(bool(subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode))
    except OSError:
        return 1

if __name__ == "__main__":
    sys.exit(main())
