"""Run inside the isolated, credential-free source execution context only."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("platform", choices=["linux","windows"])
    parser.add_argument("shard", choices=["runtime","other"])
    args = parser.parse_args()
    src = args.source.resolve(strict=True)
    windows = args.platform == "windows"
    if windows != (sys.platform == "win32") or (not windows and sys.platform != "linux"):
        return 2
    if sys.version_info[:2] != (3,10):
        return 2
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("GITHUB_", "ACTIONS_", "RUNNER_")):
            del env[key]
    env["QT_QPA_PLATFORM"] = "windows" if windows else "offscreen"
    def run(command, extra=None):
        subprocess.run(command, cwd=src, env={**env, **(extra or {})},
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True)
    try:
        run([sys.executable, "-m", "pip", "install", "-r", "requirements-dev.txt"])
        if windows:
            run(["pwsh", "-NoProfile", "-NonInteractive", "-Command",
                 "$ErrorActionPreference='Stop'; Set-DisplayResolution -Width 1920 -Height 1080 -Force"])
            run([sys.executable, "-c", "import emo_master; from PySide2.QtWidgets import QApplication; a=QApplication([]); g=a.primaryScreen().geometry(); assert a.platformName()=='windows' and g.width()>=1920 and g.height()>=1080"], {"PYTHONPATH": str(src/"src")})
        run([sys.executable, "scripts/ci_check.py", "--shard", args.shard])
        if not (src/"build"/"ci-shard.json").is_file():
            return 1
    except (OSError, subprocess.SubprocessError):
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
