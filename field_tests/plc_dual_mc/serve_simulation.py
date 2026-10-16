"""Run two existing test peers on loopback only; never open a physical PLC."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import threading


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from build_project import SIMULATION_FILE, build_project, write_project  # noqa: E402
from tests.runtime.plc_debug_server import PlcDebugServer  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=None, help="Optional bounded lifetime for smoke checks")
    args = parser.parse_args()
    with ExitStack() as stack:
        peers = [stack.enter_context(PlcDebugServer()) for _ in range(2)]
        for peer in peers:
            peer.words.update({796: 60000, 797: 17, 798: 18, 799: 19, 800: 6})
            peer.bits.update({100: True, 103: True})
        project = HERE / "simulation" / "session" / SIMULATION_FILE
        write_project(project, build_project(simulation=True, ports=tuple(peer.port for peer in peers)))
        print(json.dumps({"project": str(project), "loopback_ports": [p.port for p in peers],
                          "hardware_access": False}), flush=True)
        print("Open the printed .emoproj file in Designer. Ctrl+C stops both simulated PLCs.", flush=True)
        try:
            threading.Event().wait(args.seconds)
        except KeyboardInterrupt:
            pass
    print("Both loopback peers closed.", flush=True)


if __name__ == "__main__":
    main()
