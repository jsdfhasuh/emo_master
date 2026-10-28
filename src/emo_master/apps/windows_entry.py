from __future__ import annotations

import multiprocessing
import os
import sys
from typing import Sequence


def main(arguments: Sequence[str] | None = None) -> int:
    # Windowed executables have no console streams. Child bootstrap still
    # writes to stderr when a worker fails, including inside freeze_support.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    multiprocessing.freeze_support()
    commandArguments = list(sys.argv[1:] if arguments is None else arguments)
    if "--self-test" in commandArguments:
        from emo_master.apps.package_selftest import runSelfTestCommand

        return runSelfTestCommand(commandArguments)
    if commandArguments and commandArguments[0] == "--run-project":
        from emo_master.apps.operator_runtime.main import main as runProject

        return runProject(commandArguments[1:])
    if commandArguments and commandArguments[0] in {"--runtime", "--operator-view"}:
        if commandArguments[0] == "--runtime":
            from emo_master.apps.runtime.release_host import main as runEntry
        else:
            from emo_master.apps.operator_view.main import main as runEntry
        return runEntry(commandArguments[1:])
    if commandArguments:
        raise SystemExit(f"unsupported arguments: {' '.join(commandArguments)}")

    from emo_master.apps.designer.main import runDesigner

    runDesigner()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
